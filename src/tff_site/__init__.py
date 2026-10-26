"""tff_site: builds purelyfreefonts.com from the catalog.

It may import only light tff_catalog modules, which need the standard library alone
(``LIGHT_CATALOG_MODULES``: keys, names, owned, paths and jsonio), never numpy, so the site
build starts fast and stays independent of the pipeline. ``owned``, the hopper's matching,
which ``tff_site.matchfile`` builds the match file with, is one: it imports only keys, names
and jsonio.
"""

# The tff_catalog modules tff_site may load (tests/site/test_views.py checks every module).
LIGHT_CATALOG_MODULES = frozenset(
    {
        "tff_catalog",
        "tff_catalog.jsonio",
        "tff_catalog.keys",
        "tff_catalog.names",
        "tff_catalog.owned",
        "tff_catalog.paths",
    }
)
