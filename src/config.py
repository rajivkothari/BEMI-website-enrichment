"""Configuration and shared constants for the enrichment tool.

Loads environment variables from a local ``.env`` file (if present) and
defines the column schema shared across the IO, scoring, and enrichment
modules so there is a single source of truth for the output layout.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

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
    "google_business_status",
    "match_score",
    "match_confidence",
    "match_reason",
    "needs_review",
    "verification_notes",
    "error",
]

# Full ordered output schema for the standard 4-column input.
OUTPUT_COLUMNS = INPUT_COLUMNS + ENRICHMENT_COLUMNS

# --- Defaults -------------------------------------------------------------

# Default region used when parsing phone numbers without a country code.
DEFAULT_REGION = "US"

# Confidence (0.0-1.0) at or above which a match is trusted; below this a
# row is flagged with ``needs_review = True``.
REVIEW_THRESHOLD = 0.75

# Google Places API (New) endpoints. The actual requests are implemented in
# ``google_places.py`` in a later step.
PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"


@dataclass
class Settings:
    """Runtime configuration resolved from the environment and CLI flags."""

    api_key: str | None = None
    region: str = DEFAULT_REGION
    review_threshold: float = REVIEW_THRESHOLD

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a :class:`Settings` instance from environment variables."""
        return cls(
            api_key=os.getenv("GOOGLE_MAPS_API_KEY"),
            region=os.getenv("ENRICH_REGION", DEFAULT_REGION),
            review_threshold=float(
                os.getenv("ENRICH_REVIEW_THRESHOLD", str(REVIEW_THRESHOLD))
            ),
        )
