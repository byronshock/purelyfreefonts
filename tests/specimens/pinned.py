"""The five pinned real OFL fonts (``tests/fixtures/specimen-fonts.toml``) and their sample ids.

``font_bytes`` reads a font from the cache ``~/.cache/tff/fonts/<sha256>``,
downloading it first when it is missing (only tests marked ``network`` may).
The files are checked against the pinned sha256 and size and never committed.
"""

import hashlib
import json
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
PINS_FILE = FIXTURES / "specimen-fonts.toml"
SAMPLE_FILE = FIXTURES / "catalog-site.sample.json"
EXPECTED_DIR = FIXTURES / "specimens"
FONT_CACHE = Path.home() / ".cache" / "tff" / "fonts"


@dataclass(frozen=True, slots=True)
class Pin:
    key: str
    url: str
    sha256: str
    size: int
    latin: str  # "basic" or "extended"
    axes: tuple[str, ...]
    sample_id: str
    family: str  # the sample catalog's (synthetic) family name for sample_id


def pins() -> list[Pin]:
    sample = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    families = {f["id"]: f["family"] for f in sample["fonts"]}
    table = tomllib.loads(PINS_FILE.read_text(encoding="utf-8"))["font"]
    return [
        Pin(
            key=p["key"],
            url=p["url"],
            sha256=p["sha256"],
            size=p["size"],
            latin=p["latin"],
            axes=tuple(p["axes"]),
            sample_id=p["sample_id"],
            family=families[p["sample_id"]],
        )
        for p in table
    ]


def cached(pin: Pin, cache_dir: Path = FONT_CACHE) -> bytes | None:
    """The pinned file from the cache, or None when it is missing or damaged."""
    path = cache_dir / pin.sha256
    if not path.is_file():
        return None
    data = path.read_bytes()
    if len(data) != pin.size or hashlib.sha256(data).hexdigest() != pin.sha256:
        return None
    return data


def download(pin: Pin, cache_dir: Path = FONT_CACHE) -> bytes:
    """Fetch the pinned file into the cache and return it; raises on any mismatch."""
    import httpx

    response = httpx.get(pin.url, follow_redirects=True, timeout=60.0)
    response.raise_for_status()
    data = response.content
    if len(data) != pin.size or hashlib.sha256(data).hexdigest() != pin.sha256:
        raise ValueError(f"{pin.url}: size or sha256 differs from the pin")
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=cache_dir, prefix=".part-")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    Path(name).replace(cache_dir / pin.sha256)
    return data
