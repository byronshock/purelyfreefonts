"""A fake GitHub API for the github_releases tests: REST release pages and GraphQL.

``FakeGitHub(releases)`` serves, from REST-shaped release objects per repo
(newest first, as GitHub lists them):

- ``GET https://api.github.com/repos/<repo>/releases?per_page=&page=``: that
  slice, with a ``Link`` header naming the next page when there is one; 404 for
  a repo in ``missing``; an empty list for any other repo.
- ``POST https://api.github.com/graphql``: the two queries the collector sends
  (``RELEASES_QUERY`` and ``assets_query``), with opaque cursors. A missing repo
  gets GitHub's NOT_FOUND error. A query asking for more than ``node_limit``
  assets gets GitHub's RESOURCE_LIMITS_EXCEEDED error.

``Recorder`` wraps it and writes every exchange as a ``mockhttp`` fixture
(``tests/helpers/mockhttp.py``), for the contract test's ``http/`` directory.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
from tests.helpers import mockhttp

API = "https://api.github.com"
JSON = {"content-type": "application/json; charset=utf-8"}


def release(
    tag: str,
    assets: Iterable[tuple[str, int, str]],
    *,
    repo: str = "o/r",
    published: str | None = "2026-01-02T03:04:05Z",
    prerelease: bool = False,
    draft: bool = False,
    states: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """A REST release object with only the fields the collector reads (plus ``html_url``).

    ``assets`` are ``(name, download_count, created_at)``; ``states`` gives an
    asset a state other than ``uploaded``.
    """
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
        "draft": draft,
        "prerelease": prerelease,
        "published_at": published,
        "assets": [
            {
                "name": name,
                "state": (states or {}).get(name, "uploaded"),
                "download_count": count,
                "created_at": created,
            }
            for name, count, created in assets
        ],
    }


@dataclass
class FakeGitHub:
    """The fake API; ``requests`` lists ``(method, url)`` in order."""

    releases: Mapping[str, list[dict[str, Any]]]
    missing: frozenset[str] = frozenset()
    node_limit: int | None = None
    requests: list[tuple[str, str]] = field(default_factory=list)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url)))
        if request.url.host != "api.github.com":
            raise AssertionError(f"request outside api.github.com: {request.url}")
        if request.method == "POST" and request.url.path == "/graphql":
            return self._graphql(json.loads(request.content))
        parts = request.url.path.strip("/").split("/")
        if request.method == "GET" and len(parts) == 4 and parts[::3] == ["repos", "releases"]:
            query = dict(parse_qsl(request.url.query.decode()))
            return self._rest(f"{parts[1]}/{parts[2]}", query)
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    # --- REST ---------------------------------------------------------------------------------

    def _rest(self, repo: str, query: dict[str, str]) -> httpx.Response:
        if repo in self.missing:
            return httpx.Response(404, json={"message": "Not Found"}, headers=JSON)
        per_page, page = int(query.get("per_page", 30)), int(query.get("page", 1))
        items = list(self.releases.get(repo, ()))
        start = (page - 1) * per_page
        chunk = items[start : start + per_page]
        headers = dict(JSON)
        if start + per_page < len(items):
            nxt = f"{API}/repositories/1/releases?per_page={per_page}&page={page + 1}"
            headers["link"] = f'<{nxt}>; rel="next"'
        return httpx.Response(200, content=_body(chunk), headers=headers)

    # --- GraphQL ------------------------------------------------------------------------------

    def _graphql(self, doc: dict[str, Any]) -> httpx.Response:
        query, v = doc["query"], doc["variables"]
        repo = f"{v['owner']}/{v['name']}"
        if repo in self.missing:
            err = {
                "type": "NOT_FOUND",
                "path": ["repository"],
                "message": f"Could not resolve to a Repository with the name '{repo}'.",
            }
            return _gql({"repository": None}, [err])
        if "releases(" in query:
            asked = v["first"] * v["assets"]
            data = {"repository": self._releases(repo, v)}
        else:
            batch = sorted(k for k in v if k.startswith("t") and k[1:].isdigit())
            asked = len(batch) * v["n"]
            data = {"repository": {f"r{k[1:]}": self._assets(repo, v, k[1:]) for k in batch}}
        if self.node_limit is not None and asked > self.node_limit:
            err = {
                "type": "RESOURCE_LIMITS_EXCEEDED",
                "message": "Resource limits for this query exceeded.",
            }
            return _gql(data, [err])
        return _gql(data)

    def _releases(self, repo: str, v: Mapping[str, Any]) -> dict[str, Any]:
        items = list(self.releases.get(repo, ()))
        start = int(v["after"].split(":")[1]) if v.get("after") else 0
        chunk = items[start : start + v["first"]]
        end = start + len(chunk)
        return {
            "nameWithOwner": repo,
            "releases": {
                "totalCount": len(items),
                "pageInfo": {"hasNextPage": end < len(items), "endCursor": f"rel:{end}"},
                "nodes": [self._node(repo, r, v["assets"]) for r in chunk],
            },
        }

    def _node(self, repo: str, r: Mapping[str, Any], n: int) -> dict[str, Any]:
        return {
            "tagName": r["tag_name"],
            "publishedAt": r["published_at"],
            "isPrerelease": r["prerelease"],
            "isDraft": r["draft"],
            "releaseAssets": self._asset_page(r, 0, n),
        }

    def _assets(self, repo: str, v: Mapping[str, Any], i: str) -> dict[str, Any] | None:
        tag, cursor = v[f"t{i}"], v[f"c{i}"]
        found = [r for r in self.releases.get(repo, ()) if r["tag_name"] == tag]
        if not found:
            return None
        return {"releaseAssets": self._asset_page(found[0], int(cursor.split(":")[-1]), v["n"])}

    @staticmethod
    def _asset_page(r: Mapping[str, Any], start: int, n: int) -> dict[str, Any]:
        assets = [a for a in r["assets"] if a.get("state", "uploaded") == "uploaded"]
        chunk = assets[start : start + n]
        end = start + len(chunk)
        return {
            "pageInfo": {
                "hasNextPage": end < len(assets),
                "endCursor": f"asset:{r['tag_name']}:{end}",
            },
            "nodes": [
                {
                    "name": a["name"],
                    "downloadCount": a["download_count"],
                    "createdAt": a["created_at"],
                }
                for a in chunk
            ],
        }


def _body(obj: object) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _gql(data: object, errors: list[dict[str, Any]] | None = None) -> httpx.Response:
    doc: dict[str, Any] = {"data": data}
    if errors:
        doc["errors"] = errors
    return httpx.Response(200, content=_body(doc), headers=JSON)


# --- recording a fixture -------------------------------------------------------------------------


@dataclass
class Recorder:
    """Wraps a ``FakeGitHub``; ``write(directory)`` saves every exchange for ``mockhttp``."""

    fake: FakeGitHub
    entries: list[dict[str, Any]] = field(default_factory=list)
    bodies: dict[str, bytes] = field(default_factory=dict)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        response = self.fake.handle(request)
        body = response.read()
        name = self._body_name(request, body)
        entry: dict[str, Any] = {
            "method": request.method,
            "url": mockhttp.normalize_url(request.url),
            "status": response.status_code,
            "headers": {k: v for k, v in response.headers.items() if k in ("content-type", "link")},
            "body": name,
        }
        if request.method == "POST":
            entry["request_json"] = json.loads(request.content)
        self.entries.append(entry)
        return httpx.Response(response.status_code, headers=response.headers, content=body)

    def _body_name(self, request: httpx.Request, body: bytes) -> str:
        if body.strip() == b"[]":
            name = "empty.json"
        elif request.method == "POST":
            name = f"graphql-{hashlib.sha256(body).hexdigest()[:12]}.json"
        else:
            path = urlsplit(str(request.url)).path.split("/")
            page = dict(parse_qsl(request.url.query.decode())).get("page", "1")
            name = f"{path[2]}__{path[3]}-p{page}.json"
        self.bodies[name] = body
        return name

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name, body in sorted(self.bodies.items()):
            (directory / name).write_bytes(body)
        mockhttp.write_index(directory, self.entries)
