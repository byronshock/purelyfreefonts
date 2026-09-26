"""Stage "review": the monthly diff, the flags and the review pack (milestone-1 steps 15-16). Owner: agent P12b.

``build/review.md`` holds entries, exits, big moves, license changes and every
§9 flag (RBO and Spearman against last month, coverage changes, stale sources,
ruler overlaps and guard hits, share jumps, cross-check moves, the what-if
table, snapshot growth). ``build/review-pack/`` adds the top-100 lists with
tiers and per-source ranks for the owner's review (gate R).

``build/review.md`` is committed and public: sources whose ``publish_raw`` is
false (Google and Fonts Over Time, rulings T2 and T4) appear in it only as
ranks or z scores. ``build/review-pack/`` is gitignored (owner only).

**Last month.** Last month's orders are ``state/published_ranks.json`` (the
last merged run's, {rank key: {id: order}}), which stage "export" writes for
the catalog's fonts only. The month-on-month comparisons (the diff, RBO and
the summary table) therefore set it against this run's counterpart
(``published``): the part "export" wrote to ``build/state/`` this run, else
the ``ranks.json`` orders of this run's catalog members. Comparing with every
ranked font instead would count each ranked font outside the catalog as new
every month, and flag the top of ranks the catalog does not cover (coding,
dev_apps) as entries every month. Everything that looks at this month alone
(guard hits, cross-checks, disagreements, what-if, the pack's lists) uses
every ranked font (``build/stage/ranks.json``). "The last run" is the newest
``run_history`` entry dated before this run; its ``snapshots`` name the
snapshots each source's month-on-month statistics compare with. On the first
run there is no diff and no month-on-month flag, and the pack compares with
the old Top 100 instead.

**Every flag is a** ``Flag`` **of one kind** (``KINDS``, the order of the
sections in review.md). Thresholds come from ``ranking.toml [review]`` and
the sections below it; the numbers are orders (1 = first), never a source's
values:

- ``entry``, ``exit``: fonts entering or leaving the exact top
  (``display.exact_top``) of each rank key, and the catalog (rank key
  "catalog", from ``build/stage/membership.json`` against the committed
  ``state/membership.json``, so hysteresis counts).
- ``move``: a font in the top ``membership.catalog_size`` either month whose
  order changed by more than ``review.first_run_move_places`` places and by
  more than ``crosscheck.move_flag`` of the better of its two orders (so 20 →
  51 counts, 400 → 431 does not).
- ``from_below``: a font now in the exact top that was below
  ``review.top100_from_below`` last month, or not published.
- ``license``: a changed L3 license text, text URL or level
  (``state/license_hashes.json`` against the one stage "verify" wrote this run).
- ``rbo``: a rank key whose top ``membership.catalog_size`` rank-biased
  overlap (``rbo``, p = ``review.rbo_p``) with last month is under
  ``review.rbo_min``.
- ``spearman``, ``coverage``, ``share_jump``: each enabled engine source,
  this run's snapshot against the last run's (re-parsed from the store and
  mapped with this run's alias index), measured as ``backtest`` measures its
  windows, so its suggested thresholds mean the same thing: the Spearman
  correlation over the fonts in either top ``catalog_size``, the relative
  change in fonts with a value, and one font's share rising or falling more
  than ``review.share_jump_max`` times (shares of at least
  ``ranks.rising.min_share``). Lifetime counters compare lifetime totals.
- ``stale``: sources used stale or dropped (``build/stage/stale.json``, which
  also carries schema mismatches), and engine sources left out after more
  than ``stale.max_months`` stale runs.
- ``ruler_overlap``: a weighted source with fewer than
  ``engine.overlap_full`` fonts in common with the ruler (its weight is scaled
  down, or off under ``engine.overlap_off``).
- ``guard``: outlier-guard hits (``scores.json``) in the top ``catalog_size``.
- ``crosscheck``: ``engine.crosscheck.flags``, fonts moving more than 30%
  under RRF or the Fontsource ruler, in the top ``catalog_size``.
- ``disagreement``: fonts in the top ``review.disagreement_top`` of
  ``desktop_chosen`` or ``project`` but below ``review.disagreement_other``
  in the other, or not ranked there.
- ``term``: term flags (``corrections.TERM_FLAGS``) by source.
- ``growth``: a store source whose newest snapshot is more than
  ``store.GROWTH_FLAG`` bigger than its previous one.
- ``what_if``: each weight of ``ranking.toml`` (survey and view weights,
  project group shares, the overall mix) times each of
  ``review.what_if_factors``, with its effect on every rank key it can move
  (``what_if``). It reranks about 50 times (``surveys.views`` takes about
  0.2 s at 2,000 families).
- ``info``: lines at the top: the runs compared, summary statistics and any
  check that could not run (for example without ``TFF_STORE``).

review.md lists guard hits and cross-check moves inside the exact top only and
says how many more there are; ``review-pack/anomalies.md`` lists every flag.

**The review pack** (``PACK_FILES``): the top 100 of each rank with tiers,
ranges and per-source ranks; fonts held back by the two-group gate; tier-C
fonts; the comparison with the old Top 100 (``OLD_TOP100`` in the store;
methodology §9, first run); most-chosen-versus-project disagreements; every
flag; the what-if table; the per-source statistics. It is rebuilt from
scratch on every run. The old Top 100 and the installed-fonts filter it
reads come from the private store's seed copy and never reach review.md.

Everything is deterministic: the same build, state and store give the same
bytes.
"""

import importlib
import math
import shutil
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tff_catalog import jsonio, stageio, state
from tff_catalog.config_model import RANK_KEYS, SurveyRank, WeightedView

if TYPE_CHECKING:
    from tff_catalog.confidence import Confidence
    from tff_catalog.config_model import RankingConfig, SourceBase
    from tff_catalog.corrections import Term
    from tff_catalog.engine.order import Placement
    from tff_catalog.mapping import AliasIndex
    from tff_catalog.membership import Membership
    from tff_catalog.records import Observation, Relation
    from tff_catalog.stages import StageContext
    from tff_catalog.surveys import RankInputs, SurveyScores
    from tff_catalog.universe import Universe

REVIEW_FILE = "review.md"  # under build/, committed
PACK_DIR = "review-pack"  # under build/, gitignored: the owner's review aids
LIST_MAX = 20  # names listed in one aggregated flag before "and N more"
CATALOG = "catalog"  # the rank-key label of catalog entries and exits
# First run (methodology §9): the old library's Top 100, in the store's seed copy.
OLD_TOP100 = "_seed/oldlib*/final_manifest.json"  # [{rank, family, ...}], newest folder wins
OLD_POOL = "rerank.json"  # beside it: the old candidate pool, each with its `inst` status
OLD_DROPPED = frozenset({"installed", "nerd_patched_only"})  # its installed-fonts filter
INFO = "info"
NONE = "\u2013"  # an en dash: an empty cell, or a range
TIMES = "\u00d7"  # a multiplication sign
# Kinds whose review.md list stops at the exact top; the pack has them all.
CAPPED = frozenset({"guard", "crosscheck"})

# Flag kinds in review.md order: kind -> (heading, what the section lists).
KINDS: dict[str, tuple[str, str]] = {
    "entry": ("Entries", "Fonts new to the exact top of a rank, or to the catalog."),
    "exit": ("Exits", "Fonts that left the exact top of a rank, or the catalog."),
    "move": ("Big moves", "Large order changes, last month → this month."),
    "from_below": ("Top-100 entries from far below", "Entries that came from deep down."),
    "license": ("License changes", "L3 license texts, text URLs or levels that changed."),
    "rbo": ("Rank-biased overlap", "Ranks whose top list overlaps too little with last month's."),
    "spearman": ("Source correlation", "Sources whose order changed too much since the last run."),
    "coverage": ("Coverage changes", "Sources whose count of fonts with a value changed a lot."),
    "share_jump": ("Share jumps", "One font's share in one source jumping up or down."),
    "stale": ("Stale sources and schema mismatches", "Sources used stale or left out."),
    "ruler_overlap": ("Ruler overlaps", "Sources with too few fonts in common with the ruler."),
    "guard": ("Outlier-guard hits", "Terms the outlier guard gave reduced weight."),
    "crosscheck": ("Cross-check moves", "Fonts moving under RRF or the Fontsource ruler."),
    "disagreement": ("Most chosen versus project", "Fonts high in one rank and low in the other."),
    "term": ("Term flags", 'Evidence flags from stage "correct", by source.'),
    "growth": ("Snapshot growth", "Store sources whose snapshots grew fast."),
    "what_if": ("What if", "Each ranking.toml weight halved and doubled."),
}
TERM_NOTES = {
    "parent_merge": "width cuts folded into the parent; weight halved",
    "bundle_only": "counted only through a bundle",
    "dependency_review": "a Linux dependent holds 35-50% of installs; gate X",
    "stale_sync": "ecosyste.ms data not synced recently",
}
PACK_FILES: dict[str, str] = {
    "README.md": "What is here, and the gate R question.",
    "top100.md": "The top 100 of each rank, with tiers, ranges and per-source ranks.",
    "held-back.md": "Fonts the two-group gate held out of the top 100.",
    "tier-c.md": "Tier-C fonts in the top 500 of each rank.",
    "old-top100.md": "The comparison with the old Top 100 (first run).",
    "disagreements.md": "Fonts in the top of most chosen or project but low in the other.",
    "anomalies.md": "Every flag, uncut (review.md stops some lists at the exact top).",
    "what-if.md": "The what-if table: each weight halved and doubled.",
    "sources.md": "Per-source statistics against the last run, ruler overlaps and weights.",
}


@dataclass(frozen=True, slots=True)
class Flag:
    kind: str  # "rbo", "spearman", "stale", "guard", "share_jump", "crosscheck", ...
    message: str
    rank_key: str | None = None
    family_id: str | None = None


# --- rank-biased overlap -----------------------------------------------------------------------


def rbo(a: Sequence[str], b: Sequence[str], p: float = 0.98) -> float:
    """Rank-biased overlap of two orderings (extrapolated).

    RBO_ext of Webber, Moffat and Zobel (2010), eq. 32, which also handles
    lists of different lengths: with S the shorter list (length s), L the
    longer (length l) and X_d the overlap of their first d items,

        RBO = (1-p)/p · (Σ_{d=1..l} X_d/d·p^d + Σ_{d=s+1..l} X_s·(d-s)/(s·d)·p^d)
              + ((X_l - X_s)/l + X_s/s)·p^l

    Equal lengths reduce to ``backtest.rbo``. Identical lists give 1 (so does
    a list against a longer one it begins), disjoint ones 0, two empty lists
    1 and one empty list 0. Raises ``ValueError`` for p outside (0, 1) or a
    list that repeats an item.
    """
    if not 0 < p < 1:
        raise ValueError(f"rbo: p must be in (0, 1), got {p}")
    for items in (a, b):
        if len(set(items)) != len(items):
            raise ValueError("rbo: a list repeats an item")
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    s, n = len(short), len(long)
    if s == 0:
        return 1.0 if n == 0 else 0.0
    seen_short: set[str] = set()
    seen_long: set[str] = set()
    overlap = 0
    x = [0] * (n + 1)
    for d in range(1, n + 1):
        y = long[d - 1]
        if d <= s:
            z = short[d - 1]
            overlap += 1 if z == y else (z in seen_long) + (y in seen_short)
            seen_short.add(z)
        else:
            overlap += y in seen_short
        seen_long.add(y)
        x[d] = overlap
    total = math.fsum(x[d] / d * p**d for d in range(1, n + 1))
    total += math.fsum(x[s] * (d - s) / (s * d) * p**d for d in range(s + 1, n + 1))
    return (1 - p) / p * total + ((x[n] - x[s]) / n + x[s] / s) * p**n


# --- small helpers -----------------------------------------------------------------------------


def _key_order(key: str) -> tuple[int, str]:
    return (RANK_KEYS.index(key), key) if key in RANK_KEYS else (len(RANK_KEYS), key)


def _top(orders: Mapping[str, int], n: int) -> list[str]:
    """The ids with an order of ``n`` or better, best first."""
    return sorted((f for f, o in orders.items() if o <= n), key=lambda f: (orders[f], f))


def _name(names: Mapping[str, str], fid: str) -> str:
    return names.get(fid, fid)


def _listed(names: Sequence[str], limit: int = LIST_MAX) -> str:
    """Names joined as "A, B and C", cut after ``limit`` with "and N more"."""
    shown = list(names[:limit])
    rest = len(names) - len(shown)
    if rest:
        return f"{', '.join(shown)} and {rest} more"
    if len(shown) > 1:
        return f"{', '.join(shown[:-1])} and {shown[-1]}"
    return "".join(shown)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _table(header: Sequence[str], rows: Iterable[Sequence[object]], align: str = "") -> list[str]:
    """A markdown table; ``align`` has one character per column, "r" for right-aligned."""
    align = align.ljust(len(header), "l")
    rule = ["---:" if a == "r" else "---" for a in align]
    out = [f"| {' | '.join(header)} |", f"| {' | '.join(rule)} |"]
    out += [f"| {' | '.join(_cell(str(v)) for v in row)} |" for row in rows]
    return out


def _orders(placements: Mapping[str, Mapping[str, Placement]]) -> dict[str, dict[str, int]]:
    return {k: {f: p.order for f, p in ps.items()} for k, ps in sorted(placements.items())}


# --- the monthly diff --------------------------------------------------------------------------


def diff_flags(
    prev: Mapping[str, Mapping[str, int]],
    now: Mapping[str, Mapping[str, int]],
    names: Mapping[str, str],
    cfg: RankingConfig,
) -> list[Flag]:
    """Entries, exits, big moves and entries from far below, for every rank key.

    ``prev`` and ``now`` are last month's and this month's published
    {rank key: {id: order}} (module docstring): the same kind of list, so a
    font missing from one was not published then. Empty on the first run (no
    ``prev``). A rank key published for the first time gives one entry flag
    instead of its whole top list.
    """
    if not prev:
        return []
    top = cfg.display.exact_top
    out: list[Flag] = []
    for key in sorted(set(prev) | set(now), key=_key_order):
        p, n = prev.get(key, {}), now.get(key, {})
        if not p:
            if n:
                count = len(_top(n, top))
                out.append(
                    Flag("entry", f"first month with published ranks ({count} in the top)", key)
                )
            continue
        out += _entries_exits(key, p, n, names, top)
        out += _moves(key, p, n, names, cfg)
        out += _from_below(key, p, n, names, top, cfg.review.top100_from_below)
    return out


def _entries_exits(
    key: str, p: Mapping[str, int], n: Mapping[str, int], names: Mapping[str, str], top: int
) -> list[Flag]:
    before, after = set(_top(p, top)), set(_top(n, top))
    out = [
        Flag(
            "entry",
            f"{_name(names, f)} enters at {n[f]} (last month {p.get(f, 'not published')})",
            key,
            f,
        )
        for f in sorted(after - before, key=lambda f: (n[f], f))
    ]
    out += [
        Flag(
            "exit",
            f"{_name(names, f)} leaves from {p[f]} (now {n.get(f, 'not published')})",
            key,
            f,
        )
        for f in sorted(before - after, key=lambda f: (p[f], f))
    ]
    return out


def _moves(
    key: str,
    p: Mapping[str, int],
    n: Mapping[str, int],
    names: Mapping[str, str],
    cfg: RankingConfig,
) -> list[Flag]:
    places, share = cfg.review.first_run_move_places, cfg.crosscheck.move_flag
    depth = cfg.membership.catalog_size
    found = []
    for f in sorted(p.keys() & n.keys()):
        better, change = min(p[f], n[f]), abs(n[f] - p[f])
        if better <= depth and change > places and change > share * better:
            found.append(f)
    return [
        Flag("move", f"{_name(names, f)}: {p[f]} → {n[f]}", key, f)
        for f in sorted(found, key=lambda f: (min(p[f], n[f]), f))
    ]


def _from_below(
    key: str,
    p: Mapping[str, int],
    n: Mapping[str, int],
    names: Mapping[str, str],
    top: int,
    below: int,
) -> list[Flag]:
    out = []
    for f in _top(n, top):
        was = p.get(f)
        if was is None or was > below:
            since = "not published last month" if was is None else f"from {was}"
            out.append(Flag("from_below", f"{_name(names, f)} at {n[f]}, {since}", key, f))
    return out


def catalog_flags(
    committed: Mapping[str, Any],
    m: Membership,
    names: Mapping[str, str],
    overall: Mapping[str, int],
    failed_l3: frozenset[str] = frozenset(),
) -> list[Flag]:
    """Catalog entries and exits: this run's membership against ``state/membership.json``.

    Empty on the first run (no committed members).
    """
    before = {f for f, s in (committed.get("catalog") or {}).items() if s.get("member")}
    if not before:
        return []
    after = set(m.members())

    def where(f: str) -> str:
        return f"overall {overall[f]}" if f in overall else "not ranked overall"

    out = [
        Flag("entry", f"{_name(names, f)} joins the catalog ({where(f)})", CATALOG, f)
        for f in sorted(after - before, key=lambda f: (overall.get(f, math.inf), f))
    ]
    for f in sorted(before - after, key=lambda f: (overall.get(f, math.inf), f)):
        why = "; failed L3" if f in failed_l3 else ""
        out.append(
            Flag("exit", f"{_name(names, f)} leaves the catalog ({where(f)}{why})", CATALOG, f)
        )
    return out


def license_flags(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, str],
    spdx: Mapping[str, str | None] | None = None,
) -> list[Flag]:
    """Changed L3 license texts, text URLs and levels (``license_hashes`` state files).

    ``before`` is the committed state, ``after`` this run's. Families checked
    for the first time are counted in one flag (none on the first run);
    ``spdx`` (the L2 expression by family) is added to each message when given.
    """
    out = []
    for f in sorted(before.keys() & after.keys(), key=lambda f: (_name(names, f).casefold(), f)):
        b, a = before[f], after[f]
        changes = []
        if b.get("level") != a.get("level"):
            changes.append(f"level {b.get('level')} → {a.get('level')}")
        if b.get("text_sha256") != a.get("text_sha256"):
            changes.append(
                f"license text changed ({_sha(b.get('text_sha256'))} → {_sha(a.get('text_sha256'))})"
            )
        elif b.get("text_url") != a.get("text_url"):
            changes.append(f"license text now read from {a.get('text_url')}")
        if changes:
            now = f"; L2 {spdx[f]}" if spdx and spdx.get(f) else ""
            out.append(Flag("license", f"{_name(names, f)}: {'; '.join(changes)}{now}", None, f))
    new = sorted(after.keys() - before.keys(), key=lambda f: (_name(names, f).casefold(), f))
    if before and new:
        listed = _listed([_name(names, f) for f in new])
        out.append(
            Flag("license", f"{_plural(len(new), 'font')} checked for the first time: {listed}")
        )
    return out


def _sha(value: object) -> str:
    return f"{value}"[:12] if value else "none"


def disagreement_flags(
    now: Mapping[str, Mapping[str, int]], names: Mapping[str, str], cfg: RankingConfig
) -> list[Flag]:
    """Fonts in the top of most chosen or project but low in the other, or not ranked there."""
    top, other = cfg.review.disagreement_top, cfg.review.disagreement_other
    pair = ("desktop_chosen", "project")
    out = []
    for x, y in (pair, pair[::-1]):
        ox, oy = now.get(x, {}), now.get(y, {})
        if not oy:
            continue
        for f in _top(ox, top):
            o = oy.get(f)
            if o is None or o > other:
                there = "not ranked" if o is None else str(o)
                out.append(
                    Flag("disagreement", f"{_name(names, f)}: {x} {ox[f]}, {y} {there}", x, f)
                )
    return out


# --- review.md ---------------------------------------------------------------------------------


def render_review(
    prev_ranks: Mapping[str, Mapping[str, int]],
    ranks: Mapping[str, Mapping[str, int]],
    flags: Iterable[Flag],
) -> str:
    """``build/review.md``.

    ``prev_ranks`` and ``ranks`` are last month's and this month's published
    {rank key: {id: order}} (module docstring); they give the summary table. Everything else comes
    in as flags: ``info`` flags are the lines at the top, every other kind is
    a section (``KINDS`` order, then unknown kinds by name), listed in the order
    given. The diff itself is flags too (``diff_flags``, ``catalog_flags``), so
    its names and thresholds follow ranking.toml. Deterministic for a given
    input order.
    """
    flags = list(flags)
    by_kind: dict[str, list[Flag]] = defaultdict(list)
    for flag in flags:
        by_kind[flag.kind].append(flag)
    lines = ["# Monthly review", ""]
    lines += [f"- {_cell(f.message)}" for f in by_kind.pop(INFO, [])]
    if lines[-1]:
        lines.append("")
    lines += _summary(prev_ranks, ranks, by_kind)
    raised = {k: len(v) for k, v in by_kind.items() if k != "what_if" and v}
    counts = ", ".join(
        f"{k} {v}" for k, v in sorted(raised.items(), key=lambda kv: _kind_order(kv[0]))
    )
    lines += ["", f"Flags: {sum(raised.values())}" + (f" ({counts})." if counts else "."), ""]
    for kind in sorted(set(KINDS) | set(by_kind), key=_kind_order):
        lines += _section(kind, by_kind.get(kind, []))
    return "\n".join(lines).rstrip("\n") + "\n"


def _kind_order(kind: str) -> tuple[int, str]:
    kinds = list(KINDS)
    return (kinds.index(kind), kind) if kind in KINDS else (len(kinds), kind)


def _summary(
    prev: Mapping[str, Mapping[str, int]],
    now: Mapping[str, Mapping[str, int]],
    by_kind: Mapping[str, list[Flag]],
) -> list[str]:
    counted: dict[tuple[str, str | None], int] = defaultdict(int)
    for kind in ("entry", "exit", "move"):
        for f in by_kind.get(kind, []):
            if f.family_id is not None:
                counted[kind, f.rank_key] += 1
    keys = set(prev) | set(now) | {k for _, k in counted if k is not None}
    rows = []
    for key in sorted(keys, key=_key_order):
        p, n = prev.get(key), now.get(key)
        ranked = NONE if n is None and key not in RANK_KEYS else len(n or {})
        last = NONE if not prev or (p is None and key not in RANK_KEYS) else len(p or {})
        new = NONE if not prev or key not in now else len(set(n or {}) - set(p or {}))
        gone = NONE if not prev or key not in now else len(set(p or {}) - set(n or {}))
        moves = [counted.get((kind, key), 0) for kind in ("entry", "exit", "move")]
        rows.append([key, ranked, last, new, gone, *moves])
    header = ["Rank", "Published", "Last month", "New", "Gone", "Entries", "Exits", "Big moves"]
    return ["## Summary", "", *_table(header, rows, "lrrrrrrr")]


def _section(kind: str, flags: Sequence[Flag]) -> list[str]:
    heading, blurb = KINDS.get(kind, (kind.replace("_", " ").capitalize(), ""))
    lines = [f"## {heading}", ""]
    if blurb:
        lines += [blurb, ""]
    if not flags:
        return [*lines, "None.", ""]
    if any(f.rank_key for f in flags):
        rows = [[f.rank_key or NONE, f.message] for f in flags]
        return [*lines, *_table(["Rank", "Flag"], rows), ""]
    return [*lines, *(f"- {_cell(f.message)}" for f in flags), ""]


# --- flags from the stage files ----------------------------------------------------------------


def stale_flags(
    stale: Mapping[str, Any], stale_state: Mapping[str, Any], cfg: RankingConfig
) -> list[Flag]:
    """``build/stage/stale.json`` entries (``parse.StaleSource``), then the engine sources
    left out after too many stale runs (``surveys.stale_dropped`` on the stale state)."""
    from tff_catalog import surveys

    out = []
    for name in sorted(stale):
        s = stale[name]
        if s.dropped:
            msg = f"`{name}`: dropped this run, no usable snapshot ({s.reason})"
        else:
            runs = _plural(s.stale_runs, "run")
            msg = (
                f"`{name}`: stale, using the snapshot of {s.snapshot} "
                f"({s.age_days} days old, stale for {runs}; {s.reason})"
            )
        out.append(Flag("stale", msg))
    for src in sorted(surveys.stale_dropped(cfg, stale_state)):
        months = cfg.stale.max_months
        out.append(Flag("stale", f"`{src}`: left out of every rank, stale for over {months} runs"))
    return out


def overlap_flags(
    scores: Mapping[str, SurveyScores], cfg: RankingConfig, dropped: frozenset[str] = frozenset()
) -> list[Flag]:
    """Weighted sources with fewer than ``overlap_full`` fonts in common with the ruler."""
    from tff_catalog import surveys
    from tff_catalog.engine import equate

    full, off = cfg.engine.overlap_full, cfg.engine.overlap_off
    sources = cfg.sources.all()
    out = []
    for key in RANK_KEYS:
        if key in ("overall", "rising") or key not in scores:
            continue
        for s, w in sorted(surveys.nominal_weights(cfg, key).items()):
            if w <= 0 or not sources[s].enabled or s in dropped or s == cfg.engine.ruler:
                continue
            n = scores[key].overlaps.get(s, 0)
            if n >= full:
                continue
            scale = equate.overlap_scale(n, full, off)
            effect = f"under {off}: switched off" if scale == 0 else f"weight {TIMES} {scale:.2f}"
            out.append(
                Flag("ruler_overlap", f"`{s}`: {n} fonts in common with the ruler, {effect}", key)
            )
    return out


def guard_flags(
    scores: Mapping[str, SurveyScores],
    now: Mapping[str, Mapping[str, int]],
    names: Mapping[str, str],
    limit: int,
) -> list[Flag]:
    """Outlier-guard hits of fonts at order ``limit`` or better, per survey or view.

    Overall reuses its surveys' guard factors (not listed twice); Rising has no guard.
    """
    out = []
    for key in RANK_KEYS:
        if key in ("overall", "rising") or key not in scores:
            continue
        orders = now.get(key, {})
        hits = sorted(
            (orders[f], f, fused.guard)
            for f, fused in scores[key].fused.items()
            if fused.guard and f in orders and orders[f] <= limit
        )
        for order, f, guard in hits:
            terms = ", ".join(f"`{s}` {TIMES} {factor:g}" for s, factor in guard)
            out.append(Flag("guard", f"{_name(names, f)} ({order}): {terms}", key, f))
    return out


def term_flags(
    terms_all: Mapping[str, Mapping[str, Mapping[str, Term]]],
    names: Mapping[str, str],
    overall: Mapping[str, int],
) -> list[Flag]:
    """One flag per (term flag, source), naming its fonts best-ranked first, across every view."""
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for by_source in terms_all.values():
        for source, by_id in by_source.items():
            for f, term in by_id.items():
                for flag in term.flags:
                    found[flag, source].add(f)
    out = []
    for flag, source in sorted(found):
        fids = sorted(
            found[flag, source],
            key=lambda f: (overall.get(f, math.inf), _name(names, f).casefold(), f),
        )
        note = TERM_NOTES.get(flag)
        what = f"{flag} ({note})" if note else flag
        listed = _listed([_name(names, f) for f in fids])
        one = fids[0] if len(fids) == 1 else None
        out.append(
            Flag("term", f"`{source}` {what}: {_plural(len(fids), 'font')}, {listed}", None, one)
        )
    return out


# --- per-source statistics against the last run -----------------------------------------------


@dataclass(frozen=True, slots=True)
class Jump:
    family_id: str
    ratio: float  # max(share now / share then, share then / share now)
    up: bool
    rank_before: int | None  # rank among the source's fonts with a value (1 = most)
    rank_now: int | None


@dataclass(frozen=True, slots=True)
class SourceStats:
    """One engine source, this run's snapshot against the last run's (module docstring)."""

    source: str
    previous: date | None  # the last run's snapshot of the source's collector
    current: date | None
    counted: tuple[int, int] = (0, 0)  # fonts with a value, then and now
    spearman: float | None = None
    rbo: float | None = None
    coverage: float | None = None  # |n_now - n_then| / n_then
    jumps: tuple[Jump, ...] = ()  # above review.share_jump_max, biggest first
    note: str = ""  # why a statistic is missing, or "lifetime totals"


def _collectors_of(src: SourceBase) -> list[str]:
    from tff_catalog.config_model import GoogleSource

    out = [src.collector]
    if isinstance(src, GoogleSource) and src.fallback:
        out.append(src.fallback.partition(":")[0])
    return out


def family_values(
    src: SourceBase,
    recs: Mapping[str, Sequence[Observation | Relation]],
    idx: AliasIndex,
) -> dict[str, float]:
    """{family id: volume} for engine source ``src``, as stage "map" measures keys
    (``mapping.engine_observations`` and ``volumes``), summed over each family's keys.
    Keys that do not resolve to an eligible family are left out."""
    from tff_catalog import mapping

    _, rows = mapping.engine_observations(src, recs)
    out: dict[str, float] = defaultdict(float)
    for key, value in sorted(mapping.volumes(src, rows).items()):
        hit = mapping.resolve(key, idx)
        if hit is not None and hit[0] is not None:
            out[hit[0]] += value
    return dict(sorted(out.items()))


def _in_order(values: Mapping[str, float]) -> dict[str, int]:
    """Rank among the fonts with a positive value, 1 = most (ties by id)."""
    ranked = sorted((f for f, v in values.items() if v > 0), key=lambda f: (-values[f], f))
    return {f: i for i, f in enumerate(ranked, 1)}


def compare_values(
    name: str,
    then: Mapping[str, float],
    now: Mapping[str, float],
    cfg: RankingConfig,
    dates: tuple[date | None, date | None] = (None, None),
) -> SourceStats:
    """The month-on-month statistics of one source (``backtest``'s definitions)."""
    from tff_catalog import backtest

    depth = cfg.membership.catalog_size
    ta, tb = backtest.top(then, depth), backtest.top(now, depth)
    items = set(ta) | set(tb)
    counted = (sum(1 for v in then.values() if v > 0), sum(1 for v in now.values() if v > 0))
    return SourceStats(
        source=name,
        previous=dates[0],
        current=dates[1],
        counted=counted,
        spearman=backtest.spearman(then, now, items),
        rbo=rbo(ta, tb, cfg.review.rbo_p),
        coverage=backtest.coverage_change(then, now),
        jumps=_jumps(then, now, items, cfg.ranks.rising.min_share, cfg.review.share_jump_max),
    )


def _jumps(
    then: Mapping[str, float],
    now: Mapping[str, float],
    items: Iterable[str],
    min_share: float,
    limit: float,
) -> tuple[Jump, ...]:
    """Share ratios over ``limit`` for fonts with a value both times and a share of at
    least ``min_share`` in one (``backtest.share_jumps``, keeping the fonts)."""
    total_a = math.fsum(v for v in then.values() if v > 0)
    total_b = math.fsum(v for v in now.values() if v > 0)
    if total_a <= 0 or total_b <= 0:
        return ()
    ra, rb = _in_order(then), _in_order(now)
    out = []
    for f in sorted(set(items)):
        sa, sb = then.get(f, 0.0) / total_a, now.get(f, 0.0) / total_b
        if sa > 0 and sb > 0 and max(sa, sb) >= min_share:
            ratio = max(sb / sa, sa / sb)
            if ratio > limit:
                out.append(Jump(f, ratio, sb > sa, ra.get(f), rb.get(f)))
    return tuple(sorted(out, key=lambda j: (-j.ratio, j.family_id)))


def source_flags(
    stats: Iterable[SourceStats], names: Mapping[str, str], cfg: RankingConfig
) -> list[Flag]:
    """The ``spearman``, ``coverage`` and ``share_jump`` flags of the per-source statistics.

    A source whose ``publish_raw`` is false shows its jumps as ranks only.
    """
    r = cfg.review
    sources = cfg.sources.all()
    out = []
    for st in stats:
        since = f" (snapshots {st.previous} → {st.current})" if st.previous else ""
        if st.spearman is not None and st.spearman < r.spearman_min:
            out.append(
                Flag("spearman", f"`{st.source}`: {st.spearman:.3f}, under {r.spearman_min}{since}")
            )
        if st.coverage is not None and st.coverage > r.coverage_change_max:
            a, b = st.counted
            msg = f"`{st.source}`: {a} → {b} fonts with a value ({(b - a) / a:+.0%}){since}"
            out.append(Flag("coverage", msg))
        raw = sources[st.source].publish_raw if st.source in sources else False
        for j in st.jumps:
            ranks = f"rank {j.rank_before} → {j.rank_now}"
            what = (
                f"share {'up' if j.up else 'down'} {j.ratio:.1f}{TIMES}, {ranks}" if raw else ranks
            )
            out.append(
                Flag(
                    "share_jump",
                    f"{_name(names, j.family_id)}: `{st.source}` {what}",
                    None,
                    j.family_id,
                )
            )
    return out


def _load_collector(name: str) -> Any:
    """The collector called ``name``, imported directly (not ``discover``, so one
    broken collector module does not stop the others)."""
    for kind in ("ranking", "universe"):
        try:
            module = importlib.import_module(f"tff_catalog.collectors.{kind}.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"tff_catalog.collectors.{kind}.{name}":
                raise
            continue
        return module.COLLECTOR
    raise KeyError(f"no collector {name!r}")


def previous_records(
    ctx: StageContext, collectors: Iterable[str], run: Mapping[str, Any]
) -> tuple[dict[str, list[Observation | Relation]], dict[str, date], dict[str, str]]:
    """Each collector's records from the snapshot the last run used, re-parsed.

    Returns (records by collector, snapshot date by collector, note by collector
    that could not be read). Needs the store; parse failures are logged and noted.
    """
    from tff_catalog import parse
    from tff_catalog.collectors.base import load_settings
    from tff_catalog.records import Observation, Relation

    store = ctx.require_store()
    pointers = run.get("snapshots") or {}
    recs: dict[str, list[Observation | Relation]] = {}
    days: dict[str, date] = {}
    notes: dict[str, str] = {}
    for name in sorted(set(collectors)):
        pointer = pointers.get(name)
        if pointer is None:
            notes[name] = "the last run used no snapshot of it"
            continue
        day = date.fromisoformat(str(pointer))
        snap = store.snapshot(name, day)
        if snap is None:
            notes[name] = f"its snapshot of {day} is not in the store"
            continue
        try:
            collector = _load_collector(name)
            settings = load_settings(collector, ctx.paths)
            parsed = parse.parse_source(collector, snap, settings, ctx.log.getChild(name))
        except Exception as exc:  # another agent's collector: note it, never stop the review
            ctx.log.exception("review: re-parse of %s %s failed", name, day)
            notes[name] = f"re-parse of {day} failed ({type(exc).__name__})"
            continue
        recs[name] = [r for r in parsed if isinstance(r, Observation | Relation)]
        days[name] = day
    return recs, days, notes


def source_stats(
    ctx: StageContext, run: Mapping[str, Any] | None, notes: list[str]
) -> tuple[SourceStats, ...]:
    """Every enabled engine source against the last run's snapshot (module docstring).

    Appends to ``notes`` when the checks cannot run at all (no earlier run, no
    store, no records or alias index).
    """
    from tff_catalog import mapping, parse

    if run is None:
        return ()
    if ctx.store is None:
        notes.append("Sources month on month: skipped, no snapshot store (TFF_STORE is not set).")
        return ()
    try:
        inputs = mapping.load_inputs(ctx.paths)
    except (FileNotFoundError, ValueError, stageio.StageFileError) as exc:
        notes.append(
            f"Sources month on month: skipped, no records or alias index ({type(exc).__name__})."
        )
        return ()
    try:
        current = parse.load_snapshots(ctx.paths)
    except FileNotFoundError, stageio.StageFileError:
        current = {}
    cfg = ctx.config.ranking
    enabled = {n: s for n, s in sorted(cfg.sources.all().items()) if s.enabled}
    wanted = {c for s in enabled.values() for c in _collectors_of(s)}
    same = {c for c in wanted if c in current and _pointer(run, c) == current[c].snapshot}
    prev, days, problems = previous_records(ctx, wanted - same, run)
    out = []
    for name, src in enabled.items():
        collector = src.collector
        dates = (days.get(collector) or _pointer(run, collector), _current(current, collector))
        if collector in same:
            out.append(SourceStats(name, *dates, note="same snapshot as the last run"))
        elif collector in problems:
            out.append(SourceStats(name, *dates, note=problems[collector]))
        else:
            then = family_values(src, prev, inputs.idx)
            now = family_values(src, inputs.current, inputs.idx)
            if not then or not now:
                which = "the last run's" if not then else "this run's"
                out.append(SourceStats(name, *dates, note=f"no values in {which} snapshot"))
                continue
            st = compare_values(name, then, now, cfg, dates)
            if src.counting == "snapshot_delta":
                st = replace(st, note="lifetime totals")
            out.append(st)
    return tuple(out)


def _pointer(run: Mapping[str, Any], collector: str) -> date | None:
    value = (run.get("snapshots") or {}).get(collector)
    return None if value is None else date.fromisoformat(str(value))


def _current(current: Mapping[str, Any], collector: str) -> date | None:
    snap = current.get(collector)
    return None if snap is None else snap.snapshot


# --- snapshot growth ---------------------------------------------------------------------------


def growth_flags(ctx: StageContext, run: Mapping[str, Any] | None) -> tuple[list[Flag], str]:
    """Store sources whose newest snapshot (on or before the run's day) is more than
    ``store.GROWTH_FLAG`` bigger than their previous one, and a line with the totals.

    With an earlier run, only snapshots newer than its date are judged, so a source
    that stays stale is not flagged again every month.
    """
    from tff_catalog.store import GROWTH_FLAG

    store = ctx.store
    if store is None:
        return [], ""
    day = ctx.options.from_snapshots or ctx.run_date
    since = date.fromisoformat(str(run["run_date"])) if run else None
    out = []
    total = before_total = 0
    for source in store.sources():
        days = [d for d in store.dates(source) if d <= day]
        if not days:
            continue
        size = _bytes(store, source, days[-1])
        total += size
        if since is not None:
            earlier = [d for d in days if d <= since]
            before_total += _bytes(store, source, earlier[-1]) if earlier else 0
        if len(days) < 2 or (since is not None and days[-1] <= since):
            continue
        old = _bytes(store, source, days[-2])
        if old > 0 and size > old * (1 + GROWTH_FLAG):
            # Sizes rounded, never exact byte counts: validate scans committed reports
            # for whole numbers that could be a private source's value.
            grew = f"{(size - old) / old:+.0%}"
            msg = f"`{source}`: {_size(old)} → {_size(size)} ({grew}), {days[-2]} → {days[-1]}"
            out.append(Flag("growth", msg))
    line = f"Snapshot store: {_size(total)} in the newest snapshots used this run"
    if since is not None:
        line += f" ({_size(before_total)} at the last run)"
    return out, line + "."


def _size(n: int) -> str:
    """Bytes in kB or MB (decimal), one decimal place."""
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.1f} kB"


def _bytes(store: Any, source: str, day: date) -> int:
    snap = store.snapshot(source, day)
    return 0 if snap is None else snap.manifest.extract_bytes


# --- what if -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Knob:
    """One weight of ranking.toml that the what-if table varies."""

    path: str  # its ranking.toml key, e.g. "surveys.desktop.weights.homebrew"
    kind: Literal["survey", "view", "group", "mix"]
    owner: str  # the survey, view, project group or mix
    name: str  # the source, or the mix's rank key ("" for a group share)
    keys: tuple[str, ...]  # the rank keys it can move, RANK_KEYS order


@dataclass(frozen=True, slots=True)
class Effect:
    key: str
    rbo: float  # top catalog_size against the unchanged order
    entered: tuple[str, ...]  # ids new to the exact top
    left: tuple[str, ...]
    largest: tuple[str, int, int | None] | None  # (id, order, new order) in either exact top


@dataclass(frozen=True, slots=True)
class WhatIf:
    knob: Knob
    factor: float
    live: bool  # the weight carries weight this run (not switched off, stale or disabled)
    effects: tuple[Effect, ...]
    note: str = ""  # e.g. the key that sets this weight this run instead of ``knob.path``


def knobs(cfg: RankingConfig) -> list[Knob]:
    """Every weight the what-if table varies: survey weights, view weights, project
    group shares, then the overall mix, each by name (the config loader sorts them).

    Survey weights move the survey's rank keys and overall; view weights their
    view; project group shares move project and overall; the overall mix moves
    overall. Disabled sources are left out.
    """
    sources = cfg.sources.all()
    survey_keys = {
        survey: tuple(
            k
            for k in RANK_KEYS
            if isinstance(spec := getattr(cfg.ranks, k), SurveyRank) and spec.survey == survey
        )
        for survey in ("desktop", "project")
    }
    mixed = {getattr(getattr(cfg.ranks, k), "survey", None) for k in cfg.ranks.overall.mix}
    out = []
    for survey, keys in survey_keys.items():
        with_overall = ("overall", *keys) if survey in mixed else keys
        for s, w in getattr(cfg.surveys, survey).weights.items():
            if w > 0 and sources[s].enabled:
                path = f"surveys.{survey}.weights.{s}"
                out.append(Knob(path, "survey", survey, s, with_overall))
    for view in RANK_KEYS:
        spec = getattr(cfg.ranks, view)
        if isinstance(spec, WeightedView):
            for s, w in spec.weights.items():
                if w > 0 and sources[s].enabled:
                    out.append(Knob(f"ranks.{view}.weights.{s}", "view", view, s, (view,)))
    project = ("overall", *survey_keys["project"])
    out += [
        Knob(f"project_group_shares.{group}.share", "group", group, "", project)
        for group in cfg.project_group_shares
    ]
    out += [
        Knob(f"ranks.overall.mix.{key}", "mix", "overall", key, ("overall",))
        for key in cfg.ranks.overall.mix
    ]
    return out


def vary(
    inputs: RankInputs, cfg: RankingConfig, knob: Knob, factor: float
) -> tuple[RankInputs, RankingConfig, bool]:
    """``inputs`` and ``cfg`` with ``knob`` multiplied by ``factor``, and whether it is live.

    The effective weights move as the nominal ones would: w_eff is linear in w,
    and under gate M9 (b) a project source's group keeps its fixed share, so
    only its split inside the group changes. A group share scales the whole
    group; the mix changes ``ranks.overall.mix``. Overall's own weights are
    recomputed from the mix keys (``surveys.mix_weights``).
    """
    from tff_catalog import surveys

    weights = {k: dict(ws) for k, ws in inputs.weights.items()}
    live = False
    if knob.kind in ("survey", "view"):
        for key in knob.keys:
            if key == "overall" or knob.name not in weights.get(key, {}):
                continue
            live = live or weights[key][knob.name] > 0
            weights[key][knob.name] *= factor
            if knob.kind == "survey" and knob.owner == "project":
                _keep_group_shares(weights[key], inputs.weights[key], cfg)
    elif knob.kind == "group":
        members = cfg.project_group_shares[knob.owner].sources
        for key in knob.keys:
            for s in members:
                if key != "overall" and s in weights.get(key, {}):
                    live = live or weights[key][s] > 0
                    weights[key][s] *= factor
    else:
        mix = {k: m * factor if k == knob.name else m for k, m in cfg.ranks.overall.mix.items()}
        cfg = replace(cfg, ranks=replace(cfg.ranks, overall=replace(cfg.ranks.overall, mix=mix)))
        live = cfg.ranks.overall.mix[knob.name] > 0
    weights["overall"] = surveys.mix_weights(cfg.ranks.overall.mix, weights)
    return replace(inputs, weights=weights), cfg, live


def _keep_group_shares(
    ws: dict[str, float], before: Mapping[str, float], cfg: RankingConfig
) -> None:
    for group in cfg.project_group_shares.values():
        members = [s for s in group.sources if s in ws]
        was, now = math.fsum(before[s] for s in members), math.fsum(ws[s] for s in members)
        if now > 0 and was != now:
            for s in members:
                ws[s] *= was / now


def effect(key: str, base: Mapping[str, int], new: Mapping[str, int], cfg: RankingConfig) -> Effect:
    """How rank key ``key`` moved from ``base`` to ``new`` ({id: order})."""
    top, depth = cfg.display.exact_top, cfg.membership.catalog_size
    a, b = set(_top(base, top)), set(_top(new, top))
    last = len(new) + 1  # an unranked font counts one below the last (as in confidence)
    largest = None
    moved = sorted(
        ((abs(new.get(f, last) - base.get(f, last)), f) for f in a | b),
        key=lambda m: (-m[0], base.get(m[1], last), m[1]),
    )
    if moved and moved[0][0] > 0:
        f = moved[0][1]
        largest = (f, base.get(f, last), new.get(f))
    return Effect(
        key=key,
        rbo=rbo(_top(base, depth), _top(new, depth), cfg.review.rbo_p),
        entered=tuple(sorted(b - a, key=lambda f: (new[f], f))),
        left=tuple(sorted(a - b, key=lambda f: (base[f], f))),
        largest=largest,
    )


def what_if(
    inputs: RankInputs, cfg: RankingConfig, phasing: frozenset[str] = frozenset()
) -> tuple[WhatIf, ...]:
    """Every knob (``knobs``) times every ``review.what_if_factors``, against the
    unchanged ranks recomputed from the same inputs (so a factor of 1 moves nothing).

    ``phasing`` names the sources still phasing in: their weight this run is
    ``sources.<name>.phase_in_weight``, not the knob's key, and the row says so.
    """
    from tff_catalog import surveys

    base = _orders({k: s.placements for k, s in surveys.views(inputs, cfg).items()})
    out = []
    for knob in knobs(cfg):
        note = ""
        if knob.kind in ("survey", "view") and knob.name in phasing:
            note = f"phasing in: this run's weight is `sources.{knob.name}.phase_in_weight`"
        for factor in cfg.review.what_if_factors:
            varied, varied_cfg, live = vary(inputs, cfg, knob, factor)
            if not live:
                out.append(WhatIf(knob, factor, False, (), note))
                continue
            scores = surveys.views(varied, varied_cfg)
            orders = _orders({k: s.placements for k, s in scores.items()})
            effects = tuple(effect(k, base.get(k, {}), orders.get(k, {}), cfg) for k in knob.keys)
            out.append(WhatIf(knob, factor, True, effects, note))
    return tuple(out)


def phasing_in(ctx: StageContext) -> frozenset[str]:
    """The sources still phasing in this run (``surveys.phase_in`` on the Fonts Over
    Time weeks that stage "rank" counted into its ``smoothing`` part)."""
    from tff_catalog import surveys

    weeks = state.read_part(ctx.paths, "smoothing").get("fot_weeks", [])
    return frozenset(s for s, on in surveys.phase_in(ctx.config.ranking, weeks).items() if on)


def what_if_flags(rows: Iterable[WhatIf], names: Mapping[str, str], top: int) -> list[Flag]:
    """One ``what_if`` flag per row, naming only orders."""
    out = []
    for row in rows:
        head = f"`{row.knob.path}` {TIMES} {row.factor:g}"
        if row.note:
            head += f" ({row.note})"
        if not row.live:
            out.append(Flag("what_if", f"{head}: off this run, nothing moves"))
            continue
        parts = [_effect_text(e, names, top) for e in row.effects]
        out.append(Flag("what_if", f"{head}: {'; '.join(parts)}"))
    return out


def _effect_text(e: Effect, names: Mapping[str, str], top: int) -> str:
    if _unchanged(e):
        return f"{e.key} unchanged"
    text = f"{e.key} RBO {e.rbo:.3f}, {len(e.entered)} in and {len(e.left)} out of the top {top}"
    if e.largest is None:
        return f"{text}, no move inside it"
    f, a, b = e.largest
    return f"{text}, largest move {_name(names, f)} {a} → {b if b is not None else 'not ranked'}"


def _unchanged(e: Effect) -> bool:
    return e.rbo >= 1 - 1e-12 and not e.entered and not e.left and e.largest is None


# --- the old Top 100 (first run) ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OldComparison:
    """The old library's Top 100 against this run's overall order (methodology §9)."""

    seed: str  # the seed file, relative to the store
    compared: tuple[tuple[str, int, int], ...]  # (id, old rank, new position), by old rank
    missing: tuple[tuple[str, int], ...]  # (old name, old rank): not found or not ranked now
    repeats: tuple[tuple[str, int, str], ...]  # (old name, old rank, id): a better row's family
    new_only: tuple[str, ...]  # ids in the new top list the old list lacks
    old_out: tuple[tuple[str, int, int], ...]  # compared fonts now outside the new top list
    spearman: float | None
    rbo: float
    left_out: int  # ranked families the old installed-fonts filter would have dropped


def old_top100(
    store_root: Path | None,
    universe: Universe,
    overall: Mapping[str, int],
    cfg: RankingConfig,
) -> OldComparison | None:
    """Compare on families eligible in both and not dropped by the old installed filter.

    The new position is the font's place in this run's overall order without
    the fonts the old list could not include (its installed-fonts filter), so
    both sides rank the same kind of pool. None when the seed is not there.
    """
    from tff_catalog import backtest

    files = sorted(store_root.glob(OLD_TOP100)) if store_root is not None else []
    if not files:
        return None
    path = files[-1]
    rows = sorted((int(r["rank"]), str(r["family"])) for r in jsonio.load(path))
    pool = path.parent / OLD_POOL
    dropped_names = (
        {str(r["family"]) for r in jsonio.load(pool) if r.get("inst") in OLD_DROPPED}
        if pool.is_file()
        else set()
    )
    find = _finder(universe)
    dropped = {fid for n in dropped_names if (fid := find(n)) is not None}
    order = [f for f in sorted(overall, key=lambda f: (overall[f], f)) if f not in dropped]
    position = {f: i for i, f in enumerate(order, 1)}
    compared, missing, repeats = [], [], []
    seen: set[str] = set()
    for rank, family in rows:
        fid = find(family)
        if fid is None or fid not in position:
            missing.append((family, rank))
        elif fid in seen:  # two old names of one family now (a rename): keep the better row
            repeats.append((family, rank, fid))
        else:
            seen.add(fid)
            compared.append((fid, rank, position[fid]))
    size = cfg.display.exact_top
    old_ids = [f for f, _, _ in compared]
    new_top = order[:size]
    return OldComparison(
        seed=path.relative_to(store_root).as_posix() if store_root else path.name,
        compared=tuple(compared),
        missing=tuple(missing),
        repeats=tuple(repeats),
        new_only=tuple(f for f in new_top if f not in set(old_ids)),
        old_out=tuple(c for c in compared if c[2] > size),
        spearman=backtest.spearman(
            {f: float(r) for f, r, _ in compared}, {f: float(p) for f, _, p in compared}, old_ids
        ),
        rbo=rbo(old_ids, new_top, cfg.review.rbo_p),
        left_out=sum(1 for f in overall if f in dropped),
    )


def _finder(universe: Universe) -> Any:
    """name -> eligible family id: its display name (``match_key``), else a Google family key."""
    from tff_catalog.keys import match_key
    from tff_catalog.records import SourceKey

    eligible = universe.eligible()
    by_name: dict[str, str] = {}
    for fid in sorted(eligible):
        by_name.setdefault(match_key(eligible[fid].family), fid)

    def find(name: str) -> str | None:
        fid = by_name.get(match_key(name))
        if fid is None:
            fam = universe.by_key(SourceKey("gf-family", name))
            fid = fam.id if fam is not None and fam.id in eligible else None
        return fid

    return find


# --- the stage ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Analysis:
    """Everything review.md and the pack are written from."""

    run_date: date
    names: Mapping[str, str]
    prev: Mapping[str, Mapping[str, int]]  # last month's published orders
    now: Mapping[str, Mapping[str, int]]  # every ranked font's order (ranks.json)
    published: Mapping[str, Mapping[str, int]]  # this month's published orders (``published``)
    flags: tuple[Flag, ...]  # review.md
    all_flags: tuple[Flag, ...]  # the pack's anomalies.md: nothing cut
    placements: Mapping[str, Mapping[str, Placement]]
    scores: Mapping[str, SurveyScores]
    confidence: Mapping[str, Mapping[str, Confidence]]
    source_ranks: Mapping[str, Mapping[str, Mapping[str, int]]]  # {key: {source: {id: rank}}}
    sources: tuple[SourceStats, ...]
    what_if: tuple[WhatIf, ...]
    old: OldComparison | None
    notes: tuple[str, ...]


def _require(ctx: StageContext, name: str) -> Any:
    path = stageio.stage_path(ctx.paths, name)
    if not path.is_file():
        producer = stageio.STAGE_FILES[name].producer
        raise FileNotFoundError(f"{path}: missing; run stage {producer!r} first")
    return stageio.load_stage(ctx.paths, name)


def _optional(ctx: StageContext, name: str) -> Any:
    path = stageio.stage_path(ctx.paths, name)
    return stageio.load_stage(ctx.paths, name) if path.is_file() else None


def _previous_run(ctx: StageContext) -> dict[str, Any] | None:
    """The newest merged run dated before this one."""
    earlier = [
        e for e in ctx.state.run_history if str(e.get("run_date")) < ctx.run_date.isoformat()
    ]
    return dict(max(earlier, key=lambda e: str(e["run_date"]))) if earlier else None


def _next_part(ctx: StageContext, name: str) -> Any:
    """This run's state part, only if a stage wrote it (never the committed fallback)."""
    path = ctx.paths.next_state / state.STATE_FILES[name]
    return jsonio.load(path) if path.is_file() else None


def published(
    ctx: StageContext, now: Mapping[str, Mapping[str, int]], notes: list[str]
) -> dict[str, dict[str, int]]:
    """This run's published {rank key: {id: order}}, the counterpart of last month's
    ``state/published_ranks.json`` (module docstring).

    The ``published_ranks`` part stage "export" wrote to ``build/state/`` when
    every order in it matches ``now`` (else it is left over from another build);
    otherwise ``now`` cut to this run's catalog members (``membership.json``),
    as export would publish it; otherwise ``now``. The fallbacks add a note.
    """
    written = _next_part(ctx, "published_ranks")
    if written is not None:
        pub = {k: {f: int(o) for f, o in v.items()} for k, v in sorted(written.items())}
        if all(now.get(k, {}).get(f) == o for k, v in pub.items() for f, o in v.items()):
            return pub
        notes.append(
            "This month's published ranks: build/state/published_ranks.json does not match "
            'ranks.json (rerun stage "export"), so the catalog members\' orders stand in.'
        )
    membership = _optional(ctx, "membership")
    if membership is None:
        notes.append(
            "This month's published ranks: no membership.json, so every ranked font is "
            "compared with last month's catalog fonts; New and Gone overcount."
        )
        return {k: dict(v) for k, v in now.items()}
    if written is None:
        notes.append(
            'This month\'s published ranks: stage "export" has not run, so the catalog '
            "members' orders stand in."
        )
    members = set(membership.members())
    return {k: {f: o for f, o in v.items() if f in members} for k, v in now.items()}


def _source_ranks(inputs: RankInputs, cfg: RankingConfig) -> dict[str, dict[str, dict[str, int]]]:
    """Each rank key's rank in source per weighted source (overall: its mix keys' sources)."""
    from tff_catalog.engine import crosscheck

    out = {}
    for key in RANK_KEYS:
        if key == "rising":
            continue
        parts = sorted(cfg.ranks.overall.mix) if key == "overall" else [key]
        terms: dict[str, Mapping[str, Term]] = {}
        for part in parts:
            ws = inputs.weights.get(part, {})
            terms.update(
                (s, ts) for s, ts in inputs.terms.get(part, {}).items() if ws.get(s, 0.0) > 0
            )
        out[key] = crosscheck.source_ranks(terms)
    return out


def analyse(ctx: StageContext) -> Analysis:
    """Read the build and compute every flag, statistic and table (module docstring)."""
    cfg = ctx.config.ranking
    placements = _require(ctx, "ranks")
    scores = _require(ctx, "scores")
    universe = _optional(ctx, "universe")
    names = {f: fam.family for f, fam in universe.families.items()} if universe else {}
    now = _orders(placements)
    prev = {k: {f: int(o) for f, o in v.items()} for k, v in ctx.state.published_ranks.items()}
    run = _previous_run(ctx)
    notes: list[str] = []
    overall = now.get("overall", {})
    pub = published(ctx, now, notes)

    flags = _change_flags(ctx, prev, pub, overall, names, notes)
    rbos = rbo_by_key(prev, pub, cfg)
    flags += [
        Flag("rbo", f"{v:.3f} with last month's top list, under {cfg.review.rbo_min}", k)
        for k, v in rbos.items()
        if v < cfg.review.rbo_min
    ]
    stats = source_stats(ctx, run, notes)
    flags += source_flags(stats, names, cfg)
    flags += _health_flags(ctx, scores, now, names, notes)
    inputs = _rank_inputs(ctx, scores, notes)
    flags += _crosscheck_flags(inputs, now, cfg, notes)
    flags += disagreement_flags(now, names, cfg)
    flags += term_flags(_optional(ctx, "terms") or {}, names, overall)
    growth, store_line = growth_flags(ctx, run)
    flags += growth
    rows = what_if(inputs, cfg, phasing_in(ctx)) if inputs is not None else ()
    flags += what_if_flags(rows, names, cfg.display.exact_top)

    info = _info(ctx, run, prev, rbos, stats, scores, cfg, store_line)
    public, everything = _finish(flags, info, notes, now, cfg.display.exact_top)
    old = None
    if universe is not None and not prev:
        old = old_top100(ctx.store.root if ctx.store else None, universe, overall, cfg)
    return Analysis(
        run_date=ctx.run_date,
        names=names,
        prev=prev,
        now=now,
        published=pub,
        flags=public,
        all_flags=everything,
        placements=placements,
        scores=scores,
        confidence=_optional(ctx, "confidence") or {},
        source_ranks=_source_ranks(inputs, cfg) if inputs is not None else {},
        sources=stats,
        what_if=tuple(rows),
        old=old,
        notes=tuple(notes),
    )


def rbo_by_key(
    prev: Mapping[str, Mapping[str, int]], now: Mapping[str, Mapping[str, int]], cfg: RankingConfig
) -> dict[str, float]:
    """Each rank key's RBO of its top ``catalog_size``, last month against this month."""
    depth, p = cfg.membership.catalog_size, cfg.review.rbo_p
    both = sorted(prev.keys() & now.keys(), key=_key_order)
    return {k: rbo(_top(prev[k], depth), _top(now[k], depth), p) for k in both}


def _change_flags(
    ctx: StageContext,
    prev: Mapping[str, Mapping[str, int]],
    pub: Mapping[str, Mapping[str, int]],
    overall: Mapping[str, int],
    names: Mapping[str, str],
    notes: list[str],
) -> list[Flag]:
    """The monthly diff: published ranks, catalog membership (with every ranked
    font's ``overall`` order, so a font leaving the catalog shows where it went)
    and licenses."""
    flags = diff_flags(prev, pub, names, ctx.config.ranking)
    l3 = _optional(ctx, "l3") or {}
    failed = frozenset(f for f, r in l3.items() if r.level == "failed")
    membership = _optional(ctx, "membership")
    if membership is not None:
        flags += catalog_flags(ctx.state.membership, membership, names, overall, failed)
    after = _next_part(ctx, "license_hashes")
    if after is None:
        notes.append('License changes: skipped, stage "verify" wrote no license_hashes this run.')
        return flags
    spdx = {f: v.spdx for f, v in (_optional(ctx, "licenses") or {}).items()}
    return flags + license_flags(ctx.state.license_hashes, after, names, spdx)


def _health_flags(
    ctx: StageContext,
    scores: Mapping[str, SurveyScores],
    now: Mapping[str, Mapping[str, int]],
    names: Mapping[str, str],
    notes: list[str],
) -> list[Flag]:
    """Stale sources, ruler overlaps and guard hits."""
    from tff_catalog import parse, surveys

    cfg = ctx.config.ranking
    stale_state = state.read_part(ctx.paths, "stale")
    try:
        stale = parse.load_stale(ctx.paths)
    except FileNotFoundError, stageio.StageFileError:
        stale = {}
        notes.append('Stale sources: build/stage/stale.json is missing (run stage "parse").')
    flags = stale_flags(stale, stale_state, cfg)
    flags += overlap_flags(scores, cfg, surveys.stale_dropped(cfg, stale_state))
    return flags + guard_flags(scores, now, names, cfg.membership.catalog_size)


def _rank_inputs(
    ctx: StageContext, scores: Mapping[str, SurveyScores], notes: list[str]
) -> RankInputs | None:
    """What stage "rank" ranked (``confidence.load_inputs``), or None with a note."""
    from tff_catalog import confidence

    try:
        return confidence.load_inputs(ctx, scores)
    except (FileNotFoundError, stageio.StageFileError, KeyError) as exc:
        why = type(exc).__name__
        notes.append(f"Cross-checks and what-if: skipped, the rank inputs are incomplete ({why}).")
        return None


def _crosscheck_flags(
    inputs: RankInputs | None,
    now: Mapping[str, Mapping[str, int]],
    cfg: RankingConfig,
    notes: list[str],
) -> list[Flag]:
    from tff_catalog.engine import crosscheck

    if inputs is None:
        return []
    published = {k: v for k, v in now.items() if k in RANK_KEYS}
    try:
        return crosscheck.flags(inputs, published, cfg, top=cfg.membership.catalog_size)
    except ValueError as exc:
        notes.append(f"Cross-checks: skipped ({exc}).")
        return []


def _in_top(flag: Flag, now: Mapping[str, Mapping[str, int]], top: int) -> bool:
    if flag.rank_key is None or flag.family_id is None:
        return True
    return now.get(flag.rank_key, {}).get(flag.family_id, top + 1) <= top


def _info(
    ctx: StageContext,
    run: Mapping[str, Any] | None,
    prev: Mapping[str, Mapping[str, int]],
    rbos: Mapping[str, float],
    stats: Sequence[SourceStats],
    scores: Mapping[str, SurveyScores],
    cfg: RankingConfig,
    store_line: str,
) -> list[str]:
    """The lines at the top of review.md."""
    r, depth = cfg.review, cfg.membership.catalog_size
    if not prev:
        lines = [
            f"Run of {ctx.run_date}. First run: there are no published ranks to compare "
            "with, so there is no diff and no month-on-month flag yet."
        ]
    elif run is not None:
        lines = [
            f"Run of {ctx.run_date}, against the ranks published by the run of {run['run_date']}."
        ]
    else:
        lines = [f"Run of {ctx.run_date}, against the committed published ranks."]
    if rbos:
        values = ", ".join(f"{k} {v:.3f}" for k, v in rbos.items())
        lines.append(
            f"Top-{depth} rank-biased overlap with last month (p {r.rbo_p}, flag under "
            f"{r.rbo_min}): {values}."
        )
    measured = [f"{s.source} {s.spearman:.3f}" for s in stats if s.spearman is not None]
    unmeasured = [s.source for s in stats if s.spearman is None]
    if measured:
        lines.append(
            f"Sources against the last run's snapshots (Spearman over each top {depth}, flag "
            f"under {r.spearman_min}): {', '.join(measured)}."
        )
    if unmeasured:
        lines.append(
            f"Not measured against the last run: {', '.join(unmeasured)} "
            "(reasons in build/review-pack/sources.md)."
        )
    guarded = [
        f"{k} {sum(1 for f in scores[k].fused.values() if f.guard)}"
        for k in RANK_KEYS
        if k in scores and k not in ("overall", "rising")
    ]
    if guarded:
        lines.append(f"Fonts with an outlier-guard hit, all ranks: {', '.join(guarded)}.")
    if store_line:
        lines.append(store_line)
    return lines


def _finish(
    analysis_flags: list[Flag],
    info: list[str],
    notes: list[str],
    now: Mapping[str, Mapping[str, int]],
    top: int,
) -> tuple[tuple[Flag, ...], tuple[Flag, ...]]:
    """(review.md flags, every flag): info lines first; review.md cuts ``CAPPED`` kinds
    to the exact top and says how many more there are."""
    cut = [f for f in analysis_flags if f.kind in CAPPED and not _in_top(f, now, top)]
    public = [f for f in analysis_flags if not (f.kind in CAPPED and not _in_top(f, now, top))]
    lines = list(info)
    if cut:
        counts = ", ".join(
            f"{kind} {sum(1 for f in cut if f.kind == kind)}"
            for kind in sorted({f.kind for f in cut}, key=_kind_order)
        )
        lines.append(
            f"Flags below the top {top} ({counts}) are listed in build/review-pack/anomalies.md."
        )
    lines += notes
    lines.append(
        "The owner's review pack (top-100 lists with tiers and per-source ranks, the what-if "
        "table in full, the old Top 100 comparison) is in build/review-pack/, not committed."
    )
    head = tuple(Flag(INFO, line) for line in lines)
    return (*head, *public), (*head, *analysis_flags)


# --- the review pack ---------------------------------------------------------------------------


def _groups(scores: Mapping[str, SurveyScores], key: str, fid: str) -> str:
    fused = scores[key].fused.get(fid) if key in scores else None
    return ", ".join(fused.groups) if fused is not None and fused.groups else NONE


def _range(conf: Mapping[str, Mapping[str, Confidence]], key: str, fid: str) -> tuple[str, str]:
    c = conf.get(key, {}).get(fid)
    return (NONE, NONE) if c is None else (c.tier, f"{c.range[0]}{NONE}{c.range[1]}")


def _pack_top100(a: Analysis, cfg: RankingConfig) -> str:
    top = cfg.display.exact_top
    lines = [
        f"# Top {top} of each rank, {a.run_date}",
        "",
        f'Tier and 5{NONE}95% range from stage "confidence". Groups: the independence groups of the '
        "font's observed terms. Each source column is the font's rank among the fonts that "
        f"source covers (1 = the most; censored fonts share the bottom rank; {NONE} = not covered).",
        "",
    ]
    for key in RANK_KEYS:
        orders = a.now.get(key, {})
        per_source = a.source_ranks.get(key, {})
        sources = sorted(per_source)
        rows = []
        for f in _top(orders, top):
            tier, span = _range(a.confidence, key, f)
            cells = [per_source[s].get(f, NONE) for s in sources]
            rows.append(
                [orders[f], _name(a.names, f), tier, span, _groups(a.scores, key, f), *cells]
            )
        lines += [f"## {key}", ""]
        if rows:
            header = ["Rank", "Font", "Tier", "Range", "Groups", *sources]
            lines += _table(header, rows, "rllll" + "r" * len(sources))
        else:
            lines.append("No ranked fonts.")
        lines.append("")
    return "\n".join(lines)


def _pack_held(a: Analysis) -> str:
    lines = [
        f"# Held back by the two-group gate, {a.run_date}",
        "",
        "Fonts that scored inside the exact top but have observed terms from fewer than "
        "`engine.top100_min_groups` independence groups (D14). They sit just after the top.",
        "",
    ]
    for key in RANK_KEYS:
        held = sorted(
            (p.order, f, p.band) for f, p in a.placements.get(key, {}).items() if p.gate_held
        )
        lines += [f"## {key}", ""]
        rows = [
            [o, _name(a.names, f), band or NONE, _groups(a.scores, key, f)] for o, f, band in held
        ]
        lines += _table(["Order", "Font", "Band", "Groups"], rows, "rlll") if rows else ["None."]
        lines.append("")
    return "\n".join(lines)


def _pack_tier_c(a: Analysis, cfg: RankingConfig) -> str:
    depth = cfg.membership.catalog_size
    lines = [
        f"# Tier-C fonts in the top {depth}, {a.run_date}",
        "",
        f"Tier C: a 5{NONE}95% range wider than the rank itself (methodology §6).",
        "",
    ]
    for key in RANK_KEYS:
        orders = a.now.get(key, {})
        rows = []
        for f in _top(orders, depth):
            tier, span = _range(a.confidence, key, f)
            if tier == "C":
                rows.append([orders[f], _name(a.names, f), span, _groups(a.scores, key, f)])
        lines += [f"## {key}", ""]
        lines += _table(["Order", "Font", "Range", "Groups"], rows, "rlll") if rows else ["None."]
        lines.append("")
    return "\n".join(lines)


def _pack_disagreements(a: Analysis, cfg: RankingConfig) -> str:
    r = cfg.review
    chosen, project = a.now.get("desktop_chosen", {}), a.now.get("project", {})
    fids = sorted(
        {f.family_id for f in a.all_flags if f.kind == "disagreement" and f.family_id},
        key=lambda f: (min(chosen.get(f, math.inf), project.get(f, math.inf)), f),
    )
    rows = [
        [_name(a.names, f), chosen.get(f, "not ranked"), project.get(f, "not ranked")] for f in fids
    ]
    lines = [
        f"# Most chosen versus project, {a.run_date}",
        "",
        f"Fonts in the top {r.disagreement_top} of one rank but below {r.disagreement_other} "
        "in the other, or not ranked there.",
        "",
    ]
    lines += _table(["Font", "desktop_chosen", "project"], rows, "lrr") if rows else ["None."]
    return "\n".join([*lines, ""])


def _pack_what_if(a: Analysis, cfg: RankingConfig) -> str:
    top = cfg.display.exact_top
    lines = [
        f"# What if: each weight halved and doubled, {a.run_date}",
        "",
        f"Each row multiplies one ranking.toml weight and reranks. RBO: the top "
        f"{cfg.membership.catalog_size} against this run's (p {cfg.review.rbo_p}); In and Out: "
        f"fonts entering and leaving the top {top}. Project sources keep their group's fixed "
        "share (gate M9 (b)), so only the split inside the group changes. A source still "
        "phasing in is weighted by its `phase_in_weight` this run, whatever its key says; "
        "its rows vary that weight.",
        "",
    ]
    rows = []
    for row in a.what_if:
        path = f"{row.knob.path} ({row.note})" if row.note else row.knob.path
        if not row.live:
            rows.append([path, f"{row.factor:g}", NONE, NONE, NONE, NONE, "off this run"])
            continue
        for e in row.effects:
            largest = NONE
            if e.largest is not None:
                f, was, now = e.largest
                largest = f"{_name(a.names, f)} {was} → {now if now is not None else 'not ranked'}"
            rows.append(
                [
                    path,
                    f"{row.factor:g}",
                    e.key,
                    f"{e.rbo:.3f}",
                    _listed([_name(a.names, f) for f in e.entered], 5) or NONE,
                    _listed([_name(a.names, f) for f in e.left], 5) or NONE,
                    largest,
                ]
            )
    header = ["Weight", TIMES, "Rank", "RBO", "In", "Out", "Largest move"]
    lines += _table(header, rows, "lrlrlll") if rows else ["Not computed (see review.md)."]
    return "\n".join([*lines, ""])


def _pack_sources(a: Analysis, cfg: RankingConfig) -> str:
    depth = cfg.membership.catalog_size
    lines = [
        f"# Sources, {a.run_date}",
        "",
        "## Against the last run's snapshots",
        "",
        f"Spearman over the fonts in either top {depth}; RBO of the two top {depth} lists; "
        "coverage: fonts with a value, then → now; jumps: fonts whose share moved more than "
        f"{cfg.review.share_jump_max:g}{TIMES}.",
        "",
    ]
    rows = []
    for s in a.sources:
        jumps = _listed([_name(a.names, j.family_id) for j in s.jumps], 5) or NONE
        rows.append(
            [
                s.source,
                s.previous or NONE,
                s.current or NONE,
                f"{s.counted[0]} → {s.counted[1]}" if any(s.counted) else NONE,
                NONE if s.spearman is None else f"{s.spearman:.3f}",
                NONE if s.rbo is None else f"{s.rbo:.3f}",
                NONE if s.coverage is None else f"{s.coverage:.1%}",
                jumps,
                s.note or NONE,
            ]
        )
    header = ["Source", "Then", "Now", "Fonts", "Spearman", "RBO", "Coverage", "Jumps", "Note"]
    lines += (
        _table(header, rows, "lllrrrrll")
        if rows
        else ["Not measured (first run, or see review.md)."]
    )
    lines += ["", "## Weights and ruler overlaps", ""]
    rows = []
    for key in RANK_KEYS:
        sc = a.scores.get(key)
        if sc is None or key == "rising":
            continue
        for s, w in sorted(sc.weights.items()):
            rows.append([key, s, f"{w:.4f}", sc.overlaps.get(s, 0)])
    lines += (
        _table(["Rank", "Source", "Effective weight", "Overlap"], rows, "llrr")
        if rows
        else ["None."]
    )
    return "\n".join([*lines, ""])


def _pack_old(a: Analysis, cfg: RankingConfig) -> str:
    places = cfg.review.first_run_move_places
    title = f"# The old Top 100 against this run, {a.run_date}"
    old = a.old
    if old is None:
        text = (
            "Not compared: this is not the first run, or the store has no copy of the old "
            f"library's list (`{OLD_TOP100}`)."
        )
        return "\n".join([title, "", text, ""])
    top = cfg.display.exact_top
    rho = NONE if old.spearman is None else f"{old.spearman:.3f}"
    lines = [
        title,
        "",
        f"From `{old.seed}` in the store. Compared on the {len(old.compared)} families that are "
        "eligible in both lists; the new side leaves out the "
        f"{old.left_out} ranked families the old list's installed-fonts filter dropped, so a "
        "font's new position is its place among the fonts the old list could have held "
        "(methodology §9, first run). There is no pass mark.",
        "",
        f"- Spearman (old rank against new position): {rho}.",
        f"- RBO (p {cfg.review.rbo_p}) of the old list against the new top {top}: {old.rbo:.3f}.",
        "",
        f"## Moves of more than {places} places",
        "",
        "Every one needs an explanation before gate R.",
        "",
    ]
    moved = [c for c in old.compared if abs(c[2] - c[1]) > places]
    rows = [[_name(a.names, f), r, p, f"{p - r:+d}", ""] for f, r, p in moved]
    lines += _table(["Font", "Old", "New", "Change", "Why"], rows, "lrrrl") if rows else ["None."]
    lines += ["", f"## In the new top {top}, not in the old list", ""]
    lines.append(_listed([_name(a.names, f) for f in old.new_only], 200) or "None.")
    lines += ["", f"## In the old list, now outside the new top {top}", ""]
    rows = [[_name(a.names, f), r, p] for f, r, p in old.old_out]
    lines += _table(["Font", "Old", "New"], rows, "lrr") if rows else ["None."]
    lines += ["", "## In the old list, not ranked now or not found", ""]
    rows = [[name, r] for name, r in old.missing]
    lines += _table(["Old name", "Old"], rows, "lr") if rows else ["None."]
    if old.repeats:
        lines += ["", "## In the old list twice, under two names of one family now", ""]
        rows = [[name, r, _name(a.names, f)] for name, r, f in old.repeats]
        lines += _table(["Old name", "Old", "Counted as"], rows, "lrl")
    return "\n".join([*lines, ""])


def _pack_readme(a: Analysis) -> str:
    from tff_catalog import reviews

    kinds = defaultdict(int)
    for f in a.all_flags:
        if f.kind != INFO:
            kinds[f.kind] += 1
    options = reviews.catalogue("R")[0].options
    counts = ", ".join(
        f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: _kind_order(kv[0]))
    )
    lines = [
        f"# Review pack, {a.run_date}",
        "",
        "Gate R (Milestone 1 step 16). For the owner only: this folder is gitignored, and it "
        "may name more than the public build/review.md does.",
        "",
        *_table(["File", "What it holds"], ([f"[{n}]({n})", d] for n, d in PACK_FILES.items())),
        "",
        f"Flags: {sum(kinds.values())}" + (f" ({counts})." if counts else "."),
        "",
        "## The question (gate R)",
        "",
        "After reading the lists, one answer:",
        "",
        *(f"- ({reviews.LETTERS[i]}) {o}." for i, o in enumerate(options)),
        "",
        f"Answers are recorded in `data/reviews/{reviews.GATE_DIRS['R']}/<date>.toml` "
        f"(`tff-catalog questions --gate R`, then `tff-catalog rulings apply FILE`). Up to "
        f"{reviews.REVIEW_ROUNDS} rounds.",
        "",
    ]
    return "\n".join(lines)


def write_pack(directory: Path, a: Analysis, cfg: RankingConfig) -> Path:
    """Write every ``PACK_FILES`` file into ``directory``, replacing whatever was there."""
    texts = {
        "top100.md": _pack_top100(a, cfg),
        "held-back.md": _pack_held(a),
        "tier-c.md": _pack_tier_c(a, cfg),
        "old-top100.md": _pack_old(a, cfg),
        "disagreements.md": _pack_disagreements(a, cfg),
        "anomalies.md": render_review(a.prev, a.published, a.all_flags),
        "what-if.md": _pack_what_if(a, cfg),
        "sources.md": _pack_sources(a, cfg),
    }
    texts["README.md"] = _pack_readme(a)
    if directory.exists():
        shutil.rmtree(directory)
    for name in PACK_FILES:
        jsonio.atomic_write(directory / name, texts[name].encode("utf-8"))
    return directory


def review_pack(ctx: StageContext) -> Path:
    """Write ``build/review-pack/`` and return its path."""
    return write_pack(ctx.paths.build / PACK_DIR, analyse(ctx), ctx.config.ranking)


def run(ctx: StageContext) -> None:
    """Stage "review"."""
    a = analyse(ctx)
    text = render_review(a.prev, a.published, a.flags)
    jsonio.atomic_write(ctx.paths.build / REVIEW_FILE, text.encode("utf-8"))
    pack = write_pack(ctx.paths.build / PACK_DIR, a, ctx.config.ranking)
    raised = sum(1 for f in a.all_flags if f.kind not in (INFO, "what_if"))
    ctx.log.info("review: %d flags; wrote %s and %s/", raised, REVIEW_FILE, pack.name)
    for note in a.notes:
        ctx.log.warning("review: %s", note)
