"""The alias table and the "aliases" stage (milestone-1 step 7). Owner of the stage: agent P4.

``data/aliases.csv`` is the one versioned table that maps every package, slug
and name to a family (design-m1 gap G5). Columns, in order::

    alias,ns,family_id,relation,detail,source,first_seen,reviewed_by

- ``alias`` + ``ns``: the name as written and its namespace (``records.NAMESPACES``);
  ``inter`` can be an npm, Fontsource and Google key at once, so ``ns`` is required.
- ``family_id``: the target family; empty only for ``ineligible`` rows. For
  ``distinct`` rows it is the family the alias must never map to.
- ``relation``: one of ``RELATIONS``.
- ``detail``: the ineligible reason (``INELIGIBLE_REASONS``), the build subtype
  (``BUILD_DETAILS``), or free text for the other relations.
- ``source``: the miner or person that proposed the row; ``first_seen``: ISO
  date; ``reviewed_by``: who accepted it (``auto:<rule>`` for auto-accepted).

Miners never write this file: they write ``data/alias-seeds/<miner>.csv``
(``SEED_COLUMNS``), and ``tff-catalog aliases --apply`` (the lead only) merges
accepted rows here; so does ``refresh`` for the rows its run used
(``apply_accepted``), and its pull request carries the table for review. ``load_aliases`` and ``write_aliases`` are strict and
deterministic.

What the relations do (the stage below):

- ``MAPPING_RELATIONS`` (rename, build, package, postscript, ineligible) send a
  key's counts to one family, or, for ``ineligible``, out of the ranking. One
  (ns, match_key) maps to at most one family (``AliasTable`` checks it).
- ``bundle`` rows credit several families from one key (D2); ``sibling`` and
  ``related`` rows are information for the catalog. None of them enter the
  alias index (``mapping.IndexEntry`` holds one family per key). A candidate
  that would send a bundled key (a name: bundled in any name namespace) to one
  family is ``blocked``, never queued: the table could not hold both.
- ``distinct`` rows are blocks: the alias never maps to ``family_id``. With
  detail ``SIBLING_DETAIL`` the block covers every key whose match_key
  *contains* the alias's (so ``fonts-roboto-slab`` and ``@fontsource/roboto-slab``
  never reach Roboto); otherwise it covers the alias itself. ``SIBLING_RULES``
  seeds them for Roboto/Roboto Slab, Fira Sans/Fira Code, Inter/Inter Tight and
  Noto Sans/Noto Sans JP; the owner's rejections add exact ones.
- A family name is the same name in every ``NAME_NAMESPACES`` namespace, so a
  name row or universe name holds for Google, Fonts Over Time, the Almanac,
  font name tables and foundries alike. An explicit key in the namespace wins.
- A rename keeps the family's first id (methodology §2): when the old name is
  registered in ``state/ids.json`` and the new name's family was minted this
  run, both names get rename rows to the old id (the new one in ``font-name``,
  detail ``current``), so the next universe run folds the new name into it.
  Until then the index sends both to this run's family. When the new name's id
  is registered too (a refresh merged before the rename was applied), or a
  stale source still lists the old name as a live family, the owner is asked,
  and the question's "accept" keeps the old id.

Only ``AUTO_RULES`` candidates are accepted without review: google/fonts
renames (miner ``gf_history``) and Nerd ``unpatchedName`` rows (miner
``nerd``), and only when nothing contradicts them. Everything else waits in the
review queue for the owner (gate A).

Gate A questions and rulings. ``questions(paths)`` gives ``reviews`` one
question per queue item, id = the item's id, options ``QUESTION_OPTIONS``: (a)
accept, (b) reject, (c) research (keeps it open). Plain proposals (reason
"review") recommend (a), so ``tff-catalog questions --gate A`` asks alike ones in
batches; the rest recommend nothing and are asked one by one. Rulings
(``data/reviews/aliases/<YYYY-MM-DD>.toml``) are read with ``reviews.load_rulings``
(schema-checked): a table named after a queue item (``A-<hash>``) rules on that
item, and any table may instead list ``items``. ``choice = "a"`` accepts, ``"b"``
rejects (a rejection with a known family becomes an exact ``distinct`` row), any
other choice decides nothing. An accepting single-item table may correct the row
with ``family_id``, ``relation`` or ``detail``. A later file wins over an earlier
one. Rulings are read on every run, so a decision holds until the table carries it.

Stage outputs, besides ``build/stage/alias_index.json`` (``mapping.IndexEntry``):

- ``build/stage/alias_candidates.jsonl``: one line per distinct candidate, its
  fields (``alias`` and ``target`` as ``{ns, key}``) plus ``status`` (accepted,
  known, blocked, queued, rejected), ``family_id``, ``reason`` and ``item``;
  ``--apply`` reads it back instead of mining again.
- ``build/stage/queues/aliases.json``: ``{"items", "counts", "failed_miners",
  "notes"}``. An item is ``{id, alias, target, family_id, relation, detail,
  reason, note, sources, evidence, auto, current}``, ``reason`` a
  ``QUEUE_REASONS`` key. ``counts.unapplied`` counts the rows this run used
  that ``data/aliases.csv`` lacks (or dropped): the universe stage ran before
  them, so a run with any should be followed by ``--apply`` and a rerun from
  stage "universe" before it is published (a rename accepted this run, for
  example, can leave the new name's Google key on this run's family and its
  survey names on the kept id until then). ``refresh`` does both itself.
"""

import csv
import hashlib
import io
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import astuple, dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tff_catalog import clock, jsonio, reviews, stageio
from tff_catalog.jsonio import atomic_write
from tff_catalog.keys import match_key
from tff_catalog.names import ID_PATTERN
from tff_catalog.records import NAMESPACES, SourceKey
from tff_catalog.store import RawDir

if TYPE_CHECKING:
    from tff_catalog.paths import Paths
    from tff_catalog.stages import StageContext
    from tff_catalog.universe import Universe

COLUMNS = ("alias", "ns", "family_id", "relation", "detail", "source", "first_seen", "reviewed_by")
SEED_COLUMNS = (
    "alias",
    "ns",
    "target_ns",
    "target",
    "relation",
    "detail",
    "source",
    "evidence",
    "auto",
)
RELATIONS = frozenset(
    {
        "rename",
        "build",
        "package",
        "postscript",
        "sibling",
        "related",
        "bundle",
        "distinct",
        "ineligible",
    }
)
INELIGIBLE_REASONS = frozenset({"proprietary", "itf", "cjk", "icon", "generic", "system"})
BUILD_DETAILS = frozenset(
    {
        "",
        "nerd",
        "nf",
        "nfm",
        "nfp",
        "propo",
        "powerline",
        "nl",
        "cjk",
        "variable",
        "static",
        "opsz",
    }
)


class AliasError(ValueError):
    """A malformed alias or seed file."""


@dataclass(frozen=True, slots=True, order=True)
class AliasRow:
    alias: str
    ns: str
    family_id: str
    relation: str
    detail: str
    source: str
    first_seen: date
    reviewed_by: str

    @property
    def key(self) -> SourceKey:
        return SourceKey(self.ns, self.alias)

    def problems(self) -> list[str]:
        """What is wrong with this row, if anything."""
        out = []
        if not self.alias or self.alias != self.alias.strip():
            out.append("alias is empty or has outer spaces")
        if self.ns not in NAMESPACES:
            out.append(f"unknown ns {self.ns!r}")
        if self.relation not in RELATIONS:
            out.append(f"unknown relation {self.relation!r}")
        if self.relation == "ineligible":
            if self.family_id:
                out.append("ineligible rows have no family_id")
            if self.detail not in INELIGIBLE_REASONS:
                out.append(f"ineligible reason {self.detail!r} not in {sorted(INELIGIBLE_REASONS)}")
        elif not ID_PATTERN.match(self.family_id):
            out.append(f"bad family_id {self.family_id!r}")
        if self.relation == "build" and self.detail not in BUILD_DETAILS:
            out.append(f"build detail {self.detail!r} not in {sorted(BUILD_DETAILS)}")
        if not self.source:
            out.append("source is empty")
        return out


def _sort_key(row: AliasRow) -> tuple[str, ...]:
    return (
        row.family_id,
        row.relation,
        row.ns,
        match_key(row.alias),
        row.alias,
        row.detail,
        row.source,
        row.first_seen.isoformat(),
        row.reviewed_by,
    )


def _read_csv(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None or tuple(header) != columns:
            raise AliasError(f"{path}: header must be {','.join(columns)}")
        rows = []
        for number, values in enumerate(reader, start=2):
            if not values:
                continue
            if len(values) != len(columns):
                raise AliasError(
                    f"{path}:{number}: expected {len(columns)} fields, got {len(values)}"
                )
            rows.append(dict(zip(columns, values, strict=True)))
        return rows


def _write_csv(path: Path, columns: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    atomic_write(Path(path), buf.getvalue().encode("utf-8"))


def load_aliases(path: Path) -> list[AliasRow]:
    """Read and validate ``data/aliases.csv``; raises ``AliasError`` naming the line."""
    out: list[AliasRow] = []
    seen: set[AliasRow] = set()
    for number, d in enumerate(_read_csv(path, COLUMNS), start=2):
        try:
            row = AliasRow(**{**d, "first_seen": date.fromisoformat(d["first_seen"])})
        except ValueError as exc:
            raise AliasError(f"{path}:{number}: {exc}") from exc
        problems = row.problems()
        if problems:
            raise AliasError(f"{path}:{number}: {'; '.join(problems)}")
        if row in seen:
            raise AliasError(f"{path}:{number}: duplicate row")
        seen.add(row)
        out.append(row)
    return out


def write_aliases(rows: Iterable[AliasRow], path: Path) -> None:
    """Validate and write ``rows`` in canonical order (by family, relation, ns, key)."""
    rows = sorted(set(rows), key=_sort_key)
    for row in rows:
        problems = row.problems()
        if problems:
            raise AliasError(f"{row.ns}:{row.alias}: {'; '.join(problems)}")
    _write_csv(
        path,
        COLUMNS,
        (tuple(v.isoformat() if isinstance(v, date) else v for v in astuple(row)) for row in rows),
    )


# --- miner output: data/alias-seeds/<miner>.csv ------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class AliasCandidate:
    """A proposed alias. ``target`` names the family by one of its universe keys."""

    alias: SourceKey
    target: SourceKey
    relation: str
    detail: str
    source: str  # miner name
    evidence: str  # commit, URL or file that shows it
    auto: bool  # eligible for auto-accept (google/fonts renames, Nerd unpatchedName)


def load_seeds(path: Path) -> list[AliasCandidate]:
    """Read a miner's seed file."""
    out = []
    for number, d in enumerate(_read_csv(path, SEED_COLUMNS), start=2):
        if d["auto"] not in ("true", "false"):
            raise AliasError(f"{path}:{number}: auto must be true or false")
        cand = AliasCandidate(
            alias=SourceKey(d["ns"], d["alias"]),
            target=SourceKey(d["target_ns"], d["target"]),
            relation=d["relation"],
            detail=d["detail"],
            source=d["source"],
            evidence=d["evidence"],
            auto=d["auto"] == "true",
        )
        bad = [k.ns for k in (cand.alias, cand.target) if k.ns not in NAMESPACES]
        if bad or cand.relation not in RELATIONS:
            raise AliasError(f"{path}:{number}: bad namespace {bad} or relation {cand.relation!r}")
        out.append(cand)
    return out


def write_seeds(cands: Iterable[AliasCandidate], path: Path) -> None:
    """Write a miner's seed file, sorted and de-duplicated."""
    _write_csv(
        path,
        SEED_COLUMNS,
        (
            (
                c.alias.key,
                c.alias.ns,
                c.target.ns,
                c.target.key,
                c.relation,
                c.detail,
                c.source,
                c.evidence,
                "true" if c.auto else "false",
            )
            for c in sorted(set(cands))
        ),
    )


# --- the stage (agent P4) -------------------------------------------------------------

# Relations that send a key's counts to one family (or, for "ineligible", out of the ranking).
MAPPING_RELATIONS = frozenset({"rename", "build", "package", "postscript", "ineligible"})
# Namespaces whose keys are family names as written; a name means one family in all of them.
NAME_NAMESPACES = frozenset(
    {"gf-family", "fot-name", "almanac-name", "font-name", "foundry-family"}
)
# Which relation an index entry keeps when rows name the same family twice.
_RELATION_ORDER = ("direct", "rename", "build", "package", "postscript", "ineligible")
# npm packages that their publisher names after a key the universe holds: (package prefix,
# universe namespace, index detail). Fontsource publishes font id <id> as @fontsource/<id>
# and @fontsource-variable/<id>; Expo publishes each Google family as its name in kebab
# case, which has the family name's match_key. The universe has no npm keys, so without
# these the npm, ecosyste.ms and Expo sources would match nothing.
PACKAGE_KEYS: tuple[tuple[str, str, str], ...] = (
    ("@fontsource/", "fs-id", "fontsource"),
    ("@fontsource-variable/", "fs-id", "fontsource"),
    ("@expo-google-fonts/", "gf-family", "expo"),
)
PACKAGE_NS = "npm"

SIBLING_DETAIL = "sibling"  # distinct-row detail: block every key that contains the alias
REJECTED_DETAIL = "rejected"  # distinct-row detail of an owner's rejection
SIBLING_SOURCE = "rule:sibling"
# Siblings that must never share counts (milestone-1 step 7). Each pair gives two
# distinct rows once both names are in the universe.
SIBLING_RULES: tuple[tuple[str, str], ...] = (
    ("Roboto", "Roboto Slab"),
    ("Fira Sans", "Fira Code"),
    ("Inter", "Inter Tight"),
    ("Noto Sans", "Noto Sans JP"),
)

CANDIDATES_FILE = "alias_candidates.jsonl"  # under build/stage/
QUEUE_FILE = "aliases.json"  # under build/stage/queues/


@dataclass(frozen=True, slots=True)
class AutoRule:
    """Candidates accepted without review: ``auto`` rows of ``miner`` with these relations."""

    miner: str
    relations: frozenset[str]


# Milestone-1 step 7: auto-accept only renames from google/fonts history and Nerd
# unpatchedName rows; the owner reviews everything else.
AUTO_RULES: dict[str, AutoRule] = {
    "gf_history_rename": AutoRule("gf_history", frozenset({"rename"})),
    "nerd_unpatched": AutoRule("nerd", frozenset({"build", "rename"})),
}
DEFAULT_AUTO_RULES = frozenset(AUTO_RULES)

# Why a candidate waits for the owner (QueueItem.reason).
QUEUE_REASONS = {
    "review": "no auto rule covers it",
    "target-unknown": "its target names no family in the universe or the table",
    "target-ambiguous": "its target names more than one family",
    "target-ineligible": "its target is an ineligible name",
    "conflict": "a row already maps the alias to another family",
    "other-family": "the alias is a name or key of another family: accepting merges two families",
    "competing": "candidates propose different families for this alias",
    "registry": "the old name's registered id is ambiguous, or the rename joins two registered families",
    "invalid": "the row it would make is invalid",
}


def _check_one_family(key: tuple[str, str], rows: list[AliasRow]) -> None:
    mapped = {r.family_id for r in rows if r.relation in MAPPING_RELATIONS}
    where = f"{key[0]}:{rows[0].alias}"
    if len(mapped) > 1:
        names = ", ".join(sorted(f or "(ineligible)" for f in mapped))
        raise AliasError(f"{where} maps to {len(mapped)} families: {names}")
    if mapped and any(r.relation == "bundle" for r in rows):
        raise AliasError(f"{where} is both a bundle and a single-family row")


@dataclass(frozen=True, slots=True)
class AliasTable:
    """``aliases.csv`` indexed for lookups."""

    rows: tuple[AliasRow, ...]
    _by_key: dict[tuple[str, str], tuple[AliasRow, ...]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        grouped: dict[tuple[str, str], list[AliasRow]] = defaultdict(list)
        for row in self.rows:
            grouped[(row.ns, match_key(row.alias))].append(row)
        for key, rows in grouped.items():
            _check_one_family(key, rows)
        object.__setattr__(self, "_by_key", {k: tuple(v) for k, v in grouped.items()})

    @classmethod
    def from_rows(cls, rows: Iterable[AliasRow]) -> AliasTable:
        """Index rows; raises ``AliasError`` when one (ns, match_key) maps to two families."""
        return cls(tuple(sorted(set(rows), key=_sort_key)))

    def lookup(self, key: SourceKey) -> tuple[AliasRow, ...]:
        """Rows whose ``ns`` equals ``key.ns`` and whose ``match_key`` equals ``match_key(key.key)``."""
        return self._by_key.get((key.ns, match_key(key.key)), ())


# --- blocks and the alias index ----------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class FamilyRef:
    """What this stage needs of a universe family: its id, current name and keys."""

    id: str
    name: str
    keys: tuple[SourceKey, ...] = ()


def family_refs(universe: Universe) -> tuple[FamilyRef, ...]:
    """The families of ``build/stage/universe.json``, dropped ones included, sorted by id."""
    return tuple(
        sorted(FamilyRef(f.id, f.family, tuple(f.keys)) for f in universe.families.values())
    )


def _ns_class(ns: str) -> str:
    return "name" if ns in NAME_NAMESPACES else ns


@dataclass(frozen=True, slots=True)
class Blocks:
    """The ``distinct`` rows as a check: which families a key may never map to."""

    exact: frozenset[tuple[str, str, str]] = frozenset()  # (ns or "name", match_key, family_id)
    contains: tuple[tuple[str, str], ...] = ()  # (match_key needle, family_id)

    @classmethod
    def of(cls, rows: Iterable[AliasRow]) -> Blocks:
        exact: set[tuple[str, str, str]] = set()
        contains: set[tuple[str, str]] = set()
        for row in rows:
            if row.relation != "distinct" or not row.family_id:
                continue
            needle = match_key(row.alias)
            if row.detail == SIBLING_DETAIL and needle:
                contains.add((needle, row.family_id))
            else:
                exact.add((_ns_class(row.ns), needle, row.family_id))
        return cls(frozenset(exact), tuple(sorted(contains)))

    def blocks(self, key: SourceKey, family_id: str) -> bool:
        """Whether a distinct row forbids ``key`` to map to ``family_id``."""
        if not family_id:
            return False
        mk = match_key(key.key)
        if (_ns_class(key.ns), mk, family_id) in self.exact:
            return True
        return any(fid == family_id and needle in mk for needle, fid in self.contains)


@dataclass(frozen=True, slots=True)
class Hit:
    """Where one index key points, and how it was found."""

    family_id: str  # "" for an ineligible name
    relation: str  # MAPPING_RELATIONS, or "direct" for a universe key or name
    detail: str
    level: int  # 0 row, 1 universe key, 2 row name, 3 universe name; the lowest level wins


@dataclass(frozen=True, slots=True)
class IndexBuild:
    """The alias index with what could not go in it."""

    hits: dict[tuple[str, str], Hit]
    ambiguous: frozenset[tuple[str, str]]  # keys that name two or more families: left out
    notes: tuple[str, ...]  # left-out rows and names, for the review
    violations: tuple[str, ...]  # rows or universe keys that break a distinct row (hard failure)

    def get(self, key: SourceKey) -> Hit | None:
        return self.hits.get((key.ns, match_key(key.key)))

    def is_ambiguous(self, key: SourceKey) -> bool:
        return (key.ns, match_key(key.key)) in self.ambiguous

    def index(self) -> dict[tuple[str, str], tuple[str, str, str]]:
        """The ``mapping.AliasIndex``: (ns, match_key) -> (family_id, relation, detail)."""
        return {k: (h.family_id, h.relation, h.detail) for k, h in sorted(self.hits.items())}


def _moved_ids(
    rows: tuple[AliasRow, ...],
    fams: Mapping[str, FamilyRef],
    direct: Mapping[tuple[str, str], set[str]],
    names: Mapping[str, set[str]],
) -> dict[str, str]:
    """Kept ids missing from this run's universe -> the family that holds their names now."""
    missing = sorted(
        {r.family_id for r in rows if r.relation in MAPPING_RELATIONS} - set(fams) - {""}
    )
    out = {}
    for fid in missing:
        now: set[str] = set()
        for r in rows:
            if r.family_id == fid and r.relation in MAPPING_RELATIONS:
                mk = match_key(r.alias)
                now |= direct.get((r.ns, mk), set())
                if r.ns in NAME_NAMESPACES:
                    now |= names.get(mk, set())
        if len(now) == 1:
            out[fid] = now.pop()
    return out


def build_index(rows: Iterable[AliasRow], families: Iterable[FamilyRef]) -> IndexBuild:
    """The alias index stage "map" reads: every universe key, every name, every mapping row.

    For each (ns, match_key) the first level that names anything wins: 0 a row in
    that namespace, 1 a universe key or an npm package named after one
    (``PACKAGE_KEYS``, relation ``package``), 2 a name row from another name
    namespace, 3 a universe family's name. A level naming two families leaves the
    key out (never guessed). Entries a distinct row forbids are left out; when the
    entry came from a row or a universe key, it is also a violation.
    """
    rows = tuple(rows)
    fams = {f.id: f for f in families}
    blocks = Blocks.of(rows)
    notes: set[str] = set()
    violations: set[str] = set()
    direct: dict[tuple[str, str], set[str]] = defaultdict(set)
    names: dict[str, set[str]] = defaultdict(set)
    packaged: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    for fam in fams.values():
        for k in fam.keys:
            direct[(k.ns, match_key(k.key))].add(fam.id)
            if k.ns in NAME_NAMESPACES:
                names[match_key(k.key)].add(fam.id)
            for prefix, ns, detail in PACKAGE_KEYS:
                if k.ns == ns:
                    packaged[(PACKAGE_NS, match_key(prefix + k.key))].add(
                        (fam.id, "package", detail)
                    )
        names[match_key(fam.name)].add(fam.id)

    moved = _moved_ids(rows, fams, direct, names)
    by_row: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    by_row_name: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in rows:
        if row.relation == "bundle":
            notes.add(f"bundle row {row.ns}:{row.alias} -> {row.family_id} is not in the index")
        if row.relation not in MAPPING_RELATIONS:
            continue
        fid = moved.get(row.family_id, row.family_id)
        if fid and fid not in fams:
            notes.add(f"row {row.ns}:{row.alias} -> {row.family_id}: no such family this run")
            continue
        value = (fid, row.relation, row.detail)
        by_row[(row.ns, match_key(row.alias))].add(value)
        if row.ns in NAME_NAMESPACES:
            by_row_name[match_key(row.alias)].add(value)

    name_keys = {(ns, mk) for mk in names.keys() | by_row_name.keys() for ns in NAME_NAMESPACES}
    hits: dict[tuple[str, str], Hit] = {}
    ambiguous: set[tuple[str, str]] = set()
    for key in sorted(by_row.keys() | direct.keys() | packaged.keys() | name_keys):
        ns, mk = key
        named = ns in NAME_NAMESPACES
        levels = (
            by_row.get(key, set()),
            {(fid, "direct", "") for fid in direct.get(key, ())} | packaged.get(key, set()),
            by_row_name.get(mk, set()) if named else set(),
            {(fid, "direct", "") for fid in names.get(mk, ())} if named else set(),
        )
        level, values = next((n, v) for n, v in enumerate(levels) if v)
        fids = sorted({v[0] for v in values})
        if len(fids) > 1:
            ambiguous.add(key)
            where = f"{ns}:{mk}" if level in (0, 1) else f"name {mk!r}"
            notes.add(f"{where} names {len(fids)} families ({', '.join(fids)}); left out")
            continue
        fid, relation, detail = min(values, key=lambda v: (_RELATION_ORDER.index(v[1]), v[2]))
        if blocks.blocks(SourceKey(ns, mk), fid):
            # A package key only repeats its universe key, which is reported on its own.
            hard = level == 0 or (level == 1 and key in direct)
            text = f"{ns}:{mk} -> {fid} breaks a distinct row"
            (violations if hard else notes).add(text + ("" if hard else "; left out"))
            continue
        hits[key] = Hit(fid, relation, detail, level)
    return IndexBuild(hits, frozenset(ambiguous), tuple(sorted(notes)), tuple(sorted(violations)))


# --- merge -------------------------------------------------------------------------------

Status = Literal["accepted", "known", "blocked", "queued", "rejected"]


@dataclass(frozen=True, slots=True, order=True)
class QueueItem:
    """One question for the owner (gate A); candidates that agree share an item."""

    id: str  # "A-" + 10 hex digits, stable for the same proposal
    alias: SourceKey
    target: SourceKey
    family_id: str | None  # the family it would map to; None when the target names none
    relation: str
    detail: str
    reason: str  # a QUEUE_REASONS key
    note: str
    sources: tuple[str, ...]
    evidence: tuple[str, ...]
    auto: bool
    current: SourceKey | None = None  # a rename keeping an old id: the new name, mapped to it too


@dataclass(frozen=True, slots=True)
class Outcome:
    """What merge did with one candidate."""

    candidate: AliasCandidate
    status: Status
    family_id: str | None
    reason: str = ""  # QUEUE_REASONS key when queued; why, when blocked
    item: str | None = None  # QueueItem.id when queued


@dataclass(frozen=True, slots=True)
class MergeResult:
    rows: tuple[AliasRow, ...]  # the whole table after the merge, canonical order
    queue: tuple[QueueItem, ...]  # sorted by id
    outcomes: tuple[Outcome, ...]  # one per distinct candidate, in candidate order


@dataclass(frozen=True, slots=True)
class _Assessment:
    status: Status
    family_id: str | None
    reason: str = ""
    note: str = ""
    rule: str | None = None
    current: SourceKey | None = None


@dataclass(frozen=True, slots=True)
class _Inputs:
    table: AliasTable
    base: IndexBuild
    blocks: Blocks
    registry: Mapping[str, Any]
    registered: Mapping[str, set[str]]  # match_key of a registered name -> ids
    universe_ids: frozenset[str]
    names: Mapping[str, str]  # family id -> this run's display name
    auto_rules: frozenset[str]
    today: date


def _queued(reason: str, family_id: str | None, note: str = "", **kw: Any) -> _Assessment:
    return _Assessment("queued", family_id, reason, note, **kw)


def _registered_names(registry: Mapping[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for fid, entry in registry.items():
        if not isinstance(entry, Mapping):
            continue
        for name in (entry.get("family"), entry.get("minted_from")):
            if isinstance(name, str) and name:
                out[match_key(name)].add(fid)
    return out


def _auto_rule(cand: AliasCandidate, auto_rules: frozenset[str]) -> str | None:
    if not cand.auto:
        return None
    for name in sorted(auto_rules):
        rule = AUTO_RULES[name]
        if rule.miner == cand.source and cand.relation in rule.relations:
            return name
    return None


def _row(cand: AliasCandidate, family_id: str, today: date, reviewed_by: str) -> AliasRow:
    return AliasRow(
        alias=cand.alias.key,
        ns=cand.alias.ns,
        family_id=family_id,
        relation=cand.relation,
        detail=cand.detail,
        source=cand.source,
        first_seen=today,
        reviewed_by=reviewed_by,
    )


def _current_row(key: SourceKey, family_id: str, source: str, day: date, by: str) -> AliasRow:
    """The rename row that maps a family's new name to its kept id.

    A name goes in ``font-name``, where the universe stage matches alias rows
    against every record's family name, so each record of the new name finds it.
    """
    ns = "font-name" if key.ns in NAME_NAMESPACES else key.ns
    return AliasRow(key.key, ns, family_id, "rename", "current", source, day, by)


def _target_family(cand: AliasCandidate, inp: _Inputs) -> _Assessment | str:
    """The family the candidate points at, or the queue assessment when there is none."""
    if cand.relation == "ineligible":
        return ""
    where = f"{cand.target.ns}:{cand.target.key}"
    if inp.base.is_ambiguous(cand.target):
        return _queued("target-ambiguous", None, f"{where} names more than one family")
    hit = inp.base.get(cand.target)
    if hit is None:
        return _queued("target-unknown", None, f"{where} is not in the universe or the table")
    if not hit.family_id:
        return _queued("target-ineligible", None, f"{where} is an ineligible name")
    return hit.family_id


def _new_name(cand: AliasCandidate, fam: str, inp: _Inputs) -> SourceKey:
    """The renamed family's new name, as a key the universe stage reads as a name.

    The target itself when it is a name; otherwise (a package or folder key) the
    family's display name, since the universe groups a family's records by name.
    """
    if cand.target.ns in NAME_NAMESPACES or fam not in inp.names:
        return cand.target
    return SourceKey("font-name", inp.names[fam])


def _kept_id(
    cand: AliasCandidate, fam: str, hit: Hit | None, inp: _Inputs
) -> _Assessment | str | None:
    """For a rename in a name namespace: the id registered under the old name, which
    the family keeps (methodology §2), or the queue assessment when that needs the owner.

    None when no other id carries the old name, or when the old name is now another
    family's (the caller asks about that). When accepting keeps the old id, the item's
    ``family_id`` is that id and ``current`` the new name, so the owner's "accept"
    keeps it too.
    """
    old = inp.registered.get(match_key(cand.alias.key), set()) - {fam}
    if not old:
        return None
    if len(old) > 1:
        return _queued("registry", fam, f"the old name matches ids {', '.join(sorted(old))}")
    kept = next(iter(old))
    if hit is not None and hit.family_id != kept:
        return None
    if inp.blocks.blocks(cand.alias, kept) or inp.blocks.blocks(_new_name(cand, fam, inp), kept):
        return _Assessment("blocked", kept, f"a distinct row forbids {kept}")
    if hit is not None:
        # A source still lists the old name (a stale cask or package): two live families.
        note = f"the old name is still {kept}; accepting folds {fam} into it and keeps that id"
        return _queued("other-family", kept, note, current=_new_name(cand, fam, inp))
    if kept in inp.universe_ids:
        return _queued("other-family", fam, f"{kept}, registered under the old name, is live")
    if fam in inp.registry:
        note = f"{fam} is registered too; accepting folds it into {kept}, the old name's id"
        return _queued("registry", kept, note, current=_new_name(cand, fam, inp))
    return kept


def _bundled(key: SourceKey, table: AliasTable) -> bool:
    """Whether a ``bundle`` row credits several families from ``key`` (a name: in any
    ``NAME_NAMESPACES`` namespace, since a name means the same thing in all of them)."""
    spaces = NAME_NAMESPACES if key.ns in NAME_NAMESPACES else (key.ns,)
    return any(
        r.relation == "bundle" for ns in spaces for r in table.lookup(SourceKey(ns, key.key))
    )


def _assess(cand: AliasCandidate, inp: _Inputs) -> _Assessment:
    if cand.relation in MAPPING_RELATIONS and _bundled(cand.alias, inp.table):
        # The table makes the key a bundle (D2): a row sending it to one family could never
        # join it (``AliasTable`` refuses both on one key), so the proposal is settled.
        return _Assessment("blocked", None, "a bundle row credits several families from it")
    fam = _target_family(cand, inp)
    if isinstance(fam, _Assessment):
        return fam
    if cand.relation in MAPPING_RELATIONS | {"bundle"} and inp.blocks.blocks(cand.alias, fam):
        return _Assessment("blocked", fam, f"a distinct row forbids {fam}")
    if cand.relation not in MAPPING_RELATIONS:
        same = [
            r
            for r in inp.table.lookup(cand.alias)
            if (r.relation, r.family_id) == (cand.relation, fam)
        ]
        return _Assessment("known", fam) if same else _queued("review", fam)

    current = None
    hit = inp.base.get(cand.alias)
    if hit is not None and hit.family_id == fam:
        return _Assessment("known", fam)
    if hit is None and inp.base.is_ambiguous(cand.alias):
        return _queued("other-family", fam, "it names more than one family")
    if cand.relation == "rename" and cand.alias.ns in NAME_NAMESPACES:
        kept = _kept_id(cand, fam, hit, inp)
        if isinstance(kept, _Assessment):
            return kept
        if kept is not None:
            fam, current = kept, _new_name(cand, fam, inp)
    if hit is not None:
        who = hit.family_id or "ineligible"
        if hit.level in (0, 2):
            return _queued("conflict", fam, f"a row maps it to {who}")
        return _queued("other-family", fam, f"it is a name or key of {who}")
    # Rows the index left out (their family is missing this run) still hold their keys.
    new = [_row(cand, fam, inp.today, "check")]
    if current is not None:
        new.append(_current_row(current, fam, cand.source, inp.today, "check"))
    elsewhere = sorted(
        {
            r.family_id or "(ineligible)"
            for row in new
            for r in inp.table.lookup(row.key)
            if r.relation in MAPPING_RELATIONS and r.family_id != fam
        }
    )
    if elsewhere:
        return _queued("conflict", fam, f"a row maps it to {', '.join(elsewhere)}", current=current)

    problems = [p for row in new for p in row.problems()]
    if problems:
        return _queued("invalid", fam, "; ".join(problems), current=current)
    rule = _auto_rule(cand, inp.auto_rules)
    if rule is None:
        return _queued("review", fam, current=current)
    return _Assessment("accepted", fam, rule=rule, current=current)


def _settle(assessed: list[tuple[AliasCandidate, _Assessment]]) -> None:
    """One family per alias: competing proposals all wait; agreeing ones follow the accepted."""
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, (cand, a) in enumerate(assessed):
        if (
            cand.relation in MAPPING_RELATIONS
            and a.status in ("accepted", "queued")
            and a.family_id is not None
        ):
            groups[(cand.alias.ns, match_key(cand.alias.key))].append(i)
    for members in groups.values():
        fams = {assessed[i][1].family_id or "" for i in members}
        if len(fams) > 1:
            note = "proposed: " + ", ".join(sorted(f or "(ineligible)" for f in fams))
            for i in members:
                cand, a = assessed[i]
                assessed[i] = (
                    cand,
                    replace(a, status="queued", reason="competing", note=note, rule=None),
                )
            continue
        winner = next((i for i in members if assessed[i][1].status == "accepted"), None)
        if winner is None:
            continue
        for i in members:
            if i != winner:
                cand, a = assessed[i]
                assessed[i] = (cand, replace(a, status="known", reason="", note="", rule=None))
    # Two renames to one new name that keep different old ids (a merge): the owner picks.
    renamed: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, (_, a) in enumerate(assessed):
        if a.status == "accepted" and a.current is not None:
            renamed[(_ns_class(a.current.ns), match_key(a.current.key))].append(i)
    for members in renamed.values():
        kept = sorted({assessed[i][1].family_id or "" for i in members})
        if len(kept) > 1:
            note = f"renames to one name would keep ids {', '.join(kept)}"
            for i in members:
                cand, a = assessed[i]
                assessed[i] = (
                    cand,
                    replace(a, status="queued", reason="competing", note=note, rule=None),
                )


def _item_id(key: tuple[str, ...]) -> str:
    return "A-" + hashlib.sha256(jsonio.canonical_bytes(list(key))).hexdigest()[:10]


def _item_key(cand: AliasCandidate, a: _Assessment) -> tuple[str, ...]:
    target = a.family_id if a.family_id is not None else f"?{cand.target.ns}:{cand.target.key}"
    return (cand.alias.ns, cand.alias.key, target, cand.relation, cand.detail)


def _sibling_rows(
    table: AliasTable, base: IndexBuild, fams: Mapping[str, FamilyRef], today: date
) -> list[AliasRow]:
    """``SIBLING_RULES`` as distinct rows, for the pairs whose families are known.

    A pair is skipped only when the table already blocks as much: an exact row
    (an owner's rejection, say) does not stand in for a sibling row, which covers
    every key that contains the name.
    """
    distinct = [r for r in table.rows if r.relation == "distinct" and r.family_id]
    contains = {(match_key(r.alias), r.family_id) for r in distinct if r.detail == SIBLING_DETAIL}
    exact = {(_ns_class(r.ns), match_key(r.alias), r.family_id) for r in distinct}
    out = []
    for pair in SIBLING_RULES:
        for name, other in (pair, pair[::-1]):  # ``name`` never maps to ``other``'s family
            hit = base.get(SourceKey("gf-family", other))
            if hit is None or not hit.family_id:
                continue
            fid, needle = hit.family_id, match_key(name)
            ref = fams.get(fid)
            own = {match_key(other)} | (
                {match_key(ref.name), *(match_key(k.key) for k in ref.keys)} if ref else set()
            )
            # A name inside the family's own names ("Roboto" in "Roboto Slab") blocks only itself.
            detail = SIBLING_DETAIL if not any(needle in o for o in own) else ""
            if (needle, fid) in contains or (not detail and ("name", needle, fid) in exact):
                continue
            out.append(
                AliasRow(
                    name,
                    "font-name",  # the universe stage applies font-name blocks to names
                    fid,
                    "distinct",
                    detail,
                    SIBLING_SOURCE,
                    today,
                    "auto:sibling",
                )
            )
    return out


def merge_detailed(
    table: AliasTable,
    cands: Iterable[AliasCandidate],
    auto_rules: frozenset[str],
    *,
    families: Iterable[FamilyRef] = (),
    registry: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> MergeResult:
    """``merge`` with the reasons: every candidate's outcome and the queue items.

    ``families`` resolve candidate targets (universe keys) to ids; without them
    only targets that are themselves table rows resolve. ``registry`` is the
    committed ``state/ids.json``, used to keep a renamed family's id. ``today``
    (the run date) is the ``first_seen`` of new rows.
    """
    unknown = sorted(set(auto_rules) - AUTO_RULES.keys())
    if unknown:
        raise ValueError(f"unknown auto rules {unknown}; known: {sorted(AUTO_RULES)}")
    day = today or clock.utc_today()
    fams = {f.id: f for f in families}
    registry = registry or {}
    base = build_index(table.rows, fams.values())
    rows = set(table.rows) | set(_sibling_rows(table, base, fams, day))
    inp = _Inputs(
        table=table,
        base=base,
        blocks=Blocks.of(rows),
        registry=registry,
        registered=_registered_names(registry),
        universe_ids=frozenset(fams),
        names={fid: f.name for fid, f in fams.items()},
        auto_rules=frozenset(auto_rules),
        today=day,
    )
    assessed = [(c, _assess(c, inp)) for c in sorted(set(cands))]
    _settle(assessed)

    buckets: dict[tuple[str, ...], list[tuple[AliasCandidate, _Assessment]]] = defaultdict(list)
    outcomes = []
    for cand, a in assessed:
        item = None
        if a.status == "accepted":
            assert a.family_id is not None
            assert a.rule is not None
            by = f"auto:{a.rule}"
            rows.add(_row(cand, a.family_id, day, by))
            if a.current is not None:
                rows.add(_current_row(a.current, a.family_id, cand.source, day, by))
        elif a.status == "queued":
            key = _item_key(cand, a)
            buckets[key].append((cand, a))
            item = _item_id(key)
        outcomes.append(Outcome(cand, a.status, a.family_id, a.reason, item))

    queue = []
    for key, members in buckets.items():
        cand, a = members[0]
        queue.append(
            QueueItem(
                id=_item_id(key),
                alias=cand.alias,
                target=cand.target,
                family_id=a.family_id,
                relation=cand.relation,
                detail=cand.detail,
                reason=a.reason,
                note=a.note,
                sources=tuple(sorted({c.source for c, _ in members})),
                evidence=tuple(sorted({c.evidence for c, _ in members})),
                auto=any(c.auto for c, _ in members),
                current=a.current,
            )
        )
    AliasTable(tuple(rows))  # never hand back a table that maps one key to two families
    return MergeResult(tuple(sorted(rows, key=_sort_key)), tuple(sorted(queue)), tuple(outcomes))


def merge(
    table: AliasTable,
    cands: Iterable[AliasCandidate],
    auto_rules: frozenset[str],
    *,
    families: Iterable[FamilyRef] = (),
    registry: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> tuple[list[AliasRow], list[AliasCandidate]]:
    """Accept auto-eligible candidates under ``auto_rules``; return (rows, review queue).

    ``rows`` is the whole table after the merge. A candidate is accepted only
    when an ``AUTO_RULES`` rule in ``auto_rules`` covers it, its target names
    exactly one family, no distinct row forbids it, and nothing already maps its
    alias elsewhere. Blocked and already-known candidates are in neither list.
    The keyword arguments are as in ``merge_detailed``.
    """
    result = merge_detailed(
        table, cands, auto_rules, families=families, registry=registry, today=today
    )
    queued = sorted({o.candidate for o in result.outcomes if o.status == "queued"})
    return list(result.rows), queued


# --- owner rulings (gate A) -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Decision:
    """The owner's ruling on one queue item."""

    accept: bool
    day: date  # the rulings file's date
    family_id: str | None = None  # corrections, for an accepted single item
    relation: str | None = None
    detail: str | None = None


GATE = "A"  # reviews.GATE_DIRS: data/reviews/aliases/
# The options of every gate A question, in ``Decision`` order: (a) accepts, (b) rejects,
# (c) decides nothing and keeps the question open (``reviews.REOPEN``).
QUESTION_OPTIONS = (
    "Accept: add the alias row",
    "Reject: the alias never maps to that family",
    "Research: look again and ask later",
)


def load_alias_rulings(paths: Paths) -> dict[str, Decision]:
    """Gate A decisions by queue item id.

    Read with ``reviews.load_rulings``: every file is checked against the rulings
    schema and read oldest first, so a later ruling wins. A malformed file raises
    ``AliasError`` naming it.
    """
    try:
        rulings = reviews.load_rulings(paths, GATE)
    except reviews.RulingError as exc:
        raise AliasError(str(exc)) from exc
    out: dict[str, Decision] = {}
    for ruling in rulings:
        for answer in ruling.answers:
            out.update(_decisions(ruling.day, answer))
    return out


def _decisions(day: date, answer: reviews.Answer) -> dict[str, Decision]:
    where = f"gate {GATE} ruling of {day.isoformat()}: [{answer.id}]"
    if answer.choice not in ("a", "b"):
        return {}
    values = dict(answer.values)
    items = values.get("items", (answer.id,) if answer.id.startswith("A-") else ())
    if not isinstance(items, tuple) or not all(isinstance(i, str) for i in items):
        raise AliasError(f"{where} items must be a list of queue ids")
    fixes = {k: values.get(k) for k in ("family_id", "relation", "detail")}
    if any(v is not None and not isinstance(v, str) for v in fixes.values()):
        raise AliasError(f"{where} family_id, relation and detail must be strings")
    if len(items) > 1 and any(v is not None for v in fixes.values()):
        raise AliasError(f"{where} corrections need a single item")
    return {i: Decision(answer.choice == "a", day, **fixes) for i in items}


def _question(item: Mapping[str, Any]) -> reviews.Question:
    alias, target, fam = item["alias"], item["target"], item["family_id"]
    kind = item["relation"] + (f"/{item['detail']}" if item["detail"] else "")
    if fam is None:
        where = f"an unresolved target ({target['ns']}:{target['key']})"
    else:
        where = fam or "nothing (ineligible)"
    parts = [
        f"Map {alias['ns']}:{alias['key']} to {where} as {kind}?",
        f"Proposed by {', '.join(item['sources'])}; evidence: {'; '.join(item['evidence'][:3])}.",
        f"Asked because {QUEUE_REASONS[item['reason']]}"
        + (f" ({item['note']})." if item["note"] else "."),
    ]
    if item["current"] is not None:
        cur = item["current"]
        parts.append(f"Accepting also maps {cur['ns']}:{cur['key']} to {fam}, so the id is kept.")
    if fam is None:
        parts.append("To accept, the ruling must add family_id.")
    # Only a plain proposal (nothing contradicts it) recommends accepting, so alike
    # ones are asked in batches; the others are asked one by one.
    recommended = 0 if item["reason"] == "review" else None
    return reviews.Question(GATE, item["id"], " ".join(parts), QUESTION_OPTIONS, recommended)


def questions(paths: Paths) -> list[reviews.Question]:
    """Gate A's questions: one per item of the last run's queue (``reviews.QUEUE_SOURCES``).

    Raises ``FileNotFoundError`` when stage "aliases" has not written its queue.
    """
    doc = jsonio.load(paths.queues / QUEUE_FILE)
    return [_question(item) for item in doc["items"]]


def apply_rulings(result: MergeResult, decisions: Mapping[str, Decision]) -> MergeResult:
    """Turn the owner's decisions on queue items into rows; undecided items stay queued.

    An accepted item becomes a row (``reviewed_by = "owner:<date>"``) and
    replaces any row that mapped its alias elsewhere; a rejected one with a known
    family becomes an exact ``distinct`` row (for an item that keeps an old id,
    on its new name: the new name is not that family). Raises ``AliasError``
    when a ruling makes an invalid row, maps a blocked alias, or accepts two
    families for one alias.
    """
    rows = set(result.rows)
    blocks = Blocks.of(rows)
    status: dict[str, Status] = {}
    set_by_owner: dict[tuple[str, str], str] = {}
    kept = []
    for item in result.queue:
        d = decisions.get(item.id)
        if d is None:
            kept.append(item)
            continue
        by, source = f"owner:{d.day.isoformat()}", "+".join(item.sources)
        if not d.accept:
            if item.family_id:
                # Rejecting a kept id means the new name is not the old family; the
                # old name may well still be that family's (a stale source lists it).
                no = item.current or item.alias
                ns = "font-name" if item.current and no.ns in NAME_NAMESPACES else no.ns
                rows.add(
                    AliasRow(
                        no.key, ns, item.family_id, "distinct", REJECTED_DETAIL, source, d.day, by
                    )
                )
            status[item.id] = "rejected"
            continue
        relation = d.relation or item.relation
        fam = "" if relation == "ineligible" else (d.family_id or item.family_id)
        if fam is None:
            kept.append(replace(item, note="accepted, but no family is known: add family_id"))
            continue
        detail = d.detail if d.detail is not None else item.detail
        row = AliasRow(item.alias.key, item.alias.ns, fam, relation, detail, source, d.day, by)
        problems = row.problems()
        if problems:
            raise AliasError(f"ruling on {item.id}: {'; '.join(problems)}")
        # Only a row that credits a family can break a block (a distinct row adds one).
        credits = relation in MAPPING_RELATIONS | {"bundle"}
        for key in (item.alias, item.current):
            if credits and key is not None and blocks.blocks(key, fam):
                raise AliasError(f"ruling on {item.id}: a distinct row forbids {key.key} -> {fam}")
        if relation in MAPPING_RELATIONS:
            key = (row.ns, match_key(row.alias))
            if set_by_owner.setdefault(key, fam) != fam:
                raise AliasError(f"rulings accept two families for {row.ns}:{row.alias}")
            rows = {
                r
                for r in rows
                if not (r.relation in MAPPING_RELATIONS and (r.ns, match_key(r.alias)) == key)
            }
        rows.add(row)
        if item.current is not None:
            rows.add(_current_row(item.current, fam, source, d.day, by))
        status[item.id] = "accepted"
    AliasTable(tuple(rows))
    outcomes = tuple(
        replace(o, status=status[o.item]) if o.item in status else o for o in result.outcomes
    )
    return MergeResult(tuple(sorted(rows, key=_sort_key)), tuple(kept), outcomes)


# --- mining --------------------------------------------------------------------------------


def _mine(ctx: StageContext) -> tuple[list[AliasCandidate], list[str]]:
    """Run the miners (not in replay), then read every seed file; also the failed miners."""
    from tff_catalog.aliases import miners  # the miners package imports this module

    seeds = ctx.paths.alias_seeds
    failed: list[str] = []
    chosen = (
        {}
        if ctx.options.replay
        else {n: m for n, m in miners.discover().items() if ctx.options.selects(n)}
    )
    if chosen:
        raw = RawDir(
            ctx.paths.raw_dir(ctx.run_date.isoformat()) / "aliases", keep=ctx.options.keep_raw
        )
        mctx = miners.MineContext(
            ctx.paths, ctx.store, raw, ctx.run_date, ctx.log.getChild("miners")
        )
        try:
            for name, miner in chosen.items():
                try:
                    found = list(miner.mine(mctx))
                except Exception:
                    # A miner's committed seeds stay valid; the queue file names the failure.
                    ctx.log.exception("miner %s failed; keeping its committed seeds", name)
                    failed.append(name)
                    continue
                wrong = sorted({c.source for c in found} - {name})
                if wrong:
                    raise AliasError(f"miner {name} proposed candidates as {wrong}")
                write_seeds(found, seeds / f"{name}.csv")
                ctx.log.info("miner %s: %d candidates", name, len(set(found)))
        finally:
            if raw.path.exists() and not raw.keep:
                raw.cleanup()
    out: set[AliasCandidate] = set()
    for path in sorted(seeds.glob("*.csv")) if seeds.is_dir() else ():
        rows = load_seeds(path)
        wrong = sorted({c.source for c in rows} - {path.stem})
        if wrong:
            # The auto rules trust a candidate's source, so it must be the file's miner.
            raise AliasError(f"{path}: rows name source {wrong}, not {path.stem!r}")
        out.update(rows)
    return sorted(out), failed


def mine_all(ctx: StageContext) -> list[AliasCandidate]:
    """Run every miner (``aliases.miners.discover()``) and read ``data/alias-seeds/``.

    Each miner's candidates replace its ``data/alias-seeds/<name>.csv``; then
    every seed file is read, hand-made ones included, and a row's ``source``
    must be its file's name. ``--only`` limits which miners run. In replay
    (``--from-snapshots``) no miner runs: the committed seeds are the input, so
    replay needs no network. A miner that fails keeps its committed seeds.
    """
    return _mine(ctx)[0]


# --- the stage -------------------------------------------------------------------------------


def _load_universe(paths: Paths) -> Universe:
    path = stageio.stage_path(paths, "universe")
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing: run `tff-catalog universe` first")
    return stageio.load_stage(paths, "universe")


def _load_table(paths: Paths) -> AliasTable:
    if not paths.aliases_csv.is_file():
        return AliasTable(())
    return AliasTable.from_rows(load_aliases(paths.aliases_csv))


def _resolve(ctx: StageContext, cands: list[AliasCandidate]) -> tuple[MergeResult, IndexBuild]:
    families = family_refs(_load_universe(ctx.paths))
    result = merge_detailed(
        _load_table(ctx.paths),
        cands,
        DEFAULT_AUTO_RULES,
        families=families,
        registry=ctx.state.ids,
        today=ctx.run_date,
    )
    result = apply_rulings(result, load_alias_rulings(ctx.paths))
    built = build_index(result.rows, families)
    if built.violations:
        raise AliasError(
            "the alias index breaks distinct rows (methodology §9 known answers):\n  "
            + "\n  ".join(built.violations)
        )
    return result, built


def _key_json(key: SourceKey | None) -> dict[str, str] | None:
    return None if key is None else {"ns": key.ns, "key": key.key}


def _candidate_json(o: Outcome) -> dict[str, Any]:
    c = o.candidate
    return {
        "alias": _key_json(c.alias),
        "target": _key_json(c.target),
        "relation": c.relation,
        "detail": c.detail,
        "source": c.source,
        "evidence": c.evidence,
        "auto": c.auto,
        "status": o.status,
        "family_id": o.family_id,
        "reason": o.reason,
        "item": o.item,
    }


def _candidate_of(d: Mapping[str, Any]) -> AliasCandidate:
    return AliasCandidate(
        alias=SourceKey(d["alias"]["ns"], d["alias"]["key"]),
        target=SourceKey(d["target"]["ns"], d["target"]["key"]),
        relation=d["relation"],
        detail=d["detail"],
        source=d["source"],
        evidence=d["evidence"],
        auto=d["auto"],
    )


def _item_json(item: QueueItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "alias": _key_json(item.alias),
        "target": _key_json(item.target),
        "family_id": item.family_id,
        "relation": item.relation,
        "detail": item.detail,
        "reason": item.reason,
        "note": item.note,
        "sources": list(item.sources),
        "evidence": list(item.evidence),
        "auto": item.auto,
        "current": _key_json(item.current),
    }


def _write_outputs(
    ctx: StageContext, result: MergeResult, built: IndexBuild, failed: list[str]
) -> None:
    from tff_catalog import mapping  # mapping may import this module

    jsonio.dump_jsonl(
        (_candidate_json(o) for o in result.outcomes), ctx.paths.stage / CANDIDATES_FILE
    )
    stageio.dump_stage(ctx.paths, "alias_index", mapping.entries_of(built.index()))
    counts: dict[str, int] = defaultdict(int)
    for o in result.outcomes:
        counts[o.status] += 1
    # Rows this run uses that data/aliases.csv does not carry yet (or drops): until
    # `--apply` and a rerun from stage "universe", the universe does not know them.
    unapplied = set(result.rows) ^ set(_load_table(ctx.paths).rows)
    doc = {
        "items": [_item_json(i) for i in result.queue],
        "counts": {
            "candidates": len(result.outcomes),
            **{s: counts[s] for s in ("accepted", "known", "blocked", "queued", "rejected")},
            "items": len(result.queue),
            "rows": len(result.rows),
            "unapplied": len(unapplied),
            "index": len(built.hits),
        },
        "failed_miners": sorted(failed),
        "notes": list(built.notes),
    }
    jsonio.dump(doc, ctx.paths.queues / QUEUE_FILE)


def run(ctx: StageContext) -> None:
    """Stage "aliases": write ``stage/alias_candidates.jsonl``, ``stage/alias_index.json``
    and ``stage/queues/aliases.json``. Never writes ``data/aliases.csv``.

    The index is written with ``stageio.dump_stage(paths, "alias_index",
    mapping.entries_of(index))``, the format stage "map" reads.

    Reads ``build/stage/universe.json`` (stage "universe" first),
    ``data/aliases.csv``, the seeds, gate A rulings and ``state/ids.json``.
    Auto-accepted and owner-accepted rows are in this run's index before
    ``--apply`` writes them to the table. Raises ``AliasError`` when the index
    would break a distinct row, as when the universe folds Roboto Slab into Roboto.
    """
    cands, failed = _mine(ctx)
    result, built = _resolve(ctx, cands)
    _write_outputs(ctx, result, built, failed)
    ctx.log.info(
        "aliases: %d candidates, %d rows, %d index keys, %d queued",
        len(result.outcomes),
        len(result.rows),
        len(built.hits),
        len(result.queue),
    )


def _read_queue(ctx: StageContext) -> dict[str, Any] | None:
    path = ctx.paths.queues / QUEUE_FILE
    return jsonio.load(path) if path.is_file() else None


def cmd_queue(ctx: StageContext, *, count: bool = False) -> int:
    """``aliases --queue [--count]``: print the review queue (or its size)."""
    doc = _read_queue(ctx)
    if doc is None:
        print("tff-catalog aliases: no queue yet; run `tff-catalog aliases` first", file=sys.stderr)
        return 1
    items = doc["items"]
    if count:
        print(len(items))
        return 0
    for it in items:
        alias, fam = it["alias"], it["family_id"]
        target = fam if fam is not None else f"? {it['target']['ns']}:{it['target']['key']}"
        kind = it["relation"] + (f"/{it['detail']}" if it["detail"] else "")
        note = f"  ({it['note']})" if it["note"] else ""
        cur = it["current"]
        also = f"  +{cur['ns']}:{cur['key']}" if cur is not None else ""
        print(
            f"{it['id']}  {it['reason']:<17}  {alias['ns']}:{alias['key']}{also}"
            f" -> {target or '(ineligible)'}  [{kind}; {', '.join(it['sources'])}]{note}"
        )
    return 0


def unapplied(paths: Paths) -> int:
    """``counts.unapplied`` of the last stage run's queue file (0 without one)."""
    path = paths.queues / QUEUE_FILE
    if not path.is_file():
        return 0
    return int(jsonio.load(path).get("counts", {}).get("unapplied", 0))


@dataclass(frozen=True, slots=True)
class Applied:
    rows: int  # rows in data/aliases.csv now
    added: int
    replaced: int
    queued: int  # items still waiting for the owner


def apply_accepted(ctx: StageContext) -> Applied:
    """Merge accepted rows and owner rulings into ``data/aliases.csv`` (``--apply``).

    Uses the candidates of the last stage run (``build/stage/alias_candidates.jsonl``),
    so it never mines, and rewrites the stage outputs to match the new table.
    Raises ``FileNotFoundError`` when there are none.
    """
    path = ctx.paths.stage / CANDIDATES_FILE
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing; run `tff-catalog aliases` first")
    cands = [_candidate_of(d) for d in jsonio.iter_jsonl(path)]
    before = set(_load_table(ctx.paths).rows)
    result, built = _resolve(ctx, cands)
    write_aliases(result.rows, ctx.paths.aliases_csv)
    previous = _read_queue(ctx) or {}
    _write_outputs(ctx, result, built, list(previous.get("failed_miners", [])))
    after = set(result.rows)
    return Applied(len(result.rows), len(after - before), len(before - after), len(result.queue))


def cmd_apply(ctx: StageContext) -> int:
    """``aliases --apply``: merge accepted rows and owner rulings into ``data/aliases.csv``.

    Uses the candidates of the last ``tff-catalog aliases`` run
    (``build/stage/alias_candidates.jsonl``), so it never mines, and rewrites
    the stage outputs to match the new table.
    """
    try:
        done = apply_accepted(ctx)
    except FileNotFoundError as exc:
        print(f"tff-catalog aliases: {exc}", file=sys.stderr)
        return 1
    print(
        f"{ctx.paths.aliases_csv}: {done.rows} rows ({done.added} added, "
        f"{done.replaced} replaced); {done.queued} still queued"
    )
    return 0
