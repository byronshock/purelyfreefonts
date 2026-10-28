from typing import Any
from tests.helpers import hopper_page
from tests.site.test_mine import doc, families, first, RECORDER_JS, IDLE_HELD_JS, IDLE_RUN_JS, poll, wait_held

WRITES_JS = """() => {
  window.__tffWrites = [];
  const status = document.getElementById('status');
  new MutationObserver(() => {
    const s = getComputedStyle(status);
    window.__tffWrites.push([status.textContent, s.visibility, status.getClientRects().length,
      document.documentElement.getAttribute('data-list-pending'),
      typeof status.checkVisibility === 'function' ? status.checkVisibility({visibilityProperty: true}) : null]);
  }).observe(status, { childList: true, characterData: true, subtree: true });
}"""


def test_probe(guarded_context: Any, first: str) -> None:
    guarded = guarded_context()
    guarded.context.add_init_script(RECORDER_JS)
    guarded.context.add_init_script(IDLE_HELD_JS)
    page = guarded.new_page()
    held: list[Any] = []
    page.route("**/assets/list.*.json", lambda route: held.append(route))
    page.goto("/#cat=serif")
    poll(page, "() => document.documentElement.hasAttribute('data-js')", "js")
    wait_held(page, held)
    page.evaluate(WRITES_JS)
    page.evaluate(hopper_page.DROP_JS, ["#list", first, None, 0, "fonts.txt"])
    poll(page, "() => !document.getElementById('mine-busy').hidden", "start")
    held[0].continue_()
    poll(page, "() => !document.getElementById('mine-result').hidden", "result")
    print("pending before begin", page.evaluate("() => document.documentElement.getAttribute('data-list-pending')"))
    page.evaluate(IDLE_RUN_JS)
    page.wait_for_function("() => window.__tffReady >= 1")
    for w in page.evaluate("() => window.__tffWrites"):
        print(w)
    guarded.assert_clean(page)
