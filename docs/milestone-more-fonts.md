# More truly free fonts checklist (after launch)

This milestone lists every other font that passes the gates, A–Z and without a rank, after the ranked fonts, and adds fonts on request (owner rulings `more_fonts` and `font_requests` of 2026-09-29). On 2026-09-30 the owner moved it out of the work of getting the site live (`more_fonts_timing`, [AUTHORITY.md](../AUTHORITY.md)): it comes after Milestone 2's launch, as a milestone of its own, before or after Milestone 3's owned-font tools. The owner chooses which when Milestone 2 is done. Its items were Milestone 1 step 15b and the listed-only items of Milestone 2 steps 3 and 10; the wording is theirs, with only the step numbers updated.

**How to read each step:**

- **Who:** the owner, Claude, or both.
- **Depends on:** the steps that must finish first. M1, M2 and M3 steps are in [milestone-1.md](milestone-1.md), [milestone-2.md](milestone-2.md) and [milestone-3.md](milestone-3.md).
- **Done when:** what must be true before the step is ticked.

Tick each item as soon as it is done and verified. If an item is only partly done, leave it unticked and note what's left.

**This milestone is done when:**
- every font that passes the gates is either ranked, listed only or named as held back in `review.md`;
- the live list ends every view but Rising with its listed-only fonts, A–Z, within the performance budget;
- one family added on request goes from ruling to listed in a monthly refresh.

---

### Step 1: The listed-only data, and fonts added on request
**Who:** Claude; the owner rules on the license and link queues and on each request. **Depends on:** M1 steps 6b, 14 and 15; M2 step 14 (the site is live). `catalog-site.json` v1 is frozen by then (M1 step 20), so the listed-only data goes in a file of its own, an additive change to the contract.
- [ ] L3 for every font that passes the gates, not only overall rank 700 or better and the top 150s (M1 step 6b): about 1,250 more families in the 2026-09-29 run, the 1,143 Google families among them in one automatic batch. A font that fails L3 goes to the owner's queue and isn't listed until it is ruled on.
- [ ] Claude researches the Latin families excluded as "no license found" beyond the overall top 700 (47 in the 2026-09-29 run), as it did for the top 700 under the 2026-09-26 ruling, and asks the owner only about genuine conflicts.
- [ ] Download links for listed-only fonts under M1 step 14's policy: Google families take their specimen page, and the rest go to gate K like a catalog font (about 100 in the 2026-09-29 run). A listed-only font with no accepted link yet is held back, and `review.md` names it.
- [ ] Specimens for the listed-only fonts that may have a preview (all of them in the 2026-09-29 run), under M2 step 5's rules. If they break step 5's 10 MB total, the owner chooses between a larger budget and name-only previews for listed-only fonts.
- [ ] The export writes each listed-only font with the identity, license, link, alias, preview and `flags[]` fields of M1 step 15's list, and no ranks, `order`, tiers or per-source data. It goes in a file of its own, for example `catalog-more.json`, that the site fetches only when a visitor needs it, so the list data keeps M2 step 10's budget. This is a contract change, so the schemas, `site/CONTRACT.md`, `docs/catalog-schema.md` and the sample change with it.
- [ ] Validation: every font that passes the gates is either in the catalog or listed only, never both, and is missing from both only while it is held back; an ineligible font is never listed (§9).
- [ ] `config/foundries.toml` takes families added on request: each one records the date it was added and that it came by request, never who asked, and `config_model.FoundriesConfig` and its tests learn the new keys.
- [ ] `review.md`'s monthly diff lists fonts that joined or left the listed-only set, and families added on request.

**Done when:** the listed-only data validates in CI, every font that passes the gates is either ranked, listed only or named as held back in `review.md`, and one family added on request goes from ruling to listed in a rebuild.

### Step 2: The listed-only fonts on the site
**Who:** Claude. **Depends on:** 1.
- [ ] After the ranked fonts, each list goes on with the listed-only fonts, A–Z under a heading such as "More truly free fonts", with no number or band. One line under the heading says why they have no rank (owner ruling `more_fonts` of 2026-09-29). Every filter applies to them, and search finds them by name and alias; Coding shows only the monospace ones, and Rising shows none. Sorting by name merges ranked and listed-only fonts into one A–Z list. Their data is fetched when a visitor reaches the heading, searches or changes a filter, so the first view never waits for it.
- [ ] Browser tests: listed-only fonts after the ranked ones, found by search before their heading is reached.
- [ ] The listed-only data loads on demand, so it counts apart from M2 step 10's 100 KB, under a budget set once its size is measured.
- [ ] A filter or rank change redraws the full catalog with the listed-only fonts, once they are loaded, within 200 ms under M2 step 10's slowdown.
- [ ] The methodology page and the About page say that every qualifying font is listed.

**Done when:** every view but Rising ends with its listed-only fonts, A–Z, on the live site, and the budgets pass in CI.
