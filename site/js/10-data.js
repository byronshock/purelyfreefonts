// 10-data: the two JSON payloads (site/CONTRACT.md sections 5, 7 and 8). Both URLs come from
// the list's data- attributes, so every request stays on this site (connect-src 'self').
// The list index is preloaded by the page's <link rel="preload" as="fetch" crossorigin>; a
// plain fetch() of the same URL (mode cors, credentials same-origin) reuses that response.
// Details load on first use only. Each loader is memoised: every caller shares one promise.
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

  // The payload URL in #list[data-<name>]: a same-origin path, never an absolute URL.
  const urlOf = (name) => {
    const node = listNode();
    const url = node ? node.dataset[name] : '';
    if (!url || !url.startsWith('/') || url.startsWith('//')) {
      throw new Error(`Data: #list has no usable data-${name}`);
    }
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
      index.v === 2 &&
      Number.isInteger(n) &&
      [index.views, index.cats, index.bands, index.why_labels].every(Array.isArray) &&
      [index.ids, index.cat, index.bits, index.keys, index.by_name].every(
        (column) => Array.isArray(column) && sized(column),
      ) &&
      Boolean(index.r) &&
      typeof index.r === 'object' &&
      Object.values(index.r).every(
        (view) =>
          Boolean(view) &&
          Array.isArray(view.order) &&
          [view.top, view.band, view.why, view.tier].every(sized),
      );
    if (!ok) throw new Error('Data: the list index is not in format 2');
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

  return Object.freeze({ loadIndex, loadDetails, Stale, STALE_MESSAGE });
})();
