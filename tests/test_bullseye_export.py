"""Tests for src.bullseye_export."""
import json

import pandas as pd

from src.bullseye_export import to_bullseye_enrichment_payload, write_jsonl


def strong_row(**over):
    row = {
        "practice_name": "Bright Smile Dental",
        "phone": "(951) 555-1234",
        "city": "Riverside",
        "state": "CA",
        "google_place_id": "ChIJ_abc",
        "google_name": "Bright Smile Dental",
        "google_formatted_address": "123 Main St, Riverside, CA 92501, USA",
        "google_phone": "(951) 555-1234",
        "google_website": "https://brightsmile.example",
        "google_maps_uri": "https://maps.google.com/?cid=42",
        "google_business_status": "OPERATIONAL",
        "official_website_candidate": "https://brightsmile.example",
        "website_source": "google_places",
        "match_score": 92,
        "match_confidence": "high",
        "match_reason": "score=92; confidence=high; phone match (+45)",
        "needs_review": False,
        "verification_notes": "",
        "error": "",
    }
    row.update(over)
    return row


class TestPayloadShape:
    def test_strong_match_matches_expected_shape(self):
        payload = to_bullseye_enrichment_payload(strong_row())
        assert payload == {
            "lead_external_id": None,
            "practice_name": "Bright Smile Dental",
            "phone": "(951) 555-1234",
            "city": "Riverside",
            "state": "CA",
            "website": "https://brightsmile.example",
            "website_confidence": "high",
            "website_evidence": {
                "source": "google_places",
                "google_place_id": "ChIJ_abc",
                "google_maps_uri": "https://maps.google.com/?cid=42",
                "matched_phone": True,
                "matched_city": True,
                "matched_state": True,
                "score": 92,
                "reason": "Exact phone match; city/state matched; "
                          "website returned by Google Places.",
            },
            "needs_manual_review": False,
        }

    def test_is_json_serializable(self):
        json.dumps(to_bullseye_enrichment_payload(strong_row()))

    def test_lead_external_id_passthrough(self):
        payload = to_bullseye_enrichment_payload(strong_row(lead_external_id="LEAD-7"))
        assert payload["lead_external_id"] == "LEAD-7"


class TestEvidence:
    def test_verified_source_and_reason(self):
        payload = to_bullseye_enrichment_payload(strong_row(website_source="google_places_verified"))
        assert payload["website_evidence"]["source"] == "google_places_verified"
        assert "website verified on homepage" in payload["website_evidence"]["reason"]

    def test_verification_notes_set_matched_flags(self):
        # No Google phone, but the homepage verification confirmed phone+city.
        row = strong_row(
            google_phone="", phone="(212) 555-7777", google_formatted_address="",
            verification_notes="fetched (200); phone=match, city=match, state=no",
        )
        ev = to_bullseye_enrichment_payload(row)["website_evidence"]
        assert ev["matched_phone"] is True
        assert ev["matched_city"] is True
        assert ev["matched_state"] is False

    def test_directory_website_is_not_official(self):
        # enrich blanks official_website_candidate for directory sites.
        row = strong_row(google_website="https://facebook.com/x",
                         official_website_candidate="", match_confidence="medium")
        payload = to_bullseye_enrichment_payload(row)
        assert payload["website"] == ""
        assert "no official website found" in payload["website_evidence"]["reason"]


class TestNoMatch:
    def test_blank_row(self):
        row = {
            "practice_name": "Nowhere LLC", "phone": "", "city": "", "state": "",
            "google_place_id": "", "google_phone": "", "google_formatted_address": "",
            "google_website": "", "google_maps_uri": "", "official_website_candidate": "",
            "website_source": "", "match_score": "", "match_confidence": "",
            "needs_review": True, "verification_notes": "", "error": "no_candidates",
        }
        payload = to_bullseye_enrichment_payload(row)
        assert payload["website"] == ""
        assert payload["website_confidence"] == "none"
        assert payload["website_evidence"]["source"] == "google_places"
        assert payload["website_evidence"]["score"] == 0
        assert payload["website_evidence"]["matched_phone"] is False
        assert payload["needs_manual_review"] is True


class TestWriteJsonl:
    def test_one_line_per_row(self, tmp_path):
        df = pd.DataFrame([strong_row(), strong_row(practice_name="Second")])
        path = write_jsonl(df, tmp_path / "bullseye.jsonl")
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["practice_name"] == "Bright Smile Dental"
        assert second["practice_name"] == "Second"
