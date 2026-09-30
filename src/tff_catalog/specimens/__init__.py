"""Stage "specimens": SVG previews of each font (Milestone 2 step 5, design-m2 §3). Owner: agent A5.

It runs inside ``tff-catalog refresh`` between "export" and "export-site"
(design-m1 gap G9): for each ``preview_ok`` font it fetches ``font_file.url``
into ``~/.cache/tff/fonts/<sha256>``, checks the sha256, renders
``build/specimens/<id>.svg`` with HarfBuzz and records ``preview {path, sha256}``.
The SVGs are committed, because ``tff-site build`` makes no network requests.
"""

RENDERER_VERSION = 1  # bump when output bytes change on purpose
UNITS_PER_EM = 256  # integer coordinate grid
NAME_SIZE_EM = 1.0  # line 1: the family name
SAMPLE_SIZE_EM = 0.6  # line 2: the sample
# The owner's site ruling of 2026-09-29 (specimen_sample_latin), recorded in data/reviews/site/.
# Changing either line re-renders every specimen (the texts are in the cache key).
SAMPLE = "Dolor dolorosus est."  # owner ruling 2026-09-29 (specimen_sample_latin)
BASIC_SAMPLE = "Sphinx of black quartz, judge my vow"  # fallback for basic-Latin fonts
DEFAULT_WEIGHT = 400.0  # variable fonts: wght=400 if the axis allows it, else the default instance

# Budget (tff-catalog specimens --check), in decimal kilobytes as tff_site.budgets counts them,
# so a set that passes here also passes `tff-site check`. The 30 KB cap and the total are raw
# bytes, the stricter reading for SVG text, which always compresses.
SMALL_GZIP_BYTES = 5_000  # at least half the files at or under this, gzip -9
MAX_FILE_BYTES = 30_000  # larger files are re-rendered with the name only
MAX_TOTAL_BYTES = 10_000_000

FLAGS = ("specimen_failed", "specimen_name_only", "specimen_hash_mismatch")
