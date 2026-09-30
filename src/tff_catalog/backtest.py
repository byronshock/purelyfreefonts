"""Backtest of month-to-month churn on historical windows (milestone-1 step 13). Owner: agent P10.

Windows: the pkgstats monthly series, 18 months of npm, Homebrew 30/90/365
days and Google's windows. The report sets the alert thresholds in
``ranking.toml [review]`` and is committed as ``docs/backtests/<date>.md``.
Runs on its own (``tff-catalog backtest``), never inside refresh.

The question is how much one source's ranking moves when its ranking window
moves on by one month, as it does between two monthly runs. For each enabled
engine source (``ranking.toml [sources]``), from the parsed records
(``build/stage/mapped.jsonl`` by family when stage "map" has run, else
``build/stage/records/<collector>.jsonl`` by source key):

- **Monthly series** (series ``YYYY-MM``, as pkgstats and npm's monthly sums
  give): rolling windows of ``months`` (12) months over the latest run of
  consecutive complete months, each compared with the next. With fewer than
  13 months the windows shrink to N-1 months, which churn more, so the
  thresholds err towards fewer flags. pkgstats values become shares (count /
  samples). A month whose observations do not span the whole calendar month,
  or that had not ended before the run date, is left out: npm's
  ``range/last-year`` sums begin and end with part-months, which would read
  as churn.
- **Fixed windows** (Homebrew 30d/90d/365d, Google's 7-day to 1-year views):
  the next month is estimated. The source's ranking window (its ``series``)
  loses an average month and gains the latest 30-day window, scaled to an
  average month: next = long·(1 - 30/D) + short·(Σlong·30/D)/Σshort.

Each pair of windows gives the RBO of the top ``membership.catalog_size`` (p =
``review.rbo_p``), the Spearman correlation over the fonts in either top list,
the largest share jump and the coverage change. Each suggested threshold is
the worst value seen, moved out by ``MARGIN`` and rounded outwards to 2
decimals, so none of the months measured would have raised a flag. The report
holds these statistics only, never a source's values (rulings T2 and T4).
"""

import itertools
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np

from tff_catalog import jsonio, records, stageio
from tff_catalog.records import Observation

if TYPE_CHECKING:
    from pathlib import Path

    from tff_catalog.config_model import RankingConfig, SourceBase
    from tff_catalog.paths import Paths
    from tff_catalog.stages import StageContext

MONTH = re.compile(r"^(\d{4})-(\d{2})$")
NAMED_DAYS = re.compile(r"^(\d+)\s*d(?:ays?)?$")  # "30d", "90day", "7 days"
WORDS_DAYS = {"year": 365, "last-year": 365, "month": 30, "last-month": 30, "week": 7}
SLIDE_DAYS = 30  # one monthly run
MONTHS = 12  # a source's ranking window, unless its config says otherwise (arch.months)
MARGIN = 0.01  # beyond the worst month measured, so a month like it does not flag
SUGGESTED_KEYS = ("rbo_min", "spearman_min", "share_jump_max", "coverage_change_max")


@dataclass(frozen=True, slots=True)
class BacktestReport:
    windows: tuple[str, ...]
    rbo: tuple[tuple[str, float], ...]  # (window pair, rank-biased overlap)
    spearman: tuple[tuple[str, float], ...]
    suggested: tuple[tuple[str, float], ...]  # (ranking.toml [review] key, value)
    run_date: date | None = None
    current: tuple[tuple[str, float], ...] = ()  # (ranking.toml [review] key, value now)
    jumps: tuple[tuple[str, float], ...] = ()  # (window pair, largest share jump)
    coverage: tuple[tuple[str, float], ...] = ()  # (window pair, relative coverage change)
    depth: int = 500  # top-list length for RBO and Spearman
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Window:
    """One ranking window of one engine source: {item: value}, where an item is a family or key."""

    source: str
    label: str
    values: Mapping[str, float]

    @property
    def name(self) -> str:
        return f"{self.source}: {self.label}"


def pair_name(a: Window, b: Window) -> str:
    return f"{a.source}: {a.label} → {b.label}"


# --- statistics ---------------------------------------------------------------------------


def top(values: Mapping[str, float], depth: int) -> list[str]:
    """Items with a positive value, highest first (ties by item), at most ``depth``."""
    ranked = sorted((item for item, v in values.items() if v > 0), key=lambda i: (-values[i], i))
    return ranked[:depth]


def rbo(a: Sequence[str], b: Sequence[str], p: float = 0.98) -> float:
    """Extrapolated rank-biased overlap (Webber, Moffat and Zobel 2010) of two top lists.

    Both lists are cut to the shorter one's length k:
    RBO = (X_k/k)·p^k + ((1-p)/p)·Σ_{d=1..k} (X_d/d)·p^d, where X_d is the
    overlap of the first d items. Identical lists give 1, disjoint ones 0.
    """
    k = min(len(a), len(b))
    if k == 0:
        return 1.0 if len(a) == len(b) else 0.0
    seen_a: set[str] = set()
    seen_b: set[str] = set()
    overlap, total = 0, 0.0
    for d in range(1, k + 1):
        x, y = a[d - 1], b[d - 1]
        if x == y:
            overlap += 1
        else:
            overlap += (x in seen_b) + (y in seen_a)
        seen_a.add(x)
        seen_b.add(y)
        total += overlap / d * p**d
    return overlap / k * p**k + (1 - p) / p * total


def midranks(values: Sequence[float]) -> list[float]:
    """1-based ranks, ties sharing their mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: Mapping[str, float], b: Mapping[str, float], items: Iterable[str]) -> float | None:
    """Spearman's rho over ``items`` (a missing value counts 0); None if undefined."""
    keys = sorted(set(items))
    if len(keys) < 3:
        return None
    ra = np.array(midranks([a.get(k, 0.0) for k in keys]))
    rb = np.array(midranks([b.get(k, 0.0) for k in keys]))
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def share_jumps(
    a: Mapping[str, float], b: Mapping[str, float], items: Iterable[str], min_share: float
) -> list[float]:
    """max(s_b/s_a, s_a/s_b) per item present in both windows with a share >= ``min_share`` in one."""
    total_a = sum(v for v in a.values() if v > 0)
    total_b = sum(v for v in b.values() if v > 0)
    if total_a <= 0 or total_b <= 0:
        return []
    out = []
    for item in sorted(set(items)):
        sa, sb = a.get(item, 0.0) / total_a, b.get(item, 0.0) / total_b
        if sa > 0 and sb > 0 and max(sa, sb) >= min_share:
            out.append(max(sb / sa, sa / sb))
    return out


def coverage_change(a: Mapping[str, float], b: Mapping[str, float]) -> float | None:
    """|n_b - n_a| / n_a, where n counts items with a positive value."""
    na = sum(1 for v in a.values() if v > 0)
    nb = sum(1 for v in b.values() if v > 0)
    return abs(nb - na) / na if na else None


# --- windows ------------------------------------------------------------------------------


type Rows = list[tuple[str, Observation]]  # (item, observation)


def load_rows(paths: Paths, collectors: Iterable[str]) -> dict[str, Rows | None]:
    """Each collector's (item, observation) rows; None when it has no stage file.

    From ``mapped.jsonl`` (read once) the item is the family id, and
    ineligible keys are left out; without it, from each collector's records,
    the item is the source key.
    """
    wanted = sorted(set(collectors))
    if stageio.stage_path(paths, "mapped").is_file():
        out: dict[str, Rows | None] = {c: [] for c in wanted}
        for m in stageio.load_stage(paths, "mapped"):
            rows = out.get(m.record.source)
            if rows is not None and isinstance(m.record, Observation) and m.family_id:
                rows.append((m.family_id, m.record))
        return out
    out = {}
    for c in wanted:
        path = paths.records / f"{c}.jsonl"
        out[c] = (
            [(r.key.key, r) for r in records.read_jsonl(path) if isinstance(r, Observation)]
            if path.is_file()
            else None
        )
    return out


def series_values(
    rows: Iterable[tuple[str, Observation]], src: SourceBase, by_family: bool
) -> dict[str, dict[str, float]]:
    """{series: {item: value}} for one engine source, summed over an item's keys.

    Keys outside the source's ``scopes`` (npm) are left out; without families,
    a scoped key's item is its name without the scope, so a package and its
    variable twin count together. A value with a ``samples`` attr becomes a share.
    """
    scopes = tuple(f"{s}/" for s in getattr(src, "scopes", ()))
    out: dict[str, dict[str, float]] = {}
    for item, obs in rows:
        if obs.value is None or (scopes and not obs.key.key.startswith(scopes)):
            continue
        name = item if by_family or not scopes else item.split("/", 1)[1]
        value = obs.value
        samples = dict(obs.attrs).get("samples")
        if obs.unit != "share" and isinstance(samples, int) and samples > 0:
            value /= samples
        per_item = out.setdefault(obs.series, {})
        per_item[name] = per_item.get(name, 0.0) + value
    return out


def _month_index(series: str) -> int:
    m = MONTH.match(series)
    assert m is not None
    return int(m[1]) * 12 + int(m[2]) - 1


def month_bounds(month: str) -> tuple[date, date]:
    """The first and last day of ``YYYY-MM``."""
    index = _month_index(month)
    year, mon = divmod(index, 12)
    after = date(year + (mon + 1) // 12, (mon + 1) % 12 + 1, 1)
    return date(year, mon + 1, 1), after - timedelta(days=1)


def incomplete_months(rows: Iterable[Observation], run_date: date | None) -> list[str]:
    """The ``YYYY-MM`` series that are not whole months, sorted.

    A month is whole when its observations together span it (the earliest
    ``start`` on or before its first day, the latest ``end`` on or after its
    last) and, given ``run_date``, it ended before that day.
    """
    spans: dict[str, tuple[date, date]] = {}
    for obs in rows:
        if MONTH.match(obs.series):
            lo, hi = spans.get(obs.series, (obs.start, obs.end))
            spans[obs.series] = (min(lo, obs.start), max(hi, obs.end))
    out = []
    for month, (start, end) in spans.items():
        first, last = month_bounds(month)
        if start > first or end < last or (run_date is not None and last >= run_date):
            out.append(month)
    return sorted(out, key=_month_index)


def latest_months(series: Iterable[str]) -> list[str]:
    """The latest run of consecutive ``YYYY-MM`` series, oldest first."""
    months = sorted((s for s in series if MONTH.match(s)), key=_month_index)
    run: list[str] = []
    for month in reversed(months):
        if run and _month_index(run[-1]) - _month_index(month) != 1:
            break
        run.append(month)
    return run[::-1]


def monthly_windows(
    source: str, by_series: Mapping[str, Mapping[str, float]], months: int
) -> list[Window]:
    """Rolling windows of min(``months``, N-1) months over the latest consecutive months."""
    run = latest_months(by_series)
    length = min(months, len(run) - 1)
    if length < 1:
        return []
    windows = []
    for start in range(len(run) - length + 1):
        span = run[start : start + length]
        values: dict[str, float] = {}
        for month in span:
            for item, v in by_series[month].items():
                values[item] = values.get(item, 0.0) + v
        windows.append(Window(source, f"{span[0]}..{span[-1]}", values))
    return windows


def series_days(series: str, rows: Iterable[Observation] = ()) -> int | None:
    """A fixed window's length in days, from its name, else from the observations' dates."""
    m = NAMED_DAYS.match(series)
    if m:
        return int(m[1])
    if series in WORDS_DAYS:
        return WORDS_DAYS[series]
    spans = [(o.end - o.start).days + 1 for o in rows if o.series == series]
    return max(spans) if spans else None


def slid_windows(
    source: str,
    by_series: Mapping[str, Mapping[str, float]],
    main: str,
    days: Mapping[str, int | None],
) -> list[Window]:
    """The main window and its estimated successor one month on (module docstring), or []."""
    long_days = days.get(main)
    if main not in by_series or long_days is None or long_days <= SLIDE_DAYS:
        return []
    shorts = sorted(
        (abs(d - SLIDE_DAYS), s)
        for s, d in days.items()
        if s != main and d is not None and d < long_days and s in by_series
    )
    if not shorts or shorts[0][0] > 3:
        return []
    short = shorts[0][1]
    long_values, short_values = by_series[main], by_series[short]
    kept = 1 - SLIDE_DAYS / long_days
    long_total = sum(v for v in long_values.values() if v > 0)
    short_total = sum(v for v in short_values.values() if v > 0)
    if long_total <= 0 or short_total <= 0:
        return []
    scale = long_total * SLIDE_DAYS / long_days / short_total
    items = set(long_values) | set(short_values)
    nxt = {i: long_values.get(i, 0.0) * kept + short_values.get(i, 0.0) * scale for i in items}
    return [
        Window(source, main, dict(long_values)),
        Window(source, f"{main} moved on by {short}", nxt),
    ]


# --- the backtest -------------------------------------------------------------------------


def backtest(ctx: StageContext) -> BacktestReport:
    """Compute churn between consecutive historical windows."""
    cfg = ctx.config.ranking
    depth = cfg.membership.catalog_size
    by_family = stageio.stage_path(ctx.paths, "mapped").is_file()
    windows: list[str] = []
    rbos, rhos, jumps, coverage = [], [], [], []
    notes: list[str] = []
    enabled = {name: src for name, src in sorted(cfg.sources.all().items()) if src.enabled}
    loaded = load_rows(ctx.paths, (src.collector for src in enabled.values()))
    for name, src in enabled.items():
        rows = loaded[src.collector]
        if rows is None:
            notes.append(f"`{name}`: no records from `{src.collector}` (run parse first).")
            continue
        found = source_windows(name, src, rows, by_family, notes, ctx.run_date)
        windows.extend(w.name for w in found)
        for a, b in itertools.pairwise(found):
            pair = pair_name(a, b)
            ta, tb = top(a.values, depth), top(b.values, depth)
            rbos.append((pair, rbo(ta, tb, cfg.review.rbo_p)))
            rho = spearman(a.values, b.values, set(ta) | set(tb))
            if rho is not None:
                rhos.append((pair, rho))
            ratios = share_jumps(a.values, b.values, set(ta) | set(tb), cfg.ranks.rising.min_share)
            if ratios:
                jumps.append((pair, max(ratios)))
            change = coverage_change(a.values, b.values)
            if change is not None:
                coverage.append((pair, change))
    ctx.log.info("backtest: %d windows, %d pairs", len(windows), len(rbos))
    return BacktestReport(
        windows=tuple(windows),
        rbo=tuple(rbos),
        spearman=tuple(rhos),
        suggested=suggest(rbos, rhos, jumps, coverage),
        run_date=ctx.run_date,
        current=_current(cfg),
        jumps=tuple(jumps),
        coverage=tuple(coverage),
        depth=depth,
        notes=tuple(notes),
    )


def source_windows(
    name: str,
    src: SourceBase,
    rows: Rows,
    by_family: bool,
    notes: list[str],
    run_date: date | None = None,
) -> list[Window]:
    """One engine source's windows in time order: monthly if it has 2+ whole months, else slid.

    Months that are not whole (``incomplete_months``) are left out, with a note.
    """
    by_series = series_values(rows, src, by_family)
    partial = [m for m in incomplete_months((o for _, o in rows), run_date) if m in by_series]
    if partial:
        notes.append(f"`{name}`: incomplete months left out: {', '.join(partial)}.")
        by_series = {s: v for s, v in by_series.items() if s not in partial}
    months = latest_months(by_series)
    if len(months) >= 2:
        length = getattr(src, "months", MONTHS)
        if len(months) - 1 < length:
            notes.append(
                f"`{name}`: {len(months)} consecutive months, so its windows are "
                f"{len(months) - 1} months long instead of {length}."
            )
        return monthly_windows(name, by_series, length)
    obs = [o for _, o in rows]
    days = {s: series_days(s, obs) for s in sorted(by_series)}
    found = slid_windows(name, by_series, src.series, days)
    if found:
        notes.append(
            f"`{name}`: only fixed windows are published, so the next month is estimated "
            f"({found[1].label}; module docstring of `tff_catalog.backtest`)."
        )
    else:
        notes.append(f"`{name}`: no monthly series and no 30-day window; not measured.")
    return found


def _floor(x: float, places: int) -> float:
    scale = 10**places
    return math.floor(round(x * scale, 6)) / scale


def _ceil(x: float, places: int) -> float:
    scale = 10**places
    return math.ceil(round(x * scale, 6)) / scale


def suggest(
    rbos: Sequence[tuple[str, float]],
    rhos: Sequence[tuple[str, float]],
    jumps: Sequence[tuple[str, float]],
    coverage: Sequence[tuple[str, float]],
) -> tuple[tuple[str, float], ...]:
    """The worst value of each statistic, moved out by ``MARGIN`` and rounded outwards."""
    out = []
    if rbos:
        out.append(("rbo_min", _floor(min(v for _, v in rbos) - MARGIN, 2)))
    if rhos:
        out.append(("spearman_min", _floor(min(v for _, v in rhos) - MARGIN, 2)))
    if jumps:
        out.append(("share_jump_max", _ceil(max(v for _, v in jumps) + MARGIN, 2)))
    if coverage:
        out.append(("coverage_change_max", _ceil(max(v for _, v in coverage) + MARGIN, 2)))
    return tuple(out)


def _current(cfg: RankingConfig) -> tuple[tuple[str, float], ...]:
    review = cfg.review
    return (("rbo_p", review.rbo_p), *((k, getattr(review, k)) for k in SUGGESTED_KEYS))


# --- the report ---------------------------------------------------------------------------

_BASIS = {
    "rbo_min": "the lowest RBO below, less 0.01, rounded down",
    "spearman_min": "the lowest Spearman below, less 0.01, rounded down",
    "share_jump_max": "the largest share jump below, plus 0.01, rounded up",
    "coverage_change_max": "the largest coverage change below, plus 0.01, rounded up",
}


def _fmt(value: float | None, places: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g}"


def render(report: BacktestReport) -> str:
    """The Markdown report."""
    current, suggested = dict(report.current), dict(report.suggested)
    when = report.run_date.isoformat() if report.run_date else "undated"
    p = current.get("rbo_p", 0.98)
    lines = [
        f"# Churn backtest, {when}",
        "",
        "How much does each source's ranking move when its ranking window moves on by one "
        "month, as it does between two monthly runs? This report measures that on the history "
        "the sources already publish, so that the alert thresholds in `config/ranking.toml` "
        "`[review]` sit just outside normal churn (methodology §9). It shows rank statistics "
        "only, never a source's values.",
        "",
        "## Suggested thresholds",
        "",
        "| `[review]` key | Now | Suggested | From |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| `{key}` | {_number(current.get(key))} | {_number(suggested.get(key))} "
        f"| {_BASIS[key] if key in suggested else 'no windows to measure'} |"
        for key in SUGGESTED_KEYS
    )
    lines += [
        "",
        "None of the months measured here would have raised a flag at the suggested values. "
        "The RBO flag compares fused ranks, which move less than any one source, so a value "
        "set from single sources errs towards fewer flags.",
        "",
        "## Windows compared",
        "",
        f"RBO: the top {report.depth}, p = {p:g}. Spearman: the fonts in either top "
        f"{report.depth}. Share jump: the largest ratio of a font's two shares of the "
        "source, larger over smaller, among fonts in either top list that hold at least the "
        "Rising minimum share in one window. Coverage change: the relative change in how many "
        "fonts the source counts.",
        "",
    ]
    if report.rbo:
        rhos, jumps, cover = dict(report.spearman), dict(report.jumps), dict(report.coverage)
        lines += [
            "| Windows | RBO | Spearman | Share jump | Coverage change |",
            "|---|---|---|---|---|",
        ]
        lines.extend(
            f"| {pair} | {_fmt(value)} | {_fmt(rhos.get(pair))} "
            f"| {_fmt(jumps.get(pair), 2)} | {_fmt(cover.get(pair))} |"
            for pair, value in report.rbo
        )
    else:
        lines.append("No source had two windows to compare.")
    if report.notes:
        lines += ["", "## Notes", ""]
        lines += [f"- {note}" for note in report.notes]
    return "\n".join(lines) + "\n"


def report_path(paths: Paths, run_date: date) -> Path:
    """``docs/backtests/<run_date>.md``."""
    return paths.root / "docs" / "backtests" / f"{run_date.isoformat()}.md"


def run(ctx: StageContext) -> None:
    """``tff-catalog backtest``: write ``docs/backtests/<run_date>.md``."""
    path = report_path(ctx.paths, ctx.run_date)
    jsonio.atomic_write(path, render(backtest(ctx)).encode("utf-8"))
    ctx.log.info("backtest: wrote %s", path)
