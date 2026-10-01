"""The strict config loader and the config hash (milestone-1 step 2).

Each test copies ``config/`` into a temporary root, changes one thing by
parsing a file and writing it back out with ``dump_toml``, and loads it.
"""

import json
import re
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT

from tff_catalog import collectors
from tff_catalog.collectors import discover
from tff_catalog.collectors.base import load_settings
from tff_catalog.config import (
    check_category_ids,
    check_collectors,
    config_hash,
    load_config,
    ranking_hash,
)
from tff_catalog.config_model import CONFIG_FILES, Config, ConfigError
from tff_catalog.paths import Paths
from tff_catalog.records import GROUPS, NAMESPACES

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


# --- a small TOML writer, enough to rewrite our config files ------------------------------------


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else json.dumps(k, ensure_ascii=False)


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + "}"
    raise TypeError(f"cannot write {v!r} as TOML")


def dump_toml(data: dict[str, Any], *, reverse: bool = False) -> str:
    """TOML for ``data``, with no comments; ``reverse`` writes every table's keys backwards."""
    lines: list[str] = []

    def table(prefix: str, t: dict[str, Any]) -> None:
        order = (lambda xs: list(reversed(xs))) if reverse else list
        scalars = order([(k, v) for k, v in t.items() if not isinstance(v, dict)])
        tables = order([(k, v) for k, v in t.items() if isinstance(v, dict)])
        if prefix:
            lines.append(f"\n[{prefix}]")
        lines.extend(f"{_key(k)} = {_value(v)}" for k, v in scalars)
        for k, v in tables:
            table(f"{prefix}.{_key(k)}" if prefix else _key(k), v)

    table("", data)
    return "\n".join(lines).lstrip("\n") + "\n"


# --- fixtures ---------------------------------------------------------------------------------


@dataclass
class ConfigCopy:
    root: Path

    @property
    def paths(self) -> Paths:
        return Paths.for_root(self.root)

    @property
    def config(self) -> Path:
        return self.root / "config"

    def read(self, filename: str) -> dict[str, Any]:
        return tomllib.loads((self.config / filename).read_text(encoding="utf-8"))

    def write(self, filename: str, data: dict[str, Any], *, reverse: bool = False) -> None:
        path = self.config / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_toml(data, reverse=reverse), encoding="utf-8")

    def change(self, filename: str, edit: Callable[[dict[str, Any]], object]) -> None:
        data = self.read(filename)
        edit(data)
        self.write(filename, data)

    def load(self) -> Config:
        return load_config(self.paths)


@pytest.fixture
def cfg(tmp_path: Path) -> ConfigCopy:
    shutil.copytree(ROOT / "config", tmp_path / "config")
    return ConfigCopy(tmp_path)


# --- the committed config -------------------------------------------------------------------


def test_repo_config_loads() -> None:
    cfg = load_config(Paths.for_root(ROOT))
    assert re.fullmatch(r"[0-9a-f]{64}", config_hash(cfg))
    assert re.fullmatch(r"[0-9a-f]{64}", ranking_hash(cfg))


def test_writer_round_trips_every_config_file(cfg: ConfigCopy) -> None:
    for filename in CONFIG_FILES:
        data = cfg.read(filename)
        assert tomllib.loads(dump_toml(data)) == data
        assert tomllib.loads(dump_toml(data, reverse=True)) == data


# --- the hash -----------------------------------------------------------------------------


def test_hash_is_stable_across_loads(cfg: ConfigCopy) -> None:
    first, second = cfg.load(), cfg.load()
    assert first == second
    assert config_hash(first) == config_hash(second)
    assert ranking_hash(first) == ranking_hash(second)
    assert config_hash(first) == config_hash(load_config(Paths.for_root(ROOT)))


def test_hash_ignores_key_order_comments_and_layout(cfg: ConfigCopy) -> None:
    before = cfg.load()
    for filename in CONFIG_FILES:
        cfg.write(filename, cfg.read(filename), reverse=True)
    assert "#" not in (cfg.config / "ranking.toml").read_text(encoding="utf-8")
    after = cfg.load()
    assert config_hash(after) == config_hash(before)
    assert ranking_hash(after) == ranking_hash(before)


def test_hash_follows_values(cfg: ConfigCopy) -> None:
    base = cfg.load()
    cfg.change("ranking.toml", lambda d: d["engine"].update(kappa=d["engine"]["kappa"] + 0.1))
    changed = cfg.load()
    assert config_hash(changed) != config_hash(base)
    assert ranking_hash(changed) != ranking_hash(base)


def test_source_settings_change_config_hash_only(cfg: ConfigCopy) -> None:
    base = cfg.load()
    cfg.write("sources/synth_source.toml", {"enabled": True, "series": "365d"})
    changed = cfg.load()
    assert changed.sources["synth_source"] == {"enabled": True, "series": "365d"}
    assert config_hash(changed) != config_hash(base)
    assert ranking_hash(changed) == ranking_hash(base)


# --- strictness ---------------------------------------------------------------------------


def _set(*path: str, value: object) -> Callable[[dict[str, Any]], None]:
    def edit(d: dict[str, Any]) -> None:
        for part in path[:-1]:
            d = d[part]
        d[path[-1]] = value

    return edit


def _drop(*path: str) -> Callable[[dict[str, Any]], None]:
    def edit(d: dict[str, Any]) -> None:
        for part in path[:-1]:
            d = d[part]
        del d[path[-1]]

    return edit


UNKNOWN_KEYS = {
    "top-level": ("ranking.toml", _set("bogus", value=1), "bogus"),
    "engine": ("ranking.toml", _set("engine", "kapa", value=0.4), "kapa"),
    "nested-guard": ("ranking.toml", _set("engine", "guard", "gapp", value=1.5), "gapp"),
    "source-table": ("ranking.toml", _set("sources", "homebrew", "flor", value=0.0), "flor"),
    "rank-table": ("ranking.toml", _set("ranks", "rising", "min_shares", value=0.1), "min_shares"),
    "licenses": ("licenses.toml", _set("allowd", value={}), "allowd"),
    "preinstalled": ("preinstalled.toml", _set("sytems", value={}), "sytems"),
    "category-overrides": ("category-overrides.toml", _set("familes", value={}), "familes"),
}


@pytest.mark.parametrize("case", sorted(UNKNOWN_KEYS))
def test_unknown_key_fails(cfg: ConfigCopy, case: str) -> None:
    filename, edit, key = UNKNOWN_KEYS[case]
    cfg.change(filename, edit)
    with pytest.raises(ConfigError, match=rf"{re.escape(filename)}.*unknown key.*{key}"):
        cfg.load()


MISSING_KEYS = {
    "engine-kappa": (_drop("engine", "kappa"), "engine.kappa"),
    "guard-factor": (_drop("engine", "guard", "factor"), "engine.guard.factor"),
    "whole-table": (_drop("display"), "display"),
    "source-series": (_drop("sources", "homebrew", "series"), "sources.homebrew.series"),
    "schema": (_drop("schema"), "schema"),
}


@pytest.mark.parametrize("case", sorted(MISSING_KEYS))
def test_missing_key_fails(cfg: ConfigCopy, case: str) -> None:
    edit, where = MISSING_KEYS[case]
    cfg.change("ranking.toml", edit)
    with pytest.raises(ConfigError, match=rf"ranking\.toml\.{re.escape(where)}: missing key"):
        cfg.load()


WRONG_VALUES = {
    "bool-as-number": (_set("engine", "kappa", value=True), "expected a number"),
    "string-as-number": (_set("engine", "kappa", value="0.4"), "expected a number"),
    "float-as-int": (_set("engine", "overlap_full", value=50.5), "expected an integer"),
    "not-a-literal": (_set("engine", "github_homebrew_same_group", value="maybe"), "not one of"),
    "unknown-group": (_set("sources", "homebrew", "group", value="nope"), "group"),
    "mix-not-one": (_set("ranks", "overall", "mix", "project", value=0.6), "sum to 1"),
    "nan": (_set("engine", "kappa", value=float("nan")), "finite"),
    "inf": (_set("engine", "guard", "gap", value=float("inf")), "finite"),
    "group-shares-not-one": (
        _set("project_group_shares", "web", "share", value=0.6),
        "shares must sum to 1",
    ),
    "project-source-in-no-group": (
        _set("project_group_shares", "apps", "sources", value=["npm_expo"]),
        "in no group: flutter",
    ),
    "source-in-two-groups": (
        _set("project_group_shares", "apps", "sources", value=["npm_expo", "flutter", "fot"]),
        "also in group",
    ),
    "desktop-source-in-a-group": (
        _set("project_group_shares", "apps", "sources", value=["npm_expo", "flutter", "homebrew"]),
        "not a project source",
    ),
}


@pytest.mark.parametrize("case", sorted(WRONG_VALUES))
def test_wrong_value_fails(cfg: ConfigCopy, case: str) -> None:
    edit, message = WRONG_VALUES[case]
    cfg.change("ranking.toml", edit)
    with pytest.raises(ConfigError, match=re.escape(message)):
        cfg.load()


SITE_BREAKS = {
    "provisional-license": (_set("data_license", "provisional", value=True), "final"),
    "view-missing": (lambda d: d["views"].pop(), "every rank key once"),
    # The list opens on the first view (M2-D1): never a retired one, nor Rising.
    "retired-view-first": (
        lambda d: d["views"].insert(0, d["views"].pop(-1)),
        "the first view is the default (M2-D1): not 'overall'",
    ),
    "rising-first": (
        lambda d: d["views"].insert(0, d["views"].pop(-2)),
        "the first view is the default (M2-D1): not 'rising'",
    ),
    "retired-not-a-bool": (
        lambda d: d["views"][-1].update(retired="yes"),
        "views[6].retired: expected true or false",
    ),
    "credit-for-unknown-source": (
        lambda d: d["sources"].update(chocolatey=d["sources"]["homebrew"]),
        "sources.chocolatey: not an engine source",
    ),
    "credit-missing": (_drop("sources", "almanac"), "no credit for enabled sources almanac"),
    "class-twice": (
        lambda d: d["license_classes"].append(d["license_classes"][0]),
        "listed once",
    ),
    "http-url": (_set("sources", "google", "url", value="http://fonts.google.com"), "https"),
    "nerd-legend-without-its-marker": (
        _set("nerd", "legend", value="Nerd Font version available."),
        "must start with '<marker>: <label>'",
    ),
    "nerd-marker-empty": (_set("nerd", "marker", value=" "), "must not be empty"),
}


@pytest.mark.parametrize("case", sorted(SITE_BREAKS))
def test_site_toml_cross_checks(cfg: ConfigCopy, case: str) -> None:
    edit, message = SITE_BREAKS[case]
    cfg.change("site.toml", edit)
    with pytest.raises(ConfigError, match=re.escape(message)):
        cfg.load()


def test_views_follow_the_rulings_of_2026_09_30() -> None:
    """default_rank_project and overall_retired: the list opens on Used in projects, and
    Overall stays listed (catalog-site.json v1 names every rank key) but retired."""
    reviews = ROOT / "data" / "reviews"
    site = tomllib.loads((reviews / "site" / "2026-09-30.toml").read_text(encoding="utf-8"))
    method = tomllib.loads((reviews / "method" / "2026-09-30.toml").read_text(encoding="utf-8"))
    assert site["default_rank_project"]["value"] == "used_in_projects"
    assert method["overall_retired"]["value"] == "no_fused_overall_projects_rederived"
    views = load_config(Paths.for_root(ROOT)).site.views
    assert (views[0].key, views[0].label) == ("project", "Used in projects")
    assert [v.key for v in views if v.retired] == ["overall"]


def test_project_group_shares_follow_ruling_m9() -> None:
    """Gate M9 (b), owner ruling 2026-09-25: web 55%, code 30%, apps 15%, fixed."""
    groups = load_config(Paths.for_root(ROOT)).ranking.project_group_shares
    assert {name: g.share for name, g in groups.items()} == {
        "web": 0.55,
        "code": 0.30,
        "apps": 0.15,
    }
    assert {name: set(g.sources) for name, g in groups.items()} == {
        "web": {"fot", "almanac", "google"},
        "code": {"npm_fontsource", "ecosystems", "jsdelivr"},
        "apps": {"npm_expo", "flutter"},
    }


def test_chocolatey_is_dropped_by_ruling_t3() -> None:
    ranking = load_config(Paths.for_root(ROOT)).ranking
    assert "chocolatey" not in ranking.sources.all()
    for weights in (
        ranking.surveys.desktop.weights,
        ranking.ranks.coding.weights,
        ranking.ranks.dev_apps.weights,
    ):
        assert "chocolatey" not in weights
    assert "chocolatey" not in GROUPS
    assert "choco-id" not in NAMESPACES


def test_missing_file_fails(cfg: ConfigCopy) -> None:
    (cfg.config / "licenses.toml").unlink()
    with pytest.raises(ConfigError, match=r"licenses\.toml: missing"):
        cfg.load()


# --- config/category-overrides.toml (owner ruling of 2026-09-29, gate R round 1) --------------

CATEGORIES_22 = {
    "serif": {
        "junicode",
        "latin-modern",
        "tex-gyre-termes",
        "tex-gyre-pagella",
        "tex-gyre-schola",
        "tex-gyre-bonum",
        "charter",
        "gentium",
        "fanwood",
    },
    "monospace": {"cozette", "miracode", "compagnon"},
    "handwriting": {"tex-gyre-chorus"},
    "sans-serif": {
        "tex-gyre-heros",
        "tex-gyre-heros-cn",
        "tex-gyre-adventor",
        "sn-pro-font-family",
        "pretendard-std",
        "sophia-nubian",
        "tagmukay",
        "heavy-data",
        "awami-nastaliq",
    },
}


# Later rulings of 2026-09-29: ET Book is a serif (gate LIC, LIC-et-book, which let it in), and
# FreeFont stays monospace, as FreeMono's file makes it (gate R, freefont_category).
LATER_CATEGORIES = {"et-book": "serif", "freefont": "monospace"}


def test_category_overrides_hold_the_owner_ruling_of_2026_09_29() -> None:
    """categories_22 in data/reviews/review/2026-09-29.toml: every one of the 22 fonts is
    listed, the nine the owner kept on sans-serif included; and the later rulings."""
    families = load_config(Paths.for_root(ROOT)).category_overrides.families
    by_category: dict[str, set[str]] = {}
    for fid, category in families.items():
        if fid not in LATER_CATEGORIES:
            by_category.setdefault(category, set()).add(fid)
    assert by_category == CATEGORIES_22
    assert len(families) == 22 + len(LATER_CATEGORIES)
    assert {f: families.get(f) for f in LATER_CATEGORIES} == LATER_CATEGORIES


CATEGORY_BREAKS = {
    "not-a-category": (_set("families", "junicode", value="slab"), "families.junicode: 'slab'"),
    "not-a-string": (_set("families", "junicode", value=1), "expected a string"),
    "not-an-id": (_set("families", "Junicode", value="serif"), "'Junicode' is not a family id"),
    "no-table": (_drop("families"), "families: missing key"),
    "schema": (_set("schema", value=2), "schema 2, expected 1"),
}


@pytest.mark.parametrize("case", sorted(CATEGORY_BREAKS))
def test_category_overrides_load_strictly(cfg: ConfigCopy, case: str) -> None:
    edit, message = CATEGORY_BREAKS[case]
    cfg.change("category-overrides.toml", edit)
    with pytest.raises(ConfigError, match=rf"category-overrides\.toml.*{re.escape(message)}"):
        cfg.load()


def test_category_overrides_count_toward_the_config_hash_only(cfg: ConfigCopy) -> None:
    base = cfg.load()
    cfg.change("category-overrides.toml", _set("families", "junicode", value="display"))
    changed = cfg.load()
    assert changed.category_overrides.families["junicode"] == "display"
    assert config_hash(changed) != config_hash(base)
    assert ranking_hash(changed) == ranking_hash(base)


def test_strict_check_needs_every_overridden_family_in_the_id_registry(cfg: ConfigCopy) -> None:
    loaded = cfg.load()
    check_category_ids(loaded, cfg.paths)  # no registry yet: nothing to check against
    ids = {fid: {"family": fid} for fid in loaded.category_overrides.families}
    state_ids = cfg.root / "state" / "ids.json"
    state_ids.parent.mkdir()
    state_ids.write_text(json.dumps(ids), encoding="utf-8")
    check_category_ids(loaded, cfg.paths)
    del ids["tagmukay"]
    state_ids.write_text(json.dumps(ids), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"not in the id registry \(state/ids\.json\): tagmukay"):
        check_category_ids(loaded, cfg.paths)


NERD_HIDDEN_BREAKS = {
    "no-reason": (_set("families", "hack", value="  "), "families.hack: give the reason"),
    "not-a-string": (_set("families", "hack", value=True), "expected a string"),
    "not-an-id": (_set("families", "Hack", value="Icons failed"), "'Hack' is not a family id"),
    "no-table": (_drop("families"), "families: missing key"),
    "schema": (_set("schema", value=2), "schema 2, expected 1"),
}


@pytest.mark.parametrize("case", sorted(NERD_HIDDEN_BREAKS))
def test_hidden_nerd_builds_load_strictly(cfg: ConfigCopy, case: str) -> None:
    """config/nerd-hidden.toml (owner ruling of 2026-09-29): family id -> a required reason."""
    edit, message = NERD_HIDDEN_BREAKS[case]
    cfg.change("nerd-hidden.toml", _set("families", "hack", value="Its icon sets failed."))
    cfg.change("nerd-hidden.toml", edit)
    with pytest.raises(ConfigError, match=rf"nerd-hidden\.toml.*{re.escape(message)}"):
        cfg.load()


def test_hidden_nerd_builds_count_toward_the_config_hash(cfg: ConfigCopy) -> None:
    base = cfg.load()
    assert base.nerd_hidden.families == {}  # nothing hidden yet
    cfg.change("nerd-hidden.toml", _set("families", "hack", value="Its icon sets failed."))
    changed = cfg.load()
    assert changed.nerd_hidden.families == {"hack": "Its icon sets failed."}
    assert config_hash(changed) != config_hash(base)
    assert ranking_hash(changed) == ranking_hash(base)
    # --strict: the id must be in the registry, as for category-overrides.toml.
    ids = {fid: {"family": fid} for fid in changed.category_overrides.families}
    state_ids = cfg.root / "state" / "ids.json"
    state_ids.parent.mkdir()
    state_ids.write_text(json.dumps(ids), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"nerd-hidden\.toml: families: not in the id registry"):
        check_category_ids(changed, cfg.paths)
    ids["hack"] = {"family": "Hack"}
    state_ids.write_text(json.dumps(ids), encoding="utf-8")
    check_category_ids(changed, cfg.paths)


def test_bad_toml_fails(cfg: ConfigCopy) -> None:
    (cfg.config / "ranking.toml").write_text("[engine\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"ranking\.toml"):
        cfg.load()


# --- collector settings (config/sources/<name>.toml) ------------------------------------------


@dataclass(frozen=True, slots=True)
class _Settings:
    series: str
    enabled: bool = True
    scopes: tuple[str, ...] = ()


class _Collector:
    name = "synth_source"
    Settings = _Settings


def test_every_settings_file_belongs_to_a_collector() -> None:
    found = discover()
    for path in sorted((ROOT / "config" / "sources").glob("*.toml")):
        assert path.stem in found, f"{path.name}: no collector of that name"


def _drop_source_settings(cfg: ConfigCopy) -> None:
    """Remove the copied sources/*.toml: with ``discover`` patched to {}, each real
    settings file would fail as "no collector" before the check under test."""
    for path in (cfg.config / "sources").glob("*.toml"):
        path.unlink()


def test_strict_check_rejects_a_stray_settings_file(
    cfg: ConfigCopy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collectors, "discover", lambda kind=None: {})
    _drop_source_settings(cfg)
    cfg.write("sources/synth_source.toml", {"enabled": True})
    with pytest.raises(ConfigError, match=r"sources/synth_source\.toml: no collector"):
        check_collectors(cfg.load(), cfg.paths)


def test_strict_check_needs_a_collector_for_every_enabled_source(
    cfg: ConfigCopy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collectors, "discover", lambda kind=None: {})
    _drop_source_settings(cfg)
    with pytest.raises(ConfigError, match=r"sources\.homebrew\.collector: no collector"):
        check_collectors(cfg.load(), cfg.paths)


def test_collector_settings_load_strictly(cfg: ConfigCopy) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load_settings(_Collector(), cfg.paths)
    cfg.write("sources/synth_source.toml", {"series": "365d", "scopes": ["@fontsource"]})
    assert load_settings(_Collector(), cfg.paths) == _Settings("365d", True, ("@fontsource",))
    cfg.write("sources/synth_source.toml", {"series": "365d", "scope": ["@fontsource"]})
    with pytest.raises(ConfigError, match=r"unknown key.*scope"):
        load_settings(_Collector(), cfg.paths)
    cfg.write("sources/synth_source.toml", {"enabled": False})
    with pytest.raises(ConfigError, match="series: missing key"):
        load_settings(_Collector(), cfg.paths)
