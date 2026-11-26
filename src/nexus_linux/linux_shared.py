"""The shared Nexus interface (history panel, pinboards, Settings, themes,
languages) composed with the Linux services.

This is unification phase 2: Linux runs the same panel as Windows instead of
its own TimelineWindow. Opt in with NEXUS_UI=shared (or "ui": "shared" in
settings.json) until it has been validated on real Wayland desktops; the
classic Linux panel stays the default meanwhile. The Stack keeps the Linux
delivery model (dispatch + confirmation), because Wayland cannot verify a
synthetic paste the way Windows can.
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from . import i18n, settings as linux_settings, windows_design
from .i18n import tr
from .windows_app import SettingsBackend
from .windows_settings import load_settings as load_shared_settings
from .windows_shortcuts import Result, ShortcutStore
from .windows_timeline import WindowsTimelineWindow


def autostart_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "nexus.desktop"


def autostart_command() -> str:
    return shlex.join([sys.executable, "-m", "nexus_linux.app"])


def wants_shared_ui(settings: dict | None = None) -> bool:
    if os.environ.get("NEXUS_UI") in ("shared", "classic"):
        return os.environ["NEXUS_UI"] == "shared"
    return (settings if settings is not None else load_shared_settings()).get("ui") == "shared"


class DesktopHotkeys:
    """On Linux the global shortcuts belong to the desktop (Hyprland binds,
    KDE kglobalaccel, GNOME custom shortcuts): Nexus shows them but does not
    rebind them itself."""

    failures: dict = {}
    notices: list = []

    def change(self, name, text):
        return Result(False, tr("En Linux este atajo global lo gestiona tu escritorio (Hyprland, KDE o GNOME): cámbialo allí."))

    def suspend(self):
        pass

    def resume(self):
        pass


class _Unsupported:
    """Makes hasattr(backend, name) false, so Settings hides the option."""

    def __get__(self, obj, objtype=None):
        raise AttributeError("not available on this platform")


class LinuxSettingsBackend(SettingsBackend):
    glass_optical = _Unsupported()       # DWM optical glass is Windows-only
    set_glass_optical = _Unsupported()

    def __init__(self, settings, timeline, monitor, previews=()):
        super().__init__(settings, ShortcutStore(settings), DesktopHotkeys(), timeline, [])
        self._monitor = monitor
        self.previews = tuple(previews)

    # capture: the Linux preference (capture_enabled, behind the live validation gate)
    def capture_paused(self):
        return not linux_settings.load_settings().capture_enabled

    def set_capture_paused(self, paused):
        current = linux_settings.load_settings()
        linux_settings.save_capture_preferences(not paused, list(current.excluded_patterns))
        if self._monitor is not None:
            self._monitor.configure(not paused)
        for callback in list(self._capture_listeners):
            callback(self.capture_paused())

    # link previews: the Linux key is network_previews (off by default there)
    def link_previews(self):
        return linux_settings.load_settings().network_previews

    def set_link_previews(self, enabled):
        enabled = bool(enabled)
        self.settings["network_previews"] = self.settings["link_previews"] = enabled
        self._save()
        for preview in self.previews:
            preview.network_enabled = enabled
            if hasattr(preview, "_allow_network"):
                preview._allow_network = enabled

    # start with the session: an XDG autostart entry, per user
    def autostart_enabled(self):
        return autostart_file().is_file()

    def set_autostart(self, enabled, registry=None):
        path = autostart_file()
        try:
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "[Desktop Entry]\nType=Application\nName=Nexus\n"
                    f"Comment={tr('Gestor de portapapeles')}\nExec={autostart_command()}\n"
                    "Icon=nexus\nTerminal=false\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
            elif path.is_file():
                path.unlink()
        except OSError:
            return tr("No se pudo cambiar el inicio con la sesión. Inténtalo de nuevo.")
        return ""

    def restart(self):
        from .app import restart_application
        restart_application()


def build_shared_panel(storage, paste_service, preview_service, monitor, apply_theme):
    """The shared history panel with its Linux Settings backend."""
    timeline = WindowsTimelineWindow(storage, paste_service, preview_service)
    backend = LinuxSettingsBackend(load_shared_settings(), timeline, monitor, (preview_service,))
    timeline.configure_settings(backend)
    timeline.theme_requested.connect(apply_theme)
    return timeline, backend


def shared_stylesheet() -> str:
    return windows_design.qss()


__all__ = ["build_shared_panel", "wants_shared_ui", "shared_stylesheet", "LinuxSettingsBackend", "i18n"]
