"""Font details (Milestone 2 step 4): site/js/40-details.js, site/css/30-details.css and
``tff-site linkcheck`` (src/tff_site/linkcheck.py).

- **Part checks** (no browser): both parts pass the build's lint, and the script never
  builds nodes from HTML or writes a style attribute.
- **Pure helpers** (browser, blank page): the part is loaded on its own, after 00-core.js, as
  an inline module, like tests/site/test_keys_js.py does, so the hash and destination-name
  helpers are tested against the Python reference (``tff_site.data.destination_name``).
- **The panel on the built site** (browser, ``guarded_context``): one panel at a time, the
  ``#font=`` link, Back, focus return on Esc and Close, "Type your own text" loading the
  font only on request, the stale-details message, 320 px reflow, axe on an open panel, and
  no foreign request, cookie, storage or CSP violation throughout. Once the list is live,
  the font key goes through State: Back into a view that hid the font, the hook's
  ``setState({font})``, a search typed just before opening, and the terms ruling's hidden
  source ranks each have a test.
- **The owner's ten** (``-k owner_ten``): every field of ten fonts' panels against the site
  data. The ten are ``TFF_OWNER_TEN`` (comma-separated ids) if set, else Claude's pick: the
  top 5 overall and 5 edge cases (attribution required, not redistributable, held by the
  gate, preinstalled, pulled in by a package). Run it on real data with
  ``TFF_SITE_DATA=build/catalog-site.json TFF_OWNER_TEN=a,b,…``.
- **linkcheck** (no network): a mock transport checks rate limiting per host, redirects,
  failures and the CLI; one ``network`` test checks a real font's links.
"""

import json
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from tests.site.conftest import fetch_unencoded

from tff_site import assets, cli, data, linkcheck

ROOT = Path(__file__).resolve().parents[2]
JS_PART = ROOT / "site" / "js" / "40-details.js"
CSS_PART = ROOT / "site" / "css" / "30-details.css"
CORE_PART = ROOT / "site" / "js" / "00-core.js"
SAMPLE = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
SITE_DATA = Path(os.environ.get("TFF_SITE_DATA", SAMPLE))

# The wording site/js/40-details.js shows (owner approval: Milestone 2 step 4).
REDIST_YES = (
    "Yes. You may pass the font files on, for example inside an app or on your own website."
)
REDIST_NO = (
    "No. You may use the font for anything, but not pass its files on: point people to the "
    "official download instead."
)
GATE_LINE = "Held out of the top 100: only one group of sources has evidence for it."
STALE_LINE = "The list was updated. Reload to see details."
REPORT_LINK = "Report a problem with this font on GitHub"  # the link names its destination
SURVEY_CAPTIONS = {"desktop": "Desktop sources", "project": "Project sources"}
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def _load_doc(path: Path) -> dict[str, Any]:
    return data.load(path)


def owner_ten(doc: dict[str, Any]) -> list[str]:
    """``TFF_OWNER_TEN``, or the top 5 overall plus the first font of each edge case."""
    given = os.environ.get("TFF_OWNER_TEN", "")
    if given.strip():
        return [i.strip() for i in given.split(",") if i.strip()]
    fonts = data.server_order(doc)
    picked = [f["id"] for f in fonts[:5]]
    edge_cases = (
        lambda f: f["license"]["attribution_required"],
        lambda f: not f["license"]["redistributable"],
        lambda f: any(entry["gate_held"] for entry in f["ranks"].values()),
        lambda f: bool(f["preinstalled_on"]),
        lambda f: bool(f["pulled_in_by"]),
    )
    for case in edge_cases:
        match = next((f["id"] for f in fonts if case(f) and f["id"] not in picked), None)
        if match:
            picked.append(match)
    return picked


DOC = _load_doc(SITE_DATA)
FONTS = {f["id"]: f for f in DOC["fonts"]}
OWNER_TEN = owner_ten(DOC)


# ------------------------------------------------------------------------ part checks


def test_parts_pass_the_build_lint():
    assert assets.lint_js_part(JS_PART.name, JS_PART.read_text(encoding="utf-8")) == []
    assert assets.lint_css_part(CSS_PART.name, CSS_PART.read_text(encoding="utf-8")) == []


def test_script_builds_nodes_without_html_or_style_attributes():
    source = JS_PART.read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("//"))
    for needle in ("innerHTML", "insertAdjacentHTML", "createContextualFragment", "DOMParser"):
        assert needle not in code
    assert not re.search(r"setAttribute\(\s*['\"](?:style|on\w+)['\"]", code)
    assert not re.search(r"\.on[a-z]+\s*=", code), "no event handler properties"
    assert not re.search(r"\bfetch\(", code), "Data.loadDetails() does the fetching"
    # The only style write is the CSSOM font of the "Type your own text" input.
    assert re.findall(r"\.style\.\w+", code) == [".style.fontFamily"]


def test_css_uses_only_tokens_for_colours():
    code = re.sub(r"/\*.*?\*/", "", CSS_PART.read_text(encoding="utf-8"), flags=re.DOTALL)
    colour_props = re.findall(
        r"(?:^|[;{\s])(?:color|background(?:-color)?|border(?:-\w+)?-color|outline-color)"
        r"\s*:\s*([^;]+);",
        code,
    )
    for value in colour_props:
        assert value.strip().startswith("var(--c-"), value
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", code)


# ------------------------------------------------------------------------ pure helpers


@dataclass
class Part:
    """A blank page with 00-core.js and 40-details.js loaded as one module."""

    page: Any
    errors: list[str] = field(default_factory=list)

    def run(self, body: str, arg: Any = None) -> Any:
        """Evaluate ``(D, arg) => body`` with ``D`` the part's ``Details``."""
        return self.page.evaluate(
            f"(arg) => ((D, arg) => {body})(globalThis.__tffDetails, arg)", arg
        )


@pytest.fixture(scope="module")
def part(browser: Any) -> Iterator[Part]:
    core = CORE_PART.read_text(encoding="utf-8")
    details = JS_PART.read_text(encoding="utf-8")
    assert "</script" not in (core + details).lower()
    context = browser.new_context()
    page = context.new_page()
    loaded = Part(page)
    page.on("pageerror", lambda error: loaded.errors.append(str(error)))
    page.set_content(
        "<!doctype html><html lang=en><meta charset=utf-8><title>details</title>"
        f"<script type=module>\n{core}\n{details}\nglobalThis.__tffDetails = Details;\n</script>",
        wait_until="load",
    )
    yield loaded
    context.close()


def test_part_loads_alone_without_errors(part):
    assert part.errors == []
    assert part.run("[typeof D.open, typeof D.close, D.openId]") == ["function", "function", None]


def _links() -> list[dict[str, str]]:
    found = []
    for font in DOC["fonts"]:
        found.append(font["links"]["primary"])
        if font["links"]["designer"]:
            found.append(font["links"]["designer"])
    extra = [
        {"url": "https://www.github.com/Owner/Repo.git/tree/main"},
        {"url": "https://github.com/owner"},
        {"url": "https://fonts.google.com/specimen/Inter?query=x"},
        {"url": "https://www.example.org:8443/a/b"},
        {"url": "https://GitHub.com/rsms/inter"},
        {"url": "https://example.com/x", "label": "Given label"},
    ]
    return found + extra


def test_destination_names_match_python(part):
    links = _links()
    got = part.run("arg.map((link) => D.destination(link))", links)
    assert got == [data.destination_name(link) for link in links]


def test_nerd_link_texts_match_python(part):
    """A Nerd Font build's link names the build, then where it goes (CONTRACT section 1)."""
    links = [f["links"]["nerd"] for f in DOC["fonts"] if f["links"]["nerd"]] + [
        {
            "url": "https://github.com/ryanoasis/nerd-fonts/tree/v3.5.1/patched-fonts/X",
            "label": "X",
        },
        {"url": "https://www.example.org/nf/", "label": "Example NF"},
    ]
    got = part.run("arg.map((link) => D.nerdText(link))", links)
    assert got == [data.nerd_link_text(link) for link in links]
    assert data.nerd_link_text(links[-2]) == "X (GitHub: ryanoasis/nerd-fonts)"


@pytest.mark.parametrize(
    ("hash_", "font_id", "want"),
    [
        ("", "inter", "#font=inter"),
        ("#rank=project&os=linux", "inter", "#rank=project&font=inter&os=linux"),
        ("#os=linux&font=old&cat=serif", "inter", "#cat=serif&font=inter&os=linux"),
        ("#font=inter", None, ""),
        ("#font=inter&os=linux&x", None, "#os=linux&x"),
        ("#q=a%20b&rank=coding&font=a&font=b", "c", "#rank=coding&q=a%20b&font=c"),
    ],
)
def test_hash_with_keeps_every_other_pair(part, hash_, font_id, want):
    assert part.run("D.hashWith(arg[0], arg[1])", [hash_, font_id]) == want


@pytest.mark.parametrize(
    ("hash_", "want"),
    [
        ("", None),
        ("#font=sample-sans-01", "sample-sans-01"),
        ("#font=a&font=b", "b"),
        ("#font=a&font=B", None),
        ("#font=%E0%A4%A", None),
        ("#rank=project&font=x-1&os=linux", "x-1"),
        ("#fontx=1", None),
    ],
)
def test_hash_font_reads_the_last_valid_pair(part, hash_, want):
    assert part.run("D.hashFont(arg)", hash_) == want


# ------------------------------------------------------------------------ the built site


def _sample_data() -> bool:
    """Whether the site is built from the sample, whose font ids some panel tests name."""
    return bool(DOC.get("synthetic")) and all(f["id"].startswith("sample-") for f in DOC["fonts"])


def _sample_only() -> None:
    """Skip a test that names fonts of the sample catalog (as tests/site/test_list.py does).
    The other panel tests pick their fonts from the data, so they run on a real build too."""
    if not _sample_data():
        pytest.skip("names fonts of the sample catalog")


def _first_ids(n: int) -> list[str]:
    """The first n fonts of the default list, which every build shows on load."""
    return [f["id"] for f in data.server_order(DOC)[:n]]


def _open_page(guarded: Any, path: str = "/") -> Any:
    page = guarded.new_page()
    page.goto(path)
    page.wait_for_selector("html[data-js]", state="attached")
    return page


def _toggle(page: Any, font_id: str) -> Any:
    return page.locator(f"#font-{font_id} .details-toggle")


def _panel(page: Any, font_id: str) -> Any:
    return page.locator(f"#details-{font_id}")


def _wait_ready(page: Any, font_id: str) -> None:
    page.wait_for_selector(f"#details-{font_id}[data-state='ready']")


def _font_requests(guarded: Any) -> list[str]:
    return [url for url in guarded.requests if "/assets/fonts/" in url]


def _wait_for(page: Any, expression: str, timeout_s: float = 5.0) -> None:
    """Poll a JS expression until it is truthy. (Playwright's wait_for_function evaluates a
    string inside the page, which the site's CSP blocks as eval.)"""
    deadline = time.monotonic() + timeout_s
    while not page.evaluate(f"() => Boolean({expression})"):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for: {expression}")
        page.wait_for_timeout(25)


def _hash(page: Any) -> str:
    return page.evaluate("location.hash")


def _focused(page: Any) -> str:
    return page.evaluate(
        "() => { const a = document.activeElement;"
        " return a ? `${a.tagName.toLowerCase()}.${a.className}#${a.id}` : ''; }"
    )


def test_toggle_opens_one_panel_at_a_time_and_back_follows(guarded_context):
    guarded = guarded_context()
    page = _open_page(guarded)
    first, second = _first_ids(2)
    assert _panel(page, first).get_attribute("hidden") is not None

    _toggle(page, first).click()
    _wait_ready(page, first)
    assert _toggle(page, first).get_attribute("aria-expanded") == "true"
    assert _panel(page, first).is_visible()
    assert _hash(page) == f"#font={first}"
    # A click leaves focus on the button; only a #font= link moves it to the heading.
    assert _focused(page).startswith("button.details-toggle")
    assert page.evaluate("document.activeElement.getAttribute('aria-controls')") == (
        f"details-{first}"
    )
    title = _panel(page, first).locator(":scope > h4.details-title")
    assert title.get_attribute("id") == f"details-{first}-h"
    assert title.get_attribute("tabindex") == "-1"
    assert title.inner_text() == f"Details for {FONTS[first]['family']}"

    _toggle(page, second).click()
    _wait_ready(page, second)
    assert _toggle(page, first).get_attribute("aria-expanded") == "false"
    assert not _panel(page, first).is_visible()
    assert page.locator(".details:not([hidden])").count() == 1
    assert _hash(page) == f"#font={second}"

    page.go_back()
    _wait_for(page, f"location.hash === '#font={first}'")
    page.wait_for_selector(f"#details-{first}:not([hidden])")
    assert page.locator(".details:not([hidden])").count() == 1

    page.go_back()
    _wait_for(page, "location.hash === ''")
    page.wait_for_selector(".details:not([hidden])", state="detached")
    assert page.locator(".details-toggle[aria-expanded='true']").count() == 0

    # A second click on the same button closes the panel and drops the font from the URL.
    _toggle(page, first).click()
    _wait_ready(page, first)
    _toggle(page, first).click()
    assert not _panel(page, first).is_visible()
    assert _hash(page) == ""
    guarded.assert_clean(page)


def test_opening_keeps_other_hash_pairs(guarded_context):
    _sample_only()
    guarded = guarded_context()
    page = _open_page(guarded, "/#rank=project&os=linux")
    _wait_for(page, "location.hash.startsWith('#rank=project')")
    _toggle(page, "sample-sans-01").click()
    _wait_ready(page, "sample-sans-01")
    assert _hash(page) == "#rank=project&font=sample-sans-01&os=linux"
    guarded.assert_clean(page)


def test_close_button_and_escape_return_focus_to_the_toggle(guarded_context):
    guarded = guarded_context()
    page = _open_page(guarded)
    font_id = _first_ids(4)[-1]
    _toggle(page, font_id).click()
    _wait_ready(page, font_id)
    close = _panel(page, font_id).locator("button.details-close")
    assert close.inner_text().startswith("Close")
    close.focus()
    page.keyboard.press("Enter")
    assert not _panel(page, font_id).is_visible()
    assert _focused(page).startswith("button.details-toggle")
    assert _toggle(page, font_id).get_attribute("aria-expanded") == "false"

    page.keyboard.press("Enter")  # the toggle has focus: open again by keyboard
    _wait_ready(page, font_id)
    _panel(page, font_id).locator("a").first.focus()
    page.keyboard.press("Escape")
    assert not _panel(page, font_id).is_visible()
    assert _focused(page).startswith("button.details-toggle")
    assert _hash(page) == ""
    guarded.assert_clean(page)


def test_font_link_on_load_opens_and_focuses_the_heading(guarded_context):
    _sample_only()
    guarded = guarded_context(viewport={"width": 1280, "height": 800})
    font_id = "sample-mono-35"  # the last row: the page must scroll to it
    page = _open_page(guarded, f"/#font={font_id}")
    _wait_ready(page, font_id)
    _wait_for(
        page, f"document.activeElement && document.activeElement.id === 'details-{font_id}-h'"
    )
    top = page.evaluate(f"document.getElementById('font-{font_id}').getBoundingClientRect().top")
    assert -1 <= top < 800
    assert _hash(page) == f"#font={font_id}"
    guarded.assert_clean(page)


def test_font_link_with_another_rank_lands_on_the_moved_row(guarded_context):
    _sample_only()
    guarded = guarded_context(viewport={"width": 1280, "height": 800})
    font_id = "sample-mono-07"  # 7th overall, 2nd in Coding: the row moves on load
    page = _open_page(guarded, f"/#rank=coding&font={font_id}")
    _wait_for(page, "globalThis.tff !== undefined")
    _wait_ready(page, font_id)
    _wait_for(
        page, f"document.activeElement && document.activeElement.id === 'details-{font_id}-h'"
    )
    rank = page.locator(f"#font-{font_id} .rank").inner_text()
    assert rank == "2"
    top = page.evaluate(f"document.getElementById('font-{font_id}').getBoundingClientRect().top")
    assert -1 <= top < 800
    guarded.assert_clean(page)


def test_font_link_for_a_filtered_out_font_opens_nothing(guarded_context):
    _sample_only()
    guarded = guarded_context()
    font_id = "sample-sans-01"  # not a serif
    page = _open_page(guarded, f"/#cat=serif&font={font_id}")
    _wait_for(page, "globalThis.tff !== undefined")
    page.wait_for_timeout(200)
    assert page.locator(f"#font-{font_id}").count() == 0
    assert page.locator(".details:not([hidden])").count() == 0
    guarded.assert_clean(page)


def test_a_font_link_in_the_page_opens_with_focus(guarded_context):
    _sample_only()
    guarded = guarded_context()
    page = _open_page(guarded)
    font_id = "sample-serif-30"
    page.evaluate(f"location.hash = '#font={font_id}'")
    _wait_ready(page, font_id)
    _wait_for(
        page, f"document.activeElement && document.activeElement.id === 'details-{font_id}-h'"
    )
    guarded.assert_clean(page)


def _open_ids(page: Any) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('.details:not([hidden])')].map(d => d.id)")


def _live_page(guarded: Any, path: str = "/") -> Any:
    """A page whose list is live (the Milestone 3 hook is up), so State owns the hash."""
    page = _open_page(guarded, path)
    _wait_for(page, "globalThis.tff !== undefined")
    return page


def test_back_reopens_a_font_the_previous_view_hid(guarded_context):
    # Back from a view that hides the font: the list is redrawn first, then the panel opens.
    _sample_only()
    guarded = guarded_context()
    font_id = "sample-sans-01"
    page = _live_page(guarded, f"/#font={font_id}")
    _wait_ready(page, font_id)
    page.evaluate("location.hash = '#cat=serif'")
    _wait_for(page, f"!document.getElementById('font-{font_id}')")
    assert _open_ids(page) == []
    page.go_back()
    _wait_for(page, f"location.hash === '#font={font_id}'")
    _wait_for(
        page, f"document.activeElement && document.activeElement.id === 'details-{font_id}-h'"
    )
    assert _open_ids(page) == [f"details-{font_id}"]
    assert _toggle(page, font_id).get_attribute("aria-expanded") == "true"
    guarded.assert_clean(page)


def test_the_hook_opens_and_closes_panels_through_the_font_key(guarded_context):
    # site/CONTRACT.md section 9: font=<id> means "its details panel is open", so Milestone
    # 3's tff.list.setState({font}) opens it (without moving focus) and font '' closes it.
    _sample_only()
    guarded = guarded_context()
    page = _live_page(guarded)
    font_id = "sample-serif-04"
    before = _focused(page)
    page.evaluate(f"tff.list.setState({{ font: '{font_id}' }})")
    _wait_ready(page, font_id)
    assert _open_ids(page) == [f"details-{font_id}"]
    assert _hash(page) == f"#font={font_id}"
    assert _focused(page) == before
    # And a click here shows up in the hook's state.
    _toggle(page, "sample-sans-01").click()
    _wait_ready(page, "sample-sans-01")
    assert page.evaluate("tff.list.getState().font") == "sample-sans-01"
    assert _open_ids(page) == ["details-sample-sans-01"]
    page.evaluate("tff.list.setState({ font: '' })")
    _wait_for(page, "location.hash === ''")
    assert _open_ids(page) == []
    assert _toggle(page, "sample-sans-01").get_attribute("aria-expanded") == "false"
    guarded.assert_clean(page)


def test_a_search_typed_just_before_opening_survives_back(guarded_context):
    # The search is written to its history entry (after 300 ms of quiet) before a panel's
    # entry is pushed, so Back returns to the search, not to the view before it.
    _sample_only()
    guarded = guarded_context()
    page = _live_page(guarded)
    page.fill("#f-q", "sample")
    _toggle(page, "sample-sans-01").click()  # well within the 300 ms
    _wait_ready(page, "sample-sans-01")
    page.wait_for_timeout(400)
    assert _hash(page) == "#q=sample&font=sample-sans-01"
    page.go_back()
    _wait_for(page, "location.hash === '#q=sample'")
    assert page.input_value("#f-q") == "sample"
    assert _open_ids(page) == []
    guarded.assert_clean(page)


def test_a_font_the_link_hides_opens_once_the_view_shows_it(guarded_context):
    _sample_only()
    guarded = guarded_context()
    font_id = "sample-sans-01"  # not a serif
    page = _live_page(guarded, f"/#cat=serif&font={font_id}")
    assert _open_ids(page) == []
    page.evaluate("tff.list.setState({ cat: '' })")
    _wait_ready(page, font_id)
    assert _open_ids(page) == [f"details-{font_id}"]
    assert _hash(page) == f"#font={font_id}"
    guarded.assert_clean(page)


def test_a_source_that_may_not_publish_ranks_never_shows_one(guarded_context):
    # Ruling on source terms: publish_rank false hides every rank of that source, even one
    # the data wrongly carries (tff_site.data's semantic checks should stop that earlier).
    source = next((s for s in DOC["sources"] if not s["publish_rank"]), None)
    if source is None:
        pytest.skip("no source withholds its ranks in this data")
    font_id = next(i for i, f in FONTS.items() if f["sources"][source["id"]]["state"] == "observed")
    guarded = guarded_context()
    page = guarded.new_page()

    def tampered(route: Any) -> None:
        payload = fetch_unencoded(route).json()
        payload["fonts"][font_id]["sources"][source["id"]]["rank_in_source"] = 7
        route.fulfill(json=payload)

    page.route("**/assets/details.*.json", tampered)
    page.goto(f"/#font={font_id}")
    _wait_ready(page, font_id)
    cell = page.locator(f"#details-{font_id} tr[data-source='{source['id']}'] td")
    assert cell.inner_text().startswith("observed; rank not published")
    assert "#7" not in cell.inner_text()
    guarded.assert_clean(page)


def test_type_own_text_loads_the_font_only_on_request(guarded_context):
    guarded = guarded_context()
    page = _open_page(guarded)
    with_file = [f["id"] for f in data.server_order(DOC) if f["font_file"] and f["preview_ok"]]
    if not with_file:
        pytest.skip("no font in this data has a font file")
    font_id = "sample-sans-01" if _sample_data() else with_file[0]
    font = FONTS[font_id]
    _toggle(page, font_id).click()
    _wait_ready(page, font_id)
    button = _panel(page, font_id).locator("button.typeown-load")
    kb = round(font["font_file"]["size"] / 1000)
    assert button.inner_text() == f"Load font ({kb} KB) to type your own text"
    assert _font_requests(guarded) == []

    button.focus()
    page.keyboard.press("Enter")
    field = _panel(page, font_id).locator("input.typeown-input")
    field.wait_for()
    assert page.evaluate(f"document.fonts.check('16px \"tff-{font_id}\"')")
    assert _focused(page).startswith("input.typeown-input")
    family = field.evaluate("(el) => getComputedStyle(el).fontFamily")
    assert family.replace('"', "").startswith(f"tff-{font_id}")
    assert field.input_value() == font["family"]
    assert field.get_attribute("style") is not None  # set through CSSOM, which the CSP allows
    label = _panel(page, font_id).locator("label.typeown-label")
    assert label.get_attribute("for") == field.get_attribute("id")
    field.fill("Hamburgefonstiv")
    requests = _font_requests(guarded)
    assert len(requests) == 1
    assert re.search(rf"/assets/fonts/{font_id}\.[0-9a-f]{{10}}\.ttf$", requests[0])
    guarded.assert_clean(page)


def test_no_type_own_text_without_a_font_file(guarded_context):
    guarded = guarded_context()
    page = _open_page(guarded)
    font_id = next(
        (i for i, f in FONTS.items() if f["font_file"] is None and f["preview_ok"]), None
    )
    if font_id is None:
        pytest.skip("every previewed font in this data has a font file")
    _toggle(page, font_id).click()
    _wait_ready(page, font_id)
    assert _panel(page, font_id).locator(".typeown-load, .details-typeown").count() == 0
    guarded.assert_clean(page)


def test_stale_details_show_the_reload_message(guarded_context):
    _sample_only()
    guarded = guarded_context()
    page = guarded.new_page()
    page.route("**/assets/details.*.json", lambda route: route.fulfill(status=404, body="gone"))
    page.goto("/")
    page.wait_for_selector("html[data-js]", state="attached")
    font_id = "sample-sans-03"
    _toggle(page, font_id).click()
    page.wait_for_selector(f"#details-{font_id}[data-state='stale']")
    assert _panel(page, font_id).locator(".details-status").inner_text() == STALE_LINE
    assert _panel(page, font_id).locator("button.details-reload").is_visible()
    # Details for another font say the same, without another request.
    _toggle(page, "sample-sans-01").click()
    page.wait_for_selector("#details-sample-sans-01[data-state='stale']")
    assert len([u for u in guarded.requests if "/assets/details." in u]) == 1
    guarded.assert_clean(page)


def test_a_failed_load_can_be_retried(guarded_context):
    _sample_only()
    guarded = guarded_context()
    page = guarded.new_page()
    failures = {"left": 1}

    def flaky(route: Any) -> None:
        if failures["left"]:
            failures["left"] -= 1
            route.abort()
        else:
            route.continue_()

    page.route("**/assets/details.*.json", flaky)
    page.goto("/")
    page.wait_for_selector("html[data-js]", state="attached")
    font_id = "sample-sans-03"
    _toggle(page, font_id).click()
    page.wait_for_selector(f"#details-{font_id}[data-state='error']")
    _panel(page, font_id).locator("button.details-retry").click()
    _wait_ready(page, font_id)
    # The retry button was replaced by the details: focus goes to the heading, not <body>.
    assert page.evaluate("document.activeElement.id") == f"details-{font_id}-h"
    guarded.assert_clean(page)


def test_panel_reflows_at_320_px(guarded_context):
    guarded = guarded_context(viewport={"width": 320, "height": 700})
    page = _open_page(guarded)
    if _sample_data():
        ids: tuple[str, ...] = ("sample-serif-22-long", "sample-sans-01")
    else:  # the longest name among the first rows, and the first row
        shown = data.server_order(DOC)[:40]
        ids = (max(shown, key=lambda f: len(f["family"]))["id"], shown[0]["id"])
    for font_id in ids:
        _toggle(page, font_id).click()
        _wait_ready(page, font_id)
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= 320, f"{font_id}: page is {width}px wide"
        overflowing = page.evaluate(
            f"""() => [...document.querySelectorAll('#details-{font_id} *')]
                .filter((el) => !el.closest('.visually-hidden')
                    && el.scrollWidth > el.clientWidth + 1
                    && getComputedStyle(el).overflowX !== 'visible')
                .map((el) => el.className)"""
        )
        assert overflowing == []
    guarded.assert_clean(page)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_finds_nothing_in_an_open_panel(guarded_context, scheme):
    from axe_playwright_python.sync_playwright import Axe

    guarded = guarded_context(color_scheme=scheme)
    page = _open_page(guarded)
    ids = ("sample-sans-01", "sample-mono-23") if _sample_data() else tuple(_first_ids(2))
    for font_id in ids:
        _toggle(page, font_id).click()
        _wait_ready(page, font_id)
        results = Axe().run(
            page,
            context=f"#details-{font_id}",
            options={"runOnly": {"type": "tag", "values": AXE_TAGS}},
        )
        assert results.violations_count == 0, results.generate_report()


# ------------------------------------------------------------------------ the owner's ten


def _tier_label(tier: str) -> str:
    return f"Tier {tier}"


def _place(entry: dict[str, Any]) -> str:
    if entry["unranked"]:
        return f"Not ranked: {data.UNRANKED_LABELS[entry['unranked']]}"
    bits = [f"#{entry['rank']}" if entry["rank"] else f"Band {entry['band']}"]
    bits.append(f"tier {entry['tier']}")
    low, high = entry["range"]
    bits.append(f"likely #{low}" if low == high else f"likely #{low} to #{high}")
    return ", ".join(bits)


def _source_text(doc: dict[str, Any], source: dict[str, Any], entry: dict[str, Any]) -> str:
    label = data.STATE_LABELS[entry["state"]]
    out = label
    if entry["state"] == "observed":
        if entry["rank_in_source"]:
            out = f"#{entry['rank_in_source']}"
        elif not source["publish_rank"]:
            out = f"{label}; rank not published"
    if entry["abstains_in"]:
        views = {v["key"]: v["label"] for v in doc["views"]}
        out += "; left out of " + " and ".join(views[k] for k in entry["abstains_in"])
    if source["stale"]:
        out += f" (stale: data from {source['data_date']})"
    return out


def expected_panel(doc: dict[str, Any], font: dict[str, Any]) -> dict[str, Any]:
    """What the panel of ``font`` must show, from the site data (the Python reference)."""
    lic = font["license"]
    classes = {c["id"]: c["label"] for c in doc["license_classes"]}
    systems = {s["id"]: s["label"] for s in doc["systems"]}
    license_pairs = [["License", f"{lic['name']} ({lic['spdx']})"]]
    if lic["class"] in classes:
        license_pairs.append(["Group", classes[lic["class"]]])
    license_pairs.append(["Redistributable", REDIST_YES if lic["redistributable"] else REDIST_NO])
    credit = f"Required: {lic['attribution']}" if lic["attribution_required"] else "Not required."
    license_pairs.append(["Credit", credit])

    primary, designer = font["links"]["primary"], font["links"]["designer"]
    links = [["Official download", data.destination_name(primary), primary["url"]]]
    if designer:
        links.append(["Designer", data.destination_name(designer), designer["url"]])
    nerd = font["links"]["nerd"]  # the marker is its term (owner ruling of 2026-09-29)
    if nerd:
        links.append([doc["nerd"]["marker"], data.nerd_link_text(nerd), nerd["url"]])

    ranks, tiers = [], set()
    for view in doc["views"]:
        entry = font["ranks"].get(view["key"])
        if not view["available"] or entry is None:
            continue
        if entry["tier"]:
            tiers.add(entry["tier"])
        ranks.append([view["label"], _place(entry), GATE_LINE if entry["gate_held"] else None])
    tier_pairs = [[_tier_label(t), doc["tiers"][t]] for t in "ABC" if t in tiers]

    sources = [
        [
            SURVEY_CAPTIONS[source["survey"]],
            source["id"],
            source["name"],
            f"/methodology/#source-{source['id']}",
            _source_text(doc, source, font["sources"][source["id"]]),
        ]
        for source in doc["sources"]
    ]

    formats = font["formats"]
    tags = []
    if font["preinstalled_on"]:
        tags.append(
            ["Comes with", ", ".join(systems[p["system"]] for p in font["preinstalled_on"])]
        )
    if font["pulled_in_by"]:
        pulled = "; ".join(
            f"{p['package']} on {systems[p['system']]}" for p in font["pulled_in_by"]
        )
        tags.append(["Pulled in by", pulled])
    if formats["variable"] and formats["static"]:
        tags.append(["Formats", "Variable and static"])
    else:
        tags.append(["Formats", "Variable" if formats["variable"] else "Static"])
    tags.append(["Spacing", "Monospaced" if font["is_monospace"] else "Proportional"])
    coverage = font["latin"]["coverage"]
    tags.append(
        [
            "Latin coverage",
            "Basic Latin only (limited accents)"
            if coverage == "basic"
            else "Extended (accented letters)",
        ]
    )
    if font["aliases"]:
        names = [
            f"{a['name']} (PostScript name)" if a["relation"] == "postscript" else a["name"]
            for a in font["aliases"]
        ]
        tags.append(["Also known as", ", ".join(names)])

    run_date = doc["run"]["date"]
    subject = f"Problem with {font['family']} ({font['id']})"
    body = f"Font: {font['id']}\nData date: {run_date}\n\nWhat is wrong:\n"
    safe = "-_.!~*'()"  # encodeURIComponent's unescaped set
    report = {
        "issue": f"{data.REPORT_ISSUE_URL}&font_id={font['id']}&data_date={run_date}",
        "issue_text": REPORT_LINK,
        "email": data.FEEDBACK_EMAIL,
        "mailto": f"mailto:{data.FEEDBACK_EMAIL}?subject={quote(subject, safe=safe)}"
        f"&body={quote(body, safe=safe)}",
    }
    return {
        "title": f"Details for {font['family']}",
        "license": license_pairs,
        "license_href": lic["text_url"],
        "links": links,
        "link_note": primary.get("note"),
        "nerd_legend": doc["nerd"]["legend"] if nerd else None,
        "ranks": ranks,
        "tiers": tier_pairs,
        "sources": sources,
        "tags": tags,
        "report": report,
    }


# Reads the open panel back into the shape of expected_panel().
READ_PANEL = """(id) => {
  const panel = document.getElementById(`details-${id}`);
  const q = (sel) => panel.querySelector(sel);
  const pairs = (root) => root ? [...root.querySelectorAll('.details-pair')].map((p) =>
      [p.querySelector('dt').textContent, p.querySelector('dd').textContent]) : [];
  const links = [...panel.querySelectorAll('.details-links .details-pair')].map((p) => {
    const a = p.querySelector('dd a');
    return [p.querySelector('dt').textContent, a.textContent, a.getAttribute('href')];
  });
  const ranks = [...panel.querySelectorAll('.details-rank-list .details-pair')].map((p) => {
    const gate = p.querySelector('.details-gate');
    return [p.querySelector('dt').textContent, p.querySelector('.details-place').textContent,
            gate ? gate.textContent : null];
  });
  const sources = [...panel.querySelectorAll('.details-src-table')].flatMap((table) =>
    [...table.querySelectorAll('tbody tr')].map((tr) => {
      const a = tr.querySelector('th a');
      return [table.querySelector('caption').textContent, tr.dataset.source, a.textContent,
              a.getAttribute('href'), tr.querySelector('td').textContent];
    }));
  const issue = q('.details-report-issue');
  const email = q('.details-report-email');
  const note = q('.details-links .details-link-note');
  const legend = q('.details-links .details-nf-legend');
  return {
    title: q(':scope > .details-title').textContent,
    license: pairs(q('.details-license')),
    license_href: q('.details-lic-link').getAttribute('href'),
    links,
    link_note: note && note.textContent,
    nerd_legend: legend && legend.textContent,
    ranks,
    tiers: pairs(q('.details-tiers')),
    sources,
    tags: pairs(q('.details-tags')),
    report: {
      issue: issue && issue.getAttribute('href'),
      issue_text: issue && issue.textContent,
      email: email && email.textContent,
      mailto: email && email.getAttribute('href'),
    },
  };
}"""


@pytest.mark.parametrize("font_id", OWNER_TEN)
def test_owner_ten_fields_match_the_data(guarded_context, font_id):
    assert font_id in FONTS, f"{font_id} is not in {SITE_DATA}"
    guarded = guarded_context()
    page = _open_page(guarded, f"/#font={font_id}")
    _wait_ready(page, font_id)
    got = page.evaluate(READ_PANEL, font_id)
    want = expected_panel(DOC, FONTS[font_id])
    for key in want:
        assert got[key] == want[key], key
    # Never a link to a font file (M1 step 14): only license, download, designer,
    # methodology and report links.
    hrefs = page.evaluate(
        f"[...document.querySelectorAll('#details-{font_id} a')].map((a) => a.getAttribute('href'))"
    )
    font_file = FONTS[font_id]["font_file"]
    assert not any("/assets/fonts/" in h for h in hrefs)
    if font_file:
        assert font_file["url"] not in hrefs
    guarded.assert_clean(page)


NERD_FONTS = [f["id"] for f in DOC["fonts"] if f["links"]["nerd"]]


@pytest.mark.parametrize("font_id", NERD_FONTS)
def test_a_nerd_font_builds_link_and_legend(guarded_context, font_id):
    """The panel lists the Nerd Font build's page after the official and designer links, with
    the marker as its term and the legend below (owner rulings of 2026-09-29)."""
    guarded = guarded_context()
    page = _open_page(guarded, f"/#font={font_id}")
    _wait_ready(page, font_id)
    got = page.evaluate(READ_PANEL, font_id)
    want = expected_panel(DOC, FONTS[font_id])
    assert (got["links"], got["nerd_legend"]) == (want["links"], want["nerd_legend"])
    mark = page.locator(f"#details-{font_id} .details-links dt .nf-mark")
    assert mark.get_attribute("role") == "img"
    assert mark.get_attribute("aria-label") == DOC["nerd"]["label"]
    link = page.locator(f"#details-{font_id} a.details-nf-link")
    assert link.get_attribute("href") == FONTS[font_id]["links"]["nerd"]["url"]
    guarded.assert_clean(page)


OWNER_TEN_CASES = {
    "attribution required": lambda f: f["license"]["attribution_required"],
    "not redistributable": lambda f: not f["license"]["redistributable"],
    "held by the gate": lambda f: any(e["gate_held"] for e in f["ranks"].values()),
    "preinstalled": lambda f: bool(f["preinstalled_on"]),
    "pulled in by a package": lambda f: bool(f["pulled_in_by"]),
}


def test_owner_ten_covers_the_edge_cases():
    if os.environ.get("TFF_OWNER_TEN"):
        pytest.skip("the owner named the fonts")
    fonts = [FONTS[i] for i in OWNER_TEN]
    missing = [name for name, case in OWNER_TEN_CASES.items() if not any(map(case, fonts))]
    if not _sample_data():
        # Real data need not hold every case (the first real run has no font that needs
        # attribution or forbids redistribution); the sample always does.
        if missing:
            pytest.skip(f"this data has no font that is: {', '.join(missing)}")
        return
    assert len(OWNER_TEN) == 10
    assert missing == []


# ------------------------------------------------------------------------ linkcheck


class FakeClock:
    """Monotonic time that only moves when the code sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_net(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Route linkcheck's requests to a handler; record (time, method, url) per request."""
    clock = FakeClock()
    state: dict[str, Any] = {"log": [], "clock": clock, "handler": None}

    def handle(request: httpx.Request) -> httpx.Response:
        state["log"].append((clock.now, request.method, str(request.url)))
        return state["handler"](request)

    def client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handle), timeout=5)

    monkeypatch.setattr(linkcheck, "_client", client)
    monkeypatch.setattr(linkcheck, "_monotonic", clock.time)
    monkeypatch.setattr(linkcheck, "_sleep", clock.sleep)
    return state


def _catalog(tmp_path: Path, fonts: list[dict[str, Any]]) -> Path:
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    by_id = {f["id"]: f for f in doc["fonts"]}
    out = []
    for spec in fonts:
        font = json.loads(json.dumps(by_id["sample-sans-01"]))
        font["id"] = spec["id"]
        font["license"]["text_url"] = spec["text_url"]
        font["links"]["primary"] = {"url": spec["primary"]}
        font["links"]["designer"] = {"url": spec["designer"]} if spec.get("designer") else None
        nerd = spec.get("nerd")
        font["links"]["nerd"] = {"url": nerd, "label": "A Nerd Font"} if nerd else None
        out.append(font)
    doc["fonts"] = out
    path = tmp_path / "catalog-site.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_linkcheck_checks_every_link_once_and_paces_each_host(tmp_path, fake_net):
    path = _catalog(
        tmp_path,
        [
            {
                "id": "a",
                "text_url": "https://licenses.test/ofl.txt",
                "primary": "https://code.test/a",
                "designer": "https://people.test/a",
                "nerd": "https://nerd.test/a",
            },
            {
                "id": "b",
                "text_url": "https://licenses.test/ofl.txt",  # shared: fetched once
                "primary": "https://code.test/b",
            },
            {
                "id": "c",
                "text_url": "https://licenses.test/apache.txt",
                "primary": "https://code.test/c",
            },
        ],
    )
    fake_net["handler"] = lambda request: httpx.Response(200)
    results = linkcheck.linkcheck(path, rate=1.0)
    assert [(r.font_id, r.field, r.url, r.status) for r in results] == [
        ("a", "license.text_url", "https://licenses.test/ofl.txt", 200),
        ("a", "links.primary", "https://code.test/a", 200),
        ("a", "links.designer", "https://people.test/a", 200),
        ("a", "links.nerd", "https://nerd.test/a", 200),
        ("b", "license.text_url", "https://licenses.test/ofl.txt", 200),
        ("b", "links.primary", "https://code.test/b", 200),
        ("c", "license.text_url", "https://licenses.test/apache.txt", 200),
        ("c", "links.primary", "https://code.test/c", 200),
    ]
    log = fake_net["log"]
    urls = [url for _, _, url in log]
    assert len(urls) == len(set(urls)) == 7
    assert all(method == "GET" for _, method, _ in log)
    by_host: dict[str, list[float]] = {}
    for when, _, url in log:
        by_host.setdefault(httpx.URL(url).host, []).append(when)
    for host, times in by_host.items():
        gaps = [b - a for a, b in pairwise(times)]
        assert all(gap >= 1.0 - 1e-9 for gap in gaps), (host, gaps)
    # Hosts are interleaved, so 3 requests to code.test take about 2 s, not 5.
    assert fake_net["clock"].now - 1000.0 <= 2.0 + 1e-9


def test_linkcheck_follows_redirects_and_reports_failures(tmp_path, fake_net):
    path = _catalog(
        tmp_path,
        [
            {
                "id": "a",
                "text_url": "https://licenses.test/moved",
                "primary": "https://code.test/missing",
                "designer": "https://down.test/",
            },
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://licenses.test/moved":
            return httpx.Response(301, headers={"Location": "https://mirror.test/ofl.txt"})
        if url == "https://mirror.test/ofl.txt":
            return httpx.Response(200)
        if url == "https://code.test/missing":
            return httpx.Response(404)
        raise httpx.ConnectError("refused", request=request)

    fake_net["handler"] = handler
    results = linkcheck.linkcheck(path, ids=["a"])
    assert [(r.field, r.status) for r in results] == [
        ("license.text_url", 200),
        ("links.primary", 404),
        ("links.designer", 0),
    ]


def test_linkcheck_stops_redirect_loops_and_refuses_http(tmp_path, fake_net):
    path = _catalog(
        tmp_path,
        [{"id": "a", "text_url": "https://loop.test/1", "primary": "https://code.test/x"}],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "loop.test":
            return httpx.Response(302, headers={"Location": "/1"})
        return httpx.Response(302, headers={"Location": "http://code.test/plain"})

    fake_net["handler"] = handler
    results = linkcheck.linkcheck(path)
    assert [r.status for r in results] == [0, 0]
    assert not any(url.startswith("http://") for _, _, url in fake_net["log"])


def test_linkcheck_reports_unknown_ids(tmp_path, fake_net):
    path = _catalog(
        tmp_path, [{"id": "a", "text_url": "https://licenses.test/x", "primary": "https://c.test/"}]
    )
    fake_net["handler"] = lambda request: httpx.Response(200)
    results = linkcheck.linkcheck(path, ids=["nope", "a"])
    assert (results[0].font_id, results[0].field, results[0].status) == ("nope", "id", 0)
    assert [r.status for r in results[1:]] == [200, 200]
    # Spaces around ids (as in --ids "a, b") and a bare string are read as ids.
    assert [r.font_id for r in linkcheck.linkcheck(path, ids=[" a ", "", "a"])] == ["a", "a"]
    assert [r.font_id for r in linkcheck.linkcheck(path, ids="a")] == ["a", "a"]


def test_linkcheck_cli(tmp_path, fake_net, capsys):
    path = _catalog(
        tmp_path,
        [
            {"id": "a", "text_url": "https://licenses.test/x", "primary": "https://c.test/a"},
            {"id": "b", "text_url": "https://licenses.test/y", "primary": "https://c.test/b"},
        ],
    )
    fake_net["handler"] = lambda request: httpx.Response(404 if request.url.path == "/b" else 200)
    assert cli.main(["linkcheck", "--data", str(path), "--ids", "a"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "200 a license.text_url https://licenses.test/x",
        "200 a links.primary https://c.test/a",
    ]
    assert cli.main(["linkcheck", "--data", str(path), "--ids", "a,b"]) == 1
    assert "404 b links.primary https://c.test/b" in capsys.readouterr().out


def test_linkcheck_rejects_a_bad_rate(tmp_path):
    with pytest.raises(ValueError, match="rate"):
        linkcheck.linkcheck(SAMPLE, rate=0)


@pytest.mark.network
def test_linkcheck_real_links_answer_200():
    results = linkcheck.linkcheck(SAMPLE, ids=["sample-sans-01"])
    assert [r.field for r in results] == ["license.text_url", "links.primary", "links.designer"]
    assert [r.status for r in results] == [200, 200, 200], results
