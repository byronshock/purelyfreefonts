"""The arch_repos collector: font dependency edges in the Arch and CachyOS sync databases.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the fixture (trimmed real Arch rows, synthetic CachyOS rows; see its NOTICE),
plus one real fetch marked ``network``. Rebuild the fixture with
``uv run python -m tests.collectors.arch_repos.build_fixture``.
"""

import dataclasses
import gzip
import json
import logging
import re
import time
from collections.abc import Iterable
from compression import zstd
from datetime import UTC, date
from pathlib import Path
from typing import Any

import pytest
from tests.collectors.arch_repos import build_fixture
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, ParseContext, load_settings
from tff_catalog.collectors.ranking.arch_repos import (
    COLLECTOR,
    DEFAULT_REPOS,
    DISTROS,
    EXTRACT_SUFFIX,
    FONT_GROUPS,
    FONT_PATTERN,
    FONT_PROVIDES,
    NOT_FONTS,
    REPOS,
    Repo,
    Settings,
    check_counts,
    check_packages,
    compression,
    dep_name,
    is_font,
    last_modified_day,
    parse_desc,
    previous_fonts,
    previous_packages,
    read_db,
    select_rows,
)
from tff_catalog.config import load_config
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import LicenseFact, Record, Relation, SourceKey
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = build_fixture.FIXTURE
HTTP = build_fixture.HTTP
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.arch_repos")
EMAIL = re.compile(rb"[\w.+-]+@[\w-]+\.[\w.-]+")


def parse(snapshot: Snapshot = SNAPSHOT) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, SETTINGS)


def relations(recs: Iterable[Record]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """{(subject, kind, object, repo): attrs} of every Relation."""
    out = {}
    for r in recs:
        if isinstance(r, Relation):
            a = dict(r.attrs)
            out[(r.subject.key, r.kind, r.object.key, str(a["repo"]))] = a
    return out


def licenses(recs: Iterable[Record]) -> dict[tuple[str, str], list[LicenseFact]]:
    """{(package, repo): its license facts}."""
    out: dict[tuple[str, str], list[LicenseFact]] = {}
    for r in recs:
        if isinstance(r, LicenseFact):
            out.setdefault((r.key.key, str(dict(r.attrs)["repo"])), []).append(r)
    return out


def rows(snapshot: Snapshot, repo: str) -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in snapshot.iter_jsonl(repo + EXTRACT_SUFFIX)}


def fetch(tmp: Path, **kwargs: Any) -> Snapshot:
    return build_fixture.run_fetch(tmp, **kwargs)


# --- settings ---------------------------------------------------------------------------------


def test_config_file_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS
    assert SETTINGS.repos == DEFAULT_REPOS
    assert [r.name for r in SETTINGS.repos] == ["core", "extra", "multilib", "cachyos"]
    assert (SETTINGS.font_pattern, SETTINGS.not_fonts) == (FONT_PATTERN, NOT_FONTS)
    assert (SETTINGS.font_groups, SETTINGS.font_provides) == (FONT_GROUPS, FONT_PROVIDES)
    assert "endeavouros" not in json.dumps(dataclasses.asdict(SETTINGS))  # gate C2 (rec)
    assert dataclasses.replace(SETTINGS, not_fonts="").not_fonts == ""  # none left out


def test_distros_are_package_systems() -> None:
    """corrections names the system that pulls a font in from the ``distro`` attr."""
    systems = load_config(Paths.for_root(ROOT)).site.package_systems
    assert set(DISTROS) <= set(systems)
    assert {r.distro for r in SETTINGS.repos} == set(DISTROS)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"repos": ()}, "at least one"),
        ({"repos": (DEFAULT_REPOS[0], DEFAULT_REPOS[0])}, "repeat"),
        ({"repos": (Repo("Core", "arch", DEFAULT_REPOS[0].url),)}, "lower case"),
        ({"repos": (Repo("eos", "endeavouros", DEFAULT_REPOS[0].url),)}, "distro"),
        ({"repos": (Repo("x", "arch", "https://mirror.example.org/x.db"),)}, "https URL on"),
        ({"repos": (Repo("x", "arch", "http://geo.mirror.pkgbuild.com/x.db"),)}, "https URL"),
        ({"font_pattern": "(ttf"}, "font_pattern"),
        ({"not_fonts": "[a-"}, "not_fonts"),
        ({"font_pattern": ""}, "font_pattern: '' matches every package name"),
        ({"font_provides": "x*"}, "font_provides: 'x\\*' matches every"),
        ({"not_fonts": "^(lib)?"}, "not_fonts: .* matches every"),
        ({"min_fonts": -1}, "min_fonts"),
        ({"min_share": 1.5}, "min_share"),
    ],
)
def test_settings_refuse_bad_values(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        dataclasses.replace(SETTINGS, **change)


# --- reading databases ------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["gz", "zst", "xz", ""])
def test_compression_comes_from_the_magic_bytes(how: str, tmp_path: Path) -> None:
    data = build_fixture.compress(build_fixture.tar_bytes(build_fixture.ARCH["core"]), how)
    assert compression(data[:512]) == how
    path = tmp_path / "core.files"  # the name says nothing
    path.write_bytes(data)
    assert sorted(read_db(path)) == ["filesystem", "glibc"]


def test_bzip2_is_recognised() -> None:
    assert compression(b"BZh91AY&SY") == "bz2"


@pytest.mark.parametrize(
    "body", [b"<html><body>502 Bad Gateway</body></html>", b"", b"\x1f\x8b\x08\x00trunc"]
)
def test_a_body_that_is_not_a_database_fails(body: bytes, tmp_path: Path) -> None:
    path = tmp_path / "extra.db"
    path.write_bytes(body)
    with pytest.raises(ValueError, match=r"extra\.db|not a pacman database"):
        read_db(path)


def test_an_empty_database_fails(tmp_path: Path) -> None:
    path = tmp_path / "core.db"
    path.write_bytes(build_fixture.compress(build_fixture.tar_bytes([]), "gz"))
    with pytest.raises(ValueError, match="lists no package"):
        read_db(path)


def test_old_style_depends_files_are_merged(tmp_path: Path) -> None:
    path = tmp_path / "cachyos.db"
    path.write_bytes(build_fixture.compress(build_fixture.tar_bytes(build_fixture.CACHYOS), "zst"))
    st = read_db(path)["synth-st-config"]
    assert st["DEPENDS"] == ["ttf-hack", "ttf-fira-code", "ttf-jetbrains-mono"]
    assert st["LICENSE"] == ["MIT"]


def test_parse_desc() -> None:
    text = "%NAME%\nfoo\n\n%DEPENDS%\nbar>=1\n  baz  \n\n%EMPTY%\n\nstray\n%DESC%\nA %X% font\n"
    assert parse_desc(text) == {
        "NAME": ["foo"],
        "DEPENDS": ["bar>=1", "baz"],
        "EMPTY": [],
        "DESC": ["A %X% font"],
    }


@pytest.mark.parametrize(
    ("entry", "name"),
    [
        ("ttf-font", "ttf-font"),
        ("ttf-dejavu>=2.37", "ttf-dejavu"),
        ("ttf-dejavu<3", "ttf-dejavu"),
        ("noto-fonts=1:2026.09.01-1", "noto-fonts"),
        ("noto-fonts: Recommended font", "noto-fonts"),
        ("powerline-fonts>=2.8: prompt glyphs", "powerline-fonts"),
        ("  libstdc++  ", "libstdc++"),
        ("", ""),
    ],
)
def test_dep_name(entry: str, name: str) -> None:
    assert dep_name(entry) == name


@pytest.mark.parametrize(
    ("name", "fields", "font"),
    [
        ("ttf-dejavu", {}, True),
        ("otf-font-awesome", {}, True),
        ("ttc-iosevka", {}, True),
        ("woff2-font-awesome", {}, True),
        ("noto-fonts-emoji", {}, True),
        ("inter-font", {}, True),
        ("xorg-fonts-misc", {}, True),
        ("gsfonts", {}, True),
        ("wqy-microhei", {}, True),
        ("glyphs-synth", {"GROUPS": ["nerd-fonts"]}, True),
        ("iosevka-synth-bin", {"PROVIDES": ["ttf-font"]}, True),
        ("ttf-helper-synth", {"PROVIDES": ["ttf-font-nerd=3"]}, True),
        ("fontconfig", {"PROVIDES": ["libfontconfig.so=1-64"]}, False),
        ("woff2", {}, False),
        ("sdl2_ttf", {}, False),
        ("perl-font-ttf", {}, False),
        ("lib32-fontconfig", {}, False),
        ("font-manager", {}, False),
        ("gnome-font-viewer", {}, False),
        ("xorg-font-util", {}, False),
        ("xorg-fonts-encodings", {"GROUPS": ["xorg-fonts"]}, False),
        ("xorg-fonts-alias-misc", {}, False),
        ("firefox", {"DEPENDS": ["ttf-font"]}, False),
        ("sdl2_ttf", {"PROVIDES": ["ttf-font"]}, False),  # not_fonts wins
    ],
)
def test_is_font(name: str, fields: dict[str, list[str]], font: bool) -> None:
    assert is_font({"NAME": [name], **fields}, SETTINGS) is font


# --- fetch ------------------------------------------------------------------------------------


def test_fixture_databases_match_build_fixture() -> None:
    """http/*.db hold exactly the packages build_fixture lists (rebuild it after edits)."""
    for repo, packages in build_fixture.databases().items():
        data = (HTTP / f"{repo}.db").read_bytes()
        plain = zstd.decompress(data) if repo == "cachyos" else gzip.decompress(data)
        assert plain == build_fixture.tar_bytes(packages), repo
    assert len([p for packages in build_fixture.ARCH.values() for p in packages]) <= 50


def test_fetch_keeps_fonts_in_full_and_dependents_trimmed(tmp_path: Path) -> None:
    snap = fetch(tmp_path)
    extra = rows(snap, "extra")
    assert extra["ttf-fantasque-nerd"] == {
        "name": "ttf-fantasque-nerd",
        "base": "nerd-fonts",
        "version": "3.5.1-2",
        "url": None,
        "license": ["OFL-1.1-no-RFN"],
        "groups": ["nerd-fonts"],
        "provides": ["ttf-font-nerd"],
        "replaces": ["nerd-fonts-fantasque-sans-mono"],
        "depends": [],
        "optdepends": [],
        "font": True,
    }
    firefox = extra["firefox"]
    assert (firefox["depends"], firefox["optdepends"], firefox["font"]) == (["ttf-font"], [], False)
    assert (firefox["url"], firefox["license"], firefox["groups"]) == (None, [], [])
    assert extra["rofimoji"]["optdepends"] == [
        "emoji-font: for the emojis character file",
        "otf-font-awesome: for the fontawesome6 character file",
        "woff2-font-awesome: for the fontawesome6 character file",
    ]
    # Not fonts, and pulling in no font: left out.
    for name in ("woff2", "sdl2_ttf", "hedgewars", "font-manager", "fontconfig", "glibc"):
        assert name not in extra
    assert list(rows(snap, "core")) == []
    assert list(rows(snap, "multilib")) == ["steam"]
    cachyos = rows(snap, "cachyos")
    assert "synth-kernel-manager" not in cachyos
    assert cachyos["synth-office-bin"]["depends"] == [
        "ttf-carlito",
        "ttf-dejavu>=2.37",
        "ttf-dejavu<3",
        "ttf-liberation",
    ]
    assert list(extra) == sorted(extra)


def test_fetch_keeps_no_packager_signature_or_email(tmp_path: Path) -> None:
    packager = build_fixture.SYNTH_PACKAGER[0].encode()
    assert packager in zstd.decompress((HTTP / "cachyos.db").read_bytes())  # the test bites
    snap = fetch(tmp_path)
    for entry in snap.manifest.extracts:
        data = snap.read_bytes(entry.path)
        assert not EMAIL.search(data), entry.path
        for field in (b"packager", b"PACKAGER", b"pgpsig", b"sha256sum", b"makedepends"):
            assert field not in data, (entry.path, field)
        for value in (packager, b"c3ludGhldGljIHNpZ25hdHVyZQ==", b"synth-build-helper"):
            assert value not in data, (entry.path, value)


def test_fixture_holds_no_email_address() -> None:
    """No email address anywhere in the fixture, not even an invented one."""
    for path in sorted(FIXTURE.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.suffix == ".db":
            data = (
                zstd.decompress(data) if data[:4] == b"\x28\xb5\x2f\xfd" else gzip.decompress(data)
            )
        elif path.suffix == ".gz":
            data = gzip.decompress(data)
        assert not EMAIL.search(data), path


def test_fetch_manifest(tmp_path: Path) -> None:
    snap = fetch(tmp_path)
    m = snap.manifest
    assert [f.url for f in m.fetched] == [r.url for r in DEFAULT_REPOS]
    assert not any(f.kept for f in m.fetched)
    assert m.data_date == date(2026, 9, 25)  # extra.db's Last-Modified, the newest
    assert sorted(e.path for e in m.extracts) == sorted(
        [REPOS] + [r.name + EXTRACT_SUFFIX for r in DEFAULT_REPOS]
    )
    listed = {r["name"]: r for r in snap.load_json(REPOS)}
    assert listed["extra"] == {
        "name": "extra",
        "distro": "arch",
        "url": DEFAULT_REPOS[1].url,
        "extract": "extra.jsonl.gz",
        "packages": 38,
        "fonts": 25,
        "dependents": 7,
    }
    assert (listed["cachyos"]["distro"], listed["cachyos"]["fonts"]) == ("cachyos", 4)


def test_fetch_fails_below_min_fonts(tmp_path: Path) -> None:
    settings = dataclasses.replace(SETTINGS, min_fonts=30)
    with pytest.raises(ValueError, match="only 29 font-like packages"):
        fetch(tmp_path, settings=settings)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, build_fixture.DAY) is None


def test_fetch_fails_when_the_font_list_shrinks(tmp_path: Path) -> None:
    dbs = build_fixture.databases()
    kept = [p for p in dbs["extra"] if not p[0]["NAME"][0].startswith("ttf-")]
    # Non-font stand-ins keep extra's package count, so only the font check can fire.
    fillers = [build_fixture.pkg(f"synth-filler-{i}", "1-1") for i in range(14)]
    dbs["extra"] = kept + fillers
    assert len(dbs["extra"]) == 38
    http = tmp_path / "http"
    build_fixture.write_http(http, dbs)
    settings = dataclasses.replace(SETTINGS, min_fonts=0)
    with pytest.raises(ValueError, match="15 font-like packages, down from 29"):
        fetch(tmp_path, http=http, previous=SNAPSHOT, settings=settings)
    fetch(tmp_path / "ok", previous=SNAPSHOT)  # the same count passes


def test_check_counts() -> None:
    check_counts(100, None, SETTINGS)
    check_counts(80, 100, SETTINGS)
    with pytest.raises(ValueError, match="min_fonts"):
        check_counts(19, None, SETTINGS)
    with pytest.raises(ValueError, match="min_share"):
        check_counts(79, 100, SETTINGS)


def test_fetch_fails_when_a_repository_shrinks(tmp_path: Path) -> None:
    """A half-synced CachyOS database fails even though every font-like package is there."""
    dbs = build_fixture.databases()
    gone = {"synth-kernel-manager", "synth-browser-bin", "synth-office-bin"}
    dbs["cachyos"] = [p for p in dbs["cachyos"] if p[0]["NAME"][0] not in gone]
    assert sum(is_font(p[0], SETTINGS) for p in dbs["cachyos"]) == 4  # the fonts all stay
    http = tmp_path / "http"
    build_fixture.write_http(http, dbs)
    with pytest.raises(ValueError, match="cachyos lists 7 packages, down from 10"):
        fetch(tmp_path, http=http, previous=SNAPSHOT)
    fetch(tmp_path / "first", http=http)  # nothing to compare with: it passes


def test_check_packages() -> None:
    check_packages({"extra": 80, "new": 1}, {"extra": 100, "gone": 5}, SETTINGS)
    with pytest.raises(ValueError, match="extra lists 79 packages"):
        check_packages({"extra": 79}, {"extra": 100}, SETTINGS)


@pytest.mark.parametrize(
    "listed",
    [None, "not a list", [{"name": "extra"}], [{"name": "extra", "fonts": "x", "packages": []}]],
)
def test_an_unreadable_previous_snapshot_skips_the_checks(listed: object, tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    with store.writer(COLLECTOR.name, build_fixture.DAY, COLLECTOR.version) as out:
        out.write_json("other.json", {})
        if listed is not None:
            out.write_json(REPOS, listed)
    snap = store.snapshot(COLLECTOR.name, build_fixture.DAY)
    assert snap is not None
    assert (previous_fonts(snap), previous_packages(snap)) == (None, {})
    assert (previous_fonts(None), previous_packages(None)) == (None, {})


def test_previous_counts() -> None:
    assert previous_fonts(SNAPSHOT) == 29
    assert previous_packages(SNAPSHOT) == {"core": 2, "extra": 38, "multilib": 2, "cachyos": 10}


@pytest.mark.parametrize(
    ("changes", "day"),
    [
        ({"extra": "Sat, 26 Sep 2026 00:10:00 GMT"}, build_fixture.DAY),  # a mirror clock ahead
        ({"extra": None, "core": None}, date(2026, 9, 24)),  # the newest left: multilib's
    ],
)
def test_data_date(changes: dict[str, str | None], day: date, tmp_path: Path) -> None:
    answers = dict(build_fixture.HEADERS)
    for repo, modified in changes.items():
        answers[repo] = (modified, answers[repo][1])
    http = tmp_path / "http"
    build_fixture.write_http(http, answers=answers)
    assert fetch(tmp_path, http=http).manifest.data_date == day


def test_data_date_is_the_run_date_without_last_modified(tmp_path: Path) -> None:
    http = tmp_path / "http"
    answers = {repo: (None, etag) for repo, (_, etag) in build_fixture.HEADERS.items()}
    build_fixture.write_http(http, answers=answers)
    assert fetch(tmp_path, http=http).manifest.data_date == build_fixture.DAY


@pytest.mark.parametrize(
    ("value", "day"),
    [
        ("Fri, 25 Sep 2026 04:50:18 GMT", date(2026, 9, 25)),
        ("Fri, 25 Sep 2026 23:30:00 -0500", date(2026, 9, 26)),
        ("Fri, 25 Sep 2026 01:00:00 -0000", date(2026, 9, 25)),  # naive: read as UTC
        ("yesterday", None),
        ("", None),
        (None, None),
    ],
)
def test_last_modified_day(
    value: str | None, day: date | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day never depends on the machine's time zone (UTC+14 here)."""
    try:
        with monkeypatch.context() as m:
            m.setenv("TZ", "Pacific/Kiritimati")
            time.tzset()
            got = last_modified_day(value)
    finally:
        time.tzset()
    assert got == day


def test_font_targets_span_repositories() -> None:
    """A CachyOS package pulling in an Arch font is kept, though the font is in extra."""
    dbs = {
        "extra": {"ttf-fira-sans": {"NAME": ["ttf-fira-sans"]}},
        "cachyos": {
            "synth-settings": {"NAME": ["synth-settings"], "DEPENDS": ["ttf-fira-sans", "bash"]}
        },
    }
    out = select_rows(dbs, SETTINGS)
    assert [r["name"] for r in out["cachyos"]] == ["synth-settings"]
    assert out["cachyos"][0]["depends"] == ["ttf-fira-sans"]


# --- parse ------------------------------------------------------------------------------------


def test_dependency_edges() -> None:
    rel = relations(parse())
    arch = {"distro": "arch", "repo": "extra"}
    cachy = {"distro": "cachyos", "repo": "cachyos"}
    assert rel[("auto-multiple-choice", "depends", "ttf-linux-libertine", "extra")] == arch
    assert rel[("chromium", "depends", "ttf-liberation", "extra")] == arch
    assert rel[("adapta-gtk-theme", "optdepends", "noto-fonts", "extra")] == arch
    assert rel[("waybar", "optdepends", "otf-font-awesome", "extra")] == arch
    for font in ("ttf-fantasque-nerd", "noto-fonts", "ttf-fira-sans"):
        assert rel[("synth-kde-settings", "depends", font, "cachyos")] == cachy
    for font in ("ttf-hack", "ttf-fira-code", "ttf-jetbrains-mono"):  # an old-style entry
        assert rel[("synth-st-config", "depends", font, "cachyos")] == cachy
    # Font to font: the family's own package, which corrections does not count.
    assert ("ttf-nerd-fonts-symbols", "depends", "ttf-nerd-fonts-symbols-common", "extra") in rel
    assert all(r.alt == 0 for r in parse() if isinstance(r, Relation))


def test_virtual_targets_are_flagged() -> None:
    rel = relations(parse())
    virtual = {k[:3] for k, a in rel.items() if a.get("virtual") is True}
    assert virtual == {
        ("firefox", "depends", "ttf-font"),
        ("steam", "depends", "ttf-font"),
        ("synth-browser-bin", "depends", "ttf-font"),
        ("gnome-characters", "depends", "emoji-font"),
        ("rofimoji", "optdepends", "emoji-font"),
        ("synth-prompt-theme", "optdepends", "emoji-font"),
        ("synth-prompt-theme", "optdepends", "ttf-font-nerd"),
    }
    assert rel[("steam", "depends", "ttf-font", "multilib")]["distro"] == "arch"


def test_versions_are_stripped_and_repeats_collapse() -> None:
    edges = [k for k in relations(parse()) if k[0] == "synth-office-bin"]
    assert sorted(k[2] for k in edges) == ["ttf-carlito", "ttf-dejavu", "ttf-liberation"]
    assert ("synth-prompt-theme", "optdepends", "powerline-fonts", "cachyos") in relations(parse())


def test_edges_to_non_fonts_are_left_out() -> None:
    targets = {(k[0], k[2]) for k in relations(parse())}
    for pair in [
        ("rofimoji", "nerd-fonts"),  # a group name, not a package
        ("rofimoji", "rofi"),
        ("ttf-aster-mono", "fontconfig"),
        ("synth-kde-settings", "synth-cursor-theme"),
        ("wqy-zenhei", "sh"),
        ("steam", "xorg-fonts-misc"),  # a font, but not in this fixture's databases
    ]:
        assert pair not in targets


def test_group_and_provides_edges() -> None:
    rel = relations(parse())
    members = {k[2] for k in rel if k[1] == "group"}
    assert members == {
        "ttf-fantasque-nerd",
        "ttf-jetbrains-mono-nerd",
        "ttf-nerd-fonts-symbols",
        "glyphs-synth",
    }
    assert {k[0] for k in rel if k[1] == "group"} == {"nerd-fonts"}
    assert ("adobe-source-sans-fonts", "provides", "adobe-source-sans-pro-fonts", "extra") in rel
    assert ("ttf-aster-mono", "provides", "aster-mono-font", "cachyos") in rel
    providers = {k[0] for k in rel if k[1:3] == ("provides", "ttf-font")}
    assert providers == {
        "ttf-dejavu",
        "ttf-liberation",
        "noto-fonts",
        "ttf-roboto",
        "ttf-aster-mono",
        "iosevka-synth-bin",
    }
    # Dependents' own provides are not font facts.
    assert ("synth-kde-settings", "provides", "synth-desktop-settings", "cachyos") not in rel


def test_license_facts() -> None:
    lic = licenses(parse())
    assert [f.raw for f in lic[("ttf-linux-libertine", "extra")]] == ["GPL", "custom:OFL"]
    assert [f.raw for f in lic[("iosevka-synth-bin", "cachyos")]] == ["OFL-1.1"]  # repeated once
    assert [f.raw for f in lic[("gsfonts", "extra")]] == [
        "AGPL-3.0-only WITH PS-or-PDF-font-exception-20170817"
    ]
    rfn = {k[0]: facts[0].rfn for k, facts in lic.items()}
    assert (rfn["otf-monaspace-nerdfonts"], rfn["ttf-aster-mono"]) == (True, True)
    assert (rfn["noto-fonts"], rfn["ttf-fira-code"]) == (False, None)
    assert all(f.spdx is None for facts in lic.values() for f in facts)
    # Both builds of ttf-hack, told apart by repo; dependents carry no license facts.
    assert ("ttf-hack", "extra") in lic
    assert ("ttf-hack", "cachyos") in lic
    assert not {k[0] for k in lic} & {"firefox", "synth-kde-settings", "steam", "woff2"}


def test_every_key_is_an_arch_package() -> None:
    for r in parse():
        keys = (r.subject, r.object) if isinstance(r, Relation) else (r.key,)
        assert all(k.ns == "arch-pkg" for k in keys)
        assert dict(r.attrs)["distro"] in ("arch", "cachyos")


def test_parse_skips_missing_extracts_and_nameless_rows(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = Store(tmp_path / "store")
    with store.writer(COLLECTOR.name, build_fixture.DAY, COLLECTOR.version) as out:
        out.write_json(
            REPOS,
            [
                {"name": "extra", "distro": "arch", "extract": "extra.jsonl.gz"},
                {"name": "gone", "distro": "arch", "extract": "gone.jsonl.gz"},
            ],
        )
        out.write_jsonl(
            "extra.jsonl.gz",
            [
                {"name": "", "font": True, "license": ["MIT"]},
                {"font": True, "license": ["MIT"]},
                {"name": "ttf-x", "font": True, "license": ["MIT"], "provides": ["ttf-font"]},
                {"name": "app", "font": False, "depends": ["ttf-font", "ttf-x>=1"]},
            ],
        )
    snap = store.snapshot(COLLECTOR.name, build_fixture.DAY)
    assert snap is not None
    with caplog.at_level(logging.WARNING):
        recs = parse(snap)
    assert "gone" in caplog.text
    rel = relations(recs)
    assert set(rel) == {
        ("ttf-x", "provides", "ttf-font", "extra"),
        ("app", "depends", "ttf-font", "extra"),
        ("app", "depends", "ttf-x", "extra"),
    }
    assert rel[("app", "depends", "ttf-font", "extra")]["virtual"] is True
    assert "virtual" not in rel[("app", "depends", "ttf-x", "extra")]
    assert set(licenses(recs)) == {("ttf-x", "extra")}


def test_parse_is_independent_of_the_font_settings() -> None:
    """The font-like choice is made at fetch time and read from the extract's flag."""
    other = dataclasses.replace(SETTINGS, font_pattern="^nothing$", font_groups=())
    ctx = ParseContext(snapshot=SNAPSHOT, settings=other, log=LOG)
    assert list(COLLECTOR.parse(ctx)) == parse()


# --- the real databases -----------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real fetch of the four databases (about 10 MB), parsed offline."""
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
    assert snap.manifest.data_date is not None
    assert snap.manifest.data_date <= clock.utc_now().astimezone(UTC).date()
    listed = {r["name"]: r for r in snap.load_json(REPOS)}
    assert listed["extra"]["packages"] > 10_000
    assert sum(r["fonts"] for r in listed.values()) >= 150
    for entry in snap.manifest.extracts:
        assert not EMAIL.search(snap.read_bytes(entry.path)), entry.path
    recs = COLLECTOR.parse(ParseContext(snapshot=snap, settings=SETTINGS, log=LOG))
    rel = relations(recs)
    nerd = {k[2] for k in rel if k[:2] == ("nerd-fonts", "group")}
    assert len(nerd) >= 50
    assert "ttf-firacode-nerd" in nerd
    assert rel[("firefox", "depends", "ttf-font", "extra")]["virtual"] is True
    kde = {k[2] for k in rel if k[:2] == ("cachyos-kde-settings", "depends")}
    assert {"ttf-fantasque-nerd", "ttf-fira-sans", "noto-fonts"} <= kde
    assert rel[("cachyos-kde-settings", "depends", "ttf-fira-sans", "cachyos")]["distro"] == (
        "cachyos"
    )
    assert SourceKey("arch-pkg", "ttf-dejavu") in {
        r.key for r in recs if isinstance(r, LicenseFact)
    }


def test_mockhttp_fixture_lists_every_repository() -> None:
    mock = mockhttp.MockHTTP.from_dir(HTTP)
    assert {r.url for r in mock.responses} == {r.url for r in DEFAULT_REPOS}
    for rec in mock.responses:
        assert rec.header("last-modified")
        assert rec.header("etag")
