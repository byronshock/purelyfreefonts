"""Command line entry point for ``tff-catalog`` (design-m1 §1.2). Frozen contract.

``main(argv) -> int``: ``argv`` defaults to ``sys.argv[1:]`` and the return
value is the exit code: 0 success, 1 a failed check or bad config, 2 a usage
error, a missing ``TFF_STORE``, or a part that is not implemented yet.

Every stage in ``stages.STAGES`` is a subcommand of the same name and takes
the common flags ``--date``, ``--only``, ``--from-snapshots``, ``--refetch`` and
``--keep-raw``. Some stages add a mode flag (``licenses --queue``, ``aliases
--apply``, ``map --check-unmatched N``, ``verify --check``, ``links --check``,
``specimens --check``). The other commands are ``config``, ``refresh``,
``store {ls,check,commit,gc}``, ``questions --gate G`` and ``rulings apply FILE``.

Modules are imported only when their command runs, so ``--help`` and
``config`` stay fast and never load numpy.

Expected failures end in one line, ``tff-catalog <command>: <message>``, and
exit code 1, never a traceback: a fetch, host scope, budget, frozen snapshot or
git error, a missing or malformed input or stage file, and each of validate's
hard failures (already redacted, since Actions logs of the public repo are
public). Any other exception is a bug and keeps its traceback.
"""

import argparse
import importlib
import logging
import re
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from tff_catalog import __version__, clock, stages
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import BudgetExceeded, FetchError, HostNotAllowed
from tff_catalog.gitsrc import GitError
from tff_catalog.paths import Paths, StoreNotConfigured
from tff_catalog.reviews import GATES
from tff_catalog.stageio import StageFileError
from tff_catalog.store import SnapshotFrozen

PROG = "tff-catalog"
COMMANDS = frozenset({"config", *stages.names(), "refresh", "store", "questions", "rulings"})

_STAGE_HELP = {
    "fetch": "snapshot every enabled source into the store",
    "parse": "turn snapshots into records (build/stage/records/)",
    "universe": "build the candidate families and their ids",
    "latin": "apply the Latin gate",
    "facts": "derive category, monospace and formats",
    "licenses": "classify licenses (L1, L2)",
    "aliases": "mine aliases and build the alias index",
    "map": "map source keys to families; write build/unmatched.md",
    "correct": "apply floors, credits, exposure and Linux abstentions",
    "rank": "score every survey and view",
    "membership": "update catalog membership",
    "verify": "verify catalog licenses against upstream texts (L3)",
    "confidence": "compute rank ranges and tiers",
    "links": "choose and check official download links",
    "export": "write build/catalog.json",
    "specimens": "render SVG specimens (Milestone 2)",
    "export-site": "write build/catalog-site.json and build/names.json",
    "validate": "check schemas and the hard checks",
    "review": "write build/review.md",
    "backtest": "backtest churn on historical windows",
    "match": "write the owned-font match file (Milestone 3)",
}


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text!r}") from None


def _names(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _duration(text: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([dh])", text)
    if not m:
        raise argparse.ArgumentTypeError(f"not a duration like 7d or 12h: {text!r}")
    n = int(m[1])
    return timedelta(days=n) if m[2] == "d" else timedelta(hours=n)


def _common_flags() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("run options")
    g.add_argument(
        "--date", type=_iso_date, metavar="YYYY-MM-DD", help="run date (default: today, UTC)"
    )
    g.add_argument(
        "--only",
        type=_names,
        action="extend",
        default=[],
        metavar="NAMES",
        help="only these collectors or sources (comma-separated, repeatable)",
    )
    g.add_argument(
        "--from-snapshots",
        type=_iso_date,
        metavar="YYYY-MM-DD",
        help="replay from the store's snapshots for this date, with no network",
    )
    g.add_argument("--refetch", action="store_true", help="replace today's snapshots")
    g.add_argument("--keep-raw", action="store_true", help="keep big raw downloads after the run")
    return common


def build_parser() -> argparse.ArgumentParser:
    """The full argument parser; every subcommand sets ``func``."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Builds the ranked catalog of truly free Latin fonts.",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="log more (-vv: debug)")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    common = _common_flags()

    p = sub.add_parser("config", help="print the effective config and its sha256")
    p.add_argument(
        "--strict",
        action="store_true",
        help="also check config/sources/ and ranking.toml against the collectors",
    )
    p.set_defaults(func=_cmd_config)

    for stage in stages.STAGES:
        p = sub.add_parser(
            stage.name,
            parents=[common],
            help=f"{_STAGE_HELP[stage.name]} ({stage.step})",
            description=f"Stage {stage.number}: {_STAGE_HELP[stage.name]}. Implemented in {stage.step}.",
        )
        p.set_defaults(func=_cmd_stage)
        _add_mode_flags(stage.name, p)

    p = sub.add_parser("refresh", parents=[common], help="run the whole pipeline (M1 step 18)")
    p.set_defaults(func=_cmd_refresh)

    p = sub.add_parser("store", help="inspect and maintain the snapshot store ($TFF_STORE)")
    store_sub = p.add_subparsers(dest="store_command", metavar="<store command>", required=True)
    q = store_sub.add_parser("ls", help="list sources, or one source's snapshots")
    q.add_argument("source", nargs="?")
    q.set_defaults(func=_cmd_store)
    q = store_sub.add_parser("check", help="check snapshot sizes and completeness")
    q.set_defaults(func=_cmd_store)
    q = store_sub.add_parser("commit", help="commit and push new snapshots")
    q.add_argument("-m", "--message")
    q.set_defaults(func=_cmd_store)
    q = store_sub.add_parser("gc", help="delete old raw download directories")
    q.add_argument("--raw-older-than", type=_duration, default=timedelta(days=7), metavar="AGE")
    q.set_defaults(func=_cmd_store)

    p = sub.add_parser("questions", help="print a gate's open owner questions")
    p.add_argument("--gate", required=True, choices=GATES)
    p.set_defaults(func=_cmd_questions)

    p = sub.add_parser("rulings", help="record the owner's rulings")
    rulings_sub = p.add_subparsers(
        dest="rulings_command", metavar="<rulings command>", required=True
    )
    q = rulings_sub.add_parser("apply", help="check an answers file and save it in data/reviews/")
    q.add_argument("file", type=Path)
    q.set_defaults(func=_cmd_rulings_apply)
    return parser


def _add_mode_flags(name: str, p: argparse.ArgumentParser) -> None:
    if name in ("licenses", "aliases"):
        modes = p.add_mutually_exclusive_group()
        modes.add_argument("--queue", action="store_true", help="print the review queue")
        if name == "aliases":
            modes.add_argument(
                "--apply", action="store_true", help="merge accepted rows into data/aliases.csv"
            )
        p.add_argument("--count", action="store_true", help="with --queue: print only its size")
    elif name == "map":
        p.add_argument(
            "--check-unmatched",
            type=int,
            metavar="N",
            help="fail if any source has an unmatched key in its top N",
        )
    elif name in ("verify", "links", "specimens"):
        p.add_argument("--check", action="store_true", help="check the outputs instead of running")
    elif name == "validate":
        p.add_argument(
            "--committed",
            action="store_true",
            help="check only what the committed outputs can show (no build/stage/)",
        )


# --- handlers -------------------------------------------------------------------------


def _call(target: str, *args: object, **kwargs: object) -> int:
    module, _, function = target.partition(":")
    return getattr(importlib.import_module(module), function)(*args, **kwargs)


def _options(args: argparse.Namespace) -> stages.RunOptions:
    return stages.RunOptions(
        only=tuple(args.only),
        from_snapshots=args.from_snapshots,
        refetch=args.refetch,
        keep_raw=args.keep_raw,
    )


def _run_date(args: argparse.Namespace) -> date:
    return args.date or args.from_snapshots or clock.utc_today()


def _mode(args: argparse.Namespace) -> tuple[str, dict[str, object]] | None:
    name = args.command
    if name in ("licenses", "aliases") and (args.queue or args.count):
        return f"tff_catalog.{name}:cmd_queue", {"count": args.count}
    if name == "aliases" and args.apply:
        return "tff_catalog.aliases:cmd_apply", {}
    if name == "map" and args.check_unmatched is not None:
        return "tff_catalog.mapping:cmd_check_unmatched", {"top": args.check_unmatched}
    if name == "verify" and args.check:
        return "tff_catalog.license_l3:cmd_check", {}
    if name == "links" and args.check:
        return "tff_catalog.links:cmd_check", {}
    if name == "specimens" and args.check:
        return "tff_catalog.specimens.budget:cmd_check", {}
    if name == "validate" and args.committed:
        return "tff_catalog.validate:cmd_committed", {}
    return None


def _cmd_config(args: argparse.Namespace) -> int:
    return _call("tff_catalog.config:cmd_config", args)


def _cmd_stage(args: argparse.Namespace) -> int:
    from tff_catalog.config import load_config

    stage = stages.get(args.command)
    paths = Paths.from_env()
    ctx = stages.make_context(
        paths,
        load_config(paths),
        _run_date(args),
        _options(args),
        network=stage.network,
        log=logging.getLogger("tff_catalog"),
    )
    try:
        mode = _mode(args)
        if mode is not None:
            target, kwargs = mode
            return _call(target, ctx, **kwargs)
        stages.run_stage(stage.name, ctx)
        return 0
    finally:
        if ctx.fetcher is not None:
            ctx.fetcher.close()


def _cmd_refresh(args: argparse.Namespace) -> int:
    from tff_catalog.refresh import refresh

    result = refresh(
        Paths.from_env(),
        _run_date(args),
        from_snapshots=args.from_snapshots,
        options=_options(args),
    )
    for failure in result.failures:
        print(f"{PROG} refresh: {failure}", file=sys.stderr)
    return 0 if result.ok else 1


def _cmd_store(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    paths.require_store()
    command = args.store_command
    if command == "ls":
        return _call("tff_catalog.store:cmd_ls", paths, args.source)
    if command == "check":
        return _call("tff_catalog.store:cmd_check", paths)
    if command == "commit":
        return _call("tff_catalog.store:cmd_commit", paths, args.message)
    return _call("tff_catalog.store:cmd_gc", paths, args.raw_older_than)


def _cmd_questions(args: argparse.Namespace) -> int:
    return _call("tff_catalog.reviews:cmd_questions", Paths.from_env(), args.gate)


def _cmd_rulings_apply(args: argparse.Namespace) -> int:
    return _call("tff_catalog.reviews:cmd_apply_rulings", Paths.from_env(), args.file)


# --- main ---------------------------------------------------------------------------------

# Expected failures (module docstring): a message and exit code 1. StageFileError
# is a ValueError and FileNotFoundError an OSError; neither base is caught, so
# other errors of those kinds keep their tracebacks.
_FAILURES: tuple[type[Exception], ...] = (
    FetchError,
    HostNotAllowed,
    BudgetExceeded,
    SnapshotFrozen,
    GitError,
    StageFileError,
    FileNotFoundError,
)


def _validation_failed() -> type[Exception]:
    """``validate.ValidationFailed``, imported only once an exception needs matching.

    ``validate`` pulls in ``export``, which ``--help`` and ``config`` do without.
    """
    from tff_catalog.validate import ValidationFailed

    return ValidationFailed


def _where(exc: BaseException) -> str:
    """``module.function`` of the innermost frame, which raised ``exc``."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    if tb is None:
        return "?"
    frame = tb.tb_frame
    return f"{frame.f_globals.get('__name__', '?')}.{frame.f_code.co_name}"


def main(argv: list[str] | None = None) -> int:
    """Run tff-catalog with ``argv`` and return the process exit code."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not args_list:
        parser.print_help(sys.stderr)
        return 2
    command = next((a for a in args_list if not a.startswith("-")), None)
    if command is not None and command not in COMMANDS:
        print(f"{PROG}: unknown command {command!r}", file=sys.stderr)
        parser.print_usage(sys.stderr)
        return 2
    try:
        args = parser.parse_args(args_list)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    level = (logging.WARNING, logging.INFO, logging.DEBUG)[min(args.verbose, 2)]
    logging.basicConfig(
        level=level, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    # httpx logs every request URL at INFO, and refresh runs with -v in public Actions logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    func: Callable[[argparse.Namespace], int] = args.func
    try:
        return func(args)
    except NotImplementedError as exc:
        # Name the stage's own step too: until M1 step 3 lands, every stage
        # stops early in load_state, which is not the stage's own stub.
        who = args.command
        if who in stages.names():
            who = f"{who} ({stages.get(who).step})"
        step = str(exc) or "a later step"
        print(f"{PROG} {who}: not implemented yet ({step}: {_where(exc)})", file=sys.stderr)
        return 2
    except StoreNotConfigured as exc:
        print(f"{PROG} {args.command}: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"{PROG} {args.command}: {exc}", file=sys.stderr)
        return 1
    except _FAILURES as exc:
        print(f"{PROG} {args.command}: {exc}", file=sys.stderr)
        return 1
    except _validation_failed() as exc:
        for failure in getattr(exc, "failures", ()) or (exc,):
            print(f"{PROG} {args.command}: {failure}", file=sys.stderr)
        return 1
