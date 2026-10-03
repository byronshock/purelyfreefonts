"""Draw the site's wordmark, "Purely Free Fonts" in League Gothic, as outlines.

Run from the repository root, then commit what it writes:

    uv run python site/static/_src/make_wordmark.py FONT

- ``FONT`` is League Gothic 2.001, ``LeagueGothic[wdth].ttf`` from google/fonts at b5efa9c
  (``ofl/leaguegothic``). The script refuses any other file, so the output stays the same.
- HarfBuzz shapes the text with the font's default features, so its own kerning applies;
  fontTools draws the outlines into one ``<path>``. The SVG needs no font and no ``<style>``.
- The viewBox is the ink's bounding box in font units, with no side bearings, so CSS alone
  sizes it. ``width`` and ``height`` are its size at a 100 px font size.
- Black on white, in light and dark mode alike (AUTHORITY.md, "Headline font"). The SVG is
  transparent: the white comes from the header behind it.
- Output: ``site/static/wordmark.svg``.

Nothing here uses the network.
"""

import argparse
import hashlib
import io
import math
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.roundingPen import RoundingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

TEXT = "Purely Free Fonts"
FILL = "#000000"
FONT_SHA256 = "3b0e998c9a0034222394ffecdd383e6948259ad037b95555b139a217629ce1d0"
AXES = {"wdth": 100.0}  # the default instance, as in the sample the owner chose from
PX_PER_EM = 100
OUT = Path(__file__).resolve().parent.parent / "wordmark.svg"


def outline(blob: bytes, font: TTFont) -> RecordingPen:
    """The shaped text's outlines in font units (y up), starting at x = 0."""
    hbfont = hb.Font(hb.Face(blob))
    hbfont.set_variations(AXES)
    buf = hb.Buffer()
    buf.add_str(TEXT)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf, {})

    order = font.getGlyphOrder()
    glyphs = font.getGlyphSet(location=AXES)
    rec = RecordingPen()
    x = 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        glyphs[order[info.codepoint]].draw(
            TransformPen(rec, (1, 0, 0, 1, x + pos.x_offset, pos.y_offset))
        )
        x += pos.x_advance
    return rec


def wordmark(blob: bytes) -> str:
    font = TTFont(io.BytesIO(blob))
    rec = outline(blob, font)
    bounds = BoundsPen(None)
    rec.replay(bounds)
    x0, y0, x1, y1 = bounds.bounds
    x0, y0, x1, y1 = math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)
    w, h = x1 - x0, y1 - y0

    path = SVGPathPen(None, ntos=lambda v: str(int(v)))
    # Flip y (fonts are y-up, SVG is y-down) and put the ink's top-left corner at 0,0.
    rec.replay(TransformPen(RoundingPen(path), (1, 0, 0, -1, -x0, y1)))

    scale = PX_PER_EM / font["head"].unitsPerEm
    version = font["name"].getDebugName(5).removeprefix("Version ")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w * scale:g}" height="{h * scale:g}" role="img" aria-label="{TEXT}">'
        f"<!-- League Gothic {version} by The League of Moveable Type, SIL Open Font License"
        f" 1.1. wdth {AXES['wdth']:g}, shaped with HarfBuzz."
        f" Drawn by site/static/_src/make_wordmark.py. -->"
        f"<title>{TEXT}</title>"
        f'<path fill="{FILL}" d="{path.getCommands()}"/></svg>\n'
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("font", type=Path, help="LeagueGothic[wdth].ttf, League Gothic 2.001")
    blob = ap.parse_args().font.read_bytes()
    if (sha := hashlib.sha256(blob).hexdigest()) != FONT_SHA256:
        raise SystemExit(f"not the pinned League Gothic 2.001 (sha256 {sha})")
    OUT.write_text(wordmark(blob), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
