"""Alias miner "fontsource_legacy" (milestone-1 step 7; design-m1 §2.3, gap G5).

Offline tests run the miner on a trimmed real ``fontsource`` snapshot written to
a temporary store and on recorded npm and jsDelivr answers
(``fixtures/``, see its NOTICE); ``fixtures/expected.csv`` is the seed file they
give. The committed ``data/alias-seeds/fontsource_legacy.csv`` is checked too.
"""

import csv
import io
import json
import logging
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.helpers import ROOT
from tests.helpers.mockhttp import MockHTTP

from tff_catalog import fetch
from tff_catalog.aliases import (
    DEFAULT_AUTO_RULES,
    MAPPING_RELATIONS,
    SEED_COLUMNS,
    AliasCandidate,
    AliasRow,
    AliasTable,
    FamilyRef,
    load_seeds,
    merge_detailed,
    write_seeds,
)
from tff_catalog.aliases.miners import MineContext, Miner
from tff_catalog.aliases.miners import fontsource_legacy as fl
from tff_catalog.fetch import USER_AGENT, Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import NAMESPACES, SourceKey
from tff_catalog.store import RawDir, Snapshot, Store

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SNAPSHOT = FIXTURES / "snapshot"
HTTP = FIXTURES / "http"
EXPECTED = FIXTURES / "expected.csv"
SEEDS = ROOT / "data" / "alias-seeds" / "fontsource_legacy.csv"

SNAP_DAY = date(2026, 9, 25)
RUN_DAY = date(2026, 9, 26)
COMMIT = "43cfc04f7f53ef63cc5781c018c4058d8908f485"
REPLACEMENTS = (
    f"https://github.com/fontsource/fontsource/blob/{COMMIT}/registry/data/replacements.json"
)
LOG = logging.getLogger("test.fontsource_legacy")


def k(ns: str, key: str) -> SourceKey:
    return SourceKey(ns, key)


def cand(alias: SourceKey, target: SourceKey, relation: str, evidence: str) -> AliasCandidate:
    return AliasCandidate(alias, target, relation, "", "fontsource_legacy", evidence, False)


def fixture_json(name: str) -> Any:
    return json.loads((SNAPSHOT / name).read_text(encoding="utf-8"))


def fixture_rows(group: str) -> list[dict[str, Any]]:
    text = (SNAPSHOT / f"families-{group}.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_snapshot(
    store: Store,
    day: date = SNAP_DAY,
    *,
    version: int = 1,
    replacements: object = None,
    leave_out: tuple[str, ...] = (),
) -> Snapshot:
    """A ``fontsource`` snapshot shaped like the collector's (format 1) from ``fixtures/snapshot``."""
    w = store.writer("fontsource", day, version)
    extracts: dict[str, Callable[[str], object]] = {
        "registry.json": lambda n: w.write_json(n, fixture_json(n)),
        "replacements.json": lambda n: w.write_json(
            n, fixture_json(n) if replacements is None else replacements
        ),
        "api-missing.json": lambda n: w.write_json(n, fixture_json(n)),
        "families/google.jsonl.gz": lambda n: w.write_jsonl(n, fixture_rows("google")),
        "families/fontsource.jsonl.gz": lambda n: w.write_jsonl(n, fixture_rows("fontsource")),
    }
    for name, write in extracts.items():
        if name not in leave_out:
            write(name)
    return w.close()


def context(tmp_path: Path, store: Store | None, day: date = RUN_DAY) -> MineContext:
    paths = Paths.for_root(tmp_path / "repo", store=store.root if store else None)
    raw = RawDir(tmp_path / "raw")
    return MineContext(paths, store, raw, day, LOG)


@pytest.fixture(autouse=True)
def sleeps(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The fetcher's pacing waits, on a fake clock that only waiting moves, so they are
    exact and never slept (real waits for real hosts)."""
    waits: list[float] = []
    if request.node.get_closest_marker("network") is None:
        now = [1000.0]

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            now[0] += seconds

        monkeypatch.setattr(fetch, "_sleep", sleep)
        monkeypatch.setattr(fetch, "_monotonic", lambda: now[0])
    return waits


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "store")
    write_snapshot(s)
    return s


@pytest.fixture
def http() -> MockHTTP:
    return MockHTTP.from_dir(HTTP)


def seed_bytes(cands: list[AliasCandidate], tmp_path: Path) -> bytes:
    path = tmp_path / "seeds" / "fontsource_legacy.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_seeds(cands, path)
    return path.read_bytes()


# --- the miner as the stage runs it ------------------------------------------------------------


def test_miner_contract() -> None:
    assert isinstance(fl.MINER, Miner)
    assert fl.MINER.name == Path(fl.__file__).stem == "fontsource_legacy"


def test_mine_gives_the_expected_seed_file(
    tmp_path: Path, store: Store, http: MockHTTP, caplog: pytest.LogCaptureFixture
) -> None:
    miner = fl.FontsourceLegacy(transport=http.transport)
    caplog.set_level(logging.INFO, logger=LOG.name)
    cands = miner.mine(context(tmp_path, store))
    assert cands == sorted(set(cands))
    assert seed_bytes(cands, tmp_path) == EXPECTED.read_bytes()
    assert "5 replacements, 25 packages listed, 5 retired (5 named); 20 candidates" in caplog.text


def test_mine_is_deterministic(tmp_path: Path, store: Store) -> None:
    runs = [
        fl.FontsourceLegacy(transport=MockHTTP.from_dir(HTTP).transport).mine(
            context(tmp_path, store)
        )
        for _ in range(2)
    ]
    assert runs[0] == runs[1]


def test_mine_asks_only_for_the_listings_and_retired_packages(
    tmp_path: Path, store: Store, http: MockHTTP, sleeps: list[float]
) -> None:
    fl.FontsourceLegacy(transport=http.transport).mine(context(tmp_path, store))
    assert http.unmatched == []
    assert http.hosts() <= set(fl.HOSTS)
    assert {r.headers["user-agent"] for r in http.requests} == {USER_AGENT}
    metadata = sorted(u for u in http.urls() if u.endswith("/metadata.json"))
    assert metadata == [
        f"https://cdn.jsdelivr.net/npm/@fontsource/{p}/metadata.json"
        for p in ("material-icons-rounded", "muli", "ostrich-sans-dashed", "spartan", "youngserif")
    ]
    assert len(http.requests) == 7  # two listings, five packages
    # paced at 1 request a second per host: one wait on the registry, four on the CDN
    assert sleeps == [1.0] * 5


def test_mine_writes_no_file(tmp_path: Path, store: Store, http: MockHTTP) -> None:
    """The stage writes the seed file; a miner never writes data/aliases.csv (design-m1 §2.3)."""
    ctx = context(tmp_path, store)
    before = sorted(tmp_path.rglob("*"))
    fl.FontsourceLegacy(transport=http.transport).mine(ctx)
    assert sorted(tmp_path.rglob("*")) == before
    assert not ctx.paths.aliases_csv.exists()


def test_source_sans_pro_known_answer(tmp_path: Path, store: Store, http: MockHTTP) -> None:
    """Methodology §9: the legacy id and its package are proposed for Source Sans 3."""
    cands = set(fl.FontsourceLegacy(transport=http.transport).mine(context(tmp_path, store)))
    target = k("fs-id", "source-sans-3")
    assert cand(k("fs-id", "source-sans-pro"), target, "rename", REPLACEMENTS) in cands
    assert cand(k("npm", "@fontsource/source-sans-pro"), target, "package", REPLACEMENTS) in cands


def test_every_candidate_is_a_valid_unreviewed_proposal(
    tmp_path: Path, store: Store, http: MockHTTP
) -> None:
    cands = fl.FontsourceLegacy(transport=http.transport).mine(context(tmp_path, store))
    for c in cands:
        assert c.source == "fontsource_legacy"
        assert not c.auto  # no auto rule: the owner reviews Fontsource's legacy ids (gate A)
        assert c.relation in ("rename", "package")
        assert c.detail == ""
        assert {c.alias.ns, c.target.ns} <= NAMESPACES
        assert c.evidence.startswith(
            ("https://github.com/fontsource/", "https://cdn.jsdelivr.net/")
        )
        if c.alias.ns == "npm":
            assert c.alias.key.startswith(("@fontsource/", "@fontsource-variable/"))


def test_no_row_for_current_ids(tmp_path: Path, store: Store, http: MockHTTP) -> None:
    """Deprecated ids without a replacement (kantumruy), API-only ids (google-sans) and
    live ids are current: they have universe keys of their own and get no row."""
    cands = fl.FontsourceLegacy(transport=http.transport).mine(context(tmp_path, store))
    aliased = {c.alias.key.rsplit("/", 1)[-1] for c in cands}
    for current in ("kantumruy", "google-sans", "inter", "young-serif", "source-sans-3"):
        assert current not in aliased


def test_seed_file_round_trips(tmp_path: Path, store: Store, http: MockHTTP) -> None:
    cands = fl.FontsourceLegacy(transport=http.transport).mine(context(tmp_path, store))
    path = tmp_path / "fontsource_legacy.csv"
    write_seeds(cands, path)
    assert load_seeds(path) == cands
    assert {c.source for c in load_seeds(path)} == {path.stem}  # what stage "aliases" checks


# --- inputs that stop the miner -------------------------------------------------------------------


def test_no_store_fails(tmp_path: Path) -> None:
    with pytest.raises(fl.MinerError, match="no snapshot store"):
        fl.FontsourceLegacy().mine(context(tmp_path, None))


def test_no_snapshot_on_or_before_the_run_date_fails(tmp_path: Path) -> None:
    s = Store(tmp_path / "store")
    write_snapshot(s, date(2026, 9, 27))
    with pytest.raises(fl.MinerError, match="tff-catalog fetch --only fontsource"):
        fl.FontsourceLegacy().mine(context(tmp_path, s))


def test_reads_the_newest_snapshot_not_after_the_run_date(tmp_path: Path, http: MockHTTP) -> None:
    s = Store(tmp_path / "store")
    write_snapshot(s, date(2026, 8, 25), replacements={"source-sans-pro": "source-sans-3"})
    write_snapshot(s)
    write_snapshot(s, date(2026, 9, 27), replacements={})
    cands = fl.FontsourceLegacy(transport=http.transport).mine(context(tmp_path, s))
    assert seed_bytes(cands, tmp_path) == EXPECTED.read_bytes()


def test_a_snapshot_older_than_one_refresh_cycle_fails(tmp_path: Path) -> None:
    """npm's listing is live: against an old registry, every font added since would look
    retired. The miner fails first (no request), so the stage keeps the committed seeds."""
    s = Store(tmp_path / "store")
    write_snapshot(s, RUN_DAY - fl.MAX_SNAPSHOT_AGE - timedelta(days=1))
    with pytest.raises(fl.MinerError, match="every font added since as retired"):
        fl.FontsourceLegacy().mine(context(tmp_path, s))


def test_a_snapshot_one_refresh_cycle_old_is_still_read(tmp_path: Path, http: MockHTTP) -> None:
    s = Store(tmp_path / "store")
    write_snapshot(s, RUN_DAY - fl.MAX_SNAPSHOT_AGE)
    cands = fl.FontsourceLegacy(transport=http.transport).mine(context(tmp_path, s))
    assert seed_bytes(cands, tmp_path) == EXPECTED.read_bytes()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        pytest.param({"version": 2}, "newer than 1", id="newer-format"),
        pytest.param({"leave_out": ("replacements.json",)}, "no replacements.json", id="no-file"),
        pytest.param({"replacements": ["a", "b"]}, "is not an object", id="not-object"),
        pytest.param(
            {
                "leave_out": (
                    "families/google.jsonl.gz",
                    "families/fontsource.jsonl.gz",
                    "api-missing.json",
                )
            },
            "no family ids",
            id="no-families",
        ),
    ],
)
def test_bad_snapshot_fails(tmp_path: Path, kwargs: dict[str, Any], message: str) -> None:
    s = Store(tmp_path / "store")
    snap = write_snapshot(s, **kwargs)
    with pytest.raises(fl.MinerError, match=message):
        fl.read_registry(snap, LOG)


def test_empty_org_listing_fails(tmp_path: Path, store: Store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with pytest.raises(fl.MinerError, match="non-empty object"):
        fl.FontsourceLegacy(transport=httpx.MockTransport(handler)).mine(context(tmp_path, store))


# --- the parts ---------------------------------------------------------------------------------------


def test_read_registry(tmp_path: Path) -> None:
    reg = fl.read_registry(write_snapshot(Store(tmp_path)), LOG)
    assert reg.evidence == REPLACEMENTS
    assert reg.replacements == fixture_json("replacements.json")
    assert "google-sans" in reg.current  # the API-only rows count
    assert {"kantumruy", "source-sans-pro", "geist-sans"} <= reg.current  # deprecated ids too
    assert "muli" not in reg.current


def test_replacements_url() -> None:
    repo = "https://github.com/fontsource/fontsource"
    assert fl.replacements_url({"repository": repo, "commit": COMMIT, "ref": "main"}) == (
        REPLACEMENTS
    )
    assert fl.replacements_url({"repository": repo + ".git", "commit": "", "ref": "main"}) == (
        f"{repo}/blob/main/registry/data/replacements.json"
    )
    with pytest.raises(fl.MinerError, match="https repository"):
        fl.replacements_url({"repository": "git@github.com:fontsource/fontsource", "ref": "main"})
    with pytest.raises(fl.MinerError, match="neither a commit nor a ref"):
        fl.replacements_url({"repository": repo})


def test_bad_replacement_entries_are_skipped(caplog: pytest.LogCaptureFixture) -> None:
    raw = {"old-id": "new-id", "Upper": "x", "same": "same", "n": 3, "a b": "c"}
    assert fl._clean_replacements(raw, LOG) == {"old-id": "new-id"}
    assert caplog.text.count("skipped entry") == 4


def test_chains_are_followed_and_cycles_dropped(caplog: pytest.LogCaptureFixture) -> None:
    chain = {"a": "b", "b": "c", "x": "y", "y": "x", "p": "x"}
    assert fl.final_targets(chain, LOG) == {"a": "c", "b": "c"}
    assert caplog.text.count("replacement cycle") == 3


def test_replacement_candidates() -> None:
    reg = fl.Registry(
        replacements={"old": "mid", "mid": "new", "gone": "missing"},
        current=frozenset({"mid", "new"}),
        evidence="https://example.invalid/replacements.json",
    )
    packages = frozenset({"@fontsource/old", "@fontsource-variable/mid", "@expo-google-fonts/old"})
    ev = reg.evidence
    new = k("fs-id", "new")
    assert sorted(fl.replacement_candidates(reg, packages, LOG)) == sorted(
        [
            cand(k("fs-id", "old"), new, "rename", ev),
            cand(k("npm", "@fontsource/old"), new, "package", ev),
            cand(k("fs-id", "mid"), new, "rename", ev),
            cand(k("npm", "@fontsource-variable/mid"), new, "package", ev),
            cand(k("fs-id", "gone"), k("fs-id", "missing"), "rename", ev),
        ]
    )


def test_a_replacement_naming_no_current_id_is_still_proposed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reg = fl.Registry({"gone": "missing"}, frozenset({"x"}), "https://example.invalid/r.json")
    [c] = fl.replacement_candidates(reg, frozenset(), LOG)
    assert c.target == k("fs-id", "missing")
    assert "names no current id" in caplog.text


@pytest.mark.parametrize(
    ("package", "parts"),
    [
        ("@fontsource/inter", ("@fontsource", "inter")),
        ("@fontsource-variable/noto-sans-jp", ("@fontsource-variable", "noto-sans-jp")),
        ("fontsource-inter", None),
        ("@Fontsource/inter", None),
        ("@fontsource/", None),
        ("@fontsource/inter/extra", None),
    ],
)
def test_split_package(package: str, parts: tuple[str, str] | None) -> None:
    assert fl.split_package(package) == parts


def test_retired_packages() -> None:
    reg = fl.Registry({"source-sans-pro": "source-sans-3"}, frozenset({"inter"}), "")
    listed = [
        "@fontsource/muli",
        "@fontsource/inter",
        "@fontsource-variable/inter",
        "@fontsource/source-sans-pro",
        "@expo-google-fonts/muli",
        "@fontsource-variable/andada",
    ]
    assert fl.retired_packages(listed, reg) == ["@fontsource-variable/andada", "@fontsource/muli"]


@pytest.mark.parametrize(
    ("metadata", "target"),
    [
        pytest.param({"fontName": "Muli", "type": "google"}, k("gf-family", "Muli"), id="google"),
        pytest.param(
            {"fontName": "Ostrich Sans Dashed", "type": "league"},
            k("font-name", "Ostrich Sans Dashed"),
            id="league",
        ),
        pytest.param({"fontName": " Vazir\u00a0 Code "}, k("font-name", "Vazir Code"), id="spaces"),
        # a 5.x package (every current one; shape of @fontsource/source-sans-pro@5.3.0)
        pytest.param(
            {"id": "x", "family": "Source Sans Pro", "type": "google"},
            k("gf-family", "Source Sans Pro"),
            id="5.x",
        ),
        pytest.param(
            {"fontName": "Muli", "family": "Mulish"}, k("font-name", "Muli"), id="4.x-first"
        ),
        pytest.param({"fontName": " ", "family": "Muli"}, k("font-name", "Muli"), id="blank-4.x"),
    ],
)
def test_package_candidates(metadata: Mapping[str, Any], target: SourceKey) -> None:
    ev = "https://cdn.jsdelivr.net/npm/@fontsource/x@1.0.0/metadata.json"
    assert fl.package_candidates("@fontsource/x", metadata, ev) == [
        cand(k("npm", "@fontsource/x"), target, "package", ev),
        cand(k("fs-id", "x"), target, "rename", ev),
    ]


def test_a_retired_variable_package_names_its_id() -> None:
    ev = "https://cdn.jsdelivr.net/npm/@fontsource-variable/x@5.0.1/metadata.json"
    rows = fl.package_candidates("@fontsource-variable/x", {"family": "X Sans"}, ev)
    assert rows == [
        cand(k("npm", "@fontsource-variable/x"), k("font-name", "X Sans"), "package", ev),
        cand(k("fs-id", "x"), k("font-name", "X Sans"), "rename", ev),
    ]


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"fontName": ""},
        {"fontName": "  "},
        {"fontName": 7},
        {"fontId": "x"},
        {"id": "x", "family": ""},
        {"family": ["X"]},
    ],
)
def test_no_font_name_no_candidate(metadata: Mapping[str, Any]) -> None:
    assert fl.package_candidates("@fontsource/x", metadata, "ev") == []


def fetcher_for(status: int, body: bytes, headers: Mapping[str, str] | None = None) -> Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=dict(headers or {}), content=body)

    return Fetcher(hosts=fl.HOSTS, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("status", "body", "version", "warning"),
    [
        pytest.param(404, b"Not found", "1.2.3", "no metadata.json", id="missing"),
        pytest.param(200, b"<html>", "1.2.3", "not JSON", id="not-json"),
        pytest.param(200, b"[1]", None, "not an object", id="not-object"),
    ],
)
def test_unreadable_metadata_gives_no_row(
    status: int,
    body: bytes,
    version: str | None,
    warning: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = {fl.VERSION_HEADER: version} if version else {}
    with fetcher_for(status, body, headers) as fetcher:
        doc, evidence = fl.fetch_metadata(fetcher, "@fontsource/dseg7", LOG)
    assert doc is None
    assert warning in caplog.text
    pinned = f"@{version}" if version else ""
    assert evidence == f"https://cdn.jsdelivr.net/npm/@fontsource/dseg7{pinned}/metadata.json"


def test_an_odd_version_header_is_not_pinned() -> None:
    with fetcher_for(200, b'{"fontName": "DSEG7"}', {fl.VERSION_HEADER: "1 2/x"}) as fetcher:
        doc, evidence = fl.fetch_metadata(fetcher, "@fontsource/dseg7", LOG)
    assert doc == {"fontName": "DSEG7"}
    assert evidence == "https://cdn.jsdelivr.net/npm/@fontsource/dseg7/metadata.json"


def test_a_package_without_metadata_is_left_out(tmp_path: Path, store: Store) -> None:
    """Nothing is guessed from how an id looks: without metadata, ``source-sans`` is not
    paired with ``source-sans-3`` or ``source-sans-pro``, nor ``dseg7`` with anything."""
    listings = {
        "/-/org/fontsource/package": {
            "@fontsource/inter": "write",
            "@fontsource/dseg7": "write",
            "@fontsource/source-sans": "write",
        },
        "/-/org/fontsource-variable/package": {"@fontsource-variable/inter": "write"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in listings:
            return httpx.Response(200, json=listings[request.url.path])
        return httpx.Response(404, content=b"Not found")

    cands = fl.FontsourceLegacy(transport=httpx.MockTransport(handler)).mine(
        context(tmp_path, store)
    )
    aliased = {c.alias.key for c in cands}
    assert aliased.isdisjoint(
        {"dseg7", "@fontsource/dseg7", "source-sans", "@fontsource/source-sans"}
    )
    assert {c.target for c in cands} == {
        k("fs-id", v) for v in fixture_json("replacements.json").values()
    }


def test_a_package_retired_at_5x_is_read(tmp_path: Path, store: Store) -> None:
    """A package retired after 2026-09 ends at 5.x, whose metadata.json says ``family``."""
    listings = {
        "/-/org/fontsource/package": {"@fontsource/inter": "write", "@fontsource/edu-x": "write"},
        "/-/org/fontsource-variable/package": {"@fontsource-variable/inter": "write"},
    }
    v5 = {"id": "edu-x", "family": "Edu X Hand", "type": "google", "category": "handwriting"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in listings:
            return httpx.Response(200, json=listings[request.url.path])
        if request.url.path == "/npm/@fontsource/edu-x/metadata.json":
            return httpx.Response(200, json=v5, headers={fl.VERSION_HEADER: "5.2.1"})
        return httpx.Response(404, content=b"Not found")

    cands = fl.FontsourceLegacy(transport=httpx.MockTransport(handler)).mine(
        context(tmp_path, store)
    )
    ev = "https://cdn.jsdelivr.net/npm/@fontsource/edu-x@5.2.1/metadata.json"
    target = k("gf-family", "Edu X Hand")
    assert cand(k("npm", "@fontsource/edu-x"), target, "package", ev) in cands
    assert cand(k("fs-id", "edu-x"), target, "rename", ev) in cands


def test_extract_names_match_the_fontsource_collector() -> None:
    """Kept as copies so the collector cannot break miner discovery; they must agree."""
    from tff_catalog.collectors.universe import fontsource as collector

    assert fl.REGISTRY_EXTRACT == collector.REGISTRY_EXTRACT
    assert fl.REPLACEMENTS_EXTRACT == collector.REPLACEMENTS_EXTRACT
    assert fl.API_EXTRACT == collector.API_EXTRACT
    assert fl.FAMILIES_PREFIX == collector.FAMILIES_PREFIX
    assert fl.FAMILIES_SUFFIX == collector.FAMILIES_SUFFIX
    assert fl.FS_NS == collector.NS
    assert f"{collector.REGISTRY_DIR}/{collector.REPLACEMENTS_EXTRACT}" == fl.REPLACEMENTS_PATH
    assert collector.COLLECTOR.name == fl.SOURCE
    assert collector.COLLECTOR.version <= fl.KNOWN_VERSION


# --- in stage "aliases" -----------------------------------------------------------------------------


def test_the_stage_queues_them_for_the_owner(tmp_path: Path, store: Store, http: MockHTTP) -> None:
    """With the families known, each proposal resolves and waits for gate A (no auto rule)."""
    cands = fl.FontsourceLegacy(transport=http.transport).mine(context(tmp_path, store))
    families = [
        FamilyRef("source-sans-3", "Source Sans 3", (k("fs-id", "source-sans-3"),)),
        FamilyRef("mulish", "Mulish", (k("gf-family", "Mulish"),)),
        FamilyRef("young-serif", "Young Serif", (k("fs-id", "young-serif"),)),
    ]
    # google/fonts history would give this rename; the Muli package follows it
    muli = AliasRow("Muli", "gf-family", "mulish", "rename", "", "gf_history", SNAP_DAY, "auto:x")
    result = merge_detailed(
        AliasTable.from_rows([muli]),
        cands,
        frozenset({"gf_history_rename", "nerd_unpatched"}),
        families=families,
        today=RUN_DAY,
    )
    outcome = {(o.candidate.alias.ns, o.candidate.alias.key): o for o in result.outcomes}
    for key, family in (
        (("fs-id", "source-sans-pro"), "source-sans-3"),
        (("npm", "@fontsource/source-sans-pro"), "source-sans-3"),
        (("npm", "@fontsource/muli"), "mulish"),
        (("fs-id", "muli"), "mulish"),
    ):
        o = outcome[key]
        assert (o.status, o.reason, o.family_id) == ("queued", "review", family), key
    # "@fontsource/youngserif" has the match_key of Young Serif's own package, which the
    # index already maps to it (aliases.PACKAGE_KEYS): nothing to ask.
    o = outcome[("npm", "@fontsource/youngserif")]
    assert (o.status, o.family_id) == ("known", "young-serif")
    assert not any(o.status == "accepted" for o in result.outcomes)
    assert outcome[("npm", "@fontsource/spartan")].reason == "target-unknown"


def test_a_deprecated_id_still_in_the_registry_asks_before_merging(
    tmp_path: Path, store: Store, http: MockHTTP
) -> None:
    """The registry keeps deprecated ids as families of their own (Source Sans Pro, status
    deprecated), so the universe has them: folding one into its replacement merges two
    families, and the stage says so. So does its npm package, which the index maps to the
    deprecated id's family by Fontsource's package naming (aliases.PACKAGE_KEYS)."""
    cands = fl.FontsourceLegacy(transport=http.transport).mine(context(tmp_path, store))
    families = [
        FamilyRef("source-sans-3", "Source Sans 3", (k("fs-id", "source-sans-3"),)),
        FamilyRef("source-sans-pro", "Source Sans Pro", (k("fs-id", "source-sans-pro"),)),
    ]
    result = merge_detailed(
        AliasTable.from_rows([]), cands, DEFAULT_AUTO_RULES, families=families, today=RUN_DAY
    )
    outcome = {(o.candidate.alias.ns, o.candidate.alias.key): o for o in result.outcomes}
    o = outcome[("fs-id", "source-sans-pro")]
    assert (o.status, o.reason, o.family_id) == ("queued", "other-family", "source-sans-3")
    o = outcome[("npm", "@fontsource/source-sans-pro")]
    assert (o.status, o.reason, o.family_id) == ("queued", "other-family", "source-sans-3")
    assert not any(o.status == "accepted" for o in result.outcomes)


# --- the committed seed file -----------------------------------------------------------------------


def test_committed_seed_file_is_canonical(tmp_path: Path) -> None:
    rows = load_seeds(SEEDS)
    assert rows
    assert seed_bytes(rows, tmp_path) == SEEDS.read_bytes()
    header = next(csv.reader(io.StringIO(SEEDS.read_text(encoding="utf-8"))))
    assert tuple(header) == SEED_COLUMNS
    targets: dict[SourceKey, set[SourceKey]] = {}
    for c in rows:
        assert c.source == "fontsource_legacy"
        assert not c.auto
        assert c.relation in MAPPING_RELATIONS
        assert c.detail == ""
        assert c.evidence.startswith(
            ("https://github.com/fontsource/", "https://cdn.jsdelivr.net/")
        )
        # an id is renamed, a package is a package; npm keys only in Fontsource's scopes
        assert (c.alias.ns, c.relation) in (("fs-id", "rename"), ("npm", "package"))
        if c.alias.ns == "npm":
            assert (fl.split_package(c.alias.key) or ("",))[0] in fl.SCOPES
        assert c.target.ns in ("fs-id", "gf-family", "font-name")
        targets.setdefault(c.alias, set()).add(c.target)
    assert all(len(t) == 1 for t in targets.values())  # one proposal per alias


def test_committed_seed_file_has_the_known_answer() -> None:
    rows = set(load_seeds(SEEDS))
    target = k("fs-id", "source-sans-3")
    assert any(
        (c.alias, c.target, c.relation) == (k("fs-id", "source-sans-pro"), target, "rename")
        for c in rows
    )
    assert any(
        (c.alias, c.target, c.relation)
        == (k("npm", "@fontsource/source-sans-pro"), target, "package")
        for c in rows
    )


# --- the real sources -----------------------------------------------------------------------------


@pytest.mark.network
def test_real_listings_and_metadata() -> None:
    with Fetcher(hosts=fl.HOSTS) as fetcher:
        packages = fl.fetch_packages(fetcher)
        assert {"@fontsource/inter", "@fontsource-variable/inter", "@fontsource/muli"} <= packages
        doc, evidence = fl.fetch_metadata(fetcher, "@fontsource/muli", LOG)
        # a 5.x package: the shape of any package Fontsource retires from now on
        doc5, evidence5 = fl.fetch_metadata(fetcher, "@fontsource/source-sans-pro", LOG)
    assert doc is not None
    assert evidence.startswith("https://cdn.jsdelivr.net/npm/@fontsource/muli@")
    [npm, fs] = fl.package_candidates("@fontsource/muli", doc, evidence)
    assert npm.target == fs.target == k("gf-family", "Muli")
    assert doc5 is not None
    assert evidence5.startswith("https://cdn.jsdelivr.net/npm/@fontsource/source-sans-pro@5.")
    [npm5, _] = fl.package_candidates("@fontsource/source-sans-pro", doc5, evidence5)
    assert npm5.target == k("gf-family", "Source Sans Pro")
