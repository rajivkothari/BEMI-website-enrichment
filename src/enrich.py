"""Enrichment orchestration: input rows -> enriched output rows.

This wires the pieces together: normalize the input, run a Google Places
text search, fetch details for the best candidate, and score the match. Each
row is processed defensively so a single failure (network, parsing, etc.) is
recorded in the ``error`` column instead of aborting the whole batch.
"""
from __future__ import annotations

from typing import Mapping, Optional

import pandas as pd

from . import google_places, normalize, scoring
from .config import OUTPUT_COLUMNS, Settings


def _blank_output_row(record: Mapping[str, object]) -> dict[str, object]:
    """Create an output row pre-filled with the input fields."""
    row: dict[str, object] = {col: "" for col in OUTPUT_COLUMNS}
    for col in ("practice_name", "phone", "city", "state"):
        row[col] = record.get(col, "")
    row["needs_review"] = True
    return row


def _best_phone(details: Mapping[str, object]) -> str:
    """Prefer the national phone, falling back to the international one."""
    return str(details.get("national_phone") or details.get("international_phone") or "")


def enrich_record(
    record: Mapping[str, object],
    settings: Settings,
    client: "google_places.GooglePlacesClient",
) -> dict[str, object]:
    """Enrich a single practice record into an output row.

    Args:
        record: A mapping with the input columns.
        settings: Resolved runtime settings (region, thresholds).
        client: A configured :class:`~src.google_places.GooglePlacesClient`.

    Returns:
        A dict keyed by :data:`~src.config.OUTPUT_COLUMNS`.
    """
    row = _blank_output_row(record)

    # Normalize inputs so querying and scoring are consistent. Keep the E.164
    # phone when available, otherwise fall back to the original value.
    norm = dict(record)
    phone_info = normalize.normalize_phone(record.get("phone"), settings.region)
    norm["phone"] = phone_info["e164"] or str(record.get("phone") or "")
    norm["city"] = normalize.normalize_city(record.get("city"))
    norm["state"] = normalize.normalize_state(record.get("state"))

    query = normalize.build_search_query(
        norm.get("practice_name"),
        norm.get("phone"),
        norm.get("city"),
        norm.get("state"),
    )
    if not query:
        row["match_reason"] = "empty_query"
        return row

    try:
        candidates = client.text_search(query)

        # TODO: rank candidates instead of taking the first result.
        best = candidates[0] if candidates else None
        if best is None:
            row["match_reason"] = "no_candidates"
            return row

        details = client.place_details(best["place_id"])

        # Map the normalized Google fields onto the scorer's expected keys.
        candidate = {
            "google_name": details.get("name", ""),
            "google_phone": _best_phone(details),
        }
        result = scoring.score_match(
            norm,
            candidate,
            region=settings.region,
            review_threshold=settings.review_threshold,
        )
        row.update(
            {
                "google_place_id": details.get("place_id", ""),
                "google_name": details.get("name", ""),
                "google_formatted_address": details.get("formatted_address", ""),
                "google_phone": _best_phone(details),
                "google_website": details.get("website", ""),
                "match_confidence": result.confidence,
                "match_reason": result.reason,
                "needs_review": result.needs_review,
            }
        )
    except google_places.PlacesError as exc:
        row["error"] = f"places_error: {exc}"
    except Exception as exc:  # noqa: BLE001 - keep the batch going, record the error
        row["error"] = f"{type(exc).__name__}: {exc}"

    return row


def enrich_table(
    df: pd.DataFrame,
    settings: Settings,
    client: Optional["google_places.GooglePlacesClient"] = None,
) -> pd.DataFrame:
    """Enrich an entire DataFrame, returning a new DataFrame.

    Args:
        df: Input DataFrame with the practice columns.
        settings: Resolved runtime settings.
        client: Optional pre-built client (useful for testing). When omitted,
            one is created from ``settings.api_key``.

    Returns:
        A DataFrame with exactly :data:`~src.config.OUTPUT_COLUMNS`.
    """
    if client is None:
        try:
            client = google_places.GooglePlacesClient(settings.api_key)
        except google_places.PlacesError as exc:
            # Without an API key we cannot enrich anything; flag every row
            # rather than raising, so the output still mirrors the input.
            rows = []
            for record in df.to_dict("records"):
                row = _blank_output_row(record)
                row["error"] = f"config_error: {exc}"
                rows.append(row)
            return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)

    rows = [enrich_record(rec, settings, client) for rec in df.to_dict("records")]
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
