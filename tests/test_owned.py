"""tff_catalog.owned, the hopper's reference (Milestone 3, slice 1), and the words it shares.

- The pipeline's words stay: ``names.py``'s public words and rules, which owned.py strips
  with, are pinned literally, and so is the behaviour of the pipeline functions built on
  them, so making them public changed nothing.
"""

import re

import pytest

from tff_catalog import keys, names
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
