"""Fixtures for the specimen tests.

- ``pinned_fonts``: {key: (Pin, bytes)} for the five real OFL fonts. Tests using it
  are marked ``network``: a missing file is downloaded into ``~/.cache/tff/fonts``,
  and the test skips when that fails (offline without a cache).
- ``quiet_log``: a logger for the stage functions.
"""

import logging

import pytest
from tests.specimens import pinned


@pytest.fixture(scope="session")
def pinned_fonts() -> dict[str, tuple[pinned.Pin, bytes]]:
    out = {}
    for pin in pinned.pins():
        data = pinned.cached(pin)
        if data is None:
            try:
                data = pinned.download(pin)
            except Exception as exc:  # offline, or the host is unreachable
                pytest.skip(f"pinned font {pin.key} is not cached and can't be fetched: {exc}")
        out[pin.key] = (pin, data)
    return out


@pytest.fixture
def quiet_log() -> logging.Logger:
    return logging.getLogger("tests.specimens")
