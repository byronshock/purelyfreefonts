"""Load and validate ``catalog-site.json``, and derive the two page payloads from it.

Validation has two layers, and ``tff-site validate`` and ``tff-site build`` run both:

- ``schema_errors``: JSON Schema 2020-12, ``schemas/catalog-site.schema.json``.
- ``semantic_errors``: the cross-references a schema can't express: unique ids, every rank
  key in ``views``, bands that tile 101 upwards, ``rank == order`` inside the top 100, band
  labels that match ``order``, license classes, systems and sources that exist, one source
  entry per source, ranks withheld for sources whose terms forbid them, view universes
  (every font in every available view; ``coding`` holds exactly the monospace fonts), and
  links a visitor follows that go to a page, never to a download (M1 step 14).

The payloads (``list_index`` and ``details``) are the formats in ``site/CONTRACT.md``
(sections 7 and 8). They are pure functions of the document, so the same catalog always gives
the same bytes once serialised with ``jsonio.canonical_bytes``.
"""

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tff_catalog.keys import search_key

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "catalog-site.schema.json"
SCHEMA_VERSION = "1.0.0-draft"

# Versioned rank keys (methodology §7), in the rank selector's order.
RANK_KEYS = (
    "overall",
    "desktop_chosen",
    "desktop_installed",
    "project",
    "coding",
    "dev_apps",
    "rising",
)
# The only view whose universe is a subset of the catalog: monospace fonts.
MONOSPACE_VIEW = "coding"
# Linux sources never abstain here (D8): it counts every install.
NO_ABSTAIN_VIEW = "desktop_installed"
TOP_N = 100

# Page wording for the enums. The list index carries these, so the script holds no copy.
UNRANKED_LABELS = {
    "no_deliberate_evidence": "no evidence of deliberate installs",
    "no_evidence": "no evidence in this rank",
    "too_new": "too new to rank",
}
STATE_LABELS = {
    "observed": "observed",
    "censored": "below the floor",
    "not_covered": "not covered",
    "too_new": "too new",
}
SPECIMEN_FLAGS = frozenset({"specimen_failed", "specimen_name_only", "specimen_hash_mismatch"})
# Written by the sample until the specimens are rendered: the build treats it as no preview.
PLACEHOLDER_SHA256 = "0" * 64

# The rank the list page shows first and server-renders (M2-D1).
DEFAULT_VIEW = "overall"
# Categories in schema order (the list index's ``cats``), with their page labels.
CATEGORY_LABELS = {
    "sans-serif": "Sans serif",
    "serif": "Serif",
    "display": "Display",
    "handwriting": "Handwriting",
    "monospace": "Monospace",
}
CATEGORIES = tuple(CATEGORY_LABELS)
# Operating-system families for "Hide fonts that come with …", with their page labels.
OS_LABELS = {"windows": "Windows", "macos": "macOS", "linux": "Linux", "android": "Android"}

# List-index ``bits`` (site/CONTRACT.md section 7).
BIT_MONOSPACE = 1
BIT_VARIABLE = 2
BIT_LIMITED = 4
BIT_ATTRIBUTION = 8
# 16 was "not redistributable" until Rule 3 of 2026-09-30 let in only redistributable fonts.
BIT_SPECIMEN = 32
BIT_TYPE_OWN = 64
OS_BITS = {"windows": 128, "macos": 256, "linux": 512, "android": 1024}  # os "app": no bit
BIT_NEW = 2048
BIT_PULLED = 4096
BIT_NERD = 8192  # a Nerd Font build (links.nerd): the "NF" marker and filter (TASK-2)

LIST_FORMAT = 2  # 2 (2026-09-30): site categories; no lics/lic columns
DETAILS_FORMAT = 1
TIER_UNRANKED = "-"
TIER_OUTSIDE = "."

# Where people report problems (M2-D10): the license issue form, and the email fallback.
REPO_URL = "https://github.com/byronshock/trulyfreefonts"
FEEDBACK_EMAIL = "admin@trulyfreefonts.com"
REPORT_ISSUE_URL = f"{REPO_URL}/issues/new?template=license.yml"


class CatalogError(ValueError):
    """The catalog failed validation. ``errors`` holds one line per problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s); first: {errors[0] if errors else '-'}")


@dataclass(frozen=True, slots=True)
class Validated:
    """What ``tff-site validate`` reports for a valid file."""

    version: str
    fonts: int


def load(path: Path) -> dict[str, Any]:
    """Read a catalog-site JSON file (UTF-8) without validating it (see ``loads``)."""
    return loads(Path(path).read_bytes())


def loads(blob: bytes) -> dict[str, Any]:
    """Parse catalog-site JSON bytes (UTF-8) without validating the document.

    Stricter than ``json.loads``: a key repeated in one object, ``NaN`` and ``Infinity`` are
    refused (``ValueError``), because the schema would see only one of the repeated values.
    """
    return json.loads(blob.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_no_nan)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out = dict(pairs)
    if len(out) != len(pairs):
        dupes = sorted(k for k, n in Counter(k for k, _ in pairs).items() if n > 1)
        raise ValueError(f"key repeated in one object: {dupes}")
    return out


def _no_nan(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def schema() -> dict[str, Any]:
    """Return the catalog-site JSON Schema."""
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def schema_errors(doc: Any) -> list[str]:
    """Return JSON Schema violations as ``"<json path>: <message>"`` lines, in path order."""
    # Imported here so `tff-site --help` stays fast.
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(schema())
    found = sorted(validator.iter_errors(doc), key=lambda e: (e.json_path, e.message))
    return [f"{e.json_path}: {e.message}" for e in found]


def semantic_errors(doc: Mapping[str, Any]) -> list[str]:
    """Return cross-reference problems in a schema-valid document (see the module docstring)."""
    errors: list[str] = []
    views = doc["views"]
    view_keys = [v["key"] for v in views]
    errors += [f"views: {k!r} listed twice" for k in _dupes(view_keys)]
    errors += [f"views: rank key {k!r} missing" for k in RANK_KEYS if k not in view_keys]
    available = [v["key"] for v in views if v["available"]]
    errors += _band_errors(doc["bands"])
    band_labels = {b["label"] for b in doc["bands"]}

    source_ids = [s["id"] for s in doc["sources"]]
    errors += [f"sources: id {k!r} listed twice" for k in _dupes(source_ids)]
    publishes = {s["id"]: s["publish_rank"] for s in doc["sources"]}
    survey = {s["id"]: s["survey"] for s in doc["sources"]}
    system_os = {s["id"]: s["os"] for s in doc["systems"]}
    errors += [f"systems: id {k!r} listed twice" for k in _dupes(s["id"] for s in doc["systems"])]
    classes = {c["id"] for c in doc["license_classes"]}
    errors += [
        f"license_classes: id {k!r} listed twice"
        for k in _dupes(c["id"] for c in doc["license_classes"])
    ]

    fonts = doc["fonts"]
    errors += [f"fonts: id {k!r} listed twice" for k in _dupes(f["id"] for f in fonts)]
    orders: dict[str, Counter[int]] = {k: Counter() for k in view_keys}
    for i, font in enumerate(fonts):
        where = f"fonts[{i}] {font['id']}"
        errors += [f"{where}: {msg}" for msg in _font_errors(font, classes, system_os)]
        for key in available:
            present = key in font["ranks"]
            wanted = font["is_monospace"] if key == MONOSPACE_VIEW else True
            if present != wanted:
                errors.append(f"{where}: ranks.{key} {'unexpected' if present else 'missing'}")
        for key, entry in font["ranks"].items():
            errors += [f"{where}: ranks.{key}: {msg}" for msg in _rank_errors(entry, doc["bands"])]
            if entry["band"] is not None and entry["band"] not in band_labels:
                errors.append(f"{where}: ranks.{key}: unknown band {entry['band']!r}")
            if entry["order"] is not None and key in orders:
                orders[key][entry["order"]] += 1
        if set(font["sources"]) != set(source_ids):
            missing = sorted(set(source_ids) - set(font["sources"]))
            extra = sorted(set(font["sources"]) - set(source_ids))
            errors.append(f"{where}: sources: missing {missing}, unknown {extra}")
        for sid, entry in font["sources"].items():
            if entry["rank_in_source"] is not None and not publishes.get(sid, False):
                errors.append(f"{where}: sources.{sid}: rank_in_source for an unpublished source")
            if entry["abstains_in"]:
                if survey.get(sid) != "desktop":
                    errors.append(f"{where}: sources.{sid}: only desktop sources abstain")
                if NO_ABSTAIN_VIEW in entry["abstains_in"]:
                    errors.append(f"{where}: sources.{sid}: abstains in {NO_ABSTAIN_VIEW}")
                stray = sorted(set(entry["abstains_in"]) - set(font["ranks"]))
                if stray:
                    errors.append(f"{where}: sources.{sid}: abstains in views without it {stray}")
    for key, counts in orders.items():
        errors += [f"ranks.{key}: order {o} used by {n} fonts" for o, n in counts.items() if n > 1]
    return errors


def validate(doc: Any) -> Validated:
    """Run both layers; raise ``CatalogError`` listing every problem, or return the summary."""
    errors = schema_errors(doc)
    if not errors:
        errors = semantic_errors(doc)
    if errors:
        raise CatalogError(errors)
    return Validated(version=doc["schema_version"], fonts=len(doc["fonts"]))


def validate_file(path: Path) -> Validated:
    """``validate(load(path))``; a file that isn't JSON raises ``CatalogError`` too."""
    try:
        doc = load(path)
    except (OSError, ValueError) as exc:  # ValueError: bad UTF-8 or JSON (see ``loads``)
        raise CatalogError([f"{path}: {exc}"]) from exc
    return validate(doc)


def band_of(order: int, bands: list[Mapping[str, Any]]) -> str | None:
    """Return the label of the band holding ``order``, or None inside the exact top 100."""
    for band in bands:
        if order >= band["from"] and (band["to"] is None or order <= band["to"]):
            return band["label"]
    return None


def list_index(doc: Mapping[str, Any], *, commit: str) -> dict[str, Any]:
    """Return the list-index payload (``/assets/list.<h>.json``; format in site/CONTRACT.md).

    Font index ``i`` is the ``i``-th server-rendered row: Overall order, then fonts unranked
    in Overall by Python ``str.casefold`` of the family, then id.

    Bit 32 (a specimen) is set for a ``preview`` whose sha256 is not the placeholder, and bit
    64 ("Type your own text") for a ``font_file``: a build without font files passes a
    document whose ``font_file`` values are null.
    """
    fonts = server_order(doc)
    system_os = {s["id"]: s["os"] for s in doc["systems"]}
    cat_index = {cat: i for i, cat in enumerate(CATEGORIES)}
    bands = [b["label"] for b in doc["bands"]]
    return {
        "v": LIST_FORMAT,
        "commit": commit,
        "run_date": doc["run"]["date"],
        "n": len(fonts),
        "ids": [f["id"] for f in fonts],
        "cats": list(CATEGORIES),
        "cat": [cat_index[site_category(f)] for f in fonts],
        "bits": [font_bits(f, system_os) for f in fonts],
        "keys": [search_keys(f) for f in fonts],
        "by_name": sorted(range(len(fonts)), key=lambda i: name_order(fonts[i])),
        "views": [dict(v) for v in doc["views"]],
        "bands": bands,
        "why_labels": list(UNRANKED_LABELS.values()),
        "r": {
            view["key"]: _view_columns(fonts, view["key"], bands)
            for view in doc["views"]
            if view["available"]
        },
    }


def details(doc: Mapping[str, Any], *, font_assets: Mapping[str, str]) -> dict[str, Any]:
    """Return the details payload (``/assets/details.<h>.json``; format in site/CONTRACT.md).

    ``font_assets`` maps font id to the hashed ``/assets/fonts/…`` URL of its font file.
    A font without an entry there gets ``type_own: null``.
    """
    fonts = {f["id"]: f for f in doc["fonts"]}
    stray = sorted(k for k in font_assets if fonts.get(k, {}).get("font_file") is None)
    if stray:
        raise ValueError(f"font_assets names fonts without a font_file: {stray}")
    out: dict[str, Any] = {}
    for font_id, font in fonts.items():
        entry = {k: v for k, v in font.items() if k not in ("preview", "font_file")}
        url = font_assets.get(font_id)
        entry["type_own"] = None if url is None else {"url": url, "size": font["font_file"]["size"]}
        out[font_id] = entry
    return {
        "v": DETAILS_FORMAT,
        "run_date": doc["run"]["date"],
        "views": doc["views"],
        "bands": doc["bands"],
        "tiers": doc["tiers"],
        "sources": doc["sources"],
        "systems": doc["systems"],
        "license_classes": doc["license_classes"],
        "nerd": doc["nerd"],
        "state_labels": dict(STATE_LABELS),
        "why_labels": dict(UNRANKED_LABELS),
        "report": {"issue_url": REPORT_ISSUE_URL, "email": FEEDBACK_EMAIL},
        "fonts": out,
    }


def server_order(doc: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the fonts in server-rendered order (see ``list_index``)."""
    ranked: list[tuple[int, Mapping[str, Any]]] = []
    unranked: list[Mapping[str, Any]] = []
    for font in doc["fonts"]:
        entry = font["ranks"].get(DEFAULT_VIEW)
        if entry is not None and entry["order"] is not None:
            ranked.append((entry["order"], font))
        else:
            unranked.append(font)
    ranked.sort(key=lambda pair: pair[0])
    return [font for _, font in ranked] + sorted(unranked, key=name_order)


def site_category(font: Mapping[str, Any]) -> str:
    """The category the site files a font under: "monospace" for every monospaced font, else
    the catalog's category (owner ruling of 2026-09-30, ``monospace_category``). So Category
    "Monospace" lists the fonts the Coding rank orders, and the other categories list
    proportional fonts only. The catalog's own ``category`` is unchanged."""
    return "monospace" if font["is_monospace"] else font["category"]


def name_order(font: Mapping[str, Any]) -> tuple[str, str]:
    """Sort key for "by name": Python ``str.casefold`` of the family, then the id."""
    return (font["family"].casefold(), font["id"])


def has_specimen(font: Mapping[str, Any]) -> bool:
    """True when the font has a rendered specimen (a preview that isn't the placeholder)."""
    preview = font["preview"]
    return preview is not None and preview["sha256"] != PLACEHOLDER_SHA256


def font_bits(font: Mapping[str, Any], system_os: Mapping[str, str]) -> int:
    """Return the list-index ``bits`` of one font (site/CONTRACT.md section 7)."""
    bits = 0
    if font["is_monospace"]:
        bits |= BIT_MONOSPACE
    if font["formats"]["variable"]:
        bits |= BIT_VARIABLE
    if font["latin"]["coverage"] == "basic":
        bits |= BIT_LIMITED
    if font["license"]["attribution_required"]:
        bits |= BIT_ATTRIBUTION
    if has_specimen(font):
        bits |= BIT_SPECIMEN
    if font["preview_ok"] and font["font_file"] is not None:
        bits |= BIT_TYPE_OWN
    for item in font["preinstalled_on"]:
        bits |= OS_BITS.get(system_os[item["system"]], 0)
    if "too_new" in font["flags"]:
        bits |= BIT_NEW
    if font["pulled_in_by"]:
        bits |= BIT_PULLED
    if font["links"]["nerd"] is not None:
        bits |= BIT_NERD
    return bits


def search_keys(font: Mapping[str, Any]) -> str:
    """``search_key`` of the family, then of each alias in catalog order, joined by ``|``."""
    names = [font["family"], *(alias["name"] for alias in font["aliases"])]
    return "|".join(search_key(name) for name in names)


def destination_name(link: Mapping[str, Any]) -> str:
    """Name a link's destination (site/CONTRACT.md section 1, "Destination names")."""
    if link.get("label"):
        return link["label"]
    return url_destination(link["url"])


def url_destination(url: str) -> str:
    """Name where ``url`` goes, from the URL alone (rules 2 to 4 of "Destination names")."""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    segments = [s for s in parts.path.split("/") if s]
    if host == "github.com" and len(segments) >= 2:
        return f"GitHub: {segments[0]}/{segments[1].removesuffix('.git')}"
    if host == "fonts.google.com":
        return "Google Fonts"
    return host


def nerd_link_text(link: Mapping[str, Any]) -> str:
    """The text of a Nerd Font build link (site/CONTRACT.md section 1): the build's name,
    then where the link goes, "SauceCodePro Nerd Font (GitHub: ryanoasis/nerd-fonts)"."""
    return f"{link['label']} ({url_destination(link['url'])})"


def _view_columns(fonts: list[Mapping[str, Any]], key: str, bands: list[str]) -> dict[str, Any]:
    band_index = {label: i for i, label in enumerate(bands)}
    why_index = {reason: i for i, reason in enumerate(UNRANKED_LABELS)}
    entries = [font["ranks"].get(key) for font in fonts]
    ranked = sorted((e["order"], i) for i, e in enumerate(entries) if e and e["order"] is not None)
    tiers = []
    for entry in entries:
        if entry is None:
            tiers.append(TIER_OUTSIDE)
        elif entry["order"] is None:
            tiers.append(TIER_UNRANKED)
        else:
            tiers.append(entry["tier"])
    return {
        "order": [i for _, i in ranked],
        "top": [(e["rank"] or 0) if e else 0 for e in entries],
        "band": [band_index[e["band"]] if e and e["band"] is not None else -1 for e in entries],
        "tier": "".join(tiers),
        "why": [why_index[e["unranked"]] if e and e["unranked"] else -1 for e in entries],
    }


def _dupes(items: Iterable[str]) -> list[str]:
    return sorted(k for k, n in Counter(items).items() if n > 1)


def _band_errors(bands: list[Mapping[str, Any]]) -> list[str]:
    errors = [f"bands: label {k!r} listed twice" for k in _dupes(b["label"] for b in bands)]
    if bands[0]["from"] != TOP_N + 1:
        errors.append(f"bands: the first band must start at {TOP_N + 1}")
    for prev, band in pairwise(bands):
        if prev["to"] is None or band["from"] != prev["to"] + 1:
            errors.append(f"bands: {band['label']!r} doesn't follow {prev['label']!r}")
    errors += [
        f"bands: {band['label']!r} ends before it starts"
        for band in bands
        if band["to"] is not None and band["to"] < band["from"]
    ]
    if bands[-1]["to"] is not None:
        errors.append("bands: the last band must be open-ended (to: null)")
    return errors


def _rank_errors(entry: Mapping[str, Any], bands: list[Mapping[str, Any]]) -> list[str]:
    order = entry["order"]
    if order is None:
        return []
    errors = []
    if entry["rank"] is not None and entry["rank"] != order:
        errors.append(f"rank {entry['rank']} != order {order}")
    if (entry["rank"] is None) != (order > TOP_N):
        errors.append(f"order {order} needs {'a band' if order > TOP_N else 'an exact rank'}")
    if entry["band"] is not None and entry["band"] != band_of(order, bands):
        errors.append(f"band {entry['band']!r} doesn't hold order {order}")
    low, high = entry["range"]
    if low > high:
        errors.append(f"range {entry['range']} is reversed")
    return errors


# Paths a link must not end in: a release archive or a font file downloads instead of opening
# a page (M1 step 14). Plain-text license files (OFL.txt, LICENSE) are fine.
DOWNLOAD_SUFFIXES = (
    *(".zip", ".tar.gz", ".tgz", ".tar.xz", ".7z"),
    *(".ttf", ".otf", ".ttc", ".woff", ".woff2"),
)


def _link_errors(font: Mapping[str, Any]) -> list[str]:
    urls = {"license.text_url": font["license"]["text_url"]} | {
        f"links.{key}": link["url"] for key, link in font["links"].items() if link
    }
    return [
        f"{where}: {url} is a download, not a page (M1 step 14)"
        for where, url in urls.items()
        if urlsplit(url).path.lower().endswith(DOWNLOAD_SUFFIXES)
    ]


def _font_errors(
    font: Mapping[str, Any], classes: set[str], system_os: Mapping[str, str]
) -> list[str]:
    errors = _link_errors(font)
    if font["license"]["class"] not in classes:
        errors.append(f"license.class {font['license']['class']!r} isn't in license_classes")
    if not font["license"]["redistributable"]:
        errors.append("license.redistributable is false: Rule 3 lists redistributable fonts only")
    errors += [
        f"preinstalled_on: unknown system {item['system']!r}"
        for item in font["preinstalled_on"]
        if item["system"] not in system_os
    ]
    errors += [
        f"pulled_in_by: {item['system']!r} is not a Linux system"
        for item in font["pulled_in_by"]
        if system_os.get(item["system"]) != "linux"
    ]
    preview = font["preview"]
    if preview is not None and preview["path"] != f"specimens/{font['id']}.svg":
        errors.append(f"preview.path must be specimens/{font['id']}.svg")
    flags = set(font["flags"])
    if flags & SPECIMEN_FLAGS and not font["preview_ok"]:
        errors.append("specimen flags on a font without preview_ok")
    if flags & {"specimen_failed", "specimen_hash_mismatch"} and preview is not None:
        errors.append("a failed specimen can't have a preview")
    if "specimen_name_only" in flags and preview is None:
        errors.append("specimen_name_only needs a preview")
    too_new = any(e["unranked"] == "too_new" for e in font["ranks"].values())
    if too_new and "too_new" not in flags:
        errors.append("unranked as too_new without the too_new flag")
    return errors
