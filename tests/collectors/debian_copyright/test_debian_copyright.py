"""The debian_copyright collector: license short names from DEP-5 copyright files.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the synthetic fixture (docs/sources.md: facts only, synthetic fixtures), plus
one real request marked ``network``.
"""

import dataclasses
import gzip
import json
import logging
import lzma
import re
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog import fetch as fetch_module
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.universe.debian_copyright import (
    COLLECTOR,
    EXTRACT,
    FILES_ATTR_MAX,
    METADATA_BASE,
    SOURCES_URL,
    DebianCopyright,
    Settings,
    SourcePackage,
    copyright_url,
    font_sources,
    font_stanzas,
    license_facts,
    paragraphs,
    read_copyright,
    version_compare,
)
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import Fetcher, FetchError
from tff_catalog.paths import Paths
from tff_catalog.records import LicenseFact, Record, SourceKey
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.debian_copyright")


def parse(snapshot: Snapshot, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def facts(recs: list[Record]) -> dict[str, list[LicenseFact]]:
    """Facts by package, in canonical order."""
    out: dict[str, list[LicenseFact]] = {}
    for r in recs:
        assert isinstance(r, LicenseFact)
        out.setdefault(r.key.key, []).append(r)
    return out


def raws(recs: list[Record]) -> dict[str, list[str]]:
    return {pkg: sorted(f.raw for f in fs) for pkg, fs in facts(recs).items()}


def fetch(
    tmp: Path,
    http: Path,
    *,
    day: date = DAY,
    previous: Snapshot | None = None,
    settings: object = SETTINGS,
    retries: int = 0,
) -> tuple[Snapshot, mockhttp.MockHTTP]:
    """Run ``fetch()`` offline against ``http`` into the store under ``tmp``."""
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
        Fetcher(
            transport=mock.transport,
            min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0),
            retries=retries,
            log=LOG,
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
                settings=settings,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    return snap, mock


def rows(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    return {r["package"]: r for r in snapshot.iter_jsonl(EXTRACT)}


def copy_http(tmp: Path, *, drop: str | None = None, **changes: dict[str, Any]) -> Path:
    """The fixture's http/ in ``tmp``: optionally without the entry for ``drop`` (a package),
    and with fields of the entries for other packages replaced (``changes[package]``)."""
    target = tmp / "http"
    target.mkdir(parents=True)
    for path in HTTP.iterdir():
        (target / path.name).write_bytes(path.read_bytes())
    index = json.loads((HTTP / mockhttp.INDEX).read_text(encoding="utf-8"))
    out = []
    for entry in index["responses"]:
        package = entry["url"].rsplit("/", 2)[-2]
        if package == drop:
            continue
        out.append({**entry, **changes.get(package.replace("-", "_"), {})})
    mockhttp.write_index(target, out)
    return target


def sources_http(tmp: Path, text: str) -> Path:
    """An http/ fixture serving ``text`` as Sources.xz, and every copyright file of the fixture."""
    target = copy_http(tmp)
    (target / "Sources.xz").write_bytes(lzma.compress(text.encode("utf-8")))
    return target


# --- deb822 and Sources --------------------------------------------------------------------------


def test_paragraphs_read_fields_continuations_and_separators() -> None:
    text = (
        "# a comment\n"
        "Package: a\n"
        "Files: one\n"
        " two\n"
        "\tthree\n"
        "files: ignored repeat\n"
        "garbage line\n"
        " dropped continuation\n"
        "   \n"
        "License: OFL-1.1\n"
        " .\n"
        " text\n"
        "\n\n"
    )
    assert list(paragraphs(text.splitlines(keepends=True))) == [
        {"package": "a", "files": "one\ntwo\nthree"},
        {"license": "OFL-1.1\n.\ntext"},
    ]


def test_font_sources_select_by_source_or_binary_section_at_the_highest_version() -> None:
    lines = (HTTP / "Sources.xz").read_bytes()
    got = font_sources(lzma.decompress(lines).decode().splitlines(), ("fonts",))
    assert [(p.name, p.version) for p in got] == [
        ("fonts-aster-sans", "1.2-1"),  # listed at 1.0-1 and 1.2-1
        ("fonts-birch-mono", "3.003-5"),  # source section misc, binary fonts
        ("fonts-cobalt-serif", "0.9-3"),
        ("fonts-delta-grotesk", "1:3.1-2"),
        ("fonts-ember-slab", "20240101-1"),
        ("xfonts-fjord", "1:1.0.5-2"),  # source section x11
    ]
    assert got[0] == SourcePackage(
        "fonts-aster-sans", "1.2-1", "pool/main/f/fonts-aster-sans", "https://aster.example/"
    )
    assert font_sources(lzma.decompress(lines).decode().splitlines(), ("editors",)) == [
        SourcePackage("synth-editor", "4.0-1", "pool/main/s/synth-editor")
    ]


def test_font_sources_skip_paragraphs_without_the_needed_fields() -> None:
    text = (
        "Package: fonts-a\nVersion: 1\nSection: fonts\n\n"  # no directory
        "Package: fonts-b\nVersion: 1\nSection: fonts\nDirectory: elsewhere/b\n\n"
        "Version: 1\nSection: fonts\nDirectory: pool/main/f/c\n\n"
        "Package: fonts-d\nVersion: 1\nSection: contrib/fonts\nDirectory: pool/contrib/f/fonts-d\n"
    )
    assert [p.name for p in font_sources(text.splitlines(), ("fonts",))] == ["fonts-d"]


def test_copyright_url_follows_the_pool_directory() -> None:
    assert copyright_url(METADATA_BASE, "pool/main/libf/libfoo-fonts") == (
        "https://metadata.ftp-master.debian.org/changelogs/main/libf/libfoo-fonts/"
        "unstable_copyright"
    )


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("1.0", "1.0-1"),
        ("1.0~rc1", "1.0"),
        ("1.0~rc1-1", "1.0~rc2-1"),
        ("1.0-1~bpo1", "1.0-1"),
        ("2.0", "1:0.1"),
        ("2.37-8", "2.37-9"),
        ("2.37-9", "2.37-10"),
        ("1.0", "1.0a"),
        ("1.0a", "1.0+b1"),  # letters sort before other characters
        ("1.0", "1.0.1"),
        ("20201225-2", "20210101-1"),
        ("3.003-5", "3.003-5.1"),
        ("0.9", "0.10"),
    ],
)
def test_version_compare_follows_dpkg(a: str, b: str) -> None:
    assert version_compare(a, b) < 0
    assert version_compare(b, a) > 0
    assert version_compare(a, a) == 0


def test_version_compare_ignores_leading_zeros() -> None:
    assert version_compare("1.00", "1.0") == 0
    assert version_compare("0:1.0-1", "1.0-1") == 0


# --- DEP-5 --------------------------------------------------------------------------------------

DEP5 = """\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Garnet Sans
Upstream-Contact: Synth Maintainer <maintainer@synth.invalid>
Source: https://garnet.example/fonts, mirrored by Synth <maintainer@synth.invalid>
 https://user@git.garnet.example/garnet.git
License: OFL-1.1

Files: *
Copyright: 2020 Synth Maintainer <maintainer@synth.invalid>
License: OFL-1.1-RFN
 Some text.

Files: debian/* debian/patches/*
Copyright: 2021 Synth Packager <packager@synth.invalid>
License: GPL-2+

Files: ./debian/rules fonts/*.otf
 fonts/*.ttf,
Copyright: 2020 Synth Maintainer
License: OFL-1.1-RFN

Files: build/*
Copyright: 2020 Synth Maintainer

License: OFL-1.1-RFN
 The full text.
"""


def test_read_copyright_keeps_license_names_and_patterns_only() -> None:
    got = read_copyright(DEP5)
    assert got == {
        "dep5": True,
        "upstream_name": "Garnet Sans",
        "source_urls": ["https://garnet.example/fonts"],
        "header_license": "OFL-1.1",
        "stanzas": [
            {"files": ["*"], "license": "OFL-1.1-RFN"},
            {"files": ["fonts/*.otf", "fonts/*.ttf"], "license": "OFL-1.1-RFN"},
            {"files": ["build/*"], "license": None},
        ],
    }
    assert "@" not in json.dumps(got)
    assert "Copyright" not in json.dumps(got)


def test_read_copyright_takes_the_old_format_header_and_refuses_free_form() -> None:
    old = "Format-Specification: http://svn.debian.org/dep5\n\nFiles: *\nLicense: GPL-2+\n"
    assert read_copyright(old)["stanzas"] == [{"files": ["*"], "license": "GPL-2+"}]
    free = read_copyright("This package was debianized in 2001.\n\nFiles: * License: GPL\n")
    assert free == {
        "dep5": False,
        "upstream_name": None,
        "source_urls": [],
        "header_license": None,
        "stanzas": [],
    }
    assert read_copyright("")["dep5"] is False


def test_read_copyright_counts_a_header_that_carries_files() -> None:
    """A malformed file with ``Files`` in its header paragraph: its license is not lost."""
    text = (
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        "Files: *\nCopyright: 2020 Synth\nLicense: OFL-1.1\n\n"
        "Files: debian/*\nLicense: GPL-2+\n\n"
        "Files: extra/*\nLicense: MIT\n"
    )
    got = read_copyright(text)
    assert got["header_license"] == "OFL-1.1"
    assert got["stanzas"] == [
        {"files": ["*"], "license": "OFL-1.1"},
        {"files": ["extra/*"], "license": "MIT"},
    ]


def test_font_stanzas_skip_packaging_licenseless_and_non_font_stanzas() -> None:
    stanzas = [
        {"files": ["*"], "license": "OFL-1.1"},
        {"files": ["debian/*"], "license": "GPL-2+"},
        {"files": ["tools/*.py", "scripts/*"], "license": "GPL-2+"},
        {"files": ["tools/*.py", "fonts/*.ttf"], "license": "MIT"},
        {"files": ["build/*"], "license": None},
        {"files": [], "license": "CC0-1.0"},
        "not a stanza",
    ]
    got = list(font_stanzas(stanzas, ("tools/*", "scripts/*")))
    assert got == [("OFL-1.1", ["*"]), ("MIT", ["tools/*.py", "fonts/*.ttf"])]
    assert list(font_stanzas(None, ())) == []


# --- parse --------------------------------------------------------------------------------------


def test_parse_gives_one_fact_per_distinct_license_of_font_stanzas() -> None:
    assert raws(parse(SNAPSHOT)) == {
        "fonts-aster-sans": ["OFL-1.1-RFN"],
        "fonts-birch-mono": ["BitstreamVera", "Expat"],
        "fonts-ember-slab": ["OFL-1.1 or GPL-3+ with Font exception"],
        "xfonts-fjord": ["OFL-1.1-no-RFN"],
    }


def test_parse_facts_carry_rfn_version_files_and_upstream_name() -> None:
    by = facts(parse(SNAPSHOT))
    [aster] = by["fonts-aster-sans"]
    assert aster == LicenseFact(
        source="debian_copyright",
        key=SourceKey("deb-src", "fonts-aster-sans"),
        raw="OFL-1.1-RFN",
        rfn=True,
        attrs=(("files", "*"), ("upstream_name", "Aster Sans"), ("version", "1.2-1")),
    )
    expat = next(f for f in by["fonts-birch-mono"] if f.raw == "Expat")
    assert dict(expat.attrs)["files"] == "* postbuild/*"  # two stanzas, one fact
    assert expat.rfn is None
    [fjord] = by["xfonts-fjord"]
    assert (fjord.rfn, dict(fjord.attrs)) == (False, {"files": "*", "version": "1:1.0.5-2"})
    assert all(f.spdx is None and f.text_url is None for fs in by.values() for f in fs)


def test_parse_skips_missing_free_form_and_non_font_stanzas() -> None:
    by = raws(parse(SNAPSHOT))
    assert "fonts-cobalt-serif" not in by  # 404
    assert "fonts-delta-grotesk" not in by  # free-form
    # ember: tools/scripts (GPL-2+), appstream (CC0) and doc (CC-BY-SA) stanzas are not fonts
    everything = dataclasses.replace(SETTINGS, non_font_files=())
    assert raws(parse(SNAPSHOT, everything))["fonts-ember-slab"] == [
        "CC-BY-SA-4.0",
        "CC0-1.0",
        "GPL-2+",
        "OFL-1.1 or GPL-3+ with Font exception",
    ]


@pytest.mark.parametrize(
    ("raw", "rfn"),
    [
        ("OFL-1.1-RFN", True),
        ("ofl-1.1-rfn", True),
        ("OFL-1.1-RFN or GPL-2.0+", True),
        ("OFL-1.1-no-RFN", False),
        ("GPL-3.0+ with Font Exception or OFL-1.1-no-RFN", False),
        ("OFL-1.1", None),
        ("GPL-2+ with Font exception", None),
    ],
)
def test_rfn_follows_the_short_name(raw: str, rfn: bool | None) -> None:
    row = {**rows(SNAPSHOT)["fonts-aster-sans"], "stanzas": [{"files": ["*"], "license": raw}]}
    [fact] = license_facts(row, SETTINGS)
    assert (fact.raw, fact.rfn, fact.spdx) == (raw, rfn, None)


def test_parse_clips_long_files_attr(tmp_path: Path) -> None:
    long = [f"fonts/face-{n:03d}.ttf" for n in range(40)]
    row = {
        **rows(SNAPSHOT)["fonts-aster-sans"],
        "stanzas": [{"files": long, "license": "OFL-1.1"}],
    }
    snap = _snapshot_with_rows(tmp_path, [row])
    [fact] = parse(snap)
    files = dict(fact.attrs)["files"]
    assert len(files) == FILES_ATTR_MAX
    assert files.endswith("…")
    assert files.startswith("fonts/face-000.ttf fonts/face-001.ttf")


def _snapshot_with_rows(tmp: Path, extract_rows: list[dict[str, Any]]) -> Snapshot:
    store = Store(tmp / "store")
    with store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as writer:
        writer.write_jsonl(EXTRACT, extract_rows)
    snap = store.snapshot(COLLECTOR.name, DAY)
    assert snap is not None
    return snap


def test_parse_ignores_repeated_and_malformed_rows(tmp_path: Path) -> None:
    good = rows(SNAPSHOT)["fonts-aster-sans"]
    bad = [
        {**good, "stanzas": [{"files": ["*"], "license": "MIT"}]},  # repeat: first row wins
        {**good, "package": "fonts-x", "status": 404},
        {**good, "package": "fonts-y", "dep5": False},
        {**good, "package": "fonts-z", "version": None},
        {"package": None},
    ]
    snap = _snapshot_with_rows(tmp_path, [good, *bad])
    assert raws(parse(snap)) == {"fonts-aster-sans": ["OFL-1.1-RFN"]}


# --- fetch --------------------------------------------------------------------------------------


def test_fetch_keeps_facts_only(tmp_path: Path) -> None:
    snap, mock = fetch(tmp_path, HTTP)
    assert not mock.unmatched
    data = snap.read_bytes(EXTRACT)
    assert snap.manifest.extracts[0].rows == 6
    for word in (b"@", b"Copyright", b"Permission", b"debianized", b'"GPL-3+"'):
        assert word not in data, word
    got = rows(snap)
    assert got["fonts-cobalt-serif"]["status"] == 404
    assert got["fonts-cobalt-serif"]["stanzas"] == []
    assert got["fonts-delta-grotesk"]["dep5"] is False
    assert got["fonts-ember-slab"]["stanzas"][2] == {"files": ["appstream/*"], "license": "CC0-1.0"}
    assert not any(
        f.startswith("debian/") for r in got.values() for s in r["stanzas"] for f in s["files"]
    )
    index = snap.manifest.fetched[0]
    assert (index.url, index.kept) == (SOURCES_URL, False)
    assert not any(p.name == "Sources.xz" for p in snap.path.iterdir())
    assert snap.manifest.data_date == DAY
    assert "copyright files missing (404): 1" in snap.manifest.notes
    assert "copyright files not in DEP-5 format (no facts): 1" in snap.manifest.notes
    assert regen.golden(snap.path / EXTRACT) == regen.golden(FIXTURE / "snapshot" / EXTRACT)


def test_second_fetch_is_conditional_and_carries_unchanged_rows(tmp_path: Path) -> None:
    first, _ = fetch(tmp_path / "a", HTTP)
    later = date(2026, 11, 3)
    second, mock = fetch(tmp_path / "a", HTTP, day=later, previous=first)
    sent = [r for r in mock.requests if "unstable_copyright" in str(r.url)]
    assert len(sent) == 6
    assert sum("if-none-match" in r.headers for r in sent) == 5  # not the 404
    statuses = sorted(f.status for f in second.manifest.fetched)
    assert statuses == [200, 304, 304, 304, 304, 304, 404]
    assert rows(second) == rows(first)
    assert parse(second) == parse(first)
    assert "copyright files not modified since the previous snapshot: 5" in second.manifest.notes


def test_no_conditional_request_without_content_to_carry_over(tmp_path: Path) -> None:
    first, _ = fetch(tmp_path / "a", HTTP)
    earlier = [
        {**r, "status": 404} if r["package"] == "fonts-aster-sans" else r
        for r in rows(first).values()
    ]
    odd = _previous_with(tmp_path / "b", first, earlier)
    second, mock = fetch(tmp_path / "c", HTTP, previous=odd)
    [aster] = [r for r in mock.requests if "fonts-aster-sans" in str(r.url)]
    assert "if-none-match" not in aster.headers
    assert rows(second)["fonts-aster-sans"] == rows(first)["fonts-aster-sans"]


def test_a_carried_row_takes_the_current_sources_version(tmp_path: Path) -> None:
    """A 304 says the file is unchanged, so its facts hold for the version Sources lists now."""
    first, _ = fetch(tmp_path / "a", HTTP)
    text = lzma.decompress((HTTP / "Sources.xz").read_bytes()).decode()
    newer = sources_http(tmp_path / "b", text.replace("Version: 3.003-5\n", "Version: 3.003-6\n"))
    second, _ = fetch(tmp_path / "c", newer, previous=first, day=date(2026, 11, 3))
    birch = rows(second)["fonts-birch-mono"]
    assert birch["version"] == "3.003-6"
    assert birch["stanzas"] == rows(first)["fonts-birch-mono"]["stanzas"]
    assert "copyright files not modified since the previous snapshot: 5" in second.manifest.notes


def test_rows_carry_over_only_from_the_same_collector_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After an extract-format change every file is read again: no 304, no carried row."""
    monkeypatch.setattr(fetch_module, "_sleep", lambda s: None)
    first, _ = fetch(tmp_path / "a", HTTP)
    monkeypatch.setattr(DebianCopyright, "version", COLLECTOR.version + 1)
    broken = copy_http(tmp_path / "b", fonts_birch_mono={"status": 500, "body": None})
    second, mock = fetch(tmp_path / "c", broken, previous=first, day=date(2026, 11, 3))
    assert second.manifest.collector_version == COLLECTOR.version
    assert not any("if-none-match" in r.headers for r in mock.requests)
    assert 304 not in {f.status for f in second.manifest.fetched}
    assert "fonts-birch-mono" not in rows(second)  # failed, and the old row is not carried
    assert rows(second)["fonts-aster-sans"] == rows(first)["fonts-aster-sans"]


def test_a_shrink_is_measured_against_a_snapshot_of_any_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = fetch(tmp_path / "a", HTTP)
    monkeypatch.setattr(DebianCopyright, "version", COLLECTOR.version + 1)
    text = lzma.decompress((HTTP / "Sources.xz").read_bytes()).decode()
    fewer = sources_http(tmp_path / "b", text.split("Package: fonts-cobalt-serif")[0])
    with pytest.raises(ValueError, match="below min_share"):
        fetch(tmp_path / "c", fewer, previous=first, day=date(2026, 11, 3))


def _previous_with(tmp: Path, like: Snapshot, extract_rows: list[dict[str, Any]]) -> Snapshot:
    """A previous snapshot with ``like``'s requests and these extract rows."""
    store = Store(tmp / "store")
    day = date(2026, 9, 3)
    with store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer:
        for entry in like.manifest.fetched:
            writer.record_fetch(entry)
        writer.write_jsonl(EXTRACT, extract_rows)
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    return snap


def test_a_failed_request_keeps_the_previous_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_module, "_sleep", lambda s: None)
    first, _ = fetch(tmp_path / "a", HTTP)
    broken = copy_http(tmp_path / "b", fonts_birch_mono={"status": 500, "body": None})
    second, _ = fetch(tmp_path / "c", broken, previous=first, day=date(2026, 11, 3))
    assert rows(second)["fonts-birch-mono"] == rows(first)["fonts-birch-mono"]
    assert any(n.startswith("failed: fonts-birch-mono:") for n in second.manifest.notes)


def test_a_failed_request_without_a_previous_row_leaves_the_package_out(tmp_path: Path) -> None:
    broken = copy_http(tmp_path, fonts_birch_mono={"status": 500, "body": None})
    snap, _ = fetch(tmp_path / "x", broken)
    assert "fonts-birch-mono" not in rows(snap)
    assert len(rows(snap)) == 5


def test_too_many_failed_requests_fail_the_fetch(tmp_path: Path) -> None:
    broken = copy_http(tmp_path, fonts_birch_mono={"status": 500, "body": None})
    strict = dataclasses.replace(SETTINGS, max_failures=0)
    with pytest.raises(FetchError, match="max_failures = 0"):
        fetch(tmp_path / "x", broken, settings=strict)


def test_a_shrunken_package_list_fails_the_fetch(tmp_path: Path) -> None:
    first, _ = fetch(tmp_path / "a", HTTP)
    text = lzma.decompress((HTTP / "Sources.xz").read_bytes()).decode()
    fewer = sources_http(tmp_path / "b", text.split("Package: fonts-cobalt-serif")[0])
    with pytest.raises(ValueError, match="below min_share"):
        fetch(tmp_path / "c", fewer, previous=first, day=date(2026, 11, 3))


def test_an_index_without_font_packages_fails_the_fetch(tmp_path: Path) -> None:
    empty = sources_http(tmp_path, "Package: synth-editor\nVersion: 1\nSection: editors\n")
    with pytest.raises(ValueError, match="no source package"):
        fetch(tmp_path / "x", empty)


def test_fixture_is_synthetic_hosts_and_no_addresses() -> None:
    """BRIEF item 12: Debian DEP-5 fixtures are synthetic. Every URL is Debian's own or on a
    reserved .example host, and no e-mail address appears."""
    texts = [p.read_bytes() for p in HTTP.iterdir() if p.name != "Sources.xz"]
    texts.append(lzma.decompress((HTTP / "Sources.xz").read_bytes()))
    texts += [p.read_bytes() for p in (FIXTURE / "snapshot").iterdir() if p.suffix != ".gz"]
    texts.append(gzip.decompress((FIXTURE / "snapshot" / EXTRACT).read_bytes()))
    debian = {
        "deb.debian.org",
        "metadata.ftp-master.debian.org",
        "www.debian.org",
        "svn.debian.org",
    }
    for text in texts:
        for host in re.findall(rb"https?://([^/\s\"'<>()]+)", text):
            name = host.decode()
            assert name in debian or name.endswith(".example"), name
        assert not re.search(rb"[\w.+-]+@[\w-]+\.[\w.]+", text)


def test_fixture_rows_are_sorted_by_package() -> None:
    raw = (FIXTURE / "snapshot" / EXTRACT).read_bytes()
    lines = gzip.decompress(raw).decode().splitlines()
    assert [json.loads(line)["package"] for line in lines] == sorted(
        json.loads(line)["package"] for line in lines
    )


# --- settings -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sources_url": "http://deb.debian.org/debian/x"}, "sources_url"),
        ({"sources_url": "https://mirror.example/debian/x"}, "sources_url"),
        ({"metadata_base": "https://metadata.ftp-master.debian.org/changelogs"}, "end with"),
        ({"metadata_base": "https://example.org/changelogs/"}, "metadata_base"),
        ({"copyright_name": "../etc"}, "copyright_name"),
        ({"sections": ()}, "sections"),
        ({"min_share": 1.5}, "min_share"),
        ({"max_failures": -1}, "max_failures"),
        ({"non_font_files": ("",)}, "non_font_files"),
    ],
)
def test_settings_refuse_bad_values(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings(**changes)  # type: ignore[arg-type]


def test_settings_file_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS


# --- the real thing -----------------------------------------------------------------------------


@pytest.mark.network
def test_real_copyright_file_reads_as_dep5() -> None:
    """One real copyright file (DejaVu: a non-SPDX short name) through the real fetcher."""
    url = copyright_url(METADATA_BASE, "pool/main/f/fonts-dejavu")
    with Fetcher(hosts=COLLECTOR.hosts) as fetcher:
        result = fetcher.get(url)
    got = read_copyright(result.body().decode("utf-8", "replace"))
    assert got["dep5"] is True
    licenses = {license_ for license_, _ in font_stanzas(got["stanzas"], SETTINGS.non_font_files)}
    assert "bitstream-vera" in licenses
    assert result.etag or result.last_modified  # the conditional GET of later runs needs one
