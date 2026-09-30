# Milestone 1 checklist: build catalog.json (about 500 families)

Milestone 1 builds the ranked, license-checked catalog that the filterable list (Milestone 2) publishes. The method is in [ranking-methodology.md](ranking-methodology.md), and settled decisions are in [AUTHORITY.md](../AUTHORITY.md).

**Where it stands (2026-09-30).** The catalog is built, reviewed by the owner and committed: 500 fonts, ranked and license-checked, and the site's data file is ready. Two steps are left: switching on the monthly refresh (step 19) and handing off to Milestone 2 (step 20). Four smaller items can be done any time. Everything finished is under [Completed](#completed) at the bottom, with its evidence.

**How to read it.** *Who* says who does the work, *Needs* what must be finished first, and *Done when* what must be true to close a step. When an item is done and verified, tick it and move it to Completed.

---

## Still to do

### Step 19: Switch on the monthly refresh
**Who:** Claude sets it up with `gh` (owner ruling CI1, 2026-09-26); the owner merges the first refresh pull request. **Needs:** nothing more: the one-command refresh (step 18) is done.

The workflow, `.github/workflows/refresh.yml`, is written but has never run on GitHub. Its schedule stays off until the repository variable `REFRESH_SCHEDULE` is "on" (owner ruling of 2026-09-29).

- [ ] Let refresh pull requests trigger CI: turn on "Allow GitHub Actions to create and approve pull requests" with `gh` (it is off now). The refresh job opens its pull request with `GITHUB_TOKEN` and then starts `ci.yml` on `refresh/monthly` itself, because pull requests that token opens trigger no workflows. No GitHub App or personal token is needed.
- [ ] Give the workflow the private data store: create the data repository's deploy key and this repository's `DATA_STORE_KEY` secret (D15), and record both in SERVER.md and here.
- [ ] Run it once by hand (`workflow_dispatch`) and check:
  - that the collectors work on GitHub's runners: rate limits, the GitHub API budget, and whether Google's endpoints respond;
  - that it opens or updates the pull request on the fixed branch `refresh/monthly` with `catalog.json`, `catalog-site.json`, `review.md`, the `state/` changes, and the alias rows and seeds the run added to `data/`, and that a hard failure fails the job and opens an issue instead (the `publish` and `report` jobs, written but not yet run).
- [ ] Install the watchdog on the VPS. GitHub disables scheduled workflows in public repositories after 60 days without activity, so a timer outside GitHub, reading its public API, warns the owner if no refresh pull request has appeared for 35 days, or `main` has had no commit for 50 days. (Written: `ops/refresh-watchdog/`, a script with a systemd timer.)
- [ ] Turn the schedule on with `gh variable set REFRESH_SCHEDULE --body on`. The workflow then runs at 06:17 UTC on the 3rd of each month (`17 6 3 * *`) and on demand, with a uv cache, the `refresh` concurrency group, and write permission for `contents`, `pull-requests` and `issues`.

**Done when:** a manual run opens a correct pull request, CI runs on it, and the owner merges it.

### Step 20: Hand off to Milestone 2
**Who:** both. **Needs:** step 19.

- [ ] Freeze `catalog-site.json` v1: `schema_version` goes from `1.0.0-draft` to `1.0.0`, with the sample file and the schema doc. First, Milestone 2's step 2 approves the fields added since it approved the list on 2026-09-25, each from an owner ruling: the `nerd` group, the `app` system type, a link `note`, `score` and `previous_score`.
- [ ] A handoff note lists what Milestone 2 must settle:
  - the default rank order;
  - numbering under filters, now scores in place of numbers (AUTHORITY.md, "Scores instead of numbers");
  - the deploy path to the VPS;
  - the methodology page.
- [ ] A runbook lists the manual monthly tasks (methodology §10). None exists yet in `ops/` or `docs/`.
- [ ] "Current step" in AUTHORITY.md moves to Milestone 2, and `docs/roadmap.md` shows the new status.

**Done when:** the owner accepts the handoff.

### Smaller items, any time
None of these blocks the refresh or the handoff.

- [ ] <a id="step-3"></a>**Step 3, run state.** The state that carries from month to month is built (`state.py`): a run writes it to `build/state/`, the one `refresh/monthly` pull request copies it to `state/`, and a new run replaces an unmerged one. `state/` stays empty until the first refresh pull request is merged. Still open:
  - the four `[real]` cases of `tests/pipeline/test_state_two_runs.py` (two runs without a merge give the same state as one run) xfail until the synthetic store holds the real collectors' extract formats; the stub pipeline and the real state and parse modules pass;
  - no run has yet fetched one real source twice on one date to show that it keeps exactly one snapshot per date (the fake-collector tests in `tests/test_store.py` and `tests/test_fetch.py` pass).
- [ ] **Step 5, fonts without a readable file.** 129 families are out as `no_file`, because the pipeline has no file of theirs to test for Latin coverage.
  - Next, now that the Milestone 1 pull request has merged (owner ruling `no_file_builds`, 2026-09-29): the builds and umbrella casks among them, and the 21 Iosevka variants.
  - Waiting for the pipeline to read tar archives (`no_file_files`): Computer Modern, New Computer Modern, Spleen and Scientifica.
  - Out anyway: Microsoft's and Apple's fonts and the mainly CJK fonts; no ranking source counts the 64 others.
- [ ] **Step 13, backtest.** Run `tff-catalog backtest` on the real store and commit its report to `docs/backtests/`. (Built: `backtest.py`; `tests/pipeline/test_backtest.py`. `ranking.toml [review]` keeps the §9 alert thresholds until the owner revisits them after 3 merged refreshes, ruling of 2026-09-26.)
- [ ] **Step 14, a monthly link check that tells someone.** The check runs in every refresh and as `tff-catalog links --check`, but a failure reaches only the log and `build/stage/queues/links.json`: `review.md`, validate and gate K don't report it, and `refresh.yml` doesn't run `links --check`. So a link that breaks later would stay published without a flag.

### Moved out of this milestone
- **Step 15b**, every other qualifying font A–Z and fonts added on request: moved on 2026-09-30 to [milestone-more-fonts.md](milestone-more-fonts.md), after Milestone 2's launch (`more_fonts_timing`, AUTHORITY.md).

---

## Completed

The finished steps and items, as they were ticked, with their evidence. The critical path was 0 → 1 → 2 → 3 → 4 → (5, 5b, 6a and 7, alongside 8) → 9 → 10 → 11 → 12 → 6b → 13 → 15 → 16 → (17 and 18) → 19 → 20, with step 14 alongside steps 8–13.

<details>
<summary>Every finished step and item, with its evidence (click to open)</summary>

### Step 0: Owner reviews the ranking methodology
**Who:** owner; Claude answers questions and makes edits. **Depends on:** nothing.
- [x] Claude opens a pull request adding `docs/ranking-methodology.md` (status: proposal) and this checklist. ([#1](https://github.com/byronshock/trulyfreefonts/pull/1))
- [x] Claude posts a rough preview in the pull request ([comment](https://github.com/byronshock/trulyfreefonts/pull/1#issuecomment-5833687627)): the top 50 of the desktop, project and overall ranks, built from the local data of 2026-09-25 with naive name matching and labelled "rough".
- [x] The owner answers the eleven **DECISION [Step 0]** items and edits any [later OK] default. *(Done 2026-09-25; [later OK] defaults kept.)*
  - D1 method: equated scores plus shrinkage;
  - D3 licenses: CC-BY in with a badge, copyleft out;
  - D4 Latin: option (C), strict plus a reviewed allowlist;
  - D7 Nerd and CJK credit: 1.0;
  - D8 two desktop views, *most chosen* feeds overall;
  - D10 project scope: websites, code and apps, plus ecosyste.ms dependents;
  - D12 overall: usage only, 50/50;
  - D13 extra views: all four;
  - D14 evidence gate: two groups plus κ 0.2;
  - D15 snapshots: private data repository;
  - D17 licenses: MIT code; CC BY-SA 4.0 data, provisional until step 3. *(made final 2026-09-25, ruling T5)*
- [x] If D12 is (a), the owner names 3–5 designer lists (with URLs); otherwise designer picks are set to 0. *(D12 is usage only: designer picks are 0.)*
- [x] Claude records each answer in AUTHORITY.md with its date. *(All eleven recorded 2026-09-25.)*
- [x] Claude updates the stale lines in PLAN.md, which stays local and untracked:
  - "reciprocal-rank fusion";
  - "publishes by rsync";
  - "Google Fonts metadata" as a ranking source.
- [x] Claude marks the methodology "approved"; the owner merges the pull request. *(Approved 2026-09-25; merged by Claude at the owner's request.)*

**Done when:** no [Step 0] decision is open, and AUTHORITY.md and both docs are merged to `main`.

### Step 1: Project setup
**Who:** Claude, including branch protection with gh (owner-approved 2026-09-25). **Depends on:** D17 from step 0; the rest can start during step 0.
- [x] A uv project on Python 3.14:
  - `pyproject.toml`, `src/tff_catalog/`, `tests/`, and a CLI named `tff-catalog`;
  - dependencies httpx, fonttools, numpy and jsonschema (tomllib is built in);
  - `uv.lock` committed.
- [x] Layout: *(All committed: the four config files, `data/aliases.csv` and `data/reviews/`, and `build/catalog.json`, `catalog-site.json` and `review.md` from the real-store rebuild of 2026-09-29. `state/` holds only `.gitkeep` until the first refresh pull request is merged.)*
  - `config/`: `ranking.toml`, `licenses.toml`, `preinstalled.toml`, `foundries.toml`;
  - `data/`: `aliases.csv`, `reviews/` (owner rulings);
  - `state/` (see step 3);
  - `build/`: `catalog.json`, `catalog-site.json`, `review.md`.
- [x] `LICENSE` (MIT) for code and `LICENSE-DATA` (CC BY-SA 4.0) for the catalog, per D17. The data license is marked provisional. *(Made final 2026-09-25 by ruling T5.)*
- [x] A README with source credits.
- [x] CI on every pull request: `uv run ruff check`, `uv run pytest`, and a secret scan (gitleaks). *(`.github/workflows/ci.yml` has the `lint`, `test` and `secrets` jobs; the first run on GitHub passed on [#6](https://github.com/byronshock/trulyfreefonts/pull/6).)*
- [x] Claude, with gh (owner-approved 2026-09-25): branch protection for `main` requiring the CI checks, with **0 required approvals**, because the owner can't approve their own pull requests. *(Checks lint, test, secrets; 0 approvals.)*
- [x] Confirm `.gitignore` still excludes `PLAN.md`, `ops/SERVER.local.md` and `.claude/settings.local.json`.

**Done when:** a pull request with one trivial test passes CI, and both license files are on `main`. *(Met 2026-09-25: [#6](https://github.com/byronshock/trulyfreefonts/pull/6).)*

### Step 2: Config files
**Who:** Claude writes them; the owner edits values. **Depends on:** 0, 1.
- [x] `ranking.toml` holds every parameter in the methodology (the §5 tables and §8). It is validated on load, and unknown or missing keys fail. *(Done 2026-09-25, with the rulings' gate values; `tests/test_config.py`.)*
- [x] `licenses.toml` sorts licenses into allowed, excluded and owner-ruling classes per D3. Anything not listed is excluded. *(30 allowed ids in the site's filter groups, 35 excluded and 13 for owner rulings, loaded strictly; `licenses.classify` excludes any id it doesn't list; `tests/pipeline/test_licenses.py::test_d3_classes`.)*
- [x] `preinstalled.toml`, seeded from the research and reviewed by the owner. *(Eleven entries, each naming its system and OS: seven Linux systems (GNOME with Adwaita), Cascadia on `windows-11`, `macos`, `android` and the LibreOffice bundle (`os = "app"`). Only `os = "linux"` entries cause abstentions (`corrections.abstentions`), and every entry adds `preinstalled_on` tags; `tests/test_config_data.py`, `tests/pipeline/test_corrections.py`. Approved at gate C, 2026-09-26.)* Seeds:
  - Linux defaults;
  - Cascadia on Windows 11;
  - the macOS list;
  - GNOME's Adwaita fonts;
  - Android;

  Each entry names its system. Only Linux entries trigger abstentions; Windows, macOS and Android entries only add `preinstalled_on` tags.
  - the LibreOffice bundle.
- [x] `foundries.toml` is a hand list of the families of League of Moveable Type, Velvetyne, Collletttivo, Open Foundry and Roundo. Claude seeds it once from the foundry sites, and the owner reviews it with `preinstalled.toml`; the sites are not scraped each month (ruling M12). *(The League of Moveable Type (20 families), Velvetyne (43), Collletttivo (16) and Open Foundry (25); Roundo, a family rather than a foundry, is listed under Fontshare; `tests/test_config_data.py::test_m12_foundries_are_present`. Approved at gate C, 2026-09-26.)*
- [x] `tff-catalog config` prints the effective config and its hash. *(Done 2026-09-25; the hash is stable across runs.)*

**Done when:** the loader tests pass and the owner has reviewed `preinstalled.toml` and `foundries.toml`. *(Met 2026-09-26: `tests/test_config.py` and `tests/test_config_data.py` pass, and the owner approved both files at gate C, `data/reviews/config/2026-09-26.toml`.)*

### Step 3: Collector framework, snapshots, run state and source terms
**Who:** Claude; the owner rules on the terms table and posts the Fonts Over Time request. **Depends on:** 1 and D15.
- [x] A shared fetcher: *(`fetch.Fetcher`: this User-Agent, retries with seeded exponential backoff that honour `Retry-After`, one request a second per host, and conditional GETs from the last manifest; `tests/test_fetch.py`.)*
  - User-Agent `trulyfreefonts-catalog/<version> (+https://github.com/byronshock/trulyfreefonts)`, never a personal email;
  - retries with backoff;
  - per-host rate limits;
  - conditional GETs.
- [x] Snapshot store per D15: *(`store.Store` writes each snapshot whole, with its `manifest.json`; raw files are deleted after the run unless `--keep-raw`; `Store.check()` holds the size limits; `tests/test_store.py`. The real store holds one 2026-09-26 snapshot per source.)*
  - font-relevant extracts plus a manifest (url, sha256, fetched_at, status) in `<store>/<source>/<YYYY-MM-DD>/`;
  - big raw files expire after the run;
  - a size check.
- [x] Stale-data policy: reuse the last snapshot for up to 2 months, flagged. *(`[stale] max_months = 2`: `parse` takes the newest snapshot of the last 62 days, flags the source stale in `review.md` and the catalog's `sources`, then drops it; `tests/test_parse.py::test_stale_and_dropped_sources`, `tests/test_fetch.py`.)*
- [x] Terms audit, one row per source, recording whether raw values and fixtures may be republished. The result goes in `docs/sources.md`. *(Rulings T1–T5 given 2026-09-25, in `data/reviews/terms/`; `docs/sources.md` written and its links and quotes checked 2026-09-25. The owner ruled its two open points on 2026-09-26: ecosyste.ms is collected and credited per its terms, and Google's shares stay in the private data repository (`data/reviews/terms/2026-09-26.toml`).)* Rows:
  - Google's undocumented endpoints;
  - Fonts Over Time (no license file);
  - ecosyste.ms (data CC BY-SA 4.0, verified 2026-09-25);
  - the Almanac sheets;
  - Homebrew;
  - pkgstats;
  - popcon;
  - npm;
  - jsDelivr;
  - Chocolatey;
  - GitHub.
- [x] Claude drafts a request to the Fonts Over Time author for an explicit data license (for example CC BY 4.0); the owner posts it. *(Posted 2026-09-25 as [fcjr/fontsovertime#1](https://github.com/fcjr/fontsovertime/issues/1).)*

### Step 4: Candidate universe
**Who:** Claude. **Depends on:** 2, 3.
- [x] Collectors for: *(Seven collectors in `src/tff_catalog/collectors/universe/`, each tested offline in `tests/collectors/<name>/`; the store holds a 2026-09-26 snapshot of each.)*
  - Google Fonts metadata;
  - the google/fonts tree and METADATA.pb files (license folder, repository, minisite);
  - Fontsource;
  - Nerd Fonts `fonts.json`;
  - Homebrew `cask.json`;
  - Fontist formulas;
  - `foundries.toml`.
- [x] Each family gets a stable `id`, minted from its name at first sighting and never changed. Renames only add aliases. Source memberships and upstream URLs are recorded. *(`universe.py`: ids minted by `names.mint_id` into the append-only registry, and each family records its sources, keys and URLs; `tests/pipeline/test_universe.py::test_rename_keeps_id`, `::test_registry_is_append_only`. The registry reaches `state/ids.json` with the first merged refresh.)*
- [x] Drop non-text families, each with a reason code. *(`universe.family_drop`: the real run dropped 355 of 3,141 families, each listed with its reason in `build/universe.md`; `tests/pipeline/test_universe.py::test_family_drop`.)*

**Done when:**
- a universe report shows every name mapped to a family, with per-source counts and drop reasons;
- ids are identical across two runs;
- a rename test keeps the id.

*(Met 2026-09-29: `build/universe.md` maps every key, with 0 unmapped, and gives per-source counts and drop reasons; `tests/pipeline/test_universe.py::test_ids_identical_across_two_runs` and `::test_rename_keeps_id` pass.)*

**Parallel:** one agent per catalog collector.

### Step 5: Latin filter
**Who:** Claude; the owner signs off the allowlist. **Depends on:** 4 and D4.
- [x] Google families: apply rule A (expect 1,264). *(`latin.rule_a`; on Google's list of 2026-09-26 it passes 1,248 of the 1,946 families, not the 1,264 of earlier data; `tests/pipeline/test_latin.py::test_rule_a`.)*
- [x] Dual-script review sheet: about 30 candidates, sorted by Google year views, with the Latin-language counts from each family's metadata. The owner marks the allowlist. *(2026-09-26: the real sheet had 443 candidates; the owner ruled a rule instead (gate L2), which Claude applied family by family in `data/reviews/latin/2026-09-26.toml`: 178 included, 206 script companions and 59 basic-Latin-only families left out. 2026-09-28: the owner confirmed that the 149 companions the first count had put on the include side stay out (`data/reviews/latin/2026-09-28.toml`); the latest run's latin stage has the 178 as `owner_allowlist`.)*
- [x] Map CJK builds to their Latin parent at `cjk_build_credit`, and exclude families that are mainly CJK. *(Built: `build` rows with detail `cjk` fold CJK builds into their parent (Maple Mono's CN casks), and the real run keeps 367 mainly-CJK families out; `tests/pipeline/test_latin.py`, `tests/pipeline/test_corrections.py`. 2026-09-29: Homebrew's four HackGen and Cica casks fold into Hack as builds, as gate A made Arch's `ttf-hackgen` and `ttf-cica` on 2026-09-28 (Claude's ruling `claude-hackgen-cica-casks`, `data/reviews/aliases/2026-09-29.toml`); in a replay of the 2026-09-26 snapshots the four families are gone and 363 mainly-CJK families are out.)*

**Done when:**
- every family has a `latin` field;
- the allowlist is approved;
- spot checks pass: Inter and Iosevka are in; Pretendard and LXGW WenKai are out.

*(Met 2026-09-29: every eligible family has a `latin` verdict, the owner approved the allowlist at gate L, and on the real run Inter and Iosevka are in while Pretendard and LXGW WenKai are out as mainly CJK; the Latin-only Pretendard Std is a family of its own. The non-Google item is still open.)*

**Parallel:** batch the file tests across agents.

### Step 5b: Font facts
**Who:** Claude. **Depends on:** 4; runs alongside step 5.
- [x] For every family, including non-Google ones: *(`facts.py`: all three facts for the 2,786 eligible families of the real run, from Google's metadata, Fontsource or the font's own tables, cached by file sha256, with the 22 owner categories first; `tests/pipeline/test_facts.py`.)*
  - `category`;
  - `is_monospace` (from `post.isFixedPitch` or panose when no metadata exists);
  - `formats` {variable, static}.

  Sources: GF metadata, Fontsource, or the font's own tables, cached by file hash. The owner's categories in `config/category-overrides.toml` come before all of them (owner ruling of 2026-09-29, gate R round 1: the 22 catalog fonts left on the sans-serif default).

**Done when:** every family has all three fields, and spot checks pass: JetBrains Mono is monospace; Inter is sans and variable. *(Met 2026-09-29: every eligible family has all three in the real run, and in `build/catalog.json` JetBrains Mono is monospace and Inter is sans-serif and variable.)*

### Step 6a: License classification (before ranking)
**Who:** Claude; the owner rules on the queue. **Depends on:** 2, 4.
- [x] L1: map every license string to an SPDX ID through `licenses.toml`. *(Built: `licenses.normalize` and `classify` read strings through `config/license-aliases.toml` and `licenses.toml`; `tests/pipeline/test_licenses.py`. 2026-09-29: the 8 strings the run could not read are mapped: Arch's `LicenseRef-OFL-1.1`, `LicenseRef-GUST-Font-1.0` and `custom:Ubuntu Font Licence 1.0`; Debian's `BSD3|SIL` (IBM 3270), `OFL-1.1-no-RFN or EPL-2.0 or BSD-3-clause` (B612) and `GPL-3+ with Special Font Exception` (FreeFont's font files); and Fontist's AU Passata and fontopo ids. EPL-2.0 and those two Fontist licenses are new, so `licenses.toml` holds them in the ruling class for the owner. In a replay of the 2026-09-26 snapshots `licenses --queue` has no L1 item left.)*
- [x] L2: cross-check the google/fonts folder, Fontsource, Fontist, Nerd Fonts, Debian DEP-5 and Arch. *(`licenses.cross_check` combines the license facts of all six and queues disagreements; `tests/pipeline/test_licenses.py`.)*
- [x] Per font, provisionally: class, redistributable, attribution_required and preview_ok (per D3). *(`licenses.Verdict` for all 1,816 candidates of the 2026-09-29 run: 1,747 allowed, 65 excluded, and 4 waiting on strings L1 can't read (AU Passata, AU Passata Light, AU Peto, fontoPoSOLID); `tests/pipeline/test_licenses.py::test_d3_classes`. Later that day, with 6 more families through the Latin gate: 1,822 candidates, 1,750 allowed, 68 excluded (ET Book and Open Sans Hebrew and Condensed have no license found), and the same 4 waiting on the owner's rulings of their now readable licenses.)*
- [x] Rule 3 of 2026-09-30 (owner ruling `redistributable_only`): only redistributable fonts qualify. Gate LIC's option (b) now reads "Excluded: free to use but not redistributable (Rule 3)", and a stored (b) answer excludes; a `licenses.toml` entry with `redistributable = false` classifies as excluded; export fails on an allowed font that isn't redistributable. *(`tff_catalog.licenses`, `export`; `tests/pipeline/test_licenses.py`, `test_export.py`. Merged in [#25](https://github.com/byronshock/trulyfreefonts/pull/25); its real-store rebuild (d6144b1) dropped fontopoSOLID, the one family under `LIC-spdx-licenseref-fontopo-free` that had been in the qualifying universe, and left the 500 catalog fonts unchanged.)*
- [x] Review queue for NOASSERTION results, custom texts and disagreements (DejaVu, Hack, Cascadia, OpenDyslexic, URW, Roboto Mono). Owner rulings are saved in `data/reviews/`. *(2026-09-26: the owner's 14 answers are recorded. 2026-09-28: the 5 Monaspace families and the 25 researched no-license families are ruled (`data/reviews/licenses/2026-09-28.toml`). Later batches that day ruled the researched Monofur, Vic Fieger (Heavy Data), Salaowu and Letters licenses, the Debian disagreements, Conakry and five more disagreements. No gate LIC question is open on the real store (replay of 2026-09-29); the queue's 8 remaining items are L1's unread strings. 2026-09-29, later: with those strings read, the queue holds 13 owner questions instead: 11 disagreements they raise (8 TeX Gyre families, as TeX Gyre Heros's of 2026-09-26; IBM 3270; B612 and B612 Mono) and the two new Fontist licenses. The owner ruled them the same day (`data/reviews/licenses/2026-09-29.toml`): the 11 qualify and are redistributable (the TeX Gyre families under the GUST Font License), AU Passata's terms are excluded and fontopo's qualify but are not redistributable; so do FreeFont (GPL-3.0-or-later with the font exception), ET Book (MIT) and Linux Libertine and Biolinum (OFL-1.1, the disagreement their new files raised).)*

**Done when:** every candidate has a provisional class, and the queue is empty. *(Met 2026-09-29: in a replay of the 2026-09-26 snapshots with the owner's rulings of that day, all 1,822 candidates have a class (1,755 allowed, 67 excluded, none waiting) and `licenses --queue --count` is 0.)*

### Step 7: Alias table
**Who:** Claude; the owner reviews flagged rows. **Depends on:** 4.
- [x] `data/aliases.csv`, with columns: alias, family_id, relation, source, first_seen, reviewed_by. *(3,493 rows. The header adds `ns` and `detail`, which carries the reason code, and sibling pairs are `distinct` rows; `aliases.AliasRow.problems` refuses an unknown relation or reason; `tests/pipeline/test_aliases.py`.)* Relations:
  - rename, build, package, postscript;
  - sibling;
  - related;
  - **ineligible**, with a reason code (proprietary, ITF, CJK, icon, generic, system; since the owner's ruling of 2026-09-28 also not_font, non_latin and unlisted).
- [x] Mine aliases from: *(One miner each in `src/tff_catalog/aliases/miners/`: `gf_history` (never through googlefontdirectory-hg), `fontsource_legacy`, `nerd` (the one list of Nerd casks), `homebrew`, `distro`, `github_repos` (each repo with its `main_channel` flag) and `name_tables`; `tests/aliases/`.)*
  - google/fonts delisted directories and git history (never pair through googlefontdirectory-hg);
  - Fontsource's legacy names;
  - Nerd Fonts `fonts.json`, keeping one authoritative list of Nerd casks;
  - Homebrew `old_tokens` and `cask_renames`;
  - Arch and Debian package names;
  - ~~a hand-made table of about 60 Chocolatey ids;~~ *(not needed: Chocolatey is dropped from v1, ruling T3)*
  - a GitHub repo-to-family table with a main-download-channel flag;
  - upstream name tables (Inter Variable, Inter Display).
- [x] Sibling rules that block false matches: Roboto ≠ Roboto Slab, Fira Sans ≠ Fira Code, Inter ≠ Inter Tight, Noto Sans ≠ Noto Sans JP. *(`aliases.SIBLING_RULES` writes them as `distinct` rows; `tests/pipeline/test_known_answers.py::test_a_sibling_gets_no_counts_of_its_parent`.)*
- [x] Auto-accept only renames from google/fonts history and Nerd `unpatchedName` rows; the owner reviews the rest. *(2026-09-28: under the owner's delegation (A_U_queues), Claude settled 817 of the 1,038 queued rows that were mechanical, as its own rulings in `data/reviews/aliases/2026-09-28.toml` (`by = "claude"`, rows `reviewed_by claude:2026-09-28`): distro packages whose file lists hold one family, exact names, declared renames, the families' own names, and ineligible names. Later batches that day settled the other 221: the owner's rulings, with Claude's package-contents research settling the clear rows and 21 doubtful ones going to the owner. The Chunk fold followed on 2026-09-29 (`data/reviews/aliases/2026-09-29.toml`). The queue is empty on the real store (replay of 2026-09-29).)*

**Done when:** the known-answer tests pass and the review queue is empty. *(Met 2026-09-29: `tests/pipeline/test_known_answers.py` passes, validate's known-answer check passes on the real run, and the alias queue is empty.)*

**Parallel:** one agent per alias source.

### Step 8: One collector per ranking source
**Who:** Claude. **Depends on:** 3; runs alongside steps 5–7. Each collector writes a dated snapshot and parsed values, and has a test built from a fixture the terms ruling allows (synthetic if not).
- [x] Homebrew analytics for 30, 90 and 365 days. Cask add dates come from a tree diff of `Casks/font` at the window start and end, plus the commits API for added casks (design-m1 C4). *(`collectors/ranking/homebrew_analytics.py`, inside the run's GitHub budget; `tests/collectors/homebrew_analytics/`.)*
- [x] Arch pkgstats: monthly shares for the last 12 complete months, for all font-like packages. *(`collectors/ranking/pkgstats.py` keeps each package's count and sample size per month; `tests/collectors/pkgstats/`.)*
- [x] Dependency data: *(`collectors/ranking/arch_repos.py` reads Arch core, extra and multilib and CachyOS's `cachyos.db`; `debian.py` reads `Packages.xz` and `by_inst.gz`; EndeavourOS's fonts are listed in `config/preinstalled.toml`; `tests/collectors/arch_repos/`, `tests/collectors/debian/`.)*
  - Arch core/extra `.db`;
  - the CachyOS and EndeavourOS repository databases (EndeavourOS has no font dependencies of its own; its fonts come from eos-base-group, gate C2, 2026-09-26);
  - Debian `Packages.xz` and popcon `by_inst.gz`.
- [x] Debian popcon main/fonts. *(`collectors/ranking/debian.py` keeps the `by_inst.gz` rows of Section fonts packages, a superset of main/fonts, and of their dependents; `tests/collectors/debian/`.)*
- [x] GitHub releases for main-channel repos: every release, with no cap by date, measured as growth between snapshots; for Iosevka, only the latest 24 releases, through GraphQL (ruling M2). All within a per-run GitHub API budget. *(`collectors/ranking/github_releases.py` over the 52 repos of `config/sources/github_releases.toml`; the growth is computed in `corrections.py`; `tests/collectors/github_releases/`.)*
- [x] Nerd Fonts releases. *(`collectors/ranking/nerd_releases.py`: every release of ryanoasis/nerd-fonts, font archives only; `tests/collectors/nerd_releases/`.)*
- ~~Chocolatey OData.~~ *(Dropped from v1: its terms forbid automated access; ruling T3.)*
- [x] Fonts Over Time weekly JSONL and `latest.csv`, with a column check that marks the source stale on mismatch. *(`collectors/ranking/fot.py`; `tests/collectors/fot/test_fot.py::test_a_column_mismatch_leaves_the_source_stale`.)*
- [x] Web Almanac 2025 sheets: find the header row, validate the columns, and pin the sheet id and tabs per edition in config. *(`collectors/ranking/almanac.py`, pinned in `config/sources/almanac.toml`; `tests/collectors/almanac/`.)*
- [x] Google Fonts `/metadata/stats`, falling back to the popularity field. *(`collectors/ranking/gf_stats.py`, and `[sources.google] fallback` in `ranking.toml`; `tests/collectors/gf_stats/`, `tests/pipeline/test_mapping.py::test_google_falls_back_to_popularity`.)*
- [x] npm downloads for every @fontsource, @fontsource-variable and @expo-google-fonts package: one `downloads/range` request per package over 540 days (last-year total, complete months and first download day), at most 1 request a second with backoff on 429. *(`collectors/ranking/npm.py`, paced at one request per 1.5 seconds; `tests/collectors/npm/`.)*
- [x] ecosyste.ms dependent-repository counts for the @fontsource and @fontsource-variable packages only (packages.ecosyste.ms API; ruling M7), credited as CC BY-SA 4.0. *(`collectors/ranking/ecosystems.py`, credited in the README and `catalog-site.json`; `tests/collectors/ecosystems/`.)*
- [x] Fontsource `/v1/stats` (jsDelivr). *(`collectors/ranking/fontsource_stats.py`: monthly jsDelivr hits per package; `tests/collectors/fontsource_stats/`.)*
- ~~Flutter code search: deferred until a token is chosen.~~ *(Off in v1: owner ruling of 2026-09-26, `default_sources` in `data/reviews/terms/`; `[sources.flutter] enabled = false`, and there is no collector.)*

**Done when:** `tff-catalog fetch` runs every collector locally. The Actions run is checked in step 19. *(Met 2026-09-26: the fetch wrote a complete snapshot for all 12 ranking collectors, none stale; `build/catalog.json` lists each source's data date and fetch time.)*

**Parallel:** one agent per collector; the biggest speed-up in the milestone.

### Step 9: Name mapping and unmatched report
**Who:** Claude; the owner reviews the top of the report. **Depends on:** 7, 8.
- [x] Map every source key to a family id or an ineligible row, and set the four evidence states. *(`mapping.resolve` and `map_records` look keys up exactly, and stage `correct` sets the four states, all of them present in `build/catalog.json`; `tests/pipeline/test_mapping.py`, `tests/pipeline/test_corrections.py`.)*
- [x] `build/unmatched.md` lists keys with no alias row, above each source's floor, sorted by volume. Nothing is guessed. *(`mapping.unmatched_report`; `tests/pipeline/test_mapping.py::test_report_lists_keys_above_the_floor_largest_first`.)*
- [x] The owner resolves the top entries; new rows go into `aliases.csv`. *(2026-09-28: of the 501 keys gate U asked, 180 were settled by Claude's hand rows under the owner's delegation and 154 by the gate A rows; the other 167 are settled after the owner's later rulings of the same day: bundle rows from file lists, the new reasons not_font, non_latin and unlisted, researched leftover packages, and four keys left unmatched (answer (d)). Rulings in `data/reviews/unmatched/2026-09-28.toml`. 2026-09-29: the row that ruling U-115b853c4cd3 gives `deb-pkg:fonts-adwaita`, a package of Adwaita Sans, never reached `aliases.csv`, because the gate A item it names, A-a49b3ac465, matched no candidate any more; it and `deb-src:fonts-adwaita`'s row (A-a04c72303e) are now hand rows dated as those rulings (Claude's record `claude-adwaita-package-rows`, `data/reviews/aliases/2026-09-29.toml`).)*

**Done when:** no source has an unmatched key in its top 200 (ineligible rows count as resolved, and so do keys the owner rules to leave unmatched, gate U answer (d): TeX Live's font packages and `ttf-google-fonts-git`, 2026-09-28), and the known answers pass. *(Met 2026-09-29: in a replay of the 2026-09-26 snapshots, `tff-catalog map --check-unmatched 200` finds no unmatched key in any source's top 200 (it listed `deb-pkg:fonts-adwaita` at Debian #148 before), the alias queue is empty, and the known answers pass.)*

### Step 10: Confound corrections
**Who:** Claude; the owner reviews flagged preinstalled and dependency entries. **Depends on:** 8, 9.
- [x] For every rank except *most installed* (most chosen, overall, Coding): reverse-dependency abstentions (the largest single dependent at 50% or more, with `a | b` credited to the first alternative and 35–50% flagged; ruling M8), plus abstentions for fonts a Linux system preinstalls. The *most installed* view keeps every count. *(`corrections.dependency_shares` and `abstentions`, applied by `build_views` to every view but *most installed*; `tests/pipeline/test_corrections.py::test_linux_sources_abstain_in_most_chosen_and_count_in_most_installed`.)*
- [x] `preinstalled_on` and `pulled_in_by` tags for each affected font, used by both views. *(`corrections.build_tags`, exported per font to `catalog.json`; `tests/pipeline/test_corrections.py::test_tags_name_the_systems_and_packages`.)*
- [x] Noise floors (Homebrew, the Arch Nerd Fonts group, Nerd release downloads), bundle, Nerd and CJK credits, and exposure counted from data dates. *(`corrections.key_floors` (rulings M3 and M4, Nerd's 10th percentile), `apply_floors`, `_credit` and `exposure`; the real run's floors are in `build/corrections.md`; `tests/pipeline/test_corrections.py`.)*
- [x] A per-font correction report, with new cases flagged for the owner. *(2026-09-28: the owner ruled every new case (`data/reviews/corrections/`); the gate X queue is empty on the real store, replay of 2026-09-29.)*

**Done when:**
- the owner has reviewed the report;
- in *most chosen*, Quicksand and DejaVu abstain on Debian and Hack abstains on Arch; in *most installed*, they count;
- Fantasque Sans Mono and Fira Sans have been checked against CachyOS's dependencies.

*(Met 2026-09-29: the owner ruled every gate X case (`data/reviews/corrections/`). In `build/catalog.json`, Quicksand and the DejaVu families abstain on Debian and Hack on Arch in *most chosen*, and all of them count in *most installed*. On the 2026-09-26 data, Fantasque Sans Mono's largest CachyOS dependent is cachyos-fish-config at 32.4% (cachyos-kde-settings 22.5%), and Fira Sans's is cachyos-kde-settings at 19.0%: both are under 35%, so both keep counting.)*

### Step 11: Per-survey ranking engine
**Who:** Claude. **Depends on:** 2 for building on synthetic fixtures during steps 4–10; 10 for the real run.
- [x] Ruler built from Homebrew values after the gates, alias-summing and credits. *(`corrections.ruler_counts` and `engine/equate.build_ruler`; `tests/pipeline/test_corrections.py::test_floors_credits_ruler_and_gates`.)*
- [x] Equating, and the censored value pinned as in the methodology (§3). *(`engine/equate.equate_source`: the censored block sits at the bottom of the overlap; `tests/engine/test_equate.py`.)*
- [x] Weight scaling for small overlaps. *(`equate.overlap_scale` in `surveys.effective_weights`: min(1, |O|/50), off below 15; `tests/engine/test_equate.py::test_overlap_scale`. The real run scales GitHub in Coding, where it shares 39 fonts with the ruler.)*
- [x] Shrunk mean, outlier guard, the two-group gate for the top 100, and deterministic ties. *(`engine/fuse.py` (κ 0.2, the guard on the median basis) and `engine/order.py` (the gate; ties by score, evidence weight, ruler z, then name); `tests/engine/test_fuse.py`, `tests/engine/test_order.py`.)*
- [x] Property tests: *(`tests/engine/test_properties.py`, with each exception pinned by an example.)*
  - determinism;
  - adding an ineligible font moves nothing;
  - a higher count never lowers a rank, except when the guard changes for that font or for a font that overtakes it, or through the ruler's scale.
- [x] The worked example (2.48 / 2.15 / 1.98, with the outlier guard on and its median basis; ruling M1 as amended on 2026-09-26) as a fixture. *(`tests/fixtures/engine/worked_example.toml`, `tests/engine/test_worked_example.py`, which also checks the fixture's guard against `ranking.toml`.)*
- [x] Cross-checks: coverage-aware RRF (k=60) and the Fontsource ruler. *(`engine/crosscheck.py`; `tests/engine/test_crosscheck.py`; `build/review.md` lists the moves under both.)*

**Done when:** the tests pass, and both desktop views and the project rank are produced from real data. *(Met 2026-09-29: `tests/engine` passes, and the real-store rebuild places 317 catalog fonts in *most chosen*, 322 in *most installed* and 436 in the project rank.)*

### Step 12: Overall rank, views and membership
**Who:** Claude. **Depends on:** 11.
- [x] Overall rank with split weights (desktop from *most chosen*), shrunk once. *(`surveys.overall`; `tests/pipeline/test_surveys.py::test_overall_is_the_mixed_weighted_mean_shrunk_once`.)*
- [x] Views: Desktop *most installed*, Coding, Developers & apps, categories, Rising (beta). *(`surveys.views`. Developers & apps is bands only, Rising is published from the third merged refresh, and categories are the site's category filter; `tests/pipeline/test_views.py`.)*
- [x] A test that changing any extra view's weights (Coding, Developers & apps, Rising) leaves the overall rank unchanged. *(`tests/pipeline/test_views.py::test_changing_an_extra_view_leaves_overall_unchanged`.)*
- [x] A leak test: changing a Linux source's count for a font that abstains in *most chosen* leaves the overall rank unchanged. *(`tests/pipeline/test_views.py::test_abstention_leak_linux_counts_of_abstaining_fonts_never_reach_overall`; validate runs the same check each refresh.)*
- [x] Catalog membership (the overall top 500 plus the top 100 of the project rank and of both desktop views), with hysteresis counters in `state/`. For the top-100 lists, a font enters at 90 or better and leaves after 2 runs worse than 110 (ruling M11). *(`membership.update`; `tests/pipeline/test_membership.py`. The stage writes the counters to `build/state/membership.json`, which reaches `state/` when a refresh pull request is merged.)*

**Done when:** every catalog candidate has every published rank it has evidence for; fonts unranked in *most chosen* only because of abstentions are listed with their tag. *(Met 2026-09-29: in `build/catalog.json` every catalog font is placed in each rank it has evidence for, and the 5 fonts unranked in *most chosen* for want of deliberate installs, among them Clear Sans and PT Sans Narrow, carry their `pulled_in_by` tags.)*

### Step 6b: License verification for the catalog (after ranking)
**Who:** Claude; the owner rules on the queue. **Depends on:** 12; on the critical path before step 13.
- [x] L3, for overall rank 700 or better and the top 150 of the project rank and both desktop views:
  - fetch the upstream license text and match its fingerprint;
  - check name-table IDs 13 and 14;
  - store text_url, sha256, checked_on and font_version;
  - record the font file this check used as `font_file` {url, sha256}; Milestone 2 builds previews from it.
- [x] Any font that fails L3 leaves the catalog; rerun the rank.

**Done when:**
- every catalog font is at L3 or has an owner ruling;
- a test shows that a changed license hash puts the font back in the queue.

*(Met 2026-09-29: 497 catalog fonts are at L3, and Cascadia Code, Cascadia Mono and IBM 3270 are kept by owner rulings (`data/reviews/l3/2026-09-28.toml`); `tests/pipeline/test_license_l3.py::test_license_hash_change_requeues`. Later that day, in a scratch run of the 2026-09-26 snapshots with the owner's license rulings and the new texts of `config/license-texts.toml`: FreeFont (its README, a researched text holding the GPL grant and the font exception), ET Book, Go and Liberation Sans and Serif pass L3; Linux Libertine and Biolinum fail it, because their name IDs 13 and 14 name the GPL beside the OFL-1.1 the owner ruled, and wait in the L3 queue for the owner.)*

**Parallel:** Google families in one automatic batch; non-Google upstreams split across agents.

### Step 13: Confidence and stability
**Who:** Claude. **Depends on:** 6b.
- [x] 200 weight perturbations plus leave-one-source-out runs, giving ranges, tiers and bands. *(`confidence.perturb` and `tier`; `tests/pipeline/test_confidence.py::test_stage_gives_every_ranked_font_a_range_and_a_tier`.)*

### Step 14: Official download links
**Who:** Claude; the owner approves overrides. **Depends on:** 4, 6a; runs alongside steps 8–13.
- [x] Google families:
  - primary link: the specimen page;
  - designer link: `minisite_url` or `repository_url`, never googlefontdirectory-hg.
- [x] An override list (Inter, IBM Plex, JetBrains Mono, the Adobe Source families), approved by the owner. *(All 32 overrides in `config/link-overrides.toml` are approved in `data/reviews/links/`, the last on 2026-09-29.)*
- [x] Non-Google fonts: *(2026-09-29: `links --check` on the real store passes all 500 catalog families with 0 failures; its only warnings are two designer links, Grandstander's 404 and Vollkorn's certificate mismatch.)*
  - link the designer's homepage or the repository's releases page;
  - never a release asset, `/releases/latest` or an aggregator (open-foundry.com counts as one: owner ruling of 2026-09-26);
  - web.archive.org counts as an aggregator for every automatic pick, but an override the owner approves may link a timestamped Wayback Machine capture of the designer's page when that page is gone, marked and labelled as archived (owner ruling of 2026-09-29);
  - auto-accept only when two sources agree, or when the owner-approved foundry list (`config/foundries.toml`, gate C3) gives the link (owner ruling of 2026-09-26);
  - an `http://` homepage is upgraded to `https://` and checked monthly (owner ruling of 2026-09-26).
- [x] Nerd Font build link (owner rulings of 2026-09-28 and 2026-09-29, TASK-2): for every family with a Nerd Font build, whether a Nerd Fonts project build (`fonts.json`; alias rows with relation `build`, detail `nerd`) or the maker's own NF build, set `links.nerd` to that build's own page: the build's folder in the Nerd Fonts repository at the current release tag, or the maker's release page for a maker-built one. Never a release asset or `/releases/latest`. Its label names the build (for example "SauceCodePro Nerd Font"), and the monthly link check covers it. *(The 2026-09-29 ruling replaces the 2026-09-28 ruling's one shared releases page. Built 2026-09-29 and checked on the real store on 2026-09-30: 70 catalog fonts have a Nerd link, 60 to their folder in the Nerd Fonts repository at v3.5.1 and 10 to the maker's releases page (Cascadia Code's and Mono's among them), each labelled with the build's name, none a release asset or `/releases/latest`, and `links --check` passes all 500 catalog families. The code: `links.nerd_builds` (the maker's own build first, then the Nerd Fonts folder at the newest release tag of the `nerd_releases` records, only with a base license `licenses.toml` allows), `policy_problems(nerd=True)`, and the stage check and `links --check` covering the Nerd links; `tests/pipeline/test_links.py`.)*

**Done when:** every catalog font has a primary link that follows the policy and returns HTTP 200. *(Met 2026-09-29: `links --check` passes all 500 catalog families on the real store, and every primary follows the policy, Heavy Data's and Monofur's as approved archived overrides.)*

**Parallel:** batch the link checks.

### Step 15: catalog.json schema, validation and site export
**Who:** Claude. **Depends on:** 13, 14.
- [x] A JSON Schema for `catalog.json` (methodology §7), with the hard-failure checks from §9 wired in. The rank keys are versioned constants. *(`schemas/catalog.schema.json`; `validate.CHECKS` runs every §9 hard failure as refresh's last stage; `export.RANK_KEYS` is pinned to the schemas by `tests/pipeline/test_export.py::test_rank_keys_are_versioned_constants`; `tests/pipeline/test_validate.py`.)*
- [x] `catalog-site.json` for the filterable list, with these fields per font: *(`schemas/catalog-site.schema.json`, written by `export.py`. The gate-held and no-deliberate-evidence flags sit in each rank entry (`gate_held`, `unranked`), as `docs/catalog-schema.md` says, and `flags[]` holds `too_new`; `tests/pipeline/test_export.py`.)*
  - id, family, category, is_monospace;
  - formats, Latin coverage;
  - license {spdx, class, redistributable, attribution_required, text_url};
  - preview_ok, links (primary, designer and the nullable `nerd` of the TASK-2 ruling), preinstalled_on, pulled_in_by;
  - rank or band, `order`, tier and 5–95% range for each published rank;
  - `aliases[]` (old names and build names, for search and matching);
  - per source: {state, rank_in_source}, only as far as the step 3 terms ruling allows;
  - `flags[]`: held back by the two-group gate, no evidence of deliberate installs, too new;
  - `preview` {path, sha256} for fonts with a preview.

  At the top level, the file also carries the run date, method version, stale sources with their data dates, the data license and the source credits. Milestone 2's step 2 reviews this list before it is frozen.
- [x] Each rank entry in `catalog-site.json` also carries the rank's `score`, the engine's fused score, before step 20's freeze (site rulings of 2026-09-29, `score_field_timing`; Milestone 2 shows it as a 0–100 bar). *(A number for every ranked entry, and a number or null for an unranked one, as in `catalog.json`; `schemas/catalog-site.schema.json`, `export.py`; `tests/pipeline/test_export.py::test_site_keeps_only_the_site_fields`. In the committed outputs from the rebuild of adc28e0.)*
- [x] `names.json`: the names and aliases of every eligible family in the universe, not only the catalog. Milestone 3's matching uses it, so a real font outside the catalog is recognised instead of showing up as a near-match. *(`build/names.json`: the 1,750 families that pass the gates, 500 of them in the catalog, plus 1,692 ineligible names with their reasons; `tests/pipeline/test_export.py::test_names_lists_every_eligible_family`.)*
- [x] A monthly diff (entries, exits, big moves, license changes) and the flags, written to `build/review.md`. *(`review.py`; the what-if table stays in the private review pack (owner ruling of 2026-09-26); `tests/pipeline/test_review.py`. The first run has no diff yet.)*
- [x] `docs/catalog-schema.md` documents both files. *(It covers `catalog.json`, `catalog-site.json` and `names.json`, with the validation checks.)*

**Done when:** the three files validate in CI and the hard checks pass on the real run. *(Met 2026-09-30: CI's `test` job runs `tests/pipeline/test_validate.py::test_committed_build_outputs_validate` on every pull request, and its `site-real` jobs run `tff-catalog validate --committed`, green on [#23](https://github.com/byronshock/trulyfreefonts/pull/23) to [#27](https://github.com/byronshock/trulyfreefonts/pull/27); validate's hard checks pass on the real run.)*

### Step 16: First full run and owner review of the top lists
**Who:** both. **Depends on:** 15.
- [x] Claude prepares a review pack: *(`build/review-pack/`, rebuilt by every refresh; the owner reviewed round 1 from it in chat on 2026-09-29)*
  - the top 100 of each rank, with tiers and per-source ranks;
  - fonts held back by the gate;
  - tier-C fonts;
  - the comparison with the old Top 100;
  - *most chosen*-versus-project disagreements;
  - anomalies;
  - the what-if table.
- [x] The owner reviews and edits `ranking.toml`, the aliases or `preinstalled.toml`; Claude reruns (up to 3 rounds). *(2026-09-29: round 1 is ruled (`data/reviews/review/2026-09-29.toml`) and implemented: Nerd Fonts is its own independence group, and 22 catalog fonts get owner categories in `config/category-overrides.toml`. The real-store rebuild followed the same day (replays byte-identical, validate green), and the owner approved the lists in round 2 (R2).)*

**Done when:** the owner approves the lists. *(Met 2026-09-29: the owner approved the lists in round 2, R2 in `data/reviews/review/2026-09-29.toml`.)*

### Step 17: Record decisions
**Who:** both. **Depends on:** 16; runs alongside step 18.
- [x] AUTHORITY.md gets dated entries for the final method, weights, license rulings and Latin policy. *(2026-09-29: "Final method (Milestone 1 step 17)" names the method version, the weights in `config/ranking.toml`, and where the license and Latin rulings live; each ruling keeps its own dated entry.)*
- [x] `docs/ranking-methodology.md` becomes the public text, starting from its plain-words section. *(2026-09-29: status "published"; it opens with §1, and the Step 0 decision markers and the options already decided are gone. The section numbers, which the code and `/methodology/` use, are unchanged.)*
- [x] Confirm every completed item in this checklist is already ticked. *(2026-09-29: every item of steps 1–15 was checked against the code, the tests and the real run's outputs, and ticked with its evidence or left open with what's left. Step 9's owner item is open again: one ruled alias row is missing.)*

**Done when:** the changes are merged to `main`.

### Step 18: One-command refresh
**Who:** Claude. **Depends on:** 15.
- [x] `uv run tff-catalog refresh` runs fetch → map → correct → rank → confidence → links → export → `review.md` → validate, so validate's scan of the committed reports covers this run's `review.md`. *(First real run 2026-09-26: 27 stages in 44 min with the snapshots already fetched; review ran before validate.)*
- [x] `--from-snapshots <date>` replays a run offline. *(2026-09-26, with the network blocked: 99 s, and every file in `build/` and `data/` came out byte for byte the same. A replay in a clean clone is still open for the done-when.)*

**Done when:** a clean clone produces identical output from the same snapshots, and the run time is recorded. *(Met 2026-09-29: a clean clone of `m1/wave1` at 4df41d3, replaying a copy of the store at 928ba2d with a fresh HOME, TMPDIR and TFF_RAW (no font cache) and the network blocked (`unshare -rn`, `uv run --offline --frozen`), ran 21 stages in 118.5 s (119 s wall) on a Ryzen 9 9950X with 32 threads, under load 10–11 from other jobs. Every committed file in `build/` and `data/` came out byte-identical except `catalog.json`'s `run.code_commit`, which names the checkout that ran it; all 500 specimens were kept from `build/specimens/index.json`. With the font cache the run took 121.9 s and gave the same bytes. Compare with `git diff --exit-code -I'"code_commit": "[0-9a-f]{40}",?$'`. So that `code_commit` names a commit that reproduces the outputs, code changes are committed before the rebuild whose outputs are committed. Still open, under step 3: the real-refresh variant of the two-runs test, whose `[real]` cases xfail.)*

### Step 20: Handoff to Milestone 2
**Who:** both. **Depends on:** 17, 19.
- [x] Before the freeze, each rank entry carries `previous_score`, last month's published score, so the rising and falling markers of Backlog TASK-4 need no schema change later; until a monthly refresh is merged it equals `score` (owner ruling of 2026-09-30, `score_previous_bootstrap`). *(Both schemas; `export.py` and the state file `published_scores.json`; `validate`'s `previous_score` checks, in a run and with `--committed`; `tests/pipeline/test_export.py` and `test_validate.py`.)*

</details>
