"""Web-search fallback for finding a practice's official website.

When the Google *Places* listing has no website, this searches the web and
accepts a result **only if it looks like the practice's own standalone site**
— a non-directory domain whose name matches the practice. Group/parent or
directory results are not accepted; they're surfaced as a review candidate.

Provider-agnostic: the matching logic is independent of the search backend.
Backends (pick one; configure its key in ``.env``):
  * Serper.dev   — Google results, simplest drop-in     (SERPER_API_KEY)
  * Brave Search — independent index, free tier          (BRAVE_API_KEY)
  * Google CSE   — only legacy "whole-web" engines now   (GOOGLE_CSE_ID + key)

(As of Jan 2026, new Google Programmable Search Engines can no longer search
the whole web, so Serper or Brave are the practical choices.)
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

import requests

from . import normalize, scoring

_RETRYABLE = {429, 500, 502, 503, 504}

# Generic words that don't make a domain "belong" to a specific practice.
_GENERIC_TOKENS = {
    "obgyn", "ob", "gyn", "md", "do", "llc", "pc", "pa", "inc", "plc", "dr",
    "the", "and", "of", "clinic", "center", "centre", "medical", "medicine",
    "health", "healthcare", "care", "associates", "group", "practice", "office",
    "family", "womens", "women", "mens", "pediatrics", "dental", "veterinary",
    "optometry", "wellness", "specialists", "physicians",
}


class WebSearchError(RuntimeError):
    """Raised when a web-search request fails or is misconfigured."""


# --- Standalone-site matching (provider-independent) ----------------------

def _registrable_domain(url: str) -> str:
    host = urlsplit(url if "//" in url else "http://" + url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _name_tokens(practice_name: Any) -> List[str]:
    text = normalize.normalize_text(practice_name)
    return [t for t in re.split(r"[^a-z0-9]+", text) if len(t) >= 4 and t not in _GENERIC_TOKENS]


def _is_standalone(practice_name: Any, url: str) -> bool:
    """True if the domain contains a distinctive token from the practice name."""
    flat = re.sub(r"[^a-z0-9]", "", _registrable_domain(url))
    return any(tok in flat for tok in _name_tokens(practice_name))


def choose_website(practice_name: Any, results: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Pick a standalone official site from web results (or a review candidate).

    Returns ``{"website", "candidate", "is_standalone", "reason"}``. ``website``
    is set only when a non-directory, name-matching domain is found.
    """
    non_directory = []
    for item in results:
        link = str(item.get("link") or item.get("url") or "").strip()
        if link and not scoring.is_directory_website(link):
            non_directory.append(link)

    if not non_directory:
        return {"website": "", "candidate": "", "is_standalone": False,
                "reason": "web search: only directory/aggregator results"}

    for link in non_directory:
        if _is_standalone(practice_name, link):
            return {"website": link, "candidate": link, "is_standalone": True,
                    "reason": f"web search: standalone site {_registrable_domain(link)}"}

    top = non_directory[0]
    return {"website": "", "candidate": top, "is_standalone": False,
            "reason": f"web search: possible group/parent site {_registrable_domain(top)} "
                      "(not standalone) — review"}


# --- Search backends ------------------------------------------------------

def _request_json(session, method, url, *, headers=None, params=None, json=None,
                  timeout, max_retries, backoff_base) -> Dict[str, Any]:
    last = "unknown error"
    for attempt in range(max_retries + 1):
        try:
            resp = session.request(method, url, headers=headers, params=params, json=json, timeout=timeout)
        except requests.RequestException as exc:
            last = f"network error: {exc}"
        else:
            if resp.status_code < 400:
                return resp.json()
            if resp.status_code in _RETRYABLE:
                last = f"HTTP {resp.status_code}"
            else:
                raise WebSearchError(f"web search HTTP {resp.status_code}: {resp.text[:200]}")
        if attempt < max_retries:
            time.sleep(backoff_base * (2 ** attempt))
    raise WebSearchError(f"web search failed after {max_retries + 1} attempts: {last}")


class _BaseSearcher:
    def __init__(self, *, session=None, timeout=10.0, max_retries=2, backoff_base=0.5):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def _json(self, method, url, **kw):
        return _request_json(self.session, method, url, timeout=self.timeout,
                             max_retries=self.max_retries, backoff_base=self.backoff_base, **kw)


class SerperClient(_BaseSearcher):
    """Serper.dev — Google search results via a simple REST API."""

    URL = "https://google.serper.dev/search"

    def __init__(self, api_key: Optional[str], **kw) -> None:
        if not api_key:
            raise WebSearchError("Serper needs SERPER_API_KEY (https://serper.dev).")
        super().__init__(**kw)
        self.api_key = api_key

    def search(self, query: str, num: int = 5) -> List[Dict[str, Any]]:
        data = self._json("POST", self.URL,
                          headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                          json={"q": query, "num": num})
        return [{"link": it.get("link", ""), "title": it.get("title", ""),
                 "snippet": it.get("snippet", "")} for it in data.get("organic", [])]


class BraveClient(_BaseSearcher):
    """Brave Search API — independent web index."""

    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: Optional[str], **kw) -> None:
        if not api_key:
            raise WebSearchError("Brave needs BRAVE_API_KEY (https://brave.com/search/api).")
        super().__init__(**kw)
        self.api_key = api_key

    def search(self, query: str, num: int = 5) -> List[Dict[str, Any]]:
        data = self._json("GET", self.URL,
                          headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
                          params={"q": query, "count": num})
        results = (data.get("web") or {}).get("results", [])
        return [{"link": it.get("url", ""), "title": it.get("title", ""),
                 "snippet": it.get("description", "")} for it in results]


class CustomSearchClient(_BaseSearcher):
    """Google Custom Search JSON API (legacy whole-web engines only)."""

    URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: Optional[str], cx: Optional[str], **kw) -> None:
        if not api_key or not cx:
            raise WebSearchError("Custom Search needs GOOGLE_CSE_ID and an API key.")
        super().__init__(**kw)
        self.api_key = api_key
        self.cx = cx

    def search(self, query: str, num: int = 5) -> List[Dict[str, Any]]:
        data = self._json("GET", self.URL,
                          params={"key": self.api_key, "cx": self.cx, "q": query, "num": num})
        return [{"link": it.get("link", ""), "title": it.get("title", ""),
                 "snippet": it.get("snippet", "")} for it in data.get("items", [])]


def make_searcher(settings) -> Optional[_BaseSearcher]:
    """Build a searcher from settings (provider override or first key present)."""
    provider = (getattr(settings, "web_search_provider", None) or "").strip().lower()
    try:
        if provider in ("serper", "") and getattr(settings, "serper_api_key", None):
            return SerperClient(settings.serper_api_key)
        if provider in ("brave", "") and getattr(settings, "brave_api_key", None):
            return BraveClient(settings.brave_api_key)
        if provider in ("google_cse", "cse", "") and getattr(settings, "cse_id", None):
            return CustomSearchClient(settings.cse_api_key, settings.cse_id)
    except WebSearchError:
        return None
    return None


# --- Integration ----------------------------------------------------------

def fill_missing_website(row: Dict[str, Any], *, practice_name: Any, city: Any, state: Any,
                         searcher: "_BaseSearcher") -> Dict[str, Any]:
    """If ``row`` has no official website, try a web search to find one.

    A standalone match becomes the official website (source ``web_search``,
    flagged for review); a group/parent candidate is recorded in
    ``reviewer_notes`` without being accepted. Never raises.
    """
    if str(row.get("official_website_candidate") or "").strip():
        return row
    query = " ".join(str(p) for p in (practice_name, city, state) if p and str(p).strip())
    try:
        results = searcher.search(query)
    except WebSearchError:
        return row

    pick = choose_website(practice_name, results)
    if pick["website"]:
        row["google_website"] = pick["website"]
        row["official_website_candidate"] = pick["website"]
        row["website_source"] = "web_search"
        row["match_confidence"] = "medium"
        row["match_score"] = max(int(row.get("match_score") or 0), 60)
    elif pick["candidate"]:
        existing = str(row.get("reviewer_notes") or "").strip()
        row["reviewer_notes"] = (f"{existing} web candidate: {pick['candidate']}").strip()
    row["needs_review"] = True
    row["match_reason"] = (str(row.get("match_reason") or "") + "; " + pick["reason"]).strip("; ")
    return row
