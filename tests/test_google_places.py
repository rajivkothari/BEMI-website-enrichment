"""Tests for src.google_places (mocked HTTP — no real network calls)."""
import pytest

from src import google_places
from src.google_places import GooglePlacesClient, PlacesError


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no JSON body")
        return self._json_data


class FakeSession:
    """Returns queued responses (or raises queued exceptions) in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


# Sample raw payloads in the shape the Places API (New) returns.
RAW_PLACE = {
    "id": "ChIJ_test_123",
    "displayName": {"text": "Smile Bright Dental", "languageCode": "en"},
    "formattedAddress": "123 Market St, San Francisco, CA 94103, USA",
    "nationalPhoneNumber": "(415) 555-0182",
    "internationalPhoneNumber": "+1 415-555-0182",
    "websiteUri": "https://smilebright.example",
    "types": ["dentist", "health"],
    "businessStatus": "OPERATIONAL",
    "googleMapsUri": "https://maps.google.com/?cid=123",
}


def make_client(responses, **kwargs):
    kwargs.setdefault("backoff_base", 0)  # no real sleeping in tests
    return GooglePlacesClient(api_key="test-key", session=FakeSession(responses), **kwargs)


class TestApiKey:
    def test_missing_key_raises_clear_error(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
        with pytest.raises(PlacesError, match="Missing Google Maps API key"):
            GooglePlacesClient(api_key=None)

    def test_key_from_environment(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "env-key")
        client = GooglePlacesClient()
        assert client.api_key == "env-key"


class TestTextSearch:
    def test_normalizes_results(self):
        client = make_client([FakeResponse(200, {"places": [RAW_PLACE]})])
        results = client.text_search("Smile Bright Dental")
        assert results == [
            {
                "place_id": "ChIJ_test_123",
                "name": "Smile Bright Dental",
                "formatted_address": "123 Market St, San Francisco, CA 94103, USA",
                "national_phone": "(415) 555-0182",
                "international_phone": "+1 415-555-0182",
                "website": "https://smilebright.example",
                "types": ["dentist", "health"],
                "business_status": "OPERATIONAL",
                "google_maps_uri": "https://maps.google.com/?cid=123",
            }
        ]

    def test_sends_expected_request(self):
        session = FakeSession([FakeResponse(200, {"places": []})])
        client = GooglePlacesClient(api_key="test-key", session=session)
        client.text_search("Acme", city="Reno", state="NV")

        call = session.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == "https://places.googleapis.com/v1/places:searchText"
        assert call["json"] == {"textQuery": "Acme Reno NV"}
        assert call["headers"]["X-Goog-Api-Key"] == "test-key"
        assert call["headers"]["Content-Type"] == "application/json"
        assert "places.websiteUri" in call["headers"]["X-Goog-FieldMask"]

    def test_empty_results(self):
        client = make_client([FakeResponse(200, {})])
        assert client.text_search("nothing here") == []


class TestPlaceDetails:
    def test_normalizes_and_uses_get(self):
        session = FakeSession([FakeResponse(200, RAW_PLACE)])
        client = GooglePlacesClient(api_key="test-key", session=session)
        details = client.place_details("ChIJ_test_123")

        assert details["place_id"] == "ChIJ_test_123"
        assert details["name"] == "Smile Bright Dental"
        assert details["website"] == "https://smilebright.example"

        call = session.calls[0]
        assert call["method"] == "GET"
        assert call["url"].endswith("/v1/places/ChIJ_test_123")
        assert "Content-Type" not in call["headers"]  # no body on GET

    def test_missing_optional_fields_default_to_blank(self):
        client = make_client([FakeResponse(200, {"id": "X", "displayName": {"text": "Y"}})])
        details = client.place_details("X")
        assert details["website"] == ""
        assert details["national_phone"] == ""
        assert details["types"] == []


class TestRetries:
    def test_retries_on_503_then_succeeds(self):
        client = make_client(
            [
                FakeResponse(503, text="overloaded"),
                FakeResponse(503, text="overloaded"),
                FakeResponse(200, {"places": [RAW_PLACE]}),
            ]
        )
        results = client.text_search("Smile Bright Dental")
        assert len(results) == 1
        assert len(client.session.calls) == 3

    def test_retries_on_429(self):
        client = make_client(
            [FakeResponse(429, text="rate limited"), FakeResponse(200, RAW_PLACE)]
        )
        details = client.place_details("X")
        assert details["place_id"] == "ChIJ_test_123"

    def test_gives_up_after_max_retries(self):
        client = make_client([FakeResponse(500, text="boom")] * 4, max_retries=3)
        with pytest.raises(PlacesError, match="failed after 4 attempts"):
            client.text_search("anything")
        assert len(client.session.calls) == 4

    def test_does_not_retry_on_4xx(self):
        client = make_client([FakeResponse(403, text="forbidden")])
        with pytest.raises(PlacesError, match="HTTP 403"):
            client.text_search("anything")
        assert len(client.session.calls) == 1  # no retry on client error

    def test_retries_on_network_error(self):
        import requests

        client = make_client(
            [requests.ConnectionError("down"), FakeResponse(200, {"places": []})]
        )
        assert client.text_search("anything") == []
        assert len(client.session.calls) == 2
