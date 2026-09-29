"""Stages "export" and "export-site" (milestone-1 step 15) on synthetic stage files.

``make_build`` writes every stage file export reads, for eleven invented
families (no real data, hosts under example.com), then returns a
``StageContext`` over them. ``tests/pipeline/test_validate.py`` reuses it.

The families, and what each one exercises:

- ``alpha-sans``: ranked everywhere; aliases, a related family, a superfamily,
  a preview, a guard event in overall.
- ``beta-mono``: the only monospace font (Coding); preinstalled on a Linux
  system, so Arch and Debian abstain in most chosen, overall and Coding.
- ``gamma-serif``: CC BY (attribution); held past 100 by the two-group gate.
- ``delta-display``: every term too new.
- ``epsilon-hand``: a free-use grant that forbids redistribution (no preview);
  in the 251-500 band; license at an owner ruling.
- ``kappa-sans``: desktop evidence only from abstaining Linux sources, so it
  is unranked in most chosen ("no deliberate evidence") but ranked overall.
- Developers & apps: every source is in the npm registry group, so no font
  passes the two-group gate there, as on real data (the engine then gives
  orders inside the top but no rank).
- ``alpha-slab``, ``zeta-sans``: eligible, not in the catalog (zeta's license
  waits for a ruling, so it has no terms).
- ``eta-icons`` (dropped: icon), ``theta-cjk`` (fails Latin: CJK),
  ``iota-sans`` (license excluded): names.json's ineligible names.
"""

import dataclasses
import json
import logging
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT

from tff_catalog import export, jsonio, stageio
from tff_catalog.confidence import Confidence
from tff_catalog.config import load_config
from tff_catalog.config_model import (
    RANK_KEYS,
    Config,
    PreinstalledConfig,
    PreinstalledSystem,
    SourceCredit,
)
from tff_catalog.corrections import Tags, Term
from tff_catalog.engine.fuse import Fused
from tff_catalog.engine.order import Placement
from tff_catalog.facts import Facts
from tff_catalog.latin import LatinResult
from tff_catalog.license_l3 import L3Result
from tff_catalog.licenses import LicenseClass, Verdict
from tff_catalog.links import Link, Links
from tff_catalog.membership import Membership, MemberState
from tff_catalog.parse import SNAPSHOTS_FILE, STALE_FILE, SourceSnapshot
from tff_catalog.paths import Paths
from tff_catalog.records import FontFileRef, SourceKey
from tff_catalog.specimens.stage import Preview
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.surveys import SurveyScores
from tff_catalog.universe import Family, Universe

RUN_DATE = date(2026, 10, 3)
COMMIT = "c0ffee" + "0" * 34
SHA = {name: f"{i:x}" * 64 for i, name in enumerate(("a", "b", "c", "d", "e", "f"), start=10)}
SYSTEMS = PreinstalledConfig(
    schema=1,
    systems={
        "synthix": PreinstalledSystem(
            label="Synthix Linux",
            os="linux",
            families=("Beta Mono", "Kappa Sans"),
            source="https://example.com/synthix/fonts",
        ),
        "winsynth": PreinstalledSystem(
            label="Winsynth 1",
            os="windows",
            families=("Alpha Sans",),
            source="https://example.com/winsynth/fonts",
        ),
    },
)
MEMBERS = ("alpha-sans", "beta-mono", "delta-display", "epsilon-hand", "gamma-serif", "kappa-sans")

# (id, family, drop, category, monospace, latin passes, coverage)
FAMILIES = (
    ("alpha-sans", "Alpha Sans", None, "sans-serif", False, True, "extended"),
    ("beta-mono", "Beta Mono", None, "monospace", True, True, "extended"),
    ("gamma-serif", "Gamma Serif", None, "serif", False, True, "extended"),
    ("delta-display", "Delta Display", None, "display", False, True, "basic"),
    ("epsilon-hand", "Epsilon Hand", None, "handwriting", False, True, "extended"),
    ("kappa-sans", "Kappa Sans", None, "sans-serif", False, True, "extended"),
    ("alpha-slab", "Alpha Slab", None, "serif", False, True, "extended"),
    ("zeta-sans", "Zeta Sans", None, "sans-serif", False, True, "extended"),
    ("eta-icons", "Eta Icons", "icon", "display", False, True, "extended"),
    ("theta-cjk", "Theta CJK", None, "sans-serif", False, False, None),
    ("iota-sans", "Iota Sans", None, "sans-serif", False, True, "extended"),
)

ALIASES = """alias,ns,family_id,relation,detail,source,first_seen,reviewed_by
Alpha Sans Pro,gf-family,alpha-sans,rename,,synthetic,2026-09-01,owner
Alpha Sans Variable,font-name,alpha-sans,build,variable,synthetic,2026-09-01,owner
AlphaSans-Regular,font-name,alpha-sans,postscript,,synthetic,2026-09-01,owner
@fontsource/alpha-sans,npm,alpha-sans,package,,synthetic,2026-09-01,owner
alpha-sans-pro,fs-id,alpha-sans,rename,,synthetic,2026-09-01,owner
Zeta Sans,gf-family,alpha-sans,related,derived from Alpha Sans,synthetic,2026-09-01,owner
Alpha Slab,gf-family,alpha-sans,distinct,,synthetic,2026-09-01,owner
BetaMono Nerd Font,font-name,beta-mono,build,nerd,synthetic,2026-09-01,owner
BtMono,nerd-folder,beta-mono,build,nerd,synthetic,2026-09-01,owner
Eta Icons Outline,font-name,eta-icons,rename,,synthetic,2026-09-01,owner
Arial,font-name,,ineligible,proprietary,synthetic,2026-09-01,owner
"""
SUPERFAMILIES = """superfamily_id,name,family_id,source,first_seen,reviewed_by
alpha,Alpha,alpha-sans,synthetic,2026-09-01,owner
alpha,Alpha,alpha-slab,synthetic,2026-09-01,owner
"""

GROUP = {
    "homebrew": "homebrew",
    "arch": "arch",
    "debian": "debian",
    "google": "google",
    "fot": "fot",
    "npm_fontsource": "npm_registry",
}


def obs(source: str, value: float) -> Term:
    return Term(value, "observed", GROUP[source])


def cens(source: str, value: float) -> Term:
    return Term(value, "censored", GROUP[source], "below_floor")


def new(source: str) -> Term:
    return Term(None, "too_new", GROUP[source])


def nc(source: str) -> Term:
    return Term(None, "not_covered", GROUP[source], "no_package")


INSTALLED = {
    "homebrew": {
        "alpha-sans": obs("homebrew", 50000),
        "beta-mono": obs("homebrew", 30000),
        "gamma-serif": obs("homebrew", 5000),
        "delta-display": new("homebrew"),
        "epsilon-hand": obs("homebrew", 200),
        "kappa-sans": nc("homebrew"),
        "alpha-slab": obs("homebrew", 1000),
    },
    "arch": {
        "alpha-sans": obs("arch", 0.05),
        "beta-mono": obs("arch", 0.08),
        "kappa-sans": obs("arch", 0.10),
        "gamma-serif": cens("arch", 0.001),
        "delta-display": new("arch"),
    },
    "debian": {"kappa-sans": obs("debian", 5000), "beta-mono": obs("debian", 800)},
}
ABSTAIN = {
    ("arch", "beta-mono"),
    ("arch", "kappa-sans"),
    ("debian", "beta-mono"),
    ("debian", "kappa-sans"),
}
PROJECT = {
    "google": {
        "alpha-sans": obs("google", 10_000_000),
        "gamma-serif": obs("google", 200_000),
        "epsilon-hand": obs("google", 50_000),
        "kappa-sans": obs("google", 3_000_000),
        "alpha-slab": obs("google", 100_000),
        "delta-display": new("google"),
    },
    "fot": {
        "alpha-sans": obs("fot", 120),
        "kappa-sans": obs("fot", 30),
        "gamma-serif": cens("fot", 1),
    },
    "npm_fontsource": {
        "alpha-sans": obs("npm_fontsource", 90000),
        "beta-mono": obs("npm_fontsource", 40000),
    },
}

# Placements by rank key, best first: (id, order, gate_held).
ORDERS: dict[str, tuple[tuple[str, int, bool], ...]] = {
    "overall": (
        ("alpha-sans", 1, False),
        ("kappa-sans", 2, False),
        ("beta-mono", 3, False),
        ("alpha-slab", 4, False),
        ("gamma-serif", 101, True),
        ("epsilon-hand", 300, False),
    ),
    "desktop_chosen": (
        ("alpha-sans", 1, False),
        ("beta-mono", 2, False),
        ("gamma-serif", 3, False),
        ("alpha-slab", 4, False),
        ("epsilon-hand", 5, False),
    ),
    "desktop_installed": (
        ("alpha-sans", 1, False),
        ("kappa-sans", 2, False),
        ("beta-mono", 3, False),
        ("gamma-serif", 4, False),
        ("alpha-slab", 5, False),
        ("epsilon-hand", 6, False),
    ),
    "project": (
        ("alpha-sans", 1, False),
        ("kappa-sans", 2, False),
        ("gamma-serif", 3, False),
        ("alpha-slab", 4, False),
        ("epsilon-hand", 5, False),
        ("beta-mono", 6, False),
    ),
    "coding": (("beta-mono", 1, False),),
    # Nobody passes the gate (one independence group): orders 1-2, no rank.
    "dev_apps": (("alpha-sans", 1, True), ("beta-mono", 2, True)),
}
DESKTOP_WEIGHTS = {"homebrew": 1.0, "arch": 0.75, "github": 0.0, "nerd": 0.0, "debian": 0.25}
PROJECT_WEIGHTS = {"fot": 0.1375, "almanac": 0.0, "google": 0.20625, "npm_fontsource": 0.3}
VIEW_WEIGHTS = {
    "desktop_chosen": DESKTOP_WEIGHTS,
    "desktop_installed": DESKTOP_WEIGHTS,
    "project": PROJECT_WEIGHTS,
    "coding": {"homebrew": 1.0, "arch": 0.75, "npm_fontsource": 0.5},
    "dev_apps": {"npm_fontsource": 0.15, "npm_expo": 0.1},
    "overall": {},
}
RULER = {
    "alpha-sans": 2.0,
    "beta-mono": 1.6,
    "gamma-serif": 0.4,
    "alpha-slab": 0.1,
    "epsilon-hand": -0.6,
    "delta-display": -1.0,
}
GUARD = {("overall", "alpha-sans"): (("homebrew", 0.5),)}


# Rising's recent window (stage "correct" writes it every run, with the same abstentions).
RECENT = {
    "homebrew": {"alpha-sans": obs("homebrew", 4000), "beta-mono": obs("homebrew", 2600)},
    "arch": {"alpha-sans": obs("arch", 0.05), "kappa-sans": obs("arch", 0.11)},
}


def terms_by_view() -> dict[str, dict[str, dict[str, Term]]]:
    def chosen(view: dict[str, dict[str, Term]]) -> dict[str, dict[str, Term]]:
        return {
            s: {f: t for f, t in by_id.items() if (s, f) not in ABSTAIN}
            for s, by_id in view.items()
        }

    return {
        "desktop_installed": INSTALLED,
        "desktop_chosen": chosen(INSTALLED),
        "project": PROJECT,
        "coding": {
            "homebrew": {"beta-mono": INSTALLED["homebrew"]["beta-mono"]},
            "arch": {},
            "npm_fontsource": {"beta-mono": PROJECT["npm_fontsource"]["beta-mono"]},
        },
        "dev_apps": {"npm_fontsource": PROJECT["npm_fontsource"]},
        "rising": chosen(RECENT),
    }


def view_terms(terms: Mapping[str, Mapping[str, Mapping[str, Term]]], key: str) -> Any:
    if key == "overall":
        return {**terms["project"], **terms["desktop_chosen"]}
    return terms[key]


def scores_and_ranks(
    terms: Mapping[str, Mapping[str, Mapping[str, Term]]],
) -> tuple[dict[str, SurveyScores], dict[str, dict[str, Placement]]]:
    scores, ranks = {}, {}
    for key, rows in ORDERS.items():
        vt = view_terms(terms, key)
        fused, placed = {}, {}
        for i, (fid, order, held) in enumerate(rows):
            counted = [t for by in vt.values() if (t := by.get(fid)) and t.state in export.COUNTED]
            observed = [t for t in counted if t.state == "observed"]
            fused[fid] = Fused(
                score=3.0 - 0.25 * i,
                weight=1.0,
                terms=len(counted),
                observed=len(observed),
                groups=tuple(sorted({t.group for t in observed})),
                guard=GUARD.get((key, fid), ()),
            )
            rank = order if order <= 100 and not held else None
            placed[fid] = Placement(order, rank, None, held)
        scores[key] = SurveyScores(
            key=key,
            fused=fused,
            placements=placed,
            overlaps=dict.fromkeys(VIEW_WEIGHTS[key], 40),
            weights=VIEW_WEIGHTS[key],
        )
        ranks[key] = placed
    return scores, ranks


def confidence(ranks: Mapping[str, Mapping[str, Placement]]) -> dict[str, dict[str, Confidence]]:
    return {
        key: {
            fid: Confidence((max(1, p.order - 1), p.order + 3), "A" if p.rank else "C")
            for fid, p in placed.items()
        }
        for key, placed in ranks.items()
    }


def universe() -> Universe:
    families = {}
    for fid, name, drop, *_ in FAMILIES:
        families[fid] = Family(
            id=fid,
            family=name,
            keys=(SourceKey("gf-family", name),),
            sources=("google_metadata",),
            first_seen=date(2026, 3, 1),
            minted_from=name,
            drop=drop,
            urls=(("license", f"https://example.com/{fid}/LICENSE.txt"),),
        )
    return Universe(families=families, unmapped=())


def allowed(
    spdx: str, group: str, *, redistributable: bool = True, credit: bool = False
) -> LicenseClass:
    return LicenseClass(spdx, "allowed", group, redistributable, credit)


LICENSES = {
    "alpha-sans": allowed("OFL-1.1", "open-font"),
    "beta-mono": allowed("MIT", "permissive"),
    "gamma-serif": allowed("CC-BY-4.0", "attribution", credit=True),
    "delta-display": allowed("OFL-1.1", "open-font"),
    "epsilon-hand": allowed("LicenseRef-Epsilon-Free", "freeware", redistributable=False),
    "kappa-sans": allowed("OFL-1.1", "open-font"),
    "alpha-slab": allowed("OFL-1.1", "open-font"),
    "zeta-sans": LicenseClass("LicenseRef-Zeta", "ruling", reason="needs the owner"),
    "theta-cjk": allowed("OFL-1.1", "open-font"),
    "iota-sans": LicenseClass("CC-BY-SA-4.0", "excluded", reason="D3"),
}


def verdicts() -> dict[str, Verdict]:
    out = {}
    for fid, lic in LICENSES.items():
        ok = lic.status == "allowed" and bool(lic.redistributable)
        queue = "LicenseRef-Zeta needs a ruling" if lic.status == "ruling" else None
        seen = (("fontsource", lic.spdx), ("google_repo", lic.spdx))
        out[fid] = Verdict(fid, lic.spdx, lic, ok, seen, queue)
    return out


def l3_results() -> dict[str, L3Result]:
    files = {
        "alpha-sans": FontFileRef(
            "https://example.com/fonts/alpha-sans/AlphaSans%5Bwght%5D.ttf", SHA["a"], 876576
        ),
        "beta-mono": FontFileRef(
            "https://example.com/fonts/beta-mono/BetaMono.woff2", SHA["b"], 90000
        ),
        "epsilon-hand": FontFileRef(
            "https://example.com/fonts/epsilon/Epsilon.otf", SHA["e"], 50000
        ),
    }
    return {
        fid: L3Result(
            family_id=fid,
            level="ruling" if fid == "epsilon-hand" else "L3",
            checked_on=date(2026, 10, 2),
            text_url=f"https://example.com/fonts/{fid}/LICENSE.txt",
            text_sha256=SHA["c"],
            matched=LICENSES[fid].spdx,
            name_ids=(None, None),
            font_version="1.000",
            font_file=files.get(fid),
            copyright="Copyright 2026 Gamma Type" if fid == "gamma-serif" else None,
        )
        for fid in MEMBERS
    }


def links() -> dict[str, Links]:
    out = {}
    for fid in MEMBERS:
        designer = (
            Link("https://example.com/studio", "Gamma Type") if fid == "gamma-serif" else None
        )
        out[fid] = Links(Link(f"https://example.com/fonts/{fid}"), designer, "two_sources")
    return out


def snapshots() -> dict[str, SourceSnapshot]:
    collectors = (
        "homebrew_analytics",
        "pkgstats",
        "github_releases",
        "nerd_releases",
        "debian",
        "fot",
        "almanac",
        "gf_stats",
        "npm",
        "ecosystems",
        "fontsource_stats",
    )
    out = {}
    for c in collectors:
        stale = c == "fontsource_stats"
        day = date(2026, 9, 3) if stale else RUN_DATE
        data_date = None if c == "debian" else date(day.year, day.month, day.day - 1)
        out[c] = SourceSnapshot(day, stale, data_date, None, f"{day}T06:00:00Z", 1, 10, ())
    return out


def config(**site_sources: bool) -> Config:
    """The repository's config with synthetic preinstalled systems; ``site_sources``
    overrides sources' ``publish_rank``."""
    cfg = load_config(Paths.for_root(ROOT))
    credits: dict[str, SourceCredit] = dict(cfg.site.sources)
    for name, publish in site_sources.items():
        credits[name] = dataclasses.replace(credits[name], publish_rank=publish)
    site = dataclasses.replace(cfg.site, sources=credits)
    return dataclasses.replace(cfg, preinstalled=SYSTEMS, site=site)


def make_build(
    tmp_path: Path,
    *,
    cfg: Config | None = None,
    state: State | None = None,
    previews: Mapping[str, Preview] | None = None,
) -> StageContext:
    """Write every stage file export reads under ``tmp_path`` and return the context."""
    root = tmp_path / "repo"
    paths = Paths.for_root(root).with_(config=ROOT / "config", schemas=ROOT / "schemas")
    paths.data.mkdir(parents=True)
    paths.aliases_csv.write_text(ALIASES, encoding="utf-8")
    paths.superfamilies_csv.write_text(SUPERFAMILIES, encoding="utf-8")
    terms = terms_by_view()
    scores, ranks = scores_and_ranks(terms)
    tags = {
        "alpha-sans": Tags(preinstalled_on=("winsynth",)),
        "beta-mono": Tags(preinstalled_on=("synthix",), pulled_in_by=(("arch", "synthix-meta"),)),
        "kappa-sans": Tags(preinstalled_on=("synthix",), flags=("no_deliberate_evidence",)),
    }
    latin = {
        fid: LatinResult(True, "gf_metadata", cover)
        if passes
        else LatinResult(False, None, None, reason="cjk", cjk_codepoints=20000)
        for fid, _, drop, _, _, passes, cover in FAMILIES
        if drop is None
    }
    facts = {
        fid: Facts(
            category, mono, variable=fid == "alpha-sans", static=True, basis="google_metadata"
        )
        for fid, _, drop, category, mono, _, _ in FAMILIES
        if drop is None
    }
    member_states = {
        fid: MemberState(fid in MEMBERS, RUN_DATE, 0) for fid in (*MEMBERS, "alpha-slab")
    }
    files: dict[str, object] = {
        "universe": universe(),
        "latin": latin,
        "facts": facts,
        "licenses": verdicts(),
        "terms": terms,
        "tags": tags,
        "ruler": RULER,
        "ruler_counts": dict.fromkeys(RULER, 10.0),
        "scores": scores,
        "ranks": ranks,
        "membership": Membership(catalog=member_states, top100={}),
        "l3": l3_results(),
        "confidence": confidence(ranks),
        "links": links(),
    }
    if previews is not None:
        files["previews"] = previews
    for name, obj in files.items():
        stageio.dump_stage(paths, name, obj)
    stageio.dump(snapshots(), paths.stage / SNAPSHOTS_FILE)
    stageio.dump({}, paths.stage / STALE_FILE)
    jsonio.dump(
        {"items": [{"families": ["zeta-sans"], "id": "LIC-spdx-zeta"}]},
        paths.queues / "licenses.json",
    )
    return StageContext(
        paths=paths,
        config=cfg or config(),
        state=state or State(),
        run_date=RUN_DATE,
        store=None,
        fetcher=None,
        log=logging.getLogger("test_export"),
    )


PREVIEWS = {
    "alpha-sans": Preview("specimens/alpha-sans.svg", SHA["f"]),
    "beta-mono": Preview(None, None, ("specimen_failed",)),
}


@pytest.fixture(autouse=True)
def fixed_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "code_commit", lambda root: COMMIT)


def run_all(ctx: StageContext) -> dict[str, Any]:
    """Run "export" then "export-site"; return the three documents by file name."""
    export.run(ctx)
    export.run_site(ctx)
    return {name: jsonio.load(ctx.paths.build / name) for name in export.SCHEMA_FILES}


def fonts(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {f["id"]: f for f in doc["fonts"]}


@pytest.fixture
def built(tmp_path: Path) -> tuple[StageContext, dict[str, Any]]:
    ctx = make_build(tmp_path, previews=PREVIEWS)
    return ctx, run_all(ctx)


# --- schemas and determinism -------------------------------------------------------------------


def test_outputs_validate_against_their_schemas(built) -> None:
    from tff_catalog.validate import schema_errors

    ctx, _ = built
    for name, schema in export.SCHEMA_FILES.items():
        assert schema_errors(ctx.paths.build / name, ROOT / "schemas" / schema) == [], name


def test_site_file_passes_the_frozen_site_checks(built) -> None:
    from tff_site.data import validate

    _, docs = built
    assert validate(docs[export.SITE_FILE]).fonts == len(MEMBERS)


def test_same_inputs_give_the_same_bytes(tmp_path: Path) -> None:
    a = make_build(tmp_path / "a", previews=PREVIEWS)
    b = make_build(tmp_path / "b", previews=PREVIEWS)
    run_all(a)
    run_all(b)
    for name in (*export.SCHEMA_FILES, "state/published_ranks.json"):
        assert (a.paths.build / name).read_bytes() == (b.paths.build / name).read_bytes(), name


def test_rank_keys_are_versioned_constants() -> None:
    from tff_site import data as site_data

    catalog = json.loads((ROOT / "schemas" / "catalog.schema.json").read_text(encoding="utf-8"))
    site = json.loads((ROOT / "schemas" / "catalog-site.schema.json").read_text(encoding="utf-8"))
    names = json.loads((ROOT / "schemas" / "names.schema.json").read_text(encoding="utf-8"))
    assert export.RANK_KEYS == RANK_KEYS == site_data.RANK_KEYS
    assert tuple(catalog["$defs"]["rank_key"]["enum"]) == export.RANK_KEYS
    assert tuple(site["$defs"]["rank_key"]["enum"]) == export.RANK_KEYS
    assert catalog["properties"]["schema_version"]["const"] == export.CATALOG_SCHEMA_VERSION
    assert site["properties"]["schema_version"]["const"] == export.SITE_SCHEMA_VERSION
    assert names["properties"]["schema_version"]["const"] == export.NAMES_SCHEMA_VERSION
    assert site_data.SCHEMA_VERSION == export.SITE_SCHEMA_VERSION


# --- catalog.json --------------------------------------------------------------------------


def test_catalog_top_level(built) -> None:
    ctx, docs = built
    doc = docs[export.CATALOG_FILE]
    assert doc["run"]["date"] == "2026-10-03"
    assert doc["run"]["code_commit"] == COMMIT
    assert doc["data_license"] == {
        "spdx": "CC-BY-SA-4.0",
        "provisional": False,
        "url": ctx.config.site.data_license.url,
    }
    sources = {s["id"]: s for s in doc["sources"]}
    assert "flutter" not in sources  # disabled in ranking.toml
    assert sources["jsdelivr"]["stale"] is True
    assert sources["homebrew"]["data_date"] == "2026-10-02"
    assert sources["debian"]["data_date"] == "2026-10-03"  # no data date: the snapshot's
    assert sources["google"]["publish_raw"] is False
    assert sources["homebrew"]["weight"] == 1.0
    assert sorted(f["id"] for f in doc["fonts"]) == sorted(MEMBERS)


def test_google_and_fot_carry_no_raw_values(built) -> None:
    _, docs = built
    for font in docs[export.CATALOG_FILE]["fonts"]:
        for source in ("google", "fot"):
            assert "value" not in font["sources"][source]
    alpha = fonts(docs[export.CATALOG_FILE])["alpha-sans"]
    assert alpha["sources"]["homebrew"]["value"] == 50000.0
    assert alpha["sources"]["google"]["rank_in_source"] == 1  # ranks are published (T2)
    assert alpha["sources"]["google"]["z"] is not None
    assert "value" not in json.dumps(docs[export.SITE_FILE])


def test_rank_in_source_is_a_competition_rank(built) -> None:
    _, docs = built
    doc = fonts(docs[export.CATALOG_FILE])
    homebrew = {fid: f["sources"]["homebrew"] for fid, f in doc.items()}
    assert homebrew["alpha-sans"]["rank_in_source"] == 1
    assert homebrew["beta-mono"]["rank_in_source"] == 2
    assert homebrew["epsilon-hand"]["rank_in_source"] == 5  # alpha-slab (not a member) is 4th
    assert homebrew["delta-display"] == {
        "state": "too_new",
        "reason": None,
        "rank_in_source": None,
        "z": None,
        "weight_used": None,
        "abstains_in": [],
    }
    assert homebrew["kappa-sans"]["state"] == "not_covered"
    assert homebrew["kappa-sans"]["reason"] == "no_package"
    assert doc["alpha-sans"]["sources"]["almanac"]["reason"] == "outside_frame"  # no term at all
    gamma_arch = doc["gamma-serif"]["sources"]["arch"]
    assert (gamma_arch["state"], gamma_arch["reason"], gamma_arch["rank_in_source"]) == (
        "censored",
        "below_floor",
        None,
    )
    assert gamma_arch["z"] is not None


def test_weight_used_follows_the_overall_formula(built) -> None:
    ctx, docs = built
    alpha = fonts(docs[export.CATALOG_FILE])["alpha-sans"]["sources"]
    total = sum(DESKTOP_WEIGHTS.values())
    mix = ctx.config.ranking.ranks.overall.mix["desktop_chosen"]
    guard = 0.5  # the guard fired on alpha's Homebrew term in overall
    rounded = {"abs": 10**-export.DIGITS}
    assert alpha["homebrew"]["weight_used"] == pytest.approx(mix * 1.0 * guard / total, **rounded)
    assert alpha["arch"]["weight_used"] == pytest.approx(mix * 0.75 / total, **rounded)
    project_mix = ctx.config.ranking.ranks.overall.mix["project"]
    assert alpha["google"]["weight_used"] == pytest.approx(
        project_mix * 0.20625 / sum(PROJECT_WEIGHTS.values()), **rounded
    )
    assert alpha["almanac"]["weight_used"] is None


def test_linux_abstentions_are_listed_per_view(built) -> None:
    _, docs = built
    doc = fonts(docs[export.CATALOG_FILE])
    beta, kappa = doc["beta-mono"], doc["kappa-sans"]
    assert beta["sources"]["arch"]["abstains_in"] == ["overall", "desktop_chosen", "coding"]
    assert beta["sources"]["debian"]["abstains_in"] == ["overall", "desktop_chosen"]
    assert beta["sources"]["arch"]["weight_used"] is None
    assert kappa["sources"]["arch"]["abstains_in"] == ["overall", "desktop_chosen"]
    assert kappa["sources"]["arch"]["rank_in_source"] == 1  # still ranked in its own source
    assert kappa["ranks"]["desktop_chosen"]["unranked"] == "no_deliberate_evidence"
    assert kappa["ranks"]["desktop_installed"]["rank"] == 2
    assert kappa["ranks"]["overall"]["rank"] == 2
    assert "no_deliberate_evidence" in kappa["flags"]
    assert doc["alpha-sans"]["sources"]["arch"]["abstains_in"] == []


def test_rank_entries_bands_and_gate(built) -> None:
    _, docs = built
    doc = fonts(docs[export.CATALOG_FILE])
    alpha = doc["alpha-sans"]["ranks"]["overall"]
    assert alpha | {"score": None} == {
        "rank": 1,
        "band": None,
        "order": 1,
        "tier": "A",
        "range": [1, 4],
        "gate_held": False,
        "unranked": None,
        "score": None,
        "groups": 5,
    }
    gamma = doc["gamma-serif"]
    assert gamma["ranks"]["overall"]["band"] == "101\u2013250"
    assert gamma["ranks"]["overall"]["rank"] is None
    assert gamma["ranks"]["overall"]["gate_held"] is True
    assert "gate_held" in gamma["flags"]
    assert doc["epsilon-hand"]["ranks"]["overall"]["band"] == "251\u2013500"


def test_views_and_unranked_reasons(built) -> None:
    _, docs = built
    doc = fonts(docs[export.CATALOG_FILE])
    assert set(doc["beta-mono"]["ranks"]) == {k for k in RANK_KEYS if k != "rising"}
    assert set(doc["alpha-sans"]["ranks"]) == {
        k for k in RANK_KEYS if k not in ("rising", "coding")
    }
    delta = doc["delta-display"]
    for key in ("overall", "desktop_chosen", "desktop_installed", "project"):
        assert delta["ranks"][key]["unranked"] == "too_new", key
    assert delta["ranks"]["dev_apps"]["unranked"] == "no_evidence"
    assert "too_new" in delta["flags"]


def test_rising_appears_with_three_months_of_history(tmp_path: Path) -> None:
    history = ({"run_date": "2026-08-03"}, {"run_date": "2026-09-03"})
    ctx = make_build(tmp_path, state=State(run_history=history))
    docs = run_all(ctx)
    doc = fonts(docs[export.CATALOG_FILE])
    assert doc["alpha-sans"]["ranks"]["rising"]["unranked"] == "no_evidence"
    views = {v["key"]: v["available"] for v in docs[export.SITE_FILE]["views"]}
    assert views["rising"] is True
    # Rising abstains like every rank but most installed (D8).
    beta, kappa = doc["beta-mono"]["sources"], doc["kappa-sans"]["sources"]
    assert beta["arch"]["abstains_in"] == ["overall", "desktop_chosen", "coding", "rising"]
    assert kappa["arch"]["abstains_in"] == ["overall", "desktop_chosen", "rising"]
    from tff_site.data import validate

    validate(docs[export.SITE_FILE])


def test_rising_new_comes_from_channel_dates(tmp_path: Path) -> None:
    """Rising's "New" is the earliest channel date under 90 days (as stage "rank" rules), not
    the day the universe first listed the family: on the third run that is every family."""
    history = ({"run_date": "2026-08-03"}, {"run_date": "2026-09-03"})
    ctx = make_build(tmp_path, state=State(run_history=history))
    universe_now = stageio.load_stage(ctx.paths, "universe")
    families = {
        fid: dataclasses.replace(fam, first_seen=date(2026, 8, 3))
        for fid, fam in universe_now.families.items()
    }
    stageio.dump_stage(ctx.paths, "universe", dataclasses.replace(universe_now, families=families))
    doc = fonts(run_all(ctx)[export.CATALOG_FILE])
    assert {f["ranks"]["rising"]["unranked"] for f in doc.values()} == {"no_evidence"}
    assert not any("too_new" in f["flags"] for f in doc.values() if f["id"] != "delta-display")
    jsonio.dump(
        {
            "alpha-sans": {"catalog": None, "sources": {"homebrew": "2026-08-20"}},
            "beta-mono": {"catalog": None, "sources": {"homebrew": "2025-01-01", "npm": None}},
        },
        ctx.paths.next_state / "first_seen.json",
    )
    docs = run_all(ctx)
    doc = fonts(docs[export.CATALOG_FILE])
    assert doc["alpha-sans"]["ranks"]["rising"]["unranked"] == "too_new"
    assert "too_new" in doc["alpha-sans"]["flags"]
    assert doc["beta-mono"]["ranks"]["rising"]["unranked"] == "no_evidence"
    from tff_site.data import validate

    validate(docs[export.SITE_FILE])


def test_a_view_nobody_passes_the_gate_in_starts_past_the_top(built) -> None:
    """Developers & apps: the engine gives held-back fonts orders 1, 2 but no rank; nothing
    may sit inside the top 100 without a rank, so they move down to 101, 102."""
    ctx, docs = built
    for name in (export.CATALOG_FILE, export.SITE_FILE):
        doc = fonts(docs[name])
        alpha, beta = doc["alpha-sans"]["ranks"]["dev_apps"], doc["beta-mono"]["ranks"]["dev_apps"]
        assert (alpha["rank"], alpha["order"], alpha["band"]) == (None, 101, "101\u2013250")
        assert alpha["range"] == [101, 104]  # the engine's [1, 4], moved with the order
        assert alpha["gate_held"] is True
        assert (beta["rank"], beta["order"]) == (None, 102)
    published = jsonio.load(ctx.paths.next_state / "published_ranks.json")
    assert published["dev_apps"] == {"alpha-sans": 101, "beta-mono": 102}
    assert published["overall"]["gamma-serif"] == 101  # a full top: nothing moves


def test_band_shift() -> None:
    full = {"a": Placement(1, 1, None, False), "b": Placement(101, None, "101-250", True)}
    assert export.band_shift(full, 100) == 0
    part = {"a": Placement(1, 1, None, False), "b": Placement(2, None, "101-250", True)}
    assert export.band_shift(part, 100) == 99
    assert export.band_shift({}, 100) == 0


def test_band_labels_follow_the_display_config() -> None:
    display = load_config(Paths.for_root(ROOT)).ranking.display
    assert [export.band_label(n, display) for n in (1, 100, 101, 250, 251, 500, 501, 9000)] == [
        None,
        None,
        "101\u2013250",
        "101\u2013250",
        "251\u2013500",
        "251\u2013500",
        "501+",
        "501+",
    ]


def test_fonts_over_time_z_is_the_smoothed_one_the_ranking_used(tmp_path: Path) -> None:
    """Stage "rank" equates Fonts Over Time's EWMA z (this month's smoothing part), not its
    raw counts; its rank in the source stays the raw one."""
    from tff_catalog.state import write_part

    ctx = make_build(tmp_path)

    def fot(month: str) -> dict[str, Any]:
        smoothing = {"month": month, "fot_ewma": {"alpha-sans": -1.0, "kappa-sans": 2.0}}
        write_part(ctx.paths, "smoothing", smoothing, stage="rank")
        doc = fonts(run_all(ctx)[export.CATALOG_FILE])
        return {fid: doc[fid]["sources"]["fot"] for fid in ("alpha-sans", "kappa-sans")}

    raw = fot("2026-09")  # last month's part: not this run's, so the counts are equated
    assert raw["alpha-sans"]["z"] > raw["kappa-sans"]["z"]  # 120 sites against 30
    smoothed = fot("2026-10")
    # Kappa (2.0) now sits above Alpha (-1.0); both reach the top of the ruler's z over
    # the overlap (Alpha and Gamma, which is censored), so they tie there.
    assert smoothed["kappa-sans"]["z"] > raw["kappa-sans"]["z"]
    assert smoothed["kappa-sans"]["z"] == smoothed["alpha-sans"]["z"] == RULER["alpha-sans"]
    assert [smoothed[f]["rank_in_source"] for f in ("alpha-sans", "kappa-sans")] == [1, 2]
    from tff_catalog.validate import hard_checks

    assert [str(f) for f in hard_checks(ctx)] == []


def test_a_member_without_an_accepted_link_is_held_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Stage "links" leaves out a family no two sources agree on (gate K decides); every
    # catalog font needs a primary link, so the export holds it back instead of failing.
    ctx = make_build(tmp_path)
    stageio.dump_stage(ctx.paths, "links", {k: v for k, v in links().items() if k != "gamma-serif"})
    with caplog.at_level(logging.WARNING):
        docs = run_all(ctx)
    assert "gamma-serif" not in fonts(docs[export.CATALOG_FILE])
    assert "gamma-serif" not in fonts(docs[export.SITE_FILE])
    assert "held back until gate K accepts a download link: gamma-serif" in caplog.text
    assert export.Inputs(ctx.paths).unlinked == ("gamma-serif",)
    from tff_catalog.validate import hard_checks

    assert [str(f) for f in hard_checks(ctx)] == []


def test_the_exact_ranks_close_up_over_a_held_back_member(tmp_path: Path) -> None:
    # Owner ruling of 2026-09-28 (rank_holes): the fonts below a held-back member move up one
    # place in every view, so the published exact ranks run 1..N; bands keep their orders.
    full = fonts(run_all(make_build(tmp_path / "full"))[export.CATALOG_FILE])
    ctx = make_build(tmp_path / "held")
    stageio.dump_stage(ctx.paths, "links", {k: v for k, v in links().items() if k != "gamma-serif"})
    held = fonts(run_all(ctx)[export.CATALOG_FILE])
    gone = full["gamma-serif"]["ranks"]
    assert any(e["rank"] is not None for e in gone.values())  # it had exact places to leave
    for fid, font in held.items():
        for key, entry in font["ranks"].items():
            before, hole = full[fid]["ranks"][key], gone.get(key, {}).get("rank")
            if before["rank"] is None:
                assert entry == before, (fid, key)
                continue
            up = 1 if hole is not None and hole < before["rank"] else 0
            assert entry["rank"] == entry["order"] == before["rank"] - up, (fid, key)
            assert entry["range"][0] <= entry["rank"] <= entry["range"][1] or up == 0, (fid, key)
    from tff_catalog.review import export_orders, rank_holes

    # No new gap: the synthetic views' own gaps (fonts ranked outside the catalog) just move.
    before = rank_holes(jsonio.load(tmp_path / "full" / "repo" / "build" / export.CATALOG_FILE))
    after = rank_holes(jsonio.load(ctx.paths.build / export.CATALOG_FILE))
    for key in sorted(set(before) | set(after)):
        hole = gone.get(key, {}).get("rank")
        moved = [h - (1 if hole is not None and hole < h else 0) for h in before.get(key, [])]
        assert after.get(key, []) == moved, key
    # Stage "review" takes the published orders export wrote (``published_ranks``) only when
    # every one matches its own, so it must close up the same way.
    placements = stageio.load_stage(ctx.paths, "ranks")
    exact_top = load_config(Paths.for_root(ROOT)).ranking.display.exact_top
    now = export_orders(placements, exact_top, {"gamma-serif"})
    written = export.published_ranks(jsonio.load(ctx.paths.build / export.CATALOG_FILE))
    assert all(now[k][f] == o for k, v in written.items() for f, o in v.items())
    assert all("gamma-serif" not in v for v in now.values())


def test_an_archived_link_says_so_in_both_files(tmp_path: Path) -> None:
    # Owner ruling of 2026-09-29: an approved override marked archived may link a Wayback
    # Machine capture; its label and note reach catalog.json and catalog-site.json, so the
    # site names the destination as archived and says why.
    ctx = make_build(tmp_path)
    capture = "https://web.archive.org/web/20221209161833/http://example.com/gamma/"
    label = "Wayback Machine: example.com (archived copy)"
    note = "The designer's site is gone; this is the Internet Archive's copy of it."
    chosen = links()
    chosen["gamma-serif"] = Links(Link(capture, label, note, archived=True), None, "override")
    stageio.dump_stage(ctx.paths, "links", chosen)
    docs = run_all(ctx)
    for name in (export.CATALOG_FILE, export.SITE_FILE):
        primary = fonts(docs[name])["gamma-serif"]["links"]["primary"]
        assert primary == {"url": capture, "label": label, "note": note}, name
    from tff_catalog.validate import schema_errors
    from tff_site.data import destination_name, validate

    for name, schema in export.SCHEMA_FILES.items():
        assert schema_errors(ctx.paths.build / name, ROOT / "schemas" / schema) == [], name
    site = docs[export.SITE_FILE]
    validate(site)
    assert destination_name(fonts(site)["gamma-serif"]["links"]["primary"]) == label


def test_a_member_without_stage_data_fails_the_export(tmp_path: Path) -> None:
    ctx = make_build(tmp_path)
    facts = stageio.load_stage(ctx.paths, "facts")
    stageio.dump_stage(ctx.paths, "facts", {k: v for k, v in facts.items() if k != "gamma-serif"})
    with pytest.raises(export.ExportError, match=r"gamma-serif \(facts\)"):
        export.run(ctx)


def test_superfamilies_file_is_strict(tmp_path: Path) -> None:
    path = tmp_path / "superfamilies.csv"
    assert export.load_superfamilies(path) == {}  # no file yet
    path.write_text("superfamily_id,family_id\nalpha,alpha-sans\n", encoding="utf-8")
    with pytest.raises(export.ExportError, match="header"):
        export.load_superfamilies(path)
    path.write_text(
        SUPERFAMILIES + "beta,Beta,alpha-sans,synthetic,2026-09-01,owner\n", encoding="utf-8"
    )
    with pytest.raises(export.ExportError, match="alpha-sans is in two superfamilies"):
        export.load_superfamilies(path)


def test_l3_failures_take_no_rank_in_a_source(tmp_path: Path) -> None:
    """Stage "rank" drops L3 failures before equating; so does each source's view."""
    ctx = make_build(tmp_path)
    before = fonts(run_all(ctx)[export.CATALOG_FILE])["epsilon-hand"]["sources"]["homebrew"]
    assert before["rank_in_source"] == 5  # alpha-slab's 1,000 installs come 4th
    failed = dataclasses.replace(l3_results()["alpha-sans"], family_id="alpha-slab", level="failed")
    l3 = stageio.load_stage(ctx.paths, "l3")
    stageio.dump_stage(ctx.paths, "l3", {**l3, "alpha-slab": failed})
    after = fonts(run_all(ctx)[export.CATALOG_FILE])["epsilon-hand"]["sources"]["homebrew"]
    assert after["rank_in_source"] == 4


def test_license_block(built) -> None:
    _, docs = built
    doc = fonts(docs[export.CATALOG_FILE])
    gamma = doc["gamma-serif"]["license"]
    assert gamma["name"] == "Creative Commons Attribution 4.0"
    assert gamma["class"] == "attribution"
    assert gamma["attribution"] == (
        "Gamma Serif, Copyright 2026 Gamma Type, Creative Commons Attribution 4.0"
    )
    assert gamma["verified_level"] == "L3"
    assert doc["epsilon-hand"]["license"]["verified_level"] == "ruling"
    assert doc["epsilon-hand"]["license"]["name"] == "LicenseRef-Epsilon-Free"
    assert doc["alpha-sans"]["license"]["attribution"] is None
    assert doc["alpha-sans"]["font_file"] == {
        "url": "https://example.com/fonts/alpha-sans/AlphaSans%5Bwght%5D.ttf",
        "sha256": SHA["a"],
        "size": 876576,
        "format": "ttf",
    }
    assert doc["gamma-serif"]["font_file"] is None  # the L3 check read no file


def test_no_redistribution_means_no_preview(built) -> None:
    _, docs = built
    catalog = fonts(docs[export.CATALOG_FILE])["epsilon-hand"]
    site = fonts(docs[export.SITE_FILE])["epsilon-hand"]
    assert catalog["preview_ok"] is False
    assert catalog["font_file"] is not None  # a fact about the license check
    assert (site["preview_ok"], site["preview"], site["font_file"]) == (False, None, None)


def test_previews_are_merged_into_both_files(built) -> None:
    _, docs = built
    for name in (export.CATALOG_FILE, export.SITE_FILE):
        doc = fonts(docs[name])
        assert doc["alpha-sans"]["preview"] == {
            "path": "specimens/alpha-sans.svg",
            "sha256": SHA["f"],
        }
        assert doc["beta-mono"]["preview"] is None
        assert "specimen_failed" in doc["beta-mono"]["flags"]
        assert doc["kappa-sans"]["preview"] is None  # no entry from the specimens stage


def test_export_site_without_specimens_keeps_previews_null(tmp_path: Path) -> None:
    ctx = make_build(tmp_path)
    docs = run_all(ctx)
    assert all(f["preview"] is None for f in docs[export.CATALOG_FILE]["fonts"])


def test_aliases_related_and_superfamily(built) -> None:
    _, docs = built
    alpha = fonts(docs[export.CATALOG_FILE])["alpha-sans"]
    assert alpha["aliases"] == [
        {"name": "Alpha Sans Pro", "relation": "rename"},
        {"name": "Alpha Sans Variable", "relation": "build"},
        {"name": "AlphaSans-Regular", "relation": "postscript"},
    ]  # package and slug namespaces are left out
    assert alpha["related"] == [{"id": "zeta-sans", "note": "derived from Alpha Sans"}]
    assert alpha["superfamily_id"] == "alpha"
    assert fonts(docs[export.CATALOG_FILE])["beta-mono"]["aliases"] == [
        {"name": "BetaMono Nerd Font", "relation": "build"}
    ]


def test_tags_become_systems(built) -> None:
    _, docs = built
    site = docs[export.SITE_FILE]
    assert [s["id"] for s in site["systems"]] == [
        "winsynth",
        "arch",
        "cachyos",
        "debian",
        "synthix",
    ]
    beta = fonts(site)["beta-mono"]
    assert beta["preinstalled_on"] == [{"system": "synthix"}]
    assert beta["pulled_in_by"] == [{"system": "arch", "package": "synthix-meta"}]


def test_published_ranks_state(built) -> None:
    ctx, _ = built
    published = jsonio.load(ctx.paths.next_state / "published_ranks.json")
    assert published["overall"] == {
        "alpha-sans": 1,
        "beta-mono": 3,
        "epsilon-hand": 300,
        "gamma-serif": 101,
        "kappa-sans": 2,
    }
    assert "alpha-slab" not in published["desktop_installed"]  # not a catalog font
    assert published["coding"] == {"beta-mono": 1}


def test_missing_stage_file_names_its_stage(tmp_path: Path) -> None:
    ctx = make_build(tmp_path)
    (ctx.paths.stage / "confidence.json").unlink()
    with pytest.raises(export.MissingInput, match="'confidence'"):
        export.run(ctx)


def test_code_commit_outside_git_is_zeros(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    assert export.code_commit(tmp_path) == export.UNKNOWN_COMMIT


# --- catalog-site.json -------------------------------------------------------------------------


def test_site_wording_comes_from_site_toml(built) -> None:
    ctx, docs = built
    site, cfg = docs[export.SITE_FILE], ctx.config.site
    assert [(v["key"], v["label"], v["measures"]) for v in site["views"]] == [
        (v.key, v.label, v.measures) for v in cfg.views
    ]
    assert site["tiers"] == {"A": cfg.tiers.A, "B": cfg.tiers.B, "C": cfg.tiers.C}
    assert site["license_classes"] == [{"id": c.id, "label": c.label} for c in cfg.license_classes]
    for source in site["sources"]:
        credit = cfg.sources[source["id"]]
        assert (source["name"], source["measures"], source["url"], source["license"]) == (
            credit.name,
            credit.measures,
            credit.url,
            credit.license,
        )
    assert [b["label"] for b in site["bands"]] == ["101\u2013250", "251\u2013500", "501+"]
    assert site["bands"][-1]["to"] is None


def test_site_keeps_only_the_site_fields(built) -> None:
    _, docs = built
    alpha = fonts(docs[export.SITE_FILE])["alpha-sans"]
    assert "score" not in alpha["ranks"]["overall"]
    assert set(alpha["sources"]["homebrew"]) == {"state", "rank_in_source", "reason", "abstains_in"}
    assert "gate_held" not in fonts(docs[export.SITE_FILE])["gamma-serif"]["flags"]


def test_publish_rank_false_hides_ranks_everywhere(tmp_path: Path) -> None:
    ctx = make_build(tmp_path, cfg=config(google=False))
    docs = run_all(ctx)
    for name in (export.CATALOG_FILE, export.SITE_FILE):
        entry = fonts(docs[name])["alpha-sans"]["sources"]["google"]
        assert entry["rank_in_source"] is None
    assert fonts(docs[export.CATALOG_FILE])["alpha-sans"]["sources"]["google"]["z"] is None
    assert {s["id"]: s["publish_rank"] for s in docs[export.SITE_FILE]["sources"]}[
        "google"
    ] is False
    from tff_site.data import validate

    validate(docs[export.SITE_FILE])


def test_a_dropped_source_is_left_out(tmp_path: Path) -> None:
    ctx = make_build(tmp_path)
    snaps = snapshots()
    del snaps["almanac"]
    stageio.dump(snaps, ctx.paths.stage / SNAPSHOTS_FILE)
    docs = run_all(ctx)
    for name in (export.CATALOG_FILE, export.SITE_FILE):
        assert "almanac" not in {s["id"] for s in docs[name]["sources"]}
        assert "almanac" not in fonts(docs[name])["alpha-sans"]["sources"]


# --- names.json --------------------------------------------------------------------------------


def test_names_lists_every_eligible_family(built) -> None:
    _, docs = built
    names = docs[export.NAMES_FILE]
    families = {f["id"]: f for f in names["families"]}
    assert set(families) == {*MEMBERS, "alpha-slab", "zeta-sans"}
    assert families["alpha-slab"]["in_catalog"] is False
    assert families["zeta-sans"]["in_catalog"] is False  # license awaiting a ruling: known
    assert families["alpha-sans"]["in_catalog"] is True
    assert families["alpha-sans"]["superfamily_id"] == "alpha"
    assert families["alpha-sans"]["distinct_from"] == ["alpha-slab"]
    assert families["alpha-slab"]["distinct_from"] == ["alpha-sans"]
    assert families["alpha-sans"]["names"] == [
        {"name": "Alpha Sans Pro", "relation": "rename"},
        {"name": "Alpha Sans Variable", "relation": "build", "detail": "variable"},
        {"name": "AlphaSans-Regular", "relation": "postscript"},
        {"name": "Zeta Sans", "relation": "related"},
    ]
    assert families["beta-mono"]["names"] == [
        {"name": "BetaMono Nerd Font", "relation": "build", "detail": "nerd"}
    ]


def test_names_lists_ineligible_names_with_reasons(built) -> None:
    _, docs = built
    assert docs[export.NAMES_FILE]["ineligible"] == [
        {"name": "Arial", "reason": "proprietary"},
        {"name": "Eta Icons", "reason": "icon"},
        {"name": "Eta Icons Outline", "reason": "icon"},
        {"name": "Iota Sans", "reason": "license"},
        {"name": "Theta CJK", "reason": "cjk"},
    ]


def test_name_lists_hold_only_names_people_see(built) -> None:
    """The alias stage's name namespaces; a Nerd folder (BtMono) is a key, not a name."""
    from tff_catalog.aliases import NAME_NAMESPACES

    assert export.NAME_NAMESPACES == NAME_NAMESPACES
    _, docs = built
    for name in (export.CATALOG_FILE, export.NAMES_FILE):
        assert "BtMono" not in json.dumps(docs[name])


def test_a_failed_l3_check_makes_names_ineligible(tmp_path: Path) -> None:
    ctx = make_build(tmp_path)
    failed = dataclasses.replace(l3_results()["alpha-sans"], family_id="alpha-slab", level="failed")
    stageio.dump_stage(ctx.paths, "l3", {**l3_results(), "alpha-slab": failed})
    names = run_all(ctx)[export.NAMES_FILE]
    assert "alpha-slab" not in {f["id"] for f in names["families"]}
    assert {"name": "Alpha Slab", "reason": "license"} in names["ineligible"]


def test_a_family_with_no_license_is_ineligible(tmp_path: Path) -> None:
    ctx = make_build(tmp_path)
    verdicts_now = stageio.load_stage(ctx.paths, "licenses")
    none = dataclasses.replace(verdicts_now["alpha-slab"], spdx=None, license=None)
    stageio.dump_stage(ctx.paths, "licenses", {**verdicts_now, "alpha-slab": none})
    names = run_all(ctx)[export.NAMES_FILE]
    assert "alpha-slab" not in {f["id"] for f in names["families"]}
    assert {"name": "Alpha Slab", "reason": "license"} in names["ineligible"]
