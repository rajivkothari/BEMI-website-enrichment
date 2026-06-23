"""Normalization helpers for phone numbers, cities, and states.

These keep the input data and the Google Places responses comparable when
scoring matches (e.g. so ``(415) 555-0182`` and ``+1 415-555-0182`` are seen
as the same number). The functions are deliberately small and pure so they
are easy to unit-test.
"""
from __future__ import annotations

from typing import Optional

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


def normalize_phone(phone: object, region: str = DEFAULT_REGION) -> Optional[str]:
    """Normalize a phone number to E.164 format (e.g. ``+14155550182``).

    Args:
        phone: A phone number in any common format. ``None``/blank returns
            ``None``.
        region: Default region for numbers without a country code.

    Returns:
        The E.164 string, or ``None`` if the value is blank or unparseable.
    """
    if phone is None:
        return None
    raw = str(phone).strip()
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_city(city: object) -> Optional[str]:
    """Collapse whitespace and title-case a city name.

    Args:
        city: A city name. ``None``/blank returns ``None``.

    Returns:
        A cleaned city name, or ``None`` if blank.
    """
    if city is None:
        return None
    cleaned = " ".join(str(city).split())
    if not cleaned:
        return None
    return cleaned.title()


def normalize_state(state: object) -> Optional[str]:
    """Normalize a US state to its two-letter USPS abbreviation.

    Accepts either a full state name (``"California"``) or an existing
    abbreviation (``"ca"``).

    Args:
        state: A state name or abbreviation. ``None``/blank returns ``None``.

    Returns:
        A two-letter abbreviation, or ``None`` if unrecognized.
    """
    if state is None:
        return None
    cleaned = " ".join(str(state).split())
    if not cleaned:
        return None
    if len(cleaned) == 2 and cleaned.upper() in _VALID_ABBREVIATIONS:
        return cleaned.upper()
    return _US_STATES.get(cleaned.lower())
