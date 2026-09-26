"""site/js/05-keys.js against the shared vector file tests/vectors/name-keys.json.

The part is loaded on its own into a blank page, as an inline ES module (the built ``app.js``
is one module too), so it must work without the other parts. Every vector must pass in each
engine given with ``--browser`` (CI runs Chromium and Firefox), both constants must equal the
file's ``spec``, and the keys must equal the Python reference, ``tff_catalog.keys``, for every
code point both sides know and for a few thousand names drawn by Hypothesis.

These tests need no built site: they use the ``browser`` fixture and a context of their own.
"""

import json
import re
import sys
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tff_catalog.keys import expand_codepoints, match_key, search_key

ROOT = Path(__file__).resolve().parents[2]
PART = ROOT / "site" / "js" / "05-keys.js"
VECTORS = ROOT / "tests" / "vectors" / "name-keys.json"
DATA = json.loads(VECTORS.read_text(encoding="utf-8"))
SPEC = DATA["spec"]
CASES = DATA["cases"]
CASE_IDS = [f"{i:02d}-{case['input']}" for i, case in enumerate(CASES)]

# The part, then one line that hands its const to the tests (a module's consts are not global).
PAGE = (
    "<!doctype html><html lang=en><meta charset=utf-8><title>keys</title>"
    "<script type=module>\n{part}\nglobalThis.__tffKeys = Keys;\n</script>"
)

# The part is pure string code: no network, page, storage, clock or randomness, outside its
# comments. That keeps it private, deterministic and usable by Milestone 3 off the page.
ENVIRONMENT = re.compile(
    r"\b(?:fetch|XMLHttpRequest|sendBeacon|WebSocket|EventSource|import|document|window"
    r"|navigator|location|globalThis|localStorage|sessionStorage|indexedDB|caches"
    r"|Date|random|setTimeout|setInterval|requestIdleCallback)\b"
)


@dataclass
class Loaded:
    """A blank page with the Keys part loaded, the errors it raised and requests it made."""

    page: Any
    errors: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)

    def run(self, body: str, arg: Any = None) -> Any:
        """Evaluate ``(K, arg) => body`` in the page, with ``K`` the part's ``Keys``."""
        return self.page.evaluate(f"(arg) => ((K, arg) => {body})(globalThis.__tffKeys, arg)", arg)


@pytest.fixture(scope="module")
def keys(browser: Any) -> Iterator[Loaded]:
    """One page per engine with only site/js/05-keys.js loaded."""
    source = PART.read_text(encoding="utf-8")
    assert "</script" not in source.lower()
    context = browser.new_context()
    page = context.new_page()
    loaded = Loaded(page)
    context.on("request", lambda request: loaded.requests.append(request.url))
    page.on("pageerror", lambda error: loaded.errors.append(str(error)))
    page.set_content(PAGE.format(part=source), wait_until="load")
    yield loaded
    context.close()


def test_part_loads_as_a_module_without_errors(keys):
    assert keys.errors == []
    assert keys.run("typeof K") == "object"


def test_part_is_pure_string_code(keys):
    lines = PART.read_text(encoding="utf-8").splitlines()
    code = "\n".join(line.split("//", 1)[0] for line in lines)
    assert ENVIRONMENT.findall(code) == []
    # Using the keys makes no request either (the blank page itself makes none).
    keys.run("[K.matchKey('Source Sans Pro'), K.searchKey('\\u0141\\u00f3d\\u017a Sans')]")
    assert keys.requests == []
    assert keys.errors == []


def test_part_exposes_exactly_the_contract_members_frozen(keys):
    assert keys.run("Object.keys(K)") == [
        "matchKey",
        "searchKey",
        "DROP_CODEPOINTS",
        "CASEFOLD_EXTRA",
    ]
    assert keys.run("typeof K.matchKey === 'function' && typeof K.searchKey === 'function'")
    frozen = "[K, K.DROP_CODEPOINTS, K.CASEFOLD_EXTRA].every((o) => Object.isFrozen(o))"
    assert keys.run(frozen) is True


def test_drop_codepoints_equal_the_file(keys):
    assert keys.run("K.DROP_CODEPOINTS") == SPEC["drop_codepoints"]


def test_casefold_extra_equals_the_file(keys):
    assert keys.run("K.CASEFOLD_EXTRA") == SPEC["casefold_extra"]
    # Same entries in the same order, so a diff against the file stays readable.
    assert keys.run("Object.keys(K.CASEFOLD_EXTRA)") == list(SPEC["casefold_extra"])


@pytest.fixture(scope="module")
def vector_results(keys: Loaded) -> list[dict[str, str]]:
    """The part's keys for every vector input, and for the expected keys themselves."""
    body = """arg.map((c) => ({
        match_key: K.matchKey(c.input),
        search_key: K.searchKey(c.input),
        match_of_match: K.matchKey(c.match_key),
        search_of_search: K.searchKey(c.search_key),
        search_of_match: K.searchKey(c.match_key),
    }))"""
    return keys.run(body, CASES)


@pytest.mark.parametrize("index", range(len(CASES)), ids=CASE_IDS)
def test_vector(vector_results, index):
    case, got = CASES[index], vector_results[index]
    assert got["match_key"] == case["match_key"]
    assert got["search_key"] == case["search_key"]


@pytest.mark.parametrize("index", range(len(CASES)), ids=CASE_IDS)
def test_vector_keys_are_fixed_points(vector_results, index):
    case, got = CASES[index], vector_results[index]
    assert got["match_of_match"] == case["match_key"]
    assert got["search_of_search"] == case["search_key"]
    assert got["search_of_match"] == case["search_key"]


def test_values_that_are_not_strings(keys):
    got = keys.run("[null, undefined, 42, ''].map((v) => [K.matchKey(v), K.searchKey(v)])")
    assert got == [["", ""], ["", ""], ["42", "42"], ["", ""]]


def _units(s: str) -> list[int]:
    """UTF-16 code units of ``s``; a lone surrogate is one unit, as in JavaScript."""
    data = s.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


# A lone surrogate can't cross to or from the page as a string, so these tests send and receive
# UTF-16 code units. Each returns [matchKey, searchKey, matchKey of it, searchKey of it].
KEYS_OF_UNITS = """arg.map((units) => {
    const s = String.fromCharCode(...units);
    const u = (t) => Array.from({ length: t.length }, (_, i) => t.charCodeAt(i));
    const m = K.matchKey(s);
    const f = K.searchKey(s);
    return [u(m), u(f), u(K.matchKey(m)), u(K.searchKey(f))];
})"""

# A high surrogate right before a low one: one character in JavaScript, two in Python.
_PAIRED = re.compile("[\ud800-\udbff][\udc00-\udfff]")


def _want_units(name: str) -> list[list[int]]:
    m, f = match_key(name), search_key(name)
    return [_units(m), _units(f), _units(m), _units(f)]


LONE_SURROGATE_NAMES = [
    "\ud800",
    "A\ud800B",
    "\udc00E\u0301",  # a letter after the surrogate still takes its mark
    "e\ud800\u0301",  # but not across the surrogate
    "x\udc00\u0301 Sans",
    "\ud800 \ud800",
    "Caf\udbffe\u0301 \u1e9e\ufb01",
]


def test_lone_surrogates_are_kept_as_in_python(keys):
    """A pasted query or a broken font name can hold half a surrogate pair.

    Python and Chromium keep it through normalization; Firefox's ``normalize()`` alone would
    turn it into U+FFFD, which the part works around.
    """
    got = keys.run(KEYS_OF_UNITS, [_units(name) for name in LONE_SURROGATE_NAMES])
    assert got == [_want_units(name) for name in LONE_SURROGATE_NAMES]
    assert keys.errors == []


def _describe(cp: int) -> str:
    return f"U+{cp:04X} {unicodedata.name(chr(cp), '?')}"


def test_every_code_point_matches_python(keys):
    """Each assigned code point on its own gives Python's keys (skips what one side lacks).

    The engine and Python may ship different Unicode versions, so a code point is compared
    only when both call it assigned. Surrogates are left out (see the test above); private
    use is kept, since icon fonts put it in names.
    """
    body = """(() => {
        const skip = /[\\p{Cn}\\p{Cs}]/u;
        const changed = [];
        const unassigned = [];
        for (let cp = 0; cp <= 0x10FFFF; cp++) {
          const ch = String.fromCodePoint(cp);
          if (skip.test(ch)) {
            if (/\\p{Cn}/u.test(ch)) unassigned.push(cp);
            continue;
          }
          const m = K.matchKey(ch);
          const s = K.searchKey(ch);
          if (m !== ch || s !== ch) changed.push([cp, m, s]);
        }
        return { changed, unassigned };
    })()"""
    got = keys.run(body)
    changed = {cp: (m, s) for cp, m, s in got["changed"]}
    engine_unassigned = set(got["unassigned"])
    compared = 0
    mismatches = []
    for cp in range(sys.maxunicode + 1):
        ch = chr(cp)
        if unicodedata.category(ch) in {"Cn", "Cs"} or cp in engine_unassigned:
            continue
        compared += 1
        want = (match_key(ch), search_key(ch))
        have = changed.get(cp, (ch, ch))
        if have != want:
            mismatches.append(f"{_describe(cp)}: JS {have!r}, Python {want!r}")
    # Unicode 16.0 assigns about 155,000 code points outside private use, which adds 137,468.
    assert compared > 280_000
    assert mismatches == [], f"{len(mismatches)} code points differ, first: {mismatches[:20]}"


def _pool() -> list[str]:
    """Characters the keys treat specially, plus plain ones, all assigned for many years."""
    ranges = [
        (0x0020, 0x007E),  # ASCII
        (0x00A0, 0x024F),  # Latin-1 and Latin Extended-A, -B
        (0x0300, 0x036F),  # combining diacritics
        (0x0370, 0x03FF),  # Greek
        (0x0400, 0x04FF),  # Cyrillic
        (0x0900, 0x097F),  # Devanagari (composition exclusions such as U+0958)
        (0x1100, 0x11FF),  # Hangul jamo, which NFC composes
        (0x1E00, 0x1FFF),  # Latin Extended Additional, Greek Extended
        (0x2000, 0x206F),  # general punctuation: spaces, dashes, joiners
        (0x2100, 0x218F),  # letterlike symbols and number forms
        (0x2460, 0x24FF),  # enclosed alphanumerics
        (0x3300, 0x33FF),  # CJK compatibility squares
        (0xAC00, 0xAC7F),  # Hangul syllables
        (0xFB00, 0xFB4F),  # ligatures and presentation forms
        (0xFE30, 0xFE6F),  # compatibility and small forms
        (0xFF00, 0xFFEF),  # fullwidth and halfwidth forms
        (0x1D400, 0x1D7FF),  # mathematical alphanumerics
    ]
    points = {cp for first, last in ranges for cp in range(first, last + 1)}
    points |= expand_codepoints(SPEC["drop_codepoints"])
    points |= {int(cp, 16) for cp in SPEC["casefold_extra"]}
    return sorted(chr(cp) for cp in points if unicodedata.category(chr(cp)) != "Cn")


# Arbitrary names, weighted towards the characters the keys treat specially, and towards a
# letter, a dropped space or joiner and a combining mark in a row (only the final NFC joins
# the letter and the mark).
_DROPPED = sorted(chr(cp) for cp in expand_codepoints(SPEC["drop_codepoints"]))
_across_a_drop = st.tuples(
    st.sampled_from("aeiouAEIOUnsyzCc\u03b1\u03c9\u0438"),  # and Greek alpha, omega, Cyrillic i
    st.sampled_from(_DROPPED),
    st.characters(categories=["Mn"]),
).map("".join)
_names = st.lists(
    st.one_of(
        st.sampled_from(_pool()),
        st.characters(categories=["Mn"]),
        st.characters(exclude_categories=["Cn", "Co", "Cs"]),
        _across_a_drop,
    ),
    max_size=12,
).map("".join)


@settings(max_examples=40, derandomize=True, deadline=None)
@given(st.lists(_names, min_size=1, max_size=150))
def test_random_names_match_python(keys, names):
    """Each name, and each key fed back in, gives Python's keys.

    A name with a code point the engine calls unassigned comes back as null and is skipped,
    for the same reason as in the code point test.
    """
    body = """arg.map((s) => {
        if (/\\p{Cn}/u.test(s)) return null;
        const m = K.matchKey(s);
        const f = K.searchKey(s);
        return [m, f, K.matchKey(m), K.searchKey(f), K.searchKey(m)];
    })"""
    got = keys.run(body, names)
    mismatches = []
    for name, have in zip(names, got, strict=True):
        if have is None:
            continue
        m, f = match_key(name), search_key(name)
        want = [m, f, m, f, f]
        if have != want:
            mismatches.append(f"{name!r}: JS {have!r}, Python {want!r}")
    assert mismatches == [], f"{len(mismatches)} names differ, first: {mismatches[:10]}"


_names_with_lone = (
    st.lists(
        st.one_of(st.sampled_from(_pool()), st.integers(0xD800, 0xDFFF).map(chr)),
        min_size=1,
        max_size=12,
    )
    .map("".join)
    .filter(lambda name: _PAIRED.search(name) is None)
)


@settings(max_examples=20, derandomize=True, deadline=None)
@given(st.lists(_names_with_lone, min_size=1, max_size=100))
def test_random_names_with_lone_surrogates_match_python(keys, names):
    """Names mixing lone surrogates with the characters the keys treat specially.

    A name whose Python key puts a high surrogate right before a low one (a dropped space or
    mark between them) is skipped: the two would read as one character in JavaScript.
    """
    names = [
        name
        for name in names
        if not any(_PAIRED.search(key) for key in (match_key(name), search_key(name)))
    ]
    got = keys.run(KEYS_OF_UNITS, [_units(name) for name in names])
    mismatches = [
        f"{name!r}: JS {have!r}, Python {want!r}"
        for name, have in zip(names, got, strict=True)
        if have != (want := _want_units(name))
    ]
    assert mismatches == [], f"{len(mismatches)} names differ, first: {mismatches[:10]}"
