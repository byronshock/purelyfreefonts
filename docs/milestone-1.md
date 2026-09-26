# Milestone 1 checklist: build catalog.json (about 500 families)

Milestone 1 builds the ranked, license-checked catalog that the filterable list (Milestone 2) will publish. The method is in [ranking-methodology.md](ranking-methodology.md), and settled decisions go in [AUTHORITY.md](../AUTHORITY.md).

**How to read each step:**

- **Who:** the owner, Claude, or both.
- **Depends on:** the steps that must finish first.
- **Done when:** what must be true before the step is ticked.
- **Parallel:** where Claude can run several agents at once.

Tick each item as soon as it is done and verified. If an item is only partly done, leave it unticked and note what's left.

**Critical path:** 0 → 1 → 2 → 3 → 4 → (5, 5b, 6a and 7, alongside 8) → 9 → 10 → 11 → 12 → 6b → 13 → 15 → 16 → (17 and 18) → 19 → 20. Step 14 runs alongside steps 8–13.

---

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
- [ ] Layout: *(the directories exist; their files come in step 2 (`config/`), step 7 (`aliases.csv`) and step 15 (`build/`))*
  - `config/`: `ranking.toml`, `licenses.toml`, `preinstalled.toml`, `foundries.toml`;
  - `data/`: `aliases.csv`, `reviews/` (owner rulings);
  - `state/` (see step 3);
  - `build/`: `catalog.json`, `catalog-site.json`, `review.md`.
- [x] `LICENSE` (MIT) for code and `LICENSE-DATA` (CC BY-SA 4.0) for the catalog, per D17. The data license is marked provisional. *(Made final 2026-09-25 by ruling T5.)*
- [x] A README with source credits.
- [x] CI on every pull request: `uv run ruff check`, `uv run pytest`, and a secret scan (gitleaks). *(`.github/workflows/ci.yml` has the `lint`, `test` and `secrets` jobs; the first run on GitHub passed on [#6](https://github.com/byronshock/trulyfreefonts/pull/6).)*
- [x] Claude, with gh (owner-approved 2026-09-25): branch protection for `main` requiring the CI checks, with **0 required approvals**, because the owner can't approve their own pull requests. *(Checks lint, test, secrets; 0 approvals.)*
- [x] Confirm `.gitignore` still excludes `PLAN.md`, `ops/SERVER.local.md` and `.claude/settings.local.json`.

**Done when:** a pull request with one trivial test passes CI, and both license files are on `main`.

### Step 2: Config files
**Who:** Claude writes them; the owner edits values. **Depends on:** 0, 1.
- [x] `ranking.toml` holds every parameter in the methodology (the §5 tables and §8). It is validated on load, and unknown or missing keys fail. *(Done 2026-09-25, with the rulings' gate values; `tests/test_config.py`.)*
- [ ] `licenses.toml` sorts licenses into allowed, excluded and owner-ruling classes per D3. Anything not listed is excluded.
- [ ] `preinstalled.toml`, seeded from the research and reviewed by the owner. Seeds:
  - Linux defaults;
  - Cascadia on Windows 11;
  - the macOS list;
  - GNOME's Adwaita fonts;
  - Android;

  Each entry names its system. Only Linux entries trigger abstentions; Windows, macOS and Android entries only add `preinstalled_on` tags.
  - the LibreOffice bundle.
- [ ] `foundries.toml` is a hand list of the families of League of Moveable Type, Velvetyne, Collletttivo, Open Foundry and Roundo. Claude seeds it once from the foundry sites, and the owner reviews it with `preinstalled.toml`; the sites are not scraped each month (ruling M12).
- [x] `tff-catalog config` prints the effective config and its hash. *(Done 2026-09-25; the hash is stable across runs.)*

**Done when:** the loader tests pass and the owner has reviewed `preinstalled.toml` and `foundries.toml`.

### Step 3: Collector framework, snapshots, run state and source terms
**Who:** Claude; the owner rules on the terms table and posts the Fonts Over Time request. **Depends on:** 1 and D15.
- [ ] A shared fetcher:
  - User-Agent `trulyfreefonts-catalog/<version> (+https://github.com/byronshock/trulyfreefonts)`, never a personal email;
  - retries with backoff;
  - per-host rate limits;
  - conditional GETs.
- [ ] Snapshot store per D15:
  - font-relevant extracts plus a manifest (url, sha256, fetched_at, status) in `<store>/<source>/<YYYY-MM-DD>/`;
  - big raw files expire after the run;
  - a size check.
- [ ] Run-state design:
  - `state/` on `main` holds membership counters, first_seen, license hashes, stale counters, last published ranks and snapshot baselines;
  - state advances only when a refresh pull request is merged;
  - a new run replaces an unmerged refresh pull request;
  - test: two runs without a merge give the same state as one run.
- [ ] Stale-data policy: reuse the last snapshot for up to 2 months, flagged.
- [x] Terms audit, one row per source, recording whether raw values and fixtures may be republished. The result goes in `docs/sources.md`. *(Rulings T1–T5 given 2026-09-25, in `data/reviews/terms/`; `docs/sources.md` written and its links and quotes checked 2026-09-25. Its open points still need the owner: ecosyste.ms terms §9 and §11(c), and Google shares kept in `state/smoothing.json`.)* Rows:
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

**Done when:**
- fetching one real source twice leaves exactly one snapshot per date, with no duplicates;
- the state test passes;
- the owner has ruled on the terms table;
- the data license is final.

**Parallel:** split the terms audit by source.

### Step 4: Candidate universe
**Who:** Claude. **Depends on:** 2, 3.
- [ ] Collectors for:
  - Google Fonts metadata;
  - the google/fonts tree and METADATA.pb files (license folder, repository, minisite);
  - Fontsource;
  - Nerd Fonts `fonts.json`;
  - Homebrew `cask.json`;
  - Fontist formulas;
  - `foundries.toml`.
- [ ] Each family gets a stable `id`, minted from its name at first sighting and never changed. Renames only add aliases. Source memberships and upstream URLs are recorded.
- [ ] Drop non-text families, each with a reason code.

**Done when:**
- a universe report shows every name mapped to a family, with per-source counts and drop reasons;
- ids are identical across two runs;
- a rename test keeps the id.

**Parallel:** one agent per catalog collector.

### Step 5: Latin filter
**Who:** Claude; the owner signs off the allowlist. **Depends on:** 4 and D4.
- [ ] Google families: apply rule A (expect 1,264).
- [ ] Dual-script review sheet: about 30 candidates, sorted by Google year views, with the Latin-language counts from each family's metadata. The owner marks the allowlist.
- [ ] Non-Google fonts:
  - fetch one Regular file per candidate;
  - test it against the GF glyphsets with fontTools;
  - cache the result by file hash;
  - record coverage (basic or latin-ext).
- [ ] Map CJK builds to their Latin parent at `cjk_build_credit`, and exclude families that are mainly CJK.

**Done when:**
- every family has a `latin` field;
- the allowlist is approved;
- spot checks pass: Inter and Iosevka are in; Pretendard and LXGW WenKai are out.

**Parallel:** batch the file tests across agents.

### Step 5b: Font facts
**Who:** Claude. **Depends on:** 4; runs alongside step 5.
- [ ] For every family, including non-Google ones:
  - `category`;
  - `is_monospace` (from `post.isFixedPitch` or panose when no metadata exists);
  - `formats` {variable, static}.

  Sources: GF metadata, Fontsource, or the font's own tables, cached by file hash.

**Done when:** every family has all three fields, and spot checks pass: JetBrains Mono is monospace; Inter is sans and variable.

### Step 6a: License classification (before ranking)
**Who:** Claude; the owner rules on the queue. **Depends on:** 2, 4.
- [ ] L1: map every license string to an SPDX ID through `licenses.toml`.
- [ ] L2: cross-check the google/fonts folder, Fontsource, Fontist, Nerd Fonts, Debian DEP-5 and Arch.
- [ ] Per font, provisionally: class, redistributable, attribution_required and preview_ok (per D3).
- [ ] Review queue for NOASSERTION results, custom texts and disagreements (DejaVu, Hack, Cascadia, OpenDyslexic, URW, Roboto Mono). Owner rulings are saved in `data/reviews/`.

**Done when:** every candidate has a provisional class, and the queue is empty.

### Step 7: Alias table
**Who:** Claude; the owner reviews flagged rows. **Depends on:** 4.
- [ ] `data/aliases.csv`, with columns: alias, family_id, relation, source, first_seen, reviewed_by. Relations:
  - rename, build, package, postscript;
  - sibling;
  - related;
  - **ineligible**, with a reason code (proprietary, ITF, CJK, icon, generic, system).
- [ ] Mine aliases from:
  - google/fonts delisted directories and git history (never pair through googlefontdirectory-hg);
  - Fontsource's legacy names;
  - Nerd Fonts `fonts.json`, keeping one authoritative list of Nerd casks;
  - Homebrew `old_tokens` and `cask_renames`;
  - Arch and Debian package names;
  - ~~a hand-made table of about 60 Chocolatey ids;~~ *(not needed: Chocolatey is dropped from v1, ruling T3)*
  - a GitHub repo-to-family table with a main-download-channel flag;
  - upstream name tables (Inter Variable, Inter Display).
- [ ] Sibling rules that block false matches: Roboto ≠ Roboto Slab, Fira Sans ≠ Fira Code, Inter ≠ Inter Tight, Noto Sans ≠ Noto Sans JP.
- [ ] Auto-accept only renames from google/fonts history and Nerd `unpatchedName` rows; the owner reviews the rest.

**Done when:** the known-answer tests pass and the review queue is empty.

**Parallel:** one agent per alias source.

### Step 8: One collector per ranking source
**Who:** Claude. **Depends on:** 3; runs alongside steps 5–7. Each collector writes a dated snapshot and parsed values, and has a test built from a fixture the terms ruling allows (synthetic if not).
- [ ] Homebrew analytics for 30, 90 and 365 days. Cask add dates come from a tree diff of `Casks/font` at the window start and end, plus the commits API for added casks (design-m1 C4).
- [ ] Arch pkgstats: monthly shares for the last 12 complete months, for all font-like packages.
- [ ] Dependency data:
  - Arch core/extra `.db`;
  - the CachyOS and EndeavourOS repository databases;
  - Debian `Packages.xz` and popcon `by_inst.gz`.
- [ ] Debian popcon main/fonts.
- [ ] GitHub releases for main-channel repos: every release, with no cap by date, measured as growth between snapshots; for Iosevka, only the latest 24 releases, through GraphQL (ruling M2). All within a per-run GitHub API budget.
- [ ] Nerd Fonts releases.
- ~~Chocolatey OData.~~ *(Dropped from v1: its terms forbid automated access; ruling T3.)*
- [ ] Fonts Over Time weekly JSONL and `latest.csv`, with a column check that marks the source stale on mismatch.
- [ ] Web Almanac 2025 sheets: find the header row, validate the columns, and pin the sheet id and tabs per edition in config.
- [ ] Google Fonts `/metadata/stats`, falling back to the popularity field.
- [ ] npm downloads for every @fontsource, @fontsource-variable and @expo-google-fonts package: one `downloads/range` request per package over 540 days (last-year total, complete months and first download day), at most 1 request a second with backoff on 429.
- [ ] ecosyste.ms dependent-repository counts for the @fontsource and @fontsource-variable packages only (packages.ecosyste.ms API; ruling M7), credited as CC BY-SA 4.0.
- [ ] Fontsource `/v1/stats` (jsDelivr).
- [ ] Flutter code search: deferred until a token is chosen.

**Done when:** `tff-catalog fetch` runs every collector locally. The Actions run is checked in step 19.

**Parallel:** one agent per collector; the biggest speed-up in the milestone.

### Step 9: Name mapping and unmatched report
**Who:** Claude; the owner reviews the top of the report. **Depends on:** 7, 8.
- [ ] Map every source key to a family id or an ineligible row, and set the four evidence states.
- [ ] `build/unmatched.md` lists keys with no alias row, above each source's floor, sorted by volume. Nothing is guessed.
- [ ] The owner resolves the top entries; new rows go into `aliases.csv`.

**Done when:** no source has an unmatched key in its top 200 (ineligible rows count as resolved), and the known answers pass.

### Step 10: Confound corrections
**Who:** Claude; the owner reviews flagged preinstalled and dependency entries. **Depends on:** 8, 9.
- [ ] For every rank except *most installed* (most chosen, overall, Coding): reverse-dependency abstentions (the largest single dependent at 50% or more, with `a | b` credited to the first alternative and 35–50% flagged; ruling M8), plus abstentions for fonts a Linux system preinstalls. The *most installed* view keeps every count.
- [ ] `preinstalled_on` and `pulled_in_by` tags for each affected font, used by both views.
- [ ] Noise floors (Homebrew, the Arch Nerd Fonts group, Nerd release downloads), bundle, Nerd and CJK credits, and exposure counted from data dates.
- [ ] A per-font correction report, with new cases flagged for the owner.

**Done when:**
- the owner has reviewed the report;
- in *most chosen*, Quicksand and DejaVu abstain on Debian and Hack abstains on Arch; in *most installed*, they count;
- Fantasque Sans Mono and Fira Sans have been checked against CachyOS's dependencies.

### Step 11: Per-survey ranking engine
**Who:** Claude. **Depends on:** 2 for building on synthetic fixtures during steps 4–10; 10 for the real run.
- [ ] Ruler built from Homebrew values after the gates, alias-summing and credits.
- [ ] Equating, and the censored value pinned as in the methodology (§3).
- [ ] Weight scaling for small overlaps.
- [ ] Shrunk mean, outlier guard, the two-group gate for the top 100, and deterministic ties.
- [ ] Property tests:
  - determinism;
  - adding an ineligible font moves nothing;
  - a higher count never lowers a rank, except when the guard changes for that font or for a font that overtakes it, or through the ruler's scale.
- [ ] The worked example (2.53 / 2.15 / 1.98, with the outlier guard on; ruling M1) as a fixture.
- [ ] Cross-checks: coverage-aware RRF (k=60) and the Fontsource ruler.

**Done when:** the tests pass, and both desktop views and the project rank are produced from real data.

### Step 12: Overall rank, views and membership
**Who:** Claude. **Depends on:** 11.
- [ ] Overall rank with split weights (desktop from *most chosen*), shrunk once.
- [ ] Views: Desktop *most installed*, Coding, Developers & apps, categories, Rising (beta).
- [ ] A test that changing any extra view's weights (Coding, Developers & apps, Rising) leaves the overall rank unchanged.
- [ ] A leak test: changing a Linux source's count for a font that abstains in *most chosen* leaves the overall rank unchanged.
- [ ] Catalog membership (the overall top 500 plus the top 100 of the project rank and of both desktop views), with hysteresis counters in `state/`. For the top-100 lists, a font enters at 90 or better and leaves after 2 runs worse than 110 (ruling M11).

**Done when:** every catalog candidate has every published rank it has evidence for; fonts unranked in *most chosen* only because of abstentions are listed with their tag.

### Step 6b: License verification for the catalog (after ranking)
**Who:** Claude; the owner rules on the queue. **Depends on:** 12; on the critical path before step 13.
- [ ] L3, for overall rank 700 or better and the top 150 of the project rank and both desktop views:
  - fetch the upstream license text and match its fingerprint;
  - check name-table IDs 13 and 14;
  - store text_url, sha256, checked_on and font_version;
  - record the font file this check used as `font_file` {url, sha256}; Milestone 2 builds previews from it.
- [ ] Any font that fails L3 leaves the catalog; rerun the rank.

**Done when:**
- every catalog font is at L3 or has an owner ruling;
- a test shows that a changed license hash puts the font back in the queue.

**Parallel:** Google families in one automatic batch; non-Google upstreams split across agents.

### Step 13: Confidence and stability
**Who:** Claude. **Depends on:** 6b.
- [ ] 200 weight perturbations plus leave-one-source-out runs, giving ranges, tiers and bands.
- [ ] A backtest on historical windows (pkgstats series, 18 months of npm, Homebrew 30/90/365 days, Google's windows), used to set the alert thresholds.

**Done when:** every ranked font has a range and a tier, and the backtest report is committed.

### Step 14: Official download links
**Who:** Claude; the owner approves overrides. **Depends on:** 4, 6a; runs alongside steps 8–13.
- [ ] Google families:
  - primary link: the specimen page;
  - designer link: `minisite_url` or `repository_url`, never googlefontdirectory-hg.
- [ ] An override list (Inter, IBM Plex, JetBrains Mono, the Adobe Source families), approved by the owner.
- [ ] Non-Google fonts:
  - link the designer's homepage or the repository's releases page;
  - never a release asset, `/releases/latest` or an aggregator;
  - auto-accept only when two sources agree.
- [ ] A monthly link check.

**Done when:** every catalog font has a primary link that follows the policy and returns HTTP 200.

**Parallel:** batch the link checks.

### Step 15: catalog.json schema, validation and site export
**Who:** Claude. **Depends on:** 13, 14.
- [ ] A JSON Schema for `catalog.json` (methodology §7), with the hard-failure checks from §9 wired in. The rank keys are versioned constants.
- [ ] `catalog-site.json` for the filterable list, with these fields per font:
  - id, family, category, is_monospace;
  - formats, Latin coverage;
  - license {spdx, class, redistributable, attribution_required, text_url};
  - preview_ok, links, preinstalled_on, pulled_in_by;
  - rank or band, `order`, tier and 5–95% range for each published rank;
  - `aliases[]` (old names and build names, for search and matching);
  - per source: {state, rank_in_source}, only as far as the step 3 terms ruling allows;
  - `flags[]`: held back by the two-group gate, no evidence of deliberate installs, too new;
  - `preview` {path, sha256} for fonts with a preview.

  At the top level, the file also carries the run date, method version, stale sources with their data dates, the data license and the source credits. Milestone 2's step 2 reviews this list before it is frozen.
- [ ] `names.json`: the names and aliases of every eligible family in the universe, not only the catalog. Milestone 3's matching uses it, so a real font outside the catalog is recognised instead of showing up as a near-match.
- [ ] A monthly diff (entries, exits, big moves, license changes) and the flags, written to `build/review.md`.
- [ ] `docs/catalog-schema.md` documents both files.

**Done when:** the three files validate in CI and the hard checks pass on the real run.

### Step 16: First full run and owner review of the top lists
**Who:** both. **Depends on:** 15.
- [ ] Claude prepares a review pack:
  - the top 100 of each rank, with tiers and per-source ranks;
  - fonts held back by the gate;
  - tier-C fonts;
  - the comparison with the old Top 100;
  - *most chosen*-versus-project disagreements;
  - anomalies;
  - the what-if table.
- [ ] The owner reviews and edits `ranking.toml`, the aliases or `preinstalled.toml`; Claude reruns (up to 3 rounds).

**Done when:** the owner approves the lists.

### Step 17: Record decisions
**Who:** both. **Depends on:** 16; runs alongside step 18.
- [ ] AUTHORITY.md gets dated entries for the final method, weights, license rulings and Latin policy.
- [ ] `docs/ranking-methodology.md` becomes the public text, starting from its plain-words section.
- [ ] Confirm every completed item in this checklist is already ticked.

**Done when:** the changes are merged to `main`.

### Step 18: One-command refresh
**Who:** Claude. **Depends on:** 15.
- [ ] `uv run tff-catalog refresh` runs fetch → map → correct → rank → confidence → links → export → `review.md` → validate, so validate's scan of the committed reports covers this run's `review.md`.
- [ ] `--from-snapshots <date>` replays a run offline.

**Done when:** a clean clone produces identical output from the same snapshots, and the run time is recorded.

### Step 19: Monthly GitHub Actions workflow
**Who:** Claude builds it; the owner sets up access and merges. **Depends on:** 18 and D15.
- [ ] Owner: let refresh pull requests trigger CI (gate CI1): turn on "Allow GitHub Actions to create and approve pull requests", which the repository currently has off. The refresh job opens the pull request with `GITHUB_TOKEN` and then dispatches `ci.yml` on `refresh/monthly`, because pull requests that `GITHUB_TOKEN` opens trigger no workflows. No GitHub App or personal token is needed.
- [ ] Owner: add the secrets:
  - access to the private snapshot store if D15 is (a), with a deploy key or App;
  - a token for Flutter code search only if that is turned on.
- [ ] The workflow:
  - runs on a monthly cron at an off-minute (for example `17 6 3 * *`) and on `workflow_dispatch`;
  - uses a uv cache and a concurrency group;
  - has write permission for `contents`, `pull-requests` and `issues`.
- [ ] It opens or updates a pull request on the fixed branch `refresh/monthly`, with `catalog.json`, `catalog-site.json`, `review.md`, the `state/` changes, and the alias rows and seeds the run added to `data/`. A hard failure fails the job and opens an issue instead.
- [ ] Check the collectors on GitHub's runners: rate limits, the GitHub API budget, and whether Google's endpoints respond.
- [ ] A watchdog for GitHub's rule that disables scheduled workflows in public repositories after 60 days without activity. It runs outside this repository's schedule (for example a timer on the VPS reading GitHub's public API, which the rule can't disable), and warns the owner if no refresh pull request has appeared for 35 days, or `main` has had no commit for 50 days.

**Done when:** a manual dispatch opens a correct pull request, CI runs on it, and the owner merges it.

### Step 20: Handoff to Milestone 2
**Who:** both. **Depends on:** 17, 19.
- [ ] `catalog-site.json` v1 is frozen, with a sample file and the schema doc, but only after Milestone 2's step 2 has approved its fields.
- [ ] A handoff note lists what Milestone 2 must settle:
  - the default rank order;
  - numbering under filters;
  - the deploy path to the VPS;
  - the methodology page.
- [ ] The runbook lists the manual tasks (methodology §10).
- [ ] "Current step" in AUTHORITY.md moves to Milestone 2, and `docs/roadmap.md` shows the new status.

**Done when:** the owner accepts the handoff.
