"""Read a run's stage into the measurement model's terms (``engine.builders``).

Only ``build/stage/`` is read, so a replay gives the same terms:

- ``terms.json``: each Project source's frame (the terms it counts) and the Web Almanac's
  values, states and flags;
- ``mapped.jsonl``: each observation's family (stage "map"), with its count;
- ``records/fot.jsonl``: Fonts Over Time's weekly rows with their categories, methods and
  sites crawled; ``records/npm.jsonl``: each package's first-seen day;
  ``records/google_metadata.jsonl``: the day Google Fonts added each family;
- ``l3.json``: the families license check L3 failed, which stage "rank" leaves out;
- ``snapshots.json``: each source's data date, where Google's, npm's and Expo's age shares
  are measured to (q7), as the 60-day grace is (b8b).

Counts are read and passed on, never logged: some are Google's or Fonts Over Time's (T2, T4).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from tff_catalog import jsonio, stageio
from tff_catalog.engine import builders, measure
from tff_catalog.paths import Paths

PROJECT_VIEW = "project"


def _lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _day(text: str | None) -> date | None:
    return None if text is None else date.fromisoformat(text[:10])


def _observations(paths: Paths) -> Iterator[tuple[str, str | None, str, float, str | None]]:
    """(source, series, key, value, family id) of every observation stage "map" saw."""
    for row in _lines(stageio.stage_path(paths, "mapped")):
        record = row["record"]
        if record.get("type") != "Observation":
            continue
        value = record.get("value")
        yield (
            record["source"],
            record.get("series"),
            record["key"]["key"],
            0.0 if value is None else float(value),
            row.get("family_id"),
        )


def _excluded(paths: Paths) -> frozenset[str]:
    from tff_catalog import license_l3

    return license_l3.exclusions(paths)


AGE_SOURCES = ("gf_stats", "npm")  # the sources whose packages' bulk is scaled by age (q7)


def project_data(paths: Paths, run_date: date, *, window_ends: bool = True) -> builders.ProjectData:
    """Everything the Project and Developers & apps terms read, from ``paths.stage``. With
    ``window_ends`` false, every age share is measured to the run date, as the reference
    implementation did (the parity test)."""
    terms = jsonio.load(stageio.stage_path(paths, "terms"))[PROJECT_VIEW]
    excluded = _excluded(paths)
    frames = {source: builders.frame(entries, excluded) for source, entries in terms.items()}

    fot_links: dict[str, str] = {}
    google: list[tuple[str, str, float]] = []
    google_links: dict[str, str] = {}
    npm: list[tuple[str, str, float]] = []
    ecosystems: list[tuple[str, str, float, date | None]] = []
    jsdelivr: list[tuple[str, str, float, date | None]] = []
    for source, series, key, value, fid in _observations(paths):
        if fid is None:
            continue
        if source == "fot":
            fot_links[key] = fid
        elif source == "gf_stats" and series == "year":
            google.append((key, fid, value))
        elif source == "google_metadata":
            google_links[key] = fid
        elif source == "npm" and series == "last-year":
            npm.append((key, fid, value))
        elif source == "ecosystems":
            ecosystems.append((key, fid, value, None))
        elif source == "fontsource_stats" and series == "jsdelivr_monthly":
            jsdelivr.append((key, fid, value, None))

    records = paths.stage / "records"
    first_seen: dict[str, date | None] = {}
    for row in _lines(records / "npm.jsonl"):
        if row.get("type") == "Observation":
            first_seen.setdefault(row["key"]["key"], _day(row["attrs"].get("first_seen")))
    packages = {
        source: tuple(
            (key, fid, value, first_seen.get(key))
            for key, fid, value in npm
            if key.startswith(prefix)
        )
        for source, prefix in builders.NPM_PREFIXES.items()
    }

    added: dict[str, date | None] = {}
    for row in _lines(records / "google_metadata.jsonl"):
        if row.get("type") == "Observation":
            continue
        fid = google_links.get(row["family"])
        if fid is None:
            continue
        day = _day(row.get("added"))
        if fid in added and added[fid] != day:
            raise ValueError(f"google_metadata: {fid} has two families with different dates added")
        added[fid] = day

    fot = tuple(
        builders.FotRow(
            week=row["series"],
            end=date.fromisoformat(row["end"]),
            key=row["key"]["key"],
            category=row["attrs"]["category"],
            method=row["attrs"]["method"],
            sites=float(row["value"]),
            category_sites=int(row["attrs"]["category_sites"]),
        )
        for row in _lines(records / "fot.jsonl")
        if row.get("type") == "Observation"
    )
    data_dates: dict[str, date] = {}
    if window_ends:
        for source, snap in stageio.load_stage(paths, "snapshots").items():
            if source in AGE_SOURCES and snap.data_date is not None:
                data_dates[source] = snap.data_date
    return builders.ProjectData(
        run_date=run_date,
        frames=frames,
        fot=fot,
        fot_links=fot_links,
        google=tuple(google),
        google_added=added,
        npm=packages,
        ecosystems=tuple(ecosystems),
        jsdelivr=tuple(jsdelivr),
        data_dates=data_dates,
    )


def models(
    paths: Paths, run_date: date, *, window_ends: bool = True
) -> dict[str, tuple[measure.Mixed, dict[int, float]]]:
    """The Project and Developers & apps models of a run, with their fixed parameters."""
    terms = builders.project_terms(project_data(paths, run_date, window_ends=window_ends))
    return {
        "project": builders.model(terms, builders.PROJECT_TERMS),
        "dev_apps": builders.model(terms, builders.DEV_APPS_TERMS),
    }
