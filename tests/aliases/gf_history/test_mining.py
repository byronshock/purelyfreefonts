"""The gf_history miner end to end: a synthetic google/fonts history, the committed seeds,
and one real run (marked ``network``).

The fixture repository is built at test time with fixed dates, so its commit
shas are stable. Every family in it is made up (ruling T1 allows synthetic
fixtures everywhere); its history has one case of each rule in the miner's
docstring. The miner is run on the work tree directly (``mine_repo``) and
through its own blobless clone of a local remote (``MINER.mine``).
"""

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from tests.helpers import ROOT

from tff_catalog import gitsrc
from tff_catalog.aliases import AliasCandidate, load_seeds, write_seeds
from tff_catalog.aliases.miners import MineContext, Miner, gf_history
from tff_catalog.aliases.miners.gf_history import (
    CHECKOUT,
    CLONE_URL,
    MINER,
    commit_url,
    delist_url,
    mine_repo,
)
from tff_catalog.keys import match_key
from tff_catalog.paths import Paths
from tff_catalog.store import RawDir

LOG = logging.getLogger("tests.gf_history")
SEEDS = ROOT / "data" / "alias-seeds" / "gf_history.csv"
_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "tff test",
    "GIT_AUTHOR_EMAIL": "",  # no email in a fixture, not even a made-up one
    "GIT_COMMITTER_NAME": "tff test",
    "GIT_COMMITTER_EMAIL": "",
}


def git(*args: str, cwd: Path, when: str | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | _ENV
    env["GIT_ALLOW_PROTOCOL"] = "file"
    if when:
        env |= {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, check=True)
    return done.stdout.decode().strip()


def meta(name: str, designer: str, extra: str = "") -> bytes:
    return (
        f'name: "{name}"\ndesigner: "{designer}"\nlicense: "OFL"\n'
        f'category: "SANS_SERIF"\ndate_added: "2020-01-01"\n{extra}'
    ).encode()


BETA_BROKEN = 'fonts {\n  name: "Beta"\n  full_name: Beta Regular"\n}\n'  # as in real history

DELIST_1 = """\
# will be replaced by Gamma (VF version)
https://fonts.google.com/specimen/Gamma+One

# is replaced by ofl/kappanew
ofl/kappa # https://github.com/google/fonts/pull/1

# will be replaced by Mu Text and Mu Display
https://fonts.google.com/specimen/Mu

# Will be replaced by https://github.com/google/fonts/pull/2
https://fonts.google.com/specimen/Zeta+New

# Apache license will be replaced by ofl
apache/delta # https://github.com/google/fonts/pull/3

# will be replaced by Tau
ofl/tauone
ofl/tautwo
"""
DELIST_2 = (
    "# will be replaced by Gamma (VF version)\r\n"
    "https://fonts.google.com/specimen/Gamma+One\r\n"
    "\r\n"
    "# Delist Rho Olde (we published Rho Underline instead)\r\n"
    "https://fonts.google.com/specimen/Rho+Old\r\n"
    "\r\n"
    "# is replaced by ofl/omegaprime\r\n"
    "ofl/omega # listed after its folder was deleted\r\n"
)

# Rule 3: successors named one per comment line (google/fonts' Big Shoulders block).
DELIST_3 = """\
# Nu fonts need to be de-listed after the new variable version reached the API
# New versions:
# Nu Stencil https://github.com/google/fonts/pull/5
# Nu https://github.com/google/fonts/pull/6
# To delist:
https://fonts.google.com/specimen/Nu+Display
https://fonts.google.com/specimen/Nu+Text
https://fonts.google.com/specimen/Nu+Stencil+Display
https://fonts.google.com/specimen/Xi+Display

# still in dev
ofl/upsilon
"""


@dataclass(frozen=True)
class Fonts:
    work: Path
    remote: str  # a bare clone that allows partial clones
    sha: dict[str, str]  # step label -> commit sha


def write(root: Path, files: dict[str, bytes | str]) -> None:
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)


@pytest.fixture(scope="module")
def fonts(tmp_path_factory: pytest.TempPathFactory) -> Fonts:
    """A google/fonts-like history, one rule per step (see the labels)."""
    base = tmp_path_factory.mktemp("gf")
    work = base / "work"
    work.mkdir()
    sha: dict[str, str] = {}

    def commit(label: str, day: str) -> None:
        git("add", "-A", cwd=work)
        git("commit", "-q", "--no-gpg-sign", "-m", label, cwd=work, when=f"{day}T12:00:00+00:00")
        sha[label] = git("rev-parse", "HEAD", cwd=work)

    def mv(old: str, new: str, *metadata: str) -> None:
        git("mv", old, new, cwd=work)
        if metadata:
            write(work, {f"{new}/METADATA.pb": meta(*metadata)})

    git("init", "-q", "-b", "main", cwd=work)
    write(
        work,
        {
            "ofl/alpha/METADATA.pb": meta("Alpha", "Ann Able"),
            "ofl/alpha/Alpha[wght].ttf": b"\x00\x01\x00\x00font",
            "ofl/beta/METADATA.pb": meta("Beta", "Bob Brown", BETA_BROKEN),
            "apache/delta/METADATA.pb": meta("Delta", "Di Dow"),
            "ofl/eps/METADATA.pb": meta("Eps", "Ed Eel"),
            "ofl/zeta/METADATA.pb": meta("Zeta", "Zo Zim"),
            "ofl/zeta/static/METADATA.pb": meta("Zeta", "Zo Zim"),
            "ofl/gammaone/METADATA.pb": meta("Gamma One", "Cy Cole"),
            "ofl/kappa/METADATA.pb": meta("Kappa", "Kim Kay"),
            "ofl/lam/METADATA.pb": meta("Lambda", "Lu Lee"),
            "ofl/mu/METADATA.pb": meta("Mu", "Mo Moe"),
            "ofl/omicron/METADATA.pb": meta("Omicron", "Oz Orr"),
            "ofl/omega/METADATA.pb": meta("Omega Colored", "Oa Ox"),
            "ofl/rhoold/METADATA.pb": meta("Rho Old", "Ro Ray"),
            "ofl/sig/METADATA.pb": meta("Sigma", "Si Sun"),
            "ofl/tauone/METADATA.pb": meta("Tau One", "Ty Tan"),
            "ofl/tautwo/METADATA.pb": meta("Tau Two", "Ty Tan"),
            "to_delist.txt": "",
            "README.md": "fonts\n",
        },
    )
    commit("start", "2020-01-01")
    # A folder and its name replaced; a nested METADATA.pb deleted alongside is not a family.
    mv("ofl/alpha", "ofl/alphasans", "Alpha Sans", "Ann Able, Al Ade")
    git("rm", "-q", "ofl/zeta/static/METADATA.pb", cwd=work)
    commit("rename", "2020-02-01")
    mv("apache/delta", "ofl/delta")
    commit("license-move", "2020-03-01")
    git("rm", "-rq", "ofl/beta", cwd=work)
    write(work, {"ofl/betanew/METADATA.pb": meta("Beta New", "Xavier X")})
    commit("no-shared-designer", "2020-04-01")
    git("rm", "-rq", "ofl/eps", "ofl/zeta", cwd=work)
    write(
        work,
        {
            "ofl/epsnew/METADATA.pb": meta("Eps New", "Ed Eel"),
            "ofl/zetanew/METADATA.pb": meta("Zeta New", "Zo Zim"),
        },
    )
    commit("two-for-two", "2020-05-01")
    mv("ofl/omicron", "ofl/omicron_todelist")
    commit("park", "2020-06-01")
    mv("ofl/alphasans", "ofl/alphapro", "Alpha Pro", "Ann Able")
    commit("chain", "2020-07-01")
    # A pull request that deletes in one commit and adds in the next.
    git("checkout", "-q", "-b", "pr", cwd=work)
    git("rm", "-rq", "ofl/lam", cwd=work)
    commit("pr-delete", "2020-08-01")
    write(work, {"ofl/lambdanew/METADATA.pb": meta("Lambda New", "Lu Lee")})
    commit("pr-add", "2020-08-02")
    git("checkout", "-q", "main", cwd=work)
    merge = ["merge", "-q", "--no-ff", "--no-gpg-sign", "-m", "Merge pull request #1", "pr"]
    git(*merge, cwd=work, when="2020-08-03T12:00:00+00:00")
    sha["merge"] = git("rev-parse", "HEAD", cwd=work)
    write(
        work,
        {
            "to_delist.txt": DELIST_1,
            "ofl/gamma/METADATA.pb": meta("Gamma", "Cy Cole"),
            "ofl/kappanew/METADATA.pb": meta("Kappa New", "Kim Kay"),
            "ofl/mutext/METADATA.pb": meta("Mu Text", "Mo Moe"),
            "ofl/mudisplay/METADATA.pb": meta("Mu Display", "Mo Moe"),
            "ofl/tau/METADATA.pb": meta("Tau", "Ty Tan"),
            "ofl/rhounderline/METADATA.pb": meta("Rho Underline", "Ro Ray"),
            "ofl/omegaprime/METADATA.pb": meta("Omega Prime", "Oa Ox"),
        },
    )
    commit("delist-1", "2020-09-01")
    git("rm", "-rq", "ofl/kappa", "ofl/omega", cwd=work)
    commit("delete-listed", "2020-10-01")
    write(work, {"to_delist.txt": DELIST_2})
    commit("delist-2", "2020-11-01")
    mv("ofl/sig", "ofl/signew", "Sigma New", "Si Sun")
    commit("there", "2020-12-01")
    mv("ofl/signew", "ofl/sig", "Sigma", "Si Sun")
    commit("back", "2021-01-01")
    write(
        work,
        {
            "to_delist.txt": DELIST_3,
            "ofl/nu/METADATA.pb": meta("Nu", "Ny Nox"),
            "ofl/nustencil/METADATA.pb": meta("Nu Stencil", "Ny Nox"),
            "ofl/nudisplay/METADATA.pb": meta("Nu Display", "Ny Nox"),
            "ofl/nutext/METADATA.pb": meta("Nu Text", "Ny Nox"),
            "ofl/nustencildisplay/METADATA.pb": meta("Nu Stencil Display", "Ny Nox"),
            "ofl/xidisplay/METADATA.pb": meta("Xi Display", "Xa Xu"),
        },
    )
    commit("delist-3", "2021-02-01")

    bare = base / "fonts.git"
    git("clone", "-q", "--bare", str(work), str(bare), cwd=base)
    git("config", "uploadpack.allowFilter", "true", cwd=bare)
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare)
    return Fonts(work, bare.as_uri(), sha)


@pytest.fixture(autouse=True)
def quiet_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own git config out of the clones."""
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)


def rows(cands: list[AliasCandidate]) -> set[tuple[str, str, str, str, str, str, bool]]:
    return {
        (c.alias.ns, c.alias.key, c.target.ns, c.target.key, c.relation, c.detail, c.auto)
        for c in cands
    }


EXPECTED = {
    # rename, then chain: every old folder and name points at the current one
    ("gf-dir", "alpha", "gf-dir", "alphapro", "rename", "", True),
    ("gf-dir", "alphasans", "gf-dir", "alphapro", "rename", "", True),
    ("gf-family", "Alpha", "gf-family", "Alpha Pro", "rename", "", True),
    ("gf-family", "Alpha Sans", "gf-family", "Alpha Pro", "rename", "", True),
    # no designer in common: proposed, never auto-accepted (and the broken file was read)
    ("gf-dir", "beta", "gf-dir", "betanew", "rename", "", False),
    ("gf-family", "Beta", "gf-family", "Beta New", "rename", "", False),
    # a pull request's merge as one change
    ("gf-dir", "lam", "gf-dir", "lambdanew", "rename", "", True),
    ("gf-family", "Lambda", "gf-family", "Lambda New", "rename", "", True),
    # delist statements: the old folder of Gamma One is still at the tip, kappa's is not
    ("gf-family", "Gamma One", "gf-family", "Gamma", "rename", "", True),
    ("gf-dir", "kappa", "gf-dir", "kappanew", "rename", "", True),
    ("gf-family", "Kappa", "gf-family", "Kappa New", "rename", "", True),
    ("gf-family", "Rho Old", "gf-family", "Rho Underline", "rename", "", True),
    ("gf-dir", "omega", "gf-dir", "omegaprime", "rename", "", True),
    ("gf-family", "Omega Colored", "gf-family", "Omega Prime", "rename", "", True),
    ("gf-family", "Mu", "gf-family", "Mu Text", "related", "split", False),
    ("gf-family", "Mu", "gf-family", "Mu Display", "related", "split", False),
    ("gf-family", "Tau One", "gf-family", "Tau", "rename", "merged", False),
    ("gf-family", "Tau Two", "gf-family", "Tau", "rename", "merged", False),
    # successors named one per comment line, paired by the names' words: never auto
    ("gf-family", "Nu Display", "gf-family", "Nu", "rename", "merged", False),
    ("gf-family", "Nu Text", "gf-family", "Nu", "rename", "merged", False),
    ("gf-family", "Nu Stencil Display", "gf-family", "Nu Stencil", "rename", "", False),
    # renamed and back: only the intermediate name and folder point anywhere
    ("gf-dir", "signew", "gf-dir", "sig", "rename", "", True),
    ("gf-family", "Sigma New", "gf-family", "Sigma", "rename", "", True),
}


def test_mine_repo_finds_exactly_the_stated_renames(fonts: Fonts) -> None:
    assert rows(mine_repo(fonts.work)) == EXPECTED


def test_evidence_names_the_commits_and_list_versions(fonts: Fonts) -> None:
    by_alias = {(c.alias.key, c.target.key): c.evidence for c in mine_repo(fonts.work)}
    sha = fonts.sha
    assert by_alias[("Alpha", "Alpha Pro")] == (
        f"{commit_url(sha['rename'])} {commit_url(sha['chain'])}"
    )
    assert by_alias[("lam", "lambdanew")] == commit_url(sha["merge"])
    # the first version that states it, not the later one that repeats it
    assert by_alias[("Gamma One", "Gamma")] == delist_url(sha["delist-1"])
    assert by_alias[("Omega Colored", "Omega Prime")] == delist_url(sha["delist-2"])
    assert by_alias[("Sigma New", "Sigma")] == commit_url(sha["back"])


def test_nothing_is_paired_without_a_statement(fonts: Fonts) -> None:
    keys = {k for c in mine_repo(fonts.work) for k in (c.alias.key, c.target.key)}
    # two-for-two, license moves, parking, pull-request links and a rename back
    for name in ("eps", "Eps", "zeta", "Zeta New", "delta", "Delta", "omicron", "Omicron"):
        assert name not in keys
    assert ("Sigma", "gf-family") not in {(c.alias.key, c.alias.ns) for c in mine_repo(fonts.work)}


def test_candidates_are_sorted_and_repeatable(fonts: Fonts, tmp_path: Path) -> None:
    first, second = mine_repo(fonts.work), mine_repo(fonts.work)
    assert first == second == sorted(set(first))
    assert all(c.source == "gf_history" for c in first)
    assert all(match_key(c.alias.key) != match_key(c.target.key) for c in first)
    write_seeds(first, tmp_path / "a.csv")
    write_seeds(reversed(second), tmp_path / "b.csv")
    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()
    assert sorted(load_seeds(tmp_path / "a.csv")) == first


def test_the_miner_contract() -> None:
    assert isinstance(MINER, Miner)
    assert MINER.name == "gf_history" == gf_history.__name__.rsplit(".", 1)[1]


def context(root: Path, raw: Path) -> MineContext:
    return MineContext(Paths.for_root(root), None, RawDir(raw), date(2026, 9, 26), LOG)


def test_the_miner_clones_blobless_and_finds_the_same(
    fonts: Fonts, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{fonts.remote}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", CLONE_URL)
    ctx = context(tmp_path / "root", tmp_path / "raw")
    found = MINER.mine(ctx)
    assert found == mine_repo(fonts.work)
    clone = tmp_path / "raw" / "gf_history" / "fonts"
    checked_out = {
        p.relative_to(clone).as_posix() for p in clone.rglob("*") if ".git" not in p.parts
    }
    assert "ofl/alphapro/METADATA.pb" in checked_out
    assert not any(p.endswith(".ttf") or p == "to_delist.txt" for p in checked_out)
    # It writes nothing but its clone: no seeds (the stage writes them), never aliases.csv.
    assert not (tmp_path / "root").exists()
    # A clone kept from an earlier attempt (--keep-raw) is replaced.
    assert MINER.mine(ctx) == found


def _git_version() -> tuple[int, ...]:
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout
    return tuple(int(n) for n in out.split()[2].split(".")[:2])


@pytest.mark.skipif(_git_version() < (2, 44), reason="GIT_NO_LAZY_FETCH needs git 2.44")
def test_a_blobless_clone_is_read_in_a_few_batched_fetches(
    fonts: Fonts, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old blobs come in batches, not one request per blob (the host's rate limit):
    with git's on-demand download switched off, mining still reads every blob."""
    expected = mine_repo(fonts.work)
    clone = tmp_path / "fonts"
    gitsrc.sparse_clone(fonts.remote, clone, CHECKOUT, depth=None)
    monkeypatch.setenv("GIT_NO_LAZY_FETCH", "1")
    with monkeypatch.context() as m:  # the guard works: without batches, reading fails
        m.setattr(gf_history.Blobs, "prefetch", lambda self, blobs: None)
        with pytest.raises(gitsrc.GitError):
            mine_repo(clone)
    fetches: list[tuple[str, ...]] = []
    real = gf_history._git

    def spy(repo: Path, *args: str, check: bool = True) -> bytes:
        if "fetch" in args:
            fetches.append(args)
        return real(repo, *args, check=check)

    monkeypatch.setattr(gf_history, "_git", spy)
    assert mine_repo(clone) == expected
    assert 1 <= len(fetches) <= 3


# --- the committed seeds ------------------------------------------------------------------------


def test_committed_seeds_are_canonical_and_well_formed(tmp_path: Path) -> None:
    seeds = load_seeds(SEEDS)
    assert seeds, f"{SEEDS} is empty"
    write_seeds(seeds, tmp_path / "again.csv")
    assert (tmp_path / "again.csv").read_bytes() == SEEDS.read_bytes()
    for c in seeds:
        assert c.source == "gf_history"
        assert c.relation in {"rename", "related"}
        assert c.alias.ns == c.target.ns
        assert c.alias.ns in {"gf-family", "gf-dir"}
        assert match_key(c.alias.key) != match_key(c.target.key)
        assert all(u.startswith("https://github.com/google/fonts/") for u in c.evidence.split())
        assert not c.auto or (c.relation == "rename" and not c.detail)


KNOWN = {
    ("gf-family", "Muli", "Mulish", True),
    ("gf-dir", "muli", "mulish", True),
    ("gf-family", "BBH Sans Bartle", "BBH Bartle", True),
    ("gf-family", "42dot Sans", "Asta Sans", True),
    ("gf-family", "Saira Stencil One", "Saira Stencil", True),
    ("gf-family", "Montserrat Subrayada", "Montserrat Underline", True),
}


def test_committed_seeds_hold_the_known_renames() -> None:
    have = {(c.alias.ns, c.alias.key, c.target.key, c.auto) for c in load_seeds(SEEDS)}
    assert have >= KNOWN


@pytest.mark.network
def test_real_google_fonts_history(tmp_path: Path) -> None:
    """One real run: a blobless full-history clone of github.com/google/fonts (about 25 MB)."""
    start = time.monotonic()
    found = MINER.mine(context(tmp_path / "root", tmp_path / "raw"))
    LOG.info("gf_history real run: %d candidates in %.1f s", len(found), time.monotonic() - start)
    have = {(c.alias.ns, c.alias.key, c.target.key, c.auto) for c in found}
    assert have >= KNOWN
    assert all(match_key(c.alias.key) != match_key(c.target.key) for c in found)
