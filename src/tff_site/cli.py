"""Command line entry point for ``tff-site`` (frozen with the Milestone 2 contracts).

Contract: ``main(argv) -> int``, where ``argv`` defaults to ``sys.argv[1:]`` and the return
value is the exit code: 0 success, 1 a failed check or not implemented yet, 2 usage.

| Command | Calls |
|---|---|
| ``build [--data F] [--out D] [--drafts] ...`` | ``tff_site.build.build`` |
| ``serve [SITE_DIR] [--host H] [--port N]`` | ``tff_site.serve.serve`` |
| ``check [SITE_DIR]`` | ``tff_site.budgets.check`` |
| ``pack [SITE_DIR] [--out F] [--only F] [--manifest F]`` | ``tff_site.pack.pack`` |
| ``fetch-fonts [--data F] [--cache D]`` | ``tff_site.fonts.fetch_fonts`` |
| ``validate [FILE]`` | ``tff_site.data.validate_file`` |
| ``linkcheck [--data F] [--ids a,b] [--rate R]`` | ``tff_site.linkcheck.linkcheck`` |

A command that fails on bad input (``CatalogError``, ``BuildError``, ``AssetError``,
``PageError``, ``BlogError``, ``PackError`` or a Jinja2 ``TemplateError``) prints
``tff-site <command>: failed`` and each problem, at most ``MAX_ERRORS_SHOWN``, to stderr and
returns 1, with no traceback. Any other exception is a bug and keeps its traceback.
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from tff_catalog import __version__
from tff_site import assets, blog, budgets, build, data, fonts, linkcheck, pack, pages, serve

MAX_ERRORS_SHOWN = 50
# Bad input, not bugs: each carries its problems as ``errors`` or ``problems`` (module docstring).
_FAILURES: tuple[type[Exception], ...] = (
    data.CatalogError,
    build.BuildError,
    assets.AssetError,
    pages.PageError,
    blog.BlogError,
    pack.PackError,
)


def _template_error() -> type[Exception]:
    """``jinja2.TemplateError``, imported only once an exception needs matching."""
    import jinja2

    return jinja2.TemplateError


def _print_lines(lines: list[str]) -> None:
    for line in lines[:MAX_ERRORS_SHOWN]:
        print(f"  {line}", file=sys.stderr)
    if len(lines) > MAX_ERRORS_SHOWN:
        print(f"  ... and {len(lines) - MAX_ERRORS_SHOWN} more", file=sys.stderr)


def _cmd_build(ns: argparse.Namespace) -> int:
    result = build.build(
        ns.data,
        ns.out,
        fonts_dir=ns.fonts_dir,
        commit=ns.commit,
        allow_dirty=ns.allow_dirty,
        font_files=not ns.no_font_files,
        drafts=ns.drafts,
    )
    print(f"built {result.files} files into {result.out_dir}")
    return 0


def _cmd_serve(ns: argparse.Namespace) -> int:
    serve.serve(ns.site_dir, host=ns.host, port=ns.port)
    return 0


def _cmd_check(ns: argparse.Namespace) -> int:
    problems = budgets.check(ns.site_dir)
    for problem in problems:
        print(f"{problem.path}: {problem.message}", file=sys.stderr)
    if problems:
        return 1
    print(f"{ns.site_dir}: all budgets and CSP checks pass")
    return 0


def _cmd_pack(ns: argparse.Namespace) -> int:
    only = None
    if ns.only is not None:
        only = [line.strip() for line in ns.only.read_text(encoding="utf-8").splitlines()]
        only = [line for line in only if line]
    if ns.out == "-":
        pack.pack(ns.site_dir, sys.stdout.buffer, only=only, manifest_path=ns.manifest)
    else:
        with Path(ns.out).open("wb") as fh:
            pack.pack(ns.site_dir, fh, only=only, manifest_path=ns.manifest)
    return 0


def _cmd_fetch_fonts(ns: argparse.Namespace) -> int:
    report = fonts.fetch_fonts(ns.data, ns.cache)
    for font_id, reason in sorted(report.failed.items()):
        print(f"{font_id}: {reason}", file=sys.stderr)
    print(
        f"fetched {len(report.fetched)}, cached {len(report.cached)}, failed {len(report.failed)}"
    )
    return 1 if report.failed else 0


def _cmd_validate(ns: argparse.Namespace) -> int:
    try:
        result = data.validate_file(ns.path)
    except data.CatalogError as exc:
        print(f"{ns.path}: invalid", file=sys.stderr)
        _print_lines(exc.errors)
        return 1
    print(f"valid ({result.version}), {result.fonts} fonts")
    return 0


def _cmd_linkcheck(ns: argparse.Namespace) -> int:
    ids = [i for i in ns.ids.split(",") if i] if ns.ids else None
    results = linkcheck.linkcheck(ns.data, ids=ids, rate=ns.rate)
    bad = [r for r in results if r.status != 200]
    for r in results:
        print(f"{r.status:3d} {r.font_id} {r.field} {r.url}")
    return 1 if bad else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tff-site",
        description="Builds purelyfreefonts.com from the catalog.",
    )
    parser.add_argument("--version", action="version", version=f"tff-site {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", title="commands")

    def add(name: str, func: Callable[[argparse.Namespace], int], text: str):
        p = sub.add_parser(name, help=text, description=text[0].upper() + text[1:] + ".")
        p.set_defaults(func=func)
        return p

    site_dir = {"type": Path, "nargs": "?", "default": build.DEFAULT_OUT}
    catalog = {"type": Path, "default": build.DEFAULT_DATA}

    p = add("build", _cmd_build, "build the site from catalog-site.json, offline")
    p.add_argument("--data", **catalog, help="catalog-site.json (default: build/catalog-site.json)")
    p.add_argument("--out", type=Path, default=build.DEFAULT_OUT, help="default: build/site")
    p.add_argument("--fonts-dir", type=Path, default=fonts.DEFAULT_CACHE, help="font cache")
    p.add_argument("--commit", help="commit to record in version.txt (default: git HEAD)")
    p.add_argument("--allow-dirty", action="store_true", help="build from a dirty tree")
    p.add_argument("--no-font-files", action="store_true", help='leave out "Type your own text"')
    p.add_argument(
        "--drafts", action="store_true", help="also publish blog posts marked draft (staging)"
    )

    p = add("serve", _cmd_serve, "preview a built site locally with the production headers")
    p.add_argument("site_dir", **site_dir, help="default: build/site")
    p.add_argument("--host", default=serve.DEFAULT_HOST)
    p.add_argument("--port", type=int, default=serve.DEFAULT_PORT)

    p = add("check", _cmd_check, "check a built site's size budgets and CSP rules")
    p.add_argument("site_dir", **site_dir, help="default: build/site")

    p = add("pack", _cmd_pack, "write a deterministic tar and manifest of a built site")
    p.add_argument("site_dir", **site_dir, help="default: build/site")
    p.add_argument("--out", default="-", help="tar file, or - for stdout (default)")
    p.add_argument("--only", type=Path, help="file listing the paths to include, one per line")
    p.add_argument("--manifest", type=Path, help="also write site.manifest.json here")

    p = add("fetch-fonts", _cmd_fetch_fonts, "download and verify the font files the site serves")
    p.add_argument("--data", **catalog, help="catalog-site.json (default: build/catalog-site.json)")
    p.add_argument("--cache", type=Path, default=fonts.DEFAULT_CACHE, help="font cache")

    p = add("validate", _cmd_validate, "validate a catalog-site.json file")
    p.add_argument("path", type=Path, nargs="?", default=build.DEFAULT_DATA, help="the file")

    p = add("linkcheck", _cmd_linkcheck, "check that license and download links answer HTTP 200")
    p.add_argument("--data", **catalog, help="catalog-site.json (default: build/catalog-site.json)")
    p.add_argument("--ids", help="comma-separated font ids (default: every font)")
    p.add_argument("--rate", type=float, default=1.0, help="requests per second per host")
    return parser


COMMANDS = ("build", "serve", "check", "pack", "fetch-fonts", "validate", "linkcheck")


def main(argv: list[str] | None = None) -> int:
    """Run tff-site with ``argv`` and return the process exit code."""
    args = sys.argv[1:] if argv is None else argv
    parser = _parser()
    if args and not args[0].startswith("-") and args[0] not in COMMANDS:
        print(f"tff-site: unknown command {args[0]!r}", file=sys.stderr)
        print(parser.format_usage(), end="", file=sys.stderr)
        return 2
    try:
        ns = parser.parse_args(args)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if ns.command is None:
        parser.print_usage(sys.stderr)
        return 2
    try:
        return ns.func(ns)
    except NotImplementedError as exc:
        print(f"tff-site {ns.command}: not implemented yet ({exc})", file=sys.stderr)
        return 1
    except _FAILURES as exc:
        lines = getattr(exc, "errors", None) or getattr(exc, "problems", None) or [str(exc)]
        print(f"tff-site {ns.command}: failed", file=sys.stderr)
        _print_lines([str(line) for line in lines])
        return 1
    except _template_error() as exc:
        print(f"tff-site {ns.command}: failed", file=sys.stderr)
        _print_lines([f"template error: {exc}"])
        return 1
