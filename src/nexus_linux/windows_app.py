from __future__ import annotations

import logging, os, sys
from pathlib import Path
from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from . import paths, styles, type_detection
from .main_window import MainWindow
from .preview_service import PreviewService
from .stack import ClipStack
from .stack_window import StackWindow
from .storage import StorageService
from .timeline_window import TimelineWindow
from .windows_clipboard import WindowsClipboardMonitor
from .windows_paste import WindowsPasteService
from .windows_platform import WM_HOTKEY, VK_C, VK_V, foreground_window, register_hotkey, unregister_hotkey
from .windows_settings import load_settings, vk_for

PANEL_HOTKEY, STACK_HOTKEY = 1, 2
RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"

class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback): super().__init__(); self.callback = callback
    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            import ctypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY: self.callback(int(msg.wParam)); return True, 0
        return False, 0

def main():
    paths.ensure_dirs()
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(paths.LOG_PATH, encoding="utf-8")])
    app = QApplication(sys.argv); app.setApplicationName("Nexus")
    app.setQuitOnLastWindowClosed(False); app.setFont(QFont("Segoe UI", 10)); app.setStyleSheet(styles.qss())
    settings = load_settings()
    storage = StorageService(); monitor = WindowsClipboardMonitor(storage)
    paste = WindowsPasteService(on_write=monitor.suppress_next_change)
    preview = PreviewService(storage, parent=app); library = MainWindow(storage, paste); timeline = TimelineWindow(storage, paste, preview)
    stack = ClipStack(storage, paste); stack.load_persisted(); stack_window = StackWindow(stack)
    def captured(item):
        detected = type_detection.detect(item.kind, item.text_content)
        if detected in ("url", "youtube"): preview.request(item.id, item.text_content, is_youtube=detected == "youtube")
        for window in (library, timeline, stack_window):
            if window.isVisible(): window.refresh()
        if stack_window.is_collecting(): stack_window.add_capture(item)
    monitor.captured.connect(captured)
    def show_panel():
        paste.remember_target(); timeline.show_and_focus()
    def show_stack():
        paste.remember_target(); stack_window.show_and_focus()
    def hotkey(identifier):
        (show_panel if identifier == PANEL_HOTKEY else show_stack)()
    filter_ = HotkeyFilter(hotkey); app.installNativeEventFilter(filter_)
    # RegisterHotKey needs a real HWND so Windows delivers WM_HOTKEY to Qt's
    # native event dispatcher.  This host is intentionally never shown.
    hotkey_host = QWidget()
    hwnd = int(hotkey_host.winId())
    panel_vk, stack_vk = vk_for(settings["panel_hotkey"]), vk_for(settings["stack_hotkey"])
    if not register_hotkey(hwnd, PANEL_HOTKEY, panel_vk) or not register_hotkey(hwnd, STACK_HOTKEY, stack_vk):
        raise RuntimeError("No se pudieron registrar los atajos globales configurados")
    tray = QSystemTrayIcon(QIcon(str(RESOURCES_DIR / "nexus-tray.png"))); tray.setToolTip("Nexus")
    menu = QMenu(); menu.addAction("Abrir Nexus", show_panel); menu.addAction("Nexus Stack", show_stack); menu.addAction("Biblioteca", library.show_and_focus); menu.addSeparator(); menu.addAction("Salir", app.quit); tray.setContextMenu(menu); tray.show()
    app.aboutToQuit.connect(lambda: (unregister_hotkey(hwnd, PANEL_HOTKEY), unregister_hotkey(hwnd, STACK_HOTKEY)))
    if os.environ.get("NEXUS_OPEN_ON_START") == "1":
        QTimer.singleShot(250, show_panel)
    return app.exec()

if __name__ == "__main__": raise SystemExit(main())
