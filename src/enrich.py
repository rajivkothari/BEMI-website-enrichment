"""Enrichment orchestration: input rows -> enriched output rows.

This wires the pieces together (normalize -> Google Places lookup ->
scoring). Because the Places calls are still stubbed, each row is processed
defensively: a :class:`NotImplementedError` (or any error) is caught and
recorded in the ``error`` column so the pipeline runs end-to-end on sample
data today and "just works" once :mod:`src.google_places` is implemented.
"""
from __future__ import annotations

from typing import Mapping

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


def enrich_record(record: Mapping[str, object], settings: Settings) -> dict[str, object]:
    """Enrich a single practice record into an output row.

    Args:
        record: A mapping with the input columns.
        settings: Resolved runtime settings (API key, region, thresholds).

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

    try:
        query = normalize.build_search_query(
            norm.get("practice_name"),
            norm.get("phone"),
            norm.get("city"),
            norm.get("state"),
        )
        candidates = google_places.search_text(
            query, settings.api_key, region=settings.region
        )

        # TODO: choose the best candidate rather than the first.
        best = candidates[0] if candidates else None
        if best is None:
            row["match_reason"] = "no_candidates"
            return row

        details = google_places.get_place_details(best["place_id"], settings.api_key)
        result = scoring.score_match(
            norm,
            details,
            region=settings.region,
            review_threshold=settings.review_threshold,
        )
        row.update(
            {
                "google_place_id": details.get("place_id", ""),
                "google_name": details.get("google_name", ""),
                "google_formatted_address": details.get("google_formatted_address", ""),
                "google_phone": details.get("google_phone", ""),
                "google_website": details.get("google_website", ""),
                "match_confidence": result.confidence,
                "match_reason": result.reason,
                "needs_review": result.needs_review,
            }
        )
    except NotImplementedError as exc:
        # Expected while the Places integration is still a stub.
        row["error"] = f"not_implemented: {exc}"
        row["match_reason"] = "pending"
    except Exception as exc:  # noqa: BLE001 - keep the batch going, record the error
        row["error"] = f"{type(exc).__name__}: {exc}"

    return row


def enrich_table(df: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Enrich an entire DataFrame, returning a new DataFrame.

    Args:
        df: Input DataFrame with the practice columns.
        settings: Resolved runtime settings.

    Returns:
        A DataFrame with exactly :data:`~src.config.OUTPUT_COLUMNS`.
    """
    rows = [enrich_record(rec, settings) for rec in df.to_dict("records")]
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
