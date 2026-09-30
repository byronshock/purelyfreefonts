"""The five pinned real OFL fonts: expected bytes, fallbacks, instances and budget.

Marked ``network``: a font missing from ``~/.cache/tff/fonts`` is downloaded
(and the test skips when it can't be). With the cache filled, nothing is fetched.
Regenerate the expected files with ``uv run python -m tests.specimens.regen``.
"""

import gzip
import hashlib
import logging
from pathlib import Path

import pytest
import uharfbuzz as hb
from tests.specimens import pinned, regen, svgpath

from tff_catalog.specimens import (
    BASIC_SAMPLE,
    MAX_FILE_GZIP_BYTES,
    SAMPLE,
    SMALL_GZIP_BYTES,
    budget,
    render,
    stage,
)

pytestmark = pytest.mark.network

PINS = pinned.pins()


@pytest.fixture(params=[p.key for p in PINS])
def pin_and_font(request: pytest.FixtureRequest, pinned_fonts: dict) -> tuple[pinned.Pin, bytes]:
    return pinned_fonts[request.param]


def test_specimen_matches_the_committed_file(pin_and_font: tuple[pinned.Pin, bytes]) -> None:
    pin, font = pin_and_font
    spec = render.render(font, pin.family, SAMPLE, BASIC_SAMPLE)
    assert spec is not None
    expected = (pinned.EXPECTED_DIR / f"{pin.sample_id}.svg").read_bytes()
    assert spec.svg == expected, "run: uv run python -m tests.specimens.regen, and review"


def test_every_pinned_font_draws_the_sample_line(
    pin_and_font: tuple[pinned.Pin, bytes],
) -> None:
    """The sample needs no accents, so a basic-Latin font (Orbitron) draws it too."""
    pin, font = pin_and_font
    spec = render.render(font, pin.family, SAMPLE, BASIC_SAMPLE)
    assert spec is not None
    assert spec.line == "sample"


def test_variable_fonts_draw_wght_400_and_static_fonts_their_only_instance(
    pin_and_font: tuple[pinned.Pin, bytes],
) -> None:
    pin, font = pin_and_font
    face = hb.Face(hb.Blob(font))
    axes = {a.tag: (a.min_value, a.default_value, a.max_value) for a in face.axis_infos}
    assert tuple(sorted(axes)) == tuple(sorted(pin.axes))
    assert render.variation(axes) == ({"wght": 400.0} if "wght" in pin.axes else {})


def test_within_budget(pin_and_font: tuple[pinned.Pin, bytes]) -> None:
    pin, font = pin_and_font
    spec = render.render(font, pin.family, SAMPLE, BASIC_SAMPLE)
    assert spec is not None
    assert budget.gzip_size(spec.svg) <= MAX_FILE_GZIP_BYTES
    assert len(gzip.compress(spec.svg, 9, mtime=0)) <= SMALL_GZIP_BYTES


def test_ink_inside_the_view_box_and_no_notdef(pin_and_font: tuple[pinned.Pin, bytes]) -> None:
    pin, font = pin_and_font
    spec = render.render(font, pin.family, SAMPLE, BASIC_SAMPLE)
    assert spec is not None
    d = spec.svg.decode("ascii").split('d="', 1)[1].split('"', 1)[0]
    pts = svgpath.points(svgpath.parse(d))
    assert all(0 <= x <= spec.width and 0 <= y <= spec.height for x, y in pts)
    face = hb.Face(hb.Blob(font))
    text = pin.family + (BASIC_SAMPLE if pin.latin == "basic" else SAMPLE)
    assert not render.missing(set(face.unicodes), text)


def test_stage_on_the_sample_rows_writes_the_committed_files(
    pinned_fonts: dict, tmp_path: Path
) -> None:
    previews = stage.render_fonts(
        regen.sample_rows(),
        tmp_path,
        fetcher=None,
        cache_dir=pinned.FONT_CACHE,
        index_path=tmp_path / "index.json",
        log=logging.getLogger("tests.specimens"),
    )
    assert set(previews) == {p.sample_id for p in PINS}
    for font_id, preview in previews.items():
        expected = (pinned.EXPECTED_DIR / f"{font_id}.svg").read_bytes()
        assert preview == stage.Preview(
            f"specimens/{font_id}.svg", hashlib.sha256(expected).hexdigest(), ()
        )
        assert (tmp_path / f"{font_id}.svg").read_bytes() == expected
    assert budget.check(tmp_path).ok
