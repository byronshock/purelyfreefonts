"""The interface font in the browser (AUTHORITY.md, "Interface font"; site/CONTRACT.md, section 6).

Runs in each engine given with ``--browser`` against the built site of tests/site/conftest.py.

- Arimo loads from this site's ``/assets/ui/`` and sets the interface text. The preload and
  the stylesheet name the same file, so it is fetched once.
- A late font moves nothing (``font-display: optional``). Each page is opened with the font's
  files held back until it has laid out in the fallback, then they are let through: once
  Arimo has loaded, every box on the first two screens is where it was, at the same size.
  (Arimo and Liberation Sans share their widths but not all their kerning, so a font swapped
  in late could move a line break; ``swap`` did, by up to 72 px.)
"""

import time
from typing import Any
from urllib.parse import urlsplit

import pytest
from tests.site.conftest import fetch_unencoded

PAGES = ["/", "/methodology/", "/about/", "/privacy/", "/missing"]
UI_PREFIX = "/assets/ui/"

# Every element's box, down to twice the viewport's height (rows further down skip rendering),
# to a quarter pixel. Classes are left out: a row's changes as its specimen arrives.
BOXES_JS = """
() => Array.from(document.querySelectorAll('body *')).flatMap((el) => {
  const r = el.getBoundingClientRect();
  if ((!r.width && !r.height) || r.top > 2 * innerHeight) return [];
  const q = (v) => Math.round(v * 4) / 4;
  return [[el.tagName, el.id, q(r.x), q(r.y), q(r.width), q(r.height)]];
})
"""
# The Arimo faces: [style, status] each, once every font the page needs has loaded.
FACES_JS = """
async () => {
  await document.fonts.ready;
  return Array.from(document.fonts)
    .filter((f) => f.family.replace(/"/g, '') === 'Arimo')
    .map((f) => [f.style, f.status]);
}
"""
READY_JS = "() => !document.getElementById('filters').hidden"
# The upright font file has come in. (Firefox leaves a face that came too late for
# font-display: optional "loading", so the FontFace can't tell.)
ARRIVED_JS = """
() => performance.getEntriesByType('resource')
  .some((e) => e.name.includes('/assets/ui/arimo.') && e.responseEnd > 0)
"""


def same_place(before: list[Any], after: list[Any]) -> bool:
    """The same element on the same line at the same height, and within a pixel sideways
    and in width. Chromium reshapes a few bold runs off screen once the font is in, by a
    quarter pixel, which nudges what follows on their line; a changed line break would move
    a box up or down or change a height."""
    (tag, id_, x, y, width, height), (tag2, id2, x2, y2, width2, height2) = before, after
    same = (tag, id_, y, height) == (tag2, id2, y2, height2)
    return same and abs(x - x2) <= 1 and abs(width - width2) <= 1


def open_page(guarded: Any, path: str) -> Any:
    """``path`` in a new page, once it has loaded (and the list is live)."""
    page = guarded.new_page()
    page.goto(path, wait_until="load")
    if path == "/":
        page.wait_for_function(READY_JS)
    return page


def test_arimo_sets_the_interface_text_and_is_fetched_once(guarded_context: Any) -> None:
    guarded = guarded_context(viewport={"width": 1280, "height": 900})
    page = open_page(guarded, "/")
    assert ["normal", "loaded"] in page.evaluate(FACES_JS)
    family = page.evaluate("getComputedStyle(document.body).fontFamily")
    assert family.replace('"', "").startswith("Arimo, Liberation Sans, Arial, "), family
    fetched = [urlsplit(url).path for url in guarded.requests]
    upright = [path for path in fetched if path.startswith(f"{UI_PREFIX}arimo.")]
    assert len(upright) == 1, fetched
    guarded.assert_clean(page)


def moved_boxes(guarded: Any, path: str, *, swap: bool = False) -> list[Any]:
    """Open ``path`` with the font held back, let it in once the page has laid out in the
    fallback, and return the boxes that moved. With ``swap``, the stylesheet's
    ``font-display: optional`` is changed to ``swap`` first."""
    held: list[Any] = []
    page = guarded.new_page()
    if swap:

        def to_swap(route: Any) -> None:
            response = fetch_unencoded(route)
            body = response.text().replace("font-display: optional;", "font-display: swap;")
            headers = {k: v for k, v in response.headers.items() if k != "content-length"}
            route.fulfill(response=response, headers=headers, body=body)

        page.route("**/assets/style.*.css", to_swap)
    page.route(lambda url: urlsplit(url).path.startswith(UI_PREFIX), lambda r: held.append(r))
    page.goto(path, wait_until="domcontentloaded")
    if path == "/":
        page.wait_for_function(READY_JS)
    deadline = time.monotonic() + 10
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert held, "the page never asked for the font"
    page.wait_for_timeout(500)  # well past optional's block period: the fallback is set
    before = page.evaluate(BOXES_JS)
    for route in held:
        route.continue_()
    page.wait_for_function(ARRIVED_JS)
    page.wait_for_timeout(300)
    after = page.evaluate(BOXES_JS)
    if not swap:
        guarded.assert_clean(page)
    return [(b, a) for b, a in zip(before, after, strict=False) if not same_place(b, a)]


@pytest.mark.parametrize("width", [375, 1280])
@pytest.mark.parametrize("path", PAGES)
def test_a_late_font_moves_nothing(guarded_context: Any, path: str, width: int) -> None:
    moved = moved_boxes(guarded_context(viewport={"width": width, "height": 900}), path)
    assert moved == [], f"{len(moved)} boxes moved, first {moved[:3]}"


def test_the_check_sees_a_swapped_font_move_text(guarded_context: Any) -> None:
    # The harness itself: with font-display: swap the late font is swapped in, and a line
    # on the methodology page breaks elsewhere.
    guarded = guarded_context(viewport={"width": 1280, "height": 900})
    assert moved_boxes(guarded, "/methodology/", swap=True) != []
