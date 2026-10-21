"""The measurement model's terms for the Projects and Developers & apps ranks.

Each project source is a signal of one latent popularity (engine/measure.py), and its term
follows what the source counts (the owner's rulings of 2026-10-01; the private workbench
tff-stats' ``project.py`` is the reference, and the parity test holds the port to it):

- **Fonts Over Time:** two negative-binomial terms, startup homepages and the other
  homepages (q4_fot_two_terms), each with the sites crawled as its exposure and no bulk: a
  homepage uses a font or doesn't. A month's value is the mean of its weekly crawls, a
  missing week counting 0, as the engine averages them (corrections ``_count_weekly``); the
  exposure is the weekly mean of the crawl's site counts.
- **Web Almanac:** a log-normal on a top list with no bulk. A family below the list's
  smallest entry is the interval [0, smallest); a family whose count folds in width cuts
  (the ``parent_merge`` flag) is [0, y], an upper bound only (q24_almanac_merged_parent).
- **Google views, npm (Fontsource), Expo:** log-normal traffic with a bulk every package
  gets, scaled by the share of the year since the package first appeared, at most one
  (q7_age_scaled_bulk_web); the counts themselves are never scaled.
- **ecosyste.ms dependents:** a negative binomial, one bulk per package.
- **jsDelivr hits:** log-normal, one bulk per package; a family with no hits is not covered
  (q29_jsdelivr_zero).

A source's frame is the fonts its Project terms count (observed or censored in
``terms.json``), less the families license check L3 failed; a font in the 60-day grace, or
one the source doesn't cover, has no term. Developers & apps is npm (Fontsource),
ecosyste.ms and Expo on those frames (q15_views_meanwhile; Flutter stays off,
q26_project_frames).

Pure functions over plain data: ``tff_catalog.modelinputs`` reads the stage files.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

from tff_catalog.engine import measure

COUNTED = ("observed", "censored")  # the term states a model frame takes
YEAR_DAYS = 365
STARTUPS = "startups"  # Fonts Over Time's category of startup homepages
PARENT_MERGE = "parent_merge"  # corrections' flag: a Web Almanac parent with width cuts

FOT_STARTUPS, FOT_OTHERS = "Fonts Over Time (startups)", "Fonts Over Time (others)"
ALMANAC, GOOGLE = "Web Almanac", "Google views"
NPM_FONTSOURCE, ECOSYSTEMS = "npm (Fontsource)", "ecosyste.ms dependents"
JSDELIVR, EXPO = "jsDelivr hits", "Expo"
PROJECT_TERMS = (
    FOT_STARTUPS,
    FOT_OTHERS,
    ALMANAC,
    GOOGLE,
    NPM_FONTSOURCE,
    ECOSYSTEMS,
    JSDELIVR,
    EXPO,
)
DEV_APPS_TERMS = (NPM_FONTSOURCE, ECOSYSTEMS, EXPO)
NPM_PREFIXES = {"npm_fontsource": "@fontsource", "npm_expo": "@expo-google-fonts"}


@dataclass(frozen=True)
class FotRow:
    """One Fonts Over Time observation: a name's sites in one category and method, one week."""

    week: str
    end: date
    key: str
    category: str
    method: str
    sites: float
    category_sites: int


def frame(
    entries: Mapping[str, Mapping[str, Any]], excluded: frozenset[str] = frozenset()
) -> dict[str, Mapping[str, Any]]:
    """A source's frame: its Project term entries ({id: entry}) that count, by id, less
    ``excluded`` (license check L3's failures)."""
    return {
        fid: entry
        for fid, entry in sorted(entries.items())
        if entry["state"] in COUNTED and fid not in excluded
    }


def age_share(first_seen: date | None, end: date, window_days: int = YEAR_DAYS) -> float:
    """The share of the window, ending on ``end``, that a package has existed, between 0 and 1;
    1 when unknown. ``end`` is the source's data date, as for the 60-day grace (b8b): a
    young package is not marked down for bulk it could not have collected (q7)."""
    if first_seen is None:
        return 1.0
    return min(max((end - first_seen).days / window_days, 0.0), 1.0)


def month_weeks(rows: Sequence[FotRow]) -> list[str]:
    """The weeks of the month a run counts: those ending within 31 days of the newest
    crawl, as corrections ``_count_weekly`` takes them."""
    data_end = max(r.end for r in rows)
    week_end: dict[str, date] = {}
    for r in rows:
        week_end[r.week] = max(week_end.get(r.week, r.end), r.end)
    return sorted(w for w, end in week_end.items() if end > data_end - timedelta(days=31))


def fot_terms(
    rows: Iterable[FotRow], links: Mapping[str, str], frame_ids: Sequence[str]
) -> list[measure.CountTerm]:
    """Startup homepages and the other homepages, two negative-binomial terms on the
    frame's fonts: each font's mean weekly sites (0 when it has none), with the mean weekly
    sites crawled as exposure and no bulk."""
    rows = list(rows)
    weeks = month_weeks(rows)
    in_month = [r for r in rows if r.week in set(weeks)]
    exposure: dict[str, float] = {}
    sites: dict[str, dict[str, float]] = {
        STARTUPS: defaultdict(float),
        "others": defaultdict(float),
    }
    for group in (STARTUPS, "others"):
        crawled = {
            (r.week, r.category, r.method): r.category_sites
            for r in in_month
            if (r.category == STARTUPS) == (group == STARTUPS)
        }
        exposure[group] = math.fsum(crawled.values()) / len(weeks)
    weekly: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in in_month:
        fid = links.get(r.key)
        if fid is not None:
            group = STARTUPS if r.category == STARTUPS else "others"
            weekly[(group, fid)][r.week] += r.sites
    for (group, fid), by_week in weekly.items():
        sites[group][fid] = math.fsum(by_week.values()) / len(weeks)
    ids = np.array(sorted(frame_ids))
    out = []
    for group, name in ((STARTUPS, FOT_STARTUPS), ("others", FOT_OTHERS)):
        y = np.array([sites[group].get(fid, 0.0) for fid in ids], float)
        out.append(
            measure.CountTerm(
                name,
                ids,
                y,
                np.zeros(len(ids)),  # no bulk
                exposure=np.full(len(ids), exposure[group]),
            )
        )
    return out


def almanac_term(entries: Mapping[str, Mapping[str, Any]]) -> measure.CountTerm:
    """Web Almanac on its frame: a listed family's count, an unlisted one below the list's
    smallest entry [0, smallest), a merged parent [0, y]."""
    ids = np.array(sorted(entries))
    listed = [e["value"] for e in entries.values() if e["state"] == "observed"]
    smallest = float(min(listed))
    y, hi = [], []
    for fid in ids:
        entry = entries[fid]
        top = float(entry["value"]) if entry["state"] == "observed" else smallest
        merged = PARENT_MERGE in entry.get("flags", ())
        y.append(0.0 if merged or entry["state"] != "observed" else top)
        hi.append(top)
    return measure.CountTerm(
        ALMANAC,
        ids,
        np.array(y, float),
        np.zeros(len(ids)),
        y_hi=np.array(hi, float),
        dist="lognormal",
    )


def traffic_term(
    name: str,
    packages: Iterable[tuple[str, str, float, date | None]],
    frame_ids: Sequence[str],
    run_date: date,
    *,
    age: bool = True,
    dist: str = "lognormal",
    drop_zero: bool = False,
) -> measure.CountTerm:
    """A traffic source on its frame: each font's counts summed over its packages
    (key, id, count, first seen), each package bringing the bulk scaled by its age (or a
    whole bulk each, without ``age``). Frame fonts with no package have no term."""
    totals: dict[str, list[float]] = defaultdict(list)
    shares: dict[str, list[float]] = defaultdict(list)
    for _key, fid, count, first_seen in packages:
        totals[fid].append(count)
        shares[fid].append(age_share(first_seen, run_date) if age else 1.0)
    keep = set(frame_ids)
    ids = [fid for fid in sorted(totals) if fid in keep]
    y = {fid: math.fsum(totals[fid]) for fid in ids}
    if drop_zero:
        ids = [fid for fid in ids if y[fid] > 0]
    return measure.CountTerm(
        name,
        np.array(ids),
        np.array([y[fid] for fid in ids], float),
        np.array([math.fsum(shares[fid]) for fid in ids], float),
        dist=dist,
    )


def google_term(
    views: Iterable[tuple[str, str, float]],
    added: Mapping[str, date | None],
    frame_ids: Sequence[str],
    run_date: date,
) -> measure.CountTerm:
    """Google views on its frame: each font's year of views summed over its keys, with one
    bulk scaled by the share of the year since the family was added to Google Fonts."""
    totals: dict[str, list[float]] = defaultdict(list)
    for _key, fid, count in views:
        totals[fid].append(count)
    keep = set(frame_ids)
    ids = [fid for fid in sorted(totals) if fid in keep]
    return measure.CountTerm(
        GOOGLE,
        np.array(ids),
        np.array([math.fsum(totals[fid]) for fid in ids], float),
        np.array([age_share(added.get(fid), run_date) for fid in ids], float),
        dist="lognormal",
    )


def no_bulk(model: measure.Mixed) -> dict[int, float]:
    """``fit_mixed(fixed=...)`` for the terms with no bulk (every n is 0): their baseline is
    not identified, so it is held at a negligible value."""
    return {
        at: measure.LOG_B_MIN
        for term, at in zip(model.counts, model.offsets(), strict=True)
        if term.n2d.sum() == 0
    }


@dataclass(frozen=True)
class ProjectData:
    """What the Project terms read from a run's stage (``modelinputs.project_data``)."""

    run_date: date
    frames: Mapping[str, Mapping[str, Mapping[str, Any]]]  # source -> {id: entry}, counted
    fot: tuple[FotRow, ...]
    fot_links: Mapping[str, str]  # Fonts Over Time key -> family id
    google: tuple[tuple[str, str, float], ...]  # (key, id, a year's views)
    google_added: Mapping[str, date | None]  # id -> the day Google Fonts added the family
    npm: Mapping[str, tuple[tuple[str, str, float, date | None], ...]]  # source -> packages
    ecosystems: tuple[tuple[str, str, float, date | None], ...]
    jsdelivr: tuple[tuple[str, str, float, date | None], ...]
    # source -> its data date, where each package's age share is measured to (q7); a source
    # missing here is measured to the run date
    data_dates: Mapping[str, date] = field(default_factory=dict)


def project_terms(data: ProjectData) -> list[measure.CountTerm]:
    """The eight Project terms, in the reference's order."""
    f, day = data.frames, data.run_date
    google_end = data.data_dates.get("gf_stats", day)
    npm_end = data.data_dates.get("npm", day)
    return [
        *fot_terms(data.fot, data.fot_links, list(f["fot"])),
        almanac_term(f["almanac"]),
        google_term(data.google, data.google_added, list(f["google"]), google_end),
        traffic_term(
            NPM_FONTSOURCE, data.npm["npm_fontsource"], list(f["npm_fontsource"]), npm_end
        ),
        traffic_term(
            ECOSYSTEMS, data.ecosystems, list(f["ecosystems"]), day, age=False, dist="negbin"
        ),
        traffic_term(JSDELIVR, data.jsdelivr, list(f["jsdelivr"]), day, age=False, drop_zero=True),
        traffic_term(EXPO, data.npm["npm_expo"], list(f["npm_expo"]), npm_end),
    ]


def model(
    terms: Sequence[measure.CountTerm], names: Sequence[str]
) -> tuple[measure.Mixed, dict[int, float]]:
    """The model of ``names``' terms (in that order), and the parameters it holds fixed."""
    by_name = {t.name: t for t in terms}
    mixed = measure.Mixed(tuple(by_name[n] for n in names))
    return mixed, no_bulk(mixed)
