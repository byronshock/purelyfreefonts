"""Give the hopper (site/js/55-mine.js) a list in a Playwright page, as a visitor would: pasted
and checked, dropped on the page, or chosen with Choose file; then wait until the page has
taken it, a summary or an error.

Every way in ends in an announcement in ``#status``, the page's one live region (an error, or
the summary and the count line), and the "Checking your list…" line hides again, so a list has
been taken once both have happened. Used by tests/site/test_mine.py and test_privacy.py.

A page that ``choose`` is used on is ``arm``-ed first, before it loads: Playwright turns on its
file-chooser interception asynchronously, and a key pressed on Choose file just after
``expect_file_chooser`` could reach the browser first, which then opened no chooser for the
test (3 runs in 195 under load).
"""

import time
from typing import Any

# A file dropped on the element ``selector`` matches: dragenter, dragover and drop, each
# cancelable, as a browser fires them for a file dragged in from outside. Returns whether the
# page cancelled each (a cancelled drop is one the browser doesn't open). The file holds
# ``text`` as UTF-8, or ``bytes`` (a list of numbers), or ``size`` bytes of "a".
DROP_JS = """([selector, text, bytes, size, name]) => {
  let body;
  if (size) body = new Uint8Array(size).fill(0x61);
  else if (bytes) body = new Uint8Array(bytes);
  else body = new TextEncoder().encode(text);
  const transfer = new DataTransfer();
  transfer.items.add(new File([body], name, { type: 'text/plain' }));
  const target = document.querySelector(selector);
  const fire = (type) => {
    const event = new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: transfer });
    target.dispatchEvent(event);
    return event.defaultPrevented;
  };
  return { enter: fire('dragenter'), over: fire('dragover'), drop: fire('drop') };
}"""
# Text dragged over the page, not a file: the page must leave it to the browser.
DRAG_TEXT_JS = """(selector) => {
  const transfer = new DataTransfer();
  transfer.setData('text/plain', 'Inter');
  const event = new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer });
  document.querySelector(selector).dispatchEvent(event);
  return event.defaultPrevented;
}"""
PASTE_JS = """(text) => {
  document.getElementById('mine-paste').value = text;
  document.getElementById('mine-check').click();
}"""
STATUS_JS = "() => document.getElementById('status').textContent"
BUSY_JS = "() => document.getElementById('mine-busy').hidden"
# Whether the element ``selector`` matches is shown and wholly inside the window.
IN_VIEW_JS = """(selector) => {
  const el = document.querySelector(selector);
  if (!el || !el.getClientRects().length || getComputedStyle(el).visibility !== 'visible') {
    return false;
  }
  const r = el.getBoundingClientRect();
  return r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth;
}"""


def arm(page: Any) -> None:
    """Have Playwright take every file chooser this page opens, from now on (see the top)."""
    page.on("filechooser", lambda _chooser: None)


def in_view(page: Any, selector: str) -> bool:
    return page.evaluate(IN_VIEW_JS, selector)


def wait_taken(page: Any, before: str, timeout_s: float = 20.0) -> None:
    """Wait until the hopper has taken a list given since ``#status`` read ``before``."""
    deadline = time.monotonic() + timeout_s
    while page.evaluate(STATUS_JS) == before or not page.evaluate(BUSY_JS):
        if time.monotonic() > deadline:
            raise AssertionError("the hopper never took the list")
        page.wait_for_timeout(20)


def paste(page: Any, text: str, *, by_script: bool = False) -> None:
    """Paste ``text`` and press Check. ``by_script`` does both in the page, so nothing scrolls
    (a scroll could bring rows near the screen and request their previews)."""
    before = page.evaluate(STATUS_JS)
    if by_script:
        page.evaluate(PASTE_JS, text)
    else:
        page.fill("#mine-paste", text)
        page.click("#mine-check")
    wait_taken(page, before)


def drop(
    page: Any,
    text: str = "",
    *,
    data: bytes | None = None,
    size: int = 0,
    selector: str = "#list",
    name: str = "fonts.txt",
) -> dict[str, bool]:
    """Drop a file on ``selector`` (see ``DROP_JS``) and wait until the hopper has taken it.
    Returns whether the page cancelled dragenter, dragover and drop."""
    before = page.evaluate(STATUS_JS)
    payload = list(data) if data is not None else None
    prevented = page.evaluate(DROP_JS, [selector, text, payload, size, name])
    wait_taken(page, before)
    return prevented


def choose(page: Any, data: bytes, *, name: str = "fonts.txt", key: str | None = None) -> None:
    """Choose a file with Choose file: a click, or ``key`` pressed on the focused button. The
    page is ``arm``-ed (see the top)."""
    before = page.evaluate(STATUS_JS)
    with page.expect_file_chooser() as chooser:
        if key is None:
            page.click("#mine-choose")
        else:
            page.keyboard.press(key)
    chooser.value.set_files(files=[{"name": name, "mimeType": "text/plain", "buffer": data}])
    wait_taken(page, before)
