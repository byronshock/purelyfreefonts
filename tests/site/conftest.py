"""Fixtures for the site tests: a built site, a server, and guarded browser contexts.

- **Browser tests** are those that use a Playwright fixture (``page``, ``context``,
  ``browser``, ``guarded_context`` and so on). They get the ``browser`` marker at collection,
  so ``-m "not browser"`` deselects them before any fixture runs, and this module imports no
  Playwright code at import time. Browsers come from pytest-playwright's ``--browser``;
  locally, ``TFF_CHROMIUM=/usr/bin/chromium`` swaps in a system Chromium.
- ``site_data`` (session): ``TFF_SITE_DATA``, or the sample catalog.
- ``site_dir`` (session): ``TFF_SITE_DIR`` if set (a site built elsewhere, as in CI), else a
  fresh ``tff-site build`` of ``site_data``. Font files are included when all of them are in
  the font cache (run ``tff-site fetch-fonts`` first), and left out otherwise.
- ``site_url`` (session): ``http://127.0.0.1:8080`` when ``TFF_CADDY=1`` (CI serves the site
  with real Caddy and ``ops/caddy/ci.Caddyfile``); otherwise ``tff_site.serve`` in a thread.
- ``guarded_context``: a factory for contexts that load ``guards.js`` before any page
  script, record every request, abort requests to any other origin and collect console
  messages and page errors. Routing turns off the HTTP cache, so performance tests use plain
  contexts instead. A page it opens keeps the context's ``color_scheme`` (``keep_scheme``).
- ``no_network``: fails any connection or name lookup that isn't the loopback interface.
"""

import os
import re
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
GUARDS_JS = HERE / "guards.js"
CI_CADDY_URL = "http://127.0.0.1:8080"
FAKE_COMMIT = "0" * 40
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
# Chromium: "... violates the following Content Security Policy directive ..." or "Refused to
# load ..."; Firefox: "Content-Security-Policy: The page's settings blocked ...".
CSP_MESSAGE = re.compile(r"content[- ]security[- ]policy|refused to", re.IGNORECASE)
SCHEME_JS = "(dark) => matchMedia('(prefers-color-scheme: dark)').matches === dark"

# Requesting any of these makes a test a browser test.
BROWSER_FIXTURES = frozenset(
    {
        "page",
        "context",
        "browser",
        "browser_type",
        "browser_context_args",
        "new_context",
        "guarded_context",
    }
)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark tests under tests/site that use a Playwright fixture with ``browser``."""
    for item in items:
        if HERE not in Path(item.path).resolve().parents:
            continue
        if BROWSER_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.browser)


@pytest.fixture(scope="session")
def site_data() -> Path:
    """The catalog-site file the site is built from."""
    return Path(os.environ.get("TFF_SITE_DATA", SAMPLE))


@pytest.fixture(scope="session")
def site_dir(site_data: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A built site directory (see the module docstring)."""
    given = os.environ.get("TFF_SITE_DIR")
    if given:
        return Path(given)
    from tff_site import build, data, fonts

    catalog = data.load(site_data)
    wanted = [f["font_file"]["sha256"] for f in catalog["fonts"] if f["font_file"]]
    cached = all(fonts.cache_path(sha).is_file() for sha in wanted)
    out = tmp_path_factory.mktemp("site")
    build.build(site_data, out, commit=FAKE_COMMIT, allow_dirty=True, font_files=cached)
    return out


@pytest.fixture(scope="session")
def site_url(site_dir: Path) -> Iterator[str]:
    """Base URL of the served site, without a trailing slash."""
    if os.environ.get("TFF_CADDY") == "1":
        yield CI_CADDY_URL
        return
    from tff_site import serve

    server = serve.make_server(site_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, name="tff-site-serve", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict, site_url: str) -> dict:
    """pytest-playwright's context arguments, with ``base_url`` set to the served site."""
    return {**browser_context_args, "base_url": site_url}


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict, browser_name: str) -> dict:
    """Use ``TFF_CHROMIUM`` as the Chromium executable when it is set."""
    executable = os.environ.get("TFF_CHROMIUM")
    if executable and browser_name == "chromium":
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args


@dataclass
class Guarded:
    """A browser context with the privacy guards attached, and what they recorded."""

    context: Any
    origin: str
    color_scheme: str | None = None  # the context's, re-applied to each page (keep_scheme)
    requests: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    responses: list[Any] = field(default_factory=list)
    console: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    _pages: set[int] = field(default_factory=set)

    def attach(self) -> None:
        """Start recording; called once by the fixture."""
        # Playwright tags each handler with an attribute, so they must be plain functions:
        # builtin and bound methods refuse new attributes.
        self.context.add_init_script(path=str(GUARDS_JS))
        self.context.on("request", lambda request: self.requests.append(request.url))
        self.context.on("response", lambda response: self.responses.append(response))
        self.context.on("page", lambda page: self._watch(page))
        self.context.route("**/*", lambda route: self._route(route))

    def new_page(self) -> Any:
        """Open a page in this context, with console and error capture, keeping the
        context's colour scheme (``keep_scheme``)."""
        page = self.context.new_page()
        self._watch(page)
        if self.color_scheme in ("light", "dark"):
            keep_scheme(page, self.color_scheme)
        return page

    def records(self, page: Any) -> dict[str, list]:
        """Return what ``guards.js`` recorded in ``page``."""
        return page.evaluate("() => JSON.parse(JSON.stringify(window.__tffGuards))")

    def csp_messages(self) -> list[str]:
        """Console messages that report a CSP violation, in Chromium's or Firefox's wording."""
        return [text for _, text in self.console if CSP_MESSAGE.search(text)]

    def cookie_responses(self) -> list[str]:
        """URLs whose response set a cookie."""
        return [r.url for r in self.responses if "set-cookie" in r.all_headers()]

    def assert_clean(self, page: Any) -> None:
        """Fail on any foreign request, cookie, stored data, CSP violation or page error."""
        assert self.blocked == [], "requests to another origin"
        assert self.cookie_responses() == [], "responses that set a cookie"
        assert self.context.cookies() == [], "cookies in the context"
        records = self.records(page)
        assert {k: v for k, v in records.items() if v} == {}, "storage or CSP events"
        assert self.csp_messages() == [], "CSP messages in the console"
        assert self.errors == [], "page errors"
        state = self.context.storage_state(indexed_db=True)
        assert state == {"cookies": [], "origins": []}, "browser storage"

    def _watch(self, page: Any) -> None:
        if id(page) in self._pages:
            return
        self._pages.add(id(page))
        page.on("console", lambda message: self.console.append((message.type, message.text)))
        page.on("pageerror", lambda error: self.errors.append(str(error)))

    def _route(self, route: Any) -> None:
        url = route.request.url
        if _origin(url) == self.origin:
            route.continue_()
        else:
            self.blocked.append(url)
            route.abort()


def keep_scheme(page: Any, scheme: str) -> None:
    """Make ``page``'s first ``goto`` end in ``scheme`` (light or dark), and check it.

    Playwright's Firefox drops the context's ``color_scheme`` when a navigation lands on a
    page sent with ``Cross-Origin-Opener-Policy: same-origin`` (site.caddy's header: the
    process swap loses it, while ``forced_colors`` and ``reduced_motion`` survive), so a
    "dark" test would silently run light. A page-level scheme set after that sticks, so the
    first ``goto`` sets it and reloads when the page isn't in the scheme already.
    """
    goto = page.goto
    first = True

    def goto_in_scheme(url: str, **kwargs: Any) -> Any:
        nonlocal first
        response = goto(url, **kwargs)
        if first:
            first = False
            if not page.evaluate(SCHEME_JS, scheme == "dark"):
                page.emulate_media(color_scheme=scheme)
                page.reload(wait_until=kwargs.get("wait_until", "load"))
            assert page.evaluate(SCHEME_JS, scheme == "dark"), f"the {scheme} scheme isn't on"
        return response

    page.goto = goto_in_scheme


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@pytest.fixture
def guarded_context(browser: Any, site_url: str) -> Iterator[Callable[..., Guarded]]:
    """Factory: ``guarded_context(**new_context_kwargs) -> Guarded``; contexts close at teardown."""
    made: list[Any] = []

    def make(**kwargs: Any) -> Guarded:
        context = browser.new_context(base_url=site_url, **kwargs)
        made.append(context)
        scheme = kwargs.get("color_scheme")
        guarded = Guarded(context=context, origin=_origin(site_url), color_scheme=scheme)
        guarded.attach()
        return guarded

    yield make
    for context in made:
        context.close()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any connection or DNS lookup outside the loopback interface."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def allowed(sock: socket.socket, address: Any) -> bool:
        if sock.family == getattr(socket, "AF_UNIX", object()):
            return True
        return isinstance(address, tuple) and address[0] in LOOPBACK

    def connect(self: socket.socket, address: Any) -> None:
        if not allowed(self, address):
            raise AssertionError(f"network access attempted: {address!r}")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if not allowed(self, address):
            raise AssertionError(f"network access attempted: {address!r}")
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in LOOPBACK and host is not None:
            raise AssertionError(f"name lookup attempted: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
