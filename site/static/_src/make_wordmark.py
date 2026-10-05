"""Copy the site's wordmark, the owner's "PURELY FREE FONTS", into site/static/wordmark.svg.

Usage (from the repository root):

    uv run python site/static/_src/make_wordmark.py SOURCE

- ``SOURCE`` is ``wordmark/purely-free-fonts.svg`` from the owner's typeface project, Purely
  Constructed (byronshock/prb at 7d7f130, "The wordmark in the Monospace letters, kerned", on its branch
  claude/epic-cori-rcab4u),
  pinned by its sha256. The owner drew it; the site uses it as it is, white plate included
  (owner rulings of 2026-10-04, AUTHORITY.md "Headline font"): "It is part of the branding:
  Typography started out as black on white."
- The file is copied byte for byte, never redrawn: black outlines on a white rectangle, with
  no text, script, style or link, so it renders the same in every browser and under the CSP.
- A new version of the wordmark is a new owner ruling: update ``SOURCE_SHA256`` with it.
"""

import argparse
import hashlib
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "wordmark.svg"
SOURCE_SHA256 = "f6771a470eafda489655edc1161f0a0f69fb923819e5d3e4a1e53c828c1f07d3"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "source", type=Path, help="wordmark/purely-free-fonts.svg from Purely Constructed"
    )
    args = ap.parse_args()
    sha = hashlib.sha256(args.source.read_bytes()).hexdigest()
    if sha != SOURCE_SHA256:
        raise SystemExit(f"not the pinned wordmark (sha256 {sha}); a new one needs an owner ruling")
    shutil.copyfile(args.source, OUT)
    print(f"copied {args.source} to {OUT}")


if __name__ == "__main__":
    main()
