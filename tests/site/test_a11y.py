"""Accessibility of every page (Milestone 2 step 6; design-m2 §6; WCAG 2.2 AA).

Runs in each engine given with ``--browser`` (CI: Chromium and Firefox) against the built
site of tests/site/conftest.py, through ``guarded_context``, so every test also ends with no
page error and no CSP violation.

On the real catalog (site-real and deploy.yml, ``-m "not sample_only"``) a lighter pass runs:
axe on every page in both themes at 1280 x 900, axe with a details panel open, reflow at
320 px and the planted-violation check. It catches what the real data brings (a long name, a
duplicate id, a real specimen or panel). The rest of the grid runs on the sample only (owner
ruling of 2026-09-30): it tests the templates and CSS, which are the same in every row.

- **axe** (axe-core, bundled by axe-playwright-python and injected with ``page.evaluate``,
  which the CSP allows): tags ``wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa``, zero
  violations, on ``/``, ``/methodology/``, ``/privacy/``, ``/about/`` and a missing page
  (the 404), in light and dark, at 375 x 812 and 1280 x 900. The list page is also checked with
  filters on, with the phone filters open (375 only), with a details panel open and in the
  no-results state. Text whose contrast axe leaves unmeasured (rows far down the list skip
  rendering) is measured again once scrolled into view, and must pass. The one exception is
  the heading of a row whose specimen shows: its text is transparent (name_once), which axe
  can only call a 1:1 contrast, and WCAG 1.4.3 sets no contrast for text that is visible to
  no one; test_specimens_loader.py checks that it paints nothing over the specimen.
- **Structure:** axe's landmark, heading and skip-link rules (outside the WCAG tags), and each
  font a list item with its own heading.
- **Consistent help** (3.2.6): the feedback spot has the same place and links on every page.
- **Themes:** light or dark follows the system on every page.
- **Forced colours** (design-m2 §6 asks for Chromium; Playwright emulates them in Firefox
  too, where the palette is always the light one): axe on every page, and pixel checks
  that a specimen's outlines and a focus ring are visible, and that a row's Download and
  Details buttons keep a border and show their text (in the normal themes too). The sample
  catalog has no rendered specimens yet, so specimen checks use a small site built from
  ``tests/fixtures/make_large_catalog.py`` when the served site has none.
- **Reduced motion** (both engines): no transition or animation runs, and axe passes.
- **Reflow** (1.4.10): no sideways scrolling at 320 px, nor at 640 px (1280 px at 200%).
- **Text spacing** (1.4.12): the WCAG spacing overrides, injected into a ``bypass_csp``
  context only, cause no sideways scrolling and clip no text.
- **Focus** (2.4.7, 2.4.11, 1.4.11): tabbing through each page, every focused element is on
  screen, not covered by anything (``elementFromPoint`` hits it), and has an outline at least
  2 px wide with 3:1 contrast against what is behind it.
- **Keyboard paths:** the skip link, the phone "Filters" button, arrow keys in a radio
  group, a details panel opened with Enter or Space and closed with Esc, and "Clear filters"
  in the no-results state; focus never falls back to the page body.

Owners of what these tests exercise: site/css/10-base.css and base.html.j2 (shell),
20-list.css, 25-filters.css and the list templates (list), 30-details.css (panel),
35-specimens.css (specimens), 40-pages.css and the page templates (content pages).
"""

import re
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
PAGES = {
    "list": "/",
    "methodology": "/methodology/",
    "privacy": "/privacy/",
    "about": "/about/",
    "404": "/missing",
}
CONTENT_PAGES = {name: path for name, path in PAGES.items() if name != "list"}
SCHEMES = ("light", "dark")
PHONE = {"width": 375, "height": 812}
DESKTOP = {"width": 1280, "height": 900}
VIEWPORTS = {"375": PHONE, "1280": DESKTOP}
# A view with some filters on that still shows fonts, and an empty view (Coding holds only
# monospaced fonts, which are all in Category Monospace: site/CONTRACT.md section 4).
FILTERS_HASH = "#cat=sans-serif&hide=limited,windows"
NO_RESULTS_HASH = "#rank=coding&cat=serif"
# WCAG 1.4.12's test values.
TEXT_SPACING_CSS = """
* { line-height: 1.5 !important; letter-spacing: 0.12em !important;
    word-spacing: 0.16em !important; }
p { margin-bottom: 2em !important; }
"""
MAX_TAB_STOPS = 60
MIN_FOCUS_CONTRAST = 3.0
# Under forced colours the ring takes the visitor's own system colour, so its contrast is the
# palette's (Firefox's emulated palette draws Highlight #3399ff on white, 2.9:1). The check
# there is that a ring is drawn at all, clearly apart from what was behind it.
MIN_FORCED_RING_CHANGE = 2.0
MIN_OUTLINE_PX = 2.0
# Durations at or below this count as "no motion" (10-base.css uses 0.01ms under reduce).
MOTIONLESS_S = 0.00002

# Run before any page script (outside the CSP): counts 'tff:list-ready'.
READY_JS = """
(() => {
  window.__tffReady = 0;
  document.addEventListener('tff:list-ready', () => { window.__tffReady += 1; });
})();
"""

# The focused element: where it is, what is on top of it, and its outline. An inline link
# that wraps has one box per line; it counts as visible when any box's centre shows it.
FOCUS_JS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const tag = (n) => `${n.tagName.toLowerCase()}${n.id ? '#' + n.id : ''}` +
    `${n.classList.length ? '.' + [...n.classList].join('.') : ''}`;
  const r = el.getBoundingClientRect();
  const boxes = [...el.getClientRects()].filter((b) => b.width > 0 && b.height > 0);
  let inView = false;
  let covered = true;
  let hitName = null;
  for (const b of boxes) {
    // The part of the box inside the viewport (a browser may leave a box partly scrolled).
    const left = Math.max(b.left, 0);
    const top = Math.max(b.top, 0);
    const right = Math.min(b.right, document.documentElement.clientWidth);
    const bottom = Math.min(b.bottom, document.documentElement.clientHeight);
    if (right - left < 1 || bottom - top < 1) continue;
    inView = true;
    const x = (left + right) / 2;
    const y = (top + bottom) / 2;
    const hit = document.elementFromPoint(x, y);
    const mine = hit && (hit === el || el.contains(hit) ||
      (el.labels && [...el.labels].some((l) => l === hit || l.contains(hit))));
    if (mine) { covered = false; break; }
    if (hit) hitName = tag(hit);
  }
  // A stable number per element, so a revisit is recognised (names repeat: "a", "a" ...).
  const ids = window.__tffFocusIds || (window.__tffFocusIds = new WeakMap());
  if (!ids.has(el)) {
    window.__tffFocusN = (window.__tffFocusN || 0) + 1;
    ids.set(el, window.__tffFocusN);
  }
  const cs = getComputedStyle(el);
  let bg = 'rgba(0, 0, 0, 0)';
  for (let node = el.parentElement; node; node = node.parentElement) {
    const c = getComputedStyle(node).backgroundColor;
    if (!/^rgba\\(.*,\\s*0\\)$/.test(c) && c !== 'transparent') { bg = c; break; }
  }
  return {
    name: tag(el),
    key: ids.get(el),
    documentFocused: document.hasFocus(),
    rect: [r.left, r.top, r.width, r.height],
    inView,
    covered,
    hit: hitName,
    outlineStyle: cs.outlineStyle,
    outlineWidth: parseFloat(cs.outlineWidth) || 0,
    outlineColor: cs.outlineColor,
    background: bg,
  };
}
"""

FRAMES_JS = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"

# Elements wider than the viewport, or sticking out of it, that aren't inside a scrollable
# box (1.4.10 allows those for tables) or hidden on purpose.
OVERFLOW_JS = """
() => {
  const width = document.documentElement.clientWidth;
  const out = [];
  const skip = (el) => el.closest('.visually-hidden, [hidden], noscript');
  const scroller = (el) => {
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const o = getComputedStyle(n).overflowX;
      if (o === 'auto' || o === 'scroll') return true;
    }
    return false;
  };
  for (const el of document.body.querySelectorAll('*')) {
    if (skip(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right > width + 1 || r.left < -1) {
      if (scroller(el)) continue;
      if (el.matches('.skip-link') && document.activeElement !== el) continue;
      out.push(`${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}` +
        `.${[...el.classList].join('.')} [${Math.round(r.left)}, ${Math.round(r.right)}]`);
    }
  }
  return { scrollWidth: document.documentElement.scrollWidth, width, out: out.slice(0, 20) };
}
"""

# Boxes that hide overflowing text (overflow hidden or clip, or an ellipsis), and boxes whose
# text runs out of their own fixed size.
CLIPPED_JS = """
() => {
  const out = [];
  const hasText = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.data.trim());
  for (const el of document.body.querySelectorAll('*')) {
    if (el.closest('.visually-hidden, [hidden], noscript, .spec')) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    if (el.matches('.skip-link') && document.activeElement !== el) continue;
    const hides = ['hidden', 'clip'].includes(cs.overflowX) || ['hidden', 'clip'].includes(cs.overflowY)
      || cs.textOverflow === 'ellipsis';
    const text = hasText(el) || el.querySelector('*') !== null;
    if (!hides || !text) continue;
    if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) {
      out.push(`${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}` +
        `.${[...el.classList].join('.')} (${el.scrollWidth}x${el.scrollHeight} in ` +
        `${el.clientWidth}x${el.clientHeight})`);
    }
  }
  return out.slice(0, 20);
}
"""

# Running transitions and animations longer than a blink.
MOTION_JS = """
(limit) => {
  const secs = (v) => v.split(',').map((s) => {
    s = s.trim();
    return s.endsWith('ms') ? parseFloat(s) / 1000 : parseFloat(s) || 0;
  });
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    for (const pseudo of [null, '::before', '::after']) {
      const cs = getComputedStyle(el, pseudo);
      const t = Math.max(...secs(cs.transitionDuration));
      const a = cs.animationName !== 'none' ? Math.max(...secs(cs.animationDuration)) : 0;
      if (t > limit || a > limit) {
        out.push(`${el.tagName.toLowerCase()}.${[...el.classList].join('.')}${pseudo || ''}` +
          ` transition ${cs.transitionDuration} animation ${cs.animationDuration}`);
      }
    }
  }
  const root = getComputedStyle(document.documentElement);
  return { out: out.slice(0, 20), duration: root.getPropertyValue('--duration').trim(),
           scroll: root.scrollBehavior };
}
"""


# ---------------------------------------------------------------------------- helpers


def wait_for(page: Any, expression: str, timeout_s: float = 10.0) -> None:
    """Poll a JS expression until it is truthy. (Playwright's wait_for_function compiles a
    string inside the page, which the site's CSP may block as eval.)"""
    deadline = time.monotonic() + timeout_s
    while not page.evaluate(f"() => Boolean({expression})"):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for: {expression}")
        page.wait_for_timeout(25)


SCHEME_JS = "(dark) => matchMedia('(prefers-color-scheme: dark)').matches === dark"


def goto(page: Any, path: str, scheme: str | None) -> None:
    """Load ``path`` in the ``scheme`` colour scheme, if one is given, and check that it is
    in effect.

    Playwright's Firefox drops the context's ``color_scheme`` when a navigation lands on a
    page sent with ``Cross-Origin-Opener-Policy: same-origin`` (site.caddy's header; the
    process swap loses it, while ``forced_colors`` and ``reduced_motion`` survive), and the
    page then renders light. A page-level scheme set after that sticks, so the page is set
    and loaded again."""
    page.goto(path, wait_until="load")
    if scheme is None:
        return
    if not page.evaluate(SCHEME_JS, scheme == "dark"):
        page.emulate_media(color_scheme=scheme)
        page.reload(wait_until="load")
    assert page.evaluate(SCHEME_JS, scheme == "dark"), f"the {scheme} scheme isn't in effect"


def open_page(make: Callable[..., Any], path: str, **kwargs: Any) -> tuple[Any, Any]:
    """A guarded context and a page at ``path``, once it has loaded (and, on the list page,
    once the list is live). The content pages carry no script."""
    guarded = make(**kwargs)
    guarded.context.add_init_script(READY_JS)
    page = guarded.new_page()
    goto(page, path, kwargs.get("color_scheme"))
    if path.split("#", 1)[0] == "/":
        wait_for(page, "window.__tffReady >= 1")
        wait_for(page, "!document.getElementById('filters').hidden")
    return guarded, page


def list_state(page: Any, state: str) -> None:
    """Put the list page (already open at ``/``, or at the state's hash) into ``state``."""
    if state == "filters":
        wait_for(page, "!/^Showing 0 /.test(document.getElementById('count').textContent)")
        assert page.evaluate("location.hash") == FILTERS_HASH
    elif state == "filters-open":
        page.locator("#f-toggle").click()
        wait_for(page, "document.getElementById('f-toggle').ariaExpanded === 'true'")
        assert page.locator("#f-more").is_visible()
    elif state == "details":
        toggle = page.locator("#list > li.font .details-toggle").first
        toggle.click()
        panel_id = toggle.get_attribute("aria-controls")
        page.wait_for_selector(f"#{panel_id}[data-state='ready']")
        assert page.locator(f"#{panel_id}").is_visible()
    elif state == "no-results":
        wait_for(page, "!document.getElementById('no-results').hidden")


LIST_STATES = {
    "filters": f"/{FILTERS_HASH}",
    "filters-open": f"/{FILTERS_HASH}",
    "details": "/",
    "no-results": f"/{NO_RESULTS_HASH}",
}


def axe(page: Any, context: str | None = None) -> Any:
    from axe_playwright_python.sync_playwright import Axe

    options = {
        "runOnly": {"type": "tag", "values": AXE_TAGS},
        "resultTypes": ["violations", "incomplete"],
    }
    return Axe().run(page, context=context, options=options)


# Rules (WCAG 2.0 A and AA, 2.2 AA) that must have run and passed on any page, so a clean
# result cannot come from axe checking nothing (a wrong tag, or axe failing to load).
MUST_PASS = {"color-contrast", "document-title", "html-has-lang", "link-name", "target-size"}


# One axe rule on one node, with axe already in the page (the first run injected it).
RERUN_JS = """
async ([target, rule]) => {
  const r = await axe.run({ include: [target] },
    { runOnly: { type: 'rule', values: [rule] }, resultTypes: ['violations', 'incomplete'] });
  const out = [];
  for (const kind of ['violations', 'incomplete']) {
    for (const item of r[kind]) {
      for (const node of item.nodes) {
        const why = [...node.any, ...node.all, ...node.none].map((c) => c.message).join('; ');
        out.push(`${kind}: ${node.target.join(' ')}: ${why}`);
      }
    }
  }
  return out;
}
"""
SCROLL_TO_JS = """
(selector) => {
  const el = document.querySelector(selector);
  if (el) el.scrollIntoView({ block: 'center' });
  return Boolean(el);
}
"""


# Whether a node is the unpainted heading of a row whose specimen shows: transparent text over
# the drawn name (owner rulings name_once and score_centred, site/CONTRACT.md section 4).
UNPAINTED_HEADING_JS = """
(selector) => {
  const el = document.querySelector(selector);
  return Boolean(el && el.matches('.font-title.is-drawn > h3.font-name')
    && /^rgba\\(\\d+, \\d+, \\d+, 0\\)$/.test(getComputedStyle(el).color));
}
"""


# Every row rendered, or back to the stylesheet's content-visibility (CSSOM: the CSP allows it).
RENDER_ROWS_JS = """
(on) => {
  for (const li of document.querySelectorAll('li.font')) li.style.contentVisibility = on ? 'visible' : '';
}
"""


def unmeasured_contrast(page: Any, results: Any) -> list[str]:
    """Nodes whose contrast axe left unmeasured, measured again once each is on screen.

    Rows far down the list skip rendering (``content-visibility: auto``), so axe would report
    their text as "overlapped" and leave it as needing review. ``assert_axe_clean`` renders
    every row for its run, which leaves none on the real catalog's 500 rows; checking them
    here one at a time took a minute or two a page. Any node left over is scrolled to the
    middle of the screen and checked alone; it must then pass. Returns what still doesn't.

    A row's heading that the specimen hides has transparent text, which axe can only call a
    1:1 contrast: WCAG 1.4.3 sets no contrast for text visible to no one, so it is skipped."""
    left: list[str] = []
    x, y = page.evaluate("[scrollX, scrollY]")
    for item in results.response["incomplete"]:
        if item["id"] != "color-contrast":
            continue
        for node in item["nodes"]:
            target = node["target"]
            if len(target) == 1 and page.evaluate(UNPAINTED_HEADING_JS, target[0]):
                continue
            if len(target) == 1 and page.evaluate(SCROLL_TO_JS, target[0]):
                page.evaluate(FRAMES_JS)
            left += page.evaluate(RERUN_JS, [target, "color-contrast"])
    page.evaluate("([x, y]) => scrollTo(x, y)", [x, y])
    return left


def assert_axe_clean(page: Any, where: str) -> None:
    page.evaluate(RENDER_ROWS_JS, True)
    try:
        results = axe(page)
    finally:
        page.evaluate(RENDER_ROWS_JS, False)
    assert results.violations_count == 0, f"{where}\n{results.generate_report()}"
    passed = {rule["id"] for rule in results.response["passes"]}
    assert passed >= MUST_PASS, f"{where}: axe didn't run {sorted(MUST_PASS - passed)}"
    assert unmeasured_contrast(page, results) == [], f"{where}: contrast not measured"


_RGB = re.compile(r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)")


def parse_rgb(value: str) -> tuple[float, float, float, float] | None:
    m = _RGB.fullmatch(value.strip())
    if not m:
        return None
    alpha = m.group(4)
    a = 1.0 if alpha is None else float(alpha.rstrip("%")) / (100 if "%" in alpha else 1)
    return float(m.group(1)), float(m.group(2)), float(m.group(3)), a


def luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def png_pixels(png: bytes) -> np.ndarray:
    """Decode an 8-bit, non-interlaced RGB or RGBA PNG (what Playwright writes) into an
    array indexed by row, column and RGB channel."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, chunks = 8, []
    width = height = color_type = 0
    while pos < len(png):
        length = int.from_bytes(png[pos : pos + 4], "big")
        kind = png[pos + 4 : pos + 8]
        body = png[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width = int.from_bytes(body[0:4], "big")
            height = int.from_bytes(body[4:8], "big")
            depth, color_type, interlace = body[8], body[9], body[12]
            assert depth == 8, "only 8-bit PNGs"
            assert color_type in {2, 6}, "only RGB and RGBA PNGs"
            assert interlace == 0, "only non-interlaced PNGs"
        elif kind == b"IDAT":
            chunks.append(body)
        elif kind == b"IEND":
            break
    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(b"".join(chunks))
    stride = width * channels
    out = np.zeros((height, stride), dtype=np.uint8)
    prev = [0] * stride
    for y in range(height):
        start = y * (stride + 1)
        method = raw[start]  # the row's filter
        line = list(raw[start + 1 : start + 1 + stride])
        cur = [0] * stride
        for i in range(stride):
            left = cur[i - channels] if i >= channels else 0
            up = prev[i]
            corner = prev[i - channels] if i >= channels else 0
            if method == 0:
                pred = 0
            elif method == 1:
                pred = left
            elif method == 2:
                pred = up
            elif method == 3:
                pred = (left + up) // 2
            else:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                pred = left if pa <= pb and pa <= pc else up if pb <= pc else corner
            cur[i] = (line[i] + pred) & 0xFF
        out[y] = cur
        prev = cur
    return out.reshape(height, width, channels)[..., :3].astype(np.float64)


def pixel_luminance(pixels: np.ndarray) -> np.ndarray:
    c = pixels / 255
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def ink_share(pixels: np.ndarray, min_contrast: float = 3.0) -> float:
    """Share of pixels with at least ``min_contrast`` against the box's most common colour."""
    lum = pixel_luminance(pixels)
    flat = pixels.reshape(-1, 3)
    colours, counts = np.unique(flat, axis=0, return_counts=True)
    bg = pixel_luminance(colours[counts.argmax()][None, None, :])[0, 0]
    hi, lo = np.maximum(lum, bg), np.minimum(lum, bg)
    ratio = (hi + 0.05) / (lo + 0.05)
    return float((ratio >= min_contrast).mean())


# ---------------------------------------------------------------------- the specimen site


@pytest.fixture(scope="module")
def specimen_site(
    site_dir: Path, site_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    """URL of a site with rendered specimens: the served one if it has any, else a small
    site built from make_large_catalog and served here on the loopback interface."""
    if any((site_dir / "assets" / "specimens").glob("*.svg")):
        yield site_url
        return
    from tests.fixtures import make_large_catalog

    from tff_site import build, serve

    root = tmp_path_factory.mktemp("specimen-site")
    catalog = make_large_catalog.write(root / "data", fonts=60)
    out = root / "site"
    build.build(catalog, out, commit="0" * 40, allow_dirty=True, font_files=False)
    server = serve.make_server(out, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, name="a11y-specimens", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def open_specimens(browser: Any, url: str, **kwargs: Any) -> tuple[Any, Any]:
    context = browser.new_context(base_url=url, **kwargs)
    context.add_init_script(READY_JS)
    page = context.new_page()
    goto(page, "/", kwargs.get("color_scheme"))
    wait_for(page, "window.__tffReady >= 1")
    wait_for(page, "document.querySelector('#list span.spec[data-state=\"set\"]') !== null")
    return context, page


def first_specimen_pixels(page: Any, timeout_s: float = 10.0) -> np.ndarray:
    """Pixels of the first specimen box, once its mask has painted something."""
    spec = page.locator("#list span.spec[data-state='set']").first
    spec.scroll_into_view_if_needed()
    deadline = time.monotonic() + timeout_s
    while True:
        pixels = png_pixels(spec.screenshot(animations="disabled"))
        if ink_share(pixels) > 0.01 or time.monotonic() > deadline:
            return pixels
        page.wait_for_timeout(100)


# ------------------------------------------------------------------------------- axe


@pytest.mark.real_catalog(viewport="1280")
@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("name", PAGES)
def test_axe_every_page(guarded_context: Any, name: str, scheme: str, viewport: str) -> None:
    guarded, page = open_page(
        guarded_context, PAGES[name], color_scheme=scheme, viewport=VIEWPORTS[viewport]
    )
    assert_axe_clean(page, f"{PAGES[name]} {scheme} {viewport}")
    guarded.assert_clean(page)


@pytest.mark.real_catalog(state="details", viewport="1280")
@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("state", ["filters", "filters-open", "details", "no-results"])
def test_axe_list_states(guarded_context: Any, state: str, scheme: str, viewport: str) -> None:
    if state == "filters-open" and viewport != "375":
        pytest.skip("the Filters button shows only on narrow screens")
    guarded, page = open_page(
        guarded_context, LIST_STATES[state], color_scheme=scheme, viewport=VIEWPORTS[viewport]
    )
    list_state(page, state)
    assert_axe_clean(page, f"list {state} {scheme} {viewport}")
    guarded.assert_clean(page)


def test_axe_catches_a_planted_violation(guarded_context: Any) -> None:
    # The harness itself: an image without alt text and grey-on-grey text must be reported.
    guarded, page = open_page(guarded_context, "/")
    page.evaluate(
        """() => {
          const img = document.createElement('img');
          img.src = '/favicon.svg';
          const p = document.createElement('p');
          p.textContent = 'Faint text';
          p.style.color = '#bbbbbb';
          p.style.backgroundColor = '#cccccc';
          document.getElementById('main').append(img, p);
        }"""
    )
    found = {v["id"] for v in axe(page).response["violations"]}
    assert {"image-alt", "color-contrast"} <= found, found
    guarded.assert_clean(page)


@pytest.mark.sample_only
@pytest.mark.parametrize("scheme", SCHEMES)
def test_axe_with_specimens(browser: Any, specimen_site: str, scheme: str) -> None:
    context, page = open_specimens(browser, specimen_site, color_scheme=scheme)
    try:
        assert_axe_clean(page, f"list with specimens, {scheme}")
    finally:
        context.close()


# ------------------------------------------------- structure, consistent help and themes

# axe's structure rules outside the WCAG tags, for step 6's "landmarks, one h1, a skip link,
# and each font a list item with its own heading".
STRUCTURE_RULES = [
    "page-has-heading-one",
    "landmark-one-main",
    "landmark-no-duplicate-banner",
    "landmark-no-duplicate-contentinfo",
    "landmark-no-duplicate-main",
    "landmark-banner-is-top-level",
    "landmark-contentinfo-is-top-level",
    "landmark-main-is-top-level",
    "landmark-unique",
    "region",
    "heading-order",
    "empty-heading",
    "skip-link",
]
STRUCTURE_MUST_PASS = {"page-has-heading-one", "landmark-one-main", "region", "skip-link"}
STRUCTURE_PAGES = {
    **{name: (path, None) for name, path in PAGES.items()},
    "list-details": ("/", "details"),
    "list-no-results": (f"/{NO_RESULTS_HASH}", "no-results"),
    "list-filters-open": (f"/{FILTERS_HASH}", "filters-open"),
}


@pytest.mark.sample_only
@pytest.mark.parametrize("name", STRUCTURE_PAGES)
def test_page_structure(guarded_context: Any, name: str) -> None:
    path, state = STRUCTURE_PAGES[name]
    guarded, page = open_page(guarded_context, path, viewport=PHONE)
    if state:
        list_state(page, state)
    options = {"runOnly": {"type": "rule", "values": STRUCTURE_RULES}}
    from axe_playwright_python.sync_playwright import Axe

    results = Axe().run(page, options=options)
    assert results.violations_count == 0, f"{name}\n{results.generate_report()}"
    passed = {rule["id"] for rule in results.response["passes"]}
    assert passed >= STRUCTURE_MUST_PASS, sorted(STRUCTURE_MUST_PASS - passed)
    if path.split("#", 1)[0] == "/":
        # Each font: a list item in an ordered list, with its own heading.
        rows = page.evaluate(
            """() => [...document.querySelectorAll('#list > *')].map((li) =>
                 [li.tagName, li.querySelectorAll(':scope > .font-row > .font-title > h3.font-name').length])"""
        )
        assert all(row == ["LI", 1] for row in rows), rows
    guarded.assert_clean(page)


# The feedback spot (3.2.6 Consistent Help): where it sits and what it offers.
HELP_JS = """
() => {
  const help = document.getElementById('feedback');
  const footer = document.querySelector('footer.site-footer');
  const main = document.getElementById('main');
  if (!help || !footer || !main) return null;
  const words = (n) => n.textContent.trim().replace(/\\s+/g, ' ');
  const parts = [...footer.children];
  return {
    inFooter: footer.contains(help),
    afterMain: Boolean(main.compareDocumentPosition(help) & Node.DOCUMENT_POSITION_FOLLOWING),
    place: parts.findIndex((part) => part === help || part.contains(help)),
    before: parts.slice(0, parts.findIndex((part) => part.contains(help))).map((p) => p.className),
    text: words(help),
    links: [...help.querySelectorAll('a')].map((a) => [words(a), a.getAttribute('href')]),
    shown: help.getBoundingClientRect().height > 0 && getComputedStyle(help).display !== 'none',
  };
}
"""


@pytest.mark.sample_only
def test_feedback_keeps_one_place_on_every_page(guarded_context: Any) -> None:
    found = {}
    for name, path in PAGES.items():
        guarded, page = open_page(guarded_context, path, viewport=PHONE)
        found[name] = page.evaluate(HELP_JS)
        guarded.assert_clean(page)
    first = found["list"]
    assert first is not None, "no #feedback in footer.site-footer on the list page"
    assert first["inFooter"], first
    assert first["afterMain"], first
    assert first["shown"], first
    assert first["links"], first
    for name, help_spot in found.items():
        assert help_spot == first, f"{PAGES[name]}: the feedback spot differs from the list's"


BACKGROUND_JS = """
() => {
  const solid = (c) => c && c !== 'transparent' && !/^rgba\\(.*,\\s*0\\)$/.test(c);
  const bg = [document.body, document.documentElement]
    .map((el) => getComputedStyle(el).backgroundColor).find(solid) || null;
  return { bg, fg: getComputedStyle(document.body).color };
}
"""


@pytest.mark.sample_only
@pytest.mark.parametrize("name", PAGES)
def test_theme_follows_the_system(guarded_context: Any, name: str) -> None:
    seen = {}
    for scheme in SCHEMES:
        guarded, page = open_page(guarded_context, PAGES[name], color_scheme=scheme)
        seen[scheme] = page.evaluate(BACKGROUND_JS)
        guarded.assert_clean(page)
    lum = {}
    for scheme, colours in seen.items():
        bg, fg = parse_rgb(colours["bg"] or ""), parse_rgb(colours["fg"])
        assert bg, f"{scheme}: no solid page background ({colours})"
        assert fg, f"{scheme}: no text colour ({colours})"
        lum[scheme] = (luminance(bg[:3]), luminance(fg[:3]))
    assert lum["light"][0] > lum["light"][1], f"light: dark text on a light page {seen}"
    assert lum["dark"][0] < lum["dark"][1], f"dark: light text on a dark page {seen}"
    assert lum["dark"][0] < lum["light"][0], seen


# ------------------------------------------------------------------------ forced colours


@pytest.mark.sample_only
@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("name", PAGES)
def test_forced_colors_axe(guarded_context: Any, name: str, scheme: str) -> None:
    guarded, page = open_page(
        guarded_context, PAGES[name], forced_colors="active", color_scheme=scheme
    )
    assert page.evaluate("matchMedia('(forced-colors: active)').matches")
    if name == "list":
        list_state(page, "details")
    assert_axe_clean(page, f"{PAGES[name]} forced colours {scheme}")
    guarded.assert_clean(page)


@pytest.mark.sample_only
@pytest.mark.parametrize("mode", ["light", "dark", "forced-light", "forced-dark"])
def test_specimens_are_visible(browser: Any, specimen_site: str, mode: str) -> None:
    kwargs: dict[str, Any] = {"color_scheme": mode.removeprefix("forced-")}
    if mode.startswith("forced"):
        kwargs["forced_colors"] = "active"
    context, page = open_specimens(browser, specimen_site, **kwargs)
    try:
        pixels = first_specimen_pixels(page)
        share = ink_share(pixels)
        # Outlines of a family name and a sample line cover a few per cent of the box.
        assert share > 0.01, f"{mode}: specimen shows almost nothing ({share:.2%} ink)"
        assert share < 0.8, f"{mode}: specimen is a solid block ({share:.2%} ink)"
    finally:
        context.close()


@pytest.mark.sample_only
@pytest.mark.parametrize("scheme", SCHEMES)
def test_forced_colors_focus_ring_is_visible(guarded_context: Any, scheme: str) -> None:
    guarded, page = open_page(guarded_context, "/", forced_colors="active", color_scheme=scheme)
    row = page.locator("#list > li.font").first
    toggle = row.locator(".details-toggle")
    # On wide screens the list starts below the front page's note (site ruling of 2026-09-29),
    # so the first row may start below the fold: bring it into view first, so focusing it
    # later scrolls nothing and both screenshots show the same place.
    toggle.scroll_into_view_if_needed()
    page.evaluate(FRAMES_JS)
    box = toggle.bounding_box()
    assert box is not None
    pad = 8
    clip = {
        "x": max(box["x"] - pad, 0),
        "y": max(box["y"] - pad, 0),
        "width": box["width"] + 2 * pad,
        "height": box["height"] + 2 * pad,
    }
    page.mouse.move(0, 0)
    # Nothing focused before, so the only change is the button's own ring (a ring leaving the
    # link next to it can't count).
    before = png_pixels(page.screenshot(clip=clip, animations="disabled"))
    row.locator(".download").focus()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('details-toggle')")
    page.evaluate(FRAMES_JS)
    after = png_pixels(page.screenshot(clip=clip, animations="disabled"))
    lb, la = pixel_luminance(before), pixel_luminance(after)
    ratio = (np.maximum(la, lb) + 0.05) / (np.minimum(la, lb) + 0.05)
    changed = int((ratio >= MIN_FORCED_RING_CHANGE).sum())
    perimeter = 2 * (box["width"] + box["height"])
    assert changed >= perimeter, (
        f"focus ring on the details button changes {changed} pixels by "
        f"{MIN_FORCED_RING_CHANGE}:1; a ring around it needs at least {perimeter:.0f}"
    )
    guarded.assert_clean(page)


# A row's two actions: each one's border (style, width, colour), the box of its visible
# text's first line (its first text node, before the visually hidden words), and the page's
# background.
ACTIONS_JS = """() => {
  const row = document.querySelector('#list > li.font');
  return {
    page: getComputedStyle(document.body).backgroundColor,
    actions: ['.download', '.details-toggle'].map((sel) => {
      const el = row.querySelector(sel);
      const s = getComputedStyle(el);
      const range = document.createRange();
      range.selectNodeContents(el.firstChild);
      const line = range.getClientRects()[0];
      return { sel, style: s.borderTopStyle, width: parseFloat(s.borderTopWidth),
               colour: s.borderTopColor,
               text: { x: line.left, y: line.top, width: line.width, height: line.height } };
    }),
  };
}"""


@pytest.mark.sample_only
@pytest.mark.parametrize("mode", ["light", "dark", "forced-light", "forced-dark"])
def test_row_actions_keep_their_text_and_border(guarded_context: Any, mode: str) -> None:
    """The owner's site ruling of 2026-10-05 (download_button): the Download button is filled
    with the accent and Details is a faint outline. In forced colours both keep a border
    clearly apart from the page, and in every theme each one's text shows: its line's box
    has ink against its background. In forced colours Chromium paints a backplate in the
    page's colour behind text, which hid text in the accent's text colour (the box there was
    one blank colour)."""
    kwargs: dict[str, Any] = {"color_scheme": mode.removeprefix("forced-"), "viewport": PHONE}
    if mode.startswith("forced"):
        kwargs["forced_colors"] = "active"
    guarded, page = open_page(guarded_context, "/", **kwargs)
    row = page.locator("#list > li.font").first
    row.scroll_into_view_if_needed()
    page.evaluate(FRAMES_JS)
    got = page.evaluate(ACTIONS_JS)
    if mode.startswith("forced"):
        back = parse_rgb(got["page"])
        assert back is not None, got
        assert back[3] == 1, got
        for action in got["actions"]:
            colour = parse_rgb(action["colour"])
            assert action["style"] == "solid", action
            assert action["width"] >= 1, action
            assert colour is not None, action
            assert contrast(colour[:3], back[:3]) >= 3, (action, got)
    for action in got["actions"]:
        pixels = png_pixels(page.screenshot(clip=action["text"], animations="disabled"))
        share = ink_share(pixels)
        assert share > 0.05, f"{mode}: {action['sel']} shows almost no text ({share:.2%} ink)"
    guarded.assert_clean(page)


# ------------------------------------------------------------------------ reduced motion


@pytest.mark.sample_only
def test_reduced_motion_stops_every_transition(guarded_context: Any) -> None:
    guarded, page = open_page(guarded_context, "/", reduced_motion="reduce", viewport=PHONE)
    assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    list_state(page, "filters-open")
    list_state(page, "details")
    motion = page.evaluate(MOTION_JS, MOTIONLESS_S)
    assert motion["duration"] in {"0ms", "0s", "0"}, f"--duration is {motion['duration']}"
    assert motion["scroll"] != "smooth"
    assert motion["out"] == [], "transitions or animations still run"
    assert_axe_clean(page, "list, reduced motion")
    guarded.assert_clean(page)


# ------------------------------------------------------------------------- reflow (1.4.10)


@pytest.mark.real_catalog(width=320)
@pytest.mark.parametrize("width", [320, 640])
@pytest.mark.parametrize("name", PAGES)
def test_reflow_pages(guarded_context: Any, name: str, width: int) -> None:
    guarded, page = open_page(
        guarded_context, PAGES[name], viewport={"width": width, "height": 700}
    )
    found = page.evaluate(OVERFLOW_JS)
    assert found["scrollWidth"] <= width, f"{PAGES[name]} scrolls sideways: {found}"
    assert found["out"] == [], f"{PAGES[name]}: boxes stick out at {width} px"
    guarded.assert_clean(page)


@pytest.mark.sample_only
@pytest.mark.parametrize("width", [320, 640])
@pytest.mark.parametrize("state", ["filters-open", "details", "no-results"])
def test_reflow_list_states(guarded_context: Any, state: str, width: int) -> None:
    guarded, page = open_page(
        guarded_context, LIST_STATES[state], viewport={"width": width, "height": 700}
    )
    list_state(page, state)
    found = page.evaluate(OVERFLOW_JS)
    assert found["scrollWidth"] <= width, f"list {state} scrolls sideways: {found}"
    assert found["out"] == [], f"list {state}: boxes stick out at {width} px"
    guarded.assert_clean(page)


# -------------------------------------------------------------------- text spacing (1.4.12)


def _text_spacing_page(
    guarded_context: Any, path: str, viewport: dict[str, int]
) -> tuple[Any, Any]:
    # Injecting a stylesheet breaks the page's CSP, so only this context bypasses it.
    return open_page(guarded_context, path, viewport=viewport, bypass_csp=True)


@pytest.mark.sample_only
@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("name", PAGES)
def test_text_spacing_pages(guarded_context: Any, name: str, viewport: str) -> None:
    guarded, page = _text_spacing_page(guarded_context, PAGES[name], VIEWPORTS[viewport])
    page.add_style_tag(content=TEXT_SPACING_CSS)
    page.wait_for_timeout(50)
    found = page.evaluate(OVERFLOW_JS)
    width = VIEWPORTS[viewport]["width"]
    assert found["scrollWidth"] <= width, f"{PAGES[name]} scrolls sideways: {found}"
    assert found["out"] == [], f"{PAGES[name]}: boxes stick out with text spacing"
    assert page.evaluate(CLIPPED_JS) == [], f"{PAGES[name]}: text clipped with text spacing"
    assert guarded.errors == []


@pytest.mark.sample_only
@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("state", ["filters-open", "details", "no-results"])
def test_text_spacing_list_states(guarded_context: Any, state: str, viewport: str) -> None:
    if state == "filters-open" and viewport != "375":
        pytest.skip("the Filters button shows only on narrow screens")
    guarded, page = _text_spacing_page(guarded_context, LIST_STATES[state], VIEWPORTS[viewport])
    list_state(page, state)
    page.add_style_tag(content=TEXT_SPACING_CSS)
    page.wait_for_timeout(50)
    found = page.evaluate(OVERFLOW_JS)
    width = VIEWPORTS[viewport]["width"]
    assert found["scrollWidth"] <= width, f"list {state} scrolls sideways: {found}"
    assert found["out"] == [], f"list {state}: boxes stick out with text spacing"
    assert page.evaluate(CLIPPED_JS) == [], f"list {state}: text clipped with text spacing"
    assert guarded.errors == []


# ------------------------------------------------------ focus visible and not obscured


def _tab_through(page: Any, stops: int) -> list[dict[str, Any]]:
    """Press Tab up to ``stops`` times; return what each focused element looked like. Stops
    when focus leaves the page, stays put, or wraps around to the first element."""
    seen: list[dict[str, Any]] = []
    keys: set[int] = set()
    for _ in range(stops):
        page.keyboard.press("Tab")
        # Firefox scrolls the focused element into view on its next frame.
        page.evaluate(FRAMES_JS)
        info = page.evaluate(FOCUS_JS)
        if info is None or not info["documentFocused"] or info["key"] in keys:
            break
        keys.add(info["key"])
        seen.append(info)
    return seen


def _focus_problems(stops: list[dict[str, Any]]) -> list[str]:
    problems = []
    for info in stops:
        name = info["name"]
        if not info["inView"]:
            problems.append(f"{name}: focused off screen at {info['rect']}")
        elif info["covered"]:
            problems.append(f"{name}: covered by {info['hit']} (2.4.11)")
        if info["outlineStyle"] == "none" or info["outlineWidth"] < MIN_OUTLINE_PX:
            problems.append(
                f"{name}: outline {info['outlineStyle']} {info['outlineWidth']}px (2.4.7)"
            )
            continue
        ring, back = parse_rgb(info["outlineColor"]), parse_rgb(info["background"])
        if ring and back and back[3] > 0:
            ratio = contrast(ring[:3], back[:3])
            if ratio < MIN_FOCUS_CONTRAST:
                problems.append(
                    f"{name}: outline {info['outlineColor']} on {info['background']} is "
                    f"{ratio:.2f}:1 (1.4.11)"
                )
    return problems


@pytest.mark.sample_only
@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("name", PAGES)
def test_focus_is_visible_and_not_obscured(
    guarded_context: Any, name: str, viewport: str, scheme: str
) -> None:
    guarded, page = open_page(
        guarded_context, PAGES[name], viewport=VIEWPORTS[viewport], color_scheme=scheme
    )
    if name == "list" and viewport == "375":
        list_state(page, "filters-open")
        page.locator("#main").focus()
    stops = _tab_through(page, MAX_TAB_STOPS)
    assert len(stops) >= 3, f"only {len(stops)} tab stops on {PAGES[name]}"
    assert _focus_problems(stops) == []
    guarded.assert_clean(page)


@pytest.mark.sample_only
def test_focus_in_an_open_panel_is_visible_and_not_obscured(guarded_context: Any) -> None:
    guarded, page = open_page(guarded_context, "/", viewport=PHONE)
    list_state(page, "details")
    stops = _tab_through(page, 15)
    assert any("details-close" in s["name"] for s in stops), [s["name"] for s in stops]
    assert _focus_problems(stops) == []
    guarded.assert_clean(page)


# ------------------------------------------------------------------------ keyboard paths


def _active(page: Any) -> str:
    return page.evaluate(
        "() => { const a = document.activeElement; if (!a) return '';"
        " return `${a.tagName.toLowerCase()}${a.id ? '#' + a.id : ''}"
        "${a.classList.length ? '.' + [...a.classList].join('.') : ''}`; }"
    )


@pytest.mark.sample_only
@pytest.mark.parametrize("name", PAGES)
def test_skip_link_is_first_and_moves_focus_to_main(guarded_context: Any, name: str) -> None:
    guarded, page = open_page(guarded_context, PAGES[name])
    page.keyboard.press("Tab")
    assert _active(page).startswith("a.skip-link")
    info = page.evaluate(FOCUS_JS)
    assert info["inView"], info
    assert not info["covered"], info
    page.keyboard.press("Enter")
    wait_for(page, "document.activeElement && document.activeElement.id === 'main'")
    guarded.assert_clean(page)


@pytest.mark.sample_only
def test_skip_link_keeps_the_view(guarded_context: Any) -> None:
    # "#main" is not a view: following the skip link must not reset the filters.
    guarded, page = open_page(guarded_context, f"/{FILTERS_HASH}", viewport=DESKTOP)
    list_state(page, "filters")
    count = page.locator("#count").inner_text()
    page.keyboard.press("Tab")
    assert _active(page).startswith("a.skip-link")
    page.keyboard.press("Enter")
    wait_for(page, "document.activeElement && document.activeElement.id === 'main'")
    page.evaluate(FRAMES_JS)
    assert page.evaluate("location.hash") == FILTERS_HASH
    assert page.locator("#count").inner_text() == count
    assert page.locator("#f-cat-sans-serif").is_checked()
    guarded.assert_clean(page)


@pytest.mark.sample_only
def test_phone_filters_button_works_by_keyboard(guarded_context: Any) -> None:
    guarded, page = open_page(guarded_context, f"/{FILTERS_HASH}", viewport=PHONE)
    toggle = page.locator("#f-toggle")
    assert toggle.is_visible()
    assert not page.locator("#f-more").is_visible()
    # Its name counts the filters that are on (three in FILTERS_HASH).
    assert "3" in toggle.inner_text()
    toggle.focus()
    page.keyboard.press("Enter")
    wait_for(page, "document.getElementById('f-toggle').ariaExpanded === 'true'")
    assert page.locator("#f-more").is_visible()
    page.keyboard.press("Tab")
    assert page.evaluate("document.getElementById('f-more').contains(document.activeElement)")
    page.keyboard.press("Shift+Tab")
    assert _active(page) == "button#f-toggle.filters-toggle"
    page.keyboard.press("Space")
    wait_for(page, "document.getElementById('f-toggle').ariaExpanded === 'false'")
    assert not page.locator("#f-more").is_visible()
    guarded.assert_clean(page)


@pytest.mark.sample_only
def test_arrow_keys_change_a_filter_and_keep_focus(guarded_context: Any) -> None:
    guarded, page = open_page(guarded_context, "/", viewport=DESKTOP)
    before = page.locator("#count").inner_text()
    page.locator("#f-cat-all").focus()
    page.keyboard.press("ArrowDown")
    wait_for(page, "location.hash === '#cat=sans-serif'")
    assert _active(page) == "input#f-cat-sans-serif"
    wait_for(page, f"document.getElementById('count').textContent !== {before!r}")
    # The new count is announced politely (4.1.3).
    wait_for(page, "document.getElementById('status').textContent.includes('Showing')")
    assert page.locator("#status").get_attribute("role") == "status"
    guarded.assert_clean(page)


@pytest.mark.sample_only
@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_details_open_by_keyboard_and_escape_returns_focus(guarded_context: Any, key: str) -> None:
    guarded, page = open_page(guarded_context, "/", viewport=PHONE)
    toggle = page.locator("#list > li.font .details-toggle").first
    panel_id = toggle.get_attribute("aria-controls")
    toggle.focus()
    page.keyboard.press(key)
    page.wait_for_selector(f"#{panel_id}[data-state='ready']")
    assert toggle.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Tab")
    assert page.evaluate(
        f"document.getElementById('{panel_id}').contains(document.activeElement)"
    ), _active(page)
    page.keyboard.press("Escape")
    wait_for(page, f"document.getElementById('{panel_id}').hidden")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate(f"document.activeElement.getAttribute('aria-controls') === '{panel_id}'")
    guarded.assert_clean(page)


@pytest.mark.sample_only
def test_clear_filters_in_the_no_results_state_keeps_focus_in_the_page(
    guarded_context: Any,
) -> None:
    guarded, page = open_page(guarded_context, f"/{NO_RESULTS_HASH}", viewport=PHONE)
    list_state(page, "no-results")
    page.locator("#no-results-clear").focus()
    page.keyboard.press("Enter")
    wait_for(page, "document.getElementById('no-results').hidden")
    active = _active(page)
    assert active not in {"body", "html", ""}, "focus fell back to the page body"
    assert page.evaluate("document.getElementById('main').contains(document.activeElement)")
    guarded.assert_clean(page)


@pytest.mark.sample_only
def test_type_your_own_text_by_keyboard(guarded_context: Any) -> None:
    guarded, page = open_page(guarded_context, "/", viewport=PHONE)
    font_id = page.evaluate(
        """async () => {
          const index = await globalThis.tff.list.index();
          const i = index.bits.findIndex((bits) => bits & 64);  // "Type your own text"
          return i < 0 ? null : index.ids[i];
        }"""
    )
    if font_id is None:
        pytest.skip("this build has no font files (run tff-site fetch-fonts first)")
    page.locator(f"#font-{font_id} .details-toggle").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(f"#details-{font_id}[data-state='ready']")
    page.locator(f"#details-{font_id} .typeown-load").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(f"#details-{font_id} .typeown-input")
    wait_for(page, "document.activeElement.classList.contains('typeown-input')")
    page.keyboard.press("End")
    page.keyboard.type(" 0123")
    typed = page.locator(f"#details-{font_id} .typeown-input").input_value()
    assert typed.endswith(" 0123")
    assert_axe_clean(page, "list, a font loaded to type your own text")
    guarded.assert_clean(page)
