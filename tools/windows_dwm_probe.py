"""Isolated, non-activating real-desktop DWM experiment.

No Nexus user DB, settings writes, hotkeys, clipboard, input injection or other
application actions. Capture only an owned synthetic host, after checking its
visible rectangle is not occluded by any foreign top-level window. Captures
are validation evidence, NEVER used to render a material.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
get_style.argtypes = [wintypes.HWND, ctypes.c_int]
get_style.restype = ctypes.c_ssize_t
set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
set_style.restype = ctypes.c_ssize_t
dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


class Margins(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]


dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(Margins)]
WM_NCCALCSIZE = 0x0083
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_POPUP = 0x80000000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000


def hresult(result): return f"0x{result & 0xffffffff:08X}"


def set_attribute(hwnd, attribute, value):
    data = ctypes.c_int(value)
    return hresult(dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)))


class Host(QWidget):
    phase = 0

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(80, 100, 1000, 620)

    def paintEvent(self, event):
        painter = QPainter(self)
        colors = [("#F5F5F5", "#101010"), ("#F03060", "#216BE0"), ("#20252D", "#D9B956")][self.phase]
        painter.fillRect(self.rect(), QColor(colors[0]))
        for x in range(0, self.width(), 12):
            painter.fillRect(x, 0, 6, self.height(), QColor(colors[1]))
        painter.fillRect(0, 0, self.width(), 70, QColor("#28334A"))
        painter.setFont(QFont("Segoe UI", 14)); painter.setPen(QColor("#FFFFFF"))
        painter.drawText(QRect(24, 12, 950, 46), Qt.AlignmentFlag.AlignVCenter,
                         "Nexus · isolated compositor probe · synthetic background · no clipboard")
        painter.end()


class Pane(QWidget):
    def __init__(self, host, layered, no_system, alpha, styled=False, active=False, framed=False, retain_nc=False):
        super().__init__(None)
        self.host, self.alpha = host, alpha
        self.retain_nc = retain_nc
        self.nc_messages = 0
        flags = (Qt.WindowType.Window if framed else Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint) | Qt.WindowType.WindowStaysOnTopHint
        if not active: flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not active)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, layered)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, no_system)
        self.setAutoFillBackground(False)
        if styled:
            self.setStyleSheet("background: transparent;")
        self.setGeometry(host.x() + 120, host.y() + 120, 760, 340)

    def nativeEvent(self, event_type, message):
        if self.retain_nc:
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == WM_NCCALCSIZE:
                self.nc_messages += 1
                return True, 0
        return super().nativeEvent(event_type, message)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(18, 25, 35, self.alpha))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.fillRect(30, 28, 700, 60, QColor("#293B52"))
        painter.setFont(QFont("Segoe UI", 18)); painter.setPen(QColor("#FFFFFF"))
        painter.drawText(QRect(48, 28, 640, 60), Qt.AlignmentFlag.AlignVCenter, "Opaque text and controls — only the container is glass")
        painter.setPen(QColor("#9DC7FA")); painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        painter.end()


ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]


def safe_to_capture(host):
    """Stop at our host in Z order; never capture a foreign overlay."""
    hwnd = int(host.winId())
    rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
    allowed, reached = [True], [False]
    @ENUMPROC
    def visit(other, unused):
        if int(other) == hwnd:
            reached[0] = True
            return False
        if user32.IsWindowVisible(other):
            pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(other, ctypes.byref(pid))
            if pid.value != os.getpid():
                other_rect = wintypes.RECT(); user32.GetWindowRect(other, ctypes.byref(other_rect))
                if (other_rect.left < rect.right and other_rect.right > rect.left
                        and other_rect.top < rect.bottom and other_rect.bottom > rect.top):
                    allowed[0] = False
                    return False
        return True
    user32.EnumWindows(visit, 0)
    return allowed[0] and reached[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--active", action="store_true", help="Requires an explicitly agreed focused test")
    parser.add_argument("--profiles", default="", help="Optional comma-separated profile names")
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    original_foreground = int(user32.GetForegroundWindow() or 0)
    host = Host()
    host.show(); app.processEvents(); QTest.qWait(150)
    report = {"build": sys.getwindowsversion().build, "qt_platform": app.platformName(),
              "capture": "QScreen.grabWindow(0, owned_host_client_rect); HWND DC excludes overlapping panes, so use guarded screen ROI",
              "pid": os.getpid(), "foreground_became_probe": False, "profiles": []}
    profiles = [
        ("layered_control", True, True, 0, False, False),
        ("layered_dwm", True, True, 0, False, True),
        ("nonlayered_no_system", False, True, 0, False, True),
        ("nonlayered_clear", False, False, 0, False, True),
        ("nonlayered_styled", False, True, 0, True, True),
        ("layered_satin", True, True, 253, False, True),
        ("framed_reference", False, True, 0, False, True),
        ("layered_nc_enabled", True, True, 0, False, True),
        ("nonlayered_nc_enabled", False, True, 0, False, True),
        ("frameless_nc_preserved", False, True, 0, False, True),
        ("window_nc_preserved", False, True, 0, False, True),
        ("window_caption_nobuttons", False, True, 0, False, True),
        ("window_caption_nobuttons_popup_removed", False, True, 0, False, True),
        ("window_caption_nobuttons_nc_disabled", False, True, 0, False, True),
        ("window_corner_default", False, True, 0, False, True),
        ("window_corner_donotround", False, True, 0, False, True),
        ("window_tint42", False, True, 42, False, True),
        ("window_tint84", False, True, 84, False, True),
    ]
    if args.profiles:
        profiles = [profile for profile in profiles if profile[0] in args.profiles.split(",")]
    try:
        for name, layered, no_system, alpha, styled, native in profiles:
            retain_nc = name.endswith("nc_preserved") or name.startswith("window_")
            pane = Pane(host, layered, no_system, alpha, styled, args.active,
                        name.startswith("framed_") or name.startswith("window_"),
                        name.startswith("window_") or name == "window_nc_preserved")
            hwnd = int(pane.winId())
            style_before = int(get_style(hwnd, GWL_STYLE))
            if name in ("window_caption_nobuttons", "window_caption_nobuttons_nc_disabled",
                        "window_caption_nobuttons_popup_removed",
                        "window_corner_default", "window_corner_donotround"):
                style_after = (style_before | WS_CAPTION) & ~(WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
                if name == "window_caption_nobuttons_popup_removed":
                    style_after &= ~WS_POPUP
                set_style(hwnd, GWL_STYLE, style_after)
                user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                    0x0001 | 0x0002 | 0x0004 | 0x0020)  # FRAMECHANGED, geometry/Z untouched.
            elif name == "window_nc_preserved":
                # Restore only the documented standard non-client styles Qt's
                # FramelessWindowHint removed, then let Qt custom-draw client.
                set_style(hwnd, GWL_STYLE, style_before | WS_CAPTION | WS_THICKFRAME)
                user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                    0x0001 | 0x0002 | 0x0004 | 0x0020)  # FRAMECHANGED; no move/size/z-order.
            calls = {"dark": set_attribute(hwnd, 20, 1), "border": set_attribute(hwnd, 34, -2),
                     "backdrop": set_attribute(hwnd, 38, 3 if native else 1),
                     "extend_frame": hresult(dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(Margins(-1, -1, -1, -1))))}
            if name in ("window_corner_default", "window_corner_donotround"):
                calls["corner"] = set_attribute(hwnd, 33, 0 if name.endswith("default") else 1)
            if name == "window_caption_nobuttons_nc_disabled":
                calls["nc_render_policy"] = set_attribute(hwnd, 2, 1)
            pane.show()
            activation = {}
            if args.active:
                pane.raise_(); pane.activateWindow()
                activation["set_foreground"] = bool(user32.SetForegroundWindow(hwnd))
                if int(user32.GetForegroundWindow() or 0) != hwnd:
                    foreground = user32.GetForegroundWindow()
                    thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
                    own_thread = kernel32.GetCurrentThreadId()
                    attached = bool(thread and thread != own_thread and user32.AttachThreadInput(own_thread, thread, True))
                    try:
                        activation["attached"] = attached
                        activation["set_foreground_after_attach"] = bool(user32.SetForegroundWindow(hwnd))
                    finally:
                        if attached: user32.AttachThreadInput(own_thread, thread, False)
            app.processEvents(); QTest.qWait(250)
            if name.endswith("nc_enabled"):
                calls["nc_render_policy"] = set_attribute(hwnd, 2, 2)
                calls["extend_after_nc"] = hresult(dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(Margins(-1, 0, 0, 0))))
                calls["backdrop_after_nc"] = set_attribute(hwnd, 38, 3)
                QTest.qWait(200)
            nc = ctypes.c_int(-1)
            nc_hr = hresult(dwm.DwmGetWindowAttribute(hwnd, 1, ctypes.byref(nc), ctypes.sizeof(nc)))
            value = ctypes.c_int(-1)
            read = hresult(dwm.DwmGetWindowAttribute(hwnd, 38, ctypes.byref(value), ctypes.sizeof(value)))
            item = {"name": name, "hwnd": hwnd, "layered": bool(get_style(hwnd, -20) & 0x80000),
                    "qt_alpha": pane.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
                    "opacity": pane.windowOpacity(), "hresults": calls, "readback_hresult": read,
                    "backdrop_readback": value.value, "captures": []}
            item["active"] = int(user32.GetForegroundWindow() or 0) == hwnd
            item["activation"] = activation
            item["nc_rendering"] = {"enabled": nc.value, "hresult": nc_hr}
            item["nativeevent_nccalc_count"] = pane.nc_messages
            item["style"] = {"before": style_before, "after": int(get_style(hwnd, GWL_STYLE)),
                              "caption_before": bool(style_before & WS_CAPTION),
                              "caption_after": bool(get_style(hwnd, GWL_STYLE) & WS_CAPTION),
                              "popup_before": bool(style_before & WS_POPUP),
                              "popup_after": bool(get_style(hwnd, GWL_STYLE) & WS_POPUP),
                              "sysmenu_after": bool(get_style(hwnd, GWL_STYLE) & WS_SYSMENU)}
            for phase in range(3):
                host.phase = phase; host.update(); app.processEvents(); QTest.qWait(180)
                dwm.DwmFlush()
                report["foreground_became_probe"] |= int(user32.GetForegroundWindow() or 0) in (hwnd, int(host.winId()))
                if not safe_to_capture(host):
                    item["captures"].append({"phase": phase, "skipped": "foreign_window_occludes_host"})
                    continue
                position = host.mapToGlobal(QPoint(0, 0))
                image = host.screen().grabWindow(0, position.x(), position.y(), host.width(), host.height())
                calibration = image.toImage().pixelColor(round(5 * image.devicePixelRatio()), round(5 * image.devicePixelRatio()))
                if calibration.name().lower() != "#28334a":
                    item["captures"].append({"phase": phase, "skipped": "screen_roi_not_calibrated_to_owned_host"})
                    continue
                path = output / f"{name}-{phase}.png"
                image.save(str(path))
                item["captures"].append({"phase": phase, "file": path.name, "pixels": [image.width(), image.height()], "dpr": image.devicePixelRatio()})
            report["profiles"].append(item)
            pane.hide(); pane.close(); pane.destroy(); app.processEvents()
        print(json.dumps(report, indent=2))
    finally:
        host.hide(); host.close()
        if args.active and original_foreground:
            report["restore_foreground_return"] = bool(user32.SetForegroundWindow(original_foreground))
            report["foreground_restored"] = int(user32.GetForegroundWindow() or 0) == original_foreground
        (output / "probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__": main()
