// 58-matcher: the matcher module's hand-off (site/CONTRACT.md sections 5 and 11). The hopper's
// parser and matcher are a module of their own, /assets/matcher.<h>.js, which the list page
// loads only once a visitor's list arrives (Data.loadMatcher; the owner's ruling of 2026-10-23,
// inventory_matcher_on_demand), so the page keeps its weight. Modules share no top-level
// names, so this module holds its own Keys, and hands its API to the page in one way only: its
// last line, Matcher.handOver('tffMatcher'). Data defines that name before it adds the
// module's script, as a permanent setter, and handOver assigns the API to it, once. Finding no
// such setter (the module was run by something other than Data, or before Data asked),
// handOver defines the name itself, read-only and permanent, so a page that asks later
// refuses it: the module's one run leaves the name defined for good, whoever ran it. The API
// is a frozen { OwnedParse, OwnedMatch }: code only, never a list, a table or a result.
const Matcher = (() => {
  const api = Object.freeze({ OwnedParse, OwnedMatch });
  const handOver = (name) => {
    const slot = Object.getOwnPropertyDescriptor(globalThis, name);
    if (slot && typeof slot.set === 'function' && !slot.configurable) {
      globalThis[name] = api;
    } else {
      Object.defineProperty(globalThis, name, {
        value: api, writable: false, enumerable: false, configurable: false,
      });
    }
  };
  return Object.freeze({ api, handOver });
})();
Matcher.handOver('tffMatcher');
