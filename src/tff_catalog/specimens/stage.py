"""The "specimens" stage (design-m2 §3). Owner: agent A5.

1. For each ``preview_ok`` font, fetch ``font_file.url`` with the M1 fetcher
   into ``~/.cache/tff/fonts/<sha256>``. A font inside a release archive
   (``<archive>.zip#<member>``) is read out of it with ``fontfiles.read_zip_member``:
   only that member's bytes, by range.
2. Verify the sha256; a mismatch sets ``specimen_hash_mismatch`` and gives no image.
3. Render ``build/specimens/<id>.svg``.
4. Record ``preview {path, sha256}``.

The cache key is sha256(font) + sample + renderer version + uharfbuzz version,
so unchanged inputs skip rendering and a refresh only touches changed fonts.

Details:

- Input is ``build/catalog.json`` (stage "export"); output is
  ``build/stage/previews.json`` ({id: Preview}, one entry per ``preview_ok``
  font) for "export-site", plus the SVGs. SVGs of fonts without a preview are
  removed, so ``build/specimens/`` holds exactly the current previews and
  their index.
- A specimen over ``MAX_FILE_BYTES`` is drawn again with the name only. Any
  name-only specimen (budget or glyph coverage) is flagged
  ``specimen_name_only``; a font with no usable specimen, no ``font_file``, or a
  file that can't be had right now is flagged ``specimen_failed``, and every
  flag that gives no image carries its ``reason`` in words (stage "review"
  lists them).
- The render cache is ``build/specimens/index.json``: {id: {key, path, sha256,
  flags}}. It is committed with the SVGs it describes (they are committed
  because ``tff-site build`` is offline), so a fresh clone or a CI runner knows
  which inputs drew each committed file. The "sample" part of the key is every
  text the specimen may draw (family name, sample line, basic line). The
  font is still read and checked on a hit; only drawing is skipped.
- When the font can't be had (replay without the font cache, or a download
  that fails) but the index holds a specimen for the same inputs, that one is
  kept, so a replay from a fresh clone gives the committed bytes (M1 step 18)
  and a host that is down for a day doesn't delete a specimen.
- A hash mismatch and an unavailable file are never cached, so the next run
  tries again.
"""

import hashlib
import logging
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tff_catalog import fontfiles, jsonio, stageio
from tff_catalog.specimens import BASIC_SAMPLE, MAX_FILE_BYTES, RENDERER_VERSION, SAMPLE

if TYPE_CHECKING:
    from tff_catalog.fetch import Fetcher
    from tff_catalog.stages import StageContext

FONT_CACHE = Path("~/.cache/tff/fonts")
CATALOG_FILE = "catalog.json"  # under build/, from stage "export"
INDEX_FILE = "index.json"  # under build/specimens/: the render cache, committed

_SHA256 = re.compile(r"[0-9a-f]{64}")  # fullmatch: "$" would allow a final newline
_ID = re.compile(r"[a-z0-9-]+")
_NAME_ONLY = ("specimen_name_only",)
# No exception text: a reason may reach review.md, which must not depend on local paths.
UNAVAILABLE = "the font file could not be had (a failed download, or no font cache in a replay)"


class FontUnavailable(RuntimeError):
    """The font file is not in the cache and there is no fetcher (replay), or the release
    archive it lives in holds no readable member of that name."""


@dataclass(frozen=True, slots=True)
class Preview:
    """One font's specimen, ``build/stage/previews.json`` ({id: Preview})."""

    path: str | None  # "specimens/<id>.svg", None when there is no image
    sha256: str | None
    flags: tuple[str, ...] = ()  # specimen_failed, specimen_name_only, specimen_hash_mismatch
    reason: str | None = None  # why there is no image, in words (None when there is one)


def cache_key(font_sha256: str, sample: str, renderer_version: int, hb_version: str) -> str:
    """The render-cache key (hex sha256 of the joined inputs)."""
    joined = jsonio.canonical_bytes([font_sha256, sample, renderer_version, hb_version])
    return hashlib.sha256(joined).hexdigest()


def hb_version() -> str:
    """The shaping engine's versions, as they go in the cache key."""
    import uharfbuzz

    return f"uharfbuzz {uharfbuzz.__version__}; HarfBuzz {uharfbuzz.version_string()}"


def sample_texts(family: str) -> str:
    """Every text a specimen of ``family`` may draw: the "sample" part of the cache key."""
    return "\n".join((family, SAMPLE, BASIC_SAMPLE))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_font(url: str, sha256: str, fetcher: Fetcher | None, cache_dir: Path) -> Path | None:
    """The cached font file, fetched if needed; None on a hash mismatch.

    The cache file is named by the expected sha256 and checked on every call,
    so a damaged entry is fetched again. A download is streamed to a temporary
    file beside it and kept only when its hash matches; for a member of a zip
    archive the hash is the member's. Raises ``FontUnavailable`` when the file
    isn't cached and ``fetcher`` is None, or the archive has no readable member
    of that name, and lets the fetcher's own errors (``fetch.FetchError``) through.
    """
    if not _SHA256.fullmatch(sha256):
        return None  # not a sha256 any file could match
    cache_dir = cache_dir.expanduser()
    target = cache_dir / sha256
    if target.is_file():
        if file_sha256(target) == sha256:
            return target
        target.unlink()
    if fetcher is None:
        raise FontUnavailable(f"{url} is not in {cache_dir} and there is no network (replay)")
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=cache_dir, prefix=".part-")
    os.close(fd)
    part = Path(name)
    try:
        if fontfiles.split_member(url) is not None:
            try:
                part.write_bytes(fontfiles.read_zip_member(url, fetcher))
            except fontfiles.FontFileError as exc:
                raise FontUnavailable(str(exc)) from exc
        else:
            fetcher.get(url, to=part)
        if file_sha256(part) != sha256:
            return None
        part.replace(target)
        return target
    finally:
        part.unlink(missing_ok=True)


def render_one(font_id: str, family: str, font: bytes, out_dir: Path) -> Preview:
    """Draw one specimen into ``out_dir/<id>.svg`` within the budget; flag it if that fails."""
    from tff_catalog.specimens.render import render

    spec = render(font, family, SAMPLE, BASIC_SAMPLE)
    if spec is not None and len(spec.svg) > MAX_FILE_BYTES and spec.line != "name":
        spec = render(font, family, SAMPLE, BASIC_SAMPLE, name_only=True)
    if spec is None:
        return _failed("the font draws none of the sample texts, not even its name")
    if len(spec.svg) > MAX_FILE_BYTES:
        return _failed(f"over {MAX_FILE_BYTES // 1000} KB even with the name only")
    target = out_dir / f"{font_id}.svg"
    if not target.is_file() or target.read_bytes() != spec.svg:
        _write_atomic(target, spec.svg)
    flags = _NAME_ONLY if spec.line == "name" else ()
    return Preview(f"specimens/{font_id}.svg", hashlib.sha256(spec.svg).hexdigest(), flags)


def render_fonts(
    fonts: Iterable[Mapping[str, Any]],
    out_dir: Path,
    *,
    fetcher: Fetcher | None,
    cache_dir: Path,
    index_path: Path | None,
    log: logging.Logger,
) -> dict[str, Preview]:
    """Previews for every ``preview_ok`` font in catalog rows ``fonts`` (the stage's work)."""
    index = _load_index(index_path)
    engine = hb_version()
    previews: dict[str, Preview] = {}
    new_index: dict[str, dict[str, Any]] = {}
    counts = {"drawn": 0, "cached": 0}
    for font in sorted(fonts, key=lambda f: f["id"]):
        if not font.get("preview_ok"):
            continue
        font_id, family = font["id"], font["family"]
        if not _ID.fullmatch(font_id):
            raise ValueError(f"font id {font_id!r} can't name a specimen file")
        ref = font.get("font_file")
        if not ref:
            log.warning("%s: preview_ok but no font_file; flagged specimen_failed", font_id)
            previews[font_id] = _failed("no font file to draw from")
            continue
        key = cache_key(ref["sha256"], sample_texts(family), RENDERER_VERSION, engine)
        hit = _hit(index.get(font_id), key, font_id, out_dir)
        try:
            path = fetch_font(ref["url"], ref["sha256"], fetcher, cache_dir)
        except NotImplementedError:
            raise
        except (RuntimeError, OSError) as exc:
            if hit is not None:
                log.warning("%s: font unavailable (%s); kept the same-input specimen", font_id, exc)
                previews[font_id] = hit
                new_index[font_id] = {"key": key, **stageio.encode(hit)}
            else:
                log.warning("%s: font unavailable (%s); flagged specimen_failed", font_id, exc)
                previews[font_id] = _failed(UNAVAILABLE)
            continue
        if path is None:
            log.warning(
                "%s: %s doesn't match sha256 %s; flagged specimen_hash_mismatch",
                font_id,
                ref["url"],
                ref["sha256"],
            )
            previews[font_id] = Preview(
                None, None, ("specimen_hash_mismatch",), "the font file does not match its sha256"
            )
            continue
        if hit is not None:
            preview = hit
            counts["cached"] += 1
        else:
            try:
                preview = render_one(font_id, family, path.read_bytes(), out_dir)
            except Exception:  # one odd upstream file must not stop the refresh
                log.exception("%s: rendering raised; flagged specimen_failed", font_id)
                previews[font_id] = _failed("the renderer raised an error on this font")
                continue  # not cached: a fixed renderer tries again
            counts["drawn"] += 1
            if preview.flags:
                why = f" ({preview.reason})" if preview.reason else ""
                log.info("%s: %s%s", font_id, ", ".join(preview.flags), why)
        previews[font_id] = preview
        new_index[font_id] = {"key": key, **stageio.encode(preview)}
    removed = _prune(out_dir, {f"{i}.svg" for i, p in previews.items() if p.path})
    if index_path is not None:
        jsonio.dump(new_index, index_path)
    log.info(
        "specimens: %d previews (%d drawn, %d unchanged), %d removed",
        sum(1 for p in previews.values() if p.path),
        counts["drawn"],
        counts["cached"],
        removed,
    )
    return previews


def run(ctx: StageContext) -> None:
    """Stage "specimens": render every preview and write ``build/stage/previews.json``."""
    catalog = ctx.paths.build / CATALOG_FILE
    if not catalog.is_file():
        raise FileNotFoundError(f"{catalog} is missing: run stage 'export' first")
    previews = render_fonts(
        jsonio.load(catalog)["fonts"],
        ctx.paths.specimens,
        fetcher=ctx.fetcher,
        cache_dir=FONT_CACHE.expanduser(),
        index_path=ctx.paths.specimens / INDEX_FILE,
        log=ctx.log,
    )
    stageio.dump_stage(ctx.paths, "previews", previews)


def _failed(reason: str) -> Preview:
    """No image, flagged ``specimen_failed``, with why."""
    return Preview(None, None, ("specimen_failed",), reason)


def _hit(entry: object, key: str, font_id: str, out_dir: Path) -> Preview | None:
    """The cached preview when its key matches and its SVG is still on disk unchanged.

    The index is a committed file, so anything in it may have been edited by hand:
    an entry that isn't exactly what this stage writes for ``font_id`` is a miss.
    """
    if not isinstance(entry, Mapping) or entry.get("key") != key:
        return None
    try:
        preview = stageio.decode(Preview, {k: v for k, v in entry.items() if k != "key"})
    except stageio.StageFileError, TypeError:
        return None
    if preview.path is None:
        return preview if preview.flags == ("specimen_failed",) else None
    if preview.path != f"specimens/{font_id}.svg" or preview.flags not in ((), _NAME_ONLY):
        return None
    svg = out_dir / f"{font_id}.svg"
    if svg.is_file() and hashlib.sha256(svg.read_bytes()).hexdigest() == preview.sha256:
        return preview
    return None


def _load_index(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = jsonio.load(path)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _prune(out_dir: Path, keep: set[str]) -> int:
    if not out_dir.is_dir():
        return 0
    stale = [p for p in out_dir.glob("*.svg") if p.name not in keep]
    for p in stale:
        p.unlink()
    return len(stale)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        Path(name).chmod(0o644)  # mkstemp makes 0600
        Path(name).replace(path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
