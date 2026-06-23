"""Bullseye-compatible export format.

Maps an enriched output row to the JSON payload shape Bullseye expects for a
website enrichment. This module only builds the payload (and a JSONL writer);
it does not connect to any Bullseye database or API.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Union

import pandas as pd

from . import normalize

PathLike = Union[str, Path]


def _str(value: Any) -> str:
    """Clean a scalar to a stripped string (``""`` for None/NaN)."""
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return ""
    return str(value).strip()


def _to_int(value: Any) -> int:
    text = _str(value)
    if not text:
        return 0
    try:
        return int(float(text))
    except (ValueError, TypeError):
        return 0


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _str(value).lower() in ("true", "1", "yes")


def _reason(matched_phone: bool, matched_city: bool, matched_state: bool,
            website: str, source: str) -> str:
    """Build a concise, human-readable evidence sentence."""
    parts = []
    if matched_phone:
        parts.append("exact phone match")
    if matched_city and matched_state:
        parts.append("city/state matched")
    elif matched_city:
        parts.append("city matched")
    elif matched_state:
        parts.append("state matched")
    if website:
        parts.append(
            "website verified on homepage" if source == "google_places_verified"
            else "website returned by Google Places"
        )
    else:
        parts.append("no official website found")
    reason = "; ".join(parts)
    return reason[0].upper() + reason[1:] + "."


def to_bullseye_enrichment_payload(enriched_row: Mapping[str, Any]) -> dict:
    """Convert one enriched output row into a Bullseye enrichment payload.

    Args:
        enriched_row: A mapping/Series with the enrichment output columns.

    Returns:
        A JSON-serializable dict in Bullseye's expected shape.
    """
    row = enriched_row
    phone = _str(row.get("phone", ""))
    city = _str(row.get("city", ""))
    state = _str(row.get("state", ""))
    google_phone = _str(row.get("google_phone", ""))
    google_address = _str(row.get("google_formatted_address", ""))
    notes = _str(row.get("verification_notes", ""))

    # Re-derive match signals from the matched Google place, OR-ing in any
    # homepage-verification results recorded in verification_notes.
    input_e164 = normalize.normalize_phone(phone)["e164"]
    google_e164 = normalize.normalize_phone(google_phone)["e164"]
    matched_phone = bool(input_e164 and google_e164 and input_e164 == google_e164) \
        or "phone=match" in notes
    matched_city = normalize.city_in_text(google_address, city) or "city=match" in notes
    matched_state = normalize.state_in_text(google_address, state) or "state=match" in notes

    website = _str(row.get("official_website_candidate", ""))
    source = _str(row.get("website_source", "")) or "google_places"
    confidence = _str(row.get("match_confidence", "")) or "none"

    lead_external_id = row.get("lead_external_id", None)
    if lead_external_id is not None and _str(lead_external_id) == "":
        lead_external_id = None

    return {
        "lead_external_id": lead_external_id,
        "practice_name": _str(row.get("practice_name", "")),
        "phone": phone,
        "city": city,
        "state": state,
        "website": website,
        "website_confidence": confidence,
        "website_evidence": {
            "source": source,
            "google_place_id": _str(row.get("google_place_id", "")),
            "google_maps_uri": _str(row.get("google_maps_uri", "")),
            "matched_phone": matched_phone,
            "matched_city": matched_city,
            "matched_state": matched_state,
            "score": _to_int(row.get("match_score", "")),
            "reason": _reason(matched_phone, matched_city, matched_state, website, source),
        },
        "needs_manual_review": _to_bool(row.get("needs_review", True)),
    }


def write_jsonl(df: pd.DataFrame, path: PathLike) -> Path:
    """Write one Bullseye payload per row as JSON Lines. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for _, row in df.iterrows():
            payload = to_bullseye_enrichment_payload(row)
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path
