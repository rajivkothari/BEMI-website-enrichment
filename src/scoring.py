"""Match-scoring for a single Google Places candidate against an input row.

:func:`score_match` assigns a categorical ``confidence`` (high/medium/low/
none), a ``numeric_score`` (0-100), a human-readable ``match_reason``, and a
``needs_review`` flag, following the rules documented inline below.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

from rapidfuzz import fuzz

from . import normalize

# Directory / social / aggregator domains that are not a practice's own
# official website. A candidate whose only website is one of these is flagged
# and not treated as an official site.
DIRECTORY_DOMAINS = (
    "facebook.com", "yelp.com", "healthgrades.com", "zocdoc.com", "vitals.com",
    "webmd.com", "yellowpages.com", "mapquest.com", "doximity.com",
)

# Point values per signal.
_PTS_PHONE = 45
_PTS_CITY = 20
_PTS_STATE = 10
_PTS_NAME_STRONG = 20    # fuzzy name >= 90
_PTS_NAME_PARTIAL = 10   # fuzzy name 75-89
_PTS_WEBSITE = 10        # official (non-directory) website present
_PTS_NOT_OPERATIONAL = -30
# Website-verification signals (from fetching the homepage).
_PTS_WEB_PHONE = 20
_PTS_WEB_CITY = 10
_PTS_WEB_STATE = 5

# Fuzzy-name thresholds (token-sort ratio, 0-100).
_NAME_STRONG = 90
_NAME_PARTIAL = 75


@dataclass
class MatchResult:
    """Outcome of scoring one candidate against one input row."""

    confidence: str       # "high" | "medium" | "low" | "none"
    numeric_score: int    # 0-100
    match_reason: str
    needs_review: bool


def _domain_of(url: str) -> str:
    """Return the bare host (lowercased, no ``www.``/port) of a URL."""
    if not url:
        return ""
    parsed = urlparse(url if "//" in url else "http://" + url)
    host = parsed.netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_directory_website(url: str) -> bool:
    """True if ``url`` points at a known directory/social/aggregator site."""
    host = _domain_of(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in DIRECTORY_DOMAINS)


def _phone_matches(row_phone: Any, candidate: Mapping[str, Any]) -> bool:
    """True if the input phone equals either candidate phone (by E.164)."""
    target = normalize.normalize_phone(row_phone)["e164"]
    if not target:
        return False
    for key in ("national_phone", "international_phone"):
        candidate_e164 = normalize.normalize_phone(candidate.get(key))["e164"]
        if candidate_e164 and candidate_e164 == target:
            return True
    return False


def _name_score(practice_name: Any, candidate_name: Any) -> int:
    """Token-sort fuzzy ratio (0-100) of the normalized names."""
    a = normalize.normalize_text(practice_name)
    b = normalize.normalize_text(candidate_name)
    if not a or not b:
        return 0
    return int(round(fuzz.token_sort_ratio(a, b)))


def _city_in_address(formatted_address: Any, city: Any) -> bool:
    return normalize.city_in_text(formatted_address, city)


def _state_in_address(formatted_address: Any, state: Any) -> bool:
    return normalize.state_in_text(formatted_address, state)


def _confidence_level(
    score: int,
    *,
    official_website: bool,
    phone_match: bool,
    strong_name_location: bool,
) -> str:
    """Bucket a score into a confidence label, honoring the gating rules.

    A score may only be "high" with an official website and either a phone
    match or a very strong name+location match. Otherwise it falls through to
    the numeric bands (non-high scores >= 60 are reported as "medium").
    """
    if score >= 80 and official_website and (phone_match or strong_name_location):
        return "high"
    if score >= 60:
        return "medium"
    if score >= 30:
        return "low"
    return "none"


def score_match(
    row: Mapping[str, Any],
    candidate: Mapping[str, Any],
    verification: Optional[Mapping[str, Any]] = None,
) -> MatchResult:
    """Score a Google Places ``candidate`` against an input ``row``.

    Args:
        row: ``{"practice_name", "phone", "city", "state"}``.
        candidate: A normalized Places dict (``name``, ``formatted_address``,
            ``national_phone``, ``international_phone``, ``website``,
            ``business_status``, ...).
        verification: Optional website-verification result (see
            :func:`src.website_verify.verify_website`); when its
            ``website_*_match`` flags are set, they add to the score.

    Returns:
        A :class:`MatchResult`.
    """
    if not candidate or not (candidate.get("place_id") or candidate.get("name")):
        return MatchResult("none", 0, "no useful candidate", True)

    reasons: list[str] = []
    score = 0

    # --- Phone (strongest signal) ----------------------------------------
    phone_match = _phone_matches(row.get("phone"), candidate)
    if phone_match:
        score += _PTS_PHONE
        reasons.append(f"phone match (+{_PTS_PHONE})")
    elif normalize.normalize_phone(row.get("phone"))["e164"]:
        reasons.append("phone mismatch")
    else:
        reasons.append("no input phone")

    # --- City / state present in the formatted address -------------------
    city_match = _city_in_address(candidate.get("formatted_address"), row.get("city"))
    if city_match:
        score += _PTS_CITY
        reasons.append(f"city in address (+{_PTS_CITY})")
    else:
        reasons.append("city not in address")

    state_match = _state_in_address(candidate.get("formatted_address"), row.get("state"))
    if state_match:
        score += _PTS_STATE
        reasons.append(f"state in address (+{_PTS_STATE})")
    else:
        reasons.append("state not in address")

    # --- Fuzzy name match ------------------------------------------------
    name = _name_score(row.get("practice_name"), candidate.get("name"))
    if name >= _NAME_STRONG:
        score += _PTS_NAME_STRONG
        reasons.append(f"name {name} (+{_PTS_NAME_STRONG})")
    elif name >= _NAME_PARTIAL:
        score += _PTS_NAME_PARTIAL
        reasons.append(f"name {name} (+{_PTS_NAME_PARTIAL})")
    else:
        reasons.append(f"name {name}")

    # --- Website ---------------------------------------------------------
    website = str(candidate.get("website") or "").strip()
    has_website = bool(website)
    directory = is_directory_website(website)
    official_website = has_website and not directory
    if official_website:
        score += _PTS_WEBSITE
        reasons.append(f"official website (+{_PTS_WEBSITE})")
    elif directory:
        reasons.append(f"directory website: {_domain_of(website)}")
    else:
        reasons.append("no website")

    # --- Business status -------------------------------------------------
    status = str(candidate.get("business_status") or "").strip()
    if status and status.upper() != "OPERATIONAL":
        score += _PTS_NOT_OPERATIONAL
        reasons.append(f"not operational: {status} ({_PTS_NOT_OPERATIONAL})")

    # --- Website verification (optional homepage fetch) ------------------
    if verification:
        if verification.get("website_phone_match"):
            score += _PTS_WEB_PHONE
            reasons.append(f"website phone (+{_PTS_WEB_PHONE})")
        if verification.get("website_city_match"):
            score += _PTS_WEB_CITY
            reasons.append(f"website city (+{_PTS_WEB_CITY})")
        if verification.get("website_state_match"):
            score += _PTS_WEB_STATE
            reasons.append(f"website state (+{_PTS_WEB_STATE})")

    score = max(0, min(100, score))

    strong_name_location = name >= _NAME_STRONG and city_match and state_match
    confidence = _confidence_level(
        score,
        official_website=official_website,
        phone_match=phone_match,
        strong_name_location=strong_name_location,
    )

    needs_review = (
        confidence != "high"
        or not has_website
        or directory
        or not phone_match
    )

    match_reason = f"score={score}; confidence={confidence}; " + "; ".join(reasons)
    return MatchResult(confidence, score, match_reason, needs_review)
