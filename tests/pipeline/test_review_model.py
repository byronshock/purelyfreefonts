"""Stage "review" with the model views on, as config/ranking.toml has them: Projects and
Developers & apps on the measurement model, overall standing in for Projects (owner rulings
of 2026-10-01, q1_model_views, q12_overall_stand_in, q13_catalog_membership,
q2_guard_retired and q5_group_shares_retired).

review.md and the pack describe the method as it is: ruler overlaps, guard hits and the
what-if table cover the engine's ranks only (methodology §9), text that named an overall
order names the stand-in, and the pack shows the stand-in's lists once. The model's fit is
replaced by synthetic entries (``fake_models``): these tests read what review writes, not
what the model fits.
"""

import dataclasses
import logging
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from tests.helpers import ROOT
from tests.pipeline.test_review import (  # noqa: F401 (replay_collector is a fixture)
    RUN,
    SMALL,
    pack_bytes,
    ranked_build,
    replay_collector,
    small,
)

from tff_catalog import review, stageio
from tff_catalog.config_model import RankingConfig, from_mapping, load_toml
from tff_catalog.corrections import Term
from tff_catalog.modelviews import FitInfo, ModelEntry
from tff_catalog.stages import StageContext

pytestmark = pytest.mark.model_config

MODEL: RankingConfig = small(
    from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"))
)
ENGINE = ("desktop_chosen", "desktop_installed", "coding")  # the engine's ranks with weights


def model_rows(terms: Mapping[str, Mapping[str, Term]], sources: Iterable[str]) -> dict[str, Any]:
    """One synthetic ``ModelEntry`` per font with a term from ``sources``: its score the
    mean log value of its terms, every source an equal share."""
    by_font: dict[str, dict[str, Term]] = defaultdict(dict)
    for s in sources:
        for fid, term in terms.get(s, {}).items():
            by_font[fid][s] = term
    out = {}
    for fid, ts in sorted(by_font.items()):
        score = float(np.mean([math.log(t.value or 1.0) for t in ts.values()]))
        out[fid] = ModelEntry(
            score=score,
            sd=0.3,
            low=score - 0.5,
            high=score + 0.5,
            terms=len(ts),
            groups=tuple(sorted({t.group for t in ts.values()})),
            z=dict.fromkeys(ts, score),
            info=dict.fromkeys(ts, 1 / len(ts)),
            pit=(),
        )
    return out


def fake_models(ctx: StageContext) -> tuple[dict[str, Any], dict[str, Any]]:
    """In place of ``modelviews.run``: synthetic entries for each model view, from the
    project terms of the view's weighted sources."""
    cfg = ctx.config.ranking
    terms = stageio.load_stage(ctx.paths, "terms")["project"]
    weights = {"project": cfg.surveys.project.weights, "dev_apps": cfg.ranks.dev_apps.weights}
    models = {
        key: model_rows(terms, [s for s, w in weights[key].items() if w > 0])
        for key in cfg.model.views
    }
    fits = {key: FitInfo(RUN, "a" * 64, len(rows), (), (), ()) for key, rows in models.items()}
    return models, fits


@pytest.fixture
def model_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageContext:
    """``ranked_build`` on the model config, with ``fake_models`` as the model's fit, and one
    family with no license found that would reach the stand-in's top 700."""
    from tff_catalog import modelviews, nolicense

    monkeypatch.setattr(modelviews, "run", fake_models)
    ctx = ranked_build(tmp_path, cfg=MODEL)
    rows = (
        nolicense.NoLicense(
            "fam-91", {"overall": 120, "desktop_chosen": 60, "project": 120, "coding": 7}
        ),
    )
    monkeypatch.setattr(nolicense, "what_if", lambda ctx: rows)
    return ctx


def section(text: str, heading: str) -> str:
    return text.split(f"## {heading}\n")[1].split("\n## ")[0]


def test_the_config_has_the_model_views_and_the_stand_in() -> None:
    assert MODEL.model.views == ("project", "dev_apps")
    assert MODEL.ranks.overall.stand_in == "project"
    assert review.model_keys(MODEL) == {"overall", "project", "dev_apps"}
    assert review.model_keys(SMALL) == frozenset()  # the engine-only default of the tests
    assert review.overall_name(MODEL) == "project"
    assert review.overall_name(SMALL) == "overall"


@pytest.mark.usefixtures("replay_collector")
def test_ruler_overlaps_and_guard_hits_cover_the_engine_s_ranks_only(
    model_build: StageContext,
) -> None:
    """A model view has no ruler and no guard (q1, q2): its sources, which have no overlap
    with a ruler, are not reported as switched off (methodology §9)."""
    ctx = model_build
    scores = stageio.load_stage(ctx.paths, "scores")
    assert scores["project"].overlaps == {}  # what used to read as "0 fonts in common"
    review.run(ctx)
    text = (ctx.paths.build / review.REVIEW_FILE).read_text()
    overlaps = section(text, "Ruler overlaps")
    assert "In the engine's ranks, sources with too few fonts" in overlaps
    for key in ("overall", "project", "dev_apps"):
        assert f"| {key} |" not in overlaps
    flagged = {f.rank_key for f in review.analyse(ctx).all_flags if f.kind == "ruler_overlap"}
    assert flagged == {"coding"}  # the engine's ranks are still checked
    [guard] = [line for line in text.splitlines() if "outlier-guard hit" in line]
    assert guard.startswith("- Fonts with an outlier-guard hit, the engine's ranks: ")
    assert [part.split()[0] for part in guard.split(": ", 1)[1].split(", ")] == list(ENGINE)


def test_overlap_flags_skip_the_model_views() -> None:
    scores = {
        "desktop_chosen": SimpleNamespace(overlaps={"homebrew": 3, "arch": 12, "debian": 40}),
        "project": SimpleNamespace(overlaps={}),
        "dev_apps": SimpleNamespace(overlaps={}),
    }
    flags = review.overlap_flags(scores, MODEL)  # type: ignore[arg-type]
    assert [(f.rank_key, f.message.split(":")[0]) for f in flags] == [("desktop_chosen", "`arch`")]


def test_the_rank_log_gives_no_overlap_or_weight_for_a_model_view(
    model_build: StageContext, caplog: pytest.LogCaptureFixture
) -> None:
    """q1_model_views, q5_group_shares_retired: Projects on the model has no ruler and no
    hand weights, so the rank log's overlap and weight lines cover most chosen only; with
    the model views off, Projects gets its lines back."""
    from tff_catalog import surveys

    def logged(ctx: StageContext) -> set[str]:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=ctx.log.name):
            surveys.run(ctx)
        return {r.getMessage().split()[0] for r in caplog.records if ": overlap " in r.getMessage()}

    assert logged(model_build) == {"desktop_chosen"}
    engine = dataclasses.replace(MODEL, model=dataclasses.replace(MODEL.model, views=()))
    ctx = dataclasses.replace(model_build, config=SimpleNamespace(ranking=engine))
    assert logged(ctx) == {"desktop_chosen", "project"}


def test_the_what_if_table_varies_only_the_engine_s_weights() -> None:
    """q5_group_shares_retired: the project survey's weights, the group shares and the
    dev_apps weights score no rank, so they have no knob; every knob left moves an engine
    rank."""
    paths = {k.path: k for k in review.knobs(MODEL)}
    assert "surveys.desktop.weights.homebrew" in paths
    assert "ranks.coding.weights.homebrew" in paths
    assert not [p for p in paths if p.startswith("surveys.project.")]
    assert not [p for p in paths if p.startswith("project_group_shares.")]
    assert not [p for p in paths if p.startswith("ranks.dev_apps.")]
    assert not [p for p in paths if p.startswith("ranks.overall.")]  # a stand-in has no mix
    assert all(set(k.keys) <= set(ENGINE) for k in paths.values())


@pytest.mark.usefixtures("replay_collector")
def test_the_what_if_pack_names_the_engine_s_rules_only(model_build: StageContext) -> None:
    ctx = model_build
    review.run(ctx)
    pack = pack_bytes(ctx.paths)
    what_if = pack["what-if.md"].decode()
    head = what_if.split("\n| Weight |")[0]
    assert "# What if: each engine weight halved and doubled" in head
    assert "The model's ranks (project, dev_apps; overall repeats project) have no rows" in head
    for retired in ("gate M9 (b)", "phase_in_weight", "phasing in"):
        assert retired not in what_if
    rows = [line.split(" | ") for line in what_if.splitlines() if line.startswith("| ranks.")]
    rows += [line.split(" | ") for line in what_if.splitlines() if line.startswith("| surveys.")]
    assert rows
    assert {row[2] for row in rows} <= set(ENGINE)
    for path in ("surveys.project.", "project_group_shares.", "ranks.dev_apps."):
        assert path not in what_if
        assert path not in pack["anomalies.md"].decode()
    readme = pack["README.md"].decode()
    assert "Weights in ranking.toml move the engine's ranks only (what-if.md)." in readme


@pytest.mark.usefixtures("replay_collector")
def test_the_review_names_the_stand_in_where_it_named_overall(model_build: StageContext) -> None:
    """q13_catalog_membership: the license research reaches the Project top 700, and the
    catalog is the Project top 500, so the text names project, not overall."""
    ctx = model_build
    review.run(ctx)
    text = (ctx.paths.build / review.REVIEW_FILE).read_text()
    assert "would reach the project top 700" in text
    assert "overall top 700" not in text
    assert "| catalog | fam-99 leaves the catalog (not ranked in project) |" in text
    assert "joins the catalog (overall" not in text
    no_license = pack_bytes(ctx.paths)["no-license.md"].decode()
    assert "Claude researches every one at project 700 or better" in no_license
    assert "| Research | Font | Id | project | desktop_chosen | coding |" in no_license
    assert "| yes | fam-91 | fam-91 | 120 | 60 | 7 |" in no_license


@pytest.mark.usefixtures("replay_collector")
def test_the_pack_shows_the_stand_in_s_lists_once(model_build: StageContext) -> None:
    """q12_overall_stand_in: overall repeats project, so its sections point to project's
    instead of copying them, and the overall top-100 table has no desktop columns."""
    ctx = model_build
    review.run(ctx)
    pack = pack_bytes(ctx.paths)
    line = "Repeats project (owner ruling of 2026-10-01, q12_overall_stand_in): see project below."
    for name in ("top100.md", "held-back.md", "tier-c.md"):
        text = pack[name].decode()
        assert section(text, "overall").strip() == line, name
        assert "## project\n" in text
    top100 = pack["top100.md"].decode()
    header = section(top100, "project").split("\n")[1]
    assert header.startswith("| Rank | Font | Tier | Range | Groups | ")
    columns = header.strip("| ").split(" | ")[5:]
    assert columns
    assert set(columns) <= set(MODEL.surveys.project.weights)  # no desktop column
    a = review.analyse(ctx)
    assert a.source_ranks["overall"] == a.source_ranks["project"]
    checks = review._pack_checks(dataclasses.replace(a, defaulted=("fam-00",)), MODEL)
    assert "| Project | Font | Id |" in checks


@pytest.mark.usefixtures("replay_collector")
def test_a_family_to_watch_has_no_overall_row_under_the_stand_in(
    model_build: StageContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """q12_overall_stand_in: checks.md gives a family to watch one row for project, not a
    second for overall repeating it; with no stand-in, overall keeps its row."""
    a = review.analyse(model_build)
    fid = min(a.now["project"], key=a.now["project"].__getitem__)
    monkeypatch.setattr(review, "GATE_R_WATCH", {fid: "a synthetic family to watch"})

    def rows(cfg: RankingConfig) -> list[str]:
        table = review._pack_checks(a, cfg).split(f"(`{fid}`)\n")[1]
        cells = [line.split(" | ")[0].lstrip("| ") for line in table.splitlines()]
        return [c for c in cells if c in review.RANK_KEYS]

    assert "project" in rows(MODEL)
    assert "overall" not in rows(MODEL)
    overall = dataclasses.replace(MODEL.ranks.overall, stand_in="")
    mixed = dataclasses.replace(MODEL, ranks=dataclasses.replace(MODEL.ranks, overall=overall))
    assert {"overall", "project"} <= set(rows(mixed))


@pytest.mark.usefixtures("replay_collector")
def test_a_model_only_source_s_term_note_halves_no_weight(model_build: StageContext) -> None:
    """Methodology §8: no rank's score uses the Almanac's parent_merge_factor under the
    model, so its term flag no longer says the weight is halved; an engine source keeps its
    note."""
    assert "almanac" in review.model_sources(MODEL)
    assert "npm_fontsource" not in review.model_sources(MODEL)  # Coding weighs it
    assert "homebrew" not in review.model_sources(MODEL)
    assert review.model_sources(SMALL) == frozenset()
    ctx = model_build
    review.run(ctx)
    text = (ctx.paths.build / review.REVIEW_FILE).read_text()
    assert "`almanac` parent_merge (width cuts folded into the parent): 1 font" in text
    assert "weight halved" not in text
