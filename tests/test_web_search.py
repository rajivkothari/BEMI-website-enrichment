"""Tests for src.web_search (mocked HTTP — no real network)."""
import pytest

from src import web_search
from src.config import Settings
from src.web_search import (BraveClient, SerperClient, WebSearchError,
                            choose_website, fill_missing_website, make_searcher)


class FakeResp:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "json": json})
        return FakeResp(self._payload)


class TestChooseWebsite:
    def test_standalone_site_accepted(self):
        res = choose_website("Inspire Ob/Gyn, LLC", [{"link": "https://www.inspireobgyn.com/"}])
        assert res["website"] == "https://www.inspireobgyn.com/"
        assert res["is_standalone"] is True

    def test_group_site_is_candidate_not_accepted(self):
        # Real-world: Dr. Iyoyo's only site is her group's (Women's Group of Gwinnett).
        results = [
            {"link": "https://obgynfinder.com/obgyns/georgia/suwanee/obgyn-melissa-iyoyo/"},  # directory
            {"link": "https://womensgroupofgwinnett.com/providers/melissa-iyoyo"},            # group
            {"link": "https://health.usnews.com/doctors/melissa-iyoyo-730840"},               # directory
        ]
        res = choose_website("OBGYN: Melissa Iyoyo", results)
        assert res["website"] == ""                                  # not standalone -> not accepted
        assert "womensgroupofgwinnett.com" in res["candidate"]       # surfaced for review
        assert "group/parent" in res["reason"]

    def test_directory_only_returns_nothing(self):
        results = [{"link": "https://health.usnews.com/x"}, {"link": "https://www.zocdoc.com/y"}]
        res = choose_website("Xuan Cao", results)
        assert res["website"] == "" and res["candidate"] == ""
        assert "only directory" in res["reason"]


class TestClients:
    def test_serper_parses_organic(self):
        session = FakeSession({"organic": [{"link": "https://a.com", "title": "A", "snippet": "s"}]})
        out = SerperClient("key", session=session).search("acme")
        assert out[0]["link"] == "https://a.com"
        assert session.calls[0]["method"] == "POST"
        assert session.calls[0]["headers"]["X-API-KEY"] == "key"
        assert session.calls[0]["json"]["q"] == "acme"

    def test_brave_maps_url_to_link(self):
        session = FakeSession({"web": {"results": [{"url": "https://b.com", "title": "B", "description": "d"}]}})
        out = BraveClient("key", session=session).search("acme")
        assert out[0]["link"] == "https://b.com"
        assert session.calls[0]["headers"]["X-Subscription-Token"] == "key"

    def test_missing_key_raises(self):
        with pytest.raises(WebSearchError):
            SerperClient(None)
        with pytest.raises(WebSearchError):
            BraveClient("")


class TestMakeSearcher:
    def test_picks_by_configured_key(self):
        assert isinstance(make_searcher(Settings(serper_api_key="x")), SerperClient)
        assert isinstance(make_searcher(Settings(brave_api_key="y")), BraveClient)
        assert make_searcher(Settings()) is None

    def test_explicit_provider_override(self):
        s = Settings(serper_api_key="x", brave_api_key="y", web_search_provider="brave")
        assert isinstance(make_searcher(s), BraveClient)


class TestFillMissingWebsite:
    def _row(self, **over):
        row = {"official_website_candidate": "", "google_website": "", "website_source": "",
               "match_confidence": "none", "match_score": 0, "match_reason": "no_candidates",
               "needs_review": True, "reviewer_notes": ""}
        row.update(over)
        return row

    class _Searcher:
        def __init__(self, results):
            self._results = results

        def search(self, query, num=5):
            return self._results

    def test_standalone_fills_official_site(self):
        row = fill_missing_website(self._row(), practice_name="Inspire Ob/Gyn", city="Suwanee", state="GA",
                                   searcher=self._Searcher([{"link": "https://www.inspireobgyn.com/"}]))
        assert row["official_website_candidate"] == "https://www.inspireobgyn.com/"
        assert row["website_source"] == "web_search"
        assert row["needs_review"] is True

    def test_group_site_only_noted(self):
        row = fill_missing_website(self._row(), practice_name="OBGYN: Melissa Iyoyo", city="Suwanee", state="GA",
                                   searcher=self._Searcher([{"link": "https://womensgroupofgwinnett.com/x"}]))
        assert row["official_website_candidate"] == ""
        assert "womensgroupofgwinnett.com" in row["reviewer_notes"]

    def test_existing_site_unchanged(self):
        row = fill_missing_website(self._row(official_website_candidate="https://x.com"),
                                   practice_name="X", city="", state="",
                                   searcher=self._Searcher([{"link": "https://y.com"}]))
        assert row["official_website_candidate"] == "https://x.com"

    def test_search_failure_is_non_fatal(self):
        class Boom:
            def search(self, query, num=5):
                raise WebSearchError("down")

        row = fill_missing_website(self._row(), practice_name="X", city="", state="", searcher=Boom())
        assert row["official_website_candidate"] == ""
