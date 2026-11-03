"""The privacy sweep on the live site (Milestone 2 step 9): ``tests/site/test_privacy.py``'s
``Sweep``, in Chromium and Firefox, through Cloudflare.

The browser's own page requests accept HTML (a test below checks it), which is what makes
Cloudflare inject its beacon when a setting lets it. On a long real list the sweep opens an even
spread of 20 details panels and loads "Type your own text" for 2 fonts, to keep the load on the
server small.

Once a list has loaded, drawing a row asks for nothing, also a row whose preview arrived while
content-visibility skipped it (M3-D10; site/CONTRACT.md sections 4 and 5): a test below makes
most of the real list's previews arrive that way, in Chromium, the tested browser that puts
their style off.
"""

from typing import Any

import pytest
from tests.live import checks
from tests.site.test_privacy import FILTER_NAMES, SETTLE_JS, Sweep, expected_csp, page_paths
from tests.site.test_specimens_loader import (
    FLING_JS,
    LOADING_JS,
    SKIPPED_SETS_JS,
    SPEC_PREFIX,
    Hold,
    collect_garbage,
    wait_for,
)

WIDE = {"width": 1280, "height": 900}


def test_the_live_site_loads_only_its_own_files_and_stores_nothing(
    live_guarded: Any, http: Any
) -> None:
    sitemap = checks.get(http, "/sitemap.xml", "xml")
    assert sitemap.status == 200
    sweep = Sweep(live_guarded(viewport=WIDE), csp=expected_csp(), max_details=20, max_type_own=2)
    sweep.run(page_paths(sitemap.body))
    assert sweep.findings() == {}
    documents = [r for r in sweep.requests if r.resource_type == "document"]
    assert documents
    for request in documents:
        assert "text/html" in request.all_headers().get("accept", ""), request.url
    done = sweep.done
    assert done["ranks"] >= 2
    assert sweep.control_names >= FILTER_NAMES
    assert done["details"] >= 1
    assert done["font links"] == 1
    assert done["phone filters"] == 1
    assert done["type-own loaded"] == min(done["type-own fonts"], sweep.max_type_own)
    print(f"live privacy sweep: {dict(sorted(done.items()))}")


def test_previews_set_far_from_the_screen_are_never_fetched_after_a_list(
    live_guarded: Any, browser_name: str
) -> None:
    """A fling passes every row while the previews are held back; they arrive with the page
    back at the top, so most masks are set on rows content-visibility skips. The caches are
    then emptied and garbage collected, as DevTools' "Disable cache" and a long visit would,
    and the sweep's list step (a pasted list, every Show mode and rank, the whole list
    scrolled, Forget, a dropped list) may make only the hopper's two requests."""
    if browser_name != "chromium":
        pytest.skip("of the tested browsers, Chromium alone puts a skipped row's style off")
    guarded = live_guarded(viewport=WIDE)
    sweep = Sweep(guarded, csp=expected_csp())
    page = sweep.open_list()
    if not page.locator("#mine").count():
        pytest.skip("no hopper on this site yet")
    held = Hold(page, lambda path: path.startswith(SPEC_PREFIX))
    try:
        page.evaluate(SKIPPED_SETS_JS)
        page.evaluate(FLING_JS)
        page.evaluate("() => window.scrollTo(0, 0)")
        for _ in range(3):
            page.evaluate(SETTLE_JS)
        assert held.routes, "the fling asked for no preview"
    finally:
        held.release()
    wait_for(page, lambda: not page.evaluate(LOADING_JS), "the previews to arrive", timeout_s=60)
    skipped = page.evaluate("() => window.__tffSkippedSets")
    assert skipped >= 50, f"only {skipped} previews arrived on skipped rows: nothing to test"
    cdp = collect_garbage(guarded.context, page)
    sweep.give_list(page)
    assert sweep.done["hopper lists"] == 2
    assert sweep.findings() == {}
    cdp.detach()


def test_the_live_tip_link_is_the_only_way_to_stripe(live_guarded: Any) -> None:
    guarded = live_guarded(viewport=WIDE)
    sweep = Sweep(guarded, csp=expected_csp())
    page = sweep.open_list()
    sweep.scroll_specimens(page)
    tip = page.get_attribute("#tip a", "href") if page.locator("#tip a").count() else None
    if tip:
        page.hover("#tip a")
    page.wait_for_load_state("networkidle")
    assert sweep.stripe_requests() == [], "Stripe was contacted before a click"
    assert sweep.findings() == {}
    if tip is None:
        return  # no tip link yet (Milestone 2 step 8)
    request = sweep.click_tip(page)
    assert request.url == tip
    assert request.is_navigation_request()
    assert guarded.blocked == [tip]
