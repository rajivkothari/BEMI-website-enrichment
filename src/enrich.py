"""Enrichment orchestration: input rows -> enriched output rows.

For each row the pipeline:
  1. normalizes phone/city/state/practice_name,
  2. builds a text-search query from practice_name + city + state,
  3. runs a Google Places text search,
  4. scores every candidate,
  5. selects the best candidate by numeric score,
  6. optionally fetches Place Details to complete website/phone fields,
  7. re-scores the (details-completed) best candidate,
  8. emits an output row.

A failure on any single row is recorded in that row's ``error`` column rather
than aborting the whole run.
"""
from __future__ import annotations

import logging
from typing import Any, List, Mapping, Optional, Sequence

import pandas as pd
import requests

from . import google_places, normalize, scoring, website_verify
from .config import DEFAULT_REGION, ENRICHMENT_COLUMNS

logger = logging.getLogger(__name__)


def output_columns(df: pd.DataFrame) -> List[str]:
    """Return the output column order: original columns then enrichment ones."""
    original = list(df.columns)
    return original + [c for c in ENRICHMENT_COLUMNS if c not in original]


def _best_phone(candidate: Mapping[str, Any]) -> str:
    """Prefer the national phone, falling back to the international one."""
    return str(candidate.get("national_phone") or candidate.get("international_phone") or "")


def _merge_details(search_candidate: Mapping[str, Any], details: Mapping[str, Any]) -> dict:
    """Overlay non-empty Place Details fields onto the search candidate."""
    merged = dict(search_candidate)
    for key, value in details.items():
        if value not in (None, "", []):
            merged[key] = value
    return merged


def _base_row(record: Mapping[str, Any], columns: Sequence[str], region: str) -> dict:
    """Start an output row: original fields + normalized_phone, blanks elsewhere."""
    row = {col: "" for col in columns}
    for key, value in record.items():
        if key in row:
            row[key] = value
    row["normalized_phone"] = normalize.normalize_phone(record.get("phone"), region)["e164"] or ""
    row["needs_review"] = True
    return row


def enrich_record(
    record: Mapping[str, Any],
    client: "google_places.GooglePlacesClient",
    columns: Sequence[str],
    *,
    region: str = DEFAULT_REGION,
    fetch_details: bool = True,
    verify_websites: bool = False,
    verify_session: Optional[requests.Session] = None,
) -> dict:
    """Enrich a single record into an output row (never raises)."""
    row = _base_row(record, columns, region)

    query = normalize.build_search_query(
        record.get("practice_name"),
        record.get("phone"),
        record.get("city"),
        record.get("state"),
    )
    if not query:
        row["match_reason"] = "empty_query"
        return row

    try:
        candidates = client.text_search(query)
        if not candidates:
            row["match_reason"] = "no_candidates"
            return row

        # Score every candidate, then keep the highest-scoring one.
        best_candidate, best_result = max(
            ((c, scoring.score_match(record, c)) for c in candidates),
            key=lambda pair: pair[1].numeric_score,
        )

        # Optionally complete website/phone fields via Place Details, then
        # re-score. A details failure is non-fatal: keep the search result.
        if fetch_details and best_candidate.get("place_id"):
            try:
                details = client.place_details(best_candidate["place_id"])
                best_candidate = _merge_details(best_candidate, details)
                best_result = scoring.score_match(record, best_candidate)
            except google_places.PlacesError as exc:
                logger.debug("details lookup failed for %s: %s",
                             best_candidate.get("place_id"), exc)

        # Optionally verify the homepage (phone/city/state on the site) and
        # re-score with the extra signals. Never fatal: a bad site just yields
        # a verification note and no bonus points.
        verification = None
        if verify_websites and best_candidate.get("website"):
            verification = website_verify.verify_website(
                best_candidate["website"],
                record.get("phone"), record.get("city"), record.get("state"),
                session=verify_session, region=region,
            )
            row["verification_notes"] = verification.get("verification_notes", "")
            best_result = scoring.score_match(record, best_candidate, verification=verification)

        website = best_candidate.get("website", "") or ""
        verified = bool(verification and (
            verification.get("website_phone_match")
            or verification.get("website_city_match")
            or verification.get("website_state_match")
        ))
        row.update(
            {
                "google_place_id": best_candidate.get("place_id", ""),
                "google_name": best_candidate.get("name", ""),
                "google_formatted_address": best_candidate.get("formatted_address", ""),
                "google_phone": _best_phone(best_candidate),
                "google_website": website,
                "google_maps_uri": best_candidate.get("google_maps_uri", ""),
                "google_business_status": best_candidate.get("business_status", ""),
                "official_website_candidate": (
                    website if website and not scoring.is_directory_website(website) else ""
                ),
                "website_source": (
                    ("google_places_verified" if verified else "google_places") if website else ""
                ),
                "match_score": best_result.numeric_score,
                "match_confidence": best_result.confidence,
                "match_reason": best_result.match_reason,
                "needs_review": best_result.needs_review,
            }
        )
    except google_places.PlacesError as exc:
        row["error"] = f"places_error: {exc}"
    except Exception as exc:  # noqa: BLE001 - keep the batch going, record the error
        row["error"] = f"{type(exc).__name__}: {exc}"

    return row


def _dry_run_row(record: Mapping[str, Any], columns: Sequence[str], region: str) -> dict:
    """Build a preview row without calling the Google API."""
    row = _base_row(record, columns, region)
    query = normalize.build_search_query(
        record.get("practice_name"),
        record.get("phone"),
        record.get("city"),
        record.get("state"),
    )
    row["match_reason"] = f"dry-run: would search {query!r}" if query else "dry-run: empty_query"
    return row


def enrich_table(
    df: pd.DataFrame,
    *,
    client: Optional["google_places.GooglePlacesClient"] = None,
    region: str = DEFAULT_REGION,
    fetch_details: bool = True,
    verify_websites: bool = False,
    dry_run: bool = False,
    progress_every: int = 25,
) -> pd.DataFrame:
    """Enrich an entire DataFrame, returning a new DataFrame.

    Args:
        df: Input DataFrame with at least the practice columns.
        client: A configured Places client (required unless ``dry_run``).
        region: Default region for phone normalization.
        fetch_details: Whether to call Place Details for the best candidate.
        verify_websites: Whether to fetch each matched homepage and verify the
            phone/city/state on it (slower; off by default).
        dry_run: If true, normalize + build queries but make no API calls.
        progress_every: Log an INFO progress line every N rows (0 disables).

    Returns:
        A DataFrame whose columns are the original columns followed by the
        enrichment columns.
    """
    columns = output_columns(df)
    records = df.to_dict("records")
    total = len(records)
    rows: List[dict] = []

    if dry_run:
        logger.info("DRY RUN: normalizing and building queries for %d rows (no API calls)", total)
        for record in records:
            rows.append(_dry_run_row(record, columns, region))
        return pd.DataFrame(rows, columns=columns)

    if client is None:
        raise google_places.PlacesError("A Places client is required to enrich (or use dry_run=True).")

    # One shared session for homepage fetches (connection reuse).
    verify_session = requests.Session() if verify_websites else None
    logger.info("Enriching %d rows (fetch_details=%s, verify_websites=%s)",
                total, fetch_details, verify_websites)
    try:
        for index, record in enumerate(records, start=1):
            row = enrich_record(
                record, client, columns,
                region=region, fetch_details=fetch_details,
                verify_websites=verify_websites, verify_session=verify_session,
            )
            rows.append(row)
            if row.get("error"):
                logger.debug("row %d error: %s", index, row["error"])
            else:
                logger.debug(
                    "row %d: %r -> %r (score=%s, %s)",
                    index, record.get("practice_name"), row.get("google_name"),
                    row.get("match_score"), row.get("match_confidence"),
                )
            if progress_every and index % progress_every == 0:
                logger.info("Processed %d/%d rows", index, total)
    finally:
        if verify_session is not None:
            verify_session.close()

    logger.info("Done: %d rows (%d need review)", total, sum(1 for r in rows if r.get("needs_review")))
    return pd.DataFrame(rows, columns=columns)
