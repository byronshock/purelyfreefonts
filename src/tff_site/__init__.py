"""tff_site: builds purelyfreefonts.com from the catalog.

It may import only light tff_catalog modules (keys, paths, jsonio), never numpy,
so the site build starts fast and stays independent of the pipeline.
"""
