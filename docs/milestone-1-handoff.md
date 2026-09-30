# Milestone 1 handoff to Milestone 2

Milestone 1 step 20, 2026-09-30. What Milestone 1 hands over, and where each question it left for Milestone 2 is settled. Settled decisions are in [AUTHORITY.md](../AUTHORITY.md); the checklists have the detail.

## What Milestone 2 receives

- **`build/catalog-site.json`**, frozen at version 1 (`schema_version` `1.0.0`): the 500 fonts of the 2026-09-26 run, the run that `state/` holds as the first published one ([#31](https://github.com/byronshock/trulyfreefonts/pull/31)). A change to a v1 field needs v2 (owner ruling `site_fields_v1`, 2026-09-30).
  - Schema: `schemas/catalog-site.schema.json`. Fields in plain words: [catalog-schema.md](catalog-schema.md). How the site uses them: [site/CONTRACT.md](../site/CONTRACT.md).
  - The sample frozen with it: `tests/fixtures/catalog-site.sample.json`, 40 invented fonts.
- **`build/specimens/`**, one SVG specimen per catalog font.
- **`build/names.json`**, for Milestone 3's matching. It and `catalog.json` are still drafts, at `0.1.0-draft`.
- **The monthly tasks**: [ops/MONTHLY.md](../ops/MONTHLY.md). The first live refresh and the schedule come after Milestone 2 ([milestone-refresh.md](milestone-refresh.md), `refresh_timing`); until then the site shows the 2026-09-26 catalog.

## What Milestone 2 had to settle

Each question is settled. What is left is building or checking, in Milestone 2's own steps.

| Question | Answer | Where |
|---|---|---|
| Default rank order | The list opens on the **overall** rank. | AUTHORITY.md, M2-D1 (2026-09-25) |
| Numbering under filters | No numbering. Each row shows its score, 0 to 100, as a bar; filters only hide rows; a font's details keep its rank and range. This replaced M2-D2's renumbering. | AUTHORITY.md, "Scores instead of numbers" (2026-09-29), with the held-font marker rulings of 2026-09-30; built in Milestone 2 step 3 |
| Deploy path to the VPS | Each deploy uploads a release to `/srv/trulyfreefonts/prod/releases/<commit>/` and switches to it in one step. GitHub Actions deploys each push to `main` as a restricted `deploy` user; `ops/deploy.sh` is the laptop fallback. | AUTHORITY.md, M2-D6; [ops/deploy/README.md](../ops/deploy/README.md); [ops/SERVER.md](../ops/SERVER.md) ("Deploy the site"). Left in Milestone 2 step 11: server stage B and the GitHub environments and secrets. Production deploys wait for Caddy header phase B. |
| Methodology page | `/methodology/` is generated at build time from [ranking-methodology.md](ranking-methodology.md), so the two can't drift. | Milestone 2 step 7; `src/tff_site/pages.py` |
