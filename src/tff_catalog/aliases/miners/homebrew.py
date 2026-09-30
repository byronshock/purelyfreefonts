"""Alias miner "homebrew": renamed Homebrew font casks (milestone-1 step 7, design-m1 §2.3).

Homebrew's analytics keep counting installs under a cask's old token for a
year after a rename (``font-open-dyslexic-nerd-font`` had 1,271 installs in
the 365-day window of 2026-09-26, after it became
``font-opendyslexic-nerd-font``), so each old token needs a row that sends
its counts to the renamed cask's family.

**Evidence.** Homebrew's own two lists of renames, read from a
``homebrew_casks`` snapshot (the universe collector fetches them; this miner
makes no request):

- ``renames.json``: the font entries of homebrew-cask's ``cask_renames.json``,
  ``{old token: new token}``;
- ``casks.jsonl.gz``: each font cask's ``old_tokens``, the API's list of the
  tokens the cask had before.

The snapshot is the one the run's parse stage used (``build/stage/snapshots.json``),
so the targets are the keys the universe was built from; without that file, or
without a ``homebrew_casks`` entry in it, the newest complete one on or before
the run date. A snapshot whose ``collector_version`` is newer than
``FORMAT_VERSION`` fails the miner instead of being misread.

**Candidates.** One per (old token, current font cask): alias
``brew-cask:<old>``, target ``brew-cask:<new>`` (the cask's universe key),
relation ``rename``, empty detail, source ``homebrew``, never ``auto``: the
owner reviews every Homebrew rename (milestone-1 step 7). A chain of renames
(``a -> b -> c``) ends at the first current font cask on it. The evidence is
``cask_renames.json`` when that file lists the pair, else the new cask's API
page, whose ``old_tokens`` lists it. An old token that two casks claim gives
both candidates, and the stage queues them as competing. An old Nerd cask
token is a ``rename`` with no build detail, like any other: miner ``nerd`` is
the one list of Nerd casks, and this miner does not read "nerd" in a token.

**Left out** (logged): an entry whose chain reaches no current font cask
(``font-finagler`` became the app ``fontfinagler``), and an old token that is
a current cask itself, because a new cask took the name back and the installs
are its own. Nothing is paired by name likeness: only Homebrew's lists count.

The stage writes the candidates to ``data/alias-seeds/homebrew.csv``
(``aliases.write_seeds``); this miner never writes ``data/aliases.csv``.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence, Set
from datetime import date
from typing import Any, ClassVar

from tff_catalog.aliases import AliasCandidate
from tff_catalog.aliases.miners import MineContext
from tff_catalog.paths import Paths, StoreNotConfigured
from tff_catalog.records import SourceKey
from tff_catalog.store import Snapshot

NAME = "homebrew"
SOURCE = "homebrew_casks"  # the store source read (collectors/universe/homebrew_casks.py)
FORMAT_VERSION = 1  # the collector_version whose extracts this miner reads; a newer one raises
CASKS_EXTRACT = "casks.jsonl.gz"  # that collector's extracts
RENAMES_EXTRACT = "renames.json"
NAMESPACE = "brew-cask"
RELATION = "rename"
AUTO = False  # milestone-1 step 7: only gf_history and nerd rows are auto-accepted
# Stable links, so the seed file changes only when a rename does.
RENAMES_EVIDENCE = "https://github.com/Homebrew/homebrew-cask/blob/main/cask_renames.json"
CASK_EVIDENCE = "https://formulae.brew.sh/api/cask/{token}.json"


def _text(value: object) -> str | None:
    """A string with non-space content, stripped; else None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def read_casks(rows: Iterable[Any]) -> dict[str, tuple[str, ...]]:
    """Each font cask's token -> its old tokens (sorted, without the token itself).

    ``rows`` are the ``casks.jsonl.gz`` extract rows. A row without a string
    ``token`` is skipped; a repeated token keeps the union of its old tokens.
    """
    out: dict[str, set[str]] = {}
    for row in rows:
        token = _text(row.get("token")) if isinstance(row, dict) else None
        if token is None:
            continue
        olds = row.get("old_tokens")
        found = out.setdefault(token, set())
        if isinstance(olds, list):
            found.update(t for v in olds if (t := _text(v)) is not None and t != token)
    return {token: tuple(sorted(olds)) for token, olds in sorted(out.items())}


def read_renames(doc: object) -> dict[str, str]:
    """The ``{old token: new token}`` string pairs of ``renames.json``, sorted.

    Raises ``ValueError`` when ``doc`` is not a JSON object.
    """
    if not isinstance(doc, dict):
        raise ValueError(f"{RENAMES_EXTRACT}: expected a JSON object, got {type(doc).__name__}")
    out = {}
    for key, value in doc.items():
        old, new = _text(key), _text(value)
        if old is not None and new is not None and old != new:
            out[old] = new
    return dict(sorted(out.items()))


def resolve(old: str, renames: Mapping[str, str], casks: Set[str]) -> str | None:
    """The current cask that ``old`` was renamed to: the first cask on its chain of
    renames, or None when the chain ends elsewhere or loops."""
    seen = {old}
    new = renames[old]
    while new not in casks:
        if new in seen or new not in renames:
            return None
        seen.add(new)
        new = renames[new]
    return new


def rename_pairs(
    casks: Mapping[str, Sequence[str]], renames: Mapping[str, str]
) -> tuple[dict[tuple[str, str], str], list[str]]:
    """(old token, current cask) -> evidence, and a note for each entry left out."""
    pairs: dict[tuple[str, str], str] = {}
    notes: list[str] = []
    for old, new in sorted(renames.items()):
        if old in casks:
            notes.append(f"{old} -> {new}: {old} is a current cask again")
            continue
        end = resolve(old, renames, casks.keys())
        if end is None:
            notes.append(f"{old} -> {new}: the renames reach no current font cask")
            continue
        pairs[(old, end)] = RENAMES_EVIDENCE
    for token, olds in sorted(casks.items()):
        for old in olds:
            if old in casks:
                notes.append(f"{old} -> {token} (old_tokens): {old} is a current cask again")
                continue
            pairs.setdefault((old, token), CASK_EVIDENCE.format(token=token))
    return dict(sorted(pairs.items())), notes


def candidates(
    casks: Mapping[str, Sequence[str]],
    renames: Mapping[str, str],
    log: logging.Logger | None = None,
) -> list[AliasCandidate]:
    """The rename candidates of the cask list and ``renames.json``, sorted (module docstring)."""
    pairs, notes = rename_pairs(casks, renames)
    targets: dict[str, set[str]] = defaultdict(set)
    for old, new in pairs:
        targets[old].add(new)
    if log is not None:
        for note in notes:
            log.info("%s: left out %s", NAME, note)
        for old, news in sorted(targets.items()):
            if len(news) > 1:
                log.warning("%s: %s is an old token of %s", NAME, old, ", ".join(sorted(news)))
    return sorted(
        AliasCandidate(
            alias=SourceKey(NAMESPACE, old),
            target=SourceKey(NAMESPACE, new),
            relation=RELATION,
            detail="",
            source=NAME,
            evidence=evidence,
            auto=AUTO,
        )
        for (old, new), evidence in pairs.items()
    )


def parsed_day(paths: Paths) -> date | None:
    """The day of the ``homebrew_casks`` snapshot the run's parse stage used, if it wrote one."""
    from tff_catalog import parse  # imported here: parse pulls in the collector framework

    if not (paths.stage / parse.SNAPSHOTS_FILE).is_file():
        return None
    used = parse.load_snapshots(paths).get(SOURCE)
    return None if used is None else used.snapshot


def pick_snapshot(ctx: MineContext) -> Snapshot:
    """The ``homebrew_casks`` snapshot to read (module docstring).

    Raises ``StoreNotConfigured`` without a store, ``FileNotFoundError``
    without the snapshot and ``ValueError`` for a newer extract format; the
    stage then keeps the committed seeds.
    """
    if ctx.store is None:
        ctx.paths.require_store()  # raises with the fix-it message
        raise StoreNotConfigured("this mine context has no snapshot store")
    day = parsed_day(ctx.paths)
    if day is not None:
        snap = ctx.store.snapshot(SOURCE, day)
        if snap is None:
            raise FileNotFoundError(
                f"no complete {SOURCE} snapshot {day} in {ctx.store.root}, "
                "though the parse stage used it (build/stage/snapshots.json)"
            )
    else:
        snap = ctx.store.latest(SOURCE, ctx.run_date)
        if snap is None:
            raise FileNotFoundError(
                f"no {SOURCE} snapshot on or before {ctx.run_date}: "
                f"run `tff-catalog fetch --only {SOURCE}` first"
            )
    if snap.manifest.collector_version > FORMAT_VERSION:
        raise ValueError(
            f"{SOURCE} {snap.date}: extract version {snap.manifest.collector_version} "
            f"is newer than the {FORMAT_VERSION} this miner reads"
        )
    return snap


def read_snapshot(
    snap: Snapshot, log: logging.Logger | None = None
) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """(casks, renames) of a ``homebrew_casks`` snapshot; a missing ``renames.json`` is empty."""
    casks = read_casks(snap.iter_jsonl(CASKS_EXTRACT))
    if not casks:
        raise ValueError(f"{snap.path / CASKS_EXTRACT}: no font cask")
    if snap.has(RENAMES_EXTRACT):
        renames = read_renames(snap.load_json(RENAMES_EXTRACT))
    else:
        renames = {}
        if log is not None:
            log.warning("%s: %s %s has no %s", NAME, SOURCE, snap.date, RENAMES_EXTRACT)
    return casks, renames


class HomebrewMiner:
    """Old Homebrew font cask tokens -> the casks they were renamed to."""

    name: ClassVar[str] = NAME

    def mine(self, ctx: MineContext) -> list[AliasCandidate]:
        """Candidates from the run's ``homebrew_casks`` snapshot (module docstring)."""
        snap = pick_snapshot(ctx)
        casks, renames = read_snapshot(snap, ctx.log)
        found = candidates(casks, renames, ctx.log)
        ctx.log.info(
            "%s: %d candidates from %s %s (%d font casks, %d font renames)",
            NAME,
            len(found),
            SOURCE,
            snap.date,
            len(casks),
            len(renames),
        )
        return found


MINER = HomebrewMiner()
