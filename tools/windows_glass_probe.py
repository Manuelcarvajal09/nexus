"""Real-screen capture of Nexus' actual window classes over a SYNTHETIC backdrop.

Used to judge the glass material with real DWM Acrylic (offscreen renders cannot).
Safety: temporary synthetic SQLite and clips, no hotkeys, no clipboard, no
singleton, no paste target; a small borderless backdrop window of our own is
placed top-most in one corner of the primary screen for a few seconds and ONLY
that region is captured, and only if no foreign window overlaps it. Restores the
previous foreground window afterwards. It never touches your Nexus.

    python tools/windows_glass_probe.py OUT_DIR [--theme liquid_glass] [--system dark|light]
                                        [--scale 1.25] [--src DIR\\src] [--views history,settings,stack]
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

parser = argparse.ArgumentParser()
parser.add_argument("out")
parser.add_argument("--theme", default="liquid_glass")
parser.add_argument("--system", choices=("dark", "light"), default=None, help="override the system app theme (probe process only)")
parser.add_argument("--scale", default="1")
parser.add_argument("--src", default=str(Path(__file__).resolve().parents[1] / "src"))
parser.add_argument("--views", default="history,settings,stack")
parser.add_argument("--phases", default="light,color,dark")
parser.add_argument("--label", default="")
parser.add_argument("--flip", default="", help="e.g. dark>light: switch the variant on the SAME live windows, as a Windows theme change would")
args = parser.parse_args()

os.environ["QT_QPA_PLATFORM"] = "windows"          # never offscreen
os.environ["QT_SCALE_FACTOR"] = args.scale
sys.path.insert(0, args.src)

from PySide6.QtCore import QPoint, QRect, Qt                                    # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter       # noqa: E402
from PySide6.QtTest import QTest                                                 # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                              # noqa: E402

from nexus_linux import paths, styles, windows_design                            # noqa: E402

try:
    from nexus_linux import windows_system_theme                                 # noqa: E402
except ImportError:                                                              # older code under comparison
    windows_system_theme = None

from nexus_linux.storage import StorageService                                   # noqa: E402
from nexus_linux.windows_backdrop import BackdropController, NativeBackend       # noqa: E402
from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow       # noqa: E402
from nexus_linux.windows_timeline import WindowsTimelineWindow                   # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

REGION_W, REGION_H = 1240, 700


def synthetic_image():
    image = QImage(700, 420, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, 700, 420)
    gradient.setColorAt(0, QColor("#527CA8")); gradient.setColorAt(1, QColor("#C99B8D"))
    painter.fillRect(image.rect(), gradient)
    painter.fillRect(0, 280, 700, 140, QColor("#293D56"))
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor("#F1DECD")); painter.drawEllipse(500, 52, 80, 80)
    painter.end()
    from PySide6.QtCore import QBuffer, QIODevice
    buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, "PNG")
    return bytes(buffer.data())


class Backdrop(QWidget):
    """A synthetic 'desktop': sharp details so blur and transparency are obvious."""
    PHASES = {"light": ("#F6F4EE", "#2F4B73", "#FFFFFF", "#1F2937"),
              "color": ("#E8386A", "#1F6FE0", "#FFD84A", "#FFFFFF"),
              "dark": ("#1B2027", "#D9B64E", "#2A3340", "#E5E7EB")}

    def __init__(self, rect):
        super().__init__(None)
        self.phase = "light"
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(rect)

    def paintEvent(self, event):
        base, stripe, block, text = self.PHASES[self.phase]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(base))
        for x in range(0, self.width(), 26):
            painter.fillRect(x, 0, 10, self.height(), QColor(stripe))
        for index in range(7):
            painter.fillRect(60 + index * 160, 70 + (index % 3) * 150, 130, 90, QColor(block))
        painter.setPen(QColor(text)); painter.setFont(QFont("Segoe UI", 15))
        for row in range(11):
            painter.drawText(24, 36 + row * 60, "Fondo sintético · detalle nítido para ver el desenfoque del cristal")
        painter.end()


def foreground(hwnd):
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


def topmost(window):
    user32.SetWindowPos(int(window.winId()), wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)


def foreign_overlap(region):
    own = {int(w.winId()) for w in QApplication.topLevelWidgets() if w.isVisible()}
    blockers, reached = [], [False]

    @ENUMPROC
    def visit(hwnd, _):
        if int(hwnd) in own:
            reached[0] = True
            return False
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != os.getpid():
                rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if rect.left < region.right() and rect.right > region.left() and rect.top < region.bottom() and rect.bottom > region.top():
                    blockers.append(int(pid.value))
        return True

    user32.EnumWindows(visit, 0)
    return blockers


class Paste:
    def paste_item(self, *a, **k): return False
    def write_to_clipboard(self, *a, **k): return False
    def synthetic_paste(self): return False
    def remember_target(self): return None


def main():
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    original_foreground = int(user32.GetForegroundWindow() or 0)
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    if args.system and windows_system_theme is not None:
        windows_system_theme.set_override(args.system)
    screen = app.primaryScreen()
    work = screen.availableGeometry()
    region = QRect(work.x(), work.y(), min(REGION_W, work.width()), min(REGION_H, work.height()))
    fake_screen = SimpleNamespace(availableGeometry=lambda: region, geometry=lambda: region)
    report = {"build": sys.getwindowsversion().build, "dpr": screen.devicePixelRatio(), "platform": app.platformName(),
              "theme": args.theme, "system": args.system or "real", "scale": args.scale, "src": args.src, "captures": []}
    paths.ensure_dirs = lambda: None
    with tempfile.TemporaryDirectory(prefix="nexus-glass-probe-") as temporary:
        sandbox = Path(temporary)
        styles._THEME_FILE = sandbox / "theme.json"
        styles._active_key = args.theme
        app.setFont(windows_design.font()); app.setStyleSheet(windows_design.qss())
        store = StorageService(sandbox / "synthetic.sqlite")
        board = store.create_pinboard("Prompts", "#FF2D55", "📌")
        store.create_pinboard("Mercadolibre", "#FFCC00", "⭐")
        prompt = store.capture(kind="text", text_content="Eres un auditor senior de ciberseguridad.\nRevisa todo el sistema paso a paso y entrega un informe completo.", source_app="Claude")
        other = store.capture(kind="text", text_content="Resume este documento en cinco puntos clave, con tono ejecutivo.", source_app="Claude")
        link = store.capture(kind="url", text_content="https://example.com/studio", source_app="Edge")
        image = store.capture(kind="image", image_data=synthetic_image(), source_app="Recortes")
        file = store.capture(kind="file", file_path="C:/Projects/Brand guidelines.pdf", source_app="Explorer")
        code = store.capture(kind="text", text_content="const surface = glass(backdrop);\nreturn surface.render();", source_app="Code")
        store.set_title(prompt.id, "Auditoría completa de ciberseguridad")
        store.set_title(image.id, "Moodboard de la campaña")
        store.add_to_pinboard(prompt.id, board.id); store.add_to_pinboard(other.id, board.id)
        order = [code, file, link, image, other, prompt]
        for n, item in enumerate(order):
            store._conn.execute("UPDATE clip_items SET updated_at = ? WHERE id = ?", (time.time() - (len(order) - n), item.id))
        store._conn.commit()

        backdrop = Backdrop(region)
        backdrop.show(); topmost(backdrop); app.processEvents(); QTest.qWait(150)
        panel = WindowsTimelineWindow(store, Paste())
        panel.resolve_target_screen = lambda: fake_screen
        panel._preview_data[link.id] = {"title": "A calmer place for creative work", "domain": "example.com"}
        panel._delegate.set_preview_data(panel._preview_data)
        from nexus_linux import windows_app
        from nexus_linux.windows_settings import DEFAULTS
        from nexus_linux.windows_shortcuts import ShortcutStore
        settings = dict(DEFAULTS)
        backend = windows_app.SettingsBackend(settings, ShortcutStore(settings), Mock(failures={}), panel)
        panel.configure_settings(backend)
        stack_window = WindowsStackWindow(WindowsClipStack(store, Paste(), sequence=lambda: 1))
        stack_window._stack._items = [prompt, image, other]
        stack_window.refresh()

        def can_paint(window):
            return bool(sys.getwindowsversion().build == 26300 and app.platformName() == "windows"
                        and not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                        and window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) and window.windowOpacity() == 1.0)
        controllers = []
        import inspect
        extra = {"variant": windows_design.native_variant} if "variant" in inspect.signature(BackdropController).parameters else {}
        for window in (panel, stack_window):
            ctrl = BackdropController(window, backend=NativeBackend(), request="desktop_acrylic", can_native_paint=can_paint,
                                      on_state=lambda state, w=window: (w._surface.set_native_backdrop(state.native_enabled), w.update()), **extra)
            window._backdrop_controller = ctrl
            controllers.append(ctrl)
            window.winId(); ctrl.schedule()
        app.processEvents(); QTest.qWait(250)

        def grab(label):
            app.processEvents(); QTest.qWait(260)
            blockers = foreign_overlap(region)
            if blockers:
                report["captures"].append({"name": label, "skipped": "foreign window overlaps the region", "pids": blockers[:5]})
                return
            shot = screen.grabWindow(0, region.x(), region.y(), region.width(), region.height())
            shot.save(str(out / f"{args.label}{label}.png"))
            report["captures"].append({"name": label, "pixels": [shot.width(), shot.height()]})

        views = args.views.split(",")
        for phase in args.phases.split(","):
            backdrop.phase = phase; backdrop.update(); app.processEvents()
            if "history" in views:
                panel.show_and_focus(); topmost(panel); foreground(int(panel.winId())); panel.close_pinboard_manager()
                grab(f"history-{phase}")
            if "settings" in views:
                panel.show_settings(); topmost(panel); foreground(int(panel.winId())); grab(f"settings-{phase}")
                panel.close_pinboard_manager()
            if "stack" in views:
                panel.hide()
                stack_window.show_and_focus()
                stack_window.move(region.x() + (region.width() - stack_window.width()) // 2,
                                  region.y() + (region.height() - stack_window.height()) // 2)
                topmost(stack_window); foreground(int(stack_window.winId())); grab(f"stack-{phase}")
                stack_window.hide()
        if args.flip and windows_system_theme is not None:
            first, second = args.flip.split(">")
            windows_system_theme.set_override(first)
            panel_geometry = None
            for phase in ("color", "light"):
                backdrop.phase = phase; backdrop.update(); app.processEvents()
                windows_system_theme.set_override(first)
                app.setStyleSheet(windows_design.qss()); [c.schedule() for c in controllers]
                panel.apply_visual_theme(); stack_window.apply_visual_theme()
                panel.show_and_focus(); topmost(panel); foreground(int(panel.winId())); panel.close_pinboard_manager()
                grab(f"flip-{phase}-1-{first}")
                started = time.perf_counter()
                windows_system_theme.set_override(second)       # what the watcher does on WM_SETTINGCHANGE
                app.setStyleSheet(windows_design.qss()); [c.schedule() for c in controllers]
                panel.apply_visual_theme(); stack_window.apply_visual_theme()
                app.processEvents(); report.setdefault("flip_ms", []).append(round((time.perf_counter() - started) * 1000, 1))
                grab(f"flip-{phase}-2-{second}")
        panel.hide(); stack_window.hide(); backdrop.hide()
        for ctrl in controllers:
            ctrl.close()
        if original_foreground:
            foreground(original_foreground)
        store.close()
    (out / f"{args.label}report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("build", "platform", "dpr", "system", "scale")}), len(report["captures"]), "capturas")


if __name__ == "__main__":
    main()
