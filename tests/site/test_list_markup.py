"""The list page's markup and layout (A2: index/_list/_row/_filters templates, 20-list.css and
25-filters.css), checked on a built site against site/CONTRACT.md sections 3, 4, 6 and 9.

Static tests parse the built ``index.html``. Browser tests set the few attributes the page
script would (``hidden`` on ``#filters``, ``aria-expanded`` on ``#f-toggle``) themselves, so
they test the markup and CSS whether or not the list script has landed.

Fixtures: ``site_data``, ``site_dir`` and ``site_url`` from tests/site/conftest.py, and
pytest-playwright's ``browser``.
"""

import json
import re
import time
import tomllib
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tff_site import build, data, views

ROOT = Path(__file__).resolve().parents[2]

VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}
    | {"track", "wbr"}
)
BADGE_ORDER = list(build.BADGE_TEXT)


# ------------------------------------------------------------------ a tiny DOM for the tests


class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent: Node | None) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node | str] = []

    def iter(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def find_all(self, tag: str | None = None, **attrs: str) -> list[Node]:
        return [n for n in self.iter() if n.matches(tag, attrs)]

    def find(self, tag: str | None = None, **attrs: str) -> Node | None:
        found = self.find_all(tag, **attrs)
        return found[0] if found else None

    def matches(self, tag: str | None, attrs: dict[str, str]) -> bool:
        if tag and self.tag != tag:
            return False
        for key, want in attrs.items():
            key = key.rstrip("_").replace("_", "-")
            if key == "class":
                if want not in (self.attrs.get("class") or "").split():
                    return False
            elif self.attrs.get(key) != want:
                return False
        return True

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    @property
    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text for c in self.children)

    def elements(self) -> list[Node]:
        return [c for c in self.children if isinstance(c, Node)]


class TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {}, None)
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, dict(attrs), self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        assert node is not None, f"stray </{tag}>"
        assert node is self.current, f"</{tag}> closes <{self.current.tag}> early"
        self.current = node.parent

    def handle_data(self, text):
        self.current.children.append(text)


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(scope="module")
def html(site_dir: Path) -> str:
    return (site_dir / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dom(html: str) -> Node:
    builder = TreeBuilder()
    builder.feed(html)
    builder.close()
    assert builder.current is builder.root, "unclosed elements"
    return builder.root


@pytest.fixture(scope="module")
def doc(site_data: Path) -> dict:
    """The catalog as the site shows it: the views' wording from config/site.toml
    (view_labels_short, tff_site.views)."""
    return views.apply(json.loads(Path(site_data).read_text(encoding="utf-8")), views.load())


# ------------------------------------------------------------------------- static markup


def test_page_outline_and_list_semantics(dom):
    assert len(dom.find_all("h1")) == 1
    h2s = dom.find_all("h2")
    # The list's heading only: the note is on the About page (why_not_listed_to_about). On
    # phones it is hidden from sight, but screen readers and headings navigation keep it.
    assert [h.attrs.get("id") for h in h2s] == ["results-h"]
    assert h2s[0].classes == ["visually-hidden-phone"]
    assert squash(h2s[0].text) == "Fonts"
    results = dom.find("section", id="results")
    assert results.attrs["aria-labelledby"] == "results-h"
    ol = dom.find("ol", id="list")
    assert ol.attrs.get("role") == "list"  # Safari drops list semantics under list-style: none
    assert ol.attrs["data-index"].startswith("/assets/list.")
    assert ol.attrs["data-details"].startswith("/assets/details.")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", ol.attrs["data-run-date"])
    assert all(child.tag == "li" and "font" in child.classes for child in ol.elements())


def test_ids_are_unique_and_every_reference_resolves(dom):
    ids = [n.attrs["id"] for n in dom.iter() if n.attrs.get("id")]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    known = set(ids)
    for node in dom.iter():
        for attr in ("for", "aria-describedby", "aria-labelledby", "aria-controls"):
            for ref in (node.attrs.get(attr) or "").split():
                assert ref in known, f"<{node.tag} {attr}={ref!r}> points nowhere"


def test_no_inline_code_and_no_form(html):
    assert "<form" not in html
    assert "<style" not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\sstyle\s*=", html)
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", html)


def test_rows_are_in_server_order_with_overall_labels(dom, doc):
    ordered = data.server_order(doc)
    rows = dom.find("ol", id="list").elements()
    assert [li.attrs["data-id"] for li in rows] == [f["id"] for f in ordered]
    cells = build.rank_cells(ordered)
    assert [squash(li.find(class_="rank").text) for li in rows] == [c["label"] for c in cells]
    labels = [squash(li.find(class_="rank").text) for li in rows]
    assert any(re.fullmatch(r"Score \d{1,3} of 100", label) for label in labels)
    assert any(label.startswith("Not ranked: ") for label in labels) or not any(
        f["ranks"]["overall"]["order"] is None for f in doc["fonts"]
    )


def test_the_held_legend_shows_when_the_server_list_has_a_held_font(dom, doc):
    """Owner ruling of 2026-09-30 (held_marker_style): the hollow bar's legend, word for word,
    is in the page whenever a row the server draws is held, so it explains the bar without
    scripts too; the script then follows the view (#view-note starts hidden)."""
    legend = dom.find("p", id="held-legend")
    rulings = tomllib.loads((ROOT / "data/reviews/site/2026-09-30.toml").read_text())
    assert squash(legend.text) in rulings["held_marker_style"]["ruling"]
    held = [
        f
        for f in doc["fonts"]
        if (e := f["ranks"]["overall"])["order"] is not None and e["gate_held"]
    ]
    assert ("hidden" not in legend.attrs) == bool(held)
    assert "hidden" in dom.find("p", id="view-note").attrs


def test_each_row_has_its_parts(dom, doc):
    fonts = {f["id"]: f for f in doc["fonts"]}
    for li in dom.find("ol", id="list").elements():
        font_id = li.attrs["data-id"]
        font = fonts[font_id]
        family = font["family"]
        assert li.attrs["id"] == f"font-{font_id}"
        row = li.elements()[0]
        assert row.classes == ["font-row"]
        # rank first, then the title: Render redraws only the .rank cell, which holds the
        # score and its bar (score_display), or the "Not ranked" text alone
        assert [c.classes[0] for c in row.elements()[:2]] == ["rank", "font-title"]
        cell = row.elements()[0]
        if li.classes == ["font", "is-unranked"]:
            assert cell.elements() == []
        else:
            before, after, bar = cell.elements()
            assert [before.classes, after.classes] == [["visually-hidden"]] * 2
            number = int(re.fullmatch(r"Score (\d+) of 100.*", squash(cell.text))[1])
            assert (bar.tag, bar.classes, bar.elements(), bar.text) == (
                "i",
                ["bar", f"b{number}"],
                [],
                "",
            )
            assert ("is-held" in cell.classes) == squash(cell.text).endswith(
                ", from one kind of source"
            )
        title = row.elements()[1]
        heading = title.elements()[0]
        assert (heading.tag, heading.classes) == ("h3", ["font-name"])
        assert heading.attrs["id"] == f"font-{font_id}-name"
        assert squash(heading.text) == family
        assert len(li.find_all("h3")) == 1

        # The title is the heading and the specimen box, nothing else: the Nerd Font marker
        # became a tag (the owner's site ruling of 2026-10-05, nerd_tag), so no row has one.
        assert row.find_all("span", class_="nf-mark") == []
        spec = title.elements()[1]
        assert len(title.elements()) == 2

        # With a specimen the title is has-spec, so the heading lies over the drawn name
        # (owner ruling of 2026-09-29, name_once). The specimen is decorative for screen
        # readers, since the heading names the font (2026-10-05, specimen_label_hidden).
        assert spec.classes == ["font-spec"]
        span = spec.find("span", class_="spec")
        fallback = spec.find("p", class_="spec-fallback")
        assert (span is None) != (fallback is None), font_id
        assert title.classes == ["font-title"] + (["has-spec"] if span is not None else [])
        if span is not None:
            assert span.attrs["aria-hidden"] == "true"
            assert "role" not in span.attrs
            assert "aria-label" not in span.attrs
            img = spec.find("noscript").find("img")
            assert img.attrs["src"] == span.attrs["data-src"]
            assert img.attrs["alt"] == ""
            assert img.attrs["loading"] == "lazy"
            assert int(img.attrs["width"]) > 0
            assert int(img.attrs["height"]) > 0
        elif not font["preview_ok"]:
            assert squash(fallback.text).startswith(
                "No preview: this font's license doesn't let us host its files. See it on "
            )
            link = fallback.find("a")
            assert link.attrs["href"] == font["links"]["primary"]["url"]
            assert squash(link.text) == data.destination_name(font["links"]["primary"])
        else:
            assert squash(fallback.text) == "Preview not available yet."

        meta = row.find("p", class_="font-meta")
        category = data.CATEGORY_LABELS[data.site_category(font)]  # Monospace if monospaced
        assert squash(meta.find(class_="font-cat").text) == category
        assert squash(meta.find(class_="font-lic").text) == font["license"]["name"]

        download = row.find("a", class_="download")
        assert download.attrs["href"] == font["links"]["primary"]["url"]
        destination = data.destination_name(font["links"]["primary"])
        assert squash(download.text).startswith(f"Download from {destination}")
        assert family in download.text  # a unique name for each row's link (2.4.4)

        toggle = row.find("button", class_="details-toggle")
        # The row ends with its two actions, the download link first, so the keyboard
        # reaches them in that order (download_button changes only their look).
        assert row.elements()[-2:] == [download, toggle]
        assert toggle.attrs["type"] == "button"
        assert toggle.attrs["aria-expanded"] == "false"
        assert toggle.attrs["aria-controls"] == f"details-{font_id}"
        assert squash(toggle.text) == f"Details for {family}"
        details = li.elements()[1]
        assert details.tag == "div"
        assert details.classes == ["details"]
        assert details.attrs["id"] == f"details-{font_id}"
        assert "hidden" in details.attrs
        assert details.children == []


def test_badges_follow_the_contract_order(dom, doc):
    fonts = {f["id"]: f for f in doc["fonts"]}
    seen: set[str] = set()
    for li in dom.find("ol", id="list").elements():
        badges = li.find("ul", class_="badges")
        font = fonts[li.attrs["data-id"]]
        nerd = font["links"]["nerd"] is not None
        if badges is None:
            assert not nerd, "a Nerd Font build always has its tag"
            continue
        assert badges.attrs.get("role") == "list"
        assert badges.attrs["aria-label"] == "Tags"
        keys = [b.attrs["data-badge"] for b in badges.elements()]
        assert keys == sorted(keys, key=BADGE_ORDER.index)
        assert keys, "an empty tag list is left out"
        seen.update(keys)
        assert ("variable" in keys) == font["formats"]["variable"]
        assert ("attribution" in keys) == font["license"]["attribution_required"]
        # The owner's site ruling of 2026-10-05 (nerd_tag): a tag like "Adjustable weight".
        assert ("nerd" in keys) == nerd
        if nerd:
            tag = badges.find("li", data_badge="nerd")
            assert squash(tag.text) == "Nerd Font available"
            assert tag.classes == ["badge"]
    if doc.get("synthetic"):
        assert seen == set(BADGE_ORDER), set(BADGE_ORDER) - seen


def test_filters_ship_hidden_inside_search(dom):
    search = dom.find("search", id="filters")
    assert search is not None
    assert "hidden" in search.attrs
    assert search.attrs["aria-label"]
    assert dom.find("form") is None
    for control in search.find_all("input") + search.find_all("select"):
        cid = control.attrs.get("id")
        wrapped = control.parent.tag == "label"
        labelled = cid and dom.find("label", for_=cid) is not None
        assert wrapped or labelled, f"{cid} has no label"
    for fieldset in search.find_all("fieldset"):
        assert fieldset.elements()[0].tag == "legend", fieldset.attrs.get("id")
    note = dom.find("noscript")
    assert note is not None
    assert "Filters and search need JavaScript" in note.text


def radios(search: Node, name: str) -> list[tuple[str, str, bool, str]]:
    out = []
    for inp in search.find_all("input", name=name):
        label = squash(inp.parent.text) if inp.parent.tag == "label" else ""
        out.append((inp.attrs["id"], inp.attrs["value"], "checked" in inp.attrs, label))
    return out


def test_filter_controls_match_the_hash(dom, doc):
    """CONTRACT section 9: each control's name is its hash key and its value the key's value."""
    search = dom.find("search", id="filters")
    shown = [v for v in doc["views"] if v["available"]]
    select = search.find("select", id="f-rank")
    assert select.attrs["name"] == "rank"
    assert select.attrs["aria-describedby"] == "f-rank-measures"
    options = select.find_all("option")
    assert [(o.attrs["value"], squash(o.text)) for o in options] == [
        (v["key"], v["label"]) for v in shown
    ]
    assert "selected" in options[0].attrs
    assert shown[0]["key"] == "overall"
    # config/site.toml's names, whatever the catalog's copies say (view_labels_short).
    assert [squash(o.text) for o in options] == [
        views.load()[o.attrs["value"]]["label"] for o in options
    ]
    project = [v for v in shown if v["key"] == "project"]
    assert not project or project[0]["label"] == "Projects"  # site ruling 2026-10-07
    measures = search.find(id="f-rank-measures")
    assert squash(measures.text) == shown[0]["measures"]
    # The select's label (selector_label, 2026-10-05). On phones the labels and the measures
    # line are hidden from sight but stay the names and the description
    # (mobile_first_screen_two_fonts, 2026-10-06), and the search box shows its label inside.
    rank_label = dom.find("label", for_="f-rank")
    assert squash(rank_label.text) == "Measure"
    q_label = dom.find("label", for_="f-q")
    assert squash(q_label.text) == "Search fonts"
    for hidden in (rank_label, q_label, measures):
        assert "visually-hidden-phone" in hidden.classes, hidden.attrs

    q = search.find("input", id="f-q")
    assert (q.attrs["name"], q.attrs["type"], q.attrs["maxlength"]) == ("q", "search", "100")
    # The placeholder, shorter than the label so it shows whole beside the select, starts the
    # accessible name (2.5.3, label in name).
    assert q.attrs["placeholder"] == "Search"
    assert squash(q_label.text).startswith(q.attrs["placeholder"])

    assert radios(search, "cat") == [("f-cat-all", "", True, "Any")] + [
        (f"f-cat-{k}", k, False, v) for k, v in data.CATEGORY_LABELS.items()
    ]
    assert "pills" in search.find(id="f-cat").classes
    # Owner rulings of 2026-09-30: no Spacing, license-group or redistribution filter.
    for name in ("spacing", "lic", "redist"):
        assert search.find_all("input", name=name) == [], name
    for gone in ("f-mono", "f-text", "f-spacing", "f-redist", "f-sort-rank"):
        assert dom.find(id=gone) is None, gone
    credit = any(f["license"]["attribution_required"] for f in doc["fonts"])
    assert radios(search, "hide") == [
        ("f-hide-limited", "limited", False, "Accented letters"),
        *([("f-hide-attr", "attr", False, "No credit required")] if credit else []),
    ]
    systems = search.find("select", id="f-os")
    assert systems.attrs["name"] == "hide"
    assert squash(dom.find("label", for_="f-os").text) == "Hide fonts that come with"
    assert [(o.attrs["value"], squash(o.text)) for o in systems.find_all("option")] == [
        ("", "Nothing"),
        *data.OS_LABELS.items(),
    ]
    # owner ruling of 2026-09-30 (variable_label): not "Variable", which reads as "proportional"
    assert radios(search, "var") == [("f-var", "1", False, "Adjustable weight (variable font)")]
    assert radios(search, "nerd") == [("f-nerd", "1", False, "Nerd Font available")]
    nerd = search.find("input", id="f-nerd")
    assert nerd.parent.parent.attrs["id"] == "f-type"  # beside "Adjustable weight"
    assert "aria-describedby" not in nerd.attrs  # no legend to point to (nerd_tag)
    assert not any("checked" in i.attrs for i in search.find_all("input", type="checkbox"))
    # Sort: buttons over the list's columns, outside the filters, hidden until the script
    # shows them (owner ruling of 2026-09-30, sort_header).
    bar = dom.find("div", id="list-sort")
    assert "hidden" in bar.attrs
    assert (bar.attrs["role"], bar.attrs["aria-label"]) == ("group", "Sort the list")
    assert search.find(class_="sort-btn") is None
    buttons = bar.find_all("button")
    assert [(b.attrs["id"], b.attrs["data-sort"], b.attrs["aria-pressed"]) for b in buttons] == [
        ("sort-rank", "rank", "true"),
        ("sort-name", "name", "false"),
    ]
    # "Popularity" over the scores, and its orders' words, without "first" (owner's site
    # rulings of 2026-09-30 and 2026-10-07, score_column_popularity and sort_words_most_least).
    rank, name = buttons
    orders = ("most popular", "least popular")
    assert (rank.attrs["data-asc"], rank.attrs["data-desc"]) == orders
    assert (rank.attrs["data-asc-spoken"], rank.attrs["data-desc-spoken"]) == orders
    assert rank.attrs["title"] == "Show least popular instead"
    assert (name.attrs["data-asc"], name.attrs["data-desc"]) == ("A\u2013Z", "Z\u2013A")
    assert (name.attrs["data-asc-spoken"], name.attrs["data-desc-spoken"]) == ("A to Z", "Z to A")
    assert rank.attrs["data-dir"] == "asc"
    assert "data-dir" not in name.attrs
    assert squash(rank.text) == (
        "Sort by Popularity most popular most popular; select to show least popular"
    )
    assert squash(name.text) == "Sort by Name"
    for button in buttons:
        assert button.find("span", class_="sort-arrow").attrs["aria-hidden"] == "true"
    toggle = search.find("button", id="f-toggle")
    assert toggle.attrs["aria-controls"] == "f-more"
    assert toggle.attrs["aria-expanded"] == "false"
    assert toggle.find("span", class_="filters-count") is not None
    assert search.find("button", id="f-clear") is not None


def test_the_nerd_font_tag_replaces_the_marker_and_legend(dom, doc):
    """The owner's site ruling of 2026-10-05 (nerd_tag): a font with a Nerd Font build has the
    tag "Nerd Font available", like "Adjustable weight"; the "NF" marker and the legend above
    the list (nerd_legend, 2026-09-29) are gone, though the catalog keeps their wording."""
    assert build.BADGE_TEXT["nerd"] == "Nerd Font available"
    assert dom.find(id="nf-legend") is None
    assert dom.find_all(class_="nf-mark") == []
    assert doc["nerd"]["legend"] not in squash(dom.text)
    tagged = {
        li.attrs["data-id"]
        for li in dom.find("ol", id="list").elements()
        if li.find("li", class_="badge", data_badge="nerd") is not None
    }
    assert tagged == {f["id"] for f in doc["fonts"] if f["links"]["nerd"] is not None}
    assert tagged, "the data has a font with a Nerd Font build"
    results = dom.find("section", id="results").elements()
    assert [n.attrs.get("id") for n in results[:4]] == [
        "results-h",
        "ext-summary",
        "count",
        "held-legend",
    ]
    assert results[-2].attrs["id"] == "list-sort"  # the sort buttons sit right over the list


SITE_RULINGS_0930 = tomllib.loads(
    (ROOT / "data" / "reviews" / "site" / "2026-09-30.toml").read_text(encoding="utf-8")
)


def test_the_front_page_stops_after_the_lead(dom):
    """The owner's site rulings of 2026-10-07: the heading, the lead and then the list, on
    every screen. The note "Why isn't my favorite free font here?" moved to the About page
    (why_not_listed_to_about; test_pages.py) and the privacy line to every page's footer
    (privacy_line_in_footer; test_shell.py). On phones the lead shows its first sentence,
    and the second is hidden from sight only (mobile_first_screen_two_fonts)."""
    main = dom.find("main")
    kids = [" ".join(n.classes) or n.tag for n in main.elements()]
    assert kids == ["list-title", "lead", "layout"]
    assert squash(main.find("h1").text) == "The most popular purely free fonts"
    for gone in ("why", "why-wide", "why-fold", "privacy-note"):
        assert dom.find(class_=gone) is None, gone
    assert "Why isn't my favorite free font here?" not in squash(main.text)
    assert "Check the Network tab" not in squash(main.text)
    # The lead, in the owner's wording of 2026-09-30 (front_page_lead_sharing).
    lead = main.find("p", class_="lead")
    change = SITE_RULINGS_0930["front_page_lead_sharing"]["ruling"]
    assert f'"{squash(lead.text)}"' in change
    (rest,) = lead.elements()
    assert (rest.tag, rest.classes) == ("span", ["visually-hidden-phone"])
    first = squash(lead.children[0])
    assert first.endswith("."), first
    assert first.count(".") == 1, first
    assert squash(lead.text) == f"{first} {squash(rest.text)}"


def test_count_and_no_results(dom, doc):
    n = len(doc["fonts"])
    assert squash(dom.find(id="count").text) == f"Showing {n} of {n} fonts"
    no_results = dom.find(id="no-results")
    assert "hidden" in no_results.attrs
    assert no_results.find("button", id="no-results-clear") is not None
    status = dom.find(id="status")
    assert status.attrs["role"] == "status"
    assert status.children == []
    assert "hidden" in dom.find(id="ext-summary").attrs


# ------------------------------------------------------------------------------- browser

SHOW_FILTERS = "() => { document.getElementById('filters').hidden = false; }"
OPEN_PANEL = """(open) => { document.getElementById('filters').hidden = false;
  document.getElementById('f-toggle').setAttribute('aria-expanded', String(open)); }"""
SCROLL_WIDTH = "() => document.documentElement.scrollWidth"
TARGETS = """() => {
  const small = [];
  for (const el of document.querySelectorAll('main a, main button, main input, main select')) {
    const r = el.getBoundingClientRect();
    if (!r.width || getComputedStyle(el).visibility === 'hidden') continue;
    const inText = el.tagName === 'A' && el.closest('p');     // 2.5.8's inline exception
    if (!inText && (r.width < 24 || r.height < 24)) small.push(el.id || el.className || el.tagName);
  }
  return small;
}"""


def open_page(browser, site_url, width, **kw):
    context = browser.new_context(viewport={"width": width, "height": 900}, **kw)
    page = context.new_page()
    page.goto(site_url + "/")
    if kw.get("java_script_enabled", True):
        # The list script shows #filters once its data has loaded; wait for it, or a test
        # that hides the block itself races the script showing it again.
        page.wait_for_function("() => !document.getElementById('filters').hidden")
    return context, page


@pytest.mark.parametrize("width", [375, 1280])
def test_without_javascript_the_list_stands_alone(browser, site_url, width, dom):
    context, page = open_page(browser, site_url, width, java_script_enabled=False)
    try:
        assert page.evaluate("() => matchMedia('(scripting: none)').matches")
        assert page.evaluate("() => document.getElementById('filters').offsetHeight") == 0
        assert page.is_visible(".noscript-note")
        shown = page.evaluate(
            "() => [...document.querySelectorAll('.details-toggle')]"
            ".filter(b => getComputedStyle(b).display !== 'none').length"
        )
        assert shown == 0, "a details button that can open nothing"
        assert page.locator("img.spec-img").count() == len(dom.find_all("span", class_="spec"))
        assert page.evaluate(SCROLL_WIDTH) <= width
    finally:
        context.close()


@pytest.mark.parametrize("width", [320, 375, 640, 800, 1280])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_sideways_scroll_and_24px_targets(browser, site_url, width, scheme):
    context, page = open_page(browser, site_url, width, color_scheme=scheme)
    try:
        page.evaluate(OPEN_PANEL, False)
        assert page.evaluate(SCROLL_WIDTH) <= width
        page.evaluate(OPEN_PANEL, True)
        assert page.evaluate(SCROLL_WIDTH) <= width
        assert page.evaluate(TARGETS) == []
    finally:
        context.close()


ROW_OVERFLOW = """() => {
  const bad = [];
  const lis = [...document.querySelectorAll('li.font')];
  for (const li of lis) li.style.setProperty('content-visibility', 'visible');
  for (const li of lis) {
    const edge = li.getBoundingClientRect();
    for (const el of li.querySelectorAll('.font-row > *, .font-row a, .badge')) {
      const r = el.getBoundingClientRect();
      if (!r.width) continue;
      // content-visibility clips at the li, so overflow would be cut off, not scrollable
      const inline = getComputedStyle(el).display === 'inline';   // no clientWidth to compare
      if (r.left < edge.left - 0.5 || r.right > edge.right + 0.5 ||
          (!inline && el.scrollWidth > el.clientWidth + 1))
        bad.push(li.id + ' ' + (el.className || el.tagName));
    }
  }
  return bad.slice(0, 10);
}"""


@pytest.mark.parametrize("width", [320, 640, 1280])
def test_nothing_in_a_row_is_wider_than_the_row(browser, site_url, width):
    context, page = open_page(browser, site_url, width)
    try:
        assert page.evaluate(ROW_OVERFLOW) == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [375, 800, 1280])
def test_showing_the_filters_does_not_move_the_list(browser, site_url, width):
    context, page = open_page(browser, site_url, width)
    try:
        where = "() => JSON.stringify(document.getElementById('results').getBoundingClientRect())"
        page.evaluate("() => { document.getElementById('filters').hidden = true; }")
        before = page.evaluate(where)
        # hidden keeps the space but shows nothing, and nothing in it can take focus
        assert not page.is_visible("#f-q")
        assert not page.is_visible("#f-rank")
        page.evaluate(SHOW_FILTERS)
        assert page.evaluate(where) == before
        assert page.is_visible("#f-q")
        assert page.is_visible("#f-rank")
    finally:
        context.close()


def test_narrow_screens_put_the_filters_behind_the_toggle(browser, site_url):
    context, page = open_page(browser, site_url, 375)
    try:
        page.evaluate(OPEN_PANEL, False)
        assert page.is_visible("#f-q")
        assert page.is_visible("#f-rank")
        assert page.is_visible("#f-toggle")
        assert not page.is_visible("#f-more")
        page.evaluate(OPEN_PANEL, True)
        assert page.is_visible("#f-more")
        assert page.is_visible("#f-os")
        assert page.is_visible("#list-sort")  # Sort sits over the list, outside the panel
    finally:
        context.close()


def test_wide_screens_show_every_filter_in_the_sidebar(browser, site_url):
    context, page = open_page(browser, site_url, 1280)
    try:
        page.evaluate(OPEN_PANEL, False)
        assert not page.is_visible("#f-toggle")
        assert page.is_visible("#f-more")
        assert page.is_visible("#f-clear")
        side = page.locator("#filters").bounding_box()
        results = page.locator("#results").bounding_box()
        assert side["x"] + side["width"] <= results["x"]
        assert abs(side["y"] - results["y"]) < 1
    finally:
        context.close()


@pytest.mark.parametrize("width", [375, 700, 1280])
def test_milestone_3_notes_follow_the_row_actions(browser, site_url, width):
    """div.ext goes after download and Details, whether or not the row has tags."""
    context, page = open_page(browser, site_url, width)
    try:
        result = page.evaluate(
            """() => {
              const out = [];
              const rows = [...document.querySelectorAll('li.font')];
              const pick = [rows.find(li => li.querySelector('.badges')),
                            rows.find(li => !li.querySelector('.badges'))].filter(Boolean);
              for (const li of pick) {
                li.style.setProperty('content-visibility', 'visible');
                const row = li.querySelector('.font-row');
                for (const n of [1, 2]) {
                  const d = document.createElement('div');
                  d.className = 'ext';
                  d.dataset.filter = 'f' + n;
                  const p = document.createElement('p');
                  p.className = 'ext-note';
                  p.textContent = 'Note ' + n;
                  d.append(p);
                  row.append(d);
                }
                const bottom = Math.max(...['.download', '.details-toggle'].map(
                  s => row.querySelector(s).getBoundingClientRect().bottom));
                const tops = [...row.querySelectorAll('.ext')].map(e => e.getBoundingClientRect().top);
                out.push({id: li.id, bottom, tops});
              }
              return out;
            }"""
        )
        assert len(result) == 2, "the sample needs rows with and without tags"
        for row in result:
            assert row["tops"][0] >= row["bottom"] - 0.5, row
            assert row["tops"][1] > row["tops"][0], row
    finally:
        context.close()


# Each row's title, laid out in full: the specimen box, the heading and the score cell.
TITLE_LAYOUT = """() => Array.from(document.querySelectorAll('li.font'), (li) => {
  li.style.setProperty('content-visibility', 'visible');
  const mid = (r) => r.top + r.height / 2;
  const title = li.querySelector('.font-title').getBoundingClientRect();
  const span = li.querySelector('.font-title span.spec');
  const box = span && span.getBoundingClientRect();
  const h = li.querySelector('.font-title > h3.font-name').getBoundingClientRect();
  const rank = li.querySelector('.font-row > .rank');
  const r = rank.getBoundingClientRect();
  const bar = rank.querySelector('.bar');
  return { id: li.id, unranked: li.classList.contains('is-unranked'), hasBox: Boolean(box),
           titleLeft: title.left, titleWidth: title.width,
           boxLeft: box && box.left, boxWidth: box && box.width, boxHeight: box && box.height,
           boxMid: box && mid(box), headTop: h.top, headHeight: h.height, headMid: mid(h),
           rankTop: r.top, rankMid: mid(r), barMid: bar && mid(bar.getBoundingClientRect()) };
})"""


@pytest.mark.parametrize("width", [320, 375, 700, 1280])
def test_the_score_and_the_heading_are_centred_on_the_specimen(browser, site_url, width):
    """The owner's site rulings of 2026-10-05: the specimen box takes the title's full width,
    with no column kept for a marker (nerd_tag); a ranked row's score and bar are centred
    vertically on the box, and the heading, hidden once the specimen shows (name_once), is
    centred on it too, over the drawn name, so find-in-page highlights it there
    (score_centred). Phone cards (320, 375), the table rows from 40rem (700) and the wide rows
    (1280). A heading that wraps taller than the box starts at the box's top; a row without a
    specimen keeps its score level with its heading."""
    context, page = open_page(browser, site_url, width)
    try:
        rows = page.evaluate(TITLE_LAYOUT)
        boxed = [r for r in rows if r["hasBox"]]
        assert boxed, "no row has a specimen"
        box_px = 48 if width < 640 else 64  # --spec-h (specimen_box_heights)
        for r in boxed:
            assert r["boxHeight"] == box_px, r
            assert abs(r["boxLeft"] - r["titleLeft"]) <= 0.5, r
            assert abs(r["boxWidth"] - r["titleWidth"]) <= 0.5, r  # the title's full width
            if r["headHeight"] <= r["boxHeight"] + 0.5:
                assert abs(r["headMid"] - r["boxMid"]) <= 1, r
            else:
                assert abs(r["headTop"] - (r["boxMid"] - box_px / 2)) <= 1, r
            if not r["unranked"]:
                assert abs(r["rankMid"] - r["boxMid"]) <= 1, r
                assert abs(r["barMid"] - r["boxMid"]) <= 1, r
        for r in rows:
            if not r["hasBox"] and not r["unranked"]:
                assert abs(r["rankTop"] - r["headTop"]) <= 0.5, r
    finally:
        context.close()


# Each row's two actions, laid out in full: their boxes and computed look, and the token
# values they should take, read through a probe (CSSOM, which the CSP allows).
ACTIONS_LOOK = """() => {
  const probe = (prop, token) => {
    const el = document.createElement('span');
    el.style.setProperty(prop, `var(${token})`);
    document.body.append(el);
    const value = getComputedStyle(el).getPropertyValue(prop);
    el.remove();
    return value;
  };
  const tokens = { accent: probe('background-color', '--c-accent'),
                   accentFg: probe('color', '--c-accent-fg'), fg: probe('color', '--c-fg'),
                   border: probe('border-top-color', '--c-border'),
                   link: probe('color', '--c-link') };
  const look = (el) => {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return { tag: el.tagName, top: r.top, height: r.height, width: r.width,
             bg: s.backgroundColor, color: s.color, border: s.borderTopColor,
             borderWidth: parseFloat(s.borderTopWidth), borderStyle: s.borderTopStyle,
             weight: parseInt(s.fontWeight, 10), underline: s.textDecorationLine };
  };
  const rows = Array.from(document.querySelectorAll('li.font'), (li) => {
    li.style.setProperty('content-visibility', 'visible');
    return { id: li.id, download: look(li.querySelector('.download')),
             toggle: look(li.querySelector('.details-toggle')) };
  });
  return { tokens, rows };
}"""


@pytest.mark.parametrize("width", [375, 700, 1280])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_download_is_the_button_and_details_the_quieter_one(browser, site_url, width, scheme):
    """The owner's site ruling of 2026-10-05 (download_button): a row's "Download from ..."
    link has the strong, button-like look, filled with the accent, and stays a link; the
    Details button is the quieter one, an outline with no fill, its border a control's
    (--c-border, 3:1 on the page: 1.4.11 and docs/milestone-2.md). On one line the two are
    the same height and level, each at least 24px square (2.5.8); a long destination wraps
    inside the download button. Phone cards, table rows and wide rows; the forced-colours
    look is checked in test_a11y.py."""
    context, page = open_page(browser, site_url, width, color_scheme=scheme)
    try:
        got = page.evaluate(ACTIONS_LOOK)
        tokens = got["tokens"]
        assert tokens["accent"] != tokens["border"]
        for row in got["rows"]:
            download, toggle = row["download"], row["toggle"]
            assert download["tag"] == "A", row
            assert download["bg"] == download["border"] == tokens["accent"], row
            assert download["color"] == tokens["accentFg"], row
            assert download["weight"] >= 600, row
            assert download["underline"] == "none", row
            assert toggle["tag"] == "BUTTON", row
            assert toggle["bg"] in {"rgba(0, 0, 0, 0)", "transparent"}, row
            assert toggle["border"] == tokens["border"], row
            assert toggle["color"] == tokens["fg"], row
            for action in (download, toggle):
                assert action["borderStyle"] == "solid", row
                assert action["borderWidth"] >= 1, row
                assert action["width"] >= 24, row
                assert action["height"] >= 24, row
            if download["height"] < toggle["height"] + 10:  # the download on one line
                assert abs(download["height"] - toggle["height"]) <= 0.5, row
                assert abs(download["top"] - toggle["top"]) <= 0.5, row
            else:  # wrapped: taller than Details, never shorter
                assert download["height"] > toggle["height"], row
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_download_prints_as_a_link(browser, site_url, scheme):
    """Browsers drop backgrounds when printing, which would leave the Download button's text
    in the accent's text colour (white in the light theme) on the paper: in print it is an
    underlined link in the link colour (download_button)."""
    context, page = open_page(browser, site_url, 1280, color_scheme=scheme)
    try:
        page.emulate_media(media="print")
        got = page.evaluate(ACTIONS_LOOK)
        for row in got["rows"]:
            download = row["download"]
            assert download["bg"] in {"rgba(0, 0, 0, 0)", "transparent"}, row
            assert download["color"] == got["tokens"]["link"], row
            assert download["underline"] == "underline", row
    finally:
        context.close()


FRAMES = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"
# The script has shown the filters, and the results are in sight (html[data-list-pending]).
SETTLED = (
    "() => !document.getElementById('filters').hidden"
    " && !document.documentElement.hasAttribute('data-list-pending')"
)
# The top of the list page, laid out: where the first rows start, the parts above them, and
# which parts are hidden from sight (the visually-hidden clip) on this screen.
FIRST_SCREEN = """() => {
  const box = (el) => { const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width,
             height: r.height }; };
  const q = (s) => document.querySelector(s);
  const unseen = (el) => getComputedStyle(el).clipPath === 'inset(50%)';
  const hide = ['label[for="f-q"]', 'label[for="f-rank"]', '#f-rank-measures', '#results-h',
                '.lead > span'];
  return { vh: innerHeight, scrollWidth: document.documentElement.scrollWidth,
           rows: [...document.querySelectorAll('#list > li.font')].slice(0, 3).map(box),
           controls: ['#f-q', '#f-rank', '#f-toggle'].map((s) => box(q(s))),
           labels: ['#sort-rank .sort-label', '#sort-name .sort-label'].map((s) => box(q(s))),
           sort: box(q('#list-sort')), count: box(q('#count')),
           heading: box(q('#results-h')), lead: box(q('.lead')),
           hidden: Object.fromEntries(hide.map((s) => [s, unseen(q(s))])),
           status: getComputedStyle(q('.site-status')).display,
           h1: parseFloat(getComputedStyle(q('h1')).fontSize),
           placeholder: getComputedStyle(q('#f-q'), '::placeholder').opacity };
}"""
# Whole font rows on the first screen, on load (owner's site ruling of 2026-10-06,
# mobile_first_screen_two_fonts): two at 375 x 812 and 390 x 844, one at 360 x 740, and at
# 320 x 568 the first row starts on the screen.
FIRST_SCREENS = {(375, 812): 2, (390, 844): 2, (360, 740): 1, (320, 568): 0}


def first_screen(browser, site_url, width, height, hash_=""):
    """A fresh context, and the list page in it once the script has shown the filters and the
    results (a link with filters in it keeps them out of sight until then)."""
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    page.goto(site_url + "/" + hash_)
    page.wait_for_function(SETTLED)
    return context, page


@pytest.mark.parametrize(("width", "height"), FIRST_SCREENS)
def test_fonts_fill_a_phones_first_screen(browser, site_url, width, height):
    """The owner's site rulings of 2026-10-06 (mobile_first_screen_two_fonts,
    early_line_hidden_on_phones): on a phone the header has no early-version line, the heading
    is smaller, the lead shows its first sentence, search, Measure and Filters share one row
    and the count sits in the sort row, so the fonts start high enough. The heading and the
    lead stay on the page."""
    context, page = first_screen(browser, site_url, width, height)
    try:
        got = page.evaluate(FIRST_SCREEN)
        whole = sum(row["bottom"] <= got["vh"] + 0.5 for row in got["rows"])
        assert whole >= FIRST_SCREENS[(width, height)], got
        assert got["rows"][0]["top"] < got["vh"], got
        assert got["scrollWidth"] <= width
        assert got["status"] == "none"
        assert got["h1"] == 24  # --fs-xl, not --fs-2xl
        assert all(got["hidden"].values()), got["hidden"]
        assert got["lead"]["height"] > 0  # the lead's first sentence shows
    finally:
        context.close()


# From this width the count fits beside the sort buttons (on two lines up to about 390 px).
COUNT_BESIDE_FROM = 337


@pytest.mark.parametrize("width", [320, 330, 344, 360, 375, 390])
@pytest.mark.parametrize("hash_", ["", "#cat=serif&var=1"], ids=["plain", "filtered"])
def test_phone_controls_and_count_share_their_rows(browser, site_url, width, hash_):
    """Search, the Measure select and the Filters button share one row from 336 px, and below
    that wrap in two, but never run off the screen; each is a target of at least 24 px. The
    button is as wide as its longest text, so the row is the same whatever the count of
    filters on. The count sits in the sort row after the sort buttons, its first line level
    with their labels, or, where it can't keep about 7.5em beside them (below 337 px, with
    "most popular" in the Popularity button, sort_words_most_least), on the next line."""
    context, page = first_screen(browser, site_url, width, 800, hash_)
    try:
        got = page.evaluate(FIRST_SCREEN)
        controls = got["controls"]
        lines = {round(c["top"]) for c in controls}
        assert len(lines) == (1 if width >= 336 else 2), controls
        if hash_:
            assert page.text_content("#f-toggle") == "Filters\u00a0(2)"
            plain, _ = first_screen(browser, site_url, width, 800)
            try:
                same = plain.pages[0].evaluate(FIRST_SCREEN)["controls"]
            finally:
                plain.close()
            for box, plain_box in zip(controls, same, strict=True):
                assert all(abs(box[k] - plain_box[k]) <= 0.5 for k in box), (box, plain_box)
        for c in controls:
            assert c["width"] >= 24, c
            assert c["height"] >= 24, c
            assert c["left"] >= 0, c
            assert c["right"] <= width, c
        assert got["placeholder"] == "1"  # the search box's label shows inside it
        sort, count = got["sort"], got["count"]
        if width >= COUNT_BESIDE_FROM:
            assert count["left"] >= sort["right"], got
            assert count["right"] <= width - 16 + 0.5, got
            label = got["labels"][0]
            assert abs(count["top"] - label["top"]) <= 4, got
            assert count["bottom"] <= sort["bottom"] + 0.5, got
        else:
            assert count["top"] >= sort["bottom"] - 0.5, got
        assert got["rows"][0]["top"] >= max(sort["bottom"], count["bottom"]), got
    finally:
        context.close()


def test_phone_controls_keep_their_names_and_description(browser, site_url, doc):
    """The visible labels became accessible names (mobile_first_screen_two_fonts): screen
    readers still hear "Search fonts" and "Measure", the select's measures line as its
    description, and the "Fonts" heading, which headings navigation finds."""
    from playwright.sync_api import expect

    context, page = first_screen(browser, site_url, 375, 812)
    try:
        expect(page.get_by_role("searchbox", name="Search fonts", exact=True)).to_have_count(1)
        select = page.get_by_role("combobox", name="Measure", exact=True)
        expect(select).to_have_count(1)
        expect(select).to_have_accessible_description(doc["views"][0]["measures"])
        expect(page.get_by_role("heading", name="Fonts", level=2, exact=True)).to_have_count(1)
    finally:
        context.close()


@pytest.mark.parametrize("width", [640, 768, 1280])
def test_wider_screens_keep_their_layout(browser, site_url, width):
    """From 40rem nothing is hidden from sight: the labels above the controls, the measures
    line, the "Fonts" heading with the count under it and the sort buttons below them, the
    lead's two sentences and the early-version line; the search box shows no placeholder."""
    context, page = first_screen(browser, site_url, width, 900)
    try:
        got = page.evaluate(FIRST_SCREEN)
        assert not any(got["hidden"].values()), got["hidden"]
        assert got["status"] == "block"
        assert got["h1"] == 32  # --fs-2xl
        assert got["placeholder"] == "0"
        heading, count, sort = got["heading"], got["count"], got["sort"]
        assert heading["bottom"] <= count["top"] + 0.5, got
        assert count["bottom"] <= sort["top"] + 0.5, got
        assert abs(count["left"] - heading["left"]) <= 0.5, got
        assert got["scrollWidth"] <= width
    finally:
        context.close()


# Layout shifts the page makes by itself (no recent input), from the first paint, each with
# the moved nodes and those of them above the results: not in #results (the count, the
# legends, the sort row and the rows) nor in the footer below them.
SHIFTS_JS = """
(() => {
  window.__shifts = [];
  const name = (n) => `${n.nodeName}#${n.id || ''}.${n.className || ''}`;
  new PerformanceObserver((entries) => {
    const below = [document.getElementById('results'), document.querySelector('footer')];
    for (const e of entries.getEntries()) {
      if (e.hadRecentInput) continue;
      const nodes = (e.sources || []).map((s) => s.node).filter(Boolean);
      window.__shifts.push({ v: e.value, nodes: nodes.map(name), above: nodes
        .filter((n) => !below.some((b) => b && b.contains(n))).map(name) });
    }
  }).observe({ type: 'layout-shift', buffered: true });
})();
"""
# The tops of the boxes above the results, and the count's place in the sort row.
ABOVE_JS = """() => {
  const box = (s) => document.querySelector(s).getBoundingClientRect();
  const above = ['h1', '.lead', '#f-q', '#f-rank', '#f-toggle', '#results'].map((s) =>
    [s, Math.round(box(s).top * 10) / 10]);
  const sort = box('#list-sort'), count = box('#count');
  return { above, count: [Math.round((count.top - sort.top) * 10) / 10,
                          Math.round(count.right * 10) / 10] };
}"""
# A filter change made by script, so no input excuses a shift: the second view, then Serif.
REFILTER_JS = """() => {
  const rank = document.getElementById('f-rank');
  rank.value = rank.options[1].value;
  rank.dispatchEvent(new Event('change', { bubbles: true }));
  document.getElementById('f-cat-serif').click();
}"""
# How long the list index is held back, so the page paints before the script's first render,
# as on a slow connection.
INDEX_DELAY_S = 0.3


@pytest.mark.parametrize(
    ("width", "height"),
    [(375, 812), (360, 740), (344, 740), (330, 740), (320, 568), (768, 1024), (1280, 900)],
)
@pytest.mark.parametrize("link", ["plain", "filtered", "one font", "none found"])
def test_the_phone_first_screen_does_not_shift(
    browser, browser_name, site_url, dom, width, height, link
):
    """Layout shift 0 on a phone's load (and a tablet's and a desktop's), adding up every
    shift, with the list index held back so the page paints first: the controls and the sort buttons ship hidden but keep their
    space, and the server's count is the script's. A link with a view or filters in it keeps
    the results and the footer out of sight until the first render (data-list-pending), so
    nothing moves: not the rows, with filters on or with the last font of the list searched
    for, whose row ends up first; not the footer, which comes up the screen when few rows are
    left; and not the count, when the no-results message comes above the sort row. On a
    phone's refilter nothing above the results moves, and the count, whose length changes,
    keeps its place in the sort row; inside the results the rows, and the legend that comes
    or goes with the view, change as on every screen, and the footer follows them."""
    if browser_name != "chromium":
        pytest.skip("the Layout Instability API is Chromium's")
    last = squash([h for h in dom.find_all("h3") if "font-name" in h.classes][-1].text)
    hash_ = {
        "plain": "",
        "filtered": "#cat=serif&var=1",
        "one font": "#q=" + last.replace(" ", "%20"),
        "none found": "#q=zzzzzz",
    }[link]

    def held(route):
        time.sleep(INDEX_DELAY_S)
        route.continue_()

    context = browser.new_context(viewport={"width": width, "height": height})
    try:
        context.add_init_script(SHIFTS_JS)
        context.route("**/assets/list.*.json", held)
        page = context.new_page()
        page.goto(site_url + "/" + hash_)
        page.wait_for_function(SETTLED)
        page.evaluate(FRAMES)
        shifts = page.evaluate("window.__shifts")
        assert sum(s["v"] for s in shifts) < 0.001, shifts
        if link == "one font":
            assert page.evaluate(FIRST_SCREEN)["rows"][0]["top"] < height
        if width >= 640:
            return  # wider screens show the measures line, and the count above the sort row
        before = page.evaluate(ABOVE_JS)
        page.evaluate(REFILTER_JS)
        page.wait_for_function("() => location.hash.includes('rank=')")
        page.evaluate(FRAMES)
        assert page.evaluate(ABOVE_JS) == before
        assert [s for s in page.evaluate("window.__shifts") if s["above"]] == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [320, 800, 1280])
def test_focus_rings_fit_inside_their_row(browser, site_url, width):
    """Rows use content-visibility, which clips painting to the li: rings must fit inside."""
    context, page = open_page(browser, site_url, width)
    try:
        clipped = page.evaluate(
            """() => {
              const bad = [];
              const lis = [...document.querySelectorAll('li.font')];
              for (const li of lis) li.style.setProperty('content-visibility', 'visible');
              const ring = parseFloat(getComputedStyle(document.documentElement)
                .getPropertyValue('--focus-width')) + parseFloat(getComputedStyle(
                document.documentElement).getPropertyValue('--focus-offset'));
              for (const el of document.querySelectorAll('li.font a, li.font button')) {
                const r = el.getBoundingClientRect();
                const li = el.closest('li.font').getBoundingClientRect();
                if (r.left - ring < li.left || r.right + ring > li.right ||
                    r.top - ring < li.top || r.bottom + ring > li.bottom - 1)
                  bad.push(el.closest('li.font').id + ' ' + el.className);
              }
              return bad;
            }"""
        )
        assert clipped == []
    finally:
        context.close()


def test_row_height_estimate_is_close(browser, site_url):
    """contain-intrinsic-size stands in for unrendered rows; keep it near the real median."""
    for width in (375, 1280):
        context, page = open_page(browser, site_url, width)
        try:
            est, median = page.evaluate(
                """() => {
                  const lis = [...document.querySelectorAll('li.font')];
                  const est = parseFloat(getComputedStyle(lis[0]).containIntrinsicHeight
                    .replace('auto', ''));
                  for (const li of lis) li.style.setProperty('content-visibility', 'visible');
                  const hs = lis.map(li => li.getBoundingClientRect().height).sort((a, b) => a - b);
                  return [est, hs[hs.length >> 1]];
                }"""
            )
            assert 0.7 * median <= est <= 1.3 * median, (width, est, median)
        finally:
            context.close()
