"""The churn backtest (methodology §9 "First run", milestone-1 step 13), on synthetic records.

Every source here is synthetic (ruling T1 would allow trimmed real pkgstats,
npm and Homebrew rows, but a backtest needs whole series, which are too big
for a fixture; Google must be synthetic under ruling T2).
"""

import logging
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from tests.helpers import ROOT

from tff_catalog import backtest, records, stageio
from tff_catalog.backtest import Window
from tff_catalog.config_model import RankingConfig, from_mapping, load_toml
from tff_catalog.mapping import Mapped
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, SourceKey, attrs
from tff_catalog.stages import StageContext
from tff_catalog.state import State

RANKING: RankingConfig = from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"))
RUN_DATE = date(2026, 10, 3)
N_FONTS = 80


def months_back(n: int, last: str = "2026-09") -> list[str]:
    year, month = map(int, last.split("-"))
    index = year * 12 + month - 1
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(index - n + 1, index + 1)]


def month_span(month: str) -> tuple[date, date]:
    year, mon = map(int, month.split("-"))
    start = date(year, mon, 1)
    end = date(year + mon // 12, mon % 12 + 1, 1) - timedelta(days=1)
    return start, end


def popularity(seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.lognormal(0.0, 1.5, N_FONTS), rng.normal(0.0, 0.03, N_FONTS)


def pkgstats_records(n_months: int = 14, seed: int = 1) -> list[Observation]:
    base, trend = popularity(seed)
    rng = np.random.default_rng(seed + 100)
    out = []
    for t, month in enumerate(months_back(n_months)):
        samples = 20_000 + 1_000 * t
        start, end = month_span(month)
        for i in range(N_FONTS):
            share = min(0.9, 0.002 * base[i] * (1 + trend[i]) ** t * rng.lognormal(0, 0.1))
            out.append(
                Observation(
                    source="pkgstats",
                    series=month,
                    key=SourceKey("arch-pkg", f"ttf-font-{i:02d}"),
                    value=float(round(share * samples)),
                    unit="installs",
                    start=start,
                    end=end,
                    attrs=attrs(samples=samples, month=month),
                )
            )
    return out


def npm_records(n_months: int = 18, seed: int = 2) -> list[Observation]:
    base, trend = popularity(seed)
    rng = np.random.default_rng(seed + 100)
    out = []
    for t, month in enumerate(months_back(n_months)):
        start, end = month_span(month)
        for i in range(N_FONTS):
            for scope, part in (
                ("@fontsource", 0.6),
                ("@fontsource-variable", 0.4),
                ("@expo-google-fonts", 0.3),
            ):
                value = 50_000 * base[i] * part * (1 + trend[i]) ** t * rng.lognormal(0, 0.15)
                out.append(
                    Observation(
                        source="npm",
                        series=month,
                        key=SourceKey("npm", f"{scope}/font-{i:02d}"),
                        value=float(round(value)),
                        unit="downloads",
                        start=start,
                        end=end,
                        attrs=attrs(first_seen="2024-01-02"),
                    )
                )
    return out


def window_records(
    source: str, ns: str, windows: dict[str, int], unit: str, seed: int
) -> list[Observation]:
    """Fixed trailing windows ending the day before the run, the latest weeks a bit noisier."""
    base, trend = popularity(seed)
    rng = np.random.default_rng(seed + 100)
    end = RUN_DATE - timedelta(days=1)
    recent = rng.lognormal(0, 0.2, N_FONTS)
    out = []
    for series, days in windows.items():
        mix = min(1.0, 30 / days)
        for i in range(N_FONTS):
            value = 1_000 * base[i] * days * ((1 - mix) + mix * recent[i] * (1 + trend[i]))
            out.append(
                Observation(
                    source=source,
                    series=series,
                    key=SourceKey(ns, f"font-{i:02d}"),
                    value=float(round(value)),
                    unit=unit,  # type: ignore[arg-type]
                    start=end - timedelta(days=days - 1),
                    end=end,
                    attrs=attrs(first_seen="2020-01-01") if ns == "brew-cask" else (),
                )
            )
    return out


def write_all(paths: Paths) -> None:
    records.write_jsonl(pkgstats_records(), paths.records / "pkgstats.jsonl")
    records.write_jsonl(npm_records(), paths.records / "npm.jsonl")
    brew = window_records(
        "homebrew_analytics", "brew-cask", {"30d": 30, "90d": 90, "365d": 365}, "installs", 3
    )
    records.write_jsonl(brew, paths.records / "homebrew_analytics.jsonl")
    google = window_records(
        "gf_stats", "gf-family", {"7day": 7, "30day": 30, "90day": 90, "year": 365}, "views", 4
    )
    records.write_jsonl(google, paths.records / "gf_stats.jsonl")


def context(paths: Paths) -> StageContext:
    return StageContext(
        paths=paths,
        config=SimpleNamespace(ranking=RANKING),  # type: ignore[arg-type]
        state=State(),
        run_date=RUN_DATE,
        store=None,
        fetcher=None,
        log=logging.getLogger("test.backtest"),
    )


# --- statistics ---------------------------------------------------------------------------


def brute_rbo(a: list[str], b: list[str], p: float) -> float:
    k = min(len(a), len(b))
    overlaps = [len(set(a[:d]) & set(b[:d])) for d in range(1, k + 1)]
    tail = sum(x / d * p**d for d, x in enumerate(overlaps, 1))
    return overlaps[-1] / k * p**k + (1 - p) / p * tail


def test_rbo() -> None:
    assert backtest.rbo(["a", "b", "c"], ["a", "b", "c"]) == pytest.approx(1.0)
    assert backtest.rbo(["a", "b"], ["c", "d"]) == 0.0
    assert backtest.rbo(["x", "y"], ["y", "x"], p=0.5) == pytest.approx(0.5)
    assert backtest.rbo([], []) == 1.0
    rng = np.random.default_rng(5)
    for _ in range(20):
        a = [str(v) for v in rng.permutation(60)[:40]]
        b = [str(v) for v in rng.permutation(60)[:35]]
        assert backtest.rbo(a, b, 0.9) == pytest.approx(brute_rbo(a, b, 0.9))


def test_spearman_and_midranks() -> None:
    assert backtest.midranks([10.0, 20.0, 20.0, 5.0]) == [2.0, 3.5, 3.5, 1.0]
    a = {"a": 3.0, "b": 2.0, "c": 1.0, "d": 0.5}
    assert backtest.spearman(a, a, a) == pytest.approx(1.0)
    flipped = {"a": 0.5, "b": 1.0, "c": 2.0, "d": 3.0}
    assert backtest.spearman(a, flipped, a) == pytest.approx(-1.0)
    assert backtest.spearman(a, {}, ["a", "b", "c"]) is None  # all missing: no variance
    assert backtest.spearman(a, a, ["a", "b"]) is None


def test_share_jumps_and_coverage() -> None:
    a = {"x": 50.0, "y": 50.0, "z": 0.0}
    b = {"x": 80.0, "y": 20.0, "z": 5.0}
    jumps = backtest.share_jumps(a, b, ["x", "y", "z"], min_share=0.0002)
    assert jumps == pytest.approx([80 / 105 / 0.5, 0.5 / (20 / 105)])  # z is new: no ratio
    assert backtest.coverage_change(a, b) == pytest.approx(0.5)
    assert backtest.coverage_change({}, b) is None


def test_suggest_rounds_outwards() -> None:
    got = backtest.suggest([("p", 0.9345)], [("p", 0.96)], [("p", 1.2301)], [("p", 0.0)])
    assert got == (
        ("rbo_min", 0.92),  # 0.9245 rounded down
        ("spearman_min", 0.95),  # exactly 0.95: float noise must not push it to 0.94
        ("share_jump_max", 1.25),  # 1.2401 rounded up
        ("coverage_change_max", 0.01),
    )
    assert backtest.suggest([], [], [], []) == ()


# --- windows ------------------------------------------------------------------------------


def test_latest_months_takes_the_last_consecutive_run() -> None:
    series = ["2025-01", "2025-02", "2025-04", "2025-05", "2025-06", "365d", "2024-12"]
    assert backtest.latest_months(series) == ["2025-04", "2025-05", "2025-06"]
    assert backtest.latest_months(["2025-12", "2026-01"]) == ["2025-12", "2026-01"]


def test_monthly_windows_roll_one_month_at_a_time() -> None:
    months = months_back(14)
    by_series = {m: {"a": float(i + 1), "b": 1.0} for i, m in enumerate(months)}
    windows = backtest.monthly_windows("arch", by_series, 12)
    assert [w.label for w in windows] == [
        f"{months[0]}..{months[11]}",
        f"{months[1]}..{months[12]}",
        f"{months[2]}..{months[13]}",
    ]
    assert windows[0].values == {"a": sum(range(1, 13)), "b": 12.0}
    short = backtest.monthly_windows("arch", {m: by_series[m] for m in months[:5]}, 12)
    assert [w.label for w in short] == [f"{months[0]}..{months[3]}", f"{months[1]}..{months[4]}"]


def test_month_bounds() -> None:
    assert backtest.month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert backtest.month_bounds("2025-12") == (date(2025, 12, 1), date(2025, 12, 31))
    assert backtest.month_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))


def range_last_year(run: date = RUN_DATE) -> list[Observation]:
    """npm ``range/last-year`` summed by month: 365 days to the day before the run, so the
    first and last months are part-months."""
    end = run - timedelta(days=1)
    start = end - timedelta(days=364)
    out = []
    for month in months_back(13, last=f"{end:%Y-%m}"):
        first, last = month_span(month)
        span = (max(first, start), min(last, end))
        days = (span[1] - span[0]).days + 1
        out.extend(
            Observation(
                source="npm",
                series=month,
                key=SourceKey("npm", f"@fontsource/font-{i:02d}"),
                value=float(100 * (i + 1) * days),
                unit="downloads",
                start=span[0],
                end=span[1],
            )
            for i in range(N_FONTS)
        )
    return out


def test_incomplete_months_are_left_out() -> None:
    rows = range_last_year()  # 2025-10-03 .. 2026-10-02
    assert backtest.incomplete_months(rows, RUN_DATE) == ["2025-10", "2026-10"]
    # a month still under way counts as incomplete even if a collector labels it whole
    whole = pkgstats_records(n_months=3, seed=1)
    assert backtest.incomplete_months(whole, RUN_DATE) == []
    assert backtest.incomplete_months(whole, date(2026, 9, 30)) == ["2026-09"]
    # one package that starts mid-month does not make the month incomplete
    late = Observation(
        source="npm",
        series="2026-09",
        key=SourceKey("npm", "@fontsource/new-font"),
        value=5.0,
        unit="downloads",
        start=date(2026, 9, 20),
        end=date(2026, 9, 30),
    )
    assert backtest.incomplete_months([*whole, late], RUN_DATE) == []

    notes: list[str] = []
    found = backtest.source_windows(
        "npm_fontsource",
        RANKING.sources.npm_fontsource,
        [(o.key.key, o) for o in rows],
        False,
        notes,
        RUN_DATE,
    )
    # 11 whole months (2025-11 .. 2026-09): two windows of 10 months, no part-month
    assert [w.label for w in found] == ["2025-11..2026-08", "2025-12..2026-09"]
    assert notes[0] == "`npm_fontsource`: incomplete months left out: 2025-10, 2026-10."
    days = (date(2026, 9, 1) - date(2025, 11, 1)).days  # 2025-11-01 .. 2026-08-31
    assert found[0].values["font-00"] == 100 * days


def test_slid_window_estimate() -> None:
    by_series = {"365d": {"a": 365.0, "b": 365.0}, "30d": {"a": 60.0}, "90d": {"a": 1.0}}
    days = {"365d": 365, "30d": 30, "90d": 90}
    this, nxt = backtest.slid_windows("homebrew", by_series, "365d", days)
    assert this == Window("homebrew", "365d", {"a": 365.0, "b": 365.0})
    assert nxt.label == "365d moved on by 30d"
    # an average month (730 / 365 * 30 = 60) replaces the oldest one
    assert nxt.values == pytest.approx({"a": 335.0 + 60.0, "b": 335.0})
    assert backtest.slid_windows("homebrew", by_series, "365d", {"365d": 365, "90d": 90}) == []


def test_series_days() -> None:
    assert backtest.series_days("365d") == 365
    assert backtest.series_days("30day") == 30
    assert backtest.series_days("year") == 365
    obs = Observation(
        source="x",
        series="weird",
        key=SourceKey("npm", "a"),
        value=1.0,
        unit="downloads",
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
    )
    assert backtest.series_days("weird", [obs]) == 30
    assert backtest.series_days("lifetime", [obs]) is None


def test_npm_scopes_split_the_engine_sources() -> None:
    rows = [(o.key.key, o) for o in npm_records(n_months=2)]
    fontsource = backtest.series_values(rows, RANKING.sources.npm_fontsource, by_family=False)
    expo = backtest.series_values(rows, RANKING.sources.npm_expo, by_family=False)
    month = months_back(2)[0]
    assert set(fontsource[month]) == {f"font-{i:02d}" for i in range(N_FONTS)}
    raw = {o.key.key: o.value for o in npm_records(n_months=2) if o.series == month}
    assert fontsource[month]["font-07"] == pytest.approx(
        raw["@fontsource/font-07"] + raw["@fontsource-variable/font-07"]
    )
    assert expo[month]["font-07"] == raw["@expo-google-fonts/font-07"]


def test_pkgstats_values_become_shares() -> None:
    rows = [(o.key.key, o) for o in pkgstats_records(n_months=1)]
    shares = backtest.series_values(rows, RANKING.sources.arch, by_family=False)
    (month,) = shares
    assert all(0 < v < 1 for v in shares[month].values())


def test_mapped_rows_group_by_family(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    obs = [o for o in npm_records(n_months=1) if o.key.key.endswith(("font-01", "font-02"))]
    mapped = [
        Mapped(
            record=o, family_id=None if o.key.key.endswith("font-02") else "one", relation="direct"
        )
        for o in obs
    ]
    stageio.dump_stage(paths, "mapped", mapped)
    loaded = backtest.load_rows(paths, ["npm", "gf_stats"])
    rows = loaded["npm"]
    assert rows is not None
    assert {item for item, _ in rows} == {"one"}  # ineligible keys are left out
    values = backtest.series_values(rows, RANKING.sources.npm_fontsource, by_family=True)
    assert set(next(iter(values.values()))) == {"one"}
    assert loaded["gf_stats"] == []


def test_records_are_read_per_collector(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    records.write_jsonl(npm_records(n_months=1), paths.records / "npm.jsonl")
    loaded = backtest.load_rows(paths, ["npm", "gf_stats", "npm"])
    assert loaded["gf_stats"] is None
    assert loaded["npm"] is not None
    assert {item for item, _ in loaded["npm"]} >= {
        "@fontsource/font-00",
        "@expo-google-fonts/font-00",
    }


# --- the report ---------------------------------------------------------------------------


def test_backtest_on_synthetic_history(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    write_all(paths)
    report = backtest.backtest(context(paths))
    pairs = [pair for pair, _ in report.rbo]
    by_source = {
        s: [p for p in pairs if p.startswith(f"{s}: ")]
        for s in ("arch", "npm_fontsource", "npm_expo", "homebrew", "google")
    }
    assert {s: len(p) for s, p in by_source.items()} == {
        "arch": 2,  # 14 months: 3 windows of 12
        "npm_fontsource": 6,  # 18 months: 7 windows of 12
        "npm_expo": 6,
        "homebrew": 1,  # 365d and its estimated successor
        "google": 1,
    }
    assert by_source["homebrew"] == ["homebrew: 365d → 365d moved on by 30d"]
    assert by_source["google"] == ["google: year → year moved on by 30day"]
    assert len(report.windows) == 3 + 7 + 7 + 2 + 2
    assert all(0.0 <= v <= 1.0 for _, v in report.rbo)
    assert all(-1.0 <= v <= 1.0 for _, v in report.spearman)
    suggested = dict(report.suggested)
    assert list(suggested) == list(backtest.SUGGESTED_KEYS)
    assert suggested["rbo_min"] <= min(v for _, v in report.rbo)
    assert suggested["spearman_min"] <= min(v for _, v in report.spearman)
    assert suggested["share_jump_max"] >= max(v for _, v in report.jumps) >= 1.0
    assert suggested["coverage_change_max"] >= max(v for _, v in report.coverage)
    assert dict(report.current)["rbo_min"] == RANKING.review.rbo_min
    notes = " ".join(report.notes)
    assert "`github`: no records from `github_releases`" in notes
    assert "`homebrew`: only fixed windows are published" in notes
    assert backtest.backtest(context(paths)) == report  # deterministic


def test_report_shows_statistics_never_values(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    write_all(paths)
    text = backtest.render(backtest.backtest(context(paths)))
    assert text.startswith("# Churn backtest, 2026-10-03\n")
    assert "| `rbo_min` | 0.9 |" in text
    assert "| google: year → year moved on by 30day |" in text
    # ruling T2: never a Google view count; the report's numbers are statistics under 10,
    # besides the top-list length
    numbers = {float(n) for n in re.findall(r"(?<![\w.-])\d+(?:\.\d+)?(?![\w.-])", text)}
    assert max(numbers - {float(RANKING.membership.catalog_size)}) < 10
    google = records.read_jsonl(paths.records / "gf_stats.jsonl")
    assert all(isinstance(r, Observation) and r.value and r.value > 10 for r in google)


def test_render_without_history() -> None:
    report = backtest.BacktestReport(windows=(), rbo=(), spearman=(), suggested=())
    text = backtest.render(report)
    assert "No source had two windows to compare." in text
    assert "| `rbo_min` | n/a | n/a | no windows to measure |" in text


def test_run_writes_docs_backtests(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    write_all(paths)
    ctx = context(paths)
    backtest.run(ctx)
    written = (tmp_path / "docs" / "backtests" / "2026-10-03.md").read_text(encoding="utf-8")
    assert written == backtest.render(backtest.backtest(ctx))
