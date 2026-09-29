# Nerd Fonts download link — spike findings (TASK-2)

Investigated 2026-09-28 for the owner's ruling. Question: should a catalog font
that has a Nerd Fonts build also offer a Nerd Fonts download link beside its
official one? Evidence: Nerd Fonts `fonts.json` (master, 2026-09-28, MIT per
terms ruling T1), the Nerd Fonts glyph-set table (`src/glyphs/README.md`),
GitHub's license API, and HTTP checks of the candidate link targets the same day.

Context: about 59% of Homebrew font installs are Nerd builds
([ranking-methodology.md](ranking-methodology.md), known biases), and D7 counts
those installs toward the original family. The site today has room for only
`links.primary` and `links.designer`
([schemas/catalog-site.schema.json](../schemas/catalog-site.schema.json)), and
M1 step 14 never allows an aggregator as the primary link.

## 1. Which fonts

The catalog does not exist yet — M1 steps 8–13 have not run — so the exact
intersection cannot be listed. It will fall out of the pipeline for free:
`data/aliases.csv` already models patched builds (`relation: build`, `detail:
nerd`), and `fonts.json` maps each build to its original
(`unpatchedName` → `patchedName`), including the renames. The full mapping
(72 builds) is in the appendix.

Renames the alias table must carry, e.g.: SauceCodePro (Source Code Pro),
CaskaydiaCove/CaskaydiaMono (Cascadia Code/Mono), LiterationMono (Liberation
Mono), BlexMono (IBM Plex Mono), ShureTechMono (Share Tech Mono), AnonymicePro
(Anonymous Pro), BitstromWera (Bitstream Vera Sans Mono), DejaVuSansM (DejaVu
Sans Mono), IntoneMono (Intel One Mono), Monaspice (Monaspace), iMWriting
(iA Writer), Hurmit (Hermit), Terminess (Terminus), RecMono (Recursive Mono),
FiraMono (Fira), JetBrainsMono (JetBrains Mono), GeistMono (Geist Mono).

Likely catalog members with a Nerd build (from the methodology's own examples
and the prior top-100 library, to be confirmed by the first full run):
JetBrains Mono (the §6 worked example), Meslo LG (D7's Powerlevel10k note),
Maple Mono (the `cjk_build_credit` note), Anonymous Pro (prior rank 82), Geist
Mono, IBM Plex Mono, Share Tech Mono, Fira Code and Fira Mono, Cascadia Code,
Hack, Source Code Pro, Roboto Mono, DejaVu Sans Mono, Liberation Mono,
Inconsolata, Iosevka, Ubuntu/Ubuntu Mono, Victor Mono, Space Mono.

`Symbols Only` is in `fonts.json` but is not a family; the methodology already
drops it as a source term.

Each patched font ships in three variants — *Nerd Font* (double-width glyphs),
*Nerd Font Mono* (single-width) and *Nerd Font Propo* — which the alias
details `nf`/`nfm`/`nfp` already cover. One link serves all three.

## 2. Licenses (Rules 1–4)

**Base fonts.** Of the 72 builds' `licenseId`s: OFL-1.1 (49, RFN and no-RFN),
Apache-2.0 (4), MIT (4+2 dual), Bitstream Vera (3+1), Ubuntu Font License (3),
BSD-3-Clause (1), BSD-3-Clause-Clear (1), WTFPL (1), two custom freeware
licenses, and one CC-BY-SA-4.0. All pass Rule 1 except:

- **BigBlue Terminal (CC-BY-SA-4.0)** — copyleft without a font exception,
  excluded by D3. The original would never enter the catalog either, so no
  Nerd link would exist for it. No action needed.
- **Overpass** is dual-licensed "OFL-1.1-no-RFN or LGPL-2.1-only"; the OFL
  choice qualifies (D3 excludes plain LGPL, not a dual license whose OFL arm is
  chosen).
- **Monofur and Heavy Data** (custom freeware licenses): both grant free
  personal and commercial use, so they pass Rule 1; neither explicitly grants
  modification, which is Nerd Fonts' upstream concern, not ours — we link, we
  do not host or modify. Neither family is likely to reach the catalog.
- RFN builds are compliant: Nerd Fonts renames every RFN original (that is why
  SauceCodePro, CaskaydiaCove etc. exist). We only link, so RFN never binds us.

**Patched icon sets** (`src/glyphs/README.md`, 2026-09-28) — all 14 pass:

| Icon set | License | Rule 1 |
|---|---|---|
| Codicons | CC BY 4.0 | passes, attribution |
| Devicons | MIT | passes |
| extraglyphs (from Hack) | MIT | passes |
| Font Awesome | CC BY 4.0 | passes, attribution |
| Font Awesome Extension | MIT | passes |
| Font Logos | Unlicense | passes (see below) |
| MaterialDesign | Apache 2.0 | passes |
| Octicons | MIT | passes |
| Seti and original | MIT | passes |
| Pomicons | OFL 1.1 (RFN) | passes |
| Powerline Extra Symbols | MIT | passes |
| Powerline Symbols | MIT | passes |
| Power Symbols IEC | MIT | passes |
| Weather Icons | OFL 1.1 | passes |

- The glyph table writes Font Logos as "unlicensed"; that means **The
  Unlicense** — GitHub's license API reports `spdx_id: Unlicense` for
  `lukas-w/font-logos`. A public-domain-style grant; it passes.
- The two CC BY 4.0 sets require attribution when shared. D3 already qualifies
  CC-BY fonts with an attribution badge, and Nerd Fonts credits every glyph set
  in each patched font's README, which our link leads to. No extra duty for us
  beyond not stripping that credit (we host nothing).
- The Nerd Fonts patcher itself is MIT (terms ruling T1 already treats
  `fonts.json` as open).

**Conclusion: no Nerd build introduces a disqualifier.** A Nerd link adds no
license risk beyond what the catalog family already cleared.

## 3. Link target

Step 14 rules apply to this link by analogy: a page, never a release asset or
`/releases/latest`, and the text names the destination. Candidates (all HTTP
200 on 2026-09-28):

- **Recommended: `https://github.com/ryanoasis/nerd-fonts/releases`** — the
  repository's releases *page*, the exact target class step 14 already allows
  for non-Google fonts. One hop to the per-font zips of the current release.
  One shared URL for every font; the Nerd Fonts site is a JS app with no stable
  per-font deep links or anchors.
- Alternative: `https://www.nerdfonts.com/font-downloads` — the project's own
  glyph-search and download page. Friendlier, but its download buttons point at
  `/releases/latest/download/…` assets, and search is JS-only with no
  prefill parameter found.
- Rejected: `/releases/latest` and direct asset URLs (step 14), the
  `nerdfonts.com` home page (no closer to the download than the releases page).

Nerd Fonts is the official source of the patched builds, not an aggregator, and
this is a secondary link — the step 14 aggregator ban is about `primary`.

**Proposed link text:** "Nerd Fonts build", shown with its destination per the
M2 step 4 convention ("GitHub: ryanoasis/nerd-fonts"), which the schema's
`link.label` supports.

## 4. Placement

- **Details panel only** (M2 step 4, next to the official and designer links).
  The row (M2 step 3) already carries the official download link; a second link
  per row adds clutter on phones and bytes against the 100 KB budget, for a
  niche need.
- **Every view, no view gating.** The link is an attribute of the font; only
  fonts with a build show it. In practice those are almost all monospace, so it
  surfaces mostly in Coding and Developers & apps anyway. View gating would add
  logic for no gain.
- Bonus, no schema work: patched names reach search through the existing
  `aliases[]` (`relation: build`) and the details panel's "also known as" tags,
  so "SauceCodePro" already finds Source Code Pro once the alias table is
  seeded from `fonts.json`.

## 5. Schema change and timing

Add one nullable field, mirroring `designer`:

- `schemas/catalog-site.schema.json`: `font.links.required` becomes
  `["primary", "designer", "nerd"]`, with `"nerd": oneOf(null, $defs/link)`.
  `schema_version` stays `1.0.0-draft`.
- `schemas/catalog.schema.json`: same change to `links`.
- `src/tff_catalog/links.py`: `Links` dataclass gains `nerd: Link | None =
  None`. The step 14 stage is still a stub (`NotImplementedError`), so the
  implementation can emit the field from day one — the URL is one constant, set
  when the family has a `build`/`nerd` alias. No per-font lookup, and the
  monthly link check covers one extra URL in total.
- `tests/fixtures/catalog-site.sample.json`: give one synthetic monospace font
  a `nerd` link (the fixture is owner-approved under M2 step 2 — this ruling
  covers that re-approval).
- Size: ~90 bytes per Nerd font in `catalog-site.json`; negligible against the
  budget even if half the catalog has builds.

**Timing: it must land before the M1 step 20 v1 freeze, and it is cheapest
before step 14/15 are implemented (both still stubs).** After the freeze the
same change means a `schema_version` bump, a new data version and migration of
anything already published. The seed of `data/aliases.csv` from `fonts.json`
(the mapping stage's job) is independent of this decision and already planned.

## 6. Recommendation

Add `links.nerd` now, before the v1 freeze:

1. Field: nullable `links.nerd` in both schemas, populated for every family
   with a Nerd build per the alias table.
2. Target: `https://github.com/ryanoasis/nerd-fonts/releases` (one constant).
3. Text: "Nerd Fonts build", destination shown per M2 step 4's convention.
4. Placement: details panel only, in every view.
5. Build work: fold into M1 step 14 (links stage), step 15 (schemas, sample
   fixture) and M2 step 4 (details panel line) — no new milestone needed.

The owner's ruling goes to AUTHORITY.md; the checklist edits go to
docs/milestone-1.md and docs/milestone-2.md in the same pull request.

## 7. Ruling (2026-09-28)

Adopted, with one change to the recommendation: the link shows **on the list
row as well as in the details panel** (§4's "details panel only" is overruled).
Everything else stands: nullable `links.nerd` in both schemas before the M1
step 20 freeze, the GitHub releases page as the one shared target, "Nerd Fonts
build" as the text, every view, no view gating. Recorded in AUTHORITY.md (Site
(Milestone 2)) and `data/reviews/site/2026-09-28.toml`; applied in
docs/milestone-1.md (steps 14, 15) and docs/milestone-2.md (steps 2, 3, 4).

## Appendix: the 72 `fonts.json` builds (2026-09-28)

| Original | Build | License | Renamed |
|---|---|---|---|
| 0xProto | 0xProto | OFL-1.1-no-RFN | |
| Adwaita Mono | AdwaitaMono | OFL-1.1-no-RFN | yes |
| Agave | Agave | OFL-1.1-no-RFN | |
| Annotation Mono | AnnotationM | OFL-1.1-no-RFN | yes |
| Anonymous Pro | AnonymicePro | OFL-1.1-RFN | yes |
| Arimo | Arimo | OFL-1.1-no-RFN | |
| Atkinson Hyperlegible Mono | AtkynsonMono | OFL-1.1-RFN | yes |
| Aurulent Sans Mono | AurulentSansM | OFL-1.1-no-RFN | yes |
| BigBlue Terminal | BigBlueTerm | CC-BY-SA-4.0 | yes |
| Bitstream Vera Sans Mono | BitstromWera | Bitstream-Vera | yes |
| Cascadia Code | CaskaydiaCove | OFL-1.1-RFN | yes |
| Cascadia Mono | CaskaydiaMono | OFL-1.1-RFN | yes |
| Code New Roman | CodeNewRoman | OFL-1.1-no-RFN | yes |
| Comic Shanns Mono | ComicShannsMono | MIT | yes |
| Commit Mono | CommitMono | OFL-1.1-no-RFN | yes |
| Cousine | Cousine | OFL-1.1-no-RFN | |
| D2Coding | D2KodingLigature | OFL-1.1-RFN | yes |
| DaddyTimeMono | DaddyTimeMono | OFL-1.1-no-RFN | |
| DejaVu Sans Mono | DejaVuSansM | Bitstream-Vera | yes |
| Departure Mono | DepartureMono | OFL-1.1-no-RFN | yes |
| Droid Sans Mono | DroidSansM | Apache-2.0 | yes |
| Envy Code R | EnvyCodeR | OFL-1.1-RFN | yes |
| Fantasque Sans Mono | FantasqueSansM | OFL-1.1-no-RFN | yes |
| Fira | FiraMono | OFL-1.1-no-RFN | yes |
| Fira Code | FiraCode | OFL-1.1-no-RFN | yes |
| Geist Mono | GeistMono | OFL-1.1-no-RFN | yes |
| Go Mono | GoMono | BSD-3-Clause-Clear | yes |
| Gohu | GohuFont | WTFPL | yes |
| Google Sans Code | GoogleSansCode | OFL-1.1-no-RFN | yes |
| Hack | Hack | Bitstream-Vera AND MIT | |
| Hasklig | Hasklug | OFL-1.1-RFN | yes |
| Heavy Data | HeavyData | LicenseRef-VicFieger | yes |
| Hermit | Hurmit | OFL-1.1-RFN | yes |
| iA Writer | iMWriting | OFL-1.1-RFN | yes |
| IBM 3270 | 3270 | BSD-3-Clause | yes |
| IBM Plex Mono | BlexMono | OFL-1.1-RFN | yes |
| Inconsolata | Inconsolata | OFL-1.1-no-RFN | |
| Inconsolata LGC | InconsolataLGC | OFL-1.1-no-RFN | yes |
| InconsolataGo | InconsolataGo | OFL-1.1-no-RFN | |
| Intel One Mono | IntoneMono | OFL-1.1-RFN | yes |
| Iosevka | Iosevka | OFL-1.1-no-RFN | |
| Iosevka Term | IosevkaTerm | OFL-1.1-no-RFN | yes |
| Iosevka Term Slab | IosevkaTermSlab | OFL-1.1-no-RFN | yes |
| JetBrains Mono | JetBrainsMono | OFL-1.1-no-RFN | yes |
| Lekton | Lekton | OFL-1.1-no-RFN | |
| Liberation Mono | LiterationMono | OFL-1.1-RFN | yes |
| Lilex | Lilex | OFL-1.1-no-RFN | |
| MartianMono | MartianMono | OFL-1.1-no-RFN | |
| Meslo LG | MesloLG | Apache-2.0 | yes |
| Monaspace | Monaspice | OFL-1.1-RFN | yes |
| Monofur | Monofur | LicenseRef-Monofur | |
| Monoid | Monoid | MIT OR OFL-1.1-no-RFN | |
| Mononoki | Mononoki | OFL-1.1-RFN | |
| MPlus | M+ | OFL-1.1-no-RFN | yes |
| Noto | Noto | OFL-1.1-no-RFN | |
| OpenDyslexic | OpenDyslexic | Bitstream-Vera | |
| Overpass | Overpass | OFL-1.1-no-RFN or LGPL-2.1-only | |
| ProFont | ProFont | MIT | |
| ProggyCleanTT | ProggyClean | MIT | yes |
| Recursive Mono | RecMono | OFL-1.1-no-RFN | yes |
| Roboto Mono | RobotoMono | Apache-2.0 | yes |
| Share Tech Mono | ShureTechMono | OFL-1.1-RFN | yes |
| Source Code Pro | SauceCodePro | OFL-1.1-RFN | yes |
| Space Mono | SpaceMono | OFL-1.1-no-RFN | yes |
| Symbols Only | Symbols | MIT | yes |
| Terminus | Terminess | OFL-1.1-RFN | yes |
| Tinos | Tinos | Apache-2.0 | |
| Ubuntu | Ubuntu | LicenseRef-UbuntuFont | |
| Ubuntu Mono | UbuntuMono | LicenseRef-UbuntuFont | yes |
| Ubuntu Sans | UbuntuSans | LicenseRef-UbuntuFont | yes |
| Victor Mono | VictorMono | OFL-1.1-no-RFN | yes |
| Zed Mono | ZedMono | OFL-1.1-no-RFN | yes |
