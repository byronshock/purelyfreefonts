"""engine.equate: the ruler, equating onto it, and overlap scaling."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tff_catalog.engine.equate import build_ruler, equate_source, overlap, overlap_scale
from tff_catalog.engine.stats import midrank_p, probit

NEG_INF = -math.inf


def _ruler(n: int) -> dict[str, float]:
    return build_ruler({f"f{i:02d}": float(i) for i in range(n)})


def test_ruler_is_probit_of_midranks():
    ruler = build_ruler({"a": 5.0, "b": 0.0, "c": 20.0, "d": 0.0})
    pool = [0.0, 0.0, 5.0, 20.0]
    assert ruler == {
        "a": probit(midrank_p(5.0, pool, include_self=True)),
        "b": probit(2 / 8),
        "c": probit(7 / 8),
        "d": probit(2 / 8),
    }
    assert list(ruler) == ["a", "b", "c", "d"]


def test_ruler_zero_counts_tie_at_the_bottom_and_stay_finite():
    """Casks with no analytics row count 0: no floor, one bottom tie block."""
    ruler = build_ruler({"x": 0.0, "y": 0.0, "z": 0.0, "top": 1000.0})
    assert ruler["x"] == ruler["y"] == ruler["z"] == probit(3 / 8)
    assert all(math.isfinite(z) for z in ruler.values())


def test_ruler_edge_cases():
    assert build_ruler({}) == {}
    assert build_ruler({"only": 3.0}) == {"only": 0.0}
    with pytest.raises(ValueError, match="NaN"):
        build_ruler({"a": math.nan})


def test_identical_source_reproduces_the_ruler_exactly():
    ruler = _ruler(30)
    xs = {f"f{i:02d}": float(i) ** 2 + 3 for i in range(30)}  # any monotone transform
    assert equate_source(xs, ruler) == ruler


def test_a_popular_only_source_maps_onto_the_popular_end():
    """A source that packages only the top fonts can't label its least one unpopular."""
    ruler = _ruler(40)
    xs = {f"f{i:02d}": float(100 - i) for i in range(30, 40)}  # reversed order inside
    z = equate_source(xs, ruler)
    top_z = sorted(ruler[f] for f in xs)
    assert min(z.values()) == top_z[0]
    assert max(z.values()) == top_z[-1]
    assert z["f30"] == top_z[-1]  # the source's favourite gets the top of its overlap
    assert z["f39"] == top_z[0] > probit(0.75)  # its least favourite still sits high


def test_censored_block_maps_to_the_bottom_of_the_overlap():
    ruler = _ruler(20)
    xs = {f"f{i:02d}": float(i) for i in range(10, 20)} | {"f00": NEG_INF, "f01": NEG_INF}
    z = equate_source(xs, ruler)
    scale = sorted(ruler[f] for f in xs)
    assert z["f00"] == z["f01"]
    # p = c/2n = 2/24 -> h = 1.5, halfway between the two lowest z_R in O_s
    assert z["f00"] == pytest.approx((scale[0] + scale[1]) / 2)
    assert z["f00"] < min(z[f] for f in xs if f not in {"f00", "f01"})


def test_a_family_outside_the_ruler_lands_between_its_neighbours():
    ruler = _ruler(20)
    xs = {f"f{i:02d}": float(i) for i in range(20)} | {"new": 9.5}
    z = equate_source(xs, ruler)
    assert ruler["f09"] < z["new"] < ruler["f10"]
    assert {f: z[f] for f in ruler} == ruler  # outsiders never move the families in O_s


def test_a_family_outside_the_ruler_shares_a_tie_with_ruler_families():
    """Ties share a mid-rank within a source (§6), ruler family or not.

    Counting the outsider into a tie it shares would pull its p toward 0.5: at
    the ends of a 1,000-family scale that costs 0.16 z for identical evidence.
    """
    ruler = build_ruler({f"f{i:04d}": float(i) for i in range(1000)})
    xs = {f"f{i:04d}": float(i) for i in range(2, 1000)} | {"f0000": NEG_INF, "f0001": NEG_INF}
    xs |= {"top": 999.0, "mid": 700.0, "cens": NEG_INF, "above": 5000.0, "between": 700.5}
    z = equate_source(xs, ruler)
    assert z["top"] == z["f0999"] == max(ruler.values())
    assert z["mid"] == z["f0700"]
    assert z["cens"] == z["f0000"] == z["f0001"]  # one censored block
    assert z["above"] == max(ruler.values())  # a value past every other clamps to the top
    assert z["f0700"] < z["between"] < z["f0701"]  # a new value lands between its neighbours


def test_no_overlap_gives_no_scale():
    assert equate_source({"a": 1.0}, {"b": 0.0}) == {}
    assert overlap({"a": 1.0, "b": NEG_INF, "c": 2.0}, {"b": 0.0, "c": 1.0, "d": 2.0}) == ["b", "c"]


def test_equate_rejects_nan():
    with pytest.raises(ValueError, match="NaN"):
        equate_source({"a": math.nan}, {"a": 0.0})


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, 0.0), (14, 0.0), (15, 0.3), (25, 0.5), (50, 1.0), (400, 1.0)],
)
def test_overlap_scale(n: int, expected: float):
    assert overlap_scale(n) == pytest.approx(expected)


def test_overlap_scale_custom_and_invalid():
    assert overlap_scale(4, full=8, off=3) == 0.5
    with pytest.raises(ValueError, match="full"):
        overlap_scale(10, full=0)


@given(
    st.dictionaries(st.sampled_from([f"f{i}" for i in range(30)]), st.integers(0, 9).map(float)),
    st.dictionaries(
        st.sampled_from([f"f{i}" for i in range(35)]),
        st.one_of(st.just(NEG_INF), st.integers(0, 9).map(float)),
    ),
)
def test_equated_values_stay_inside_the_overlap_scale(
    counts: dict[str, float], xs: dict[str, float]
):
    ruler = build_ruler(counts)
    z = equate_source(xs, ruler)
    shared = overlap(xs, ruler)
    if not shared:
        assert z == {}
        return
    scale = [ruler[f] for f in shared]
    assert set(z) == set(xs)
    assert all(min(scale) <= v <= max(scale) for v in z.values())
    # order-preserving, and ties share one z whether or not a family is on the ruler
    for a in xs:
        for b in xs:
            if xs[a] < xs[b]:
                assert z[a] <= z[b]
            elif xs[a] == xs[b]:
                assert z[a] == z[b]


@given(st.dictionaries(st.text("abc", min_size=1, max_size=3), st.integers(0, 6).map(float)))
def test_identity_holds_on_any_subset_with_ties(counts: dict[str, float]):
    """Equating a source equal to the ruler on any O_s returns z_R there (ties too)."""
    ruler = build_ruler(counts)
    subset = {f: x for f, x in counts.items() if len(f) != 2}
    assert equate_source(subset, ruler) == {f: ruler[f] for f in subset}
