# Site contract (Milestone 2)

This file freezes the interfaces between the parts of trulyfreefonts.com, so that several people or agents can build them at once. Each part may change freely inside itself, but a change to anything named here is a contract change: agree it with the lead engineer first, then update this file, the schema and the tests in the same pull request.

`tests/test_site_contracts.py` checks the parts of this file that a machine can check: the JS parts table, the CSS tokens, the template blocks and the sample catalog.

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

## 1. Data in

- **`catalog-site.json`**, draft schema `schemas/catalog-site.schema.json` (`schema_version` `1.0.0-draft`; Milestone 1 step 20 freezes v1). `tff_site.data.validate` runs the schema and then the cross-reference checks the schema can't express (`semantic_errors`). `tff-site validate FILE` prints `valid (1.0.0-draft), N fonts`.
  - **Stricter than JSON:** a key repeated in one object, `NaN` and `Infinity` make the file invalid (`tff_site.data.loads`), because the schema would check only one of the repeated values while the page showed another.
- **Wording** in the catalog (view labels and measures lines, tiers, license classes, the Nerd Font marker and legend, source credits, system labels) comes from `config/site.toml`, the one home for the owner's wording rulings about the data; Milestone 1's export copies it, and a contract test keeps the sample equal to it. The page's own text (the list page's lead and its note "Why isn't my favorite free font here?", the filter labels, the tip link) lives in the templates, and where the owner ruled on it, a test holds it to the ruling in `data/reviews/site/`.
- **Nerd Font builds** (owner rulings of 2026-09-28 and 2026-09-29, TASK-2). A font whose `links.nerd` is set has a Nerd Font build: the list shows the catalog's `nerd.marker` ("NF") beside its name, named `nerd.label` ("Nerd Font version available") for screen readers, the list and the details panel show `nerd.legend`, and the "Nerd Font available" filter keeps only these fonts. The sample gives one to `sample-mono-02` (a Nerd Fonts folder) and `sample-mono-13` (a maker's own build).
  - `links.nerd` is null, so the font shows no marker, no Nerd link and no filter match, when the font has no build, when the build's link failed the link check recorded for the run's date (the font stays listed; the link is back once a check passes, and `review.md` flags it), or when the owner hides the build (`config/nerd-hidden.toml`, with the reason). The site never decides any of this itself: it shows what the data gives.
- **The sample**, `tests/fixtures/catalog-site.sample.json`: 40 invented fonts (`"synthetic": true`, ids `sample-*`, families `Sample …`). Five of them point `font_file` at the real OFL files pinned in `tests/fixtures/specimen-fonts.toml`, so specimens and "Type your own text" run on real outlines: `sample-sans-01` (Inter), `sample-mono-02` (JetBrains Mono), `sample-sans-05` (Source Sans 3, CFF), `sample-display-10` (Orbitron, basic Latin only) and `sample-script-12` (Lobster).
- **Specimens.** `preview.path` is `specimens/<id>.svg`, relative to the directory holding the data file: `build/specimens/` for real data, `tests/fixtures/specimens/` for the sample. The build copies each one after checking its sha256. A specimen may not contain `<script`, `<foreignObject`, an `on…=` handler, or an `href` other than `#…` (a link inside the file); the build refuses it.
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
| `index.html` | the list, server-rendered in Overall order | `no-cache, no-transform` |
| `methodology/index.html`, `privacy/index.html`, `about/index.html` | the content pages | `no-cache, no-transform` |
| `404.html` | served with status 404 by Caddy's `handle_errors` | `no-cache, no-transform` |
| `robots.txt`, `sitemap.xml` | per M2-D8 | `no-cache, no-transform` |
| `version.txt` | `commit=`, `run_date=`, `method_version=`, `catalog_sha256=` (the sha256 of the `catalog-site.json` file's bytes), `schema=catalog-site/1`, one per line; never a build time | `no-cache, no-transform` |
| `favicon.ico`, `favicon.svg`, `apple-touch-icon.png`, `share.png`, `wordmark.svg` | copied from `site/static/` (these five files only; `site/static/_src/` holds their generators and sources and is never published). `wordmark.svg` is the header's wordmark, "Truly Free Fonts" in League Gothic drawn as outlines (AUTHORITY.md, "Headline font") | `no-cache, no-transform` |
| `assets/app.<h>.js` | the one script: `site/js/*.js` concatenated (section 5) | `public, max-age=31536000, immutable` |
| `assets/style.<h>.css` | the one stylesheet: `site/css/*.css` concatenated (section 6) | immutable |
| `assets/list.<h>.json` | the list index (section 7) | immutable |
| `assets/details.<h>.json` | the details payload (section 8), fetched on first use | immutable |
| `assets/specimens/<id>.<h>.svg` | one specimen per font with a preview | immutable |
| `assets/fonts/<id>.<h>.<ext>` | the unchanged upstream font file, for "Type your own text" | immutable |
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
| `footer` | inside `<footer class="site-footer">`: everything below | feedback, tip, source link |
| `feedback` | inside `<div class="feedback" id="feedback">`: the one feedback spot, the same on every page | issue forms and email |
| `tip_link` | the tip link, shown only when `site.tip_url` is set | `<p class="tip" id="tip">` |

**Context every page gets:**

| Name | Fields |
|---|---|
| `site` | `name` ("Truly Free Fonts"), `base_url` ("https://trulyfreefonts.com"), `repo_url`, `feedback` {`issues_url`, `email`, `mailto`}, `tip_url` (the Stripe link from ops/DONATIONS.md, or none until M2 step 8), `blog` (none until a post is published; then {`url` "/blog/", `feed_url` "/blog/feed.xml"}, which adds the nav's Blog link and a `<link rel="alternate">` to the feed) |
| `page` | `path` ("/", "/methodology/" …), `title`, `description`, `canonical` (false on the 404 page) |
| `assets` | `css`, `js` (hashed URLs) |
| `build` | `commit`, `run_date` |
| `urls` | the absolute URLs of the canonical pages, sorted by path, for the sitemap |

**Extra context for `index.html.j2`**, as `list`:

| Field | Contents |
|---|---|
| `views` | the available views, `{key, label, measures}`, in catalog order; the first is the default (Overall, M2-D1) |
| `categories` | `{value, label}`: `sans-serif` Sans serif, `serif` Serif, `display` Display, `handwriting` Handwriting, `monospace` Monospace |
| `license_classes` | `{id, label}` from the catalog |
| `systems_os` | `{value, label}`: `windows` Windows, `macos` macOS, `linux` Linux, `android` Android |
| `nerd` | the catalog's `nerd` wording, `{marker, label, legend}`, plus `after_marker`: the legend less its leading marker, so the legend shows its marker as the rows do |
| `total` | number of fonts |
| `index_url`, `details_url` | hashed URLs of the two payloads |
| `rows` | one per font, in server order (section 7): `id`, `family`, `label` (the Overall rank label), `category_label`, `license_name`, `badges` [{`key`, `text`}], `specimen` {`url`, `width`, `height`} or none (`width` and `height` are the no-script `<img>`'s display size: 48 px high, the `--spec-h` box, and as wide as the SVG's aspect ratio makes it), `fallback` (none, `"license"` or `"failed"`), `download` {`url`, `label`}, `nerd` (true for a font with a Nerd Font build, `links.nerd`) |

Badge keys, in this order: `variable`, `monospace`, `limited` ("Limited accents"), `attribution` ("Attribution required"), `noredist` ("Not redistributable"), `preinstalled` ("Comes with Windows 11, macOS"), `pulled` ("Pulled in by sample-office-common on Debian", from `pulled_in_by`; several are joined with "; "), `new` ("New"). `preinstalled` and `pulled` are the tags that explain "Not ranked: no evidence of deliberate installs" in *most chosen* (Milestone 2 step 3).

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
  <h1>…</h1>
  <div class="why why-wide">                          <!-- the owner's note; wide screens only -->
    <h2 id="why-h">Why isn't my favorite free font here?</h2>
    <p>Not every font that's free to download … email
      <a href="mailto:admin@trulyfreefonts.com">admin@trulyfreefonts.com</a> and ask us to consider it.</p>
  </div>
  <p class="lead">…</p>
  <p class="privacy-note">No cookies, no tracking, and the page loads only its own files.
    <a href="/privacy/">Check the Network tab</a>.</p>
  <details class="why why-fold">                       <!-- the same note; phones only -->
    <summary>Why isn't my favorite free font here?</summary>
    <p>…the same text…</p>
  </details>
  <div class="layout">
    <search id="filters" class="filters" aria-label="Filter fonts" hidden>  <!-- the script removes hidden -->
      <div class="filters-bar">                                       <!-- always visible: search and rank -->
        <label for="f-q">Search fonts</label>
        <input id="f-q" name="q" type="search" maxlength="100" autocomplete="off" spellcheck="false">
        <label for="f-rank">Rank</label>
        <select id="f-rank" name="rank" aria-describedby="f-rank-measures">…one option per available view…</select>
        <p id="f-rank-measures" class="measures">…the view's measures line…</p>
        <button type="button" id="f-toggle" class="filters-toggle" aria-expanded="false" aria-controls="f-more">
          Filters<span class="filters-count"> (3)</span></button>             <!-- narrow screens only -->
      </div>
      <div id="f-more" class="filters-more">
        <fieldset id="f-cat"><legend>Category</legend>
          <input type="radio" id="f-cat-all" name="cat" value="" checked> …
          <input type="radio" id="f-cat-serif" name="cat" value="serif"> …  <!-- f-cat-<value> -->
        </fieldset>
        <fieldset id="f-spacing"><legend>Spacing</legend>                  <!-- site ruling 2026-09-25 -->
          <input type="radio" id="f-spacing-any" name="spacing" value="" checked> …                  <!-- Any -->
          <input type="radio" id="f-spacing-proportional" name="spacing" value="proportional"> …  <!-- Proportional -->
          <input type="radio" id="f-spacing-monospaced" name="spacing" value="monospaced"> …      <!-- Monospaced -->
        </fieldset>
        <fieldset id="f-type"><legend>Type</legend>
          <input type="checkbox" id="f-var" name="var" value="1">            <!-- Variable only -->
          <input type="checkbox" id="f-nerd" name="nerd" value="1" aria-describedby="nf-legend">  <!-- Nerd Font available -->
          <input type="checkbox" id="f-hide-limited" name="hide" value="limited">
        </fieldset>
        <fieldset id="f-license"><legend>License</legend>
          <input type="checkbox" id="f-lic-open-font" name="lic" value="open-font"> …  <!-- f-lic-<class id> -->
          <input type="checkbox" id="f-hide-attr" name="hide" value="attr">
          <input type="checkbox" id="f-redist" name="redist" value="1" aria-describedby="f-redist-help">
          <p id="f-redist-help">…what redistributing means…</p>
        </fieldset>
        <fieldset id="f-system"><legend>Hide fonts that come with</legend>
          <input type="checkbox" id="f-hide-windows" name="hide" value="windows"> …  <!-- f-hide-<os> -->
        </fieldset>
        <fieldset id="f-sort"><legend>Sort</legend>
          <input type="radio" id="f-sort-rank" name="sort" value="rank" checked>
          <input type="radio" id="f-sort-name" name="sort" value="name">
        </fieldset>
        <button type="button" id="f-clear">Clear filters</button>
      </div>
    </search>
    <noscript><p class="noscript-note">Filters and search need JavaScript. Below is the full list by overall rank.</p></noscript>
    <section id="results" class="results" aria-labelledby="results-h">
      <h2 id="results-h">Fonts</h2>
      <p id="ext-summary" class="ext-summary" hidden></p>             <!-- Milestone 3: tff.list.setSummary -->
      <!-- Main inserts <p id="load-note" class="noscript-note"> here, before #count, when the
           list index fails to load or doesn't match the rows; the server's list stays. -->
      <p id="count" class="count">Showing 540 of 540 fonts</p>
      <p id="nf-legend" class="nf-legend"><span class="nf-mark">NF</span>: Nerd Font version available
        (adds developer icons, which have their own licenses).</p>     <!-- the catalog's nerd.legend -->
      <div id="status" class="visually-hidden" role="status"></div>  <!-- Announce's only live region -->
      <div id="no-results" class="no-results" hidden>
        <p id="no-results-text">…names the filters to loosen…</p>
        <button type="button" id="no-results-clear">Clear filters</button>
      </div>
      <ol id="list" class="font-list" data-index="/assets/list.<h>.json"
          data-details="/assets/details.<h>.json" data-run-date="2026-09-25">…rows…</ol>
    </section>
  </div>
</main>
```

- **The front page's note** (site rulings of 2026-09-29, `data/reviews/site/2026-09-29.toml`: `why_not_listed`, `why_not_listed_layout`): "Why isn't my favorite free font here?" and the owner's text, word for word, in `index.html.j2`, the address a `mailto:` link to `site.feedback.email`. It is in the HTML twice and the stylesheet displays one, so neither needs a script and nothing moves as the page loads: `div.why-wide` from `40rem` up, a frame floated right beside the lead and the privacy note, which wrap around it, while `.layout` clears it, so the list starts below the frame and rows are never narrowed; `details.why-fold` at `40rem` and below, full width under the intro, folded to its `summary`. The other copy is `display: none`, so screen readers get the text once.
- The controls' `name` attributes are the hash keys (section 9); `value` is the key's value. Filters live in `<search>` and `<fieldset>`/`<legend>` groups, never in a `<form>`.
- `#filters` carries `hidden` in the HTML; the script removes it. Showing it must not move the list (reserve its space in CSS), because the layout-shift budget is 0.1.
- Narrow screens are below `60rem`: `#f-more` is hidden until `#f-toggle` expands it, and `#f-toggle`'s text includes the number of active filters. From `60rem` up, `#f-more` is always shown in the sidebar and `#f-toggle` is hidden.
- `#f-spacing` (Any / Proportional / Monospaced) is shown on every rank; it replaces the old "Text only" and "Monospace only" boxes (owner's site ruling of 2026-09-25, `data/reviews/site/2026-09-25.toml`). Proportional hides monospace fonts. On Coding, whose fonts are all monospace, Proportional leaves nothing, and `#no-results-text` names the Spacing filter.
- `#f-nerd` ("Nerd Font available", owner's site ruling of 2026-09-29, `data/reviews/site/2026-09-29.toml`, `nerd_filter`) keeps only the fonts with a Nerd Font build, on every rank. `#nf-legend` describes it and explains the rows' marker: the catalog's `nerd.legend`, its leading marker shown as `span.nf-mark`.

### A row

```html
<li class="font" id="font-inter" data-id="inter">
  <div class="font-row">
    <span class="rank">1</span>                                  <!-- number, band, or "Not ranked: <reason>" -->
    <h3 class="font-name" id="font-inter-name">Inter</h3>
    <span class="nf-mark" role="img" aria-label="Nerd Font version available">NF</span>  <!-- only with links.nerd -->
    <div class="font-spec">
      <span class="spec" role="img" aria-label="Inter sample" data-src="/assets/specimens/inter.<h>.svg"></span>
      <noscript><img class="spec-img" src="/assets/specimens/inter.<h>.svg" alt="Inter sample"
        width="…" height="…" loading="lazy"></noscript>
      <!-- or, with no specimen: -->
      <p class="spec-fallback">No preview: this font's license doesn't let us host its files.
        See it on <a href="…">GitHub: rsms/inter</a>.</p>        <!-- fallback "license" -->
      <p class="spec-fallback">Preview not available yet.</p>    <!-- fallback "failed" -->
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
- **The Nerd Font marker** (owner rulings of 2026-09-29, TASK-2): a font with a Nerd Font build (`links.nerd`) has `span.nf-mark[role=img]` right after its `h3`, on every rank: the catalog's `nerd.marker` as text, `nerd.label` as its accessible name. It sits in the name's grid cell, at its end, at a fixed width and no taller than the name's first line, in the width every row's name leaves free (`.font-name`'s end padding, marker or not), so no row's height depends on it. The heading's text stays the family name alone.
- `span.spec[data-src]` is always `/assets/specimens/<id>.<h>.svg`. `Specimens` ignores any other value, so a `data-src` can never break out of the CSS `url("…")` it becomes.
- `Render` moves rows in and out of `#list` (hidden rows are detached, not given `hidden`) and changes only `.rank` text. Rows carry `content-visibility: auto`, so they must not change height when their specimen arrives.
- **States set by scripts:** `html[data-js]` once the script runs; `span.spec[data-state="set"]` once its mask is set; `li.font.is-dim` for a row a Milestone 3 filter dims; `li.font.is-unranked` for a row whose `.rank` reads "Not ranked: <reason>" (the server sets it too), which puts that label on a line of its own above the name (the owner's site ruling of 2026-09-26).
- **The Milestone 3 slot** (section 10). For each filter whose `note` gives a row something to show, `Render` adds one `<div class="ext" data-filter="<filter id>">` at the end of `.font-row` (created on demand, removed when that filter no longer has a note for the row), built with `Core.el` only:
  - `span.ext-badge` for `badge`, `p.ext-note` for `text`;
  - one `a.ext-link` per link (`href` relative or `https://`, anything else dropped);
  - one `button.ext-action[type=button][data-action="<action id>"]` per action.

  Rows may grow when a note appears; that happens only after the visitor starts Milestone 3's check, so it is not a load-time layout shift.
- **Details panel** (owned by `Details`; only these hooks are frozen): the panel's first child is `<h4 class="details-title" id="details-<id>-h" tabindex="-1">`, which receives focus when the panel opens from a `#font=` link, and it has a `button.details-close`. Esc or the close button returns focus to the row's `.details-toggle`. "Type your own text" is `button.typeown-load` ("Load font (312 KB) to type your own text") and then `input.typeown-input`. A font with a Nerd Font build lists its link after the official and designer links: the term is the marker (`span.nf-mark[role=img]`, as in the row), the link `a.details-nf-link` (text by section 1's Nerd rule), and `p.details-nf-legend` below the links holds `nerd.legend`.

### Shared classes

`visually-hidden` (hidden from sight, read by screen readers), `skip-link`, `site-header`, `site-name`, `site-name-mark`, `site-nav`, `site-footer`, `feedback`, `tip`, `footer-meta`.

**The header** (owner ruling of 2026-09-29): `a.site-name[href="/"]` holds only `img.site-name-mark[src="/wordmark.svg"]`, whose `alt` is the site's name ("Truly Free Fonts"), with `width` and `height` in the wordmark's 8275:1862 ratio (213 x 48). It is about 48 px tall on wide screens and 32 px on phones (below `40rem`). The favicon is the browser's icon only, not in the header. The header is white in both themes, behind the black wordmark: it takes the `--c-header-*` tokens, whose dark values are the light ones, so its links and text keep their light-theme colours and its focus ring 3:1 on white. Under forced colours it takes the system colours, and the wordmark keeps a white plate (`--c-wordmark-bg`).

## 5. JS parts

The build concatenates `site/js/*.js` in filename order into one ES module, `/assets/app.<h>.js`, loaded by `<script type="module" src>`. The rule, which the build's lint enforces:

- Each part declares **exactly one** top-level binding: `const <Name> = …;`, usually an IIFE that returns a frozen object. Top-level lines start at column 0; everything inside is indented.
- No other top-level declaration (`let`, `var`, `function`, `class`, a second `const`), and no `import`, `export` or dynamic `import(`. The one top-level statement allowed besides the declarations is `Main.start();`, exactly once, as the last top-level line of `90-main.js`.
- Any other top-level line only closes the declaration: closing brackets, a call's parentheses and arguments, and one final `;` (`})();`, `})(Core);`), so no second statement can hide behind it.
- A part's initializer may use only lower-numbered parts. Its functions may call any part once `Main.start()` runs.
- Never, anywhere, comments included: `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `eval(`, `Function(`, `document.write`. Build nodes with `Core.el` and `textContent`.
- `fetch()` takes only relative URLs read from the DOM (`#list[data-index]`, `#list[data-details]`). No other request API: no `sendBeacon`, `XMLHttpRequest`, `WebSocket` or `EventSource`. No `setAttribute('style'` either (the CSP blocks style attributes; set styles through CSSOM).
- Nothing is stored: no `localStorage`, `sessionStorage`, `indexedDB`, `document.cookie`, `caches`, service workers or `cookieStore`. The privacy tests fail on any use.

| File | Const | Owner | Status | Responsibilities |
|---|---|---|---|---|
| `00-core.js` | `Core` | wave 0 | written | DOM helpers: `$`, `$$`, `el`, `append`, `text`, `clear`, `setHidden`, `on` (delegation), `debounce`, `idle`, `painted`, `formatBytes`, `formatCount`, `plural` |
| `05-keys.js` | `Keys` | A7 | written | `matchKey(s)`, `searchKey(s)`, `DROP_CODEPOINTS` and `CASEFOLD_EXTRA` (equal to `tests/vectors/name-keys.json`'s `spec`; casefold is the table, then `toLowerCase()` with ß→ss and ς→σ, one code point at a time) |
| `10-data.js` | `Data` | A3 | written | `loadIndex()` and `loadDetails()` (memoised promises; details only on first use; a 404 rejects with `Data.Stale`, shown as "The list was updated. Reload to see details.") |
| `15-state.js` | `State` | A3 | written | the state object, hash parse and serialise (section 9), `pushState` for discrete changes, `replaceState` for search (300 ms), `popstate`/`hashchange` |
| `20-view.js` | `View` | A3 | written | pure `compute(state, index, filters)` → `{order, labels, dimmed, notes, shown, total}` (below) |
| `25-render.js` | `Render` | A3 | written | reorders server-rendered rows through a `DocumentFragment`, updates `.rank`, `#count`, `#no-results`, keeps focus |
| `30-filters-ui.js` | `FiltersUI` | A3 | written | the controls in `#filters`: reads and reflects state, the phone disclosure, `#f-rank-measures` |
| `35-announce.js` | `Announce` | A3 | written | `#status`: immediate for discrete changes (coalesced per microtask), 500 ms debounce for search, silent on first load |
| `40-details.js` | `Details` | A4 | written | `open(id, {focus})`, `close()`, the panel built with `Core.el`, "Type your own text" (`FontFace` + CSSOM `style.fontFamily`). Owns `.details-toggle` clicks. Before `tff:list-ready` it writes the hash's `font` pair itself, keeping every other pair as written. After it, it records `font` through `State.set` and follows `State.subscribe`, having subscribed after `Main` |
| `45-specimens.js` | `Specimens` | A6 | written | IntersectionObserver on `li.font` (`rootMargin: '600px 0px'`) setting `mask-image`; `start()`, `pause()`, `resume()`, `paused` |
| `50-ext.js` | `Ext` | A12 | written | builds `globalThis.tff` (section 10) and dispatches `tff:list-ready`; keeps the external filters, read by `View.compute` through `Ext.filters()`; `Ext.start(host)` takes `Main`'s `{ refresh, onChange, ready }` |
| `90-main.js` | `Main` | A3 | written | `start()`: sets `html[data-js]`, wires the parts, event delegation on `#list` (including `button.ext-action`, section 10), indexing in idle callbacks |

**`View.compute(state, index, filters)`** is pure and unit-tested through `page.evaluate`:

1. The universe is the fonts in the rank's universe (tier character not `.`, section 7).
2. Apply the filters and search (`Keys.searchKey(state.q)` as a substring of `keys[i]`).
3. Apply the external filters with `affectsNumbering: true`.
4. Sort ranked fonts by `order`, then unranked fonts by `by_name`.
5. Number per M2-D2: a counter counts only fonts with `top > 0`; others take their band label; unranked fonts take "Not ranked: <why label>".
6. Apply the external filters with `affectsNumbering: false` (Milestone 3's "numbers stay as published").
7. With `sort=name`, re-sort by `by_name`, keeping the labels.
8. Return `order` (font indexes to show, in order), `labels`, `dimmed` and `notes` (parallel to `order`), `shown` and `total` (the size of the rank's universe).

## 6. CSS parts and tokens

`site/css/*.css` is concatenated in filename order into `/assets/style.<h>.css`: `00-tokens.css` (wave 0), `10-base.css` (A14), `20-list.css` and `25-filters.css` (A2), `30-details.css` (A4), `35-specimens.css` (A6), `40-pages.css` (A8), `45-blog.css` (the blog, step 7b).

- Colours come only from the tokens; no other part writes a colour value.
- Focus: `outline: var(--focus-ring); outline-offset: var(--focus-offset);` on `:focus-visible`. No sticky header, so focus is never hidden (2.4.11).
- Breakpoints (custom properties don't work in media queries, so these are fixed): narrow below `60rem` (filters behind the button), phone below `40rem` (specimen box 40 px).
- Specimens: `.spec { mask-size: contain; mask-repeat: no-repeat; mask-position: left center; height: var(--spec-h); }` and `.spec[data-state="set"] { background-color: var(--c-spec); }`, so the box fills only once its mask is set (an unmasked fill is a solid bar); `.spec` is hidden under `(scripting: none)`, where the `<noscript>` image shows instead; under `forced-colors`, `forced-color-adjust: none` (the token becomes `CanvasText`). The noscript image gets `filter: invert(1)` in dark mode.
- No `@import`, no `@font-face`, and no `url()` with a scheme or another host, a `data:` URL included: the page loads only its own files. The build's lint refuses them.
- Rows: `li.font { content-visibility: auto; contain-intrinsic-size: auto var(--row-est-h); }`.

**Tokens** (defined in `00-tokens.css` on `:root`; every `--c-` token is redefined for dark mode and for forced colours):

| Token | Use |
|---|---|
| `--font-ui` | the system font stack for all interface text |
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
| `--spec-h` | specimen box height: 48 px, 40 px on phones |
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
| `--c-accent` | primary buttons |
| `--c-accent-fg` | text on `--c-accent` |
| `--c-badge-bg` | badge background |
| `--c-badge-fg` | badge text |
| `--c-warn-bg` | "Not redistributable" badge background |
| `--c-warn-fg` | "Not redistributable" badge text |
| `--c-spec` | specimen fill: `currentColor`, `CanvasText` under forced colours |
| `--c-header-bg` | the header's background: white in both themes |
| `--c-header-fg` | the header's text (the light `--c-fg`, in both themes) |
| `--c-header-muted` | the header's secondary text (the light `--c-muted`) |
| `--c-header-link` | the header's links (the light `--c-link`) |
| `--c-header-focus` | the focus outline inside the header (the light `--c-focus`) |
| `--c-wordmark-bg` | the plate behind the black wordmark: white, under forced colours too |

## 7. List index JSON

`/assets/list.<h>.json`, built by `tff_site.data.list_index`, preloaded by `<link rel="preload" as="fetch" crossorigin href>` in the list page's `head` block and fetched from `#list[data-index]`. Columnar: every per-font array has `n` entries, indexed by font index.

**Font index** `i` is the server-rendered order: fonts ranked in Overall by `order`, then fonts unranked in Overall by Python `str.casefold()` of the family, then by id.

| Key | Type | Contents |
|---|---|---|
| `v` | int | format version, `1` |
| `commit` | string | the commit in `version.txt` |
| `run_date` | string | `run.date` |
| `n` | int | number of fonts |
| `ids` | string[n] | font ids |
| `cats` | string[] | the five categories, in schema order |
| `cat` | int[n] | index into `cats` |
| `lics` | string[] | license class ids, in `license_classes` order |
| `lic` | int[n] | index into `lics` |
| `bits` | int[n] | flags, below |
| `keys` | string[n] | `search_key` of the family, then of each alias in catalog order, joined by `\|` |
| `by_name` | int[n] | font indexes sorted by Python `str.casefold()` of the family, then id |
| `views` | object[] | the catalog's `views`, unchanged |
| `bands` | string[] | band labels, in order |
| `why_labels` | string[] | unranked-reason labels, in the order `no_deliberate_evidence`, `no_evidence`, `too_new` |
| `r` | object | one entry per view with `available: true`, keyed by rank key, below |

**`bits`**: 1 monospace (`is_monospace`), 2 variable, 4 limited accents (`latin.coverage` basic), 8 attribution required, 16 not redistributable, 32 has a specimen, 64 "Type your own text" available, 128 comes with Windows, 256 macOS, 512 Linux, 1024 Android (from `preinstalled_on` systems' `os`; a system with `os` `app`, an application's own bundle such as LibreOffice's, sets none; `pulled_in_by` doesn't count), 2048 new (flag `too_new`), 4096 pulled in by a package (`pulled_in_by` is not empty; no filter hides by it), 8192 a Nerd Font build (`links.nerd` is not null; "Nerd Font available" keeps only these, site ruling 2026-09-29). Spacing Proportional hides the fonts with bit 1 and Monospaced keeps only them (site ruling 2026-09-25).

**`r.<rank key>`:**

| Key | Type | Contents |
|---|---|---|
| `order` | int[] | the font indexes ranked in this view, by `order` |
| `top` | int[n] | exact rank 1–100, or 0 |
| `band` | int[n] | index into `bands`, or -1 |
| `tier` | string (n chars) | `A`, `B` or `C` if ranked; `-` if in the view's universe but unranked; `.` if not in the universe (a non-monospace font in Coding) |
| `why` | int[n] | index into `why_labels` for unranked fonts, else -1 |

## 8. Details JSON

`/assets/details.<h>.json`, built by `tff_site.data.details`, fetched from `#list[data-details]` on the first details open or a `#font=` load.

| Key | Contents |
|---|---|
| `v` | `1` |
| `run_date` | `run.date` |
| `views`, `bands`, `tiers`, `sources`, `systems`, `license_classes`, `nerd` | as in the catalog |
| `state_labels` | `{observed, censored, not_covered, too_new}` → label (`censored` → "below the floor") |
| `why_labels` | `{no_deliberate_evidence, no_evidence, too_new}` → label |
| `report` | `{issue_url, email}`: `https://github.com/byronshock/trulyfreefonts/issues/new?template=license.yml`, to which `Details` appends `&font_id=<id>&data_date=<run_date>`, and the fallback address for a `mailto:` link |
| `fonts` | `{<id>: font}`: the catalog's font object without `preview` and `font_file`, plus `type_own`: `{url, size}` (the hashed `/assets/fonts/…` URL and its size in bytes) or `null` |

Each source links to its credit at `/methodology/#source-<id>`.

## 9. URL hash

The view lives after `#`, so it never reaches the server and nothing is stored.

```
hash  = "#" [ pair *( "&" pair ) ]
pair  = key "=" value              ; value encoded with encodeURIComponent; list items joined by ","
key   = "rank" / "cat" / "lic" / "spacing" / "var" / "nerd" / "hide" / "redist" / "q" / "sort" / "font"
      / ext-key                    ; an extension key, below
```

| Key | Value | Default (omitted) |
|---|---|---|
| `rank` | a view key with `available: true` | `overall` |
| `cat` | `sans-serif`, `serif`, `display`, `handwriting` or `monospace` | any category |
| `lic` | license class ids, in `license_classes` order: show only these | any license (nothing chosen) |
| `spacing` | `proportional` (hides monospace fonts) or `monospaced` (monospace fonts only), on every rank | any |
| `var` | `1`: variable fonts only | off |
| `nerd` | `1`: "Nerd Font available", fonts with a Nerd Font build only | off |
| `hide` | any of `limited`, `attr`, `windows`, `macos`, `linux`, `android`, in this order | nothing hidden |
| `redist` | `1`: "Redistributable fonts only" | off |
| `q` | search text, at most 100 characters | empty |
| `sort` | `name` | `rank` |
| `font` | a font id: its details panel is open | none |

- **Writing:** keys in the table's order, defaults left out; the default view is the empty hash, restored with `history.replaceState(null, '', location.pathname + location.search)`. Discrete changes use `pushState`; search typing uses `replaceState`, debounced 300 ms.
- **Reading:** split on `&`, then each pair at its first `=`; a key seen twice keeps the last value; for the keys in the table, undecodable and invalid values fall back to the default. If the canonical form differs from `location.hash`, it is rewritten with `replaceState`. `popstate` and `hashchange` apply the hash without pushing.
- **Extension keys.** A key not in the table belongs to someone else, for example Milestone 3's system tabs (`os=linux`). `State` never interprets or drops one: when it reads, canonicalises or writes the hash, it keeps every extension pair exactly as written (key and raw value), in its original order, after its own keys. When it writes, it takes the extension pairs from `location.hash` as they stand at that moment, so a change Milestone 3 made meanwhile survives. The default view with extension keys is `#` plus those pairs. `State`'s tests must show that `#rank=project&os=linux` survives the first load, a filter change and Back.
- **In-page anchors.** `State` runs only on pages with `#list`. A hash of one token with no `=` or `&` that names an element in the document (the skip link's `#main`, `#font-<id>`) is not a view: `State` leaves `location.hash` alone for it, and its next write drops the anchor. A single token that names no element is read as a hash like any other.
- **`font`.** A font the current view hides stays in the hash; its panel opens when a later view shows it.
- `rank=rising` while Rising has `available: false` means `overall`.
- Example: `#rank=project&cat=serif&spacing=proportional&hide=limited,windows&redist=1&font=inter`.

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
- Filters run in the order added; `hide` from any filter wins over `dim`. `affectsNumbering: false` keeps the published numbers (View step 6); `true` renumbers (View step 3).
- `dim` rows get `li.font.is-dim`; dimmed text must keep 4.5:1 contrast (no opacity). A note shows on any row that is shown, dimmed or not; when several filters give a note, each gets its own `div.ext`, in the order the filters were added.
- A click on `button.ext-action` dispatches `document` event `tff:row-action` with `detail: { filterId, fontId, actionId }` (delegated from `#list`). Focus stays on the button; if the action hides its row, focus moves to the `.details-toggle` of the next shown row, or to `#main` when none is left.
- `index()` gives what Milestone 3 needs for counts such as "You have 41 of the top 100 in this view": `r[rank].top[i] > 0` marks the exact top 100 of a view, and `ids[i]` names font `i`.
- Milestone 3 keeps its own URL state in extension keys (section 9), which `State` preserves.
- The hook works for either Milestone 3 design (M3-D8): the same list markup (`_list.html.j2`) on the home page or on a `/check/` page.
- **Open for Milestone 3 step 0:** the hook has no way to announce a change it makes (WCAG 4.1.3), for example `refresh({ announce })` or `announce(text)` routed to `Announce`. It is added then, as an additive v1 field, not by changing the frozen hook now.
