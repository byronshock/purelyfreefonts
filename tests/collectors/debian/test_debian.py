"""The debian collector: popcon installs and font dependencies from Packages.xz.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``. These pin this collector's own rules on
the trimmed real fixture (ruling T1) and on small synthetic stanzas, plus one
real fetch marked ``network``.
"""

import gzip
import json
import logging
import lzma
import shutil
import zlib
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, ParseContext, load_settings
from tff_catalog.collectors.ranking import debian
from tff_catalog.collectors.ranking.debian import (
    COLLECTOR,
    DEPENDENT,
    FONT,
    META,
    PACKAGES,
    POPCON,
    POPCON_URL,
    RESULTS_URL,
    SERIES,
    Settings,
    comparable,
    compare_versions,
    dedupe,
    http_day,
    iter_by_inst,
    iter_stanzas,
    open_text,
    relation_groups,
    relations,
    section_name,
    select_packages,
    submissions_from,
)
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record, Relation
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.debian")
PACKAGES_URL = "https://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.xz"


def parse(snapshot: Snapshot, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def installs(recs: list[Record]) -> dict[str, Observation]:
    return {r.key.key: r for r in recs if isinstance(r, Observation)}


def edges(recs: list[Record], subject: str) -> list[tuple[str, str, int, dict[str, Any]]]:
    """(kind, object, alt, attrs) of ``subject``'s relations, in canonical order."""
    return [
        (r.kind, r.object.key, r.alt, dict(r.attrs))
        for r in recs
        if isinstance(r, Relation) and r.subject.key == subject
    ]


def copy_http(directory: Path) -> Path:
    """A writable copy of the fixture's recorded responses."""
    shutil.copytree(HTTP, directory)
    return directory


def edit_index(directory: Path, url: str, **changes: Any) -> None:
    """Change the recorded response for ``url`` (``headers`` replaces the headers)."""
    path = directory / mockhttp.INDEX
    index = json.loads(path.read_text(encoding="utf-8"))
    for entry in index["responses"]:
        if entry["url"] == url:
            entry.update(changes)
    mockhttp.write_index(directory, index["responses"])


def fetch(
    tmp: Path,
    http: Path = HTTP,
    *,
    day: date = DAY,
    previous: Snapshot | None = None,
    settings: object = SETTINGS,
    store: Store | None = None,
) -> tuple[Snapshot | None, mockhttp.MockHTTP]:
    """Run ``fetch()`` offline against ``http``; return the snapshot and the mock."""
    mock = mockhttp.MockHTTP.from_dir(http)
    store = store or Store(tmp / "store")
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
                raw=RawDir(tmp / f"raw-{day}"),
                previous=previous,
                settings=settings,
                log=LOG,
            )
        )
    return store.snapshot(COLLECTOR.name, day), mock


# --- parse ------------------------------------------------------------------------------------


def test_popcon_installs_of_font_packages() -> None:
    obs = installs(parse(SNAPSHOT))
    dejavu = obs["fonts-dejavu-core"]
    assert (dejavu.series, dejavu.unit, dejavu.value) == (SERIES, "installs", 230374.0)
    assert dejavu.start == dejavu.end == date(2026, 9, 25)  # by_inst's Last-Modified day
    assert dict(dejavu.attrs) == {
        "deb_source": "fonts-dejavu",
        "in_sid": True,
        "packaged": True,
        "role": FONT,
        "section": "fonts",
        "submissions": 290748,
    }
    # fonts-dejavu-mono is packaged but has no popcon row: in the frame, no usable value.
    mono = obs["fonts-dejavu-mono"]
    assert mono.value is None
    assert "in_sid" not in dict(mono.attrs)
    # tex-gyre is filed in Section fonts though its name has no font prefix.
    assert dict(obs["tex-gyre"].attrs)["role"] == FONT


def test_popcon_rows_outside_the_packages_file() -> None:
    obs = installs(parse(SNAPSHOT))
    removed = dict(obs["ttf-dejavu-core"].attrs)
    assert (removed["in_sid"], removed["packaged"], removed["role"]) == (False, False, FONT)
    assert "section" not in removed
    assert obs["fonts-adwaita-sans"].value == 3038.0  # in sid only: kept for its name
    assert "apt" not in obs  # neither a font nor a dependent
    assert "0ad" not in obs  # its dependency on 0ad-data-common does not reach a font


def test_dependents_carry_their_installs() -> None:
    obs = installs(parse(SNAPSHOT))
    for name, value in [("desktop-base", 134418.0), ("task-desktop", 122839.0)]:
        assert obs[name].value == value
        assert dict(obs[name].attrs)["role"] == DEPENDENT
    assert dict(obs["task-desktop"].attrs)["deb_source"] == "tasksel"


def test_alternative_groups_keep_their_positions() -> None:
    recs = parse(SNAPSHOT)
    group = (
        "fonts-dejavu-core | fonts-liberation | fonts-croscore | fonts-freefont-otf | "
        "fonts-freefont-ttf | fonts-urw-base35 | fonts-texgyre | fonts-noto-core"
    )
    found = edges(recs, "fontconfig-config")
    assert [(kind, obj, alt) for kind, obj, alt, _ in found] == [
        ("depends", "fonts-dejavu-core", 0),
        ("depends", "fonts-liberation", 1),
        ("depends", "fonts-croscore", 2),
        ("depends", "fonts-freefont-otf", 3),
        ("depends", "fonts-freefont-ttf", 4),
        ("depends", "fonts-urw-base35", 5),
        ("depends", "fonts-texgyre", 6),
        ("depends", "fonts-noto-core", 7),
    ]  # the debconf | debconf-2.0 group names no font, so it is not kept
    assert all(a == {"field": "Depends", "group": group} for *_, a in found)
    assert edges(recs, "desktop-base") == [("depends", "fonts-quicksand", 0, {"field": "Depends"})]


def test_suggests_are_optional_and_virtuals_are_provided() -> None:
    recs = parse(SNAPSHOT)
    poppler = edges(recs, "poppler-data")
    assert ("optdepends", "fonts-japanese-gothic", 0) in [(k, o, a) for k, o, a, _ in poppler]
    assert ("optdepends", "fonts-ipafont-gothic", 1) in [(k, o, a) for k, o, a, _ in poppler]
    assert edges(recs, "fonts-ipafont-gothic") == [
        ("recommends", "fonts-ipafont-mincho", 0, {"field": "Recommends"}),
        ("provides", "fonts-japanese-gothic", 0, {}),
    ]


def test_extracts_keep_no_maintainers() -> None:
    rows = list(SNAPSHOT.iter_jsonl(PACKAGES))
    assert {r["package"] for r in rows} >= {"fonts-quicksand", "desktop-base", "tex-gyre"}
    assert "0ad" not in {r["package"] for r in rows}
    assert all(
        set(r)
        == {
            "package",
            "version",
            "source",
            "section",
            "architecture",
            "description",
            "homepage",
            "provides",
            "depends",
            "pre_depends",
            "recommends",
            "suggests",
            "role",
        }
        for r in rows
    )
    popcon = list(SNAPSHOT.iter_jsonl(POPCON))
    assert all(
        set(r) == {"name", "rank", "inst", "vote", "old", "recent", "no_files", "in_sid"}
        for r in popcon
    )
    for name in (PACKAGES, POPCON, META):
        text = SNAPSHOT.read_bytes(name).decode()
        assert "@" not in text
        assert "aintainer" not in text


def test_parse_ignores_settings_beyond_its_type() -> None:
    other = replace(SETTINGS, name_pattern="^nothing-")
    assert parse(SNAPSHOT, other) == parse(SNAPSHOT)
    with pytest.raises(TypeError, match="needs its Settings"):
        list(COLLECTOR.parse(ParseContext(SNAPSHOT, object(), LOG)))


# --- Packages helpers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "groups"),
    [
        ("a (>= 1.0) | b:any, c", [["a", "b"], ["c"]]),
        ("fonts-nanum (>= 20200506-1)", [["fonts-nanum"]]),
        ("x [amd64] <!nocheck> | y", [["x", "y"]]),
        ("a | a, , b", [["a"], ["b"]]),
        ("a,\n b | c", [["a"], ["b", "c"]]),
        ("${misc:Depends}, a", [["a"]]),
        ("", []),
    ],
)
def test_relation_groups(value: str, groups: list[list[str]]) -> None:
    assert relation_groups(value) == groups


def test_iter_stanzas_joins_continuation_lines() -> None:
    text = "Package: a\nTag: x::y,\n z::w\n\n\nPackage: b\nDepends: a\n"
    assert list(iter_stanzas(text.splitlines(keepends=True))) == [
        {"Package": "a", "Tag": "x::y,\nz::w"},
        {"Package": "b", "Depends": "a"},
    ]
    with pytest.raises(ValueError, match="continuation outside a field"):
        list(iter_stanzas([" stray\n"]))
    with pytest.raises(ValueError, match="not a field"):
        list(iter_stanzas(["Package: a\n", "no colon here\n"]))


@pytest.mark.parametrize(
    ("a", "b", "sign"),
    [
        ("1.0~rc1", "1.0", -1),
        ("1.0~~", "1.0~", -1),
        ("1.0", "1.0-1", -1),
        ("2.37-8", "2.37-10", -1),
        ("1.0", "1.0a", -1),
        ("1.0", "1.0+b1", -1),
        ("1.0", "1.0.0", -1),
        ("2.0", "1:0.1", -1),
        ("00303-23", "303-23", 0),
        ("1.0-1", "1.0-1", 0),
        ("1.0~git20231203.6ac1634-2", "1.0-1", -1),
    ],
)
def test_compare_versions_follows_dpkg(a: str, b: str, sign: int) -> None:
    got = compare_versions(a, b)
    assert (got > 0) - (got < 0) == sign
    back = compare_versions(b, a)
    assert (back > 0) - (back < 0) == -sign


def test_dedupe_keeps_the_highest_version() -> None:
    stanzas = [
        {"Package": "fonts-a", "Version": "1.0-2", "Maintainer": "someone"},
        {"Package": "fonts-a", "Version": "1.0-10"},
        {"Package": "fonts-a", "Version": "1.0~rc1-1"},
        {"Package": "fonts-b", "Version": "2"},
        {"Version": "no package"},
    ]
    packages, repeated = dedupe(stanzas)
    assert packages == {
        "fonts-a": {"Package": "fonts-a", "Version": "1.0-10"},
        "fonts-b": {"Package": "fonts-b", "Version": "2"},
    }
    assert repeated == ["fonts-a"]
    assert dedupe(reversed(stanzas))[0] == packages  # file order does not matter


def test_select_packages_rules() -> None:
    stanzas = [
        {"Package": "fonts-mono", "Section": "fonts", "Version": "1.0", "Provides": "font-virtual"},
        {"Package": "fonts-mono", "Section": "fonts", "Version": "0.9"},  # older, dropped
        {"Package": "fonttool", "Section": "fonts", "Depends": "libc6"},
        {"Package": "boot-splash", "Section": "misc", "Pre-Depends": "fonts-mono"},
        {"Package": "viewer", "Section": "x11", "Depends": "font-virtual | fonts-mono"},
        {"Package": "editor", "Section": "editors", "Depends": "libc6, libgtk-3-0"},
        {"Package": "fonts-meta", "Section": "misc", "Depends": "fonts-meta, fonts-gone"},
        {"Package": "suggester", "Suggests": "ttf-removed"},
    ]
    sel = select_packages(stanzas, SETTINGS)
    rows = {r["package"]: r for r in sel.rows}
    assert sorted(rows) == [
        "boot-splash",
        "fonts-meta",
        "fonts-mono",
        "fonttool",
        "suggester",
        "viewer",
    ]
    assert (sel.listed, sel.fonts, sel.dependents, sel.repeated) == (7, 3, 3, ["fonts-mono"])
    assert rows["fonts-mono"]["provides"] == ["font-virtual"]
    assert rows["fonts-mono"]["version"] == "1.0"
    assert rows["fonttool"]["depends"] == []  # a tool in Section fonts: kept, no font edges
    assert rows["boot-splash"]["pre_depends"] == [["fonts-mono"]]
    assert rows["viewer"]["depends"] == [["font-virtual", "fonts-mono"]]
    assert rows["fonts-meta"]["depends"] == [["fonts-gone"]]  # no edge to itself
    assert rows["suggester"]["suggests"] == [["ttf-removed"]]
    assert rows["viewer"]["source"] == "viewer"  # no Source field: the binary's own name
    recs = list(relations(sel.rows))
    boot = [r for r in recs if r.subject.key == "boot-splash"]
    assert [(r.kind, r.object.key, dict(r.attrs)) for r in boot] == [
        ("depends", "fonts-mono", {"field": "Pre-Depends"})
    ]
    assert [r.object.key for r in recs if r.kind == "provides"] == ["font-virtual"]


def test_sections_outside_main_carry_an_area_prefix() -> None:
    """contrib and non-free stanzas say ``Section: contrib/fonts``; that is still ``fonts``."""
    assert section_name({"Section": "contrib/fonts"}) == "fonts"
    assert section_name({"Section": "fonts"}) == "fonts"
    assert section_name({}) == ""
    stanzas = [
        {"Package": "msttcorefonts-like", "Section": "contrib/fonts", "Depends": "cabextract"},
        {"Package": "office", "Section": "contrib/editors", "Depends": "msttcorefonts-like"},
        {"Package": "fonty", "Section": "contrib/fontsx"},  # not a section listed
    ]
    rows = {r["package"]: r for r in select_packages(stanzas, SETTINGS).rows}
    assert sorted(rows) == ["msttcorefonts-like", "office"]
    assert (rows["msttcorefonts-like"]["role"], rows["office"]["role"]) == (FONT, DEPENDENT)
    assert rows["msttcorefonts-like"]["section"] == "contrib/fonts"  # stored as the file says


def test_open_text_follows_magic_bytes_not_names(tmp_path: Path) -> None:
    text = "Package: fonts-a\nSection: fonts\n"
    xz, gz, plain = tmp_path / "a.gz", tmp_path / "b.xz", tmp_path / "c.xz"
    xz.write_bytes(lzma.compress(text.encode()))
    gz.write_bytes(gzip.compress(text.encode(), mtime=0))
    plain.write_text(text, encoding="utf-8")  # decoded on the wire despite its name
    for path in (xz, gz, plain):
        with open_text(path) as fh:
            assert fh.read() == text


# --- popcon helpers ---------------------------------------------------------------------------

BY_INST_HEADER = (
    "#rank name                            inst  vote   old recent no-files (maintainer)\n"
)


def test_iter_by_inst_reads_rows_until_the_total() -> None:
    lines = [
        "#Format\n",
        BY_INST_HEADER,
        "1     fonts-a       10     2     3     4     1 (Debian Fonts Task Force)\n",
        "2     ttf-b          5     0     0     0     5 (Not in sid)\n",
        "3     fonts-c        1     0     0     0     1\n",
        "-----\n",
        "3     Total         16     2     3     4     7 0\n",
    ]
    rows = list(iter_by_inst(lines))
    assert rows == [
        {"name": "fonts-a", "rank": 1, "inst": 10, "vote": 2, "old": 3, "recent": 4,
         "no_files": 1, "in_sid": True},
        {"name": "ttf-b", "rank": 2, "inst": 5, "vote": 0, "old": 0, "recent": 0,
         "no_files": 5, "in_sid": False},
        {"name": "fonts-c", "rank": 3, "inst": 1, "vote": 0, "old": 0, "recent": 0,
         "no_files": 1, "in_sid": True},
    ]  # fmt: skip


def test_iter_by_inst_refuses_another_format() -> None:
    with pytest.raises(ValueError, match="header"):
        list(iter_by_inst(["#rank name inst vote\n", "1 fonts-a 10 2 3 4 1 (x)\n"]))
    with pytest.raises(ValueError, match="unreadable row"):
        list(iter_by_inst([BY_INST_HEADER, "1 fonts-a many\n"]))


def test_submissions_from_the_head_of_all_popcon_results() -> None:
    whole = gzip.compress(b"Submissions:   290748\n" + b"Release: 1.28   2\n" * 5000, mtime=0)
    assert submissions_from(whole) == 290748
    assert submissions_from(whole[:64]) == 290748  # a truncated stream still starts right
    assert submissions_from(gzip.compress(b"Release: 1.28 2\n", mtime=0)) is None
    assert submissions_from(b"<html>not gzip</html>") is None
    assert submissions_from(zlib.compress(b"Submissions: 5\n")) is None  # zlib, not gzip


def test_http_day() -> None:
    assert http_day("Fri, 25 Sep 2026 23:58:49 GMT") == date(2026, 9, 25)
    assert http_day("Fri, 25 Sep 2026 23:58:49 -0500") == date(2026, 9, 26)
    assert http_day(None) is None
    assert http_day("yesterday") is None


# --- settings -----------------------------------------------------------------------------------


def test_settings_file_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS
    assert SETTINGS.packages_url == PACKAGES_URL


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"architecture": "all"}, "binary-all"),
        ({"suite": "Trixie"}, "suite"),
        ({"component": "main/debian-installer"}, "component"),
        ({"sections": ()}, "sections"),
        ({"name_pattern": "^(fonts"}, "name_pattern"),
        ({"min_share": 1.5}, "min_share"),
    ],
)
def test_settings_are_checked(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings(**changes)


# --- fetch ----------------------------------------------------------------------------------


def test_fetch_keeps_extracts_only(tmp_path: Path) -> None:
    snap, mock = fetch(tmp_path)
    assert snap is not None
    assert [e.path for e in snap.manifest.extracts] == [META, PACKAGES, POPCON]
    assert [(f.url, f.status, f.kept) for f in snap.manifest.fetched] == [
        (PACKAGES_URL, 200, False),
        (POPCON_URL, 200, False),
        (RESULTS_URL, 206, False),
    ]
    assert mock.requests[2].headers["range"] == "bytes=0-4095"
    assert snap.manifest.data_date == date(2026, 9, 25)
    assert parse(snap) == parse(SNAPSHOT)
    # The committed snapshot is exactly what fetch() writes from the committed responses.
    for name in (META, PACKAGES, POPCON):
        assert snap.read_bytes(name) == SNAPSHOT.read_bytes(name), name


def test_unchanged_packages_file_is_copied(tmp_path: Path) -> None:
    first, _ = fetch(tmp_path, day=DAY)
    assert first is not None
    later = date(2026, 10, 26)
    second, mock = fetch(tmp_path, day=later, previous=first, store=Store(tmp_path / "store"))
    assert second is not None
    assert mock.requests[0].headers["if-none-match"] == '"93ae2c-65b442aad5439"'
    assert [f.status for f in second.manifest.fetched] == [304, 200, 206]
    assert second.manifest.extract(PACKAGES) == first.manifest.extract(PACKAGES)
    assert second.manifest.notes == (
        "Packages.xz unchanged since Sat, 12 Sep 2026 07:29:52 GMT (304)",
    )
    assert second.load_json(META)["packages"] == first.load_json(META)["packages"]
    assert parse(second) == parse(first)
    # The 304 carries the validators over, so the month after asks conditionally again.
    third, mock = fetch(tmp_path, day=date(2026, 11, 26), previous=second)
    assert third is not None
    assert mock.requests[0].headers["if-none-match"] == '"93ae2c-65b442aad5439"'


def test_another_selection_refetches_the_packages_file(tmp_path: Path) -> None:
    first, _ = fetch(tmp_path)
    assert first is not None
    narrower = replace(SETTINGS, name_pattern="^fonts-")
    second, mock = fetch(tmp_path, day=date(2026, 10, 26), previous=first, settings=narrower)
    assert second is not None
    assert "if-none-match" not in mock.requests[0].headers
    assert second.manifest.fetched[0].status == 200
    assert "ttf-dejavu-core" not in installs(parse(second, narrower))


def test_a_new_selection_revision_refetches_the_packages_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changed selection code must not hide behind an unchanged Packages.xz (a 304)."""
    first, _ = fetch(tmp_path)
    assert first is not None
    assert first.load_json(META)["selection_revision"] == debian.SELECTION_REVISION
    monkeypatch.setattr(debian, "SELECTION_REVISION", debian.SELECTION_REVISION + 1)
    second, mock = fetch(tmp_path, day=date(2026, 10, 26), previous=first)
    assert second is not None
    assert "if-none-match" not in mock.requests[0].headers
    assert second.manifest.fetched[0].status == 200
    assert second.load_json(META)["selection_revision"] == debian.SELECTION_REVISION


def test_a_previous_snapshot_without_packages_meta_lends_nothing(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    meta = SNAPSHOT.load_json(META)
    with store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as writer:
        writer.write_json(META, {k: v for k, v in meta.items() if k != "packages"})
        writer.write_jsonl(PACKAGES, SNAPSHOT.iter_jsonl(PACKAGES))
        writer.write_jsonl(POPCON, SNAPSHOT.iter_jsonl(POPCON))
    broken = store.snapshot(COLLECTOR.name, DAY)
    assert broken is not None
    assert comparable(broken, SETTINGS) is None
    assert comparable(SNAPSHOT, SETTINGS) is SNAPSHOT
    assert comparable(None, SETTINGS) is None


def test_packages_listed_twice_are_noted(tmp_path: Path) -> None:
    http = copy_http(tmp_path / "http")
    text = lzma.decompress((http / "Packages.xz").read_bytes()).decode()
    older = "Package: fonts-hack\nVersion: 3.003-2\nSection: fonts\nArchitecture: all\n"
    (http / "Packages.xz").write_bytes(lzma.compress(f"{text}\n{older}".encode()))
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    assert snap.manifest.notes == ("listed twice, highest version kept: fonts-hack",)
    hack = next(r for r in snap.iter_jsonl(PACKAGES) if r["package"] == "fonts-hack")
    assert hack["version"] == "3.003-3"
    assert parse(snap) == parse(SNAPSHOT)


def test_a_popcon_file_decoded_on_the_wire_is_still_read(tmp_path: Path) -> None:
    """A server sending by_inst.gz with Content-Encoding: gzip leaves plain text on disk."""
    http = copy_http(tmp_path / "http")
    (http / "by_inst.gz").write_bytes(gzip.decompress((http / "by_inst.gz").read_bytes()))
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    assert parse(snap) == parse(SNAPSHOT)


def test_a_shrunken_popcon_file_fails(tmp_path: Path) -> None:
    http = copy_http(tmp_path / "http")
    lines = gzip.decompress((http / "by_inst.gz").read_bytes()).decode().splitlines(True)
    kept = [line for line in lines if not line.startswith(("2", "3", "4", "5", "6"))]
    (http / "by_inst.gz").write_bytes(gzip.compress("".join(kept).encode(), mtime=0))
    with pytest.raises(ValueError, match=r"popcon\.jsonl\.gz: \d+ rows, down from 22"):
        fetch(tmp_path, http, day=date(2026, 10, 26), previous=SNAPSHOT)


def test_a_shrunken_packages_file_fails(tmp_path: Path) -> None:
    http = copy_http(tmp_path / "http")
    few = "Package: fonts-a\nSection: fonts\n\nPackage: fonts-b\nSection: fonts\n"
    (http / "Packages.xz").write_bytes(lzma.compress(few.encode()))
    edit_index(http, PACKAGES_URL, headers={"content-type": "application/x-xz"})
    with pytest.raises(ValueError, match="down from 19"):
        fetch(tmp_path, http, day=date(2026, 10, 26), previous=SNAPSHOT)
    snap, _ = fetch(tmp_path / "fresh", http, day=date(2026, 10, 26))  # nothing to compare with
    assert snap is not None


def test_a_packages_file_without_fonts_fails(tmp_path: Path) -> None:
    http = copy_http(tmp_path / "http")
    (http / "Packages.xz").write_bytes(lzma.compress(b"Package: apt\nSection: admin\n"))
    with pytest.raises(ValueError, match="no rows kept"):
        fetch(tmp_path, http)


def test_a_changed_popcon_format_fails(tmp_path: Path) -> None:
    http = copy_http(tmp_path / "http")
    text = gzip.decompress((http / "by_inst.gz").read_bytes()).decode()
    text = text.replace("#rank name", "#rank package")
    (http / "by_inst.gz").write_bytes(gzip.compress(text.encode(), mtime=0))
    with pytest.raises(ValueError, match="header"):
        fetch(tmp_path, http)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_missing_submission_total_is_noted(tmp_path: Path) -> None:
    http = copy_http(tmp_path / "http")
    edit_index(http, RESULTS_URL, status=404, body=None)
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    assert snap.load_json(META)["popcon"]["submissions"] is None
    assert any("submission total unknown" in n for n in snap.manifest.notes)
    obs = installs(parse(snap))
    assert "submissions" not in dict(obs["fonts-hack"].attrs)
    assert obs["fonts-hack"].value == 39671.0


def test_popcon_without_last_modified_is_dated_by_the_fetch(tmp_path: Path) -> None:
    http = copy_http(tmp_path / "http")
    edit_index(http, POPCON_URL, headers={"content-type": "application/x-gzip"})
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    assert snap.manifest.data_date == DAY
    assert installs(parse(snap))["fonts-hack"].end == DAY


# --- the real thing ---------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real fetch: Packages.xz (about 9.7 MB) and by_inst.gz (about 3.4 MB), parsed offline."""
    store = Store(tmp_path / "store")
    day = clock.utc_today()
    with Fetcher(log=LOG) as fetcher, store.writer(COLLECTOR.name, day, 1) as writer:
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
    recs = list(COLLECTOR.parse(ParseContext(snapshot=snap, settings=SETTINGS, log=LOG)))
    obs = installs(recs)
    meta = snap.load_json(META)
    assert meta["packages"]["fonts"] >= 500
    assert meta["popcon"]["submissions"] >= 100_000
    assert obs["fonts-dejavu-core"].value >= 100_000
    assert dict(obs["desktop-base"].attrs)["role"] == DEPENDENT
    assert ("depends", "fonts-quicksand", 0, {"field": "Depends"}) in edges(recs, "desktop-base")
    firsts = {o for k, o, a, _ in edges(recs, "fontconfig-config") if k == "depends" and a == 0}
    assert firsts == {"fonts-dejavu-core"}
