"""The committed ``tests/fixtures/specimens/*.svg`` (no fonts needed, so CI runs this)."""

import re

from tests.specimens import pinned, svgpath

from tff_catalog.specimens import budget

SVG_RE = re.compile(
    r'<svg xmlns="http://www\.w3\.org/2000/svg" width="(\d+)" height="(\d+)" '
    r'viewBox="0 0 \1 \2"><path d="([mlhvqtcsz\d -]*)"/></svg>\n'
)


def test_one_file_per_sample_id_with_a_real_font() -> None:
    names = sorted(p.name for p in pinned.EXPECTED_DIR.glob("*.svg"))
    assert names == sorted(f"{p.sample_id}.svg" for p in pinned.pins())


def test_each_file_has_the_fixed_form_and_stays_in_its_box() -> None:
    for path in sorted(pinned.EXPECTED_DIR.glob("*.svg")):
        m = SVG_RE.fullmatch(path.read_text(encoding="ascii"))
        assert m, path.name
        width, height = int(m.group(1)), int(m.group(2))
        pts = svgpath.points(svgpath.parse(m.group(3)))
        assert pts, path.name
        assert all(0 <= x <= width and 0 <= y <= height for x, y in pts), path.name


def test_the_committed_files_pass_the_budget() -> None:
    report = budget.check(pinned.EXPECTED_DIR)
    assert report.files == 5
    assert report.ok
    assert report.small_share == 1.0
