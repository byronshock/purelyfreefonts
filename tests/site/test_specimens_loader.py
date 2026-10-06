"""Browser tests for the specimen loader.

The loader is ``site/js/45-specimens.js`` with ``site/css/35-specimens.css``: Milestone 2
step 5; site/CONTRACT.md sections 4, 6 and 10; design-m2 §2 "Specimens" and §6.

This module overrides ``site_dir`` and ``site_url`` with its own site, served by
``tff_site.serve``: a build of a copy of the catalog (``site_data``) in which every font with
a ``preview`` has its SVG, the file next to the data when there is one, otherwise one of the
committed sample specimens as a stand-in, with the hash rewritten to match. (While the sample
still carries placeholder hashes, its own build has no specimen at all.) The list is then long
enough that its lower rows start well below a 720 px screen.

Checked here:

- at load, once the page has finished starting and the network is quiet, no specimen is
  requested for a row more than 1000 px below the viewport; every row on screen or within most
  of the 600 px margin has its mask, and every mask set has been fetched (so nothing is left
  to fetch later, while paused);
- scrolling loads the rows that come near; rows a jump skipped over stay unloaded;
- ``tff.list.specimens.pause()`` stops new requests, and ``resume()`` loads only the rows still
  near the screen (M3-D10); rows that Render detaches and moves still load when near;
- a ``data-src`` outside ``/assets/specimens/`` is never loaded, even on a row on screen;
- the box is 64 px high (48 px on phones), unfilled until its mask is set, empty (never a solid
  bar) while its specimen downloads or if it never arrives, and the row keeps its height when
  the mask arrives;
- the outlines are visible in light, dark and forced colours (pixels from a screenshot);
- names: the specimen is decorative for screen readers (``aria-hidden``, an empty ``alt``;
  owner's site ruling of 2026-10-05, specimen_label_hidden), and the row's heading names the
  font; the fallback texts, and the no-script images (``loading="lazy"``, the same box,
  inverted in dark mode);
- the name shows once (owner ruling of 2026-09-29, name_once): a shown specimen draws it and
  the heading, still there for screen readers, isn't painted; a specimen that hasn't arrived,
  a missing one or none at all leaves the heading in view. The heading is centred on the box,
  over the drawn name (score_centred, 2026-10-05), and its text is transparent, so it paints
  nothing there, in forced colours too, while a selection of it (standing in for a
  find-in-page match) shows its highlight over the drawn name.
"""

import hashlib
import html
import json
import re
import threading
import time
import zlib
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from tests.site.conftest import fetch_unencoded

ROOT = Path(__file__).resolve().parents[2]
STANDINS = ROOT / "tests" / "fixtures" / "specimens"
FAKE_COMMIT = "0" * 40

WIDE = {"width": 1280, "height": 720}
PHONE = {"width": 375, "height": 812}
SPEC_PREFIX = "/assets/specimens/"
MARGIN_PX = 600  # the observer's rootMargin (site/CONTRACT.md section 5)
FAR_PX = 1000  # design-m2 §6: nothing is requested for rows this far below the viewport
LICENSE_FALLBACK = (
    "No preview: this font's license doesn't let us host its files. See it on {label}."
)
FAILED_FALLBACK = "Preview not available yet."

# Every row with a specimen box: where it is and whether its mask is set.
ROWS_JS = """() => Array.from(document.querySelectorAll('li.font')).flatMap((li) => {
  const span = li.querySelector('span.spec');
  if (!span) return [];
  const box = li.getBoundingClientRect();
  const spec = span.getBoundingClientRect();
  const style = getComputedStyle(span);
  return [{
    id: li.dataset.id,
    src: span.dataset.src,
    set: span.dataset.state === 'set',
    top: box.top,
    bottom: box.bottom,
    rowHeight: box.height,
    specHeight: spec.height,
    specWidth: spec.width,
    background: style.backgroundColor,
    color: style.color,
    mask: style.maskImage || style.webkitMaskImage || '',
  }];
})"""
# Each row's heading: what paints it (see ``painted``), and whether it is centred on the
# specimen box, if any (a heading that wraps taller than the box starts at its top instead).
HEADINGS_JS = """() => Array.from(document.querySelectorAll('li.font')).map((li) => {
  const title = li.querySelector('.font-title');
  const heading = title.querySelector('h3.font-name');
  const span = title.querySelector('span.spec');
  const h = heading.getBoundingClientRect();
  const b = (span || title).getBoundingClientRect();
  return { id: li.dataset.id, state: span ? span.dataset.state || null : 'none',
           drawn: title.classList.contains('is-drawn'), hasSpec: title.classList.contains('has-spec'),
           paint: [getComputedStyle(heading).opacity, getComputedStyle(heading).color],
           text: heading.textContent.trim(),
           overBox: (h.height <= b.height + 0.5
                     ? Math.abs(h.top + h.height / 2 - (b.top + b.height / 2)) < 1
                     : Math.abs(h.top - b.top) < 1) && Math.abs(h.left - b.left) < 0.5 };
})"""
PAINT_JS = "(e) => [getComputedStyle(e).opacity, getComputedStyle(e).color]"
SETTLE_JS = """() => new Promise((resolve) =>
  requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 60))))"""
SCROLL_JS = "(id) => document.getElementById('font-' + id).scrollIntoView({block: 'center'})"


# --- the site under test ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def loader_data(site_data: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of ``site_data`` in which every ``preview`` has an SVG whose hash matches."""
    standins = sorted(STANDINS.glob("*.svg"))
    assert standins, f"no stand-in specimens in {STANDINS}"
    doc = json.loads(site_data.read_bytes())
    dest = tmp_path_factory.mktemp("specimen-loader-data")
    for n, font in enumerate(doc["fonts"]):
        preview = font.get("preview")
        if not preview:
            continue
        own = site_data.parent / preview["path"]
        blob = own.read_bytes() if own.is_file() else standins[n % len(standins)].read_bytes()
        target = dest / preview["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        preview["sha256"] = hashlib.sha256(blob).hexdigest()
    path = dest / "catalog-site.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def loader_doc(loader_data: Path) -> dict[str, Any]:
    return json.loads(loader_data.read_bytes())


@pytest.fixture(scope="module")
def site_dir(loader_data: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """This module's site: a build of ``loader_data`` (overrides the session fixture)."""
    from tff_site import build

    out = tmp_path_factory.mktemp("specimen-loader-site")
    build.build(loader_data, out, commit=FAKE_COMMIT, allow_dirty=True, font_files=False)
    return out


@pytest.fixture(scope="module")
def site_url(site_dir: Path) -> Iterator[str]:
    """This module's site, served by ``tff-site serve`` with the production headers."""
    from tff_site import serve

    server = serve.make_server(site_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, name="specimen-loader", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --- helpers ---------------------------------------------------------------------------------


def settle(page: Any, rounds: int = 3) -> None:
    """Let layout, the intersection observer and any mask it sets run their course."""
    for _ in range(rounds):
        page.evaluate(SETTLE_JS)


def goto(guarded: Any, scheme: str | None = None) -> Any:
    """Open the list page in a new page, in the ``scheme`` colour scheme if one is given.

    Playwright's Firefox drops the context's ``color_scheme`` on a page served with
    ``Cross-Origin-Opener-Policy: same-origin`` (the process swap loses it; ``forced_colors``
    survives), so the scheme is applied again once the page is open, and checked.
    """
    page = guarded.new_page()
    page.goto("/")
    if scheme is not None:
        page.emulate_media(color_scheme=scheme)
        dark = page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
        assert dark == (scheme == "dark"), f"the {scheme} colour scheme isn't in effect"
    return page


def open_list(guarded: Any, *, hook: bool = False, scheme: str | None = None) -> Any:
    """Open the list and wait for the first masks (and for ``globalThis.tff`` with ``hook``)."""
    page = goto(guarded, scheme)
    page.wait_for_selector('span.spec[data-state="set"]', state="attached")
    if hook:
        page.wait_for_function("() => globalThis.tff !== undefined")
    settle(page)
    return page


def rows(page: Any) -> list[dict[str, Any]]:
    return page.evaluate(ROWS_JS)


def row(page: Any, font_id: str) -> dict[str, Any]:
    return next(r for r in rows(page) if r["id"] == font_id)


def requested(guarded: Any) -> list[str]:
    """Paths of the specimen requests so far, in order."""
    paths = (urlsplit(url).path for url in guarded.requests)
    return [path for path in paths if path.startswith(SPEC_PREFIX)]


def wait_for_requests(page: Any, guarded: Any, paths: set[str], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while missing := paths - set(requested(guarded)):
        if time.monotonic() > deadline:
            raise AssertionError(f"never requested: {sorted(missing)}")
        page.wait_for_timeout(50)


def wait_set(page: Any, font_id: str) -> None:
    page.wait_for_selector(f'#font-{font_id} span.spec[data-state="set"]', state="attached")


def far_rows(page: Any, viewport: dict[str, int]) -> list[dict[str, Any]]:
    return [r for r in rows(page) if r["top"] > viewport["height"] + FAR_PX]


def specimens(page: Any, method: str) -> Any:
    return page.evaluate(f"() => globalThis.tff.list.specimens.{method}")


def png_pixels(blob: bytes) -> list[tuple[int, int, int]]:
    """Decode an 8-bit, non-interlaced RGB or RGBA PNG (Playwright's screenshots) to RGB."""
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, header = 8, bytearray(), b""
    while pos < len(blob):
        length = int.from_bytes(blob[pos : pos + 4])
        kind, body = blob[pos + 4 : pos + 8], blob[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            idat += body
    width, height = int.from_bytes(header[:4]), int.from_bytes(header[4:8])
    depth, colour, interlace = header[8], header[9], header[12]
    assert (depth, interlace) == (8, 0), header
    assert colour in (2, 6), header
    bpp = 3 if colour == 2 else 4
    stride = width * bpp
    raw = zlib.decompress(bytes(idat))
    prev = bytearray(stride)
    pixels: list[tuple[int, int, int]] = []
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if kind == 1:
                line[i] = (line[i] + a) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + b) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + pred) & 0xFF
        pixels += [(line[i], line[i + 1], line[i + 2]) for i in range(0, stride, bpp)]
        prev = line
    return pixels


def painted(opacity: str, color: str) -> bool:
    """Whether text with this computed opacity and colour is painted: a shown specimen hides
    the heading with transparent text, never opacity (name_once; see the CSS)."""
    return opacity != "0" and not re.fullmatch(r"rgba\(\d+, \d+, \d+, 0\)|transparent", color)


def changed(one: list[tuple[int, int, int]], two: list[tuple[int, int, int]]) -> int:
    """How many pixels of two screenshots of one box differ by more than 16 levels in some
    channel; the outlines' antialiasing can shift by a level or two between paints."""
    return sum(
        1
        for p, q in zip(one, two, strict=True)
        if max(abs(a - b) for a, b in zip(p, q, strict=True)) > 16
    )


def luminance(rgb: tuple[int, int, int]) -> float:
    def channel(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one: tuple[int, int, int], two: tuple[int, int, int]) -> float:
    high, low = sorted((luminance(one), luminance(two)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def ink(pixels: list[tuple[int, int, int]]) -> tuple[tuple[int, int, int], float, float]:
    """Return the ink colour, its contrast with the background, and the share of ink pixels.

    The background is the commonest pixel; the ink is the pixel furthest from it; ink pixels
    are those at least half as far.
    """
    background = Counter(pixels).most_common(1)[0][0]

    def distance(p: tuple[int, int, int]) -> int:
        return max(abs(x - y) for x, y in zip(p, background, strict=True))

    darkest = max(pixels, key=distance)
    cut = max(1, distance(darkest) // 2)
    share = sum(1 for p in pixels if distance(p) >= cut) / len(pixels)
    return darkest, contrast(darkest, background), share


def outlines(page: Any, span: Any) -> list[tuple[int, int, int]]:
    """The specimen box's pixels once its outlines have been painted and two screenshots in a
    row agree (up to 5 s after its mask is set), or as they are by then."""
    deadline = time.monotonic() + 5
    last: list[tuple[int, int, int]] = []
    while True:
        pixels = png_pixels(span.screenshot())
        if ink(pixels)[2] > 0.01 and last and not changed(last, pixels):
            return pixels
        if time.monotonic() > deadline:
            return pixels
        last = pixels
        page.wait_for_timeout(100)


# --- loading ---------------------------------------------------------------------------------


@pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["wide", "phone"])
def test_nothing_is_requested_for_rows_far_below_the_viewport_at_load(
    guarded_context: Any, viewport: dict[str, int]
) -> None:
    guarded = guarded_context(viewport=viewport)
    # The hook comes after the first render; then wait for a quiet network and a little more,
    # so a loader that fetched the other rows later (in an idle callback, say) would show.
    page = open_list(guarded, hook=True)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(750)
    settle(page)
    height = viewport["height"]
    listed = rows(page)
    far = [r for r in listed if r["top"] > height + FAR_PX]
    assert len(far) >= 5, "the test list should reach well below the screen"
    assert [r["id"] for r in far if r["set"]] == []
    assert {r["src"] for r in far} & set(requested(guarded)) == set()

    # Rows on screen, and those most of the margin below it, have their masks.
    near = [r for r in listed if r["top"] < height + MARGIN_PX - 100]
    assert near
    assert [r["id"] for r in near if not r["set"]] == []
    # Every mask set is fetched now, not when its row is first painted (rows use
    # content-visibility): pause() relies on this, since a fetch put off until the row
    # scrolls into view could happen while paused (M3-D10).
    wait_for_requests(page, guarded, {r["src"] for r in listed if r["set"]})
    # Nothing else is requested: no no-script image, nothing without its mask set.
    assert set(requested(guarded)) <= {r["src"] for r in listed if r["set"]}
    assert len(requested(guarded)) == len(set(requested(guarded))), "a specimen requested twice"


def test_scrolling_requests_the_rows_that_come_near(guarded_context: Any) -> None:
    guarded = guarded_context(viewport=WIDE)
    page = open_list(guarded)
    at_load = {r["id"]: r for r in far_rows(page, WIDE)}
    target = list(at_load.values())[-1]

    page.evaluate(SCROLL_JS, target["id"])
    wait_set(page, target["id"])
    settle(page)
    listed = rows(page)
    on_screen = [r for r in listed if r["bottom"] > 0 and r["top"] < WIDE["height"]]
    assert target["id"] in {r["id"] for r in on_screen}
    assert [r["id"] for r in on_screen if not r["set"]] == []
    wait_for_requests(page, guarded, {r["src"] for r in on_screen})

    # Rows the jump went past, far from both scroll positions, were never near: not loaded.
    skipped = [r for r in listed if r["id"] in at_load and r["bottom"] < -FAR_PX]
    assert skipped, "the test list should be long enough to jump over some rows"
    assert [r["id"] for r in skipped if r["set"]] == []
    assert {r["src"] for r in skipped} & set(requested(guarded)) == set()
    guarded.assert_clean(page)


def test_pause_stops_requests_and_resume_loads_only_rows_still_near(guarded_context: Any) -> None:
    guarded = guarded_context(viewport=WIDE)
    page = open_list(guarded, hook=True)
    assert specimens(page, "paused") is False
    far = far_rows(page, WIDE)
    passed, target = far[0], far[-1]
    assert target["top"] - passed["bottom"] > 2 * (MARGIN_PX + WIDE["height"])

    specimens(page, "pause()")
    specimens(page, "pause()")  # pausing twice is the same as once: one resume() undoes it
    assert specimens(page, "paused") is True
    before = requested(guarded)
    page.evaluate(SCROLL_JS, passed["id"])
    settle(page)
    page.wait_for_timeout(300)
    assert requested(guarded) == before
    assert not row(page, passed["id"])["set"]

    # Scroll away and resume in one task, as Milestone 3 might: the observer hasn't yet seen
    # `passed` leave, and it must still not load.
    page.evaluate(
        "(id) => { document.getElementById('font-' + id).scrollIntoView({block: 'center'});"
        " globalThis.tff.list.specimens.resume(); }",
        target["id"],
    )
    assert specimens(page, "paused") is False
    wait_set(page, target["id"])
    wait_for_requests(page, guarded, {target["src"]})
    settle(page)
    assert not row(page, passed["id"])["set"]
    assert passed["src"] not in requested(guarded)
    specimens(page, "resume()")  # resuming twice changes nothing
    assert specimens(page, "paused") is False
    guarded.assert_clean(page)


def test_rows_that_render_moves_or_detaches_load_when_near(guarded_context: Any) -> None:
    guarded = guarded_context(viewport=WIDE)
    page = open_list(guarded, hook=True)
    far = far_rows(page, WIDE)
    moved, held, queued = far[-1], far[-2], far[-3]
    to_top = (
        "(id) => { document.getElementById('list').prepend(document.getElementById('font-' + id));"
        " window.scrollTo(0, 0); }"
    )

    # Moved to the top of the list, as Render does when a filter changes the order.
    page.evaluate(to_top, moved["id"])
    wait_set(page, moved["id"])
    wait_for_requests(page, guarded, {moved["src"]})

    # Detached into a fragment (a hidden row), then put back at the top.
    page.evaluate(
        "(id) => { window.__held = document.createDocumentFragment();"
        " window.__held.append(document.getElementById('font-' + id)); }",
        held["id"],
    )
    settle(page)
    assert held["src"] not in requested(guarded)
    page.evaluate("() => document.getElementById('list').prepend(window.__held)")
    wait_set(page, held["id"])
    wait_for_requests(page, guarded, {held["src"]})

    # Moved near while paused: not loaded. Hidden (detached) and resumed in one task, as a
    # Milestone 3 comparison might: still not loaded. Shown again near the top: loaded.
    specimens(page, "pause()")
    page.evaluate(to_top, queued["id"])
    settle(page)
    page.wait_for_timeout(200)
    assert not row(page, queued["id"])["set"]
    assert queued["src"] not in requested(guarded)
    page.evaluate(
        "(id) => { window.__held = document.createDocumentFragment();"
        " window.__held.append(document.getElementById('font-' + id));"
        " globalThis.tff.list.specimens.resume(); }",
        queued["id"],
    )
    settle(page)
    assert page.evaluate(
        "() => window.__held.querySelector('span.spec').dataset.state === undefined"
    ), "a row hidden before resume() loaded"
    page.evaluate("() => document.getElementById('list').prepend(window.__held)")
    wait_set(page, queued["id"])
    wait_for_requests(page, guarded, {queued["src"]})
    guarded.assert_clean(page)


# Values a data-src must never get into url("..."): another site, a path outside the
# specimens folder, and an attempt to close url("...") and open another.
UNSAFE_SOURCES = (
    "https://example.com/assets/specimens/x.svg",
    "//example.com/assets/specimens/x.svg",
    '/assets/specimens/x.svg"), url("/assets/other.svg',
    "/assets/specimens/../other.svg",
    "/assets/other.svg",
)


def test_a_source_outside_the_specimens_folder_is_never_loaded(guarded_context: Any) -> None:
    # Tall enough that rows after the unsafe ones come near the screen and load, even with
    # the real catalog's taller rows.
    tall = {"width": WIDE["width"], "height": 1600}
    guarded = guarded_context(viewport=tall)
    page = guarded.new_page()
    sources = iter(UNSAFE_SOURCES)

    def swap(match: re.Match[str]) -> str:
        unsafe = next(sources, None)
        return match[0] if unsafe is None else f'data-src="{html.escape(unsafe)}"'

    def rewrite(route: Any) -> None:
        # The list page with its first rows' data-src replaced; its headers (the CSP) as sent.
        response = fetch_unencoded(route)
        headers = {k: v for k, v in response.headers.items() if k != "content-length"}
        body = re.sub(r'data-src="[^"]*"', swap, response.text())
        route.fulfill(response=response, headers=headers, body=body)

    page.route(lambda url: urlsplit(url).path == "/", rewrite)
    page.goto("/")
    page.wait_for_selector('span.spec[data-state="set"]', state="attached")
    settle(page)
    listed = rows(page)
    unsafe = [r for r in listed if r["src"] in UNSAFE_SOURCES]
    assert len(unsafe) == len(UNSAFE_SOURCES)
    # These rows are near the screen, so each would have loaded with a proper source.
    assert all(r["top"] < tall["height"] + MARGIN_PX - 100 for r in unsafe)
    assert [r["id"] for r in unsafe if r["set"] or r["mask"] not in {"", "none"}] == []
    assert set(requested(guarded)) <= {r["src"] for r in listed if r["set"]}
    paths = {urlsplit(url).path for url in guarded.requests}
    assert paths.isdisjoint({"/assets/other.svg", "/assets/specimens/x.svg"})
    guarded.assert_clean(page)


# --- the box ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("viewport", "box"), [(WIDE, 64), (PHONE, 48)], ids=["wide", "phone"])
def test_the_box_has_a_fixed_height_and_the_row_keeps_its_height(
    guarded_context: Any, viewport: dict[str, int], box: int
) -> None:
    guarded = guarded_context(viewport=viewport)
    page = open_list(guarded, hook=True)
    specimens(page, "pause()")
    target = far_rows(page, viewport)[-1]
    page.evaluate(SCROLL_JS, target["id"])
    settle(page)
    before = row(page, target["id"])
    assert not before["set"]
    assert before["specHeight"] == box
    assert before["specWidth"] > 0
    assert before["background"] in {"transparent", "rgba(0, 0, 0, 0)"}, "an unmasked box is filled"

    specimens(page, "resume()")
    wait_set(page, target["id"])
    wait_for_requests(page, guarded, {target["src"]})
    settle(page)
    after = row(page, target["id"])
    assert after["specHeight"] == box
    assert after["rowHeight"] == before["rowHeight"]
    assert after["background"] == after["color"], "the mask is filled with the text colour"
    assert target["src"] in after["mask"]


@pytest.mark.parametrize(("failure", "state"), [("pending", "loading"), ("missing", "failed")])
def test_a_specimen_that_has_not_arrived_leaves_the_box_empty_and_the_name_in_view(
    guarded_context: Any, failure: str, state: str
) -> None:
    """The mask is set only once its file has loaded, so the box shows nothing until then, and
    the heading stays in view (owner ruling of 2026-09-29, name_once).

    A slow network holds a specimen back (``pending``); a tab left open across a deploy can
    ask for one that is gone (``missing``). Neither may show a solid bar or lose the name.
    """
    guarded = guarded_context(viewport=WIDE)
    page = guarded.new_page()
    held: list[Any] = []
    if failure == "pending":
        page.route(f"**{SPEC_PREFIX}*", lambda route: held.append(route))
    else:
        page.route(f"**{SPEC_PREFIX}*", lambda route: route.fulfill(status=404, body="Not found"))
    try:
        page.goto("/", wait_until="domcontentloaded")  # "load" waits for the held images
        page.wait_for_selector(f'span.spec[data-state="{state}"]', state="attached")
        settle(page)
        first = next(h for h in page.evaluate(HEADINGS_JS) if h["state"] == state)
        src = row(page, first["id"])["src"]
        wait_for_requests(page, guarded, {src})
        settle(page)
        box = row(page, first["id"])
        assert not box["set"]
        assert not box["mask"].startswith("url")
        assert box["background"] in {"transparent", "rgba(0, 0, 0, 0)"}, "the box is filled"
        # The heading lies over the box: set it aside (CSSOM) to see the box alone.
        heading = page.locator(f"#font-{first['id']} h3.font-name")
        heading.evaluate("(h) => h.style.setProperty('visibility', 'hidden')")
        pixels = png_pixels(page.locator(f"#font-{first['id']} span.spec").screenshot())
        heading.evaluate("(h) => h.style.removeProperty('visibility')")
        assert len(set(pixels)) == 1, "the box shows something before its specimen arrived"
        assert not first["drawn"]
        assert painted(*first["paint"]), "the name is hidden with no specimen to draw it"
        assert page.locator(f"#font-{first['id']} h3.font-name").is_visible()
    finally:
        page.unroute_all(behavior="ignoreErrors")  # also lets the held requests go


def test_a_shown_specimen_is_the_visible_name(
    guarded_context: Any, loader_doc: dict[str, Any]
) -> None:
    """Once its specimen shows, a row's heading isn't painted but is still a heading; a row
    without a specimen, or whose specimen hasn't loaded yet, shows its heading (owner ruling
    of 2026-09-29, name_once). The heading is centred on the specimen box, over the drawn name
    (score_centred, 2026-10-05), and shares its cell, so hiding it moves nothing."""
    guarded = guarded_context(viewport=WIDE)
    page = open_list(guarded)
    families = {f["id"]: f["family"] for f in loader_doc["fonts"]}
    headings = page.evaluate(HEADINGS_JS)
    drawn = [h for h in headings if h["state"] == "set"]
    assert drawn, "no specimen has shown"
    for h in headings:
        assert h["text"] == families[h["id"]]
        assert h["hasSpec"] == (h["state"] != "none"), h
        if h["state"] == "set":
            assert h["drawn"], h
            assert h["paint"][0] == "1", h  # transparent text, not opacity: see below
            assert not painted(*h["paint"]), h
        else:
            assert not h["drawn"], h
            assert painted(*h["paint"]), h
        if h["hasSpec"]:
            assert h["overBox"], h
    family = families[drawn[0]["id"]]
    assert page.get_by_role("heading", name=family, exact=True).count() == 1


@pytest.mark.parametrize("viewport", [WIDE, PHONE], ids=["wide", "phone"])
def test_a_selection_of_the_hidden_heading_shows_over_the_drawn_name(
    guarded_context: Any, viewport: dict[str, int]
) -> None:
    """The hidden heading lies over the drawn name so that find-in-page highlights it there
    (owner's site ruling of 2026-10-05, score_centred). Its text is transparent rather than
    at opacity 0, which would hide the highlight with the text. The browser's find bar can't
    be driven from a test; a selection of the heading's text, painted the same way, stands
    in: it must change the pixels over the box, and nothing else may."""
    guarded = guarded_context(viewport=viewport)
    page = open_list(guarded)
    first = next(h for h in page.evaluate(HEADINGS_JS) if h["drawn"])
    assert first["overBox"], first
    span = page.locator(f"#font-{first['id']} span.spec")
    heading = page.locator(f"#font-{first['id']} h3.font-name")
    before = outlines(page, span)
    heading.evaluate("(h) => getSelection().selectAllChildren(h)")
    assert page.evaluate("() => getSelection().toString().trim()") == first["text"]
    selected = png_pixels(span.screenshot())
    page.evaluate("() => getSelection().removeAllRanges()")
    after = png_pixels(span.screenshot())
    # Once the selection is gone, the box looks as it did: a CI runner repaints a few edge
    # pixels differently (10 of about 25,000 seen), so up to 0.1% may differ, against the
    # more than 1% a selection changes.
    stray = changed(before, after)
    assert stray <= 0.001 * len(before), f"the box changed: {stray} of {len(before)} pixels"
    shown = changed(before, selected)
    assert shown > 0.01 * len(before), f"the selection changed {shown} of {len(before)} pixels"
    guarded.assert_clean(page)


# --- what people see and hear ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("scheme", "forced"),
    [("light", "none"), ("dark", "none"), ("light", "active"), ("dark", "active")],
    ids=["light", "dark", "forced-light", "forced-dark"],
)
def test_specimens_are_visible_in_every_theme(
    guarded_context: Any, scheme: str, forced: str
) -> None:
    guarded = guarded_context(viewport=WIDE, color_scheme=scheme, forced_colors=forced)
    page = open_list(guarded, scheme=scheme)
    if forced == "active" and not page.evaluate("matchMedia('(forced-colors: active)').matches"):
        pytest.skip("this browser doesn't emulate forced colours")
    first = next(r for r in rows(page) if r["set"])
    wait_for_requests(page, guarded, {first["src"]})
    span = page.locator(f"#font-{first['id']} span.spec")
    shown = outlines(page, span)
    colour, ratio, share = ink(shown)
    assert share > 0.01, "no outlines in the specimen box"
    assert ratio >= 4.5, f"outlines {colour} have contrast {ratio:.2f} with the background"
    # The hidden heading lies over the drawn name and must add nothing to it; forced colours
    # would repaint its transparent text but for forced-color-adjust: none.
    heading = page.locator(f"#font-{first['id']} h3.font-name")
    heading.evaluate("(h) => h.style.setProperty('visibility', 'hidden')")
    alone = png_pixels(span.screenshot())
    heading.evaluate("(h) => h.style.removeProperty('visibility')")
    assert not changed(shown, alone), "the hidden heading is painted over the drawn name"
    if forced == "active":
        style = page.evaluate(
            "() => { const s = getComputedStyle(document.querySelector('span.spec'));"
            " return [s.forcedColorAdjust, s.backgroundColor,"
            " getComputedStyle(document.body).color]; }"
        )
        adjust, fill, canvas_text = style
        assert adjust == "none"
        assert fill == canvas_text
    guarded.assert_clean(page)


def test_each_specimen_is_hidden_from_screen_readers(
    guarded_context: Any, loader_doc: dict[str, Any]
) -> None:
    """The owner's site ruling of 2026-10-05 (specimen_label_hidden): the specimen shows only
    the name, which the heading beside it already says, so it is decorative, with no role or
    name, before and after the loader sets its mask; the heading names the font."""
    guarded = guarded_context(viewport=WIDE)
    page = open_list(guarded)
    found = page.evaluate(
        """() => Array.from(document.querySelectorAll('li.font')).flatMap((li) => {
          const span = li.querySelector('span.spec');
          if (!span) return [];
          return [{ id: li.dataset.id, state: span.dataset.state || null,
                    hidden: span.getAttribute('aria-hidden'), role: span.getAttribute('role'),
                    label: span.getAttribute('aria-label'),
                    heading: li.querySelector('h3.font-name').textContent.trim() }];
        })"""
    )
    families = {f["id"]: f["family"] for f in loader_doc["fonts"] if f["preview"]}
    assert {item["id"] for item in found} == set(families)
    assert any(item["state"] == "set" for item in found), "no mask was set"
    for item in found:
        family = families[item["id"]]
        assert item["hidden"] == "true", item
        assert item["role"] is None, item
        assert item["label"] is None, item
        assert item["heading"] == family, "the heading names the font"
    assert page.locator("#list [role=img]").count() == 0
    family = families[found[0]["id"]]
    assert page.get_by_role("heading", name=family, exact=True).count() == 1
    assert page.get_by_role("img", name=family).count() == 0


def test_fonts_without_a_specimen_show_the_fallback_text(
    guarded_context: Any, loader_doc: dict[str, Any]
) -> None:
    from tff_site import data

    guarded = guarded_context(viewport=WIDE)
    page = guarded.new_page()
    page.goto("/")
    without = [f for f in loader_doc["fonts"] if not f["preview"]]
    kinds = {"failed" if f["preview_ok"] else "license" for f in without}
    if loader_doc.get("synthetic"):
        assert {"license", "failed"} <= kinds  # the sample holds both kinds
    elif not without:
        pytest.skip("every font in this data has a specimen")
    for font in without:
        spec = page.locator(f"#font-{font['id']} .font-spec")
        assert spec.locator("span.spec, img").count() == 0
        heading = page.locator(f"#font-{font['id']} h3.font-name")
        assert painted(*heading.evaluate(PAINT_JS))
        # textContent: innerText is empty in a row content-visibility skips.
        text = " ".join((spec.locator("p.spec-fallback").text_content() or "").split())
        if font["preview_ok"]:
            assert text == FAILED_FALLBACK
        else:
            primary = font["links"]["primary"]
            assert text == LICENSE_FALLBACK.format(label=data.destination_name(primary))
            assert spec.locator("a").get_attribute("href") == primary["url"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_without_javascript_the_no_script_images_show(
    guarded_context: Any, loader_doc: dict[str, Any], scheme: str
) -> None:
    """The no-script images carry ``loading="lazy"``, but browsers load them all at once.

    The HTML standard's "will lazy load element steps" return false when scripting is
    disabled (an anti-tracking rule), and Chromium and Firefox follow it, so a row's image
    far below the screen is fetched too. Nothing here asserts that it isn't.
    """
    # A 375 x 812 phone (iPhone X class): the first row sits below the lead, the no-script
    # note and the count.
    viewport = {"width": 375, "height": 812}
    guarded = guarded_context(java_script_enabled=False, viewport=viewport, color_scheme=scheme)
    page = goto(guarded, scheme)
    page.wait_for_load_state("load")
    page.wait_for_timeout(500)
    images = page.evaluate(
        """() => Array.from(document.querySelectorAll('li.font img.spec-img')).map((img) => {
          const li = img.closest('li.font');
          const span = li.querySelector('span.spec');
          return { id: li.dataset.id, src: img.getAttribute('src'), alt: img.alt,
                   loading: img.getAttribute('loading'), width: img.getAttribute('width'),
                   height: img.getAttribute('height'), shown: img.getBoundingClientRect().height,
                   loaded: img.complete && img.naturalWidth > 0,
                   top: li.getBoundingClientRect().top,
                   filter: getComputedStyle(img).filter,
                   spanShown: span !== null && getComputedStyle(span).display !== 'none',
                   heading: [getComputedStyle(li.querySelector('h3.font-name')).opacity,
                             getComputedStyle(li.querySelector('h3.font-name')).color] };
        })"""
    )
    # The image draws the name, so the heading isn't painted (name_once); a row without an
    # image keeps its heading in view.
    fallbacks = page.evaluate(
        """() => Array.from(document.querySelectorAll('li.font'))
          .filter((li) => !li.querySelector('.font-title.has-spec'))
          .map((li) => [getComputedStyle(li.querySelector('h3.font-name')).opacity,
                        getComputedStyle(li.querySelector('h3.font-name')).color])"""
    )
    families = {f["id"]: f["family"] for f in loader_doc["fonts"] if f["preview"]}
    # The sample has rows without an image; in the real catalog every font has one.
    assert len(fallbacks) == len(loader_doc["fonts"]) - len(families)
    assert all(painted(*paint) for paint in fallbacks)
    assert {i["id"] for i in images} == set(families)
    for image in images:
        assert image["loading"] == "lazy"
        assert image["alt"] == ""  # decorative: the heading names the font
        assert int(image["width"]) > 0
        assert int(image["height"]) == 64
        assert image["filter"] == ("invert(1)" if scheme == "dark" else "none")
        assert not image["spanShown"], "the empty mask box shows without JavaScript"
        assert not painted(*image["heading"]), "the name shows twice without JavaScript"
    first = images[0]
    assert first["top"] < viewport["height"]
    assert first["loaded"]
    assert first["shown"] == 48  # the phone box
    # guards.js can't run without JavaScript; the rest of assert_clean still applies.
    assert guarded.blocked == []
    assert guarded.csp_messages() == []
    assert guarded.context.cookies() == []
