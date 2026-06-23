"""Tests for src.normalize."""
from src import normalize


class TestNormalizePhone:
    def test_formats_us_number_to_e164(self):
        assert normalize.normalize_phone("(415) 555-0182") == "+14155550182"

    def test_various_formats_normalize_equal(self):
        a = normalize.normalize_phone("415-555-0182")
        b = normalize.normalize_phone("4155550182")
        c = normalize.normalize_phone("+1 (415) 555-0182")
        assert a == b == c == "+14155550182"

    def test_blank_or_none_returns_none(self):
        assert normalize.normalize_phone("") is None
        assert normalize.normalize_phone("   ") is None
        assert normalize.normalize_phone(None) is None

    def test_unparseable_returns_none(self):
        assert normalize.normalize_phone("not a phone") is None


class TestNormalizeState:
    def test_full_name_to_abbreviation(self):
        assert normalize.normalize_state("California") == "CA"
        assert normalize.normalize_state("new york") == "NY"

    def test_abbreviation_passthrough_uppercased(self):
        assert normalize.normalize_state("ca") == "CA"
        assert normalize.normalize_state("NY") == "NY"

    def test_unknown_returns_none(self):
        assert normalize.normalize_state("Atlantis") is None

    def test_blank_or_none_returns_none(self):
        assert normalize.normalize_state("") is None
        assert normalize.normalize_state(None) is None


class TestNormalizeCity:
    def test_collapses_whitespace_and_titlecases(self):
        assert normalize.normalize_city("  san   francisco ") == "San Francisco"

    def test_blank_or_none_returns_none(self):
        assert normalize.normalize_city("") is None
        assert normalize.normalize_city("   ") is None
        assert normalize.normalize_city(None) is None
