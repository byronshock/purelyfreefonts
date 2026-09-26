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
from tff_catalog.config import check_collectors, config_hash, load_config, ranking_hash
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
}


@pytest.mark.parametrize("case", sorted(SITE_BREAKS))
def test_site_toml_cross_checks(cfg: ConfigCopy, case: str) -> None:
    edit, message = SITE_BREAKS[case]
    cfg.change("site.toml", edit)
    with pytest.raises(ConfigError, match=re.escape(message)):
        cfg.load()


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
