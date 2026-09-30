// 15-state: the view the visitor asked for, kept in the URL after "#" (site/CONTRACT.md
// section 9). The fragment never reaches the server and nothing is stored anywhere.
//
// The state is { rank, cat, var, nerd, hide[], q, sort, font }. The hash holds the keys in
// that order, with defaults left out, then every extension pair (a key this page doesn't own,
// such as Milestone 3's os=linux) exactly as written, in its original order. Discrete changes
// push a history entry; search typing replaces the current one after 300 ms of quiet.
// popstate and hashchange apply the hash without pushing.
//
// Retired keys (owner rulings of 2026-09-30): spacing, lic and redist are read once and
// dropped from the URL, so old links still open. spacing=monospaced becomes cat=monospace
// when the link names no category; the rest match every listed font now. hide holds at most
// one operating system, since the page offers them as one select.
//
// parse(), serialize(), coerce() and same() are pure, for tests/site/test_list.py.
const State = (() => {
  const KEYS = Object.freeze(['rank', 'cat', 'var', 'nerd', 'hide', 'q', 'sort', 'font']);
  const OWN = new Set(KEYS);
  const RETIRED = Object.freeze(['spacing', 'lic', 'redist']);
  const LISTS = new Set(['hide']);
  const DEFAULT_RANK = 'overall';
  const HIDES = Object.freeze(['limited', 'attr', 'windows', 'macos', 'linux', 'android']);
  const OSES = Object.freeze(['windows', 'macos', 'linux', 'android']);
  // The sort orders (owner ruling of 2026-09-30, sort_header): by rank, best first (the
  // default) or reversed, and by name, A to Z or reversed.
  const SORTS = Object.freeze(['rank', 'rank-desc', 'name', 'name-desc']);
  const MAX_Q = 100;
  const TYPING_MS = 300;
  // Font ids, as the schema allows them.
  const ID = /^[a-z0-9-]+$/;

  const DEFAULTS = Object.freeze({
    rank: DEFAULT_RANK,
    cat: '',
    var: false,
    nerd: false,
    hide: Object.freeze([]),
    q: '',
    sort: 'rank',
    font: '',
  });

  // What the hash may name. configure() fills it from the list index; until then only the
  // defaults and the fixed vocabularies of section 9 are known.
  const BASE_VOCAB = Object.freeze({
    ranks: Object.freeze([DEFAULT_RANK]),
    cats: Object.freeze(['sans-serif', 'serif', 'display', 'handwriting', 'monospace']),
    ids: null,
  });
  let vocab = BASE_VOCAB;

  let current = DEFAULTS;
  // The extension pairs as last read or written, for when the hash is an in-page anchor.
  let lastExt = [];
  let listening = false;
  const listeners = new Set();

  // --- pure helpers ---------------------------------------------------------------------

  // At most MAX_Q UTF-16 units, as the search box's maxlength, without splitting a pair.
  const clip = (text) => {
    if (text.length <= MAX_Q) return text;
    const cut = text.slice(0, MAX_Q);
    return /[\uD800-\uDBFF]$/.test(cut) ? cut.slice(0, -1) : cut;
  };

  // Keep the allowed items of a list, once each, in the vocabulary's order.
  const pickList = (items, allowed) => {
    const wanted = new Set(items.map(String));
    return Object.freeze(allowed.filter((item) => wanted.has(item)));
  };

  const asList = (value) => {
    if (Array.isArray(value)) return value;
    if (value === null || value === undefined || value === '') return [];
    return String(value).split(',');
  };

  const isOn = (value) => value === true || value === 1 || value === '1';

  // The hide list, with at most one operating system: the first, in HIDES order.
  const pickHide = (items) => {
    const picked = pickList(items, HIDES);
    const os = picked.find((item) => OSES.includes(item));
    return Object.freeze(picked.filter((item) => !OSES.includes(item) || item === os));
  };

  // One key's value, validated: anything invalid falls back to the default.
  const coerceKey = (key, value, v = vocab) => {
    switch (key) {
      case 'rank': {
        const rank = String(value ?? '');
        if (v.ranks.includes(rank)) return rank;
        return v.ranks.includes(DEFAULT_RANK) ? DEFAULT_RANK : v.ranks[0];
      }
      case 'cat': {
        const cat = String(value ?? '');
        return v.cats.includes(cat) ? cat : '';
      }
      case 'var':
      case 'nerd':
        return isOn(value);
      case 'hide':
        return pickHide(asList(value));
      case 'q':
        return typeof value === 'string' ? clip(value) : '';
      case 'sort':
        return SORTS.includes(value) ? value : 'rank';
      case 'font': {
        const id = String(value ?? '');
        if (!ID.test(id)) return '';
        return v.ids === null || v.ids.has(id) ? id : '';
      }
      default:
        return undefined;
    }
  };

  // A full state from `base` and a partial of own keys; other keys are ignored.
  const coerce = (partial, base = DEFAULTS, v = vocab) => {
    const next = { ...base };
    for (const key of KEYS) {
      if (partial && Object.hasOwn(partial, key)) next[key] = coerceKey(key, partial[key], v);
    }
    next.rank = coerceKey('rank', next.rank, v);
    return Object.freeze(next);
  };

  const same = (a, b) =>
    KEYS.every((key) =>
      LISTS.has(key) ? a[key].join(',') === b[key].join(',') : a[key] === b[key],
    );

  const decode = (raw, key) => {
    // A hand-typed "+" in the search means a space; the page itself writes %20.
    const text = key === 'q' ? raw.replaceAll('+', ' ') : raw;
    try {
      return decodeURIComponent(text);
    } catch {
      return null;
    }
  };

  // Parse a hash ("#a=1&b=2", "a=1" or "") into { state, ext }. `ext` holds the pairs whose
  // key this page doesn't own, exactly as written; a retired key is neither (see the top).
  // Own keys: the last one wins, and an undecodable or invalid value means the default.
  const parse = (hash, v = vocab) => {
    const body = String(hash ?? '').replace(/^#/, '');
    const raw = {};
    const ext = [];
    let spacing = null;
    for (const piece of body.split('&')) {
      if (piece === '') continue;
      const eq = piece.indexOf('=');
      const key = eq < 0 ? piece : piece.slice(0, eq);
      if (OWN.has(key)) raw[key] = eq < 0 ? '' : piece.slice(eq + 1);
      else if (key === 'spacing') spacing = eq < 0 ? '' : decode(piece.slice(eq + 1), key);
      else if (!RETIRED.includes(key)) ext.push(piece);
    }
    const partial = {};
    for (const [key, value] of Object.entries(raw)) {
      const text = decode(value, key);
      if (text === null) continue;
      partial[key] = LISTS.has(key) ? text.split(',') : text;
    }
    if (spacing === 'monospaced' && !partial.cat) partial.cat = 'monospace';
    return { state: coerce(partial, DEFAULTS, v), ext };
  };

  const encode = (value) => encodeURIComponent(value);

  // The canonical hash of a state plus extension pairs: "" for the default view without any.
  const serialize = (state, ext = []) => {
    const pairs = [];
    if (state.rank !== DEFAULT_RANK) pairs.push(`rank=${encode(state.rank)}`);
    if (state.cat) pairs.push(`cat=${encode(state.cat)}`);
    if (state.var) pairs.push('var=1');
    if (state.nerd) pairs.push('nerd=1');
    if (state.hide.length) pairs.push(`hide=${state.hide.map(encode).join(',')}`);
    if (state.q) pairs.push(`q=${encode(state.q)}`);
    if (state.sort !== 'rank') pairs.push(`sort=${encode(state.sort)}`);
    if (state.font) pairs.push(`font=${encode(state.font)}`);
    const all = pairs.concat(ext);
    return all.length ? `#${all.join('&')}` : '';
  };

  // Every filter off; the rank, sort order and open details stay.
  const cleared = (state) => coerce({ cat: '', var: false, nerd: false, hide: [], q: '' }, state);

  // True when no filter (search included) is on.
  const isClear = (state) => same(cleared(state), state);

  // --- the page's state -----------------------------------------------------------------

  // A plain in-page link ("#main" from the skip link, "#font-inter"): one token, no "=",
  // naming an element. It is not a view, so it must not reset one.
  const isAnchor = (hash) => {
    const body = hash.replace(/^#/, '');
    if (!body || body.includes('=') || body.includes('&')) return false;
    try {
      return document.getElementById(decodeURIComponent(body)) !== null;
    } catch {
      return false;
    }
  };

  // The extension pairs in the URL right now, so a change Milestone 3 made meanwhile stays.
  const extNow = () => {
    const hash = location.hash;
    if (!isAnchor(hash)) lastExt = parse(hash).ext;
    return lastExt;
  };

  // The empty hash is written as the page's own URL; a query string, never read here, stays.
  const urlOf = (hash) => hash || `${location.pathname}${location.search}`;

  const write = (hash, push) => {
    if (push) history.pushState(null, '', urlOf(hash));
    else history.replaceState(null, '', urlOf(hash));
  };

  // True when the address bar still ends in a bare "#" (location.hash is then "").
  const bareHash = () => location.hash === '' && location.href.endsWith('#');

  const writeNow = (push) => {
    const hash = serialize(current, extNow());
    if (push || hash !== location.hash || bareHash()) write(hash, push);
  };

  let typingPending = false;
  const replaceLater = Core.debounce(() => {
    typingPending = false;
    adoptFont();
    writeNow(false);
  }, TYPING_MS);

  // Write a pending search to the current entry before anything else changes the history,
  // so Back returns to it.
  const flushTyping = () => {
    if (!typingPending) return;
    replaceLater.cancel();
    typingPending = false;
    adoptFont();
    writeNow(false);
  };

  const notify = (prev, info) => {
    for (const fn of [...listeners]) fn(current, prev, info);
  };

  // Learn what the hash may name from the list index (section 7): the available views, the
  // categories and the font ids. The current state is revalidated.
  const configure = (index) => {
    const ranks = index.views
      .filter((view) => view.available && index.r && index.r[view.key])
      .map((view) => view.key);
    vocab = Object.freeze({
      ranks: Object.freeze(ranks.length ? ranks : [DEFAULT_RANK]),
      cats: Object.freeze([...index.cats]),
      ids: new Set(index.ids),
    });
    current = coerce(current, DEFAULTS);
  };

  const onNavigate = () => {
    const hash = location.hash;
    replaceLater.cancel();
    typingPending = false;
    if (isAnchor(hash)) {
      // The skip link or another in-page link: keep the view, and put it back in the URL
      // so a copied link still reproduces it.
      write(serialize(current, lastExt), false);
      return;
    }
    const { state, ext } = parse(hash);
    lastExt = ext;
    const canonical = serialize(state, ext);
    if (canonical !== hash || bareHash()) write(canonical, false);
    if (same(state, current)) return;
    const prev = current;
    current = state;
    notify(prev, { source: 'history' });
  };

  // Read the hash once (after configure), rewrite it in canonical form if it differs, and
  // follow Back, Forward and edits to the hash from then on. Returns the state. An in-page
  // anchor on load names no view: the browser has already scrolled to it, and the URL
  // becomes the default view's, so Back to this entry means the default view.
  const load = () => {
    const hash = location.hash;
    const { state, ext } = isAnchor(hash) ? { state: coerce({}), ext: [] } : parse(hash);
    current = state;
    lastExt = ext;
    const canonical = serialize(state, ext);
    if (canonical !== hash || bareHash()) write(canonical, false);
    if (!listening) {
      listening = true;
      addEventListener('popstate', onNavigate);
      addEventListener('hashchange', onNavigate);
    }
    return current;
  };

  // Details may write the font pair itself (before the Milestone 3 hook is up), and such a
  // write fires no event. Take the URL's font as the state's, silently, so the next write
  // keeps it. Every other key only changes through this part.
  const adoptFont = () => {
    if (!listening || isAnchor(location.hash)) return;
    const { font } = parse(location.hash).state;
    if (font !== current.font) current = coerce({ font }, current);
  };

  // A copy of the state, safe to change.
  const get = () => {
    adoptFont();
    return { ...current, hide: [...current.hide] };
  };

  // Change some keys, validated like the hash. A discrete change pushes a history entry
  // (push: false replaces the current one) before the listeners run, so they see the URL
  // and the state agree; typing: true replaces the entry after 300 ms of quiet. Listeners
  // run at once either way. Returns true when the state changed.
  const set = (partial, { push = true, typing = false } = {}) => {
    adoptFont();
    const next = coerce(partial, current);
    if (same(next, current)) return false;
    const prev = current;
    if (typing) {
      typingPending = true;
      current = next;
      replaceLater();
      notify(prev, { source: 'typing' });
    } else {
      flushTyping();
      current = next;
      writeNow(push);
      notify(prev, { source: 'set' });
    }
    return true;
  };

  // fn(state, previous, { source: 'set' | 'typing' | 'history' }) after every change.
  // Returns a function that stops it.
  const subscribe = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };

  return Object.freeze({
    KEYS,
    RETIRED,
    DEFAULTS,
    HIDES,
    OSES,
    SORTS,
    MAX_Q,
    parse,
    serialize,
    coerce,
    same,
    cleared,
    isClear,
    configure,
    load,
    get,
    set,
    subscribe,
  });
})();
