"""Lightweight website verification.

Fetches a candidate's homepage and checks whether the input phone, city, and
state appear on it — a cheap signal that the matched website really belongs to
the practice. Homepage only (no crawling).

This module never raises: any fetch/parse problem is reported through
``verification_notes`` with all match flags left ``False``, so a slow or broken
site can never fail an enrichment row.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import phonenumbers
import requests
from bs4 import BeautifulSoup

from . import normalize
from .config import DEFAULT_REGION

_USER_AGENT = "Mozilla/5.0 (compatible; BEMI-enrichment/0.1; website verification)"
_HEADERS = {"User-Agent": _USER_AGENT}

# Tags whose contents are not "visible-ish" page text.
_STRIP_TAGS = ["script", "style", "noscript", "template", "head", "svg"]


def _blank_result(url: str) -> Dict[str, Any]:
    return {
        "final_url": url or "",
        "http_status": None,
        "website_phone_match": False,
        "website_city_match": False,
        "website_state_match": False,
        "verification_notes": "",
    }


def _phone_numbers(soup: BeautifulSoup, text: str, region: str) -> set:
    """Collect E.164 numbers from ``tel:`` links and visible text."""
    found: set = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href[:4].lower() == "tel:":
            e164 = normalize.normalize_phone(href[4:], region)["e164"]
            if e164:
                found.add(e164)
    try:
        for match in phonenumbers.PhoneNumberMatcher(text, region):
            found.add(
                phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
            )
    except Exception:  # noqa: BLE001 - the matcher must not break verification
        pass
    return found


def verify_website(
    url: str,
    input_phone: Any,
    city: Any,
    state: Any,
    timeout: float = 10,
    *,
    session: Optional[requests.Session] = None,
    region: str = DEFAULT_REGION,
) -> Dict[str, Any]:
    """Fetch ``url`` (following redirects) and check for the input details.

    Args:
        url: Homepage URL to verify.
        input_phone: Input phone to look for (matched by E.164).
        city: Input city to look for.
        state: Input state to look for.
        timeout: Per-request timeout in seconds.
        session: Optional shared :class:`requests.Session`.
        region: Default region for phone parsing.

    Returns:
        A dict with ``final_url``, ``http_status``, ``website_phone_match``,
        ``website_city_match``, ``website_state_match``, and
        ``verification_notes``. Never raises.
    """
    result = _blank_result(str(url) if url else "")
    if not url or not str(url).strip():
        result["verification_notes"] = "no website to verify"
        return result

    getter = session or requests
    try:
        response = getter.get(url, timeout=timeout, allow_redirects=True, headers=_HEADERS)
    except requests.RequestException as exc:
        result["verification_notes"] = f"fetch failed: {type(exc).__name__}: {exc}"
        return result

    result["final_url"] = getattr(response, "url", url) or url
    result["http_status"] = response.status_code
    if response.status_code >= 400:
        result["verification_notes"] = f"unreachable: HTTP {response.status_code}"
        return result

    try:
        soup = BeautifulSoup(response.text or "", "html.parser")
        for tag in soup(_STRIP_TAGS):
            tag.decompose()
        text = soup.get_text(separator=" ")
    except Exception as exc:  # noqa: BLE001 - parsing must not fail the row
        result["verification_notes"] = f"parse failed: {type(exc).__name__}: {exc}"
        return result

    phones = _phone_numbers(soup, text, region)
    target = normalize.normalize_phone(input_phone, region)["e164"]
    phone_match = bool(target and target in phones)
    city_match = normalize.city_in_text(text, city)
    state_match = normalize.state_in_text(text, state)

    result["website_phone_match"] = phone_match
    result["website_city_match"] = city_match
    result["website_state_match"] = state_match
    result["verification_notes"] = (
        f"fetched ({response.status_code}); "
        f"phone={'match' if phone_match else 'no'}, "
        f"city={'match' if city_match else 'no'}, "
        f"state={'match' if state_match else 'no'}"
    )
    return result
