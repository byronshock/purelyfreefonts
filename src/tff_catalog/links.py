"""Stage "links": official download links (milestone-1 step 14). Owner: agent P11.

- Google families (on Google's live list): primary is the specimen page;
  designer is ``minisite_url`` or ``repository_url``, never googlefontdirectory-hg.
- Others: the designer's homepage or the repository's releases page; never a
  release asset, ``/releases/latest`` or an aggregator; auto-accepted only
  when two sources agree, or when the owner-approved foundry list gives it.
- Owner-approved overrides come from gate K (``reviews.gate_dir(paths, "K")``).

A monthly check records every response in the ``link_checks`` pseudo-source,
so replay is offline. Writes ``build/stage/links.json``.

**Policy** (``policy_problems``). A link must be a well-formed https URL, and
must not be a release asset or other file download (``/releases/download/``,
``/archive/``, ``raw.githubusercontent.com``, SourceForge's
``/files/.../download``, a path ending in ``.zip``, ``.ttf`` ...), a
``/releases/latest`` link, the googlefontdirectory-hg mirror, or an aggregator
(``AGGREGATOR_HOSTS``, ``AGGREGATOR_OWNERS``, ``AGGREGATOR_REPOS``).

**Choosing** (``choose``), in this order:

1. An approved override (``config/link-overrides.toml``) wins, then a link the
   owner picked on the family's own gate K question (``owner_picks``).
2. A family with a live record from a Google source: primary is its specimen
   page, ``https://fonts.google.com/specimen/<name>``; designer is the first
   compliant ``minisite`` URL, else the first compliant ``repository`` URL
   (a forge repository as its root, ``https://github.com/<owner>/<repo>``).
3. Any other family: every ``homepage``, ``repository`` and ``minisite`` URL
   of its records is resolved (``resolve``) to a web page, or to its forge
   repository's releases page (GitHub, GitLab, Codeberg; derived even from a
   release-asset or ``/releases/latest`` URL, which is never the link itself).
   Two URLs agree when they resolve to the same key: the same repository, or
   the same page up to case of the host, ``www.``, ``http``/``https`` and a
   trailing slash. A key is accepted when records of at least
   ``MIN_AGREEING_SOURCES`` different sources (collectors) give it, or when a
   source in ``SINGLE_SOURCE_OK`` gives it: ``config/foundries.toml``, which the
   owner approved at gate C3, is enough on its own (owner ruling of 2026-09-26,
   link_rules (1)). The primary is the accepted key with the most sources (a
   homepage before a repository on a tie); the designer is the best accepted
   key of the other kind, if any. The basis is ``two_sources``, or
   ``foundry_list`` when the primary was accepted on the foundry list alone.
   Nothing accepted: ``NoAcceptedLink``, and the family goes to the gate K queue.

**Overrides and gate K.** ``config/link-overrides.toml`` proposes overrides.
Each names the gate K question that approves it and the answer that applies it
(``choice``, default "a"). It applies only when the owner's latest ruling on
that question (``data/reviews/links/<date>.toml``, read by
``reviews.latest_answers``) is that choice. ``questions`` gives gate K's
questions to ``reviews``: the override questions, then the catalog families
left undecided, with their candidates as options.

**Owner picks.** When the owner answers an undecided family's question
(``queue_question``) with one of its candidates, the family takes that link
(basis ``PICK_BASIS``) from the next run on, with no override entry: the
ruling itself names the link, because ``rulings apply`` records the picked
option's text, "<url> (from <sources>)", as ``ruling`` (``picked_url``; a
``url`` value, when given, wins). The URL the owner saw is kept even if a later
run lists the candidates in another order. An answer asking for research
(``RESEARCH_OPTION``) picks nothing: Claude researches the page and proposes
an override under a new question id, and the family's own question is then no
longer asked. An override entry on the family's own question decides instead
of the pick.

**The check** (``check``) HEADs every primary and designer link of the
catalog's families (all chosen links when there is no membership yet), one
worker per host at the fetcher's per-host pace, and falls back to one GET when
a HEAD is not answered 200. A link passes when it answers 200, follows the
policy, and does not redirect to a link the policy forbids. Every answer is
saved as ``link_checks/<run date>/checks.jsonl`` (rows ``{url, status,
final_url, error}``); a later run on the same day reuses the 200 answers and
re-checks the others (``--refetch``: all), and a replay reads them with the
network off.

A replay cannot check a link the owner accepted after the run it replays (an
owner pick, a newly approved override): the link is used, but it fails its
check as "not checked", and the stage logs a warning. Before replaying run
``D`` again, run the stage live for that date, ``tff-catalog links --date D``,
which checks just the links with no 200 answer recorded for ``D`` and adds
them to ``link_checks/D/`` (not recorded once a merged run has used ``D``).

Outputs: ``build/stage/links.json`` (accepted links only, by family id) and
``build/stage/queues/links.json`` (families without an accepted link, overrides
waiting for gate K, owner picks, failed checks).
"""

import dataclasses
import hashlib
import logging
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, quote_plus, urlsplit, urlunsplit

from tff_catalog import jsonio, stageio
from tff_catalog.config_model import ConfigError, from_mapping, load_toml
from tff_catalog.fetch import FetchError, HostNotAllowed
from tff_catalog.names import ID_PATTERN

if TYPE_CHECKING:
    from tff_catalog.fetch import Fetcher
    from tff_catalog.paths import Paths
    from tff_catalog.records import UniverseRecord
    from tff_catalog.reviews import Answer, Question
    from tff_catalog.stages import StageContext
    from tff_catalog.store import FetchRecord, Store
    from tff_catalog.universe import Family, Universe

OVERRIDES_FILE = "link-overrides.toml"  # under config/
OVERRIDES_SCHEMA = 1
GATE = "K"
QUEUE_FILE = "links.json"  # under build/stage/queues/
CHECKS_SOURCE = "link_checks"  # store.PSEUDO_SOURCES
CHECKS_VERSION = 1
CHECKS_EXTRACT = "checks.jsonl"
CHECK_WORKERS = 8  # hosts checked at once; each host keeps the fetcher's pace (1 a second)
MIN_AGREEING_SOURCES = 2  # milestone-1 step 14: "auto-accept only when two sources agree"
# Sources whose link is accepted with no second source agreeing. Owner ruling of 2026-09-26
# (links, link_rules (1)): the foundry list the owner approved at gate C3.
SINGLE_SOURCE_OK = frozenset({"foundries"})

GOOGLE_SOURCES = frozenset({"google_metadata", "google_repo"})
LIVE_LIST = "google_metadata"  # Google's live list: a family on it has a specimen page
SPECIMEN_HOST = "fonts.google.com"
SPECIMEN_BASE = "https://fonts.google.com/specimen/"
CANDIDATE_ROLES = frozenset({"homepage", "repository", "minisite"})
DESIGNER_ROLES = ("minisite", "repository")  # Google designer link, in order of preference
# Whose spelling of an agreed URL is kept; sources not listed come after, by name.
# debian_copyright emits license facts only (no UniverseRecords), so it gives no link.
SOURCE_ORDER = (
    "foundries",
    "google_repo",
    "fontsource",
    "homebrew_casks",
    "fontist",
    "nerdfonts",
    "google_metadata",
)

HG_MIRROR = "googlefontdirectory-hg"
# Aggregators, never linked: font sites, CDNs, package indexes. A subdomain counts too.
AGGREGATOR_HOSTS = frozenset(
    {
        "1001fonts.com",
        "1001freefonts.com",
        "abstractfonts.com",
        "archlinux.org",
        "befonts.com",
        "cdn.jsdelivr.net",
        "cdnjs.cloudflare.com",
        "dafont.com",
        "ffonts.net",
        "fontesk.com",
        "fontget.com",
        "fontist.org",
        "fontlibrary.org",
        "fontlot.com",
        "fontmeme.com",
        "fonts.adobe.com",
        "fonts.bunny.net",
        "fonts.googleapis.com",
        "fonts2u.com",
        "fontsgeek.com",
        "fontshop.com",
        "fontsource.org",
        "fontspace.com",
        "fontsquirrel.com",
        "fontzillion.com",
        "formulae.brew.sh",
        "jsdelivr.com",
        "myfonts.com",
        "nerdfonts.com",
        "npmjs.com",
        "open-foundry.com",  # a showcase of other designers' fonts (owner ruling 2026-09-26, link_rules (4))
        "openfontlibrary.org",
        "packages.debian.org",
        "packages.ubuntu.com",
        "tracker.debian.org",
        "unpkg.com",
        "urbanfonts.com",
        "web.archive.org",  # an archived copy is never the official page
    }
)
AGGREGATOR_OWNERS = frozenset({"fontist", "fontsource", "homebrew"})  # every GitHub repo of these
AGGREGATOR_REPOS = frozenset({"google/fonts", "ryanoasis/nerd-fonts"})  # owner/repo, lower case
# File hosts and file names: a link to one is a download, never a page.
ASSET_HOSTS = frozenset(
    {
        "codeload.github.com",
        "dl.dropboxusercontent.com",
        "downloads.sourceforge.net",
        "drive.google.com",  # a file share, never a page
        "fonts.gstatic.com",
        "github-releases.githubusercontent.com",
        "media.githubusercontent.com",
        "objects.githubusercontent.com",
        "raw.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
ASSET_EXTENSIONS = (
    ".7z",
    ".apk",
    ".bz2",
    ".deb",
    ".dmg",
    ".eot",
    ".exe",
    ".gz",
    ".msi",
    ".otc",
    ".otf",
    ".pfa",
    ".pfb",
    ".pkg",
    ".rar",
    ".rpm",
    ".tar",
    ".tgz",
    ".ttc",
    ".ttf",
    ".txz",
    ".woff",
    ".woff2",
    ".xz",
    ".zip",
    ".zst",
)
GITHUB_ASSET_PATHS = frozenset({"archive", "raw", "files", "zipball", "tarball"})  # /<o>/<r>/<this>
GITLAB_ASSET_PATHS = frozenset({"archive", "raw", "jobs", "package_files"})  # /<project>/-/<this>
SOURCEFORGE = frozenset({"sourceforge.net"})  # ".../files/<path>/download" is a file download
# First path segments of github.com that are not an owner.
GITHUB_RESERVED = frozenset(
    {
        "about",
        "apps",
        "collections",
        "enterprise",
        "explore",
        "features",
        "login",
        "marketplace",
        "orgs",
        "pricing",
        "search",
        "settings",
        "sponsors",
        "topics",
        "users",
    }
)
FORGE_RELEASES = {
    "github.com": "https://github.com/{}/releases",
    "codeberg.org": "https://codeberg.org/{}/releases",
    "gitlab.com": "https://gitlab.com/{}/-/releases",
}
_QUESTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")  # schemas/review.schema.json
QUESTION_MAX = 64  # schemas/review.schema.json: question ids are at most 64 characters
_CHOICE_RE = re.compile(r"^[a-z]$")
_HOST_RE = re.compile(r"[^\s/\\?#@:<>\"'`{}|^%]+")  # no spaces or URL delimiters in a host name
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# The last option of a queued family's question. It starts with "Research", so an answer
# picking it keeps the question open (reviews.REOPEN) until Claude proposes an override.
RESEARCH_OPTION = "Research the official page: none of these is it"
NOT_CHECKED = "not checked: no answer recorded for this day"  # a replayed link without one
PICK_BASIS = "owner_pick"  # Links.basis of a candidate the owner picked on gate K
PICK_URL = "url"  # an answer's value naming the picked link; else its ruling text does
# A queued family's candidate option, as ``questions`` words it and ``rulings apply``
# records the picked one as the ruling.
_PICKED_OPTION_RE = re.compile(r"(?P<url>https://\S+) \(from [^()]*\)")

Kind = Literal["homepage", "repository"]


@dataclass(frozen=True, slots=True)
class Link:
    url: str  # https
    label: str | None = None


@dataclass(frozen=True, slots=True)
class Links:
    primary: Link
    designer: Link | None
    basis: str  # "google_specimen", "override", "two_sources", ...


@dataclass(frozen=True, slots=True)
class LinkCheck:
    url: str
    status: int
    final_url: str
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.problems


# --- the policy --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Url:
    scheme: str
    netloc: str  # lower case
    host: str  # lower case, no port or trailing dot
    path: str
    query: str

    @property
    def bare(self) -> str:
        """The host without a leading ``www.``."""
        return self.host.removeprefix("www.")

    @property
    def segments(self) -> list[str]:
        return [s for s in self.path.split("/") if s]


def _split(url: str) -> _Url | None:
    """``url`` in parts; None unless it has a well-formed host (and port) and no control characters."""
    url = url.strip()
    if _CONTROL_RE.search(url):
        return None
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".")
        parts.port  # noqa: B018 -- raises ValueError for a port out of range
    except ValueError:
        return None
    if not host or not _HOST_RE.fullmatch(host):
        return None
    netloc = parts.netloc.lower().rsplit("@", 1)[-1]
    return _Url(parts.scheme.lower(), netloc, host.lower(), parts.path, parts.query)


def _host_in(host: str, hosts: frozenset[str]) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def _follows(segs: list[str], *wanted: str) -> bool:
    """Whether ``wanted`` appears in ``segs`` as consecutive segments."""
    n = len(wanted)
    return any(tuple(segs[i : i + n]) == wanted for i in range(len(segs) - n + 1))


def _is_latest(segs: list[str]) -> bool:
    return _follows(segs, "releases", "latest") or _follows(segs, "releases", "permalink", "latest")


def _is_asset(u: _Url, segs: list[str]) -> bool:
    if _host_in(u.bare, ASSET_HOSTS):
        return True
    if u.bare.split(".", 1)[0] == "api":  # an API endpoint serves data or files, never a page
        return True
    if segs and segs[-1].endswith(ASSET_EXTENSIONS):
        return True
    if any(value.lower().endswith(ASSET_EXTENSIONS) for _, value in parse_qsl(u.query)):
        return True  # "download.php?name=Font-Regular.ttf"
    if (
        u.bare == "github.com"
        and len(segs) >= 3
        and (segs[2] in GITHUB_ASSET_PATHS or segs[2:4] == ["releases", "download"])
    ):
        return True
    if "-" in segs:
        after = segs[segs.index("-") + 1 :]
        if after and after[0] in GITLAB_ASSET_PATHS:
            return True
        if after[:1] == ["releases"] and "downloads" in after:
            return True
    return _host_in(u.bare, SOURCEFORGE) and "files" in segs and segs[-1] == "download"


def _aggregator_repo(forge: str, path: str) -> str | None:
    """The aggregator a forge repository path ("<owner>/<repo>") belongs to, if any."""
    if forge != "github.com":
        return None
    parts = path.lower().split("/")
    if parts[0] in AGGREGATOR_OWNERS:
        return f"github.com/{parts[0]}"
    if len(parts) >= 2 and f"{parts[0]}/{parts[1].removesuffix('.git')}" in AGGREGATOR_REPOS:
        return f"github.com/{parts[0]}/{parts[1].removesuffix('.git')}"
    return None


def _aggregator(u: _Url, segs: list[str]) -> str | None:
    """The aggregator ``u`` belongs to, if any."""
    if _host_in(u.bare, AGGREGATOR_HOSTS):
        return u.bare
    return _aggregator_repo(u.bare, "/".join(segs)) if segs else None


def policy_problems(url: str) -> list[str]:
    """Why ``url`` breaks the link policy (not https, release asset, /releases/latest, hg mirror, aggregator)."""
    u = _split(url)
    if u is None:
        return ["not a URL"]
    out = []
    if u.scheme != "https":
        out.append("not https")
    if HG_MIRROR in url.lower():
        out.append("the googlefontdirectory-hg mirror")
    segs = [s.lower() for s in u.segments]
    if _is_latest(segs):
        out.append("a /releases/latest link")
    if _is_asset(u, segs):
        out.append("a release asset or file download")
    aggregator = _aggregator(u, segs)
    if aggregator is not None:
        out.append(f"an aggregator ({aggregator})")
    return out


# --- resolving a source's URL ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Target:
    """What a source's URL can link to."""

    key: str  # URLs with the same key agree
    url: str  # the link: the page, or the repository's releases page
    kind: Kind
    root: str  # the page, or the repository's root


def _forge_repo(u: _Url) -> tuple[str, str] | None:
    """(forge host, "<owner>/<repo>") when ``u`` points into a forge repository."""
    segs = u.segments
    if u.bare in ("github.com", "codeberg.org"):
        if len(segs) >= 2 and segs[0].lower() not in GITHUB_RESERVED:
            repo = segs[1].removesuffix(".git")
            if repo:
                return u.bare, f"{segs[0]}/{repo}"
        return None
    if u.bare == "gitlab.com":
        project = segs[: segs.index("-")] if "-" in segs else segs
        if len(project) >= 2:
            return u.bare, "/".join([*project[:-1], project[-1].removesuffix(".git")])
    return None


def resolve(url: str) -> Target | str:
    """The link ``url`` gives (``Target``), or why it gives none.

    A URL inside a forge repository gives that repository, whatever the path
    (a release asset gives its repository's releases page). Any other URL gives
    itself as an https page, unless the policy forbids it or it is a Google
    Fonts page, which is linked only as a live Google family's primary.
    """
    u = _split(url)
    if u is None or u.scheme not in ("http", "https"):
        return "not a web URL"
    if HG_MIRROR in url.lower():
        return "the googlefontdirectory-hg mirror"
    repo = _forge_repo(u)
    if repo is not None:
        forge, path = repo
        aggregator = _aggregator_repo(forge, path)
        if aggregator is not None:
            return f"an aggregator ({aggregator})"
        return Target(
            key=f"{forge}/{path.lower()}",
            url=FORGE_RELEASES[forge].format(path),
            kind="repository",
            root=f"https://{forge}/{path}",
        )
    netloc = u.netloc.removesuffix(":80") if u.scheme == "http" else u.netloc
    page = urlunsplit(("https", netloc, u.path or "/", u.query, ""))
    problems = policy_problems(page)
    if problems:
        return problems[0]
    if u.bare == SPECIMEN_HOST:
        return "a Google Fonts page"
    key = u.bare + u.path.rstrip("/") + (f"?{u.query}" if u.query else "")
    return Target(key=key, url=page, kind="homepage", root=page)


# --- choosing ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Candidate:
    """A link some of a family's sources give (non-Google families)."""

    key: str
    url: str
    kind: Kind
    sources: tuple[str, ...]  # the collectors that give it, sorted


@dataclass(frozen=True, slots=True, order=True)
class Rejected:
    url: str
    source: str
    why: str


class NoAcceptedLink(LookupError):
    """No link for a family meets the policy's agreement rule; it goes to gate K."""

    def __init__(
        self, family_id: str, candidates: tuple[Candidate, ...], rejected: tuple[Rejected, ...]
    ) -> None:
        super().__init__(f"{family_id}: no link that {MIN_AGREEING_SOURCES} sources agree on")
        self.family_id = family_id
        self.candidates = candidates
        self.rejected = rejected


def _source_rank(source: str) -> tuple[int, str]:
    if source in SOURCE_ORDER:
        return (SOURCE_ORDER.index(source), source)
    return (len(SOURCE_ORDER), source)


def _record_order(r: UniverseRecord) -> tuple[tuple[int, str], str, str]:
    return (_source_rank(r.source), r.key.ns, r.key.key)


def candidates(
    recs: Iterable[UniverseRecord],
) -> tuple[tuple[Candidate, ...], tuple[Rejected, ...]]:
    """Every link the records give, best first, and the URLs that give none (with why).

    Best first: most sources, then homepages before repositories, then by key.
    """
    support: dict[str, set[str]] = defaultdict(set)
    spellings: dict[str, list[tuple[tuple[int, str], str, Kind]]] = defaultdict(list)
    rejected: set[Rejected] = set()
    for r in sorted(recs, key=_record_order):
        for role, url in r.urls:
            if role not in CANDIDATE_ROLES:
                continue
            target = resolve(url)
            if isinstance(target, str):
                rejected.add(Rejected(url, r.source, target))
                continue
            support[target.key].add(r.source)
            spellings[target.key].append((_source_rank(r.source), target.url, target.kind))
    found = []
    for key, sources in support.items():
        _, url, kind = min(spellings[key])
        found.append(Candidate(key=key, url=url, kind=kind, sources=tuple(sorted(sources))))
    found.sort(key=lambda c: (-len(c.sources), c.kind != "homepage", c.key))
    return tuple(found), tuple(sorted(rejected))


def specimen_url(name: str) -> str:
    """Google Fonts' specimen page of family ``name``."""
    return SPECIMEN_BASE + quote_plus(name)


def _live_google(recs: Iterable[UniverseRecord]) -> list[UniverseRecord]:
    """A family's live Google records, when Google's live list has it (design-m1 C12): a
    google/fonts folder the live list lacks has no specimen page to link to."""
    live = sorted(
        (r for r in recs if r.source in GOOGLE_SOURCES and r.status == "live"), key=_record_order
    )
    return live if any(r.source == LIVE_LIST for r in live) else []


def _specimen(live: list[UniverseRecord]) -> str:
    """The specimen page: one a record gives, else built from the Google family name."""
    given = sorted(
        url
        for r in live
        for role, url in r.urls
        if role == "specimen"
        and not policy_problems(url)
        and (u := _split(url)) is not None
        and u.host == SPECIMEN_HOST
        and u.path.startswith("/specimen/")
    )
    if given:
        return given[0]
    named = sorted(r.key.key for r in live if r.key.ns == "gf-family")
    return specimen_url(named[0] if named else live[0].family)


def _google_designer(live: list[UniverseRecord]) -> Link | None:
    for wanted in DESIGNER_ROLES:
        for url in sorted({url for r in live for role, url in r.urls if role == wanted}):
            target = resolve(url)
            if isinstance(target, Target):
                return Link(target.root)
    return None


def choose(fam: Family, recs: Iterable[UniverseRecord], overrides: Mapping[str, Links]) -> Links:
    """Pick one family's links.

    ``recs`` are the family's universe records; ``overrides`` the approved
    overrides and the owner's picks by family id (``approved_overrides``,
    ``owner_picks``). Pure and deterministic:
    record order does not matter. Raises ``NoAcceptedLink`` when no link is
    accepted (see the module docstring).
    """
    if fam.id in overrides:
        return overrides[fam.id]
    recs = list(recs)
    live = _live_google(recs)
    if live:
        return Links(Link(_specimen(live)), _google_designer(live), "google_specimen")
    found, rejected = candidates(recs)
    agreed = [c for c in found if _accepted(c)]
    if not agreed:
        raise NoAcceptedLink(fam.id, found, rejected)
    primary = agreed[0]
    other = next((c for c in agreed[1:] if c.kind != primary.kind), None)
    basis = "two_sources" if len(primary.sources) >= MIN_AGREEING_SOURCES else "foundry_list"
    return Links(Link(primary.url), None if other is None else Link(other.url), basis)


def _accepted(c: Candidate) -> bool:
    """Whether ``c`` is accepted without the owner: two sources agree, or a source in
    ``SINGLE_SOURCE_OK`` (the owner-approved foundry list) gives it."""
    return len(c.sources) >= MIN_AGREEING_SOURCES or not SINGLE_SOURCE_OK.isdisjoint(c.sources)


def group_records(u: Universe, recs: Iterable[UniverseRecord]) -> dict[str, list[UniverseRecord]]:
    """Records of every eligible family, by id: a record of a several-families key goes to
    the family it was placed in, and a key otherwise kept out of every family is left out."""
    owner = {key: fid for fid, fam in u.eligible().items() for key in fam.keys}
    grouped: dict[str, list[UniverseRecord]] = {fid: [] for fid in sorted(u.eligible())}
    for r in recs:
        fid = owner.get(r.key)
        if fid is None:
            fid = u.record_owner(r.key, r.family)
        if fid is not None and fid in grouped:
            grouped[fid].append(r)
    return grouped


def choose_all(
    u: Universe, grouped: Mapping[str, list[UniverseRecord]], overrides: Mapping[str, Links]
) -> tuple[dict[str, Links], dict[str, NoAcceptedLink]]:
    """Links of every eligible family, and the families left without one."""
    links: dict[str, Links] = {}
    undecided: dict[str, NoAcceptedLink] = {}
    for fid in sorted(u.eligible()):
        try:
            links[fid] = choose(u.families[fid], grouped.get(fid, ()), overrides)
        except NoAcceptedLink as exc:
            undecided[fid] = exc
    return links, undecided


# --- overrides and gate K -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinkOverride:
    """One ``[[override]]`` table of ``config/link-overrides.toml``."""

    family: str  # family id (state/ids.json)
    name: str  # display name, for the owner
    question: str  # the gate K question that approves it
    primary: str
    reason: str
    designer: str = ""
    primary_label: str = ""
    designer_label: str = ""
    choice: str = "a"  # the answer to ``question`` that applies this override

    def links(self) -> Links:
        designer = Link(self.designer, self.designer_label or None) if self.designer else None
        return Links(Link(self.primary, self.primary_label or None), designer, "override")


@dataclass(frozen=True, slots=True)
class OverridesFile:
    schema: int
    override: tuple[LinkOverride, ...] = ()


def _check_override(o: LinkOverride, where: str) -> None:
    if not ID_PATTERN.fullmatch(o.family):
        raise ConfigError(f"{where}.family: {o.family!r} is not a family id")
    if not _QUESTION_RE.fullmatch(o.question) or len(o.question) > 64:
        raise ConfigError(f"{where}.question: {o.question!r} is not a question id")
    if not _CHOICE_RE.fullmatch(o.choice):
        raise ConfigError(f"{where}.choice: {o.choice!r} is not one letter a-z")
    for field in ("primary", "designer"):
        url = getattr(o, field)
        if field == "designer" and not url:
            continue
        problems = policy_problems(url)
        if problems:
            raise ConfigError(f"{where}.{field}: {url} is {'; '.join(problems)}")


def load_overrides(paths: Paths) -> tuple[LinkOverride, ...]:
    """``config/link-overrides.toml``, checked (no file: no overrides).

    Raises ``ConfigError`` for unknown or missing keys, a bad family or question
    id, a family listed twice, or a URL the policy forbids.
    """
    path = paths.config / OVERRIDES_FILE
    if not path.is_file():
        return ()
    doc = from_mapping(OverridesFile, load_toml(path), where=OVERRIDES_FILE)
    if doc.schema != OVERRIDES_SCHEMA:
        raise ConfigError(f"{OVERRIDES_FILE}: schema {doc.schema}, expected {OVERRIDES_SCHEMA}")
    seen: set[str] = set()
    for i, o in enumerate(doc.override):
        where = f"{OVERRIDES_FILE}: override[{i}]"
        _check_override(o, where)
        if o.family in seen:
            raise ConfigError(f"{where}.family: {o.family!r} is listed twice")
        seen.add(o.family)
    return doc.override


def gate_answers(paths: Paths) -> dict[str, Answer]:
    """Question id -> the owner's latest answer in gate K's rulings.

    Read with ``reviews.latest_answers``: every file is schema-checked (a bad
    one raises ``reviews.RulingError``) and a later ruling overrides an earlier
    one.
    """
    from tff_catalog.reviews import latest_answers

    return {qid: answer for qid, (answer, _day) in latest_answers(paths, GATE).items()}


def choices_of(answers: Mapping[str, Answer]) -> dict[str, str]:
    """Question id -> ``choice``, leaving out answers without one."""
    return {qid: a.choice for qid, a in answers.items() if a.choice is not None}


def gate_choices(paths: Paths) -> dict[str, str]:
    """Question id -> the owner's latest ``choice`` in gate K's rulings (``gate_answers``)."""
    return choices_of(gate_answers(paths))


def queue_question(fid: str) -> str:
    """The gate K question id of an undecided family: ``K-<family id>``.

    An id too long for the rulings schema keeps its first characters and ends
    with a hash of the family id, so it stays unique and stable.
    """
    qid = f"{GATE}-{fid}"
    if len(qid) <= QUESTION_MAX:
        return qid
    digest = hashlib.sha256(fid.encode()).hexdigest()[:8]
    return f"{qid[: QUESTION_MAX - len(digest) - 1]}-{digest}"


def approved_overrides(
    overrides: Iterable[LinkOverride], choices: Mapping[str, str]
) -> dict[str, Links]:
    """The overrides whose question the owner answered with their ``choice``, by family id."""
    return {o.family: o.links() for o in overrides if choices.get(o.question) == o.choice}


def asks_research(answer: Answer) -> bool:
    """Whether ``answer`` picked the research option (``RESEARCH_OPTION``, ``reviews.REOPEN``)."""
    from tff_catalog.reviews import REOPEN

    return answer.ruling.strip().casefold().startswith(REOPEN)


def picked_url(answer: Answer) -> str | None:
    """The link an answer to a queued family's question picked, or None when it names none.

    A ``url`` value (``PICK_URL``) names it; otherwise the ruling does, when it is
    a candidate option as ``questions`` words it, "<url> (from <sources>)", which
    ``rulings apply`` records for the picked letter. The link is not checked here.
    """
    given = dict(answer.values).get(PICK_URL)
    if isinstance(given, str):
        return given.strip()
    m = _PICKED_OPTION_RE.fullmatch(answer.ruling.strip())
    return m["url"] if m else None


@dataclass(frozen=True, slots=True)
class OwnerPicks:
    """What the owner's answers to the queued families' questions decide (``owner_picks``)."""

    links: dict[str, Links]  # family id -> the picked link (basis PICK_BASIS)
    picked: dict[str, str]  # question id -> the picked URL
    research: tuple[str, ...]  # family ids whose question the owner sent to research, sorted
    unusable: dict[str, str]  # question id -> why its answer gives no link


def owner_picks(
    families: Iterable[str], answers: Mapping[str, Answer], overrides: Iterable[LinkOverride]
) -> OwnerPicks:
    """The links the owner picked on the families' own gate K questions (``queue_question``).

    ``answers`` are gate K's latest answers (``gate_answers``). A question an
    override entry uses is left to that entry. An answer asking for research
    picks nothing and is listed while no override entry names the family. An
    answer whose link is missing or breaks the policy is listed as unusable.
    """
    overrides = list(overrides)
    entries = {o.question for o in overrides}
    researched = {o.family for o in overrides}
    links: dict[str, Links] = {}
    picked: dict[str, str] = {}
    research: list[str] = []
    unusable: dict[str, str] = {}
    for fid in sorted(set(families)):
        qid = queue_question(fid)
        answer = answers.get(qid)
        if answer is None or qid in entries:
            continue
        if asks_research(answer):
            if fid not in researched:
                research.append(fid)
            continue
        url = picked_url(answer)
        if url is None:
            unusable[qid] = (
                f"the ruling names no link: expected a {PICK_URL} value or the picked option's "
                f"text, got {answer.ruling!r}"
            )
        elif problems := policy_problems(url):
            unusable[qid] = f"{url} is {'; '.join(problems)}"
        else:
            links[fid] = Links(Link(url), None, PICK_BASIS)
            picked[qid] = url
    return OwnerPicks(links, picked, tuple(research), unusable)


def _override_lines(items: Iterable[LinkOverride]) -> str:
    """ "A, B: primary X, designer Y; C: primary Z", families with the same links together."""
    names: dict[tuple[str, str], list[str]] = defaultdict(list)
    for o in items:
        names[(o.primary, o.designer)].append(o.name)
    parts = []
    for (primary, designer), group in names.items():
        tail = f", designer {designer}" if designer else ""
        parts.append(f"{', '.join(group)}: primary {primary}{tail}")
    return "; ".join(parts)


def questions(paths: Paths) -> list[Question]:
    """Every gate K question, answered or not (``reviews.QUEUE_SOURCES``).

    ``reviews.questions`` keeps the ones without a ruling. All are
    single-select with 2 to ``reviews.MAX_OPTIONS`` options:

    - one per override question of ``config/link-overrides.toml`` ("a"
      approves, "b" keeps the link the policy picks);
    - once stage "membership" has run, one per catalog family that the last
      links run left without an accepted link (``queue_question``): up to three
      of its candidates, the best recommended, and ``RESEARCH_OPTION``, which
      keeps the question open. A family with no candidate, or outside the
      catalog, is not asked about: Claude researches its page and proposes an
      override instead.

    Picking a candidate links the family from the next run on (``owner_picks``);
    the family then leaves the queue, and its question with it. An override that
    follows research gets a new question id, and once an override entry names a
    family, that entry's question is asked instead of the family's own.
    """
    from tff_catalog.reviews import MAX_OPTIONS, Question

    by_question: dict[str, list[LinkOverride]] = defaultdict(list)
    for o in load_overrides(paths):
        by_question[o.question].append(o)
    with_entry = {o.family for items in by_question.values() for o in items}
    out = []
    for qid, items in sorted(by_question.items()):
        text = f"Official link override. {_override_lines(items)}. Why: {items[0].reason}"
        options = ("Approve the override", "Reject it: keep the link the policy picks")
        out.append(Question(GATE, qid, text, options, recommended=0))
    queue_path = paths.queues / QUEUE_FILE
    members = catalog_members(paths)
    if members is None or not queue_path.is_file():
        return out
    undecided = jsonio.load(queue_path).get("undecided", {})
    for fid in members:
        entry, qid = undecided.get(fid), queue_question(fid)
        shown = [] if entry is None else entry.get("candidates", [])[: MAX_OPTIONS - 1]
        if not shown or qid in by_question or fid in with_entry:
            continue
        options = (
            *(f"{c['url']} (from {', '.join(c['sources'])})" for c in shown),
            RESEARCH_OPTION,
        )
        text = (
            f"No two sources agree on the official link for {entry.get('family', fid)}. "
            "Which link is official?"
        )
        out.append(Question(GATE, qid, text, options, recommended=0))
    return out


# --- the check ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckRow:
    """One recorded answer: what a link returned on the check day."""

    url: str
    status: int  # 0: no answer
    final_url: str  # after redirects
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "error": self.error,
            "final_url": self.final_url,
            "status": self.status,
            "url": self.url,
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> CheckRow:
        if set(d) != {"error", "final_url", "status", "url"}:
            raise ValueError(
                f"CheckRow: expected error, final_url, status and url, got {sorted(d)}"
            )
        return cls(
            url=str(d["url"]),
            status=int(d["status"]),
            final_url=str(d["final_url"]),
            error=d["error"],
        )


def to_check(row: CheckRow) -> LinkCheck:
    """The verdict on a recorded answer: policy problems of the link and of where it
    redirects, and the error when there was no answer."""
    problems = policy_problems(row.url)
    if row.error:
        problems.append(f"no answer: {row.error}")
    if row.final_url != row.url:
        problems += [f"redirects to {row.final_url}, {p}" for p in policy_problems(row.final_url)]
    return LinkCheck(row.url, row.status, row.final_url, tuple(problems))


def _check_one(url: str, fetcher: Fetcher) -> tuple[CheckRow, list[FetchRecord]]:
    """HEAD ``url``, then GET it once if the HEAD was not answered 200.

    Returns the answer and a manifest entry for every URL reached, redirects
    included (``FetchResult.to_records``).
    """
    from httpx import InvalidURL

    done: list[FetchRecord] = []
    try:
        result = fetcher.head(url)
        done += result.to_records()
        if result.status != 200:  # some servers refuse or mishandle HEAD
            result = fetcher.get(url, expect=range(100, 600))
            done += result.to_records()
    except (FetchError, HostNotAllowed, ValueError, InvalidURL) as exc:
        return CheckRow(url, 0, url, f"{type(exc).__name__}: {exc}"), done
    return CheckRow(url, result.status, result.final_url), done


def _host(url: str) -> str:
    u = _split(url)
    return u.host if u is not None else ""


def check_urls(
    urls: Iterable[str], fetcher: Fetcher
) -> tuple[dict[str, CheckRow], list[FetchRecord]]:
    """Check ``urls`` live: one worker per host (at most ``CHECK_WORKERS`` at once).

    Returns the answers by URL and every request made, sorted by URL.
    """
    by_host: dict[str, list[str]] = defaultdict(list)
    for url in sorted(set(urls)):
        by_host[_host(url)].append(url)
    if not by_host:
        return {}, []

    def run_host(host_urls: list[str]) -> list[tuple[CheckRow, list[FetchRecord]]]:
        return [_check_one(url, fetcher) for url in host_urls]

    workers = min(CHECK_WORKERS, len(by_host))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="linkcheck") as pool:
        batches = list(pool.map(run_host, [by_host[h] for h in sorted(by_host)]))
    rows: dict[str, CheckRow] = {}
    fetched: list[FetchRecord] = []
    for row, done in sorted((pair for batch in batches for pair in batch), key=lambda p: p[0].url):
        rows[row.url] = row
        fetched += done
    return rows, fetched


def recorded_checks(store: Store, day: date) -> dict[str, CheckRow]:
    """The answers recorded in ``link_checks/<day>/`` (none when there is no snapshot)."""
    snap = store.snapshot(CHECKS_SOURCE, day)
    if snap is None or not snap.has(CHECKS_EXTRACT):
        return {}
    rows = (CheckRow.from_json(d) for d in snap.iter_jsonl(CHECKS_EXTRACT))
    return {row.url: row for row in rows}


def record_checks(
    store: Store,
    day: date,
    rows: Mapping[str, CheckRow],
    fetched: Iterable[FetchRecord],
    *,
    frozen: frozenset[date] = frozenset(),
) -> None:
    """Save ``rows`` as ``link_checks/<day>/checks.jsonl``, keeping the day's earlier rows.

    The snapshot is rewritten with the union (a new row wins) and every request
    listed. Raises ``store.SnapshotFrozen`` for a day a merged run used.
    """
    existing = store.snapshot(CHECKS_SOURCE, day)
    merged = dict(recorded_checks(store, day)) if existing is not None else {}
    merged.update(rows)
    writer = store.writer(
        CHECKS_SOURCE, day, CHECKS_VERSION, refetch=existing is not None, frozen=frozen
    )
    with writer:
        if existing is not None:
            for record in existing.manifest.fetched:
                writer.record_fetch(record)
        for record in fetched:
            writer.record_fetch(record)
        writer.write_jsonl(CHECKS_EXTRACT, [merged[url].to_json() for url in sorted(merged)])
        writer.set_data_date(day)
        writer.note("HEAD checks of the catalog's official links (stage links)")


def _link_urls(links: Mapping[str, Links]) -> list[str]:
    urls = {
        link.url for ls in links.values() for link in (ls.primary, ls.designer) if link is not None
    }
    return sorted(urls)


def check(links: Mapping[str, Links], ctx: StageContext) -> dict[str, LinkCheck]:
    """HEAD-check every primary and designer link (through the store in replay).

    Returns a ``LinkCheck`` per URL. A live run reuses the 200 answers already
    recorded for the run date, re-checks the rest (everything with
    ``--refetch``), so a passing failure or a fixed link needs no full rerun,
    and saves the new answers in the store. A replay (no fetcher) reads the
    answers recorded for the replayed date, and a URL without one fails as
    "not checked".
    """
    urls = _link_urls(links)
    day = ctx.options.from_snapshots or ctx.run_date
    recorded = recorded_checks(ctx.store, day) if ctx.store is not None else {}
    if ctx.fetcher is None:
        out = {}
        for url in urls:
            row = recorded.get(url)
            if row is None:
                problems = (*policy_problems(url), NOT_CHECKED)
                out[url] = LinkCheck(url, 0, url, tuple(problems))
            else:
                out[url] = to_check(row)
        return out
    todo = [
        url
        for url in urls
        if ctx.options.refetch or url not in recorded or recorded[url].status != 200
    ]
    fresh, fetched = check_urls(todo, ctx.fetcher)
    if fresh and ctx.store is not None:
        try:
            record_checks(
                ctx.store,
                ctx.run_date,
                fresh,
                fetched,
                frozen=ctx.state.frozen_dates(CHECKS_SOURCE),
            )
        except Exception as exc:  # a lost record costs replay fidelity, not this run
            ctx.log.warning("could not record link checks in the store: %s", exc)
    elif fresh:
        ctx.log.warning(
            "TFF_STORE is not set: link checks are not recorded, so a replay cannot repeat them"
        )
    rows = recorded | fresh
    return {url: to_check(rows[url]) for url in urls}


# --- the stage ------------------------------------------------------------------------------------


def catalog_members(paths: Paths) -> list[str] | None:
    """The catalog's family ids from stage "membership", or None before it has run."""
    if not stageio.stage_path(paths, "membership").is_file():
        return None
    return list(stageio.load_stage(paths, "membership").members())


def _check_scope(paths: Paths, links: Mapping[str, Links], log: logging.Logger) -> dict[str, Links]:
    members = catalog_members(paths)
    if members is None:
        log.warning("no catalog membership yet: checking every chosen link")
        return dict(links)
    return {fid: links[fid] for fid in members if fid in links}


def _candidate_json(c: Candidate) -> dict[str, Any]:
    return {"kind": c.kind, "sources": list(c.sources), "url": c.url}


def build_queue(
    u: Universe,
    undecided: Mapping[str, NoAcceptedLink],
    overrides: Iterable[LinkOverride],
    choices: Mapping[str, str],
    links: Mapping[str, Links],
    checks: Mapping[str, LinkCheck],
    picks: OwnerPicks | None = None,
) -> dict[str, Any]:
    """``build/stage/queues/links.json``: what the owner or Claude must act on.

    - ``undecided``: families without an accepted link, their candidates and
      the URLs that gave none;
    - ``overrides``: override questions with no ruling (``pending``), ruled
      otherwise (``rejected``), overrides of an unknown family; and the owner's
      answers to the undecided families' own questions (``picks``):
      ``picked``, the links taken from them (question id -> URL), ``research``,
      the undecided families the owner sent to research that no override entry
      names yet (Claude proposes one), and ``unusable``, answers that give no
      link (question id -> why);
    - ``failed_checks``: links that failed the check, with the families using them.
    """
    picks = picks or OwnerPicks({}, {}, (), {})
    by_url: dict[str, list[str]] = defaultdict(list)
    for fid, ls in sorted(links.items()):
        for role, link in (("primary", ls.primary), ("designer", ls.designer)):
            if link is not None:
                by_url[link.url].append(f"{fid}:{role}")
    overrides = list(overrides)
    return {
        "undecided": {
            fid: {
                "family": u.families[fid].family,
                "candidates": [_candidate_json(c) for c in exc.candidates],
                "rejected": [
                    {"source": r.source, "url": r.url, "why": r.why} for r in exc.rejected
                ],
            }
            for fid, exc in sorted(undecided.items())
        },
        "overrides": {
            "pending": sorted({o.question for o in overrides if o.question not in choices}),
            "picked": dict(sorted(picks.picked.items())),
            "rejected": sorted(
                {
                    o.question
                    for o in overrides
                    if o.question in choices and choices[o.question] != o.choice
                }
            ),
            "research": [queue_question(fid) for fid in picks.research if fid in undecided],
            "unknown_family": sorted(o.family for o in overrides if o.family not in u.families),
            "unusable": dict(sorted(picks.unusable.items())),
        },
        "failed_checks": {
            url: {
                "final_url": c.final_url,
                "problems": list(c.problems),
                "status": c.status,
                "used_by": by_url.get(url, []),
            }
            for url, c in sorted(checks.items())
            if not c.ok
        },
    }


def _unchecked(checks: Mapping[str, LinkCheck]) -> list[str]:
    return sorted(
        url for url, c in checks.items() if c.status == 0 and c.problems[-1:] == (NOT_CHECKED,)
    )


def run(ctx: StageContext) -> None:
    """Stage "links"."""
    from tff_catalog.universe import universe_records

    paths = ctx.paths
    u: Universe = stageio.load_stage(paths, "universe")
    grouped = group_records(u, universe_records(paths.records))
    overrides = load_overrides(paths)
    answers = gate_answers(paths)
    choices = choices_of(answers)
    approved = approved_overrides(overrides, choices)
    picks = owner_picks(u.eligible(), answers, overrides)
    links, undecided = choose_all(u, grouped, {**picks.links, **approved})
    stageio.dump_stage(paths, "links", links)
    checks = check(_check_scope(paths, links, ctx.log), ctx)
    queue = build_queue(u, undecided, overrides, choices, links, checks, picks)
    jsonio.dump(queue, paths.queues / QUEUE_FILE)

    bases: dict[str, int] = defaultdict(int)
    for ls in links.values():
        bases[ls.basis] += 1
    ctx.log.info(
        "links: %d families linked %s, %d without an accepted link (gate K queue)",
        len(links),
        dict(sorted(bases.items())),
        len(undecided),
    )
    ctx.log.info(
        "links: %d overrides approved, %d questions pending, %d owner picks; "
        "%d of %d checked links failed",
        len(approved),
        len(queue["overrides"]["pending"]),
        len(picks.links),
        len(queue["failed_checks"]),
        len(checks),
    )
    for fid in queue["overrides"]["unknown_family"]:
        ctx.log.warning("links: override for unknown family id %r", fid)
    for qid, why in queue["overrides"]["unusable"].items():
        ctx.log.warning("links: gate K answer %s gives no link: %s", qid, why)
    unchecked = _unchecked(checks)
    if unchecked:
        day = ctx.options.from_snapshots or ctx.run_date
        ctx.log.warning(
            "links: %d links have no check recorded for %s (accepted after that run?); "
            "run `tff-catalog links --date %s` live to check and record them, then replay: %s",
            len(unchecked),
            day.isoformat(),
            day.isoformat(),
            ", ".join(unchecked),
        )


def cmd_check(ctx: StageContext) -> int:
    """``links --check``: non-zero unless every primary link is compliant and returns 200.

    Checks the catalog's families (every family in ``links.json`` before the
    first membership run). A catalog family without links fails; so does any
    link the policy forbids and any primary that does not answer 200. A designer
    link that does not answer 200 is only reported.

    It only reads: the answers recorded in the store for the run date (or the
    replayed one), never the network, and it writes nothing, so it cannot change
    the dated snapshot that ``_runs/`` and a replay depend on. A URL with no
    recorded answer fails as "not checked"; rerun the stage to check it again.
    """
    paths = ctx.paths
    path = stageio.stage_path(paths, "links")
    if not path.is_file():
        print(f"links --check: {path} is missing; run `tff-catalog links` first", file=sys.stderr)
        return 1
    links: dict[str, Links] = stageio.load_stage(paths, "links")
    members = catalog_members(paths)
    scope = sorted(links) if members is None else sorted(members)
    failures: list[str] = [f"{fid}: no accepted link (gate K)" for fid in scope if fid not in links]
    chosen = {fid: links[fid] for fid in scope if fid in links}
    results = check(chosen, dataclasses.replace(ctx, fetcher=None))
    warnings: list[str] = []
    for fid, ls in sorted(chosen.items()):
        primary = results[ls.primary.url]
        if not primary.ok:
            failures.append(f"{fid}: primary {_verdict(primary)}")
        if ls.designer is not None:
            designer = results[ls.designer.url]
            if policy_problems(ls.designer.url):
                failures.append(f"{fid}: designer {_verdict(designer)}")
            elif not designer.ok:
                warnings.append(f"{fid}: designer {_verdict(designer)}")
    for line in warnings:
        print(f"warning: {line}")
    for line in failures:
        print(f"FAIL {line}")
    print(
        f"links --check: {len(scope)} families, {len(failures)} failures, {len(warnings)} warnings"
        + ("" if members is not None else " (no catalog membership yet: every chosen link)")
    )
    return 1 if failures else 0


def _verdict(c: LinkCheck) -> str:
    said = f"HTTP {c.status}" if c.status else "no answer"
    return f"{c.url}: {'; '.join((said, *c.problems))}"
