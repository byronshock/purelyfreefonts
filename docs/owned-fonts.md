# Owned-font check: commands and formats

**Status:** research notes checked on 2026-09-25, not yet verified on every OS. [Milestone 3](milestone-3.md) step 1 tests each command on real machines and fills in [Results](#results); step 7 adds the browser findings. This doc is the source for "How the check works" on the methodology page. Samples here are anonymized: no paths, usernames or host names.

## Rules for every command

- Read-only: it lists font names, changes nothing and sends nothing.
- A one-liner the visitor pastes into a terminal. We never ship a script file: on Windows a downloaded `.ps1` falls under the execution policy (a pasted one-liner does not), and a script is harder to read.
- The default output carries no file paths, because paths contain the username. If paths appear, the parser drops them at once.
- Whatever M3-D1 picks, the parser accepts every alternative below.

## The commands on the site

The hopper ([Milestone 3](milestone-3.md#slice-1-the-hopper), slice 1) shows one command per system under "How to make the list", from `tff_site.data.MINE_COMMANDS`, each with a Copy button. Each writes `fonts.txt` in the home folder, which the visitor then chooses or drops on the list page: not the Desktop, which iCloud (macOS) and OneDrive (Windows) can sync off the machine. Only the Linux command has run on a real machine; the others show "(not yet tested)" until step 1 runs them (`inventory_linux_tested_only`, 2026-10-21).

| System | Command | Tested |
|---|---|---|
| Linux | `fc-list : family \| sort > ~/fonts.txt` | yes (the owner's CachyOS) |
| macOS | `system_profiler SPFontsDataType \| grep 'Family:' \| sort -u > ~/fonts.txt` (about 13 s) | not yet |
| Windows (PowerShell) | `Add-Type -AssemblyName System.Drawing; (New-Object System.Drawing.Text.InstalledFontCollection).Families.Name \| Set-Content -Encoding UTF8 "$HOME\fonts.txt"` | not yet |

To paste the list instead (`inventory_file_or_paste`), the same commands can copy it to the clipboard:

```sh
fc-list : family | sort | wl-copy                                     # Linux, Wayland
fc-list : family | sort | xclip -selection clipboard                  # Linux, X11
system_profiler SPFontsDataType | grep 'Family:' | sort -u | pbcopy   # macOS
```

```powershell
Add-Type -AssemblyName System.Drawing; (New-Object System.Drawing.Text.InstalledFontCollection).Families.Name | Set-Clipboard
```

- Windows PowerShell 5.1's `-Encoding UTF8` writes a byte-order mark and PowerShell 7's doesn't; the parser takes either, and UTF-16 too.
- The Windows command gives GDI+'s family names, one per line, which the parser reads as a plain list, so the page's summary names no system for it. GDI+'s gaps (below) are for step 1 to check.
- A terminal's scrollback can cut a long pasted list at the top; the page warns when a sorted paste seems to start late, and the file has no such limit.

## Linux (fontconfig)

Measured with fontconfig 2.18.3 on the owner's CachyOS machine (8,672 faces).

| Command | Output | Lines | Size | Time |
|---|---|---|---|---|
| `fc-list : family` | family names, comma-separated | 4,332 | 204 KB | under 0.1 s |
| `fc-list --format '%{[]family{%{family}\n}}' \| sort -u` | one name per line | 4,540 | 124 KB | not measured |
| `fc-list` | path, names and style per face | 8,672 | about 1 MB | not measured |

- `fc-list : family` prints **one line per distinct set of family names**, not one per face: 8,672 faces gave 4,332 lines, already unique. `sort -u` would remove nothing; `sort` only orders the list, which lets the parser spot a list cut off at the top.
- Names on a line follow fontconfig's order: WWS family (name ID 21), typographic family (16), legacy family (1), then localized names, for example `Sarasa UI J,更紗ゴシック UI J`. The first name is matched first; the others are fallbacks.
- `\`, `-`, `:` and `,` are escaped with a backslash (`icomoon\-feather`), according to fontconfig's source.
- A variable font's named instances can carry a misleading legacy name: every Archivo instance here lists `Archivo,Archivo SemiBold`.
- Plain `fc-list` prints file paths, which include the username; `: family` leaves them out.
- Nerd builds drop the spaces from the name (`FiraCode Nerd Font`, `DejaVuSansM Nerd Font`, `Iosevka NF`), and some use a new name (`SauceCodePro`, `BlexMono`, `CaskaydiaCove`, `AtkynsonMono`). All 72 families are listed in Nerd Fonts' `fonts.json` (`unpatchedName` → `patchedName`).
- No clipboard command is on every system (`wl-copy` for Wayland, `xclip -selection clipboard` for X11), and some terminals keep only 1,000 lines of scrollback, fewer than a typical list. So the default writes a file, which the visitor chooses or drops into the page.
- The one-name-per-line form is 40% smaller, but it loses which names belong to the same font, which the Archivo Black rule needs.
- Pitfalls: in WSL, `fc-list` shows only the Linux fonts, so Windows users need the PowerShell command; in a toolbox or distrobox container, it shows the container's fonts; a Flatpak or Snap browser may see a different set.

```sh
fc-list : family | sort > ~/fonts.txt                   # default; then choose or drop the file
fc-list : family | sort | wl-copy                       # Wayland clipboard
fc-list : family | sort | xclip -selection clipboard    # X11 clipboard
```

## macOS

```sh
system_profiler SPFontsDataType | grep 'Family:' | sort -u | pbcopy    # default
osascript -l JavaScript -e 'ObjC.import("AppKit"); ObjC.deepUnwrap($.NSFontManager.sharedFontManager.availableFontFamilies).join("\n")' | pbcopy
system_profiler -json SPFontsDataType | pbcopy
```

- `system_profiler SPFontsDataType` lists every font file with its path (which includes the username) and each typeface's family, full name, style, version, vendor and more. One report measured 13 s and 2.1 MB. `grep 'Family:'` keeps only the family names; here `sort -u` does remove repeats, because each face repeats its family.
- In `-json` form, each font file has `_name`, `path`, `enabled` and `typefaces[]`; in each typeface, `_name` is the PostScript name, alongside `family`, `fullname`, `style` and `enabled`. It is complete, but runs to megabytes, and its paths include the username.
- The same report measured the `osascript` one-liner at 0.46 s. Apple describes `availableFontFamilies` as "the names of the font families available in the system", and it is not deprecated. Hidden system families start with ".".
- macOS 26.4 (March 2026) added a Terminal warning that holds back some commands pasted from a browser, aimed at ClickFix attacks. Testers found it doesn't fire for harmless commands, but Apple hasn't said how it decides. `osascript` is common in ClickFix attacks, so it is the likelier to trip the warning, and it looks alarming.
- `pbcopy` is on every Mac.
- M3-D1's rule: switch to `osascript` only if `system_profiler` takes more than about a minute on a Mac with many fonts and `osascript` trips no warning.

## Windows

Candidates, all to verify in step 1 in Windows PowerShell 5.1 and PowerShell 7:

```powershell
# default: registry value names, all users and current user
Get-Item 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion\Fonts','HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts' -ErrorAction Ignore | ForEach-Object Property | Set-Clipboard
# WPF
Add-Type -AssemblyName PresentationCore; [Windows.Media.Fonts]::SystemFontFamilies | ForEach-Object Source | Set-Clipboard
# GDI+
Add-Type -AssemblyName System.Drawing; (New-Object System.Drawing.Text.InstalledFontCollection).Families | ForEach-Object Name | Set-Clipboard
```

- Per-user installs (Windows 10 1809 and later) go to `%LOCALAPPDATA%\Microsoft\Windows\Fonts` and are registered under `HKCU\Software\Microsoft\Windows NT\CurrentVersion\Fonts`, with absolute paths. "Install for all users" uses `C:\Windows\Fonts` and HKLM, with bare file names. Reading only HKLM, or only `C:\Windows\Fonts`, misses per-user fonts.
- Registry value names are full names (family plus style) with a suffix such as ` (TrueType)` or ` (OpenType)`. Collections join their names with ` & ` (`Cambria & Cambria Math (TrueType)`). Installers write these names, so fonts installed by scripts can have odd ones. The default command reads only the value names, so it carries no paths or usernames.
- WPF `SystemFontFamilies` reads "the default system font location" and is reported to miss per-user fonts. According to Microsoft's docs, GDI+ supports OpenType only in part, and `.otf` fonts are reported missing. Neither is verified.
- In Windows PowerShell 5.1, `>` writes UTF-16 files; PowerShell 7 writes UTF-8. The parser accepts both byte-order marks.

## The Chromium button (`queryLocalFonts()`)

- **Support** (MDN's compatibility data, 2026-09-25): Chrome 103 and later on desktop, and Edge and Opera, which follow Chrome. Not Chrome on Android, Firefox or Safari. The spec is a WICG draft of 7 June 2024.
- **Requirements:** a secure context, a click (transient activation), and the `local-fonts` permission, whose Permissions-Policy default is `self`. Milestone 2's site header turns `local-fonts` off, so the check's path allows it for `self` (Milestone 3 step 6).
- **Errors.** Both fall back to paste.
  - `SecurityError`: a Permissions-Policy header blocks the feature, or there was no click.
  - `NotAllowedError`: the permission is denied, by the visitor or by their organisation. Chrome's enterprise policies `DefaultLocalFontsSetting` and `LocalFontsBlockedForUrls` most likely resolve as a denial without a prompt; step 7 records which error they actually give.
- **Result:** one `FontData` per face (`family`, `fullName`, `postscriptName`, `style`), sorted by PostScript name. The spec says `family` is name ID 1, but Chromium's source takes it from the platform: fontconfig's first family on Linux (TrueType and CFF fonts only), CoreText's family on macOS, and DirectWrite's family on Windows. Chromium drops faces whose PostScript name repeats.
- On Windows, DirectWrite's system collection includes per-user fonts and fonts loaded by font services (Microsoft's docs), so the button can see fonts the registry command misses.
- The check calls it without a `postscriptNames` filter, because our table can't list every build's PostScript name, and never calls `blob()`, so no font data is read.
- Brave randomizes font lists against fingerprinting. Whether that affects `queryLocalFonts()` is untested.
- The check never probes for fonts (`document.fonts.check`, canvas measuring). Browsers hide user-installed fonts from probing on purpose, and it is a fingerprinting technique.

## What this means for matching

- **Linux and macOS lists** give family names, so they match directly. On a Linux line, the first name outranks the legacy names after it.
- **The Windows registry** gives full names, so matching needs style stripping and the ambiguous rule (Archivo Black).
- **The button** gives all three names. The family comes first, and the PostScript name settles the ambiguous cases.

## Cloudflare

Cloudflare's settings and the site's headers belong to [Milestone 2](milestone-2.md) (step 9); the check's privacy test re-verifies them after each deploy. Seen on trulyfreefonts.com on 2026-09-25: plain `curl` got the stub unchanged, but a request sending a browser's `Accept: text/html` header got the Web Analytics beacon injected (`static.cloudflareinsights.com`), so checks must send that header. Cloudflare's Network Error Logging (`NEL` and `Report-To` headers, which ask browsers to report connection failures to `a.nel.cloudflare.com`) was on; it was turned off on all three zones the same day ([ops/SERVER.md](../ops/SERVER.md) section F). Free-plan zones have had the Web Analytics beacon on by default since October 2025, and Bot Fight Mode injects a script under `/cdn-cgi/challenge-platform/`; Milestone 2 step 9 turns both off.

## Open questions for step 1

**Linux** (2 or more distributions, one with an older fontconfig such as Debian 12 or Ubuntu 22.04):
- `fc-list : family` escaping and name order;
- the misleading legacy names on variable fonts' named instances;
- whether Google's static Archivo files give their Black weight the legacy family name "Archivo Black";
- the file route, the clipboard route (`wl-copy`, `xclip`), and copying a long list out of a terminal with 1,000 lines of scrollback;
- whether a Flatpak or Snap Chromium sees the same fonts as `fc-list` on the host (for step 7).

**macOS** (the current release and the one before, including a Mac with many fonts):
- the time and size of each candidate;
- whether pasting each one from a browser trips the Terminal paste warning;
- fonts disabled in Font Book, fonts activated by Adobe Creative Cloud or a font manager, and hidden system families.

**Windows** (Windows 11 and Windows 10 22H2, in Windows PowerShell 5.1 and PowerShell 7):
- which candidate sees each of these: a font installed for all users; one installed for the current user only; an `.otf` (CFF) font; a variable font; a `.ttc` collection; a font activated by a font manager;
- `Set-Clipboard` with non-Latin names;
- the UTF-16 file that `>` writes in PowerShell 5.1.

## Results

Step 1 fills in one row per command and OS; step 7 adds the browser rows.

| OS or browser | Command or API | Name returned | Lines | Size | Time | Pitfalls | Sample |
|---|---|---|---|---|---|---|---|
| Linux (CachyOS, fontconfig 2.18.3) | `fc-list : family` | family: WWS, typographic, legacy, localized | 4,332 | 204 KB | under 0.1 s | see Linux | pending |
| Linux (older fontconfig) | | | | | | | pending |
| macOS (current and previous) | | | | | | | pending |
| Windows 11 and 10 22H2 | | | | | | | pending |
| Brave | `queryLocalFonts()` | | | | | | pending |
| Flatpak or Snap Chromium | `queryLocalFonts()` | | | | | | pending |
| Chrome with `LocalFontsBlockedForUrls` | `queryLocalFonts()` | error given: pending | | | | | |

## Sources

- Local Font Access, WICG draft of 7 June 2024: https://wicg.github.io/local-font-access/
- MDN, `Window.queryLocalFonts()` and its browser-compatibility data: https://developer.mozilla.org/en-US/docs/Web/API/Window/queryLocalFonts
- fontconfig's source, for `fc-list` escaping and name order; Chromium's source, for how it fills `family`.
- Apple's documentation for `NSFontManager.availableFontFamilies`.
- Microsoft's documentation on per-user font installation, DirectWrite's system font collection, WPF `Fonts.SystemFontFamilies` and GDI+ font support.
- Nerd Fonts' `fonts.json`.
