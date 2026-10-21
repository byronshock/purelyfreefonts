"""Overall as a stand-in for Projects (owner ruling of 2026-10-01, q12_overall_stand_in), and
membership starting afresh where a method changed (membership_fresh_at_switch, 2026-10-19)."""

from dataclasses import replace
from datetime import date

import pytest
from tests.helpers import ROOT
from tests.pipeline.test_surveys import RANKING, inputs_of, synth

from tff_catalog import config, export, membership, review, surveys
from tff_catalog.config import load_config
from tff_catalog.membership import Membership, MemberState
from tff_catalog.paths import Paths

STAND_IN = replace(
    RANKING,
    ranks=replace(RANKING.ranks, overall=replace(RANKING.ranks.overall, stand_in="project")),
)


def test_overall_repeats_its_stand_in() -> None:
    scores = surveys.views(inputs_of(synth()), STAND_IN)
    overall, project = scores["overall"], scores["project"]
    assert overall.key == "overall"
    assert overall.placements == project.placements
    assert overall.fused == project.fused
    fused = surveys.views(inputs_of(synth()), RANKING)["overall"]
    assert fused.placements != project.placements  # the D12 mix, without a stand-in


def test_export_reads_overall_as_its_stand_in() -> None:
    cfg = replace(load_config(Paths.for_root(ROOT)), ranking=STAND_IN)
    assert export.stand_in(cfg, "overall") == "project"
    assert export.stand_in(cfg, "coding") == "coding"
    assert export.view_sources(cfg, "overall") == export.view_sources(cfg, "project")
    assert export.view_abstains(cfg, "overall") == export.view_abstains(cfg, "project")
    plain = load_config(Paths.for_root(ROOT))
    assert export.stand_in(plain, "overall") == "overall"
    assert "homebrew" in export.view_sources(plain, "overall")


def test_a_stand_in_must_be_a_rank_overall_could_mix() -> None:
    bad = replace(STAND_IN.ranks.overall, stand_in="rising")
    with pytest.raises(config.ConfigError, match="cannot stand in"):
        config.check_ranking(replace(STAND_IN, ranks=replace(STAND_IN.ranks, overall=bad)))
    config.check_ranking(STAND_IN)


def test_a_stand_in_has_no_mix_knobs() -> None:
    assert not [k for k in review.knobs(STAND_IN) if k.kind == "mix"]
    assert [k for k in review.knobs(RANKING) if k.kind == "mix"]
    survey = {k.path: k for k in review.knobs(STAND_IN)}
    assert "overall" not in survey["surveys.desktop.weights.homebrew"].keys
    assert "overall" in survey["surveys.project.weights.google"].keys


def test_membership_starts_afresh_where_the_method_changed() -> None:
    day = date(2026, 9, 26)
    kept = MemberState(member=True, entered=day, runs_outside=1)
    prev = Membership(catalog={"a": kept}, top100={"project": {"a": kept}, "coding": {"b": kept}})
    assert membership.fresh(prev, frozenset()) == prev
    project_only = membership.fresh(prev, frozenset({"project"}))
    assert project_only.catalog == prev.catalog
    assert project_only.top100 == {"coding": {"b": kept}}
    everything = membership.fresh(prev, frozenset({"overall", "project", "coding"}))
    assert everything == Membership(catalog={}, top100={})
