"""Stage "latin" (milestone-1 step 5): rule A, the glyph test, the allowlist and the stage.

Every cmap here is synthetic (ruling T1: Google data is never a public
fixture); the spot checks give Inter, Iosevka, Pretendard and LXGW WenKai
cmaps shaped like the real fonts' (scout measurements: Inter misses only
U+030B from GF_Latin_Core and carries Greek and Cyrillic; Iosevka adds about
850 mathematical alphanumerics; Pretendard is Hangul, LXGW WenKai Han). The
thresholds are the shipped ``config/ranking.toml [latin]``, so a config change
that would drop Inter or Iosevka fails here.
"""

import hashlib
import logging
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.helpers import ROOT

from tff_catalog import fontfiles, latin, reviews, stageio
from tff_catalog.aliases import AliasRow, write_aliases
from tff_catalog.config_model import Latin, RankingConfig, from_mapping, load_toml
from tff_catalog.fetch import USER_AGENT, FetchError, HostNotAllowed
from tff_catalog.fontfiles import FileRead, FontFacts, FontFileCache, record_reads
from tff_catalog.paths import Paths
from tff_catalog.records import (
    FontFileRef,
    Observation,
    Record,
    SourceKey,
    UniverseRecord,
    write_jsonl,
)
from tff_catalog.stages import RunOptions, StageContext
from tff_catalog.state import State
from tff_catalog.store import Store
from tff_catalog.universe import Family, Universe

RANKING = from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"), where="ranking")
TH = RANKING.latin
GS = latin.default_glyphsets()
DAY = date(2026, 10, 1)
LOG = logging.getLogger("test.latin")


def span(first: int, last: int) -> set[int]:
    return set(range(first, last + 1))


# A complete Latin font: GF Kernel and Core, plus Latin Extended-A and -B.
LATIN = set(GS.kernel | GS.core) | span(0x0100, 0x024F)
GREEK = span(0x0370, 0x03FF)
CYRILLIC = span(0x0400, 0x052F)
MATH_LETTERS = span(0x1D400, 0x1D7CB)  # mathematical alphanumerics: script Common
ICONS = span(0xE000, 0xE9FF)  # Nerd-style private-use icons
HANGUL = span(0xAC00, 0xD7A3)  # the 11,172 syllables
HAN = span(0x4E00, 0x9FFF)
KANA = span(0x3041, 0x30FF)
ARABIC = span(0x0620, 0x064A) | span(0xFB50, 0xFDFF) | span(0xFE70, 0xFEFC)

INTER = (LATIN - {0x030B}) | GREEK | CYRILLIC
IOSEVKA = LATIN | GREEK | CYRILLIC | MATH_LETTERS | ICONS
PRETENDARD = LATIN | HANGUL
LXGW_WENKAI = LATIN | HAN | KANA


def ranges_text(cps: set[int] | frozenset[int]) -> str:
    """A CSS unicode-range list for ``cps``, as the Fontsource registry writes it."""
    parts, run = [], None
    for cp in sorted(cps):
        if run and cp == run[1] + 1:
            run[1] = cp
            continue
        if run:
            parts.append(run)
        run = [cp, cp]
    if run:
        parts.append(run)
    return ", ".join(f"U+{a:04X}" if a == b else f"U+{a:04X}-{b:04X}" for a, b in parts)


# --- glyphsets --------------------------------------------------------------------------------


def test_vendored_glyphsets_are_the_pinned_files() -> None:
    directory = ROOT / "data" / "glyphsets"
    for name, sha in latin.GLYPHSET_SHA256.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == sha
    notice = (directory / "NOTICE").read_text(encoding="utf-8")
    assert latin.GLYPHSET_COMMIT in notice
    assert all(sha in notice for sha in latin.GLYPHSET_SHA256.values())
    assert "Apache License" in (directory / "LICENSE").read_text(encoding="utf-8")


def test_glyphset_contents() -> None:
    assert len(GS.kernel) == 116
    assert len(GS.core) == 319
    assert GS.kernel <= GS.core
    assert span(0x20, 0x7E) <= GS.kernel
    assert {0x030B, 0x1E9E} <= GS.core


def test_load_glyphset_is_strict(tmp_path: Path) -> None:
    good = tmp_path / "good.nam"
    good.write_text("# header\n\n0x0041 # A\n0x1F600\n", encoding="utf-8")
    assert latin.load_glyphset(good) == {0x41, 0x1F600}
    bad = tmp_path / "bad.nam"
    bad.write_text("0x0041\nU+0042\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad.nam:2"):
        latin.load_glyphset(bad)
    empty = tmp_path / "empty.nam"
    empty.write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no code points"):
        latin.load_glyphset(empty)


@pytest.mark.network
def test_vendored_glyphsets_match_upstream() -> None:
    for name, sha in latin.GLYPHSET_SHA256.items():
        url = latin.GLYPHSET_URL.format(commit=latin.GLYPHSET_COMMIT, name=name)
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30.0)
        resp.raise_for_status()
        assert hashlib.sha256(resp.content).hexdigest() == sha
        assert resp.content == (ROOT / "data" / "glyphsets" / name).read_bytes()


# --- rule A -------------------------------------------------------------------------------------


def gf(
    name: str,
    script: str | None,
    subsets: tuple[str, ...],
    *,
    source: str = "google_metadata",
    languages: int | None = None,
) -> UniverseRecord:
    ns = "gf-family" if source == "google_metadata" else "gf-dir"
    return UniverseRecord(
        source=source,
        key=SourceKey(ns, name),
        family=name,
        primary_script=script,
        subsets=subsets,
        latin_languages=languages,
    )


@pytest.mark.parametrize(
    ("script", "subsets", "expected"),
    [
        ("", ("latin", "latin-ext"), True),
        (None, ("latin",), True),
        ("Latn", ("latin", "vietnamese"), True),
        (" Latn ", ("latin",), True),
        ("Deva", ("devanagari", "latin", "latin-ext"), False),
        ("", ("cyrillic", "greek"), False),
        ("Latn", (), False),
    ],
)
def test_rule_a(script: str | None, subsets: tuple[str, ...], expected: bool) -> None:
    assert latin.rule_a(gf("X", script, subsets)) is expected


def test_google_result_reasons_and_coverage() -> None:
    r = latin.google_result(gf("Inter", "", ("cyrillic", "latin", "latin-ext")))
    assert (r.latin, r.basis, r.coverage) == (True, "gf_metadata", "extended")
    assert latin.google_result(gf("Abel", "", ("latin",))).coverage == "basic"
    assert latin.google_result(gf("Poppins", "Deva", ("latin",))).reason == "dual_script"
    assert latin.google_result(gf("Noto Sans JP", "Jpan", ("latin",))).reason == "cjk"
    assert latin.google_result(gf("Noto Sans Arabic", "Arab", ("arabic",))).reason == (
        "no_latin_subset"
    )


# --- the glyph test -----------------------------------------------------------------------------


def test_spot_check_inter_is_in_with_extended_coverage() -> None:
    r = latin.glyph_test(INTER, TH)
    assert (r.latin, r.basis, r.coverage) == (True, "glyph_test", "extended")
    assert r.core_missing == (0x030B,)
    assert r.kernel_missing == ()
    assert r.latin_share is not None
    assert 0.5 < r.latin_share < 0.7


def test_spot_check_iosevka_is_in_despite_math_letters() -> None:
    r = latin.glyph_test(IOSEVKA, TH)
    assert (r.latin, r.coverage) == (True, "extended")
    # Mathematical alphanumerics are Common script: they do not dilute the share.
    assert r.latin_share == latin.glyph_test(IOSEVKA - MATH_LETTERS, TH).latin_share


@pytest.mark.parametrize("cmap", [PRETENDARD, LXGW_WENKAI], ids=["pretendard", "lxgw-wenkai"])
def test_spot_check_mainly_cjk_fonts_are_out(cmap: set[int]) -> None:
    r = latin.glyph_test(cmap, TH)
    assert (r.latin, r.basis, r.coverage, r.reason) == (False, None, None, "cjk")
    assert r.cjk_codepoints >= TH.cjk_codepoints_below
    assert r.kernel_missing == ()  # they do cover basic Latin


def test_cjk_threshold_boundary() -> None:
    # Enough Latin letters that 999 ideographs leave the Latin share above its minimum.
    big_latin = LATIN | span(0x0250, 0x02AF) | span(0x1D00, 0x1DBF) | span(0x1E00, 0x1EFF)
    big_latin |= span(0x2C60, 0x2C7F) | span(0xA720, 0xA7FF) | span(0xAB30, 0xAB64)
    han = sorted(HAN)
    below = latin.glyph_test(big_latin | set(han[: TH.cjk_codepoints_below - 1]), TH)
    at = latin.glyph_test(big_latin | set(han[: TH.cjk_codepoints_below]), TH)
    assert below.latin
    assert (at.latin, at.reason) == (False, "cjk")


def test_kernel_missing_is_counted_against_the_threshold() -> None:
    r = latin.glyph_test(LATIN - {0x2122}, TH)
    assert (r.latin, r.reason, r.kernel_missing) == (False, "kernel_missing", (0x2122,))
    looser = replace(TH, kernel_missing_max=1)
    assert latin.glyph_test(LATIN - {0x2122}, looser).latin


def test_low_latin_share_is_out() -> None:
    r = latin.glyph_test(set(GS.kernel) | ARABIC, TH)
    assert (r.latin, r.reason) == (False, "latin_share")
    assert r.latin_share is not None
    assert r.latin_share < TH.latin_share_min


@pytest.mark.parametrize(
    ("missing", "coverage"),
    [
        (set(), "extended"),
        ({0x030B, 0x0328}, "extended"),  # two combining marks
        ({0x030B, 0x0328, 0x0326}, "basic"),  # three
        ({0x1E9E}, "basic"),  # a letter (capital sharp s), not a mark
    ],
)
def test_core_tolerates_only_a_few_combining_marks(missing: set[int], coverage: str) -> None:
    assert latin.glyph_test(LATIN - missing, TH).coverage == coverage


def test_basic_latin_only_font_is_in_with_basic_coverage() -> None:
    r = latin.glyph_test(set(GS.kernel), TH)
    assert (r.latin, r.coverage) == (True, "basic")
    assert len(r.core_missing) == len(GS.core - GS.kernel)


def test_empty_cmap_fails_without_crashing() -> None:
    r = latin.glyph_test(set(), TH)
    assert (r.latin, r.reason, r.latin_share) == (False, "kernel_missing", 0.0)


def test_l1_rec_matches_the_documented_gate_values() -> None:
    assert Latin(0, 2, 0.40, 1000) == latin.L1_REC


# --- unicode ranges -----------------------------------------------------------------------------


def test_parse_unicode_range() -> None:
    assert latin.parse_unicode_range("U+0020-0022, U+00A0,u+4E0?") == frozenset(
        {0x20, 0x21, 0x22, 0xA0} | span(0x4E00, 0x4E0F)
    )
    for bad in (None, "", "U+0020-007E, U+2022...", "U+0020-007E, …", "U+007E-0020", "0020"):
        assert latin.parse_unicode_range(bad) is None


@given(st.sets(st.integers(min_value=0, max_value=0x10FFFF), min_size=1, max_size=60))
def test_unicode_range_round_trip(cps: set[int]) -> None:
    assert latin.parse_unicode_range(ranges_text(cps)) == cps


def test_trusted_range_checks_the_codepoint_count() -> None:
    text = ranges_text(INTER)
    assert latin.trusted_range(FontFileRef(url="u", unicode_range=text)) == INTER
    ok = FontFileRef(url="u", unicode_range=text, codepoints=len(INTER))
    assert latin.trusted_range(ok) == INTER
    assert latin.trusted_range(replace(ok, codepoints=len(INTER) + 1)) is None


# --- owner rulings ------------------------------------------------------------------------------


def write_ruling(paths: Paths, day: str, body: str) -> None:
    path = paths.reviews / "latin" / f"{day}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


RULING = """\
# Owner rulings, gate L (synthetic).
[L2-batch-1]
choice = "c"
recommended = false
ruling = "Batch 1 one by one."
reason = "Test."
include = ["poppins", "hind"]
exclude = ["mukta"]
"""


def test_load_allowlist_later_rulings_win(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    assert latin.load_allowlist(paths) == {}
    write_ruling(paths, "2026-10-01", RULING)
    later = '[L2-fix]\nruling = "Hind out."\nreason = "Test."\nexclude = ["hind"]\n'
    write_ruling(paths, "2026-11-01", later)
    assert latin.load_allowlist(paths) == {"poppins": True, "hind": False, "mukta": False}


def test_load_allowlist_reads_the_queue_questions_answers(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    write_ruling(paths, "2026-10-01", RULING)
    rows = (
        '[L-hind]\nchoice = "a"\nrecommended = false\nruling = "In."\nreason = "T."\n'
        '[L-single-day]\nchoice = "b"\nrecommended = false\nruling = "Out."\nreason = "T."\n'
        '[L-mukta]\nchoice = "c"\nrecommended = false\nruling = "Later."\nreason = "T."\n'
    )
    write_ruling(paths, "2026-11-01", rows)
    assert latin.load_allowlist(paths) == {
        "poppins": True,
        "hind": True,
        "mukta": False,  # research decides nothing: the earlier exclude holds
        "single-day": False,
    }


def test_the_stage_queues_gate_l_questions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _ = make_run(tmp_path, monkeypatch)
    latin.run(ctx)
    asked = {q.id: q for q in reviews.questions(ctx.paths, "L") if q.id.startswith("L-")}
    # Hind waits for a ruling (Poppins and Mukta have one); Single Day passes rule A with a
    # CJK subset. Every row takes the same three options and no recommendation.
    assert sorted(asked) == ["L-hind", "L-single-day"]
    assert all(
        q.options == latin.QUESTION_OPTIONS and q.recommended is None for q in asked.values()
    )
    assert "Devanagari" in asked["L-hind"].text or "Deva" in asked["L-hind"].text


@pytest.mark.parametrize(
    "body",
    [
        '[q]\nruling = "r"\nreason = "r"\ninclude = ["a"]\nexclude = ["a"]\n',
        '[q]\nruling = "r"\nreason = "r"\ninclude = ["Not An Id"]\n',
        '[q]\nruling = "r"\nreason = "r"\ninclude = "poppins"\n',
    ],
)
def test_load_allowlist_rejects_bad_files(tmp_path: Path, body: str) -> None:
    paths = Paths.for_root(tmp_path)
    write_ruling(paths, "2026-10-01", body)
    with pytest.raises(ValueError, match=r"\[q\]"):
        latin.load_allowlist(paths)


# --- deciding every family ----------------------------------------------------------------------


def fs(
    family: str,
    *files: FontFileRef,
    source: str = "fontsource",
    ns: str = "fs-id",
    key: str | None = None,
) -> UniverseRecord:
    return UniverseRecord(
        source=source,
        key=SourceKey(ns, key or family.lower().replace(" ", "-")),
        family=family,
        files=files,
    )


def ref(name: str, role: str = "regular", cmap: set[int] | None = None) -> FontFileRef:
    sha = hashlib.sha256(name.encode()).hexdigest()
    return FontFileRef(
        url=f"https://files.example/{name}.ttf",
        sha256=sha,
        role=role,  # type: ignore[arg-type]
        codepoints=None if cmap is None else len(cmap),
        unicode_range=None if cmap is None else ranges_text(cmap),
    )


CMAPS = {  # what a file read would return, by URL
    "https://files.example/Pretendard-Regular.ttf": PRETENDARD,
    "https://files.example/LXGWWenKai-Regular.ttf": LXGW_WENKAI,
    "https://files.example/MapleMono-NF-CN-Regular.ttf": LATIN | HAN,
    "https://files.example/MapleMono[wght].ttf": LATIN | GREEK,
}

RECORDS = [
    gf("Inter", "", ("cyrillic", "greek", "latin", "latin-ext"), languages=412),
    gf("Abel", "", ("latin",)),
    gf("Poppins", "Deva", ("devanagari", "latin", "latin-ext"), languages=236),
    gf("Hind", "Deva", ("devanagari", "latin", "latin-ext")),
    gf("Mukta", "Deva", ("devanagari", "latin")),
    gf("Noto Sans JP", "Jpan", ("japanese", "latin")),
    gf("Noto Sans Arabic", "Arab", ("arabic",)),
    gf("Material Icons", "", ("latin",)),
    # Rule A has no CJK test when the primary script is unset (Single Day, 2026-09-25).
    gf("Single Day", "", ("korean", "latin")),
    # The repo says Deva, but the live list (preferred) says unset.
    gf("Rubik", "", ("latin", "hebrew"), source="google_metadata"),
    gf("rubik", "Hebr", ("latin", "hebrew"), source="google_repo"),
    fs("Iosevka", ref("Iosevka-Regular", cmap=IOSEVKA)),
    fs("Pretendard", ref("Pretendard-Regular")),
    fs(
        "LXGW WenKai",
        replace(ref("LXGWWenKai-Regular"), unicode_range="U+0020-007E, U+4E00..."),
    ),
    # Maple Mono: the CN build has a "regular" file, the parent only a variable one.
    fs("Maple Mono", ref("MapleMono[wght]", role="variable")),
    fs(
        "Maple Mono NF CN",
        ref("MapleMono-NF-CN-Regular"),
        source="homebrew_casks",
        ns="brew-cask",
        key="font-maple-mono-nf-cn",
    ),
    fs("Nerd Only", source="nerdfonts", ns="nerd-folder", key="NerdOnly"),
]
BY_KEY = {r.key: [r] for r in RECORDS}
FAMILIES = {
    "inter": ["Inter"],
    "abel": ["Abel"],
    "poppins": ["Poppins"],
    "hind": ["Hind"],
    "mukta": ["Mukta"],
    "noto-sans-jp": ["Noto Sans JP"],
    "noto-sans-arabic": ["Noto Sans Arabic"],
    "material-icons": ["Material Icons"],
    "single-day": ["Single Day"],
    "rubik": ["Rubik", "rubik"],
    "iosevka": ["Iosevka"],
    "pretendard": ["Pretendard"],
    "lxgw-wenkai": ["LXGW WenKai"],
    "maple-mono": ["Maple Mono", "Maple Mono NF CN"],
    "nerd-only": ["Nerd Only"],
}
CN_KEY = SourceKey("brew-cask", "font-maple-mono-nf-cn")
CN_ALIAS = AliasRow(
    alias="font-maple-mono-nf-cn",
    ns="brew-cask",
    family_id="maple-mono",
    relation="build",
    detail="cjk",
    source="test",
    first_seen=DAY,
    reviewed_by="owner",
)
CN_NAME_ALIAS = replace(CN_ALIAS, alias="Maple Mono NF CN", ns="font-name")  # a name row


def universe(families: dict[str, list[str]] = FAMILIES) -> Universe:
    by_family = {r.family: r for r in RECORDS}
    fams = {}
    for fid, names in families.items():
        recs = [by_family[n] for n in names]
        fams[fid] = Family(
            id=fid,
            family=recs[0].family,
            keys=tuple(sorted(r.key for r in recs)),
            sources=tuple(sorted({r.source for r in recs})),
            first_seen=DAY,
            minted_from=recs[0].family,
            drop="icon" if fid == "material-icons" else None,
        )
    return Universe(families=fams, unmapped=())


def read_cmap(r: FontFileRef) -> frozenset[int] | None:
    cmap = CMAPS.get(r.url)
    return None if cmap is None else frozenset(cmap)


def test_decide_every_eligible_family() -> None:
    cjk_keys = latin.cjk_build_keys([CN_ALIAS])
    out = latin.decide(
        universe(),
        BY_KEY,
        TH,
        allowlist={"poppins": True, "mukta": False},
        cjk_keys=cjk_keys,
        cmap_for=read_cmap,
    )
    assert list(out) == sorted(FAMILIES.keys() - {"material-icons"})
    got = {fid: (r.latin, r.basis, r.coverage, r.reason) for fid, r in out.items()}
    assert got == {
        "abel": (True, "gf_metadata", "basic", None),
        "hind": (False, None, None, "dual_script"),
        "inter": (True, "gf_metadata", "extended", None),
        "iosevka": (True, "glyph_test", "extended", None),
        "lxgw-wenkai": (False, None, None, "cjk"),
        "maple-mono": (True, "glyph_test", "extended", None),
        "mukta": (False, None, None, "owner_excluded"),
        "nerd-only": (False, None, None, "no_file"),
        "noto-sans-arabic": (False, None, None, "no_latin_subset"),
        "noto-sans-jp": (False, None, None, "cjk"),
        "poppins": (True, "owner_allowlist", "extended", None),
        "pretendard": (False, None, None, "cjk"),
        "rubik": (True, "gf_metadata", "basic", None),
        "single-day": (True, "gf_metadata", "basic", None),
    }


def test_the_familys_own_file_comes_before_a_builds() -> None:
    # No alias row: the CN build's "regular" file would fail Maple Mono, but the family's
    # own (variable) file is tried first.
    out = latin.decide(universe(), BY_KEY, TH, cmap_for=read_cmap)
    assert (out["maple-mono"].latin, out["maple-mono"].basis) == (True, "glyph_test")
    by_family = {r.family: r for r in RECORDS}
    order = latin.candidate_files(
        [by_family["Maple Mono NF CN"], by_family["Maple Mono"]], family="Maple Mono"
    )
    assert [f.url for f in order] == [
        "https://files.example/MapleMono[wght].ttf",
        "https://files.example/MapleMono-NF-CN-Regular.ttf",
    ]


@pytest.mark.parametrize("row", [CN_ALIAS, CN_NAME_ALIAS], ids=["key-row", "name-row"])
def test_cjk_build_file_never_decides_the_parent(row: AliasRow) -> None:
    # A parent with no file of its own: without the alias row the CN build's file decides it.
    bare = replace(next(r for r in RECORDS if r.family == "Maple Mono"), files=())
    records = {**BY_KEY, bare.key: [bare]}
    out = latin.decide(universe(), records, TH, cmap_for=read_cmap)
    assert out["maple-mono"].reason == "cjk"
    keys = latin.cjk_build_keys([row])
    fixed = latin.decide(universe(), records, TH, cjk_keys=keys, cmap_for=read_cmap)
    assert fixed["maple-mono"].reason == "no_file"


def test_a_name_row_names_the_build_in_every_name_namespace() -> None:
    keys = latin.cjk_build_keys([CN_NAME_ALIAS])
    foundry = fs(
        "Maple Mono NF CN", source="foundries", ns="foundry-family", key="Maple Mono NF CN"
    )
    cask = fs("Maple Mono NF CN", source="homebrew_casks", ns="brew-cask", key="font-x")
    other = fs("Maple Mono", source="homebrew_casks", ns="brew-cask", key="font-maple-mono-nf-cn")
    assert latin.is_cjk_build(foundry, keys)
    assert latin.is_cjk_build(cask, keys)  # by its family name
    assert not latin.is_cjk_build(other, keys)  # a name row is not a cask-key row
    assert latin.is_cjk_build(other, latin.cjk_build_keys([CN_ALIAS]))


def one_family(rec: UniverseRecord) -> Universe:
    fid = rec.family.lower().replace(" ", "-")
    fam = Family(
        id=fid,
        family=rec.family,
        keys=(rec.key,),
        sources=(rec.source,),
        first_seen=DAY,
        minted_from=rec.family,
    )
    return Universe(families={fid: fam}, unmapped=())


def test_only_font_files_are_read_and_at_most_max_reads() -> None:
    calls: list[str] = []

    def failing(r: FontFileRef) -> frozenset[int] | None:
        calls.append(r.url)
        return None

    rec = fs(
        "Many Files",
        FontFileRef(url="https://github.com/o/r/releases/download/v1/ManyFiles.zip"),
        FontFileRef(url="http://files.example/Plain.ttf"),
        *(FontFileRef(url=f"https://files.example/F{i}.ttf") for i in range(5)),
    )
    out = latin.decide(one_family(rec), {rec.key: [rec]}, TH, cmap_for=failing)
    assert out["many-files"].reason == "no_file"
    assert calls == [f"https://files.example/F{i}.ttf" for i in range(latin.MAX_READS)]


def test_a_complete_code_point_list_needs_no_read_even_on_a_page_url() -> None:
    listed = replace(ref("Listed", cmap=INTER), url="https://example.org/fonts/listed")
    rec = fs("Listed", listed)
    out = latin.decide(one_family(rec), {rec.key: [rec]}, TH)  # no reader at all
    assert (out["listed"].latin, out["listed"].coverage) == (True, "extended")


def test_cjk_build_suggestions() -> None:
    fams = dict(
        FAMILIES, **{"maple-mono": ["Maple Mono"], "maple-mono-nf-cn": ["Maple Mono NF CN"]}
    )
    u = universe(fams)
    out = latin.decide(u, BY_KEY, TH, cmap_for=read_cmap)
    assert (out["maple-mono"].latin, out["maple-mono-nf-cn"].reason) == (True, "cjk")
    assert latin.cjk_build_suggestions(u, out) == [("maple-mono-nf-cn", "maple-mono")]


def test_decide_measures_each_file_once() -> None:
    calls: list[str] = []

    def counting(r: FontFileRef) -> frozenset[int] | None:
        calls.append(r.url)
        return read_cmap(r)

    cache: dict[str, latin.Measure] = {}
    first = latin.decide(universe(), BY_KEY, TH, cmap_for=counting, cache=cache)
    assert calls  # Pretendard and the Maple Mono files were read
    calls.clear()
    again = latin.decide(universe(), BY_KEY, TH, cmap_for=counting, cache=cache)
    assert calls == []
    assert again == first


def test_owner_include_of_a_non_google_family() -> None:
    out = latin.decide(universe(), BY_KEY, TH, allowlist={"nerd-only": True}, cmap_for=read_cmap)
    assert (out["nerd-only"].latin, out["nerd-only"].basis, out["nerd-only"].coverage) == (
        True,
        "owner_allowlist",
        "basic",
    )


# --- views ranks and the sheet ------------------------------------------------------------------


def views(name: str, value: float | None) -> Observation:
    return Observation(
        source="gf_stats",
        series="year",
        key=SourceKey("gf-family", name),
        value=value,
        unit="views",
        start=date(2025, 10, 1),
        end=date(2026, 9, 30),
    )


def test_competition_ranks_share_ties() -> None:
    vals = {
        SourceKey("gf-family", k): v for k, v in {"a": 5.0, "b": 9.0, "c": 5.0, "d": 1.0}.items()
    }
    ranks = latin.competition_ranks(vals, ascending=False)
    assert {k.key: r for k, r in ranks.items()} == {"b": 1, "a": 2, "c": 2, "d": 4}


def test_family_ranks_take_the_best_key() -> None:
    obs = [views("Poppins", 900.0), views("Poppins Old", 950.0), views("Hind", 10.0)]
    obs.append(views("Mukta", None))
    index = {
        SourceKey("gf-family", "Poppins"): "poppins",
        SourceKey("gf-family", "Poppins Old"): "poppins",
        SourceKey("gf-family", "Hind"): "hind",
        SourceKey("gf-family", "Mukta"): "mukta",
    }
    assert latin.family_ranks(obs, index, ascending=False) == {"poppins": 1, "hind": 3}


def test_sheet_lists_waiting_candidates_by_rank_in_batches() -> None:
    u = universe()
    google = {
        fid: latin.google_record(latin.family_records(fam, BY_KEY))
        for fid, fam in u.families.items()
        if fid in {"poppins", "hind", "mukta", "noto-sans-jp", "inter"}
    }
    ranks = {"poppins": 4, "hind": 120, "inter": 1}
    cands = latin.dual_script_candidates(google, ranks, {"mukta": False})  # type: ignore[arg-type]
    assert [(c.id, c.views_rank, c.ruled) for c in cands] == [
        ("poppins", 4, None),
        ("hind", 120, None),
        ("mukta", None, False),
    ]
    sheet = latin.allowlist_sheet(u, candidates=cands, notes=["Out 3: cjk 3"])
    assert sheet.index("| 4 | Poppins | `poppins` | Deva | 236 | latin-ext |") < sheet.index(
        "| 120 | Hind | `hind` |"
    )
    assert "`mukta`" not in sheet  # already ruled
    assert "## Batch 1" in sheet
    assert "## Notes for the lead" in sheet


def test_sheet_batches_of_eight_and_row_cap() -> None:
    cands = [
        latin.Candidate(f"f{i:02d}", "Deva", True, None, i + 1, None)
        for i in range(latin.SHEET_ROWS + 5)
    ]
    sheet = latin.allowlist_sheet(Universe(families={}, unmapped=()), candidates=cands)
    assert sheet.count("## Batch ") == latin.SHEET_ROWS // latin.BATCH_SIZE
    assert "`f31`" in sheet
    assert "`f32`" not in sheet
    assert f"Waiting: {latin.SHEET_ROWS + 5}; the first {latin.SHEET_ROWS} are below." in sheet


# --- reading font files -------------------------------------------------------------------------


def fake_facts(url: str, cmap: set[int] | frozenset[int]) -> FontFacts:
    """Facts as fontfiles would read them; the sha256 is ``ref``'s for the same file name."""
    name = url.rsplit("/", 1)[-1].removesuffix(".ttf")
    return FontFacts(
        sha256=hashlib.sha256(name.encode()).hexdigest(),
        git_blob=None,
        format="ttf",
        cmap=fontfiles.cmap_ranges(cmap),
        family_name=None,
        full_name=None,
        postscript_name=None,
        version=None,
        license_description=None,
        license_url=None,
        is_fixed_pitch=None,
        panose_proportion=None,
    )


FETCHER = SimpleNamespace()  # a live run's fetcher; facts_for is replaced, so it is never used


def fake_facts_for(calls: list[str]) -> object:
    """``fontfiles.facts_for`` over ``CMAPS``: the cache first, else a "read" (logged in calls)."""

    def facts_for(r: FontFileRef, fetcher: object, cache: FontFileCache) -> FontFacts:
        assert fetcher is not None, "no network in replay"
        found = fontfiles.cached_facts(r, cache)
        if found is not None:
            return found
        calls.append(r.url)
        if r.url.startswith("http://"):
            raise HostNotAllowed(f"not https: {r.url}")
        cmap = CMAPS.get(r.url)
        if cmap is None:
            raise FetchError(f"HTTP 404 for {r.url}")
        facts = fake_facts(r.url, cmap)
        cache.put(facts)
        cache.put_url(r.url, facts.sha256)
        return facts

    return facts_for


def test_file_cmaps_live_logs_every_read_and_never_retries_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(fontfiles, "facts_for", fake_facts_for(calls))
    cmaps = latin.FileCmaps(FontFileCache(tmp_path / "facts.jsonl"), FETCHER, LOG)  # type: ignore[arg-type]
    good = FontFileRef(url="https://files.example/Pretendard-Regular.ttf")
    gone = FontFileRef(url="https://files.example/Gone.ttf")
    plain = FontFileRef(url="http://files.example/Plain.ttf")
    assert cmaps(good) == PRETENDARD
    assert cmaps(gone) is None
    assert cmaps(plain) is None  # HostNotAllowed is a failed read, not a crashed stage
    assert cmaps(gone) is None
    assert calls == [good.url, gone.url, plain.url]  # the failure was not retried
    assert (cmaps.read, cmaps.failed) == (1, 3)
    logged = cmaps.reads.reads
    assert logged[good.url].sha256 == fake_facts(good.url, PRETENDARD).sha256
    assert logged[gone.url].sha256 is None
    assert "404" in (logged[gone.url].error or "")


def test_file_cmaps_try_no_other_member_of_an_archive_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(fontfiles, "facts_for", fake_facts_for(calls))
    cache = FontFileCache(tmp_path / "facts.jsonl")
    cmaps = latin.FileCmaps(cache, FETCHER, LOG)  # type: ignore[arg-type]
    dead = "https://dead.example/Fonts.zip"
    first, second = (FontFileRef(url=fontfiles.member_url(dead, f"F-{n}.otf")) for n in "AB")
    known = FontFileRef(url=fontfiles.member_url(dead, "F-C.otf"))
    facts = fake_facts(known.url, PRETENDARD)
    cache.put(facts)
    cache.put_url(known.url, facts.sha256)
    assert cmaps(first) is None
    assert cmaps(second) is None  # its archive is down: not requested again
    assert cmaps(known) == PRETENDARD  # the cache still answers
    assert calls == [first.url]
    assert "failed earlier this run" in (cmaps.reads.reads[second.url].error or "")


def test_file_cmaps_without_a_fetcher_answers_from_the_log_then_the_cache(tmp_path: Path) -> None:
    cache = FontFileCache(tmp_path / "facts.jsonl")
    known = fake_facts("https://files.example/Pretendard-Regular.ttf", PRETENDARD)
    cache.put(known)
    by_sha = FontFileRef(
        url="https://elsewhere.example/Pretendard-Regular.ttf", sha256=known.sha256
    )
    failed = FontFileRef(url="https://files.example/Failed.ttf", sha256=known.sha256)
    logged = {
        failed.url: FileRead(failed.url, None, "timeout"),
        "https://files.example/Moved.ttf": FileRead(
            "https://files.example/Moved.ttf", known.sha256
        ),
    }
    cmaps = latin.FileCmaps(cache, None, LOG, logged)
    assert cmaps(by_sha) == PRETENDARD  # the cache, by sha256
    assert cmaps(failed) is None  # the day's log wins over the cache
    assert cmaps(FontFileRef(url="https://files.example/Moved.ttf")) == PRETENDARD
    assert cmaps(FontFileRef(url="https://files.example/Unknown.ttf")) is None


# --- the stage ----------------------------------------------------------------------------------


EXTRA_RULING = """\
[L2-extra]
ruling = "Include Noto Sans Arabic (synthetic)."
reason = "Test: a Google family without a latin subset cannot be included."
include = ["noto-sans-arabic"]
"""


def make_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, store: bool = True
) -> tuple[StageContext, list[str]]:
    """A live run's context (fetcher and store) over the synthetic records; and the reads made."""
    root = tmp_path / "repo"
    paths = Paths.for_root(root)
    shutil.copytree(ROOT / "data" / "glyphsets", paths.data / "glyphsets")
    stageio.dump_stage(paths, "universe", universe())
    by_source: dict[str, list[Record]] = {}
    for r in RECORDS:
        by_source.setdefault(r.source, []).append(r)
    by_source["gf_stats"] = [
        views("Inter", 7.5e11),
        views("Poppins", 3.1e11),
        views("Hind", 2.0e9),
        views("Mukta", 1.0e9),
        views("Material Icons", 2.5e11),
    ]
    for source, recs in by_source.items():
        write_jsonl(recs, paths.records / f"{source}.jsonl")
    write_aliases([CN_ALIAS], paths.aliases_csv)
    write_ruling(paths, "2026-10-01", RULING.replace(', "hind"', ""))
    write_ruling(paths, "2026-10-02", EXTRA_RULING)
    calls: list[str] = []
    monkeypatch.setattr(fontfiles, "facts_for", fake_facts_for(calls))
    ctx = StageContext(
        paths=paths,
        config=SimpleNamespace(ranking=RANKING),  # type: ignore[arg-type]
        state=State(),
        run_date=DAY,
        store=Store(tmp_path / "store") if store else None,
        fetcher=FETCHER,  # type: ignore[arg-type]
        log=LOG,
    )
    return ctx, calls


def replay(ctx: StageContext) -> StageContext:
    return replace(ctx, fetcher=None, options=RunOptions(from_snapshots=DAY))


def outputs(ctx: StageContext) -> list[bytes]:
    paths = (ctx.paths.stage / "latin.json", ctx.paths.build / "review" / "latin-allowlist.md")
    return [p.read_bytes() for p in paths]


def test_stage_run_spot_checks_and_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, calls = make_run(tmp_path, monkeypatch)
    latin.run(ctx)
    paths = ctx.paths
    out = stageio.load_stage(paths, "latin")
    # docs/milestone-1.md step 5 "Done when": the spot checks, and no family without a verdict.
    spot = ("inter", "iosevka", "pretendard", "lxgw-wenkai")
    assert [out[f].latin for f in spot] == [True, True, False, False]
    assert set(out) == set(FAMILIES) - {"material-icons"}  # every eligible family, no dropped one
    assert all(isinstance(r.latin, bool) for r in out.values())
    assert out["poppins"].basis == "owner_allowlist"
    assert out["mukta"].reason == "owner_excluded"
    assert out["noto-sans-arabic"].reason == "no_latin_subset"  # the include could not apply
    assert out["maple-mono"].latin  # the CN build's file was skipped
    # Iosevka's registry list needed no read; LXGW WenKai's list is truncated, so it was read.
    assert sorted(calls) == [
        "https://files.example/LXGWWenKai-Regular.ttf",
        "https://files.example/MapleMono[wght].ttf",
        "https://files.example/Pretendard-Regular.ttf",
    ]
    assert ctx.store is not None
    assert (ctx.store.root / "_cache" / "fontfacts.jsonl").is_file()
    snap = ctx.store.snapshot(fontfiles.READS_SOURCE, DAY)
    assert snap is not None
    assert sorted(r["url"] for r in snap.iter_jsonl("latin.jsonl")) == sorted(calls)

    sheet = (paths.build / "review" / "latin-allowlist.md").read_text(encoding="utf-8")
    assert "| 4 | Hind | `hind` | Deva |" in sheet  # 4th of 5 on year views
    assert "`poppins`" not in sheet  # ruled
    assert "`mukta`" not in sheet
    for raw in ("750000000000", "7.5e", "2000000000", "2.0e9"):
        assert raw not in sheet  # ranks only (ruling T2)
    assert "Rule A: 5 of the 10 records on Google's live list" in sheet
    assert "Rule A passes 1 with a CJK subset and no primary script" in sheet
    assert "Single Day" in sheet
    assert "Included by a ruling but still out, having no latin subset: Noto Sans Arabic" in sheet
    assert "No file to test (1): Nerd Only" in sheet
    assert (paths.cache / "latin.json").is_file()


def test_stage_run_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, calls = make_run(tmp_path, monkeypatch)
    latin.run(ctx)
    first = outputs(ctx)
    calls.clear()
    latin.run(ctx)  # the second run reads its measurements from build/cache/latin.json
    assert outputs(ctx) == first
    assert calls == []
    (ctx.paths.cache / "latin.json").write_text("{not json", encoding="utf-8")
    latin.run(ctx)  # a damaged cache is rebuilt, from the store's facts cache
    assert outputs(ctx) == first
    assert calls == []


def test_a_replay_repeats_the_live_run_without_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, calls = make_run(tmp_path, monkeypatch)
    latin.run(ctx)
    first = outputs(ctx)
    shutil.rmtree(ctx.paths.cache)  # a fresh clone: only the store is left
    calls.clear()
    latin.run(replay(ctx))
    assert outputs(ctx) == first
    assert calls == []
    assert not (ctx.paths.cache / "latin.json").exists()  # a replay leaves the local cache alone

    # Had Pretendard's file failed that day, the replay fails it too, although the store's
    # facts cache and the local measurements (restored by a live run) both know the file.
    latin.run(ctx)
    assert (ctx.paths.cache / "latin.json").is_file()
    assert ctx.store is not None
    url = "https://files.example/Pretendard-Regular.ttf"
    record_reads(ctx.store, DAY, "latin", [FileRead(url, None, "timeout")])
    latin.run(replay(ctx))
    assert stageio.load_stage(ctx.paths, "latin")["pretendard"].reason == "no_file"


def test_a_run_without_the_store_caches_facts_in_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, calls = make_run(tmp_path, monkeypatch, store=False)
    latin.run(ctx)
    assert len(calls) == 3  # read, as with a store
    out = stageio.load_stage(ctx.paths, "latin")
    assert [out[f].latin for f in ("inter", "iosevka", "pretendard", "lxgw-wenkai")] == [
        True,
        True,
        False,
        False,
    ]
    assert (ctx.paths.cache / "fontfacts.jsonl").is_file()


def test_a_replay_with_nothing_recorded_tests_only_listed_code_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, calls = make_run(tmp_path, monkeypatch)
    latin.run(replay(ctx))
    out = stageio.load_stage(ctx.paths, "latin")
    assert out["iosevka"].latin  # from the registry's unicode range, no download
    assert out["pretendard"].reason == "no_file"
    assert calls == []


def test_a_repo_folder_is_a_google_family_only_when_queued_for_the_live_list() -> None:
    live = gf("Big Shoulders", "", ("latin",))
    repo = gf("bigshoulders", "", ("latin",), source="google_repo")
    split = gf("bigshouldersdisplay", "", ("latin",), source="google_repo")
    queued = replace(gf("newsans", "", ("latin",), source="google_repo"), status="queued")
    assert latin.google_record([repo, live]) == live
    assert latin.google_record([split]) is None  # the live list lacks it: the glyph test
    assert latin.google_record([queued]) == queued


@pytest.mark.parametrize("live_first", [True, False])
def test_latin_languages_takes_the_largest_count(live_first: bool) -> None:
    live = gf("Hind", "Deva", ("devanagari", "latin"), languages=0)  # the live list: empty
    repo = gf("hind", "Deva", ("devanagari", "latin"), source="google_repo", languages=31)
    keys = (live.key, repo.key) if live_first else (repo.key, live.key)
    fam = Family(
        id="hind",
        family="Hind",
        keys=keys,
        sources=("google_metadata", "google_repo"),
        first_seen=DAY,
        minted_from="Hind",
    )
    u = Universe(families={"hind": fam}, unmapped=())
    records = {live.key: [live], repo.key: [repo]}
    assert latin._latin_languages(["hind"], u, records) == {"hind": 31}
