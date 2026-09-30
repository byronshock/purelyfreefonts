"""The live ``version.txt`` (Milestone 2 steps 11 and 14): the contract's fields in order, the
deployed commit (``--expect-commit``), and the same commit and run date as the list index the
page loads, so the HTML and its assets come from one build.
"""

import json
from typing import Any

from tests.live import checks


def test_version_txt_names_the_deployed_commit(
    crawled: tuple[list[Any], list[str]], expect_commit: str | None
) -> None:
    fetched, _ = crawled
    version = next(f for f in fetched if f.path == "/version.txt")
    assert version.status == 200
    lists = [f for f in fetched if f.kind == "json" and "/assets/list." in f.path]
    index = json.loads(lists[0].body) if lists else None
    text = version.body.decode("utf-8")
    assert checks.version_problems(text, expect_commit=expect_commit, index=index) == []


def test_the_footer_shows_the_run_date(crawled: tuple[list[Any], list[str]]) -> None:
    fetched, _ = crawled
    version = next(f for f in fetched if f.path == "/version.txt")
    run_date = checks.parse_version(version.body.decode("utf-8")).get("run_date")
    home = next(f for f in fetched if f.path == "/")
    assert f"Data from {run_date}." in home.body.decode("utf-8")
