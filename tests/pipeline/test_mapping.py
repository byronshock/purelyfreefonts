"""Stage "map" (milestone-1 step 9): keys to families, frames, evidence states, unmatched.md.

Everything here is synthetic: public family names with invented keys and
counts, no real source rows (BRIEF item 12).
"""

import logging
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from tests.helpers import ROOT

from tff_catalog import jsonio, mapping, stageio
from tff_catalog.config_model import Config, RankingConfig, from_mapping, load_toml
from tff_catalog.keys import match_key
from tff_catalog.mapping import (
    Frame,
    IndexEntry,
    Mapped,
    Unmatched,
    entries_of,
    evidence_state,
    index_of,
    map_records,
    resolve,
    unmatched_report,
)
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Relation, SourceKey, attrs, write_jsonl
from tff_catalog.state import State
from tff_catalog.universe import Family, Universe

RUN = date(2026, 10, 3)
YEAR_START, YEAR_END = RUN - timedelta(days=365), RUN - timedelta(days=1)


# --- builders -------------------------------------------------------------------------------------


def ranking() -> RankingConfig:
    return from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"))


def obs(
    source: str,
    ns: str,
    key: str,
    value: float | None,
    *,
    series: str = "365d",
    unit: str = "installs",
    start: date = YEAR_START,
    end: date = YEAR_END,
    **kw: Any,
) -> Observation:
    return Observation(
        source=source,
        series=series,
        key=SourceKey(ns, key),
        value=value,
        unit=cast(Any, unit),
        start=start,
        end=end,
        attrs=attrs(**kw),
    )


def rel(source: str, subject: SourceKey, obj: SourceKey, kind: str = "depends") -> Relation:
    return Relation(source=source, subject=subject, kind=cast(Any, kind), object=obj)


def entry(
    ns: str, name: str, family: str, relation: str = "direct", detail: str = ""
) -> IndexEntry:
    return IndexEntry(ns, match_key(name), family, relation, detail)


def fam(fid: str, name: str, *keys: tuple[str, str], drop: str | None = None) -> Family:
    return Family(
        id=fid,
        family=name,
        keys=tuple(sorted(SourceKey(ns, k) for ns, k in keys)),
        sources=("synth",),
        first_seen=date(2026, 9, 3),
        minted_from=name,
        drop=drop,
    )


UNIVERSE = Universe(
    families={
        f.id: f
        for f in (
            fam(
                "inter",
                "Inter",
                ("gf-family", "Inter"),
                ("fs-id", "inter"),
                ("brew-cask", "font-inter"),
                ("npm", "@fontsource/inter"),
            ),
            fam("roboto", "Roboto", ("gf-family", "Roboto"), ("brew-cask", "font-roboto")),
            fam(
                "roboto-slab",
                "Roboto Slab",
                ("gf-family", "Roboto Slab"),
                ("brew-cask", "font-roboto-slab"),
            ),
            fam(
                "source-sans-3",
                "Source Sans 3",
                ("gf-family", "Source Sans 3"),
                ("brew-cask", "font-source-sans-3"),
            ),
            fam(
                "source-code-pro",
                "Source Code Pro",
                ("gf-family", "Source Code Pro"),
                ("nerd-folder", "SourceCodePro"),
            ),
            fam(
                "jetbrains-mono",
                "JetBrains Mono",
                ("gf-family", "JetBrains Mono"),
                ("brew-cask", "font-jetbrains-mono"),
                ("nerd-folder", "JetBrainsMono"),
            ),
            fam("hack", "Hack", ("nerd-folder", "Hack"), ("brew-cask", "font-hack")),
            fam("ostrich-sans", "Ostrich Sans", ("foundry-family", "Ostrich Sans")),
            fam("material-icons", "Material Icons", ("gf-family", "Material Icons"), drop="icon"),
        )
    },
    unmapped=(),
)

ALIASES = [
    entry("brew-cask", "font-source-sans-pro", "source-sans-3", "rename"),
    entry("npm", "@fontsource/source-sans-pro", "source-sans-3", "rename"),
    entry("fot-name", "Source Sans Pro", "source-sans-3", "rename"),
    entry("brew-cask", "font-sauce-code-pro-nerd-font", "source-code-pro", "build", "nerd"),
    entry("gh-asset", "SourceCodePro", "source-code-pro", "build", "nerd"),
    entry("brew-cask", "font-jetbrains-mono-nerd-font", "jetbrains-mono", "build", "nerd"),
    entry("font-name", "Arial", "", "ineligible", "proprietary"),
    entry("fot-name", "Arial", "", "ineligible", "proprietary"),
    entry("fot-name", "Inter", "inter"),
    entry("fot-name", "Roboto", "roboto"),
    entry("fot-name", "Roboto Slab", "roboto-slab"),
    entry("fot-name", "Inter Tight", "inter", "sibling"),
    entry("deb-pkg", "fonts-inter", "inter", "package"),
    entry("deb-pkg", "fonts-roboto", "roboto", "package"),
    entry("deb-pkg", "fonts-roboto-slab", "roboto-slab", "package"),
    entry("arch-pkg", "inter-font", "inter", "package"),
]


def universe_index(u: Universe) -> list[IndexEntry]:
    """Every universe key as a direct row, as stage "aliases" writes them."""
    return [entry(k.ns, k.key, f.id) for f in u.families.values() for k in f.keys]


IDX = index_of(universe_index(UNIVERSE) + ALIASES)

DEPENDENT = SourceKey("deb-pkg", "gnome-shell")
FULLWIDTH = "Source Sans Pro".translate({c: c + 0xFEE0 for c in range(0x21, 0x7F)})


def scenario() -> dict[str, list[Observation | Relation]]:
    """Records by collector: a synthetic month of every engine source kind."""
    hb = "homebrew_analytics"
    arch_months = [("2026-08", 10_000), ("2026-09", 12_000)]
    out: dict[str, list[Observation | Relation]] = {
        hb: [
            obs(hb, "brew-cask", "font-inter", 50_000),
            obs(hb, "brew-cask", "font-roboto", 20_000),
            obs(hb, "brew-cask", "font-roboto-slab", 3_000),
            obs(hb, "brew-cask", "font-source-sans-pro", 4_000),
            obs(hb, "brew-cask", "font-sauce-code-pro-nerd-font", 9_000),
            obs(hb, "brew-cask", "font-jetbrains-mono-nerd-font", 30_000),
            obs(hb, "brew-cask", "font-roboto-flex", 2_500),
            obs(hb, "brew-cask", "font-mystery", 5_000),
            obs(hb, "brew-cask", "font-tiny", 50),
            obs(hb, "brew-cask", "font-inter", 15_000, series="90d", start=RUN - timedelta(90)),
        ],
        "debian": [
            obs("debian", "deb-pkg", "fonts-inter", 500, series="inst", start=YEAR_END),
            obs("debian", "deb-pkg", "fonts-roboto", 3_000, series="inst", start=YEAR_END),
            obs("debian", "deb-pkg", "gnome-shell", 90_000, series="inst", start=YEAR_END),
            obs("debian", "deb-pkg", "fonts-unknown", 400, series="inst", start=YEAR_END),
            obs("debian", "deb-pkg", "fonts-low", 50, series="inst", start=YEAR_END),
            rel("debian", DEPENDENT, SourceKey("deb-pkg", "fonts-roboto")),
            rel(
                "debian", SourceKey("deb-pkg", "fonts-unknown"), SourceKey("deb-pkg", "fontconfig")
            ),
            rel("debian", SourceKey("deb-pkg", "fonts-roboto-slab"), SourceKey("deb-pkg", "libc6")),
        ],
        "fot": [
            obs("fot", "fot-name", name, v, series=week, unit="sites", start=RUN, end=RUN)
            for week, rows in (
                ("2026-W38", {"Inter": 40, "Roboto": 30, "Arial": 100, "Proxima Nova": 37}),
                ("2026-W39", {"Inter": 42, "Inter Tight": 12, "Arial": 90, "Proxima Nova": 39}),
            )
            for name, v in rows.items()
        ],
        "gf_stats": [
            obs("gf_stats", "gf-family", name, v, series="year", unit="views")
            for name, v in {
                "Inter": 1_000_000_000,
                "Roboto": 2_000_000_000,
                "Roboto Slab": 100_000_000,
                "Material Icons": 5_000_000_000,
                "Brand New Font": 70_123_456,
            }.items()
        ],
        "npm": [
            obs("npm", "npm", pkg, v, series="last-year", unit="downloads")
            for pkg, v in {
                "@fontsource/inter": 1_200_000,
                "@fontsource/source-sans-pro": 240_000,
                "@fontsource/mystery": 60_000,
                "@fontsource/little": 6_000,
                "@expo-google-fonts/inter": 24_000,
            }.items()
        ],
        "pkgstats": [
            obs(
                "pkgstats",
                "arch-pkg",
                pkg,
                count,
                series=month,
                unit="installs",
                start=date.fromisoformat(month + "-01"),
                end=date.fromisoformat(month + "-28"),
                samples=samples,
            )
            for month, samples in arch_months
            for pkg, count in {
                "inter-font": samples // 5,
                "ttf-foo": 1_000 * (month > "2026-08"),
            }.items()
            if count
        ],
    }
    return out


def flat(recs: dict[str, list[Observation | Relation]]) -> list[Observation | Relation]:
    return [r for rows in recs.values() for r in rows]


# --- resolving keys -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ns", "key", "expected"),
    [
        # Known answer: Source Sans Pro -> Source Sans 3, in every namespace that has the old name.
        ("brew-cask", "font-source-sans-pro", ("source-sans-3", "rename", "")),
        ("npm", "@fontsource/source-sans-pro", ("source-sans-3", "rename", "")),
        ("fot-name", "Source Sans Pro", ("source-sans-3", "rename", "")),
        # Known answer: Sauce Code Pro (the Nerd build's name) -> Source Code Pro.
        ("brew-cask", "font-sauce-code-pro-nerd-font", ("source-code-pro", "build", "nerd")),
        ("gh-asset", "SourceCodePro", ("source-code-pro", "build", "nerd")),
        # Known answer: Roboto Slab keys reach Roboto Slab, never Roboto.
        ("brew-cask", "font-roboto-slab", ("roboto-slab", "direct", "")),
        ("fot-name", "Roboto Slab", ("roboto-slab", "direct", "")),
        # A renamed family keeps its id: old and new names reach the same one.
        ("gf-family", "Source Sans 3", ("source-sans-3", "direct", "")),
    ],
)
def test_known_answers(ns: str, key: str, expected: tuple[str, str, str]) -> None:
    assert resolve(SourceKey(ns, key), IDX) == expected


def test_roboto_slab_gets_no_roboto_counts() -> None:
    mapped, unmatched = map_records(flat(scenario()), IDX)
    totals: dict[str, float] = defaultdict(float)
    for m in mapped:
        rec = m.record
        if (
            isinstance(rec, Observation)
            and rec.source == "homebrew_analytics"
            and rec.series == "365d"
            and rec.value is not None
            and m.family_id
        ):
            totals[m.family_id] += rec.value
    assert totals["roboto"] == 20_000
    assert totals["roboto-slab"] == 3_000
    assert SourceKey("brew-cask", "font-roboto-flex") in {u.key for u in unmatched}


@pytest.mark.parametrize(
    ("ns", "key"),
    [
        ("brew-cask", "font-roboto-flex"),  # longer than an indexed key
        ("brew-cask", "font-robot"),  # shorter
        ("npm", "@fontsource/inter-tight"),
        ("gf-family", "Inter Tight"),
        ("gf-family", "Roboto Mono"),
        ("gf-family", "Roboto Slab Bold"),
        ("fs-id", "inter-variable"),
    ],
)
def test_never_by_prefix(ns: str, key: str) -> None:
    assert resolve(SourceKey(ns, key), IDX) is None


@pytest.mark.parametrize(
    ("idx_name", "key"),
    [("Fira Sans", "Fira Code"), ("Noto Sans", "Noto Sans JP"), ("Inter", "Inter Tight")],
)
def test_sibling_names_never_match_each_other(idx_name: str, key: str) -> None:
    idx = index_of([entry("gf-family", idx_name, "x")])
    assert resolve(SourceKey("gf-family", key), idx) is None
    assert resolve(SourceKey("gf-family", idx_name), idx) == ("x", "direct", "")


@pytest.mark.parametrize(
    "spelling",
    [
        "Source Sans Pro",
        "source-sans-pro",
        "SOURCE_SANS_PRO",
        "Source\u00a0Sans\u00adPro",  # no-break space, soft hyphen
        FULLWIDTH,  # fullwidth letters
        "Source\u2013Sans Pro",  # en dash
    ],
)
def test_exact_match_after_match_key(spelling: str) -> None:
    assert resolve(SourceKey("fot-name", spelling), IDX) == ("source-sans-3", "rename", "")


def test_accents_are_kept() -> None:
    idx = index_of([entry("fot-name", "Łódź Sans", "lodz-sans")])
    assert resolve(SourceKey("fot-name", "łódź sans"), idx) is not None
    assert resolve(SourceKey("fot-name", "Lodz Sans"), idx) is None


def test_namespace_is_part_of_the_key() -> None:
    assert resolve(SourceKey("fs-id", "inter"), IDX) == ("inter", "direct", "")
    assert resolve(SourceKey("brew-cask", "inter"), IDX) is None
    assert resolve(SourceKey("gh-asset", "font-inter"), IDX) is None


def test_ineligible_rows_resolve_without_a_family() -> None:
    arial = obs("fot", "fot-name", "Arial", 100, series="2026-W39", unit="sites")
    mapped, unmatched = map_records([arial], IDX)
    assert mapped == [Mapped(arial, None, "ineligible", "proprietary")]
    assert unmatched == []


def test_blocking_rows_leave_the_key_unmatched() -> None:
    tight = obs("fot", "fot-name", "Inter Tight", 12, series="2026-W39", unit="sites")
    mapped, unmatched = map_records([tight], IDX)
    assert mapped == []
    assert unmatched == [Unmatched("fot", tight.key, 12.0, "sites")]


def test_malformed_index_entries_are_refused() -> None:
    bad = {
        ("npm", "Upper"): ("inter", "direct", ""),
        ("npm", "a"): ("inter", "ineligible", "icon"),
        ("npm", "b"): ("", "rename", ""),
        ("npm", "c"): ("inter", "guess", ""),
    }
    problems = mapping.index_problems(bad)
    assert len(problems) == 4
    for key in ("a", "b", "c"):
        with pytest.raises(ValueError, match="malformed"):
            resolve(SourceKey("npm", key), bad)
    assert mapping.index_problems(IDX) == []


def test_relations_map_their_object_and_resolve_their_subject() -> None:
    recs = scenario()["debian"]
    rels = [r for r in recs if isinstance(r, Relation)]
    mapped, unmatched = map_records(rels, IDX)
    assert [(m.record.object.key, m.family_id, m.relation) for m in mapped] == [
        ("fonts-roboto", "roboto", "package")
    ]
    assert {(u.key.key, u.value, u.unit) for u in unmatched} == {
        ("fontconfig", None, "depends"),
        ("libc6", None, "depends"),
    }
    # the other end: a font package that depends on something resolves too
    subject = next(r.subject for r in rels if r.object.key == "libc6")
    assert resolve(subject, IDX) == ("roboto-slab", "package", "")
    assert resolve(DEPENDENT, IDX) is None


def test_map_records_is_order_independent() -> None:
    recs = flat(scenario())
    first = map_records(recs, IDX)
    shuffled = list(reversed(recs[::2])) + recs[1::2]
    assert map_records(shuffled, IDX) == first
    mapped, _ = first
    assert mapped == sorted(mapped, key=mapping._mapped_order)


def test_index_round_trips_through_its_stage_file(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    stageio.dump_stage(paths, "alias_index", entries_of(IDX))
    assert index_of(stageio.load_stage(paths, "alias_index")) == IDX


# --- volumes, ranks and dependents ----------------------------------------------------------------


def test_volumes_in_rate_units() -> None:
    cfg = ranking().sources
    recs = scenario()
    _, hb = mapping.engine_observations(cfg.homebrew, recs)
    vols = mapping.volumes(cfg.homebrew, hb)
    assert vols[SourceKey("brew-cask", "font-inter")] == pytest.approx(50_000)  # 365d only
    _, npm = mapping.engine_observations(cfg.npm_fontsource, recs)
    vols = mapping.volumes(cfg.npm_fontsource, npm)
    assert vols[SourceKey("npm", "@fontsource/inter")] == pytest.approx(100_000)  # a month
    assert SourceKey("npm", "@expo-google-fonts/inter") not in vols  # another engine source
    _, arch = mapping.engine_observations(cfg.arch, recs)
    vols = mapping.volumes(cfg.arch, arch)
    assert vols[SourceKey("arch-pkg", "inter-font")] == pytest.approx(0.2)
    # a package first seen in the last month is averaged over that month only
    assert vols[SourceKey("arch-pkg", "ttf-foo")] == pytest.approx(1_000 / 12_000)
    _, fot = mapping.engine_observations(cfg.fot, recs)
    vols = mapping.volumes(cfg.fot, fot)
    assert vols[SourceKey("fot-name", "Proxima Nova")] == pytest.approx(38)
    assert vols[SourceKey("fot-name", "Inter Tight")] == pytest.approx(6)


def test_arch_uses_only_its_latest_months() -> None:
    src = ranking().sources.arch
    months = [f"{2025 + (m > 12)}-{(m - 1) % 12 + 1:02d}" for m in range(9, 22)]
    assert (len(months), months[0], months[-1]) == (13, "2025-09", "2026-09")
    rows = [
        obs("pkgstats", "arch-pkg", "old", 100 if i == 0 else None, series=mo, unit="share")
        for i, mo in enumerate(months)
    ] + [obs("pkgstats", "arch-pkg", "new", 0.01, series=mo, unit="share") for mo in months]
    vols = mapping.volumes(src, rows)
    assert "old" not in {k.key for k in vols}  # its only value is in the 13th month back
    assert vols[SourceKey("arch-pkg", "new")] == pytest.approx(0.01)


def test_rate_value() -> None:
    week = obs("x", "npm", "a", 70, start=date(2026, 9, 1), end=date(2026, 9, 7))
    assert mapping.rate_value(week, "per_year") == pytest.approx(70 * 365 / 7)
    assert mapping.rate_value(week, "per_month") == pytest.approx(70 * 365 / 7 / 12)
    assert mapping.rate_value(week, "count") == 70
    assert mapping.rate_value(replace(week, value=None), "count") is None
    assert mapping.rate_value(obs("x", "arch-pkg", "a", 5, samples=100), "share") == 0.05
    assert mapping.rate_value(obs("x", "arch-pkg", "a", 5), "share") is None  # no denominator


def test_rates_count_only_the_days_available() -> None:
    """Methodology §3: counts become rates over min(window, days available)."""
    end = YEAR_END
    young = obs("npm", "npm", "a", 6_000, first_seen=(end - timedelta(days=29)).isoformat())
    assert mapping.rate_value(young, "per_month") == pytest.approx(6_000 * (365 / 12) / 30)
    old = obs("npm", "npm", "a", 6_000, first_seen="2020-01-01")  # before the window: no effect
    assert mapping.rate_value(old, "per_month") == pytest.approx(500)
    # a window within a few days of a year or a month is exactly one, as stage "correct" counts
    leap = obs("x", "npm", "a", 3_660, start=date(2027, 10, 1), end=date(2028, 9, 30))
    assert mapping.rate_value(leap, "per_year") == pytest.approx(3_660)
    month = obs("x", "npm", "a", 3_000, start=date(2026, 9, 1), end=date(2026, 9, 30))
    assert mapping.rate_value(month, "per_month") == pytest.approx(3_000)
    # a lifetime counter: the total over the days since the asset was created
    life = obs("x", "gh-asset", "a", 7_300, series="lifetime", start=date(2020, 1, 1), end=RUN)
    made = replace(life, attrs=attrs(published_at=(RUN - timedelta(days=730)).isoformat()))
    assert mapping.rate_value(made, "per_year", lifetime=True) == pytest.approx(3_650)
    seen = replace(life, attrs=attrs(first_seen=(RUN - timedelta(days=365)).isoformat()))
    assert mapping.rate_value(seen, "per_year", lifetime=True) == pytest.approx(7_300)


def test_a_young_package_above_its_floor_is_reported() -> None:
    """A key 30 days old with 6,000 downloads runs at about 6,000 a month, far above
    npm's floor of 1,000: averaged over a whole year it would hide at 500."""
    recs = scenario()
    first = (YEAR_END - timedelta(days=29)).isoformat()
    recs["npm"].append(
        obs(
            "npm",
            "npm",
            "@fontsource/newcomer",
            6_000,
            series="last-year",
            unit="downloads",
            first_seen=first,
        )
    )
    rows, _ = mapping.report_rows(ranking().sources.all(), recs, IDX)
    got = {u.key.key: u.value for u in rows if u.source == "npm_fontsource"}
    assert got["@fontsource/newcomer"] == pytest.approx(6_000 * (365 / 12) / 30)


def test_arch_mean_runs_from_the_first_nonzero_month() -> None:
    src = ranking().sources.arch
    rows = [
        obs("pkgstats", "arch-pkg", "gap", share, series=month, unit="share")
        for month, share in (("2026-07", 0.03), ("2026-09", 0.06))
    ] + [obs("pkgstats", "arch-pkg", "late", 0.0, series="2026-08", unit="share")]
    vols = mapping.volumes(src, rows)
    assert vols[SourceKey("arch-pkg", "gap")] == pytest.approx(0.03)  # (0.03 + 0 + 0.06) / 3
    assert SourceKey("arch-pkg", "late") not in vols  # never above zero


def test_fot_reads_the_last_month_of_weeks_and_its_methods() -> None:
    src = ranking().sources.fot
    week_end = {
        "2026-W30": RUN - timedelta(days=60),
        "2026-W38": RUN - timedelta(7),
        "2026-W39": RUN,
    }
    rows = [
        obs("fot", "fot-name", "Inter", 10, series=w, unit="sites", start=end, end=end)
        for w, end in week_end.items()
    ] + [
        obs("fot", "fot-name", "Inter", 99, series="2026-W39", unit="sites", method="other"),
        obs("fot", "fot-name", "Inter", 5, series="2026-W39", unit="sites", method="static"),
    ]
    _, chosen = mapping.engine_observations(src, {"fot": rows})
    assert len(chosen) == 4  # the "other" method is not counted
    # W30 is outside the last month; (10 + 10 + 5) / 2 weeks
    assert mapping.volumes(src, chosen) == {SourceKey("fot-name", "Inter"): pytest.approx(12.5)}


def test_prereleases_and_excluded_assets_are_not_read() -> None:
    cfg = ranking().sources
    life = {"series": "lifetime", "unit": "downloads"}
    recs: dict[str, list[Observation | Relation]] = {
        "github_releases": [
            obs("github_releases", "gh-asset", "o/r/Font.zip", 10, release="v1", **life),
            obs(
                "github_releases",
                "gh-asset",
                "o/r/Font.zip",
                99,
                release="v2-rc",
                prerelease=True,
                **life,
            ),
        ],
        "nerd_releases": [
            obs("nerd_releases", "gh-asset", "v3.4.0/FontPatcher.zip", 99, **life),
            obs(
                "nerd_releases",
                "gh-asset",
                "x",
                99,
                asset="SymbolsOnly/NerdFontsSymbolsOnly.zip",
                **life,
            ),
            obs("nerd_releases", "gh-asset", "v3.4.0/Hack.zip", 10, **life),
        ],
    }
    _, gh = mapping.engine_observations(cfg.github, recs)
    assert [o.value for o in gh] == [10]
    _, nerd = mapping.engine_observations(cfg.nerd, recs)
    assert [o.key.key for o in nerd] == ["v3.4.0/Hack.zip"]


def test_selector_scopes_and_excluded_assets() -> None:
    cfg = ranking().sources
    fs, expo = mapping.selector(cfg.npm_fontsource), mapping.selector(cfg.npm_expo)
    assert fs(SourceKey("npm", "@fontsource/inter"))
    assert fs(SourceKey("npm", "@fontsource-variable/inter"))
    assert fs(SourceKey("npm", "pkg:npm/%40fontsource/inter"))
    assert not fs(SourceKey("npm", "@expo-google-fonts/inter"))
    assert not fs(SourceKey("npm", "fontsource-inter"))  # unscoped legacy id
    assert expo(SourceKey("npm", "@expo-google-fonts/inter"))
    nerd = mapping.selector(cfg.nerd)
    assert not nerd(SourceKey("gh-asset", "FontPatcher.zip"))
    assert not nerd(SourceKey("gh-asset", "NerdFontsSymbolsOnly.tar.xz"))
    assert nerd(SourceKey("gh-asset", "JetBrainsMono.zip"))


def test_series_matches() -> None:
    assert mapping.series_matches("YYYY-MM", "2026-08")
    assert not mapping.series_matches("YYYY-MM", "2026-W38")
    assert mapping.series_matches("YYYY-Www", "2026-W38")
    assert mapping.series_matches("365d", "365d")
    assert not mapping.series_matches("365d", "90d")


def test_dependents_are_not_font_keys() -> None:
    rels = [r for r in flat(scenario()) if isinstance(r, Relation)]
    deps = mapping.dependents(rels, IDX)
    # gnome-shell pulls a resolved font in; fonts-unknown only depends on a library
    assert deps == frozenset({DEPENDENT})
    chain = [*rels, rel("debian", SourceKey("deb-pkg", "meta"), DEPENDENT)]
    assert DEPENDENT not in mapping.dependents(chain, IDX)  # itself pulled in: kept


@pytest.mark.parametrize(
    "name", ["fonts-roboto-extra", "ttf-foo-nerd", "otf-foo", "ttc-foo", "foo-fonts", "xfonts-foo"]
)
def test_a_font_package_that_pulls_in_another_font_is_still_a_font_key(name: str) -> None:
    """fonts-dejavu-extra depends on fonts-dejavu-core: it is still a font package."""
    extra = SourceKey("deb-pkg", name)
    rels = [rel("debian", extra, SourceKey("deb-pkg", "fonts-roboto"))]
    assert mapping.dependents(rels, IDX) == frozenset()
    recs: dict[str, list[Observation | Relation]] = {
        "debian": [*rels, obs("debian", "deb-pkg", name, 5_000, series="inst", start=YEAR_END)]
    }
    rows, _ = mapping.report_rows(ranking().sources.all(), recs, IDX)
    assert [(u.source, u.key.key, u.rank) for u in rows] == [("debian", name, 1)]


def test_system_packages_take_no_rank() -> None:
    """pkgstats counts every package: glibc and firefox must neither be listed nor push
    fonts down; the package data's font signals keep oddly named font packages."""
    arch = ranking().sources.arch
    shares = {
        "glibc": 0.99,  # no edge, no font name: not a font key
        "firefox": 0.60,  # depends on the virtual ttf-font: not a font key
        "gtk3": 0.70,  # a dependency of firefox: not a font key
        "inter-font": 0.10,  # resolves
        "croscore": 0.05,  # provides ttf-font: a font package
        "monocraft": 0.03,  # in the nerd-fonts group
        "ttf-lonely": 0.02,  # a font package name
    }
    rows = [
        obs("pkgstats", "arch-pkg", name, share, series="2026-09", unit="share")
        for name, share in shares.items()
    ]
    pkg = {name: SourceKey("arch-pkg", name) for name in shares} | {
        v: SourceKey("arch-pkg", v) for v in ("ttf-font", "nerd-fonts")
    }
    recs: dict[str, list[Observation | Relation]] = {
        "pkgstats": list(rows),
        "arch_repos": [
            rel("arch_repos", pkg["firefox"], pkg["ttf-font"]),
            rel("arch_repos", pkg["firefox"], pkg["gtk3"]),
            rel("arch_repos", pkg["croscore"], pkg["ttf-font"], "provides"),
            rel("arch_repos", pkg["monocraft"], pkg["nerd-fonts"], "group"),
        ],
    }
    got, _ = mapping.report_rows({"arch": arch}, recs, IDX)
    assert [(u.key.key, u.rank) for u in got] == [
        ("croscore", 2),
        ("monocraft", 3),
        ("ttf-lonely", 4),
    ]
    is_font = mapping.font_key_filter(mapping._relations(recs), IDX)
    assert {n for n in shares if is_font(pkg[n])} == {
        "inter-font",
        "croscore",
        "monocraft",
        "ttf-lonely",
    }
    assert is_font(SourceKey("npm", "glibc"))  # only distro packages are filtered


@pytest.mark.parametrize("name", ["gnome-shell", "fontconfig", "libreoffice-core"])
def test_non_font_names_that_pull_fonts_in_are_dependents(name: str) -> None:
    rels = [rel("debian", SourceKey("deb-pkg", name), SourceKey("deb-pkg", "fonts-roboto"))]
    assert mapping.dependents(rels, IDX) == frozenset({SourceKey("deb-pkg", name)})


def test_ranks_share_ties() -> None:
    keys = [SourceKey("npm", k) for k in "abcd"]
    assert mapping.ranks(dict(zip(keys, [5.0, 9.0, 5.0, 1.0], strict=True))) == {
        keys[1]: 1,
        keys[0]: 2,
        keys[2]: 2,
        keys[3]: 4,
    }


def test_google_falls_back_to_popularity() -> None:
    src = ranking().sources.google
    popularity = [
        obs("google_metadata", "gf-family", name, rank, series="popularity", unit="rank")
        for name, rank in (("Inter", 3), ("Roboto", 1))
    ]
    recs: dict[str, list[Observation | Relation]] = {"google_metadata": list(popularity)}
    collector, rows = mapping.engine_observations(src, recs)
    assert collector == "google_metadata"
    vols = mapping.volumes(src, rows)
    assert mapping.ranks(vols) == {  # a rank is 1 for the top: smaller is larger
        SourceKey("gf-family", "Roboto"): 1,
        SourceKey("gf-family", "Inter"): 2,
    }
    recs["gf_stats"] = [obs("gf_stats", "gf-family", "Inter", 9, series="year", unit="views")]
    assert mapping.engine_observations(src, recs)[0] == "gf_stats"


# --- the report -----------------------------------------------------------------------------------


def report_rows() -> tuple[list[Unmatched], dict[str, float]]:
    return mapping.report_rows(ranking().sources.all(), scenario(), IDX)


def test_report_rows() -> None:
    rows, floors = report_rows()
    got = {(u.source, u.key.key): (u.rank, u.value) for u in rows}
    assert got == {
        ("homebrew", "font-mystery"): (5, pytest.approx(5_000)),
        ("homebrew", "font-roboto-flex"): (8, pytest.approx(2_500)),
        ("debian", "fonts-unknown"): (3, 400.0),  # gnome-shell is a dependent: no rank
        ("arch", "ttf-foo"): (2, pytest.approx(1 / 12)),
        ("npm_fontsource", "@fontsource/mystery"): (3, pytest.approx(5_000)),
        ("npm_expo", "@expo-google-fonts/inter"): (1, pytest.approx(2_000)),
        ("fot", "Proxima Nova"): (3, None),  # ranks only (ruling T4)
        ("fot", "Inter Tight"): (5, None),  # a sibling row is not a match
        ("google", "Brand New Font"): (5, None),  # ranks only (ruling T2)
    }
    assert floors == {
        "homebrew": 80.0,
        "arch": 0.003,
        "debian": 100.0,
        "fot": 3.0,
        "google": 0.0,
        "npm_fontsource": 1000.0,
        "npm_expo": 1000.0,
    }


def test_report_lists_keys_above_the_floor_largest_first() -> None:
    rows = [
        Unmatched("homebrew", SourceKey("brew-cask", "font-b"), 500.0, "installs a year", 9),
        Unmatched("homebrew", SourceKey("brew-cask", "font-a"), 1_500.0, "installs a year", 3),
        Unmatched("homebrew", SourceKey("brew-cask", "font-low"), 79.0, "installs a year", 50),
        Unmatched("homebrew", SourceKey("brew-cask", "font-b"), 400.0, "installs a year", 11),
        Unmatched("github", SourceKey("gh-asset", "Zero.zip"), 0.0, "downloads a year", 7),
        Unmatched("other", SourceKey("npm", "x"), 1e9, "downloads", 1),
        Unmatched("arch", SourceKey("arch-pkg", "ttf|pipe"), 0.0123, "share", 4),
        Unmatched("homebrew", SourceKey("brew-cask", "font-relation-only"), None, "depends"),
    ]
    text = unmatched_report(rows, {"homebrew": 80.0, "arch": 0.003, "github": 0.0})
    assert text == unmatched_report(
        list(reversed(rows)), {"github": 0.0, "arch": 0.003, "homebrew": 80.0}
    )
    lines = text.splitlines()
    body = [line for line in lines if line.startswith("| ") and "`" in line]
    assert body == [
        "| 4 | `ttf\\|pipe` | arch-pkg | 1.23% |",
        "| 3 | `font-a` | brew-cask | 1,500 |",
        "| 9 | `font-b` | brew-cask | 500 |",
    ]
    assert "| homebrew | 80 installs a year | 2 |" in lines
    assert "| arch | 0.3% | 1 |" in lines
    assert "| github | 0 | 0 |" in lines
    assert "## other" not in text
    assert "## github" not in text
    assert "font-low" not in text
    assert "font-relation-only" not in text
    assert lines[0] == "# Unmatched source keys"


def test_report_hides_values_of_google_and_fot() -> None:
    rows, floors = report_rows()
    text = unmatched_report(rows, floors)
    for section in ("google", "fot"):
        part = text.split(f"## {section}\n", 1)[1].split("\n## ", 1)[0]
        cells = [line.split(" | ")[-1] for line in part.splitlines() if "`" in line]
        assert cells, part
        assert all(c == "rank only |" for c in cells), part
    assert "70,123,456" not in text
    assert "7.01e" not in text
    assert "## homebrew" in text
    assert "5,000" in text  # open sources show values (ruling T1)


def test_report_keeps_odd_keys_on_one_visible_line() -> None:
    rows = [
        Unmatched("fot", SourceKey("fot-name", "Bad\nName"), None, "sites", 1),
        Unmatched("fot", SourceKey("fot-name", "No\u00a0Break"), None, "sites", 2),
    ]
    text = unmatched_report(rows, {"fot": 3.0})
    assert "| 1 | `Bad\\u000aName` | fot-name | rank only |" in text.splitlines()
    assert "| 2 | `No\\u00a0Break` | fot-name | rank only |" in text.splitlines()


def test_empty_report() -> None:
    text = unmatched_report([], {})
    assert "(no source)" in text
    assert text == unmatched_report([], {})


# --- frames and evidence states ---------------------------------------------------------------------


def frames() -> dict[str, Frame]:
    return mapping.frames(ranking().sources.all(), UNIVERSE, scenario(), IDX)


def test_package_frames() -> None:
    f = frames()
    # Homebrew: every family with a cask (universe keys) or a mapped analytics row
    assert f["homebrew"].families == (
        "hack",
        "inter",
        "jetbrains-mono",
        "roboto",
        "roboto-slab",
        "source-code-pro",
        "source-sans-3",
    )
    assert f["homebrew"].basis == "packaged"
    assert f["homebrew"].namespaces == ("brew-cask",)
    # Debian: roboto-slab has no popcon row, but its package appears in a relation
    assert f["debian"].families == ("inter", "roboto", "roboto-slab")
    assert f["arch"].families == ("inter",)
    assert f["npm_fontsource"].families == ("inter", "source-sans-3")
    assert f["npm_expo"].families == ()  # no alias row for the Expo package yet


def test_list_and_web_frames() -> None:
    f = frames()
    # Google's list: Google families only; the dropped icon family is never in a frame
    assert f["google"].basis == "listed"
    assert f["google"].families == (
        "inter",
        "jetbrains-mono",
        "roboto",
        "roboto-slab",
        "source-code-pro",
        "source-sans-3",
    )
    # Fonts Over Time (gap G11): every web-servable family: Google, Fontsource, foundries
    assert f["fot"].basis == "web_servable"
    assert "ostrich-sans" in f["fot"]
    assert "hack" not in f["fot"]
    assert "material-icons" not in f["fot"]
    assert "material-icons" not in f["google"]
    recs = scenario()
    recs["fot"].append(obs("fot", "fot-name", "Hack", 5, series="2026-W39", unit="sites"))
    idx = dict(IDX) | {("fot-name", "hack"): ("hack", "direct", "")}
    assert "hack" in mapping.frames(ranking().sources.all(), UNIVERSE, recs, idx)["fot"]


def test_frames_skip_sources_without_rows() -> None:
    f = frames()
    assert set(f) == {"homebrew", "arch", "debian", "fot", "google", "npm_fontsource", "npm_expo"}


@pytest.mark.parametrize(
    ("family", "value", "exposed", "expected"),
    [
        ("inter", 5_000.0, True, ("observed", None)),
        ("inter", 60.0, True, ("observed", None)),  # at the floor
        ("inter", 59.0, True, ("censored", "below_floor")),
        ("inter", None, True, ("censored", "no_value")),
        ("inter", 5_000.0, False, ("too_new", None)),
        ("ostrich-sans", 5_000.0, True, ("not_covered", "no_package")),
        ("ostrich-sans", None, False, ("not_covered", "no_package")),
    ],
)
def test_evidence_states_package_source(
    family: str, value: float | None, exposed: bool, expected: tuple[str, str | None]
) -> None:
    frame = frames()["homebrew"]
    assert evidence_state(frame, family, value, 60.0, exposed=exposed) == expected


def test_evidence_states_list_sources() -> None:
    f = frames()
    assert evidence_state(f["google"], "hack", 1.0, 0.0) == ("not_covered", "outside_frame")
    assert evidence_state(f["fot"], "hack", None, 3.0) == ("not_covered", "outside_frame")
    assert evidence_state(f["fot"], "ostrich-sans", None, 3.0) == ("censored", "no_value")


def test_frame_membership() -> None:
    frame = Frame("packaged", ("npm",), ("a", "c", "e"))
    assert "c" in frame
    assert [x in frame for x in ("a", "b", "e", "z", 3)] == [True, False, True, False, False]


# --- the stage --------------------------------------------------------------------------------------


def write_build(root: Path, recs: dict[str, list[Observation | Relation]]) -> Paths:
    paths = Paths.for_root(root)
    stageio.dump_stage(paths, "alias_index", entries_of(IDX))
    stageio.dump_stage(paths, "universe", UNIVERSE)
    for collector, rows in recs.items():
        write_jsonl(rows, paths.records / f"{collector}.jsonl")
    return paths


def context(paths: Paths) -> Any:
    from tff_catalog.stages import StageContext

    none = cast(Any, None)
    config = Config(
        ranking=ranking(),
        licenses=none,
        license_aliases=none,
        preinstalled=none,
        foundries=none,
        site=none,
        sources={},
    )
    return StageContext(
        paths=paths,
        config=config,
        state=State(),
        run_date=RUN,
        store=None,
        fetcher=None,
        log=logging.getLogger("test_mapping"),
    )


def outputs(paths: Paths) -> dict[str, bytes]:
    return {
        name: path.read_bytes()
        for name, path in {
            "mapped": stageio.stage_path(paths, "mapped"),
            "report": paths.build / "unmatched.md",
        }.items()
    }


def test_run_writes_mapped_and_report(tmp_path: Path) -> None:
    recs = scenario()
    baseline = obs("nerd_releases", "gh-asset", "SourceCodePro", 100, series="lifetime")
    paths = write_build(tmp_path / "a", recs)
    write_jsonl([baseline], paths.records / "nerd_releases@2026-09-03.jsonl")
    mapping.run(context(paths))

    mapped = stageio.load_stage(paths, "mapped")
    assert mapped == map_records([*flat(recs), baseline], IDX)[0]
    assert any(m.record == baseline for m in mapped)  # baselines are mapped too
    assert not (paths.stage / "frames.json").exists()  # not a stage file until "correct" reads it
    rows, floors = report_rows()
    report = outputs(paths)["report"].decode()
    assert report == unmatched_report(rows, floors)
    assert "## nerd" not in report  # a baseline alone is never reported

    other = write_build(tmp_path / "b", dict(reversed(list(recs.items()))))
    write_jsonl([baseline], other.records / "nerd_releases@2026-09-03.jsonl")
    mapping.run(context(other))
    assert outputs(other) == outputs(paths)


def test_gate_u_asks_about_each_unmatched_key_in_a_top_200_once(tmp_path: Path) -> None:
    from tff_catalog import reviews

    key = SourceKey("npm", "@fontsource/mystery")
    ranked = [
        mapping.Unmatched("npm_fontsource", key, 5_000.0, "downloads a month", 7),
        mapping.Unmatched("ecosystems", key, 12.0, "dependents", 3),
        mapping.Unmatched("google", SourceKey("gf-family", "Secret"), None, "views", 9),
        mapping.Unmatched("github", SourceKey("gh-asset", "o/r/x.zip"), 1.0, "downloads", 201),
    ]
    asked = mapping.queue_questions(ranked)
    assert [q.text.split("`")[1] for q in asked] == ["npm:@fontsource/mystery", "gf-family:Secret"]
    assert "ecosystems rank 3, 12" in asked[0].text  # its best rank
    assert "google rank 9)" in asked[1].text  # ranks only: publish_raw is false
    assert all(q.gate == "U" and q.recommended is None for q in asked)
    assert reviews.pins(asked[0]) == {"key": "npm:@fontsource/mystery"}

    paths = write_build(tmp_path, scenario())
    mapping.run(context(paths))
    got = reviews.questions(paths, "U")
    assert [q.id for q in got] == [q["id"] for q in jsonio.load(paths.queues / "unmatched.json")]
    assert got  # the scenario has unmatched keys


def test_run_refuses_a_malformed_index(tmp_path: Path) -> None:
    paths = write_build(tmp_path, scenario())
    bad = [*entries_of(IDX), IndexEntry("npm", "Not Normalised", "inter", "direct")]
    stageio.dump_stage(paths, "alias_index", bad)
    with pytest.raises(ValueError, match="match_key form"):
        mapping.run(context(paths))


def test_check_unmatched(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = write_build(tmp_path, scenario())
    ctx = context(paths)
    assert mapping.cmd_check_unmatched(ctx, top=200) == 1
    out = capsys.readouterr().out
    assert "homebrew: 3 unmatched key(s) in its top 200:" in out
    assert "#5 brew-cask:font-mystery\n" in out
    # the literal "Done when": a key under the floor counts too, and says why it is not listed
    assert "#9 brew-cask:font-tiny (under the floor; not in unmatched.md)" in out
    assert "gnome-shell" not in out
    assert "Arial" not in out
    assert mapping.cmd_check_unmatched(ctx, top=1) == 1  # npm_expo's only key is unmatched
    # without it, every source's top key resolves (Arial is ineligible, which counts)
    capsys.readouterr()
    recs = scenario()
    recs["npm"] = [r for r in recs["npm"] if "expo" not in r.key.key]
    paths = write_build(tmp_path / "b", recs)
    assert mapping.cmd_check_unmatched(context(paths), top=1) == 0
    assert "no source has an unmatched key in its top 1" in capsys.readouterr().out


def test_bundle_keys_count_as_matched(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A bundle row of data/aliases.csv credits its families in stage "correct", so its
    key is matched: it leaves unmatched.md and --check-unmatched."""
    paths = write_build(tmp_path, scenario())
    paths.aliases_csv.parent.mkdir(parents=True, exist_ok=True)
    paths.aliases_csv.write_text(
        "alias,ns,family_id,relation,detail,source,first_seen,reviewed_by\n"
        + "".join(
            f"font-mystery,brew-cask,{fid},bundle,,synth,2026-09-01,owner\n"
            for fid in ("inter", "roboto")
        ),
        encoding="utf-8",
    )
    assert mapping.bundle_rows(paths.aliases_csv) == {
        ("brew-cask", match_key("font-mystery")): ("inter", "roboto")
    }
    mapping.run(context(paths))
    assert "font-mystery" not in (paths.build / "unmatched.md").read_text(encoding="utf-8")
    assert mapping.cmd_check_unmatched(context(paths), top=200) == 1
    out = capsys.readouterr().out
    assert "homebrew: 2 unmatched key(s) in its top 200:" in out
    assert "font-mystery" not in out


def test_missing_inputs_name_the_stage_to_run(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    with pytest.raises(FileNotFoundError, match="run stage 'aliases' first"):
        mapping.run(context(paths))
    stageio.dump_stage(paths, "alias_index", entries_of(IDX))
    with pytest.raises(FileNotFoundError, match="run stage 'parse' first"):
        mapping.run(context(paths))
    # a run that stops on a missing input writes nothing
    assert not stageio.stage_path(paths, "mapped").exists()
    assert not (paths.build / "unmatched.md").exists()
