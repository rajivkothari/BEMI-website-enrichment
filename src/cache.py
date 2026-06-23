"""SQLite-backed cache for Google Places API responses.

Caches the *normalized* results returned by
:class:`~src.google_places.GooglePlacesClient` so repeated runs over the same
input don't re-hit (and re-pay for) the Places API.

Tables:
  * ``text_search_cache``  (cache_key, query, response_json, created_at)
  * ``place_details_cache`` (place_id, response_json, created_at)

Entries older than the TTL (default 30 days) are treated as cache misses, so
the client transparently refreshes them.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional, Union

PathLike = Union[str, Path]

DEFAULT_TTL_DAYS = 30
_SECONDS_PER_DAY = 86_400
# Bump this if the normalized response shape changes, to invalidate old keys.
_KEY_VERSION = "v1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS text_search_cache (
    cache_key     TEXT PRIMARY KEY,
    query         TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS place_details_cache (
    place_id      TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at    REAL NOT NULL
);
"""


def _normalize_query(query: Any) -> str:
    """Lowercase + collapse whitespace so equivalent queries share a key."""
    return " ".join(str(query or "").lower().split())


class SQLiteCache:
    """A small SQLite cache with per-entry TTL expiry.

    Args:
        db_path: Path to the SQLite file (parent dirs are created).
        ttl_days: Entries at least this old are treated as misses. ``<= 0``
            disables reuse (every lookup is a miss).
    """

    def __init__(self, db_path: PathLike, ttl_days: float = DEFAULT_TTL_DAYS) -> None:
        self.db_path = Path(db_path)
        self.ttl_seconds = float(ttl_days) * _SECONDS_PER_DAY
        if self.db_path.parent:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- text search ------------------------------------------------------

    @staticmethod
    def text_search_key(query: Any) -> str:
        """Stable cache key derived from the normalized query."""
        payload = f"{_KEY_VERSION}:{_normalize_query(query)}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get_text_search(self, query: Any) -> Optional[Any]:
        """Return the cached (non-expired) result for ``query``, or ``None``."""
        row = self._conn.execute(
            "SELECT response_json, created_at FROM text_search_cache WHERE cache_key = ?",
            (self.text_search_key(query),),
        ).fetchone()
        return self._unpack(row)

    def set_text_search(
        self, query: Any, response: Any, created_at: Optional[float] = None
    ) -> None:
        """Store ``response`` for ``query`` (``created_at`` defaults to now)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO text_search_cache "
            "(cache_key, query, response_json, created_at) VALUES (?, ?, ?, ?)",
            (
                self.text_search_key(query),
                str(query),
                json.dumps(response),
                time.time() if created_at is None else created_at,
            ),
        )
        self._conn.commit()

    # -- place details ----------------------------------------------------

    def get_place_details(self, place_id: str) -> Optional[Any]:
        """Return the cached (non-expired) details for ``place_id``, or ``None``."""
        row = self._conn.execute(
            "SELECT response_json, created_at FROM place_details_cache WHERE place_id = ?",
            (place_id,),
        ).fetchone()
        return self._unpack(row)

    def set_place_details(
        self, place_id: str, response: Any, created_at: Optional[float] = None
    ) -> None:
        """Store ``response`` for ``place_id`` (``created_at`` defaults to now)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO place_details_cache "
            "(place_id, response_json, created_at) VALUES (?, ?, ?)",
            (
                place_id,
                json.dumps(response),
                time.time() if created_at is None else created_at,
            ),
        )
        self._conn.commit()

    # -- helpers ----------------------------------------------------------

    def _unpack(self, row: Optional[tuple]) -> Optional[Any]:
        if row is None:
            return None
        response_json, created_at = row
        if self._is_expired(created_at):
            return None
        try:
            return json.loads(response_json)
        except (ValueError, TypeError):
            return None

    def _is_expired(self, created_at: float) -> bool:
        if self.ttl_seconds <= 0:
            return True
        return (time.time() - float(created_at)) >= self.ttl_seconds

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SQLiteCache":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
