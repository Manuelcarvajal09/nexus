# Road to Nexus 1.0.0

What "1.0" should mean for Nexus: **someone who has never met the author can install it on any of the three systems, use it every day without surprises, update it, and trust it with their clipboard.** Version 0.4.0 meets that bar on Windows for the core features, but not yet for trust (signing), updates, or the other two systems.

## Where 0.4.0 stands

| Area | State |
|---|---|
| Core features (history, search, pinboards, titles, Stack, themes) | Solid on Windows; 1,360+ automated tests |
| Reliability fixes from the 2026-11 audit | Done; validated with tests and a real-clipboard probe, not yet with weeks of real use |
| Interface language | English and Spanish (Windows) |
| Windows installer | Done: per-user or all-users, clean uninstall |
| Linux | Early build on its own branch, Spanish only, no package |
| macOS | Original Swift app, outside the repository |
| Signing, updates, CI | Missing |

## Must have for 1.0 (blockers)

1. **Signed Windows builds.** Unsigned installers trigger SmartScreen warnings, the main reason people abandon a download. Apply to the SignPath Foundation (free code signing for open-source projects), or buy a certificate or Azure Trusted Signing.
2. **Real-world validation.** Two to four weeks of daily use plus a scripted native test of the risky paths: paste into Office/Chrome/Electron/terminal, the Stack across Win+L and sleep, apps running as administrator, 4K screenshots, two monitors at different DPI. Track any `paste.finish`, `capture.dropped` or `paste_watcher.*` warnings in the log.
3. **Update path.** At minimum, an "update available" notice that checks the GitHub releases page once a day (opt-out, no data sent beyond the request). Better: winget, or an in-app updater that downloads and runs the signed installer.
4. **Continuous integration.** Run the test suite on every push and build the installer on every tag (GitHub Actions, `windows-latest`). Releases then come from CI, not from a personal PC.
5. **One codebase, three platforms** (see below). 1.0 should not mean "1.0 on Windows, beta elsewhere": either all three ship from this repository, or the README says plainly which systems are 1.0.
6. **Settings that exist today only in `settings.json`**: excluded apps and history retention need a place in Settings.

## Should have

- **First-run guide**: three steps (shortcut, paste, Stack) on first launch, dismissible.
- **Backup and restore from Settings**: export/import the database (the verified backup code already exists in `tools/windows_db_tools.py`).
- **Diagnostics button**: "Copy diagnostic info" (version, OS, last 200 log lines, never clip content) for bug reports.
- **Accessibility pass**: screen-reader names on every control, Windows high-contrast themes, full keyboard path through pinboard management.
- **Large-history test**: 50,000 clips with images; keep open < 50 ms and search < 10 ms.
- **CHANGELOG.md** and semantic versioning from here on.
- **Store presence**: winget first, then Microsoft Store (MSIX), Flathub, and a notarized macOS `.pkg`.

## Nice to have

- Sync between devices (end-to-end encrypted, opt-in).
- Snippets with placeholders (`{date}`, `{clipboard}`), plain-text transforms (trim, change case).
- Per-app rules (always plain text in terminals).
- Encryption at rest for the database (Windows DPAPI / macOS Keychain / libsecret).

## Unifying the three systems

Today Windows and Linux share history, storage and preview code, but each has its own windows, styles and platform code, and they live on different branches; macOS is a separate Swift app. Recommendation: **one Python + Qt 6 codebase on `main`**, organized as:

```
src/nexus/
  core/        storage, search, previews, i18n, models, Stack logic   (no Qt widgets, no OS calls)
  ui/          the panel, cards, Stack, settings, themes              (Qt widgets, shared by all)
  platform/
    windows/   clipboard events, paste (SendInput), hotkeys, autostart, Acrylic
    linux/     wl-clipboard/X11, paste helper or portals, hotkeys, autostart (.desktop)
    macos/     NSPasteboard, CGEvent paste (Accessibility), hotkeys, login item, vibrancy
```

Progress: **step 1 is done** (Linux merged onto `main`, one test suite on both systems, Windows modules importable anywhere) and **step 3's interface half is done for Linux**: `NEXUS_UI=shared` runs the Windows panel, Settings, themes and languages on Linux with Linux services (XDG autostart, desktop-owned global shortcuts, Linux capture and preview preferences). Left: make it the Linux default after testing on Hyprland/KDE/GNOME, move the Stack onto the shared window with a Wayland delivery adapter, delete the classic Linux panel, and the `core/ui/platform` reorganisation.

Steps, each one shippable on its own:

1. Move the Linux branch onto `main` behind the `platform/linux` adapter; delete duplicated UI modules in favour of the Windows ones, which are the most polished (themes, cards, Settings, i18n).
2. Extract `core/` and `ui/` from the current `windows_*` modules (mostly renames plus small interfaces such as `ClipboardSource`, `PasteTarget`, `HotkeyService`, `Autostart`).
3. Write `platform/macos` with PySide6 (pasteboard change count polling, `CGEventPost` for ⌘V after an Accessibility check, Carbon hotkeys via `pyobjc`), package with PyInstaller as a `.app`, and ship a `.pkg` with the per-user/all-users choice.
4. Keep the Swift app as the design reference until the Qt macOS build matches it, then retire it (or keep it as a separate native edition if it remains clearly better).

Why not three native apps: three times the features to keep in sync, which is exactly where the current Windows/Linux drift came from.

## Suggested sequence

0.5 Settings for exclusions and retention, CI, update notice, first-run guide → 0.6 unified codebase with Linux on `main` (Flatpak/AppImage) → 0.7 macOS on the shared codebase (signed `.pkg`) → 0.8–0.9 signing on Windows, accessibility, large-history performance, real-world beta → **1.0** when all blockers above are closed.
