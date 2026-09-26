"""Property tests of the ranking engine (design-m1 §6, properties 1-7).

Universes of 5-40 families (15-60 for the overlap switch) with 3-6 engine
sources (the ruler's own term plus 2-5 others), random frames, censoring,
groups and weights, run through the reference pipeline in
``tests/engine/model.py``. Under ``CI=true`` the
conftest loads the ``ci`` profile (derandomized, 200 examples).

Monotonicity (property 3) has two documented exceptions, each pinned by a
concrete example below rather than hidden in the property:

- **Guard cross-effect** (non-ruler sources): raising f in source s lowers the
  z of the fonts f passes there. That can switch a *rival's* guard off (or on)
  and lift the rival past f, even though the guard never fired for f. So the
  tests assert the tight form (``_check_monotone``) on every example: unless
  f's own guard factors change, f's score never falls and every font that
  overtakes f had its own guard factors change; with no such overtaker, f's
  order does not worsen. With the guard off there is no exception at all.
  Raises include a censored f becoming observed.
- **Quantile-map cross-effect** (the ruler): raising f's ruler count moves the
  ruler z values that other sources are equated onto. When f passes a font
  outside some O_s, or splits a tie, the scale of s shifts for every font in
  s, and a rival can gain more than f. The property holds strictly when every
  source's frame covers the whole ruler and ruler counts are distinct.
"""

from dataclasses import replace

from hypothesis import assume, event, given
from hypothesis import strategies as st
from tests.engine.model import Params, Result, Source, Universe, only, run

from tff_catalog.engine.equate import build_ruler, equate_source
from tff_catalog.engine.order import Scored, sort_key
from tff_catalog.jsonio import canonical_bytes

GROUPS = ["almanac", "arch", "debian", "fot", "google", "npm_registry"]
NAMES = ["Inter", "inter", "Fira Code", "Iosevka", "Noto Sans", "Roboto"]  # few: name ties
WEIGHTS = [0.1, 0.25, 0.5, 0.75, 1.0]


def _subset(draw: st.DrawFn, ids: list[str]) -> list[str]:
    """A random subset whose density is itself random (10% to 100%)."""
    density = draw(st.integers(1, 10))
    picks = draw(st.lists(st.integers(0, 9), min_size=len(ids), max_size=len(ids)))
    return [f for f, pick in zip(ids, picks, strict=True) if pick < density]


@st.composite
def universes(
    draw: st.DrawFn,
    *,
    min_families: int = 5,
    max_families: int = 40,
    full_cover: bool = False,
    distinct_ruler: bool = False,
) -> Universe:
    """A random universe; ``full_cover`` puts every family in the ruler and every
    ruler family in every source's frame."""
    n = draw(st.integers(min_families, max_families))
    ids = [f"f{i:02d}" for i in range(n)]
    names = {f: draw(st.sampled_from(NAMES)) for f in ids}
    if distinct_ruler:
        counts = [float(c) for c in draw(st.permutations(range(n)))]
    else:
        counts = [float(c) for c in draw(st.lists(st.integers(0, 12), min_size=n, max_size=n))]
    in_ruler = set(ids if full_cover else _subset(draw, ids))
    ruler = {f: counts[i] for i, f in enumerate(ids) if f in in_ruler}
    sources = {}
    for k in range(draw(st.integers(2, 5))):
        frame = set(_subset(draw, ids)) | (in_ruler if full_cover else set())
        raw = draw(st.lists(st.none() | st.integers(0, 15), min_size=n, max_size=n))
        sources[f"s{k}"] = Source(
            group=draw(st.sampled_from(GROUPS)),
            weight=draw(st.sampled_from(WEIGHTS)),
            values={
                f: float(raw[i]) for i, f in enumerate(ids) if f in frame and raw[i] is not None
            },
            censored=frozenset(f for i, f in enumerate(ids) if f in frame and raw[i] is None),
        )
    return Universe(names, ruler, sources, ruler_weight=draw(st.sampled_from(WEIGHTS)))


@st.composite
def params(draw: st.DrawFn, *, guard: bool | None = None, default_overlap: bool = False) -> Params:
    off = 15 if default_overlap else draw(st.sampled_from([0, 1, 3, 5, 15]))
    full = 50 if default_overlap else draw(st.integers(max(off, 1), 50))
    top = draw(st.integers(1, 12))
    use_guard = draw(st.booleans()) if guard is None else guard
    return Params(
        kappa=draw(st.sampled_from([0.0, 0.2, 0.5])),
        mu0=draw(st.sampled_from([0.0, 0.25])),
        guard=(1.5, 3, 0.5) if use_guard else None,
        overlap_full=full,
        overlap_off=off,
        min_groups=draw(st.sampled_from([1, 2, 3])),
        exact_top=top,
        bands=((top + 1, top + 10), (top + 11, top + 20)),
        open_band_from=top + 21,
    )


def _bytes(result: Result) -> bytes:
    return canonical_bytes(result.to_json())


def _with_terms(result: Result) -> bytes:
    """Placements and every fused font that has at least one weighted term."""
    ranks = result.ranks_json()
    ranks["fused"] = {f: v for f, v in ranks["fused"].items() if v["terms"]}
    return canonical_bytes(ranks)


def _guards(result: Result) -> dict[str, tuple[tuple[str, float], ...]]:
    return {f: v.guard for f, v in result.fused.items()}


# --- 1. Determinism -------------------------------------------------------------------------


def _shuffled(universe: Universe, perm: list[int]) -> Universe:
    ids = sorted(universe.names)
    order = [ids[i] for i in perm]

    def again(mapping):
        return {f: mapping[f] for f in order if f in mapping}

    return replace(
        universe,
        names=again(universe.names),
        ruler=again(universe.ruler),
        sources={
            s: replace(
                src,
                values=again(src.values),
                censored=frozenset(f for f in order if f in src.censored),
            )
            for s, src in reversed(list(universe.sources.items()))
        },
    )


@given(universes(), params(), st.data())
def test_determinism(universe: Universe, p: Params, data: st.DataObject):
    """Same inputs, any mapping order: byte-identical canonical output."""
    perm = data.draw(st.permutations(range(len(universe.names))))
    first = _bytes(run(universe, p))
    assert _bytes(run(universe, p)) == first
    assert _bytes(run(_shuffled(universe, perm), p)) == first


# --- 2. Ineligible invariance ---------------------------------------------------------------


@given(universes(max_families=30), params(), st.data())
def test_ineligible_families_move_nothing(universe: Universe, p: Params, data: st.DataObject):
    """Adding an ineligible family's observations anywhere leaves every rank unchanged."""
    extra = [f"x{i}" for i in range(data.draw(st.integers(1, 5)))]
    ruler = dict(universe.ruler)
    sources = dict(universe.sources)
    for f in extra:
        if data.draw(st.booleans()):
            ruler[f] = float(data.draw(st.integers(0, 50)))
        for s in data.draw(st.lists(st.sampled_from(sorted(sources)), unique=True)):
            src = sources[s]
            if data.draw(st.booleans()):
                src = replace(
                    src, values=dict(src.values) | {f: float(data.draw(st.integers(0, 50)))}
                )
            else:
                src = replace(src, censored=src.censored | {f})
            sources[s] = src
    names = dict(universe.names) | dict.fromkeys(extra, "Arial")
    bigger = Universe(names, ruler, sources, universe.ruler_weight, universe.ruler_censor_below)
    eligible = frozenset(universe.names)
    assert _bytes(run(bigger, p, eligible)) == _bytes(run(universe, p))


def test_filtering_must_come_before_equating():
    """Why the gates run first: an ineligible ruler cask shifts z_R and the equated z."""
    counts = {f"f{i}": float(i) for i in range(20)}
    assert build_ruler(counts | {"arial": 100.0})["f19"] != build_ruler(counts)["f19"]
    xs = {"f18": 1.0, "f19": 2.0}
    with_arial = equate_source(xs | {"arial": 3.0}, build_ruler(counts | {"arial": 100.0}))
    assert with_arial["f19"] != equate_source(xs, build_ruler(counts))["f19"]
    universe = Universe(
        {f: f for f in counts} | {"arial": "Arial"},
        counts | {"arial": 100.0},
        {"s0": Source("arch", 1.0, {f: counts[f] for f in counts} | {"arial": 100.0})},
    )
    p = Params(overlap_off=0, overlap_full=1)
    assert only(universe, frozenset(counts)) != universe
    assert _bytes(run(universe, p, frozenset(counts))) != _bytes(run(universe, p))


# --- 3. Monotonicity ------------------------------------------------------------------------


def _sort_keys(
    universe: Universe, result: Result
) -> dict[str, tuple[float, float, float, str, str]]:
    """Every fused font's ``sort_key`` (ranked or not), as the pipeline would sort it."""
    return {
        f: sort_key(
            Scored(f, universe.names[f], v.score, v.weight, result.ruler_z.get(f), v.observed, 0)
        )
        for f, v in result.fused.items()
    }


def _check_monotone(before: Universe, after: Universe, a: Result, b: Result, family: str) -> str:
    """Property 3 in its tight form, for a raise of f that moves no other font up.

    Unless f's own guard factors changed (the design's exception), f's score
    does not fall, and every font that overtakes f in sort order had its own
    guard factors change (the cross-effect). With no overtaker, f's order does
    not worsen. Returns which case held, for hypothesis' statistics.
    """
    guards_a, guards_b = _guards(a), _guards(b)
    if guards_a[family] != guards_b[family]:
        return "exception: the guard changed for f"
    assert b.fused[family].score >= a.fused[family].score
    keys_a, keys_b = _sort_keys(before, a), _sort_keys(after, b)
    overtakers = sorted(
        g
        for g in keys_a
        if g != family and keys_a[g] > keys_a[family] and keys_b[g] < keys_b[family]
    )
    for g in overtakers:
        assert guards_a[g] != guards_b[g], f"{g} overtook {family} with unchanged guard factors"
    if overtakers:
        return "exception: a rival's guard changed and it overtook f"
    if a.order(family) is not None:
        assert b.order(family) is not None
        assert b.order(family) <= a.order(family)
        return "strict: f ranked"
    return "strict: f not ranked before"


def _raise_in_source(universe: Universe, source: str, family: str, delta: float) -> Universe:
    """x_s(f) + delta; a censored f (x = -inf) becomes observed at ``delta``."""
    src = universe.sources[source]
    value = src.values[family] + delta if family in src.values else delta
    raised = replace(
        src, values=dict(src.values) | {family: value}, censored=src.censored - {family}
    )
    return replace(universe, sources=dict(universe.sources) | {source: raised})


@st.composite
def raised_in_source(draw: st.DrawFn) -> tuple[Universe, Universe, str]:
    """A universe and a copy with one non-ruler value raised (censored ones included)."""
    universe = draw(universes())
    observed = [(s, f) for s, src in sorted(universe.sources.items()) for f in sorted(src.values)]
    censored = [(s, f) for s, src in sorted(universe.sources.items()) for f in sorted(src.censored)]
    # Pick the kind first, so observed raises (the common case) are not
    # drowned out by the many censored cells hypothesis likes to generate.
    from_censored = draw(st.booleans()) if observed and censored else not observed
    candidates = censored if from_censored else observed
    assume(candidates)
    source, family = draw(st.sampled_from(candidates))
    delta = float(draw(st.integers(1, 20)))
    event(f"raised from censored: {from_censored}")
    return universe, _raise_in_source(universe, source, family, delta), family


@given(raised_in_source(), params(guard=False))
def test_monotone_non_ruler_without_guard(case: tuple[Universe, Universe, str], p: Params):
    """Strict: with the guard off, raising x_s(f) never lowers f's score or worsens its order."""
    before, after, family = case
    a, b = run(before, p), run(after, p)
    outcome = _check_monotone(before, after, a, b, family)
    event(outcome)
    assert outcome.startswith("strict")


@given(raised_in_source(), params(guard=True))
def test_monotone_non_ruler_with_guard(case: tuple[Universe, Universe, str], p: Params):
    """Strict except through a guard change, for f or for the rival that overtakes it."""
    before, after, family = case
    event(_check_monotone(before, after, run(before, p), run(after, p), family))


def test_guard_cross_effect_example():
    """Documented exception: f's raise switches a rival's guard off, and the rival overtakes f.

    100 ruler families f00-f99 (counts = index); the ruler has no term of its
    own. t1 ranks like the ruler but puts f = f84 low; t2 ranks like the ruler
    without f; s ranks like the ruler but puts rival r = f30 just above f.
    f has 2 terms, so its guard can never fire. r's s term sits 1.51 z above
    the mean of its t1 and t2 terms (guarded, x0.5). Raising f past r in s
    drops r's s term one scale step, to 1.47 above: the guard lets go, r's
    weight on a still-high term doubles, and r jumps past f's slightly higher
    score.
    """
    ids = [f"f{i:02d}" for i in range(100)]
    ruler = {f: float(i) for i, f in enumerate(ids)}
    f, r = "f84", "f30"

    def universe(f_in_s: float) -> Universe:
        sources = {
            "t1": Source("arch", 1.0, ruler | {f: 7.5}),
            "t2": Source("debian", 1.0, {g: c for g, c in ruler.items() if g != f}),
            "s": Source("fot", 1.0, ruler | {r: 84.5, f: f_in_s}),
        }
        return Universe({g: g for g in ids}, ruler, sources, ruler_weight=0.0)

    p = Params(
        overlap_off=0, overlap_full=1, exact_top=100, bands=((101, 250),), open_band_from=251
    )
    a, b = run(universe(84.0), p), run(universe(84.7), p)
    assert a.fused[f].guard == b.fused[f].guard == ()  # the guard never fired for f
    assert a.fused[r].guard == (("s", 0.5),)
    assert b.fused[r].guard == ()
    assert b.fused[f].score > a.fused[f].score  # f's own score rises...
    assert a.fused[r].score < a.fused[f].score < b.fused[f].score < b.fused[r].score
    assert b.order(f) == a.order(f) + 1  # ...but r overtakes it
    no_guard = replace(p, guard=None)  # without the guard, no exception
    assert run(universe(84.7), no_guard).order(f) <= run(universe(84.0), no_guard).order(f)


def _raise_ruler(universe: Universe, family: str, delta: float) -> Universe:
    return replace(universe, ruler=dict(universe.ruler) | {family: universe.ruler[family] + delta})


@given(universes(full_cover=True, distinct_ruler=True), params(), st.data())
def test_monotone_ruler_with_full_cover(universe: Universe, p: Params, data: st.DataObject):
    """Raising f's ruler count never worsens f's order, when every source's frame
    covers the ruler and counts stay distinct: then every source's scale is the
    same set of z values before and after. With the guard on, the same guard
    exceptions as for the other sources apply, and nothing else."""
    assume(universe.ruler)
    family = data.draw(st.sampled_from(sorted(universe.ruler)))
    delta = data.draw(st.integers(1, 10)) + 0.5  # counts stay distinct (integers + 0.5)
    after = _raise_ruler(universe, family, delta)
    a, b = run(universe, p), run(after, p)
    assert b.ruler_z[family] >= a.ruler_z[family]
    outcome = _check_monotone(universe, after, a, b, family)
    event(outcome)
    if p.guard is None:
        assert outcome.startswith("strict")


def test_ruler_cross_effect_example():
    """Documented exception: raising f's ruler count lets a rival overtake f.

    The ruler carries f and g (tied at 0); s carries f and r, and r is not a
    ruler family. Both ruler counts sit below Homebrew's censor line (2), so
    f's own Homebrew term is the censored block, which stays at the middle of
    the scale. Raising f to 1 passes g, a font s does not carry, so the only
    ruler z that s is equated onto (f's) rises: r's s term gains all of it,
    while f's gain is diluted by its unchanged Homebrew term.
    """
    universe = Universe(
        {"f": "F", "g": "G", "r": "R"},
        {"f": 0.0, "g": 0.0},
        {"s": Source("almanac", 0.1, {"f": 0.0, "r": 0.0})},
        ruler_weight=0.1,
        ruler_censor_below=2.0,
    )
    p = Params(kappa=0.0, guard=None, overlap_full=1, overlap_off=0, min_groups=1, exact_top=1)
    a, b = run(universe, p), run(_raise_ruler(universe, "f", 1.0), p)
    assert b.ruler_z["f"] > a.ruler_z["f"]
    assert (a.order("f"), a.order("r")) == (1, 2)
    assert (b.order("f"), b.order("r")) == (2, 1)


def test_ruler_tie_split_can_lower_a_censored_score():
    """The same cross-effect on a font's own score: splitting a ruler tie moves the
    median of the scale that a censored block maps onto."""
    universe = Universe({f: f for f in "abc"}, dict.fromkeys("abc", 0.0), {}, ruler_weight=0.1)
    p = Params(kappa=0.0, guard=None, overlap_full=1, overlap_off=0)
    a, b = run(universe, p), run(_raise_ruler(universe, "a", 1.0), p)
    assert a.fused["a"].score == 0.0
    assert b.fused["a"].score < 0.0  # all three still censored; the block's z fell


# --- 4. Identity ----------------------------------------------------------------------------


@given(universes(), params(), st.data())
def test_a_source_equal_to_the_ruler_gives_z_r(universe: Universe, p: Params, data: st.DataObject):
    """On the whole ruler or any subset of it, with ties: z_s = z_R exactly."""
    keep = data.draw(st.sets(st.sampled_from(sorted(universe.ruler)))) if universe.ruler else set()
    twin = {f: c for f, c in universe.ruler.items() if f in keep}
    full = dict(universe.ruler)
    sources = dict(universe.sources) | {
        "twin": Source("google", 1.0, twin),
        "full": Source("almanac", 1.0, full),
    }
    result = run(replace(universe, sources=sources), p)
    assert result.z["full"] == result.ruler_z
    assert result.z["twin"] == {f: result.ruler_z[f] for f in twin}


# --- 5. Shrinkage bounds --------------------------------------------------------------------


@given(universes(), params())
def test_shrinkage_bounds_in_the_pipeline(universe: Universe, p: Params):
    """min(μ0, min z) <= S <= max(μ0, max z) over each font's weighted terms."""
    result = run(universe, p)
    for family, fused in result.fused.items():
        live = [zs[family] for s, zs in result.z.items() if family in zs and result.weights[s] > 0]
        prior = [p.mu0] if p.kappa > 0 or not live else []
        assert min(prior + live) <= fused.score <= max(prior + live)


# --- 6. Gate --------------------------------------------------------------------------------


@given(universes(), params())
def test_gate_keeps_thin_evidence_out_of_the_top(universe: Universe, p: Params):
    """No font with fewer than min_groups groups gets an exact rank; orders stay exact."""
    result = run(universe, p)
    placements = result.placements
    event(f"held by the gate: {any(pl.gate_held for pl in placements.values())}")
    assert sorted(pl.order for pl in placements.values()) == list(range(1, len(placements) + 1))
    passing = [f for f in placements if len(result.fused[f].groups) >= p.min_groups]
    ranks = sorted(pl.rank for pl in placements.values() if pl.rank is not None)
    assert ranks == list(range(1, min(p.exact_top, len(passing)) + 1))
    for family, pl in placements.items():
        assert result.fused[family].observed >= 1  # ranked at all
        if pl.rank is not None:
            assert len(result.fused[family].groups) >= p.min_groups
            assert pl.rank == pl.order
            assert pl.band is None
        else:
            assert pl.band is not None
        if pl.gate_held or (pl.rank is None and pl.order <= p.exact_top):
            assert len(result.fused[family].groups) < p.min_groups


# --- 7. Overlap switch ----------------------------------------------------------------------


@given(universes(min_families=15, max_families=60), params(default_overlap=True))
def test_small_overlap_contributes_nothing(universe: Universe, p: Params):
    """A source overlapping the ruler on fewer than 15 families changes nothing at all."""
    result = run(universe, p)
    small = sorted(s for s in universe.sources if result.overlaps[s] < 15)
    event(f"switched off: {len(small)} of {len(universe.sources)}")
    for source in small:
        assert result.weights[source] == 0.0
    trimmed = replace(
        universe, sources={s: v for s, v in universe.sources.items() if s not in small}
    )
    assert _with_terms(run(trimmed, p)) == _with_terms(result)
    for source in universe.sources:
        if source not in small:
            assert result.weights[source] == universe.sources[source].weight * min(
                1.0, result.overlaps[source] / 50
            )
