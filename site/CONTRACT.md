# Site contract (Milestone 2)

This file freezes the interfaces between the parts of purelyfreefonts.com, so that several people or agents can build them at once. Each part may change freely inside itself, but a change to anything named here is a contract change: agree it with the lead engineer first, then update this file, the schema and the tests in the same pull request.

`tests/test_site_contracts.py` checks the parts of this file that a machine can check: the JS parts table, the CSS tokens, the template blocks, the sample catalog and its names, and section 11's codes and worked example.

Contents:

1. [Data in](#1-data-in)
2. [Build output](#2-build-output)
3. [Templates](#3-templates)
4. [DOM](#4-dom)
5. [JS parts](#5-js-parts)
6. [CSS parts and tokens](#6-css-parts-and-tokens)
7. [List index JSON](#7-list-index-json)
8. [Details JSON](#8-details-json)
9. [URL hash](#9-url-hash)
10. [Milestone 3 hook (`globalThis.tff`)](#10-milestone-3-hook-globalthistff)
11. [Match JSON](#11-match-json)

## 1. Data in

- **`catalog-site.json`**, schema `schemas/catalog-site.schema.json` (`schema_version` `1.0.0`, frozen by Milestone 1 step 20 on 2026-09-30; a change to a v1 field needs v2, owner ruling `site_fields_v1`). `tff_site.data.validate` runs the schema and then the cross-reference checks the schema can't express (`semantic_errors`). `tff-site validate FILE` prints `valid (1.0.0), N fonts`.
  - **Stricter than JSON:** a key repeated in one object, `NaN` and `Infinity` make the file invalid (`tff_site.data.loads`), because the schema would check only one of the repeated values while the page showed another. So does nesting more than 64 levels deep anywhere in the file (`data.MAX_DEPTH`; the catalog nests 6 and `names.json` 5), which would otherwise end in a traceback from a serialiser or a validator.
- **`names.json`**, schema `schemas/names.schema.json` (`0.1.0-draft`; docs/catalog-schema.md): the names of the catalog's run, from which the build derives the hopper's match file (section 11; the owner's ruling of 2026-10-22, `inventory_match_data_site_build`). By default it is the file beside the catalog, its name with `catalog-site` read as `names` (`build/names.json`; for the sample, `tests/fixtures/names.sample.json`); `tff-site build --names F` takes another. The build fails unless the file is at `0.1.0-draft`, has the catalog's `run.date` and `run.method_version`, lists exactly the catalog's fonts as `in_catalog` families, each under the catalog's family name, and holds every catalog alias with its relation (`tff_site.matchfile.names_errors`). It checks in stages, its schema version first, then its shape, then its run and families, and lists every problem of the first stage that has one at once (a file at another schema version gets that problem alone). CI validates the committed file against its schema, so the build checks only what the match file relies on, and that no object holds a key the schema doesn't allow (nothing unread can hide in the file), and names each problem of shape by its JSON pointer (at most 20).
  - **The reviewed list** beside it, `names.reviewed.toml` (`build/names.reviewed.toml`; the sample's `tests/fixtures/names.sample.reviewed.toml`; beside `--names F`, its name with `.json` read as `.reviewed.toml`): the owner's reviewed ambiguous names and recodes (section 11). No pipeline stage writes it.
  - **The sample's names** are synthetic like the sample: its 40 fonts as `in_catalog` families with their aliases; a bundle, a related and a sibling name; a build alias, "Sample Mono 20 Gen", that the sample's reviewed list recodes as related, as `build/names.reviewed.toml` recodes HackGen; three families outside the catalog (`Sample Held 41`, held back; `Sample Sans 06 Black`, whose name is also Sample Sans 06's Black weight, ambiguous as Archivo Black is; `Sample Sans 01 Tight`, distinct from Sample Sans 01); and three ineligible names. Its match file holds every code of section 11.
- **Wording** in the catalog (view labels and measures lines, tiers, license classes, the Nerd Font marker and legend, source credits, system labels) comes from `config/site.toml`, the one home for the owner's wording rulings about the data; Milestone 1's export copies it, and a contract test keeps the sample equal to it.
  - **The views' names, read at build time** (the owner's site ruling of 2026-10-07, `view_labels_short`): the views are "Overall", "Chosen (desktop)", "Installed (desktop)", "Projects", "Coding" and "Developers & apps" (Rising unchanged and unavailable). Only a monthly refresh writes the catalog, and rebuilding the committed one before the first live refresh would move its scores (`tff-catalog validate --committed` fails), so the committed `build/catalog-site.json` still carries the old names ("Desktop: most chosen", "Used in projects", …). Until that refresh copies the new ones, `tff_site.views` reads `config/site.toml`'s `[[views]]` with `tomllib` (no `tff_catalog` import) and the build shows config's `label` and `measures` for each view, by key, wherever the site's own code or templates name a view: the rank select and its measures line, the list index, the details payload, and on the methodology page the list of ranks and the template's own sentences that name one (`method.view_labels`, by key, so no template spells a view's name). The prose the methodology page takes from `docs/ranking-methodology.md` (§1 and §5) is not overridden: it keeps the document's own words ("most chosen", "most installed", "Used in projects") until that document is edited. Since the scoring-method port ([docs/milestone-method.md](../docs/milestone-method.md) step 3) the views' order and the retired ones are config's too: the list opens on Projects, config's first view (`default_rank_project`), and a view config marks `retired = true`, Overall (`overall_retired`), gets `available: false`, though the committed catalog of 2026-09-26 lists Overall first and available. Which views there are, and `available` for the others, stay the catalog's, and the catalog file is left as it is, the record of what was published (`version.txt` hashes it as read). So until the refresh the page and the catalog differ in those fields only. The keys must match: a view that config names and the catalog lacks, or the other way round, fails the build. `tests/site/test_views.py` checks that the committed catalog differs from config only in labels, measures lines, order and the retired view's `available`, so once the refresh carries config's views the override changes nothing. `views.CONFIG_PATH` is `config/site.toml` in the checkout the build runs from (CI's site jobs and `ops/deploy.sh` build in one); `build(site_config=…)` takes another file.
  - The page's own text (the list page's lead, the filter and sort labels, the About page's note "Why isn't my favorite free font here?", the footer's privacy line and tip link) lives in the templates, and where the owner ruled on it, a test holds it to the ruling in `data/reviews/site/`.
- **Nerd Font builds** (owner rulings of 2026-09-28 and 2026-09-29, TASK-2, and the site rulings of 2026-10-05, `nerd_tag` and `details_nerd_box_dropped`). A font whose `links.nerd` is set has a Nerd Font build: its row has the tag "Nerd Font available" (badge `nerd`, section 3), the details panel lists the build's link, whose own text gives the build's name (section 4), and the "Nerd Font available" filter keeps only these fonts. The page shows none of the catalog's `nerd` wording, which stays in the data (frozen in v1) and in the details payload: no `nerd.marker` ("NF"), since the details panel dropped the fixed-width "NF" box before the link, and no `nerd.legend`, whose caution about the icons' licenses moves into the details panel with PLAN-REVIEWERS-1.md step 7e (`nerd_caution_in_details`). Like "Official:", the line leads with a visible "Nerd Font:" (site ruling of 2026-10-06, `details_nerd_lead_in`): a maker's own build can be named "<family> NF" ("Cascadia Code NF", 9 of the 70 on the real list), which says "Nerd Font" nowhere. The sample gives one to `sample-mono-02` (a Nerd Fonts folder) and `sample-mono-13` (a maker's own build).
  - `links.nerd` is null, so the font shows no tag, no Nerd link and no filter match, when the font has no build, when the build's link failed the link check recorded for the run's date (the font stays listed; the link is back once a check passes, and `review.md` flags it), or when the owner hides the build (`config/nerd-hidden.toml`, with the reason). The site never decides any of this itself: it shows what the data gives.
- **Scores.** Each rank entry carries `score`, the engine's fused score on the shared z scale (a number for every ranked entry; a number or null for an unranked one, as in `catalog.json`). The list shows max(1, ⌈100·clip(`score` / 3, 0, 1)⌉) as a bar where the number was (site rulings of 2026-09-29, `score_display` and `score_held_fonts`; the linear score of 2026-10-01, `q11_display_linear` and `q30_display_ends`, in every view since 2026-10-19, `score_display_linear_all_views`, replacing `score_curve`'s 100·Φ(score)). The sample's scores are synthetic, falling with `order`. Each rank entry also carries `previous_score`, the font's score in the last published catalog, or null if it had none; the site doesn't use it yet (Backlog TASK-4's rising and falling markers wait). Until a monthly refresh has been merged it equals `score`, so nothing can show as moved (owner ruling of 2026-09-30, `score_previous_bootstrap`); the sample's equal their scores.
- **The sample**, `tests/fixtures/catalog-site.sample.json`: 40 invented fonts (`"synthetic": true`, ids `sample-*`, families `Sample …`). Five of them point `font_file` at the real OFL files pinned in `tests/fixtures/specimen-fonts.toml`, so specimens and "Type your own text" run on real outlines: `sample-sans-01` (Inter), `sample-mono-02` (JetBrains Mono), `sample-sans-05` (Source Sans 3, CFF), `sample-display-10` (Orbitron, basic Latin only) and `sample-script-12` (Lobster).
- **Specimens.** `preview.path` is `specimens/<id>.svg`, relative to the directory holding the data file: `build/specimens/` for real data, `tests/fixtures/specimens/` for the sample. The build checks each one's sha256, then serves it as `/assets/specimens/<id>.<h>.svg` (section 2). A row shows only the family's name (the owner's site rulings of 2026-10-05, `specimen_name_only_list` and `specimen_trim_served`): until `build/specimens` is redrawn names-only, a two-line specimen whose sha256 is pinned in `src/tff_site/trim.sha256` (the 500 committed ones and the sample's 5) is served cut down to its name line by `tff_site.trim`, in a view box as wide as the name's ink and as tall as its line. In 7 of them the sample line had shifted the drawing right, and the name moves back by the amount `trim.SHIFTS` records for that file, so every cut draws exactly the path `render(name_only=True)` draws from its font. A font flagged `specimen_name_only` and any file that isn't pinned are served as they are. A specimen may not contain `<script`, `<foreignObject`, an `on…=` handler, or an `href` other than `#…` (a link inside the file); the build refuses it.
  - **Placeholder:** a `preview.sha256` of 64 zeros means "not rendered yet". The build treats it as no preview ("Preview not available yet") instead of failing. In the sample, the five fonts with real font files carry the hashes of their committed specimens, `tests/fixtures/specimens/*.svg`, and the contract test requires the two to match (a changed sample line means regenerating both with `tests.specimens.regen`); the other fonts keep the placeholder.
- **Font files** for "Type your own text" come from the cache `~/.cache/tff/fonts/<sha256>`, filled by `tff-site fetch-fonts`. The build never downloads anything. A `font_file.url` of the form `<archive>.zip#<member>` names a font that upstream publishes only inside a release archive: `fetch-fonts` downloads the archive once per run, extracts that member unchanged and keeps it only when its size and sha256 match.
- **Wording for enums** lives in `tff_site.data` (`UNRANKED_LABELS`, `STATE_LABELS`) and reaches the page through the JSON payloads, so the script holds no copy. The per-source state `censored` reads "below the floor".
- **Destination names** for links ("links name their destination"), used by the build for row links and by `Details` for panel links:
  1. `link.label` when the data gives one;
  2. `https://github.com/<owner>/<repo>…` → `GitHub: <owner>/<repo>`;
  3. `https://fonts.google.com/…` → `Google Fonts`;
  4. otherwise the host name without a leading `www.`.

  A primary link may carry `link.note`, a sentence `Details` shows as `p.details-link-note` under the panel's links (Metropolis: why an archived mirror is its official download).

  A Nerd Font build's link, `links.nerd`, always has a `label`, which names the build. Its text names the build and then the destination by rules 2 to 4: `<label> (<destination>)`, "SauceCodePro Nerd Font (GitHub: ryanoasis/nerd-fonts)" (`tff_site.data.nerd_link_text`, `Details.nerdText`).

## 2. Build output

`tff-site build` writes one directory (default `build/site/`, gitignored). It is offline and byte-identical for the same inputs. `<h>` is the first 10 hex digits of the file's sha256. Every path matches `^([a-z0-9][a-z0-9._-]*/)*[a-z0-9][a-z0-9._-]*$`, the deploy receiver's rule.

| Path | Contents | Cache-Control (from `ops/caddy/site.caddy`) |
|---|---|---|
| `index.html` | the list, server-rendered in the default rank's order, Projects (section 7) | `no-cache, no-transform` |
| `methodology/index.html`, `privacy/index.html`, `about/index.html` | the content pages | `no-cache, no-transform` |
| `404.html` | served with status 404 by Caddy's `handle_errors` | `no-cache, no-transform` |
| `robots.txt`, `sitemap.xml` | per M2-D8 | `no-cache, no-transform` |
| `version.txt` | `commit=`, `run_date=`, `method_version=`, `catalog_sha256=` (the sha256 of the `catalog-site.json` file's bytes), `names_sha256=` and `reviewed_sha256=` (the sha256 of the bytes of `names.json` and of the reviewed list beside it, of no bytes when there is none: the match file's inputs, section 11), `schema=catalog-site/1`, one per line; never a build time | `no-cache, no-transform` |
| `favicon.ico`, `favicon.svg`, `apple-touch-icon.png`, `share.png`, `wordmark.svg` | copied from `site/static/` (these five files only; `site/static/_src/` holds their generators and sources and is never published). `wordmark.svg` is the header's wordmark, the owner's "Purely Free Fonts" plate (black outlines on a white rectangle), written by `_src/make_wordmark.py` from his drawing (`_src/wordmark-source.svg`, sha256-pinned) with the editor's data left out (AUTHORITY.md, "Headline font") | `no-cache, no-transform` |
| `assets/app.<h>.js` | the one script: `site/js/*.js` concatenated (section 5) | `public, max-age=31536000, immutable` |
| `assets/style.<h>.css` | the one stylesheet: `site/css/*.css` concatenated (section 6) | immutable |
| `assets/list.<h>.json` | the list index (section 7) | immutable |
| `assets/details.<h>.json` | the details payload (section 8), fetched on first use | immutable |
| `assets/match.<h>.json` | the hopper's match data (section 11), from `names.json` and the reviewed list; fetched only once a visitor's list arrives | immutable |
| `assets/specimens/<id>.<h>.svg` | one specimen per font with a preview | immutable |
| `assets/fonts/<id>.<h>.<ext>` | the unchanged upstream font file, for "Type your own text" | immutable |
| `assets/ui/arimo.<h>.woff2`, `assets/ui/arimo-italic.<h>.woff2` | the interface font, Arimo upright and italic, cut to Latin in `site/static/fonts/` (section 6) | immutable |
| `blog/index.html` | the blog, newest post first (only once a post is published, below) | `no-cache, no-transform` |
| `blog/<slug>/index.html` | one page per post | `no-cache, no-transform` |
| `blog/feed.xml` | the blog's Atom feed (RFC 4287) | `no-cache, no-transform` |
| `assets/blog/<slug>.<h>.<ext>` | an image in a post, PNG or SVG; `<slug>` is the post's | immutable |

**The blog** (M2-D12, Milestone 2 step 7b; `tff_site.blog`):

- **Posts** are `site/content/blog/<yyyy-mm-dd>-<slug>.md`: YAML front matter with `title`, `date` and `description`, and optionally `updated` and `draft`, then Markdown (step 7's `render_markdown`, raw HTML off, headings from `##`). The build fails on a missing, unknown or repeated field, a value of the wrong kind, a file name whose date isn't the front matter's `date`, a repeated slug, or a slug that isn't a page directory under the path rule above (lowercase letters, digits and hyphens, starting with a letter or digit). Every post is checked, drafts included.
- **Dates** come only from the front matter, never from file times or the build time, so the build stays byte-identical. Posts are shown newest first (by `date`, then slug).
- **Drafts** (`draft: true`) are published only by `tff-site build --drafts`, which the staging deploy uses. Until a post is published, the build writes no `blog/` file and no Blog link (`site.blog` is none, section 3).
- **Images** sit beside their post and are named in it by file name alone (`![What it shows](chart.png)`); PNG and SVG only, since those are the deploy's image extensions, and an SVG may hold no script, event handler or link. The build fails on an image with no alt text. The `<img>` carries the image's `width` and `height`.
- **Pages** load no script, are in `sitemap.xml`, and each post page gives its license, CC BY-SA 4.0 (LICENSE-DATA covers `site/content/blog/`). The feed's text uses absolute URLs.

## 3. Templates

Jinja2 with `autoescape=True`, `StrictUndefined`, `trim_blocks`, `lstrip_blocks` and `keep_trailing_newline`. No template may contain an inline `<script>` (only `<script type="module" src>`), a `<style>` element, a `style=""` or `on…=""` attribute, or a `<form>` (the CSP has `form-action 'none'`).

**Files:** `base.html.j2` (the shell); `index.html.j2`, `_list.html.j2`, `_row.html.j2`, `_filters.html.j2` (the list; `_list.html.j2` holds the whole list so a later `/check/` page can include it unchanged); `methodology.html.j2`, `privacy.html.j2`, `about.html.j2`, `404.html.j2`; `blog.html.j2`, `blog-post.html.j2`, `blog-feed.xml.j2` (the blog); `sitemap.xml.j2`, `robots.txt.j2`.

**Pages.** The list page `/` is `index.html.j2` with the `list` context below. Every other page comes from `tff_site.pages.page_contexts(doc)`, keyed by URL path, and its template and output file follow from the path: `/x/` → `x.html.j2` → `x/index.html`, and `/f.ext` → `f.ext.j2` → `f.ext`. `/404.html`, `/robots.txt` and `/sitemap.xml` get a default context when `page_contexts` leaves them out. Content pages, the 404 included, also get `heading`, plus `content` (rendered Markdown) or, on `/methodology/`, `method`. The blog's pages come from `tff_site.blog.page_contexts(posts)`, only once a post is published (section 2), and `tff_site.blog.page_files` names their files: `/blog/` → `blog.html.j2` → `blog/index.html`, `/blog/<slug>/` → `blog-post.html.j2` → `blog/<slug>/index.html`, and `/blog/feed.xml` → `blog-feed.xml.j2` → `blog/feed.xml`. None of them loads a script (they override `scripts` with nothing).

**Blocks in `base.html.j2`:**

| Block | Holds | Default |
|---|---|---|
| `title` | the `<title>` text; also used for `og:title` | `page.title` |
| `description` | the meta description; also `og:description` | `page.description` |
| `scripts` | the script tag, in `<head>` (modules are deferred) | the one module; a page without JS overrides it with nothing |
| `head` | extra `<head>` tags, such as the list index `<link rel="preload">` | empty |
| `header` | inside `<header class="site-header">`: the wordmark, linked home, and the `Site` navigation | wordmark and nav |
| `main` | inside `<main id="main" tabindex="-1">`: the page's one `<h1>` and content | empty |
| `footer` | inside `<footer class="site-footer">`: everything below | feedback, tip, privacy line, data date and source link |
| `feedback` | inside `<div class="feedback" id="feedback">`: the one feedback spot, the same on every page | issue forms and email |
| `tip_link` | the tip link, shown only when `site.tip_url` is set | `<p class="tip" id="tip">` |

**Context every page gets:**

| Name | Fields |
|---|---|
| `site` | `name` ("Purely Free Fonts"), `base_url` ("https://purelyfreefonts.com"), `repo_url`, `feedback` {`issues_url`, `email`, `mailto`}, `tip_url` (the live Stripe link from ops/DONATIONS.md, since M2 step 8; none leaves the tip link out), `blog` (none until a post is published; then {`url` "/blog/", `feed_url` "/blog/feed.xml"}, which adds the nav's Blog link and a `<link rel="alternate">` to the feed) |
| `page` | `path` ("/", "/methodology/" …), `title`, `description`, `canonical` (false on the 404 page) |
| `assets` | `css`, `js` and `font`, the upright interface font that `base.html.j2` preloads (hashed URLs) |
| `build` | `commit`, `run_date` |
| `urls` | the absolute URLs of the canonical pages, sorted by path, for the sitemap |

**Extra context for `index.html.j2`**, as `list`:

| Field | Contents |
|---|---|
| `views` | the available views, `{key, label, measures}`, in config order, with `label` and `measures` from `config/site.toml` (section 1); the first is the default, Projects (`project`: M2-D1, amended 2026-09-30, `default_rank_project`), and the build fails if it isn't. The retired Overall rank (`retired = true` in config) is left out, so it is never offered |
| `categories` | `{value, label}`: `sans-serif` Sans serif, `serif` Serif, `display` Display, `handwriting` Handwriting, `monospace` Monospace |
| `credit_filter` | true when some font needs credit (`attribution_required`): only then is "No credit required" shown (owner ruling of 2026-09-30, `license_filter`) |
| `systems_os` | `{value, label}`: `windows` Windows, `macos` macOS, `linux` Linux, `android` Android |
| `total` | number of fonts |
| `index_url`, `details_url`, `match_url` | hashed URLs of the three payloads: the list index, the details and the hopper's match file (section 11) |
| `rows` | one per font, in server order (section 7): `id`, `family`, `label` (the rank label in the default rank, Projects), `category_label` (of the site category, `tff_site.data.site_category`: Monospace for every monospaced font), `license_name`, `badges` [{`key`, `text`}], `specimen` {`url`, `width`, `height`} or none (`width` and `height` are the no-script `<img>`'s display size: 64 px high, the wide-screen `--spec-h` box, and as wide as the SVG's aspect ratio makes it; phones show it 48 px high), `fallback` (none, `"license"` or `"failed"`), `download` {`url`, `label`} |

Badge keys, in this order: `variable` ("Adjustable weight", owner ruling of 2026-09-30, `variable_label`), `nerd` ("Nerd Font available": a font with a Nerd Font build, `links.nerd`; the owner's site ruling of 2026-10-05, `nerd_tag`, in place of the "NF" marker beside the name), `limited` ("Limited accents"), `attribution` ("Credit required"), `preinstalled` ("Comes with Windows, Linux, LibreOffice": each operating system once, every Linux distribution being "Linux", then apps; `tff_site.build.comes_with`), `new` ("New"). Kept short (owner ruling of 2026-09-30, `filters_layout`): there is no "Monospace" badge (the category says it) or "Not redistributable" one (Rule 3), and "Pulled in by" is in the details panel. `preinstalled` is the tag that explains "Not ranked: no evidence of deliberate installs" in *most chosen* (Milestone 2 step 3); the details panel names the packages.

**Extra context for the blog pages** (`tff_site.blog.page_contexts`); dates are `yyyy-mm-dd` text:

| Page | Context |
|---|---|
| `/blog/` | `heading` ("Blog"), `posts`: one per published post, newest first, each {`url`, `title`, `date`, `updated` (or none), `description`, `draft`} |
| `/blog/<slug>/` | `heading` (the post's title), `post`: the same fields plus `content` (the rendered Markdown) and `license` {`name` ("CC BY-SA 4.0"), `url`, `scope_url` (LICENSE-DATA on GitHub)} |
| `/blog/feed.xml` | `feed`: `id`, `title`, `subtitle`, `self_url`, `alternate_url`, `updated`, `rights`, `entries` [{`id`, `url`, `title`, `published`, `updated`, `summary`, `content`}]; every URL absolute, every time RFC 3339 (midnight UTC of the front matter's date), `content` the post's HTML as text for a `type="html"` element |

## 4. DOM

Ids, classes, `data-` attributes and ARIA below are the contract; visible text is illustrative. Every page has one `<h1>`, the landmarks `header`, `nav`, `main` and `footer`, and the skip link `a.skip-link[href="#main"]` first in `<body>`.

### The list page

```html
<main id="main" tabindex="-1">
  <h1 class="list-title">…</h1>
  <p class="lead">Every font here is free for any personal or commercial use, and you may share the files.
    <span class="visually-hidden-phone">They are ranked by …</span></p>   <!-- phones: the first sentence only -->
  <div class="layout">
    <search id="filters" class="filters" aria-label="Filter fonts" hidden>  <!-- the script removes hidden -->
      <div class="filters-bar">                                       <!-- always visible: search and rank -->
        <label for="f-q" class="visually-hidden-phone">Search fonts</label>
        <input id="f-q" name="q" type="search" maxlength="100" autocomplete="off" spellcheck="false"
          placeholder="Search">                                        <!-- the placeholder shows on phones only -->
        <label for="f-rank" class="visually-hidden-phone">Measure</label>
        <select id="f-rank" name="rank" aria-describedby="f-rank-measures">…one option per available view…</select>
        <p id="f-rank-measures" class="measures visually-hidden-phone">…the view's measures line…</p>
        <button type="button" id="f-toggle" class="filters-toggle" aria-expanded="false" aria-controls="f-more">
          Filters<span class="filters-count"> (3)</span></button>             <!-- narrow screens only -->
      </div>
      <div id="f-more" class="filters-more">
        <fieldset id="f-cat" class="pills"><legend>Category</legend>      <!-- radios drawn as pills -->
          <input type="radio" id="f-cat-all" name="cat" value="" checked> …
          <input type="radio" id="f-cat-serif" name="cat" value="serif"> …  <!-- f-cat-<value> -->
        </fieldset>
        <fieldset id="f-type"><legend>Features</legend>
          <input type="checkbox" id="f-var" name="var" value="1">  <!-- Adjustable weight (variable font) -->
          <input type="checkbox" id="f-nerd" name="nerd" value="1">  <!-- Nerd Font available -->
          <input type="checkbox" id="f-hide-limited" name="hide" value="limited">  <!-- Accented letters -->
        </fieldset>
        <fieldset id="f-license"><legend>License</legend>                  <!-- only if list.credit_filter -->
          <input type="checkbox" id="f-hide-attr" name="hide" value="attr">  <!-- No credit required -->
        </fieldset>
        <div id="f-system" class="filters-select">
          <label for="f-os">Hide fonts that come with</label>
          <select id="f-os" name="hide"><option value="" selected>Nothing</option>
            <option value="windows">Windows</option>…</select>              <!-- one option per os -->
        </div>
        <button type="button" id="f-clear">Clear filters</button>
      </div>
    </search>
    <noscript><p class="noscript-note">Filters and search need JavaScript. Below is the full list, ranked by use in projects.</p></noscript>
    <section id="results" class="results" aria-labelledby="results-h">
      <h2 id="results-h" class="visually-hidden-phone">Fonts</h2>
      <p id="ext-summary" class="ext-summary" hidden></p>             <!-- Milestone 3: tff.list.setSummary -->
      <!-- Main inserts <p id="load-note" class="noscript-note"> here, before #count, when the
           list index fails to load or doesn't match the rows; the server's list stays. -->
      <p id="count" class="count">Showing 540 of 540 fonts</p>           <!-- phones: in the sort row -->
      <p id="held-legend" class="held-legend is-held" hidden>…a hollow bar… Hollow bar: this score
        rests on one kind of source, so it may move more.</p>          <!-- shown while a held row is -->
      <p id="view-note" class="view-note" hidden></p>                 <!-- the view's note (list index r.<key>.note) -->
      <div id="status" class="visually-hidden" role="status"></div>  <!-- Announce's only live region -->
      <div id="no-results" class="no-results" hidden>
        <p id="no-results-text">…names the filters to loosen…</p>
        <button type="button" id="no-results-clear">Clear filters</button>
      </div>
      <div id="list-sort" class="list-sort" role="group" aria-label="Sort the list" hidden>  <!-- shown by the script -->
        <button type="button" class="sort-btn" id="sort-rank" data-sort="rank" data-asc="most popular"
          data-desc="least popular" data-asc-spoken="…" data-desc-spoken="…" aria-pressed="true"
          data-dir="asc"><span class="visually-hidden">Sort by </span><span class="sort-label">Popularity</span>
          <span class="sort-arrow" aria-hidden="true"></span><span class="sort-dir">…</span></button>
        <button type="button" class="sort-btn" id="sort-name" data-sort="name" data-asc="A–Z" data-desc="Z–A"
          data-asc-spoken="A to Z" data-desc-spoken="Z to A" aria-pressed="false">…Name…</button>
      </div>
      <ol id="list" class="font-list" data-index="/assets/list.<h>.json"
          data-details="/assets/details.<h>.json" data-match="/assets/match.<h>.json"
          data-run-date="2026-09-25">…rows…</ol>
    </section>
  </div>
</main>
```

- **The page stops after the lead** (owner's site rulings of 2026-10-07, `why_not_listed_to_about` and `privacy_line_in_footer`): `main` holds the `h1`, `p.lead` and `div.layout`, nothing else, on every screen. The note "Why isn't my favorite free font here?" is on the About page (below), and the privacy line is in every page's footer ("Shared classes").
- **Phones** (below `40rem`; the owner's site rulings of 2026-10-06, `mobile_first_screen_two_fonts` and `early_line_hidden_on_phones`): two fonts fit on a 375 x 812 first screen. Top to bottom: the header without its early-version line; a smaller `h1` (`--fs-xl`); the lead's first sentence, its second in `span.visually-hidden-phone`, so screen readers keep it; **the controls row**, `#f-q`, `#f-rank` and `#f-toggle` on one line, their labels and `#f-rank-measures` hidden from sight by `visually-hidden-phone` (the labels stay the controls' accessible names and `#f-rank-measures` the select's description), the search box showing "Search" as a placeholder (the start of its name, "Search fonts", short enough to show whole), every target at least 24 px, `#f-toggle` as wide as its longest text ("Filters (6)", `7em`) so the count of filters on never changes the row, search and the select sharing the rest two to three (each at least `5.5rem`), the line on one row from 336 px and in two below that, whatever the count, never scrolling sideways, and M2-D4 unchanged, with everything else behind `#f-toggle`; then the results, whose `#results-h` is hidden from sight but stays for screen readers and headings navigation, and **the sort row**, `#list-sort` with `#count` after it at the row's end (`order`; on its own line where it can't keep about 7.5em beside the buttons, below 337 px since the Popularity button's words became "most popular", and on two lines beside them up to about 390 px), its first line level with the buttons' labels, so a count that changes length moves nothing. `#held-legend`, `#view-note` and `#no-results` stay above the sort row. From `40rem` up the page is as before: visible labels, the measures line, the "Fonts" heading with the count under it, then the sort buttons ("Sorting", below).
- `#list[data-match]` names the hopper's match file (section 11). Nothing else in the built site names it: no preload, prefetch or other link, no other page, and no script or payload. `tff-site check` fails a site where any file but the list page names it, or where the list page names it more than once. The script fetches it only once a visitor's list arrives (M3-D7 a, `inventory_slice_1_defaults`); `tests/site/test_privacy.py` checks that loading, scrolling and using the list without a list never request it.
- The controls' `name` attributes are the hash keys (section 9); `value` is the key's value. Filters live in `<search>` and `<fieldset>`/`<legend>` groups, never in a `<form>`.
- `#filters` and `#list-sort` carry `hidden` in the HTML; the script removes it. Showing them must not move the list (reserve their space in CSS), because the layout-shift budget is 0.1.
- **A link with a view or filters in it** (`State.changesList`: any hash pair but `font`'s, an extension's included) would have the first render change the server's list after it is painted: on phones, where the list starts on the first screen, rows and the footer would move (layout shift up to about 0.55 on 2026-10-07). So, as the script starts, Main sets `html[data-list-pending=""]`, under which `#results` and `.site-footer` aren't displayed; after the first render it sets `data-list-pending="settling"`, under which they are laid out but invisible for two frames, while the rows on the screen grow from their `contain-intrinsic-size` estimate to their own height; then it removes the attribute, and only then does `tff:list-ready` fire (section 10), since an invisible row can't take focus, which a `#font=` link's heading needs. They show anyway if the list index fails to load, and after 3 seconds without it (the late render then moves the list). No hash, an in-page anchor or a font alone never sets it. Layout shift on load is 0 on every screen for such links, with the index held back so the page paints first; `#font=` links still shift when Details opens the panel and scrolls to it, as before.
- Narrow screens are below `60rem`: `#f-more` is hidden until `#f-toggle` expands it, and `#f-toggle`'s text includes the number of active filters. From `60rem` up, `#f-more` is always shown in the sidebar and `#f-toggle` is hidden.
- **The filter set** (owner rulings of 2026-09-30, `data/reviews/site/2026-09-30.toml`: `monospace_category`, `license_filter`, `filters_layout`). Category's **Monospace** is every monospaced font, the list Coding orders, and the other categories hold proportional fonts only: the list index's `cat` is the site category (section 7). There is no Spacing filter (it replaced "Text only" and "Monospace only" on 2026-09-25) and no license-group or "Redistributable fonts only" filter (Rule 3). `#f-hide-attr`, "No credit required", is rendered only while some font needs credit. `#f-os` offers one operating system at a time; its value joins `hide` with the checked boxes.
- **Sorting** (owner ruling of 2026-09-30, `sort_header`): `#list-sort`, over the list, outside `#filters`, holds one `button.sort-btn[data-sort]` per column, Popularity (the score column's heading, `score_column_popularity`, 2026-09-30; its orders "most popular" and "least popular", `sort_words_most_least`, 2026-10-07, which amended `sort_words_popular`'s "most popular first" and "least popular first" of 2026-10-05) and Name. `aria-pressed="true"` and `data-dir` (`asc` or `desc`) mark the column in use: the CSS fills that one of its two stacked arrows (`.sort-arrow`), and `.sort-dir` holds the order's words, from the button's `data-asc`/`data-desc` (shown) and `data-asc-spoken`/`data-desc-spoken` (for screen readers, with what a click does). A click on the button in use reverses its order; a click on the other sorts by it, `asc`. The spoken words are the shown ones, so the button's title is "Show least popular instead" and the announcement after a sort change adds "Sorted by <label>, <spoken words>" to the count line ("Sorted by popularity, least popular"). The order's words take one line, the button's second (`sort_two_lines`): from `40rem` "most popular" and "least popular" (about 83 px) are no wider than the Popularity label and its arrow (about 94 px), so the button fits over the 6rem rank column and Name starts over the names. In either order one button has two lines and the other one, so `#list-sort` is one height whichever column sorts, and sorting never moves the list.
- **Words** (owner's site rulings of 2026-10-05, `selector_label` and `method_page_name`): the rank select's label is "Measure", the methodology page is "How we measure popularity", and the nav links to it as "How it works". A font's details keep "Rank" for its exact place (`score_column_popularity`).
- `#f-nerd` ("Nerd Font available", owner's site ruling of 2026-09-29, `data/reviews/site/2026-09-29.toml`, `nerd_filter`) keeps only the fonts with a Nerd Font build, on every rank: the rows tagged "Nerd Font available". There is no legend above the list: the owner's site ruling of 2026-10-05 (`nerd_tag`) replaced `#nf-legend` and the rows' "NF" marker with the tag.

### A row

```html
<li class="font" id="font-inter" data-id="inter">
  <div class="font-row">
    <span class="rank"><span class="visually-hidden">Score </span>83<span class="visually-hidden">
      of 100</span><i class="bar b83"></i></span>                <!-- or <span class="rank">Not ranked: <reason></span> -->
    <div class="font-title has-spec">                            <!-- has-spec only with a specimen -->
      <h3 class="font-name" id="font-inter-name">Inter</h3>
      <div class="font-spec">
        <span class="spec" aria-hidden="true" data-src="/assets/specimens/inter.<h>.svg"></span>
        <noscript><img class="spec-img" src="/assets/specimens/inter.<h>.svg" alt=""
          width="…" height="…" loading="lazy"></noscript>
        <!-- or, with no specimen: -->
        <p class="spec-fallback">No preview: this font's license doesn't let us host its files.
          See it on <a href="…">GitHub: rsms/inter</a>.</p>        <!-- fallback "license": no preview_ok,
                                                                        which Rule 3 leaves unused -->
        <p class="spec-fallback">Preview not available yet.</p>    <!-- fallback "failed" -->
      </div>
    </div>
    <p class="font-meta"><span class="font-cat">Sans serif</span> <span class="font-lic">SIL Open Font License 1.1</span></p>
    <ul class="badges" aria-label="Tags"><li class="badge" data-badge="variable">Variable</li>…</ul>  <!-- omitted when empty -->
    <a class="download" href="https://github.com/rsms/inter">…names the destination…</a>
    <button type="button" class="details-toggle" aria-expanded="false" aria-controls="details-inter">
      Details<span class="visually-hidden"> for Inter</span></button>
  </div>
  <div class="details" id="details-inter" hidden></div>          <!-- filled by Details on first open -->
</li>
```

- Font index `i` (section 7) is the `i`-th `li.font` in the server-rendered `#list`.
- **The name, once** (owner rulings of 2026-09-29, `name_once`, and of 2026-10-05, `score_centred`). `div.font-title` holds the heading and the specimen box, which takes the title's full width on every row (`nerd_tag`). With a specimen (`has-spec`), the specimen draws the family name in the font, centred in the box, and the heading shares the box's cell, centred on the box, so it lies over the drawn name and find-in-page highlights it there; a heading that wraps taller than the box fills the cell from the box's top. Once the specimen shows, the title is `is-drawn` and the heading's text isn't painted, though screen readers, search and find-in-page still have it: its text is transparent (not `opacity: 0`, which would hide the highlight too), so a selection or a find-in-page match paints its highlight over the drawn name. Where the specimen doesn't show (no specimen, a file that fails to load, no CSS masks, no IntersectionObserver, the loader paused), the heading does. Without scripting the `<noscript>` image draws the name and the heading isn't painted; since that image's `alt` is empty (`specimen_label_hidden`), one that fails to load there leaves no visible name, and only screen readers have it, from the heading. Hiding the heading moves nothing: it and the box share one cell.
- **The Nerd Font tag** (owner's site ruling of 2026-10-05, `nerd_tag`, which replaced the fixed-width "NF" marker at the end of the title, `nerd_marker_spot_title`): a font with a Nerd Font build (`links.nerd`) has `li.badge[data-badge="nerd"]`, "Nerd Font available", among its tags, on every rank. The title keeps no column for a marker.
- **The specimen is decorative** (owner's site ruling of 2026-10-05, `specimen_label_hidden`, in place of the name "<family> sample"): `span.spec` carries `aria-hidden="true"` and no role or name, and the no-script image has `alt=""`, since the heading names the font. Neither `Specimens` nor `Render` adds a label.
- `span.spec[data-src]` is always `/assets/specimens/<id>.<h>.svg`. `Specimens` ignores any other value, so a `data-src` can never break out of the CSS `url("…")` it becomes.
- **The score** (owner rulings of 2026-09-29, `score_display` and `score_curve`, which replaced M2-D2's numbers and bands). A ranked row's `.rank` holds its score in this rank, max(1, ⌈100·clip(`ranks.<key>.score` / 3, 0, 1)⌉) (list index `s`; `q11_display_linear`, `q30_display_ends`, `score_display_linear_all_views`), as text, and a blue bar that long: an empty `i.bar.b<score>`, whose fill is its `::before`, sized by the width class `.b1` to `.b100` in `20-list.css`, since the CSP forbids style attributes. Screen readers hear the cell's text, "Score 83 of 100" (list index `score_words`). A font the two-source rule holds back (`gate_held`) has `.rank.is-held`, a hollow bar, and ", from one kind of source" after the score (owner ruling of 2026-09-30, `held_marker_style`), except in a view with a note (`r.<key>.note`: Developers & apps, `held_marker_dev_apps`), which marks no row. `#held-legend` shows while a held row is shown (the server shows it when its list has one), and `#view-note` holds the view's note. An unranked row's `.rank` is the text "Not ranked: <reason>". With a specimen, a ranked row's score and bar are centred vertically on the specimen box in every layout (owner's site ruling of 2026-10-05, `score_centred`): the `.rank` cell is as tall as the box (`--spec-h`) and starts level with it; without one, they stay level with the heading's first line.
- **The row's actions** (owner's site ruling of 2026-10-05, `download_button`): `a.download` is the main one and has the strong, button-like look, filled with `--c-accent`, though it stays a link that navigates; `button.details-toggle` is the quieter one, a `--c-border` outline with no fill. They close the row in that order, so the keyboard reaches the download first, both with the focus ring; on one line the two are the same height, and a long destination wraps inside the download button. Under forced colours both keep a `--c-border` border and the download takes the link colours on `--c-bg` (Chromium paints a backplate in the page's colour behind text there, which hides text in `--c-accent-fg`); in print, where browsers drop backgrounds, it is an underlined link.
- **Phone cards** (owner's site ruling of 2026-10-05, `phone_rows_trim`, which amended `phone_rows_merged`): below `40rem` a card's vertical padding, the gap above its actions and the actions' side padding are `--space-2`, `--space-3` from `40rem`, so about five cards fit on a phone's screen with the larger names from 360 px wide (median 160 px on the real list, against 812 / 5 = 162.4). Below about 352 px "Download from Google Fonts" takes two lines and the median card is 175 px: four to a screen. Category and license keep their own line, and the type, the specimen box and the buttons' heights keep their sizes.
- `Render` moves rows in and out of `#list` (hidden rows are detached, not given `hidden`) and changes only the `.rank` cell, drawn as the server draws it. Rows carry `content-visibility: auto`, so they must not change height when their specimen arrives.
- **States set by scripts:** `html[data-js]` once the script runs; `html[data-list-pending]` (`""`, then `"settling"`) on the list page while a link's view or filters wait for the first render ("The list page"); `span.spec[data-state]`: `loading` while its file loads, then `set` once its mask is set (and its `div.font-title` gets `is-drawn`), or `failed` when the file can't be had; `li.font.is-dim` for a row a Milestone 3 filter dims; `li.font.is-unranked` for a row whose `.rank` reads "Not ranked: <reason>" (the server sets it too), which puts that label on a line of its own above the name (the owner's site ruling of 2026-09-26).
- **The Milestone 3 slot** (section 10). For each filter whose `note` gives a row something to show, `Render` adds one `<div class="ext" data-filter="<filter id>">` at the end of `.font-row` (created on demand, removed when that filter no longer has a note for the row), built with `Core.el` only:
  - `span.ext-badge` for `badge`, `p.ext-note` for `text`;
  - one `a.ext-link` per link (`href` relative or `https://`, anything else dropped);
  - one `button.ext-action[type=button][data-action="<action id>"]` per action.

  Rows may grow when a note appears; that happens only after the visitor starts Milestone 3's check, so it is not a load-time layout shift.
- **Details panel** (owned by `Details`; only these hooks are frozen): the panel's first child is `<h4 class="details-title" id="details-<id>-h" tabindex="-1">`, which receives focus when the panel opens from a `#font=` link, and it has a `button.details-close`. Esc or the close button returns focus to the row's `.details-toggle`. Its body, in order (owner ruling of 2026-09-30, `details_layout`):
  1. "Type your own text": `button.typeown-load` ("Type your own text (loads 312 KB)"), then `input.typeown-input`;
  2. `div.details-summary`, one `dl.details-essentials`: Get it (`ul.details-links`: "Official: …", "Designer: …", and for a Nerd Font build the link `a.details-nf-link`, its text by section 1's Nerd rule, with no "NF" box before it (owner's site ruling of 2026-10-05, `details_nerd_box_dropped`) and no legend; the line leads with "Nerd Font:", `details_nerd_lead_in`), License (`a.details-lic-link`, then "No credit needed." or "Credit required: …"), Font (formats and Latin coverage), Comes with, Also known as, and Rank (`span.details-rank-now`: the rank the selector shows, kept current);
  3. `details.details-evidence`, closed, its `summary` "All ranks and sources": every available rank with its place only ("#12", "Band 101–250" or "Not ranked: …"; no tier, likely range or tier legend: owner's site ruling of 2026-09-30, `no_tiers_or_ranges`), "Pulled in by", and the per-source tables (`table.details-src-table[data-survey]`), where a Linux source "left out of" some ranks names the available ones only;
  4. `p.details-report`.

### The About page

**The note "Why isn't my favorite free font here?"** (site rulings of 2026-09-29, `data/reviews/site/2026-09-29.toml`: `why_not_listed`; moved from the front page on 2026-10-07, `why_not_listed_to_about`): the owner's text, word for word, closes `/about/` as `<h2 id="why-not-listed">` and one `<p>`, written by `about.html.j2` after the page's Markdown, so its anchor, `/about/#why-not-listed`, stays put whatever `about.md`'s headings say (`tff_site.pages.TEMPLATE_IDS` keeps the id from them). The address is a `mailto:` link to `site.feedback.email`, admin@purelyfreefonts.com since the site ruling of 2026-10-02 (`contact_address`). PLAN-REVIEWERS-1.md step 5 rewrites it there and links the front page's empty-search message to the anchor.

### Shared classes

`visually-hidden` (hidden from sight, read by screen readers), `visually-hidden-phone` (the same below `40rem` only, and shown as it is from `40rem` up: the list page's filter labels, measures line, "Fonts" heading and the lead's second sentence), `skip-link`, `site-header`, `site-name`, `site-name-mark`, `site-nav`, `site-footer`, `feedback`, `tip`, `footer-privacy`, `footer-meta`.

**The header** (owner rulings of 2026-09-29, 2026-10-04 and 2026-10-14): `a.site-name[href="/"]` holds only `img.site-name-mark[src="/wordmark.svg"]`, whose `alt` is the site's name ("Purely Free Fonts"), with `width` and `height` in the wordmark's 9809:1250 ratio (463 x 59). The wordmark is the owner's "Purely Free Fonts" in the Book of his typeface Purely Constructed, near-black (`#111111`) on its own white plate: his file, byte for byte. It is 463 px wide from `51rem` (816 px) up, where the navigation fits beside it on one row, and 300 px below that, so its capitals stand about 38 and 24.5 px tall; at 320 px it shrinks to fit (288 px). The breakpoint is the widest one-row header: 16 + 463 + 24 + the nav + 16 px, 800 px with the nav at its widest, 281 px with "How it works" bold on its own page (measured on 2026-10-14; `test_shell.py` re-measures it and checks every width from 320 to 1280 px for a second row). The 300 px wordmark keeps the nav beside it from 637 px, so from the `40rem` phone breakpoint up (306 px, for capitals of 25, would not). That holds without the Blog link: once a post is published (`site.blog`) the nav is about 329 px, the one-row header needs about 850 px and the phone nav wraps below about 360 px, so the first post moves the breakpoint (the test fails on such a build until it does; a draft shown only on staging isn't measured). Below about 637 px the nav takes its own line under the wordmark, still one line at 320 px. `p.site-status`, the early-version line, takes a line of its own from `40rem` up and isn't shown on phones (`early_line_hidden_on_phones`, 2026-10-06). The favicon is the browser's icon only, not in the header. The header is white in both themes, behind the black wordmark: it takes the `--c-header-*` tokens, whose dark values are the light ones, so its links and text keep their light-theme colours and its focus ring 3:1 on white. Under forced colours it takes the system colours, and the wordmark keeps a white plate (`--c-wordmark-bg`).

**The footer**: `div#feedback` first (M2-D10, 3.2.6), then `p.tip` (when `site.tip_url` is set), `p.footer-privacy`, the privacy line on every page ("No cookies, no tracking, and the page loads only its own files. Check the Network tab.", its link to `/privacy/#check-for-yourself`, where the Privacy page explains the check; owner's site ruling of 2026-10-07, `privacy_line_in_footer`), and `p.footer-meta`, the data date and the source link.

## 5. JS parts

The build concatenates `site/js/*.js` in filename order into one ES module, `/assets/app.<h>.js`, loaded by `<script type="module" src>`, leaving out the lines that hold only a `//` comment (outside template literals; `assets.strip_js_comments`), which ship no code but cost the page budget. The rule, which the build's lint enforces on the parts as written:

- Each part declares **exactly one** top-level binding: `const <Name> = …;`, usually an IIFE that returns a frozen object. Top-level lines start at column 0; everything inside is indented.
- No other top-level declaration (`let`, `var`, `function`, `class`, a second `const`), and no `import`, `export` or dynamic `import(`. The one top-level statement allowed besides the declarations is `Main.start();`, exactly once, as the last top-level line of `90-main.js`.
- Any other top-level line only closes the declaration: closing brackets, a call's parentheses and arguments, and one final `;` (`})();`, `})(Core);`), so no second statement can hide behind it.
- A part's initializer may use only lower-numbered parts. Its functions may call any part once `Main.start()` runs.
- Never, anywhere, comments included: `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `eval(`, `Function(`, `document.write`. Build nodes with `Core.el` and `textContent`.
- `fetch()` takes only relative URLs read from the DOM (`#list[data-index]`, `#list[data-details]`, and `#list[data-match]`, fetched only once a visitor's list arrives: section 11). No other request API: no `sendBeacon`, `XMLHttpRequest`, `WebSocket` or `EventSource`. No `setAttribute('style'` either (the CSP blocks style attributes; set styles through CSSOM).
- Nothing is stored: no `localStorage`, `sessionStorage`, `indexedDB`, `document.cookie`, `caches`, service workers or `cookieStore`. The privacy tests fail on any use.

| File | Const | Owner | Status | Responsibilities |
|---|---|---|---|---|
| `00-core.js` | `Core` | wave 0 | written | DOM helpers: `$`, `$$`, `el`, `append`, `text`, `clear`, `setHidden`, `on` (delegation), `debounce`, `idle`, `painted`, `formatBytes`, `formatCount`, `plural` |
| `05-keys.js` | `Keys` | A7 | written | `matchKey(s)`, `searchKey(s)`, `DROP_CODEPOINTS` and `CASEFOLD_EXTRA` (equal to `tests/vectors/name-keys.json`'s `spec`; casefold is the table, then `toLowerCase()` with ß→ss and ς→σ, one code point at a time) |
| `10-data.js` | `Data` | A3 | written | `loadIndex()` and `loadDetails()` (memoised promises; details only on first use; a 404 rejects with `Data.Stale`, shown as "The list was updated. Reload to see details.") |
| `15-state.js` | `State` | A3 | written | the state object, hash parse and serialise (section 9), `pushState` for discrete changes, `replaceState` for search (300 ms), `popstate`/`hashchange` |
| `20-view.js` | `View` | A3 | written | pure `compute(state, index, filters)` → `{order, labels, scores, held, words, dimmed, notes, shown, total, note}` (below) |
| `25-render.js` | `Render` | A3 | written | reorders server-rendered rows through a `DocumentFragment`, redraws `.rank`, updates `#count`, `#no-results`, `#held-legend`, `#view-note`, keeps focus |
| `30-filters-ui.js` | `FiltersUI` | A3 | written | the controls in `#filters`: reads and reflects state, the phone disclosure, `#f-rank-measures` |
| `35-announce.js` | `Announce` | A3 | written | `#status`: immediate for discrete changes (coalesced per microtask), 500 ms debounce for search, silent on first load |
| `40-details.js` | `Details` | A4 | written | `open(id, {focus})`, `close()`, the panel built with `Core.el`, "Type your own text" (`FontFace` + CSSOM `style.fontFamily`). Owns `.details-toggle` clicks. Before `tff:list-ready` it writes the hash's `font` pair itself, keeping every other pair as written. After it, it records `font` through `State.set` and follows `State.subscribe`, having subscribed after `Main` |
| `45-specimens.js` | `Specimens` | A6 | written | IntersectionObserver on `li.font` (`rootMargin: '600px 0px'`) setting `mask-image`; `start()`, `pause()`, `resume()`, `paused` |
| `50-ext.js` | `Ext` | A12 | written | builds `globalThis.tff` (section 10) and dispatches `tff:list-ready`; keeps the external filters, read by `View.compute` through `Ext.filters()`; `Ext.start(host)` takes `Main`'s `{ refresh, onChange, ready }` |
| `90-main.js` | `Main` | A3 | written | `start()`: sets `html[data-js]` (and `html[data-list-pending]` for a link with a view or filters, until the first render), wires the parts, event delegation on `#list` (including `button.ext-action`, section 10), indexing in idle callbacks |

**`View.compute(state, index, filters)`** is pure and unit-tested through `page.evaluate`:

1. The universe is the fonts in the rank's universe (`u` character not `.`, section 7).
2. Apply the filters and search (`Keys.searchKey(state.q)` as a substring of `keys[i]`).
3. Apply the external filters with `affectsNumbering: true`.
4. Sort ranked fonts by score (the index's `order`), then unranked fonts by `by_name`.
5. Scores: each ranked font keeps its score `s[i]`, whatever the filters (owner ruling of 2026-09-29, `score_display`: filters only hide rows, with no renumbering and no bands in the list); `held` is `r.held[i]`, unless the view has a `note`; unranked fonts take "Not ranked: <why label>".
6. Apply the external filters with `affectsNumbering: false`. Since scores replaced numbers, steps 3 and 6 only hide or dim; the flag stays, as the hook is frozen.
7. With `sort=name` or `name-desc`, re-sort by `by_name`, keeping the labels; then with `rank-desc` or `name-desc`, reverse the whole order (a true reverse: unranked fonts come first when the rank is reversed).
8. Return `order` (font indexes to show, in order), `labels` (each row's `.rank` text), `scores`, `held`, `dimmed` and `notes` (parallel to `order`), `words` (the index's `score_words`), `shown`, `total` (the size of the rank's universe) and `note` (the view's note, or null).

## 6. CSS parts and tokens

`site/css/*.css` is concatenated in filename order into `/assets/style.<h>.css`, without its `/* … */` comments (`assets.strip_css_comments`): `00-tokens.css` (wave 0), `10-base.css` (A14), `20-list.css` and `25-filters.css` (A2), `30-details.css` (A4), `35-specimens.css` (A6), `40-pages.css` (A8), `45-blog.css` (the blog, step 7b).

- Colours come only from the tokens; no other part writes a colour value.
- Focus: `outline: var(--focus-ring); outline-offset: var(--focus-offset);` on `:focus-visible`. No sticky header, so focus is never hidden (2.4.11).
- Breakpoints (custom properties don't work in media queries, so these are fixed): narrow below `60rem` (filters behind the button), phone below `40rem` (specimen box 48 px, the compact first screen and `visually-hidden-phone`), and the header's wordmark 300 px below `51rem`. The first screen's phone rules are written `not all and (min-width: 40rem)`, so that they and the `(min-width: 40rem)` rules never both apply at exactly 640 px.
- Specimens: `.spec { mask-size: contain; mask-repeat: no-repeat; mask-position: left center; height: var(--spec-h); }` and `.spec[data-state="set"] { background-color: var(--c-spec); }`, so the box fills only once its mask is set (an unmasked fill is a solid bar); `.spec` is hidden under `(scripting: none)`, where the `<noscript>` image shows instead; under `forced-colors`, `forced-color-adjust: none` (the token becomes `CanvasText`). The noscript image gets `filter: invert(1)` in dark mode.
- The name once: `.font-title.is-drawn > .font-name { color: transparent; forced-color-adjust: none; }` inside `@supports (mask-image: none)`, and `.font-title.has-spec > .font-name` likewise under `@media (scripting: none)` (section 4); never `opacity`, which hides a selection's or a find-in-page match's highlight too, and `forced-color-adjust: none` keeps forced colours from painting the text over the drawn name. That heading is `align-self: center` over the box, and a ranked row's `.rank` has `min-height: var(--spec-h)` unless its title lacks `has-spec` (`:has()`), so the score is centred on the box (`score_centred`).
- No `@import`, no `@font-face`, and no `url()` with a scheme or another host, a `data:` URL included: the page loads only its own files. The build's lint refuses them.
- The interface font (AUTHORITY.md, "Interface font"): the build writes one `@font-face` for Arimo upright and one for its italic ahead of the parts, both `font-weight: 400 700` and `font-display: optional`, from `/assets/ui/` (section 2). Every page preloads the upright with `<link rel="preload" as="font" type="font/woff2" crossorigin>`, so it is usually there by the first paint. A font that comes later is never swapped in, so no text moves: that page view keeps Liberation Sans or Arial, which have Arimo's widths, and the next page has Arimo from the cache. `tff-site check` holds each file to 30 KB.
- Rows: `li.font { content-visibility: auto; contain-intrinsic-size: auto var(--row-est-h); }`.

**Tokens** (defined in `00-tokens.css` on `:root`; every `--c-` token is redefined for dark mode and for forced colours):

| Token | Use |
|---|---|
| `--font-ui` | all interface text: Arimo, then Liberation Sans and Arial (same metrics) |
| `--font-mono` | the system monospace stack |
| `--fs-small` | small text: badges, meta lines |
| `--fs-base` | body text |
| `--fs-large` | row names, `h3` |
| `--fs-xl` | `h2` |
| `--fs-2xl` | `h1` |
| `--lh-body` | body line height |
| `--lh-tight` | heading line height |
| `--space-1` | 0.25rem |
| `--space-2` | 0.5rem |
| `--space-3` | 0.75rem |
| `--space-4` | 1rem |
| `--space-5` | 1.5rem |
| `--space-6` | 2rem |
| `--space-7` | 3rem |
| `--radius` | corner radius |
| `--measure` | maximum line length of prose |
| `--sidebar-width` | the filter sidebar on wide screens |
| `--target-min` | minimum target size, 24 px (2.5.8) |
| `--spec-h` | specimen box height: 64 px, 48 px on phones (`specimen_box_heights`, 2026-10-05) |
| `--row-est-h` | estimated row height for `contain-intrinsic-size` |
| `--focus-width` | focus outline width |
| `--focus-offset` | focus outline offset |
| `--focus-ring` | the focus outline shorthand |
| `--duration` | transition length; 0 under reduced motion |
| `--c-bg` | page background |
| `--c-surface` | raised areas: sidebar, details panel |
| `--c-fg` | text |
| `--c-muted` | secondary text (still 4.5:1) |
| `--c-border` | control borders (3:1) |
| `--c-divider` | decorative rules only |
| `--c-link` | links |
| `--c-link-visited` | visited links |
| `--c-focus` | focus outline (3:1 on every background) |
| `--c-accent` | primary buttons, and a row's Download link (`download_button`) |
| `--c-accent-fg` | text on `--c-accent` |
| `--c-badge-bg` | badge background |
| `--c-badge-fg` | badge text |
| `--c-warn-bg` | warning background (a font file that didn't load) |
| `--c-warn-fg` | warning text |
| `--c-spec` | specimen fill: `currentColor`, `CanvasText` under forced colours |
| `--c-header-bg` | the header's background: white in both themes |
| `--c-header-fg` | the header's text (the light `--c-fg`, in both themes) |
| `--c-header-muted` | the header's secondary text (the light `--c-muted`) |
| `--c-header-link` | the header's links (the light `--c-link`) |
| `--c-header-focus` | the focus outline inside the header, and the skip link's, which shows over it (the light `--c-focus`) |
| `--c-wordmark-bg` | the plate behind the wordmark image: white, under forced colours too (the SVG also draws its own) |

**The blue** is lapis lazuli (the owner's site ruling of 2026-10-07, `lapis_lazuli_blue`): `#26619c` for `--c-link`, `--c-focus` and `--c-accent` in light mode and for `--c-header-link` and `--c-header-focus` in both themes (6.4:1 on white, 5.4:1 on `--c-badge-bg`), and a lighter tint, `#8cb6e3`, for `--c-link`, `--c-focus` and `--c-accent` in dark mode (8.4:1 on the dark `--c-bg`, where true lapis is about 2.8:1). `--c-link-visited` keeps its purple, and forced colours keep the system colours. The favicon, the touch icon and the share image (`site/static/`, drawn by `site/static/_src/make_static.py`) still use the old blue, `#0a56c2`: the ruling names the tokens only, and the images wait for the owner.

## 7. List index JSON

`/assets/list.<h>.json`, built by `tff_site.data.list_index`, preloaded by `<link rel="preload" as="fetch" crossorigin href>` in the list page's `head` block and fetched from `#list[data-index]`. Columnar: every per-font array has `n` entries, indexed by font index.

**Font index** `i` is the server-rendered order: fonts ranked in the default rank, Projects (`tff_site.data.DEFAULT_VIEW`, `project`), by score, best first, ties by `order` (`tff_site.data.score_order`), then fonts unranked there by Python `str.casefold()` of the family, then by id.

| Key | Type | Contents |
|---|---|---|
| `v` | int | format version, `4` (`no_tiers_or_ranges`: `u` in place of `tier`; `3`, 2026-09-30: scores, `r.<key>.s`, `held` and `note`, and `order` by score; `2`: `cat` is the site category, and `lics`/`lic` are gone) |
| `commit` | string | the commit in `version.txt` |
| `run_date` | string | `run.date` |
| `n` | int | number of fonts |
| `ids` | string[n] | font ids |
| `cats` | string[] | the five categories, in schema order |
| `cat` | int[n] | index into `cats` of the site category: `monospace` for every monospaced font, else the catalog's `category` (`tff_site.data.site_category`, owner ruling of 2026-09-30) |
| `bits` | int[n] | flags, below |
| `keys` | string[n] | `search_key` of the family, then of each alias in catalog order, joined by `\|` |
| `by_name` | int[n] | font indexes sorted by Python `str.casefold()` of the family, then id |
| `views` | object[] | the catalog's `views` in `config/site.toml`'s order, with each view's `label` and `measures` from it (section 1, `view_labels_short`), and `available: false` for a view it retires (Overall, `overall_retired`) |
| `bands` | string[] | band labels, in order |
| `why_labels` | string[] | unranked-reason labels, in the order `no_deliberate_evidence`, `no_evidence`, `too_new` |
| `score_words` | object | the words around a score for screen readers: `before` ("Score "), `after` (" of 100"), `held` (", from one kind of source") |
| `r` | object | one entry per view with `available: true`, keyed by rank key, below |

**`bits`**: 1 monospace (`is_monospace`), 2 variable, 4 limited accents (`latin.coverage` basic), 8 attribution required, 16 unused (it was "not redistributable" until Rule 3 of 2026-09-30), 32 has a specimen, 64 "Type your own text" available, 128 comes with Windows, 256 macOS, 512 Linux, 1024 Android (from `preinstalled_on` systems' `os`; a system with `os` `app`, an application's own bundle such as LibreOffice's, sets none; `pulled_in_by` doesn't count), 2048 new (flag `too_new`), 4096 pulled in by a package (`pulled_in_by` is not empty; no filter hides by it), 8192 a Nerd Font build (`links.nerd` is not null; "Nerd Font available" keeps only these, site ruling 2026-09-29). Category Monospace is exactly the fonts with bit 1.

**`r.<rank key>`:**

| Key | Type | Contents |
|---|---|---|
| `order` | int[] | the font indexes ranked in this view, best score first, ties by the catalog's `order` (a font the two-source rule holds back takes its score's place) |
| `s` | int[n] | the score, 1 to 100 (max(1, ⌈100·clip(`score` / 3, 0, 1)⌉), on the score's decimal value), if ranked; else -1 |
| `held` | string (n chars) | `1` if ranked and held back by the two-source rule (`gate_held`), else `0` |
| `note` | string or null | a note shown above the list in this view instead of held markers (Developers & apps) |
| `top` | int[n] | exact rank 1–100, or 0 |
| `band` | int[n] | index into `bands`, or -1 |
| `u` | string (n chars) | `+` if ranked; `-` if in the view's universe but unranked; `.` if not in the universe (a non-monospace font in Coding). It replaced `tier` (format 4): the site shows no tiers |
| `why` | int[n] | index into `why_labels` for unranked fonts, else -1 |

## 8. Details JSON

`/assets/details.<h>.json`, built by `tff_site.data.details`, fetched from `#list[data-details]` on the first details open or a `#font=` load.

| Key | Contents |
|---|---|
| `v` | `2` (`no_tiers_or_ranges`: no `tiers`, and no `tier` or `range` in a font's ranks) |
| `run_date` | `run.date` |
| `views` | the catalog's `views` in `config/site.toml`'s order, with each view's `label` and `measures` from it (section 1, `view_labels_short`), and `available: false` for a view it retires (Overall, `overall_retired`) |
| `bands`, `sources`, `systems`, `license_classes`, `nerd` | as in the catalog |
| `state_labels` | `{observed, censored, not_covered, too_new}` → label (`censored` → "below the floor") |
| `why_labels` | `{no_deliberate_evidence, no_evidence, too_new}` → label |
| `report` | `{issue_url, email}`: `https://github.com/byronshock/purelyfreefonts/issues/new?template=license.yml`, to which `Details` appends `&font_id=<id>&data_date=<run_date>`, and the fallback address for a `mailto:` link |
| `fonts` | `{<id>: font}`: the catalog's font object without `preview` and `font_file`, and each of its `ranks` without `tier` and `range`, plus `type_own`: `{url, size}` (the hashed `/assets/fonts/…` URL and its size in bytes) or `null` |

Each source links to its credit at `/methodology/#source-<id>`.

## 9. URL hash

The view lives after `#`, so it never reaches the server and nothing is stored.

```
hash  = "#" [ pair *( "&" pair ) ]
pair  = key "=" value              ; value encoded with encodeURIComponent; list items joined by ","
key   = "rank" / "cat" / "var" / "nerd" / "hide" / "q" / "sort" / "font"
      / retired-key / ext-key      ; below
```

| Key | Value | Default (omitted) |
|---|---|---|
| `rank` | a view key with `available: true` | `project` (Projects) |
| `cat` | `sans-serif`, `serif`, `display`, `handwriting` or `monospace` (every monospaced font) | any category |
| `var` | `1`: variable fonts only | off |
| `nerd` | `1`: "Nerd Font available", fonts with a Nerd Font build only | off |
| `hide` | any of `limited` (shown as "Accented letters"), `attr` ("No credit required"), and at most one of `windows`, `macos`, `linux`, `android`, in this order; a second system is dropped | nothing hidden |
| `q` | search text, at most 100 characters | empty |
| `sort` | `rank-desc` (the whole rank order reversed, unranked fonts first), `name` (A–Z) or `name-desc` (Z–A) | `rank` (most popular first) |
| `font` | a font id: its details panel is open | none |

- **Writing:** keys in the table's order, defaults left out; the default view is the empty hash, restored with `history.replaceState(null, '', location.pathname + location.search)`. Discrete changes use `pushState`; search typing uses `replaceState`, debounced 300 ms.
- **Reading:** split on `&`, then each pair at its first `=`; a key seen twice keeps the last value; for the keys in the table, undecodable and invalid values fall back to the default. If the canonical form differs from `location.hash`, it is rewritten with `replaceState`. `popstate` and `hashchange` apply the hash without pushing.
- **Retired keys** (owner rulings of 2026-09-30): `spacing`, `lic` and `redist` are read and dropped, so old links still open and the rewritten hash no longer has them. `spacing=monospaced` becomes `cat=monospace` when the hash has no `cat`; `spacing=proportional`, `lic` and `redist` add nothing (every listed font is redistributable). Milestone 3 must not use these names as extension keys.
- **Extension keys.** A key not in the table and not retired belongs to someone else, for example Milestone 3's system tabs (`os=linux`). `State` never interprets or drops one: when it reads, canonicalises or writes the hash, it keeps every extension pair exactly as written (key and raw value), in its original order, after its own keys. When it writes, it takes the extension pairs from `location.hash` as they stand at that moment, so a change Milestone 3 made meanwhile survives. The default view with extension keys is `#` plus those pairs. `State`'s tests must show that `#rank=coding&os=linux` survives the first load, a filter change and Back.
- **In-page anchors.** `State` runs only on pages with `#list`. A hash of one token with no `=` or `&` that names an element in the document (the skip link's `#main`, `#font-<id>`) is not a view: `State` leaves `location.hash` alone for it, and its next write drops the anchor. A single token that names no element is read as a hash like any other.
- **`font`.** A font the current view hides stays in the hash; its panel opens when a later view shows it.
- `rank=rising` while Rising has `available: false` means the default, `project`. So does `rank=overall`: Overall retired on 2026-09-30 (owner ruling `overall_retired`) and has `available: false`, so an old link opens the default view and the rewritten hash drops the pair, as it drops a retired key.
- Example: `#rank=desktop_chosen&cat=serif&hide=limited,windows&font=inter`.

## 10. Milestone 3 hook (`globalThis.tff`)

`Ext` publishes one frozen object once the list is ready, then announces it:

```js
globalThis.tff = Object.freeze({
  version: 1,
  keys: Keys,                                    // { matchKey(s), searchKey(s), DROP_CODEPOINTS, CASEFOLD_EXTRA }
  list: Object.freeze({
    addFilter(id, { classify, note, affectsNumbering = false }),
                                                 // classify(fontId) -> 'show' | 'dim' | 'hide'
                                                 // note(fontId) -> null | string | { text?, badge?,
                                                 //   links?: [{ label, href }], actions?: [{ id, label }] }
                                                 //   shown in the row's div.ext slot (section 4), on any shown row
    removeFilter(id),
    refresh(),                                   // recompute after a filter's inputs change
    getState(),                                  // a copy of the state (section 9's keys, no extension keys)
    setState(partial, { push = true } = {}),     // validated like the hash
    index(),                                     // Promise of the list index (section 7), frozen
    on(type, fn),                                // 'change': fn({ state, shown, total }); returns an off() function
    setSummary(text),                            // text in #ext-summary; null hides it
    specimens: Object.freeze({ pause(), resume(), get paused() }),
  }),
});
document.dispatchEvent(new CustomEvent('tff:list-ready', { detail: globalThis.tff }));
```

- **Edge cases.** `addFilter` with an existing id replaces that filter in place; bad arguments throw `TypeError`. `classify` and `note` are called with the options object as `this`. A `classify` that throws or returns anything but `show`, `dim` or `hide` counts as `show`, and a `note` that throws gives no note; each is reported once per filter through `reportError`. Note links keep only relative same-site or `https://` hrefs. `on` knows only `'change'`. `setSummary('')` hides like `null`. `index()` is a deep-frozen copy, loaded once.
- Filters run in the order added; `hide` from any filter wins over `dim`. `affectsNumbering: true` filters run first (View step 3), then the others (step 6). Since scores replaced numbers (2026-09-29), no filter changes a score, so the flag no longer changes what is shown; it stays, as the hook is frozen.
- `dim` rows get `li.font.is-dim`; dimmed text must keep 4.5:1 contrast (no opacity). A note shows on any row that is shown, dimmed or not; when several filters give a note, each gets its own `div.ext`, in the order the filters were added.
- A click on `button.ext-action` dispatches `document` event `tff:row-action` with `detail: { filterId, fontId, actionId }` (delegated from `#list`). Focus stays on the button; if the action hides its row, focus moves to the `.details-toggle` of the next shown row, or to `#main` when none is left.
- `index()` gives what Milestone 3 needs for counts such as "You have 41 of the top 100 in this view": `r[rank].top[i] > 0` marks the exact top 100 of a view, and `ids[i]` names font `i`.
- Milestone 3 keeps its own URL state in extension keys (section 9), which `State` preserves.
- The hook works for either Milestone 3 design (M3-D8): the same list markup (`_list.html.j2`) on the home page or on a `/check/` page.
- **Open for Milestone 3 step 0:** the hook has no way to announce a change it makes (WCAG 4.1.3), for example `refresh({ announce })` or `announce(text)` routed to `Announce`. It is added then, as an additive v1 field, not by changing the frozen hook now.

## 11. Match JSON

`/assets/match.<h>.json` is the hopper's match data (Milestone 3, slice 1). The owner's ruling of 2026-10-22, `inventory_match_data_site_build`, amends Milestone 3 step 3: the build makes the file with `tff_catalog.owned.match_document`, from `names.json` and the catalog's ids (section 1), in `tff_site.matchfile`, and no pipeline stage writes it. It is canonical compact JSON like the other payloads, so the same inputs always give the same bytes and the same name. Its schema is `schemas/match.schema.json`, which docs/catalog-schema.md ("The hopper's match file") describes in plain words. `tff-site check` holds it to `budgets.MATCH_MAX` (50 KB gzip -9).

**What the build refuses**, listing every problem (`tff_site.matchfile.match_file`). A names file it can't read, of another run or of the wrong shape (in the stages of section 1), and a malformed reviewed list, are listed together and stop it first, since the checks read both. Past them, a refused recode and what the collision check finds come together, the check running without the refused recodes: a refresh that moves a recoded alias and changes the ambiguous set fails once, naming all of it, so the owner is asked once (ops/MONTHLY.md step 5). It refuses:

- a names file of another run, or of the wrong shape (section 1), or a reviewed list that is malformed;
- a `distinct_from` pair that meets (a name of one family that, stripped, is a key at which the other could count as owned: Inter Tight never reaches Inter, M3-D5 a). Only a catalog family can: one outside the catalog is coded `-` and never owned, so a name that reaches it hides nothing, and the build lets it pass;
- a key that a catalog family owns with another family, in the catalog or not: a list's name there would count the catalog family as owned (a key that only families outside the catalog share hides nothing, so the build lets it pass). Step 3 fails one "outside the ambiguous set", and the build fails one in it too: there the hopper doubts only an untrusted name, and a trusted one counts every owned code at the key, while a family that gains a name at a reviewed ambiguous key leaves the entry's `families` as they were, so the change would pass unreviewed (`inventory_ambiguous_names` fails the build "on any new, changed or dropped name until the owner has reviewed it");
- a catalog name that strips to the key of a family outside the catalog;
- an ambiguous set other than the reviewed one: an ambiguous name nobody has reviewed, a reviewed one whose families changed, or one that is no longer ambiguous;
- a recode whose key isn't an alias of exactly the family it names, whose family `names.json` has outside the catalog or whose aliases it already gives no owned relation (stale entries that change nothing), or that another name of that family bypasses (a patched build of the recoded name would own the family again, so it needs a recode of its own; every such name is listed in one run, and when the name is at the family name's key, which no recode touches, the message says so instead of asking for its recode);
- a match file that `owned.Table.from_document`, the reader the page mirrors, refuses.

Editing the reviewed list clears the ambiguous set's and the recodes' problems, and only some of the other three, which Milestone 3 step 3 fails "outside the ambiguous set" (and the build a shared key in it too): a `distinct_from` pair that meets, a key that a catalog family owns with another family, and a catalog name that strips to the key of a family outside the catalog (which the ambiguous set, holding catalog families only, can't take). No `[[ambiguous]]` entry clears them. A `[[recode]]` clears a meeting or a shared key when the build takes a recode at its key (`matchfile.recodable_keys`: the aliases there are of one catalog family alone, and not at its own name, and each other name of the family that reaches the key and would bypass the recode, as a HackGen NF build would HackGen's, is an alias the build recodes too, so none of the refusals above applies to the recodes it names) and that family's owned code there makes the problem: it is the meeting's other family, or a catalog family that shares the key with one other family, or only with families outside the catalog. The recode makes those aliases near-matches, which may share a key as a bundle that two families hold does, so that problem ends with `matchfile.RECODABLE`, naming the family and each name of it whose recode the build needs too (`Recodable.also`), so listing them all clears it in one round of review; another family's name at the key doesn't stop it, since a recode touches no family's name. The rest end with `matchfile.UNLISTABLE`, "no entry in the reviewed list clears this": a meeting at a family's own name, a shared key that families' own names keep shared whatever is recoded, a key whose aliases two families hold (the build refuses a recode there, which would recode both), a key whose recode a name the build can't recode would bypass (the family's own name, as for a family renamed "Sample Sans 08 Bold" that keeps "Sample Sans 08" as an old name, or an alias two families hold), and every stripped catalog name (stripping reads no relation). The owner rules on each: a recode it rules goes in the list on the refresh pull request, and otherwise an alias row through Milestone 1's review, or the change to the build that the ruling asks for, carries it out on `main` before the month runs again (ops/MONTHLY.md step 5). A catalog entry or exit can bring the last one: Archivo leaving the catalog while Archivo Black stays, or Alkalami Light entering while Alkalami stays out. A new catalog family whose name is another family's old name can bring a shared key that a recode clears.

**The reviewed list**, `names.reviewed.toml` beside the names file (section 1), is edited by hand after the owner's review, and each entry cites the ruling in `data/reviews/` that records it. `tests/test_site_contracts.py` checks that every cited ruling is recorded; a ruling still to be asked is listed in its `RULINGS_PENDING`, as a strict xfail that fails once the ruling is recorded, so the exception can't outlive the question. A missing list reviews nothing, so any ambiguous name fails the build. A refresh that changes what the list reviews fails until the list is edited on its pull request; the tests read the ambiguous set and the recodes from the list too, so they need no edit for either, except that an entry citing a newly recorded ruling also names that ruling in `tests/test_site_contracts.py` (`AMBIGUOUS_RULINGS` or `RECODES_RULED`, below). The hopper's known answers (`tests/vectors/owned-known-answers.json`) also hold on the committed names, so a refresh that drops a family they name, or changes their result, edits them on the same pull request (ops/MONTHLY.md step 5).

- `[[ambiguous]]` entries (`name`, `families`, `ruling`): names that are a family of their own and also, with a style word stripped, a catalog family's weight ("Archivo Black" may be Archivo's Black). The hopper treats them with doubt (`owned.match`): only a trusted name (fc-list's first family, a macOS family, or a PostScript name's part before its hyphen) counts as owned. The committed list holds the five the owner reviewed as a set: Archivo Black, Bungee Hairline, Gentium Book, Gentium Plus Book and Syne Italic (`inventory_ambiguous_names`, 2026-10-23). The ruling also covers Gentium Book as a weight of Gentium Plus, as on the October data, so that refresh edits the entry's families and cites the same ruling; `tests/test_site_contracts.py` checks that every entry citing it is one of those five, with the families it names. A refresh that drops one, adds a new name or makes another change to one of the five waits for the owner's review (ops/MONTHLY.md step 5); then a drop removes the entry, and a new name or a change cites the ruling that reviews it instead, so that test needs no edit (the new ruling is named by its kind, below). The build sees a change of the families, a drop, and a family gaining a name at the key (a shared key, above). An entry records only the families the name may be a weight of, not what owns its key, so a one-for-one change of owner, one family leaving `names.json` in the run in which another gains the name, still passes: whether entries also record the owners is a question for the owner. They only pass or fail the build: the match file's `amb` is derived, so they change none of the match file's bytes (`version.txt`'s `reviewed_sha256` still records the list, section 2).
- `[[recode]]` entries (`name`, `family`, `relation`, `ruling`): an alias that the hopper codes as a near-match only (`related`, `sibling` or `bundle`), though `names.json` gives it an owned relation. HackGen, a build of Hack in `names.json` through an alias row the ranking keeps, is only related to Hack here (`hopper_hackgen_related`, 2026-10-23). The ruling covers the names of Hack that reach HackGen's key too (`owned.candidates`): a HackGen NF build would own Hack again, so the build asks for its recode, which cites the same ruling. `tests/test_site_contracts.py` checks that every recode citing it is HackGen or such a name, on `hack`, as `related`, and the other way round, that every recode of such a name cites it, so HackGen's can't change relation or ruling unreviewed (a new ruling on HackGen is cited while it is in `RULINGS_PENDING`, and once recorded is added to the test's `RECODES_RULED` with what it covers); a recode of anything else cites the ruling that reviews it. No entry may cite a ruling of the other kind of entry: the test names each ruling the list cites by its kind (`RECODES_RULED`, with what it covers, or `AMBIGUOUS_RULINGS`) and fails on a recorded one in neither, so each new ruling is added there once recorded. A recode changes the match file's codes, and nothing in `names.json`.

**Loading** (M3-D7 a): the page names the file in `#list[data-match]` only (section 4). The script fetches it once a visitor's list arrives (a drop, a chosen file or a paste), from that same-origin hashed URL as the DOM gives it. The request has no query string and no body, and nothing from the list goes in it, so it is the same for every visitor, and the file is cached as immutable. The matching runs in the page. A 404 or 410 means the page is more than one deploy old: the receiver keeps one generation of assets for open tabs. The hopper then says the list was updated and asks for a reload, as `Data.Stale` does for the details payload (section 5). Any other failure gives an error message: a network error, another status, a body that isn't JSON, or a file the reader refuses (`format` not `tff-match`, `v` not 1, or a malformed part). The hopper never shows a result it couldn't compute, and never an empty one in its place.

| Key | Type | Contents |
|---|---|---|
| `format` | string | `"tff-match"` |
| `v` | int | format version, `1` |
| `run_date` | string | `run.date` of both files |
| `names_sha256` | string | sha256 of `names.json` in canonical form (`jsonio.canonical_bytes`). It is not the file's hash: `version.txt`'s `names_sha256` is that (section 2) |
| `ids` | string[] | the catalog's font ids, sorted; `<i>` in a code indexes this |
| `k` | object | the `match_key` (`Keys.matchKey`) of every known name → its codes, comma-joined: every `names.json` family's `family` and `names[]`, catalog or not, and every `ineligible[]` name. Look keys up in a `Map` or with `Object.hasOwn`: "Constructor" is a font name too |
| `amb` | object | ambiguous keys (step 3's collision check, `owned.check_document`) → the indexes of the catalog families their name may be a weight of |
| `reasons` | string[] | the ineligible reasons in use, sorted; `!<j>` indexes this |
| `words` | object | what `owned.candidates` strips (`owned.WORDS`): `whitespace`, `dashes`, `joiners` and `separators` (hex code points), `style`, `modifiers`, `postscript`, `abbreviations`, `variable`, `variable_joined`, `nerd`, `nerd_tokens`, `cased` |

**Codes**, one per catalog family per key (the strongest), by index; then `-` once at most; then `!` codes:

| Code | The name is | Owned? |
|---|---|---|
| `<i>` | the family `ids[i]` | yes |
| `<i>p` | its PostScript name | yes |
| `<i>b` | a build of the same font (`detail` empty, `variable`, `static`, `opsz`) | yes |
| `<i>r` | an old name (M3-D3 a) | yes |
| `<i>n` | a patched build (`nerd`, `nf`, `nfm`, `nfp`, `propo`, `powerline`, `nl`, `cjk`; M3-D4 a) | yes, while patched builds count |
| `<i>s` | a width sibling folded into it | never (M3-D5 a): a near-match |
| `<i>x` | a related family, or an alias the reviewed list recodes as related | never: a near-match |
| `<i>u` | a bundle or package holding it | never: a near-match |
| `-` | a known family outside the catalog | no; known, so no near-match |
| `!<j>` | an ineligible name, for `reasons[j]` | no |

**A worked example.** With the catalog ids `["foo-sans"]` and the run date 2026-01-01, this `names.json`:

```json
{
  "schema_version": "0.1.0-draft",
  "run": {"date": "2026-01-01", "method_version": "2026-01-01"},
  "families": [
    {"id": "foo-sans", "family": "Foo Sans", "in_catalog": true, "superfamily_id": null,
     "names": [
       {"name": "FooSans-Regular", "relation": "postscript"},
       {"name": "Foo Sans Variable", "relation": "build", "detail": "variable"},
       {"name": "Foo Grotesk", "relation": "rename"},
       {"name": "Foo Sans NF", "relation": "build", "detail": "nf"},
       {"name": "Foo Sans Gen", "relation": "build", "detail": ""},
       {"name": "Foo Sans Narrow", "relation": "sibling"},
       {"name": "Foo Text", "relation": "related"},
       {"name": "Foo Collection", "relation": "bundle"}
     ],
     "distinct_from": []},
    {"id": "foo-sans-black", "family": "Foo Sans Black", "in_catalog": false,
     "superfamily_id": null, "names": [], "distinct_from": []}
  ],
  "ineligible": [{"name": "Foo Icons", "reason": "icon"}]
}
```

with this reviewed list beside it ("Foo Sans Black" is ambiguous: a family, and also Foo Sans's Black; "Foo Sans Gen" is recoded):

```toml
[[ambiguous]]
name = "Foo Sans Black"
families = ["foo-sans"]
ruling = "example_ambiguous"

[[recode]]
name = "Foo Sans Gen"
family = "foo-sans"
relation = "related"
ruling = "example_recode"
```

gives this match file, `names_sha256` and `words` left out. `tests/test_site_contracts.py` checks it.

```json
{
  "format": "tff-match",
  "v": 1,
  "run_date": "2026-01-01",
  "ids": ["foo-sans"],
  "k": {
    "foocollection": "0u",
    "foogrotesk": "0r",
    "fooicons": "!0",
    "foosans": "0",
    "foosansblack": "-",
    "foosansgen": "0x",
    "foosansnarrow": "0s",
    "foosansnf": "0n",
    "foosansregular": "0p",
    "foosansvariable": "0b",
    "footext": "0x"
  },
  "amb": {"foosansblack": [0]},
  "reasons": ["icon"]
}
```
