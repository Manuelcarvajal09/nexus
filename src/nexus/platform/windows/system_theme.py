"""Windows light/dark app preference, from the native source.

Windows stores it in HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\
Personalize\\AppsUseLightTheme (1 = light, 0 = dark) and announces a change by
broadcasting WM_SETTINGCHANGE with lParam "ImmersiveColorSet". The preference is
only ever READ here; nothing in the registry is written.

`set_override` exists for tests and the isolated probe tools: it is never set by
the application itself, so a real run always follows Windows.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QObject, QTimer, Signal

PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
WM_SETTINGCHANGE = 0x001A
_THEME_PARAMETERS = {"ImmersiveColorSet", "WindowsThemeElement"}
_override: str | None = None
_cached: bool | None = None   # palette() runs on every paint: read the registry once, not per call


def set_override(value: str | None) -> None:
    global _override
    if value not in (None, "dark", "light"):
        raise ValueError("override must be None, 'dark' or 'light'")
    _override = value


def override() -> str | None:
    return _override


def system_prefers_light(registry=None) -> bool:
    """True when Windows apps use the light theme. Unknown/missing means dark
    (the safe default for a glass panel)."""
    global _cached
    if _override is not None:
        return _override == "light"
    if registry is None:
        if _cached is not None:
            return _cached
        if sys.platform != "win32":
            return False
        import winreg as registry
        remember = True
    else:
        remember = False
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, PERSONALIZE, 0, registry.KEY_READ) as key:
            value, kind = registry.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return False
    if kind != registry.REG_DWORD or value not in (0, 1):
        return False
    if remember:
        _cached = value == 1
    return value == 1


def invalidate_cache() -> None:
    global _cached
    _cached = None


def is_theme_message(message: int, parameter) -> bool:
    return message == WM_SETTINGCHANGE and parameter in _THEME_PARAMETERS


def parameter_of(l_param: int):
    """The string a WM_SETTINGCHANGE carries in lParam (or None)."""
    if not l_param:
        return None
    try:
        return ctypes.wstring_at(l_param)
    except (OSError, ValueError):
        return None


class SystemThemeWatcher(QObject):
    """Emits `changed(is_light)` when the Windows app theme really changes.

    Two triggers, one debounced re-read: Qt's colorSchemeChanged and the native
    WM_SETTINGCHANGE (call `notify()` from a native event filter). Windows sends
    several messages per switch; the debounce collapses them into one signal."""

    changed = Signal(bool)

    def __init__(self, parent=None, debounce_ms: int = 150):
        super().__init__(parent)
        self._light = system_prefers_light()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._check)
        try:
            from PySide6.QtGui import QGuiApplication
            hints = QGuiApplication.styleHints()
            hints.colorSchemeChanged.connect(lambda *_: self.notify())
        except (AttributeError, RuntimeError):
            pass

    @property
    def light(self) -> bool:
        return self._light

    def notify(self) -> None:
        self._timer.start()

    def _check(self) -> None:
        invalidate_cache()   # Windows said something changed: read it again
        light = system_prefers_light()
        if light != self._light:
            self._light = light
            self.changed.emit(light)

    def close(self) -> None:
        self._timer.stop()
