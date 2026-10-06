"""``tff_site.trim``: the committed specimens cut down to the family's name (PLAN-REVIEWERS-1.md
step 7a; the owner's site ruling of 2026-10-05, ``specimen_trim_served``).

- ``trim.PINS_FILE`` lists exactly the specimens the real catalog and the sample serve, and
  ``trim.SHIFTS`` only pinned files.
- Each pinned file trims to the file whose sha256 ``TRIMMED`` records, so any change to a cut
  fails here, fonts or not. That file has the renderer's form and holds the original's first
  contours, moved left by the file's ``SHIFTS`` entry, in a view box at least ``NAME_BOX``
  units tall that holds all their points and is as wide as their ink.
- Each pinned file whose font is in the font cache trims to exactly the path
  ``render(name_only=True)`` draws, in a box as tall and no wider, once the font has redrawn
  the two-line file byte for byte. CI's site-real job caches every real font (``tff-site
  fetch-fonts``), so it checks all 500; the test job caches none and skips.
- Anything else is refused: a file the renderer didn't write, one with no second line, or a
  shift past the name's ink. A file of one line can't be told from two by its contours alone
  (a backward jump between two contours of one name is common), which is why only pinned files
  are cut, and never a font flagged ``specimen_name_only`` (tests/site/test_build.py).

No test here needs the network.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.specimens import pinned, svgpath

from tff_catalog.specimens import BASIC_SAMPLE, NAME_SIZE_EM, SAMPLE, SAMPLE_SIZE_EM, UNITS_PER_EM
from tff_catalog.specimens import render as renderer
from tff_site import build, data, fonts, trim

ROOT = Path(__file__).resolve().parents[2]
TRIMMED = Path(__file__).with_name("trimmed.sha256")
SVG_RE = re.compile(
    rb'<svg xmlns="http://www\.w3\.org/2000/svg" width="(\d+)" height="(\d+)" '
    rb'viewBox="0 0 \1 \2"><path d="([mlhvqtcsz\d -]*)"/></svg>\n'
)
# The names the sample line had shifted right (PLAN-REVIEWERS-1.md step 7a).
SHIFTED = {
    "cinzel-decorative",
    "gochi-hand",
    "homemade-apple",
    "indie-flower",
    "reenie-beanie",
    "shrikhand",
    "sunshiney",
}


@dataclass(frozen=True, slots=True)
class Served:
    """A specimen the real catalog or the sample serves."""

    path: str  # from the repository root, as trim.PINS_FILE names it
    sha256: str
    font_id: str
    family: str
    font_file: str  # the font file's sha256, its name in the font cache


def served() -> list[Served]:
    """Every specimen the real catalog and the sample serve, in path order."""
    out = []
    for data_file in (ROOT / "build" / "catalog-site.json", pinned.SAMPLE_FILE):
        base = data_file.parent.relative_to(ROOT).as_posix()
        doc = json.loads(data_file.read_text(encoding="utf-8"))
        out += [
            Served(
                f"{base}/{f['preview']['path']}",
                f["preview"]["sha256"],
                f["id"],
                f["family"],
                f["font_file"]["sha256"],
            )
            for f in doc["fonts"]
            if data.has_specimen(f) and "specimen_name_only" not in f["flags"]
        ]
    return sorted(out, key=lambda s: s.path)


def form(svg: bytes) -> tuple[int, int, str]:
    """Width, height and path data of a specimen of the renderer's form."""
    match = SVG_RE.fullmatch(svg)
    assert match, svg[:120]
    return int(match[1]), int(match[2]), match[3].decode("ascii")


def moved(contours: list[svgpath.Contour], dx: int) -> list[svgpath.Contour]:
    return [
        ((s[0] + dx, s[1]), tuple((k, tuple((x + dx, y) for x, y in pts)) for k, pts in segs))
        for s, segs in contours
    ]


def cut(spec: Served) -> bytes:
    return trim.name_only((ROOT / spec.path).read_bytes(), trim.SHIFTS.get(spec.sha256, 0))


def test_the_pins_are_the_served_specimens() -> None:
    """When ``build/specimens`` changes, check each new file's cut against its font (the
    font test below) before pinning it and recording its cut, or remove the trim with the
    names-only redraw (step 7d)."""
    lines = trim.PINS_FILE.read_text(encoding="ascii").splitlines()
    specs = served()
    assert lines == [f"{s.sha256}  {s.path}" for s in specs]  # in LC_ALL=C order
    assert len(trim.pinned()) == len(specs) == 505
    for spec in specs:
        assert hashlib.sha256((ROOT / spec.path).read_bytes()).hexdigest() == spec.sha256
    assert {s.font_id for s in specs if s.sha256 in trim.SHIFTS} == SHIFTED
    assert len(trim.SHIFTS) == len(SHIFTED)
    assert all(shift > 0 for shift in trim.SHIFTS.values())


def test_every_pinned_specimen_trims_to_its_recorded_cut() -> None:
    lines = [line for line in TRIMMED.read_text(encoding="ascii").splitlines() if line[0] != "#"]
    expected = {path: sha for sha, path in (line.split("  ") for line in lines)}
    specs = served()
    assert list(expected) == [s.path for s in specs]
    for spec in specs:
        original = (ROOT / spec.path).read_bytes()
        trimmed = cut(spec)
        assert hashlib.sha256(trimmed).hexdigest() == expected[spec.path], spec.font_id
        width, height, d = form(trimmed)
        old_width, old_height, old_d = form(original)
        contours, old_contours = svgpath.parse(d), svgpath.parse(old_d)
        assert 0 < len(contours) < len(old_contours), spec.font_id
        shift = trim.SHIFTS.get(spec.sha256, 0)
        assert contours == moved(old_contours[: len(contours)], -shift), spec.font_id
        points = svgpath.points(contours)
        assert all(0 <= x <= width and 0 <= y <= height for x, y in points), spec.font_id
        assert max(x for x, _ in points) == width, spec.font_id  # as wide as the ink
        assert trim.NAME_BOX <= height < old_height, spec.font_id
        assert width <= old_width, spec.font_id
        assert build.svg_size(trimmed) == (width, height)


def test_each_cut_is_what_the_renderer_draws_name_only() -> None:
    checked = 0
    for spec in served():
        path = fonts.cache_path(spec.font_file)
        if not path.is_file():
            continue
        font = path.read_bytes()
        if hashlib.sha256(font).hexdigest() != spec.font_file:
            continue  # damaged; tff-site fetch-fonts replaces it
        two_lines = renderer.render(font, spec.family, SAMPLE, BASIC_SAMPLE)
        assert two_lines is not None, spec.font_id
        assert two_lines.svg == (ROOT / spec.path).read_bytes(), f"{spec.font_id}: another font"
        names = renderer.render(font, spec.family, SAMPLE, BASIC_SAMPLE, name_only=True)
        assert names is not None, spec.font_id
        width, height, d = form(cut(spec))
        drawn_width, drawn_height, drawn = form(names.svg)
        assert (d, height) == (drawn, drawn_height), spec.font_id
        assert width <= drawn_width, spec.font_id  # the ink's right edge, not the advance past it
        checked += 1
    if not checked:
        pytest.skip(f"no pinned specimen's font is in {fonts.DEFAULT_CACHE} (tff-site fetch-fonts)")


def test_the_boxes_are_the_renderers() -> None:
    line = renderer.LINE_HEIGHT * UNITS_PER_EM
    assert math.ceil(line * NAME_SIZE_EM) == trim.NAME_BOX
    assert math.ceil(line * (NAME_SIZE_EM + SAMPLE_SIZE_EM)) == trim.TWO_LINE_BOX


@pytest.mark.parametrize(
    ("svg", "error"),
    [
        # the build tests' stand-in: absolute commands
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="2800" height="492" '
            b'viewBox="0 0 2800 492"><path d="M0 0h5v10z"/></svg>\n',
            "not a specimen as",
        ),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="492" '
            b'viewBox="0 0 20 492"><path d="m9 9h5v5zm-9 300h5v5z"/><script/></svg>\n',
            "not a specimen as",
        ),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="492" '
            b'viewBox="0 0 20 492"><path d="m9 9h5v5zm-9 300h5v5"/></svg>\n',
            "left open",
        ),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="492" '
            b'viewBox="0 0 20 492"><path d="m9 9h5v5zh5m-9 300h5v5z"/></svg>\n',
            "h before any m",
        ),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="400" '
            b'viewBox="0 0 20 400"><path d="m9 9h5v5zm-9 300h5v5z"/></svg>\n',
            "less than two lines",
        ),
    ],
    ids=["absolute", "markup", "open", "no-move", "short"],
)
def test_anything_else_is_refused(svg: bytes, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        trim.name_only(svg)


def test_a_small_two_line_drawing_is_cut_and_realigned() -> None:
    """Two contours on the name's line, then the sample line's ink at x = 0. Cut alone, the
    name keeps its place (x = 6); with a shift of 6 it moves to x = 0, and no further. The box
    keeps the file's top margin of 4 units. The smooth curve's reflected control point, at
    x = 35, counts toward the width, as the renderer counts it."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="496" '
        b'viewBox="0 0 40 496"><path d="m6 4h10v300h-10zm14 0q5 4 10 0t2 0zm-20 310h8v8z"/>'
        b"</svg>\n"
    )
    assert trim.name_only(svg) == (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="35" height="312" '
        b'viewBox="0 0 35 312"><path d="m6 4h10v300h-10zm14 0q5 4 10 0t2 0z"/></svg>\n'
    )
    assert trim.name_only(svg, 6) == (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="29" height="312" '
        b'viewBox="0 0 29 312"><path d="m0 4h10v300h-10zm14 0q5 4 10 0t2 0z"/></svg>\n'
    )
    with pytest.raises(ValueError, match="left of x = 0"):
        trim.name_only(svg, 7)
