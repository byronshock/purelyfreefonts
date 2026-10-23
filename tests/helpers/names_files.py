"""The files beside a catalog that the site build reads too: the names file of its run and
the reviewed list beside that (``tff_site.matchfile``, the hopper's match file).

A test that builds from a copy of a catalog copies them with ``copy_names``, so the copy
builds as the original does.
"""

import shutil
from pathlib import Path

from tff_site import matchfile


def copy_names(data_path: Path, copy_path: Path) -> Path:
    """Copy the names file of the catalog at ``data_path``, and its reviewed list when it has
    one, to where the build looks for them beside ``copy_path``, the catalog's copy. Return
    the copied names file's path."""
    names = matchfile.default_names_path(data_path)
    target = matchfile.default_names_path(copy_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(names, target)
    reviewed = matchfile.default_reviewed_path(names)
    if reviewed.is_file():
        shutil.copyfile(reviewed, matchfile.default_reviewed_path(target))
    return target
