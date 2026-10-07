"""Write the site's wordmark, the owner's "Purely Free Fonts", to site/static/wordmark.svg.

Usage (from the repository root):

    uv run python site/static/_src/make_wordmark.py

- The source is ``wordmark-source.svg`` beside this script: the owner's drawing, "Purely Free
  Fonts" in mixed case, light (his file ``purely-free-fonts-schoolbook-light-mixed.svg``, saved
  by Inkscape), pinned by its sha256 (owner rulings of 2026-10-06, AUTHORITY.md "Headline font").
- The site serves the drawing only: the view box, the title, the white plate ("Typography
  started out as black on white", 2026-10-04) and the black group of letter paths, exactly as
  drawn. Inkscape's editor settings, metadata and element ids are left out. No text, script,
  style or link, so it renders the same in every browser and under the CSP.
- A new version of the wordmark is a new owner ruling: replace the source and update
  ``SOURCE_SHA256`` with it.
"""

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "wordmark-source.svg"
OUT = HERE.parent / "wordmark.svg"
SOURCE_SHA256 = "46254277025d01a5ee9cd86893054517441c06ce115796f0b9a5fb6f67959efe"
SVG = "{http://www.w3.org/2000/svg}"


def drawing_only(source: bytes) -> str:
    """The drawing of ``source``: view box, title, white rectangle and the letters' group."""
    root = ET.fromstring(source)
    title = root.find(f"{SVG}title")
    rect = root.find(f"{SVG}rect")
    group = root.find(f"{SVG}g")
    if title is None or rect is None or group is None:
        raise SystemExit("not the expected drawing: it needs a title, a rect and a group")
    paths = group.findall(f"{SVG}path")
    if not paths or len(group) != len(paths):
        raise SystemExit("not the expected drawing: the letters' group must hold only paths")
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="{}" width="{}" height="{}">'.format(
            root.get("viewBox"), root.get("width"), root.get("height")
        ),
        f"  <title>{title.text}</title>",
        '  <rect width="{}" height="{}" fill="{}"/>'.format(
            rect.get("width"), rect.get("height"), rect.get("fill")
        ),
        '  <g transform="{}" fill="{}">'.format(group.get("transform"), group.get("fill")),
        *(f'    <path d="{p.get("d")}"/>' for p in paths),
        "  </g>",
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    data = SOURCE.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if sha != SOURCE_SHA256:
        raise SystemExit(
            f"not the pinned wordmark source (sha256 {sha}); a new one needs an owner ruling"
        )
    OUT.write_text(drawing_only(data), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
