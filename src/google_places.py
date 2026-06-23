"""Google Maps Platform Places API (New) helpers.

STUBS ONLY for this step. The network calls (:func:`search_text` and
:func:`get_place_details`) intentionally raise ``NotImplementedError`` until
the API integration is built out. Search queries are built upstream by
:func:`src.normalize.build_search_query`.

Planned implementation (Places API - New):
  * ``search_text``       -> POST ``places:searchText`` with a field mask.
  * ``get_place_details`` -> GET ``places/{place_id}`` with a field mask.
Both require the ``GOOGLE_MAPS_API_KEY`` header and return parsed dicts.
"""
from __future__ import annotations

from typing import Any, Optional

import requests

from .config import PLACES_DETAILS_URL, PLACES_TEXT_SEARCH_URL


class PlacesError(RuntimeError):
    """Raised when a Places API request fails."""


def search_text(
    query: str,
    api_key: Optional[str],
    *,
    region: Optional[str] = None,
    session: Optional["requests.Session"] = None,
) -> list[dict[str, Any]]:
    """Search for places matching a free-text query.

    STUB: not implemented yet.

    Planned behavior: POST ``query`` to :data:`PLACES_TEXT_SEARCH_URL` with a
    field mask and return a list of candidate dicts containing at least
    ``place_id``, ``google_name``, and ``google_formatted_address``.

    Raises:
        NotImplementedError: Always, until the integration is built.
    """
    raise NotImplementedError("Google Places Text Search is not implemented yet")


def get_place_details(
    place_id: str,
    api_key: Optional[str],
    *,
    session: Optional["requests.Session"] = None,
) -> dict[str, Any]:
    """Fetch details (website, phone, address) for a ``place_id``.

    STUB: not implemented yet.

    Planned behavior: GET :data:`PLACES_DETAILS_URL` (formatted with
    ``place_id``) with a field mask and return a dict with the
    ``google_*`` fields used by the scorer and output writer.

    Raises:
        NotImplementedError: Always, until the integration is built.
    """
    raise NotImplementedError("Google Places Details is not implemented yet")
