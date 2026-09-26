"""Alias miner "name_tables" (milestone-1 step 7): upstream name-table names.

Every fixture is synthetic (ruling T1: Fontist data is synthetic-only): the
Fontist formula rows are written here by hand, and the font files are built
with fontTools' FontBuilder and served through ``httpx.MockTransport``. The
committed seed file is checked for the known answers, and one ``network`` test
reads a real pinned upstream file.
"""

import io
import logging
import pkgutil
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.helpers import ROOT

from tff_catalog import clock, stageio
from tff_catalog.aliases import BUILD_DETAILS, AliasCandidate, load_seeds, write_seeds
from tff_catalog.aliases import miners as miners_pkg
from tff_catalog.aliases.miners import MineContext, Miner
from tff_catalog.aliases.miners import name_tables as nt
from tff_catalog.fetch import Fetcher
from tff_catalog.fontfiles import FontFileError
from tff_catalog.keys import match_key
from tff_catalog.paths import Paths, StoreNotConfigured
from tff_catalog.records import FileRole, FontFileRef, SourceKey, UniverseRecord
from tff_catalog.store import RawDir, Store

DAY = date(2026, 9, 26)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
FILES = f"https://raw.githubusercontent.com/example/font-files/{COMMIT}"
ARCHIVE = "https://example.com/releases/Inter-4.0.zip"
SEED_FILE = ROOT / "data" / "alias-seeds" / "name_tables.csv"


def name_key(name: str) -> SourceKey:
    return SourceKey("font-name", name)


def cand(
    alias: str, target: SourceKey, relation: str, detail: str, evidence: str
) -> AliasCandidate:
    return AliasCandidate(name_key(alias), target, relation, detail, "name_tables", evidence, False)


def style(
    family: str, font: str, *, preferred: str | None = None, ps: str | None = None
) -> dict[str, Any]:
    return {
        "family_name": family,
        "font": font,
        "full_name": f"{family} Regular",
        "post_script_name": ps,
        "preferred_family_name": preferred,
        "type": "Regular",
    }


def formula(key: str, styles: list[dict[str, Any]], **flags: Any) -> dict[str, Any]:
    """A ``formulas.jsonl.gz`` row as ``collectors.universe.fontist`` writes it."""
    return {
        "fonts": [{"collection": None, "name": key, "styles": styles}],
        "homepage": None,
        "key": key,
        "license_url": None,
        "name": key,
        "open_license": flags.get("open_license", True),
        "repository": None,
        "requires_license_agreement": flags.get("requires_license_agreement", False),
        "resources": flags.get(
            "resources",
            [
                {
                    "file_size": 1,
                    "name": f"{key}.zip",
                    "sha256": flags.get("sha256", "e" * 64),
                    "urls": flags.get("urls", []),
                }
            ],
        ),
        "spdx_license": "OFL-1.1",
    }


INTER = formula(
    "inter",
    [
        style("Inter", "Inter-Regular.otf", ps="Inter-Regular"),
        style("Inter Black", "Inter-Black.otf", preferred="Inter", ps="Inter-Black"),
        style("Inter Display", "InterDisplay-Regular.otf", ps="InterDisplay-Regular"),
        style(
            "Inter Display SemiBold",
            "InterDisplay-SemiBold.otf",
            preferred="Inter Display",
            ps="InterDisplay-SemiBold",
        ),
        style("Inter Variable", "InterVariable.ttf", ps="InterVariable"),
    ],
    urls=["http://example.com/old.zip", ARCHIVE],
)
FIRA = formula(
    "fira_code",
    [
        style("Fira Code", "FiraCode-Regular.ttf", ps="FiraCode-Regular"),
        style(
            "Fira Code Retina", "FiraCode-Retina.ttf", preferred="Fira Code", ps="FiraCode-Retina"
        ),
    ],
    urls=["https://example.com/Fira_Code_v5.2.zip"],
)
# Three families in one archive, none a build of another: no row.
PLEX = formula(
    "ibm_plex",
    [
        style("IBM Plex Sans", "IBMPlexSans-Regular.otf", ps="IBMPlexSans-Regular"),
        style("IBM Plex Sans Condensed", "IBMPlexSansCond.otf", ps="IBMPlexSansCondensed-Regular"),
        style("IBM Plex Mono", "IBMPlexMono-Regular.otf", ps="IBMPlexMono-Regular"),
        style("Inter", "x.otf"),
        style("Inter Tight", "y.otf"),
    ],
    urls=["https://example.com/plex.zip"],
)
PROPRIETARY = formula(
    "office",
    [style("Office Sans", "os.ttf"), style("Office Sans Variable", "osv.ttf")],
    open_license=False,
    requires_license_agreement=True,
    urls=["https://example.com/office.exe"],
)


# --- rules ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "base", "detail"),
    [
        ("Inter Variable", "Inter", "variable"),
        ("Inter Display", "Inter", "opsz"),
        ("Newsreader 16pt", "Newsreader", "opsz"),
        ("JetBrains Mono NL", "JetBrains Mono", "nl"),
        ("Cascadia Code PL", "Cascadia Code", "powerline"),
        ("monofur for Powerline", "Monofur", "powerline"),
        ("Hack Nerd Font Mono", "Hack", "nfm"),
        ("Cascadia Code NF", "Cascadia Code", "nf"),
        ("Maple Mono NF CN", "Maple Mono", "cjk"),
        ("Inter Display Variable", "Inter", "opsz"),
        ("D2Coding ligature", "D2Coding", ""),
        ("Monaspace Neon Var", "Monaspace Neon", "variable"),
        ("inter  variable", "Inter", "variable"),
        ("Inter Tight", "Inter", None),
        ("Roboto Slab", "Roboto", None),
        ("Noto Sans JP", "Noto Sans", None),
        ("IBM Plex Mono", "IBM Plex Sans", None),
        ("TeX Gyre Heros Cn", "TeX Gyre Heros", None),  # "Cn" is condensed; only "CN" is CJK
        ("Inter", "Inter", None),
        ("Inter", "Inter Variable", None),
        ("InterVariable", "Inter", None),  # no word boundary: never matched by prefix
        ("Inter Variable", "", None),
    ],
)
def test_build_detail(name: str, base: str, detail: str | None) -> None:
    assert nt.build_detail(name, base) == detail


@pytest.mark.parametrize(
    ("family", "typographic", "expected"),
    [
        ("Fira Code Retina", "Fira Code", "Fira Code Retina"),
        ("Inter Display SemiBold", "Inter Display", None),
        ("Noto Sans Condensed Black", "Noto Sans", "Noto Sans Condensed"),
        ("Comic Shanns Mono-Regular", "Comic Shanns Mono", None),
        ("Martian Mono Cn Lt", "Martian Mono", "Martian Mono Cn"),
        ("Martian Mono Std xBd", "Martian Mono", "Martian Mono Std"),
        ("Kinto Sans Med", "Kinto Sans", None),
        ("Selawik Semilight", "Selawik", None),
        ("Montserrat Alternates ExLight", "Montserrat Alternates", None),
        ("Joongnajoche Light OTF", "Joongnajoche OTF", None),
        ("Archivo Black", "Archivo", None),
        # Only a name that begins with the ID 16 name: another font of a
        # collection ("NSimSun" in SimSun's file) or a fork's parent is not it.
        ("NSimSun-18030", "SimSun-18030", None),
        ("Inter", "Adwaita Sans", None),
        ("Noto Sans_Condensed Black", "Noto Sans", "Noto Sans Condensed"),
        ("Inter", "Inter", None),
        ("Inter", None, None),
        (None, "Inter", None),
    ],
)
def test_legacy_alias(family: str | None, typographic: str | None, expected: str | None) -> None:
    assert nt.legacy_alias(nt.FontNames(family, typographic)) == expected


@pytest.mark.parametrize(
    ("legacy", "base", "typo_detail", "expected"),
    [
        ("Aptos Display", "Aptos", None, "opsz"),
        ("Noto Sans Condensed", "Noto Sans", None, "static"),
        ("Maple Mono NF Condensed", "Maple Mono", "nf", "nf"),
        ("Maple Mono NF CN Condensed", "Maple Mono", "cjk", "cjk"),
        ("D2Coding ligature Condensed", "D2Coding", "", "static"),
        ("Inter Display Variable", "Inter", None, "opsz"),
    ],
)
def test_legacy_detail(legacy: str, base: str, typo_detail: str | None, expected: str) -> None:
    assert nt.legacy_detail(legacy, base, typo_detail) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Martian Mono Cn Lt", "Martian Mono Cn"),
        ("Inter Bold Italic", "Inter"),
        ("Foo Bd It", "Foo"),
        ("Lt", "Lt"),  # keeps one word
        ("Archivo  Black", "Archivo"),
    ],
)
def test_strip_styles(name: str, expected: str) -> None:
    assert nt.strip_styles(name) == expected


@pytest.mark.parametrize(
    ("ps", "known", "expected"),
    [
        ("3270-Regular", {"ibm3270"}, ["3270"]),
        ("Newsreader16pt-Regular", {"newsreader"}, ["Newsreader16pt"]),
        ("Aptos-Narrow-Bold-Italic", {"aptosnarrow"}, []),
        ("Inter-Regular", {"inter"}, []),
        ("ArialMT", {"arial"}, []),  # no "-<style>": the family part is not certain
        ("DMCAsansserif-100", {"dmcasansserif"}, []),
        ("Arial-Black", {"arialblack"}, []),  # the whole name is the family
        ("InterVariable", {"intervariable"}, []),
        ("-Regular", {"x"}, []),
    ],
)
def test_postscript_aliases(ps: str, known: set[str], expected: list[str]) -> None:
    names = nt.FontNames("x", postscript=ps)
    assert nt.postscript_aliases(names, known) == expected


def test_no_postscript_alias_from_a_file_whose_names_disagree() -> None:
    """A collection member or a fork: ID 1 does not begin with ID 16, so no name is certain."""
    agree = nt.FontNames("IBM 3270 Semi Bold", "IBM 3270", "3270-Bold")
    disagree = nt.FontNames("NSimSun", "SimSun", "NSimSun-Regular")
    spaced = nt.FontNames("TeXGyreHerosCn", "TeX Gyre Heros Cn", "TeXGyreHerosCondensed-Bold")
    assert nt.postscript_aliases(agree, {"ibm3270"}) == ["3270"]
    assert nt.postscript_aliases(spaced, {"texgyreheroscn"}) == ["TeXGyreHerosCondensed"]
    assert nt.postscript_aliases(disagree, {"simsun"}) == []


def test_a_forks_inherited_variations_prefix_is_not_read() -> None:
    """Adwaita Sans (from Inter) keeps ID 25 "InterVariable": no row may say it is Adwaita."""
    body = make_font("Adwaita Sans", ps="AdwaitaSans-Regular", prefix="InterVariable")
    url = f"{FILES}/AdwaitaSans-Regular.ttf"
    with fetcher({url: body}) as f:
        names = nt.read_names(FontFileRef(url), f)
    assert names == nt.FontNames("Adwaita Sans", None, "AdwaitaSans-Regular")
    rec = record("Adwaita Sans", (), key=SourceKey("fs-id", "adwaita-sans"))
    assert nt.record_candidates(rec, [nt.NamedFile(names, url)]) == []


# --- kind 1: Fontist formulas ---------------------------------------------------------------------


def test_formula_files_read_every_style_with_its_archive() -> None:
    files = nt.formula_files(INTER)
    assert [f.evidence for f in files][:2] == [
        f"{ARCHIVE}#Inter-Regular.otf",
        f"{ARCHIVE}#Inter-Black.otf",
    ]
    assert files[1].names == nt.FontNames("Inter Black", "Inter", "Inter-Black")
    no_url = nt.formula_files(formula("x", [style("X", "x.ttf")]))
    assert [f.evidence for f in no_url] == ["fontist:x#x.ttf"]


def test_fontist_candidates_known_answers() -> None:
    notes: list[str] = []
    found = nt.best(nt.fontist_candidates([INTER, FIRA, PLEX, PROPRIETARY], notes))
    inter, fira = name_key("Inter"), name_key("Fira Code")
    assert found == [
        cand(
            "Fira Code Retina",
            fira,
            "build",
            "static",
            "https://example.com/Fira_Code_v5.2.zip#FiraCode-Retina.ttf",
        ),
        cand("Inter Display", inter, "build", "opsz", f"{ARCHIVE}#InterDisplay-Regular.otf"),
        cand("Inter Variable", inter, "build", "variable", f"{ARCHIVE}#InterVariable.ttf"),
    ]
    assert notes == []


def test_legacy_names_take_the_detail_of_their_build_words() -> None:
    """ID 1 "Aptos Display" of ID 16 "Aptos" is an optical size, like "Inter Display"."""
    row = formula(
        "aptos_like",
        [
            style("Sans", "Sans.ttf", preferred="Sans", ps="Sans"),
            style("Sans Display", "Sans-Display.ttf", preferred="Sans", ps="SansDisplay"),
            style("Sans Black", "Sans-Black.ttf", preferred="Sans", ps="Sans-Black"),
            style("NSans", "NSans.ttf", preferred="Sans", ps="NSans-Regular"),
        ],
        urls=["https://example.com/sans.zip"],
    )
    found = nt.best(nt.fontist_candidates([row]))
    assert [(c.alias.key, c.target.key, c.relation, c.detail) for c in found] == [
        ("Sans Display", "Sans", "build", "opsz"),
    ]


def test_unpinned_formulas_are_skipped() -> None:
    notes: list[str] = []
    row = formula("loose", [style("Loose", "a.ttf"), style("Loose Variable", "b.ttf")], sha256=None)
    assert nt.fontist_candidates([row], notes) == []
    assert notes == ["fontist formula 'loose': no sha256, not pinned"]
    several = formula("x", [style("X", "x.ttf")], sha256=["a" * 64, "b" * 64])
    assert nt.is_pinned_formula(several)


def test_the_evidence_is_the_resource_that_holds_the_file() -> None:
    resources = [
        {"name": "Mono.zip", "sha256": "a" * 64, "urls": ["https://example.com/Mono.zip"]},
        {"name": "Mono-NL.ttf", "sha256": "b" * 64, "urls": ["https://example.com/Mono-NL.ttf"]},
    ]
    row = formula(
        "mono",
        [style("Mono", "Mono.ttf"), style("Mono NL", "mono-nl.TTF")],
        resources=resources,
    )
    row["fonts"].append({"collection": "Mono.ttc", "name": "c", "styles": [style("Mono", None)]})
    assert [f.evidence for f in nt.formula_files(row)] == [
        "https://example.com/Mono.zip#Mono.ttf",
        "https://example.com/Mono-NL.ttf#mono-nl.TTF",
        "https://example.com/Mono.zip#Mono.ttc",  # a collection's styles name its file
    ]


def test_proprietary_formulas_are_read_when_the_switch_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nt, "SKIP_PROPRIETARY", False)
    found = nt.best(nt.fontist_candidates([PROPRIETARY]))
    assert [(c.alias.key, c.target.key, c.detail) for c in found] == [
        ("Office Sans Variable", "Office Sans", "variable")
    ]


def test_a_postscript_family_of_several_families_is_left_out() -> None:
    row = formula(
        "franklin",
        [
            style("Franklin Gothic Book", "a.ttf", ps="FranklinGothic-Regular"),
            style("Franklin Gothic Demi", "b.ttf", ps="FranklinGothic-Bold"),
            style("Franklin Gothic Heavy", "c.ttf", ps="FranklinGothicHv-Regular"),
        ],
    )
    notes: list[str] = []
    found = nt.best(nt.fontist_candidates([row], notes))
    assert [(c.alias.key, c.relation, c.target.key) for c in found] == [
        ("FranklinGothicHv", "postscript", "Franklin Gothic Heavy")
    ]
    assert notes == [
        "'FranklinGothic': one file set names it for Franklin Gothic Book, Franklin Gothic Demi"
    ]


def test_the_root_is_the_base_with_fewest_words() -> None:
    row = formula(
        "maple",
        [
            style("Maple Mono", "a.ttf"),
            style("Maple Mono NF", "b.ttf"),
            style("Maple Mono NF CN", "c.ttf"),
        ],
    )
    found = nt.best(nt.fontist_candidates([row]))
    assert [(c.alias.key, c.target.key, c.detail) for c in found] == [
        ("Maple Mono NF", "Maple Mono", "nf"),
        ("Maple Mono NF CN", "Maple Mono", "cjk"),
    ]


def test_best_keeps_one_candidate_per_alias_and_target() -> None:
    target = name_key("Inter")
    kept = nt.best(
        [
            cand("Inter Variable", target, "postscript", "", "b"),
            cand("inter variable", target, "build", "variable", "z"),
            cand("Inter Variable", target, "build", "variable", "a"),
            cand("Inter Variable", target, "build", "static", "0"),
            cand("Inter", target, "build", "", "self"),  # a family's own name
            cand("Inter Variable", SourceKey("fs-id", "inter"), "build", "variable", "c"),
        ]
    )
    assert kept == [
        cand("Inter Variable", target, "build", "variable", "a"),
        cand("Inter Variable", SourceKey("fs-id", "inter"), "build", "variable", "c"),
    ]


# --- kind 2: the universe's pinned files ----------------------------------------------------------


def ref(name: str, role: FileRole = "regular") -> FontFileRef:
    return FontFileRef(url=f"{FILES}/{name}", role=role)


def record(
    family: str,
    files: tuple[FontFileRef, ...],
    *,
    source: str = "fontsource",
    key: SourceKey | None = None,
) -> UniverseRecord:
    key = key or SourceKey("fs-id", family.lower().replace(" ", "-"))
    return UniverseRecord(source=source, key=key, family=family, files=files)


def test_chosen_files_are_pinned_upstream_files_first_regular_and_variable() -> None:
    gf = FontFileRef(
        f"https://raw.githubusercontent.com/google/fonts/{COMMIT}/ofl/inter/Inter.ttf", "a" * 64
    )
    unpinned = FontFileRef("https://example.com/main/Z.ttf")
    archive = FontFileRef("https://example.com/Z.zip", "b" * 64, role="other")
    hashed = FontFileRef("https://example.com/Hashed.otf", "c" * 64, role="italic")
    rec = record(
        "Z",
        (
            ref("B-Regular.ttf"),
            ref("A-Regular.ttf"),
            ref("Z[wght].ttf", "variable"),
            ref("A-Italic.ttf", "italic"),
            gf,
            unpinned,
            archive,
            hashed,
        ),
    )
    assert nt.chosen_files(rec) == [ref("A-Regular.ttf"), ref("Z[wght].ttf", "variable")]
    only_italic = record("Z", (hashed, unpinned, gf))
    assert nt.chosen_files(only_italic) == [hashed]
    assert nt.chosen_files(record("Z", (ref("A-Regular.ttf"),), source="fontist")) == []
    assert nt.is_pinned(ref("A.ttf"))
    assert nt.is_pinned(hashed)
    assert not nt.is_pinned(unpinned)
    assert not nt.is_pinned(archive)


def test_record_candidates() -> None:
    rec = record("IBM 3270", (), source="nerdfonts", key=SourceKey("nerd-folder", "3270"))
    files = [
        nt.NamedFile(nt.FontNames("IBM 3270", postscript="3270-Regular"), "u1"),
        nt.NamedFile(nt.FontNames("IBM 3270 Variable", postscript="IBM3270Variable"), "u2"),
        nt.NamedFile(nt.FontNames("IBM 3270 Semi", "IBM 3270", "3270-SemiNarrow"), "u3"),
        nt.NamedFile(nt.FontNames("Noto Sans Mono", postscript="NotoSansMono-Regular"), "u4"),
        nt.NamedFile(nt.FontNames(None), "u5"),
    ]
    notes: list[str] = []
    found = nt.best(nt.record_candidates(rec, files, notes))
    target = rec.key
    assert found == [
        cand("3270", target, "postscript", "", "u1"),
        cand("IBM 3270 Semi", target, "build", "static", "u3"),
        cand("IBM 3270 Variable", target, "build", "variable", "u2"),
    ]
    assert notes == ["nerd-folder:3270 (IBM 3270): u4 is 'Noto Sans Mono'"]


def test_a_fontist_record_targets_its_family_name() -> None:
    rec = record("Inter", (), source="homebrew_casks", key=SourceKey("fontist-formula", "inter"))
    assert nt.record_target(rec) == name_key("Inter")


# --- font files over (mock) HTTP ------------------------------------------------------------------


def make_font(
    family: str,
    *,
    typographic: str | None = None,
    ps: str | None = None,
    prefix: str | None = None,
    glyphs: int = 1,
) -> bytes:
    """A minimal TrueType font whose name table says these names.

    Many glyphs make ``hmtx`` big, so the ``name`` table lies past the first
    16 KB and ``fontfiles.read_font`` has to fetch it with a second range.
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", *(f"g{i}" for i in range(1, glyphs))]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({})
    empty = TTGlyphPen(None).glyph()
    fb.setupGlyf(dict.fromkeys(order, empty))
    fb.setupHorizontalMetrics(dict.fromkeys(order, (500, 0)))
    fb.setupHorizontalHeader()
    names: dict[str, str] = {"familyName": family, "styleName": "Regular"}
    if typographic:
        names["typographicFamily"] = typographic
    if ps:
        names["psName"] = ps
    if prefix:
        names["variationsPostScriptNamePrefix"] = prefix
    fb.setupNameTable(names)
    fb.setupOS2()
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


def ranged(files: dict[str, bytes], seen: list[str] | None = None) -> httpx.MockTransport:
    """Serve ``files`` by URL, honouring ``Range: bytes=a-b``; 404 for anything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append(f"{url} {request.headers.get('range', '')}".strip())
        body = files.get(url)
        if body is None:
            return httpx.Response(404)
        m = re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers.get("range", ""))
        if m is None:
            return httpx.Response(200, content=body)
        start, end = int(m[1]), min(int(m[2]), len(body) - 1)
        headers = {"content-range": f"bytes {start}-{end}/{len(body)}"}
        return httpx.Response(206, content=body[start : end + 1], headers=headers)

    return httpx.MockTransport(handler)


def fetcher(files: dict[str, bytes], seen: list[str] | None = None) -> Fetcher:
    hosts = {httpx.URL(u).host for u in files} | {"raw.githubusercontent.com"}
    return Fetcher(
        hosts=hosts,
        min_interval=dict.fromkeys(hosts, 0.0),
        retries=0,
        transport=ranged(files, seen),
    )


def test_read_names_reads_the_name_table_by_range() -> None:
    body = make_font("Inter Variable", typographic=None, ps="InterVariable", glyphs=6000)
    assert len(body) > 20_000
    url = f"{FILES}/InterVariable.ttf"
    seen: list[str] = []
    with fetcher({url: body}, seen) as f:
        names = nt.read_names(FontFileRef(url, size=len(body)), f)
    assert names == nt.FontNames("Inter Variable", None, "InterVariable")
    assert len(seen) == 2
    assert all("bytes=" in s for s in seen)


def test_read_names_rejects_a_wrong_size_and_a_non_font() -> None:
    body = make_font("X")
    url, junk = f"{FILES}/X.ttf", f"{FILES}/junk.ttf"
    with fetcher({url: body, junk: b"not a font at all" * 10}) as f:
        with pytest.raises(FontFileError, match="not the expected"):
            nt.read_names(FontFileRef(url, size=len(body) + 1), f)
        with pytest.raises(FontFileError):
            nt.read_names(FontFileRef(junk), f)


def test_read_all_tolerates_a_few_failures_then_gives_up(caplog: pytest.LogCaptureFixture) -> None:
    recs = [record(f"F{i}", (ref(f"F{i}.ttf"),)) for i in range(4)]

    def reader(r: FontFileRef) -> nt.FontNames:
        if r.url.endswith(("F1.ttf", "F2.ttf", "F3.ttf")):
            raise FontFileError("broken")
        return nt.FontNames("F0")

    with caplog.at_level(logging.WARNING):
        reads = nt.read_all(recs, reader, max_failures=3, log=logging.getLogger("t"))
    assert reads.names == {f"{FILES}/F0.ttf": nt.FontNames("F0")}
    assert sorted(reads.failed) == [f"{FILES}/F{i}.ttf" for i in (1, 2, 3)]
    assert "cannot read" in caplog.text
    with pytest.raises(RuntimeError, match="more than 2"):
        nt.read_all(recs, reader, max_failures=2)


# --- the miner end to end -------------------------------------------------------------------------


def write_fontist(store: Path, rows: list[dict[str, Any]], day: date = DAY) -> None:
    writer = Store(store).writer(nt.FONTIST, day, 1)
    writer.write_jsonl(nt.FORMULAS_EXTRACT, rows)
    writer.close()


def context(tmp_path: Path, store: Path | None) -> MineContext:
    paths = Paths.for_root(tmp_path / "repo", store=store, raw_root=tmp_path / "raw")
    raw = RawDir(tmp_path / "raw" / "aliases")
    return MineContext(paths, Store(store) if store else None, raw, DAY, logging.getLogger("t"))


NERD = record(
    "IBM 3270",
    (ref("3270-Regular.ttf"),),
    source="nerdfonts",
    key=SourceKey("nerd-folder", "3270"),
)
MONO = record("Martian", (ref("Martian-Regular.ttf"), ref("MartianVF.ttf", "variable")))
SERVED = {
    f"{FILES}/3270-Regular.ttf": make_font("IBM 3270", ps="3270-Regular"),
    f"{FILES}/Martian-Regular.ttf": make_font("Martian", ps="Martian-Regular"),
    f"{FILES}/MartianVF.ttf": make_font("Martian VF", ps="MartianVF-Regular"),
}


def setup_inputs(tmp_path: Path, recs: list[UniverseRecord]) -> MineContext:
    store = tmp_path / "store"
    write_fontist(store, [INTER, FIRA, PLEX])
    ctx = context(tmp_path, store)
    stageio.dump_rows(recs, ctx.paths.records / "fontsource.jsonl")
    return ctx


def test_mine_end_to_end(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    ctx = setup_inputs(tmp_path, [NERD, MONO])
    miner = nt.NameTablesMiner(transport=ranged(SERVED), min_interval=0.0)
    with caplog.at_level(logging.INFO):
        found = miner.mine(ctx)
    assert [(c.alias.key, c.target, c.relation, c.detail) for c in found] == [
        ("3270", NERD.key, "postscript", ""),
        ("Fira Code Retina", name_key("Fira Code"), "build", "static"),
        ("Inter Display", name_key("Inter"), "build", "opsz"),
        ("Inter Variable", name_key("Inter"), "build", "variable"),
        ("Martian VF", MONO.key, "build", "variable"),
    ]
    assert {c.source for c in found} == {"name_tables"}
    assert not any(c.auto for c in found)
    assert found[-1].evidence == f"{FILES}/MartianVF.ttf"
    assert "5 candidates (3 formulas" in caplog.text
    # The stage writes the seed file; the miner writes nothing, least of all aliases.csv.
    assert not ctx.paths.aliases_csv.exists()
    assert not ctx.paths.alias_seeds.exists()
    # Deterministic, and a valid seed file.
    again = nt.NameTablesMiner(transport=ranged(SERVED), min_interval=0.0).mine(ctx)
    assert again == found
    seeds = tmp_path / "name_tables.csv"
    write_seeds(found, seeds)
    assert load_seeds(seeds) == found


def test_mine_fails_without_its_inputs(tmp_path: Path) -> None:
    miner = nt.NameTablesMiner(transport=ranged({}), min_interval=0.0)
    with pytest.raises(StoreNotConfigured):
        miner.mine(context(tmp_path, None))
    store = tmp_path / "store"
    store.mkdir()
    with pytest.raises(FileNotFoundError, match="no fontist snapshot"):
        miner.mine(context(tmp_path, store))
    write_fontist(store, [INTER])
    with pytest.raises(FileNotFoundError, match="tff-catalog parse"):
        miner.mine(context(tmp_path, store))


def test_mine_fails_when_too_many_files_fail(tmp_path: Path) -> None:
    ctx = setup_inputs(tmp_path, [NERD, MONO])
    miner = nt.NameTablesMiner(transport=ranged({}), min_interval=0.0, max_failures=2)
    with pytest.raises(RuntimeError, match="committed seeds"):
        miner.mine(ctx)


def test_the_miner_satisfies_the_discovery_contract() -> None:
    # What miners.discover() checks per module, without importing the other miners.
    modules = {m.name for m in pkgutil.iter_modules(miners_pkg.__path__)}
    assert "name_tables" in modules
    assert isinstance(nt.MINER, Miner)
    assert nt.MINER.name == "name_tables"


# --- the committed seed file ----------------------------------------------------------------------


def test_committed_seeds_hold_the_known_answers() -> None:
    rows = load_seeds(SEED_FILE)
    assert rows == sorted(set(rows))
    assert {c.source for c in rows} == {"name_tables"}
    assert {c.alias.ns for c in rows} == {"font-name"}
    assert not any(c.auto for c in rows)
    assert {c.relation for c in rows} <= {"build", "postscript"}
    assert {c.detail for c in rows if c.relation == "build"} <= BUILD_DETAILS
    assert {c.detail for c in rows if c.relation == "postscript"} == {""}
    assert all(c.evidence.startswith("https://") for c in rows)  # a pinned upstream file
    got = {(c.alias.key, c.target.key, c.relation, c.detail) for c in rows}
    assert {
        ("Inter Variable", "Inter", "build", "variable"),
        ("Inter Display", "Inter", "build", "opsz"),
        ("JetBrains Mono NL", "JetBrains Mono", "build", "nl"),
        ("Fira Code Retina", "Fira Code", "build", "static"),
        ("Pretendard Variable", "pretendard", "build", "variable"),
        ("Libertinus Serif Display", "Libertinus Serif", "build", "opsz"),
        ("3270", "3270", "postscript", ""),
    } <= got
    for bad in ("Inter Tight", "Roboto Slab", "Noto Sans JP", "Playfair Display"):
        assert not any(c.alias.key == bad for c in rows), bad
    # Adwaita Sans keeps Inter's ID 25 prefix; only Inter may own "Inter Variable".
    assert {c.target.key for c in rows if match_key(c.alias.key) == "intervariable"} == {"Inter"}


# --- real data ------------------------------------------------------------------------------------

NERD_3270 = (
    "https://raw.githubusercontent.com/ryanoasis/nerd-fonts/"
    "64a084f95480ee5efb62132c01cd142ab6147655/src/unpatched-fonts/3270/3270-Regular.ttf"
)


@pytest.mark.network
def test_real_pinned_file_name_table() -> None:
    """The Nerd Fonts copy of IBM 3270 at a pinned commit, read by range."""
    hosts = frozenset({"raw.githubusercontent.com"})
    with Fetcher(hosts=hosts) as f:
        names = nt.read_names(FontFileRef(NERD_3270), f)
    assert names.family == "IBM 3270"
    assert names.postscript == "3270-Regular"
    rec = record("IBM 3270", (), source="nerdfonts", key=SourceKey("nerd-folder", "3270"))
    found = nt.record_candidates(rec, [nt.NamedFile(names, NERD_3270)])
    assert [(c.alias.key, c.relation) for c in found] == [("3270", "postscript")]


@pytest.mark.store
def test_real_fontist_snapshot_gives_the_inter_builds() -> None:
    """The newest Fontist snapshot in ``$TFF_STORE`` names Inter's builds."""
    store = Store(Paths.from_env().require_store())
    snap = store.latest(nt.FONTIST, clock.utc_today())
    if snap is None:
        pytest.skip("no fontist snapshot in TFF_STORE")
    found = nt.best(nt.fontist_candidates(nt.formula_rows(snap)))
    got = {(c.alias.key, c.target.key, c.detail) for c in found}
    assert ("Inter Variable", "Inter", "variable") in got
    assert ("Inter Display", "Inter", "opsz") in got
