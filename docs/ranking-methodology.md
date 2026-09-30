# Ranking methodology

**Status: published 2026-09-29** (Milestone 1, step 17). This is how the catalog is ranked, and the code follows it. Every number is set in [`config/ranking.toml`](../config/ranking.toml). Labels such as D4, M5 and T2 name the owner's decisions and rulings, which [AUTHORITY.md](../AUTHORITY.md) records with their dates. The rulings and their reasons are in [`data/reviews/`](../data/reviews/).

## 1. In plain words

Each month we collect public counts of how often free fonts are installed on computers and used in websites, code and apps. The sources measure different things, so we compare positions, not raw numbers: each source's order is translated onto one shared scale.

A font's score is the weighted average over the sources that could have seen it. A source that doesn't carry a font at all (a Linux distribution with no package for it) is left out, not counted as zero.

Fonts with little evidence are pulled toward the middle. No font reaches the top 100 on one kind of evidence alone.

The desktop rank comes in two versions, and both list the same fonts. **Most installed** counts every install. **Most chosen** leaves out the installs that Linux systems make on their own (fonts that come preinstalled, or that another program brings in), so those fonts are ranked on the installs people made themselves. The overall rank uses *most chosen*, blended with the *Used in projects* rank. Past #100 we show bands, because the data can't separate those fonts precisely.

**Principles:**

- Filter first, then rank: an ineligible font never takes a rank or shifts one.
- Absent is not unpopular.
- Each raw source feeds exactly one survey. Of the two desktop views, only *most chosen* feeds the overall rank; *most installed* and the extra views reuse the same data but never feed it.
- Scores are rank-based, so a spike lifts a font to the top of one source and no further.

## 2. What is ranked

**Unit:** the family as Google Fonts defines it. Roboto, Roboto Slab and Roboto Mono are three families. These fold into their parent family:

- static and variable builds ("Inter Variable");
- optical-size splits the parent already covers ("Inter Display");
- reviewed small-caps and Guides companions;
- old names (Source Sans Pro → Source Sans 3);
- patched builds (Nerd Font, NF, Powerline);
- CJK builds of a Latin family (Maple Mono NF CN → Maple Mono), at `cjk_build_credit`.

**IDs:** each family gets a stable `id` minted from its name the first time it is seen, and it never changes. A rename adds an alias and updates the display name, but keeps the `id`, so links, history and "first seen" survive.

**Siblings and bundles (D2).** Width siblings such as Barlow Condensed are families of their own. A bundle (fonts-urw-base35, the Monaspace cask) credits each of its member families with half its count, and a count that reaches a font only through bundles is flagged "bundle-only".

**Universe:** the candidate families come from:

- Google Fonts (1,946 families);
- Fontsource's non-Google fonts (120);
- the original fonts behind Nerd Fonts;
- Homebrew's non-Google font casks;
- Fontist's open-license formulas;
- a hand list of foundry families in `config/foundries.toml` (League of Moveable Type, Velvetyne, Collletttivo, Open Foundry and others). It was seeded once from the foundry sites and approved by the owner (gate C); the sites are not scraped each month (ruling M12).

Appearing in one of these lists never counts as popularity. After the gates, about 1,600 families are ranked.

### Gates, applied before ranking

**License.** A license counts as *verified* when all of these hold:

- the upstream license text (or the file in the google/fonts folder) matches an allowed ID;
- the font's name table doesn't contradict it;
- the text's hash and check date are stored.

Unknown IDs are excluded.

**License classes and previews (D3).**

- **Qualify:** CC-BY qualifies, with an "attribution" badge. An informal freeware grant qualifies only if it explicitly allows any use.
- **Excluded in v1:**
  - CC-BY-SA;
  - GPL/LGPL without the font exception;
  - URW Base 35's AGPL.
- **Rule 4 extended** to any ban on modification or embedding. That excludes:
  - the MS Core Fonts EULA;
  - CC-BY-ND;
  - fonts with no license found. Claude researches any such family that would otherwise reach the overall top 700 before it drops out (owner ruling of 2026-09-26), and a license found goes to the owner as a gate LIC ruling that names it (the first 25 were ruled on 2026-09-28). Each month the review pack lists these families with the overall order each would get if its license were found, from a rerun of the ranking on a copy of the build.
- **Previews (`preview_ok`):** a font may be previewed on our site only if it is redistributable. We serve the upstream files unchanged: no subsetting or format conversion of fonts with a Reserved Font Name. Whether conversion is fine under the OFL is *unverified*, so we avoid it.

`config/licenses.toml` lists the licenses that qualify, in the groups the site's license filter shows, and the licenses that are excluded. A license it doesn't list is excluded. The owner's rulings on particular licenses and fonts are in `data/reviews/licenses/` (gate LIC) and `data/reviews/l3/` (gate L3).

**Latin (D4).** A Google family is Latin when Google's metadata says so: its primary script is Latin or unset, and it has a `latin` subset. About 1,250 of Google's 1,946 families pass this strict test. It misses dual-script families such as Poppins (Latin and Devanagari), so those join from an allowlist the owner reviews.

The allowlist follows the owner's rule of 2026-09-26 (gate L2): a dual-script Google family with full Latin Extended is included unless it is a script companion, a script version of a family already listed whose Latin is that family's (Noto Sans Arabic of Noto Sans, Hind Siliguri of Hind); companions and families with basic Latin only stay out. Claude applied the rule family by family: on 2026-09-26, 178 families were included and 265 left out (206 companions, 59 with basic Latin only), and on 2026-09-28 the owner confirmed the companions stay out. The lists are in `data/reviews/latin/`, and the owner can move any family. A new dual-script family is asked about with the rule's advice: keep it out if its Latin is basic, include it if no other family's name starts its own, and otherwise the owner decides whether its extra words name a script or a style.

Also:

- Families that cover basic Latin only are kept, with a "limited accents" badge.
- A non-Google font qualifies if it lacks at most 2 of GF_Latin_Kernel's code points, at least 30% of its letters are Latin, and it has fewer than 1,000 CJK code points. Its coverage is "extended" when at most 3 of GF_Latin_Core's code points are missing. A letter's script is its Unicode Script property, and letters of the Common, Inherited and Unknown scripts are not counted. These are the owner's gate L1 thresholds of 2026-09-26, in `config/ranking.toml [latin]`. A font with no readable file can't be tested, so it stays out.
- A Google family served only with Google's `menu` subset (the Playwrite families and Allkin) has no metadata to judge, so it takes the same test on its files (gate L, 2026-09-26).
- Families that are mainly CJK (Sarasa, LXGW WenKai, D2Coding) are out.

**Text only.** Excluded:

- icon, emoji, symbol, barcode, math and music fonts;
- generic names (system-ui, sans-serif);
- proprietary fonts (SF Pro).

## 3. Evidence states and one scale

**Cleaning.**

- Every package, slug or name maps to a family through one versioned alias table.
- Names must match exactly after normalization, never by prefix. "Sibling" rows stop Roboto from matching Roboto Slab.
- Names that must never map to a catalog family are recorded as *ineligible*, with a reason: proprietary, ITF, CJK, icon, generic or system. These include Arial, SF Pro, Satoshi and Font Awesome. Three more reasons cover package and name keys no family fits: not a font (a program or library, such as a font viewer), non-Latin (a font for another script, such as Thai or Devanagari), and unlisted (a Latin font that no catalog source lists).
- A family's packages are summed: static and variable npm packages, ttf and otf, renamed casks.
- Counts become rates over min(window, days available). Exposure is counted from each source's *data* date; for example, the Almanac 2025 crawl ran in July 2025.

**Four evidence states** for each font/source pair:

| State | When | Effect |
|---|---|---|
| Observed | at or above the source's noise floor | normal term |
| Censored | the source could have shown the font (it has a package, or it is a crawl or top-N list whose frame includes the font), but the value is below the floor or missing | a term at the censored value (below) |
| Not covered | no package; outside a list's frame (a non-Google font on a Google-only list); merged away by the Almanac's name regex; a Linux source abstaining because a Linux system preinstalls the font or another package pulls it in (in every rank except *most installed*) | no term |
| Too new | under 60 days on that channel | no term; "New" badge |

**One ruler.** Every source is measured against one ruler: the Homebrew font casks. They cover 1,940 of the 1,946 Google families and most non-Google fonts. The ruler is built after the gates and alias-summing, with the same Nerd, bundle and CJK credits as the Homebrew source. Homebrew's noise floor applies only to Homebrew's own term. On the ruler, it would tie hundreds of tail casks.

**Formulas.**

- Ruler: z_R(f) = Φ⁻¹((L+U)/2n), the mid-rank percentile among eligible ruler families.
- Source s with overlap O_s (the families both the ruler and s carry): let p be the mid-rank percentile of x_s(f) among s's values on O_s. Then z_s(f) is the p-quantile of {z_R(g) : g ∈ O_s}.
- Censored value: z_s(f) is the same mapping applied to the mid-percentile of the censored block within the source's frame.

**What this design achieves.**

- A source that packages only popular fonts can't label its least-installed font unpopular. For example, the typical font Debian packages sits at Homebrew's 87th percentile.
- For sources that see nearly every family (Fonts Over Time, the Almanac, Google, npm), this reduces to a plain rank-to-normal-score conversion. Homebrew's developer skew therefore barely reaches the project rank.
- Small overlaps are noisy. A source's weight is multiplied by min(1, |O_s|/50), and the source is switched off below 15.

**The ruler (D5)** is Homebrew. Each month the ranking is also rerun with Fontsource (its jsDelivr counts) as the ruler, as a check (§9).

**Why not the Top 100's reciprocal-rank fusion (RRF, k=20)?**

- RRF scores a missing entry as 0, the same as disinterest, and k sets the size of that penalty. Monaspace lands at #10, #27, #60 or #199 for k = 1, 20, 60 or 200.
- Averaging log counts fails the other way: fonts seen only by Homebrew, mostly coding fonts, reached #4–#6.
- With only 2–4 lists, Borda, Robust Rank Aggregation, Markov-chain and Kemeny methods either ignore weights or produce cycles.

**Method (D1).** Equated normal scores plus shrinkage, as this document describes. Coverage-aware RRF (k=60, averaged over only the sources that cover the font, plus a prior) runs each month as a cross-check.

## 4. Fusion

For survey g with source weights w_s, let W_g = Σ w_s over the survey. The sum below runs over observed and censored terms:

S_g(f) = (κ·W_g·μ0 + Σ w_s·z_s(f)) / (κ·W_g + Σ w_s), with κ = 0.2 and μ0 = 0 (the median eligible font).

A font covered by every source keeps 83% of its signal; one covered by a third of the weight keeps 63%.

**Outlier guard:** when a font has at least 3 terms and one source sits more than 1.5 z from the median of all the font's terms, that source gets half weight for that font. All terms are judged against the unguarded values at once, and the event is logged (owner ruling of 2026-09-26, amending M1).

**Overall:** the same formula over all project terms and the *most chosen* desktop terms: a Linux source that abstains for a font in *most chosen* abstains in overall too. Each source is weighted v_s = M_g·w_s / W_g, where M_g is survey g's share of the overall mix (D12), and shrunk once (with Σ M_g in place of W_g). W_g uses the effective weights (after phase-in, overlap scaling, stale drops and switched-off sources), and the overall rank reuses each survey's guard factors rather than running the guard again. A desktop-only font gets censored terms from the web crawls, not a placeholder: the crawls' frame is every eligible font.

**Worked example** (Homebrew 1.0, Arch 0.75, Debian 0.25; κ·W = 0.4; live values from 2026-09-25):

| Font | z (Homebrew, Arch, Debian) | Score |
|---|---|---|
| JetBrains Mono | 3.54, 2.70, 1.59 | 2.48 |
| Inter | 2.99, 2.29, 1.77 | 2.15 |
| Monaspace | 2.92, 1.77, no Debian package | 1.98 |

For JetBrains Mono the outlier guard does not fire: the median of its terms is Arch's 2.70, and Debian's 1.59 sits 1.11 z below it, within the 1.5 gap.

The missing Debian package costs Monaspace only a slightly stronger pull toward the middle: it keeps 81% of its signal instead of 83%. RRF would have scored it as if Debian users had rejected it.

**Display (D6).** Exact ranks 1–100 with a confidence tier, then the bands 101–250 and 251–500, and "501+" for a catalog font placed past 500. Each font also shows its rank in every source. The site data carries an internal sort order, so lists can still be sorted within a band.

## 5. The rankings

### Desktop: two views

Both views use the sources below. They differ only in how fonts that Linux systems install automatically are counted:

- **Most chosen**: fonts people deliberately install. A Linux source leaves out a font that a Linux system preinstalls, or that one other package pulls in for at least half of its installs; the font is still ranked on its other sources. **This view feeds the overall rank.**
- **Most installed**: fonts on the most computers. Every install counts as it is, automatic or not. Published as its own view; it does not affect the overall rank.

In both views, affected fonts carry a tag naming the systems they come with or the packages that pull them in.


| Source | Weight | Handling |
|---|---|---|
| Homebrew cask installs, 365 days (macOS) | 1.0 | subtract a bulk-install floor of 20 a year from each cask; Nerd casks instead get their own floor each run, the 10th percentile of Nerd casks' 365-day installs (about 2,150 a year), subtracted before `nerd_credit` (ruling M3); a family under 60 a year after the floors is censored |
| Arch pkgstats, share of systems | 0.75 | mean of the monthly shares over 12 complete months; subtract the Nerd Fonts group floor: the 10th-percentile share of members that have been in the group at least 6 months, with newer members not floored (ruling M4); under 0.3% is censored |
| GitHub release downloads (only repos that are the main download channel) | 0.5 | change between snapshots (see below) |
| Nerd Fonts release downloads, credited to the original font | 0.3 | subtract the 10th-percentile floor; Symbols Only is dropped |
| ~~Chocolatey download count (Windows)~~ | ~~0.3~~ | not used in v1: its terms forbid automated access and republishing (ruling T3) |
| Debian popcon installs | 0.25 | under 100 is censored |

**Notes on the sources.**

- **Arch** uses the mean of monthly shares because its sample grew from 14,465 to 32,749 systems a month between Sep 2025 and Aug 2026; pooling the months would overweight recent ones.
- **GitHub** reports lifetime totals, so we use differences between snapshots:
  - first run: lifetime total ÷ days listed;
  - months 2–11: (latest − earliest snapshot) ÷ days between them;
  - from month 12: the 12-month difference.

  An asset present in both snapshots counts its growth, and an asset created after the earlier snapshot counts its whole count, since its baseline is 0 (owner ruling of 2026-09-26); an older asset the earlier snapshot lacks is ignored. Negative differences (deleted or re-uploaded assets) are clamped to 0 and flagged. Every release of a main-channel repo is counted, with no cap by date, because old releases still dominate: FiraCode's latest release is from 2021 and JetBrains Mono's from 2023. Iosevka, with over 400 releases, is the exception: only its latest 24 releases are fetched, through GitHub's GraphQL API (ruling M2). Prereleases don't count, except in a repository that publishes nothing else (OpenDyslexic: `prerelease_repos`, owner ruling of 2026-09-26).
- **Linux dependency correction (every rank except *most installed*).** In *most chosen*, the overall rank and the Coding view, a Linux source abstains for a font when the font's top reverse Depends/Recommends accounts for at least 50% of its installs (a virtual package, named only in Provides, never pulls a font in, even with one provider: owner ruling of 2026-09-26), or when the owner's preinstalled list names the font for a Linux system whose installs that source counts: Arch-family systems (CachyOS, EndeavourOS) silence Arch pkgstats only, Debian and Ubuntu silence popcon only, and desktops (GNOME, KDE Plasma) silence both (owner ruling of 2026-09-26). Windows, macOS and Android entries on that list only add `preinstalled_on` tags and never cause an abstention. A Linux source counts a family by its most-installed package, not the sum of its packages, since one system installs each package once (owner ruling of 2026-09-26); the dependency share uses the same basis. The test uses the largest single dependent, not the sum of all dependents. When a package depends on alternatives (`a | b`), only the first alternative counts as pulled in. A largest dependent at 35–50% is flagged for the owner's review (ruling M8). Dependencies are parsed from:
  - Arch core/extra;
  - the CachyOS and EndeavourOS repository databases (cachyos-kde-settings, on 3.57% of Arch systems in Aug 2026, requires ttf-fantasque-nerd, ttf-fira-sans and noto-fonts);
  - Debian's Packages.xz.

  Homebrew installs are explicit, so they count as they are everywhere. The *most installed* view skips this correction for every source.
- **Fonts left without desktop evidence.** If abstentions leave a font no observed desktop term, it stays listed in *most chosen* as "no evidence of deliberate installs", with its tag, and keeps its *most installed* rank and its overall rank. It is never removed from the catalog for this reason.
- **Resolution.** The data separates roughly 200–300 families.

**Nerd and CJK credit (D7).** A patched build's installs count in full toward the original font: `nerd_credit` 1.0 and `cjk_build_credit` 1.0 (Maple Mono NF CN and similar). About 59% of Homebrew font installs are Nerd casks, and Meslo LG ranks high because Powerlevel10k recommends it, so coding fonts rank higher under this choice. If the two credits ever differ, a build that is both NF and CN gets the smaller.

**Preinstalled and dependency-pulled fonts (D8).** These fonts stay in every rank; only how their Linux installs are counted changes. Two desktop views are published, *most chosen* and *most installed*, as described at the top of this section. *Most chosen* feeds the overall rank. The owner reviews new preinstalled and dependency entries when the script flags them.

**Desktop weights (D9)** are as in the table. Noise estimates suggesting Arch deserves 1.0 came from only 45 families, before correction.

### Project: fonts used in sites, code and apps

| Group | Source (what it measures) | Weight |
|---|---|---|
| Web 55% | Fonts Over Time (FOT): distinct homepages (about 10k) using the font for body text, headings or at least 5% of the text | 0.25 (0.10 while phasing in) |
| | Web Almanac 2025 sheets: pages declaring the font | 0.15 |
| | Google Fonts views, 1 year: traffic Google serves | 0.15 |
| Code 30% | npm downloads of @fontsource and @fontsource-variable, 1 year | 0.15 |
| | ecosyste.ms dependent repositories: GitHub projects declaring an @fontsource or @fontsource-variable package | 0.10 |
| | jsDelivr hits (Fontsource stats) | 0.05 |
| Apps 15% | npm downloads of @expo-google-fonts | 0.10 |
| | Flutter `GoogleFonts.<name>` code search (off in the first build) | 0.05 |

**Group shares (fixed).** The web, code and apps groups always carry 55%, 30% and 15% of the project weight. Within a group, the sources split the group's share in proportion to their effective weights, after phase-in, overlap scaling, stale drops and any source switched off. So while FOT phases in at 0.10, the web group's 0.55 is split about 0.14 (FOT), 0.21 (Almanac) and 0.21 (Google); with Flutter off, Expo carries the apps group's whole 0.15 (ruling M9).

**Notes on the sources.**

- **Fonts Over Time.**
  - It is new: the crawl started in September 2026, and its repository has no license file. It runs at weight 0.10 until it has 8 weekly snapshots, then 0.25.
  - Each run checks FOT's file columns. On a mismatch, FOT is marked stale rather than failing the run.
  - Counted rows: browser and static only; weekly snapshots averaged over the month; under 3 sites is censored; ITF fonts and generic names dropped.
  - Startups make up 5,231 of its 10,465 sites, so they are capped at 25% of the weight, for an effective sample of about 8,400 sites (D11).
- **Almanac.**
  - The term comes from the pages tab (pages declaring the font; gid 1668708562).
  - Families it doesn't list are censored.
  - Its name regex folds Condensed, Narrow and Black cuts into the parent. Those cuts count as not covered, and the parent's term is flagged and its weight halved (its value is unchanged). The requests-by-service tab (gid 1594814478; top 100 per service) is used only to flag these parent merges and adds no term of its own (ruling M6).
  - A new edition arrives yearly. The sheet id and tabs are set in config and switched in one step, flagged in that month's pull request.
- **Google.** Google is counted once. The metadata `popularity` field is dropped, except as a fallback, because it tracks 7-day views almost exactly (Spearman 0.998). The Top 100 counted Google twice. Google's view counts are used only to rank fonts and are never published (ruling T2).
- **npm.**
  - Each package's daily downloads come from one `api.npmjs.org/downloads/range` request over the last 540 days, at one request a second. The term is the last-year total.
  - Legacy ids are folded (source-sans-pro → source-sans-3).
  - Floors: npm 1,000 a month and jsDelivr 10,000 a month; values below are censored.
- **ecosyste.ms** dependent-repository counts (projects that declare each package, so CI re-downloads can't inflate them) are used at 0.10. Only the @fontsource and @fontsource-variable packages are counted, not Expo's (ruling M7). Its data is CC BY-SA 4.0, the same license as our catalog data (D17). Floor: 5 dependents; values below are censored.

**Project scope (D10).** Websites, code and apps, including ecosyste.ms dependent repositories at 0.10 in the code group.

Print stays at 0, because the terms of Fonts In Use forbid robots.

**Fonts Over Time (D11, ruling T4).** It is used under its "free to download and reuse" wording, at the phase-in weight (0.10 until 8 weekly snapshots), with credit and a link back. Only ranks and their rank-based z scores are published, never its raw values, and test fixtures are synthetic. The owner asked its author for an explicit data license on 2026-09-25 ([fcjr/fontsovertime#1](https://github.com/fcjr/fontsovertime/issues/1)).

### Overall

**Overall mix (D12).** Desktop (*most chosen*) 0.5 and project 0.5, measuring usage only; the owner kept this mix at the gate R review of 2026-09-29. Designer picks are not used.

### Extra views

These never feed the overall rank. The owner chose all four (D13).

- **Coding fonts.** Developers are a real audience and already dominate the desktop data.
  - Covers monospace families.
  - Weights: Nerd release downloads 1.0, Homebrew 1.0, Arch 0.75, GitHub 0.5, Fontsource npm 0.5. Arch uses the *most chosen* abstentions.
  - On the site, a spacing filter (Any / Proportional / Monospaced) works on every rank. *Proportional* hides monospace and coding fonts (owner ruling, 2026-09-25).
- **Developers & apps.** Separates what builders choose from web traffic. Its weights are the project weights, rescaled: Fontsource npm 0.15, ecosyste.ms dependents 0.10, Expo 0.10, and Flutter 0.05 when it is on (ruling M10). While Flutter is off, all its sources share one independence group, so no font can pass the two-group gate: the view is published as bands only, labelled as such, until a second independent source is on (owner ruling of 2026-09-26).
- **By category.** The overall scores filtered by sans, serif, display, handwriting and mono. A font's category comes from Google Fonts' metadata, then Fontsource, then the font's own tables. The owner's category in `config/category-overrides.toml` comes before all of them (owner ruling of 2026-09-29, gate R).
- **Rising (beta).** Feeds "New popular free fonts this month".
  - Per source, the log-ratio of recent share to 12-month *share*, not counts: npm keeps growing overall, and Homebrew's 30-day total fell to about 20% of its 90-day total.
  - A font rises only when at least 2 sources agree, its share is at least 0.02%, and the rise holds over 3 months of smoothing.
  - Fonts under 3 months old go to "New" instead.
  - Google alone can't make a font rise: Andada Pro was up 925% in 90 days. A font's rise is its second-largest rise among its sources, so no single source sets it.
  - Every Rising font shows tier C while the view is in beta: Rising has no weights, so its confidence ranges are nearly all single points and would otherwise read as tier A (owner ruling of 2026-09-26).
  - The view appears from the third monthly refresh, once three months of shares exist.

**Deferred:**

- dotfile parsing for Coding fonts;
- an opt-in visitor survey, which would break "font lists never leave the browser";
- HTTP Archive through BigQuery. A sandbox query, dry-run first, could later replace the Almanac sheets.

## 6. Evidence, ties, stability, confidence, state

**Evidence.**

- *Ranked at all:* at least 1 observed term in that survey.
- *Top 100 of any rank:* observed terms from at least 2 independence groups. The groups are:
  - Google serving;
  - FOT;
  - HTTP Archive;
  - the npm registry (npm, ecosyste.ms dependents, Expo);
  - jsDelivr;
  - Homebrew;
  - Arch;
  - Debian;
  - GitHub counters (releases);
  - Nerd Fonts;
  - Flutter.

  GitHub release counts and Homebrew count as one group for a font whose Homebrew cask downloads that repository's release asset, because GitHub's counts then include the cask's installs (ruling M5). Nerd Fonts stays a group of its own even when a font's Nerd cask downloads the Nerd release, because Homebrew's Nerd casks are a small share of Nerd's downloads: for Hack, about 62,000 Homebrew installs a year against 2.3 million Nerd downloads (owner ruling of 2026-09-29).

  A font that fails the gate sits at 101 or below and is flagged. In a crude check, shrinkage alone left Homebrew-only fonts in the top 15.

**Evidence gate (D14).** The 2-group gate plus κ 0.2.

**Ties.** Mid-ranks within a source. The final sort is by score, then evidence weight, then ruler z, then name, so runs are deterministic.

**Stability.**

- Windows are 12 months long.
- FOT is smoothed with an exponentially weighted moving average (λ 0.5) of its ruler-equated z. Each month the smoothed values are ranked again and mapped back onto the ruler, like any other source (owner ruling of 2026-09-26).
- The catalog is the overall top 500 plus the top 100 of the project rank and of both desktop views (`desktop_chosen`, `desktop_installed`).
- A font enters at rank 450 or better and leaves after 2 runs below 550. This also caps the monthly license review.
- Top-100 membership has its own hysteresis: a font enters a top 100 at 90 or better and leaves after 2 runs worse than 110 (ruling M11).

**Confidence.** A 5–95% rank range from 200 Dirichlet weight perturbations plus leave-one-source-out runs. Each draw re-weights a survey's sources with Dirichlet(20·w/W), across all its sources, so the project group shares vary too (owner ruling of 2026-09-26); the overall mix M_g is not perturbed. The leave-one-source-out run that drops Homebrew keeps Homebrew as the ruler, so only its term is removed and every other source keeps its scale (owner ruling of 2026-09-26).

- Tier A: at least 2 groups, and a range no wider than max(10, 0.3·rank).
- Tier B: a range no wider than the rank.
- Tier C: everything else.

**Outages.** If a source fails, reuse its last snapshot for up to 2 months, flagged "stale", then drop the source.

**Run state.** Some things carry over between monthly runs:

- membership counters;
- first-seen and exposure dates;
- license text hashes;
- stale-source counters;
- last month's published ranks;
- snapshot baselines for the GitHub differences;
- owner rulings.

They live in committed files under `state/` on the main branch.

- State advances only when a refresh pull request is merged.
- Each run reads state from main plus the snapshot store.
- A new run replaces a refresh pull request that was never merged, so a skipped month doesn't corrupt the next one.

**Snapshot storage (D15).** We keep monthly snapshots from the first run, because the GitHub differences and Rising need history. Only font-relevant extracts plus a manifest (url, sha256, fetched_at) are kept; big raw files (about 55 MB a month) expire after the run. They live in a private data repository that the GitHub Action reads and writes with a deploy key (ruling CI1), not a personal token that expires.

**Rejected sources (D16).** The research rejected these sources:

- Google trending;
- Fontshare views (only for ITF fonts);
- AUR votes;
- Ubuntu popcon (discontinued);
- Wikipedia pageviews;
- Google Trends;
- social media counts;
- GitHub stars;
- Fonts In Use (robots forbidden);
- code-search counts for web usage;
- LLM-compiled lists.

**Licenses for this repository (D17).**

- **Code:** MIT.
- **Catalog data:** CC BY-SA 4.0, final since 2026-09-25 (ruling T5). What the Step 3 terms audit found, and how the owner ruled:
  - ecosyste.ms data is CC BY-SA 4.0, compatible with ours (see D10);
  - the open sources may be republished with credit, raw counts included (ruling T1);
  - Fonts Over Time has no license file, so only its ranks and z scores are published while its author is asked for one (D11, ruling T4);
  - Google's metadata endpoints are undocumented, so only ranks and z scores are published, never view counts (ruling T2);
  - Chocolatey's terms forbid automated access, so it is not used in v1 (ruling T3).

## 7. Per font in catalog.json

- **Identity:** `id` (stable; see §2), `family`, `category`, `is_monospace`, `superfamily_id`, `aliases[]`, `related[]` (for example, Adwaita Sans is "derived from Inter").
- **License:** `license` {spdx, class, redistributable, attribution_required, text_url, verified_level, text_sha256, checked_on}, plus `font_file` {url, sha256}: the font file the license check read, which previews are built from.
- **Other fields:**
  - `latin` {basis, coverage};
  - `formats` {variable, static};
  - `preview_ok`;
  - `preinstalled_on[]`, `pulled_in_by[]`;
  - `links` {primary, designer, nerd};
  - `first_seen`;
  - `flags[]`.
- **Download links:** the primary link is where to get the font. For a Google family it is the Google Fonts specimen page. For any other font it is the designer's homepage or the repository's releases page, never a file download, `/releases/latest` or an aggregator (open-foundry.com counts as one). A link is accepted automatically only when two sources agree, or when the owner-approved foundry list gives it; otherwise the owner picks it or approves an override (gate K, `config/link-overrides.toml`). An `http://` homepage is upgraded to `https://`. The Wayback Machine counts as an aggregator too, except that an override the owner approves may link a dated capture of the designer's page when that page is gone; such a link is labelled as archived and says why (owner ruling of 2026-09-29). Every link is checked at each monthly refresh, and it passes only when it answers HTTP 200.
- **Nerd Font build link:** a font with a Nerd Font build also links that build's own page, labelled with the build's name (owner rulings of 2026-09-28 and 2026-09-29). A build its maker publishes (Maple Mono NF, Cascadia Code NF) links the maker's release page, and it comes first, since it is the official source. Otherwise the Nerd Fonts project's build links its folder in the Nerd Fonts repository at the current release tag ("SauceCodePro Nerd Font"), and only when `fonts.json` gives the original a license that qualifies. Nerd Fonts' page for its own build is not an aggregator for this link, but it is never a font's primary link. The site marks these fonts "NF". The link is checked monthly like the others.
- **Per rank or view:** {rank or band, order, tier, range, score, groups}. The rank keys (`overall`, `desktop_chosen`, `desktop_installed`, `project`, `coding`, `dev_apps`, `rising`) are versioned schema constants.
- **Held-back fonts:** a catalog font with no accepted download link yet (the owner decides the link at gate K) is held back from `catalog.json`, and the published exact ranks close up over it: in each view, every font ranked below it moves up one place, with its range. Places past the exact top keep their order. The monthly review names the held-back font and its places (owner ruling of 2026-09-28).
- **Per source:** {state, rank_in_source, z, weight_used}. Raw values appear only where the source's terms allow; never for Google or Fonts Over Time, whose entries carry no raw values (rulings T2, T4).
- **Top level:** run date, method version, `ranking.toml` hash, fetch times.

A trimmed `catalog-site.json` feeds the filterable list (Milestone 2), and `names.json`, the names and aliases of every eligible family, feeds the owned-font matching (Milestone 3).

## 8. Parameters (`config/ranking.toml`)

Source weights and fixed floors are in the §5 tables.

| Key | Value |
|---|---|
| kappa, mu0 | 0.2, 0 |
| ruler overlap: full weight / off | 50 / 15 |
| min_exposure_days | 60, counted from the data date |
| nerd_credit, cjk_build_credit, bundle_credit | 1.0, 1.0, 0.5 |
| homebrew_nerd_floor | the 10th percentile of Nerd casks' 365-day installs, each run (about 2,150 a year), subtracted before `nerd_credit` |
| arch_nerd_group_floor | the 10th-percentile share of members in the group at least 6 months; newer members not floored |
| dependency_abstain (every rank except *most installed*) | 0.5; 0.35–0.5 flagged for review |
| almanac_parent_merge_factor | 0.5 |
| fot_phase_in | weight 0.10 until 8 weekly snapshots |
| project_group_shares | web 0.55, code 0.30, apps 0.15, fixed; split pro rata within each group |
| outlier guard | gap 1.5 z from the median of the font's terms, at least 3 terms, weight × 0.5 |
| top100_min_groups | 2 |
| catalog | 500; enter at 450; leave below 550 for 2 runs |
| top100_membership | enter at 90; leave after 2 runs worse than 110 |
| uncertainty | 200 runs, Dirichlet 20·w, 5–95% |
| rising | 0.02% share, 2 sources, 3 months, "New" under 90 days |
| stale_max_months | 2 |

## 9. Validation, every run

**Hard failures block the pull request:**

- an ineligible font (ITF, icon, mainly CJK, unverified license) is ranked;
- a flagged license is missing from the review queue;
- the schema check fails;
- a rerun from the same snapshots gives different output;
- adding an ineligible font moves any rank;
- a known answer fails:
  - Source Sans Pro → Source Sans 3;
  - Sauce Code Pro → Source Code Pro;
  - Roboto Slab gets no Roboto counts;
  - a renamed family keeps its `id`;
- a higher count lowers a rank, except when the outlier guard changes for that font or for a font that overtakes it, or through the ruler's scale;
- the two desktop views differ for any reason other than Linux abstentions;
- changing a Linux source's count for a font that abstains in *most chosen* changes the overall rank.

**Flags in the pull request, not blocking:**

- top-500 rank-biased overlap (RBO, p 0.98) with last month below 0.9;
- a source's month-on-month Spearman correlation below 0.9;
- a coverage change above 10%;
- stale sources and schema mismatches;
- ruler overlaps and outlier-guard hits;
- one-source share jumps above 3×;
- top-100 entries that came from below 250;
- a font moving more than 30% under the coverage-aware RRF or Fontsource-ruler cross-checks;
- fonts in the top 50 of *most chosen* or project but below 300 in the other;
- a what-if table with each weight halved and doubled;
- snapshot size growth;
- exact ranks missing from `catalog.json` (a font outside the catalog, such as a Coding font), and the fonts held back for want of a download link, with their places (owner ruling of 2026-09-28).

**First run:**

- Compare with the old Top 100 only on families eligible in both and not dropped by its installed-fonts filter. That list left out Roboto, Open Sans, JetBrains Mono, Fira Code and other fonts already installed on the owner's machine. Its 5 ranked ITF fonts no longer qualify.
- Report the Spearman correlation and RBO, and explain every move of more than 30 places. There is no pass mark.
- Beforehand, backtest the month-to-month churn on historical windows so the alert thresholds mean something. Windows: pkgstats series, 18 months of npm, Homebrew 30/90/365 days, Google's windows.

## 10. Manual work beyond monthly review

The monthly pull request asks the owner only to review what it flags: new aliases and unmatched names, licenses and failed license checks, preinstalled and dependency entries, new dual-script families, and download links. The other manual tasks are:

- **Yearly:** switch the Almanac to the new edition (sheet id and tabs in config).
- **Once:** ask the author of Fonts Over Time for an explicit data license (asked on 2026-09-25: [fcjr/fontsovertime#1](https://github.com/fcjr/fontsovertime/issues/1)).

## 11. Known biases

- **Developer skew.** The desktop data comes from developers who leave telemetry on. About 59% of Homebrew font installs are Nerd builds. The 10 biggest plain casks come from 8 families, and 7 of those are coding fonts.
- **Thin platform coverage.** There is no Windows source in v1: Chocolatey's terms forbid automated access, so it was dropped. Windows users, and designers who download zips, show up only through GitHub counters.
- **Defaults.** Installs driven by tool defaults count in both desktop views (Meslo via Powerlevel10k). Installs that Linux systems make automatically count fully in *most installed*. In *most chosen* they are left out, and those fonts are ranked on their other sources. The preinstalled list is kept by hand, and derivative distributions we don't parse leak through into *most chosen*.
- **Web data.**
  - Fonts Over Time crawls US homepages only, and half of them are startups.
  - The Almanac is yearly, covers only the top 100, and folds width cuts into their parents.
  - Google's views are weighted by traffic and can't see self-hosted fonts.
  - CI runs inflate npm.
- **The ruler.** Homebrew is the ruler, so it decides how popular each source's fonts count as a group. As a check, each run also ranks the fonts with Fontsource as the ruler.
- **Not measured.** Print use is unmeasured, and the site itself will nudge installs.
