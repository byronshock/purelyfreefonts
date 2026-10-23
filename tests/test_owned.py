"""tff_catalog.owned, the hopper's reference (Milestone 3, slice 1), and the words it shares.

- The pipeline's words stay: ``names.py``'s public words and rules, which owned.py strips
  with, are pinned literally, and so is the behaviour of the pipeline functions built on
  them, so making them public changed nothing.
- WORDS and candidates: pinned cases, and Hypothesis checks that the hopper's rules strip
  what the pipeline's rules strip (``names.strip_style``, ``name_tables.strip_styles``,
  ``NERD_SUFFIX``, ``CJK_BUILD_SUFFIX``, ``fot._VARIABLE``) on ASCII names built from their
  words, and that every chain shrinks one known unit at a time.
- The match document and its collision check, on a synthetic names.json and on the
  committed ``build/names.json``, with the reviewed recodes (``hopper_hackgen_related``).
- The parser: every fixture in ``tests/fixtures/owned/`` against ``expected.json``, the
  fixtures and vectors free of real paths and hosts, Hypothesis inputs that never make it
  raise, JSON read alike whole or cut, and 10 MB of escaped quotes read in linear time.
- The vectors (``tests/vectors/owned-fonts.json``), which site/js's port must pass too:
  every parse, candidates and match case gives its result, and every malformed match file
  is refused.
- The known answers (``tests/vectors/owned-known-answers.json``) on ``build/names.json``,
  with the recodes; a strict xfail marks a known data problem.
- Speed and imports: 10,000 names parse and match well inside a second, and owned.py loads
  only the standard library and three light modules.

``uv run python -m tests.helpers.owned_vectors`` rewrites the vectors and ``expected.json``
after a deliberate change; review the diff.
"""

import functools
import hashlib
import itertools
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from tests.helpers import ROOT
from tests.helpers import owned_vectors as ov

from tff_catalog import jsonio, keys, names, owned
from tff_catalog.aliases.miners import builds, name_tables
from tff_catalog.collectors.ranking import fot
from tff_catalog.latin import CJK_BUILD_SUFFIX

# ---------------------------------------------------------------- the pipeline's words stay


def test_the_style_words_are_pinned():
    assert sorted(names.STYLE_WORDS) == [
        "black", "bold", "book", "demibold", "extrablack", "extrabold", "extralight",
        "hairline", "heavy", "italic", "light", "medium", "normal", "oblique", "regular",
        "roman", "semibold", "thin", "ultrablack", "ultrabold", "ultralight",
    ]  # fmt: skip
    assert sorted(names.MODIFIERS) == ["demi", "extra", "semi", "ultra"]
    assert sorted(names.PS_ABBREVIATIONS) == ["it"]
    assert sorted(names.STYLE_ABBREVIATIONS) == [
        "bd", "blk", "exbold", "exlight", "hv", "it", "lt", "md", "med", "rg", "sb",
        "semilight", "smbd", "th", "xbd", "xlt",
    ]  # fmt: skip
    assert sorted(names.VARIABLE_WORDS) == ["var", "variable", "vf"]


def test_the_old_private_names_are_the_public_objects():
    assert names._MODIFIERS is names.MODIFIERS
    assert names._PS_ABBREVIATIONS is names.PS_ABBREVIATIONS
    assert names._is_style is names.is_style_word
    assert name_tables.STYLE_ABBREVIATIONS is names.STYLE_ABBREVIATIONS


def test_the_pipeline_s_suffix_patterns_are_unchanged():
    assert names.NERD_SUFFIX.pattern == (
        r"[\s_-]*(?P<build>(?i:nerd[\s_-]*font(?:[\s_-]*(?:mono|propo))?)"
        r"|(?<=[\s_a-z0-9-])NF[MP]?)\Z"
    )
    assert CJK_BUILD_SUFFIX.pattern == r"[\s_-]+CN\Z"
    assert fot._VARIABLE.pattern == r"(?:[\s_-]+(?i:variable|var|vf)|(?<=[a-z])Variable)$"
    assert builds._SUFFIX.pattern == r"(?:\s+(?:Nerd\ Font|Variable|NFM|NFP|Var|NF|CN|PL|NL))+\Z"
    for pattern in (names.NERD_SUFFIX, CJK_BUILD_SUFFIX, fot._VARIABLE, builds._SUFFIX):
        assert pattern.flags == re.UNICODE


def test_the_variable_words_are_the_ones_fot_folds():
    [spelled] = re.findall(r"\(\?i:([a-z|]+)\)", fot._VARIABLE.pattern)
    assert set(spelled.split("|")) == names.VARIABLE_WORDS


def test_drop_codepoints_is_its_four_groups_in_order():
    groups = (
        keys.WHITESPACE_CODEPOINTS,
        keys.DASH_CODEPOINTS,
        keys.UNDERSCORE_CODEPOINTS,
        keys.JOINER_CODEPOINTS,
    )
    assert [entry for group in groups for entry in group] == list(keys.DROP_CODEPOINTS)
    assert list(keys.DROP_CODEPOINTS) == [
        "0009-000D", "0020", "0085", "00A0", "1680", "2000-200A", "2028", "2029", "202F",
        "205F", "3000", "002D", "00AD", "2010-2014", "2212", "FE63", "FF0D", "005F",
        "200B-200D", "2060", "FEFF",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("name", "stripped"),
    [
        ("Inter Bold Italic", "Inter"),
        ("Fira Sans Extra Light", "Fira Sans"),
        ("JetBrainsMono-BoldItalic", "JetBrainsMono"),
        ("SourceSansPro-BoldIt", "SourceSansPro"),
        ("Archivo Black", "Archivo"),
        ("Roboto Condensed Bold", "Roboto Condensed"),
        ("Bold", "Bold"),
        ("Extra Light", "Extra"),
        ("Martian Mono Cn Lt", "Martian Mono Cn Lt"),
        ("Inter-Display", "Inter-Display"),
        ("Foo Semi Bold", "Foo"),
        ("ArialMT", "ArialMT"),
        ("Inter  Bold", "Inter"),
        ("Selawik Semilight", "Selawik Semilight"),
        ("3270-Regular", "3270"),
        ("Foo Bold™", "Foo"),
        ("Foo Bold-Italic", "Foo"),
        ("Inter-Tight-Bold", "Inter-Tight"),
        ("Kinto Sans Med", "Kinto Sans Med"),
        ("Foo Extra", "Foo Extra"),
    ],
)
def test_strip_style_behaves_as_before(name, stripped):
    assert names.strip_style(name) == stripped


@pytest.mark.parametrize(
    ("name", "stripped"),
    [
        ("Martian Mono Cn Lt", "Martian Mono Cn"),
        ("Inter Bold Italic", "Inter"),
        ("Kinto Sans Med", "Kinto Sans"),
        ("Selawik Semilight", "Selawik"),
        ("Montserrat Alternates ExLight", "Montserrat Alternates"),
        ("Foo Bold Med", "Foo"),
        ("Lt", "Lt"),
        ("Foo Semi Lt", "Foo Semi"),
        ("Foo Lt Bold", "Foo"),
    ],
)
def test_name_tables_strip_styles_behaves_as_before(name, stripped):
    assert name_tables.strip_styles(name) == stripped


@pytest.mark.parametrize(
    ("name", "folded"),
    [
        ("Inter-Bold", "inter"),
        ("Inter Variable", "inter"),
        ("InterVariable", "inter"),
        ("Satoshi-VF", "satoshi"),
        ("Inter var", "inter"),
        ("Roboto Condensed Bold", "robotocondensed"),
        ("Variable", "variable"),
        ("Inter Variable Bold", "inter"),
        ("Fira Code VF Bold", "firacode"),
        ("Foo Var\u0131able", "foo"),
    ],
)
def test_fot_family_fold_behaves_as_before(name, folded):
    assert fot.family_fold(name) == folded


@pytest.mark.parametrize(
    ("name", "split"),
    [
        ("Maple Mono NF CN", ("Maple Mono", "nf")),
        ("Cascadia Code PL", ("Cascadia Code", "powerline")),
        ("Monaspace Var", ("Monaspace", "variable")),
        ("FiraCode Nerd Font", ("FiraCode", "nerd")),
        ("TeX Gyre Heros Cn", None),
        ("NF", None),
        ("Iosevka", None),
        ("JetBrains Mono NL", ("JetBrains Mono", "nl")),
        ("Hack NFM", ("Hack", "nfm")),
        ("Inter Variable", ("Inter", "variable")),
        ("Foo CN", ("Foo", "cjk")),
    ],
)
def test_builds_split_build_behaves_as_before(name, split):
    assert builds.split_build(name) == split


# ---------------------------------------------------------------- WORDS and candidates


def keys_of(name: str) -> list[tuple[str, bool, bool]]:
    return [(c.key, c.patched, c.postscript) for c in owned.candidates(name)]


def test_words_are_the_shared_constants_as_json():
    words = owned.WORDS
    assert json.loads(json.dumps(words)) == words
    assert words["whitespace"] == list(keys.WHITESPACE_CODEPOINTS)
    assert words["dashes"] == list(keys.DASH_CODEPOINTS)
    assert words["joiners"] == list(keys.JOINER_CODEPOINTS)
    assert words["style"] == sorted(names.STYLE_WORDS)
    assert words["modifiers"] == sorted(names.MODIFIERS)
    assert words["postscript"] == sorted(names.PS_ABBREVIATIONS)
    assert words["abbreviations"] == sorted(names.STYLE_ABBREVIATIONS)
    assert words["variable"] == sorted(names.VARIABLE_WORDS)


@pytest.mark.parametrize(
    ("name", "chain"),
    [
        ("- Bold", [("bold", False, False)]),
        ("\u2013 Bold", [("bold", False, False)]),
        ("_ Bold", [("bold", False, False)]),
        ("- Extra Bold", [("extrabold", False, False)]),
        ("Extra Bold", [("extrabold", False, False), ("extra", False, False)]),
        (
            "Maple Mono NF CN",
            [
                ("maplemononfcn", False, False),
                ("maplemononf", True, False),
                ("maplemono", True, False),
            ],
        ),
        ("Inter-Tight-Bold", [("intertightbold", False, False), ("intertight", False, False)]),
        (
            "Archivo-Black-Bold",
            [
                ("archivoblackbold", False, False),
                ("archivoblack", False, False),
                ("archivo", False, False),
            ],
        ),
        (
            "ArchivoBlackNF-Regular",
            [
                ("archivoblacknfregular", False, False),
                ("archivoblacknf", False, True),
                ("archivoblack", True, True),
            ],
        ),
        ("Archivo-Black", [("archivoblack", False, False), ("archivo", False, True)]),
        (
            "ArchivoBlack-Regular",
            [("archivoblackregular", False, False), ("archivoblack", False, True)],
        ),
        ("Hack\ufeffNF", [("hacknf", False, False), ("hack", True, False)]),
        (
            "Fira Code\u00a0Nerd Font",
            [("firacodenerdfont", False, False), ("firacode", True, False)],
        ),
        (
            "Roboto Condensed Bold",
            [("robotocondensedbold", False, False), ("robotocondensed", False, False)],
        ),
        ("Constructor Bold", [("constructorbold", False, False), ("constructor", False, False)]),
        ("Kinto Sans Med", [("kintosansmed", False, False), ("kintosans", False, False)]),
        ("Inter \uff22\uff4f\uff4c\uff44", [("interbold", False, False), ("inter", False, False)]),
        ("InterVariable", [("intervariable", False, False), ("inter", False, False)]),
        ("Cascadia Code PL", [("cascadiacodepl", False, False), ("cascadiacode", True, False)]),
        (
            "Archivo BlackItalic",
            [
                ("archivoblackitalic", False, False),
                ("archivoblack", False, False),
                ("archivo", False, False),
            ],
        ),
        (
            "Archivo Black-Regular",
            [
                ("archivoblackregular", False, False),
                ("archivoblack", False, False),
                ("archivo", False, False),
            ],
        ),
        (
            "Syne Italic-Bold",
            [
                ("syneitalicbold", False, False),
                ("syneitalic", False, False),
                ("syne", False, False),
            ],
        ),
        (
            "Foo ExtraLightItalic",
            [
                ("fooextralightitalic", False, False),
                ("fooextralight", False, False),
                ("foo", False, False),
            ],
        ),
        (
            "Foo Extra_Light-Italic",
            [
                ("fooextralightitalic", False, False),
                ("fooextralight", False, False),
                ("foo", False, False),
            ],
        ),
        ("Foo Semi-Bold", [("foosemibold", False, False), ("foo", False, False)]),
        ("Foo BoldExtra", [("fooboldextra", False, False)]),
        ("", []),
        ("\u200d", []),
    ],
)
def test_candidates_strip_one_unit_at_a_time(name, chain):
    assert keys_of(name) == chain


@pytest.mark.parametrize(
    "name",
    ["Foo Bold™", "Foo BoldÉ", "Foo Bold٣", "Foo Var\u0131able", "Foo VAR\u0130ABLE", "Hack\x1fNF",
     "TeX Gyre Heros Cn", "Noto Sans JP", "CONF", "Inter-Display", "Bold", "Foo Sansa"],
)  # fmt: skip
def test_names_the_ascii_rules_leave_whole(name):
    """Non-ASCII look-alikes and unknown words strip nothing: no Unicode case folding, and
    nothing matches by prefix. (The pipeline's strip_style does strip "Bold™".)"""
    assert len(owned.candidates(name)) == 1


def test_candidates_take_other_words_and_refuse_bad_ones():
    words = json.loads(json.dumps(owned.WORDS))
    words["style"] = [*words["style"], "fett"]
    assert [c.key for c in owned.candidates("Foo Fett", words)] == ["foofett", "foo"]
    assert [c.key for c in owned.candidates("Foo Fett")] == ["foofett"]
    words["nerd"] = []
    with pytest.raises(ValueError, match="nerd"):
        owned.candidates("Foo", words)


_NAME_PIECES = st.sampled_from(
    ["Inter", "Fira", "Code", "Sans", "Mono", "Condensed", "Tight", "-", "_", "\u2013", " ",
     "Bold", "Italic", "Extra", "Semi", "Light", "NF", "NFM", "Nerd Font", "nerd font mono",
     "CN", "PL", "NL", "Variable", "VF", "var", "Regular", "BoldItalic", "It", "Lt", "Med", "x",
     "9", "É", "™", "\u200d", "\u00a0", ""]
)  # fmt: skip
_NAMES = (
    st.lists(_NAME_PIECES, max_size=7).map("".join)
    | st.lists(_NAME_PIECES, max_size=7).map(" ".join)
    | st.text(max_size=30)
)


@settings(max_examples=2000, derandomize=True, deadline=None)
@given(_NAMES)
def test_every_candidate_key_is_a_proper_prefix_of_the_one_before(name):
    chain = owned.candidates(name)
    if not chain:
        assert names.match_key(name) == ""
        return
    assert chain[0].key == names.match_key(name)
    assert chain[0].text == owned._normalize(name, owned._DEFAULT_RULES)
    for before, after in itertools.pairwise(chain):
        assert after.key
        assert after.key != before.key
        assert before.key.startswith(after.key)
        assert before.patched <= after.patched
        assert before.postscript <= after.postscript


_WORD = st.sampled_from(
    ["Inter", "Fira", "Code", "Sans", "Mono", "Condensed", "Tight", "Pro", "One", "Display"]
)
_PART = st.sampled_from(sorted(names.STYLE_WORDS | names.MODIFIERS)).map(str.title)


@settings(max_examples=1500, derandomize=True, deadline=None)
@given(
    st.lists(_WORD, min_size=1, max_size=3),
    st.lists(_PART, min_size=1, max_size=4),
    st.sampled_from(["", "-", "_"]),
)
def test_a_token_of_style_parts_strips_as_the_spaced_words_do(base, parts, joiner):
    """One part at a time, longest first: "Archivo BlackItalic" and "Archivo Black-Italic"
    reach "Archivo Black" before "Archivo", as "Archivo Black Italic" does."""
    joined = owned.candidates(" ".join([*base, joiner.join(parts)]))
    spaced = owned.candidates(" ".join([*base, *parts]))
    assert [(c.key, c.patched, c.postscript) for c in joined] == [
        (c.key, c.patched, c.postscript) for c in spaced
    ]


_STYLE = st.sampled_from(
    [*sorted(names.STYLE_WORDS), "Semi", "Extra", "Ultra", "Demi", "BoldItalic", "SemiBold",
     "ExtraLightItalic", "It", "BoldIt", "Lt", "Med"]
)  # fmt: skip


def _cased(word: str) -> st.SearchStrategy[str]:
    return st.sampled_from([word, word.upper(), word.lower(), word.title()])


def _stripped(name: str, kinds: tuple[str, ...]) -> str:
    rules = owned._DEFAULT_RULES
    text = owned._normalize(name, rules)
    while (step := owned._strip(text, rules)) is not None and step[1] in kinds:
        text = step[0]
    return text


@settings(max_examples=1500, derandomize=True, deadline=None)
@given(
    st.lists(_WORD, min_size=1, max_size=3),
    st.lists(_STYLE.flatmap(_cased), max_size=4),
    st.booleans(),
)
def test_the_style_rule_strips_what_strip_style_strips(base, styles, ps):
    """On ASCII names built from the words; abbreviations are name_tables' (next test).
    "Semi" and "light" join to the abbreviation "Semilight", so a PostScript name of styles
    that join to one is left out."""
    styles = [s for s in styles if s.lower() not in names.STYLE_ABBREVIATIONS]
    if ps and "".join(styles).lower() in names.STYLE_ABBREVIATIONS:
        return
    name = ("".join(base) + "-" + "".join(styles)) if ps and styles else " ".join(base + styles)
    want = names.strip_style(name) if ps else name
    if not ps:
        while (shorter := names.strip_style(want)) != want:
            want = shorter
    assert _stripped(name, ("style", "postscript")) == want


@settings(max_examples=1500, derandomize=True, deadline=None)
@given(st.lists(_WORD, min_size=1, max_size=3), st.lists(_STYLE.flatmap(_cased), max_size=4))
def test_the_style_and_abbreviation_rule_strips_what_strip_styles_strips(base, styles):
    name = " ".join(base + styles)
    got = _stripped(name, ("style",))
    assert got in (
        name_tables.strip_styles(name),
        names.strip_style(name_tables.strip_styles(name)),
    )


_TAILS = st.sampled_from(
    ["Nerd Font", "NerdFont", "nerd font mono", "Nerd_Font_Propo", "NF", "NFM", "NFP", "nf", "CN",
     "PL", "NL", "Variable", "var", "VF", "Vf", "VARIABLE"]
)  # fmt: skip


@settings(max_examples=2000, derandomize=True, deadline=None)
@given(
    st.lists(_WORD, min_size=1, max_size=2),
    st.sampled_from([" ", "-", "_", "  ", " - ", ""]),
    _TAILS,
    st.booleans(),
)
def test_the_suffix_rules_strip_what_the_pipeline_strips(base, sep, tail, lower_end):
    """NERD_SUFFIX, CJK_BUILD_SUFFIX, fot._VARIABLE, and PL or NL after a separator."""
    name = " ".join(base) + ("x" if lower_end else "X") + sep + tail
    rules = owned._DEFAULT_RULES
    step = owned._strip(owned._normalize(name, rules), rules)
    got = step[0].rstrip(" -_") if step and step[1] in ("patched", "variable") else None
    if tail == "CN":
        want = CJK_BUILD_SUFFIX.sub("", name) if CJK_BUILD_SUFFIX.search(name) else None
    elif tail in ("PL", "NL"):
        m = re.search(r"[\s_-]+(PL|NL)\Z", name)
        want = name[: m.start()] if m else None
    elif tail.lower() in names.VARIABLE_WORDS:
        want = fot._VARIABLE.sub("", name) if fot._VARIABLE.search(name) else None
    else:
        m = names.NERD_SUFFIX.search(name)
        want = name[: m.start()] if m else None
    if want is not None:
        want = " ".join(want.split()).rstrip(" -_")
    assert got == want


# ---------------------------------------------------------------- the match document


NAMES_JSON = ROOT / "build" / "names.json"
CATALOG = ROOT / "build" / "catalog-site.json"
# The reviewed recodes the site build will read from a file beside names.json (PR 2);
# owner ruling hopper_hackgen_related, 2026-10-23.
RECODES = {"HackGen": "related"}


def _schema(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
    )


@functools.cache
def real() -> tuple[dict[str, Any], list[str]]:
    """The committed build/names.json and the catalog's ids."""
    doc = json.loads(NAMES_JSON.read_text(encoding="utf-8"))
    ids = [font["id"] for font in json.loads(CATALOG.read_text(encoding="utf-8"))["fonts"]]
    return doc, ids


def synthetic() -> dict[str, Any]:
    """The match document of the synthetic names.json, as JSON gives it back."""
    return json.loads(json.dumps(owned.match_document(ov.NAMES, ov.CATALOG_IDS, ov.RUN_DATE)))


def test_the_suffix_words_are_the_pipeline_s_build_tokens():
    """Every token the builds miner and name_tables read as a patched build strips here too,
    and the details split as names.schema.json's enum does."""
    stripped = {*owned.WORDS["nerd_tokens"], *owned.WORDS["cased"]}
    patched_tokens = {t for t, d in builds.DETAILS.items() if d in owned.PATCHED_DETAILS}
    assert patched_tokens - {"Nerd Font"} == stripped
    assert {"Var", "Variable"} <= {t for t, d in builds.DETAILS.items() if d == "variable"}
    cased = {t for t, d in name_tables.CASED_WORDS.items() if d in owned.PATCHED_DETAILS}
    assert cased == stripped
    assert {
        t.lower() for t, d in name_tables.CASED_WORDS.items() if d == "variable"
    } <= names.VARIABLE_WORDS
    schema = json.loads((ROOT / "schemas" / "names.schema.json").read_text(encoding="utf-8"))
    row = schema["properties"]["families"]["items"]["properties"]["names"]["items"]
    details = set(row["properties"]["detail"]["enum"])
    assert details == owned.SAME_FONT_DETAILS | owned.PATCHED_DETAILS
    assert not owned.SAME_FONT_DETAILS & owned.PATCHED_DETAILS
    relations = set(row["properties"]["relation"]["enum"])
    assert relations == {"build", *owned._LETTERS}
    assert relations >= owned.RECODE_RELATIONS


def test_the_synthetic_document_codes_every_relation():
    doc = synthetic()
    assert doc["ids"] == ["bar-serif", "foo-sans", "foo-sans-black", "foo-sans-tight"]
    assert doc["k"] == {
        "barmono": "0x", "barserif": "0", "barserifdisplay": "0s", "foofamily": "1u",
        "foohairline": "-", "foosans": "1", "foosansblack": "2", "foosanshairline": "-",
        "foosansheavy": "!1", "foosansjp": "!0", "foosansnf": "1n", "foosanspro": "1r",
        "foosanstext": "1b", "foosanstight": "3", "foosansvariable": "1b", "fsans": "1p",
    }  # fmt: skip
    assert doc["amb"] == {"foosansblack": [1], "foosanshairline": [1], "foosansheavy": [1]}
    assert doc["reasons"] == ["cjk", "proprietary"]
    assert doc["names_sha256"] == hashlib.sha256(jsonio.canonical_bytes(ov.NAMES)).hexdigest()


def test_the_document_does_not_depend_on_the_catalog_ids_order():
    shuffled = list(reversed(ov.CATALOG_IDS))
    assert (
        json.loads(json.dumps(owned.match_document(ov.NAMES, shuffled, ov.RUN_DATE))) == synthetic()
    )


@pytest.mark.parametrize(
    ("ids", "run_date", "message"),
    [
        (ov.CATALOG_IDS[1:], ov.RUN_DATE, "only in names.json"),
        ([*ov.CATALOG_IDS, "foo-sans-hairline"], ov.RUN_DATE, "only in the catalog"),
        ([*ov.CATALOG_IDS, "bar-serif"], ov.RUN_DATE, "repeat"),
        (ov.CATALOG_IDS, "2026-01-02", "of run 2026-01-01"),
    ],
)
def test_match_document_refuses_ids_or_a_run_that_differ(ids, run_date, message):
    with pytest.raises(ValueError, match=message):
        owned.match_document(ov.NAMES, ids, run_date)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"name": "Foo Sans Odd", "relation": "build", "detail": "odd"}, "unknown detail"),
        ({"name": "Foo Sans Odd", "relation": "cousin"}, "unknown relation"),
    ],
)
def test_match_document_refuses_unknown_relations_and_details(row, message):
    doc = json.loads(json.dumps(ov.NAMES))
    doc["families"][1]["names"].append(row)
    with pytest.raises(ValueError, match=message):
        owned.match_document(doc, ov.CATALOG_IDS, ov.RUN_DATE)


def test_a_recode_changes_only_its_alias_s_code():
    recodes = {"Foo Sans Text": "related"}
    doc = owned.match_document(ov.NAMES, ov.CATALOG_IDS, ov.RUN_DATE, recodes=recodes)
    assert doc["k"]["foosanstext"] == "1x"
    plain = synthetic()["k"]
    assert {key: codes for key, codes in doc["k"].items() if key != "foosanstext"} == {
        key: codes for key, codes in plain.items() if key != "foosanstext"
    }
    assert owned.check_document(ov.NAMES, ov.CATALOG_IDS, recodes=recodes).meetings == ()


@pytest.mark.parametrize(
    ("recodes", "message"),
    [
        ({"Foo Sans Txt": "related"}, "no alias of names.json"),
        ({"Foo Sans": "related"}, "no alias of names.json"),
        ({"Foo Sans Text": "rename"}, "is not one of"),
        ({"Foo Sans Text": "build"}, "is not one of"),
        ({"-": "related"}, "no alias of names.json"),
        ({"Foo Sans Text": "related", "FooSansText": "sibling"}, "two relations"),
    ],
)
def test_a_recode_must_name_an_alias_and_a_near_match_relation(recodes, message):
    with pytest.raises(ValueError, match=message):
        owned.match_document(ov.NAMES, ov.CATALOG_IDS, ov.RUN_DATE, recodes=recodes)
    with pytest.raises(ValueError, match=message):
        owned.check_document(ov.NAMES, ov.CATALOG_IDS, recodes=recodes)


def _with_rows(*rows: dict[str, str]) -> dict[str, Any]:
    """The synthetic names.json with more alias rows for Foo Sans."""
    doc = json.loads(json.dumps(ov.NAMES))
    doc["families"][1]["names"].extend(rows)
    return doc


def test_a_recode_works_by_key_whatever_the_spelling():
    """Another spelling of a recoded alias, at the same key, is recoded too: kept as a
    build, it would leave Foo Sans owned there (hopper_hackgen_related)."""
    doc = _with_rows({"name": "FooSans Text", "relation": "build", "detail": ""})
    built = owned.match_document(
        doc, ov.CATALOG_IDS, ov.RUN_DATE, recodes={"Foo Sans Text": "related"}
    )
    assert built["k"]["foosanstext"] == "1x"
    respelled = owned.match_document(
        ov.NAMES, ov.CATALOG_IDS, ov.RUN_DATE, recodes={"FOO SANS TEXT": "related"}
    )
    assert respelled == owned.match_document(
        ov.NAMES, ov.CATALOG_IDS, ov.RUN_DATE, recodes={"Foo Sans Text": "related"}
    )


@pytest.mark.parametrize(
    ("row", "recodes", "message"),
    [
        (
            {"name": "Foo Sans Text NF", "relation": "build", "detail": "nf"},
            {"Foo Sans Text": "related"},
            "'Foo Sans Text NF' reaches it and still counts as owned",
        ),
        (
            {"name": "Foo Sans Text Bold", "relation": "rename"},
            {"Foo Sans Text": "sibling"},
            "'Foo Sans Text Bold' reaches it",
        ),
        (
            {"name": "FooSans", "relation": "rename"},
            {"FooSans": "related"},
            "family name of foo-sans",
        ),
    ],
)
def test_a_recode_that_leaves_its_family_owned_is_refused(row, recodes, message):
    """A list's "Foo Sans Text NF" would hit its own build row and own Foo Sans again, so
    the reviewed list must recode it too; a family's own name can't be recoded."""
    doc = _with_rows(row)
    owned.match_document(doc, ov.CATALOG_IDS, ov.RUN_DATE)
    with pytest.raises(ValueError, match=message):
        owned.match_document(doc, ov.CATALOG_IDS, ov.RUN_DATE, recodes=recodes)
    with pytest.raises(ValueError, match=message):
        owned.check_document(doc, ov.CATALOG_IDS, recodes=recodes)
    if "reaches" in message:
        both = {**recodes, row["name"]: recodes["Foo Sans Text"]}
        built = owned.match_document(doc, ov.CATALOG_IDS, ov.RUN_DATE, recodes=both)
        assert not built["k"][names.match_key(row["name"])].endswith(("b", "n", "r"))


def test_the_check_finds_a_planted_meeting_of_distinct_families():
    assert owned.check_document(ov.NAMES, ov.CATALOG_IDS).meetings == ()
    planted = json.loads(json.dumps(ov.NAMES))
    planted["families"][4]["names"] = [{"name": "Foo Sans Bold", "relation": "related"}]
    meetings = owned.check_document(planted, ov.CATALOG_IDS).meetings
    assert [(m.family, m.other, m.name, m.key) for m in meetings] == [
        ("foo-sans-tight", "foo-sans", "Foo Sans Bold", "foosans")
    ]


def test_the_check_reports_shared_owned_keys():
    shared = json.loads(json.dumps(ov.NAMES))
    shared["families"][0]["names"].append({"name": "Foo Sans Pro", "relation": "rename"})
    assert owned.check_document(shared, ov.CATALOG_IDS).shared == {
        "foosanspro": ("bar-serif", "foo-sans")
    }


def test_the_real_names_json_has_five_ambiguous_keys_and_no_meeting():
    doc, ids = real()
    check = owned.check_document(doc, ids, recodes=RECODES)
    assert check.amb == {
        "archivoblack": ("archivo",),
        "bungeehairline": ("bungee",),
        "gentiumbook": ("gentium",),
        "gentiumplusbook": ("gentium-plus",),
        "syneitalic": ("syne",),
    }
    assert check.meetings == ()
    assert check.shared == {}
    assert len(check.collisions) == 15
    assert sum(bool(c.catalog) for c in check.collisions) == 5


def test_the_real_match_document_is_valid_and_its_codes_name_every_alias():
    doc, ids = real()
    built = owned.match_document(doc, ids, doc["run"]["date"], recodes=RECODES)
    assert list(_schema("match").iter_errors(json.loads(json.dumps(built)))) == []
    assert built["ids"] == sorted(ids)
    every = {f["family"] for f in doc["families"]} | {
        n["name"] for f in doc["families"] for n in f["names"]
    }
    every |= {row["name"] for row in doc["ineligible"]}
    assert {names.match_key(n) for n in every} - {""} == set(built["k"])
    assert built["k"]["hackgen"] == f"{built['ids'].index('hack')}x"
    unrecoded = owned.match_document(doc, ids, doc["run"]["date"])
    assert unrecoded["k"]["hackgen"] == f"{built['ids'].index('hack')}b"


# ---------------------------------------------------------------- parsing


EXPECTED = json.loads(ov.EXPECTED.read_text(encoding="utf-8"))
FIXTURE_FILES = sorted(p.name for p in ov.FIXTURES.iterdir() if p.name != ov.EXPECTED.name)


def test_every_fixture_has_an_expectation_and_the_fixtures_cover_every_format():
    assert sorted(EXPECTED) == FIXTURE_FILES
    formats = {result["format"] for result in EXPECTED.values()} - {None}
    assert formats == set(owned.FORMATS)
    errors = {result["error"] for result in EXPECTED.values()} - {None}
    assert errors == {"empty", "html", "command", "not_found", "font_file"}


@pytest.mark.parametrize("name", FIXTURE_FILES)
def test_a_fixture_parses_as_expected(name):
    assert owned.parse((ov.FIXTURES / name).read_bytes()).to_json() == EXPECTED[name]


# Real home folders and user names must never reach a public fixture (Milestone 3 step 2).
_HOME = re.compile(r"/home/|/Users/|/root/|[A-Za-z]:\\{1,2}Users\\", re.IGNORECASE)
# A user@host prompt; drawn listings glue pieces, so the user may follow other letters.
_AT_HOST = re.compile(r"([A-Za-z0-9_.-]+)@([A-Za-z0-9_.-]+)")


def _guarded_files() -> list[Path]:
    return [
        *sorted(ov.FIXTURES.iterdir()),
        *sorted((ROOT / "tests" / "vectors").glob("owned-*.json")),
        ROOT / "tests" / "helpers" / "owned_vectors.py",
    ]


@pytest.mark.parametrize("path", _guarded_files(), ids=lambda p: p.name)
def test_no_fixture_or_vector_holds_a_home_path_or_a_real_host(path):
    raw = path.read_bytes()
    texts = [raw.decode("utf-8", "replace")]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        texts.append(raw.decode("utf-16", "replace"))
    for text in texts:
        assert _HOME.findall(text) == []
        for user, host in _AT_HOST.findall(text):
            assert user.endswith("user"), user
            assert host == "host", host


def test_the_guard_catches_home_paths_in_each_spelling():
    for text in [
        "/home/ann/x",
        "/Users/Ann/Library",
        "C:\\Users\\ann",
        "c:\\\\users\\\\ann",
        "/root/x",
    ]:
        assert _HOME.findall(text), text
    assert _AT_HOST.findall("ann@laptop ~ $") == [("ann", "laptop")]


@settings(max_examples=1500, derandomize=True, deadline=None)
@given(st.one_of(st.binary(max_size=400), st.text(max_size=400)))
def test_parse_never_raises_and_its_results_hold_together(data):
    p = owned.parse(data)
    assert (p.error is None) == bool(p.entries)
    assert p.error is None or p.error in owned.ERRORS
    assert p.format is None or p.format in owned.FORMATS
    assert list(p.warnings) == [w for w in owned.WARNINGS if w in p.warnings]
    assert p.counts["entries"] == len(p.entries)
    text = json.dumps(p.to_json(), ensure_ascii=False)
    text.encode("utf-8")  # no lone surrogate is left
    for entry in p.entries:
        for name in (*entry.families, entry.full, entry.postscript):
            if name is not None:
                assert name
                assert len(name) <= owned.MAX_NAME
                assert name == " ".join(name.split(" "))
                assert name.strip(" ") == name


_JSON_KEYS = st.sampled_from(
    ["family", "fullname", "_name", "path", "typefaces", "0", "1", "x", "SPFontsDataType"]
)
_JSON_LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(10**30), 10**30),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=8),
    st.sampled_from(["Archivo", "Inter Bold", "\U0001d504", "a\\b", 'q"t']),
)
_JSON_VALUES = st.recursive(
    _JSON_LEAVES,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(_JSON_KEYS, inner, max_size=5)
    ),
    max_leaves=30,
)


def _walk_of_json_loads(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Every object's string members and every listed string of ``json.loads(text)``, in
    opening order: what the scanner must give for valid JSON whose keys are unique."""
    objects: list[dict[str, Any]] = []
    strings: list[str] = []
    stack: list[tuple[Any, bool]] = [(json.loads(text), True)]
    while stack:
        item, listed = stack.pop()
        if isinstance(item, dict):
            members = {k: v for k, v in item.items() if isinstance(v, str)}
            if members:
                objects.append(members)
            stack.extend((v, False) for v in reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend((v, True) for v in reversed(item))
        elif isinstance(item, str) and listed:
            strings.append(item)
    return objects, strings


@settings(max_examples=1500, derandomize=True, deadline=None)
@given(_JSON_VALUES, st.booleans())
def test_the_json_scanner_reads_valid_json_as_json_loads_does(value, indent):
    text = json.dumps(value, indent=2 if indent else None, ensure_ascii=not indent)
    objects, strings, cut = owned._json_values(text)
    assert (objects, strings) == _walk_of_json_loads(text)
    assert not cut


@pytest.mark.parametrize(
    ("text", "families"),
    [
        ('{"0": 1, "x": ["Archivo"], "x": ["Inter"]}', ["Archivo", "Inter"]),
        ('{"0": {"family": "Archivo"}, "0": {"family": "Inter"}}', ["Archivo", "Inter"]),
        (
            '{"x": {"family": "Archivo"}, "x": {"family": "Inter"}, "n": 1' + "0" * 5000 + "}",
            ["Archivo", "Inter"],
        ),
        ('{"x": ["Archivo"], "x": ["Inter"], "n": 1' + "0" * 5000 + "}", ["Archivo", "Inter"]),
        (
            "[\n" * 120_000
            + '{"x": {"family": "Archivo"}, "x": {"family": "Inter"}}'
            + "\n]" * 120_000,
            ["Archivo", "Inter"],
        ),
    ],
    ids=["lists", "integer keys", "a huge integer", "lists and a huge integer", "120,000 deep"],
)
def test_duplicate_keys_keep_every_object_and_list(text, families):
    """json.loads would keep only the last of duplicate keys, refuse a 5,000-digit integer
    and overflow on deep nesting; the scanner reads them all, as the port does."""
    p = owned.parse(text)
    assert [e.families[0] for e in p.entries] == families
    assert "cut_off" not in p.warnings


def test_ten_megabytes_of_escaped_quotes_parse_in_linear_time():
    """'["' and 5 million escaped quotes: a scan that tokenized all the text from every
    quote took 9 s for 80 KB; this one reads it once."""
    text = '["' + '\\"' * 4_999_990
    start = time.perf_counter()
    p = owned.parse(text)
    elapsed = time.perf_counter() - start
    assert p.error == "no_names"
    assert elapsed < 2.0, elapsed


# ---------------------------------------------------------------- the vectors


VECTORS = json.loads(ov.VECTORS.read_text(encoding="utf-8"))
E = owned.Entry


@functools.cache
def vector_table() -> owned.Table:
    return owned.Table.from_document(VECTORS["document"])


def test_the_vector_document_is_match_document_s_output_and_valid():
    built = owned.match_document(VECTORS["names"], VECTORS["catalog_ids"], VECTORS["run_date"])
    assert json.loads(json.dumps(built)) == VECTORS["document"]
    assert list(_schema("match").iter_errors(VECTORS["document"])) == []
    assert list(_schema("names").iter_errors(VECTORS["names"])) == []


def test_a_recoded_alias_matches_only_as_a_near_match():
    doc = owned.match_document(
        ov.NAMES, ov.CATALOG_IDS, ov.RUN_DATE, recodes={"Foo Sans Text": "related"}
    )
    result = owned.match([E(("Foo Sans Text",), trusted=True)], owned.Table.from_document(doc))
    assert result.owned == {}
    assert result.near == {"foo-sans": {"as": "Foo Sans Text", "why": "related"}}


def test_the_vector_file_s_spec_is_owned_py_s():
    assert (VECTORS["format"], VECTORS["version"]) == ("tff-owned-fonts", 1)
    assert VECTORS["spec"] == ov.spec()
    assert VECTORS["spec"]["words"] == owned.WORDS
    assert list(_schema("owned-fonts").iter_errors(VECTORS)) == []


PARSE = VECTORS["parse"]
CANDIDATES = VECTORS["candidates"]
MATCH = VECTORS["match"]
MALFORMED = VECTORS["malformed"]


@pytest.mark.parametrize("case", PARSE, ids=[c["label"] for c in PARSE])
def test_parse_vector(case):
    assert owned.parse(ov.data_of(case["input"])).to_json() == case["expect"]


def test_parse_vectors_reach_javascript_as_python_reads_them():
    """No text input holds a surrogate pair as two code points (JavaScript would join them),
    and every input the page builds from parts holds no surrogate."""
    for case in PARSE:
        data = ov.data_of(case["input"])
        if "parts" not in case["input"]:
            assert ov.spelled(data) == case["input"]


def test_every_fixture_is_a_parse_vector_too():
    """PR 3 checks the port against the vectors only, so each fixture is one of them."""
    by_label = {c["label"]: c for c in PARSE}
    for path in ov.fixture_files():
        case = by_label[f"fixture {path.name}"]
        assert ov.data_of(case["input"]) == path.read_bytes()
        assert case["expect"] == EXPECTED[path.name]


def test_the_registry_vectors_say_what_their_labels_say():
    """Value names without a suffix ("Courier 10,12,15") stay whole and untrusted in a list
    with at least as many registry lines as plain ones; with fewer, commas split as fc-list's."""
    by_label = {c["label"]: c["expect"] for c in PARSE}
    for label in (
        "registry value names without a suffix stay whole",
        "registry value names without a suffix stay whole at a tie too",
        "registry value names without a suffix stay whole, as UTF-16",
        "fixture windows-registry-utf16.txt",
    ):
        expect = by_label[label]
        assert expect["format"] == "windows-registry", label
        assert {"Courier 10,12,15", "Small Fonts"} <= {e["full"] for e in expect["entries"]}
        assert not any(e["trusted"] or e["families"] for e in expect["entries"]), label
    fewer = by_label["fewer registry lines than plain ones leave fc-list's commas"]
    assert fewer["format"] == "fc-list"
    assert [e["families"] for e in fewer["entries"] if e["trusted"]] == [
        ["Archivo", "Archivo Black"],
        ["Foo,Bar", "Baz"],
    ]


@pytest.mark.parametrize("case", CANDIDATES, ids=[repr(c["name"]) for c in CANDIDATES])
def test_candidates_vector(case):
    got = [
        {"key": c.key, "text": c.text, "patched": c.patched, "postscript": c.postscript}
        for c in vector_table().candidates(case["name"])
    ]
    assert got == case["expect"]
    assert tuple(owned.candidates(case["name"])) == vector_table().candidates(case["name"])


@pytest.mark.parametrize("case", MATCH, ids=[c["label"] for c in MATCH])
def test_match_vector(case):
    entries = [ov.entry_of(e) for e in case["entries"]]
    got = owned.match(entries, vector_table(), patched=case["patched"]).to_json()
    assert got == case["expect"]


@pytest.mark.parametrize("case", MALFORMED, ids=[c["label"] for c in MALFORMED])
def test_a_malformed_match_file_is_refused(case):
    with pytest.raises(ValueError):  # noqa: PT011 (every refusal is a ValueError)
        owned.Table.from_document(json.loads(json.dumps(case["document"])))


def test_the_reader_takes_json_numbers_as_json_parse_leaves_them():
    """1.0 is the version 1 and 0.0 the index 0, since JSON.parse can't tell them apart."""
    doc = json.loads(json.dumps(VECTORS["document"]))
    doc["v"] = 1.0
    doc["amb"] = {key: [float(i) for i in v] for key, v in doc["amb"].items()}
    table = owned.Table.from_document(doc)
    assert table.amb == vector_table().amb
    assert owned.Table.from_document(json.loads(json.dumps(VECTORS["document"]))).amb == table.amb


def test_the_named_match_cases_say_what_their_labels_say():
    """A few vectors read in words, so a regenerated file that changed them shows here."""
    by_label = {c["label"]: c["expect"] for c in MATCH}
    assert by_label["family"]["owned"] == {"foo-sans": {"as": "Foo Sans", "how": "family"}}
    assert by_label["listed patched build, patched off"]["near"] == {
        "foo-sans": {"as": "Foo Sans NF", "why": "patched"}
    }
    assert by_label["ambiguous, untrusted"]["near"] == {
        "foo-sans": {"as": "Foo Sans Black", "why": "ambiguous"},
        "foo-sans-black": {"as": "Foo Sans Black", "why": "ambiguous"},
    }
    assert by_label["ambiguous, PostScript"]["owned"] == {
        "foo-sans-black": {"as": "FooSansBlack-Regular", "how": "family"}
    }
    for label in (
        "ambiguous, PostScript of a weight",
        "ambiguous, PostScript with two hyphens",
        "ambiguous, PostScript without a hyphen",
        "ambiguous, untrusted, camel-case styles",
        "ambiguous, untrusted, hyphenated styles",
    ):
        assert by_label[label]["owned"] == {}, label
        assert set(by_label[label]["near"]) == {"foo-sans", "foo-sans-black"}, label
    assert by_label["ineligible stops the chain"]["counts"]["reasons"] == {"cjk": 1}
    assert by_label["Object.prototype keys"]["counts"]["unknown"] == 4
    assert by_label["prefixes never match"]["counts"]["unknown"] == 3


_ENTRY_NAMES = st.one_of(
    st.sampled_from(ov._SYN_NAMES).flatmap(
        lambda n: st.sampled_from(ov._SUFFIXES).map(lambda s: n + s)
    ),
    st.text(max_size=20),
)
_ENTRIES = st.builds(
    E,
    st.lists(_ENTRY_NAMES, max_size=3).map(tuple),
    st.none() | _ENTRY_NAMES,
    st.none() | _ENTRY_NAMES,
    st.booleans(),
)


@settings(max_examples=1000, derandomize=True, deadline=None)
@given(st.lists(_ENTRIES, max_size=8), st.booleans())
def test_match_never_raises_and_never_counts_a_family_both_ways(entries, patched):
    result = owned.match(entries, vector_table(), patched=patched)
    assert not set(result.owned) & set(result.near)
    assert set(result.owned) | set(result.near) <= set(vector_table().ids)
    counts = result.counts
    assert counts["entries"] == len(entries) == sum(counts[o] for o in owned.OUTCOMES)
    assert all(v["how"] in owned.HOW for v in result.owned.values())
    assert all(v["why"] in owned.WHY for v in result.near.values())
    if not patched:
        assert "patched" not in {v["how"] for v in result.owned.values()}


# ---------------------------------------------------------------- known answers


KNOWN = json.loads(ov.KNOWN.read_text(encoding="utf-8"))


@functools.cache
def real_table(recoded: bool = True) -> owned.Table:
    """The table of the real match document, read back from its JSON as the page reads it."""
    doc, ids = real()
    recodes = RECODES if recoded else None
    built = owned.match_document(doc, ids, doc["run"]["date"], recodes=recodes)
    return owned.Table.from_document(json.loads(jsonio.canonical_bytes(built)))


def _holds(case: dict[str, Any], result: dict[str, Any]) -> bool:
    outcome = next((o for o in owned.OUTCOMES if result["counts"][o]), None)
    return (
        all(result["owned"].get(fid, {}).get("how") == how for fid, how in case["owned"].items())
        and not set(case["not_owned"]) & set(result["owned"])
        and set(case["near"]) <= set(result["near"])
        and (case["outcome"] is None or case["outcome"] == outcome)
    )


def test_the_known_answers_are_ov_s_and_name_real_families():
    assert ov.known_answers() == KNOWN
    assert list(_schema("owned-known-answers").iter_errors(KNOWN)) == []
    labels = [c["label"] for c in KNOWN]
    assert len(set(labels)) == len(labels)
    doc, _ = real()
    ids = {f["id"] for f in doc["families"]}
    named = {fid for c in KNOWN for fid in (*c["owned"], *c["not_owned"], *c["near"])}
    assert named <= ids


def test_the_known_answers_carry_every_case_the_milestone_names():
    """Milestone 3 step 5's fixture, which Milestone 4's final check reuses, and the slice's
    checklist (Source Sans Pro, FiraCode NF, Roboto Slab, Adwaita Sans, HackGen)."""
    tried = {
        name
        for c in KNOWN
        for name in (*c["entry"]["families"], c["entry"]["full"], c["entry"]["postscript"])
        if name
    }
    assert {
        "Inter Variable", "Inter Display", "Fira Code Retina", "Source Sans 3 ExtraLight",
        "Playfair Display Bold", "JetBrains Mono NL", "Cascadia Code PL", "Cascadia Code NF",
        "Inter Tight", "Noto Sans JP", "Roboto Condensed", "Archivo", "Archivo Black",
        "Source Sans Pro", "FiraCode NF", "Roboto Slab", "Adwaita Sans", "HackGen",
    } <= tried  # fmt: skip
    static_archivo = [c for c in KNOWN if c["entry"]["families"][:1] == ["Archivo"]]
    assert all(
        c["owned"] == {"archivo": "family"} and "archivo-black" in c["not_owned"]
        for c in static_archivo
    )
    assert len(static_archivo) == 2


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(c, marks=pytest.mark.xfail(strict=True, reason=c["xfail"])) if "xfail" in c else c
        for c in KNOWN
    ],
    ids=[c["label"] for c in KNOWN],
)  # fmt: skip
def test_known_answer_holds_on_the_real_names_json(case):
    result = owned.match([ov.entry_of(case["entry"])], real_table(), patched=case["patched"])
    assert _holds(case, result.to_json())


def test_without_its_recode_hackgen_is_a_build_of_hack():
    """The alias row that folds HackGen into Hack for the ranking stays (leftover_cica_hackgen);
    only the hopper's recode makes it a near-match."""
    result = owned.match([E(("HackGen",), trusted=True)], real_table(recoded=False))
    assert result.owned == {"hack": {"as": "HackGen", "how": "build"}}


# ---------------------------------------------------------------- speed and imports


def test_ten_thousand_names_parse_and_match_well_inside_a_second():
    """Milestone 3 step 5 asks the browser for 200 ms; the reference takes about 90 ms here."""
    doc, _ = real()
    base = [f["family"] for f in doc["families"]] + [r["name"] for r in doc["ineligible"]]
    styles = [
        "",
        " Bold",
        " Italic",
        " Light Italic",
        " NF",
        " Nerd Font Mono",
        " Variable",
        " Condensed Bold",
        "-Regular",
    ]
    text = "\n".join(
        base[i % len(base)]
        + styles[(i // len(base)) % len(styles)]
        + ("" if i < 3 * len(base) else f" {i}")
        for i in range(10_000)
    )
    built = jsonio.canonical_bytes(owned.match_document(doc, real()[1], doc["run"]["date"]))
    start = time.perf_counter()
    table = owned.Table.from_document(json.loads(built))
    result = owned.match(owned.parse(text), table)
    elapsed = time.perf_counter() - start
    assert result.counts["entries"] > 9_000
    assert elapsed < 1.0, elapsed


def test_owned_imports_only_the_standard_library_and_three_light_modules():
    """tff_site imports owned.py for the site build, which loads none of the pipeline."""
    code = (
        "import sys, tff_catalog.owned\n"
        "mods = {m.split('.')[0] for m in sys.modules} - set(sys.stdlib_module_names)\n"
        "print(sorted(mods - {'__main__', '_virtualenv'}))\n"
        "print(sorted(m for m in sys.modules if m.startswith('tff_catalog')))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    third, ours = out.stdout.splitlines()
    assert third == "['tff_catalog']"
    assert ours == str(
        [
            "tff_catalog",
            "tff_catalog.jsonio",
            "tff_catalog.keys",
            "tff_catalog.names",
            "tff_catalog.owned",
        ]
    )
