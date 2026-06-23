"""Tests for src.scoring."""
from src import scoring


class TestNameSimilarity:
    def test_identical_names_score_one(self):
        assert scoring.name_similarity("Smile Bright Dental", "Smile Bright Dental") == 1.0

    def test_token_order_does_not_matter(self):
        score = scoring.name_similarity("Bright Smile Dental", "Smile Bright Dental")
        assert score > 0.9

    def test_unrelated_names_score_low(self):
        score = scoring.name_similarity("Smile Bright Dental", "Joe's Auto Repair")
        assert score < 0.5

    def test_missing_name_scores_zero(self):
        assert scoring.name_similarity("", "Anything") == 0.0
        assert scoring.name_similarity("Anything", None) == 0.0


class TestPhoneMatch:
    def test_same_number_different_formats(self):
        assert scoring.phone_match("(415) 555-0182", "415-555-0182") is True

    def test_different_numbers(self):
        assert scoring.phone_match("415-555-0182", "408-555-0193") is False

    def test_unknown_when_either_missing(self):
        assert scoring.phone_match("", "415-555-0182") is None
        assert scoring.phone_match("415-555-0182", None) is None


class TestScoreMatch:
    def test_strong_match_is_trusted(self):
        record = {"practice_name": "Smile Bright Dental", "phone": "(415) 555-0182"}
        candidate = {"google_name": "Smile Bright Dental", "google_phone": "415-555-0182"}
        result = scoring.score_match(record, candidate)
        assert result.confidence >= 0.75
        assert result.needs_review is False
        assert "phone=match" in result.reason

    def test_weak_match_needs_review(self):
        record = {"practice_name": "Smile Bright Dental", "phone": "(415) 555-0182"}
        candidate = {"google_name": "Joe's Auto Repair", "google_phone": "408-555-0193"}
        result = scoring.score_match(record, candidate)
        assert result.needs_review is True

    def test_missing_phone_marks_unknown(self):
        record = {"practice_name": "Smile Bright Dental", "phone": ""}
        candidate = {"google_name": "Smile Bright Dental", "google_phone": ""}
        result = scoring.score_match(record, candidate)
        assert "phone=unknown" in result.reason
