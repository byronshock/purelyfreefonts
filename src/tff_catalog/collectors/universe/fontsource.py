"""Universe collector "fontsource": the Fontsource registry, plus API-only ids. Owner: u/fontsource.

Milestone-1 step 4 (candidate universe); its license facts feed step 6a (L2)
and its file facts steps 5 and 5b.

**Fetch.** A sparse, depth-1, blobless clone of ``fontsource/fontsource``
(``Settings.repository`` at ``Settings.ref``) checks out only
``registry/data/families/*/*/family.json`` and the small registry files in
``AUX_FILES`` (about 5 MB of git objects; the clone lives in ``ctx.raw``). git is
not HTTP, so the clone is recorded as a manifest note naming the commit, not as
a ``fetched`` entry. Then one GET of ``Settings.api_url`` (``/v1/fonts``, the
API's whole list), recorded with ``record_fetch``; only its rows whose id the
registry lacks are kept (design-m1 §2.4: registry first, API only for missing
ids, because the API is stale: DejaVu's license, Iosevka's version, subsets).

**Extracts** (format ``version`` 1):

- ``registry.json``: ``{"repository", "ref", "commit"}`` of the clone.
- ``families/<group>.jsonl.gz``, one per registry group (``fontsource``,
  ``google``, ``google-icons``): one row ``{"id", "family.json"}`` per family,
  sorted by id. Split by group so git stores an unchanged group once. The
  ``family.json`` is the registry's, trimmed (``trim_family``):
  ``TRIMMED_KEYS`` removed (designer names and site display text: not facts);
  ``languages`` replaced by ``languageCounts`` ({script: languages}, after the
  ``family-overrides.json`` languages); ``tags`` merged with
  ``family-tags.json``; each ``sources[].inspection`` without ``features``,
  and without ``unicodeRange`` outside ``Settings.full_ranges`` (the ranges of
  every Google file would add about 3 MB a month, and the Latin stage judges
  Google families on Google's metadata; ``codepoints`` is always kept).
- ``replacements.json``: the registry's legacy-id map ``{old id: new id}``, as
  is (the ``fontsource_legacy`` alias miner reads it too).
- ``api-missing.json``: the ``/v1/fonts`` rows whose id is in no registry
  group, as the API gives them, sorted by id.

**Parse** (offline, pure). Per registry row, keyed ``fs-id``:

- a ``UniverseRecord``: family, ``displayName``, the classifications as
  given (no category: facts.py reads the first classification), primary
  script, ``latin_languages`` = ``languageCounts["Latn"]``, ``is_monospace`` =
  "monospace" is a classification (Fontsource's classifications are complete),
  ``variable`` = any source file has axes, ``added`` = ``dateAdded`` (Google
  families only), status ``active`` -> live, ``deprecated`` -> deprecated, the
  project repository and the upstream license file, when it is a raw file
  pinned to a commit on GitHub or another forge (``pinned_raw_file``), as URLs, a
  ``FontFileRef`` per source file (``file_ref``), a non-text drop code
  (``drop_code``: the ``google-icons`` group, the special-use tags in
  ``DROP_TAGS``, a ``Settings.symbol_classifications`` classification, then
  ``Settings.drop_names``; the "symbols" rule is the Google collectors' own,
  and on 2026-09-25 every registry family carrying "symbols" was a symbol,
  placeholder or punctuation-only font), and attrs ``origin="registry"``,
  ``fs_group`` and, for a legacy id,
  ``replaced_by``. Legacy ids are not asserted as ``names``: the owner reviews
  Fontsource renames through the alias miner (milestone-1 step 7);
- a ``LicenseFact``: ``raw`` and ``spdx`` = ``license.id`` (the registry
  speaks SPDX), ``text_url`` = the family's ``license.txt`` in the registry,
  pinned to the cloned commit.

Per ``api-missing.json`` row: a ``UniverseRecord`` (family, the API's single
category, ``variable``, a drop code from the "icons" category or
``drop_names``, attrs ``origin="api"`` and ``fs_type``, and for an id that
``replacements.json`` retires, status deprecated and ``replaced_by``; the
API's subsets are not trusted) and a ``LicenseFact`` with ``raw`` only (the
API's strings are not SPDX: it writes ``mit``). On 2026-09-25 these were
Google Sans, Google Sans Flex and Material Symbols.
"""

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, cast, get_args
from urllib.parse import quote, urlsplit

from tff_catalog import gitsrc
from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.records import (
    DropReason,
    FileRole,
    FontFileRef,
    LicenseFact,
    Record,
    SourceKey,
    Status,
    UniverseRecord,
    attrs,
)

NS = "fs-id"
REGISTRY_DIR = "registry/data"
FAMILIES_DIR = f"{REGISTRY_DIR}/families"
AUX_FILES = ("replacements.json", "family-tags.json", "family-overrides.json")
SPARSE_PATTERNS = (
    f"/{FAMILIES_DIR}/*/*/family.json",
    *(f"/{REGISTRY_DIR}/{name}" for name in AUX_FILES),
)

REGISTRY_EXTRACT = "registry.json"
FAMILIES_PREFIX = "families/"
FAMILIES_SUFFIX = ".jsonl.gz"
REPLACEMENTS_EXTRACT = "replacements.json"
API_EXTRACT = "api-missing.json"
FAMILY_JSON = "family.json"  # the row key holding the trimmed family.json

# family.json keys left out of the extract: people's names and site display text.
TRIMMED_KEYS = frozenset({"designer", "languages", "sampleText", "previewSubset", "previewContext"})
TRIMMED_INSPECTION = frozenset({"features"})  # OpenType feature tags: no stage reads them

# How the registry's own labels map to records.DROP_REASONS (non-text families).
DROP_GROUPS: dict[str, DropReason] = {"google-icons": "icon"}
DROP_TAGS: dict[str, DropReason] = {
    "special-use/emoji": "emoji",
    "special-use/icons": "icon",
    "special-use/math": "math",
    "special-use/music-symbols": "music",
    "special-use/barcode": "barcode",
    "special-use/punctuation": "symbol",  # YakuHan: a few dozen punctuation marks, no letters
}
SYMBOL: DropReason = "symbol"  # a Settings.symbol_classifications classification
SYMBOL_CLASSIFICATIONS: tuple[str, ...] = ("symbols",)  # google_metadata's "Symbols"
API_DROP_CATEGORIES: dict[str, DropReason] = {"icons": "icon"}
_DROP_ORDER: tuple[str, ...] = get_args(DropReason)

STATUSES: dict[str, Status] = {"active": "live", "deprecated": "deprecated"}
RAW_HOST = "raw.githubusercontent.com"
RAW_BASE = f"https://{RAW_HOST}"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_REPO_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GROUP = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SCRIPT = re.compile(r"^[A-Z][a-z]{3}$")
_URL = re.compile(r"^https?://\S+$")  # schemas/stage/records.schema.json $defs.url
_URL_MAX = 2048


class RegistryError(ValueError):
    """The registry clone or the API answer is not in the shape this collector reads."""


# --- fetch: the registry clone -----------------------------------------------------------------


def count_scripts(languages: Iterable[str]) -> dict[str, int]:
    """Languages per ISO 15924 script, from tags like ``aa_Latn`` or ``zh_Hans_CN``."""
    counts: Counter[str] = Counter()
    for tag in languages:
        parts = str(tag).split("_")
        if len(parts) >= 2 and _SCRIPT.match(parts[1]):
            counts[parts[1]] += 1
    return dict(sorted(counts.items()))


def trim_source(src: Mapping[str, Any], *, keep_range: bool) -> dict[str, Any]:
    """One ``sources[]`` entry without the inspection fields the extract leaves out."""
    out = dict(src)
    inspection = src.get("inspection")
    if isinstance(inspection, Mapping):
        dropped = TRIMMED_INSPECTION if keep_range else TRIMMED_INSPECTION | {"unicodeRange"}
        out["inspection"] = {k: v for k, v in inspection.items() if k not in dropped}
    return out


def trim_family(
    data: Mapping[str, Any],
    *,
    keep_ranges: bool,
    extra_tags: Iterable[str] = (),
    override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A family.json as the extract keeps it (module docstring, **Extracts**).

    ``override`` is the family's entry in ``family-overrides.json``; only its
    ``languages`` matter here, since its other keys are display text.
    """
    out = {k: v for k, v in data.items() if k not in TRIMMED_KEYS}
    languages = (override or {}).get("languages", data.get("languages", []))
    out["languageCounts"] = count_scripts(languages if isinstance(languages, list) else [])
    tags = data.get("tags", [])
    out["tags"] = sorted({*(tags if isinstance(tags, list) else []), *extra_tags})
    sources = data.get("sources", [])
    out["sources"] = [
        trim_source(s, keep_range=keep_ranges) for s in sources if isinstance(s, Mapping)
    ]
    return out


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RegistryError(f"{path}: {exc}") from exc


def _aux(root: Path, name: str) -> dict[str, Any]:
    path = root / REGISTRY_DIR / name
    if not path.is_file():
        return {}
    doc = _load_json(path)
    if not isinstance(doc, dict):
        raise RegistryError(f"{path}: expected an object")
    return doc


def tags_by_id(family_tags: Mapping[str, Any]) -> dict[str, set[str]]:
    """``family-tags.json`` ({tag: [ids]}) turned around: {id: {tags}}."""
    out: defaultdict[str, set[str]] = defaultdict(set)
    for tag, ids in family_tags.items():
        for fid in ids if isinstance(ids, list) else []:
            out[str(fid)].add(str(tag))
    return dict(out)


def read_registry(
    root: Path, full_ranges: Iterable[str]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Rows per registry group, and ``replacements.json``, from a checkout at ``root``."""
    base = root / FAMILIES_DIR
    if not base.is_dir():
        raise RegistryError(f"{base}: no registry families in the clone")
    keep = frozenset(full_ranges)
    extra = tags_by_id(_aux(root, "family-tags.json"))
    overrides = _aux(root, "family-overrides.json")
    groups: dict[str, list[dict[str, Any]]] = {}
    for group_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        group = group_dir.name
        if not _GROUP.match(group):
            raise RegistryError(f"{group_dir}: unexpected registry group name")
        rows = []
        for path in sorted(group_dir.glob(f"*/{FAMILY_JSON}")):
            fid = path.parent.name
            data = _load_json(path)
            if not isinstance(data, dict):
                raise RegistryError(f"{path}: expected an object")
            override = overrides.get(fid)
            family = trim_family(
                data,
                keep_ranges=group in keep,
                extra_tags=extra.get(fid, ()),
                override=override if isinstance(override, dict) else None,
            )
            rows.append({"id": fid, FAMILY_JSON: family})
        groups[group] = rows
    return groups, _aux(root, "replacements.json")


def api_missing(api: Any, registry_ids: set[str]) -> list[dict[str, Any]]:
    """The ``/v1/fonts`` rows whose id the registry lacks, sorted by id."""
    if not isinstance(api, list):
        raise RegistryError(f"/v1/fonts: expected a list, got {type(api).__name__}")
    rows = [r for r in api if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"]]
    return sorted((r for r in rows if r["id"] not in registry_ids), key=lambda r: r["id"])


# --- parse: records -----------------------------------------------------------------------------


def github_slug(url: str) -> str | None:
    """``owner/repo`` of a ``https://github.com/owner/repo[.git]`` URL, else None."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc.lower() != "github.com":
        return None
    slug = parts.path.strip("/").removesuffix(".git")
    return slug if _REPO_SLUG.match(slug) else None


def raw_url(slug: str, revision: str, path: str) -> str:
    """A raw.githubusercontent.com URL; brackets are escaped as google/fonts file names need."""
    return f"{RAW_BASE}/{slug}/{revision}/{quote(path.lstrip('/'), safe='/,')}"


def license_text_url(meta: Mapping[str, Any], group: str, fid: str) -> str | None:
    """The family's ``license.txt`` in the registry, pinned to the cloned commit."""
    slug = github_slug(str(meta.get("repository", "")))
    commit = str(meta.get("commit", ""))
    if slug is None or not _COMMIT.match(commit):
        return None
    return raw_url(slug, commit, f"{FAMILIES_DIR}/{group}/{fid}/license.txt")


def _int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def file_role(src: Mapping[str, Any]) -> FileRole:
    """Italic, else variable (it has axes), else regular (weight 400), else other."""
    inspection = src.get("inspection") or {}
    variant = src.get("variant") or {}
    if (variant.get("style") or inspection.get("style")) == "italic":
        return "italic"
    if inspection.get("axes"):
        return "variable"
    weight = variant.get("weight", inspection.get("weight"))
    return "regular" if weight == 400 else "other"


def file_ref(src: Mapping[str, Any], provenance: Mapping[str, Any]) -> FontFileRef | None:
    """A registry source file as a ``FontFileRef``, pinned to its provenance revision.

    None when the provenance is not a GitHub repository at a revision, or the
    entry has no path, or the URL would not be one the records schema accepts.
    """
    slug, revision, path = (
        provenance.get("repository"),
        provenance.get("revision"),
        src.get("path"),
    )
    if provenance.get("type", "github") != "github" or not all(
        isinstance(v, str) and v for v in (slug, revision, path)
    ):
        return None
    if not _REPO_SLUG.match(slug):
        return None
    url = raw_url(slug, revision, path)
    if not _is_url(url):
        return None
    inspection = src.get("inspection") or {}
    sha = src.get("sha256")
    rng = inspection.get("unicodeRange")
    return FontFileRef(
        url=url,
        sha256=sha if isinstance(sha, str) and _SHA256.match(sha) else None,
        size=_int(src.get("size")),
        role=file_role(src),
        codepoints=_int(inspection.get("codepoints")),
        unicode_range=rng if isinstance(rng, str) and rng else None,
    )


type DropNames = Sequence[tuple[DropReason, re.Pattern[str]]]


def compile_drop_names(pairs: Iterable[tuple[DropReason, str]]) -> DropNames:
    """``Settings.drop_names`` with the patterns compiled."""
    return tuple((code, re.compile(pattern)) for code, pattern in pairs)


def name_drop(family: str, names: DropNames) -> DropReason | None:
    """The code of the first ``drop_names`` pattern the family name matches, if any."""
    return next((code for code, pattern in names if pattern.search(family)), None)


def drop_code(
    group: str,
    fam: Mapping[str, Any],
    names: DropNames = (),
    symbols: Iterable[str] = SYMBOL_CLASSIFICATIONS,
) -> DropReason | None:
    """The non-text code the registry's group, tags, classifications or name give, if any.

    The group decides first, then the special-use tags (the first tag code in
    ``records.DropReason`` order), then any classification in ``symbols``
    (``Settings.symbol_classifications``, as the Google collectors rule),
    then ``Settings.drop_names``.
    """
    if group in DROP_GROUPS:
        return DROP_GROUPS[group]
    tagged = {DROP_TAGS[t] for t in _strings(fam.get("tags")) if t in DROP_TAGS}
    if tagged:
        return min(tagged, key=_DROP_ORDER.index)
    if set(_strings(fam.get("classifications"))) & set(symbols):
        return SYMBOL
    return name_drop(str(fam.get("family", "")), names)


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(v for v in value if isinstance(v, str) and v) if isinstance(value, list) else ()


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _is_url(value: str | None) -> bool:
    """A URL the records schema accepts (http or https, no spaces, at most 2,048 characters)."""
    return value is not None and len(value) <= _URL_MAX and _URL.match(value) is not None


def pinned_raw_file(url: str) -> bool:
    """Whether ``url`` is a raw file pinned to a commit, so its bytes cannot change.

    ``raw.githubusercontent.com/<owner>/<repo>/<sha>/<path>``, or a forge's raw
    path with the sha after a ``raw`` segment (GitLab ``/-/raw/<sha>/``, Gitea
    and Forgejo ``/raw/commit/<sha>/``). A web page (``/blob/<sha>/``), a
    branch or an archive link is not one.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not _is_url(url):
        return False
    segments = [s for s in parts.path.split("/") if s]
    if parts.netloc == RAW_HOST:
        return len(segments) > 3 and _COMMIT.match(segments[2]) is not None
    sha = next((i for i, s in enumerate(segments) if _COMMIT.match(s)), None)
    return sha is not None and "raw" in segments[:sha] and sha < len(segments) - 1


def _urls(fam: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """The project repository, and the upstream license file when it is pinned to a commit."""
    out = set()
    project = fam.get("project") or {}
    repo = _text(project.get("repository"))
    if _is_url(repo):
        out.add(("repository", repo))
    lic = _text((fam.get("license") or {}).get("url"))
    if lic and pinned_raw_file(lic):
        out.add(("license", lic))
    return tuple(sorted(out))


def registry_records(
    source: str,
    group: str,
    row: Mapping[str, Any],
    meta: Mapping[str, Any],
    replacements: Mapping[str, Any],
    names: DropNames = (),
    symbols: Iterable[str] = SYMBOL_CLASSIFICATIONS,
) -> Iterator[Record]:
    """The ``UniverseRecord`` and ``LicenseFact`` of one registry row (module docstring)."""
    fid, fam = row["id"], row[FAMILY_JSON]
    key = SourceKey(NS, fid)
    classes = _strings(fam.get("classifications"))
    sources = [s for s in fam.get("sources", []) if isinstance(s, Mapping)]
    provenance = fam.get("provenance") or {}
    files = {ref.url: ref for s in sources if (ref := file_ref(s, provenance)) is not None}
    extra: dict[str, str] = {"origin": "registry", "fs_group": group}
    if isinstance(replacements.get(fid), str):
        extra["replaced_by"] = replacements[fid]
    display = _text(fam.get("displayName"))
    yield UniverseRecord(
        source=source,
        key=key,
        family=fam["family"],
        display_name=display if display != fam["family"] else None,
        classifications=classes,
        primary_script=_text(fam.get("primaryScript")),
        latin_languages=_int((fam.get("languageCounts") or {}).get("Latn", 0)),
        is_monospace=("monospace" in classes) if classes else None,
        variable=any(bool((s.get("inspection") or {}).get("axes")) for s in sources)
        if sources
        else None,
        added=_date(fam.get("dateAdded")),
        status=STATUSES.get(str(fam.get("status")), "live"),
        urls=_urls(fam),
        files=tuple(files[u] for u in sorted(files)),
        drop=drop_code(group, fam, names, symbols),
        attrs=attrs(**extra),
    )
    lic = _text((fam.get("license") or {}).get("id"))
    if lic is not None:
        yield LicenseFact(
            source=source,
            key=key,
            raw=lic,
            spdx=lic,
            text_url=license_text_url(meta, group, fid),
            attrs=attrs(origin="registry"),
        )


def api_records(
    source: str,
    row: Mapping[str, Any],
    names: DropNames = (),
    replacements: Mapping[str, Any] | None = None,
) -> Iterator[Record]:
    """The ``UniverseRecord`` and ``LicenseFact`` of one API-only row.

    Its drop code comes from the API's "icons" category, else ``drop_names``.
    The API is stale, so it can still list an id the registry has retired: an
    id that ``replacements`` maps is deprecated, with ``replaced_by``, as it
    would be in the registry.
    """
    fid = row["id"]
    key = SourceKey(NS, fid)
    family = _text(row.get("family"))
    if family is None:
        return
    category = _text(row.get("category"))
    variable = row.get("variable")
    extra: dict[str, str] = {"origin": "api"}
    if (kind := _text(row.get("type"))) is not None:
        extra["fs_type"] = kind
    new = (replacements or {}).get(fid)
    if isinstance(new, str) and new:
        extra["replaced_by"] = new
    yield UniverseRecord(
        source=source,
        key=key,
        family=family,
        category=category,
        variable=variable if type(variable) is bool else None,
        status="deprecated" if "replaced_by" in extra else "live",
        drop=API_DROP_CATEGORIES.get(category or "") or name_drop(family, names),
        attrs=attrs(**extra),
    )
    lic = _text(row.get("license"))
    if lic is not None:
        yield LicenseFact(source=source, key=key, raw=lic, attrs=attrs(origin="api"))


# --- the collector ------------------------------------------------------------------------------


class Fontsource(CollectorBase):
    """The Fontsource registry (git) and the API's ids the registry lacks."""

    name: ClassVar[str] = "fontsource"
    kind: ClassVar[Kind] = "universe"
    version: ClassVar[int] = 1
    # github.com is the git remote, cloned by gitsrc, not through the fetcher.
    hosts: ClassVar[tuple[str, ...]] = ("api.fontsource.org", "github.com")
    emits: ClassVar[tuple[type, ...]] = (UniverseRecord, LicenseFact)

    @dataclass(frozen=True, slots=True)
    class Settings:
        """``config/sources/fontsource.toml``; checked when loaded (``ConfigError``)."""

        enabled: bool = True
        repository: str = "https://github.com/fontsource/fontsource"  # a GitHub repository
        ref: str = "main"  # branch, tag or commit sha of the registry
        api_url: str = "https://api.fontsource.org/v1/fonts"
        full_ranges: tuple[str, ...] = ("fontsource",)  # groups keeping unicodeRange
        # (code, regex on the family name): drop codes the registry's labels miss.
        drop_names: tuple[tuple[DropReason, str], ...] = (("icon", r"^Material (Icons|Symbols)\b"),)
        symbol_classifications: tuple[str, ...] = SYMBOL_CLASSIFICATIONS  # -> drop "symbol"

        def __post_init__(self) -> None:
            where = "sources/fontsource.toml"
            if github_slug(self.repository) is None:
                raise ConfigError(
                    f"{where}.repository: must be https://github.com/<owner>/<repo>, "
                    f"got {self.repository!r}"
                )
            if self.ref.startswith("-") or any(c.isspace() for c in self.ref):
                raise ConfigError(f"{where}.ref: not a branch, tag or commit: {self.ref!r}")
            if not self.api_url.startswith("https://"):
                raise ConfigError(f"{where}.api_url: must be an https URL, got {self.api_url!r}")
            bad = [g for g in self.full_ranges if not _GROUP.match(g)]
            if bad:
                raise ConfigError(f"{where}.full_ranges: not registry group names: {bad}")
            for _, pattern in self.drop_names:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ConfigError(
                        f"{where}.drop_names: bad pattern {pattern!r}: {exc}"
                    ) from exc

    def fetch(self, ctx: FetchContext) -> None:
        """Clone the registry, write the extracts, then fetch the API list for missing ids."""
        s = cast(Fontsource.Settings, ctx.settings)
        checkout = ctx.raw.path / "fontsource-registry"
        commit = gitsrc.sparse_clone(s.repository, checkout, SPARSE_PATTERNS, ref=s.ref or None)
        ctx.out.note(f"git {s.repository} {s.ref or 'HEAD'} at {commit}")
        groups, replacements = read_registry(checkout, s.full_ranges)
        ctx.out.write_json(
            REGISTRY_EXTRACT, {"repository": s.repository, "ref": s.ref, "commit": commit}
        )
        for group, rows in groups.items():
            ctx.out.write_jsonl(f"{FAMILIES_PREFIX}{group}{FAMILIES_SUFFIX}", rows)
        ctx.out.write_json(REPLACEMENTS_EXTRACT, replacements)
        ids = {row["id"] for rows in groups.values() for row in rows}
        result = ctx.fetcher.get(s.api_url)
        for record in result.to_records(kept=False):
            ctx.out.record_fetch(record)
        missing = api_missing(result.json(), ids)
        ctx.out.write_json(API_EXTRACT, missing)
        ctx.log.info(
            "fontsource: registry %s, %d families (%s); %d API-only ids",
            commit[:12],
            len(ids),
            ", ".join(f"{g} {len(r)}" for g, r in groups.items()),
            len(missing),
        )

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """Records of every registry family, then of the API-only ids (module docstring)."""
        snap = ctx.snapshot
        s = cast(Fontsource.Settings, ctx.settings)
        names = compile_drop_names(s.drop_names)
        meta = snap.load_json(REGISTRY_EXTRACT)
        replacements = (
            snap.load_json(REPLACEMENTS_EXTRACT) if snap.has(REPLACEMENTS_EXTRACT) else {}
        )
        for extract, doc in ((REGISTRY_EXTRACT, meta), (REPLACEMENTS_EXTRACT, replacements)):
            if not isinstance(doc, dict):
                raise RegistryError(f"{extract}: expected an object")
        seen: set[str] = set()
        for entry in snap.manifest.extracts:
            name = entry.path
            if not (name.startswith(FAMILIES_PREFIX) and name.endswith(FAMILIES_SUFFIX)):
                continue
            group = name.removeprefix(FAMILIES_PREFIX).removesuffix(FAMILIES_SUFFIX)
            for row in snap.iter_jsonl(name):
                fid, fam = (
                    (row.get("id"), row.get(FAMILY_JSON)) if isinstance(row, dict) else ("", None)
                )
                if not isinstance(fid, str) or not fid or not isinstance(fam, dict):
                    raise RegistryError(f"{name}: bad row {str(row)[:200]}")
                if _text(fam.get("family")) is None:
                    ctx.log.warning("fontsource: %s/%s has no family name; skipped", group, fid)
                    continue
                if fid in seen:
                    ctx.log.warning("fontsource: id %s is in two registry groups", fid)
                    continue
                if fam.get("status") not in STATUSES:
                    ctx.log.warning(
                        "fontsource: %s has status %r; read as live", fid, fam.get("status")
                    )
                seen.add(fid)
                yield from registry_records(
                    self.name, group, row, meta, replacements, names, s.symbol_classifications
                )
        rows = snap.load_json(API_EXTRACT) if snap.has(API_EXTRACT) else []
        for row in api_missing(rows, seen):
            yield from api_records(self.name, row, names, replacements)


COLLECTOR = Fontsource()
