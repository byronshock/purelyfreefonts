"""``tff_site.trim``: the committed specimens cut down to the family's name (PLAN-REVIEWERS-1.md
step 7a; the owner's site ruling of 2026-10-05, ``specimen_trim_served``).

- ``trim.PINS_FILE`` lists exactly the committed specimens of the real catalog, and each one
  trims to a file of the renderer's form holding the original's first contours, moved left or
  not at all, in a view box at least ``NAME_BOX`` units tall that holds all their points.
- The five fixtures (tests/fixtures/specimens) trim to the path ``render(name_only=True)``
  draws from their pinned fonts, in a box as tall and no wider. They are skipped when the fonts
  aren't in the font cache, as in CI's test job. Before the pins were committed, all 500
  committed specimens were checked the same way against their fonts (the pull request records
  the result): the renderer's contours in all 500, at the same place except in 6 names.
- Anything else is refused: a file the renderer didn't write, or one with no second line. A
  file of one line can't be told from two by its contours alone (a backward jump between two
  contours of one name is common), which is why only pinned files are cut, and never a font
  flagged ``specimen_name_only`` (tests/site/test_build.py).

No test here needs the network.
"""

import hashlib
import json
import math
import re
from pathlib import Path

import pytest
from tests.specimens import pinned, svgpath

from tff_catalog.specimens import BASIC_SAMPLE, NAME_SIZE_EM, SAMPLE, SAMPLE_SIZE_EM, UNITS_PER_EM
from tff_catalog.specimens import render as renderer
from tff_site import build, data, trim

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
SVG_RE = re.compile(
    rb'<svg xmlns="http://www\.w3\.org/2000/svg" width="(\d+)" height="(\d+)" '
    rb'viewBox="0 0 \1 \2"><path d="([mlhvqtcsz\d -]*)"/></svg>\n'
)
# The names the sample line had shifted right, or whose ink starts at x = 0 beside it: trimmed,
# they start at their own ink's left edge.
MOVED = {
    "cinzel-decorative",
    "gochi-hand",
    "homemade-apple",
    "indie-flower",
    "reenie-beanie",
    "shrikhand",
    "sunshiney",
    "whisper",
}


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


def committed() -> dict[str, tuple[str, str]]:
    """{sha256: (id, path)} of every specimen the real catalog serves."""
    doc = json.loads((BUILD / "catalog-site.json").read_text(encoding="utf-8"))
    return {
        f["preview"]["sha256"]: (f["id"], f["preview"]["path"])
        for f in doc["fonts"]
        if data.has_specimen(f) and "specimen_name_only" not in f["flags"]
    }


def test_the_pins_are_the_committed_specimens() -> None:
    """When ``build/specimens`` changes, check each new file's cut against its font before
    pinning it, or remove the trim with the names-only redraw (step 7d)."""
    lines = trim.PINS_FILE.read_text(encoding="ascii").splitlines()
    assert lines == sorted(lines, key=lambda line: line.split()[1])
    pins = dict(line.split("  ") for line in lines)
    assert len(pins) == len(lines) == len(trim.pinned()) == 500
    assert set(pins) == trim.pinned()
    assert pins == {sha: path for sha, (_, path) in committed().items()}
    for sha, path in pins.items():
        assert hashlib.sha256((BUILD / path).read_bytes()).hexdigest() == sha, path


def test_every_pinned_specimen_trims_to_its_name_line() -> None:
    shifted = set()
    for sha, (font_id, path) in sorted(committed().items(), key=lambda kv: kv[1]):
        assert sha in trim.pinned()
        original = (BUILD / path).read_bytes()
        cut = trim.name_only(original)
        width, height, d = form(cut)
        old_width, old_height, old_d = form(original)
        contours, old_contours = svgpath.parse(d), svgpath.parse(old_d)
        assert 0 < len(contours) < len(old_contours), font_id
        dx = contours[0][0][0] - old_contours[0][0][0]
        assert dx <= 0, font_id
        assert contours == moved(old_contours[: len(contours)], dx), font_id
        if dx:
            shifted.add(font_id)
        points = svgpath.points(contours)
        assert all(0 <= x <= width and 0 <= y <= height for x, y in points), font_id
        assert max(x for x, _ in points) == width, font_id  # as wide as the ink
        assert trim.NAME_BOX <= height < old_height, font_id
        assert width <= old_width, font_id
        assert build.svg_size(cut) == (width, height)
    assert shifted == MOVED


@pytest.mark.parametrize("pin", pinned.pins(), ids=lambda p: p.sample_id)
def test_the_fixtures_trim_to_what_the_renderer_draws_name_only(pin: pinned.Pin) -> None:
    font = pinned.cached(pin)
    if font is None:
        pytest.skip(
            f"{pin.key} is not in {pinned.FONT_CACHE} (python -m tests.specimens.regen --fetch)"
        )
    two_lines = (pinned.EXPECTED_DIR / f"{pin.sample_id}.svg").read_bytes()
    spec = renderer.render(font, pin.family, SAMPLE, BASIC_SAMPLE, name_only=True)
    assert spec is not None
    width, height, d = form(trim.name_only(two_lines))
    drawn_width, drawn_height, drawn = form(spec.svg)
    assert d == drawn
    assert height == drawn_height
    assert width <= drawn_width  # the ink's right edge, not the advance past it


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
    """Two contours on the name's line, then the sample line's ink at x = 0: the name moves
    back to its own left edge (x = 6 to 0), and the box keeps the file's top margin of 4 units.
    The smooth curve's reflected control point, at x = 35, counts toward the width, as the
    renderer counts it."""
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="496" '
        b'viewBox="0 0 40 496"><path d="m6 4h10v300h-10zm14 0q5 4 10 0t2 0zm-20 310h8v8z"/>'
        b"</svg>\n"
    )
    assert trim.name_only(svg) == (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="29" height="312" '
        b'viewBox="0 0 29 312"><path d="m0 4h10v300h-10zm14 0q5 4 10 0t2 0z"/></svg>\n'
    )
