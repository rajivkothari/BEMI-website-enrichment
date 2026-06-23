"""Normalization helpers for phone numbers, free text, cities, and states.

These keep the input data and the Google Places responses comparable when
building search queries and scoring matches (e.g. so ``(415) 555-0182`` and
``+1 415-555-0182`` are recognized as the same number). The functions are
deliberately small and pure so they are easy to unit-test.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict

import phonenumbers

from .config import DEFAULT_REGION

# Full state/territory names mapped to their USPS two-letter abbreviations.
_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR",
}
_VALID_ABBREVIATIONS = set(_US_STATES.values())
# USPS abbreviation -> full (lowercased) state name, for text matching.
_ABBREV_TO_STATE = {abbrev: name for name, abbrev in _US_STATES.items()}

# Smart punctuation -> ASCII, applied before stripping "weird" characters so
# names like "O'Brien" survive curly-apostrophe input.
_SMART_PUNCTUATION = {
    ord("‘"): "'", ord("’"): "'",   # ' '
    ord("“"): '"', ord("”"): '"',   # " "
    ord("–"): "-", ord("—"): "-",   # - -
}
# Characters to drop from free text: anything that is not a word character,
# whitespace, ampersand, apostrophe, or hyphen.
_WEIRD_PUNCTUATION = re.compile(r"[^\w\s&'-]", re.UNICODE)


def normalize_phone(phone: Any, region: str = DEFAULT_REGION) -> Dict[str, Any]:
    """Parse a phone number into a set of normalized representations.

    Args:
        phone: A phone number in any common format. ``None``/blank/garbage is
            handled gracefully.
        region: Default region for numbers without a country code (ISO 3166).

    Returns:
        A dict with keys:
            ``raw``      - the original value, unchanged.
            ``e164``     - E.164 string (``"+19515551234"``) or ``None``.
            ``national`` - national format (``"(951) 555-1234"``) or ``None``.
            ``digits``   - national significant digits (``"9515551234"``) or ``None``.
            ``valid``    - ``True`` if it is a valid number for ``region``.
    """
    result: Dict[str, Any] = {
        "raw": phone,
        "e164": None,
        "national": None,
        "digits": None,
        "valid": False,
    }

    if phone is None:
        return result
    text = str(phone).strip()
    if not text:
        return result

    try:
        parsed = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return result

    # Reject numbers that are not even plausible (wrong length, etc.).
    if not phonenumbers.is_possible_number(parsed):
        return result

    national = phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.NATIONAL
    )
    result["e164"] = phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.E164
    )
    result["national"] = national
    result["digits"] = re.sub(r"\D", "", national)
    result["valid"] = phonenumbers.is_valid_number(parsed)
    return result


def normalize_text(value: Any) -> str:
    """Lowercase, strip, collapse whitespace, and drop weird punctuation.

    Useful for fuzzy comparison and query building. Word characters,
    whitespace, ``&``, ``'``, and ``-`` are preserved; other punctuation is
    replaced with a space. Smart quotes/dashes are folded to ASCII first.

    Args:
        value: Any value. ``None`` returns ``""``.

    Returns:
        A cleaned, lowercased string (possibly empty).
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.translate(_SMART_PUNCTUATION)
    text = text.lower()
    text = _WEIRD_PUNCTUATION.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_city(value: Any) -> str:
    """Collapse whitespace and title-case a city name.

    Args:
        value: A city name. ``None``/blank returns ``""``.

    Returns:
        A cleaned, title-cased city name (possibly empty).
    """
    if value is None:
        return ""
    return " ".join(str(value).split()).title()


def normalize_state(value: Any) -> str:
    """Normalize a US state to its two-letter USPS abbreviation.

    Accepts a full state name (``"California"``) or an existing abbreviation
    (``"ca"``). Unrecognized values are returned cleaned but unchanged so no
    data is silently dropped.

    Args:
        value: A state name or abbreviation. ``None``/blank returns ``""``.

    Returns:
        A two-letter abbreviation when recognized, otherwise the cleaned input.
    """
    if value is None:
        return ""
    cleaned = " ".join(str(value).split())
    if not cleaned:
        return ""
    if len(cleaned) == 2 and cleaned.upper() in _VALID_ABBREVIATIONS:
        return cleaned.upper()
    return _US_STATES.get(cleaned.lower(), cleaned)


def build_search_query(
    practice_name: Any,
    phone: Any = "",
    city: Any = "",
    state: Any = "",
) -> str:
    """Build a Google Places text-search query from practice fields.

    Produces a query of the form ``"{practice_name} {city} {state}"`` with the
    city title-cased and the state abbreviated. ``phone`` is accepted for
    signature symmetry and possible future phone-based lookups, but is not part
    of the text query.

    Args:
        practice_name: The business/practice name.
        phone: Unused in the query (see above).
        city: Optional city to disambiguate the search.
        state: Optional state to disambiguate the search.

    Returns:
        A single-spaced query string, e.g. ``"Smile Bright Dental San
        Francisco CA"``. Empty parts are omitted.
    """
    name = " ".join(str(practice_name).split()) if practice_name else ""
    parts = [name, normalize_city(city), normalize_state(state)]
    return " ".join(part for part in parts if part)


def city_in_text(text: Any, city: Any) -> bool:
    """True if ``city`` appears in ``text`` (case/whitespace-insensitive)."""
    needle = normalize_text(city)
    return bool(needle) and needle in normalize_text(text)


def state_in_text(text: Any, state: Any) -> bool:
    """True if ``state`` (name or abbreviation) appears in ``text``.

    Matches the uppercase USPS code as a whole word (case-sensitive, so "CA"
    does not match "Ca" inside a word), then falls back to the full state name
    (case-insensitive). The fallback never uses the bare two-letter code, which
    would otherwise match substrings like "MA" in "Market".
    """
    if not state or not text:
        return False
    raw = str(text)
    abbrev = normalize_state(state)
    if abbrev and re.search(rf"\b{re.escape(abbrev)}\b", raw):
        return True
    full_name = _ABBREV_TO_STATE.get(abbrev) if abbrev else None
    if full_name is None:
        # Unrecognized input: use it as a name only if it is longer than a code.
        candidate = normalize_text(state)
        full_name = candidate if len(candidate) > 2 else None
    return bool(full_name) and full_name in normalize_text(raw)
