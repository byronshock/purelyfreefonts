"""RelPathPen: compact relative path data that reads back to the same integer outline."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.specimens import svgpath

from tff_catalog.specimens.render import RelPathPen

coord = st.integers(min_value=-2000, max_value=2000)
point = st.tuples(coord, coord)
segment = st.one_of(
    st.tuples(st.just("L"), st.tuples(point)),
    st.tuples(st.just("Q"), st.tuples(point, point)),
    st.tuples(st.just("C"), st.tuples(point, point, point)),
)
contour = st.tuples(point, st.lists(segment, min_size=1, max_size=8))


def _feed(pen: RelPathPen, contours: list) -> None:
    """Draw ``contours`` given in grid coordinates (the pen flips y, so pass -y)."""
    for start, segs in contours:
        pen.move_to(start[0], -start[1])
        for kind, pts in segs:
            flat = [v for x, y in pts for v in (x, -y)]
            {"L": pen.line_to, "Q": pen.quadratic_to, "C": pen.cubic_to}[kind](*flat)
        pen.close_path()


@given(st.lists(contour, max_size=6), st.integers(-500, 500), st.integers(-500, 500))
def test_path_reads_back_to_what_was_kept(contours: list, ox: int, oy: int) -> None:
    pen = RelPathPen(1.0)
    pen.place(ox, oy)
    _feed(pen, contours)
    kept = [(s, tuple(segs)) for s, segs in pen._contours]
    assert svgpath.parse(pen.path()) == kept


@given(st.lists(contour, min_size=1, max_size=6))
def test_every_kept_segment_is_a_drawn_one_or_a_straight_equivalent(contours: list) -> None:
    pen = RelPathPen(1.0)
    _feed(pen, contours)
    drawn = {(k, pts) for _, segs in contours for k, pts in segs}
    for _, segs in pen._contours:
        for kind, pts in segs:
            assert (kind, pts) in drawn or kind == "L"


def _model(contours: list, ox: int, oy: int) -> list:
    """What the pen should keep, worked out independently of it (grid coordinates).

    A curve whose control points sit on its end points is a line; a line that goes
    nowhere is dropped; a closing line back to the start is left to "z"; a contour
    with nothing left is dropped.
    """
    out = []
    for start, segs in contours:
        start = (start[0] + ox, start[1] + oy)
        cur, kept = start, []
        for kind, pts in segs:
            pts = tuple((x + ox, y + oy) for x, y in pts)
            end = pts[-1]
            if (kind == "Q" and pts[0] in (cur, end)) or (
                kind == "C" and pts[0] == cur and pts[1] == end
            ):
                kind, pts = "L", (end,)
            if kind == "L" and end == cur:
                continue
            kept.append((kind, pts))
            cur = end
        if kept and kept[-1][0] == "L" and kept[-1][1][0] == start:
            kept.pop()
        if kept:
            out.append((start, tuple(kept)))
    return out


# Small coordinates and optional closing lines make the degenerate cases common.
near = st.tuples(st.integers(-2, 2), st.integers(-2, 2))
near_segment = st.one_of(
    st.tuples(st.just("L"), st.tuples(near)),
    st.tuples(st.just("Q"), st.tuples(near, near)),
    st.tuples(st.just("C"), st.tuples(near, near, near)),
)
closing_contour = st.tuples(
    near, st.lists(st.one_of(segment, near_segment), min_size=1, max_size=8), st.booleans()
).map(lambda t: (t[0], t[1] + ([("L", (t[0],))] if t[2] else [])))


@given(st.lists(closing_contour, max_size=6), st.integers(-500, 500), st.integers(-500, 500))
def test_path_matches_an_independent_model_of_what_is_kept(
    contours: list, ox: int, oy: int
) -> None:
    pen = RelPathPen(1.0)
    pen.place(ox, oy)
    _feed(pen, contours)
    assert svgpath.parse(pen.path()) == _model(contours, ox, oy)


def test_one_pen_continues_relative_moves_across_glyphs_and_lines() -> None:
    pen = RelPathPen(1.0)
    square = [((0, 0), [("L", ((10, 0),)), ("L", ((10, 10),)), ("L", ((0, 10),))])]
    for origin in [(0, 100), (50, 100), (0, 300)]:  # two glyphs on line 1, one on line 2
        pen.place(*origin)
        _feed(pen, square)
    starts = [start for start, _ in svgpath.parse(pen.path())]
    assert starts == [(0, 100), (50, 100), (0, 300)]
    assert pen.path() == "m0 100h10v10h-10zm50 0h10v10h-10zm-50 200h10v10h-10z"


@pytest.mark.parametrize(
    ("contours", "expected"),
    [
        # a closing line that "z" draws anyway is dropped; h and v for axis lines
        ([((0, 0), [("L", ((5, 0),)), ("L", ((5, 5),)), ("L", ((0, 0),))])], "m0 0h5v5z"),
        # zero-length lines vanish; a contour with nothing left vanishes
        ([((3, 3), [("L", ((3, 3),))]), ((1, 1), [("L", ((4, 5),))])], "m1 1 3 4z"),
        # the "l" after "m" is implicit; a repeated letter is left out; no space before "-"
        ([((0, 0), [("L", ((3, 4),)), ("L", ((1, 9),))])], "m0 0 3 4-2 5z"),
        # a curve whose control sits on an end point is a line
        ([((0, 0), [("Q", ((0, 0), (7, 3)))])], "m0 0 7 3z"),
        ([((0, 0), [("C", ((0, 0), (7, 3), (7, 3)))])], "m0 0 7 3z"),
        # an exact reflection of the previous control point becomes t or s
        ([((0, 0), [("Q", ((5, -5), (10, 0))), ("Q", ((15, 5), (20, 0)))])], "m0 0q5-5 10 0t10 0z"),
        (
            [((0, 0), [("C", ((1, -5), (9, -5), (10, 0))), ("C", ((11, 5), (19, 5), (20, 0)))])],
            "m0 0c1-5 9-5 10 0s9 5 10 0z",
        ),
        # but not across a line in between
        (
            [((0, 0), [("Q", ((5, -5), (10, 0))), ("L", ((10, 3),)), ("Q", ((10, 3), (12, 9)))])],
            "m0 0q5-5 10 0v3l2 6z",
        ),
    ],
)
def test_compact_forms(contours: list, expected: str) -> None:
    pen = RelPathPen(1.0)
    _feed(pen, contours)
    assert pen.path() == expected


def test_scale_and_rounding_happen_on_absolute_points() -> None:
    pen = RelPathPen(0.256)  # 1000 units/em onto the 256 grid
    for x in range(10):  # ten glyphs at fractional origins: no drift
        pen.place(x * 153.6, 0.0)
        pen.move_to(0, 0)
        pen.line_to(600, 0)
        pen.line_to(600, 700)
        pen.close_path()
    starts = [s for s, _ in svgpath.parse(pen.path())]
    assert starts == [(round(x * 153.6), 0) for x in range(10)]


def test_fonttools_pen_protocol_is_answered() -> None:
    a, b = RelPathPen(1.0), RelPathPen(1.0)
    a.move_to(0, 0)
    a.quadratic_to(5, 5, 10, 0)
    a.quadratic_to(15, -5, 20, 0)
    a.cubic_to(21, 1, 22, 2, 23, 0)
    a.close_path()
    b.moveTo((0, 0))
    b.qCurveTo((5, 5), (15, -5), (20, 0))  # a fontTools run with an implied on-curve point
    b.curveTo((21, 1), (22, 2), (23, 0))
    b.closePath()
    assert a.path() == b.path()


def test_translate_moves_only_the_first_move() -> None:
    pen = RelPathPen(1.0)
    _feed(pen, [((0, 0), [("L", ((4, 0),)), ("L", ((4, 4),))]), ((9, 9), [("L", ((9, 12),))])])
    before = pen.path()
    assert pen.bounds() == (0, 0, 9, 12)
    pen.translate(3, 7)
    assert pen.bounds() == (3, 7, 12, 19)
    assert pen.path().removeprefix("m3 7") == before.removeprefix("m0 0")
