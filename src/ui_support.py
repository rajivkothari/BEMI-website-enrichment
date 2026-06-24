"""Pure helpers for the Streamlit UI (no Streamlit import, so they're testable).

Covers: detecting an input file's columns (incl. Outscraper exports), cleaning
website URLs, passing through rows that already have a website, and building /
applying the review table. Keeping this separate from ``app.py`` means the
logic is unit-tested without a Streamlit runtime.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import unquote, urlsplit, urlunsplit

import pandas as pd

from . import normalize, scoring
from .config import DEFAULT_REGION, INPUT_COLUMNS, ENRICHMENT_COLUMNS

# Canonical fields -> candidate header names (lowercased), first match wins.
# Covers plain headers and Outscraper exports.
_COLUMN_ALIASES: Dict[str, List[str]] = {
    "practice_name": ["practice_name", "name", "business_name", "company", "practice", "name_for_emails"],
    "phone": ["phone", "phone_number", "telephone", "tel", "phone_1"],
    "city": ["city", "town", "locality"],
    "state": ["state", "state_code", "province", "region", "us_state"],
    # Optional: an already-known website / identifiers (e.g. from Outscraper).
    "website": ["official_website_candidate", "website", "site", "url", "web", "domain"],
    "place_id": ["place_id", "google_id"],
    "business_status": ["business_status", "status"],
}

REQUIRED_FIELDS = list(INPUT_COLUMNS)            # practice_name, phone, city, state
OPTIONAL_FIELDS = ["website", "place_id", "business_status"]

# Internal column names used to carry pre-existing source data through the run.
SRC_WEBSITE = "_src_website"
SRC_PLACE_ID = "_src_place_id"
SRC_STATUS = "_src_business_status"

OUTPUT_SCHEMA = INPUT_COLUMNS + ENRICHMENT_COLUMNS

_CONFIDENCE_DOTS = {"high": "🟢", "medium": "🟡", "low": "🟠", "none": "⚪", "": "⚪"}

# Per-row status markers (the at-a-glance "did we find it?" signal).
STATUS_FOUND = "✅ Found"           # official site, high confidence
STATUS_CHECK = "🟡 Check"           # official site, but verify (medium/low)
STATUS_CANDIDATE = "⚠️ Candidate"   # only a group/directory site — not accepted
STATUS_NONE = "🚫 None"             # no website found at all
STATUS_ERROR = "❗ Error"           # the row failed (API/parse error)

STATUS_LEGEND = ("✅ official site found · 🟡 found — verify · "
                 "⚠️ only a group/directory candidate · 🚫 no website · ❗ error")

# Approximate Google Places (New) cost per looked-up row: one Text Search
# (~$0.032) + one Place Details (~$0.017), Pro SKUs, before Google's monthly
# free credit. Tune to your actual billing.
PRICE_PER_LOOKUP = 0.049


def detect_column_mapping(columns) -> Dict[str, Optional[str]]:
    """Best-guess mapping of canonical fields to actual column names."""
    lower = {str(c).strip().lower(): c for c in columns}
    mapping: Dict[str, Optional[str]] = {}
    for field, aliases in _COLUMN_ALIASES.items():
        mapping[field] = next((lower[a] for a in aliases if a in lower), None)
    return mapping


def is_outscraper(columns) -> bool:
    """Heuristic: does this look like an Outscraper export?"""
    cols = {str(c).strip().lower() for c in columns}
    return {"place_id", "name"}.issubset(cols) or {"google_id", "name"}.issubset(cols)


def clean_website_url(url: Any) -> str:
    """Tidy a URL: percent-decode, drop tracking query/fragment, trim slash.

    e.g. ``https://x.com/%3Futm_source%3Dgmb_auth`` -> ``https://x.com``.
    """
    if not url or normalize.normalize_text(url) == "":
        return ""
    text = unquote(str(url).strip())
    if "//" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if not parts.netloc:
        return ""
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return cleaned.rstrip("/")


def build_working_df(df: pd.DataFrame, mapping: Mapping[str, Optional[str]]) -> pd.DataFrame:
    """Project the input down to canonical inputs + carried source columns."""
    work = pd.DataFrame(index=df.index)
    for field in REQUIRED_FIELDS:
        col = mapping.get(field)
        work[field] = df[col].fillna("") if col and col in df.columns else ""
    work[SRC_WEBSITE] = (
        df[mapping["website"]].fillna("") if mapping.get("website") in df.columns else ""
    )
    work[SRC_PLACE_ID] = (
        df[mapping["place_id"]].fillna("") if mapping.get("place_id") in df.columns else ""
    )
    work[SRC_STATUS] = (
        df[mapping["business_status"]].fillna("") if mapping.get("business_status") in df.columns else ""
    )
    return work


def has_existing_website(record: Mapping[str, Any]) -> bool:
    """True if the row already carries a (non-blank) website from the source."""
    return bool(clean_website_url(record.get(SRC_WEBSITE, "")))


def passthrough_row(record: Mapping[str, Any], region: str = DEFAULT_REGION) -> dict:
    """Build an output row from a source website without calling Google.

    A clean, official URL is trusted (high, no review); a directory/social URL
    is flagged for review; the website itself is always tidied first.
    """
    row = {col: "" for col in OUTPUT_SCHEMA}
    for col in INPUT_COLUMNS:
        row[col] = record.get(col, "")

    row["normalized_phone"] = normalize.normalize_phone(record.get("phone"), region)["e164"] or ""
    site = clean_website_url(record.get(SRC_WEBSITE, ""))
    directory = scoring.is_directory_website(site)

    row["google_website"] = site
    row["google_phone"] = str(record.get("phone", "") or "")
    row["google_place_id"] = str(record.get(SRC_PLACE_ID, "") or "")
    row["google_business_status"] = str(record.get(SRC_STATUS, "") or "")
    row["website_source"] = "outscraper" if site else ""
    row["official_website_candidate"] = "" if (not site or directory) else site

    if site and not directory:
        row.update(match_score=90, match_confidence="high", needs_review=False,
                   match_reason="Website provided by source (Outscraper / Google listing).")
    elif directory:
        row.update(match_score=30, match_confidence="low", needs_review=True,
                   match_reason=f"Source website is a directory/social site: {site}")
    else:
        row.update(match_score=0, match_confidence="none", needs_review=True,
                   match_reason="No website in source data.")
    return row


def count_lookups(work: pd.DataFrame, *, fill_gaps_only: bool, has_key: bool) -> int:
    """How many rows will actually call the (paid) Google API."""
    if not has_key:
        return 0
    if not fill_gaps_only:
        return len(work)
    return sum(not has_existing_website(r) for r in work.to_dict("records"))


def estimate_cost(n_lookups: int, price_per_lookup: float = PRICE_PER_LOOKUP) -> float:
    """Estimated USD cost for ``n_lookups`` Google lookups (rounded)."""
    return round(max(0, n_lookups) * max(0.0, price_per_lookup), 2)


def confidence_dot(label: Any) -> str:
    return _CONFIDENCE_DOTS.get(str(label).strip().lower(), "⚪")


def tile_counts(df: pd.DataFrame) -> Dict[str, int]:
    """Summary counts for the stat tiles."""
    def conf(level: str) -> int:
        return int((df.get("match_confidence", pd.Series(dtype=str)).astype(str).str.lower() == level).sum())

    website_found = int((df.get("official_website_candidate", pd.Series(dtype=str))
                         .fillna("").astype(str).str.strip() != "").sum())
    needs_review = int(sum(str(v).strip().lower() == "true" or v is True
                           for v in df.get("needs_review", pd.Series(dtype=str))))
    total = len(df)
    return {
        "total": total,
        "website_found": website_found,
        "no_website": total - website_found,
        "needs_review": needs_review,
        "high": conf("high"),
        "medium": conf("medium"),
        "low": conf("low"),
    }


def row_status(row: Mapping[str, Any]) -> str:
    """One at-a-glance status marker for a row (see the STATUS_* constants)."""
    if str(row.get("error") or "").strip():
        return STATUS_ERROR
    if str(row.get("official_website_candidate") or "").strip():
        return STATUS_FOUND if str(row.get("match_confidence")).strip().lower() == "high" \
            else STATUS_CHECK
    if str(row.get("google_website") or "").strip() or "web candidate" in str(row.get("reviewer_notes") or ""):
        return STATUS_CANDIDATE
    return STATUS_NONE


def build_review_table(df: pd.DataFrame) -> pd.DataFrame:
    """Project the enriched frame into the editable review view."""
    df = df.reset_index(drop=True).fillna("")

    def col(name: str) -> pd.Series:
        return df[name] if name in df.columns else pd.Series([""] * len(df))

    location = (col("city").astype(str) + ", " + col("state").astype(str)).str.strip(", ")
    phone = col("normalized_phone").where(col("normalized_phone").astype(str) != "", col("phone"))
    website = col("official_website_candidate").where(
        col("official_website_candidate").astype(str) != "", col("google_website"))

    return pd.DataFrame({
        "status": [row_status(rec) for rec in df.to_dict("records")],
        "needs_review": [str(v).strip().lower() == "true" or v is True for v in col("needs_review")],
        "practice": col("practice_name"),
        "location": location,
        "phone": phone,
        "website": website,
        "confidence": [f"{confidence_dot(v)} {v}".strip() for v in col("match_confidence")],
        "score": pd.to_numeric(col("match_score"), errors="coerce").fillna(0).astype(int),
        "source": col("website_source"),
        "reason": col("match_reason"),
        "decision": col("review_decision"),
        "final_website": website,
        "notes": col("reviewer_notes"),
    })


def apply_review_edits(enriched: pd.DataFrame, edited: pd.DataFrame) -> pd.DataFrame:
    """Fold the reviewer's edits back into a copy of the enriched frame."""
    out = enriched.reset_index(drop=True).copy()
    edited = edited.reset_index(drop=True)
    out["review_decision"] = edited["decision"].astype(str).values
    out["reviewer_notes"] = edited["notes"].astype(str).values
    # A pasted/corrected URL becomes the official website used by the export.
    final = edited["final_website"].astype(str).map(clean_website_url).values
    out["official_website_candidate"] = final
    return out
