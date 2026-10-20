"""Setup shared by every test (design-m1 §2.3 and §3).

- **No network** unless a test is marked ``network``. During every other test,
  connections and name lookups outside the loopback interface raise
  ``NetworkBlocked``, and git subprocesses may use only the ``file`` protocol
  (``GIT_ALLOW_PROTOCOL``). Loopback stays open for local servers (the site
  tests serve the built site on 127.0.0.1). An attempt that the code under test
  catches and swallows still fails the test.
- **Hypothesis**: the ``ci`` profile (derandomized, 200 examples, no deadline,
  because CI runners are slow and shared) is loaded when ``CI=true``, as GitHub
  Actions sets it.
- **Store tests** (marked ``store``) skip when ``TFF_STORE`` is not a directory,
  as in public CI.
- Fixtures ``synth_store`` and ``synth_state``: a fresh synthetic snapshot store
  and state S0 per test (``tests/helpers/synth.py``); ``network_attempts``: the
  blocked attempts so far, for a test of the guard itself to clear.
"""

import ipaddress
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from hypothesis import settings
from tests.helpers import synth

settings.register_profile("ci", derandomize=True, max_examples=200, deadline=None)
if os.environ.get("CI", "").strip().lower() in {"true", "1"}:
    settings.load_profile("ci")


# --- network guard --------------------------------------------------------------------------


class NetworkBlocked(AssertionError):
    """A test not marked ``network`` tried to reach the network."""


_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
_attempts: list[str] = []
_attempts_key = pytest.StashKey[list[str]]()


def _is_local(host: Any) -> bool:
    if host is None or host in ("", b""):
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host)
    if host.lower() in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _allowed(sock: socket.socket, address: Any) -> bool:
    if sock.family == getattr(socket, "AF_UNIX", None):
        return True
    return isinstance(address, tuple) and bool(address) and _is_local(address[0])


def _block(what: str) -> None:
    _attempts.append(what)
    raise NetworkBlocked(f"network access in a test not marked 'network': {what}")


def _install_guard(mp: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname
    real_gethostbyname_ex = socket.gethostbyname_ex

    def connect(self: socket.socket, address: Any) -> None:
        if not _allowed(self, address):
            _block(f"connect {address!r}")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if not _allowed(self, address):
            _block(f"connect {address!r}")
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _is_local(host):
            _block(f"name lookup {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def gethostbyname(host: str) -> str:
        if not _is_local(host):
            _block(f"name lookup {host!r}")
        return real_gethostbyname(host)

    def gethostbyname_ex(host: str) -> Any:
        if not _is_local(host):
            _block(f"name lookup {host!r}")
        return real_gethostbyname_ex(host)

    mp.setattr(socket.socket, "connect", connect)
    mp.setattr(socket.socket, "connect_ex", connect_ex)
    mp.setattr(socket, "getaddrinfo", getaddrinfo)
    mp.setattr(socket, "gethostbyname", gethostbyname)
    mp.setattr(socket, "gethostbyname_ex", gethostbyname_ex)
    mp.setenv("GIT_ALLOW_PROTOCOL", "file")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[Any]:
    """Guard the whole test, fixtures of every scope included, unless marked ``network``."""
    if item.get_closest_marker("network") is not None:
        return (yield)
    mp = pytest.MonkeyPatch()
    _attempts.clear()
    item.stash[_attempts_key] = _attempts
    _install_guard(mp)
    try:
        return (yield)
    finally:
        mp.undo()
        _attempts.clear()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[Any]:
    """Fail a passing test whose code caught and swallowed a ``NetworkBlocked``."""
    report = yield
    attempts = item.stash.get(_attempts_key, None)
    if report.when == "call" and report.passed and attempts:
        report.outcome = "failed"
        report.longrepr = "network access attempted and swallowed:\n  " + "\n  ".join(attempts)
    return report


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("store") is not None:
        store = os.environ.get("TFF_STORE", "")
        if not store or not Path(store).expanduser().is_dir():
            pytest.skip("needs TFF_STORE (the private snapshot store)")


# --- fixtures -------------------------------------------------------------------------------


@pytest.fixture
def network_attempts() -> list[str]:
    """Blocked attempts so far in this test; a test that expects some clears the list."""
    return _attempts


@pytest.fixture
def synth_store(tmp_path: Path) -> Path:
    """A fresh synthetic snapshot store (``synth.make_store``)."""
    return synth.make_store(tmp_path / "store")


@pytest.fixture
def synth_state(tmp_path: Path, synth_store: Path) -> Path:
    """State S0 matching ``synth_store`` (``synth.make_state``)."""
    return synth.make_state(tmp_path / "state", synth_store)


@pytest.fixture(autouse=True)
def _engine_only_config(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rank on the engine unless the test is marked ``model_config``
    (``tests.helpers.engine_only``): ``load_config`` reads ranking.toml through
    ``config.from_mapping``, so every stage, the CLI and ``config_hash`` see the same."""
    if request.node.get_closest_marker("model_config"):
        return
    from tests.helpers import engine_only

    from tff_catalog import config
    from tff_catalog.config_model import RankingConfig

    real = config.from_mapping

    def from_mapping(cls: Any, data: Any, where: str = "") -> Any:
        out = real(cls, data, where=where)
        return engine_only(out) if cls is RankingConfig else out

    monkeypatch.setattr(config, "from_mapping", from_mapping)
