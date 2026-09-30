"""Synthetic fonts for the specimen edge cases, built with fontTools' FontBuilder.

Every glyph is one closed polygon whose shape depends on its code point, so a
glyph drawn in the wrong place or twice changes the output. ``.notdef`` is the
classic two-contour box: a test can tell whether one was drawn.
"""

import io
from collections.abc import Iterable, Sequence

import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.TupleVariation import TupleVariation

from tff_catalog.specimens import BASIC_SAMPLE, SAMPLE

UPEM = 1000
ADVANCE = 600
NOTDEF_CONTOURS = 2

type Contour = list[tuple[int, int]]


def glyph_name(ch: str) -> str:
    return "space" if ch == " " else f"uni{ord(ch):04X}"


def letter_contour(ch: str, *, top: int = 700, left: int = 50) -> Contour:
    """A pentagon whose peak moves with the code point (stays inside the advance)."""
    peak = left + 50 + (ord(ch) * 37) % 400
    return [(left, 0), (left, top - 100), (peak, top), (left + 500, top - 100), (left + 500, 0)]


def noisy_contour(ch: str, points: int, seed: int) -> Contour:
    """A closed ring of ``points`` jittered points: big, incompressible outlines."""
    rng = np.random.default_rng([seed, ord(ch)])
    angles = np.linspace(0.0, 2 * np.pi, points, endpoint=False)
    radius = 250 + rng.integers(-120, 120, size=points)
    xs = (300 + radius * np.cos(angles)).round().astype(int)
    ys = (350 + radius * np.sin(angles)).round().astype(int)
    return [(int(x), int(y)) for x, y in zip(xs, ys, strict=True)]


def _draw(contours: Iterable[Contour]) -> object:
    pen = TTGlyphPen(None)
    for contour in contours:
        pen.moveTo(contour[0])
        for point in contour[1:]:
            pen.lineTo(point)
        pen.closePath()
    return pen.glyph()


def notdef_contours() -> list[Contour]:
    outer = [(50, 0), (50, 700), (550, 700), (550, 0)]
    inner = [(100, 50), (500, 50), (500, 650), (100, 650)]
    return [outer, inner]


def make_font(
    chars: Iterable[str],
    *,
    family: str = "Test Sans",
    space: bool = True,
    noisy: int = 0,
    seed: int = 1,
    tall: str = "",
    overhang: str = "",
    empty: str = "",
    axis: tuple[float, float, float] | None = None,
    wght_peak: float = 1.0,
    flavor: str | None = None,
) -> bytes:
    """A TrueType font covering ``chars`` (plus a space unless ``space=False``).

    - ``noisy``: points per glyph ring instead of the simple pentagon.
    - ``tall``: characters drawn 1500 units high (ink above any line box).
    - ``overhang``: characters drawn from x = -300 (a negative left side bearing).
    - ``empty``: characters mapped to glyphs with no outline.
    - ``axis``: a ``wght`` axis (min, default, max) with one gvar master at
      normalized ``wght_peak`` that widens every glyph by 200 units.
    - ``flavor``: "woff" or "woff2" for a web-font file of the same font.
    """
    wanted = sorted({c for c in chars if c != " "} | ({" "} if space else set()))
    order = [".notdef", *(glyph_name(c) for c in wanted)]
    glyphs = {".notdef": _draw(notdef_contours())}
    for ch in wanted:
        if ch == " " or ch in empty:
            glyphs[glyph_name(ch)] = _draw([])
        elif noisy:
            glyphs[glyph_name(ch)] = _draw([noisy_contour(ch, noisy, seed)])
        elif ch in tall:
            glyphs[glyph_name(ch)] = _draw([letter_contour(ch, top=1500)])
        elif ch in overhang:
            glyphs[glyph_name(ch)] = _draw([letter_contour(ch, left=-300)])
        else:
            glyphs[glyph_name(ch)] = _draw([letter_contour(ch)])

    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(c): glyph_name(c) for c in wanted})
    fb.setupGlyf(glyphs)
    glyf = fb.font["glyf"]
    metrics = {}
    for name in order:
        g = glyf[name]
        lsb = g.xMin if g.numberOfContours else 0
        metrics[name] = (ADVANCE, lsb)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    if axis is not None:
        lo, default, hi = axis
        fb.setupFvar([("wght", lo, default, hi, "Weight")], [])
        fb.setupGvar({name: _widen(glyf[name], wght_peak) for name in order})
    out = io.BytesIO()
    fb.save(out)
    if flavor is None:
        return out.getvalue()
    tt = TTFont(io.BytesIO(out.getvalue()))
    tt.flavor = flavor
    web = io.BytesIO()
    tt.save(web)
    return web.getvalue()


def _widen(glyph: object, peak: float) -> list[TupleVariation]:
    """One master moving every point right of the glyph's middle by 200 units."""
    coords = list(getattr(glyph, "coordinates", []))
    if not coords:
        return []
    mid = sum(x for x, _ in coords) / len(coords)
    deltas: list[tuple[int, int] | None] = [(200 if x > mid else 0, 0) for x, _ in coords]
    deltas += [(0, 0), (200, 0), (0, 0), (0, 0)]  # phantom points: the advance grows too
    region = (min(peak, 0.0), peak, max(peak, 0.0))
    return [TupleVariation({"wght": region}, deltas)]


def sample_chars(family: str = "Test Sans", *, extra: str = "") -> str:
    """Every character a specimen of ``family`` can draw (name, sample and basic lines)."""
    return family + SAMPLE + BASIC_SAMPLE + extra


def basic_chars(family: str = "Test Sans") -> str:
    """The name and the basic-Latin line only."""
    return family + BASIC_SAMPLE


def without(chars: str, drop: Sequence[str]) -> str:
    return "".join(c for c in chars if c not in drop)
