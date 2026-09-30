// 40-details: the details panel under each row (site/CONTRACT.md sections 4, 8 and 9).
//
// One panel is open at a time, inside the list. Opening one from its row pushes #font=<id>;
// a load, Back, Forward or a link with #font=<id> opens that font's panel and focuses its
// heading. The panel is built with Core.el and textContent only, from the details payload
// that Data.loadDetails() fetches on first use; a 404 there means the page is older than the
// deploy, and the panel says so. "Type your own text" loads the font's unchanged file with
// FontFace only after a click, and sets the input's font through CSSOM (element.style),
// which the CSP allows; nothing sets a style attribute.
//
// The font key of the hash: until the list is live ('tff:list-ready'), Details reads and
// writes it itself, rewriting only the font pair and keeping every other pair as written.
// From then on it follows State, like every other key: a panel opened or closed here goes
// through State.set({ font }), which flushes a pending search first, and State's changes
// (Back, Forward, #font= links, tff.list.setState) open or close the panel after Main has
// redrawn the list, so a font the new view shows is in the list when its panel opens. A
// font the view hides stays in the URL and opens when a later view shows it again.
//
// Details wires its own listeners when the script runs (start() is idempotent, and Main
// calls it too). It owns clicks on .details-toggle: its capture listener stops them there,
// so a second handler can't toggle the panel back. open() and close() change nothing when
// the panel is already in the asked state, so callers may repeat them.
const Details = (() => {
  const { el, append, clear, text, formatBytes } = Core;

  // The hash keys of site/CONTRACT.md section 9, in order; any other key is someone else's.
  const HASH_KEYS = ['rank', 'cat', 'var', 'nerd', 'hide', 'q', 'sort', 'font'];
  const ID = /^[a-z0-9-]+$/;
  // "Type your own text" loads only a hashed font file of this site.
  const FONT_URL = /^\/assets\/fonts\/[a-z0-9][a-z0-9._-]*$/;
  const METHODOLOGY = '/methodology/';
  const SURVEY_CAPTIONS = { desktop: 'Desktop sources', project: 'Project sources' };
  const MAX_TYPED = 200;

  // Page wording (owner approval: Milestone 2 step 4; the layout of 2026-09-30).
  const WORDS = Object.freeze({
    title: (family) => `Details for ${family}`,
    loading: 'Loading details…',
    stale: 'The list was updated. Reload to see details.',
    reload: 'Reload page',
    failed: 'Details didn’t load. Check your connection, then try again.',
    retry: 'Try again',
    official: 'Official',
    designer: 'Designer',
    creditNo: 'No credit needed.',
    creditYes: 'Credit required',
    latinBasic: 'Basic Latin only (limited accents)',
    latinExtended: 'Accented letters',
    evidence: 'All ranks and sources',
    gate: 'Held out of the top 100: only one group of sources has evidence for it.',
    sourcesNote: 'Each source links to its credit on the How we rank page.',
    typeownLoad: (size) => `Type your own text (loads ${size})`,
    typeownLoading: 'Loading font…',
    typeownLabel: 'Your text',
    typeownFailed: 'The font file didn’t load. Try again.',
    reportIssue: 'Report a problem with this font on GitHub',
    reportByEmail: 'Report a problem with this font: email ',
  });

  let openId = null; // the font whose panel is open
  let openRow = null; // its li.font, kept even while Render has detached it
  let started = false;
  let following = false; // true once State owns the font key (after 'tff:list-ready')
  let poppedUrl = null; // the URL a popstate just applied, so its hashchange is skipped
  const loading = new WeakMap(); // panel -> promise of its first fill

  // ---- the hash

  // The font named by `hash`: the last font pair wins, and a bad value means none.
  const hashFont = (hash = location.hash) => {
    let found = null;
    for (const pair of hash.replace(/^#/, '').split('&')) {
      const eq = pair.indexOf('=');
      if (eq < 0 || pair.slice(0, eq) !== 'font') continue;
      let value = null;
      try {
        value = decodeURIComponent(pair.slice(eq + 1));
      } catch {
        value = null;
      }
      found = value && ID.test(value) ? value : null;
    }
    return found;
  };

  // `hash` with its font pair set to `id` (or removed), every other pair kept as written:
  // the section 9 keys in order, then extension pairs in their original order.
  const hashWith = (hash, id) => {
    const own = new Map();
    const ext = [];
    for (const pair of hash.replace(/^#/, '').split('&')) {
      if (!pair) continue;
      const eq = pair.indexOf('=');
      const key = eq < 0 ? pair : pair.slice(0, eq);
      if (HASH_KEYS.includes(key)) own.set(key, pair);
      else ext.push(pair);
    }
    own.delete('font');
    if (id) own.set('font', `font=${encodeURIComponent(id)}`);
    const pairs = HASH_KEYS.filter((key) => own.has(key)).map((key) => own.get(key));
    return pairs.length || ext.length ? `#${pairs.concat(ext).join('&')}` : '';
  };

  // Record the open font (or none) in the URL. Once State owns the key, through State.set:
  // `push` adds a history entry, otherwise the current one is updated, so the URL never
  // names a panel that isn't open. Before that, only a push writes anything, by rewriting
  // the hash's font pair alone; State adopts it when it loads (15-state.js, adoptFont).
  // Either way there is one history entry per open or close.
  const recordFont = (id, push) => {
    if (following) {
      State.set({ font: id || '' }, { push });
      return;
    }
    if (!push || hashFont() === (id || null)) return;
    const next = hashWith(location.hash, id);
    history.pushState(history.state, '', `${location.pathname}${location.search}${next}`);
  };

  // ---- rows and panels

  const rowOf = (id) => {
    if (typeof id !== 'string' || !ID.test(id)) return null;
    const row = document.getElementById(`font-${id}`);
    return row && row.matches('li.font') && row.dataset.id === id ? row : null;
  };

  const partsOf = (row) => ({
    id: row.dataset.id,
    family: (row.querySelector('.font-name')?.textContent || row.dataset.id).trim(),
    toggle: row.querySelector('.details-toggle'),
    panel: row.querySelector(':scope > .details'),
  });

  // The frozen skeleton (CONTRACT section 4): the h4 first, then the close button, then the
  // body that fill() replaces. Built on first open, so focus can land before data arrives.
  const skeleton = (row) => {
    const { id, family, panel } = partsOf(row);
    if (panel.querySelector(':scope > .details-title')) return panel;
    const headingId = `details-${id}-h`;
    panel.setAttribute('role', 'region');
    panel.setAttribute('aria-labelledby', headingId);
    append(
      clear(panel),
      el('h4', {
        class: 'details-title',
        id: headingId,
        tabindex: '-1',
        text: WORDS.title(family),
      }),
      el(
        'button',
        { type: 'button', class: 'details-close' },
        'Close',
        el('span', { class: 'visually-hidden', text: ` details for ${family}` }),
      ),
      el('div', { class: 'details-body' }),
    );
    return panel;
  };

  const bodyOf = (panel) => panel.querySelector(':scope > .details-body');

  const setExpanded = (row, expanded) => {
    const { toggle, panel } = partsOf(row);
    if (toggle) toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    if (panel) Core.setHidden(panel, !expanded);
  };

  const focusHeading = (row, { scroll = true } = {}) => {
    const heading = row.querySelector(':scope > .details > .details-title');
    if (!heading) return;
    if (scroll) row.scrollIntoView({ block: 'start' });
    heading.focus({ preventScroll: scroll });
  };

  // Open the panel of font `id`. `focus` moves focus to its heading and scrolls the row to
  // the top (as for a #font= link); `push` records it in a new history entry (see
  // recordFont). Resolves to true once the panel shows the font's details, false if the row
  // isn't in the list or the data failed.
  const open = (id, { focus = false, push = false } = {}) => {
    const row = rowOf(id);
    if (!row || !partsOf(row).panel) return Promise.resolve(false);
    if (openId !== id || openRow !== row) {
      if (openRow) hide(openRow, { focus: false });
      openId = id;
      openRow = row;
      skeleton(row);
      setExpanded(row, true);
    }
    recordFont(id, push);
    if (focus) focusHeading(row);
    return fill(row).then((ok) => (ok ? showRank(row).then(() => ok) : ok));
  };

  const hide = (row, { focus }) => {
    const { toggle, panel } = partsOf(row);
    const inside = panel && panel.contains(document.activeElement);
    setExpanded(row, false);
    if (toggle && (focus === true || (focus === 'auto' && inside))) toggle.focus();
  };

  // Close the open panel. `focus: true` returns focus to its row's details button; the
  // default, 'auto', does that only when focus was inside the panel. `push` records the
  // change in a new history entry (see recordFont).
  const close = ({ focus = 'auto', push = false } = {}) => {
    const row = openRow;
    openId = null;
    openRow = null;
    if (row) hide(row, { focus });
    recordFont(null, push);
  };

  // Open font `id`'s panel, or close it if it is the open one.
  const toggle = (id, options = {}) => {
    if (openId !== id) return open(id, options);
    close(options);
    return Promise.resolve(false);
  };

  // ---- filling a panel

  const loadPayload = () => {
    if (typeof Data === 'object' && Data && typeof Data.loadDetails === 'function') {
      return Promise.resolve().then(() => Data.loadDetails());
    }
    return Promise.reject(new Error('Data.loadDetails is missing'));
  };

  // Data rejects with Data.Stale when the details file is gone (a 404: a newer deploy).
  const isStale = (error) => {
    const Stale = typeof Data === 'object' && Data ? Data.Stale : undefined;
    if (!error || Stale === undefined) return false;
    if (error === Stale) return true;
    if (typeof Stale === 'function') {
      try {
        if (error instanceof Stale) return true;
      } catch {
        // Stale is not a class
      }
    }
    return error.stale === true || error.name === 'Stale';
  };

  const status = (panel, message, button) => {
    append(clear(bodyOf(panel)), el('p', { class: 'details-status', text: message }), button);
  };

  const fill = (row) => {
    const { id, panel } = partsOf(row);
    const state = panel.dataset.state;
    if (state === 'ready') return Promise.resolve(true);
    if (state === 'stale') return Promise.resolve(false);
    if (loading.has(panel)) return loading.get(panel);
    panel.dataset.state = 'loading';
    status(panel, WORDS.loading);
    const done = loadPayload()
      .then((payload) => {
        const fonts = payload && payload.fonts;
        const font = fonts && Object.hasOwn(fonts, id) ? fonts[id] : null;
        if (!font) {
          // The list and the details come from different builds.
          panel.dataset.state = 'stale';
          staleMessage(panel);
          return false;
        }
        append(clear(bodyOf(panel)), ...sections(payload, font));
        panel.dataset.state = 'ready';
        return true;
      })
      .catch((error) => {
        if (isStale(error)) {
          panel.dataset.state = 'stale';
          staleMessage(panel);
        } else {
          panel.dataset.state = 'error';
          const retry = el('button', { type: 'button', class: 'details-retry', text: WORDS.retry });
          status(panel, WORDS.failed, retry);
        }
        return false;
      })
      .finally(() => loading.delete(panel));
    loading.set(panel, done);
    return done;
  };

  const staleMessage = (panel) => {
    const reload = el('button', { type: 'button', class: 'details-reload', text: WORDS.reload });
    status(panel, WORDS.stale, reload);
  };

  // ---- the panel's content

  // A link only to this site or an https:// page; anything else is shown as plain text.
  const link = (href, label, props = {}) =>
    typeof href === 'string' && /^(https:\/\/|\/(?!\/)|mailto:)/.test(href)
      ? el('a', { href, ...props }, label)
      : el('span', {}, label);

  // Where a URL goes, from the URL alone (CONTRACT section 1, rules 2 to 4), as
  // tff_site.data.url_destination names it.
  const place = (href) => {
    let url;
    try {
      url = new URL(href);
    } catch {
      return href;
    }
    const host = url.hostname.replace(/^www\./, '');
    const segments = url.pathname.split('/').filter(Boolean);
    if (host === 'github.com' && segments.length >= 2) {
      return `GitHub: ${segments[0]}/${segments[1].replace(/\.git$/, '')}`;
    }
    if (host === 'fonts.google.com') return 'Google Fonts';
    return host;
  };

  // Destination names (CONTRACT section 1), as tff_site.data.destination_name gives them.
  const destination = (target) => (target.label ? target.label : place(target.url));

  // A Nerd Font build link's text: the build, then where it goes (CONTRACT section 1), as
  // tff_site.data.nerd_link_text gives it: "SauceCodePro Nerd Font (GitHub: ryanoasis/…)".
  const nerdText = (target) => `${target.label} (${place(target.url)})`;

  // The "NF" marker (owner ruling of 2026-09-29), named as in the rows: payload.nerd.
  const nerdMark = (words) =>
    el('span', { class: 'nf-mark', role: 'img', 'aria-label': words.label, text: words.marker });

  const section = (name, title, ...children) =>
    el(
      'section',
      { class: ['details-sec', `details-${name}`] },
      el('h5', { class: 'details-h', text: title }),
      ...children,
    );

  // A description list; each item is [term, ...description nodes or strings], or false. A
  // term is a string or a node.
  const pairs = (items, cls) =>
    el(
      'dl',
      { class: ['details-dl', cls] },
      items
        .filter(Boolean)
        .map(([term, ...desc]) =>
          el('div', { class: 'details-pair' }, el('dt', {}, term), el('dd', {}, ...desc)),
        ),
    );

  // The panel (owner ruling of 2026-09-30, details_layout): essentials first, then the
  // evidence folded behind a closed "All ranks and sources" disclosure, then the report line.
  const sections = (payload, font) =>
    [
      typeOwnSection(font),
      summarySection(payload, font),
      evidenceSection(payload, font),
      reportSection(payload, font),
    ].filter(Boolean);

  // ---- the essentials

  // One short list: Get it, License, Font, Comes with, Also known as, and the rank the
  // selector shows (kept current by showRank).
  const summarySection = (payload, font) =>
    el(
      'div',
      { class: 'details-summary' },
      pairs(
        [
          ['Get it', ...getIt(payload, font)],
          ['License', ...licenseLine(font.license)],
          ['Font', fontLine(font)],
          comesWith(payload, font),
          font.aliases.length > 0 && ['Also known as', aliasNames(font).join(', ')],
          ['Rank', el('span', { class: 'details-rank-now' })],
        ],
        'details-essentials',
      ),
    );

  // The official, designer and Nerd Font build links, one per line, each naming where it
  // goes (CONTRACT section 1). The Nerd link carries the "NF" marker; its legend is above
  // the list. A link's note (why an archived mirror is the official download) follows it.
  const getIt = (payload, font) => {
    const { primary, designer, nerd } = font.links;
    const words = nerd && payload.nerd;
    const line = (...children) => el('li', {}, ...children);
    return [
      el(
        'ul',
        { class: 'details-links', role: 'list' },
        line(`${WORDS.official}: `, link(primary.url, destination(primary))),
        designer && line(`${WORDS.designer}: `, link(designer.url, destination(designer))),
        words &&
          line(
            nerdMark(words),
            ' ',
            link(nerd.url, nerdText(nerd), { class: 'details-nf-link' }),
          ),
      ),
      primary.note && el('p', { class: 'details-link-note', text: primary.note }),
    ];
  };

  const licenseLine = (lic) => [
    link(lic.text_url, `${lic.name} (${lic.spdx})`, { class: 'details-lic-link' }),
    '. ',
    lic.attribution_required ? `${WORDS.creditYes}: ${lic.attribution}` : WORDS.creditNo,
  ];

  const fontLine = (font) => {
    const { variable, static: fixed } = font.formats;
    const formats = variable && fixed ? 'Variable and static' : variable ? 'Variable' : 'Static';
    const latin = font.latin.coverage === 'basic' ? WORDS.latinBasic : WORDS.latinExtended;
    return `${formats} · ${latin}`;
  };

  const systemName = (payload) => {
    const systems = new Map((payload.systems || []).map((s) => [s.id, s.label]));
    return (id) => systems.get(id) || id;
  };

  const comesWith = (payload, font) => {
    if (!font.preinstalled_on.length) return false;
    const name = systemName(payload);
    return ['Comes with', font.preinstalled_on.map((p) => name(p.system)).join(', ')];
  };

  const aliasNames = (font) =>
    font.aliases.map((a) => (a.relation === 'postscript' ? `${a.name} (PostScript name)` : a.name));

  // The rank the selector shows: State's, else the page's default.
  const shownRank = () => {
    if (typeof State === 'object' && State && typeof State.get === 'function') {
      return State.get().rank;
    }
    return 'overall';
  };

  // "Overall: #39, likely #29 to #53", plus the gate line when the rule holds the font back.
  const rankNow = (payload, font, key) => {
    const views = (payload.views || []).filter((v) => v.available && font.ranks[v.key]);
    const view = views.find((v) => v.key === key) || views[0];
    if (!view) return [];
    const entry = font.ranks[view.key];
    return [
      `${view.label}: ${placeText(payload, entry, { tier: false })}`,
      entry.gate_held && el('span', { class: 'details-gate', text: WORDS.gate }),
    ];
  };

  // Write the shown rank into a filled panel; the rank selector may have changed since.
  const showRank = (row) => {
    const slot = partsOf(row).panel.querySelector('.details-rank-now');
    if (!slot) return Promise.resolve();
    return loadPayload().then(
      (payload) => {
        const font = payload.fonts[row.dataset.id];
        if (font) append(clear(slot), ...rankNow(payload, font, shownRank()).filter(Boolean));
      },
      () => {},
    );
  };

  // ---- the evidence, folded

  const range = ([low, high]) => (low === high ? `likely #${low}` : `likely #${low} to #${high}`);

  // "#1, tier A, likely #1 to #6"; "Band 101–250, tier C, …"; "Not ranked: <why>".
  const placeText = (payload, entry, { tier = true } = {}) => {
    if (entry.unranked) {
      return `Not ranked: ${(payload.why_labels || {})[entry.unranked] || entry.unranked}`;
    }
    const bits = [entry.rank ? `#${entry.rank}` : `Band ${entry.band}`];
    if (tier && entry.tier) bits.push(`tier ${entry.tier}`);
    if (entry.range) bits.push(range(entry.range));
    return bits.join(', ');
  };

  const evidenceSection = (payload, font) =>
    el(
      'details',
      { class: 'details-evidence' },
      el('summary', { class: 'details-evidence-toggle', text: WORDS.evidence }),
      ranksSection(payload, font),
      pulledIn(payload, font),
      sourcesSection(payload, font),
    );

  const ranksSection = (payload, font) => {
    const tiers = new Set();
    const items = (payload.views || [])
      .filter((view) => view.available && font.ranks[view.key])
      .map((view) => {
        const entry = font.ranks[view.key];
        if (entry.tier) tiers.add(entry.tier);
        return [
          view.label,
          el('span', { class: 'details-place', text: placeText(payload, entry) }),
          entry.gate_held && el('span', { class: 'details-gate', text: WORDS.gate }),
        ];
      });
    const legend = ['A', 'B', 'C']
      .filter((tier) => tiers.has(tier) && payload.tiers && payload.tiers[tier])
      .map((tier) => [`Tier ${tier}`, payload.tiers[tier]]);
    return section(
      'ranks',
      'Ranks',
      pairs(items, 'details-rank-list'),
      legend.length > 0 && pairs(legend, 'details-tiers'),
      el('p', { class: 'details-more' }, link(METHODOLOGY, 'How ranks, bands and tiers work')),
    );
  };

  // The Linux packages that install the font on their own (D8), which is why a Linux source
  // may be left out of some ranks.
  const pulledIn = (payload, font) => {
    if (!font.pulled_in_by.length) return false;
    const name = systemName(payload);
    const text = font.pulled_in_by.map((p) => `${p.package} on ${name(p.system)}`).join('; ');
    return pairs([['Pulled in by', text]], 'details-pulled');
  };

  const viewLabels = (payload, keys) =>
    keys.map((key) => ((payload.views || []).find((v) => v.key === key) || { label: key }).label);

  // "#2"; "below the floor"; "observed; rank not published"; plus abstentions and staleness.
  const sourceText = (payload, source, entry) => {
    if (!entry) return 'no data';
    const label = (payload.state_labels || {})[entry.state] || entry.state;
    let out = label;
    if (entry.state === 'observed') {
      // The terms ruling: a source with publish_rank false never shows a rank, even if the
      // data (wrongly) carries one.
      if (!source.publish_rank) out = `${label}; rank not published`;
      else if (entry.rank_in_source) out = `#${entry.rank_in_source}`;
    }
    if (entry.abstains_in && entry.abstains_in.length) {
      out += `; left out of ${viewLabels(payload, entry.abstains_in).join(' and ')}`;
    }
    if (source.stale) out += ` (stale: data from ${source.data_date})`;
    return out;
  };

  const sourcesSection = (payload, font) => {
    const groups = new Map();
    for (const source of payload.sources || []) {
      if (!groups.has(source.survey)) groups.set(source.survey, []);
      groups.get(source.survey).push(
        el(
          'tr',
          { dataset: { source: source.id } },
          el('th', { scope: 'row' }, link(`${METHODOLOGY}#source-${source.id}`, source.name)),
          el('td', { text: sourceText(payload, source, font.sources[source.id]) }),
        ),
      );
    }
    const tables = [...groups].map(([survey, rows]) =>
      el(
        'table',
        { class: 'details-src-table', dataset: { survey } },
        el('caption', { text: SURVEY_CAPTIONS[survey] || survey }),
        el(
          'thead',
          {},
          el(
            'tr',
            {},
            el('th', { scope: 'col', text: 'Source' }),
            el('th', { scope: 'col', text: 'This font' }),
          ),
        ),
        el('tbody', {}, rows),
      ),
    );
    const note = el('p', { class: 'details-more', text: WORDS.sourcesNote });
    return section('sources', 'Sources', ...tables, note);
  };

  // ---- "Type your own text", first

  const typeOwnSection = (font) => {
    const own = font.type_own;
    if (!own || !FONT_URL.test(own.url)) return null;
    if (typeof FontFace !== 'function' || !document.fonts) return null;
    const button = el('button', {
      type: 'button',
      class: 'typeown-load',
      dataset: { url: own.url },
      text: WORDS.typeownLoad(formatBytes(own.size)),
    });
    return el('div', { class: ['typeown', 'details-typeown'] }, button);
  };

  // Load the unchanged font file (only now, on request) and swap the button for an input.
  const loadFont = (button) => {
    const row = button.closest('li.font');
    if (!row || button.getAttribute('aria-disabled') === 'true') return Promise.resolve(false);
    const { id, family } = partsOf(row);
    const url = button.dataset.url;
    if (!FONT_URL.test(url || '')) return Promise.resolve(false);
    const box = button.closest('.typeown');
    const label = button.textContent;
    const old = box.querySelector('.typeown-error');
    if (old) old.remove();
    // aria-disabled, not disabled: a disabled button would drop keyboard focus.
    button.setAttribute('aria-disabled', 'true');
    text(button, WORDS.typeownLoading);
    const face = new FontFace(`tff-${id}`, `url("${url}")`);
    document.fonts.add(face);
    return face.load().then(
      () => {
        const inputId = `typeown-${id}`;
        const input = el('input', {
          class: 'typeown-input',
          id: inputId,
          type: 'text',
          maxlength: MAX_TYPED,
          autocomplete: 'off',
          spellcheck: 'false',
        });
        input.value = family;
        input.style.fontFamily = `"tff-${id}", system-ui, sans-serif`;
        // Focus the box unless the visitor has moved on (Safari doesn't focus a clicked button).
        const active = document.activeElement;
        const refocus = !active || active === button || active === document.body;
        const labelEl = el('label', { class: 'typeown-label', for: inputId });
        labelEl.textContent = WORDS.typeownLabel;
        button.replaceWith(labelEl, input);
        if (refocus) input.focus();
        return true;
      },
      () => {
        document.fonts.delete(face);
        button.removeAttribute('aria-disabled');
        text(button, label);
        box.append(el('p', { class: 'typeown-error', text: WORDS.typeownFailed }));
        return false;
      },
    );
  };

  const reportSection = (payload, font) => {
    const report = payload.report || {};
    let issue = null;
    try {
      const url = new URL(report.issue_url);
      url.searchParams.set('font_id', font.id);
      url.searchParams.set('data_date', payload.run_date);
      issue = url.href;
    } catch {
      issue = null;
    }
    const subject = `Problem with ${font.family} (${font.id})`;
    const body = `Font: ${font.id}\nData date: ${payload.run_date}\n\nWhat is wrong:\n`;
    const query = `subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    const mailto = report.email ? `mailto:${report.email}?${query}` : null;
    if (!issue && !mailto) return null;
    // Both links name their destination (Milestone 2 step 6): GitHub, and the address.
    return el(
      'p',
      { class: 'details-report' },
      issue
        ? link(issue, WORDS.reportIssue, { class: 'details-report-issue' })
        : WORDS.reportByEmail,
      issue && mailto && ', or email ',
      mailto && link(mailto, report.email, { class: 'details-report-email' }),
      '.',
    );
  };

  // ---- events

  const onClick = (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const row = target && target.closest('li.font');
    if (!row) return;
    const hit = target.closest(
      '.details-toggle, .details-close, .typeown-load, .details-retry, .details-reload',
    );
    if (!hit || !row.contains(hit)) return;
    if (hit.matches('.details-toggle')) {
      // Details owns this button (see the top of this file).
      event.stopPropagation();
      const id = row.dataset.id;
      if (openId === id && openRow === row) close({ focus: false, push: true });
      else open(id, { push: true });
    } else if (hit.matches('.details-close')) {
      close({ focus: true, push: true });
    } else if (hit.matches('.typeown-load')) {
      loadFont(hit);
    } else if (hit.matches('.details-retry')) {
      const panel = partsOf(row).panel;
      delete panel.dataset.state;
      // The button is replaced either way, so put focus on what replaced it.
      fill(row).then((ok) => {
        if (ok) showRank(row);
        if (panel.contains(document.activeElement)) return;
        const next = panel.querySelector(ok ? ':scope > .details-title' : '.details-retry');
        if (next) next.focus();
      });
    } else if (hit.matches('.details-reload')) {
      location.reload();
    }
  };

  const onKeydown = (event) => {
    if (event.key !== 'Escape' || event.isComposing || event.defaultPrevented || !openRow) return;
    if (!(event.target instanceof Node) || !openRow.contains(event.target)) return;
    event.preventDefault();
    close({ focus: true, push: true });
  };

  // Apply the hash's font (a load, Back or Forward, or a #font= link).
  const sync = ({ focus }) => {
    const id = hashFont();
    if (id === null) {
      if (openRow) close({ focus: 'auto' });
      return;
    }
    if (id !== openId || !openRow || !openRow.isConnected) open(id, { focus });
  };

  // Until State owns the font key: Back, Forward and #font= links fire popstate and then
  // hashchange; act on the first.
  const onPopstate = () => {
    if (following) return;
    poppedUrl = location.href;
    sync({ focus: true });
  };

  const onHashchange = (event) => {
    if (following) return;
    const popped = poppedUrl;
    poppedUrl = null;
    if (popped !== null && event.newURL === popped) return;
    sync({ focus: true });
  };

  // Once State owns the font key: every change of state, after Main has redrawn the list. A
  // change from the history (Back, Forward, a #font= link) focuses the heading, as a load
  // does; one made through State.set (a click here, or tff.list.setState) leaves focus alone.
  const onState = (state, previous, info) => {
    const id = state.font || null;
    if (id === null) {
      if (openRow) close({ focus: 'auto' });
      return;
    }
    if (id !== openId || !openRow || !openRow.isConnected) {
      open(id, { focus: Boolean(info && info.source === 'history') });
    } else if (previous && state.rank !== previous.rank) {
      showRank(openRow);
    }
  };

  const canFollow = () =>
    typeof State === 'object' &&
    State !== null &&
    typeof State.subscribe === 'function' &&
    typeof State.set === 'function';

  // A load with #font= opens that panel once the list has applied the hash (Main fires
  // 'tff:list-ready' once, after the first render and after it subscribed to State), so the
  // row is where it will stay, or is left closed when the filters in the hash hide it. If
  // that event is late, the panel opens anyway after LIST_READY_WAIT_MS, and is brought back
  // into view when the event comes, unless the visitor has moved on. From then on Details
  // follows State; subscribing now puts it after Main, so the list is redrawn first.
  const LIST_READY_WAIT_MS = 4000;
  let initialTimer = 0;

  const onListReady = () => {
    clearTimeout(initialTimer);
    if (openRow && openRow.isConnected) {
      const heading = openRow.querySelector(':scope > .details > .details-title');
      if (heading && document.activeElement === heading) focusHeading(openRow);
    } else if (openId === null) {
      sync({ focus: true });
    }
    if (!following && canFollow()) {
      following = true;
      State.subscribe(onState);
    }
    // State has read the hash's rank by now; a panel filled before that shows it.
    if (openRow && openRow.isConnected) showRank(openRow);
  };

  const start = () => {
    if (started) return;
    started = true;
    document.addEventListener('click', onClick, true);
    document.addEventListener('keydown', onKeydown);
    window.addEventListener('popstate', onPopstate);
    window.addEventListener('hashchange', onHashchange);
    document.addEventListener('tff:list-ready', onListReady, { once: true });
    if (document.getElementById('list') && hashFont() !== null) {
      loadPayload().catch(() => {}); // fetch now; fill() reports any failure
      initialTimer = setTimeout(() => {
        if (openId === null) sync({ focus: true });
      }, LIST_READY_WAIT_MS);
    }
  };

  start();

  return Object.freeze({
    open,
    close,
    toggle,
    start,
    get openId() {
      return openId;
    },
    // For tests and Milestone 3: pure helpers.
    hashFont,
    hashWith,
    destination,
    nerdText,
    WORDS,
  });
})();
