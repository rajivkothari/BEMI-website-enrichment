"""Tests for src.cache and its integration with GooglePlacesClient."""
import time

from src.cache import SQLiteCache
from src.google_places import GooglePlacesClient

DAY = 86_400

RAW_PLACE = {
    "id": "ChIJ_acme",
    "displayName": {"text": "Acme Dental", "languageCode": "en"},
    "formattedAddress": "1 Main St, San Francisco, CA 94103, USA",
    "nationalPhoneNumber": "(415) 555-0182",
    "internationalPhoneNumber": "+1 415-555-0182",
    "websiteUri": "https://acme.example",
    "types": ["dentist"],
    "businessStatus": "OPERATIONAL",
}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = ""

    def json(self):
        return self._json


class RoutingSession:
    """Counts text-search vs. place-details calls and returns canned data."""

    def __init__(self):
        self.search_calls = 0
        self.details_calls = 0

    def request(self, method, url, headers=None, json=None, timeout=None):
        if "searchText" in url:
            self.search_calls += 1
            return FakeResponse(200, {"places": [RAW_PLACE]})
        self.details_calls += 1
        return FakeResponse(200, RAW_PLACE)


def make_client(session, cache):
    return GooglePlacesClient(api_key="test-key", session=session, cache=cache, backoff_base=0)


# --------------------------------------------------------------------------
# Direct cache behavior
# --------------------------------------------------------------------------

class TestCacheUnit:
    def test_roundtrip_uses_normalized_key(self, tmp_path):
        with SQLiteCache(tmp_path / "c.sqlite") as cache:
            cache.set_text_search("Acme   DENTAL  sf", [{"place_id": "x"}])
            # Different whitespace/case resolves to the same normalized key.
            assert cache.get_text_search("acme dental sf") == [{"place_id": "x"}]

    def test_missing_returns_none(self, tmp_path):
        with SQLiteCache(tmp_path / "c.sqlite") as cache:
            assert cache.get_text_search("nope") is None
            assert cache.get_place_details("nope") is None

    def test_expired_entry_returns_none(self, tmp_path):
        with SQLiteCache(tmp_path / "c.sqlite", ttl_days=30) as cache:
            cache.set_text_search("q", [1, 2], created_at=time.time() - 40 * DAY)
            assert cache.get_text_search("q") is None

    def test_place_details_roundtrip(self, tmp_path):
        with SQLiteCache(tmp_path / "c.sqlite") as cache:
            cache.set_place_details("pid", {"name": "X"})
            assert cache.get_place_details("pid") == {"name": "X"}

    def test_creates_parent_directory(self, tmp_path):
        db = tmp_path / "nested" / "dir" / "cache.sqlite"
        with SQLiteCache(db):
            assert db.exists()


# --------------------------------------------------------------------------
# Client + cache integration
# --------------------------------------------------------------------------

class TestClientCaching:
    def test_same_query_does_not_call_api_twice(self, tmp_path):
        session = RoutingSession()
        with SQLiteCache(tmp_path / "c.sqlite") as cache:
            client = make_client(session, cache)
            first = client.text_search("Acme Dental", state="CA")
            second = client.text_search("Acme Dental", state="CA")
        assert first == second
        assert first[0]["name"] == "Acme Dental"
        assert session.search_calls == 1  # second served from cache

    def test_expired_cache_refreshes(self, tmp_path):
        session = RoutingSession()
        with SQLiteCache(tmp_path / "c.sqlite", ttl_days=30) as cache:
            client = make_client(session, cache)
            client.text_search("Acme Dental")
            assert session.search_calls == 1

            # Backdate the cached entry beyond the TTL via the public API.
            cached = cache.get_text_search("Acme Dental")
            cache.set_text_search("Acme Dental", cached, created_at=time.time() - 40 * DAY)

            client.text_search("Acme Dental")
            assert session.search_calls == 2  # refreshed after expiry

    def test_place_details_cache_works(self, tmp_path):
        session = RoutingSession()
        with SQLiteCache(tmp_path / "c.sqlite") as cache:
            client = make_client(session, cache)
            first = client.place_details("ChIJ_acme")
            second = client.place_details("ChIJ_acme")
        assert first == second
        assert first["website"] == "https://acme.example"
        assert session.details_calls == 1  # second served from cache

    def test_no_cache_always_calls_api(self, tmp_path):
        session = RoutingSession()
        client = make_client(session, cache=None)
        client.text_search("Acme Dental")
        client.text_search("Acme Dental")
        assert session.search_calls == 2


class TestCounters:
    def test_cache_and_api_counters_align(self, tmp_path):
        session = RoutingSession()
        with SQLiteCache(tmp_path / "c.sqlite") as cache:
            client = make_client(session, cache)
            client.text_search("Acme")          # miss -> api call
            client.text_search("Acme")          # hit
            client.place_details("ChIJ_acme")   # miss -> api call
            client.place_details("ChIJ_acme")   # hit
            assert cache.hits == 2
            assert cache.misses == 2
            assert client.api_call_count == 2   # one per miss

    def test_api_count_without_cache(self):
        session = RoutingSession()
        client = make_client(session, cache=None)
        client.text_search("Acme")
        client.place_details("ChIJ_acme")
        assert client.api_call_count == 2
