"""The methodology's worked example (§4) as a fixture: guard off and on (ruling M1, with
the median basis of the owner's ruling of 2026-09-26)."""

import tomllib
from typing import Any

import pytest
from tests.helpers import FIXTURES, ROOT

from tff_catalog.engine.fuse import Fused, fuse, guard_factors
from tff_catalog.engine.order import Scored, sort_key

EXAMPLE: dict[str, Any] = tomllib.loads(
    (FIXTURES / "engine" / "worked_example.toml").read_text(encoding="utf-8")
)
FONTS = {font["name"]: font for font in EXAMPLE["fonts"]}
GUARD = EXAMPLE["guard"]


def _fuse(font: dict[str, Any], *, guarded: bool, basis: str | None = None) -> Fused:
    z = font["z"]
    factors = (
        guard_factors(z, GUARD["gap"], GUARD["min_terms"], GUARD["factor"], basis or GUARD["basis"])
        if guarded
        else None
    )
    return fuse(
        z,
        EXAMPLE["weights"],
        EXAMPLE["w_total"],
        EXAMPLE["kappa"],
        EXAMPLE["mu0"],
        guard=factors,
        observed=frozenset(z),
    )


def test_fixture_matches_the_ranking_config():
    """The fixture's κ and guard are the live config/ranking.toml values."""
    config = tomllib.loads((ROOT / "config" / "ranking.toml").read_text("utf-8"))
    engine = config["engine"]
    assert engine["kappa"] == EXAMPLE["kappa"]
    assert engine["mu0"] == EXAMPLE["mu0"]
    assert engine["guard"] == GUARD
    assert sum(EXAMPLE["weights"].values()) == EXAMPLE["w_total"]
    assert EXAMPLE["kappa"] * EXAMPLE["w_total"] == pytest.approx(0.4)


@pytest.mark.parametrize("name", list(FONTS))
def test_guard_off(name: str):
    font = FONTS[name]
    fused = _fuse(font, guarded=False)
    assert round(fused.score, EXAMPLE["decimals"]) == font["guard_off"]
    assert fused.guard == ()
    assert fused.terms == fused.observed == len(font["z"])


@pytest.mark.parametrize("name", list(FONTS))
def test_guard_on(name: str):
    """The median basis (owner ruling of 2026-09-26): the guard fires on no example font."""
    font = FONTS[name]
    fused = _fuse(font, guarded=True)
    assert round(fused.score, EXAMPLE["decimals"]) == font["guard_on"]
    assert round(fused.score, EXAMPLE["published_decimals"]) == font["published"]
    assert dict(fused.guard) == font["guarded"]


def test_jetbrains_mono_exact_arithmetic():
    """5.9625 / 2.4 with the median basis; 5.76375 / 2.275 under M1's mean of the others,
    where Debian's weight halves from 0.25 to 0.125."""
    font = FONTS["JetBrains Mono"]
    fused = _fuse(font, guarded=True)
    assert fused.score == pytest.approx(5.9625 / 2.4, abs=1e-12)
    others = _fuse(font, guarded=True, basis="others_mean")
    assert others.weight == pytest.approx(1.875)
    assert others.score == pytest.approx(5.76375 / 2.275, abs=1e-12)
    assert round(others.score, EXAMPLE["decimals"]) == font["others_mean_on"]
    assert dict(others.guard) == font["others_mean_guarded"]


def test_the_guard_gap_for_jetbrains_mono():
    """Debian sits 1.11 z below the median 2.70, under the 1.5 gap; it sat 1.53 z below
    mean(3.54, 2.70) = 3.12, just over it, under M1's first reading."""
    z = FONTS["JetBrains Mono"]["z"]
    median_gap = z["arch"] - z["debian"]
    assert median_gap == pytest.approx(1.11)
    assert median_gap < GUARD["gap"]
    others_gap = (z["homebrew"] + z["arch"]) / 2 - z["debian"]
    assert others_gap == pytest.approx(1.53)
    assert others_gap > GUARD["gap"]


def test_signal_shares():
    """Full coverage keeps 83% of the signal; Monaspace without Debian keeps 81%."""
    prior = EXAMPLE["kappa"] * EXAMPLE["w_total"]
    full = _fuse(FONTS["Inter"], guarded=False).weight
    monaspace = _fuse(FONTS["Monaspace"], guarded=False).weight
    assert full / (prior + full) == pytest.approx(0.8333, abs=1e-4)
    assert monaspace / (prior + monaspace) == pytest.approx(
        FONTS["Monaspace"]["signal_share"], abs=1e-4
    )


@pytest.mark.parametrize("guarded", [False, True])
def test_order(guarded: bool):
    scored = []
    for name, font in FONTS.items():
        fused = _fuse(font, guarded=guarded)
        scored.append(
            Scored(name.lower(), name, fused.score, fused.weight, None, fused.observed, 3)
        )
    assert [f.name for f in sorted(scored, key=sort_key)] == [
        "JetBrains Mono",
        "Inter",
        "Monaspace",
    ]
