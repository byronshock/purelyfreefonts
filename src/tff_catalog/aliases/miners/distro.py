"""Alias miner "distro": Arch and Debian package names to families (milestone-1 step 7).

Arch, CachyOS and Debian count installs per package (pkgstats, popcon) and
declare dependencies between packages, but no universe collector lists a
package as a family, so every ``arch-pkg``, ``deb-pkg`` and ``deb-src`` key
reaches a family only through an alias row. This miner proposes those rows:
relation ``package`` (a distro's package of the family), or ``build`` for a
package that holds one build only: a Nerd Fonts patched build (detail ``nerd``,
``nf``, ``nfm`` or ``nfp``, which the correction stage credits at
``nerd_credit``), a CJK build (``cjk``), or the variable or static fonts alone.
It never proposes bundles: which families a multi-family package such as
``ttf-ibm-plex`` holds is the owner's call (``build/unmatched.md``). Every
candidate waits for the owner (gate A); none is ``auto``.

**Keys.** ``arch-pkg``: the font-like packages of the ``arch_repos`` snapshot
(Arch and CachyOS) and every pkgstats name with a font affix (AUR packages and
old names included). ``deb-pkg``: the font-role packages of the ``debian``
Packages extract and every popcon name with a font affix. ``deb-src``: the
source packages of ``debian_copyright`` (its license facts are keyed by them)
and the sources of Debian's font packages.

**Evidence.** Every rule is exact; nothing is guessed by prefix, so
``fonts-roboto-slab`` can only ever be Roboto Slab, never Roboto.

1. *Name.* ``stem`` cuts fixed affixes off a package name: for Arch a ``-git``
   or ``-bin`` suffix, then one packaging prefix (``ttf-``, ``otf-``,
   ``fonts-``...), format suffixes (``-fonts``, ``-ttf``...) and one build affix
   (``-nerd``, ``nerd-fonts-``, ``-nf``, ``-cn``, ``-variable``, ``-vf``,
   ``-static``). What is left must have the ``match_key`` of one family's name
   or key (a Google name, a Fontsource id, a cask token without ``font-``...),
   and of no other family's. A Nerd build is matched against the Nerd Fonts
   folder names first, so its row follows the ``nerd-folder`` key's family. A
   name with no font affix (a ``-git`` suffix is not one) is matched only when a
   collector flagged the package as a font. An Arch package's ``replaces``
   names (its former names) count as its own names too.
2. *Upstream-Name* (DEP-5), for its source package and, when the source has
   only one font binary, for that binary; and *upstream URLs*: Arch ``%URL%``,
   Debian ``Homepage`` and the DEP-5 ``Source`` URLs, normalised by ``url_key``,
   equal to a homepage, repository, minisite or specimen URL of one family. An
   Upstream-Name or URL counts only when it names exactly one family and every
   package that gives it either matches that family by name or matches nothing
   by name and shares one stem with the others (a source's only font binary
   counts with the source's stem). So a repository of several
   products (``github.com/mozilla/Fira``) and a DEP-5 header copied from
   another package never pair, and aggregator URLs never do either
   (``AGGREGATOR_URLS``; the googlefontdirectory-hg mirror above all). Nor do
   documentation, tool and library packages (``NON_FONT_SUFFIXES``).
   Upstream-Names count as names; a URL decides only when no name does.
3. *Declared renames.* An Arch package's ``replaces`` names, a Debian
   transitional package (``transitional`` in its description, depending on
   exactly one package) and a provided name with the provider's own stem and
   build (``ttf-engadget`` from ``fonts-engadget``) get the row of the package
   that replaces, succeeds or provides them, when every such package agrees and
   the name's own evidence does not point elsewhere (its name counts even when
   no snapshot lists it).
4. *Binaries.* A source package gets the family its font binaries all agree on
   (``-doc``, ``-bin`` and other ``NON_FONT_SUFFIXES`` binaries left out),
   unless its own evidence names another.

Evidence naming two families gives no candidate. A candidate's ``evidence``
lists what matched (``name``, ``replaces``, ``upstream-name``, ``url``,
``replaced by``, ``provided by``, ``transitional to``, ``binary``) and, when
no URL agrees, the package's homepages as ``upstream <url>``, so the owner can
spot a same-named stranger. The seed file is public, and ruling T1 opens
package data, not the DEP-5 copyright files, which are used as facts only
(docs/sources.md): their ``Source`` URLs can pair a package but are never
listed, and a header shows only as the name it gives (``upstream-name``). A
candidate's target is the family key the evidence matched (by
``TARGET_NS_ORDER``); the merge resolves it against the universe as it stands
then.

**Inputs** (read-only): ``build/stage/universe.json`` (every family, dropped
ones too, so an icon font's package resolves to the dropped family instead of
reaching ``unmatched.md``) and the snapshots of ``SOURCES`` that the run's
parse stage used (``build/stage/snapshots.json``), else the newest on or
before the run date. A missing input raises ``DistroInputError``, so the
committed ``data/alias-seeds/distro.csv`` stays (``aliases.mine_all``).
"""

import csv
import io
import logging
import re
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar
from urllib.parse import unquote, unquote_plus, urlsplit

from tff_catalog import stageio
from tff_catalog.aliases import NAME_NAMESPACES, AliasCandidate
from tff_catalog.aliases.miners import MineContext
from tff_catalog.keys import match_key
from tff_catalog.records import SourceKey
from tff_catalog.store import Snapshot, Store

NAME = "distro"
ARCH, PKGSTATS, DEBIAN, COPYRIGHT = "arch_repos", "pkgstats", "debian", "debian_copyright"
SOURCES = (ARCH, PKGSTATS, DEBIAN, COPYRIGHT)
# The extract formats this miner reads (collector_version); a newer snapshot raises.
FORMAT_VERSIONS = {ARCH: 1, PKGSTATS: 1, DEBIAN: 1, COPYRIGHT: 1}
ARCH_REPOS = "repos.json"  # arch_repos: the repositories and their extracts
DEB_PACKAGES = "packages.jsonl.gz"
DEB_POPCON = "popcon.jsonl.gz"
DEB_COPYRIGHT = "copyright.jsonl.gz"
PKGSTATS_MONTH = re.compile(r"^month-\d{4}-\d{2}\.csv\.gz$")
ARCH_NS, DEB_NS, SRC_NS = "arch-pkg", "deb-pkg", "deb-src"

# Affixes ``stem`` cuts (lower case); where one ends another, the longer comes first.
PREFIXES = ("woff2-", "woff-", "ttf-", "otf-", "ttc-", "otb-", "pcf-", "bdf-", "xfonts-", "fonts-")
PREFIXES += ("t1-",)
FORMAT_SUFFIXES = ("-font-family", "-fonts", "-font", "-ttf", "-otf", "-ttc", "-otb")
FORMAT_SUFFIXES += ("-woff2", "-woff")
PREFIXED_SUFFIXES = ("-web",)  # cut only after a font prefix: many programs end in -web
VCS_SUFFIXES = ("-git", "-bin")  # Arch only: AUR builds from a repository or a release
BUILD_PREFIXES = (("nerd-fonts-", "nerd"),)  # the old AUR names of Nerd builds
BUILD_SUFFIXES = (
    ("-nerd-fonts", "nerd"),
    ("-nerdfonts", "nerd"),
    ("-nerd-font", "nerd"),
    ("-nerd", "nerd"),
    ("-nfm", "nfm"),
    ("-nfp", "nfp"),
    ("-nf", "nf"),
    ("-variable", "variable"),
    ("-vf", "variable"),
    ("-static", "static"),
    ("-cn", "cjk"),
)
NERD_DETAILS = frozenset({"nerd", "nf", "nfm", "nfp"})  # aliases.BUILD_DETAILS of Nerd builds
# Packages that hold no fonts; they never take a family from a URL or an Upstream-Name.
NON_FONT_SUFFIXES = ("-doc", "-docs", "-dev", "-dbg", "-bin", "-utils", "-tools", "-common")
NON_FONT_SUFFIXES += ("-examples",)

# Which family key a candidate targets, best first; a family with none is named.
TARGET_NS_ORDER = ("gf-family", "fs-id", "foundry-family", "brew-cask", "fontist-formula")
TARGET_NS_ORDER += ("gf-dir", "nerd-folder")
NAME_TARGET_NS = "font-name"
CASK_PREFIX = "font-"  # Homebrew font cask tokens
NAME_KEY_NS = frozenset({"fs-id", "gf-dir", "fontist-formula"}) | NAME_NAMESPACES
URL_ROLES = frozenset({"homepage", "repository", "minisite", "specimen"})
# Pages that list many families: they never pair a package with one.
AGGREGATOR_URLS = (
    "googlefontdirectory",  # the old Google Fonts hg mirror (milestone-1 step 7)
    "github.com/google/fonts",
    "github.com/ryanoasis/nerd-fonts",
    "github.com/notofonts",
    "notofonts.github.io",
    "google.com/get/noto",
    "fonts.google.com/noto",
)
# Hosts whose first two path segments name a project (owner/repository).
FORGE_HOSTS = frozenset({"github.com", "gitlab.com", "codeberg.org", "bitbucket.org"})
FORGE_HOSTS |= {"gitlab.gnome.org", "salsa.debian.org", "sr.ht"}
SPECIMEN_HOST = "fonts.google.com"
# Open owner question (2026-09-26): list package homepages in the public seed file as
# ``upstream <url>``? They show a same-named stranger; some are designers' own sites.
LIST_HOMEPAGES = True
_TRANSITIONAL = re.compile(r"\btransitional\b", re.IGNORECASE)
_PKG_NAME = re.compile(r"^[a-z0-9][a-z0-9.+_-]*")  # a package name before any version constraint


class DistroInputError(RuntimeError):
    """An input the miner needs is missing or in an unknown format."""


# --- names and URLs -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stem:
    """What ``stem`` leaves of a package name."""

    text: str  # lower case, affixes cut
    detail: str  # build detail (``BUILD_SUFFIXES``), or "" for the family's own package
    affixed: bool  # a font affix was cut: a packaging prefix, a format suffix or a Nerd affix

    @property
    def key(self) -> str:
        return match_key(self.text)


def _cut_suffixes(s: str, affixes: tuple[str, ...]) -> str:
    """Cut suffixes until none is left (``terminus-font-otb`` -> ``terminus``)."""
    while cut := next((a for a in affixes if s.endswith(a) and len(s) > len(a)), None):
        s = s[: -len(cut)]
    return s


def stem(name: str, *, vcs: bool = False) -> Stem:
    """Cut the fixed affixes off a package name (module docstring, rule 1).

    ``vcs`` (Arch names) first cuts a ``-git`` or ``-bin`` suffix, which is not
    a font affix: ``whisper-git`` is a program, not the Whisper font.
    """
    s = name.strip().lower()
    if vcs:
        s = _cut_suffixes(s, VCS_SUFFIXES)
    detail = ""
    for prefix, build in BUILD_PREFIXES:
        if s.startswith(prefix) and len(s) > len(prefix):
            s, detail = s[len(prefix) :], build
            break
    prefix = next((p for p in PREFIXES if s.startswith(p) and len(s) > len(p)), "")
    suffixes = FORMAT_SUFFIXES + (PREFIXED_SUFFIXES if prefix else ())
    bare = s[len(prefix) :]
    s = _cut_suffixes(bare, suffixes)
    affixed = bool(detail or prefix) or s != bare
    if not detail:
        for suffix, build in BUILD_SUFFIXES:
            if s.endswith(suffix) and len(s) > len(suffix):
                rest = s[: -len(suffix)]
                s, detail = _cut_suffixes(rest, suffixes), build
                affixed = affixed or build in NERD_DETAILS or s != rest
                break
    return Stem(s, detail, affixed)


def key_stem(key: SourceKey) -> Stem:
    """``stem`` of a package key, with Arch's ``-git``/``-bin`` cut."""
    return stem(key.key, vcs=key.ns == ARCH_NS)


def is_non_font(key: SourceKey) -> bool:
    """Documentation, tools and libraries (``NON_FONT_SUFFIXES``).

    An Arch name is read after its ``-git``/``-bin`` cut: ``ttf-foo-bin`` is a
    release build of the fonts, not a program.
    """
    name = key.key.lower()
    if key.ns == ARCH_NS:
        name = _cut_suffixes(name, VCS_SUFFIXES)
    return name.endswith(NON_FONT_SUFFIXES)


def url_key(url: str | None) -> str | None:
    """A URL as ``host/path`` for comparison, or None when it cannot pair anything.

    Scheme, ``www.``, query, fragment, ``.git`` and trailing slashes are dropped
    and everything is lower case. On a forge (``FORGE_HOSTS``) only
    ``owner/repository`` is kept; a Google Fonts specimen keeps its family name
    with ``+`` read as a space. Aggregator URLs and bare forge hosts give None.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    host = host.removeprefix("www.")
    path = (unquote_plus if host == SPECIMEN_HOST else unquote)(parts.path).lower()
    segments = [s for s in path.split("/") if s]
    if host in FORGE_HOSTS:
        if len(segments) < 2:
            return None
        segments = segments[:2]
    out = "/".join([host, *segments]).removesuffix(".git")
    return None if any(a in out for a in AGGREGATOR_URLS) else out


# --- the families -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FamilyInfo:
    """What the miner needs of a universe family."""

    id: str
    name: str
    keys: tuple[SourceKey, ...] = ()
    urls: tuple[tuple[str, str], ...] = ()  # (role, url)


def families_of(universe: Any) -> list[FamilyInfo]:
    """``FamilyInfo`` of every family of a ``universe.Universe``, dropped ones included."""
    return [
        FamilyInfo(f.id, f.family, tuple(f.keys), tuple(f.urls))
        for f in sorted(universe.families.values(), key=lambda f: f.id)
    ]


def _key_name(key: SourceKey) -> str | None:
    """The name a family key stands for, as a stem reads it (None: not a name)."""
    if key.ns == "brew-cask":
        return key.key.removeprefix(CASK_PREFIX) if key.key.startswith(CASK_PREFIX) else None
    return key.key if key.ns in NAME_KEY_NS else None


def _target_rank(key: SourceKey) -> tuple[int, str, str]:
    order = TARGET_NS_ORDER.index(key.ns) if key.ns in TARGET_NS_ORDER else len(TARGET_NS_ORDER)
    return (order, key.ns, key.key)


Hits = frozenset[tuple[str, SourceKey]]  # (family id, the family key that matched)


@dataclass(frozen=True, slots=True)
class FamilyIndex:
    """Families by name, Nerd folder and URL."""

    names: Mapping[str, Hits]
    nerd: Mapping[str, Hits]
    urls: Mapping[str, frozenset[str]]
    targets: Mapping[str, SourceKey]  # family id -> its best key, for URL-only candidates
    sorted_names: tuple[str, ...] = ()  # the keys of ``names``, sorted, for ``group``

    @classmethod
    def build(cls, families: Iterable[FamilyInfo]) -> FamilyIndex:
        names: dict[str, set[tuple[str, SourceKey]]] = defaultdict(set)
        nerd: dict[str, set[tuple[str, SourceKey]]] = defaultdict(set)
        urls: dict[str, set[str]] = defaultdict(set)
        targets: dict[str, SourceKey] = {}
        for fam in families:
            ranked = sorted(fam.keys, key=_target_rank)
            targets[fam.id] = ranked[0] if ranked else SourceKey(NAME_TARGET_NS, fam.name)
            names[match_key(fam.name)].add((fam.id, targets[fam.id]))
            for key in fam.keys:
                if key.ns == "nerd-folder":
                    nerd[match_key(key.key)].add((fam.id, key))
                elif (name := _key_name(key)) is not None:
                    names[match_key(name)].add((fam.id, key))
            for role, url in fam.urls:
                if role in URL_ROLES and (u := url_key(url)) is not None:
                    urls[u].add(fam.id)
        names.pop("", None)
        return cls(
            {k: frozenset(v) for k, v in names.items()},
            {k: frozenset(v) for k, v in nerd.items()},
            {k: frozenset(v) for k, v in urls.items()},
            targets,
            tuple(sorted(names)),
        )

    def by_stem(self, s: Stem) -> Hits:
        """Families named ``s``; a Nerd build tries the Nerd folders first."""
        if s.detail in NERD_DETAILS and (hit := self.nerd.get(s.key)):
            return hit
        return self.names.get(s.key, frozenset())

    def by_name(self, text: str) -> Hits:
        """Families named ``text`` (an Upstream-Name)."""
        return self.names.get(match_key(text), frozenset())

    def group(self, prefix: str) -> set[str]:
        """Families with a name or key that starts with ``prefix`` (a match_key).

        Only ever a reason to refuse: a package named ``fonts-jsmath`` whose URL
        one jsMath family lists holds the whole jsMath group, not that family.
        """
        out: set[str] = set()
        for name in self.sorted_names[bisect_left(self.sorted_names, prefix) :]:
            if not name.startswith(prefix):
                break
            out |= {fid for fid, _ in self.names[name]}
        return out


# --- the packages -------------------------------------------------------------------------


@dataclass(slots=True)
class Package:
    """Everything the snapshots say about one package key."""

    key: SourceKey
    font: bool = False  # a collector flagged it as a font package (else: a counted name)
    urls: set[str] = field(default_factory=set)  # package homepages, as given
    dep5_urls: set[str] = field(default_factory=set)  # DEP-5 ``Source`` URLs (sources)
    upstream: set[str] = field(default_factory=set)  # DEP-5 Upstream-Name (sources)
    replaces: set[str] = field(default_factory=set)  # Arch: the names it replaces
    provides: set[str] = field(default_factory=set)  # the names it provides
    successor: str | None = None  # a Debian transitional package: the one it pulls in
    source: str | None = None  # a Debian binary: its source package
    binaries: set[str] = field(default_factory=set)  # a Debian source: its font binaries


@dataclass(slots=True)
class Packages:
    """Package keys, filled by the ``add_*`` readers."""

    by_key: dict[SourceKey, Package] = field(default_factory=dict)

    def get(self, ns: str, name: str) -> Package:
        key = SourceKey(ns, name)
        return self.by_key.setdefault(key, Package(key))


def _dep_name(entry: str) -> str | None:
    m = _PKG_NAME.match(entry.strip().lower())
    return m.group(0) if m else None


def add_arch(pkgs: Packages, rows: Iterable[Mapping[str, Any]]) -> None:
    """Font-like rows (``font: true``) of an ``arch_repos`` extract."""
    for row in rows:
        if not row.get("font"):
            continue
        pkg = pkgs.get(ARCH_NS, row["name"])
        pkg.font = True
        if row.get("url"):
            pkg.urls.add(row["url"])
        for name, into in (("replaces", pkg.replaces), ("provides", pkg.provides)):
            into |= {d for e in row.get(name) or () if (d := _dep_name(e)) and d != pkg.key.key}


def add_names(pkgs: Packages, ns: str, names: Iterable[str]) -> None:
    """Names a ranking source counts (pkgstats, popcon); only those with a font affix."""
    for name in names:
        if stem(name, vcs=ns == ARCH_NS).affixed:
            pkgs.get(ns, name)


def _only(groups: Iterable[Iterable[str]]) -> str | None:
    """The package of a dependency list that is one group of one alternative."""
    groups = [list(g) for g in groups]
    return _dep_name(groups[0][0]) if len(groups) == 1 and len(groups[0]) == 1 else None


def add_debian(pkgs: Packages, rows: Iterable[Mapping[str, Any]]) -> None:
    """Font-role rows of the ``debian`` Packages extract, and their source packages."""
    for row in rows:
        if row.get("role") != "font":
            continue
        pkg = pkgs.get(DEB_NS, row["package"])
        pkg.font = True
        pkg.provides |= {d for e in row.get("provides") or () if (d := _dep_name(e))}
        pkg.provides.discard(pkg.key.key)
        if _TRANSITIONAL.search(row.get("description") or ""):
            pkg.successor = _only([*row.get("pre_depends", ()), *row.get("depends", ())])
        pkg.source = row.get("source") or row["package"]
        src = pkgs.get(SRC_NS, pkg.source)
        src.font = True
        src.binaries.add(row["package"])
        for p in (pkg, src):
            if row.get("homepage"):
                p.urls.add(row["homepage"])


def add_copyright(pkgs: Packages, rows: Iterable[Mapping[str, Any]]) -> None:
    """Rows of the ``debian_copyright`` extract (one per font source package)."""
    for row in rows:
        if row.get("status") != 200:
            continue
        src = pkgs.get(SRC_NS, row["package"])
        src.font = True
        if row.get("homepage"):
            src.urls.add(row["homepage"])
        src.dep5_urls |= {u for u in row.get("source_urls") or () if u}
        if (name := (row.get("upstream_name") or "").strip()) != "":
            src.upstream.add(name)


# --- evidence and decisions -----------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Vote:
    family: str
    target: SourceKey
    evidence: str


@dataclass(frozen=True, slots=True, order=True)
class Decision:
    """The row proposed for one package key."""

    family: str
    target: SourceKey
    relation: str
    detail: str
    evidence: tuple[str, ...]


def _votes(hits: Hits, evidence: str) -> list[Vote]:
    """One vote per family in ``hits``, targeting its best matched key."""
    best: dict[str, SourceKey] = {}
    for fid, key in sorted(hits):
        if fid not in best or _target_rank(key) < _target_rank(best[fid]):
            best[fid] = key
    return [Vote(fid, key, evidence) for fid, key in sorted(best.items())]


def _families(votes: Iterable[Vote]) -> set[str]:
    return {v.family for v in votes}


def _decide(strong: list[Vote], weak: list[Vote], detail: str) -> Decision | None:
    """One family from name evidence, else from URL evidence; None when it is not one."""
    for votes in (strong, weak):
        fams = _families(votes)
        if len(fams) > 1:
            return None
        if fams:
            fam = fams.pop()
            target = min((v.target for v in votes), key=_target_rank)
            agree = sorted({v.evidence for v in (*strong, *weak) if v.family == fam})
            return Decision(fam, target, "build" if detail else "package", detail, tuple(agree))
    return None


@dataclass(slots=True)
class Resolver:
    """The four rules over one set of packages and families (``propose`` runs it)."""

    pkgs: Packages
    index: FamilyIndex
    decisions: dict[SourceKey, Decision] = field(default_factory=dict)
    conflicts: dict[SourceKey, str] = field(default_factory=dict)
    _own: dict[SourceKey, list[Vote]] = field(default_factory=dict)
    _upstreams: dict[str, str] = field(default_factory=dict)  # match_key -> family
    _urls: dict[str, str] = field(default_factory=dict)  # url_key -> family

    def own_votes(self, pkg: Package) -> list[Vote]:
        """Rule 1: the package's name and the names it replaces."""
        if pkg.key not in self._own:
            s = key_stem(pkg.key)
            votes = _votes(self.index.by_stem(s), f"name {s.text}") if s.affixed or pkg.font else []
            for old in sorted(pkg.replaces):
                o = stem(old, vcs=pkg.key.ns == ARCH_NS)
                if o.affixed and o.detail == s.detail:
                    votes += _votes(self.index.by_stem(o), f"replaces {old}")
            self._own[pkg.key] = votes
        return self._own[pkg.key]

    def upstream_names(self, pkg: Package) -> set[str]:
        """Its DEP-5 Upstream-Names: a source's own, or its only font binary's source's."""
        if pkg.key.ns == DEB_NS and pkg.source is not None:
            src = self.pkgs.by_key.get(SourceKey(SRC_NS, pkg.source))
            only = src is not None and _font_binaries(src) == [pkg.key]
            return set(src.upstream) if src is not None and only else set()
        return set(pkg.upstream)

    def name_votes(self, pkg: Package) -> list[Vote]:
        """Rule 1 and the Upstream-Names of rule 2 that may pair."""
        votes = list(self.own_votes(pkg))
        for name in sorted(self.upstream_names(pkg)):
            if match_key(name) in self._upstreams:
                votes += _votes(self.index.by_name(name), f"upstream-name {name}")
        return votes

    def _set_stem(self, pkg: Package) -> str:
        """The stem key of ``pkg``, or of its source when it is the source's only font
        binary: then the two are one package set. Binaries of a bundle keep their own."""
        if pkg.key.ns == DEB_NS and pkg.source is not None:
            src = SourceKey(SRC_NS, pkg.source)
            if _font_binaries(self.pkgs.by_key.get(src)) == [pkg.key]:
                return key_stem(src).key
        return key_stem(pkg.key).key

    def _pairing(
        self,
        carried: Mapping[str, list[Package]],
        families: Callable[[str], set[str]],
        named: Callable[[Package], set[str]],
    ) -> dict[str, str]:
        """Rule 2's test: the items (URLs, Upstream-Names) that name one family and
        whose packages either match it by name or match nothing and share one stem.
        A source's only font binary counts with the source's stem (``_set_stem``)."""
        out = {}
        for item, pkgs in sorted(carried.items()):
            fams = families(item)
            if len(fams) != 1:
                continue
            fam = next(iter(fams))
            names = [(p, named(p)) for p in pkgs]
            loose = {self._set_stem(p) for p, n in names if not n}
            if all(n <= {fam} for _, n in names) and len(loose) <= 1:
                out[item] = fam
        return out

    def find_pairing(self) -> None:
        """Rule 2: which Upstream-Names, then which URLs, may pair a package."""
        fonts = [p for p in self.pkgs.by_key.values() if not is_non_font(p.key)]
        by_name: dict[str, list[Package]] = defaultdict(list)
        by_url: dict[str, list[Package]] = defaultdict(list)
        for pkg in fonts:
            for name in self.upstream_names(pkg):
                by_name[match_key(name)].append(pkg)
            for u in _url_keys(pkg):
                by_url[u].append(pkg)
        self._upstreams = self._pairing(
            by_name,
            lambda k: {fid for fid, _ in self.index.names.get(k, ())},
            lambda p: _families(self.own_votes(p)),
        )
        self._urls = self._pairing(
            by_url,
            lambda u: set(self.index.urls.get(u, ())),
            lambda p: _families(self.name_votes(p)),
        )

    def decide_direct(self) -> None:
        """Rules 1 and 2 for every key."""
        self.find_pairing()
        for key, pkg in sorted(self.pkgs.by_key.items()):
            font = not is_non_font(key)
            strong = self.name_votes(pkg) if font else self.own_votes(pkg)
            urls = sorted(_url_keys(pkg))
            weak = [
                Vote(self._urls[u], self.index.targets[self._urls[u]], f"url {u}")
                for u in urls
                if font and u in self._urls
            ]
            s = key_stem(key)
            found = _decide(strong, weak, s.detail)
            if found is None:
                if strong or weak:
                    named = ", ".join(sorted(_families((*strong, *weak))))
                    self.conflicts[key] = f"evidence names {named}"
                continue
            if not strong and len(group := self.index.group(s.key)) > 1:
                self.conflicts[key] = f"a URL only, and {len(group)} families start with {s.text}"
                continue
            # With no URL behind it, show the package's homepages: a same-named stranger stands
            # out. DEP-5 Source URLs may pair but are never shown: ruling T1 opens package
            # data for publishing, not the copyright files, and the seed file is public.
            agreed = any(v.family == found.family for v in weak)
            shown = sorted(_url_keys(pkg, dep5=False)) if LIST_HOMEPAGES and not agreed else []
            notes = [f"upstream {u}" for u in shown]
            self.decisions[key] = Decision(
                found.family,
                found.target,
                found.relation,
                found.detail,
                found.evidence + tuple(notes),
            )

    def _derive(self, key: SourceKey, via: list[tuple[SourceKey, str]]) -> None:
        """Give ``key`` the row that every package in ``via`` agrees on (rule 3)."""
        if any(k in self.conflicts or k not in self.decisions for k, _ in via):
            return
        found = [(self.decisions[k], why) for k, why in via]
        shapes = {(d.family, d.relation, d.detail) for d, _ in found}
        if len(shapes) > 1:
            self.conflicts[key] = "renamed into different families or builds"
            return
        self._settle(key, *shapes.pop(), [d.target for d, _ in found], [w for _, w in found])

    def _settle(
        self,
        key: SourceKey,
        family: str,
        relation: str,
        detail: str,
        targets: list[SourceKey],
        evidence: list[str],
    ) -> None:
        own = self.decisions.get(key)
        # A replaced or provided name that no snapshot lists has no decision of its own,
        # but its name still counts: ``ttf-beta`` replaced by an Alpha package is no Alpha row.
        named = _families(self.own_votes(self.pkgs.by_key.get(key) or Package(key)))
        if (own is not None and own.family != family) or named - {family}:
            other = own.family if own is not None else ", ".join(sorted(named - {family}))
            self.conflicts[key] = f"its own evidence names {other}, its packages {family}"
            return
        if own is not None:
            targets, evidence = [*targets, own.target], [*evidence, *own.evidence]
        target = min(targets, key=_target_rank)
        self.decisions[key] = Decision(
            family, target, relation, detail, tuple(sorted(set(evidence)))
        )

    def decide_derived(self) -> None:
        """Rule 3: replaced, transitional and provided names."""
        via: dict[SourceKey, list[tuple[SourceKey, str]]] = defaultdict(list)
        for key, pkg in sorted(self.pkgs.by_key.items()):
            own = key_stem(key)
            vcs = key.ns == ARCH_NS
            for old in sorted(pkg.replaces):
                if stem(old, vcs=vcs).affixed:
                    via[SourceKey(key.ns, old)].append((key, f"replaced by {key.key}"))
            for alias in sorted(pkg.provides):
                # Only the provider's own name: a variable build providing the plain
                # ``crimson-pro-font`` stands in for it, it is not that package.
                s = stem(alias, vcs=vcs)
                if s.affixed and (s.key, s.detail) == (own.key, own.detail):
                    via[SourceKey(key.ns, alias)].append((key, f"provided by {key.key}"))
            if pkg.successor is not None:
                successor = SourceKey(key.ns, pkg.successor)
                via[key].append((successor, f"transitional to {pkg.successor}"))
        for key, sources in sorted(via.items()):
            self._derive(key, sources)

    def decide_sources(self) -> None:
        """Rule 4: a Debian source package from the family its font binaries share
        (documentation and tool binaries, ``NON_FONT_SUFFIXES``, left out)."""
        for key, pkg in sorted(self.pkgs.by_key.items()):
            if key.ns != SRC_NS or key in self.conflicts:
                continue
            binaries = _font_binaries(pkg)
            found = [self.decisions.get(b) for b in binaries if b not in self.conflicts]
            if not binaries or len(found) != len(binaries) or None in found:
                continue
            decided = [d for d in found if d is not None]
            if len({d.family for d in decided}) != 1:
                continue
            shapes = {(d.relation, d.detail) for d in decided}
            relation, detail = shapes.pop() if len(shapes) == 1 else ("package", "")
            evidence = [f"binary {b.key}" for b in binaries]
            targets = [d.target for d in decided]
            self._settle(key, decided[0].family, relation, detail, targets, evidence)

    def candidates(self) -> list[AliasCandidate]:
        """Run the four rules; one candidate per decided, uncontested key."""
        self.decide_direct()
        self.decide_derived()
        self.decide_sources()
        return [
            AliasCandidate(
                alias=key,
                target=d.target,
                relation=d.relation,
                detail=d.detail,
                source=NAME,
                evidence="; ".join(d.evidence),
                auto=False,
            )
            for key, d in sorted(self.decisions.items())
            if key not in self.conflicts
        ]


def _font_binaries(src: Package | None) -> list[SourceKey]:
    """A Debian source's binaries, sorted, without ``NON_FONT_SUFFIXES`` ones."""
    binaries = (SourceKey(DEB_NS, b) for b in sorted(src.binaries if src else ()))
    return [b for b in binaries if not is_non_font(b)]


def _url_keys(pkg: Package, *, dep5: bool = True) -> set[str]:
    """The package's URLs as ``url_key``s; ``dep5`` includes the DEP-5 Source URLs."""
    urls = pkg.urls | pkg.dep5_urls if dep5 else pkg.urls
    return {u for u in map(url_key, urls) if u is not None}


def propose(
    families: Iterable[FamilyInfo], pkgs: Packages, log: logging.Logger | None = None
) -> list[AliasCandidate]:
    """The candidates for ``pkgs`` against ``families``; pure and deterministic."""
    resolver = Resolver(pkgs, FamilyIndex.build(families))
    found = resolver.candidates()
    log = log or logging.getLogger(__name__)
    for key, why in sorted(resolver.conflicts.items()):
        log.debug("distro: no candidate for %s:%s (%s)", key.ns, key.key, why)
    return found


# --- reading the store ----------------------------------------------------------------------


def _used_snapshots(ctx: MineContext) -> dict[str, date]:
    """The snapshot day of each source the run's parse stage used, if it wrote them."""
    from tff_catalog import parse  # parse imports every collector; only this path needs it

    if not (ctx.paths.stage / parse.SNAPSHOTS_FILE).is_file():
        return {}
    return {name: s.snapshot for name, s in parse.load_snapshots(ctx.paths).items()}


def snapshot(store: Store, source: str, day: date | None, run_date: date) -> Snapshot:
    """The snapshot of ``source`` for ``day``, else the newest on or before ``run_date``."""
    snap = store.snapshot(source, day) if day else store.latest(source, run_date)
    if snap is None:
        when = day.isoformat() if day else f"on or before {run_date.isoformat()}"
        raise DistroInputError(f"no complete {source} snapshot {when} in {store.root}")
    if snap.manifest.collector_version > FORMAT_VERSIONS[source]:
        raise DistroInputError(
            f"{source} {snap.date}: extract version {snap.manifest.collector_version} is newer "
            f"than the {FORMAT_VERSIONS[source]} this miner reads"
        )
    return snap


def _pkgstats_names(snap: Snapshot) -> Iterator[str]:
    for entry in snap.manifest.extracts:
        if not PKGSTATS_MONTH.match(entry.path):
            continue
        reader = csv.reader(io.StringIO(snap.read_bytes(entry.path).decode("utf-8")))
        if next(reader, [None])[0] != "name":
            raise DistroInputError(f"{snap.path / entry.path}: the first column is not name")
        yield from (row[0] for row in reader if row)


def read_packages(snaps: Mapping[str, Snapshot]) -> Packages:
    """Every package key the four snapshots list (module docstring, Keys)."""
    pkgs = Packages()
    arch = snaps[ARCH]
    for repo in arch.load_json(ARCH_REPOS):
        add_arch(pkgs, arch.iter_jsonl(repo["extract"]))
    add_names(pkgs, ARCH_NS, _pkgstats_names(snaps[PKGSTATS]))
    add_debian(pkgs, snaps[DEBIAN].iter_jsonl(DEB_PACKAGES))
    add_names(pkgs, DEB_NS, (row["name"] for row in snaps[DEBIAN].iter_jsonl(DEB_POPCON)))
    add_copyright(pkgs, snaps[COPYRIGHT].iter_jsonl(DEB_COPYRIGHT))
    return pkgs


class DistroMiner:
    """``MINER``: reads the universe and the store, then ``propose``."""

    name: ClassVar[str] = NAME

    def mine(self, ctx: MineContext) -> list[AliasCandidate]:
        if ctx.store is None:
            raise DistroInputError("the distro miner reads the snapshot store: set TFF_STORE")
        path = stageio.stage_path(ctx.paths, "universe")
        if not path.is_file():
            raise DistroInputError(f"{path} is missing: run `tff-catalog universe` first")
        used = _used_snapshots(ctx)
        snaps = {s: snapshot(ctx.store, s, used.get(s), ctx.run_date) for s in SOURCES}
        pkgs = read_packages(snaps)
        found = propose(families_of(stageio.load_stage(ctx.paths, "universe")), pkgs, ctx.log)
        ctx.log.info(
            "distro: %d candidates for %d package keys (%s)",
            len(found),
            len(pkgs.by_key),
            ", ".join(f"{s} {snaps[s].date}" for s in SOURCES),
        )
        return found


MINER = DistroMiner()
