"""The homebrew_casks collector: Homebrew's font casks and their renames.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the trimmed real fixture (ruling T1) and on small hand-made casks, plus one
light real fetch marked ``network``.
"""

import gzip
import json
import logging
import re
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock, jsonio
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.universe.homebrew_casks import (
    API_HOST,
    CASKS_EXTRACT,
    COLLECTOR,
    EXTRACT_FIELDS,
    RAW_HOST,
    RENAMES_EXTRACT,
    TAP_MIGRATIONS_EXTRACT,
    Settings,
    check_shrink,
    distinct_names,
    drop_code,
    extract_casks,
    extract_row,
    file_role,
    font_entries,
    font_files,
    gf_dir,
    modified_day,
    old_names,
    record_attrs,
    rename_target,
    renamed_to,
    split_build,
    status,
    universe_record,
)
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import FontFileRef, SourceKey, UniverseRecord
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
BUILD = re.compile(SETTINGS.build_pattern)
LOG = logging.getLogger("tests.homebrew_casks")
SHA = "6f6376c6ed2960ea8a963cd7387ec9d76e3f629125bc33d1fdcd7eb7012f7bbf"


def fixture_casks() -> dict[str, dict[str, Any]]:
    doc = json.loads(gzip.decompress((HTTP / "cask.json.gz").read_bytes()))
    return {c["token"]: c for c in doc}


def parse(snapshot: Snapshot, settings: object = SETTINGS) -> dict[str, UniverseRecord]:
    recs = regen.parse_records(COLLECTOR, snapshot, settings)
    assert all(isinstance(r, UniverseRecord) for r in recs)
    return {r.key.key: r for r in recs if isinstance(r, UniverseRecord)}


def cask(token: str, **fields: Any) -> dict[str, Any]:
    """A minimal cask object as ``cask.json`` has it."""
    return {
        "token": token,
        "tap": "homebrew/cask",
        "name": [token.removeprefix("font-").title()],
        "homepage": "https://fonts.synth.example/",
        "url": f"https://fonts.synth.example/{token}.zip",
        "url_specs": {},
        "version": "1.0",
        "sha256": SHA,
        "old_tokens": [],
        "deprecated": False,
        "disabled": False,
        "artifacts": [{"font": [f"{token}-Regular.ttf"], "target": "/x"}],
        **fields,
    }


def row(token: str, **fields: Any) -> dict[str, Any]:
    return extract_row(cask(token, **fields))


def write_snapshot(
    tmp: Path, rows: list[object], renames: object = None, migrations: object = None
) -> Snapshot:
    """A complete snapshot of hand-made extracts."""
    with Store(tmp / "store").writer(COLLECTOR.name, DAY, COLLECTOR.version) as out:
        out.write_jsonl(CASKS_EXTRACT, rows)
        if renames is not None:
            out.write_json(RENAMES_EXTRACT, renames)
        if migrations is not None:
            out.write_json(TAP_MIGRATIONS_EXTRACT, migrations)
    snap = Store(tmp / "store").snapshot(COLLECTOR.name, DAY)
    assert snap is not None
    return snap


def fetch(
    tmp: Path, http: Path = HTTP, *, previous: Snapshot | None = None
) -> tuple[Snapshot | None, mockhttp.MockHTTP]:
    """Run ``fetch()`` offline against ``http`` into a fresh store."""
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(DAY, time(6), tzinfo=UTC)),
        Fetcher(
            transport=mock.transport, min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0), log=LOG
        ) as fetcher,
        store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=DAY,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp / "raw"),
                previous=previous,
                settings=SETTINGS,
                log=LOG,
            )
        )
    return store.snapshot(COLLECTOR.name, DAY), mock


# --- settings ------------------------------------------------------------------------------------


def test_config_file_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS
    assert COLLECTOR.hosts == (API_HOST, RAW_HOST)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"cask_url": "http://formulae.brew.sh/api/cask.json"}, "cask_url"),
        ({"cask_url": "https://example.com/api/cask.json"}, "cask_url"),
        ({"renames_url": "https://formulae.brew.sh/api/cask_renames.json"}, "renames_url"),
        ({"tap_migrations_url": "https://raw.githubusercontent.com/x"}, "tap_migrations_url"),
        ({"token_prefix": " "}, "token_prefix"),
        ({"min_share": 1.5}, "min_share"),
        ({"build_pattern": r"^(?P<family>.+)$"}, "build_pattern"),
        ({"build_pattern": "("}, "bad pattern"),
        ({"drop_names": [["ornament", "x"]]}, "ornament"),
        ({"drop_names": [["icon", "["]]}, "bad pattern"),
        ({"proprietary_hosts": ["https://apple.com/"]}, "bare host"),
        ({"parent_relation": "rename"}, "parent_relation"),
        ({"min_casks": 2000}, "unknown key"),
    ],
)
def test_settings_are_checked(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=re.escape(message)):
        from_mapping(Settings, changes, "sources/homebrew_casks.toml")


# --- fetch: the extracts -------------------------------------------------------------------------


def test_extract_row_keeps_only_the_font_facts() -> None:
    real = fixture_casks()["font-sf-pro"]
    got = extract_row(real)
    assert tuple(got) == EXTRACT_FIELDS
    assert got["name"] == ["San Francisco Pro", "SF Pro"]
    assert got["fonts"] == []  # a pkg installer, no font artifacts
    abeezee = extract_row(fixture_casks()["font-abeezee"])
    assert abeezee["only_path"] == "ofl/abeezee"
    assert abeezee["fonts"] == ["ABeeZee-Italic.ttf", "ABeeZee-Regular.ttf"]
    gentium = extract_row(fixture_casks()["font-gentium-basic"])
    assert (gentium["disabled"], gentium["disable_date"]) == (True, "2026-06-25")
    assert (gentium["deprecated"], gentium["deprecation_reason"]) == (True, "discontinued")


def test_font_paths_take_font_artifacts_once_in_order() -> None:
    artifacts = [
        {"font": ["b/B.otf"], "target": "/x"},
        {"pkg": ["Fonts.pkg"]},
        {"font": ["a/A.otf"]},
        {"font": ["b/B.otf"]},
        {"font": []},
        {"artifact": ["meta.json", {"target": "/y"}]},
        "junk",
    ]
    assert row("font-x", artifacts=artifacts)["fonts"] == ["b/B.otf", "a/A.otf"]


def test_extract_casks_keeps_font_casks_sorted_once() -> None:
    doc = [
        cask("font-zeta", tap_git_head="abc"),
        cask("fontforge-app"),
        cask("font-alpha", tap_git_head="abc"),
        cask("font-zeta", version="2.0"),
        {"token": None},
        "junk",
    ]
    rows, notes = extract_casks(doc, "font-")
    assert [r["token"] for r in rows] == ["font-alpha", "font-zeta"]
    assert rows[1]["version"] == "1.0"  # the first of a repeated token wins
    assert notes == ["repeated tokens kept once: font-zeta", "homebrew-cask commit abc"]


@pytest.mark.parametrize("doc", [{"casks": []}, [cask("fontforge-app")], []])
def test_extract_casks_refuses_a_broken_body(doc: object) -> None:
    with pytest.raises(ValueError, match=r"cask\.json"):
        extract_casks(doc, "font-")


def test_font_entries_keep_font_tokens_on_either_side() -> None:
    doc = {"font-a": "font-b", "font-c": "c-app", "d": "font-d", "zoom": "zoom-us", "font-e": 3}
    assert font_entries(doc, "font-", "renames") == {
        "d": "font-d",
        "font-a": "font-b",
        "font-c": "c-app",
    }
    with pytest.raises(ValueError, match="renames"):
        font_entries(["font-a"], "font-", "renames")


def test_check_shrink() -> None:
    check_shrink(90, 100, 0.9)
    check_shrink(5, None, 0.9)
    with pytest.raises(ValueError, match="down from 100"):
        check_shrink(89, 100, 0.9)


@pytest.mark.parametrize(
    ("value", "day"),
    [
        ("Sat, 26 Sep 2026 05:26:32 GMT", date(2026, 9, 26)),
        ("Sat, 26 Sep 2026 23:30:00 -0200", date(2026, 9, 27)),  # the UTC day
        (None, None),
        ("", None),
        ("yesterday", None),
    ],
)
def test_modified_day(value: str | None, day: date | None) -> None:
    assert modified_day(value) == day


def test_fetch_writes_the_font_extracts(tmp_path: Path) -> None:
    snap, mock = fetch(tmp_path)
    assert snap is not None
    assert mock.hosts() == {API_HOST, RAW_HOST}
    rows = list(snap.iter_jsonl(CASKS_EXTRACT))
    assert len(rows) == 26
    assert all(r["token"].startswith("font-") for r in rows)
    assert snap.load_json(TAP_MIGRATIONS_EXTRACT) == {}
    renames = snap.load_json(RENAMES_EXTRACT)
    assert "fontforge" not in renames
    assert renames["font-finagler"] == "fontfinagler"
    assert snap.manifest.data_date == date(2026, 9, 26)
    assert [f.kept for f in snap.manifest.fetched] == [False, False, False]
    assert [e.path for e in snap.manifest.extracts] == [
        CASKS_EXTRACT,
        RENAMES_EXTRACT,
        TAP_MIGRATIONS_EXTRACT,
    ]
    assert (tmp_path / "raw" / "cask.json").stat().st_size == snap.manifest.fetched[0].bytes
    assert snap.manifest.notes == ("homebrew-cask commit 81097d41aaaa8d6927a697667b01112ab1817a96",)
    assert snap.read_bytes(CASKS_EXTRACT) == SNAPSHOT.read_bytes(CASKS_EXTRACT)


def test_fetch_without_last_modified_dates_the_data_by_the_run(tmp_path: Path) -> None:
    http = tmp_path / "http"
    shutil.copytree(HTTP, http)
    index = json.loads((http / mockhttp.INDEX).read_text())
    for entry in index["responses"]:
        entry["headers"].pop("last-modified", None)
    mockhttp.write_index(http, index["responses"])
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    assert snap.manifest.data_date == DAY


def test_fetch_never_dates_the_data_after_the_run(tmp_path: Path) -> None:
    http = tmp_path / "http"
    shutil.copytree(HTTP, http)
    index = json.loads((http / mockhttp.INDEX).read_text())
    index["responses"][0]["headers"]["last-modified"] = "Tue, 29 Sep 2026 01:00:00 GMT"
    mockhttp.write_index(http, index["responses"])
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    assert snap.manifest.data_date == DAY


def test_fetch_fails_on_a_broken_renames_body(tmp_path: Path) -> None:
    http = tmp_path / "http"
    shutil.copytree(HTTP, http)
    (http / "cask_renames.json").write_text('["font-a"]')
    with pytest.raises(ValueError, match="expected a JSON object"):
        fetch(tmp_path, http)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_fetch_refuses_a_list_that_shrank(tmp_path: Path) -> None:
    before = write_snapshot(tmp_path / "before", [{"token": f"font-{i}"} for i in range(40)])
    with pytest.raises(ValueError, match="down from 40"):
        fetch(tmp_path / "now", previous=before)
    same = write_snapshot(tmp_path / "same", [{"token": f"font-{i}"} for i in range(28)])
    snap, _ = fetch(tmp_path / "ok", previous=same)
    assert snap is not None


# --- parse: records ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "tokens", "want"),
    [
        ("a", {"c"}, "c"),  # a -> b -> c
        ("a", {"b", "c"}, "b"),  # the first current cask on the chain wins
        ("a", {"z"}, None),  # the chain ends at no current cask
        ("x", {"z"}, None),  # x -> y -> x loops
        ("s", {"z"}, None),  # s -> s
        ("n", {"z"}, None),  # not renamed
    ],
)
def test_rename_target(old: str, tokens: set[str], want: str | None) -> None:
    renames = {"a": "b", "b": "c", "x": "y", "y": "x", "s": "s"}
    assert rename_target(old, renames, tokens) == want


def test_renamed_to_skips_tokens_that_still_exist() -> None:
    renames = {"font-a": "font-b", "font-b": "font-c", "font-d": "font-c", "font-e": "app"}
    assert renamed_to(renames, {"font-c", "font-d"}) == {"font-c": {"font-a", "font-b"}}
    # font-b is current again: font-a stops there, and font-b's own rename is ignored.
    assert renamed_to(renames, {"font-b", "font-c"}) == {
        "font-b": {"font-a"},
        "font-c": {"font-d"},
    }


def test_old_names_agree_with_the_homebrew_miner() -> None:
    from tff_catalog.aliases.miners import homebrew

    rows = {
        "font-b": {"old_tokens": ["font-x", "font-c"]},  # font-c is a current cask
        "font-c": {"old_tokens": []},
        "font-d": {"old_tokens": ["font-y"]},
    }
    renames = {"font-a": "font-b", "font-z": "font-q", "font-q": "font-d", "font-e": "app"}
    got = old_names(rows, renames)
    assert got == {"font-b": {"font-a", "font-x"}, "font-d": {"font-q", "font-y", "font-z"}}
    casks = {t: tuple(r["old_tokens"]) for t, r in rows.items()}
    pairs, _ = homebrew.rename_pairs(casks, renames)
    mined: dict[str, set[str]] = {}
    for old, new in pairs:
        mined.setdefault(new, set()).add(old)
    assert got == mined


@pytest.mark.parametrize(
    ("name", "family", "parent"),
    [
        ("CaskaydiaCove Nerd Font (Cascadia Code)", "CaskaydiaCove Nerd Font", "Cascadia Code"),
        (
            "JetBrainsMono Nerd Font families (JetBrains Mono)",
            "JetBrainsMono Nerd Font",
            "JetBrains Mono",
        ),
        ("GohuFont  Nerd Font families (Gohu)", "GohuFont Nerd Font", "Gohu"),
        ("M+ Nerd Font families (MPlus)", "M+ Nerd Font", "MPlus"),
        ("Monocraft with Nerd glyphs", "Monocraft with Nerd glyphs", None),
        ("Inter", "Inter", None),
        ("Foo (Bar)", "Foo (Bar)", None),
    ],
)
def test_split_build(name: str, family: str, parent: str | None) -> None:
    assert split_build(" ".join(name.split()), BUILD) == (family, parent)


@pytest.mark.parametrize(
    ("url", "only_path", "folder"),
    [
        ("https://github.com/google/fonts/raw/main/ofl/abeezee/ABeeZee-Regular.ttf", None, "ofl/abeezee"),
        ("https://github.com/google/fonts/blob/main/apache/roboto/Roboto.ttf", None, "apache/roboto"),
        ("https://raw.githubusercontent.com/google/fonts/main/ufl/ubuntu/U.ttf", None, "ufl/ubuntu"),
        ("https://github.com/google/fonts.git", "ofl/firamono", "ofl/firamono"),
        ("https://github.com/google/fonts.git", "ofl/firamono/", "ofl/firamono"),
        ("https://github.com/google/fonts.git", "ofl", None),
        ("https://github.com/google/fonts.git", None, None),
        ("https://github.com/google/material-design-icons.git", "variablefont", None),
        ("https://github.com/google/fonts/raw/main/tools/x/y.py", None, None),
        (None, None, None),
    ],
)  # fmt: skip
def test_gf_dir(url: str | None, only_path: str | None, folder: str | None) -> None:
    assert gf_dir(url, only_path) == folder


def test_font_files_only_for_font_downloads() -> None:
    variable = "https://github.com/google/fonts/raw/main/ofl/notoemoji/NotoEmoji%5Bwght%5D.ttf"
    assert font_files(row("font-a", url=variable, sha256="no_check")) == (
        FontFileRef(url=variable, sha256=None, role="variable"),
    )
    raw = "https://github.com/org/repo/blob/master/Font-Regular.ttf?raw=true"
    assert font_files(row("font-b", url=raw, sha256=SHA.upper())) == (
        FontFileRef(url=raw, sha256=SHA, role="regular"),
    )
    # A zip: each installed font as a member reference, without the archive's sha256.
    zipped = row(
        "font-c",
        url="https://fonts.synth.example/c.zip",
        artifacts=[
            {"font": ["c/C[wght].ttf"]},
            {"font": ["c/C-Italic.otf"]},
            {"font": ["c/OFL.txt"]},
        ],
    )
    assert font_files(zipped) == (  # sorted by url
        FontFileRef(url="https://fonts.synth.example/c.zip#c/C%5Bwght%5D.ttf", role="variable"),
        FontFileRef(url="https://fonts.synth.example/c.zip#c/C-Italic.otf", role="italic"),
    )
    assert font_files(row("font-t", url="https://fonts.synth.example/t.tar.xz")) == ()
    assert font_files(row("font-d", url=None)) == ()
    assert file_role("https://x.example/Serif-BoldItalic.otf") == "italic"


@pytest.mark.parametrize(
    ("family", "code"),
    [
        ("Material Symbols", "icon"),
        ("Material Design Icons Webfont", "icon"),
        ("Open Iconic", "icon"),
        ("Font Awesome", "icon"),
        ("Codicon", "icon"),
        ("Symbols Nerd Font", "icon"),
        ("Noto Color Emoji", "emoji"),
        ("Libre Barcode 39 Text", "barcode"),
        ("STIX Two Math", "math"),
        ("Noto Znamenny Musical Notation", "music"),
        ("Noto Sans Symbols 2", "symbol"),
        ("Powerline Symbols", "symbol"),
        ("Webdings", "symbol"),
        ("Yarndings 12 Charted", "symbol"),
        ("ITC Zapf Dingbats", "symbol"),
        ("Headings Sans", None),
        ("Cica without emoji", None),
        ("Text font for musical scores", None),
        ("jsMath cmr10", None),
        ("Iconsolata", None),
        ("JetBrains Mono", None),
    ],
)
def test_drop_code_by_name(family: str, code: str | None) -> None:
    assert drop_code(family, "https://fonts.synth.example/f.zip", SETTINGS) == code


def test_drop_code_proprietary_by_download_host() -> None:
    apple = "https://devimages-cdn.apple.com/design/resources/download/SF-Pro.dmg"
    assert drop_code("SF Pro", apple, SETTINGS) == "proprietary"
    assert drop_code("SF Symbols", apple, SETTINGS) == "symbol"  # a non-text code wins
    assert drop_code("SF Pro", None, SETTINGS) is None


@pytest.mark.parametrize(
    ("deprecated", "disabled", "want"),
    [(False, False, "live"), (True, False, "deprecated"), (True, True, "delisted"), (False, True, "delisted")],
)  # fmt: skip
def test_status(deprecated: bool, disabled: bool, want: str) -> None:
    assert status(row("font-a", deprecated=deprecated, disabled=disabled)) == want


def test_record_attrs() -> None:
    url = "https://github.com/IBM/plex/releases/download/%40ibm%2Fplex-mono%402.5.0/mono.zip"
    got = dict(record_attrs(row("font-a", url=url)))
    assert got == {
        "url": url,
        "fonts": 1,
        "github_repo": "IBM/plex",
        "release": "@ibm/plex-mono@2.5.0",
        "tap": "homebrew/cask",
        "version": "1.0",
    }
    retired = row(
        "font-b",
        deprecated=True,
        deprecation_date="2025-02-19",
        deprecation_reason="discontinued",
        disabled=True,
        disable_date="2026-02-22",
        disable_reason="unreachable",
        disable_replacement_cask="font-c",
    )
    got = dict(record_attrs(retired))
    assert (got["status_date"], got["status_reason"], got["replacement"]) == (
        "2026-02-22",
        "unreachable",
        "font-c",
    )
    archive = "https://github.com/org/repo/archive/refs/tags/v1.tar.gz"
    assert "github_repo" not in dict(record_attrs(row("font-d", url=archive)))


def test_download_is_attr_url_for_gate_m5() -> None:
    """``corrections`` (gate M5) looks for a cask's GitHub release asset in its files,
    its urls and its ``url`` attr; the download of an archive is only in the attr."""
    download = "https://github.com/JetBrains/JetBrainsMono/releases/download/v2/x.zip"
    rec = universe_record(row("font-jb", url=download), set(), SETTINGS, BUILD)
    assert all(f.url.startswith(download + "#") for f in rec.files)  # zip members only
    assert download not in {u for _, u in rec.urls}
    assert dict(rec.attrs)["url"] == download


def test_universe_record_names() -> None:
    r = universe_record(
        row(
            "font-m+-nerd-font",
            name=["M+  Nerd Font families (MPlus)", "M+ NF", "M+ Nerd Font", "m+ nerd-font"],
            old_tokens=["font-ignored-here"],
        ),
        {"font-mplus", "font-mplus-nerd-font", "font-m+-nerd-font"},
        SETTINGS,
        BUILD,
    )
    assert r.key == SourceKey("brew-cask", "font-m+-nerd-font")
    # The family is the font Homebrew says was patched, as in nerdfonts' records.
    assert r.family == "MPlus"
    assert r.names == (
        ("M+ NF", "related"),
        ("M+ Nerd Font", "build"),
        ("font-mplus", "rename"),
        ("font-mplus-nerd-font", "rename"),
    )


def test_a_nerd_cask_takes_its_parents_family_and_keeps_its_drop_code() -> None:
    """The build's name never becomes the family: the universe would display it (the gf
    JetBrains Mono family showed as "JetBrainsMono Nerd Font")."""
    jb = universe_record(
        row("font-jetbrains-mono-nerd-font", name=["JetBrainsMono Nerd Font (JetBrains Mono)"]),
        set(),
        SETTINGS,
        BUILD,
    )
    assert jb.family == "JetBrains Mono"
    assert jb.names == (("JetBrainsMono Nerd Font", "build"),)
    symbols = universe_record(
        row("font-symbols-only-nerd-font", name=["Symbols Nerd Font (Symbols Only)"]),
        set(),
        SETTINGS,
        BUILD,
    )
    assert symbols.family == "Symbols Only"
    assert symbols.drop == "icon"


def test_distinct_names_keep_one_spelling_per_match_key() -> None:
    got = distinct_names(
        [("Hack", "build"), ("hack", "related"), ("Hack NF", "related"), ("Hack Nerd-Font", "x")],
        ("Hack Nerd Font",),
    )
    assert got == (("Hack", "build"), ("Hack NF", "related"))


def test_universe_record_without_a_name_uses_the_token(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        r = universe_record(row("font-nameless", name=[]), set(), SETTINGS, BUILD, LOG)
    assert r.family == "nameless"
    assert "has no name" in caplog.text


def test_fixture_records() -> None:
    recs = parse(SNAPSHOT)
    assert len(recs) == 26
    assert "fontforge-app" not in recs
    nerd = recs["font-opendyslexic-nerd-font"]
    assert nerd.family == "OpenDyslexic"
    assert nerd.names == (
        ("OpenDyslexic Nerd Font", "build"),
        ("font-open-dyslexic-nerd-font", "rename"),
    )
    assert recs["font-sf-pro"].drop == "proprietary"
    assert recs["font-sf-pro"].names == (("SF Pro", "related"),)
    assert dict(recs["font-abeezee"].attrs)["gf_dir"] == "ofl/abeezee"
    assert recs["font-noto-emoji"].files[0].role == "variable"
    assert recs["font-zed-mono"].status == "deprecated"
    assert recs["font-golos-ui"].status == "delisted"
    assert recs["font-jetbrains-mono"].urls == (("homepage", "https://www.jetbrains.com/lp/mono"),)
    # font-maple renames to a cask the fixture lacks; font-finagler to an app.
    assert not any(
        n.startswith(("font-maple", "font-finagler")) for r in recs.values() for n, _ in r.names
    )


def test_parse_hand_made_snapshot(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    rows = [
        row("font-new"),
        row("font-new", version="9.9"),
        {"token": "fontforge-app"},
        {"token": None},
        row("font-other", old_tokens=["font-older"]),
    ]
    snap = write_snapshot(
        tmp_path,
        rows,
        renames={"font-old": "font-mid", "font-mid": "font-new", "font-gone": 7},
        migrations={"font-moved": "someone/fonts", "go": "homebrew/core"},
    )
    with caplog.at_level(logging.INFO):
        recs = parse(snap)
    assert sorted(recs) == ["font-new", "font-other"]
    assert dict(recs["font-new"].attrs)["version"] == "1.0"
    assert recs["font-new"].names == (("font-mid", "rename"), ("font-old", "rename"))
    assert recs["font-other"].names == (("font-older", "rename"),)
    assert "font-moved" in caplog.text
    assert parse(snap) == recs


def test_parse_accepts_a_snapshot_without_rename_extracts(tmp_path: Path) -> None:
    snap = write_snapshot(tmp_path, [row("font-a", old_tokens=["font-b"])])
    assert parse(snap)["font-a"].names == (("font-b", "rename"),)


def test_parse_respects_the_settings(tmp_path: Path) -> None:
    nerd = "Aster Nerd Font (Aster Icons)"
    snap = write_snapshot(
        tmp_path, [row("font-a", name=["Aster Icons"]), row("font-b", name=[nerd])]
    )
    recs = parse(snap, replace(SETTINGS, drop_names=(), parent_relation="related"))
    assert recs["font-a"].drop is None
    assert recs["font-b"].names == (("Aster Icons", "related"),)


# --- the real source -----------------------------------------------------------------------------


@pytest.mark.network
def test_real_cask_shape_and_renames(tmp_path: Path) -> None:
    """One small cask and the two small lists, fetched for real (about 10 KB)."""
    with Fetcher(log=LOG) as fetcher:
        net = fetcher.scoped(COLLECTOR.hosts)
        one = net.get(f"https://{API_HOST}/api/cask/font-jetbrains-mono.json").json()
        renames = net.get(SETTINGS.renames_url).json()
        migrations = net.get(SETTINGS.tap_migrations_url).json()
    got = extract_row(one)
    assert got["token"] == "font-jetbrains-mono"
    assert got["name"] == ["JetBrains Mono"]
    assert got["fonts"]
    assert all(p.endswith((".ttf", ".otf")) for p in got["fonts"])
    assert got["url"].startswith("https://github.com/JetBrains/JetBrainsMono/releases/download/")
    assert dict(record_attrs(got))["github_repo"] == "JetBrains/JetBrainsMono"
    fonts = font_entries(renames, SETTINGS.token_prefix, "renames")
    assert fonts.get("font-mplus-nerd-font") == "font-m+-nerd-font"
    assert isinstance(font_entries(migrations, SETTINGS.token_prefix, "migrations"), dict)
    jsonio.dump(got, tmp_path / "row.json")  # the row is JSON as the extract writes it
