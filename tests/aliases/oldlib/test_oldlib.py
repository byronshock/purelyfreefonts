"""Alias miner "oldlib" (milestone-1 step 7): the old top-100 run's name tables.

The fixtures (``fixtures/``, see its NOTICE) are synthetic copies of the seed
files' shapes. The ``store`` test runs the miner on the real seed in
``$TFF_STORE`` and checks the committed ``data/alias-seeds/oldlib.csv``.
"""

import json
import logging
import os
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
from tests.helpers import ROOT

from tff_catalog import aliases
from tff_catalog.aliases import (
    BUILD_DETAILS,
    DEFAULT_AUTO_RULES,
    INELIGIBLE_REASONS,
    AliasCandidate,
    AliasRow,
    AliasTable,
    FamilyRef,
)
from tff_catalog.aliases.miners import MineContext, Miner, oldlib
from tff_catalog.keys import match_key
from tff_catalog.paths import Paths
from tff_catalog.records import NAMESPACES, SourceKey
from tff_catalog.store import RawDir, Store

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 9, 26)
SEED_CSV = ROOT / "data" / "alias-seeds" / "oldlib.csv"

# (alias ns, alias, target, relation, detail) for the fixture; an ineligible row targets itself.
EXPECTED = {
    ("nerd-folder", "CascadiaMono", "Cascadia Code", "build", "nerd"),
    ("nerd-folder", "IosevkaTerm", "Iosevka", "build", "nerd"),
    ("nerd-folder", "JetBrainsMono", "JetBrains Mono", "build", "nerd"),
    ("arch-pkg", "ttf-hack", "Hack", "package", ""),
    ("arch-pkg", "ttf-hack-nerd", "Hack", "build", "nerd"),
    ("arch-pkg", "ttf-meslo-nerd-font-powerlevel10k", "Meslo LG", "build", "nerd"),
    ("arch-pkg", "ttf-nerd-fonts-symbols", None, "ineligible", "icon"),
    ("arch-pkg", "ttf-rubik-vf", "Rubik", "build", "variable"),
    ("arch-pkg", "gnu-free-fonts", "FreeFont", "package", ""),  # the canon renames the target
    ("deb-pkg", "fonts-firacode", "Fira Code", "package", ""),
    ("deb-pkg", "fonts-font-awesome", None, "ineligible", "icon"),
    ("deb-pkg", "fonts-hack", "Hack", "package", ""),
    ("deb-pkg", "fonts-roboto-slab", "Roboto Slab", "package", ""),
    ("brew-cask", "font-inter", "Inter", "package", ""),
    ("brew-cask", "font-jetbrains-mono", "JetBrains Mono", "package", ""),
    ("brew-cask", "font-lexend", "Lexend", "package", ""),
    ("brew-cask", "font-powerline-symbols", None, "ineligible", "icon"),
    ("brew-cask", "font-roboto", "Roboto", "package", ""),
    ("brew-cask", "font-roboto-slab", "Roboto Slab", "package", ""),
    ("brew-cask", "font-source-code-pro", "Source Code Pro", "package", ""),
    ("font-name", "Muli", "Mulish", "rename", ""),
    ("font-name", "Geist Sans", "Geist", "rename", ""),
    ("font-name", "GNU FreeFont", "FreeFont", "rename", ""),
    ("font-name", "Lato Hairline", "Lato", "build", ""),
    ("font-name", "Pretendard Variable", "Pretendard", "build", "variable"),
    ("font-name", "Noto Sans CJK JP", "Noto Sans JP", "build", "cjk"),
    ("font-name", "源ノ角ゴシック JP", "Noto Sans JP", "build", "cjk"),
    ("font-name", "Futura PT Web", None, "ineligible", "proprietary"),
    ("font-name", "Futura PT", None, "ineligible", "proprietary"),
    ("font-name", "Font Awesome Free", None, "ineligible", "icon"),
    ("font-name", "fa-brands", None, "ineligible", "icon"),
    ("font-name", "Font Awesome Brands", None, "ineligible", "icon"),
    ("font-name", "icomoon", None, "ineligible", "icon"),
    ("font-name", "Copyright Example Type Co", None, "ineligible", "proprietary"),
    ("font-name", "My Font", None, "ineligible", "generic"),
    ("font-name", "Satoshi", None, "ineligible", "itf"),
}


def make_store(root: Path) -> Path:
    """A store holding the fixture seed files where the real ones live."""
    for name, rel in (
        ("agg_mono.py.txt", oldlib.AGG_MONO),
        ("brew365.json", oldlib.BREW365),
        ("canon_items.json", oldlib.CANON),
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / name, root / rel)
    return root


def context(tmp_path: Path, store: Path | None) -> MineContext:
    paths = Paths.for_root(tmp_path / "repo", store=store, raw_root=tmp_path / "raw")
    raw = RawDir(tmp_path / "raw" / "aliases")
    return MineContext(paths, Store(store) if store else None, raw, DAY, logging.getLogger("t"))


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return make_store(tmp_path / "store")


@pytest.fixture
def ctx(tmp_path: Path, store: Path) -> MineContext:
    return context(tmp_path, store)


def shape(c: AliasCandidate) -> tuple[str, str, str | None, str, str]:
    if c.relation == "ineligible":
        assert c.target == c.alias
        return c.alias.ns, c.alias.key, None, c.relation, c.detail
    assert c.target.ns == oldlib.TARGET_NS
    return c.alias.ns, c.alias.key, c.target.key, c.relation, c.detail


def by_alias(found: list[AliasCandidate]) -> dict[tuple[str, str], AliasCandidate]:
    return {(c.alias.ns, c.alias.key): c for c in found}


# --- the miner on the fixture ---------------------------------------------------------------------


def test_miner_contract() -> None:
    assert isinstance(oldlib.MINER, Miner)
    assert oldlib.MINER.name == "oldlib" == oldlib.__name__.rsplit(".", 1)[-1]


def test_mines_the_old_tables(ctx: MineContext) -> None:
    found = list(oldlib.MINER.mine(ctx))
    assert found == sorted(found)
    assert len(found) == len({(c.alias, c.target, c.relation, c.detail) for c in found})
    assert {shape(c) for c in found} == EXPECTED


def test_every_candidate_is_for_review_and_valid(ctx: MineContext) -> None:
    for c in oldlib.MINER.mine(ctx):
        assert c.source == "oldlib"
        assert c.auto is False  # no auto rule covers the old run: gate A asks the owner
        assert {c.alias.ns, c.target.ns} <= NAMESPACES
        if c.relation == "ineligible":
            assert c.detail in INELIGIBLE_REASONS
        elif c.relation == "build":
            assert c.detail in BUILD_DETAILS
        row = AliasRow(
            c.alias.key,
            c.alias.ns,
            "" if c.relation == "ineligible" else "some-family",
            c.relation,
            c.detail,
            c.source,
            DAY,
            "check",
        )
        assert row.problems() == [], c


def test_nothing_is_taken_by_prefix(ctx: MineContext) -> None:
    """``^lexend`` gives font-lexend only; a Nerd or Powerline cask is miner nerd's to map."""
    keys = set(by_alias(oldlib.MINER.mine(ctx)))
    for token in (
        "font-lexend-deca",
        "font-jetbrains-mono-nl",
        "font-roboto-flex",
        "font-jetbrains-mono-nerd-font",
        "font-sauce-code-pro-nerd-font",
        "font-symbols-only-nerd-font",
        "font-dejavu-sans-mono-for-powerline",
        "example/fonts-extra/font-some-tap-font",
        "fontforge-app",
    ):
        assert ("brew-cask", token) not in keys


def test_leaves_out_bundles_descriptions_and_umbrellas(ctx: MineContext) -> None:
    keys = set(by_alias(oldlib.MINER.mine(ctx)))
    assert ("brew-cask", "font-liberation") not in keys  # LEFT_OUT: a bundle
    assert ("arch-pkg", "ttf-croscore") not in keys
    assert ("deb-pkg", "fonts-go") not in keys
    assert ("nerd-folder", "iA-Writer") not in keys  # an umbrella folded into a member
    assert ("deb-pkg", "fonts-droid-fallback") not in keys  # LEFT_OUT: another font
    assert ("arch-pkg", "noto-fonts-extra") not in keys  # the old table had no family
    names = {k for ns, k in keys if ns == "font-name"}
    assert not names & {"IBM Plex", "Ubuntu (Sans/Mono)", "Ubuntu", "Inter", "Mystery Grotesk"}
    assert not any("(" in n for n in names)


def test_evidence_names_the_table(ctx: MineContext) -> None:
    found = by_alias(oldlib.MINER.mine(ctx))
    agg, canon = f"$TFF_STORE/{oldlib.AGG_MONO}", f"$TFF_STORE/{oldlib.CANON}"
    assert found[("arch-pkg", "ttf-hack")].evidence == f"{agg} ARCH_MAP"
    assert found[("nerd-folder", "IosevkaTerm")].evidence == f"{agg} NF"
    assert found[("brew-cask", "font-lexend")].evidence == f"{agg} BREW_MAP ^lexend"
    assert found[("font-name", "Muli")].evidence == f"{canon} Muli -> Mulish (free)"
    # one fact seen twice is one candidate, with both rows as evidence
    assert found[("font-name", "Futura PT")].evidence == (
        f"{canon} Futura PT (proprietary); Futura PT Web -> Futura PT (proprietary)"
    )
    assert found[("font-name", "Satoshi")].evidence.endswith("ITF Free Font License")
    # a target the canon renamed shows both the table row and the canon row
    assert found[("arch-pkg", "gnu-free-fonts")].evidence == (
        f"{agg} ARCH_MAP | {canon} GNU FreeFont -> FreeFont (free)"
    )


def test_never_reads_counts(ctx: MineContext, tmp_path: Path) -> None:
    """Name facts only: no install or star count of the seed reaches the seed file."""
    out = tmp_path / "oldlib.csv"
    aliases.write_seeds(oldlib.MINER.mine(ctx), out)
    text = out.read_text(encoding="utf-8")
    brew = json.loads((FIXTURES / "brew365.json").read_text(encoding="utf-8"))
    counts = {i["count"] for i in brew["items"]} | {
        i["count"].replace(",", "") for i in brew["items"]
    }
    counts |= set(re.findall(r"\d{4,}", (FIXTURES / "agg_mono.py.txt").read_text()))
    assert not [n for n in counts if n in text]


def test_deterministic_and_round_trips(ctx: MineContext, tmp_path: Path) -> None:
    first, second = list(oldlib.MINER.mine(ctx)), list(oldlib.MINER.mine(ctx))
    assert first == second
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    aliases.write_seeds(first, a)
    aliases.write_seeds(reversed(second), b)
    assert a.read_bytes() == b.read_bytes()
    assert aliases.load_seeds(a) == first


def test_input_order_does_not_matter(store: Path, tmp_path: Path) -> None:
    before = list(oldlib.MINER.mine(context(tmp_path, store)))
    for rel in (oldlib.BREW365, oldlib.CANON):
        path = store / rel
        doc = json.loads(path.read_text(encoding="utf-8"))
        items = doc["items"] if isinstance(doc, dict) else doc
        items.reverse()
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert list(oldlib.MINER.mine(context(tmp_path, store))) == before


def test_writes_nothing(ctx: MineContext) -> None:
    """The stage writes the seed file; the miner never writes data/ (least of all aliases.csv)."""
    oldlib.MINER.mine(ctx)
    assert not ctx.paths.data.exists()


def test_logs_what_it_left_out(ctx: MineContext, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    oldlib.MINER.mine(ctx)
    assert f"oldlib: {len(EXPECTED)} candidates" in caplog.text
    assert "BREW_MAP: matched by prefix only 2" in caplog.text
    assert "BREW_MAP: Nerd or Powerline cask (miner nerd) 4" in caplog.text
    assert "package tables: LEFT_OUT 5" in caplog.text


def test_the_stage_would_ask_the_owner(ctx: MineContext) -> None:
    """Merged against a small universe, nothing is accepted: known, or queued for gate A."""
    fam = FamilyRef
    families = [
        fam("jetbrains-mono", "JetBrains Mono", (SourceKey("brew-cask", "font-jetbrains-mono"),)),
        fam("cascadia-mono", "Cascadia Mono", (SourceKey("nerd-folder", "CascadiaMono"),)),
        fam("cascadia-code", "Cascadia Code"),
        fam("mulish", "Mulish", (SourceKey("gf-family", "Mulish"),)),
        fam("hack", "Hack"),
    ]
    result = aliases.merge_detailed(
        AliasTable(()), oldlib.MINER.mine(ctx), DEFAULT_AUTO_RULES, families=families, today=DAY
    )
    status = {(o.candidate.alias.ns, o.candidate.alias.key): o for o in result.outcomes}
    assert not [o for o in result.outcomes if o.status == "accepted"]
    assert status[("brew-cask", "font-jetbrains-mono")].status == "known"
    assert status[("nerd-folder", "CascadiaMono")].reason == "other-family"
    assert status[("font-name", "Muli")].reason == "review"
    assert status[("arch-pkg", "ttf-hack")].family_id == "hack"
    assert status[("font-name", "Satoshi")].family_id == ""  # ineligible
    assert result.rows == ()


# --- inputs that must stop the miner (the stage then keeps the committed seeds) -------------------


def test_needs_the_store(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="TFF_STORE"):
        oldlib.MINER.mine(context(tmp_path, None))


@pytest.mark.parametrize("rel", [oldlib.AGG_MONO, oldlib.BREW365, oldlib.CANON])
def test_missing_seed_file(store: Path, tmp_path: Path, rel: str) -> None:
    (store / rel).unlink()
    with pytest.raises(FileNotFoundError, match=re.escape(rel)):
        oldlib.MINER.mine(context(tmp_path, store))


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("NF = {'A': 'A'}\nBREW_MAP = [('^a', 'A')]\nARCH_MAP = {'a': 'A'}\n", "DEB_MAP"),
        ("NF = {}\nBREW_MAP = [('^a', 'A')]\nARCH_MAP = {'a': 'A'}\nDEB_MAP = {'a': 'A'}", "NF"),
        (
            "NF = {'A': 'A'}\nBREW_MAP = make()\nARCH_MAP = {'a': 'A'}\nDEB_MAP = {'a': 'A'}",
            "not a literal",
        ),
        (
            "NF = {'A': None}\nBREW_MAP = [('^a', 'A')]\nARCH_MAP = {'a': 'A'}\nDEB_MAP = {'a': 1}",
            "not a name",
        ),
        (
            "NF = {'A': 'A'}\nBREW_MAP = [('^(a', 'A')]\nARCH_MAP = {'a': 'A'}\nDEB_MAP = {'a': 'A'}",
            "pattern",
        ),
        ("NF = {'A': 'A'\n", "does not parse"),
    ],
)
def test_malformed_tables(source: str, message: str) -> None:
    with pytest.raises(oldlib.OldlibError, match=message):
        oldlib.read_tables(source)


@pytest.mark.parametrize(
    "doc",
    [
        [],
        [{"raw": "A", "canonical": "A", "status": "maybe", "note": ""}],
        [{"raw": " ", "canonical": "A", "status": "free", "note": ""}],
        {"raw": "A"},
    ],
)
def test_malformed_canon(doc: object) -> None:
    with pytest.raises(oldlib.OldlibError):
        oldlib.read_canon(doc)


@pytest.mark.parametrize(
    "doc",
    [{}, {"items": []}, {"items": [{"count": "1"}]}, [], {"items": [{"cask": "fontforge-app"}]}],
)
def test_malformed_brew365(doc: object) -> None:
    with pytest.raises(oldlib.OldlibError):
        oldlib.read_cask_tokens(doc)


# --- the rules --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "canonical", "expected"),
    [
        ("Muli", "Mulish", ("rename", "")),
        ("Source Sans Pro", "Source Sans 3", ("rename", "")),
        ("Rounded Mplus 1c", "M PLUS Rounded 1c", ("rename", "")),
        ("Geist Sans", "Geist", ("rename", "")),
        ("DejaVu Mono", "DejaVu Sans Mono", ("rename", "")),
        ("Lato Hairline", "Lato", ("build", "")),
        ("Redaction 70", "Redaction", ("build", "")),
        ("Pretendard Variable", "Pretendard", ("build", "variable")),
        ("Noto Sans CJK JP", "Noto Sans JP", ("build", "cjk")),
        ("源ノ角ゴシック JP", "Noto Sans JP", ("build", "cjk")),
        ("IBM Plex", "IBM Plex Sans", None),
        ("Liberation", "Liberation Sans", None),
        ("Saira Stencil", "Saira Stencil One", None),
    ],
)
def test_canon_relation(raw: str, canonical: str, expected: tuple[str, str] | None) -> None:
    assert oldlib.canon_relation(raw, canonical) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Neue Haas Unica W1G", True),
        ("woodmart-icons-style-1", True),
        ("Suisse Int'l", True),
        ("Geist (Sans + Mono)", False),
        ("IPAexFont / IPAFont", False),
        ("Klim Type Foundry (metadata string)", False),
        ("", False),
    ],
)
def test_is_name(value: str, expected: bool) -> None:
    assert oldlib.is_name(value) is expected


@pytest.mark.parametrize(
    ("ns", "key", "expected"),
    [
        ("nerd-folder", "Hack", ("build", "nerd")),
        ("arch-pkg", "ttf-hack-nerd", ("build", "nerd")),
        ("arch-pkg", "ttf-meslo-nerd-font-powerlevel10k", ("build", "nerd")),
        ("arch-pkg", "ttf-nerdish", ("package", "")),
        ("arch-pkg", "ttf-hack", ("package", "")),
        ("deb-pkg", "fonts-hack", ("package", "")),
        ("arch-pkg", "ttf-rubik-vf", ("build", "variable")),
        ("deb-pkg", "fonts-example-variable", ("build", "variable")),
        ("arch-pkg", "ttf-vf-sans", ("package", "")),
    ],
)
def test_package_relation(ns: str, key: str, expected: tuple[str, str]) -> None:
    assert oldlib.package_relation(ns, key) == expected


def _tables(arch: dict[str, str | None]) -> dict[str, object]:
    return {"NF": {"A": "A"}, "BREW_MAP": [], "ARCH_MAP": arch, "DEB_MAP": {}}


def _canon(*rows: tuple[str, str]) -> list[oldlib.CanonItem]:
    return [oldlib.CanonItem(raw, new, "free", "") for raw, new in rows]


def test_package_targets_follow_canon_renames() -> None:
    """The old tables wrote some families by an old name; the canon's rename is the target."""
    found, _ = oldlib.candidates(
        _tables({"ttf-old": "Old Sans", "ttf-kept": "Kept Sans Hairline"}),
        [],
        _canon(("Old Sans", "New Sans"), ("Kept Sans Hairline", "Kept Sans")),
    )
    targets = {c.alias.key: c.target.key for c in found if c.alias.ns == "arch-pkg"}
    # only a rename moves a target; a build's name stays its own (the merge resolves it)
    assert targets == {"ttf-old": "New Sans", "ttf-kept": "Kept Sans Hairline"}


def test_an_ambiguous_rename_changes_no_target() -> None:
    """An old name the canon renames two ways keeps its name as target (never guessed)."""
    rows = (("Old Sans", "New Sans"), ("Old Sans", "Other Sans"))
    for order in (rows, rows[::-1]):
        found, _ = oldlib.candidates(_tables({"ttf-old": "Old Sans"}), [], _canon(*order))
        [pkg] = [c for c in found if c.alias.ns == "arch-pkg"]
        assert pkg.target.key == "Old Sans"
        assert pkg.evidence == f"$TFF_STORE/{oldlib.AGG_MONO} ARCH_MAP"
    # two spellings of one name are one rename, not two
    found, _ = oldlib.candidates(
        _tables({"ttf-old": "Old Sans"}),
        [],
        _canon(("Old Sans", "New Sans"), ("old sans", "NEW SANS")),
    )
    [pkg] = [c for c in found if c.alias.ns == "arch-pkg"]
    assert match_key(pkg.target.key) == match_key("New Sans")


# --- the committed seed file ------------------------------------------------------------------------


def test_committed_seed_file(tmp_path: Path) -> None:
    """``data/alias-seeds/oldlib.csv``: canonical, reviewed-only rows of this miner."""
    rows = aliases.load_seeds(SEED_CSV)
    assert rows
    assert {c.source for c in rows} == {"oldlib"}
    assert not any(c.auto for c in rows)
    again = tmp_path / "oldlib.csv"
    aliases.write_seeds(rows, again)
    assert again.read_bytes() == SEED_CSV.read_bytes()
    keys = {(c.alias.ns, c.alias.key) for c in rows}
    assert not keys & set(oldlib.LEFT_OUT)
    assert not [
        k
        for ns, k in keys
        if ns == "brew-cask" and oldlib.BUILD_SUFFIX.search(k.removeprefix(oldlib.CASK_PREFIX))
    ]


@pytest.mark.store
def test_real_seed_gives_the_committed_file(tmp_path: Path) -> None:
    """Mining the real seed in $TFF_STORE reproduces the committed seed file byte for byte."""
    real = Path(os.environ["TFF_STORE"]).expanduser()
    if not all((real / rel).is_file() for rel in (oldlib.AGG_MONO, oldlib.BREW365, oldlib.CANON)):
        pytest.skip(f"$TFF_STORE has no copy of {oldlib.SEED_DIR}")
    found = list(oldlib.MINER.mine(context(tmp_path, real)))
    out = tmp_path / "oldlib.csv"
    aliases.write_seeds(found, out)
    assert out.read_bytes() == SEED_CSV.read_bytes()
    # every LEFT_OUT row is a row of the real tables (no typo leaves a bundle in)
    tables = oldlib.read_tables((real / oldlib.AGG_MONO).read_text(encoding="utf-8"))
    tokens = set(oldlib.read_cask_tokens(json.loads((real / oldlib.BREW365).read_text())))
    present = {("arch-pkg", k) for k in tables["ARCH_MAP"]} | {
        ("deb-pkg", k) for k in tables["DEB_MAP"]
    }
    present |= {("brew-cask", t) for t in tokens} | {("nerd-folder", k) for k in tables["NF"]}
    assert set(oldlib.LEFT_OUT) <= present
