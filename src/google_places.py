"""Google Maps Platform Places API (New) client.

Thin wrapper over the Places API (New) REST endpoints using ``requests``:

  * Text Search   -> ``POST  https://places.googleapis.com/v1/places:searchText``
  * Place Details -> ``GET   https://places.googleapis.com/v1/places/{place_id}``

Both responses are normalized into the flat dict shape consumed by the rest
of the pipeline (see :meth:`GooglePlacesClient._normalize_place`).
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests

from .config import PLACES_DETAILS_URL, PLACES_TEXT_SEARCH_URL

# Field masks tell the Places API which fields to return (required by the New
# API). Keep these in sync with what _normalize_place reads.
_TEXT_SEARCH_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.internationalPhoneNumber,"
    "places.websiteUri,places.types,places.businessStatus"
)
_DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,nationalPhoneNumber,"
    "internationalPhoneNumber,websiteUri,types,businessStatus,googleMapsUri"
)

# HTTP statuses that are worth retrying.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class PlacesError(RuntimeError):
    """Raised when a Places API request fails (config, HTTP, or parsing)."""


def _snippet(text: str, limit: int = 300) -> str:
    """Truncate a response body for inclusion in error messages."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


class GooglePlacesClient:
    """Client for the Google Places API (New).

    Args:
        api_key: Google Maps Platform API key. Falls back to the
            ``GOOGLE_MAPS_API_KEY`` environment variable.
        session: Optional pre-configured :class:`requests.Session`.
        timeout: Per-request timeout in seconds.
        max_retries: Number of retries (in addition to the first attempt) for
            429/5xx responses and transient network errors.
        backoff_base: Base seconds for exponential backoff between retries
            (delay = ``backoff_base * 2 ** attempt``).
        cache: Optional response cache. Any object exposing
            ``get_text_search``/``set_text_search``/``get_place_details``/
            ``set_place_details`` (e.g. :class:`src.cache.SQLiteCache`).

    Raises:
        PlacesError: If no API key can be resolved.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        session: Optional[requests.Session] = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        cache: Optional[Any] = None,
    ) -> None:
        api_key = api_key or os.getenv("GOOGLE_MAPS_API_KEY")
        if not api_key:
            raise PlacesError(
                "Missing Google Maps API key. Set GOOGLE_MAPS_API_KEY in your "
                "environment or .env file, or pass api_key=... explicitly."
            )
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.cache = cache
        # Count of logical Places API calls actually sent (cache misses /
        # uncached calls); retries within a call are not counted separately.
        self.api_call_count = 0

    # -- Public API --------------------------------------------------------

    def text_search(
        self,
        query: str,
        city: Optional[str] = None,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search for places matching a free-text query.

        Args:
            query: The text query (e.g. a practice name, optionally already
                including the location).
            city: Optional city appended to the query to focus the search.
            state: Optional state appended to the query to focus the search.

        Returns:
            A list of normalized candidate dicts (see
            :meth:`_normalize_place`). Empty if there are no matches.
        """
        text_query = " ".join(
            part.strip()
            for part in (query, city, state)
            if part and str(part).strip()
        )
        if self.cache is not None:
            cached = self.cache.get_text_search(text_query)
            if cached is not None:
                return cached
        data = self._request(
            "POST",
            PLACES_TEXT_SEARCH_URL,
            field_mask=_TEXT_SEARCH_FIELD_MASK,
            payload={"textQuery": text_query},
        )
        result = [self._normalize_place(place) for place in data.get("places", [])]
        if self.cache is not None:
            self.cache.set_text_search(text_query, result)
        return result

    def place_details(self, place_id: str) -> Dict[str, Any]:
        """Fetch details for a place by its Place ID.

        Args:
            place_id: A Google Place ID (e.g. ``"ChIJ..."``).

        Returns:
            A normalized place dict (see :meth:`_normalize_place`).
        """
        if self.cache is not None:
            cached = self.cache.get_place_details(place_id)
            if cached is not None:
                return cached
        url = PLACES_DETAILS_URL.format(place_id=place_id)
        data = self._request("GET", url, field_mask=_DETAILS_FIELD_MASK)
        result = self._normalize_place(data)
        if self.cache is not None:
            self.cache.set_place_details(place_id, result)
        return result

    # -- Internals ---------------------------------------------------------

    def _headers(self, field_mask: str, *, json_body: bool = False) -> Dict[str, str]:
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": field_mask,
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        field_mask: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a request with retry/backoff for 429, 5xx, and network errors."""
        headers = self._headers(field_mask, json_body=payload is not None)
        last_error = "unknown error"
        self.api_call_count += 1

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise PlacesError(
                            f"Invalid JSON in Places API response: {exc}"
                        ) from exc
                if response.status_code in _RETRYABLE_STATUSES:
                    last_error = f"HTTP {response.status_code}: {_snippet(response.text)}"
                else:
                    # Non-retryable client error (e.g. 400, 401, 403, 404).
                    raise PlacesError(
                        f"Places API error HTTP {response.status_code}: "
                        f"{_snippet(response.text)}"
                    )

            # Back off before the next attempt, unless this was the last one.
            if attempt < self.max_retries:
                time.sleep(self.backoff_base * (2 ** attempt))

        raise PlacesError(
            f"Places API request to {url} failed after "
            f"{self.max_retries + 1} attempts: {last_error}"
        )

    @staticmethod
    def _normalize_place(place: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten a raw Places API place object into our standard shape."""
        display_name = place.get("displayName") or {}
        if isinstance(display_name, dict):
            name = display_name.get("text", "")
        else:  # be tolerant if the API ever returns a bare string
            name = str(display_name)

        return {
            "place_id": place.get("id", ""),
            "name": name or "",
            "formatted_address": place.get("formattedAddress", ""),
            "national_phone": place.get("nationalPhoneNumber", ""),
            "international_phone": place.get("internationalPhoneNumber", ""),
            "website": place.get("websiteUri", ""),
            "types": place.get("types", []) or [],
            "business_status": place.get("businessStatus", ""),
            "google_maps_uri": place.get("googleMapsUri", ""),
        }
