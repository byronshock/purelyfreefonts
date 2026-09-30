"""Alias miner "homebrew" (milestone-1 step 7): renamed Homebrew font casks.

The fixture (``fixtures/``, see its NOTICE) is trimmed real data from the
``homebrew_casks`` snapshot of 2026-09-26; the other cases use invented tokens.
"""

import json
import logging
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from tests.helpers import ROOT

from tff_catalog import aliases, clock, parse, stageio
from tff_catalog.aliases import AliasCandidate, AliasTable, FamilyRef
from tff_catalog.aliases.miners import MineContext, Miner
from tff_catalog.aliases.miners import homebrew as hb
from tff_catalog.collectors.universe import homebrew_casks as collector
from tff_catalog.paths import Paths, StoreNotConfigured
from tff_catalog.records import SourceKey
from tff_catalog.store import RawDir, Store

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 9, 26)
RENAMES_URL = hb.RENAMES_EVIDENCE

# What the miner proposes for the fixture: every font rename, old token -> current cask.
EXPECTED = [
    ("font-darwin", "font-hundar"),
    ("font-hanamina", "font-hanamin"),
    ("font-maple", "font-maple-mono"),
    ("font-monaspace-nerd-font", "font-monaspice-nerd-font"),
    ("font-mplus-nerd-font", "font-m+-nerd-font"),
    ("font-open-dyslexic", "font-opendyslexic"),
    ("font-open-dyslexic-nerd-font", "font-opendyslexic-nerd-font"),
    ("font-rounded-m+", "font-rounded-mplus"),
]


def cand(old: str, new: str, evidence: str = RENAMES_URL) -> AliasCandidate:
    return AliasCandidate(
        alias=SourceKey("brew-cask", old),
        target=SourceKey("brew-cask", new),
        relation="rename",
        detail="",
        source="homebrew",
        evidence=evidence,
        auto=False,
    )


def fixture_rows() -> list[dict[str, object]]:
    lines = (FIXTURES / "casks.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def fixture_renames() -> dict[str, str]:
    return json.loads((FIXTURES / "renames.json").read_text(encoding="utf-8"))


def write_snapshot(
    store: Path,
    day: date,
    rows: list[dict[str, object]],
    renames: dict[str, str] | None,
    version: int = 1,
) -> None:
    """A ``homebrew_casks`` snapshot as the collector's fetch writes it."""
    writer = Store(store).writer(hb.SOURCE, day, version)
    writer.write_jsonl(hb.CASKS_EXTRACT, rows)
    if renames is not None:
        writer.write_json(hb.RENAMES_EXTRACT, renames)
    writer.close()


def context(tmp_path: Path, store: Path | None, day: date = DAY) -> MineContext:
    paths = Paths.for_root(tmp_path / "repo", store=store, raw_root=tmp_path / "raw")
    raw = RawDir(tmp_path / "raw" / "aliases")
    return MineContext(paths, Store(store) if store else None, raw, day, logging.getLogger("t"))


@pytest.fixture
def real_ctx(tmp_path: Path) -> MineContext:
    store = tmp_path / "store"
    write_snapshot(store, DAY, fixture_rows(), fixture_renames())
    return context(tmp_path, store)


# --- the miner on the trimmed real snapshot ------------------------------------------------------


def test_miner_contract() -> None:
    assert isinstance(hb.MINER, Miner)
    assert hb.MINER.name == "homebrew" == hb.__name__.rsplit(".", 1)[-1]


def test_mines_every_font_rename(real_ctx: MineContext) -> None:
    found = hb.MINER.mine(real_ctx)
    assert found == [cand(old, new) for old, new in EXPECTED]


def test_leaves_out_renames_to_apps(
    real_ctx: MineContext, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    found = hb.MINER.mine(real_ctx)
    olds = {c.alias.key for c in found}
    assert "font-finagler" not in olds  # became the app fontfinagler
    assert "font-smoothing-adjuster" not in olds
    assert "font-finagler -> fontfinagler" in caplog.text


def test_deterministic_and_round_trips(real_ctx: MineContext, tmp_path: Path) -> None:
    first, second = hb.MINER.mine(real_ctx), hb.MINER.mine(real_ctx)
    assert first == second
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    aliases.write_seeds(first, a)
    aliases.write_seeds(reversed(second), b)
    assert a.read_bytes() == b.read_bytes()
    assert aliases.load_seeds(a) == first


def test_writes_nothing(real_ctx: MineContext) -> None:
    """The stage writes the seed file; the miner never writes data/ (least of all aliases.csv)."""
    hb.MINER.mine(real_ctx)
    assert not real_ctx.paths.data.exists()


# --- rename rules (invented tokens) ---------------------------------------------------------------


def casks(*tokens: str, **olds: list[str]) -> dict[str, tuple[str, ...]]:
    """A cask list; ``olds`` gives old tokens by token, spelt with ``_`` for ``-``."""
    rows = [{"token": t, "old_tokens": olds.get(t.replace("-", "_"), [])} for t in tokens]
    return hb.read_casks(rows)


def test_chain_ends_at_the_current_cask() -> None:
    renames = {"font-a": "font-b", "font-b": "font-c"}
    assert hb.candidates(casks("font-c"), renames) == [
        cand("font-a", "font-c"),
        cand("font-b", "font-c"),
    ]


def test_chain_stops_at_the_first_current_cask() -> None:
    renames = {"font-a": "font-b", "font-b": "font-c"}
    found = hb.candidates(casks("font-b", "font-c"), renames)
    # font-b is a cask again, so it keeps its own installs; font-a went to it.
    assert found == [cand("font-a", "font-b")]


def test_loops_and_dead_ends_give_nothing() -> None:
    renames = {"font-a": "font-b", "font-b": "font-a", "font-x": "font-gone"}
    assert hb.candidates(casks("font-c"), renames) == []


def test_old_tokens_alone_are_evidence() -> None:
    found = hb.candidates(casks("font-new", font_new=["font-old"]), {})
    assert found == [
        cand("font-old", "font-new", "https://formulae.brew.sh/api/cask/font-new.json")
    ]


def test_both_lists_give_one_candidate() -> None:
    found = hb.candidates(casks("font-new", font_new=["font-old"]), {"font-old": "font-new"})
    assert found == [cand("font-old", "font-new")]


def test_an_old_token_that_is_a_cask_again_is_left_out() -> None:
    found = hb.candidates(
        casks("font-old", "font-new", font_new=["font-old"]), {"font-old": "font-new"}
    )
    assert found == []


def test_two_claims_give_competing_candidates(caplog: pytest.LogCaptureFixture) -> None:
    found = hb.candidates(
        casks("font-b", "font-c", font_c=["font-a"]), {"font-a": "font-b"}, logging.getLogger("t")
    )
    assert found == [
        cand("font-a", "font-b"),
        cand("font-a", "font-c", "https://formulae.brew.sh/api/cask/font-c.json"),
    ]
    assert "font-a is an old token of font-b, font-c" in caplog.text


def test_nothing_is_paired_by_name_likeness() -> None:
    """Near-identical tokens with no rename entry stay apart."""
    assert hb.candidates(casks("font-inter", "font-inter-tight", "font-roboto-slab"), {}) == []


def test_read_casks_and_renames_skip_junk() -> None:
    rows = [
        {"token": " font-a ", "old_tokens": ["font-z", "", 3, "font-a", "font-y"]},
        {"token": "font-a", "old_tokens": ["font-x"]},
        {"token": None},
        "not a row",
    ]
    assert hb.read_casks(rows) == {"font-a": ("font-x", "font-y", "font-z")}
    assert hb.read_renames({"font-a": "font-b", "font-c": "font-c", "font-d": 1}) == {
        "font-a": "font-b"
    }
    with pytest.raises(ValueError, match="JSON object"):
        hb.read_renames(["font-a"])


# --- the snapshot it reads ---------------------------------------------------------------------------


def test_reads_the_latest_snapshot_up_to_the_run_date(tmp_path: Path) -> None:
    store = tmp_path / "store"
    rows = [{"token": "font-b", "old_tokens": []}, {"token": "font-c", "old_tokens": []}]
    write_snapshot(store, date(2026, 8, 1), rows, {"font-a": "font-b"})
    write_snapshot(store, date(2026, 9, 1), rows, {"font-a": "font-c"})
    assert hb.MINER.mine(context(tmp_path, store, date(2026, 8, 15))) == [cand("font-a", "font-b")]
    assert hb.MINER.mine(context(tmp_path, store, date(2026, 9, 1))) == [cand("font-a", "font-c")]


def parsed(paths: Paths, **days: date) -> None:
    """``build/stage/snapshots.json`` as the parse stage writes it: ``source=snapshot day``."""
    entry = parse.SourceSnapshot(
        snapshot=DAY,
        stale=False,
        data_date=None,
        window=None,
        fetched_at=None,
        collector_version=1,
        records=1,
        baselines=(),
    )
    doc = {source: replace(entry, snapshot=day) for source, day in sorted(days.items())}
    stageio.dump(doc, paths.stage / parse.SNAPSHOTS_FILE)


def test_reads_the_snapshot_the_run_parsed(tmp_path: Path) -> None:
    """snapshots.json pins the day the universe was built from; a newer snapshot is not read."""
    store = tmp_path / "store"
    rows = [{"token": "font-b", "old_tokens": []}, {"token": "font-c", "old_tokens": []}]
    write_snapshot(store, date(2026, 9, 1), rows, {"font-a": "font-b"})
    write_snapshot(store, DAY, rows, {"font-a": "font-c"})
    ctx = context(tmp_path, store)
    parsed(ctx.paths, homebrew_casks=date(2026, 9, 1))
    assert hb.MINER.mine(ctx) == [cand("font-a", "font-b")]
    parsed(ctx.paths, homebrew_casks=date(2026, 8, 1))
    with pytest.raises(FileNotFoundError, match="homebrew_casks snapshot 2026-08-01"):
        hb.MINER.mine(ctx)
    # Parse did not read homebrew_casks (--only, or dropped): the newest one up to the run date.
    parsed(ctx.paths, fontsource=date(2026, 9, 1))
    assert hb.MINER.mine(ctx) == [cand("font-a", "font-c")]


def test_a_newer_extract_format_fails(tmp_path: Path) -> None:
    store = tmp_path / "store"
    write_snapshot(store, DAY, fixture_rows(), fixture_renames(), version=hb.FORMAT_VERSION + 1)
    with pytest.raises(ValueError, match="extract version 2 is newer"):
        hb.MINER.mine(context(tmp_path, store))


def test_no_renames_extract_reads_old_tokens(tmp_path: Path) -> None:
    store = tmp_path / "store"
    write_snapshot(store, DAY, [{"token": "font-b", "old_tokens": ["font-a"]}], None)
    found = hb.MINER.mine(context(tmp_path, store))
    assert found == [cand("font-a", "font-b", "https://formulae.brew.sh/api/cask/font-b.json")]


def test_no_snapshot_or_store_fails(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    with pytest.raises(FileNotFoundError, match="tff-catalog fetch --only homebrew_casks"):
        hb.MINER.mine(context(tmp_path, store))
    with pytest.raises(StoreNotConfigured):
        hb.MINER.mine(context(tmp_path, None))


def test_empty_cask_list_fails(tmp_path: Path) -> None:
    store = tmp_path / "store"
    write_snapshot(store, DAY, [], {"font-a": "font-b"})
    with pytest.raises(ValueError, match="no font cask"):
        hb.MINER.mine(context(tmp_path, store))


def test_extract_names_match_the_collector() -> None:
    """A change to the collector's extracts (names or format version) must reach this miner."""
    mine = (hb.SOURCE, hb.CASKS_EXTRACT, hb.RENAMES_EXTRACT, hb.NAMESPACE, hb.FORMAT_VERSION)
    theirs = (
        collector.NAME,
        collector.CASKS_EXTRACT,
        collector.RENAMES_EXTRACT,
        collector.NAMESPACE,
        collector.COLLECTOR.version,
    )
    assert mine == theirs


@pytest.mark.parametrize(
    ("rows", "renames"),
    [
        pytest.param(fixture_rows(), fixture_renames(), id="fixture"),
        pytest.param(
            [
                {"token": "font-b", "old_tokens": []},
                {"token": "font-c", "old_tokens": ["font-a", "font-b", "font-z"]},
                {"token": "font-d", "old_tokens": ["font-z"]},
            ],
            {
                "font-a": "font-b",
                "font-b": "font-c",
                "font-x": "font-y",
                "font-y": "font-c",
                "font-l": "font-m",
                "font-m": "font-l",
                "font-app": "app",
            },
            id="chains-loops-claims",
        ),
    ],
)
def test_agrees_with_the_collector(rows: list[dict[str, object]], renames: dict[str, str]) -> None:
    """The universe record of a cask lists as ``rename`` names exactly the old tokens this
    miner sends to its key (the collector's ``old_names`` states the same rule)."""
    by_token = {str(r["token"]): r for r in rows}
    expected = {
        (old, new) for new, olds in collector.old_names(by_token, renames).items() for old in olds
    }
    found = hb.candidates(hb.read_casks(rows), hb.read_renames(renames))
    assert {(c.alias.key, c.target.key) for c in found} == expected


def test_fixture_follows_ruling_t1() -> None:
    """Trimmed real data (at most 50 rows, token and old_tokens only) with a NOTICE."""
    notice = (FIXTURES / "NOTICE").read_text(encoding="utf-8")
    for needle in ("BSD", "formulae.brew.sh", "cask_renames.json"):
        assert needle in notice
    rows = fixture_rows()
    assert len(rows) <= 50
    assert len(fixture_renames()) <= 50
    assert all(set(r) == {"token", "old_tokens"} for r in rows)


# --- with the aliases stage -----------------------------------------------------------------------


def test_merge_queues_them_for_the_owner(real_ctx: MineContext) -> None:
    """Never auto-accepted; a rename whose key already matches is known, not asked."""
    fams = [
        FamilyRef("maple-mono", "Maple Mono", (SourceKey("brew-cask", "font-maple-mono"),)),
        FamilyRef("opendyslexic", "OpenDyslexic", (SourceKey("brew-cask", "font-opendyslexic"),)),
    ]
    result = aliases.merge_detailed(
        AliasTable(()),
        hb.MINER.mine(real_ctx),
        aliases.DEFAULT_AUTO_RULES,
        families=fams,
        today=DAY,
    )
    by_alias = {o.candidate.alias.key: o for o in result.outcomes}
    assert (by_alias["font-maple"].status, by_alias["font-maple"].reason) == ("queued", "review")
    assert by_alias["font-maple"].family_id == "maple-mono"
    # match_key("font-open-dyslexic") == match_key("font-opendyslexic"): already mapped.
    assert by_alias["font-open-dyslexic"].status == "known"
    assert not [o for o in result.outcomes if o.status == "accepted"]
    assert result.rows == ()


# --- the committed seed file ---------------------------------------------------------------------


SEEDS = ROOT / "data" / "alias-seeds" / "homebrew.csv"


def test_committed_seeds_are_canonical(tmp_path: Path) -> None:
    rows = aliases.load_seeds(SEEDS)
    assert rows
    assert {(c.alias.ns, c.target.ns, c.relation, c.source, c.auto) for c in rows} == {
        ("brew-cask", "brew-cask", "rename", "homebrew", False)
    }
    again = tmp_path / "homebrew.csv"
    aliases.write_seeds(rows, again)
    assert again.read_bytes() == SEEDS.read_bytes()


# --- real data ---------------------------------------------------------------------------------------


@pytest.mark.store
def test_latest_real_snapshot(tmp_path: Path) -> None:
    """Invariants on the private store's newest homebrew_casks snapshot."""
    store = Store(Path(os.environ["TFF_STORE"]).expanduser())
    snap = store.latest(hb.SOURCE, clock.utc_today())
    if snap is None:
        pytest.skip("no homebrew_casks snapshot in TFF_STORE")
    cask_list, renames = hb.read_snapshot(snap)
    found = hb.candidates(cask_list, renames)
    assert found, "Homebrew always lists some font renames"
    assert all(c.target.key in cask_list for c in found)
    assert not any(c.alias.key in cask_list for c in found)
    olds = {old for olds in cask_list.values() for old in olds}
    proposed = {c.alias.key for c in found}
    assert olds - {o for o in olds if o in cask_list} <= proposed


@pytest.mark.network
def test_evidence_links_resolve() -> None:
    """The evidence links point at Homebrew's live lists (3 requests, 1 per second per host)."""
    from tff_catalog.fetch import Fetcher

    fetcher = Fetcher(hosts=["github.com", "formulae.brew.sh"])
    assert fetcher.get(hb.RENAMES_EVIDENCE).status == 200
    page = fetcher.get(hb.CASK_EVIDENCE.format(token="font-maple-mono")).json()
    assert page["token"] == "font-maple-mono"
    assert "font-maple" in page["old_tokens"]
    page = fetcher.get(hb.CASK_EVIDENCE.format(token="font-m+-nerd-font")).json()
    assert "font-mplus-nerd-font" in page["old_tokens"]
