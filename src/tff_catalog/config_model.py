"""Frozen dataclasses for every config file, and the strict loader. Frozen contract.

``from_mapping(cls, data, where)`` turns parsed TOML into ``cls``:

- an unknown key fails;
- a missing key fails, unless the field has a default (TOML has no null, so
  optional fields are the ones with defaults);
- values are type-checked: ``bool`` is never accepted as a number, an ``int``
  is accepted where a ``float`` is expected (and becomes a float), a float
  must be finite (TOML's ``nan`` and ``inf`` fail), a ``Literal`` accepts only
  its listed values.

Collector ``Settings`` classes use the same loader (``collectors.base.load_settings``).

Files and their owners:
- ``config/ranking.toml`` → ``RankingConfig`` (methodology §5 and §8)
- ``config/licenses.toml`` → ``LicensesConfig`` (D3)
- ``config/license-aliases.toml`` → ``LicenseAliasesConfig`` (L1 normalisation)
- ``config/preinstalled.toml`` → ``PreinstalledConfig`` (D8)
- ``config/foundries.toml`` → ``FoundriesConfig`` (universe)
- ``config/site.toml`` → ``SiteConfig`` (the site wording export-site copies)
- ``config/category-overrides.toml`` → ``CategoryOverridesConfig`` (the owner's
  categories, which stage "facts" puts over every other basis)
- ``config/sources/<collector>.toml`` → kept raw in ``Config.sources``; each
  collector's ``Settings`` types it.
"""

import dataclasses
import math
import tomllib
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1

# Versioned schema constants (methodology §7): every published rank or view.
RANK_KEYS = (
    "overall",
    "desktop_chosen",
    "desktop_installed",
    "project",
    "coding",
    "dev_apps",
    "rising",
)
SURVEYS = ("desktop", "project")
# "app": fonts an application bundles for its own use (LibreOffice's installers). Such an
# entry only tags fonts: no "comes with" bit on the site and never an abstention.
OS_FAMILIES = ("windows", "macos", "linux", "android", "app")


class ConfigError(ValueError):
    """A config file is missing, unreadable or does not match its dataclass."""


# --- strict loader ------------------------------------------------------------


def load_toml(path: Path) -> dict[str, Any]:
    """Parse one TOML file, turning every failure into ``ConfigError``."""
    try:
        with Path(path).open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: missing") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def from_mapping[T](cls: type[T], data: Mapping[str, Any], where: str = "") -> T:
    """Build dataclass ``cls`` from ``data`` strictly (see the module docstring)."""
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")
    if not isinstance(data, Mapping):
        raise ConfigError(f"{where or cls.__name__}: expected a table, got {type(data).__name__}")
    hints = typing.get_type_hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls) if f.init}
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise ConfigError(f"{where or cls.__name__}: unknown key(s) {', '.join(unknown)}")
    kwargs: dict[str, Any] = {}
    for name, f in fields.items():
        at = f"{where}.{name}" if where else name
        if name in data:
            kwargs[name] = _convert(hints[name], data[name], at)
        elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            raise ConfigError(f"{at}: missing key")
    return cls(**kwargs)


def _convert(tp: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if tp is Any or tp is object:
        return value
    if origin is Literal:
        # The type check keeps true from matching a Literal 1 (True == 1).
        if type(value) not in {type(a) for a in args} or value not in args:
            raise ConfigError(f"{where}: {value!r} is not one of {', '.join(map(repr, args))}")
        return value
    if origin in (typing.Union, types.UnionType):
        options = [a for a in args if a is not type(None)]
        errors = []
        for option in options:
            try:
                return _convert(option, value, where)
            except ConfigError as exc:
                errors.append(str(exc))
        raise ConfigError("; ".join(errors))
    if dataclasses.is_dataclass(tp):
        return from_mapping(tp, value, where)
    if origin is tuple:
        if not isinstance(value, list | tuple):
            raise ConfigError(f"{where}: expected an array, got {type(value).__name__}")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_convert(args[0], v, f"{where}[{i}]") for i, v in enumerate(value))
        if len(value) != len(args):
            raise ConfigError(f"{where}: expected {len(args)} items, got {len(value)}")
        return tuple(
            _convert(a, v, f"{where}[{i}]")
            for i, (a, v) in enumerate(zip(args, value, strict=True))
        )
    if origin is dict:
        if not isinstance(value, Mapping):
            raise ConfigError(f"{where}: expected a table, got {type(value).__name__}")
        key_type, value_type = args
        out = {}
        for k in sorted(value):
            _convert(key_type, k, f"{where} key")
            out[k] = _convert(value_type, value[k], f"{where}.{k}")
        return out
    if tp is bool:
        if type(value) is not bool:
            raise ConfigError(f"{where}: expected true or false, got {value!r}")
        return value
    if tp is int:
        if type(value) is not int:
            raise ConfigError(f"{where}: expected an integer, got {value!r}")
        return value
    if tp is float:
        if type(value) not in (int, float):
            raise ConfigError(f"{where}: expected a number, got {value!r}")
        if not math.isfinite(value):
            raise ConfigError(f"{where}: must be a finite number, got {value!r}")
        return float(value)
    if tp is str:
        if type(value) is not str:
            raise ConfigError(f"{where}: expected a string, got {value!r}")
        return value
    if tp is date:
        if type(value) is not date:
            raise ConfigError(f"{where}: expected a date (YYYY-MM-DD), got {value!r}")
        return value
    if tp is datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ConfigError(f"{where}: expected an offset date-time, got {value!r}")
        return value
    raise TypeError(f"{where}: unsupported field type {tp!r}")


# --- config/ranking.toml ------------------------------------------------------

# How a source's values become one number per key: a fixed window total, the mean
# of monthly shares, the change between lifetime-counter snapshots, the mean of
# weekly snapshots, a yearly edition, or a current count.
Counting = Literal[
    "window", "monthly_mean_share", "snapshot_delta", "weekly_mean", "yearly", "current"
]
# Where a channel's exposure ("too new") is counted from: design-m1 gap G3.
Exposure = Literal[
    "add_date",
    "first_nonzero_day",
    "first_nonzero_month",
    "date_added",
    "asset_created",
    "data_date",
    "none",
]
Rate = Literal["per_year", "per_month", "share", "count"]


@dataclass(frozen=True, slots=True)
class Guard:
    """Outlier guard (methodology §4)."""

    gap: float
    min_terms: int
    factor: float
    # What a term is compared with: "median" of all the font's terms (owner ruling of
    # 2026-09-26, guard_basis) or "others_mean", the mean of its other terms (ruling M1).
    basis: Literal["median", "others_mean"]


@dataclass(frozen=True, slots=True)
class Engine:
    kappa: float
    mu0: float
    ruler: str  # engine source used as the ruler (D5)
    alt_ruler: str  # monthly cross-check rerun (D5)
    overlap_full: int
    overlap_off: int
    top100_min_groups: int
    min_observed_terms: int
    github_homebrew_same_group: Literal["cask_asset", "never"]  # gate M5
    guard: Guard


@dataclass(frozen=True, slots=True)
class Corrections:
    min_exposure_days: int
    nerd_credit: float
    cjk_build_credit: float
    bundle_credit: float
    bundle_mode: Literal["fixed", "split"]  # D2
    width_siblings: Literal["separate", "fold"]  # D2
    dependency_abstain: float
    dependency_review: float  # gate M8
    dependency_rule: Literal["top", "sum"]  # gate M8
    dependency_alternatives: Literal["first", "all"]  # gate M8
    # Owner ruling of 2026-09-26 (per_system_basis): a Linux source's family value is its
    # most-installed package ("largest_package"), not the "sum" of its packages.
    per_system_basis: Literal["largest_package", "sum"]
    # Owner ruling of 2026-09-26 (abstain_scope): a preinstalled Linux system silences only
    # the Linux sources that count its installs ("by_package_system", through
    # abstain_sources), or every Linux source ("all", D8 as first written).
    abstain_scope: Literal["by_package_system", "all"]
    abstain_sources: dict[str, tuple[str, ...]]  # Linux system id -> the Linux sources it silences


@dataclass(frozen=True, slots=True)
class SourceBase:
    """An engine source: a collector plus a selector and its handling (methodology §5).

    Engine sources are not collectors: ``npm_fontsource`` and ``npm_expo`` both
    read collector ``npm`` with different scopes.
    """

    collector: str
    group: str  # independence group, records.GROUPS
    survey: Literal["desktop", "project"]
    enabled: bool
    linux: bool  # abstains in every rank except desktop_installed (D8)
    publish_raw: bool  # terms ruling (gate T): raw values may appear in outputs
    series: str  # Observation.series the engine reads
    counting: Counting
    exposure: Exposure
    rate: Rate  # the unit of `floor` and `censor_below`
    floor: float  # subtracted before ranking
    censor_below: float  # values under this (after the floor) are censored


@dataclass(frozen=True, slots=True)
class HomebrewSource(SourceBase):
    nerd_floor: Literal["p10", "flat"]  # gate M3
    nerd_floor_quantile: float


@dataclass(frozen=True, slots=True)
class ArchSource(SourceBase):
    months: int
    nerd_group_floor: Literal["tenured_p10", "flat"]  # gate M4
    nerd_group_floor_quantile: float
    nerd_group_tenure_months: int
    nerd_group_flat_floor: float


@dataclass(frozen=True, slots=True)
class GithubSource(SourceBase):
    release_history: Literal["all", "latest24", "24months"]  # gate M2
    # Repos ("owner/name", lower case) that publish only prereleases, whose prereleases
    # count (owner ruling of 2026-09-26, opendyslexic_prereleases); others' never do.
    prerelease_repos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NerdSource(SourceBase):
    floor_quantile: float
    exclude_assets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FotSource(SourceBase):
    phase_in_weight: float
    phase_in_snapshots: int
    ewma_lambda: float
    startup_cap: float  # D11
    methods: tuple[str, ...]
    dominant_share: float


@dataclass(frozen=True, slots=True)
class AlmanacSource(SourceBase):
    tab: Literal["pages", "service", "mean"]  # gate M6
    parent_merge_factor: float


@dataclass(frozen=True, slots=True)
class GoogleSource(SourceBase):
    fallback: str  # "<collector>:<series>" read when the main series is missing


@dataclass(frozen=True, slots=True)
class NpmSource(SourceBase):
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EcosystemsSource(SourceBase):
    scopes: tuple[str, ...]  # gate M7
    stale_sync_days: int


@dataclass(frozen=True, slots=True)
class Sources:
    homebrew: HomebrewSource
    arch: ArchSource
    github: GithubSource
    nerd: NerdSource
    debian: SourceBase  # no chocolatey: gate T3 (a) dropped it from v1
    fot: FotSource
    almanac: AlmanacSource
    google: GoogleSource
    npm_fontsource: NpmSource
    ecosystems: EcosystemsSource
    jsdelivr: SourceBase
    npm_expo: NpmSource
    flutter: SourceBase

    def all(self) -> dict[str, SourceBase]:
        """Every engine source by name, in declaration order."""
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}


@dataclass(frozen=True, slots=True)
class Survey:
    weights: dict[str, float]  # engine source -> nominal weight w


@dataclass(frozen=True, slots=True)
class Surveys:
    desktop: Survey
    project: Survey


@dataclass(frozen=True, slots=True)
class ProjectGroup:
    """A project group (D10) and its fixed share of the project weight (gate M9 (b)).

    Within the group each source gets ``share * w_eff / sum(w_eff of the group)``
    (``surveys.effective_weights``).
    """

    share: float
    sources: tuple[str, ...]  # engine sources, each in exactly one group


@dataclass(frozen=True, slots=True)
class SurveyRank:
    survey: Literal["desktop", "project"]
    abstain: bool


@dataclass(frozen=True, slots=True)
class OverallRank:
    mix: dict[str, float]  # rank key -> share of the overall mix (D12)


@dataclass(frozen=True, slots=True)
class WeightedView:
    weights: dict[str, float]
    abstain: bool
    monospace_only: bool


@dataclass(frozen=True, slots=True)
class RisingView:
    min_share: float
    min_sources: int
    smoothing_months: int
    new_days: int
    min_history_months: int
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Ranks:
    overall: OverallRank
    desktop_chosen: SurveyRank
    desktop_installed: SurveyRank
    project: SurveyRank
    coding: WeightedView
    dev_apps: WeightedView
    rising: RisingView


@dataclass(frozen=True, slots=True)
class Display:
    exact_top: int
    bands: tuple[tuple[int, int], ...]
    open_band_from: int
    categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Top100Hysteresis:
    enabled: bool  # gate M11
    enter: int
    leave: int
    leave_runs: int


@dataclass(frozen=True, slots=True)
class Membership:
    catalog_size: int
    enter: int
    leave: int
    leave_runs: int
    extra_top: int
    extra_ranks: tuple[str, ...]
    top100: Top100Hysteresis
    l3_overall_max: int  # stage "verify" (L3) checks overall order this or better ...
    l3_extra_top: int  # ... and the top this many of each extra rank


@dataclass(frozen=True, slots=True)
class Uncertainty:
    runs: int
    concentration: float
    quantiles: tuple[float, float]
    leave_one_out: bool
    seed: int


@dataclass(frozen=True, slots=True)
class Tiers:
    a_min_groups: int
    a_min_width: int
    a_rank_share: float
    b_rank_share: float


@dataclass(frozen=True, slots=True)
class Stale:
    max_months: int


@dataclass(frozen=True, slots=True)
class Crosscheck:
    rrf_k: int
    move_flag: float


@dataclass(frozen=True, slots=True)
class Review:
    rbo_p: float
    rbo_min: float
    spearman_min: float
    coverage_change_max: float
    share_jump_max: float
    top100_from_below: int
    disagreement_top: int
    disagreement_other: int
    what_if_factors: tuple[float, ...]
    move_places: int  # owner ruling 2026-09-26 (review_report): a monthly move flag needs more places than this
    first_run_move_places: int


@dataclass(frozen=True, slots=True)
class Latin:
    kernel_missing_max: int  # gate L1
    core_missing_max: int  # gate L1: GF_Latin_Core code points of any kind missing for "extended"
    latin_share_min: float  # gate L1
    cjk_codepoints_below: int


@dataclass(frozen=True, slots=True)
class Universe:
    foundry_families: Literal["hand", "scrape"]  # gate M12


@dataclass(frozen=True, slots=True)
class RankingConfig:
    schema: int
    engine: Engine
    corrections: Corrections
    sources: Sources
    surveys: Surveys
    project_group_shares: dict[str, ProjectGroup]  # gate M9 (b): web, code, apps
    ranks: Ranks
    display: Display
    membership: Membership
    uncertainty: Uncertainty
    tiers: Tiers
    stale: Stale
    crosscheck: Crosscheck
    review: Review
    latin: Latin
    universe: Universe


# --- config/licenses.toml -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllowedLicense:
    name: str  # display name ("SIL Open Font License 1.1")
    group: str  # license-class filter group on the site
    redistributable: bool
    attribution_required: bool
    note: str = ""


@dataclass(frozen=True, slots=True)
class ExcludedLicense:
    reason: str  # D3 or AUTHORITY rule 4


@dataclass(frozen=True, slots=True)
class RulingLicense:
    question: str  # what the owner must rule on; answers go in gate LIC (data/reviews/licenses/)
    name: str = ""  # display name ("Ubuntu Font Licence 1.0"); without one, the id shows


@dataclass(frozen=True, slots=True)
class LicensesConfig:
    """SPDX ids (or LicenseRef-*) by class. Anything not listed is excluded (D3)."""

    schema: int
    allowed: dict[str, AllowedLicense]
    excluded: dict[str, ExcludedLicense]
    ruling: dict[str, RulingLicense]


# --- config/license-aliases.toml ----------------------------------------------


@dataclass(frozen=True, slots=True)
class LicenseAliasesConfig:
    """L1: a raw license string, exactly as a source writes it, to an SPDX expression."""

    schema: int
    aliases: dict[str, str]


# --- config/preinstalled.toml -------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreinstalledSystem:
    label: str  # "Ubuntu 24.04 desktop"
    os: Literal["windows", "macos", "linux", "android", "app"]  # only linux entries abstain (D8)
    families: tuple[str, ...]  # family names as the system ships them
    source: str  # where the list comes from (https URL)
    note: str = ""


@dataclass(frozen=True, slots=True)
class PreinstalledConfig:
    schema: int
    systems: dict[str, PreinstalledSystem]  # system id -> entry


# --- config/foundries.toml ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class FoundryFamily:
    name: str
    url: str  # the family's page at the foundry (https)
    license: str  # as the foundry states it; L1 normalises it
    repository: str = ""
    # One Regular font file (https, pinned to a commit), so the Latin gate and L3 can read
    # the family: the list itself carries no files otherwise.
    files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Foundry:
    name: str
    url: str
    families: tuple[FoundryFamily, ...]


@dataclass(frozen=True, slots=True)
class FoundriesConfig:
    schema: int
    foundries: dict[str, Foundry]  # foundry id -> entry


# --- config/site.toml ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DataLicense:
    spdx: Literal["CC-BY-SA-4.0"]  # D17
    provisional: bool  # gate T5 (a): false, the license is final
    url: str


@dataclass(frozen=True, slots=True)
class ViewText:
    key: str  # a RANK_KEYS entry
    label: str
    measures: str  # one line under the rank selector
    # A view the owner retired: still listed, since catalog-site.json v1 names every rank
    # key, but published with available false, so the site never offers it (Overall, owner
    # ruling of 2026-09-30, overall_retired). ``config.check_site`` keeps it off the default.
    retired: bool = False


@dataclass(frozen=True, slots=True)
class TierTexts:
    A: str
    B: str
    C: str


@dataclass(frozen=True, slots=True)
class LicenseClassText:
    id: str  # licenses.toml allowed[*].group
    label: str


@dataclass(frozen=True, slots=True)
class PackageSystem:
    label: str  # a Linux distribution whose packages can pull a font in


@dataclass(frozen=True, slots=True)
class SourceCredit:
    name: str
    measures: str
    url: str  # https
    license: str  # the credit line: the source's data license or terms, as the site shows it
    publish_rank: bool  # gate T: the page may show each font's rank in this source


@dataclass(frozen=True, slots=True)
class NerdText:
    """The Nerd Font marker's wording (owner ruling of 2026-09-29, TASK-2)."""

    marker: str  # the text beside a font's name: "NF"
    label: str  # the marker's accessible name: "Nerd Font version available"
    legend: str  # the site's legend: "<marker>: <label> (...)."


@dataclass(frozen=True, slots=True)
class SiteConfig:
    """Wording for ``catalog-site.json``; owner rulings on wording live here."""

    schema: int
    data_license: DataLicense
    views: tuple[ViewText, ...]  # rank selector order, default first; every RANK_KEYS entry once
    tiers: TierTexts
    license_classes: tuple[LicenseClassText, ...]  # filter order
    nerd: NerdText
    package_systems: dict[str, PackageSystem]  # pulled_in_by system id -> label
    sources: dict[str, SourceCredit]  # engine source id -> credit


# --- config/category-overrides.toml -------------------------------------------

CATEGORY_OVERRIDES_FILE = "category-overrides.toml"


@dataclass(frozen=True, slots=True)
class CategoryOverridesConfig:
    """The owner's categories (owner ruling of 2026-09-29, gate R round 1, categories_22).

    Stage "facts" gives each listed family this category, with basis "owner", over
    every other basis; "monospace" also makes the family monospaced.
    ``config.check_category_overrides`` checks that each key is a family id and each
    value a ``facts.CATEGORIES`` word; stage "facts" fails on an id its universe lacks.
    """

    schema: int
    families: dict[str, str]  # family id (state/ids.json) -> category


# --- config/nerd-hidden.toml ----------------------------------------------------

NERD_HIDDEN_FILE = "nerd-hidden.toml"


@dataclass(frozen=True, slots=True)
class NerdHiddenConfig:
    """Families whose Nerd Font build the owner hides (owner ruling of 2026-09-29, TASK-2).

    Stage "links" gives a listed family no ``links.nerd``, so the site shows no NF
    marker, link or filter match for it, whatever build it has.
    ``config.check_nerd_hidden`` checks that each key is a family id and each reason is
    given; ``--strict`` checks the ids against the registry, and stage "links" fails on an
    id its universe lacks.
    """

    schema: int
    families: dict[str, str]  # family id (state/ids.json) -> why its build is hidden


# --- everything ---------------------------------------------------------------


def _no_category_overrides() -> CategoryOverridesConfig:
    return CategoryOverridesConfig(schema=SCHEMA_VERSION, families={})


def _no_nerd_hidden() -> NerdHiddenConfig:
    return NerdHiddenConfig(schema=SCHEMA_VERSION, families={})


@dataclass(frozen=True, slots=True)
class Config:
    """The effective configuration of one run; ``config.config_hash`` hashes it.

    ``load_config`` fills every field from its file. ``category_overrides`` and
    ``nerd_hidden`` have defaults (none) only so that a test can build a ``Config``
    without them.
    """

    ranking: RankingConfig
    licenses: LicensesConfig
    license_aliases: LicenseAliasesConfig
    preinstalled: PreinstalledConfig
    foundries: FoundriesConfig
    site: SiteConfig
    sources: dict[str, dict[str, Any]]  # collector -> raw config/sources/<collector>.toml
    category_overrides: CategoryOverridesConfig = dataclasses.field(
        default_factory=_no_category_overrides
    )
    nerd_hidden: NerdHiddenConfig = dataclasses.field(default_factory=_no_nerd_hidden)


# File name -> (Config field, dataclass).
CONFIG_FILES: dict[str, tuple[str, type]] = {
    "ranking.toml": ("ranking", RankingConfig),
    "licenses.toml": ("licenses", LicensesConfig),
    "license-aliases.toml": ("license_aliases", LicenseAliasesConfig),
    "preinstalled.toml": ("preinstalled", PreinstalledConfig),
    "foundries.toml": ("foundries", FoundriesConfig),
    "site.toml": ("site", SiteConfig),
    CATEGORY_OVERRIDES_FILE: ("category_overrides", CategoryOverridesConfig),
    NERD_HIDDEN_FILE: ("nerd_hidden", NerdHiddenConfig),
}
