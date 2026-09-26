# trulyfreefonts

This project builds a ranked list of the most popular truly free Latin fonts and publishes it at [trulyfreefonts.com](https://trulyfreefonts.com). A font counts as truly free only if its license allows use in all personal and commercial projects, with no use restrictions. Display-only, personal-use-only, demo and non-commercial licenses don't qualify. The first release is the filterable list. A later release compares the list with the fonts a visitor already has and shows the ones they don't have.

## Status

Milestone 1, the catalog pipeline, is in progress. Until the first release, trulyfreefonts.com shows a placeholder page.

- Plan: [docs/roadmap.md](docs/roadmap.md) and the milestone checklists, starting with [docs/milestone-1.md](docs/milestone-1.md).
- How fonts are ranked: [docs/ranking-methodology.md](docs/ranking-methodology.md).
- Settled decisions: [AUTHORITY.md](AUTHORITY.md).

## Development

You need [uv](https://docs.astral.sh/uv/). It installs Python 3.14 if you don't have it.

```sh
uv sync                     # create .venv with the locked dependencies
uv run pytest               # run the tests
uv run tff-catalog --help   # the catalog command line
uv run ruff check           # lint
uv run ruff format --check  # formatting
```

CI runs the lint, the tests and a secret scan (gitleaks) on every pull request. It skips tests marked `network`, `store` or `browser`, which need the network, the private snapshot store or a browser.

## Licenses

- **Code:** MIT. See [LICENSE](LICENSE).
- **Catalog data** (`build/*.json`, `build/*.md` and `data/`, except files with their own notice; the specimen images in `build/specimens/` follow each font's license): CC BY-SA 4.0. The owner made it final on 2026-09-25 with the terms rulings T1–T5 (Milestone 1, step 3). See [LICENSE-DATA](LICENSE-DATA).
- **Fonts** are not part of this project's licenses. Each font keeps its own.

## Source credits

The rankings are built from data that others publish. Thank you to all of them.

The license column shows each source's license or terms and, where the owner has ruled on them (2026-09-25; see [AUTHORITY.md](AUTHORITY.md)), what we may publish from it. "Terms under review" means we have found no license for the data and no ruling covers it yet.

**Candidate fonts and license facts**

| Source | What we use | License or terms |
|---|---|---|
| [Google Fonts](https://github.com/google/fonts) repository | family list, metadata, licenses | each family folder is under its font's license (OFL 1.1, Apache 2.0 or UFL) |
| [Fontsource](https://fontsource.org) registry ([repository](https://github.com/fontsource/fontsource)) | non-Google fonts, font facts | MIT |
| [Nerd Fonts](https://github.com/ryanoasis/nerd-fonts) `fonts.json` | the original fonts behind Nerd Font builds | MIT |
| [Homebrew casks](https://github.com/Homebrew/homebrew-cask) | fonts packaged for macOS | BSD-2-Clause |
| [Fontist formulas](https://github.com/fontist/formulas) | license facts only | terms under review (no license file) |
| [Debian copyright files](https://sources.debian.org) | license cross-checks, identifiers only | terms under review (each file belongs to its package) |
| [Google Fonts glyph sets](https://github.com/googlefonts/glyphsets) | the Latin coverage test | Apache 2.0 |
| Foundries: [The League of Moveable Type](https://www.theleagueofmoveabletype.com), [Velvetyne](https://velvetyne.fr), [Collletttivo](https://www.collletttivo.it), [Open Foundry](https://open-foundry.com) and others | extra candidate fonts | terms under review |

**Desktop installs**

| Source | What we use | License or terms |
|---|---|---|
| [Homebrew analytics](https://formulae.brew.sh/analytics/) | font cask installs on macOS, 365 days | no data license stated; counts may be published, with credit |
| [Arch Linux pkgstats](https://pkgstats.archlinux.de) | share of Arch systems with each font package | no data license stated; counts may be published, with credit |
| Package databases of [Arch Linux](https://archlinux.org/packages/), [CachyOS](https://cachyos.org) and [EndeavourOS](https://endeavouros.com) | which packages pull fonts in | Arch: no data license stated; may be published, with credit. CachyOS and EndeavourOS: terms under review |
| [Debian popularity contest](https://popcon.debian.org) | installs reported by Debian-based systems | MIT (Expat) or GPL-2.0-or-later, per [Debian's web license](https://www.debian.org/license) |
| [Debian package lists](https://deb.debian.org/debian/) | which packages pull fonts in | no data license stated; may be published, with credit |
| GitHub release downloads, through the [GitHub API](https://docs.github.com/en/rest/releases) | downloads of fonts whose main channel is GitHub | counts may be published, with credit ([GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)) |
| [Nerd Fonts releases](https://github.com/ryanoasis/nerd-fonts/releases) | patched-build downloads, credited to the original font | as for GitHub |
| [Chocolatey](https://community.chocolatey.org) | download counts on Windows | not used in v1: its terms forbid automated access and republishing |

**Fonts used in websites, code and apps**

| Source | What we use | License or terms |
|---|---|---|
| [Fonts Over Time](https://fontsovertime.com) | fonts on about 10,000 homepages | used with credit and a link back, as its data page asks; an explicit license has been [requested](https://github.com/fcjr/fontsovertime/issues/1) (no license file; the data page says the data is free to download and reuse). Only ranks and rank-based scores are published |
| [Web Almanac 2025](https://almanac.httparchive.org) | pages declaring each font | Apache 2.0 |
| [Google Fonts](https://fonts.google.com) | views over one year | used for ranks and rank-based scores only; view counts are not published ([Google Terms of Service](https://policies.google.com/terms)) |
| [npm](https://www.npmjs.com) downloads of `@fontsource`, `@fontsource-variable` and `@expo-google-fonts` packages | code and app use | npm's terms say nothing specific about download counts; counts may be published, with credit |
| [ecosyste.ms](https://packages.ecosyste.ms) | GitHub projects that depend on each @fontsource or @fontsource-variable package | CC BY-SA 4.0 |
| [jsDelivr](https://www.jsdelivr.com) hits, through [Fontsource](https://fontsource.org)'s stats | CDN use | no data license stated; counts may be published, with credit |
| Uses of [google_fonts for Flutter](https://pub.dev/packages/google_fonts) in public code | app use; not collected in the first build | terms under review |
