"""Regenerate collectors' golden files, and the helpers the contract test shares.

Usage, from the repository root::

    uv run python -m tests.helpers.regen <collector> [<collector> ...]
    uv run python -m tests.helpers.regen --all
    uv run python -m tests.helpers.regen --check <collector>   # exit 1 if out of date

It parses ``tests/fixtures/collectors/<name>/snapshot/`` with the collector's
settings from ``config/sources/<name>.toml`` and writes the records, in
canonical order, to ``tests/fixtures/collectors/<name>/expected.jsonl``.
Review the diff before committing it: the golden file is the contract.

A fixture directory holds:

- ``snapshot/``: ``manifest.json`` and the extracts, as the store keeps them;
- ``expected.jsonl``: what ``parse()`` of ``snapshot/`` must return;
- ``http/``: recorded responses for ``fetch()`` (``mockhttp``), and/or
  ``git/<host>/<owner>/<repo>/``: files served as a local git remote;
- ``previous/`` (optional): an earlier snapshot, passed as ``ctx.previous``;
- ``root/`` (optional): the repository ``fetch()`` sees (``ctx.paths``), for a
  collector whose input list lives under ``config/`` (foundries);
- ``NOTICE``: the source and license of trimmed real data, or "Synthetic".
"""

import argparse
import gzip
import logging
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tests.helpers import FIXTURES, ROOT

from tff_catalog import jsonio
from tff_catalog.collectors import discover, get
from tff_catalog.collectors.base import Collector, ParseContext, load_settings
from tff_catalog.paths import Paths
from tff_catalog.records import Record, sort_key, to_json, write_jsonl
from tff_catalog.store import MANIFEST_NAME, Manifest, Snapshot

COLLECTOR_FIXTURES = FIXTURES / "collectors"
EXPECTED = "expected.jsonl"
LOG = logging.getLogger("tests.regen")


def fixture_dir(name: str, base: Path = COLLECTOR_FIXTURES) -> Path:
    """``tests/fixtures/collectors/<name>/``."""
    return base / name


def load_snapshot(directory: Path, source: str) -> Snapshot:
    """Open a snapshot directory kept outside a store (a fixture's ``snapshot/``)."""
    manifest = Manifest.from_json(jsonio.load(Path(directory) / MANIFEST_NAME))
    if manifest.source != source:
        raise ValueError(f"{directory}: manifest source {manifest.source!r}, expected {source!r}")
    return Snapshot(source=source, date=manifest.date, path=Path(directory), manifest=manifest)


def golden(path: Path) -> object:
    """What a golden-file test compares for ``path``: the same on every machine.

    Builds of zlib (zlib, zlib-ng and their versions) compress the same input to different
    bytes, and CI's Python (uv's) does not use this machine's zlib. So a ``.gz`` file
    compares gunzipped, a snapshot's ``manifest.json`` as ``portable`` has it, and any
    other file byte for byte.
    """
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes())
    if path.name == MANIFEST_NAME:
        return portable(Manifest.from_json(jsonio.load(path)))
    return path.read_bytes()


def portable(manifest: Manifest) -> dict[str, Any]:
    """``manifest.to_json()`` without the stored ``sha256`` and ``bytes`` of gzipped extracts,
    which depend on the zlib build (see ``golden``)."""
    doc = manifest.to_json()
    doc["extracts"] = [
        {k: v for k, v in e.items() if k not in ("sha256", "bytes")}
        if e["path"].endswith(".gz")
        else e
        for e in doc["extracts"]
    ]
    return doc


def parse_records(collector: Collector, snapshot: Snapshot, settings: object) -> list[Record]:
    """Run ``collector.parse`` on ``snapshot``; return the records in canonical order."""
    ctx = ParseContext(snapshot=snapshot, settings=settings, log=LOG.getChild(collector.name))
    return sorted(collector.parse(ctx), key=sort_key)


def encode(recs: Iterable[Record]) -> bytes:
    """The bytes ``records.write_jsonl`` writes for ``recs`` (canonical order, one per line)."""
    return b"".join(jsonio.canonical_bytes(to_json(r)) + b"\n" for r in sorted(recs, key=sort_key))


def regen(
    name: str,
    *,
    check: bool = False,
    paths: Paths | None = None,
    base: Path = COLLECTOR_FIXTURES,
) -> bool:
    """Rewrite (or, with ``check``, compare) one golden file; return True if it was out of date."""
    paths = paths or Paths.for_root(ROOT)
    collector = get(name)
    directory = fixture_dir(name, base)
    snapshot = load_snapshot(directory / "snapshot", name)
    recs = parse_records(collector, snapshot, load_settings(collector, paths))
    target = directory / EXPECTED
    fresh = encode(recs)
    stale = not target.is_file() or target.read_bytes() != fresh
    if stale and not check:
        write_jsonl(recs, target)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.helpers.regen",
        description="Regenerate tests/fixtures/collectors/<name>/expected.jsonl from snapshot/.",
    )
    parser.add_argument("names", nargs="*", metavar="collector")
    parser.add_argument("--all", action="store_true", help="every discovered collector")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if stale")
    args = parser.parse_args(argv)
    if args.all == bool(args.names):
        parser.error("name at least one collector, or pass --all")
    known = discover()
    names = sorted(known) if args.all else args.names
    unknown = [n for n in names if n not in known]
    if unknown:
        parser.error(
            f"unknown collector(s) {', '.join(unknown)}; known: {', '.join(known) or 'none'}"
        )
    if not names:
        print("no collectors yet")
    stale = []
    for name in names:
        if regen(name, check=args.check):
            stale.append(name)
            verb = "out of date" if args.check else "rewritten"
            print(f"{fixture_dir(name).relative_to(ROOT) / EXPECTED}: {verb}")
        else:
            print(f"{fixture_dir(name).relative_to(ROOT) / EXPECTED}: up to date")
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
