"""The google_repo collector: the google/fonts repository.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
its fixture (trimmed real families plus synthetic edge cases, see its NOTICE),
plus one real fetch marked ``network``. After changing the fixture's ``git/``
files, rebuild it: ``uv run python -m tests.collectors.google_repo.build_fixture``.
"""

import dataclasses
import gzip
import hashlib
import json
import logging
import os
import shutil
import tomllib
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.collectors.google_repo import build_fixture
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import jsonio
from tff_catalog.collectors.base import ParseContext, load_settings
from tff_catalog.collectors.universe.google_repo import (
    COLLECTOR,
    EXTRACT,
    FONT_FIELDS,
    METADATA_FIELDS,
    REPO_EXTRACT,
    Settings,
    _utc_date,
    check_shrink,
    drop_code,
    family_urls,
    file_role,
    is_heading,
    license_facts,
    list_entries,
    list_statuses,
    pending_entries,
    pinned_url,
    read_metadata,
    reserved_font_name,
    shared_folder_names,
    tree_files,
    universe_record,
    unknown_headings,
)
from tff_catalog.config_model import ConfigError
from tff_catalog.paths import Paths
from tff_catalog.records import LicenseFact, Record, UniverseRecord
from tff_catalog.store import Snapshot

FIXTURE = build_fixture.FIXTURE
REPO_FILES = build_fixture.GIT / build_fixture.REPO
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
COMMIT = SNAPSHOT.load_json(REPO_EXTRACT)["commit"]
PINNED = f"https://raw.githubusercontent.com/google/fonts/{COMMIT}"
LOG = logging.getLogger("tests.google_repo")


def parse(snapshot: Snapshot = SNAPSHOT, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def universe(recs: list[Record]) -> dict[str, UniverseRecord]:
    return {r.key.key: r for r in recs if isinstance(r, UniverseRecord)}


def facts(recs: list[Record]) -> dict[str, list[LicenseFact]]:
    out: dict[str, list[LicenseFact]] = {}
    for r in recs:
        if isinstance(r, LicenseFact):
            out.setdefault(r.key.key, []).append(r)
    return out


def rows(snapshot: Snapshot = SNAPSHOT) -> list[dict[str, Any]]:
    return list(snapshot.iter_jsonl(EXTRACT))


def roles(r: UniverseRecord) -> list[tuple[str, str]]:
    return [(f.url.rsplit("/", 1)[1], f.role) for f in r.files]


@pytest.fixture
def git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Serve the fixture's git/ as github.com; returns the scratch directory to fetch into."""
    for key, value in build_fixture.git_env(tmp_path / "git").items():
        monkeypatch.setenv(key, value)
    return tmp_path


# --- parse ------------------------------------------------------------------------------------


def test_one_record_per_readable_family_folder() -> None:
    recs = universe(parse())
    assert sorted(recs) == [
        "astersans",
        "birchmono",
        "claritycity",
        "cobaltsymbols",
        "dunesans",
        "embercolor",
        "jetbrainsmono",
        "notosansnko_todelist",
        "robotoslab",
        "ubuntu",
        "ubuntusans",
    ]  # not ofl/brokenfam (unreadable) nor ofl/orphanfolder (no METADATA.pb)
    assert {r.key.ns for r in recs.values()} == {"gf-dir"}
    assert dict(recs["robotoslab"].attrs)["folder"] == "apache"
    assert dict(recs["ubuntu"].attrs)["folder"] == "ufl"


def test_metadata_facts_of_a_real_family() -> None:
    jb = universe(parse())["jetbrainsmono"]
    assert (jb.family, jb.category, jb.is_monospace, jb.variable) == (
        "JetBrains Mono",
        "MONOSPACE",
        True,
        True,
    )
    assert jb.added == date(2020, 11, 18)
    assert jb.subsets == ("cyrillic", "cyrillic-ext", "greek", "latin", "latin-ext", "vietnamese")
    assert jb.status == "live"  # listed under "# Upgrade", so already live
    assert jb.urls == (
        ("repository", "https://github.com/JetBrains/JetBrainsMono"),
        ("minisite", "https://www.jetbrains.com/lp/mono/"),
        ("license", f"{PINNED}/ofl/jetbrainsmono/OFL.txt"),
    )
    assert dict(jb.attrs) == {"axes": "wght", "folder": "ofl"}


def test_font_files_are_commit_pinned_with_tree_sizes() -> None:
    jb = universe(parse())["jetbrainsmono"]
    assert [f.url for f in jb.files] == [
        f"{PINNED}/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
        f"{PINNED}/ofl/jetbrainsmono/JetBrainsMono-Italic%5Bwght%5D.ttf",
    ]
    assert [f.role for f in jb.files] == ["variable", "italic"]
    on_disk = REPO_FILES / "ofl" / "jetbrainsmono" / "JetBrainsMono[wght].ttf"
    assert jb.files[0].size == on_disk.stat().st_size
    assert all(f.sha256 is None for f in jb.files)  # blobs are never downloaded


def test_static_roles_and_only_metadata_fonts() -> None:
    recs = universe(parse())
    assert roles(recs["ubuntu"]) == [
        ("Ubuntu-Light.ttf", "other"),
        ("Ubuntu-Regular.ttf", "regular"),
        ("Ubuntu-Italic.ttf", "italic"),
    ]  # Ubuntu-Bold.ttf is in the folder but not in METADATA.pb
    assert roles(recs["astersans"]) == [
        ("AsterSans-Regular.ttf", "regular"),
        ("AsterSans-Italic.ttf", "italic"),
        ("AsterSans-Bold.ttf", "other"),
    ]  # static/AsterSans-Light.ttf is not served


def test_synthetic_edge_cases() -> None:
    aster = universe(parse())["astersans"]
    assert aster.category == "SANS_SERIF"  # category: DISPLAY, then SANS_SERIF; the last counts
    assert aster.display_name == "Äster Sans"
    assert aster.names == (("Aster Grotesk", "rename"),)
    assert aster.latin_languages == 2  # de_Latn, en_Latn; ru_Cyrl is not Latin
    assert aster.subsets == ("cyrillic", "latin")
    assert aster.variable is False


@pytest.mark.parametrize(
    ("category", "classifications", "want"),
    [
        (["MONOSPACE", "SANS_SERIF"], [], False),  # only the last category counts
        (["SANS_SERIF", "MONOSPACE"], [], True),  # Inconsolata, Martian Mono
        (["MONOSPACE", "SANS_SERIF"], ["MONOSPACE"], True),  # Sono
        (["SANS_SERIF"], ["DISPLAY"], False),
    ],
)
def test_monospace_follows_the_last_category(
    category: list[str], classifications: list[str], want: bool
) -> None:
    row = {
        "dir": "ofl/x",
        "metadata": {"name": "X", "category": category, "classifications": classifications},
        "files": [],
        "license_file": None,
    }
    got = universe_record(row, SETTINGS, owner_repo="google/fonts", commit="c", statuses={})
    assert (got.category, got.is_monospace) == (category[-1], want)


def test_the_hg_mirror_is_never_a_link() -> None:
    recs = universe(parse())
    for key in ("astersans", "ubuntu"):
        assert all(role != "repository" for role, _ in recs[key].urls)
        assert dict(recs[key].attrs)["hg_mirror"] is True
    assert "hg_mirror" not in dict(recs["jetbrainsmono"].attrs)


def test_family_urls_keep_only_web_addresses() -> None:
    row = {
        "dir": "ofl/x",
        "metadata": {
            "source": {"repository_url": "git@github.com:synth/x.git"},
            "minisite_url": " https://x.synth.example/ ",
        },
        "license_file": {"name": "OFL.txt"},
    }
    assert family_urls(row, "google/fonts", "c" * 40) == (
        ("minisite", "https://x.synth.example/"),  # trimmed
        ("license", f"https://raw.githubusercontent.com/google/fonts/{'c' * 40}/ofl/x/OFL.txt"),
    )  # an ssh remote is no link
    row["metadata"] = {"source": {"repository_url": "https://github.com/synth/x y"}}
    row["license_file"] = None
    assert family_urls(row, "google/fonts", "c") == ()


def test_monospace_from_a_classification_and_a_file_missing_from_the_tree() -> None:
    birch = universe(parse())["birchmono"]
    assert (birch.category, birch.is_monospace) == ("SANS_SERIF", True)
    assert [f.size for f in birch.files][1] is None  # BirchMono-Italic[wght].ttf is not in the tree
    assert birch.urls == (
        ("repository", "https://www.github.com/synth/birchmono"),
        ("minisite", "http://birch.synth.example/"),
    )  # as written; no license text in the folder


def test_statuses_from_the_lists_and_todelist_folders() -> None:
    status = {k: r.status for k, r in universe(parse()).items()}
    assert status == {
        "astersans": "live",
        "birchmono": "live",
        "claritycity": "queued",
        "cobaltsymbols": "deprecated",
        "dunesans": "queued",
        "embercolor": "live",
        "jetbrainsmono": "live",
        "notosansnko_todelist": "deprecated",
        "robotoslab": "live",
        "ubuntu": "live",
        "ubuntusans": "live",
    }  # embercolor is under "# Metadata / Description / License", after "# New"


def test_drop_codes() -> None:
    drops = {k: r.drop for k, r in universe(parse()).items() if r.drop}
    assert drops == {"cobaltsymbols": "symbol", "embercolor": "emoji"}


@pytest.mark.parametrize(
    ("name", "subsets", "classifications", "want"),
    [
        ("Libre Barcode 39 Text", ["latin"], [], "barcode"),
        ("Noto Sans Math", ["math"], [], "math"),
        ("Noto Music", ["music"], [], "music"),
        ("Material Symbols Outlined", ["latin"], ["SYMBOLS"], "icon"),  # the name comes first
        ("Noto Emoji", ["emoji"], [], "emoji"),
        ("Noto Color Emoji", ["emoji", "menu"], ["SYMBOLS"], "emoji"),
        ("Mathematica Sans", ["latin"], [], None),  # whole words only
        ("Emberly", ["emoji", "latin"], [], None),  # emoji is not its only subset
    ],
)
def test_drop_code_rules(
    name: str, subsets: list[str], classifications: list[str], want: str | None
) -> None:
    meta = {"name": name, "subsets": subsets, "classifications": classifications}
    assert drop_code(meta, SETTINGS) == want


def test_display_name_only_when_it_differs() -> None:
    row = {"dir": "ofl/x", "metadata": {"name": "X Sans", "display_name": "X Sans"}}
    got = universe_record(row, SETTINGS, owner_repo="google/fonts", commit="c", statuses={})
    assert (got.family, got.display_name, got.files, got.urls) == ("X Sans", None, (), ())


def test_noto_script_and_languages() -> None:
    nko = universe(parse())["notosansnko_todelist"]
    assert (nko.family, nko.display_name, nko.primary_script) == (
        "Noto Sans N Ko",
        "Noto Sans N'Ko",
        "Nkoo",
    )
    assert nko.latin_languages == 0
    assert nko.subsets == ("nko",)
    assert dict(nko.attrs)["is_noto"] is True


def test_license_facts_carry_the_pinned_text_and_its_hash() -> None:
    found = facts(parse())
    assert set(found) == set(universe(parse()))
    assert all(len(v) == 1 for v in found.values())  # METADATA.pb agrees with the folder
    jb = found["jetbrainsmono"][0]
    text = (REPO_FILES / "ofl" / "jetbrainsmono" / "OFL.txt").read_bytes()
    assert (jb.raw, jb.spdx, jb.rfn) == ("OFL", None, False)
    assert jb.text_url == f"{PINNED}/ofl/jetbrainsmono/OFL.txt"
    assert jb.text_sha256 == hashlib.sha256(text).hexdigest()
    assert found["astersans"][0].rfn is True  # "with Reserved Font Name" in its header
    slab = found["robotoslab"][0]
    assert (slab.raw, slab.rfn, slab.text_url) == (
        "APACHE2",
        None,
        f"{PINNED}/apache/robotoslab/LICENSE.txt",
    )
    assert found["ubuntu"][0].raw == "UFL"
    birch = found["birchmono"][0]
    assert (birch.text_url, birch.text_sha256, birch.rfn) == (None, None, None)


def test_a_licence_txt_is_the_license_text() -> None:
    """ufl/ubuntusans and ufl/ubuntusansmono carry only LICENCE.txt (British spelling)."""
    fact = facts(parse())["ubuntusans"][0]
    text = (REPO_FILES / "ufl" / "ubuntusans" / "LICENCE.txt").read_bytes()
    assert (fact.raw, fact.rfn) == ("UFL", None)
    assert fact.text_url == f"{PINNED}/ufl/ubuntusans/LICENCE.txt"
    assert fact.text_sha256 == hashlib.sha256(text).hexdigest()
    sans = universe(parse())["ubuntusans"]
    assert ("license", f"{PINNED}/ufl/ubuntusans/LICENCE.txt") in sans.urls
    assert [f.url for f in sans.files] == [
        f"{PINNED}/ufl/ubuntusans/UbuntuSans%5Bwdth,wght%5D.ttf",
        f"{PINNED}/ufl/ubuntusans/UbuntuSans-Italic%5Bwdth,wght%5D.ttf",
    ]
    assert (sans.variable, dict(sans.attrs)["axes"]) == (True, "wdth,wght")


def test_the_folder_claims_its_license_when_metadata_disagrees() -> None:
    row = {"dir": "apache/x", "metadata": {"license": "OFL"}, "license_file": None}
    got = license_facts(row, owner_repo="google/fonts", commit="c" * 40)
    assert [f.raw for f in got] == ["OFL", "apache"]
    row["metadata"] = {"license": None}
    assert [f.raw for f in license_facts(row, owner_repo="g/f", commit="c")] == ["apache"]


def test_parse_is_defensive_about_odd_rows(tmp_path: Path) -> None:
    good = next(r for r in rows() if r["dir"] == "ofl/astersans")
    odd = [
        {"dir": "noslash", "metadata": good["metadata"]},
        {"dir": "ofl/nameless", "metadata": {"name": ""}},
        {"metadata": good["metadata"]},
        "not a row",
        good,
        good,  # repeated: kept once
    ]
    snap = _snapshot_with(tmp_path, odd)
    recs = parse(snap)
    assert [r.key.key for r in recs if isinstance(r, UniverseRecord)] == ["astersans"]


def test_parse_needs_its_settings() -> None:
    ctx = ParseContext(snapshot=SNAPSHOT, settings=object(), log=LOG)
    with pytest.raises(TypeError, match="Settings"):
        list(COLLECTOR.parse(ctx))


# --- the pending lists ------------------------------------------------------------------------


def test_list_entries_follow_headings_not_prose() -> None:
    text = (REPO_FILES / "to_sandbox.txt").read_text()
    assert list(list_entries(text, ("ofl",))) == [
        ("upgrade", "ofl/astersans"),
        ("new", "ofl/dunesans"),
        ("new", "ofl/dunesans"),  # a deeper path names its family folder
        ("metadata / description / license", "ofl/embercolor"),  # gftools' heading
    ]  # the prose comment and the commented-out ofl/birchmono change nothing
    prod = (REPO_FILES / "to_production.txt").read_text()
    assert list(list_entries(prod, ("ofl", "apache", "ufl"))) == [
        ("new", "ofl/claritycity"),
        ("upgrade", "ofl/jetbrainsmono"),
    ]  # "# Deleted: ofl/oldfamily" is no entry; catalog/designers/... no license folder


@pytest.mark.parametrize(
    ("words", "want"),
    [
        ("New", True),
        ("Designer profile", True),
        ("Metadata / Description / License", True),  # gftools' own, though not plain words
        ("metadata / description / license", True),
        ("Some Future Section", False),  # not gftools': a note (the fetch lists its line)
        ("Held back for now", False),
        ("Deleted: ofl/oldfamily # https://github.com/google/fonts/pull/1", False),
        ("ofl/birchmono # https://github.com/google/fonts/pull/10004", False),
        ("A note on why ofl/birchmono is held back: its registry overrides were", False),
        ("removed, so the entry is commented out below.", False),
    ],
)
def test_headings(words: str, want: bool) -> None:
    assert is_heading(words) is want


def test_pending_entries_keep_only_family_entries() -> None:
    text = "# New\nofl/a # https://x/pull/1\nofl/a/METADATA.pb\n# Upgrade\nofl/a\n"
    assert pending_entries(text, ("ofl",)) == [
        {"section": "new", "dir": "ofl/a"},  # repeated in one section: kept once
        {"section": "upgrade", "dir": "ofl/a"},
    ]


def test_a_short_note_inside_new_keeps_the_section() -> None:
    """A hand-written note is no heading, so the new families after it stay queued."""
    text = "# New\nofl/a # u\n# Held back for now\n# ofl/b # u\nofl/c # u\n# TODO\nofl/d\n"
    assert list(list_entries(text, ("ofl",))) == [
        ("new", "ofl/a"),
        ("new", "ofl/c"),
        ("new", "ofl/d"),
    ]
    pending = {"to_production.txt": pending_entries(text, ("ofl",))}
    assert list_statuses(pending, ("new",)) == dict.fromkeys(("ofl/a", "ofl/c", "ofl/d"), "queued")
    assert unknown_headings(text) == [3, 6]  # "# ofl/b # u" is a commented-out entry


def test_unknown_headings_skip_gftools_headings_prose_and_entries() -> None:
    text = (REPO_FILES / "to_sandbox.txt").read_text() + "\n# Future Section\nofl/x\n"
    lines = text.splitlines()
    assert [lines[n - 1] for n in unknown_headings(text)] == ["# Future Section"]


def test_delisting_wins_over_queueing() -> None:
    lists = {
        "to_production.txt": "# New\nofl/a\nofl/b\n",
        "to_sandbox.txt": "ofl/c\n# New\nofl/d\n",  # ofl/c comes before any heading
        "to_delist.txt": "ofl/b # replaced\n",
    }
    pending = {name: pending_entries(text, ("ofl",)) for name, text in lists.items()}
    got = list_statuses(pending, ("new",))
    assert got == {"ofl/a": "queued", "ofl/b": "deprecated", "ofl/d": "queued"}


def test_list_statuses_skip_malformed_entries() -> None:
    pending = {
        "to_production.txt": [{"section": "new", "dir": "ofl/a"}, {"dir": "ofl/b"}, "ofl/c"],
        "to_sandbox.txt": "not a list",
    }
    assert list_statuses(pending, ("new",)) == {"ofl/a": "queued"}


# --- fetch helpers ----------------------------------------------------------------------------


OFL_BODY = "This Font Software is licensed under the SIL Open Font License, Version 1.1.\n"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (f"Copyright 2020 The X Authors\n\n{OFL_BODY}Reserved Font Name: defined here\n", False),
        (f'Copyright 2020 X, with Reserved Font Name "X".\n\n{OFL_BODY}', True),
        ('Copyright X, with Reserved Font Names "A" and\n"B".\nThis Font Software is licensed'
         "\nunder the SIL Open Font License,\nVersion 1.1.", True),
        ("Apache License\nVersion 2.0, January 2004\n", None),
    ],
    ids=["body-only", "header", "wrapped", "not-ofl"],
)  # fmt: skip
def test_reserved_font_name_reads_only_the_header(text: str, want: bool | None) -> None:
    assert reserved_font_name(text) is want


def test_read_metadata_keeps_only_the_listed_fields() -> None:
    text = (REPO_FILES / "ofl" / "birchmono" / "METADATA.pb").read_text()
    meta, unknown = read_metadata(text)
    assert unknown is True  # FontProto.position is newer than gfmetadata
    assert tuple(meta) == METADATA_FIELDS
    assert tuple(meta["fonts"][0]) == FONT_FIELDS
    assert meta["axes"] == [{"tag": "wght", "min": 200.5, "max": 700.0}]
    assert "designer" not in meta
    assert "copyright" not in meta["fonts"][0]
    meta, unknown = read_metadata((REPO_FILES / "ufl" / "ubuntu" / "METADATA.pb").read_text())
    assert unknown is False
    assert meta["category"] == ["SANS_SERIF"]
    with pytest.raises(ValueError, match="Expected"):
        read_metadata((REPO_FILES / "ofl" / "brokenfam" / "METADATA.pb").read_text())


def test_axis_bounds_are_written_as_given() -> None:
    meta, _ = read_metadata(
        'name: "X"\naxes {\n  tag: "opsz"\n  min_value: 0.1\n  max_value: 14.4\n}\n'
    )
    assert meta["axes"] == [{"tag": "opsz", "min": 0.1, "max": 14.4}]  # not 0.10000000149


def test_extract_rows_hold_no_designer_or_copyright() -> None:
    raw = gzip.decompress((FIXTURE / "snapshot" / EXTRACT).read_bytes()).decode()
    for word in ("designer", "copyright", "Synth Type Foundry"):
        assert word not in raw
    for row in rows():
        assert set(row) == {"dir", "metadata", "unknown_fields", "license_file", "files"}
        assert set(row["metadata"]) == set(METADATA_FIELDS)  # keys are sorted in the file


def test_tree_files_keeps_font_blobs_by_family() -> None:
    doc = {
        "tree": [
            {"path": "inter", "type": "tree", "sha": "t"},
            {"path": "inter/Inter[opsz,wght].ttf", "type": "blob", "sha": "b1", "size": 876576},
            {"path": "inter/OFL.txt", "type": "blob", "sha": "b2", "size": 4400},
            {"path": "inter/static/Inter-Bold.TTF", "type": "blob", "sha": "b3", "size": "big"},
            {"path": "README.md", "type": "blob", "sha": "b4", "size": 10},
            "junk",
        ]
    }
    assert tree_files(doc, (".ttf", ".otf")) == {
        "inter": [
            {"path": "Inter[opsz,wght].ttf", "blob": "b1", "size": 876576},
            {"path": "static/Inter-Bold.TTF", "blob": "b3", "size": None},
        ]
    }


def test_pinned_urls_encode_brackets_and_keep_commas() -> None:
    assert (
        pinned_url("google/fonts", "abc", "ofl/inter/Inter[opsz,wght].ttf")
        == "https://raw.githubusercontent.com/google/fonts/abc/ofl/inter/Inter%5Bopsz,wght%5D.ttf"
    )


@pytest.mark.parametrize(
    ("font", "role"),
    [
        ({"style": "normal", "weight": 400, "filename": "A[wght].ttf"}, "variable"),
        ({"style": "italic", "weight": 400, "filename": "A-Italic[wght].ttf"}, "italic"),
        ({"style": "normal", "weight": 400, "filename": "A-Regular.ttf"}, "regular"),
        ({"style": "normal", "weight": 700, "filename": "A-Bold.ttf"}, "other"),
        ({"style": "italic", "weight": 700, "filename": "A-BoldItalic.ttf"}, "italic"),
    ],
)
def test_file_roles(font: dict[str, Any], role: str) -> None:
    assert file_role(font) == role


# --- fetch, offline ---------------------------------------------------------------------------


def test_fetch_reproduces_the_fixture_snapshot(git_env: Path) -> None:
    snap = build_fixture.run_fetch(git_env)
    for name in (EXTRACT, REPO_EXTRACT):
        assert (snap.path / name).read_bytes() == (FIXTURE / "snapshot" / name).read_bytes()
    m = snap.manifest
    assert m.data_date == build_fixture.DAY  # the commit's date
    assert len(m.fetched) == 5
    assert all(f.url.startswith("https://api.github.com/") for f in m.fetched)
    assert m.fetched[0].url.endswith(f"/git/commits/{COMMIT}")
    assert sum(f.url.endswith("?recursive=1") for f in m.fetched) == 3
    assert m.notes[0].endswith(f"at {COMMIT}")
    assert any("unreadable, family skipped: ofl/brokenfam" in n for n in m.notes)
    assert any("unknown to gfmetadata skipped in: ofl/birchmono" in n for n in m.notes)
    assert any(
        "not in the tree: 1 (ofl/birchmono/BirchMono-Italic[wght].ttf)" in n for n in m.notes
    )
    repo = snap.load_json(REPO_EXTRACT)
    assert repo["unparsed"][0]["dir"] == "ofl/brokenfam"
    assert sorted(repo["pending"]) == ["to_delist.txt", "to_production.txt", "to_sandbox.txt"]
    assert m.extract(EXTRACT).rows == 11
    assert not any("push lists missing" in n or "two license folders" in n for n in m.notes)


def test_extract_keeps_no_list_comments_or_links() -> None:
    raw = (FIXTURE / "snapshot" / REPO_EXTRACT).read_text()
    for word in ("pull/", "catalog/designers", "held back", "oldfamily", "birchmono"):
        assert word not in raw  # prose, PR links, designer profiles, commented-out entries


def test_fetch_notes_a_truncated_tree(git_env: Path) -> None:
    http = git_env / "http"
    shutil.copytree(build_fixture.HTTP, http)
    doc = json.loads((http / "tree-ofl.json").read_text())
    doc["truncated"] = True
    doc["tree"] = [e for e in doc["tree"] if not e["path"].startswith("jetbrainsmono/")]
    (http / "tree-ofl.json").write_bytes(jsonio.pretty_bytes(doc))
    snap = build_fixture.run_fetch(git_env, http)
    assert snap.load_json(REPO_EXTRACT)["truncated"] == ["ofl"]
    assert any("truncated" in n for n in snap.manifest.notes)
    jb = universe(parse(snap))["jetbrainsmono"]
    assert [f.size for f in jb.files] == [None, None]


def _fetch_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[Path], None]
) -> Snapshot:
    """Fetch offline from a copy of the fixture repository that ``change`` edited."""
    git = tmp_path / "git"
    shutil.copytree(build_fixture.GIT, git)
    change(git / build_fixture.REPO)
    env = mockhttp.git_remotes(git, tmp_path / "remotes", build_fixture.DAY)
    for key, value in (env | {"GITHUB_TOKEN": build_fixture.TOKEN}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    build_fixture.write_http(tmp_path / "remotes" / build_fixture.REPO, tmp_path / "http")
    return build_fixture.run_fetch(tmp_path, tmp_path / "http")


def test_fetch_notes_a_missing_list_and_a_folder_in_two_license_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A license move caught halfway keeps both rows, under one gf-dir key."""

    def change(fonts: Path) -> None:
        (fonts / "to_delist.txt").unlink()
        shutil.copytree(fonts / "ofl" / "jetbrainsmono", fonts / "apache" / "jetbrainsmono")
        meta = fonts / "apache" / "jetbrainsmono" / "METADATA.pb"
        meta.write_text(meta.read_text().replace('license: "OFL"', 'license: "APACHE2"'))

    snap = _fetch_changed(tmp_path, monkeypatch, change)
    notes = snap.manifest.notes
    assert any("push lists missing" in n and "to_delist.txt" in n for n in notes)
    assert any(n.endswith("(one gf-dir key): ['jetbrainsmono']") for n in notes)
    recs = parse(snap)
    jb = [r for r in recs if isinstance(r, UniverseRecord) and r.key.key == "jetbrainsmono"]
    assert sorted(dict(r.attrs)["folder"] for r in jb) == ["apache", "ofl"]
    assert sorted(f.raw for f in facts(recs)["jetbrainsmono"]) == ["APACHE2", "OFL"]
    assert universe(recs)["cobaltsymbols"].status == "live"  # no to_delist.txt now


def test_shared_folder_names() -> None:
    rows = [{"dir": "apache/a"}, {"dir": "ofl/a"}, {"dir": "ofl/b"}, {"dir": "ufl/a"}]
    assert shared_folder_names(rows) == ["a"]


def test_fetch_notes_unknown_headings_by_line_and_keeps_the_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def change(fonts: Path) -> None:
        prod = fonts / "to_production.txt"
        prod.write_text(prod.read_text().replace("# New\n", "# New\n# Held back for now\n"))

    snap = _fetch_changed(tmp_path, monkeypatch, change)
    notes = [n for n in snap.manifest.notes if "shaped like a heading" in n]
    assert len(notes) == 1
    assert notes[0].startswith("to_production.txt:")
    assert notes[0].endswith("lines [2]")
    assert "Held back" not in snap.path.joinpath(REPO_EXTRACT).read_text()  # no comment text
    assert universe(parse(snap))["claritycity"].status == "queued"


def test_fetch_fails_when_a_license_folder_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="has no ufl/"):
        _fetch_changed(tmp_path, monkeypatch, lambda fonts: shutil.rmtree(fonts / "ufl"))
    assert not (tmp_path / "store" / COLLECTOR.name / build_fixture.DAY.isoformat()).exists()


def test_check_shrink_refuses_an_empty_clone() -> None:
    with pytest.raises(ValueError, match="no family folder"):
        check_shrink(0, None, 0.9)
    check_shrink(90, 100, 0.9)  # exactly min_share passes
    with pytest.raises(ValueError, match="down from 100"):
        check_shrink(89, 100, 0.9)


@pytest.mark.parametrize(
    ("stamp", "day"),
    [
        ("2026-09-24T09:58:38Z", date(2026, 9, 24)),
        ("2026-09-24T22:30:00-05:00", date(2026, 9, 25)),  # the UTC day, not the local one
        ("2026-09-24T09:58:38", date(2026, 9, 24)),  # no offset: read as UTC
    ],
)
def test_the_data_date_is_the_commits_utc_day(stamp: str, day: date) -> None:
    assert _utc_date(stamp) == day


def test_fetch_refuses_a_clone_that_shrank(git_env: Path) -> None:
    entries = tuple(
        dataclasses.replace(e, rows=100) if e.path == EXTRACT else e
        for e in SNAPSHOT.manifest.extracts
    )
    previous = dataclasses.replace(
        SNAPSHOT, manifest=dataclasses.replace(SNAPSHOT.manifest, extracts=entries)
    )
    with pytest.raises(ValueError, match="down from 100"):
        build_fixture.run_fetch(git_env, previous=previous)
    assert not (git_env / "store" / COLLECTOR.name / build_fixture.DAY.isoformat()).exists()


def test_fetch_accepts_a_small_shrink(git_env: Path) -> None:
    entries = tuple(
        dataclasses.replace(e, rows=12) if e.path == EXTRACT else e
        for e in SNAPSHOT.manifest.extracts
    )
    previous = dataclasses.replace(
        SNAPSHOT, manifest=dataclasses.replace(SNAPSHOT.manifest, extracts=entries)
    )
    assert build_fixture.run_fetch(git_env, previous=previous).manifest.complete


# --- settings ---------------------------------------------------------------------------------


def test_settings_file_matches_the_defaults() -> None:
    path = ROOT / "config" / "sources" / f"{COLLECTOR.name}.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    names = {f.name for f in dataclasses.fields(Settings)}
    assert set(data) == names  # every setting is spelled out in the file
    assert Settings() == SETTINGS
    assert SETTINGS.owner_repo == "google/fonts"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"repository": "https://gitlab.com/google/fonts"}, "repository"),
        ({"repository": "https://github.com/google/fonts.git"}, "repository"),
        ({"ref": "--upload-pack=x"}, "ref"),
        ({"folders": ("ofl", "cc-by-sa")}, "folders"),
        ({"folders": ()}, "folders"),
        ({"folders": ("ofl", "ofl")}, "folders"),
        ({"font_extensions": ("ttf",)}, "font_extensions"),
        ({"queued_sections": ("New",)}, "queued_sections"),
        ({"min_share": 1.5}, "min_share"),
        ({"drop_names": (("glyph", "x"),)}, "drop_names"),
        ({"drop_names": (("icon", "("),)}, "bad pattern"),
    ],
)
def test_settings_are_checked(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings(**changes)


def test_queued_sections_may_name_any_gftools_heading() -> None:
    got = Settings(queued_sections=("new", "metadata / description / license"))
    assert got.queued_sections == ("new", "metadata / description / license")


# --- helpers ----------------------------------------------------------------------------------


def _snapshot_with(tmp: Path, items: list[Any]) -> Snapshot:
    """The fixture snapshot with ``families.jsonl.gz`` replaced by ``items``."""
    from tff_catalog.store import Store

    store = Store(tmp / "store")
    day = SNAPSHOT.date
    with store.writer(COLLECTOR.name, day, COLLECTOR.version) as w:
        w.write_bytes(REPO_EXTRACT, (FIXTURE / "snapshot" / REPO_EXTRACT).read_bytes())
        w.write_jsonl(EXTRACT, items)
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    return snap


# --- the real repository ------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real fetch of github.com/google/fonts (about 5 MB clone, five API requests),
    then two small reads that show the commit-pinned URLs resolve."""
    from tff_catalog import clock
    from tff_catalog.collectors.base import FetchContext
    from tff_catalog.fetch import Fetcher
    from tff_catalog.store import RawDir, Store

    day = clock.utc_today()
    store = Store(tmp_path / "store")
    with Fetcher(log=LOG) as fetcher:
        with store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer:
            COLLECTOR.fetch(
                FetchContext(
                    run_date=day,
                    fetcher=fetcher.scoped(COLLECTOR.hosts),
                    out=writer,
                    raw=RawDir(tmp_path / "raw"),
                    previous=None,
                    settings=SETTINGS,
                    log=LOG,
                )
            )
        snap = store.snapshot(COLLECTOR.name, day)
        assert snap is not None
        assert snap.manifest.extract(EXTRACT).rows > 1900
        recs = parse(snap)
        fams = universe(recs)
        inter = fams["inter"]
        assert (inter.family, inter.variable, inter.status) == ("Inter", True, "live")
        commit = snap.load_json(REPO_EXTRACT)["commit"]
        assert inter.files[0].url.startswith(
            f"https://raw.githubusercontent.com/google/fonts/{commit}/"
        )
        assert "%5B" in inter.files[0].url
        assert (inter.files[0].size or 0) > 100_000
        assert fams["jetbrainsmono"].is_monospace is True
        lic = facts(recs)["inter"][0]
        assert lic.text_url is not None
        assert os.environ.get("GIT_ALLOW_PROTOCOL") != "file"  # really went to github.com
        raw = fetcher.scoped(("raw.githubusercontent.com",))
        text = raw.get(lic.text_url)
        assert text.sha256 == lic.text_sha256  # the pinned license text is the one hashed
        head = raw.get_range(inter.files[0].url, 0, 3)  # the bracketed name resolves
        assert (head.status, head.header("content-range")) == (
            206,
            f"bytes 0-3/{inter.files[0].size}",
        )
