# Platforms: status and app requirements

Windows and Linux now build from the same code on `main` and share one test suite (CI runs it on both). Linux can already run the same interface as Windows, history panel and Stack included (`NEXUS_UI=shared`); the classic Linux panel stays the default until that is validated on real Wayland desktops. macOS is still the separate Swift original. See [ROADMAP_1.0.md](../ROADMAP_1.0.md).

| | Windows | Linux | macOS |
|---|---|---|---|
| Code | `main`, Python + Qt 6 | `main` (same code; `nexus.platform.linux.app`), Python + Qt 6 | Original Swift/SwiftUI app, not yet in this repository |
| Status | Main build, released | Early build, Wayland (Hyprland/KDE) | Private original; to be reviewed and published |
| Interface language | English / Spanish (automatic) | English / Spanish with the shared interface; Spanish in the classic panel | Spanish |
| Installer | `Nexus-Setup-<version>.exe` | Not yet | Not yet |

Legend below: ✅ done · 🟡 partial · ❌ missing.

## Windows

| Requirement | Status | Notes |
|---|---|---|
| Native installer with uninstaller | ✅ | Inno Setup 6 (`packaging/windows`). Registers in *Settings › Apps* with publisher, version and icon. |
| Install scope: current user or all users | ✅ | First page asks. "Only for me": no administrator rights, `%LOCALAPPDATA%\Programs\Nexus`. "All users": UAC, `Program Files\Nexus`. |
| User data outside the install folder | ✅ | `%LOCALAPPDATA%\Nexus` per user; kept on upgrade; the uninstaller asks before deleting it. |
| Upgrade in place | ✅ | Same `AppId`; a running Nexus is detected and closed first. |
| File version resource (Explorer › Details) | ✅ | Product, company, version, copyright. |
| Start menu entry with AppUserModelID | ✅ | Notifications and taskbar group under Nexus. |
| Start with Windows: per user, off by default | ✅ | Settings › Startup (`HKCU\...\Run`); removed by the uninstaller. |
| High DPI, light/dark, reduced motion | ✅ | Qt 6 per-monitor DPI; Liquid Glass follows the system theme; respects animation settings. |
| Code signing (Authenticode) | ❌ | Unsigned: SmartScreen shows "Windows protected your PC" until the file builds reputation. Free option for open source: SignPath Foundation; paid: a code-signing certificate or Azure Trusted Signing. |
| winget package | ❌ | Possible once releases are signed or have some reputation (manifest points at the release asset). |
| Microsoft Store (MSIX) | ❌ | Needs an MSIX package and a Partner Center account. |
| Automatic updates | ❌ | Today: download the new installer; settings and history are kept. |

## Linux

| Requirement | Status | Notes |
|---|---|---|
| `.desktop` entry and icon | 🟡 | `resources/nexus-linux.desktop` and PNG icons exist; not yet installed to the XDG `hicolor` theme sizes. |
| AppStream metainfo (`io.github.manuelcarvajal09.Nexus.metainfo.xml`) | ❌ | Needed by software centers and Flathub. |
| Package | ❌ | Recommended: Flatpak (Flathub) for distribution, AppImage as a single-file download. |
| Install scope | ❌ | Flatpak covers both: `flatpak install --user` (per user, `~/.local/share/flatpak`) or `--system` (all users, needs admin). AppImage is always per user. |
| XDG base directories | ✅ | Data in `$XDG_DATA_HOME`, config in `$XDG_CONFIG_HOME`. |
| Wayland clipboard and paste | 🟡 | Works on wlroots/KDE with `wl-clipboard` and a paste helper; GNOME restricts global shortcuts and synthetic input (portal-based work needed). |

## macOS

| Requirement | Status | Notes |
|---|---|---|
| App bundle (`Nexus.app`) | 🟡 | The Swift original builds one; not in this repository yet. |
| Code signing + notarization | ❌ | Requires an Apple Developer account (paid). Without it Gatekeeper blocks the first launch. |
| Hardened Runtime and privacy strings | ❌ | Synthetic paste needs the **Accessibility** permission (`AXIsProcessTrusted`); the app must explain why. |
| Install scope: current user or all users | ❌ | A signed `.pkg` built with `productbuild` and `<domains enable_currentUserHome="true" enable_localSystem="true"/>` makes macOS Installer ask "Install for me only" (`~/Applications`) or "Install for all users of this computer" (`/Applications`). A `.dmg` is drag-to-install only. |
| Universal binary (Apple silicon + Intel) | ❌ | `xcodebuild -arch arm64 -arch x86_64`. |
| Updates | ❌ | Sparkle is the usual choice outside the App Store. |

See [MACOS.md](../MACOS.md) for the step-by-step handoff to bring the macOS build into this repository.
