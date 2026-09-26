"""Alias miner "distro" (milestone-1 step 7): Arch and Debian package names to families.

The fixture snapshots (``fixtures/``, see its NOTICE) are synthetic rows in
the extract formats of arch_repos, pkgstats, debian and debian_copyright; the
universe below is made up for them. Each family is there to exercise one rule.
"""

import json
import logging
import os
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tff_catalog import aliases, stageio
from tff_catalog.aliases import BUILD_DETAILS, AliasCandidate
from tff_catalog.aliases.miners import MineContext, Miner
from tff_catalog.aliases.miners import distro as d
from tff_catalog.paths import Paths
from tff_catalog.records import SourceKey
from tff_catalog.store import RawDir, Store
from tff_catalog.universe import Family, Universe

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 9, 26)


def fam(
    fid: str,
    name: str,
    *keys: tuple[str, str],
    urls: tuple[tuple[str, str], ...] = (),
    drop: str | None = None,
) -> Family:
    return Family(
        id=fid,
        family=name,
        keys=tuple(sorted(SourceKey(ns, k) for ns, k in keys)),
        sources=("test",),
        first_seen=DAY,
        minted_from=name,
        drop=drop,
        urls=tuple(sorted(urls)),
    )


GF, FS, CASK, NERD = "gf-family", "fs-id", "brew-cask", "nerd-folder"
FAMILIES = (
    # Name and URL agree; the variable package is a build; a 404 copyright row is ignored.
    fam(
        "inter",
        "Inter",
        (GF, "Inter"),
        (FS, "inter"),
        urls=(("repository", "https://github.com/inter-type/inter"),),
    ),
    # Siblings: a package is never matched to a family by prefix.
    fam("roboto", "Roboto", (GF, "Roboto")),
    fam("roboto-slab", "Roboto Slab", (GF, "Roboto Slab")),
    # Nerd builds follow the nerd-folder key, and their old AUR names follow them.
    fam("jetbrains-mono", "JetBrains Mono", (GF, "JetBrains Mono")),
    fam("jetbrainsmono-nerd-font", "JetBrainsMono Nerd Font", (NERD, "JetBrainsMono")),
    fam("fantasquesansm-nerd-font", "FantasqueSansM Nerd Font", (NERD, "FantasqueSansMono")),
    fam("montserrat", "Montserrat", (GF, "Montserrat")),
    # A repository of several products (mozilla/Fira) pairs no package.
    fam(
        "fira-sans",
        "Fira Sans",
        (GF, "Fira Sans"),
        urls=(("repository", "https://github.com/mozilla/Fira"),),
    ),
    fam(
        "dejavu",
        "DejaVu",
        (CASK, "font-dejavu"),
        urls=(("homepage", "https://dejavu-fonts.github.io/"),),
    ),
    fam("culmus", "Culmus", ("fontist-formula", "culmus")),
    fam("engadget", "Engadget", ("foundry-family", "Engadget")),
    # A DEP-5 header copied from another package (fonts-gfs-solomos) pairs nothing.
    fam(
        "abyssinica-sil",
        "Abyssinica SIL",
        (GF, "Abyssinica SIL"),
        urls=(("homepage", "https://software.sil.org/abyssinica/"),),
    ),
    fam("gfs-solomos", "GFS Solomos", (GF, "GFS Solomos")),
    # A URL of one family, but the package's stem starts several: no candidate.
    fam(
        "jsmath-cmex10",
        "jsMath cmex10",
        (FS, "jsmath-cmex10"),
        urls=(
            ("homepage", "https://www.university.example/~tex/jsMath/download/jsMath-fonts.html"),
        ),
    ),
    fam("jsmath-cmr10", "jsMath cmr10", (FS, "jsmath-cmr10")),
    # URL only: the package names say nothing.
    fam(
        "carlito",
        "Carlito",
        (GF, "Carlito"),
        urls=(("repository", "https://github.com/googlefonts/carlito"),),
    ),
    fam(
        "source-sans-3",
        "Source Sans 3",
        (GF, "Source Sans 3"),
        urls=(("repository", "https://github.com/adobe-fonts/source-sans"),),
    ),
    fam(
        "hack", "Hack", (CASK, "font-hack"), urls=(("homepage", "https://sourcefoundry.org/hack/"),)
    ),
    fam("unifont", "Unifont", (FS, "unifont")),
    # Only an aggregator URL: never a pairing, and the name differs.
    fam(
        "cantarell",
        "Cantarell",
        (GF, "Cantarell"),
        urls=(("repository", "https://github.com/googlefonts/googlefontdirectory-hg"),),
    ),
    fam("rubik", "Rubik", (GF, "Rubik")),
    # A dropped (icon) family still takes its packages, so they never reach unmatched.md.
    fam("font-awesome", "Font Awesome", (CASK, "font-fontawesome"), drop="icon"),
    fam("intone-mono", "Intone Mono", (FS, "intone-mono")),
    fam("lexend", "Lexend", (GF, "Lexend")),
)

# (alias ns, alias, target ns, target, relation, detail) for the fixture.
EXPECTED = [
    ("arch-pkg", "inter-font", GF, "Inter", "package", ""),
    ("arch-pkg", "lexend-fonts-git", GF, "Lexend", "package", ""),
    ("arch-pkg", "montserrat-ttf", GF, "Montserrat", "package", ""),
    ("arch-pkg", "nerd-fonts-fantasque-sans-mono", NERD, "FantasqueSansMono", "build", "nerd"),
    ("arch-pkg", "nerd-fonts-jetbrains-mono", NERD, "JetBrainsMono", "build", "nerd"),
    ("arch-pkg", "otf-intone-mono", FS, "intone-mono", "package", ""),
    ("arch-pkg", "ttf-dejavu", CASK, "font-dejavu", "package", ""),
    ("arch-pkg", "ttf-fantasque-nerd", NERD, "FantasqueSansMono", "build", "nerd"),
    ("arch-pkg", "ttf-fira-sans", GF, "Fira Sans", "package", ""),
    ("arch-pkg", "ttf-font-awesome", CASK, "font-fontawesome", "package", ""),
    ("arch-pkg", "ttf-hack", CASK, "font-hack", "package", ""),
    ("arch-pkg", "ttf-jetbrains-mono", GF, "JetBrains Mono", "package", ""),
    ("arch-pkg", "ttf-jetbrains-mono-nerd", NERD, "JetBrainsMono", "build", "nerd"),
    ("arch-pkg", "ttf-montserrat", GF, "Montserrat", "package", ""),
    ("arch-pkg", "ttf-roboto", GF, "Roboto", "package", ""),
    ("arch-pkg", "ttf-roboto-slab", GF, "Roboto Slab", "package", ""),
    ("arch-pkg", "ttf-rubik-vf", GF, "Rubik", "build", "variable"),
    ("arch-pkg", "woff2-font-awesome", CASK, "font-fontawesome", "package", ""),
    ("deb-pkg", "culmus", "fontist-formula", "culmus", "package", ""),
    ("deb-pkg", "fonts-adobe-sourcesans3", GF, "Source Sans 3", "package", ""),
    ("deb-pkg", "fonts-crosextra-carlito", GF, "Carlito", "package", ""),
    ("deb-pkg", "fonts-culmus", "fontist-formula", "culmus", "package", ""),
    ("deb-pkg", "fonts-dejavu", CASK, "font-dejavu", "package", ""),
    ("deb-pkg", "fonts-dejavu-core", CASK, "font-dejavu", "package", ""),
    ("deb-pkg", "fonts-engadget", "foundry-family", "Engadget", "package", ""),
    ("deb-pkg", "fonts-gfs-solomos", GF, "GFS Solomos", "package", ""),
    ("deb-pkg", "fonts-hack-ttf", CASK, "font-hack", "package", ""),
    ("deb-pkg", "fonts-inter", GF, "Inter", "package", ""),
    ("deb-pkg", "fonts-inter-variable", GF, "Inter", "build", "variable"),
    ("deb-pkg", "fonts-roboto-slab", GF, "Roboto Slab", "package", ""),
    ("deb-pkg", "fonts-sil-abyssinica", GF, "Abyssinica SIL", "package", ""),
    ("deb-pkg", "ttf-engadget", "foundry-family", "Engadget", "package", ""),
    ("deb-pkg", "unifont", FS, "unifont", "package", ""),
    ("deb-src", "culmus", "fontist-formula", "culmus", "package", ""),
    ("deb-src", "fonts-adobe-sourcesans3", GF, "Source Sans 3", "package", ""),
    ("deb-src", "fonts-crosextra-carlito", GF, "Carlito", "package", ""),
    ("deb-src", "fonts-dejavu", CASK, "font-dejavu", "package", ""),
    ("deb-src", "fonts-engadget", "foundry-family", "Engadget", "package", ""),
    ("deb-src", "fonts-gfs-solomos", GF, "GFS Solomos", "package", ""),
    ("deb-src", "fonts-inter", GF, "Inter", "package", ""),
    ("deb-src", "fonts-roboto-slab", GF, "Roboto Slab", "package", ""),
    ("deb-src", "fonts-sil-abyssinica", GF, "Abyssinica SIL", "package", ""),
    ("deb-src", "unifont", FS, "unifont", "package", ""),
]


def shape(c: AliasCandidate) -> tuple[str, ...]:
    return (c.alias.ns, c.alias.key, c.target.ns, c.target.key, c.relation, c.detail)


def by_alias(found: list[AliasCandidate]) -> dict[tuple[str, str], AliasCandidate]:
    return {(c.alias.ns, c.alias.key): c for c in found}


def parts(c: AliasCandidate) -> list[str]:
    return c.evidence.split("; ")


# --- the fixture store and universe --------------------------------------------------------------


def _jsonl(path: Path) -> list[object]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def write_store(root: Path, day: date = DAY, *, version: int = 1) -> Store:
    """The four snapshots, as the collectors' fetch writes them."""
    store = Store(root)
    arch = store.writer(d.ARCH, day, version)
    repos = json.loads((FIXTURES / d.ARCH / d.ARCH_REPOS).read_text("utf-8"))
    for repo in repos:
        arch.write_jsonl(
            repo["extract"], _jsonl(FIXTURES / d.ARCH / repo["extract"].removesuffix(".gz"))
        )
    arch.write_json(d.ARCH_REPOS, repos)
    arch.close()
    pkgstats = store.writer(d.PKGSTATS, day, version)
    for month in sorted((FIXTURES / d.PKGSTATS).glob("month-*.csv")):
        pkgstats.write_bytes(month.name + ".gz", month.read_bytes())
    pkgstats.close()
    debian = store.writer(d.DEBIAN, day, version)
    debian.write_jsonl(d.DEB_PACKAGES, _jsonl(FIXTURES / d.DEBIAN / "packages.jsonl"))
    debian.write_jsonl(d.DEB_POPCON, _jsonl(FIXTURES / d.DEBIAN / "popcon.jsonl"))
    debian.write_json(
        "meta.json", json.loads((FIXTURES / d.DEBIAN / "meta.json").read_text("utf-8"))
    )
    debian.close()
    copyright_ = store.writer(d.COPYRIGHT, day, version)
    copyright_.write_jsonl(d.DEB_COPYRIGHT, _jsonl(FIXTURES / d.COPYRIGHT / "copyright.jsonl"))
    copyright_.close()
    return store


def context(tmp_path: Path, store: Store | None, *, universe: bool = True) -> MineContext:
    paths = Paths.for_root(
        tmp_path / "repo",
        store=store.root if store else None,
        raw_root=tmp_path / "raw",
    )
    if universe:
        paths.stage.mkdir(parents=True)
        stageio.dump_stage(paths, "universe", Universe({f.id: f for f in FAMILIES}, ()))
    raw = RawDir(tmp_path / "raw" / "aliases")
    return MineContext(paths, store, raw, DAY, logging.getLogger("t"))


@pytest.fixture
def ctx(tmp_path: Path) -> MineContext:
    return context(tmp_path, write_store(tmp_path / "store"))


@pytest.fixture
def found(ctx: MineContext) -> list[AliasCandidate]:
    return list(d.MINER.mine(ctx))


# --- the miner on the fixture --------------------------------------------------------------------


def test_miner_contract() -> None:
    assert isinstance(d.MINER, Miner)
    assert d.MINER.name == "distro" == d.__name__.rsplit(".", 1)[-1]


def test_mines_the_fixture(found: list[AliasCandidate]) -> None:
    assert [shape(c) for c in found] == EXPECTED


def test_every_candidate_waits_for_the_owner(found: list[AliasCandidate]) -> None:
    """Gate A: no distro row is auto-accepted, and only package and build rows are made."""
    assert all(c.source == "distro" and not c.auto for c in found)
    assert {c.relation for c in found} == {"package", "build"}
    assert all(c.detail in BUILD_DETAILS for c in found)
    assert all(c.detail == "" for c in found if c.relation == "package")
    assert all(c.evidence for c in found)


def test_siblings_never_match_by_prefix(found: list[AliasCandidate]) -> None:
    """Roboto Slab's packages go to Roboto Slab; Roboto Condensed (not in the universe) nowhere."""
    rows = by_alias(found)
    assert rows["arch-pkg", "ttf-roboto-slab"].target == SourceKey(GF, "Roboto Slab")
    assert rows["deb-pkg", "fonts-roboto-slab"].target == SourceKey(GF, "Roboto Slab")
    assert ("arch-pkg", "ttf-roboto-condensed") not in rows
    assert ("arch-pkg", "ttf-twin") not in rows


def test_nerd_builds_follow_the_nerd_folder(found: list[AliasCandidate]) -> None:
    rows = by_alias(found)
    nerd = rows["arch-pkg", "ttf-jetbrains-mono-nerd"]
    assert (nerd.target, nerd.relation, nerd.detail) == (
        SourceKey(NERD, "JetBrainsMono"),
        "build",
        "nerd",
    )
    # The old AUR name follows its replacement; ``fantasque`` alone names nothing,
    # so ttf-fantasque-nerd is placed by the name it replaces.
    assert rows["arch-pkg", "nerd-fonts-jetbrains-mono"].target == nerd.target
    fantasque = rows["arch-pkg", "ttf-fantasque-nerd"]
    assert fantasque.evidence == "replaces nerd-fonts-fantasque-sans-mono"
    assert rows["arch-pkg", "nerd-fonts-fantasque-sans-mono"].evidence == (
        "replaced by ttf-fantasque-nerd"
    )


def test_builds_and_counted_names(found: list[AliasCandidate]) -> None:
    rows = by_alias(found)
    assert shape(rows["deb-pkg", "fonts-inter-variable"])[4:] == ("build", "variable")
    assert shape(rows["arch-pkg", "ttf-rubik-vf"])[4:] == ("build", "variable")
    # pkgstats names: a -git build is a font package; a -git program is not a key at all.
    assert rows["arch-pkg", "lexend-fonts-git"].target == SourceKey(GF, "Lexend")
    assert ("arch-pkg", "whisper-git") not in rows
    # A popcon name gets a row by its own name, and a provided name by its provider.
    assert rows["deb-pkg", "fonts-hack-ttf"].target == SourceKey(CASK, "font-hack")
    assert rows["deb-pkg", "ttf-engadget"].evidence == "name engadget; provided by fonts-engadget"


def test_a_shared_repository_pairs_nothing(found: list[AliasCandidate]) -> None:
    """github.com/mozilla/Fira holds FiraGO, Fira Mono and Fira Sans: it pairs none of them."""
    rows = by_alias(found)
    assert ("arch-pkg", "ttf-fira-go") not in rows
    assert ("arch-pkg", "ttf-fira-mono") not in rows
    sans = rows["arch-pkg", "ttf-fira-sans"]
    assert sans.evidence == "name fira-sans; upstream github.com/mozilla/fira"


def test_url_pairing(found: list[AliasCandidate]) -> None:
    rows = by_alias(found)
    assert (
        rows["deb-pkg", "fonts-crosextra-carlito"].evidence == "url github.com/googlefonts/carlito"
    )
    assert rows["deb-pkg", "fonts-dejavu-core"].evidence == "url dejavu-fonts.github.io"
    # Documentation never takes a family from a URL.
    assert ("deb-pkg", "fonts-crosextra-carlito-doc") not in rows
    # One family's URL, but two families start with "jsmath": the package may hold both.
    assert ("deb-pkg", "fonts-jsmath") not in rows
    assert ("deb-src", "fonts-jsmath") not in rows


def test_never_pairs_through_googlefontdirectory_hg(found: list[AliasCandidate]) -> None:
    assert d.url_key("https://github.com/googlefonts/googlefontdirectory-hg") is None
    assert ("arch-pkg", "cantarell-classic-fonts") not in by_alias(found)


def test_a_copied_upstream_name_pairs_nothing(found: list[AliasCandidate]) -> None:
    """fonts-gfs-solomos's DEP-5 header names Abyssinica SIL; its own name wins, and
    the Upstream-Name, given by a package of another family, pairs neither package."""
    rows = by_alias(found)
    for ns in ("deb-pkg", "deb-src"):
        assert rows[ns, "fonts-gfs-solomos"].target == SourceKey(GF, "GFS Solomos")
        assert "Abyssinica" not in rows[ns, "fonts-gfs-solomos"].evidence
        assert rows[ns, "fonts-sil-abyssinica"].target == SourceKey(GF, "Abyssinica SIL")
        assert "upstream-name" not in rows[ns, "fonts-sil-abyssinica"].evidence
    assert "upstream-name Inter" in rows["deb-src", "fonts-inter"].evidence


def test_transitional_and_source_packages(found: list[AliasCandidate]) -> None:
    rows = by_alias(found)
    assert rows["deb-pkg", "culmus"].evidence == (
        "name culmus; transitional to fonts-culmus; upstream culmus.sourceforge.net"
    )
    # A source gets the family of its font binaries; -doc and -bin binaries are left out.
    assert rows["deb-src", "fonts-crosextra-carlito"].evidence == (
        "binary fonts-crosextra-carlito; url github.com/googlefonts/carlito"
    )
    assert rows["deb-src", "unifont"].evidence.startswith("binary unifont; name unifont")
    # A variable binary and a static one: the source is the family's own package.
    assert shape(rows["deb-src", "fonts-inter"])[4:] == ("package", "")
    # A copyright file that was not found (404) makes no key.
    assert ("deb-src", "fonts-missing") not in rows


def test_dep5_source_urls_pair_but_are_never_listed(found: list[AliasCandidate]) -> None:
    """A DEP-5 Source URL may pair a source package, but only package homepages are
    listed as ``upstream``: the seed file is public (ruling T1 opens package data)."""
    rows = by_alias(found)
    # fonts-inter's homepage is inter.example; its DEP-5 Source is the family's repository.
    assert "url github.com/inter-type/inter" in parts(rows["deb-src", "fonts-inter"])
    assert parts(rows["deb-pkg", "fonts-inter"]) == ["name inter", "upstream inter.example"]
    # culmus: the Sources homepage is listed, the DEP-5 Source URL is not.
    assert "upstream culmus.sourceforge.net" in rows["deb-src", "culmus"].evidence
    assert all("sourceforge.net/culmus" not in c.evidence for c in found)


def test_homepages_can_be_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIST_HOMEPAGES holds the owner's open choice on homepages in the public seed file."""
    families = [info("a", "Alpha", (GF, "Alpha"))]
    arch = [arch_row("ttf-alpha", url="https://alpha.example/")]
    listed = propose(families, arch=arch)["arch-pkg", "ttf-alpha"]
    assert listed.evidence == "name alpha; upstream alpha.example"
    monkeypatch.setattr(d, "LIST_HOMEPAGES", False)
    assert propose(families, arch=arch)["arch-pkg", "ttf-alpha"].evidence == "name alpha"


def test_deterministic_and_round_trips(ctx: MineContext, tmp_path: Path) -> None:
    first, second = list(d.MINER.mine(ctx)), list(d.MINER.mine(ctx))
    assert first == second == sorted(first)
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    aliases.write_seeds(first, a)
    aliases.write_seeds(reversed(second), b)
    assert a.read_bytes() == b.read_bytes()
    assert aliases.load_seeds(a) == first


def test_writes_nothing(ctx: MineContext) -> None:
    """The stage writes the seed file; the miner never writes data/ (least of all aliases.csv)."""
    list(d.MINER.mine(ctx))
    assert not ctx.paths.data.exists()


# --- names and URLs --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "vcs", "text", "detail", "affixed"),
    [
        ("ttf-jetbrains-mono", True, "jetbrains-mono", "", True),
        ("ttf-jetbrains-mono-nerd", True, "jetbrains-mono", "nerd", True),
        ("nerd-fonts-jetbrains-mono", True, "jetbrains-mono", "nerd", True),
        ("otf-monaspace-nerdfonts", True, "monaspace", "nerd", True),
        ("ttf-maplemono-nf-cn", True, "maplemono-nf", "cjk", True),
        ("fonts-cascadia-mono-nf", False, "cascadia-mono", "nf", True),
        ("fonts-inter-variable", False, "inter", "variable", True),
        ("ttf-rubik-vf", True, "rubik", "variable", True),
        ("terminus-font-otb", True, "terminus", "", True),
        ("fonts-hack-web", False, "hack", "", True),
        ("lexend-fonts-git", True, "lexend", "", True),
        ("ttf-smiley-sans-bin", True, "smiley-sans", "", True),
        # Not font affixes: a -git program, a -web program, a bare name.
        ("whisper-git", True, "whisper", "", False),
        ("foo-web", False, "foo-web", "", False),
        ("unifont", False, "unifont", "", False),
        # An affix is never the whole name.
        ("ttf-", False, "ttf-", "", False),
        ("fonts-nerd", False, "nerd", "", True),
    ],
)
def test_stem(name: str, vcs: bool, text: str, detail: str, affixed: bool) -> None:
    assert d.stem(name, vcs=vcs) == d.Stem(text, detail, affixed)


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("https://github.com/Inter-Type/inter/", "github.com/inter-type/inter"),
        ("https://github.com/rsms/inter.git", "github.com/rsms/inter"),
        ("https://github.com/rsms/inter/releases/tag/v4.1", "github.com/rsms/inter"),
        ("http://www.Example.org/fonts/Foo/?x=1#top", "example.org/fonts/foo"),
        ("https://fonts.google.com/specimen/Roboto+Slab", "fonts.google.com/specimen/roboto slab"),
        ("https://gitlab.com/velvetyne/murmure", "gitlab.com/velvetyne/murmure"),
        # Aggregators, bare forges and non-web URLs never pair.
        ("https://code.google.com/p/googlefontdirectory/", None),
        ("https://github.com/google/fonts/tree/main/ofl/inter", None),
        ("https://github.com/ryanoasis/nerd-fonts", None),
        ("https://github.com/notofonts/latin-greek-cyrillic", None),
        ("https://github.com/", None),
        ("ftp://ftp.example.org/fonts", None),
        ("", None),
        (None, None),
    ],
)
def test_url_key(url: str | None, key: str | None) -> None:
    assert d.url_key(url) == key


def test_non_font_packages() -> None:
    assert d.is_non_font(SourceKey("deb-pkg", "unifont-bin"))
    assert d.is_non_font(SourceKey("deb-pkg", "fonts-crosextra-carlito-doc"))
    # An Arch -bin package is a release build of the fonts.
    assert not d.is_non_font(SourceKey("arch-pkg", "ttf-smiley-sans-bin"))
    assert not d.is_non_font(SourceKey("arch-pkg", "ttf-inter"))


# --- the rules on hand-made packages ---------------------------------------------------------------


def info(
    fid: str, name: str, *keys: tuple[str, str], urls: tuple[tuple[str, str], ...] = ()
) -> d.FamilyInfo:
    return d.FamilyInfo(fid, name, tuple(SourceKey(ns, k) for ns, k in keys), urls)


def arch_row(name: str, **fields: object) -> dict[str, object]:
    return {"name": name, "font": True, "url": None, "replaces": [], "provides": []} | fields


def deb_row(package: str, **fields: object) -> dict[str, object]:
    row = {"package": package, "role": "font", "source": package, "description": "a font"}
    return row | {"homepage": None, "provides": [], "depends": [], "pre_depends": []} | fields


def propose(
    families: list[d.FamilyInfo],
    arch: Iterable[Mapping[str, Any]] = (),
    deb: Iterable[Mapping[str, Any]] = (),
    dep5: Iterable[Mapping[str, Any]] = (),
    counted: Iterable[str] = (),
) -> dict[tuple[str, str], AliasCandidate]:
    """The candidates for hand-made extract rows, by alias."""
    pkgs = d.Packages()
    d.add_arch(pkgs, arch)
    d.add_names(pkgs, "arch-pkg", counted)
    d.add_debian(pkgs, deb)
    d.add_copyright(pkgs, dep5)
    return {(c.alias.ns, c.alias.key): c for c in d.propose(families, pkgs)}


def test_evidence_naming_two_families_gives_nothing() -> None:
    families = [info("a", "Alpha", (GF, "Alpha")), info("b", "Beta", (GF, "Beta"))]
    rows = propose(families, arch=[arch_row("ttf-alpha", replaces=["ttf-beta"])])
    assert ("arch-pkg", "ttf-alpha") not in rows
    # The replaced name agrees with no single row either.
    assert ("arch-pkg", "ttf-beta") not in rows


def test_a_name_shared_by_two_families_gives_nothing() -> None:
    families = [info("a", "Alpha", (GF, "Alpha")), info("a2", "Alpha", (CASK, "font-alpha"))]
    assert propose(families, arch=[arch_row("ttf-alpha")]) == {}


def test_renamed_into_two_families_gives_nothing() -> None:
    families = [info("a", "Alpha", (GF, "Alpha")), info("b", "Beta", (GF, "Beta"))]
    arch = [arch_row("ttf-alpha", replaces=["ttf-old"]), arch_row("ttf-beta", replaces=["ttf-old"])]
    rows = propose(families, arch=arch)
    assert set(rows) == {("arch-pkg", "ttf-alpha"), ("arch-pkg", "ttf-beta")}


def test_a_replaced_name_keeps_its_own_evidence() -> None:
    """ttf-beta names Beta itself; being replaced by an Alpha package does not move it."""
    families = [info("a", "Alpha", (GF, "Alpha")), info("b", "Beta", (GF, "Beta"))]
    arch = [arch_row("ttf-alpha", replaces=["ttf-beta"])]
    rows = propose(families, arch=arch, counted=["ttf-beta"])
    assert ("arch-pkg", "ttf-alpha") not in rows  # its own evidence names both
    assert rows["arch-pkg", "ttf-beta"].target == SourceKey(GF, "Beta")


def test_a_bare_name_needs_a_font_flag() -> None:
    families = [info("w", "Whisper", (GF, "Whisper"))]
    assert propose(families, counted=["whisper", "whisper-git"]) == {}
    rows = propose(families, deb=[deb_row("whisper")])
    assert rows["deb-pkg", "whisper"].target == SourceKey(GF, "Whisper")


def test_sources_whose_binaries_disagree_get_nothing() -> None:
    families = [info("a", "Alpha", (GF, "Alpha")), info("b", "Beta", (GF, "Beta"))]
    deb = [deb_row("fonts-alpha", source="twofonts"), deb_row("fonts-beta", source="twofonts")]
    rows = propose(families, deb=deb)
    assert set(rows) == {("deb-pkg", "fonts-alpha"), ("deb-pkg", "fonts-beta")}


def test_a_transitional_package_needs_one_successor() -> None:
    families = [info("a", "Alpha", (GF, "Alpha"))]
    deb = [
        deb_row("fonts-alpha"),
        deb_row("oldname", description="transitional package", depends=[["fonts-alpha"]]),
        deb_row("oldpair", description="transitional", depends=[["fonts-alpha", "fonts-x"]]),
    ]
    rows = propose(families, deb=deb)
    assert rows["deb-pkg", "oldname"].evidence == "transitional to fonts-alpha"
    assert ("deb-pkg", "oldpair") not in rows


def test_upstream_name_pairs_a_single_binary() -> None:
    families = [info("a", "Alpha Sans", (GF, "Alpha Sans"))]
    deb = [deb_row("fonts-alphas", source="alpha-src")]
    dep5 = [{"package": "alpha-src", "status": 200, "upstream_name": "Alpha Sans"}]
    rows = propose(families, deb=deb, dep5=dep5)
    assert rows["deb-pkg", "fonts-alphas"].evidence == "upstream-name Alpha Sans"
    assert rows["deb-src", "alpha-src"].evidence == (
        "binary fonts-alphas; upstream-name Alpha Sans"
    )


def test_a_replaced_name_of_another_build_is_not_an_own_name() -> None:
    """ttf-alpha-nerd replaces ttf-beta: ttf-beta is no Nerd name, so it neither splits
    ttf-alpha-nerd's evidence nor takes its row (its own name, Beta, disagrees), even
    though no snapshot counts ttf-beta."""
    families = [
        info("a", "Alpha", (GF, "Alpha")),
        info("a-nf", "Alpha Nerd Font", (NERD, "Alpha")),
        info("b", "Beta", (GF, "Beta")),
    ]
    rows = propose(families, arch=[arch_row("ttf-alpha-nerd", replaces=["ttf-beta"])])
    assert shape(rows["arch-pkg", "ttf-alpha-nerd"]) == (
        "arch-pkg",
        "ttf-alpha-nerd",
        NERD,
        "Alpha",
        "build",
        "nerd",
    )
    assert ("arch-pkg", "ttf-beta") not in rows


def test_a_successor_never_overrides_the_own_name() -> None:
    """A transitional package, or a source package, whose own name is another family's."""
    families = [info("a", "Alpha", (GF, "Alpha")), info("b", "Beta", (GF, "Beta"))]
    deb = [
        deb_row("fonts-alpha", source="fonts-beta"),
        deb_row("fonts-beta-old", description="transitional", depends=[["fonts-alpha"]]),
        deb_row("fonts-gamma", description="transitional", depends=[["fonts-alpha"]]),
    ]
    rows = propose(families, deb=deb)
    assert rows["deb-pkg", "fonts-alpha"].target == SourceKey(GF, "Alpha")
    assert ("deb-src", "fonts-beta") not in rows  # its binary is Alpha, its name Beta
    assert ("deb-pkg", "fonts-beta-old") in rows  # "beta-old" names nothing
    assert rows["deb-pkg", "fonts-gamma"].evidence == "transitional to fonts-alpha"
    families.append(info("bo", "Beta Old", (GF, "Beta Old")))
    assert ("deb-pkg", "fonts-beta-old") not in propose(families, deb=deb)


def test_a_source_whose_upstream_name_disagrees_with_its_binaries_gets_nothing() -> None:
    families = [info("a", "Alpha", (GF, "Alpha")), info("b", "Beta Sans", (GF, "Beta Sans"))]
    deb = [deb_row("fonts-alpha", source="betasrc"), deb_row("fonts-alpha-otf", source="betasrc")]
    dep5 = [{"package": "betasrc", "status": 200, "upstream_name": "Beta Sans"}]
    rows = propose(families, deb=deb, dep5=dep5)
    assert {("deb-pkg", "fonts-alpha"), ("deb-pkg", "fonts-alpha-otf")} <= set(rows)
    assert ("deb-src", "betasrc") not in rows


def test_a_provided_name_needs_the_providers_build() -> None:
    """The plain alpha-font is provided by the plain and the variable package; only
    the plain one is that name, so the row is a package row, not a conflict."""
    families = [info("a", "Alpha", (GF, "Alpha"))]
    arch = [
        arch_row("ttf-alpha", provides=["alpha-font"]),
        arch_row("ttf-alpha-variable", provides=["alpha-font", "alpha-font-variable"]),
    ]
    rows = propose(families, arch=arch)
    assert shape(rows["arch-pkg", "alpha-font"])[4:] == ("package", "")
    assert rows["arch-pkg", "alpha-font"].evidence == "provided by ttf-alpha"
    variable = rows["arch-pkg", "alpha-font-variable"]
    assert (shape(variable)[4:], variable.evidence) == (
        ("build", "variable"),
        "provided by ttf-alpha-variable",
    )


def test_name_evidence_sets_the_target() -> None:
    """A URL that agrees adds evidence; the target stays the key the name matched."""
    families = [
        info(
            "a",
            "Alpha Sans",
            (GF, "Alpha Sans"),
            (CASK, "font-alpha"),
            urls=(("homepage", "https://alpha.example/"),),
        )
    ]
    rows = propose(families, arch=[arch_row("ttf-alpha", url="https://alpha.example")])
    row = rows["arch-pkg", "ttf-alpha"]
    assert (row.target, row.evidence) == (
        SourceKey(CASK, "font-alpha"),
        "name alpha; url alpha.example",
    )


def test_target_prefers_the_ranked_key() -> None:
    """A family found by its cask token targets its Google key (TARGET_NS_ORDER)."""
    families = [info("a", "Alpha One", (CASK, "font-alpha-one"), (GF, "Alpha One"))]
    rows = propose(families, arch=[arch_row("ttf-alpha-one")])
    assert rows["arch-pkg", "ttf-alpha-one"].target == SourceKey(GF, "Alpha One")


# --- inputs ------------------------------------------------------------------------------------------


def test_needs_a_store(tmp_path: Path) -> None:
    with pytest.raises(d.DistroInputError, match="TFF_STORE"):
        d.MINER.mine(context(tmp_path, None))


def test_needs_the_universe(tmp_path: Path) -> None:
    store = write_store(tmp_path / "store")
    with pytest.raises(d.DistroInputError, match="tff-catalog universe"):
        d.MINER.mine(context(tmp_path, store, universe=False))


def test_needs_every_snapshot(tmp_path: Path) -> None:
    store = write_store(tmp_path / "store", date(2026, 9, 27))  # after the run date
    with pytest.raises(d.DistroInputError, match="no complete arch_repos snapshot"):
        d.MINER.mine(context(tmp_path, store))


def test_refuses_a_newer_extract_format(tmp_path: Path) -> None:
    store = write_store(tmp_path / "store", version=2)
    with pytest.raises(d.DistroInputError, match="extract version 2"):
        d.MINER.mine(context(tmp_path, store))


def test_reads_the_snapshots_the_run_parsed(tmp_path: Path) -> None:
    """build/stage/snapshots.json pins the day; a newer snapshot is not read."""
    store = write_store(tmp_path / "store", date(2026, 9, 20))
    ctx = context(tmp_path, store)
    pinned = {
        "snapshot": "2026-09-19",
        "stale": False,
        "data_date": None,
        "window": None,
        "fetched_at": None,
        "collector_version": 1,
        "records": 1,
        "baselines": [],
    }
    (ctx.paths.stage / "snapshots.json").write_text(json.dumps({d.ARCH: pinned}), "utf-8")
    with pytest.raises(d.DistroInputError, match="no complete arch_repos snapshot 2026-09-19"):
        d.MINER.mine(ctx)
    (ctx.paths.stage / "snapshots.json").write_text(
        json.dumps({d.ARCH: pinned | {"snapshot": "2026-09-20"}}), "utf-8"
    )
    assert [shape(c) for c in d.MINER.mine(ctx)] == EXPECTED


def test_checks_the_pkgstats_header(tmp_path: Path) -> None:
    store = write_store(tmp_path / "store")
    bad = store.writer(d.PKGSTATS, date(2026, 9, 25), 1)
    bad.write_bytes("month-2026-08.csv.gz", b"package,count,samples\nttf-x,1,2\n")
    bad.close()
    ctx = context(tmp_path, store)
    snap = d.snapshot(store, d.PKGSTATS, date(2026, 9, 25), DAY)
    with pytest.raises(d.DistroInputError, match="first column is not name"):
        list(d._pkgstats_names(snap))
    assert list(d.MINER.mine(ctx))  # the newest snapshot (DAY) is fine


# --- the committed seed file and the real store ------------------------------------------------------


def test_committed_seeds_are_valid(tmp_path: Path) -> None:
    """data/alias-seeds/distro.csv loads, is this miner's, and is as write_seeds writes it."""
    path = Path(__file__).resolve().parents[3] / "data" / "alias-seeds" / "distro.csv"
    rows = aliases.load_seeds(path)
    assert rows
    assert all(c.source == "distro" and not c.auto for c in rows)
    assert {c.alias.ns for c in rows} == {"arch-pkg", "deb-pkg", "deb-src"}
    assert {c.relation for c in rows} <= {"package", "build"}
    assert all(c.detail in BUILD_DETAILS for c in rows)
    keys = [(c.alias.ns, c.alias.key) for c in rows]
    assert len(keys) == len(set(keys))
    again = tmp_path / "distro.csv"
    aliases.write_seeds(rows, again)
    assert again.read_bytes() == path.read_bytes()


@pytest.mark.store
def test_real_snapshots() -> None:
    """Known answers on the store's newest snapshots, against a hand-made universe."""
    store = Store(Path(os.environ["TFF_STORE"]))
    if any(not store.dates(s) for s in d.SOURCES):
        pytest.skip("the store lacks a snapshot of " + ", ".join(d.SOURCES))
    day = max(store.dates(d.ARCH))
    snaps = {s: d.snapshot(store, s, None, day) for s in d.SOURCES}
    families = [
        info("jetbrains-mono", "JetBrains Mono", (GF, "JetBrains Mono")),
        info("jbm-nerd", "JetBrainsMono Nerd Font", (NERD, "JetBrainsMono")),
        info("roboto", "Roboto", (GF, "Roboto")),
        info("roboto-slab", "Roboto Slab", (GF, "Roboto Slab")),
        info("dejavu", "DejaVu", (CASK, "font-dejavu")),
    ]
    pkgs = d.read_packages(snaps)
    rows = {(c.alias.ns, c.alias.key): c for c in d.propose(families, pkgs)}
    assert rows["arch-pkg", "ttf-jetbrains-mono"].target == SourceKey(GF, "JetBrains Mono")
    nerd = rows["arch-pkg", "ttf-jetbrains-mono-nerd"]
    assert (nerd.target, nerd.relation) == (SourceKey(NERD, "JetBrainsMono"), "build")
    assert rows["deb-pkg", "fonts-roboto-slab"].target == SourceKey(GF, "Roboto Slab")
    assert rows["arch-pkg", "ttf-dejavu"].target == SourceKey(CASK, "font-dejavu")
    assert all(c.target != SourceKey(GF, "Roboto") for c in rows.values() if "slab" in c.alias.key)
