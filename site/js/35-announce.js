// 35-announce: the page's one live region, #status (role="status", so polite; WCAG 4.1.3).
// A discrete change (a filter, the rank, the sort order, Back) is announced at once, and
// several in one task become one message (the last). Typing in the search is announced 500 ms
// after the last key, so a screen reader isn't talked over. Nothing is announced until the
// first say(): Main stays silent on the first load. Focus is never moved from here.
//
// #status is inside #results, which html[data-list-pending] takes out of sight, and so out of
// the accessibility tree, while a link's view waits for its first render (90-main.js): a
// message written then would never be heard. So it is kept, the last one only, and written
// once Main has shown the results (flush).
const Announce = (() => {
  const TYPING_MS = 500;
  const NBSP = '\u00A0';
  let queued = null;
  let scheduled = false;
  let kept = null; // the last message said while #status couldn't be heard

  const region = () => document.getElementById('status');

  // Screen readers skip a live region whose text didn't change, so a message equal to the
  // last one gets a no-break space added to count as new (and loses it the next time).
  const write = (message) => {
    const node = region();
    if (!node) return;
    if (document.documentElement.hasAttribute('data-list-pending')) {
      kept = message;
      return;
    }
    kept = null;
    node.textContent = message === node.textContent ? `${message}${NBSP}` : message;
  };

  const later = Core.debounce((message) => write(message), TYPING_MS);

  // Announce `message` now (coalesced per microtask). A pending search message is dropped.
  const say = (message) => {
    later.cancel();
    queued = String(message);
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(() => {
      scheduled = false;
      if (queued === null) return;
      const text = queued;
      queued = null;
      write(text);
    });
  };

  // Announce `message` once typing has paused for 500 ms.
  const typing = (message) => {
    queued = null;
    later(String(message));
  };

  // Drop anything pending.
  const cancel = () => {
    later.cancel();
    queued = null;
    kept = null;
  };

  // Main has shown the results: write the message kept meanwhile, if any.
  const flush = () => {
    const message = kept;
    kept = null;
    if (message !== null) write(message);
  };

  return Object.freeze({ say, typing, cancel, flush });
})();
