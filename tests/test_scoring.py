"""Tests for src.scoring."""
from src import scoring
from src.scoring import score_match


def candidate(**overrides):
    """A normalized Places candidate with sensible defaults."""
    base = {
        "place_id": "ChIJ_test",
        "name": "Smile Bright Dental",
        "formatted_address": "123 Market St, San Francisco, CA 94103, USA",
        "national_phone": "(415) 555-0182",
        "international_phone": "+1 415-555-0182",
        "website": "https://smilebright.example",
        "types": ["dentist"],
        "business_status": "OPERATIONAL",
    }
    base.update(overrides)
    return base


ROW = {
    "practice_name": "Smile Bright Dental",
    "phone": "(415) 555-0182",
    "city": "San Francisco",
    "state": "CA",
}


class TestIsDirectoryWebsite:
    def test_flags_known_directories(self):
        assert scoring.is_directory_website("https://www.facebook.com/smilebright")
        assert scoring.is_directory_website("http://YELP.COM/biz/x")
        assert scoring.is_directory_website("https://maps.facebook.com/x")  # subdomain

    def test_allows_official_sites(self):
        assert not scoring.is_directory_website("https://smilebright.example")
        assert not scoring.is_directory_website("")


class TestHighConfidence:
    def test_exact_phone_city_state_website_is_high(self):
        result = score_match(ROW, candidate())
        assert result.confidence == "high"
        assert result.numeric_score == 100  # 45+20+10+20+10 clamped
        assert result.needs_review is False

    def test_phone_match_via_international_only(self):
        result = score_match(ROW, candidate(national_phone="", international_phone="+1 415-555-0182"))
        assert result.confidence == "high"
        assert result.needs_review is False


class TestNeedsReview:
    def test_directory_website_needs_review(self):
        result = score_match(ROW, candidate(website="https://www.facebook.com/smilebright"))
        assert result.needs_review is True
        assert result.confidence != "high"
        assert "directory website" in result.match_reason

    def test_missing_website_needs_review(self):
        result = score_match(ROW, candidate(website=""))
        assert result.needs_review is True
        assert result.confidence != "high"
        assert "no website" in result.match_reason

    def test_phone_mismatch_needs_review_even_when_strong(self):
        # Everything else perfect, but the phone disagrees.
        result = score_match(ROW, candidate(national_phone="(415) 999-0000", international_phone=""))
        assert result.needs_review is True
        assert result.confidence != "high"


class TestWrongLocation:
    def test_wrong_city_is_low_or_medium(self):
        row = {**ROW, "practice_name": "Joe's Auto Repair"}
        cand = candidate(
            name="Joe's Auto Repair",
            formatted_address="500 Main St, Los Angeles, CA 90012, USA",
            national_phone="(213) 555-9999",
            international_phone="",
            website="https://joesauto.example",
        )
        result = score_match(row, cand)
        # state(+10) + name(+20) + website(+10) = 40; no phone, no city.
        assert result.numeric_score == 40
        assert result.confidence in ("low", "medium")
        assert result.needs_review is True


class TestFuzzyName:
    """Isolate the name signal (no phone/city/state/website)."""

    bare_row = {"practice_name": "Smile Bright Dental", "phone": "", "city": "", "state": ""}

    def _bare_candidate(self, name):
        return {
            "place_id": "x", "name": name, "formatted_address": "",
            "national_phone": "", "international_phone": "", "website": "",
            "types": [], "business_status": "",
        }

    def test_strong_name_adds_20(self):
        result = score_match(self.bare_row, self._bare_candidate("Smile Bright Dental"))
        assert result.numeric_score == 20
        assert "(+20)" in result.match_reason

    def test_partial_name_adds_10(self):
        result = score_match(self.bare_row, self._bare_candidate("Smile Bright Dental Care"))
        assert result.numeric_score == 10
        assert "(+10)" in result.match_reason

    def test_weak_name_adds_nothing(self):
        result = score_match(self.bare_row, self._bare_candidate("Downtown Medical Group"))
        assert result.numeric_score == 0


class TestBusinessStatus:
    def test_not_operational_is_penalized(self):
        result = score_match(ROW, candidate(business_status="CLOSED_PERMANENTLY"))
        # 45+20+10+20+10 - 30 = 75
        assert result.numeric_score == 75
        assert result.confidence == "medium"
        assert "not operational" in result.match_reason
        assert result.needs_review is True


class TestWebsiteVerification:
    def test_website_signals_add_points(self):
        # Weak base: name match + official website only (30), no phone/city/state.
        weak = candidate(formatted_address="", national_phone="(415) 000-0000",
                         international_phone="")
        base = score_match(ROW, weak)
        assert base.numeric_score == 30

        verification = {
            "website_phone_match": True,
            "website_city_match": True,
            "website_state_match": True,
        }
        verified = score_match(ROW, weak, verification=verification)
        assert verified.numeric_score == 65  # 30 + 20 + 10 + 5
        assert "website phone (+20)" in verified.match_reason
        assert "website city (+10)" in verified.match_reason
        assert "website state (+5)" in verified.match_reason

    def test_no_verification_is_unchanged(self):
        weak = candidate(formatted_address="", national_phone="(415) 000-0000",
                         international_phone="")
        assert score_match(ROW, weak, verification=None).numeric_score == 30


class TestNoCandidate:
    def test_empty_candidate_is_none(self):
        for empty in ({}, None):
            result = score_match(ROW, empty)
            assert result.confidence == "none"
            assert result.numeric_score == 0
            assert result.needs_review is True
            assert "no useful candidate" in result.match_reason
