"""tff_catalog.owned, the hopper's reference (Milestone 3, slice 1), and the words it shares.

- The pipeline's words stay: ``names.py``'s public words and rules, which owned.py strips
  with, are pinned literally, and so is the behaviour of the pipeline functions built on
  them, so making them public changed nothing.
- WORDS and candidates: pinned cases, and Hypothesis checks that the hopper's rules strip
  what the pipeline's rules strip (``names.strip_style``, ``name_tables.strip_styles``,
  ``NERD_SUFFIX``, ``CJK_BUILD_SUFFIX``, ``fot._VARIABLE``) on ASCII names built from their
  words, and that every chain shrinks one known unit at a time.
"""

import itertools
import json
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tff_catalog import keys, names, owned
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
        ("Inter-Tight-Bold", [("intertightbold", False, False), ("intertight", False, True)]),
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
