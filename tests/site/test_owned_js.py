"""site/js/52-owned-parse.js and 54-owned-match.js against the Python reference, tff_catalog.owned.

The hopper's parser and matcher (Milestone 3, slice 1; site/CONTRACT.md sections 5 and 11) are
a module of their own, ``/assets/matcher.<h>.js`` (the owner's ruling of 2026-10-23,
``inventory_matcher_on_demand``): ``assets.concat_js`` of the matcher's parts, 05-keys.js among
them, as the build writes it. A page on a routed origin, which answers only the page and that
file, loads it with ``<script type="module" src>`` and the tests take its hand-off,
``globalThis.tffMatcher``. In each engine given with ``--browser`` (CI runs Chromium and
Firefox):

- their constants equal Python's, which ``tests/vectors/owned-fonts.json``'s ``spec`` records;
- every parse, candidates and match vector in that file gives its expected result, and every
  malformed match file in it is refused;
- every known answer (``tests/vectors/owned-known-answers.json``) gives Python's result on the
  match file the site build makes from the committed ``build/names.json`` (its reviewed recodes
  included: HackGen is only related to Hack, ``hopper_hackgen_related``). Whether its
  expectation holds is a test of its own, with the strict xfails the file names, so an xfail
  never hides a difference;
- names from every assigned code point, damaged files in each encoding, fc-list escapes,
  prompts, JSON with duplicate keys, entries, mutated match files and other words, drawn by
  Hypothesis, give Python's result, and so do four realistic lists of 10,000 fonts and 5 MB
  of ``system_profiler -json`` cut short;
- 10 MB of escaped quotes parse in linear time;
- the parts are pure code: they name no network, page, frame, worker, storage, clock or
  randomness API; no such global is read while they load and run, and they make no request;
  a table keeps nothing of the lists matched against it, and once parse, match or candidates
  returns, the engine's legacy RegExp statics hold nothing of the list;
- the module hands over exactly ``{ OwnedParse, OwnedMatch }``, frozen, as a global that can't
  be written, deleted or listed.

How fast they are (Milestone 3 steps 4 and 5) is test_perf.py's to measure, in CI's site-perf
job.

``Data.loadMatch`` and ``Data.loadMatcher`` (site/js/10-data.js) are tested here too, on a
routed page with a stub list: one GET of the path in ``#list[data-match]``, memoised; a 404 or
410 of it or of the list index rejects with ``Data.Stale``, and any other failure with a
``Data.MatchError`` and its code; one ``<script type="module">`` for the path in
``#list[data-matcher]``, memoised, resolving with the module's hand-off, and each way it can
fail (no load, no usable hand-off, no answer in ``MATCHER_TIMEOUT_MS``, on Playwright's clock) a
``Data.MatchError`` with its code; a URL off the page's origin is never requested; and the
parser and matcher that loadMatcher hands over match a list on loadMatch's file as Python does.

A parse input reaches the page as the vector file spells it (``owned_vectors.spelled``): a lone
surrogate can't cross to the page as a string, so a text that holds one goes as UTF-16 code
units, and a text that holds a high and a low surrogate side by side, which Python reads as two
code points and JavaScript as one character, is refused. These tests need no built site.
"""

import contextlib
import functools
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.fixtures import hopper_lists
from tests.helpers import owned_vectors as ov

from tff_catalog import owned
from tff_site import assets, matchfile

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "site" / "js"
PARTS = (JS / "05-keys.js", JS / "52-owned-parse.js", JS / "54-owned-match.js")
DATA_PART = JS / "10-data.js"
# The matcher module as the build writes it, at the path the build would give it.
MATCHER = assets.concat_js(JS, assets.MATCHER_PARTS)
MATCHER_PATH = "/assets/" + assets.hashed_name("matcher.js", MATCHER.encode())
VECTORS = json.loads(ov.VECTORS.read_text(encoding="utf-8"))
KNOWN = json.loads(ov.KNOWN.read_text(encoding="utf-8"))
NAMES = ROOT / "build" / "names.json"
CATALOG = ROOT / "build" / "catalog-site.json"
LINEAR_MS = 1000
# A routed origin, as test_list.py uses (port 9 is one browsers refuse to load).
ORIGIN = "http://127.0.0.1:59999"

# The matcher module, then a module that ends the watch WATCH_JS starts (modules run in
# document order).
PAGE = (
    "<!doctype html><html lang=en><meta charset=utf-8><title>owned</title>"
    f'<script type=module src="{MATCHER_PATH}"></script>'
    "<script type=module>globalThis.__tffWatch = false;</script>"
)
# test_keys_js.py's rule, wider: no network, page, frame, worker, storage, clock or
# randomness name outside comments.
ENVIRONMENT = re.compile(
    r"\b(?:fetch|XMLHttpRequest|sendBeacon|WebSocket|EventSource|import|document|window"
    r"|navigator|location|globalThis|localStorage|sessionStorage|indexedDB|caches|cookieStore"
    r"|Date|random|setTimeout|setInterval|requestIdleCallback|requestAnimationFrame"
    r"|performance|crypto|self|top|parent|opener|frames|history|Worker|SharedWorker"
    r"|BroadcastChannel|MessageChannel|postMessage|Image|Audio|FileReader|Blob|URL"
    r"|Notification)\b"
)
# The globals WATCH_JS watches: ENVIRONMENT's, but for the keywords and globalThis. Those the
# page can't redefine ([LegacyUnforgeable] in Web IDL) are the static rule's alone.
WATCHED = (
    "fetch", "XMLHttpRequest", "WebSocket", "EventSource", "navigator", "localStorage",
    "sessionStorage", "indexedDB", "caches", "cookieStore", "Date", "setTimeout", "setInterval",
    "requestIdleCallback", "requestAnimationFrame", "performance", "crypto", "self", "parent",
    "opener", "frames", "history", "Worker", "SharedWorker", "BroadcastChannel",
    "MessageChannel", "postMessage", "Image", "Audio", "FileReader", "Blob", "URL",
    "Notification", "document", "window", "location", "top",
)  # fmt: skip
UNFORGEABLE = frozenset({"document", "window", "location", "top"})
# An init script: from before the parts load until the page's last line, and while a test
# sets __tffWatch, any read of a WATCHED global or a call of Math.random is recorded.
WATCH_JS = """
(() => {
  const touched = [];
  const watched = [];
  globalThis.__tffWatch = true;
  for (const name of WATCHED_NAMES) {
    const real = globalThis[name];
    try {
      Object.defineProperty(globalThis, name, {
        configurable: true,
        get() {
          if (globalThis.__tffWatch) touched.push(name);
          return real;
        },
      });
      watched.push(name);
    } catch (error) {
      // [LegacyUnforgeable]: the page can't redefine it.
    }
  }
  const random = Math.random;
  Math.random = () => {
    if (globalThis.__tffWatch) touched.push('Math.random');
    return random();
  };
  Object.defineProperty(globalThis, '__tffTouched', { value: touched });
  Object.defineProperty(globalThis, '__tffWatched', { value: watched });
})();
""".replace("WATCHED_NAMES", json.dumps(WATCHED))
# In the page: a parse input as the vectors spell it (inputOf).
HELPERS = """
const inputOf = (c) => {
  if (c.base64 !== undefined) return Uint8Array.from(atob(c.base64), (ch) => ch.charCodeAt(0));
  if (c.parts !== undefined) {
    const s = c.parts.map(([t, n]) => t.repeat(n)).join('');
    return c.bytes ? new TextEncoder().encode(s) : s;
  }
  if (c.units === undefined) return c.text;
  let s = '';
  for (let i = 0; i < c.units.length; i += 4096) {
    s += String.fromCharCode(...c.units.slice(i, i + 4096));
  }
  return s;
};
"""


def candidates_of(table: owned.Table, name: str) -> list[dict[str, Any]]:
    """Python's candidates as the page returns them."""
    return [
        {"key": c.key, "text": c.text, "patched": c.patched, "postscript": c.postscript}
        for c in table.candidates(name)
    ]


@dataclass
class Loaded:
    """A page with the parts loaded, the errors it raised and every request its context made."""

    page: Any
    errors: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)

    def run(self, body: str, arg: Any = None) -> Any:
        """Evaluate ``(P, M, arg) => body`` in the page, with ``P`` OwnedParse and ``M`` OwnedMatch."""
        return self.page.evaluate(
            f"(arg) => {{ {HELPERS} const o = globalThis.tffMatcher;"
            f" return ((P, M, arg) => {body})(o.OwnedParse, o.OwnedMatch, arg); }}",
            arg,
        )


def _load(browser: Any, *, watch: bool = False) -> tuple[Any, Loaded]:
    """A new context and a page in it at ORIGIN with the matcher module loaded. The route
    answers the page and the module and nothing else (404), so no request leaves the test;
    with ``watch``, WATCH_JS runs first."""

    def answer(route: Any) -> None:
        if route.request.url == f"{ORIGIN}/":
            route.fulfill(status=200, content_type="text/html", body=PAGE)
        elif route.request.url == f"{ORIGIN}{MATCHER_PATH}":
            route.fulfill(status=200, content_type="text/javascript", body=MATCHER)
        else:
            route.fulfill(status=404, body="")

    context = browser.new_context()
    context.route(f"{ORIGIN}/**", answer)
    if watch:
        context.add_init_script(WATCH_JS)
    result = Loaded(context.new_page())
    context.on("request", lambda request: result.requests.append(request.url))
    result.page.on("pageerror", lambda error: result.errors.append(str(error)))
    result.page.goto(f"{ORIGIN}/", wait_until="load")
    return context, result


@pytest.fixture(scope="module")
def loaded(browser: Any) -> Iterator[Loaded]:
    """One page per engine with the parts loaded, and two tables in it: the vector file's
    (``globalThis.__tffVectors``) and the real names.json's (``__tffReal``)."""
    context, result = _load(browser)
    result.run("(globalThis.__tffVectors = M.table(arg), true)", VECTORS["document"])
    result.run("(globalThis.__tffReal = M.table(arg), true)", real()[0])
    yield result
    context.close()


@functools.cache
def real() -> tuple[dict[str, Any], owned.Table, tuple[str, ...]]:
    """The match file the site build makes from the committed build/names.json and
    catalog-site.json, with the reviewed list beside them; its Python table; and every name
    names.json knows (for Hypothesis)."""
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    doc = json.loads(matchfile.match_file(catalog, NAMES).blob)
    names = json.loads(NAMES.read_text(encoding="utf-8"))
    known = {f["family"] for f in names["families"]} | {
        n["name"] for f in names["families"] for n in f["names"]
    }
    return doc, owned.Table.from_document(doc), tuple(sorted(known))


@functools.cache
def vector_table() -> owned.Table:
    return owned.Table.from_document(VECTORS["document"])


@functools.cache
def realistic() -> dict[str, str]:
    """hopper_lists' four lists of 10,000 fonts from the committed names.json, and the 5 MB
    system_profiler paste."""
    names = json.loads(NAMES.read_text(encoding="utf-8"))
    return {**hopper_lists.lists(names), "5 MB cut": hopper_lists.system_profiler_cut()}


def parse_in_page(loaded: Loaded, inputs: list[bytes | str]) -> list[dict[str, Any]]:
    return loaded.run("arg.map((c) => P.parse(inputOf(c)))", [ov.spelled(data) for data in inputs])


def match_in_page(
    loaded: Loaded, table: str, runs: list[tuple[list[dict[str, Any]], bool]]
) -> list[dict[str, Any]]:
    body = f"arg.map(([entries, patched]) => M.match(entries, globalThis.{table}, {{ patched }}))"
    return loaded.run(body, runs)


# In the page: each match vector on the file's table, or on a table of its own document's,
# as JSON text.
MATCH_VECTORS = """arg.map(([entries, patched, doc]) => JSON.stringify(
  M.match(entries, doc === null ? globalThis.__tffVectors : M.table(JSON.parse(doc)), { patched })))"""


def match_vectors_in_page(loaded: Loaded, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The page's result for each match vector. Its own document goes to the page as JSON text,
    read with JSON.parse as Data.loadMatch reads one, and every result comes back as JSON text:
    Playwright's own serialization drops a "__proto__" key both ways, and the vectors hold ids
    and reasons of that name."""
    runs = [
        (c["entries"], c["patched"], json.dumps(c["document"]) if "document" in c else None)
        for c in cases
    ]
    return [json.loads(text) for text in loaded.run(MATCH_VECTORS, runs)]


# ---------------------------------------------------------------- the parts themselves

PARSE = VECTORS["parse"]
CANDIDATES = VECTORS["candidates"]
MATCH = VECTORS["match"]


def test_the_matcher_module_loads_without_errors(loaded):
    assert loaded.errors == []
    assert loaded.run("[typeof P, typeof M]") == ["object", "object"]
    assert MATCHER.count("\nconst ") + MATCHER.startswith("const ") == len(assets.MATCHER_PARTS)


def test_parts_name_no_environment_api():
    for path in PARTS[1:]:
        code = "\n".join(line.split("//", 1)[0] for line in path.read_text("utf-8").splitlines())
        assert ENVIRONMENT.findall(code) == [], path.name


# Every vector, and both tables, in a page of its own: what the purity test runs.
WORKLOAD = """(() => {
  globalThis.__tffWatch = true;
  try {
    const tables = [M.table(arg.document), M.table(arg.real)];
    arg.parse.forEach((c) => P.parse(inputOf(c)));
    arg.candidates.forEach((name) => M.candidates(name, tables[0]));
    arg.match.forEach(([entries, patched, doc]) =>
      M.match(entries, doc === null ? tables[0] : M.table(doc), { patched }));
    arg.known.forEach((entries) => M.match(entries, tables[1]));
    arg.malformed.forEach((doc) => {
      try {
        M.table(doc);
      } catch (e) {
        if (e.code !== 'malformed') throw e;
      }
    });
  } finally {
    globalThis.__tffWatch = false;
  }
  return [globalThis.__tffTouched, globalThis.__tffWatched];
})()"""


def test_parts_read_no_environment_global_and_make_no_request(browser):
    """In a page where every WATCHED global records its reads, loading the parts and running
    every vector through them reads none, and the context asks for nothing but the page."""
    context, page = _load(browser, watch=True)
    try:
        arg = {
            "document": VECTORS["document"],
            "real": real()[0],
            "parse": [c["input"] for c in PARSE if "parts" not in c["input"]],
            "candidates": [c["name"] for c in CANDIDATES],
            "match": [[c["entries"], c["patched"], c.get("document")] for c in MATCH],
            "known": [[c["entry"]] for c in KNOWN],
            "malformed": [c["document"] for c in VECTORS["malformed"]],
        }
        touched, watched = page.run(WORKLOAD, arg)
    finally:
        context.close()
    assert touched == []
    assert set(watched) == set(WATCHED) - UNFORGEABLE
    assert page.requests == [f"{ORIGIN}/", f"{ORIGIN}{MATCHER_PATH}"]
    assert page.errors == []


def test_parts_expose_exactly_their_members_frozen(loaded):
    assert loaded.run("Object.keys(P)") == [
        "parse", "LIMITS", "FORMATS", "ERRORS", "WARNINGS", "COMMAND_WORDS", "FONT_EXTENSIONS",
        "WHITESPACE", "JOINERS",
    ]  # fmt: skip
    assert loaded.run("Object.keys(M)") == [
        "table", "match", "candidates", "FORMAT", "VERSION", "HOW", "WHY", "OUTCOMES",
    ]  # fmt: skip
    frozen = "[P, M, ...Object.values(P), ...Object.values(M)].filter((v) => typeof v === 'object')"
    assert loaded.run(f"{frozen}.every((v) => Object.isFrozen(v))") is True


# In the page: the hand-off, then attempts to replace, delete and redefine it.
HAND_OFF = """(() => {
  const api = globalThis.tffMatcher;
  const slot = Object.getOwnPropertyDescriptor(globalThis, 'tffMatcher');
  globalThis.tffMatcher = { OwnedParse: null };
  const deleted = delete globalThis.tffMatcher;
  let redefined = true;
  try {
    Object.defineProperty(globalThis, 'tffMatcher', { value: {} });
  } catch (error) {
    redefined = false;
  }
  api.extra = 1;
  return {
    keys: Object.keys(api), frozen: Object.isFrozen(api), added: 'extra' in api,
    slot: [slot.writable, slot.enumerable, slot.configurable, 'get' in slot],
    listed: Object.keys(globalThis).includes('tffMatcher'),
    same: globalThis.tffMatcher === api, deleted, redefined,
  };
})()"""


def test_the_module_hands_over_its_parts_frozen_as_a_global_nothing_can_replace(loaded):
    """inventory_matcher_on_demand: the module's one way to the page (58-matcher.js) is a
    global that can't be written, deleted, redefined or listed, holding a frozen object of
    exactly the two parts; nothing can be added to it."""
    assert loaded.run(HAND_OFF) == {
        "keys": ["OwnedParse", "OwnedMatch"],
        "frozen": True,
        "slot": [False, False, False, False],
        "listed": False,
        "same": True,
        "deleted": False,
        "redefined": False,
        "added": False,
    }


def test_a_table_keeps_nothing_of_the_lists_matched_against_it(loaded):
    """A table is the file, its rules and its keys by hash; what match works out lives only
    during the call."""
    body = """(() => {
      const t = globalThis.__tffReal;
      const sizes = () => [t.k.size, t.amb.size, Object.keys(t.rules).length, t.hashed.size];
      const before = sizes();
      M.match(P.parse(Array.from({ length: 500 }, (_, i) => `Zeta ${i} Bold`).join('\\n')), t);
      return [Object.keys(t), Object.isFrozen(t), before, sizes()];
    })()"""
    keys, frozen, before, after = loaded.run(body)
    assert (keys, frozen, before) == (["ids", "k", "amb", "rules", "hashed"], True, after)


# Lists whose text a regex would leave in the RegExp statics, each as the page gets it.
_PROFILER = {"SPFontsDataType": [{"_name": "Secret.ttf", "typefaces": [
    {"_name": "SecretGrotesk-Bold", "family": "Secret Grotesk", "fullname": "Secret Grotesk Bold"}
]}]}  # fmt: skip
LEFTOVER_LISTS = {
    "a paste of names": "Secret Studio Grotesk\nInter\nFira Code Bold\nZzz Private Name Italic",
    "fc-list lines": "Secret Sans,Secret Sans Bold\n/usr/share/fonts/secret.ttf: Secret Mono:style=Bold",
    "a registry paste": "Secret Serif Bold (TrueType)\nSecret Serif & Secret Serif Italic (TrueType)",
    "system_profiler -json, dropped": json.dumps(_PROFILER).encode(),
    "an html page": "<!doctype html><title>Secret page</title><p>Secret Studio Grotesk</p>",
    "names past ASCII": "\u00c9criture Secr\u00e8te Bold\n\u793a\u4f8b Secret 12\nZ\u00fc Light NF",
    "a pasted command": "fc-list : family | grep Secret",
}
# In the page: each list's parse, match and candidates, with a regex run on other text before
# each call, and the legacy RegExp statics after it.
STATICS = """(() => {
  const statics = () => [RegExp.input, RegExp.lastMatch, RegExp.lastParen, RegExp.leftContext,
    RegExp.rightContext, RegExp.$1, RegExp.$2, RegExp.$3, RegExp.$4, RegExp.$5, RegExp.$6,
    RegExp.$7, RegExp.$8, RegExp.$9];
  const other = () => /(o)th(e)r/.exec('some other text');
  other();
  const parsed = P.parse(inputOf(arg));
  const afterParse = statics();
  other();
  M.match(parsed, globalThis.__tffReal);
  const afterMatch = statics();
  const first = parsed.entries[0];
  const name = first ? first.families[0] || first.full : 'Secret Sans Bold';
  other();
  M.candidates(name, globalThis.__tffReal);
  return { afterParse, afterMatch, afterCandidates: statics(), entries: parsed.entries.length };
})()"""


@pytest.mark.parametrize("label", LEFTOVER_LISTS)
def test_no_regexp_static_holds_anything_of_a_list_once_a_call_returns(loaded, label):
    """Every successful match leaves its text in RegExp.input, lastMatch and the other legacy
    statics until the next one; parse, match and candidates each leave '' in all of them."""
    got = loaded.run(STATICS, ov.spelled(LEFTOVER_LISTS[label]))
    empty = [""] * 14
    assert (got["afterParse"], got["afterMatch"], got["afterCandidates"]) == (empty,) * 3


def test_constants_equal_python_s(loaded):
    spec = VECTORS["spec"]
    got = loaded.run(
        "({limits: P.LIMITS, formats: P.FORMATS, errors: P.ERRORS, warnings: P.WARNINGS,"
        " command_words: P.COMMAND_WORDS, font_extensions: P.FONT_EXTENSIONS,"
        " whitespace: P.WHITESPACE, joiners: P.JOINERS, format: M.FORMAT, version: M.VERSION,"
        " how: M.HOW, why: M.WHY, outcomes: M.OUTCOMES})"
    )
    assert got == {
        "limits": spec["limits"],
        "formats": spec["formats"],
        "errors": spec["errors"],
        "warnings": spec["warnings"],
        "command_words": spec["command_words"],
        "font_extensions": spec["font_extensions"],
        "whitespace": spec["words"]["whitespace"],
        "joiners": spec["words"]["joiners"],
        "format": owned.FORMAT,
        "version": owned.VERSION,
        "how": list(owned.HOW),
        "why": list(owned.WHY),
        "outcomes": list(owned.OUTCOMES),
    }
    assert spec["words"] == owned.WORDS
    assert spec["limits"] == {
        name.lower(): getattr(owned, name)
        for name in ("MAX_BYTES", "MAX_CHARS", "MAX_LINE", "MAX_NAME", "SNIFF_BYTES",
                     "SNIFF_HTML", "SNIFF_JSON", "FEW_NAMES", "CUT_OFF_LINES", "FC_LIST_ONE_IN")
    }  # fmt: skip


def test_every_parse_vector_reaches_the_page_as_python_reads_it():
    """A vector's input, other than ``parts`` (which the page builds as data_of does), is in
    the one spelling the page can hold: spelled(data_of(input)) gives it back."""
    for c in PARSE:
        if "parts" not in c["input"]:
            assert ov.spelled(ov.data_of(c["input"])) == c["input"], c["label"]


# ---------------------------------------------------------------- the vectors


@pytest.fixture(scope="module")
def parse_results(loaded: Loaded) -> list[dict[str, Any]]:
    """The page's result for every parse vector, each input as the file spells it."""
    body = "arg.map((c) => { try { return P.parse(inputOf(c)); } catch (e) { return String(e); } })"
    return loaded.run(body, [c["input"] for c in PARSE])


@pytest.mark.parametrize("index", range(len(PARSE)), ids=[c["label"] for c in PARSE])
def test_parse_vector(parse_results, index):
    assert parse_results[index] == PARSE[index]["expect"]


@pytest.fixture(scope="module")
def candidates_results(loaded: Loaded) -> list[list[dict[str, Any]]]:
    return loaded.run(
        "arg.map((name) => M.candidates(name, globalThis.__tffVectors))",
        [c["name"] for c in CANDIDATES],
    )


@pytest.mark.parametrize("index", range(len(CANDIDATES)), ids=[c["name"] for c in CANDIDATES])
def test_candidates_vector(candidates_results, index):
    assert candidates_results[index] == CANDIDATES[index]["expect"]


@pytest.fixture(scope="module")
def match_results(loaded: Loaded) -> list[dict[str, Any]]:
    return match_vectors_in_page(loaded, MATCH)


@pytest.mark.parametrize("index", range(len(MATCH)), ids=[c["label"] for c in MATCH])
def test_match_vector(match_results, index):
    assert match_results[index] == MATCH[index]["expect"]


REFUSED = """arg.map((text) => {
  try {
    M.table(JSON.parse(text));
    return null;
  } catch (e) {
    return e.code || String(e);
  }
})"""


@pytest.fixture(scope="module")
def malformed_results(loaded: Loaded) -> list[str | None]:
    return loaded.run(REFUSED, [json.dumps(c["document"]) for c in VECTORS["malformed"]])


@pytest.mark.parametrize(
    "index", range(len(VECTORS["malformed"])), ids=[c["label"] for c in VECTORS["malformed"]]
)
def test_a_malformed_match_file_is_refused(malformed_results, index):
    """As Table.from_document refuses it (tests/test_owned.py), with the code PR 4 shows."""
    assert malformed_results[index] == "malformed"


def _expectation_holds(c: dict[str, Any], result: dict[str, Any]) -> bool:
    outcome = next((o for o in owned.OUTCOMES if result["counts"][o]), None)
    return (
        all(result["owned"].get(fid, {}).get("how") == how for fid, how in c["owned"].items())
        and not set(c["not_owned"]) & set(result["owned"])
        and set(c["near"]) <= set(result["near"])
        and (c["outcome"] is None or c["outcome"] == outcome)
    )


@pytest.fixture(scope="module")
def known_results(loaded: Loaded) -> list[dict[str, Any]]:
    return match_in_page(loaded, "__tffReal", [([c["entry"]], c["patched"]) for c in KNOWN])


@pytest.mark.parametrize("index", range(len(KNOWN)), ids=[c["label"] for c in KNOWN])
def test_known_answer_gives_python_s_result(known_results, index):
    """Every known answer, those the file marks xfail too: any difference from Python is a bug."""
    c = KNOWN[index]
    python = owned.match([ov.entry_of(c["entry"])], real()[1], patched=c["patched"]).to_json()
    assert known_results[index] == python


@pytest.mark.parametrize(
    "index",
    [
        pytest.param(i, marks=pytest.mark.xfail(strict=True, reason=c["xfail"]))
        if c.get("xfail")
        else i
        for i, c in enumerate(KNOWN)
    ],
    ids=[c["label"] for c in KNOWN],
)
def test_known_answer_holds(known_results, index):
    assert _expectation_holds(KNOWN[index], known_results[index])


# ---------------------------------------------------------------- inputs no vector holds

# '["' and 5 million escaped quotes: a scan that tokenizes all of the text first reads them once
# per quote.
ESCAPED_QUOTES = [['["', 1], ['\\"', 4_999_990]]


def test_ten_megabytes_of_escaped_quotes_parse_in_linear_time(loaded):
    """Read once. A scan that read them once per quote took 1.2 s for 80 KB, so 10 MB would
    take hours; the bound is far above a linear scan."""
    c = {"parts": ESCAPED_QUOTES}
    body = """(() => {
      const text = inputOf(arg);
      const t0 = performance.now();
      const parsed = P.parse(text);
      return { ms: performance.now() - t0, parsed };
    })()"""
    got = loaded.run(body, c)
    assert got["parsed"] == owned.parse(ov.data_of(c)).to_json()
    assert got["ms"] < LINEAR_MS, got["ms"]


@pytest.mark.parametrize("label", [*hopper_lists.LABELS, "5 MB cut"])
def test_realistic_lists_parse_and_match_as_in_python(loaded, label):
    """The lists test_perf.py times: 10,000 fonts as the registry, fc-list and system_profiler
    give them, fc-list's with a name past ASCII on each line, and a system_profiler paste cut
    at 5 MB."""
    text = realistic()[label]
    body = "(() => { const p = P.parse(arg); return [p, M.match(p, globalThis.__tffReal)]; })()"
    got = loaded.run(body, text)
    parsed = owned.parse(text)
    assert got == [parsed.to_json(), owned.match(parsed, real()[1]).to_json()]
    assert got[1]["counts"]["entries"] > 8000


# ---------------------------------------------------------------- random inputs, as in Python

SETTINGS = settings(max_examples=30, derandomize=True, deadline=None)
_name_piece = st.sampled_from(
    ["Inter", "Fira", "Code", "Sans", "Mono", "Condensed", "Tight", "-", "_", "\u2013", " ",
     "Bold", "Italic", "Extra", "Semi", "Light", "NF", "NFM", "Nerd Font", "nerd font mono",
     "CN", "PL", "NL", "Variable", "VF", "var", "Regular", "BoldItalic", "Black-Regular",
     "ItalicBold", "BoldExtra", "It", "Lt", "Med", "x", "9", "\u00c9", "\u2122", "\u200d",
     "\u00a0", "\uff26", "\U0001d504"]
)  # fmt: skip
# Any assigned code point: an engine whose Unicode is newer than Python's keys a code point
# Python calls unassigned (Cn) differently, so neither side draws one (test_keys_js.py's rule),
# and a name the engine calls unassigned is skipped in the page.
_assigned = st.characters(exclude_categories=("Cn", "Co", "Cs"))
_names = st.one_of(
    st.lists(_name_piece, max_size=7).map("".join), st.text(alphabet=_assigned, max_size=30)
)
_file_piece = st.sampled_from(
    ["Archivo", "Inter Tight", "\u66f4\u7d17\u30b4\u30b7\u30c3\u30af UI J", "\U0001d504 Sans",
     "\r\n", "\n", "\r", " ", "\t", "\ufeff", "\u200b", "\u2028", ",", "\\,", "\u00e9", ".Hidden"]
)  # fmt: skip
_codecs = st.sampled_from(["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"])


def _damaged(args: tuple[str, str, int, bytes]) -> bytes:
    text, codec, at, junk = args
    raw = text.encode(codec)
    return raw[:at] + junk + raw[at:]


_files = st.tuples(
    st.lists(_file_piece, max_size=30).map("".join),
    _codecs,
    st.integers(0, 300),
    st.sampled_from([b"", b"\xff", b"\xc3", b"\x00\xd8", b"\xed\xa0\x80", b"\x00"]),
).map(_damaged)
_line_piece = st.sampled_from(
    ["Foo", "Bar Sans", ",", "\\,", "\\\\", "\\-", "\\:", "\\x", ", ", ":style=Bold",
     "/usr/share/fonts/a.ttf: ", "C:\\Windows\\Fonts\\a.ttf: ", ".Hidden", " (TrueType)",
     " (OpenType) : a.ttf", " : b.otf", " & ", "user@host:~$ ", "PS C:\\x> ", "$ ", "\u276f ",
     "sudo ", "fc-list : family", "Get-Item", "Fonts  ", "----", " ----", "Name", "Hive:",
     "PSPath : x", "+ ", "^~~", "command not found", "Sudo", "(", ")", "  ", "\t", '"', '\\"',
     "[", "{", ":"]
)  # fmt: skip
_listings = st.lists(st.lists(_line_piece, max_size=8).map("".join), min_size=1, max_size=40)
_json_key = st.sampled_from(["family", "fullname", "_name", "path", "typefaces", "0", "12", "x"])
_json_leaf = st.one_of(
    st.none(), st.booleans(), st.integers(-5, 5), st.sampled_from(["Archivo", "Inter Bold", ""])
)
_json = st.recursive(
    _json_leaf,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(_json_key, inner, max_size=5)
    ),
    max_leaves=25,
)


def _json_text(args: tuple[Any, bool, int, bool]) -> str:
    """JSON as system_profiler or ConvertTo-Json writes it, maybe cut at the end or the top."""
    value, indent, cut, at_top = args
    text = json.dumps(value, indent=2 if indent else None)
    return text[cut:] if at_top else text[: max(1, len(text) - cut)]


def _members(args: tuple[list[tuple[str, Any]], int]) -> str:
    """An object whose keys may repeat, holding objects and lists, maybe cut at the end."""
    pairs, cut = args
    text = "{" + ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in pairs) + "}"
    return text[: max(1, len(text) - cut)]


_json_texts = st.one_of(
    st.tuples(_json, st.booleans(), st.integers(0, 40), st.booleans()).map(_json_text),
    st.tuples(st.lists(st.tuples(_json_key, _json), max_size=6), st.integers(0, 8)).map(_members),
)


@SETTINGS
@given(st.lists(_names, min_size=1, max_size=100))
def test_random_names_give_python_s_candidates(loaded, names):
    body = "arg.map((n) => (/\\p{Cn}/u.test(n) ? null : M.candidates(n, globalThis.__tffVectors)))"
    got = loaded.run(body, names)
    want = [candidates_of(vector_table(), name) for name in names]
    pairs = [(g, w) for g, w in zip(got, want, strict=True) if g is not None]
    assert [g for g, _ in pairs] == [w for _, w in pairs]


@SETTINGS
@given(st.lists(st.one_of(_files, st.binary(max_size=200)), min_size=1, max_size=40))
def test_random_files_parse_as_in_python(loaded, files):
    assert parse_in_page(loaded, files) == [owned.parse(data).to_json() for data in files]


@SETTINGS
@given(st.lists(_listings, min_size=1, max_size=20), st.booleans())
def test_random_listings_parse_as_in_python(loaded, listings, dropped):
    inputs: list[bytes | str] = ["\n".join(lines) for lines in listings]
    if dropped:
        inputs = [text.encode("utf-8") for text in inputs]
    assert parse_in_page(loaded, inputs) == [owned.parse(data).to_json() for data in inputs]


@SETTINGS
@given(st.lists(_json_texts, min_size=1, max_size=30))
def test_random_json_parses_as_in_python(loaded, texts):
    """Whole, cut or with duplicate keys: both sides scan every JSON text the same way."""
    assert parse_in_page(loaded, texts) == [owned.parse(text).to_json() for text in texts]


_suffix = st.sampled_from(
    ["", " Bold", " Italic", " Black", " NF", " Nerd Font Mono", "NF", " CN", " PL", " NL",
     " Variable", "Variable", " Condensed", " Book", "-Regular", "-BoldItalic", " BlackItalic",
     " Med", " 2"]
)  # fmt: skip
_known_name = st.deferred(lambda: st.sampled_from(real()[2]))
_real_name = st.tuples(_known_name, _suffix, _suffix).map("".join)
_entry = st.one_of(
    st.tuples(st.lists(_real_name, min_size=1, max_size=3), st.booleans()).map(
        lambda t: owned.Entry(tuple(t[0]), trusted=t[1])
    ),
    _real_name.map(lambda n: owned.Entry(full=n)),
    _real_name.map(lambda n: owned.Entry(postscript=n.replace(" ", ""))),
)


@SETTINGS
@given(st.lists(st.tuples(st.lists(_entry, min_size=1, max_size=12), st.booleans()), min_size=1))
def test_random_entries_match_as_in_python(loaded, runs):
    got = match_in_page(
        loaded, "__tffReal", [([e.to_json() for e in entries], p) for entries, p in runs]
    )
    assert got == [owned.match(entries, real()[1], patched=p).to_json() for entries, p in runs]


# Names that put a file's words to work: separators, Nerd phrases in any case, style and
# PostScript words, build tokens, variable words, and letters other scripts fold with ASCII.
PROBES = (
    "Fira Code", "FiraCode NF", "Fira Code Nerd Font Mono", "FIRA CODE NERD FONT", "Hack NL",
    "JetBrainsMono-BoldItalic", "Inter Tight Bold Italic", "Roboto Condensed", "Maple Mono NF CN",
    "Cascadia Code PL", "Recursive Variable", "RecursiveVariable", "Foo_Bar-Regular",
    "Foo\u00e9nerdfont", "Foo\u00c9NERDFONT", "Foo\u00ff Nerd Font", "Foo\u0178 NF", "FooAnerdAfont",
    "Foo a Nerd a Font", "\u00dcn\u00ef Sans Bold", "\uff26\uff49\uff52\uff41 Code",
    "Foo\u2013Bold", "Foo\u200dBar", "Foo\u00a0Bold", "\u0391\u03b2\u03b3 \u03a3ans Bold",
    "Stra\u00dfe Light", "Kelvin \u212a NF", "Foo\u017f Nerd Font", "\u0130stanbul Sans",
    "Foo  Bold", " Foo Bold ", "Bold", "Source Sans Pro", "Noto Sans JP", "HackGen", "Archivo Black",
    "Archivo BlackItalic", "Archivo Black-Regular", "ArchivoBlack-Regular", "Archivo-Black-Bold",
)  # fmt: skip
PROBE_ENTRIES = [
    *(owned.Entry((name,), trusted=True) for name in PROBES),
    owned.Entry(full="Fira Code Bold"),
    owned.Entry(postscript="InterTight-Bold"),
    owned.Entry(postscript="Archivo-Black"),
    owned.Entry(("Roboto Slab", "Roboto"), trusted=False),
]
# Values a hand-made or damaged match file might hold where the build writes something else:
# among them code points of letters that fold with others (Latin-1, Greek, the Kelvin sign).
_ODD = [True, False, None, 0, 1, 1.0, 0.0, -0.0, 2, -1, 1.5, 1e21, "", "1", "abc", "0x20",
        "2_0", " 20", "D800", "DFFF-E000", "110000", "0020-001F", "0020-0030-0040", "-", "0020-",
        "\u00e9", "nerd\u2003font", "\t", " ", "a b", [], [""], ["x"], [1], {}, {"0": 1},
        "0041", "0061", "007A", "002E", "007F", "00A0", "00C9", "00E9", "00DF", "00FF", "0178",
        "0391", "03B1", "03A3", "03C2", "03C3", "0130", "0131", "017F", "212A", "2010-2015",
        "10020", "Bold", "NF"]  # fmt: skip


def _mutated(args: tuple[tuple[str, ...], Any, str]) -> str:
    """The real match file with one value set, dropped, appended to or put first in a list."""
    path, value, how = args
    doc = json.loads(json.dumps(real()[0]))
    target = doc
    for key in path[:-1]:
        target = target[key]
    key = path[-1]
    if how == "drop":
        target.pop(key, None)
    elif how == "append" and isinstance(target.get(key), list):
        target[key] = [*target[key], value]
    elif how == "first" and isinstance(target.get(key), list) and target[key]:
        target[key] = [value, *target[key][1:]]
    else:
        target[key] = value
    return json.dumps(doc)


# In the page: each match file refused (its code), or its candidates of the probes and its
# match of the probe entries.
UNDER_EACH = """(() => arg.texts.map((text) => {
  let table;
  try {
    table = M.table(JSON.parse(text));
  } catch (e) {
    return e.code || String(e);
  }
  return {
    candidates: arg.names.map((name) => M.candidates(name, table)),
    match: M.match(arg.entries, table),
  };
}))()"""


def _under_each_in_python(
    texts: list[str], names: list[str], entries: list[owned.Entry]
) -> list[Any]:
    out: list[Any] = []
    for text in texts:
        try:
            table = owned.Table.from_document(json.loads(text))
        except ValueError:
            out.append("malformed")
            continue
        out.append(
            {
                "candidates": [candidates_of(table, name) for name in names],
                "match": owned.match(entries, table).to_json(),
            }
        )
    return out


def _compare_under_each(loaded: Loaded, texts: list[str], names: list[str]) -> None:
    """Both readers refuse the same files, and on every file they accept give the same
    candidates and the same match: parity under any accepted words, not only the build's.
    Names the engine calls unassigned are left out (see _assigned)."""
    unassigned = loaded.run("arg.map((n) => /\\p{Cn}/u.test(n))", names)
    names = [name for name, skip in zip(names, unassigned, strict=True) if not skip]
    arg = {"texts": texts, "names": names, "entries": [e.to_json() for e in PROBE_ENTRIES]}
    got = loaded.run(UNDER_EACH, arg)
    assert got == _under_each_in_python(texts, names, PROBE_ENTRIES)


_paths = st.deferred(
    lambda: st.sampled_from(
        [("v",), ("format",), ("ids",), ("reasons",), ("k",), ("amb",), ("words",)]
        + [("words", part) for part in owned.WORDS]
        + [("amb", next(iter(real()[0]["amb"]))), ("k", next(iter(real()[0]["k"])))]
    )
)


@SETTINGS
@given(
    st.lists(
        st.tuples(
            _paths, st.sampled_from(_ODD), st.sampled_from(["set", "drop", "append", "first"])
        ),
        min_size=1,
        max_size=20,
    )
)
def test_random_match_files_read_as_in_python(loaded, mutations):
    """The real match file, one value changed: refused alike, or read alike."""
    _compare_under_each(loaded, [_mutated(m) for m in mutations], list(PROBES))


_CODE_POINT_PARTS = ("whitespace", "dashes", "joiners", "separators")
_HEX_POOL = ["0020", "002D", "005F", "0041", "0061", "007A", "002E", "002F", "005C", "005D",
             "005E", "0009", "007F", "00A0", "00C9", "00E9", "00DF", "00FF", "0178", "0391",
             "03B1", "03A3", "03C2", "03C3", "0130", "0131", "017F", "212A", "2010-2015",
             "200B-200D", "FEFF", "3000", "1F600", "10020"]  # fmt: skip
_WORD_POOL = ["Bold", "bold", "Nerd Font", "nerd  font", "NF", "nf", "Mono", "Code", "Sans",
              "Light", "Semi", "ExtraBold", "It", "Var", "VF", "Variable", "variable", "CN",
              "PL", "NL", "a", "A", "x y", "Ital", "BoldItalic", "Book", "Condensed", "Propo",
              "Term", "0041", "-", "_"]  # fmt: skip
_change = st.sampled_from(list(owned.WORDS)).flatmap(
    lambda part: st.tuples(
        st.just(part),
        st.sampled_from(_HEX_POOL if part in _CODE_POINT_PARTS else _WORD_POOL),
        st.sampled_from(["add", "first", "drop", "only"]),
    )
)


def _words_file(changes: list[tuple[str, str, str]]) -> str:
    """A small match file whose words are owned.WORDS with each change made: a value added,
    put first, the part's first entry dropped, or the part made of the value only."""
    words = json.loads(json.dumps(owned.WORDS))
    for part, value, how in changes:
        if how == "add":
            words[part].append(value)
        elif how == "first":
            words[part].insert(0, value)
        elif how == "drop":
            words[part] = words[part][1:]
        else:
            words[part] = [value]
    doc = {"format": owned.FORMAT, "v": owned.VERSION, "run_date": "2026-01-01", "ids": [],
           "k": {}, "amb": {}, "reasons": [], "words": words}  # fmt: skip
    return json.dumps(doc)


@SETTINGS
@given(st.lists(st.lists(_change, min_size=1, max_size=6), min_size=1, max_size=8),
       st.lists(_names, max_size=20))  # fmt: skip
# A Nerd phrase and a joined variable word that end in upper case: the port's test on a text's
# last character must read the phrase in any case and the joined word in its own.
@example(
    files=[[("nerd", "NF", "only")], [("variable_joined", "VF", "only")]],
    names=["Foo nf", "Foo NF", "FooVF", "Foovf"],
)
def test_other_words_give_python_s_candidates(loaded, files, names):
    """Words a later build might write, or a damaged file holds, as both sides read them."""
    _compare_under_each(loaded, [_words_file(changes) for changes in files], [*PROBES, *names])


# ---------------------------------------------------------------- Data.loadMatch

MATCH_PATH = "/assets/match.0123456789.json"
INDEX = {"v": 4, "n": 0, "run_date": "2026-01-01", "views": [], "cats": [], "bands": [],
         "why_labels": [], "score_words": {}, "ids": [], "cat": [], "bits": [], "keys": [],
         "by_name": [], "r": {}}  # fmt: skip
DOC = {"format": "tff-match", "v": 1, "run_date": "2026-01-01", "ids": [], "k": {}, "amb": {},
       "reasons": [], "words": {}}  # fmt: skip
DATA_PAGE = (
    '<!doctype html><html lang=en><meta charset=utf-8><title>data</title><ol id="list"'
    ' data-index="{index}" data-details="/d.json" {match} {matcher}></ol><script type=module>\n'
    "{part}\nglobalThis.__tffData = Data;\n</script>"
)
OUTCOME = """async (load) => {
  const outcome = async () => {
    try {
      const doc = await globalThis.__tffData[load]();
      return ['ok', Object.isFrozen(doc) && (doc.k === undefined || Object.isFrozen(doc.k))];
    } catch (e) {
      return [e.name, e.code || null];
    }
  };
  return [await outcome(), await outcome()];
}"""
# (status, body) or (status, body, content type), or None for a network error
Answer = tuple[int, Any] | tuple[int, Any, str] | None
HOLD = "hold"  # an answer that never comes, until the test gives it (data_page's held)
MATCHER_ATTR = f'data-matcher="{MATCHER_PATH}"'


@dataclass
class DataPages:
    """``open(match_attr, match, ...)`` loads 10-data.js under a routed origin that answers
    the list index, the match file and the matcher module as told, and returns the page;
    ``requests`` holds every request made, and ``held`` the routes answered with HOLD."""

    context: Any
    requests: list[Any] = field(default_factory=list)
    held: list[Any] = field(default_factory=list)

    def open(
        self,
        match_attr: str,
        match: Answer,
        index: Answer = (200, INDEX),
        index_url: str = "/i.json",
        matcher_attr: str = MATCHER_ATTR,
        matcher: Answer | str = (200, MATCHER),
        clock: bool = False,
    ) -> Any:
        html = DATA_PAGE.format(
            part=DATA_PART.read_text(encoding="utf-8"),
            match=match_attr,
            matcher=matcher_attr,
            index=index_url,
        )

        def answer(route: Any, what: Answer | str, content_type: str) -> None:
            if what == HOLD:
                self.held.append(route)
                return
            if what is None:
                route.abort()
                return
            status, body, *served_as = what
            text = body if isinstance(body, str) else json.dumps(body)
            route.fulfill(status=status, content_type=(*served_as, content_type)[0], body=text)

        def handle(route: Any) -> None:
            path = route.request.url[len(ORIGIN) :]
            if path == "/":
                route.fulfill(status=200, content_type="text/html", body=html)
            elif path == "/i.json":
                answer(route, index, "application/json")
            elif path == MATCHER_PATH:
                answer(route, matcher, "text/javascript")
            else:
                answer(route, match, "application/json")

        self.context.route(f"{ORIGIN}/**", handle)
        page = self.context.new_page()
        if clock:
            page.clock.install()
        page.goto(f"{ORIGIN}/")
        return page


@pytest.fixture
def data_pages(browser: Any) -> Iterator[DataPages]:
    context = browser.new_context()
    pages = DataPages(context)
    context.on("request", lambda request: pages.requests.append(request))
    yield pages
    for route in pages.held:
        with contextlib.suppress(Exception):
            route.abort()
    context.close()


@pytest.fixture
def data_page(data_pages: DataPages) -> tuple[Any, list[Any]]:
    """The factory as the loadMatch tests use it: ``(open_page, requests)``."""
    return data_pages.open, data_pages.requests


def _sent(requests: list[Any], path: str) -> list[Any]:
    return [r for r in requests if r.url == ORIGIN + path]


DATA_CASES: dict[str, tuple[Answer, Answer, list[Any], int, int]] = {
    # label: (match file, list index, both calls' outcome, match requests, index requests)
    "ok": ((200, DOC), (200, INDEX), ["ok", True], 1, 1),
    "gone": ((404, ""), (200, INDEX), ["Stale", None], 1, 1),
    "gone for good": ((410, ""), (200, INDEX), ["Stale", None], 1, 1),
    "a server error": ((500, ""), (200, INDEX), ["MatchError", "status"], 2, 1),
    "a network error": (None, (200, INDEX), ["MatchError", "network"], 2, 1),
    "not JSON": ((200, '{"format": "tff-'), (200, INDEX), ["MatchError", "json"], 2, 1),
    "another format": ((200, {**DOC, "format": "other"}), (200, INDEX), ["MatchError", "format"], 2, 1),
    "another version": ((200, {**DOC, "v": 2}), (200, INDEX), ["MatchError", "format"], 2, 1),
    "another run": ((200, {**DOC, "run_date": "2026-02-01"}), (200, INDEX), ["MatchError", "run_date"], 2, 1),
    "the index answers 500": ((200, DOC), (500, ""), ["MatchError", "index"], 0, 2),
    "the index is not format 4": ((200, DOC), (200, {**INDEX, "v": 3}), ["MatchError", "index"], 0, 2),
    "the index can't be reached": ((200, DOC), None, ["MatchError", "index"], 0, 2),
    "the index is gone": ((200, DOC), (404, ""), ["Stale", None], 0, 1),
}  # fmt: skip


@pytest.mark.parametrize("label", DATA_CASES)
def test_load_match_fetches_the_list_s_match_file_once_and_checks_it(data_page, label):
    """A good file loads once (memoised); a stale file or index stays stale; any other failure,
    the list index's included, is a MatchError and is forgotten, so a second call asks again.
    The match file is asked for only after the index loads, and only by a plain GET."""
    open_page, requests = data_page
    match, index, want, asked, indexed = DATA_CASES[label]
    page = open_page(f'data-match="{MATCH_PATH}"', match, index)
    assert page.evaluate(OUTCOME, "loadMatch") == [want, want]
    sent = _sent(requests, MATCH_PATH)
    assert (len(sent), len(_sent(requests, "/i.json"))) == (asked, indexed)
    assert {(r.method, r.post_data) for r in sent} <= {("GET", None)}


DEEP = 200_000
# In the page: the match file's "x", walked down to its innermost list, each level frozen.
DEEPEST = """async () => {
  const doc = await globalThis.__tffData.loadMatch();
  let value = doc.x;
  let depth = 0;
  while (Array.isArray(value) && Object.isFrozen(value) && value.length) {
    value = value[0];
    depth += 1;
  }
  return [depth, Array.isArray(value) && Object.isFrozen(value)];
}"""


def test_load_match_freezes_a_file_nested_deeper_than_the_call_stack(data_page):
    """Only the header is checked, so a body of 200,000 nested lists loads, frozen all the way
    down. A recursive freeze rejected with a RangeError (Chromium) or an InternalError
    (Firefox), which is neither of the two failures section 5 promises."""
    open_page, _requests = data_page
    body = json.dumps(DOC)[:-1] + ', "x": ' + "[" * DEEP + "]" * DEEP + "}"
    page = open_page(f'data-match="{MATCH_PATH}"', (200, body))
    assert page.evaluate(DEEPEST) == [DEEP - 1, True]


@pytest.mark.parametrize(
    "attr",
    ["", 'data-match="/assets/match.json?x=1"', 'data-match="/assets/match.json#x"',
     'data-match="https://example.com/m.json"', 'data-match="//example.com/m.json"',
     'data-match="/\\example.com/m.json"', 'data-match="/\\\\example.com/m.json"',
     'data-match="assets/match.json"'],
)  # fmt: skip
def test_load_match_refuses_a_missing_or_foreign_url_without_a_request(data_page, attr):
    """A path like "/\\host" starts with one "/", but a browser reads it as "//host"."""
    open_page, requests = data_page
    page = open_page(attr, (200, DOC))
    assert page.evaluate(OUTCOME, "loadMatch") == [["MatchError", "url"], ["MatchError", "url"]]
    assert [r.url for r in requests] == [f"{ORIGIN}/"]


def test_the_list_index_is_never_asked_for_off_this_origin(data_page):
    """The rule is urlOf's, shared by every loader."""
    open_page, requests = data_page
    page = open_page(f'data-match="{MATCH_PATH}"', (200, DOC), index_url="/\\example.com/i.json")
    assert page.evaluate(OUTCOME, "loadIndex") == [["Error", None], ["Error", None]]
    assert page.evaluate(OUTCOME, "loadMatch")[0] == ["MatchError", "index"]
    assert [r.url for r in requests] == [f"{ORIGIN}/"]


def test_load_match_is_never_called_by_loading_the_index(data_page):
    open_page, requests = data_page
    page = open_page(f'data-match="{MATCH_PATH}"', (200, DOC))
    page.evaluate("() => globalThis.__tffData.loadIndex().then(() => true)")
    assert _sent(requests, MATCH_PATH) == []


# ---------------------------------------------------------------- Data.loadMatcher

# A module that runs but hands over nothing usable, by kind.
NO_HAND_OFF = {
    "no global": "export {};\n",
    "a writable global": "globalThis.tffMatcher = Object.freeze({ OwnedParse: { parse() {} },"
    " OwnedMatch: { table() {}, match() {} } });\n",
    "an unfrozen object": "Object.defineProperty(globalThis, 'tffMatcher', { value: {"
    " OwnedParse: { parse() {} }, OwnedMatch: { table() {}, match() {} } } });\n",
    "no matcher in it": "Object.defineProperty(globalThis, 'tffMatcher',"
    " { value: Object.freeze({ OwnedParse: { parse() {} } }) });\n",
    "a module that throws first": "throw new Error('x');\n" + MATCHER,
}
MATCHER_CASES: dict[str, tuple[Answer | str, list[Any], tuple[int, int]]] = {
    # label: (the module's answer, both calls' outcome, (fewest, most) requests for it)
    "ok": ((200, MATCHER), ["ok", True], (1, 1)),
    "gone": ((404, ""), ["MatchError", "load"], (1, 2)),
    "gone for good": ((410, ""), ["MatchError", "load"], (1, 2)),
    "a server error": ((500, ""), ["MatchError", "load"], (1, 2)),
    "a network error": (None, ["MatchError", "load"], (1, 2)),
    "served as JSON": ((200, MATCHER, "application/json"), ["MatchError", "load"], (1, 2)),
    "not JavaScript": ((200, "<!doctype html><title>x</title>"), ["MatchError", "handoff"], (1, 1)),
    **{label: ((200, js), ["MatchError", "handoff"], (1, 1)) for label, js in NO_HAND_OFF.items()},
}  # fmt: skip


@pytest.mark.parametrize("label", MATCHER_CASES)
def test_load_matcher_loads_the_list_s_matcher_module_once_and_takes_its_hand_off(
    data_pages, label
):
    """inventory_matcher_on_demand: one <script type="module"> for #list[data-matcher], a
    plain GET; its hand-off, frozen, memoised. A module that fails to load is a MatchError
    'load' (a script's error event doesn't tell a 404 from a network error), and one that runs
    but hands over nothing usable is 'handoff'. A failure is forgotten, but a browser may keep
    a module's failed fetch for the page, so the second try may ask again or not."""
    answer, want, (fewest, most) = MATCHER_CASES[label]
    page = data_pages.open(f'data-match="{MATCH_PATH}"', (200, DOC), matcher=answer)
    assert page.evaluate(OUTCOME, "loadMatcher") == [want, want]
    sent = _sent(data_pages.requests, MATCHER_PATH)
    assert fewest <= len(sent) <= most
    assert {(r.method, r.post_data, r.resource_type) for r in sent} == {("GET", None, "script")}
    assert _sent(data_pages.requests, MATCH_PATH) == _sent(data_pages.requests, "/i.json") == []
    added = page.evaluate(
        "() => [...document.head.querySelectorAll('script')].map((s) => [s.type, s.src, s.text])"
    )
    assert added == [["module", f"{ORIGIN}{MATCHER_PATH}", ""]] * len(added)
    assert 1 <= len(added) <= 2
    assert page.evaluate("() => document.scripts.length") == len(added) + 1


@pytest.mark.parametrize(
    "attr",
    ["", f'data-matcher="{MATCHER_PATH}?x=1"', f'data-matcher="{MATCHER_PATH}#x"',
     'data-matcher="https://example.com/m.js"', 'data-matcher="//example.com/m.js"',
     'data-matcher="/\\example.com/m.js"', 'data-matcher="assets/matcher.js"'],
)  # fmt: skip
def test_load_matcher_refuses_a_missing_or_foreign_url_without_a_request(data_pages, attr):
    page = data_pages.open(f'data-match="{MATCH_PATH}"', (200, DOC), matcher_attr=attr)
    assert page.evaluate(OUTCOME, "loadMatcher") == [["MatchError", "url"]] * 2
    assert [r.url for r in data_pages.requests] == [f"{ORIGIN}/"]
    assert page.evaluate("() => document.scripts.length") == 1


def test_load_matcher_gives_up_after_its_timeout_and_takes_a_late_hand_off(data_pages):
    """A module that doesn't answer is a MatchError 'timeout' after MATCHER_TIMEOUT_MS (on
    Playwright's clock); once it has run after all, the next call takes its hand-off without
    another script or request."""
    page = data_pages.open(f'data-match="{MATCH_PATH}"', (200, DOC), matcher=HOLD, clock=True)
    page.evaluate(
        "() => { globalThis.__tffFirst = globalThis.__tffData.loadMatcher()"
        ".then(() => ['ok', null], (e) => [e.name, e.code]); }"
    )
    page.wait_for_function("() => document.scripts.length === 2")
    timeout = page.evaluate("() => globalThis.__tffData.MATCHER_TIMEOUT_MS")
    assert timeout == 30_000
    page.clock.run_for(timeout - 1)
    assert page.evaluate("() => Promise.race([globalThis.__tffFirst, 'waiting'])") == "waiting"
    page.clock.run_for(2)
    assert page.evaluate("() => globalThis.__tffFirst") == ["MatchError", "timeout"]
    (held,) = data_pages.held
    held.fulfill(status=200, content_type="text/javascript", body=MATCHER)
    data_pages.held.clear()
    page.wait_for_function("() => globalThis.tffMatcher !== undefined")
    assert page.evaluate(OUTCOME, "loadMatcher") == [["ok", True], ["ok", True]]
    assert len(_sent(data_pages.requests, MATCHER_PATH)) == 1
    assert page.evaluate("() => document.scripts.length") == 2


def test_the_list_s_two_loaders_make_two_requests_and_match_as_python_does(data_pages):
    """A list's arrival as the hopper will handle it: loadMatch and loadMatcher together ask
    for the match file and the matcher module once each (and the list index, which the page
    has loaded by then), and the parser and matcher handed over read a list against the
    file loadMatch checked as Python does. A second list asks for nothing."""
    doc = real()[0]
    index = {**INDEX, "run_date": doc["run_date"]}
    page = data_pages.open(f'data-match="{MATCH_PATH}"', (200, doc), index=(200, index))
    text = "Inter\nFira Code Bold\nHackGen\nSource Sans Pro\nZzz Unknown Grotesk\n"
    run = """async (text) => {
      const [api, doc] = await Promise.all([
        globalThis.__tffData.loadMatcher(), globalThis.__tffData.loadMatch()]);
      const { OwnedParse, OwnedMatch } = api;
      return JSON.stringify(OwnedMatch.match(OwnedParse.parse(text), OwnedMatch.table(doc)));
    }"""
    got = json.loads(page.evaluate(run, text))
    want = owned.match(owned.parse(text), real()[1]).to_json()
    assert got == want
    assert got["owned"]
    paths = [r.url[len(ORIGIN) :] for r in data_pages.requests]
    assert sorted(paths) == sorted(["/", "/i.json", MATCH_PATH, MATCHER_PATH])
    assert (
        json.loads(page.evaluate(run, "Inter Bold\n"))
        == owned.match(owned.parse("Inter Bold\n"), real()[1]).to_json()
    )
    assert len(data_pages.requests) == 4
    for request in data_pages.requests[1:]:
        assert (request.method, request.post_data) == ("GET", None)
        assert "?" not in request.url
