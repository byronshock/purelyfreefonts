"""The alias miner "nerd": Nerd Fonts' fonts.json as build aliases (milestone-1 step 7).

The fixture is 13 trimmed real entries of ``fonts.json`` (ruling T1, see
``fixtures/NOTICE``), put in a store built per test. ``fixtures/expected.csv``
is the golden seed file; after an intended change, rewrite it with
``uv run python -c "from tests.aliases.nerd.test_nerd import regen; regen()"``
and read the diff. The committed ``data/alias-seeds/nerd.csv`` (a real run) is
checked for shape, and one test marked ``network`` fetches the real file.
"""

import importlib
import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT

from tff_catalog import clock, parse, stageio
from tff_catalog.aliases import (
    BUILD_DETAILS,
    DEFAULT_AUTO_RULES,
    AliasCandidate,
    AliasRow,
    AliasTable,
    FamilyRef,
    load_seeds,
    merge_detailed,
    write_seeds,
)
from tff_catalog.aliases.miners import MineContext, Miner
from tff_catalog.aliases.miners.nerd import (
    CASK_SUFFIX,
    MINER,
    SUFFIXES,
    build_names,
    candidates,
    cask,
    cask_differences,
    clashes,
    evidence,
    nerd_casks,
)
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.universe import homebrew_casks, nerdfonts
from tff_catalog.fetch import Fetcher
from tff_catalog.keys import match_key
from tff_catalog.names import NERD_SUFFIX, mint_id
from tff_catalog.paths import Paths
from tff_catalog.records import NAMESPACES, SourceKey
from tff_catalog.store import RawDir, Store

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = FIXTURES / "expected.csv"
COMMITTED = ROOT / "data" / "alias-seeds" / "nerd.csv"
DAY = date(2026, 10, 1)  # the run date
SNAPSHOT_DAY = date(2026, 9, 26)
COMMIT = "64a084f95480ee5efb62132c01cd142ab6147655"
OTHER_COMMIT = "0123456789abcdef0123456789abcdef01234567"
LOG = logging.getLogger("tests.miners.nerd")
# The suffix Nerd Fonts writes -> its alias detail, as names.NERD_SUFFIX's group reads it.
SUFFIX_DETAILS = {
    "nerdfont": "nerd",
    "nerdfontmono": "nfm",
    "nerdfontpropo": "propo",
    "nf": "nf",
    "nfm": "nfm",
    "nfp": "nfp",
}


def entries() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "fonts.json").read_text(encoding="utf-8"))["fonts"]


def pin_doc(commit: str = COMMIT) -> dict[str, Any]:
    return {**json.loads((FIXTURES / "pin.json").read_text(encoding="utf-8")), "commit": commit}


def pin() -> nerdfonts.Pin:
    return nerdfonts.Pin.from_json(pin_doc())


def put_nerd(root: Path, day: date, fonts: list[dict[str, Any]], commit: str = COMMIT) -> None:
    """A complete ``nerdfonts`` snapshot of ``fonts`` in the store at ``root``."""
    writer = Store(root).writer(nerdfonts.NAME, day, nerdfonts.COLLECTOR.version)
    body = json.dumps({"fonts": fonts}, indent=2, ensure_ascii=False).encode()
    writer.write_bytes(nerdfonts.FONTS, body, rows=len(fonts))
    writer.write_json(nerdfonts.PIN, pin_doc(commit))
    writer.close()


def put_casks(root: Path, day: date, tokens: list[str]) -> Path:
    """A ``homebrew_casks`` snapshot listing ``tokens`` (only the field the miner reads)."""
    writer = Store(root).writer(homebrew_casks.NAME, day, homebrew_casks.COLLECTOR.version)
    writer.write_jsonl(homebrew_casks.CASKS_EXTRACT, ({"token": t} for t in sorted(tokens)))
    return writer.close().path


def context(tmp: Path, store: Store | None, day: date = DAY) -> MineContext:
    return MineContext(Paths.for_root(tmp), store, RawDir(tmp / "raw"), day, LOG)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    put_nerd(tmp_path / "store", SNAPSHOT_DAY, entries())
    return Store(tmp_path / "store")


def mine(tmp: Path, store: Store | None, day: date = DAY) -> list[AliasCandidate]:
    return list(MINER.mine(context(tmp, store, day)))


def names_of(found: list[AliasCandidate], folder: str) -> set[tuple[str, str]]:
    return {
        (c.alias.key, c.detail)
        for c in found
        if c.target.key == folder and c.alias.ns == "font-name"
    }


def regen() -> None:
    """Rewrite ``fixtures/expected.csv`` from the fixture (run from the repository root)."""
    write_seeds(candidates(entries(), pin()), EXPECTED)


# --- the contract ---------------------------------------------------------------------------


def test_is_a_miner_named_after_its_module() -> None:
    module = importlib.import_module("tff_catalog.aliases.miners.nerd")
    assert module.MINER is MINER
    assert isinstance(MINER, Miner)
    assert MINER.name == "nerd" == module.__name__.rpartition(".")[2]


def test_golden_seed_file(tmp_path: Path, store: Store) -> None:
    out = tmp_path / "nerd.csv"
    write_seeds(mine(tmp_path, store), out)
    assert out.read_text(encoding="utf-8") == EXPECTED.read_text(encoding="utf-8")


def test_same_input_same_output(tmp_path: Path, store: Store) -> None:
    first, second = mine(tmp_path, store), mine(tmp_path, store)
    assert first == second == sorted(set(first))
    assert candidates(reversed(entries()), pin()) == first


def test_seed_file_round_trips(tmp_path: Path, store: Store) -> None:
    found = mine(tmp_path, store)
    write_seeds(found, tmp_path / "nerd.csv")
    assert load_seeds(tmp_path / "nerd.csv") == found


def test_every_candidate_is_an_auto_build_row(tmp_path: Path, store: Store) -> None:
    found = mine(tmp_path, store)
    assert {(c.relation, c.source, c.auto) for c in found} == {("build", "nerd", True)}
    assert {c.alias.ns for c in found} == {"font-name", "brew-cask"}
    assert {c.detail for c in found} <= BUILD_DETAILS - {""}
    for c in found:
        row = AliasRow(
            c.alias.key,
            c.alias.ns,
            "some-family",
            c.relation,
            c.detail,
            c.source,
            DAY,
            "auto:nerd_unpatched",
        )
        assert row.problems() == [], c


# --- what is proposed ---------------------------------------------------------------------------


def test_targets_are_the_entries_own_universe_keys(tmp_path: Path, store: Store) -> None:
    """Each candidate names its family by the key the nerdfonts collector gives the entry."""
    found = mine(tmp_path, store)
    settings = nerdfonts.Settings()
    keys = {nerdfonts.universe_record(e, pin(), settings).key for e in entries()}
    assert {c.target for c in found} == keys
    assert {c.target.ns for c in found} <= NAMESPACES


def test_one_build_row_per_listed_cask(tmp_path: Path, store: Store) -> None:
    found = mine(tmp_path, store)
    casks = {c.alias.key: c for c in found if c.alias.ns == "brew-cask"}
    assert {k: c.target.key for k, c in casks.items()} == nerd_casks(entries())
    assert nerd_casks(entries())["font-caskaydia-cove-nerd-font"] == "CascadiaCode"
    assert nerd_casks(entries())["font-m+-nerd-font"] == "MPlus"
    assert nerd_casks(entries())["font-symbols-only-nerd-font"] == "NerdFontsSymbolsOnly"
    assert {c.detail for c in casks.values()} == {"nerd"}
    assert len(casks) == len(entries())
    assert all(k.endswith(CASK_SUFFIX) for k in casks)


def test_a_reserved_font_name_gives_the_patched_name_and_its_builds(
    tmp_path: Path, store: Store
) -> None:
    assert names_of(mine(tmp_path, store), "CascadiaCode") == {
        ("CaskaydiaCove", "nerd"),
        ("CaskaydiaCove Nerd Font", "nerd"),
        ("CaskaydiaCove Nerd Font Mono", "nfm"),
        ("CaskaydiaCove Nerd Font Propo", "propo"),
        ("CaskaydiaCove NF", "nf"),
        ("CaskaydiaCove NFM", "nfm"),
        ("CaskaydiaCove NFP", "nfp"),
    }


@pytest.mark.parametrize(
    ("folder", "patched"),
    [
        ("Hack", "Hack"),
        ("IosevkaTerm", "IosevkaTerm"),  # "Iosevka Term" has the same match key
        ("Meslo", "MesloLG"),  # "Meslo LG"
        ("InconsolataLGC", "Inconsolata LGC"),
    ],
)
def test_no_bare_name_when_it_is_the_original(
    tmp_path: Path, store: Store, folder: str, patched: str
) -> None:
    got = names_of(mine(tmp_path, store), folder)
    assert got == {(f"{patched} {suffix}", detail) for suffix, detail in SUFFIXES}


def test_folder_names_are_never_names(tmp_path: Path, store: Store) -> None:
    """ "Recursive" (Recursive Mono's folder) or "Meslo" would claim another family."""
    found = mine(tmp_path, store)
    aliases = {match_key(c.alias.key) for c in found if c.alias.ns == "font-name"}
    for folder in ("Recursive", "Meslo", "NerdFontsSymbolsOnly", "MPlus", "Go-Mono"):
        assert match_key(folder) not in aliases, folder
    assert ("RecMono", "nerd") in names_of(found, "Recursive")
    assert ("M+", "nerd") in names_of(found, "MPlus")
    assert ("Symbols Nerd Font", "nerd") in names_of(found, "NerdFontsSymbolsOnly")


def test_suffixes_are_what_names_nerd_suffix_reads(tmp_path: Path, store: Store) -> None:
    """Every built name splits back into its patched name and a suffix of its detail."""
    patched = {e["folderName"]: e["patchedName"] for e in entries()}
    for c in mine(tmp_path, store):
        if c.alias.ns != "font-name":
            continue
        m = NERD_SUFFIX.search(c.alias.key)
        if m is None:  # the bare patched name
            assert c.alias.key == patched[c.target.key]
            continue
        assert c.alias.key[: m.start()] == patched[c.target.key]
        assert SUFFIX_DETAILS[match_key(m.group("build"))] == c.detail


def test_evidence_is_the_file_at_the_pinned_commit(tmp_path: Path, store: Store) -> None:
    found = mine(tmp_path, store)
    base = f"https://github.com/ryanoasis/nerd-fonts/blob/{COMMIT}/bin/scripts/lib/fonts.json"
    assert {c.evidence for c in found if c.target.key == "Go-Mono"} == {f"{base}#Go-Mono"}
    assert evidence(pin(), "a b") == f"{base}#a%20b"


# --- input -------------------------------------------------------------------------------------


def test_reads_the_newest_snapshot_on_or_before_the_run_date(tmp_path: Path) -> None:
    root = tmp_path / "store"
    hack = [e for e in entries() if e["folderName"] == "Hack"]
    put_nerd(root, date(2026, 9, 1), hack, OTHER_COMMIT)
    put_nerd(root, date(2026, 10, 15), entries())
    found = mine(tmp_path, Store(root), DAY)
    assert {c.target.key for c in found} == {"Hack"}
    assert all(OTHER_COMMIT in c.evidence for c in found)
    assert len(mine(tmp_path, Store(root), date(2026, 10, 15))) > len(found)


def test_fails_rather_than_proposing_nothing(tmp_path: Path) -> None:
    """An empty answer would wipe the committed seeds; a failure keeps them."""
    with pytest.raises(FileNotFoundError, match="TFF_STORE"):
        mine(tmp_path, None)
    with pytest.raises(FileNotFoundError, match="no nerdfonts snapshot"):
        mine(tmp_path, Store(tmp_path / "empty"))
    put_nerd(tmp_path / "later", date(2026, 11, 1), entries())
    with pytest.raises(FileNotFoundError, match="on or before 2026-10-01"):
        mine(tmp_path, Store(tmp_path / "later"))


def used_snapshots(tmp: Path, day: date) -> None:
    """The parse stage's ``snapshots.json`` under ``tmp``, naming ``nerdfonts`` at ``day``."""
    used = parse.SourceSnapshot(day, False, day, None, None, nerdfonts.COLLECTOR.version, 1, ())
    stageio.dump({nerdfonts.NAME: used}, Paths.for_root(tmp).stage / parse.SNAPSHOTS_FILE)


def test_reads_the_snapshot_the_parse_stage_used(tmp_path: Path) -> None:
    """The targets must be the keys of the universe the merge resolves them against."""
    root = tmp_path / "store"
    hack = [e for e in entries() if e["folderName"] == "Hack"]
    put_nerd(root, date(2026, 9, 1), hack, OTHER_COMMIT)
    put_nerd(root, SNAPSHOT_DAY, entries())
    assert len(mine(tmp_path, Store(root))) > len(SUFFIXES) + 1  # newest by default
    used_snapshots(tmp_path, date(2026, 9, 1))
    found = mine(tmp_path, Store(root))
    assert {c.target.key for c in found} == {"Hack"}
    assert all(OTHER_COMMIT in c.evidence for c in found)
    used_snapshots(tmp_path, date(2026, 8, 1))
    with pytest.raises(FileNotFoundError, match=r"of 2026-08-01 \(the parse stage's\)"):
        mine(tmp_path, Store(root))


def test_a_folder_listed_twice_keeps_its_first_entry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """As the nerdfonts collector parses it, so no name is missing from the universe record."""
    hack = next(e for e in entries() if e["folderName"] == "Hack")
    again = {**hack, "patchedName": "Hock", "caskName": "hock"}
    put_nerd(tmp_path / "store", SNAPSHOT_DAY, [hack, again])
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        found = mine(tmp_path, Store(tmp_path / "store"))
    assert found == candidates([hack], pin())
    assert nerd_casks([hack, again]) == {"font-hack-nerd-font": "Hack"}
    assert "folder Hack is listed 2 times; the first entry is kept" in caplog.text


def test_a_name_two_folders_share_is_reported_and_queued(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """fonts.json never means two folders by one name; the owner decides which one holds."""
    hack = next(e for e in entries() if e["folderName"] == "Hack")
    twin = {**hack, "folderName": "HackTwin", "unpatchedName": "Hack Twin"}
    put_nerd(tmp_path / "store", SNAPSHOT_DAY, [hack, twin])
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        found = mine(tmp_path, Store(tmp_path / "store"))
    both = [SourceKey("nerd-folder", "Hack"), SourceKey("nerd-folder", "HackTwin")]
    shared = clashes(found)
    assert shared[SourceKey("brew-cask", "font-hack-nerd-font")] == both
    assert shared[SourceKey("font-name", "Hack NF")] == both
    assert len(shared) == len(SUFFIXES) + 1  # "Hack" itself is the Hack Twin row alone
    assert nerd_casks([twin, hack]) == {"font-hack-nerd-font": "Hack"}
    assert "brew-cask:font-hack-nerd-font is proposed for nerd-folder:Hack, " in caplog.text
    fams = [
        FamilyRef("hack", "Hack", (both[0],)),
        FamilyRef("hack-twin", "Hack Twin", (both[1],)),
    ]
    result = merge_detailed(AliasTable(()), found, DEFAULT_AUTO_RULES, families=fams, today=DAY)
    status = {(o.candidate.alias.key, o.status, o.reason) for o in result.outcomes}
    assert ("font-hack-nerd-font", "queued", "competing") in status
    assert ("Hack", "queued", "other-family") in status  # the bare name is Hack's own
    assert not {o.status for o in result.outcomes} & {"accepted"}


def test_entries_without_a_usable_cask_or_patched_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fonts = entries()
    by_folder = {e["folderName"]: e for e in fonts}
    del by_folder["Hack"]["caskName"]
    by_folder["JetBrainsMono"]["caskName"] = "JetBrains Mono"
    del by_folder["CascadiaCode"]["patchedName"]
    by_folder["Meslo"]["patchedName"] = "   "
    put_nerd(tmp_path / "store", SNAPSHOT_DAY, fonts)
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        found = mine(tmp_path, Store(tmp_path / "store"))
    targets = Counter(c.target.key for c in found)
    assert targets["Hack"] == targets["JetBrainsMono"] == len(SUFFIXES)
    assert targets["CascadiaCode"] == targets["Meslo"] == 1  # the cask alone
    assert "font-hack-nerd-font" not in {c.alias.key for c in found}
    assert "Hack has no usable caskName: None" in caplog.text
    assert "JetBrainsMono has no usable caskName: 'JetBrains Mono'" in caplog.text


def test_build_names_collapse_white_space() -> None:
    entry = {"unpatchedName": "Foo Sans", "patchedName": " Bar \t Sans ", "folderName": "Foo"}
    assert build_names(entry)[:2] == [("Bar Sans", "nerd"), ("Bar Sans Nerd Font", "nerd")]
    assert build_names({**entry, "patchedName": 7}) == []
    assert cask({"caskName": "m+"}) == "font-m+-nerd-font"
    assert cask({"caskName": "-x"}) is None


# --- the Nerd cask list against Homebrew ------------------------------------------------------------


def test_cask_differences() -> None:
    listed = ["font-a-nerd-font", "font-b-nerd-font"]
    brew = ["font-b-nerd-font", "font-c-nerd-font", "font-hackgen-nerd", "font-c"]
    assert cask_differences(listed, brew) == (["font-a-nerd-font"], ["font-c-nerd-font"])
    assert nerdfonts.cask_token("x").endswith(CASK_SUFFIX)


def test_logs_where_homebrew_and_fonts_json_disagree(
    tmp_path: Path, store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    plain = mine(tmp_path, store)
    tokens = set(nerd_casks(entries())) - {"font-hack-nerd-font"}
    tokens |= {"font-monocraft-nerd-font", "font-hackgen-nerd", "font-hack"}
    put_casks(store.root, SNAPSHOT_DAY, sorted(tokens))
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        assert mine(tmp_path, store) == plain  # a report only: nothing is mapped
    assert "fonts.json casks Homebrew lacks: font-hack-nerd-font" in caplog.text
    assert "does not list (need a hand row): font-monocraft-nerd-font" in caplog.text
    assert "font-hackgen-nerd" not in caplog.text


def test_a_broken_homebrew_snapshot_is_only_reported(
    tmp_path: Path, store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    plain = mine(tmp_path, store)
    path = put_casks(store.root, SNAPSHOT_DAY, ["font-hack"])
    (path / homebrew_casks.CASKS_EXTRACT).write_bytes(b"not what the manifest says")
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        assert mine(tmp_path, store) == plain
    assert "cannot read homebrew_casks 2026-09-26" in caplog.text


# --- through the aliases stage's merge ---------------------------------------------------------


def families(*extra: FamilyRef) -> list[FamilyRef]:
    """One family per fixture entry, named by its unpatchedName and keyed by its folder."""
    out: list[FamilyRef] = list(extra)
    for e in entries():
        fid = mint_id(e["unpatchedName"], {f.id for f in out})
        out.append(FamilyRef(fid, e["unpatchedName"], (SourceKey("nerd-folder", e["folderName"]),)))
    return out


def test_the_merge_accepts_every_candidate_by_the_nerd_rule(tmp_path: Path, store: Store) -> None:
    found = mine(tmp_path, store)
    result = merge_detailed(
        AliasTable(()), found, DEFAULT_AUTO_RULES, families=families(), today=DAY
    )
    assert {o.status for o in result.outcomes} == {"accepted"}
    assert not result.queue
    assert {r.reviewed_by for r in result.rows} == {"auto:nerd_unpatched"}
    by_alias = {(r.ns, r.alias): r for r in result.rows}
    assert by_alias[("brew-cask", "font-caskaydia-cove-nerd-font")].family_id == "cascadia-code"
    assert by_alias[("font-name", "RecMono Nerd Font")].family_id == "recursive-mono"
    assert by_alias[("font-name", "3270 NFM")].family_id == "ibm-3270"


def test_the_merge_queues_a_name_of_another_family(tmp_path: Path, store: Store) -> None:
    """Nerd calls Fira Mono's original "Fira": "FiraMono" is then another family's name."""
    found = mine(tmp_path, store)
    fira_mono = FamilyRef("fira-mono", "Fira Mono", (SourceKey("gf-family", "Fira Mono"),))
    result = merge_detailed(
        AliasTable(()), found, DEFAULT_AUTO_RULES, families=families(fira_mono), today=DAY
    )
    status = {(o.candidate.alias.key, o.status, o.reason) for o in result.outcomes}
    assert ("FiraMono", "queued", "other-family") in status
    assert ("FiraMono Nerd Font", "accepted", "") in status
    assert [(q.alias.key, q.family_id) for q in result.queue] == [("FiraMono", "fira")]


def test_a_cask_the_universe_already_folded_is_known(tmp_path: Path, store: Store) -> None:
    found = mine(tmp_path, store)
    fams = [
        FamilyRef(f.id, f.name, (*f.keys, SourceKey("brew-cask", "font-hack-nerd-font")))
        if f.id == "hack"
        else f
        for f in families()
    ]
    result = merge_detailed(AliasTable(()), found, DEFAULT_AUTO_RULES, families=fams, today=DAY)
    known = [o.candidate.alias.key for o in result.outcomes if o.status == "known"]
    assert known == ["font-hack-nerd-font"]


# --- the committed seeds and the real file ----------------------------------------------------------


def test_the_committed_seed_file_is_well_formed() -> None:
    rows = load_seeds(COMMITTED)
    assert rows == sorted(set(rows))
    assert {(c.relation, c.source, c.auto) for c in rows} == {("build", "nerd", True)}
    assert {c.detail for c in rows} <= BUILD_DETAILS - {""}
    casks = [c for c in rows if c.alias.ns == "brew-cask"]
    assert len(casks) >= 60
    assert all(c.alias.key.endswith(CASK_SUFFIX) for c in casks)
    assert {c.target.ns for c in rows} == {"nerd-folder"}
    keys = Counter((c.alias.ns, match_key(c.alias.key)) for c in rows)
    assert max(keys.values()) == 1  # no name proposed for two families


@pytest.mark.network
def test_real_fonts_json(tmp_path: Path) -> None:
    """Fetch the real file with the nerdfonts collector, then mine it."""
    store = Store(tmp_path / "store")
    day = clock.utc_today()
    settings = load_settings(nerdfonts.COLLECTOR, Paths.for_root(ROOT))
    with (
        Fetcher(log=LOG) as fetcher,
        store.writer(nerdfonts.NAME, day, nerdfonts.COLLECTOR.version) as writer,
    ):
        nerdfonts.COLLECTOR.fetch(
            FetchContext(
                run_date=day,
                fetcher=fetcher.scoped(nerdfonts.COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp_path / "raw"),
                previous=None,
                settings=settings,
                log=LOG,
            )
        )
    found = mine(tmp_path, store, day)
    casks = {c.alias.key: c.target.key for c in found if c.alias.ns == "brew-cask"}
    assert len(casks) >= 60
    assert casks["font-jetbrains-mono-nerd-font"] == "JetBrainsMono"
    assert ("CaskaydiaCove Nerd Font", "nerd") in names_of(found, "CascadiaCode")
    write_seeds(found, tmp_path / "nerd.csv")
    assert load_seeds(tmp_path / "nerd.csv") == found
