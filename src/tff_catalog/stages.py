"""The stage registry, the stage context and the runner (design-m1 §1.2). Frozen contract.

Every stage is a function ``run(ctx: StageContext) -> None`` in its own module,
named in ``STAGES`` as ``"module:function"`` and imported only when it runs, so
``tff-catalog config`` never loads numpy.

Stages read the committed ``state/`` through ``ctx.state`` and never write it.
Each writes only the ``build/state/`` files it owns, with
``state.write_part`` (``state.STATE_OWNERS``); a later owner of the same file
reads the earlier one's with ``state.read_part``. Other stage outputs go through
``stageio`` (``STAGE_FILES``).

``STAGES`` is in pipeline order, "validate" last. ``refresh`` runs every stage whose
``pipeline`` is true, skipping ``fetch`` in replay; stage "verify" can ask for
one rerun of "rank" and "membership" (refresh.py handles that). "backtest"
runs only on its own. The Milestone 3 slot "match" (``tff_catalog.match``) is
in the pipeline so the monthly refresh regenerates ``build/match-site.json``
(M3 step 12); until M3 step 3 it is a logged no-op, and "specimens" is one
until M2 step 5.
"""

import importlib
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date

from tff_catalog.config_model import Config
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths, StoreNotConfigured
from tff_catalog.state import State, load_state
from tff_catalog.store import Store


@dataclass(frozen=True, slots=True)
class RunOptions:
    """The common command-line flags."""

    only: tuple[str, ...] = ()  # --only: collectors or sources to include (empty = all)
    from_snapshots: date | None = None  # --from-snapshots: replay, no network
    refetch: bool = False  # --refetch: replace today's snapshot
    keep_raw: bool = False  # --keep-raw: keep big raw files after the run

    @property
    def replay(self) -> bool:
        return self.from_snapshots is not None

    def selects(self, name: str) -> bool:
        """Whether ``--only`` lets ``name`` through."""
        return not self.only or name in self.only


@dataclass(frozen=True, slots=True)
class StageContext:
    """Everything a stage may use. ``fetcher`` is None in replay; ``store`` is None without TFF_STORE.

    ``state`` is the committed state, read-only. A stage proposes its part of
    the next state with ``state.write_part(ctx.paths, name, obj, stage=...)``.
    """

    paths: Paths
    config: Config
    state: State
    run_date: date
    store: Store | None
    fetcher: Fetcher | None
    log: logging.Logger
    options: RunOptions = field(default_factory=RunOptions)

    def require_store(self) -> Store:
        """The snapshot store, or ``paths.StoreNotConfigured`` with a fix-it message."""
        if self.store is None:
            self.paths.require_store()  # raises with the fix-it message
            raise StoreNotConfigured("this stage context has no snapshot store")
        return self.store

    def require_fetcher(self) -> Fetcher:
        """The fetcher; raises in replay, where every read must come from the store."""
        if self.fetcher is None:
            raise RuntimeError("no network in replay (--from-snapshots): read from the store")
        return self.fetcher


@dataclass(frozen=True, slots=True)
class Stage:
    name: str  # also the tff-catalog subcommand
    number: str  # design-m1 §1.2 table
    target: str  # "module:function", imported lazily
    step: str  # checklist step that implements it, e.g. "M1 step 4"
    pipeline: bool = True  # part of `refresh`
    network: bool = False  # reads the network outside replay (through the store's pseudo-sources)

    def load(self) -> Callable[[StageContext], None]:
        module, _, function = self.target.partition(":")
        return getattr(importlib.import_module(module), function)


STAGES: tuple[Stage, ...] = (
    Stage("fetch", "1", "tff_catalog.fetch:run", "M1 step 3", network=True),
    Stage("parse", "2", "tff_catalog.parse:run", "M1 step 3"),
    Stage("universe", "3", "tff_catalog.universe:run", "M1 step 4"),
    Stage("latin", "4", "tff_catalog.latin:run", "M1 step 5", network=True),
    Stage("facts", "5", "tff_catalog.facts:run", "M1 step 5b", network=True),
    Stage("licenses", "6", "tff_catalog.licenses:run", "M1 step 6a"),
    Stage("aliases", "7", "tff_catalog.aliases:run", "M1 step 7"),
    Stage("map", "8", "tff_catalog.mapping:run", "M1 step 9"),
    Stage("correct", "9", "tff_catalog.corrections:run", "M1 step 10"),
    Stage("rank", "10", "tff_catalog.surveys:run", "M1 step 11"),
    Stage("membership", "11", "tff_catalog.membership:run", "M1 step 12"),
    Stage("verify", "12", "tff_catalog.license_l3:run", "M1 step 6b", network=True),
    Stage("confidence", "13", "tff_catalog.confidence:run", "M1 step 13"),
    Stage("links", "14", "tff_catalog.links:run", "M1 step 14", network=True),
    Stage("export", "15", "tff_catalog.export:run", "M1 step 15"),
    Stage("specimens", "15b", "tff_catalog.specimens.stage:run", "M2 step 5", network=True),
    Stage("export-site", "15c", "tff_catalog.export:run_site", "M1 step 15"),
    Stage("match", "15d", "tff_catalog.match:run", "M3 step 3"),
    # "review" before "validate": validate scans the committed build/*.md reports for
    # hidden values, so this run's review.md is checked in this run, and a hard failure
    # still leaves the review. The numbers stay the design table's.
    Stage("review", "17", "tff_catalog.review:run", "M1 step 15"),
    Stage("validate", "16", "tff_catalog.validate:run", "M1 step 15"),
    Stage("backtest", "-", "tff_catalog.backtest:run", "M1 step 13", pipeline=False),
)
_BY_NAME = {s.name: s for s in STAGES}


def get(name: str) -> Stage:
    """The stage called ``name``; ``KeyError`` lists the valid names."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown stage {name!r}; stages: {', '.join(_BY_NAME)}") from None


def names() -> tuple[str, ...]:
    """Every stage name, in ``STAGES`` order."""
    return tuple(_BY_NAME)


def pipeline(*, replay: bool = False) -> tuple[Stage, ...]:
    """The stages ``refresh`` runs, in order (without ``fetch`` in replay)."""
    return tuple(s for s in STAGES if s.pipeline and not (replay and s.name == "fetch"))


def make_context(
    paths: Paths,
    config: Config,
    run_date: date,
    options: RunOptions | None = None,
    *,
    network: bool = True,
    log: logging.Logger | None = None,
) -> StageContext:
    """Build the context for a run: load ``state/``, open the store, create the fetcher.

    The fetcher is created only when ``network`` is true and the run is not a
    replay.
    """
    options = options or RunOptions()
    state = load_state(paths.state)
    store = Store(paths.store) if paths.store is not None else None
    fetcher = Fetcher() if network and not options.replay else None
    return StageContext(
        paths=paths,
        config=config,
        state=state,
        run_date=run_date,
        store=store,
        fetcher=fetcher,
        log=log or logging.getLogger("tff_catalog"),
        options=options,
    )


def run_stage(name: str, ctx: StageContext) -> None:
    """Run one stage with a child logger named after it."""
    stage = get(name)
    run = stage.load()
    log = ctx.log.getChild(stage.name)
    log.info("stage %s (%s) starting", stage.name, stage.number)
    run(replace(ctx, log=log))
    log.info("stage %s done", stage.name)


def run_pipeline(ctx: StageContext, names: Iterable[str] | None = None) -> list[str]:
    """Run ``names`` (default: the refresh pipeline) in ``STAGES`` order; return what ran."""
    wanted = set(names) if names is not None else None
    ran = []
    for stage in pipeline(replay=ctx.options.replay):
        if wanted is None or stage.name in wanted:
            run_stage(stage.name, ctx)
            ran.append(stage.name)
    return ran
