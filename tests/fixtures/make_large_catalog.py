"""Write a deterministic, synthetic 540-font ``catalog-site.json`` for the performance tests.

The real catalog will hold about 540 fonts (Milestone 2 design §0); the sample holds 40. This
generator scales the sample up to the real size so the list page, the list index and the
refilter timings can be measured before Milestone 1's data exists. Nothing here is real data:
ids are ``large-<category>-<n>``, families ``Large <Category> <n>``, and the catalog carries
``"synthetic": true``.

- **Deterministic:** the same ``--fonts`` and ``--seed`` always give the same bytes (a seeded
  numpy RNG and sorted-key JSON), so two runs can be compared and a failure reproduced.
- **Valid:** the output passes ``tff_site.data.validate`` (schema and cross-references).
- **Shaped like the sample:** font ``k`` copies the traits of sample font ``k % 40``
  (category, monospace, formats, Latin coverage, license, systems, aliases, flags and
  per-source states), so every filter, badge and fallback text keeps its share. Every view
  gets a fresh ranking: an exact top 100, then the bands up to "501+", and a few unranked
  fonts with a reason (Coding holds only the monospace fonts).
- **Specimens:** every font with ``preview_ok`` gets ``specimens/<id>.svg`` next to the
  catalog, copied in turn from the committed sample specimens
  (``tests/fixtures/specimens/*.svg``) with its real sha256, so the page loads a realistic
  number of masks. ``font_file`` is null: builds need no font cache.

Usage::

    uv run python tests/fixtures/make_large_catalog.py OUT_DIR [--fonts 540] [--seed 2026]
    uv run tff-site build --data OUT_DIR/catalog-site.json --out /tmp/large-site

or ``write(out_dir)`` from a test. The output is not committed.
"""

import argparse
import copy
import hashlib
from collections.abc import Mapping
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from tff_catalog import jsonio

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "catalog-site.sample.json"
SPECIMENS = HERE / "specimens"
DEFAULT_FONTS = 540
DEFAULT_SEED = 2026
TOP_N = 100
CATALOG_NAME = "catalog-site.json"
# Share of each view's universe left unranked (with a reason), as in the sample.
UNRANKED_SHARE = 0.06
# Views where a font flagged too_new stays unranked as "too new to rank" (the sample's shape:
# ranked overall and in projects, too new elsewhere).
TOO_NEW_VIEWS = frozenset({"desktop_chosen", "desktop_installed", "coding", "dev_apps"})
CATEGORY_WORD = {
    "sans-serif": "Sans",
    "serif": "Serif",
    "display": "Display",
    "handwriting": "Hand",
    "monospace": "Mono",
}
SPECIMEN_FLAGS = frozenset({"specimen_failed", "specimen_name_only", "specimen_hash_mismatch"})


def make_catalog(
    fonts: int = DEFAULT_FONTS, seed: int = DEFAULT_SEED
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return ``(catalog, specimens)``: the document and ``{"specimens/<id>.svg": bytes}``."""
    if fonts < 1:
        raise ValueError("fonts must be at least 1")
    sample = jsonio.load(SAMPLE)
    rng = np.random.default_rng(seed)
    svgs = [path.read_bytes() for path in sorted(SPECIMENS.glob("*.svg"))]
    templates = sample["fonts"]
    views = [v["key"] for v in sample["views"] if v["available"]]

    made: list[dict[str, Any]] = []
    specimens: dict[str, bytes] = {}
    for k in range(fonts):
        font = _font_from(templates[k % len(templates)], k, views)
        if font["preview_ok"] and svgs:
            blob = svgs[k % len(svgs)]
            path = f"specimens/{font['id']}.svg"
            font["preview"] = {"path": path, "sha256": hashlib.sha256(blob).hexdigest()}
            font["flags"] = [f for f in font["flags"] if f not in SPECIMEN_FLAGS]
            specimens[path] = blob
        made.append(font)

    for key in views:
        _rank_view(made, key, sample["bands"], rng)

    doc = {name: copy.deepcopy(value) for name, value in sample.items() if name != "fonts"}
    doc["synthetic"] = True
    doc["fonts"] = made
    return doc, specimens


def write(out_dir: Path, fonts: int = DEFAULT_FONTS, seed: int = DEFAULT_SEED) -> Path:
    """Write the catalog and its specimens under ``out_dir``; return the catalog's path."""
    out_dir = Path(out_dir)
    doc, specimens = make_catalog(fonts, seed)
    for rel, blob in specimens.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    target = out_dir / CATALOG_NAME
    jsonio.atomic_write(target, jsonio.pretty_bytes(doc))
    return target


def _font_from(template: Mapping[str, Any], k: int, views: list[str]) -> dict[str, Any]:
    """Font ``k``: the template's traits under a new id, family and aliases; ranks cleared."""
    font = copy.deepcopy(dict(template))
    word = CATEGORY_WORD[font["category"]]
    number = f"{k + 1:03d}"
    font["id"] = f"large-{word.lower()}-{number}"
    font["family"] = f"Large {word} {number}"
    renamed = []
    for i, alias in enumerate(font["aliases"]):
        suffix = ("Variable", "Classic", "Nerd Font", "Legacy")[i % 4]
        renamed.append({"name": f"Large {word} {number} {suffix}", "relation": alias["relation"]})
    font["aliases"] = renamed
    font["preview"] = None
    font["font_file"] = None
    in_views = [v for v in views if v != "coding" or font["is_monospace"]]
    font["ranks"] = dict.fromkeys(in_views)
    for entry in font["sources"].values():
        entry["abstains_in"] = [v for v in entry["abstains_in"] if v in in_views]
    return font


def _rank_view(
    fonts: list[dict[str, Any]], key: str, bands: list[Mapping[str, Any]], rng: np.random.Generator
) -> None:
    """Fill ``ranks[key]`` for every font in the view's universe."""
    universe = [f for f in fonts if key in f["ranks"]]
    too_new = [f for f in universe if "too_new" in f["flags"] and key in TOO_NEW_VIEWS]
    rest = [f for f in universe if f not in too_new]
    # A noisy copy of the catalog order, so the views differ but stay related.
    position_of = {font["id"]: k for k, font in enumerate(fonts)}
    noise = len(fonts) / 8
    jitter = rng.normal(0.0, noise, size=len(rest))
    scored = [
        f
        for _, f in sorted(
            zip(jitter, rest, strict=True), key=lambda p: position_of[p[1]["id"]] + p[0]
        )
    ]
    unranked_count = round(len(scored) * UNRANKED_SHARE)
    ranked = scored[: len(scored) - unranked_count]
    unranked = scored[len(scored) - unranked_count :]

    for position, font in enumerate(ranked, start=1):
        width = max(1, position // 4)
        tier = "A" if position <= 30 else "B" if position <= 150 else "C"
        font["ranks"][key] = entry = {
            "rank": position if position <= TOP_N else None,
            "band": None if position <= TOP_N else _band_of(position, bands),
            "order": position,
            "tier": tier,
            "range": [max(1, position - width), position + width],
            # A fused score that falls with the position, on the z scale, as the engine's.
            "score": round(NormalDist().inv_cdf(1 - (position - 0.5) / (2 * len(ranked))), 6),
            "gate_held": False,
            "unranked": None,
        }
        entry["previous_score"] = entry["score"]  # the bootstrap: nothing has moved
    for font in unranked:
        deliberate = key == "desktop_chosen" and (font["preinstalled_on"] or font["pulled_in_by"])
        font["ranks"][key] = _unranked("no_deliberate_evidence" if deliberate else "no_evidence")
    for font in too_new:
        font["ranks"][key] = _unranked("too_new")


def _unranked(reason: str) -> dict[str, Any]:
    return {
        "rank": None,
        "band": None,
        "order": None,
        "tier": None,
        "range": None,
        "score": None,
        "previous_score": None,
        "gate_held": False,
        "unranked": reason,
    }


def _band_of(order: int, bands: list[Mapping[str, Any]]) -> str:
    for band in bands:
        if order >= band["from"] and (band["to"] is None or order <= band["to"]):
            return band["label"]
    raise ValueError(f"no band holds order {order}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out_dir", type=Path, help="directory for catalog-site.json and specimens/")
    parser.add_argument("--fonts", type=int, default=DEFAULT_FONTS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    path = write(args.out_dir, args.fonts, args.seed)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
