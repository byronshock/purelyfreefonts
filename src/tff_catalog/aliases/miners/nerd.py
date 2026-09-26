"""Alias miner "nerd": the Nerd Fonts build names in ``fonts.json`` (milestone-1 step 7).

**Input.** The ``nerdfonts`` snapshot the run's parse stage used
(``build/stage/snapshots.json``), so the targets are the keys of the universe
the merge resolves them against; without that file, the newest snapshot on or
before the run date. Of it, ``fonts.json`` (one entry per patched family) and
``pin.json`` (the commit), as ``collectors.universe.nerdfonts`` keeps them.
The miner makes no request. With no such snapshot it fails, and the stage
keeps the committed ``data/alias-seeds/nerd.csv``.

**Candidates.** Every one has relation ``build``, is ``auto`` (rule
``nerd_unpatched``: the stage accepts it unless something contradicts it) and
targets the entry's own universe key ``nerd-folder:<folderName>``. That key
sits in the family the universe stage built from the entry's
``unpatchedName``, so the target follows the universe wherever it put the
original. A folder listed twice keeps its first entry, as the collector parses
it. A name proposed for two folders is logged; the merge queues it
("competing"). Per entry:

- ``brew-cask:font-<caskName>-nerd-font`` (``nerdfonts.cask_token``), detail
  ``nerd``. These rows are the one authoritative list of Nerd casks
  (``nerd_casks``): no other miner maps a current Nerd cask (the ``homebrew``
  miner maps only old tokens to their new ones), and this one never takes a
  cask for a Nerd build because of its token. When the store has a
  ``homebrew_casks`` snapshot, the casks on one list but not the other are
  logged, never mapped (``font-monocraft-nerd-font`` is a Nerd build made
  upstream, which ``fonts.json`` does not list).
- ``font-name:<patchedName>``, detail ``nerd``, when its match key differs
  from the ``unpatchedName``'s: the name a Reserved Font Name forced
  ("CaskaydiaCove" for Cascadia Code), but not "Hack" for Hack.
- ``font-name:<patchedName> <suffix>`` for each family name Nerd Fonts v3
  gives its builds (``SUFFIXES``): "Nerd Font" (nerd), "Nerd Font Mono" (nfm),
  "Nerd Font Propo" (propo), and the short "NF" (nf), "NFM" (nfm) and "NFP"
  (nfp), as ``names.NERD_SUFFIX`` reads them. A ``font-name`` row holds in every
  name namespace (``aliases.NAME_NAMESPACES``), so "JetBrainsMono Nerd Font"
  in a web survey finds JetBrains Mono too.

Nothing else is derived. ``folderName`` is a key, not a name: as a name it
would claim "Recursive" (the folder of Recursive Mono) or "Meslo" (of Meslo LG)
as a build. The Nerd release assets are keyed by it already. ``evidence`` is
the file at the pinned commit on GitHub, with the folder as the fragment.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, ClassVar
from urllib.parse import quote

from tff_catalog.aliases import AliasCandidate
from tff_catalog.aliases.miners import MineContext
from tff_catalog.collectors.universe import homebrew_casks, nerdfonts
from tff_catalog.keys import match_key
from tff_catalog.records import SourceKey
from tff_catalog.store import Snapshot

NAME = "nerd"
RELATION = "build"
DETAIL = "nerd"  # a cask or a bare patched name: every variant of the build
NAME_NS = "font-name"
CASK_NS = homebrew_casks.NAMESPACE
TARGET_NS = nerdfonts.NAMESPACE
CASK_SUFFIX = "-nerd-font"  # how every nerdfonts.cask_token ends
# The family names of a Nerd Fonts v3 build, after the patched name, and their alias detail.
SUFFIXES: tuple[tuple[str, str], ...] = (
    ("Nerd Font", "nerd"),
    ("Nerd Font Mono", "nfm"),
    ("Nerd Font Propo", "propo"),
    ("NF", "nf"),
    ("NFM", "nfm"),
    ("NFP", "nfp"),
)
# A Homebrew cask token's characters ("m+" is one); anything else is a broken entry.
_CASK_NAME = re.compile(r"^[a-z0-9][a-z0-9+.@_-]*$")

Entry = Mapping[str, Any]


def _clean(value: object) -> str | None:
    """A string with its runs of white space collapsed; None when empty or not a string."""
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def build_names(entry: Entry) -> list[tuple[str, str]]:
    """``(name, detail)`` for the family names of the entry's builds (see the module doc)."""
    patched = _clean(entry.get("patchedName"))
    if patched is None:
        return []
    out = []
    if match_key(patched) not in ("", match_key(entry["unpatchedName"])):
        out.append((patched, DETAIL))
    out.extend((f"{patched} {suffix}", detail) for suffix, detail in SUFFIXES)
    return out


def cask(entry: Entry) -> str | None:
    """The Homebrew token of the entry's Nerd cask; None when ``caskName`` is missing or malformed."""
    name = entry.get("caskName")
    if not isinstance(name, str) or not _CASK_NAME.fullmatch(name):
        return None
    return nerdfonts.cask_token(name)


def first_entries(entries: Iterable[Entry]) -> list[Entry]:
    """Each ``folderName``'s first entry, in file order: a repeat is ignored, as
    ``nerdfonts`` parses it, so no candidate carries names its universe record lacks."""
    out: dict[str, Entry] = {}
    for entry in entries:
        out.setdefault(entry["folderName"], entry)
    return list(out.values())


def nerd_casks(entries: Iterable[Entry]) -> dict[str, str]:
    """Every Nerd cask token -> the ``folderName`` of its entry: the one list of Nerd casks.

    A token two folders share goes to the first folder by name (``clashes`` reports it).
    """
    out: dict[str, str] = {}
    for token, folder in sorted(
        (token, e["folderName"]) for e in first_entries(entries) if (token := cask(e)) is not None
    ):
        out.setdefault(token, folder)
    return out


def evidence(pin: nerdfonts.Pin, folder: str) -> str:
    """The file at the pinned commit on GitHub, with the entry's folder as the fragment."""
    path = quote(pin.path, safe="/")
    return f"https://github.com/{pin.repo}/blob/{pin.commit}/{path}#{quote(folder, safe='')}"


def candidates(entries: Iterable[Entry], pin: nerdfonts.Pin) -> list[AliasCandidate]:
    """The candidates of ``fonts.json`` entries (checked by ``nerdfonts.font_entries``), sorted."""
    out: set[AliasCandidate] = set()
    for entry in first_entries(entries):
        folder = entry["folderName"]
        keys = [(SourceKey(NAME_NS, name), detail) for name, detail in build_names(entry)]
        if (token := cask(entry)) is not None:
            keys.append((SourceKey(CASK_NS, token), DETAIL))
        target = SourceKey(TARGET_NS, folder)
        proof = evidence(pin, folder)
        out.update(
            AliasCandidate(key, target, RELATION, detail, NAME, proof, auto=True)
            for key, detail in keys
        )
    return sorted(out)


def clashes(cands: Iterable[AliasCandidate]) -> dict[SourceKey, list[SourceKey]]:
    """Aliases (one per namespace and match key) proposed for two or more targets.

    The merge queues every such candidate as "competing", so none is lost; this
    only says why, since ``fonts.json`` never meant two folders by one name.
    """
    targets: dict[tuple[str, str], set[SourceKey]] = defaultdict(set)
    first: dict[tuple[str, str], SourceKey] = {}
    for c in sorted(cands):
        key = (c.alias.ns, match_key(c.alias.key))
        targets[key].add(c.target)
        first.setdefault(key, c.alias)
    return {first[k]: sorted(t) for k, t in sorted(targets.items()) if len(t) > 1}


def cask_differences(listed: Iterable[str], brew: Iterable[str]) -> tuple[list[str], list[str]]:
    """(listed casks Homebrew lacks, Homebrew casks named like Nerd casks but not listed)."""
    listed, brew = set(listed), set(brew)
    unlisted = {t for t in brew - listed if t.endswith(CASK_SUFFIX)}
    return sorted(listed - brew), sorted(unlisted)


def _parsed_day(ctx: MineContext) -> date | None:
    """The ``nerdfonts`` snapshot day the run's parse stage used, when it wrote its list."""
    from tff_catalog import parse  # only this path needs the parse stage's file format

    if not (ctx.paths.stage / parse.SNAPSHOTS_FILE).is_file():
        return None
    used = parse.load_snapshots(ctx.paths).get(nerdfonts.NAME)
    return used.snapshot if used is not None else None


def _snapshot(ctx: MineContext) -> Snapshot:
    """The parse stage's ``nerdfonts`` snapshot, else the newest on or before the run date."""
    if ctx.store is None:
        raise FileNotFoundError(f"miner {NAME} reads the {nerdfonts.NAME} snapshot: set TFF_STORE")
    day = _parsed_day(ctx)
    snap = (
        ctx.store.snapshot(nerdfonts.NAME, day)
        if day is not None
        else ctx.store.latest(nerdfonts.NAME, ctx.run_date)
    )
    if snap is None:
        when = f"of {day} (the parse stage's)" if day else f"on or before {ctx.run_date}"
        raise FileNotFoundError(
            f"miner {NAME}: no {nerdfonts.NAME} snapshot {when} in {ctx.store.root} "
            f"(run `tff-catalog fetch --only {nerdfonts.NAME}`)"
        )
    return snap


def _load(ctx: MineContext) -> tuple[list[Entry], nerdfonts.Pin]:
    snap = _snapshot(ctx)
    entries = nerdfonts.font_entries(snap.load_json(nerdfonts.FONTS))
    pin = nerdfonts.Pin.from_json(snap.load_json(nerdfonts.PIN))
    ctx.log.info(
        "%s: %s %s, %d entries at %s", NAME, snap.source, snap.date, len(entries), pin.commit[:12]
    )
    return entries, pin


def _brew_tokens(ctx: MineContext) -> set[str] | None:
    """The cask tokens of the newest ``homebrew_casks`` snapshot, or None without a usable one."""
    snap = ctx.store.latest(homebrew_casks.NAME, ctx.run_date) if ctx.store else None
    if snap is None or not snap.has(homebrew_casks.CASKS_EXTRACT):
        return None
    try:
        rows = list(snap.iter_jsonl(homebrew_casks.CASKS_EXTRACT))
    except (OSError, ValueError) as exc:  # a corrupt snapshot too: this is only a report
        ctx.log.warning("%s: cannot read %s %s: %s", NAME, snap.source, snap.date, exc)
        return None
    return {r["token"] for r in rows if isinstance(r, dict) and isinstance(r.get("token"), str)}


def _check_casks(ctx: MineContext, listed: Iterable[str]) -> None:
    """Log where Homebrew and ``fonts.json`` disagree on the Nerd casks (nothing is mapped)."""
    brew = _brew_tokens(ctx)
    if brew is None:
        return
    missing, unlisted = cask_differences(listed, brew)
    if missing:
        ctx.log.warning("%s: fonts.json casks Homebrew lacks: %s", NAME, ", ".join(missing))
    if unlisted:
        ctx.log.warning(
            "%s: Homebrew Nerd casks fonts.json does not list (need a hand row): %s",
            NAME,
            ", ".join(unlisted),
        )


class NerdMiner:
    """Build aliases of the fonts Nerd Fonts patches, from its ``fonts.json``."""

    name: ClassVar[str] = NAME

    def mine(self, ctx: MineContext) -> list[AliasCandidate]:
        """The candidates of the run's ``nerdfonts`` snapshot (see the module doc)."""
        entries, pin = _load(ctx)
        repeated = Counter(e["folderName"] for e in entries)
        for folder, times in sorted(repeated.items()):
            if times > 1:
                ctx.log.warning(
                    "%s: folder %s is listed %d times; the first entry is kept", NAME, folder, times
                )
        for entry in first_entries(entries):
            if cask(entry) is None:
                ctx.log.warning(
                    "%s: entry %s has no usable caskName: %r",
                    NAME,
                    entry["folderName"],
                    entry.get("caskName"),
                )
        found = candidates(entries, pin)
        for alias, targets in clashes(found).items():
            ctx.log.warning(
                "%s: %s:%s is proposed for %s; the merge queues it",
                NAME,
                alias.ns,
                alias.key,
                ", ".join(f"{t.ns}:{t.key}" for t in targets),
            )
        _check_casks(ctx, nerd_casks(entries))
        ctx.log.info("%s: %d candidates", NAME, len(found))
        return found


MINER = NerdMiner()
