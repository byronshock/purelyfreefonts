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
import tomllib
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tff_site import build, data

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
    return json.loads(Path(site_data).read_text(encoding="utf-8"))


# ------------------------------------------------------------------------- static markup


def test_page_outline_and_list_semantics(dom):
    assert len(dom.find_all("h1")) == 1
    h2s = dom.find_all("h2")
    assert [h.attrs.get("id") for h in h2s] == ["why-h", "results-h"]  # the note's, the list's
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


def test_rows_are_in_server_order_with_default_view_labels(dom, doc):
    ordered = data.server_order(doc)
    rows = dom.find("ol", id="list").elements()
    assert [li.attrs["data-id"] for li in rows] == [f["id"] for f in ordered]
    cells = build.rank_cells(ordered)
    assert [squash(li.find(class_="rank").text) for li in rows] == [c["label"] for c in cells]
    labels = [squash(li.find(class_="rank").text) for li in rows]
    assert any(re.fullmatch(r"Score \d{1,3} of 100", label) for label in labels)
    assert any(label.startswith("Not ranked: ") for label in labels) or not any(
        f["ranks"][data.DEFAULT_VIEW]["order"] is None for f in doc["fonts"]
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
        if (e := f["ranks"][data.DEFAULT_VIEW])["order"] is not None and e["gate_held"]
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

        # The Nerd Font marker, right after the heading, only for a font with a Nerd Font build
        # (owner ruling of 2026-09-29): its text is the marker, its name says what it means.
        marks = row.find_all("span", class_="nf-mark")
        assert len(marks) == (font["links"]["nerd"] is not None), font_id
        if marks:
            mark = marks[0]
            assert title.elements()[1] is mark
            assert mark.attrs["role"] == "img"
            assert mark.attrs["aria-label"] == doc["nerd"]["label"]
            assert squash(mark.text) == doc["nerd"]["marker"]

        # The specimen box closes the title; with a specimen the title is has-spec, so the
        # heading lies over the drawn name (owner ruling of 2026-09-29, name_once).
        spec = title.elements()[-1]
        assert spec.classes == ["font-spec"]
        span = spec.find("span", class_="spec")
        fallback = spec.find("p", class_="spec-fallback")
        assert (span is None) != (fallback is None), font_id
        assert title.classes == ["font-title"] + (["has-spec"] if span is not None else [])
        if span is not None:
            assert span.attrs["role"] == "img"
            assert span.attrs["aria-label"] == f"{family} sample"
            img = spec.find("noscript").find("img")
            assert img.attrs["src"] == span.attrs["data-src"]
            assert img.attrs["alt"] == f"{family} sample"
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
        if badges is None:
            continue
        assert badges.attrs.get("role") == "list"
        assert badges.attrs["aria-label"] == "Tags"
        keys = [b.attrs["data-badge"] for b in badges.elements()]
        assert keys == sorted(keys, key=BADGE_ORDER.index)
        assert keys, "an empty tag list is left out"
        seen.update(keys)
        assert ("variable" in keys) == font["formats"]["variable"]
        assert ("attribution" in keys) == font["license"]["attribution_required"]
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
    views = [v for v in doc["views"] if v["available"]]
    select = search.find("select", id="f-rank")
    assert select.attrs["name"] == "rank"
    assert select.attrs["aria-describedby"] == "f-rank-measures"
    options = select.find_all("option")
    assert [(o.attrs["value"], squash(o.text)) for o in options] == [
        (v["key"], v["label"]) for v in views
    ]
    assert "selected" in options[0].attrs
    # The list opens on Used in projects (M2-D1, amended 2026-09-30: default_rank_project;
    # its label is the site ruling of 2026-09-25), and never offers the retired Overall.
    assert (views[0]["key"], views[0]["label"]) == ("project", "Used in projects")
    assert not data.RETIRED_VIEWS & {o.attrs["value"] for o in options}
    assert squash(search.find(id="f-rank-measures").text) == views[0]["measures"]

    q = search.find("input", id="f-q")
    assert (q.attrs["name"], q.attrs["type"], q.attrs["maxlength"]) == ("q", "search", "100")

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
    assert nerd.attrs["aria-describedby"] == "nf-legend"
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
    rank, name = buttons
    assert (rank.attrs["data-asc"], rank.attrs["data-desc"]) == ("best first", "least used first")
    assert (name.attrs["data-asc"], name.attrs["data-desc"]) == ("A\u2013Z", "Z\u2013A")
    assert (name.attrs["data-asc-spoken"], name.attrs["data-desc-spoken"]) == ("A to Z", "Z to A")
    assert rank.attrs["data-dir"] == "asc"
    assert "data-dir" not in name.attrs
    assert (
        squash(rank.text) == "Sort by Rank best first best first; select to show least used first"
    )
    assert squash(name.text) == "Sort by Name"
    for button in buttons:
        assert button.find("span", class_="sort-arrow").attrs["aria-hidden"] == "true"
    toggle = search.find("button", id="f-toggle")
    assert toggle.attrs["aria-controls"] == "f-more"
    assert toggle.attrs["aria-expanded"] == "false"
    assert toggle.find("span", class_="filters-count") is not None
    assert search.find("button", id="f-clear") is not None


def test_the_nerd_legend_is_the_owners(dom, doc):
    """The legend under the count, word for word (owner ruling of 2026-09-29, nerd_legend),
    its leading marker shown as the rows show it."""
    legend = dom.find("p", id="nf-legend")
    assert legend.attrs["class"] == "nf-legend"
    assert squash(legend.text) == doc["nerd"]["legend"]
    mark = legend.elements()[0]
    assert (mark.tag, mark.classes, squash(mark.text)) == ("span", ["nf-mark"], "NF")
    results = dom.find("section", id="results").elements()
    assert [n.attrs.get("id") for n in results[:4]] == [
        "results-h",
        "ext-summary",
        "count",
        "nf-legend",
    ]
    assert results[-2].attrs["id"] == "list-sort"  # the sort buttons sit right over the list


SITE_RULINGS_0929 = tomllib.loads(
    (ROOT / "data" / "reviews" / "site" / "2026-09-29.toml").read_text(encoding="utf-8")
)
SITE_RULINGS_0930 = tomllib.loads(
    (ROOT / "data" / "reviews" / "site" / "2026-09-30.toml").read_text(encoding="utf-8")
)
# The note's sentence as the owner changed it on 2026-09-30 (front_page_lead_sharing).
NOTE_0929 = "Some of these fonts ask you to credit the designer, or don't let you pass the font files on, and we mark those."
NOTE_0930 = "Some of these fonts ask you to credit the designer, and we mark those."
# The lead's ranking sentence, owner-approved on 2026-09-30 (front_page_lead_sharing), stopped
# being true when the list moved to Used in projects the same day (default_rank_project). Its
# replacement is Claude's proposal and waits for the owner's OK; the rest of the lead is the
# owner's wording, word for word.
RANKING_0930 = "They are ranked by how many people install them and use them in their work."
RANKING_PROPOSED = "They are ranked by how widely they are used in websites, code and apps."


def test_the_front_page_note_is_the_owners(dom):
    """The owner's note, word for word, in both of its copies (site rulings of 2026-09-29,
    why_not_listed): a frame for wide screens, and a folded one for phones."""
    ruling = SITE_RULINGS_0929["why_not_listed"]
    change = SITE_RULINGS_0930["front_page_lead_sharing"]["ruling"]
    assert NOTE_0929 in ruling["text"]
    assert NOTE_0930 in change
    text = ruling["text"].replace(NOTE_0929, NOTE_0930)
    main = dom.find("main")
    wide = main.find("div", class_="why-wide")
    fold = main.find("details", class_="why-fold")
    assert wide.classes == ["why", "why-wide"]
    assert fold.classes == ["why", "why-fold"]
    assert "open" not in fold.attrs  # folded until the visitor opens it
    assert squash(wide.find("h2").text) == ruling["heading"]
    assert squash(fold.find("summary").text) == ruling["heading"]
    for copy in (wide, fold):
        assert squash(copy.find("p").text) == text
        (link,) = copy.find("p").find_all("a")
        assert link.attrs["href"] == "mailto:admin@trulyfreefonts.com"
        assert squash(link.text) == "admin@trulyfreefonts.com"
    # The wide frame floats beside the lead and the privacy note, so it comes before them.
    kids = [n.attrs.get("class") or n.tag for n in main.elements()]
    assert kids[:6] == ["h1", "why why-wide", "lead", "privacy-note", "why why-fold", "layout"]
    # The lead, in the owner's wording of 2026-09-30 but for its ranking sentence (above).
    lead = squash(main.find("p", class_="lead").text)
    assert lead.endswith(f" {RANKING_PROPOSED}")
    assert RANKING_0930 in change
    assert f'"{lead.replace(RANKING_PROPOSED, RANKING_0930)}"' in change


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


NF_LAYOUT = """() => {
  const out = [];
  for (const li of document.querySelectorAll('li.font')) {
    const mark = li.querySelector(':scope > .font-row > .font-title > .nf-mark');
    if (!mark) continue;
    li.style.setProperty('content-visibility', 'visible');
    const name = li.querySelector('.font-name');
    const line = parseFloat(getComputedStyle(name).lineHeight);
    const n = name.getBoundingClientRect();
    const t = mark.parentNode.getBoundingClientRect();
    const m = mark.getBoundingClientRect();
    const before = li.getBoundingClientRect().height;
    const parent = mark.parentNode;
    const next = mark.nextSibling;
    mark.remove();
    const after = li.getBoundingClientRect().height;
    parent.insertBefore(mark, next);
    out.push({ id: li.id, before, after, inLine: m.top >= n.top - 0.5 && m.bottom <= n.top + line + 0.5,
               atEnd: Math.abs(m.right - t.right) <= 0.5, clear: m.left >= n.right - 0.5,
               right: m.right, width: m.width });
  }
  return out;
}"""


@pytest.mark.parametrize("width", [320, 700, 1280])
def test_the_nerd_marker_keeps_the_row_height(browser, site_url, width, doc):
    """The fixed-width marker sits in the title's first line, at its end, clear of the name, in
    one column down the list, and a row is as tall with it as without it (CONTRACT section 4,
    owner ruling nerd_marker_spot_title of 2026-09-29; rows use content-visibility)."""
    context, page = open_page(browser, site_url, width)
    try:
        got = page.evaluate(NF_LAYOUT)
        assert len(got) == sum(f["links"]["nerd"] is not None for f in doc["fonts"])
        widths = {round(row["width"], 1) for row in got}
        assert len(widths) <= 1, widths  # fixed-width
        assert len({round(row["right"]) for row in got}) <= 1, got  # one column
        for row in got:
            assert row["before"] == row["after"], row
            assert row["inLine"], row
            assert row["atEnd"], row
            assert row["clear"], row
    finally:
        context.close()


WHY_LAYOUT = """() => {
  // A block's box runs under a float; its lines are what wrap, so "right" is its text's.
  const box = (s) => { const e = document.querySelector(s); const r = e.getBoundingClientRect();
    const range = document.createRange(); range.selectNodeContents(e);
    const lines = [...range.getClientRects()].filter((q) => q.width > 0);
    return { top: r.top + scrollY, bottom: r.bottom + scrollY, left: r.left, right: r.right,
             width: r.width, display: getComputedStyle(e).display,
             textRight: lines.length ? Math.max(...lines.map((q) => q.right)) : r.right }; };
  const main = document.querySelector('main');
  const pad = parseFloat(getComputedStyle(main).paddingRight);
  return { wide: box('.why-wide'), fold: box('.why-fold'), lead: box('.lead'),
           note: box('.privacy-note'), layout: box('.layout'), results: box('#results'),
           mainRight: main.getBoundingClientRect().right - pad,
           mainLeft: main.getBoundingClientRect().left + parseFloat(getComputedStyle(main).paddingLeft),
           open: document.querySelector('.why-fold').open,
           said: (document.body.innerText.match(/Why isn't my favorite free font here\\?/g) || []).length,
           text: (document.body.innerText.match(/Not every font that's free to download/g) || []).length };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_note_is_a_frame_beside_the_intro_on_wide_screens(browser, site_url, scheme):
    """Site ruling of 2026-09-29 (why_not_listed_layout): floated right beside the lead and
    the privacy note, which wrap around it; the list starts below it, full width."""
    context, page = open_page(browser, site_url, 1280, color_scheme=scheme)
    try:
        got = page.evaluate(WHY_LAYOUT)
        wide, lead, note, layout = got["wide"], got["lead"], got["note"], got["layout"]
        assert got["fold"]["display"] == "none"
        assert abs(wide["right"] - got["mainRight"]) < 1, got  # at the right edge
        assert wide["top"] <= lead["top"] + 1, got  # level with the lead
        assert lead["textRight"] <= wide["left"], got  # the lead wraps beside it
        assert note["textRight"] <= wide["left"], got  # so does the privacy note
        assert layout["top"] >= wide["bottom"], got  # the list starts below the frame
        assert abs(layout["left"] - got["mainLeft"]) < 1, got
        assert abs(layout["right"] - got["mainRight"]) < 1, got  # rows keep their full width
        assert (got["said"], got["text"]) == (1, 1), got  # read once
    finally:
        context.close()


@pytest.mark.parametrize("javascript", [True, False], ids=["js", "no-js"])
def test_the_note_is_folded_on_phones(browser, site_url, javascript):
    """Site ruling of 2026-09-29: on a phone only the heading shows, full width; one tap opens
    the text, with or without JavaScript, and nothing else moves."""
    context = browser.new_context(
        viewport={"width": 375, "height": 812}, java_script_enabled=javascript
    )
    try:
        page = context.new_page()
        page.goto(site_url + "/")
        got = page.evaluate(WHY_LAYOUT)
        assert got["wide"]["display"] == "none"
        assert got["fold"]["display"] == "block"
        assert not got["open"]
        assert abs(got["fold"]["left"] - got["mainLeft"]) < 1, got
        assert abs(got["fold"]["right"] - got["mainRight"]) < 1, got  # full width
        assert (got["said"], got["text"]) == (1, 0), got  # the heading only, once
        before = got["layout"]["top"] - got["fold"]["bottom"]
        page.click(".why-fold summary")
        opened = page.evaluate(WHY_LAYOUT)
        assert opened["open"]
        assert (opened["said"], opened["text"]) == (1, 1), opened
        assert opened["layout"]["top"] - opened["fold"]["bottom"] == pytest.approx(before, abs=1)
        assert page.evaluate(SCROLL_WIDTH) <= 375
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
