"""Tests for ``tff_catalog.fetch``: the shared fetcher (httpx.MockTransport) and the fetch stage.

No test sleeps or reads the wall clock: ``fake_time`` replaces the fetcher's
sleep and monotonic clock, and ``clock.frozen`` fixes ``fetched_at``. The stage
tests use the real snapshot store in a temporary directory.
"""

import dataclasses
import email.utils
import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from tests.helpers import mockhttp

from tff_catalog import __version__, clock, fetch, jsonio
from tff_catalog.collectors.base import CollectorBase, FetchContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import (
    BACKOFF_CAP,
    GITHUB_BUDGET,
    GITHUB_GRAPHQL_URL,
    MAX_RETRY_WAIT,
    USER_AGENT,
    Budget,
    BudgetExceeded,
    Fetcher,
    FetchError,
    FetchResult,
    HostNotAllowed,
    SourceFetch,
    backoff_delay,
    host_allowed,
    previous_record,
)
from tff_catalog.paths import Paths, StoreNotConfigured
from tff_catalog.stages import RunOptions, StageContext
from tff_catalog.state import State
from tff_catalog.store import FetchRecord, SnapshotFrozen, Store

NOW = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)
DAY = NOW.date()
URL = "https://example.org/data.json"


# --- a fake clock and a scripted server ---------------------------------------------------------


@dataclass
class FakeTime:
    now: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def fake_time(monkeypatch: pytest.MonkeyPatch) -> FakeTime:
    fake = FakeTime()
    monkeypatch.setattr(fetch, "_sleep", fake.sleep)
    monkeypatch.setattr(fetch, "_monotonic", fake.monotonic)
    return fake


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[None]:
    with clock.frozen(NOW):
        yield


@pytest.fixture(autouse=True)
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


Reply = tuple[int, dict[str, str], bytes] | Exception | Callable[[httpx.Request], httpx.Response]


def reply(status: int = 200, body: bytes = b"", **headers: str) -> Reply:
    return (status, {k.replace("_", "-"): v for k, v in headers.items()}, body)


class Server:
    """Scripted responses per (method, URL); the last reply of a route repeats."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[Reply]] = {}
        self.requests: list[httpx.Request] = []

    def on(self, url: str, *replies: Reply, method: str = "GET") -> None:
        self.routes[(method, url)] = list(replies)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, str(request.url)))
        if queue is None:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(request)
        status, headers, body = item
        return httpx.Response(status, headers=headers, content=body)

    def fetcher(self, **kwargs: Any) -> Fetcher:
        kwargs.setdefault("min_interval", {})
        return Fetcher(transport=self.transport, **kwargs)


@pytest.fixture
def server() -> Server:
    return Server()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- FetchResult and plain GETs ------------------------------------------------------------------


def test_user_agent_names_the_project_not_a_person() -> None:
    assert (
        f"trulyfreefonts-catalog/{__version__} (+https://github.com/byronshock/trulyfreefonts)"
    ) == USER_AGENT
    assert "@" not in USER_AGENT


def test_get_returns_body_hash_and_headers(server: Server) -> None:
    body = b'{"a": 1}'
    server.on(
        URL,
        reply(
            body=body,
            ETag='"v1"',
            Last_Modified="Fri, 02 Oct 2026 10:00:00 GMT",
            Content_Type="application/json",
        ),
    )
    with server.fetcher() as f:
        r = f.get(URL)
    assert server.requests[0].headers["user-agent"] == USER_AGENT
    assert (r.url, r.final_url, r.status) == (URL, URL, 200)
    assert (r.content, r.path, r.size, r.sha256) == (body, None, len(body), sha(body))
    assert r.fetched_at == NOW
    assert (r.etag, r.last_modified, r.not_modified) == (
        '"v1"',
        "Fri, 02 Oct 2026 10:00:00 GMT",
        False,
    )
    assert ("content-type", "application/json") in r.headers
    assert all(k == k.lower() for k, _ in r.headers)
    assert r.header("ETag") == '"v1"'
    assert r.json() == {"a": 1}
    assert r.text() == '{"a": 1}'
    assert f.last is r
    assert r.to_record(kept=True) == FetchRecord(
        url=URL,
        status=200,
        fetched_at="2026-10-03T06:00:00Z",
        sha256=sha(body),
        bytes=len(body),
        etag='"v1"',
        last_modified="Fri, 02 Oct 2026 10:00:00 GMT",
        kept=True,
    )


def test_params_become_part_of_the_recorded_url(server: Server) -> None:
    full = "https://example.org/api?q=font+x&limit=10"
    server.on(full, reply(body=b"[]"))
    with server.fetcher() as f:
        r = f.get("https://example.org/api", params={"q": "font x", "limit": 10})
    assert r.url == full == str(server.requests[0].url)
    assert r.to_record().url == full


@pytest.mark.parametrize("prefix", [b")]}'\n", b")]}',\n", b"  )]}'", b""])
def test_json_strips_an_optional_xssi_prefix(prefix: bytes) -> None:
    r = FetchResult(URL, URL, 200, (), prefix + b'{"x": [1]}', None, "", 0, NOW)
    assert r.json() == {"x": [1]}


def test_expect_allows_other_statuses(server: Server) -> None:
    server.on(URL, reply(404, b"gone"))
    with server.fetcher() as f:
        assert f.get(URL, expect=(200, 404)).status == 404
        with pytest.raises(FetchError, match=r"HTTP 404.*gone"):
            f.get(URL)
    assert len(server.requests) == 2  # a 404 is final: no retries


# --- scope: hosts and https ------------------------------------------------------------------------


def test_hosts_outside_the_scope_are_refused_before_any_request(server: Server) -> None:
    with server.fetcher(hosts=["Example.ORG"]) as f:
        with pytest.raises(HostNotAllowed, match=r"other\.org"):
            f.get("https://other.org/x")
        with pytest.raises(HostNotAllowed, match="https"):
            f.get("http://example.org/x")
        view = f.scoped(["example.org", "cdn.example.org"])
        assert view.hosts == {"example.org"}  # a view never widens
        with pytest.raises(HostNotAllowed):
            view.head("https://cdn.example.org/x")
    assert server.requests == []


def test_a_hosts_pattern_covers_its_leftmost_label_only(server: Server) -> None:
    server.on("https://doc-04-7g-sheets.example.com/x", reply(body=b"ok"))
    with server.fetcher(hosts=["doc-*-sheets.example.com"]) as f:
        assert f.get("https://doc-04-7g-sheets.example.com/x").content == b"ok"
        for url in (
            "https://other.example.com/x",
            "https://doc-1-sheets.example.org/x",
            "https://a.doc-1-sheets.example.com/x",
            "https://example.com/x",
        ):
            with pytest.raises(HostNotAllowed):
                f.get(url)
    assert host_allowed("DOC-9-SHEETS.example.com", ["doc-*-sheets.example.com"])
    assert not host_allowed("docsheets.example.com", ["doc-*-sheets.example.com"])
    for bad in ("*.example.com", "doc-*.*.com", "doc-*-sheets.com", "a.b-*.example.com"):
        with pytest.raises(ValueError, match="leftmost label"):
            Fetcher(hosts=[bad])


def test_an_unscoped_fetcher_allows_any_https_host(server: Server) -> None:
    server.on("https://anywhere.example/x", reply(body=b"ok"))
    with server.fetcher() as f:
        assert f.hosts is None
        assert f.get("https://anywhere.example/x").content == b"ok"
    with pytest.raises(TypeError):
        Fetcher(hosts="example.org")


def test_redirects_are_followed_hop_by_hop_and_checked(server: Server) -> None:
    server.on(URL, reply(302, location="https://cdn.example.net/data.json"))
    server.on("https://cdn.example.net/data.json", reply(301, location="/v2/data.json"))
    server.on("https://cdn.example.net/v2/data.json", reply(body=b"moved"))
    with server.fetcher() as f:
        scoped = f.scoped(["example.org"])
        with pytest.raises(HostNotAllowed, match=r"cdn\.example\.net"):
            scoped.get(URL)
        assert len(server.requests) == 1  # the refused hop was never sent
        r = f.scoped(["example.org", "cdn.example.net"]).get(URL)
    assert (r.url, r.final_url, r.content) == (
        URL,
        "https://cdn.example.net/v2/data.json",
        b"moved",
    )


def test_redirect_hops_are_listed_for_the_manifest(server: Server) -> None:
    body = b'{"moved": true}'
    cdn, final = "https://cdn.example.net/data.json", "https://cdn.example.net/v2/data.json"
    server.on(URL, reply(302, location=cdn))
    server.on(cdn, reply(301, location="/v2/data.json"))
    server.on(final, reply(body=body, etag='"e"'))
    server.on("https://example.org/direct", reply(body=b"x"))
    with server.fetcher() as f:
        r = f.get(URL)
        direct = f.get("https://example.org/direct")
    assert (r.redirects, r.final_url) == ((cdn, final), final)
    records = r.to_records(kept=True)
    assert [rec.url for rec in records] == [URL, cdn, final]  # every URL the server saw
    assert [str(q.url) for q in server.requests[:3]] == [URL, cdn, final]
    for rec in records:
        assert (rec.status, rec.sha256, rec.bytes, rec.etag, rec.kept) == (
            200,
            sha(body),
            len(body),
            '"e"',
            True,
        )
    assert direct.redirects == ()
    assert direct.to_records() == (direct.to_record(),)


def test_a_redirect_to_plain_http_is_refused(server: Server) -> None:
    server.on(URL, reply(302, location="http://example.org/data.json"))
    with server.fetcher() as f, pytest.raises(HostNotAllowed, match="https"):
        f.get(URL)
    assert len(server.requests) == 1


def test_303_turns_a_post_into_a_get(server: Server) -> None:
    server.on(URL, reply(303, location="https://example.org/result"), method="POST")
    server.on("https://example.org/result", reply(body=b"[]"))
    with server.fetcher() as f:
        assert f.post_json(URL, {"a": 1}).json() == []
    assert [r.method for r in server.requests] == ["POST", "GET"]
    assert server.requests[1].content == b""


def test_redirect_loops_fail(server: Server) -> None:
    server.on(URL, reply(302, location=URL))
    with server.fetcher(retries=0) as f, pytest.raises(FetchError, match="redirects"):
        f.get(URL)


# --- retries -----------------------------------------------------------------------------------


def test_5xx_is_retried_with_seeded_backoff(server: Server, fake_time: FakeTime) -> None:
    server.on(URL, reply(503), reply(502), reply(body=b"ok"))
    with server.fetcher() as f:
        assert f.get(URL).content == b"ok"
    assert len(server.requests) == 3
    assert fake_time.sleeps == [backoff_delay(URL, 0), backoff_delay(URL, 1)]


def test_backoff_doubles_is_capped_and_reproducible() -> None:
    for attempt in range(10):
        base = min(BACKOFF_CAP, 2.0 * 2**attempt)
        assert 0.5 * base <= backoff_delay(URL, attempt) < base
    assert backoff_delay(URL, 3) == backoff_delay(URL, 3)
    assert backoff_delay(URL, 0) != backoff_delay("https://example.org/other", 0)
    assert backoff_delay(URL, 20) <= BACKOFF_CAP


def test_connection_errors_are_retried_then_reported(server: Server) -> None:
    server.on(URL, httpx.ConnectError("refused"))
    with server.fetcher(retries=2) as f, pytest.raises(FetchError, match=r"ConnectError.*3 tries"):
        f.get(URL)
    assert len(server.requests) == 3


def test_a_read_error_mid_body_is_retried(server: Server) -> None:
    class Broken(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"partial"
            raise httpx.ReadError("connection reset")

    server.on(URL, lambda request: httpx.Response(200, stream=Broken()), reply(body=b"whole"))
    with server.fetcher() as f:
        assert f.get(URL).content == b"whole"


@pytest.mark.parametrize(
    ("headers", "wait"),
    [
        ({"Retry-After": "7"}, 7.0),
        (
            {"Retry-After": email.utils.format_datetime(NOW + timedelta(seconds=30), usegmt=True)},
            30.0,
        ),
    ],
)
def test_retry_after_is_honoured(
    server: Server, fake_time: FakeTime, headers: dict[str, str], wait: float
) -> None:
    server.on(URL, (429, headers, b""), reply(body=b"ok"))
    with server.fetcher() as f:
        assert f.get(URL).content == b"ok"
    assert fake_time.sleeps == [max(wait, backoff_delay(URL, 0))]


def test_a_retry_after_over_the_limit_fails_at_once(server: Server, fake_time: FakeTime) -> None:
    server.on(URL, reply(503, Retry_After=str(int(MAX_RETRY_WAIT) + 1)))
    with server.fetcher() as f, pytest.raises(FetchError, match="asks for a"):
        f.get(URL)
    assert fake_time.sleeps == []
    assert len(server.requests) == 1


def test_github_rate_limit_403_waits_for_the_reset(server: Server, fake_time: FakeTime) -> None:
    reset = str(int((NOW + timedelta(seconds=20)).timestamp()))
    api = "https://api.github.com/repos/o/r/releases"
    server.on(
        api, reply(403, x_ratelimit_remaining="0", x_ratelimit_reset=reset), reply(body=b"[]")
    )
    with server.fetcher() as f:
        assert f.get(api).json() == []
    assert fake_time.sleeps == [21.0]


@pytest.mark.parametrize("status", [403, 429])
def test_a_github_secondary_limit_without_headers_waits_a_minute(
    server: Server, fake_time: FakeTime, status: int
) -> None:
    api = "https://api.github.com/repos/o/r/releases"
    limited = b'{"message": "You have exceeded a secondary rate limit. Please wait."}'
    server.on(api, reply(status, limited), reply(body=b"[]"))
    with server.fetcher() as f:
        assert f.get(api).json() == []
    assert fake_time.sleeps == [fetch.GITHUB_SECONDARY_WAIT]


def test_a_plain_403_is_final(server: Server) -> None:
    server.on(URL, reply(403, b"forbidden"))
    # On the GitHub API too, when the body names no rate limit.
    api = "https://api.github.com/repos/o/r"
    server.on(api, reply(403, b'{"message": "Resource not accessible by integration"}'))
    with server.fetcher() as f:
        with pytest.raises(FetchError, match="HTTP 403"):
            f.get(URL)
        with pytest.raises(FetchError, match="not accessible"):
            f.get(api)
    assert len(server.requests) == 2


def test_a_body_that_fails_to_decode_is_retried_then_reported(server: Server) -> None:
    def garbled(request: httpx.Request) -> httpx.Response:
        stream = httpx.ByteStream(b"not gzip at all")  # decoded only when the fetcher reads it
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)

    server.on(URL, garbled, reply(body=b"ok"))
    with server.fetcher(retries=1) as f:
        assert f.get(URL).content == b"ok"
        server.on(URL, garbled)
        with pytest.raises(FetchError, match=r"DecodingError.*2 tries"):
            f.get(URL)


# --- pacing ---------------------------------------------------------------------------------------


def test_each_host_is_paced_and_views_share_the_pace(server: Server, fake_time: FakeTime) -> None:
    other = "https://other.org/x"
    server.on(URL, reply(body=b"a"))
    server.on(other, reply(body=b"b"))
    with Fetcher(transport=server.transport, min_interval={"other.org": 0.0}) as f:
        a, b = f.scoped(["example.org"]), f.scoped(["example.org"])
        a.get(URL)
        assert fake_time.sleeps == []
        b.get(URL)  # another view: still one host, one pace
        assert fake_time.sleeps == [fetch.DEFAULT_MIN_INTERVAL]
        f.get(other)
        f.get(other)  # 0.0 means no wait
        assert fake_time.sleeps == [fetch.DEFAULT_MIN_INTERVAL]
        fake_time.now += 5
        f.get(URL)  # the interval has passed
        assert len(fake_time.sleeps) == 1


def test_npm_downloads_are_paced_slower_by_default(server: Server, fake_time: FakeTime) -> None:
    npm = "https://api.npmjs.org/downloads/range/last-year/@fontsource/inter"
    server.on(npm, reply(body=b"{}"))
    with Fetcher(transport=server.transport) as f:
        f.get(npm)
        f.get(npm)
    assert fake_time.sleeps == [fetch.HOST_MIN_INTERVAL["api.npmjs.org"]] == [1.5]
    with Fetcher(transport=server.transport, min_interval={"api.npmjs.org": 0.0}) as f:
        f.get(npm)
        f.get(npm)  # a caller's own interval wins
    assert fake_time.sleeps == [1.5]


def test_min_interval_is_per_host(server: Server, fake_time: FakeTime) -> None:
    server.on(URL, reply(body=b"a"))
    with server.fetcher(min_interval={"example.org": 3.0}) as f:
        f.get(URL)
        fake_time.now += 1.0
        f.get(URL)
    assert fake_time.sleeps == [2.0]
    with pytest.raises(ValueError, match="negative"):
        Fetcher(min_interval={"example.org": -1.0})


# --- conditional GETs and streaming ----------------------------------------------------------------


def recorded(tmp_path: Path, **headers: str) -> mockhttp.MockHTTP:
    (tmp_path / "http").mkdir()
    (tmp_path / "http" / "body.json").write_bytes(b'{"v": 1}')
    mockhttp.write_index(
        tmp_path / "http", [{"url": URL, "status": 200, "headers": headers, "body": "body.json"}]
    )
    return mockhttp.MockHTTP.from_dir(tmp_path / "http")


def test_conditional_get_with_an_etag(tmp_path: Path) -> None:
    mock = recorded(tmp_path, etag='"abc"')
    with Fetcher(transport=mock.transport, min_interval={}) as f:
        first = f.get(URL)
        assert "if-none-match" not in mock.requests[0].headers
        again = f.get(URL, previous=first.to_record())
        stale = FetchRecord(URL, 200, "2026-09-01T00:00:00Z", "0" * 64, 1, etag='"old"')
        changed = f.get(URL, previous=stale)
    assert mock.requests[1].headers["if-none-match"] == '"abc"'
    assert (again.status, again.not_modified, again.content, again.sha256, again.size) == (
        304,
        True,
        b"",
        "",
        0,
    )
    assert again.etag == '"abc"'
    record = again.to_record()
    assert (record.status, record.sha256, record.bytes, record.etag) == (304, "", 0, '"abc"')
    assert (changed.status, changed.content) == (200, b'{"v": 1}')


def test_conditional_get_with_last_modified_keeps_the_validator(tmp_path: Path) -> None:
    stamp = "Thu, 01 Oct 2026 00:00:00 GMT"
    mock = recorded(tmp_path, **{"last-modified": stamp})
    previous = FetchRecord(URL, 200, "2026-10-01T00:00:00Z", "0" * 64, 8, last_modified=stamp)
    with Fetcher(transport=mock.transport, min_interval={}) as f:
        r = f.get(URL, previous=previous)
    assert mock.requests[0].headers["if-modified-since"] == stamp
    assert (r.not_modified, r.last_modified, r.etag) == (True, stamp, None)


def test_previous_record_finds_a_validated_entry_by_url() -> None:
    entries = (
        FetchRecord("https://Example.org/api?b=2&a=1", 200, "2026-09-01T00:00:00Z", "0" * 64, 1),
        FetchRecord(
            "https://example.org/api?a=1&b=2", 200, "2026-09-01T00:00:00Z", "1" * 64, 1, etag="x"
        ),
    )
    snap = SimpleNamespace(manifest=SimpleNamespace(fetched=entries))
    assert previous_record(snap, "https://example.org/api?b=2&a=1") == entries[1]
    assert previous_record(snap, "https://example.org/other") is None
    assert previous_record(None, URL) is None


def test_streaming_to_a_file(server: Server, tmp_path: Path) -> None:
    body = b'{"big": true}' * 1000
    server.on(URL, reply(body=body))
    target = tmp_path / "raw" / "big.json"
    with server.fetcher() as f:
        r = f.get(URL, to=target)
    assert (r.content, r.path, r.size, r.sha256) == (b"", target, len(body), sha(body))
    assert target.read_bytes() == body
    assert r.text() == body.decode()
    assert sorted(p.name for p in target.parent.iterdir()) == ["big.json"]


def test_a_304_leaves_the_stream_target_alone(tmp_path: Path) -> None:
    mock = recorded(tmp_path, etag='"abc"')
    target = tmp_path / "raw" / "data.json"
    previous = FetchRecord(URL, 200, "2026-10-01T00:00:00Z", "0" * 64, 8, etag='"abc"')
    with Fetcher(transport=mock.transport, min_interval={}) as f:
        r = f.get(URL, previous=previous, to=target)
    assert (r.status, r.not_modified, r.path, r.body()) == (304, True, None, b"")
    assert not target.parent.exists()


def test_a_failed_stream_leaves_no_partial_file(server: Server, tmp_path: Path) -> None:
    class Broken(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield b"partial"
            raise httpx.ReadError("connection reset")

    server.on(URL, lambda request: httpx.Response(200, stream=Broken()))
    target = tmp_path / "raw" / "x.bin"
    with server.fetcher(retries=1) as f, pytest.raises(FetchError, match="ReadError"):
        f.get(URL, to=target)
    assert list(target.parent.iterdir()) == []


# --- ranges, HEAD and POST -------------------------------------------------------------------------


def test_get_range_sends_a_range_and_accepts_206_or_a_full_200(server: Server) -> None:
    font = "https://example.org/Font.ttf"
    server.on(font, reply(206, b"\x00\x01\x00\x00"), reply(200, b"whole file"), reply(416))
    with server.fetcher() as f:
        part = f.get_range(font, 0, 3)
        whole = f.get_range(font, 0, 3)
        with pytest.raises(FetchError, match="416"):
            f.get_range(font, 100, 200)
        with pytest.raises(ValueError, match="range"):
            f.get_range(font, 5, 4)
    sent = server.requests[0].headers
    assert (sent["range"], sent["accept-encoding"]) == ("bytes=0-3", "identity")
    assert (part.status, part.content) == (206, b"\x00\x01\x00\x00")
    assert (whole.status, whole.content) == (200, b"whole file")


def test_head_returns_any_final_status(server: Server) -> None:
    server.on(URL, reply(301, location="https://example.org/new"), method="HEAD")
    server.on("https://example.org/new", reply(404), method="HEAD")
    server.on("https://example.org/busy", reply(503), method="HEAD")
    with server.fetcher(retries=1) as f:
        r = f.head(URL)
        busy = f.head("https://example.org/busy")
    assert (r.status, r.final_url, r.content) == (404, "https://example.org/new", b"")
    assert r.sha256 == sha(b"")  # recorded like any other request
    assert busy.status == 503
    assert [q.method for q in server.requests] == ["HEAD"] * 4  # the 503 was retried once


def test_post_json_sends_canonical_json(server: Server) -> None:
    lookup = "https://packages.example.org/api/v1/packages/bulk_lookup"
    server.on(lookup, reply(201, b'[{"purl": "x"}]'), method="POST")
    with server.fetcher(budgets=[Budget("ecosystems", 5)]) as f:
        r = f.post_json(lookup, {"purls": ["b", "a"], "a": 1}, budget="ecosystems")
        assert f.budget("ecosystems").used == 1
    sent = server.requests[0]
    assert sent.content == jsonio.canonical_bytes({"purls": ["b", "a"], "a": 1})
    assert sent.headers["content-type"] == "application/json"
    assert r.json() == [{"purl": "x"}]


# --- budgets and the GitHub token ----------------------------------------------------------------


def test_budget_spend() -> None:
    b = Budget("github", 2)
    b.spend()
    assert (b.used, b.remaining) == (1, 1)
    with pytest.raises(BudgetExceeded, match="github"):
        b.spend(2)
    assert b.used == 1
    b.spend()
    with pytest.raises(BudgetExceeded):
        b.spend()
    with pytest.raises(ValueError, match="spend"):
        b.spend(-1)


def test_a_spent_budget_refuses_before_sending(server: Server) -> None:
    server.on(URL, reply(body=b"ok"))
    with server.fetcher(budgets=[Budget("npm", 2)]) as f:
        f.get(URL, budget="npm")
        f.get(URL, budget="npm")
        with pytest.raises(BudgetExceeded):
            f.get(URL, budget="npm")
        with pytest.raises(KeyError, match="no budget named 'nope'"):
            f.get(URL, budget="nope")
        assert f.budget("npm").remaining == 0
    assert len(server.requests) == 2
    with pytest.raises(ValueError, match="twice"):
        Fetcher(budgets=[Budget("a", 1), Budget("a", 2)])


def test_every_github_api_request_is_charged_once(server: Server) -> None:
    api = "https://api.github.com/repos/o/r/releases"
    server.on(api, reply(502), reply(body=b"[]"))
    server.on(URL, reply(body=b"{}"))
    with server.fetcher() as f:
        view = f.scoped(["api.github.com", "example.org"])
        assert f.budget("github").limit == GITHUB_BUDGET
        view.get(api)  # a retry is a request too
        view.get(api, budget="github")
        view.get(URL)
        assert f.budget("github").used == 3
    with server.fetcher(budgets=[Budget("github", 1)]) as f:
        f.get(api)  # the 502 spends the whole budget
        with pytest.raises(BudgetExceeded):
            f.get(api)


def test_the_token_goes_to_the_github_api_only(
    server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    api = "https://api.github.com/repos/o/r/releases/assets/1"
    server.on(api, reply(302, location="https://objects.githubusercontent.com/asset"))
    server.on("https://objects.githubusercontent.com/asset", reply(body=b"zip"))
    server.on("https://raw.githubusercontent.com/o/r/main/x", reply(body=b"x"))
    with server.fetcher() as f:
        f.get(api, headers={"Accept": "application/octet-stream", "Cookie": "c=1"})
        f.get("https://raw.githubusercontent.com/o/r/main/x")
        assert f.budget("github").used == 1  # the API hop only, not the asset host
    first, hop, raw = server.requests
    assert first.headers["authorization"] == "Bearer secret-token"
    assert first.headers["accept"] == "application/octet-stream"  # the caller's wins
    assert first.headers["x-github-api-version"] == "2022-11-28"
    for request in (hop, raw):
        assert "authorization" not in request.headers
    assert "cookie" not in hop.headers


def test_anonymous_github_requests_still_work(server: Server) -> None:
    api = "https://api.github.com/rate_limit"
    server.on(api, reply(body=b"{}"))
    with server.fetcher() as f:
        f.get(api)
    assert "authorization" not in server.requests[0].headers
    assert server.requests[0].headers["accept"] == "application/vnd.github+json"


def test_graphql_returns_data_and_is_recordable(
    server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    server.on(
        GITHUB_GRAPHQL_URL,
        reply(body=b'{"data": {"repository": {"name": "r"}}}'),
        reply(body=b'{"data": null, "errors": [{"message": "Bad credentials"}]}'),
        method="POST",
    )
    with server.fetcher() as f:
        view = f.scoped(["api.github.com"])
        data = view.graphql("query($o: String!) { x }", {"o": "owner"})
        assert data == {"repository": {"name": "r"}}
        assert view.last is not None
        assert view.last.to_record().url == GITHUB_GRAPHQL_URL
        assert f.budget("github").used == 1
        with pytest.raises(FetchError, match="Bad credentials"):
            view.graphql("{ x }")
    sent = server.requests[0]
    assert json.loads(sent.content) == {
        "query": "query($o: String!) { x }",
        "variables": {"o": "owner"},
    }
    assert sent.headers["authorization"] == "Bearer secret-token"


def test_graphql_needs_a_token(server: Server) -> None:
    with server.fetcher() as f, pytest.raises(FetchError, match="GITHUB_TOKEN"):
        f.graphql("{ x }")
    assert server.requests == []


# --- lifetime --------------------------------------------------------------------------------------


def test_only_the_creating_fetcher_closes_the_client(server: Server) -> None:
    server.on(URL, reply(body=b"ok"))
    f = server.fetcher()
    view = f.scoped(["example.org"])
    view.close()
    assert f.get(URL).content == b"ok"
    f.close()
    with pytest.raises(RuntimeError):
        view.get(URL)
    with pytest.raises(ValueError, match="retries"):
        Fetcher(retries=-1)


# --- the fetch stage ------------------------------------------------------------------------------


def make_collector(
    name: str,
    fetch_fn: Callable[[FetchContext], None] | None = None,
    hosts: tuple[str, ...] = ("example.org",),
) -> Any:
    """A minimal collector whose fetch saves ``https://example.org/<name>.json``."""

    def default(ctx: FetchContext) -> None:
        url = f"https://example.org/{name}.json"
        r = ctx.fetcher.get(url, previous=previous_record(ctx.previous, url))
        ctx.out.record_fetch(r.to_record(kept=not r.not_modified))
        if r.not_modified:
            assert ctx.previous is not None
            ctx.out.copy_extract(ctx.previous, f"{name}.json")
        else:
            ctx.out.write_bytes(f"{name}.json", r.content)
        ctx.raw.file("scratch/clone.bin").write_bytes(b"raw")

    class Fake(CollectorBase):
        seen: ClassVar[list[FetchContext]] = []

        def fetch(self, ctx: FetchContext) -> None:
            self.seen.append(ctx)
            (fetch_fn or default)(ctx)

        def parse(self, ctx: object) -> tuple[()]:
            return ()

    Fake.name = name
    Fake.hosts = hosts
    return Fake()


@dataclass
class Stage:
    tmp: Path
    server: Server
    paths: Paths
    store: Store
    collectors: dict[str, Any] = field(default_factory=dict)
    state: State = field(default_factory=State)
    engine: dict[str, Any] = field(default_factory=dict)

    def add(self, collector: Any, *, enabled: bool = True, toml: str | None = None) -> Any:
        self.collectors[collector.name] = collector
        self.paths.sources_config.mkdir(parents=True, exist_ok=True)
        text = f"enabled = {'true' if enabled else 'false'}\n" if toml is None else toml
        (self.paths.sources_config / f"{collector.name}.toml").write_text(text)
        return collector

    def ctx(self, day: date = DAY, **options: Any) -> StageContext:
        config = SimpleNamespace(
            ranking=SimpleNamespace(
                stale=SimpleNamespace(max_months=2),
                sources=SimpleNamespace(all=lambda: self.engine),
            )
        )
        return StageContext(
            paths=self.paths,
            config=config,
            state=self.state,
            run_date=day,
            store=self.store,
            fetcher=self.server.fetcher(retries=1),
            log=logging.getLogger("test.fetch"),
            options=RunOptions(**options),
        )

    def run(self, day: date = DAY, **options: Any) -> tuple[SourceFetch, ...]:
        return fetch.fetch_all(self.ctx(day, **options), self.collectors)

    def serve(self, name: str, *replies: Reply) -> None:
        self.server.on(f"https://example.org/{name}.json", *replies)


@pytest.fixture
def stage(tmp_path: Path, server: Server) -> Stage:
    root = tmp_path / "store"
    root.mkdir()
    paths = Paths.for_root(tmp_path / "repo", store=root, raw_root=tmp_path / "raw")
    return Stage(tmp_path, server, paths, Store(root))


def test_stage_snapshots_each_enabled_collector(stage: Stage) -> None:
    alpha = stage.add(make_collector("alpha"))
    stage.add(make_collector("beta"), enabled=False)
    stage.serve("alpha", reply(body=b'{"a": 1}', etag='"e1"'))
    assert stage.run() == (SourceFetch("alpha", "fetched", DAY),)
    snap = stage.store.snapshot("alpha", DAY)
    assert snap is not None
    assert snap.load_json("alpha.json") == {"a": 1}
    [entry] = snap.manifest.fetched
    assert (entry.url, entry.status, entry.etag, entry.kept) == (
        "https://example.org/alpha.json",
        200,
        '"e1"',
        True,
    )
    assert stage.store.dates("beta") == []
    [ctx] = alpha.seen
    assert ctx.fetcher.hosts == {"example.org"}
    assert (ctx.previous, ctx.run_date, ctx.settings.enabled) == (None, DAY, True)
    assert not stage.paths.raw_dir(f"fetch-{DAY}").exists()  # raw files expire with the run


def test_one_snapshot_per_date_and_refetch_replaces_it(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.serve("alpha", reply(body=b'{"v": 1}'), reply(body=b'{"v": 2}'))
    assert stage.run()[0].outcome == "fetched"
    assert stage.run()[0].outcome == "kept"
    assert len(stage.server.requests) == 1
    assert stage.run(refetch=True)[0].outcome == "fetched"
    assert len(stage.server.requests) == 2
    assert sorted(p.name for p in (stage.store.root / "alpha").iterdir()) == [DAY.isoformat()]
    snap = stage.store.snapshot("alpha", DAY)
    assert snap is not None
    assert snap.load_json("alpha.json") == {"v": 2}


def test_incremental_fetch_gets_the_previous_snapshot(stage: Stage) -> None:
    alpha = stage.add(make_collector("alpha"))
    earlier = DAY - timedelta(days=30)

    def etagged(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == '"e1"':
            return httpx.Response(304, headers={"etag": '"e1"'})
        return httpx.Response(200, headers={"etag": '"e1"'}, content=b'{"v": 1}')

    stage.serve("alpha", etagged)
    stage.run(earlier)
    assert stage.run()[0].outcome == "fetched"  # the server answers 304 to the second GET
    assert alpha.seen[-1].previous.date == earlier
    assert stage.server.requests[1].headers["if-none-match"] == '"e1"'
    snap = stage.store.snapshot("alpha", DAY)
    assert snap is not None
    assert snap.load_json("alpha.json") == {"v": 1}
    assert snap.manifest.fetched[0].status == 304


def test_a_failed_source_falls_back_to_a_recent_snapshot(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.add(make_collector("beta"))
    stage.serve("alpha", reply(body=b"{}"), reply(500, b"down"))
    stage.serve("beta", reply(body=b"{}"))
    earlier = DAY - timedelta(days=40)
    stage.run(earlier)
    alpha, beta = stage.run()
    assert (alpha.outcome, alpha.snapshot, alpha.stale) == ("failed", earlier, True)
    assert alpha.error is not None
    assert "HTTP 500" in alpha.error
    assert beta == SourceFetch("beta", "fetched", DAY)
    assert stage.store.dates("alpha") == [earlier]
    assert sorted(p.name for p in (stage.store.root / "alpha").iterdir()) == [earlier.isoformat()]


def test_a_failed_refetch_keeps_the_existing_snapshot(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.serve("alpha", reply(body=b'{"v": 1}'), reply(503))
    stage.run()
    [alpha] = stage.run(refetch=True)
    assert (alpha.outcome, alpha.snapshot, alpha.stale) == ("failed", DAY, False)
    assert "HTTP 503" in (alpha.error or "")
    snap = stage.store.snapshot("alpha", DAY)
    assert snap is not None
    assert snap.load_json("alpha.json") == {"v": 1}
    assert sorted(p.name for p in (stage.store.root / "alpha").iterdir()) == [DAY.isoformat()]


def test_a_crashing_collector_leaves_no_snapshot_and_no_raw_files(stage: Stage) -> None:
    def crash(ctx: FetchContext) -> None:
        ctx.out.write_bytes("half.json", b"{}")
        ctx.raw.file("clone/pack.bin").write_bytes(b"raw")
        raise ValueError("the parser broke")

    stage.add(make_collector("alpha", crash))
    [alpha] = stage.run()
    assert (alpha.outcome, alpha.snapshot, alpha.error) == (
        "failed",
        None,
        "ValueError: the parser broke",
    )
    assert list((stage.store.root / "alpha").iterdir()) == []  # no <date>.tmp left behind
    assert not stage.paths.raw_dir(f"fetch-{DAY}").exists()


def test_an_incomplete_snapshot_is_replaced_without_refetch(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.serve("alpha", reply(body=b'{"v": 2}'))
    partial = stage.store.writer("alpha", DAY, 1)
    partial.write_bytes("alpha.json", b'{"v": 1}')
    partial.close(complete=False)
    assert stage.run() == (SourceFetch("alpha", "fetched", DAY),)
    snap = stage.store.snapshot("alpha", DAY)
    assert snap is not None
    assert snap.load_json("alpha.json") == {"v": 2}


def test_settings_without_an_enabled_switch_mean_always_on(stage: Stage) -> None:
    @dataclass(frozen=True, slots=True)
    class Settings:
        note: str

    collector = make_collector("alpha")
    type(collector).Settings = Settings
    stage.add(collector, toml='note = "x"\n')
    stage.serve("alpha", reply(body=b"{}"))
    assert stage.run() == (SourceFetch("alpha", "fetched", DAY),)  # as parse treats it
    assert collector.seen[0].settings == Settings(note="x")


def test_a_source_past_the_stale_window_drops_out(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.serve("alpha", reply(body=b"{}"), reply(500))
    stage.run(DAY - timedelta(days=63))  # two months are 62 days
    [alpha] = stage.run()
    assert (alpha.outcome, alpha.snapshot, alpha.stale) == ("failed", None, False)


def test_run_fails_only_when_every_source_fails(stage: Stage) -> None:
    ctx = stage.ctx()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fetch, "fetch_all", lambda c: (SourceFetch("a", "failed", None, error="x"),))
        with pytest.raises(FetchError, match="every source failed"):
            fetch.run(ctx)
        mp.setattr(
            fetch,
            "fetch_all",
            lambda c: (SourceFetch("a", "failed", None, error="x"), SourceFetch("b", "kept", DAY)),
        )
        fetch.run(ctx)
        mp.setattr(fetch, "fetch_all", lambda c: ())
        fetch.run(ctx)


def test_refetch_is_refused_for_a_merged_run(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.serve("alpha", reply(body=b"{}"))
    stage.run()
    stage.state = State(
        run_history=({"run_date": DAY.isoformat(), "snapshots": {"alpha": DAY.isoformat()}},)
    )
    assert stage.run()[0].outcome == "kept"
    with pytest.raises(SnapshotFrozen):
        stage.run(refetch=True)
    assert len(stage.server.requests) == 1


def test_a_refused_refetch_changes_nothing(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.add(make_collector("zeta"))
    stage.serve("alpha", reply(body=b'{"v": 1}'), reply(body=b'{"v": 2}'))
    stage.serve("zeta", reply(body=b"{}"))
    stage.run()
    # A later merged run used zeta's snapshot of DAY (say, standing in while zeta was down).
    later = (DAY + timedelta(days=30)).isoformat()
    stage.state = State(run_history=({"run_date": later, "snapshots": {"zeta": DAY.isoformat()}},))
    with pytest.raises(SnapshotFrozen, match=r"zeta.*nothing was fetched"):
        stage.run(refetch=True)
    assert len(stage.server.requests) == 2  # alpha was not refetched either
    snap = stage.store.snapshot("alpha", DAY)
    assert snap is not None
    assert snap.load_json("alpha.json") == {"v": 1}


def test_a_merged_run_date_without_a_snapshot_is_not_fetched(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.serve("alpha", reply(body=b"{}"))
    earlier = DAY - timedelta(days=20)
    stage.run(earlier)
    stage.state = State(
        run_history=({"run_date": DAY.isoformat(), "snapshots": {"alpha": earlier.isoformat()}},)
    )
    assert stage.run() == (SourceFetch("alpha", "frozen", earlier, stale=True),)
    assert len(stage.server.requests) == 1


def test_a_collector_cannot_leave_its_hosts(stage: Stage) -> None:
    def rogue(ctx: FetchContext) -> None:
        ctx.fetcher.get("https://elsewhere.example/x")

    stage.add(make_collector("alpha", rogue))
    [alpha] = stage.run()
    assert alpha.outcome == "failed"
    assert "HostNotAllowed" in (alpha.error or "")
    assert stage.server.requests == []


def test_only_selects_collectors_directly_or_by_engine_source(
    stage: Stage, caplog: pytest.LogCaptureFixture
) -> None:
    stage.add(make_collector("alpha"))
    stage.add(make_collector("beta"))
    stage.serve("alpha", reply(body=b"{}"))
    stage.serve("beta", reply(body=b"{}"))
    stage.engine = {"npm_fontsource": SimpleNamespace(collector="beta")}
    assert [r.source for r in stage.run(only=("alpha",))] == ["alpha"]
    with caplog.at_level(logging.WARNING):
        assert [r.source for r in stage.run(only=("npm_fontsource", "nosuch"))] == ["beta"]
    assert "--only nosuch" in caplog.text


def test_settings_are_checked_before_any_request(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.collectors["zeta"] = make_collector("zeta")  # no config/sources/zeta.toml
    with pytest.raises(ConfigError, match=r"zeta\.toml"):
        stage.run()
    assert stage.server.requests == []


def test_keep_raw_keeps_the_raw_files(stage: Stage) -> None:
    stage.add(make_collector("alpha"))
    stage.serve("alpha", reply(body=b"{}"))
    stage.run(keep_raw=True)
    kept = stage.paths.raw_dir(f"fetch-{DAY}") / "alpha" / "scratch" / "clone.bin"
    assert kept.read_bytes() == b"raw"


def test_the_stage_needs_a_fetcher_and_a_store(stage: Stage) -> None:
    ctx = stage.ctx()
    with pytest.raises(RuntimeError, match="replay"):
        fetch.fetch_all(dataclasses.replace(ctx, fetcher=None), {})
    with pytest.raises(StoreNotConfigured):
        fetch.fetch_all(dataclasses.replace(ctx, store=None, paths=ctx.paths.with_(store=None)), {})


# --- the real network -------------------------------------------------------------------------------


@pytest.mark.network
def test_real_conditional_and_range_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """raw.githubusercontent.com: a 304 on a repeated GET and a 206 for the first sfnt bytes."""
    monkeypatch.setattr(fetch, "_sleep", time.sleep)  # real pacing: this one talks to GitHub
    monkeypatch.setattr(fetch, "_monotonic", time.monotonic)
    base = "https://raw.githubusercontent.com/google/fonts/main/ofl/inter"
    with Fetcher(hosts=["raw.githubusercontent.com"]) as f:
        first = f.get(f"{base}/OFL.txt")
        assert first.status == 200
        assert b"SIL OPEN FONT LICENSE" in first.content.upper()
        assert first.etag
        again = f.get(f"{base}/OFL.txt", previous=first.to_record())
        assert again.not_modified
        assert again.status == 304
        head = f.get_range(f"{base}/Inter%5Bopsz,wght%5D.ttf", 0, 11)
        assert head.status == 206
        assert head.size == 12
        assert head.content[:4] == b"\x00\x01\x00\x00"
