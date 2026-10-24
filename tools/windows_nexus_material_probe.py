"""Real-screen DWM validation of Nexus's actual Windows widget classes.

Uses a temporary SQLite file, synthetic clips/background, a visible controlled
probe window and no hotkey, application singleton, paste target or clipboard
read/write. Restores the prior foreground window after capture.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from nexus_linux import paths, styles, windows_design
from nexus_linux.preview_service import PreviewService
from nexus_linux.storage import StorageService
from nexus_linux.windows_backdrop import BackdropController, NativeBackend
from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow
from nexus_linux.windows_timeline import WindowsTimelineWindow

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
get_style.argtypes = [wintypes.HWND, ctypes.c_int]
get_style.restype = ctypes.c_ssize_t
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wintypes.BOOL
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                     ctypes.POINTER(wintypes.FILETIME)]
kernel32.GetProcessTimes.restype = wintypes.BOOL
psapi = ctypes.WinDLL("psapi", use_last_error=True)


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
          "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
          "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]
psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]


def synthetic_image():
    image = QImage(700, 420, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, 700, 420)
    gradient.setColorAt(0, QColor("#527CA8")); gradient.setColorAt(1, QColor("#C99B8D"))
    painter.fillRect(image.rect(), gradient)
    painter.fillRect(0, 280, 700, 140, QColor("#293D56"))
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor("#F1DECD"))
    painter.drawEllipse(500, 52, 80, 80)
    painter.end()
    from PySide6.QtCore import QBuffer, QIODevice
    buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, "PNG")
    return bytes(buffer.data())


class SyntheticBackdrop(QWidget):
    def __init__(self, screen):
        super().__init__(None)
        self.phase = 0
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(screen.availableGeometry())

    def paintEvent(self, event):
        painter = QPainter(self)
        backgrounds = (("#F4F1EA", "#304A72"), ("#ED3B62", "#2171D8"), ("#20262E", "#D8B84F"))
        first, second = backgrounds[self.phase]
        painter.fillRect(self.rect(), QColor(first))
        for x in range(0, self.width(), 24):
            painter.fillRect(x, 0, 11, self.height(), QColor(second))
        painter.fillRect(0, 0, self.width(), 80, QColor("#29374B"))
        painter.setPen(QColor("#FFFFFF")); painter.setFont(QFont("Segoe UI", 17))
        painter.drawText(QRect(28, 12, self.width() - 50, 56), Qt.AlignmentFlag.AlignVCenter,
                         "Synthetic background · DWM desktop Acrylic probe · no private content")
        painter.end()


def safe_capture(screen):
    """Do not record foreign windows overlapping the owned synthetic desktop."""
    area = screen.availableGeometry()
    probe_hwnds = set()
    for widget in QApplication.topLevelWidgets():
        if widget.isVisible():
            probe_hwnds.add(int(widget.winId()))
    allowed, reached_owned = [True], [False]
    blockers = []
    @ENUMPROC
    def visit(hwnd, unused):
        if int(hwnd) in probe_hwnds:
            reached_owned[0] = True
            return False
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != os.getpid():
                rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if (rect.left < area.right() and rect.right > area.left()
                        and rect.top < area.bottom() and rect.bottom > area.top()):
                    blockers.append({"hwnd": int(hwnd), "pid": int(pid.value),
                                     "rect": [rect.left, rect.top, rect.right, rect.bottom]})
                    allowed[0] = False
                    return True
        return True
    user32.EnumWindows(visit, 0)
    return allowed[0] and reached_owned[0], blockers


def put_owned_window_topmost(window):
    hwnd = int(window.winId())
    return bool(user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0,
                                    0x0001 | 0x0002 | 0x0010 | 0x0040))


def restore_foreground(hwnd):
    if not hwnd:
        return False
    if user32.SetForegroundWindow(hwnd):
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)
    current = user32.GetForegroundWindow()
    thread = int(user32.GetWindowThreadProcessId(current, None)) if current else 0
    own = int(kernel32.GetCurrentThreadId())
    attached = bool(thread and thread != own and user32.AttachThreadInput(own, thread, True))
    try:
        return bool(user32.SetForegroundWindow(hwnd)) and int(user32.GetForegroundWindow() or 0) == int(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(own, thread, False)


def foreground_owned_window(window):
    hwnd = int(window.winId())
    if user32.SetForegroundWindow(hwnd):
        return int(user32.GetForegroundWindow() or 0) == hwnd
    foreground = user32.GetForegroundWindow()
    thread = int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
    own = int(kernel32.GetCurrentThreadId())
    attached = bool(thread and thread != own and user32.AttachThreadInput(own, thread, True))
    try:
        return bool(user32.SetForegroundWindow(hwnd)) and int(user32.GetForegroundWindow() or 0) == hwnd
    finally:
        if attached:
            user32.AttachThreadInput(own, thread, False)


def process_usage():
    process = kernel32.GetCurrentProcess()
    counters = PROCESS_MEMORY_COUNTERS_EX(cb=ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX))
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    if not kernel32.GetProcessTimes(process, ctypes.byref(creation), ctypes.byref(exit_time),
                                   ctypes.byref(kernel_time), ctypes.byref(user_time)):
        raise ctypes.WinError()
    def filetime(value): return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
    return {"working_set_mib": counters.WorkingSetSize / 1024**2,
            "private_mib": counters.PrivateUsage / 1024**2,
            "process_cpu_100ns": filetime(kernel_time) + filetime(user_time)}


class PasteStub:
    def paste_item(self, *args, **kwargs): return False
    def write_to_clipboard(self, *args, **kwargs): return False
    def synthetic_paste(self): return False
    def remember_target(self): return None


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else
                  "C:/Users/Admin/AppData/Local/Temp/opencode/nexus-native-phase/nexus-ui")
    material_request = sys.argv[2] if len(sys.argv) > 2 else "desktop_acrylic"
    idle_seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    if material_request not in ("desktop_acrylic", "satin"):
        raise ValueError("material must be desktop_acrylic or satin")
    output.mkdir(parents=True, exist_ok=True)
    original_foreground = int(user32.GetForegroundWindow() or 0)
    original_foreground_pid = wintypes.DWORD()
    if original_foreground:
        user32.GetWindowThreadProcessId(original_foreground, ctypes.byref(original_foreground_pid))
    report = {"build": sys.getwindowsversion().build, "qt": __import__("PySide6.QtCore", fromlist=["qVersion"]).qVersion(),
              "dpr": None, "database": "temporary synthetic only", "clipboard_access": False,
              "foreground_before_hwnd": original_foreground,
              "foreground_before_pid": original_foreground_pid.value,
              "hotkeys_registered": False, "screenshots_use_render": False,
              "material_request": material_request, "resource_measurement_idle_seconds": idle_seconds,
              "captures": [], "material": []}
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    app.setFont(windows_design.font()); app.setStyleSheet(windows_design.qss())
    screen = app.primaryScreen()
    if screen is None:
        raise RuntimeError("No Qt screen is available")
    report["dpr"] = screen.devicePixelRatio()
    report["work_area_logical"] = [screen.availableGeometry().x(), screen.availableGeometry().y(),
                                   screen.availableGeometry().width(), screen.availableGeometry().height()]
    backdrop = SyntheticBackdrop(screen)
    backdrop.show(); put_owned_window_topmost(backdrop); app.processEvents(); QTest.qWait(120)

    prior_ensure = paths.ensure_dirs
    paths.ensure_dirs = lambda: None
    original_theme_file, original_theme = styles._THEME_FILE, styles._active_key
    store = None
    panel = stack_window = None
    controllers = []
    try:
        with tempfile.TemporaryDirectory(prefix="nexus-native-material-") as temporary:
            sandbox = Path(temporary)
            styles._THEME_FILE = sandbox / "theme.json"
            styles._active_key = "liquid_glass"
            store = StorageService(sandbox / "synthetic.sqlite")
            text = store.capture("text", "Diseña con intención. Guarda lo importante y encuentra cada idea.", source_app="Editor")
            image = store.capture("image", image_data=synthetic_image(), source_app="Recortes")
            link = store.capture("url", text_content="https://example.invalid/studio", source_app="Browser")
            code = store.capture("text", text_content="const surface = glass(backdrop);\nreturn surface.render();", source_app="Code")
            board = store.create_pinboard("Mercado Libre", "#E2BA60")
            store.add_to_pinboard(image.id, board.id); store.add_to_pinboard(text.id, board.id)
            for row_id, in store._conn.execute("SELECT id FROM clip_items").fetchall():
                store._conn.execute("UPDATE clip_items SET updated_at = ? WHERE id = ?",
                                    (time.time() - (code.id - row_id), row_id))
            store._conn.commit()
            preview = PreviewService(store, parent=app)
            panel = WindowsTimelineWindow(store, PasteStub(), preview)
            panel.resolve_target_screen = lambda: screen
            panel._preview_data[link.id] = {"title": "A calmer place for creative work", "domain": "example.invalid"}
            panel._delegate.set_preview_data(panel._preview_data)
            states = []
            def can_paint(window):
                return bool(sys.getwindowsversion().build == 26300 and app.platformName() == "windows"
                            and not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                            and window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
                            and window.windowOpacity() == 1.0)
            controller = BackdropController(panel, backend=NativeBackend(), request=material_request,
                                            can_native_paint=can_paint, on_state=lambda state: (
                                                states.append(state),
                                                panel._surface.set_native_backdrop(state.native_enabled), panel.update()))
            controllers.append(controller)
            panel._backdrop_controller = controller
            panel.winId(); controller.schedule()
            stack = WindowsClipStack(store, PasteStub(), sequence=lambda: 1)
            stack_window = WindowsStackWindow(stack)
            stack_window._stack._items = [text, image, code]
            stack_window.refresh()
            stack_states = []
            stack_controller = BackdropController(stack_window, backend=NativeBackend(), request=material_request,
                can_native_paint=can_paint, on_state=lambda state: (
                    stack_states.append(state),
                    stack_window._surface.set_native_backdrop(state.native_enabled), stack_window.update()))
            controllers.append(stack_controller)
            stack_window._backdrop_controller = stack_controller
            stack_window.winId(); stack_controller.schedule()
            app.processEvents(); QTest.qWait(250)
            for name, collection in (("history", states), ("stack", stack_states)):
                state = collection[-1] if collection else None
                report["material"].append({"window": name,
                    "requested": state.requested if state else "pending",
                    "applied": state.applied if state else "pending",
                    "native_enabled": state.native_enabled if state else False,
                    "runtime_visual_verified": state.verified_visual if state else False,
                    "visual_acceptance": "determined_by_synthetic_screen_probe"})

            def capture(name):
                app.processEvents(); QTest.qWait(180)
                allowed, blockers = safe_capture(screen)
                if not allowed:
                    report["captures"].append({"name": name, "skipped": "foreign_window_overlaps_capture",
                                               "blockers": blockers[:8]})
                    return
                area = screen.availableGeometry()
                shot = screen.grabWindow(0, area.x(), area.y(), area.width(), area.height())
                filename = f"{name}.png"
                shot.save(str(output / filename))
                report["captures"].append({"name": name, "file": filename,
                                           "pixels": [shot.width(), shot.height()],
                                           "dpr": shot.devicePixelRatio(), "capture": "real_screen"})
                return shot.toImage()

            active_samples = []
            for phase, label in enumerate(("light", "color", "dark")):
                backdrop.phase = phase; backdrop.update()
                panel.show_and_focus(); put_owned_window_topmost(panel)
                report["win32_foreground_history"] = foreground_owned_window(panel)
                report["win32_foreground_hwnd"] = int(user32.GetForegroundWindow() or 0)
                shot = capture(f"history-{label}-active")
                if shot is not None:
                    area = screen.availableGeometry()
                    sample = panel.mapToGlobal(QPoint(12, max(40, panel.height() - 20)))
                    x = round((sample.x() - area.x()) * shot.devicePixelRatio())
                    y = round((sample.y() - area.y()) * shot.devicePixelRatio())
                    active_samples.append(shot.pixelColor(x, y).name())
                report["active_focus_history"] = bool(panel.isActiveWindow())
                report["history_style_after_show"] = int(get_style(int(panel.internalWinId()), -16)) & 0xFFFFFFFF
                report["history_popup_after_show"] = bool(report["history_style_after_show"] & 0x80000000)
                report["history_ex_style_after_show"] = int(get_style(int(panel.internalWinId()), -20)) & 0xFFFFFFFF
                report["history_layered_after_show"] = bool(report["history_ex_style_after_show"] & 0x80000)
                report["panel_geometry"] = [panel.x(), panel.y(), panel.width(), panel.height()]
                report["system_material_readback"] = states[-1].readback if states else None
                if phase == 0:
                    report["inactive_focus_host"] = foreground_owned_window(backdrop)
                    panel.raise_(); put_owned_window_topmost(panel)
                    inactive_shot = capture("history-light-inactive")
                    if inactive_shot is not None:
                        report["inactive_visual_sample"] = inactive_shot.pixelColor(x, y).name()
                    foreground_owned_window(panel)
            panel.show_pinboard_management(); capture("pinboard-manager")
            panel.close_pinboard_manager(); panel._toggle_expanded(); capture("expanded")
            stack_samples = []
            stack_grid_samples = []
            for phase, label in enumerate(("light", "color", "dark")):
                backdrop.phase = phase; backdrop.update()
                stack_window.show_and_focus(); put_owned_window_topmost(stack_window)
                report["win32_foreground_stack"] = foreground_owned_window(stack_window)
                stack_shot = capture(f"stack-{label}-active")
                if stack_shot is not None:
                    area = screen.availableGeometry()
                    samples = []
                    for fraction in (.12, .20, .28, .36, .44, .52, .60, .68, .76):
                        sample = stack_window.mapToGlobal(QPoint(
                            max(12, stack_window.width() - 12),
                            round(stack_window.height() * fraction)))
                        x = round((sample.x() - area.x()) * stack_shot.devicePixelRatio())
                        y = round((sample.y() - area.y()) * stack_shot.devicePixelRatio())
                        samples.append(stack_shot.pixelColor(x, y).name())
                    stack_grid_samples.append(samples)
            panel.show_and_focus(); put_owned_window_topmost(panel)
            foreground_owned_window(panel); capture("history-reopened")
            report["active_surface_samples"] = active_samples
            report["active_surface_varies_with_synthetic_background"] = len(set(active_samples)) >= 2
            report["stack_active_surface_samples"] = stack_samples
            report["stack_active_surface_grid_samples"] = stack_grid_samples
            report["stack_geometry"] = [stack_window.x(), stack_window.y(), stack_window.width(), stack_window.height()]
            report["stack_active_surface_varies_with_synthetic_background"] = bool(
                stack_grid_samples and any(len({samples[i] for samples in stack_grid_samples}) >= 2
                                           for i in range(len(stack_grid_samples[0])))
            )
            report["verified_visual"] = bool(
                report["active_surface_varies_with_synthetic_background"]
                and report["stack_active_surface_varies_with_synthetic_background"]
                and all(entry["native_enabled"] and entry["applied"] == "desktop_acrylic"
                        for entry in report["material"])
            )
            if idle_seconds > 0:
                from PySide6.QtCore import QEventLoop, QTimer
                panel.show_and_focus(); foreground_owned_window(panel)
                start_usage = process_usage()
                start_wall = time.perf_counter()
                idle = QEventLoop()
                QTimer.singleShot(round(idle_seconds * 1000), idle.quit)
                idle.exec()
                elapsed = time.perf_counter() - start_wall
                end_usage = process_usage()
                cpu_seconds = (end_usage["process_cpu_100ns"] - start_usage["process_cpu_100ns"]) / 10_000_000
                report["resource_measurement"] = {
                    "window": "visible_history_synthetic_db_no_hotkeys_no_clipboard",
                    "wall_seconds": elapsed,
                    "working_set_mib": round(end_usage["working_set_mib"], 2),
                    "private_mib": round(end_usage["private_mib"], 2),
                    "process_cpu_seconds": round(cpu_seconds, 4),
                    "process_cpu_percent_one_core": round(cpu_seconds / elapsed * 100, 3),
                    "dwm_gpu_cpu_measured": False,
                }
            report["reopen_geometry_stable"] = [panel.x(), panel.y(), panel.width(), panel.height()] == report["panel_geometry"]
            controller.close(); stack_controller.close()
            panel.hide(); stack_window.hide(); backdrop.hide()
            report["foreground_restored"] = restore_foreground(original_foreground)
            final_foreground = user32.GetForegroundWindow()
            final_pid = wintypes.DWORD()
            if final_foreground:
                user32.GetWindowThreadProcessId(final_foreground, ctypes.byref(final_pid))
            report["foreground_after_restore_hwnd"] = int(final_foreground or 0)
            report["foreground_after_restore_pid"] = final_pid.value
            store.close(); store = None
    finally:
        for controller in controllers:
            controller.close()
        for window in (panel, stack_window, backdrop):
            if window is not None:
                window.hide()
                window.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if store is not None:
            store.close()
        paths.ensure_dirs = prior_ensure
        styles._THEME_FILE = original_theme_file
        styles._active_key = original_theme
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
