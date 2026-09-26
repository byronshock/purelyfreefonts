"""The live site's headers and HTML (Milestone 2 step 9; design-m2 §6, "Live only").

Over plain HTTP with ``Accept: text/html`` (``tests/live/checks.py``): site.caddy's CSP and other
security headers on HTML, JS, JSON, SVG and 404 responses; no ``Set-Cookie``, ``NEL`` or
``Report-To``; ``X-Robots-Tag`` only on the test site; site.caddy's ``Cache-Control``; no
Cloudflare code in any HTML; ``robots.txt`` equal to the built file; and a vantage point where
Cloudflare would inject its beacon if a setting let it.
"""

from typing import Any

import pytest
from tests.live import checks


def test_the_crawl_finds_every_kind_of_file(crawled: tuple[list[Any], list[str]]) -> None:
    fetched, problems = crawled
    assert problems == []
    assert {f.kind for f in fetched} >= checks.CSP_KINDS


def test_every_response_has_the_site_headers_and_none_that_track(
    crawled: tuple[list[Any], list[str]], expected_headers: Any, staging: bool
) -> None:
    fetched, _ = crawled
    assert checks.header_problems(fetched, expected_headers, staging=staging) == []


def test_the_html_has_no_cloudflare_code(crawled: tuple[list[Any], list[str]]) -> None:
    fetched, _ = crawled
    assert checks.html_problems(fetched) == []


def test_robots_txt_is_the_built_file(
    crawled: tuple[list[Any], list[str]], built_robots: bytes
) -> None:
    fetched, _ = crawled
    served = next(f for f in fetched if f.path == "/robots.txt")
    assert checks.robots_problems(served, built_robots) == []


def test_the_checks_run_where_cloudflare_would_inject(http: Any) -> None:
    """A clean result from the EU, EEA, UK or Switzerland proves nothing about the beacon
    (ops/SERVER.md section G), so the run says so instead of passing quietly."""
    location = checks.cloudflare_location(http)
    if location is None:
        pytest.skip("the site isn't behind Cloudflare (no /cdn-cgi/trace)")
    if location in checks.NO_BEACON_LOCATIONS:
        pytest.skip(
            f"Cloudflare sees this client in {location}, where Web Analytics injects nothing: "
            "run the live tests from the US (CI does) to check the beacon"
        )
    assert location
