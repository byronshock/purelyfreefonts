"""The ecosystems collector: ecosyste.ms dependent repositories per Fontsource npm package.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the trimmed real fixture (ruling T1, CC BY-SA 4.0), on synthetic answers for
batching and the sanity checks, plus one small real lookup marked ``network``.
"""

import itertools
import json
import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking.ecosystems import (
    API_HOST,
    BULK_URL,
    COLLECTOR,
    EXTRACT,
    HEADER,
    MAX_PURLS,
    NPM_HOST,
    Row,
    Settings,
    batches,
    check_against,
    dependents_csv,
    is_package,
    org_url,
    purl_package,
    read_bulk,
    read_dependents_csv,
    read_org_listing,
    synced_day,
    to_purl,
)
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record
from tff_catalog.store import RawDir, Snapshot, SnapshotWriter, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.ecosystems")


def parse(snapshot: Snapshot = SNAPSHOT) -> dict[str, Observation]:
    recs: list[Record] = regen.parse_records(COLLECTOR, snapshot, SETTINGS)
    assert all(isinstance(r, Observation) for r in recs)
    return {r.key.key: r for r in recs if isinstance(r, Observation)}


def package(name: str, count: int | None, synced: str | None = "2026-09-20T01:02:03.456Z") -> dict:
    """One package object as bulk_lookup answers it (the fields the collector reads)."""
    return {
        "name": name,
        "ecosystem": "npm",
        "purl": to_purl(name),
        "dependent_repos_count": count,
        "downloads": 999_999,  # npm's last month: never read
        "last_synced_at": synced,
    }


def write_http(directory: Path, responses: list[dict[str, Any]]) -> Path:
    """A mockhttp fixture: each response is ``{url, body, [method], [request_json]}``."""
    directory.mkdir(parents=True, exist_ok=True)
    index = []
    for i, r in enumerate(responses):
        (directory / f"body{i}.json").write_text(json.dumps(r["body"]), encoding="utf-8")
        entry = {k: v for k, v in r.items() if k != "body"} | {"body": f"body{i}.json"}
        index.append(entry)
    mockhttp.write_index(directory, index)
    return directory


def org(scope: str, names: list[str]) -> dict[str, Any]:
    return {"url": org_url(scope), "body": dict.fromkeys(names, "write")}


def bulk(purls: list[str], answer: list[dict]) -> dict[str, Any]:
    return {"method": "POST", "url": BULK_URL, "request_json": {"purls": purls}, "body": answer}


def fetch(
    tmp: Path,
    http: Path,
    settings: Settings = SETTINGS,
    *,
    day: date = DAY,
    previous: Snapshot | None = None,
) -> tuple[Snapshot | None, mockhttp.MockHTTP]:
    """Run ``fetch()`` offline into a fresh store; return the snapshot and the mock."""
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
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
    assert not mock.unmatched, mock.unmatched
    return store.snapshot(COLLECTOR.name, day), mock


def previous_of(tmp: Path, rows: list[Row], version: int = COLLECTOR.version) -> Snapshot:
    writer = SnapshotWriter(tmp / "previous", COLLECTOR.name, date(2026, 8, 26), version)
    writer.write_bytes(EXTRACT, dependents_csv(rows), rows=len(rows))
    return writer.close()


# --- identity and settings --------------------------------------------------------------------


def test_identity_follows_the_design() -> None:
    assert (COLLECTOR.name, COLLECTOR.kind, COLLECTOR.group) == (
        "ecosystems",
        "ranking",
        "npm_registry",
    )
    assert set(COLLECTOR.hosts) == {API_HOST, NPM_HOST}
    assert COLLECTOR.emits == (Observation,)
    assert COLLECTOR.needs_baseline is False


def test_settings_follow_ruling_m7() -> None:
    assert isinstance(SETTINGS, Settings)
    assert SETTINGS.scopes == ("@fontsource", "@fontsource-variable")  # gate M7 (rec)
    assert SETTINGS.batch_size == MAX_PURLS == 100
    assert SETTINGS.enabled is True


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"scopes": []}, "at least one"),
        ({"scopes": ["fontsource"]}, "not an npm scope"),
        ({"scopes": ["@Fontsource"]}, "not an npm scope"),
        ({"scopes": ["@fontsource", "@fontsource"]}, "twice"),
        ({"batch_size": 0}, "between 1 and 100"),
        ({"batch_size": 101}, "between 1 and 100"),
        ({"min_share": 1.5}, "between 0 and 1"),
        ({"scopes": "@fontsource"}, "expected an array"),
        ({"batchsize": 50}, "unknown key"),
    ],
)
def test_settings_are_checked(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        from_mapping(Settings, data, where="sources/ecosystems.toml")


# --- names and purls --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["@fontsource/inter", "@fontsource-variable/roboto-flex", "@fontsource/m-plus-1p"]
)
def test_purls_round_trip(name: str) -> None:
    assert is_package(name)
    assert to_purl(name) == "pkg:npm/%40" + name.removeprefix("@")
    assert purl_package(to_purl(name)) == name


@pytest.mark.parametrize(
    ("purl", "name"),
    [
        ("pkg:npm/%40fontsource/inter", "@fontsource/inter"),
        ("pkg:npm/@fontsource/inter", "@fontsource/inter"),  # the spec encodes @, callers may not
        ("pkg:npm/%40fontsource/inter@5.2.8", "@fontsource/inter"),
        ("pkg:npm/@fontsource/inter@5.2.8?repository_url=x#sub", "@fontsource/inter"),
        ("pkg:npm/left-pad", "left-pad"),
        ("pkg:pypi/inter", None),
        ("pkg:npm/", None),
    ],
)
def test_purl_package_strips_version_and_qualifiers(purl: str, name: str | None) -> None:
    assert purl_package(purl) == name


def test_org_listing_keeps_the_scope_only() -> None:
    doc = {"@fontsource/b": "write", "@fontsource/a": "read", "@other/c": "write", "left": "x"}
    doc["@fontsource/Bad"] = "write"
    assert read_org_listing(doc, "@fontsource") == (
        ["@fontsource/a", "@fontsource/b"],
        ["@fontsource/Bad", "@other/c", "left"],
    )
    assert org_url("@fontsource-variable") == (
        "https://registry.npmjs.org/-/org/fontsource-variable/package"
    )
    with pytest.raises(ValueError, match="object of package names"):
        read_org_listing(["@fontsource/a"], "@fontsource")


def test_batches_are_consecutive_and_bounded() -> None:
    assert [list(b) for b in batches(list(range(7)), 3)] == [[0, 1, 2], [3, 4, 5], [6]]
    assert list(batches([], 100)) == []
    with pytest.raises(ValueError, match="positive"):
        list(batches([1], 0))


# --- the bulk answer --------------------------------------------------------------------------


def test_bulk_answers_are_keyed_by_purl_whatever_their_order() -> None:
    answer = [
        package("@fontsource/c", 7, "2026-09-01T00:00:00Z"),
        package("@fontsource/a", 12),
        {"name": "@fontsource/b", "dependent_repos_count": None, "last_synced_at": None},
        package("@fontsource/zzz", 3),  # not asked about
    ]
    found, unexpected = read_bulk(answer, ["@fontsource/a", "@fontsource/b", "@fontsource/c"])
    assert found == {
        "@fontsource/a": Row("@fontsource/a", 12, "2026-09-20T01:02:03.456Z"),
        "@fontsource/b": Row("@fontsource/b", None, None),  # named by `name` without a purl
        "@fontsource/c": Row("@fontsource/c", 7, "2026-09-01T00:00:00Z"),
    }
    assert unexpected == ["@fontsource/zzz"]


def test_a_package_answered_twice_keeps_the_later_sync() -> None:
    old = package("@fontsource/a", 50, "2026-07-01T00:00:00Z")
    new = package("@fontsource/a", 40, "2026-09-01T00:00:00Z")
    for answer in ([old, new], [new, old]):
        found, _ = read_bulk(answer, ["@fontsource/a"])
        assert found["@fontsource/a"].count == 40


def test_a_package_answered_twice_is_chosen_whatever_the_order() -> None:
    """The latest sync wins to the second, then the larger count; never the answer's order."""
    morning = package("@fontsource/a", 50, "2026-09-01T06:00:00Z")
    evening = package("@fontsource/a", 40, "2026-09-01T18:00:00.500Z")
    again = package("@fontsource/a", 45, "2026-09-01T18:00:00.500Z")
    unsynced = package("@fontsource/a", 60, None)
    for answer in itertools.permutations([morning, evening, again, unsynced]):
        found, _ = read_bulk(list(answer), ["@fontsource/a"])
        assert found["@fontsource/a"] == Row("@fontsource/a", 45, "2026-09-01T18:00:00.500Z")


def test_a_purl_of_another_ecosystem_is_never_wanted() -> None:
    other = package("@fontsource/a", 99) | {"ecosystem": "pypi", "purl": "pkg:pypi/fontsource-a"}
    found, unexpected = read_bulk([other], ["@fontsource/a"])
    assert found == {}
    assert unexpected == ["pkg:pypi/fontsource-a"]


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ({"error": "Maximum 100 PURLs allowed per request"}, "expected a list"),
        (["@fontsource/a"], "not an object"),
        ([{"dependent_repos_count": 1}], "no purl or name"),
        ([package("@fontsource/a", -1)], "not a count"),
        ([package("@fontsource/a", True)], "not a count"),
        ([package("@fontsource/a", 1.5)], "not a count"),
        ([package("@fontsource/a", 1, "yesterday")], "last_synced_at"),
        ([package("@fontsource/a", 1, 1790000000)], "not a timestamp"),
    ],
)
def test_bad_bulk_answers_are_refused(answer: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        read_bulk(answer, ["@fontsource/a"])


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("2026-07-24T21:17:33.931Z", date(2026, 7, 24)),
        ("2026-07-24T23:30:00-02:00", date(2026, 7, 25)),  # the UTC day
        ("2026-07-24T01:00:00+02:00", date(2026, 7, 23)),
        ("2026-07-24T21:17:33", date(2026, 7, 24)),  # no offset: UTC
        ("2026-07-24", date(2026, 7, 24)),
    ],
)
def test_synced_day_is_the_utc_day(text: str, day: date) -> None:
    assert synced_day(text) == day


# --- the extract ------------------------------------------------------------------------------


def test_extract_round_trips_sorted() -> None:
    rows = [Row("@fontsource/b", 3, "2026-09-20T00:00:00Z"), Row("@fontsource/a")]
    data = dependents_csv(rows)
    assert data.decode().splitlines() == [
        ",".join(HEADER),
        "@fontsource/a,,",
        "@fontsource/b,3,2026-09-20T00:00:00Z",
    ]
    assert read_dependents_csv(data) == sorted(rows)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("package,count\n", "header"),
        ("package,dependent_repos_count,last_synced_at\n@fontsource/a,1\n", "3 fields"),
        ("package,dependent_repos_count,last_synced_at\n@fontsource/a,x,\n", "not a count"),
        ("package,dependent_repos_count,last_synced_at\n@fontsource/a,-1,\n", "not a count"),
        ("package,dependent_repos_count,last_synced_at\ninter,1,\n", "bad or repeated"),
        (
            "package,dependent_repos_count,last_synced_at\n@fontsource/a,1,\n@fontsource/a,2,\n",
            "bad or repeated",
        ),
        ("package,dependent_repos_count,last_synced_at\n@fontsource/a,1,soon\n", "last_synced"),
    ],
)
def test_bad_extracts_are_refused(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        read_dependents_csv(text.encode())


# --- parse of the real fixture ----------------------------------------------------------------


def test_fixture_parses_one_observation_per_package() -> None:
    obs = parse()
    assert len(obs) == 25
    assert {o.key.ns for o in obs.values()} == {"npm"}
    assert {k.split("/")[0] for k in obs} == {"@fontsource", "@fontsource-variable"}  # M7
    assert {(o.series, o.unit, o.start, o.end) for o in obs.values()} == {
        ("dependent_repos", "dependents", DAY, DAY)
    }
    roboto = obs["@fontsource/roboto"]
    assert (roboto.value, dict(roboto.attrs)) == (14419.0, {"last_synced_at": "2026-07-31"})
    assert obs["@fontsource-variable/inter"].value == 83.0
    assert obs["@fontsource/geist-mono"].value == 0.0  # a real zero, not censored


def test_packages_ecosystems_does_not_know_have_no_value() -> None:
    obs = parse()
    unknown = sorted(k for k, o in obs.items() if o.value is None)
    assert unknown == ["@fontsource-variable/akt", "@fontsource/akt"]
    assert all(obs[k].attrs == () for k in unknown)


def test_parse_reads_the_data_date_and_optional_fields(tmp_path: Path) -> None:
    rows = [Row("@fontsource/a", 7), Row("@fontsource/b", None, "2026-09-01T23:59:59-01:00")]
    writer = SnapshotWriter(tmp_path, COLLECTOR.name, date(2026, 9, 27), COLLECTOR.version)
    writer.write_bytes(EXTRACT, dependents_csv(rows), rows=len(rows))
    writer.set_data_date(date(2026, 9, 26))
    obs = parse(writer.close())
    assert (obs["@fontsource/a"].value, obs["@fontsource/a"].attrs) == (7.0, ())
    assert obs["@fontsource/b"].value is None  # known, but without a count: censored
    assert dict(obs["@fontsource/b"].attrs) == {"last_synced_at": "2026-09-02"}  # the UTC day
    assert {(o.start, o.end) for o in obs.values()} == {(date(2026, 9, 26), date(2026, 9, 26))}


def test_fixture_answer_came_out_of_order() -> None:
    """The recorded answer is in ecosyste.ms's own order, so the keying is exercised."""
    answer = json.loads((FIXTURE / "http" / "bulk-0001.json").read_text(encoding="utf-8"))
    names = [a["name"] for a in answer]
    assert names != sorted(names)


# --- fetch --------------------------------------------------------------------------------------


STATIC = ["@fontsource/aster-sans", "@fontsource/birch-mono", "@fontsource/cobalt-serif"]
STATIC += ["@fontsource/dune-display", "@fontsource/ember-hand"]
VARIABLE = ["@fontsource-variable/aster-sans", "@fontsource-variable/birch-mono"]
SMALL = Settings(scopes=("@fontsource", "@fontsource-variable"), batch_size=3, min_share=0.5)


def synthetic_http(tmp: Path, answers: list[list[dict]] | None = None) -> Path:
    """npm lists STATIC and VARIABLE; bulk_lookup answers each batch of 3 (reversed)."""
    wanted = sorted(STATIC + VARIABLE)
    chunks = [wanted[i : i + 3] for i in range(0, len(wanted), 3)]
    if answers is None:
        answers = [
            [package(n, 10 * (i + 1)) for i, n in reversed(list(enumerate(chunk)))]
            for chunk in chunks
        ]
    responses = [
        org("@fontsource", [*reversed(STATIC), "@other-scope/aster-sans"]),
        org("@fontsource-variable", VARIABLE),
    ]
    responses += [
        bulk([to_purl(n) for n in chunk], answer)
        for chunk, answer in zip(chunks, answers, strict=True)
    ]
    return write_http(tmp / "http", responses)


def test_fetch_looks_up_every_package_in_batches(tmp_path: Path) -> None:
    snap, mock = fetch(tmp_path, synthetic_http(tmp_path), SMALL)
    assert snap is not None
    bodies = [json.loads(r.content) for r in mock.requests if r.method == "POST"]
    sent = [p for b in bodies for p in b["purls"]]
    assert all(len(b["purls"]) <= 3 for b in bodies)
    assert sent == sorted(to_purl(n) for n in STATIC + VARIABLE)  # each once, in order
    assert [m.url for m in snap.manifest.fetched] == [
        org_url("@fontsource"),
        org_url("@fontsource-variable"),
        *[BULK_URL] * 3,
    ]
    rows = read_dependents_csv(snap.read_bytes(EXTRACT))
    assert [r.package for r in rows] == sorted(STATIC + VARIABLE)
    assert all(r.count is not None for r in rows)
    assert snap.manifest.data_date == DAY
    assert "@fontsource: names outside the scope, dropped: @other-scope/aster-sans" in (
        snap.manifest.notes
    )
    assert "ecosyste.ms counted 7 of 7 packages; bulk_lookup calls: 3" in snap.manifest.notes
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == [
        "bulk-0001.json",
        "bulk-0002.json",
        "bulk-0003.json",
    ]


def test_fetch_keeps_unknown_packages_with_empty_values(tmp_path: Path) -> None:
    # Batches (sorted; "-" sorts before "/"): variable aster, variable birch, aster;
    # birch, cobalt, dune; ember. Each call is keyed to its own purls only.
    answers = [
        [package("@fontsource-variable/aster-sans", 4)],
        [package("@fontsource/birch-mono", 9)],
        [
            package("@fontsource/ember-hand", 1),
            package("@fontsource/aster-sans", 20),  # asked in the first call, not this one
            package("@fontsource/zeta-extra", 5),
        ],
    ]
    snap, _ = fetch(tmp_path, synthetic_http(tmp_path, answers), SMALL)
    assert snap is not None
    rows = {r.package: r for r in read_dependents_csv(snap.read_bytes(EXTRACT))}
    assert len(rows) == 7
    assert rows["@fontsource-variable/birch-mono"] == Row("@fontsource-variable/birch-mono")
    assert rows["@fontsource/aster-sans"] == Row("@fontsource/aster-sans")
    assert (
        "answers for packages not asked about, ignored: @fontsource/aster-sans, "
        "@fontsource/zeta-extra" in snap.manifest.notes
    )
    assert "ecosyste.ms counted 3 of 7 packages; bulk_lookup calls: 3" in snap.manifest.notes
    obs = parse(snap)
    assert obs["@fontsource/cobalt-serif"].value is None
    assert obs["@fontsource/birch-mono"].value == 9.0
    assert obs["@fontsource-variable/aster-sans"].value == 4.0


def test_default_settings_send_at_most_100_purls_a_call(tmp_path: Path) -> None:
    names = [f"@fontsource/font-{i:03d}" for i in range(2 * MAX_PURLS + 1)]
    chunks = [names[i : i + MAX_PURLS] for i in range(0, len(names), MAX_PURLS)]
    responses = [org("@fontsource", names), org("@fontsource-variable", [])]
    responses += [bulk([to_purl(n) for n in c], [package(n, 5) for n in c]) for c in chunks]
    snap, mock = fetch(tmp_path, write_http(tmp_path / "http", responses))
    assert snap is not None
    sizes = [len(json.loads(r.content)["purls"]) for r in mock.requests if r.method == "POST"]
    assert sizes == [100, 100, 1]
    assert snap.manifest.extracts[0].rows == len(names)


def test_fetch_fails_when_ecosystems_knows_none(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="none of the 7 packages"):
        fetch(tmp_path, synthetic_http(tmp_path, [[], [], []]), SMALL)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_fetch_fails_when_npm_lists_nothing(tmp_path: Path) -> None:
    http = write_http(tmp_path / "http", [org("@fontsource", ["@other/x"])])
    settings = Settings(scopes=("@fontsource",))
    with pytest.raises(ValueError, match="npm lists no packages"):
        fetch(tmp_path, http, settings)


def test_fetch_fails_on_a_shrunken_answer(tmp_path: Path) -> None:
    before = [Row(f"@fontsource/font-{i:02d}", i, "2026-08-20T00:00:00Z") for i in range(20)]
    previous = previous_of(tmp_path, before)
    with pytest.raises(ValueError, match="previous snapshot"):
        fetch(tmp_path, synthetic_http(tmp_path), SMALL, previous=previous)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_fetch_fails_when_the_counts_shrink(tmp_path: Path) -> None:
    previous = previous_of(
        tmp_path, [Row(n, 10, "2026-08-20T00:00:00Z") for n in STATIC + VARIABLE]
    )
    answers = [
        [package("@fontsource-variable/aster-sans", 4)],
        [package("@fontsource/birch-mono", 9)],
        [package("@fontsource/ember-hand", 1)],
    ]
    with pytest.raises(ValueError, match="3 counts, down from 7"):
        fetch(tmp_path, synthetic_http(tmp_path, answers), SMALL, previous=previous)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_previous_snapshot_without_the_extract_is_not_compared(tmp_path: Path) -> None:
    empty = SnapshotWriter(tmp_path / "previous", COLLECTOR.name, date(2026, 8, 26), 1).close()
    snap, _ = fetch(tmp_path, synthetic_http(tmp_path), SMALL, previous=empty)
    assert snap is not None


def test_previous_snapshot_of_another_version_is_not_compared(tmp_path: Path) -> None:
    before = [Row(f"@fontsource/font-{i:02d}", i) for i in range(20)]
    previous = previous_of(tmp_path, before, version=COLLECTOR.version + 1)
    snap, _ = fetch(tmp_path, synthetic_http(tmp_path), SMALL, previous=previous)
    assert snap is not None


def test_check_against_counts_packages_and_counts() -> None:
    rows = [Row("@fontsource/a", 1), Row("@fontsource/b"), Row("@fontsource/c")]
    check_against(rows, None, 0.5)
    check_against(rows, [Row("@fontsource/a", 1), Row("@fontsource/b", 1)], 0.5)
    with pytest.raises(ValueError, match="1 counts, down from 3"):
        check_against(rows, [Row(f"@fontsource/{n}", 1) for n in "abc"], 0.5)
    with pytest.raises(ValueError, match="none of the 1 packages"):
        check_against([Row("@fontsource/a")], None, 0.5)
    check_against([], None, 0.5)


def test_fixture_fetch_asks_ruling_m7_scopes_in_one_call(tmp_path: Path) -> None:
    snap, mock = fetch(tmp_path, FIXTURE / "http")
    assert snap is not None
    posts = [json.loads(r.content)["purls"] for r in mock.requests if r.method == "POST"]
    assert len(posts) == 1
    assert len(posts[0]) == 25
    assert all(
        p.startswith(("pkg:npm/%40fontsource/", "pkg:npm/%40fontsource-variable/"))
        for p in posts[0]
    )
    assert snap.read_bytes(EXTRACT) == SNAPSHOT.read_bytes(EXTRACT)
    raw = (tmp_path / "raw" / "bulk-0001.json").read_bytes()
    assert raw == (FIXTURE / "http" / "bulk-0001.json").read_bytes()  # the raw answer, whole


# --- network ----------------------------------------------------------------------------------


@pytest.mark.network
def test_real_listing_and_lookup(tmp_path: Path) -> None:
    """Two real requests: npm's @fontsource-variable list and one small bulk_lookup."""
    with Fetcher(log=LOG) as fetcher:
        scoped = fetcher.scoped(COLLECTOR.hosts)
        listing = scoped.get(org_url("@fontsource-variable"))
        kept, dropped = read_org_listing(listing.json(), "@fontsource-variable")
        assert len(kept) >= 500
        assert "@fontsource-variable/inter" in kept
        assert dropped == []
        wanted = ["@fontsource-variable/inter", "@fontsource/inter", "@fontsource/roboto"]
        answer = scoped.post_json(BULK_URL, {"purls": [to_purl(p) for p in wanted]})
    found, unexpected = read_bulk(answer.json(), wanted)
    assert set(found) == set(wanted)
    assert unexpected == []
    assert found["@fontsource/roboto"].count is not None
    assert found["@fontsource/roboto"].count >= 1000
    assert all(r.synced is not None for r in found.values())
