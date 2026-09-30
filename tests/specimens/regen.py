"""Regenerate ``tests/fixtures/specimens/<id>.svg``: the sample ids that point at real fonts.

Usage, from the repository root::

    uv run python -m tests.specimens.regen            # write the files, print their sha256
    uv run python -m tests.specimens.regen --check    # exit 1 if a file is out of date
    uv run python -m tests.specimens.regen --fetch    # download missing pinned fonts first

It runs the stage's own ``render_fonts`` on the rows of
``tests/fixtures/catalog-site.sample.json`` whose ``font_file`` is set (the
five pinned OFL fonts in ``tests/fixtures/specimen-fonts.toml``), reading the
fonts from ``~/.cache/tff/fonts``. The printed hashes are what the sample's
``preview.sha256`` must hold once these files are committed (site/CONTRACT.md §1).
Review the SVGs before committing them: they are the contract.
"""

import argparse
import hashlib
import json
import logging
import sys
import tempfile
from pathlib import Path

from tests.specimens import pinned

from tff_catalog.specimens.stage import render_fonts


def sample_rows() -> list[dict]:
    doc = json.loads(pinned.SAMPLE_FILE.read_text(encoding="utf-8"))
    return [f for f in doc["fonts"] if f["preview_ok"] and f["font_file"]]


def render_into(out_dir: Path) -> dict[str, str]:
    """Render the sample's real-font rows into ``out_dir``; return {id: sha256}."""
    previews = render_fonts(
        sample_rows(),
        out_dir,
        fetcher=None,
        cache_dir=pinned.FONT_CACHE,
        index_path=None,
        log=logging.getLogger("regen"),
    )
    bad = {i: p.flags for i, p in previews.items() if p.path is None}
    if bad:
        raise SystemExit(f"no specimen for {bad}; are the pinned fonts cached? (--fetch)")
    return {i: p.sha256 or "" for i, p in sorted(previews.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.specimens.regen")
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    parser.add_argument("--fetch", action="store_true", help="download missing pinned fonts")
    args = parser.parse_args(argv)
    if args.fetch:
        for pin in pinned.pins():
            if pinned.cached(pin) is None:
                pinned.download(pin)
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = render_into(Path(tmp))
            stale = [
                i
                for i in fresh
                if (Path(tmp) / f"{i}.svg").read_bytes() != _read(pinned.EXPECTED_DIR / f"{i}.svg")
            ]
        for font_id in stale:
            print(f"out of date: tests/fixtures/specimens/{font_id}.svg", file=sys.stderr)
        return 1 if stale else 0
    hashes = render_into(pinned.EXPECTED_DIR)
    for font_id, sha in hashes.items():
        path = pinned.EXPECTED_DIR / f"{font_id}.svg"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
        print(f"{font_id}\t{sha}")
    return 0


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


if __name__ == "__main__":
    sys.exit(main())
