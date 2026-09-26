"""Stage "correct" (``tff_catalog.corrections``, milestone-1 step 10).

Every number here is invented. The data is shaped like the real sources (the
package, cask and font names are the public ones the methodology discusses:
Quicksand and DejaVu pulled in on Debian, Hack and Fantasque pulled in by
CachyOS settings packages), but no row is copied from a real snapshot.
"""

import logging
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from tests.helpers import ROOT

from tff_catalog import corrections as corr
from tff_catalog import jsonio, reviews, stageio
from tff_catalog.config_model import (
    Config,
    FoundriesConfig,
    LicenseAliasesConfig,
    LicensesConfig,
    PreinstalledConfig,
    PreinstalledSystem,
    RankingConfig,
    SiteConfig,
    from_mapping,
    load_toml,
)
from tff_catalog.corrections import (
    Inputs,
    KeyValue,
    Link,
    Term,
    abstentions,
    apply_credits,
    apply_floors,
    count_keys,
    dependency_shares,
    exposure,
    key_floors,
    pkg_id,
    ruler_counts,
    select,
    sum_aliases,
)
from tff_catalog.latin import LatinResult
from tff_catalog.licenses import LicenseClass, Verdict
from tff_catalog.mapping import Mapped
from tff_catalog.paths import Paths
from tff_catalog.records import (
    FontFileRef,
    Observation,
    Record,
    Relation,
    SourceKey,
    UniverseRecord,
    attrs,
    write_jsonl,
)
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.universe import Family, Universe

RANKING = from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"), "ranking.toml")
SITE = from_mapping(SiteConfig, load_toml(ROOT / "config" / "site.toml"), "site.toml")
CORR = RANKING.corrections
SRC = RANKING.sources
RUN = date(2026, 10, 3)
END = date(2026, 10, 2)  # the data date of the daily sources
YEAR_START = date(2025, 10, 3)
MONTHS = [f"{y}-{m:02d}" for y, m in [(2025, 9), (2025, 10), (2025, 11), (2025, 12)]] + [
    f"2026-{m:02d}" for m in range(1, 9)
]
PREINSTALLED = PreinstalledConfig(
    schema=1,
    systems={
        "gnome-synth": PreinstalledSystem(
            "GNOME (synthetic)", "linux", ("Cantarell",), "https://synth.example/gnome"
        ),
        "android-synth": PreinstalledSystem(
            "Android (synthetic)", "android", ("Roboto",), "https://synth.example/android"
        ),
        "ghost-synth": PreinstalledSystem(
            "Ghost (synthetic)",
            "linux",
            ("Nonexistent Sans", "Sarasa Gothic"),  # no family; a family the gates removed
            "https://synth.example/ghost",
        ),
    },
)


# --- builders ----------------------------------------------------------------------------------


def k(ns: str, key: str) -> SourceKey:
    return SourceKey(ns, key)


def obs(
    source: str,
    series: str,
    key: SourceKey,
    value: float | None,
    *,
    start: date = YEAR_START,
    end: date = END,
    unit: str = "installs",
    **a: Any,
) -> Observation:
    return Observation(source, series, key, value, unit, start, end, attrs(**a))  # type: ignore[arg-type]


def month(ym: str) -> tuple[date, date]:
    y, m = map(int, ym.split("-"))
    start = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def pkgstats(pkg: str, share: float, months: list[str] = MONTHS) -> list[Observation]:
    return [
        obs(
            "pkgstats",
            ym,
            k("arch-pkg", pkg),
            share,
            start=month(ym)[0],
            end=month(ym)[1],
            unit="share",
        )
        for ym in months
    ]


def family(fid: str, name: str, keys: list[tuple[str, str]] = ()) -> Family:  # type: ignore[assignment]
    return Family(
        id=fid,
        family=name,
        keys=tuple(sorted(k(ns, key) for ns, key in keys)),
        sources=("synth_universe",),
        first_seen=date(2026, 9, 1),
        minted_from=name,
    )


def depends(source: str, subject: str, obj: str, alt: int = 0, **a: Any) -> Relation:
    ns = "deb-pkg" if source == "debian" else "arch-pkg"
    return Relation(source, k(ns, subject), "depends", k(ns, obj), alt, attrs(**a))


def group_edge(member: str) -> Relation:
    return Relation("arch_repos", k("arch-pkg", "nerd-fonts"), "group", k("arch-pkg", member))


def inputs(
    fams: list[Family],
    keymap: dict[SourceKey, tuple[str, str, str]],
    observations: list[Observation],
    **kw: Any,
) -> Inputs:
    by_collector: dict[str, list[Observation]] = {}
    for o in observations:
        by_collector.setdefault(o.source, []).append(o)
    families = {f.id: f for f in fams}
    return Inputs(
        families=families,
        links={key: (Link(key, *v),) for key, v in keymap.items()},
        observations={c: tuple(v) for c, v in by_collector.items()},
        universe_keys={key: f.id for f in fams for key in f.keys},
        **kw,
    )


# --- the world of the stage test -----------------------------------------------------------------

FAMILIES = [
    family("quicksand", "Quicksand", [("gf-family", "Quicksand"), ("brew-cask", "font-quicksand")]),
    family("dejavu-sans", "DejaVu Sans", [("brew-cask", "font-dejavu")]),
    family("liberation-sans", "Liberation Sans", [("brew-cask", "font-liberation")]),
    family(
        "hack",
        "Hack",
        [("brew-cask", "font-hack"), ("brew-cask", "font-hack-nerd-font"), ("nerd-folder", "Hack")],
    ),
    family(
        "fantasque-sans-mono", "Fantasque Sans Mono", [("brew-cask", "font-fantasque-sans-mono")]
    ),
    family("fira-sans", "Fira Sans", [("gf-family", "Fira Sans")]),
    family("cantarell", "Cantarell", [("gf-family", "Cantarell"), ("brew-cask", "font-cantarell")]),
    family(
        "jetbrains-mono",
        "JetBrains Mono",
        [
            ("gf-family", "JetBrains Mono"),
            ("brew-cask", "font-jetbrains-mono"),
            ("brew-cask", "font-jetbrains-mono-nerd-font"),
            ("nerd-folder", "JetBrainsMono"),
        ],
    ),
    family("roboto", "Roboto", [("gf-family", "Roboto")]),
    family("newfont-sans", "Newfont Sans", [("brew-cask", "font-newfont-sans")]),
    family("sarasa-gothic", "Sarasa Gothic", [("brew-cask", "font-sarasa-gothic")]),  # fails Latin
    family("urw-bookman", "URW Bookman", [("brew-cask", "font-urw-bookman")]),  # excluded license
]
DROPPED = replace(
    family("synth-icons", "Synth Icons", [("brew-cask", "font-synth-icons")]), drop="icon"
)

KEYMAP: dict[SourceKey, tuple[str, str, str]] = {
    # Homebrew casks
    k("brew-cask", "font-quicksand"): ("quicksand", "direct", ""),
    k("brew-cask", "font-dejavu"): ("dejavu-sans", "direct", ""),
    k("brew-cask", "font-hack"): ("hack", "direct", ""),
    k("brew-cask", "font-hack-nerd-font"): ("hack", "build", "nerd"),
    k("brew-cask", "font-fantasque-sans-mono"): ("fantasque-sans-mono", "direct", ""),
    k("brew-cask", "font-cantarell"): ("cantarell", "direct", ""),
    k("brew-cask", "font-jetbrains-mono"): ("jetbrains-mono", "direct", ""),
    k("brew-cask", "font-jetbrains-mono-nerd-font"): ("jetbrains-mono", "build", "nerd"),
    k("brew-cask", "font-newfont-sans"): ("newfont-sans", "direct", ""),
    k("brew-cask", "font-sarasa-gothic"): ("sarasa-gothic", "direct", ""),
    k("brew-cask", "font-urw-bookman"): ("urw-bookman", "direct", ""),
    k("brew-cask", "font-synth-icons"): ("synth-icons", "direct", ""),
    # Arch packages
    k("arch-pkg", "ttf-hack"): ("hack", "package", ""),
    k("arch-pkg", "ttf-fantasque-sans-mono"): ("fantasque-sans-mono", "package", ""),
    k("arch-pkg", "ttf-fantasque-nerd"): ("fantasque-sans-mono", "build", "nerd"),
    k("arch-pkg", "ttf-fira-sans"): ("fira-sans", "package", ""),
    k("arch-pkg", "ttf-dejavu"): ("dejavu-sans", "package", ""),
    k("arch-pkg", "ttf-jetbrains-mono"): ("jetbrains-mono", "package", ""),
    k("arch-pkg", "ttf-jetbrains-mono-nerd"): ("jetbrains-mono", "build", "nerd"),
    k("arch-pkg", "cantarell-fonts"): ("cantarell", "package", ""),
    # Debian packages
    k("deb-pkg", "fonts-quicksand"): ("quicksand", "package", ""),
    k("deb-pkg", "fonts-dejavu-core"): ("dejavu-sans", "package", ""),
    k("deb-pkg", "fonts-liberation"): ("liberation-sans", "package", ""),
    k("deb-pkg", "fonts-hack"): ("hack", "package", ""),
    k("deb-pkg", "fonts-cantarell"): ("cantarell", "package", ""),
    k("deb-pkg", "fonts-fantasque-sans"): ("fantasque-sans-mono", "package", ""),
    # Google, GitHub and Nerd Fonts
    k("gf-family", "Quicksand"): ("quicksand", "direct", ""),
    k("gf-family", "Fira Sans"): ("fira-sans", "direct", ""),
    k("gf-family", "Cantarell"): ("cantarell", "direct", ""),
    k("gf-family", "JetBrains Mono"): ("jetbrains-mono", "direct", ""),
    k("gf-family", "Roboto"): ("roboto", "direct", ""),
    k("gh-asset", "JetBrains/JetBrainsMono/JetBrainsMono.zip"): ("jetbrains-mono", "direct", ""),
    k("nerd-folder", "JetBrainsMono"): ("jetbrains-mono", "build", "nerd"),
    k("nerd-folder", "Hack"): ("hack", "build", "nerd"),
}

NERD_CASKS = {  # 365-day installs; the unmapped ones still set the gate-M3 floor
    "font-jetbrains-mono-nerd-font": 141_000,
    "font-hack-nerd-font": 20_000,
    "font-fira-code-nerd-font": 60_000,
    "font-meslo-lg-nerd-font": 83_000,
    "font-agave-nerd-font": 2_149,
    "font-3270-nerd-font": 2_300,
}
NERD_CASK_FLOOR = 2_224.5  # p10 of NERD_CASKS: 2,149 + 0.5 x (2,300 - 2,149)


def homebrew_records() -> list[Record]:
    old = {"first_seen": "2019-06-01"}
    rows = [
        obs("homebrew_analytics", "365d", k("brew-cask", c), v, **old)
        for c, v in NERD_CASKS.items()
    ]
    plain = {
        "font-quicksand": 900,
        "font-dejavu": 400,
        "font-hack": 5_000,
        "font-fantasque-sans-mono": 700,
        "font-cantarell": 50,
        "font-jetbrains-mono": 42_000,
        "font-sarasa-gothic": 50_000,
        "font-urw-bookman": 3_000,
        "font-synth-icons": 9_000,
    }
    rows += [
        obs("homebrew_analytics", "365d", k("brew-cask", c), v, **old) for c, v in plain.items()
    ]
    rows.append(
        obs(
            "homebrew_analytics",
            "365d",
            k("brew-cask", "font-newfont-sans"),
            300,
            first_seen=(END - timedelta(days=20)).isoformat(),
        )
    )
    month_ago = END - timedelta(days=29)  # the 30-day window: Rising's recent one
    rows.append(
        obs(
            "homebrew_analytics",
            "30d",
            k("brew-cask", "font-quicksand"),
            1e9,
            start=month_ago,
            **old,
        )
    )
    rows.append(
        obs("homebrew_analytics", "30d", k("brew-cask", "font-hack"), 100, start=month_ago, **old)
    )
    return rows


def cask_records() -> list[Record]:
    def cask(token: str, url: str) -> UniverseRecord:
        return UniverseRecord(
            "homebrew_casks", k("brew-cask", token), token, files=(FontFileRef(url=url),)
        )

    return [
        cask(
            "font-jetbrains-mono",
            "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip",
        ),
        cask(
            "font-jetbrains-mono-nerd-font",
            "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/JetBrainsMono.zip",
        ),
        cask("font-hack", "https://fonts.synth.example/hack-v3.003.zip"),
        cask("font-liberation", "https://fonts.synth.example/liberation-2.1.5.tar.gz"),
    ]


def arch_records() -> tuple[list[Record], list[Record]]:
    shares = {
        "ttf-hack": 0.06,
        "cachyos-hyprland-settings": 0.035,
        "ttf-fantasque-sans-mono": 0.01,
        "ttf-fantasque-nerd": 0.045,
        "cachyos-kde-settings": 0.0357,
        "ttf-fira-sans": 0.09,
        "ttf-dejavu": 0.30,
        "ttf-jetbrains-mono": 0.15,
        "ttf-jetbrains-mono-nerd": 0.14,
        "cantarell-fonts": 0.20,
        "ttf-firacode-nerd": 0.13,
        "ttf-meslo-nerd": 0.06,
        "ttf-sourcecodepro-nerd": 0.042,
        "ttf-iosevka-nerd": 0.041,
    }
    stats: list[Record] = [o for pkg, s in shares.items() for o in pkgstats(pkg, s)]
    stats += pkgstats("otf-departure-mono-nerd", 0.0008, MONTHS[-2:])
    members = [p for p in shares if p.endswith("-nerd")] + ["otf-departure-mono-nerd"]
    repos: list[Record] = [group_edge(m) for m in members]
    repos += [
        depends("arch_repos", "cachyos-hyprland-settings", "ttf-hack", distro="cachyos"),
        depends("arch_repos", "cachyos-kde-settings", "ttf-fantasque-nerd", distro="cachyos"),
        depends("arch_repos", "cachyos-kde-settings", "ttf-fira-sans", distro="cachyos"),
    ]
    return stats, repos


def debian_records() -> list[Record]:
    inst = {
        "fonts-quicksand": 128_000,
        "desktop-base": 150_000,
        "fonts-dejavu-core": 226_000,
        "fontconfig-config": 231_000,
        "fonts-liberation": 125_000,
        "fonts-hack": 38_000,
        "synth-editor": 10,
        "synth-shell": 90_000,
        "fonts-cantarell": 81_000,
        "fonts-fantasque-sans": 900,
    }
    rows: list[Record] = [
        obs("debian", "inst", k("deb-pkg", p), v, start=END, submissions=285_000)
        for p, v in inst.items()
    ]
    rows += [
        depends("debian", "desktop-base", "fonts-quicksand"),
        depends("debian", "fontconfig-config", "fonts-dejavu-core", 0),
        depends("debian", "fontconfig-config", "fonts-liberation", 1),
        depends("debian", "synth-editor", "fonts-hack"),
        depends("debian", "synth-shell", "fonts-cantarell"),  # also preinstalled (gnome-synth)
    ]
    return rows


def counter_records() -> tuple[list[Record], list[Record]]:
    github: list[Record] = [
        obs(
            "github_releases",
            "lifetime",
            k("gh-asset", "JetBrains/JetBrainsMono/JetBrainsMono.zip"),
            560_000,
            start=date(2023, 1, 14),
            release="v2.304",
            first_seen="2023-01-14",
            published_at="2023-01-14",
            repo="JetBrains/JetBrainsMono",
        )
    ]
    nerd_values = {
        "JetBrainsMono": 900_000,
        "Hack": 400_000,
        "FiraCode": 700_000,
        "Meslo": 500_000,
        "Agave": 150_000,
        "FontPatcher": 50_000,
    }
    nerd: list[Record] = [
        obs(
            "nerd_releases",
            "lifetime",
            k("nerd-folder", name),
            v,
            start=date(2025, 4, 24),
            release="v3.4.0",
            first_seen="2025-04-24",
        )
        for name, v in nerd_values.items()
    ]
    return github, nerd


def google_records() -> list[Record]:
    views = {
        "Quicksand": 1_000_000,
        "Fira Sans": 5_000_000,
        "Cantarell": 1_000,
        "JetBrains Mono": 2_000_000,
        "Roboto": 900_000_000,
    }
    rows: list[Record] = [
        obs("gf_stats", "year", k("gf-family", n), v, unit="views") for n, v in views.items()
    ]
    month_ago = END - timedelta(days=29)
    rows += [
        obs(
            "gf_stats", "30day", k("gf-family", "Fira Sans"), 600_000, start=month_ago, unit="views"
        ),
        obs(
            "gf_stats", "30day", k("gf-family", "Roboto"), 70_000_000, start=month_ago, unit="views"
        ),
    ]
    return rows


def build_world(root: Path) -> Paths:
    paths = Paths.for_root(root)
    stats, repos = arch_records()
    github, nerd = counter_records()
    records = {
        "homebrew_analytics": homebrew_records(),
        "homebrew_casks": cask_records(),
        "pkgstats": stats,
        "arch_repos": repos,
        "debian": debian_records(),
        "github_releases": github,
        "nerd_releases": nerd,
        "gf_stats": google_records(),
    }
    for collector, recs in records.items():
        write_jsonl(recs, paths.records / f"{collector}.jsonl")
    universe = Universe(families={f.id: f for f in [*FAMILIES, DROPPED]}, unmapped=())
    stageio.dump_stage(paths, "universe", universe)
    mapped = [
        Mapped(r, KEYMAP[r.key][0], KEYMAP[r.key][1], KEYMAP[r.key][2])
        for recs in records.values()
        for r in recs
        if isinstance(r, Observation) and r.key in KEYMAP
    ]
    stageio.dump_stage(paths, "mapped", mapped)
    latin = {
        f.id: LatinResult(f.id != "sarasa-gothic", "gf_metadata", "extended") for f in FAMILIES
    }
    stageio.dump_stage(paths, "latin", latin)
    licenses = {
        f.id: Verdict(
            f.id,
            "OFL-1.1",
            LicenseClass("OFL-1.1", "excluded" if f.id == "urw-bookman" else "allowed"),
            True,
            (),
        )
        for f in FAMILIES
    }
    stageio.dump_stage(paths, "licenses", licenses)
    return paths


def context(paths: Paths, first_seen: dict[str, Any] | None = None) -> StageContext:
    config = Config(
        ranking=RANKING,
        licenses=LicensesConfig(1, {}, {}, {}),
        license_aliases=LicenseAliasesConfig(1, {}),
        preinstalled=PREINSTALLED,
        foundries=FoundriesConfig(1, {}),
        site=SITE,
        sources={},
    )
    return StageContext(
        paths=paths,
        config=config,
        state=State(first_seen=first_seen or {}),
        run_date=RUN,
        store=None,
        fetcher=None,
        log=logging.getLogger("test_corrections"),
    )


@pytest.fixture
def world(tmp_path: Path) -> Paths:
    return build_world(tmp_path)


def outputs(paths: Paths) -> dict[str, bytes]:
    files = [
        paths.stage / "terms.json",
        paths.stage / "ruler_counts.json",
        paths.stage / "tags.json",
        paths.build / "corrections.md",
        paths.next_state / "first_seen.json",
    ]
    return {p.name: p.read_bytes() for p in files}


# --- the stage -----------------------------------------------------------------------------------


def test_linux_sources_abstain_in_most_chosen_and_count_in_most_installed(world: Paths) -> None:
    corr.run(context(world))
    views = stageio.load_stage(world, "terms")
    chosen, installed = views["desktop_chosen"], views["desktop_installed"]
    # Quicksand (desktop-base) and DejaVu (fontconfig-config, first alternative) on Debian.
    for fid in ("quicksand", "dejavu-sans"):
        assert fid not in chosen["debian"]
        assert installed["debian"][fid].state == "observed"
    # Hack on Arch, pulled in by a CachyOS settings package.
    assert "hack" not in chosen["arch"]
    assert installed["arch"]["hack"].state == "observed"
    # The second alternative of `a | b` is not pulled in; a tiny dependent pulls nothing.
    assert chosen["debian"]["liberation-sans"].state == "observed"
    assert chosen["debian"]["hack"].state == "observed"
    # A Linux-preinstalled font abstains in every Linux source.
    for source in ("arch", "debian"):
        assert "cantarell" not in chosen[source]
        assert installed[source]["cantarell"].state == "observed"
    # Every view but most installed abstains, and Homebrew never does.
    for view in ("coding", "rising"):
        assert "hack" not in views[view]["arch"]
    assert chosen["homebrew"]["quicksand"].state == "observed"
    # 35-50% is flagged, not abstained (gate M8).
    assert chosen["arch"]["fira-sans"].flags == ("dependency_review",)
    assert installed["arch"]["fira-sans"].flags == ("dependency_review",)
    assert "project" in views
    assert "overall" not in views  # overall reuses desktop_chosen and project


def test_tags_name_the_systems_and_packages(world: Paths) -> None:
    corr.run(context(world))
    tags = stageio.load_stage(world, "tags")
    assert tags["quicksand"].pulled_in_by == (("debian", "desktop-base"),)
    assert tags["dejavu-sans"].pulled_in_by == (("debian", "fontconfig-config"),)
    assert tags["hack"].pulled_in_by == (("cachyos", "cachyos-hyprland-settings"),)
    assert tags["fantasque-sans-mono"].pulled_in_by == (("cachyos", "cachyos-kde-settings"),)
    assert tags["cantarell"].preinstalled_on == ("gnome-synth",)
    assert tags["cantarell"].flags == ("no_deliberate_evidence",)  # Homebrew is censored
    assert tags["roboto"].preinstalled_on == ("android-synth",)  # a tag, never an abstention
    assert "fira-sans" not in tags
    assert tags["quicksand"].flags == ()  # it keeps an observed Homebrew term


def test_floors_credits_ruler_and_gates(world: Paths) -> None:
    corr.run(context(world))
    views = stageio.load_stage(world, "terms")
    installed = views["desktop_installed"]
    brew = installed["homebrew"]
    # Gate M3: the Nerd cask loses the Nerd floor before nerd_credit; then the flat 20.
    assert brew["jetbrains-mono"].value == pytest.approx(42_000 + 141_000 - NERD_CASK_FLOOR - 20)
    assert brew["cantarell"] == Term(30.0, "censored", "homebrew", "below_floor")
    assert brew["liberation-sans"] == Term(None, "censored", "homebrew", "no_value")
    assert brew["newfont-sans"] == Term(None, "too_new", "homebrew")
    # The ruler: credits, no floor at all, 0 for a cask with no analytics row.
    ruler = stageio.load_stage(world, "ruler_counts")
    assert ruler["jetbrains-mono"] == 42_000 + 141_000
    assert ruler["liberation-sans"] == 0.0
    # Filter first: failed Latin, excluded license and dropped families appear nowhere.
    for fid in ("sarasa-gothic", "urw-bookman", "synth-icons"):
        assert fid not in ruler
        assert all(fid not in terms for view in views.values() for terms in view.values())
    # Gate M4: tenured nerd-fonts members lose the p10 of their shares (0.0415 here).
    assert installed["arch"]["jetbrains-mono"].value == pytest.approx(0.15 + 0.14 - 0.0415)
    assert installed["arch"]["fantasque-sans-mono"].value == pytest.approx(0.01 + 0.045 - 0.0415)
    # Gate M5: the cask downloads the repo's asset, so GitHub joins the homebrew group.
    assert installed["github"]["jetbrains-mono"].group == "homebrew"
    assert installed["nerd"]["jetbrains-mono"].group == "homebrew"
    assert installed["nerd"]["hack"].group == "github_counters"
    # Google: ranks only in the report; the term keeps its value for the engine.
    assert views["project"]["google"]["fira-sans"].state == "observed"


def test_a_missing_input_names_the_stage_to_run(world: Paths) -> None:
    stageio.stage_path(world, "mapped").unlink()
    with pytest.raises(FileNotFoundError, match="run stage 'map' first"):
        corr.run(context(world))
    assert not (world.stage / "terms.json").exists()  # nothing written


def test_outputs_match_their_schemas(world: Paths) -> None:
    corr.run(context(world))
    for name in ("terms", "ruler_counts"):
        schema = jsonio.load(ROOT / "schemas" / stageio.STAGE_FILES[name].schema)  # type: ignore[operator]
        doc = jsonio.load(stageio.stage_path(world, name))
        errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=str)
        assert not errors, errors[0].message


def test_report_flags_new_cases_and_hides_google_values(world: Paths) -> None:
    corr.run(context(world))
    report = (world.build / "corrections.md").read_text()
    assert "new cases for the owner (gate X)" in report
    for cid in (
        "dep-debian-quicksand",
        "dep-debian-dejavu-sans",
        "dep-arch-hack",
        "pre-arch-cantarell",
    ):
        assert f"`{cid}` | **new**" in report
    assert "`rev-arch-fira-sans` | **new**" in report
    assert "| ghost-synth | Nonexistent Sans |" in report
    assert "Sarasa Gothic |" not in report  # a family the Latin gate removed is no alias gap
    assert "2,224" in report  # the Homebrew Nerd floor
    queue = jsonio.load(world.queues / "corrections.json")
    assert [q["id"] for q in queue] == sorted(q["id"] for q in queue)
    assert len(queue) == report.count("| **new** |")
    quicksand = next(q for q in queue if q["id"] == "dep-debian-quicksand")
    assert quicksand["gate"] == "X"
    assert quicksand["options"][quicksand["recommended"]] == "Yes: leave it out of most chosen"
    assert all(len(q["options"]) <= reviews.MAX_OPTIONS for q in queue)
    for value in ("5,000,000", "5000000", "900,000,000", "600,000", "70,000,000"):
        assert value not in report  # Google's views, 12-month or recent (ruling T2)


def test_state_and_reruns_are_byte_stable(world: Paths) -> None:
    prev = {"quicksand": {"catalog": "2026-09-03", "sources": {"homebrew": "2018-01-01"}}}
    corr.run(context(world, prev))
    first = outputs(world)
    corr.run(context(world, prev))
    assert outputs(world) == first
    seen = jsonio.load(world.next_state / "first_seen.json")
    assert seen["quicksand"]["catalog"] == "2026-09-03"
    assert seen["quicksand"]["sources"]["homebrew"] == "2018-01-01"  # the earlier date wins
    assert seen["jetbrains-mono"]["sources"]["homebrew"] == "2019-06-01"
    assert seen["jetbrains-mono"]["sources"]["github"] == "2023-01-14"  # the asset's creation
    assert seen["newfont-sans"]["sources"]["homebrew"] == (END - timedelta(days=20)).isoformat()
    # Only a channel's own date is a first day on it: Debian has none, and an Arch
    # package with shares from the first month of the window is older than the window.
    # Recording the data date would make every such font look new to Rising.
    assert "debian" not in seen["quicksand"]["sources"]
    assert "arch" not in seen["hack"]["sources"]
    assert "cantarell" not in seen or "debian" not in seen["cantarell"]["sources"]


def test_owner_rulings_reverse_cases(world: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = reviews.gate_dir(world, "X")
    directory.mkdir(parents=True)
    (directory / "2026-10-04.toml").write_text("# read through reviews.load_rulings\n")
    ruling = reviews.Ruling(
        "X",
        date(2026, 10, 4),
        (
            reviews.Answer("dep-debian-quicksand", "Keep counting", "Owner", "b", False),
            reviews.Answer("rev-arch-fira-sans", "Abstain", "Owner", "b", False),
            reviews.Answer("dep-arch-hack", "Abstain", "Owner", "a", True),
        ),
    )
    monkeypatch.setattr(reviews, "load_rulings", lambda paths, gate: [ruling])
    corr.run(context(world))
    views = stageio.load_stage(world, "terms")
    assert views["desktop_chosen"]["debian"]["quicksand"].state == "observed"
    assert "fira-sans" not in views["desktop_chosen"]["arch"]
    assert "hack" not in views["desktop_chosen"]["arch"]
    tags = stageio.load_stage(world, "tags")
    assert "quicksand" not in tags
    assert tags["fira-sans"].pulled_in_by == (("cachyos", "cachyos-kde-settings"),)
    report = (world.build / "corrections.md").read_text()
    assert "`dep-debian-quicksand` | overruled" in report
    assert "`dep-arch-hack` | accepted" in report
    # The queue keeps every case, so a ruling can change later; reviews asks only the open ones.
    answered = {"dep-debian-quicksand", "rev-arch-fira-sans", "dep-arch-hack"}
    queue = {q["id"] for q in jsonio.load(world.queues / "corrections.json")}
    assert answered <= queue
    still_open = {q.id for q in reviews.questions(world, "X")}
    assert still_open == queue - answered
    assert "dep-debian-dejavu-sans" in still_open


def test_an_overruled_preinstall_leaves_the_dependency_rule(
    world: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    corr.run(context(world))
    cases = {q["id"] for q in jsonio.load(world.queues / "corrections.json")}
    assert "pre-debian-cantarell" in cases
    assert "dep-debian-cantarell" not in cases  # the preinstall decides first
    directory = reviews.gate_dir(world, "X")
    directory.mkdir(parents=True)
    (directory / "2026-10-04.toml").write_text("# read through reviews.load_rulings\n")
    ruling = reviews.Ruling(
        "X",
        date(2026, 10, 4),
        (reviews.Answer("pre-debian-cantarell", "Keep counting", "Owner", "b", False),),
    )
    monkeypatch.setattr(reviews, "load_rulings", lambda paths, gate: [ruling])
    corr.run(context(world))
    # synth-shell still pulls fonts-cantarell into every Debian install: a new case, abstaining.
    queue = {q["id"]: q for q in jsonio.load(world.queues / "corrections.json")}
    assert queue["dep-debian-cantarell"]["package"] == "synth-shell"
    report = (world.build / "corrections.md").read_text()
    assert "`dep-debian-cantarell` | **new**" in report
    assert "`pre-debian-cantarell` | overruled" in report
    chosen = stageio.load_stage(world, "terms")["desktop_chosen"]
    assert "cantarell" not in chosen["debian"]
    assert "cantarell" not in chosen["arch"]  # the Arch preinstall case stands


def test_rising_holds_the_recent_windows(world: Paths) -> None:
    corr.run(context(world))
    views = stageio.load_stage(world, "terms")
    rising, installed = views["rising"], views["desktop_installed"]
    # Homebrew's 30 days, as a yearly rate like its 12-month term.
    brew = rising["homebrew"]
    assert brew["hack"].value == pytest.approx(100 * 365 / (365 / 12) - 20)
    assert brew["quicksand"].value > installed["homebrew"]["quicksand"].value
    # The 12-month term's families, no more: no 30-day row is censored, too new stays so.
    assert set(brew) == set(installed["homebrew"])
    assert brew["jetbrains-mono"] == Term(None, "censored", "homebrew", "no_value")
    assert brew["newfont-sans"].state == "too_new"
    # Arch: the latest month, floored with the 12-month tenure (the two-month member
    # would lower the gate-M4 floor), and with the same abstentions as most chosen.
    arch = rising["arch"]
    assert arch["jetbrains-mono"].value == pytest.approx(installed["arch"]["jetbrains-mono"].value)
    assert set(arch) == set(views["desktop_chosen"]["arch"])
    assert "hack" not in arch
    # Google's 30 days. This world has no npm or jsDelivr data.
    assert rising["google"]["fira-sans"].state == "observed"
    assert rising["google"]["quicksand"] == Term(None, "censored", "google", "no_value")
    assert set(rising) == {"homebrew", "arch", "google"}
    report = (world.build / "corrections.md").read_text()
    assert "| homebrew | 365d | 30d |" in report
    assert "| arch | YYYY-MM | 2026-08 |" in report
    assert "| npm_fontsource | no data | |" in report


def test_bundle_rows_credit_every_family(tmp_path: Path) -> None:
    paths = build_world(tmp_path)
    fams = [
        *FAMILIES,
        family("liberation-serif", "Liberation Serif"),
        family("liberation-mono", "Liberation Mono"),
    ]
    universe = Universe(families={f.id: f for f in [*fams, DROPPED]}, unmapped=())
    stageio.dump_stage(paths, "universe", universe)
    latin = stageio.load_stage(paths, "latin")
    ok = LatinResult(True, "gf_metadata", "extended")
    stageio.dump_stage(paths, "latin", {**latin, "liberation-serif": ok, "liberation-mono": ok})
    licenses = stageio.load_stage(paths, "licenses")
    allowed = licenses["liberation-sans"]
    stageio.dump_stage(
        paths,
        "licenses",
        {
            **licenses,
            "liberation-serif": allowed,
            "liberation-mono": replace(allowed, license=None),
        },
    )
    rows = [
        f"{alias},{ns},liberation-{m},bundle,,synth,2026-09-01,owner"
        for alias, ns in [
            ("font-liberation-bundle", "brew-cask"),
            ("fonts-synth-bundle", "deb-pkg"),
        ]
        for m in ("mono", "sans", "serif")
    ]
    paths.aliases_csv.parent.mkdir(parents=True, exist_ok=True)
    paths.aliases_csv.write_text(
        "alias,ns,family_id,relation,detail,source,first_seen,reviewed_by\n"
        + "\n".join(rows)
        + "\n"
    )
    extra = obs("homebrew_analytics", "365d", k("brew-cask", "font-liberation-bundle"), 3_000)
    write_jsonl([*homebrew_records(), extra], paths.records / "homebrew_analytics.jsonl")
    # A Debian bundle package that another package depends on pulls in each family it holds.
    debian = [
        *debian_records(),
        obs("debian", "inst", k("deb-pkg", "fonts-synth-bundle"), 50_000, start=END),
        obs("debian", "inst", k("deb-pkg", "synth-office"), 60_000, start=END),
        depends("debian", "synth-office", "fonts-synth-bundle"),
    ]
    write_jsonl(debian, paths.records / "debian.jsonl")
    corr.run(context(paths))
    views = stageio.load_stage(paths, "terms")
    chosen, installed = views["desktop_chosen"]["debian"], views["desktop_installed"]["debian"]
    assert installed["liberation-serif"].value == 25_000.0  # bundle_credit
    assert "liberation-serif" not in chosen  # all its installs come with synth-office
    # Liberation Sans's own package holds more: 50,000 of 125,000 is flagged, not abstained.
    assert chosen["liberation-sans"].flags == ("dependency_review",)
    tags = stageio.load_stage(paths, "tags")
    assert tags["liberation-serif"].pulled_in_by == (("debian", "synth-office"),)
    brew = views["desktop_installed"]["homebrew"]
    # D2 default: each eligible member gets bundle_credit (0.5) and a bundle-only flag.
    assert brew["liberation-serif"] == Term(
        3_000 * 0.5 - 20, "observed", "homebrew", flags=("bundle_only",)
    )
    assert brew["liberation-sans"].value == pytest.approx(3_000 * 0.5 - 20)
    assert brew["liberation-sans"].flags == ("bundle_only",)
    assert "liberation-mono" not in brew  # no license: the gate removed it
    ruler = stageio.load_stage(paths, "ruler_counts")
    assert ruler["liberation-serif"] == 1_500.0
    split = replace(CORR, bundle_mode="split")
    sums = corr.family_sums(
        [Link(extra.key, f"liberation-{m}", "bundle", "3") for m in ("sans", "serif")],
        {extra.key: 3_000.0},
        split,
    )
    assert sums["liberation-sans"].value == pytest.approx(1_000.0)  # 1/3: mono is in the bundle


def test_a_bundle_package_pulls_in_each_of_its_families() -> None:
    counts = {"debian": {"deb-pkg:fonts-bundle": 100.0, "deb-pkg:app": 80.0}}
    families = {"deb-pkg:fonts-bundle": ("x-sans", "x-serif")}
    deps = [depends("debian", "app", "fonts-bundle")]
    found = abstentions(deps, counts, PREINSTALLED, CORR, families=families)["debian"]
    assert set(found) == {"x-sans", "x-serif"}
    assert found["x-serif"].share == 0.8


# --- the rules, one at a time --------------------------------------------------------------------


def test_exposure_counts_whole_days() -> None:
    assert exposure(None, END, 60)
    assert exposure(END - timedelta(days=60), END, 60)
    assert not exposure(END - timedelta(days=59), END, 60)
    assert not exposure(END + timedelta(days=5), END, 60)


def test_credits_take_the_smaller_for_nerd_and_cjk() -> None:
    cfg = replace(CORR, nerd_credit=1.0, cjk_build_credit=0.8)
    values = {
        "brew-cask:font-maple-mono-nf-cn": 100.0,
        "brew-cask:font-maple-mono-cn": 100.0,
        "brew-cask:font-hack-nerd-font": 100.0,
        "npm:@fontsource/inter": 100.0,
        "deb-pkg:fonts-urw-base35": 100.0,
    }
    details = {
        "brew-cask:font-maple-mono-nf-cn": "cjk",
        "brew-cask:font-maple-mono-cn": "cjk",
        "brew-cask:font-hack-nerd-font": "nerd",
        "deb-pkg:fonts-urw-base35": "bundle:4",
    }
    out = apply_credits(values, details, cfg)
    assert out == {
        "brew-cask:font-maple-mono-nf-cn": 80.0,
        "brew-cask:font-maple-mono-cn": 80.0,
        "brew-cask:font-hack-nerd-font": 100.0,
        "npm:@fontsource/inter": 100.0,
        "deb-pkg:fonts-urw-base35": 50.0,
    }
    nerd_low = replace(cfg, nerd_credit=0.5)
    assert (
        apply_credits(values, {"brew-cask:font-maple-mono-nf-cn": "nf"}, nerd_low)[
            "brew-cask:font-maple-mono-nf-cn"
        ]
        == 0.5 * 100
    )
    split = replace(cfg, bundle_mode="split")
    assert apply_credits(values, details, split)["deb-pkg:fonts-urw-base35"] == 25.0
    # "Cn" as a word of a name is condensed, not CJK: a width build keeps full credit.
    condensed = {"font-name:Martian Mono Cn": 100.0, "font-name:TeX Gyre Heros Cn": 100.0}
    widths = dict.fromkeys(condensed, "static")
    assert apply_credits(condensed, widths, cfg) == condensed


def test_sum_aliases_sums_packages_with_credits() -> None:
    def row(
        key: str, value: float | None, fid: str | None, relation: str, detail: str = ""
    ) -> Mapped:
        return Mapped(obs("npm", "last-year", k("npm", key), value), fid, relation, detail)

    mapped = [
        row("@fontsource/inter", 100.0, "inter", "direct"),
        row("@fontsource-variable/inter", 50.0, "inter", "package"),
        row("@fontsource/inter-nerd", 30.0, "inter", "build", "nerd"),
        row("@fontsource/arial", 999.0, None, "ineligible", "proprietary"),
        row("@fontsource/adwaita-sans", 777.0, "inter", "related"),
        row("@fontsource/pending", None, "pending", "direct"),
    ]
    assert sum_aliases(mapped, replace(CORR, nerd_credit=0.5)) == {"npm": {"inter": 165.0}}


def test_floors_subtract_then_censor() -> None:
    terms = apply_floors({"a": 100.0, "b": 70.0, "c": 10.0}, SRC.homebrew)
    assert terms == {
        "a": Term(80.0, "observed", "homebrew"),
        "b": Term(50.0, "censored", "homebrew", "below_floor"),
        "c": Term(0.0, "censored", "homebrew", "below_floor"),
    }
    zero = apply_floors({"d": 0.0}, SRC.github)  # no floor, no censor line: 0 is still no evidence
    assert zero["d"].state == "censored"


def test_ruler_counts_have_zero_for_casks_without_rows() -> None:
    assert ruler_counts({"b": 5.0, "a": 7.5}, ["a", "c"]) == {"a": 7.5, "b": 5.0, "c": 0.0}


def test_homebrew_nerd_floor_is_p10_of_nerd_casks() -> None:
    values = {
        k("brew-cask", c): KeyValue(float(v), None, END)
        for c, v in {**NERD_CASKS, "font-inter": 30_000}.items()
    }
    floored, notes = key_floors("homebrew", SRC.homebrew, values, ())
    assert notes[0].value == pytest.approx(NERD_CASK_FLOOR)
    assert notes[0].members == len(NERD_CASKS)
    assert floored[k("brew-cask", "font-hack-nerd-font")].value == pytest.approx(
        20_000 - NERD_CASK_FLOOR
    )
    assert floored[k("brew-cask", "font-agave-nerd-font")].value == 0.0
    assert floored[k("brew-cask", "font-inter")].value == 30_000
    # A legacy tap key is the same cask: it sets nothing, and the cask loses the floor once.
    legacy = {
        **values,
        k("brew-cask", "homebrew/cask-fonts/font-hack-nerd-font"): KeyValue(5.0, None, END),
        k("brew-cask", "homebrew/cask-fonts/font-agave-nerd-font"): KeyValue(100.0, None, END),
    }
    floored, notes = key_floors("homebrew", SRC.homebrew, legacy, ())
    assert notes[0].value == pytest.approx(NERD_CASK_FLOOR)
    assert notes[0].members == len(NERD_CASKS)
    assert floored[k("brew-cask", "homebrew/cask-fonts/font-hack-nerd-font")].value == 5.0
    assert floored[k("brew-cask", "font-agave-nerd-font")].value == 0.0
    assert floored[k("brew-cask", "homebrew/cask-fonts/font-agave-nerd-font")].value == (
        pytest.approx(100.0 - (NERD_CASK_FLOOR - 2_149))  # what its cask's own key lacked
    )
    flat = replace(SRC.homebrew, nerd_floor="flat")
    assert key_floors("homebrew", flat, values, ()) == (values, [])


def test_arch_group_floor_spares_new_members() -> None:
    stats = [
        o
        for pkg, s in [("ttf-a-nerd", 0.04), ("ttf-b-nerd", 0.05), ("ttf-c", 0.2)]
        for o in pkgstats(pkg, s)
    ]
    stats += pkgstats("ttf-new-nerd", 0.001, MONTHS[-3:])
    values = count_keys(SRC.arch, select(SRC.arch, stats))
    edges = [group_edge(p) for p in ("ttf-a-nerd", "ttf-b-nerd", "ttf-new-nerd")]
    floored, notes = key_floors("arch", SRC.arch, values, edges)
    assert notes[0].value == pytest.approx(0.041)  # p10 of the tenured 0.04 and 0.05
    assert floored[k("arch-pkg", "ttf-a-nerd")].value == pytest.approx(0.0)
    assert floored[k("arch-pkg", "ttf-b-nerd")].value == pytest.approx(0.009)
    assert floored[k("arch-pkg", "ttf-new-nerd")].value == pytest.approx(0.001)  # 3 months: kept
    assert floored[k("arch-pkg", "ttf-c")].value == pytest.approx(0.2)
    flat = replace(SRC.arch, nerd_group_floor="flat")
    floored, _ = key_floors("arch", flat, values, edges)
    assert floored[k("arch-pkg", "ttf-new-nerd")].value == 0.0


def test_nerd_releases_drop_excluded_assets_and_subtract_p10() -> None:
    _, nerd = counter_records()
    chosen = select(SRC.nerd, [r for r in nerd if isinstance(r, Observation)])
    assert k("nerd-folder", "FontPatcher") not in {o.key for o in chosen}
    values = count_keys(SRC.nerd, chosen)
    floored, notes = key_floors("nerd", SRC.nerd, values, ())
    rates = sorted(v.value or 0.0 for v in values.values())
    assert notes[0].value == pytest.approx(rates[0] + 0.4 * (rates[1] - rates[0]))
    assert min(v.value or 0.0 for v in floored.values()) == 0.0


def test_window_counts_become_rates_over_days_available() -> None:
    full = obs(
        "homebrew_analytics", "365d", k("brew-cask", "font-a"), 365.0, first_seen="2019-01-01"
    )
    young = obs(
        "homebrew_analytics",
        "365d",
        k("brew-cask", "font-b"),
        100.0,
        first_seen=(END - timedelta(days=99)).isoformat(),
    )
    values = count_keys(SRC.homebrew, [full, young])
    assert values[full.key].value == 365.0
    assert values[young.key].value == pytest.approx(100.0 * 365 / 100)
    npm = obs("npm", "last-year", k("npm", "@fontsource/a"), 12_000.0, first_seen="2020-01-01")
    assert count_keys(SRC.npm_fontsource, [npm])[npm.key].value == pytest.approx(1_000.0)


def test_monthly_shares_average_from_the_first_nonzero_month() -> None:
    stats = pkgstats("ttf-old", 0.02) + pkgstats("ttf-young", 0.03, MONTHS[-4:])
    stats += [
        obs(
            "pkgstats",
            MONTHS[5],
            k("arch-pkg", "ttf-gap"),
            0.06,
            start=month(MONTHS[5])[0],
            end=month(MONTHS[5])[1],
            unit="share",
        )
    ]
    values = count_keys(SRC.arch, select(SRC.arch, stats))
    assert values[k("arch-pkg", "ttf-old")] == KeyValue(
        pytest.approx(0.02), None, date(2026, 8, 31)
    )  # type: ignore[arg-type]
    assert values[k("arch-pkg", "ttf-young")].value == pytest.approx(0.03)
    assert values[k("arch-pkg", "ttf-young")].start == date(2026, 5, 1)
    assert values[k("arch-pkg", "ttf-gap")].value == pytest.approx(0.06 / 7)  # later months count 0


def test_snapshot_delta_first_run_and_baseline() -> None:
    def asset(value: float, end: date, release: str = "v1") -> Observation:
        return obs(
            "github_releases",
            "lifetime",
            k("gh-asset", "o/r/font.zip"),
            value,
            start=date(2025, 10, 2),
            end=end,
            release=release,
            first_seen="2025-10-02",
        )

    now = [asset(36_500.0, END), asset(1_000.0, END, "v2")]
    first = count_keys(SRC.github, now)
    assert first[k("gh-asset", "o/r/font.zip")].value == pytest.approx(36_500 + 1_000)
    base = [asset(30_500.0, END - timedelta(days=60))]
    delta = count_keys(SRC.github, now, base)
    assert delta[k("gh-asset", "o/r/font.zip")].value == pytest.approx(
        6_000 * 365 / 60
    )  # v2 is new
    shrunk = count_keys(SRC.github, [asset(100.0, END)], [asset(200.0, END - timedelta(days=60))])
    assert shrunk[k("gh-asset", "o/r/font.zip")].value == 0.0
    beta = replace(asset(5.0, END), attrs=attrs(release="v3-beta", prerelease=True))
    assert select(SRC.github, [beta]) == []


def test_dependency_rules_top_sum_and_alternatives() -> None:
    counts = {
        "debian": {
            "deb-pkg:fonts-x": 100.0,
            "deb-pkg:fonts-x-extra": 40.0,
            "deb-pkg:app-a": 30.0,
            "deb-pkg:app-b": 30.0,
            "deb-pkg:app-c": 90.0,
        }
    }
    families = {"deb-pkg:fonts-x": "x", "deb-pkg:fonts-x-extra": "x"}
    deps = [
        depends("debian", "app-a", "fonts-x"),
        depends("debian", "app-b", "fonts-x-extra"),
        depends("debian", "app-c", "fonts-y", 0),
        depends("debian", "app-c", "fonts-x", 1),
        depends("debian", "fonts-x-extra", "fonts-x"),  # the family's own package
    ]
    shares = dependency_shares(deps, counts, CORR, families=families)["debian"]["x"]
    assert [(d.package, d.share) for d in shares] == [("app-a", 0.3), ("app-b", 0.3)]
    assert abstentions(deps, counts, PREINSTALLED, CORR, families=families) == {"debian": {}}
    by_sum = replace(CORR, dependency_rule="sum")
    found = abstentions(deps, counts, PREINSTALLED, by_sum, families=families)["debian"]["x"]
    assert (found.why, found.package, found.share) == ("dependency", "app-a", 0.6)
    every = replace(CORR, dependency_alternatives="all")
    found = abstentions(deps, counts, PREINSTALLED, every, families=families)["debian"]["x"]
    assert (found.package, found.share) == ("app-c", 0.9)


def test_only_linux_systems_cause_abstentions() -> None:
    counts = {"arch": {"arch-pkg:cantarell-fonts": 0.2}, "debian": {"deb-pkg:fonts-roboto": 5.0}}
    families = {
        pkg_id(k("font-name", "Cantarell")): "cantarell",
        pkg_id(k("font-name", "Roboto")): "roboto",
    }
    found = abstentions([], counts, PREINSTALLED, CORR, families=families)
    assert set(found["arch"]) == set(found["debian"]) == {"cantarell"}
    assert found["arch"]["cantarell"].why == "preinstalled"
    assert found["arch"]["cantarell"].system == "gnome-synth"
    # Two Linux systems ship it: a source names its own system when it is one of them.
    debian = PreinstalledSystem("Debian", "linux", ("Cantarell",), "https://synth.example/debian")
    both = replace(PREINSTALLED, systems={**PREINSTALLED.systems, "debian": debian})
    found = abstentions([], counts, both, CORR, families=families)
    assert found["debian"]["cantarell"].system == "debian"
    assert found["arch"]["cantarell"].system == "debian"  # else the first by id


def test_github_group_follows_the_cask_asset() -> None:
    fam = family("jetbrains-mono", "JetBrains Mono")
    github, _ = counter_records()
    key = k("gh-asset", "JetBrains/JetBrainsMono/JetBrainsMono.zip")
    inp = inputs(
        [fam],
        {key: ("jetbrains-mono", "direct", "")},
        [r for r in github if isinstance(r, Observation)],
        cask_repos={"jetbrains-mono": frozenset({"jetbrains/jetbrainsmono"})},
    )
    res = corr.correct_source("github", SRC.github, inp, RANKING, {})
    assert res is not None
    assert res.terms["jetbrains-mono"].group == "homebrew"
    never = replace(RANKING, engine=replace(RANKING.engine, github_homebrew_same_group="never"))
    res = corr.correct_source("github", SRC.github, inp, never, {})
    assert res is not None
    assert res.terms["jetbrains-mono"].group == "github_counters"
    other = replace(inp, cask_repos={"jetbrains-mono": frozenset({"someone/else"})})
    res = corr.correct_source("github", SRC.github, other, RANKING, {})
    assert res is not None
    assert res.terms["jetbrains-mono"].group == "github_counters"


def test_almanac_width_cuts_merge_into_their_parent() -> None:
    fams = [
        family("roboto", "Roboto", [("gf-family", "Roboto")]),
        family("roboto-condensed", "Roboto Condensed", [("gf-family", "Roboto Condensed")]),
        family("barlow", "Barlow", [("gf-family", "Barlow")]),
        family("barlow-condensed", "Barlow Condensed", [("gf-family", "Barlow Condensed")]),
        family("lato", "Lato", [("gf-family", "Lato")]),
    ]
    keymap = {
        k("almanac-name", "Roboto"): ("roboto", "direct", ""),
        k("almanac-name", "Roboto Condensed"): ("roboto-condensed", "direct", ""),
        k("almanac-name", "Barlow"): ("barlow", "direct", ""),
        k("almanac-name", "Barlow Condensed"): ("barlow-condensed", "direct", ""),
    }
    data = [
        obs("almanac", "pages/mobile", k("almanac-name", "Roboto"), 90_000.0, unit="pages"),
        obs("almanac", "pages/mobile", k("almanac-name", "Barlow"), 4_000.0, unit="pages"),
        obs(
            "almanac",
            "requests/mobile/google",
            k("almanac-name", "Roboto Condensed"),
            70.0,
            unit="requests",
        ),
        # The desktop pages tab is not the services tab: it flags nothing.
        obs("almanac", "pages/desktop", k("almanac-name", "Barlow Condensed"), 30.0, unit="pages"),
    ]
    res = corr.correct_source("almanac", SRC.almanac, inputs(fams, keymap, data), RANKING, {})
    assert res is not None
    assert res.terms["roboto"] == Term(
        90_000.0, "observed", "http_archive", None, 0.5, ("parent_merge",)
    )
    assert res.terms["barlow"].flags == ()  # its cut is not in the service tab
    for cut in ("roboto-condensed", "barlow-condensed"):
        assert res.terms[cut] == Term(None, "not_covered", "http_archive", "merged_into_parent")
    assert res.terms["lato"] == Term(None, "censored", "http_archive", "no_value")  # in the frame


def test_crawls_count_exposure_from_the_data_date() -> None:
    fams = [
        family("old", "Old Sans", [("gf-family", "Old Sans")]),
        family("new", "New Sans", [("gf-family", "New Sans")]),
    ]
    data = [
        obs(
            "almanac",
            "pages/mobile",
            k("almanac-name", "Old Sans"),
            10.0,
            start=date(2025, 7, 1),
            end=date(2025, 7, 31),
            unit="pages",
        )
    ]
    inp = inputs(fams, {k("almanac-name", "Old Sans"): ("old", "direct", "")}, data)
    avail = {"old": date(2019, 3, 1), "new": date(2025, 7, 1)}
    res = corr.correct_source("almanac", SRC.almanac, inp, RANKING, avail)
    assert res is not None
    assert res.terms["old"].state == "observed"
    assert res.terms["new"] == Term(None, "too_new", "http_archive")
    assert res.seen == {}  # another channel's date is no first day on this one


def test_availability_ignores_dates_that_only_bound_the_window() -> None:
    fams = [family("a", "A Sans"), family("b", "B Sans"), family("c", "C Sans")]
    keymap = {
        k("npm", "@fontsource/a"): ("a", "direct", ""),
        k("npm", "@fontsource/b"): ("b", "direct", ""),
        k("brew-cask", "font-c"): ("c", "direct", ""),
    }
    start = END - timedelta(days=364)
    data = [
        obs(
            "npm",
            "last-year",
            k("npm", "@fontsource/a"),
            9e4,
            start=start,
            first_seen=start.isoformat(),
        ),
        obs(
            "npm", "last-year", k("npm", "@fontsource/b"), 9e4, start=start, first_seen="2026-08-01"
        ),
        obs("homebrew_analytics", "365d", k("brew-cask", "font-c"), 500.0, first_seen="2019-05-01"),
    ]
    inp = inputs(fams, keymap, data, added={"c": date(2016, 1, 1)})
    enabled = {n: s for n, s in SRC.all().items() if s.enabled}
    assert corr.availability(inp, enabled) == {"b": date(2026, 8, 1), "c": date(2016, 1, 1)}


def test_google_falls_back_to_popularity_ranks() -> None:
    fams = [
        family("a", "A Sans", [("gf-family", "A Sans")]),
        family("b", "B Sans", [("gf-family", "B Sans")]),
    ]
    keymap = {
        k("gf-family", "A Sans"): ("a", "direct", ""),
        k("gf-family", "B Sans"): ("b", "direct", ""),
    }
    data = [
        obs("google_metadata", "popularity", k("gf-family", "A Sans"), 1.0, unit="rank"),
        obs("google_metadata", "popularity", k("gf-family", "B Sans"), 2.0, unit="rank"),
    ]
    res = corr.correct_source("google", SRC.google, inputs(fams, keymap, data), RANKING, {})
    assert res is not None
    assert res.terms["a"].value == 2.0  # rank 1 scores highest
    assert res.terms["b"].value == 1.0


def test_google_counts_views_from_the_date_added() -> None:
    names = ["Old Sans", "Young Sans", "Fresh Sans", "Quiet Sans", "Newest Sans"]
    fams = [family(n.split()[0].lower(), n, [("gf-family", n)]) for n in names]
    listed = fams[:3]  # Quiet and Newest are on Google with no views row yet
    keymap = {k("gf-family", f.family): (f.id, "direct", "") for f in listed}
    data = [
        obs("gf_stats", "year", k("gf-family", f.family), 36_500.0, unit="views") for f in listed
    ]
    added = {  # Google's dateAdded, from the universe record with the same key
        ("gf-family", "oldsans"): date(2015, 1, 1),
        ("gf-family", "youngsans"): END - timedelta(days=99),
        ("gf-family", "freshsans"): END - timedelta(days=30),
        ("gf-family", "quietsans"): date(2016, 1, 1),
        ("gf-family", "newestsans"): END - timedelta(days=10),
    }
    # Another source's earlier date for the family says nothing about Google.
    inp = inputs(fams, keymap, data, added_keys=added, added={"fresh": date(2010, 1, 1)})
    res = corr.correct_source("google", SRC.google, inp, RANKING, {})
    assert res is not None
    assert res.terms["old"].value == 36_500.0
    assert res.terms["young"].value == pytest.approx(36_500 * 365 / 100)  # a rate since added
    assert res.terms["fresh"] == Term(None, "too_new", "google")
    assert res.terms["quiet"] == Term(None, "censored", "google", "no_value")
    assert res.terms["newest"] == Term(None, "too_new", "google")  # not a zero
    assert res.seen["young"] == END - timedelta(days=99)


def test_almanac_case_duplicates_count_once() -> None:
    fams = [family("roboto", "Roboto", [("gf-family", "Roboto")])]
    keymap = {
        k("almanac-name", "Roboto"): ("roboto", "direct", ""),
        k("almanac-name", "roboto"): ("roboto", "direct", ""),
    }
    data = [
        obs("almanac", "pages/mobile", k("almanac-name", "Roboto"), 90_000.0, unit="pages"),
        obs("almanac", "pages/mobile", k("almanac-name", "roboto"), 5_000.0, unit="pages"),
    ]
    res = corr.correct_source("almanac", SRC.almanac, inputs(fams, keymap, data), RANKING, {})
    assert res is not None
    assert res.terms["roboto"].value == 90_000.0  # the larger, not the sum


def test_counters_below_their_baseline_are_listed() -> None:
    fam = family("x", "X Mono")
    key = k("gh-asset", "o/r/x.zip")
    now = obs("github_releases", "lifetime", key, 100.0, release="v1", first_seen="2025-01-01")
    before = replace(now, value=200.0, end=END - timedelta(days=60))
    inp = inputs(
        [fam],
        {key: ("x", "direct", "")},
        [now],
        baselines={"github_releases": ((before.end, (before,)),)},
    )
    res = corr.correct_source("github", SRC.github, inp, RANKING, {})
    assert res is not None
    assert res.shrunk == (key,)
    assert res.terms["x"].value == 0.0
    report = corr.render_report(RUN, {"github": res}, [], inp.families, [], {})
    assert "| github | `o/r/x.zip` |" in report


def test_case_ids_fit_the_rulings_schema() -> None:
    assert corr.case_id("dependency", "debian", "quicksand") == "dep-debian-quicksand"
    long = corr.case_id("review", "debian", "a" * 80)
    assert len(long) == 64
    assert long.startswith("rev-debian-aaa")


def test_fonts_over_time_startups_hold_at_most_the_cap_of_each_week() -> None:
    """D11: 5,231 startups of 10,465 sites weigh 0.5 each and the rest 1.5, at a 25% cap."""

    def row(
        key: str, category: str, value: float, sites: int, week: str = "2026-W39"
    ) -> Observation:
        return obs(
            "fot",
            week,
            k("fot-name", key),
            value,
            unit="sites",
            category=category,
            method="browser",
            category_sites=sites,
        )

    week = [row("Inter", "startups", 100.0, 5_231), row("Inter", "popular", 10.0, 5_234)]
    capped = corr.startup_capped(week, 0.25)
    assert [o.value for o in capped] == pytest.approx(
        [100 * 0.25 * 10_465 / 5_231, 10 * 1.5], rel=1e-3
    )
    weights = [capped[0].value / 100, capped[1].value / 10]
    assert 5_231 * weights[0] / (5_231 * weights[0] + 5_234 * weights[1]) == pytest.approx(0.25)
    few = [row("Lato", "startups", 4.0, 100), row("Lato", "popular", 4.0, 900)]
    assert corr.startup_capped(few, 0.25) == few  # 10% startups: under the cap, no change
    assert corr.startup_capped(week, 1.0) == week
    got = count_keys(SRC.fot, week)[k("fot-name", "Inter")].value
    assert got == pytest.approx(100 * 0.25 * 10_465 / 5_231 + 15.0, rel=1e-3)


def test_a_package_only_a_relation_names_is_censored_not_uncovered() -> None:
    """mapping.frames' rule: either end of a relation puts the family in a package frame."""
    fams = [
        family("quicksand", "Quicksand", [("gf-family", "Quicksand")]),
        family("orbit", "Orbit", [("gf-family", "Orbit")]),
    ]
    keymap = {
        k("deb-pkg", "fonts-quicksand"): ("quicksand", "direct", ""),
        k("deb-pkg", "fonts-orbit"): ("orbit", "direct", ""),
    }
    data = [obs("debian", "inst", k("deb-pkg", "fonts-quicksand"), 5_000.0, start=END)]
    rel = depends("debian", "some-app", "fonts-orbit")
    res = corr.correct_source(
        "debian", SRC.debian, inputs(fams, keymap, data, relations=(rel,)), RANKING, {}
    )
    assert res is not None
    assert res.terms["orbit"].state == "censored"
    alone = corr.correct_source("debian", SRC.debian, inputs(fams, keymap, data), RANKING, {})
    assert alone is not None
    assert "orbit" not in alone.terms  # without the relation: not covered


def test_npm_scopes_read_purl_keys_too() -> None:
    purl = obs("ecosystems", "dependent_repos", k("npm", "pkg:npm/%40fontsource/inter"), 50.0)
    other = obs("ecosystems", "dependent_repos", k("npm", "pkg:npm/%40other/inter"), 50.0)
    assert corr.select(SRC.ecosystems, [purl, other]) == [purl]
