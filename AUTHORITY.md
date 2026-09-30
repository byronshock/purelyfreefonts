# AUTHORITY — truly_free_fonts

This file holds the project's settled decisions. Everything else in the repo follows it. Only decisions the owner has made go here. Research and drafts live elsewhere and are linked below.

## Purpose

Compare the fonts a user already has installed with a ranked list of the most popular free Latin fonts, and show the ones they don't have. The preferred delivery is a web page.

## Releases

1. **Intermediate release: the filterable list.** trulyfreefonts.com first publishes the ranked list of truly free fonts, with filters, but **without** comparing against the fonts a visitor owns. It replaces the current stub. Its purpose is to test usability while the owned-font tools are built and tested. *(2026-09-25)*
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
- **D1: method.** Each source's order is mapped onto one shared scale. A source that doesn't carry a font is left out rather than counted as zero, and thin evidence is pulled toward the middle. Coverage-aware reciprocal-rank fusion runs monthly as a cross-check. *(2026-09-25)*
- **D3: license classes.**
  - CC-BY fonts qualify, with an "attribution required" badge.
  - Copyleft licenses without a font exception (CC-BY-SA, plain GPL/LGPL, AGPL) are excluded.
  - Any ban on modification or embedding excludes a font, extending Rule 4.
  - Previews are shown only for redistributable fonts, served unchanged. Since Rule 3 of 2026-09-30, every listed font is redistributable.

  *(2026-09-25)*
- **D4: Latin.** Google's strict metadata test, plus dual-script families the owner approves from a reviewed short list. *(2026-09-25)*
- **D7: patched builds.** Nerd Font and CJK builds count in full toward the original family. *(2026-09-25)*
- **D10: project rank.** It covers websites, code and apps: web 55%, code 30% (including ecosyste.ms dependent repositories), apps 15%. *(2026-09-25)*
- **D12: overall rank.** Desktop (*most chosen*) 50% plus project 50%, usage only, with no designer picks. *(2026-09-25)*
- **D13: extra views.** Publish all four: Coding fonts, Developers & apps, By category and Rising (beta). *(2026-09-25)*
- **D14: evidence gate.** A top-100 place needs evidence from at least 2 independent source groups, plus a mild pull of thin evidence toward the middle (κ 0.2). *(2026-09-25)*
- **D15: snapshots.** Monthly data extracts live in a private GitHub data repository, reached with a deploy key or GitHub App. *(2026-09-25)*
- **D17: licenses.** The code is MIT. The catalog data is CC BY-SA 4.0, now final (ruling T5 below). *(2026-09-25)*
- **Terms rulings (Milestone 1 step 3).** What the catalog may use and publish from each source. Recorded in `data/reviews/terms/2026-09-25.toml`; the full table goes in `docs/sources.md`.
  - **T1: open sources.** For Homebrew, pkgstats, Arch and Debian package data, popcon, npm, Fontsource and jsDelivr, GitHub counts, the Web Almanac (Apache-2.0), ecosyste.ms (CC BY-SA 4.0) and Nerd Fonts `fonts.json` (MIT), raw counts may be published. The public repo may hold small, trimmed real fixtures with credit notices.
  - **T2: Google.** `/metadata/fonts` and `/metadata/stats` are used. Only ranks and z scores are published, never view counts. Public fixtures are synthetic; real data stays in the private store.
  - **T3: Chocolatey** is dropped from v1, because its terms forbid scripted access and republishing. The methodology notes the thinner Windows coverage.
  - **T4: Fonts Over Time** is used as D11's default says: at the phase-in weight of 0.10, with credit and a link back, ranks only (ranks and their rank-based z scores, never its raw values), and synthetic fixtures. Claude drafts the license request, and the owner posts it.
  - **T5: data license.** CC BY-SA 4.0 is final.

  *(2026-09-25)*
- **Method clarifications (M1–M12).** Recorded in `data/reviews/method/2026-09-25.toml` and in the methodology.
  - **M1:** the outlier guard stays (gap 1.5 z, at least 3 terms, half weight). *Superseded on 2026-09-26:* the guard compares each term with the median of all the font's terms, and the worked example goes back to JetBrains Mono 2.48.
  - **M2:** GitHub counts every release of a main-channel repo, as growth between snapshots, with no 24-month cap; Iosevka uses its latest 24 releases, through GraphQL.
  - **M3:** Homebrew Nerd casks get their own floor each run, the 10th percentile of Nerd casks' 365-day installs (about 2,150 a year), subtracted before `nerd_credit`.
  - **M4:** the Arch Nerd Fonts group floor is the 10th-percentile share of members in the group at least 6 months; newer members are not floored.
  - **M5:** GitHub counters and Homebrew are one independence group for a font whose Homebrew cask downloads that repo's release asset. *Amended on 2026-09-29:* only GitHub release counts merge; Nerd Fonts is a group of its own (gate R round 1, below).
  - **M6:** the Almanac's pages tab is the term; its services tab only flags parent merges.
  - **M7:** ecosyste.ms counts `@fontsource` and `@fontsource-variable` packages only.
  - **M8:** a Linux source abstains when the largest single dependent brings in at least 50% of installs; in `a | b` the first alternative is credited; 35–50% is flagged for review.
  - **M9 (the owner's choice, not the recommended default):** the project group shares stay fixed at web 55%, code 30%, apps 15%; within a group, the sources share its weight pro rata to their effective weights (after phase-in, overlap scaling, stale drops and switched-off sources).
  - **M10:** Developers & apps uses the project weights, rescaled: npm 0.15, ecosyste.ms 0.10, Expo 0.10, and Flutter 0.05 when it is on.
  - **M11:** the top-100 lists have hysteresis: a font enters at 90 or better and leaves after 2 runs worse than 110.
  - **M12:** foundry families are a hand list in `config/foundries.toml`, seeded once by Claude from the foundry sites and reviewed by the owner with the step 2 config. *Amended on 2026-09-29:* families are also added to it on request (see "Every qualifying font is listed" under Site).

  *(2026-09-25)*
- **Rulings after the first real run.** Given in chat on 2026-09-26 and recorded in `data/reviews/<gate>/2026-09-26.toml` (method, terms, config, latin, licenses, l3, aliases, unmatched, corrections, links, review, ci, site). Unless noted, the owner took Claude's recommendation.
  - **Outlier guard (the owner's own option):** each term is compared with the median of all the font's terms; one more than 1.5 z from it gets half weight. The worked example is JetBrains Mono 2.48.
  - **Method:** Developers & apps shows bands only while its sources share one independence group (until Flutter is on); Rising is tier C while in beta; the confidence draws keep re-weighting across all of a survey's sources (the owner's choice); release assets created after the baseline count in full; §9's monotonicity wording as written there.
  - **Terms:** Google's monthly shares for Rising are kept in the private data repository, never in the public `state/`; Rising uses them.
  - **Linux sources:** a preinstalled system silences only the Linux sources that count it (Arch-family systems pkgstats, Debian and Ubuntu popcon, desktops both); popcon and pkgstats count a family by its most-installed package.
  - **Config (gate C):** `preinstalled.toml` and `foundries.toml` are approved; LibreOffice is listed as an application (`os = "app"`); EndeavourOS takes its fonts from eos-base-group; Blackout is three families.
  - **Latin (gate L):** at most 2 GF_Latin_Kernel code points missing, a Latin share of at least 30%, "extended" with at most 3 GF_Latin_Core code points missing; Google families served only with the menu subset take the glyph test; Single Day is out. Of the dual-script Google families (D4's short list), those with full Latin Extended are included unless they are script companions (a script version of a family already listed, such as Noto Sans Arabic or Hind Siliguri); companions and basic-Latin-only families stay out. Claude applied the rule family by family in `data/reviews/latin/2026-09-26.toml`: 178 included, 206 companions and 59 basic-Latin-only families left out. The owner was shown 327 included and 116 left out; on 2026-09-28 the owner confirmed that the 149 companions between the two counts (134 Noto script families, 9 Baloo 2 script versions, IBM Plex Sans Arabic, Mukta Mahee, Malar and Vaani, Playpen Sans Arabic and Deva) stay out.
  - **Licenses (gate LIC):** Bitstream Vera, Bitstream Charter, the Ubuntu Font Licence, the GUST Font License and IPA qualify (open-font group); WTFPL, LPPL 1.3c and public-domain dedications qualify (permissive); X11, BSL-1.0 and Artistic-2.0 qualify (permissive) and MPL-2.0 (open-font); GPL and LGPL with the font exception qualify; Lack is excluded until it publishes a license; Monofur, Vic Fieger, freeware grants, Arphic, Artistic-1.0 and Letters are researched; a family with no license is excluded, but Claude researches any that would reach the overall top 700. OpenDyslexic, Overpass, Roboto Mono, Tinos, TeX Gyre Heros, Cascadia Code, Cascadia Mono and Hack qualify and are redistributable (their disagreements are outdated labels); the four Salaowu families are researched.
  - **License checks (gate L3):** Claude researches all 32 fonts that failed the automatic check and asks the owner only about genuine conflicts.
  - **Aliases and unmatched keys (gates A, U):** Iosevka Term and Term Slab fold into Iosevka; build variants (Maple Mono CN and NF, Cascadia Mono NF and the other NF and PL builds by the build rule, Inter Variable) fold into their family, and so do Monocraft's Nerd builds and the width names a parent's own files carry; Monaspace, Libertinus, Noto, iA Writer and M+ are bundles (D2); the 53-repository GitHub list and its 914 plain rows are accepted; distro package rows wait until their contents are researched; Material Icons and Symbols are icon fonts; a short `build/universe.md` is committed.
  - **Links (gate K):** the four overrides (Adobe Source, IBM Plex, Inter, JetBrains Mono) are approved. A family on the approved foundry list may use that list's link with no second source; `http://` homepages are upgraded to `https://` and checked monthly; Inter Tight keeps its Google page; open-foundry.com counts as an aggregator.
  - **Review:** a move is flagged when it is over 30 places and over 30%; the what-if table stays in the private review pack; catalog fonts with the default category are listed in the pack, and @fontsource/fira-mono is checked at gate R; ecosyste.ms packages are flagged stale after 90 days.
  - **Refresh (gate CI):** Claude sets up the pull-request permission and the data key with `gh` when step 19 is ready; the owner creates the watchdog's token after the first refresh pull request.
  - **Site:** the footer link reads "Leave a tip ($5 suggested)" on every page; the access log also drops Referer and User-Agent (Claude's recommendation in chat).

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
  - **Method:** the code implements method version 2026-09-29 (`METHOD_VERSION` in `src/tff_catalog/__init__.py`, which each catalog records as `run.method_version`), with every ruling above up to gate R round 2. The owner's ruling of 2026-09-29 moved it on from 2026-09-25 to cover that day's changes: M5 as amended (Nerd Fonts its own independence group), the owner's category overrides, archived-link overrides, and the Nerd Font build link (`links.nerd`, TASK-2). Its public text is [docs/ranking-methodology.md](docs/ranking-methodology.md), published on 2026-09-29.
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

## Site (Milestone 2)

Answers to the Milestone 2, Step 0 decisions in `docs/milestone-2.md` ([pull request #2](https://github.com/byronshock/trulyfreefonts/pull/2)).

- **M2-D1: default rank.** The list opens on the **overall** rank. *(2026-09-25)*
- **M2-D2: numbers under filters.** Each filtered list renumbers from 1. Fonts past the exact top 100 show their band instead of a number. *(2026-09-25; replaced in Milestone 2 by **Scores instead of numbers**, below, 2026-09-29)*
- **M2-D3: page technology.** Plain HTML, CSS and one script, with no framework, built by a Python command in the same project as the catalog. *(2026-09-25)*
- **M2-D4: filter layout.** Every filter sits in a sidebar on wide screens. On phones, everything but search and rank goes behind one "Filters" button. *(2026-09-25)*
- **M2-D5: previews.** SVG specimens are drawn at each refresh. A "Type your own text" box loads the unchanged font file only when clicked. Only redistributable fonts get previews. *(2026-09-25)*
- **M2-D6: deploy.** GitHub Actions deploys each push to `main` as a restricted `deploy` user, and a laptop script is the fallback. *(2026-09-25)*
- **M2-D7: test site.** `staging.trulyfreefonts.com` is used for usability round 1 and reused by Milestone 3. *(2026-09-25)*
- **M2-D9: server logs.** See Infrastructure, visitor privacy on the server. *(2026-09-25)*
- **M2-D12: blog.** *(2026-09-25; checklist in [Milestone 2 step 7b](docs/milestone-2.md#step-7b-blog))*
  - **System.** Posts are Markdown files in the repository, built by `tff-site` with the rest of the site. There is no separate blog engine.
  - **When.** It ships with the list release (Milestone 2).
  - **Address.** Posts live at `/blog/`, with an Atom feed at `/blog/feed.xml`.
  - **License.** Post text and images are under CC BY-SA 4.0, the same as the catalog data.
- **Spacing filter.** "Spacing: Any / Proportional / Monospaced" replaces D13's "Text only" filter and the "monospace only" checkbox. *Proportional* is the old "Text only": it hides monospace and coding fonts. The filter applies to every rank. Recorded in `data/reviews/site/2026-09-25.toml`. *(2026-09-25; replaced by **Filters (2026-09-30)**, below)*
- **Project rank label.** On the site, the Project rank (D10) is labelled **"Used in projects"**. *(2026-09-25)*
- **Site data fields (M2 step 2).** The `catalog-site.json` fields in `schemas/catalog-site.schema.json` are approved as drafted. Designer lists are left out, because D12 is usage only. *(2026-09-25)*
- **License filter.** Four groups: open font licenses (OFL, UFL, Bitstream Vera); permissive (Apache, MIT, BSD, CC0); attribution required (CC BY); and other free-use grants, which allow any use but may forbid redistributing the files (Rule 3). *(2026-09-25; replaced by **Filters (2026-09-30)**, below. The four groups remain the license classes of the data and the pipeline.)*
- **Nerd Fonts build link (TASK-2 ruling).** A catalog family with a Nerd Fonts build gets a third, nullable link, `links.nerd`, in both catalog schemas, landing before the M1 step 20 v1 freeze. One shared target: `https://github.com/ryanoasis/nerd-fonts/releases` — the repository's releases page, never a release asset or `/releases/latest`. Text: **"Nerd Fonts build"**, naming its destination per the M2 step 4 convention. It shows on the list row and in the details panel, in every view. The spike found no license obstacle: every base license and all 14 patched icon sets pass Rules 1–4 (Font Logos' "unlicensed" is The Unlicense). Findings: [docs/nerd-fonts-link.md](docs/nerd-fonts-link.md); recorded in `data/reviews/site/2026-09-28.toml`. *(2026-09-28)*
  - *Amended on 2026-09-29* (the owner's Nerd Font marker; Claude took TASK-2 over from Qwen). The field stays a nullable `links.nerd` in both schemas, before the freeze, and the placement stays the row and the details panel on every view. What changes:
    - **Which builds:** the Nerd Fonts project's builds and the makers' own NF builds (such as Maple Mono NF, Cascadia Code NF and Rec Mono).
    - **Target:** the build's own page instead of the one shared releases page. That is its folder in the Nerd Fonts repository at the current release tag, or the maker's release page for a maker-built one. The label names the build, such as "SauceCodePro Nerd Font".
    - **Marker:** a fixed-width **"NF"** beside the font's name. The Nerd Fonts logo may replace it once the maintainers agree (Backlog TASK-3).
    - **Legend:** "NF: Nerd Font version available (adds developer icons, which have their own licenses)."
    - **Filter:** a new "Nerd Font available" filter.
    - **Before launch:** Claude confirms every linked build's license check and hides the marker for any build that fails.

    Recorded in `data/reviews/site/2026-09-29.toml`.
  - *The owner's answers after the build, 2026-09-29:*
    - **README-only folders:** a Nerd Fonts folder that holds only a README stays linked.
    - **Hide switch:** the owner can hide a build's marker and link, with a reason, in `config/nerd-hidden.toml`. That was the owner's choice; Claude had recommended adding the switch only when a check fails.
    - **Broken link:** a Nerd link that fails its check is not shown (no marker, link or filter match) until it passes again. The font stays listed, and review.md flags it. This was the owner's own answer; Claude had recommended a warning only.
    - **Marker:** it stays at the end of the name's cell, in a column. *Moved later that day* to the end of the row's title cell, since the text name is hidden where a specimen shows (**Font names**, below).
- **Every qualifying font is listed.** Recorded in `data/reviews/site/2026-09-29.toml` (`more_fonts`, `font_requests`). *(2026-09-29)*
  - **The rest, A–Z (the owner's choice (b), Claude's recommendation).** Every font that passes the gates is listed, not only the catalog. The catalog keeps its ranks and bands. The other qualifying fonts (about 1,250 in the 2026-09-29 run) follow the ranked fonts in the same list, unranked and A–Z, under a heading such as "More truly free fonts". They show no rank, band or "501+", and they take the same filters, search, details, link policy and preview rules. The owned-font comparison takes owned fonts out of them too.
  - **Timing (2026-09-30).** Not part of getting the site live. The A–Z list and fonts added on request move out of Milestone 1 (step 15b) into a milestone of their own, [milestone-more-fonts.md](docs/milestone-more-fonts.md), after Milestone 2's launch and before or after Milestone 3's owned-font tools; the owner chooses which when Milestone 2 is done. Until then the site lists the ranked catalog only. Recorded in `data/reviews/site/2026-09-30.toml` (`more_fonts_timing`).
  - **Fonts added on request (the owner's variant of option (c)).** Anyone, whether a foundry, a designer or a visitor, may ask for a missing font by email to admin@trulyfreefonts.com or with the "Missing font" form. Claude checks it against Rules 1–4, D3, D4 and the link policy, and the owner rules in chat. An accepted family joins `config/foundries.toml` (amending M12) and is listed from the next refresh: ranked if its evidence places it in the catalog, otherwise A–Z. Being added never counts as popularity, and the public files never say who asked.
- **Headline font.** The site's headline is set in **League Gothic** (The League of Moveable Type, OFL 1.1). *(2026-09-29)*
  - **Wordmark.** The headline is an SVG wordmark reading "Truly Free Fonts" in mixed case, with the letters drawn as outlines, scaled to be the site's headline. The file is `site/static/wordmark.svg`, drawn by `site/static/_src/make_wordmark.py`. *(2026-09-29)*
  - **Colour.** Black on white in both light and dark mode: the header stays white when the rest of the page is dark. *(2026-09-29)*
  - **Header.** The wordmark replaces the site name and the favicon mark in the header. It is about 48 px tall on wide screens and 32 px on phones. The header is white in both themes, and its links and text keep their light-theme colours. *(2026-09-29)*
- **Interface font.** Headings and body text are set in **Arimo** (Steve Matteson, OFL 1.1), upright and italic, served from the site itself. It replaces the system font stack. Recorded in `data/reviews/site/2026-09-30.toml` (`ui_font`). *(2026-09-30)*
  - **Choice.** The owner compared pairings with League Gothic, then mockups of the site in Arimo, iA Writer Quattro and Open Sans, and kept Arimo: "We are going with Arimo for the headings and body text", then "I want to keep Arimo. I may regret it later."
  - **Files.** A Latin subset of Arimo 1.341 from google/fonts at 23e54b51, as WOFF2 with the weight axis (400 to 700): about 23 KB upright and 26 KB italic. `site/static/_src/make_ui_font.py` makes them, the build serves them from `/assets/ui/`, and `tff-site check` holds each to 30 KB. A browser fetches the italic only for a page that uses it.
  - **No shift.** Every page preloads the upright. With `font-display: optional`, a font that arrives late is never swapped in, and that page view keeps Liberation Sans or Arial, which share Arimo's widths. Monospace text keeps the system monospace stack.
- **Front-page note.** The front page carries "Why isn't my favorite free font here?" with the owner's text word for word (Claude's draft, accepted), the address a mailto link. On wide screens it is a textbook-style frame floated right beside the lead and privacy note, with the list starting below it. The owner kept this after seeing screenshots, and accepts the blank area it leaves below the short intro; Claude had suggested a wider frame. On phones it is a full-width frame folded to its heading, opened with one tap. Recorded in `data/reviews/site/2026-09-29.toml` (`why_not_listed`, `why_not_listed_layout`, `why_not_listed_gap`). *(2026-09-29)*
- **Front-page lead.** The owner's wording: "Every font here is free for any personal or commercial use, although sharing the files may be restricted. They are ranked by how many people install them and use them in their work." The page's meta description stays as it was. *(2026-09-29)*
  - *Changed on 2026-09-30*, after Rule 3 made every listed font redistributable: "Every font here is free for any personal or commercial use, and you may share the files. They are ranked by how many people install them and use them in their work." The front-page note "Why isn't my favorite free font here?" drops "or don't let you pass the font files on" and now reads "Some of these fonts ask you to credit the designer, and we mark those." Both were Claude's recommendation, taken by the owner. Recorded in `data/reviews/site/2026-09-30.toml` (`front_page_lead_sharing`).
- **Early-version line.** Every page's header says "Early version. Coming next: free font inventory tools." (the owner's wording) until Milestone 3 removes it. *(2026-09-29)*
- **Scores instead of numbers (Milestone 2).** The owner's idea, after a filtered list ran 1 to 25 and then showed bands. Recorded in `data/reviews/site/2026-09-29.toml` (`score_display`, `score_curve`, `score_held_fonts`, `score_field_timing`). *(2026-09-29)*
  - **Display.** The list shows each font's score, 0 to 100, as a blue bar where the rank number is now. Filters only hide rows: no renumbering, and no bands in the list. A font's details keep its rank and range. This replaces M2-D2 when it lands.
  - **Score.** 100·Φ(z), the normal curve of the engine's fused score for the rank (Claude's recommendation; the owner had suggested the logistic, which differs by about one point at most). It is relative standing among the fonts the sources track, not the chance that a given person chose the font.
  - **Held fonts.** The list sorts by score: a font the two-source rule holds back takes its score's place and carries a marker. The catalog's ranks and bands keep the rule. That was the owner's choice; Claude had recommended a note instead of a score.
  - **When.** `catalog-site.json` carries each rank's score from Milestone 1, before the step 20 freeze. The display is built in Milestone 2.
- **Font names.** A row with a specimen shows the family name once, drawn in the font by the specimen's first line. The text name stays in the page for screen readers, search and find-in-page, and shows whenever the specimen isn't shown. The NF marker moves to the end of the row's title cell (the specimen, or the text name where there is none), still in a column. The owner chose to hide the text name; Claude had recommended dropping the name from the specimen. Recorded in `data/reviews/site/2026-09-29.toml` (`name_once`, `nerd_marker_spot_title`). *(2026-09-29)*
- **Specimen sample line.** "Dolorem ipsum quaerit nemo." (the owner's wording: Cicero's phrase behind Lorem ipsum, with descenders on both sides), in place of the Polish and Czech line of 2026-09-26 and of "Dolor dolorosus est.", chosen earlier the same day. "quaerit" is spelt without the æ ligature, which fonts with basic Latin only lack. Recorded in `data/reviews/site/2026-09-29.toml` (`specimen_sample_latin`). *(2026-09-29)*
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

  **Sort** (by rank or by name) moves to the list header, beside the count. *Changed the same day* (`sort_header`): sorting is a row of buttons over the list, one over the rank column and one over the names. Each is a bordered button with two stacked arrows and, on the column in use, the order's words ("best first" or "least used first", "A–Z" or "Z–A"). Clicking the button in use reverses its order, and clicking the other sorts by it in its usual order. Reversing the rank is a true reverse, with the unranked fonts first: that was the owner's choice, where Claude had recommended keeping them last. The header sits over the rank numbers until the score bars replace them.

  **Rows:** the badges get shorter. "Comes with" names only the operating systems and apps, "Pulled in by" appears only in the details panel, and there are no "Monospace" or "Not redistributable" badges.

  **Old links:** links that use the retired `spacing`, `lic` or `redist` keys still open; `spacing=monospaced` becomes the Monospace category.

  **Variable fonts** are labelled "Adjustable weight (variable font)" in the filter and "Adjustable weight" on the rows, because "Variable" alone reads as "proportional" (letters of varying width). This was Claude's recommendation, taken by the owner (`variable_label`).

  The monospace and license choices were the owner's, from Claude's recommended options. The rest was Claude's proposal in the plan the owner approved. Recorded in `data/reviews/site/2026-09-30.toml` (`monospace_category`, `license_filter`, `filters_layout`). *(2026-09-30)*
- **Details panel (2026-09-30).** The owner found a font's opened details too busy, and chose essentials first with the evidence folded (Claude's recommended option). Recorded in `data/reviews/site/2026-09-30.toml` (`details_layout`). *(2026-09-30)*
  - **Order.** First the "Type your own text" button, which still loads the font only when clicked. Then one short list:
    - **Get it:** the official, designer and Nerd Font build links;
    - **License:** the license, and whether credit is needed;
    - **Font:** formats and Latin coverage;
    - **Comes with:** the systems that preinstall it;
    - **Also known as:** other names;
    - **Rank:** the rank chosen in the selector, with its likely range.

    Last comes "Report a problem".
  - **Folded.** A closed "All ranks and sources" disclosure holds every rank with tier and range, the tier legend, "Pulled in by" and the per-source tables.
  - **Dropped:** the section headings, the license-group row, the Redistributable row, the Spacing row, and the Nerd Font legend repeated from above the list.

## Infrastructure

*(2026-09-25; setup checklist and runbook in [ops/SERVER.md](ops/SERVER.md))*

- **Domains.** trulyfreefonts.com, .org and .net, registered and hosted on Cloudflare. The canonical URL is `https://trulyfreefonts.com`; `www.*`, `.org` and `.net` 301-redirect to it, keeping the path.
- **Hosting.** A Contabo VPS running Debian 13, served by Caddy, with the site root at `/srv/trulyfreefonts/public`.
- **Cloudflare.** Proxied (orange cloud), SSL mode Full (strict), with a Cloudflare Origin CA certificate on the server.
- **Access.** One admin user `byron`, key-only SSH, passwordless sudo, root login off. Local alias `ssh tff`. Claude manages the server over SSH and Cloudflare through an API token; its ssh/scp/rsync commands to `tff` are auto-allowed in this project.
- **Secrets.** Kept out of the project: the Cloudflare token lives in `~/.config/trulyfreefonts/cloudflare.env`, and the root password (VNC emergency access only) lives in the owner's password manager.
- **Visitor privacy on the server.** Cloudflare's Network Error Logging is off on all three zones. The access log masks visitor IPs to /16 (IPv4) and /32 (IPv6), drops the port and IP-carrying headers, and is kept for 14 days. *(2026-09-25)* It also drops Referer and User-Agent. *(2026-09-26)* It also drops Cookie and Cloudflare's location headers finer than the country. *(2026-09-28)*

## Repository and workflow

*(2026-09-25)*

- **Repository.** Public, at [github.com/byronshock/trulyfreefonts](https://github.com/byronshock/trulyfreefonts), default branch `main`. The methodology and rankings are open.
- **Kept out of git.**
  - `PLAN.md`: private planning notes.
  - `ops/SERVER.local.md`: origin IPs, VNC console and zone IDs; publishing the origin IP would let anyone bypass Cloudflare.
  - `.claude/settings.local.json`.
- **Tracked docs.** These are versioned:
  - `docs/ranking-methodology.md`;
  - `docs/roadmap.md`;
  - the milestone checklists `docs/milestone-1.md` to `docs/milestone-4.md`, and `docs/milestone-more-fonts.md` *(2026-09-30)*;
  - `docs/owned-fonts.md`, the notes for Milestone 3;
  - `PLAN-NERD-FONTS.md`, the working checklist for Backlog TASK-2. *(2026-09-28)*
- **Monthly data refresh.** A scheduled GitHub Actions workflow runs the pipeline and opens a pull request with the new catalog and anything flagged for review. The owner reviews and merges it.
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

- A donation button on the site, and nothing more: no donation strategy or fundraising work. By the owner's reckoning the site costs nothing except their time. *(2026-09-25)*
- **Provider.** A Stripe Payment Link where visitors choose the amount, with $5 suggested. No code and no Ko-fi. The link goes on the site with the intermediate release (the filterable list), not on the current stub. Checklist in [ops/DONATIONS.md](ops/DONATIONS.md). *(2026-09-25)*

## Current step

1. Survey the tools that already exist for this, or for parts of it. *(done 2026-09-25; findings in [docs/prior-art.md](docs/prior-art.md))*
2. Milestone 1: Step 0 is done; the methodology was approved and merged on 2026-09-25 ([docs/milestone-1.md](docs/milestone-1.md)). Step 1, project setup, has met its done-when (2026-09-25, [pull request #6](https://github.com/byronshock/trulyfreefonts/pull/6)); its layout item fills in with steps 2, 7 and 15. Steps 2 and 3 are under way. *(2026-09-25)*
3. Milestone 2: Step 0 decisions are answered and the checklist is merged ([docs/milestone-2.md](docs/milestone-2.md)). Its steps 1–12 can start on a sample catalog alongside Milestone 1. Milestones 3 and 4 keep their Step 0 until each starts ([docs/roadmap.md](docs/roadmap.md)). *(2026-09-25)*

## Background

- The earlier top-100 library is at `~/Documents/code/fonts`; its `MANIFEST.md` explains the ranking method. Its 8 Fontshare families (Satoshi, General Sans, Clash Display, Cabinet Grotesk, Switzer, Ranade, Gambetta, Chillax) are under the ITF license and do not qualify.
