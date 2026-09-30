"""Stage "licenses": L1 and L2 classification before ranking (milestone-1 step 6a). Owner: agent P3.

- L1 ``normalize``: every license string to an SPDX expression through
  ``config/license-aliases.toml`` (its header gives the lookup order); unknown
  strings give None (and a queue entry). "NOASSERTION" means the source said
  nothing usable.
- ``classify``: the SPDX expression to a class through ``config/licenses.toml``
  (D3); anything not listed is excluded. ``OR`` takes the best branch, ``AND``
  needs every part to pass, and an unlisted ``WITH`` exception falls back to
  its base license (an exception only adds permissions). Ties go to the group
  that comes first in ``GROUP_ORDER``.
- L2 ``cross_check``: the google/fonts folder, Fontsource, Fontist, Nerd Fonts,
  Debian DEP-5 and Arch must agree; disagreements go to the queue. Several
  facts from one source are ANDed.

Per family (``Verdict``): the agreed expression, its class (allowed, excluded
or ruling), redistributable, attribution_required and ``preview_ok`` (D3:
allowed and redistributable). Rule 3 (owner ruling of 2026-09-30,
``redistributable_only``) lets a family in only if its license may also be
redistributed, so an allowed class is always redistributable: a licenses.toml
entry with ``redistributable = false`` classifies as excluded. Every eligible
family gets a class:

- no license fact at all: excluded ("no license found", D3), not queued;
- facts, but none readable: class "ruling" (NOASSERTION), queued;
- sources disagree: the class of the highest-precedence source
  (``SOURCE_ORDER``), queued, unless every reading is excluded (URW's AGPL);
- a license in the ``ruling`` class: queued once per license, not per family.

Owner rulings come from gate LIC (``reviews.gate_dir(paths, "LIC")``, that is
``data/reviews/licenses/``), read with ``reviews.load_rulings``. Question ids:
``LIC-<family id>`` for a family and ``LIC-spdx-<slug>`` for a license
(``family_question_id``, ``license_question_id``). Every question has the
same four options (``OPTIONS``): (a) qualifies and redistributable, (b)
free to use but not redistributable, which excludes it under Rule 3 (before
2026-09-30 it qualified, so a stored (b) answer now excludes too), (c)
excluded, (d) research more. An answer may add values: ``spdx`` (the
expression the ruling is about, for a family; required for (a) when the
sources name no license or the provisional one is excluded), ``group`` (the
site's license class) and
``attribution_required``. A "d" answer keeps the item in the queue. A ruling
whose id matches no listed license and no eligible family is logged.

Writes ``build/stage/licenses.json`` ({id: Verdict}) and
``build/stage/queues/licenses.json`` (``Queue``). The queue holds owner
questions and L1 gaps ("alias" items: a string Claude must add to
``license-aliases.toml``); ``licenses --queue --count`` is 0 only when both
are done. Families that fail the Latin gate get a verdict but never a queue
entry.
"""

import hashlib
import logging
import re
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

from tff_catalog import jsonio, reviews, stageio
from tff_catalog.aliases import AliasRow, load_aliases
from tff_catalog.config_model import AllowedLicense, ExcludedLicense
from tff_catalog.keys import match_key
from tff_catalog.names import slugify
from tff_catalog.records import LicenseFact, SourceKey, from_json

if TYPE_CHECKING:
    from tff_catalog.config_model import LicensesConfig, SiteConfig
    from tff_catalog.paths import Paths
    from tff_catalog.reviews import Answer
    from tff_catalog.stages import StageContext
    from tff_catalog.universe import Family, Universe

GATE = "LIC"
NOASSERTION = "NOASSERTION"  # SPDX: the source makes no usable statement
NONE = "NONE"  # SPDX: the source says there is no license
SCOPE = "::"  # "<collector>::<string>" keys in license-aliases.toml apply to one source
QUEUE_FILE = "licenses.json"  # under build/stage/queues/
QUEUE_SCHEMA = 1
NO_LICENSE_REASON = "no license found (D3)"
MAX_QUESTION_ID = 64  # schemas/review.schema.json propertyNames

# L2 precedence when sources disagree: the first source with a readable
# license gives the provisional class (scout-universe §10). The google/fonts
# folder decides for Google families; the foundry's own statement comes next.
SOURCE_ORDER = (
    "google_repo",
    "foundries",
    "fontsource",
    "nerdfonts",
    "fontist",
    "debian_copyright",
    "arch_repos",
)

OPTIONS = (
    "Qualifies and redistributable",
    "Excluded: free to use but not redistributable (Rule 3)",
    "Excluded",
    "Research more",
)
CHOICES = ("a", "b", "c", "d")

# The site's license-class groups, best first (site.toml [[license_classes]]
# order): between equally good OR branches, or equally strict AND parts, the
# first group names the class.
GROUP_ORDER = ("open-font", "permissive", "attribution", "freeware")

# License class for a license or family ruled (a) when nothing better is known.
# (b) no longer lets a font in (Rule 3, 2026-09-30), so it has no group.
DEFAULT_GROUPS = {"a": "permissive"}

# The answers that keep a license or family out: (c), and (b) under Rule 3.
EXCLUDING = frozenset({"b", "c"})
NOT_REDISTRIBUTABLE_REASON = (
    "free to use but may not be redistributed (Rule 3: only redistributable fonts qualify)"
)

# gate LIC (rec): Claude's recommended answer per ruling-class license, and the
# license class if the owner answers (a). A license missing here is recommended
# (d), research more.
RULING_ADVICE: dict[str, tuple[str, str | None]] = {
    "Bitstream-Vera": ("a", "open-font"),
    "Bitstream-Charter": ("a", "open-font"),
    "Ubuntu-font-1.0": ("a", "open-font"),
    "WTFPL": ("a", "permissive"),
    "LPPL-1.3c": ("a", "permissive"),
    "LicenseRef-GUST-Font-License": ("a", "open-font"),
    "LicenseRef-PublicDomain": ("a", "permissive"),
    "IPA": ("a", "open-font"),
    "LicenseRef-Monofur": ("d", None),
    "LicenseRef-VicFieger": ("d", None),
    "LicenseRef-Freeware": ("d", None),
    "Arphic-1999": ("d", None),
}

# gate LIC: families that go to the owner even when their sources agree,
# because the problem is not visible in the license ids (design-m1 §7, gate LIC;
# milestone-1 step 6a). Keyed by family id; a variant id "<id>-..." (Redaction
# 35, OpenDyslexic Mono, Unifont Upper) is watched too (``watch_note``). DejaVu
# and Bitstream Vera come in through the Bitstream-Vera license question;
# URW's AGPL is excluded automatically.
WATCH: dict[str, str] = {
    "hack": "MIT for Hack's own work plus the Bitstream Vera license for the glyphs it derives from",
    "opendyslexic": "Debian labels it Bitstream (Vera); the upstream repository says OFL-1.1",
    "cascadia-code": "Microsoft's font, also preinstalled on Windows 11; Arch labels it custom:OFL",
    "cascadia-mono": "Microsoft's font, also preinstalled on Windows 11; Arch labels it custom:OFL",
    "roboto-mono": "moved from google/fonts apache/ to ofl/, so older sources may still say Apache-2.0",
    "redaction": "OFL-1.1 with a Reserved Font Name, also offered under LGPL-2.1; the official "
    "download page asks for an EULA checkbox",
    "gnu-unifont": "OFL-1.1 or GPL-2.0-or-later with the font exception; a pan-Unicode bitmap font",
    "unifont": "OFL-1.1 or GPL-2.0-or-later with the font exception; a pan-Unicode bitmap font",
}

# aliases.csv relations whose rows carry a family's own license facts. A build
# (Nerd, CJK) has its own license; a sibling or related font is another family.
FACT_RELATIONS = frozenset({"rename", "package", "postscript", "bundle"})

# Queue order. "research": the owner answered (d) for a family that has no other reason.
QueueKind = Literal["disagreement", "noassertion", "watch", "research", "license", "alias"]
_KIND_ORDER = {kind: i for i, kind in enumerate(get_args(QueueKind))}


class RulingError(ValueError):
    """A gate LIC ruling cannot be applied (unknown choice, group or expression)."""


@dataclass(frozen=True, slots=True)
class LicenseClass:
    spdx: str
    status: Literal["allowed", "excluded", "ruling"]
    group: str | None = None  # license-class filter group, for allowed licenses
    redistributable: bool | None = None
    attribution_required: bool | None = None
    reason: str | None = None  # for excluded and ruling


@dataclass(frozen=True, slots=True)
class Verdict:
    family_id: str
    spdx: str | None  # the agreed expression
    license: LicenseClass | None
    preview_ok: bool  # D3: redistributable fonts only
    seen: tuple[tuple[str, str | None], ...]  # (source, normalised spdx), sorted
    queue: str | None = None  # why it needs the owner, if it does


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One entry of ``build/stage/queues/licenses.json``."""

    id: str  # the question id, or "L1-<hash>" for an alias item
    kind: QueueKind
    subject: str  # family id, license id, or "<source>: <raw string>"
    families: tuple[str, ...]  # candidate families it affects, sorted
    detail: str  # the evidence, one line
    question: reviews.Question | None  # for the owner; None for alias items, which Claude fixes


@dataclass(frozen=True, slots=True)
class Queue:
    schema: int
    items: tuple[QueueItem, ...]  # by kind, then id
    counts: dict[str, int]  # candidates, allowed, excluded, ruling, no_license, unmapped_facts
    no_license: tuple[
        str, ...
    ]  # candidates with no license fact (D3: excluded unless ruled), sorted


# --- SPDX expressions -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Id:
    id: str
    exception: str | None = None  # "<id> WITH <exception>"


@dataclass(frozen=True, slots=True)
class _Op:
    op: Literal["AND", "OR"]
    args: tuple[_Node, ...]


type _Node = _Id | _Op

_WORD = re.compile(r"\(|\)|[^()\s]+")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")
_OPERATORS = frozenset({"AND", "OR", "WITH"})


class _Unknown(Exception):
    """A part of a raw license string that L1 cannot read."""


def _tokens(text: str) -> list[tuple[str, str]]:
    """``(kind, text)`` tokens; runs of plain words form one leaf ("Font exception")."""
    out: list[tuple[str, str]] = []
    leaf: list[str] = []
    for word in _WORD.findall(text):
        if word in ("(", ")") or word.upper() in _OPERATORS:
            if leaf:
                out.append(("leaf", " ".join(leaf)))
                leaf = []
            out.append((word, word) if word in ("(", ")") else ("op", word.upper()))
        else:
            leaf.append(word)
    if leaf:
        out.append(("leaf", " ".join(leaf)))
    return out


class _Parser:
    """Recursive descent with SPDX precedence: WITH, then AND, then OR."""

    def __init__(
        self, text: str, leaf: Callable[[str], _Node], exception: Callable[[str], str]
    ) -> None:
        self.tokens = _tokens(text)
        self.pos = 0
        self.leaf = leaf  # a leaf's text to its node
        self.exception = exception  # the text after WITH to an exception id

    def parse(self) -> _Node:
        if not self.tokens:
            raise ValueError("empty expression")
        node = self._expr("OR")
        if self.pos != len(self.tokens):
            raise ValueError(f"unexpected {self.tokens[self.pos][1]!r}")
        return node

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise ValueError("expression ends too early")
        self.pos += 1
        return token

    def _expr(self, op: Literal["AND", "OR"]) -> _Node:
        sub = self._with if op == "AND" else lambda: self._expr("AND")
        args = [sub()]
        while self._peek() == ("op", op):
            self.pos += 1
            args.append(sub())
        return args[0] if len(args) == 1 else _Op(op, tuple(args))

    def _with(self) -> _Node:
        node = self._atom()
        if self._peek() == ("op", "WITH"):
            self.pos += 1
            kind, text = self._next()
            if kind != "leaf" or not isinstance(node, _Id) or node.exception is not None:
                raise ValueError("WITH needs one license id on each side")
            node = _Id(node.id, self.exception(text))
        return node

    def _atom(self) -> _Node:
        kind, text = self._next()
        if kind == "(":
            node = self._expr("OR")
            if self._next()[0] != ")":
                raise ValueError("unbalanced parentheses")
            return node
        if kind == "leaf":
            return self.leaf(text)
        raise ValueError(f"unexpected {text!r}")


def _canonical_leaf(text: str) -> _Id:
    if not _ID.match(text):
        raise ValueError(f"{text!r} is not an SPDX id")
    return _Id(text)


def _canonical_exception(text: str) -> str:
    return _canonical_leaf(text).id


def _parse_canonical(expr: str) -> _Node:
    """Parse an SPDX expression whose ids are written as they are (config values, rulings)."""
    return _Parser(expr, _canonical_leaf, _canonical_exception).parse()


def _simplify(node: _Node) -> _Node:
    """Flatten, de-duplicate and sort; NOASSERTION poisons AND and drops out of OR."""
    if isinstance(node, _Id):
        return _Id(NOASSERTION) if node.id == NOASSERTION else node
    flat: dict[str, _Node] = {}
    for arg in (_simplify(a) for a in node.args):
        for part in arg.args if isinstance(arg, _Op) and arg.op == node.op else (arg,):
            flat[_render(part, top=False)] = part
    if NOASSERTION in flat:
        if node.op == "AND" or len(flat) == 1:
            return _Id(NOASSERTION)
        del flat[NOASSERTION]
    args = tuple(flat[k] for k in sorted(flat))
    return args[0] if len(args) == 1 else _Op(node.op, args)


def _render(node: _Node, *, top: bool = True) -> str:
    if isinstance(node, _Id):
        return node.id if node.exception is None else f"{node.id} WITH {node.exception}"
    inner = f" {node.op} ".join(_render(a, top=False) for a in node.args)
    return inner if top else f"({inner})"


def canonical(expr: str) -> str:
    """The canonical form of an SPDX expression: operands sorted, duplicates dropped.

    Raises ``ValueError`` for a malformed expression.
    """
    return _render(_simplify(_parse_canonical(expr)))


def _leaves(node: _Node) -> Iterator[_Id]:
    if isinstance(node, _Id):
        yield node
    else:
        for arg in node.args:
            yield from _leaves(arg)


# --- L1: normalize ----------------------------------------------------------------------------


class AliasIndex(Mapping[str, str]):
    """``license-aliases.toml [aliases]`` with its lookups precomputed.

    ``normalize`` accepts a plain mapping too, but builds an index per call;
    the stage builds one and reuses it. Raises ``ValueError`` when a value is
    not a well-formed SPDX expression.
    """

    def __init__(self, table: Mapping[str, str]) -> None:
        self._table = dict(table)
        self._folded: dict[str, str] = {}
        self._globs: list[tuple[str, str]] = []
        self._canon: dict[str, str] = {s.casefold(): s for s in (NOASSERTION, NONE)}
        for key, value in sorted(self._table.items()):
            try:
                node = _parse_canonical(value)
            except ValueError as exc:
                raise ValueError(f"license-aliases.toml: {key!r} = {value!r}: {exc}") from exc
            if key.endswith("*"):
                self._globs.append((key[:-1].casefold(), value))
            else:
                self._folded.setdefault(key.casefold(), value)
            for leaf in _leaves(node):
                for ident in (leaf.id, leaf.exception):
                    if ident is not None:
                        self._canon.setdefault(ident.casefold(), ident)
        self._globs.sort(key=lambda g: (-len(g[0]), g[0]))

    def __getitem__(self, key: str) -> str:
        return self._table[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._table)

    def __len__(self) -> int:
        return len(self._table)

    def lookup(self, text: str, source: str) -> str | None:
        """The table's expression for ``text`` as ``source`` writes it (steps 1-3), or None."""
        keys = (f"{source}{SCOPE}{text}", text)
        for key in keys:
            if key in self._table:
                return self._table[key]
        for key in keys:
            hit = self._folded.get(key.casefold())
            if hit is not None:
                return hit
        ident = self._canon.get(text.casefold())
        if ident is not None:
            return ident
        if len(_tokens(text)) != 1 or _tokens(text)[0][0] != "leaf":
            return None  # a prefix key names one license, never a whole expression
        for key in keys:
            folded = key.casefold()
            for prefix, value in self._globs:
                if folded.startswith(prefix):
                    return value
        return None


def _as_index(table: Mapping[str, str]) -> AliasIndex:
    return table if isinstance(table, AliasIndex) else AliasIndex(table)


def _is_exception(ident: str) -> bool:
    # SPDX exception ids ("Font-exception-2.0", "PS-or-PDF-font-exception-20170817")
    # say so in their name; one standing alone is not a license.
    return "exception" in ident.casefold()


def normalize(raw: str, source: str, table: Mapping[str, str]) -> str | None:
    """L1: ``raw`` as ``source`` wrote it, to an SPDX expression, or None if unknown.

    An exception id outside ``WITH`` ("Font exception" on its own) is unknown
    too: it names no license, and guessing one would exclude the font silently.
    """
    index = _as_index(table)
    text = " ".join(raw.split())
    if not text:
        return None

    def leaf(part: str) -> _Node:
        value = index.lookup(part, source)
        if value is None:
            raise _Unknown(part)
        return _parse_canonical(value)

    def exception(part: str) -> str:
        value = index.lookup(part, source)
        if value is None or not _ID.match(value):
            raise _Unknown(part)
        return value

    whole = index.lookup(text, source)
    try:
        node = _parse_canonical(whole) if whole else _Parser(text, leaf, exception).parse()
    except _Unknown, ValueError:
        return None
    if any(_is_exception(part.id) for part in _leaves(node)):
        return None
    return _render(_simplify(node))


def _fact_expr(fact: LicenseFact, index: AliasIndex) -> str | None:
    """A fact's SPDX field when it speaks SPDX we can read, else its raw string."""
    for text in (fact.spdx, fact.raw):
        if text:
            expr = normalize(text, fact.source, index)
            if expr is not None:
                return expr
    return None


# --- classify ---------------------------------------------------------------------------------

_STATUS_RANK = {"allowed": 0, "ruling": 1, "excluded": 2}


def classify(expr: str, cfg: LicensesConfig) -> LicenseClass:
    """The class of an SPDX expression (``OR``: the best allowed branch; ``AND``: all must pass).

    Between equally good branches (or, for ``AND``, equally strict parts), the
    group that comes first in ``GROUP_ORDER`` names the class, then a license
    without an exception: "MIT OR OFL-1.1" and "MIT AND OFL-1.1" read as
    open-font, and Unifont's "GPL-2.0-or-later WITH Font-exception-2.0 OR
    OFL-1.1" as OFL-1.1.
    """
    return _classify(_simplify(_parse_canonical(expr)), cfg)


def _rank(c: LicenseClass) -> tuple[int, bool, str]:
    group = GROUP_ORDER.index(c.group) if c.group in GROUP_ORDER else len(GROUP_ORDER)
    return (group, " WITH " in c.spdx, c.spdx)


def _classify(node: _Node, cfg: LicensesConfig) -> LicenseClass:
    if isinstance(node, _Id):
        return _leaf_class(node, cfg)
    parts = [_classify(arg, cfg) for arg in node.args]
    if node.op == "OR":
        return min(parts, key=_goodness)
    spdx = _render(node)
    for status in ("excluded", "ruling"):
        bad = [p for p in parts if p.status == status]
        if bad:
            reasons = dict.fromkeys(f"{p.spdx}: {p.reason}" for p in bad)
            return LicenseClass(spdx, status, reason="; ".join(reasons))
    strictest = min(parts, key=_leniency)
    return LicenseClass(
        spdx,
        "allowed",
        group=strictest.group,
        redistributable=all(p.redistributable is True for p in parts),
        attribution_required=any(p.attribution_required is True for p in parts),
    )


def _goodness(c: LicenseClass) -> tuple[int, bool, bool, tuple[int, bool, str]]:
    return (
        _STATUS_RANK[c.status],
        c.redistributable is not True,
        c.attribution_required is True,
        _rank(c),
    )


def _leniency(c: LicenseClass) -> tuple[bool, bool, tuple[int, bool, str]]:
    return (c.redistributable is True, c.attribution_required is not True, _rank(c))


def _leaf_class(node: _Id, cfg: LicensesConfig) -> LicenseClass:
    key = _render(node)
    if key in cfg.allowed:
        entry = cfg.allowed[key]
        if not entry.redistributable:
            return LicenseClass(key, "excluded", reason=NOT_REDISTRIBUTABLE_REASON)
        return LicenseClass(
            key,
            "allowed",
            group=entry.group,
            redistributable=entry.redistributable,
            attribution_required=entry.attribution_required,
        )
    if key in cfg.excluded:
        return LicenseClass(key, "excluded", reason=cfg.excluded[key].reason)
    if key in cfg.ruling:
        return LicenseClass(key, "ruling", reason=cfg.ruling[key].question)
    if node.exception is not None:
        base = _leaf_class(_Id(node.id), cfg)
        if base.status == "allowed":  # an exception only adds permissions
            return replace(base, spdx=key)
        return replace(base, spdx=key, reason=f"{base.reason}; {node.exception} is not listed")
    if key == NOASSERTION:
        return LicenseClass(key, "excluded", reason=NO_LICENSE_REASON)
    if key == NONE:
        return LicenseClass(key, "excluded", reason="the source says there is no license (D3)")
    return LicenseClass(
        key,
        "excluded",
        reason="not listed in config/licenses.toml (D3: anything not listed is excluded)",
    )


def _ruling_ids(spdx: str, cfg: LicensesConfig) -> tuple[str, ...]:
    """The ruling-class licenses that ``spdx`` (a class's expression) waits on."""
    out = set()
    for leaf in _leaves(_parse_canonical(spdx)):
        key = _render(leaf)
        if key in cfg.ruling:
            out.add(key)
        elif leaf.exception is not None and key not in cfg.allowed and leaf.id in cfg.ruling:
            out.add(leaf.id)
    return tuple(sorted(out))


def _preview_ok(c: LicenseClass | None) -> bool:
    return c is not None and c.status == "allowed" and c.redistributable is True


# --- L2: cross_check --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Check:
    """One family's verdict plus what the queue needs to know (``classify_all``)."""

    verdict: Verdict
    kinds: tuple[QueueKind, ...] = ()  # family-level reasons: disagreement ... research
    unknown: tuple[tuple[str, str], ...] = ()  # (source, raw) that L1 could not read
    ruling_ids: tuple[str, ...] = ()  # ruling-class licenses the class waits on


def watch_note(family_id: str) -> str | None:
    """Why ``family_id`` is on the ``WATCH`` list (itself or as a variant "<id>-..."), or None."""
    for watched in sorted(WATCH, key=lambda w: (-len(w), w)):
        if family_id == watched or family_id.startswith(f"{watched}-"):
            return WATCH[watched]
    return None


def _precedence(source: str) -> tuple[int, str]:
    return (SOURCE_ORDER.index(source) if source in SOURCE_ORDER else len(SOURCE_ORDER), source)


def cross_check(
    family_id: str, facts: Sequence[LicenseFact], table: Mapping[str, str], cfg: LicensesConfig
) -> Verdict:
    """L2: combine every source's license fact for one family."""
    return _check(family_id, facts, _as_index(table), cfg).verdict


def _opinions(readings: Sequence[tuple[str, str | None]]) -> dict[str, str]:
    """One expression per source, by precedence; several facts from one source are ANDed.

    A source whose facts AND to NOASSERTION ("MIT" plus Arch's bare "custom":
    part of the package is under a license nobody named) states nothing usable,
    the same as the single string "MIT AND custom" in ``normalize``.
    """
    by_source: dict[str, set[str]] = {}
    for source, expr in readings:
        if expr is not None:
            by_source.setdefault(source, set()).add(expr)
    out = {}
    for source in sorted(by_source, key=_precedence):
        exprs = sorted(by_source[source])
        expr = exprs[0] if len(exprs) == 1 else canonical(" AND ".join(f"({e})" for e in exprs))
        if expr != NOASSERTION:
            out[source] = expr
    return out


def _check(
    family_id: str, facts: Sequence[LicenseFact], index: AliasIndex, cfg: LicensesConfig
) -> Check:
    readings = [(f.source, _fact_expr(f, index)) for f in facts]
    seen = tuple(sorted(set(readings), key=lambda p: (p[0], p[1] or "")))
    unknown = tuple(
        sorted({(f.source, f.raw) for f, (_, e) in zip(facts, readings, strict=True) if e is None})
    )
    opinions = _opinions(readings)
    kinds: list[QueueKind] = []
    reasons: list[str] = []
    spdx: str | None = None
    if not facts:
        lic = LicenseClass(NOASSERTION, "excluded", reason=NO_LICENSE_REASON)
    elif not opinions:
        lic = LicenseClass(NOASSERTION, "ruling", reason="no source states a license L1 can read")
        if not unknown:
            kinds.append("noassertion")
            said = ", ".join(sorted({f"{s} {e}" for s, e in readings}))
            reasons.append(f"noassertion: no source states a usable license ({said})")
    else:
        spdx = next(iter(opinions.values()))
        lic = classify(spdx, cfg)
        distinct = set(opinions.values())
        # Sources that disagree only about how a font is excluded need no ruling (URW's AGPL).
        if len(distinct) > 1 and any(classify(e, cfg).status != "excluded" for e in distinct):
            kinds.append("disagreement")
            reasons.append("disagreement: " + "; ".join(f"{s} {e}" for s, e in opinions.items()))
    if unknown:
        reasons.append("unreadable: " + "; ".join(f"{s} {raw!r}" for s, raw in unknown))
    ruling_ids = _ruling_ids(lic.spdx, cfg) if lic.status == "ruling" and spdx else ()
    if ruling_ids:
        reasons.append(f"license: {', '.join(ruling_ids)} needs a ruling")
    note = watch_note(family_id)
    if note is not None:
        kinds.append("watch")
        reasons.append(f"watch: {note}")
    verdict = Verdict(
        family_id=family_id,
        spdx=spdx,
        license=lic,
        preview_ok=_preview_ok(lic),
        seen=seen,
        queue=" | ".join(reasons) or None,
    )
    return Check(verdict, tuple(kinds), unknown, ruling_ids)


# --- rulings ----------------------------------------------------------------------------------


def _question_id(name: str) -> str:
    """``LIC-<name>``, cut to the rulings schema's 64 characters with a hash when longer."""
    qid = f"{GATE}-{name}"
    if len(qid) <= MAX_QUESTION_ID:
        return qid
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return f"{qid[: MAX_QUESTION_ID - 9].rstrip('-')}-{digest}"


def family_question_id(family_id: str) -> str:
    """The gate LIC question id for a family."""
    return _question_id(family_id)


def license_question_id(spdx: str) -> str:
    """The gate LIC question id for a license in licenses.toml."""
    return _question_id(f"spdx-{slugify(spdx)}")


def _values(answer: Answer) -> dict[str, object]:
    return dict(answer.values)


def _choice(answer: Answer) -> str:
    if answer.choice not in CHOICES:
        raise RulingError(f"{answer.id}: choice must be one of {', '.join(CHOICES)}")
    return answer.choice


def _group(values: Mapping[str, object], fallback: str | None, choice: str) -> str:
    group = values.get("group", fallback or DEFAULT_GROUPS[choice])
    if not isinstance(group, str):
        raise RulingError(f"group must be a string, got {group!r}")
    return group


def _flag(answer: Answer, values: Mapping[str, object], key: str, default: bool) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        raise RulingError(f"{answer.id}: {key} must be true or false, got {value!r}")
    return value


def load_answers(paths: Paths) -> dict[str, tuple[Answer, date]]:
    """Gate LIC answers by question id; a later ruling overrides an earlier one.

    Built on ``reviews.load_rulings``, the frozen part of the reviews contract.
    """
    out: dict[str, tuple[Answer, date]] = {}
    for ruling in reviews.load_rulings(paths, GATE):
        for answer in ruling.answers:
            out[answer.id] = (answer, ruling.day)
    return out


def apply_license_rulings(
    cfg: LicensesConfig, answers: Mapping[str, tuple[Answer, date]]
) -> LicensesConfig:
    """``cfg`` with every license the owner ruled on moved to allowed or excluded."""
    ids = {license_question_id(i): i for i in (*cfg.allowed, *cfg.excluded, *cfg.ruling)}
    allowed, excluded, ruling = dict(cfg.allowed), dict(cfg.excluded), dict(cfg.ruling)
    for qid in sorted(answers):
        spdx = ids.get(qid)
        answer, day = answers[qid]
        if spdx is None or _choice(answer) == "d":
            continue
        values = _values(answer)
        how = f"owner ruling {qid} ({day.isoformat()}): {answer.ruling}"
        old = allowed.get(spdx)
        for table in (allowed, excluded, ruling):
            table.pop(spdx, None)
        if answer.choice in EXCLUDING:
            if answer.choice == "b":
                how = f"{how} ({NOT_REDISTRIBUTABLE_REASON})"
            excluded[spdx] = ExcludedLicense(reason=how)
            continue
        advice = RULING_ADVICE.get(spdx, (None, None))[1]
        allowed[spdx] = AllowedLicense(
            name=str(values.get("name", old.name if old else spdx)),
            group=_group(values, old.group if old else advice, answer.choice),
            redistributable=True,  # only (a) gets here (Rule 3)
            attribution_required=_flag(
                answer,
                values,
                "attribution_required",
                old.attribution_required if old else False,
            ),
            note=how,
        )
    return replace(cfg, allowed=allowed, excluded=excluded, ruling=ruling)


def effective_config(paths: Paths, cfg: LicensesConfig) -> LicensesConfig:
    """``licenses.toml`` with gate LIC's license rulings applied, as stage "licenses" uses it.

    Later stages that check ids against the allowed list (L3, export) should
    read this rather than ``config.licenses``, so a license the owner allowed at
    gate LIC counts as allowed there too.
    """
    return apply_license_rulings(cfg, load_answers(paths))


def _promote_rulings(cfg: LicensesConfig, choice: str) -> LicensesConfig:
    """``cfg`` as if the owner had answered ``choice`` for every ruling-class license."""
    allowed = dict(cfg.allowed)
    for spdx in cfg.ruling:
        advice = RULING_ADVICE.get(spdx, (None, None))[1]
        allowed[spdx] = AllowedLicense(
            name=spdx,
            group=advice or DEFAULT_GROUPS[choice],
            redistributable=choice == "a",
            attribution_required=False,
        )
    return replace(cfg, allowed=allowed, ruling={})


def _apply_family_ruling(check: Check, answer: Answer, day: date, cfg: LicensesConfig) -> Check:
    """The family's class as the owner ruled it; "d" keeps it in the queue.

    An (a) answer must name the license (``spdx``) when the sources name none,
    or when the provisional one is excluded: otherwise the catalog would
    publish, say, CC-BY-SA-4.0 as an allowed license. (b) and (c) exclude.
    """
    v = check.verdict
    choice = _choice(answer)
    how = f"owner ruling {answer.id} ({day.isoformat()}): {answer.ruling}"
    if choice == "d":
        queue = " | ".join(q for q in (v.queue, f"research: {how}") if q)
        return replace(check, verdict=replace(v, queue=queue), kinds=check.kinds or ("research",))
    values = _values(answer)
    spdx = values.get("spdx", v.spdx)
    if spdx is not None and not isinstance(spdx, str):
        raise RulingError(f"{answer.id}: spdx must be a string")
    if spdx is not None:
        try:
            spdx = canonical(spdx)
        except ValueError as exc:
            raise RulingError(f"{answer.id}: spdx {spdx!r}: {exc}") from exc
    if choice in EXCLUDING:
        if choice == "b":
            how = f"{how} ({NOT_REDISTRIBUTABLE_REASON})"
        lic = LicenseClass(spdx or NOASSERTION, "excluded", reason=how)
    else:
        if spdx is None:
            raise RulingError(f"{answer.id}: the sources name no license, so the ruling needs spdx")
        base = classify(spdx, _promote_rulings(cfg, choice))
        ok = base.status == "allowed"
        if not ok and "spdx" not in values:
            raise RulingError(
                f"{answer.id}: {spdx} is {base.status} in licenses.toml, so a ruling that lets "
                "the family in needs spdx, the license it is under"
            )
        lic = LicenseClass(
            spdx,
            "allowed",
            group=_group(values, base.group if ok else None, choice),
            redistributable=True,  # only (a) gets here (Rule 3)
            attribution_required=_flag(
                answer, values, "attribution_required", bool(ok and base.attribution_required)
            ),
            reason=how,
        )
    verdict = replace(v, spdx=spdx, license=lic, preview_ok=_preview_ok(lic), queue=None)
    return Check(verdict)


# --- the queue --------------------------------------------------------------------------------


def _describe(c: LicenseClass | None) -> str:
    if c is None:
        return "no class"
    if c.status != "allowed":
        return f"{c.spdx}, {c.status}"
    extras = [f"group {c.group}", "redistributable" if c.redistributable else "not redistributable"]
    if c.attribution_required:
        extras.append("attribution required")
    return f"{c.spdx}, allowed ({', '.join(extras)})"


def _recommend(c: LicenseClass | None, ruling_ids: Sequence[str]) -> int:
    """Index into OPTIONS: follow the provisional class, or the advice for its ruling licenses."""
    if c is not None and c.status == "allowed":
        return 0 if c.redistributable else 1
    if c is not None and c.status == "excluded":
        return 2
    advice = {RULING_ADVICE.get(i, ("d", None))[0] for i in ruling_ids}
    return CHOICES.index(advice.pop()) if len(advice) == 1 else 3


def _names(fids: Sequence[str], universe: Universe, limit: int = 8) -> str:
    shown = [universe.families[f].family for f in fids[:limit]]
    more = len(fids) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more else "")


def _family_item(check: Check, fam: Family) -> QueueItem:
    v = check.verdict
    qid = family_question_id(fam.id)
    detail = v.queue or ""
    text = (
        f"{fam.family} ({fam.id}): {detail}. Provisional class: {_describe(v.license)}. "
        "Does it qualify, and may it be redistributed?"
    )
    if v.spdx is None or v.license is None or v.license.status == "excluded":
        text += " An answer of (a) must name the license it is under (spdx)."
    question = reviews.Question(GATE, qid, text, OPTIONS, _recommend(v.license, check.ruling_ids))
    return QueueItem(qid, check.kinds[0], fam.id, (fam.id,), detail, question)


def _license_item(
    spdx: str, fids: Sequence[str], cfg: LicensesConfig, universe: Universe, note: str | None
) -> QueueItem:
    qid = license_question_id(spdx)
    detail = f"{spdx} is used by {len(fids)} candidate families: {_names(fids, universe)}"
    if note:
        detail += f" ({note})"
    text = f"{cfg.ruling[spdx].question} Used by {_names(fids, universe)}."
    question = reviews.Question(GATE, qid, text, OPTIONS, _recommend(None, (spdx,)))
    return QueueItem(qid, "license", spdx, tuple(fids), detail, question)


def _alias_item(source: str, raw: str, fids: Sequence[str], universe: Universe) -> QueueItem:
    digest = hashlib.sha256(f"{source}{SCOPE}{raw}".encode()).hexdigest()[:10]
    detail = (
        f"L1 cannot read {raw!r} from {source} ({_names(fids, universe)}): "
        "add it to config/license-aliases.toml"
    )
    return QueueItem(f"L1-{digest}", "alias", f"{source}: {raw}", tuple(fids), detail, None)


def build_queue(
    checks: Mapping[str, Check],
    candidates: Iterable[str],
    universe: Universe,
    cfg: LicensesConfig,
    answers: Mapping[str, tuple[Answer, date]],
    unmapped: int = 0,
) -> Queue:
    """The review queue for the candidate families (sorted, deterministic)."""
    cands = sorted(set(candidates) & set(checks))
    items: list[QueueItem] = []
    by_license: dict[str, list[str]] = {}
    by_string: dict[tuple[str, str], list[str]] = {}
    for fid in cands:
        check = checks[fid]
        if check.kinds:
            items.append(_family_item(check, universe.families[fid]))
        for spdx in check.ruling_ids:
            by_license.setdefault(spdx, []).append(fid)
        for pair in check.unknown:
            by_string.setdefault(pair, []).append(fid)
    for spdx, fids in sorted(by_license.items()):
        answered = answers.get(license_question_id(spdx))
        note = f"research requested {answered[1].isoformat()}" if answered else None
        items.append(_license_item(spdx, fids, cfg, universe, note))
    for (source, raw), fids in sorted(by_string.items()):
        items.append(_alias_item(source, raw, fids, universe))
    items.sort(key=lambda i: (_KIND_ORDER[i.kind], i.id))
    verdicts = [checks[f].verdict for f in cands]
    counts = {
        "candidates": len(cands),
        "unmapped_facts": unmapped,
        "no_license": sum(1 for v in verdicts if not v.seen),
    }
    for status in ("allowed", "excluded", "ruling"):
        counts[status] = sum(1 for v in verdicts if v.license and v.license.status == status)
    no_license = tuple(v.family_id for v in verdicts if not v.seen)
    return Queue(QUEUE_SCHEMA, tuple(items), counts, no_license)


# --- the stage --------------------------------------------------------------------------------


def queue_path(paths: Paths) -> Path:
    """``build/stage/queues/licenses.json``."""
    return paths.queues / QUEUE_FILE


def load_queue(paths: Paths) -> Queue:
    """Read the queue the last ``licenses`` run wrote."""
    return stageio.load(queue_path(paths), Queue)


def questions(paths: Paths) -> list[reviews.Question]:
    """Gate LIC's open owner questions, from the last run's queue (for ``reviews.questions``)."""
    return [i.question for i in load_queue(paths).items if i.question is not None]


def license_facts(paths: Paths) -> Iterator[LicenseFact]:
    """Every LicenseFact in ``build/stage/records/<source>.jsonl`` (baseline files skipped)."""
    if not paths.records.is_dir():
        raise FileNotFoundError(f"{paths.records} is missing: run `tff-catalog parse` first")
    for path in sorted(paths.records.glob("*.jsonl")):
        if "@" in path.name:
            continue
        for row in jsonio.iter_jsonl(path):
            if row.get("type") == "LicenseFact":
                fact = from_json(row)
                if isinstance(fact, LicenseFact):
                    yield fact


class _Resolver:
    """A license fact's key to families: universe keys, then reviewed aliases.csv rows.

    Like ``Universe.by_key``, a key that differs from a universe key only by
    ``match_key`` folding counts only when one family holds it: a fact that
    could belong to two families belongs to neither (it is counted unmapped).
    """

    def __init__(self, universe: Universe, rows: Iterable[AliasRow]) -> None:
        self.exact: dict[SourceKey, set[str]] = {}
        self.folded: dict[tuple[str, str], set[str]] = {}
        self.aliases: dict[tuple[str, str], set[str]] = {}
        for fam in universe.families.values():
            for key in fam.keys:
                self.exact.setdefault(key, set()).add(fam.id)
                self.folded.setdefault((key.ns, match_key(key.key)), set()).add(fam.id)
            # A formula's license covers every family it ships: a several-families key is
            # in no family's keys, only in each such family's ``shared``.
            for key, _ in fam.shared:
                self.exact.setdefault(key, set()).add(fam.id)
        for row in rows:
            if row.relation in FACT_RELATIONS and row.family_id:
                self.aliases.setdefault((row.ns, match_key(row.alias)), set()).add(row.family_id)

    def __call__(self, key: SourceKey) -> set[str]:
        if key in self.exact:
            return self.exact[key]
        folded = (key.ns, match_key(key.key))
        near = self.folded.get(folded, set())
        if near:
            return near if len(near) == 1 else set()
        return self.aliases.get(folded, set())


def _alias_rows(paths: Paths) -> list[AliasRow]:
    return load_aliases(paths.aliases_csv) if paths.aliases_csv.is_file() else []


def _candidates(paths: Paths, universe: Universe, log: logging.Logger) -> set[str]:
    """Eligible families that pass the Latin gate (all eligible ones before stage "latin" ran)."""
    eligible = {fid for fid, fam in universe.families.items() if fam.drop is None}
    if not stageio.stage_path(paths, "latin").is_file():
        log.warning("no latin.json: every eligible family counts as a candidate")
        return eligible
    latin = stageio.load_stage(paths, "latin")
    return {fid for fid in eligible if fid in latin and latin[fid].latin}


def _check_groups(checks: Mapping[str, Check], site: SiteConfig) -> None:
    classes = {c.id for c in site.license_classes}
    for fid, check in sorted(checks.items()):
        lic = check.verdict.license
        if lic is not None and lic.status == "allowed" and lic.group not in classes:
            raise RulingError(f"{fid}: group {lic.group!r} is not in site.toml license_classes")


def classify_all(
    universe: Universe,
    facts: Iterable[LicenseFact],
    rows: Iterable[AliasRow],
    table: Mapping[str, str],
    cfg: LicensesConfig,
    answers: Mapping[str, tuple[Answer, date]],
) -> tuple[dict[str, Check], int]:
    """Every eligible family's check, with its gate LIC ruling applied; plus the unmapped-fact count.

    ``cfg`` already carries the license rulings (``apply_license_rulings``).
    """
    index = _as_index(table)
    resolve = _Resolver(universe, rows)
    by_family: dict[str, list[LicenseFact]] = {}
    unmapped = 0
    for fact in facts:
        fids = resolve(fact.key)
        if not fids:
            unmapped += 1
        for fid in fids:
            by_family.setdefault(fid, []).append(fact)
    checks: dict[str, Check] = {}
    for fid, fam in sorted(universe.families.items()):
        if fam.drop is not None:
            continue
        check = _check(fid, by_family.get(fid, ()), index, cfg)
        ruled = answers.get(family_question_id(fid))
        if ruled is not None:
            check = _apply_family_ruling(check, *ruled, cfg)
        checks[fid] = check
    return checks, unmapped


def run(ctx: StageContext) -> None:
    """Stage "licenses"."""
    paths = ctx.paths
    if not stageio.stage_path(paths, "universe").is_file():
        raise FileNotFoundError(
            "build/stage/universe.json is missing: run `tff-catalog universe` first"
        )
    universe = stageio.load_stage(paths, "universe")
    answers = load_answers(paths)
    cfg = apply_license_rulings(ctx.config.licenses, answers)
    checks, unmapped = classify_all(
        universe,
        license_facts(paths),
        _alias_rows(paths),
        AliasIndex(ctx.config.license_aliases.aliases),
        cfg,
        answers,
    )
    _check_groups(checks, ctx.config.site)
    listed = (*cfg.allowed, *cfg.excluded, *cfg.ruling)  # every listed license, ruled or not
    known = {license_question_id(s) for s in listed} | {family_question_id(f) for f in checks}
    for qid in sorted(set(answers) - known):
        ctx.log.warning("gate LIC ruling %s names no listed license or eligible family", qid)
    candidates = _candidates(paths, universe, ctx.log)
    queue = build_queue(checks, candidates, universe, cfg, answers, unmapped)
    stageio.dump_stage(paths, "licenses", {fid: c.verdict for fid, c in checks.items()})
    stageio.dump(queue, queue_path(paths))
    ctx.log.info(
        "licenses: %s; %d queue items",
        ", ".join(f"{k} {v}" for k, v in sorted(queue.counts.items())),
        len(queue.items),
    )


def render_queue(queue: Queue) -> str:
    """The queue as text: each item, its evidence and, for owner items, the lettered options."""
    lines = [f"{len(queue.items)} license queue items"]
    for item in queue.items:
        text = item.question.text if item.question is not None else item.detail
        lines += ["", f"{item.id}  [{item.kind}]  {item.subject}", f"  {text}"]
        if item.question is not None:
            for i, option in enumerate(item.question.options):
                rec = " (rec)" if i == item.question.recommended else ""
                lines.append(f"  ({CHOICES[i]}) {option}{rec}")
    return "\n".join(lines) + "\n"


def cmd_queue(ctx: StageContext, *, count: bool = False) -> int:
    """``licenses --queue [--count]``: print the review queue (or its size)."""
    if not queue_path(ctx.paths).is_file():
        print(
            "tff-catalog licenses: no queue yet; run `tff-catalog licenses` first", file=sys.stderr
        )
        return 1
    queue = load_queue(ctx.paths)
    sys.stdout.write(f"{len(queue.items)}\n" if count else render_queue(queue))
    return 0
