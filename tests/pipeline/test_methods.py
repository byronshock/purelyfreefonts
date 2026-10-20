"""Method versions per rank (tff_catalog.methods; q20_previous_score_at_switch, 2026-10-01,
and method_versions_all_seven, 2026-10-19)."""

import tff_catalog
from tff_catalog import methods
from tff_catalog.config_model import RANK_KEYS

NOW = dict(tff_catalog.METHOD_VERSIONS)
LEGACY = dict.fromkeys(RANK_KEYS, tff_catalog.LEGACY_METHOD_VERSION)


def test_every_rank_has_a_version_and_the_catalog_records_the_newest():
    assert tuple(tff_catalog.METHOD_VERSIONS) == RANK_KEYS
    assert max(tff_catalog.METHOD_VERSIONS.values()) == tff_catalog.METHOD_VERSION


def test_an_entry_from_before_per_rank_versions_records_the_legacy_method():
    assert methods.recorded({"run_date": "2026-09-26"}) == LEGACY
    assert methods.recorded({"run_date": "2026-10-17", "method_versions": NOW}) == NOW


def test_changed_names_each_rank_with_another_version():
    before = {**NOW, "project": "2026-01-01"}
    del before["rising"]  # a rank the earlier run didn't have
    assert methods.changed(before) == {"project", "rising"}
    assert methods.changed(NOW) == frozenset()


def test_the_bootstrap_follows_the_newest_merged_run():
    assert methods.changed_since_last_run(()) == frozenset()  # published_scores decides
    older = {"run_date": "2026-08-26", "method_versions": {**NOW, "coding": "2026-01-01"}}
    newer = {"run_date": "2026-09-26", "method_versions": NOW}
    assert methods.changed_since_last_run((newer, older)) == frozenset()  # by date, not order
    assert methods.changed_since_last_run((older,)) == {"coding"}


def test_a_clone_knows_which_ranks_the_last_run_restarted():
    assert methods.changed_by_last_run(()) is None
    assert methods.changed_by_last_run(({"run_date": "2026-09-26"},)) is None
    legacy = {"run_date": "2026-09-26"}
    switch = {"run_date": "2026-10-17", "method_versions": {**LEGACY, "project": "2026-10-19"}}
    assert methods.changed_by_last_run((switch, legacy)) == {"project"}
    same = {"run_date": "2026-11-03", "method_versions": switch["method_versions"]}
    assert methods.changed_by_last_run((legacy, switch, same)) == frozenset()
