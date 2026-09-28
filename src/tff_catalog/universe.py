"""Stage "universe": the candidate families (milestone-1 step 4). Owner: agent P1.

Builds one family per Google-Fonts-style family from the universe collectors'
records, using ``data/aliases.csv`` to fold renames, builds and packages into
their family. Each family keeps the id in ``state/ids.json``; a new family gets
``names.mint_id`` once, and a rename only adds an alias. Non-text families are
kept with a ``drop`` reason code, never silently removed.

Writes ``build/stage/universe.json``, ``build/universe.md`` (per-source counts,
drop reasons, the keys kept out and the registry's changes), ``build/review/
universe-keys.md`` (every key mapped, several thousand rows: gitignored, owner
ruling of 2026-09-26, U_rules) and ``build/state/ids.json``.
Committed reports show sources whose ``publish_raw`` is false (Google and
Fonts Over Time, rulings T2 and T4) only as ranks or z scores, never as values.

**How a record finds its family.** Every rule is exact; nothing is guessed. The
alias table is read with the stage "aliases" rules (``tff_catalog.aliases``):
rows match by ``ns`` and ``match_key``, a name means the same family in every
``NAME_NAMESPACES`` namespace, and ``Blocks`` says which family a ``distinct``
row forbids a key or a name (as a ``font-name`` key).

1. Its key. A row in the key's own namespace with a ``FOLD_RELATIONS``
   relation (rename, build, package, postscript) puts the record in that row's
   family; an ``ineligible`` or ``bundle`` row keeps the key out of every family
   (``Universe.excluded``). A fold row that a distinct row contradicts leaves the
   key unmapped.

2. Otherwise its family name. An ``ineligible`` or ``bundle`` row on that name in
   any name namespace keeps the record out too (so a ``font-name`` row for
   "SF Pro" also keeps out the cask ``font-sf-pro`` named "SF Pro"); such a row
   next to a fold row on the same name leaves the key unmapped. Records whose
   names have the same ``match_key`` are one group, and a name the record itself
   asserts (``UniverseRecord.names`` with a fold relation) joins that name's
   group too. A group takes the id its names point to:

   - fold rows in a name namespace (rank ``RANK_ALIAS``);
   - the registry's current ``family`` names (``RANK_CURRENT``);
   - the registry's ``minted_from`` names, and the names of records that step 1
     placed, their own and the ones they assert (``RANK_OLDER``). So a key row
     that folds a renamed Fontsource id into the old family takes the other
     sources' records of the new name with it.

   No id: the group is a new family, and ``mint_id`` mints one from its display
   name. Several ids: each record is resolved on its own name, where the best
   rank wins, or else on the one id that all the names the records of its name
   assert give; a record placed so lends its names to the rest, round by round.
   A name left with a tie, or with no id, stays unmapped. A family a distinct
   row forbids is never a candidate: the record is resolved without it, and gets
   a new family when nothing is left. That family's id then shares the registry
   name with the forbidden one, which keeps the name (``_pick``); any other
   shared current name is a tie.

3. Then it settles. Minting treats a new group as one family, but the next run
   finds the new ids in the registry and places record by record, so the stage
   places again against the registry it proposes (``next_ids``) until nothing
   moves; the next run, from that registry once merged, places every key the
   same way. Keys that keep moving (contradictory asserted names) are held back
   as unmapped.

A key whose records end up in several families (a formula or cask that ships
several families) is kept out of every family's ``keys`` as ``several-families``;
its records still count towards each family's sources and URLs, and each family
lists the key and its record's name in ``shared``, so the later stages find that
record's files and the key's license facts (``Universe.record_owner``,
``Universe.sharing``). So is a key with
one record kept out by name and another placed. Unmapped keys are in no family's
``keys`` either; ``run`` fails when there are any. Each key is listed once:
unmapped first, then kept out, then in a family.

**Display name.** The ``family`` of the best record: a name that another
record of the family asserts (an old name or a build; a build name only when
the record asserting it ranks at least as high by the next two rules) last,
then live before queued,
deprecated and delisted, then ``SOURCE_PRIORITY``. The registry keeps
``minted_from`` and ``first_seen`` for ever and takes the current display name
(``next_ids``). An intermediate name is not kept: the report lists every
changed display name so its old name can go into ``data/aliases.csv``. A
``rename`` row in a name namespace with detail ``display`` (``DISPLAY_DETAIL``)
names its family's display name outright, for a family no record names the
way the owner ruled (ProggyCleanTT is shown as ProggyClean, 2026-09-26); two
such rows for one family raise ``UniverseError``.

**Drop.** A family is dropped when any record gives a non-text code (icon,
emoji, symbol, barcode, math, music, non-font), or when every record says
``proprietary``: the licenses stage settles a lone license claim. A record
without a code whose primary script is a symbol script (``SCRIPT_DROPS``) counts
as that code.
"""

import copy
import weakref
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

from tff_catalog import clock, jsonio, stageio, state
from tff_catalog.keys import match_key
from tff_catalog.names import ID_PATTERN, mint_id
from tff_catalog.records import (
    DROP_REASONS,
    DropReason,
    SourceKey,
    UniverseRecord,
    read_jsonl,
    sort_key,
)

if TYPE_CHECKING:
    from tff_catalog.aliases import AliasRow, AliasTable
    from tff_catalog.stages import StageContext

REPORT_NAME = "universe.md"  # under build/, committed
KEYS_REPORT = Path("review") / "universe-keys.md"  # under build/, gitignored: every key mapped
NAME_KEY_NS = "font-name"  # a family name, asked of the alias table as a key
FOLD_RELATIONS = frozenset({"rename", "build", "package", "postscript"})
# Display-name preference; sources not listed come after, by name.
SOURCE_PRIORITY = (
    "google_metadata",
    "google_repo",
    "fontsource",
    "foundries",
    "fontist",
    "nerdfonts",
    "homebrew_casks",
)
# ISO 15924 symbol scripts: a family whose primary script is one of these is not text.
SCRIPT_DROPS: dict[str, str] = {"Zsye": "emoji", "Zmth": "math", "Zsym": "symbol"}
UNANIMOUS_DROPS = frozenset({"proprietary"})  # dropped only when every record says so
DROP_ORDER: tuple[str, ...] = get_args(DropReason)  # the code kept when records disagree
SEVERAL_FAMILIES = "several-families"
BUNDLE = "bundle"
INELIGIBLE = "ineligible"
DISTINCT = "distinct"
DISPLAY_DETAIL = "display"  # a rename row's detail: the row's alias is the family's display name
SETTLE_ROUNDS = 8  # build_universe: rounds of placing against the proposed registry

_STATUS_RANK = {"live": 0, "queued": 1, "deprecated": 2, "delisted": 3}


class UniverseError(RuntimeError):
    """The universe cannot be built, or left keys unmapped."""


@dataclass(frozen=True, slots=True)
class Family:
    id: str
    family: str  # current display name
    keys: tuple[SourceKey, ...]  # every universe key that maps here, sorted
    sources: tuple[str, ...]  # universe collectors that list it, sorted
    first_seen: date
    minted_from: str  # the name the id was minted from
    drop: str | None = None  # records.DROP_REASONS code when out of the universe
    urls: tuple[tuple[str, str], ...] = ()  # (role, url), sorted
    # (key, record family) of the records placed here whose key is kept out as
    # "several-families" (a formula or cask that ships several families), sorted: the
    # key maps no counts, but that record's files and the key's license are this family's.
    shared: tuple[tuple[SourceKey, str], ...] = ()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Universe:
    families: dict[str, Family]  # by id, including dropped ones
    unmapped: tuple[SourceKey, ...]  # universe keys that reached no family (must be empty)
    # Keys kept out of every family, sorted, each once: "ineligible:<reason>[+<reason>]"
    # and "bundle" (alias rows on the key or the name), "several-families" (records of
    # one key in several families); reasons of one key's records joined with ", ".
    excluded: tuple[tuple[SourceKey, str], ...] = ()

    def record_owner(self, key: SourceKey, family: str) -> str | None:
        """The family a record of ``key`` named ``family`` describes: the family holding the
        key exactly, else the one its several-families key placed that record in."""
        exact, _ = _key_index(self)
        return exact.get(key) or _shared_index(self)[0].get((key, family))

    def sharing(self, key: SourceKey) -> tuple[str, ...]:
        """The families a several-families key reached (``Family.shared``), sorted."""
        return _shared_index(self)[1].get(key, ())

    def by_key(self, key: SourceKey) -> Family | None:
        """The family holding ``key``: the exact key, else the only key with the same
        ``ns`` and ``match_key``. None when no family, or several, hold it."""
        exact, folded = _key_index(self)
        fid = exact.get(key)
        if fid is None:
            fids = folded.get((key.ns, match_key(key.key)), ())
            fid = fids[0] if len(fids) == 1 else None
        return None if fid is None else self.families[fid]

    def eligible(self) -> dict[str, Family]:
        """Families without a ``drop`` reason."""
        return {fid: f for fid, f in self.families.items() if f.drop is None}


# Built once per Universe (treated as immutable) and freed with it.
_KEY_INDEXES: dict[int, tuple[dict[SourceKey, str], dict[tuple[str, str], tuple[str, ...]]]] = {}


def _key_index(
    u: Universe,
) -> tuple[dict[SourceKey, str], dict[tuple[str, str], tuple[str, ...]]]:
    found = _KEY_INDEXES.get(id(u))
    if found is None:
        exact: dict[SourceKey, str] = {}
        folded: dict[tuple[str, str], set[str]] = defaultdict(set)
        for fid, fam in u.families.items():
            for key in fam.keys:
                exact.setdefault(key, fid)
                folded[(key.ns, match_key(key.key))].add(fid)
        found = (exact, {k: tuple(sorted(v)) for k, v in folded.items()})
        _KEY_INDEXES[id(u)] = found
        weakref.finalize(u, _KEY_INDEXES.pop, id(u), None)
    return found


_SHARED_INDEXES: dict[
    int, tuple[dict[tuple[SourceKey, str], str], dict[SourceKey, tuple[str, ...]]]
] = {}


def _shared_index(
    u: Universe,
) -> tuple[dict[tuple[SourceKey, str], str], dict[SourceKey, tuple[str, ...]]]:
    found = _SHARED_INDEXES.get(id(u))
    if found is None:
        by_record: dict[tuple[SourceKey, str], str] = {}
        by_key: dict[SourceKey, set[str]] = defaultdict(set)
        for fid, fam in sorted(u.families.items()):
            for key, name in fam.shared:
                by_record.setdefault((key, name), fid)
                by_key[key].add(fid)
        found = (by_record, {k: tuple(sorted(v)) for k, v in by_key.items()})
        _SHARED_INDEXES[id(u)] = found
        weakref.finalize(u, _SHARED_INDEXES.pop, id(u), None)
    return found


# --- the alias table, as the universe reads it -------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Rules:
    """``aliases.csv`` rows by (ns, match_key), the way ``AliasTable.lookup`` matches."""

    fold: dict[tuple[str, str], frozenset[str]]
    ineligible: dict[tuple[str, str], frozenset[str]]  # -> reasons
    bundle: frozenset[tuple[str, str]]
    name_ns: frozenset[str]  # aliases.NAME_NAMESPACES
    blocks: Any  # aliases.Blocks: the distinct rows
    split: frozenset[str]  # the families distinct rows name: something was split off them
    display: dict[str, str]  # family id -> display name the owner ruled (DISPLAY_DETAIL)

    @classmethod
    def of(cls, rows: Iterable[AliasRow]) -> _Rules:
        # Imported here: stage "aliases" reads the universe, so it may import this module.
        from tff_catalog.aliases import NAME_NAMESPACES, Blocks

        rows = tuple(rows)
        fold: dict[tuple[str, str], set[str]] = defaultdict(set)
        ineligible: dict[tuple[str, str], set[str]] = defaultdict(set)
        bundle: set[tuple[str, str]] = set()
        display: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            if (
                row.relation == "rename"
                and row.detail == DISPLAY_DETAIL
                and row.ns in NAME_NAMESPACES
            ):
                display[row.family_id].add(row.alias)
            k = (row.ns, match_key(row.alias))
            if row.relation in FOLD_RELATIONS:
                fold[k].add(row.family_id)
            elif row.relation == INELIGIBLE:
                ineligible[k].add(row.detail)
            elif row.relation == BUNDLE:
                bundle.add(k)
        return cls(
            fold={k: frozenset(v) for k, v in fold.items()},
            ineligible={k: frozenset(v) for k, v in ineligible.items()},
            bundle=frozenset(bundle),
            name_ns=NAME_NAMESPACES,
            blocks=Blocks.of(rows),
            split=frozenset(r.family_id for r in rows if r.relation == DISTINCT and r.family_id),
            display=_one_display(display),
        )

    def key_verdict(self, key: SourceKey) -> tuple[str, str]:
        """("alias", family_id), ("exclude", why), ("unmapped", "") or ("name", "")."""
        return self._verdict({(key.ns, match_key(key.key))}, alias=True)

    def name_verdict(self, name: str) -> tuple[str, str]:
        """For a record the key left to its name: the rows on that name in every name
        namespace. ("exclude", why), ("unmapped", "") or ("name", ""); a fold row on the
        name only links it (``_name_links``), so it never gives ("alias", ...)."""
        mk = match_key(name)
        verdict = self._verdict({(ns, mk) for ns in self.name_ns}, alias=False)
        return ("name", "") if verdict[0] == "alias" else verdict

    def _verdict(self, ks: set[tuple[str, str]], *, alias: bool) -> tuple[str, str]:
        fold = frozenset().union(*(self.fold.get(k, frozenset()) for k in ks))
        reasons = frozenset().union(*(self.ineligible.get(k, frozenset()) for k in ks))
        bundle = any(k in self.bundle for k in ks)
        if fold:
            # AliasTable refuses these within one namespace, but not across the name
            # namespaces; a contradiction is never guessed.
            if (alias and len(fold) > 1) or reasons or bundle:
                return ("unmapped", "")
            return ("alias", next(iter(fold)) if alias else "")
        if reasons:
            return ("exclude", f"{INELIGIBLE}:{'+'.join(sorted(reasons))}")
        if bundle:
            return ("exclude", BUNDLE)
        return ("name", "")

    def bars(self, r: UniverseRecord, fid: str) -> bool:
        """Whether a distinct row forbids ``r`` (its key or its name) in family ``fid``."""
        return self.blocks.blocks(r.key, fid) or self.name_bars(r.family, fid)

    def name_bars(self, name: str, fid: str) -> bool:
        return self.blocks.blocks(SourceKey(NAME_KEY_NS, name), fid)


def _one_display(found: Mapping[str, set[str]]) -> dict[str, str]:
    twice = sorted(f"{fid} ({', '.join(sorted(n))})" for fid, n in found.items() if len(n) > 1)
    if twice:
        raise UniverseError(f"more than one display-name row for {'; '.join(twice)}")
    return {fid: next(iter(names)) for fid, names in sorted(found.items())}


# --- grouping by name ----------------------------------------------------------------------------


class _Groups:
    """Union-find over name keys (``match_key`` of a family name)."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, n: str) -> None:
        self._parent.setdefault(n, n)

    def find(self, n: str) -> str:
        self.add(n)
        while self._parent[n] != n:
            self._parent[n] = self._parent[self._parent[n]]
            n = self._parent[n]
        return n

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb))
            self._parent[hi] = lo

    def members(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for n in list(self._parent):
            out[self.find(n)].add(n)
        return out


def _folded_names(r: UniverseRecord) -> list[str]:
    """Name keys of the names ``r`` asserts with a fold relation."""
    return [mk for alias, rel in r.names if rel in FOLD_RELATIONS and (mk := match_key(alias))]


# How strongly a name points to an id; a lower rank wins when a name points to several.
RANK_ALIAS = 0  # a fold row in a name namespace (reviewed)
RANK_CURRENT = 1  # the registry's current display name
RANK_OLDER = 2  # the registry's minted_from, or a placed record's own or asserted name


def _name_links(
    ids: Mapping[str, Mapping[str, Any]],
    rules: _Rules,
    anchored: Iterable[tuple[UniverseRecord, str]],
) -> dict[str, dict[str, int]]:
    """Name key -> {id: rank}, without the ids a distinct row forbids the name."""
    links: dict[str, dict[str, int]] = defaultdict(dict)
    for (ns, mk), fids in rules.fold.items():
        if ns in rules.name_ns:
            for fid in fids:
                _link(links, rules, mk, fid, RANK_ALIAS)
    for fid, entry in ids.items():
        _link(links, rules, entry.get("family"), fid, RANK_CURRENT)
        _link(links, rules, entry.get("minted_from"), fid, RANK_OLDER)
    _link_placed(links, rules, anchored)
    return links


def _link(
    links: dict[str, dict[str, int]], rules: _Rules, name: object, fid: str, rank: int
) -> None:
    if isinstance(name, str) and (mk := match_key(name)) and not rules.name_bars(mk, fid):
        links[mk][fid] = min(rank, links[mk].get(fid, rank))


def _link_placed(
    links: dict[str, dict[str, int]],
    rules: _Rules,
    placed: Iterable[tuple[UniverseRecord, str]],
) -> None:
    """Link the names of placed records to their ids (``RANK_OLDER``): their own
    name, so a source listing the same name without an alias row does not mint a
    second family of that name, and the names they assert."""
    for r, fid in placed:
        for mk in (match_key(r.family), *_folded_names(r)):
            _link(links, rules, mk, fid, RANK_OLDER)


def _pick(
    own: Mapping[str, int], rules: _Rules, ids: Mapping[str, Mapping[str, Any]]
) -> str | None:
    """The one id a name points to: the best-ranked, or None when that is a tie.

    Registry families that share a current name because a distinct row split one
    off are no tie: the family a distinct row names keeps the name (of several,
    the one first seen, when that is one), and records of the other reach it
    because that row forbids them the first. Any other shared current name is a tie.
    """
    if not own:
        return None
    best = min(own.values())
    tied = sorted(fid for fid, rank in own.items() if rank == best)
    if len(tied) == 1:
        return tied[0]
    split = [fid for fid in tied if fid in rules.split]
    if best != RANK_CURRENT or not split:
        return None
    first = min(_registry_date(ids[fid], date.max) for fid in split)
    oldest = [fid for fid in split if _registry_date(ids[fid], date.max) == first]
    return oldest[0] if len(oldest) == 1 else None


@dataclass(frozen=True, slots=True)
class _Target:
    """Where a record goes: ``id`` (a known family id) or ``new`` (a group minted this run)."""

    record: UniverseRecord
    kind: str  # "id" or "new"
    value: str  # the family id, or the group's key


@dataclass(slots=True)
class _Placement:
    targets: list[_Target]
    unmapped: set[SourceKey]
    excluded: dict[SourceKey, set[str]]  # key -> why


def _place(
    recs: list[UniverseRecord], rules: _Rules, ids: Mapping[str, Mapping[str, Any]]
) -> _Placement:
    out = _Placement([], set(), defaultdict(set))
    anchored: list[tuple[UniverseRecord, str]] = []
    named: list[tuple[UniverseRecord, str]] = []
    for r in recs:
        verdict, value = rules.key_verdict(r.key)
        if verdict == "name":
            verdict, value = rules.name_verdict(r.family)
        if verdict == "alias" and rules.bars(r, value):
            verdict = "unmapped"
        if verdict == "alias":
            anchored.append((r, value))
        elif verdict == "exclude":
            out.excluded[r.key].add(value)
        elif verdict == "unmapped" or not match_key(r.family):
            out.unmapped.add(r.key)
        else:
            named.append((r, match_key(r.family)))
    out.targets += [_Target(r, "id", fid) for r, fid in anchored]

    links = _name_links(ids, rules, anchored)
    groups = _Groups()
    for r, mk in named:
        groups.add(mk)
        for other in _folded_names(r):
            groups.union(mk, other)
    group_ids = {
        root: {fid for n in names for fid in links.get(n, {})}
        for root, names in groups.members().items()
    }
    asserted: dict[str, set[str]] = defaultdict(set)  # name -> what its records assert
    for r, mk in named:
        asserted[mk].update(_folded_names(r))
    pending: list[tuple[UniverseRecord, str, int]] = []
    for r, mk in named:
        root = groups.find(mk)
        candidates = group_ids[root]
        if len(candidates) == 1 and not rules.bars(r, only := next(iter(candidates))):
            out.targets.append(_Target(r, "id", only))
        elif not candidates:
            out.targets.append(_Target(r, "new", root))
        else:
            pending.append((r, mk, len(candidates)))
    # Several ids, or the group's one id is forbidden: resolve each record on its own
    # name, else on the names that the records of its name assert. A record placed so
    # lends its names to the rest, as one step 1 placed does, until nothing moves;
    # each round sees only the links of earlier rounds.
    while pending:
        tried = [
            (r, mk, n, _resolve(r, mk, asserted[mk], links, rules, ids)) for r, mk, n in pending
        ]
        done = [(r, fid) for r, _, _, fid in tried if fid is not None]
        if not done:
            break
        out.targets += [_Target(r, "id", fid) for r, fid in done]
        _link_placed(links, rules, done)
        pending = [(r, mk, n) for r, mk, n, fid in tried if fid is None]
    for r, mk, n in pending:
        if n == 1 and not _open(r, mk, links, rules):
            out.targets.append(_Target(r, "new", f"{mk}\x00apart"))
        else:
            out.unmapped.add(r.key)
    return out


def _open(
    r: UniverseRecord, mk: str, links: Mapping[str, Mapping[str, int]], rules: _Rules
) -> dict[str, int]:
    """The ids ``links`` gives name ``mk``, without those a distinct row forbids ``r``."""
    return {fid: rank for fid, rank in links.get(mk, {}).items() if not rules.bars(r, fid)}


def _resolve(
    r: UniverseRecord,
    mk: str,
    asserted: Iterable[str],
    links: Mapping[str, Mapping[str, int]],
    rules: _Rules,
    ids: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """The id of ``r`` on its own name ``mk``, else the one id that every name in
    ``asserted`` (what the records of name ``mk`` assert) gives.

    Per name, so records of one name never part. None when a name is a tie, or the
    asserted names disagree: never guessed.
    """
    own = _open(r, mk, links, rules)
    if own:
        return _pick(own, rules, ids)
    picks = {_pick(o, rules, ids) for n in sorted(asserted) if (o := _open(r, n, links, rules))}
    return picks.pop() if len(picks) == 1 else None


# --- assembling families -------------------------------------------------------------------------


def _source_rank(source: str) -> tuple[int, str]:
    if source in SOURCE_PRIORITY:
        return (SOURCE_PRIORITY.index(source), source)
    return (len(SOURCE_PRIORITY), source)


def _best(recs: list[UniverseRecord]) -> UniverseRecord:
    """The record whose ``family`` is the display name (module docstring).

    An old name demotes a record whoever asserts it. A build name demotes only records
    that rank no higher than the one asserting it: Nerd Fonts' folder names "FiraMono"
    and "Recursive" are build names of its "Fira" and "Recursive Mono", and must not
    demote Google's live "Fira Mono" and "Recursive".
    """

    def standing(r: UniverseRecord) -> tuple[int, tuple[int, str]]:
        return (_STATUS_RANK[r.status], _source_rank(r.source))

    asserted = [
        (standing(r) if rel == "build" else None, mk)
        for r in recs
        for alias, rel in r.names
        if rel in FOLD_RELATIONS and (mk := match_key(alias)) and mk != match_key(r.family)
    ]

    def demoted(r: UniverseRecord) -> bool:
        mine, mk = standing(r), match_key(r.family)
        return any(name == mk and (by is None or by <= mine) for by, name in asserted)

    return min(
        recs,
        key=lambda r: (
            demoted(r),
            _STATUS_RANK[r.status],
            _source_rank(r.source),
            r.key.ns,
            r.key.key,
            r.family,
        ),
    )


def _drop_code(r: UniverseRecord) -> str | None:
    if r.drop is not None:
        return r.drop
    return SCRIPT_DROPS.get(r.primary_script or "")


def family_drop(recs: Iterable[UniverseRecord]) -> str | None:
    """The family's drop code (module docstring), or None when it stays in."""
    codes = [_drop_code(r) for r in recs]
    given = {c for c in codes if c is not None}
    nontext = given - UNANIMOUS_DROPS
    if nontext:
        return min(nontext, key=DROP_ORDER.index)
    if given and None not in codes:
        return min(given, key=DROP_ORDER.index)
    return None


def check_registry(ids: Mapping[str, Any]) -> None:
    """Raise ``UniverseError`` unless ``ids`` looks like ``state/ids.json``.

    Every id matches ``names.ID_PATTERN`` and maps to an object whose ``family``
    and ``minted_from`` are strings and whose ``first_seen`` is an ISO date, each
    when present (``next_ids`` fills in a missing one). A bad ``first_seen`` would
    otherwise read as the run date, so the family's first sighting would move.
    """
    for fid, entry in ids.items():
        where = f"state/ids.json: {fid!r}"
        if not isinstance(fid, str) or not ID_PATTERN.match(fid):
            raise UniverseError(f"{where} is not a family id ({ID_PATTERN.pattern})")
        if not isinstance(entry, Mapping):
            raise UniverseError(f"{where} must map to an object, not {entry!r}")
        for name in ("family", "minted_from"):
            if name in entry and not isinstance(entry[name], str):
                raise UniverseError(f"{where}: {name} must be a string, not {entry[name]!r}")
        first = entry.get("first_seen")
        if first is not None and not (isinstance(first, str) and _is_iso(first)):
            raise UniverseError(f"{where}: first_seen must be YYYY-MM-DD, not {first!r}")


def _is_iso(text: str) -> bool:
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def _registry_date(entry: Mapping[str, Any], default: date) -> date:
    value = entry.get("first_seen")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return default


def _mint(
    targets: list[_Target], ids: Mapping[str, Mapping[str, Any]], rules: _Rules
) -> tuple[dict[str, list[UniverseRecord]], dict[str, str]]:
    """Records by family id, minting ids for new groups; also minted id -> minted name.

    Groups are minted in name order, so the ids do not depend on input order. A
    record that a distinct row forbids the id its group got is minted again, with
    the other such records of its name, after the pass.
    """
    by_id: dict[str, list[UniverseRecord]] = defaultdict(list)
    new: dict[str, list[UniverseRecord]] = defaultdict(list)
    for t in targets:
        (by_id if t.kind == "id" else new)[t.value].append(t.record)
    taken = set(ids)
    minted: dict[str, str] = {}
    pending = list(new.values())
    while pending:
        again: dict[str, list[UniverseRecord]] = defaultdict(list)
        for group in sorted(pending, key=_mint_order):
            name = _best(group).family
            fid = mint_id(name, taken)
            taken.add(fid)
            kept = [r for r in group if not rules.bars(r, fid)]
            if kept:
                minted[fid] = name
                by_id[fid] += kept
            for r in group:
                if rules.bars(r, fid):
                    again[match_key(r.family)].append(r)
        pending = list(again.values())
    return by_id, minted


def _mint_order(group: list[UniverseRecord]) -> tuple[str, str, SourceKey]:
    best = _best(group)
    return (match_key(best.family), best.family, min(r.key for r in group))


def _assemble(
    by_id: dict[str, list[UniverseRecord]],
    minted: dict[str, str],
    ids: Mapping[str, Mapping[str, Any]],
    run_date: date,
    kept_out: set[SourceKey],
    display: Mapping[str, str],
) -> tuple[dict[str, Family], set[SourceKey]]:
    """The families, and the keys found in several of them.

    A key in ``kept_out`` (another record of it found no family, or was kept out by
    the alias table) or in several families stays out of every family's ``keys``;
    its placed records still count towards sources, URLs and the drop.
    """
    homes: dict[SourceKey, set[str]] = defaultdict(set)
    for fid, recs in by_id.items():
        for r in recs:
            homes[r.key].add(fid)
    several = {k for k, fids in homes.items() if len(fids) > 1} - kept_out
    families = {}
    for fid in sorted(by_id):
        recs = by_id[fid]
        name = display.get(fid) or _best(recs).family
        entry = ids.get(fid, {})
        minted_from = entry.get("minted_from") or entry.get("family") or minted.get(fid) or name
        families[fid] = Family(
            id=fid,
            family=name,
            keys=tuple(sorted({r.key for r in recs} - several - kept_out)),
            sources=tuple(sorted({r.source for r in recs})),
            first_seen=_registry_date(entry, run_date),
            minted_from=str(minted_from),
            drop=family_drop(recs),
            urls=tuple(sorted({(role, url) for r in recs for role, url in r.urls})),
            shared=tuple(sorted({(r.key, r.family) for r in recs if r.key in several})),
        )
    return families, several


def build_universe(
    recs: Iterable[UniverseRecord],
    aliases: AliasTable,
    ids: Mapping[str, Mapping[str, Any]],
    *,
    run_date: date | None = None,
) -> Universe:
    """Group universe records into families. Deterministic; never guesses a match.

    ``aliases`` is read through its ``rows``, with the rules of stage "aliases"
    (module docstring); ``ids`` is the committed registry, never modified.
    ``run_date`` is the ``first_seen`` of families minted now (default: today, UTC).
    Input order does not matter, and the next run, from the registry this one
    proposes (``next_ids``), places every key the same way (``_settle``). Raises
    ``ValueError`` for a record whose ``drop`` is not in ``records.DROP_REASONS``,
    and ``UniverseError`` for a malformed registry (``check_registry``).
    """
    check_registry(ids)
    day = run_date or clock.utc_today()
    records = sorted(set(recs), key=sort_key)
    for r in records:
        if r.drop is not None and r.drop not in DROP_REASONS:
            raise ValueError(f"{r.source} {r.key}: drop {r.drop!r} not in {sorted(DROP_REASONS)}")
    rules = _Rules.of(aliases.rows)
    held: set[SourceKey] = set()
    while True:
        u, moving = _settle(records, rules, ids, day, held)
        if not moving:
            return u
        held |= moving


def _settle(
    records: list[UniverseRecord],
    rules: _Rules,
    ids: Mapping[str, Mapping[str, Any]],
    day: date,
    held: set[SourceKey],
) -> tuple[Universe, set[SourceKey]]:
    """Place, then place again against the registry this run proposes, until nothing moves.

    Minting treats a new group as one family, but the next run reads the new ids
    from the registry and places record by record; this makes the two agree, so
    that run keeps every id. Returns the universe and, when it has not settled
    after ``SETTLE_ROUNDS``, the keys that still move (contradictory asserted
    names): the caller holds them back as unmapped and starts again.
    """
    u, where = _build(records, rules, ids, day, held)
    moving: set[SourceKey] = set()
    for _ in range(SETTLE_ROUNDS):
        again, now = _build(records, rules, next_ids(u, ids), day, held)
        if again == u:
            return u, set()
        moving = {r.key for r in records if where.get(r) != now.get(r)}
        u, where = again, now
    # Only records move a family; "every key" is a backstop that cannot loop.
    return u, moving or {r.key for r in records} - held


def _build(
    records: list[UniverseRecord],
    rules: _Rules,
    ids: Mapping[str, Mapping[str, Any]],
    day: date,
    held: set[SourceKey],
) -> tuple[Universe, dict[UniverseRecord, str]]:
    """One placement against registry ``ids``; also each placed record's family id.

    Records of a ``held`` key are left unmapped (``_settle``).
    """
    placed = _place([r for r in records if r.key not in held], rules, ids)
    placed.unmapped |= held & {r.key for r in records}
    by_id, minted = _mint(placed.targets, ids, rules)
    kept_out = placed.unmapped | set(placed.excluded)
    families, several = _assemble(by_id, minted, ids, day, kept_out, rules.display)
    # One entry per key: unmapped wins, then the alias table's reasons.
    whys = {k: v for k, v in placed.excluded.items() if k not in placed.unmapped}
    whys |= {k: {SEVERAL_FAMILIES} for k in several}
    u = Universe(
        families=families,
        unmapped=tuple(sorted(placed.unmapped)),
        excluded=tuple(sorted((k, ", ".join(sorted(v))) for k, v in whys.items())),
    )
    return u, {r: fid for fid, recs in by_id.items() for r in recs}


def next_ids(u: Universe, ids: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """``ids.json`` for ``build/state/``: the old registry plus newly minted ids (append-only).

    No id is ever removed. An existing entry keeps ``minted_from`` and
    ``first_seen`` and takes the family's current display name as ``family``.
    """
    out = {fid: copy.deepcopy(dict(entry)) for fid, entry in ids.items()}
    for fid, fam in u.families.items():
        entry = out.setdefault(fid, {})
        entry["family"] = fam.family
        entry.setdefault("minted_from", fam.minted_from)
        entry.setdefault("first_seen", fam.first_seen.isoformat())
    return dict(sorted(out.items()))


# --- the report ----------------------------------------------------------------------------------


def _cell(value: object) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _table(header: tuple[str, ...], rows: Iterable[tuple[object, ...]]) -> list[str]:
    lines = [f"| {' | '.join(header)} |", f"|{'|'.join('---' for _ in header)}|"]
    lines += [f"| {' | '.join(_cell(v) for v in row)} |" for row in rows]
    return lines if len(lines) > 2 else ["None."]


def _section(title: str, lines: list[str]) -> list[str]:
    return ["", f"## {title}", "", *lines]


def report(u: Universe, previous: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    """``build/universe.md``: per-source counts, drop reasons and the keys kept out.

    With ``previous`` (the committed registry) it also lists the ids minted
    this run, the display names that changed and the registry ids no source listed.
    Every key's family is in ``keys_report`` instead (U_rules: the committed report
    stays short).
    """
    fams = [u.families[fid] for fid in sorted(u.families)]
    dropped = [f for f in fams if f.drop is not None]
    n_keys = sum(len(f.keys) for f in fams)
    out = [
        "# Candidate universe",
        "",
        "Written by `tff-catalog universe` (milestone-1 step 4). Every universe key is in a "
        "family, kept out by the alias table, or unmapped.",
        "",
        *_table(
            ("Measure", "Count"),
            [
                ("Families", len(fams)),
                ("Eligible families", len(fams) - len(dropped)),
                ("Dropped families", len(dropped)),
                ("Keys in a family", n_keys),
                ("Keys kept out of every family", len(u.excluded)),
                ("Unmapped keys (must be 0)", len(u.unmapped)),
            ],
        ),
    ]
    out += _section("Per source", _per_source(fams))
    out += _section("Keys per namespace", _per_namespace(u))
    out += _section("Drop reasons", _drop_counts(dropped))
    out += _section(
        "Dropped families",
        _table(
            ("Id", "Family", "Reason", "Sources"),
            ((f.id, f.family, f.drop, ", ".join(f.sources)) for f in dropped),
        ),
    )
    out += _section(
        "Unmapped keys", _table(("Namespace", "Key"), ((k.ns, k.key) for k in u.unmapped))
    )
    out += _section(
        "Keys kept out of every family",
        _table(("Namespace", "Key", "Why"), ((k.ns, k.key, why) for k, why in u.excluded)),
    )
    if previous is not None:
        out += _registry_sections(fams, previous)
    out += [
        "",
        f"Every key's family is listed in build/{KEYS_REPORT.as_posix()} (not committed).",
    ]
    return "\n".join(out) + "\n"


def keys_report(u: Universe) -> str:
    """``build/review/universe-keys.md``: every key mapped, with its family."""
    fams = [u.families[fid] for fid in sorted(u.families)]
    out = [
        "# Every universe key mapped",
        "",
        "Written by `tff-catalog universe` (milestone-1 step 4); not committed.",
    ]
    out += _section(
        "Every key mapped",
        _table(
            ("Namespace", "Key", "Id", "Family"),
            sorted((k.ns, k.key, f.id, f.family) for f in fams for k in f.keys),
        ),
    )
    return "\n".join(out) + "\n"


def _per_source(fams: list[Family]) -> list[str]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f in fams:
        for source in f.sources:
            counts[source][0 if f.drop is None else 1] += 1
    return _table(
        ("Source", "Families", "Eligible", "Dropped"),
        ((s, sum(c), c[0], c[1]) for s, c in sorted(counts.items())),
    )


def _per_namespace(u: Universe) -> list[str]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for f in u.families.values():
        for k in f.keys:
            counts[k.ns][0] += 1
    for k, _ in u.excluded:
        counts[k.ns][1] += 1
    for k in u.unmapped:
        counts[k.ns][2] += 1
    return _table(
        ("Namespace", "In a family", "Kept out", "Unmapped"),
        ((ns, *c) for ns, c in sorted(counts.items())),
    )


def _drop_counts(dropped: list[Family]) -> list[str]:
    counts = dict.fromkeys(DROP_ORDER, 0)
    for f in dropped:
        counts[str(f.drop)] = counts.get(str(f.drop), 0) + 1
    return _table(("Reason", "Families"), counts.items())


def _registry_sections(fams: list[Family], previous: Mapping[str, Mapping[str, Any]]) -> list[str]:
    seen = {f.id for f in fams}
    if not previous:  # the first run: every id is new, and the key table lists them all
        out = _section(
            "New ids this run",
            [
                f"First run: all {len(fams)} ids are new (build/{KEYS_REPORT.as_posix()} lists them)."
            ],
        )
    else:
        out = _section(
            "New ids this run",
            _table(
                ("Id", "Family", "Minted from"),
                ((f.id, f.family, f.minted_from) for f in fams if f.id not in previous),
            ),
        )
    out += _section(
        "Display names changed this run (record the old name in data/aliases.csv)",
        _table(
            ("Id", "Registry name", "Current name"),
            (
                (f.id, previous[f.id].get("family", ""), f.family)
                for f in fams
                if f.id in previous and previous[f.id].get("family") != f.family
            ),
        ),
    )
    out += _section(
        "Registry ids no source listed this run",
        _table(
            ("Id", "Registry name"),
            ((fid, previous[fid].get("family", "")) for fid in sorted(previous) if fid not in seen),
        ),
    )
    return out


# --- files and the stage ------------------------------------------------------------------------


def load_universe(path: Path) -> Universe:
    """Read ``build/stage/universe.json``."""
    return stageio.load(path, Universe)


def dump_universe(u: Universe, path: Path) -> None:
    """Write ``build/stage/universe.json`` deterministically."""
    stageio.dump(u, path)


def universe_records(directory: Path) -> list[UniverseRecord]:
    """Every ``UniverseRecord`` in ``build/stage/records/<source>.jsonl`` (baselines skipped)."""
    out: list[UniverseRecord] = []
    for path in sorted(Path(directory).glob("*.jsonl")):
        if "@" in path.name:  # <source>@<baseline>.jsonl: lifetime-counter baselines
            continue
        out += [r for r in read_jsonl(path) if isinstance(r, UniverseRecord)]
    return out


def run(ctx: StageContext) -> None:
    """Stage "universe".

    Reads ``build/stage/records/``, ``data/aliases.csv`` and the committed
    ``state/ids.json``; writes the stage file, the report and ``build/state/ids.json``.
    Raises ``UniverseError`` after writing them when a key is unmapped.
    """
    # Imported here: the aliases stage reads the universe, so it may import this module.
    from tff_catalog.aliases import AliasTable, load_aliases

    paths = ctx.paths
    recs = universe_records(paths.records)
    if not recs:
        raise UniverseError(
            f"no universe records in {paths.records}: run `tff-catalog parse` first"
        )
    rows = load_aliases(paths.aliases_csv) if paths.aliases_csv.is_file() else []
    u = build_universe(recs, AliasTable(rows=tuple(rows)), ctx.state.ids, run_date=ctx.run_date)
    stageio.dump_stage(paths, "universe", u)
    state.write_part(paths, "ids", next_ids(u, ctx.state.ids), stage="universe")
    jsonio.atomic_write(paths.build / REPORT_NAME, report(u, ctx.state.ids).encode("utf-8"))
    (paths.build / KEYS_REPORT).parent.mkdir(parents=True, exist_ok=True)
    jsonio.atomic_write(paths.build / KEYS_REPORT, keys_report(u).encode("utf-8"))
    eligible = len(u.eligible())
    ctx.log.info(
        "%d families (%d eligible, %d dropped), %d new ids, %d keys kept out, %d unmapped",
        len(u.families),
        eligible,
        len(u.families) - eligible,
        len(set(u.families) - set(ctx.state.ids)),
        len(u.excluded),
        len(u.unmapped),
    )
    if u.unmapped:
        shown = ", ".join(f"{k.ns}:{k.key}" for k in u.unmapped[:10])
        raise UniverseError(
            f"{len(u.unmapped)} universe key(s) reached no family ({shown}); "
            f"see {paths.build / REPORT_NAME} and add alias rows"
        )
