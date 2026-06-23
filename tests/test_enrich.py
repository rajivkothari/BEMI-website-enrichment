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
        self.api_call_count = 0  # mirrors GooglePlacesClient's interface

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


class TestReviewColumns:
    def test_official_website_excludes_directories(self):
        cand = make_candidate(website="https://www.facebook.com/acme")
        out = enrich.enrich_table(_df(ROW), client=FakeClient(results=[cand]),
                                  fetch_details=False).iloc[0]
        assert out["google_website"] == "https://www.facebook.com/acme"
        assert out["official_website_candidate"] == ""        # directory excluded
        assert out["website_source"] == "google_places"

    def test_official_website_for_real_site(self):
        out = enrich.enrich_table(_df(ROW), client=FakeClient(results=[make_candidate()]),
                                  fetch_details=False).iloc[0]
        assert out["official_website_candidate"] == "https://acme.example"
        assert out["website_source"] == "google_places"

    def test_reviewer_columns_blank(self):
        out = enrich.enrich_table(_df(ROW), client=FakeClient(results=[make_candidate()]),
                                  fetch_details=False).iloc[0]
        assert out["review_decision"] == ""
        assert out["reviewer_notes"] == ""

    def test_website_source_verified(self, monkeypatch):
        def fake_verify(url, phone, city, state, **kw):
            return {"final_url": url, "http_status": 200, "website_phone_match": True,
                    "website_city_match": True, "website_state_match": True,
                    "verification_notes": "ok"}

        monkeypatch.setattr(enrich.website_verify, "verify_website", fake_verify)
        out = enrich.enrich_table(_df(ROW), client=FakeClient(results=[make_candidate()]),
                                  fetch_details=False, verify_websites=True).iloc[0]
        assert out["website_source"] == "google_places_verified"


class TestVerifyWebsites:
    def test_verification_adds_notes_and_bonus(self, monkeypatch):
        def fake_verify(url, phone, city, state, **kw):
            return {
                "final_url": url, "http_status": 200,
                "website_phone_match": True, "website_city_match": True,
                "website_state_match": True,
                "verification_notes": "fetched (200); phone=match, city=match, state=match",
            }

        monkeypatch.setattr(enrich.website_verify, "verify_website", fake_verify)
        # Weak Google match so the website bonus is visible in the score.
        cand = make_candidate(formatted_address="", national_phone="(415) 000-0000",
                              international_phone="")
        result = enrich.enrich_table(_df(ROW), client=FakeClient(results=[cand]),
                                     fetch_details=False, verify_websites=True)
        out = result.iloc[0]
        assert "phone=match" in out["verification_notes"]
        assert "website phone (+20)" in out["match_reason"]
        assert out["match_score"] == 65

    def test_disabled_by_default_leaves_notes_blank(self):
        result = enrich.enrich_table(_df(ROW), client=FakeClient(results=[make_candidate()]),
                                     fetch_details=False)
        assert result.iloc[0]["verification_notes"] == ""


class TestResume:
    def _prior_row(self, cols, **over):
        row = {c: "" for c in cols}
        row.update(ROW)
        row.update(over)
        return row

    def test_skips_rows_with_place_id_or_error(self):
        df = _df(ROW, ROW, ROW)
        cols = enrich.output_columns(df)
        prior = pd.DataFrame([
            self._prior_row(cols, google_place_id="PRIOR0", google_name="Prior Co"),
            self._prior_row(cols, error="places_error: old"),
            self._prior_row(cols),  # blank -> not done
        ])
        client = FakeClient(results=[make_candidate()])
        out = enrich.enrich_table(df, client=client, fetch_details=False, prior=prior)

        assert len(client.search_calls) == 1          # only the not-done row hit the API
        assert out.iloc[0]["google_place_id"] == "PRIOR0"   # reused
        assert out.iloc[0]["google_name"] == "Prior Co"
        assert out.iloc[1]["error"] == "places_error: old"  # reused (errors are skipped)
        assert out.iloc[2]["google_place_id"] == "A"        # freshly enriched

    def test_extra_input_rows_beyond_prior_are_enriched(self):
        df = _df(ROW, ROW)
        cols = enrich.output_columns(df)
        prior = pd.DataFrame([self._prior_row(cols, google_place_id="DONE")])  # only 1 prior row
        client = FakeClient(results=[make_candidate()])
        out = enrich.enrich_table(df, client=client, fetch_details=False, prior=prior)
        assert len(client.search_calls) == 1
        assert out.iloc[0]["google_place_id"] == "DONE"
        assert out.iloc[1]["google_place_id"] == "A"


class TestCheckpoint:
    def test_fires_every_n_with_full_length_frames(self):
        df = _df(ROW, ROW, ROW, ROW, ROW)
        client = FakeClient(results=[make_candidate()])
        snapshots = []
        enrich.enrich_table(df, client=client, fetch_details=False,
                            checkpoint_every=2, on_checkpoint=snapshots.append)

        assert len(snapshots) == 2                      # fires after rows 2 and 4 (not the final 5th)
        assert all(len(s) == 5 for s in snapshots)      # always full length
        first = snapshots[0]
        assert first.iloc[0]["google_place_id"] == "A"  # processed
        assert first.iloc[1]["google_place_id"] == "A"
        assert first.iloc[2]["google_place_id"] == ""   # pending -> blank but valid

    def test_disabled_when_zero(self):
        df = _df(ROW, ROW)
        snapshots = []
        enrich.enrich_table(_df(ROW, ROW), client=FakeClient(results=[make_candidate()]),
                            fetch_details=False, checkpoint_every=0, on_checkpoint=snapshots.append)
        assert snapshots == []


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

        rc = cli.main([str(src_csv), "--output", str(out),
                       "--cache-db", str(tmp_path / "cache.sqlite")])
        assert rc == 0
        assert out.exists()

        written = pd.read_csv(out)
        assert list(written.columns) == INPUT_COLUMNS + ENRICHMENT_COLUMNS
        assert written.iloc[0]["google_name"] == "Acme Dental"

        # Audit artifacts land alongside the output.
        assert (tmp_path / "enrichment_events.csv").exists()
        assert list(tmp_path.glob("run_log_*.json"))

    def test_resume_skips_already_enriched_rows(self, monkeypatch, tmp_path):
        fake = FakeClient(results=[make_candidate()])
        monkeypatch.setattr(google_places, "GooglePlacesClient",
                            lambda api_key=None, **kw: fake)
        src_csv = tmp_path / "in.csv"
        pd.DataFrame([ROW, {**ROW, "practice_name": "Beta Clinic"}]).to_csv(src_csv, index=False)
        out = tmp_path / "out.csv"

        # First run enriches both rows.
        cli.main([str(src_csv), "--output", str(out), "--no-cache"])
        assert len(fake.search_calls) == 2

        # Second run with --resume: both rows already done, so no new API work.
        cli.main([str(src_csv), "--output", str(out), "--no-cache", "--resume"])
        assert len(fake.search_calls) == 2

        written = pd.read_csv(out)
        assert len(written) == 2  # still a complete, valid file

    def test_resume_with_xlsx_output(self, monkeypatch, tmp_path):
        fake = FakeClient(results=[make_candidate()])
        monkeypatch.setattr(google_places, "GooglePlacesClient",
                            lambda api_key=None, **kw: fake)
        src = tmp_path / "in.csv"
        pd.DataFrame([ROW]).to_csv(src, index=False)
        out = tmp_path / "out.xlsx"

        cli.main([str(src), "--output", str(out), "--no-cache", "--checkpoint-every", "1"])
        assert out.exists()
        cli.main([str(src), "--output", str(out), "--no-cache", "--resume"])
        assert len(fake.search_calls) == 1  # the one row was already done

    def test_missing_api_key_exits_nonzero(self, monkeypatch, tmp_path):
        def boom(api_key=None, **kw):
            raise google_places.PlacesError("Missing Google Maps API key.")

        monkeypatch.setattr(google_places, "GooglePlacesClient", boom)
        src_csv = tmp_path / "in.csv"
        pd.DataFrame([ROW]).to_csv(src_csv, index=False)
        rc = cli.main([str(src_csv), "--output", str(tmp_path / "out.csv"),
                       "--cache-db", str(tmp_path / "cache.sqlite")])
        assert rc == 2