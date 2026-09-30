"""Rebuild the arch_repos fixture: the sync databases, the snapshot and the golden file.

Run from the repository root after changing ``ARCH`` or ``CACHYOS`` below::

    uv run python -m tests.collectors.arch_repos.build_fixture

It writes ``http/`` (the four databases as pacman serves them: gzip tars for
Arch, a zstd tar for CachyOS, and ``index.json``), runs ``fetch()`` offline
into ``snapshot/`` and rewrites ``expected.jsonl``. Review the diff before
committing it.

``ARCH`` is trimmed real data (ruling T1; see the fixture's NOTICE): 42 rows
of the Arch databases of 2026-09-25, with the fields the collector does not
keep left out, packager names never copied, web addresses kept only for
organisations, and long dependency lists cut. ``CACHYOS`` is synthetic.
"""

import gzip
import io
import logging
import lzma
import shutil
import sys
import tarfile
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from compression import zstd
from datetime import UTC, date, datetime, time
from pathlib import Path

from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking.arch_repos import COLLECTOR, DEFAULT_REPOS
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.store import RawDir, Snapshot, Store

DAY = date(2026, 9, 25)  # the snapshot date
FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
LOG = logging.getLogger("tests.arch_repos")
MTIME = int(datetime.combine(DAY, time(4), tzinfo=UTC).timestamp())  # of every tar member

type Fields = dict[str, list[str]]  # desc field -> its values
type Package = tuple[Fields, bool]  # (fields, split: DEPENDS in an old-style "depends" file)


def pkg(name: str, version: str, *, split: bool = False, **fields: Sequence[str]) -> Package:
    """A package: NAME, BASE (default the name), VERSION, ARCH "any", plus ``fields``."""
    out: Fields = {"NAME": [name], "BASE": [name], "VERSION": [version], "ARCH": ["any"]}
    for key, values in fields.items():
        out[key.upper()] = list(values)
    return out, split


# Trimmed real rows of Arch Linux's core, extra and multilib databases (2026-09-25).
ARCH: dict[str, list[Package]] = {
    "core": [
        pkg(
            "glibc",
            "2.44+r24+g16be1518495f-1",
            arch=["x86_64"],
            url=["https://www.gnu.org/software/libc"],
            license=["GPL-2.0-or-later", "LGPL-2.1-or-later"],
            depends=["linux-api-headers>=4.10", "tzdata", "filesystem"],
            optdepends=["gd: for memusagestat", "perl: for mtrace"],
        ),
        pkg("filesystem", "2025.10.12-1", license=["0BSD"], depends=["iana-etc"]),
    ],
    "extra": [
        # Fonts.
        pkg(
            "ttf-dejavu",
            "2.37+18+g9b5d1b2f-8",
            url=["https://dejavu-fonts.github.io"],
            license=["custom"],
            provides=["ttf-font"],
        ),
        pkg("ttf-liberation", "2.1.5-2", license=["custom:OFL"], provides=["ttf-font"]),
        pkg(
            "noto-fonts",
            "1:2026.09.01-1",
            url=["https://fonts.google.com/noto"],
            license=["OFL-1.1-no-RFN"],
            provides=["ttf-font"],
            optdepends=[
                "noto-fonts-cjk: CJK characters",
                "noto-fonts-emoji: Emoji characters",
                "noto-fonts-extra: additional variants (condensed, semi-bold, extra-light)",
            ],
        ),
        pkg("noto-fonts-emoji", "1:2.051-1", license=["OFL-1.1-no-RFN"], provides=["emoji-font"]),
        pkg("ttf-fira-sans", "1:4.301-3", base=["fira-sans"], license=["custom:OFL"]),
        pkg("ttf-fira-code", "6.2-4", base=["fira-code"], license=["OFL-1.1"]),
        pkg(
            "ttf-fantasque-nerd",
            "3.5.1-2",
            base=["nerd-fonts"],
            license=["OFL-1.1-no-RFN"],
            groups=["nerd-fonts"],
            provides=["ttf-font-nerd"],
            replaces=["nerd-fonts-fantasque-sans-mono"],
        ),
        pkg("ttf-jetbrains-mono", "2.304-2", license=["custom:OFL"]),
        pkg(
            "ttf-jetbrains-mono-nerd",
            "3.5.1-2",
            base=["nerd-fonts"],
            license=["OFL-1.1-no-RFN"],
            groups=["nerd-fonts"],
            provides=["ttf-font-nerd"],
            replaces=["nerd-fonts-jetbrains-mono"],
        ),
        pkg("ttf-hack", "3.003-7", license=["custom:ttf-hack"]),
        pkg("ttf-linux-libertine", "5.3.0-10", license=["GPL", "custom:OFL"]),
        pkg(
            "adobe-source-sans-fonts",
            "3.052-2",
            url=["https://adobe-fonts.github.io/source-sans/"],
            license=["OFL-1.1"],
            provides=["adobe-source-sans-pro-fonts=3.052"],
            replaces=["adobe-source-sans-pro-fonts<=3.028-1"],
        ),
        pkg(
            "cantarell-fonts",
            "1:0.311-1",
            url=["https://gitlab.gnome.org/GNOME/cantarell-fonts"],
            license=["OFL-1.1"],
        ),
        pkg(
            "gsfonts",
            "20200910-6",
            license=["AGPL-3.0-only WITH PS-or-PDF-font-exception-20170817"],
        ),
        pkg(
            "wqy-zenhei",
            "0.9.45-10",
            license=["GPL2", 'custom:"font embedding exception"'],
            depends=["sh"],
        ),
        pkg(
            "otf-monaspace-nerdfonts",
            "1.400-1",
            base=["monaspace-font"],
            license=["OFL-1.0-RFN"],
            provides=["monaspace-font", "ttf-font-nerd"],
        ),
        pkg(
            "woff2-font-awesome",
            "7.3.1-1",
            base=["font-awesome"],
            license=["LicenseRef-OFL"],
            provides=["ttf-font-awesome"],
            replaces=["ttf-font-awesome"],
        ),
        pkg("otf-font-awesome", "7.3.1-1", base=["font-awesome"], license=["LicenseRef-OFL"]),
        pkg("awesome-terminal-fonts", "1.1.0-5", license=["MIT"]),
        pkg(
            "ttf-nerd-fonts-symbols",
            "3.5.1-1",
            license=["MIT"],
            groups=["nerd-fonts"],
            provides=["ttf-font-nerd"],
            replaces=["ttf-nerd-fonts-symbols-1000-em", "ttf-nerd-fonts-symbols-2048-em"],
            depends=["ttf-nerd-fonts-symbols-common"],
        ),
        pkg(
            "ttf-nerd-fonts-symbols-common",
            "3.5.1-1",
            base=["ttf-nerd-fonts-symbols"],
            license=["MIT"],
        ),
        pkg("ttf-roboto", "3.016-1", license=["OFL-1.1"], provides=["ttf-font"]),
        pkg("ttf-opensans", "3.003-1", license=["OFL-1.1"]),
        pkg(
            "powerline-fonts",
            "2.8.4-4",
            base=["powerline"],
            arch=["x86_64"],
            license=["MIT"],
            provides=["otf-powerline-symbols"],
        ),
        pkg(
            "ttf-carlito",
            "20230509-2",
            license=["LicenseRef-OFL"],
            provides=["google-crosextra-carlito-fonts"],
        ),
        # Names the font pattern catches that are not fonts (not_fonts), and fontconfig.
        pkg(
            "xorg-fonts-encodings",
            "1.1.0-2",
            license=["LicenseRef-xorg-fonts-encodings"],
            groups=["xorg-fonts", "xorg"],
        ),
        pkg(
            "font-manager",
            "0.9.4-3",
            arch=["x86_64"],
            license=["GPL-3.0-only"],
            depends=["cairo", "fontconfig", "gtk4"],
        ),
        pkg(
            "sdl2_ttf",
            "2.24.0-2",
            arch=["x86_64"],
            license=["MIT"],
            depends=["sdl2", "freetype2", "harfbuzz"],
        ),
        pkg(
            "woff2",
            "1.0.2-6",
            arch=["x86_64"],
            license=["MIT"],
            provides=["libwoff2common.so=1.0.2-64"],
            depends=["brotli", "gcc-libs", "glibc"],
        ),
        pkg(
            "fontconfig",
            "2:2.18.3-2",
            arch=["x86_64"],
            license=["HPND AND Unicode-DFS-2016"],
            provides=["fontconfig-docs", "libfontconfig.so=1-64"],
            depends=["bash", "expat", "freetype2", "glibc"],
        ),
        # Packages that pull fonts in (dependency lists cut).
        pkg(
            "auto-multiple-choice",
            "1.7.0-7",
            arch=["x86_64"],
            license=["GPL-2.0-or-later"],
            depends=["cairo", "perl", "texlive-latex", "ttf-linux-libertine"],
            optdepends=["netpbm: manual data capture"],
        ),
        pkg(
            "firefox",
            "156.0.1-1",
            arch=["x86_64"],
            url=["https://www.firefox.com/"],
            license=["MPL-2.0"],
            depends=["fontconfig", "gtk3", "nss", "ttf-font"],
            optdepends=["libnotify: Notification integration"],
        ),
        pkg(
            "chromium",
            "153.0.8010.52-1",
            arch=["x86_64"],
            license=["BSD-3-Clause"],
            depends=["gtk3", "nss", "ttf-liberation", "fontconfig"],
        ),
        pkg(
            "adapta-gtk-theme",
            "3.95.0.11-4",
            license=["CCPL", "GPL2"],
            optdepends=["noto-fonts: Recommended font", "ttf-roboto: Recommended font"],
        ),
        pkg(
            "waybar",
            "0.15.0-3",
            arch=["x86_64"],
            license=["MIT"],
            depends=["gtk3", "libpulse"],
            optdepends=["otf-font-awesome: Icons in the default configuration"],
        ),
        pkg(
            "gnome-characters",
            "50.0-1",
            arch=["x86_64"],
            license=["BSD-3-Clause AND GPL-2.0-or-later"],
            depends=["dconf", "emoji-font", "gjs", "gtk4"],
        ),
        pkg(
            "rofimoji",
            "6.8.0-1",
            license=["MIT"],
            depends=["python-configargparse"],
            optdepends=[
                "emoji-font: for the emojis character file",
                "nerd-fonts: for the nerd_font character file",
                "otf-font-awesome: for the fontawesome6 character file",
                "woff2-font-awesome: for the fontawesome6 character file",
                "rofi: for one of the X.Org selectors",
            ],
        ),
        pkg(
            "hedgewars",
            "1.0.3-83",
            arch=["x86_64"],
            license=["GPL-2.0-only"],
            depends=["qt5-base", "sdl2", "sdl2_ttf", "lua51"],
        ),
    ],
    "multilib": [
        pkg(
            "steam",
            "1.0.0.87-3",
            arch=["x86_64"],
            license=["LicenseRef-steam-subscriber-agreement"],
            depends=["bash", "ttf-font", "lib32-fontconfig", "lib32-glibc"],
            optdepends=["xorg-fonts-misc: for non-latin locales"],
        ),
        pkg(
            "lib32-fontconfig",
            "2:2.18.3-1",
            arch=["x86_64"],
            license=["HPND AND Unicode-DFS-2016"],
            depends=["fontconfig", "lib32-expat", "lib32-freetype2", "lib32-glibc"],
        ),
    ],
}

# Synthetic rows shaped like CachyOS's own repository (ruling T1: never real rows).
# Names ending in "synth" or starting with "synth-", versions, licenses and other
# packages are invented; only the Arch font packages they depend on are real.
# The packager line has a real line's shape without an address (no emails in fixtures).
SYNTH_PACKAGER = ["Synth Packager <synth-packager>"]  # must never reach an extract
CACHYOS: list[Package] = [
    pkg(
        "synth-kde-settings",
        "2.0-1",
        license=["GPL-3.0-or-later"],
        packager=SYNTH_PACKAGER,
        pgpsig=["c3ludGhldGljIHNpZ25hdHVyZQ=="],
        conflicts=["synth-desktop-settings"],
        provides=["synth-desktop-settings"],
        depends=["synth-cursor-theme", "ttf-fira-sans", "noto-fonts", "ttf-fantasque-nerd"],
        makedepends=["synth-build-helper"],
    ),
    # Old-style database entry: the dependency fields sit in a separate "depends" file.
    pkg(
        "synth-st-config",
        "1.2-1",
        split=True,
        license=["MIT"],
        packager=SYNTH_PACKAGER,
        depends=["ttf-hack", "ttf-fira-code", "ttf-jetbrains-mono"],
    ),
    pkg(
        "synth-browser-bin",
        "12.0-1",
        arch=["x86_64"],
        license=["MPL-2.0"],
        packager=SYNTH_PACKAGER,
        depends=["gtk3", "ttf-font"],
    ),
    pkg(
        "synth-office-bin",
        "3.1-2",
        arch=["x86_64"],
        license=["AGPL-3.0-only"],
        packager=SYNTH_PACKAGER,
        depends=["ttf-carlito", "ttf-dejavu>=2.37", "ttf-dejavu<3", "ttf-liberation", "gtk3"],
    ),
    pkg(
        "synth-prompt-theme",
        "1.0.0-1",
        license=["MIT"],
        packager=SYNTH_PACKAGER,
        optdepends=[
            "powerline-fonts>=2.8: prompt glyphs",
            "ttf-font-nerd: icons",
            "emoji-font: emoji",
            "git: status segment",
        ],
    ),
    pkg(
        "ttf-aster-mono",
        "2.0-1",
        url=["https://aster.synth.example/"],
        license=["OFL-1.1-RFN"],
        packager=SYNTH_PACKAGER,
        provides=["ttf-font", "aster-mono-font=2.0"],
        depends=["fontconfig"],
    ),
    # A rebuild of an Arch package under its Arch name.
    pkg("ttf-hack", "3.003-7.1", license=["custom:ttf-hack"], packager=SYNTH_PACKAGER),
    # Font-like only through what it provides, and only through its group.
    pkg(
        "iosevka-synth-bin",
        "33.0-1",
        license=["OFL-1.1", "OFL-1.1"],
        packager=SYNTH_PACKAGER,
        provides=["ttf-font"],
    ),
    pkg(
        "glyphs-synth",
        "0.1-1",
        license=["MIT"],
        packager=SYNTH_PACKAGER,
        groups=["nerd-fonts"],
    ),
    pkg(
        "synth-kernel-manager",
        "1.0-1",
        arch=["x86_64"],
        license=["GPL-3.0-or-later"],
        packager=SYNTH_PACKAGER,
        depends=["qt6-base", "polkit"],
    ),
]

# The databases' Last-Modified and ETag answers (invented).
HEADERS: dict[str, tuple[str | None, str]] = {
    "core": ("Fri, 25 Sep 2026 01:37:12 GMT", '"6ab74f0a-1fb4d"'),
    "extra": ("Fri, 25 Sep 2026 04:50:18 GMT", '"6ab74f0a-873a18"'),
    "multilib": ("Thu, 24 Sep 2026 19:02:40 GMT", '"6ab4c1e0-14e9a"'),
    "cachyos": ("Thu, 24 Sep 2026 02:10:42 GMT", '"6ab729a2-80dc7"'),
}
COMPRESSION = {"core": "gz", "extra": "gz", "multilib": "gz", "cachyos": "zst"}


def desc_text(fields: Mapping[str, Sequence[str]]) -> str:
    """A desc file: ``%FIELD%``, one value per line, a blank line after each field."""
    return "".join(f"%{k}%\n" + "".join(f"{v}\n" for v in vs) + "\n" for k, vs in fields.items())


def _member(tar: tarfile.TarFile, name: str, data: bytes | None) -> None:
    info = tarfile.TarInfo(name)
    info.mtime = MTIME
    info.uname = info.gname = "root"
    if data is None:
        info.type, info.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(info)
    else:
        info.size, info.mode = len(data), 0o644
        tar.addfile(info, io.BytesIO(data))


def tar_bytes(packages: Iterable[Package]) -> bytes:
    """An uncompressed pacman database tar of ``packages`` (sorted, fixed metadata)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for fields, split in sorted(packages, key=lambda p: p[0]["NAME"][0]):
            folder = f"{fields['NAME'][0]}-{fields['VERSION'][0]}"
            deps = {k: v for k, v in fields.items() if split and k.endswith("DEPENDS")}
            rest = {k: v for k, v in fields.items() if k not in deps}
            _member(tar, folder, None)
            _member(tar, f"{folder}/desc", desc_text(rest).encode())
            if deps:
                _member(tar, f"{folder}/depends", desc_text(deps).encode())
    return buf.getvalue()


def compress(data: bytes, how: str) -> bytes:
    """``data`` compressed as pacman's mirrors serve databases ("" leaves it plain)."""
    match how:
        case "gz":
            return gzip.compress(data, compresslevel=9, mtime=0)
        case "zst":
            return zstd.compress(data, level=19)
        case "xz":
            return lzma.compress(data)
        case "":
            return data
    raise ValueError(f"unknown compression {how!r}")


def databases() -> dict[str, list[Package]]:
    """Every repository's packages, by repository name."""
    return {**ARCH, "cachyos": CACHYOS}


def write_http(
    directory: Path = HTTP,
    dbs: Mapping[str, Iterable[Package]] | None = None,
    compression: Mapping[str, str] = COMPRESSION,
    answers: Mapping[str, tuple[str | None, str]] = HEADERS,
) -> None:
    """Write the databases and ``index.json`` for ``DEFAULT_REPOS``' URLs into ``directory``.

    ``answers`` are each repository's (Last-Modified, ETag); None leaves the
    Last-Modified header out.
    """
    dbs = databases() if dbs is None else dbs
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    index = []
    for repo in DEFAULT_REPOS:
        body = f"{repo.name}.db"
        (directory / body).write_bytes(compress(tar_bytes(dbs[repo.name]), compression[repo.name]))
        modified, etag = answers[repo.name]
        headers = {"content-type": "application/octet-stream", "etag": etag}
        if modified is not None:
            headers["last-modified"] = modified
        index.append({"url": repo.url, "headers": headers, "body": body})
    mockhttp.write_index(directory, index)


def run_fetch(
    tmp: Path,
    http: Path = HTTP,
    *,
    previous: Snapshot | None = None,
    settings: object = None,
) -> Snapshot:
    """Run ``fetch()`` offline against ``http`` into a fresh store under ``tmp``."""
    settings = settings or load_settings(COLLECTOR, Paths.for_root(ROOT))
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(DAY, time(6), tzinfo=UTC)),
        Fetcher(
            transport=mock.transport, min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0), log=LOG
        ) as fetcher,
        store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=DAY,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp / "raw"),
                previous=previous,
                settings=settings,
                log=LOG,
            )
        )
    assert not mock.unmatched, mock.unmatched
    snap = store.snapshot(COLLECTOR.name, DAY)
    assert snap is not None
    return snap


def main() -> int:
    write_http()
    with tempfile.TemporaryDirectory() as name:
        snap = run_fetch(Path(name))
        target = FIXTURE / "snapshot"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(snap.path, target)
    regen.regen(COLLECTOR.name)
    print(f"rebuilt {FIXTURE.relative_to(ROOT)}: http/, snapshot/, expected.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
