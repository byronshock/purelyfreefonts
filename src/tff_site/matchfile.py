"""The hopper's match file, derived at build time from ``names.json`` and the catalog.

The owner's ruling of 2026-10-22, ``inventory_match_data_site_build``, amends Milestone 3
step 3: the site build, not a pipeline stage, makes the match file with
``tff_catalog.owned.match_document`` from the committed ``build/names.json`` and
``catalog-site.json``, and publishes it as ``/assets/match.<h>.json`` (site/CONTRACT.md
section 11). ``match_file`` first refuses, listing every problem:

- a names file that can't be read, of another run, or of the wrong shape (``names_errors``,
  in stages: its schema version, then its shape, then its run), and a reviewed list that is
  malformed (``load_reviewed``), together; the checks below read both, so these stop the
  build before them;
- recodes that name no alias of the family they say, or that change nothing, an alias
  already a near-match or of a family outside the catalog (``refused_recodes``), or that
  another name of the family bypasses (``owned.RecodeError``),
  together with what Milestone 3 step 3's collision check finds without them (``check_errors``
  over ``owned.check_document``), including an ambiguous set other than the reviewed one, so
  a refresh that changes the ambiguous set, moves a recoded alias, or both, fails once,
  naming all of it, until the owner has reviewed it ("the owner reviews flagged
  collisions");
- a match file that the hopper's reader, ``owned.Table.from_document``, which the page
  mirrors, refuses.

The reviewed list sits beside the names file (``default_reviewed_path``); for the catalog
that is ``build/names.reviewed.toml``, which no pipeline stage writes. It holds the reviewed
ambiguous names, which only pass or fail the build (the match file's ``amb`` is derived, so
they change none of its bytes), and the reviewed recodes, which change an alias's code in the
match file and nothing in ``names.json`` (``hopper_hackgen_related``, 2026-10-23: HackGen, a
build of Hack in names.json, is only related to Hack in the hopper, so the ranking keeps its
alias row). It is read with ``tomllib``, as ``tff_site.views`` reads ``config/site.toml``.
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
# The keys schemas/names.schema.json allows in each kind of object (additionalProperties is
# false throughout), so names_shape_errors refuses the rest.
NAMES_KEYS = frozenset({"schema_version", "run", "families", "ineligible"})
RUN_KEYS = frozenset({"date", "method_version"})
FAMILY_KEYS = frozenset({"id", "family", "superfamily_id", "in_catalog", "names", "distinct_from"})
NAME_KEYS = frozenset({"name", "relation", "detail"})
INELIGIBLE_KEYS = frozenset({"name", "reason"})
AMBIGUOUS_FIELDS = frozenset({"name", "families", "ruling"})
RECODE_FIELDS = frozenset({"name", "family", "relation", "ruling"})
# The end of each collision problem that no edit to the reviewed list can clear: a meeting or a
# shared key that no recode the build takes would end, a catalog name reaching a family outside
# the catalog (ops/MONTHLY.md step 5).
UNLISTABLE = (
    "; no entry in the reviewed list clears this: the owner rules, and an alias row "
    "(Milestone 1's review) or a change to the build carries it out (ops/MONTHLY.md step 5)"
)
# The end of a meeting or a shared key that one recode the build takes would end: the owned
# code there is a catalog family's, through aliases that a [[recode]] can make near-matches
# (``recodable_keys``).
RECODABLE = (
    "; the owner rules: a [[recode]] making {family}'s alias at that key a near-match clears "
    "this, and otherwise an alias row (Milestone 1's review) or a change to the build carries "
    "it out (ops/MONTHLY.md step 5)"
)


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
    except RecursionError:  # tomllib recurses: an array or inline table some 400 levels deep
        raise MatchFileError([f"{path}: nested too deeply to read"]) from None
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
    relation = entry["relation"]
    if not (isinstance(relation, str) and relation in owned.RECODE_RELATIONS):
        return (
            f"relation {relation!r} is not one of {sorted(owned.RECODE_RELATIONS)}: "
            "a recode can only make an alias a near-match"
        )
    return None


def names_shape_errors(names: Any) -> list[str]:
    """Where ``names`` lacks the shape the match file is made from, by JSON pointer.

    What ``names_errors`` and ``tff_catalog.owned`` read is checked: the run's ``date`` and
    ``method_version``; each family's ``id``, ``family``, ``in_catalog``, ``distinct_from`` and
    ``names`` (a name, a relation, and on a build a detail that ``owned`` knows); each
    ineligible name and reason. So are every object's keys, against what the schema allows,
    and each family's ``superfamily_id`` (null or a string), so nothing the build doesn't read
    can hide in the file and reach the serialiser (``owned`` hashes the whole document). A
    broken file gets a message that says where, never a bare ``KeyError`` or
    ``RecursionError``. The full schema takes
    seconds on the real file, and CI runs it on the committed one (docs/catalog-schema.md,
    "Validation").
    """
    if not isinstance(names, dict):
        return [f"/ is a {type(names).__name__}, not an object"]
    errors = _unknown_keys("", names, NAMES_KEYS)
    run = names.get("run")
    if isinstance(run, dict):
        errors += _unknown_keys("/run", run, RUN_KEYS)
        errors += [
            f"/run/{key} is missing or not a string"
            for key in sorted(RUN_KEYS)
            if not isinstance(run.get(key), str)
        ]
    else:
        errors.append("/run is missing or not an object")
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
    for n, row in enumerate(ineligible):
        if not (isinstance(row, dict) and _strings(row.get("name"), row.get("reason"))):
            errors.append(f"/ineligible/{n} is not a name and a reason, both strings")
        else:
            errors += _unknown_keys(f"/ineligible/{n}", row, INELIGIBLE_KEYS)
    if len(errors) > MAX_SHAPE_ERRORS:
        errors[MAX_SHAPE_ERRORS:] = [f"and {len(errors) - MAX_SHAPE_ERRORS} more such problems"]
    return errors


def names_errors(doc: Mapping[str, Any], names: Any) -> list[str]:
    """Why ``names`` isn't the names file of the catalog ``doc``'s run; empty when it is.

    The two must be of one run: ``names`` at ``NAMES_SCHEMA_VERSION`` and of the shape
    ``names_shape_errors`` checks, with the catalog's ``run.date`` and ``run.method_version``
    (one run's snapshots scored again under a new method keep their date, as the run of
    2026-10-17 did under the method of 2026-10-20), no family listed twice, its
    ``in_catalog`` families exactly the catalog's fonts, each under the catalog's family name
    and holding every alias the catalog gives the font, with its relation.

    In stages, each returning before the next: another schema version is the one problem
    listed, then the shape's problems (``names_shape_errors``), then the run's and the
    families'.
    """
    if not isinstance(names, dict):
        return [f"/ is a {type(names).__name__}, not an object"]
    if names.get("schema_version") != NAMES_SCHEMA_VERSION:
        return [f"schema_version is {names.get('schema_version')!r}, not {NAMES_SCHEMA_VERSION!r}"]
    errors = names_shape_errors(names)
    if errors:
        return errors
    errors += [
        f"run.{key} is {names['run'][key]}, the catalog's is {doc['run'][key]}"
        for key in ("date", "method_version")
        if names["run"][key] != doc["run"][key]
    ]
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


def refused_recodes(names: Mapping[str, Any], reviewed: Reviewed) -> dict[str, str]:
    """Why each refused recode is, by its key: one whose key isn't an alias of exactly the
    family it names, whose family is outside the catalog, or whose aliases names.json already
    gives no owned relation (``owned.OWNED_RELATIONS``). Empty when every recode is one that
    ``owned`` takes.

    A recode works by ``match_key`` on every alias row at that key (``owned``), so the review
    of one family's alias would silently recode another's, or recode nothing once a refresh
    moves the alias, takes its family out of the catalog (the match file codes a family
    outside it ``-`` whatever its relation) or makes the alias a near-match itself (a stale
    entry). ``owned`` refuses what remains, a recode that another name of its family bypasses
    (``owned.RecodeError``).
    """
    holders, relations = _alias_holders(names)
    in_catalog = {fam["id"] for fam in names["families"] if fam["in_catalog"]}
    refused: dict[str, str] = {}
    for key, recode in sorted(reviewed.recodes.items()):
        found = sorted(holders.get(key, ()))
        if found != [recode.family]:
            refused[key] = (
                f"{recode.name!r} is reviewed as an alias of {recode.family} ({recode.ruling}), "
                f"but names.json's aliases at its key {key!r} are of {', '.join(found) or 'none'}"
            )
        elif recode.family not in in_catalog:
            refused[key] = (
                f"{recode.name!r} is reviewed ({recode.ruling}) as an alias of {recode.family}, "
                "which names.json has outside the catalog, so the recode changes nothing in the "
                "match file; once the owner has reviewed the change, remove it"
            )
        elif not relations[key] & owned.OWNED_RELATIONS:
            refused[key] = (
                f"{recode.name!r} is reviewed ({recode.ruling}) as an owned alias of "
                f"{recode.family} that the hopper makes {recode.relation}, but names.json's "
                f"aliases at its key {key!r} are already {', '.join(sorted(relations[key]))}, "
                "so the recode changes nothing; once the owner has reviewed the change, remove it"
            )
    return refused


def _alias_holders(names: Mapping[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """By ``match_key``, the families with an alias row there, and those rows' relations."""
    holders: dict[str, set[str]] = defaultdict(set)
    relations: dict[str, set[str]] = defaultdict(set)
    for fam in names["families"]:
        for item in fam["names"]:
            holders[match_key(item["name"])].add(fam["id"])
            relations[match_key(item["name"])].add(item["relation"])
    return holders, relations


def recodable_keys(names: Mapping[str, Any]) -> dict[str, str]:
    """The keys at which the build takes a ``[[recode]]``, each with the family it names.

    As the build decides it: the aliases at the key are of that family alone, which is in the
    catalog, and some have an owned relation (``refused_recodes``); the family's own name has
    another key (``owned._check_recodes``). Another family may hold the key by its name, which
    no recode touches. A recode makes those aliases near-matches, so it ends a meeting or a
    shared key whose owned code there is that family's (``check_errors``). ``owned`` can still
    find another name of the family that reaches the key and bypasses the recode, as a
    HackGen NF build would bypass HackGen's, and then asks for that name's recode too.
    """
    holders, relations = _alias_holders(names)
    by_id = {fam["id"]: fam for fam in names["families"]}
    out: dict[str, str] = {}
    for key, fids in sorted(holders.items()):
        if not key or len(fids) != 1 or not relations[key] & owned.OWNED_RELATIONS:
            continue
        (fid,) = fids
        if by_id[fid]["in_catalog"] and match_key(by_id[fid]["family"]) != key:
            out[key] = fid
    return out


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
    (Inter Tight reaching Inter, M3-D5 a; only at a catalog family, since one outside the
    catalog is never owned: ``owned.check_document``), a key that a catalog family owns with
    another family, in the catalog or not (a list's name there counts the catalog family as
    owned), a catalog name whose stripped key only families outside the catalog hold (the
    ambiguous set holds catalog families only, so it can't take one), and an ambiguous set
    other than the reviewed one: the hopper doubts those names (``owned.match``), so which ones
    it doubts is the owner's call. A collision that starts from no catalog name and reaches no
    catalog family changes no result, and is only reported.

    A shared key fails in the ambiguous set too. The hopper doubts only an untrusted name there:
    a trusted one (fc-list's first family, a macOS family, a PostScript name's family part)
    counts every owned code at the key. A family that gains a name at a reviewed ambiguous key
    leaves the entry's families as they were, so without this a changed name would pass
    unreviewed, which ``inventory_ambiguous_names`` fails ("on any new, changed or dropped name
    until the owner has reviewed it").

    Editing the reviewed list clears the ambiguous set's problems. Its ``[[ambiguous]]``
    entries hold no exception for the other three. A ``[[recode]]`` clears a meeting or a
    shared key when the build takes a recode at the key (``recodable_keys``) and the family it
    names is the one whose owned code there makes the problem: the meeting's other family, or
    a catalog family sharing the key with one other family, or with families outside the
    catalog only. A recode makes that family's aliases at the key near-matches, which may
    share a key as a bundle does, so that problem ends with ``RECODABLE``, naming the family. The rest end with ``UNLISTABLE``, carried out
    by an alias row or a change to the build (ops/MONTHLY.md step 5): an owned code through a
    family's own name, or through aliases that two families hold, which the build refuses to
    recode, and every stripped catalog name (stripping reads no relation).
    """
    recodable = recodable_keys(names)
    in_catalog = {fam["id"] for fam in names["families"] if fam["in_catalog"]}

    def ending(key: str, owners: list[str]) -> str:
        """RECODABLE when the family that ``recodable`` gives at ``key`` is one of the
        ``owners`` there, and a recode of it leaves no catalog family sharing the key."""
        fid = recodable.get(key)
        rest = [other for other in owners if other != fid]
        if fid in owners and (len(rest) <= 1 or not in_catalog.intersection(rest)):
            return RECODABLE.format(family=fid)
        return UNLISTABLE

    errors = [
        f"{m.family}: its name {m.name!r} reaches {m.other} at key {m.key!r}, "
        f"and the two are distinct_from each other{ending(m.key, [m.other])}"
        for m in check.meetings
    ]
    for key, fids in check.shared.items():
        inside = [fid for fid in fids if fid in in_catalog]
        outside = [fid for fid in fids if fid not in in_catalog]
        who = (
            f"several families ({', '.join(inside)} in the catalog, {', '.join(outside)} not)"
            if outside
            else f"several catalog families ({', '.join(inside)})"
        )
        where = (
            "in the ambiguous set, where a trusted name counts each as owned"
            if key in check.amb
            else "outside the ambiguous set"
        )
        errors.append(f"key {key!r} is owned by {who}, {where}{ending(key, list(fids))}")
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
    catalog_keys = {
        match_key(name)
        for fam in names["families"]
        if fam["in_catalog"]
        for name in (fam["family"], *(n["name"] for n in fam["names"]))
    }
    outside = [
        c
        for c in check.collisions
        if not c.catalog and c.key in catalog_keys and c.key not in check.amb
    ]
    # Archivo leaving while Archivo Black stays: the name drops out of the ambiguous set
    # because its other family left, so removing the entry clears nothing (UNLISTABLE below).
    left = {c.key for c in outside}
    errors += [
        f"{seen.name!r} is reviewed as ambiguous in {reviewed_path}, but now strips only to a "
        "family outside the catalog; the owner reviews it with that collision, below"
        if key in left
        else f"{seen.name!r} is reviewed as ambiguous in {reviewed_path}, but no longer is; "
        "once the owner has reviewed the drop, remove it"
        for key, seen in sorted(reviewed.ambiguous.items())
        if key not in check.amb
    ]
    errors += [
        f"{c.name!r}, a catalog name, strips to the key {c.reached!r} of "
        f"{', '.join(c.families)}, outside the catalog, which the ambiguous set can't hold"
        f"{UNLISTABLE}"
        for c in outside
    ]
    return errors


def match_file(doc: Mapping[str, Any], names_path: Path) -> MatchFile:
    """The match file of the catalog ``doc`` and the names file at ``names_path``.

    The reviewed list comes from ``default_reviewed_path(names_path)``. The hashes go in
    ``version.txt``: ``names_sha256`` is the names file's, so ``sha256sum build/names.json``
    matches a built site as ``catalog_sha256`` does, and ``reviewed_sha256`` the reviewed
    list's (of no bytes when there is none), since its recodes change the match file too. The
    match file's own ``names_sha256`` hashes the canonical form (``owned.match_document``).
    Raises ``MatchFileError`` with every problem of its stage: the two files' own problems
    (unreadable, of another run, of the wrong shape, malformed) stop it before the checks,
    which read both, and the names file's come in stages of their own (``names_errors``: its
    schema version, then its shape, then its run, each stopping it before the next); past
    them, the refused recodes and the collision check's findings come together, the check
    running without the refused recodes.
    """
    names_path = Path(names_path)
    reviewed_path = default_reviewed_path(names_path)
    errors: list[str] = []
    names: Any = None
    blob = b""
    try:
        blob = names_path.read_bytes()
        names = data.loads(blob)
    except FileNotFoundError:
        errors.append(
            f"{names_path}: not found; the build needs the names file of the catalog's run"
        )
    except (OSError, ValueError) as exc:  # ValueError: bad UTF-8 or JSON, or too deep
        errors.append(f"{names_path}: {exc}")
    else:
        errors += [f"{names_path}: {error}" for error in names_errors(doc, names)]
    reviewed, reviewed_blob = Reviewed(), b""
    try:
        reviewed, reviewed_blob = load_reviewed(reviewed_path)
    except MatchFileError as exc:
        errors += exc.errors
    if errors:
        raise MatchFileError(errors)
    refused = refused_recodes(names, reviewed)
    errors = [f"{reviewed_path}: {error}" for _, error in sorted(refused.items())]
    ids = [font["id"] for font in doc["fonts"]]
    recodes = {r.name: r.relation for key, r in reviewed.recodes.items() if key not in refused}
    while True:
        try:
            check = owned.check_document(names, ids, recodes=recodes)
            break
        except owned.RecodeError as exc:  # other names bypass them: check the rest without them
            kept = {name: rel for name, rel in recodes.items() if match_key(name) not in exc.keys}
            if len(kept) == len(recodes):  # not recodes of ours: never loop
                raise MatchFileError([*errors, f"{names_path}: {exc}"]) from exc
            errors += [f"{reviewed_path}: {message}" for _, message in exc.problems]
            recodes = kept
        except ValueError as exc:  # what owned refuses that the checks above let through
            raise MatchFileError([*errors, f"{names_path}: {exc}"]) from exc
    errors += [
        f"{names_path}: {error}" for error in check_errors(check, names, reviewed, reviewed_path)
    ]
    if errors:
        raise MatchFileError(errors)
    try:
        match = owned.match_document(names, ids, doc["run"]["date"], recodes=recodes)
    except ValueError as exc:  # what owned refuses that the checks above let through
        raise MatchFileError([f"{names_path}: {exc}"]) from exc
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


def _unknown_keys(at: str, obj: Mapping[str, Any], allowed: frozenset[str]) -> list[str]:
    return [
        f"{at or '/'} has the key {key!r}, which {NAMES_SCHEMA} doesn't allow"
        for key in sorted(obj.keys() - allowed)
    ]


def _family_shape_errors(at: str, fam: Any) -> list[str]:
    if not isinstance(fam, dict):
        return [f"{at} is not an object"]
    errors = _unknown_keys(at, fam, FAMILY_KEYS)
    errors += [
        f"{at}/{key} is missing or not a string"
        for key in ("id", "family")
        if not isinstance(fam.get(key), str)
    ]
    if "superfamily_id" not in fam or not isinstance(fam["superfamily_id"], str | None):
        errors.append(f"{at}/superfamily_id is missing or not null or a string")
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
        errors += _unknown_keys(where, entry, NAME_KEYS)
        relation, detail = entry.get("relation"), entry.get("detail")
        if not (isinstance(relation, str) and relation in NAMES_RELATIONS):
            errors.append(f"{where}/relation is {relation!r}, not a relation of {NAMES_SCHEMA}")
        elif relation == "build" and not (
            isinstance(detail, str) and detail in owned.SAME_FONT_DETAILS | owned.PATCHED_DETAILS
        ):
            errors.append(f"{where}/detail is {detail!r}, not a build detail of {NAMES_SCHEMA}")
    return errors
