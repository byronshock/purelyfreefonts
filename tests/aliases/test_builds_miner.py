"""Alias miner "builds" (build variants the universe made families of their own)."""

import pytest

from tff_catalog.aliases.miners import builds


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Maple Mono NF CN", ("Maple Mono", "nf")),
        ("Maple Mono CN", ("Maple Mono", "cjk")),
        ("Cascadia Code PL", ("Cascadia Code", "powerline")),
        ("Monaspace Var", ("Monaspace", "variable")),
        ("JetBrains Mono NL", ("JetBrains Mono", "nl")),
        ("TeX Gyre Heros Cn", None),  # condensed, not CJK
        ("NF", None),
        ("Inter", None),
    ],
)
def test_split_build(name: str, expected: tuple[str, str] | None) -> None:
    assert builds.split_build(name) == expected


def test_the_miner_is_discovered() -> None:
    from tff_catalog.aliases import miners

    assert miners.get("builds") is builds.MINER


def test_mine_proposes_each_key_of_a_build_family(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import logging
    from datetime import date

    from tff_catalog import stageio
    from tff_catalog.aliases.miners import MineContext
    from tff_catalog.paths import Paths
    from tff_catalog.records import SourceKey
    from tff_catalog.store import RawDir
    from tff_catalog.universe import Family, Universe

    day = date(2026, 10, 3)

    def fam(fid: str, name: str, *keys: str) -> Family:
        return Family(
            id=fid,
            family=name,
            keys=tuple(SourceKey("brew-cask", k) for k in keys),
            sources=("homebrew_casks",),
            first_seen=day,
            minted_from=name,
        )

    u = Universe(
        families={
            "maple-mono": fam("maple-mono", "Maple Mono", "font-maple-mono"),
            "maple-mono-nf-cn": fam(
                "maple-mono-nf-cn", "Maple Mono NF CN", "font-maple-mono-nf-cn"
            ),
            "playwrite-nl": fam("playwrite-nl", "Playwrite NL", "font-playwrite-nl"),
        },
        unmapped=(),
    )
    paths = Paths.for_root(tmp_path)
    stageio.dump_stage(paths, "universe", u)
    ctx = MineContext(paths, None, RawDir(tmp_path / "raw"), day, logging.getLogger("t"))
    (cand,) = builds.MINER.mine(ctx)
    assert (cand.alias.key, cand.target.key, cand.relation, cand.detail, cand.auto) == (
        "font-maple-mono-nf-cn",
        "font-maple-mono",
        "build",
        "nf",
        False,
    )
