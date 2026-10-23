"""The hopper's match file, derived at build time from ``names.json`` and the catalog.

The owner's ruling of 2026-10-22, ``inventory_match_data_site_build``, amends Milestone 3
step 3: the site build, not a pipeline stage, makes the match file with
``tff_catalog.owned.match_document`` from the committed ``build/names.json`` and
``catalog-site.json``, and publishes it as ``/assets/match.<h>.json`` (site/CONTRACT.md
section 11). ``match_file`` first refuses, listing every problem at once:

- a names file of another run, or of the wrong shape (``names_errors``);
- a reviewed list that is malformed, or whose recodes name no alias of the family they
  say (``load_reviewed``, ``recode_errors``);
- what Milestone 3 step 3's collision check finds (``check_errors`` over
  ``owned.check_document``), including an ambiguous set other than the reviewed one, so a
  refresh that adds or changes an ambiguous name fails until the owner has reviewed it
  ("the owner reviews flagged collisions");
- a match file that the hopper's reader, ``owned.Table.from_document``, which the page
  mirrors, refuses.

The reviewed list sits beside the names file (``default_reviewed_path``); for the catalog
that is ``build/names.reviewed.toml``, which no pipeline stage writes. It holds the reviewed
ambiguous names, which only pass or fail the build, and the reviewed recodes, which change
an alias's code in the match file and nothing in ``names.json`` (``hopper_hackgen_related``,
2026-10-23: HackGen, a build of Hack in names.json, is only related to Hack in the hopper,
so the ranking keeps its alias row). It is read with ``tomllib``, as ``tff_site.views``
reads ``config/site.toml``.
"""

import hashlib
import json
import tomllib
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tff_catalog import jsonio, owned
from tff_catalog.keys import match_key
from tff_site import data

# names.json's schema version, the one the match file is derived from (docs/catalog-schema.md).
NAMES_SCHEMA_VERSION = "0.1.0-draft"
NAMES_SCHEMA = "schemas/names.schema.json"
# The relations of a names.json name (schemas/names.schema.json).
NAMES_RELATIONS = frozenset(
    {"rename", "build", "package", "postscript", "bundle", "sibling", "related"}
)
# At most this many shape problems are listed: a file wrong throughout would give thousands.
MAX_SHAPE_ERRORS = 20
# names.json -> names.reviewed.toml, beside it.
REVIEWED_SUFFIX = ".reviewed.toml"
AMBIGUOUS_FIELDS = frozenset({"name", "families", "ruling"})
RECODE_FIELDS = frozenset({"name", "family", "relation", "ruling"})


class MatchFileError(ValueError):
    """The match file can't be made. ``errors`` holds one line per problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s); first: {errors[0] if errors else '-'}")


@dataclass(frozen=True, slots=True)
class Ambiguous:
    """A reviewed ambiguous name: the catalog ids it may be a weight of."""

    name: str
    families: tuple[str, ...]
    ruling: str


@dataclass(frozen=True, slots=True)
class Recode:
    """A reviewed recode: the alias ``name`` of ``family`` is a ``relation`` in the hopper."""

    name: str
    family: str
    relation: str
    ruling: str


@dataclass(frozen=True, slots=True)
class Reviewed:
    """The reviewed list beside a names file, each part by ``match_key``."""

    ambiguous: Mapping[str, Ambiguous] = field(default_factory=dict)
    recodes: Mapping[str, Recode] = field(default_factory=dict)

    def relations(self) -> dict[str, str]:
        """The recodes as ``owned.match_document`` takes them: alias name -> relation."""
        return {recode.name: recode.relation for recode in self.recodes.values()}

    def rulings(self) -> set[str]:
        """The ruling ids the entries cite."""
        return {e.ruling for e in (*self.ambiguous.values(), *self.recodes.values())}


@dataclass(frozen=True, slots=True)
class MatchFile:
    """A made match file: its canonical JSON bytes, and the sha256 of the bytes of the two
    files it came from, for ``version.txt`` (``names_sha256``, ``reviewed_sha256``)."""

    blob: bytes
    names_sha256: str
    reviewed_sha256: str


def default_names_path(data_path: Path) -> Path:
    """The names file of a catalog file's run: its name with ``catalog-site`` read as ``names``.

    ``build/catalog-site.json`` gives ``build/names.json``, and the sample,
    ``tests/fixtures/catalog-site.sample.json``, ``tests/fixtures/names.sample.json``. A catalog
    file named otherwise gives ``names.json`` beside it.
    """
    data_path = Path(data_path)
    name = data_path.name
    if "catalog-site" in name:
        return data_path.with_name(name.replace("catalog-site", "names", 1))
    return data_path.with_name("names.json")


def default_reviewed_path(names_path: Path) -> Path:
    """The reviewed list of a names file, beside it: ``build/names.json`` gives
    ``build/names.reviewed.toml``, the sample's ``names.sample.reviewed.toml``."""
    names_path = Path(names_path)
    return names_path.with_name(names_path.name.removesuffix(".json") + REVIEWED_SUFFIX)


def load_reviewed(path: Path) -> tuple[Reviewed, bytes]:
    """The reviewed list at ``path``, and the bytes read; an empty list and no bytes when
    the file is missing.

    ``[[ambiguous]]`` entries have exactly a ``name``, its ``families`` (catalog ids,
    sorted, no repeats) and the ``ruling`` id that records the owner's review.
    ``[[recode]]`` entries have exactly a ``name`` (an alias in names.json), the ``family``
    whose alias it is, the ``relation`` the hopper gives it instead (``owned.RECODE_RELATIONS``)
    and the ``ruling``. No two entries of one kind may share a key. A missing file reviews
    nothing, so every ambiguous name fails the build.
    """
    path = Path(path)
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        return Reviewed(), b""
    except OSError as exc:
        raise MatchFileError([f"{path}: {exc}"]) from exc
    try:
        doc = tomllib.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise MatchFileError([f"{path}: {exc}"]) from exc
    errors = [
        f"{path}: unknown key {key!r}" for key in sorted(doc.keys() - {"ambiguous", "recode"})
    ]
    ambiguous: dict[str, Ambiguous] = {}
    recodes: dict[str, Recode] = {}
    for kind, fields, out in (
        ("ambiguous", AMBIGUOUS_FIELDS, ambiguous),
        ("recode", RECODE_FIELDS, recodes),
    ):
        entries = doc.get(kind, [])
        if not isinstance(entries, list):
            errors.append(f"{path}: {kind} is not an array of tables")
            continue
        for n, entry in enumerate(entries):
            where = f"{path}: {kind}[{n}]"
            if not isinstance(entry, dict) or entry.keys() != fields:
                errors.append(f"{where}: needs exactly {_and(sorted(fields))}")
                continue
            problem = _entry_problem(kind, entry)
            if problem:
                errors.append(f"{where}: {problem}")
                continue
            key = match_key(entry["name"])
            if key in out:
                errors.append(f"{where}: {entry['name']!r} has the key of {out[key].name!r}")
                continue
            if kind == "ambiguous":
                ambiguous[key] = Ambiguous(entry["name"], tuple(entry["families"]), entry["ruling"])
            else:
                recodes[key] = Recode(**entry)
    if errors:
        raise MatchFileError(errors)
    return Reviewed(ambiguous, recodes), blob


def _and(words: list[str]) -> str:
    return ", ".join(words[:-1]) + f" and {words[-1]}"


def _entry_problem(kind: str, entry: Mapping[str, Any]) -> str | None:
    name, ruling = entry["name"], entry["ruling"]
    if not (_strings(name, ruling) and match_key(name) and ruling):
        return "the name and the ruling must be non-empty strings"
    if kind == "ambiguous":
        families = entry["families"]
        if not (isinstance(families, list) and families and _strings(*families)) or (
            families != sorted(set(families))
        ):
            return "families must be catalog ids, sorted, without repeats"
        return None
    if not (isinstance(entry["family"], str) and entry["family"]):
        return "the family must be a family id"
    if entry["relation"] not in owned.RECODE_RELATIONS:
        return (
            f"relation {entry['relation']!r} is not one of {sorted(owned.RECODE_RELATIONS)}: "
            "a recode can only make an alias a near-match"
        )
    return None


def names_shape_errors(names: Any) -> list[str]:
    """Where ``names`` lacks the shape the match file is made from, by JSON pointer.

    Only what ``names_errors`` and ``tff_catalog.owned`` read is checked: ``run.date``; each
    family's ``id``, ``family``, ``in_catalog``, ``distinct_from`` and ``names`` (a name, a
    relation, and on a build a detail that ``owned`` knows); each ineligible name and reason.
    So a broken file gets a message that says where, never a bare ``KeyError``. The full schema
    takes seconds on the real file, and CI runs it on the committed one
    (docs/catalog-schema.md, "Validation").
    """
    if not isinstance(names, dict):
        return [f"/ is a {type(names).__name__}, not an object"]
    errors: list[str] = []
    run = names.get("run")
    if not (isinstance(run, dict) and isinstance(run.get("date"), str)):
        errors.append("/run/date is missing or not a string")
    families = names.get("families")
    if not isinstance(families, list):
        errors.append("/families is missing or not a list")
        families = []
    for n, fam in enumerate(families):
        errors += _family_shape_errors(f"/families/{n}", fam)
    ineligible = names.get("ineligible")
    if not isinstance(ineligible, list):
        errors.append("/ineligible is missing or not a list")
        ineligible = []
    errors += [
        f"/ineligible/{n} is not a name and a reason, both strings"
        for n, row in enumerate(ineligible)
        if not (isinstance(row, dict) and _strings(row.get("name"), row.get("reason")))
    ]
    if len(errors) > MAX_SHAPE_ERRORS:
        errors[MAX_SHAPE_ERRORS:] = [f"and {len(errors) - MAX_SHAPE_ERRORS} more such problems"]
    return errors


def names_errors(doc: Mapping[str, Any], names: Any) -> list[str]:
    """Why ``names`` isn't the names file of the catalog ``doc``'s run; empty when it is.

    The two must be of one run: ``names`` at ``NAMES_SCHEMA_VERSION`` and of the shape
    ``names_shape_errors`` checks, with the catalog's ``run.date``, no family listed twice, its
    ``in_catalog`` families exactly the catalog's fonts, each under the catalog's family name
    and holding every alias the catalog gives the font, with its relation.
    """
    if not isinstance(names, dict):
        return [f"/ is a {type(names).__name__}, not an object"]
    if names.get("schema_version") != NAMES_SCHEMA_VERSION:
        return [f"schema_version is {names.get('schema_version')!r}, not {NAMES_SCHEMA_VERSION!r}"]
    errors = names_shape_errors(names)
    if errors:
        return errors
    run_date = doc["run"]["date"]
    if names["run"]["date"] != run_date:
        errors.append(f"run.date is {names['run']['date']}, the catalog's is {run_date}")
    counts = Counter(fam["id"] for fam in names["families"])
    errors += [f"family {fid} is listed {n} times" for fid, n in sorted(counts.items()) if n > 1]
    listed = {fam["id"]: fam for fam in names["families"] if fam["in_catalog"]}
    fonts = {font["id"]: font for font in doc["fonts"]}
    errors += [
        f"{fid}: in the catalog, but not an in_catalog family"
        for fid in sorted(fonts.keys() - listed.keys())
    ]
    errors += [
        f"{fid}: an in_catalog family the catalog lacks"
        for fid in sorted(listed.keys() - fonts.keys())
    ]
    for fid in sorted(fonts.keys() & listed.keys()):
        font, fam = fonts[fid], listed[fid]
        if fam["family"] != font["family"]:
            errors.append(f"{fid}: family {fam['family']!r}, the catalog's {font['family']!r}")
        held = {(n["name"], n["relation"]) for n in fam["names"]}
        errors += [
            f"{fid}: no {a['relation']} name {a['name']!r}, which the catalog lists"
            for a in font["aliases"]
            if (a["name"], a["relation"]) not in held
        ]
    return errors


def recode_errors(names: Mapping[str, Any], reviewed: Reviewed) -> list[str]:
    """Recodes whose key isn't an alias of exactly the family they name.

    A recode works by ``match_key`` on every alias row at that key (``owned``), so the review
    of one family's alias would silently recode another's, or recode nothing once a refresh
    moves the alias (a stale entry).
    """
    holders: dict[str, set[str]] = defaultdict(set)
    for fam in names["families"]:
        for item in fam["names"]:
            holders[match_key(item["name"])].add(fam["id"])
    errors: list[str] = []
    for key, recode in sorted(reviewed.recodes.items()):
        found = sorted(holders.get(key, ()))
        if found != [recode.family]:
            errors.append(
                f"{recode.name!r} is reviewed as an alias of {recode.family} ({recode.ruling}), "
                f"but names.json's aliases at its key {key!r} are of {', '.join(found) or 'none'}"
            )
    return errors


def check_errors(
    check: owned.Check,
    names: Mapping[str, Any],
    reviewed: Reviewed,
    reviewed_path: Path,
) -> list[str]:
    """What in ``owned.check_document``'s findings fails the build (Milestone 3 step 3).

    Step 3: "CI fails if, outside the ambiguous set, two families share a match_key or
    stripping style words from a catalog name yields another family's key", and "the owner
    reviews flagged collisions". So the build fails on a ``distinct_from`` pair that meets
    (Inter Tight reaching Inter, M3-D5 a), a key that several catalog families own outside the
    ambiguous set, a catalog name whose stripped key only families outside the catalog hold
    (the ambiguous set holds catalog families only, so it can't take one), and an ambiguous set
    other than the reviewed one: the hopper doubts those names (``owned.match``), so which ones
    it doubts is the owner's call. A collision that starts from no catalog name and reaches no
    catalog family changes no result, and is only reported.
    """
    errors = [
        f"{m.family}: its name {m.name!r} reaches {m.other} at key {m.key!r}, "
        "and the two are distinct_from each other"
        for m in check.meetings
    ]
    errors += [
        f"key {key!r} is owned by several catalog families ({', '.join(fids)}), "
        "outside the ambiguous set"
        for key, fids in check.shared.items()
        if key not in check.amb
    ]
    first_name: dict[str, str] = {}
    for c in check.collisions:
        first_name.setdefault(c.key, c.name)
    for key, fids in check.amb.items():
        seen = reviewed.ambiguous.get(key)
        if seen is None:
            errors.append(
                f"{first_name[key]!r} may be a weight of {', '.join(fids)}: an ambiguous name "
                f"nobody has reviewed; once the owner has, list it in {reviewed_path}"
            )
        elif seen.families != tuple(fids):
            errors.append(
                f"{first_name[key]!r} may be a weight of {', '.join(fids)}, but was reviewed "
                f"({seen.ruling}) as one of {', '.join(seen.families)}; the owner reviews the change"
            )
    errors += [
        f"{seen.name!r} is reviewed as ambiguous in {reviewed_path}, but no longer is; remove it"
        for key, seen in sorted(reviewed.ambiguous.items())
        if key not in check.amb
    ]
    catalog_keys = {
        match_key(name)
        for fam in names["families"]
        if fam["in_catalog"]
        for name in (fam["family"], *(n["name"] for n in fam["names"]))
    }
    errors += [
        f"{c.name!r}, a catalog name, strips to the key {c.reached!r} of "
        f"{', '.join(c.families)}, outside the catalog, which the ambiguous set can't hold"
        for c in check.collisions
        if not c.catalog and c.key in catalog_keys and c.key not in check.amb
    ]
    return errors


def match_file(doc: Mapping[str, Any], names_path: Path) -> MatchFile:
    """The match file of the catalog ``doc`` and the names file at ``names_path``.

    The reviewed list comes from ``default_reviewed_path(names_path)``. The hashes go in
    ``version.txt``: ``names_sha256`` is the names file's, so ``sha256sum build/names.json``
    matches a built site as ``catalog_sha256`` does, and ``reviewed_sha256`` the reviewed
    list's (of no bytes when there is none), since its recodes change the match file too. The
    match file's own ``names_sha256`` hashes the canonical form (``owned.match_document``).
    Raises ``MatchFileError`` with every problem.
    """
    names_path = Path(names_path)
    try:
        blob = names_path.read_bytes()
        names = data.loads(blob)
    except FileNotFoundError:
        raise MatchFileError(
            [f"{names_path}: not found; the build needs the names file of the catalog's run"]
        ) from None
    except (OSError, ValueError) as exc:  # ValueError: bad UTF-8 or JSON, or too deep
        raise MatchFileError([f"{names_path}: {exc}"]) from exc
    errors = names_errors(doc, names)
    if errors:
        raise MatchFileError([f"{names_path}: {error}" for error in errors])
    reviewed_path = default_reviewed_path(names_path)
    reviewed, reviewed_blob = load_reviewed(reviewed_path)
    errors = recode_errors(names, reviewed)
    if errors:
        raise MatchFileError([f"{reviewed_path}: {error}" for error in errors])
    ids = [font["id"] for font in doc["fonts"]]
    recodes = reviewed.relations()
    try:
        check = owned.check_document(names, ids, recodes=recodes)
        match = owned.match_document(names, ids, doc["run"]["date"], recodes=recodes)
    except ValueError as exc:  # what owned refuses that the checks above let through
        raise MatchFileError([f"{names_path}: {exc}"]) from exc
    errors = check_errors(check, names, reviewed, reviewed_path)
    if errors:
        raise MatchFileError([f"{names_path}: {error}" for error in errors])
    out = jsonio.canonical_bytes(match)
    try:
        owned.Table.from_document(json.loads(out))
    except ValueError as exc:
        raise MatchFileError(
            [f"{names_path}: the match file made from it is one the hopper can't read ({exc})"]
        ) from exc
    return MatchFile(
        out, hashlib.sha256(blob).hexdigest(), hashlib.sha256(reviewed_blob).hexdigest()
    )


def _strings(*values: Any) -> bool:
    return all(isinstance(value, str) for value in values)


def _family_shape_errors(at: str, fam: Any) -> list[str]:
    if not isinstance(fam, dict):
        return [f"{at} is not an object"]
    errors = [
        f"{at}/{key} is missing or not a string"
        for key in ("id", "family")
        if not isinstance(fam.get(key), str)
    ]
    if not isinstance(fam.get("in_catalog"), bool):
        errors.append(f"{at}/in_catalog is missing or not true or false")
    others = fam.get("distinct_from")
    if not (isinstance(others, list) and _strings(*others)):
        errors.append(f"{at}/distinct_from is missing or not a list of family ids")
    entries = fam.get("names")
    if not isinstance(entries, list):
        return [*errors, f"{at}/names is missing or not a list"]
    for m, entry in enumerate(entries):
        where = f"{at}/names/{m}"
        if not (isinstance(entry, dict) and isinstance(entry.get("name"), str)):
            errors.append(f"{where} is not an object with a name string")
            continue
        relation, detail = entry.get("relation"), entry.get("detail")
        if not (isinstance(relation, str) and relation in NAMES_RELATIONS):
            errors.append(f"{where}/relation is {relation!r}, not a relation of {NAMES_SCHEMA}")
        elif relation == "build" and not (
            isinstance(detail, str) and detail in owned.SAME_FONT_DETAILS | owned.PATCHED_DETAILS
        ):
            errors.append(f"{where}/detail is {detail!r}, not a build detail of {NAMES_SCHEMA}")
    return errors
