# The catalog files

Each monthly run writes three JSON files to `build/`. This page says what is in them, in plain words. The method behind the numbers is in [ranking-methodology.md](ranking-methodology.md), and the sources and their terms are in [sources.md](sources.md).

| File | Written by | Schema | Read by |
|---|---|---|---|
| `catalog.json` | stage `export` | `schemas/catalog.schema.json` | the specimens stage, the monthly review, anyone who wants the full data |
| `catalog-site.json` | stage `export-site` | `schemas/catalog-site.schema.json` | `tff-site build` (Milestone 2) |
| `names.json` | stage `export-site` | `schemas/names.schema.json` | owned-font matching (Milestone 3) |

All three are public and committed. Their data license is CC BY-SA 4.0 (D17, final since ruling T5).

The files are canonical JSON: keys sorted, two-space indent, UTF-8. The same inputs always give the same bytes. Numbers computed by the engine (scores, z, weights) keep 6 decimals.

## Versions and rank keys

Each file carries a `schema_version`. `catalog-site.json` is at `1.0.0`: Milestone 1 step 20 froze version 1 on 2026-09-30, and a change to a v1 field now needs version 2 (owner ruling `site_fields_v1`). `catalog.json` and `names.json` are still drafts, at `0.1.0-draft`.

The rank keys are versioned constants, the same in all three schemas and in the code (`export.RANK_KEYS`):

| Key | Rank | Feeds overall |
|---|---|---|
| `overall` | Overall: desktop *most chosen* and *Used in projects*, half each | – |
| `desktop_chosen` | Desktop: most chosen | yes |
| `desktop_installed` | Desktop: most installed | no |
| `project` | Used in projects | yes |
| `coding` | Coding fonts (monospace only) | no |
| `dev_apps` | Developers & apps | no |
| `rising` | Rising (beta) | no |

Renaming or removing a key is a new major version of all three files. Adding one is a minor version.

A run publishes every key except `rising`, which appears from the third month of merged runs.

## catalog.json

The full catalog: every font in it, every rank, and every source.

### Top level

- `schema_version`.
- `run`: the run `date`, the `method_version` (the date of the methodology the code implements), `config_sha256` (all config files), `ranking_toml_sha256` (the ranking parameters alone), `code_commit` (the commit that ran; forty zeros outside a git checkout) and `generator`.
- `data_license`: `CC-BY-SA-4.0`, not provisional, with its URL.
- `sources[]`: one entry per engine source used this run, in `ranking.toml` order. Disabled sources (Flutter, for now) and sources dropped after two stale months are left out. Each entry has:
  - `id`, `collector`, independence `group` and `survey`;
  - `weight`: its effective weight in the survey view that feeds the overall rank, after overlap scaling, phase-in and the project group shares;
  - `publish_raw`: whether raw values may appear (false for Google and Fonts Over Time, rulings T2 and T4);
  - `data_date` and `fetched_at` of the snapshot used, and `stale` when that snapshot is older than the run;
  - `ruler_overlap`: how many families the source shares with the ruler (Homebrew).
- `fonts[]`: every catalog font, sorted by id.

### Each font

The catalog is the overall top 500 plus the top 100 of the project rank and both desktop views, with the membership hysteresis in methodology §6. Every font in it has an accepted download link: a member with no link that two sources agree on, or that the owner-approved foundry list gives, is held back until the owner picks one (gate K), and `review.md` names it. The published exact ranks close up over it: every font ranked below it moves up one place in that view, so a held-back font leaves no gap in the ranks the site shows (owner ruling of 2026-09-28).

- **Identity:** `id` (stable, never changes), `family` (the current name), `category`, `is_monospace`, `superfamily_id` (from `data/superfamilies.csv`, or null).
  - `category` is `sans-serif`, `serif`, `display`, `handwriting` or `monospace`. The owner's category in `config/category-overrides.toml` comes first (owner ruling of 2026-09-29). Otherwise it comes from Google's metadata, then Fontsource, then the other sources; then `monospace` for a monospaced font; then the font's own OS/2 tables. With none of those it is `sans-serif`, and the monthly review pack lists those catalog fonts for the owner to rule on.
  - `is_monospace` comes from the same sources and the font's tables. A font whose category is `monospace` is always monospaced, the owner's included.
- `aliases[]`: other names of the family, each with its `relation`:
  - `rename`: an old name (Source Sans Pro for Source Sans 3);
  - `build`: a build of the same design (Inter Variable, JetBrainsMono Nerd Font);
  - `postscript`: a PostScript name;
  - `package`, `bundle`, `sibling`: package names, bundle members and folded width siblings.

  Only names people see are listed (family names, font names, and the names the Almanac and Fonts Over Time use); package slugs, folder names and asset names are not. `names.json` follows the same rule.
- `related[]`: families derived from or close to this one, with an optional `note` (Adwaita Sans is derived from Inter). Both families list each other.
- `license`:
  - `spdx` and a readable `name`;
  - `class`, the license class (`open-font`, `permissive`, `attribution`, `freeware`);
  - `redistributable` (always true since Rule 3 of 2026-09-30: only redistributable fonts qualify) and `attribution_required`, and for the latter the `attribution` line to credit;
  - `text_url`, the license text that was checked, with its `text_sha256` and `checked_on` date;
  - `verified_level`: `L3` (the text matched a known license and the font's name table agrees), `ruling` (the owner ruled), or `L2`/`L1` for fonts the full check has not reached.
- `font_file`: the upstream file the license check read, with its `url`, `sha256`, `size` and `format`. Previews are made from it. Null when the check read no file. When the family publishes only an archive, `url` names the file inside it as `<archive>.zip#<path in the archive>`, and `sha256`, `size` and `format` describe that file, not the archive.
- `latin`: `coverage` (`basic` means basic Latin only, the "limited accents" badge) and `basis` (how it passed the Latin gate).
- `formats`: `variable` and `static`.
- `preview_ok`: the license lets the site show previews (redistributable fonts only). `preview`: the rendered specimen (`specimens/<id>.svg` and its sha256), or null.
- `preinstalled_on[]`: systems that ship the font. `pulled_in_by[]`: Linux packages that install it on their own.
- `links`: the `primary` download page and the `designer` page, each with an optional `label`. The primary link may also carry a `note`, a sentence the font's details show under it (for example why an archived mirror is the official download). When the designer's page is gone, an override the owner approves may link an archived copy of it, such as a Wayback Machine capture (owner ruling of 2026-09-29); its `label` then says "archived" and its `note` says why.
- `links.nerd`: the page of the font's Nerd Font build, with a `label` that names the build, or null when it has none (owner rulings of 2026-09-28 and 2026-09-29, TASK-2). Two kinds of build count:
  - **the maker's own**, such as Maple Mono NF or Cascadia Code NF: a Homebrew cask or GitHub release asset that the alias table marks as a Nerd build of the family and that comes from the family's own repository. The link is that repository's releases page, and the label the build's Homebrew name ("Maple Mono NF"), or "<family> NF" when no cask names it;
  - **the Nerd Fonts project's**: the family's folder in Nerd Fonts' `fonts.json`, or the bundle folder it belongs to (Noto, M+, iA Writer). The link is the folder in the Nerd Fonts repository at the current release tag, `https://github.com/ryanoasis/nerd-fonts/tree/<tag>/patched-fonts/<folder>`. The tag is the newest release that is not a prerelease, read from the run's Nerd Fonts release data, so each monthly refresh moves it forward, and the folder must be one that release ships. The label is the build's name in `fonts.json` plus "Nerd Font" ("SauceCodePro Nerd Font"). It is linked only when the build's base license, `fonts.json`'s `licenseId`, is one `config/licenses.toml` allows, with the owner's license rulings.

  When both exist, the maker's own build wins, since it is the official source; so it does over a Nerd Fonts folder that holds only a README (`repoRelease: false` in `fonts.json`, such as CascadiaMono and Monaspace). Without a maker's build, such a folder is still linked: its README names the build, its variants and its downloads. A Nerd link is never a release asset or `/releases/latest`. Nerd Fonts' page for its own build is not an aggregator for this link, while the primary link still may not name `ryanoasis/nerd-fonts` or nerdfonts.com. The monthly link check covers it.

  `links.nerd` is also null (owner rulings of 2026-09-29) when the build's link failed the link check recorded for the run's date, until a later check passes: the font stays in the file, and `review.md` names it under "Nerd Font links left out". And it is null for a family the owner lists in `config/nerd-hidden.toml`, with the reason, whatever build it has.
- `first_seen`: the date the family first appeared in the universe.
- `flags[]`:
  - `too_new`: too new to rank in some view;
  - `gate_held`: the two-group gate kept it out of a top 100;
  - `no_deliberate_evidence`: Linux abstentions left no desktop evidence in *most chosen*;
  - `parent_merge`, `bundle_only`, `dependency_review`, `stale_sync`: flags from the corrections stage;
  - `specimen_failed`, `specimen_name_only`, `specimen_hash_mismatch`: flags from the specimens stage.

### Each rank

`ranks` has one entry per published rank key, except that `coding` holds monospace fonts only.

- A **ranked** font has:
  - `order`, its exact position;
  - `rank`, the same number, inside the top 100;
  - `band` instead of `rank` past 100: `101–250`, `251–500` or `501+`;
  - `tier` (A, B or C) and `range`, the 5–95% rank range;
  - `gate_held` when the two-group gate moved it past 100.
- Nothing sits inside the top 100 without an exact rank. When fewer fonts pass the two-group gate than the top 100 holds, the top holds only those, and every other font's `order` (and `range`) counts on from 101 in the same sequence. This happens in *Developers & apps*: all its sources are one independence group (the npm registry) while Flutter is off, so no font there passes the gate.
- An **unranked** font has every placement field null and a reason in `unranked`:
  - `no_deliberate_evidence`: a Linux source abstained, and it had observed the font in *most installed*;
  - `too_new`: its terms in that rank are too new; in Rising, the font's first day on any channel is under 90 days ago (the "New" list);
  - `no_evidence`: anything else.
- Either way, `score` is the fused score (null without a term) and `groups` the number of independence groups among observed terms.
- `previous_score` is the font's score in that rank in the last published catalog (`state/published_scores.json`, which each run's export writes for the next), or null if it had none there. Before any catalog has been published it equals `score`, so nothing shows as moved (owner ruling of 2026-09-30, `score_previous_bootstrap`). It is kept for the rising and falling markers of Backlog TASK-4.

### Each source

`sources` has one entry per source in `sources[]`. It describes the font as the source's own survey sees it, where nothing abstains: *most installed* for desktop sources, *Used in projects* for project sources. Families that failed the license check are left out there, as the ranking leaves them out, so they take no rank in a source either.

- `state`: `observed`; `censored` (the source could show the font, but its value is under the floor: `reason` `below_floor` or `no_value`); `not_covered` (`reason` `no_package`, `outside_frame` or `merged_into_parent`); or `too_new`.
- `rank_in_source`: the font's rank among the source's observed values, 1 being the highest; ties share a rank (1, 2, 2, 4). Null unless observed.
- `z`: the source's value mapped onto the ruler's scale (rank-based), as the ranking used it. For Fonts Over Time that is after its monthly smoothing (methodology §6), so its `z` can order fonts differently from its `rank_in_source`, which is this month's own count.
- `weight_used`: the weight this source's term carried in the overall rank, after the outlier guard and any weight factor. Null when the font has no term from it there (not covered, too new, or abstaining).
- `abstains_in[]`: the views where a Linux source leaves the font out because a Linux system preinstalls it or another package pulls it in: every view it is in except *most installed*, Rising included.
- `value`: the value after alias sums, credits and floors. It appears only for sources whose `publish_raw` is true. Google and Fonts Over Time never carry one; their ranks and z are published, their counts are not.

A source whose terms forbid showing ranks (`publish_rank = false` in `config/site.toml`) shows no rank, z or value for any font.

## catalog-site.json

The trimmed catalog the site is built from. The owner approved its field list in Milestone 2 step 2 (on 2026-09-25, and the fields added since on 2026-09-30), and Milestone 1 step 20 froze version 1 on 2026-09-30. `site/CONTRACT.md` describes how the site uses it.

Every piece of wording in it comes from `config/site.toml`: the view labels and their one-line descriptions, the tier meanings, the license classes, the Nerd Font marker and legend, and the source credits. System labels come from `config/preinstalled.toml` and, for package systems, from `site.toml`. Band labels come from `config/ranking.toml [display]`.

### Top level

- `schema_version`, and `run`: date, method version, `ranking_toml_sha256` and generator.
- `data_license`, as in `catalog.json`.
- `views[]`: every rank key once, in the order of the rank selector, each with its `label`, `measures` line and `available` (false hides it; Rising stays hidden until it has 3 months of history).
- `bands[]`: `101–250`, `251–500` and `501+` (open-ended).
- `tiers`: what A, B and C mean.
- `sources[]`: the source credits (name, what it measures, URL, license or terms), its `group` and `survey`, `data_date`, `stale`, and `publish_rank` (whether the page may show each font's rank in it).
- `systems[]`: every system a font can come with or be pulled in by, with its operating system; the "Hide fonts that come with …" filter uses it.
- `license_classes[]`: the license filter groups, in filter order.
- `nerd`: the Nerd Font marker's wording (owner rulings of 2026-09-29): the `marker` shown beside the name of a font whose `links.nerd` is set ("NF"), its accessible `label` ("Nerd Font version available"), and the `legend` the list and the details panel show ("NF: Nerd Font version available (adds developer icons, which have their own licenses).").
- `fonts[]`.

### Each font

The same as in `catalog.json`, trimmed:

- the license without its checking details (no level, hash or date);
- `preview` and `font_file` only for fonts whose license allows previews; `font_file` also only up to 20 MB;
- `aliases[]` only the renames, builds and PostScript names, which the search uses;
- `flags[]` only `too_new` and the specimen flags (the others are in the rank entries);
- each rank without `groups`, but with `score`, the fused score, which Milestone 2 shows as a 0–100 bar, 100·Φ(score) (site rulings of 2026-09-29, `score_display`, `score_curve`); every ranked entry has one; and `previous_score`, as in `catalog.json`;
- each source as `state`, `rank_in_source`, `reason` and `abstains_in` only. The file never holds a raw value.

The site build checks more than the schema can: unique ids, bands that follow each other, rank equal to order in the top 100, known classes and systems, one source entry per source, and each font in every available view (`tff_site.data.semantic_errors`).

## names.json

The names Milestone 3 needs to recognise installed fonts. It covers the whole universe, not only the catalog, so a real font outside the catalog is known instead of showing up as a near-match.

- `schema_version`, and `run`: date and method version.
- `families[]`: every family in the universe that passes the gates so far (not dropped, Latin passed, license not excluded, license check not failed). A family whose license still waits for the owner's ruling is included, so the matcher knows it. Each has:
  - `id`, `family`, `superfamily_id` (required, null when the family has no superfamily; the matcher can rely on the key being present), and `in_catalog`;
  - `names[]`: its other names, each with a `relation`: `rename`, `build`, `postscript`, `package`, `bundle`, `sibling` or `related`. A build also has a `detail`: the build type (`nerd`, `nf`, `nfm`, `nfp`, `propo`, `powerline`, `nl`, `cjk`, `variable`, `static`, `opsz`, or empty). Matching an installed font by a `related` or `sibling` name never makes the family count as owned;
  - `distinct_from[]`: families it must never be confused with (Roboto Slab is not Roboto).
- `ineligible[]`: names known not to qualify, each with a `reason`:
  - `proprietary`, `itf`, `generic`, `system`: from the alias table;
  - `icon`: icon, emoji, symbol, barcode, math and music fonts;
  - `cjk` and `latin`: fonts that fail the Latin gate;
  - `license`: fonts whose license is excluded or failed the license check.

## Validation

`uv run tff-catalog validate` runs after the export stages in every refresh. Any failure blocks the refresh pull request. It checks:

1. **Schemas.** The three files against their schemas, and `catalog-site.json` against the site's own cross-reference checks.
2. **Nothing ineligible is ranked.** Every font with evidence passes the gates that come before ranking (not dropped, Latin passed, license not excluded). Every placed or scored font, and every font on the ruler, also passes the license check. Every catalog font also has an allowed license, at L3 or with an owner ruling. A font whose license still waits for the owner is neither ranked nor catalogued until the ruling (filter first). Because no ineligible font has evidence at all, none can move a rank.
3. **Flagged licenses are queued.** Every license that needs the owner, and every failed license check the owner has not already excluded, is in a review queue.
4. **Same inputs, same output.** Rebuilding the three files from the same stage files gives the same bytes (the commit aside). The whole-pipeline replay, `tff-catalog refresh --from-snapshots`, checks the earlier stages.
5. **Known answers.** Source Sans Pro is Source Sans 3; Sauce Code Pro is Source Code Pro; Roboto Slab gets no Roboto counts, and no count comes through a name a `distinct` alias row blocks; a renamed family keeps its id (no new id takes a registered family's name, and no id changes the name it was minted from). Each answer is checked when the families it names are in the universe. A registered id that no source lists any more is a delisted font, not a failure; `build/universe.md` lists it.
6. **A higher count never lowers a rank.** Among fonts with the same sources in a view, one with every value at least another's, and one higher, never scores lower, unless the outlier guard fired. A font whose Fonts Over Time term is smoothed with an earlier month's, and the Rising view, are left to the engine's own tests.
7. **The desktop views differ only by abstentions.** *Most chosen* has the same evidence as *most installed*, less Linux sources' abstentions.
8. **Abstentions don't leak.** A Linux source that abstains for a font in *most chosen* has no evidence for it in any other view where Linux sources abstain, and carries no weight in the overall rank.
9. **No forbidden raw values.** `catalog.json` has no value for a source whose `publish_raw` is false. No committed report (`build/*.md`, `docs/backtests/*.md`) shows such a source's count of 100,000 or more (Google's of 10,000 or more) on a line that also names its font. Smaller counts, such as Fonts Over Time's, can't be told from chance, so the stages that write reports keep them out themselves. The review stage writes `build/review.md` before validation, so a run's own review is scanned.
10. **No private fields.** No committed JSON file (`build/*.json`, the specimen index, `state/`) holds anything but ranks, rank-based z, states and reasons under the id of a source whose `publish_raw` is false.
11. **Previous scores are last month's.** Every rank entry's `previous_score`, in both files, is the font's score in the last published catalog, or null if it had none. With no catalog published yet (no scores in `state/`), it equals `score`. `validate --committed` checks that bootstrap on a clone while `state/run_history.json` holds at most one run; later, `state/` holds the merged run's own scores, so only the run itself can check more.

A check that can't run, because a file is missing or the check itself fails, counts as a failure; the other checks still run. Failures go to the log and, in the monthly refresh, to a public issue, so no failure message repeats a count the terms keep private.

The CI test job also validates the committed `build/*.json` files against their schemas, and CI's `site-real` job runs `tff-catalog validate --committed`: the checks the committed files can answer on their own (schemas, the known answers `names.json` holds, raw values in `catalog.json` and private fields).

Gaps in the exact ranks are not a failure (owner ruling of 2026-09-28). The export closes the ranks up over a held-back member, and `review.md` flags any gap left, such as a Coding font outside the catalog. A rank given twice still fails the schema check, through the site's cross-reference checks.
