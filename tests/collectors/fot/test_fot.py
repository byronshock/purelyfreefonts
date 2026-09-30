"""The fot collector: Fonts Over Time's weekly crawl, pinned to a commit.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules.

The fixture is synthetic (ruling T4): invented ``.example`` sites in
``synthetic/<week>.jsonl`` and ``synthetic/latest.csv``, shaped like the real
files. ``http/`` is derived from them (the gzipped weekly files, the listing
with their git blob shas, the branch head), so after changing them rebuild
``http/``, ``snapshot/`` and ``expected.jsonl`` with::

    uv run python -m tests.collectors.fot.test_fot
"""

import dataclasses
import gzip
import hashlib
import json
import logging
import shutil
import tempfile
from collections import defaultdict
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock, fetch, jsonio
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking import fot
from tff_catalog.collectors.ranking.fot import (
    API_HOST,
    COLLECTOR,
    GENERIC_NAMES,
    HEADER_BYTES,
    INDEX,
    RAW_HOST,
    ColumnMismatch,
    Index,
    Settings,
    Spelling,
    clean_name,
    csv_header,
    extract_week,
    family_fold,
    head_commit,
    listing_url,
    raw_url,
    ref_url,
    reusable_weeks,
    site_id,
    site_keys,
    site_of,
    weekly_files,
)
from tff_catalog.config import load_config
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record
from tff_catalog.stages import RunOptions, StageContext
from tff_catalog.state import State
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SYNTHETIC = FIXTURE / "synthetic"
SETTINGS: Settings = load_settings(COLLECTOR, Paths.for_root(ROOT))  # type: ignore[assignment]
DAY = date(2026, 10, 3)
LATER = date(2026, 11, 3)
COMMIT = "3a5f9c1e7b2d4f6a8c0e1b3d5f7a9c2e4b6d8f01"
LATER_COMMIT = "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d"
WEEKS = ("2026-W39", "2026-W40")
LOG = logging.getLogger("tests.fot")
PRIVATE_KEYS = {"domain", "final_url", "title", "description", "browser_error", "error"}


# --- building http/ from synthetic/ ------------------------------------------------------------


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def gz(rows: list[dict[str, Any]]) -> bytes:
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    return gzip.compress(text.encode(), compresslevel=9, mtime=0)


def synthetic_rows(week: str) -> list[dict[str, Any]]:
    lines = (SYNTHETIC / f"{week}.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def ref_body(commit: str) -> bytes:
    url = f"https://{API_HOST}/repos/{SETTINGS.repo}/git"
    doc = {
        "ref": f"refs/heads/{SETTINGS.ref}",
        "url": f"{url}/refs/heads/{SETTINGS.ref}",
        "object": {"sha": commit, "type": "commit", "url": f"{url}/commits/{commit}"},
    }
    return json.dumps(doc, indent=1).encode()


def listing_body(files: dict[str, bytes]) -> bytes:
    """A contents-API listing of the weekly folder with ``files`` (name -> bytes), plus noise."""
    entries: list[dict[str, Any]] = [
        {"name": "README.md", "path": f"{SETTINGS.weekly_dir}/README.md", "type": "file",
         "sha": git_blob(b"weekly crawls\n"), "size": 14},
        {"name": "archive", "path": f"{SETTINGS.weekly_dir}/archive", "type": "dir",
         "sha": git_blob(b""), "size": 0},
    ]  # fmt: skip
    for name, data in sorted(files.items()):
        path = f"{SETTINGS.weekly_dir}/{name}"
        entries.append(
            {
                "name": name,
                "path": path,
                "sha": git_blob(data),
                "size": len(data),
                "type": "file",
                "download_url": f"https://{RAW_HOST}/{SETTINGS.repo}/{SETTINGS.ref}/{path}",
            }
        )
    return json.dumps(entries, indent=1).encode()


@dataclasses.dataclass
class Upstream:
    """What the fake FOT repository serves at one commit."""

    commit: str = COMMIT
    weeks: dict[str, bytes] = dataclasses.field(default_factory=dict)  # week -> gz bytes
    latest: bytes = b""
    listing: bytes | None = None  # default: listing_body(weeks)

    @classmethod
    def fixture(cls) -> Upstream:
        return cls(
            weeks={w: (HTTP / f"{w}.jsonl.gz").read_bytes() for w in WEEKS},
            latest=(HTTP / "latest.csv").read_bytes(),
        )

    def write(self, directory: Path) -> Path:
        """Write a mockhttp fixture serving this commit."""
        directory.mkdir(parents=True, exist_ok=True)
        files = {f"{w}.jsonl.gz": data for w, data in self.weeks.items()}
        (directory / "ref.json").write_bytes(ref_body(self.commit))
        (directory / "latest.csv").write_bytes(self.latest[:HEADER_BYTES])
        (directory / "weekly.json").write_bytes(self.listing or listing_body(files))
        responses = [
            {"url": ref_url(SETTINGS), "body": "ref.json",
             "headers": {"content-type": "application/json; charset=utf-8"}},
            {"url": raw_url(SETTINGS, self.commit, SETTINGS.latest_csv), "status": 206,
             "request_headers": {"range": f"bytes=0-{HEADER_BYTES - 1}"}, "body": "latest.csv",
             "headers": {"content-type": "text/plain; charset=utf-8", "accept-ranges": "bytes"}},
            {"url": listing_url(SETTINGS, self.commit), "body": "weekly.json",
             "headers": {"content-type": "application/json; charset=utf-8"}},
        ]  # fmt: skip
        for name, data in sorted(files.items()):
            (directory / name).write_bytes(data)
            responses.append(
                {
                    "url": raw_url(SETTINGS, self.commit, f"{SETTINGS.weekly_dir}/{name}"),
                    "body": name,
                    "headers": {"content-type": "application/octet-stream"},
                }
            )
        mockhttp.write_index(directory, responses)
        return directory


# --- running fetch and parse offline ------------------------------------------------------------


def fetch_into(
    tmp: Path,
    http: Path,
    *,
    day: date = DAY,
    previous: Snapshot | None = None,
    settings: Settings = SETTINGS,
    requested: list[str] | None = None,
) -> tuple[Snapshot | None, list[str]]:
    """Run ``fetch()`` offline into a fresh store; return the snapshot and the requested URLs.

    ``requested``, when given, receives the requested URLs even when ``fetch()`` raises.
    """
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / f"store-{day}")
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
                    settings=settings,
                    log=LOG,
                )
            )
    finally:
        if requested is not None:
            requested.extend(mock.urls())
        assert not mock.unmatched, mock.unmatched
    return store.snapshot(COLLECTOR.name, day), mock.urls()


def snapshot() -> Snapshot:
    return regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)


def parse(snap: Snapshot | None = None, settings: Settings = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snap or snapshot(), settings)


def observations(recs: list[Record]) -> list[Observation]:
    assert all(isinstance(r, Observation) for r in recs)
    return [r for r in recs if isinstance(r, Observation)]


def totals(recs: list[Record], week: str) -> dict[str, float]:
    """Sites per name in ``week``, over every category and method."""
    out: dict[str, float] = defaultdict(float)
    for o in observations(recs):
        if o.series == week and o.value is not None:
            out[o.key.key] += o.value
    return dict(out)


def weekly_url(week: str, commit: str = COMMIT) -> str:
    return mockhttp.normalize_url(
        raw_url(SETTINGS, commit, f"{SETTINGS.weekly_dir}/{week}.jsonl.gz")
    )


# --- settings --------------------------------------------------------------------------------------


def test_settings_agree_with_the_engine_source() -> None:
    """fot.toml repeats two values of ranking.toml [sources.fot]; they must stay equal."""
    engine = load_config(Paths.for_root(ROOT)).ranking.sources.fot
    assert SETTINGS.methods == tuple(engine.methods)
    assert SETTINGS.dominant_share == engine.dominant_share
    assert COLLECTOR.group == engine.group
    assert frozenset(SETTINGS.generic_names) == frozenset(GENERIC_NAMES)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"repo": "fontsovertime"}, "owner/name"),
        ({"ref": "ma in"}, "branch"),
        ({"weekly_dir": "../x"}, "relative path"),
        ({"keep_weeks": 0}, "at least 1"),
        ({"dominant_share": 0.0}, "above 0"),
        ({"dominant_share": 1.5}, "above 0"),
        ({"methods": []}, "distinct"),
        ({"csv_columns": ["domain", "domain"]}, "distinct"),
        ({"row_fields": ["domain", "category"]}, "must include"),
        ({"generic_names": ["System-UI"]}, "lower case"),
        ({"keep_weeks": "6"}, "integer"),
        ({"keep_week": 6}, "unknown key"),
    ],
)
def test_settings_reject_bad_values(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        from_mapping(Settings, changes, where="sources/fot.toml")


# --- the fixture itself -----------------------------------------------------------------------------


def test_http_fixture_is_the_synthetic_source() -> None:
    """http/ holds the synthetic files gzipped, listed with their git blob shas."""
    listing = weekly_files(json.loads((HTTP / "weekly.json").read_bytes()), SETTINGS)
    assert sorted(listing) == list(WEEKS)
    for week in WEEKS:
        data = (HTTP / f"{week}.jsonl.gz").read_bytes()
        assert [json.loads(x) for x in gzip.decompress(data).splitlines()] == synthetic_rows(week)
        assert (listing[week].blob, listing[week].size) == (git_blob(data), len(data))
    assert (HTTP / "latest.csv").read_bytes() == (SYNTHETIC / "latest.csv").read_bytes()
    assert head_commit(json.loads((HTTP / "ref.json").read_bytes()), SETTINGS) == COMMIT


def test_fixture_domains_are_invented() -> None:
    for week in WEEKS:
        for row in synthetic_rows(week):
            assert row["domain"].endswith(".example"), row["domain"]


# --- fetch ---------------------------------------------------------------------------------------


def test_extracts_keep_no_domain_url_title_or_description() -> None:
    snap = snapshot()
    domains = {r["domain"] for w in WEEKS for r in synthetic_rows(w)}
    for week in WEEKS:
        raw = snap.read_bytes(f"weeks/{week}.jsonl.gz")
        assert b".example" not in raw
        for row in snap.iter_jsonl(f"weeks/{week}.jsonl.gz"):
            assert set(row) == {"site", "category", "method", "body", "heading", "dominant"}
            assert not PRIVATE_KEYS & set(row)
            assert len(row["site"]) == 16
            assert row["site"] in {site_id(d) for d in domains}
    assert b".example" not in snap.read_bytes(INDEX)


def test_counted_sites_are_ok_rows_of_the_counted_methods_once_each() -> None:
    snap = snapshot()
    rows = list(snap.iter_jsonl("weeks/2026-W39.jsonl.gz"))
    ok = {
        r["domain"]
        for r in synthetic_rows("2026-W39")
        if r["status"] == "ok" and r["method"] in SETTINGS.methods
    }
    assert {r["site"] for r in rows} == {site_id(d) for d in ok}
    assert len(rows) == len(ok)  # jade-labs was crawled twice
    jade = next(r for r in rows if r["site"] == site_id("jade-labs.example"))
    assert (jade["method"], jade["body"]) == ("browser", "Inter")  # browser beats static
    assert site_id("opal-shop.example") not in {r["site"] for r in rows}  # method "wayback"
    assert [r["site"] for r in rows] == sorted(r["site"] for r in rows)
    aster = next(r for r in rows if r["site"] == site_id("aster-labs.example"))
    assert aster["dominant"] == [["Inter", 0.92], ["JetBrains Mono", 0.06]]  # 0.02 dropped


def test_index_pins_the_commit_and_counts_statuses() -> None:
    index = Index.from_json(snapshot().load_json(INDEX))
    assert index.commit == COMMIT
    w39, w40 = index.weeks
    assert (w39.week, w39.lines, w39.sites) == ("2026-W39", 19, 13)
    assert w39.status == {"blocked": 1, "error": 1, "http_error": 1, "ok": 15, "timeout": 1}
    assert (w39.crawled_from, w39.crawled_to) == (date(2026, 9, 23), date(2026, 9, 23))
    assert (w40.week, w40.sites, w40.crawled_to) == ("2026-W40", 11, date(2026, 9, 27))
    manifest = snapshot().manifest
    assert manifest.window == (date(2026, 9, 23), date(2026, 9, 27))
    assert manifest.data_date == date(2026, 9, 27)


def test_fetch_makes_three_requests_plus_one_per_new_week(tmp_path: Path) -> None:
    snap, urls = fetch_into(tmp_path, HTTP)
    assert snap is not None
    assert urls[0] == mockhttp.normalize_url(ref_url(SETTINGS))
    assert urls[1].startswith(f"https://{RAW_HOST}/{SETTINGS.repo}/{COMMIT}/")
    assert urls[3:] == [weekly_url(w) for w in WEEKS]
    assert {e.path for e in snap.manifest.extracts} == {
        INDEX,
        *(f"weeks/{w}.jsonl.gz" for w in WEEKS),
    }


def test_unchanged_weeks_are_copied_from_the_previous_snapshot(tmp_path: Path) -> None:
    upstream = Upstream.fixture()
    upstream.commit = LATER_COMMIT
    upstream.weeks["2026-W41"] = gz(synthetic_rows("2026-W40")[:3])
    snap, urls = fetch_into(
        tmp_path, upstream.write(tmp_path / "http"), day=LATER, previous=snapshot()
    )
    assert snap is not None
    assert urls[3:] == [weekly_url("2026-W41", LATER_COMMIT)]  # W39 and W40 not downloaded
    index = Index.from_json(snap.load_json(INDEX))
    assert [w.week for w in index.weeks] == ["2026-W39", "2026-W40", "2026-W41"]
    assert [w.commit for w in index.weeks] == [COMMIT, COMMIT, LATER_COMMIT]
    assert snap.read_bytes("weeks/2026-W39.jsonl.gz") == snapshot().read_bytes(
        "weeks/2026-W39.jsonl.gz"
    )
    assert index.commit == LATER_COMMIT


def test_a_week_rewritten_upstream_is_extracted_again(tmp_path: Path) -> None:
    """An opt-out rewrites published weeks: a new blob sha means a new download."""
    upstream = Upstream.fixture()
    upstream.commit = LATER_COMMIT
    upstream.weeks["2026-W39"] = gz(synthetic_rows("2026-W39")[1:])  # the first site opted out
    snap, urls = fetch_into(
        tmp_path, upstream.write(tmp_path / "http"), day=LATER, previous=snapshot()
    )
    assert snap is not None
    assert urls[3:] == [weekly_url("2026-W39", LATER_COMMIT)]
    assert any("2026-W39 changed upstream" in n for n in snap.manifest.notes)
    sites = {r["site"] for r in snap.iter_jsonl("weeks/2026-W39.jsonl.gz")}
    assert site_id("aster-labs.example") not in sites


def test_extracts_made_with_other_settings_are_not_reused(tmp_path: Path) -> None:
    settings = dataclasses.replace(SETTINGS, dominant_share=0.1)
    snap, urls = fetch_into(tmp_path, HTTP, day=LATER, previous=snapshot(), settings=settings)
    assert snap is not None
    assert urls[3:] == [weekly_url(w) for w in WEEKS]


def test_keep_weeks_keeps_the_newest(tmp_path: Path) -> None:
    settings = dataclasses.replace(SETTINGS, keep_weeks=1)
    snap, urls = fetch_into(tmp_path, HTTP, settings=settings)
    assert snap is not None
    assert urls[3:] == [weekly_url("2026-W40")]
    assert [w.week for w in Index.from_json(snap.load_json(INDEX)).weeks] == ["2026-W40"]


def test_a_changed_csv_header_fails_the_fetch_before_any_download(tmp_path: Path) -> None:
    upstream = Upstream.fixture()
    upstream.latest = upstream.latest.replace(b"body_font,heading_font", b"body,heading", 1)
    with pytest.raises(ColumnMismatch, match=r"latest\.csv columns changed"):
        fetch_into(tmp_path, upstream.write(tmp_path / "http"))


@pytest.mark.parametrize(
    ("drop", "status", "method", "message"),
    [
        ("status", "ok", "browser", "no status"),
        ("crawled_at", "timeout", "browser", "no crawled_at"),
        ("heading_font", "ok", "static", "no heading_font"),
        ("dominant", "ok", "browser", "no dominant"),
    ],
)
def test_a_weekly_row_without_a_read_field_fails_the_fetch(
    tmp_path: Path, drop: str, status: str, method: str, message: str
) -> None:
    rows = synthetic_rows("2026-W40")
    target = next(r for r in rows if r["status"] == status and r["method"] == method)
    del target[drop]
    upstream = Upstream.fixture()
    upstream.weeks["2026-W40"] = gz(rows)
    with pytest.raises(ColumnMismatch, match=message):
        fetch_into(tmp_path, upstream.write(tmp_path / "http"))


def test_a_download_that_does_not_match_its_blob_fails(tmp_path: Path) -> None:
    upstream = Upstream.fixture()
    files = {f"{w}.jsonl.gz": d for w, d in upstream.weeks.items()}
    upstream.listing = listing_body(files).replace(
        git_blob(upstream.weeks["2026-W39"]).encode(), b"0" * 40
    )
    with pytest.raises(ValueError, match="does not match blob"):
        fetch_into(tmp_path, upstream.write(tmp_path / "http"))


def test_an_oversized_weekly_file_is_refused_before_it_is_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fot, "MAX_FILE_BYTES", 1024)  # the fixture's weeks are larger
    requested: list[str] = []
    with pytest.raises(ValueError, match="over the 1024-byte limit"):
        fetch_into(tmp_path, HTTP, requested=requested)
    assert len(requested) == 3  # ref, header, listing; no weekly file
    assert not set(requested) & {weekly_url(w) for w in WEEKS}


def test_extracts_of_another_collector_version_are_not_reused() -> None:
    assert sorted(reusable_weeks(snapshot(), SETTINGS, COLLECTOR.version)) == list(WEEKS)
    assert reusable_weeks(snapshot(), SETTINGS, COLLECTOR.version + 1) == {}
    assert reusable_weeks(None, SETTINGS, COLLECTOR.version) == {}


def _line(domain: str, method: str, crawled_at: str, status: str = "ok") -> bytes:
    row = {
        "domain": domain,
        "category": "indie",
        "crawled_at": crawled_at,
        "method": method,
        "status": status,
        "body_font": "Lato",
        "heading_font": None,
        "title": "t",
    }
    if method == "browser":
        row["dominant"] = [{"family": "Lato", "share": 1}]
    return json.dumps(row).encode() + b"\n"


def test_crawl_days_are_those_of_the_rows_kept() -> None:
    """A static crawl replaced by the preferred browser crawl adds no crawl day."""
    found = extract_week(
        [
            _line("a.example", "static", "2026-09-20T23:00:00Z"),
            _line("a.example", "browser", "2026-09-21T01:00:00+00:00"),
            _line("b.example", "static", "2026-09-22T10:00:00Z"),
            _line("c.example", "browser", "2026-09-19T10:00:00Z", status="timeout"),
            b"\n",
        ],
        SETTINGS,
        "w",
    )
    assert (found.lines, len(found.rows)) == (4, 2)
    assert [r["method"] for r in found.rows if r["site"] == site_id("a.example")] == ["browser"]
    assert (found.crawled_from, found.crawled_to) == (date(2026, 9, 21), date(2026, 9, 22))
    assert found.status == {"ok": 3, "timeout": 1}


def test_a_weekly_line_that_is_not_json_fails_the_fetch() -> None:
    with pytest.raises(ValueError, match=r"w:2: not JSON"):
        extract_week([_line("a.example", "static", "2026-09-20T23:00:00Z"), b"{"], SETTINGS, "w")


def test_a_column_mismatch_leaves_the_source_stale(tmp_path: Path) -> None:
    """Through the fetch stage: the failed fetch falls back on the last good snapshot."""
    root = tmp_path / "store"
    shutil.copytree(FIXTURE / "snapshot", root / COLLECTOR.name / DAY.isoformat())
    paths = Paths.for_root(tmp_path / "repo", store=root, raw_root=tmp_path / "raw")
    paths.sources_config.mkdir(parents=True)
    shutil.copy(ROOT / "config/sources/fot.toml", paths.sources_config / "fot.toml")
    upstream = Upstream.fixture()
    upstream.latest = b"domain,category,font\n"
    mock = mockhttp.MockHTTP.from_dir(upstream.write(tmp_path / "http"))
    config = SimpleNamespace(ranking=SimpleNamespace(stale=SimpleNamespace(max_months=2)))
    with Fetcher(transport=mock.transport, min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0)) as f:
        ctx = StageContext(
            paths=paths,
            config=config,  # type: ignore[arg-type]
            state=State(),
            run_date=LATER,
            store=Store(root),
            fetcher=f,
            log=LOG,
            options=RunOptions(),
        )
        (result,) = fetch.fetch_all(ctx, {COLLECTOR.name: COLLECTOR})
    assert (result.outcome, result.snapshot, result.stale) == ("failed", DAY, True)
    assert result.error is not None
    assert "ColumnMismatch" in result.error
    assert Store(root).dates(COLLECTOR.name) == [DAY]


# --- the upstream answers ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "doc",
    [
        {},
        {"object": {"sha": "abc", "type": "commit"}},
        {"object": {"sha": COMMIT, "type": "tag"}},
        [],
    ],
)
def test_a_ref_answer_without_a_commit_is_refused(doc: object) -> None:
    with pytest.raises(ValueError, match="no commit"):
        head_commit(doc, SETTINGS)


def test_a_listing_without_weekly_files_is_refused() -> None:
    with pytest.raises(ValueError, match="no weekly files"):
        weekly_files(json.loads(listing_body({})), SETTINGS)
    with pytest.raises(ValueError, match="not a folder listing"):
        weekly_files({"message": "Not Found"}, SETTINGS)
    odd = [{"name": "2026-W99.jsonl.gz", "type": "file", "sha": COMMIT, "size": 1, "path": "x"}]
    with pytest.raises(ValueError, match="no weekly files"):
        weekly_files(odd, SETTINGS)


@pytest.mark.parametrize(
    ("data", "columns"),
    [
        (b"a,b,c\nx,y,z\n", ("a", "b", "c")),
        (b"\xef\xbb\xbfa,b\r\nx,y\r\n", ("a", "b")),
        (b"a,b", ("a", "b")),  # the whole file, shorter than the range
        (b'"a,b",c\n', ("a,b", "c")),
    ],
)
def test_csv_header(data: bytes, columns: tuple[str, ...]) -> None:
    assert csv_header(data) == columns


def test_a_csv_prefix_without_a_line_end_is_a_mismatch() -> None:
    with pytest.raises(ColumnMismatch, match="no header line"):
        csv_header(b"x" * HEADER_BYTES)


# --- parse ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Inter", "Inter"),
        ("  Work   Sans ", "Work Sans"),
        ("'Inter'", "Inter"),
        ('"DM Sans"', "DM Sans"),
        ("__Inter_d65c78", "Inter"),
        ("__IBM_Plex_Mono_46fe82", "IBM Plex Mono"),
        ("__Inter_Fallback_d65c78", None),
        ("Inter Fallback", None),  # Turbopack's next/font
        ("neue-haas-grotesk-display-fallback", None),
        ("UnsungSymbolsFallback", "UnsungSymbolsFallback"),  # no word "Fallback"
        ("Fallback", "Fallback"),
        ("__notnext", "__notnext"),
        ("system-ui", None),
        ("Sans-Serif", None),
        ("BlinkMacSystemFont", None),
        ("-apple-system", None),
        ("", None),
        ("  ", None),
        ("--", None),
        (None, None),
        (12, None),
        ("Inter-Bold", "Inter-Bold"),
    ],
)
def test_clean_name(raw: object, name: str | None) -> None:
    assert clean_name(raw) == name


@pytest.mark.parametrize(
    ("name", "fold"),
    [
        ("Inter", "inter"),
        ("Inter-Bold", "inter"),
        ("Inter Variable", "inter"),
        ("Inter var", "inter"),
        ("InterVariable", "inter"),
        ("inter-variable", "inter"),
        ("Satoshi-VF", "satoshi"),
        ("Inter Bold Variable", "inter"),
        ("Aktiv Grotesk VF Variable Regular", "aktivgrotesk"),
        ("Archivo Black", "archivo"),
        ("Roboto Condensed", "robotocondensed"),  # width words name families
        ("Inter Display", "interdisplay"),
        ("Segoe UI Variable Display", "segoeuivariabledisplay"),
        ("Variable", "variable"),
        ("VF", "vf"),
    ],
)
def test_family_fold(name: str, fold: str) -> None:
    assert family_fold(name) == fold


def _spelling(*sites: set[str]) -> Spelling:
    return Spelling.of(site_of({"category": "c", "method": "browser", "body": None, "heading": None,
                                "dominant": [[n, 1] for n in sorted(s)]}, 0.05, frozenset())
                       for s in sites)  # fmt: skip


def test_spelling_is_the_most_common_then_the_smallest() -> None:
    spelling = _spelling(
        {"Work Sans"}, {"Work Sans"}, {"WorkSans"}, {"work sans"}, {"Lato"}, {"lato"}
    )
    assert spelling("worksans") == "Work Sans"
    assert spelling("WORK SANS") == "Work Sans"
    assert spelling("lato") == "Lato"  # one site each: "Lato" < "lato"
    assert spelling.sites["Work Sans"] == 4


@pytest.mark.parametrize(
    ("names", "keys"),
    [
        ({"Inter", "Inter-Bold"}, {"Inter"}),
        ({"Inter Bold", "Inter"}, {"Inter"}),
        ({"Poppins-SemiBold"}, {"Poppins-SemiBold"}),
        ({"Inter", "Inter Tight"}, {"Inter", "Inter Tight"}),
        ({"Roboto-Regular", "Roboto-Medium"}, {"Roboto-Medium"}),  # the more common one
        ({"Archivo", "Archivo Black"}, {"Archivo"}),  # a site counts once per stripped name
        ({"Archivo Black"}, {"Archivo Black"}),
        ({"JetBrains Mono", "Roboto Mono"}, {"JetBrains Mono", "Roboto Mono"}),
        ({"Inter", "Inter Variable", "Inter var"}, {"Inter"}),
        ({"Geist Variable", "Geist Mono Variable"}, {"Geist Variable", "Geist Mono Variable"}),
        ({"Satoshi Variable", "Satoshi-Bold"}, {"Satoshi Variable"}),  # the more common one
        ({"Inter", "Inter Display"}, {"Inter", "Inter Display"}),
    ],
)
def test_site_keys_count_a_site_once_per_family(names: set[str], keys: set[str]) -> None:
    spelling = _spelling(
        names, {"Roboto-Medium"}, {"Inter Bold"}, {"Archivo Black"}, {"Satoshi Variable"}
    )
    assert site_keys(names, spelling) == frozenset(keys)


def test_parse_counts_sites_per_name() -> None:
    w39 = totals(parse(), "2026-W39")
    assert w39 == {
        "Georgia": 1.0,
        "Inter": 4.0,  # aster, birch (next/font), jade, quartz
        "Inter Tight": 1.0,
        "JetBrains Mono": 1.0,  # 0.06 of aster's text
        "Merriweather": 1.0,
        "Open Sans": 1.0,  # raven, static after a browser timeout
        "Playfair Display": 1.0,
        "Poppins": 1.0,  # harbor's heading Poppins-SemiBold folds into it
        "Poppins-SemiBold": 1.0,  # nova uses it alone: mapping decides
        "Public Sans": 1.0,
        "Roboto": 1.0,  # dune's Roboto-Bold folds into it
        "SF Mono": 1.0,  # exactly dominant_share
        "Work Sans": 2.0,  # fern's "work sans"/"WorkSans" and grove's "Work Sans"
    }
    w40 = totals(parse(), "2026-W40")
    assert "SF Mono" not in w40  # 0.04
    # cobalt: Inter, "Inter var" and "Inter Variable" count once; "Inter Fallback" not at all
    assert (w40["Inter"], w40["Geist"], w40["DM Sans"]) == (5.0, 1.0, 1.0)
    assert not {"Inter var", "Inter Variable", "Inter Fallback"} & set(w40)


def test_parse_warns_when_the_extracts_kept_fewer_shares(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Shares under the extracts' dominant_share were never kept, so a lower one changes nothing."""
    with caplog.at_level(logging.WARNING):
        recs = parse(settings=dataclasses.replace(SETTINGS, dominant_share=0.01))
    assert "smaller shares were not kept" in caplog.text
    assert recs == parse()


def test_parse_splits_by_category_and_method_with_their_site_counts() -> None:
    rows = {
        (o.key.key, dict(o.attrs)["category"], dict(o.attrs)["method"]): o
        for o in observations(parse())
        if o.series == "2026-W39"
    }
    inter = rows["Inter", "startups", "browser"]
    assert (inter.value, dict(inter.attrs)["category_sites"]) == (4.0, 6)
    assert dict(rows["Open Sans", "startups", "static"].attrs)["category_sites"] == 1
    assert dict(rows["Poppins", "popular", "static"].attrs)["category_sites"] == 1
    assert dict(rows["Georgia", "popular", "browser"].attrs)["category_sites"] == 2
    assert (inter.start, inter.end, inter.unit, inter.key.ns) == (
        date(2026, 9, 23),
        date(2026, 9, 23),
        "sites",
        "fot-name",
    )


def test_a_site_with_only_generic_names_counts_in_the_denominator_only() -> None:
    recs = observations(parse())
    # cobalt-cloud (system-ui only) is one of the 6 startups/browser sites in W39
    startups = {dict(o.attrs)["category_sites"] for o in recs if o.series == "2026-W39"
                and dict(o.attrs)["category"] == "startups" and dict(o.attrs)["method"] == "browser"}  # fmt: skip
    assert startups == {6}
    assert not {o.key.key.casefold() for o in recs} & set(GENERIC_NAMES)


def test_parse_applies_a_higher_dominant_share() -> None:
    w39 = totals(parse(settings=dataclasses.replace(SETTINGS, dominant_share=0.3)), "2026-W39")
    assert "JetBrains Mono" not in w39
    assert "SF Mono" not in w39
    assert w39["Playfair Display"] == 1.0  # still a heading font


def test_a_week_without_counted_sites_gives_no_rows(tmp_path: Path) -> None:
    rows = [r for r in synthetic_rows("2026-W40") if r["status"] != "ok"]
    upstream = Upstream.fixture()
    upstream.weeks["2026-W40"] = gz(rows)
    snap, _ = fetch_into(tmp_path, upstream.write(tmp_path / "http"))
    assert snap is not None
    assert {o.series for o in observations(parse(snap))} == {"2026-W39"}
    assert snap.manifest.data_date == date(2026, 9, 23)


# --- the real source ----------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real fetch of the newest week (GITHUB_TOKEN is used when set) and a parse of it."""
    settings = dataclasses.replace(SETTINGS, keep_weeks=1)
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
                settings=settings,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    (week,) = Index.from_json(snap.load_json(INDEX)).weeks
    assert week.sites >= 5000
    for row in snap.iter_jsonl(week.extract):
        assert not PRIVATE_KEYS & set(row)
    recs = parse(snap, settings)
    names = totals(recs, week.week)
    top = sorted(names, key=lambda n: -names[n])[:10]
    assert "Inter" in top
    assert sum(1 for o in observations(recs) if o.value and o.value >= 3) >= 100


# --- rebuilding the fixture -----------------------------------------------------------------------


def rebuild() -> None:
    """Write ``http/`` from ``synthetic/``, fetch it into ``snapshot/``, regen the golden file."""
    upstream = Upstream(
        weeks={w: gz(synthetic_rows(w)) for w in WEEKS},
        latest=(SYNTHETIC / "latest.csv").read_bytes(),
    )
    shutil.rmtree(HTTP, ignore_errors=True)
    upstream.write(HTTP)
    with tempfile.TemporaryDirectory() as tmp:
        snap, _ = fetch_into(Path(tmp), HTTP)
        assert snap is not None
        target = FIXTURE / "snapshot"
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(snap.path, target)
    regen.regen(COLLECTOR.name)
    print(jsonio.pretty_bytes(Index.from_json(snapshot().load_json(INDEX)).to_json()).decode())


if __name__ == "__main__":
    rebuild()


def test_a_malformed_site_row_error_never_quotes_the_row() -> None:
    # Ruling T4: Fonts Over Time's rows stay private, and parse errors reach public logs.
    from tff_catalog.collectors.ranking.fot import _sites

    row = {"category": "c", "domain": "private.example", "body": "Secret Sans"}
    with pytest.raises(ValueError, match="row 1") as caught:
        _sites([row], "weeks/2026-W39.jsonl.gz", 0.5, frozenset())
    message = str(caught.value)
    assert message == "weeks/2026-W39.jsonl.gz: row 1: weekly extract row lacks method"
    assert "private.example" not in message
    assert "Secret" not in message
