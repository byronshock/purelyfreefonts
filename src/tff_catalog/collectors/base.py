"""The collector contract (design-m1 §2.2). Frozen contract.

A collector is a module ``collectors/{universe,ranking}/<name>.py`` exposing
``COLLECTOR``, an object satisfying ``Collector``. It has two halves:

- ``fetch(ctx)`` talks to the network through ``ctx.fetcher`` (scoped to
  ``hosts``) and writes extracts into ``ctx.out``. Only extracts are kept.
- ``parse(ctx)`` turns one snapshot into records: offline, pure and
  deterministic. It never reads aliases, state or other sources, and must
  accept every extract format up to ``version``.

Each collector's settings are a frozen dataclass ``Settings``, loaded strictly
from ``config/sources/<name>.toml`` by ``load_settings``.

Two conventions every collector follows (the contract test checks both):

- ``Settings`` has a field ``enabled: bool``: the fetch and parse stages run
  only enabled collectors. Subclassing ``CollectorBase.Settings`` gives it.
- ``fetch()`` records every request it makes in the manifest, by calling
  ``for rec in result.to_records(kept=...): ctx.out.record_fetch(rec)`` right
  after each ``ctx.fetcher`` call (``kept=True`` when the body itself is saved
  as an extract), so every redirect hop is listed too. The fetcher does not
  record on its own.

Collector modules keep heavy imports (numpy, fontTools) inside functions, so
discovery (``tff-catalog config --strict``, the contract test) stays fast.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar, Literal, Protocol, runtime_checkable

from tff_catalog.config_model import ConfigError, from_mapping, load_toml
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record
from tff_catalog.store import RawDir, Snapshot, SnapshotWriter

Kind = Literal["universe", "ranking", "license"]


@dataclass(frozen=True, slots=True)
class FetchContext:
    """What ``fetch()`` gets. Record every request with ``out.record_fetch`` (module docstring)."""

    run_date: date
    fetcher: Fetcher  # UA, per-host rate, retries, conditional GET; refuses hosts not in `hosts`
    out: SnapshotWriter  # <store>/<name>/<run_date>/ ; atomic; manifest written on close
    raw: RawDir  # big raw files; deleted after the run unless --keep-raw
    previous: Snapshot | None  # latest complete earlier snapshot, for incremental fetches
    settings: object  # instance of type(self).Settings
    log: logging.Logger
    # The repository's paths, for a collector whose input list lives in config/
    # (foundries reads config/foundries.toml); None only in tests that need none.
    paths: Paths | None = None


@dataclass(frozen=True, slots=True)
class ParseContext:
    snapshot: Snapshot
    settings: object
    log: logging.Logger


@runtime_checkable
class Collector(Protocol):
    """A collector; see the module docstring for the conventions on ``Settings`` and ``fetch``."""

    name: ClassVar[str]  # == module name == store directory
    kind: ClassVar[Kind]
    version: ClassVar[int]  # extract format; parse() must accept every version <= this
    hosts: ClassVar[tuple[str, ...]]
    emits: ClassVar[tuple[type, ...]]
    group: ClassVar[str | None]  # independence group (ranking collectors only)
    needs_baseline: ClassVar[bool]  # lifetime counters: parse also runs on baseline snapshots
    Settings: ClassVar[type]  # frozen dataclass with `enabled: bool`; config/sources/<name>.toml

    def fetch(self, ctx: FetchContext) -> None: ...

    def parse(self, ctx: ParseContext) -> Iterable[Record]:
        """Offline, pure, deterministic. Never reads aliases, state or other sources."""
        ...


class CollectorBase:
    """Optional defaults to subclass."""

    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    group: ClassVar[str | None] = None
    needs_baseline: ClassVar[bool] = False
    emits: ClassVar[tuple[type, ...]] = (Observation,)

    @dataclass(frozen=True, slots=True)
    class Settings:
        enabled: bool = True


def settings_path(paths: Paths, name: str) -> Path:
    """``config/sources/<name>.toml``."""
    return paths.sources_config / f"{name}.toml"


def load_settings(collector: Collector, paths: Paths) -> object:
    """Load ``collector.Settings`` strictly from ``config/sources/<name>.toml``.

    The file must exist; unknown keys fail, and so do missing keys without a
    default (``config_model.from_mapping``).
    """
    path = settings_path(paths, collector.name)
    if not path.is_file():
        raise ConfigError(f"{path}: missing (every collector needs its settings file)")
    return from_mapping(collector.Settings, load_toml(path), where=f"sources/{path.name}")
