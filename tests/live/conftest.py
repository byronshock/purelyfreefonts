"""Fixtures for the live tests (Milestone 2 steps 9 and 11; design-m2 §6, "Live only").

They check a deployed site, after every deploy (``.github/workflows/deploy.yml`` and
``ops/deploy.sh``) and by hand::

    uv run --group browser pytest tests/live --base-url https://staging.trulyfreefonts.com \\
        --expect-commit <sha40> --browser chromium --browser firefox

- ``--base-url`` (pytest-base-url's option, or ours when the browser group isn't installed):
  the site. Without it every live test skips. A host starting ``staging.`` is the test site,
  which must send ``X-Robots-Tag: noindex``; any other host must not.
- ``--expect-commit``: the commit ``version.txt`` must name. Without it, only the format is
  checked.
- ``--built-site DIR``: the build to compare ``robots.txt`` with. Without it, the sample is
  built into a temporary directory (``robots.txt`` doesn't depend on the catalog).

Every test here is marked ``network`` (so ``-m "not network"`` leaves them out, and the no-network
guard of tests/conftest.py lets them through); those that use a browser are also marked
``browser``. They run from a US vantage point in CI, where Cloudflare would inject its beacon if
a setting let it (ops/SERVER.md section G).
"""

import contextlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from tests.live import checks

from tff_site import serve

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
FAKE_COMMIT = "0" * 40
BROWSER_FIXTURES = frozenset(
    {"page", "context", "browser", "browser_type", "new_context", "live_guarded"}
)


def pytest_addoption(parser: pytest.Parser) -> None:
    # pytest-base-url, installed with the browser group, adds --base-url itself.
    with contextlib.suppress(ValueError):
        parser.addoption(
            "--base-url",
            metavar="url",
            default=os.environ.get("PYTEST_BASE_URL"),
            help="base url for the application under test.",
        )
    group = parser.getgroup("tff live", "trulyfreefonts live tests (tests/live)")
    group.addoption(
        "--expect-commit",
        metavar="SHA",
        default=None,
        help="the commit the live version.txt must name",
    )
    group.addoption(
        "--built-site",
        metavar="DIR",
        default=None,
        help="the built site whose robots.txt the live one must equal (default: build the sample)",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every test here ``network``, and those that use a browser ``browser``."""
    for item in items:
        if HERE not in Path(item.path).resolve().parents:
            continue
        item.add_marker(pytest.mark.network)
        if BROWSER_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.browser)


@pytest.fixture(scope="session")
def live_url(request: pytest.FixtureRequest) -> str:
    """The site under test, without a trailing slash."""
    url = request.config.getoption("base_url", default=None)
    if not url:
        pytest.skip("the live tests need --base-url https://…")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def staging(live_url: str) -> bool:
    """Whether the site under test is the test site (M2-D7 (a))."""
    return (urlsplit(live_url).hostname or "").startswith("staging.")


@pytest.fixture(scope="session")
def expect_commit(request: pytest.FixtureRequest) -> str | None:
    return request.config.getoption("expect_commit", default=None)


@pytest.fixture(scope="session")
def expected_headers() -> serve.CaddyHeaders:
    """The headers of ops/caddy/site.caddy at this checkout."""
    return serve.parse_headers(serve.SITE_CADDY.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def http(live_url: str) -> Iterator[Any]:
    """An HTTP client for the site that sends ``Accept: text/html`` (``checks.client``)."""
    with checks.client(live_url) as client:
        yield client


@pytest.fixture(scope="session")
def crawled(http: Any) -> tuple[list[checks.Fetched], list[str]]:
    """One of every kind of response the site sends, and what the crawl couldn't find."""
    return checks.crawl(http)


@pytest.fixture(scope="session")
def built_robots(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """The built ``robots.txt``: from ``--built-site``, or from a build of the sample."""
    given = request.config.getoption("built_site", default=None)
    if given:
        return (Path(given) / "robots.txt").read_bytes()
    from tff_site import build

    out = tmp_path_factory.mktemp("live-built")
    build.build(SAMPLE, out, commit=FAKE_COMMIT, allow_dirty=True, font_files=False)
    return (out / "robots.txt").read_bytes()


@pytest.fixture
def live_guarded(browser: Any, live_url: str) -> Iterator[Callable[..., Any]]:
    """Like tests/site's ``guarded_context``, on the live site: ``live_guarded(**kwargs)``."""
    from tests.site.conftest import Guarded

    made: list[Any] = []

    def make(**kwargs: Any) -> Any:
        context = browser.new_context(base_url=live_url, **kwargs)
        made.append(context)
        parts = urlsplit(live_url)
        guarded = Guarded(context=context, origin=f"{parts.scheme}://{parts.netloc}")
        guarded.attach()
        return guarded

    yield make
    for context in made:
        context.close()
