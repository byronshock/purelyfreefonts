"""Alias miner "oldlib": the name tables of the old top-100 run (design-m1 §7 P4.8; milestone-1 step 7).

**Input.** One-off copies of the old run (2026-09-23) in the private store, read
only; the miner makes no request:

- ``$TFF_STORE/_seed/oldlib-2026-09-23/agg_mono.py`` (``AGG_MONO``): the tables
  ``NF`` (Nerd Fonts folder -> family), ``BREW_MAP`` (Homebrew cask regex ->
  family), ``ARCH_MAP`` and ``DEB_MAP`` (package -> family). A family of
  ``"ICON"`` marks an icon font; ``None`` means "no family". The file is parsed
  with ``ast`` and never run. Its ``STARS`` table (star counts) is not read.
- ``.../brew365.json`` (``BREW365``): only the cask tokens the old run applied
  ``BREW_MAP`` to. Its install counts are never read into a candidate.
- ``$TFF_STORE/_seed/probe-2026-09-25/oldlib/canon_items.json`` (``CANON``):
  the old run's canonicalisation of 650 raw names, ``{raw, canonical, status,
  note}``, status one of ``CANON_STATUSES``.

A missing or malformed input raises, so the stage keeps the committed
``data/alias-seeds/oldlib.csv`` (an empty result would erase it).

**Candidates.** Name -> family facts only. Every candidate has source
``oldlib``, ``auto`` false (no auto rule covers the old run: the owner reviews
each one at gate A) and evidence naming the table it came from. A family is
targeted by name, ``font-name:<family>``, which the aliases stage resolves in the
universe; an ``ineligible`` candidate targets its own alias.

- ``NF``: ``nerd-folder:<folder>``, relation ``build``, detail ``nerd``.
- ``ARCH_MAP``: ``arch-pkg:<package>``: ``build``/``nerd`` when ``nerd`` is a
  word of the package name (``ttf-hack-nerd``), ``build``/``variable`` when it
  ends in ``-vf`` or ``-variable`` (``ttf-rubik-vf``, as miner ``distro`` types
  it), else ``package``.
- ``DEB_MAP``: ``deb-pkg:<package>``, relation ``package`` (or
  ``build``/``variable`` as above).
- ``BREW_MAP``: ``brew-cask:<token>`` for each ``font-`` token of ``brew365.json``
  with no build suffix, relation ``package`` (or ``build``/``variable`` as
  above). The pattern is the one the old run chose (the first that matches),
  and it must match the whole token less ``font-``: nothing is taken by
  prefix, so ``^lexend`` gives ``font-lexend`` but never ``font-lexend-deca``.
  Casks with a Nerd or Powerline suffix are left to miner ``nerd``, the one
  list of Nerd casks.
- ``"ICON"`` rows of the three package tables: ``ineligible``/``icon``.
- A package table's family that ``CANON`` renames (and renames one way only)
  is targeted by its canonical name, with the canon row as evidence too:
  ``gnu-free-fonts`` -> FreeFont, not "GNU FreeFont", so the target resolves
  like the other miners' rows for the same key.
- ``CANON``, in the ``font-name`` namespace (a name row holds in every name
  namespace): statuses ``proprietary``, ``icon_font`` and ``system_or_generic``
  give ``ineligible`` rows (``proprietary``, ``icon``, ``generic``) for the raw
  name and the canonical one. A ``free`` row whose note names the ITF Free Font
  License gives ``ineligible``/``itf`` (AUTHORITY rule 4). Any other ``free``
  row whose canonical name differs gives one mapping row, typed by
  ``canon_relation``; ``unclear`` rows give nothing.

Left out, and logged: ``None`` targets; strings that are descriptions rather
than names ("Ubuntu (Sans/Mono)", "Red Hat Display / Text / Mono": see
``is_name``); umbrella names folded into one member ("IBM Plex" -> "IBM Plex
Sans"), which are superfamily questions (design-m1 gap G6), not aliases; and the
package and folder rows in ``LEFT_OUT``. Most of those ship several families,
which the old run folded into one (``ttf-croscore`` -> Arimo, the Nerd ``Noto``
folder -> Noto Sans Mono): that is a bundle (D2), and a one-family row for it
would clash with the bundle rows (rejecting it would even block them).
Candidates that differ only in evidence are merged, their evidence joined.
"""

import ast
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from tff_catalog import names
from tff_catalog.aliases import AliasCandidate
from tff_catalog.aliases.miners import MineContext
from tff_catalog.keys import match_key
from tff_catalog.records import SourceKey

NAME = "oldlib"
SEED_DIR = "_seed/oldlib-2026-09-23"  # relative to $TFF_STORE
AGG_MONO = f"{SEED_DIR}/agg_mono.py"
BREW365 = f"{SEED_DIR}/brew365.json"
CANON = "_seed/probe-2026-09-25/oldlib/canon_items.json"

TARGET_NS = "font-name"
NAME_NS = "font-name"
ICON = "ICON"  # the old tables' family for an icon font
NERD_DETAIL = "nerd"
VARIABLE_SUFFIXES = frozenset({"vf", "variable"})  # a package of the variable fonts only
CASK_PREFIX = "font-"  # the old run read only bare font casks (no third-party tap)

# The tables of agg_mono.py this miner reads, and their namespaces (BREW_MAP is keyed below).
TABLES = ("NF", "BREW_MAP", "ARCH_MAP", "DEB_MAP")
_TABLE_NS = {"NF": "nerd-folder", "ARCH_MAP": "arch-pkg", "DEB_MAP": "deb-pkg"}

# agg_mono.brew_family's build suffixes (its second pass strips a subset of these).
BUILD_SUFFIX = re.compile(
    r"-(nerd-font(-mono|-propo)?|nf(-cn|-mono|-propo)?|nfm|nfp|for-powerline"
    r"|for-powerlevel10k|nerd|powerline)$"
)

# Package and folder rows of the old tables left out (see the module doc), with why.
# The seed never changes, so this list is complete for it.
LEFT_OUT: dict[tuple[str, str], str] = {
    ("arch-pkg", "noto-fonts"): "bundle: the Noto families",
    ("arch-pkg", "noto-fonts-cjk"): "bundle: Noto Sans CJK, Noto Serif CJK",
    ("arch-pkg", "ttf-bitstream-vera"): "bundle: Bitstream Vera Sans, Serif, Sans Mono",
    ("arch-pkg", "ttf-cascadia-code"): "bundle: Cascadia Code, Cascadia Mono (and NF, PL)",
    ("arch-pkg", "ttf-croscore"): "bundle: Arimo, Cousine, Tinos",
    ("arch-pkg", "ttf-dejavu"): "bundle: DejaVu Sans, Serif, Sans Mono",
    ("arch-pkg", "ttf-droid"): "bundle: Droid Sans, Serif, Sans Mono",
    ("arch-pkg", "ttf-ibm-plex"): "bundle: the IBM Plex families",
    ("arch-pkg", "ttf-liberation"): "bundle: Liberation Sans, Serif, Mono",
    ("arch-pkg", "ttf-linux-libertine"): "bundle: Linux Libertine, Linux Biolinum",
    ("arch-pkg", "ttf-linux-libertine-g"): "bundle: Linux Libertine G, Linux Biolinum G",
    ("arch-pkg", "ttf-noto-nerd"): "bundle: Nerd builds of several Noto families",
    ("arch-pkg", "ttf-ubuntu-font-family"): "bundle: Ubuntu, Ubuntu Condensed, Ubuntu Mono",
    ("brew-cask", "font-dejavu"): "bundle: DejaVu Sans, Serif, Sans Mono",
    ("brew-cask", "font-ibm-plex"): "bundle: the IBM Plex families",
    ("brew-cask", "font-liberation"): "bundle: Liberation Sans, Serif, Mono",
    ("deb-pkg", "fonts-cascadia-code"): "bundle: Cascadia Code, Cascadia Mono (and PL)",
    ("deb-pkg", "fonts-croscore"): "bundle: Arimo, Cousine, Tinos",
    ("deb-pkg", "fonts-dejavu-core"): "bundle: DejaVu Sans, DejaVu Serif",
    ("deb-pkg", "fonts-go"): "bundle: Go, Go Mono, Go Smallcaps",
    ("deb-pkg", "fonts-ibm-plex"): "bundle: the IBM Plex families",
    ("deb-pkg", "fonts-liberation"): "bundle: Liberation Sans, Serif, Mono",
    ("deb-pkg", "fonts-liberation2"): "bundle: Liberation Sans, Serif, Mono",
    ("deb-pkg", "fonts-linuxlibertine"): "bundle: Linux Libertine, Linux Biolinum",
    ("deb-pkg", "fonts-noto-cjk"): "bundle: Noto Sans CJK, Noto Serif CJK",
    ("deb-pkg", "fonts-noto-core"): "bundle: the Noto families",
    ("deb-pkg", "fonts-ubuntu"): "bundle: Ubuntu, Ubuntu Mono",
    ("deb-pkg", "ttf-bitstream-vera"): "bundle: Bitstream Vera Sans, Serif, Sans Mono",
    ("deb-pkg", "fonts-droid-fallback"): "another font: Droid Sans Fallback, not Droid Sans",
    ("deb-pkg", "fonts-stix"): "another font: the STIX 1 families, not STIX Two",
    # Nerd folders whose unpatched font is an umbrella (fonts.json unpatchedName),
    # folded by the old run into one member; miner nerd maps the folders.
    ("nerd-folder", "Noto"): "bundle: Nerd builds of several Noto families",
    ("nerd-folder", "iA-Writer"): "bundle: iA Writer Mono, Duo, Quattro",
}

CANON_STATUSES = frozenset({"free", "proprietary", "icon_font", "unclear", "system_or_generic"})
# canon status -> ineligible reason (aliases.INELIGIBLE_REASONS); the one
# system_or_generic row is a placeholder name ("My Font"), hence "generic".
CANON_INELIGIBLE = {
    "proprietary": "proprietary",
    "icon_font": "icon",
    "system_or_generic": "generic",
}
ITF_NOTE = re.compile(r"(\bITF\b|Indian Type Foundry).*Free Font Licen[cs]e", re.IGNORECASE)
# Words that make a longer name a style or build of the canonical one ("Lato Hairline").
STYLE_WORDS = names.STYLE_WORDS | {"variable", "vf"}
_VARIABLE_WORDS = (("variable",), ("vf",))
_CJK_PREFIXES = ("CJK", "HIRAGANA", "KATAKANA", "HANGUL", "BOPOMOFO")
_NOT_A_NAME = re.compile(r"[()\[\]{}/+]")


class OldlibError(ValueError):
    """A seed file of the old run is missing a table or has an unexpected shape."""


# --- names and relations ----------------------------------------------------------------------


def clean(value: object) -> str | None:
    """A string with its runs of white space collapsed; None when empty or not a string."""
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def is_name(value: str) -> bool:
    """Whether ``value`` reads as a name as some source writes it, not an LLM description.

    Parentheses, brackets, slashes and plus signs mark descriptions such as
    "Geist (Sans + Mono)" or "IPAexFont / IPAFont".
    """
    return bool(value) and not _NOT_A_NAME.search(value)


def _has_cjk(value: str) -> bool:
    return any(unicodedata.name(ch, "").startswith(_CJK_PREFIXES) for ch in value)


def canon_relation(raw: str, canonical: str) -> tuple[str, str] | None:
    """The (relation, detail) of the canon row ``raw -> canonical``, or None to leave it out.

    - None: the canonical name extends the raw one word by word ("IBM Plex" ->
      "IBM Plex Sans"): an umbrella folded into one member.
    - ``build``/``cjk``: the raw name is a CJK build ("Noto Sans CJK JP",
      "源ノ角ゴシック JP").
    - ``build``: the raw name is the canonical one plus style words or numbers
      ("Lato Hairline", "Redaction 70"); detail ``variable`` for "<name> Variable".
    - ``rename`` otherwise ("Muli" -> "Mulish", "Geist Sans" -> "Geist").
    """
    r, c = raw.casefold().split(), canonical.casefold().split()
    if len(c) > len(r) and c[: len(r)] == r:
        return None
    if "cjk" in r or _has_cjk(raw):
        return "build", "cjk"
    extra = tuple(r[len(c) :]) if len(r) > len(c) and r[: len(c)] == c else ()
    if extra and all(w in STYLE_WORDS or w.isdigit() for w in extra):
        return "build", "variable" if extra in _VARIABLE_WORDS else ""
    return "rename", ""


def package_relation(ns: str, key: str) -> tuple[str, str]:
    """(relation, detail) of a package the old tables map to a family (not an icon)."""
    words = key.split("-")
    if ns == "nerd-folder":
        return "build", NERD_DETAIL
    if ns == "arch-pkg" and NERD_DETAIL in words:
        return "build", NERD_DETAIL
    if len(words) > 1 and words[-1] in VARIABLE_SUFFIXES:
        return "build", "variable"
    return "package", ""


# --- reading the seed files -------------------------------------------------------------------


def read_tables(source: str) -> dict[str, Any]:
    """``TABLES`` from the text of ``agg_mono.py``, by literal evaluation (never run).

    A dict literal with a repeated key keeps the last value, as Python did when
    the old run imported it. Raises ``OldlibError`` when a table is missing,
    empty or not a literal of the expected shape.
    """
    try:
        body = ast.parse(source).body
    except SyntaxError as exc:
        raise OldlibError(f"agg_mono.py does not parse: {exc}") from exc
    found: dict[str, Any] = {}
    for node in body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in TABLES:
            try:
                found[target.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError) as exc:
                raise OldlibError(f"agg_mono.py: {target.id} is not a literal: {exc}") from exc
    missing = [t for t in TABLES if not found.get(t)]
    if missing:
        raise OldlibError(f"agg_mono.py: missing or empty tables {missing}")
    for table in _TABLE_NS:
        _check_map(table, found[table], none_ok=table != "NF")
    _check_brew_map(found["BREW_MAP"])
    return found


def _check_map(table: str, value: object, *, none_ok: bool) -> None:
    if not isinstance(value, dict):
        raise OldlibError(f"agg_mono.py: {table} must be a dict")
    for key, fam in value.items():
        if not isinstance(key, str) or not (isinstance(fam, str) or (none_ok and fam is None)):
            raise OldlibError(f"agg_mono.py: {table}[{key!r}] = {fam!r} is not a name")


def _check_brew_map(value: object) -> None:
    if not isinstance(value, list):
        raise OldlibError("agg_mono.py: BREW_MAP must be a list of (pattern, family)")
    for row in value:
        if not (isinstance(row, tuple) and len(row) == 2 and all(isinstance(v, str) for v in row)):
            raise OldlibError(f"agg_mono.py: BREW_MAP row {row!r} is not (pattern, family)")
        try:
            re.compile(row[0])
        except re.error as exc:
            raise OldlibError(f"agg_mono.py: BREW_MAP pattern {row[0]!r}: {exc}") from exc


def read_cask_tokens(doc: object) -> list[str]:
    """The bare ``font-`` cask tokens of the old ``brew365.json``, sorted (counts are ignored).

    Raises ``OldlibError`` when there is none: the wrong file would otherwise
    drop every ``BREW_MAP`` row without a word.
    """
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list) or not items:
        raise OldlibError("brew365.json: no items")
    tokens = set()
    for item in items:
        cask = item.get("cask") if isinstance(item, dict) else None
        if not isinstance(cask, str):
            raise OldlibError(f"brew365.json: item without a cask: {item!r}")
        if cask.startswith(CASK_PREFIX):
            tokens.add(cask)
    if not tokens:
        raise OldlibError(f"brew365.json: no {CASK_PREFIX}* cask")
    return sorted(tokens)


@dataclass(frozen=True, slots=True)
class CanonItem:
    raw: str
    canonical: str | None
    status: str
    note: str


def read_canon(doc: object) -> list[CanonItem]:
    """The rows of ``canon_items.json``; extra keys are ignored, unknown statuses raise."""
    if not isinstance(doc, list) or not doc:
        raise OldlibError("canon_items.json: expected a non-empty list")
    out = []
    for row in doc:
        if not isinstance(row, dict) or row.get("status") not in CANON_STATUSES:
            raise OldlibError(f"canon_items.json: bad row {row!r}")
        raw = clean(row.get("raw"))
        if raw is None:
            raise OldlibError(f"canon_items.json: row without a raw name: {row!r}")
        note = row.get("note")
        out.append(
            CanonItem(
                raw,
                clean(row.get("canonical")),
                row["status"],
                note if isinstance(note, str) else "",
            )
        )
    return out


# --- candidates -------------------------------------------------------------------------------


Proof = tuple[str, str]  # (seed file, what in it shows the fact)


def evidence(proofs: Iterable[Proof]) -> str:
    """``$TFF_STORE/<file> <what>; <what>``, one part per file, parts joined by `` | ``."""
    by_file: dict[str, set[str]] = defaultdict(set)
    for file, what in proofs:
        by_file[file].add(what)
    return " | ".join(
        f"$TFF_STORE/{file} {'; '.join(sorted(whats))}" for file, whats in sorted(by_file.items())
    )


@dataclass(slots=True)
class _Found:
    """Candidates keyed without their evidence, so one fact seen twice is one row."""

    proofs: dict[tuple[SourceKey, SourceKey, str, str], set[Proof]] = field(
        default_factory=lambda: defaultdict(set)
    )
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # match_key(old name) -> (canonical name, the canon rows that say so); see ``learn_renames``
    renames: dict[str, tuple[str, tuple[Proof, ...]]] = field(default_factory=dict)

    def learn_renames(self) -> None:
        """Keep the name rows typed ``rename`` found so far, for ``family``.

        An old name renamed to two different names is kept out (never guessed).
        """
        # old match_key -> new match_key -> the new name as written
        seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        shown: dict[str, set[Proof]] = defaultdict(set)
        for (alias, target, relation, _), proofs in self.proofs.items():
            if alias.ns == NAME_NS and relation == "rename":
                old = match_key(alias.key)
                seen[old][match_key(target.key)].add(target.key)
                shown[old] |= proofs
        self.renames = {
            old: (min(spellings), tuple(sorted(shown[old])))
            for old, new in seen.items()
            if len(new) == 1
            for spellings in new.values()
        }

    def family(self, name: str) -> tuple[str, tuple[Proof, ...]]:
        """A package table's family by its canonical name, with the canon rows that renamed it."""
        return self.renames.get(match_key(name), (name, ()))

    def add(
        self, alias: SourceKey, target: SourceKey, relation: str, detail: str, proof: Proof
    ) -> None:
        self.proofs[(alias, target, relation, detail)].add(proof)

    def ineligible(self, alias: SourceKey, reason: str, proof: Proof) -> None:
        self.add(alias, alias, "ineligible", reason, proof)

    def skip(self, why: str) -> None:
        self.skipped[why] += 1

    def candidates(self) -> list[AliasCandidate]:
        return sorted(
            AliasCandidate(alias, target, relation, detail, NAME, evidence(proofs), False)
            for (alias, target, relation, detail), proofs in self.proofs.items()
        )


def _package_row(found: _Found, alias: SourceKey, family: str | None, proof: Proof) -> None:
    """One row of a package table: a package, a Nerd build or an icon font."""
    if family is None:
        found.skip("package tables: no family")
    elif family == ICON:
        found.ineligible(alias, "icon", proof)
    elif (alias.ns, alias.key) in LEFT_OUT:
        found.skip("package tables: LEFT_OUT")
    else:
        name, renamed_by = found.family(family)
        relation, detail = package_relation(alias.ns, alias.key)
        for p in (proof, *renamed_by):
            found.add(alias, SourceKey(TARGET_NS, name), relation, detail, p)


def _table_rows(found: _Found, table: str, mapping: Mapping[str, str | None]) -> None:
    ns, proof = _TABLE_NS[table], (AGG_MONO, table)
    for key, family in sorted(mapping.items()):
        _package_row(found, SourceKey(ns, key), clean(family), proof)


def _brew_rows(found: _Found, brew_map: list[tuple[str, str]], tokens: Iterable[str]) -> None:
    patterns = [(re.compile(p), p, fam) for p, fam in brew_map]
    for token in tokens:
        stem = token.removeprefix(CASK_PREFIX)
        if BUILD_SUFFIX.search(stem):
            found.skip("BREW_MAP: Nerd or Powerline cask (miner nerd)")
            continue
        chosen = next(((rx, p, fam) for rx, p, fam in patterns if rx.search(stem)), None)
        if chosen is None:
            continue
        rx, pattern, family = chosen
        if not rx.fullmatch(stem):
            found.skip("BREW_MAP: matched by prefix only")
            continue
        proof = (AGG_MONO, f"BREW_MAP {pattern}")
        _package_row(found, SourceKey("brew-cask", token), clean(family), proof)


def _canon_rows(found: _Found, items: Iterable[CanonItem]) -> None:
    for item in items:
        shown = (
            item.raw if item.canonical in (None, item.raw) else f"{item.raw} -> {item.canonical}"
        )
        what = f"{shown} ({item.status})"
        names = [n for n in (item.raw, item.canonical) if n is not None and is_name(n)]
        if not names:
            found.skip("canon: no name, only a description")
            continue
        reason = CANON_INELIGIBLE.get(item.status)
        if reason is None and item.status == "free" and ITF_NOTE.search(item.note):
            reason, what = "itf", f"{what}, ITF Free Font License"
        if reason is not None:
            for key in _distinct_names(names):
                found.ineligible(SourceKey(NAME_NS, key), reason, (CANON, what))
        elif item.status == "free":
            _canon_mapping(found, item, (CANON, what))
        else:
            found.skip(f"canon: {item.status}")


def _distinct_names(names: list[str]) -> Iterator[str]:
    seen: set[str] = set()
    for name in names:
        if match_key(name) not in seen:
            seen.add(match_key(name))
            yield name


def _canon_mapping(found: _Found, item: CanonItem, proof: Proof) -> None:
    raw, canonical = item.raw, item.canonical
    if canonical is None or match_key(raw) == match_key(canonical):
        return  # the name is already its family's
    if not (is_name(raw) and is_name(canonical)):
        found.skip("canon: no name, only a description")
        return
    kind = canon_relation(raw, canonical)
    if kind is None:
        found.skip("canon: umbrella folded into a member")
        return
    found.add(SourceKey(NAME_NS, raw), SourceKey(TARGET_NS, canonical), *kind, proof)


def candidates(
    tables: Mapping[str, Any], cask_tokens: Iterable[str], canon: Iterable[CanonItem]
) -> tuple[list[AliasCandidate], dict[str, int]]:
    """The candidates of the old run's tables, sorted, and what was left out (reason -> count)."""
    found = _Found()
    _canon_rows(found, canon)
    found.learn_renames()  # the package tables' targets follow the canon's renames
    for table in _TABLE_NS:
        _table_rows(found, table, tables[table])
    _brew_rows(found, tables["BREW_MAP"], cask_tokens)
    return found.candidates(), dict(sorted(found.skipped.items()))


def _store_root(ctx: MineContext) -> Path:
    root = ctx.store.root if ctx.store is not None else ctx.paths.store
    if root is None:
        raise FileNotFoundError(
            f"miner {NAME} reads {SEED_DIR}/ in the private store: set TFF_STORE"
        )
    return root


def _read(root: Path, rel: str) -> str:
    path = root / rel
    if not path.is_file():
        raise FileNotFoundError(f"miner {NAME}: {path} is missing (the old run's seed copy)")
    return path.read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class OldlibMiner:
    name: ClassVar[str] = NAME

    def mine(self, ctx: MineContext) -> list[AliasCandidate]:
        """The old run's name -> family facts (see the module doc), sorted."""
        root = _store_root(ctx)
        tables = read_tables(_read(root, AGG_MONO))
        tokens = read_cask_tokens(json.loads(_read(root, BREW365)))
        canon = read_canon(json.loads(_read(root, CANON)))
        found, skipped = candidates(tables, tokens, canon)
        ctx.log.info(
            "%s: %d candidates from %d canon rows and tables %s; left out: %s",
            NAME,
            len(found),
            len(canon),
            ", ".join(TABLES),
            ", ".join(f"{why} {n}" for why, n in skipped.items()) or "nothing",
        )
        return found


MINER = OldlibMiner()
