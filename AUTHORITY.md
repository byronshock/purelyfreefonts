# AUTHORITY — truly_free_fonts

This file holds the project's settled decisions. Everything else in the repo follows it. Only decisions the owner has made go here. Research and drafts live elsewhere and are linked below.

## Purpose

Compare the fonts a user already has installed with a ranked list of the most popular free Latin fonts, and show the ones they don't have. The preferred delivery is a web page.

## Releases

1. **Intermediate release: the filterable list.** trulyfreefonts.com first publishes the ranked list of truly free fonts, with filters, but **without** comparing against the fonts a visitor owns. It replaces the current stub. *(The site is renamed Purely Free Fonts, at purelyfreefonts.com, on 2026-10-02: see **Name and domain (2026-10-02)** under Site.)* Its purpose is to test usability while the owned-font tools are built and tested. *(2026-09-25)*
2. **Full release: owned-font comparison.** Tools that take in a visitor's owned-font list and filter owned fonts out of the list. These ship only after the testing they need.

## Rules

1. **Licenses.** A font qualifies only if its license allows use in **all personal and commercial projects**. A truly free font has **no use restrictions**. Display-only licenses are excluded, and so are personal-use-only, demo/trial and non-commercial licenses. *(2026-09-25)*
2. **Script.** Latin fonts only.
3. **Redistribution.** A font qualifies only if its license also lets anyone pass the font files on. The site lists only redistributable fonts, so it needs no "Redistributable fonts only" option, and its wording about sharing the files is simpler. This excludes none of the September 2026 top 500. It replaces the rule of 2026-09-25, under which a license with no use restrictions that forbade redistribution still qualified and the site offered that option. Recorded in `data/reviews/site/2026-09-30.toml` (`redistributable_only`). *(2026-09-30)*
4. **Excluded licenses.**
   - **ITF Free Font License v2.0 (17 Aug 2026)**, used by 64 of Fontshare's 100 fonts. It has use restrictions: it forbids modification, including subsetting and format conversion, and it forbids offering the font to third parties through a website, app, SaaS, design tool or template editor. Fontshare fonts under the SIL OFL are unaffected. *(2026-09-25)*

## Ranking

Answers to the Milestone 1, Step 0 decisions in [docs/ranking-methodology.md](docs/ranking-methodology.md) (approved 2026-09-25 in [pull request #1](https://github.com/byronshock/trulyfreefonts/pull/1)).

- **D8: automatically installed fonts.** Fonts that Linux systems preinstall or pull in as dependencies of other packages stay in the rankings. The desktop rank is published as two views:
  - **most chosen:** a Linux source is left out for any font that a Linux system preinstalls or that other packages mostly pull in (threshold in the methodology); the font is still ranked on its other sources;
  - **most installed:** every install counts.

  Only **most chosen** feeds the overall rank. Affected fonts are tagged with the systems or packages that bring them in. *(2026-09-25)*

  *Amended on 2026-09-30 and 2026-10-01, landing with the desktop model:* a font that packages pull in keeps its Linux term in **most chosen**, less the installs its top dependent brings in; preinstalled fonts still abstain, and Overall retires (see **The scoring method** below).
- **D1: method.** Each source's order is mapped onto one shared scale. A source that doesn't carry a font is left out rather than counted as zero, and thin evidence is pulled toward the middle. Coverage-aware reciprocal-rank fusion runs monthly as a cross-check. *(2026-09-25; for the Project and Developers & apps ranks, replaced on 2026-10-01 by the measurement model, which keeps "left out rather than counted as zero"; see **The scoring method** below)*
- **D3: license classes.**
  - CC-BY fonts qualify, with an "attribution required" badge.
  - Copyleft licenses without a font exception (CC-BY-SA, plain GPL/LGPL, AGPL) are excluded.
  - Any ban on modification or embedding excludes a font, extending Rule 4.
  - Previews are shown only for redistributable fonts, served unchanged. Since Rule 3 of 2026-09-30, every listed font is redistributable.

  *(2026-09-25)*
- **D4: Latin.** Google's strict metadata test, plus dual-script families the owner approves from a reviewed short list. *(2026-09-25)*
- **D7: patched builds.** Nerd Font and CJK builds count in full toward the original family. *(2026-09-25; confirmed on 2026-09-30 and 2026-10-01, with Homebrew's plain and Nerd casks counted apart; see **The scoring method** below)*
- **D10: project rank.** It covers websites, code and apps: web 55%, code 30% (including ecosyste.ms dependent repositories), apps 15%. *(2026-09-25; the fixed shares retire on 2026-10-01, see **The scoring method** below)*
- **D12: overall rank.** Desktop (*most chosen*) 50% plus project 50%, usage only, with no designer picks. *(2026-09-25; **retired on 2026-09-30**: no fused score, see **The scoring method** below)*
- **D13: extra views.** Publish all four: Coding fonts, Developers & apps, By category and Rising (beta). *(2026-09-25)*
- **D14: evidence gate.** A top-100 place needs evidence from at least 2 independent source groups, plus a mild pull of thin evidence toward the middle (κ 0.2). *(2026-09-25; for the model ranks, κ gives way to the model's own pull and evidence means a term above its bulk, 2026-10-01, see **The scoring method** below)*
- **D15: snapshots.** Monthly data extracts live in a private GitHub data repository, reached with a deploy key or GitHub App. *(2026-09-25)*
- **D17: licenses.** The code is MIT. The catalog data is CC BY-SA 4.0, now final (ruling T5 below). *(2026-09-25)*
- **Terms rulings (Milestone 1 step 3).** What the catalog may use and publish from each source. Recorded in `data/reviews/terms/2026-09-25.toml`; the full table goes in `docs/sources.md`.
  - **T1: open sources.** For Homebrew, pkgstats, Arch and Debian package data, popcon, npm, Fontsource and jsDelivr, GitHub counts, the Web Almanac (Apache-2.0), ecosyste.ms (CC BY-SA 4.0) and Nerd Fonts `fonts.json` (MIT), raw counts may be published. The public repo may hold small, trimmed real fixtures with credit notices.
  - **T2: Google.** `/metadata/fonts` and `/metadata/stats` are used. Only ranks and z scores are published, never view counts. Public fixtures are synthetic; real data stays in the private store.
  - **T3: Chocolatey** is dropped from v1, because its terms forbid scripted access and republishing. The methodology notes the thinner Windows coverage.
  - **T4: Fonts Over Time** is used as D11's default says: at the phase-in weight of 0.10, with credit and a link back, ranks only (ranks and their rank-based z scores, never its raw values), and synthetic fixtures. Claude drafts the license request, and the owner posts it. *Amended on 2026-10-01* (see **The scoring method** below): Fonts Over Time enters the Project model now, at its fitted weight, as two terms; a model score that combines several sources is not a Fonts Over Time value, and its source entries show its rank-based z only.
  - **T5: data license.** CC BY-SA 4.0 is final.

  *(2026-09-25)*
- **Method clarifications (M1–M12).** Recorded in `data/reviews/method/2026-09-25.toml` and in the methodology.
  - **M1:** the outlier guard stays (gap 1.5 z, at least 3 terms, half weight). *Superseded on 2026-09-26:* the guard compares each term with the median of all the font's terms, and the worked example goes back to JetBrains Mono 2.48. *Retired for the model ranks on 2026-10-01*, which keep only a review flag (see **The scoring method** below).
  - **M2:** GitHub counts every release of a main-channel repo, as growth between snapshots, with no 24-month cap; Iosevka uses its latest 24 releases, through GraphQL.
  - **M3:** Homebrew Nerd casks get their own floor each run, the 10th percentile of Nerd casks' 365-day installs (about 2,150 a year), subtracted before `nerd_credit`. *Replaced on 2026-09-30* by Homebrew's count model, landing with the desktop model (see **The scoring method** below).
  - **M4:** the Arch Nerd Fonts group floor is the 10th-percentile share of members in the group at least 6 months; newer members are not floored. *Replaced on 2026-09-30* by Arch's count model, landing with the desktop model (see **The scoring method** below).
  - **M5:** GitHub counters and Homebrew are one independence group for a font whose Homebrew cask downloads that repo's release asset. *Amended on 2026-09-29:* only GitHub release counts merge; Nerd Fonts is a group of its own (gate R round 1, below).
  - **M6:** the Almanac's pages tab is the term; its services tab only flags parent merges.
  - **M7:** ecosyste.ms counts `@fontsource` and `@fontsource-variable` packages only.
  - **M8:** a Linux source abstains when the largest single dependent brings in at least 50% of installs; in `a | b` the first alternative is credited; 35–50% is flagged for review. *Replaced on 2026-09-30* for pulled-in fonts by an offset, landing with the desktop model (see **The scoring method** below).
  - **M9 (the owner's choice, not the recommended default):** the project group shares stay fixed at web 55%, code 30%, apps 15%; within a group, the sources share its weight pro rata to their effective weights (after phase-in, overlap scaling, stale drops and switched-off sources). *Retired on 2026-10-01* (see **The scoring method** below).
  - **M10:** Developers & apps uses the project weights, rescaled: npm 0.15, ecosyste.ms 0.10, Expo 0.10, and Flutter 0.05 when it is on. *Retired on 2026-10-01*: Developers & apps moves to the model with Project (see **The scoring method** below).
  - **M11:** the top-100 lists have hysteresis: a font enters at 90 or better and leaves after 2 runs worse than 110.
  - **M12:** foundry families are a hand list in `config/foundries.toml`, seeded once by Claude from the foundry sites and reviewed by the owner with the step 2 config. *Amended on 2026-09-29:* families are also added to it on request (see "Every qualifying font is listed" under Site).

  *(2026-09-25)*
- **Rulings after the first real run.** Given in chat on 2026-09-26 and recorded in `data/reviews/<gate>/2026-09-26.toml` (method, terms, config, latin, licenses, l3, aliases, unmatched, corrections, links, review, ci, site). Unless noted, the owner took Claude's recommendation.
  - **Outlier guard (the owner's own option):** each term is compared with the median of all the font's terms; one more than 1.5 z from it gets half weight. The worked example is JetBrains Mono 2.48.
  - **Method:** Developers & apps shows bands only while its sources share one independence group (until Flutter is on); Rising is tier C while in beta; the confidence draws keep re-weighting across all of a survey's sources (the owner's choice; for the model ranks, replaced on 2026-10-01 by a deterministic range, see **The scoring method** below); release assets created after the baseline count in full; §9's monotonicity wording as written there.
  - **Terms:** Google's monthly shares for Rising are kept in the private data repository, never in the public `state/`; Rising uses them.
  - **Linux sources:** a preinstalled system silences only the Linux sources that count it (Arch-family systems pkgstats, Debian and Ubuntu popcon, desktops both); popcon and pkgstats count a family by its most-installed package.
  - **Config (gate C):** `preinstalled.toml` and `foundries.toml` are approved; LibreOffice is listed as an application (`os = "app"`); EndeavourOS takes its fonts from eos-base-group; Blackout is three families.
  - **Latin (gate L):** at most 2 GF_Latin_Kernel code points missing, a Latin share of at least 30%, "extended" with at most 3 GF_Latin_Core code points missing; Google families served only with the menu subset take the glyph test; Single Day is out. Of the dual-script Google families (D4's short list), those with full Latin Extended are included unless they are script companions (a script version of a family already listed, such as Noto Sans Arabic or Hind Siliguri); companions and basic-Latin-only families stay out. Claude applied the rule family by family in `data/reviews/latin/2026-09-26.toml`: 178 included, 206 companions and 59 basic-Latin-only families left out. The owner was shown 327 included and 116 left out; on 2026-09-28 the owner confirmed that the 149 companions between the two counts (134 Noto script families, 9 Baloo 2 script versions, IBM Plex Sans Arabic, Mukta Mahee, Malar and Vaani, Playpen Sans Arabic and Deva) stay out.
  - **Licenses (gate LIC):** Bitstream Vera, Bitstream Charter, the Ubuntu Font Licence, the GUST Font License and IPA qualify (open-font group); WTFPL, LPPL 1.3c and public-domain dedications qualify (permissive); X11, BSL-1.0 and Artistic-2.0 qualify (permissive) and MPL-2.0 (open-font); GPL and LGPL with the font exception qualify; Lack is excluded until it publishes a license; Monofur, Vic Fieger, freeware grants, Arphic, Artistic-1.0 and Letters are researched; a family with no license is excluded, but Claude researches any that would reach the overall top 700 (the Project top 700 once Overall retires, 2026-10-01). OpenDyslexic, Overpass, Roboto Mono, Tinos, TeX Gyre Heros, Cascadia Code, Cascadia Mono and Hack qualify and are redistributable (their disagreements are outdated labels); the four Salaowu families are researched.
  - **License checks (gate L3):** Claude researches all 32 fonts that failed the automatic check and asks the owner only about genuine conflicts.
  - **Aliases and unmatched keys (gates A, U):** Iosevka Term and Term Slab fold into Iosevka; build variants (Maple Mono CN and NF, Cascadia Mono NF and the other NF and PL builds by the build rule, Inter Variable) fold into their family, and so do Monocraft's Nerd builds and the width names a parent's own files carry; Monaspace, Libertinus, Noto, iA Writer and M+ are bundles (D2); the 53-repository GitHub list and its 914 plain rows are accepted; distro package rows wait until their contents are researched; Material Icons and Symbols are icon fonts; a short `build/universe.md` is committed.
  - **Links (gate K):** the four overrides (Adobe Source, IBM Plex, Inter, JetBrains Mono) are approved. A family on the approved foundry list may use that list's link with no second source; `http://` homepages are upgraded to `https://` and checked monthly; Inter Tight keeps its Google page; open-foundry.com counts as an aggregator.
  - **Review:** a move is flagged when it is over 30 places and over 30%; the what-if table stays in the private review pack; catalog fonts with the default category are listed in the pack, and @fontsource/fira-mono is checked at gate R; ecosyste.ms packages are flagged stale after 90 days.
  - **Refresh (gate CI):** Claude sets up the pull-request permission and the data key with `gh` when step 19 is ready; the owner creates the watchdog's token after the first refresh pull request.
  - **Site:** the footer link reads "Leave a tip ($5 suggested)" on every page; the access log also drops Referer and User-Agent (Claude's recommendation in chat). *Replaced on 2026-10-05:* the footer link is removed, and a "Tip Jar" link ends the header nav (`tip_jar`; **First reviews of the test site**, below).

  *(2026-09-26)*
- **Rulings of 2026-09-28.** Given in chat in several batches, after the link research, the finish-gates-2 run, the finish-gates-3 run and the batch-4 run, and recorded in `data/reviews/<gate>/2026-09-28.toml` (links, aliases, unmatched, review, latin, licenses, l3, corrections, site). The owner took Claude's recommendation each time, except where noted.
  - **Links (gate K):** Metropolis links the archived mirror `github.com/dw5/Metropolis` (the repository, not its releases), labelled "archived mirror", and its details explain that the designer made his repository private in 2020; ProFont links `tobiasjung.name/profont/`; Terminus links the designer's page, `terminus-font.sourceforge.net`.
  - **Aliases (gate A):** Noto Mono is left out as superseded: it folds into Noto Sans Mono as its superseded name, so `fonts-noto-mono` and `font-noto-mono` count toward Noto Sans Mono (this replaces the 2026-09-26 rows that sent them to Noto Mono). Fredoka One is an old name of Fredoka. InconsolataGo is a build of Inconsolata, like Iosevka Term, not a separate fork.
  - **Held-back fonts:** when a catalog font is held back because it has no accepted download link yet, the published exact ranks close up among the published fonts, `review.md` names the held-back font and its places, and a gap in the exact ranks is a review flag, not a validation failure.
  - **Links, batch 2 (gate K):** DejaVu Sans Mono and DejaVu Serif link `https://dejavu.sourceforge.net/`; DejaVu Mono `https://github.com/dejavu-fonts/dejavu-fonts/releases`; Hack `https://sourcefoundry.org/hack/`; JuliaMono `https://juliamono.netlify.app/`; Meslo LG `https://github.com/andreberg/Meslo-Font/releases`; FiraGO `https://github.com/bBoxType/FiraGO/releases` (carrois.com's page answered 404). Font-specific pages: Courier Prime Code and Courier Prime Sans `https://quoteunquoteapps.com/courierprime/`, Gentium `https://software.sil.org/gentium/`, and each TeX Gyre family its own GUST page, `https://www.gust.org.pl/projects/e-foundry/tex-gyre/<name>`.
  - **Dual-script Latin (gate L):** the 149 script companions the owner first saw counted as included stay out, as the L2 rule says: 178 dual-script families are in, and 206 companions and 59 basic-Latin-only families are out.
  - **No-license families (gate LIC):** of the 25 families with no license found that would reach the overall top 700, Claude's research found a qualifying license for 24, each recorded as qualifying and redistributable with its license and source; CamingoCode (CC BY-ND 3.0) is excluded. Monaspace's five families qualify and are redistributable under OFL-1.1 (Arch's OFL-1.0 label is outdated).
  - **Build folds (gate A):** seven of those 25 are builds and fold into their family: the three Inconsolata Powerline builds into Inconsolata, Delugia into Cascadia Code (its two Mono casks, built from Cascadia Mono, into Cascadia Mono), Meslo LG DZ into Meslo LG, Recursive Code into Recursive, and Monaspace Frozen into the Monaspace bundle.
  - **Edu families (gate A):** every live Edu family stays separate; the six queued Fontsource merges are rejected and the six Edu old-name hand rows of 2026-09-26 are withdrawn.
  - **License checks (gate L3):** Cascadia Code, Cascadia Mono and GFS Didot are kept (OFL-1.1 governs; the name ID 13 wording is Windows boilerplate or the OFL draft's copyleft clause); Code New Roman is excluded (derived from proprietary fonts, no OFL text or source). **IBM 3270 is kept (the owner's choice: Claude had recommended excluding it under D3):** the Debian logo glyph under CC-BY-SA-3.0 or LGPL-3.0-or-later is one private-use glyph and does not taint the font.
  - **New Linux cases (gate X):** all 17 are left out of most chosen, as recommended (three groups).
  - **Access log:** dropping Cookie and Cloudflare's location headers finer than the country is the owner's ruling (see Infrastructure). The 2026-09-26 trim of Referer and User-Agent was Claude's recommendation in chat, and its record now says so.
  - **Alias and unmatched queues (gates A, U; the owner's choice: Claude had recommended about 20 grouped questions):** Claude settles the mechanical rows itself and records them as Claude's rulings, citing this delegation: exact name or package-name matches to one family, clear build, rename and package rows the approved gate A rules already decide, keys of excluded or ineligible families (icon, CJK, proprietary) as ineligible rows, and rows the owner's earlier answers already decide. Anything with doubt goes to the owner, grouped.
  - **Links, batch 4 (gate K):** Droid Sans Mono links Android's source folder, `https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/data/fonts/`, and stays a family of its own (Apache-2.0); DejaVu Sans Mono and DejaVu Serif link `https://dejavu-fonts.org/` instead of the earlier pick, whose redirect through `http://` fails the link check; IBM 3270 links `https://github.com/rbanffy/3270font/releases`, Go Mono `https://go.dev/blog/go-fonts` and Bitstream Vera Sans Mono `https://download.gnome.org/sources/ttf-bitstream-vera/1.10/`.
  - **Duplicates (gate A):** Geist Sans folds into Geist, Fontsource's DejaVu Mono into DejaVu Sans Mono, and Terminus TTF into Terminus as a build; their separate links lapse.
  - **Packages (gates A, U):** a package that ships several families gets bundle rows from its real file list; TeX Live's font packages and `ttf-google-fonts-git` stay unmatched (gate U's answer "leave it unmatched", which counts as resolved for step 9). A metapackage or transitional package with no font files maps to what it pulls in. The ineligible reasons gain not_font (no typeface: font tools, libraries, encoding files), non_latin (a font for another script) and unlisted (a Latin font no catalog source lists), and the 103 keys Claude listed get them.
  - **Names (gates A, U; the owner's choice for Creep: Claude had recommended rejecting its three asset rows):** Redaction 70 and Gentium Book Basic stay families of their own; the retired Google names map to their successors (Andika New Basic to Andika, Kdam Thmor to Kdam Thmor Pro, Andada to Andada Pro, Spartan to League Spartan, Be Vietnam to Be Vietnam Pro); Wix's web names (`madefor-text`, `madefor-display`, `wixmadefortextapp`, `wixmadefordisplayapp`) map to Wix Madefor Text and Display, and Google Sans Text to Google Sans; Maple Mono's `cn-base-static.zip` is ineligible (CJK). Claude researches whether Creep belongs in the universe before its assets are decided.
  - **Leftover packages (gates A, U):** Claude researches the contents of the AUR and removed packages that have no readable file list and settles the clear ones under the delegation above; the doubtful ones go to the owner.
  - **Licenses (gate LIC):** the four Salaowu families, Monofur (its author's 2018 grant) and Letters are OFL-1.1; the Artistic License 1.0 qualifies (permissive), replacing the 2026-09-26 ruling that sent it to research; Vic Fieger's freeware terms (Heavy Data) qualify, in the freeware group, and are redistributable; the 11 new disagreements from Debian's and Arch's license data (JetBrains Mono, Open Sans, Goudy Bookletter 1911, Prociono, Tuffy and others) qualify and are redistributable under their current licenses.
  - **New Linux cases (gate X):** all 40 new cases are ruled as recommended (eight groups): 36 are left out of most chosen, and the 4 whose largest dependent brings in 35–50% of installs keep counting.
  - **Batch 5 (after the batch-4 run; the owner took Claude's recommendation each time):**
    - **Creep** stays out of the universe: `romeovs/creep` leaves the GitHub repository list and its three asset rows are rejected (no catalog source lists it, and it has been unmaintained since 2014).
    - **Umbrella casks (gate A):** Homebrew's `font-liberation`, `font-urw-base35`, `font-stix`, `font-adwaita` and `font-bitstream-vera` get bundle rows to their member families, as `font-dejavu` has.
    - **Links (gate K):** Latin Modern links `https://www.gust.org.pl/projects/e-foundry/latin-modern`.
    - **Licenses (gate LIC):** Conakry qualifies (OFL-1.1, redistributable); Fanwood, Fanwood Text, Monoid, Monoisome and Overpass Mono qualify and are redistributable under their current licenses.
    - **New Linux cases (gate X):** the 46 new cases the bundle rows raised, asked group by group, are all left out of most chosen.
    - **Leftover packages (gate A):** of the 21 Claude could not settle, `ttf-cica` and `ttf-hackgen` are builds of Hack (D7); `ttf-lastresort` is ineligible (icon), `ttf-tahoma` is Wine's Tahoma, Debian's third-party `fonts-consolas` is ineligible (proprietary), `fonts-harmonyos-sans-cn` is ineligible (CJK) and `fonts-harmonyos-sans` maps to HarmonyOS Sans; twelve third-party Debian keys map to their family by name; `fonts-fira-mono` and `fonts-fira-sans`, which exist in no archive, are dropped.
    - **Chunk (gate A, 2026-09-29):** Fontsource's Chunk Five folds into Chunk from The League of Moveable Type's list, because both are the one typeface from `theleagueof/chunk`; AUR `ttf-chunk` counts toward Chunk.

  *(2026-09-28)*
- **Rulings of 2026-09-29.** Given in chat in one batch, after the real-store rebuild held 20 catalog members back without an accepted download link, and recorded in `data/reviews/links/2026-09-29.toml` and `config/link-overrides.toml`. The owner took Claude's recommendation each time.
  - **Links (gate K):** Adwaita Sans, Charter, SN Pro Font Family, Sudo, Ioskeley Mono, Microsoft Selawik, Miracode, Monocraft, Myna, Pretendard Std, psudoFont Liga Mono, Twilio Sans Mono and Zed Sans take their printed candidate. Three take a corrected link, as an approved override, instead of theirs: Anka/Coder links its designer's repository, `https://github.com/loafer-mka/anka-coder-fonts` (the printed Google Code page is dead); Awami Nastaliq SIL's page, `https://software.sil.org/awami/`; and Cozette `https://github.com/the-moonwitch/Cozette/releases`, where its repository moved. Sophia Nubian and Tagmukay, which had no candidate, link SIL's pages, `https://software.sil.org/sophianubian/` and `https://software.sil.org/tagmukay/`. TeX Gyre Heros Cn, held back after the next rebuild, links GUST's TeX Gyre Heros page, `https://www.gust.org.pl/projects/e-foundry/tex-gyre/heros`, which offers the condensed faces, as each sister TeX Gyre family links its own page, rather than its printed pick, the page for the whole collection.
  - **Archived links (gate K):** web.archive.org stays an aggregator for every automatic pick (sources, two sources agreeing, the foundry list), but an override the owner approves may link a timestamped Wayback Machine capture, `https://web.archive.org/web/<14-digit timestamp>/<original URL>`, of the designer's own page when that page is gone. Such an override is marked `archived` and labelled as archived, the way Metropolis's link says "archived mirror", with a note that says why. This replaces the wave-1 default that banned web.archive.org outright. Heavy Data links Vic Fieger's site as captured in December 2022, `https://web.archive.org/web/20221209161833/http://www.vicfieger.com/`. Monofur links the last capture of its designer's page on eurofurence.net, from February 2020, `https://web.archive.org/web/20200206093756/http://eurofurence.net/monofur.html`, instead of its printed candidate. The September 2022 capture proposed first turned out to be a registrar's parking page, so the owner was asked again (`K-monofur-page`). Heavy Data keeps its capture, a choice the owner made knowing that Vic Fieger's pages never listed the font (his downloads went through dafont.com); its note doesn't claim that the font is offered there.

  *(2026-09-29)*
- **Rulings of 2026-09-29, gate R round 1.** Given in chat in four batches, after the real-store rebuild, and recorded with their reasons in `data/reviews/review/2026-09-29.toml`. The owner took Claude's recommendation each time.
  - **Independence groups (R1):** Nerd Fonts release downloads are an independence group of their own, `nerd`, and M5 now merges only GitHub release counts into Homebrew's group. Homebrew's Nerd casks are a small share of Nerd's downloads (Hack: about 62,000 Homebrew installs a year against 2.3 million Nerd downloads), and the merge held Hack out of the coding, most-chosen and overall top 100s.
  - **Categories (categories_22):** the 22 catalog fonts left on the sans-serif default get the owner's category, set in `config/category-overrides.toml`, which wins over every other source. Serif: Junicode, Latin Modern, TeX Gyre Termes, Pagella, Schola and Bonum, Charter, Gentium and Fanwood. Monospace: Cozette, Miracode and Compagnon. Handwriting: TeX Gyre Chorus. The other nine keep sans-serif, now by ruling: TeX Gyre Heros, Heros Cn and Adventor, SN Pro Font Family, Pretendard Std, Sophia Nubian, Tagmukay, Heavy Data and Awami Nastaliq.
  - **Overall mix (D12):** unchanged, desktop (*most chosen*) 50% and project 50%.
  - **Fira Mono:** no hand correction; it stays on the gate R watch list.
  - **Lists:** the project, desktop (*most chosen*, *most installed*) and coding lists look right apart from the Hack case; Developers & apps stays bands only; the explanations of the moves against the old Top 100 are accepted.
  - **Round 2 (R2): approved.** After the two changes were rebuilt on the real store, Hack is #5 in coding and *most chosen* and #53 overall, and Geist Mono, Liberation Mono, Commit Mono and Zed Mono enter *most chosen*. The owner judged the changes right and approved the top lists, which completes Milestone 1 step 16.

  *(2026-09-29)*
- **Final method (Milestone 1 step 17).** The method the owner approved at gate R round 2 is final. The entries above hold each decision; this one says where the final state lives.
  - **Method:** the code implements method version 2026-09-29 (`METHOD_VERSION` in `src/tff_catalog/__init__.py`, which each catalog records as `run.method_version`), with every ruling above up to gate R round 2. The owner's ruling of 2026-09-29 moved it on from 2026-09-25 to cover that day's changes: M5 as amended (Nerd Fonts its own independence group), the owner's category overrides, archived-link overrides, and the Nerd Font build link (`links.nerd`, TASK-2). Its public text is [docs/ranking-methodology.md](docs/ranking-methodology.md), published on 2026-09-29. The rulings of 2026-09-30 and 2026-10-01 on the scoring method (below) are not in it yet: they take a new method version, which the port brings.
  - **Weights:** every weight and parameter is in `config/ranking.toml`, as the approved run used it (`run.ranking_toml_sha256` `c22345a4…` in `build/catalog.json`). The headline values:
    - overall: desktop (*most chosen*) 50% and project 50% (D12);
    - desktop: Homebrew 1.0, Arch 0.75, GitHub 0.5, Nerd Fonts 0.3, Debian 0.25 (D9);
    - project: web 55%, code 30% and apps 15%, fixed, each shared among its sources (D10, M9);
    - Coding: Nerd Fonts 1.0, Homebrew 1.0, Arch 0.75, GitHub 0.5, Fontsource npm 0.5 (D13);
    - Developers & apps: Fontsource npm 0.15, ecosyste.ms 0.10, Expo 0.10, and Flutter 0.05 once it is on (M10).
  - **Licenses:** Rules 1, 3 and 4, and D3. `config/licenses.toml` lists the licenses that qualify, by filter group, and those excluded; a license it doesn't list is excluded. The owner's rulings on particular licenses and fonts are in `data/reviews/licenses/` (gate LIC) and `data/reviews/l3/` (gate L3).
  - **Latin:** D4 with the owner's allowlist rulings (gate L, 2026-09-26 and 2026-09-28): Google's strict metadata test, plus the dual-script families of `data/reviews/latin/`; every other font takes the glyph test, with the thresholds in `config/ranking.toml [latin]`.

  *(2026-09-29)*
- **Rulings of 2026-09-29, data fixes.** Given in chat in two batches after the data-fix runs, and recorded in `data/reviews/<gate>/2026-09-29.toml`. The owner took Claude's recommendation each time, except where noted.
  - **Licenses (gate LIC):** reading the last 8 license strings raised 11 disagreements, which qualify and are redistributable:
    - the eight TeX Gyre families, under the GUST Font License, as for TeX Gyre Heros;
    - IBM 3270, under BSD-3-Clause;
    - B612 and B612 Mono, under OFL-1.1.

    AU Passata's terms are excluded (no public grant; "All rights reserved"). fontopo's terms qualify, but its fonts are not redistributable.
  - **FreeFont (gate LIC):** qualifies and is redistributable under GPL-3.0-or-later with the font exception; its release's README is its L3 text. It stays monospace, as FreeMono's file makes it (coding #31), which `config/category-overrides.toml` pins. That was the owner's choice; Claude had recommended FreeSerif, as a serif.
  - **ET Book and Open Sans Hebrew (gates LIC, L):** ET Book qualifies and is redistributable under MIT, with the category serif. Open Sans Hebrew and Open Sans Hebrew Condensed stay out, as basic-Latin Hebrew versions of Open Sans.
  - **Families with no readable file (gates L, LIC, L3, A):**
    - Linux Libertine and Linux Biolinum take CTAN's byte-identical copies of the 5.3.0 files, pinned by sha256 (`config/font-files.toml`), under OFL-1.1. Their name tables name the designer's dual grant (GPL with the font exception, or OFL), so they pass L3 by ruling: OFL-1.1 governs, as with Cascadia.
    - Computer Modern, New Computer Modern, Spleen and Scientifica wait until the pipeline reads tar archives, after the Milestone 1 pull request.
    - So do the builds and umbrella casks among these families, and the 21 Iosevka variants. That was the owner's choice; Claude had recommended folding them now.
  - **Links (gate K):**
    - Go: `https://go.dev/blog/go-fonts`, as for Go Mono;
    - Liberation Serif: `https://github.com/liberationfonts/liberation-fonts/releases`, as for Liberation Mono and Sans;
    - ET Book: `https://edwardtufte.github.io/et-book/`;
    - Linux Libertine and Linux Biolinum: `https://libertine-fonts.org/`.
  - **New Linux cases (gate X):** all 8 are left out of *most chosen*:
    - FreeFont, pulled in by vlc-plugin-skins2;
    - Go, by texlive-fonts-extra;
    - Linux Libertine and Biolinum, by libreoffice;
    - Liberation Sans and Serif, preinstalled on CachyOS and Ubuntu.

  *(2026-09-29)*
- **Rulings of 2026-09-30.** Given in chat and recorded in `data/reviews/aliases/2026-09-30.toml` and `data/reviews/corrections/2026-09-30.toml`. The owner took Claude's recommendation, except where noted.
  - **SN Pro (gate A, `sn_pro_fold`):** Homebrew's `font-sn-pro`, whose cask title is "SN Pro Font Family", counts toward SN Pro as a package row, as `font-geist` counts toward Geist (`duplicates`, 2026-09-28). The cask installs Supernotes' own SN Pro release, whose fonts are named "SN Pro"; only the cask's title made a second family. SN Pro Font Family leaves the catalog, and its own rulings lapse: its gate K link to Supernotes' page (2026-09-29), its category override (gate R round 1) and its L3 research entry.
  - **When (`sn_pro_fold_timing`, the owner's choice):** the ruling, the alias row and the config go to `main` now, but `build/` keeps the published 2026-09-26 catalog, SN Pro Font Family included, until the first live refresh builds the fold in. Claude had recommended rebuilding now, with a replay of the only merged run's own date treated as the bootstrap. The fold moves scores, and since #31 put the 2026-09-26 scores in `state/`, a replay sets each moved `previous_score` to the published score, which `validate --committed` refuses under `score_previous_bootstrap`. So until the first live refresh, any rebuild of `build/` from `main` meets that check.
  - **SIL renames and Selawik (gate A, `sil_selawik_folds`, given in chat with the lead session):** four more duplicates fold, as SN Pro did, and the catalog takes them at the first live refresh. SIL renamed Charis SIL to Charis, Gentium Plus to Gentium and Gentium Book Plus to Gentium Book; Homebrew's, Fontist's and Arch's packages of the new names, and Debian's 2003 Gentium, count toward Charis SIL, Gentium Plus and Gentium Book Plus. Fontist's Selawik counts toward Microsoft Selawik, which keeps its id, link and license ruling and is shown as Selawik, the font's own name. The family Gentium leaves the catalog, with its gate K link, category override and L3 research entry. In a replay of 2026-09-26 with the folds: Gentium Plus moves from 300 to 285 and enters the most-installed top 100 at #89; Gentium Book Plus joins the catalog at 337; Charis SIL drops from 265 to 329 (next item); there are still 499 fonts.
  - **Their names (`sil_rename_names`):** Charis SIL, Gentium Plus and Gentium Book Plus keep their ids and Google's names, since Google Fonts still lists them so (methodology §2: Google's family is the unit, and a rename keeps the id). SIL's new names are also-known-as names. The ruling relayed by the lead had said "into the current upstream name"; Claude flagged that the rules pick Google's names, and the owner took them.
  - **Charis SIL and praat (gate X, `dep-arch-charis-sil`, ruled ahead of the refresh):** Arch's praat depends on `ttf-charis`, SIL's 7.000 package, and brings in about two thirds of its installs, so Arch is left out of *most chosen* for Charis SIL, as for Doulos SIL (2026-09-28).
  - **More duplicates (gate A, `duplicates_batch_3`, given in chat with the lead session, after verified evidence):** seven retired or split names fold into their live successor, and the catalog takes them at the first live refresh (`sn_pro_fold_timing`):
    - Scheherazade into Scheherazade New, with its Debian, Fontsource, npm and Expo packages;
    - OFL Sorts Mill Goudy TT into Sorts Mill Goudy;
    - Rubik One into Rubik;
    - Big Shoulders Display and Text into Big Shoulders, as optical-size builds, so the catalog entry moves from `big-shoulders-display` to `big-shoulders`;
    - Yaldevi Colombo into Yaldevi;
    - Sansita One into Sansita;
    - Finlandica into Finlandica Headline, as its old name. This revises the 2026-09-26 answer that Finlandica is related to its split families; Finlandica Text stays related.

    In a replay of 2026-09-26 at main with the rows, there are still 499 fonts. Big Shoulders replaces Big Shoulders Display at overall 250 (it was 360). Scheherazade New moves from 249 to 246 and gains a LibreOffice tag. Finlandica Headline moves from 471 to 488. The other top-100 changes are moves of a place or two.
  - **Checked folds (`duplicates_batch_3_checked`):** of the four the owner ruled "fold after a replay check", Big Shoulders Inline Display and Text fold into Big Shoulders Inline, Big Shoulders Stencil Display and Text into Big Shoulders Stencil, and Nosifer Caps into Nosifer. Creepster Caps stays a family of its own (the owner's answer to Claude after the check): its google/fonts folder is Apache-2.0 while Creepster is OFL-1.1, and folded, Creepster's license became "Apache-2.0 AND OFL-1.1".
  - **Small-caps companions (`sc_companions_keep_separate`, a class ruling):** small-caps companions that Google no longer lists (the six Big Shoulders SC families, Fragment Mono SC, Alumni Sans Collegiate One SC and the like) stay families of their own and aren't raised as duplicates again.
  - **Scheherazade New and LibreOffice (gate X, `dep-debian-scheherazade-new`, ruled ahead of the refresh):** Debian is left out of *most chosen* for Scheherazade New, as it was for the old Scheherazade (2026-09-28): libreoffice-l10n-ar brings in 60% of the package's installs.

  *(2026-09-30)*

- **Rulings of 2026-10-01.** Recorded in `data/reviews/aliases/2026-10-01.toml`.
  - **Expo packages with no successor (gate A, `expo_no_successor_unlisted`, Claude's recommendation):** the Expo packages of Amatica SC, Droid Sans and Droid Serif get ineligible rows ("unlisted"). No catalog source lists those fonts, and none has a successor of the same design. In the same file, under the delegation of 2026-09-28, Claude:
    - sent the Expo packages of 20 other retired Google names to the successors the owner's rulings chose;
    - made the Material icon and emoji packages, Expo's directory package and the Baloo v1 script versions ineligible;
    - gave the DSEG packages bundle rows from their file lists;
    - left the Expo packages of seven delisted families unmapped, since Expo's frame is Google's live list.

  *(2026-10-01)*

- **The scoring method, from data sources to views (2026-09-30 and 2026-10-01).** On 2026-09-30 the owner set the method's ad-hoc parts aside: "We are going to ditch the ad-hoc methodology in favor of something grounded in real math." A separate session reworked it source by source, starting from Homebrew (`p1_pass_order`), and the owner answered its plan's questions on 2026-10-01. Recorded in `data/reviews/method/2026-09-30.toml` and `data/reviews/method/2026-10-01.toml`. The plan itself stays private, because it quotes estimates made from Google Fonts and Fonts Over Time data (T2, T4). The owner took Claude's recommendation each time, except where noted.

  **None of this is in the code yet.** The catalog runs method version 2026-09-29 until the port lands these rulings. On 2026-09-30 the owner froze the methodology-tied work "until the methodology is sound"; these records went in during the freeze because they change no code.
  - **No fused score (`overall_retired`, 2026-09-30).** The Overall rank (D12) retires, and the front page ranks by the Project rank, "Used in projects" (M2-D1, amended). The owner's reason: popularity in projects "as a single factor explains much of the variance in the data" and is "one of the most interesting views". The other ranks stay selectable. In `catalog-site.json` v1, Overall stays in `views` with `available: false`, and each font's `ranks.overall` repeats its Project entry as a stand-in (`q12_overall_stand_in`), so no v2 is needed. The catalog becomes the Project top 500 plus each rank's top 100, and the license research reaches the Project top 700 (`q13_catalog_membership`).
  - **The measurement model (`q1_model_views`).** It covers the Project and Developers & apps ranks (`q15_views_meanwhile`):
    - Each font has one latent popularity.
    - Each source is a count term with its own noise, its own window and exposure, and a bulk baseline that every package collects whatever its popularity.
    - The model is fitted by maximum likelihood, and the score is the font's posterior mean.

    For those ranks it replaces:
    - the Homebrew ruler and the equating;
    - the floors and the censored terms;
    - the hand weights and group shares (D10, M9 and M10, `q5_group_shares_retired`);
    - κ;
    - the outlier guard's half weight (M1). A review flag stays, for a term that conflicts with the font's other terms (`q2_guard_retired`).
    - the confidence draws. A likely range becomes the font's rank at its own posterior 5% and 95% quantiles (`q19_ranges_tiers_stand_in`).

    Desktop, Coding and Rising stay on the engine until desktop's model is designed, with GitHub and Nerd Fonts off. On the 2026-09-26 data, the model's Project order agrees with today's at Spearman 0.977 and keeps 91 of the top 100.
  - **Desktop sources (2026-09-30).** Rulings that need no model land in the engine with the port: Homebrew's window, no source scaling up a young font, Arch's pooled months, the Debian frame, the dropped vote, and the GitHub and Nerd Fonts deferral. The rest waits for the desktop model, and until then the engine keeps its desktop floors.
    - **Homebrew.**
      - A cask's installs count as they are, and a young cask is never scaled up to a year (`h3_young_casks_not_scaled`). The owner, unprompted: "New casks will need the same evidence as existing casks, installs in the last year."
      - Plain and Nerd casks are counted apart, and D7's full credit stays (`h1_homebrew_nerd_split`).
      - The floor gives way to a count model: two terms, each with an estimated bulk, and no floor, censoring or subtraction (`h2_homebrew_count_model`, replacing M3). The owner, after Claude's floor sampler: "Obviously the way we have been doing the Homebrew floor is wrong." A young cask's expected bulk is scaled to its age; its installs never are (`h4_young_cask_bulk_by_age`).
    - **Every source.** No source turns a young font's count into a rate over its days available (`b8a_no_scaling_any_source`). The 60-day grace and the New badge stay (`b8b_grace_kept`).
    - **Arch.**
      - Arch gets the same count model, with months below pkgstats' 16-system line read as 0 to 15 (`a1_arch_count_model`, replacing M4).
      - System-months are pooled (`a2_arch_pool_months`).
      - A young package's bulk is scaled to its age, for packages in the `nerd-fonts` group only (`a3_arch_nerd_bulk_by_age`, `q8_a3_group_only`).
    - **GitHub and Nerd Fonts.** Neither has a term in any rank until 6 monthly snapshots exist, then they are revisited (`g1_github_deferred`, `g2_nerd_deferred`, `g3_github_revisit`). The owner: "We will defer acceding the github data until it has been observed for several months." Rulings M5 and R1 are moot meanwhile.
    - **Debian.**
      - A font is covered only through a package in the release's main archive. A third-party, removed or sid-only package is not covered (`debian_frame`), so Adwaita loses its Debian term until it reaches a release (`de1_frame_effect`).
      - One count term, with no floor (`de2_debian_count_model`).
      - The vote series is dropped (`de3_drop_vote`).
      - No age adjustment (`de6_debian_no_age`).
    - **Bundles.** D2's half credit stays in every source. That was the owner's choice ("yes, half"); Claude had proposed full credit (`de4_bundle_credit_half`).
    - **Pulled-in fonts.** In place of M8's abstention, a Linux term subtracts the installs its top dependent brings in: max(0, y − d) (`de5_dependency_offset`, `q28_offset_subtracted`). A font is tagged "mostly pulled in" when that takes half its installs or more (`q10_pulled_in_tag`).
    - **Later.** Desktop gets two correlated factors, macOS-and-developer and Linux, in one view. The owner sets their weight when desktop moves, in place of D9's weights (`q21_desktop_two_factors`). A Nerd factor waits for Nerd Fonts' own data (`q22_nerd_factor_later`).
  - **Project sources.**
    - Claude chose each source's measure, under the owner's delegation: "decide what is the most compatible statistical measure for each" (`pd1_project_measures`).
    - Fonts Over Time enters now, at its fitted weight, as two terms, startups and other homepages. This retires D11's cap and phase-in weight (`q4_fot_two_terms`, `q6_fot_now`; T4 amended).
    - Google views, npm and Expo get an age-scaled bulk (`q7_age_scaled_bulk_web`).
    - An Almanac count that merges a parent's counts is an upper bound only (`q24_almanac_merged_parent`).
    - A jsDelivr family with no hits is not covered until its statistics show hits (`q29_jsdelivr_zero`).
    - The port checks the frames, and Flutter stays off until it has its own pass (`q26_project_frames`).
    - Google's own products' traffic is left to the noise (`q23_google_own_traffic`).
    - The small overlap between the web sources is disclosed and accepted (`q25_residual_dependence`).
  - **Rules.**
    - D14's two-group rule stays: a group gives evidence when one of its terms sits above what bulk alone predicts (`q14_two_groups`). Developers & apps can't meet it, so its single note stays (`held_marker_dev_apps`).
    - A source that doesn't carry a font says nothing about it (`q3_coverage_mar`).
    - D7's full credit stays for CJK builds (`q27_cjk_credit`).
    - The model's parameters are held for a year, and re-estimated early when a drift check fires or a source changes (`q9_theta_cadence`).
  - **Published fields (v1 unchanged).**
    - `weight_used` is each term's share of the font's information, and null for Google and Fonts Over Time (`q16_weight_used`).
    - Every term's `state` reads "observed", interval terms included. That was the owner's choice; Claude had recommended "censored" for an interval (`q18a_state_observed`).
    - A term's `z` is the popularity that term alone implies; Fonts Over Time keeps its rank-based z (`q18b_z`, `q17_t4_wording`).
    - A rank's first run under a new method bootstraps `previous_score`, so no triangle shows that month (`q20_previous_score_at_switch`).
  - **The shown score.** It is linear between 0 and 3 on the latent scale, so 1 is the average tracked font and 100 is three standard deviations above it: max(1, ⌈100 · clip(η̂ / 3, 0, 1)⌉). It replaces 100·Φ(z) (`score_curve`).
    - The linear scale was the owner's choice, where Claude had recommended keeping 100·Φ (`q11_display_linear`). The ends were Claude's recommendation (`q30_display_ends`).
    - Over the Project top 500 on 2026-09-26: no font at 1, 5 at 100, a median of 33, and a median of 70 in the top 100.
    - It is needed by the first live refresh, and the ends are site constants, held with the model's parameters.

  *(2026-09-30, 2026-10-01)*

## Site (Milestone 2)

Answers to the Milestone 2, Step 0 decisions in `docs/milestone-2.md` ([pull request #2](https://github.com/byronshock/trulyfreefonts/pull/2)).

- **M2-D1: default rank.** The list opens on the **overall** rank. *(2026-09-25; amended 2026-09-30: it opens on **Used in projects**, the Project rank, since Overall retired (D12). Recorded in `data/reviews/site/2026-09-30.toml` (`default_rank_project`); it lands with the site work the freeze holds)*
- **M2-D2: numbers under filters.** Each filtered list renumbers from 1. Fonts past the exact top 100 show their band instead of a number. *(2026-09-25; replaced in Milestone 2 by **Scores instead of numbers**, below, 2026-09-29)*
- **M2-D3: page technology.** Plain HTML, CSS and one script, with no framework, built by a Python command in the same project as the catalog. *(2026-09-25)*
- **M2-D4: filter layout.** Every filter sits in a sidebar on wide screens. On phones, everything but search and rank goes behind one "Filters" button. *(2026-09-25)* *Extended on 2026-09-30:* on wide screens the sidebar stays in view while the list scrolls (`filters_sticky`; **First reviews of the test site**, below).
- **M2-D5: previews.** SVG specimens are drawn at each refresh. A "Type your own text" box loads the unchanged font file only when clicked. Only redistributable fonts get previews. *(2026-09-25)*
- **M2-D6: deploy.** GitHub Actions deploys each push to `main` as a restricted `deploy` user, and a laptop script is the fallback. *(2026-09-25)*
- **M2-D7: test site.** `staging.trulyfreefonts.com` is used for usability round 1 and reused by Milestone 3. *(2026-09-25; amended 2026-10-02: the test site moves to `staging.purelyfreefonts.com`, and the old host 301s to it, see **Name and domain (2026-10-02)** under Site)*
- **M2-D8: search engines during the soft launch.** Indexing is allowed; only announcements wait for Milestone 4. *(2026-09-25, the [later OK] default kept)*
- **M2-D9: server logs.** See Infrastructure, visitor privacy on the server. *(2026-09-25)*
- **M2-D10: feedback channels.** GitHub issue forms, plus an email link to the site's `admin@` address for people without GitHub. *(2026-09-25, the [later OK] default kept)*
- **M2-D11: caching pages at Cloudflare.** HTML is not edge-cached, so deploys need no purge; hashed `/assets/` files are cached as immutable. *(2026-09-25, the [later OK] default kept)*
- **M2-D12: blog.** *(2026-09-25; checklist in [Milestone 2 step 7b](docs/milestone-2.md#step-7b-blog))*
  - **System.** Posts are Markdown files in the repository, built by `tff-site` with the rest of the site. There is no separate blog engine.
  - **When.** It ships with the list release (Milestone 2).
  - **Address.** Posts live at `/blog/`, with an Atom feed at `/blog/feed.xml`.
  - **License.** Post text and images are under CC BY-SA 4.0, the same as the catalog data.
- **Spacing filter.** "Spacing: Any / Proportional / Monospaced" replaces D13's "Text only" filter and the "monospace only" checkbox. *Proportional* is the old "Text only": it hides monospace and coding fonts. The filter applies to every rank. Recorded in `data/reviews/site/2026-09-25.toml`. *(2026-09-25; replaced by **Filters (2026-09-30)**, below)*
- **Project rank label.** On the site, the Project rank (D10) is labelled **"Used in projects"**. *(2026-09-25)* *Changed on 2026-09-30* to **"Projects: most used"** (`project_rank_label_most_used`; **First reviews of the test site**, below). It lands with the Project default.
- **Site data fields (M2 step 2).** The `catalog-site.json` fields in `schemas/catalog-site.schema.json` are approved as drafted. Designer lists are left out, because D12 is usage only. *(2026-09-25)*
  - **Fields added since, approved for the v1 freeze** (Milestone 1 step 20): the `nerd` group, the `app` system type, a link's `note`, and each rank entry's `score` and `previous_score`. After the freeze a change to any of them needs v2. Recorded in `data/reviews/site/2026-09-30.toml` (`site_fields_v1`). *(2026-09-30)*
- **License filter.** Four groups: open font licenses (OFL, UFL, Bitstream Vera); permissive (Apache, MIT, BSD, CC0); attribution required (CC BY); and other free-use grants, which allow any use but may forbid redistributing the files (Rule 3). *(2026-09-25; replaced by **Filters (2026-09-30)**, below. The four groups remain the license classes of the data and the pipeline.)*
- **Nerd Fonts build link (TASK-2 ruling).** A catalog family with a Nerd Fonts build gets a third, nullable link, `links.nerd`, in both catalog schemas, landing before the M1 step 20 v1 freeze. One shared target: `https://github.com/ryanoasis/nerd-fonts/releases` — the repository's releases page, never a release asset or `/releases/latest`. Text: **"Nerd Fonts build"**, naming its destination per the M2 step 4 convention. It shows on the list row and in the details panel, in every view. The spike found no license obstacle: every base license and all 14 patched icon sets pass Rules 1–4 (Font Logos' "unlicensed" is The Unlicense). Findings: [docs/nerd-fonts-link.md](docs/nerd-fonts-link.md); recorded in `data/reviews/site/2026-09-28.toml`. *(2026-09-28)*
  - *Amended on 2026-09-29* (the owner's Nerd Font marker; Claude took TASK-2 over from Qwen). The field stays a nullable `links.nerd` in both schemas, before the freeze, and the placement stays the row and the details panel on every view. What changes:
    - **Which builds:** the Nerd Fonts project's builds and the makers' own NF builds (such as Maple Mono NF, Cascadia Code NF and Rec Mono).
    - **Target:** the build's own page instead of the one shared releases page. That is its folder in the Nerd Fonts repository at the current release tag, or the maker's release page for a maker-built one. The label names the build, such as "SauceCodePro Nerd Font".
    - **Marker:** a fixed-width **"NF"** beside the font's name. The Nerd Fonts logo may replace it once the maintainers agree (Backlog TASK-3). *Replaced on 2026-10-05:* a tag, "Nerd Font available", among the row's tags (`nerd_tag`; **First reviews of the test site**, below).
    - **Legend:** "NF: Nerd Font version available (adds developer icons, which have their own licenses)." *Replaced on 2026-10-05:* no legend; the caution moves into each font's details, beside the Nerd Font build link, after the freeze (`nerd_caution_in_details`).
    - **Filter:** a new "Nerd Font available" filter.
    - **Before launch:** Claude confirms every linked build's license check and hides the marker for any build that fails.

    Recorded in `data/reviews/site/2026-09-29.toml`.
  - *The owner's answers after the build, 2026-09-29:*
    - **README-only folders:** a Nerd Fonts folder that holds only a README stays linked.
    - **Hide switch:** the owner can hide a build's marker and link, with a reason, in `config/nerd-hidden.toml`. That was the owner's choice; Claude had recommended adding the switch only when a check fails.
    - **Broken link:** a Nerd link that fails its check is not shown (no marker, link or filter match) until it passes again. The font stays listed, and review.md flags it. This was the owner's own answer; Claude had recommended a warning only.
    - **Marker:** it stays at the end of the name's cell, in a column. *Moved later that day* to the end of the row's title cell, since the text name is hidden where a specimen shows (**Font names**, below). *Replaced on 2026-10-05* by the tag (`nerd_tag`).
- **Every qualifying font is listed.** Recorded in `data/reviews/site/2026-09-29.toml` (`more_fonts`, `font_requests`). *(2026-09-29)*
  - **The rest, A–Z (the owner's choice (b), Claude's recommendation).** Every font that passes the gates is listed, not only the catalog. The catalog keeps its ranks and bands. The other qualifying fonts (about 1,250 in the 2026-09-29 run) follow the ranked fonts in the same list, unranked and A–Z, under a heading such as "More truly free fonts". They show no rank, band or "501+", and they take the same filters, search, details, link policy and preview rules. The owned-font comparison takes owned fonts out of them too.
  - **Timing (2026-09-30).** Not part of getting the site live. The A–Z list and fonts added on request move out of Milestone 1 (step 15b) into a milestone of their own, [milestone-more-fonts.md](docs/milestone-more-fonts.md), after Milestone 2's launch and before or after Milestone 3's owned-font tools; the owner chooses which when Milestone 2 is done. Until then the site lists the ranked catalog only. Recorded in `data/reviews/site/2026-09-30.toml` (`more_fonts_timing`).
  - **Fonts added on request (the owner's variant of option (c)).** Anyone, whether a foundry, a designer or a visitor, may ask for a missing font by email to admin@trulyfreefonts.com (admin@purelyfreefonts.com from 2026-10-02; the old address keeps forwarding) or with the "Missing font" form. Claude checks it against Rules 1–4, D3, D4 and the link policy, and the owner rules in chat. An accepted family joins `config/foundries.toml` (amending M12) and is listed from the next refresh: ranked if its evidence places it in the catalog, otherwise A–Z. Being added never counts as popularity, and the public files never say who asked.
- **Name and domain (2026-10-02).** The site is renamed **Purely Free Fonts**, at **https://purelyfreefonts.com**. "Truly" is part of the name of a working type foundry, trulytype LLC of Los Angeles. The owner: "I'd rather *not* get a cease-and-desist letter, so I just registered purelyfreefonts.com." Claude's name check found no foundry, font shop or font trademark called Purely, and no other "Purely Free Fonts"; it is research, not legal advice. Recorded in `data/reviews/site/2026-10-02.toml`; the steps are `docs/milestone-2.md` step 13b and `ops/SERVER.md` section K. The owner took Claude's recommendation each time, except where noted.
  - **Addresses** (`site_name`): every old address 301s to the same path and query on the new domain (see **Domains** under Infrastructure).
  - **Wording** (`public_phrase`, the owner's choice; Claude had recommended neutral wording such as "free for any use"): public copy says "purely free" where it said "truly free". That covers the front page's heading, "The most popular purely free fonts", and its description, the 404 page, the license form, the README and headings to come. Dated rulings and records keep their wording.
  - **Wordmark** (`wordmark_stand_in`): it reads "Purely Free Fonts", drawn in League Gothic as before, until the owner's own typeface has its lowercase. Then it is drawn from that font, pinned by its file's sha256. The owner: "I plan on using my own font for the wordmark as much as I like the League Gothic story." *(Replaced on 2026-10-04 by the owner's own wordmark: see **Headline font**.)*
  - **Repository** (`repo_rename`): renamed `byronshock/purelyfreefonts` by the owner. The private data store keeps its name.
  - **Test site** (`test_site_host`): `staging.purelyfreefonts.com`, amending M2-D7.
  - **Contact** (`contact_address`): admin@purelyfreefonts.com. admin@trulyfreefonts.com keeps forwarding for good, since rulings, issue forms and old pages carry it.
  - **Ko-fi** (`kofi_closed`): the page ko-fi.com/trulyfreefonts is closed. Tips go through the Stripe link only.
  - **Other domains** (`typo_domain`, `typo_domain_mail`, `more_domains`): truelyfreefonts.com, and purelyfreefonts.org and .net.
  - **What stays:**
    - the internal names: the `tff` commands, packages and environment variables, `/srv/trulyfreefonts`, the deploy users, the server's hostname, the Cloudflare token and the project folder;
    - the frozen `catalog-site.json` v1 schema, whose `$id` is only an identifier;
    - every dated ruling and record.

    The catalog carries no site name or URL, so it isn't rebuilt.

  *(2026-10-02)*
- **Headline font.** The site's headline is set in **League Gothic** (The League of Moveable Type, OFL 1.1). *(2026-09-29; amended 2026-10-02: the wordmark reads "Purely Free Fonts", in League Gothic until the owner's own typeface has its lowercase, then in that font, see **Name and domain (2026-10-02)** under Site; amended 2026-10-04: the owner's own wordmark, below; amended 2026-10-06: the mixed-case wordmark)*
  - **The owner's wordmark (2026-10-04).** The header shows the owner's own drawing, "PURELY FREE FONTS" in capitals from his typeface Purely Constructed, as it is: black outlines on its white rectangle ("It is part of the branding: Typography started out as black on white."). It is 418 px wide from 760 px up and 278 px below that, keeping the old wordmark's capital height of about 38 and 25 px and the header on one row (Claude's recommendations). The file is pinned by sha256 (`site/static/_src/make_wordmark.py`, from byronshock/prb at 7d7f130). The About page now credits only Arimo (the owner's choice; Claude had suggested also naming his typeface). It replaces the League Gothic wordmark and its mixed case. Recorded in `data/reviews/site/2026-10-04.toml` (`wordmark_owners_svg`, `wordmark_size`, `wordmark_breakpoint`, `about_font_credit`). *(2026-10-04)* *Replaced on 2026-10-06* by the mixed-case wordmark below.
  - **The mixed-case wordmark (2026-10-06).** The header shows the owner's new drawing, "Purely Free Fonts" in mixed case, light, on its white plate (9717 x 1400): "I have a new wordmark SVG. I want to use this instead of the existing wordmark." The site serves the drawing only, without Inkscape's editor data (Claude's recommendation); `site/static/_src/make_wordmark.py` writes it from the pinned source, `_src/wordmark-source.svg`. It keeps the capital height of about 38 px and 25 px: 410 x 59 px from 760 px up and 270 x 39 px below, so the header is about 13 px taller (Claude's recommendation). Recorded in `data/reviews/site/2026-10-06.toml` (`wordmark_schoolbook_light`, `wordmark_drawing_only`, `wordmark_size_mixed`). *(2026-10-06)*
  - **Wordmark.** The headline is an SVG wordmark reading "Truly Free Fonts" in mixed case, with the letters drawn as outlines, scaled to be the site's headline. The file is `site/static/wordmark.svg`, drawn by `site/static/_src/make_wordmark.py`. *(2026-09-29)* *Replaced on 2026-10-04* by the owner's wordmark, above.
  - **Colour.** Black on white in both light and dark mode: the header stays white when the rest of the page is dark. *(2026-09-29)*
  - **Header.** The wordmark replaces the site name and the favicon mark in the header. It is about 48 px tall on wide screens and 32 px on phones. The header is white in both themes, and its links and text keep their light-theme colours. *(2026-09-29)* *Its size was replaced on 2026-10-04:* 418 px wide from 760 px up, 278 px below (`wordmark_size`, `wordmark_breakpoint`).
- **Interface font.** Headings and body text are set in **Arimo** (Steve Matteson, OFL 1.1), upright and italic, served from the site itself. It replaces the system font stack. Recorded in `data/reviews/site/2026-09-30.toml` (`ui_font`). *(2026-09-30)*
  - **Choice.** The owner compared pairings with League Gothic, then mockups of the site in Arimo, iA Writer Quattro and Open Sans, and kept Arimo: "We are going with Arimo for the headings and body text", then "I want to keep Arimo. I may regret it later."
  - **Files.** A Latin subset of Arimo 1.341 from google/fonts at 23e54b51, as WOFF2 with the weight axis (400 to 700): about 23 KB upright and 26 KB italic. `site/static/_src/make_ui_font.py` makes them, the build serves them from `/assets/ui/`, and `tff-site check` holds each to 30 KB. A browser fetches the italic only for a page that uses it.
  - **No shift.** Every page preloads the upright. With `font-display: optional`, a font that arrives late is never swapped in, and that page view keeps Liberation Sans or Arial, which share Arimo's widths. Monospace text keeps the system monospace stack.
- **Front-page note.** The front page carries "Why isn't my favorite free font here?" with the owner's text word for word (Claude's draft, accepted), the address a mailto link. On wide screens it is a textbook-style frame floated right beside the lead and privacy note, with the list starting below it. The owner kept this after seeing screenshots, and accepts the blank area it leaves below the short intro; Claude had suggested a wider frame. On phones it is a full-width frame folded to its heading, opened with one tap. Recorded in `data/reviews/site/2026-09-29.toml` (`why_not_listed`, `why_not_listed_layout`, `why_not_listed_gap`). *(2026-09-29)* *Changed on 2026-09-30:* it comes after the fonts and ends with the tip pitch (`why_not_listed_after_list`), and on 2026-10-05 it says "with no use restrictions" (`note_no_use_restrictions`; **First reviews of the test site**, below).
- **Front-page lead.** The owner's wording: "Every font here is free for any personal or commercial use, although sharing the files may be restricted. They are ranked by how many people install them and use them in their work." The page's meta description stays as it was. *(2026-09-29)*
  - *Changed on 2026-09-30*, after Rule 3 made every listed font redistributable: "Every font here is free for any personal or commercial use, and you may share the files. They are ranked by how many people install them and use them in their work." The front-page note "Why isn't my favorite free font here?" drops "or don't let you pass the font files on" and now reads "Some of these fonts ask you to credit the designer, and we mark those." Both were Claude's recommendation, taken by the owner. Recorded in `data/reviews/site/2026-09-30.toml` (`front_page_lead_sharing`). *Changed again on 2026-10-05:* the second sentence reads "They are ranked by public counts of use in websites, code and apps.", landing with the Project default (`front_page_lead_counts`; **First reviews of the test site**, below).
- **Early-version line.** Every page's header says "Early version. Coming next: free font inventory tools." (the owner's wording) until Milestone 3 removes it. *(2026-09-29)*
- **Scores instead of numbers (Milestone 2).** The owner's idea, after a filtered list ran 1 to 25 and then showed bands. Recorded in `data/reviews/site/2026-09-29.toml` (`score_display`, `score_curve`, `score_held_fonts`, `score_field_timing`). *(2026-09-29)* The column's heading says **"Popularity"** (`score_column_popularity`, 2026-09-30; **First reviews of the test site**, below).
  - **Display.** The list shows each font's score, 0 to 100, as a blue bar where the rank number is now. Filters only hide rows: no renumbering, and no bands in the list. A font's details keep its rank and range (*the range goes on 2026-09-30*, **No tiers or likely ranges**, below). This replaces M2-D2 when it lands.
  - **Score.** 100·Φ(z), the normal curve of the engine's fused score for the rank (Claude's recommendation; the owner had suggested the logistic, which differs by about one point at most). It is relative standing among the fonts the sources track, not the chance that a given person chose the font. *Replaced on 2026-10-01*, with the measurement model, by a linear score between fixed ends (`q11_display_linear`, `q30_display_ends`; see **The scoring method** under Ranking).
  - **Held fonts.** The list sorts by score: a font the two-source rule holds back takes its score's place and carries a marker. The catalog's ranks and bands keep the rule. That was the owner's choice; Claude had recommended a note instead of a score. In Developers & apps, where every font rests on one kind of source, one note above the list replaces the row marker (`held_marker_dev_apps`, 2026-09-30). The marker is a hollow bar, explained by one legend line above the list (`held_marker_style`, 2026-09-30, Claude's recommendation).
  - **When.** `catalog-site.json` carries each rank's score from Milestone 1, before the step 20 freeze. The display is built in Milestone 2.
  - **Last month's score.** *(2026-09-30)* Each rank entry also carries `previous_score`, the font's score in the last published catalog, kept from month to month in `state/published_scores.json`, so the schema holds what Backlog TASK-4's green rising and red falling triangles need before the freeze; the triangles themselves wait. To bootstrap, and only then, last month's score is this month's: until a monthly refresh has been merged no scores are published, so `previous_score` equals `score` and no triangle can show. `tff-catalog validate` asserts it, in a run and on the committed files (the owner's words: "we can assert this to be the case"). Recorded in `data/reviews/site/2026-09-30.toml` (`score_previous_bootstrap`). *Amended on 2026-10-01:* a rank's first run under a new method bootstraps the same way (`q20_previous_score_at_switch`).
- **Font names.** A row with a specimen shows the family name once, drawn in the font by the specimen's first line. The text name stays in the page for screen readers, search and find-in-page, and shows whenever the specimen isn't shown. The NF marker moves to the end of the row's title cell (the specimen, or the text name where there is none), still in a column. The owner chose to hide the text name; Claude had recommended dropping the name from the specimen. Recorded in `data/reviews/site/2026-09-29.toml` (`name_once`, `nerd_marker_spot_title`). *(2026-09-29)*
- **Specimen sample line.** "Dolorem ipsum quaerit nemo." (the owner's wording: Cicero's phrase behind Lorem ipsum, with descenders on both sides), in place of the Polish and Czech line of 2026-09-26 and of "Dolor dolorosus est.", chosen earlier the same day. "quaerit" is spelt without the æ ligature, which fonts with basic Latin only lack. Recorded in `data/reviews/site/2026-09-29.toml` (`specimen_sample_latin`). *(2026-09-29)* *Replaced on 2026-10-05:* a row's specimen shows the family name only, larger; the details show "Hamburgefonstiv 0123", a code line for monospace fonts and a glyph strip, and "Dolorem ipsum quaerit nemo." retires (`specimen_name_only_list`, `details_sample_lines`, `glyph_strip`; **First reviews of the test site**, below).
- **Specimen size cap.** A specimen may be up to 16 KB compressed (gzip -9), about what a visitor downloads, in place of 30 KB uncompressed; a larger one shows the family name only. The owner's answer after Claude showed that the raw cap made six fonts name-only although each was 7 to 15 KB compressed. Recorded in `data/reviews/site/2026-09-30.toml` (`specimen_max_size`). *(2026-09-30)*
- **Filters (2026-09-30).** The owner found the filters repeated one another. The sidebar now holds, in order:
  - Search;
  - Rank, with its measures line;
  - Category, where **Monospace** means every monospaced font (the list the Coding rank orders) and the other categories list proportional fonts only;
  - Features: Variable, Nerd Font available, and Accented letters (which hides fonts with basic Latin only);
  - License: one **"No credit required"** checkbox, shown only while some listed font needs credit;
  - "Hide fonts that come with", as one select: Windows, macOS, Linux or Android;
  - "Clear filters".

  **Removed:** the Spacing filter, the four license-group checkboxes, "Hide attribution required" (now "No credit required") and "Redistributable fonts only" (Rule 3).

  **Sort** (by rank or by name) moves to the list header, beside the count. *Changed the same day* (`sort_header`): sorting is a row of buttons over the list, one over the rank column and one over the names. Each is a bordered button with two stacked arrows and, on the column in use, the order's words ("best first" or "least used first", "A–Z" or "Z–A"). Clicking the button in use reverses its order, and clicking the other sorts by it in its usual order. Reversing the rank is a true reverse, with the unranked fonts first: that was the owner's choice, where Claude had recommended keeping them last. The header sits over the rank numbers until the score bars replace them. The order's words take a second, smaller line inside the button, so the Rank button fits over the 96px rank column and Name starts over the names (`sort_two_lines`, Claude's recommendation, after the audit found Name 34px off). *Changed on 2026-10-05:* the order's words are "most popular first" and "least popular first" (`sort_words_popular`; **First reviews of the test site**, below).

  **Rows:** the badges get shorter. "Comes with" names only the operating systems and apps, "Pulled in by" appears only in the details panel, and there are no "Monospace" or "Not redistributable" badges. *Changed on 2026-09-30:* "Comes with" names the systems, such as "Comes with CachyOS, EndeavourOS", not just "Linux" (`comes_with_names_systems`; **First reviews of the test site**, below).

  **Old links:** links that use the retired `spacing`, `lic` or `redist` keys still open; `spacing=monospaced` becomes the Monospace category.

  **Variable fonts** are labelled "Adjustable weight (variable font)" in the filter and "Adjustable weight" on the rows, because "Variable" alone reads as "proportional" (letters of varying width). This was Claude's recommendation, taken by the owner (`variable_label`).

  The monospace and license choices were the owner's, from Claude's recommended options. The rest was Claude's proposal in the plan the owner approved. Recorded in `data/reviews/site/2026-09-30.toml` (`monospace_category`, `license_filter`, `filters_layout`). *(2026-09-30)*
- **Details panel (2026-09-30).** The owner found a font's opened details too busy, and chose essentials first with the evidence folded (Claude's recommended option). Recorded in `data/reviews/site/2026-09-30.toml` (`details_layout`). The small "Ranks" and "Sources" headings inside the fold stay (`fold_headings`). *(2026-09-30)* *Amended on 2026-10-05*, landing after the freeze: the designer link is labelled "Designer's page", and the sample lines and a glyph strip join the details (**First reviews of the test site**, below).
  - **Order.** First the "Type your own text" button, which still loads the font only when clicked. Then one short list:
    - **Get it:** the official, designer and Nerd Font build links;
    - **License:** the license, and whether credit is needed;
    - **Font:** formats and Latin coverage;
    - **Comes with:** the systems that preinstall it;
    - **Also known as:** other names;
    - **Rank:** the rank chosen in the selector, with its likely range (*the range goes*, **No tiers or likely ranges**, below).

    Last comes "Report a problem".
  - **Folded.** A closed "All ranks and sources" disclosure holds every rank with tier and range, the tier legend, "Pulled in by" and the per-source tables. (*The tiers, ranges and legend go*, **No tiers or likely ranges**, below.)
  - **Dropped:** the section headings, the license-group row, the Redistributable row, the Spacing row, and the Nerd Font legend repeated from above the list.
- **No tiers or likely ranges (2026-09-30).** The site shows no tiers and no likely ranges. Every rank shows just its place ("#12", or its band past 100). Removed: the tier letters, the Firm/Fair/Rough legend, the "likely #8 to #17" ranges, the methodology page's tier section, and those fields in the site's details data. `catalog-site.json` v1 keeps its tier and range fields, unshown (`q19_ranges_tiers_stand_in`). The owner: "Tiers are something you came up with, not me." Of the folded details: "Yuck. Can we take these details out? The site is heavy as it is." The owner took Claude's recommended option, "Tiers and ranges, everywhere", over tiers only or the whole per-rank list, and then froze the work "until the methodology is sound", so it lands after the freeze. Recorded in `data/reviews/site/2026-09-30.toml` (`no_tiers_or_ranges`). *(2026-09-30)*
- **First reviews of the test site (2026-09-30 and 2026-10-05).** Three reviews of the test site on 2026-09-30: the owner's first impressions, Claude's critique from inside the team, and an outside reviewer (a Claude chat given no project context). The working checklist is [PLAN-REVIEWERS-1.md](PLAN-REVIEWERS-1.md). The owner's rulings of 2026-09-30 and answers of 2026-10-05 are recorded in `data/reviews/site/2026-09-30.toml` and `data/reviews/site/2026-10-05.toml`. The owner took Claude's recommendation each time, except where noted. The build waits where the plan says: anything that needs the Project default or the new scores waits for the scoring-method port, and the details changes wait for the freeze.
  - **Order** (`reviewer_priorities`): the outside reviewer's three priorities, which the owner agreed to: match the homepage claims to the evidence; put the fonts first; free up page weight before adding anything to the list page.
    - *Amended on 2026-10-05* (`step7_list_first`): the list part of priority 2 lands first, right after the plan's pull request (#56), in three pull requests: bigger names-only specimens, the NF column and legend, and Download as the button. Step 8's parts that touch no template go alongside. The template work of steps 8 and 5 waits for the parked Project-default branch.
  - **Layout:**
    - the filter sidebar stays in view on wide screens (`filters_sticky`, the owner's words: "Obviously we should have a fixed sidebar on large screens");
    - "Why isn't my favorite free font here?" comes after the fonts, as a short conversation with the reader that ends with the tip pitch, and also appears when a search finds nothing (`why_not_listed_after_list`, the owner's idea);
    - on phones a row merges lines, so about five fonts fit on a screen (`phone_rows_merged`). *Amended the same day* (`phone_rows_trim`): main already fits about five rows per phone screen, so Download becomes the button and a few pixels of row margin go, but category and license aren't merged;
    - "Download from …" gets the button style, and Details the quieter one (`download_button`).
  - **Words:**
    - the score column's heading is "Popularity" (`score_column_popularity`, the owner's idea);
    - the rank selector's label is "Measure" (`selector_label`);
    - the Project rank is labelled "Projects: most used" (`project_rank_label_most_used`);
    - the sort button says "most popular first" and "least popular first" (`sort_words_popular`);
    - the methodology page is "How we measure popularity", and its nav link "How it works" (`method_page_name`; the owner had suggested "How we evaluate popularity");
    - How we rank's "(owner ruling of …)" asides are rephrased as public text in the methodology's shown sentences (`method_asides_rewrite`).
  - **Claims** (priority 1):
    - the front-page lead's second sentence reads "They are ranked by public counts of use in websites, code and apps." (`front_page_lead_counts`, the owner's choice; Claude had recommended "…how widely they are used…");
    - the note says "with no use restrictions" (`note_no_use_restrictions`);
    - "Comes with" names the systems (`comes_with_names_systems`);
    - the details label the designer link "Designer's page" (`designer_label`).
  - **Specimens** (priority 2):
    - a row's specimen shows only the family's name, larger (`specimen_name_only_list`, the owner's call: "People come to see the fonts, and the font names set in sample fonts are already too small");
    - until `build/specimens` is redrawn names-only, the site build serves a copy trimmed to the name line from each committed, sha256-checked file, for the pinned files only (the 500 committed specimens and the sample's 5, each cut checked against its font's names-only drawing), with nothing in `build/` or the catalogs changing (`specimen_trim_served`; built in #58). While the details panel is frozen, the site shows no sample lines;
    - the specimen box is 64 px tall on wide screens, and 48 px on phones once the NF column stops reserving space on rows without a Nerd Font build (`specimen_box_heights`);
    - the details show "Hamburgefonstiv 0123", a line of code for monospace fonts (`details_sample_lines`, the owner's choice of lines) and a glyph strip (`glyph_strip`, the owner's choice; Claude had recommended "not now"). All load only when the details open;
    - the NF legend shows only while an NF row is visible, and on phones the NF column takes space only where it's needed (`nf_legend_visible_rows`). *Replaced the same day* (`nerd_tag`, the owner: "Make NF a tag like Adjustable weight, 'Nerd Font available'. Get as much space as possible for the name. Then center the score."):
      - the NF mark becomes a "Nerd Font available" tag, the NF column and the legend go, and the name gets the title cell's full width;
      - the score is centred on the specimen, with the hidden name over the drawn one (`score_centred`);
      - the caution about the icons' licenses moves into the details, after the freeze (`nerd_caution_in_details`, the owner's choice; Claude had recommended a legend line);
      - the specimen image is decorative for screen readers, since the heading names the font (`specimen_label_hidden`).
      - the details panel drops its "NF" box before the Nerd Font build link, whose text names the build (`details_nerd_box_dropped`); a find-in-page match shows the hidden heading's text over the drawn name while active (`find_highlight_accepted`).
      - *2026-10-06:* the details' Nerd Font link gets a visible "Nerd Font:" lead-in (`details_nerd_lead_in`); on phones five rows per screen holds in the default view, while the Coding view and the Nerd filter fit four (`phone_rows_four_in_coding`). Recorded in `data/reviews/site/2026-10-06.toml`.
  - **Search:**
    - when a search finds no listed font, the list says "No listed font matches '…'" and shows the note; later, once names.json's reasons are cleaned up, it also gives the reason and free fonts with the same letter widths (`no_match_search`);
    - fonts whose own name matches come first (`search_name_first`).
  - **Page weight** (priority 3):
    - Brotli, with CI's page budget measuring the Brotli size (`brotli`);
    - every row stays in the front page's HTML (`rows_all_in_html`, the owner's choice; Claude had recommended the top 100 rows plus a static `/all/` page).
  - **Tips** (`funding_pitch`, `tip_jar`): see **Funding**. The footer's tip link is removed.
  - **Unchanged:** the header stays black on white in dark mode (`header_dark_mode_kept`, the owner: "black on white is the branding of typography"), and the license name stays on every row.
  - **Milestone 3** (`inventory_additions`): three new Step 0 decisions:
    - "Hide fonts that come with" as the first, no-permission step;
    - "Show only mine";
    - a test that no font name enters the page address.

  *(2026-09-30 and 2026-10-05)*

## Infrastructure

*(2026-09-25; setup checklist and runbook in [ops/SERVER.md](ops/SERVER.md))*

- **Domains.** trulyfreefonts.com, .org and .net, registered and hosted on Cloudflare. The canonical URL is `https://trulyfreefonts.com`; `www.*`, `.org` and `.net` 301-redirect to it, keeping the path.
  - *Amended on 2026-10-02* (see **Name and domain (2026-10-02)** under Site). The canonical URL becomes `https://purelyfreefonts.com` at the cutover in `docs/milestone-2.md` step 13b. These domains, all registered and hosted on Cloudflare, then 301 to it, keeping the path and query:
    - trulyfreefonts.com, .org and .net, and their `www.` hosts (through Caddy, as now);
    - `staging.trulyfreefonts.com`, to `staging.purelyfreefonts.com`;
    - truelyfreefonts.com, a misspelling a staging tester typed (registered 2026-10-02), with an edge redirect at Cloudflare and no origin;
    - purelyfreefonts.org and .net, the same way.

    Mail to admin@ on purelyfreefonts.com, trulyfreefonts.com and truelyfreefonts.com is forwarded to the owner; the .org and .net domains take and send no mail. All seven domains stay registered for as long as their redirects matter.
- **Hosting.** A Contabo VPS running Debian 13, served by Caddy, with the site root at `/srv/trulyfreefonts/public`.
- **Cloudflare.** Proxied (orange cloud), SSL mode Full (strict), with a Cloudflare Origin CA certificate on the server.
- **Access.** One admin user `byron`, key-only SSH, passwordless sudo, root login off. Local alias `ssh tff`. Claude manages the server over SSH and Cloudflare through an API token; its ssh/scp/rsync commands to `tff` are auto-allowed in this project.
- **Secrets.** Kept out of the project: the Cloudflare token lives in `~/.config/trulyfreefonts/cloudflare.env`, and the root password (VNC emergency access only) lives in the owner's password manager.
- **Visitor privacy on the server.** Cloudflare's Network Error Logging is off on all three zones (on all seven since 2026-10-02: the four new ones are in `ops/SERVER.md` section K). The access log masks visitor IPs to /16 (IPv4) and /32 (IPv6), drops the port and IP-carrying headers, and is kept for 14 days. *(2026-09-25)* It also drops Referer and User-Agent. *(2026-09-26)* It also drops Cookie and Cloudflare's location headers finer than the country. *(2026-09-28)*

## Repository and workflow

*(2026-09-25)*

- **Repository.** Public, at [github.com/byronshock/trulyfreefonts](https://github.com/byronshock/trulyfreefonts), default branch `main`. *Renamed on 2026-10-02* [github.com/byronshock/purelyfreefonts](https://github.com/byronshock/purelyfreefonts); GitHub redirects the old URLs, so the old name is never reused. The private data store keeps the name `byronshock/trulyfreefonts-data`. The methodology and rankings are open.
- **Kept out of git.**
  - `PLAN.md`: private planning notes.
  - `ops/SERVER.local.md`: origin IPs, VNC console and zone IDs; publishing the origin IP would let anyone bypass Cloudflare.
  - `.claude/settings.local.json`.
- **Tracked docs.** These are versioned:
  - `docs/ranking-methodology.md`;
  - `docs/roadmap.md`;
  - the milestone checklists `docs/milestone-1.md` to `docs/milestone-4.md`, and `docs/milestone-refresh.md` and `docs/milestone-more-fonts.md` *(2026-09-30)*;
  - `docs/owned-fonts.md`, the notes for Milestone 3;
  - `docs/usability-test.md`, the usability test plan (Milestone 2 step 12) *(2026-09-30)*;
  - `PLAN-NERD-FONTS.md`, the working checklist for Backlog TASK-2. *(2026-09-28)*
  - `PLAN-REVIEWERS-1.md`, the working checklist for the changes from the first reviews of the test site. *(2026-10-05)*
  - `docs/milestone-1-handoff.md`, Milestone 1's handoff note, and `ops/MONTHLY.md`, the monthly runbook. *(2026-09-30)*
- **Monthly data refresh.** A scheduled GitHub Actions workflow runs the pipeline and opens a pull request with the new catalog and anything flagged for review. The owner reviews and merges it.
  - **Timing (2026-09-30).** The first live refresh and the monthly schedule wait until after Milestone 2's launch, and come before Milestone 3: [milestone-refresh.md](docs/milestone-refresh.md), which takes the rest of Milestone 1 step 19 and Milestone 2 step 16. Until then the site shows the 2026-09-26 catalog, which `state/` holds as the first published run (#31). Recorded in `data/reviews/ci/2026-09-30.toml` (`refresh_timing`).
  - **Off switch (2026-09-29).** Until Milestone 1 step 19 sets up the data store's key, the schedule does nothing. A scheduled run starts only once the repository variable `REFRESH_SCHEDULE` is "on", as production deploys wait for `PRODUCTION_DEPLOYS`; manual runs always work. So merging the workflow to `main` starts no failing monthly run. Recorded in `data/reviews/ci/2026-09-29.toml`.
- **Site tests on the real catalog.** *(2026-09-30)* CI's `site-real` job and `deploy.yml` run the site tests on the committed catalog (500 rows) with a lighter accessibility pass:
  - **On the real catalog:** axe on every page in both themes at desktop width, axe with a details panel open, reflow at 320 px, and every test that depends on the data (list, details, specimens, privacy, serving).
  - **On the sample catalog only** (`site-browser`, marked `sample_only`): the rest of the accessibility grid, which covers every width, filter state, forced colours, text spacing, focus and the keyboard paths. It tests the templates and CSS, which are the same in every row, and on the real catalog it ran past CI's 45-minute limit.
  - Milestone 2 step 6's axe requirement is met by the sample run. Recorded in `data/reviews/ci/2026-09-30.toml`.
- **Backlog.** *(2026-09-25)*
  - **Tool.** [Backlog.md](https://github.com/MrLesk/Backlog.md) keeps its tasks in `backlog/`, committed to the public repository.
  - **What goes in it.** Only loose items: ideas, bugs and later work that no milestone checklist covers. The milestone checklists stay the plan, and decisions stay in this file, never in `backlog/decisions/`.
  - **Changes.** A task's status changes in the same pull request as its work.
  - **Agents.** `CLAUDE.md` tells Claude sessions to use the `backlog` command.

## Funding

- A donation button on the site, and nothing more: no donation strategy or fundraising work. By the owner's reckoning the site costs nothing except their time. *(2026-09-25)* *Amended on 2026-09-30 and 2026-10-05:* also one short pitch, at the end of the note "Why isn't my favorite free font here?", asking the reader to leave a tip with no amount in the text, and a "Tip Jar" link at the end of the header nav. Both go to the Stripe Payment Link, prefilled with the $5 suggestion (`funding_pitch`, `tip_jar`; **First reviews of the test site**, below).
- **Provider.** A Stripe Payment Link where visitors choose the amount, with $5 suggested. No code and no Ko-fi. The link goes on the site with the intermediate release (the filterable list), not on the current stub. Checklist in [ops/DONATIONS.md](ops/DONATIONS.md). *(2026-09-25)*

## Current step

1. Survey the tools that already exist for this, or for parts of it. *(done 2026-09-25; findings in [docs/prior-art.md](docs/prior-art.md))*
2. Milestone 1: handed off to Milestone 2 (step 20). The 2026-09-26 catalog is built, reviewed by the owner and committed, and `catalog-site.json` v1 is frozen. The handoff note is [docs/milestone-1-handoff.md](docs/milestone-1-handoff.md), and the monthly tasks are in [ops/MONTHLY.md](ops/MONTHLY.md). Left in [docs/milestone-1.md](docs/milestone-1.md): the owner's acceptance of the handoff, and three smaller items (steps 3, 5 and 13). *(2026-09-30)*
3. **Milestone 2 is the current step:** the filterable list goes live ([docs/milestone-2.md](docs/milestone-2.md)). Its Step 0 decisions are answered, and the questions Milestone 1 left for it are settled (the handoff note). After it come the first live monthly refresh ([docs/milestone-refresh.md](docs/milestone-refresh.md)) and Milestone 3. Milestones 3 and 4 keep their Step 0 until each starts ([docs/roadmap.md](docs/roadmap.md)). *(2026-09-30)*

## Background

- The earlier top-100 library is at `~/Documents/code/fonts`; its `MANIFEST.md` explains the ranking method. Its 8 Fontshare families (Satoshi, General Sans, Clash Display, Cabinet Grotesk, Switzer, Ranade, Gambetta, Chillax) are under the ITF license and do not qualify.
