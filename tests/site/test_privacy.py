"""Privacy (Milestone 2 step 9; design-m2 §6, "Privacy"): whatever a visitor does, the site loads
only its own files and stores nothing.

**The sweep** (``Sweep``; ``tests/live/test_privacy_live.py`` runs it against the live site)
opens every page (the sitemap's, and a missing one for the 404 page), each in its own tab of a
``guarded_context`` (``guards.js`` recorders; every request recorded, and requests to any other
origin aborted). On the list it chooses every rank and, under each, sets and clears every filter
control; searches, once for nothing; clears filters; uses the phone filters button; opens
details panels (every font, or an even spread of ``max_details`` on a long list) and a
``#font=`` link in a new tab; loads "Type your own text" for up to ``max_type_own`` fonts
(checking each font file came from this site) and types in it; and scrolls the whole list a
screen at a time until every specimen has been requested. ``findings()`` then reports, and the
tests fail on:

- a request to another origin, or to ``/cdn-cgi/``, or a WebSocket;
- a request to Stripe (the sweep never clicks the tip link);
- a cookie in the context, or a ``Set-Cookie`` on any response;
- anything in ``storage_state(indexed_db=True)``, or anything the recorders saw: storage and
  cookie writes, IndexedDB, Cache Storage, service workers, ``cookieStore``, CSP violations;
- anything left in ``localStorage``, ``sessionStorage`` (which ``storage_state`` leaves out),
  Cache Storage or a service worker registration, however it was written;
- a CSP message or a Permissions-Policy error (an unknown feature) in the console, or a page
  error;
- a ``<link>`` that prefetches, prerenders or preconnects, a speculation-rules script or header,
  a ``Link`` header that does, or a request the browser marks as a prefetch;
- a script or loading ``<link>`` from anywhere else, or an inline script;
- a same-origin response without site.caddy's CSP, or with ``NEL``, ``Report-To`` or
  ``Reporting-Endpoints``;
- ``cloudflareinsights``, ``data-cf-beacon`` or ``/cdn-cgi/`` in any HTML the browser got.

**This module's site** (it overrides ``site_dir`` and ``site_url``): a build of ``site_data`` in
which every font with a preview has a specimen (a committed sample specimen stands in where the
data has none, as in test_specimens_loader.py) and every font with a ``font_file`` has its file
(the cached one from ``tff-site fetch-fonts``, or a tiny generated stand-in font when it isn't
cached, as in CI's browser jobs, so "Type your own text" is always swept), with the tip link on
(``build.TIP_URL``, or the link in ops/DONATIONS.md until step 8 sets it), served by
``tff_site.serve`` with site.caddy's headers.

Also here: the tip link is a plain link and the only way to Stripe; without JavaScript the pages
stay on this site too; the built files point nowhere else and the script names no storage API;
the sweep visits every page the build writes; the sweep and the live checks
(``tests/live/checks.py``) catch what they are for (a planted cookie, storage write, CSP
violation, prefetch link, foreign request, injected beacon, inline script, page error and bad
headers); and the live checks pass against this build.
"""

import hashlib
import io
import json
import math
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from tests.live import checks

from tff_site import serve

ROOT = Path(__file__).resolve().parents[2]
STANDINS = ROOT / "tests" / "fixtures" / "specimens"
DONATIONS = ROOT / "ops" / "DONATIONS.md"
FAKE_COMMIT = "0" * 40
WIDE = {"width": 1280, "height": 900}
PHONE = {"width": 375, "height": 812}
NOT_FOUND_PATH = checks.NOT_FOUND_PATH
SEARCH_NOTHING = "zzqx no such font"
TYPE_TEXT = "Hamburgefonstiv 0123"
STRIPE_HOST = re.compile(r"(^|\.)(stripe\.com|stripe\.network|stripecdn\.com)$")
TIP_LINK = re.compile(r"https://buy\.stripe\.com/[A-Za-z0-9_]+")
REPORTING_HEADERS = ("nel", "report-to", "reporting-endpoints")
# Chromium's console error for a Permissions-Policy feature it doesn't know, such as a retired
# one (design-m2 §4: the header must drop those).
POLICY_MESSAGE = re.compile(r"permissions[- ]policy|feature[- ]policy|unrecognized feature", re.I)
PREFETCH_RELS = checks.PREFETCH_RELS
# <link> relations that make the browser fetch something.
LOADING_RELS = frozenset(
    {"stylesheet", "icon", "apple-touch-icon", "preload", "modulepreload", "manifest", "mask-icon"}
)
# A JS expression: every specimen box in the list has its mask.
SPECS_SET = (
    "Array.from(document.querySelectorAll('#list span.spec[data-src]'))"
    ".every((span) => span.dataset.state === 'set')"
)

# Counts the list's 'change' events (site/CONTRACT.md section 10), so each step can wait for
# the page to react to it. Runs before any page script, like guards.js.
CHANGES_JS = """(() => {
  window.__tffPrivacyChanges = 0;
  document.addEventListener('tff:list-ready', (event) => {
    event.detail.list.on('change', () => { window.__tffPrivacyChanges += 1; });
  });
})();"""
CONTROLS_JS = """() => Array.from(
  document.querySelectorAll('#filters input[type=checkbox], #filters input[type=radio]'),
  (el) => ({ id: el.id, type: el.type, name: el.name, checked: el.checked }))"""
INDEX_JS = """async () => {
  const index = await globalThis.tff.list.index();
  return { n: index.n, ids: Array.from(index.ids), bits: Array.from(index.bits),
           ranks: Object.keys(index.r) };
}"""
DOM_JS = """() => ({
  links: Array.from(document.querySelectorAll('link'),
    (l) => ({ rel: (l.getAttribute('rel') || '').toLowerCase(), href: l.href })),
  scripts: Array.from(document.querySelectorAll('script'),
    (s) => ({ src: s.src, type: (s.getAttribute('type') || '').toLowerCase() })),
})"""
RECORDS_JS = "() => window.__tffGuards ? JSON.parse(JSON.stringify(window.__tffGuards)) : null"
# What is left in the browser however it was written (``localStorage.x = 1`` bypasses the
# guards.js wrappers, and storage_state() has no sessionStorage). Reading these stores nothing.
STORED_JS = """async () => {
  const out = {};
  const count = async (name, fn) => {
    try { const n = await fn(); if (n) out[name] = n; } catch (error) { /* not available */ }
  };
  await count('localStorage', () => localStorage.length);
  await count('sessionStorage', () => sessionStorage.length);
  await count('caches', async () => (await caches.keys()).length);
  await count('serviceWorkers',
    async () => (await navigator.serviceWorker.getRegistrations()).length);
  return out;
}"""
SETTLE_JS = """() => new Promise((resolve) =>
  requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 30))))"""
AT_BOTTOM_JS = "() => innerHeight + scrollY >= document.documentElement.scrollHeight - 1"
TYPE_OWN_BIT = 64  # list index bits (site/CONTRACT.md section 7)
# The filter controls' names, which are the hash keys (site/CONTRACT.md sections 4 and 9).
FILTER_NAMES = frozenset({"cat", "spacing", "var", "hide", "lic", "redist", "sort"})


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def expected_csp() -> str:
    """The CSP in ops/caddy/site.caddy, which every response must carry."""
    headers = serve.parse_headers(serve.SITE_CADDY.read_text(encoding="utf-8"))
    return headers.security["Content-Security-Policy"]


def donations_tip_url() -> str:
    """The Stripe link in ops/DONATIONS.md's Facts table."""
    match = TIP_LINK.search(DONATIONS.read_text(encoding="utf-8"))
    assert match, f"no https://buy.stripe.com/ link in {DONATIONS}"
    return match.group(0)


def page_paths(sitemap_xml: bytes) -> list[str]:
    """The pages to visit: ``/`` first, the sitemap's other pages, then a missing page."""
    paths = ["/"] + [p for p in checks.sitemap_paths(sitemap_xml) if p != "/"]
    return [*dict.fromkeys(paths), NOT_FOUND_PATH]


def wait_until(page: Any, expression: str, what: str, timeout_s: float = 10.0) -> None:
    """Poll a JS expression until it is truthy. (``wait_for_function`` would compile the
    expression in the page, which the site's CSP reports as eval.)"""
    deadline = time.monotonic() + timeout_s
    while not page.evaluate(f"() => Boolean({expression})"):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        page.wait_for_timeout(20)


@dataclass
class Sweep:
    """Drive the site the way visitors do and collect everything that would break privacy.

    ``guarded`` is a ``Guarded`` from tests/site/conftest.py, made with a ``WIDE`` viewport.
    ``csp`` is the CSP every same-origin response must carry (None skips that check).
    ``scripts`` is False for a context with JavaScript off, which only visits and scrolls.
    """

    guarded: Any
    csp: str | None
    scripts: bool = True
    max_details: int = 60
    max_type_own: int = 5
    pages: list[tuple[str, Any]] = field(default_factory=list)
    requests: list[Any] = field(default_factory=list)
    done: Counter[str] = field(default_factory=Counter)
    specimen_sources: set[str] = field(default_factory=set)
    font_sources: set[str] = field(default_factory=set)
    control_names: set[str] = field(default_factory=set)
    websockets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.origin = self.guarded.origin
        context = self.guarded.context
        context.add_init_script(CHANGES_JS)
        # Plain functions: Playwright tags each handler with an attribute. Routes don't see
        # WebSockets, so each page reports its own.
        context.on("request", lambda request: self.requests.append(request))
        context.on(
            "page", lambda page: page.on("websocket", lambda ws: self.websockets.append(ws.url))
        )

    # --- driving -------------------------------------------------------------------------

    def run(self, paths: list[str]) -> None:
        """Visit ``paths``, each in its own tab, doing everything a visitor can on each."""
        for path in paths:
            page = self.visit(path)
            if page.locator("#list").count():
                if self.scripts:
                    self.exercise_list(page)
                self.scroll_specimens(page)
            else:
                self.scroll_to_bottom(page)
        for _, page in self.pages:
            page.wait_for_load_state("networkidle")

    def visit(self, path: str) -> Any:
        """Open ``path`` in a new tab and wait for its load event."""
        page = self.guarded.new_page()
        page.goto(path)
        page.wait_for_load_state("load")
        self.pages.append((path, page))
        self.done["pages"] += 1
        return page

    def open_list(self, path: str = "/") -> Any:
        """Open the list page and wait for its script to start."""
        page = self.visit(path)
        wait_until(page, "globalThis.tff !== undefined", "the list's script to start")
        return page

    def exercise_list(self, page: Any) -> None:
        """Every rank, and under each every filter control; then search, clear, the phone
        button, details, "Type your own text" and a ``#font=`` link."""
        wait_until(page, "globalThis.tff !== undefined", "the list's script to start")
        index = page.evaluate(INDEX_JS)
        ranks = page.evaluate(
            "() => Array.from(document.querySelectorAll('#f-rank option'), (o) => o.value)"
        )
        assert sorted(ranks) == sorted(index["ranks"]), "#f-rank doesn't offer every rank"
        controls = page.evaluate(CONTROLS_JS)
        defaults = {c["name"]: c["id"] for c in controls if c["type"] == "radio" and c["checked"]}
        for rank in ranks:
            if page.input_value("#f-rank") != rank:
                self._change(page, partial(page.select_option, "#f-rank", rank))
            self.done["ranks"] += 1
            for control in controls:
                self._toggle(page, control, defaults)
                self.control_names.add(control["name"])
        if page.input_value("#f-rank") != ranks[0]:
            self._change(page, partial(page.select_option, "#f-rank", ranks[0]))
        self._search(page)
        self._clear(page)
        self._phone(page)
        shown = page.evaluate("() => document.querySelectorAll('#list > li.font').length")
        assert shown == index["n"], "the default view doesn't show every font"
        self._details(page, index)
        self._type_own(page, index)
        if index["ids"]:
            link = self.visit(f"/#font={index['ids'][0]}")
            link.wait_for_selector(
                f"#details-{index['ids'][0]}[data-state='ready']", state="attached"
            )
            self.done["font links"] += 1

    def scroll_specimens(self, page: Any) -> None:
        """Scroll the whole list into view and wait until every specimen has been requested."""
        page.keyboard.press("Escape")
        sources = page.evaluate(
            "() => Array.from(document.querySelectorAll('#list span.spec[data-src]'),"
            " (span) => span.dataset.src)"
        )
        if not self.scripts:
            sources = page.evaluate(
                "() => Array.from(document.querySelectorAll('#list img.spec-img'),"
                " (img) => img.getAttribute('src'))"
            )
        self.scroll_to_bottom(page)
        if self.scripts and sources:
            wait_until(page, SPECS_SET, "every specimen's mask")
        wanted = set(sources)
        deadline = time.monotonic() + 15
        while missing := wanted - self._requested_paths():
            if time.monotonic() > deadline:
                raise AssertionError(f"specimens never requested: {sorted(missing)[:10]}")
            page.wait_for_timeout(50)
        self.specimen_sources |= wanted
        self.done["specimens"] = len(self.specimen_sources)

    def scroll_to_bottom(self, page: Any) -> None:
        """Scroll from the top to the bottom, a screen at a time, letting each screen render."""
        page.evaluate("() => window.scrollTo(0, 0)")
        for _ in range(5000):
            if self.scripts:
                page.evaluate(SETTLE_JS)
            else:  # no animation-frame callbacks run while scripting is off
                page.wait_for_timeout(50)
            if page.evaluate(AT_BOTTOM_JS):
                break
            page.evaluate("() => window.scrollBy(0, Math.max(100, innerHeight - 100))")
            self.done["screens"] += 1

    def click_tip(self, page: Any) -> Any:
        """Click the tip link and return the request it makes (aborted: it leaves the site)."""
        with page.expect_request(lambda request: _is_stripe(request.url)) as caught:
            page.click("#tip a")
        return caught.value

    # --- steps ---------------------------------------------------------------------------

    def _change(self, page: Any, action: Callable[[], Any]) -> None:
        """Do ``action`` and wait for the list's 'change' event."""
        before = page.evaluate("() => window.__tffPrivacyChanges")
        action()
        wait_until(page, f"window.__tffPrivacyChanges > {before}", "the list to change")
        self.done["changes"] += 1

    def _toggle(self, page: Any, control: dict[str, Any], defaults: dict[str, str]) -> None:
        selector = f"#{control['id']}"
        if control["type"] == "checkbox":
            self._change(page, partial(page.check, selector))
            self._change(page, partial(page.uncheck, selector))
        elif control["id"] != defaults.get(control["name"]):
            self._change(page, partial(page.check, selector))
            self._change(page, partial(page.check, f"#{defaults[control['name']]}"))
        self.done["controls"] += 1

    def _search(self, page: Any) -> None:
        name = page.text_content("#list > li.font .font-name") or "sans"
        self._change(page, partial(page.fill, "#f-q", name.split()[0].lower()))
        self._change(page, partial(page.fill, "#f-q", SEARCH_NOTHING))
        wait_until(page, "!document.getElementById('no-results').hidden", "the no-results note")
        self._change(page, partial(page.click, "#no-results-clear"))
        if page.input_value("#f-q"):
            self._change(page, partial(page.fill, "#f-q", ""))
        self.done["searches"] += 2

    def _clear(self, page: Any) -> None:
        self._change(page, partial(page.check, "#f-var"))
        self._change(page, partial(page.click, "#f-clear"))
        if not page.is_checked("#f-sort-rank"):
            self._change(page, partial(page.check, "#f-sort-rank"))

    def _phone(self, page: Any) -> None:
        page.set_viewport_size(PHONE)
        page.evaluate(SETTLE_JS)
        if page.is_visible("#f-toggle"):
            page.click("#f-toggle")
            self._change(page, partial(page.check, "#f-var"))
            self._change(page, partial(page.click, "#f-clear"))
            page.click("#f-toggle")
            self.done["phone filters"] += 1
        page.set_viewport_size(WIDE)
        page.evaluate(SETTLE_JS)

    def _details(self, page: Any, index: dict[str, Any]) -> None:
        ids = index["ids"]
        step = max(1, math.ceil(len(ids) / self.max_details))
        for font_id in ids[::step]:
            page.click(f"#font-{font_id} .details-toggle")
            page.wait_for_selector(f"#details-{font_id}[data-state='ready']", state="attached")
            self.done["details"] += 1
        page.keyboard.press("Escape")

    def _type_own(self, page: Any, index: dict[str, Any]) -> None:
        own = [
            i for i, bits in zip(index["ids"], index["bits"], strict=True) if bits & TYPE_OWN_BIT
        ]
        self.done["type-own fonts"] = len(own)
        for font_id in own[: self.max_type_own]:
            panel = f"#details-{font_id}"
            if page.get_attribute(panel, "hidden") is not None:
                page.click(f"#font-{font_id} .details-toggle")
            page.wait_for_selector(f"{panel}[data-state='ready']", state="attached")
            url = page.get_attribute(f"{panel} button.typeown-load", "data-url") or ""
            page.click(f"{panel} button.typeown-load")
            page.wait_for_selector(f"{panel} input.typeown-input")
            page.fill(f"{panel} input.typeown-input", TYPE_TEXT)
            path = urlsplit(url).path
            assert path.startswith("/assets/fonts/"), f"{font_id}: font file at {url!r}"
            assert path in self._requested_paths(), f"{font_id}: {path} was never requested"
            self.font_sources.add(path)
            self.done["type-own loaded"] += 1
        page.keyboard.press("Escape")

    # --- what was seen --------------------------------------------------------------------

    def _requested_paths(self) -> set[str]:
        return {urlsplit(r.url).path for r in self.requests if _origin(r.url) == self.origin}

    def stripe_requests(self) -> list[str]:
        return [r.url for r in self.requests if _is_stripe(r.url)]

    def findings(self) -> dict[str, list[Any]]:
        """Everything the sweep saw that breaks the privacy rules, by kind; empty when clean."""
        found: dict[str, list[Any]] = {}

        def add(kind: str, items: list[Any]) -> None:
            if items:
                found[kind] = items

        g = self.guarded
        web = [r for r in self.requests if urlsplit(r.url).scheme in ("http", "https", "ws", "wss")]
        foreign = [r.url for r in web if _origin(r.url) != self.origin]
        add("requests to another origin", sorted(set(foreign) | set(g.blocked)))
        add(
            "requests to /cdn-cgi/",
            [r.url for r in web if urlsplit(r.url).path.startswith("/cdn-cgi/")],
        )
        add("requests to Stripe", self.stripe_requests())
        add("WebSockets", list(self.websockets))
        add("responses that set a cookie", g.cookie_responses())
        add("cookies in the browser", g.context.cookies())
        state = g.context.storage_state(indexed_db=True)
        add("browser storage", [] if state == {"cookies": [], "origins": []} else [state])
        add("guard records", self._records())
        add("data left in the browser", self._stored())
        add("CSP messages in the console", g.csp_messages())
        add(
            "Permissions-Policy errors in the console",
            [text for _, text in g.console if POLICY_MESSAGE.search(text)],
        )
        add("page errors", list(g.errors))
        dom, prefetch = self._dom()
        add("scripts or links from elsewhere, or inline scripts", dom)
        prefetch += self._prefetch_requests()
        headers, injected = self._responses()
        add("prefetch, prerender or preconnect", prefetch + headers.pop("prefetch", []))
        add("reporting headers", headers.pop("reporting", []))
        add("responses without the site's CSP", headers.pop("csp", []))
        add("Cloudflare code in the HTML", injected)
        return found

    def _records(self) -> list[Any]:
        seen = []
        for path, page in self.pages:
            if page.is_closed():
                continue
            records = page.evaluate(RECORDS_JS)
            if records is None:
                if self.scripts:
                    seen.append((path, "guards.js did not run"))
                continue
            if any(records.values()):
                seen.append((path, {k: v for k, v in records.items() if v}))
        return seen

    def _stored(self) -> list[Any]:
        # With scripting off only a Set-Cookie can store anything (checked apart), and Firefox
        # never settles an async evaluate in such a page.
        if not self.scripts:
            return []
        seen = []
        for path, page in self.pages:
            if not page.is_closed() and (stored := page.evaluate(STORED_JS)):
                seen.append((path, stored))
        return seen

    def _dom(self) -> tuple[list[Any], list[Any]]:
        others: list[Any] = []
        prefetch: list[Any] = []
        for path, page in self.pages:
            if page.is_closed():
                continue
            dom = page.evaluate(DOM_JS)
            for link in dom["links"]:
                rels = set(link["rel"].split())
                if rels & PREFETCH_RELS:
                    prefetch.append((path, link))
                if rels & LOADING_RELS and _origin(link["href"]) != self.origin:
                    others.append((path, link))
            for script in dom["scripts"]:
                if script["type"] == "speculationrules":
                    prefetch.append((path, script))
                elif not script["src"] or _origin(script["src"]) != self.origin:
                    others.append((path, script))
        return others, prefetch

    def _prefetch_requests(self) -> list[str]:
        marked = []
        for request in self.requests:
            if _origin(request.url) != self.origin:
                continue  # already a finding
            try:
                headers = request.all_headers()
            except Exception:  # the request's page is gone; its URL was still checked
                continue
            purpose = f"{headers.get('sec-purpose', '')} {headers.get('purpose', '')}".lower()
            if "prefetch" in purpose or "prerender" in purpose:
                marked.append(request.url)
        return marked

    def _responses(self) -> tuple[dict[str, list[str]], list[str]]:
        bad: dict[str, list[str]] = {}
        injected: list[str] = []
        for response in self.guarded.responses:
            if _origin(response.url) != self.origin:
                continue
            where = f"{urlsplit(response.url).path} ({response.status})"
            headers = response.all_headers()
            if self.csp is not None and headers.get("content-security-policy") != self.csp:
                bad.setdefault("csp", []).append(where)
            for name in REPORTING_HEADERS:
                if name in headers:
                    bad.setdefault("reporting", []).append(f"{where}: {name}")
            link = headers.get("link", "").lower()
            if "speculation-rules" in headers or checks.PREFETCH_LINK_HEADER.search(link):
                bad.setdefault("prefetch", []).append(f"{where}: {link or 'speculation-rules'}")
            if response.request.resource_type == "document":
                try:
                    text = response.text()
                except Exception:  # a redirect or a body the browser didn't keep
                    self.notes.append(f"no body for {where}")
                    continue
                injected += [f"{where}: {m.group(0)}" for m in checks.BEACON.finditer(text)]
        return bad, injected


def _is_stripe(url: str) -> bool:
    return bool(STRIPE_HOST.search(urlsplit(url).hostname or ""))


# --- this module's site -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def tip_url() -> str:
    from tff_site import build

    return build.TIP_URL or donations_tip_url()


def standin_font() -> bytes:
    """A tiny valid TrueType font (every printable ASCII character is a box), made the same way
    every time, for a ``font_file`` that isn't in the font cache."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    builder = FontBuilder(1000, isTTF=True)
    builder.setupHead(unitsPerEm=1000, created=0, modified=0)
    builder.setupGlyphOrder([".notdef", "box"])
    builder.setupCharacterMap(dict.fromkeys(range(0x21, 0x7F), "box"))
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((100, 700))
    pen.lineTo((500, 700))
    pen.lineTo((500, 0))
    pen.closePath()
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "box": pen.glyph()})
    builder.setupHorizontalMetrics({".notdef": (600, 0), "box": (600, 100)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "TFF Stand-in", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    out = io.BytesIO()
    builder.save(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def privacy_data(site_data: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of ``site_data`` in which every ``preview`` has an SVG whose hash matches, and
    every ``font_file`` is in ``fonts/`` next to it: the cached file, or ``standin_font()``."""
    from tff_site import fonts

    standins = sorted(STANDINS.glob("*.svg"))
    assert standins, f"no stand-in specimens in {STANDINS}"
    doc = json.loads(site_data.read_bytes())
    dest = tmp_path_factory.mktemp("privacy-data")
    font_dir = dest / "fonts"
    font_dir.mkdir()
    for n, font in enumerate(doc["fonts"]):
        preview = font.get("preview")
        if preview:
            own = site_data.parent / preview["path"]
            blob = own.read_bytes() if own.is_file() else standins[n % len(standins)].read_bytes()
            target = dest / preview["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            preview["sha256"] = hashlib.sha256(blob).hexdigest()
        font_file = font.get("font_file")
        if font_file:
            cached = fonts.cache_path(font_file["sha256"])
            blob = cached.read_bytes() if cached.is_file() else standin_font()
            font_file["sha256"] = hashlib.sha256(blob).hexdigest()
            font_file["size"] = len(blob)
            fonts.cache_path(font_file["sha256"], font_dir).write_bytes(blob)
    path = dest / "catalog-site.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def site_dir(privacy_data: Path, tip_url: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """This module's site (overrides the session fixture): specimens, fonts and the tip link."""
    from tff_site import build

    out = tmp_path_factory.mktemp("privacy-site")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build, "TIP_URL", tip_url)
        build.build(
            privacy_data,
            out,
            fonts_dir=privacy_data.parent / "fonts",
            commit=FAKE_COMMIT,
            allow_dirty=True,
        )
    return out


@pytest.fixture(scope="module")
def site_url(site_dir: Path) -> Iterator[str]:
    """This module's site, served by ``tff-site serve`` with site.caddy's headers."""
    server = serve.make_server(site_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, name="privacy-site", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def paths(site_dir: Path) -> list[str]:
    return page_paths((site_dir / "sitemap.xml").read_bytes())


# --- the built files --------------------------------------------------------------------------


def _html_files(site_dir: Path) -> list[Path]:
    return sorted(site_dir.rglob("*.html"))


def test_built_pages_point_only_at_this_site(site_dir: Path) -> None:
    """Everything a page makes the browser fetch is a path on this site; nothing prefetches."""
    loading = {
        ("script", "src"),
        ("img", "src"),
        ("img", "srcset"),
        ("source", "src"),
        ("source", "srcset"),
        ("iframe", "src"),
        ("video", "src"),
        ("video", "poster"),
        ("audio", "src"),
        ("embed", "src"),
        ("object", "data"),
        ("track", "src"),
    }
    problems = []
    pages = _html_files(site_dir)
    assert {p.name for p in pages} >= {"index.html", "404.html"}
    for html in pages:
        where = html.relative_to(site_dir).as_posix()
        for tag, attrs in checks.tags(html.read_bytes()):
            rels = set(attrs.get("rel", "").lower().split())
            urls = [attrs[a] for t, a in loading if t == tag and a in attrs]
            if tag == "link" and rels & LOADING_RELS:
                urls.append(attrs.get("href", ""))
            if tag == "link" and rels & PREFETCH_RELS:
                problems.append(f"{where}: prefetch-like link {attrs}")
            if tag == "script" and not checks.SCRIPT_SRC.match(attrs.get("src", "")):
                problems.append(f"{where}: script {attrs}")
            if tag == "meta" and attrs.get("http-equiv", "").lower() == "refresh":
                problems.append(f"{where}: meta refresh {attrs}")
            for url in urls:
                for part in url.split(","):
                    target = part.strip().split(" ")[0]
                    if not target.startswith("/") or target.startswith("//"):
                        problems.append(f"{where}: <{tag}> loads {target!r}")
        text = html.read_text(encoding="utf-8")
        problems += [f"{where}: contains {m.group(0)!r}" for m in checks.BEACON.finditer(text)]
    css = "".join(p.read_text(encoding="utf-8") for p in site_dir.glob("assets/style.*.css"))
    for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", css):
        if not url.startswith(("/", "#", "data:")):
            problems.append(f"style.css: url({url})")
    assert "@import" not in css
    assert problems == []


def test_the_script_names_no_storage_api(site_dir: Path) -> None:
    """site/CONTRACT.md section 5: nothing is stored. Milestone 3 (M3-D13) turns this into an
    allowlist if it ever stores anything."""
    scripts = list(site_dir.glob("assets/app.*.js"))
    assert len(scripts) == 1
    source = scripts[0].read_text(encoding="utf-8")
    storage = re.compile(
        r"\b(localStorage|sessionStorage|indexedDB|cookieStore|serviceWorker|openDatabase"
        r"|getDirectory|sendBeacon|WebSocket|WebTransport|EventSource|RTCPeerConnection)\b"
        r"|document\s*\.\s*cookie|\bcaches\s*\.|\bnavigator\s*\.\s*storage\b"
    )
    assert [m.group(0) for m in storage.finditer(source)] == []


def test_the_tip_link_is_one_plain_link_to_stripe(site_dir: Path, tip_url: str) -> None:
    """ops/DONATIONS.md step 11: one plain link, no Stripe script, in the footer of every page.
    A plain link: no ``ping`` (a request on click), no ``target`` or ``download``; ``rel`` and
    ``referrerpolicy`` may only take things away."""
    plain = {"href", "class", "rel", "referrerpolicy"}
    for html in _html_files(site_dir):
        found = checks.tags(html.read_bytes())
        links = [a for t, a in found if t == "a" and "stripe" in a.get("href", "")]
        assert [a["href"] for a in links] == [tip_url], html.name
        assert set(links[0]) <= plain, f"{html.name}: the tip link isn't plain: {links[0]}"
        assert not [a for t, a in found if "stripe" in " ".join(a.values()) and t != "a"]


def test_the_sweep_visits_every_built_page(site_dir: Path, paths: list[str]) -> None:
    """Every HTML file the build writes is one of the sweep's pages (the 404 page through a
    missing path), so a page left out of the sitemap is still swept."""
    built = {
        "/" + p.relative_to(site_dir).as_posix().removesuffix("index.html")
        for p in _html_files(site_dir)
        if p.name != "404.html"
    }
    assert built <= set(paths), f"pages the sweep never opens: {sorted(built - set(paths))}"
    assert paths[-1] == NOT_FOUND_PATH
    assert (site_dir / "404.html").is_file()


# --- the browser sweep ------------------------------------------------------------------------


def test_every_page_rank_filter_and_panel_stays_on_this_site(
    guarded_context: Any, paths: list[str], privacy_data: Path
) -> None:
    fonts = json.loads(privacy_data.read_bytes())["fonts"]
    own = sum(1 for f in fonts if f["preview_ok"] and f.get("font_file"))
    sweep = Sweep(guarded_context(viewport=WIDE), csp=expected_csp())
    sweep.run(paths)
    assert sweep.findings() == {}
    done = sweep.done
    assert done["pages"] >= len(paths) + 1  # and the #font= link
    assert done["ranks"] >= 2
    assert sweep.control_names >= FILTER_NAMES, "a filter the sweep never set"
    assert done["controls"] >= 10 * done["ranks"]
    assert done["details"] >= 1
    assert done["font links"] == 1
    assert done["searches"] == 2
    assert done["phone filters"] == 1, "the phone Filters button never showed"
    assert done["specimens"] >= 1, "no specimen was requested: nothing was scrolled into view"
    assert done["type-own fonts"] == own, "fonts offering Type your own text"
    assert done["type-own loaded"] == min(done["type-own fonts"], sweep.max_type_own)
    print(f"privacy sweep: {dict(sorted(done.items()))}")


def test_the_tip_link_is_the_only_way_to_stripe(guarded_context: Any, tip_url: str) -> None:
    guarded = guarded_context(viewport=WIDE)
    sweep = Sweep(guarded, csp=expected_csp())
    page = sweep.open_list()
    sweep.scroll_specimens(page)
    page.hover("#tip a")
    page.wait_for_load_state("networkidle")
    assert sweep.stripe_requests() == [], "Stripe was contacted before a click"
    assert sweep.findings() == {}
    request = sweep.click_tip(page)
    assert request.url == tip_url
    assert request.is_navigation_request()
    assert request.resource_type == "document"
    assert guarded.blocked == [tip_url], "the click made more than the one navigation"


def test_without_javascript_the_pages_stay_on_this_site(
    guarded_context: Any, paths: list[str]
) -> None:
    sweep = Sweep(
        guarded_context(viewport=PHONE, java_script_enabled=False),
        csp=expected_csp(),
        scripts=False,
    )
    sweep.run(paths)
    assert sweep.findings() == {}
    assert sweep.done["specimens"] >= 1


def test_the_build_caddy_serves_stays_on_this_site(browser: Any) -> None:
    """In CI (``TFF_CADDY=1``): the sweep and the live checks through real Caddy
    (ops/caddy/ci.Caddyfile on 127.0.0.1:8080), on the build CI made (``TFF_SITE_DIR``), so
    Caddy's own handling (the 404 route, deferred headers, compression) is covered too."""
    from tests.site.conftest import CI_CADDY_URL, Guarded

    site = os.environ.get("TFF_SITE_DIR")
    if os.environ.get("TFF_CADDY") != "1" or not site:
        pytest.skip("needs Caddy serving TFF_SITE_DIR on 127.0.0.1:8080 (TFF_CADDY=1), as in CI")
    expected = serve.parse_headers(serve.SITE_CADDY.read_text(encoding="utf-8"))
    with checks.client(CI_CADDY_URL) as http:
        fetched, problems = checks.crawl(http)
    by_path = {f.path: f for f in fetched}
    problems += checks.header_problems(fetched, expected, staging=False)
    problems += checks.html_problems(fetched)
    problems += checks.robots_problems(
        by_path["/robots.txt"], Path(site, "robots.txt").read_bytes()
    )
    built_version = Path(site, "version.txt").read_text(encoding="utf-8")
    commit = checks.parse_version(built_version)["commit"]
    served_version = by_path["/version.txt"].body.decode("utf-8")
    problems += checks.version_problems(served_version, expect_commit=commit)
    assert problems == []

    context = browser.new_context(base_url=CI_CADDY_URL, viewport=WIDE)
    try:
        guarded = Guarded(context=context, origin=CI_CADDY_URL)
        guarded.attach()
        sweep = Sweep(guarded, csp=expected_csp())
        sweep.run(page_paths(by_path["/sitemap.xml"].body))
        found = sweep.findings()
    finally:
        context.close()
    assert found == {}
    assert sweep.done["ranks"] >= 2
    assert sweep.control_names >= FILTER_NAMES
    assert sweep.done["type-own loaded"] == min(sweep.done["type-own fonts"], sweep.max_type_own)


BEACON_PAGE = (
    b"<!doctype html><title>Beacon</title><p>Injected</p>"
    b"<script>document.title = 'inline';</script>"
    b'<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
    b'data-cf-beacon=\'{"token": "probe"}\'></script>'
)


class _Misbehaving(BaseHTTPRequestHandler):
    """A server that breaks the rules on purpose: ``/`` is a page with the site's CSP;
    ``/beacon`` is one with Cloudflare's beacon injected, an inline script and a
    Permissions-Policy feature no browser knows; anything else sets a cookie, asks for NEL
    reports, links a prefetch and has no CSP."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path in ("/", "/beacon"):
            body = b"<!doctype html><title>Probe</title><p>Probe</p>"
            if self.path == "/beacon":
                body = BEACON_PAGE
            headers = {
                "Content-Type": "text/html; charset=utf-8",
                "Content-Security-Policy": expected_csp(),
            }
            if self.path == "/beacon":
                headers["Permissions-Policy"] = "tff-probe=()"
        else:
            body = b"planted"
            headers = {
                "Content-Type": "text/plain",
                "Set-Cookie": "tff-probe=1; Path=/",
                "NEL": '{"report_to":"x","max_age":1}',
                "Link": "</robots.txt>; rel=prefetch",
            }
        self.send_response(200)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def misbehaving_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Misbehaving)
    thread = threading.Thread(target=server.serve_forever, name="misbehaving", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_sweep_catches_what_it_is_for(
    browser: Any, browser_name: str, misbehaving_url: str
) -> None:
    """Plant each kind of problem and check the sweep reports it, so a clean sweep means
    something. (A real server sends the cookie: Playwright hides ``Set-Cookie`` on responses
    a route fulfils.)"""
    from tests.site.conftest import Guarded

    context = browser.new_context(base_url=misbehaving_url, viewport=WIDE)
    try:
        guarded = Guarded(context=context, origin=misbehaving_url)
        guarded.attach()
        sweep = Sweep(guarded, csp=expected_csp())
        page = sweep.visit("/")
        page.evaluate(
            """async () => {
              localStorage.setItem('tff-probe', '1');
              sessionStorage.tffProbe = '1';  // a property write: the wrappers never see it
              document.cookie = 'tff-probe=2; path=/';
              const link = document.createElement('link');
              link.rel = 'prefetch';
              link.href = '/robots.txt';
              document.head.append(link);
              const img = document.createElement('img');
              img.src = 'https://example.com/pixel.png';
              document.body.append(img);
              await fetch('/planted');
              await fetch('/cdn-cgi/rum');
              setTimeout(() => { throw new Error('tff-probe'); }, 0);
            }"""
        )
        sweep.visit("/beacon")
        other = guarded.new_page()
        with pytest.raises(Exception, match=r"ERR_FAILED|NS_ERROR|net::"):
            other.goto("https://buy.stripe.com/test_probe")
        page.wait_for_timeout(200)
        found = sweep.findings()
    finally:
        context.close()
    assert set(found) >= {
        "requests to another origin",
        "requests to /cdn-cgi/",
        "requests to Stripe",
        "responses that set a cookie",
        "cookies in the browser",
        "browser storage",
        "guard records",
        "data left in the browser",
        "page errors",
        "scripts or links from elsewhere, or inline scripts",
        "prefetch, prerender or preconnect",
        "reporting headers",
        "responses without the site's CSP",
        "Cloudflare code in the HTML",
    }
    assert "CSP messages in the console" in found
    if browser_name == "chromium":  # Firefox ignores Permissions-Policy
        assert "tff-probe" in " ".join(found["Permissions-Policy errors in the console"])
    records = dict(found["guard records"])
    assert {"storage", "cookies", "violations"} <= set(records["/"])
    assert dict(found["data left in the browser"])["/"] == {
        "localStorage": 1,
        "sessionStorage": 1,
    }
    assert f"{misbehaving_url}/planted" in found["responses that set a cookie"]
    assert "/planted (200)" in found["responses without the site's CSP"]
    assert "/planted (200): nel" in found["reporting headers"]
    blocked = [v["blocked"] for v in records["/beacon"]["violations"]]
    assert "inline" in blocked
    # Chromium reports the blocked script as a request too; Firefox only as a violation.
    beacon = "https://static.cloudflareinsights.com/beacon.min.js"
    assert beacon in blocked
    injected = "\n".join(found["Cloudflare code in the HTML"])
    assert "/beacon (200): cloudflareinsights" in injected
    assert "/beacon (200): data-cf-beacon" in injected
    assert [s for _, s in found["scripts or links from elsewhere, or inline scripts"]] == [
        {"src": "", "type": ""},
        {"src": "https://static.cloudflareinsights.com/beacon.min.js", "type": ""},
    ]


# --- the live checks, offline -----------------------------------------------------------------


def test_the_live_checks_pass_against_this_build(site_url: str, site_dir: Path) -> None:
    """``tests/live/checks.py`` against this build served locally: what the live tests assert
    after each deploy holds for the build itself."""
    expected = serve.parse_headers(serve.SITE_CADDY.read_text(encoding="utf-8"))
    with checks.client(site_url) as http:
        fetched, problems = checks.crawl(http)
        by_path = {f.path: f for f in fetched}
        index = json.loads(next(f.body for f in fetched if "/assets/list." in f.path))
        assert checks.cloudflare_location(http) is None
    assert problems == []
    assert {f.kind for f in fetched} >= checks.CSP_KINDS | {"css", "txt", "xml"}
    assert checks.header_problems(fetched, expected, staging=False) == []
    assert checks.html_problems(fetched) == []
    built_robots = (site_dir / "robots.txt").read_bytes()
    assert checks.robots_problems(by_path["/robots.txt"], built_robots) == []
    version = by_path["/version.txt"].body.decode()
    assert checks.version_problems(version, expect_commit=FAKE_COMMIT, index=index) == []


def test_the_live_checks_catch_what_they_are_for() -> None:
    """A made-up site with every problem the live checks look for."""
    expected = serve.parse_headers(serve.SITE_CADDY.read_text(encoding="utf-8"))
    good = {**expected.security, "Cache-Control": expected.revalidate["Cache-Control"]}
    beacon = (
        '<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
        "data-cf-beacon='{}'></script>"
    )
    home = (
        '<!doctype html><link rel="stylesheet" href="/assets/style.0123456789.css">'
        '<link rel="preconnect" href="https://example.com">'
        '<script type="module" src="/assets/app.0123456789.js"></script>'
        f'<ol id="list" data-index="/assets/list.0123456789.json" data-details="https://x.test/d.json">'
        f"</ol>{beacon}"
    )
    sitemap = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://trulyfreefonts.com/</loc></url></urlset>"
    )
    version = "commit=" + "1" * 40 + "\nrun_date=2026-09-25\n"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        headers = dict(good)
        if path.startswith("/assets/"):
            headers["Cache-Control"] = expected.immutable["Cache-Control"]
        body: str = "x"
        status = 200
        if path == "/":
            body = home
            headers["Set-Cookie"] = "__cf_bm=1"
            headers["X-Robots-Tag"] = "noindex"
        elif path == "/sitemap.xml":
            body = sitemap
        elif path == "/version.txt":
            body = version
        elif path == "/robots.txt":
            body = "User-agent: *\nAllow: /\n# Content-Signal: search=yes\n"
        elif path.startswith("/assets/app."):
            del headers["Content-Security-Policy"]
            headers["NEL"] = "{}"
        elif path == "/favicon.svg":
            headers["Link"] = "</x>; rel=preconnect"
        elif path == checks.NOT_FOUND_PATH:
            status = 404
            body = "<!doctype html><script>/* inline */</script>"
        return httpx.Response(status, headers=headers, text=body)

    transport = httpx.MockTransport(handler)
    with checks.client("https://example.test", transport=transport) as http:
        fetched, problems = checks.crawl(http)
    assert problems == [
        "/ names 'https://static.cloudflareinsights.com/beacon.min.js' (js), which is not a path "
        "on this site",
        "/ names 'https://x.test/d.json' (json), which is not a path on this site",
    ]
    headers = "\n".join(checks.header_problems(fetched, expected, staging=False))
    assert "/ (200): sends set-cookie" in headers
    assert "X-Robots-Tag 'noindex' outside the test site" in headers
    assert "Content-Security-Policy is None" in headers
    assert "sends nel" in headers
    assert "a Link header prefetches or preconnects" in headers
    staging = "\n".join(checks.header_problems(fetched, expected, staging=True))
    assert "/sitemap.xml (200): the test site sends no X-Robots-Tag: noindex" in staging
    html = "\n".join(checks.html_problems(fetched))
    assert "contains 'cloudflareinsights'" in html
    assert "contains 'data-cf-beacon'" in html
    assert "a link that prefetches or preconnects" in html
    assert f"{checks.NOT_FOUND_PATH} (404): a script that isn't the site's" in html
    robots = next(f for f in fetched if f.path == "/robots.txt")
    assert checks.robots_problems(robots, b"User-agent: *\nAllow: /\n")[0].startswith(
        "/robots.txt differs from the built file"
    )
    index = {"commit": "2" * 40, "run_date": "2026-09-25"}
    wrong = checks.version_problems(version, expect_commit="3" * 40, index=index)
    assert len(wrong) == 3  # missing fields, another commit, and the index disagrees
    challenge = httpx.MockTransport(
        lambda request: httpx.Response(403, headers={"cf-mitigated": "challenge"}, text="x")
    )
    with (
        checks.client("https://example.test", transport=challenge) as http,
        pytest.raises(checks.CloudflareChallenge),
    ):
        checks.crawl(http)
