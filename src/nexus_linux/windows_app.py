from __future__ import annotations

import hashlib, logging, os, sys
from contextlib import ExitStack
from pathlib import Path
from PySide6.QtCore import QAbstractNativeEventFilter, QTimer, Qt, qVersion
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from . import paths, styles, type_detection
from . import windows_design
from .windows_backdrop import BackdropController, NativeBackend
from .preview_service import PreviewService
from .windows_stack import WindowsClipStack, WindowsStackWindow
from .storage import StorageService
from .windows_timeline import WindowsTimelineWindow
from .windows_clipboard import WindowsClipboardMonitor
from .windows_paste import WindowsPasteService
from .windows_platform import (
    WM_HOTKEY, acquire_single_instance_mutex,
    register_hotkey, release_handle, unregister_hotkey,
)
from .windows_settings import load_settings, vk_for
from .windows_keyboard import WindowsPasteWatcher

PANEL_HOTKEY, STACK_HOTKEY = 1, 2
RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"

class StackCaptureGate:
    """Exclude delayed reads begun outside the current explicit session."""
    def __init__(self, monitor):
        self.monitor = monitor
        self.baseline = getattr(monitor, "_generation", None)

    def activated(self):
        self.baseline = getattr(self.monitor, "_generation", None)

    def accepts(self):
        generation = getattr(self.monitor, "_generation", None)
        return self.baseline is None or generation is None or generation > self.baseline

class ClipboardPrivacyFilter(logging.Filter):
    """Shared preview code logs URLs/exceptions; never persist those on Windows."""
    def filter(self, record):
        if record.name.startswith("nexus.preview"):
            record.msg = "preview.event (private source and error details omitted)"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True

def effective_retention_days(settings) -> int:
    try:
        return max(30, int(settings.get("retention_days", 30)))
    except (TypeError, ValueError):
        return 30

def register_configured_hotkeys(hwnd, settings, lifetime):
    panel_vk, stack_vk = vk_for(settings["panel_hotkey"]), vk_for(settings["stack_hotkey"])
    if panel_vk == stack_vk:
        raise ValueError("Los atajos de historial y Stack deben ser distintos")
    for identifier, key in ((PANEL_HOTKEY, panel_vk), (STACK_HOTKEY, stack_vk)):
        if not register_hotkey(hwnd, identifier, key):
            raise RuntimeError("No se pudieron registrar los atajos globales configurados")
        lifetime.callback(unregister_hotkey, hwnd, identifier)

def toggle_stack_window(stack_window, timeline, paste):
    """Explicit hotkey is the only path that opens Stack and enables capture.

    Hiding or closing always disables capture, including after a paste-hide.
    Show first, then activate, so no clipboard event can be accepted in the
    interval before the overlay is actually visible.
    """
    if stack_window.isVisible() and not stack_window.is_dismissing():
        stack_window.fade_hide()
        return False
    if timeline.isVisible():
        timeline.fade_hide()
    paste.remember_target()
    stack_window.show_and_focus()
    stack_window.start_collecting()
    return True

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
    # Unwind successful registrations even if a later startup step fails.
    with ExitStack() as lifetime:
        lifetime.callback(release_handle, mutex)
        return _run_windows(lifetime)

def _run_windows(lifetime):
    paths.ensure_dirs()
    handler = logging.FileHandler(paths.LOG_PATH, encoding="utf-8")
    handler.addFilter(ClipboardPrivacyFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for source in ("windows_app.py", "windows_keyboard.py", "windows_stack.py", "windows_paste.py"):
        digest = hashlib.sha256(Path(__file__).with_name(source).read_bytes()).hexdigest()
        logging.getLogger("nexus.startup").info("source=%s sha256=%s pid=%s", source, digest, os.getpid())
    lifetime.callback(handler.close)
    lifetime.callback(root.removeHandler, handler)
    app = QApplication(sys.argv); app.setApplicationName("Nexus")
    app.setQuitOnLastWindowClosed(False); app.setFont(windows_design.font()); app.setStyleSheet(windows_design.qss())
    settings = load_settings()
    storage = StorageService()
    lifetime.callback(storage.close)
    retention_days = effective_retention_days(settings)
    purged = storage.purge_older_than(retention_days)
    logging.getLogger("nexus.retention").info("startup days=%s purged=%s", retention_days, purged)
    retention_timer = QTimer(app)
    retention_timer.timeout.connect(lambda: logging.getLogger("nexus.retention").info(
        "periodic days=%s purged=%s", retention_days, storage.purge_older_than(retention_days)
    ))
    retention_timer.start(6 * 60 * 60 * 1000)
    lifetime.callback(retention_timer.stop)
    monitor = WindowsClipboardMonitor(storage, settings)
    lifetime.callback(monitor.stop)
    paste = WindowsPasteService(on_write=monitor.suppress_next_change)
    preview = PreviewService(storage, parent=app)
    timeline = WindowsTimelineWindow(storage, paste, preview)
    stack = WindowsClipStack(storage, paste); stack.load_persisted(); stack_window = WindowsStackWindow(stack)
    themed_windows = (timeline, stack_window)

    def native_surface_matches_probe(window):
        # The successful real-screen probe was run against this exact platform
        # profile. Other OS/Qt combinations still get satin fallback until a
        # native capture validates their compositor behavior.
        return bool(
            sys.platform == "win32"
            and sys.getwindowsversion().build == 26300
            and qVersion() == "6.12.0"
            and not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            and window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
            and window.windowOpacity() == 1.0
        )

    backdrops = {}
    native_backend = NativeBackend()
    def attach_backdrop(window):
        def update_surface(state):
            window._backdrop_state = state
            window._surface.set_native_backdrop(state.native_enabled)
            window.update()
            logging.getLogger("nexus.backdrop").info(
                "window=%s requested=%s applied=%s native_enabled=%s visual_verified=%s reason=%s hwnd=%#x readback=%s hresults=%s",
                type(window).__name__, state.requested, state.applied, state.native_enabled, state.verified_visual,
                state.reason, state.hwnd, state.readback, state.hresults,
            )
        controller = BackdropController(
            window, backend=native_backend,
            request="desktop_acrylic",
            can_native_paint=native_surface_matches_probe, on_state=update_surface,
        )
        window._backdrop_controller = controller
        backdrops[window] = controller
        # Create once now: prepare HWND/style/material before first display,
        # never toggle window flags or re-create the HWND during theme changes.
        window.winId()
        controller.schedule()
        lifetime.callback(controller.close)

    for window in themed_windows:
        attach_backdrop(window)

    def apply_native_theme_material():
        request = "desktop_acrylic"
        for controller in backdrops.values():
            controller.set_request(request)
    apply_native_theme_material()
    paste_watcher = WindowsPasteWatcher(app)
    lifetime.callback(paste_watcher.stop)
    lifetime.callback(stack_window.stop_collecting)
    paste_watcher.paste_detected.connect(stack_window.schedule_real_paste)
    # Keep the hook installed while paused so an already swallowed V release
    # cannot leak through; disabled chords otherwise follow the native path.
    watcher_failed = False
    capture_gate = StackCaptureGate(monitor)
    def enable_watcher(active):
        if active:
            capture_gate.activated()
        if active and watcher_failed:
            stack_window.stop_collecting()
            stack_window._subtitle.setText("Stack detenido: Ctrl+V no disponible")
        elif hasattr(paste_watcher, "set_enabled"):
            paste_watcher.set_enabled(active)
        elif active:
            paste_watcher.start()
        else:
            paste_watcher.stop()
    stack_window.collecting_changed.connect(enable_watcher)
    def hook_failed(reason):
        nonlocal watcher_failed
        watcher_failed = True
        stack_window.stop_collecting()
        stack_window._subtitle.setText(reason)
    if hasattr(paste_watcher, "hook_failed"):
        paste_watcher.hook_failed.connect(hook_failed)
    if hasattr(paste_watcher, "set_enabled"):
        paste_watcher.start(enabled=False)
    def captured(item):
        detected = type_detection.detect(item.kind, item.text_content)
        if detected in ("url", "youtube"): preview.request(item.id, item.text_content, is_youtube=detected == "youtube")
        if timeline.isVisible(): timeline.refresh()
    monitor.captured.connect(captured)
    def captured_for_stack(item, sequence):
        # Delayed image rendering may advance the clipboard sequence without
        # starting a new capture. Such an inactive-session read remains History
        # only, even if its final signal arrives after explicit activation.
        if capture_gate.accepts():
            stack_window.add_capture(item, sequence)
    monitor.captured_versioned.connect(captured_for_stack)
    def show_panel():
        if timeline.isVisible() and not timeline.is_dismissing(): timeline.fade_hide()
        else:
            if stack_window.is_collecting() or stack_window.isVisible(): stack_window.fade_hide()
            paste.remember_target(); timeline.show_and_focus()
    def show_stack():
        toggle_stack_window(stack_window, timeline, paste)
    def hotkey(identifier):
        if identifier == PANEL_HOTKEY: show_panel()
        elif identifier == STACK_HOTKEY: show_stack()
    filter_ = HotkeyFilter(hotkey); app.installNativeEventFilter(filter_)
    lifetime.callback(app.removeNativeEventFilter, filter_)
    # RegisterHotKey needs a real HWND so Windows delivers WM_HOTKEY to Qt's
    # native event dispatcher.  This host is intentionally never shown.
    hotkey_host = QWidget()
    hwnd = int(hotkey_host.winId())
    register_configured_hotkeys(hwnd, settings, lifetime)
    tray = QSystemTrayIcon(QIcon(str(RESOURCES_DIR / "nexus-tray.png"))); tray.setToolTip("Nexus")
    menu = QMenu()
    menu.addAction("Abrir Nexus", show_panel)
    stack_action = menu.addAction("Nexus Stack", show_stack)
    def update_stack_label():
        active = stack_window.is_collecting()
        hidden = active and not stack_window.isVisible()
        stack_action.setText("Stack activo · oculto" if hidden else "Stack activo" if active else "Nexus Stack · en pausa")
    stack_window.collecting_changed.connect(lambda _active: update_stack_label())
    tray_state_timer = QTimer(app)
    tray_state_timer.timeout.connect(update_stack_label)
    tray_state_timer.start(250)
    lifetime.callback(tray_state_timer.stop)
    update_stack_label()
    def show_pinboards():
        stack_window.fade_hide()
        paste.remember_target()
        if not timeline.isVisible(): timeline.show_and_focus()
        timeline.show_pinboard_management()
    menu.addAction("Administrar pinboards y biblioteca…", show_pinboards)
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
    lifetime.callback(tray.hide)
    app.aboutToQuit.connect(lifetime.close)
    if os.environ.get("NEXUS_OPEN_ON_START") == "1":
        QTimer.singleShot(250, show_panel)
    return app.exec()

if __name__ == "__main__": raise SystemExit(main())
