"""The privacy sweep on the live site (Milestone 2 step 9): ``tests/site/test_privacy.py``'s
``Sweep``, in Chromium and Firefox, through Cloudflare.

The browser's own page requests accept HTML (a test below checks it), which is what makes
Cloudflare inject its beacon when a setting lets it. On a long real list the sweep opens an even
spread of 20 details panels and loads "Type your own text" for 2 fonts, to keep the load on the
server small.
"""

from typing import Any

from tests.live import checks
from tests.site.test_privacy import FILTER_NAMES, Sweep, expected_csp, page_paths

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
