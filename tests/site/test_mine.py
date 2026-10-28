"""The hopper on the list (Milestone 3, slice 1, PR 4): ``site/js/55-mine.js`` and its wiring in
``30-filters-ui.js`` and ``90-main.js``, in each engine given with ``--browser`` (CI: Chromium
and Firefox), on the site of tests/site/conftest.py, through ``guarded_context``, so every test
also ends with no page error, no CSP violation, no cookie and nothing stored
(``Guarded.assert_clean``).

What a visitor does and sees (site/CONTRACT.md sections 4, 5 and 11; the owner's rulings of
2026-10-21 to 2026-10-23 in data/reviews/site/):

- A list **pasted** and checked, a file **chosen** (UTF-16 with a byte-order mark, as Windows
  PowerShell 5.1 writes one, and without one) or **dropped** anywhere on the page, which never
  opens it, leaves out of the list exactly the fonts that Python's reference
  (``tff_catalog.owned``) finds, on the match file the build wrote; the summary (naming the
  system that wrote the list), the near-matches it counts, the warning for a list cut off at
  the top, the count line and the announcement say so, and a second list replaces the first.
- **In sight**: what a dropped list brings ("Checking your list…", the summary, the Show menu
  and Forget, or its error) comes into view, the Filters panel opening if it is closed, and a
  ring round the window shows while a file is dragged over the page.
- **The Show menu**: fonts you don't have (the default), all fonts with yours marked, or only
  fonts you have, in **every view**: each rank, and the filters, against the same view with no
  list.
- **Forget this list**, which drops the summary's words too; **Clear filters**, which sets the
  menu back and keeps the list; the no-results text naming the menu, and the **phone Filters
  button** counting it, while it leaves some font out; **keyboard** use; the hopper's filter
  ahead of the hook's.
- **Copy**: the clipboard gets each command exactly as shown; where it is refused, the command
  is selected and the page says so.
- **Errors**: each refusal of the parser in plain words, and each failure of Data's two loaders
  as section 11 says (a reload, a "Try again" that works, focus kept, or a check that can't
  run here), the matcher's timeout among them, with the list left as it was; and while the
  list index has failed, the page's own note in place of a list.
- **Privacy**: the address, the history and the title never change, nothing is stored, a
  reload or Back restores no Show menu, and the previews stay paused once a list loads, under
  every Show mode (M3-D10 a). test_privacy.py counts the requests.

The lists are synthetic: the catalog's own family names, and names no catalog has.
"""

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import hopper_page

from tff_catalog import owned
from tff_site import data

UNKNOWN = ["Zzz Unknown Grotesk", "Qqq Test Serif"]
MODES = ("missing", "all", "only")
SYSTEMS = {
    "fc-list": "Linux",
    "fc-list-plain": "Linux",
    "macos-text": "macOS",
    "macos-json": "macOS",
    "windows-registry": "Windows",
}
HOW = {
    "missing": "left out of the list",
    "all": "marked in the list",
    "only": "and only those are shown",
}
MARK = "You have this font"
FORGOTTEN = "The list is forgotten."
NOTHING = "Paste the list first."
COPIED = "Copied."
COPY_REFUSED = "Copying isn\u2019t allowed here, so the command is selected: press Ctrl+C, or \u2318C on a Mac."
STALE = "The list was updated. Reload the page to check your fonts."
RELOAD = "The font check didn\u2019t load. Check your connection, then reload the page."
RETRY = "The font check didn\u2019t load. Check your connection, then try again."
BROKEN = "The font check can\u2019t run on this page, so nothing was left out of the list."
UNREADABLE = "That file couldn\u2019t be read. Try choosing it again."
REFUSALS = {
    "empty": "That list is empty.",
    "too_big": "That\u2019s too big for a list of font names: over 10 MB.",
    "binary": "That file isn\u2019t text. Choose the fonts.txt the command wrote.",
    "font_file": (
        "That\u2019s a font file, not a list of fonts. Choose the fonts.txt the command wrote."
    ),
    "html": "That\u2019s a web page, not a list of fonts.",
    "command": (
        "That\u2019s the command itself. Run it first, then give this page the fonts.txt it writes."
    ),
    "not_found": (
        "That says \u201ccommand not found\u201d, so the command didn\u2019t run. Check that you "
        "used the one for your system."
    ),
    "no_names": "No font names were found in that list.",
}

# Run before the page's own script: counts the list going live, its 'change' events (each
# redraw, the hopper's included), and every history write the page makes.
RECORDER_JS = """(() => {
  window.__tffReady = 0;
  window.__tffChanges = 0;
  window.__tffHistory = 0;
  for (const name of ['pushState', 'replaceState']) {
    const original = History.prototype[name];
    History.prototype[name] = function (...args) {
      window.__tffHistory += 1;
      return original.apply(this, args);
    };
  }
  document.addEventListener('tff:list-ready', (event) => {
    window.__tffReady += 1;
    event.detail.list.on('change', () => { window.__tffChanges += 1; });
  });
})();"""
# A clipboard that takes what it is given (window.__tffCopied), refuses it, or isn't there.
CLIPBOARD_JS = """(mode) => {
  window.__tffCopied = [];
  const clipboard = {
    writeText: (text) => {
      if (mode === 'refuse') return Promise.reject(new DOMException('No', 'NotAllowedError'));
      window.__tffCopied.push(text);
      return Promise.resolve();
    },
  };
  Object.defineProperty(Navigator.prototype, 'clipboard', {
    configurable: true,
    get: () => (mode === 'absent' ? undefined : clipboard),
  });
}"""
SHOWN_JS = "() => Array.from(document.querySelectorAll('#list > li.font'), (li) => li.dataset.id)"
MARKED_JS = """() => Array.from(document.querySelectorAll('#list > li.font'))
  .filter((li) => li.querySelector('.ext[data-filter="mine"] .ext-badge'))
  .map((li) => [li.dataset.id, li.querySelector('.ext[data-filter="mine"]').textContent])"""
WHERE_JS = "() => [location.href, history.length, document.title, window.__tffHistory]"
STORED_JS = "() => [localStorage.length, sessionStorage.length, document.cookie]"
PAUSED_JS = "() => globalThis.tff.list.specimens.paused"
CUT_OFF = "The top of your list seems to be missing: copy all of it, or use the file."
LOAD_FAILED = "Filters and search didn\u2019t load. Check your connection, then reload the page."
# Playwright's clock, as tests/site/test_owned_js.py runs it: installed at CLOCK_START, then
# paused an hour on once the list is live, so that only run_for moves it.
CLOCK_START = 1_767_225_600_000  # 2026-01-01T00:00:00Z, in ms
CLOCK_PAUSED = CLOCK_START + 3_600_000


# --------------------------------------------------------------------------- the reference


@pytest.fixture(scope="module")
def doc(site_data: Path) -> dict[str, Any]:
    return data.load(site_data)


@pytest.fixture(scope="module")
def match_doc(site_dir: Path) -> dict[str, Any]:
    """The match file the build wrote, as the page names it."""
    html = (site_dir / "index.html").read_text(encoding="utf-8")
    url = re.search(r'data-match="(/assets/match\.[0-9a-f]+\.json)"', html)
    assert url
    return json.loads((site_dir / url.group(1).lstrip("/")).read_bytes())


@pytest.fixture(scope="module")
def table(match_doc: dict[str, Any]) -> owned.Table:
    return owned.Table.from_document(match_doc)


@pytest.fixture(scope="module")
def families(doc: dict[str, Any]) -> list[str]:
    """The catalog's family names, in the list's own order."""
    return [font["family"] for font in data.server_order(doc)]


@pytest.fixture(scope="module")
def near_names(site_data: Path, table: owned.Table) -> list[str]:
    """Two or three names that are only near matches, each of another catalog font (a
    sibling, a related family or a bundle in the names file beside the catalog), owning none.
    (The real catalog's names file has two such on 2026-09-26.)"""
    path = site_data.with_name(site_data.name.replace("catalog-site", "names"))
    found: dict[str, str] = {}  # font id -> the name near it
    for family in json.loads(path.read_bytes())["families"]:
        for alias in family["names"] if family["in_catalog"] else []:
            if alias["relation"] in ("sibling", "related", "bundle") and len(found) < 3:
                result = owned.match(owned.parse(alias["name"] + "\n"), table).to_json()
                if len(result["near"]) == 1 and not result["owned"]:
                    found.setdefault(next(iter(result["near"])), alias["name"])
    assert len(found) >= 2, "the names file should have near matches"
    return list(found.values())


def linux_list(names: list[str]) -> str:
    """An ``fc-list : family | sort`` list: one line of two names, so it reads as one."""
    lines = [f"{names[0]},{names[0]} Regular", *names[1:], *UNKNOWN]
    return "\n".join(sorted(lines)) + "\n"


def plain_list(names: list[str]) -> str:
    """One name a line, as the Windows command writes it."""
    return "\n".join([*names, *UNKNOWN]) + "\n"


@pytest.fixture(scope="module")
def first(families: list[str]) -> str:
    return linux_list(families[0:6:2])


@pytest.fixture(scope="module")
def second(families: list[str]) -> str:
    return plain_list(families[1:7:2])


def reference(given: str | bytes, table: owned.Table) -> tuple[dict[str, Any], dict[str, Any]]:
    """What tff_catalog.owned makes of a list: the parsed list and the match, as JSON."""
    parsed = owned.parse(given)
    return parsed.to_json(), owned.match(parsed, table).to_json()


def summary(parsed: dict[str, Any], result: dict[str, Any], total: int, mode: str) -> str:
    """The summary the hopper shows for a list (55-mine.js's foundLine and haveLine)."""
    names = parsed["counts"]["entries"]
    system = SYSTEMS.get(parsed["format"])
    where = f"a {system} list" if system else "your list"
    found = f"Found {names:,} font {'name' if names == 1 else 'names'} in {where}."
    have = len(result["owned"])
    of = f"of the {total:,} {'font' if total == 1 else 'fonts'} here"
    if have == 0:
        return f"{found} You have none {of}."
    return f"{found} You have {have:,} {of}, {HOW[mode]}."


def near_line(result: dict[str, Any]) -> str:
    near = len(result["near"])
    if near == 0:
        return ""
    if near == 1:
        return "1 more font here is only a near match, so it isn\u2019t counted as yours."
    return f"{near:,} more fonts here are only near matches, so they aren\u2019t counted as yours."


# ------------------------------------------------------------------------------- the page


def open_list(
    make: Callable[..., Any], hash_: str = "", *, clock: bool = False, **kwargs: Any
) -> tuple[Any, Any]:
    """A guarded context and a page on the list, once the list is live (``clock``: on
    Playwright's clock, paused once it is)."""
    guarded = make(**kwargs)
    guarded.context.add_init_script(RECORDER_JS)
    page = guarded.new_page()
    hopper_page.arm(page)
    if clock:
        page.clock.install(time=CLOCK_START)
    page.goto(f"/{hash_}")
    page.wait_for_function("() => window.__tffReady >= 1")
    if clock:
        page.clock.pause_at(CLOCK_PAUSED)
    return guarded, page


def poll(page: Any, expression: str, what: str, timeout_s: float = 20.0) -> None:
    """Wait until ``expression`` (a function's source) is truthy in the page, polling on
    Playwright's own timer, which the page's clock doesn't hold."""
    deadline = time.monotonic() + timeout_s
    while not page.evaluate(expression):
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        page.wait_for_timeout(20)


def held_routes(page: Any, pattern: str) -> list[Any]:
    """Hold every request ``pattern`` matches, unanswered, in the list returned."""
    held: list[Any] = []
    page.route(pattern, lambda route: held.append(route))
    return held


def wait_held(page: Any, held: list[Any]) -> None:
    deadline = time.monotonic() + 20
    while not held:
        assert time.monotonic() < deadline, "no request was held"
        page.wait_for_timeout(20)


def shown(page: Any) -> list[str]:
    return page.evaluate(SHOWN_JS)


def text_of(page: Any, selector: str) -> str:
    return re.sub(r"\s+", " ", page.text_content(selector) or "").strip()


def status(page: Any) -> str:
    return page.text_content("#status") or ""


def count_line(shown_: int, total: int) -> str:
    return f"Showing {shown_:,} of {total:,} {'font' if total == 1 else 'fonts'}"


def changed(page: Any, action: Callable[[], Any]) -> None:
    """Do ``action`` and wait for the list's redraw."""
    before = page.evaluate("() => window.__tffChanges")
    action()
    page.wait_for_function(f"() => window.__tffChanges > {before}")


def show(page: Any, mode: str) -> None:
    """Choose a Show mode: the list redraws, and the previews stay paused (M3-D10 a)."""
    changed(page, lambda: page.select_option("#mine-show", mode))
    assert page.evaluate(PAUSED_JS) is True, f"previews went on under {mode}"


def universe(page: Any) -> int:
    """The M of "Showing N of M" in the current view."""
    found = re.fullmatch(r"Showing [\d,]+ of ([\d,]+) fonts?", text_of(page, "#count"))
    assert found, text_of(page, "#count")
    return int(found.group(1).replace(",", ""))


def assert_left_out(page: Any, before: list[str], have: set[str], mode: str) -> None:
    """The rows of a view, given the rows it showed with no list, under one Show mode."""
    keep = {
        "missing": [i for i in before if i not in have],
        "all": list(before),
        "only": [i for i in before if i in have],
    }[mode]
    assert shown(page) == keep, mode
    assert text_of(page, "#count") == count_line(len(keep), universe(page))
    marked = page.evaluate(MARKED_JS)
    assert marked == ([[i, MARK] for i in keep if i in have] if mode == "all" else [])


def assert_untouched(page: Any, where: list[Any]) -> None:
    """The address, the history and the title as they were, and nothing stored."""
    assert page.evaluate(WHERE_JS) == where
    assert page.evaluate(STORED_JS) == [0, 0, ""]


# ---------------------------------------------------------------------- a list, three ways


def test_a_pasted_list_leaves_out_the_fonts_it_has(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table, first: str
) -> None:
    guarded, page = open_list(guarded_context)
    where = page.evaluate(WHERE_JS)
    before = shown(page)
    total = len(doc["fonts"])
    assert page.is_hidden("#mine-result")
    assert page.evaluate(PAUSED_JS) is False
    parsed, result = reference(first, table)
    have = set(result["owned"])
    assert len(have) >= 2, "the list should own some of the catalog's fonts"
    hopper_page.paste(page, first)
    said = summary(parsed, result, total, "missing")
    assert parsed["format"] == "fc-list"
    assert said.startswith(f"Found {parsed['counts']['entries']} font names in a Linux list. ")
    assert text_of(page, "#mine-summary") == said
    assert text_of(page, "#mine-near") == near_line(result)
    assert page.is_hidden("#mine-near") == (near_line(result) == "")
    assert page.is_hidden("#mine-warning")
    assert page.input_value("#mine-show") == "missing"
    assert_left_out(page, before, have, "missing")
    line = count_line(len(before) - len(have & set(before)), universe(page))
    assert status(page) == " ".join(filter(None, [said, near_line(result), line]))
    assert page.input_value("#mine-paste") == "", "the paste box keeps no list"
    assert page.evaluate(PAUSED_JS) is True, "previews stay off once a list loads (M3-D10 a)"
    assert page.is_hidden("#mine-error")
    assert_untouched(page, where)
    guarded.assert_clean(page)


def test_a_chosen_file_is_read_as_bytes(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table, families: list[str]
) -> None:
    """A UTF-16 file with its byte-order mark, as Windows PowerShell 5.1's ``>`` writes one,
    and one in UTF-16BE without it: the parser decodes both, the second by its pattern of NULs,
    so the page reads the file as bytes (a file read as text would hold NULs, which the parser
    refuses as binary: Chromium's Blob.text() reads a byte-order mark, but not that pattern)."""
    guarded, page = open_list(guarded_context)
    where = page.evaluate(WHERE_JS)
    before = shown(page)
    text = linux_list(families[3:9:2]).replace("\n", "\r\n")
    plain = plain_list(families[4:10:2])
    for blob in (
        "\ufeff".encode("utf-16-le") + text.encode("utf-16-le"),
        plain.encode("utf-16-be"),
    ):
        parsed, result = reference(blob, table)
        assert parsed["error"] is None
        assert result["owned"]
        hopper_page.choose(page, blob)
        assert text_of(page, "#mine-summary") == summary(
            parsed, result, len(doc["fonts"]), "missing"
        )
        assert_left_out(page, before, set(result["owned"]), "missing")
        assert page.is_hidden("#mine-error")
        assert page.input_value("#mine-file") == "", "the file input lets the same file go again"
    assert_untouched(page, where)
    guarded.assert_clean(page)


def test_the_summary_names_the_system_that_wrote_the_list(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table, families: list[str]
) -> None:
    """Linux (above), macOS and Windows where the parser's format tells it, else "your list"."""
    guarded, page = open_list(guarded_context)
    total = len(doc["fonts"])
    names = families[2:8:3]
    lists = {
        "macOS": "".join(f"      Family: {name}\n" for name in names),
        "Windows": "".join(f"{name} (TrueType)\n" for name in names),
        "": plain_list(names),
    }
    for system, given in lists.items():
        parsed, result = reference(given, table)
        assert SYSTEMS.get(parsed["format"], "") == system
        hopper_page.paste(page, given)
        said = summary(parsed, result, total, "missing")
        where = f"a {system} list" if system else "your list"
        assert re.match(rf"Found [\d,]+ font names in {where}\. You have ", said), said
        assert text_of(page, "#mine-summary") == said
    guarded.assert_clean(page)


def test_a_file_dropped_anywhere_is_taken_and_never_opened(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table, first: str, second: str
) -> None:
    guarded, page = open_list(guarded_context)
    where = page.evaluate(WHERE_JS)
    url = page.url
    before = shown(page)
    total = len(doc["fonts"])
    assert page.evaluate(hopper_page.DRAG_TEXT_JS, "#list") is False, "text is the browser's"
    for selector, given in (("#list", first), ("#mine-paste", second), ("h1", first)):
        prevented = hopper_page.drop(page, given, selector=selector)
        assert prevented == {"enter": True, "over": True, "drop": True}, selector
        parsed, result = reference(given, table)
        assert text_of(page, "#mine-summary") == summary(parsed, result, total, "missing")
        assert_left_out(page, before, set(result["owned"]), "missing")
        assert page.input_value("#mine-paste") == "", "a dropped file goes in no box"
    assert page.url == url
    assert page.evaluate(DRAG_JS, ["dragover", None]) == [False, False], "a drop ends it"
    assert_untouched(page, where)
    guarded.assert_clean(page)


# A file dragged in the page, with ``type`` ("dragenter", "dragleave"...) on #list and
# ``related`` the element the drag moves to (null: out of the window), or "dragover" to only
# read: whether the window has its ring (html[data-dropping], drawn) and the zone its border.
DRAG_JS = """([type, related]) => {
  if (type !== 'dragover') {
    const transfer = new DataTransfer();
    transfer.items.add(new File(['Inter'], 'fonts.txt', { type: 'text/plain' }));
    const relatedTarget = related ? document.querySelector(related) : null;
    document.getElementById('list').dispatchEvent(new DragEvent(type,
      { bubbles: true, cancelable: true, dataTransfer: transfer, relatedTarget }));
  }
  const ring = getComputedStyle(document.body, '::after');
  const on = document.documentElement.hasAttribute('data-dropping');
  if (on !== (ring.position === 'fixed' && ring.pointerEvents === 'none')) return 'ring';
  return [on, document.getElementById('mine').classList.contains('is-dropping')];
}"""


@pytest.mark.parametrize("width", [900, 1440])
def test_a_file_dragged_over_the_page_rings_the_window_until_it_leaves(
    guarded_context: Any, width: int
) -> None:
    """The drop zone may be out of sight (behind Filters below 60rem, or below the fold), so
    the window gets a ring that takes no pointer events while a file is over the page; a move
    within the page keeps it, and leaving the window (a dragleave to no element) ends it."""
    guarded, page = open_list(guarded_context, viewport={"width": width, "height": 900})
    assert page.evaluate(DRAG_JS, ["dragover", None]) == [False, False]
    assert page.evaluate(DRAG_JS, ["dragenter", None]) == [True, True]
    assert page.evaluate(DRAG_JS, ["dragleave", "h1"]) == [True, True]
    assert page.evaluate(DRAG_JS, ["dragleave", None]) == [False, False]
    guarded.assert_clean(page)


@pytest.mark.parametrize(
    "viewport",
    [{"width": 900, "height": 900}, {"width": 1440, "height": 900}],
    ids=["panel-closed", "sidebar-below-the-fold"],
)
def test_what_a_dropped_list_brings_comes_into_sight(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table, first: str, viewport: Any
) -> None:
    """A file dropped on the list, the hopper out of sight (below 60rem behind the closed
    Filters button; from 60rem in the sidebar below the fold), with the page at its top:
    "Checking your list…", then the summary, the Show menu and Forget, or the error, come into
    view, the panel opening where it was closed."""
    guarded, page = open_list(guarded_context, viewport=viewport)
    narrow = viewport["width"] < 960
    assert page.evaluate("() => scrollY") == 0
    assert not hopper_page.in_view(page, "#mine-drop"), "the hopper starts out of sight"
    if narrow:
        assert page.get_attribute("#f-toggle", "aria-expanded") == "false"
    held = held_routes(page, "**/assets/match.*.json")
    said = status(page)
    page.evaluate(hopper_page.DROP_JS, ["#list", first, None, 0, "fonts.txt"])
    wait_held(page, held)
    assert hopper_page.in_view(page, "#mine-busy")
    assert text_of(page, "#mine-busy") == "Checking your list\u2026"
    if narrow:
        assert page.get_attribute("#f-toggle", "aria-expanded") == "true"
    held[0].continue_()
    hopper_page.wait_taken(page, said)
    parsed, result = reference(first, table)
    assert text_of(page, "#mine-summary") == summary(parsed, result, len(doc["fonts"]), "missing")
    for part in ("#mine-result", "#mine-summary", "#mine-show", "#mine-forget"):
        assert hopper_page.in_view(page, part), part
    # Back at the top, the panel closed again: a refused file's error comes into view too.
    page.evaluate("() => window.scrollTo(0, 0)")
    if narrow:
        page.click("#f-toggle")
        assert page.is_hidden("#mine")
    hopper_page.drop(page, PASTED_REFUSALS["command"])
    assert_error(page, REFUSALS["command"], retry=False)
    assert hopper_page.in_view(page, "#mine-error")
    guarded.assert_clean(page)


def test_a_second_list_replaces_the_first_and_forget_drops_it(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table, first: str, second: str
) -> None:
    guarded, page = open_list(guarded_context)
    where = page.evaluate(WHERE_JS)
    before = shown(page)
    total = len(doc["fonts"])
    hopper_page.paste(page, first)
    page.select_option("#mine-show", "only")
    parsed, result = reference(second, table)
    hopper_page.paste(page, second)
    # The second list keeps the visitor's Show menu.
    assert text_of(page, "#mine-summary") == summary(parsed, result, total, "only")
    assert_left_out(page, before, set(result["owned"]), "only")
    page.locator("#mine-forget").focus()
    changed(page, lambda: page.keyboard.press("Enter"))
    assert shown(page) == before
    assert page.is_hidden("#mine-result")
    assert page.input_value("#mine-show") == "missing"
    assert status(page) == f"{FORGOTTEN} {count_line(len(before), total)}"
    assert page.evaluate("() => document.activeElement.id") == "mine-choose"
    assert page.evaluate(MARKED_JS) == []
    # Nothing of the list stays in the page, hidden or not.
    for line in ("#mine-summary", "#mine-near", "#mine-warning"):
        assert page.text_content(line) == "", line
    # Previews that haven't loaded stay off until a reload (M3-D10 a), the list gone or not.
    assert page.evaluate(PAUSED_JS) is True
    assert_untouched(page, where)
    guarded.assert_clean(page)


def test_near_matches_are_counted_and_a_list_cut_off_at_the_top_is_warned_of(
    guarded_context: Any,
    doc: dict[str, Any],
    table: owned.Table,
    families: list[str],
    near_names: list[str],
) -> None:
    """Near-matches (a sibling, a related family, a bundle) are counted only, one or more, in
    the summary and the announcement; a sorted paste of 500 names or more that starts after C
    has lost its top (the parser's cut_off), and the summary says so."""
    guarded, page = open_list(guarded_context)
    total = len(doc["fonts"])
    lines = []
    for given in (plain_list(near_names[:1]), plain_list(near_names)):
        parsed, result = reference(given, table)
        line = near_line(result)
        hopper_page.paste(page, given)
        assert page.is_visible("#mine-near")
        assert text_of(page, "#mine-near") == line
        assert page.is_hidden("#mine-warning")
        said = summary(parsed, result, total, "missing")
        assert status(page).startswith(f"{said} {line} Showing ")
        lines.append(line)
    assert lines == [
        "1 more font here is only a near match, so it isn\u2019t counted as yours.",
        f"{len(near_names)} more fonts here are only near matches, so they aren\u2019t counted as "
        "yours.",
    ]
    late = [f for f in families if owned.search_key(f)[:1] >= "d"]
    cut = "\n".join(
        sorted([*late, *(f"Dzz Cut Test {i:04d}" for i in range(520))], key=owned.search_key)
    )
    parsed, result = reference(cut, table)
    assert "cut_off" in parsed["warnings"]
    hopper_page.paste(page, cut)
    assert page.is_visible("#mine-warning")
    assert text_of(page, "#mine-warning") == CUT_OFF
    assert page.is_hidden("#mine-near") == (near_line(result) == "")
    said = summary(parsed, result, total, "missing")
    assert status(page).startswith(" ".join(filter(None, [said, near_line(result), CUT_OFF])))
    guarded.assert_clean(page)


# ------------------------------------------------------------------- the Show menu and views


def test_the_show_menu_leaves_out_marks_or_keeps_only_the_fonts_a_list_has(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table, first: str
) -> None:
    guarded, page = open_list(guarded_context)
    before = shown(page)
    total = len(doc["fonts"])
    parsed, result = reference(first, table)
    have = set(result["owned"])
    hopper_page.paste(page, first)
    options = page.evaluate(
        "() => Array.from(document.querySelectorAll('#mine-show option'),"
        " (o) => [o.value, o.textContent])"
    )
    assert options == [
        ["missing", "Fonts you don't have"],
        ["all", "All fonts, yours marked"],
        ["only", "Only fonts you have"],
    ]
    for mode in ("all", "only", "missing", "only", "all"):
        show(page, mode)
        assert_left_out(page, before, have, mode)
        assert text_of(page, "#mine-summary") == summary(parsed, result, total, mode)
        assert status(page).replace("\u00a0", "") == text_of(page, "#count")
    guarded.assert_clean(page)


VIEW_HASHES = [
    "#cat=serif",
    "#cat=monospace",
    "#var=1",
    "#nerd=1",
    "#hide=limited,windows",
    "#hide=attr",
    "#q=sa",
    "#sort=name-desc",
    "#sort=rank-desc",
]


def test_the_fonts_a_list_has_leave_every_view(
    guarded_context: Any, table: owned.Table, families: list[str]
) -> None:
    """Each rank, and each filter, with each Show mode, against the same view with no list."""
    guarded, page = open_list(guarded_context)
    ranks = page.evaluate(
        "() => Array.from(document.querySelectorAll('#f-rank option'), (o) => o.value)"
    )
    hashes = [f"#rank={rank}" for rank in ranks[1:]] + VIEW_HASHES + ["#rank=coding&sort=name"]
    if "coding" not in ranks:
        hashes.remove("#rank=coding&sort=name")

    def go(hash_: str) -> None:
        changed(page, lambda: page.evaluate("(h) => { location.hash = h; }", hash_))

    without = {}
    for hash_ in hashes:
        go(hash_)
        without[hash_] = shown(page)
    # A third of the catalog, as a plain list: some of each view is left out.
    given = plain_list(families[::3])
    _, result = reference(given, table)
    have = set(result["owned"])
    hopper_page.paste(page, given)
    for hash_ in hashes:
        go(hash_)
        # Each mode once, each a change from the last.
        now = page.input_value("#mine-show")
        for mode in [m for m in MODES if m != now] + [now]:
            show(page, mode)
            assert_left_out(page, without[hash_], have, mode)
    guarded.assert_clean(page)


def test_clear_filters_sets_the_show_menu_back_and_keeps_the_list(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table, first: str
) -> None:
    guarded, page = open_list(guarded_context)
    before = shown(page)
    total = len(doc["fonts"])
    parsed, result = reference(first, table)
    have = set(result["owned"])
    hopper_page.paste(page, first)
    show(page, "only")
    changed(page, lambda: page.check("#f-var"))
    changed(page, lambda: page.click("#f-clear"))
    assert page.input_value("#mine-show") == "missing"
    assert not page.is_checked("#f-var")
    assert text_of(page, "#mine-summary") == summary(parsed, result, total, "missing")
    assert_left_out(page, before, have, "missing")
    # The menu alone: the hash's filters are clear already, so only the hopper changes.
    show(page, "all")
    changed(page, lambda: page.click("#f-clear"))
    assert page.input_value("#mine-show") == "missing"
    assert_left_out(page, before, have, "missing")
    assert status(page).replace("\u00a0", "") == text_of(page, "#count")
    # The no-results text names the menu, and its own Clear filters does the same.
    show(page, "only")
    changed(page, lambda: page.fill("#f-q", "zzqx no such font"))
    page.wait_for_selector("#no-results:not([hidden])")
    assert text_of(page, "#no-results-text") == (
        "No fonts match these filters together: Show: Only fonts you have and Search fonts: "
        "\u201czzqx no such font\u201d. Loosen one of them, or clear the filters."
    )
    changed(page, lambda: page.click("#no-results-clear"))
    assert page.input_value("#mine-show") == "missing"
    assert page.input_value("#f-q") == ""
    assert_left_out(page, before, have, "missing")
    guarded.assert_clean(page)


# Two hook filters beside the hopper's (section 10): one that renumbers, which View runs first
# (step 3), and one that keeps the numbering, which runs after the hopper's (step 6). Each
# records the fonts it is asked about, and marks every row.
PROBES_JS = """() => {
  window.__tffAsked = { renumbering: [], keeping: [] };
  for (const [id, affectsNumbering] of [['renumbering', true], ['keeping', false]]) {
    globalThis.tff.list.addFilter(id, {
      affectsNumbering,
      classify: (fontId) => {
        window.__tffAsked[id].push(fontId);
        return 'show';
      },
      note: () => ({ badge: id }),
    });
  }
}"""
ASKED_JS = """() => {
  const asked = window.__tffAsked;
  window.__tffAsked = { renumbering: [], keeping: [] };
  return asked;
}"""
SLOTS_JS = """(id) => Array.from(document.querySelectorAll(`#list > li.font[data-id="${id}"] .ext`),
  (slot) => slot.dataset.filter)"""


def test_the_hopper_s_filter_runs_ahead_of_the_hook_s_that_keep_the_numbering(
    guarded_context: Any, table: owned.Table, first: str
) -> None:
    """Main passes the hopper's filter ahead of Ext.filters() (site/CONTRACT.md section 5,
    View.compute): a hook filter that keeps the numbering is never asked about a font the
    hopper left out, one that renumbers runs first and is, and a row's slots put the
    hopper's first."""
    guarded, page = open_list(guarded_context)
    before = shown(page)
    have = set(reference(first, table)[1]["owned"])
    changed(page, lambda: page.evaluate(PROBES_JS))
    page.evaluate(ASKED_JS)
    hopper_page.paste(page, first)
    asked = page.evaluate(ASKED_JS)
    assert set(asked["keeping"]) == set(before) - have
    assert set(asked["renumbering"]) == set(before)
    assert_left_out(page, before, have, "missing")
    show(page, "all")
    mine = sorted(have & set(before))[0]
    assert page.evaluate(SLOTS_JS, mine) == ["mine", "renumbering", "keeping"]
    guarded.assert_clean(page)


def test_the_phone_filters_button_counts_the_hopper(
    guarded_context: Any, table: owned.Table, first: str
) -> None:
    guarded, page = open_list(guarded_context, viewport={"width": 375, "height": 812})
    page.click("#f-toggle")
    assert page.is_visible("#mine")
    assert page.is_visible(".mine-phone")
    assert text_of(page, ".mine-phone") == "Make the list on a computer."
    hopper_page.paste(page, first)
    counts = []
    for step in (
        lambda: None,
        lambda: show(page, "all"),
        lambda: show(page, "only"),
        lambda: changed(page, lambda: page.check("#f-var")),
        lambda: changed(page, lambda: page.click("#mine-forget")),
    ):
        step()
        counts.append(text_of(page, "#f-toggle"))
    assert counts == ["Filters (1)", "Filters", "Filters (1)", "Filters (2)", "Filters (1)"]
    guarded.assert_clean(page)


def test_a_list_that_has_none_of_the_fonts_counts_as_no_filter_until_it_leaves_some_out(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table
) -> None:
    """A list with none of the catalog's fonts leaves nothing out with "Fonts you don't
    have", so neither the phone button nor the no-results text names it; "Only fonts you
    have" leaves every font out, and both do."""
    guarded, page = open_list(guarded_context, viewport={"width": 375, "height": 812})
    page.click("#f-toggle")
    given = plain_list([])
    parsed, result = reference(given, table)
    assert result["owned"] == {}
    hopper_page.paste(page, given)
    assert text_of(page, "#mine-summary") == summary(parsed, result, len(doc["fonts"]), "missing")
    assert text_of(page, "#f-toggle") == "Filters"
    changed(page, lambda: page.fill("#f-q", "zzqx no such font"))
    page.wait_for_selector("#no-results:not([hidden])")
    assert text_of(page, "#no-results-text") == (
        "No fonts match this filter: Search fonts: \u201czzqx no such font\u201d. Loosen it, or "
        "clear the filters."
    )
    changed(page, lambda: page.fill("#f-q", ""))
    show(page, "only")
    assert shown(page) == []
    assert text_of(page, "#f-toggle") == "Filters (1)"
    assert text_of(page, "#no-results-text") == (
        "No fonts match this filter: Show: Only fonts you have. Loosen it, or clear the filters."
    )
    guarded.assert_clean(page)


# The stylesheet's media rules that show the phone line, as their conditions.
PHONE_RULES_JS = """() => {
  const found = [];
  for (const sheet of document.styleSheets) {
    for (const rule of sheet.cssRules) {
      if (rule.media && Array.from(rule.cssRules).some((r) => r.selectorText === '.mine-phone'
          && r.style.display === 'block')) found.push(rule.media.mediaText);
    }
  }
  return found;
}"""


@pytest.mark.parametrize(("width", "seen"), [(1280, False), (375, True)])
def test_the_phone_line_shows_on_phones_and_touch_screens_only(
    guarded_context: Any, width: int, seen: bool
) -> None:
    """A phone-sized screen or a coarse pointer (a touch screen) shows "Make the list on a
    computer"; a desktop with a mouse doesn't. Choosing a file and pasting stay."""
    guarded, page = open_list(guarded_context, viewport={"width": width, "height": 900})
    page.evaluate("() => document.getElementById('f-toggle').setAttribute('aria-expanded', 'true')")
    assert page.evaluate("() => matchMedia('(pointer: coarse)').matches") is False
    assert page.is_visible(".mine-phone") is seen
    assert page.is_visible("#mine-choose")
    assert page.is_visible("#mine-paste")
    rules = [re.sub(r"\s+", " ", m) for m in page.evaluate(PHONE_RULES_JS)]
    assert rules == ["(pointer: coarse), not all and (min-width: 40rem)"]
    guarded.assert_clean(page)


# ------------------------------------------------------------------------------ keyboard


def test_the_hopper_works_by_keyboard(
    guarded_context: Any, table: owned.Table, families: list[str], first: str
) -> None:
    guarded, page = open_list(guarded_context)
    before = shown(page)
    page.locator("#mine-choose").focus()
    blob = linux_list(families[1:5]).encode()
    hopper_page.choose(page, blob, key="Enter")
    assert_left_out(page, before, set(reference(blob, table)[1]["owned"]), "missing")
    # Choose file, then the paste box: the file input itself takes no focus.
    page.locator("#mine-choose").focus()
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.id") == "mine-paste"
    page.keyboard.insert_text(first)
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.id") == "mine-check"
    status_before = status(page)
    page.keyboard.press("Space")
    hopper_page.wait_taken(page, status_before)
    have = set(reference(first, table)[1]["owned"])
    assert_left_out(page, before, have, "missing")
    # The Show menu by arrow keys, then Tab to Forget.
    page.locator("#mine-show").focus()
    changed(page, lambda: page.keyboard.press("ArrowDown"))
    assert page.input_value("#mine-show") == "all"
    assert_left_out(page, before, have, "all")
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.id") == "mine-forget"
    changed(page, lambda: page.keyboard.press("Enter"))
    assert page.evaluate("() => document.activeElement.id") == "mine-choose"
    assert shown(page) == before
    # A command opens and copies by keyboard too.
    page.evaluate(CLIPBOARD_JS, "take")
    page.locator("#mine-os-linux > summary").focus()
    page.keyboard.press("Enter")
    assert page.evaluate("() => document.getElementById('mine-os-linux').open") is True
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.dataset.command") == "mine-cmd-linux"
    page.keyboard.press("Enter")
    page.wait_for_function("() => window.__tffCopied.length === 1")
    assert page.evaluate("() => window.__tffCopied") == [data.MINE_COMMANDS[0]["command"]]
    guarded.assert_clean(page)


# --------------------------------------------------------------------------------- copying


@pytest.mark.parametrize("command", data.MINE_COMMANDS, ids=lambda c: c["os"])
def test_copy_puts_each_command_on_the_clipboard_exactly(
    guarded_context: Any, command: dict[str, Any]
) -> None:
    guarded, page = open_list(guarded_context)
    page.evaluate(CLIPBOARD_JS, "take")
    page.click(f"#mine-os-{command['os']} > summary")
    shown_command = page.text_content(f"#mine-cmd-{command['os']}")
    assert shown_command == command["command"]
    page.click(f"#mine-os-{command['os']} .mine-copy")
    page.wait_for_function("() => window.__tffCopied.length === 1")
    assert page.evaluate("() => window.__tffCopied") == [command["command"]]
    note = f"#mine-os-{command['os']} .mine-copied"
    page.wait_for_selector(f"{note}:not([hidden])")
    assert text_of(page, note) == COPIED
    assert status(page) == COPIED
    guarded.assert_clean(page)


@pytest.mark.parametrize("clipboard", ["refuse", "absent"])
def test_where_the_clipboard_is_refused_the_command_is_selected(
    guarded_context: Any, clipboard: str
) -> None:
    guarded, page = open_list(guarded_context)
    page.evaluate(CLIPBOARD_JS, clipboard)
    command = data.MINE_COMMANDS[2]
    page.click(f"#mine-os-{command['os']} > summary")
    page.click(f"#mine-os-{command['os']} .mine-copy")
    note = f"#mine-os-{command['os']} .mine-copied"
    page.wait_for_selector(f"{note}:not([hidden])")
    assert text_of(page, note) == COPY_REFUSED
    assert status(page) == COPY_REFUSED
    assert page.evaluate("() => getSelection().toString()") == command["command"]
    assert page.evaluate("() => window.__tffCopied") == []
    guarded.assert_clean(page)


# ---------------------------------------------------------------------------------- errors


def assert_error(page: Any, text: str, *, retry: bool) -> None:
    assert text_of(page, "#mine-error-text") == text
    assert page.is_visible("#mine-error")
    assert page.is_visible("#mine-retry") == retry
    assert status(page).replace("\u00a0", "") == text


PASTED_REFUSALS = {
    "command": data.MINE_COMMANDS[0]["command"] + "\n",
    "not_found": "zsh: command not found: fc-list\n",
    "html": "<!doctype html><html><body><p>Fonts</p></body></html>\n",
    "empty": "\u200b\u200b\n",
    "no_names": "/usr/share/fonts/a.ttf\n/usr/share/fonts/b.otf\n",
}
# Records each Blob.prototype.slice call's arguments (window.__tffSlices).
SLICES_JS = """() => {
  window.__tffSlices = [];
  const slice = Blob.prototype.slice;
  Blob.prototype.slice = function recorded(...args) {
    window.__tffSlices.push(args);
    return slice.apply(this, args);
  };
}"""
FILE_REFUSALS = {
    "font_file": b"\x00\x01\x00\x00" + bytes(60),
    "binary": b"\x89PNG\r\n\x1a\n" + bytes(60),
}


@pytest.mark.parametrize("code", sorted(PASTED_REFUSALS) + sorted(FILE_REFUSALS) + ["too_big"])
def test_a_list_the_parser_refuses_gets_its_reason_in_plain_words(
    guarded_context: Any, table: owned.Table, code: str
) -> None:
    guarded, page = open_list(guarded_context)
    before = shown(page)
    if code in PASTED_REFUSALS:
        assert reference(PASTED_REFUSALS[code], table)[0]["error"] == code
        hopper_page.paste(page, PASTED_REFUSALS[code])
        assert page.input_value("#mine-paste") == PASTED_REFUSALS[code], "kept, to be mended"
    elif code in FILE_REFUSALS:
        assert reference(FILE_REFUSALS[code], table)[0]["error"] == code
        hopper_page.choose(page, FILE_REFUSALS[code], name="fonts.ttf")
    else:
        assert owned.MAX_BYTES == 10_000_000  # "over 10 MB"
        page.evaluate(SLICES_JS)
        hopper_page.drop(page, size=owned.MAX_BYTES + 2)
        # Read no further than one byte past the limit, which is enough to refuse it.
        assert page.evaluate("() => window.__tffSlices") == [[0, owned.MAX_BYTES + 1]]
    assert_error(page, REFUSALS[code], retry=False)
    assert page.is_hidden("#mine-result")
    assert shown(page) == before
    assert page.evaluate(PAUSED_JS) is False, "no list loaded, so previews go on"
    guarded.assert_clean(page)


def test_an_empty_paste_box_asks_for_a_list_and_loads_nothing(guarded_context: Any) -> None:
    guarded, page = open_list(guarded_context)
    page.fill("#mine-paste", "  \n\t ")
    page.click("#mine-check")
    page.wait_for_selector("#mine-error:not([hidden])")
    assert_error(page, NOTHING, retry=False)
    assert page.evaluate("() => 'tffMatcher' in globalThis") is False, "no list, no matcher"
    assert page.evaluate("() => document.scripts.length") == 1
    guarded.assert_clean(page)


def test_a_refused_list_leaves_the_last_one_as_it_was(
    guarded_context: Any, doc: dict[str, Any], table: owned.Table, first: str
) -> None:
    guarded, page = open_list(guarded_context)
    before = shown(page)
    parsed, result = reference(first, table)
    hopper_page.paste(page, first)
    hopper_page.paste(page, PASTED_REFUSALS["command"])
    assert_error(page, REFUSALS["command"], retry=False)
    assert text_of(page, "#mine-summary") == summary(parsed, result, len(doc["fonts"]), "missing")
    assert_left_out(page, before, set(result["owned"]), "missing")
    # The next list that works takes the error away.
    hopper_page.paste(page, first)
    assert page.is_hidden("#mine-error")
    guarded.assert_clean(page)


def _answer(kind: str, site_dir: Path) -> Callable[[Any], None]:
    """A route handler for the match file or the matcher module that fails as ``kind`` says."""

    def handle(route: Any) -> None:
        path = route.request.url.split("/assets/", 1)[1]
        body = (site_dir / "assets" / path).read_bytes()
        if kind == "404":
            route.fulfill(status=404, body="gone")
        elif kind == "500":
            route.fulfill(status=500, body="busy")
        elif kind == "abort":
            route.abort()
        elif kind == "not-json":
            route.fulfill(status=200, content_type="application/json", body="{not json")
        elif kind in ("format", "run_date", "malformed"):
            doc = json.loads(body)
            if kind == "format":
                doc["format"] = "tff-list"
            elif kind == "run_date":
                doc["run_date"] = "1999-01-01"
            else:
                doc["k"] = ["not", "an", "object"]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(doc))
        elif kind == "no-handoff":
            route.fulfill(status=200, content_type="text/javascript", body="const nothing = 1;\n")
        else:
            raise AssertionError(kind)

    return handle


# (what fails, how, the message, whether "Try again" shows)
FAILURES = [
    ("match", "404", STALE, False),
    ("match", "500", RETRY, True),
    ("match", "abort", RETRY, True),
    ("match", "not-json", RETRY, True),
    ("match", "format", BROKEN, False),
    ("match", "run_date", BROKEN, False),
    ("match", "malformed", BROKEN, False),
    ("matcher", "404", RELOAD, False),
    ("matcher", "abort", RELOAD, False),
    ("matcher", "no-handoff", RELOAD, False),
    ("both", "404", STALE, False),
]


@pytest.mark.parametrize(
    ("what", "kind", "message", "retry"), FAILURES, ids=[f"{w}-{k}" for w, k, _, _ in FAILURES]
)
def test_each_failure_to_load_says_what_section_11_says(
    guarded_context: Any,
    site_dir: Path,
    table: owned.Table,
    first: str,
    what: str,
    kind: str,
    message: str,
    retry: bool,
) -> None:
    guarded, page = open_list(guarded_context)
    before = shown(page)
    handler = _answer(kind, site_dir)
    patterns = {"match": ["**/assets/match.*.json"], "matcher": ["**/assets/matcher.*.js"]}
    patterns["both"] = patterns["match"] + patterns["matcher"]
    for pattern in patterns[what]:
        page.route(pattern, handler)
    hopper_page.paste(page, first)
    assert_error(page, message, retry=retry)
    assert page.is_hidden("#mine-result"), "no result it couldn't compute"
    assert shown(page) == before, "and no empty one in its place"
    assert page.input_value("#mine-paste") == first
    if retry:
        # Mended: "Try again" takes the same list, and asks only for what failed.
        for pattern in patterns[what]:
            page.unroute(pattern)
        try_again(page)
        assert_left_out(page, before, set(reference(first, table)[1]["owned"]), "missing")
    guarded.assert_clean(page)


def try_again(page: Any) -> None:
    """Press "Try again" by keyboard: once the list is taken, the error has gone and the focus,
    which was in it, is on Choose file (the hopper's first control that stays), not lost."""
    page.locator("#mine-retry").focus()
    said = status(page)
    page.keyboard.press("Enter")
    hopper_page.wait_taken(page, said)
    assert page.is_hidden("#mine-error")
    assert page.evaluate("() => document.activeElement.id") == "mine-choose"


def test_a_matcher_that_never_answers_offers_try_again(
    guarded_context: Any, table: owned.Table, first: str
) -> None:
    """Data.loadMatcher's 'timeout' (section 11): after MATCHER_TIMEOUT_MS on Playwright's clock
    the hopper offers "Try again", and once the script has arrived after all, it works."""
    guarded, page = open_list(guarded_context, clock=True)
    before = shown(page)
    held = held_routes(page, "**/assets/matcher.*.js")
    said = status(page)
    page.evaluate(hopper_page.PASTE_JS, first)
    wait_held(page, held)
    assert page.is_visible("#mine-busy")
    page.clock.run_for(29_999)
    assert page.is_visible("#mine-busy"), "still waiting"
    page.clock.run_for(2)
    hopper_page.wait_taken(page, said)
    assert_error(page, RETRY, retry=True)
    assert page.evaluate(PAUSED_JS) is False, "no list loaded, so previews go on"
    held[0].continue_()
    poll(page, "() => typeof globalThis.tffMatcher === 'object'", "the matcher's hand-off")
    try_again(page)
    assert_left_out(page, before, set(reference(first, table)[1]["owned"]), "missing")
    guarded.assert_clean(page)


class FailingIndex:
    """A route for the list index that fails it: at once, or once ``fail`` is called."""

    def __init__(self, *, now: bool) -> None:
        self.failing = now
        self.held: list[Any] = []

    def __call__(self, route: Any) -> None:
        if self.failing:
            route.abort()
        else:
            self.held.append(route)

    def fail(self) -> None:
        self.failing = True
        for route in self.held:
            route.abort()


def open_failing_index(make: Callable[..., Any], *, now: bool = True, **kwargs: Any) -> Any:
    """A guarded page on the list whose list index fails (``FailingIndex``), its script
    started."""
    guarded = make(**kwargs)
    page = guarded.new_page()
    index = FailingIndex(now=now)
    page.route("**/assets/list.*.json", index)
    page.goto("/")
    poll(page, "() => document.documentElement.hasAttribute('data-js')", "the page's script")
    return guarded, page, index


HIDDEN_JS = "(id) => document.getElementById(id).hidden"


def test_while_the_list_index_has_failed_a_dropped_list_gets_the_page_s_note(
    guarded_context: Any, first: str
) -> None:
    """The filters never showed, so the hopper can leave nothing out: a file dropped on the
    page is taken (never opened) and asks for nothing, and the note above the count is said
    again and brought into view, each time (test_privacy.py counts the requests)."""
    guarded, page, _ = open_failing_index(guarded_context, viewport={"width": 1440, "height": 900})
    page.wait_for_selector("#load-note")
    assert text_of(page, "#load-note") == LOAD_FAILED
    errors = list(guarded.errors)
    assert errors, "the index's failure reaches the console"
    for again in ("", "\u00a0"):
        page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        assert not hopper_page.in_view(page, "#load-note")
        said = status(page)
        prevented = page.evaluate(hopper_page.DROP_JS, ["#list", first, None, 0, "fonts.txt"])
        assert prevented == {"enter": True, "over": True, "drop": True}
        poll(
            page,
            f"() => document.getElementById('status').textContent !== {json.dumps(said)}",
            "the note",
        )
        assert status(page) == LOAD_FAILED + again
        assert hopper_page.in_view(page, "#load-note")
    assert page.evaluate(HIDDEN_JS, "mine-busy") is True
    assert page.evaluate("() => 'tffMatcher' in globalThis") is False, "nothing asked for"
    assert guarded.errors == errors
    guarded.errors.clear()
    guarded.assert_clean(page)


def test_a_list_on_its_way_when_the_list_index_fails_gets_the_page_s_note(
    guarded_context: Any, first: str
) -> None:
    """Data.loadMatch's 'index' (section 11): a list dropped while the list index is still on
    its way waits for it, and when it fails, the page's note answers the list too, with no
    "Try again" (the list can't go live without a reload)."""
    guarded, page, index = open_failing_index(guarded_context, now=False)
    wait_held(page, index.held)
    said = status(page)
    page.evaluate(hopper_page.DROP_JS, ["#list", first, None, 0, "fonts.txt"])
    poll(page, "() => !document.getElementById('mine-busy').hidden", "the hopper to start")
    poll(page, "() => typeof globalThis.tffMatcher === 'object'", "the matcher's hand-off")
    index.fail()
    hopper_page.wait_taken(page, said)
    assert status(page) == LOAD_FAILED
    for part in ("mine-error", "mine-result"):
        assert page.evaluate(HIDDEN_JS, part) is True, part
    assert guarded.errors, "the index's failure reaches the console"
    guarded.errors.clear()
    guarded.assert_clean(page)


def test_a_file_that_cannot_be_read_says_so(guarded_context: Any) -> None:
    guarded, page = open_list(guarded_context)
    page.evaluate(
        """() => {
          Blob.prototype.slice = function slice() {
            return { arrayBuffer: () => Promise.reject(new DOMException('gone', 'NotFoundError')) };
          };
        }"""
    )
    hopper_page.drop(page, "Inter\n")
    assert_error(page, UNREADABLE, retry=False)
    assert page.is_hidden("#mine-result")
    guarded.assert_clean(page)


# ------------------------------------------------------------------------- the whole visit


def test_a_reload_or_back_restores_no_list_and_no_show_menu(
    guarded_context: Any, first: str
) -> None:
    """Nothing is kept, a reload's or Back's included: the Show menu has autocomplete="off", so
    Firefox doesn't restore it on reload, nor either engine on Back, where the page is loaded
    again (a page the back/forward cache kept whole keeps its list in memory, and its menu
    with it)."""
    guarded, page = open_list(guarded_context)
    hopper_page.paste(page, first)
    show(page, "only")
    page.reload()
    page.wait_for_function("() => window.__tffReady >= 1")
    assert page.is_hidden("#mine-result")
    assert page.input_value("#mine-show") == "missing"
    assert page.evaluate(PAUSED_JS) is False
    hopper_page.paste(page, first)
    show(page, "only")
    page.goto("/about/")
    page.go_back()
    page.wait_for_function("() => window.__tffReady >= 1")
    kept = page.evaluate("() => !document.getElementById('mine-result').hidden")
    assert page.input_value("#mine-show") == ("only" if kept else "missing")
    assert page.evaluate(STORED_JS) == [0, 0, ""]
    guarded.assert_clean(page)


def test_nothing_from_a_list_reaches_the_address_history_title_or_storage(
    guarded_context: Any, first: str, second: str
) -> None:
    guarded, page = open_list(guarded_context)
    where = page.evaluate(WHERE_JS)
    hopper_page.paste(page, first)
    for mode in ("all", "only", "missing"):
        show(page, mode)
    hopper_page.drop(page, second)
    hopper_page.choose(page, first.encode())
    page.evaluate(CLIPBOARD_JS, "take")
    page.click("#mine-os-linux > summary")
    page.click("#mine-os-linux .mine-copy")
    hopper_page.paste(page, PASTED_REFUSALS["command"])
    changed(page, lambda: page.click("#mine-forget"))
    assert_untouched(page, where)
    assert page.evaluate("() => location.hash") == ""
    names = page.evaluate(
        "() => Array.from(document.querySelectorAll('#mine [name]'), (e) => e.id)"
    )
    assert names == [], "a named control could reach the address"
    guarded.assert_clean(page)
