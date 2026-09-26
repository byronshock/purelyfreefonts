"""Alias miner "builds": build variants the universe made families of their own.

Homebrew ships a family's builds as casks of their own ("font-maple-mono-nf-cn",
"font-cascadia-code-pl", "font-monaspace-var"), and the universe stage mints a
family for each cask it cannot fold. Methodology §2 folds such builds into their
family (D7 credits their installs to it); the owner's gate A ruling of 2026-09-26
(A_structure) confirmed it for the builds seen then, which are hand rows in
``data/aliases.csv``. This miner proposes the next ones, so a new build cask is
asked about once instead of ranking as a family.

**Input.** ``build/stage/universe.json`` of this run (stage "universe" runs
before "aliases"); no request.

**Candidates.** A family (not dropped) whose display name is another family's
name followed by build tokens (``TOKENS``, case-sensitive, so "Cn" for condensed
is never read as CJK) gives one candidate per universe key of the build: relation
``build``, detail from the last token that names one (``DETAILS``), target the
parent's first universe key, never ``auto``: the owner accepts each at gate A,
where a plain proposal is asked in a batch with its recommendation. A parent that
is itself such a build, or a name several families hold, gives nothing.
"""

import re
from collections import defaultdict
from typing import ClassVar

from tff_catalog import stageio
from tff_catalog.aliases import AliasCandidate
from tff_catalog.aliases.miners import MineContext
from tff_catalog.keys import match_key

NAME = "builds"
# Trailing words that mark a build, as Homebrew and Nerd Fonts spell them.
TOKENS = ("NF", "NFM", "NFP", "CN", "PL", "NL", "Var", "Variable", "Nerd Font")
DETAILS = {
    "NF": "nf",
    "NFM": "nfm",
    "NFP": "nfp",
    "Nerd Font": "nerd",
    "CN": "cjk",
    "PL": "powerline",
    "NL": "nl",
    "Var": "variable",
    "Variable": "variable",
}
_SUFFIX = re.compile(
    r"(?:\s+(?:" + "|".join(sorted(map(re.escape, TOKENS), key=len, reverse=True)) + r"))+\Z"
)
_ORDER = ("nerd", "nf", "nfm", "nfp", "powerline", "cjk", "nl", "variable")


def split_build(name: str) -> tuple[str, str] | None:
    """(parent name, build detail) when ``name`` ends in build tokens, else None."""
    m = _SUFFIX.search(name)
    if m is None or m.start() == 0:
        return None
    found = [DETAILS[t] for t in TOKENS if re.search(rf"\s{re.escape(t)}(?=\s|\Z)", m.group(0))]
    detail = min(found, key=_ORDER.index) if found else ""
    return name[: m.start()], detail


class _Miner:
    name: ClassVar[str] = NAME

    def mine(self, ctx: MineContext) -> list[AliasCandidate]:
        """The candidates of this run's universe (see the module docstring)."""
        path = stageio.stage_path(ctx.paths, "universe")
        if not path.is_file():
            ctx.log.warning("%s: no %s yet; nothing to mine", NAME, path)
            return []
        u = stageio.load_stage(ctx.paths, "universe")
        by_name: dict[str, list[str]] = defaultdict(list)
        for fid, fam in u.families.items():
            if fam.drop is None:
                by_name[match_key(fam.family)].append(fid)
        out: list[AliasCandidate] = []
        for _, fam in sorted(u.families.items()):
            split = split_build(fam.family) if fam.drop is None else None
            if split is None:
                continue
            parent_name, detail = split
            parents = by_name.get(match_key(parent_name), [])
            if len(parents) != 1:
                continue
            parent = u.families[parents[0]]
            if split_build(parent.family) is not None or not parent.keys:
                continue
            target = sorted(parent.keys)[0]
            evidence = f"universe: {fam.family} is {parent.family} plus build tokens"
            out.extend(
                AliasCandidate(key, target, "build", detail, NAME, evidence, auto=False)
                for key in sorted(fam.keys)
            )
        ctx.log.info("%s: %d candidates", NAME, len(out))
        return out


MINER = _Miner()
