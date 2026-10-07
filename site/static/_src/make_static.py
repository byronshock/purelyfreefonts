"""Draw the site's static images: favicon.svg, favicon.ico, apple-touch-icon.png, share.png.

Run from anywhere, then commit what it writes:

    uv run --group browser python site/static/_src/make_static.py

- Text is drawn as outlines: HarfBuzz shapes it with the pinned OFL fonts of
  ``tests/fixtures/specimen-fonts.toml``, read from the font cache (``~/.cache/tff/fonts/<sha256>``,
  filled by the specimen tests or ``tff-site fetch-fonts``). So the images don't depend on the
  fonts installed on this computer, and the SVGs need no font and no ``<style>``.
- The SVGs use presentation attributes only, so ``favicon.svg`` also renders under the site's
  CSP. The colours are the light theme's tokens in ``site/css/00-tokens.css``, the accent
  lapis lazuli since the owner's site ruling of 2026-10-07 (``lapis_lazuli_blue``).
- Playwright's Chromium turns the SVGs into PNGs. ``favicon.ico`` holds 16, 32 and 48 px PNGs.
- Outputs: ``site/static/{favicon.svg,favicon.ico,apple-touch-icon.png,share.png}``, plus the
  SVG sources of the two PNGs next to this script, for review. The build copies only the four
  files in ``site/static/``, never this folder.

Nothing here uses the network.
"""

import argparse
import hashlib
import struct
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent
ROOT = HERE.parents[2]
PINNED = ROOT / "tests" / "fixtures" / "specimen-fonts.toml"
FONT_CACHE = Path.home() / ".cache" / "tff" / "fonts"

# Light-theme tokens (site/css/00-tokens.css). ACCENT is lapis lazuli: the owner's site
# rulings of 2026-10-07 made --c-accent lapis (lapis_lazuli_blue) and these images with it.
ACCENT = "#26619c"  # --c-accent
ACCENT_FG = "#ffffff"  # --c-accent-fg
BG = "#ffffff"  # --c-bg
FG = "#1b1d21"  # --c-fg
MUTED = "#50555c"  # --c-muted
SURFACE = "#f4f5f7"  # --c-surface
DIVIDER = "#d5d8dd"  # --c-divider

ICO_SIZES = (16, 32, 48)
TOUCH = 180
SHARE_W, SHARE_H = 1200, 630

# The mark: "Aa", the usual sign for type, in Inter ExtraBold.
MARK_TEXT = "Aa"
MARK_FONT = ("inter", {"wght": 800, "opsz": 32})

# share.png wording. Page text: the owner approves it with the site's other wording.
SHARE_DOMAIN = "purelyfreefonts.com"
SHARE_TITLE = "Purely Free Fonts"
SHARE_LINES = (
    "The most popular fonts whose licenses allow",
    "all personal and commercial use.",
)
# One "Aa" per pinned font, left to right: sans, sans (CFF), mono, script, display.
SHARE_SPECIMENS = (
    ("inter", {"wght": 400, "opsz": 32}),
    ("source-sans-3", {}),
    ("jetbrains-mono", {"wght": 400}),
    ("lobster", {}),
    ("orbitron", {"wght": 500}),
)


@dataclass(frozen=True, slots=True)
class Run:
    """One shaped line of text: its outlines, advance and exact ink box, in font units."""

    outlines: RecordingPen
    advance: float
    box: tuple[float, float, float, float]
    upem: int


@cache
def _font_file(key: str, fonts_dir: Path) -> bytes:
    pinned = {f["key"]: f for f in tomllib.loads(PINNED.read_text(encoding="utf-8"))["font"]}
    entry = pinned[key]
    path = fonts_dir / entry["sha256"]
    if not path.is_file():
        raise SystemExit(
            f"{key}: {path} is missing. Fill the font cache first (tff-site fetch-fonts, or run "
            "the network-marked specimen tests)."
        )
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise SystemExit(f"{key}: {path} does not match its pinned sha256")
    return data


def shape(key: str, text: str, variations: dict[str, float], fonts_dir: Path) -> Run:
    """Shape ``text`` in the pinned font ``key`` at the given axis values."""
    face = hb.Face(hb.Blob(_font_file(key, fonts_dir)))
    font = hb.Font(face)
    if variations:
        font.set_variations(variations)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {})
    outlines, x = RecordingPen(), 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        moved = TransformPen(outlines, (1, 0, 0, 1, x + pos.x_offset, pos.y_offset))
        font.draw_glyph_with_pen(info.codepoint, moved)
        x += pos.x_advance
    bounds = BoundsPen(None)
    outlines.replay(bounds)
    return Run(outlines, x, bounds.bounds, face.upem)


def _num(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def path_data(run: Run, size: float, x: float, baseline: float) -> str:
    """SVG path data for ``run`` at ``size`` px, its origin (pen start, baseline) at (x, y)."""
    scale = size / run.upem
    pen = SVGPathPen(None, ntos=_num)
    run.outlines.replay(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
    return pen.getCommands()


def centred(run: Run, size: float, cx: float, cy: float) -> str:
    """Path data for ``run`` with its ink box centred on (cx, cy)."""
    scale = size / run.upem
    x_min, y_min, x_max, y_max = run.box
    return path_data(run, size, cx - (x_min + x_max) / 2 * scale, cy + (y_min + y_max) / 2 * scale)


def left_aligned(run: Run, size: float, left: float, baseline: float) -> str:
    """Path data for ``run`` with its ink starting exactly at ``left``."""
    return path_data(run, size, left - run.box[0] * size / run.upem, baseline)


def ink_width(run: Run, size: float) -> float:
    return (run.box[2] - run.box[0]) * size / run.upem


def svg(width: int, height: int, body: list[str]) -> str:
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
    )
    return "\n".join([head, *body, "</svg>", ""])


def mark(size: float, x: float, y: float, *, radius: float, fonts_dir: Path) -> list[str]:
    """The mark as SVG elements: an accent square with "Aa" in the accent text colour."""
    run = shape(MARK_FONT[0], MARK_TEXT, MARK_FONT[1], fonts_dir)
    # Size the text so its ink spans 72% of the square's width.
    text_size = 0.72 * size / ((run.box[2] - run.box[0]) / run.upem)
    d = centred(run, text_size, x + size / 2, y + size / 2)
    rx = f' rx="{_num(radius)}"' if radius else ""
    return [
        f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(size)}" height="{_num(size)}"{rx} '
        f'fill="{ACCENT}"/>',
        f'<path fill="{ACCENT_FG}" d="{d}"/>',
    ]


def favicon_svg(fonts_dir: Path) -> str:
    return svg(32, 32, mark(32, 0, 0, radius=7, fonts_dir=fonts_dir))


def touch_svg(fonts_dir: Path) -> str:
    # iOS rounds the corners itself and wants no transparency, so the square fills the icon.
    return svg(TOUCH, TOUCH, mark(TOUCH, 0, 0, radius=0, fonts_dir=fonts_dir))


def share_svg(fonts_dir: Path) -> str:
    left = 80
    body = [f'<rect width="{SHARE_W}" height="{SHARE_H}" fill="{BG}"/>']

    # Mark and domain, top left.
    mark_size, mark_top = 72, 64
    body += mark(mark_size, left, mark_top, radius=16, fonts_dir=fonts_dir)
    domain = shape("inter", SHARE_DOMAIN, {"wght": 600, "opsz": 32}, fonts_dir)
    domain_size = 34
    cap = 0.727 * domain_size  # Inter's cap height is 1490 of 2048 units
    baseline = mark_top + mark_size / 2 + cap / 2
    d = left_aligned(domain, domain_size, left + mark_size + 24, baseline)
    body.append(f'<path fill="{FG}" d="{d}"/>')

    # Title and the one-line definition.
    title = shape("inter", SHARE_TITLE, {"wght": 800, "opsz": 32}, fonts_dir)
    body.append(f'<path fill="{FG}" d="{left_aligned(title, 112, left, 292)}"/>')
    for i, line in enumerate(SHARE_LINES):
        run = shape("inter", line, {"wght": 400, "opsz": 32}, fonts_dir)
        d = left_aligned(run, 42, left, 372 + i * 56)
        body.append(f'<path fill="{MUTED}" d="{d}"/>')

    # A strip of specimens along the bottom.
    strip_top = 470
    body.append(
        f'<rect y="{strip_top}" width="{SHARE_W}" height="{SHARE_H - strip_top}" fill="{SURFACE}"/>'
    )
    body.append(f'<rect y="{strip_top}" width="{SHARE_W}" height="2" fill="{DIVIDER}"/>')
    slot = (SHARE_W - 2 * left) / len(SHARE_SPECIMENS)
    for i, (key, axes) in enumerate(SHARE_SPECIMENS):
        run = shape(key, "Aa", axes, fonts_dir)
        size = 104
        x = left + slot * (i + 0.5) - ink_width(run, size) / 2
        body.append(f'<path fill="{FG}" d="{left_aligned(run, size, x, 590)}"/>')
    return svg(SHARE_W, SHARE_H, body)


def rasterise(jobs: list[tuple[str, int, bool]]) -> list[bytes]:
    """Render each (svg, size or width, transparent) job to PNG bytes with Chromium."""
    from playwright.sync_api import sync_playwright

    shots = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(device_scale_factor=1)
        for source, width, transparent in jobs:
            src_w, src_h = (int(v) for v in source.split('viewBox="0 0 ')[1].split('"')[0].split())
            height = round(width * src_h / src_w)
            page.set_viewport_size({"width": width, "height": height})
            page.set_content(
                "<!doctype html><style>html,body{margin:0;background:transparent}"
                f"svg{{display:block;width:{width}px;height:{height}px}}</style>{source}"
            )
            clip = {"x": 0, "y": 0, "width": width, "height": height}
            shots.append(page.screenshot(clip=clip, omit_background=transparent, type="png"))
        browser.close()
    return shots


def ico(pngs: list[tuple[int, bytes]]) -> bytes:
    """An ICO file holding each (size, PNG bytes) image as a PNG entry."""
    out = [struct.pack("<HHH", 0, 1, len(pngs))]
    offset = 6 + 16 * len(pngs)
    for size, png in pngs:
        out.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset))
        offset += len(png)
    out += [png for _, png in pngs]
    return b"".join(out)


def png_size(png: bytes) -> tuple[int, int]:
    width, height = struct.unpack(">II", png[16:24])
    return width, height


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--fonts-dir", type=Path, default=FONT_CACHE, help="the font cache")
    parser.add_argument("--svg-only", action="store_true", help="write the SVGs, skip the PNGs")
    args = parser.parse_args(argv)

    favicon = favicon_svg(args.fonts_dir)
    touch = touch_svg(args.fonts_dir)
    share = share_svg(args.fonts_dir)
    (STATIC / "favicon.svg").write_text(favicon, encoding="utf-8")
    (HERE / "apple-touch-icon.svg").write_text(touch, encoding="utf-8")
    (HERE / "share.svg").write_text(share, encoding="utf-8")
    if args.svg_only:
        return

    jobs = [(favicon, size, True) for size in ICO_SIZES]
    jobs += [(touch, TOUCH, False), (share, SHARE_W, False)]
    *icons, touch_png, share_png = rasterise(jobs)
    (STATIC / "favicon.ico").write_bytes(ico(list(zip(ICO_SIZES, icons, strict=True))))
    (STATIC / "apple-touch-icon.png").write_bytes(touch_png)
    (STATIC / "share.png").write_bytes(share_png)
    for name in ("favicon.svg", "favicon.ico", "apple-touch-icon.png", "share.png"):
        path = STATIC / name
        size = f" {png_size(path.read_bytes())}" if name.endswith(".png") else ""
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size} bytes{size}")


if __name__ == "__main__":
    main()
