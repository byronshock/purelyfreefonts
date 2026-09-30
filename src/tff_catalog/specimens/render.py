"""Render one specimen SVG with HarfBuzz (design-m2 §3). Owner: agent A5.

- ``uharfbuzz`` Buffer, ``guess_segment_properties``, ``shape`` with default features
  (the language is pinned to ``SHAPING_LANGUAGE``, never the process locale's).
- Variation: ``wght=400`` when 400 is inside the axis range, else the default instance.
- ``font.draw_glyph_with_pen(gid, RelPathPen)``, one pen across all lines
  (relative ``m`` commands continue across lines).
- Integer coordinates on a 256-units-per-em grid.
- ``<svg xmlns=… width=W height=H viewBox="0 0 W H"><path d="…"/></svg>\\n``:
  fixed attribute order, no timestamps, byte-stable across runs.
- Missing glyphs: a cmap test before shaping and a ``gid 0`` check after. The
  sample line falls back to the basic line, then to the name only; failing
  that, no specimen (the font is flagged ``specimen_failed``).

Layout, in ems of the name's size (``UNITS_PER_EM`` grid units): each line box is
``LINE_HEIGHT`` of its size with the baseline ``ASCENT`` below the box top, so
line 1 is 1.2 em tall and line 2 is 0.72 em. Every font gets the same box and so
the same type size in the list; only ink outside it (a tall swash, a negative
left side bearing) grows the view box, so nothing is ever clipped. The name
starts at x = 0 and the width is the longer line's advance.
"""

import io
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

import uharfbuzz as hb

from tff_catalog.specimens import DEFAULT_WEIGHT, NAME_SIZE_EM, SAMPLE_SIZE_EM, UNITS_PER_EM

SVG_NS = "http://www.w3.org/2000/svg"
LINE_HEIGHT = 1.2  # line box height, as a multiple of the line's size
ASCENT = 0.95  # baseline position below the line box top, as a multiple of the line's size
MISSING_SPACE_ADVANCE = 0.25  # em; a space the font lacks is left blank, never drawn as .notdef
WEIGHT_AXIS = "wght"
# The buffer's language, set before guess_segment_properties: left unset, HarfBuzz takes the
# process locale's (hb_language_get_default), and a "pl" or "tr" locale would switch on a font's
# locl forms, so the same font would draw differently on another machine. "und" (undetermined)
# selects the default language system, which is what an unset language gives under the C locale.
SHAPING_LANGUAGE = "und"

_WOFF_MAGIC = (b"wOFF", b"wOF2")

type Point = tuple[int, int]
type Segment = tuple[str, tuple[Point, ...]]  # ("L" | "Q" | "C", points ending at the end point)


@dataclass(frozen=True, slots=True)
class Specimen:
    svg: bytes
    line: Literal["sample", "basic", "name"]  # what line 2 shows ("name": no line 2)
    width: int
    height: int


class RelPathPen:
    """A fontTools-style pen writing a compact relative SVG path on the integer grid.

    Points arrive in font units. ``place`` sets where the glyph origin sits on the
    grid (y grows downwards, as in SVG) and ``scale`` converts font units to grid
    units. Every point is rounded to an integer grid point first and the path
    stores differences between rounded points, so rounding never accumulates.

    One pen draws every glyph of every line: the path's relative ``m`` after a
    ``z`` continues from the previous contour's start, wherever it was.

    Output is compact but plain SVG 1.1 path data: relative commands only,
    ``h``/``v`` for axis-aligned lines, ``t``/``s`` when the control point is the
    exact reflection of the previous one, a repeated command letter left out,
    and no separator before a minus sign. Zero-length segments, a closing line
    that ``z`` draws anyway, and contours with nothing left are dropped.

    Besides the snake_case methods, it answers to fontTools' pen protocol
    (``moveTo``, ``lineTo``, ``qCurveTo``, ``curveTo``, ``closePath``), which is
    what ``uharfbuzz.Font.draw_glyph_with_pen`` calls.
    """

    def __init__(self, scale: float) -> None:
        self.scale = scale
        self._ox = 0.0
        self._oy = 0.0
        self._contours: list[tuple[Point, list[Segment]]] = []
        self._start: Point | None = None
        self._segments: list[Segment] = []
        self._cur: Point = (0, 0)
        self.moves = 0  # contours started, dropped ones included: "did this glyph draw anything"

    # --- placement ------------------------------------------------------------------------

    def place(self, x: float, y: float, scale: float | None = None) -> None:
        """Put the next glyph's origin at grid point (``x``, ``y``); optionally change the scale."""
        self._ox, self._oy = x, y
        if scale is not None:
            self.scale = scale

    def translate(self, dx: int, dy: int) -> None:
        """Shift everything drawn so far by whole grid units."""
        self._contours = [
            (_shift(start, dx, dy), [(k, tuple(_shift(p, dx, dy) for p in pts)) for k, pts in segs])
            for start, segs in self._contours
        ]

    def bounds(self) -> tuple[int, int, int, int] | None:
        """(xmin, ymin, xmax, ymax) of every point, control points included; None if empty."""
        xs: list[int] = []
        ys: list[int] = []
        for start, segs in self._contours:
            for x, y in (start, *(p for _, pts in segs for p in pts)):
                xs.append(x)
                ys.append(y)
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    # --- the pen ----------------------------------------------------------------------------

    def _pt(self, x: float, y: float) -> Point:
        return round(self._ox + x * self.scale), round(self._oy - y * self.scale)

    def move_to(self, x: float, y: float) -> None:
        if self._start is not None:
            self.close_path()  # an open contour fills as if closed
        self.moves += 1
        self._start = self._cur = self._pt(x, y)
        self._segments = []

    def line_to(self, x: float, y: float) -> None:
        self._line(self._pt(x, y))

    def quadratic_to(self, x1: float, y1: float, x: float, y: float) -> None:
        c, p = self._pt(x1, y1), self._pt(x, y)
        if c in (self._cur, p):  # the curve is a straight segment
            self._line(p)
            return
        self._segments.append(("Q", (c, p)))
        self._cur = p

    def cubic_to(self, x1: float, y1: float, x2: float, y2: float, x: float, y: float) -> None:
        c1, c2, p = self._pt(x1, y1), self._pt(x2, y2), self._pt(x, y)
        if c1 == self._cur and c2 == p:
            self._line(p)
            return
        self._segments.append(("C", (c1, c2, p)))
        self._cur = p

    def close_path(self) -> None:
        if self._start is None:
            return
        segs = self._segments
        if segs and segs[-1][0] == "L" and segs[-1][1][-1] == self._start:
            segs.pop()  # "z" draws the closing line
        if segs:
            self._contours.append((self._start, segs))
        self._cur = self._start  # after "z" the current point is the contour's start
        self._start = None
        self._segments = []

    def _line(self, p: Point) -> None:
        if self._start is None:
            raise ValueError("line before move_to")
        if p != self._cur:
            self._segments.append(("L", (p,)))
            self._cur = p

    # fontTools' pen protocol, for uharfbuzz.Font.draw_glyph_with_pen
    def moveTo(self, pt: tuple[float, float]) -> None:
        self.move_to(*pt)

    def lineTo(self, pt: tuple[float, float]) -> None:
        self.line_to(*pt)

    def qCurveTo(self, *pts: tuple[float, float]) -> None:
        # HarfBuzz sends one off-curve point per call; split a fontTools-style run anyway.
        *offs, end = pts
        for i, off in enumerate(offs):
            nxt = offs[i + 1] if i + 1 < len(offs) else None
            to = end if nxt is None else ((off[0] + nxt[0]) / 2, (off[1] + nxt[1]) / 2)
            self.quadratic_to(off[0], off[1], to[0], to[1])

    def curveTo(self, *pts: tuple[float, float]) -> None:
        (x1, y1), (x2, y2), (x, y) = pts
        self.cubic_to(x1, y1, x2, y2, x, y)

    def closePath(self) -> None:
        self.close_path()

    def endPath(self) -> None:
        self.close_path()

    # --- output -----------------------------------------------------------------------------

    def path(self) -> str:
        """The path data: relative commands from (0, 0), deterministic for the same drawing."""
        if self._start is not None:
            self.close_path()
        out = _PathWriter()
        cur: Point = (0, 0)
        for start, segs in self._contours:
            out.command("m", _diff(start, cur))
            out.last = "l"  # coordinate pairs after "m" are implicit "l"
            cur = start
            ctrl: Point | None = None  # reflection source for t/s
            kind_prev = ""
            for kind, pts in segs:
                end = pts[-1]
                if kind == "L":
                    dx, dy = _diff(end, cur)
                    if dy == 0:
                        out.command("h", (dx,))
                    elif dx == 0:
                        out.command("v", (dy,))
                    else:
                        out.command("l", (dx, dy))
                    ctrl = None
                elif kind == "Q":
                    c = pts[0]
                    if kind_prev == "Q" and ctrl is not None and c == _reflect(ctrl, cur):
                        out.command("t", _diff(end, cur))
                    else:
                        out.command("q", (*_diff(c, cur), *_diff(end, cur)))
                    ctrl = c
                else:  # "C"
                    c1, c2 = pts[0], pts[1]
                    if kind_prev == "C" and ctrl is not None and c1 == _reflect(ctrl, cur):
                        out.command("s", (*_diff(c2, cur), *_diff(end, cur)))
                    else:
                        out.command("c", (*_diff(c1, cur), *_diff(c2, cur), *_diff(end, cur)))
                    ctrl = c2
                kind_prev = kind
                cur = end
            out.command("z", ())
            cur = start
        return out.text()


class _PathWriter:
    """Joins commands and integers with the fewest separators; drops repeated letters."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self.last = ""
        self._after_number = False

    def command(self, letter: str, numbers: Iterable[int]) -> None:
        if letter != self.last or letter in "mz":
            self._parts.append(letter)
            self._after_number = False
        self.last = letter
        for n in numbers:
            if self._after_number and n >= 0:
                self._parts.append(" ")
            self._parts.append(str(n))
            self._after_number = True

    def text(self) -> str:
        return "".join(self._parts)


def _shift(p: Point, dx: int, dy: int) -> Point:
    return p[0] + dx, p[1] + dy


def _diff(p: Point, q: Point) -> Point:
    return p[0] - q[0], p[1] - q[1]


def _reflect(c: Point, about: Point) -> Point:
    return 2 * about[0] - c[0], 2 * about[1] - c[1]


def variation(axes: Mapping[str, tuple[float, float, float]]) -> dict[str, float]:
    """The instance to draw: {"wght": 400.0} when allowed, else {} (the default).

    ``axes`` maps an axis tag to (minimum, default, maximum), as in ``fvar``.
    """
    weight = axes.get(WEIGHT_AXIS)
    if weight is not None and weight[0] <= DEFAULT_WEIGHT <= weight[2]:
        return {WEIGHT_AXIS: DEFAULT_WEIGHT}
    return {}


def missing(cmap: Mapping[int, object] | set[int], text: str) -> set[str]:
    """Characters of ``text`` (ignoring spaces) the font's cmap lacks."""
    return {ch for ch in text if not ch.isspace() and ord(ch) not in cmap}


def sfnt_bytes(font: bytes) -> bytes:
    """The font as plain sfnt data: WOFF and WOFF2 are unpacked (HarfBuzz reads neither)."""
    if font[:4] not in _WOFF_MAGIC:
        return font
    from fontTools.ttLib import TTFont  # only for the rare WOFF upstream file

    tt = TTFont(io.BytesIO(font))
    tt.flavor = None
    out = io.BytesIO()
    tt.save(out, reorderTables=False)
    return out.getvalue()


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    size: float  # ems of the name's size
    top: float  # grid units


def render(
    font: bytes, family: str, sample: str, basic: str, *, name_only: bool = False
) -> Specimen | None:
    """Render the family name and the best sample line; None when even the name fails."""
    try:
        data = sfnt_bytes(font)
    except Exception:  # fontTools raises many types for a broken WOFF
        return None
    face = hb.Face(hb.Blob(data))
    if face.glyph_count == 0:
        return None
    hb_font = hb.Font(face)
    axes = {a.tag: (a.min_value, a.default_value, a.max_value) for a in face.axis_infos}
    instance = variation(axes)
    if instance:
        hb_font.set_variations(instance)
    cmap = set(face.unicodes)
    if missing(cmap, family):
        return None
    upem = face.upem

    name_line = _Line(family, NAME_SIZE_EM, 0.0)
    second_top = NAME_SIZE_EM * LINE_HEIGHT * UNITS_PER_EM
    candidates: list[tuple[Literal["sample", "basic"], str]] = (
        [] if name_only else [("sample", sample), ("basic", basic)]
    )
    for kind, text in candidates:
        if missing(cmap, text):
            continue
        drawn = _draw(hb_font, upem, [name_line, _Line(text, SAMPLE_SIZE_EM, second_top)])
        if drawn is not None:
            return _specimen(*drawn, kind)
    drawn = _draw(hb_font, upem, [name_line])
    return None if drawn is None else _specimen(*drawn, "name")


def _draw(
    hb_font: hb.Font, upem: int, lines: list[_Line]
) -> tuple[RelPathPen, float, float] | None:
    """Draw ``lines`` into one pen: (pen, widest advance, box bottom) in grid units.

    None when a line shapes to a ``.notdef`` glyph or draws no outline at all.
    """
    pen = RelPathPen(1.0)
    widest = bottom = 0.0
    for line in lines:
        scale = line.size * UNITS_PER_EM / upem
        baseline = line.top + line.size * ASCENT * UNITS_PER_EM
        advance = _draw_line(hb_font, upem, line.text, pen, scale, baseline)
        if advance is None:
            return None
        widest = max(widest, advance * scale)
        bottom = line.top + line.size * LINE_HEIGHT * UNITS_PER_EM
    return pen, widest, bottom


def _draw_line(
    hb_font: hb.Font, upem: int, text: str, pen: RelPathPen, scale: float, baseline: float
) -> float | None:
    """Shape and draw one line at ``baseline``; return its advance in font units."""
    if not text.strip():
        return None
    buf = hb.Buffer()
    buf.add_codepoints([ord(ch) for ch in text])  # clusters are indices into text
    buf.language = SHAPING_LANGUAGE
    buf.guess_segment_properties()
    hb.shape(hb_font, buf)
    x = y = 0.0
    moves_before = pen.moves
    draw: Callable[[int, RelPathPen], None] = hb_font.draw_glyph_with_pen
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        if info.codepoint == 0:
            if not text[info.cluster].isspace():
                return None  # a .notdef box: never drawn
            x += MISSING_SPACE_ADVANCE * upem
            continue
        pen.place((x + pos.x_offset) * scale, baseline - (y + pos.y_offset) * scale, scale)
        draw(info.codepoint, pen)
        x += pos.x_advance
        y += pos.y_advance
    if pen.moves == moves_before:
        return None  # no outlines at all (a bitmap or color-only font)
    return x


def _specimen(
    pen: RelPathPen, advance: float, box_bottom: float, kind: Literal["sample", "basic", "name"]
) -> Specimen:
    """Frame the drawing: the fixed box, grown (never clipped) to hold all the ink."""
    left, top, right, bottom = pen.bounds() or (0, 0, 0, 0)
    dx, dy = max(0, -left), max(0, -top)
    pen.translate(dx, dy)
    width = max(math.ceil(advance), right) + dx
    height = max(math.ceil(box_bottom), bottom) + dy
    svg = (
        f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><path d="{pen.path()}"/></svg>\n'
    )
    return Specimen(svg=svg.encode("ascii"), line=kind, width=width, height=height)
