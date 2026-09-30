"""The generic collector contract (design-m1 §2.3), run for every discovered collector.

For collector ``c`` and its fixture ``tests/fixtures/collectors/<name>/`` (layout
in ``tests/helpers/regen.py``), each check is one test id ``<name>-<check>``:

- ``identity``: the name matches its module; kind, version, hosts, emits and
  group are well formed (a ranking collector's group is in ``GROUPS``); the
  fixture directory is complete; ``Settings`` is a frozen dataclass with
  ``enabled: bool`` and loads strictly from ``config/sources/<name>.toml``.
- ``fetch``: ``fetch()`` against the recorded responses (and local git
  remotes), offline, with the clock frozen: the manifest is valid and lists
  every requested URL with its sha256; extracts match their hashes and fit the
  size limits; no host outside ``hosts`` is contacted.
- ``parse``: ``parse()`` of the fixture snapshot, run twice, gives identical
  bytes, equal to ``expected.jsonl``.
- ``records``: every record is of a type in ``emits``, comes from this source,
  is valid against ``schemas/stage/records.schema.json``, uses namespaces from
  ``NAMESPACES``, survives a JSON round trip and is not duplicated; reserved
  attrs (``RESERVED_ATTRS``) have the agreed kind, and the Observations an
  engine source dates its exposure from carry ``first_seen``.
- ``end_to_end``: ``fetch()`` then ``parse()``, offline, equals ``expected.jsonl``.

While there are no collectors the parameter set is empty, which pytest reports
as one skip. The self-tests below run every check against a small fake
collector, working and broken, so the checks are known to bite before the
first collector lands. ``run_fetch`` needs the real store and fetcher (M1
step 3), so the fake exercises the checks on fetch output, not the fetch run.
"""

import contextlib
import dataclasses
import gzip
import hashlib
import importlib
import json
import os
import pkgutil
import re
import socket
import subprocess
import sys
import textwrap
import typing
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from jsonschema import Draft202012Validator
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock, jsonio, store
from tff_catalog.collectors import discover
from tff_catalog.collectors import ranking as ranking_pkg
from tff_catalog.collectors import universe as universe_pkg
from tff_catalog.collectors.base import Collector, FetchContext, load_settings
from tff_catalog.config import load_config
from tff_catalog.config_model import ConfigError, SourceBase
from tff_catalog.fetch import Budget, Fetcher, host_allowed
from tff_catalog.paths import Paths
from tff_catalog.records import (
    EXPOSURE_ATTR_KINDS,
    GROUPS,
    NAMESPACES,
    RECORD_TYPES,
    Observation,
    Record,
    Relation,
    SourceKey,
    attr_problems,
    from_json,
    to_json,
    write_jsonl,
)
from tff_catalog.store import MANIFEST_NAME, ExtractRecord, Manifest, RawDir, Snapshot, Store

CHECKS = ("identity", "fetch", "parse", "records", "end_to_end")
KINDS = ("universe", "ranking", "license")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
# fetch's narrow pattern: "*" in the leftmost label only, beside literal text, then a
# domain of two or more labels (doc-*-sheets.googleusercontent.com).
_HOST_PATTERN = re.compile(
    r"^(?=[^.]*[a-z0-9])[a-z0-9-]*\*[a-z0-9*-]*(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?){2,}$"
)
RECORDS_SCHEMA = Draft202012Validator(
    json.loads((ROOT / "schemas/stage/records.schema.json").read_text(encoding="utf-8"))
)
MANIFEST_SCHEMA = Draft202012Validator(
    json.loads((ROOT / "schemas/stage/manifest.schema.json").read_text(encoding="utf-8"))
)
FIXTURE_TOKEN = "fixture-token"  # GITHUB_TOKEN during fetch; never a real one
ENGINE_SOURCES = load_config(Paths.for_root(ROOT)).ranking.sources.all()
# Placeholders that ranking.toml uses for dated series.
_SERIES_PLACEHOLDERS = {"YYYY-MM": r"[0-9]{4}-[0-9]{2}", "YYYY-Www": r"[0-9]{4}-W[0-9]{2}"}


# --- where each collector lives ------------------------------------------------------------


def locate() -> dict[str, tuple[str, str]]:
    """Collector name -> (``"universe"`` or ``"ranking"``, module name), independent of ``discover``."""
    out: dict[str, tuple[str, str]] = {}
    for pkg in (universe_pkg, ranking_pkg):
        folder = pkg.__name__.rsplit(".", 1)[1]
        for m in pkgutil.iter_modules(pkg.__path__):
            if not m.name.startswith("_"):
                module = importlib.import_module(f"{pkg.__name__}.{m.name}")
                out[module.COLLECTOR.name] = (folder, m.name)
    return out


# --- the checks: each raises AssertionError with a readable message -------------------------


def check_identity(c: Collector, folder: str, module: str) -> None:
    assert c.name == module, f"COLLECTOR.name {c.name!r} must equal its module name {module!r}"
    assert _NAME.match(c.name), f"name {c.name!r} must be a lower-case identifier"
    assert c.kind in KINDS, f"kind {c.kind!r} is not one of {KINDS}"
    assert (folder == "ranking") == (c.kind == "ranking"), (
        f"a {c.kind} collector must not live in collectors/{folder}/"
    )
    assert type(c.version) is int, "version must be an int"
    assert c.version >= 1, "version must be >= 1"
    assert type(c.needs_baseline) is bool, "needs_baseline must be a bool"
    assert isinstance(c.hosts, tuple), "hosts must be a tuple"
    for host in c.hosts:
        assert _HOST.match(host) or _HOST_PATTERN.match(host), (
            f"host {host!r} must be a bare lower-case host name, or one with '*' in its "
            "leftmost label only"
        )
    assert len(set(c.hosts)) == len(c.hosts), "hosts has duplicates"
    assert isinstance(c.emits, tuple), "emits must be a tuple"
    assert c.emits, "emits must not be empty"
    known = set(RECORD_TYPES.values())
    for t in c.emits:
        assert t in known, f"emits {t!r}, which is not a record type"
    if c.kind == "ranking":
        assert c.group in GROUPS, f"ranking collector group {c.group!r} is not in GROUPS"
    else:
        assert c.group is None or c.group in GROUPS, f"group {c.group!r} is not in GROUPS"
    settings = c.Settings
    assert isinstance(settings, type), "Settings must be a class"
    assert dataclasses.is_dataclass(settings), "Settings must be a dataclass"
    assert settings.__dataclass_params__.frozen, "Settings must be a frozen dataclass"
    hints = typing.get_type_hints(settings)
    assert hints.get("enabled") is bool, (
        "Settings needs a field `enabled: bool` (the stages run enabled collectors only)"
    )


def check_fixture(fixture: Path) -> None:
    assert fixture.is_dir(), f"missing fixture directory {fixture}"
    for part in ("snapshot/" + MANIFEST_NAME, regen.EXPECTED):
        assert (fixture / part).is_file(), f"fixture lacks {part}"
    assert (fixture / "http" / mockhttp.INDEX).is_file() or (fixture / "git").is_dir(), (
        "fixture needs http/index.json or git/ for fetch()"
    )
    notice = fixture / "NOTICE"
    assert notice.is_file(), "fixture needs a NOTICE naming the source and license, or 'Synthetic'"
    assert notice.read_text(encoding="utf-8").strip(), "the fixture's NOTICE is empty"


def check_settings(c: Collector, paths: Paths) -> object:
    try:
        settings = load_settings(c, paths)
    except ConfigError as exc:
        raise AssertionError(f"Settings do not load strictly: {exc}") from exc
    assert isinstance(settings, c.Settings)
    return settings


def check_parse(c: Collector, snapshot: Snapshot, settings: object, expected: Path) -> bytes:
    first = regen.encode(regen.parse_records(c, snapshot, settings))
    second = regen.encode(regen.parse_records(c, snapshot, settings))
    assert first == second, "parse() is not deterministic: two runs gave different records"
    assert expected.is_file(), (
        f"missing {expected}; run: uv run python -m tests.helpers.regen {c.name}"
    )
    assert_same_lines(first, expected.read_bytes(), c.name)
    return first


def assert_same_lines(got: bytes, want: bytes, name: str) -> None:
    if got == want:
        return
    got_lines, want_lines = got.splitlines(), want.splitlines()
    for i, (g, w) in enumerate(zip(got_lines, want_lines, strict=False), start=1):
        if g != w:
            detail = f"line {i}:\n  got:  {g.decode()}\n  want: {w.decode()}"
            break
    else:
        detail = f"{len(got_lines)} lines, expected.jsonl has {len(want_lines)}"
    raise AssertionError(
        f"parse() output differs from expected.jsonl ({detail}); if the change is intended, "
        f"run: uv run python -m tests.helpers.regen {name}"
    )


def _keys(r: Record) -> tuple[SourceKey, ...]:
    return (r.subject, r.object) if isinstance(r, Relation) else (r.key,)


def first_seen_series(c: Collector, sources: dict[str, SourceBase]) -> list[re.Pattern[str]]:
    """Series of ``c`` whose Observations must carry ``first_seen`` (records.RESERVED_ATTRS)."""
    out = []
    for src in sources.values():
        if src.collector == c.name and src.enabled and src.exposure in EXPOSURE_ATTR_KINDS:
            pattern = re.escape(src.series)
            for placeholder, regex in _SERIES_PLACEHOLDERS.items():
                pattern = pattern.replace(re.escape(placeholder), regex)
            out.append(re.compile(pattern))
    return out


def check_records(
    c: Collector, recs: list[Record], sources: dict[str, SourceBase] | None = None
) -> None:
    """``sources`` are the engine sources (default: ``config/ranking.toml``'s)."""
    assert recs, "parse() returned no records; the fixture must exercise the parser"
    dated = first_seen_series(c, ENGINE_SOURCES if sources is None else sources)
    for r in recs:
        kind = type(r).__name__
        assert type(r) in c.emits, f"{kind} is not in emits {[t.__name__ for t in c.emits]}"
        assert r.source == c.name, f"{kind}.source is {r.source!r}, expected {c.name!r}"
        for key in _keys(r):
            assert key.ns in NAMESPACES, f"{kind} uses namespace {key.ns!r}, not in NAMESPACES"
        try:
            back = from_json(to_json(r))
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"{kind} does not survive a JSON round trip: {exc}") from exc
        assert back == r, (
            f"{kind} changes in a JSON round trip (use tuples and records.attrs()): {r!r}"
        )
        errors = sorted(RECORDS_SCHEMA.iter_errors(to_json(r)), key=str)
        assert not errors, f"{kind} fails the records schema: {errors[0].message} in {r!r}"
        problems = attr_problems(r.attrs)
        assert not problems, f"{kind} has a reserved attr of the wrong kind: {problems[0]}"
        if isinstance(r, Observation) and any(p.fullmatch(r.series) for p in dated):
            assert "first_seen" in dict(r.attrs), (
                f"Observation of series {r.series!r} needs attrs first_seen: an engine source "
                f"dates its exposure from it (records.RESERVED_ATTRS): {r!r}"
            )
    lines = Counter(jsonio.canonical_bytes(to_json(r)) for r in recs)
    dupes = [line.decode() for line, n in lines.items() if n > 1]
    assert not dupes, f"duplicate records: {dupes[:3]}"


def check_fetch_output(
    c: Collector, snapshot_dir: Path, requested: list[str], unmatched: list[str]
) -> None:
    assert not unmatched, f"requests with no recorded response: {unmatched}"
    for url in requested:
        host = urlsplit(url).hostname
        assert host is not None, f"no host in {url}"
        assert host_allowed(host, c.hosts), f"contacted {host!r}, which is not in hosts {c.hosts}"
    manifest = jsonio.load(snapshot_dir / MANIFEST_NAME)
    errors = sorted(MANIFEST_SCHEMA.iter_errors(manifest), key=str)
    assert not errors, f"manifest fails its schema: {errors[0].message}"
    assert manifest["source"] == c.name, "manifest names another source"
    assert manifest["collector_version"] == c.version, "manifest collector_version != version"
    assert manifest["complete"] is True, "snapshot is not complete"
    listed = {mockhttp.normalize_url(f["url"]): f for f in manifest["fetched"]}
    for url in requested:
        assert url in listed, f"manifest does not list the request {url}"
        entry = listed[url]
        assert entry["sha256"] or entry["status"] == 304, f"manifest has no sha256 for {url}"
    total = 0
    for e in manifest["extracts"]:
        path = snapshot_dir / e["path"]
        assert path.is_file(), f"extract {e['path']} is missing"
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == e["sha256"], f"extract {e['path']}: sha256"
        assert len(data) == e["bytes"], f"extract {e['path']}: bytes"
        assert len(data) <= store.EXTRACT_MAX_BYTES, (
            f"extract {e['path']} is {len(data)} bytes, over the {store.EXTRACT_MAX_BYTES} limit"
        )
        total += len(data)
    assert total <= store.SNAPSHOT_MAX_BYTES, (
        f"snapshot is {total} bytes, over the {store.SNAPSHOT_MAX_BYTES} limit"
    )
    on_disk = {
        p.relative_to(snapshot_dir).as_posix() for p in snapshot_dir.rglob("*") if p.is_file()
    }
    stray = on_disk - {MANIFEST_NAME} - {e["path"] for e in manifest["extracts"]}
    assert not stray, f"files in the snapshot that the manifest does not list: {sorted(stray)}"


# --- running fetch() offline ------------------------------------------------------------------


def run_fetch(
    c: Collector, fixture: Path, paths: Paths, tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Snapshot, mockhttp.MockHTTP]:
    """Run ``c.fetch()`` against the fixture's recorded network into a fresh store.

    A fixture with a ``root/`` directory is the repository ``fetch()`` sees
    (``FetchContext.paths``), for a collector whose input list lives in
    ``config/``; its settings still come from ``paths``.
    """
    settings = load_settings(c, paths)
    root = fixture / "root"
    day = date.fromisoformat(jsonio.load(fixture / "snapshot" / MANIFEST_NAME)["date"])
    http = fixture / "http"
    mock = mockhttp.MockHTTP.from_dir(http) if (http / mockhttp.INDEX).is_file() else None
    mock = mock or mockhttp.MockHTTP(())
    if (fixture / "git").is_dir():
        for key, value in mockhttp.git_remotes(fixture / "git", tmp / "remotes", day).items():
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_TOKEN", FIXTURE_TOKEN)
    previous = None
    if (fixture / "previous").is_dir():
        previous = regen.load_snapshot(fixture / "previous", c.name)
    snapshots = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
        Fetcher(
            transport=mock.transport,
            min_interval=dict.fromkeys(c.hosts, 0.0),
            budgets=(Budget("github", 5000),),
            log=regen.LOG,
        ) as fetcher,
        snapshots.writer(c.name, day, c.version) as writer,
    ):
        ctx = FetchContext(
            run_date=day,
            fetcher=fetcher.scoped(c.hosts),
            out=writer,
            raw=RawDir(tmp / "raw"),
            previous=previous,
            settings=settings,
            log=regen.LOG.getChild(c.name),
            paths=Paths.for_root(root) if root.is_dir() else paths,
        )
        c.fetch(ctx)
    snap = snapshots.snapshot(c.name, day)
    assert snap is not None, "fetch() left no complete snapshot"
    return snap, mock


# --- the parametrised contract ----------------------------------------------------------------

COLLECTORS = discover()
PARAMS = [pytest.param(n, check, id=f"{n}-{check}") for n in COLLECTORS for check in CHECKS]


@pytest.mark.parametrize(("name", "check"), PARAMS)
def test_contract(name: str, check: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = COLLECTORS[name]
    fixture = regen.fixture_dir(name)
    paths = Paths.for_root(ROOT)
    expected = fixture / regen.EXPECTED
    if check == "identity":
        folder, module = locate()[name]
        check_identity(c, folder, module)
        check_fixture(fixture)
        check_settings(c, paths)
    elif check == "fetch":
        snap, mock = run_fetch(c, fixture, paths, tmp_path, monkeypatch)
        check_fetch_output(c, snap.path, mock.urls(), mock.unmatched)
    elif check == "parse":
        snapshot = regen.load_snapshot(fixture / "snapshot", name)
        check_parse(c, snapshot, load_settings(c, paths), expected)
    elif check == "records":
        snapshot = regen.load_snapshot(fixture / "snapshot", name)
        check_records(c, regen.parse_records(c, snapshot, load_settings(c, paths)))
    else:
        snap, _ = run_fetch(c, fixture, paths, tmp_path, monkeypatch)
        got = regen.encode(regen.parse_records(c, snap, load_settings(c, paths)))
        assert_same_lines(got, expected.read_bytes(), name)


def assert_discovery_consistent() -> None:
    found = discover()
    assert list(found) == sorted(found)
    assert set(found) == set(locate())
    by_kind = {k: discover(k) for k in KINDS}
    assert sorted(n for d in by_kind.values() for n in d) == sorted(found)
    for kind, collectors in by_kind.items():
        assert all(c.kind == kind for c in collectors.values())


def test_discovery_is_consistent() -> None:
    assert_discovery_consistent()


# --- self-tests: a fake collector, working and broken ------------------------------------------

FAKE = "fake_counts"
FAKE_DAY = date(2026, 10, 3)
FAKE_URL = "https://counts.synth.example/counts.json"
FAKE_MODULE = textwrap.dedent(
    '''
    """A fake ranking collector for the contract self-tests."""

    from dataclasses import dataclass
    from datetime import timedelta

    from tff_catalog import jsonio
    from tff_catalog.collectors.base import CollectorBase
    from tff_catalog.records import Observation, SourceKey, attrs


    class FakeCounts(CollectorBase):
        name = "fake_counts"
        kind = "ranking"
        group = "homebrew"
        hosts = ("counts.synth.example",)
        emits = (Observation,)

        @dataclass(frozen=True, slots=True)
        class Settings:
            enabled: bool = True
            series: str = "365d"

        def fetch(self, ctx):
            result = ctx.fetcher.get("FAKE_URL")
            ctx.out.record_fetch(result.to_record(kept=True))
            ctx.out.write_bytes("counts.json", result.content)

        def parse(self, ctx):
            day = ctx.snapshot.date
            counts = jsonio.load(ctx.snapshot.path / "counts.json")
            for key, count in sorted(counts.items()):
                yield Observation(
                    source=self.name,
                    series=ctx.settings.series,
                    key=SourceKey("brew-cask", key),
                    value=float(count),
                    unit="installs",
                    start=day - timedelta(days=365),
                    end=day - timedelta(days=1),
                    attrs=attrs(tap="synth/fonts"),
                )


    COLLECTOR = FakeCounts()
    '''
).replace("FAKE_URL", FAKE_URL)
FAKE_COUNTS = {"font-aster-sans": 1200, "font-birch-mono": 80, "font-cobalt-serif": 5066}


@dataclass
class Fake:
    collector: Collector
    paths: Paths
    fixtures: Path  # the fake's tests/fixtures/collectors/
    fixture: Path
    snapshot: Snapshot

    @property
    def settings(self) -> object:
        return load_settings(self.collector, self.paths)


def fake_snapshot(directory: Path, source: str) -> Snapshot:
    """``regen.load_snapshot`` without ``Manifest.from_json`` (a stub until M1 step 3)."""
    m = jsonio.load(directory / MANIFEST_NAME)
    manifest = Manifest(
        schema=m["schema"],
        source=m["source"],
        date=date.fromisoformat(m["date"]),
        collector_version=m["collector_version"],
        complete=m["complete"],
        data_date=None,
        window=None,
        fetched=(),
        extracts=tuple(ExtractRecord(**e) for e in m["extracts"]),
    )
    assert manifest.source == source
    return Snapshot(source=source, date=manifest.date, path=directory, manifest=manifest)


def write_fake_snapshot(directory: Path, counts: dict[str, int], urls: list[str]) -> bytes:
    """A complete snapshot of the fake collector, as its fetch() would leave it."""
    data = jsonio.canonical_bytes(counts)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "counts.json").write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    jsonio.dump(
        {
            "schema": 1,
            "source": FAKE,
            "date": FAKE_DAY.isoformat(),
            "collector_version": 1,
            "complete": True,
            "data_date": (FAKE_DAY - timedelta(days=1)).isoformat(),
            "window": None,
            "fetched": [
                {
                    "url": url,
                    "status": 200,
                    "fetched_at": f"{FAKE_DAY.isoformat()}T06:00:00Z",
                    "sha256": sha,
                    "bytes": len(data),
                    "etag": None,
                    "last_modified": None,
                    "kept": True,
                }
                for url in urls
            ],
            "extracts": [{"path": "counts.json", "sha256": sha, "bytes": len(data), "rows": None}],
            "stale_of": None,
            "notes": [],
        },
        directory / MANIFEST_NAME,
    )
    return data


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fake]:
    """The fake collector, discoverable, with its settings file and a complete fixture."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / f"{FAKE}.py").write_text(FAKE_MODULE, encoding="utf-8")
    monkeypatch.setattr(ranking_pkg, "__path__", [*ranking_pkg.__path__, str(package)])
    module_name = f"{ranking_pkg.__name__}.{FAKE}"
    importlib.invalidate_caches()

    root = tmp_path / "root"
    (root / "config" / "sources").mkdir(parents=True)
    (root / "config" / "sources" / f"{FAKE}.toml").write_text('series = "365d"\n')
    fixtures = tmp_path / "fixtures"
    fixture = fixtures / FAKE
    data = write_fake_snapshot(fixture / "snapshot", FAKE_COUNTS, [FAKE_URL])
    (fixture / "http").mkdir()
    (fixture / "http" / "counts.json").write_bytes(data)
    mockhttp.write_index(
        fixture / "http",
        [{"url": FAKE_URL, "headers": {"content-type": "application/json"}, "body": "counts.json"}],
    )
    (fixture / "NOTICE").write_text("Synthetic data for the contract self-tests.\n")
    try:
        c = discover()[FAKE]
        snapshot = fake_snapshot(fixture / "snapshot", FAKE)
        paths = Paths.for_root(root)
        recs = regen.parse_records(c, snapshot, load_settings(c, paths))
        write_jsonl(recs, fixture / regen.EXPECTED)
        yield Fake(c, paths, fixtures, fixture, snapshot)
    finally:
        sys.modules.pop(module_name, None)


def fetched_urls(fake: Fake, urls: list[str]) -> tuple[list[str], list[str]]:
    """Request ``urls`` through the fake's recorded responses; return (requested, unmatched)."""
    mock = mockhttp.MockHTTP.from_dir(fake.fixture / "http")
    with httpx.Client(transport=mock.transport) as client:
        for url in urls:
            with contextlib.suppress(mockhttp.UnrecordedRequest):
                client.get(url)
    return mock.urls(), mock.unmatched


def test_fake_is_discovered(fake: Fake) -> None:
    assert FAKE in discover()
    assert FAKE in discover("ranking")
    assert FAKE not in discover("universe")
    assert locate()[FAKE] == ("ranking", FAKE)
    assert_discovery_consistent()


def test_checks_accept_a_working_collector(fake: Fake) -> None:
    c = fake.collector
    check_identity(c, "ranking", FAKE)
    check_fixture(fake.fixture)
    check_settings(c, fake.paths)
    got = check_parse(c, fake.snapshot, fake.settings, fake.fixture / regen.EXPECTED)
    assert got == (fake.fixture / regen.EXPECTED).read_bytes()  # encode == write_jsonl
    recs = regen.parse_records(c, fake.snapshot, fake.settings)
    assert len(recs) == len(FAKE_COUNTS)
    check_records(c, recs)
    requested, unmatched = fetched_urls(fake, [FAKE_URL])
    check_fetch_output(c, fake.snapshot.path, requested, unmatched)


def _variant(c: Collector, **changes: object) -> Collector:
    """An instance of a subclass of ``type(c)`` with some class attributes replaced."""
    return type("Variant", (type(c),), dict(changes))()


def _obs(**changes: object) -> Observation:
    base = Observation(
        source=FAKE,
        series="365d",
        key=SourceKey("brew-cask", "font-x"),
        value=1.0,
        unit="installs",
        start=FAKE_DAY - timedelta(days=365),
        end=FAKE_DAY - timedelta(days=1),
    )
    return dataclasses.replace(base, **changes)


def _dated_source(exposure: str = "add_date") -> dict[str, SourceBase]:
    """An engine source that reads the fake collector and dates exposure from first_seen."""
    source = SourceBase(
        collector=FAKE,
        group="homebrew",
        survey="desktop",
        enabled=True,
        linux=False,
        publish_raw=True,
        series="365d",
        counting="window",
        exposure=exposure,
        rate="per_year",
        floor=0.0,
        censor_below=0.0,
    )
    return {"fake": source}


def test_first_seen_is_required_only_where_exposure_needs_it(fake: Fake) -> None:
    dated = _obs(attrs=(("first_seen", "2025-05-15"), ("tap", "synth/fonts")))
    check_records(fake.collector, [dated], sources=_dated_source())
    check_records(fake.collector, [_obs()], sources=_dated_source(exposure="data_date"))
    check_records(fake.collector, [_obs()], sources={})


def _nondeterministic(fake: Fake) -> None:
    calls = iter(range(100))

    def parse(self: object, ctx: object) -> Iterator[Observation]:
        yield _obs(value=float(next(calls)))

    c = _variant(fake.collector, parse=parse)
    check_parse(c, fake.snapshot, fake.settings, fake.fixture / regen.EXPECTED)


def _stale_golden(fake: Fake) -> None:
    expected = fake.fixture / regen.EXPECTED
    expected.write_bytes(expected.read_bytes().replace(b"1200.0", b"1201.0"))
    check_parse(fake.collector, fake.snapshot, fake.settings, expected)


def _unknown_setting(fake: Fake) -> None:
    (fake.paths.sources_config / f"{FAKE}.toml").write_text('sereis = "365d"\n')
    check_settings(fake.collector, fake.paths)


def _tampered_extract(fake: Fake) -> None:
    (fake.snapshot.path / "counts.json").write_bytes(b"{}")
    check_fetch_output(fake.collector, fake.snapshot.path, *fetched_urls(fake, [FAKE_URL]))


def _stray_file(fake: Fake) -> None:
    (fake.snapshot.path / "raw-dump.json").write_bytes(b"{}")
    check_fetch_output(fake.collector, fake.snapshot.path, *fetched_urls(fake, [FAKE_URL]))


def _oversize_extract(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "EXTRACT_MAX_BYTES", 10)
    check_fetch_output(fake.collector, fake.snapshot.path, *fetched_urls(fake, [FAKE_URL]))


def _unlisted_request(fake: Fake) -> None:
    write_fake_snapshot(fake.snapshot.path, FAKE_COUNTS, urls=[])
    check_fetch_output(fake.collector, fake.snapshot.path, *fetched_urls(fake, [FAKE_URL]))


def _foreign_host(fake: Fake) -> None:
    check_fetch_output(
        fake.collector, fake.snapshot.path, [FAKE_URL, "https://elsewhere.example/x"], []
    )


def _unrecorded_url(fake: Fake) -> None:
    urls = [FAKE_URL, "https://counts.synth.example/other.json"]
    check_fetch_output(fake.collector, fake.snapshot.path, *fetched_urls(fake, urls))


BROKEN: dict[str, tuple[Callable[..., None], str]] = {
    "name-differs-from-module": (
        lambda f: check_identity(f.collector, "ranking", "other_name"),
        "module name",
    ),
    "group-not-in-GROUPS": (
        lambda f: check_identity(_variant(f.collector, group="nope"), "ranking", FAKE),
        "group",
    ),
    "universe-kind-in-ranking-folder": (
        lambda f: check_identity(_variant(f.collector, kind="universe"), "ranking", FAKE),
        "must not live",
    ),
    "host-with-scheme": (
        lambda f: check_identity(
            _variant(f.collector, hosts=("https://counts.synth.example",)), "ranking", FAKE
        ),
        "bare lower-case host",
    ),
    "emits-a-non-record": (
        lambda f: check_identity(_variant(f.collector, emits=(dict,)), "ranking", FAKE),
        "not a record type",
    ),
    "settings-not-frozen": (
        lambda f: check_identity(
            _variant(f.collector, Settings=dataclasses.make_dataclass("S", [("enabled", bool)])),
            "ranking",
            FAKE,
        ),
        "frozen",
    ),
    "settings-without-enabled": (
        lambda f: check_identity(
            _variant(
                f.collector,
                Settings=dataclasses.make_dataclass("S", [("series", str)], frozen=True),
            ),
            "ranking",
            FAKE,
        ),
        "enabled: bool",
    ),
    "unknown-settings-key": (_unknown_setting, "sereis"),
    "fixture-without-notice": (
        lambda f: (f.fixture / "NOTICE").unlink() or check_fixture(f.fixture),
        "NOTICE",
    ),
    "record-type-not-in-emits": (
        lambda f: check_records(
            f.collector,
            [Relation(FAKE, SourceKey("deb-pkg", "a"), "depends", SourceKey("deb-pkg", "b"))],
        ),
        "not in emits",
    ),
    "record-from-another-source": (
        lambda f: check_records(f.collector, [_obs(source="other")]),
        "expected 'fake_counts'",
    ),
    "unknown-namespace": (
        lambda f: check_records(f.collector, [_obs(key=SourceKey("bogus", "x"))]),
        "namespace",
    ),
    "unsorted-attrs": (
        lambda f: check_records(f.collector, [_obs(attrs=(("z", 1), ("a", 2)))]),
        "round trip",
    ),
    "schema-violation": (
        lambda f: check_records(f.collector, [_obs(series="")]),
        "schema",
    ),
    "duplicate-records": (
        lambda f: check_records(f.collector, [_obs(), _obs()]),
        "duplicate",
    ),
    "no-records": (lambda f: check_records(f.collector, []), "no records"),
    "reserved-attr-of-the-wrong-kind": (
        lambda f: check_records(f.collector, [_obs(attrs=(("samples", "12"),))]),
        "reserved attr of the wrong kind",
    ),
    "exposure-without-first-seen": (
        lambda f: check_records(f.collector, [_obs()], sources=_dated_source()),
        "needs attrs first_seen",
    ),
    "nondeterministic-parse": (_nondeterministic, "not deterministic"),
    "stale-golden-file": (_stale_golden, "differs from expected.jsonl"),
    "extract-hash-mismatch": (_tampered_extract, "sha256"),
    "file-not-in-manifest": (_stray_file, "does not list"),
    "extract-over-limit": (_oversize_extract, "limit"),
    "request-not-in-manifest": (_unlisted_request, "does not list the request"),
    "host-not-declared": (_foreign_host, "not in hosts"),
    "url-not-recorded": (_unrecorded_url, "no recorded response"),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_checks_reject_a_broken_collector(
    case: str, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    breaker, message = BROKEN[case]
    args = (fake, monkeypatch) if breaker is _oversize_extract else (fake,)
    with pytest.raises(AssertionError, match=re.escape(message)):
        breaker(*args)


def test_regen_writes_and_checks_golden_files(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(regen, "load_snapshot", fake_snapshot)
    expected = fake.fixture / regen.EXPECTED
    kwargs = {"paths": fake.paths, "base": fake.fixtures}
    assert regen.regen(FAKE, check=True, **kwargs) is False
    expected.write_bytes(b"")
    assert regen.regen(FAKE, check=True, **kwargs) is True
    assert expected.read_bytes() == b"", "--check must not write"
    assert regen.regen(FAKE, **kwargs) is True
    assert regen.regen(FAKE, check=True, **kwargs) is False
    with pytest.raises(KeyError, match="unknown collector"):
        regen.regen("no_such_collector", **kwargs)


def test_golden_files_compare_gzip_by_content(tmp_path: Path) -> None:
    """Two zlib builds (CI's and this machine's) may gzip one input to different bytes."""
    data = b'{"family": "Aster Sans"}\n' * 40
    fast, best = tmp_path / "fast.jsonl.gz", tmp_path / "best.jsonl.gz"
    fast.write_bytes(gzip.compress(data, compresslevel=1, mtime=0))
    best.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    assert fast.read_bytes() != best.read_bytes()
    assert regen.golden(fast) == regen.golden(best) == data

    manifest = regen.load_snapshot(
        regen.fixture_dir("fontsource") / "snapshot", "fontsource"
    ).manifest
    gz = next(e for e in manifest.extracts if e.path.endswith(".gz"))
    plain = next(e for e in manifest.extracts if not e.path.endswith(".gz"))

    def swap(old: ExtractRecord, new: ExtractRecord) -> Manifest:
        return dataclasses.replace(
            manifest, extracts=tuple(new if e is old else e for e in manifest.extracts)
        )

    rezipped = swap(gz, dataclasses.replace(gz, sha256="0" * 64, bytes=gz.bytes + 1))
    assert rezipped.to_json() != manifest.to_json()
    assert regen.portable(rezipped) == regen.portable(manifest)
    recounted = swap(gz, dataclasses.replace(gz, rows=gz.rows + 1))
    assert regen.portable(recounted) != regen.portable(manifest)
    unzipped_changed = swap(plain, dataclasses.replace(plain, sha256="0" * 64))
    assert regen.portable(unzipped_changed) != regen.portable(manifest)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([], "at least one collector"),
        (["--all", FAKE], "at least one collector"),
        (["no_such_collector"], "unknown collector(s) no_such_collector"),
    ],
    ids=["no-name", "name-and-all", "unknown-name"],
)
def test_regen_usage_errors(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        regen.main(argv)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_regen_all_with_no_collectors_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(regen, "discover", dict)
    assert regen.main(["--all", "--check"]) == 0
    assert "no collectors yet" in capsys.readouterr().out


# --- self-tests: the offline network helpers -------------------------------------------------


def _http_fixture(tmp_path: Path) -> mockhttp.MockHTTP:
    d = tmp_path / "http"
    d.mkdir()
    (d / "a.json").write_bytes(b'{"a": 1}')
    (d / "b.json.gz").write_bytes(gzip.compress(b'{"b": 2}', mtime=0))
    (d / "bulk.json").write_bytes(b'[{"purl": "pkg:npm/x", "dependent_repos_count": 3}]')
    (d / "head.bin").write_bytes(b"\x00\x01\x00\x00")
    mockhttp.write_index(
        d,
        [
            {
                "url": "https://api.synth.example/list?page=2&limit=10",
                "headers": {"content-type": "application/json", "etag": '"v1"'},
                "body": "a.json",
            },
            {
                "url": "https://api.synth.example/b.json",
                "headers": {"content-encoding": "gzip"},
                "body": "b.json.gz",
            },
            {
                "method": "POST",
                "url": "https://api.synth.example/bulk",
                "request_json": {"purls": ["pkg:npm/x"]},
                "body": "bulk.json",
            },
            {
                "url": "https://cdn.synth.example/font.woff2",
                "status": 206,
                "request_headers": {"range": "bytes=0-3"},
                "body": "head.bin",
            },
            {"method": "HEAD", "url": "https://cdn.synth.example/font.woff2", "body": "head.bin"},
            {"url": "https://api.synth.example/missing", "status": 404, "body": None},
        ],
    )
    return mockhttp.MockHTTP.from_dir(d)


def test_mockhttp_replays_recorded_responses(tmp_path: Path) -> None:
    mock = _http_fixture(tmp_path)
    with httpx.Client(transport=mock.transport) as client:
        r = client.get("https://API.synth.example/list", params={"limit": 10, "page": 2})
        assert (r.status_code, r.json(), r.headers["etag"]) == (200, {"a": 1}, '"v1"')
        assert client.get("https://api.synth.example/b.json").json() == {"b": 2}
        bulk = client.post("https://api.synth.example/bulk", json={"purls": ["pkg:npm/x"]})
        assert bulk.json()[0]["dependent_repos_count"] == 3
        part = client.get("https://cdn.synth.example/font.woff2", headers={"Range": "bytes=0-3"})
        assert (part.status_code, part.content) == (206, b"\x00\x01\x00\x00")
        head = client.head("https://cdn.synth.example/font.woff2")
        assert (head.status_code, head.content) == (200, b"")
        assert client.get("https://api.synth.example/missing").status_code == 404
        again = client.get(
            "https://api.synth.example/list?page=2&limit=10", headers={"If-None-Match": '"v1"'}
        )
        assert (again.status_code, again.content) == (304, b"")
    assert mock.hosts() == {"api.synth.example", "cdn.synth.example"}
    assert mock.urls()[0] == "https://api.synth.example/list?limit=10&page=2"
    assert mock.unmatched == []


def test_mockhttp_refuses_unrecorded_requests(tmp_path: Path) -> None:
    mock = _http_fixture(tmp_path)
    with httpx.Client(transport=mock.transport) as client:
        for method, url, kwargs in [
            ("GET", "https://api.synth.example/list?page=3&limit=10", {}),
            ("POST", "https://api.synth.example/bulk", {"json": {"purls": []}}),
            ("GET", "https://cdn.synth.example/font.woff2", {}),
            ("GET", "https://elsewhere.example/", {}),
        ]:
            with pytest.raises(mockhttp.UnrecordedRequest):
                client.request(method, url, **kwargs)
    assert len(mock.unmatched) == 4


def test_mockhttp_index_rejects_unknown_fields(tmp_path: Path) -> None:
    mockhttp.write_index(tmp_path, [{"url": "https://a.example/", "stauts": 200}])
    with pytest.raises(ValueError, match="stauts"):
        mockhttp.MockHTTP.from_dir(tmp_path)


def test_git_remotes_serve_fixture_repos_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "git" / "github.com" / "synth" / "fonts"
    (repo / "ofl" / "astersans").mkdir(parents=True)
    (repo / "ofl" / "astersans" / "METADATA.pb").write_text('name: "Aster Sans"\n')
    (repo / "README.md").write_text("synthetic\n")
    shas = []
    for n in (1, 2):
        env = mockhttp.git_remotes(tmp_path / "git", tmp_path / f"remotes{n}", FAKE_DAY)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        dest = tmp_path / f"clone{n}"
        git = ["git", "-c", "advice.detachedHead=false"]
        subprocess.run(
            [
                *git,
                "clone",
                "-q",
                "--filter=blob:none",
                "--sparse",
                "--depth",
                "1",
                "https://github.com/synth/fonts.git",
                str(dest),
            ],
            check=True,
        )
        subprocess.run(
            [*git, "-C", str(dest), "sparse-checkout", "set", "--no-cone", "/ofl/*/METADATA.pb"],
            check=True,
        )
        assert (dest / "ofl" / "astersans" / "METADATA.pb").read_text() == 'name: "Aster Sans"\n'
        sha = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        shas.append(sha)
    assert shas[0] == shas[1], "fixture commits must have stable shas"


GUARD_PROBE = """
import socket
import pytest

def test_swallowed():
    try:
        socket.getaddrinfo("example.com", 443)
    except AssertionError:
        pass

def test_blocked():
    socket.create_connection(("192.0.2.1", 80), timeout=1)

@pytest.mark.network
def test_marked():
    assert "_install_guard" not in socket.getaddrinfo.__qualname__

def test_guarded():
    assert "_install_guard" in socket.getaddrinfo.__qualname__
"""


def test_network_guard_blocks_outside_hosts(network_attempts: list[str]) -> None:
    with pytest.raises(AssertionError, match="not marked 'network'"):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(AssertionError, match="not marked 'network'"):
        socket.create_connection(("192.0.2.1", 80), timeout=1)
    with pytest.raises(AssertionError, match="not marked 'network'"), httpx.Client() as client:
        client.get("https://example.com/")
    assert os.environ["GIT_ALLOW_PROTOCOL"] == "file"
    assert len(network_attempts) == 3
    network_attempts.clear()


def test_network_guard_allows_loopback() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        with socket.create_connection(("localhost", port), timeout=5):
            conn, _ = server.accept()
            conn.close()


def test_network_guard_fails_swallowed_attempts(tmp_path: Path) -> None:
    probe = tmp_path / "test_probe.py"
    probe.write_text(GUARD_PROBE, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider",
         "-W", "ignore::pytest.PytestUnknownMarkWarning", "-q", "-rA",
         "--rootdir", str(tmp_path), str(probe)],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
    )  # fmt: skip
    out = result.stdout
    assert "2 failed, 2 passed" in out, out
    assert "network access attempted and swallowed" in out, out
    for outcome, name in [
        ("FAILED", "test_swallowed"),
        ("FAILED", "test_blocked"),
        ("PASSED", "test_marked"),
        ("PASSED", "test_guarded"),
    ]:
        assert re.search(rf"^{outcome} \S*test_probe\.py::{name}\b", out, re.MULTILINE), out
