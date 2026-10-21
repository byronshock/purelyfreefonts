"""The Project and Developers & apps terms (engine/builders.py) and their reading from a
run's stage (modelinputs.py), on synthetic stage files. Every number here is made up."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tff_catalog import modelinputs
from tff_catalog.engine import builders
from tff_catalog.paths import Paths

RUN = date(2026, 10, 17)


def entry(
    state: str = "observed", value: float = 1.0, flags: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {"state": state, "value": value, "flags": list(flags), "factor": 1.0, "group": "g"}


def fot(
    week: str, end: str, key: str, category: str, sites: float, crawled: int, method: str = "m"
):
    return builders.FotRow(week, date.fromisoformat(end), key, category, method, sites, crawled)


def test_a_frame_takes_the_counted_terms_less_l3_failures():
    entries = {
        "b": entry("censored", 0.0),
        "a": entry("observed"),
        "c": entry("too_new"),
        "d": entry("not_covered"),
        "e": entry("observed"),
    }
    assert list(builders.frame(entries, frozenset({"e"}))) == ["a", "b"]


def test_age_share_is_the_share_of_the_year_at_most_one():
    assert builders.age_share(None, RUN) == 1.0
    assert builders.age_share(date(2026, 4, 17), RUN) == pytest.approx(183 / 365)
    assert builders.age_share(date(2020, 1, 1), RUN) == 1.0
    assert builders.age_share(date(2026, 11, 1), RUN) == 0.0


def test_fonts_over_time_is_the_month_s_weekly_mean_in_two_terms():
    rows = [
        # an old week, more than 31 days before the newest crawl: not this month's
        fot("2026-W36", "2026-09-06", "Inter", "others", 99, 1000),
        fot("2026-W39", "2026-09-27", "Inter", "others", 3, 1000),
        fot("2026-W39", "2026-09-27", "Inter", "startups", 1, 400),
        fot("2026-W40", "2026-10-04", "Inter", "others", 6, 1100),
        fot("2026-W40", "2026-10-04", "Inter", "startups", 2, 500),
        fot("2026-W41", "2026-10-11", "Inter", "others", 9, 1200),
        fot("2026-W41", "2026-10-11", "Inter", "startups", 0, 600),
        fot("2026-W41", "2026-10-11", "Lato", "others", 3, 1200),  # missing weeks count 0
        fot("2026-W41", "2026-10-11", "Lato", "others", 3, 1200, method="n"),
        fot("2026-W41", "2026-10-11", "Unmapped", "others", 7, 1200),
    ]
    assert builders.month_weeks(rows) == ["2026-W39", "2026-W40", "2026-W41"]
    links = {"Inter": "inter", "Lato": "lato"}
    startups, others = builders.fot_terms(rows, links, ["lato", "inter", "roboto"])
    assert (startups.name, others.name) == (builders.FOT_STARTUPS, builders.FOT_OTHERS)
    assert others.ids.tolist() == ["inter", "lato", "roboto"]
    assert others.y.tolist() == [6.0, 2.0, 0.0]  # (3 + 6 + 9) / 3; (3 + 3) / 3; none
    assert startups.y.tolist() == [1.0, 0.0, 0.0]
    # Each method's sites crawled, summed for the group, averaged over the weeks.
    assert others.exposure.tolist() == [(1000 + 1100 + 2 * 1200) / 3] * 3
    assert startups.exposure.tolist() == [500.0] * 3
    for term in (startups, others):
        assert term.dist == "negbin"
        assert term.n2d.sum() == 0  # no bulk


def test_one_week_of_fonts_over_time_is_that_week():
    rows = [fot("2026-W39", "2026-09-27", "Inter", "others", 5, 1000)]
    _, others = builders.fot_terms(rows, {"Inter": "inter"}, ["inter"])
    assert others.y.tolist() == [5.0]
    assert others.exposure.tolist() == [1000.0]


def test_the_almanac_is_a_list_with_intervals_below_it_and_merged_parents_upper_bounds():
    entries = {
        "a": entry("observed", 50.0),
        "b": entry("observed", 20.0),
        "c": entry("censored", 0.0),
        "d": entry("observed", 80.0, ("parent_merge",)),
    }
    term = builders.almanac_term(entries)
    assert term.ids.tolist() == ["a", "b", "c", "d"]
    assert term.y.tolist() == [50.0, 20.0, 0.0, 0.0]
    assert term.y_hi.tolist() == [50.0, 20.0, 20.0, 80.0]  # c: [0, smallest); d: [0, y]
    assert term.dist == "lognormal"
    assert term.n2d.sum() == 0


def test_traffic_sums_packages_each_with_an_age_scaled_bulk():
    packages = [
        ("@fontsource/inter", "inter", 100.0, None),
        ("@fontsource-variable/inter", "inter", 50.0, date(2026, 4, 17)),
        ("@fontsource/lato", "lato", 7.0, date(2020, 1, 1)),
        ("@fontsource/unlisted", "unlisted", 9.0, None),  # outside the frame
    ]
    term = builders.traffic_term("npm", packages, ["lato", "inter", "roboto"], RUN)
    assert term.ids.tolist() == ["inter", "lato"]  # roboto has no package: no term
    assert term.y.tolist() == [150.0, 7.0]
    assert term.n.tolist() == pytest.approx([1 + 183 / 365, 1.0])
    flat = builders.traffic_term("e", packages, ["inter"], RUN, age=False, dist="negbin")
    assert (flat.n.tolist(), flat.dist) == ([2.0], "negbin")


def test_jsdelivr_zeros_are_not_covered():
    packages = [("inter", "inter", 10.0, None), ("lato", "lato", 0.0, None)]
    term = builders.traffic_term("j", packages, ["inter", "lato"], RUN, age=False, drop_zero=True)
    assert term.ids.tolist() == ["inter"]


def test_google_has_one_bulk_a_font_however_many_keys():
    views = [("Inter", "inter", 10.0), ("Inter Tight", "inter", 5.0), ("Lato", "lato", 3.0)]
    added = {"inter": date(2026, 4, 17), "lato": None}
    term = builders.google_term(views, added, ["inter", "lato"], RUN)
    assert term.y.tolist() == [15.0, 3.0]
    assert term.n.tolist() == pytest.approx([183 / 365, 1.0])
    assert term.dist == "lognormal"


def test_the_models_and_their_fixed_baselines(tmp_path: Path):
    paths = Paths.for_root(tmp_path)
    write_stage(paths)
    models = modelinputs.models(paths, RUN)
    project, fixed = models["project"]
    assert [t.name for t in project.counts] == list(builders.PROJECT_TERMS)
    # Fonts Over Time's two terms and the Almanac have no bulk: their baselines are held.
    offsets = project.offsets()
    assert sorted(fixed) == [offsets[0], offsets[1], offsets[2]]
    dev, dev_fixed = models["dev_apps"]
    assert [t.name for t in dev.counts] == list(builders.DEV_APPS_TERMS)
    assert dev_fixed == {}
    by_name = {t.name: t for t in project.counts}
    assert by_name[builders.GOOGLE].ids.tolist() == ["inter"]  # lato: L3-failed
    assert by_name[builders.GOOGLE].y.tolist() == [100.0]  # the year's views only
    assert by_name[builders.GOOGLE].n.tolist() == pytest.approx([183 / 365])
    npm = by_name[builders.NPM_FONTSOURCE]
    assert npm.ids.tolist() == ["inter"]  # lato is L3-failed, out of every frame
    # npm's data date is 5 days before the run: the young package's age ends there (q7)
    assert npm.n.tolist() == pytest.approx([1.0 + 178 / 365])
    expo = by_name[builders.EXPO]
    assert expo.ids.tolist() == ["inter"]
    assert by_name[builders.JSDELIVR].ids.tolist() == ["inter"]  # lato: zero hits
    assert by_name[builders.ECOSYSTEMS].n.tolist() == [2.0]
    others = by_name[builders.FOT_OTHERS]
    assert others.y.tolist() == [4.0]  # (6 + 2) / 2 weeks
    assert by_name[builders.ALMANAC].y_hi.tolist() == [30.0]


def test_without_window_ends_every_age_is_measured_to_the_run_date(tmp_path: Path):
    """As the reference implementation measured them (the parity test)."""
    paths = Paths.for_root(tmp_path)
    write_stage(paths)
    project, _ = modelinputs.models(paths, RUN, window_ends=False)["project"]
    npm = next(t for t in project.counts if t.name == builders.NPM_FONTSOURCE)
    assert npm.n.tolist() == pytest.approx([1.0 + 183 / 365])


# --- a synthetic stage -------------------------------------------------------------------------


def write_stage(paths: Paths) -> None:
    stage = paths.stage
    (stage / "records").mkdir(parents=True)
    counted = {"inter": entry("observed", 30.0), "lato": entry("observed", 10.0)}
    sources = ("fot", "almanac", "google", "npm_fontsource", "ecosystems", "jsdelivr", "npm_expo")
    terms = {"project": {s: dict(counted) for s in sources}}
    (stage / "terms.json").write_text(json.dumps(terms))
    (stage / "snapshots.json").write_text(
        json.dumps({"npm": snapshot("2026-10-12"), "gf_stats": snapshot("2026-10-17")})
    )
    (stage / "l3.json").write_text(
        json.dumps({"inter": l3("inter", "L3"), "lato": l3("lato", "failed")})
    )
    mapped = [
        obs("fot", "2026-W40", "Inter", 6.0, "inter"),
        obs("gf_stats", "year", "Inter", 100.0, "inter"),
        obs("gf_stats", "30day", "Inter", 9.0, "inter"),  # not the year: not read
        obs("gf_stats", "year", "Lato", 50.0, "lato"),
        obs("google_metadata", "popularity", "Inter", 1.0, "inter"),
        obs("google_metadata", "popularity", "Lato", 2.0, "lato"),
        obs("npm", "last-year", "@fontsource/inter", 10.0, "inter"),
        obs("npm", "last-year", "@fontsource-variable/inter", 5.0, "inter"),
        obs("npm", "2026-09", "@fontsource/inter", 1.0, "inter"),  # a month: not read
        obs("npm", "last-year", "@expo-google-fonts/inter", 3.0, "inter"),
        obs("npm", "last-year", "@fontsource/lato", 3.0, "lato"),
        obs("ecosystems", "dependent_repos", "fontsource/inter", 4.0, "inter"),
        obs("ecosystems", "dependent_repos", "fontsource-variable/inter", 1.0, "inter"),
        obs("fontsource_stats", "jsdelivr_monthly", "inter", 40.0, "inter"),
        obs("fontsource_stats", "jsdelivr_monthly", "lato", 0.0, "lato"),
        obs("fontsource_stats", "npm_monthly", "inter", 8.0, "inter"),  # not jsDelivr
        obs("npm", "last-year", "@fontsource/nobody", 3.0, None),  # unmapped
    ]
    lines(
        stage / "mapped.jsonl",
        ({"record": r, "family_id": f, "relation": "direct", "detail": ""} for r, f in mapped),
    )
    lines(
        stage / "records" / "fot.jsonl",
        [
            fot_record("2026-W40", "2026-10-04", "Inter", "others", 6.0, 1000),
            fot_record("2026-W41", "2026-10-11", "Inter", "others", 2.0, 1200),
            fot_record("2026-W41", "2026-10-11", "Inter", "startups", 1.0, 300),
        ],
    )
    lines(
        stage / "records" / "npm.jsonl",
        [
            npm_record("@fontsource/inter", None),
            npm_record("@fontsource-variable/inter", "2026-04-17"),
            npm_record("@fontsource-variable/inter", "2026-01-01"),  # only the first row counts
            npm_record("@expo-google-fonts/inter", None),
        ],
    )
    lines(
        stage / "records" / "google_metadata.jsonl",
        [
            {"type": "FontFacts", "family": "Inter", "added": "2026-04-17"},
            {"type": "FontFacts", "family": "Lato", "added": None},
            {"type": "FontFacts", "family": "Unlinked", "added": "2020-01-01"},
            {
                "type": "Observation",
                "source": "google_metadata",
                "key": {"ns": "gf", "key": "Inter"},
            },
        ],
    )


def snapshot(data_date: str) -> dict[str, Any]:
    return {
        "snapshot": "2026-10-17",
        "stale": False,
        "data_date": data_date,
        "window": None,
        "fetched_at": None,
        "collector_version": 1,
        "records": 1,
        "baselines": [],
    }


def l3(fid: str, level: str) -> dict[str, Any]:
    return {
        "family_id": fid,
        "level": level,
        "checked_on": "2026-10-17",
        "text_url": None,
        "text_sha256": None,
        "matched": None,
        "name_ids": [None, None],
        "font_version": None,
        "font_file": None,
        "problems": [],
        "copyright": None,
    }


def obs(source: str, series: str, key: str, value: float, family: str | None):
    record = {
        "type": "Observation",
        "source": source,
        "series": series,
        "key": {"ns": source, "key": key},
        "value": value,
    }
    return record, family


def fot_record(week: str, end: str, key: str, category: str, sites: float, crawled: int):
    return {
        "type": "Observation",
        "source": "fot",
        "series": week,
        "start": end,
        "end": end,
        "key": {"ns": "fot", "key": key},
        "value": sites,
        "attrs": {"category": category, "method": "m", "category_sites": crawled},
    }


def npm_record(key: str, first_seen: str | None):
    return {
        "type": "Observation",
        "source": "npm",
        "series": "2026-09",
        "key": {"ns": "npm", "key": key},
        "value": 1.0,
        "attrs": {"first_seen": first_seen, "month": "2026-09"},
    }


def lines(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_two_families_with_different_dates_for_one_font_stop(tmp_path: Path):
    paths = Paths.for_root(tmp_path)
    write_stage(paths)
    path = paths.stage / "records" / "google_metadata.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps({"type": "FontFacts", "family": "Inter Tight", "added": "2025-01-01"}) + "\n"
        )
    mapped = paths.stage / "mapped.jsonl"
    with mapped.open("a", encoding="utf-8") as f:
        record, _ = obs("google_metadata", "popularity", "Inter Tight", 1.0, "inter")
        f.write(
            json.dumps({"record": record, "family_id": "inter", "relation": "rename", "detail": ""})
            + "\n"
        )
    with pytest.raises(ValueError, match="two families with different dates"):
        modelinputs.models(paths, RUN)
