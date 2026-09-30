"""Stages "export" and "export-site" (milestone-1 step 15). Owner: agent P12.

- "export" (15) writes ``build/catalog.json`` (``schemas/catalog.schema.json``,
  methodology §7) from every stage output, and its part of the next state,
  ``published_ranks`` ({rank key: {id: order}} for the catalog's fonts).
- "specimens" (15b, Milestone 2) runs between them and fills ``preview``.
- "export-site" (15c) copies the specimens' ``preview`` and flags into
  ``build/catalog.json``, then writes ``build/catalog-site.json``
  (``schemas/catalog-site.schema.json``) and ``build/names.json``
  (``schemas/names.schema.json``): the names and aliases of every eligible
  family in the universe, not only the catalog.

Raw source values appear only where ``ranking.toml`` ``publish_raw`` allows.
Output is canonical (``jsonio.dump``), so the same inputs give the same bytes.

Every piece of site wording comes from ``config/site.toml`` (``cfg.site``),
never from code or the sample: ``data_license``, ``views`` (``available`` is
computed: Rising needs ``ranks.rising.min_history_months`` of history),
``tiers``, ``license_classes``, the Nerd Font marker's wording (``nerd``:
``marker``, ``label`` and ``legend``), the source credits (``sources[].name``,
``measures``, ``url``, ``license``, ``publish_rank``; ``group`` and ``survey``
come from ``ranking.toml``) and the labels of package systems. Preinstalled
systems take their labels from ``preinstalled.toml``. Band labels are derived
from ``ranking.toml [display]``: "<from>\u2013<to>" (an en dash), and
"<from>+" for the open band.

How the per-font fields are made (``docs/catalog-schema.md`` says it for readers):

- **Fonts** are the catalog members (``membership.json``), sorted by id, that
  have an accepted download link. Stage "links" accepts one only when two
  sources agree or the owner approves it (gate K), and every catalog font
  needs a primary link (milestone-1 step 14), so a member without one is held
  back (``Inputs.unlinked``, logged and named in ``review.md``) until gate K
  settles it, the way a font waiting on its license is. The exact ranks close
  up over it (owner ruling of 2026-09-28, rank_holes): in every view, a ranked
  font's rank, order and range move up one for each held-back font ranked above
  it (``engine.order.close_up``), so it leaves no gap, and the ranks agree
  with the site, which numbers rows by position (M2-D2). Places past the exact
  top keep their order and band.
- **Views.** A font has an entry for every published rank key
  (``available_views``), except that ``coding`` holds monospace fonts only. A
  font with a placement is ranked; one without is unranked, with the reason
  stage "rank" gives (``surveys.unranked``: ``no_deliberate_evidence``,
  ``too_new`` or ``no_evidence``). Rising, which that skips, is ``too_new``
  for a font whose earliest channel date (``first_seen`` state) is under
  ``new_days`` old, as stage "rank" decides, and ``no_evidence`` otherwise.
- **Orders past the exact top.** When fewer fonts pass the two-group gate
  than the exact top holds (Developers & apps, whose sources are all one
  independence group while Flutter is off), the engine gives the fonts it
  held back orders inside the top but no rank. Nothing may sit there without
  a rank (methodology §6: a font that fails the gate sits at 101 or below; the
  site checks rank = order up to 100), so every unranked placement's order,
  and its range, move down by the same amount (``band_shift``), keeping their
  sequence, and its band is the one of the moved order.
- **Sources.** A source's evidence state, rank and z describe the font in
  the source's own survey view, where nothing abstains: ``desktop_installed``
  for desktop sources, ``project`` for project sources, less the families
  that failed L3 (stage "rank" removes them before equating, so they take no
  rank in a source either). ``rank_in_source`` is the competition rank
  (1, 2, 2, 4) among observed values; ``z`` is the source's values equated to
  the ruler over that view (``equate_source``), as stage "rank" equates them:
  Fonts Over Time's observed values are first replaced by its EWMA z from this
  month's ``smoothing`` state part (``surveys.smooth``, methodology §6), so its
  published z is the one the ranking used; ``weight_used`` is
  v_s = M_g·w_s·factor·guard/W_g in the overall rank, from the survey view that
  feeds it (``desktop_chosen`` or ``project``). ``abstains_in`` lists the
  font's views whose terms leave out a Linux source that covers it (every view
  but ``desktop_installed``, Rising included: D8). A source whose
  ``publish_rank`` is false shows no rank, z or value.
- **Sources listed** are the enabled engine sources, less any the parse stage
  dropped (no snapshot inside the stale window).
- **The Nerd Font link** (``links.nerd``) is stage "links"' choice, except that a link
  that failed the check recorded for the run's date (``Links.nerd_problem``) is left
  out, null, until a later check passes (owner ruling of 2026-09-29); the font stays.
"""

import bisect
import csv
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from tff_catalog import METHOD_VERSION, __version__, fontfiles, jsonio, stageio
from tff_catalog.config_model import OS_FAMILIES
from tff_catalog.config_model import RANK_KEYS as _CONFIG_RANK_KEYS
from tff_catalog.keys import match_key
from tff_catalog.records import SourceKey

if TYPE_CHECKING:
    import logging

    from tff_catalog.aliases import AliasRow
    from tff_catalog.confidence import Confidence
    from tff_catalog.config_model import Config, Display, SourceBase
    from tff_catalog.corrections import Tags, Term
    from tff_catalog.engine.order import Placement
    from tff_catalog.facts import Facts
    from tff_catalog.latin import LatinResult
    from tff_catalog.license_l3 import L3Result
    from tff_catalog.licenses import Verdict
    from tff_catalog.links import Link, Links
    from tff_catalog.membership import Membership
    from tff_catalog.parse import SourceSnapshot, StaleSource
    from tff_catalog.paths import Paths
    from tff_catalog.specimens.stage import Preview
    from tff_catalog.stages import StageContext
    from tff_catalog.state import State
    from tff_catalog.surveys import SurveyScores
    from tff_catalog.universe import Family, Universe

CATALOG_SCHEMA_VERSION = "0.1.0-draft"
NAMES_SCHEMA_VERSION = "0.1.0-draft"
SITE_SCHEMA_VERSION = "1.0.0-draft"  # catalog-site.schema.json's const

# Versioned schema constants (methodology §7): every published rank key. Renaming
# or removing one is a new major schema version of all three files; adding one is
# a minor version. tests/pipeline/test_export.py keeps the schemas equal to it.
RANK_KEYS: tuple[str, ...] = _CONFIG_RANK_KEYS

# Output files under build/, and their schemas under schemas/.
CATALOG_FILE = "catalog.json"
SITE_FILE = "catalog-site.json"
NAMES_FILE = "names.json"
SCHEMA_FILES = {
    CATALOG_FILE: "catalog.schema.json",
    SITE_FILE: "catalog-site.schema.json",
    NAMES_FILE: "names.schema.json",
}
SUPERFAMILY_COLUMNS = ("superfamily_id", "name", "family_id", "source", "first_seen", "reviewed_by")

GENERATOR = f"tff-catalog {__version__}"
UNKNOWN_COMMIT = "0" * 40  # code_commit outside a git checkout
DIGITS = 6  # decimals kept for scores, z, weights and values
COUNTED = frozenset({"observed", "censored"})  # states that make a term (methodology §3)
DESKTOP_VIEW, PROJECT_VIEW = "desktop_chosen", "project"  # the survey views feeding overall
NO_ABSTAIN_VIEW = "desktop_installed"  # D8: every install counts here

SPECIMEN_FLAGS = frozenset({"specimen_failed", "specimen_name_only", "specimen_hash_mismatch"})
SITE_FLAGS = SPECIMEN_FLAGS | {"too_new"}  # the rest ride in the rank entries on the site
SITE_MAX_FONT_BYTES = 20_000_000  # catalog-site.schema.json font_file.size maximum
FONT_FORMATS = frozenset({"ttf", "otf", "woff2", "woff"})

# Alias relations in output order: catalog.json aliases[], the site's, names.json names[].
ALIAS_RELATIONS = ("rename", "build", "package", "postscript", "bundle", "sibling")
SITE_ALIAS_RELATIONS = ("rename", "build", "postscript")
NAME_RELATIONS = (*ALIAS_RELATIONS, "related")
INELIGIBLE_NAME_RELATIONS = ("rename", "build", "postscript")
# Namespaces whose keys are font names people see (aliases.NAME_NAMESPACES); package,
# slug, folder and asset namespaces (npm, brew-cask, fs-id, gf-dir, nerd-folder, ...)
# are left out of name lists.
NAME_NAMESPACES = frozenset(
    {"gf-family", "font-name", "foundry-family", "almanac-name", "fot-name"}
)
# names.json ineligible reasons (M3 step 3's list plus license and latin) for
# universe drop codes; "non-font" names no font at all, so it is left out.
DROP_INELIGIBLE = {
    "icon": "icon",
    "emoji": "icon",
    "symbol": "icon",
    "barcode": "icon",
    "math": "icon",
    "music": "icon",
    "proprietary": "proprietary",
}


class ExportError(ValueError):
    """The stage files can't make a catalog; the message lists why."""


class MissingInput(FileNotFoundError):
    """A file export needs is missing; the message names the stage to run."""


# --- inputs ------------------------------------------------------------------------------------


def stage_input(paths: Paths, name: str) -> Any:
    """Stage file ``name`` (``stageio.STAGE_FILES``), or ``MissingInput`` naming its producer."""
    path = stageio.stage_path(paths, name)
    if not path.is_file():
        producer = stageio.STAGE_FILES[name].producer
        raise MissingInput(f"{path} is missing: run stage {producer!r} first")
    return stageio.load_stage(paths, name)


class Inputs:
    """The files export reads, each loaded on first use and then kept (validate shares
    one instance across its checks). A missing stage file raises ``MissingInput``."""

    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    def _stage(self, name: str) -> Any:
        return stage_input(self.paths, name)

    @cached_property
    def universe(self) -> Universe:
        return self._stage("universe")

    @cached_property
    def latin(self) -> Mapping[str, LatinResult]:
        return self._stage("latin")

    @cached_property
    def facts(self) -> Mapping[str, Facts]:
        return self._stage("facts")

    @cached_property
    def licenses(self) -> Mapping[str, Verdict]:
        return self._stage("licenses")

    @cached_property
    def terms(self) -> Mapping[str, Mapping[str, Mapping[str, Term]]]:
        return self._stage("terms")

    @cached_property
    def tags(self) -> Mapping[str, Tags]:
        return self._stage("tags")

    @cached_property
    def ruler(self) -> Mapping[str, float]:
        return self._stage("ruler")

    @cached_property
    def scores(self) -> Mapping[str, SurveyScores]:
        return self._stage("scores")

    @cached_property
    def ranks(self) -> Mapping[str, Mapping[str, Placement]]:
        return self._stage("ranks")

    @cached_property
    def membership(self) -> Membership:
        return self._stage("membership")

    @cached_property
    def l3(self) -> Mapping[str, L3Result]:
        return self._stage("l3")

    @cached_property
    def confidence(self) -> Mapping[str, Mapping[str, Confidence]]:
        return self._stage("confidence")

    @cached_property
    def links(self) -> Mapping[str, Links]:
        return self._stage("links")

    @cached_property
    def aliases(self) -> tuple[AliasRow, ...]:
        """``data/aliases.csv``; empty when the file does not exist yet."""
        from tff_catalog.aliases import load_aliases

        path = self.paths.aliases_csv
        return tuple(load_aliases(path)) if path.is_file() else ()

    @cached_property
    def superfamilies(self) -> dict[str, str]:
        return load_superfamilies(self.paths.superfamilies_csv)

    @cached_property
    def snapshots(self) -> Mapping[str, SourceSnapshot] | None:
        """``build/stage/snapshots.json`` by collector; None when parse wrote none."""
        from tff_catalog import parse

        if not (self.paths.stage / parse.SNAPSHOTS_FILE).is_file():
            return None
        return parse.load_snapshots(self.paths)

    @cached_property
    def stale(self) -> Mapping[str, StaleSource]:
        from tff_catalog import parse

        if not (self.paths.stage / parse.STALE_FILE).is_file():
            return {}
        return parse.load_stale(self.paths)

    @cached_property
    def first_seen(self) -> Mapping[str, Any]:
        """This run's ``first_seen`` state part (``state.read_part``), as stage "rank" reads it."""
        from tff_catalog.state import read_part

        return read_part(self.paths, "first_seen")

    @cached_property
    def smoothing(self) -> Mapping[str, Any]:
        """This run's ``smoothing`` state part (stage "rank"), as ``state.read_part`` has it."""
        from tff_catalog.state import read_part

        return read_part(self.paths, "smoothing")

    @cached_property
    def l3_failed(self) -> frozenset[str]:
        """Families whose L3 check failed: stage "rank" removes them before equating."""
        return frozenset(fid for fid, r in self.l3.items() if r.level == "failed")

    @cached_property
    def members(self) -> tuple[str, ...]:
        """The catalog: every member of ``membership.json`` with an accepted link, by id."""
        return tuple(fid for fid in self.listed if fid in self.links)

    @cached_property
    def listed(self) -> tuple[str, ...]:
        """Every member of ``membership.json``, sorted by id."""
        return tuple(sorted(fid for fid, st in self.membership.catalog.items() if st.member))

    @cached_property
    def unlinked(self) -> tuple[str, ...]:
        """Members held back because no download link is accepted yet (gate K)."""
        return tuple(fid for fid in self.listed if fid not in self.links)

    @cached_property
    def rows_by_family(self) -> dict[str, tuple[AliasRow, ...]]:
        out: dict[str, list[AliasRow]] = defaultdict(list)
        for row in self.aliases:
            if row.family_id:
                out[row.family_id].append(row)
        return {fid: tuple(sorted(rows)) for fid, rows in out.items()}

    @cached_property
    def resolver(self) -> Resolver:
        return Resolver(self.universe)


def load_superfamilies(path: Path) -> dict[str, str]:
    """``data/superfamilies.csv`` as {family_id: superfamily_id}; {} when the file is absent."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None or tuple(header) != SUPERFAMILY_COLUMNS:
            raise ExportError(f"{path}: header must be {','.join(SUPERFAMILY_COLUMNS)}")
        out: dict[str, str] = {}
        for number, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(SUPERFAMILY_COLUMNS):
                raise ExportError(f"{path}:{number}: expected {len(SUPERFAMILY_COLUMNS)} fields")
            superfamily, family = row[0], row[2]
            if out.setdefault(family, superfamily) != superfamily:
                raise ExportError(f"{path}:{number}: {family} is in two superfamilies")
        return out


def load_previews(paths: Paths) -> dict[str, Preview] | None:
    """``build/stage/previews.json`` from stage "specimens"; None when it has not run."""
    if not stageio.stage_path(paths, "previews").is_file():
        return None
    return stageio.load_stage(paths, "previews")


class Resolver:
    """Finds the family an alias names: its exact universe key, else the only family
    with that (ns, ``match_key``) key, else the only family of that display name."""

    def __init__(self, universe: Universe) -> None:
        self._exact: dict[SourceKey, str] = {}
        self._folded: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._named: dict[str, set[str]] = defaultdict(set)
        for fid, fam in universe.families.items():
            for key in fam.keys:
                self._exact[key] = fid
                self._folded[key.ns, match_key(key.key)].add(fid)
            self._named[match_key(fam.family)].add(fid)

    def resolve(self, ns: str, name: str) -> str | None:
        """The family id ``name`` in namespace ``ns`` names, or None (unknown or ambiguous)."""
        exact = self._exact.get(SourceKey(ns, name))
        if exact is not None:
            return exact
        for found in (self._folded.get((ns, match_key(name))), self._named.get(match_key(name))):
            if found:
                return next(iter(found)) if len(found) == 1 else None
        return None


# --- small shared helpers --------------------------------------------------------------------


def num(x: float | None) -> float | None:
    """``x`` rounded to ``DIGITS`` decimals, with -0.0 as 0.0; None stays None."""
    if x is None:
        return None
    if not math.isfinite(x):
        raise ExportError(f"{x!r} is not a finite number")
    return round(x, DIGITS) + 0.0


def code_commit(root: Path) -> str:
    """The commit checked out at ``root``, or ``UNKNOWN_COMMIT`` outside a git checkout."""
    from tff_catalog.gitsrc import head_sha

    try:
        sha = head_sha(root).strip()
    except RuntimeError, OSError:
        return UNKNOWN_COMMIT
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else UNKNOWN_COMMIT


def history_months(state: State, run_date: date) -> int:
    """Distinct months with a merged run (``run_history``), this run's month included."""
    months = {str(r.get("run_date", ""))[:7] for r in state.run_history if r.get("run_date")}
    months.add(run_date.isoformat()[:7])
    return len(months)


def available_views(cfg: Config, state: State, run_date: date) -> tuple[str, ...]:
    """The rank keys published this run, in the rank selector's order (``site.toml`` views).

    Every key except Rising, which needs ``min_history_months`` months of merged
    runs (design-m1 gap G14: from the third merged refresh).
    """
    rising = history_months(state, run_date) >= cfg.ranking.ranks.rising.min_history_months
    return tuple(v.key for v in cfg.site.views if v.key != "rising" or rising)


def band_rows(display: Display) -> list[tuple[str, int, int | None]]:
    """(label, from, to) of each band past the exact top, the open one last."""
    rows: list[tuple[str, int, int | None]] = [
        (f"{lo}\u2013{hi}", lo, hi) for lo, hi in display.bands
    ]
    rows.append((f"{display.open_band_from}+", display.open_band_from, None))
    return rows


def band_label(order: int, display: Display) -> str | None:
    """The band holding ``order``, or None inside the exact top."""
    if order <= display.exact_top:
        return None
    for label, lo, hi in band_rows(display):
        if order >= lo and (hi is None or order <= hi):
            return label
    return None


def band_shift(placed: Mapping[str, Placement], exact_top: int) -> int:
    """How far a rank key's unranked placements move (``engine.order.band_shift``)."""
    from tff_catalog.engine.order import band_shift as shift

    return shift(placed, exact_top)


def new_fonts(first_seen: Mapping[str, Any], run_date: date, days: int) -> frozenset[str]:
    """Families whose earliest channel date is under ``days`` before the run: Rising's
    "New" (the rule of stage "rank", which reads the same ``first_seen`` state part)."""
    new = set()
    for fid, entry in first_seen.items():
        dates = [date.fromisoformat(d) for d in (entry.get("sources") or {}).values() if d]
        if dates and (run_date - min(dates)).days < days:
            new.add(fid)
    return frozenset(new)


def view_sources(cfg: Config, key: str) -> frozenset[str]:
    """The engine sources rank key ``key`` weighs."""
    r = cfg.ranking
    match key:
        case "overall":
            return frozenset(r.surveys.desktop.weights) | frozenset(r.surveys.project.weights)
        case "desktop_chosen" | "desktop_installed":
            return frozenset(r.surveys.desktop.weights)
        case "project":
            return frozenset(r.surveys.project.weights)
        case "coding":
            return frozenset(r.ranks.coding.weights)
        case "dev_apps":
            return frozenset(r.ranks.dev_apps.weights)
        case "rising":
            return frozenset(r.ranks.rising.sources)
    raise KeyError(key)


def view_abstains(cfg: Config, key: str) -> bool:
    """Whether Linux sources abstain in ``key``: overall follows most chosen, and Rising
    abstains like every rank but most installed (D8; ``surveys.RISING_ABSTAINS``, and
    stage "correct" writes ``terms.json["rising"]`` so)."""
    ranks = cfg.ranking.ranks
    if key == "overall":
        return ranks.desktop_chosen.abstain
    if key == "rising":
        return True
    return bool(getattr(ranks, key).abstain)


def monospace_only(cfg: Config, key: str) -> bool:
    """Whether ``key`` holds monospace fonts only (the Coding view)."""
    view = getattr(cfg.ranking.ranks, key, None)
    return bool(getattr(view, "monospace_only", False))


def view_terms(
    terms: Mapping[str, Mapping[str, Mapping[str, Term]]], key: str
) -> Mapping[str, Mapping[str, Term]] | None:
    """{source: {id: Term}} of ``key``; overall merges most chosen and project. None if absent."""
    if key in terms:
        return terms[key]
    if key == "overall" and DESKTOP_VIEW in terms and PROJECT_VIEW in terms:
        return {**terms[PROJECT_VIEW], **terms[DESKTOP_VIEW]}
    return None


def base_view(src: SourceBase) -> str:
    """The view where a source's evidence is shown: its survey, with no abstentions."""
    return NO_ABSTAIN_VIEW if src.survey == "desktop" else PROJECT_VIEW


def survey_view(src: SourceBase) -> str:
    """The view of a source's survey that feeds the overall rank."""
    return DESKTOP_VIEW if src.survey == "desktop" else PROJECT_VIEW


def published_sources(cfg: Config, inputs: Inputs) -> tuple[str, ...]:
    """Enabled engine sources, in ``ranking.toml`` order, less those parse dropped."""
    snapshots, stale = inputs.snapshots, inputs.stale
    out = []
    for name, src in cfg.ranking.sources.all().items():
        if not src.enabled:
            continue
        dropped = src.collector in stale and stale[src.collector].dropped
        if dropped or (snapshots is not None and src.collector not in snapshots):
            continue
        out.append(name)
    return tuple(out)


def publish_rank(cfg: Config, source: str) -> bool:
    """The terms ruling lets outputs show this source's per-font ranks (``site.toml``)."""
    credit = cfg.site.sources.get(source)
    return credit is not None and credit.publish_rank


def license_name(spdx: str, cfg: Config) -> str:
    """A readable name for an SPDX expression, from ``licenses.toml``; unknown ids stay as written."""
    allowed = cfg.licenses.allowed
    if spdx in allowed:
        return allowed[spdx].name
    parts = re.split(r"\s+(AND|OR)\s+", spdx.strip())
    words = []
    for i, part in enumerate(parts):
        if i % 2:
            words.append("and" if part == "AND" else "or")
        else:
            bare = part.strip("() ")
            words.append(allowed[bare].name if bare in allowed else bare)
    return " ".join(words)


# --- alias names ------------------------------------------------------------------------------


def family_names(
    fam: Family, rows: Iterable[AliasRow], relations: tuple[str, ...]
) -> list[tuple[str, str, str]]:
    """(name, relation, detail) for ``fam``: its alias rows in ``NAME_NAMESPACES`` with those
    relations, plus the name its id was minted from as a rename. A name equal to the
    family's own, or to one already listed with the same relation and detail (by
    ``match_key``), is left out; sorted by relation order, then ``match_key``."""
    found = {
        (row.alias, row.relation, row.detail if row.relation == "build" else "")
        for row in rows
        if row.relation in relations and row.ns in NAME_NAMESPACES
    }
    if "rename" in relations:
        found.add((fam.minted_from, "rename", ""))
    own = match_key(fam.family)
    seen: set[tuple[str, str, str]] = set()
    kept = []
    for name, relation, detail in sorted(
        found, key=lambda n: (relations.index(n[1]), match_key(n[0]), n[0], n[2])
    ):
        key = (match_key(name), relation, detail)
        if key[0] != own and key not in seen:
            seen.add(key)
            kept.append((name, relation, detail))
    return kept


def related_families(inputs: Inputs) -> dict[str, list[dict[str, str]]]:
    """``related[]`` per family: both ends of every "related" alias row that names a family."""
    families = inputs.universe.families
    found: dict[str, dict[str, str | None]] = defaultdict(dict)
    for row in inputs.aliases:
        if row.relation != "related" or row.family_id not in families:
            continue
        other = inputs.resolver.resolve(row.ns, row.alias)
        if other is None or other == row.family_id:
            continue
        note = " ".join(row.detail.split()) or None
        for a, b in ((row.family_id, other), (other, row.family_id)):
            found[a].setdefault(b, note)
    return {
        fid: [{"id": other} | ({"note": note} if note else {}) for other, note in sorted(m.items())]
        for fid, m in sorted(found.items())
    }


def distinct_families(inputs: Inputs) -> dict[str, list[str]]:
    """``distinct_from`` per family: both ends of every "distinct" row (sibling blocks)."""
    families = inputs.universe.families
    found: dict[str, set[str]] = defaultdict(set)
    for row in inputs.aliases:
        if row.relation != "distinct" or row.family_id not in families:
            continue
        other = inputs.resolver.resolve(row.ns, row.alias)
        if other is not None and other != row.family_id:
            found[other].add(row.family_id)
            found[row.family_id].add(other)
    return {fid: sorted(ids) for fid, ids in found.items()}


# --- the gates, as names.json and validate need them --------------------------------------------


def gate_status(
    fid: str, inputs: Inputs, *, l3: bool = True, pending: bool = True
) -> tuple[str, str | None]:
    """Where a universe family stands: ("eligible", None), ("ineligible", reason),
    ("unknown", why) when a gate stage has no answer for it, or ("skip", None) for a
    record that names no font. Reasons are names.json's ``ineligible`` reasons.

    The gates are no drop, Latin passed and a license that is not excluded, and, with
    ``l3``, no failed L3 check (stage "verify"). A license waiting for the owner's ruling
    passes when ``pending`` (names.json, so Milestone 3 knows the font), and fails
    without it, as in stage "correct" (``corrections.eligible_families``: filter first)."""
    fam = inputs.universe.families[fid]
    if fam.drop is not None:
        reason = DROP_INELIGIBLE.get(fam.drop)
        return ("ineligible", reason) if reason else ("skip", None)
    latin = inputs.latin.get(fid)
    if latin is None:
        return "unknown", "no Latin result"
    if not latin.latin:
        return "ineligible", "cjk" if latin.reason == "cjk" else "latin"
    verdict = inputs.licenses.get(fid)
    if verdict is None:
        return "unknown", "no license verdict"
    if verdict.license is None or verdict.license.status == "excluded":
        return "ineligible", "license"  # no license found is excluded too (D3)
    if not pending and verdict.license.status != "allowed":
        return "ineligible", "license"  # waiting for the owner: never ranked before the ruling
    result = inputs.l3.get(fid) if l3 else None
    if result is not None and result.level == "failed":
        return "ineligible", "license"
    return "eligible", None


def ineligible(fid: str, inputs: Inputs, *, l3: bool = True) -> str | None:
    """Why ``fid`` may not be ranked at all (``gate_status``), in words; None when it may.

    Without ``l3``: the gates before ranking, which ``terms.json`` and
    ``ruler_counts.json`` follow (stage "correct" runs before "verify"). With it: also
    a failed L3 check, which ``ruler.json``, ``scores.json`` and ``ranks.json`` follow."""
    if fid not in inputs.universe.families:
        return "not in the universe"
    status, reason = gate_status(fid, inputs, l3=l3, pending=False)
    return None if status == "eligible" else f"{status}: {reason or 'no font'}"


def publishable(fid: str, inputs: Inputs) -> str | None:
    """None when ``fid`` may be in the catalog: it passes every gate and its license is
    allowed (not one still waiting for the owner); else why not, in words. The L3 level
    (L3 or an owner ruling) is checked separately (``validate``)."""
    why = ineligible(fid, inputs)
    if why is not None:
        return why
    lic = inputs.licenses[fid].license
    if lic is None or lic.status != "allowed":
        return f"license not allowed ({'unknown' if lic is None else lic.status})"
    return None


# --- catalog.json ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceView:
    """One source's evidence in its base view: terms, competition ranks and equated z."""

    terms: Mapping[str, Term]
    ranks: Mapping[str, int]
    z: Mapping[str, float]


def source_view(
    terms: Mapping[str, Term],
    ruler: Mapping[str, float],
    ranked_on: Mapping[str, float] | None = None,
) -> SourceView:
    """Ranks among observed values (1 is the highest) and z equated to the ruler.

    ``ranked_on`` gives the values stage "rank" equates in place of observed ones
    (Fonts Over Time's EWMA z, ``smoothed_values``); an observed family it lacks keeps
    its own value, as stage "rank" does. Ranks always come from the source's own
    values."""
    from tff_catalog.engine.equate import equate_source

    observed = {
        fid: t.value for fid, t in terms.items() if t.state == "observed" and t.value is not None
    }
    ascending = sorted(observed.values())
    ranks = {
        fid: len(ascending) - bisect.bisect_right(ascending, v) + 1 for fid, v in observed.items()
    }
    swap = ranked_on or {}
    xs = {
        fid: swap.get(fid, observed[fid]) if fid in observed else -math.inf
        for fid, t in terms.items()
        if t.state in COUNTED
    }
    return SourceView(terms, ranks, equate_source(xs, ruler))


def smoothed_values(smoothing: Mapping[str, Any], run_date: date) -> dict[str, float]:
    """Fonts Over Time's EWMA z by family (``smoothing.json`` ``fot_ewma``), which stage
    "rank" ranks on in place of its observed values; {} unless the part is this run
    month's (stage "rank" has not run this month, or Fonts Over Time is off)."""
    if smoothing.get("month") != f"{run_date:%Y-%m}":
        return {}
    return {fid: float(z) for fid, z in (smoothing.get("fot_ewma") or {}).items()}


def state_reason(term: Term | None, src: SourceBase) -> tuple[str, str | None]:
    """A term's evidence state and a reason the schemas accept for that state."""
    fallback = "no_package" if src.survey == "desktop" else "outside_frame"
    if term is None:
        return "not_covered", fallback
    match term.state:
        case "censored":
            if term.reason in ("below_floor", "no_value"):
                return "censored", term.reason
            return "censored", "no_value" if term.value is None else "below_floor"
        case "not_covered":
            if term.reason in ("no_package", "outside_frame", "merged_into_parent"):
                return "not_covered", term.reason
            return "not_covered", fallback
    return term.state, None


class CatalogBuilder:
    """Builds catalog.json's parts from ``Inputs``; every method is a pure function."""

    def __init__(self, cfg: Config, inputs: Inputs, run_date: date, views: tuple[str, ...]) -> None:
        self.cfg = cfg
        self.inputs = inputs
        self.run_date = run_date
        self.views = views
        self.sources = published_sources(cfg, inputs)
        self.src = cfg.ranking.sources.all()
        from tff_catalog.surveys import FOT

        failed = inputs.l3_failed
        smoothed = smoothed_values(inputs.smoothing, run_date)
        self.base = {}
        for s in self.sources:
            terms = inputs.terms.get(base_view(self.src[s]), {}).get(s, {})
            kept = {fid: t for fid, t in terms.items() if fid not in failed}
            self.base[s] = source_view(kept, inputs.ruler, smoothed if s == FOT else None)
        self.related = related_families(inputs)
        self.view_terms = {k: view_terms(inputs.terms, k) for k in RANK_KEYS}
        exact_top = cfg.ranking.display.exact_top
        self.shift = {k: band_shift(placed, exact_top) for k, placed in inputs.ranks.items()}
        from tff_catalog.engine.order import held_places

        held = frozenset(inputs.unlinked)
        self.held = {k: held_places(placed, held) for k, placed in inputs.ranks.items()}
        self.new = new_fonts(inputs.first_seen, run_date, cfg.ranking.ranks.rising.new_days)

    @cached_property
    def reasons(self) -> dict[str, dict[str, str]]:
        """Stage "rank"'s reason for each catalog font without a place, by rank key
        (``surveys.unranked``; Rising is not among them)."""
        from tff_catalog.surveys import unranked

        facts = self.inputs.facts
        listed = {
            key: [
                fid
                for fid in self.inputs.members
                if not monospace_only(self.cfg, key) or facts[fid].is_monospace
            ]
            for key in self.views
        }
        return unranked(self.inputs.terms, self.inputs.scores, self.cfg.ranking, listed)

    # -- the document --

    def document(self, commit: str) -> dict[str, Any]:
        from tff_catalog.config import config_hash, ranking_hash

        members = self.inputs.members
        problems = self.missing(members)
        if problems:
            raise ExportError("catalog members lack stage data: " + "; ".join(problems))
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "run": {
                "date": self.run_date.isoformat(),
                "method_version": METHOD_VERSION,
                "config_sha256": config_hash(self.cfg),
                "ranking_toml_sha256": ranking_hash(self.cfg),
                "code_commit": commit,
                "generator": GENERATOR,
            },
            "data_license": data_license(self.cfg),
            "sources": [self.source(s) for s in self.sources],
            "fonts": [self.font(fid) for fid in members],
        }

    def missing(self, members: Iterable[str]) -> list[str]:
        """Members some stage has no row for (a pipeline fault, not an eligibility one)."""
        tables = {
            "universe": self.inputs.universe.families,
            "facts": self.inputs.facts,
            "latin": self.inputs.latin,
            "licenses": self.inputs.licenses,
            "links": self.inputs.links,
        }
        return [
            f"{fid} ({', '.join(name for name, table in tables.items() if fid not in table)})"
            for fid in members
            if any(fid not in table for table in tables.values())
        ]

    def source(self, s: str) -> dict[str, Any]:
        src = self.src[s]
        snap = (self.inputs.snapshots or {}).get(src.collector)
        scores = self.inputs.scores.get(survey_view(src))
        return {
            "id": s,
            "collector": src.collector,
            "group": src.group,
            "survey": src.survey,
            "weight": num(scores.weights.get(s, 0.0)) if scores else 0.0,
            "publish_raw": src.publish_raw,
            "data_date": (snap.data_date or snap.snapshot).isoformat() if snap else None,
            "fetched_at": snap.fetched_at if snap else None,
            "stale": bool(snap.stale) if snap else src.collector in self.inputs.stale,
            "ruler_overlap": scores.overlaps.get(s) if scores else None,
        }

    # -- one font --

    def font(self, fid: str) -> dict[str, Any]:
        inp = self.inputs
        fam = inp.universe.families[fid]
        facts, latin, verdict = inp.facts[fid], inp.latin[fid], inp.licenses[fid]
        l3, links, tags = inp.l3.get(fid), inp.links[fid], inp.tags.get(fid)
        keys = self.font_views(facts.is_monospace)
        ranks = {k: self.rank(k, fid) for k in keys}
        lic = self.license(fam, verdict, l3, links)
        return {
            "id": fid,
            "family": fam.family,
            "category": facts.category,
            "is_monospace": facts.is_monospace,
            "superfamily_id": inp.superfamilies.get(fid),
            "aliases": [
                {"name": n, "relation": r}
                for n, r in dict.fromkeys(
                    (n, r)
                    for n, r, _ in family_names(
                        fam, inp.rows_by_family.get(fid, ()), ALIAS_RELATIONS
                    )
                )
            ],
            "related": self.related.get(fid, []),
            "license": lic,
            "font_file": font_file(l3),
            "latin": {"basis": latin.basis, "coverage": latin.coverage},
            "formats": {"variable": facts.variable, "static": facts.static},
            "preview_ok": bool(verdict.preview_ok and lic["redistributable"]),
            "preview": None,
            "preinstalled_on": [
                {"system": s} for s in sorted(set(tags.preinstalled_on if tags else ()))
            ],
            "pulled_in_by": [
                {"system": s, "package": p}
                for s, p in sorted(set(tags.pulled_in_by if tags else ()))
            ],
            "links": {
                "primary": link(links.primary),
                "designer": link(links.designer) if links.designer else None,
                # A Nerd link that failed this run's check is left out until it passes
                # again (owner ruling of 2026-09-29): no marker, link or filter match.
                "nerd": link(links.nerd) if links.nerd and not links.nerd_problem else None,
            },
            "first_seen": fam.first_seen.isoformat(),
            "flags": self.flags(fid, tags, ranks),
            "ranks": ranks,
            "sources": {s: self.font_source(s, fid, keys) for s in self.sources},
        }

    def font_views(self, is_monospace: bool) -> list[str]:
        return [k for k in self.views if is_monospace or not monospace_only(self.cfg, k)]

    def rank(self, key: str, fid: str) -> dict[str, Any]:
        """One rank entry: a placement with its tier and range, or an unranked reason."""
        scores = self.inputs.scores.get(key)
        fused = scores.fused.get(fid) if scores else None
        placed = self.inputs.ranks.get(key, {}).get(fid)
        entry: dict[str, Any] = {
            "score": num(fused.score) if fused else None,
            "groups": len(fused.groups) if fused else 0,
        }
        if placed is None:
            return entry | {
                "rank": None,
                "band": None,
                "order": None,
                "tier": None,
                "range": None,
                "gate_held": False,
                "unranked": self.unranked(key, fid),
            }
        from tff_catalog.engine.order import close_up

        conf = self.inputs.confidence.get(key, {}).get(fid)
        if placed.rank is None:
            shift = self.shift.get(key, 0)
            order, rank = placed.order + shift, None
            span = [conf.range[0] + shift, conf.range[1] + shift] if conf else None
        else:  # the exact ranks close up over held-back members (``Inputs.unlinked``)
            gone = self.held.get(key, [])
            order = rank = close_up(gone, placed.order)
            span = [close_up(gone, conf.range[0]), close_up(gone, conf.range[1])] if conf else None
        return entry | {
            "rank": rank,
            "band": band_label(order, self.cfg.ranking.display),
            "order": order,
            "tier": conf.tier if conf else None,
            "range": span,
            "gate_held": placed.gate_held,
            "unranked": None,
        }

    def unranked(self, key: str, fid: str) -> str:
        if key != "rising":
            return self.reasons.get(key, {}).get(fid, "no_evidence")
        # Rising (surveys.unranked skips it): its "New" rule, as stage "rank" applies it.
        return "too_new" if fid in self.new else "no_evidence"

    def abstains(self, s: str, fid: str, key: str) -> bool:
        """A Linux source covers the font in its base view but leaves it out of ``key``."""
        src = self.src.get(s)
        if src is None or not src.linux or key == NO_ABSTAIN_VIEW:
            return False
        if not view_abstains(self.cfg, key) or s not in view_sources(self.cfg, key):
            return False
        base = self.base.get(s)
        term = base.terms.get(fid) if base else None
        terms = self.view_terms[key]
        if term is None or term.state not in COUNTED or terms is None or s not in terms:
            return False
        return fid not in terms[s]

    def font_source(self, s: str, fid: str, keys: list[str]) -> dict[str, Any]:
        src, view = self.src[s], self.base[s]
        term = view.terms.get(fid)
        state, reason = state_reason(term, src)
        shown = publish_rank(self.cfg, s)
        entry: dict[str, Any] = {
            "state": state,
            "reason": reason,
            "rank_in_source": view.ranks.get(fid) if shown and state == "observed" else None,
            "z": num(view.z.get(fid)) if shown and state in COUNTED else None,
            "weight_used": self.weight_used(s, fid),
            "abstains_in": [k for k in keys if self.abstains(s, fid, k)],
        }
        if src.publish_raw and shown and state in COUNTED and term and term.value is not None:
            entry["value"] = num(term.value)
        return entry

    def weight_used(self, s: str, fid: str) -> float | None:
        """v_s = M_g·w_s·factor·guard / W_g in the overall rank; None without a term there."""
        src = self.src[s]
        key = survey_view(src)
        term = (self.view_terms[key] or {}).get(s, {}).get(fid)
        scores = self.inputs.scores.get(key)
        if term is None or term.state not in COUNTED or scores is None:
            return None
        total = sum(scores.weights.values())
        if total <= 0:
            return 0.0
        mix = self.cfg.ranking.ranks.overall.mix.get(key, 0.0)
        return num(mix * scores.weights.get(s, 0.0) * term.factor * self.guard(s, fid, key) / total)

    def guard(self, s: str, fid: str, key: str) -> float:
        """The guard factor overall used for this term (overall reuses each survey's)."""
        for k in ("overall", key):
            scores = self.inputs.scores.get(k)
            fused = scores.fused.get(fid) if scores else None
            if fused is not None and any(src == s for src, _ in fused.guard):
                return dict(fused.guard)[s]
        return 1.0

    def flags(
        self, fid: str, tags: Tags | None, ranks: Mapping[str, Mapping[str, Any]]
    ) -> list[str]:
        """Tags' and terms' flags, plus gate_held and too_new from the rank entries."""
        flags = set(tags.flags) if tags else set()
        for by_source in self.inputs.terms.values():
            for by_id in by_source.values():
                term = by_id.get(fid)
                if term is not None:
                    flags.update(term.flags)
        if any(e["gate_held"] for e in ranks.values()):
            flags.add("gate_held")
        if any(e["unranked"] == "too_new" for e in ranks.values()):
            flags.add("too_new")
        return sorted(flags)

    def license(
        self, fam: Family, verdict: Verdict, l3: L3Result | None, links: Links
    ) -> dict[str, Any]:
        lic = verdict.license
        spdx = lic.spdx if lic else verdict.spdx or "NOASSERTION"
        name = license_name(spdx, self.cfg)
        needs_credit = bool(lic and lic.attribution_required)
        if l3 is not None:
            level = l3.level
        else:
            level = "L2" if len({s for s, _ in verdict.seen}) > 1 else "L1"
        if lic is not None and lic.status == "allowed" and not lic.redistributable:
            # Rule 3 (2026-09-30): stage "licenses" excludes these; one here is a bug.
            raise ExportError(f"{fam.id}: {spdx} is allowed but not redistributable (Rule 3)")
        return {
            "spdx": spdx,
            "name": name,
            "class": lic.group if lic else None,
            "redistributable": bool(lic and lic.redistributable),
            "attribution_required": needs_credit,
            "attribution": (
                attribution(fam.family, name, l3.copyright if l3 else None)
                if needs_credit
                else None
            ),
            "text_url": (l3.text_url if l3 else None) or dict(fam.urls).get("license"),
            "verified_level": level,
            "text_sha256": l3.text_sha256 if l3 else None,
            "checked_on": l3.checked_on.isoformat() if l3 else None,
        }


def data_license(cfg: Config) -> dict[str, Any]:
    d = cfg.site.data_license
    return {"spdx": d.spdx, "provisional": d.provisional, "url": d.url}


def link(value: Link) -> dict[str, str]:
    out = {"url": value.url} | ({"label": value.label} if value.label else {})
    return out | ({"note": value.note} if value.note else {})


def attribution(family: str, license_title: str, copyright: str | None = None) -> str:
    """The credit line for a license that requires one: the family, the copyright line of
    the file L3 read (name ID 0) when it has one, and the license.

    Not the designer link's label: that names the link's destination, not the holder.
    """
    holder = " ".join(copyright.split()) if copyright else ""
    return f"{family}, {holder}, {license_title}" if holder else f"{family}, {license_title}"


def font_file(l3: L3Result | None) -> dict[str, Any] | None:
    """The file the license check read (Milestone 2 previews it), when it is complete.

    A font inside a release archive keeps its ``<archive>.zip#<member>`` reference
    (``fontfiles.member_url``); its format, sha256 and size are the member's.
    """
    ref = l3.font_file if l3 else None
    if ref is None or ref.sha256 is None or not ref.size:
        return None
    member = fontfiles.split_member(ref.url)
    path = member[1] if member is not None else urlsplit(ref.url).path
    fmt = PurePosixPath(path).suffix.lower().lstrip(".")
    if fmt not in FONT_FORMATS:
        return None
    return {"url": ref.url, "sha256": ref.sha256, "size": ref.size, "format": fmt}


def catalog_document(
    inputs: Inputs, cfg: Config, state: State, run_date: date, commit: str
) -> dict[str, Any]:
    """``catalog.json`` from loaded inputs (``build_catalog`` without the file reads)."""
    views = available_views(cfg, state, run_date)
    return CatalogBuilder(cfg, inputs, run_date, views).document(commit)


def build_catalog(ctx: StageContext) -> dict[str, Any]:
    """The ``catalog.json`` document."""
    return catalog_document(
        Inputs(ctx.paths), ctx.config, ctx.state, ctx.run_date, code_commit(ctx.paths.root)
    )


def published_ranks(catalog: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """State ``published_ranks``: {rank key: {id: order}} for every ranked catalog font."""
    out: dict[str, dict[str, int]] = {}
    for font in catalog["fonts"]:
        for key, entry in font["ranks"].items():
            if entry["order"] is not None:
                out.setdefault(key, {})[font["id"]] = entry["order"]
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def with_previews(
    catalog: Mapping[str, Any], previews: Mapping[str, Preview] | None
) -> dict[str, Any]:
    """``catalog`` with each ``preview_ok`` font's ``preview`` and specimen flags from stage
    "specimens"; fonts it has no entry for keep no preview. None (not run) changes nothing."""
    doc: dict[str, Any] = json.loads(jsonio.canonical_bytes(catalog))  # a deep copy
    if previews is None:
        return doc
    for font in doc["fonts"]:
        found = previews.get(font["id"]) if font["preview_ok"] else None
        flags = set(font["flags"]) - SPECIMEN_FLAGS
        if found is None:
            font["preview"] = None
        else:
            has_image = found.path is not None and found.sha256 is not None
            font["preview"] = {"path": found.path, "sha256": found.sha256} if has_image else None
            flags |= set(found.flags) & SPECIMEN_FLAGS
        font["flags"] = sorted(flags)
    return doc


# --- catalog-site.json -------------------------------------------------------------------------


def systems(cfg: Config) -> list[dict[str, str]]:
    """Every preinstalled system and package system, by operating system then id."""
    found = {sid: (s.label, s.os) for sid, s in cfg.preinstalled.systems.items()}
    for sid, ps in cfg.site.package_systems.items():
        found.setdefault(sid, (ps.label, "linux"))
    return [
        {"id": sid, "label": label, "os": family}
        for sid, (label, family) in sorted(
            found.items(), key=lambda i: (OS_FAMILIES.index(i[1][1]), i[0])
        )
    ]


def site_font(
    font: Mapping[str, Any], keep: frozenset[str], views: frozenset[str]
) -> dict[str, Any]:
    """One catalog font, trimmed to the site's fields (catalog-site.schema.json)."""
    lic = font["license"]
    preview_ok = font["preview_ok"]
    file = font["font_file"] if preview_ok else None
    if file is not None and file["size"] > SITE_MAX_FONT_BYTES:
        file = None  # too big to serve for "Type your own text"
    rank_fields = ("rank", "band", "order", "tier", "range", "score", "gate_held", "unranked")
    return {
        "id": font["id"],
        "family": font["family"],
        "category": font["category"],
        "is_monospace": font["is_monospace"],
        "formats": font["formats"],
        "latin": font["latin"],
        "license": {
            k: lic[k]
            for k in (
                "spdx",
                "name",
                "class",
                "redistributable",
                "attribution_required",
                "attribution",
                "text_url",
            )
        },
        "preview_ok": preview_ok,
        "preview": font["preview"] if preview_ok else None,
        "font_file": file,
        "links": font["links"],
        "preinstalled_on": font["preinstalled_on"],
        "pulled_in_by": [
            {"system": p["system"], "package": p["package"]} for p in font["pulled_in_by"]
        ],
        "aliases": [a for a in font["aliases"] if a["relation"] in SITE_ALIAS_RELATIONS],
        "first_seen": font["first_seen"],
        "flags": [f for f in font["flags"] if f in SITE_FLAGS],
        "ranks": {
            k: {f: e[f] for f in rank_fields} for k, e in font["ranks"].items() if k in views
        },
        "sources": {
            s: {f: e[f] for f in ("state", "rank_in_source", "reason", "abstains_in")}
            for s, e in font["sources"].items()
            if s in keep
        },
    }


def site_document(
    catalog: Mapping[str, Any], cfg: Config, views: tuple[str, ...]
) -> dict[str, Any]:
    """``catalog-site.json`` from a catalog document (``build_site`` without the context)."""
    site = cfg.site
    shown = [s for s in catalog["sources"] if s["data_date"] is not None]
    return {
        "schema_version": SITE_SCHEMA_VERSION,
        "run": {
            "date": catalog["run"]["date"],
            "method_version": catalog["run"]["method_version"],
            "ranking_toml_sha256": catalog["run"]["ranking_toml_sha256"],
            "generator": GENERATOR,
        },
        "data_license": data_license(cfg),
        "views": [
            {"key": v.key, "label": v.label, "measures": v.measures, "available": v.key in views}
            for v in site.views
        ],
        "bands": [
            {"label": label, "from": lo, "to": hi}
            for label, lo, hi in band_rows(cfg.ranking.display)
        ],
        "tiers": {"A": site.tiers.A, "B": site.tiers.B, "C": site.tiers.C},
        "sources": [
            {
                "id": s["id"],
                "name": site.sources[s["id"]].name,
                "group": s["group"],
                "survey": s["survey"],
                "measures": site.sources[s["id"]].measures,
                "url": site.sources[s["id"]].url,
                "license": site.sources[s["id"]].license,
                "data_date": s["data_date"],
                "stale": s["stale"],
                "publish_rank": site.sources[s["id"]].publish_rank,
            }
            for s in shown
        ],
        "systems": systems(cfg),
        "license_classes": [{"id": c.id, "label": c.label} for c in site.license_classes],
        "nerd": {"marker": site.nerd.marker, "label": site.nerd.label, "legend": site.nerd.legend},
        "fonts": [
            site_font(f, frozenset(s["id"] for s in shown), frozenset(views))
            for f in catalog["fonts"]
        ],
    }


def build_site(catalog: dict[str, Any], ctx: StageContext) -> dict[str, Any]:
    """The trimmed ``catalog-site.json`` document for the filterable list."""
    return site_document(catalog, ctx.config, available_views(ctx.config, ctx.state, ctx.run_date))


# --- names.json --------------------------------------------------------------------------------


def names_document(
    inputs: Inputs, run_date: date, log: logging.Logger | None = None
) -> dict[str, Any]:
    """``names.json`` from loaded inputs.

    ``families``: every universe family that passes the gates so far (not dropped, Latin
    passed, license not excluded, L3 not failed; a license still waiting for the owner's
    ruling counts, so Milestone 3 knows the font). ``ineligible``: the names of dropped
    and failed families, and the alias table's ineligible rows, with their reasons.
    """
    universe, members = inputs.universe, frozenset(inputs.members)
    distinct = distinct_families(inputs)
    families: list[dict[str, Any]] = []
    ineligible: set[tuple[str, str]] = set()
    for fid in sorted(universe.families):
        fam = universe.families[fid]
        status, reason = gate_status(fid, inputs)
        rows = inputs.rows_by_family.get(fid, ())
        if status == "eligible":
            families.append(
                {
                    "id": fid,
                    "family": fam.family,
                    "superfamily_id": inputs.superfamilies.get(fid),
                    "in_catalog": fid in members,
                    "names": [
                        {"name": n, "relation": r} | ({"detail": d} if r == "build" else {})
                        for n, r, d in family_names(fam, rows, NAME_RELATIONS)
                    ],
                    "distinct_from": distinct.get(fid, []),
                }
            )
        elif status == "ineligible" and reason is not None:
            names = [
                fam.family,
                *(n for n, _, _ in family_names(fam, rows, INELIGIBLE_NAME_RELATIONS)),
            ]
            ineligible.update((name, reason) for name in names)
        elif status == "unknown" and log is not None:
            log.warning("names.json: %s left out (%s)", fid, reason)
    ineligible.update(
        (row.alias, row.detail)
        for row in inputs.aliases
        if row.relation == "ineligible" and row.ns in NAME_NAMESPACES
    )
    return {
        "schema_version": NAMES_SCHEMA_VERSION,
        "run": {"date": run_date.isoformat(), "method_version": METHOD_VERSION},
        "families": families,
        "ineligible": [
            {"name": name, "reason": reason}
            for name, reason in sorted(ineligible, key=lambda i: (match_key(i[0]), i[0], i[1]))
        ],
    }


def build_names(ctx: StageContext) -> dict[str, Any]:
    """The ``names.json`` document for owned-font matching (Milestone 3)."""
    return names_document(Inputs(ctx.paths), ctx.run_date, ctx.log)


# --- the stages --------------------------------------------------------------------------------


def run(ctx: StageContext) -> None:
    """Stage "export": write ``build/catalog.json``."""
    from tff_catalog.state import write_part

    inputs = Inputs(ctx.paths)
    if inputs.unlinked:
        ctx.log.warning(
            "export: %d catalog members held back until gate K accepts a download link: %s",
            len(inputs.unlinked),
            ", ".join(inputs.unlinked),
        )
    doc = catalog_document(inputs, ctx.config, ctx.state, ctx.run_date, code_commit(ctx.paths.root))
    jsonio.dump(doc, ctx.paths.build / CATALOG_FILE)
    write_part(ctx.paths, "published_ranks", published_ranks(doc), stage="export")
    ctx.log.info(
        "export: %d fonts, %d sources (commit %s)",
        len(doc["fonts"]),
        len(doc["sources"]),
        doc["run"]["code_commit"][:12],
    )


def run_site(ctx: StageContext) -> None:
    """Stage "export-site": write ``build/catalog-site.json`` and ``build/names.json``."""
    path = ctx.paths.build / CATALOG_FILE
    if not path.is_file():
        raise MissingInput(f"{path} is missing: run stage 'export' first")
    previews = load_previews(ctx.paths)
    if previews is None:
        ctx.log.warning("export-site: no build/stage/previews.json; previews stay as they are")
    catalog = with_previews(jsonio.load(path), previews)
    jsonio.dump(catalog, path)
    site = build_site(catalog, ctx)
    jsonio.dump(site, ctx.paths.build / SITE_FILE)
    names = build_names(ctx)
    jsonio.dump(names, ctx.paths.build / NAMES_FILE)
    ctx.log.info(
        "export-site: %d site fonts, %d named families, %d ineligible names",
        len(site["fonts"]),
        len(names["families"]),
        len(names["ineligible"]),
    )
