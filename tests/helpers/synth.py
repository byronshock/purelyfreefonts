"""Synthetic records, snapshot store and run state, plus a stub pipeline (design-m1 §5).

Everything here is deterministic by ``seed`` (numpy's PCG64 via
``default_rng``, the same bits on every platform) and contains no real data.
Names, hosts and URLs are invented; hosts use the reserved ``.example`` domain.

**Records.** ``families(seed)`` makes the synthetic font families. The record
builders (``universe_record``, ``license_fact``, ``observation``, ``relation``)
make one record of each type, and ``records(seed, day)`` returns every record of
every synthetic source for one day, in canonical order.

**Store.** ``make_store(root)`` writes ``<source>/<YYYY-MM-DD>/manifest.json``
(valid against ``schemas/stage/manifest.schema.json``) plus one extract,
``records.jsonl``, for three sources and three run days (``DAYS``):

- ``synth_universe``: a ``UniverseRecord`` and a ``LicenseFact`` per family; the
  last family first appears on the last day, so a run mints a new id.
- ``synth_installs``: a 365-day install count per family (``Observation``). The
  ``FADING`` family collapses after the first day, so it drops out of the
  catalog and its ``runs_outside`` counter advances.
- ``synth_packages``: a current install count for some families, and
  ``Relation`` edges from a meta package. It has no snapshot on the last day,
  so that run uses the previous one and marks it stale.

**State.** ``make_state(root, store)`` writes S0: the state a merged run on
``DAYS[0]`` left behind, starting from nothing.

**Stub pipeline.** ``stub_refresh`` has the signature of
``tff_catalog.refresh.refresh`` and the same contract for the two-run test: it
reads ``paths.state`` and the store, never writes ``state/``, and writes
``build/catalog.json`` and ``build/state/*`` deterministically. It always reads
snapshots (``from_snapshots``, else the run date): it never fetches. It uses
``read_state`` and ``apply_state`` in place of ``tff_catalog.state``'s until
M1 step 3 lands, and the two-run test swaps in the real ones at M1 step 18.
"""

import copy
import hashlib
import math
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from tests.helpers import ROOT

from tff_catalog import jsonio
from tff_catalog.names import match_key, mint_id, slugify
from tff_catalog.paths import Paths
from tff_catalog.records import (
    FontFileRef,
    LicenseFact,
    Observation,
    Record,
    Relation,
    SourceKey,
    UniverseRecord,
    attrs,
    read_jsonl,
    sort_key,
    write_jsonl,
)
from tff_catalog.refresh import RunResult
from tff_catalog.stages import RunOptions
from tff_catalog.state import MAX_RUN_HISTORY, STATE_FILES, State
from tff_catalog.store import MANIFEST_NAME, MANIFEST_SCHEMA, stale_max_age

SEED = 20260925
DAYS = (date(2026, 9, 3), date(2026, 10, 3), date(2026, 11, 3))
N_FAMILIES = 12
FADING = 0  # index of the family whose installs collapse after DAYS[0]
CATALOG_SIZE = 6  # the stub's catalog: the top 6
LEAVE_RUNS = 2  # runs outside the top before a member leaves

UNIVERSE = "synth_universe"
INSTALLS = "synth_installs"
PACKAGES = "synth_packages"
SOURCES = (UNIVERSE, INSTALLS, PACKAGES)
EXTRACT = "records.jsonl"
META_PACKAGE = SourceKey("deb-pkg", "synth-desktop")
STUB_STAGES = ("parse", "universe", "rank", "membership", "export")

_FIRST = (
    "Aster",
    "Birch",
    "Cobalt",
    "Dune",
    "Ember",
    "Fjord",
    "Garnet",
    "Heath",
    "Élan",
    "Juniper",
    "Kestrel",
    "Łąka",
    "Moss",
    "Nimbus",
    "Opal",
    "Quill",
)
_SECOND = {
    "Sans": "sans-serif",
    "Grotesk": "sans-serif",
    "Serif": "serif",
    "Slab": "serif",
    "Mono": "monospace",
    "Display": "display",
}
_LICENSES = (
    ("SIL Open Font License, Version 1.1", "OFL-1.1"),
    ("OFL-1.1", "OFL-1.1"),
    ("Apache License, Version 2.0", "Apache-2.0"),
)


def _rng(seed: int, *stream: int) -> np.random.Generator:
    return np.random.default_rng([seed, *stream])


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- families and records -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Family:
    index: int
    name: str
    slug: str
    category: str
    since: date  # first day the universe source lists it

    @property
    def monospace(self) -> bool:
        return self.category == "monospace"


def families(seed: int = SEED, n: int = N_FAMILIES) -> tuple[Family, ...]:
    """``n`` distinct synthetic families; the last one first appears on ``DAYS[-1]``."""
    rng = _rng(seed, 0)
    pairs = [(a, b) for a in _FIRST for b in _SECOND]
    if not 1 <= n <= len(pairs):
        raise ValueError(f"n must be between 1 and {len(pairs)}")
    picked = [pairs[int(i)] for i in rng.permutation(len(pairs))[:n]]
    return tuple(
        Family(
            index=i,
            name=f"{a} {b}",
            slug=slugify(f"{a} {b}"),
            category=_SECOND[b],
            since=DAYS[-1] if i == n - 1 and n > 1 else DAYS[0],
        )
        for i, (a, b) in enumerate(picked)
    )


def universe_record(fam: Family, seed: int = SEED) -> UniverseRecord:
    """The universe source's record for ``fam`` (the same on every day)."""
    rng = _rng(seed, 1, fam.index)
    names = (("Old " + fam.name, "rename"),) if fam.index == 1 else ()
    return UniverseRecord(
        source=UNIVERSE,
        key=SourceKey("fs-id", fam.slug),
        family=fam.name,
        category=fam.category,
        subsets=("latin", "latin-ext") if rng.random() < 0.7 else ("latin",),
        latin_languages=int(rng.integers(40, 900)),
        is_monospace=fam.monospace,
        variable=bool(rng.random() < 0.5),
        added=date(2018, 1, 1) + timedelta(days=int(rng.integers(0, 2500))),
        status="live",
        urls=(
            ("homepage", f"https://{fam.slug}.synth.example/"),
            ("repository", f"https://git.synth.example/{fam.slug}"),
        ),
        files=(
            FontFileRef(
                url=f"https://cdn.synth.example/{fam.slug}/1.0/{fam.slug}-Regular.woff2",
                sha256=_sha(f"file:{fam.slug}"),
                size=int(rng.integers(12_000, 90_000)),
                role="variable" if fam.index % 2 else "regular",
            ),
        ),
        names=names,
        attrs=attrs(version="1.000", weights=int(rng.integers(1, 10))),
    )


def dropped_record() -> UniverseRecord:
    """An icon font the universe source lists; every stage must ignore it."""
    return UniverseRecord(
        source=UNIVERSE,
        key=SourceKey("fs-id", "synth-icons"),
        family="Synth Icons",
        category="display",
        drop="icon",
    )


def license_fact(fam: Family, seed: int = SEED) -> LicenseFact:
    """The universe source's license statement for ``fam``."""
    rng = _rng(seed, 2, fam.index)
    raw, spdx = _LICENSES[int(rng.integers(0, len(_LICENSES)))]
    return LicenseFact(
        source=UNIVERSE,
        key=SourceKey("fs-id", fam.slug),
        raw=raw,
        spdx=spdx if raw == spdx else None,
        text_url=f"https://{fam.slug}.synth.example/LICENSE.txt",
        text_sha256=_sha(f"license:{fam.slug}"),
        rfn=bool(rng.random() < 0.2),
    )


def observation(fam: Family, source: str, day: date, value: float | None) -> Observation:
    """One count for ``fam`` from a synthetic ranking source, for the run on ``day``."""
    if source == INSTALLS:
        return Observation(
            source=source,
            series="365d",
            key=SourceKey("brew-cask", f"font-{fam.slug}"),
            value=value,
            unit="installs",
            start=day - timedelta(days=365),
            end=day - timedelta(days=1),
            attrs=attrs(tap="synth/fonts"),
        )
    if source == PACKAGES:
        return Observation(
            source=source,
            series="inst",
            key=SourceKey("deb-pkg", f"fonts-{fam.slug}"),
            value=value,
            unit="installs",
            start=day - timedelta(days=1),
            end=day - timedelta(days=1),
        )
    raise ValueError(f"{source} is not a synthetic ranking source")


def relation(fam: Family, alt: int = 0) -> Relation:
    """The meta package depends on ``fam``'s package (``alt`` > 0: an ``a | b`` alternative)."""
    return Relation(
        source=PACKAGES,
        subject=META_PACKAGE,
        kind="depends",
        object=SourceKey("deb-pkg", f"fonts-{fam.slug}"),
        alt=alt,
    )


def _trend(fam: Family, day: date) -> float:
    """The FADING family leads on DAYS[0], then collapses in every ranking source."""
    if fam.index != FADING:
        return 1.0
    return 50.0 if day <= DAYS[0] else 0.01


def _installs(fam: Family, day: date, seed: int) -> int:
    base = float(_rng(seed, 3, fam.index).lognormal(8.0, 1.2)) + 50.0
    noise = 1.0 + 0.15 * float(_rng(seed, 4, fam.index, day.toordinal()).standard_normal())
    return max(0, round(base * noise * _trend(fam, day)))


def _packaged(fam: Family) -> bool:
    return fam.index % 3 != 2


def _popcon(fam: Family, day: date, seed: int) -> int:
    base = float(_rng(seed, 5, fam.index).lognormal(6.0, 1.0))
    drift = 1.0 + 0.05 * float(_rng(seed, 6, fam.index, day.toordinal()).standard_normal())
    return max(0, round(base * drift * _trend(fam, day)))


def source_records(source: str, day: date, seed: int = SEED, n: int = N_FAMILIES) -> list[Record]:
    """Every record ``source`` emits for the run on ``day``, in canonical order."""
    fams = [f for f in families(seed, n) if f.since <= day]
    out: list[Record] = []
    if source == UNIVERSE:
        for fam in fams:
            out += [universe_record(fam, seed), license_fact(fam, seed)]
        out.append(dropped_record())
    elif source == INSTALLS:
        out += [observation(fam, source, day, float(_installs(fam, day, seed))) for fam in fams]
    elif source == PACKAGES:
        packaged = [fam for fam in fams if _packaged(fam)]
        out += [observation(fam, source, day, float(_popcon(fam, day, seed))) for fam in packaged]
        out += [relation(fam, alt) for alt, fam in enumerate(packaged[:3])]
    else:
        raise ValueError(f"unknown synthetic source {source!r}")
    return sorted(out, key=sort_key)


def records(seed: int = SEED, day: date = DAYS[0], n: int = N_FAMILIES) -> list[Record]:
    """Every record of every synthetic source for ``day``: all four record types."""
    out = [r for source in SOURCES for r in source_records(source, day, seed, n)]
    return sorted(out, key=sort_key)


# --- store ----------------------------------------------------------------------------------


def write_snapshot(store: Path, source: str, day: date, recs: list[Record]) -> Path:
    """Write one complete snapshot ``<store>/<source>/<day>/`` holding ``recs``."""
    directory = Path(store) / source / day.isoformat()
    rows = write_jsonl(recs, directory / EXTRACT)
    data = (directory / EXTRACT).read_bytes()
    window = None
    if source == INSTALLS:
        window = {"start": (day - timedelta(days=365)).isoformat(), "end": _prev(day)}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "source": source,
        "date": day.isoformat(),
        "collector_version": 1,
        "complete": True,
        "data_date": _prev(day),
        "window": window,
        "fetched": [
            {
                "url": f"https://{source.replace('_', '-')}.synth.example/{day.isoformat()}.json",
                "status": 200,
                "fetched_at": f"{day.isoformat()}T06:00:00Z",
                "sha256": _sha_bytes(data),
                "bytes": len(data),
                "etag": None,
                "last_modified": None,
                "kept": False,
            }
        ],
        "extracts": [
            {"path": EXTRACT, "sha256": _sha_bytes(data), "bytes": len(data), "rows": rows}
        ],
        "stale_of": None,
        "notes": ["synthetic"],
    }
    jsonio.dump(manifest, directory / MANIFEST_NAME)
    return directory


def make_store(
    root: Path, seed: int = SEED, days: tuple[date, ...] = DAYS, n: int = N_FAMILIES
) -> Path:
    """Write the synthetic store (see the module docstring); return ``root``."""
    root = Path(root)
    for source in SOURCES:
        for day in days:
            if source == PACKAGES and day == days[-1]:
                continue  # goes stale on the last day
            write_snapshot(root, source, day, source_records(source, day, seed, n))
    return root


def _prev(day: date) -> str:
    return (day - timedelta(days=1)).isoformat()


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class Inputs:
    """What one run reads from the store."""

    records: dict[str, list[Record]]  # source -> records
    snapshots: dict[str, date]  # source -> snapshot date used
    stale: tuple[str, ...] = ()  # sources whose snapshot is older than the run's


def _stale_max_months() -> int:
    with (ROOT / "config" / "ranking.toml").open("rb") as fh:
        return tomllib.load(fh)["stale"]["max_months"]


def load_inputs(store: Path, day: date) -> Inputs:
    """The newest complete snapshot of each source on or before ``day``, no older than the
    stale window of ``config/ranking.toml`` (``store.stale_max_age``)."""
    max_age = stale_max_age(_stale_max_months())
    recs: dict[str, list[Record]] = {}
    used: dict[str, date] = {}
    for source_dir in sorted(p for p in Path(store).iterdir() if p.is_dir()):
        if source_dir.name.startswith(("_", ".")):
            continue
        best = None
        for snap in sorted(source_dir.iterdir()):
            manifest_path = snap / MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            manifest = jsonio.load(manifest_path)
            snap_day = date.fromisoformat(manifest["date"])
            if manifest["complete"] and day - max_age <= snap_day <= day:
                best = (snap_day, snap, manifest)
        if best is None:
            continue
        snap_day, snap, manifest = best
        extract = next(e for e in manifest["extracts"] if e["path"] == EXTRACT)
        if _sha_bytes((snap / EXTRACT).read_bytes()) != extract["sha256"]:
            raise ValueError(f"{snap / EXTRACT}: sha256 does not match the manifest")
        recs[source_dir.name] = read_jsonl(snap / EXTRACT)
        used[source_dir.name] = snap_day
    stale = tuple(sorted(s for s, d in used.items() if d < day))
    return Inputs(records=recs, snapshots=used, stale=stale)


# --- state ----------------------------------------------------------------------------------


def read_state(directory: Path) -> State:
    """Read a state directory into ``tff_catalog.state.State``; missing files read as empty."""
    kwargs: dict[str, Any] = {}
    for name, filename in STATE_FILES.items():
        path = Path(directory) / filename
        if path.is_file():
            value = jsonio.load(path)
            kwargs[name] = tuple(value) if name == "run_history" else value
    return State(**kwargs)


def write_state(files: dict[str, Any], directory: Path) -> Path:
    """Write every state file (``STATE_FILES``) into ``directory``."""
    for name, filename in STATE_FILES.items():
        jsonio.dump(files[name], Path(directory) / filename)
    return Path(directory)


def apply_state(state_dir: Path, next_state_dir: Path, out_dir: Path | None = None) -> Path:
    """Simulate merging a refresh PR: ``next_state_dir``'s files over ``state_dir``'s.

    Same contract as ``tff_catalog.state.apply_state``: writes into ``out_dir`` (a
    fresh copy of ``state_dir``) or, when it is None, into ``state_dir``.
    """
    out = Path(state_dir)
    if out_dir is not None:
        out = Path(out_dir)
        shutil.copytree(state_dir, out)
    for path in sorted(Path(next_state_dir).iterdir()):
        if path.is_file():
            shutil.copyfile(path, out / path.name)
    return out


def make_state(root: Path, store: Path) -> Path:
    """Write S0, the state a merged run on ``DAYS[0]`` left, into ``root``."""
    next_files, _ = advance(State(), load_inputs(store, DAYS[0]), DAYS[0])
    return write_state(next_files, root)


# --- stub pipeline --------------------------------------------------------------------------


@dataclass(slots=True)
class _Run:
    today: str
    ids: dict[str, dict[str, Any]]
    slug_to_id: dict[str, str] = field(default_factory=dict)
    universe: dict[str, UniverseRecord] = field(default_factory=dict)  # id -> record
    licenses: dict[str, LicenseFact] = field(default_factory=dict)  # id -> fact
    values: dict[str, dict[str, float]] = field(default_factory=dict)  # source -> id -> value


def _slug(key: SourceKey) -> str:
    for prefix in ("font-", "fonts-"):
        if key.key.startswith(prefix):
            return key.key.removeprefix(prefix)
    return key.key


def advance(base: State, inputs: Inputs, run_date: date) -> tuple[dict[str, Any], dict[str, Any]]:
    """The stub pipeline's core: ``(next state files, catalog)``, a pure function of its inputs."""
    run = _Run(today=run_date.isoformat(), ids=copy.deepcopy(base.ids))
    _universe(run, inputs)
    _values(run, inputs)
    candidates = sorted(run.universe)
    scores = _scores(run, candidates)
    order = {fid: i + 1 for i, fid in enumerate(sorted(candidates, key=lambda f: (-scores[f], f)))}
    catalog_members = _membership(base, order, run.today)
    members = sorted(f for f, m in catalog_members.items() if m["member"])
    files = {
        "run_history": _run_history(base, inputs, run.today),
        "ids": run.ids,
        "first_seen": _first_seen(base, run, candidates, catalog_members),
        "membership": {"catalog": catalog_members, "top100": {}},
        "license_hashes": _license_hashes(base, run, members),
        "stale": _stale(base, inputs),
        "published_ranks": {"overall": {f: order[f] for f in members}},
        "published_scores": {"overall": {f: scores[f] for f in members}},
        "smoothing": {"fot_ewma": {}, "rising": _rising(base, run)},
    }
    catalog = {
        "format": "synthetic",
        "run_date": run.today,
        "stale": list(inputs.stale),
        "families": [
            {
                "id": fid,
                "family": run.universe[fid].family,
                "order": order[fid],
                "score": scores[fid],
                "member": fid in members,
            }
            for fid in sorted(candidates, key=order.__getitem__)
        ],
    }
    return files, catalog


def _universe(run: _Run, inputs: Inputs) -> None:
    by_key = {match_key(v["family"]): fid for fid, v in run.ids.items()}
    universe = [r for r in inputs.records.get(UNIVERSE, ()) if isinstance(r, UniverseRecord)]
    for r in universe:
        if r.drop is not None:
            continue
        mk = match_key(r.family)
        fid = by_key.get(mk)
        if fid is None:
            fid = mint_id(r.family, set(run.ids))
            run.ids[fid] = {
                "family": r.family,
                "minted_from": f"{r.source}:{r.key.ns}:{r.key.key}",
                "first_seen": run.today,
            }
            by_key[mk] = fid
        run.slug_to_id[r.key.key] = fid
        run.universe[fid] = r
    for r in inputs.records.get(UNIVERSE, ()):
        if isinstance(r, LicenseFact) and r.key.key in run.slug_to_id:
            run.licenses[run.slug_to_id[r.key.key]] = r


def _values(run: _Run, inputs: Inputs) -> None:
    for source, recs in sorted(inputs.records.items()):
        for r in recs:
            if isinstance(r, Observation) and r.value is not None:
                fid = run.slug_to_id.get(_slug(r.key))
                if fid is not None:
                    run.values.setdefault(source, {})[fid] = r.value


def _scores(run: _Run, candidates: list[str]) -> dict[str, float]:
    scores = dict.fromkeys(candidates, 0.0)
    for values in run.values.values():
        ranked = sorted(values, key=lambda f: (-values[f], f))
        for i, fid in enumerate(ranked):
            scores[fid] += 1.0 - i / len(ranked)
    return {fid: round(s, 6) for fid, s in scores.items()}


def _membership(base: State, order: dict[str, int], today: str) -> dict[str, dict[str, Any]]:
    prev = base.membership.get("catalog", {})
    out: dict[str, dict[str, Any]] = {}
    for fid in sorted(set(prev) | set(order)):
        p = prev.get(fid)
        was_member = bool(p and p["member"])
        if order.get(fid, math.inf) <= CATALOG_SIZE:
            entered = p["entered"] if p and was_member else today
            out[fid] = {"member": True, "entered": entered, "runs_outside": 0}
        elif p and was_member:
            runs = p["runs_outside"] + 1
            out[fid] = {"member": runs < LEAVE_RUNS, "entered": p["entered"], "runs_outside": runs}
    return out


def _run_history(base: State, inputs: Inputs, today: str) -> list[dict[str, Any]]:
    entry = {
        "run_date": today,
        "merged_pr": None,
        "code_commit": "synthetic",
        "config_sha256": "synthetic",
        "snapshots": {s: d.isoformat() for s, d in sorted(inputs.snapshots.items())},
    }
    history = [h for h in base.run_history if h["run_date"] != today] + [entry]
    return sorted(history, key=lambda h: h["run_date"])[-MAX_RUN_HISTORY:]


def _first_seen(
    base: State, run: _Run, candidates: list[str], members: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    out = copy.deepcopy(base.first_seen)
    for fid in candidates:
        entry = out.setdefault(fid, {"catalog": None, "sources": {}})
        if members.get(fid, {}).get("member") and entry["catalog"] is None:
            entry["catalog"] = run.today
    for source, values in sorted(run.values.items()):
        for fid in values:
            out[fid]["sources"].setdefault(source, run.today)
    return out


def _license_hashes(base: State, run: _Run, members: list[str]) -> dict[str, dict[str, Any]]:
    out = copy.deepcopy(base.license_hashes)
    for fid in members:
        fact = run.licenses.get(fid)
        if fact is None:
            continue
        prev = out.get(fid)
        same = prev is not None and prev["text_sha256"] == fact.text_sha256
        font = run.universe[fid].files[0] if run.universe[fid].files else None
        out[fid] = {
            "text_url": fact.text_url,
            "text_sha256": fact.text_sha256,
            "checked_on": prev["checked_on"] if same else run.today,
            "font_version": None,
            "font_file": None if font is None else {"url": font.url, "sha256": font.sha256},
            "level": "L2",
        }
    return out


def _stale(base: State, inputs: Inputs) -> dict[str, dict[str, Any]]:
    out = {}
    for source, snap_day in sorted(inputs.snapshots.items()):
        prev_runs = base.stale.get(source, {}).get("stale_runs", 0)
        runs = prev_runs + 1 if source in inputs.stale else 0
        out[source] = {"last_good": snap_day.isoformat(), "stale_runs": runs}
    return out


def _rising(base: State, run: _Run) -> dict[str, dict[str, list[float]]]:
    prev = base.smoothing.get("rising", {}).get(INSTALLS, {})
    values = run.values.get(INSTALLS, {})
    total = sum(values.values())
    shares = {}
    for fid in sorted(values):
        share = round(values[fid] / total, 9) if total else 0.0
        shares[fid] = [*prev.get(fid, []), share][-3:]
    return {INSTALLS: shares}


def stub_refresh(
    paths: Paths,
    run_date: date,
    from_snapshots: date | None = None,
    *,
    options: RunOptions | None = None,
) -> RunResult:
    """A stand-in for ``tff_catalog.refresh.refresh`` over the synthetic store."""
    del options  # accepted for signature parity; the stub has no flags
    inputs = load_inputs(paths.require_store(), from_snapshots or run_date)
    files, catalog = advance(read_state(paths.state), inputs, run_date)
    jsonio.dump(catalog, paths.build / "catalog.json")
    write_state(files, paths.next_state)
    return RunResult(
        run_date=run_date,
        build=paths.build,
        stages=STUB_STAGES,
        stale=inputs.stale,
        failures=(),
        seconds=0.0,
    )
