"""Opt-in documented DWM backdrop; independent of windows_platform.

Contract: apply(hwnd, request, prefs) returns an API observation, NOT a visual
acceptance. Supply preferences() and a probe-approved can_native_paint=True
only after testing the actual Qt surface configuration. No Qt painting flags,
opacity, global preferences, activation or visibility are changed here.

Controller integration: one instance per QWidget; forward relevant native
messages from the owner's nativeEvent to native_settings_changed(message).
There is deliberately no process-wide native filter, hook or polling.
"""
from __future__ import annotations

import ctypes
import dataclasses
import sys
from dataclasses import dataclass, field
from typing import Literal

from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtGui import QGuiApplication, QPlatformSurfaceEvent

Request = Literal["desktop_acrylic", "satin"]
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE, DWMSBT_TRANSIENTWINDOW = 1, 3
DWMWA_NCRENDERING_POLICY = 2
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_EX_LAYERED = 0x00020000, 0x00010000, 0x00080000
WS_POPUP = 0x80000000
GWL_STYLE = -16


def hresult_from_win32(error):
    return signed_hresult(0x80070000 | (int(error) & 0xFFFF)) if error else 0


def signed_hresult(value: int) -> int:
    """HRESULT is signed LONG (32 bits even on a 64-bit host)."""
    return ctypes.c_int32(getattr(value, "value", value)).value


@dataclass(frozen=True)
class BackdropPreferences:
    os_build: int | None = None
    composition: bool | None = None
    high_contrast: bool | None = None
    transparency: bool | None = None
    power_saver: bool | None = None
    remote_session: bool | None = None
    can_native_paint: bool = False  # external surface probe, not an OS preference
    # Acrylic follows the window dark/light attribute: dark = DWMWA_USE_IMMERSIVE_DARK_MODE on.
    dark_mode: bool = True
    border_color: int = 0x635346      # COLORREF of the single native contour


@dataclass(frozen=True)
class BackdropState:
    requested: Request
    applied: Literal["desktop_acrylic", "satin"]
    reason: str
    hwnd: int
    hresults: dict[str, int] = field(default_factory=dict)
    readback: int | None = None
    native_window_style: dict[str, int | bool] = field(default_factory=dict)	
    native_enabled: bool = False
    verified_visual: bool = False


class MARGINS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "right", "top", "bottom")]


class HIGHCONTRAST(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                ("lpszDefaultScheme", ctypes.c_void_p)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ubyte) for name in
                ("ACLineStatus", "BatteryFlag", "BatteryLifePercent", "SystemStatusFlag")] + [
                ("BatteryLifeTime", ctypes.c_uint32), ("BatteryFullLifeTime", ctypes.c_uint32)]


class CtypesBackdropAPI:
    """Lazy Windows-only wrappers. Construction and imports do not change OS state."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("windows_required")
        from ctypes import wintypes
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name in ("DwmSetWindowAttribute", "DwmGetWindowAttribute"):
            fn = getattr(self.dwm, name)
            fn.argtypes = [wintypes.HWND, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
            fn.restype = ctypes.c_long  # Windows LONG / signed HRESULT
        self.dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
        self.dwm.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
        self.dwm.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
        self.dwm.DwmIsCompositionEnabled.restype = ctypes.c_long
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.user.IsWindow.restype = wintypes.BOOL
        self.user.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        self.user.SystemParametersInfoW.restype = wintypes.BOOL
        self.user.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user.GetSystemMetrics.restype = ctypes.c_int
        self.kernel.GetSystemPowerStatus.argtypes = [ctypes.POINTER(SYSTEM_POWER_STATUS)]
        self.kernel.GetSystemPowerStatus.restype = wintypes.BOOL
        self._style = getattr(self.user, "GetWindowLongPtrW", self.user.GetWindowLongW)
        self._style.argtypes = [wintypes.HWND, ctypes.c_int]
        self._style.restype = ctypes.c_ssize_t
        self._set_style = getattr(self.user, "SetWindowLongPtrW", self.user.SetWindowLongW)
        self._set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self._set_style.restype = ctypes.c_ssize_t
        self.user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        self.user.SetWindowPos.restype = wintypes.BOOL

    def is_window(self, hwnd):
        return bool(self.user.IsWindow(hwnd))

    def window_style(self, hwnd):
        ctypes.set_last_error(0)
        style = self._style(hwnd, -16)
        if not style and ctypes.get_last_error():
            raise OSError("style_unavailable")
        ctypes.set_last_error(0)
        extended = self._style(hwnd, -20)
        if not extended and ctypes.get_last_error():
            raise OSError("style_unavailable")
        return {"style": style & 0xFFFFFFFF, "ex_style": extended & 0xFFFFFFFF,
                "layered": bool(extended & 0x80000)}

    def prepare_frameless(self, hwnd):
        """Retain DWM's documented WS_CAPTION requirement without system chrome.

        The owning QWidget handles WM_NCCALCSIZE (return 0) to expand its Qt
        client into this retained caption region. No focus, geometry, Z order or
        visibility change is requested by SetWindowPos.
        """
        current = int(self._style(hwnd, -16)) & 0xFFFFFFFF
        # The controlled DWM comparison showed the successful Qt profile keeps
        # WS_POPUP. Clearing it produces a grey DWM fallback despite HRESULT=0
        # and readback=3. Preserve the original top-level type; restore only
        # the caption bit DWM needs and remove system controls/thick resize.
        desired = ((current | WS_CAPTION)
                   & ~(WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX))
        if desired != current:
            ctypes.set_last_error(0)
            previous = self._set_style(hwnd, -16, desired)
            error = ctypes.get_last_error()
            if not previous and error:
                return hresult_from_win32(error)
            flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOACTIVATE|FRAMECHANGED
            if not self.user.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags):
                return hresult_from_win32(ctypes.get_last_error())
        return 0

    def set_attribute(self, hwnd, attribute, value):
        data = ctypes.c_uint32(value)
        return self.dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))

    def get_attribute(self, hwnd, attribute):
        data = ctypes.c_uint32()
        hr = self.dwm.DwmGetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))
        return hr, data.value

    def extend_frame(self, hwnd, margin):
        data = MARGINS(margin, margin, margin, margin)
        return self.dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(data))

    @staticmethod
    def _transparency():
        import winreg
        values = []
        for path in (r"Software\Microsoft\Windows\DWM",
                     r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
                    value, kind = winreg.QueryValueEx(key, "EnableTransparency")
                if kind != winreg.REG_DWORD or value not in (0, 1):
                    return None
                values.append(bool(value))
            except FileNotFoundError:
                continue  # not present in every Windows version/location
            except OSError:
                return None
        return all(values) if values else None

    def preferences(self, *, can_native_paint=False):
        from ctypes import wintypes
        composition = wintypes.BOOL()
        hr = self.dwm.DwmIsCompositionEnabled(ctypes.byref(composition))
        contrast = HIGHCONTRAST(cbSize=ctypes.sizeof(HIGHCONTRAST))
        contrast_ok = self.user.SystemParametersInfoW(0x42, contrast.cbSize, ctypes.byref(contrast), 0)
        power = SYSTEM_POWER_STATUS()
        power_ok = self.kernel.GetSystemPowerStatus(ctypes.byref(power))
        return BackdropPreferences(
            os_build=sys.getwindowsversion().build,
            composition=bool(composition.value) if signed_hresult(hr) >= 0 else None,
            high_contrast=bool(contrast.dwFlags & 1) if contrast_ok else None,
            transparency=self._transparency(),
            power_saver=bool(power.SystemStatusFlag) if power_ok and power.SystemStatusFlag in (0, 1) else None,
            remote_session=bool(self.user.GetSystemMetrics(0x1000)),
            can_native_paint=can_native_paint,
        )


class NativeBackend:
    """Injected API implements is_window, window_style, set/get_attribute, extend_frame.

        The QWidget owner must preserve documented WS_CAPTION, handle
        WM_NCCALCSIZE, and have WA_TranslucentBackground=False. Every fallback
        attempts NONE + zero margins on a valid HWND; their HRESULTs
    remain observable, so satin is a painting decision, not a claimed reset ACK.
    Unknown preferences fail closed. Animation preferences are intentionally absent.
    """

    def __init__(self, api=None):
        self.api = api if api is not None else CtypesBackdropAPI()

    def apply(self, hwnd: int, request: Request, prefs: BackdropPreferences) -> BackdropState:
        if request not in ("desktop_acrylic", "satin"):
            raise ValueError("invalid_backdrop_request")
        results, styles = {}, {}
        readback = None

        def record(name, call):
            try:
                results[name] = signed_hresult(call())
                return results[name] >= 0
            except Exception:
                return False  # never retain private exception strings

        def fallback(reason, reset=True):
            if reset:
                record("reset_dark", lambda: self.api.set_attribute(hwnd, 20, 0))
                record("reset_border", lambda: self.api.set_attribute(hwnd, 34, 0xFFFFFFFF))
                record("reset_backdrop", lambda: self.api.set_attribute(hwnd, 38, 1))
                record("reset_frame", lambda: self.api.extend_frame(hwnd, 0))
            return BackdropState(request, "satin", reason, hwnd, results, readback, styles, False)

        try:
            if not hwnd or not self.api.is_window(hwnd):
                return fallback("invalid_window", False)
        except Exception:
            return fallback("window_validation_unavailable", False)
        try:
            styles.update(self.api.window_style(hwnd))
        except Exception:
            return fallback("window_diagnostics_unavailable")
        prepare = getattr(self.api, "prepare_frameless", None)
        if prepare is not None:
            try:
                results["frameless_style"] = signed_hresult(prepare(hwnd))
                if results["frameless_style"] < 0:
                    return fallback("frameless_style_failed")
                styles.update(self.api.window_style(hwnd))
            except Exception:
                return fallback("frameless_style_failed")
        if styles.get("layered"):
            return fallback("layered_surface_incompatible")
        if request == "satin":
            return fallback("satin_requested")
        if prefs.os_build is None or prefs.os_build < 22621:
            return fallback("unsupported_build")
        for name, expected in (("composition", True), ("high_contrast", False),
                               ("transparency", True), ("power_saver", False), ("remote_session", False)):
            value = getattr(prefs, name)
            if value is None:
                return fallback(f"{name}_unknown")
            if value is not expected:
                return fallback(f"{name}_incompatible")
        if not prefs.can_native_paint:
            return fallback("surface_not_probed")
        # Layered composition was empirically opaque on the verified Qt profile.
        # DWMWA_WINDOW_CORNER_PREFERENCE must remain untouched: a real-screen
        # A/B probe showed even explicit DEFAULT/DONOTROUND suppresses or
        # neutralizes Desktop Acrylic on this Qt/DWM profile. A real native
        # COLORREF border completes the single silhouette (#465363).
        import os as _os
        border = 0xFFFFFFFE if _os.environ.get("NEXUS_SINGLE_CONTOUR", "1") == "0" else prefs.border_color
        for attr, value, name in ((20, 1 if prefs.dark_mode else 0, "dark"),
                                  (34, border, "border"), (38, 3, "backdrop")):
            if not record(name, lambda a=attr, v=value: self.api.set_attribute(hwnd, a, v)):
                return fallback(f"{name}_failed")
        if not record("frame", lambda: self.api.extend_frame(hwnd, -1)):
            return fallback("frame_failed")
        try:
            hr, value = self.api.get_attribute(hwnd, 38)
            results["readback"] = signed_hresult(hr)
            if results["readback"] < 0:
                return fallback("readback_failed")
            readback = value
        except Exception:
            return fallback("readback_failed")
        if readback != 3:
            return fallback("readback_mismatch")
        # Native DWM is enabled, but this runtime has not captured/analyzed its
        # own pixels. The external validation harness records that separately.
        return BackdropState(request, "desktop_acrylic",
                             "api_ack_visual_capture_required", hwnd, results, readback, styles,
                             native_enabled=True, verified_visual=False)


class BackdropController(QObject):
    """One queued per-window subscription. Callbacks never imply visual approval.

    can_native_paint(window) defaults false until integration's surface probe.
    on_state(state) owns optional surface updates; this class never changes paint.
    native_settings_changed is explicitly forwarded by the owning nativeEvent.
    """
    _PROPERTY = "_nexus_documented_backdrop_controller"

    def __init__(self, window, *, backend=None, request="desktop_acrylic",
                 can_native_paint=lambda window: False, on_state=lambda state: None,
                 variant=lambda: (True, 0x635346)):
        if request not in ("desktop_acrylic", "satin"):
            raise ValueError("invalid_backdrop_request")
        if window.property(self._PROPERTY):
            raise ValueError("backdrop_controller_already_installed")
        super().__init__(window)
        self.window, self.backend, self.request = window, backend, request
        self.can_native_paint, self.on_state = can_native_paint, on_state
        # variant() -> (dark_mode, border COLORREF): read again on every apply, so a
        # Windows light/dark switch re-tints the Acrylic of the same HWND.
        self.variant = variant
        self._closed = False
        self._applying = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._apply)
        self._enabled = sys.platform == "win32"
        window.setProperty(self._PROPERTY, True)
        if self._enabled:
            window.installEventFilter(self)

    def set_request(self, request):
        if request not in ("desktop_acrylic", "satin"):
            raise ValueError("invalid_backdrop_request")
        if request != self.request:
            self.request = request
            self.schedule()

    def close(self):
        self._closed = True
        self._timer.stop()
        if self.window is not None:
            self.window.removeEventFilter(self)
            self.window.setProperty(self._PROPERTY, False)

    def schedule(self):
        if self._enabled and not self._closed and not self._applying and not self._timer.isActive():
            self._timer.start(0)

    def native_settings_changed(self, message):
        if message in (0x001A, 0x031A, 0x031E, 0x02E0, 0x0218):
            self.schedule()  # settings, theme, composition, DPI, power

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.Close, QEvent.Type.DeferredDelete):
            self._closed = True
            self._timer.stop()
        elif kind == QEvent.Type.PlatformSurface:
            if event.surfaceEventType() == QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed:
                self._timer.stop()
            else:
                self.schedule()
        elif kind in (QEvent.Type.Show, QEvent.Type.WinIdChange, QEvent.Type.ThemeChange):
            if kind == QEvent.Type.Show:
                self._closed = False
            self.schedule()
        return False

    def _apply(self):
        if self._closed or not self.window.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            return
        # Verified on PySide6 6.12: internalWinId exists and does NOT create an HWND.
        hwnd = int(self.window.internalWinId())
        if not hwnd:
            return
        self._applying = True
        try:
            try:
                if self.backend is None:
                    self.backend = NativeBackend()
                try:
                    prefs = self.backend.api.preferences(can_native_paint=bool(self.can_native_paint(self.window)))
                    dark, border = self.variant()
                    prefs = dataclasses.replace(prefs, dark_mode=bool(dark), border_color=int(border))
                except Exception:
                    # A preference-read exception must not leave prior material
                    # active. Unknown prefs conservatively reset this HWND only.
                    prefs = BackdropPreferences()
                state = self.backend.apply(hwnd, self.request, prefs)
            except Exception:
                state = BackdropState(self.request, "satin", "preferences_or_backend_unavailable", hwnd)
            self.on_state(state)
        finally:
            self._applying = False


def configure_native_host(window):
    """Set the tested non-layered DWM profile before Qt creates the HWND."""
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return
    if window.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
        raise RuntimeError("native_host_must_be_configured_before_winId")
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)


def handle_native_host_event(window, event_type, message):
    """Implement the probed NCCALCSIZE contract and forward settings changes."""
    if event_type != b"windows_generic_MSG":
        return None
    try:
        import ctypes.wintypes
        msg = ctypes.wintypes.MSG.from_address(int(message))
    except (TypeError, ValueError, OSError):
        return None
    number = int(msg.message)
    controller = getattr(window, "_backdrop_controller", None)
    if controller is not None and hasattr(controller, "native_settings_changed"):
        controller.native_settings_changed(number)
    if number == 0x0083:  # WM_NCCALCSIZE: client fills the preserved caption.
        return True, 0
    return None
