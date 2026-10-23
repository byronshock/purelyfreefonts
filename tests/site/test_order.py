"""The order tests/site runs in (tests/site/conftest.py, ``one_browser_and_module_at_a_time``).

pytest keys pytest-playwright's ``browser_name`` by the call's position in its function's
parametrization, so in files that parametrize further (test_owned_js.py's vectors,
test_keys_js.py's) its reordering interleaved the files and, with two browsers, the browsers:
each turn rebuilt the module fixtures, and each switch launched a browser. The conftest sorts
the collected tests by browser, then by module. This collects the two files with both browsers,
as deploy.yml runs tests/site, and checks that each browser and each file comes once.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = ("tests/site/test_keys_js.py", "tests/site/test_owned_js.py")


@pytest.mark.browser
def test_a_run_of_both_browsers_takes_each_browser_once_and_each_file_once_in_it():
    pytest.importorskip("pytest_playwright")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         *FILES, "--browser", "chromium", "--browser", "firefox"],
        cwd=ROOT, capture_output=True, text=True, timeout=300, check=True,
    )  # fmt: skip
    ids = [line for line in done.stdout.splitlines() if "::" in line]
    runs = [(line.split("::")[0], "firefox" if "[firefox" in line else "chromium") for line in ids]
    assert len(ids) > 2000
    browsers = [b for i, (_, b) in enumerate(runs) if i == 0 or runs[i - 1][1] != b]
    assert browsers == ["chromium", "firefox"]
    turns = [run for i, run in enumerate(runs) if i == 0 or runs[i - 1] != run]
    files = [f for f, b in turns if b == "chromium"]
    assert sorted(files) == sorted(FILES)
    assert turns == [(f, b) for b in ("chromium", "firefox") for f in files]
