"""Tests for the universe collector "fontist" (``collectors/universe/fontist.py``).

The generic contract (identity, fetch, parse, records, end to end) is in
``tests/collectors/test_contract.py``. These tests pin the rules the golden file
only shows by example: facts only in the extract, family names, file roles and
hashes, RFN, NOASSERTION, the proprietary drop, and the schema guard. The fixture
is synthetic (``tests/fixtures/collectors/fontist/NOTICE``).
"""

import dataclasses
import gzip
import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock, gitsrc
from tff_catalog.collectors import get
from tff_catalog.collectors.base import FetchContext, ParseContext, load_settings
from tff_catalog.collectors.universe import fontist
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import FontFileRef, LicenseFact, SourceKey, UniverseRecord
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir("fontist")
DAY = date(2026, 10, 3)
LOG = logging.getLogger("tests.fontist")
SENTINEL = "SENTINEL-"  # every description, copyright and license text in the fixture has it


def settings(**changes: Any) -> fontist.Fontist.Settings:
    base = load_settings(fontist.COLLECTOR, Paths.for_root(ROOT))
    assert isinstance(base, fontist.Fontist.Settings)
    return dataclasses.replace(base, **changes)


def run_fetch(
    tmp: Path, monkeypatch: pytest.MonkeyPatch, s: object, previous: Snapshot | None = None
) -> Snapshot:
    """``fetch()`` against the fixture's git remote, offline, into a fresh store."""
    for key, value in mockhttp.git_remotes(FIXTURE / "git", tmp / "remotes", DAY).items():
        monkeypatch.setenv(key, value)
    store = Store(tmp / "store")
    c = fontist.COLLECTOR
    with (
        clock.frozen(datetime.combine(DAY, time(6), tzinfo=UTC)),
        Fetcher(transport=mockhttp.MockHTTP(()).transport) as fetcher,
        store.writer(c.name, DAY, c.version) as out,
    ):
        c.fetch(
            FetchContext(
                run_date=DAY,
                fetcher=fetcher.scoped(c.hosts),
                out=out,
                raw=RawDir(tmp / "raw"),
                previous=previous,
                settings=s,
                log=LOG,
            )
        )
    snap = store.snapshot(c.name, DAY)
    assert snap is not None
    return snap


def parse(snap: Snapshot, s: object | None = None) -> list[Any]:
    ctx = ParseContext(snapshot=snap, settings=s or settings(), log=LOG)
    return list(fontist.COLLECTOR.parse(ctx))


def style(family: str, kind: str = "Regular", font: str | None = None, **more: str) -> dict:
    return {"family_name": family, "type": kind, "font": font, **more}


def row(**changes: Any) -> dict[str, Any]:
    """An extract row with one family ("Opal Sans") and one archive."""
    base: dict[str, Any] = {
        "key": "opal_sans",
        "name": "Opal Sans",
        "homepage": "https://opal.synth.example",
        "repository": None,
        "license_url": None,
        "spdx_license": "OFL-1.1-RFN",
        "open_license": True,
        "requires_license_agreement": False,
        "resources": [
            {
                "name": "Opal.zip",
                "urls": ["https://opal.synth.example/Opal.zip"],
                "sha256": "ab" * 32,
                "file_size": 10,
            }
        ],
        "fonts": [{"name": "Opal Sans", "collection": None, "styles": [style("Opal Sans")]}],
    }
    return base | changes


def resource(name: str, *urls: str, sha: object = None) -> dict[str, Any]:
    return {"name": name, "urls": list(urls), "sha256": sha, "file_size": None}


# --- the collector and its settings -----------------------------------------------------------


def test_collector_is_discovered_with_the_design_identity() -> None:
    c = get("fontist")
    assert c is fontist.COLLECTOR
    assert (c.kind, c.group, c.needs_baseline) == ("universe", None, False)
    assert set(c.emits) == {UniverseRecord, LicenseFact}


def test_settings_read_the_root_and_sil_folders_only() -> None:
    s = settings()
    assert s.folders == (".", "sil")
    assert fontist.sparse_patterns(s.folders) == ["/Formulas/*.yml", "/Formulas/sil/*.yml"]
    assert (s.repository, s.schema_version) == ("https://github.com/fontist/formulas", 5)


# --- fetch: the extract keeps facts only ------------------------------------------------------


def test_formula_row_keeps_facts_and_drops_formula_text() -> None:
    doc = yaml.safe_load(
        (FIXTURE / "git/github.com/fontist/formulas/Formulas/aster_sans.yml").read_text()
    )
    got = fontist.formula_row("aster_sans", doc)
    assert SENTINEL not in repr(got)
    assert set(got) == {
        "key",
        "name",
        "homepage",
        "repository",
        "license_url",
        "spdx_license",
        "open_license",
        "requires_license_agreement",
        "resources",
        "fonts",
    }
    assert (got["open_license"], got["requires_license_agreement"]) == (True, False)
    first = got["fonts"][0]["styles"][0]
    assert set(first) == set(fontist._STYLE_FIELDS)
    assert "copyright" not in first
    assert "version" not in first


@pytest.mark.parametrize(
    ("value", "flag"),
    [("SENTINEL-LICENSE-TEXT", True), ("  ", False), ("", False), (None, False), (False, False)],
)
def test_license_flags(value: object, flag: bool) -> None:
    got = fontist.formula_row("x", {"open_license": value, "requires_license_agreement": value})
    assert (got["open_license"], got["requires_license_agreement"]) == (flag, flag)


def test_yaml_scalars_are_kept_as_text() -> None:
    doc = yaml.safe_load(
        "schema_version: 5\n"
        "fonts:\n"
        "- name: 3270\n"
        "  styles:\n"
        "  - {family_name: 3270, type: 400, full_name: [a], post_script_name: no}\n"
        "resources:\n"
        "  a.zip: {urls: [https://a.synth.example/a.zip, 7], sha256: null, file_size: '12'}\n"
    )
    got = fontist.formula_row("ibm", doc)
    s = got["fonts"][0]["styles"][0]
    assert (got["fonts"][0]["name"], s["family_name"], s["type"]) == ("3270", "3270", "400")
    assert (s["full_name"], s["post_script_name"], s["font"]) == (None, None, None)
    assert got["resources"] == [
        {"name": "a.zip", "urls": ["https://a.synth.example/a.zip", "7"], "sha256": None, "file_size": None}
    ]  # fmt: skip


def test_fetch_reads_only_root_and_sil_and_keeps_no_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = run_fetch(tmp_path, monkeypatch, settings())
    rows = list(snap.iter_jsonl(fontist.FORMULAS_EXTRACT))
    keys = [r["key"] for r in rows]
    assert keys == [
        "aster_sans",
        "birch_mono",
        "cobalt_serif",
        "dune_display",
        "ember_text",
        "hazel_tools",
        "kelp_marks",
        "sil/iris_sil_2.000",
        "sil/iris_sil_2.100",
    ]
    source = snap.load_json(fontist.SOURCE_EXTRACT)
    assert source["skipped"] == [
        "fern_legacy: schema_version 4, expected 5",
        "garnet_broken: unreadable YAML (ParserError)",
    ]
    assert len(source["commit"]) == 40
    assert any(source["commit"] in note for note in snap.manifest.notes)
    assert snap.manifest.fetched == ()  # a git clone, not a fetcher request
    for e in snap.manifest.extracts:
        data = (snap.path / e.path).read_bytes()
        text = (gzip.decompress(data) if e.path.endswith(".gz") else data).decode()
        assert SENTINEL not in text, e.path
    assert (
        snap.path.joinpath(fontist.FORMULAS_EXTRACT).read_bytes()
        == (FIXTURE / "snapshot" / fontist.FORMULAS_EXTRACT).read_bytes()
    )


def test_fetch_fails_when_no_formula_has_the_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(fontist.FormulaSchemaError, match="schema_version 6"):
        run_fetch(tmp_path, monkeypatch, settings(schema_version=6))
    assert Store(tmp_path / "store").snapshot("fontist", DAY) is None


def test_fetch_clones_the_configured_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in mockhttp.git_remotes(FIXTURE / "git", tmp_path / "r", DAY).items():
        monkeypatch.setenv(key, value)
    head = gitsrc.sparse_clone(
        "https://github.com/fontist/formulas", tmp_path / "c", ["/README.md"], ref="main"
    )
    snap = run_fetch(tmp_path / "f", monkeypatch, settings(ref=head))
    assert snap.load_json(fontist.SOURCE_EXTRACT)["commit"] == head


# --- parse: records ---------------------------------------------------------------------------


def test_parse_of_the_fixture() -> None:
    snap = regen.load_snapshot(FIXTURE / "snapshot", "fontist")
    recs = parse(snap)
    unis = {(r.key.key, r.family): r for r in recs if isinstance(r, UniverseRecord)}
    facts = {r.key.key: r for r in recs if isinstance(r, LicenseFact)}
    assert sorted(unis) == [
        ("aster_sans", "Aster Sans"),  # "Aster Sans Light" is a style of it (name ID 16)
        ("birch_mono", "Birch Mono"),
        ("birch_mono", "Birch Mono NL"),
        ("cobalt_serif", "Cobalt Serif"),
        ("dune_display", "Dune Display"),
        ("ember_text", "Ember Text"),
        ("ember_text", "Ember Text Condensed"),
        ("kelp_marks", "Kelp Dingbats"),
        ("kelp_marks", "Kelp Sans"),
        ("kelp_marks", "KelpMath"),
        ("sil/iris_sil_2.000", "Iris SIL"),
        ("sil/iris_sil_2.100", "Iris SIL"),
    ]
    assert "hazel_tools" not in facts  # names no family, so no records at all
    drops = {k: r.drop for k, r in unis.items() if r.drop is not None}
    assert drops == {
        ("cobalt_serif", "Cobalt Serif"): "proprietary",
        ("kelp_marks", "Kelp Sans"): "proprietary",
        ("kelp_marks", "KelpMath"): "math",  # a name code comes before "proprietary"
        ("kelp_marks", "Kelp Dingbats"): "symbol",
    }
    assert unis[("dune_display", "Dune Display")].urls == ()  # "www.dune…" is not a URL
    # Dune Display is named by no other formula, so its missing license id is stated.
    assert facts["dune_display"].raw == "NOASSERTION"
    assert facts["dune_display"].text_url == "http://scripts.synth.example/OFL"
    # Iris SIL 2.100 names no license id, but 2.000 states one for the same family.
    assert "sil/iris_sil_2.100" not in facts
    assert facts["sil/iris_sil_2.000"].spdx == "OFL-1.1-RFN"
    assert all(r.key.ns == "fontist-formula" for r in recs)


def test_family_names_prefer_the_typographic_name_and_keep_order() -> None:
    fonts = [
        {"name": "B", "collection": None, "styles": [style("Beta Light", preferred_family_name="Beta")]},
        {"name": "A", "collection": None, "styles": [style("Alpha"), style("  Beta  ")]},
        {"name": "C", "collection": None, "styles": [style("", preferred_family_name=" ")]},
    ]  # fmt: skip
    assert fontist.family_names(row(fonts=fonts)) == ["Beta", "Alpha"]


def test_archives_go_to_every_family_with_role_other() -> None:
    fonts = [
        {"name": "A", "collection": None, "styles": [style("Alpha")]},
        {"name": "B", "collection": None, "styles": [style("Beta")]},
    ]
    got = fontist.files_by_family(row(fonts=fonts))
    ref = FontFileRef("https://opal.synth.example/Opal.zip", "ab" * 32, 10, "other")
    assert got == {"Alpha": (ref,), "Beta": (ref,)}


def test_a_zip_archive_also_gives_each_family_its_fonts_as_members() -> None:
    fonts = [
        {
            "name": "A",
            "collection": None,
            "styles": [style("Alpha", "Regular", "A-R.ttf"), style("Alpha", "Italic", "A-I.ttf")],
        },
        {"name": "B", "collection": None, "styles": [style("Beta", "Regular", "B.otf")]},
    ]
    got = fontist.files_by_family(row(fonts=fonts))
    archive = FontFileRef("https://opal.synth.example/Opal.zip", "ab" * 32, 10, "other")
    member = "https://opal.synth.example/Opal.zip#"
    assert got == {
        "Alpha": (
            archive,
            FontFileRef(member + "A-I.ttf", role="italic"),
            FontFileRef(member + "A-R.ttf", role="regular"),
        ),
        "Beta": (archive, FontFileRef(member + "B.otf", role="regular")),
    }


def test_direct_font_files_go_to_the_families_that_name_them() -> None:
    fonts = [
        {
            "name": "A",
            "collection": None,
            "styles": [style("Alpha", "Regular", "A-R.ttf"), style("Alpha", "Italic", "A-I.ttf")],
        },
        {"name": "B", "collection": None, "styles": [style("Beta", "Bold", "b-bold.TTF")]},
    ]
    resources = [
        resource("A-R.ttf", "https://x.synth.example/A-R.ttf"),
        resource("A-I.ttf", "https://x.synth.example/A-I.ttf"),
        resource("B-Bold.ttf", "https://x.synth.example/B-Bold.ttf"),
        resource("other.otf", "https://x.synth.example/other.otf"),
    ]
    got = fontist.files_by_family(row(fonts=fonts, resources=resources))
    roles = {fam: [(r.url.rsplit("/", 1)[1], r.role) for r in refs] for fam, refs in got.items()}
    assert roles == {
        "Alpha": [("A-I.ttf", "italic"), ("A-R.ttf", "regular"), ("other.otf", "other")],
        "Beta": [("B-Bold.ttf", "other"), ("other.otf", "other")],
    }


def test_a_lone_font_file_belongs_to_every_style() -> None:
    fonts = [{"name": "D", "collection": None, "styles": [style("Delta", "Regular", "D302.ttf")]}]
    resources = [resource("Delta.ttf", "https://x.synth.example/D.ttf")]
    got = fontist.files_by_family(row(fonts=fonts, resources=resources))
    assert [r.role for r in got["Delta"]] == ["regular"]


def test_collection_styles_take_the_collection_file() -> None:
    fonts = [
        {"name": "E", "collection": "E.ttc", "styles": [style("Eta")]},
        {"name": "F", "collection": "E.ttc", "styles": [style("Eta Cond", "Bold")]},
    ]
    resources = [
        resource("E.ttc", "https://x.synth.example/E.ttc"),
        resource("readme.txt", "https://x.synth.example/readme.txt"),
    ]
    got = fontist.files_by_family(row(fonts=fonts, resources=resources))
    assert [(r.url[-6:], r.role) for r in got["Eta"]] == [
        ("/E.ttc", "regular"),
        ("me.txt", "other"),
    ]
    assert [r.role for r in got["Eta Cond"]] == ["other", "other"]


@pytest.mark.parametrize(
    ("urls", "sha", "want_url", "want_sha"),
    [
        (["http://m.synth.example/a.zip", "https://a.synth.example/a.zip"], "AB" * 32,
         "https://a.synth.example/a.zip", "ab" * 32),
        (["ftp://a.synth.example/a.zip", "http://m.synth.example/a b.zip", "http://m.synth.example/a.zip"],
         ["cd" * 32], "http://m.synth.example/a.zip", "cd" * 32),
        (["https://a.synth.example/a.zip"], ["cd" * 32, "ef" * 32], "https://a.synth.example/a.zip", None),
        (["https://a.synth.example/a.zip"], "not-a-hash", "https://a.synth.example/a.zip", None),
    ],
    ids=["https-first", "skip-bad-urls", "several-hashes", "bad-hash"],
)  # fmt: skip
def test_resource_url_and_hash(urls: list[str], sha: object, want_url: str, want_sha: str) -> None:
    got = fontist.files_by_family(row(resources=[resource("a.zip", *urls, sha=sha)]))
    assert [(r.url, r.sha256) for r in got["Opal Sans"]] == [(want_url, want_sha)]


def test_a_resource_without_a_usable_url_is_left_out() -> None:
    got = fontist.files_by_family(row(resources=[resource("a.zip", "ftp://a.synth.example/a")]))
    assert got == {"Opal Sans": ()}


@pytest.mark.parametrize(
    ("spdx", "rfn"),
    [
        ("OFL-1.1-RFN", True),
        ("OFL-1.1-no-RFN", False),
        ("MIT OR OFL-1.1-no-RFN", False),
        ("(OFL-1.1-RFN)", True),
        ("OFL-1.1-RFN OR OFL-1.1-no-RFN", None),
        ("MIT", None),
        (None, None),
    ],
)
def test_rfn(spdx: str | None, rfn: bool | None) -> None:
    assert fontist._rfn(spdx) is rfn


def test_license_fact() -> None:
    fact = fontist.license_fact(
        "fontist", row(spdx_license=" OFL-1.1-RFN ", license_url="http://l.synth.example/OFL ")
    )
    assert fact == LicenseFact(
        source="fontist",
        key=SourceKey("fontist-formula", "opal_sans"),
        raw="OFL-1.1-RFN",
        spdx="OFL-1.1-RFN",
        text_url="http://l.synth.example/OFL",
        rfn=True,
        attrs=(("open_license", True), ("requires_license_agreement", False)),
    )
    bare = fontist.license_fact("fontist", row(spdx_license=None, license_url="scripts.sil.org"))
    assert (bare.raw, bare.spdx, bare.text_url, bare.rfn) == ("NOASSERTION", None, None, None)


@pytest.mark.parametrize(
    ("open_license", "agreement", "setting", "drop"),
    [
        (False, True, True, "proprietary"),
        (False, True, False, None),
        (True, True, True, None),  # an open license wins over the agreement flag
        (True, False, True, None),
        (False, False, True, None),  # neither flag (the Noto CJK formulas)
    ],
)
def test_proprietary_drop(
    open_license: bool, agreement: bool, setting: bool, drop: str | None
) -> None:
    recs = fontist.formula_records(
        "fontist",
        row(open_license=open_license, requires_license_agreement=agreement),
        settings(agreement_is_proprietary=setting),
    )
    assert [r.drop for r in recs if isinstance(r, UniverseRecord)] == [drop]
    fact = next(r for r in recs if isinstance(r, LicenseFact))
    assert dict(fact.attrs) == {
        "open_license": open_license,
        "requires_license_agreement": agreement,
    }


def test_a_formula_without_families_gives_no_records() -> None:
    assert fontist.formula_records("fontist", row(fonts=[]), settings()) == []


def test_parse_honours_the_setting() -> None:
    snap = regen.load_snapshot(FIXTURE / "snapshot", "fontist")
    recs = parse(snap, settings(agreement_is_proprietary=False))
    drops = {r.family: r.drop for r in recs if isinstance(r, UniverseRecord) and r.drop}
    assert drops == {"KelpMath": "math", "Kelp Dingbats": "symbol"}  # name codes stay


@pytest.mark.parametrize(
    ("family", "code"),
    [
        ("Libertinus Math", "math"),
        ("NewComputerModernSansMath", "math"),
        ("DejaVu Math TeX Gyre", "math"),
        ("Standard Symbols PS", "symbol"),
        ("Wine Symbol", "symbol"),
        ("wx_symbols", "symbol"),
        ("Wingdings 2", "symbol"),
        ("Wine Webdings", "symbol"),
        ("Wine Marlett", "symbol"),
        ("D050000L", "symbol"),
        ("Opal Emoji", "emoji"),
        ("Opal Icons", "icon"),
        ("Opal Barcode", "barcode"),
        ("Opal Musical", "music"),
        ("Opal Sans", None),
        ("Mathilde", None),
        ("Aftermath Serif", None),
        ("Symbolic Sans", None),
    ],
)
def test_drop_names(family: str, code: str | None) -> None:
    assert fontist.drop_code(family, False, settings()) == code


def test_a_license_id_elsewhere_stands_in_for_a_missing_one() -> None:
    """NOASSERTION would void, in the licenses stage's AND, what another formula states."""
    stated_row = row(key="opal_2", spdx_license="OFL-1.1-RFN")
    silent = row(key="opal_3", spdx_license=None)
    stated = fontist.stated_families([stated_row, silent])
    assert stated == {"opalsans"}
    covered = fontist.formula_records("fontist", silent, settings(), stated)
    assert [type(r) for r in covered] == [UniverseRecord]
    fonts = [
        {"name": "O", "collection": None, "styles": [style("OPAL sans")]},
        {"name": "P", "collection": None, "styles": [style("Opal Serif")]},
    ]
    partly = fontist.formula_records(
        "fontist", row(fonts=fonts, spdx_license=None), settings(), stated
    )
    assert [r.raw for r in partly if isinstance(r, LicenseFact)] == ["NOASSERTION"]


@pytest.mark.parametrize(
    "change",
    [
        {"drop_names": (("ornament", "x"),)},
        {"drop_names": (("math", "("),)},
        {"min_share": 1.5},
        {"folders": ()},
        {"folders": ("../google",)},
        {"folders": ("/sil",)},
        {"repository": "http://github.com/fontist/formulas"},
        {"repository": "https://git.synth.example/fontist/formulas"},
    ],
    ids=[
        "bad-code",
        "bad-regex",
        "bad-share",
        "no-folder",
        "parent-folder",
        "absolute-folder",
        "http-repository",
        "other-host",
    ],
)
def test_settings_are_checked(change: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        settings(**change)


def test_fetch_fails_when_the_clone_shrinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = Store(tmp_path / "old")
    with older.writer("fontist", date(2026, 9, 1), 1) as out:
        out.write_jsonl(fontist.FORMULAS_EXTRACT, [{"key": f"f{i}"} for i in range(11)])
    previous = older.snapshot("fontist", date(2026, 9, 1))
    with pytest.raises(ValueError, match="9 formulas, down from 11"):
        run_fetch(tmp_path / "a", monkeypatch, settings(), previous)
    snap = run_fetch(tmp_path / "b", monkeypatch, settings(min_share=0.8), previous)
    assert snap.manifest.extract(fontist.FORMULAS_EXTRACT).rows == 9


# --- the real repository ----------------------------------------------------------------------


@pytest.mark.network
def test_real_formulas(tmp_path: Path) -> None:
    """One real sparse clone of fontist/formulas: the layout and schema still hold."""
    s = settings()
    checkout = tmp_path / "formulas"
    commit = gitsrc.sparse_clone(
        s.repository, checkout, fontist.sparse_patterns(s.folders), ref=s.ref
    )
    assert len(commit) == 40
    files = fontist.formula_files(checkout, s.folders)
    rows, skipped = fontist.read_formulas(files, s.schema_version)
    assert len(rows) >= 200, skipped
    assert not any(k.startswith(("google/", "macos/")) for k, _ in files)
    assert any(r["key"].startswith("sil/") for r in rows)
    stated = fontist.stated_families(rows)
    recs = [r for x in rows for r in fontist.formula_records("fontist", x, s, stated)]
    facts = {r.key.key: r for r in recs if isinstance(r, LicenseFact)}
    assert facts["fira_code"].spdx is not None
    assert facts["fira_code"].spdx.startswith("OFL-1.1")
    families = {r.family for r in recs if isinstance(r, UniverseRecord)}
    assert {"Fira Code", "JetBrains Mono"} <= families
