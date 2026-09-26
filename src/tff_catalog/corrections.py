"""Stage "correct": confound corrections and evidence states (milestone-1 step 10). Owner: agent P6.

Turns mapped values into one term per (view, source, family):

- sums a family's packages and aliases, with Nerd, CJK and bundle credits;
- subtracts noise floors (Homebrew, including the gate-M3 Nerd floor; the Arch
  Nerd group floor; Nerd release deltas) and censors values under the floor;
- counts exposure from each channel's data date ("too new" under 60 days);
- makes Linux sources abstain, in every view except ``desktop_installed``, for
  fonts a Linux system preinstalls or whose top reverse dependency holds at
  least ``dependency_abstain`` of its installs;
- tags ``preinstalled_on`` and ``pulled_in_by``;
- gives each term its family's independence group (gate M5) and its weight
  factor and flags (the Almanac parent merge).

Writes:

- ``build/stage/terms.json``: {view: {source: {id: Term}}}, schema
  ``schemas/stage/terms.schema.json``;
- ``build/stage/ruler_counts.json``: {id: count}, the ruler's input
  (``ruler_counts``), schema ``schemas/stage/ruler_counts.schema.json``;
- ``build/stage/tags.json`` ({id: Tags}) and ``build/corrections.md``;
- ``build/stage/queues/corrections.json``: every gate-X case as a single-select
  question (``questions``), for ``tff-catalog questions --gate X``;
- its part of the next state, ``first_seen`` (``state.write_part``).

``build/corrections.md`` is committed: sources whose ``publish_raw`` is false
(Google and Fonts Over Time, rulings T2 and T4) appear in it only as ranks or
z scores, never as values.

**Reads** ``build/stage/universe.json``, ``mapped.jsonl`` and, when present,
``latin.json``, ``licenses.json`` and ``alias_index.json`` (``stageio``); the
parsed records in ``build/stage/records/`` (every package count, dependency and
group edge, cask URLs, universe ``added`` dates, and the ``<collector>@<date>``
baselines of lifetime counters); the ``bundle`` rows of ``data/aliases.csv``
(D2: one key crediting several families, which the alias index cannot hold);
``state/first_seen.json`` (``ctx.state``); ``config/ranking.toml``,
``config/preinstalled.toml``; gate X rulings (``data/reviews/corrections/``).

**Per engine source** (``ranking.toml [sources]``, enabled ones):

1. select its observations: collector, series (``YYYY-MM``/``YYYY-Www`` are
   patterns), npm scopes, Nerd ``exclude_assets``, FOT ``methods``, the GitHub
   ``release_history`` (gate M2); prereleases count only for the GitHub repos in
   ``prerelease_repos`` (repos that publish nothing else);
2. count one number per source key (``count_keys``, by ``counting``), as a rate
   in the source's ``rate`` unit over min(window, days available), where the
   days available run from the key's ``first_seen`` attr (design-m1 gap G3);
   a window within a few days of a year or a month counts as exactly one;
   for the crawls, keys that differ only in case count once, at the larger
   value (the Almanac's case duplicates: max, not sum);
3. subtract the key-level floors, before any credit (``key_floors``): gate M3
   (Nerd casks, once per cask with its legacy tap keys), gate M4 (the Arch
   nerd-fonts group, tenured members only) and the Nerd release p10;
4. sum each family's keys with credits (``family_sums``; ``sum_aliases`` and
   ``apply_credits`` are the same rules on raw values); a bundle key credits
   each of its families (``bundle_credit``, or 1/n when ``bundle_mode = "split"``);
5. subtract ``floor`` and censor (``apply_floors``); a family in the source's
   frame with no value is censored (``no_value``); exposure under
   ``min_exposure_days`` makes the term ``too_new``;
6. set the group (gate M5), flags and factor (Almanac parent merge).

The frame of a source is every eligible family with a key in it, or with a
universe key in the same namespace (a Homebrew cask with no analytics row);
for the crawls (``weekly_mean`` and ``yearly`` counting) it is every eligible
family, as methodology §4 says ("a desktop-only font gets censored terms from
the web crawls, not a placeholder"; design-m1 gap G11's web-servable frame was
never ruled on). Families outside the frame get no term, which reads as not
covered.

Exposure (gap G3): ``add_date``, ``first_nonzero_day`` and ``asset_created``
come from the keys' ``first_seen`` attrs, ``first_nonzero_month`` from the
monthly series, ``date_added`` from the ``added`` date of the universe record
with the same key (Google's dateAdded; the views become a rate over the days
since); an earlier date for the same source in ``state/first_seen.json`` wins.
The crawls (``data_date``) count from the earliest date the family is known to
exist (``availability``). A family with no known start counts as covered. The
next ``first_seen`` part records a source's date for a family only when the
channel gives one (a channel exposure with a known start), so a font the
first run meets in Debian or a crawl is not taken for a new one. Fonts Over
Time's rows count only its ``methods``, with startups capped at
``startup_cap`` of each week's weight (D11, ``startup_capped``). Not done here:
Coding's ``monospace_only`` is left to stage "rank", which reads the facts.

**Rising** (D13). ``terms.json["rising"]`` holds each rising source's recent
window (``RISING_WINDOWS``: Homebrew's 30 days, the latest month of a monthly
series, Google's 30 days), corrected like the 12-month term (key floors,
credits, floor, censoring) over the same families, with the same abstentions;
stage "rank" compares its shares with the 12-month ones. A source with no
recent window (jsDelivr's term is already monthly) or no rows for it is left
out of the view.

**Linux sources** (``linux = true``) abstain for a family, in every view whose
rank has ``abstain = true`` (all but ``desktop_installed``), when a Linux
system in ``preinstalled.toml`` ships it, or when one package (gate M8
``dependency_rule = "top"``) pulls in at least ``dependency_abstain`` of its
installs: share = min(dependent's installs, pulled package's installs) / the
family's installs, where the family's installs are those of its most-installed
package in that source (one system counts once per package, so a sum would
double-count). Only ``depends`` and ``recommends`` edges pull; with
``dependency_alternatives = "first"`` only the first of ``a | b`` does
(``Relation.alt == 0``). Optional dependencies, groups and virtual provides
never pull. A bundle package pulls in every family it credits. Shares from
``dependency_review`` up to ``dependency_abstain`` are flagged. An abstaining
term is simply absent from that view.

**Gate X.** Every abstention and review flag is a case with an id such as
``dep-debian-quicksand`` (``case_id``). ``build/corrections.md`` marks the cases
no ruling in ``data/reviews/corrections/`` names yet as **new**. A ruling with
choice "a" accepts the computed handling; choice "b" reverses it (the source
keeps counting the font, or a flagged font abstains). When the owner overrules
a preinstalled case, the dependency rule still applies to that font (a case
of its own). The queue (``questions``) lists every case; ``reviews`` leaves out
the answered ones.
"""

import copy
import hashlib
import math
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tff_catalog import jsonio, reviews, stageio, state
from tff_catalog.config_model import (
    AlmanacSource,
    ArchSource,
    EcosystemsSource,
    FotSource,
    GithubSource,
    GoogleSource,
    HomebrewSource,
    NerdSource,
)
from tff_catalog.keys import match_key
from tff_catalog.mapping import bundle_rows, frame_basis, in_scopes
from tff_catalog.names import NERD_SUFFIX
from tff_catalog.records import (
    EXPOSURE_ATTR_KINDS,
    Observation,
    Record,
    Relation,
    SourceKey,
    UniverseRecord,
    read_jsonl,
    sort_key,
)

if TYPE_CHECKING:
    import logging

    from tff_catalog.config_model import (
        Corrections,
        PreinstalledConfig,
        RankingConfig,
        SourceBase,
    )
    from tff_catalog.mapping import IndexEntry, Mapped
    from tff_catalog.paths import Paths
    from tff_catalog.stages import StageContext
    from tff_catalog.universe import Family, Universe

EvidenceState = Literal["observed", "censored", "not_covered", "too_new"]
Reason = Literal["below_floor", "no_value", "no_package", "outside_frame", "merged_into_parent"]
# Flags a term may carry; review.md lists them. Owner of this list: agent P6.
TERM_FLAGS = frozenset(
    {
        "parent_merge",  # Almanac: the name regex folded width cuts into this parent (§5)
        "bundle_only",  # counted only through a bundle (D2)
        "dependency_review",  # a Linux dependent holds 35-50% of installs (gate M8)
        "stale_sync",  # ecosyste.ms last_synced_at older than stale_sync_days
    }
)

# Alias relations whose counts go to the family (design-m1 gap G5); sibling,
# related and distinct rows only block matches.
COUNTED_RELATIONS = frozenset({"direct", "rename", "build", "package", "postscript", "bundle"})
NERD_DETAILS = frozenset({"nerd", "nf", "nfm", "nfp", "propo", "powerline"})  # patched builds (D7)
CJK_DETAILS = frozenset({"cjk"})
_NERD_TOKENS = frozenset({"nerd", "nf", "nfm", "nfp"})
_CJK_TOKENS = frozenset({"cjk"})
# "cn" marks a CJK build only as a package or cask suffix (font-maple-mono-nf-cn); as a
# word of a name it means condensed ("Martian Mono Cn", "TeX Gyre Heros Cn").
_CJK_SUFFIXES = ("-cn",)
PULLING_KINDS = frozenset({"depends", "recommends"})  # pacman optdepends are never auto-installed
NERD_GROUP = "nerd-fonts"  # the Arch package group of gate M4
NERD_REPO = "ryanoasis/nerd-fonts"  # where Nerd casks download from (gate M5)
CRAWL_COUNTINGS = frozenset({"weekly_mean", "yearly"})
CHANNEL_EXPOSURES = frozenset(
    {"add_date", "first_nonzero_day", "first_nonzero_month", "date_added", "asset_created"}
)
# Preinstalled names resolve through these alias namespaces when no family has the name.
NAME_NAMESPACES = ("font-name", "gf-family", "foundry-family", "fontist-formula", "system")
GATE = "X"
REPORT = "corrections.md"  # under build/
QUEUE = "corrections.json"  # under build/stage/queues/: the new gate-X cases
_CASE_PREFIX = {"preinstalled": "pre", "dependency": "dep", "review": "rev"}
_WIDTH_CUT = re.compile(
    r"\s+(?:(?:semi|extra|ultra)[\s-]?)?(?:condensed|narrow|compressed)\Z|\s+black\Z",
    re.IGNORECASE,
)
_GITHUB_ASSET = re.compile(r"https://github\.com/([^/\s]+/[^/\s]+)/releases/download/", re.I)
_SERIES_PLACEHOLDERS = {"YYYY-MM": r"[0-9]{4}-[0-9]{2}", "YYYY-Www": r"[0-9]{4}-W[0-9]{2}"}
# Rising (D13): each rising engine source's recent window, the Observation.series it
# reads; a placeholder series means its latest period. The names follow the collector
# specs (design-m1 §2.4); a source missing here or without rows is left out of Rising.
# jsDelivr's term is already one month, so it has no shorter window.
RISING_WINDOWS: dict[str, str] = {
    "homebrew": "30d",
    "arch": "YYYY-MM",
    "npm_fontsource": "YYYY-MM",
    "npm_expo": "YYYY-MM",
    "google": "30day",
}
_USED = frozenset({"observed", "censored"})  # the states that make a term
_YEAR = 365.0
_MONTH = 365.0 / 12
_MIN_BASELINE_GAP = timedelta(days=28)  # a baseline must be about a month older ...
_MAX_BASELINE_GAP = timedelta(days=372)  # ... and at most 12 months older (methodology §5)


@dataclass(frozen=True, slots=True)
class Term:
    """One family's evidence from one engine source in one view.

    - ``value``: after credits and floors; None unless observed or censored.
    - ``group``: the family's independence group for this source: the source's
      ``group``, except that the GitHub counters (``github``, ``nerd``) take
      ``homebrew`` for a family whose Homebrew cask downloads that repository's
      release asset (gate M5 (a), ``engine.github_homebrew_same_group``). The
      2-group gate and tier A count distinct groups among observed terms.
    - ``factor``: a weight multiplier for this family. The engine uses
      w' = w_eff * factor * guard factor. The Almanac parent merge ("flagged and
      halved", methodology §5) is ``factor = almanac.parent_merge_factor`` plus
      the flag ``parent_merge``; it never changes ``value``.
    - ``flags``: sorted, from ``TERM_FLAGS``.
    """

    value: float | None
    state: EvidenceState
    group: str  # records.GROUPS
    reason: Reason | None = None
    factor: float = 1.0
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Tags:
    """One family's tags, ``build/stage/tags.json`` ({id: Tags})."""

    preinstalled_on: tuple[str, ...] = ()  # preinstalled.toml system ids, sorted
    pulled_in_by: tuple[tuple[str, str], ...] = ()  # (system, package), sorted
    flags: tuple[str, ...] = ()  # "no_deliberate_evidence" when abstentions left no desktop term


@dataclass(frozen=True, slots=True)
class Abstention:
    source: str
    family_id: str
    why: Literal["preinstalled", "dependency"]
    system: str  # the preinstalled system, or the dependency's distribution
    package: str | None = None  # the pulling package
    share: float | None = None  # its share of installs


@dataclass(frozen=True, slots=True)
class Dependent:
    """A package that pulls a font in on a Linux system."""

    system: str  # config/site.toml [package_systems]: arch, cachyos, debian
    package: str
    share: float  # of the family's installs in the source, at most 1


@dataclass(frozen=True, slots=True)
class KeyValue:
    """One source key after counting: a rate in the source's ``rate`` unit."""

    value: float | None  # None: the source lists the key without a usable value
    start: date | None  # first day on the channel, when known; None: unknown or before the window
    end: date  # the key's data date


@dataclass(frozen=True, slots=True, order=True)
class Link:
    """A source key counted toward a family (a ``mapped.jsonl`` row)."""

    key: SourceKey
    family_id: str
    relation: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Part:
    """One key's share of a family's value, for the report."""

    key: SourceKey
    value: float | None  # after key floors, before the credit
    credit: float
    code: str  # the credit code (``apply_credits``)


@dataclass(frozen=True, slots=True)
class FamilySum:
    value: float | None  # None when every key lacks a value
    parts: tuple[Part, ...]
    bundle_only: bool


@dataclass(frozen=True, slots=True)
class FloorNote:
    """A floor computed this run, for the report."""

    source: str
    what: str
    value: float
    members: int  # values the quantile was taken over


@dataclass(frozen=True, slots=True)
class Case:
    """A preinstalled, dependency or review case for the owner (gate X)."""

    id: str
    kind: Literal["preinstalled", "dependency", "review"]
    source: str
    family_id: str
    system: str
    package: str | None
    share: float | None
    status: Literal["new", "accepted", "overruled"]

    @property
    def abstains(self) -> bool:
        """Whether the source abstains for the family after the owner's ruling."""
        return (self.kind == "review") == (self.status == "overruled")


@dataclass(frozen=True, slots=True)
class Inputs:
    """What stage "correct" reads, resolved to eligible families (``load_inputs``).

    - ``families``: eligible families (universe minus drops, Latin and license gates);
    - ``links``: counted mapped keys of eligible families;
    - ``observations``: collector -> every observation an engine source may read;
    - ``baselines``: collector -> (date, observations) of earlier lifetime-counter snapshots;
    - ``relations``: every dependency, group and preinstall edge;
    - ``relation_families``: pkg_id -> family of a relation's pulled package (stage "map");
    - ``universe_keys``: universe key -> eligible family;
    - ``added``: family -> earliest universe ``added`` date of any source;
    - ``added_keys``: (ns, match_key) of a universe key -> its earliest ``added``
      date (Google's dateAdded for a ``gf-family`` key);
    - ``cask_repos``: family -> GitHub repos its Homebrew casks download from (gate M5);
    - ``names``: preinstalled.toml family name -> family;
    - ``ineligible_names``: preinstalled.toml names of universe families the gates
      removed (not reported as unresolved);
    - ``first_seen``: the committed ``state/first_seen.json``.
    """

    families: Mapping[str, Family]
    links: Mapping[SourceKey, tuple[Link, ...]]
    observations: Mapping[str, tuple[Observation, ...]]
    baselines: Mapping[str, tuple[tuple[date, tuple[Observation, ...]], ...]] = field(
        default_factory=dict
    )
    relations: tuple[Relation, ...] = ()
    relation_families: Mapping[str, str] = field(default_factory=dict)
    universe_keys: Mapping[SourceKey, str] = field(default_factory=dict)
    added: Mapping[str, date] = field(default_factory=dict)
    added_keys: Mapping[tuple[str, str], date] = field(default_factory=dict)
    cask_repos: Mapping[str, frozenset[str]] = field(default_factory=dict)
    names: Mapping[str, str] = field(default_factory=dict)
    ineligible_names: frozenset[str] = frozenset()
    first_seen: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceResult:
    """One engine source after correction (before Linux abstentions)."""

    name: str
    src: SourceBase
    data_date: date
    terms: dict[str, Term]
    sums: dict[str, FamilySum]  # after key floors and credits
    unfloored: dict[str, float]  # credits, no floor at all: the ruler's input
    counts: dict[str, float]  # pkg_id -> counted value before floors and credits
    adjusted: dict[SourceKey, tuple[float, float]]  # keys a key floor changed: (before, after)
    floors: tuple[FloorNote, ...]
    seen: dict[str, date]  # family -> first day on this channel, when known (state first_seen)
    key_starts: dict[SourceKey, date | None] = field(default_factory=dict)  # counted starts
    shrunk: tuple[SourceKey, ...] = ()  # lifetime counters below their baseline (clamped to 0)
    window: str = ""  # the series read, or its latest period (Rising's recent window)


# --- small helpers -----------------------------------------------------------------------------


def pkg_id(key: SourceKey) -> str:
    """``"ns:key"``: how ``counts`` and ``families`` name a package or a font name."""
    return f"{key.ns}:{key.key}"


def quantile(values: Iterable[float], q: float) -> float | None:
    """The ``q`` quantile with linear interpolation (numpy's default); None if empty."""
    xs = sorted(values)
    if not xs:
        return None
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def case_id(kind: str, source: str, family_id: str) -> str:
    """The gate-X question id of a case: ``dep-debian-quicksand`` (at most 64 characters)."""
    base = f"{_CASE_PREFIX[kind]}-{source}-{family_id}"
    if len(base) <= 64:
        return base
    return f"{base[:55]}-{hashlib.sha256(base.encode()).hexdigest()[:8]}"


@cache
def series_pattern(series: str) -> re.Pattern[str]:
    """``ranking.toml`` ``series`` as a pattern: ``YYYY-MM`` and ``YYYY-Www`` match any date."""
    pattern = re.escape(series)
    for placeholder, regex in _SERIES_PLACEHOLDERS.items():
        pattern = pattern.replace(re.escape(placeholder), regex)
    return re.compile(pattern)


def _attr(r: Observation | Relation, name: str) -> Any:
    return dict(r.attrs).get(name)


def _attr_date(r: Observation | Relation, name: str) -> date | None:
    value = _attr(r, name)
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _earliest(dates: Iterable[date | None]) -> date | None:
    known = [d for d in dates if d is not None]
    return min(known) if known else None


def _by_key(obs: Iterable[Observation]) -> dict[SourceKey, list[Observation]]:
    out: dict[SourceKey, list[Observation]] = defaultdict(list)
    for o in obs:
        out[o.key].append(o)
    return dict(sorted(out.items()))


def _total(obs: Iterable[Observation]) -> float | None:
    values = [o.value for o in obs if o.value is not None]
    return math.fsum(values) if values else None


def _round(value: float | None) -> float | None:
    """Nine significant digits, so stage files stay readable and byte-stable."""
    return None if value is None else float(f"{value:.9g}")


def _pulled_share(dependents: Sequence[Dependent], rule: str) -> float:
    if not dependents:
        return 0.0
    if rule == "sum":
        return min(1.0, math.fsum(d.share for d in dependents))
    return dependents[0].share


# --- the step's rules (public helpers) ---------------------------------------------------------


def exposure(first_seen: date | None, data_date: date, min_days: int) -> bool:
    """Whether a channel has seen the font long enough (``False`` means "too new").

    ``first_seen`` None means no start date is known: the font counts as covered.
    """
    return first_seen is None or (data_date - first_seen).days >= min_days


def apply_credits(
    values: Mapping[str, float], details: Mapping[str, str], cfg: Corrections
) -> dict[str, float]:
    """Nerd, CJK and bundle credits (a build that is both NF and CN takes the smaller).

    Keys are package ids (``pkg_id``); a missing detail means full credit.
    ``details`` holds the credit code of each key: ``""`` (the family's own
    package), a build detail (``aliases.BUILD_DETAILS``: ``nerd``, ``nf``,
    ``powerline``... take ``nerd_credit``, ``cjk`` takes ``cjk_build_credit``,
    ``variable``, ``nl``... take 1), or ``"bundle:<n>"`` for a bundle shared by n
    families (``bundle_credit`` each, or 1/n when ``bundle_mode = "split"``).
    A build whose package name also carries the other marker (``nf`` and ``cn``
    in ``font-maple-mono-nf-cn``) takes the smaller of the two credits.
    """
    return {
        k: v * _credit(k.partition(":")[2] or k, details.get(k, ""), cfg) for k, v in values.items()
    }


def _credit(name: str, code: str, cfg: Corrections, *, nerd: bool = False) -> float:
    """The credit of one key. The key's own name decides too, whatever its alias relation:
    a Nerd cask or package ("font-0xproto-nerd-font") or a CJK build's suffix takes the
    build credit even when mapped ``direct``, and ``nerd`` (every Nerd Fonts release
    asset) always takes ``nerd_credit`` (D7)."""
    if code.startswith("bundle"):
        n = int(code.partition(":")[2] or 1)
        return cfg.bundle_credit if cfg.bundle_mode == "fixed" else 1.0 / max(n, 1)
    tokens = set(re.split(r"[^a-z0-9]+", name.lower()))
    credits = []
    if nerd or code in NERD_DETAILS or tokens & _NERD_TOKENS:
        credits.append(cfg.nerd_credit)
    if code in CJK_DETAILS or tokens & _CJK_TOKENS or name.lower().endswith(_CJK_SUFFIXES):
        credits.append(cfg.cjk_build_credit)
    return min(credits, default=1.0)


def _credit_code(link: Link, families: int) -> str:
    """A bundle link's detail is the bundle's size when known (every family it
    credits, eligible or not); else the families the key is linked to."""
    if link.relation == "bundle":
        return f"bundle:{link.detail if link.detail.isdigit() else families}"
    return link.detail if link.relation == "build" else ""


def family_sums(
    links: Iterable[Link],
    values: Mapping[SourceKey, float | None],
    cfg: Corrections,
    *,
    largest: bool = False,
    nerd: bool = False,
) -> dict[str, FamilySum]:
    """Each family's value: the sum of its keys' values times their credits, or with
    ``largest`` the largest of them (a Linux source under ``per_system_basis =
    "largest_package"``: one system counts once per package, so a sum double-counts).

    A key is counted once per family (duplicate links are ignored). A family
    whose keys all lack a value gets ``value`` None. ``nerd`` gives every key the
    Nerd credit (the Nerd Fonts release assets).
    """
    by_key: dict[SourceKey, dict[str, Link]] = defaultdict(dict)
    for link in sorted(set(links)):
        by_key[link.key].setdefault(link.family_id, link)
    parts: dict[str, list[Part]] = defaultdict(list)
    for key in sorted(by_key):
        fams = by_key[key]
        for fid, link in sorted(fams.items()):
            code = _credit_code(link, len(fams))
            credit = _credit(key.key, code, cfg, nerd=nerd)
            parts[fid].append(Part(key, values.get(key), credit, code))
    out = {}
    for fid, ps in sorted(parts.items()):
        known = [p.value * p.credit for p in ps if p.value is not None]
        total = (max(known) if largest else math.fsum(known)) if known else None
        out[fid] = FamilySum(
            value=total,
            parts=tuple(ps),
            bundle_only=all(p.code.startswith("bundle") for p in ps),
        )
    return out


def sum_aliases(mapped: Iterable[Mapped], cfg: Corrections) -> dict[str, dict[str, float]]:
    """{source: {family_id: value}} with packages summed and build/bundle credits applied.

    Works on raw record values: every Observation row of a source is summed per
    key, then per family. Rows without a family (ineligible) or whose relation
    does not count (``COUNTED_RELATIONS``) are skipped. The stage itself sums
    rates (``family_sums`` after ``count_keys``), by the same rules.
    """
    rows: dict[str, list[Mapped]] = defaultdict(list)
    for m in mapped:
        if isinstance(m.record, Observation) and m.family_id and m.relation in COUNTED_RELATIONS:
            rows[m.record.source].append(m)
    out = {}
    for source, ms in sorted(rows.items()):
        values = {key: _total(obs) for key, obs in _by_key(m.record for m in ms).items()}
        links = [Link(m.record.key, m.family_id or "", m.relation, m.detail) for m in ms]
        sums = family_sums(links, values, cfg)
        out[source] = {fid: s.value for fid, s in sums.items() if s.value is not None}
    return out


def apply_floors(values: Mapping[str, float], src: SourceBase) -> dict[str, Term]:
    """Subtract the source's floor and censor values under ``censor_below``.

    Observed needs a value above 0 and at least ``censor_below`` after the floor;
    anything else is censored (``below_floor``), at the floored value (never
    below 0).
    """
    out = {}
    for fid in sorted(values):
        v = values[fid] - src.floor
        if v > 0 and v >= src.censor_below:
            out[fid] = Term(v, "observed", src.group)
        else:
            out[fid] = Term(max(0.0, v), "censored", src.group, reason="below_floor")
    return out


def ruler_counts(credited: Mapping[str, float], cask_families: Iterable[str]) -> dict[str, float]:
    """The ruler's input (methodology §3, design-m1 §6), written to ``build/stage/ruler_counts.json``.

    Homebrew 365-day installs per family after the Latin and license gates,
    alias sums and credits, with **no floor** (neither the flat floor nor the
    gate-M3 Nerd floor), and 0 for a family with a cask but no analytics row.
    ``credited`` is ``sum_aliases`` output for ``homebrew``; ``cask_families``
    are the eligible families that have a Homebrew cask.
    """
    out = dict.fromkeys(cask_families, 0.0)
    for fid, value in credited.items():
        out[fid] = max(0.0, float(value))
    return dict(sorted(out.items()))


PackageFamilies = Mapping[str, str | tuple[str, ...]]  # pkg_id -> family, or a bundle's families


def _members(families: PackageFamilies, pid: str) -> tuple[str, ...]:
    fams = families.get(pid)
    if not fams:
        return ()
    return (fams,) if isinstance(fams, str) else tuple(fams)


def dependency_shares(
    deps: Iterable[Relation],
    counts: Mapping[str, Mapping[str, float]],
    cfg: Corrections,
    *,
    families: PackageFamilies,
) -> dict[str, dict[str, tuple[Dependent, ...]]]:
    """{source: {family_id: dependents, largest share first}} for the Linux sources.

    ``counts`` is {source: {pkg_id: installs}} with every package the source
    counts (fonts and the packages that depend on them); an edge belongs to a
    source when its pulled package is in that source's counts. ``families``
    maps package ids to a family, or to a bundle's families, which it pulls in
    together (``Relation.attrs["family_id"]`` is the fallback). A dependent of
    the family's own is not a dependent; a dependent pulling two of the
    family's packages counts once, at its larger share.
    """
    edges = sorted(
        (r for r in deps if r.kind in PULLING_KINDS),
        key=sort_key,
    )
    if cfg.dependency_alternatives == "first":
        edges = [r for r in edges if r.alt == 0]
    out: dict[str, dict[str, tuple[Dependent, ...]]] = {}
    for source in sorted(counts):
        pkgs = counts[source]
        installs: dict[str, float] = defaultdict(float)
        for pid, c in pkgs.items():
            for fid in _members(families, pid):
                installs[fid] = max(installs[fid], c)
        shares: dict[str, dict[tuple[str, str], float]] = defaultdict(dict)
        for rel in edges:
            pid = pkg_id(rel.object)
            if pid not in pkgs:
                continue
            fallback = _attr(rel, "family_id")
            fids = _members(families, pid) or (
                (fallback,) if isinstance(fallback, str) and fallback else ()
            )
            own = set(_members(families, pkg_id(rel.subject)))
            for fid in fids:
                total = max(installs.get(fid, 0.0), pkgs[pid])
                if fid in own or total <= 0:
                    continue
                share = min(1.0, min(pkgs.get(pkg_id(rel.subject), 0.0), pkgs[pid]) / total)
                who = (_system(rel, source), rel.subject.key)
                shares[fid][who] = max(shares[fid].get(who, 0.0), share)
        found = {}
        for fid, per in sorted(shares.items()):
            ds = sorted(
                (Dependent(s, p, round(v, 6)) for (s, p), v in per.items() if v > 0),
                key=lambda d: (-d.share, d.system, d.package),
            )
            if ds:
                found[fid] = tuple(ds)
        out[source] = found
    return out


def _system(rel: Relation, default: str) -> str:
    """The package system of a dependency edge: its reserved ``distro`` attr, else the
    source's own."""
    distro = _attr(rel, "distro")
    if isinstance(distro, str) and distro:
        return distro
    return default


def silences(system: str, source: str, cfg: Corrections) -> bool:
    """Whether preinstalled Linux system ``system`` makes Linux source ``source`` abstain."""
    return cfg.abstain_scope == "all" or source in cfg.abstain_sources.get(system, ())


def abstentions(
    deps: Iterable[Relation],
    counts: Mapping[str, Mapping[str, float]],
    preinst: PreinstalledConfig,
    cfg: Corrections,
    *,
    families: PackageFamilies | None = None,
) -> dict[str, dict[str, Abstention]]:
    """{source: {family_id: Abstention}} for the Linux sources (D8, gate M8).

    ``counts`` and ``families`` are as in ``dependency_shares``; ``families``
    also resolves preinstalled names under ``pkg_id(SourceKey("font-name",
    name))``. A family a Linux system of ``preinst`` ships (or a ``preinstalled``
    edge names) abstains in every Linux source; Windows, macOS and Android
    systems never cause an abstention. Otherwise a family abstains when its
    pulled share (``dependency_rule``) reaches ``dependency_abstain``.
    Preinstalled wins over a dependency. The system named is the source's own
    when it ships the family (Debian's DejaVu), else the first in id order.
    With ``abstain_scope = "by_package_system"`` (the owner's ruling of 2026-09-26) a
    system silences only the sources ``abstain_sources`` names for it: CachyOS and
    EndeavourOS pkgstats, Debian and Ubuntu popcon, GNOME and KDE Plasma both.
    """
    families = families or {}
    deps = list(deps)
    linux = {sid for sid, system in preinst.systems.items() if system.os == "linux"}
    shipped: dict[str, set[str]] = defaultdict(set)
    for sid in sorted(linux):
        for name in preinst.systems[sid].families:
            for fid in _members(families, pkg_id(SourceKey("font-name", name))):
                shipped[fid].add(sid)
    for rel in sorted((r for r in deps if r.kind == "preinstalled"), key=sort_key):
        fallback = _attr(rel, "family_id")
        fids = _members(families, pkg_id(rel.object)) or (
            (fallback,) if isinstance(fallback, str) and fallback else ()
        )
        if rel.subject.key in linux:
            for fid in fids:
                shipped[fid].add(rel.subject.key)
    out: dict[str, dict[str, Abstention]] = {}
    shares = dependency_shares(deps, counts, cfg, families=families)
    for source in sorted(counts):
        found = {}
        for fid, sids in sorted(shipped.items()):
            silencing = {sid for sid in sids if silences(sid, source, cfg)}
            if silencing:
                named = source if source in silencing else min(silencing)
                found[fid] = Abstention(source, fid, "preinstalled", named)
        for fid, ds in shares.get(source, {}).items():
            share = _pulled_share(ds, cfg.dependency_rule)
            if fid not in found and share >= cfg.dependency_abstain:
                found[fid] = Abstention(
                    source, fid, "dependency", ds[0].system, ds[0].package, round(share, 6)
                )
        out[source] = dict(sorted(found.items()))
    return out


# --- selection and counting --------------------------------------------------------------------


def select(src: SourceBase, obs: Iterable[Observation]) -> list[Observation]:
    """The observations engine source ``src`` reads, in canonical order."""
    pattern = series_pattern(src.series)
    scopes = tuple(getattr(src, "scopes", ()))
    out = [
        o
        for o in obs
        if o.source == src.collector and pattern.fullmatch(o.series) and _wanted(src, o, scopes)
    ]
    if isinstance(src, GithubSource) and out:
        out = _release_history(out, src.release_history)
    return sorted(out, key=sort_key)


def _wanted(src: SourceBase, o: Observation, scopes: tuple[str, ...]) -> bool:
    if not in_scopes(o.key, scopes):  # "@scope/name" or a pkg:npm/%40scope/name purl
        return False
    if _attr(o, "prerelease") is True and not (
        isinstance(src, GithubSource) and _repo_of(o) in src.prerelease_repos
    ):
        return False
    if isinstance(src, NerdSource):
        names = {_asset_base(o.key.key), _asset_base(str(_attr(o, "asset") or ""))}
        if names & set(src.exclude_assets):
            return False
    method = _attr(o, "method")
    return not (isinstance(src, FotSource) and method is not None and method not in src.methods)


def _asset_base(name: str) -> str:
    return re.sub(r"\.(?:zip|tar\.xz)\Z", "", name.rsplit("/", 1)[-1])


def _repo_of(o: Observation) -> str:
    repo = _attr(o, "repo")
    if isinstance(repo, str) and repo:
        return repo.lower()
    parts = o.key.key.split("/")
    return "/".join(parts[:2]).lower() if len(parts) >= 3 else ""


def _release_history(obs: list[Observation], mode: str) -> list[Observation]:
    """Gate M2: every release (``all``), or the latest 24 per repo, or 24 months."""
    if mode == "all":
        return obs
    if mode == "24months":
        cutoff = max(o.end for o in obs) - timedelta(days=730)
        return [o for o in obs if (_attr_date(o, "published_at") or o.start) >= cutoff]
    releases: dict[str, set[tuple[date, str]]] = defaultdict(set)
    for o in obs:
        releases[_repo_of(o)].add(
            (_attr_date(o, "published_at") or o.start, str(_attr(o, "release")))
        )
    keep = {
        (repo, tag) for repo, rels in releases.items() for _, tag in sorted(rels, reverse=True)[:24]
    }
    return [o for o in obs if (_repo_of(o), str(_attr(o, "release"))) in keep]


def count_keys(
    src: SourceBase, obs: Sequence[Observation], baseline: Sequence[Observation] = ()
) -> dict[SourceKey, KeyValue]:
    """One value per source key, by the source's ``counting`` (module docstring, step 2).

    ``baseline`` is the earlier snapshot of a lifetime counter
    (``snapshot_delta``); without one, the first-run rule applies: lifetime
    total over the days since the asset was created.
    """
    match src.counting:
        case "window":
            return _count_window(obs, src.rate)
        case "monthly_mean_share":
            return _count_monthly(obs, getattr(src, "months", 12))
        case "snapshot_delta":
            return _count_delta(obs, baseline, src.rate)
        case "weekly_mean":
            if isinstance(src, FotSource):
                obs = startup_capped(obs, src.startup_cap)
            return _count_weekly(obs)
        case _:  # "current" and "yearly": the value as it is
            return _count_plain(obs)


def _snap(days: int) -> float:
    """A window within a few days of a year or a month counts as exactly one."""
    if 358 <= days <= 372:
        return _YEAR
    if 28 <= days <= 31:
        return _MONTH
    return float(max(days, 1))


def _to_rate(total: float, start: date, end: date, seen: date | None, rate: str) -> float:
    window = _snap((end - start).days + 1)
    avail = window
    if seen is not None and seen > start:
        avail = max(1.0, min(window, float((end - seen).days + 1)))
    if rate == "per_year":
        return total * _YEAR / avail
    if rate == "per_month":
        return total * _MONTH / avail
    return total * window / avail


def _count_window(obs: Sequence[Observation], rate: str) -> dict[SourceKey, KeyValue]:
    out = {}
    for key, group in _by_key(obs).items():
        start, end = min(o.start for o in group), max(o.end for o in group)
        seen = _earliest(_attr_date(o, "first_seen") for o in group)
        total = _total(group)
        value = None if total is None else _to_rate(total, start, end, seen, rate)
        out[key] = KeyValue(value, seen, end)
    return out


def _count_plain(obs: Sequence[Observation]) -> dict[SourceKey, KeyValue]:
    return {
        key: KeyValue(
            _total(group),
            _earliest(_attr_date(o, "first_seen") for o in group),
            max(o.end for o in group),
        )
        for key, group in _by_key(obs).items()
    }


def _share(o: Observation) -> float | None:
    if o.value is None:
        return None
    samples = _attr(o, "samples")
    if o.unit != "share" and isinstance(samples, int) and samples > 0:
        return o.value / samples
    return o.value


def _count_monthly(obs: Sequence[Observation], months: int) -> dict[SourceKey, KeyValue]:
    """Arch: the mean of monthly shares from the key's first non-zero month on."""
    window = sorted({o.series for o in obs})[-months:]
    if not window:
        return {}
    inside = [o for o in obs if o.series in set(window)]
    month_start = {m: min(o.start for o in inside if o.series == m) for m in window}
    data_end = max(o.end for o in inside)
    out = {}
    for key, group in _by_key(inside).items():
        shares: dict[str, float] = defaultdict(float)
        known = False
        for o in group:
            s = _share(o)
            if s is not None:
                shares[o.series] += s
                known = True
        nonzero = [m for m in window if shares.get(m, 0.0) > 0]
        if not nonzero:
            out[key] = KeyValue(0.0 if known else None, None, data_end)
            continue
        used = window[window.index(nonzero[0]) :]
        value = math.fsum(shares.get(m, 0.0) for m in used) / len(used)
        start = None if nonzero[0] == window[0] else month_start[nonzero[0]]
        out[key] = KeyValue(value, start, data_end)
    return out


def _instances(obs: Sequence[Observation]) -> dict[tuple[SourceKey, str], tuple[float, date, date]]:
    """Lifetime counters by (key, release): (total, created, data date)."""
    grouped: dict[tuple[SourceKey, str], list[Observation]] = defaultdict(list)
    for o in obs:
        grouped[(o.key, str(_attr(o, "release") or ""))].append(o)
    out = {}
    for ident, group in sorted(grouped.items()):
        total = _total(group)
        if total is None:
            continue
        created = _earliest(
            _attr_date(o, "first_seen") or _attr_date(o, "published_at") or o.start for o in group
        )
        out[ident] = (total, created or min(o.start for o in group), max(o.end for o in group))
    return out


def _count_delta(
    obs: Sequence[Observation], baseline: Sequence[Observation], rate: str
) -> dict[SourceKey, KeyValue]:
    """GitHub and Nerd lifetime counters (methodology §5): growth between snapshots.

    With a baseline, a (key, release) pair present in both snapshots counts its
    growth, and a negative difference counts as 0. An asset created after the
    baseline's data date counts its whole count over the days since that date (its
    baseline is 0: the owner's ruling of 2026-09-26, new_release_assets); an older
    asset the baseline lacks is ignored. Without a baseline: lifetime total over the
    days since each asset was created.
    """
    period = _MONTH if rate == "per_month" else _YEAR
    now = _instances(obs)
    then = _instances(baseline) if baseline else {}
    base_day = max(o.end for o in baseline) if then else None
    values: dict[SourceKey, list[float]] = defaultdict(list)
    created: dict[SourceKey, list[date]] = defaultdict(list)
    ends: dict[SourceKey, list[date]] = defaultdict(list)
    for (key, release), (total, made, end) in now.items():
        created[key].append(made)
        ends[key].append(end)
        if then:
            before = then.get((key, release))
            if before is not None:
                days = max(1, (end - before[2]).days)
                values[key].append(max(0.0, total - before[0]) * period / days)
            elif base_day is not None and made > base_day:
                values[key].append(total * period / max(1, (end - base_day).days))
        else:
            values[key].append(total * period / max(1, (end - made).days))
    return {
        key: KeyValue(
            math.fsum(values[key]) if values.get(key) else None, min(created[key]), max(ends[key])
        )
        for key in sorted(created)
    }


FOT_STARTUPS = "startups"  # Fonts Over Time's category of startup homepages (D11)


def startup_capped(obs: Sequence[Observation], cap: float) -> list[Observation]:
    """Fonts Over Time's rows with startups held to ``cap`` of each week's weight (D11).

    A week's counted sites are its rows' ``category_sites`` (one total per category
    and method). When startups are more than ``cap`` of them, a startup site weighs
    cap·N/S and any other site (1 - cap)·N/(N - S), so the week keeps its N sites
    of weight and startups hold ``cap`` of it: with 5,231 startups of 10,465 sites,
    0.5 and 1.5 (an effective sample of about 8,400 sites, methodology D11). A value
    is then a weighted site count, and censoring applies to it. Rows without the
    attrs, and weeks with no startups or none else, are left as they are.
    """
    totals: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
    for o in obs:
        sites, category = _attr(o, "category_sites"), _attr(o, "category")
        if type(sites) is int and isinstance(category, str):
            totals[o.series][(category, str(_attr(o, "method")))] = sites
    weights: dict[tuple[str, str], float] = {}
    for week, by_group in totals.items():
        n = sum(by_group.values())
        startups = sum(v for (c, _), v in by_group.items() if c == FOT_STARTUPS)
        if not 0 < startups < n or startups <= cap * n:
            continue
        for category, _ in by_group:
            share = (
                cap * n / startups if category == FOT_STARTUPS else (1 - cap) * n / (n - startups)
            )
            weights[(week, category)] = share
    out = []
    for o in obs:
        w = weights.get((o.series, str(_attr(o, "category"))))
        out.append(o if w is None or o.value is None else replace(o, value=o.value * w))
    return out


def _count_weekly(obs: Sequence[Observation]) -> dict[SourceKey, KeyValue]:
    """Fonts Over Time: the mean of the month's weekly values (a missing week counts 0)."""
    data_end = max(o.end for o in obs)
    week_end: dict[str, date] = {}
    for o in obs:
        week_end[o.series] = max(week_end.get(o.series, o.end), o.end)
    month = sorted(w for w, end in week_end.items() if end > data_end - timedelta(days=31))
    out = {}
    for key, group in _by_key(o for o in obs if o.series in set(month)).items():
        weekly: dict[str, float] = defaultdict(float)
        known = False
        for o in group:
            if o.value is not None:
                weekly[o.series] += o.value
                known = True
        value = math.fsum(weekly.values()) / len(month) if known else None
        seen = _earliest(_attr_date(o, "first_seen") for o in group)
        out[key] = KeyValue(value, seen, max(o.end for o in group))
    return out


# --- key-level floors --------------------------------------------------------------------------


def key_floors(
    name: str,
    src: SourceBase,
    values: Mapping[SourceKey, KeyValue],
    relations: Iterable[Relation],
    *,
    tenure: Mapping[SourceKey, date | None] | None = None,
) -> tuple[dict[SourceKey, KeyValue], list[FloorNote]]:
    """Floors that apply to single keys, before any credit.

    - Homebrew, gate M3 ``p10``: Nerd casks (``names.NERD_SUFFIX``) lose the
      ``nerd_floor_quantile`` of every Nerd cask's value, once per cask: a key
      with a legacy tap prefix ("homebrew/cask-fonts/font-x") is the same cask,
      so it does not set the floor and loses only what its cask's own key
      could not. Every other cask loses the flat ``floor`` (20 a year) the same
      way, once per cask: methodology §5 gives Nerd casks their own floor
      "instead", so no family loses both, and ``apply_floors`` subtracts no
      second floor for Homebrew.
    - Arch, gate M4 ``tenured_p10``: members of the ``nerd-fonts`` group that
      have been in it ``nerd_group_tenure_months`` or more lose the quantile of
      those members' shares; newer members keep theirs. ``flat`` takes
      ``nerd_group_flat_floor`` from every member. ``tenure`` gives each key's
      first month when ``values`` covers too short a window to tell (Rising).
    - Nerd releases: every asset loses the ``floor_quantile`` of all assets.

    Quantiles are taken over positive values only; values never go below 0;
    keys without a value are left alone.
    """
    if isinstance(src, HomebrewSource):
        return _nerd_cask_floor(name, src, values)
    if isinstance(src, NerdSource):
        keys = list(values)
        return _subtract(values, keys, keys, src.floor_quantile, name, "Nerd Fonts assets")
    if isinstance(src, ArchSource):
        return _arch_group_floor(name, src, values, relations, tenure)
    return dict(values), []


def _nerd_cask_floor(
    name: str, src: HomebrewSource, values: Mapping[SourceKey, KeyValue]
) -> tuple[dict[SourceKey, KeyValue], list[FloorNote]]:
    # cask token -> its keys; with gate M3 "flat" a Nerd cask takes the flat floor too.
    casks: dict[str, list[SourceKey]] = defaultdict(list)
    plain: dict[str, list[SourceKey]] = defaultdict(list)
    for k in sorted(values):
        nerd = src.nerd_floor == "p10" and NERD_SUFFIX.search(k.key)
        (casks if nerd else plain)[k.key.rsplit("/", 1)[-1]].append(k)
    own = [values[k].value for k in values if k.key in casks]  # keys without a tap prefix
    basis = [v for v in own if v is not None and v > 0]
    floor = quantile(basis, src.nerd_floor_quantile)
    out = dict(values)
    notes = []
    if floor is not None:
        _take_per_cask(out, casks, floor)
        notes.append(FloorNote(name, "Nerd casks (gate M3)", floor, len(basis)))
    if src.floor > 0:
        _take_per_cask(out, plain, src.floor)
        notes.append(FloorNote(name, "other casks (flat)", src.floor, len(plain)))
    return out, notes


def _take_per_cask(
    out: dict[SourceKey, KeyValue], casks: Mapping[str, Sequence[SourceKey]], floor: float
) -> None:
    """Subtract ``floor`` once per cask from its keys, the cask's own key first (never below 0)."""
    for token, keys in sorted(casks.items()):
        left = floor
        for k in sorted(keys, key=lambda k: (k.key != token, k.key)):
            kv = out[k]
            if kv.value is not None and left > 0:
                taken = min(left, max(kv.value, 0.0))
                out[k] = replace(kv, value=kv.value - taken)
                left -= taken


def _subtract(
    values: Mapping[SourceKey, KeyValue],
    basis: Sequence[SourceKey],
    targets: Sequence[SourceKey],
    q: float | None,
    name: str,
    what: str,
    flat: float | None = None,
) -> tuple[dict[SourceKey, KeyValue], list[FloorNote]]:
    known = [v for k in basis if (v := values[k].value) is not None and v > 0]
    floor = flat if flat is not None else quantile(known, q or 0.0)
    out = dict(values)
    if floor is None:
        return out, []
    for key in targets:
        kv = out[key]
        if kv.value is not None:
            out[key] = replace(kv, value=max(0.0, kv.value - floor))
    return out, [FloorNote(name, what, floor, len(known))]


def _group_members(relations: Iterable[Relation], group: str) -> dict[SourceKey, date | None]:
    """Members of a package group, with the day each joined when the edge says so."""
    out: dict[SourceKey, date | None] = {}
    for rel in relations:
        if rel.kind != "group":
            continue
        if rel.subject.key == group:
            member = rel.object
        elif rel.object.key == group:
            member = rel.subject
        else:
            continue
        joined = _attr_date(rel, "first_seen")
        prev = out.get(member)
        out[member] = joined if prev is None else min(prev, joined or prev)
    return out


def _months(start: date | None, end: date) -> int:
    """Whole months from ``start``'s month to ``end``'s, inclusive (None: before the window)."""
    if start is None:
        return sys.maxsize
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _arch_group_floor(
    name: str,
    src: ArchSource,
    values: Mapping[SourceKey, KeyValue],
    relations: Iterable[Relation],
    tenure: Mapping[SourceKey, date | None] | None = None,
) -> tuple[dict[SourceKey, KeyValue], list[FloorNote]]:
    joined = _group_members(relations, NERD_GROUP)
    members = sorted(k for k in joined if k in values)
    if not members:
        return dict(values), []
    if src.nerd_group_floor == "flat":
        what = "nerd-fonts group, flat (gate M4)"
        return _subtract(values, members, members, None, name, what, src.nerd_group_flat_floor)
    first = {k: tenure.get(k) if tenure is not None else values[k].start for k in members}
    tenured = [
        k
        for k in members
        if _months(joined[k] or first[k], values[k].end) >= src.nerd_group_tenure_months
    ]
    what = f"nerd-fonts group members of {src.nerd_group_tenure_months}+ months (gate M4)"
    return _subtract(values, tenured, tenured, src.nerd_group_floor_quantile, name, what)


# --- one engine source ---------------------------------------------------------------------------


def _observations(src: SourceBase, inp: Inputs) -> tuple[list[Observation], SourceBase]:
    """The source's observations, or Google's fallback series (ranks become scores)."""
    obs = select(src, inp.observations.get(src.collector, ()))
    if obs or not isinstance(src, GoogleSource) or ":" not in src.fallback:
        return obs, src
    collector, _, series = src.fallback.partition(":")
    fallback = sorted(
        (o for o in inp.observations.get(collector, ()) if o.series == series), key=sort_key
    )
    ranks = [o.value for o in fallback if o.unit == "rank" and o.value is not None]
    if ranks:
        top = max(ranks) + 1
        fallback = [
            replace(o, value=top - o.value) if o.unit == "rank" and o.value is not None else o
            for o in fallback
        ]
    return fallback, replace(src, counting="current")


def _baseline(src: SourceBase, inp: Inputs, obs: Sequence[Observation]) -> list[Observation]:
    """The oldest baseline snapshot 1 to 12 months before this one."""
    if src.counting != "snapshot_delta" or not obs:
        return []
    end = max(o.end for o in obs)
    for _, recs in sorted(inp.baselines.get(src.collector, ()), key=lambda b: b[0]):
        chosen = select(src, recs)
        if chosen and _MIN_BASELINE_GAP <= end - max(o.end for o in chosen) <= _MAX_BASELINE_GAP:
            return chosen
    return []


def _frame(
    src: SourceBase, inp: Inputs, obs: Sequence[Observation], links: Iterable[Link]
) -> set[str]:
    """The families the source could show (module docstring), as ``mapping.frames`` rules:
    its keys' families, universe keys in its namespaces and npm scopes, and for a package
    source either end of any relation between keys it reads (a distro package with no
    count is then censored, not uncovered)."""
    if src.counting in CRAWL_COUNTINGS:
        # Methodology §4: "A desktop-only font gets censored terms from the web crawls,
        # not a placeholder", so the crawls' frame is every eligible family.
        return set(inp.families)
    namespaces = {o.key.ns for o in obs}
    scopes = tuple(getattr(src, "scopes", ()))

    def reads(key: SourceKey) -> bool:
        return key.ns in namespaces and in_scopes(key, scopes)

    fams = {link.family_id for link in links}
    fams |= {fid for key, fid in inp.universe_keys.items() if reads(key)}
    if frame_basis(src) == "packaged":
        ends = {k for rel in inp.relations for k in (rel.subject, rel.object) if reads(k)}
        fams |= {link.family_id for k in sorted(ends) for link in inp.links.get(k, ())}
    return fams & set(inp.families)


def _prev_seen(inp: Inputs, fid: str, source: str) -> date | None:
    raw = (inp.first_seen.get(fid, {}).get("sources") or {}).get(source)
    try:
        return date.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None


def _starts(
    name: str,
    src: SourceBase,
    counted: Mapping[SourceKey, KeyValue],
    links: Iterable[Link],
    inp: Inputs,
    availability: Mapping[str, date],
) -> dict[str, date | None]:
    """Each family's first day on this channel, for exposure (None: long enough, or unknown).

    Channel dates (``CHANNEL_EXPOSURES``) take an earlier date from state; a
    family with any key of unknown or pre-window start counts as old. A family
    the channel lists with no value (a universe key in the source's namespaces,
    no row) starts on its universe record's ``added`` date, when there is one:
    a font Google added last week is too new, not censored.
    """
    if src.exposure == "none":
        return {}
    if src.exposure == "data_date":
        return dict(availability)
    per: dict[str, list[date | None]] = defaultdict(list)
    for link in links:
        per[link.family_id].append(counted[link.key].start)
    namespaces = {key.ns for key in counted}
    listed: dict[str, list[date | None]] = defaultdict(list)
    for key, fid in sorted(inp.universe_keys.items()):
        if key.ns in namespaces and fid not in per:
            listed[fid].append(inp.added_keys.get((key.ns, match_key(key.key))))
    per.update(listed)
    out: dict[str, date | None] = {}
    for fid, starts in sorted(per.items()):
        start = None if None in starts else min(d for d in starts if d is not None)
        prev = _prev_seen(inp, fid, name)
        out[fid] = start if start is None or prev is None else min(start, prev)
    return out


def _dated(src: SourceBase, obs: Sequence[Observation], inp: Inputs) -> list[Observation]:
    """Exposure ``date_added``: each row without a ``first_seen`` attr takes the
    ``added`` date of the universe record with its key (Google's dateAdded), so
    the views become a rate over the days since and a new family is too new."""
    if src.exposure != "date_added":
        return list(obs)
    out = []
    for o in obs:
        day = inp.added_keys.get((o.key.ns, match_key(o.key.key)))
        if day is not None and _attr(o, "first_seen") is None:
            o = replace(o, attrs=tuple(sorted((*o.attrs, ("first_seen", day.isoformat())))))
        out.append(o)
    return out


def _dedupe_case(counted: Mapping[SourceKey, KeyValue]) -> dict[SourceKey, KeyValue]:
    """The crawls' keys that differ only in case ("Roboto", "roboto") count once, at
    the larger value: the same pages declare both, so a sum would count them twice."""
    best: dict[tuple[str, str], SourceKey] = {}
    for key in sorted(counted):
        ident = (key.ns, match_key(key.key))
        kept = best.get(ident)
        if kept is None or (counted[key].value or 0.0) > (counted[kept].value or 0.0):
            best[ident] = key
    return {key: counted[key] for key in sorted(best.values())}


def _shrunk(obs: Sequence[Observation], baseline: Sequence[Observation]) -> tuple[SourceKey, ...]:
    """Lifetime counters that went down since the baseline (deleted or re-uploaded
    assets): their difference counts as 0, and the report lists them (methodology §5)."""
    if not baseline:
        return ()
    then = _instances(baseline)
    now = _instances(obs)
    return tuple(
        sorted(
            {ident[0] for ident, got in now.items() if ident in then and got[0] < then[ident][0]}
        )
    )


def correct_source(
    name: str, src: SourceBase, inp: Inputs, cfg: RankingConfig, availability: Mapping[str, date]
) -> SourceResult | None:
    """Steps 1-6 of the module docstring for one engine source; None when it has no data."""
    obs, counting_src = _observations(src, inp)
    if not obs:
        return None
    return _correct(name, src, counting_src, obs, _baseline(src, inp, obs), inp, cfg, availability)


def _correct(
    name: str,
    src: SourceBase,
    counting_src: SourceBase,
    obs: Sequence[Observation],
    baseline: Sequence[Observation],
    inp: Inputs,
    cfg: RankingConfig,
    availability: Mapping[str, date],
    tenure: Mapping[SourceKey, date | None] | None = None,
) -> SourceResult:
    obs = _dated(src, obs, inp)
    counted = count_keys(counting_src, obs, baseline)
    if src.counting in CRAWL_COUNTINGS:
        counted = _dedupe_case(counted)
    data_date = max(o.end for o in obs)
    floored, notes = key_floors(name, src, counted, inp.relations, tenure=tenure)
    links = [link for key in sorted(counted) for link in inp.links.get(key, ())]
    corr = cfg.corrections
    how = {
        "largest": src.linux and corr.per_system_basis == "largest_package",
        "nerd": isinstance(src, NerdSource),
    }
    sums = family_sums(links, {k: v.value for k, v in floored.items()}, corr, **how)
    unfloored = family_sums(links, {k: v.value for k, v in counted.items()}, corr, **how)
    # Homebrew's flat floor went per cask in key_floors (a Nerd cask gets its own instead).
    per_family = replace(src, floor=0.0) if isinstance(src, HomebrewSource) else src
    terms = apply_floors({f: s.value for f, s in sums.items() if s.value is not None}, per_family)
    for fid in sorted(_frame(src, inp, obs, links) - terms.keys()):
        terms[fid] = Term(None, "censored", src.group, reason="no_value")
    starts = _starts(name, src, counted, links, inp, availability)
    for fid in sorted(terms):
        if not exposure(starts.get(fid), data_date, corr.min_exposure_days):
            terms[fid] = Term(None, "too_new", src.group)
    terms = _finish(src, terms, sums, links, obs, inp, cfg)
    # Only a date the channel gives is a first day on it: a font the first run meets in
    # Debian or a crawl has been there for an unknown time, and must not read as new.
    seen = {}
    if src.exposure in CHANNEL_EXPOSURES:
        seen = {link.family_id: day for link in links if (day := starts.get(link.family_id))}
    adjusted = {
        k: (v.value, floored[k].value)
        for k, v in counted.items()
        if v.value is not None and floored[k].value is not None and floored[k].value != v.value
    }
    windows = sorted({o.series for o in obs})
    return SourceResult(
        name=name,
        src=src,
        data_date=data_date,
        terms=terms,
        sums=sums,
        unfloored={f: s.value for f, s in unfloored.items() if s.value is not None},
        counts={pkg_id(k): v.value for k, v in counted.items() if v.value is not None},
        adjusted=adjusted,
        floors=tuple(notes),
        seen=dict(sorted(seen.items())),
        key_starts={k: v.start for k, v in sorted(counted.items())},
        shrunk=_shrunk(obs, baseline),
        window=windows[-1] if len(windows) == 1 else src.series,
    )


def recent_source(
    name: str,
    src: SourceBase,
    inp: Inputs,
    cfg: RankingConfig,
    availability: Mapping[str, date],
    full: SourceResult,
) -> SourceResult | None:
    """Rising's recent window of one engine source (``RISING_WINDOWS``), or None.

    Corrected like the 12-month result ``full`` (the Arch group floor's tenure
    comes from it), over the same families: a family too new or merged away
    there is so here; one with a term there but no value here is censored.
    """
    series = RISING_WINDOWS.get(name)
    if series is None:
        return None
    wanted = replace(src, series=series)
    obs = select(wanted, inp.observations.get(src.collector, ()))
    if obs and series_pattern(series).pattern != re.escape(series):
        latest = max(o.series for o in obs)
        obs = [o for o in obs if o.series == latest]
    if not obs:
        return None
    res = _correct(name, src, wanted, obs, (), inp, cfg, availability, tenure=full.key_starts)
    terms = {}
    for fid, term in sorted(full.terms.items()):
        got = res.terms.get(fid)
        if term.state not in _USED:
            terms[fid] = term
        elif got is not None and got.state in _USED:
            terms[fid] = got
        else:
            terms[fid] = replace(term, value=None, state="censored", reason="no_value")
    return replace(res, terms=terms, seen={})


def _finish(
    src: SourceBase,
    terms: Mapping[str, Term],
    sums: Mapping[str, FamilySum],
    links: Sequence[Link],
    obs: Sequence[Observation],
    inp: Inputs,
    cfg: RankingConfig,
) -> dict[str, Term]:
    """Group (gate M5), flags, the Almanac parent merge, and rounded values."""
    keys: dict[str, set[SourceKey]] = defaultdict(set)
    for link in links:
        keys[link.family_id].add(link.key)
    by_key = _by_key(obs)
    stale = _stale_sync(src, keys, by_key)
    merged, parents = (
        _almanac_merges(src, inp, set(keys)) if isinstance(src, AlmanacSource) else (set(), set())
    )
    out = {}
    for fid, term in sorted(terms.items()):
        group = _group(src, cfg, inp, fid, keys.get(fid, set()), by_key)
        if fid in merged:
            out[fid] = Term(None, "not_covered", group, reason="merged_into_parent")
            continue
        flags = set(term.flags)
        s = sums.get(fid)
        if s is not None and s.bundle_only and term.state in ("observed", "censored"):
            flags.add("bundle_only")
        if fid in stale:
            flags.add("stale_sync")
        factor = term.factor
        if fid in parents and isinstance(src, AlmanacSource):
            flags.add("parent_merge")
            factor = src.parent_merge_factor
        out[fid] = replace(
            term, value=_round(term.value), group=group, factor=factor, flags=tuple(sorted(flags))
        )
    return out


def _group(
    src: SourceBase,
    cfg: RankingConfig,
    inp: Inputs,
    fid: str,
    keys: Iterable[SourceKey],
    by_key: Mapping[SourceKey, list[Observation]],
) -> str:
    """Gate M5: a GitHub counter joins ``homebrew`` when the family's cask downloads its asset."""
    if src.group != "github_counters" or cfg.engine.github_homebrew_same_group != "cask_asset":
        return src.group
    casks = inp.cask_repos.get(fid)
    if not casks:
        return src.group
    repos = set()
    for key in keys:
        found = {_repo_of(o) for o in by_key.get(key, ())} - {""}
        repos |= found or ({NERD_REPO} if isinstance(src, NerdSource) else set())
    return "homebrew" if repos & casks else src.group


def _stale_sync(
    src: SourceBase,
    keys: Mapping[str, set[SourceKey]],
    by_key: Mapping[SourceKey, list[Observation]],
) -> set[str]:
    """ecosyste.ms families with a package synced longer ago than ``stale_sync_days``."""
    if not isinstance(src, EcosystemsSource):
        return set()
    out = set()
    for fid, ks in keys.items():
        for key in ks:
            for o in by_key.get(key, ()):
                synced = _attr_date(o, "last_synced_at")
                if synced is not None and (o.end - synced).days > src.stale_sync_days:
                    out.add(fid)
    return out


ALMANAC_SERVICES_PREFIX = "requests/"  # the Almanac's services tab (collectors/ranking/almanac.py)


def _almanac_merges(src: AlmanacSource, inp: Inputs, listed: set[str]) -> tuple[set[str], set[str]]:
    """(width cuts the pages tab folds away, parents flagged and halved), methodology §5.

    Every eligible width cut ("Roboto Condensed") of an eligible parent that the
    pages tab does not list is merged into the parent (not covered). The parent
    is flagged when the services tab (series ``requests/<client>/<service>``, gate
    M6) shows the cut in use; the other pages series (desktop) do not count.
    """
    by_name: dict[str, str] = {}
    for fid, fam in sorted(inp.families.items()):
        by_name.setdefault(match_key(fam.family), fid)
    shown = {
        link.family_id
        for o in inp.observations.get(src.collector, ())
        if o.series.startswith(ALMANAC_SERVICES_PREFIX) and o.value
        for link in inp.links.get(o.key, ())
    }
    merged, parents = set(), set()
    for fid, fam in sorted(inp.families.items()):
        parent_name = _WIDTH_CUT.sub("", fam.family)
        parent = by_name.get(match_key(parent_name)) if parent_name != fam.family else None
        if parent is None or parent == fid or fid in listed:
            continue
        merged.add(fid)
        if fid in shown and parent in listed:
            parents.add(parent)
    return merged, parents


# --- Linux abstentions, views, tags and state ------------------------------------------------------


def view_sources(cfg: RankingConfig) -> dict[str, tuple[tuple[str, ...], bool]]:
    """{rank key: (engine sources, abstain)} for every view with its own terms.

    ``overall`` has none of its own: it reuses ``desktop_chosen`` and
    ``project`` (surveys.py). Rising abstains like every view but
    ``desktop_installed``.
    """
    r = cfg.ranks
    out: dict[str, tuple[tuple[str, ...], bool]] = {}
    for key in ("desktop_chosen", "desktop_installed", "project"):
        rank = getattr(r, key)
        out[key] = (tuple(getattr(cfg.surveys, rank.survey).weights), rank.abstain)
    out["coding"] = (tuple(r.coding.weights), r.coding.abstain)
    out["dev_apps"] = (tuple(r.dev_apps.weights), r.dev_apps.abstain)
    out["rising"] = (tuple(r.rising.sources), True)
    return out


def build_cases(
    results: Mapping[str, SourceResult],
    found: Mapping[str, Mapping[str, Abstention]],
    shares: Mapping[str, Mapping[str, Sequence[Dependent]]],
    corr: Corrections,
    rulings: Mapping[str, str],
) -> list[Case]:
    """Every abstention and 35-50% flag on a family the source has a term for, with its status.

    A preinstalled case the owner overruled leaves the family to the dependency
    rule, which may make a case of its own (``dep-`` or ``rev-``).
    """
    cases = []
    for source in sorted(set(found) | set(shares)):
        terms = results[source].terms if source in results else {}
        abstaining = found.get(source, {})
        pulled = shares.get(source, {})
        for fid in sorted((set(abstaining) | set(pulled)) & set(terms)):
            a = abstaining.get(fid)
            if a is not None:
                cid = case_id(a.why, source, fid)
                case = Case(
                    cid, a.why, source, fid, a.system, a.package, a.share, _status(cid, rulings)
                )
                cases.append(case)
                if a.why == "dependency" or case.abstains:
                    continue
            ds = pulled.get(fid)
            if not ds:
                continue
            share = round(_pulled_share(ds, corr.dependency_rule), 6)
            if share >= corr.dependency_abstain:
                kind: Literal["dependency", "review"] = "dependency"
            elif share >= corr.dependency_review:
                kind = "review"
            else:
                continue
            cid = case_id(kind, source, fid)
            cases.append(
                Case(
                    cid,
                    kind,
                    source,
                    fid,
                    ds[0].system,
                    ds[0].package,
                    share,
                    _status(cid, rulings),
                )
            )
    return cases


def _status(cid: str, rulings: Mapping[str, str]) -> Literal["new", "accepted", "overruled"]:
    if cid not in rulings:
        return "new"
    return "overruled" if rulings[cid] == "b" else "accepted"


def build_views(
    cfg: RankingConfig,
    results: Mapping[str, SourceResult],
    cases: Iterable[Case],
    recent: Mapping[str, SourceResult] | None = None,
) -> dict[str, dict[str, dict[str, Term]]]:
    """{view: {source: {family: Term}}}: Linux terms of abstaining families are left out
    of every view whose rank abstains; 35-50% cases carry ``dependency_review``.

    The ``rising`` view takes its terms from ``recent`` (``recent_source``), never
    from the 12-month results; a source without a recent window is left out.
    """
    cases = list(cases)
    out_of = {(c.source, c.family_id) for c in cases if c.abstains}
    review = {(c.source, c.family_id) for c in cases if c.kind == "review" and not c.abstains}
    views: dict[str, dict[str, dict[str, Term]]] = {}
    for view, (sources, abstain) in view_sources(cfg).items():
        pool = (recent or {}) if view == "rising" else results
        per_source = {}
        for name in sources:
            res = pool.get(name)
            if res is None:
                continue
            terms = {}
            for fid, term in sorted(res.terms.items()):
                if abstain and res.src.linux and (name, fid) in out_of:
                    continue
                if (name, fid) in review:
                    term = replace(term, flags=tuple(sorted({*term.flags, "dependency_review"})))
                terms[fid] = term
            per_source[name] = terms
        views[view] = per_source
    return views


def build_tags(
    preinstalled_on: Mapping[str, Iterable[str]],
    pulled: Mapping[str, Iterable[tuple[str, str]]],
    views: Mapping[str, Mapping[str, Mapping[str, Term]]],
    cases: Iterable[Case],
) -> dict[str, Tags]:
    """Tags for every family that has one: systems, pulling packages and the
    ``no_deliberate_evidence`` flag (an observed desktop term only in most installed)."""
    installed = views.get("desktop_installed", {})
    chosen = views.get("desktop_chosen", {})
    candidates = set(preinstalled_on) | set(pulled) | {c.family_id for c in cases if c.abstains}
    out = {}
    for fid in sorted(candidates):
        flags = ()
        if _observed(installed, fid) and not _observed(chosen, fid):
            flags = ("no_deliberate_evidence",)
        tags = Tags(
            tuple(sorted(set(preinstalled_on.get(fid, ())))),
            tuple(sorted(set(pulled.get(fid, ())))),
            flags,
        )
        if tags != Tags():
            out[fid] = tags
    return out


def _observed(view: Mapping[str, Mapping[str, Term]], fid: str) -> bool:
    return any(
        terms.get(fid, Term(None, "censored", "")).state == "observed" for terms in view.values()
    )


def pulled_in_by(
    shares: Mapping[str, Mapping[str, Sequence[Dependent]]],
    cases: Iterable[Case],
    corr: Corrections,
) -> dict[str, set[tuple[str, str]]]:
    """(system, package) of every dependent at or above ``dependency_abstain``, unless the
    owner overruled the case; the top dependent when the family abstains by the sum rule or
    by the owner's ruling."""
    cases = list(cases)
    overruled = {
        (c.source, c.family_id) for c in cases if c.kind == "dependency" and not c.abstains
    }
    forced = {(c.source, c.family_id) for c in cases if c.kind == "review" and c.abstains}
    out: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for source, fams in shares.items():
        for fid, ds in fams.items():
            if (source, fid) in overruled or not ds:
                continue
            out[fid] |= {(d.system, d.package) for d in ds if d.share >= corr.dependency_abstain}
            top = _pulled_share(ds, corr.dependency_rule) >= corr.dependency_abstain
            if top or (source, fid) in forced:
                out[fid].add((ds[0].system, ds[0].package))
    return {fid: pairs for fid, pairs in out.items() if pairs}


def next_first_seen(
    prev: Mapping[str, Mapping[str, Any]], results: Iterable[SourceResult]
) -> dict[str, dict[str, Any]]:
    """``first_seen.json``: the committed file plus each source's first day for each family.

    An earlier date always wins, so a rerun writes the same bytes. ``catalog``
    is kept as it is (stage "membership" sets it).
    """
    out: dict[str, dict[str, Any]] = copy.deepcopy({k: dict(v) for k, v in prev.items()})
    for res in results:
        for fid, day in res.seen.items():
            entry = out.setdefault(fid, {"catalog": None, "sources": {}})
            sources = dict(entry.get("sources") or {})
            iso = day.isoformat()
            if sources.get(res.name) is None or iso < sources[res.name]:
                sources[res.name] = iso
            entry["sources"] = dict(sorted(sources.items()))
    return dict(sorted(out.items()))


# --- inputs ------------------------------------------------------------------------------------


def eligible_families(
    u: Universe,
    latin: Mapping[str, Any] | None,
    licenses: Mapping[str, Any] | None,
    log: logging.Logger | None = None,
) -> dict[str, Family]:
    """Universe families without a drop reason that pass the Latin gate and whose license
    is allowed. A family whose license waits for an owner ruling is left out until the
    ruling (methodology §1 "filter first", §9: a font with an unverified license must never
    be ranked). A missing gate file skips that gate, with a warning."""
    fams = {fid: f for fid, f in sorted(u.families.items()) if f.drop is None}
    if latin is None:
        if log:
            log.warning("no build/stage/latin.json: the Latin gate is skipped")
    else:
        fams = {fid: f for fid, f in fams.items() if fid in latin and latin[fid].latin}
    if licenses is None:
        if log:
            log.warning("no build/stage/licenses.json: the license gate is skipped")
    else:
        fams = {fid: f for fid, f in fams.items() if _license_ok(licenses.get(fid))}
    return fams


def _license_ok(verdict: Any) -> bool:
    return (
        verdict is not None and verdict.license is not None and verdict.license.status == "allowed"
    )


def _optional(paths: Paths, name: str) -> Any:
    return stageio.load_stage(paths, name) if stageio.stage_path(paths, name).is_file() else None


def _required(paths: Paths, name: str) -> Any:
    """``stageio.load_stage``, naming the stage to run first when the file is missing."""
    path = stageio.stage_path(paths, name)
    if not path.is_file():
        producer = stageio.STAGE_FILES[name].producer
        raise FileNotFoundError(f"{path}: missing; run stage {producer!r} first")
    return stageio.load_stage(paths, name)


def _read_records(
    directory: Path,
) -> tuple[dict[str, list[Record]], dict[str, list[tuple[date, tuple[Observation, ...]]]]]:
    """``build/stage/records/<collector>.jsonl`` and ``<collector>@<date>.jsonl`` baselines."""
    current: dict[str, list[Record]] = {}
    baselines: dict[str, list[tuple[date, tuple[Observation, ...]]]] = defaultdict(list)
    if not directory.is_dir():
        return current, baselines
    for path in sorted(directory.glob("*.jsonl")):
        collector, _, day = path.name.removesuffix(".jsonl").partition("@")
        recs = read_jsonl(path)
        if day:
            obs = tuple(r for r in recs if isinstance(r, Observation))
            baselines[collector].append((date.fromisoformat(day), obs))
        else:
            current[collector] = recs
    return current, baselines


def _cask_repos(
    recs: Iterable[UniverseRecord],
    universe_keys: Mapping[SourceKey, str],
    links: Mapping[SourceKey, Iterable[Link]] | None = None,
) -> dict[str, frozenset[str]]:
    """Family -> the GitHub repos its Homebrew casks download release assets from
    (gate M5). A cask belongs to the family holding its universe key, else to the
    one family its analytics key is mapped to."""
    out: dict[str, set[str]] = defaultdict(set)
    for r in recs:
        if r.key.ns != "brew-cask":
            continue
        fid = universe_keys.get(r.key)
        if fid is None:
            mapped = {link.family_id for link in (links or {}).get(r.key, ())}
            fid = mapped.pop() if len(mapped) == 1 else None
        if fid is None:
            continue
        urls = [f.url for f in r.files] + [u for _, u in r.urls]
        urls += [str(v) for k, v in r.attrs if k == "url"]
        for url in urls:
            m = _GITHUB_ASSET.match(url)
            if m:
                out[fid].add(m.group(1).lower())
    return {fid: frozenset(repos) for fid, repos in sorted(out.items())}


def resolve_names(
    names: Iterable[str], families: Mapping[str, Family], index: Iterable[IndexEntry] = ()
) -> tuple[dict[str, str], list[str]]:
    """Preinstalled family names to family ids: the family's own name, else an alias in
    ``NAME_NAMESPACES`` (exact ``match_key``). Returns (resolved, unresolved names)."""
    own: dict[str, str] = {}
    for fid, fam in sorted(families.items()):
        own.setdefault(match_key(fam.family), fid)
    alias = {(e.ns, e.match_key): e.family_id for e in index if e.family_id in families}
    resolved, missing = {}, []
    for name in sorted(set(names)):
        mk = match_key(name)
        fid = own.get(mk) or next(
            (alias[(ns, mk)] for ns in NAME_NAMESPACES if (ns, mk) in alias), None
        )
        if fid:
            resolved[name] = fid
        else:
            missing.append(name)
    return resolved, missing


def _collectors_read(cfg: RankingConfig) -> set[str]:
    """Collectors whose observations an enabled engine source reads (Google's fallback too)."""
    out = set()
    for src in cfg.sources.all().values():
        if src.enabled:
            out.add(src.collector)
            if isinstance(src, GoogleSource):
                out.add(src.fallback.partition(":")[0])
    return out


def _bundle_links(
    keys: Iterable[SourceKey],
    bundles: Mapping[tuple[str, str], tuple[str, ...]],
    families: Mapping[str, Family],
) -> dict[SourceKey, set[Link]]:
    """A bundle key's link to each eligible family it credits; its detail is the bundle's
    size (every family it credits, eligible or not), for ``bundle_mode = "split"``."""
    out: dict[SourceKey, set[Link]] = defaultdict(set)
    for key in sorted(set(keys)):
        members = bundles.get((key.ns, match_key(key.key)), ())
        for fid in members:
            if fid in families:
                out[key].add(Link(key, fid, "bundle", str(len(members))))
    return out


def _added(
    recs: Iterable[UniverseRecord], universe_keys: Mapping[SourceKey, str]
) -> tuple[dict[str, date], dict[tuple[str, str], date]]:
    """({family: earliest added}, {(ns, match_key): earliest added}) of the universe records."""
    by_family: dict[str, date] = {}
    by_key: dict[tuple[str, str], date] = {}
    for r in recs:
        if r.added is None:
            continue
        ident = (r.key.ns, match_key(r.key.key))
        by_key[ident] = min(by_key.get(ident, r.added), r.added)
        fid = universe_keys.get(r.key)
        if fid is not None:
            by_family[fid] = min(by_family.get(fid, r.added), r.added)
    return dict(sorted(by_family.items())), dict(sorted(by_key.items()))


def load_inputs(ctx: StageContext) -> Inputs:
    """Read the stage's inputs (module docstring) and resolve them to eligible families."""
    paths = ctx.paths
    universe = _required(paths, "universe")
    families = eligible_families(
        universe, _optional(paths, "latin"), _optional(paths, "licenses"), ctx.log
    )
    mapped = _required(paths, "mapped")
    current, baselines = _read_records(paths.records)
    links: dict[SourceKey, set[Link]] = defaultdict(set)
    relation_families: dict[str, str] = {}
    mapped_obs: dict[str, list[Observation]] = defaultdict(list)
    extra_relations: set[Relation] = set()
    for m in mapped:
        rec, fid = m.record, m.family_id
        if isinstance(rec, Relation):
            extra_relations.add(rec)
            if fid in families:
                relation_families.setdefault(pkg_id(rec.object), fid)
            continue
        mapped_obs[rec.source].append(rec)
        if fid in families and m.relation in COUNTED_RELATIONS:
            links[rec.key].add(Link(rec.key, fid, m.relation, m.detail))
    read = _collectors_read(ctx.config.ranking)
    observations = {
        c: tuple(r for r in recs if isinstance(r, Observation))
        for c, recs in current.items()
        if c in read
    }
    for collector, obs in mapped_obs.items():
        if collector in read:
            observations.setdefault(collector, tuple(sorted(set(obs), key=sort_key)))
    relations = {r for recs in current.values() for r in recs if isinstance(r, Relation)}
    relations |= extra_relations
    # Bundle keys stay out of the alias index (one family per key), so they are linked here.
    bundles = bundle_rows(paths.aliases_csv)
    unlinked = [o.key for obs in observations.values() for o in obs if o.key not in links]
    for key, found in _bundle_links(unlinked, bundles, families).items():
        links[key] |= found
    universe_keys = {
        key: fid
        for fid, fam in sorted(universe.families.items())
        if fid in families
        for key in fam.keys
    }
    urecs = [r for recs in current.values() for r in recs if isinstance(r, UniverseRecord)]
    added, added_keys = _added(urecs, universe_keys)
    preinst = ctx.config.preinstalled
    all_names = [n for system in preinst.systems.values() for n in system.families]
    index = _optional(paths, "alias_index") or ()
    names, missing = resolve_names(all_names, families, index)
    elsewhere, _ = resolve_names(missing, universe.families, index)
    return Inputs(
        families=families,
        links={k: tuple(sorted(v)) for k, v in sorted(links.items())},
        observations=dict(sorted(observations.items())),
        baselines={c: tuple(sorted(b, key=lambda x: x[0])) for c, b in sorted(baselines.items())},
        relations=tuple(sorted(relations, key=sort_key)),
        relation_families=relation_families,
        universe_keys=universe_keys,
        added=added,
        added_keys=added_keys,
        cask_repos=_cask_repos(urecs, universe_keys, links),
        names=names,
        ineligible_names=frozenset(elsewhere),
        first_seen=ctx.state.first_seen,
    )


def availability(inp: Inputs, sources: Mapping[str, SourceBase]) -> dict[str, date]:
    """The earliest day each family is known to exist: universe ``added`` dates and the
    ``first_seen`` attrs of the sources that carry them. The crawls (exposure
    ``data_date``) count exposure from it.

    Only dates that can make a font too new are kept: a window source's
    ``first_seen`` on or before its window start only says "before the window"
    (npm's first non-zero day of a year-long range), and a family with no date
    counts as covered.
    """
    out = dict(inp.added)
    for src in sources.values():
        if src.exposure not in EXPOSURE_ATTR_KINDS:
            continue
        for o in select(src, inp.observations.get(src.collector, ())):
            day = _attr_date(o, "first_seen")
            if day is None or (src.counting == "window" and day <= o.start):
                continue
            for link in inp.links.get(o.key, ()):
                if link.family_id not in out or day < out[link.family_id]:
                    out[link.family_id] = day
    return dict(sorted(out.items()))


def package_families(
    inp: Inputs, results: Iterable[SourceResult]
) -> dict[str, str | tuple[str, ...]]:
    """pkg_id -> family for the Linux sources' font packages and every preinstalled name;
    a bundle package (D2) gives every family it credits, sorted."""
    out: dict[str, str | tuple[str, ...]] = {}
    for res in results:
        for key in sorted(res.counts):
            ns, _, name = key.partition(":")
            fams = sorted({link.family_id for link in inp.links.get(SourceKey(ns, name), ())})
            if len(fams) == 1:
                out[key] = fams[0]
            elif fams:
                out[key] = tuple(fams)
    for pid, fid in inp.relation_families.items():
        out.setdefault(pid, fid)
    for name, fid in inp.names.items():
        out[pkg_id(SourceKey("font-name", name))] = fid
    return dict(sorted(out.items()))


def load_rulings(paths: Paths) -> dict[str, str]:
    """Gate X answers: {case id: choice} ("a" when an answer has no choice)."""
    directory = reviews.gate_dir(paths, GATE)
    if not directory.is_dir() or not any(directory.glob("*.toml")):
        return {}
    out = {}
    for ruling in reviews.load_rulings(paths, GATE):
        for answer in ruling.answers:
            out[answer.id] = answer.choice or "a"
    return out


# --- the report ----------------------------------------------------------------------------------


def _fmt(value: float | None, src: SourceBase) -> str:
    if value is None:
        return "no value"
    if src.rate == "share":
        return f"{value * 100:.2f}%"
    return f"{value:,.0f}" if abs(value) >= 100 or value.is_integer() else f"{value:,.2f}"


def _unit(src: SourceBase) -> str:
    return {"per_year": " a year", "per_month": " a month"}.get(src.rate, "")


def _label(fid: str, families: Mapping[str, Family]) -> str:
    fam = families.get(fid)
    return f"{fam.family} (`{fid}`)" if fam else f"`{fid}`"


def _rank_in(res: SourceResult, fid: str) -> str:
    observed = sorted(
        (f for f, t in res.terms.items() if t.state == "observed" and t.value is not None),
        key=lambda f: (-(res.terms[f].value or 0.0), f),
    )
    return f"rank {observed.index(fid) + 1}" if fid in observed else "unranked"


def render_report(
    run_date: date,
    results: Mapping[str, SourceResult],
    cases: Sequence[Case],
    families: Mapping[str, Family],
    unresolved: Sequence[tuple[str, str]],
    views: Mapping[str, Mapping[str, Mapping[str, Term]]],
    *,
    recent: Mapping[str, SourceResult] | None = None,
    rising: Sequence[str] = (),
) -> str:
    """``build/corrections.md``: floors, gate-X cases (new ones marked), unresolved
    preinstalled names, per-font corrections, lifetime counters that went down,
    evidence counts and Rising's recent windows. Sources whose ``publish_raw`` is
    false show ranks, never values."""
    new = [c for c in cases if c.status == "new"]
    lines = [
        "# Corrections report",
        "",
        f'Stage "correct" (milestone-1 step 10), run date {run_date.isoformat()}. '
        "Generated; do not edit.",
        "",
        f"**{len(new)} new case{'s' if len(new) != 1 else ''} for the owner (gate X).** "
        "Rule on them in `data/reviews/corrections/`, one table per case id: "
        'choice "a" accepts the handling shown, choice "b" reverses it (the source keeps '
        "counting the font, or a flagged font abstains).",
        "",
    ]
    lines += _report_floors(results)
    lines += _report_cases(cases, families)
    if unresolved:
        lines += ["## Preinstalled names with no family", "", "| System | Name |", "|---|---|"]
        lines += [f"| {sid} | {name} |" for sid, name in unresolved]
        lines += ["", "Add an alias, or fix the name in `config/preinstalled.toml`.", ""]
    lines += _report_fonts(results, families)
    lines += _report_shrunk(results)
    lines += _report_states(results, views, cases)
    lines += _report_rising(results, recent or {}, rising)
    return "\n".join(lines).rstrip() + "\n"


def _report_shrunk(results: Mapping[str, SourceResult]) -> list[str]:
    rows = [
        f"| {name} | `{key.key}` |" for name, res in sorted(results.items()) for key in res.shrunk
    ]
    if not rows:
        return []
    return [
        "## Lifetime counters below their baseline",
        "",
        "Deleted or re-uploaded assets: the difference counts as 0 (methodology §5).",
        "",
        "| Source | Key |",
        "|---|---|",
        *rows,
        "",
    ]


def _report_rising(
    results: Mapping[str, SourceResult], recent: Mapping[str, SourceResult], rising: Sequence[str]
) -> list[str]:
    if not rising:
        return []
    lines = ["## Rising: recent windows", "", "| Source | 12 months | Recent |", "|---|---|---|"]
    for name in sorted(rising):
        full, got = results.get(name), recent.get(name)
        if full is None:
            lines.append(f"| {name} | no data | |")
        elif got is None:
            wanted = RISING_WINDOWS.get(name)
            why = f"no `{wanted}` rows" if wanted else "none (left out of Rising)"
            lines.append(f"| {name} | {full.window} | {why} |")
        else:
            lines.append(f"| {name} | {full.window} | {got.window} |")
    return [*lines, ""]


def _report_floors(results: Mapping[str, SourceResult]) -> list[str]:
    lines = ["## Floors this run", "", "| Source | Floor | Value | Over |", "|---|---|---|---|"]
    for name, res in sorted(results.items()):
        if res.src.floor:
            lines.append(
                f"| {name} | flat floor | {_fmt(res.src.floor, res.src)}{_unit(res.src)} | |"
            )
        for note in res.floors:
            value = (
                _fmt(note.value, res.src) + _unit(res.src)
                if res.src.publish_raw
                else "(not published)"
            )
            lines.append(f"| {name} | {note.what} | {value} | {note.members} |")
    return [*lines, ""]


def _report_cases(cases: Sequence[Case], families: Mapping[str, Family]) -> list[str]:
    lines = [
        "## Linux abstentions and flags (every rank except most installed)",
        "",
    ]
    if not cases:
        return [*lines, "None.", ""]
    lines += [
        "| Case | Status | Family | Source | Why | System | Package | Share |",
        "|---|---|---|---|---|---|---|---|",
    ]
    order = {"new": 0, "overruled": 1, "accepted": 2}
    for c in sorted(cases, key=lambda c: (order[c.status], c.kind, c.source, c.family_id)):
        status = "**new**" if c.status == "new" else c.status
        why = {
            "preinstalled": "preinstalled",
            "dependency": "abstains: dependency",
            "review": "flagged: 35-50%",
        }[c.kind]
        share = "" if c.share is None else f"{c.share:.0%}"
        lines.append(
            f"| `{c.id}` | {status} | {_label(c.family_id, families)} | {c.source} | {why} "
            f"| {c.system} | {c.package or ''} | {share} |"
        )
    return [*lines, ""]


def _report_fonts(results: Mapping[str, SourceResult], families: Mapping[str, Family]) -> list[str]:
    rows = []
    for name, res in sorted(results.items()):
        for fid, s in sorted(res.sums.items()):
            notes = []
            for p in s.parts:
                before_after = res.adjusted.get(p.key)
                if not p.code and before_after is None:
                    continue
                what = "bundle" if p.code.startswith("bundle") else (p.code or "package")
                if res.src.publish_raw:
                    value = _fmt(p.value, res.src)
                    if before_after:
                        value = f"{_fmt(before_after[0], res.src)} before the floor, {value} after"
                    notes.append(f"`{p.key.key}` ({what}): {value}, credit {p.credit:g}")
                else:
                    notes.append(f"`{p.key.key}` ({what}): credit {p.credit:g}")
            term = res.terms.get(fid)
            if notes and term is not None:
                total = _fmt(term.value, res.src) if res.src.publish_raw else _rank_in(res, fid)
                rows.append(
                    f"| {_label(fid, families)} | {name} | {'; '.join(notes)} | {term.state}, {total} |"
                )
    if not rows:
        return []
    return [
        "## Per-font corrections",
        "",
        "Credited builds and bundles, and keys a key-level floor changed.",
        "",
        "| Family | Source | Keys | Term |",
        "|---|---|---|---|",
        *rows,
        "",
    ]


def _report_states(
    results: Mapping[str, SourceResult],
    views: Mapping[str, Mapping[str, Mapping[str, Term]]],
    cases: Sequence[Case],
) -> list[str]:
    lines = [
        "## Evidence states",
        "",
        "| Source | Data date | Observed | Censored | Too new | Merged into parent | Flagged | Abstaining in most chosen |",
        "|---|---|---|---|---|---|---|---|",
    ]
    chosen = views.get("desktop_chosen", {})
    for name, res in sorted(results.items()):
        states = [t.state for t in res.terms.values()]
        merged = sum(t.reason == "merged_into_parent" for t in res.terms.values())
        reviewed = {c.family_id for c in cases if c.source == name and c.kind == "review"}
        flagged = sum(bool(t.flags) or f in reviewed for f, t in res.terms.items())
        gone = len(res.terms) - len(chosen[name]) if name in chosen else 0
        lines.append(
            f"| {name} | {res.data_date.isoformat()} | {states.count('observed')} "
            f"| {states.count('censored')} | {states.count('too_new')} | {merged} | {flagged} | {gone} |"
        )
    too_new = [
        f"{fid} ({name})"
        for name, res in sorted(results.items())
        for fid, t in sorted(res.terms.items())
        if t.state == "too_new"
    ]
    if too_new:
        lines += ["", f"Too new (under the exposure minimum): {', '.join(too_new)}."]
    return [*lines, ""]


def questions(cases: Iterable[Case], families: Mapping[str, Family]) -> list[dict[str, Any]]:
    """``build/stage/queues/corrections.json``: every gate-X case as a single-select question
    (``reviews.Question`` fields plus the case), option (a) recommended.

    Answered cases stay in the queue: ``reviews`` leaves them out of the open
    questions, and still knows them when the owner rules on one again.
    """
    out = []
    for c in sorted(cases, key=lambda c: c.id):
        fam = families[c.family_id].family if c.family_id in families else c.family_id
        if c.kind == "preinstalled":
            text = f"{fam} comes with {c.system}. Leave {c.source} out of most chosen for it?"
        else:
            text = (
                f"{c.source}: {fam} is pulled in by {c.package} ({c.system}), "
                f"{c.share or 0.0:.0%} of its installs."
            )
            text += " Keep counting it?" if c.kind == "review" else " Leave it out of most chosen?"
        options = (
            ["Yes: keep counting it", "No: leave it out of most chosen"]
            if c.kind == "review"
            else ["Yes: leave it out of most chosen", "No: keep counting it"]
        )
        out.append(
            {
                "gate": GATE,
                "id": c.id,
                "text": text,
                "options": options,
                "recommended": 0,
                "kind": c.kind,
                "source": c.source,
                "family_id": c.family_id,
                "system": c.system,
                "package": c.package,
                "share": c.share,
            }
        )
    return out


# --- the stage ---------------------------------------------------------------------------------


def run(ctx: StageContext) -> None:
    """Stage "correct"."""
    paths, cfg = ctx.paths, ctx.config.ranking
    inp = load_inputs(ctx)
    sources = {n: s for n, s in cfg.sources.all().items() if s.enabled}
    avail = availability(inp, sources)
    results: dict[str, SourceResult] = {}
    for name, src in sources.items():
        res = correct_source(name, src, inp, cfg, avail)
        if res is None:
            ctx.log.warning("engine source %s has no observations this run", name)
        else:
            results[name] = res
    recent: dict[str, SourceResult] = {}
    for name in cfg.ranks.rising.sources:
        full = results.get(name)
        got = recent_source(name, full.src, inp, cfg, avail, full) if full else None
        if got is not None:
            recent[name] = got
    linux = [res for res in results.values() if res.src.linux]
    counts = {res.name: res.counts for res in linux}
    fams = package_families(inp, linux)
    corr = cfg.corrections
    shares = dependency_shares(inp.relations, counts, corr, families=fams)
    found = abstentions(inp.relations, counts, ctx.config.preinstalled, corr, families=fams)
    cases = build_cases(results, found, shares, corr, load_rulings(paths))
    views = build_views(cfg, results, cases, recent)
    preinstalled_on: dict[str, set[str]] = defaultdict(set)
    unresolved = []
    for sid, system in sorted(ctx.config.preinstalled.systems.items()):
        for name in system.families:
            if name in inp.names:
                preinstalled_on[inp.names[name]].add(sid)
            elif name not in inp.ineligible_names:
                unresolved.append((sid, name))
    tags = build_tags(preinstalled_on, pulled_in_by(shares, cases, corr), views, cases)
    ruler = results.get(cfg.engine.ruler)  # its frame is every family with a cask
    counts_out = ruler_counts(ruler.unfloored, sorted(ruler.terms)) if ruler else {}
    stageio.dump_stage(paths, "terms", views)
    stageio.dump_stage(paths, "ruler_counts", {f: _round(v) or 0.0 for f, v in counts_out.items()})
    stageio.dump_stage(paths, "tags", tags)
    report = render_report(
        ctx.run_date,
        results,
        cases,
        inp.families,
        unresolved,
        views,
        recent=recent,
        rising=cfg.ranks.rising.sources,
    )
    jsonio.atomic_write(paths.build / REPORT, report.encode("utf-8"))
    jsonio.dump(questions(cases, inp.families), paths.queues / QUEUE)
    state.write_part(
        paths, "first_seen", next_first_seen(inp.first_seen, results.values()), stage="correct"
    )
    new = sum(c.status == "new" for c in cases)
    ctx.log.info(
        "%d sources corrected; %d gate-X cases (%d new); %d tagged families",
        len(results),
        len(cases),
        new,
        len(tags),
    )
