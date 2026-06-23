"""Tests for src.website_verify (mocked HTTP — no real network)."""
import requests

from src.website_verify import verify_website

HTML = """
<html>
  <head><title>Acme Dental</title><style>.x{color:red}</style></head>
  <body>
    <h1>Acme Dental</h1>
    <p>Call us at (415) 555-0182 to book an appointment.</p>
    <a href="tel:+14155550182">Tap to call</a>
    <footer>123 Market St, San Francisco, CA 94103</footer>
    <script>var hidden = "(999) 999-9999";</script>
  </body>
</html>
"""


class FakeResponse:
    def __init__(self, text="", status_code=200, url="https://acme.example/"):
        self.text = text
        self.status_code = status_code
        self.url = url


class FakeSession:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    def get(self, url, timeout=None, allow_redirects=None, headers=None):
        self.calls.append(
            {"url": url, "timeout": timeout, "allow_redirects": allow_redirects, "headers": headers}
        )
        if self._exc is not None:
            raise self._exc
        return self._response


def verify(html, *args, **kwargs):
    session = FakeSession(FakeResponse(html))
    return verify_website(*args, session=session, **kwargs), session


class TestMatches:
    def test_all_match(self):
        result, _ = verify(HTML, "https://acme.example", "(415) 555-0182", "San Francisco", "CA")
        assert result["http_status"] == 200
        assert result["final_url"] == "https://acme.example/"
        assert result["website_phone_match"] is True
        assert result["website_city_match"] is True
        assert result["website_state_match"] is True
        assert "phone=match" in result["verification_notes"]

    def test_phone_via_tel_link_when_not_in_text(self):
        html = '<html><body><a href="tel:+1 415-555-0182">call</a></body></html>'
        result, _ = verify(html, "https://x.example", "(415) 555-0182", "Nowhere", "CA")
        assert result["website_phone_match"] is True

    def test_script_only_number_is_ignored(self):
        # (999) 999-9999 lives in a <script>; it must not count as visible text.
        result, _ = verify(HTML, "https://acme.example", "(999) 999-9999", "Reno", "NV")
        assert result["website_phone_match"] is False
        assert result["website_city_match"] is False
        assert result["website_state_match"] is False

    def test_absent_details_do_not_match(self):
        result, _ = verify(HTML, "https://acme.example", "(212) 555-1212", "Boston", "MA")
        assert result["website_phone_match"] is False
        assert result["website_city_match"] is False
        assert result["website_state_match"] is False


class TestRequestShape:
    def test_follows_redirects_and_sends_user_agent(self):
        result, session = verify(HTML, "https://acme.example", "", "", "")
        call = session.calls[0]
        assert call["allow_redirects"] is True
        assert "User-Agent" in call["headers"]


class TestFailuresAreNonFatal:
    def test_fetch_failure_marks_notes(self):
        session = FakeSession(exc=requests.ConnectionError("boom"))
        result = verify_website("https://down.example", "(415) 555-0182", "SF", "CA", session=session)
        assert result["http_status"] is None
        assert result["website_phone_match"] is False
        assert result["verification_notes"].startswith("fetch failed")

    def test_http_error_status(self):
        session = FakeSession(FakeResponse("", status_code=404))
        result = verify_website("https://x.example", "", "", "", session=session)
        assert result["http_status"] == 404
        assert "HTTP 404" in result["verification_notes"]

    def test_empty_url(self):
        result = verify_website("", "(415) 555-0182", "SF", "CA")
        assert result["http_status"] is None
        assert result["verification_notes"] == "no website to verify"
