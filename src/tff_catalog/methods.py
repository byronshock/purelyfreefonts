"""Method versions per rank: when a change of rules starts a rank's scores afresh.

Each rank key has its own method version (``tff_catalog.METHOD_VERSIONS``), the date of the
rules it is scored by, since the owner's ruling of 2026-10-01 (q20_previous_score_at_switch):
the measurement model replaces the fused score for some ranks while others keep the engine.
A run records the versions it used in its ``run_history`` entry (``method_versions``), in its
run manifest and in ``catalog.json``'s run block; ``METHOD_VERSION``, the newest, stays the
single ``run.method_version`` both catalogs carry.

A rank whose version differs from the one the last merged run before this one recorded is
new under its method, so its ``previous_score`` bootstraps: it equals ``score``, as before any catalog was
published (score_previous_bootstrap), and a change of method never shows as movement. At the
switch every rank changes (method_versions_all_seven, 2026-10-19). A ``run_history`` entry
from before per-rank versions (the run of 2026-09-26) records none: every rank had
``LEGACY_METHOD_VERSION`` then.

"Before this one" matters for a replay of a merged run's own date: the run replaces that
run's entry (``refresh.next_run_history``), so it is compared with the entry before it, as
``validate --committed`` compares the committed catalog. A replay of the first merged run has
nothing before it and bootstraps every rank, as that run did. With no merged run at all,
nothing has changed, and the bootstrap is the one ``published_scores`` being empty gives.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from tff_catalog import LEGACY_METHOD_VERSION, METHOD_VERSIONS


def recorded(entry: Mapping[str, Any]) -> dict[str, str]:
    """The method versions a ``run_history`` entry records, by rank key."""
    given = entry.get("method_versions")
    if given is None:
        return dict.fromkeys(METHOD_VERSIONS, LEGACY_METHOD_VERSION)
    return {str(k): str(v) for k, v in given.items()}


def changed(
    before: Mapping[str, str], after: Mapping[str, str] = METHOD_VERSIONS
) -> frozenset[str]:
    """The rank keys of ``after`` whose version ``before`` doesn't share (a key it lacks
    included)."""
    return frozenset(key for key, version in after.items() if before.get(key) != version)


def _by_date(history: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(history, key=lambda entry: str(entry["run_date"]))


def changed_since_last_run(history: Iterable[Mapping[str, Any]], run_date: date) -> frozenset[str]:
    """The ranks whose method this code changes from that of the newest merged run dated
    before ``run_date``: the ranks whose ``previous_score`` bootstraps in a run on that date.
    Every rank when no merged run is earlier; none with no merged run at all."""
    runs = _by_date(history)
    if not runs:
        return frozenset()
    earlier = [entry for entry in runs if str(entry["run_date"]) < run_date.isoformat()]
    if not earlier:
        return frozenset(METHOD_VERSIONS)
    return changed(recorded(earlier[-1]))


def changed_by_last_run(history: Iterable[Mapping[str, Any]]) -> frozenset[str] | None:
    """For the committed outputs: the ranks whose method the newest merged run changed from
    the one before it, which that run published with ``previous_score`` equal to ``score``.
    None with at most one merged run, which bootstrapped every rank."""
    runs = _by_date(history)
    if len(runs) < 2:
        return None
    return changed(recorded(runs[-2]), recorded(runs[-1]))
