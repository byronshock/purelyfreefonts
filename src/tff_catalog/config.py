"""Load, check and hash the configuration (milestone-1 step 2).

``load_config(paths)`` reads every file in ``config_model.CONFIG_FILES`` strictly
(unknown or missing keys fail), plus the raw ``config/sources/*.toml``, then
runs the cross-checks in ``check_ranking`` and friends. Any problem raises
``ConfigError`` naming the file and key.

``check_collectors(cfg, paths)`` (``tff-catalog config --strict``) adds the
checks that need the collectors themselves: every ``config/sources/<name>.toml``
belongs to a discovered collector and loads into its ``Settings``, and every
enabled engine source reads a discovered ranking collector. It is separate
because it imports every collector module; CI runs it once the collectors exist.

``config_hash(cfg)`` is the sha256 of the canonical JSON of the effective
config, so two runs with the same values give the same hash whatever the
file's layout or comments. ``ranking_hash(cfg)`` hashes ``ranking.toml`` alone;
the catalog records it as ``ranking_toml_sha256``.
"""

import argparse
import dataclasses
import hashlib
import math
import re
import sys
from typing import Any

from tff_catalog import jsonio
from tff_catalog.config_model import (
    CONFIG_FILES,
    RANK_KEYS,
    SCHEMA_VERSION,
    Config,
    ConfigError,
    FoundriesConfig,
    LicensesConfig,
    PreinstalledConfig,
    RankingConfig,
    SiteConfig,
    from_mapping,
    load_toml,
)
from tff_catalog.paths import Paths
from tff_catalog.records import GROUPS

_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_HTTPS = re.compile(r"^https://\S+$")


def load_config(paths: Paths) -> Config:
    """Load and check every config file under ``paths.config``."""
    parts: dict[str, Any] = {}
    for filename, (field, cls) in CONFIG_FILES.items():
        path = paths.config / filename
        parts[field] = from_mapping(cls, load_toml(path), where=filename)
        if parts[field].schema != SCHEMA_VERSION:
            raise ConfigError(
                f"{filename}: schema {parts[field].schema}, expected {SCHEMA_VERSION}"
            )
    sources: dict[str, dict[str, Any]] = {}
    if paths.sources_config.is_dir():
        for path in sorted(paths.sources_config.glob("*.toml")):
            sources[path.stem] = load_toml(path)
    cfg = Config(**parts, sources=sources)
    check_ranking(cfg.ranking)
    check_licenses(cfg.licenses)
    check_preinstalled(cfg.preinstalled)
    check_foundries(cfg.foundries)
    check_site(cfg)
    return cfg


def effective(cfg: Config) -> dict[str, Any]:
    """Return the effective config as plain JSON-ready data."""
    return dataclasses.asdict(cfg)


def _sha256(obj: object) -> str:
    return hashlib.sha256(jsonio.canonical_bytes(obj)).hexdigest()


def config_hash(cfg: Config) -> str:
    """sha256 (hex) of the canonical JSON of the whole effective config."""
    return _sha256(effective(cfg))


def ranking_hash(cfg: Config) -> str:
    """sha256 (hex) of the canonical JSON of ``ranking.toml``'s effective values."""
    return _sha256(dataclasses.asdict(cfg.ranking))


# --- cross-checks -------------------------------------------------------------


def _fail(where: str, message: str) -> None:
    raise ConfigError(f"{where}: {message}")


def _check_weights(where: str, weights: dict[str, float], known: dict[str, Any]) -> None:
    for name, weight in weights.items():
        if name not in known:
            _fail(where, f"unknown engine source {name!r}")
        if not math.isfinite(weight) or weight < 0:
            _fail(f"{where}.{name}", "weights must be finite and not negative")


def check_ranking(r: RankingConfig) -> None:
    """Cross-field checks for ``ranking.toml``; raises ``ConfigError``."""
    sources = r.sources.all()
    for name, src in sources.items():
        where = f"ranking.toml: sources.{name}"
        if src.group not in GROUPS:
            _fail(where, f"group {src.group!r} is not one of {sorted(GROUPS)}")
        if not _TOKEN.match(name):
            _fail(where, "engine source names must be lower-case tokens")
        if src.floor < 0 or src.censor_below < 0:
            _fail(where, "floor and censor_below must not be negative")
    for survey_name in ("desktop", "project"):
        survey = getattr(r.surveys, survey_name)
        where = f"ranking.toml: surveys.{survey_name}.weights"
        _check_weights(where, survey.weights, sources)
        for name in survey.weights:
            if sources[name].survey != survey_name:
                _fail(where, f"{name} belongs to survey {sources[name].survey!r}")
    # Each raw source feeds exactly one survey (methodology §1).
    for name, src in sources.items():
        if name not in getattr(r.surveys, src.survey).weights:
            _fail(f"ranking.toml: sources.{name}", f"missing from surveys.{src.survey}.weights")
    _check_project_groups(r, sources)

    e = r.engine
    for key in ("ruler", "alt_ruler"):
        if getattr(e, key) not in sources:
            _fail(f"ranking.toml: engine.{key}", f"unknown engine source {getattr(e, key)!r}")
    if not 0 < e.overlap_off <= e.overlap_full:
        _fail("ranking.toml: engine", "need 0 < overlap_off <= overlap_full")
    if e.kappa < 0 or not 0 <= e.guard.factor <= 1 or e.guard.gap <= 0:
        _fail("ranking.toml: engine", "kappa >= 0, 0 <= guard.factor <= 1 and guard.gap > 0")

    c = r.corrections
    for key in ("nerd_credit", "cjk_build_credit", "bundle_credit"):
        if not 0 <= getattr(c, key) <= 1:
            _fail(f"ranking.toml: corrections.{key}", "must be between 0 and 1")
    if not 0 <= c.dependency_review <= c.dependency_abstain <= 1:
        _fail("ranking.toml: corrections", "need 0 <= dependency_review <= dependency_abstain <= 1")

    mix = r.ranks.overall.mix
    for key in mix:
        if key not in ("desktop_chosen", "desktop_installed", "project"):
            _fail("ranking.toml: ranks.overall.mix", f"{key!r} cannot feed the overall rank")
    if not math.isclose(sum(mix.values()), 1.0, abs_tol=1e-9):
        _fail("ranking.toml: ranks.overall.mix", "shares must sum to 1")
    _check_weights("ranking.toml: ranks.coding.weights", r.ranks.coding.weights, sources)
    _check_weights("ranking.toml: ranks.dev_apps.weights", r.ranks.dev_apps.weights, sources)
    for name in r.ranks.rising.sources:
        if name not in sources:
            _fail("ranking.toml: ranks.rising.sources", f"unknown engine source {name!r}")

    d = r.display
    start = d.exact_top + 1
    for lo, hi in d.bands:
        if lo != start or hi < lo:
            _fail("ranking.toml: display.bands", "bands must be contiguous from exact_top + 1")
        start = hi + 1
    if d.open_band_from != start:
        _fail("ranking.toml: display.open_band_from", f"expected {start}")

    m = r.membership
    if not m.enter <= m.catalog_size <= m.leave or m.leave_runs < 1:
        _fail("ranking.toml: membership", "need enter <= catalog_size <= leave and leave_runs >= 1")
    for key in m.extra_ranks:
        if key not in RANK_KEYS:
            _fail("ranking.toml: membership.extra_ranks", f"unknown rank key {key!r}")
    if m.l3_overall_max < m.leave or m.l3_extra_top < m.extra_top:
        _fail(
            "ranking.toml: membership",
            "need l3_overall_max >= leave and l3_extra_top >= extra_top (L3 checks every "
            "font that can enter)",
        )
    t = m.top100
    if not t.enter <= d.exact_top <= t.leave or t.leave_runs < 1:
        _fail("ranking.toml: membership.top100", "need enter <= exact_top <= leave")

    u = r.uncertainty
    lo, hi = u.quantiles
    if not 0 < lo < hi < 1 or u.runs < 1 or u.concentration <= 0:
        _fail("ranking.toml: uncertainty", "need 0 < q_lo < q_hi < 1, runs >= 1, concentration > 0")


def _check_project_groups(r: RankingConfig, sources: dict[str, Any]) -> None:
    """Gate M9 (b): fixed group shares that sum to 1, each project source in one group."""
    where = "ranking.toml: project_group_shares"
    groups = r.project_group_shares
    if not groups:
        _fail(where, "needs at least one group")
    placed: dict[str, str] = {}
    for name, group in groups.items():
        if not _TOKEN.match(name):
            _fail(f"{where}.{name}", "group names must be lower-case tokens")
        if not 0 < group.share <= 1:
            _fail(f"{where}.{name}.share", "must be more than 0 and at most 1")
        for source in group.sources:
            if source not in sources:
                _fail(f"{where}.{name}.sources", f"unknown engine source {source!r}")
            if sources[source].survey != "project":
                _fail(f"{where}.{name}.sources", f"{source} is not a project source")
            if source in placed:
                _fail(f"{where}.{name}.sources", f"{source} is also in group {placed[source]!r}")
            placed[source] = name
    if not math.isclose(sum(g.share for g in groups.values()), 1.0, abs_tol=1e-9):
        _fail(where, "shares must sum to 1")
    loose = sorted(n for n, s in sources.items() if s.survey == "project" and n not in placed)
    if loose:
        _fail(where, f"project sources in no group: {', '.join(loose)}")


def check_collectors(cfg: Config, paths: Paths) -> None:
    """The ``--strict`` checks (module docstring); raises ``ConfigError``."""
    from tff_catalog.collectors import discover
    from tff_catalog.collectors.base import load_settings

    collectors = discover()
    for name in sorted(cfg.sources):
        if name not in collectors:
            _fail(f"sources/{name}.toml", "no collector of that name")
    for collector in collectors.values():
        load_settings(collector, paths)
    for name, src in cfg.ranking.sources.all().items():
        if not src.enabled:
            continue
        where = f"ranking.toml: sources.{name}.collector"
        if src.collector not in collectors:
            _fail(where, f"no collector {src.collector!r}")
        if collectors[src.collector].kind != "ranking":
            _fail(where, f"{src.collector!r} is not a ranking collector")


def check_licenses(lic: LicensesConfig) -> None:
    """No license id may sit in two classes."""
    seen: dict[str, str] = {}
    for klass in ("allowed", "excluded", "ruling"):
        for spdx in getattr(lic, klass):
            if spdx in seen:
                _fail(f"licenses.toml: {klass}.{spdx}", f"also listed under {seen[spdx]}")
            seen[spdx] = klass
    for spdx, entry in lic.allowed.items():
        if not _TOKEN.match(entry.group):
            _fail(f"licenses.toml: allowed.{spdx}.group", "must be a lower-case token")


def check_preinstalled(pre: PreinstalledConfig) -> None:
    """System ids are tokens and sources are https URLs."""
    for system, entry in pre.systems.items():
        if not _TOKEN.match(system):
            _fail(f"preinstalled.toml: systems.{system}", "system ids must be lower-case tokens")
        if not _HTTPS.match(entry.source):
            _fail(f"preinstalled.toml: systems.{system}.source", "must be an https URL")


def check_foundries(f: FoundriesConfig) -> None:
    """Foundry ids are tokens and every URL is https."""
    for foundry_id, foundry in f.foundries.items():
        where = f"foundries.toml: foundries.{foundry_id}"
        if not _TOKEN.match(foundry_id):
            _fail(where, "foundry ids must be lower-case tokens")
        urls = [foundry.url] + [fam.url for fam in foundry.families]
        urls += [fam.repository for fam in foundry.families if fam.repository]
        for url in urls:
            if not _HTTPS.match(url):
                _fail(where, f"{url!r} is not an https URL")


def check_site(cfg: Config) -> None:
    """``site.toml`` against the other files: views, sources, classes and systems line up."""
    site: SiteConfig = cfg.site
    if site.data_license.provisional:
        _fail("site.toml: data_license.provisional", "gate T5 (a) made the data license final")
    if not _HTTPS.match(site.data_license.url):
        _fail("site.toml: data_license.url", "must be an https URL")
    keys = [v.key for v in site.views]
    if sorted(keys) != sorted(RANK_KEYS):
        _fail("site.toml: views", f"need every rank key once: {', '.join(RANK_KEYS)}")
    classes = [c.id for c in site.license_classes]
    for klass in classes:
        if not _TOKEN.match(klass) or classes.count(klass) > 1:
            _fail("site.toml: license_classes", f"{klass!r} must be a token, listed once")
    for spdx, entry in cfg.licenses.allowed.items():
        if entry.group not in classes:
            _fail(f"licenses.toml: allowed.{spdx}.group", f"{entry.group!r} is not in site.toml")
    for system, entry in site.package_systems.items():
        if not _TOKEN.match(system):
            _fail(f"site.toml: package_systems.{system}", "system ids must be lower-case tokens")
        other = cfg.preinstalled.systems.get(system)
        if other is not None and other.label != entry.label:
            _fail(f"site.toml: package_systems.{system}", "label differs from preinstalled.toml")
    engine = cfg.ranking.sources.all()
    for name, credit in site.sources.items():
        if name not in engine:
            _fail(f"site.toml: sources.{name}", "not an engine source in ranking.toml")
        if not _HTTPS.match(credit.url):
            _fail(f"site.toml: sources.{name}.url", "must be an https URL")
    missing = sorted(n for n, s in engine.items() if s.enabled and n not in site.sources)
    if missing:
        _fail("site.toml: sources", f"no credit for enabled sources {', '.join(missing)}")


# --- CLI ------------------------------------------------------------------------


def cmd_config(args: argparse.Namespace) -> int:
    """``tff-catalog config [--strict]``: print the effective config, then ``sha256:<hash>`` last."""
    try:
        paths = Paths.from_env()
        cfg = load_config(paths)
        if getattr(args, "strict", False):
            check_collectors(cfg, paths)
    except ConfigError as exc:
        print(f"tff-catalog config: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(jsonio.pretty_bytes(effective(cfg)).decode("utf-8"))
    print(f"sha256:{config_hash(cfg)}")
    return 0
