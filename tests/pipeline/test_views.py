"""Overall rank, views and the fonts listed without a rank (milestone-1 step 12).

The step's required tests:

- changing any extra view's weights (Coding, Developers & apps, Rising) leaves
  the overall rank unchanged;
- the abstention leak test: changing a Linux source's count for a font that
  abstains in *most chosen* leaves the overall rank unchanged;
- with no abstentions, *most chosen* and *most installed* agree;
- a font unranked in *most chosen* only because of abstentions is listed there
  as "no evidence of deliberate installs", keeps its other ranks and stays in
  the catalog.

Plus Coding (monospace only, Chocolatey gone) and Rising (3 months of history,
2 sources agreeing, "New" fonts left out). Synthetic data from ``test_surveys``.
"""

import dataclasses
import logging
import math
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.pipeline.test_surveys import (
    ABSTAIN,
    DESKTOP,
    LINUX,
    LINUX_ONLY,
    RANKING,
    RUN_DATE,
    SOURCES,
    Synth,
    canonical,
    inputs_of,
    rank_ctx,
    synth,
    write_stage_files,
)

from tff_catalog import jsonio, membership, stageio, surveys, validate
from tff_catalog.config_model import RankingConfig
from tff_catalog.corrections import Tags, Term
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.store import Store

RISING = RANKING.ranks.rising


def _ranks(cfg: RankingConfig, **changes: Any) -> RankingConfig:
    return dataclasses.replace(cfg, ranks=dataclasses.replace(cfg.ranks, **changes))


def _history(months: int) -> dict[str, dict[str, list[float]]]:
    """Monthly shares: fam-000 grows in Homebrew and npm, fam-001 in Google only."""
    grow = [0.01 * (1.5**m) for m in range(months)]
    flat = [0.01] * months
    return {
        "homebrew": {"fam-000": grow, "fam-001": flat, "fam-002": flat},
        "npm_fontsource": {"fam-000": [2 * x for x in grow], "fam-001": flat},
        "google": {"fam-001": grow, "fam-000": flat},
    }


def _baseline() -> dict[str, dict[str, float]]:
    return {s: dict.fromkeys(("fam-000", "fam-001", "fam-002"), 0.01) for s in RISING.sources}


def _with_rising(s: Synth, cfg: RankingConfig = RANKING) -> surveys.RankInputs:
    rise = surveys.rising_terms(_history(3), cfg, baseline=_baseline())
    return inputs_of(s, cfg, rising=rise)


def _set_value(s: Synth, key: str, source: str, fid: str, value: float) -> None:
    old = s.terms[key][source][fid]
    s.terms[key][source][fid] = dataclasses.replace(old, value=value, state="observed")


# --- view isolation -------------------------------------------------------------------------------

EXTRA_VIEW_CHANGES = {
    "coding weights": {
        "coding": dataclasses.replace(
            RANKING.ranks.coding, weights={"homebrew": 0.1, "arch": 3.0, "nerd": 0.2}
        )
    },
    "dev_apps weights": {
        "dev_apps": dataclasses.replace(
            RANKING.ranks.dev_apps, weights={"npm_fontsource": 1.0, "npm_expo": 0.01}
        )
    },
    "rising settings": {
        "rising": dataclasses.replace(
            RISING, min_sources=1, min_share=0.0, sources=("google", "homebrew")
        )
    },
}


@pytest.mark.parametrize("changes", EXTRA_VIEW_CHANGES.values(), ids=EXTRA_VIEW_CHANGES)
def test_changing_an_extra_view_leaves_overall_unchanged(changes: dict[str, Any]) -> None:
    s = synth()
    before = surveys.views(_with_rising(s), RANKING)
    cfg = _ranks(RANKING, **changes)
    after = surveys.views(_with_rising(s, cfg), cfg)
    (changed,) = changes
    assert canonical(after[changed]) != canonical(before[changed])  # the change took effect
    for key in ("overall", "desktop_chosen", "desktop_installed", "project"):
        assert canonical(after[key]) == canonical(before[key]), key


def test_abstention_leak_linux_counts_of_abstaining_fonts_never_reach_overall() -> None:
    """Methodology §9: a Linux count change for a font that abstains in most chosen."""
    s = synth()
    before = surveys.views(inputs_of(s), RANKING)
    for fid in sorted(ABSTAIN | set(LINUX_ONLY)):
        for source in sorted(LINUX):
            assert fid not in s.terms["desktop_chosen"][source]
            _set_value(s, "desktop_installed", source, fid, 1e7)
    after = surveys.views(inputs_of(s), RANKING)
    assert canonical(after["overall"]) == canonical(before["overall"])
    assert canonical(after["desktop_chosen"]) == canonical(before["desktop_chosen"])
    assert canonical(after["coding"]) == canonical(before["coding"])  # Coding abstains too
    assert canonical(after["desktop_installed"]) != canonical(before["desktop_installed"])


def test_without_abstentions_the_two_desktop_views_agree() -> None:
    s = synth()
    s.terms["desktop_chosen"] = s.terms["desktop_installed"]
    scores = surveys.views(inputs_of(s), RANKING)
    chosen = dataclasses.replace(scores["desktop_chosen"], key="desktop_installed")
    assert canonical(chosen) == canonical(scores["desktop_installed"])


def test_the_desktop_views_differ_only_through_abstaining_terms() -> None:
    s = synth()
    inputs = inputs_of(s)
    chosen, installed = inputs.terms["desktop_chosen"], inputs.terms["desktop_installed"]
    for source in DESKTOP:
        missing = set(installed[source]) - set(chosen[source])
        assert chosen[source] == {f: installed[source][f] for f in chosen[source]}
        assert missing <= ABSTAIN | set(LINUX_ONLY)
        assert not missing or source in LINUX


# --- fonts listed without a rank ------------------------------------------------------------------


def test_abstention_only_fonts_are_listed_in_most_chosen_with_their_reason() -> None:
    s = synth()
    scores = surveys.views(inputs_of(s), RANKING)
    with_project, desktop_only = LINUX_ONLY
    for fid in LINUX_ONLY:
        assert fid in scores["desktop_installed"].placements  # most installed ranks it
        assert fid not in scores["desktop_chosen"].placements
    assert with_project in scores["overall"].placements  # keeps its overall rank
    listed = {key: sorted(s.names) for key in ("desktop_chosen", "overall", "desktop_installed")}
    why = surveys.unranked(s.terms, scores, RANKING, listed)
    assert why["desktop_chosen"][with_project] == "no_deliberate_evidence"
    assert why["desktop_chosen"][desktop_only] == "no_deliberate_evidence"
    assert why["overall"][desktop_only] == "no_deliberate_evidence"
    assert with_project not in why["overall"]
    assert all(r != "no_deliberate_evidence" for r in why["desktop_installed"].values())


def test_abstention_only_fonts_stay_in_the_catalog() -> None:
    """They keep their most-installed rank, whose top 100 is an extra rank of the catalog."""
    s = synth()
    scores = surveys.views(inputs_of(s), RANKING)
    orders = {k: {f: p.order for f, p in v.placements.items()} for k, v in scores.items()}
    m = membership.update(orders, membership.Membership({}, {}), RANKING.membership, RUN_DATE)
    for fid in LINUX_ONLY:
        assert m.catalog[fid].member
    assert set(orders["overall"]) <= set(m.members())  # under 500 fonts: all of overall is in


def test_unranked_reasons_too_new_and_no_evidence() -> None:
    s = synth()
    group = SOURCES["homebrew"].group
    for key in ("desktop_chosen", "desktop_installed"):
        for source in DESKTOP:
            s.terms[key][source].pop("fam-011", None)
            s.terms[key][source].pop("fam-012", None)
        s.terms[key]["homebrew"]["fam-011"] = Term(value=None, state="too_new", group=group)
    scores = surveys.views(inputs_of(s), RANKING)
    listed = {"desktop_installed": ["fam-011", "fam-012", "fam-013"]}
    why = surveys.unranked(s.terms, scores, RANKING, listed)["desktop_installed"]
    assert why == {"fam-011": "too_new", "fam-012": "no_evidence"}


# --- Coding and Developers & apps -----------------------------------------------------------------


def test_coding_ranks_monospace_fonts_with_its_own_weights() -> None:
    s = synth()
    inputs = inputs_of(s)
    coding = surveys.views(inputs, RANKING)["coding"]
    assert set(coding.weights) == set(RANKING.ranks.coding.weights)
    assert "chocolatey" not in coding.weights
    assert coding.placements
    assert set(coding.fused) <= s.monospace
    for source, fam in inputs.terms["coding"].items():
        assert set(fam) <= s.monospace, source


def test_coding_uses_the_most_chosen_abstentions() -> None:
    s = synth()
    fid = sorted(ABSTAIN & s.monospace)[0]
    coding = inputs_of(s).terms["coding"]
    assert fid in s.terms["desktop_installed"]["arch"]
    assert fid not in coding["arch"]
    assert coding["homebrew"] == {
        f: t for f, t in s.terms["desktop_chosen"]["homebrew"].items() if f in s.monospace
    }


def test_a_views_own_terms_win_over_assembled_ones() -> None:
    s = synth()
    s.terms["dev_apps"] = {"npm_expo": s.terms["project"]["npm_expo"]}
    terms = inputs_of(s).terms["dev_apps"]
    assert set(terms) == {"npm_expo"}


def test_dev_apps_ranks_on_code_and_app_sources_only() -> None:
    s = synth()
    dev = surveys.views(inputs_of(s), RANKING)["dev_apps"]
    assert set(dev.weights) == {"npm_fontsource", "ecosystems", "npm_expo", "flutter"}
    assert dev.weights["npm_fontsource"] == pytest.approx(0.15)
    assert dev.placements
    groups = {g for f in dev.fused.values() for g in f.groups}
    assert groups <= {"npm_registry", "flutter"}


# --- Rising ---------------------------------------------------------------------------------------


def test_rising_needs_three_months_of_history() -> None:
    assert RISING.min_history_months == 3
    assert surveys.rising(_history(2), RISING, baseline=_baseline()) == {}
    assert set(surveys.rising(_history(3), RISING, baseline=_baseline())) == {"fam-000"}


def test_a_font_rises_only_when_two_sources_agree() -> None:
    """fam-001 rises in Google alone (Andada Pro's case), so it does not rise."""
    ratios = surveys.rising_ratios(_history(3), RISING, _baseline())
    assert ratios["google"]["fam-001"] > 0
    scores = surveys.rising(_history(3), RISING, baseline=_baseline())
    assert "fam-001" not in scores
    homebrew = ratios["homebrew"]["fam-000"]
    npm = ratios["npm_fontsource"]["fam-000"]
    assert scores["fam-000"] == pytest.approx(min(homebrew, npm))  # the rise both agree on


def test_rising_smooths_the_recent_share_over_three_months() -> None:
    history = {"homebrew": {"f": [0.001, 0.002, 0.003, 0.009]}}
    ratios = surveys.rising_ratios(history, RISING, {"homebrew": {"f": 0.002}})
    assert ratios["homebrew"]["f"] == pytest.approx(math.log(((0.002 + 0.003 + 0.009) / 3) / 0.002))
    # Without a baseline, the history's own mean is the 12-month share.
    assert surveys.rising_ratios(history, RISING)["homebrew"]["f"] == pytest.approx(
        math.log(((0.002 + 0.003 + 0.009) / 3) / (0.015 / 4))
    )


def test_a_flat_font_does_not_rise_on_rounding() -> None:
    """Equal shares give a log-ratio of about 1e-16 (fmean), which is not a rise."""
    flat = [0.003] * 3  # fmean([0.003] * 3) is one ulp above 0.003
    history = {s: {"flat": list(flat)} for s in ("homebrew", "google", "npm_fontsource")}
    base = {s: {"flat": flat[0]} for s in history}
    ratios = surveys.rising_ratios(history, RISING, base)
    assert all(0 < r["flat"] < 1e-12 for r in ratios.values())
    assert surveys.rising(history, RISING, baseline=base) == {}
    terms = surveys.rising_terms(history, RANKING, baseline=base)
    assert surveys.views(inputs_of(synth(), rising=terms), RANKING)["rising"].placements == {}


def _rising_terms_json(s: Synth, recent: dict[str, float]) -> dict[str, dict[str, Term]]:
    """terms.json["rising"] as stage "correct" writes it: abstaining like most chosen,
    each source's values times ``recent[source]`` (a recent window)."""
    out = {}
    for source in RISING.sources:
        key = "desktop_chosen" if SOURCES[source].survey == "desktop" else "project"
        factor = recent.get(source)
        if factor is None or source not in s.terms[key]:
            continue
        out[source] = {
            f: dataclasses.replace(t, value=t.value * factor) if t.value is not None else t
            for f, t in s.terms[key][source].items()
        }
    return out


def test_rising_shares_compare_the_same_families() -> None:
    """Arch abstains in Rising; its 12-month share must too, or every Arch font "rises"."""
    s = synth()
    s.terms["rising"] = _rising_terms_json(s, {"arch": 2.0, "homebrew": 0.1})
    assert set(s.terms["desktop_installed"]["arch"]) > set(s.terms["rising"]["arch"])
    rs = surveys.rising_shares(s.terms, RANKING, frozenset())
    assert rs.no_window == ()
    for source in ("arch", "homebrew"):  # every value scaled alike: no share moves
        assert rs.recent[source] == pytest.approx(rs.baseline[source]), source
    history = {src: {f: [x] * 3 for f, x in rs.recent[src].items()} for src in rs.recent}
    assert surveys.rising(history, RISING, baseline=rs.baseline) == {}


def test_a_source_without_a_recent_window_is_left_out_of_rising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """terms.json["rising"] holding the 12-month terms (no recent window) must not rank
    the 12-month share's drift as a rise: the source is skipped, with a warning."""
    s = synth()
    s.terms["rising"] = _rising_terms_json(s, dict.fromkeys(RISING.sources, 1.0))
    rs = surveys.rising_shares(s.terms, RANKING, frozenset())
    assert rs.no_window == tuple(
        sorted(src for src in RISING.sources if SOURCES[src].enabled and SOURCES[src].publish_raw)
    )
    assert rs.private == ("google",)
    months = [RUN_DATE, date(2026, 11, 3), date(2026, 12, 3)]
    smoothing: dict[str, Any] = {}
    for run_date in months:
        ctx = rank_ctx(tmp_path / run_date.isoformat(), smoothing, run_date)
        write_stage_files(ctx.paths, s)
        with caplog.at_level(logging.WARNING, logger="test.rank"):
            surveys.run(ctx)
        smoothing = jsonio.load(ctx.paths.next_state / "smoothing.json")
        assert stageio.load_stage(ctx.paths, "scores")["rising"].placements == {}
    assert smoothing["rising"] == {}
    assert "no recent window" in caplog.text


def test_rising_keeps_no_shares_of_a_source_published_as_ranks_only(tmp_path: Path) -> None:
    """Ruling T2: Google's shares must never reach the public smoothing state; without a
    private store its history is not kept, so it takes no part in Rising."""
    s = synth()
    s.terms["rising"] = _rising_terms_json(s, {"google": 2.0, "homebrew": 0.5, "arch": 0.5})
    rs = surveys.rising_shares(s.terms, RANKING, frozenset())
    assert rs.private == ("google",)
    assert "google" not in rs.recent
    assert "google" not in rs.baseline
    stale_state = {"rising": {"google": {"fam-000": [0.1, 0.2]}}}
    ctx = rank_ctx(tmp_path, stale_state, RUN_DATE)
    write_stage_files(ctx.paths, s)
    surveys.run(ctx)
    smoothing = jsonio.load(ctx.paths.next_state / "smoothing.json")
    assert set(smoothing["rising"]) == {"arch", "homebrew"}


# --- Google's Rising history in the private store (ruling of 2026-09-26) --------------------------

PRIVATE = surveys.private_sources(RANKING)


def _store_ctx(
    root: Path,
    store: Path,
    smoothing: dict[str, Any],
    run_date: date,
    merged: Sequence[date] = (),
) -> StageContext:
    """``rank_ctx`` with a private store and the merged runs' dates in run_history."""
    ctx = rank_ctx(root, smoothing, run_date)
    history = tuple({"run_date": d.isoformat()} for d in merged)
    return dataclasses.replace(
        ctx, store=Store(store), state=State(smoothing=smoothing, run_history=history)
    )


def _no_private_shares_in_state(ctx: StageContext) -> None:
    """Nothing under build/state/ holds a publish_raw-false source's values (validate)."""
    files = sorted(ctx.paths.next_state.glob("*.json"))
    assert files
    for path in files:
        assert list(validate.private_fields(jsonio.load(path), PRIVATE)) == [], path
    smoothing = jsonio.load(ctx.paths.next_state / "smoothing.json")
    assert not PRIVATE & set(smoothing.get("rising", {}))


def test_google_rising_history_stays_in_the_private_store(tmp_path: Path) -> None:
    """Google's shares go to $TFF_STORE/_state/rising/google/, never to state/, and Rising
    uses them: after three merged months fam-000 rises in Google too."""
    assert "google" in PRIVATE
    assert "google" in RISING.sources
    s = synth()
    s.terms["rising"] = _recent(s)
    store = tmp_path / "store"
    smoothing: dict[str, Any] = {}
    merged: list[date] = []
    for run_date in (RUN_DATE, date(2026, 11, 3), date(2026, 12, 3)):
        ctx = _store_ctx(tmp_path / run_date.isoformat(), store, smoothing, run_date, merged)
        write_stage_files(ctx.paths, s)
        surveys.run(ctx)
        _no_private_shares_in_state(ctx)
        smoothing = jsonio.load(ctx.paths.next_state / "smoothing.json")
        kept = jsonio.load(surveys.private_history_path(store, "google", run_date))
        assert kept["month"] == f"{run_date:%Y-%m}"
        assert kept["source"] == "google"
        merged.append(run_date)
    assert len(kept["history"]["fam-000"]) == RISING.min_history_months
    assert sorted(p.name for p in (store / "_state" / "rising").iterdir()) == ["google"]
    rising = stageio.load_stage(ctx.paths, "scores")["rising"]
    assert "fam-000" in rising.placements
    assert rising.fused["fam-000"].groups == ("google", "homebrew", "npm_registry")


def test_private_history_starts_from_the_newest_merged_run_only(tmp_path: Path) -> None:
    """An unmerged run's file is never a base, and a run in a merged run's month redoes
    that month instead of adding one (as the public history does)."""
    s = synth()
    s.terms["rising"] = _recent(s)
    store = tmp_path / "store"
    unmerged = _store_ctx(tmp_path / "unmerged", store, {}, date(2026, 9, 20))
    write_stage_files(unmerged.paths, s)
    surveys.run(unmerged)
    first = _store_ctx(tmp_path / "first", store, {}, RUN_DATE)  # 2026-09-20 never merged
    write_stage_files(first.paths, s)
    surveys.run(first)
    kept = jsonio.load(surveys.private_history_path(store, "google", RUN_DATE))
    assert all(len(h) == 1 for h in kept["history"].values())
    merged = jsonio.load(first.paths.next_state / "smoothing.json")
    again = _store_ctx(tmp_path / "again", store, merged, date(2026, 10, 20), [RUN_DATE])
    write_stage_files(again.paths, s)
    surveys.run(again)
    redone = jsonio.load(surveys.private_history_path(store, "google", date(2026, 10, 20)))
    assert redone["history"] == kept["history"]


def test_a_merged_runs_private_history_is_never_rewritten(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    s = synth()
    s.terms["rising"] = _recent(s)
    store = tmp_path / "store"
    path = surveys.private_history_path(store, "google", RUN_DATE)
    old = {"schema": 1, "source": "google", "run_date": RUN_DATE.isoformat(), "month": "2026-10"}
    jsonio.dump({**old, "history": {"fam-001": [0.5]}}, path)
    before = path.read_bytes()
    ctx = _store_ctx(tmp_path / "rerun", store, {}, RUN_DATE, [RUN_DATE])
    write_stage_files(ctx.paths, s)
    with caplog.at_level(logging.ERROR, logger="test.rank"):
        surveys.run(ctx)
    assert path.read_bytes() == before
    assert "merged run's history" in caplog.text


def test_rising_needs_a_minimum_share() -> None:
    history = {"homebrew": {"f": [1e-5, 2e-5, 9e-5]}, "google": {"f": [1e-5, 2e-5, 9e-5]}}
    base = {"homebrew": {"f": 1e-5}, "google": {"f": 1e-5}}
    assert RISING.min_share == 0.0002
    assert surveys.rising(history, RISING, baseline=base) == {}


def test_new_fonts_go_to_new_instead_of_rising() -> None:
    terms = surveys.rising_terms(
        _history(3), RANKING, baseline=_baseline(), new=frozenset({"fam-000"})
    )
    assert all("fam-000" not in fam for fam in terms.values())


def test_the_rising_view_matches_rising() -> None:
    s = synth()
    scores = surveys.views(_with_rising(s), RANKING)["rising"]
    expected = surveys.rising(_history(3), RISING, baseline=_baseline())
    assert {f: v.score for f, v in scores.fused.items()} == pytest.approx(expected)
    assert set(scores.placements) == set(expected)
    assert scores.fused["fam-000"].groups == ("homebrew", "npm_registry")
    assert scores.weights == {}


def test_without_rising_terms_the_rising_view_is_empty() -> None:
    scores = surveys.views(inputs_of(synth()), RANKING)["rising"]
    assert scores.placements == {}
    assert scores.fused == {}


def test_history_appends_one_month_and_keeps_three() -> None:
    prev = {"homebrew": {"a": [0.1, 0.2, 0.3], "gone": [0.0, 0.0, 0.2]}, "google": {"b": [0.5]}}
    now = {"homebrew": {"a": 0.4, "new": 0.05}}
    out = surveys.next_history(prev, now, keep=3)
    assert out["homebrew"] == {"a": [0.2, 0.3, 0.4], "gone": [0.0, 0.2, 0.0], "new": [0.05]}
    assert out["google"] == {"b": [0.5]}  # no data this month: unchanged
    assert "gone" not in surveys.next_history(out, now, keep=2)["homebrew"]


def test_shares_are_of_the_sources_observed_total() -> None:
    group = "homebrew"
    terms = {
        "a": Term(value=30.0, state="observed", group=group),
        "b": Term(value=10.0, state="observed", group=group),
        "c": Term(value=5.0, state="censored", group=group),
    }
    assert surveys.shares(terms) == {"a": 0.75, "b": 0.25}


def test_new_is_counted_from_the_earliest_channel_date() -> None:
    first_seen = {
        "old": {"catalog": None, "sources": {"homebrew": "2025-01-01", "npm": "2026-09-01"}},
        "young": {"catalog": None, "sources": {"npm": "2026-08-01"}},
    }
    new = surveys._new_fonts(first_seen, date(2026, 10, 3), RISING.new_days)
    assert new == {"young"}


# --- through the stage ----------------------------------------------------------------------------


def _recent(s: Synth) -> dict[str, dict[str, Term]]:
    """terms.json["rising"]: this month's values; fam-000 jumps in Homebrew and npm."""
    out = {}
    for source in ("homebrew", "npm_fontsource", "google"):
        key = "desktop_installed" if SOURCES[source].survey == "desktop" else "project"
        fam = {f: t for f, t in s.terms[key][source].items() if t.state == "observed"}
        fam["fam-000"] = dataclasses.replace(fam["fam-000"], value=50 * (fam["fam-000"].value or 1))
        out[source] = fam
    return out


def test_the_stage_publishes_rising_from_the_third_month(tmp_path: Path) -> None:
    s = synth()
    s.terms["rising"] = _recent(s)
    months = [("2026-10", RUN_DATE), ("2026-11", date(2026, 11, 3)), ("2026-12", date(2026, 12, 3))]
    smoothing: dict[str, Any] = {}
    placed = []
    for month, run_date in months:
        ctx = rank_ctx(tmp_path / month, smoothing, run_date)
        write_stage_files(ctx.paths, s)
        surveys.run(ctx)
        smoothing = jsonio.load(ctx.paths.next_state / "smoothing.json")
        placed.append(stageio.load_stage(ctx.paths, "scores")["rising"].placements)
    assert placed[0] == {}
    assert placed[1] == {}
    assert "fam-000" in placed[2]
    assert len(smoothing["rising"]["homebrew"]["fam-000"]) == RISING.min_history_months


def test_a_second_run_in_the_same_month_adds_no_month(tmp_path: Path) -> None:
    s = synth()
    s.terms["rising"] = _recent(s)
    first = rank_ctx(tmp_path / "first", {}, RUN_DATE)
    write_stage_files(first.paths, s)
    surveys.run(first)
    merged = jsonio.load(first.paths.next_state / "smoothing.json")
    again = rank_ctx(tmp_path / "again", merged, date(2026, 10, 20))
    write_stage_files(again.paths, s)
    surveys.run(again)
    assert jsonio.load(again.paths.next_state / "smoothing.json") == merged


def test_abstention_leak_through_the_stage(tmp_path: Path) -> None:
    """The leak test on the files: terms.json in, ranks.json's overall bytes unchanged."""
    out = {}
    for name, value in (("before", None), ("after", 1e7)):
        s = synth()
        if value is not None:
            for fid in sorted(ABSTAIN | set(LINUX_ONLY)):
                for source in sorted(LINUX):
                    _set_value(s, "desktop_installed", source, fid, value)
        ctx = rank_ctx(tmp_path / name)
        write_stage_files(ctx.paths, s)
        surveys.run(ctx)
        ranks = stageio.load_stage(ctx.paths, "ranks")
        out[name] = {k: canonical(v) for k, v in ranks.items()}
    for key in ("overall", "desktop_chosen", "project", "coding", "dev_apps"):
        assert out["after"][key] == out["before"][key], key
    assert out["after"]["desktop_installed"] != out["before"]["desktop_installed"]


def test_the_stage_flags_abstaining_fonts_without_a_tag(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = rank_ctx(tmp_path)
    write_stage_files(ctx.paths, synth())
    tagged = Tags(pulled_in_by=(("debian", "synth-desktop"),), flags=("no_deliberate_evidence",))
    stageio.dump_stage(ctx.paths, "tags", {LINUX_ONLY[0]: tagged})
    with caplog.at_level(logging.INFO, logger="test.rank"):
        surveys.run(ctx)
    assert "unranked in desktop_chosen only because of abstentions: 2" in caplog.text
    warning = next(r.message for r in caplog.records if "no preinstalled_on" in r.message)
    assert LINUX_ONLY[1] in warning
    assert LINUX_ONLY[0] not in warning
