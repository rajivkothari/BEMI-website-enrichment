"""Tests for src.enrich and the CLI wiring (fake client — no real network)."""
import pandas as pd
import pytest

from src import cli, enrich, google_places
from src.config import INPUT_COLUMNS, ENRICHMENT_COLUMNS


def make_candidate(**overrides):
    base = {
        "place_id": "A",
        "name": "Acme Dental",
        "formatted_address": "1 Main St, San Francisco, CA 94103, USA",
        "national_phone": "(415) 555-0182",
        "international_phone": "+1 415-555-0182",
        "website": "https://acme.example",
        "types": ["dentist"],
        "business_status": "OPERATIONAL",
    }
    base.update(overrides)
    return base


class FakeClient:
    def __init__(self, api_key=None, *, results=None, details=None, fail=False, **kw):
        self.results = results or []
        self.details = details or {}
        self.fail = fail
        self.search_calls = []
        self.details_calls = []

    def text_search(self, query, city=None, state=None):
        self.search_calls.append(query)
        if self.fail:
            raise google_places.PlacesError("search failed")
        return [dict(r) for r in self.results]

    def place_details(self, place_id):
        self.details_calls.append(place_id)
        return dict(self.details.get(place_id, {}))


ROW = {"practice_name": "Acme Dental", "phone": "(415) 555-0182",
       "city": "San Francisco", "state": "CA"}


def _df(*rows):
    return pd.DataFrame(list(rows))


class TestOutputColumns:
    def test_standard_input_schema(self):
        result = enrich.enrich_table(_df(ROW), client=FakeClient(results=[make_candidate()]),
                                     fetch_details=False)
        assert list(result.columns) == INPUT_COLUMNS + ENRICHMENT_COLUMNS

    def test_extra_original_columns_are_preserved(self):
        row = {"id": "42", **ROW}
        result = enrich.enrich_table(_df(row), client=FakeClient(results=[make_candidate()]),
                                     fetch_details=False)
        assert list(result.columns)[0] == "id"
        assert result.iloc[0]["id"] == "42"


class TestRanking:
    def test_selects_highest_scoring_candidate(self):
        worse = make_candidate(place_id="A", national_phone="(415) 000-0000",
                               international_phone="")  # phone mismatch
        better = make_candidate(place_id="B")           # phone matches ROW
        result = enrich.enrich_table(_df(ROW), client=FakeClient(results=[worse, better]),
                                     fetch_details=False)
        out = result.iloc[0]
        assert out["google_place_id"] == "B"
        assert out["match_score"] == 100
        assert out["match_confidence"] == "high"
        assert not out["needs_review"]


class TestDetails:
    def test_details_completes_missing_website_and_rescores(self):
        # Search result lacks a website -> not "high"; details supplies it.
        search_hit = make_candidate(place_id="A", website="")
        client = FakeClient(results=[search_hit],
                            details={"A": {"website": "https://acme.example"}})
        result = enrich.enrich_table(_df(ROW), client=client, fetch_details=True)
        out = result.iloc[0]
        assert client.details_calls == ["A"]
        assert out["google_website"] == "https://acme.example"
        assert out["match_confidence"] == "high"
        assert out["match_score"] == 100

    def test_details_failure_is_non_fatal(self):
        class FlakyDetails(FakeClient):
            def place_details(self, place_id):
                raise google_places.PlacesError("details boom")

        client = FlakyDetails(results=[make_candidate(website="")])
        result = enrich.enrich_table(_df(ROW), client=client, fetch_details=True)
        out = result.iloc[0]
        assert out["error"] == ""           # not fatal
        assert out["google_place_id"] == "A"  # kept the search result


class TestErrorHandling:
    def test_api_failure_populates_error_not_crash(self):
        result = enrich.enrich_table(_df(ROW), client=FakeClient(fail=True))
        out = result.iloc[0]
        assert "places_error" in out["error"]
        assert out["normalized_phone"] == "+14155550182"  # still normalized
        assert out["needs_review"]
        assert out["google_place_id"] == ""

    def test_no_candidates(self):
        result = enrich.enrich_table(_df(ROW), client=FakeClient(results=[]))
        assert result.iloc[0]["match_reason"] == "no_candidates"

    def test_requires_client_unless_dry_run(self):
        with pytest.raises(google_places.PlacesError):
            enrich.enrich_table(_df(ROW), client=None, dry_run=False)


class TestDryRun:
    def test_dry_run_makes_no_api_calls(self):
        client = FakeClient(results=[make_candidate()])
        result = enrich.enrich_table(_df(ROW), client=client, dry_run=True)
        out = result.iloc[0]
        assert client.search_calls == []          # API never touched
        assert out["normalized_phone"] == "+14155550182"
        assert out["match_reason"].startswith("dry-run")
        assert out["google_place_id"] == ""


class TestCli:
    def test_dry_run_writes_no_output(self, tmp_path):
        src_csv = tmp_path / "in.csv"
        pd.DataFrame([ROW]).to_csv(src_csv, index=False)
        out = tmp_path / "out.csv"
        rc = cli.main([str(src_csv), "--output", str(out), "--dry-run"])
        assert rc == 0
        assert not out.exists()

    def test_writes_output_with_expected_columns(self, monkeypatch, tmp_path):
        fake = FakeClient(results=[make_candidate()])
        monkeypatch.setattr(google_places, "GooglePlacesClient",
                            lambda api_key=None, **kw: fake)
        src_csv = tmp_path / "in.csv"
        pd.DataFrame([ROW]).to_csv(src_csv, index=False)
        out = tmp_path / "out.csv"

        rc = cli.main([str(src_csv), "--output", str(out)])
        assert rc == 0
        assert out.exists()

        written = pd.read_csv(out)
        assert list(written.columns) == INPUT_COLUMNS + ENRICHMENT_COLUMNS
        assert written.iloc[0]["google_name"] == "Acme Dental"

    def test_missing_api_key_exits_nonzero(self, monkeypatch, tmp_path):
        def boom(api_key=None, **kw):
            raise google_places.PlacesError("Missing Google Maps API key.")

        monkeypatch.setattr(google_places, "GooglePlacesClient", boom)
        src_csv = tmp_path / "in.csv"
        pd.DataFrame([ROW]).to_csv(src_csv, index=False)
        rc = cli.main([str(src_csv), "--output", str(tmp_path / "out.csv")])
        assert rc == 2