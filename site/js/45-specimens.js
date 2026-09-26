// 45-specimens: font previews (Milestone 2 step 5; site/CONTRACT.md sections 4, 5 and 10).
// A row with a preview holds an empty span.spec[data-src]. When the row comes within 600px of
// the screen, the span gets the SVG as its CSS mask, set through CSSOM (the CSP forbids style
// attributes, not CSSOM), and 35-specimens.css fills the mask with the text colour, so the
// outlines follow light, dark and forced colours. The observer watches the li, not the span:
// rows use content-visibility: auto, and a span in a skipped row has no box to observe.
// pause() stops new requests for Milestone 3 (M3-D10: a request for a specimen would reveal
// which rows a comparison left on screen); resume() loads the rows that are near by then.
const Specimens = (() => {
  // Top and bottom only: rows span the list's width.
  const MARGIN = '600px 0px';
  // The build writes /assets/specimens/<id>.<h>.svg. Anything else is ignored, so a data-src
  // can never break out of url("...").
  const SAFE_SRC = /^\/assets\/specimens\/[a-z0-9][a-z0-9._-]*\.svg$/;

  const spanOf = (row) => row.querySelector('span.spec[data-src]');

  // Taken when the script runs, before Main.start() lets Render detach any row: a detached
  // row stays observed and loads once it is back near the screen.
  const rows = Core.$$('li.font').filter((row) => spanOf(row) !== null);

  let observer = null;
  let paused = false;
  // Rows that came near the screen while paused and haven't been seen leaving.
  const near = new Set();

  const load = (row) => {
    near.delete(row);
    if (observer) observer.unobserve(row);
    const span = spanOf(row);
    if (!span || span.dataset.state === 'set' || !SAFE_SRC.test(span.dataset.src)) return;
    const value = `url("${span.dataset.src}")`;
    span.style.setProperty('-webkit-mask-image', value);
    span.style.setProperty('mask-image', value);
    span.dataset.state = 'set';
  };

  const seen = (entries) => {
    for (const { target, isIntersecting } of entries) {
      if (!isIntersecting) near.delete(target);
      else if (paused) near.add(target);
      else load(target);
    }
  };

  // Start observing the rows (Main.start() calls this on every page; later calls do nothing).
  // Without IntersectionObserver no preview loads: the name is in the row's text anyway.
  const start = () => {
    if (observer || !rows.length || typeof IntersectionObserver !== 'function') return;
    observer = new IntersectionObserver(seen, { rootMargin: MARGIN });
    for (const row of rows) {
      const span = spanOf(row);
      if (span && span.dataset.state !== 'set') observer.observe(row);
    }
  };

  // Request no more specimens until resume(). Masks already set stay.
  const pause = () => {
    paused = true;
  };

  // Load the rows near the screen now; the rest load as they come near. Each queued row is
  // observed afresh, so the observer reports where it is now (it may have left, or been
  // detached, since it was queued) rather than where it was.
  const resume = () => {
    if (!paused) return;
    paused = false;
    const queued = [...near];
    near.clear();
    if (!observer) return;
    for (const row of queued) {
      observer.unobserve(row);
      observer.observe(row);
    }
  };

  return Object.freeze({
    start,
    pause,
    resume,
    get paused() {
      return paused;
    },
  });
})();
