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
from typing import Any, Callable, List, Mapping, Optional, Sequence

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


def _nonempty(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _is_done(prior_row: Mapping[str, Any]) -> bool:
    """A row is 'done' (skippable on resume) once it has a place_id or error."""
    return _nonempty(prior_row.get("google_place_id")) or _nonempty(prior_row.get("error"))


def enrich_table(
    df: pd.DataFrame,
    *,
    client: Optional["google_places.GooglePlacesClient"] = None,
    region: str = DEFAULT_REGION,
    fetch_details: bool = True,
    verify_websites: bool = False,
    dry_run: bool = False,
    progress_every: int = 25,
    prior: Optional[pd.DataFrame] = None,
    checkpoint_every: int = 0,
    on_checkpoint: Optional[Callable[[pd.DataFrame], None]] = None,
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
        progress_every: Log an INFO progress line every N enriched rows.
        prior: Existing output to resume from (row i is reused when it already
            has a ``google_place_id`` or ``error``), correlated by position.
        checkpoint_every: Call ``on_checkpoint`` every N newly-enriched rows
            with the full current output (0 disables).
        on_checkpoint: Callback receiving the full-length partial DataFrame.

    Returns:
        A DataFrame whose columns are the original columns followed by the
        enrichment columns.
    """
    columns = output_columns(df)
    records = df.to_dict("records")
    total = len(records)

    if dry_run:
        logger.info("DRY RUN: normalizing and building queries for %d rows (no API calls)", total)
        rows = [_dry_run_row(record, columns, region) for record in records]
        return pd.DataFrame(rows, columns=columns)

    if client is None:
        raise google_places.PlacesError("A Places client is required to enrich (or use dry_run=True).")

    prior_records = prior.to_dict("records") if prior is not None else []

    # Seed every output row: reuse prior data when resuming, else a blank row.
    # This keeps the partial output full-length (and valid) at every checkpoint.
    output_rows: List[dict] = []
    for i, record in enumerate(records):
        if i < len(prior_records):
            output_rows.append({col: prior_records[i].get(col, "") for col in columns})
        else:
            output_rows.append(_base_row(record, columns, region))

    done = [i < len(prior_records) and _is_done(prior_records[i]) for i in range(total)]
    skipped = sum(done)

    verify_session = requests.Session() if verify_websites else None
    logger.info(
        "Enriching %d rows (fetch_details=%s, verify_websites=%s); %d already done (resumed)",
        total, fetch_details, verify_websites, skipped,
    )

    processed = 0
    try:
        for i, record in enumerate(records):
            if done[i]:
                continue
            output_rows[i] = enrich_record(
                record, client, columns,
                region=region, fetch_details=fetch_details,
                verify_websites=verify_websites, verify_session=verify_session,
            )
            processed += 1

            if output_rows[i].get("error"):
                logger.debug("row %d error: %s", i + 1, output_rows[i]["error"])
            else:
                logger.debug(
                    "row %d: %r -> %r (score=%s, %s)",
                    i + 1, record.get("practice_name"), output_rows[i].get("google_name"),
                    output_rows[i].get("match_score"), output_rows[i].get("match_confidence"),
                )
            if progress_every and processed % progress_every == 0:
                logger.info("Processed %d new rows (%d/%d total)", processed, i + 1, total)
            if checkpoint_every and on_checkpoint and processed % checkpoint_every == 0:
                on_checkpoint(pd.DataFrame(output_rows, columns=columns))
    finally:
        if verify_session is not None:
            verify_session.close()

    logger.info(
        "Done: %d rows (%d enriched this run, %d reused, %d need review)",
        total, processed, skipped, sum(1 for r in output_rows if _is_true(r.get("needs_review"))),
    )
    return pd.DataFrame(output_rows, columns=columns)


def _is_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"
