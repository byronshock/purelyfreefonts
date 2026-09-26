"""Stage "map": source keys to families (milestone-1 step 9). Owner: agent P5.

Every ranking record's key is looked up in the alias index (exact
``match_key`` within its namespace, never by prefix). A key maps to one family,
to an ineligible row, or to nothing; nothing is guessed. Unmatched keys above
their source's floor go to ``build/unmatched.md``, sorted by volume.
``build/unmatched.md`` is committed, so a source whose ``publish_raw`` is false
(Google and Fonts Over Time, rulings T2 and T4) shows each key's rank in the
source, never its value.

Reads the alias index, ``build/stage/alias_index.json`` (stage "aliases";
``stageio.load_stage(paths, "alias_index")`` gives ``IndexEntry`` rows and
``index_of`` turns them into an ``AliasIndex``). Writes
``build/stage/mapped.jsonl`` (``stageio`` rows of ``Mapped``) and
``build/unmatched.md``.

**Resolving a key** (``resolve``). ``"direct"`` and the crediting alias
relations (``CREDITING``) give the family. An ``"ineligible"`` row gives no
family, with its reason in ``detail``; it counts as resolved. A ``"sibling"``,
``"related"`` or ``"distinct"`` row (``BLOCKING``) says what the key is *not*,
so the key stays unmatched.

**Rows of** ``mapped.jsonl``: one per Observation whose key resolves and one per
Relation whose object (the font pulled in, preinstalled or provided) resolves,
from every records file, baselines (``<source>@<date>.jsonl``) included, in
``records.sort_key`` order. A relation's subject (the package or system that
pulls the object in) is resolved with ``resolve`` where a stage needs it; the
frames below use both ends.

**Frames** (methodology §3, design-m1 gap G11). ``frames`` gives each enabled
engine source the eligible families it could have shown. The stage does not
write them: stage "correct" still derives its own frame, and ``frames.json``
becomes a stage file (``stageio.STAGE_FILES``) only once it reads these
through ``frames`` and ``evidence_state`` instead:

- ``packaged`` sources (Homebrew, Arch, Debian, GitHub, Nerd, npm, ecosyste.ms,
  jsDelivr): families with a key in the source's namespaces (and npm scopes)
  among the universe keys, the source's own records (any series; a ``None``
  value is "in the frame, no usable value") and either end of any relation;
- ``listed`` (Google): the same without relations, so a non-Google font is
  outside the frame;
- ``web_servable`` (Fonts Over Time, the Almanac; gap G11): every eligible
  family with a Google, Fontsource or foundry key, plus any family the source
  itself observed.

``evidence_state`` is the rule that turns frame membership, exposure and a
value into one of the four evidence states, which ``terms.json`` carries (stage
"correct" writes that file).

**The report** (``unmatched.md`` and ``map --check-unmatched N``). For each
enabled engine source, ``engine_observations`` selects the rows the engine
reads (its collector, series, npm scopes, Nerd's excluded assets, FOT's
methods, no prereleases, with Google's fallback when the main series is
missing) and ``volumes`` turns them into one number per key in the source's
``rate`` units, counted by the source's ``counting`` as methodology §3 and §5
say: a rate over min(window, days available), where the days available run
from the key's ``first_seen`` attr (a lifetime counter: from the asset's
creation); a share (count / ``samples`` when the row is a count); the mean of
monthly shares from the key's first non-zero month; the mean of the last
month's weekly values (a missing week counts 0); or the value as it is.
Only font keys take a rank (``font_key_filter``): not reverse dependents
(``dependents``: packages that only pull fonts in and whose names are not font
package names), nor unresolved distro packages with no font package name and
no font signal in the package data (glibc and firefox in pkgstats). A key is
listed when it does not resolve, is not a bundle key (``bundle_rows``: a
``bundle`` row of ``data/aliases.csv``, which stage "correct" credits to each
of its families, so fonts-urw-base35 counts as matched), and its volume is
positive and at least ``floor + censor_below`` (the line under which the
engine censors it). Its rank is its
position among the source's font keys (1 = largest; ties share the better
rank). ``--check-unmatched N`` flags every unmatched key ranked N or better,
under the floor too (milestone-1 step 9, "Done when").
"""

import dataclasses
import hashlib
import logging
import math
import re
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import unquote

from tff_catalog import jsonio, stageio
from tff_catalog.config_model import (
    EcosystemsSource,
    FotSource,
    GoogleSource,
    NerdSource,
    NpmSource,
    SourceBase,
)
from tff_catalog.keys import match_key
from tff_catalog.records import Observation, Relation, SourceKey, read_jsonl, sort_key

if TYPE_CHECKING:
    from tff_catalog.corrections import EvidenceState, Reason
    from tff_catalog.paths import Paths
    from tff_catalog.reviews import Question
    from tff_catalog.stages import StageContext
    from tff_catalog.universe import Universe


@dataclass(frozen=True, slots=True)
class Mapped:
    record: Observation | Relation
    family_id: str | None  # None for ineligible keys
    relation: str  # "direct" or the alias relation used
    detail: str = ""  # build subtype, bundle, ineligible reason


@dataclass(frozen=True, slots=True)
class Unmatched:
    """A key with no alias row. ``map_records`` gives one per record (a relation's
    object has ``value`` None and its kind as ``unit``); the report takes one per
    (engine source, key) with its volume, ``rank`` in the source, and ``value``
    None when the source's ``publish_raw`` is false."""

    source: str
    key: SourceKey
    value: float | None
    unit: str
    rank: int | None = None  # position among the source's font keys, 1 = largest


# (ns, match_key) -> (family_id or "", relation, detail), from stage/alias_index.json.
type AliasIndex = Mapping[tuple[str, str], tuple[str, str, str]]


@dataclass(frozen=True, slots=True, order=True)
class IndexEntry:
    """One row of ``build/stage/alias_index.json`` (a JSON array, sorted)."""

    ns: str
    match_key: str
    family_id: str  # "" for ineligible names
    relation: str  # aliases.RELATIONS, or "direct"
    detail: str = ""


def index_of(entries: Iterable[IndexEntry]) -> dict[tuple[str, str], tuple[str, str, str]]:
    """The ``AliasIndex`` of the entries; a (ns, match_key) listed twice raises ``ValueError``."""
    index: dict[tuple[str, str], tuple[str, str, str]] = {}
    for e in entries:
        key = (e.ns, e.match_key)
        if key in index:
            raise ValueError(f"alias index lists {key} twice")
        index[key] = (e.family_id, e.relation, e.detail)
    return index


def entries_of(index: AliasIndex) -> list[IndexEntry]:
    """The sorted rows to write for ``index`` (the inverse of ``index_of``)."""
    return sorted(IndexEntry(ns, key, *value) for (ns, key), value in index.items())


# --- resolving keys ---------------------------------------------------------------------------

DIRECT = "direct"
INELIGIBLE = "ineligible"
# The alias relations (aliases.RELATIONS) that credit a key to its family.
CREDITING = frozenset({DIRECT, "rename", "build", "package", "postscript", "bundle"})
# Rows naming the family a key must NOT map to (Roboto Slab is not Roboto).
BLOCKING = frozenset({"sibling", "related", "distinct"})

type Resolution = tuple[str | None, str, str]  # (family_id or None if ineligible, relation, detail)


def index_problems(idx: AliasIndex) -> list[str]:
    """What is wrong with the index: keys not in ``match_key`` form, unknown
    relations, ineligible rows with a family, other rows without one."""
    out = []
    for (ns, key), (family_id, relation, _) in sorted(idx.items()):
        where = f"{ns}:{key}"
        if match_key(key) != key:
            out.append(f"{where}: key is not in match_key form ({match_key(key)!r})")
        if relation == INELIGIBLE:
            if family_id:
                out.append(f"{where}: an ineligible row has family {family_id!r}")
        elif relation not in CREDITING | BLOCKING:
            out.append(f"{where}: unknown relation {relation!r}")
        elif not family_id:
            out.append(f"{where}: a {relation!r} row has no family")
    return out


def resolve(key: SourceKey, idx: AliasIndex) -> Resolution | None:
    """``key``'s family, or None when it is unmatched.

    Exact lookup of ``(key.ns, match_key(key.key))``, never by prefix. An
    ineligible row resolves with family None; a blocking row leaves the key
    unmatched. A malformed entry raises ``ValueError`` (``index_problems``).
    """
    hit = idx.get((key.ns, match_key(key.key)))
    if hit is None:
        return None
    family_id, relation, detail = hit
    if relation == INELIGIBLE and not family_id:
        return None, relation, detail
    if relation in BLOCKING and family_id:
        return None
    if relation in CREDITING and family_id:
        return family_id, relation, detail
    raise ValueError(f"alias index entry for {key.ns}:{key.key} is malformed: {hit!r}")


def _primary(rec: Observation | Relation) -> SourceKey:
    """The key a record is mapped by: an observation's key, a relation's object."""
    return rec.object if isinstance(rec, Relation) else rec.key


def map_records(
    recs: Iterable[Observation | Relation], idx: AliasIndex
) -> tuple[list[Mapped], list[Unmatched]]:
    """Map every record; relations map both ends.

    Returns the ``Mapped`` rows (a relation's row carries its object's family)
    and one ``Unmatched`` per record whose key or object does not resolve, both
    sorted. A relation's subject is not reported: it is usually a package that
    is not a font; ``resolve`` gives it when needed.
    """
    mapped: list[Mapped] = []
    unmatched: list[Unmatched] = []
    for rec in recs:
        key = _primary(rec)
        hit = resolve(key, idx)
        if hit is not None:
            mapped.append(Mapped(rec, *hit))
        elif isinstance(rec, Relation):
            unmatched.append(Unmatched(rec.source, key, None, rec.kind))
        else:
            unmatched.append(Unmatched(rec.source, key, rec.value, rec.unit))
    mapped.sort(key=_mapped_order)
    unmatched.sort(key=_unmatched_order)
    return mapped, unmatched


def _mapped_order(m: Mapped) -> tuple[object, ...]:
    return (sort_key(m.record), m.family_id or "", m.relation, m.detail)


def _unmatched_order(u: Unmatched) -> tuple[object, ...]:
    value = -u.value if u.value is not None else float("inf")
    rank = u.rank if u.rank is not None else float("inf")
    return (u.source, rank, value, u.key.ns, u.key.key, u.unit)


# --- engine rows, volumes and ranks --------------------------------------------------------------

type RecordsBy = Mapping[str, Sequence[Observation | Relation]]  # collector -> its records

_PERIODS = {"YYYY-MM": re.compile(r"^\d{4}-\d{2}$"), "YYYY-Www": re.compile(r"^\d{4}-W\d{2}$")}
DEPENDENCY_KINDS = frozenset({"depends", "recommends", "optdepends"})


def series_matches(pattern: str, series: str) -> bool:
    """Whether ``series`` is the engine series ``pattern`` ("YYYY-MM", "YYYY-Www" or a name)."""
    rx = _PERIODS.get(pattern)
    return bool(rx.match(series)) if rx is not None else series == pattern


def in_scopes(key: SourceKey, scopes: Sequence[str]) -> bool:
    """Whether an npm package (``@scope/name`` or ``pkg:npm/%40scope/name``) is in ``scopes``."""
    if not scopes:
        return True
    name = key.key
    if name.startswith("pkg:npm/"):
        name = unquote(name.removeprefix("pkg:npm/"))
    scope = name.split("/", 1)[0] if name.startswith("@") else ""
    return scope in scopes


_ARCHIVE = re.compile(r"\.(zip|tar\.xz)$", re.IGNORECASE)


def asset_base(name: str) -> str:
    """A Nerd release asset's name as ``exclude_assets`` lists it: the last path
    part, without a ``.zip`` or ``.tar.xz`` suffix, in ``match_key`` form."""
    return match_key(_ARCHIVE.sub("", name.rsplit("/", 1)[-1]))


def selector(src: SourceBase) -> Callable[[SourceKey], bool]:
    """The keys ``src`` reads: its npm scopes, minus Nerd's excluded assets
    (compared by ``asset_base``)."""
    scopes = src.scopes if isinstance(src, NpmSource | EcosystemsSource) else ()
    excluded = frozenset(
        asset_base(a) for a in (src.exclude_assets if isinstance(src, NerdSource) else ())
    )

    def selects(key: SourceKey) -> bool:
        return in_scopes(key, scopes) and not (excluded and asset_base(key.key) in excluded)

    return selects


def engine_observations(src: SourceBase, recs: RecordsBy) -> tuple[str, list[Observation]]:
    """``(collector, rows)``: the observations the engine reads for ``src``.

    Google falls back to ``fallback`` ("collector:series") when its main
    series has no rows.
    """
    rows = _select(src, src.collector, src.series, recs)
    if not rows and isinstance(src, GoogleSource) and src.fallback:
        collector, _, series = src.fallback.partition(":")
        return collector, _select(src, collector, series, recs)
    return src.collector, rows


def _select(src: SourceBase, collector: str, series: str, recs: RecordsBy) -> list[Observation]:
    selects = selector(src)
    return [
        r
        for r in recs.get(collector, ())
        if isinstance(r, Observation)
        and series_matches(series, r.series)
        and selects(r.key)
        and _wanted(src, r, selects)
    ]


def _wanted(src: SourceBase, o: Observation, selects: Callable[[SourceKey], bool]) -> bool:
    """Row filters beyond the key, as stage "correct" applies them: no
    prereleases, no excluded Nerd asset (by its ``asset`` attr too), only FOT's
    ``methods``."""
    if _attr(o, "prerelease") is True:
        return False
    asset = _attr(o, "asset")
    nerd_asset = isinstance(src, NerdSource) and isinstance(asset, str)
    if nerd_asset and not selects(SourceKey(o.key.ns, str(asset))):
        return False
    method = _attr(o, "method")
    return not (isinstance(src, FotSource) and method is not None and method not in src.methods)


def _attr(o: Observation | Relation, name: str) -> object:
    return dict(o.attrs).get(name)


def _attr_date(o: Observation, name: str) -> date | None:
    value = _attr(o, name)
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


YEAR_DAYS = 365.0
MONTH_DAYS = YEAR_DAYS / 12


def window_days(start: date, end: date) -> float:
    """Days in the window ``start``..``end`` (inclusive). A window within a few days
    of a year or a month counts as exactly one, as stage "correct" counts it."""
    days = (end - start).days + 1
    if 358 <= days <= 372:
        return YEAR_DAYS
    if 28 <= days <= 31:
        return MONTH_DAYS
    return float(max(days, 1))


def days_available(o: Observation, *, lifetime: bool = False) -> float:
    """The days ``o``'s value was counted over (methodology §3: min(window, days
    available)). The days available run from the key's ``first_seen`` attr; for a
    lifetime counter, from the asset's creation (``first_seen``, else
    ``published_at``, else ``start``)."""
    if lifetime:
        created = _attr_date(o, "first_seen") or _attr_date(o, "published_at") or o.start
        return float(max(1, (o.end - created).days))
    window = window_days(o.start, o.end)
    seen = _attr_date(o, "first_seen")
    if seen is not None and seen > o.start:
        return max(1.0, min(window, float((o.end - seen).days + 1)))
    return window


def rate_value(o: Observation, rate: str, *, lifetime: bool = False) -> float | None:
    """``o.value`` in ``rate`` units, or None when it has none.

    ``per_year``/``per_month`` turn the total into a rate over ``days_available``;
    ``share`` divides a count by its ``samples`` attr. A row in unit ``rank`` (1 is
    the top) becomes ``1 / rank``, so larger is still more.
    """
    if o.value is None:
        return None
    if o.unit == "rank":
        return 1 / o.value if o.value > 0 else None
    if rate == "share":
        if o.unit == "share":
            return o.value
        samples = _attr(o, "samples")
        return o.value / samples if type(samples) is int and samples > 0 else None
    if rate in ("per_year", "per_month"):
        period = YEAR_DAYS if rate == "per_year" else MONTH_DAYS
        return o.value * period / days_available(o, lifetime=lifetime)
    return o.value


def volumes(src: SourceBase, rows: Iterable[Observation]) -> dict[SourceKey, float]:
    """Each key's volume in ``src.rate`` units, by ``src.counting`` (module docstring)."""
    rows = list(rows)
    if not rows:
        return {}
    match src.counting:
        case "monthly_mean_share":
            return _monthly_mean(rows, src.rate, getattr(src, "months", 12))
        case "weekly_mean":
            return _weekly_mean(rows, src.rate)
        case "snapshot_delta":  # the report reads one snapshot: the first-run rule
            return _totals((o.key, rate_value(o, src.rate, lifetime=True)) for o in rows)
        case _:
            return _totals((o.key, rate_value(o, src.rate)) for o in rows)


def _totals[K](values: Iterable[tuple[K, float | None]]) -> dict[K, float]:
    """Each key's sum, independent of row order (``math.fsum``)."""
    parts: dict[K, list[float]] = defaultdict(list)
    for key, v in values:
        if v is not None:
            parts[key].append(v)
    return {key: math.fsum(vs) for key, vs in parts.items()}


def _monthly_mean(rows: Sequence[Observation], rate: str, months: int) -> dict[SourceKey, float]:
    """Arch: the mean of the last ``months`` monthly shares, from the key's first
    non-zero month on (a newer package is averaged over the months it existed)."""
    window = sorted({o.series for o in rows})[-months:]
    inside = set(window)
    by_key: dict[SourceKey, dict[str, float]] = defaultdict(dict)
    for (key, month), v in _totals(
        ((o.key, o.series), rate_value(o, rate)) for o in rows if o.series in inside
    ).items():
        by_key[key][month] = v
    out: dict[SourceKey, float] = {}
    for key, shares in by_key.items():
        nonzero = [m for m in window if shares.get(m, 0.0) > 0]
        if not nonzero:
            continue
        used = window[window.index(nonzero[0]) :]
        out[key] = math.fsum(shares.get(m, 0.0) for m in used) / len(used)
    return out


def _weekly_mean(rows: Sequence[Observation], rate: str) -> dict[SourceKey, float]:
    """Fonts Over Time: the mean over the weeks of the last month of data (weeks
    ending within 31 days of the latest), a missing week counting 0."""
    data_end = max(o.end for o in rows)
    week_end: dict[str, date] = {}
    for o in rows:
        week_end[o.series] = max(week_end.get(o.series, o.end), o.end)
    weeks = {w for w, end in week_end.items() if end > data_end - timedelta(days=31)}
    totals = _totals((o.key, rate_value(o, rate)) for o in rows if o.series in weeks)
    return {key: v / len(weeks) for key, v in totals.items()}


# Font package names (Debian fonts-*, xfonts-*; Arch ttf-*, otf-*, ttc-*, *-fonts,
# *-font): a package whose name says it is a font stays a font key even when it pulls
# other fonts in (fonts-dejavu-extra depends on fonts-dejavu-core).
FONT_PACKAGE = re.compile(r"(?:^|[-_.+])(?:x?fonts?|ttf|otf|ttc|woff2?)(?:[-_.+]|$)", re.I)


def dependents(relations: Iterable[Relation], idx: AliasIndex) -> frozenset[SourceKey]:
    """Unresolved packages that only pull fonts in: subjects of dependency edges
    to a resolved font key that are never themselves the object of one and whose
    names are not font package names (``FONT_PACKAGE``)."""
    subjects: set[SourceKey] = set()
    objects: set[SourceKey] = set()
    for r in relations:
        if r.kind not in DEPENDENCY_KINDS:
            continue
        objects.add(r.object)
        if resolve(r.object, idx) is not None:
            subjects.add(r.subject)
    return frozenset(
        k for k in subjects - objects if not FONT_PACKAGE.search(k.key) and resolve(k, idx) is None
    )


# Distro package names: a source such as pkgstats counts every package on the system.
PACKAGE_NAMESPACES = frozenset({"arch-pkg", "deb-pkg"})


def font_key_filter(relations: Iterable[Relation], idx: AliasIndex) -> Callable[[SourceKey], bool]:
    """Whether a key is a font key, which takes a rank in its source.

    Not font keys: ``dependents``, and every distro package
    (``PACKAGE_NAMESPACES``) that does not resolve, has no font package name
    (``FONT_PACKAGE``) and is neither in a package group (the nerd-fonts group)
    nor a provider of a font virtual (``ttf-font``): glibc and firefox in
    pkgstats, which counts every package. An unresolved font package without a
    font name or those edges (tex-gyre, gsfonts) is missed here until it has an
    alias row; that is the price of keeping thousands of system packages out of
    the ranks and ``unmatched.md``.
    """
    relations = list(relations)
    signalled = {k for r in relations if r.kind == "group" for k in (r.subject, r.object)}
    signalled |= {
        r.subject for r in relations if r.kind == "provides" and FONT_PACKAGE.search(r.object.key)
    }
    deps = dependents(relations, idx)

    def is_font_key(k: SourceKey) -> bool:
        if k in deps:
            return False
        if k.ns not in PACKAGE_NAMESPACES or k in signalled or FONT_PACKAGE.search(k.key):
            return True
        return resolve(k, idx) is not None

    return is_font_key


def ranks(vols: Mapping[SourceKey, float]) -> dict[SourceKey, int]:
    """1 + the number of keys with a larger volume (ties share the better rank)."""
    desc = sorted(-v for v in vols.values())
    return {key: bisect_left(desc, -v) + 1 for key, v in vols.items()}


def threshold(src: SourceBase) -> float:
    """The volume under which the engine censors a key: ``floor + censor_below``."""
    return src.floor + src.censor_below


def unit_label(unit: str, rate: str) -> str:
    """How the report names a volume: "installs a year", "downloads a month", "share"."""
    match rate:
        case "per_year":
            return f"{unit} a year"
        case "per_month":
            return f"{unit} a month"
        case "share":
            return "share"
    return unit


def report_rows(
    sources: Mapping[str, SourceBase],
    recs: RecordsBy,
    idx: AliasIndex,
    *,
    under_floor: bool = False,
    bundles: Collection[tuple[str, str]] = frozenset(),
) -> tuple[list[Unmatched], dict[str, float]]:
    """The report's rows (one per unmatched key at or above its source's floor;
    with ``under_floor``, every ranked unmatched key) and ``{engine source:
    floor}`` for every enabled source that has rows to read. A key whose
    ``(ns, match_key)`` is in ``bundles`` (``bundle_rows``) counts as matched."""
    is_font_key = font_key_filter(_relations(recs), idx)
    rows: list[Unmatched] = []
    floors: dict[str, float] = {}
    for name, src in sources.items():
        if not src.enabled:
            continue
        _, obs = engine_observations(src, recs)
        if not obs:
            continue
        vols = {k: v for k, v in volumes(src, obs).items() if v > 0 and is_font_key(k)}
        floors[name] = threshold(src)
        label = unit_label(_common_unit(obs), src.rate)
        for key, rank in ranks(vols).items():
            listed = under_floor or vols[key] >= floors[name]
            bundled = (key.ns, match_key(key.key)) in bundles
            if listed and not bundled and resolve(key, idx) is None:
                value = vols[key] if src.publish_raw else None
                rows.append(Unmatched(name, key, value, label, rank))
    rows.sort(key=_unmatched_order)
    return rows, floors


def _relations(recs: RecordsBy) -> list[Relation]:
    return [r for rows in recs.values() for r in rows if isinstance(r, Relation)]


def _common_unit(obs: Sequence[Observation]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for o in obs:
        counts[o.unit] += 1
    return min(counts, key=lambda u: (-counts[u], u))


# --- frames and evidence states ------------------------------------------------------------------

FrameBasis = Literal["packaged", "listed", "web_servable"]
# Design-m1 gap G11: crawls whose frame is "could have shown the font".
WEB_GROUPS = frozenset({"fot", "http_archive"})
LIST_GROUPS = frozenset({"google"})  # a Google-only list: a non-Google font is outside it
WEB_SERVABLE_NS = frozenset({"gf-family", "gf-dir", "fs-id", "foundry-family"})


@dataclass(frozen=True, slots=True)
class Frame:
    """The eligible families one engine source could have shown (methodology §3)."""

    basis: FrameBasis
    namespaces: tuple[str, ...]  # the key namespaces the engine reads, sorted
    families: tuple[str, ...]  # family ids, sorted

    def __contains__(self, family_id: object) -> bool:
        if not isinstance(family_id, str):
            return False
        i = bisect_left(self.families, family_id)
        return i < len(self.families) and self.families[i] == family_id


def frame_basis(src: SourceBase) -> FrameBasis:
    if src.group in WEB_GROUPS:
        return "web_servable"
    return "listed" if src.group in LIST_GROUPS else "packaged"


def frames(
    sources: Mapping[str, SourceBase], universe: Universe, recs: RecordsBy, idx: AliasIndex
) -> dict[str, Frame]:
    """``{engine source: Frame}`` for every enabled source with rows to read."""
    eligible = {fid: fam.keys for fid, fam in universe.families.items() if fam.drop is None}
    relations = _relations(recs)
    out: dict[str, Frame] = {}
    for name, src in sources.items():
        if not src.enabled:
            continue
        collector, obs = engine_observations(src, recs)
        if not obs:
            continue
        basis = frame_basis(src)
        ns = frozenset(o.key.ns for o in obs)
        reads = _reader(ns, selector(src))
        own = {k for r in recs.get(collector, ()) for k in _ends(r) if reads(k)}
        if basis == "web_servable":
            known = [fid for fid, keys in eligible.items() if _any_ns(keys, WEB_SERVABLE_NS)]
        else:
            known = [fid for fid, keys in eligible.items() if any(reads(k) for k in keys)]
        if basis == "packaged":
            own |= {k for r in relations for k in (r.subject, r.object) if reads(k)}
        fams = set(known) | {fid for fid in _families(own, idx) if fid in eligible}
        out[name] = Frame(basis, tuple(sorted(ns)), tuple(sorted(fams)))
    return out


def _reader(
    namespaces: frozenset[str], selects: Callable[[SourceKey], bool]
) -> Callable[[SourceKey], bool]:
    """Whether a key is one a source reads: in its namespaces and selected."""
    return lambda key: key.ns in namespaces and selects(key)


def _ends(rec: Observation | Relation) -> tuple[SourceKey, ...]:
    return (rec.subject, rec.object) if isinstance(rec, Relation) else (rec.key,)


def _any_ns(keys: Iterable[SourceKey], namespaces: frozenset[str]) -> bool:
    return any(k.ns in namespaces for k in keys)


def _families(keys: Iterable[SourceKey], idx: AliasIndex) -> set[str]:
    out = set()
    for key in keys:
        hit = resolve(key, idx)
        if hit is not None and hit[0] is not None:
            out.add(hit[0])
    return out


def evidence_state(
    frame: Frame,
    family_id: str,
    value: float | None,
    censor_below: float,
    *,
    exposed: bool = True,
) -> tuple[EvidenceState, Reason | None]:
    """One family's evidence state for one source (methodology §3), with its reason.

    ``value`` is after credits and the floor; ``exposed`` is False while the
    family is under ``min_exposure_days`` on the channel. In order: outside the
    frame is ``not_covered`` (``no_package`` for a package source,
    ``outside_frame`` for a list or crawl); too new is ``too_new``; no value is
    ``censored`` (``no_value``); under ``censor_below`` is ``censored``
    (``below_floor``); anything else is ``observed``.
    """
    if family_id not in frame:
        return "not_covered", "no_package" if frame.basis == "packaged" else "outside_frame"
    if not exposed:
        return "too_new", None
    if value is None:
        return "censored", "no_value"
    if value < censor_below:
        return "censored", "below_floor"
    return "observed", None


# --- the report ------------------------------------------------------------------------------------

_HEADER = """\
# Unmatched source keys

Keys that reached no family and no ineligible row in the alias index, at or
above each source's floor, largest first. Nothing is guessed: resolve a key
with a row in `data/aliases.csv` (its family, or `ineligible` with a reason);
the owner reviews them at gate U.

Rank is the key's position among the font keys its source reports (1 is the
largest). Google and Fonts Over Time show ranks only, never values (rulings T2
and T4). The floor is the volume under which the engine censors a key.
"""


def unmatched_report(unmatched: Iterable[Unmatched], floors: Mapping[str, float]) -> str:
    """``build/unmatched.md``: keys above each source's floor, largest first.

    Only sources in ``floors`` are reported, every one of them in the summary.
    A row is listed when its value is positive and at least its source's floor,
    or when its value is None but it has a rank: its source hides values, and
    the caller has applied the floor. A (source, key) given twice keeps its
    best row. Deterministic for any input order.
    """
    best: dict[tuple[str, SourceKey], Unmatched] = {}
    for u in unmatched:
        if u.source in floors and _listed(u, floors[u.source]):
            prev = best.get((u.source, u.key))
            if prev is None or _unmatched_order(u) < _unmatched_order(prev):
                best[u.source, u.key] = u
    by_source: dict[str, list[Unmatched]] = defaultdict(list)
    for u in sorted(best.values(), key=_unmatched_order):
        by_source[u.source].append(u)
    parts = [_HEADER, _summary(floors, by_source)]
    parts += [_section(name, by_source[name]) for name in sorted(by_source)]
    return "\n".join(parts)


def _listed(u: Unmatched, floor: float) -> bool:
    if u.value is None:
        return u.rank is not None
    return u.value > 0 and u.value >= floor


def _summary(floors: Mapping[str, float], by_source: Mapping[str, list[Unmatched]]) -> str:
    lines = ["| Source | Floor | Unmatched keys |", "|---|---:|---:|"]
    for name in sorted(floors):
        rows = by_source.get(name, [])
        unit = rows[0].unit if rows else ""
        floor = _fmt(floors[name], unit) + (f" {unit}" if unit and unit != "share" else "")
        lines.append(f"| {name} | {floor} | {len(rows):,} |")
    if not floors:
        lines.append("| (no source) | | 0 |")
    return "\n".join(lines) + "\n"


def _section(name: str, rows: Sequence[Unmatched]) -> str:
    unit = rows[0].unit
    hidden = all(u.value is None for u in rows)
    head = unit[0].upper() + unit[1:] if unit and not hidden else "Value"
    lines = [f"## {name}", "", f"| Rank | Key | Namespace | {head} |", "|---:|---|---|---:|"]
    for u in rows:
        rank = f"{u.rank:,}" if u.rank is not None else "-"
        value = _fmt(u.value, u.unit) if u.value is not None else "rank only"
        lines.append(f"| {rank} | {_code(u.key.key)} | {u.key.ns} | {value} |")
    return "\n".join(lines) + "\n"


def _code(text: str) -> str:
    """``text`` as inline code that a Markdown table keeps whole."""
    return "`" + _printable(text).replace("|", "\\|").replace("`", "'") + "`"


def _printable(text: str) -> str:
    """``text`` with every invisible or control character written as ``\\uXXXX``,
    so a crawled name with a newline or a no-break space stays on one visible line."""
    return "".join(c if c == " " or c.isprintable() else f"\\u{ord(c):04x}" for c in text)


def _fmt(value: float, unit: str) -> str:
    if unit == "share":
        return f"{value * 100:.3g}%"
    if abs(value) >= 100 or value == int(value):
        return f"{round(value):,}"
    return f"{value:.3g}"


# --- the stage -------------------------------------------------------------------------------------

REPORT_FILE = "unmatched.md"  # under build/, committed


@dataclass(frozen=True, slots=True)
class Inputs:
    """What stage "map" reads."""

    idx: AliasIndex
    current: dict[str, list[Observation | Relation]]  # collector -> this run's records
    baselines: list[Observation | Relation]  # lifetime-counter baselines (<source>@<date>.jsonl)
    bundles: Mapping[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)


def bundle_rows(path: Path) -> dict[tuple[str, str], tuple[str, ...]]:
    """(ns, match_key) -> every family a ``bundle`` row of ``data/aliases.csv`` credits
    from that key (D2), sorted; {} without the file.

    Bundle keys stay out of the alias index (one family per key): stage
    "correct" links them to their families, and the report counts them as matched.
    """
    if not path.is_file():
        return {}
    from tff_catalog.aliases import load_aliases  # the aliases stage is heavy to import

    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in load_aliases(path):
        if row.relation == "bundle" and row.family_id:
            out[(row.ns, match_key(row.alias))].add(row.family_id)
    return {k: tuple(sorted(v)) for k, v in sorted(out.items())}


def load_inputs(paths: Paths) -> Inputs:
    """Read the alias index, the bundle rows and every records file; a malformed
    index raises ``ValueError``."""
    idx = index_of(_load_stage(paths, "alias_index"))
    problems = index_problems(idx)
    if problems:
        more = f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""
        raise ValueError("alias index: " + "; ".join(problems[:5]) + more)
    if not paths.records.is_dir():
        raise FileNotFoundError(f"{paths.records}: no records; run stage 'parse' first")
    current: dict[str, list[Observation | Relation]] = defaultdict(list)
    baselines: list[Observation | Relation] = []
    for path in sorted(paths.records.glob("*.jsonl")):
        rows = [r for r in read_jsonl(path) if isinstance(r, Observation | Relation)]
        if "@" in path.stem:
            baselines += rows
        else:
            for r in rows:
                current[r.source].append(r)
    return Inputs(idx, dict(current), baselines, bundle_rows(paths.aliases_csv))


def _load_stage(paths: Paths, name: str) -> Any:
    """``stageio.load_stage``, naming the stage to run first when the file is missing."""
    path = stageio.stage_path(paths, name)
    if not path.is_file():
        producer = stageio.STAGE_FILES[name].producer
        raise FileNotFoundError(f"{path}: missing; run stage {producer!r} first")
    return stageio.load_stage(paths, name)


# --- gate U questions ----------------------------------------------------------------------------

QUEUE_FILE = "unmatched.json"  # under build/stage/queues/
QUESTION_TOP = 200  # milestone-1 step 9: no source has an unmatched key in its top 200
QUESTION_OPTIONS = (
    "A family's name or package: add an alias row (record its family_id)",
    "Not a font we list: add an ineligible row (record the reason)",
    "Research more",
)


def queue_questions(ranked: Iterable[Unmatched], top: int = QUESTION_TOP) -> list[Question]:
    """Gate U: one question per unmatched key in a source's top ``top`` (under the floor
    too), at its best rank. A source whose ``publish_raw`` is false shows its rank only.
    The id is a hash of the key, so each question pins the key its answer is about."""
    from tff_catalog.reviews import Question

    best: dict[SourceKey, Unmatched] = {}
    for u in ranked:
        if u.rank is None or u.rank > top:
            continue
        old = best.get(u.key)
        if old is None or (u.rank, u.source) < (old.rank or 0, old.source):
            best[u.key] = u
    out = []
    for key, u in sorted(best.items(), key=lambda kv: (kv[1].rank or 0, kv[1].source, kv[0])):
        name = f"{key.ns}:{_printable(key.key)}"
        digest = hashlib.sha256(f"{key.ns}\n{key.key}".encode()).hexdigest()[:12]
        value = "" if u.value is None else f", {_fmt(u.value, u.unit)}"
        out.append(
            Question(
                "U",
                f"U-{digest}",
                f"`{name}` matches no family ({u.source} rank {u.rank}{value}). It is resolved "
                f'with a row in data/aliases.csv. Record the answer with key = "{name}".',
                QUESTION_OPTIONS,
            )
        )
    return out


def questions(paths: Paths) -> list[Question]:
    """Gate U's open questions from the last run's queue (for ``reviews.questions``)."""
    from tff_catalog.reviews import questions_from_file

    return questions_from_file(paths.queues / QUEUE_FILE)


def run(ctx: StageContext) -> None:
    """Stage "map"."""
    paths, sources = ctx.paths, ctx.config.ranking.sources.all()
    inputs = load_inputs(paths)
    recs = [r for rows in inputs.current.values() for r in rows] + inputs.baselines
    mapped, unmatched = map_records(recs, inputs.idx)
    stageio.dump_stage(paths, "mapped", mapped)
    rows, floors = report_rows(sources, inputs.current, inputs.idx, bundles=inputs.bundles)
    report = unmatched_report(rows, floors)
    jsonio.atomic_write(paths.build / REPORT_FILE, report.encode("utf-8"))
    ranked, _ = report_rows(
        sources, inputs.current, inputs.idx, under_floor=True, bundles=inputs.bundles
    )
    asked = queue_questions(ranked)
    jsonio.dump([dataclasses.asdict(q) for q in asked], paths.queues / QUEUE_FILE)
    _log_counts(ctx.log, mapped, unmatched, rows)


def _log_counts(
    log: logging.Logger,
    mapped: Sequence[Mapped],
    unmatched: Sequence[Unmatched],
    rows: Sequence[Unmatched],
) -> None:
    keys: dict[str, dict[str, set[SourceKey]]] = defaultdict(lambda: defaultdict(set))
    for m in mapped:
        kind = "ineligible" if m.family_id is None else "mapped"
        keys[m.record.source][kind].add(_primary(m.record))
    for u in unmatched:
        keys[u.source]["unmatched"].add(u.key)
    for source in sorted(keys):
        counts = {kind: len(keys[source][kind]) for kind in ("mapped", "ineligible", "unmatched")}
        log.info("%s: %s keys", source, ", ".join(f"{v} {k}" for k, v in counts.items()))
    log.info("%d unmatched keys at or above their source's floor", len(rows))


def cmd_check_unmatched(ctx: StageContext, *, top: int) -> int:
    """``map --check-unmatched N``: non-zero if any source has an unmatched key in its top N.

    Every ranked key counts, also one under its source's floor (such a key is
    not in ``unmatched.md``, and the output says so): milestone-1 step 9 is done
    when no source has an unmatched key in its top 200.
    """
    inputs = load_inputs(ctx.paths)
    sources = ctx.config.ranking.sources.all()
    ranked, _ = report_rows(
        sources, inputs.current, inputs.idx, under_floor=True, bundles=inputs.bundles
    )
    offenders = [u for u in ranked if u.rank is not None and u.rank <= top]
    if not offenders:
        print(f"no source has an unmatched key in its top {top}")
        return 0
    shown, _ = report_rows(sources, inputs.current, inputs.idx, bundles=inputs.bundles)
    listed = {(u.source, u.key) for u in shown}
    by_source: dict[str, list[Unmatched]] = defaultdict(list)
    for u in offenders:
        by_source[u.source].append(u)
    for name in sorted(by_source):
        found = by_source[name]
        print(f"{name}: {len(found)} unmatched key(s) in its top {top}:")
        for u in found:
            note = "" if (name, u.key) in listed else " (under the floor; not in unmatched.md)"
            print(f"  #{u.rank} {u.key.ns}:{_printable(u.key.key)}{note}")
    return 1
