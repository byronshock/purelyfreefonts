"""The gf_history miner's rules, on hand-made inputs (no git).

Names are synthetic; the ``to_delist.txt`` texts copy the shapes found in the
real file's history (``replaced by`` comments, specimen links, CRLF endings,
pull-request links).
"""

import pytest

from tff_catalog.aliases.miners import gf_history
from tff_catalog.aliases.miners.gf_history import (
    Commit,
    FileChange,
    Meta,
    Ref,
    Statement,
    Step,
    Tip,
    collapse,
    commit_url,
    parse_delist,
    parse_log,
    read_meta,
    rename_candidates,
    replaced_folder,
    resolve,
    resolve_successors,
    successor_refs,
)

Z = "0" * 40
B1, B2 = "1" * 40, "2" * 40


def meta(name: str, *designers: str) -> Meta:
    return Meta(name, frozenset(d.casefold() for d in designers))


def step(t: int, old: str, new: str, *, kind: str = "dir", ok: bool = True, merged: bool = False):
    return Step((t, f"{t:040x}"), kind, old, new, commit_url(f"{t:040x}"), ok, merged)  # type: ignore[arg-type]


# --- METADATA.pb ----------------------------------------------------------------------------


def test_read_meta_takes_the_top_level_fields() -> None:
    text = b"""name: "Alpha Sans"
designer: "Ann Able, Al Ade"
license: "OFL"
fonts {
  name: "Alpha Sans"
  designer: "Somebody Else"
  full_name: "Alpha Sans Regular"
}
"""
    assert read_meta(text) == Meta("Alpha Sans", frozenset({"ann able", "al ade"}))


def test_read_meta_decodes_textproto_escapes_and_quotes() -> None:
    assert read_meta(b'name: "Caf\\303\\251 One"  # a comment\n').name == "Café One"
    assert read_meta("name: 'Café \\'Two\\''\n".encode()).name == "Café 'Two'"
    assert read_meta(b'name: "Say \\"Hi\\""\ndesigner: "A\\x42"\n') == meta('Say "Hi"', "AB")


def test_read_meta_survives_files_the_proto_parser_refuses() -> None:
    # Seen in the history: an unterminated string in a nested field.
    text = b'name: "Beta"\ndesigner: "Bob Brown"\nfonts {\n  full_name: Beta Regular"\n}\n'
    assert read_meta(text) == meta("Beta", "Bob Brown")


def test_read_meta_without_a_name() -> None:
    assert read_meta(b'  name: "Nested only"\nlicense: "OFL"\n') == Meta(None)
    assert read_meta(b'name: "  "\n').name is None
    assert read_meta(b"").designers == frozenset()


# --- git log ----------------------------------------------------------------------------------


def test_parse_log_reads_raw_z_output() -> None:
    out = (
        f"\x1e{'a' * 40} 1700000000\0\n"
        f":100644 000000 {B1} {Z} D\0ofl/old/METADATA.pb\0"
        f":000000 100644 {Z} {B2} A\0ofl/new name/METADATA.pb\0"
        f"\x1e{'b' * 40} 1700000100\0"  # a commit whose changes the filter dropped
        f"\x1e{'c' * 40} 1700000200\0\n:100644 100644 {B1} {B2} M\0to_delist.txt\0"
    )
    commits = parse_log(out)
    assert [c.sha[0] for c in commits] == ["a", "b", "c"]
    assert commits[0].files == (
        FileChange("D", "ofl/old/METADATA.pb", B1, Z),
        FileChange("A", "ofl/new name/METADATA.pb", Z, B2),
    )
    assert commits[1].files == ()
    assert commits[2].when == (1700000200, "c" * 40)
    assert commits[2].files[0].status == "M"


def change(*files: tuple[str, str]) -> Commit:
    return Commit("f" * 40, 1, tuple(FileChange(s, p, B1, B2) for s, p in files))


def test_one_folder_replaced_by_another_pairs() -> None:
    pair = replaced_folder(change(("D", "ofl/muli/METADATA.pb"), ("A", "ofl/mulish/METADATA.pb")))
    assert pair is not None
    assert (pair[0].path, pair[1].path) == ("ofl/muli/METADATA.pb", "ofl/mulish/METADATA.pb")


@pytest.mark.parametrize(
    "files",
    [
        # a license move is the same folder name
        [("D", "apache/roboto/METADATA.pb"), ("A", "ofl/roboto/METADATA.pb")],
        # parking a family for deletion is no rename
        [("D", "ofl/nko/METADATA.pb"), ("A", "ofl/nko_todelist/METADATA.pb")],
        # nor is deleting a parked one while adding another
        [("D", "ofl/nko_todelist/METADATA.pb"), ("A", "ofl/other/METADATA.pb")],
        # two for two pairs nothing
        [
            ("D", "ofl/a/METADATA.pb"),
            ("D", "ofl/b/METADATA.pb"),
            ("A", "ofl/c/METADATA.pb"),
            ("A", "ofl/d/METADATA.pb"),
        ],
        [("D", "ofl/a/METADATA.pb")],
        [("A", "ofl/a/METADATA.pb")],
    ],
)
def test_changes_that_pair_nothing(files: list[tuple[str, str]]) -> None:
    assert replaced_folder(change(*files)) is None


def test_a_parked_family_does_not_hide_a_real_replacement() -> None:
    pair = replaced_folder(
        change(
            ("D", "ofl/nko/METADATA.pb"),
            ("A", "ofl/nko_todelist/METADATA.pb"),
            ("D", "ofl/old/METADATA.pb"),
            ("A", "ofl/new/METADATA.pb"),
        )
    )
    assert pair is not None
    assert (pair[0].path, pair[1].path) == ("ofl/old/METADATA.pb", "ofl/new/METADATA.pb")


# --- to_delist.txt ----------------------------------------------------------------------------


def test_parse_delist_statements() -> None:
    text = """
# will be replaced by Gamma (VF version)
https://fonts.google.com/specimen/Gamma+One

# is replaced by ofl/kappanew
ofl/kappa # https://github.com/google/fonts/pull/1

# will be replaced by https://fonts.google.com/noto/specimen/Noto+Sans+Phi (no space)
https://fonts.google.com/noto/specimen/Noto+Sans+P+Hi

# Will be replaced by https://github.com/google/fonts/pull/2
https://fonts.google.com/specimen/Zeta+New

# will be replaced by Mu Text and Mu Display
https://fonts.google.com/specimen/Mu

# still in dev
ofl/upsilon
ofl/chi

# Deleted
# Deleted: ofl/phi/EARLY_ACCESS.category # https://github.com/google/fonts/pull/3
lang/languages/xx_Test.textproto # https://github.com/google/fonts/pull/4

ofl/rho # replaced by ofl/rhonew
ofl/sigma
"""
    assert parse_delist(text) == [
        Statement((Ref("name", "Gamma One"),), (Ref("text", "Gamma"),)),
        Statement((Ref("dir", "ofl/kappa"),), (Ref("dir", "ofl/kappanew"),)),
        Statement((Ref("name", "Noto Sans P Hi"),), (Ref("name", "Noto Sans Phi"),)),
        Statement((Ref("name", "Zeta New"),), ()),
        Statement((Ref("name", "Mu"),), (Ref("text", "Mu Text and Mu Display"),)),
        Statement((Ref("dir", "ofl/rho"),), (Ref("dir", "ofl/rhonew"),)),
    ]


def test_parse_delist_published_instead_with_crlf() -> None:
    text = (
        "# Delist Rho Olde (we published Rho Underline instead)\r\n"
        "https://fonts.google.com/specimen/Rho+Old\r\n"
    )
    assert parse_delist(text) == [
        Statement((Ref("name", "Rho Old"),), (Ref("text", "Rho Underline"),))
    ]


def test_parse_delist_needs_exactly_one_successor_per_block() -> None:
    text = "# replaced by Alpha\n# replaced by Beta\nofl/gamma\n\n# replaced by Tau\n\nofl/x\n"
    assert parse_delist(text) == []


def test_a_comment_covers_only_the_entries_under_it() -> None:
    # A later comment ends the block: ofl/beta is not replaced by Alpha.
    text = "# will be replaced by Alpha\n# see the pull request\nofl/alphaold\n# still in dev\nofl/beta\n"
    assert parse_delist(text) == [Statement((Ref("dir", "ofl/alphaold"),), (Ref("text", "Alpha"),))]
    # Nor does a successor comment reach past its block, or across a blank line.
    text = "# replaced by Alpha\nofl/a\n# replaced by Beta\nofl/b\n# replaced by Gamma\n\nofl/c\n"
    assert parse_delist(text) == [
        Statement((Ref("dir", "ofl/a"),), (Ref("text", "Alpha"),)),
        Statement((Ref("dir", "ofl/b"),), (Ref("text", "Beta"),)),
    ]


def test_parse_delist_several_entries_share_a_statement() -> None:
    text = "# will be replaced by Tau\nofl/tauone\nhttps://fonts.google.com/specimen/Tau+Two\n"
    assert parse_delist(text) == [
        Statement((Ref("dir", "ofl/tauone"), Ref("name", "Tau Two")), (Ref("text", "Tau"),))
    ]


@pytest.mark.parametrize(
    ("phrase", "refs"),
    [
        ("Gamma (VF version)", (Ref("text", "Gamma"),)),
        ("ofl/kappanew", (Ref("dir", "ofl/kappanew"),)),
        ("https://github.com/google/fonts/pull/2", ()),
        (
            "https://fonts.google.com/noto/specimen/Noto+Sans+Phi (no space)",
            (Ref("name", "Noto Sans Phi"),),
        ),
        ("Mu Text and Mu Display.", (Ref("text", "Mu Text and Mu Display"),)),
        ("(upcoming)", ()),
    ],
)
def test_successor_refs(phrase: str, refs: tuple[Ref, ...]) -> None:
    assert successor_refs(phrase) == refs


TIP = Tip(
    {
        "gamma": meta("Gamma", "Cy Cole"),
        "gammaone": meta("Gamma One", "Cy Cole"),
        "mutext": meta("Mu Text"),
        "mudisplay": meta("Mu Display"),
        "rockandroll": meta("Rock and Roll"),
        "twin": meta("Twin"),
        "twin2": meta("TWIN"),  # two folders with one name: the name resolves to neither
    }
)


@pytest.mark.parametrize(
    ("refs", "folders"),
    [
        ((Ref("text", "Gamma"),), ("gamma",)),
        ((Ref("name", "gamma one"),), ("gammaone",)),
        ((Ref("dir", "ofl/gamma"),), ("gamma",)),
        ((Ref("text", "Mu Text and Mu Display"),), ("mutext", "mudisplay")),
        ((Ref("text", "Mu Text, Mu Display"),), ("mutext", "mudisplay")),
        ((Ref("text", "Rock and Roll"),), ("rockandroll",)),  # a whole name wins over a split
        ((Ref("text", "Mu Text and Mu Serif"),), None),  # one name is no family at the tip
        ((Ref("dir", "ofl/nowhere"),), None),
        ((Ref("name", "Twin"),), None),
        ((), None),
    ],
)
def test_resolve_successors(refs: tuple[Ref, ...], folders: tuple[str, ...] | None) -> None:
    assert resolve_successors(refs, TIP) == folders


def test_tip_leaves_out_parked_folders(tmp_path) -> None:
    for folder, name in (("ofl/nko", "N Ko"), ("ofl/nko_todelist", "N Ko"), ("apache/x", "X")):
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / "METADATA.pb").write_text(f'name: "{name}"\n')
    (tmp_path / "ofl/x/static").mkdir(parents=True)
    (tmp_path / "ofl/x/static/METADATA.pb").write_text('name: "X static"\n')
    tip = Tip.read(tmp_path)
    assert sorted(tip.folders) == ["nko", "x"]
    assert tip.folder_named("NKo") == "nko"


# --- chains -----------------------------------------------------------------------------------


def chain_of(steps: list[Step], node: str, kind: str = "dir") -> list[tuple[str, str]]:
    return [(s.old, s.new) for s in resolve(collapse(steps))[(kind, node)]]  # type: ignore[index]


def test_a_chain_follows_later_renames() -> None:
    steps = [step(1, "a", "b"), step(2, "b", "c"), step(3, "c", "d")]
    assert chain_of(steps, "a") == [("a", "b"), ("b", "c"), ("c", "d")]
    assert chain_of(steps, "c") == [("c", "d")]


def test_a_chain_never_goes_back_in_time() -> None:
    # b became c before a became b: a's successor is b, not c.
    steps = [step(1, "b", "c"), step(2, "a", "b")]
    assert chain_of(steps, "a") == [("a", "b")]


def test_a_rename_back_ends_where_it_began() -> None:
    steps = [step(1, "a", "b"), step(2, "b", "a")]
    assert chain_of(steps, "a") == [("a", "b"), ("b", "a")]
    assert chain_of(steps, "b") == [("b", "a")]


def test_a_name_starts_from_its_latest_rename() -> None:
    # a became b, came back, and became c: a's successor is c.
    steps = [step(1, "a", "b"), step(2, "b", "a"), step(3, "a", "c")]
    assert chain_of(steps, "a") == [("a", "c")]


def test_repeats_count_once_at_their_first_sighting() -> None:
    steps = [step(1, "a", "b", ok=False), step(5, "a", "b", ok=True), step(3, "b", "c")]
    kept = collapse(steps)
    assert [(s.when[0], s.old, s.new) for s in kept] == [(1, "a", "b"), (3, "b", "c")]
    assert kept[0].corroborated  # the repeat corroborates the first sighting
    assert chain_of(steps, "a") == [("a", "b"), ("b", "c")]


def test_a_repeat_after_a_rename_back_is_a_new_rename() -> None:
    steps = [step(1, "a", "b"), step(2, "b", "a"), step(3, "a", "b")]
    assert len(collapse(steps)) == 3
    assert chain_of(steps, "a") == [("a", "b")]


def test_a_node_renamed_two_ways_at_once_has_no_chain() -> None:
    # One list version sends b to both c and d: neither is guessed, nor is a's chain through b.
    steps = [step(1, "a", "b"), step(2, "b", "c"), step(2, "b", "d"), step(1, "x", "y")]
    chains = resolve(collapse(steps))
    assert set(chains) == {("dir", "x")}
    # The same name twice at once (by match_key) is one rename, not two.
    steps = [step(1, "Alpha", "Beta", kind="name"), step(1, "Alpha", "BETA", kind="name")]
    assert ("name", "alpha") in resolve(collapse(steps))


def test_names_chain_by_match_key() -> None:
    steps = [
        step(1, "Alpha", "Alpha Sans", kind="name"),
        step(2, "ALPHA  SANS", "Alpha Pro", kind="name"),
    ]
    assert chain_of(steps, "alpha", "name") == [
        ("Alpha", "Alpha Sans"),
        ("ALPHA  SANS", "Alpha Pro"),
    ]


def test_rename_candidates() -> None:
    tip = Tip({"alphapro": meta("Alpha Pro"), "b": meta("B"), "tau": meta("Tau")})
    steps = [
        step(1, "alpha", "alphasans"),
        step(2, "alphasans", "alphapro", ok=False),
        step(1, "b", "c"),  # b is still at the tip: its own key
        step(1, "x", "gone"),  # the successor left the tip
        step(1, "Alpha", "Alpha Pro", kind="name"),
        step(1, "Tau One", "Tau", kind="name", merged=True),
        step(1, "Twin", "twin", kind="name"),  # the same name by match_key
    ]
    got = {
        (c.alias.ns, c.alias.key, c.target.key, c.relation, c.detail, c.auto): c.evidence
        for c in rename_candidates(resolve(collapse(steps)), tip)
    }
    assert got == {
        ("gf-dir", "alpha", "alphapro", "rename", "", False): (
            f"{commit_url(f'{1:040x}')} {commit_url(f'{2:040x}')}"
        ),
        ("gf-dir", "alphasans", "alphapro", "rename", "", False): commit_url(f"{2:040x}"),
        ("gf-family", "Alpha", "Alpha Pro", "rename", "", True): commit_url(f"{1:040x}"),
        ("gf-family", "Tau One", "Tau", "rename", "merged", False): commit_url(f"{1:040x}"),
    }


def test_the_shared_designer_check_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    tip = Tip({"b": meta("B")})
    steps = [step(1, "a", "b", ok=False), step(1, "A One", "B", kind="name", merged=True)]

    def autos() -> dict[str, bool]:
        return {c.alias.key: c.auto for c in rename_candidates(resolve(collapse(steps)), tip)}

    assert autos() == {"a": False, "A One": False}
    monkeypatch.setattr(gf_history, "AUTO_NEEDS_SHARED_DESIGNER", False)
    assert autos() == {"a": True, "A One": False}  # a merged row is never auto
