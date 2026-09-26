"""render(): the specimen SVG, its fallbacks and its instance, on synthetic FontBuilder fonts."""

import hashlib
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from tests.specimens import fontmaker, svgpath

from tff_catalog.specimens import BASIC_SAMPLE, SAMPLE, SAMPLE_SIZE_EM, UNITS_PER_EM, render

SVG_RE = re.compile(
    r'<svg xmlns="http://www\.w3\.org/2000/svg" width="(\d+)" height="(\d+)" '
    r'viewBox="0 0 \1 \2"><path d="([mlhvqtcsz\d -]*)"/></svg>\n'
)
FAMILY = "Test Sans"
BOX_TWO_LINES = math.ceil(1.2 * UNITS_PER_EM + 1.2 * 0.6 * UNITS_PER_EM)  # 492
BOX_NAME_ONLY = math.ceil(1.2 * UNITS_PER_EM)  # 308


def draw(font: bytes, family: str = FAMILY, **kw: bool) -> render.Specimen | None:
    return render.render(font, family, SAMPLE, BASIC_SAMPLE, **kw)


def contours_of(spec: render.Specimen) -> list[svgpath.Contour]:
    m = SVG_RE.fullmatch(spec.svg.decode("ascii"))
    assert m, spec.svg[:200]
    return svgpath.parse(m.group(3))


@pytest.fixture(scope="module")
def full_font() -> bytes:
    return fontmaker.make_font(fontmaker.sample_chars())


@pytest.fixture(scope="module")
def basic_font() -> bytes:
    return fontmaker.make_font(fontmaker.basic_chars())


# --- the pure helpers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("axes", "expected"),
    [
        ({}, {}),
        ({"wght": (100.0, 400.0, 900.0)}, {"wght": 400.0}),
        ({"wght": (100.0, 700.0, 900.0)}, {"wght": 400.0}),
        ({"wght": (400.0, 400.0, 900.0)}, {"wght": 400.0}),  # Orbitron: 400 is the minimum
        ({"wght": (100.0, 300.0, 400.0)}, {"wght": 400.0}),
        ({"wght": (500.0, 500.0, 900.0)}, {}),
        ({"wght": (100.0, 300.0, 399.0)}, {}),
        ({"opsz": (14.0, 14.0, 32.0)}, {}),
        ({"opsz": (14.0, 14.0, 32.0), "wght": (100.0, 400.0, 900.0)}, {"wght": 400.0}),
    ],
)
def test_variation(axes: dict, expected: dict) -> None:
    assert render.variation(axes) == expected


def test_missing_ignores_spaces_and_takes_a_set_or_a_mapping() -> None:
    text = "Ab c\u00a0d\n"
    assert render.missing({ord(c) for c in "Abcd"}, text) == set()
    assert render.missing({ord("A"): "A", ord("b"): "b"}, text) == {"c", "d"}
    assert render.missing(set(), "  ") == set()


# --- the SVG ---------------------------------------------------------------------------------


def test_svg_has_the_fixed_form_and_integer_coordinates(full_font: bytes) -> None:
    spec = draw(full_font)
    assert spec is not None
    m = SVG_RE.fullmatch(spec.svg.decode("ascii"))
    assert m
    assert (int(m.group(1)), int(m.group(2))) == (spec.width, spec.height)
    root = ET.fromstring(spec.svg)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert [child.tag for child in root] == ["{http://www.w3.org/2000/svg}path"]


def test_line_1_is_the_name_and_line_2_the_accented_sample(full_font: bytes) -> None:
    spec = draw(full_font)
    assert spec is not None
    assert spec.line == "accented"
    assert spec.height == BOX_TWO_LINES
    name_glyphs = len(FAMILY.replace(" ", ""))
    sample_glyphs = len(SAMPLE.replace(" ", ""))
    assert len(contours_of(spec)) == name_glyphs + sample_glyphs  # one contour per glyph
    # 600-unit advances on a 1000 upem: the sample line (0.6 em) is the wider one here
    sample_scale = SAMPLE_SIZE_EM * UNITS_PER_EM / fontmaker.UPEM
    assert spec.width == math.ceil(len(SAMPLE) * fontmaker.ADVANCE * sample_scale)


def test_ink_stays_inside_the_view_box(full_font: bytes) -> None:
    spec = draw(full_font)
    assert spec is not None
    pts = svgpath.points(contours_of(spec))
    assert all(0 <= x <= spec.width and 0 <= y <= spec.height for x, y in pts)


def test_output_is_byte_stable_in_another_process(full_font: bytes, tmp_path) -> None:
    spec = draw(full_font)
    assert spec is not None
    font_path = tmp_path / "f.ttf"
    font_path.write_bytes(full_font)
    code = (
        "import hashlib,sys;from pathlib import Path;"
        "from tff_catalog.specimens import SAMPLE,BASIC_SAMPLE;"
        "from tff_catalog.specimens.render import render;"
        f"s=render(Path({str(font_path)!r}).read_bytes(),{FAMILY!r},SAMPLE,BASIC_SAMPLE);"
        "print(hashlib.sha256(s.svg).hexdigest())"
    )
    env = {**os.environ, "PYTHONHASHSEED": "12345"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
    assert out.stdout.strip() == hashlib.sha256(spec.svg).hexdigest()
    assert draw(full_font) == spec


def test_the_shaping_language_is_pinned_never_the_process_locale(
    monkeypatch: pytest.MonkeyPatch, full_font: bytes
) -> None:
    # Unset, HarfBuzz would take hb_language_get_default() (the locale), and a font's locl
    # forms for that language would change the bytes from one machine to the next.
    seen: list[str | None] = []
    real = render.hb.shape

    def spy(font: object, buf: object, *a: object, **k: object) -> None:
        seen.append(buf.language)  # type: ignore[attr-defined]
        real(font, buf, *a, **k)

    monkeypatch.setattr(render.hb, "shape", spy)
    assert draw(full_font) is not None
    assert seen
    assert set(seen) == {render.SHAPING_LANGUAGE}


# --- fallbacks -------------------------------------------------------------------------------


def test_a_missing_accent_falls_back_to_the_basic_line() -> None:
    font = fontmaker.make_font(fontmaker.without(fontmaker.sample_chars(), "ť"))
    spec = draw(font)
    assert spec is not None
    assert spec.line == "basic"
    assert len(contours_of(spec)) == len((FAMILY + BASIC_SAMPLE).replace(" ", ""))


def test_basic_latin_only_font(basic_font: bytes) -> None:
    spec = draw(basic_font)
    assert spec is not None
    assert spec.line == "basic"


def test_gid_0_after_shaping_is_caught_even_when_the_cmap_test_passes(
    monkeypatch: pytest.MonkeyPatch, basic_font: bytes
) -> None:
    expected = draw(basic_font)
    monkeypatch.setattr(render, "missing", lambda cmap, text: set())
    spec = draw(basic_font)
    assert spec == expected  # the accented line shaped to .notdef and was dropped
    assert spec is not None
    assert spec.line == "basic"


def test_without_either_line_the_name_is_drawn_alone() -> None:
    font = fontmaker.make_font(FAMILY)
    spec = draw(font)
    assert spec is not None
    assert (spec.line, spec.height) == ("name", BOX_NAME_ONLY)
    assert len(contours_of(spec)) == len(FAMILY.replace(" ", ""))


def test_name_only_on_request(full_font: bytes) -> None:
    spec = draw(full_font, name_only=True)
    assert spec is not None
    assert (spec.line, spec.height) == ("name", BOX_NAME_ONLY)


def test_a_font_without_latin_glyphs_gives_nothing() -> None:
    greek = fontmaker.make_font("ΑΒΓΔΕΖΗΘαβγδεζηθ", family="Ελληνικά")
    assert draw(greek) is None
    own = draw(greek, family="ΑΒΓ")  # its own script works: only the name's glyphs matter
    assert own is not None
    assert own.line == "name"


def test_a_name_with_a_missing_glyph_gives_nothing(full_font: bytes) -> None:
    assert draw(full_font, family="Test Sans Ω") is None


@pytest.mark.parametrize("data", [b"", b"garbage" * 20, b"wOF2" + b"\0" * 60])
def test_unreadable_bytes_give_nothing(data: bytes) -> None:
    assert draw(data) is None


def test_a_missing_space_is_a_gap_never_a_notdef_box() -> None:
    font = fontmaker.make_font(fontmaker.sample_chars(), space=False)
    spec = draw(font)
    assert spec is not None
    assert spec.line == "accented"
    glyphs = len((FAMILY + SAMPLE).replace(" ", ""))
    assert len(contours_of(spec)) == glyphs  # .notdef would add two contours per space


def test_a_font_whose_glyphs_have_no_outlines_gives_nothing() -> None:
    chars = fontmaker.sample_chars()
    assert draw(fontmaker.make_font(chars, empty=chars)) is None  # a bitmap or color-only font


# --- growing the box -------------------------------------------------------------------------


def test_tall_ink_grows_the_box_instead_of_being_clipped() -> None:
    font = fontmaker.make_font(fontmaker.sample_chars(), tall="T")
    spec = draw(font)
    assert spec is not None
    assert spec.height > BOX_TWO_LINES
    pts = svgpath.points(contours_of(spec))
    assert min(y for _, y in pts) == 0
    assert all(0 <= x <= spec.width and 0 <= y <= spec.height for x, y in pts)


def test_a_negative_side_bearing_grows_the_box_to_the_left() -> None:
    font = fontmaker.make_font(fontmaker.sample_chars(), overhang="T")
    spec = draw(font)
    assert spec is not None
    pts = svgpath.points(contours_of(spec))
    assert min(x for x, _ in pts) == 0
    assert all(0 <= x <= spec.width for x, _ in pts)


# --- the instance ----------------------------------------------------------------------------


def test_wght_without_400_draws_the_default_instance() -> None:
    chars = fontmaker.sample_chars()
    # default 700 and a master at 500: a wrong wght=400 (clamped to 500) would differ
    variable = fontmaker.make_font(chars, axis=(500.0, 700.0, 900.0), wght_peak=-1.0)
    static = fontmaker.make_font(chars)
    assert draw(variable) == draw(static)


def test_wght_with_400_draws_400_not_the_default() -> None:
    chars = fontmaker.sample_chars()
    # default 700; the master at normalized -1 (wght 100) widens glyphs, so 400 is half as wide
    variable = fontmaker.make_font(chars, axis=(100.0, 700.0, 900.0), wght_peak=-1.0)
    static = fontmaker.make_font(chars)
    at_400, default = draw(variable), draw(static)
    assert at_400 is not None
    assert default is not None
    assert at_400 != default
    # wght 400 is normalized -0.5, so each outlined glyph advances 600 + 200 / 2 units; the
    # space has no outline and no delta. (wght 100 or 700 would give 800 or 600.)
    spaces = SAMPLE.count(" ")
    advances = (len(SAMPLE) - spaces) * (fontmaker.ADVANCE + 100) + spaces * fontmaker.ADVANCE
    sample_scale = SAMPLE_SIZE_EM * UNITS_PER_EM / fontmaker.UPEM
    assert at_400.width == math.ceil(advances * sample_scale)


def test_default_at_400_is_the_same_drawing() -> None:
    chars = fontmaker.sample_chars()
    variable = fontmaker.make_font(chars, axis=(100.0, 400.0, 900.0))
    assert draw(variable) == draw(fontmaker.make_font(chars))


# --- web fonts -------------------------------------------------------------------------------


@pytest.mark.parametrize("flavor", ["woff", "woff2"])
def test_woff_and_woff2_render_like_the_plain_font(full_font: bytes, flavor: str) -> None:
    web = fontmaker.make_font(fontmaker.sample_chars(), flavor=flavor)
    assert web[:4] in (b"wOFF", b"wOF2")
    assert draw(web) == draw(full_font)
