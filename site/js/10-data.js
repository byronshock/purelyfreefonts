// 10-data: the JSON payloads (site/CONTRACT.md sections 5, 7, 8 and 11). Every URL comes from
// the list's data- attributes and must be a path on this page's own origin, so every request
// stays on this site (connect-src 'self' enforces the same). The list index is preloaded by
// the page's <link rel="preload" as="fetch" crossorigin>; a plain fetch() of the same URL
// (mode cors, credentials same-origin) reuses that response. Details load on first use only,
// and the hopper's match file only once a visitor's list arrives (M3-D7 a). Each loader is
// memoised: every caller shares one promise.
const Data = (() => {
  const STALE_MESSAGE = 'The list was updated. Reload to see details.';

  // A payload named by this page no longer exists on the server: the page is more than one
  // deploy old (the receiver keeps one generation of assets for open tabs).
  class Stale extends Error {
    constructor(url) {
      super(STALE_MESSAGE);
      this.name = 'Stale';
      this.url = url;
    }
  }

  const listNode = () => document.getElementById('list');

  // Whether url is a path that resolves on this page's own origin. A browser reads "\" as
  // "/" in a URL, so "/\host/x" would reach another host although it starts with one "/".
  const ownPath = (url) => {
    if (!url || !url.startsWith('/') || url.startsWith('//')) return false;
    try {
      return new URL(url, location.href).origin === location.origin;
    } catch (error) {
      return false;
    }
  };

  // The payload URL in #list[data-<name>]: a path on this origin, never an absolute URL.
  const urlOf = (name) => {
    const node = listNode();
    const url = node ? node.dataset[name] : '';
    if (!ownPath(url)) throw new Error(`Data: #list has no usable data-${name}`);
    return url;
  };

  const getJSON = async (url) => {
    const response = await fetch(url);
    if (response.status === 404 || response.status === 410) throw new Stale(url);
    if (!response.ok) throw new Error(`Data: ${url} answered ${response.status}`);
    return response.json();
  };

  // Freeze a parsed payload all the way down, so no part (or Milestone 3) can change it.
  const deepFreeze = (value) => {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {
      Object.freeze(value);
      for (const key of Object.keys(value)) deepFreeze(value[key]);
    }
    return value;
  };

  // The checks every later part relies on: the format version, the lists State and View
  // read, and one entry per font in each per-font column.
  const checkIndex = (index) => {
    const n = index && index.n;
    const sized = (column) => column != null && column.length === n;
    const ok =
      Boolean(index) &&
      index.v === 4 &&
      Number.isInteger(n) &&
      [index.views, index.cats, index.bands, index.why_labels].every(Array.isArray) &&
      Boolean(index.score_words) &&
      [index.ids, index.cat, index.bits, index.keys, index.by_name].every(
        (column) => Array.isArray(column) && sized(column),
      ) &&
      Boolean(index.r) &&
      typeof index.r === 'object' &&
      Object.values(index.r).every(
        (view) =>
          Boolean(view) &&
          Array.isArray(view.order) &&
          [view.top, view.band, view.why, view.u, view.s, view.held].every(sized),
      );
    if (!ok) throw new Error('Data: the list index is not in format 4');
    return deepFreeze(index);
  };

  // Memoise a loader. A failed load is forgotten, so a later call can retry after a network
  // error; a Stale page stays stale until it is reloaded, so that failure is kept.
  const memo = (load) => {
    let promise = null;
    return () => {
      if (!promise) {
        promise = load().catch((error) => {
          if (!(error instanceof Stale)) promise = null;
          throw error;
        });
      }
      return promise;
    };
  };

  // Promise of the list index (section 7), frozen.
  const loadIndex = memo(async () => checkIndex(await getJSON(urlOf('index'))));

  // Promise of the details payload (section 8), frozen. Rejects with Data.Stale on a 404.
  const loadDetails = memo(async () => deepFreeze(await getJSON(urlOf('details'))));

  // A match file this page can't use. code: 'url' (#list names none, one with a query, or
  // one off this origin), 'index' (the list index failed, so the run can't be checked),
  // 'network', 'status', 'json', 'format' (not tff-match version 1) or 'run_date' (another
  // run than the list's).
  class MatchError extends Error {
    constructor(code, message) {
      super(`Data: ${message}`);
      this.name = 'MatchError';
      this.code = code;
    }
  }

  // Promise of the hopper's match file (section 11), frozen: one GET of the path in
  // #list[data-match], with no query string and no body, so nothing from a list goes in it.
  // Rejects with Data.Stale when it or the list index answers 404 or 410, and otherwise only
  // with a Data.MatchError, so the hopper has exactly two failures to tell apart.
  const loadMatch = memo(async () => {
    let url;
    try {
      url = urlOf('match');
    } catch (error) {
      throw new MatchError('url', error.message);
    }
    if (url.includes('?') || url.includes('#')) throw new MatchError('url', `${url} has a query`);
    let index;
    try {
      index = await loadIndex();
    } catch (error) {
      if (error instanceof Stale) throw error;
      throw new MatchError('index', `the list index failed (${error.message})`);
    }
    let response;
    try {
      response = await fetch(url);
    } catch (error) {
      throw new MatchError('network', `${url} could not be fetched`);
    }
    if (response.status === 404 || response.status === 410) throw new Stale(url);
    if (!response.ok) throw new MatchError('status', `${url} answered ${response.status}`);
    let doc;
    try {
      doc = await response.json();
    } catch (error) {
      throw new MatchError('json', `${url} is not JSON`);
    }
    if (!doc || doc.format !== 'tff-match' || doc.v !== 1) {
      throw new MatchError('format', `${url} is not a tff-match file of version 1`);
    }
    if (doc.run_date !== index.run_date) {
      throw new MatchError('run_date', `${url} is of run ${doc.run_date}, not ${index.run_date}`);
    }
    return deepFreeze(doc);
  });

  return Object.freeze({ loadIndex, loadDetails, loadMatch, Stale, MatchError, STALE_MESSAGE });
})();
