"""Offline network fixtures: recorded HTTP responses and local git remotes.

**HTTP.** A fixture directory (``tests/fixtures/collectors/<name>/http/``) holds
``index.json`` and one body file per response::

    {"format": "tff-mockhttp", "version": 1, "responses": [
      {"method": "GET", "url": "https://formulae.brew.sh/api/cask.json", "status": 200,
       "headers": {"content-type": "application/json", "etag": "\\"abc\\""},
       "body": "cask.json"}
    ]}

- ``url`` matches the request URL with the host lower-cased and the query
  parameters sorted, so the order a collector builds them in does not matter.
- Optional ``request_headers`` (each must be present with that value, e.g. a
  ``range``) and ``request_json`` (the POST body, compared as JSON) narrow a
  match. The first matching entry wins.
- ``body`` is a file name inside the directory, sent byte for byte, or null.
  A gzipped body with ``content-encoding: gzip`` arrives decoded, as on the wire.
- A request carrying ``If-None-Match`` equal to the entry's ``etag``, or
  ``If-Modified-Since`` equal to its ``last-modified``, gets a 304.
- Any other request is recorded in ``unmatched`` and raises ``UnrecordedRequest``,
  so a collector cannot reach a URL its fixture does not list.

**git.** ``git_remotes(git_dir, work_dir, day)`` turns plain files under
``git/<host>/<owner>/<repo>/`` into local repositories with one commit (fixed
author, committer and dates, so the commit sha is stable), and returns the
environment that makes ``https://<host>/<owner>/<repo>[.git]`` clone from them
(``url.<local>.insteadOf``). Apply it with ``monkeypatch.setenv``.
"""

import json
import os
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

INDEX = "index.json"
FORMAT = "tff-mockhttp"
VERSION = 1


class UnrecordedRequest(AssertionError):
    """A request that no entry of the fixture's ``index.json`` matches."""


def normalize_url(url: str | httpx.URL) -> str:
    """The URL with scheme and host lower-cased, query parameters sorted, no fragment."""
    parts = urlsplit(str(url))
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


@dataclass(frozen=True, slots=True)
class Recorded:
    """One entry of ``index.json``."""

    method: str
    url: str  # normalised
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    request_headers: tuple[tuple[str, str], ...] = ()
    request_json: Any = None
    has_request_json: bool = False

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers if k.lower() == name), None)

    def matches(self, request: httpx.Request) -> bool:
        if request.method != self.method or normalize_url(request.url) != self.url:
            return False
        if any(request.headers.get(k) != v for k, v in self.request_headers):
            return False
        if self.has_request_json:
            try:
                sent = json.loads(request.content or b"null")
            except ValueError:
                return False
            if sent != self.request_json:
                return False
        return True


@dataclass(slots=True)
class MockHTTP:
    """Recorded responses, the requests made against them, and an ``httpx`` transport."""

    responses: tuple[Recorded, ...]
    requests: list[httpx.Request] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    @classmethod
    def from_dir(cls, directory: Path) -> MockHTTP:
        """Load ``directory/index.json`` and its body files."""
        directory = Path(directory)
        index = json.loads((directory / INDEX).read_text(encoding="utf-8"))
        if index.get("format") != FORMAT or index.get("version") != VERSION:
            raise ValueError(f"{directory / INDEX}: not a {FORMAT} v{VERSION} index")
        return cls(tuple(_entry(directory, e) for e in index["responses"]))

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def hosts(self) -> set[str]:
        """Every host a request went to."""
        return {r.url.host for r in self.requests}

    def urls(self) -> list[str]:
        """Every requested URL (normalised), in request order."""
        return [normalize_url(r.url) for r in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for rec in self.responses:
            if rec.matches(request):
                if _not_modified(request, rec):
                    return httpx.Response(304, headers=[h for h in rec.headers if _keep_304(h)])
                body = b"" if request.method == "HEAD" else rec.body
                return httpx.Response(rec.status, headers=list(rec.headers), content=body)
        where = f"{request.method} {normalize_url(request.url)}"
        self.unmatched.append(where)
        raise UnrecordedRequest(f"no recorded response for {where}")


def write_index(directory: Path, responses: Iterable[Mapping[str, Any]]) -> Path:
    """Write ``index.json`` (for building fixtures); bodies are written by the caller."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    doc = {"format": FORMAT, "version": VERSION, "responses": list(responses)}
    path = directory / INDEX
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _entry(directory: Path, e: Mapping[str, Any]) -> Recorded:
    known = {"method", "url", "status", "headers", "body", "request_headers", "request_json"}
    unknown = set(e) - known
    if unknown:
        raise ValueError(f"{directory / INDEX}: unknown fields {sorted(unknown)} in {e!r}")
    body_name = e.get("body")
    body = b""
    if body_name is not None:
        path = (directory / body_name).resolve()
        if directory.resolve() not in path.parents:
            raise ValueError(f"{directory / INDEX}: body {body_name!r} is outside the directory")
        body = path.read_bytes()
    return Recorded(
        method=e.get("method", "GET").upper(),
        url=normalize_url(e["url"]),
        status=int(e.get("status", 200)),
        headers=tuple((k.lower(), str(v)) for k, v in sorted(e.get("headers", {}).items())),
        body=body,
        request_headers=tuple(
            (k.lower(), str(v)) for k, v in sorted(e.get("request_headers", {}).items())
        ),
        request_json=e.get("request_json"),
        has_request_json="request_json" in e,
    )


def _not_modified(request: httpx.Request, rec: Recorded) -> bool:
    if rec.status != 200:
        return False
    etag = rec.header("etag")
    modified = rec.header("last-modified")
    return (etag is not None and request.headers.get("if-none-match") == etag) or (
        modified is not None and request.headers.get("if-modified-since") == modified
    )


def _keep_304(header: tuple[str, str]) -> bool:
    return header[0] in ("etag", "last-modified", "cache-control", "date")


# --- git ------------------------------------------------------------------------------------

_GIT_IDENTITY = {"name": "tff fixture", "email": "fixture@trulyfreefonts.invalid"}


def git_remotes(git_dir: Path, work_dir: Path, day: date) -> dict[str, str]:
    """Build a local repository for each ``git/<host>/<owner>/<repo>/``; return the rewrite env.

    Each repository has one commit on ``main``, its default branch. An optional
    sibling file ``git/<host>/<owner>/<repo>.branches`` names more branches, one
    per line (blank lines and ``#`` comments skipped), created at that commit, so
    a collector can pin a named branch (fontist's ``v5``).
    """
    git_dir = Path(git_dir)
    stamp = f"{day.isoformat()}T00:00:00+00:00"
    # Drop inherited GIT_* variables: under a git hook, GIT_DIR would redirect every command.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": _GIT_IDENTITY["name"],
        "GIT_AUTHOR_EMAIL": _GIT_IDENTITY["email"],
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_NAME": _GIT_IDENTITY["name"],
        "GIT_COMMITTER_EMAIL": _GIT_IDENTITY["email"],
        "GIT_COMMITTER_DATE": stamp,
    }
    rules: list[tuple[str, str]] = []
    for repo in sorted(p for p in git_dir.glob("*/*/*") if p.is_dir()):
        rel = repo.relative_to(git_dir)
        local = Path(work_dir) / rel
        _copy_tree(repo, local)
        for args in (
            ["init", "-q", "-b", "main"],
            ["config", "uploadpack.allowFilter", "true"],
            ["config", "uploadpack.allowAnySHA1InWant", "true"],
            ["add", "-A"],
            ["commit", "-q", "--no-gpg-sign", "-m", f"fixture {rel.as_posix()}"],
        ):
            subprocess.run(["git", "-C", str(local), *args], env=env, check=True)
        for branch in _branches(repo.with_name(repo.name + ".branches")):
            subprocess.run(["git", "-C", str(local), "branch", branch], env=env, check=True)
        remote = f"https://{rel.as_posix()}"
        rules += [(local.as_uri(), remote + ".git"), (local.as_uri(), remote)]
    out = {"GIT_CONFIG_COUNT": str(len(rules))}
    for i, (local_uri, remote) in enumerate(rules):
        out[f"GIT_CONFIG_KEY_{i}"] = f"url.{local_uri}.insteadOf"
        out[f"GIT_CONFIG_VALUE_{i}"] = remote
    return out


def _branches(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _copy_tree(src: Path, dest: Path) -> None:
    for path in sorted(src.rglob("*")):
        target = dest / path.relative_to(src)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    dest.mkdir(parents=True, exist_ok=True)
