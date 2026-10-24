"""Authorized real-screen layout review of synthetic Nexus Windows views.

No clipboard, hooks, hotkeys, singleton, network, or user database. Captures
screen pixels only within the owned panel over an owned synthetic backdrop.
--baseline-directory loads the backed-up Windows layout modules for comparison.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import ctypes
from ctypes import wintypes

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", required=True)
parser.add_argument("--baseline-directory")
parser.add_argument("--backdrop", choices=("pattern", "neutral"), default="pattern")
parser.add_argument("--inactive", action="store_true", help="Also capture each real panel without focus")
parser.add_argument("--theme", default="liquid_glass", choices=("liquid_glass","midnight_teal","minimalist","storem","tech_neon"))
args = parser.parse_args()
output = Path(args.output).resolve(); output.mkdir(parents=True, exist_ok=False)
os.environ["LOCALAPPDATA"] = str(output / "sandbox")
os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import nexus_linux
if args.baseline_directory:
    for name in ("windows_design", "windows_backdrop", "windows_material", "windows_timeline", "windows_stack"):
        filename = Path(args.baseline_directory) / "src/nexus_linux" / (name + ".py")
        if not filename.exists():
            continue  # Older layout-only backups do not include the backend.
        spec = importlib.util.spec_from_file_location("nexus_linux." + name, filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        setattr(nexus_linux, name, module)
        spec.loader.exec_module(module)

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication, QLabel
from nexus_linux import styles, windows_design
from nexus_linux.storage import StorageService
from nexus_linux.windows_timeline import WindowsTimelineWindow
from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow
from nexus_linux.windows_backdrop import BackdropController, NativeBackend
from nexus_linux.windows_platform import activate_foreground_window
from windows_nexus_material_probe import (
    SyntheticBackdrop, PasteStub, synthetic_image, safe_capture,
    put_owned_window_topmost, foreground_owned_window, restore_foreground, user32,
)


def rectangle(widget, origin):
    point = widget.mapTo(origin, QPoint(0, 0))
    return [point.x(), point.y(), widget.width(), widget.height()]


def main():
    original = int(user32.GetForegroundWindow() or 0)
    report = {"platform": "windows", "capture": "real_screen_pixels_NOT_widget_render",
              "foreground_before": original,
              "data": "synthetic sandbox", "clipboard_access": False, "captures": [], "material": []}
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    styles._active_key = args.theme
    app.setFont(windows_design.font()); app.setStyleSheet(windows_design.qss())
    screen = app.primaryScreen()
    if screen is None:
        raise RuntimeError("No Windows desktop screen")
    from PySide6.QtCore import qVersion
    report.update({"os_build": sys.getwindowsversion().build, "qt_version": qVersion(),
                   "screen_dpr": screen.devicePixelRatio(),
                   "logical_dpi": screen.logicalDotsPerInch(),
                   "available_logical": [screen.availableGeometry().x(), screen.availableGeometry().y(),
                                         screen.availableGeometry().width(), screen.availableGeometry().height()]})
    class NeutralBackdrop(SyntheticBackdrop):
        def paintEvent(self, event):
            painter = QPainter(self)
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0, QColor("#151B24"))
            gradient.setColorAt(1, QColor("#26364A"))
            painter.fillRect(self.rect(), gradient)
            painter.end()
    backdrop = NeutralBackdrop(screen) if args.backdrop == "neutral" else SyntheticBackdrop(screen)
    backdrop.phase = 2
    backdrop.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)
    report["synthetic_backdrop"] = args.backdrop
    backdrop.show(); put_owned_window_topmost(backdrop)
    store = StorageService(output / "sandbox" / "synthetic.sqlite")
    controllers = []
    windows = []
    try:
        board = store.create_pinboard("Estudio creativo", "#80B5EE", "✦")
        store.create_pinboard("Referencias", "#BBA8FF")
        text = store.capture("text", text_content="Diseña con intención.\nGuarda lo importante y vuelve a tus ideas.", source_app="Editor")
        image = store.capture("image", image_data=synthetic_image(), source_app="Recortes")
        code = store.capture("text", text_content="const ideas = create();\nreturn ideas.organize();", source_app="Code")
        store.capture("url", text_content="https://example.invalid/studio", source_app="Browser")
        store.add_to_pinboard(text.id, board.id); store.add_to_pinboard(image.id, board.id)
        panel = WindowsTimelineWindow(store, PasteStub())
        panel.resolve_target_screen = lambda: screen
        stack = WindowsClipStack(store, PasteStub(), sequence=lambda: 1)
        store.save_stack_state([text.id, image.id, code.id], False)
        stack.load_persisted()
        stack_window = WindowsStackWindow(stack)
        windows.extend([panel, stack_window])
        for window in windows:
            def changed(state, window=window):
                window._surface.set_native_backdrop(state.native_enabled)
                window.update()
                report["material"].append({"window": type(window).__name__, "native_enabled": state.native_enabled,
                                           "applied": state.applied, "reason": state.reason})
            controller = BackdropController(window, backend=NativeBackend(), request="desktop_acrylic",
                can_native_paint=lambda w: bool(sys.getwindowsversion().build == 26300
                    and not w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                    and w.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)), on_state=changed)
            # Match the real app: motion must not add WS_EX_LAYERED by fading
            # the HWND, and nativeEvent must forward settings to this controller.
            window._backdrop_controller = controller
            controllers.append(controller); window.winId(); controller.schedule()
        QTest.qWait(200)

        def capture(name, window, active=True):
            window.raise_()
            if not foreground_owned_window(window if active else backdrop):
                raise RuntimeError("Cannot establish owned foreground state")
            app.processEvents(); QTest.qWait(250)
            # Keep both the panel and what Acrylic samples behind it owned.
            # Check native pixel rectangles, not mixed logical/DPI coordinates.
            put_owned_window_topmost(backdrop); put_owned_window_topmost(window)
            app.processEvents(); QTest.qWait(60)
            target = int(window.winId()); background = int(backdrop.winId())
            if (int(user32.GetForegroundWindow() or 0) == target) != active:
                raise RuntimeError("Foreground state does not match capture request")
            bounds = wintypes.RECT(); user32.GetWindowRect(target, ctypes.byref(bounds))
            seen, blockers = [], []
            enumtype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            @enumtype
            def visit(hwnd, unused):
                if int(hwnd) == target:
                    seen.append("panel")
                if int(hwnd) == background:
                    seen.append("background")
                    return False
                if user32.IsWindowVisible(hwnd):
                    pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    if pid.value != os.getpid() and (rect.left < bounds.right and rect.right > bounds.left
                            and rect.top < bounds.bottom and rect.bottom > bounds.top):
                        blockers.append({"pid": int(pid.value), "hwnd": int(hwnd)})
                return True
            user32.EnumWindows(visit, 0)
            if blockers or seen != ["panel", "background"]:
                report["capture_blocked"] = {"name": name, "owned_order": seen, "blockers": blockers}
                raise RuntimeError("Capture refused: panel and synthetic backdrop not safely on top")
            geo = window.frameGeometry()
            shot = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
            if shot.isNull() or not shot.save(str(output / (name + ".png"))):
                raise RuntimeError("Desktop capture failed")
            corner_size = round(48 * shot.devicePixelRatio())
            for corner, x, y in (("tl", 0, 0), ("tr", shot.width()-corner_size, 0),
                                  ("bl", 0, shot.height()-corner_size),
                                  ("br", shot.width()-corner_size, shot.height()-corner_size)):
                shot.copy(x, y, corner_size, corner_size).save(str(output / (name + "-" + corner + ".png")))
            margins = window._surface.layout().contentsMargins()
            row = {"name": name, "file": name + ".png", "geometry": [geo.x(), geo.y(), geo.width(), geo.height()],
                   "active": active, "qt_active": window.isActiveWindow(),
                   "pixels": [shot.width(), shot.height()], "dpr": shot.devicePixelRatio(),
                   "margins": [margins.left(), margins.top(), margins.right(), margins.bottom()],
                    "root_spacing": window._surface.layout().spacing()}
            row["native_style"] = controllers[windows.index(window)].backend.api.window_style(target)
            if row["native_style"]["layered"]:
                raise RuntimeError("Capture invalid: layered HWND does not match production Acrylic profile")
            if window is stack_window:
                row["remaining_scroll_max"] = stack_window._list.verticalScrollBar().maximum()
            if window is panel:
                row["sidebar_rect"] = rectangle(panel._sidebar, window)
                row["right_rect"] = rectangle(panel._editor_scroll, window)
                row["known_title_labels"] = [{"text": label.text(), "rect": rectangle(label, window),
                     "in_editor": panel._editor_scroll.isAncestorOf(label),
                     "in_sidebar": panel._sidebar.isAncestorOf(label)}
                    for label in panel.findChildren(QLabel)
                    if label.isVisible() and label.text() in ("Nuevo pinboard", "Estudio creativo")]
            report["captures"].append(row)
            if active and args.inactive:
                capture(name + "-inactive", window, active=False)

        panel.show_and_focus(); capture("history", panel)
        panel._navigate_board(board.id); capture("pinboard-content", panel)
        panel._navigate_board(None); panel.show_pinboard_manager(); capture("pinboards-empty", panel)
        panel._sidebar._add_btn.click(); capture("pinboard-create", panel)
        panel._open_editor(board.id); capture("pinboard-edit", panel)
        panel.hide()
        fixtures = [text, image, code]
        for index in range(18):
            fixtures.append(store.capture("text", text_content=f"Referencia visual {index+1}: conserva proporciones y jerarquía.", source_app="Editor"))
        for count, name in ((0, "stack-empty"), (1, "stack-one"), (2, "stack-two"),
                            (6, "stack-several"), (21, "stack-scroll")):
            # Fixture persistence is confined to the explicitly sandboxed store;
            # no real clipboard operations or capture signals are generated.
            store.save_stack_state([item.id for item in fixtures[:count]], False)
            stack.load_persisted()
            stack_window.show_and_focus(); capture(name, stack_window)
            stack_window.hide()
        report["sqlite_integrity"] = store._conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        for controller in controllers:
            controller.close()
        for window in windows:
            window.hide(); window.close(); window.deleteLater()
        backdrop.hide(); backdrop.close(); app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        store.close()
        report["foreground_restored"] = bool(activate_foreground_window(original))
        report["foreground_after"] = int(user32.GetForegroundWindow() or 0)
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
