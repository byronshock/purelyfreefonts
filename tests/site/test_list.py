"""The list page's script: data, state, view, render, filters, announcements and wiring.

Covers ``site/js/10-data.js`` to ``35-announce.js`` and ``90-main.js`` (site/CONTRACT.md
sections 4, 5, 9 and 10; Milestone 2 steps 3 and 6) in each engine given with ``--browser``
(CI runs Chromium and Firefox).

- **Pure parts.** ``State``, ``View`` and ``Render`` are loaded on their own into a blank page
  (as in test_keys_js.py) and fed the list index that ``tff_site.data.list_index`` builds from
  the site data. ``View.compute`` is checked against ``oracle()``, an independent Python
  reading of the catalog and M2-D2, for every rank and many filter combinations.
- **The built page**, served and driven as a visitor would: filters, numbering, band order,
  alias search, old links with retired keys, a copied URL opened in a fresh browser, Back,
  extension keys in the hash, the no-results state, the phone disclosure, the live region and
  focus. Every page test ends with the privacy guards clean (``Guarded.assert_clean``).

The data is the sample, or ``TFF_SITE_DATA``; tests that name sample fonts skip on other data.
Handy selections (Milestone 2 design §8, step 3): ``-k retired_keys``, ``-k url_roundtrip``.
"""

import copy
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures import make_large_catalog

from tff_catalog.keys import search_key
from tff_site import data

ROOT = Path(__file__).resolve().parents[2]
JS_DIR = ROOT / "site" / "js"
FAKE_COMMIT = "0" * 40

# The parts the pure tests load, in build order. None of them touches the page on load.
PURE_PARTS = ("00-core.js", "05-keys.js", "10-data.js", "15-state.js", "20-view.js", "25-render.js")
# A loopback address nothing listens on: Playwright fulfils it, so no request leaves the test.
PARTS_URL = "http://127.0.0.1:59999/parts.html"
PARTS_PAGE = (
    "<!doctype html><html lang=en><meta charset=utf-8><title>parts</title>"
    "<body><main id=main tabindex=-1></main><script type=module>\n{js}\n"
    "globalThis.__tffParts = {{ Core, Keys, Data, State, View, Render }};\n</script>"
)

# Recorders for the page tests, run before the page's own script (outside its CSP).
RECORDER = """
(() => {
  window.__tffReady = 0;
  window.__tffActions = [];
  // Entries the page adds: pushState calls (history.length is unreliable at this point, as
  // Firefox drops the initial about:blank entry only after init scripts run).
  window.__tffPushes = 0;
  const push = History.prototype.pushState;
  History.prototype.pushState = function pushState(...args) {
    window.__tffPushes += 1;
    return push.apply(this, args);
  };
  document.addEventListener('tff:list-ready', (event) => {
    window.__tffReady += 1;
    window.__tffReadyHook = event.detail !== undefined && event.detail === globalThis.tff;
  });
  document.addEventListener('tff:row-action', (event) => {
    window.__tffActions.push(event.detail);
  });
})();
"""
ROWS_JS = """() => Array.from(document.querySelectorAll('#list > li.font'),
  (li) => [li.dataset.id, li.querySelector('.rank').textContent])"""
SAMPLE_ONLY = "names fonts of the sample catalog"
FAILED_NOTE = "Filters and search didn\u2019t load. Check your connection, then reload the page."
NOT_RANKED = "Not ranked: "


# ---------------------------------------------------------------------------- the oracle


def oracle(doc: dict[str, Any], state: dict[str, Any]) -> tuple[list[str], list[str], int]:
    """What the list should show for ``state``: ``(ids, labels, universe size)``.

    Written from the catalog, not the list index: the view's universe, ranked fonts by
    ``order`` then unranked ones by name, the filters and search, and M2-D2's numbering (the
    exact top 100 count from 1; bands and "Not ranked: <reason>" otherwise).
    """
    rank = state.get("rank", "overall")
    universe = [f for f in data.server_order(doc) if rank in f["ranks"]]
    ranked = sorted(
        (f for f in universe if f["ranks"][rank]["order"] is not None),
        key=lambda f: f["ranks"][rank]["order"],
    )
    unranked = sorted(
        (f for f in universe if f["ranks"][rank]["order"] is None), key=data.name_order
    )
    shown = [f for f in ranked + unranked if _passes(doc, f, state)]
    labels = []
    count = 0
    for font in shown:
        entry = font["ranks"][rank]
        if entry["rank"] is not None:
            count += 1
            labels.append(str(count))
        elif entry["band"] is not None:
            labels.append(entry["band"])
        else:
            labels.append(NOT_RANKED + data.UNRANKED_LABELS[entry["unranked"]])
    pairs = [(f["id"], label) for f, label in zip(shown, labels, strict=True)]
    sort = state.get("sort", "rank")
    if sort in ("name", "name-desc"):
        names = {f["id"]: data.name_order(f) for f in shown}
        pairs.sort(key=lambda pair: names[pair[0]])
    if sort.endswith("-desc"):  # a true reverse (owner ruling of 2026-09-30, sort_header)
        pairs.reverse()
    return [p[0] for p in pairs], [p[1] for p in pairs], len(universe)


OSES = ("windows", "macos", "linux", "android")


def _hide(items: Any) -> set[str]:
    """``hide`` as State keeps it: at most one system, the first in the key's order."""
    systems = [os_ for os_ in OSES if os_ in (items or ())]
    return {i for i in (items or ()) if i not in OSES} | set(systems[:1])


def _passes(doc: dict[str, Any], font: dict[str, Any], state: dict[str, Any]) -> bool:
    os_of = {s["id"]: s["os"] for s in doc["systems"]}
    hide = _hide(state.get("hide"))
    # the site category (owner ruling of 2026-09-30): every monospaced font is Monospace
    if state.get("cat") and data.site_category(font) != state["cat"]:
        return False
    if state.get("var") and not font["formats"]["variable"]:
        return False
    if state.get("nerd") and font["links"]["nerd"] is None:
        return False
    if "limited" in hide and font["latin"]["coverage"] == "basic":
        return False
    if "attr" in hide and font["license"]["attribution_required"]:
        return False
    if hide & {os_of[item["system"]] for item in font["preinstalled_on"]}:
        return False
    query = search_key(state.get("q", ""))
    names = [font["family"], *(alias["name"] for alias in font["aliases"])]
    return not query or any(query in search_key(name) for name in names)


def band_order_ok(labels: list[str], bands: list[str]) -> bool:
    """Numbers 1, 2, 3 … first, then bands in their order, then unranked fonts."""
    kinds = []
    for label in labels:
        if label.isdigit():
            kinds.append((0, int(label)))
        elif label in bands:
            kinds.append((1, bands.index(label)))
        elif label.startswith(NOT_RANKED):
            kinds.append((2, 0))
        else:
            return False
    numbers = [n for kind, n in kinds if kind == 0]
    return kinds == sorted(kinds) and numbers == list(range(1, len(numbers) + 1))


def count_line(shown: int, total: int) -> str:
    return f"Showing {shown:,} of {total:,} {'font' if total == 1 else 'fonts'}"


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def doc(site_data: Path) -> dict[str, Any]:
    return data.load(site_data)


@pytest.fixture(scope="module")
def index(doc: dict[str, Any]) -> dict[str, Any]:
    return data.list_index(doc, commit=FAKE_COMMIT)


@pytest.fixture(scope="module")
def views(doc: dict[str, Any]) -> list[str]:
    return [v["key"] for v in doc["views"] if v["available"]]


@pytest.fixture
def sample(doc: dict[str, Any]) -> dict[str, Any]:
    # The sample itself, not just any synthetic catalog (make_large_catalog's is synthetic too).
    if not doc.get("synthetic") or not all(f["id"].startswith("sample-") for f in doc["fonts"]):
        pytest.skip(SAMPLE_ONLY)
    return doc


@dataclass
class Parts:
    """A blank page with the pure parts loaded, and the errors it reported."""

    page: Any
    errors: list[str] = field(default_factory=list)

    def run(self, body: str, arg: Any = None) -> Any:
        """Evaluate ``body`` with ``P`` (the parts), ``index`` and ``arg`` in scope."""
        script = (
            "async (arg) => { const P = globalThis.__tffParts; const index = globalThis.__index;"
            f" {body} }}"
        )
        return self.page.evaluate(script, arg)


@pytest.fixture(scope="module")
def parts(browser: Any, index: dict[str, Any]) -> Iterator[Parts]:
    source = "\n".join((JS_DIR / name).read_text(encoding="utf-8") for name in PURE_PARTS)
    assert "</script" not in source.lower()
    html = PARTS_PAGE.format(js=source)
    context = browser.new_context()
    page = context.new_page()
    loaded = Parts(page)
    page.on("pageerror", lambda error: loaded.errors.append(str(error)))
    page.route(
        PARTS_URL, lambda route: route.fulfill(status=200, content_type="text/html", body=html)
    )
    page.goto(PARTS_URL)
    page.wait_for_function("() => globalThis.__tffParts !== undefined")
    page.evaluate(
        "(index) => { globalThis.__index = index; globalThis.__tffParts.State.configure(index); }",
        index,
    )
    yield loaded
    context.close()


def open_list(make: Any, hash: str = "", **kwargs: Any) -> tuple[Any, Any]:
    """A guarded context and a page on the list at ``/<hash>``, once the list is live."""
    guarded = make(**kwargs)
    guarded.context.add_init_script(RECORDER)
    page = guarded.new_page()
    page.goto(f"/{hash}")
    page.wait_for_function("() => window.__tffReady >= 1")
    return guarded, page


def rows(page: Any) -> list[tuple[str, str]]:
    return [tuple(row) for row in page.evaluate(ROWS_JS)]


def expected_rows(doc: dict[str, Any], state: dict[str, Any]) -> list[tuple[str, str]]:
    ids, labels, _ = oracle(doc, state)
    return list(zip(ids, labels, strict=True))


def hash_of(page: Any) -> str:
    return page.evaluate("location.hash")


def entries(page: Any) -> int:
    """History entries the page's script added since it loaded."""
    return page.evaluate("window.__tffPushes")


def status(page: Any) -> str:
    return page.evaluate("document.getElementById('status').textContent")


# ------------------------------------------------------------------- pure: the parts load


def test_parts_load_as_one_module_without_errors(parts: Parts) -> None:
    assert parts.errors == []
    kinds = parts.run(
        "return Object.fromEntries(Object.entries(P).map(([k, v]) => [k, typeof v]));"
    )
    assert kinds == dict.fromkeys(("Core", "Keys", "Data", "State", "View", "Render"), "object")
    assert parts.run("return typeof P.Data.Stale;") == "function"
    assert (
        parts.run("return P.Data.STALE_MESSAGE;") == "The list was updated. Reload to see details."
    )


# ----------------------------------------------------------------- pure: the hash grammar

HASH_CASES = [
    # (hash as found, canonical hash)
    ("", ""),
    ("#", ""),
    ("#rank=overall", ""),
    ("#rank=project", "#rank=project"),
    ("#rank=rising", ""),  # Rising is not available yet: Overall
    ("#rank=bogus&cat=serif", "#cat=serif"),
    ("#cat=serif&rank=project", "#rank=project&cat=serif"),
    ("#cat=", ""),
    ("#cat=Serif", ""),
    ("#hide=windows,limited,windows", "#hide=limited,windows"),
    ("#hide=windows,nope", "#hide=windows"),
    ("#hide=limited%2Cattr", "#hide=limited,attr"),
    ("#hide=linux,windows,limited", "#hide=limited,windows"),  # one system: the first
    ("#var=1&var=0", ""),  # the last one wins, and "0" is not a value
    ("#var=0&var=1", "#var=1"),
    ("#var", ""),
    ("#nerd=1", "#nerd=1"),
    ("#nerd=0", ""),
    ("#nerd=1&var=1", "#var=1&nerd=1"),  # the Nerd filter sits after var
    # Retired keys (owner rulings of 2026-09-30): read, then dropped.
    ("#redist=yes", ""),
    ("#redist=1", ""),
    ("#redist=1&nerd=1&var=1", "#var=1&nerd=1"),
    ("#spacing=monospaced", "#cat=monospace"),
    ("#spacing=monospaced&cat=serif", "#cat=serif"),
    ("#spacing=proportional&var=1", "#var=1"),
    ("#spacing=mono", ""),
    ("#lic=open-font,attribution&os=linux", "#os=linux"),
    ("#q=Source+Sans", "#q=Source%20Sans"),
    ("#q=caf%C3%A9", "#q=caf%C3%A9"),
    ("#q=%E0%A4", ""),  # undecodable
    ("#q=" + "a" * 150, "#q=" + "a" * 100),
    ("#sort=name", "#sort=name"),
    ("#sort=rank", ""),
    ("#sort=name-desc", "#sort=name-desc"),
    ("#sort=rank-desc&cat=serif", "#cat=serif&sort=rank-desc"),
    ("#sort=desc", ""),
    ("#font=nope", ""),
    ("#font=Bad%20Id", ""),
    ("#os=linux", "#os=linux"),
    ("#os=linux&rank=project", "#rank=project&os=linux"),
    ("#x=1&rank=project&y&x=2&rank=coding", "#rank=coding&x=1&y&x=2"),
    ("#Rank=project", "#Rank=project"),  # keys are case-sensitive: someone else's
    ("#&&rank=project&", "#rank=project"),
    ("#m3=a%2Fb%20c&var=1", "#var=1&m3=a%2Fb%20c"),  # extension values stay as written
]


def test_hash_parse_and_serialise(parts: Parts, index: dict[str, Any]) -> None:
    cases = [[given, want] for given, want in HASH_CASES]
    font = index["ids"][0]
    cases.append([f"#font={font}", f"#font={font}"])
    cases.append(
        [
            f"#font={font}&redist=1&hide=windows,limited&spacing=proportional&cat=serif&rank=project",
            f"#rank=project&cat=serif&hide=limited,windows&font={font}",
        ]
    )
    got = parts.run(
        "return arg.map(([given]) => { const { state, ext } = P.State.parse(given);"
        " return P.State.serialize(state, ext); });",
        cases,
    )
    assert list(zip([c[0] for c in cases], got, strict=True)) == [(c[0], c[1]) for c in cases]
    # Canonical forms are stable.
    again = parts.run(
        "return arg.map((hash) => { const { state, ext } = P.State.parse(hash);"
        " return P.State.serialize(state, ext); });",
        [c[1] for c in cases],
    )
    assert again == [c[1] for c in cases]


def test_setstate_partials_are_validated_like_the_hash(parts: Parts, index: dict[str, Any]) -> None:
    got = parts.run(
        "const c = (p) => P.State.coerce(p);"
        "return [c({ var: true }).var, c({ var: 'yes' }).var, c({ redist: 1 }).redist ?? null,"
        " c({ hide: ['android', 'limited', 'x'] }).hide, c({ hide: 'attr,windows' }).hide,"
        " c({ q: 5 }).q, c({ rank: 'rising' }).rank, c({ rank: 'coding' }).rank,"
        " c({ sort: 'name', spacing: 'proportional' }).sort, c({ nope: 1 }).nope,"
        " P.State.isClear(c({ sort: 'name', rank: 'coding' })), P.State.isClear(c({ q: 'a' })),"
        " P.State.cleared(c({ rank: 'coding', sort: 'name', var: true, q: 'x', font: arg,"
        " nerd: true })), c({ nerd: '1' }).nerd, c({ nerd: 'yes' }).nerd,"
        " P.State.isClear(c({ nerd: true })) ];",
        index["ids"][0],
    )
    assert got[:10] == [
        True,
        False,
        None,  # redist is retired (owner ruling of 2026-09-30)
        ["limited", "android"],
        ["attr", "windows"],
        "",
        "overall",
        "coding" if "coding" in index["r"] else "overall",
        "name",
        None,
    ]
    assert got[10:12] == [True, False]
    cleared = got[12]
    assert (cleared["rank"], cleared["sort"], cleared["font"]) == (
        "coding" if "coding" in index["r"] else "overall",
        "name",
        index["ids"][0],
    )
    assert (cleared["var"], cleared["q"], cleared["cat"], cleared["hide"]) == (False, "", "", [])
    assert cleared["nerd"] is False
    assert got[13:] == [True, False, False]  # "1" is on, "yes" is not; the Nerd filter is a filter


# --------------------------------------------------------------------- pure: View.compute


def _filter_sets(doc: dict[str, Any]) -> list[dict[str, Any]]:
    sets: list[dict[str, Any]] = [{}]
    sets += [{"cat": cat} for cat in data.CATEGORIES]
    sets += [{"var": True}, {"nerd": True}, {"nerd": True, "cat": "sans-serif"}]
    sets += [{"hide": [item]} for item in ("limited", "attr", *OSES)]
    sets += [{"hide": ["limited", "attr", *OSES]}]  # State keeps one system: Windows
    sets += [{"hide": ["android", "linux"]}]  # and here Linux
    sets += [
        {"q": q} for q in ("sans", "Sample Sans Classic", "LODZ", "strasse", "mono", "zzz", "0")
    ]
    sets += [
        {"cat": "serif", "var": True},
        {"cat": "monospace", "var": True, "hide": ["attr"]},
        {"cat": "sans-serif", "hide": ["limited", "windows"], "q": "sample"},
    ]
    return sets


def test_view_matches_the_oracle_for_every_rank_and_filter(
    parts: Parts, doc: dict[str, Any], views: list[str]
) -> None:
    states = [
        {**filters, "rank": rank, "sort": sort}
        for rank in views
        for filters in _filter_sets(doc)
        for sort in ("rank", "rank-desc", "name", "name-desc")
    ]
    got = parts.run(
        "return arg.map((partial) => { const r = P.View.compute(P.State.coerce(partial), index, []);"
        " return [r.order.map((i) => index.ids[i]), r.labels, r.total, r.shown,"
        " r.dimmed.every((d) => d === false), r.notes.every((n) => n === null)]; });",
        states,
    )
    bands = [b["label"] for b in doc["bands"]]
    for state, (ids, labels, total, shown, undimmed, unnoted) in zip(states, got, strict=True):
        want_ids, want_labels, want_total = oracle(doc, state)
        assert (ids, labels, total) == (want_ids, want_labels, want_total), state
        assert shown == len(ids), state
        assert undimmed, state
        assert unnoted, state
        if state["sort"] == "rank":
            assert band_order_ok(labels, bands), state


def test_coding_lists_monospace_fonts_only(
    parts: Parts, doc: dict[str, Any], views: list[str]
) -> None:
    if "coding" not in views:
        pytest.skip("no Coding view in this data")
    mono = {f["id"] for f in doc["fonts"] if f["is_monospace"]}
    got = parts.run(
        "return ['', 'monospace', 'sans-serif'].map((cat) => {"
        " const r = P.View.compute(P.State.coerce({ rank: 'coding', cat }), index, []);"
        " return [r.order.map((i) => index.ids[i]), r.total]; });"
    )
    (every, total), (monospace, _), (sans, _) = got
    assert set(every) == mono
    assert total == len(mono)
    assert monospace == every  # Category Monospace is exactly Coding's fonts
    assert sans == []  # the other categories hold proportional fonts only


def test_category_monospace_is_every_monospaced_font(
    parts: Parts, doc: dict[str, Any], views: list[str]
) -> None:
    """Owner ruling of 2026-09-30 (monospace_category), on every rank."""
    mono = {f["id"] for f in doc["fonts"] if f["is_monospace"]}
    filed_elsewhere = {
        i for i in mono if next(f for f in doc["fonts"] if f["id"] == i)["category"] != "monospace"
    }
    if doc.get("synthetic"):
        assert filed_elsewhere, "the sample has a monospaced font the catalog files elsewhere"
    got = parts.run(
        "return arg.map((rank) => P.View.compute(P.State.coerce({ rank, cat: 'monospace' }),"
        " index, []).order.map((i) => index.ids[i]));",
        views,
    )
    for rank, ids in zip(views, got, strict=True):
        universe = {f["id"] for f in doc["fonts"] if rank in f["ranks"]}
        assert set(ids) == mono & universe, rank


def test_search_finds_source_sans_3_by_its_old_name(parts: Parts, doc: dict[str, Any]) -> None:
    renamed = copy.deepcopy(doc)
    font = renamed["fonts"][0]
    font["family"] = "Source Sans 3"
    font["aliases"] = [{"name": "Source Sans Pro", "relation": "rename"}]
    other = data.list_index(renamed, commit=FAKE_COMMIT)
    queries = [
        "Source Sans Pro",
        "source-sans_pro",
        "SOURCE SANS PRO",
        "Source\u00a0Sans",
        "sourcesans3",
    ]
    got = parts.run(
        "return arg.queries.map((q) => P.View.compute(P.State.coerce({ q }), arg.index, [])"
        ".order.map((i) => arg.index.ids[i]));",
        {"queries": queries, "index": other},
    )
    for query, found in zip(queries, got, strict=True):
        assert font["id"] in found, query
    before = parts.run(
        "return P.View.compute(P.State.coerce({ q: 'Source Sans Pro' }), index, []).shown;"
    )
    assert before == len(oracle(doc, {"q": "Source Sans Pro"})[0])


def test_search_by_alias_and_accents(parts: Parts, sample: dict[str, Any]) -> None:
    cases = {
        "Sample Sans Classic": ["sample-sans-05"],  # found only by an alias
        "SMono07 NF": ["sample-mono-07"],
        "łodz": ["sample-sans-37"],
        "ŁÓDŹ": ["sample-sans-37"],  # ł has no decomposition, so "lodz" is not enough
        "straße serif": ["sample-serif-27"],
        "STRASSE": ["sample-serif-27"],
        "sample|sans": [],  # a "|" never matches across two names
    }
    got = parts.run(
        "return arg.map((q) => P.View.compute(P.State.coerce({ q }), index, [])"
        ".order.map((i) => index.ids[i]));",
        list(cases),
    )
    assert dict(zip(cases, got, strict=True)) == cases


def test_external_filters_renumber_or_keep_the_published_numbers(
    parts: Parts, sample: dict[str, Any]
) -> None:
    got = parts.run(
        """
        const s = P.State.coerce({});
        const run = (filters) => {
          const r = P.View.compute(s, index, filters);
          return r.order.slice(0, 5).map((i, k) =>
            [index.ids[i], r.labels[k], r.dimmed[k], r.notes[k]]);
        };
        const hide3 = (id) => (id === 'sample-sans-03' ? 'hide' : 'show');
        const dim1 = (id) => (id === 'sample-sans-01' ? 'dim' : 'show');
        const hide1 = (id) => (id === 'sample-sans-01' ? 'hide' : 'show');
        return {
          keep: run([{ id: 'a', classify: hide3, affectsNumbering: false }]),
          renumber: run([{ id: 'a', classify: hide3, affectsNumbering: true }]),
          dim: run([{ id: 'a', classify: dim1 }]),
          hideWins: run([{ id: 'a', classify: dim1 }, { id: 'b', classify: hide1 }]),
          notes: run([
            { id: 'a', classify: () => 'show', note: (id) => (id === 'sample-sans-01' ? 'hello' : null) },
            { id: 'b', note: (id) => (id === 'sample-sans-01' ? { badge: 'B', text: 'x' } : '') },
          ]),
          odd: run([{ id: 'a', classify: () => 'maybe' }]),
        };
        """
    )
    first = oracle(sample, {})
    assert [tuple(r[:2]) for r in got["keep"]][:3] == [
        ("sample-sans-01", "1"),
        ("sample-mono-02", "2"),
        ("sample-serif-04", "4"),  # numbers stay as published
    ]
    assert [tuple(r[:2]) for r in got["renumber"]][:3] == [
        ("sample-sans-01", "1"),
        ("sample-mono-02", "2"),
        ("sample-serif-04", "3"),
    ]
    assert got["dim"][0] == ["sample-sans-01", "1", True, None]
    assert [r[2] for r in got["dim"][1:]] == [False] * 4
    assert got["hideWins"][0][0] == "sample-mono-02"
    assert got["notes"][0][3] == [
        {"filter": "a", "note": {"text": "hello"}},
        {"filter": "b", "note": {"badge": "B", "text": "x"}},
    ]
    assert [r[3] for r in got["notes"][1:]] == [None] * 4
    assert [r[0] for r in got["odd"]] == first[0][:5]


def test_a_filter_that_throws_leaves_its_rows_shown(parts: Parts, sample: dict[str, Any]) -> None:
    got = parts.run(
        """
        const seen = [];
        const listen = (event) => { seen.push(String(event.message || event.error)); event.preventDefault(); };
        window.addEventListener('error', listen);
        try {
          const r = P.View.compute(P.State.coerce({}), index, [
            { id: 'bad', classify: () => { throw new Error('boom'); }, note: () => { throw new Error('bang'); } },
          ]);
          return { shown: r.shown, notes: r.notes.every((n) => n === null), seen };
        } finally {
          window.removeEventListener('error', listen);
        }
        """
    )
    assert got["shown"] == len(sample["fonts"])
    assert got["notes"]
    if got["seen"]:  # reportError() exists in both engines today; the list must survive either way
        assert any("boom" in message for message in got["seen"])


# -------------------------------------------------------------------------- pure: Render


RENDER_SETUP = """
  const { Core, Render } = P;
  document.body.replaceChildren(
    Core.el('main', { id: 'main', tabindex: '-1' },
      Core.el('p', { id: 'count', text: 'Showing 3 of 3 fonts' }),
      Core.el('div', { id: 'no-results', hidden: true },
        Core.el('p', { id: 'no-results-text' }), Core.el('button', { id: 'no-results-clear' })),
      Core.el('ol', { id: 'list' })));
  const list = document.getElementById('list');
  const rows = ['a', 'b', 'c'].map((id, i) => Core.el('li', { class: 'font', id: `font-${id}`, dataset: { id } },
    Core.el('div', { class: 'font-row' },
      Core.el('span', { class: 'rank', text: String(i + 1) }),
      Core.el('h3', { class: 'font-name', text: id }),
      Core.el('a', { class: 'download', href: '/about/', text: 'Download' }),
      Core.el('button', { type: 'button', class: 'details-toggle', text: 'Details' })),
    Core.el('div', { class: 'details', hidden: true })));
  list.append(...rows);
  Render.init(list, rows);
  const ids = () => Array.from(list.children, (li) => li.dataset.id);
  const focused = () => {
    const a = document.activeElement;
    return a ? `${a.closest('li') ? a.closest('li').dataset.id : a.id}:${a.className}` : null;
  };
"""


def test_render_moves_rows_updates_labels_and_draws_notes(parts: Parts) -> None:
    got = parts.run(
        RENDER_SETUP
        + """
        const note = { badge: 'Installed', text: 'You have it',
          links: [{ label: 'About', href: '/about/' }, { label: 'Bad', href: 'javascript:alert(1)' },
                  { label: 'Far', href: 'https://example.org/x' }, { label: 'Proto', href: '//evil.test/' },
                  { label: 'Tab', href: 'java\\tscript:alert(1)' }, { label: 'Http', href: 'http://example.org/' }],
          actions: [{ id: 'hide-it', label: 'Hide' }] };
        const line = Render.apply({ order: [2, 0], labels: ['1', '2'], dimmed: [false, true],
          notes: [null, [{ filter: 'm3', note }]], shown: 2, total: 3 });
        const slot = rows[0].querySelector('.font-row > .ext[data-filter="m3"]');
        const out = {
          line, ids: ids(), count: document.getElementById('count').textContent,
          labels: rows.map((r) => r.querySelector('.rank').textContent),
          dim: rows.map((r) => r.classList.contains('is-dim')),
          bInDocument: rows[1].isConnected, bHidden: rows[1].hidden,
          slot: slot && Array.from(slot.children, (n) =>
            [n.tagName.toLowerCase(), n.className, n.textContent, n.getAttribute('href'),
             n.dataset.action || null, n.getAttribute('type')]),
          last: rows[0].querySelector('.font-row').lastElementChild === slot,
        };
        Render.apply({ order: [0, 1, 2], labels: ['1', '2', '3'], dimmed: [false, false, false],
          notes: [null, null, null], shown: 3, total: 3 });
        out.after = [ids(), rows[0].querySelectorAll('.ext').length, rows[0].classList.contains('is-dim')];
        return out;
        """
    )
    assert got["line"] == got["count"] == "Showing 2 of 3 fonts"
    assert got["ids"] == ["c", "a"]
    assert got["labels"] == ["2", "2", "1"]  # b keeps its old text while detached
    assert got["dim"] == [True, False, False]
    assert (got["bInDocument"], got["bHidden"]) == (False, False)  # detached, never hidden
    assert got["last"]
    assert got["slot"] == [
        ["span", "ext-badge", "Installed", None, None, None],
        ["p", "ext-note", "You have it", None, None, None],
        ["a", "ext-link", "About", "http://127.0.0.1:59999/about/", None, None],
        ["a", "ext-link", "Far", "https://example.org/x", None, None],
        ["button", "ext-action", "Hide", None, "hide-it", "button"],
    ]
    assert got["after"] == [["a", "b", "c"], 0, False]


def test_render_keeps_focus_and_moves_it_when_its_row_leaves(parts: Parts) -> None:
    got = parts.run(
        RENDER_SETUP
        + """
        const out = [];
        const note = (text) => [{ filter: 'm3', note: { text, actions: [{ id: 'go', label: 'Go' }] } }];
        Render.apply({ order: [0, 1, 2], labels: ['1', '2', '3'], dimmed: [false, false, false],
          notes: [note('one'), null, null], shown: 3, total: 3 });
        const button = rows[0].querySelector('.ext-action');
        button.focus();
        // The row moves; the same note is not redrawn: the very same button keeps focus.
        Render.apply({ order: [2, 0, 1], labels: ['1', '2', '3'], dimmed: [false, false, false],
          notes: [null, note('one'), null], shown: 3, total: 3 });
        out.push([ids(), document.activeElement === button]);
        // The note changes: the new button with the same action gets the focus.
        Render.apply({ order: [2, 0, 1], labels: ['1', '2', '3'], dimmed: [false, false, false],
          notes: [null, note('two'), null], shown: 3, total: 3 });
        out.push([document.activeElement !== button, focused(), document.activeElement.dataset.action]);
        // Its row leaves: focus goes to the next shown row's details button.
        Render.apply({ order: [2, 1], labels: ['1', '2'], dimmed: [false, false],
          notes: [null, null], shown: 2, total: 3 });
        out.push(focused());
        // The last row leaves while focused: the one before it.
        Render.apply({ order: [2], labels: ['1'], dimmed: [false], notes: [null], shown: 1, total: 3 });
        out.push(focused());
        // Nothing left: #main, and the no-results message shows.
        Render.apply({ order: [], labels: [], dimmed: [], notes: [], shown: 0, total: 3 }, { message: 'None here.' });
        out.push([focused(), document.getElementById('no-results').hidden,
          document.getElementById('no-results-text').textContent, ids()]);
        // Clearing from the no-results button: focus moves to the first row before it hides.
        document.getElementById('no-results-clear').focus();
        Render.apply({ order: [1, 0], labels: ['1', '2'], dimmed: [false, false], notes: [null, null], shown: 2, total: 3 });
        out.push([focused(), document.getElementById('no-results').hidden]);
        return out;
        """
    )
    assert got[0] == [["c", "a", "b"], True]
    assert got[1] == [True, "a:ext-action", "go"]
    assert got[2] == "b:details-toggle"
    assert got[3] == "c:details-toggle"
    assert got[4] == ["main:", False, "None here.", []]
    assert got[5] == ["b:details-toggle", True]


# ----------------------------------------------------------------------------- the page


def test_first_load_shows_the_server_list_silently(
    guarded_context: Any, doc: dict[str, Any]
) -> None:
    guarded = guarded_context()
    # Everything the script does to #list before the list is live. The parser has finished
    # at "interactive", and the script's first render comes later (after a fetch and an idle
    # callback), so the rows the server wrote are not counted.
    guarded.context.add_init_script(
        """
        window.__listChanges = [];
        document.addEventListener('readystatechange', () => {
          if (document.readyState !== 'interactive') return;
          const list = document.getElementById('list');
          // What Render may change: the rows in #list, a row's class or hidden, .rank text.
          const note = (records) => {
            for (const r of records) {
              const node = r.target.nodeType === Node.ELEMENT_NODE ? r.target : r.target.parentElement;
              if (node === list || node.matches('li.font') || node.closest('.rank')) {
                window.__listChanges.push(`${r.type} ${node.id || node.className}`);
              }
            }
          };
          const observer = new MutationObserver(note);
          observer.observe(list, { childList: true, subtree: true, characterData: true,
            attributes: true, attributeFilter: ['class', 'hidden'] });
          document.addEventListener('tff:list-ready', () => {
            note(observer.takeRecords());
            observer.disconnect();
          });
        });
        """
    )
    guarded.context.add_init_script(RECORDER)
    page = guarded.new_page()
    page.goto("/")
    page.wait_for_function("() => window.__tffReady >= 1")
    ids, labels, total = oracle(doc, {})
    assert rows(page) == list(zip(ids, labels, strict=True))
    # The default view equals the server's list, so the script changes nothing in it: no
    # layout shift once the list is live.
    assert page.evaluate("window.__listChanges") == []
    # One live region in the list's area, Announce's #status.
    assert page.evaluate(
        "Array.from(document.querySelectorAll('.layout [role=status], .layout [role=alert],"
        " .layout [role=log], .layout [aria-live]'), (node) => node.id)"
    ) == ["status"]
    # The script's fetch reuses the preloaded list index: one request.
    assert len([url for url in guarded.requests if "/assets/list." in url]) == 1
    assert page.evaluate("document.documentElement.hasAttribute('data-js')")
    assert page.evaluate("document.getElementById('filters').hidden") is False
    assert page.text_content("#count") == count_line(len(ids), total)
    assert status(page) == ""  # silent on first load
    assert hash_of(page) == ""
    assert page.evaluate("document.querySelectorAll('li.font[hidden]').length") == 0
    assert page.text_content("#f-rank-measures") == doc["views"][0]["measures"]
    assert page.text_content("#f-toggle .filters-count") == ""
    assert entries(page) == 0
    page.wait_for_timeout(100)
    assert page.evaluate("window.__tffReady") == 1  # tff:list-ready, exactly once
    guarded.assert_clean(page)


def test_rank_selector_numbers_every_view(
    guarded_context: Any, doc: dict[str, Any], views: list[str]
) -> None:
    guarded, page = open_list(guarded_context)
    bands = [b["label"] for b in doc["bands"]]
    measures = {v["key"]: v["measures"] for v in doc["views"]}
    for rank in [*views[1:], views[0]]:
        page.select_option("#f-rank", rank)
        ids, labels, total = oracle(doc, {"rank": rank})
        assert rows(page) == list(zip(ids, labels, strict=True)), rank
        assert band_order_ok(labels, bands), rank
        assert page.text_content("#count") == count_line(len(ids), total)
        assert page.text_content("#f-rank-measures") == measures[rank]
        assert hash_of(page) == ("" if rank == "overall" else f"#rank={rank}")
        assert status(page).rstrip("\u00a0") == count_line(len(ids), total)
    assert entries(page) == len(views)
    guarded.assert_clean(page)


HIDES = ("limited", "attr", *OSES)


def hash_for(state: dict[str, Any]) -> str:
    """The canonical hash of a partial state (CONTRACT section 9), as State writes it."""
    pairs = []
    if state.get("rank", "overall") != "overall":
        pairs.append(f"rank={state['rank']}")
    if state.get("cat"):
        pairs.append(f"cat={state['cat']}")
    pairs += [f"{key}=1" for key in ("var", "nerd") if state.get(key)]
    hide = _hide(state.get("hide"))
    if hide:
        pairs.append("hide=" + ",".join(h for h in HIDES if h in hide))
    if state.get("sort", "rank") != "rank":
        pairs.append(f"sort={state['sort']}")
    return "#" + "&".join(pairs) if pairs else ""


def test_filters_on_the_page_match_the_oracle(guarded_context: Any, doc: dict[str, Any]) -> None:
    guarded, page = open_list(guarded_context)
    credit = any(f["license"]["attribution_required"] for f in doc["fonts"])
    assert page.locator("#f-hide-attr").count() == int(credit)  # shown only when it can hide
    steps: list[tuple[Any, dict[str, Any]]] = [
        ("#f-cat-sans-serif", {"cat": "sans-serif"}),
        ("#f-var", {"var": True}),
        ("#f-hide-limited", {"hide": ["limited"]}),
        (("#f-os", "windows"), {"hide": ["limited", "windows"]}),
        (("#f-os", "linux"), {"hide": ["limited", "linux"]}),  # one system at a time
        *([("#f-hide-attr", {"hide": ["limited", "attr", "linux"]})] if credit else []),
        ("#f-cat-monospace", {"cat": "monospace"}),
        ("#f-cat-all", {"cat": ""}),
        ("#sort-name", {"sort": "name"}),
        ("#sort-name", {"sort": "name-desc"}),  # the button in use reverses its order
        (("#f-os", ""), {"hide": ["limited", "attr"] if credit else ["limited"]}),
    ]
    state: dict[str, Any] = {}
    for selector, change in steps:
        if isinstance(selector, tuple):
            page.select_option(*selector)
        elif selector.startswith("#sort-"):
            page.click(selector)
        else:
            page.check(selector)
        state.update(change)
        assert rows(page) == expected_rows(doc, state), selector
        assert hash_of(page) == hash_for(state), selector
        on = bool(state.get("cat")) + bool(state.get("var")) + len(_hide(state.get("hide")))
        assert page.text_content("#f-toggle .filters-count") == f"\u00a0({on})"
    guarded.assert_clean(page)


def test_retired_keys_in_old_links_open_the_nearest_view(
    guarded_context: Any, doc: dict[str, Any]
) -> None:
    """Owner rulings of 2026-09-30: spacing, lic and redist are gone, but links keep working."""
    guarded, page = open_list(guarded_context, "#spacing=monospaced&redist=1&lic=open-font&var=1")
    assert hash_of(page) == "#cat=monospace&var=1"
    assert page.is_checked("#f-cat-monospace")
    assert rows(page) == expected_rows(doc, {"cat": "monospace", "var": True})
    assert entries(page) == 0  # rewritten in place
    guarded.assert_clean(page)


def test_nerd_font_available_keeps_exactly_the_fonts_with_a_nerd_build(
    guarded_context: Any, doc: dict[str, Any], views: list[str]
) -> None:
    """The owner's site ruling of 2026-09-29 (nerd_filter), on every rank."""
    guarded, page = open_list(guarded_context)
    nerd = {f["id"] for f in doc["fonts"] if f["links"]["nerd"] is not None}
    assert nerd, "the sample needs a font with a Nerd Font build"
    assert page.get_attribute("#f-nerd", "aria-describedby") == "nf-legend"
    page.check("#f-nerd")
    assert hash_of(page) == "#nerd=1"
    assert page.text_content("#f-toggle .filters-count") == "\u00a0(1)"
    for rank in views:
        page.select_option("#f-rank", rank)
        universe, _, _ = oracle(doc, {"rank": rank})
        assert {row[0] for row in rows(page)} == nerd & set(universe), rank
        assert rows(page) == expected_rows(doc, {"rank": rank, "nerd": True})
    page.select_option("#f-rank", "overall")
    if not any(not f["is_monospace"] for f in doc["fonts"] if f["id"] in nerd):
        page.check("#f-cat-serif")  # every Nerd build here is monospace
        assert rows(page) == []
        assert "Nerd Font available" in page.text_content("#no-results-text")
    page.click("#f-clear")
    assert hash_of(page) == ""
    assert not page.is_checked("#f-nerd")
    guarded.assert_clean(page)


def test_alias_search_while_typing(guarded_context: Any, sample: dict[str, Any]) -> None:
    guarded, page = open_list(guarded_context)
    before = entries(page)
    page.locator("#f-q").press_sequentially("Sample Sans Classic")
    assert rows(page) == [("sample-sans-05", "1")]
    # Typing replaces the history entry after a pause, and is announced after a longer one.
    page.wait_for_function("() => location.hash === '#q=Sample%20Sans%20Classic'")
    assert entries(page) == before
    page.wait_for_function(
        "() => document.getElementById('status').textContent.startsWith('Showing 1 of 40 fonts')"
    )
    page.fill("#f-q", "")
    page.locator("#f-q").press_sequentially("łódź")
    assert rows(page) == [("sample-sans-37", "1")]
    page.fill("#f-q", "STRASSE")
    assert rows(page) == expected_rows(sample, {"q": "STRASSE"})
    assert [row[0] for row in rows(page)] == ["sample-serif-27"]
    guarded.assert_clean(page)


def test_url_roundtrip_opens_the_same_view_in_a_fresh_browser(
    guarded_context: Any, doc: dict[str, Any], views: list[str]
) -> None:
    rank = "project" if "project" in views else views[-1]
    guarded, page = open_list(guarded_context)
    page.select_option("#f-rank", rank)
    page.check("#f-cat-sans-serif")
    page.check("#f-hide-limited")
    page.select_option("#f-os", "macos")
    page.click("#sort-name")
    page.locator("#f-q").press_sequentially("sample")
    page.wait_for_function("() => location.hash.includes('q=sample')")
    state = {
        "rank": rank,
        "cat": "sans-serif",
        "hide": ["limited", "macos"],
        "sort": "name",
        "q": "sample",
    }
    want = expected_rows(doc, state)
    assert rows(page) == want
    url, hash_a = page.url, hash_of(page)
    assert hash_a == f"#rank={rank}&cat=sans-serif&hide=limited,macos&q=sample&sort=name"
    guarded.assert_clean(page)

    fresh = guarded_context()
    fresh.context.add_init_script(RECORDER)
    other = fresh.new_page()
    other.goto(url)
    other.wait_for_function("() => window.__tffReady >= 1")
    assert rows(other) == want
    assert hash_of(other) == hash_a
    assert entries(other) == 0
    assert other.input_value("#f-rank") == rank
    assert other.input_value("#f-q") == "sample"
    for selector in ("#f-cat-sans-serif", "#f-hide-limited"):
        assert other.is_checked(selector), selector
    assert other.input_value("#f-os") == "macos"
    assert other.get_attribute("#sort-name", "aria-pressed") == "true"
    assert other.get_attribute("#sort-name", "data-dir") == "asc"
    assert not other.is_checked("#f-var")
    measures = {v["key"]: v["measures"] for v in doc["views"]}
    assert other.text_content("#f-rank-measures") == measures[rank]
    assert status(other) == ""
    fresh.assert_clean(other)


def test_extension_keys_survive_load_a_filter_change_and_back(
    guarded_context: Any, doc: dict[str, Any], views: list[str]
) -> None:
    if "project" not in views:
        pytest.skip("no Used in projects view in this data")
    guarded, page = open_list(guarded_context, "#rank=project&os=linux")
    initial = expected_rows(doc, {"rank": "project"})
    assert hash_of(page) == "#rank=project&os=linux"
    assert page.input_value("#f-rank") == "project"
    assert rows(page) == initial
    page.check("#f-var")
    assert hash_of(page) == "#rank=project&var=1&os=linux"
    assert rows(page) == expected_rows(doc, {"rank": "project", "var": True})
    page.go_back()
    page.wait_for_function("() => location.hash === '#rank=project&os=linux'")
    page.wait_for_function("() => !document.getElementById('f-var').checked")
    assert rows(page) == initial
    page.go_forward()
    page.wait_for_function("() => location.hash === '#rank=project&var=1&os=linux'")
    assert page.is_checked("#f-var")
    guarded.assert_clean(page)


def test_a_changed_extension_pair_is_kept_on_the_next_write(guarded_context: Any) -> None:
    guarded, page = open_list(guarded_context, "#os=linux")
    # Milestone 3 changes its own key without telling the list; the next write keeps it.
    page.evaluate("history.replaceState(null, '', '#os=macos&tab=2')")
    page.check("#f-var")
    assert hash_of(page) == "#var=1&os=macos&tab=2"
    page.uncheck("#f-var")
    assert hash_of(page) == "#os=macos&tab=2"
    guarded.assert_clean(page)


def test_a_messy_hash_is_rewritten_without_a_new_history_entry(
    guarded_context: Any, views: list[str]
) -> None:
    rank = "coding" if "coding" in views else views[-1]
    guarded, page = open_list(
        guarded_context, f"#os=linux&cat=bogus&x=a%2Fb&rank=project&rank={rank}"
    )
    assert hash_of(page) == f"#rank={rank}&os=linux&x=a%2Fb"
    assert entries(page) == 0
    guarded.assert_clean(page)


def test_back_and_forward_replay_discrete_changes(
    guarded_context: Any, doc: dict[str, Any], views: list[str]
) -> None:
    guarded, page = open_list(guarded_context)
    rank = views[1]
    page.check("#f-cat-serif")
    page.select_option("#f-rank", rank)
    assert entries(page) == 2
    page.go_back()
    page.wait_for_function("() => location.hash === '#cat=serif'")
    page.wait_for_function("() => document.getElementById('f-rank').value === 'overall'")
    assert rows(page) == expected_rows(doc, {"cat": "serif"})
    ids, _, total = oracle(doc, {"cat": "serif"})
    assert status(page).rstrip("\u00a0") == count_line(len(ids), total)
    page.go_back()
    page.wait_for_function("() => location.hash === ''")
    page.wait_for_function("() => document.getElementById('f-cat-all').checked")
    assert rows(page) == expected_rows(doc, {})
    page.go_forward()
    page.wait_for_function("() => location.hash === '#cat=serif'")
    assert page.is_checked("#f-cat-serif")
    guarded.assert_clean(page)


def test_a_link_to_a_view_applies_it_without_a_push_from_the_page(
    guarded_context: Any, doc: dict[str, Any]
) -> None:
    guarded, page = open_list(guarded_context, "#os=linux")
    # A link or an edited address: the browser adds the entry and fires hashchange.
    page.evaluate("location.hash = '#hide=nope&cat=serif&os=macos'")
    page.wait_for_function("() => location.hash === '#cat=serif&os=macos'")  # canonical
    page.wait_for_function("() => document.getElementById('f-cat-serif').checked")
    assert rows(page) == expected_rows(doc, {"cat": "serif"})
    ids, _, total = oracle(doc, {"cat": "serif"})
    assert status(page).rstrip("\u00a0") == count_line(len(ids), total)
    assert entries(page) == 0  # the page itself pushed nothing
    page.go_back()
    page.wait_for_function("() => location.hash === '#os=linux'")
    page.wait_for_function("() => document.getElementById('f-cat-all').checked")
    assert rows(page) == expected_rows(doc, {})
    guarded.assert_clean(page)


def test_search_writes_the_url_after_300_ms_and_speaks_after_500_ms(
    guarded_context: Any, doc: dict[str, Any]
) -> None:
    guarded, page = open_list(guarded_context, "#os=linux")
    # Page time stands still from here, and moves only when the test says so.
    page.clock.install()
    page.clock.pause_at(page.evaluate("Date.now()") + 1000)
    search = page.locator("#f-q")
    search.press_sequentially("mo")
    page.clock.run_for(200)
    search.press_sequentially("no")
    assert rows(page) == expected_rows(doc, {"q": "mono"})  # the list follows every key
    page.clock.run_for(299)  # the wait starts again at each key
    assert hash_of(page) == "#os=linux"
    page.clock.run_for(2)
    assert hash_of(page) == "#q=mono&os=linux"  # the extension pair stays, after ours
    assert entries(page) == 0  # replaced, not pushed
    assert status(page) == ""
    page.clock.run_for(197)
    assert status(page) == ""
    page.clock.run_for(2)
    ids, _, total = oracle(doc, {"q": "mono"})
    assert status(page) == count_line(len(ids), total)

    # A discrete change while a search is still pending first writes the search into the
    # current entry, so Back returns to it.
    search.press_sequentially("space")
    page.check("#f-var")
    assert hash_of(page) == "#var=1&q=monospace&os=linux"
    assert entries(page) == 1
    page.go_back()
    page.wait_for_function("() => location.hash === '#q=monospace&os=linux'")
    page.wait_for_function("() => !document.getElementById('f-var').checked")
    assert page.input_value("#f-q") == "monospace"
    assert rows(page) == expected_rows(doc, {"q": "monospace"})
    guarded.assert_clean(page)


def test_rows_are_the_server_rendered_nodes_moved_not_rebuilt(
    guarded_context: Any, views: list[str]
) -> None:
    guarded, page = open_list(guarded_context)
    page.evaluate("() => { window.__rows = [...document.querySelectorAll('#list > li.font')]; }")
    page.select_option("#f-rank", views[-1])
    page.check("#f-cat-serif")
    page.click("#sort-name")
    page.check("#f-cat-all")
    page.click("#sort-rank")
    page.select_option("#f-rank", views[0])
    assert page.evaluate(
        """() => {
          const now = [...document.querySelectorAll('#list > li.font')];
          return now.length === window.__rows.length && now.every((li, k) => li === window.__rows[k]);
        }"""
    )
    guarded.assert_clean(page)


def test_no_results_names_the_category_filter_on_coding(
    guarded_context: Any, doc: dict[str, Any], views: list[str]
) -> None:
    if "coding" not in views:
        pytest.skip("no Coding view in this data")
    guarded, page = open_list(guarded_context)
    page.select_option("#f-rank", "coding")
    page.check("#f-cat-serif")  # Coding's fonts are all Monospace
    _, _, total = oracle(doc, {"rank": "coding"})
    assert rows(page) == []
    assert page.evaluate("document.querySelectorAll('li.font').length") == 0
    assert page.is_visible("#no-results")
    text = page.text_content("#no-results-text")
    assert "Category: Serif" in text
    assert page.text_content("#count") == count_line(0, total)
    assert status(page) == text
    page.fill("#f-q", " - ")  # matches every font, so it isn't named as a filter to loosen
    assert page.text_content("#no-results-text") == text
    page.fill("#f-q", "mono")
    assert "Search fonts: “mono”" in page.text_content("#no-results-text")
    page.click("#no-results-clear")
    assert rows(page) == expected_rows(doc, {"rank": "coding"})
    assert page.is_hidden("#no-results")
    assert hash_of(page) == "#rank=coding"
    first = rows(page)[0][0]
    assert page.evaluate("document.activeElement.className") == "details-toggle"
    assert page.evaluate("document.activeElement.closest('li').dataset.id") == first
    guarded.assert_clean(page)


def test_clear_filters_keeps_the_rank_and_sort_order(
    guarded_context: Any, doc: dict[str, Any], views: list[str]
) -> None:
    rank = views[1]
    guarded, page = open_list(guarded_context)
    page.select_option("#f-rank", rank)
    page.click("#sort-name")
    # "No credit required" shows only while some font needs credit (license_filter).
    credit = page.locator("#f-hide-attr").count() > 0
    for selector in ("#f-var", "#f-cat-serif", "#f-hide-limited", *(["#f-hide-attr"] * credit)):
        page.check(selector)
    page.select_option("#f-os", "android")
    page.fill("#f-q", "sa")
    page.click("#f-clear")
    assert hash_of(page) == f"#rank={rank}&sort=name"
    assert rows(page) == expected_rows(doc, {"rank": rank, "sort": "name"})
    assert page.input_value("#f-q") == ""
    assert page.is_checked("#f-cat-all")
    assert page.input_value("#f-os") == ""
    assert page.get_attribute("#sort-name", "aria-pressed") == "true"  # the order stays
    assert not page.is_checked("#f-var")
    assert not page.is_checked("#f-hide-limited")
    if credit:
        assert not page.is_checked("#f-hide-attr")
    assert page.text_content("#f-toggle .filters-count") == ""
    assert page.evaluate("document.activeElement.id") == "f-clear"
    guarded.assert_clean(page)


def test_the_default_view_drops_the_hash_but_keeps_the_page_url(guarded_context: Any) -> None:
    guarded, page = open_list(guarded_context, "?from=test#cat=serif&var=1")
    page.click("#f-clear")
    assert page.evaluate("location.pathname + location.search") == "/?from=test"
    assert page.url.endswith("/?from=test")  # no bare "#" left behind
    assert entries(page) == 1
    guarded.assert_clean(page)


def test_the_live_region_is_polite_coalesced_and_never_repeats_silently(
    guarded_context: Any, doc: dict[str, Any]
) -> None:
    guarded, page = open_list(guarded_context)
    assert page.get_attribute("#status", "role") == "status"
    assert status(page) == ""
    records = page.evaluate(
        """async () => {
          const node = document.getElementById('status');
          let count = 0;
          const observer = new MutationObserver((list) => { count += list.length; });
          observer.observe(node, { childList: true, characterData: true, subtree: true });
          document.getElementById('f-var').click();
          document.getElementById('f-hide-limited').click();
          await new Promise((resolve) => setTimeout(resolve, 50));
          observer.disconnect();
          return count;
        }"""
    )
    assert records == 1  # two changes in one task: one announcement
    ids, _, total = oracle(doc, {"var": True, "hide": ["limited"]})
    line = count_line(len(ids), total)
    assert status(page) == line
    page.click("#sort-name")  # same count, a new order: the order is said too
    assert status(page) == f"{line}. Sorted by name, A to Z."
    # Typing is announced after a pause (timed exactly, with the page clock, in
    # test_search_writes_the_url_after_300_ms_and_speaks_after_500_ms). "zz" alone
    # finds Piazzolla in the real catalog.
    nothing = "zzqx"
    assert not oracle(doc, {"q": nothing})[0]
    page.locator("#f-q").press_sequentially(nothing)
    page.wait_for_function(
        "() => document.getElementById('status').textContent.startsWith('No fonts')"
    )
    assert status(page) == page.text_content("#no-results-text")
    guarded.assert_clean(page)


# Every listed row laid out in full (content-visibility off, so no row keeps its estimate):
# its height, the estimate its CSS gives, and where its rank label and name sit.
LAYOUT_JS = """() => {
  const lis = Array.from(document.querySelectorAll('#list > li.font'));
  lis.forEach((li) => { li.style.contentVisibility = 'visible'; });  // CSSOM: CSP allows it
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const out = lis.map((li) => {
    const rank = li.querySelector('.rank');
    const r = rank.getBoundingClientRect();
    const n = li.querySelector('.font-name').getBoundingClientRect();
    const est = getComputedStyle(li).getPropertyValue('--row-est-h').trim();
    return {
      label: rank.textContent,
      unranked: li.classList.contains('is-unranked'),
      height: li.getBoundingClientRect().height,
      estimate: est.endsWith('rem') ? parseFloat(est) * rem : parseFloat(est),
      rankTop: r.top, rankBottom: r.bottom, rankHeight: r.height,
      nameTop: n.top,
      lineHeight: parseFloat(getComputedStyle(rank).lineHeight) || 1.5 * rem,
    };
  });
  lis.forEach((li) => { li.style.contentVisibility = ''; });
  return out;
}"""


def median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1280, "height": 720}, {"width": 700, "height": 800}, {"width": 375, "height": 812}],
    ids=["wide", "table", "phone"],
)
def test_an_unranked_label_takes_a_line_of_its_own(
    guarded_context: Any, doc: dict[str, Any], viewport: dict[str, int]
) -> None:
    """The owner's site ruling of 2026-09-26 (list_layout): "Not ranked: <reason>" is kept,
    on a line of its own above the name instead of down the narrow rank column, and the row
    estimates (``--row-est-h``) stay close to the measured heights.

    The sample's default view has unranked rows; the real catalog's Overall ranks every font,
    so the first view that has some is opened instead."""
    view = next(
        v["key"]
        for v in doc["views"]
        if v["available"]
        and any(f["ranks"].get(v["key"], {}).get("unranked") for f in doc["fonts"])
    )
    hash = "" if view == doc["views"][0]["key"] else f"#rank={view}"
    guarded, page = open_list(guarded_context, hash, viewport=viewport)
    laid = page.evaluate(LAYOUT_JS)
    unranked = [r for r in laid if r["label"].startswith(NOT_RANKED)]
    ranked = [r for r in laid if not r["label"].startswith(NOT_RANKED)]
    assert unranked, f"the {view} view has unranked rows"
    assert all(r["unranked"] for r in unranked)
    assert not any(r["unranked"] for r in ranked)
    for r in unranked:
        assert r["rankBottom"] <= r["nameTop"] + 1, r["label"]  # a line of its own, above
    for r in ranked:
        assert abs(r["rankTop"] - r["nameTop"]) < r["lineHeight"], r["label"]  # beside the name
    if viewport["width"] >= 700:  # the label fits on one line once the row is table-like
        assert all(r["rankHeight"] < 1.6 * r["lineHeight"] for r in unranked)
    for group in (ranked, unranked):
        measured, estimate = median([r["height"] for r in group]), group[0]["estimate"]
        assert 0.75 * estimate <= measured <= 1.25 * estimate, (measured, estimate)
    assert unranked[0]["estimate"] > ranked[0]["estimate"]
    guarded.assert_clean(page)


SORT_LAYOUT_JS = """() => {
  const box = (el) => el.getBoundingClientRect();
  const title = document.querySelector('li.font:not(.is-unranked) .font-title');
  return {
    rank: box(document.querySelector('#sort-rank')).right,
    name: box(document.querySelector('#sort-name')).left,
    title: box(title).left,
  };
}"""


@pytest.mark.parametrize("width", [640, 1280])
def test_the_name_button_sits_over_the_names(guarded_context: Any, width: int) -> None:
    """Owner rulings of 2026-09-30 (sort_header, sort_two_lines): from 40rem the Name button
    starts over the name column, and the Rank button fits beside it in either order."""
    guarded, page = open_list(guarded_context, viewport={"width": width, "height": 800})
    for _ in range(2):  # best first, then least used first (the longer words)
        got = page.evaluate(SORT_LAYOUT_JS)
        assert abs(got["name"] - got["title"]) <= 1, got
        assert got["rank"] < got["name"], got
        page.click("#sort-rank")
    guarded.assert_clean(page)


def test_phone_filters_button_counts_active_filters(guarded_context: Any) -> None:
    guarded, page = open_list(guarded_context, viewport={"width": 375, "height": 812})
    toggle = page.locator("#f-toggle")
    assert toggle.is_visible()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.is_hidden("#f-more")
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert page.is_visible("#f-more")
    page.check("#f-var")
    page.select_option("#f-os", "windows")
    # The count follows the redraw, which a busy machine may run a moment later.
    page.get_by_role("button", name="Filters (2)").wait_for()
    assert page.get_by_role("button", name="Filters (2)").count() == 1
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.is_hidden("#f-more")
    guarded.assert_clean(page)


def test_the_skip_link_keeps_the_view(guarded_context: Any, doc: dict[str, Any]) -> None:
    guarded, page = open_list(guarded_context, "#cat=serif")
    before = rows(page)
    page.focus("a.skip-link")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.activeElement && document.activeElement.id === 'main'")
    page.wait_for_function("() => location.hash === '#cat=serif'")
    assert rows(page) == before == expected_rows(doc, {"cat": "serif"})
    assert page.is_checked("#f-cat-serif")
    guarded.assert_clean(page)


def test_focus_stays_put_while_the_list_changes(
    guarded_context: Any, sample: dict[str, Any]
) -> None:
    guarded, page = open_list(guarded_context)
    link = "#font-sample-serif-09 .download"
    page.focus(link)
    position = page.evaluate(
        "() => [...document.querySelectorAll('#list > li')].indexOf(document.activeElement.closest('li'))"
    )
    page.evaluate("document.getElementById('f-var').click()")  # a click that doesn't move focus
    moved = page.evaluate(
        "() => [...document.querySelectorAll('#list > li')].indexOf(document.activeElement.closest('li'))"
    )
    assert page.evaluate("document.activeElement.closest('li').id") == "font-sample-serif-09"
    assert page.evaluate("document.activeElement.className") == "download"
    assert moved != position  # the row did move
    # Arrow keys in a radio group change the filter and keep focus in the group.
    page.focus("#f-cat-all")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.id") == "f-cat-sans-serif"
    assert hash_of(page) == "#cat=sans-serif&var=1"
    guarded.assert_clean(page)


def test_details_keep_their_font_key_through_filter_changes(
    guarded_context: Any, sample: dict[str, Any]
) -> None:
    guarded, page = open_list(guarded_context)
    page.click("#font-sample-sans-01 .details-toggle")
    page.wait_for_function("() => location.hash === '#font=sample-sans-01'")
    assert entries(page) == 1  # one entry per open, whoever writes it
    page.check("#f-var")
    assert hash_of(page) == "#var=1&font=sample-sans-01"
    assert entries(page) == 2
    page.go_back()
    page.wait_for_function("() => location.hash === '#font=sample-sans-01'")
    page.wait_for_function("() => !document.getElementById('f-var').checked")
    guarded.assert_clean(page)


def test_milestone_3_notes_actions_and_numbering(
    guarded_context: Any, sample: dict[str, Any]
) -> None:
    guarded, page = open_list(guarded_context)
    if page.evaluate("typeof globalThis.tff") != "object":
        pytest.skip("this build has no Milestone 3 hook (50-ext.js)")
    assert page.evaluate("window.__tffReadyHook") is True
    page.evaluate(
        """() => {
          window.__changes = [];
          tff.list.on('change', (change) => window.__changes.push([change.shown, change.total]));
          tff.list.addFilter('t', {
            classify: (id) => (id === 'sample-sans-03' ? 'hide' : id === 'sample-sans-01' ? 'dim' : 'show'),
            note: (id) => (id === 'sample-serif-04' ? { text: 'You have it', badge: 'Installed',
              links: [{ label: 'About', href: '/about/' }, { label: 'Bad', href: 'javascript:void 0' }],
              actions: [{ id: 'hide-it', label: 'Hide this font' }] } : null),
          });
        }"""
    )
    shown = rows(page)
    assert "sample-sans-03" not in [r[0] for r in shown]
    assert ("sample-serif-04", "4") in shown  # affectsNumbering false: published numbers
    assert page.evaluate(
        "document.getElementById('font-sample-sans-01').classList.contains('is-dim')"
    )
    slot = "#font-sample-serif-04 .font-row > .ext[data-filter='t']"
    assert page.text_content(f"{slot} .ext-badge") == "Installed"
    assert page.text_content(f"{slot} .ext-note") == "You have it"
    assert page.locator(f"{slot} .ext-link").count() == 1
    assert page.get_attribute(f"{slot} .ext-link", "href").endswith("/about/")
    button = f"{slot} button.ext-action[data-action='hide-it']"
    page.click(button)
    assert page.evaluate("window.__tffActions") == [
        {"filterId": "t", "fontId": "sample-serif-04", "actionId": "hide-it"}
    ]
    assert page.evaluate("document.activeElement.dataset.action") == "hide-it"
    # The action hides its row: focus moves to the next shown row's details button.
    page.evaluate(
        "tff.list.addFilter('u', { classify: (id) => (id === 'sample-serif-04' ? 'hide' : 'show') })"
    )
    after = [r[0] for r in rows(page)]
    assert "sample-serif-04" not in after
    focused_row = page.evaluate("document.activeElement.closest('li').dataset.id")
    assert page.evaluate("document.activeElement.className") == "details-toggle"
    assert focused_row == "sample-sans-05"
    # affectsNumbering: true renumbers.
    page.evaluate(
        "tff.list.addFilter('v', { classify: (id) => (id === 'sample-sans-01' ? 'hide' : 'show'), affectsNumbering: true })"
    )
    assert rows(page)[0] == ("sample-mono-02", "1")
    page.evaluate("['t', 'u', 'v'].forEach((id) => tff.list.removeFilter(id))")
    assert rows(page) == expected_rows(sample, {})
    assert page.locator(".ext").count() == 0
    assert page.evaluate("window.__changes.at(-1)") == [40, 40]
    # setState goes through State: pushed, validated, reflected in the controls.
    page.evaluate("tff.list.setState({ cat: 'serif', var: 'nope' })")
    assert hash_of(page) == "#cat=serif"
    assert page.is_checked("#f-cat-serif")
    assert page.evaluate("tff.list.getState().cat") == "serif"
    guarded.assert_clean(page)


@pytest.mark.parametrize(
    ("failure", "note"),
    [
        ("404", "The list was updated. Reload the page to use filters and search."),
        ("network", FAILED_NOTE),
        ("malformed", FAILED_NOTE),
    ],
)
def test_a_missing_index_leaves_the_server_list_with_a_note(
    guarded_context: Any, doc: dict[str, Any], failure: str, note: str
) -> None:
    guarded = guarded_context()
    page = guarded.new_page()

    def fail(route: Any) -> None:
        if failure == "404":
            route.fulfill(status=404, body="gone")
        elif failure == "malformed":  # a list index this script can't read
            route.fulfill(status=200, content_type="application/json", body='{"v": 1, "n": 0}')
        else:
            route.abort()

    page.route("**/assets/list.*.json", fail)
    page.goto("/#cat=serif")
    page.wait_for_selector("#load-note")
    assert page.text_content("#load-note") == note
    # The note sits with the list, above the count, and is not a second live region.
    assert page.evaluate(
        "document.getElementById('load-note').nextElementSibling.id === 'count'"
        " && !document.getElementById('load-note').hasAttribute('role')"
    )
    assert page.evaluate("document.getElementById('filters').hidden") is True
    assert rows(page) == expected_rows(doc, {})
    assert status(page) == ""
    assert guarded.errors, "the failure still reaches the console"
    if failure == "404":
        assert any("The list was updated" in error for error in guarded.errors)


# ----------------------------------------------------------- the large catalog generator


def test_large_catalog_is_deterministic_valid_and_complete(tmp_path: Path) -> None:
    doc_a, specimens = make_large_catalog.make_catalog()
    doc_b, _ = make_large_catalog.make_catalog()
    assert json.dumps(doc_a, sort_keys=True) == json.dumps(doc_b, sort_keys=True)
    assert doc_a["synthetic"] is True
    assert len(doc_a["fonts"]) == make_large_catalog.DEFAULT_FONTS == 540
    assert data.validate(doc_a).fonts == 540
    fonts = doc_a["fonts"]
    for view in (v["key"] for v in doc_a["views"] if v["available"]):
        entries = [f["ranks"][view] for f in fonts if view in f["ranks"]]
        tops = sorted(e["rank"] for e in entries if e["rank"] is not None)
        ranked = sum(1 for e in entries if e["order"] is not None)
        assert tops == list(range(1, min(100, ranked) + 1)), view
        assert any(e["unranked"] for e in entries), view
    assert {f["ranks"]["overall"]["band"] for f in fonts} >= {b["label"] for b in doc_a["bands"]}
    assert {f["id"] for f in fonts if "coding" in f["ranks"]} == {
        f["id"] for f in fonts if f["is_monospace"]
    }
    traits = {
        "mono_filed_elsewhere": any(
            f["is_monospace"] and f["category"] != "monospace" for f in fonts
        ),
        "attribution": any(f["license"]["attribution_required"] for f in fonts),
        "limited": any(f["latin"]["coverage"] == "basic" for f in fonts),
        "preinstalled": any(f["preinstalled_on"] for f in fonts),
        "pulled": any(f["pulled_in_by"] for f in fonts),
        "too_new": any("too_new" in f["flags"] for f in fonts),
    }
    assert all(traits.values()), traits
    assert all(f["license"]["redistributable"] for f in fonts)  # Rule 3
    path = make_large_catalog.write(tmp_path / "large", fonts=60, seed=7)
    written = data.load(path)
    assert data.validate(written).fonts == 60
    for font in written["fonts"]:
        if font["preview"]:
            blob = (path.parent / font["preview"]["path"]).read_bytes()
            assert hashlib.sha256(blob).hexdigest() == font["preview"]["sha256"]
    assert specimens
    assert all(key.startswith("specimens/large-") for key in specimens)
