"""Alias miner "gf_history": renames in the google/fonts history (milestone-1 step 7).

Design-m1 §2.3 (miners) and gap G5 (the alias relations).

**Source.** ``https://github.com/google/fonts``, cloned once per run with
``gitsrc.sparse_clone(..., depth=None)`` into the run's raw directory: the full
history without blobs (about 25 MB), with only the family ``METADATA.pb`` files
checked out at the tip. The older ``METADATA.pb`` and ``to_delist.txt`` versions
it reads (about 100) are fetched in three batched requests, not one request per
blob as git would on demand. Everything read is a public name fact. The
``googlefontdirectory-hg`` mirror is never read, and nothing is paired by a name
prefix or by similarity: every candidate comes from one of two explicit
statements in the repository.

**Evidence.**

1. *Replaced folders.* A change that deletes one family folder's ``METADATA.pb``
   and adds one other family folder's (``<license>/<folder>/METADATA.pb``)
   replaces the first by the second. Changes are read twice: each commit on its
   own, and each change on the main line with a merged pull request as one
   change (``--first-parent --diff-merges=first-parent``), so a pull request
   that deletes in one commit and adds in another still counts. A change with
   more than one deleted or added folder pairs nothing (never guessed). A
   license move (``apache/roboto`` to ``ofl/roboto``) is no rename, nor is a
   move into a ``*_todelist`` folder, which parks a family until it is deleted.
2. *Delist statements.* Every past version of ``to_delist.txt``. A run of comment
   lines saying ``replaced by X`` or ``published X instead`` (once) names the
   successor of the entries under it, up to the next comment or blank line:
   folders (``ofl/foo``) or specimen links (``fonts.google.com/specimen/Foo+Bar``).
   An entry's own trailing comment may say it instead. ``X`` is a folder, a
   specimen link, or family names joined by "and" or commas, with remarks in
   parentheses dropped; every name must be a family at the tip, or the
   statement is skipped (a pull-request link, say, names no family).

**Chains.** Renames chain (Alpha to Alpha Sans to Alpha Pro). An old folder or
name starts from its latest rename and follows each successor's next rename, in
time order, to its current successor, which must be a family folder or name at
the tip. Time only moves forward, so a rename back ends the chain where it
began, and the name gets no candidate. A rename seen again (the pull request's
merge after its commit, a statement kept in the list for months) counts once.
A chain through a change that renames one node two ways at once gives nothing.

**Candidates** (``source`` "gf_history"; ``evidence``: the GitHub URLs of the
chain's commits or ``to_delist.txt`` versions, space-separated):

- ``rename`` ``gf-family:<old name>`` -> ``gf-family:<current name>``, for each
  name that changed (by ``match_key``). Kept when the old name is still a
  folder at the tip: google/fonts keeps some delisted families (42dot Sans was
  restored two days after its rename to Asta Sans), and stage "aliases" asks
  the owner whenever the old name is still a family.
- ``rename`` ``gf-dir:<old folder>`` -> ``gf-dir:<current folder>``, for each old
  folder gone from the tip (a folder at the tip is its own key).
- A delist paragraph with several entries and one successor gives ``rename``
  rows with detail ``merged``; one entry with several successors (a split)
  gives ``related`` rows with detail ``split``, from the old name.

``auto`` (auto-accept under rule ``gf_history_rename``) is true only for a
rename every step of which is corroborated: the old and the new ``METADATA.pb``
share a designer (comma-separated names, case-folded; designers are compared,
never written). ``AUTO_NEEDS_SHARED_DESIGNER`` switches that check off. Merged
and split rows are never auto.

Not mined: a family renamed in place (its ``METADATA.pb`` name edited, the
folder kept). In the real history those edits are mostly fixes of a wrong name
at the family's addition (``Signika Negative`` to ``Signika Negative SC``), and
pairing them would propose false renames of live families.

The miner returns its candidates; stage "aliases" writes them to
``data/alias-seeds/gf_history.csv``. It never writes ``data/aliases.csv``.
"""

import logging
import re
import shutil
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import unquote_plus

from google.protobuf import text_encoding

from tff_catalog import gitsrc
from tff_catalog.aliases import AliasCandidate
from tff_catalog.aliases.miners import MineContext
from tff_catalog.keys import match_key
from tff_catalog.records import SourceKey

NAME = "gf_history"
REPOSITORY = "https://github.com/google/fonts"
CLONE_URL = f"{REPOSITORY}.git"
LICENSE_FOLDERS = ("ofl", "apache", "ufl")
METADATA = "METADATA.pb"
DELIST_FILE = "to_delist.txt"
TODELIST_SUFFIX = "_todelist"
CHECKOUT = tuple(f"/{d}/*/{METADATA}" for d in LICENSE_FOLDERS)
# Git pathspecs: "*" also matches "/", so nested ones (ofl/x/static/METADATA.pb) are dropped later.
FAMILY_PATHSPECS = tuple(f"{d}/*/{METADATA}" for d in LICENSE_FOLDERS)
MERGED_DETAIL = "merged"
SPLIT_DETAIL = "split"
PREFETCH_BATCH = 1000  # blob ids per fetch request (a run needs about 100)
# Owner decision (gate A, open; this miner's addition): auto-accept a rename only when every
# step's old and new METADATA.pb share a designer. False marks every stated rename auto, which
# milestone-1 step 7 allows. At google/fonts 23e54b51 every stated rename shares one.
AUTO_NEEDS_SHARED_DESIGNER = True

_RECORD = "\x1e"  # starts each commit in the log output
_FIELD = re.compile(
    r"""^(name|designer):[ \t]*(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)')""", re.MULTILINE
)
_DIR_REF = re.compile(r"^(?:ofl|apache|ufl)/[^/\s#]+")
_SPECIMEN = re.compile(r"https://fonts\.google\.com/(?:noto/)?specimen/([^\s/?#()]+)")
_URL = re.compile(r"https?://\S+")
_REPLACED = re.compile(r"\breplaced\s+by\s+(.+)$", re.IGNORECASE)
_INSTEAD = re.compile(r"\bpublished\s+(.+?)\s+instead\b", re.IGNORECASE)
_PARENS = re.compile(r"\([^()]*\)")
_LIST = re.compile(r"\s*(?:,|\band\b)\s*", re.IGNORECASE)


# --- METADATA.pb ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Meta:
    """The two ``METADATA.pb`` fields this miner reads."""

    name: str | None
    designers: frozenset[str] = frozenset()  # case-folded


NO_META = Meta(None)


def read_meta(data: bytes) -> Meta:
    """The top-level ``name`` and ``designer`` of a ``METADATA.pb``, read leniently.

    Only unindented fields count: nested ones, such as a font's ``name``, are
    indented. A pattern rather than the proto parser, because old files in the
    history do not always parse (one has an unterminated string) and the proto
    drops fields over time. Textproto escapes are decoded; the first occurrence
    of a field wins.
    """
    found: dict[str, str] = {}
    for m in _FIELD.finditer(data.decode("utf-8", "replace")):
        raw = m[2] if m[2] is not None else m[3]
        found.setdefault(m[1], text_encoding.CUnescape(raw).decode("utf-8", "replace"))
    name = found.get("name", "").strip() or None
    designers = frozenset(
        d.strip().casefold() for d in found.get("designer", "").split(",") if d.strip()
    )
    return Meta(name, designers)


def shares_designer(old: Meta, new: Meta) -> bool:
    """Whether two ``METADATA.pb`` name a designer in common (the auto-accept corroboration)."""
    return bool(old.designers & new.designers)


def folder_slug(path: str) -> str:
    """The family folder's name (the ``gf-dir`` key) in ``ofl/foo`` or ``ofl/foo/METADATA.pb``."""
    return path.split("/")[1]


def _is_family_metadata(path: str) -> bool:
    parts = path.split("/")
    return len(parts) == 3 and parts[0] in LICENSE_FOLDERS and parts[2] == METADATA


@dataclass(frozen=True, slots=True)
class Tip:
    """The family folders at the tip, ``*_todelist`` ones left out."""

    folders: Mapping[str, Meta]  # folder slug -> its METADATA.pb fields
    _names: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        names: dict[str, list[str]] = defaultdict(list)
        for slug, meta in sorted(self.folders.items()):
            if meta.name:
                names[match_key(meta.name)].append(slug)
        object.__setattr__(self, "_names", {k: tuple(v) for k, v in names.items()})

    @classmethod
    def read(cls, repo: Path) -> Tip:
        """The ``<license>/<folder>/METADATA.pb`` files checked out in ``repo``."""
        folders: dict[str, Meta] = {}
        for lic in LICENSE_FOLDERS:
            for path in sorted(Path(repo).glob(f"{lic}/*/{METADATA}")):
                slug = path.parent.name
                if not slug.endswith(TODELIST_SUFFIX):
                    folders.setdefault(slug, read_meta(path.read_bytes()))
        return cls(folders)

    def folder_named(self, name: str) -> str | None:
        """The one folder whose family has this name (by ``match_key``), else None."""
        found = self._names.get(match_key(name), ())
        return found[0] if len(found) == 1 else None


# --- the history ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileChange:
    status: str  # git's letter: A, D, M ...
    path: str
    old_blob: str
    new_blob: str


@dataclass(frozen=True, slots=True)
class Commit:
    """One commit of a ``git log --raw``, or a merged pull request seen as one change."""

    sha: str
    time: int  # committer time, epoch seconds
    files: tuple[FileChange, ...]

    @property
    def when(self) -> tuple[int, str]:
        return (self.time, self.sha)


def parse_log(out: str) -> list[Commit]:
    """Parse ``git log -z --raw --no-abbrev --format=<RS>%H %ct`` output."""
    commits = []
    for chunk in out.split(_RECORD)[1:]:
        header, _, rest = chunk.partition("\0")
        sha, _, stamp = header.strip().partition(" ")
        tokens = rest.split("\0")
        files = []
        i = 0
        while i < len(tokens):
            meta = tokens[i].lstrip("\n")
            if meta.startswith(":") and i + 1 < len(tokens):
                _, _, old, new, status = meta[1:].split()[:5]
                files.append(FileChange(status[0], tokens[i + 1], old, new))
                i += 2
            else:
                i += 1
        commits.append(Commit(sha, int(stamp), tuple(files)))
    return commits


# gitsrc has no public helper yet for a log with additions, a blob id at a commit, or a
# batched blob fetch; these two wrappers are the only calls to its private functions.
def _git(repo: Path, *args: str, check: bool = True) -> bytes:
    """``git args`` in ``repo`` through gitsrc's runner (prompts off, hook variables dropped)."""
    return gitsrc._git(*args, cwd=Path(repo), check=check).stdout


def _blob_id(repo: Path, sha: str, path: str) -> str | None:
    """The blob id of ``path`` at commit ``sha``, or None; reads trees only (no download)."""
    return gitsrc._blob_id(Path(repo), sha, path)


def _promisor_remote(repo: Path) -> str | None:
    """The remote a partial clone downloads missing blobs from; None for a full repository."""
    out = _git(repo, "config", "--get-regexp", r"^remote\..*\.promisor$", check=False)
    for line in sorted(out.decode("utf-8", "replace").splitlines()):
        key, _, value = line.partition(" ")
        if value.strip().lower() == "true":
            return key.removeprefix("remote.").removesuffix(".promisor")
    return None


def _is_null(blob: str) -> bool:
    return set(blob) == {"0"}


def history(repo: Path, paths: Sequence[str], *, first_parent: bool = False) -> list[Commit]:
    """Commits from HEAD touching ``paths``, oldest first, without rename detection.

    ``first_parent`` walks the main line only and shows each merge as its
    difference to the main line, i.e. a merged pull request as one change.
    """
    # Spelled out so a user's log.* settings (signatures, merge diffs) cannot change the output.
    args = ["log", "-z", "--reverse", "--no-renames", "--raw", "--no-abbrev", "--no-show-signature"]
    args.append(f"--format={_RECORD}%H %ct")
    if first_parent:
        args += ["--first-parent", "--diff-merges=first-parent"]
    else:
        args.append("--diff-merges=off")
    out = _git(repo, *args, "HEAD", "--", *paths)
    return parse_log(out.decode("utf-8", "surrogateescape"))


class Blobs:
    """Reads blobs of a (partial) clone, each parsed once.

    In a partial clone git downloads a missing blob on first read, one request
    per blob (about 60 a run, 3 a second). The miner calls ``prefetch`` with
    the blobs a step will read first, so a run costs the clone plus three small
    fetches. A blob not prefetched still arrives on demand.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)
        self.remote = _promisor_remote(self.repo)
        self.requests = 0  # prefetch requests sent
        self._asked: set[str] = set()
        self._meta: dict[str, Meta] = {}
        self._ids: dict[tuple[str, str], str | None] = {}

    def prefetch(self, blobs: Iterable[str]) -> None:
        """Download ``blobs`` in one request per ``PREFETCH_BATCH``; a no-op in a full clone."""
        todo = sorted({b for b in blobs if not _is_null(b)} - self._asked - self._meta.keys())
        if self.remote is None or not todo:
            return
        self._asked.update(todo)
        # The arguments git itself uses for an on-demand download, with many ids at once.
        fetch = ["-c", "fetch.negotiationAlgorithm=noop", "fetch", "-q", self.remote]
        fetch += ["--no-tags", "--no-write-fetch-head", "--recurse-submodules=no"]
        fetch.append("--filter=blob:none")
        for i in range(0, len(todo), PREFETCH_BATCH):
            _git(self.repo, *fetch, *todo[i : i + PREFETCH_BATCH])
            self.requests += 1

    def data(self, blob: str) -> bytes:
        return _git(self.repo, "cat-file", "blob", blob)

    def meta(self, blob: str) -> Meta:
        if blob not in self._meta:
            self._meta[blob] = read_meta(self.data(blob))
        return self._meta[blob]

    def blob_at(self, sha: str, path: str) -> str | None:
        """The blob id of ``path`` at commit ``sha``, or None when it is missing."""
        key = (sha, path)
        if key not in self._ids:
            self._ids[key] = _blob_id(self.repo, sha, path)
        return self._ids[key]


# --- renames ---------------------------------------------------------------------------------

Kind = Literal["dir", "name"]


@dataclass(frozen=True, slots=True, order=True)
class Step:
    """One rename: ``old`` became ``new`` (folder slugs, or family names as written)."""

    when: tuple[int, str]  # (committer time, commit sha): the order renames happened in
    kind: Kind
    old: str
    new: str
    evidence: str  # a GitHub URL
    corroborated: bool  # the two METADATA.pb share a designer
    merged: bool = False  # one of several entries a delist statement gives one successor


def node_key(kind: Kind) -> Callable[[str], str]:
    """Folders are compared as written, names by ``match_key``."""
    return match_key if kind == "name" else str


def replaced_folder(commit: Commit) -> tuple[FileChange, FileChange] | None:
    """The deleted and added ``METADATA.pb`` when ``commit`` replaced one folder by another."""
    deleted = {folder_slug(f.path): f for f in commit.files if f.status == "D"}
    added = {folder_slug(f.path): f for f in commit.files if f.status == "A"}
    moved = deleted.keys() & added.keys()  # license moves: apache/roboto -> ofl/roboto
    parked = {s for s in added if s.endswith(TODELIST_SUFFIX)}
    old = [
        f
        for s, f in deleted.items()
        if s not in moved and s + TODELIST_SUFFIX not in parked and not s.endswith(TODELIST_SUFFIX)
    ]
    new = [f for s, f in added.items() if s not in moved and s not in parked]
    return (old[0], new[0]) if len(old) == 1 and len(new) == 1 else None


def commit_url(sha: str) -> str:
    return f"{REPOSITORY}/commit/{sha}"


def delist_url(sha: str) -> str:
    return f"{REPOSITORY}/blob/{sha}/{DELIST_FILE}"


def folder_steps(commits: Iterable[Commit], blobs: Blobs) -> tuple[list[Step], int]:
    """Renames from replaced folders; also the number of changes that paired nothing.

    ``commits`` may hold both passes over the history (one change seen twice
    gives the same step twice). The paired ``METADATA.pb`` are fetched in one batch.
    """
    pairs: list[tuple[Commit, FileChange, FileChange]] = []
    unpaired = 0
    for commit in commits:
        family = replace(
            commit, files=tuple(f for f in commit.files if _is_family_metadata(f.path))
        )
        pair = replaced_folder(family)
        if pair is None:
            unpaired += any(f.status == "D" for f in family.files)
        else:
            pairs.append((commit, *pair))
    blobs.prefetch(b for _, gone, came in pairs for b in (gone.old_blob, came.new_blob))
    steps: list[Step] = []
    for commit, gone, came in pairs:
        old, new = blobs.meta(gone.old_blob), blobs.meta(came.new_blob)
        ok = shares_designer(old, new)
        url = commit_url(commit.sha)
        steps.append(
            Step(commit.when, "dir", folder_slug(gone.path), folder_slug(came.path), url, ok)
        )
        if old.name and new.name and match_key(old.name) != match_key(new.name):
            steps.append(Step(commit.when, "name", old.name, new.name, url, ok))
    return steps, unpaired


def collapse(steps: Iterable[Step]) -> list[Step]:
    """Count a rename seen again once, at its first sighting.

    A later sighting of the same old -> new is a repeat unless the old node was
    renamed to (came back) in between. A repeat corroborates the first sighting.
    """
    steps = sorted(set(steps))
    arrivals: dict[tuple[Kind, str], list[tuple[int, str]]] = defaultdict(list)
    for s in steps:
        arrivals[(s.kind, node_key(s.kind)(s.new))].append(s.when)
    kept: list[Step] = []
    last: dict[tuple[Kind, str], int] = {}  # node -> index in kept of its last rename
    for s in steps:
        key = node_key(s.kind)
        node = (s.kind, key(s.old))
        i = last.get(node)
        if i is not None:
            prev = kept[i]
            back = any(prev.when < t < s.when for t in arrivals[node])
            if key(prev.new) == key(s.new) and not back:
                kept[i] = replace(prev, corroborated=prev.corroborated or s.corroborated)
                continue
        last[node] = len(kept)
        kept.append(s)
    return kept


def resolve(steps: Iterable[Step]) -> dict[tuple[Kind, str], tuple[Step, ...]]:
    """Each renamed node's chain of renames to its current successor.

    A node starts from its latest rename; from each successor the chain takes
    that successor's first rename after the step that reached it. Keys are
    ``(kind, node key)``; a chain may end where it began (a rename back). A
    node whose chain passes a change that renames one node two ways at once
    (two statements in one list version, say) has no chain: never guessed.
    """
    outgoing: dict[tuple[Kind, str], list[Step]] = defaultdict(list)
    for s in sorted(set(steps)):
        outgoing[(s.kind, node_key(s.kind)(s.old))].append(s)

    def unclear(s: Step) -> bool:
        key = node_key(s.kind)
        return any(
            o.when == s.when and key(o.new) != key(s.new)
            for o in outgoing.get((s.kind, key(s.old)), ())
        )

    chains = {}
    for node, out in sorted(outgoing.items()):
        chain = [out[-1]]
        while True:
            here = chain[-1]
            later = outgoing.get((here.kind, node_key(here.kind)(here.new)), ())
            nxt = next((s for s in later if s.when > here.when), None)
            if nxt is None:
                break
            chain.append(nxt)
        if not any(unclear(s) for s in chain):
            chains[node] = tuple(chain)
    return chains


# --- to_delist.txt ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Ref:
    """A family as a delist statement names it."""

    kind: Literal["dir", "name", "text"]  # a folder path, a family name, or a phrase of names
    value: str


@dataclass(frozen=True, slots=True)
class Statement:
    """``old`` entries are replaced by ``new`` (empty when the statement names no family)."""

    old: tuple[Ref, ...]
    new: tuple[Ref, ...]


def _entry(text: str) -> Ref | None:
    """An entry line's family: a folder path or a specimen link."""
    if m := _SPECIMEN.match(text):
        return Ref("name", unquote_plus(m[1]).strip())
    if m := _DIR_REF.match(text):
        return Ref("dir", m[0])
    return None


def _successor_phrase(comment: str) -> str | None:
    """``X`` of "replaced by X" or "published X instead", if the comment says so."""
    for pattern in (_INSTEAD, _REPLACED):
        if m := pattern.search(comment):
            return m[1].strip()
    return None


def successor_refs(phrase: str) -> tuple[Ref, ...]:
    """The families a successor phrase names; empty when it names none (a pull-request link)."""
    text = _PARENS.sub(" ", phrase).strip().rstrip(".;:").strip()
    if specimens := _SPECIMEN.findall(text):
        return tuple(Ref("name", unquote_plus(s).strip()) for s in specimens)
    if _URL.search(text):
        return ()
    dirs = [w for w in text.split() if _DIR_REF.fullmatch(w)]
    if dirs:
        return tuple(Ref("dir", d) for d in dirs)
    return (Ref("text", " ".join(text.split())),) if text else ()


def _blocks(text: str) -> Iterator[tuple[list[str], list[str]]]:
    """``(comments, entries)``: a run of comment lines and the lines under it, up to the
    next comment or blank line. Entries before any comment come with no comments."""
    comments: list[str] = []
    entries: list[str] = []
    for raw in (*text.splitlines(), ""):
        line = raw.strip()
        if entries and (not line or line.startswith("#")):
            yield comments, entries
            comments, entries = [], []
        if not line:
            comments = []  # comments followed by a blank line head nothing
        elif line.startswith("#"):
            comments.append(line)
        else:
            entries.append(line)


def parse_delist(text: str) -> list[Statement]:
    """The replacement statements of one ``to_delist.txt`` version, in file order.

    A block (``_blocks``) whose comments name exactly one successor gives one
    statement for its entries; an entry whose own trailing comment names one
    gives its own. Blocks whose comments name none, or two, give nothing.
    """
    out = []
    for comments, lines in _blocks(text):
        phrases = [p for line in comments if (p := _successor_phrase(line))]
        entries = []
        for line in lines:
            body, _, note = line.partition("#")
            ref = _entry(body.strip())
            if ref is None:
                continue
            if (own := _successor_phrase(note)) is not None:
                out.append(Statement((ref,), successor_refs(own)))
            else:
                entries.append(ref)
        if len(phrases) == 1 and entries:
            out.append(Statement(tuple(entries), successor_refs(phrases[0])))
    return out


def resolve_successors(refs: Sequence[Ref], tip: Tip) -> tuple[str, ...] | None:
    """The tip folders a statement's successors name, or None when any is not at the tip."""
    out: list[str] = []
    for ref in refs:
        if ref.kind == "dir":
            slug = folder_slug(ref.value)
            found: list[str | None] = [slug if slug in tip.folders else None]
        elif ref.kind == "name" or tip.folder_named(ref.value) is not None:
            found = [tip.folder_named(ref.value)]
        else:  # a phrase of several names: "Foo Text and Foo Headline"
            found = [tip.folder_named(n) for n in _LIST.split(ref.value) if n]
        if not found or None in found:
            return None
        out += [s for s in found if s is not None]
    return tuple(dict.fromkeys(out)) or None


@dataclass(frozen=True, slots=True)
class OldFamily:
    folder: str | None  # the folder slug, when the entry names one
    meta: Meta


Deletions = Mapping[str, Sequence[tuple[tuple[int, str], str]]]


def deletions(commits: Iterable[Commit]) -> dict[str, list[tuple[tuple[int, str], str]]]:
    """Folder slug -> (when, blob) of each deleted family ``METADATA.pb``, oldest first."""
    out: dict[str, list[tuple[tuple[int, str], str]]] = defaultdict(list)
    for commit in commits:
        for f in commit.files:
            if f.status == "D" and _is_family_metadata(f.path):
                out[folder_slug(f.path)].append((commit.when, f.old_blob))
    return {k: sorted(v) for k, v in out.items()}


def _listed_blobs(ref: Ref, listed: Commit, tip: Tip, blobs: Blobs, gone: Deletions) -> list[str]:
    """Where a delisted folder gone from the tip can be read, best first: the version at
    the commit that lists it, then the last one deleted before (removed before listed)."""
    if ref.kind != "dir" or (slug := folder_slug(ref.value)) in tip.folders:
        return []
    at = blobs.blob_at(listed.sha, f"{ref.value}/{METADATA}")
    before = [blob for when, blob in gone.get(slug, ()) if when <= listed.when]
    return [b for b in (at, before[-1] if before else None) if b is not None]


def _old_family(ref: Ref, listed: Commit, tip: Tip, blobs: Blobs, gone: Deletions) -> OldFamily:
    """A delist entry's folder and fields: at the tip, else from ``_listed_blobs``."""
    if ref.kind == "dir":
        slug = folder_slug(ref.value)
        if slug in tip.folders:
            return OldFamily(slug, tip.folders[slug])
        found = (blobs.meta(b) for b in _listed_blobs(ref, listed, tip, blobs, gone))
        return OldFamily(slug, next((m for m in found if m.name), NO_META))
    slug = tip.folder_named(ref.value)
    designers = tip.folders[slug].designers if slug is not None else frozenset()
    return OldFamily(None, Meta(ref.value, designers))


@dataclass(frozen=True, slots=True)
class DelistFindings:
    steps: list[Step]
    splits: list[AliasCandidate]
    skipped: int  # statements naming no family at the tip, or several for several entries


def delist_findings(
    commits: Iterable[Commit], tip: Tip, blobs: Blobs, gone: Deletions
) -> DelistFindings:
    """Renames and splits from every ``to_delist.txt`` version (``commits``, oldest first).

    ``gone`` is ``deletions`` of the family folders' history. The list versions,
    then the old families' ``METADATA.pb``, are fetched in one batch each.
    """
    versions = [
        (c, f.new_blob)
        for c in commits
        for f in c.files
        if f.path == DELIST_FILE and not _is_null(f.new_blob)
    ]
    blobs.prefetch(blob for _, blob in versions)
    stated: list[tuple[Commit, Statement, tuple[str, ...]]] = []
    skipped = 0
    for commit, blob in versions:
        for st in parse_delist(blobs.data(blob).decode("utf-8", "replace")):
            succ = resolve_successors(st.new, tip)
            if succ is None or (len(succ) > 1 and len(st.old) > 1):
                skipped += 1
            else:
                stated.append((commit, st, succ))
    blobs.prefetch(
        b for c, st, _ in stated for ref in st.old for b in _listed_blobs(ref, c, tip, blobs, gone)
    )
    steps: list[Step] = []
    splits: dict[tuple[str, str], AliasCandidate] = {}
    for commit, st, succ in stated:
        url = delist_url(commit.sha)
        olds = [_old_family(ref, commit, tip, blobs, gone) for ref in st.old]
        if len(succ) == 1:
            steps += _delist_steps(olds, succ[0], tip, commit.when, url)
            continue
        for new in succ:
            cand = _split(olds[0], tip.folders[new], url)
            if cand is not None:
                splits.setdefault((match_key(cand.alias.key), cand.target.key), cand)
    return DelistFindings(steps, sorted(splits.values()), skipped)


def _delist_steps(
    olds: Sequence[OldFamily], succ: str, tip: Tip, when: tuple[int, str], url: str
) -> list[Step]:
    new = tip.folders[succ]
    merged = len(olds) > 1
    out = []
    for old in olds:
        ok = shares_designer(old.meta, new)
        if old.folder is not None and old.folder != succ:
            out.append(Step(when, "dir", old.folder, succ, url, ok, merged))
        name = old.meta.name
        if name and new.name and match_key(name) != match_key(new.name):
            out.append(Step(when, "name", name, new.name, url, ok, merged))
    return out


def _split(old: OldFamily, new: Meta, url: str) -> AliasCandidate | None:
    if not old.meta.name or not new.name or match_key(old.meta.name) == match_key(new.name):
        return None
    return AliasCandidate(
        alias=SourceKey("gf-family", old.meta.name),
        target=SourceKey("gf-family", new.name),
        relation="related",
        detail=SPLIT_DETAIL,
        source=NAME,
        evidence=url,
        auto=False,
    )


# --- candidates ------------------------------------------------------------------------------


def rename_candidates(
    chains: Mapping[tuple[Kind, str], Sequence[Step]], tip: Tip
) -> Iterator[AliasCandidate]:
    """One ``rename`` per chain that ends at a tip family other than where it began."""
    for (kind, node), chain in sorted(chains.items()):
        final = chain[-1].new
        if kind == "dir":
            if node in tip.folders or final == node or final not in tip.folders:
                continue
            alias, target = SourceKey("gf-dir", node), SourceKey("gf-dir", final)
        else:
            slug = tip.folder_named(final)
            current = tip.folders[slug].name if slug is not None else None
            if current is None or match_key(current) == node:
                continue
            alias, target = SourceKey("gf-family", chain[0].old), SourceKey("gf-family", current)
        merged = any(s.merged for s in chain)
        corroborated = all(s.corroborated for s in chain) or not AUTO_NEEDS_SHARED_DESIGNER
        yield AliasCandidate(
            alias=alias,
            target=target,
            relation="rename",
            detail=MERGED_DETAIL if merged else "",
            source=NAME,
            evidence=" ".join(dict.fromkeys(s.evidence for s in chain)),
            auto=corroborated and not merged,
        )


def mine_repo(repo: Path, log: logging.Logger | None = None) -> list[AliasCandidate]:
    """Every candidate from a clone of google/fonts.

    ``repo`` has the full history (blobs may be missing) and the tip's family
    ``METADATA.pb`` files checked out. Sorted; the same for the same history.
    """
    tip = Tip.read(repo)
    blobs = Blobs(repo)
    commits = history(repo, FAMILY_PATHSPECS)
    merged_prs = history(repo, FAMILY_PATHSPECS, first_parent=True)
    steps, unpaired = folder_steps([*commits, *merged_prs], blobs)
    delist = delist_findings(history(repo, (DELIST_FILE,)), tip, blobs, deletions(commits))
    chains = resolve(collapse(steps + delist.steps))
    out = sorted({*rename_candidates(chains, tip), *delist.splits})
    if log is not None:
        log.info(
            "gf_history: %d tip folders, %d rename steps, %d deleting changes paired nothing, "
            "%d delist statements skipped, %d blob fetches; %d candidates (%d auto)",
            len(tip.folders),
            len(set(steps + delist.steps)),
            unpaired,
            delist.skipped,
            blobs.requests,
            len(out),
            sum(c.auto for c in out),
        )
    return out


class GfHistory:
    """The miner: clone google/fonts (blobless, full history) and read its renames."""

    name: ClassVar[str] = NAME

    def mine(self, ctx: MineContext) -> list[AliasCandidate]:
        dest = ctx.raw.file(f"{NAME}/fonts")
        if dest.exists():
            shutil.rmtree(dest)  # an earlier attempt's clone, kept by --keep-raw
        sha = gitsrc.sparse_clone(CLONE_URL, dest, CHECKOUT, depth=None)
        ctx.log.info("gf_history: google/fonts at %s", sha)
        return mine_repo(dest, ctx.log)


MINER = GfHistory()
