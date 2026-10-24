// 58-matcher: the matcher module's hand-off (site/CONTRACT.md sections 5 and 11). The hopper's
// parser and matcher are a module of their own, /assets/matcher.<h>.js, which the list page
// loads only once a visitor's list arrives (Data.loadMatcher; the owner's ruling of 2026-10-23,
// inventory_matcher_on_demand), so the page keeps its weight. Modules share no top-level
// names, so this module holds its own Keys, and hands its API to the page in one way only: its
// last line defines globalThis.tffMatcher, read-only and permanent (defineProperty's defaults:
// not writable, enumerable or configurable), as this frozen object, which Data.loadMatcher
// reads once the script has run. It holds code only, never a list, a table or a result.
const Matcher = Object.freeze({ OwnedParse, OwnedMatch });
Object.defineProperty(globalThis, 'tffMatcher', { value: Matcher });
