"""Tests for src.normalize."""
import pytest

from src import normalize

# All 50 states + DC, used to verify full name -> abbreviation coverage.
ALL_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC",
}


class TestNormalizePhone:
    @pytest.mark.parametrize(
        "value",
        [
            "(951) 555-1234",   # parentheses
            "951-555-1234",     # dashes
            "951 555 1234",     # spaces
            "951.555.1234",     # dots
            "+1 951-555-1234",  # +1 country code
            "+19515551234",     # bare E.164
        ],
    )
    def test_equivalent_formats_normalize_identically(self, value):
        result = normalize.normalize_phone(value)
        assert result["e164"] == "+19515551234"
        assert result["national"] == "(951) 555-1234"
        assert result["digits"] == "9515551234"
        assert result["valid"] is True

    def test_raw_is_preserved(self):
        assert normalize.normalize_phone("(951) 555-1234")["raw"] == "(951) 555-1234"
        assert normalize.normalize_phone(None)["raw"] is None

    def test_invalid_phone_returns_blanks(self):
        result = normalize.normalize_phone("not a phone")
        assert result == {
            "raw": "not a phone",
            "e164": None,
            "national": None,
            "digits": None,
            "valid": False,
        }

    def test_too_short_is_not_possible(self):
        result = normalize.normalize_phone("123")
        assert result["e164"] is None
        assert result["valid"] is False

    def test_blank_and_none(self):
        for value in ("", "   ", None):
            result = normalize.normalize_phone(value)
            assert result["e164"] is None
            assert result["digits"] is None
            assert result["valid"] is False

    def test_region_override(self):
        # A London number, parsed against the GB region.
        result = normalize.normalize_phone("020 7946 0958", region="GB")
        assert result["e164"] == "+442079460958"
        assert result["valid"] is True


class TestNormalizeText:
    def test_lowercases_strips_and_collapses_whitespace(self):
        assert normalize.normalize_text("  Hello,   World!  ") == "hello world"

    def test_removes_weird_punctuation_but_keeps_safe_chars(self):
        assert normalize.normalize_text("O’Brien & Sons, Inc.") == "o'brien & sons inc"

    def test_keeps_digits_and_hyphens(self):
        assert normalize.normalize_text("7-Eleven #233") == "7-eleven 233"

    def test_preserves_accented_letters(self):
        assert normalize.normalize_text("  Café   Déli ") == "café déli"

    def test_none_and_blank_return_empty(self):
        assert normalize.normalize_text(None) == ""
        assert normalize.normalize_text("   ") == ""


class TestNormalizeCity:
    def test_collapses_whitespace_and_titlecases(self):
        assert normalize.normalize_city("  san   francisco ") == "San Francisco"

    def test_handles_punctuation_in_name(self):
        assert normalize.normalize_city("st. louis") == "St. Louis"

    def test_none_and_blank_return_empty(self):
        assert normalize.normalize_city("") == ""
        assert normalize.normalize_city("   ") == ""
        assert normalize.normalize_city(None) == ""


class TestNormalizeState:
    @pytest.mark.parametrize("name,abbrev", list(ALL_STATES.items()))
    def test_full_name_to_abbreviation(self, name, abbrev):
        assert normalize.normalize_state(name) == abbrev

    def test_case_insensitive_full_name(self):
        assert normalize.normalize_state("california") == "CA"
        assert normalize.normalize_state("NEW YORK") == "NY"

    def test_collapses_whitespace_in_name(self):
        assert normalize.normalize_state("  new   york ") == "NY"

    def test_existing_abbreviation_is_uppercased(self):
        assert normalize.normalize_state("ca") == "CA"
        assert normalize.normalize_state("Tx") == "TX"

    def test_unrecognized_is_returned_cleaned(self):
        # Not silently dropped; whitespace is still collapsed.
        assert normalize.normalize_state("  Ontario ") == "Ontario"

    def test_none_and_blank_return_empty(self):
        assert normalize.normalize_state("") == ""
        assert normalize.normalize_state(None) == ""


class TestBuildSearchQuery:
    def test_builds_name_city_state(self):
        query = normalize.build_search_query(
            "Smile Bright Dental", "(415) 555-0182", "san francisco", "California"
        )
        assert query == "Smile Bright Dental San Francisco CA"

    def test_collapses_whitespace_in_name(self):
        query = normalize.build_search_query("  Smile   Dental ", None, "Reno", "NV")
        assert query == "Smile Dental Reno NV"

    def test_omits_missing_parts(self):
        assert normalize.build_search_query("Joe's Auto", "", "", "") == "Joe's Auto"

    def test_phone_is_not_included(self):
        query = normalize.build_search_query("Acme", "415-555-0182", "", "")
        assert query == "Acme"
