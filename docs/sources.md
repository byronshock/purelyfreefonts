# Data sources and their terms

This is the terms audit from Milestone 1, step 3. It lists every source the catalog reads: what we take from it, where its terms are, the sentence in them that matters, and what we may publish. The owner ruled on the terms on 2026-09-25 (rulings T1–T5). The rulings are recorded in [AUTHORITY.md](../AUTHORITY.md) and in `data/reviews/terms/2026-09-25.toml`. The method that uses these sources is in [ranking-methodology.md](ranking-methodology.md).

Where no ruling covers a source, a conservative default applies: we publish facts only, and tests use synthetic fixtures.

Every page linked below returned HTTP 200 on 2026-09-25, and each quoted sentence was still on its page.

## How to read this page

**Raw values:** may our public outputs show the source's own numbers? The public outputs are everything the site shows or the public repository holds: `catalog.json`, `catalog-site.json`, the reports committed under `build/` (such as `review.md` and `unmatched.md`), the run state in `state/`, and the site itself.

- *Yes:* counts, shares and similar values may appear, with credit.
- *Ranks only:* only each font's rank in the source and its rank-based z score appear. Counts, and values computed from them such as shares, do not. The source has `publish_raw = false` in `config/ranking.toml`.
- *Facts only:* the source gives facts, not counts: names, license identifiers, dependencies. We state those facts in our own records, with credit, and never copy its files.

**Real fixtures:** may the public repository hold real data from the source as a test fixture?

- *Yes:* a small trimmed extract, at most 50 rows, with no personal data. A `NOTICE` file beside it names the source and its license.
- *Synthetic:* tests use made-up data in the same shape.

**Basis:** the ruling that decides the row, the source's own license, or the default.

Real snapshots of every source we collect, Google and Fonts Over Time included, are kept in the private data repository (D15). The public repository never holds them. Tests that need real data from a synthetic-only source read it from there, and public CI skips them.

Our catalog data is CC BY-SA 4.0, final since ruling T5. That matches the ShareAlike term of ecosyste.ms, the one source whose data license requires it.

## Summary

| Source | What we use | Raw values | Real fixtures | Basis |
|---|---|---|---|---|
| [Google Fonts `/metadata/fonts`](#google-fonts-metadata-endpoints) | family list and facts; its `popularity` rank as a fallback | facts and ranks only | synthetic | T2 |
| [Google Fonts `/metadata/stats`](#google-fonts-metadata-endpoints) | views over one year and shorter windows | ranks only | synthetic | T2 |
| [google/fonts repository](#googlefonts-repository) | METADATA.pb files, license files | yes | yes, with the family's license file | own licenses |
| [Google Fonts glyph sets](#google-fonts-glyph-sets) | the Latin code point lists | yes | yes, with the Apache notice | own license |
| [Fontsource registry and API](#fontsource-registry-and-api) | non-Google fonts, font facts, old ids | yes | yes | T1 |
| [Nerd Fonts `fonts.json`](#nerd-fonts-fontsjson) | the original font behind each Nerd build | yes | yes | T1 |
| [Homebrew casks](#homebrew-casks) | font casks, renames, add dates | yes | yes | T1 |
| [Fontist formulas](#fontist-formulas) | license facts, candidate fonts | facts only | synthetic | default |
| [Debian copyright files](#debian-copyright-files-dep-5) | license identifiers | facts only | synthetic | default |
| [Foundry sites](#foundry-sites) | a hand list of families | facts only | none needed | default |
| [Homebrew analytics](#homebrew-analytics) | cask installs over 30, 90 and 365 days | yes | yes | T1 |
| [Arch pkgstats](#arch-pkgstats) | monthly share of systems per package | yes | yes | T1 |
| [Arch package databases](#arch-package-databases) | dependencies, groups, licenses | yes | yes | T1 |
| [CachyOS and EndeavourOS](#cachyos-and-endeavouros) | CachyOS dependencies; EndeavourOS preinstalled fonts | facts only | synthetic | default |
| [Debian popcon](#debian-popcon) | installs per package | yes | yes | T1 |
| [Debian package lists](#debian-package-lists) | dependencies and sections | yes | yes | T1 |
| [GitHub release downloads](#github-release-downloads) | downloads from main-channel repositories | yes | yes, without author or uploader | T1 |
| [Nerd Fonts releases](#nerd-fonts-releases) | downloads per patched build | yes | yes, without author or uploader | T1 |
| [Fonts Over Time](#fonts-over-time) | fonts on about 10,000 homepages | ranks only | synthetic | T4 |
| [Web Almanac 2025](#web-almanac-2025) | pages declaring each font | yes | yes, with the Apache notice | T1 |
| [npm](#npm) | downloads of `@fontsource`, `@fontsource-variable` and `@expo-google-fonts` packages | yes | yes | T1 |
| [ecosyste.ms](#ecosystems) | projects that depend on each Fontsource package | yes | yes, marked CC BY-SA 4.0 | T1 |
| [jsDelivr, through Fontsource](#jsdelivr-through-fontsource) | CDN hits per package | yes | yes | T1 |
| [Chocolatey](#chocolatey) | nothing: dropped from v1 | not used | none | T3 |
| [Flutter code search](#flutter-code-search) | nothing yet: off in the first build | not published | synthetic | needs a ruling |

## Candidate fonts and license facts

### Google Fonts metadata endpoints

- **Use:** `fonts.google.com/metadata/fonts` gives the family list and facts (name, category, subsets, date added). Its `popularity` field is a rank, used only if the stats endpoint fails. `fonts.google.com/metadata/stats` gives views over one year for the project rank; the collector also keeps its views over 90, 30 and 7 days. Both endpoints are undocumented and need no key. The keyed Developer API is not used.
- **Terms:** [Google Terms of Service](https://policies.google.com/terms), effective 30 July 2026. Google's [robots.txt](https://fonts.google.com/robots.txt) disallows only `/license/`.
- **Quote:** "You may use Google's content as allowed by these terms and any service-specific additional terms, but we retain any intellectual property rights that we have in our content." The terms also forbid "using automated means to access content from any of our services in violation of the machine-readable instructions on our web pages (for example, robots.txt files that disallow crawling, training, or other activities)".
- **Ruling T2:** both endpoints are used. View counts are never published: only each font's rank and its rank-based z score. Family facts such as the category also appear in the catalog; the google/fonts repository carries the same facts under open licenses. Fixtures are synthetic.
- **Note:** the [Google APIs Terms of Service](https://developers.google.com/terms) forbid building databases from API content ("Scrape, build databases, or otherwise create permanent copies of such content"). They cover the keyed Developer API, which we don't use.

### google/fonts repository

- **Use:** a shallow sparse clone of the METADATA.pb files and license texts, plus git history for renamed and removed families.
- **Terms:** the repository's [README](https://github.com/google/fonts/blob/main/README.md). GitHub reports no repository-wide license.
- **Quote:** "The top-level directories indicate the license of all files found within them."
- **What follows:** a family's files are under that family's license (OFL 1.1, Apache 2.0 or UFL). The facts may be published. A fixture copied from a family folder ships with that folder's license file. No ruling was needed.

### Google Fonts glyph sets

- **Use:** the GF Latin Kernel and Core code point lists, for the Latin test.
- **Terms:** [Apache License 2.0](https://github.com/googlefonts/glyphsets/blob/main/LICENSE).
- **What follows:** the two lists are copied unchanged into `data/glyphsets/`, with the upstream license and a `NOTICE` naming the commit. No ruling was needed.

### Fontsource registry and API

- **Use:** the registry files in the Fontsource repository (`family.json` for each family, and `replacements.json` for old ids). The API at `api.fontsource.org/v1/fonts` is used only for ids missing from the registry.
- **Terms:** the repository is under the [MIT License](https://github.com/fontsource/fontsource/blob/main/LICENSE). The [API documentation](https://github.com/fontsource/fontsource/blob/main/website/docs/api/introduction.mdx) states no data license.
- **Quote:** "While the intention is not to throttle the API, we reserve the right to do so if necessary under fair use. The current hard limit is 2500 requests per 10 seconds but constant usage at this rate can result in a temporary ban."
- **Ruling T1:** values may be published, and real fixtures may be committed, with the MIT notice.

### Nerd Fonts `fonts.json`

- **Use:** the list that maps each Nerd Fonts build to its original font (`unpatchedName`, `patchedName`, `folderName`, `caskName`, `licenseId`), and the paths of the license files kept beside each original under `src/unpatched-fonts/` (from the git trees API; the license check reads those texts, pinned to the same commit).
- **Terms:** the repository's [LICENSE](https://github.com/ryanoasis/nerd-fonts/blob/master/LICENSE). `fonts.json` sits in a folder with no license of its own.
- **Quote:** "Source files not in folders containing an explicit license are using the MIT License (MIT)".
- **Ruling T1:** the file may be published and used as a fixture, with the MIT notice.

### Homebrew casks

- **Use:** `formulae.brew.sh/api/cask.json` (font casks: token, names, homepage, download URL, old tokens), `cask_renames.json` and `cask_tap_migrations.json` from the homebrew-cask repository, and cask add dates read through the GitHub API.
- **Terms:** homebrew-cask and formulae.brew.sh are both under the BSD 2-Clause License ([homebrew-cask](https://github.com/Homebrew/homebrew-cask/blob/main/LICENSE), [formulae.brew.sh](https://github.com/Homebrew/formulae.brew.sh/blob/main/LICENSE.txt)). The [API page](https://formulae.brew.sh/docs/api/) states no terms and no rate limits.
- **Quote:** "Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met".
- **Ruling T1:** values may be published, and real fixtures may be committed, with the BSD notice.

### Fontist formulas

- **Use:** license facts (`spdx_license`, `open_license`), font names and homepages, from the root and `sil/` folders of the formulas repository. The `google/` folder repeats Google Fonts, and `macos/` holds proprietary fonts, so neither is read.
- **Terms:** the [formulas repository](https://github.com/fontist/formulas) has no license file, and GitHub reports none. The Fontist tool's own BSD license does not cover this repository.
- **Quote:** none found.
- **Default (no ruling):** facts only, as one input to the license cross-check; formula files are never copied. Fixtures are synthetic.

### Debian copyright files (DEP-5)

- **Use:** the license short names in each font source package's `debian/copyright` file, from `metadata.ftp-master.debian.org`, as one input to the license cross-check. The `Files: debian/*` stanza, which covers packaging, is skipped.
- **Terms:** the footer of [sources.debian.org](https://sources.debian.org/). Each copyright file belongs to its package.
- **Quote:** "Hosted source files are available under their own copyright and licenses."
- **Default (no ruling):** facts only: the license identifiers, never the file text. Fixtures are synthetic. Ruling T1's "Debian package data" means the package lists and popcon; the copyright files were not put to the owner.

### Foundry sites

- **Use:** `config/foundries.toml`, a hand list of families from The League of Moveable Type, Velvetyne, Collletttivo, Open Foundry and Roundo. Claude seeds it once from the foundry sites and the owner reviews it. The sites are not scraped each month (ruling M12); each run only checks that the listed links still work.
- **Terms:** each foundry's own site. No data is copied from them beyond family names and links.
- **Default (no ruling):** facts only. The list is our own file, so no fixture is needed.

## Desktop installs

### Homebrew analytics

- **Use:** `formulae.brew.sh/api/analytics/cask-install/{30d,90d,365d}.json`: font cask installs.
- **Terms:** Homebrew's [analytics documentation](https://docs.brew.sh/Analytics). No license covers the analytics data itself; the site's code is BSD 2-Clause.
- **Quote:** "Aggregate reports and their JSON representations are available from the Homebrew analytics site."
- **Ruling T1:** counts may be published, and real fixtures may be committed, with credit.

### Arch pkgstats

- **Use:** `pkgstats.archlinux.de/api/packages`: for each complete month, how many reporting systems have each package installed.
- **Terms:** no data license is stated. The site's code is GPL-3.0. The [privacy policy](https://pkgstats.archlinux.de/privacy-policy) describes what is stored.
- **Quote:** "The data send by the pkgstats command line tool is stored anonymously on the server."
- **Ruling T1:** counts and shares may be published, and real fixtures may be committed, with credit.

### Arch package databases

- **Use:** the `core`, `extra` and `multilib` package databases from `geo.mirror.pkgbuild.com`: names, dependencies, optional dependencies, provides, groups and licenses of font packages and of the packages that depend on them. The archlinux.org JSON API is not used; the databases carry the same fields.
- **Terms:** Arch licenses its package sources (the PKGBUILDs the databases are built from) under 0BSD ([RFC 40](https://rfc.archlinux.page/0040-license-package-sources/)); the [LICENSE of the ttf-dejavu package](https://gitlab.archlinux.org/archlinux/packaging/packages/ttf-dejavu/-/blob/main/LICENSE) is one example. The [Arch Linux Terms of Service](https://terms.archlinux.org/docs/terms-of-service/) (version 2021-07-18) say nothing about reusing package metadata.
- **Quote:** "Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby granted."
- **Ruling T1:** the values may be published, and real fixtures may be committed, with credit.

### CachyOS and EndeavourOS

- **Use:** CachyOS's `cachyos` package database, for the fonts its settings packages pull in (for example, `cachyos-kde-settings` requires ttf-fantasque-nerd, ttf-fira-sans and noto-fonts). EndeavourOS's repository has no font dependencies, so we use only its installer's base package list, as a seed for the hand-kept `config/preinstalled.toml`.
- **Terms:** the [CachyOS package repository](https://github.com/CachyOS/CachyOS-PKGBUILDS) has no license file; [CachyOS-Settings](https://github.com/CachyOS/CachyOS-Settings) is GPL-3.0. The [EndeavourOS package lists](https://github.com/endeavouros-team/EndeavourOS-packages-lists) have no license file. Neither project publishes terms for its repositories.
- **Quote:** none found.
- **Default (no ruling):** facts only. The catalog names the packages that pull a font in (`pulled_in_by`) and the systems that preinstall it (`preinstalled_on`), and never copies the databases. Fixtures are synthetic. Ruling T1 covers Arch's own databases, not these.

### Debian popcon

- **Use:** `popcon.debian.org/by_inst.gz`: installs reported for each package.
- **Terms:** the footer of the popcon pages links to [Debian's web license](https://www.debian.org/license) ("See license terms"). No other terms cover the data.
- **Quote:** "Since 25 January 2012, the new material can be redistributed and/or modified under the terms of the MIT (Expat) License or, at your option, of the GNU General Public License; either version 2 of the License, or (at your option) any later version".
- **Ruling T1:** counts may be published, and real fixtures may be committed, with that notice.

### Debian package lists

- **Use:** trixie's `main/binary-amd64/Packages.xz`: the section, dependencies, recommends and provides of each font package and of the packages that depend on it.
- **Terms:** none stated for the archive's index files. Debian's web license covers its web pages.
- **Quote:** none found.
- **Ruling T1:** the values may be published, and real fixtures may be committed, with credit.

### GitHub release downloads

- **Use:** the download counts of release assets in repositories that are a font's main download channel, through the GitHub REST and GraphQL APIs.
- **Terms:** the [GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service), section H, and the [Acceptable Use Policies](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies).
- **Quote:** "Scraping does not refer to the collection of information through our API." And: "Researchers may use public, non-personal information from the Service for research purposes, only if any publications resulting from that research are open access." Our catalog and method are open: CC BY-SA 4.0 data and MIT code.
- **Ruling T1:** counts may be published, and real fixtures may be committed. The `author` and `uploader` fields hold personal information, so they are never stored or committed.
- **Note:** the terms warn that "Abuse or excessively frequent requests to GitHub via the API may result in the temporary or permanent suspension of your Account's access to the API." Each run has a GitHub API budget.

### Nerd Fonts releases

- **Use:** download counts of the Nerd Fonts release assets, credited to each build's original font.
- **Terms:** as for [GitHub release downloads](#github-release-downloads).
- **Ruling T1:** as for GitHub release downloads.

## Fonts used in websites, code and apps

### Fonts Over Time

- **Use:** the weekly snapshots from the [fcjr/fontsovertime](https://github.com/fcjr/fontsovertime) repository: which fonts about 10,000 homepages use for body text, headings or at least 5% of the text. Domains are stored only as hashes. Page titles and descriptions are never stored.
- **Terms:** the [data page](https://fontsovertime.com/data). The repository has no license file, and GitHub reports none.
- **Quote:** "Everything behind this site is free to download and reuse. If you publish something with it, we'd appreciate a link back."
- **Ruling T4:** used as D11 describes, at the phase-in weight of 0.10, with credit and a link back. Only ranks and their rank-based z scores are published, never its raw values. Fixtures are synthetic. Claude drafted a request to the author for an explicit data license, such as CC BY 4.0, and the owner posted it on 2026-09-25 as [fcjr/fontsovertime#1](https://github.com/fcjr/fontsovertime/issues/1).

### Web Almanac 2025

- **Use:** the "Fonts 2025" sheet behind the [Web Almanac 2025 fonts chapter](https://almanac.httparchive.org/en/2025/fonts), exported as CSV: the pages tab, plus the requests-by-service tab for flags only. The sheet id and tabs are pinned in config.
- **Terms:** the chapter's footer, which links the [Apache License 2.0](https://github.com/HTTPArchive/almanac.httparchive.org/blob/main/LICENSE). The sheet itself carries no separate notice. The export is on docs.google.com, whose [robots.txt](https://docs.google.com/robots.txt) allows `/spreadsheet` paths.
- **Quote:** "© Web Almanac. Licensed under Apache 2.0."
- **Ruling T1:** values may be published, and real fixtures may be committed, with the Apache notice.

### npm

- **Use:** `api.npmjs.org/downloads/point/last-year/<package>` (and `range` for the date of the first download) for the `@fontsource`, `@fontsource-variable` and `@expo-google-fonts` packages, plus the registry's package list for each of those scopes. At most one request a second.
- **Terms:** the [npm Open-Source Terms](https://docs.npmjs.com/policies/open-source-terms), npm's [crawler policy](https://docs.npmjs.com/policies/crawlers) and the [download counts documentation](https://github.com/npm/registry/blob/main/docs/download-counts.md). None of them mentions download-count data as such.
- **Quote:** "You will not automate access to, use, or monitor the Website, such as with a web crawler, browser plug-in or add-on, or other computer program that is not a web browser. You may replicate data from the Public Registry using the Public APIs per this Agreement." The crawler policy asks crawlers to "keep their request velocity to 1 request per second or less".
- **Ruling T1:** counts may be published, and real fixtures may be committed, with credit.

### ecosyste.ms

- **Use:** `packages.ecosyste.ms` bulk lookup: `dependent_repos_count` for each `@fontsource` and `@fontsource-variable` package (ruling M7). That is about 2,700 packages, 100 to a call, so about 30 calls a month. We use the common pool, which needs no email address.
- **Terms:** the [API page](https://ecosyste.ms/api), the footer of [ecosyste.ms](https://ecosyste.ms/) and the site's [Terms of Service](https://ecosyste.ms/terms). The OpenAPI file gives the same license.
- **Quote:** "All APIs follow OpenAPI 3.0.1 specifications and are available under CC-BY-SA-4.0 license." The footer reads "Code: AGPL-3 — Data: CC BY-SA 4.0". Section 11(b) of the terms says the data "is subject to a creative commons attribution share-alike license", and asks users to comply with it "including attributing ‘ecosyste.ms’ as the source of data when published alongside, or combined with other data".
- **Ruling T1:** counts may be published under CC BY-SA 4.0, which is our data license, crediting ecosyste.ms as the source. We don't suggest that ecosyste.ms is associated with our catalog, which section 11(b) also forbids. A real fixture is marked CC BY-SA 4.0, separately from the MIT code license.
- **Open point:** the same terms say, in section 9, that users will not "Develop or use any third-party applications that interact with our Platform without our prior written consent, including any scripts designed to scrape or extract data from our Platform". Section 11(c) gives a license for personal use that excludes "data mining, robots or similar data gathering or extraction methods". The API page invites exactly this kind of use, and its robots.txt allows everything. The owner has not ruled on these clauses; see [Open points](#open-points).

### jsDelivr, through Fontsource

- **Use:** `api.fontsource.org/v1/stats`, one request: monthly jsDelivr hits for each Fontsource package, which Fontsource reads from jsDelivr's data API. Only the monthly fields are used, because the totals are unreliable. We do not call jsDelivr ourselves.
- **Terms:** Fontsource as [above](#fontsource-registry-and-api). jsDelivr's [Terms of Use](https://github.com/jsdelivr/jsdelivr/blob/master/Terms%20of%20Use.md) (effective 30 May 2026, shown at [jsdelivr.com/terms](https://www.jsdelivr.com/terms)) cover its website and CDN, and say nothing about its usage statistics. Its [data API](https://github.com/jsdelivr/data.jsdelivr.com) states no data license.
- **Quote:** "The API is free to use and imposes no rate limits. However, if you plan to make 100+ RPM for longer periods of time, you should contact us first." (jsDelivr's data API README)
- **Ruling T1:** hits may be published, and real fixtures may be committed, with credit.

### Flutter code search

- **Use:** none yet. The plan is to count `GoogleFonts.<name>` calls in public Flutter code that uses the [google_fonts package](https://pub.dev/packages/google_fonts). It is off in the first build, and deferred until a search service and token are chosen.
- **Terms:** not audited, because the service is not chosen.
- **Default (no ruling):** `publish_raw = false`, and fixtures are synthetic. It needs a terms ruling before it is turned on.

## Not used

### Chocolatey

- **Would have given:** Windows download counts from `community.chocolatey.org`.
- **Terms:** the [Chocolatey Terms of Use](https://chocolatey.org/terms), last updated 3 June 2026. They cover the community package site.
- **Quote:** users warrant that "you will not access the Site through automated or non-human means, whether through a bot, script or otherwise", and agree not to use "any data mining, robots, or similar data gathering and extraction tools". Also: "no part of the Site and no Content or Marks may be copied, reproduced, aggregated, republished, uploaded, posted, publicly displayed, encoded, translated, transmitted, distributed, sold, licensed, or otherwise exploited for any commercial purpose whatsoever, without our express prior written permission."
- **Ruling T3:** dropped from v1. There is no collector and no fixture. The methodology notes the thinner Windows coverage (§11).

## Outside this audit

- Sources the research rejected are listed under D16 in the methodology. One of them, Fonts In Use, forbids robots.
- The font files we read for license checks and previews are not data sources. Each keeps its own license, and previews are shown only for fonts that may be redistributed (D3).
- The reference license texts in `data/license-texts/` are copied unchanged. Each belongs to its license's steward, and the `NOTICE` there names where each came from.

## Open points

- **ecosyste.ms Terms of Service, sections 9 and 11(c).** They ask for written consent before scripts extract data, while the API page and section 11(b) offer the data under CC BY-SA 4.0. The terms still name Open Collective in several places, so they look adapted from that site's terms. Ruling T1 stands. The owner may want to ask ecosyste.ms to confirm that a monthly job of about 30 API calls is fine.
- **Google shares in the Rising history.** Rising keeps each font's last three monthly shares per source in `state/smoothing.json`, which is public, and Google is one of its sources. A Google share is a view count divided by the total. Ruling T2 publishes only ranks and z scores, so this page reads it as ruling shares out too. Google's share history could stay in the private store, or be kept as ranks; otherwise the owner rules on shares.
- **Sources under the default.** Fontist, the Debian copyright files, CachyOS, EndeavourOS and the foundry sites were never put to the owner. They follow the default (facts only, synthetic fixtures) until the owner rules otherwise.
- **Flutter** needs a terms ruling before it is turned on.
- **Fonts Over Time** stays ranks only. If its author grants an explicit license in reply to [the request](https://github.com/fcjr/fontsovertime/issues/1), the owner can revisit ruling T4.

## Keeping this page current

When a source is added, or its terms change, update four places together:

- this page;
- the source's `publish_raw` in `config/ranking.toml`;
- its credit line (`license`) in `config/site.toml`;
- the source credits in the README.

A new source starts under the default until the owner rules on it. New rulings go in `data/reviews/terms/` and AUTHORITY.md.
