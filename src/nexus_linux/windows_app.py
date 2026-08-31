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
from .windows_platform import (
    WM_HOTKEY, acquire_single_instance_mutex, apply_liquid_glass_backdrop,
    register_hotkey, release_handle, unregister_hotkey,
)
from .windows_settings import load_settings, vk_for
from .windows_keyboard import WindowsPasteWatcher

PANEL_HOTKEY, STACK_HOTKEY = 1, 2
RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"

def effective_retention_days(settings) -> int:
    try:
        return max(30, int(settings.get("retention_days", 30)))
    except (TypeError, ValueError):
        return 30

class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback): super().__init__(); self.callback = callback
    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            import ctypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY: self.callback(int(msg.wParam)); return True, 0
        return False, 0

def main():
    mutex = acquire_single_instance_mutex()
    if mutex is None:
        return 0
    paths.ensure_dirs()
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(paths.LOG_PATH, encoding="utf-8")])
    app = QApplication(sys.argv); app.setApplicationName("Nexus")
    app.setQuitOnLastWindowClosed(False); app.setFont(QFont("Segoe UI", 10)); app.setStyleSheet(styles.qss())
    settings = load_settings()
    storage = StorageService()
    retention_days = effective_retention_days(settings)
    purged = storage.purge_older_than(retention_days)
    logging.getLogger("nexus.retention").info("startup days=%s purged=%s", retention_days, purged)
    retention_timer = QTimer(app)
    retention_timer.timeout.connect(lambda: logging.getLogger("nexus.retention").info(
        "periodic days=%s purged=%s", retention_days, storage.purge_older_than(retention_days)
    ))
    retention_timer.start(6 * 60 * 60 * 1000)
    monitor = WindowsClipboardMonitor(storage, settings)
    paste = WindowsPasteService(on_write=monitor.suppress_next_change)
    preview = PreviewService(storage, parent=app); library = MainWindow(storage, paste); timeline = TimelineWindow(storage, paste, preview)
    stack = ClipStack(storage, paste); stack.load_persisted(); stack_window = StackWindow(stack)
    themed_windows = (library, timeline, stack_window)
    def apply_native_theme_material():
        enabled = styles.active_theme().liquid_glass
        for window in themed_windows:
            apply_liquid_glass_backdrop(int(window.winId()), enabled)
    apply_native_theme_material()
    timeline.manage_pinboards_requested.connect(library.show_and_focus)
    paste_watcher = WindowsPasteWatcher(app)
    def consume_real_stack_paste():
        if stack_window.is_collecting() and stack_window.has_pending_items():
            QTimer.singleShot(180, lambda: (stack.consume_after_real_paste(), stack_window.refresh()))
    paste_watcher.paste_detected.connect(consume_real_stack_paste)
    paste_watcher.start()
    def captured(item):
        detected = type_detection.detect(item.kind, item.text_content)
        if detected in ("url", "youtube"): preview.request(item.id, item.text_content, is_youtube=detected == "youtube")
        for window in (library, timeline, stack_window):
            if window.isVisible(): window.refresh()
        if stack_window.is_collecting(): stack_window.add_capture(item)
    monitor.captured.connect(captured)
    def show_panel():
        if timeline.isVisible(): timeline.fade_hide()
        else:
            if stack_window.isVisible(): stack_window.fade_hide()
            paste.remember_target(); timeline.show_and_focus()
    def show_stack():
        if stack_window.isVisible(): stack_window.fade_hide()
        else:
            if timeline.isVisible(): timeline.fade_hide()
            paste.remember_target(); stack_window.start_collecting(); stack_window.show_and_focus()
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
    menu = QMenu()
    menu.addAction("Abrir Nexus", show_panel)
    menu.addAction("Nexus Stack", show_stack)
    menu.addAction("Administrar pinboards y biblioteca…", library.show_and_focus)
    menu.addSeparator()
    theme_menu = menu.addMenu("Estilo")
    theme_actions = {}
    def apply_theme(key):
        styles.set_active_theme(key); styles.clear_icon_cache(); app.setStyleSheet(styles.qss())
        apply_native_theme_material()
        for action, action_key in theme_actions.items(): action.setChecked(action_key == key)
        timeline.set_theme_selection(key)
        library.refresh(); library.refresh_sidebar_icons(); timeline.refresh(); stack_window.refresh()
    for theme_key, theme_label in styles.theme_choices():
        action = theme_menu.addAction(theme_label); action.setCheckable(True)
        action.setChecked(theme_key == styles.active_theme().key)
        action.triggered.connect(lambda _checked, key=theme_key: apply_theme(key))
        theme_actions[action] = theme_key
    timeline.theme_requested.connect(apply_theme)
    menu.addSeparator(); menu.addAction("Salir de Nexus", app.quit)
    tray.setContextMenu(menu); tray.show()
    app.aboutToQuit.connect(lambda: (paste_watcher.stop(), unregister_hotkey(hwnd, PANEL_HOTKEY), unregister_hotkey(hwnd, STACK_HOTKEY), release_handle(mutex)))
    if os.environ.get("NEXUS_OPEN_ON_START") == "1":
        QTimer.singleShot(250, show_panel)
    return app.exec()

if __name__ == "__main__": raise SystemExit(main())
