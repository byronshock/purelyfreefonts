"""Cut the site's interface font, Arimo, to Latin as WOFF2.

Run from the repository root, then commit what it writes:

    uv run python site/static/_src/make_ui_font.py UPRIGHT ITALIC

- ``UPRIGHT`` is ``Arimo[wght].ttf`` and ``ITALIC`` is ``Arimo-Italic[wght].ttf``, Arimo 1.341
  from google/fonts at 23e54b51 (``ofl/arimo``; the upright is the catalog's own font file).
  The script refuses any other file, so the output stays the same.
- The subset is Google Fonts' "latin" ranges (``LATIN``), which hold every character the site
  writes. A character outside them falls back to Liberation Sans or Arial, which have
  Arimo's metrics. Every OpenType layout feature and the weight axis (400 to 700) are kept.
- The name table is kept whole, so the copyright and the OFL notice travel inside each file
  (OFL 1.1, condition 2). Arimo has no Reserved Font Name, so a subset keeps its name.
- Output: ``site/static/fonts/arimo.woff2`` and ``arimo-italic.woff2``. The build copies
  them to ``/assets/ui/`` and declares them ahead of the CSS parts (site/CONTRACT.md,
  section 6; AUTHORITY.md, "Interface font").

Nothing here uses the network.
"""

import argparse
import hashlib
import io
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

FONTS = {  # output name: sha256 of the input it is cut from
    "arimo.woff2": "e43898b143ec826ac8cb4034816458a7047fbe0836558de2a1f8c6223ae3e0ca",
    "arimo-italic.woff2": "a80fc54fd0233c1dfe298577c4d00f5ae81d5bb83510975e473c47e699b7f4ed",
}
# Google Fonts' "latin" subset (its CSS API's unicode-range for latin).
LATIN = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,"
    "U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
)
OUT = Path(__file__).resolve().parent.parent / "fonts"


def cut(blob: bytes) -> bytes:
    """``blob`` as a Latin WOFF2 with every layout feature, the weight axis and all names."""
    # recalcTimestamp=False keeps head.modified as it was, so the output doesn't change.
    font = TTFont(io.BytesIO(blob), recalcTimestamp=False)
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=subset.parse_unicodes(LATIN))
    subsetter.subset(font)
    out = io.BytesIO()
    font.flavor = "woff2"
    font.save(out)
    return out.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("upright", type=Path, help="Arimo[wght].ttf, Arimo 1.341")
    ap.add_argument("italic", type=Path, help="Arimo-Italic[wght].ttf, Arimo 1.341")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    for (name, pinned), path in zip(FONTS.items(), (args.upright, args.italic), strict=True):
        blob = path.read_bytes()
        if (sha := hashlib.sha256(blob).hexdigest()) != pinned:
            raise SystemExit(f"{path}: not the pinned file for {name} (sha256 {sha})")
        (OUT / name).write_bytes(cut(blob))
        print(OUT / name)


if __name__ == "__main__":
    main()
