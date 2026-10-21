"""Shared test helpers (design-m1 §2.3 and §5).

- ``treehash``: a stable sha256 of a directory tree, for "same output twice" checks.
- ``mockhttp``: replays recorded HTTP responses through ``httpx.MockTransport``,
  and serves git fixtures from local repositories.
- ``synth``: synthetic records, a synthetic snapshot store and state, and a stub
  pipeline, all deterministic by seed.
- ``regen``: loads a collector's fixture snapshot, parses it, and rewrites its
  golden ``expected.jsonl`` (``uv run python -m tests.helpers.regen <collector>``).
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]  # the repository (or worktree) root
FIXTURES = ROOT / "tests" / "fixtures"


def engine_only(ranking: Any) -> Any:
    """``ranking`` (a ``RankingConfig``) with every rank on the engine: no model view, and
    overall the fused mix rather than a stand-in. The tests' default (``tests/conftest.py``):
    the engine's tests keep testing the engine, which desktop, Coding and Rising still run
    on; the model views have tests of their own, marked ``model_config``, on the config as
    it is (docs/milestone-method.md step 10)."""
    overall = replace(ranking.ranks.overall, stand_in="")
    return replace(
        ranking,
        model=replace(ranking.model, views=()),
        ranks=replace(ranking.ranks, overall=overall),
    )
