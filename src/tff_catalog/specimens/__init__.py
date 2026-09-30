"""Stage "specimens": SVG previews of each font (Milestone 2 step 5, design-m2 §3). Owner: agent A5.

It runs inside ``tff-catalog refresh`` between "export" and "export-site"
(design-m1 gap G9): for each ``preview_ok`` font it fetches ``font_file.url``
into ``~/.cache/tff/fonts/<sha256>``, checks the sha256, renders
``build/specimens/<id>.svg`` with HarfBuzz and records ``preview {path, sha256}``.
The SVGs are committed, because ``tff-site build`` makes no network requests.
"""

RENDERER_VERSION = 2  # bump when output bytes change on purpose (2: the 16 KB gzip cap)
UNITS_PER_EM = 256  # integer coordinate grid
NAME_SIZE_EM = 1.0  # line 1: the family name
SAMPLE_SIZE_EM = 0.6  # line 2: the sample
# The owner's site ruling of 2026-09-29 (specimen_sample_latin), recorded in data/reviews/site/.
# Changing either line re-renders every specimen (the texts are in the cache key).
SAMPLE = "Dolorem ipsum quaerit nemo."  # owner ruling 2026-09-29 (specimen_sample_latin)
BASIC_SAMPLE = "Sphinx of black quartz, judge my vow"  # fallback for basic-Latin fonts
DEFAULT_WEIGHT = 400.0  # variable fonts: wght=400 if the axis allows it, else the default instance

# Budget (tff-catalog specimens --check), in decimal kilobytes as tff_site.budgets counts them,
# so a set that passes here also passes `tff-site check`. The per-file cap is gzip -9, about
# what a visitor downloads (the owner's site ruling of 2026-09-30, specimen_max_size); the total
# is raw bytes. A size within a few bytes of the cap may differ between zlib builds, which only
# matters when a specimen is drawn again.
SMALL_GZIP_BYTES = 5_000  # at least half the files at or under this, gzip -9
MAX_FILE_GZIP_BYTES = 16_000  # files larger gzipped are re-rendered with the name only
MAX_TOTAL_BYTES = 10_000_000

FLAGS = ("specimen_failed", "specimen_name_only", "specimen_hash_mismatch")
