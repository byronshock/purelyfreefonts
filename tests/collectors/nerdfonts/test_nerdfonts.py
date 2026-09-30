"""The nerdfonts collector: Nerd Fonts' fonts.json, pinned to a commit.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the trimmed real fixture (ruling T1), plus one real fetch marked ``network``.
"""

import json
import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.universe.nerdfonts import (
    API_HOST,
    COLLECTOR,
    FONTS,
    LICENSES,
    PIN,
    RAW_HOST,
    Pin,
    Settings,
    build_names,
    cask_token,
    check_shrink,
    commits_url,
    font_entries,
    is_spdx_expression,
    license_files,
    license_path,
    read_licenses,
)
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import LicenseFact, Record, UniverseRecord
from tff_catalog.store import RawDir, Snapshot, SnapshotWriter, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
LATER = date(2026, 11, 1)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.nerdfonts")
COMMIT = "64a084f95480ee5efb62132c01cd142ab6147655"
OTHER_COMMIT = "0123456789abcdef0123456789abcdef01234567"
COMMITS_URL = commits_url(SETTINGS)
RAW_BASE = f"https://{RAW_HOST}/ryanoasis/nerd-fonts"
TREE = (HTTP / "tree.json").read_bytes()
UNPATCHED = f"{RAW_BASE}/{COMMIT}/src/unpatched-fonts"


def tree_url(commit: str = COMMIT) -> str:
    return f"https://{API_HOST}/repos/ryanoasis/nerd-fonts/git/trees/{commit}:src/unpatched-fonts?recursive=1"


def parse(snapshot: Snapshot = SNAPSHOT) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, SETTINGS)


def universe(recs: list[Record]) -> dict[str, UniverseRecord]:
    return {r.key.key: r for r in recs if isinstance(r, UniverseRecord)}


def licenses(recs: list[Record]) -> dict[str, LicenseFact]:
    return {r.key.key: r for r in recs if isinstance(r, LicenseFact)}


def fixture_entries() -> list[dict[str, Any]]:
    return json.loads((HTTP / "fonts.json").read_text(encoding="utf-8"))["fonts"]


def fonts_body(entries: list[dict[str, Any]]) -> bytes:
    return (json.dumps({"fonts": entries}, indent=2, ensure_ascii=False) + "\n").encode()


def commits_body(sha: str = COMMIT, when: str = "2026-09-01T21:54:46Z") -> bytes:
    return json.dumps([{"sha": sha, "commit": {"committer": {"date": when}}}]).encode()


def write_http(directory: Path, responses: list[tuple[str, bytes, str | None]]) -> Path:
    """A mockhttp fixture serving each ``(url, body, etag)``."""
    directory.mkdir(parents=True, exist_ok=True)
    index = []
    for i, (url, body, etag) in enumerate(responses):
        (directory / f"body{i}").write_bytes(body)
        entry: dict[str, Any] = {"url": url, "body": f"body{i}"}
        if etag is not None:
            entry["headers"] = {"etag": etag}
        index.append(entry)
    mockhttp.write_index(directory, index)
    return directory


def fetch(
    tmp: Path, http: Path, *, day: date = DAY, previous: Snapshot | None = None
) -> tuple[Snapshot | None, list[str]]:
    """Run ``fetch()`` offline into a fresh store; return the snapshot and the requested URLs."""
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    try:
        with (
            clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
            Fetcher(
                transport=mock.transport, min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0), log=LOG
            ) as fetcher,
            store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer,
        ):
            COLLECTOR.fetch(
                FetchContext(
                    run_date=day,
                    fetcher=fetcher.scoped(COLLECTOR.hosts),
                    out=writer,
                    raw=RawDir(tmp / "raw"),
                    previous=previous,
                    settings=SETTINGS,
                    log=LOG,
                )
            )
    finally:
        assert not mock.unmatched, mock.unmatched
    return store.snapshot(COLLECTOR.name, day), mock.urls()


def snapshot_of(tmp: Path, entries: list[dict[str, Any]], pin: dict[str, Any]) -> Snapshot:
    """A snapshot holding ``entries`` as fonts.json and ``pin`` as pin.json."""
    writer = SnapshotWriter(tmp / "made", COLLECTOR.name, DAY, COLLECTOR.version)
    writer.write_bytes(FONTS, fonts_body(entries), rows=len(entries))
    writer.write_json(PIN, pin)
    return writer.close()


# --- parse ------------------------------------------------------------------------------------


def test_records_are_keyed_by_folder_with_the_original_family() -> None:
    fams = universe(parse())
    assert len(fams) == 16
    assert {r.key.ns for r in fams.values()} == {"nerd-folder"}
    assert fams["CascadiaCode"].family == "Cascadia Code"
    assert fams["FiraMono"].family == "Fira"  # as the file says; aliases fold it
    assert fams["3270"].family == "IBM 3270"
    assert all(r.status == "live" and r.category is None for r in fams.values())
    assert fams["Ubuntu"].is_monospace is False
    assert fams["Hack"].is_monospace is True


def test_build_aliases_come_from_patched_and_folder_names() -> None:
    names = {k: r.names for k, r in universe(parse()).items()}
    assert names["CascadiaCode"] == (("CaskaydiaCove", "build"),)  # RFN rename
    assert names["AnonymousPro"] == (("AnonymicePro", "build"),)
    assert names["Meslo"] == (("Meslo", "build"),)  # patched MesloLG = Meslo LG
    assert names["FiraMono"] == (("FiraMono", "build"),)
    assert names["3270"] == (("3270", "build"),)  # patched and folder name agree
    assert names["Recursive"] == (("RecMono", "build"), ("Recursive", "build"))
    assert names["MPlus"] == (("M+", "build"),)
    for same in ("Hack", "0xProto", "Go-Mono", "EnvyCodeR", "Overpass", "Ubuntu"):
        assert names[same] == (), same  # every name has the family's match key


def test_build_names_skip_empty_and_repeated_keys() -> None:
    assert build_names("Go Mono", "GoMono", "Go-Mono") == ()
    assert build_names("Meslo LG", "MesloLG", "Meslo", "meslo") == (("Meslo", "build"),)
    assert build_names("X", None, " ", "Y") == (("Y", "build"),)


def test_symbols_only_is_an_icon_font_without_a_font_file() -> None:
    fams = universe(parse())
    symbols = fams["NerdFontsSymbolsOnly"]
    assert (symbols.family, symbols.drop, symbols.files) == ("Symbols Only", "icon", ())
    assert [k for k, r in fams.items() if r.drop] == ["NerdFontsSymbolsOnly"]


def test_font_files_are_the_originals_pinned_to_the_commit() -> None:
    fams = universe(parse())
    base = f"{RAW_BASE}/{COMMIT}/src/unpatched-fonts/"
    assert fams["AnonymousPro"].files[0].url == base + "AnonymousPro/Anonymous%20Pro.ttf"
    assert fams["Meslo"].files[0].url == (
        base + "Meslo/M/Meslo%20LG%20M%20Regular%20for%20Powerline.ttf"
    )
    files = [f for r in fams.values() for f in r.files]
    assert len(files) == 15
    assert all(f.url.startswith(base) and f.role == "regular" for f in files)
    assert all(f.sha256 is None and f.size is None for f in files)


def test_attrs_keep_cask_patched_name_and_version() -> None:
    cascadia = universe(parse())["CascadiaCode"]
    assert dict(cascadia.attrs) == {
        "cask_name": "caskaydia-cove",
        "patched_name": "CaskaydiaCove",
        "version": "2407.24",
    }
    assert cask_token("caskaydia-cove") == "font-caskaydia-cove-nerd-font"
    assert cask_token("m+") == "font-m+-nerd-font"


def test_license_facts_keep_the_raw_id_and_speak_spdx_only_when_well_formed() -> None:
    facts = licenses(parse())
    assert len(facts) == 16
    overpass = facts["Overpass"]
    assert (overpass.raw, overpass.spdx) == ("OFL-1.1-no-RFN or LGPL-2.1-only", None)
    assert facts["Hack"].spdx == "Bitstream-Vera AND MIT"
    assert facts["Ubuntu"].spdx == "LicenseRef-UbuntuFont"
    assert (facts["CascadiaCode"].rfn, facts["Hack"].rfn) == (True, False)
    assert dict(facts["EnvyCodeR"].attrs) == {
        "rfn_exception": "https://github.com/ryanoasis/nerd-fonts/pull/1318#issuecomment-1636737323"
    }
    assert all(f.text_sha256 is None for f in facts.values())  # texts are read in stage verify


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("OFL-1.1-RFN", True),
        ("Bitstream-Vera AND MIT", True),
        ("MIT OR OFL-1.1-no-RFN", True),
        ("LicenseRef-UbuntuFont", True),
        ("(MIT OR Apache-2.0) AND OFL-1.1", True),
        ("GPL-2.0-or-later WITH Font-exception-2.0", True),
        ("GPL-2.0+", True),
        ("OFL-1.1-no-RFN or LGPL-2.1-only", False),  # lower-case operator
        ("MIT AND", False),
        ("AND MIT", False),
        ("(MIT", False),
        ("MIT)", False),
        ("MIT Apache-2.0", False),
        ("SIL Open Font License", False),
        ("", False),
        ("()", False),
    ],
)
def test_is_spdx_expression(text: str, ok: bool) -> None:
    assert is_spdx_expression(text) is ok


def test_parse_takes_optional_fields_as_absent(tmp_path: Path) -> None:
    bare = {"folderName": "BirchMono", "unpatchedName": "Birch Mono"}
    odd = {
        "folderName": "Cobalt",
        "unpatchedName": "Cobalt",
        "patchedName": "",
        "licenseId": " ",
        "isMonospaced": "yes",
        "RFN": "no",
        "imagePreviewFontSource": "../outside.ttf",
    }
    pin = json.loads(SNAPSHOT.read_bytes(PIN))
    recs = parse(snapshot_of(tmp_path, [bare, odd, bare], pin))
    assert not licenses(recs)
    fams = universe(recs)
    assert fams["BirchMono"] == UniverseRecord(
        source="nerdfonts", key=fams["BirchMono"].key, family="Birch Mono"
    )
    assert (fams["Cobalt"].is_monospace, fams["Cobalt"].files, fams["Cobalt"].names) == (
        None,
        (),
        (),
    )
    assert len(recs) == 2  # the repeated entry gives the same record once


def test_a_folder_listed_twice_keeps_its_first_entry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    first = {"folderName": "Birch", "unpatchedName": "Birch Mono", "licenseId": "MIT"}
    again = {"folderName": "Birch", "unpatchedName": "Birch Sans", "licenseId": "OFL-1.1-RFN"}
    pin = json.loads(SNAPSHOT.read_bytes(PIN))
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        recs = parse(snapshot_of(tmp_path, [first, again], pin))
    assert [r.family for r in universe(recs).values()] == ["Birch Mono"]
    assert [r.raw for r in licenses(recs).values()] == ["MIT"]
    assert len(recs) == 2
    assert "folder Birch is listed again" in caplog.text


@pytest.mark.parametrize(
    "doc",
    [
        [],
        {"fonts": []},
        {"fonts": "x"},
        {"fonts": [1]},
        {"fonts": [{"folderName": "A"}]},
        {"fonts": [{"unpatchedName": "A", "folderName": ""}]},
    ],
)
def test_font_entries_reject_what_is_not_a_font_list(doc: object) -> None:
    with pytest.raises(ValueError, match=FONTS):
        font_entries(doc)


def test_pin_round_trips_and_rejects_bad_values() -> None:
    pin = Pin.from_json(json.loads(SNAPSHOT.read_bytes(PIN)))
    assert (pin.commit, pin.committed_at, pin.day) == (
        COMMIT,
        "2026-09-01T21:54:46Z",
        date(2026, 9, 1),
    )
    assert Pin.from_json(pin.to_json()) == pin
    for bad in (
        {**pin.to_json(), "schema": 2},
        {**pin.to_json(), "commit": "abc"},
        {**pin.to_json(), "committed_at": "2026-09-01"},
        {**pin.to_json(), "committed_at": "2026-09-01T23:54:46+02:00"},
        {k: v for k, v in pin.to_json().items() if k != "repo"},
    ):
        with pytest.raises(ValueError, match=PIN):
            Pin.from_json(bad)


def test_check_shrink_allows_exactly_min_share() -> None:
    check_shrink(9, 10, 0.9)
    check_shrink(3, None, 0.9)  # no previous snapshot: nothing to compare
    check_shrink(0, 10, 0.0)  # min_share 0 switches the check off
    with pytest.raises(ValueError, match="down from 10"):
        check_shrink(8, 10, 0.9)


# --- settings ---------------------------------------------------------------------------------


def test_settings_file_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS
    assert SETTINGS.drop == {"NerdFontsSymbolsOnly": "icon"}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"drop": {"X": "bogus"}}, "drop.X"),
        ({"repo": "nerd-fonts"}, "repo"),
        ({"ref": "a b"}, "ref"),
        ({"path": "../fonts.json"}, "path"),
        ({"unpatched_dir": "/src"}, "unpatched_dir"),
        ({"min_share": 1.5}, "min_share"),
        ({"sereis": 1}, "sereis"),
    ],
)
def test_settings_reject_bad_values(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        from_mapping(Settings, data, where="sources/nerdfonts.toml")


# --- license files ------------------------------------------------------------------------------


def test_license_facts_point_at_the_folders_license_file_at_the_commit() -> None:
    urls = {k: f.text_url for k, f in licenses(parse()).items()}
    assert urls["0xProto"] == f"{UNPATCHED}/0xProto/LICENSE"
    assert urls["Meslo"] == f"{UNPATCHED}/Meslo/LICENSE.txt"  # up from the preview's M/ folder
    assert urls["Overpass"] == f"{UNPATCHED}/Overpass/Mono/LICENSE.md"  # the preview's own folder
    assert urls["Ubuntu"] == f"{UNPATCHED}/Ubuntu/LICENCE.txt"  # not the FAQ or copyright.txt
    assert urls["EnvyCodeR"] == f"{UNPATCHED}/EnvyCodeR/LICENCE.md"
    assert all(u is not None for u in urls.values())


@pytest.mark.parametrize(
    ("files", "want"),
    [
        (["X/OFL.txt", "X/LICENSE"], "X/LICENSE"),
        (["X/OFL.txt", "X/Apache License.txt"], "X/OFL.txt"),
        (["X/SIL Open Font License.txt", "X/COPYING-LICENSE"], "X/SIL Open Font License.txt"),
        (["X/LICENCE-FAQ.txt", "X/copyright.txt"], None),
        (["Y/LICENSE"], None),
        (["X/sub/LICENSE", "X/other/LICENSE"], None),
    ],
)
def test_license_path_prefers_a_plain_license_file(files: list[str], want: str | None) -> None:
    entry = {"folderName": "X", "imagePreviewFontSource": "X/X-Regular.ttf"}
    assert license_path(entry, files) == want


def test_license_path_without_a_preview_looks_in_the_folder() -> None:
    assert license_path({"folderName": "X"}, ["X/LICENSE.md"]) == "X/LICENSE.md"


def test_license_files_keep_license_like_blobs_and_refuse_truncated_trees() -> None:
    doc = json.loads(TREE)
    files = license_files(doc)
    assert "Meslo/LICENSE.txt" in files
    assert "Ubuntu/LICENCE-FAQ.txt" in files  # listed; license_path passes it over
    assert not any(f.endswith((".ttf", ".otf", "README.md")) for f in files)
    with pytest.raises(ValueError, match="truncated"):
        license_files({**doc, "truncated": True})
    with pytest.raises(ValueError, match="no tree"):
        license_files({"message": "Not Found"})


def test_a_license_list_for_another_commit_is_not_used(tmp_path: Path) -> None:
    pin = Pin.from_json(SNAPSHOT.load_json(PIN))
    doc = SNAPSHOT.load_json(LICENSES)
    assert read_licenses(doc, pin, "src/unpatched-fonts") == doc["files"]
    assert read_licenses({**doc, "commit": OTHER_COMMIT}, pin, "src/unpatched-fonts") is None
    writer = SnapshotWriter(tmp_path / "made", COLLECTOR.name, DAY, COLLECTOR.version)
    writer.write_bytes(FONTS, SNAPSHOT.read_bytes(FONTS), rows=16)
    writer.write_json(PIN, SNAPSHOT.load_json(PIN))
    writer.write_json(LICENSES, {**doc, "commit": OTHER_COMMIT})
    assert all(f.text_url is None for f in licenses(parse(writer.close())).values())


def test_a_version_1_snapshot_without_license_files_still_parses(tmp_path: Path) -> None:
    old = snapshot_of(tmp_path, fixture_entries(), SNAPSHOT.load_json(PIN))
    recs = parse(old)
    assert universe(recs) == universe(parse())
    assert all(f.text_url is None for f in licenses(recs).values())


# --- fetch ------------------------------------------------------------------------------------


def test_fetch_keeps_the_file_byte_for_byte_at_the_pinned_commit(tmp_path: Path) -> None:
    snap, urls = fetch(tmp_path, HTTP)
    assert snap is not None
    assert [u.split("/")[2] for u in urls] == [API_HOST, RAW_HOST, API_HOST]
    assert snap.read_bytes(FONTS) == (HTTP / "fonts.json").read_bytes()
    assert snap.manifest.extract(FONTS).rows == 16
    assert Pin.from_json(snap.load_json(PIN)).commit == COMMIT
    assert snap.manifest.data_date == date(2026, 9, 1)
    assert [e.path for e in snap.manifest.extracts] == [FONTS, LICENSES, PIN]
    assert [f.kept for f in snap.manifest.fetched] == [False, True, False]  # the tree is not kept
    assert snap.load_json(LICENSES) == SNAPSHOT.load_json(LICENSES)


def test_an_unchanged_commit_answer_copies_both_extracts(tmp_path: Path) -> None:
    # The fixture's commits answer carries the ETag the fixture snapshot recorded: a 304.
    snap, urls = fetch(tmp_path, HTTP, day=LATER, previous=SNAPSHOT)
    assert snap is not None
    assert len(urls) == 1
    assert snap.manifest.fetched[0].status == 304
    assert snap.manifest.fetched[0].etag == SNAPSHOT.manifest.fetched[0].etag  # kept for next time
    assert snap.manifest.extracts == SNAPSHOT.manifest.extracts
    assert snap.manifest.data_date == date(2026, 9, 1)
    assert parse(snap) == parse()


def test_the_same_commit_copies_the_file_without_downloading_it(tmp_path: Path) -> None:
    http = write_http(tmp_path / "http", [(COMMITS_URL, commits_body(), '"another-etag"')])
    snap, urls = fetch(tmp_path, http, day=LATER, previous=SNAPSHOT)
    assert snap is not None
    assert urls == [mockhttp.normalize_url(COMMITS_URL)]
    assert snap.manifest.extract(FONTS) == SNAPSHOT.manifest.extract(FONTS)
    assert snap.read_bytes(PIN) == SNAPSHOT.read_bytes(PIN)
    assert snap.manifest.notes == (f"bin/scripts/lib/fonts.json unchanged since {COMMIT[:12]}",)


def test_a_new_commit_downloads_the_new_file(tmp_path: Path) -> None:
    entries = fixture_entries()
    entries[0] = {**entries[0], "version": "3.000"}
    http = write_http(
        tmp_path / "http",
        [
            (COMMITS_URL, commits_body(OTHER_COMMIT, "2026-10-20T08:00:00+02:00"), None),
            (f"{RAW_BASE}/{OTHER_COMMIT}/bin/scripts/lib/fonts.json", fonts_body(entries), None),
            (tree_url(OTHER_COMMIT), TREE, None),
        ],
    )
    snap, urls = fetch(tmp_path, http, day=LATER, previous=SNAPSHOT)
    assert snap is not None
    assert len(urls) == 3  # the previous license list is for another commit
    assert snap.load_json(LICENSES)["commit"] == OTHER_COMMIT
    pin = Pin.from_json(snap.load_json(PIN))
    assert (pin.commit, pin.committed_at) == (OTHER_COMMIT, "2026-10-20T06:00:00Z")
    assert snap.manifest.data_date == date(2026, 10, 20)
    proto = universe(parse(snap))["0xProto"]
    assert dict(proto.attrs)["version"] == "3.000"
    assert OTHER_COMMIT in proto.files[0].url
    assert OTHER_COMMIT in (licenses(parse(snap))["0xProto"].text_url or "")


@pytest.mark.parametrize(
    "tree",
    [b'{"message": "Not Found"}', json.dumps({"tree": [], "truncated": True}).encode()],
    ids=["not-a-tree", "truncated"],
)
def test_a_failed_license_list_leaves_the_snapshot_standing(tmp_path: Path, tree: bytes) -> None:
    http = write_http(
        tmp_path / "http",
        [
            (COMMITS_URL, commits_body(), None),
            (
                f"{RAW_BASE}/{COMMIT}/bin/scripts/lib/fonts.json",
                (HTTP / "fonts.json").read_bytes(),
                None,
            ),
            (tree_url(), tree, None),
        ],
    )
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    assert [e.path for e in snap.manifest.extracts] == [FONTS, PIN]
    assert snap.manifest.notes[0].startswith(f"no license file list at {COMMIT[:12]}")
    assert all(f.text_url is None for f in licenses(parse(snap)).values())


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (fonts_body(fixture_entries()[:2]), "below min_share"),
        (b"<html>rate limited</html>", "not JSON"),
        (b'{"fonts": []}', "at least one entry"),
    ],
)
def test_a_broken_or_shrunken_file_fails_the_fetch(
    tmp_path: Path, body: bytes, message: str
) -> None:
    http = write_http(
        tmp_path / "http",
        [
            (COMMITS_URL, commits_body(OTHER_COMMIT), None),
            (f"{RAW_BASE}/{OTHER_COMMIT}/bin/scripts/lib/fonts.json", body, None),
        ],
    )
    with pytest.raises(ValueError, match=message):
        fetch(tmp_path, http, day=LATER, previous=SNAPSHOT)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, LATER) is None


@pytest.mark.parametrize(
    "body",
    [b"[]", b'[{"sha": "abc"}]', json.dumps([{"sha": COMMIT, "commit": {}}]).encode()],
)
def test_a_commit_answer_without_a_commit_fails_the_fetch(tmp_path: Path, body: bytes) -> None:
    http = write_http(tmp_path / "http", [(COMMITS_URL, body, None)])
    with pytest.raises(ValueError, match="ryanoasis/nerd-fonts"):
        fetch(tmp_path, http)


@pytest.mark.parametrize(
    "pin",
    [None, {"schema": 2, "commit": COMMIT}, {"schema": 1, "repo": "ryanoasis/nerd-fonts"}],
    ids=["no-pin", "other-schema", "missing-fields"],
)
def test_a_previous_snapshot_without_a_readable_pin_is_not_reused(
    tmp_path: Path, pin: dict[str, Any] | None
) -> None:
    # The old manifest carries the ETag the fixture answers 304 to: reusing it would copy
    # a snapshot that cannot be pinned.
    writer = SnapshotWriter(tmp_path / "old", COLLECTOR.name, DAY, COLLECTOR.version)
    writer.record_fetch(SNAPSHOT.manifest.fetched[0])
    writer.write_bytes(FONTS, (HTTP / "fonts.json").read_bytes(), rows=16)
    if pin is not None:
        writer.write_json(PIN, pin)
    old = writer.close()
    snap, urls = fetch(tmp_path, HTTP, day=LATER, previous=old)
    assert snap is not None
    assert len(urls) == 3  # no validators sent, so the file is downloaded again
    assert [f.status for f in snap.manifest.fetched] == [200, 200, 200]
    assert snap.read_bytes(FONTS) == (HTTP / "fonts.json").read_bytes()
    assert Pin.from_json(snap.load_json(PIN)).commit == COMMIT


def test_the_data_date_is_never_after_the_run(tmp_path: Path) -> None:
    early = date(2026, 8, 30)  # a --date before the pinned commit's day (2026-09-01)
    snap, _ = fetch(tmp_path, HTTP, day=early)
    assert snap is not None
    assert snap.manifest.data_date == early


# --- the real source ----------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real fetch (GITHUB_TOKEN is used when set) and a parse of it."""
    store = Store(tmp_path / "store")
    day = clock.utc_today()
    with (
        Fetcher(log=LOG) as fetcher,
        store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer,
    ):
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
    recs = parse(snap)
    fams = universe(recs)
    assert len(fams) >= 60
    assert fams["CascadiaCode"].names == (("CaskaydiaCove", "build"),)
    assert fams["NerdFontsSymbolsOnly"].drop == "icon"
    assert len(licenses(recs)) == len(fams)
    assert all(f.text_url is not None for f in licenses(recs).values())
    commit = Pin.from_json(snap.load_json(PIN)).commit
    assert all(commit in f.url for r in fams.values() for f in r.files)
