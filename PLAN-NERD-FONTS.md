# Plan: a Nerd Fonts link beside the official one (TASK-2)

This is the working checklist for Backlog task TASK-2 (`backlog task view TASK-2`). Settled decisions are in [AUTHORITY.md](AUTHORITY.md); the method is in [ranking-methodology.md](docs/ranking-methodology.md).

Many developers install the Nerd Fonts build of a coding font instead of the original. About 59% of Homebrew font installs are Nerd builds (ranking-methodology.md, known biases), and D7 already counts those installs toward the original family. This investigation asks whether a font that has a Nerd build should also get a Nerd Fonts link beside its official one. It ends in findings and a recommendation; the owner rules. Nothing is built here.

Limits that already apply:
- M1 step 14 never allows a release asset, `/releases/latest` or an aggregator as a font's primary link.
- The site data has room for an official link and an optional designer link only (`links.primary`, `links.designer` in `schemas/catalog-site.schema.json`).
- Rules 1–4 and D3 in AUTHORITY.md decide which licenses qualify.

**How to read each step:**
- **Who:** the owner, Claude, or both.
- **Depends on:** the steps that must finish first. "M1 step N" and "M2 step N" are in the milestone checklists in `docs/`.
- **Done when:** what must be true before the step is ticked.
- **Known so far:** facts gathered on 2026-09-28 while writing this plan. They are leads, not findings: check each before relying on it.

Tick each item as soon as it is done and verified. Checklists nest: a parent is ticked only when every item under it is. If an item is only partly done, leave it unticked and note what's left. Findings go under [Findings](#findings), one part per step.

**Order:** 1 → (2, 3 and 4, alongside each other; 4's wording waits for 2 and 3) → 5 → 6. Step 5 is the one with a deadline.

TASK-2 moves to In Progress in the pull request that starts step 1, after reading `backlog instructions task-execution`. It moves to Done in the pull request that records the ruling.

---

### Step 1: Which fonts have a Nerd build
**Who:** Claude. **Depends on:** nothing. M1 steps 4 and 7 will automate this later; the spike does it by hand.
- [ ] Fetch Nerd Fonts' `fonts.json` at a release tag, not `master`, and record the tag, URL, date and sha256. Credit it (MIT) under the table in Findings, as ruling T1 asks of real data in the public repo.
- [ ] Map every entry to a family:
  - [ ] record `unpatchedName`, `patchedName`, `folderName`, `caskName`, `licenseId`, `RFN`, `RFNException` (the permission link for a kept name), `repoRelease` and `isMonospaced`;
  - [ ] match `unpatchedName` to the family's catalog name, by hand for now. Note in Findings that this table stands in for the alias table, which stays empty until M1 step 7, so TASK-2's first acceptance criterion is checked against it;
  - [ ] cover the renamed builds (such as SauceCodePro, BlexMono, CaskaydiaCove, Hasklug, LiterationMono, Terminess) and the shortened names (such as DejaVuSansM, FantasqueSansM, RecMono);
  - [ ] cover folders with more than one family, and families split across folders (IosevkaTerm and IosevkaTermSlab; Ubuntu Sans with UbuntuSansMono, which the readme calls UbuntuSansM; Cascadia Code and Cascadia Mono);
  - [ ] mark Symbols Only as ineligible: it is an icon font, and the ranking already drops it.
- [ ] Check each original against Rule 2 (Latin, D4). Flag dual-script ones such as D2Coding and M+ for M1 step 5's dual-script review sheet; TASK-2 doesn't rule on them.
- [ ] List the Nerd builds that font makers publish themselves (Cascadia Code NF, Monaspace NF, Maple Mono NF, Monocraft), which `fonts.json` leaves out or covers twice.
- [ ] Mark catalog membership against the rough top-50 preview from M1 step 0, until the real catalog exists (M1 step 12). Step 6 puts a recheck on the first full run into the build work.
- [ ] Keep the table in a shape M1 step 7 can reuse as `build` alias rows (detail `nerd`). Don't edit `data/aliases.csv` here.

**Done when:** every `fonts.json` entry and every maker-built Nerd build maps to a family or is marked ineligible with a reason, each has its Latin check and its place (or none) in the top-50 preview, and the table is in Findings with its credit.

**Known so far:**
- The latest release is v3.5.1 (2026-08-21). `bin/scripts/lib/fonts.json` lists 72 entries. The `fonts.json` at the repo root is a symlink to it, and its raw URL returns only the link path.
- 16 builds are renamed because the original's license reserves its name: 15 under the OFL's Reserved Font Name, and BitstromWera under the Bitstream Vera license. Envy Code R and Mononoki kept their names with the holder's permission (`RFNException`).
- `data/aliases.csv` holds only its header, and no code reads `fonts.json` yet.

### Step 2: Do the Nerd builds pass Rules 1–4?
**Who:** Claude; the owner rules on the queue. **Depends on:** 1.
- [ ] The base font, for each build from step 1:
  - [ ] compare `licenseId` with the original's license (M1 step 6a's class once it exists, the upstream license until then);
  - [ ] check that the license file inside the release archive matches;
  - [ ] check Rules 1–4 and D3: any use, commercial included; redistribution recorded (Rule 3); no ITF license (Rule 4); no copyleft without a font exception, and no ban on modification or embedding (D3);
  - [ ] check that builds under a reserved font name were renamed, and record the permission links (`RFNException`) for the two that kept their names.
- [ ] The icon sets patched into every build:
  - [ ] list each set with its version, the license Nerd gives it (in `src/glyphs/README.md` and in `license-audit.md`, which can differ), the license file in `src/glyphs/` where there is one, and the upstream repository's own license;
  - [ ] check each set against Rules 1–4 and D3;
  - [ ] settle where the sources disagree:
    - [ ] Font Awesome: Nerd says CC BY 4.0 for the icons, but the font file is OFL with the reserved name "Font Awesome";
    - [ ] Powerline Symbols: MIT in the README and the license file, "Free License" in `license-audit.md`;
    - [ ] Font Logos: "unlicensed", or the Unlicense;
    - [ ] Material Design: Apache 2.0, with some icons under other licenses;
    - [ ] Pomicons: the license file says OFL 1.1 (Gabriele Lana, 2021), but the font file's copyright line says "All rights reserved" (Davide Bignotti, 2014);
  - [ ] decide whether the CC BY 4.0 sets mean every Nerd build needs the "attribution required" badge and a credit line (D3), and draft that credit;
  - [ ] check whether the archives carry the icon licenses and credits (so far only a README table names them), and what that means for the redistributable flag.
- [ ] The combined font:
  - [ ] Nerd Fonts' LICENSE says patched fonts are OFL-1.1, which conflicts with builds that ship a non-OFL base license. Record this.
  - [ ] State what merging CC BY, Apache and MIT glyphs into an OFL font means under Rules 1–4. Mark anything that needs a legal view instead of guessing.
- [ ] A queue for the owner: each build or icon set that fails or is unclear, with the license text, a recommended ruling and the reason.

**Done when:** every build from step 1 has a verdict (passes, fails, or needs a ruling) covering its base font and its icons, and the queue is ready for step 6.

**Known so far:**
- Each archive ships the original font's license file. No icon license texts are in the archives.
- Base licenses to look at:
  - BigBlue Terminal is CC BY-SA 4.0, which D3 excludes;
  - Heavy Data (LicenseRef-VicFieger) and Monofur are freeware texts with no grant to modify;
  - Ubuntu, Ubuntu Mono and Ubuntu Sans use the Ubuntu Font Licence. AUTHORITY.md's license filter already groups it with the open font licenses, but no ruling yet says it passes Rules 1–4 (`config/licenses.toml` has only a commented example);
  - the Bitstream Vera family covers BitstromWera, DejaVu Sans Mono, Hack ("Bitstream-Vera AND MIT") and OpenDyslexic;
  - also Gohu (WTFPL), Go Mono (BSD-3-Clause-Clear), IBM 3270 (BSD-3-Clause), and the dual licenses of Overpass and Monoid.
- Icon licenses, per Nerd's table:
  - CC BY 4.0: Codicons and Font Awesome;
  - MIT: Devicons, Octicons, Seti-UI, Powerline Extra, Powerline Symbols, IEC Power Symbols, Font Awesome Extension and the Hack extra glyphs;
  - Apache 2.0: Material Design;
  - OFL 1.1: Pomicons (with a reserved name) and Weather Icons;
  - "unlicensed": Font Logos.
- Only 8 of the 14 icon sets have a license file in `src/glyphs/`. Devicons, the Hack extra glyphs, Font Awesome Extension, Font Logos, Seti-UI and IEC Power Symbols have none.
- `config/licenses.toml` has no entries yet (M1 step 2).

### Step 3: Where the link goes
**Who:** Claude; the owner rules. **Depends on:** 1.
- [ ] Test each candidate against M1 step 14: a page, never a release asset, `/releases/latest` or an aggregator.
  - [ ] the build's folder in the Nerd Fonts repository, `https://github.com/ryanoasis/nerd-fonts/tree/<ref>/patched-fonts/<folderName>`, whose README gives the variants, the icon credits and download links;
  - [ ] nerdfonts.com's download page: one page for every font, with no anchor per font, and each Download button is a direct file;
  - [ ] a release's tag page, which lists every font's files rather than one font's;
  - [ ] the Homebrew cask page on formulae.brew.sh, a package index;
  - [ ] for maker-built Nerd builds, the maker's own release page.
- [ ] Propose a meaning for "aggregator", which M1 step 14 uses but never defines. For example: a site that lists or re-hosts fonts it neither makes nor patches. Under that meaning, say whether Nerd Fonts' page for its own build counts, and whether formulae.brew.sh does.
- [ ] Choose the ref for GitHub links:
  - [ ] `master` stays current, but paths can change (Nerd's readme warns about unstable paths);
  - [ ] a release tag is stable, but goes stale at each release unless the monthly refresh rewrites it from the release collector (M1 step 8);
  - [ ] check that every folder URL returns HTTP 200 on the chosen ref.
- [ ] Maker-built Nerd builds: decide whether the link goes to the maker's build, Nerd Fonts' build or both, and whether the official link already covers it (Cascadia's release zip holds both).
- [ ] Propose how a link is accepted. M1 step 14 auto-accepts only when two sources agree; say which two apply here (for example `folderName` in `fonts.json` and the Homebrew cask).
- [ ] Propose the link text:
  - [ ] it names its destination (M2 steps 4 and 6). Without a `label`, the site's naming rule in `site/CONTRACT.md` prints "GitHub: ryanoasis/nerd-fonts", which says nothing about the build;
  - [ ] it names the build as users know it, such as "SauceCodePro Nerd Font", so a renamed build is recognisable;
  - [ ] draft two or three wordings for step 6.

**Done when:** a recommended target, ref, meaning of "aggregator", acceptance rule, rule for maker-built builds and link text are in Findings, with each alternative's drawback.

**Known so far:**
- nerdfonts.com has no page per font; `/font-downloads/source-code-pro` returns 404.
- All 72 `folderName`s exist at v3.5.1, and the folder URL returned HTTP 200 on `master` and on `v3.5.1` for the folders checked. The 17 folders whose entry has `repoRelease: false` (such as CascadiaMono, Monaspace and UbuntuSans) hold only a README, with no font or license files.

### Step 4: Where the link shows and what it says
**Who:** Claude; the owner rules. **Depends on:** 1; 2 for the icon credit; 3 for the wording.
- [ ] Compare the options:
  - [ ] the details panel only, on every view;
  - [ ] the row and the details panel, on every view;
  - [ ] the row on Coding and Developers & apps only, and the details panel on every view.
- [ ] Check each option against:
  - [ ] the row: it carries one `download` link today, and rows must not change height after load (`content-visibility: auto`, `site/CONTRACT.md` section 4). Nothing tells a row which rank is showing. A second row link, or one that shows only on some views, is a contract change;
  - [ ] the details panel: it gets the catalog's font object minus `preview` and `font_file` (`site/CONTRACT.md` section 8), so a new field under `links` reaches it with no payload change;
  - [ ] the size budgets in `src/tff_site/budgets.py` (list page, list index, details payload);
  - [ ] non-monospace Nerd builds, which never appear on Coding (monospace only): the five entries with `isMonospaced: false` (Arimo, Tinos, Ubuntu, OpenDyslexic, Heavy Data), and proportional families inside entries marked true (Ubuntu Sans, Overpass, Noto Sans and Serif, M+ 1 and 2, iA Writer Duo and Quattro);
  - [ ] the official link stays first and plainly the main one;
  - [ ] accessibility: each link's text says where it goes (WCAG 2.4.4 in context; 2.4.9 when read alone), so screen-reader users can tell the two apart.
- [ ] Draft the wording beside the link:
  - [ ] one line on what a Nerd Font is: the original with icons added for terminals and editors;
  - [ ] the icon credit, if step 2 finds one is needed.
- [ ] Check that renamed build names will reach `aliases[]` in `catalog-site.json` (M1 steps 7 and 15), so M2 step 3's alias search finds the original ("SauceCodePro" finds Source Code Pro). If they won't, add it to step 6's build work.
- [ ] Propose a usability task for M2 step 12, for example "You use a Nerd Font in your terminal. Find this font's Nerd build."

**Done when:** a recommended placement, view rule and wording are in Findings, with the contract changes each option needs.

### Step 5: Data format and timing
**Who:** Claude; the owner approves the field. **Depends on:** 2, 3, 4.

**Why now:** M1 step 20 freezes `catalog-site.json` v1, whose fields M2 step 2 approved "as drafted" on 2026-09-25.
- **Now,** `site/CONTRACT.md` already treats the draft schema as a contract, so a new field is a contract change. It is agreed first; then that file, the draft schema, the sample and the tests change in one pull request, and the owner approves the fields again.
- **After the freeze,** nothing says what a change needs. The version string `1.0.0-draft` is hard-coded in `schemas/catalog-site.schema.json` (twice), `src/tff_site/data.py`, `src/tff_catalog/export.py`, the sample, `tests/test_site_contracts.py` and `site/CONTRACT.md`, so a change would also need a new version in every one of those places.
- **Where things stand:** on 2026-09-28, Milestone 1 is at steps 2–3.

- [ ] Propose a field shape, comparing at least:
  - [ ] `links.nerd`: `null` or a link, like `links.designer`;
  - [ ] `links.builds[]`: a list of {kind, url, label} that later kinds of build could reuse without a new version.
- [ ] Say whether the field is required-and-nullable (like `designer`) or optional, given `additionalProperties: false` on `links`.
- [ ] Say whether the field needs room for the build's license or credit (step 2), which `$defs/link` (only `url` and `label`) lacks.
- [ ] List every file the change touches:
  - [ ] `schemas/catalog.schema.json` and `schemas/catalog-site.schema.json`;
  - [ ] `docs/ranking-methodology.md`, which lists `links` {primary, designer};
  - [ ] `src/tff_catalog/links.py`: the `Links` dataclass, and `policy_problems`, `choose` and `check` (the monthly check covers only the primary and designer links);
  - [ ] the `links.json` stage file in `src/tff_catalog/stageio.py` (a frozen stage contract);
  - [ ] `src/tff_catalog/export.py`;
  - [ ] `src/tff_site/linkcheck.py`, a stub whose docstring names only the license text, primary and designer links;
  - [ ] `site/CONTRACT.md`, and its row context if the placement changes the row;
  - [ ] `config/site.toml` and `SiteConfig` in `src/tff_catalog/config_model.py`, if the line on what a Nerd Font is or the icon credit is site wording. Export copies it into `catalog-site.json`, so it freezes too;
  - [ ] `tests/fixtures/catalog-site.sample.json` (a Nerd link on one or two sample fonts) and `tests/test_site_contracts.py`;
  - [ ] `docs/catalog-schema.md` (M1 step 15; not written yet).
- [ ] State whether the change must land before the freeze, and by which step: before M2 step 2's fresh approval, and in any case before M1 step 20.
- [ ] Say whether to add the field before the ruling on the link itself: an empty optional field costs little, and avoids a new data version if the answer is later yes.
- [ ] Note for the owner that nothing defines what "frozen" allows. Offer an item under M1 step 20 that says so (ask first).

**Done when:** the field shape, the file list and a deadline tied to a named step are in Findings.

### Step 6: The owner's ruling
**Who:** both. **Depends on:** 1–5.
- [ ] Put a summary at the top of Findings: one recommendation per step, with the alternatives and their drawbacks.
- [ ] Ask in chat, in batches of up to four single-select questions, each with a recommended answer:
  - [ ] a Nerd link at all, or not;
  - [ ] the target and ref (step 3), and the meaning of "aggregator";
  - [ ] the placement, views and wording (step 4);
  - [ ] the field shape and when it lands (step 5);
  - [ ] each case in step 2's license queue, and the icon attribution question;
  - [ ] maker-built Nerd builds.
- [ ] Record the answers:
  - [ ] the ruling in AUTHORITY.md with its date, including any meaning of "aggregator" as a clarification of M1 step 14;
  - [ ] the rulings in `data/reviews/` with the date: licenses in `licenses/` (gate LIC), link rules in `links/` (gate K), and wording and any new field in `site/` (gate SITE);
  - [ ] the wording in `config/site.toml`;
  - [ ] if a field is added, a fresh approval beside M2 step 2's ticked item and in AUTHORITY.md's "Site data fields" entry.
- [ ] If the answer is yes, put the build work where it belongs:
  - [ ] M1 step 4: the `fonts.json` collector keeps `folderName`, `patchedName` and `caskName`;
  - [ ] M1 steps 6a and 6b: recheck each linked Nerd build's base and icon licenses at every refresh, and hide its link while a change waits for a ruling;
  - [ ] M1 step 14: a Nerd link item (target, label, acceptance rule, monthly link check);
  - [ ] M1 step 15: the new field in the `catalog-site.json` field list;
  - [ ] M1 step 16: recheck which catalog families have a Nerd build on the first full run;
  - [ ] M2 steps 3 and 4: the rendering the placement ruling chose; M2 step 12: the usability task;
  - [ ] new Backlog tasks only for work no checklist covers.
- [ ] If the answer is no, record it in AUTHORITY.md with the reason.
- [ ] If the owner defers (for example, a case needs a legal view), leave TASK-2 In Progress with the open questions in its notes. Still ask whether an empty field goes in before the freeze (step 5), and if so, add it to M1 step 15.
- [ ] Close TASK-2 with the `backlog` CLI, after reading `backlog instructions task-finalization`:
  - [ ] check its six acceptance criteria against this file;
  - [ ] write the final summary;
  - [ ] set it to Done in the same pull request as the ruling.

**Done when:** the ruling is merged in AUTHORITY.md, the build work is in the checklists or Backlog, and TASK-2 is Done.

---

## Findings

Empty until the work starts. One part per step.

### 1. Which fonts
### 2. Licenses
### 3. Link target
### 4. Placement and wording
### 5. Data format and timing
### 6. Ruling
