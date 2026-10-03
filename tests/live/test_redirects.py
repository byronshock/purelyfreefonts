"""Every old address 301s to the same path and query on the site (the rename of 2026-10-02).

AUTHORITY.md, "Name and domain": the canonical site is https://purelyfreefonts.com, and these
names answer with a 301 to the same path and query on it. Caddy redirects trulyfreefonts.com,
.org and .net and the ``www.`` hosts; Cloudflare's edge redirects truelyfreefonts.com and
purelyfreefonts.org and .net (ops/SERVER.md section K). The test site's old name,
``staging.trulyfreefonts.com``, 301s to ``staging.purelyfreefonts.com``.

Over https each old name takes exactly one 301. Over http, Cloudflare's Always Use HTTPS may first
send the browser to the same name over https, so the chain may have two hops; it must still end
at the same URL. No response may set a cookie or carry Network Error Logging.

These checks run only with ``--check-redirects``: until the cutover the old names still serve the
site themselves.
"""

from urllib.parse import urlsplit

import httpx
import pytest
from tests.live import checks

PATH = "/a/b?c=1"
PRODUCTION_OLD = (
    "www.purelyfreefonts.com",
    "trulyfreefonts.com",
    "www.trulyfreefonts.com",
    "trulyfreefonts.org",
    "www.trulyfreefonts.org",
    "trulyfreefonts.net",
    "www.trulyfreefonts.net",
    "truelyfreefonts.com",
    "www.truelyfreefonts.com",
    "purelyfreefonts.org",
    "www.purelyfreefonts.org",
    "purelyfreefonts.net",
    "www.purelyfreefonts.net",
)
STAGING_OLD = ("staging.trulyfreefonts.com",)
TRACKING = ("set-cookie", "nel", "report-to")


def _hop(url: str) -> tuple[int, str, list[str]]:
    """One request, not followed: the status, the Location and any tracking headers."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    try:
        with checks.client(origin) as http:
            response = http.get(url[len(origin) :])
    except httpx.HTTPError as error:  # name not resolving yet, refused, TLS: report, don't stop
        return 0, f"{type(error).__name__}: {error}", []
    tracking = [name for name in TRACKING if name in response.headers]
    return response.status_code, response.headers.get("location", ""), tracking


@pytest.fixture(scope="module")
def target(request: pytest.FixtureRequest, live_url: str) -> str:
    if not request.config.getoption("--check-redirects"):
        pytest.skip("redirect checks run only with --check-redirects (from the cutover on)")
    parts = urlsplit(live_url)
    return f"{parts.scheme}://{parts.netloc}"


def _old_names(target: str) -> tuple[str, ...]:
    return STAGING_OLD if urlsplit(target).hostname.startswith("staging.") else PRODUCTION_OLD


def test_every_old_name_over_https_takes_one_301_to_the_same_path(target: str) -> None:
    problems = []
    for name in _old_names(target):
        status, location, tracking = _hop(f"https://{name}{PATH}")
        if status != 301 or location != target + PATH or tracking:
            problems.append((name, status, location, tracking))
    assert problems == []


def test_every_old_name_over_http_ends_at_the_same_path(target: str) -> None:
    problems = []
    for name in _old_names(target):
        url, hops = f"http://{name}{PATH}", []
        for _ in range(2):
            status, location, tracking = _hop(url)
            hops.append((status, location, tracking))
            if status != 301 or tracking:
                break
            url = location
            if url == target + PATH:
                break
        if url != target + PATH or any(h[0] != 301 or h[2] for h in hops):
            problems.append((name, hops))
    assert problems == []
