# Ranking methodology

**Status: revised 2026-10-20 for the scoring-method port** (first published 2026-09-29, Milestone 1 step 17). Two methods now run side by side: a measurement model scores the *Projects* and *Developers & apps* ranks, and the engine, the earlier method with interim desktop rules, scores the desktop views, *Coding* and *Rising*. The overall rank retired, and every view shows a new linear score. Each rank records its own method version; at the switch every rank took 2026-10-20. Revised 2026-10-23: GitHub and Nerd Fonts count toward the two-group gate while they stay out of every score (§6), and the desktop views and *Coding* took method version 2026-10-23. Catalogs built before the switch (method version 2026-09-29) followed the earlier text, which the repository's history keeps. Every hand-set number is in [`config/ranking.toml`](../config/ranking.toml); the model's fitted parameters are never published (§3). Labels such as D4, M5, T2 and q14 name the owner's decisions and rulings, which [AUTHORITY.md](../AUTHORITY.md) records with their dates. The rulings and their reasons are in [`data/reviews/`](../data/reviews/).

## 1. In plain words

Each month we collect public counts of how often free fonts are installed on computers and used in websites, code and apps. Two methods turn those counts into ranks, and each rank uses one of them.

The *Projects* and *Developers & apps* ranks use a measurement model. Each font has one underlying popularity, and each source's count is a noisy signal of it. The model learns from the data how noisy each source is, and how much every package collects anyway, however popular its font: mirrors, bots and build servers fetch every package. So a low count is weak evidence of a less popular font, not a zero. A font's score is the model's best estimate of its popularity from all its sources.

The desktop ranks, *Coding* and *Rising* still use our earlier method, the engine. The sources measure different things, so it compares positions, not raw numbers: each source's order is translated onto one shared scale, and a font's score is the weighted average over the sources that could have seen it. Desktop moves to a model of its own later.

In both methods, a source that doesn't carry a font at all (a Linux distribution with no package for it) is left out, not counted as zero. Fonts with little evidence are pulled toward the average tracked font. No font reaches the top 100 on one kind of evidence alone.

The desktop rank comes in two versions, and both list the same fonts. **Installed (desktop)** counts every install. **Chosen (desktop)** leaves out the installs that Linux systems make on their own (fonts that come preinstalled, or that another program brings in), so those fonts are ranked on the installs people made themselves. No rank blends the desktop views with *Projects* any more: the overall rank retired, and the list opens on *Projects* (owner ruling of 2026-09-30).

The ranked catalog is the *Projects* top 500, plus each rank's top 100 (owner ruling of 2026-10-01, q13). Every other font that passes the license and Latin checks will be listed too, after the ranked ones, A–Z and without a rank, because past about #500 the evidence is too thin to put fonts in a useful order (owner ruling of 2026-09-29). That list comes after the site's launch (owner ruling of 2026-09-30).

**Principles:**

- Filter first, then rank: an ineligible font never takes a rank or shifts one.
- Absent is not unpopular.
- Each raw source belongs to one survey, desktop or projects. The extra views reuse the same data.
- In the engine, scores are rank-based, so a spike lifts a font to the top of one source and no further.
- In the model, each source counts for as much as its measured noise allows, and a count that a font's other sources predict badly is flagged for review.

## 2. What is ranked

**Unit:** the family as Google Fonts defines it. Roboto, Roboto Slab and Roboto Mono are three families. These fold into their parent family:

- static and variable builds ("Inter Variable");
- optical-size splits the parent already covers ("Inter Display");
- reviewed small-caps and Guides companions;
- old names (Source Sans Pro → Source Sans 3);
- patched builds (Nerd Font, NF, Powerline);
- CJK builds of a Latin family (Maple Mono NF CN → Maple Mono), at `cjk_build_credit`.

**IDs:** each family gets a stable `id` minted from its name the first time it is seen, and it never changes. A rename adds an alias and updates the display name, but keeps the `id`, so links, history and "first seen" survive.

**Siblings and bundles (D2).** Width siblings such as Barlow Condensed are families of their own. A bundle (fonts-urw-base35, the Monaspace cask) credits each of its member families with half its count, in every source and in both methods (owner ruling of 2026-09-30, DE4), and a count that reaches a font only through bundles is flagged "bundle-only".

**Universe:** the candidate families come from:

- Google Fonts (1,946 families);
- Fontsource's non-Google fonts (120);
- the original fonts behind Nerd Fonts;
- Homebrew's non-Google font casks;
- Fontist's open-license formulas;
- a hand list of foundry families in `config/foundries.toml` (League of Moveable Type, Velvetyne, Collletttivo, Open Foundry and others). It was seeded once from the foundry sites and approved by the owner (gate C); the sites are not scraped each month (ruling M12). Since 2026-09-29 a family also joins it when someone asks for it (a foundry, a designer or a visitor), it passes the gates and the owner accepts it (§10; owner ruling of 2026-09-29).

Appearing in one of these lists never counts as popularity. After the gates, about 1,600 families are ranked.

### Gates, applied before ranking

**License.** A license counts as *verified* when all of these hold:

- the upstream license text (or the file in the google/fonts folder) matches an allowed ID;
- the font's name table doesn't contradict it;
- the text's hash and check date are stored.

Unknown IDs are excluded.

**License classes and previews (D3).**

- **Qualify:** CC-BY qualifies, with an "attribution" badge. An informal freeware grant qualifies only if it explicitly allows any use.
- **Redistribution (Rule 3, owner ruling of 2026-09-30):** a license must also let anyone pass the font files on. A grant that allows any use but forbids redistribution is excluded: in the 2026-09-29 run, the 14 families under fontopo's free license, none of them in the catalog. Before that ruling such fonts qualified and the site offered a "Redistributable fonts only" option.
- **Excluded in v1:**
  - CC-BY-SA;
  - GPL/LGPL without the font exception;
  - URW Base 35's AGPL.
- **Rule 4 extended** to any ban on modification or embedding. That excludes:
  - the MS Core Fonts EULA;
  - CC-BY-ND;
  - fonts with no license found. Claude researches any such family that would otherwise reach the *Projects* top 700 before it drops out (owner rulings of 2026-09-26 and 2026-10-01; the overall top 700 until Overall retired), and a license found goes to the owner as a gate LIC ruling that names it (the first 25 were ruled on 2026-09-28). Each month the review pack lists these families with the *Projects* order each would get if its license were found, from a rerun of the ranking on a copy of the build.
- **Previews (`preview_ok`):** a font may be previewed on our site only if it is redistributable. We serve the upstream files unchanged: no subsetting or format conversion of fonts with a Reserved Font Name. Whether conversion is fine under the OFL is *unverified*, so we avoid it.

`config/licenses.toml` lists the licenses that qualify, in the license classes each font's details name, and the licenses that are excluded. A license it doesn't list is excluded. The owner's rulings on particular licenses and fonts are in `data/reviews/licenses/` (gate LIC) and `data/reviews/l3/` (gate L3).

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

**Two methods, side by side (owner rulings of 2026-10-01, q1 and q15).** Each rank is scored by one method until desktop's model is designed:

| Rank | Method |
|---|---|
| *Projects*, *Developers & apps* | the measurement model (below, and §4) |
| *Chosen (desktop)*, *Installed (desktop)*, *Coding*, *Rising* | the engine, with interim desktop rules (§5) |
| Overall | no score of its own: it repeats *Projects* (§5) |

**Cleaning (both methods).**

- Every package, slug or name maps to a family through one versioned alias table.
- Names must match exactly after normalization, never by prefix. "Sibling" rows stop Roboto from matching Roboto Slab.
- Names that must never map to a catalog family are recorded as *ineligible*, with a reason: proprietary, ITF, CJK, icon, generic or system. These include Arial, SF Pro, Satoshi and Font Awesome. Three more reasons cover package and name keys no family fits: not a font (a program or library, such as a font viewer), non-Latin (a font for another script, such as Thai or Devanagari), and unlisted (a Latin font that no catalog source lists).
- A family's packages are summed: static and variable npm packages, ttf and otf, renamed casks.
- Each count is what happened in the source's window. A young font's count is never scaled up to a rate over the days it has existed: new fonts need the same evidence as old ones (owner rulings of 2026-09-30, H3 and B8a).
- A font gets no term on a channel in its first 60 days there, and a "New" badge (B8b). The days are counted from each source's *data* date; for example, the Almanac 2025 crawl ran in July 2025.

### The engine: evidence states and one ruler

**Four evidence states** for each font/source pair:

| State | When | Effect |
|---|---|---|
| Observed | at or above the source's noise floor | normal term |
| Censored | the source could have shown the font (it has a package, or it is a crawl or top-N list whose frame includes the font), but the value is below the floor or missing | a term at the censored value (below) |
| Not covered | no package; outside a list's frame (a non-Google font on a Google-only list); merged away by the Almanac's name regex; a Debian package outside the release's main archive (owner ruling of 2026-09-30, `debian_frame`); a Linux source abstaining because a Linux system preinstalls the font or another package pulls it in (in every rank except *most installed*) | no term |
| Too new | under 60 days on that channel | no term; "New" badge |

**One ruler.** Every engine source is measured against one ruler: the Homebrew font casks. They cover 1,940 of the 1,946 Google families and most non-Google fonts. The ruler is built after the gates and alias-summing, with the same Nerd, bundle and CJK credits as the Homebrew source. Homebrew's noise floor applies only to Homebrew's own term. On the ruler, it would tie hundreds of tail casks.

**Formulas.**

- Ruler: z_R(f) = Φ⁻¹((L+U)/2n), the mid-rank percentile among eligible ruler families.
- Source s with overlap O_s (the families both the ruler and s carry): let p be the mid-rank percentile of x_s(f) among s's values on O_s. Then z_s(f) is the p-quantile of {z_R(g) : g ∈ O_s}.
- Censored value: z_s(f) is the same mapping applied to the mid-percentile of the censored block within the source's frame.

**What this design achieves.**

- A source that packages only popular fonts can't label its least-installed font unpopular. For example, the typical font Debian packages sits at Homebrew's 87th percentile.
- For a source that sees nearly every family (npm, in *Coding*), this reduces to a plain rank-to-normal-score conversion.
- Small overlaps are noisy. A source's weight is multiplied by min(1, |O_s|/50), and the source is switched off below 15.

**The ruler (D5)** is Homebrew. Each month the engine's ranks are also rerun with Fontsource (its jsDelivr counts) as the ruler, as a check (§9).

**Why not the Top 100's reciprocal-rank fusion (RRF, k=20)?**

- RRF scores a missing entry as 0, the same as disinterest, and k sets the size of that penalty. Monaspace lands at #10, #27, #60 or #199 for k = 1, 20, 60 or 200.
- Averaging log counts fails the other way: fonts seen only by Homebrew, mostly coding fonts, reached #4–#6.
- With only 2–4 lists, Borda, Robust Rank Aggregation, Markov-chain and Kemeny methods either ignore weights or produce cycles.

**Method (D1).** For the engine's ranks: equated normal scores plus shrinkage, as this section and §4 describe. Coverage-aware RRF (k=60, averaged over only the sources that cover the font, plus a prior) runs each month as a cross-check on them. For *Projects* and *Developers & apps*, the measurement model replaced D1 on 2026-10-01 (q1): no ruler, equating, floors, censored terms, hand weights or cross-check. It keeps D1's rule that a source that doesn't carry a font is left out.

### The measurement model: one popularity per font

**One popularity.** Each font f has one latent popularity η_f, with a standard normal prior: 0 is the average tracked font, and the scale is in standard deviations. Each source is a count term that measures η_f with its own noise, window and exposure (q1, pd1). Fonts Over Time gives two terms (§5). Given η_f, the terms are treated as independent (§11 says where that holds only nearly).

**Count terms.** A term models the source's raw counts in its window directly, as a bulk plus a popularity part. Its mean for font f is

mean_f = e_f · (n_f·b + exp(α + λ·η_f))

- n_f·b is the *bulk*: the baseline b that every package collects whatever its popularity, times n_f, the packages the font's count sums over (a bundle package is to count as half, D2, though no bundle reaches these sources yet). For Google views, npm and Expo, each package counts for the share of the source's year it has existed, up to the source's data date and at most 1 (q7): a young package isn't expected to have a year's bulk, and its own counts are never scaled.
- exp(α + λ·η_f) is the part that comes from people choosing the font, with λ > 0, so a more popular font expects more.
- e_f is the exposure, where the source has one (Fonts Over Time: the sites crawled); otherwise 1.

Counts use a negative binomial around that mean, which allows for counts more variable than a Poisson. Traffic in the millions uses a log-normal instead: log y_f ~ N(log mean_f, σ²). A count below the bulk is weak evidence of low popularity. There is no floor, no censoring and no subtraction, so those fonts stay ranked, in the order of their counts (owner rulings of 2026-09-30 and 2026-10-01).

**Intervals.** Where a source only bounds a count, the term is the probability of that interval. A family the Web Almanac doesn't list lies below its list's smallest entry. A parent whose Almanac count folds in its width cuts is an upper bound only, between 0 and its count (q24).

**Coverage.** A source that doesn't carry a font has no term and says nothing about it (q3), as in the engine. The 60-day grace applies too. Each source's term is in §5.

**Fitting.** Each model's parameters (each term's b, α, λ and noise) are fitted by marginal maximum likelihood: η is integrated out over a fixed grid against its prior. *Projects* and *Developers & apps* are fitted separately. The parameters are fitted once and held for a year, so a monthly refit can't move scores by sampling noise alone. They are re-estimated when the year is up, or earlier when a source changes, each time by an owner ruling (q9). A drift flag, comparing each month's order under a fresh fit with its order under the held one, is also to call for an early refit, but it is off until its alarm levels have been calibrated: as first worked out they would fire on the model's known misfit alone. The first fit is made by the replay of the held run of 2026-10-17, which switches the method (owner ruling of 2026-10-19). Each fit is written once, in the private data store, with a hash that ties it to its model's terms and method version. The parameters never reach the repository, a log or a published file: with the published scores they would let anyone work back to Google's and Fonts Over Time's counts (T2, T4). A catalog names only each fit's date and its file's hash (§7).

**Score.** A font's score is the posterior mean of η_f given all its terms. The prior pulls a font with little evidence toward 0, as κ does in the engine. Each term weighs what its fitted noise says, so there are no hand weights or group shares (q5).

## 4. Fusion

### The engine: weighted average and shrinkage

For survey g with source weights w_s, let W_g = Σ w_s over the survey. The sum below runs over observed and censored terms:

S_g(f) = (κ·W_g·μ0 + Σ w_s·z_s(f)) / (κ·W_g + Σ w_s), with κ = 0.2 and μ0 = 0 (the median eligible font).

A font covered by every source keeps 83% of its signal; one covered by a third of the weight keeps 63%.

**Outlier guard:** when a font has at least 3 terms and one source sits more than 1.5 z from the median of all the font's terms, that source gets half weight for that font. All terms are judged against the unguarded values at once, and the event is logged (owner ruling of 2026-09-26, amending M1). The guard runs in the engine's ranks only.

**Worked example** (Homebrew 1.0, Arch 0.75, Debian 0.25; κ·W = 0.4; live values from 2026-09-25, under that date's rules):

| Font | z (Homebrew, Arch, Debian) | Score |
|---|---|---|
| JetBrains Mono | 3.54, 2.70, 1.59 | 2.48 |
| Inter | 2.99, 2.29, 1.77 | 2.15 |
| Monaspace | 2.92, 1.77, no Debian package | 1.98 |

For JetBrains Mono the outlier guard does not fire: the median of its terms is Arch's 2.70, and Debian's 1.59 sits 1.11 z below it, within the 1.5 gap.

The missing Debian package costs Monaspace only a slightly stronger pull toward the middle: it keeps 81% of its signal instead of 83%. RRF would have scored it as if Debian users had rejected it.

### The measurement model: the posterior

The model needs no separate fusion step: the posterior of §3 combines a font's terms, and its mean is the score. There is no κ, and no outlier guard: the guard's half weight retired for the model's ranks (q2). Instead, each term gets a leave-one-term-out check. The font's other terms predict that term's count, and when the count's probability integral transform (PIT) under that prediction falls below 0.001 or above 0.999, the font goes to review as one whose sources conflict (§9). For a count or an interval, the PIT is itself an interval, and the font is flagged only when all of it lies in a tail, so the flag depends on the font's own counts alone. Nothing is down-weighted.

### Overall and the display

**Overall** fuses nothing since it retired (§5). Each font's overall entry repeats its *Projects* entry.

**Display (D6).** In `catalog.json`, every rank has exact ranks 1–100 with a confidence tier, then the bands 101–250 and 251–500, and "501+" for a catalog font placed past 500. Each font also shows its rank in every source. The site data carries an internal sort order, so lists can still be sorted within a band. The site shows no tiers and no likely ranges (owner ruling of 2026-09-30, `no_tiers_or_ranges`): its list shows each ranked font's score, and a font's details give its place ("#12") or its band.

**The shown score (owner rulings of 2026-10-01 and 2026-10-19).** Every view shows A′ = max(1, ⌈100·clip(score/3, 0, 1)⌉), where score is the rank's score: the model's posterior mean, or the engine's fused score. It is linear between the ends 0 and 3 on that scale (q11, q30). So 1 is the average tracked font or below (for the engine, 0 is the median eligible font), and 100 is three standard deviations above it or more. It replaced 100·Φ(z) in every view, the engine's included (`score_display_linear_all_views`). The ends are site constants, reviewed with the model's parameters.

## 5. The rankings

### Desktop: two views

Both views use the sources below, scored by the engine. They differ only in how fonts that Linux systems install automatically are counted:

- **Chosen (desktop)**: fonts people deliberately install. A Linux source leaves out a font that a Linux system preinstalls, or that one other package pulls in for at least half of its installs; the font is still ranked on its other sources.
- **Installed (desktop)**: fonts on the most computers. Every install counts as it is, automatic or not.

In both views, affected fonts carry a tag naming the systems they come with or the packages that pull them in.

Until desktop has a model of its own, the engine runs with interim rules (owner rulings of 2026-09-30). A young font's count is never scaled up to a full year. Arch's monthly samples are pooled into one. Debian counts only packages in the release's main archive. GitHub and Nerd Fonts release downloads are still collected, with no term in any score yet; they count toward the two-group gate only (owner ruling of 2026-10-23, §6).

| Source | Weight | Handling |
|---|---|---|
| Homebrew cask installs, 365 days (macOS) | 1.0 | a cask's installs in the window, as they are: a young cask is not scaled up to a year (H3); subtract a bulk-install floor of 20 a year from each cask; Nerd casks instead get their own floor each run, the 10th percentile of Nerd casks' 365-day installs (about 2,150 a year), subtracted before `nerd_credit` (ruling M3); a family under 60 a year after the floors is censored |
| Arch pkgstats, share of systems | 0.75 | the installs over the systems sampled in 12 complete months, pooled (A2); subtract the Nerd Fonts group floor: the 10th-percentile share of members that have been in the group at least 6 months, with newer members not floored (ruling M4); under 0.3% is censored |
| GitHub release downloads (only repos that are the main download channel) | 0.5 | deferred: no term in any score until it is revisited after 6 monthly snapshots (G1, G3); still collected, and counted toward the two-group gate only (§6) |
| Nerd Fonts release downloads, credited to the original font | 0.3 | deferred with GitHub (G2); still collected, and counted toward the two-group gate only (§6): each file's growth loses the 10th percentile of every file's growth; Symbols Only is dropped |
| ~~Chocolatey download count (Windows)~~ | ~~0.3~~ | not used in v1: its terms forbid automated access and republishing (ruling T3) |
| Debian popcon installs | 0.25 | installs only: the vote series is dropped (DE3); only packages in the release's main archive count, so a third-party, removed or sid-only package is not covered (`debian_frame`); under 100 is censored |

**Notes on the sources.**

- **Arch** pools its months: each system-month counts once, so a recent month, with a larger sample, carries more than an early one (owner ruling of 2026-09-30, A2). Its sample grew from 14,465 to 32,749 systems a month between Sep 2025 and Aug 2026. Before the ruling the engine took the mean of the monthly shares.
- **GitHub and Nerd Fonts** report lifetime totals per file, and only a few monthly snapshots exist. Neither has a term in any score until both are revisited after 6 monthly snapshots (owner rulings of 2026-09-30, G1–G3). Meanwhile their evidence counts toward the two-group gate, in both desktop views and *Coding* (owner ruling of 2026-10-23, `deferred_sources_count_for_gate`; §6). The collectors keep snapshotting:
  - every release of a main-channel repo, with no cap by date, because old releases still dominate (FiraCode's latest release is from 2021 and JetBrains Mono's from 2023); Iosevka, with over 400 releases, is the exception: only its latest 24 releases are fetched, through GitHub's GraphQL API (ruling M2);
  - prereleases only in a repository that publishes nothing else (OpenDyslexic: `prerelease_repos`, owner ruling of 2026-09-26).

  Their evidence, for the gate now and for a term when they return, is the growth between snapshots. An asset present in both snapshots counts its growth, and an asset created after the earlier snapshot counts its whole count, since its baseline is 0 (owner ruling of 2026-09-26); an older asset the earlier snapshot lacks is ignored. Negative differences (deleted or re-uploaded assets) are clamped to 0 and flagged. The old first-run rule, lifetime total ÷ days listed, retired with B8a, so a run with no earlier snapshot has no evidence from them. The earlier snapshot is the one the run's history names: the earliest merged run's within 12 months. For the gate it may be less than a month older: the first two monthly snapshots, of 2026-09-26 and 2026-10-17, are 21 days apart, and whether a font's growth clears the source's noise floor doesn't depend on the gap, since GitHub's floor is 0 and Nerd Fonts' is the 10th percentile of the files' growth, which scales with it.
- **Linux dependency correction (every rank except *most installed*).** In *most chosen*, *Coding* and *Rising*, a Linux source abstains for a font when the font's top reverse Depends/Recommends accounts for at least 50% of its installs (a virtual package, named only in Provides, never pulls a font in, even with one provider: owner ruling of 2026-09-26), or when the owner's preinstalled list names the font for a Linux system whose installs that source counts: Arch-family systems (CachyOS, EndeavourOS) silence Arch pkgstats only, Debian and Ubuntu silence popcon only, and desktops (GNOME, KDE Plasma) silence both (owner ruling of 2026-09-26). Windows, macOS and Android entries on that list only add `preinstalled_on` tags and never cause an abstention. A Linux source counts a family by its most-installed package, not the sum of its packages, since one system installs each package once (owner ruling of 2026-09-26); the dependency share uses the same basis. The test uses the largest single dependent, not the sum of all dependents. When a package depends on alternatives (`a | b`), only the first alternative counts as pulled in. A largest dependent at 35–50% is flagged for the owner's review (ruling M8). Dependencies are parsed from:
  - Arch core/extra;
  - the CachyOS and EndeavourOS repository databases (cachyos-kde-settings, on 3.57% of Arch systems in Aug 2026, requires ttf-fantasque-nerd, ttf-fira-sans and noto-fonts);
  - Debian's Packages.xz.

  Homebrew installs are explicit, so they count as they are everywhere. The *most installed* view skips this correction for every source.
- **Fonts left without desktop evidence.** If abstentions leave a font no observed desktop term, it stays listed in *most chosen* as "no evidence of deliberate installs", with its tag, and keeps its *most installed* rank. It is never removed from the catalog for this reason.
- **Resolution.** The data separates roughly 200–300 families.

**Nerd and CJK credit (D7).** A patched build's installs count in full toward the original font: `nerd_credit` 1.0 and `cjk_build_credit` 1.0 (Maple Mono NF CN and similar). About 59% of Homebrew font installs are Nerd casks, and Meslo LG ranks high because Powerlevel10k recommends it, so coding fonts rank higher under this choice. If the two credits ever differ, a build that is both NF and CN gets the smaller.

**Preinstalled and dependency-pulled fonts (D8).** These fonts stay in every rank; only how their Linux installs are counted changes. Two desktop views are published, *most chosen* and *most installed*, as described at the top of this section. The owner reviews new preinstalled and dependency entries when the script flags them. When desktop moves to its model, a pulled-in font keeps its Linux term, less the installs its top dependent brings in, and preinstalled fonts still abstain (owner rulings of 2026-09-30 and 2026-10-01).

**Desktop weights (D9)** are as in the table, for the engine. Noise estimates suggesting Arch deserves 1.0 came from only 45 families, before correction. When desktop moves to its model, two correlated factors, macOS-and-developer and Linux, take their place, weighed as the owner sets then (q21).

### Project: fonts used in sites, code and apps

The *Projects* rank is scored by the measurement model (§3). Each source gives one term (Fonts Over Time two), in one independence group (§6):

| Source (what it measures) | Group | Term |
|---|---|---|
| Fonts Over Time (FOT): distinct homepages (about 10k) using the font for body text, headings or at least 5% of the text | FOT | two negative-binomial terms, startup homepages and the other homepages (q4): the mean weekly homepages using the font over the month's crawls, a missing week counting 0, with the sites crawled as exposure; no bulk |
| Web Almanac 2025 sheets: pages declaring the font | HTTP Archive | log-normal, no bulk; a family below the list is the interval up to its smallest entry; a parent that folds in width cuts is an upper bound only (q24) |
| Google Fonts views, 1 year: traffic Google serves | Google serving | log-normal, with a bulk scaled by the share of the year since Google Fonts added the family (q7) |
| npm downloads of @fontsource and @fontsource-variable, 1 year | npm registry | log-normal, summed over the font's packages, each with a bulk scaled by its age (q7) |
| ecosyste.ms dependent repositories: GitHub projects declaring an @fontsource or @fontsource-variable package | npm registry | negative binomial, one bulk per package |
| jsDelivr hits (Fontsource stats) | jsDelivr | log-normal, one bulk per package; a family with no hits yet is not covered (q29) |
| npm downloads of @expo-google-fonts, 1 year | npm registry | log-normal, summed over the font's packages, each with a bulk scaled by its age (q7) |
| Flutter `GoogleFonts.<name>` code search | Flutter | off, until it has its own pass (q26) |

**No hand weights.** The fixed group shares (web 55%, code 30%, apps 15%; D10 and M9) retired on 2026-10-01: each term weighs what its fitted noise says (q5). The model takes every count as it is, so the engine's floors and censoring don't apply to these terms.

**Notes on the sources.**

- **Fonts Over Time.**
  - It is new: the crawl started in September 2026, and its repository has no license file. It enters at its fitted weight, with no phase-in (q6).
  - Each run checks FOT's file columns. On a mismatch, FOT is marked stale rather than failing the run.
  - Counted rows: browser and static only, each site once; weekly snapshots averaged over the month; ITF fonts and generic names dropped.
  - Startups make up 5,231 of its 10,465 sites, so they are a term of their own, beside the other homepages (q4). In the model, that replaced D11's cap on their weight. The cap still shapes Fonts Over Time's own published rank and z (§7).
- **Almanac.**
  - The term comes from the pages tab (pages declaring the font; gid 1668708562).
  - A family it doesn't list lies below its list's smallest entry.
  - Its name regex folds Condensed, Narrow and Black cuts into the parent. Those cuts count as not covered, and the parent's count is an upper bound only, in place of the engine's halved weight (q24). The requests-by-service tab (gid 1594814478; top 100 per service) is used only to flag these parent merges and adds no term of its own (ruling M6).
  - A new edition arrives yearly. The sheet id and tabs are set in config and switched in one step, flagged in that month's pull request.
- **Google.** Google is counted once. The metadata `popularity` field is dropped, except as a fallback, because it tracks 7-day views almost exactly (Spearman 0.998). The Top 100 counted Google twice. Google's view counts are used only to rank and score fonts. They are never published, and neither are the model's parameters for them, which would give the counts back (ruling T2). Google's published z is the model's estimate from its term alone (q18b), and its information share stays null (q16).
- **npm.**
  - Each package's daily downloads come from one `api.npmjs.org/downloads/range` request over the last 540 days, at one request a second. The term is the last-year total.
  - Legacy ids are folded (source-sans-pro → source-sans-3).
  - The engine (in *Coding*) still censors npm values under 1,000 a month; the model uses every count.
- **ecosyste.ms** dependent-repository counts (projects that declare each package, so CI re-downloads can't inflate them). Only the @fontsource and @fontsource-variable packages are counted, not Expo's (ruling M7). Its data is CC BY-SA 4.0, the same license as our catalog data (D17).
- **jsDelivr.** Every package collects some hits, so a family with none means its statistics haven't started yet: it is not covered until they show hits (q29).

**Project scope (D10).** Websites, code and apps, including ecosyste.ms dependent repositories.

Print is not measured, because the terms of Fonts In Use forbid robots.

**Fonts Over Time (D11, ruling T4).** It is used under its "free to download and reuse" wording, with credit and a link back, at its fitted weight as two terms (owner rulings of 2026-10-01, q4 and q6, amending T4's phase-in weight). Only its ranks and their rank-based z scores are published, never its raw values or the model's estimates from them, and test fixtures are synthetic. A model score that combines several sources is not a Fonts Over Time value, so publishing it keeps T4 (q17). The owner asked its author for an explicit data license on 2026-09-25 ([fcjr/fontsovertime#1](https://github.com/fcjr/fontsovertime/issues/1)).

### Overall

**Retired (owner ruling of 2026-09-30, `overall_retired`).** The overall rank blended *Chosen (desktop)* and *Projects*, half each (D12). The owner retired it: popularity in projects "as a single factor explains much of the variance in the data", so the list opens on *Projects*, and the other ranks stay selectable. `catalog-site.json` v1 requires a `ranks.overall` entry for every font, so each font's overall entry repeats its *Projects* entry, as a stand-in (`[ranks.overall] stand_in`, q12). The site doesn't offer it. Designer picks are not used.

### Extra views

The owner chose all four (D13).

- **Coding fonts.** Developers are a real audience and already dominate the desktop data.
  - Covers monospace families, scored by the engine.
  - Weights: Homebrew 1.0, Arch 0.75 and Fontsource npm 0.5. Nerd release downloads (1.0) and GitHub (0.5) are deferred, as on the desktop: no term in the score, but they count toward the two-group gate (§6). Arch uses the *most chosen* abstentions.
  - On the site, a spacing filter (Any / Proportional / Monospaced) works on every rank. *Proportional* hides monospace and coding fonts (owner ruling, 2026-09-25).
- **Developers & apps.** Separates what builders choose from web traffic. It is the measurement model of three Project sources, npm (Fontsource), ecosyste.ms dependents and Expo, on the same frames, fitted on its own (q15); M10's weights retired. All its sources share one independence group, so no font can pass the two-group gate: the view is published as bands only, with one note above the list (owner rulings of 2026-09-26 and 2026-09-30), until a second independent source is on. Flutter stays off until it has its own pass.
- **By category.** The Category filter works on every rank: sans, serif, display, handwriting and mono. A font's category comes from Google Fonts' metadata, then Fontsource, then the font's own tables. The owner's category in `config/category-overrides.toml` comes before all of them (owner ruling of 2026-09-29, gate R).
- **Rising (beta).** Feeds "New popular free fonts this month". Scored by the engine.
  - Per source, the log-ratio of recent share to 12-month *share*, not counts: npm keeps growing overall, and Homebrew's 30-day total fell to about 20% of its 90-day total.
  - A font rises only when at least 2 sources agree, its share is at least 0.02%, and the rise holds over 3 months of smoothing.
  - Fonts under 3 months old go to "New" instead.
  - Google alone can't make a font rise: Andada Pro was up 925% in 90 days. A font's rise is its second-largest rise among its sources, so no single source sets it.
  - Every Rising font carries tier C in the data while the view is in beta: Rising has no weights, so its confidence ranges are nearly all single points and would otherwise read as tier A (owner ruling of 2026-09-26).
  - The view appears from the third monthly refresh, once three months of shares exist.

**Deferred:**

- dotfile parsing for Coding fonts;
- an opt-in visitor survey, which would break "font lists never leave the browser";
- HTTP Archive through BigQuery. A sandbox query, dry-run first, could later replace the Almanac sheets.

## 6. Evidence, ties, stability, confidence, state

**Evidence.**

- *Ranked at all:* at least 1 observed term in that rank. Every model term counts as observed, but a font whose only terms are Fonts Over Time's is unranked in the model's ranks: its score would be that source's alone (T4; owner ruling of 2026-10-20). Any other source's term, an interval included, ranks it.
- *Top 100 of any rank:* evidence from at least 2 independence groups. In the engine, a group gives evidence with an observed term, a gate-only source's included (below). In the model, a group gives evidence when one of its terms sits above what bulk alone predicts: a count above the expected bulk, or, for a term with no bulk, a positive count or a place on the Almanac's list (q14). The groups are:
  - Google serving;
  - FOT;
  - HTTP Archive;
  - the npm registry (npm, ecosyste.ms dependents, Expo);
  - jsDelivr;
  - Homebrew;
  - Arch;
  - Debian;
  - GitHub counters (releases; while deferred, for the gate only);
  - Nerd Fonts (while deferred, for the gate only);
  - Flutter (off).

  GitHub's release counts and Homebrew count as one group for a font whose Homebrew cask downloads that repository's release asset, because GitHub's counts then include the cask's installs (ruling M5). Nerd Fonts stays a group of its own even when a font's Nerd cask downloads the Nerd release, because Homebrew's Nerd casks are a small share of Nerd's downloads: for Hack, about 62,000 Homebrew installs a year against 2.3 million Nerd downloads (owner ruling of 2026-09-29). While the two sources are deferred, both rulings decide their groups for the gate.

  **Deferred sources count for the gate (owner ruling of 2026-10-23, `deferred_sources_count_for_gate`).** While GitHub and Nerd Fonts stay out of every score, a font with an observed term from one of them, counted as §5 says, gets that source's group for the two-group gate in each engine rank that lists the source: both desktop views and *Coding*. Their terms change no score, weight or ruler, the cross-checks and confidence draws give them no weight, and tier A counts only the groups in the score. A font that only they observe stays unranked. The case behind the ruling: in October, Hack scored #5 in *Chosen (desktop)* and #9 in *Coding*, but Homebrew was its only group there, since the Linux sources abstain for a font KDE Plasma preinstalls; its Nerd Fonts downloads give it a second group.

  A font that fails the gate sits at 101 or below and is flagged. In a crude check, shrinkage alone left Homebrew-only fonts in the top 15.

**Evidence gate (D14).** The 2-group gate, plus a mild pull toward the middle: κ 0.2 in the engine, the prior in the model.

**Ties.** In the engine: mid-ranks within a source, and a final sort by score, then evidence weight, then ruler z, then name. In the model: by score, then by posterior standard deviation, the more precisely measured font first, then by name. Either way runs are deterministic.

**Stability.**

- Most windows are 12 months long.
- Fonts Over Time's published z (§7) is smoothed with an exponentially weighted moving average (λ 0.5) of its ruler-equated z. Each month the smoothed values are ranked again and mapped back onto the ruler, like any engine source (owner ruling of 2026-09-26). The model's Fonts Over Time terms use the month's own counts.
- The model's parameters are held for a year (§3), so a month's scores move with the data, not with a refit.
- The catalog is the *Projects* top 500 (through the overall entry, which repeats it) plus the top 100 of every rank: `project`, `desktop_chosen`, `desktop_installed`, `coding`, `dev_apps` and `rising` (q13).
- A font enters at rank 450 or better and leaves after 2 runs below 550.
- Every other font that passes the gates will be published as *listed only*, after the site's launch: it has no rank, band or tier, and the site lists these fonts A–Z after the ranked ones. They take the same license (L3) and link checks as catalog fonts (owner rulings of 2026-09-29 and 2026-09-30).
- Top-100 membership has its own hysteresis: a font enters a top 100 at 90 or better and leaves after 2 runs worse than 110 (ruling M11).
- A rank whose method changes starts afresh, as a first run does. Its top-100 list starts over, and so does the catalog when the overall entry's method changes (owner ruling of 2026-10-19). Its `previous_score` equals `score` that month, so no movement shows at the switch (q20). At the switch of 2026-10-20 all seven ranks changed method version; on 2026-10-23 the desktop views and *Coding* changed again, for the deferred sources' part in the gate.

**Confidence.** Each rank entry in the data carries a likely 5–95% rank range and a tier. The site shows neither (`no_tiers_or_ranges`).

- *Engine ranks:* the range comes from 200 Dirichlet weight perturbations plus leave-one-source-out runs. Each draw re-weights a rank's sources with Dirichlet(20·w/W), across all its sources (owner ruling of 2026-09-26). The leave-one-source-out run that drops Homebrew keeps Homebrew as the ruler, so only its term is removed and every other source keeps its scale (owner ruling of 2026-09-26).
- *Model ranks:* no random draws (q19). A font's range is its rank at its own posterior 5% and 95% quantiles, each placed among every other font's score.

The tier follows from the range in both:

- Tier A: at least 2 groups, and a range no wider than max(10, 0.3·rank).
- Tier B: a range no wider than the rank.
- Tier C: everything else.

**Outages.** If a source fails, reuse its last snapshot for up to 2 months, flagged "stale", then drop the source.

**Run state.** Some things carry over between monthly runs:

- membership counters;
- first-seen and exposure dates;
- license text hashes;
- stale-source counters;
- last month's published ranks and scores;
- each run's method version for every rank;
- snapshot baselines for the GitHub differences;
- owner rulings.

They live in committed files under `state/` on the main branch.

- State advances only when a refresh pull request is merged.
- Each run reads state from main plus the snapshot store.
- A new run replaces a refresh pull request that was never merged, so a skipped month doesn't corrupt the next one.

The model's fitted parameters are not in `state/`: they stay in the private data store (§3).

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
  - Fonts Over Time has no license file, so only its ranks and z scores are published while its author is asked for one (D11, ruling T4); a model score that combines several sources is not a Fonts Over Time value (q17);
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
- **Nerd Font build link:** a font with a Nerd Font build also links that build's own page, labelled with the build's name (owner rulings of 2026-09-28 and 2026-09-29). A build its maker publishes (Maple Mono NF, Cascadia Code NF) links the maker's release page, and it comes first, since it is the official source. Otherwise the Nerd Fonts project's build links its folder in the Nerd Fonts repository at the current release tag ("SauceCodePro Nerd Font"), and only when `fonts.json` gives the original a license that qualifies. Nerd Fonts' page for its own build is not an aggregator for this link, but it is never a font's primary link. The site tags these fonts "Nerd Font available", and marks the build's link "NF" in the font's details. The link is checked monthly like the others, and one that fails its check is left out, with no tag or link, until it passes again: the font stays listed, and the monthly review names it. The owner can also hide a font's build, with a reason (`config/nerd-hidden.toml`), for example if its icon sets fail the license check (owner rulings of 2026-09-29).
- **Per rank or view:** {rank or band, order, tier, range, score, previous_score, groups}. The rank keys (`overall`, `desktop_chosen`, `desktop_installed`, `project`, `coding`, `dev_apps`, `rising`) are versioned schema constants.
  - In the engine's ranks, `score` is the fused score, `range` comes from the weight draws, and `groups` counts the independence groups among the score's observed terms. The gate also counts a gate-only source's group (§6), which the font's per-source entries show.
  - In the model's ranks, `score` is the posterior mean of η, `range` is the font's rank at its posterior 5% and 95% quantiles, and `groups` counts the groups with a term above bulk (q14, q19).
  - `overall` repeats the `project` entry (q12).
  - `previous_score` is the font's score in that rank in the last published catalog. In a rank's first run under a new method version it equals `score` (q20).
  - The site shows the score, as A′ (§4), and the rank or band; `tier` and `range` stay unshown.
- **Held-back fonts:** a catalog font with no accepted download link yet (the owner decides the link at gate K) is held back from `catalog.json`, and the published exact ranks close up over it: in each view, every font ranked below it moves up one place, with its range. Places past the exact top keep their order. The monthly review names the held-back font and its places (owner ruling of 2026-09-28).
- **Per source:** {state, rank_in_source, z, weight_used}, describing the font in the source's own survey view, where nothing abstains.
  - *Desktop sources (the engine):* `state` as in §3's table; `z` equated to the ruler; `weight_used` null, since the overall entry now repeats *Projects* and no longer weighs desktop sources.
  - *Gate-only sources (GitHub and Nerd Fonts while deferred):* listed with weight 0 and no ruler overlap, when the run has their growth. A font's entry gives only its `state` and the reason, with no rank, `z`, `weight_used` or raw value, since none of their numbers reach a score. `catalog-site.json` leaves them out.
  - *Project sources (the model):* every term's `state` is "observed", intervals included, with no reason (q18a, the owner's choice); `z` is the posterior mean of η from that source's terms alone (q18b), except Fonts Over Time's, which stays rank-based (T4); `weight_used` is the source's share of the posterior expected information from the font's open sources, every source but Google and Fonts Over Time, so a font's published shares add up to 1 and give nothing private back; it is null for those two (q16, amended 2026-10-20). A source with no term for the font is not covered or too new.
  - Raw values appear only where the source's terms allow; never for Google or Fonts Over Time, whose entries carry no raw values (rulings T2, T4).
- **Top level:** the run date; `method_version`, the newest rank's; `method_versions`, each rank's own, which moves when that rank's rules change; `model_fits`, each model rank's `fitted_on` date and the `sha256` of its stored fit file, so a replay can be held to the same fit (never a parameter); the `ranking.toml` hash; fetch times.

A trimmed `catalog-site.json` feeds the filterable list (Milestone 2), and `names.json`, the names and aliases of every eligible family, feeds the owned-font matching (Milestone 3).

Listed-only fonts (§6), once they are published after launch, carry the identity, license, link and preview fields above, but no ranks and no per-source data. The site fetches them only when a visitor needs them ([milestone-more-fonts.md](milestone-more-fonts.md)).

## 8. Parameters (`config/ranking.toml`)

Source weights and fixed floors are in the §5 tables. The model's fitted parameters are not config: they are held in the private data store (§3).

| Key | Value |
|---|---|
| **The engine** | |
| kappa, mu0 | 0.2, 0 |
| ruler overlap: full weight / off | 50 / 15 |
| homebrew_nerd_floor | the 10th percentile of Nerd casks' 365-day installs, each run (about 2,150 a year), subtracted before `nerd_credit` |
| arch_nerd_group_floor | the 10th-percentile share of members in the group at least 6 months; newer members not floored |
| dependency_abstain (every rank except *most installed*) | 0.5; 0.35–0.5 flagged for review |
| outlier guard | gap 1.5 z from the median of the font's terms, at least 3 terms, weight × 0.5 |
| uncertainty | 200 runs, Dirichlet 20·w, 5–95% |
| rising | 0.02% share, 2 sources, 3 months, "New" under 90 days |
| gate_only (`github`, `nerd`) | true: no term in any score, counted toward the two-group gate (§6) |
| **The model (`[model]`)** | |
| views | `project`, `dev_apps` (q1, q15) |
| quantiles | 0.05, 0.95: the likely range's ends (q19) |
| pit_tail | 0.001: a term whose whole leave-one-term-out PIT interval is below it, or above 1 minus it, is flagged for review (q2) |
| **Both** | |
| min_exposure_days | 60, counted from the data date |
| nerd_credit, cjk_build_credit, bundle_credit | 1.0, 1.0, 0.5 |
| top100_min_groups | 2 |
| catalog | 500 by the overall entry, which repeats *Projects*; enter at 450; leave below 550 for 2 runs; plus the top 100 of every rank |
| top100_membership | enter at 90; leave after 2 runs worse than 110 |
| stale_max_months | 2 |
| `[ranks.overall] stand_in` | `project`: the overall entry repeats *Projects* (q12) |

The shown score's ends, 0 and 3 (§4), are site constants, not in this file.

No rank's score uses these any more, since *Projects* moved to the model (owner rulings of 2026-10-01): `project_group_shares`, the project survey's weights, the `dev_apps` weights, Fonts Over Time's phase-in and startup cap, and the Almanac's `parent_merge_factor`. The startup cap still shapes Fonts Over Time's published rank and z.

## 9. Validation, every run

**Hard failures block the pull request:**

- an ineligible font (ITF, icon, mainly CJK, unverified license) is ranked or listed;
- a flagged license is missing from the review queue;
- the schema check fails;
- a rerun from the same snapshots gives different output;
- adding an ineligible font moves any rank;
- a known answer fails:
  - Source Sans Pro → Source Sans 3;
  - Sauce Code Pro → Source Code Pro;
  - Roboto Slab gets no Roboto counts;
  - a renamed family keeps its `id`;
- in the engine's ranks, a higher count lowers a rank, except when the outlier guard changes for that font or for a font that overtakes it, or through the ruler's scale;
- a model's fit has a problem: it did not converge, its covariance is not positive definite over the free parameters, a free parameter sits on a bound, or a posterior is narrower than the grid can integrate (the messages name parameters, never values);
- a model term fails the monotonicity scan: a font with that term alone, scored across a wide range of counts, loses score as its count rises;
- the two desktop views differ for any reason other than Linux abstentions;
- a Linux source that abstains for a font in *most chosen* still gives it a term in another rank that abstains, or a `weight_used`;
- a `previous_score` is not the font's last published score, or, in a rank's first run under a new method, not its own score;
- a Google or Fonts Over Time value, share or history appears in any published or committed file (T2, T4), or can be worked back from one: a font's published `weight_used` shares in the model's ranks must add up to 1, or all be null;
- a model rank places or scores a font on Fonts Over Time's terms alone (owner ruling of 2026-10-20).

**Flags in the pull request, not blocking:**

- top-500 rank-biased overlap (RBO, p 0.98) with last month below 0.9;
- a source's month-on-month Spearman correlation below 0.9;
- a coverage change above 10%;
- stale sources and schema mismatches;
- in the engine's ranks, ruler overlaps and outlier-guard hits;
- in the model's ranks, conflicting sources: a font in the top 500 with a term whose whole leave-one-term-out PIT interval is below 0.001 or above 0.999, named by source only (q2);
- one-source share jumps above 3×;
- top-100 entries that came from below 250;
- in the engine's ranks, a font moving more than 30% under the coverage-aware RRF or Fontsource-ruler cross-checks;
- fonts in the top 50 of *most chosen* or *Projects* but below 300 in the other;
- a what-if table with each engine weight halved and doubled;
- snapshot size growth;
- exact ranks missing from `catalog.json` (a font outside the catalog, such as a Coding font), and the fonts held back for want of a download link, with their places (owner ruling of 2026-09-28).

**First run:**

- Compare with the old Top 100 only on families eligible in both and not dropped by its installed-fonts filter. That list left out Roboto, Open Sans, JetBrains Mono, Fira Code and other fonts already installed on the owner's machine. Its 5 ranked ITF fonts no longer qualify.
- Report the Spearman correlation and RBO, and explain every move of more than 30 places. There is no pass mark.
- Beforehand, backtest the month-to-month churn on historical windows so the alert thresholds mean something. Windows: pkgstats series, 18 months of npm, Homebrew 30/90/365 days, Google's windows.

## 10. Manual work beyond monthly review

The monthly pull request asks the owner only to review what it flags: new aliases and unmatched names, licenses and failed license checks, preinstalled and dependency entries, new dual-script families, download links, and fonts whose sources conflict. The other manual tasks are:

- **Yearly:** switch the Almanac to the new edition (sheet id and tabs in config), and re-estimate the model's parameters, by an owner ruling (q9).
- **Once:** ask the author of Fonts Over Time for an explicit data license (asked on 2026-09-25: [fcjr/fontsovertime#1](https://github.com/fcjr/fontsovertime/issues/1)).
- **On request:** when someone asks for a missing font, by email to admin@purelyfreefonts.com or with the "Missing font" form, Claude checks it against the gates and the link policy, and the owner rules. An accepted family joins `config/foundries.toml` with the date it was added, never who asked, and is listed from the next refresh (owner ruling of 2026-09-29).

## 11. Known biases

- **Developer skew.** The desktop data comes from developers who leave telemetry on. About 59% of Homebrew font installs are Nerd builds. The 10 biggest plain casks come from 8 families, and 7 of those are coding fonts.
- **Thin platform coverage.** There is no Windows source in v1: Chocolatey's terms forbid automated access, so it was dropped. Windows users, and designers who download zips, would show up only through GitHub's release counts, which are collected but in no score until they are revisited after six monthly snapshots; until then they count toward the two-group gate only.
- **Defaults.** Installs driven by tool defaults count in both desktop views (Meslo via Powerlevel10k). Installs that Linux systems make automatically count fully in *Installed (desktop)*. In *Chosen (desktop)* they are left out, and those fonts are ranked on their other sources. The preinstalled list is kept by hand, and derivative distributions we don't parse leak through into *Chosen (desktop)*.
- **Web data.**
  - Fonts Over Time crawls US homepages only, and half of them are startups.
  - The Almanac is yearly, covers only the top 100, and folds width cuts into their parents.
  - Google's views are weighted by traffic and can't see self-hosted fonts.
  - Google's views also include the traffic of Google's own products, such as Google Sans and Arimo. The model doesn't correct for it, so Google's own fonts may rank a little high: when the owner ruled on this (2026-10-01), Google Sans ranked #71 on *Projects* under the model.
  - CI runs inflate npm.
- **Sources that move together.** The model counts each source as separate evidence of a font's popularity. A few pairs move together more than that allows: Google views and jsDelivr, ecosyste.ms and Expo, and the two Fonts Over Time terms. What they share counts a little more than it should. The owner accepted this, with this note (owner ruling of 2026-10-01). In the check behind that ruling, leaving jsDelivr out altogether barely changed the *Projects* order: 98 of the top 100 stayed, and the rank correlation (Spearman) was 0.9902.
- **Two methods.** The desktop ranks, *Coding* and *Rising* are still scored by the engine, and *Projects* and *Developers & apps* by the model. A score compares fonts within one rank, so the same score in two ranks doesn't mean the same thing.
- **The ruler.** In the engine's ranks, Homebrew's installs are the ruler that puts the other sources on one scale, so Homebrew decides how popular each source's fonts count as a group. As a check, each run also ranks those fonts with Fontsource as the ruler.
- **Not measured.** Print use is unmeasured, and the site itself will nudge installs.
- **Listed on request.** Once fonts are added on request (after launch), a font that no directory carries is listed only once someone asks for it, so fonts whose makers or fans ask are more likely to be listed than equally good fonts nobody has mentioned. Being added on request never changes a rank.
