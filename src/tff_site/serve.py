"""``tff-site serve``: a local preview server that sends the production headers.

It reads the header lines of the ``(tff_security)`` and ``(tff_site)`` snippets in
``ops/caddy/site.caddy``, so the preview, CI, staging and production share one source:

- every response gets the ``tff_security`` headers, and no ``Server`` header;
- ``/assets/*`` gets ``Cache-Control: public, max-age=31536000, immutable``;
- everything else gets ``Cache-Control: no-cache, no-transform``;
- a missing file is answered with ``/404.html`` and status 404, with the same headers.

It serves ``index.html`` for directory paths and never compresses (CI uses real Caddy for
the compressed performance runs). Standard library only.
"""

import re
import socket
import sys
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_CADDY = REPO_ROOT / "ops" / "caddy" / "site.caddy"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# What the preview mirrors of site.caddy's matchers; parse_headers refuses anything else, so
# a change there fails loudly here instead of the preview quietly sending other headers.
SECURITY_SNIPPET = "tff_security"
SITE_SNIPPET = "tff_site"
IMMUTABLE_MATCHER = ("@immutable", ("path", "/assets/*"))
REVALIDATE_MATCHER = ("@revalidate", ("not", "path", "/assets/*"))
IMMUTABLE_PREFIX = "/assets/"
NOT_FOUND_PAGE = "404.html"

# Caddy's types for the files a build writes (the deploy receiver's extension list).
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "text/xml; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/vnd.microsoft.icon",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

# A Caddyfile token: a quoted string (with backslash escapes) or a run of non-space characters.
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|(\S+)')
_ESCAPE = re.compile(r"\\(.)")


@dataclass(frozen=True, slots=True)
class CaddyHeaders:
    """Headers parsed from site.caddy: ``security`` for every response, then per route kind."""

    security: dict[str, str]
    remove: tuple[str, ...]
    immutable: dict[str, str]
    revalidate: dict[str, str]


@dataclass(slots=True)
class _Node:
    """One Caddyfile line (its tokens) and, when it opens a block, the lines inside."""

    tokens: list[str]
    children: list[_Node] | None = None


def _tokens(line: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN.finditer(line):
        quoted, bare = match.groups()
        if bare is not None and bare.startswith("#"):
            break  # a comment runs to the end of the line
        tokens.append(_ESCAPE.sub(r"\1", quoted) if quoted is not None else bare)
    return tokens


def _tree(text: str) -> list[_Node]:
    root: list[_Node] = []
    stack = [root]
    for number, line in enumerate(text.splitlines(), 1):
        tokens = _tokens(line)
        if not tokens:
            continue
        if tokens == ["}"]:
            if len(stack) == 1:
                raise ValueError(f"site.caddy line {number}: unmatched }}")
            stack.pop()
            continue
        if tokens[-1] == "{":
            node = _Node(tokens[:-1], [])
            stack[-1].append(node)
            stack.append(node.children)
        else:
            stack[-1].append(_Node(tokens))
    if len(stack) != 1:
        raise ValueError("site.caddy: a block is not closed")
    return root


def _snippet(tree: list[_Node], name: str) -> list[_Node]:
    for node in tree:
        if node.tokens == [f"({name})"] and node.children is not None:
            return node.children
    raise ValueError(f"site.caddy: no ({name}) snippet")


def _field(name: str) -> tuple[str, bool]:
    """Return a header field name without its prefix, and whether it removes the header."""
    name = name.removeprefix(">")  # deferred: applied when the response is written
    if name.startswith("-"):
        return name[1:], True
    return name.lstrip("+?"), False  # add, or set only if absent: the same for a fresh response


def _header_ops(node: _Node) -> tuple[str | None, list[tuple[str, str | None]]]:
    """Parse a ``header`` directive into (matcher or None, [(field, value or None to remove)])."""
    args = node.tokens[1:]
    matcher = args.pop(0) if args and args[0].startswith("@") else None
    lines = [args] if args else []
    if node.children is not None:
        lines += [child.tokens for child in node.children if child.tokens != ["defer"]]
    ops: list[tuple[str, str | None]] = []
    for line in lines:
        name, removes = _field(line[0])
        if removes:
            if len(line) != 1:
                raise ValueError(f"site.caddy: header -{name} takes no value")
            ops.append((name, None))
        elif len(line) == 2:
            ops.append((name, line[1]))
        else:
            raise ValueError(f"site.caddy: can't mirror header line {' '.join(line)!r}")
    return matcher, ops


def parse_headers(snippet: str) -> CaddyHeaders:
    """Parse the header lines of the ``tff_security`` and ``tff_site`` snippets.

    A field name may start with ``>`` (Caddy's deferred header, used for the
    ``@revalidate`` Cache-Control so that ``encode`` still compresses HTML); the
    parser strips it. A leading ``-`` removes a header (``-Server``).
    """
    tree = _tree(snippet)
    security: dict[str, str] = {}
    remove: list[str] = []
    by_matcher: dict[str, dict[str, str]] = {IMMUTABLE_MATCHER[0]: {}, REVALIDATE_MATCHER[0]: {}}
    matchers: dict[str, tuple[str, ...]] = {}

    def apply(matcher: str | None, ops: list[tuple[str, str | None]]) -> None:
        if matcher is not None and matcher not in by_matcher:
            raise ValueError(f"site.caddy: the preview doesn't know the matcher {matcher}")
        for name, value in ops:
            if matcher is None and value is None:
                remove.append(name)
            elif value is None:
                raise ValueError(f"site.caddy: can't mirror header {matcher} -{name}")
            else:
                (security if matcher is None else by_matcher[matcher])[name] = value

    for node in _snippet(tree, SECURITY_SNIPPET):
        if node.tokens[0] == "header":
            apply(*_header_ops(node))
    for node in _snippet(tree, SITE_SNIPPET):
        head = node.tokens[0]
        if head == "header":
            apply(*_header_ops(node))
        elif head == "import" and node.tokens[1:] == [SECURITY_SNIPPET]:
            continue  # read above
        elif head.startswith("@") and node.children is None:
            matchers[head] = tuple(node.tokens[1:])
        # encode, file_server and handle_errors (the 404 route re-applies the same headers)
        # need no mirroring here.

    for name, definition in (IMMUTABLE_MATCHER, REVALIDATE_MATCHER):
        if matchers.get(name) != definition:
            want = " ".join(definition)
            raise ValueError(f"site.caddy: the preview expects {name} {want}")
    immutable, revalidate = by_matcher[IMMUTABLE_MATCHER[0]], by_matcher[REVALIDATE_MATCHER[0]]
    if "Cache-Control" not in immutable or "Cache-Control" not in revalidate:
        raise ValueError("site.caddy: both routes need a Cache-Control header")
    return CaddyHeaders(
        security=security,
        remove=tuple(dict.fromkeys(remove)),
        immutable=immutable,
        revalidate=revalidate,
    )


class _Handler(SimpleHTTPRequestHandler):
    """Static files with site.caddy's headers; set ``headers`` and ``directory`` per server."""

    protocol_version = "HTTP/1.1"
    extensions_map: ClassVar[dict[str, str]] = {
        **SimpleHTTPRequestHandler.extensions_map,
        **CONTENT_TYPES,
    }
    timeout = 30  # drop idle keep-alive connections

    def __init__(self, *args: Any, headers: CaddyHeaders, **kwargs: Any) -> None:
        # BaseRequestHandler handles the request inside __init__, so set state first.
        self.tff_headers = headers
        self.tff_removed = {name.lower() for name in headers.remove}
        self.tff_status = 0
        super().__init__(*args, **kwargs)

    def send_response(self, code: int, message: str | None = None) -> None:
        self.tff_status = int(code)
        super().send_response(code, message)

    def send_header(self, keyword: str, value: str) -> None:
        if keyword.lower() not in self.tff_removed:
            super().send_header(keyword, value)

    def end_headers(self) -> None:
        for name, value in self.tff_headers.security.items():
            self.send_header(name, value)
        # A request line too broken to parse leaves no path; its 400 gets the revalidate route.
        path = unquote(urlsplit(getattr(self, "path", "")).path).lower()
        immutable = path.startswith(IMMUTABLE_PREFIX) and self.tff_status != HTTPStatus.NOT_FOUND
        route = self.tff_headers.immutable if immutable else self.tff_headers.revalidate
        for name, value in route.items():
            self.send_header(name, value)
        super().end_headers()

    def list_directory(self, path: str | Path) -> None:
        # Caddy's file_server doesn't list directories.
        self.send_error(HTTPStatus.NOT_FOUND)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        page = Path(self.directory) / NOT_FOUND_PAGE
        if code != HTTPStatus.NOT_FOUND or not page.is_file():
            super().send_error(code, message, explain)
            return
        body = page.read_bytes()
        self.log_error("code %d, message %s", code, message or "Not Found")
        self.send_response(code)
        self.send_header("Content-Type", CONTENT_TYPES[".html"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


class _ThreadingHTTPServerV6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def make_server(
    site_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    snippet: Path = SITE_CADDY,
) -> ThreadingHTTPServer:
    """Return a bound, not yet started server for ``site_dir``; port 0 picks a free port."""
    site = Path(site_dir)
    if not site.is_dir():
        raise FileNotFoundError(f"{site}: no built site there (run tff-site build first)")
    headers = parse_headers(Path(snippet).read_text(encoding="utf-8"))
    handler = partial(_Handler, headers=headers, directory=str(site.resolve()))
    server_class = _ThreadingHTTPServerV6 if ":" in host else ThreadingHTTPServer
    return server_class((host, port), handler)


def serve(
    site_dir: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    snippet: Path = SITE_CADDY,
) -> None:
    """Serve ``site_dir`` until interrupted."""
    try:
        server = make_server(site_dir, host=host, port=port, snippet=snippet)
    except OSError as exc:  # no built site, or the port is taken
        raise SystemExit(f"tff-site serve: {exc}") from None
    with server:
        bound_port = server.server_address[1]
        shown = f"[{host}]" if ":" in host else host
        print(
            f"Serving {site_dir} at http://{shown}:{bound_port}/ with the headers of "
            f"{snippet}. Press Ctrl-C to stop.",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Stopped.", file=sys.stderr)
