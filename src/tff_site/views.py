"""The rank views' wording on the site: ``config/site.toml``'s over the catalog's copies.

``config/site.toml`` is the one home for the views' wording (site/CONTRACT.md section 1), and
Milestone 1's export copies it into ``catalog-site.json``. The owner's site ruling of
2026-10-07 (``view_labels_short``) renamed the views ("Overall", "Chosen (desktop)",
"Installed (desktop)", "Projects", "Coding", "Developers & apps"). A catalog rebuild before the
first live refresh would move scores and fail ``tff-catalog validate --committed``, so until
that refresh carries the new wording into the catalog, the site build reads config's
``[[views]]`` itself and shows each view's ``label`` and ``measures`` line from it, by key,
wherever the page names a view: the rank select, the list index (the measures line), the
details payload and the methodology page.

Everything else about a view stays the catalog's: which views there are, their order and
``available``. The catalog remains the record of what was published, and once a refresh has
copied the same wording this changes nothing (tests/site/test_views.py compares config with
the committed ``build/catalog-site.json``, which may differ from it only in wording). The keys
must match: a view that config names and the catalog lacks, or the other way round, fails the
build.

Read with ``tomllib``, not ``tff_catalog.config``, so the site build keeps to light imports
(the ``tff_site`` package docstring). ``CONFIG_PATH`` is found from this file, as ``site/`` and
``docs/`` are (``build.SITE_DIR``, ``pages.METHODOLOGY_MD``): the site is built from a checkout
(CI's site jobs, ``ops/deploy.sh``'s worktree), where ``config/`` sits beside ``src/``.
"""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "site.toml"
# The fields config gives the page; the rest of each view is the catalog's.
WORDING = ("label", "measures")


class ConfigError(ValueError):
    """``config/site.toml``'s views can't be read, or don't match the catalog's. ``errors``
    holds one line per problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s); first: {errors[0] if errors else '-'}")


def load(path: Path = CONFIG_PATH) -> dict[str, dict[str, str]]:
    """Return config's view wording, ``{key: {"label": …, "measures": …}}``, in its order.

    Each ``[[views]]`` entry needs a ``key``, a ``label`` and a ``measures`` line, all
    non-empty strings, and no key may appear twice (``tff_catalog.config`` checks the rest,
    such as the keys being rank keys)."""
    path = Path(path)
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError([f"{path}: {exc}"]) from exc
    views = doc.get("views")
    if not isinstance(views, list) or not views:
        raise ConfigError([f"{path}: no [[views]] entries"])
    errors: list[str] = []
    out: dict[str, dict[str, str]] = {}
    for i, view in enumerate(views):
        where = f"{path}: views[{i}]"
        if not isinstance(view, dict):
            errors.append(f"{where}: not a table")
            continue
        bad = [f for f in ("key", *WORDING) if not _text(view.get(f))]
        if bad:
            errors.append(f"{where}: needs a non-empty string for {', '.join(bad)}")
            continue
        if view["key"] in out:
            errors.append(f"{where}: key {view['key']!r} listed twice")
            continue
        out[view["key"]] = {field: view[field] for field in WORDING}
    if errors:
        raise ConfigError(errors)
    return out


def apply(doc: Mapping[str, Any], wording: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    """Return a shallow copy of ``doc`` whose views take ``wording``'s label and measures
    line, by key, keeping the catalog's order and every other field. ``doc`` is unchanged.

    Raises ``ConfigError`` unless the two name exactly the same views."""
    keys = [view["key"] for view in doc["views"]]
    errors = [
        f"config/site.toml names the view {key!r}, which the catalog lacks"
        for key in wording
        if key not in keys
    ] + [
        f"the catalog has the view {key!r}, which config/site.toml doesn't name"
        for key in keys
        if key not in wording
    ]
    if errors:
        raise ConfigError(errors)
    views = [{**view, **{f: wording[view["key"]][f] for f in WORDING}} for view in doc["views"]]
    return {**doc, "views": views}


def _text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""
