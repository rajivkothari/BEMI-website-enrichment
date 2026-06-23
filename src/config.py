"""Configuration and shared constants for the enrichment tool.

Loads environment variables from a local ``.env`` file (if present) and
defines the column schema shared across the IO, scoring, and enrichment
modules so there is a single source of truth for the output layout.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load variables from a .env file in the project root, if one exists.
load_dotenv()

# --- Column schemas -------------------------------------------------------

# Columns expected in the input spreadsheet.
INPUT_COLUMNS = ["practice_name", "phone", "city", "state"]

# Columns added by the enrichment step, in output order (appended after the
# original input columns). When the input carries extra columns they are
# preserved ahead of these (see ``enrich.output_columns``).
ENRICHMENT_COLUMNS = [
    "normalized_phone",
    "google_place_id",
    "google_name",
    "google_formatted_address",
    "google_phone",
    "google_website",
    "google_maps_uri",
    "google_business_status",
    "official_website_candidate",
    "website_source",
    "match_score",
    "match_confidence",
    "match_reason",
    "needs_review",
    "verification_notes",
    "error",
    # Blank columns for the human reviewer to fill in.
    "review_decision",
    "reviewer_notes",
]

# Full ordered output schema for the standard 4-column input.
OUTPUT_COLUMNS = INPUT_COLUMNS + ENRICHMENT_COLUMNS

# --- Defaults -------------------------------------------------------------

# Default region used when parsing phone numbers without a country code.
DEFAULT_REGION = "US"

# Google Places API (New) endpoints (requests live in ``google_places.py``).
PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"


@dataclass
class Settings:
    """Runtime configuration resolved from the environment and CLI flags.

    ``api_key`` is excluded from ``repr`` so it can't leak into logs/tracebacks.
    """

    # repr=False keeps secrets out of any accidental log/traceback of Settings.
    api_key: str | None = field(default=None, repr=False)
    region: str = DEFAULT_REGION
    # Optional web-search fallback (configure ONE provider: Serper / Brave /
    # legacy Google CSE). See src/web_search.py.
    web_search_provider: str | None = None
    serper_api_key: str | None = field(default=None, repr=False)
    brave_api_key: str | None = field(default=None, repr=False)
    cse_id: str | None = None
    cse_api_key: str | None = field(default=None, repr=False)

    @property
    def web_search_enabled(self) -> bool:
        """True when a web-search fallback provider is configured."""
        return bool(self.serper_api_key or self.brave_api_key or (self.cse_id and self.cse_api_key))

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a :class:`Settings` instance from environment variables."""
        return cls(
            api_key=os.getenv("GOOGLE_MAPS_API_KEY"),
            region=os.getenv("ENRICH_REGION", DEFAULT_REGION),
            web_search_provider=os.getenv("WEB_SEARCH_PROVIDER"),
            serper_api_key=os.getenv("SERPER_API_KEY"),
            brave_api_key=os.getenv("BRAVE_API_KEY"),
            cse_id=os.getenv("GOOGLE_CSE_ID"),
            cse_api_key=os.getenv("GOOGLE_CSE_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY"),
        )
