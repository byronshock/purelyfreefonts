"""engine.order: ranked at all, the 2-group gate, tie order and placement with bands."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tff_catalog.engine.order import Placement, Scored, band_label, gate, place, ranked, sort_key


def _font(
    id: str,
    score: float = 0.0,
    *,
    name: str | None = None,
    weight: float = 1.0,
    ruler_z: float | None = 0.0,
    observed: int = 2,
    groups: int = 2,
) -> Scored:
    return Scored(id, name or id.title(), score, weight, ruler_z, observed, groups)


def test_ranked_needs_one_observed_term():
    assert ranked(_font("a", observed=1))
    assert not ranked(_font("a", observed=0))
    assert not ranked(_font("a", observed=1), min_observed=2)


def test_gate_counts_groups():
    assert gate(_font("a", groups=2))
    assert not gate(_font("a", groups=1))
    assert gate(_font("a", groups=1), groups=1)


def test_tie_order():
    """Score, then evidence weight, then ruler z (none last), then casefolded name, then id."""
    fonts = [
        _font("n1", 1.0, ruler_z=None),
        _font("low", 0.5),
        _font("w", 1.0, weight=2.0),
        _font("z2", 1.0, ruler_z=2.0),
        _font("b2", 1.0, name="beta", ruler_z=1.0),
        _font("a2", 1.0, name="Beta", ruler_z=1.0),
        _font("c", 1.0, name="alpha", ruler_z=1.0),
    ]
    assert [f.id for f in sorted(fonts, key=sort_key)] == ["w", "z2", "c", "a2", "b2", "n1", "low"]


def test_sort_key_rejects_nan():
    with pytest.raises(ValueError, match="NaN"):
        sort_key(_font("a", math.nan))


def test_band_label():
    assert band_label(101) == "101-250"
    assert band_label(250) == "101-250"
    assert band_label(251) == "251-500"
    assert band_label(501) == "501+"
    assert band_label(9000) == "501+"
    assert band_label(50) is None
    assert band_label(7, bands=((4, 6),), open_from=7) == "7+"


def _ordered(n: int, failing: set[int]) -> tuple[list[Scored], dict[str, bool]]:
    fonts = [_font(f"f{i:03d}", -i) for i in range(1, n + 1)]
    return fonts, {f.id: int(f.id[1:]) not in failing for f in fonts}


def test_place_exact_top_and_bands():
    fonts, gates = _ordered(600, set())
    placed = place(fonts, gates)
    assert placed["f001"] == Placement(1, 1, None, False)
    assert placed["f100"] == Placement(100, 100, None, False)
    assert placed["f101"] == Placement(101, None, "101-250", False)
    assert placed["f251"].band == "251-500"
    assert placed["f501"].band == "501+"
    assert [p.order for p in placed.values()] == list(range(1, 601))


def test_gate_failures_move_just_after_the_top():
    """D14: failing fonts inside the top sit at 101+, keeping their relative order."""
    fonts, gates = _ordered(300, {3, 50, 101, 150})
    placed = place(fonts, gates)
    assert placed["f004"].order == 3
    assert placed["f103"] == Placement(100, 100, None, False)  # the 100th passing font
    assert placed["f003"] == Placement(101, None, "101-250", True)
    assert placed["f050"] == Placement(102, None, "101-250", True)
    # passed over while filling the top, but it sorted at 101: no rank lost to the gate
    assert placed["f101"] == Placement(103, None, "101-250", False)
    assert placed["f104"].order == 104  # the rest keep their positions
    assert placed["f150"] == Placement(150, None, "101-250", False)
    ranks = [p.rank for p in placed.values() if p.rank is not None]
    assert ranks == list(range(1, 101))
    assert all(gates[i] for i, p in placed.items() if p.rank is not None)


def test_small_universe_never_ranks_a_held_font():
    fonts, gates = _ordered(6, {1, 4})
    placed = place(fonts, gates, exact_top=10, bands=((11, 20),), open_band_from=21)
    assert [placed[f].order for f in ("f002", "f003", "f005", "f006", "f001", "f004")] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    assert placed["f001"] == Placement(5, None, "11-20", True)
    assert placed["f006"] == Placement(4, 4, None, False)


def test_a_view_where_nothing_passes_keeps_its_order_and_ranks_nothing():
    """E.g. dev_apps, whose sources share one group: no exact ranks at all."""
    fonts, gates = _ordered(150, set(range(1, 151)))
    placed = place(fonts, gates)
    assert [p.order for p in placed.values()] == list(range(1, 151))
    assert all(p.rank is None for p in placed.values())
    assert placed["f001"] == Placement(1, None, "101-250", True)
    assert placed["f100"].gate_held
    assert placed["f101"] == Placement(101, None, "101-250", False)
    assert placed["f150"].band == "101-250"


def test_place_missing_gate_fails_and_duplicates_are_refused():
    fonts, _ = _ordered(3, set())
    placed = place(fonts, {"f001": True, "f003": True}, exact_top=2, bands=((3, 5),))
    assert placed["f002"].gate_held
    assert placed["f003"].rank == 2
    with pytest.raises(ValueError, match="duplicate"):
        place([fonts[0], fonts[0]], {"f001": True})


def test_place_returns_keys_in_order():
    fonts, gates = _ordered(5, {2})
    placed = place(fonts, gates, exact_top=3, bands=((4, 5),))
    assert list(placed) == ["f001", "f003", "f004", "f002", "f005"]


@given(st.lists(st.booleans(), max_size=60), st.integers(0, 15))
def test_place_contract(verdicts: list[bool], top: int):
    """The whole contract of ``place`` on any gate pattern and top size."""
    fonts = [_font(f"f{i:03d}", -i) for i in range(1, len(verdicts) + 1)]
    gates = {f.id: ok for f, ok in zip(fonts, verdicts, strict=True)}
    placed = place(
        fonts, gates, exact_top=top, bands=((top + 1, top + 10),), open_band_from=top + 11
    )
    orders = {f.id: placed[f.id].order for f in fonts}
    assert sorted(orders.values()) == list(range(1, len(fonts) + 1))
    passing = [f.id for f in fonts if gates[f.id]]
    failing = [f.id for f in fonts if not gates[f.id]]
    # each kind keeps its relative order
    assert [orders[i] for i in passing] == sorted(orders[i] for i in passing)
    assert [orders[i] for i in failing] == sorted(orders[i] for i in failing)
    # the exact top is the first passing fonts, ranked 1..k
    top_ids = [i for i in orders if placed[i].rank is not None]
    assert sorted(top_ids, key=orders.__getitem__) == passing[:top]
    assert sorted(placed[i].rank for i in top_ids) == list(range(1, len(top_ids) + 1))
    for position, f in enumerate(fonts, start=1):
        p = placed[f.id]
        # the gate only ever moves passing fonts up and failing fonts down
        assert p.order <= position if gates[f.id] else p.order >= position
        assert p.gate_held == (not gates[f.id] and position <= top)
        assert (p.rank is None) == (p.band is not None)
        if p.rank is not None:
            assert p.rank == p.order
