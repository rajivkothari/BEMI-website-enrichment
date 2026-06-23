"""Match-scoring helpers.

Given an input record and a Google Places candidate, produce a confidence
score, a human-readable reason, and a ``needs_review`` flag. The individual
signal helpers (name similarity, phone agreement) are implemented; the way
they are combined in :func:`score_match` is an intentionally simple baseline
that will be tuned once the Places integration returns real candidates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from rapidfuzz import fuzz

from . import normalize
from .config import DEFAULT_REGION, REVIEW_THRESHOLD


@dataclass
class MatchResult:
    """The outcome of scoring a single candidate against an input record."""

    confidence: float = 0.0
    reason: str = ""
    needs_review: bool = True


def name_similarity(input_name: object, google_name: object) -> float:
    """Fuzzy similarity between two names, scaled to ``0.0``-``1.0``.

    Uses token-sort ratio so word order does not matter (``"Bright Smile
    Dental"`` ~ ``"Smile Bright Dental"``).
    """
    if not input_name or not google_name:
        return 0.0
    return fuzz.token_sort_ratio(str(input_name), str(google_name)) / 100.0


def phone_match(
    input_phone: object,
    google_phone: object,
    region: str = DEFAULT_REGION,
) -> Optional[bool]:
    """Compare two phone numbers after normalization.

    Returns:
        ``True`` if both normalize to the same E.164 number, ``False`` if
        they differ, or ``None`` if either is missing/unparseable (unknown).
    """
    a = normalize.normalize_phone(input_phone, region)["e164"]
    b = normalize.normalize_phone(google_phone, region)["e164"]
    if a is None or b is None:
        return None
    return a == b


def score_match(
    record: Mapping[str, object],
    candidate: Mapping[str, object],
    region: str = DEFAULT_REGION,
    review_threshold: float = REVIEW_THRESHOLD,
) -> MatchResult:
    """Combine match signals into a :class:`MatchResult`.

    Baseline heuristic: start from the name similarity, then nudge the score
    up or down based on whether the phone numbers agree. This is a starting
    point — weighting, address/city/state agreement, and website sanity
    checks are TODO once real candidates are available.

    Args:
        record: The (normalized) input row, e.g. ``practice_name``/``phone``.
        candidate: A Google Places candidate with ``google_*`` fields.
        region: Default phone region for normalization.
        review_threshold: Confidence below which ``needs_review`` is set.

    Returns:
        A :class:`MatchResult`.
    """
    name = name_similarity(record.get("practice_name"), candidate.get("google_name"))
    phones = phone_match(record.get("phone"), candidate.get("google_phone"), region)

    reasons = [f"name~{name:.2f}"]
    confidence = name
    if phones is True:
        confidence = min(1.0, confidence + 0.2)
        reasons.append("phone=match")
    elif phones is False:
        confidence = max(0.0, confidence - 0.2)
        reasons.append("phone=mismatch")
    else:
        reasons.append("phone=unknown")

    confidence = round(confidence, 3)
    return MatchResult(
        confidence=confidence,
        reason="; ".join(reasons),
        needs_review=confidence < review_threshold,
    )
