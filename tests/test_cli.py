"""The ``tff-catalog`` command line (design-m1 §1.2): every command registered, --help works."""

import argparse
import importlib
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from tests.helpers import ROOT

from tff_catalog import __version__, cli, stages
from tff_catalog.config import config_hash, load_config
from tff_catalog.fetch import BudgetExceeded, FetchError, HostNotAllowed
from tff_catalog.gitsrc import GitError
from tff_catalog.paths import Paths
from tff_catalog.stageio import StageFileError
from tff_catalog.store import SnapshotFrozen

# design-m1 §1.2, plus the stages the core agent registered and M3's "match" slot.
DESIGN_COMMANDS = {
    "config",
    "fetch",
    "parse",
    "universe",
    "latin",
    "facts",
    "licenses",
    "aliases",
    "map",
    "correct",
    "rank",
    "verify",
    "confidence",
    "backtest",
    "links",
    "export",
    "validate",
    "review",
    "refresh",
    "store",
    "questions",
    "rulings",
    "match",
}
STORE_COMMANDS = ("ls", "check", "commit", "gc")
COMMON_FLAGS = ["--date", "2026-10-03", "--only", "a,b", "--only", "c"]
COMMON_FLAGS += ["--from-snapshots", "2026-10-03", "--refetch", "--keep-raw"]


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(actions) == 1
    return dict(actions[0].choices)


def test_every_command_is_registered() -> None:
    registered = _subcommands(cli.build_parser())
    assert set(registered) == set(cli.COMMANDS)
    assert set(registered) >= DESIGN_COMMANDS
    assert set(stages.names()) <= set(registered)
    assert set(_subcommands(registered["store"])) == set(STORE_COMMANDS)
    assert set(_subcommands(registered["rulings"])) == {"apply"}


def test_every_stage_handler_exists() -> None:
    for stage in stages.STAGES:
        assert callable(stage.load()), stage.target


@pytest.mark.parametrize(
    "target",
    [
        "tff_catalog.config:cmd_config",
        "tff_catalog.store:cmd_ls",
        "tff_catalog.store:cmd_check",
        "tff_catalog.store:cmd_commit",
        "tff_catalog.store:cmd_gc",
        "tff_catalog.reviews:cmd_questions",
        "tff_catalog.reviews:cmd_apply_rulings",
    ],
)
def test_command_handler_exists(target: str) -> None:
    module, _, function = target.partition(":")
    assert callable(getattr(importlib.import_module(module), function))


MODE_ARGS = [
    ["licenses", "--queue"],
    ["licenses", "--queue", "--count"],
    ["aliases", "--queue"],
    ["aliases", "--apply"],
    ["map", "--check-unmatched", "25"],
    ["verify", "--check"],
    ["links", "--check"],
    ["specimens", "--check"],
]


@pytest.mark.parametrize("argv", MODE_ARGS, ids=" ".join)
def test_mode_flags_reach_a_handler(argv: list[str]) -> None:
    args = cli.build_parser().parse_args(argv)
    mode = cli._mode(args)
    assert mode is not None
    module, _, function = mode[0].partition(":")
    assert callable(getattr(importlib.import_module(module), function))


@pytest.mark.parametrize("command", sorted(cli.COMMANDS))
def test_help_works_for_every_command(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([command, "--help"]) == 0
    assert capsys.readouterr().out.startswith(f"usage: tff-catalog {command}")


@pytest.mark.parametrize(
    "argv", [["store", c] for c in STORE_COMMANDS] + [["rulings", "apply"]], ids=" ".join
)
def test_help_works_for_nested_commands(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([*argv, "--help"]) == 0
    assert capsys.readouterr().out.startswith(f"usage: tff-catalog {' '.join(argv)}")


def test_top_level_help_lists_every_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    missing = [c for c in cli.COMMANDS if f"    {c} " not in out and f"    {c}\n" not in out]
    assert missing == []


@pytest.mark.parametrize("stage", stages.names())
def test_stages_take_the_common_flags(stage: str) -> None:
    args = cli.build_parser().parse_args([stage, *COMMON_FLAGS])
    assert args.date == date(2026, 10, 3)
    assert args.from_snapshots == date(2026, 10, 3)
    assert args.only == ["a", "b", "c"]
    assert (args.refetch, args.keep_raw) == (True, True)
    options = cli._options(args)
    assert options.only == ("a", "b", "c")
    assert options.replay


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["fetch", "--date", "2026-13-01"],
        ["store"],
        ["store", "gc", "--raw-older-than", "7 days"],
        ["questions"],
        ["questions", "--gate", "Z"],
        ["aliases", "--queue", "--apply"],
        ["map", "--check-unmatched", "many"],
    ],
    ids=lambda argv: " ".join(argv) or "no-args",
)
def test_usage_errors_exit_2(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(argv) == 2
    assert "usage: tff-catalog" in capsys.readouterr().err


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"tff-catalog {__version__}"


def test_config_prints_the_hash_last(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(ROOT)
    assert cli.main(["config"]) == 0
    last = capsys.readouterr().out.rstrip("\n").rsplit("\n", 1)[-1]
    assert last == f"sha256:{config_hash(load_config(Paths.for_root(ROOT)))}"


def test_store_commands_need_tff_store(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("TFF_STORE", raising=False)
    assert cli.main(["store", "ls"]) == 2
    assert "TFF_STORE is not set" in capsys.readouterr().err
    monkeypatch.setenv("TFF_STORE", str(tmp_path / "missing"))
    assert cli.main(["store", "check"]) == 2
    assert "is not a directory" in capsys.readouterr().err


def test_importing_the_cli_stays_light() -> None:
    code = (
        "import sys, tff_catalog.cli as c\n"
        "c.build_parser()\n"
        "heavy = sorted({'numpy', 'httpx'} & set(sys.modules))\n"
        "print(heavy)\n"
        "sys.exit(1 if heavy else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=60
    )
    assert result.returncode == 0, f"tff_catalog.cli imports {result.stdout.strip()}"


EXPECTED_FAILURES = [
    FetchError("https://example.org/x: HTTP 503 after 4 retries"),
    HostNotAllowed("https://other.org/x: host 'other.org' is not in this fetcher's hosts"),
    BudgetExceeded("the 'github' budget of 900 requests is spent"),
    SnapshotFrozen("--refetch 2026-10-03: a merged run used it"),
    GitError("git clone failed: repository not found"),
    StageFileError("$.inter: Facts unknown ['x'], missing []"),
    FileNotFoundError("build/stage/universe.json: missing; run stage 'universe' first"),
]


@pytest.mark.parametrize("exc", EXPECTED_FAILURES, ids=lambda e: type(e).__name__)
def test_expected_failures_end_in_one_line(
    exc: Exception, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(args: argparse.Namespace) -> int:
        raise exc

    monkeypatch.setattr(cli, "_cmd_stage", fail)
    assert cli.main(["map"]) == 1
    assert capsys.readouterr().err == f"tff-catalog map: {exc}\n"


def test_validation_failures_print_one_line_each(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tff_catalog.validate import Failure, ValidationFailed

    failures = (Failure("schema", "catalog.json is invalid"), Failure("ineligible", "ranked", "x"))

    def fail(args: argparse.Namespace) -> int:
        raise ValidationFailed(failures)

    monkeypatch.setattr(cli, "_cmd_stage", fail)
    assert cli.main(["validate"]) == 1
    assert capsys.readouterr().err.splitlines() == [
        "tff-catalog validate: schema: catalog.json is invalid",
        "tff-catalog validate: ineligible [x]: ranked",
    ]


def test_a_bug_keeps_its_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(args: argparse.Namespace) -> int:
        raise KeyError("a bug")

    monkeypatch.setattr(cli, "_cmd_stage", fail)
    with pytest.raises(KeyError, match="a bug"):
        cli.main(["map"])


def test_a_stage_closes_its_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class FakeFetcher:
        def close(self) -> None:
            closed.append(True)

    def context(*args: object, **kwargs: object) -> object:
        return argparse.Namespace(fetcher=FakeFetcher())

    def run_stage(name: str, ctx: object) -> None:
        raise FetchError("every source failed to fetch")

    monkeypatch.setattr(Paths, "from_env", classmethod(lambda cls, root=None, env=None: None))
    monkeypatch.setattr("tff_catalog.config.load_config", lambda paths: None)
    monkeypatch.setattr(stages, "make_context", context)
    monkeypatch.setattr(stages, "run_stage", run_stage)
    assert cli.main(["fetch"]) == 1
    assert closed == [True]


def test_httpx_request_logs_stay_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    monkeypatch.setattr(cli, "_cmd_config", lambda args: 0)
    assert cli.main(["-v", "config"]) == 0
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
