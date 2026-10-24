"""tff_catalog: the pipeline that builds the ranked catalog of purely free Latin fonts."""

from types import MappingProxyType

__version__ = "0.1.0"

# The ranking methodology this code implements (docs/ranking-methodology.md), as a date per
# rank key: the date of the rules that rank is scored by (owner rulings
# q20_previous_score_at_switch, 2026-10-01, and method_versions_all_seven, 2026-10-19;
# tff_catalog.methods). A rank's date moves when its rules change. METHOD_VERSION, the
# newest, is what a catalog records as run.method_version. 2026-10-23: GitHub and Nerd Fonts
# count toward the two-group gate while deferred, in the ranks that list them
# (deferred_sources_count_for_gate).
LEGACY_METHOD_VERSION = "2026-09-29"  # every rank's, before ranks had their own (methods.py)
METHOD_VERSIONS = MappingProxyType(
    {
        "overall": "2026-10-20",  # the measurement model (milestone-method step 10)
        "desktop_chosen": "2026-10-23",  # the interim desktop rules, with the gate-only sources
        "desktop_installed": "2026-10-23",  # the interim desktop rules, with the gate-only sources
        "project": "2026-10-20",  # the measurement model (milestone-method step 10)
        "coding": "2026-10-23",  # the interim desktop rules, with the gate-only sources
        "dev_apps": "2026-10-20",  # the measurement model (milestone-method step 10)
        "rising": "2026-10-20",  # the interim desktop rules (milestone-method step 11)
    }
)
METHOD_VERSION = max(METHOD_VERSIONS.values())
