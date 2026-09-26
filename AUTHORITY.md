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
3. **Redistribution.** Rule 1 is about use, so a license that places no restrictions on use but forbids redistributing the font files still qualifies. Each font records whether it may be redistributed. The web page has an option labelled **"Redistributable fonts only"** (or similar wording) that hides the rest; when it is off, those fonts are listed too. *(2026-09-25)*
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
  - Previews are shown only for redistributable fonts, served unchanged.

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
  - **M1:** the outlier guard stays (gap 1.5 z, at least 3 terms, half weight), and the worked example is corrected to JetBrains Mono 2.53.
  - **M2:** GitHub counts every release of a main-channel repo, as growth between snapshots, with no 24-month cap; Iosevka uses its latest 24 releases, through GraphQL.
  - **M3:** Homebrew Nerd casks get their own floor each run, the 10th percentile of Nerd casks' 365-day installs (about 2,150 a year), subtracted before `nerd_credit`.
  - **M4:** the Arch Nerd Fonts group floor is the 10th-percentile share of members in the group at least 6 months; newer members are not floored.
  - **M5:** GitHub counters and Homebrew are one independence group for a font whose Homebrew cask downloads that repo's release asset.
  - **M6:** the Almanac's pages tab is the term; its services tab only flags parent merges.
  - **M7:** ecosyste.ms counts `@fontsource` and `@fontsource-variable` packages only.
  - **M8:** a Linux source abstains when the largest single dependent brings in at least 50% of installs; in `a | b` the first alternative is credited; 35–50% is flagged for review.
  - **M9 (the owner's choice, not the recommended default):** the project group shares stay fixed at web 55%, code 30%, apps 15%; within a group, the sources share its weight pro rata to their effective weights (after phase-in, overlap scaling, stale drops and switched-off sources).
  - **M10:** Developers & apps uses the project weights, rescaled: npm 0.15, ecosyste.ms 0.10, Expo 0.10, and Flutter 0.05 when it is on.
  - **M11:** the top-100 lists have hysteresis: a font enters at 90 or better and leaves after 2 runs worse than 110.
  - **M12:** foundry families are a hand list in `config/foundries.toml`, seeded once by Claude from the foundry sites and reviewed by the owner with the step 2 config.

  *(2026-09-25)*

## Site (Milestone 2)

Answers to the Milestone 2, Step 0 decisions in `docs/milestone-2.md` ([pull request #2](https://github.com/byronshock/trulyfreefonts/pull/2)).

- **M2-D1: default rank.** The list opens on the **overall** rank. *(2026-09-25)*
- **M2-D2: numbers under filters.** Each filtered list renumbers from 1. Fonts past the exact top 100 show their band instead of a number. *(2026-09-25)*
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
- **Spacing filter.** "Spacing: Any / Proportional / Monospaced" replaces D13's "Text only" filter and the "monospace only" checkbox. *Proportional* is the old "Text only": it hides monospace and coding fonts. The filter applies to every rank. Recorded in `data/reviews/site/2026-09-25.toml`. *(2026-09-25)*
- **Project rank label.** On the site, the Project rank (D10) is labelled **"Used in projects"**. *(2026-09-25)*
- **Site data fields (M2 step 2).** The `catalog-site.json` fields in `schemas/catalog-site.schema.json` are approved as drafted. Designer lists are left out, because D12 is usage only. *(2026-09-25)*
- **License filter.** Four groups: open font licenses (OFL, UFL, Bitstream Vera); permissive (Apache, MIT, BSD, CC0); attribution required (CC BY); and other free-use grants, which allow any use but may forbid redistributing the files (Rule 3). *(2026-09-25)*

## Infrastructure

*(2026-09-25; setup checklist and runbook in [ops/SERVER.md](ops/SERVER.md))*

- **Domains.** trulyfreefonts.com, .org and .net, registered and hosted on Cloudflare. The canonical URL is `https://trulyfreefonts.com`; `www.*`, `.org` and `.net` 301-redirect to it, keeping the path.
- **Hosting.** A Contabo VPS running Debian 13, served by Caddy, with the site root at `/srv/trulyfreefonts/public`.
- **Cloudflare.** Proxied (orange cloud), SSL mode Full (strict), with a Cloudflare Origin CA certificate on the server.
- **Access.** One admin user `byron`, key-only SSH, passwordless sudo, root login off. Local alias `ssh tff`. Claude manages the server over SSH and Cloudflare through an API token; its ssh/scp/rsync commands to `tff` are auto-allowed in this project.
- **Secrets.** Kept out of the project: the Cloudflare token lives in `~/.config/trulyfreefonts/cloudflare.env`, and the root password (VNC emergency access only) lives in the owner's password manager.
- **Visitor privacy on the server.** Cloudflare's Network Error Logging is off on all three zones. The access log masks visitor IPs to /16 (IPv4) and /32 (IPv6), drops the port and IP-carrying headers, and is kept for 14 days. *(2026-09-25)*

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
  - the milestone checklists `docs/milestone-1.md` to `docs/milestone-4.md`;
  - `docs/owned-fonts.md`, the notes for Milestone 3.
- **Monthly data refresh.** A scheduled GitHub Actions workflow runs the pipeline and opens a pull request with the new catalog and anything flagged for review. The owner reviews and merges it.
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
