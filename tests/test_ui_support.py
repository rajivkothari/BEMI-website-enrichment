"""Tests for src.ui_support (pure UI helpers — no Streamlit)."""
import pandas as pd

from src import ui_support
from src.config import INPUT_COLUMNS, ENRICHMENT_COLUMNS

OUTSCRAPER_COLS = ["query", "name", "phone", "website", "city", "state", "state_code",
                   "place_id", "business_status", "address"]


class TestDetectMapping:
    def test_outscraper_headers(self):
        m = ui_support.detect_column_mapping(OUTSCRAPER_COLS)
        assert m["practice_name"] == "name"
        assert m["phone"] == "phone"
        assert m["city"] == "city"
        assert m["state"] == "state"
        assert m["website"] == "website"
        assert m["place_id"] == "place_id"

    def test_plain_headers(self):
        m = ui_support.detect_column_mapping(["practice_name", "phone", "city", "state"])
        assert m["practice_name"] == "practice_name"
        assert m["website"] is None

    def test_is_outscraper(self):
        assert ui_support.is_outscraper(OUTSCRAPER_COLS)
        assert not ui_support.is_outscraper(["practice_name", "phone"])


class TestCleanUrl:
    def test_strips_encoded_tracking(self):
        assert ui_support.clean_website_url(
            "https://www.inspireobgyn.com/%3Futm_source%3Dgmb_auth"
        ) == "https://www.inspireobgyn.com"

    def test_drops_query_and_trailing_slash(self):
        assert ui_support.clean_website_url("https://x.com/?a=1") == "https://x.com"

    def test_adds_scheme_for_bare_domain(self):
        assert ui_support.clean_website_url("example.com") == "http://example.com"

    def test_blank(self):
        assert ui_support.clean_website_url("") == ""
        assert ui_support.clean_website_url(None) == ""


class TestWorkingDf:
    def test_projects_canonical_and_source_columns(self):
        raw = pd.DataFrame([{"name": "Acme OB", "phone": "+1 404-252-1137", "city": "Suwanee",
                             "state": "Georgia", "website": "https://acme.example", "place_id": "P1"}])
        mapping = ui_support.detect_column_mapping(raw.columns)
        work = ui_support.build_working_df(raw, mapping)
        row = work.iloc[0]
        assert row["practice_name"] == "Acme OB"
        assert row[ui_support.SRC_WEBSITE] == "https://acme.example"
        assert row[ui_support.SRC_PLACE_ID] == "P1"


class TestPassthrough:
    def _rec(self, **over):
        rec = {"practice_name": "Acme OB", "phone": "+1 404-252-1137", "city": "Suwanee",
               "state": "Georgia", ui_support.SRC_WEBSITE: "https://acme.example",
               ui_support.SRC_PLACE_ID: "P1", ui_support.SRC_STATUS: "OPERATIONAL"}
        rec.update(over)
        return rec

    def test_official_site_is_trusted(self):
        row = ui_support.passthrough_row(self._rec())
        assert row["official_website_candidate"] == "https://acme.example"
        assert row["website_source"] == "outscraper"
        assert row["match_confidence"] == "high"
        assert row["needs_review"] is False
        assert row["normalized_phone"] == "+14042521137"
        assert row["google_place_id"] == "P1"

    def test_directory_site_flagged(self):
        row = ui_support.passthrough_row(self._rec(**{ui_support.SRC_WEBSITE: "https://facebook.com/acme"}))
        assert row["official_website_candidate"] == ""   # directory not official
        assert row["match_confidence"] == "low"
        assert row["needs_review"] is True

    def test_has_existing_website(self):
        assert ui_support.has_existing_website(self._rec())
        assert not ui_support.has_existing_website(self._rec(**{ui_support.SRC_WEBSITE: ""}))


class TestReviewTableAndTiles:
    def _enriched(self):
        base = {c: "" for c in INPUT_COLUMNS + ENRICHMENT_COLUMNS}
        r1 = {**base, "practice_name": "Acme OB", "city": "Suwanee", "state": "GA",
              "normalized_phone": "+14042521137", "official_website_candidate": "https://acme.example",
              "match_confidence": "high", "match_score": 90, "needs_review": False, "website_source": "outscraper"}
        r2 = {**base, "practice_name": "Beta Clinic", "city": "Atlanta", "state": "GA",
              "google_website": "https://facebook.com/beta", "official_website_candidate": "",
              "match_confidence": "low", "match_score": 30, "needs_review": True}
        return pd.DataFrame([r1, r2])

    def test_tile_counts(self):
        counts = ui_support.tile_counts(self._enriched())
        assert counts["total"] == 2
        assert counts["website_found"] == 1
        assert counts["no_website"] == 1
        assert counts["needs_review"] == 1
        assert counts["high"] == 1 and counts["low"] == 1

    def test_review_table_shape(self):
        view = ui_support.build_review_table(self._enriched())
        assert list(view.columns) == ["needs_review", "practice", "location", "phone", "website",
                                      "confidence", "score", "source", "decision", "final_website", "notes"]
        assert view.iloc[0]["website"] == "https://acme.example"
        assert view.iloc[0]["location"] == "Suwanee, GA"
        assert view.iloc[0]["phone"] == "+14042521137"
        assert "🟢" in view.iloc[0]["confidence"]

    def test_apply_edits(self):
        enriched = self._enriched()
        view = ui_support.build_review_table(enriched)
        view.loc[1, "decision"] = "replaced"
        view.loc[1, "final_website"] = "https://betaclinic.example/?utm=x"
        view.loc[1, "notes"] = "found real site"
        out = ui_support.apply_review_edits(enriched, view)
        assert out.iloc[1]["review_decision"] == "replaced"
        assert out.iloc[1]["reviewer_notes"] == "found real site"
        assert out.iloc[1]["official_website_candidate"] == "https://betaclinic.example"  # cleaned
