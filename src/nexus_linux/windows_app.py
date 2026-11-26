from __future__ import annotations

import hashlib, logging, logging.handlers, os, sys, time
from contextlib import ExitStack
from pathlib import Path
from PySide6.QtCore import QAbstractNativeEventFilter, QTimer, Qt, qVersion
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from .i18n import plural, tr
from . import i18n, paths, styles, type_detection
from . import windows_autostart, windows_design, windows_optical, windows_sandbox, windows_system_theme
from .windows_icons import app_icon, set_app_user_model_id, tray_icon
from .windows_motion import fade_in
from .windows_backdrop import BackdropController, NativeBackend
from .preview_service import PreviewService
from .windows_stack import WindowsClipStack, WindowsStackWindow
from .storage import StorageService
from .windows_timeline import WindowsTimelineWindow
from .windows_clipboard import WindowsClipboardMonitor
from .windows_paste import WindowsPasteService
from .windows_platform import (
    ERROR_HOTKEY_ALREADY_REGISTERED, WM_HOTKEY, acquire_single_instance_mutex,
    last_hotkey_error, register_hotkey, release_handle, unregister_hotkey,
)
from .windows_settings import load_settings, save_settings
from .windows_shortcuts import GLOBAL, SPECS, Result, ShortcutStore, parse_global
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

class GlobalHotkeys:
    """Owns the RegisterHotKey registrations of Nexus.

    A shortcut that cannot be registered never aborts startup: the default is
    tried, and whatever still fails is reported (notices + status) while the rest
    of the app keeps working. Changes apply live and roll back on failure."""

    IDS = {"panel": PANEL_HOTKEY, "stack": STACK_HOTKEY}

    def __init__(self, hwnd, store, save):
        self.hwnd, self.store, self._save = hwnd, store, save
        self.registered = {}   # shortcut id -> text currently registered with Windows
        self.failures = {}     # shortcut id -> why it is not registered
        self.notices = []      # startup problems worth showing to the user
        self._suspended = {}

    @staticmethod
    def _explain(code):
        if code == ERROR_HOTKEY_ALREADY_REGISTERED:
            return tr("Otra aplicación ya usa esa combinación.")
        return tr("Windows no permite registrar esa combinación (código {code}).", code=code)

    def _register(self, name, text):
        """None on success, otherwise the reason."""
        try:
            modifiers, key = parse_global(text)
        except ValueError as error:
            return str(error)
        if not register_hotkey(self.hwnd, self.IDS[name], key, modifiers):
            return self._explain(last_hotkey_error())
        self.registered[name] = text
        self.failures.pop(name, None)
        return None

    def _unregister(self, name):
        if self.registered.pop(name, None) is not None:
            unregister_hotkey(self.hwnd, self.IDS[name])

    def register_all(self):
        for name in self.IDS:
            label, text, default = ShortcutStore.spec(name).label, self.store.get(name), self.store.default(name)
            error = self._register(name, text)
            if error and text != default:
                fallback = self._register(name, default)
                if fallback is None:
                    self.store.reset(name)
                    self._save()
                    self.notices.append(tr("«{label}»: no se pudo usar {text} ({error}) Se usa {default}.", label=label, text=text, error=error, default=default))
                    continue
            if error:
                self.failures[name] = error
                self.notices.append(tr("«{label}» no está disponible: {error} Cámbiala en Configuración.", label=label, error=error))

    def change(self, name, text):
        result = self.store.validate(name, text)
        if not result.ok:
            return result
        if self._suspended:
            # Keys are released while Settings captures a combination: probe the
            # new one against Windows, then let resume() register it for real.
            error = self._register(name, result.value)
            if error:
                return Result(False, f"{result.value}: {error}")
            self._unregister(name)
            self._suspended[name] = result.value
        else:
            previous = self.registered.get(name)
            if previous != result.value:
                self._unregister(name)
                error = self._register(name, result.value)
                if error:
                    if previous is not None:
                        self._register(name, previous)
                    return Result(False, f"{result.value}: {error}")
        self.failures.pop(name, None)
        self.store.set(name, result.value)
        self._save()
        return result

    def reset(self, name):
        return self.change(name, self.store.default(name))

    def suspend(self):
        """Release every hotkey while a key is being captured in Settings."""
        if self._suspended:
            return
        self._suspended = dict(self.registered)
        for name in list(self.registered):
            self._unregister(name)

    def resume(self):
        pending, self._suspended = self._suspended, {}
        for name, text in pending.items():
            error = self._register(name, text)
            if error:
                self.failures[name] = error

    def close(self):
        for name in reversed(list(self.registered)):
            self._unregister(name)


class SettingsBackend:
    """What the Configuration page talks to: shortcuts, capture pause, themes."""

    def __init__(self, settings, store, hotkeys, timeline, notices=()):
        self.settings, self.store, self.hotkeys, self.timeline = settings, store, hotkeys, timeline
        self.startup_notices = list(notices)
        self._capture_listeners = []

    def _save(self):
        save_settings(self.settings)

    def change_shortcut(self, name, text):
        if ShortcutStore.spec(name).scope == GLOBAL and ShortcutStore.spec(name).editable:
            return self.hotkeys.change(name, text)
        result = self.store.set(name, text)
        if result.ok:
            self._save()
            self.timeline.apply_shortcuts()
        return result

    def reset_shortcut(self, name):
        return self.change_shortcut(name, self.store.default(name))

    def reset_all_shortcuts(self):
        """Returns the messages of the ones that could not be restored."""
        problems = []
        for spec in SPECS:
            if spec.editable and not self.store.is_default(spec.id):
                result = self.reset_shortcut(spec.id)
                if not result.ok:
                    problems.append(f"«{spec.label}»: {result.message}")
        return problems

    def status(self, name):
        return self.hotkeys.failures.get(name, "")

    def suspend_shortcuts(self):
        self.hotkeys.suspend()
        self.timeline.set_shortcuts_enabled(False)

    def resume_shortcuts(self):
        self.hotkeys.resume()
        self.timeline.set_shortcuts_enabled(True)

    def capture_paused(self):
        return bool(self.settings.get("paused"))

    def glass_optical(self):
        return bool(self.settings.get("glass_optical", True))

    def set_glass_optical(self, enabled):
        enabled = bool(enabled)
        if enabled != self.glass_optical():
            self.settings["glass_optical"] = enabled
            self._save()
        windows_optical.set_enabled(enabled)

    def language_choice(self):
        return str(self.settings.get("language", "auto"))

    def set_language_choice(self, code):
        if code in ("auto", *i18n.LANGUAGES) and code != self.language_choice():
            self.settings["language"] = code
            self._save()

    def restart(self):
        restart_application()

    def link_previews(self):
        return bool(self.settings.get("link_previews", True))

    def set_link_previews(self, enabled):
        enabled = bool(enabled)
        if enabled != self.link_previews():
            self.settings["link_previews"] = enabled
            self._save()
        for preview in getattr(self, "previews", ()):
            preview.network_enabled = enabled

    def autostart_enabled(self):
        return bool(self.settings.get("autostart", False))

    def set_autostart(self, enabled, registry=None):
        """Returns "" on success, or a message the Settings page shows (the setting is then left unchanged)."""
        enabled = bool(enabled)
        try:
            registry = registry or windows_autostart.default_registry()   # raises in sandbox mode: nothing is written
            windows_autostart.set_enabled(enabled, registry)
        except (windows_autostart.AutostartError, OSError) as exc:
            logging.getLogger("nexus.autostart").warning("autostart change failed: %s", type(exc).__name__)
            return tr("No se pudo cambiar el inicio con Windows. Inténtalo de nuevo.")
        self.settings["autostart"] = enabled
        self._save()
        return ""

    def on_capture_changed(self, callback):
        """Tray and Settings both show the pause state; either can change it."""
        self._capture_listeners.append(callback)

    def set_capture_paused(self, paused):
        paused = bool(paused)
        if paused == self.capture_paused():
            return
        self.settings["paused"] = paused
        self._save()
        for callback in list(self._capture_listeners):
            callback(paused)


_TRAY_TRIGGER = QSystemTrayIcon.ActivationReason.Trigger
_TRAY_WARNING = QSystemTrayIcon.MessageIcon.Warning


class TrayController:
    """The notification-area icon: quick actions only. Everything else lives in
    the panel (Configuración). Left click toggles the panel; the menu offers the
    panel, Pinboards, Stack, pausing capture, Configuración, theme and quit."""

    def __init__(self, app, backend, stack_window, *, show_panel, show_stack, show_pinboards, show_settings, apply_theme):
        self.backend, self.stack_window = backend, stack_window
        self._show_panel = show_panel
        self._suffix = " (sandbox)" if windows_sandbox.active() else ""
        self.tray = QSystemTrayIcon(tray_icon(backend.capture_paused()))
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        if not self.available:
            logging.getLogger("nexus.tray").warning("system tray not available")
        self.menu = QMenu()
        self.menu.addAction(tr("Abrir Nexus"), show_panel)
        self.menu.addAction(tr("Administrar pinboards y biblioteca…"), show_pinboards)
        self.stack_action = self.menu.addAction("Nexus Stack", show_stack)
        self.menu.addSeparator()
        self.capture_action = self.menu.addAction(tr("Pausar captura del portapapeles"), self._toggle_capture)
        self.menu.addSeparator()
        self.settings_action = self.menu.addAction(tr("Configuración…"), show_settings)
        self.shortcuts_action = self.menu.addAction(tr("Atajos de teclado…"), show_settings)
        theme_menu = self.menu.addMenu(tr("Estilo"))
        self.theme_actions = {}
        for key, label in windows_design.theme_choices():
            action = theme_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == styles.active_theme().key)
            action.triggered.connect(lambda _checked, key=key: apply_theme(key))
            self.theme_actions[action] = key
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction(tr("Salir de Nexus"), app.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        stack_window.collecting_changed.connect(lambda _active: self.refresh())
        backend.on_capture_changed(lambda _paused: self.refresh())
        # State changes refresh through the signals above; this slow tick only
        # catches visibility changes that emit nothing (it used to run 4x/s forever).
        self._timer = QTimer(app)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(2000)
        self._shown_state = None
        self.refresh()

    def _activated(self, reason):
        if reason == _TRAY_TRIGGER:
            self._show_panel()

    def _toggle_capture(self):
        self.backend.set_capture_paused(not self.backend.capture_paused())

    def set_theme_checked(self, key):
        for action, action_key in self.theme_actions.items():
            action.setChecked(action_key == key)

    def refresh(self):
        active = self.stack_window.is_collecting()
        hidden = active and not self.stack_window.isVisible()
        state = (active, hidden, self.backend.capture_paused())
        if state == getattr(self, "_shown_state", None):
            return   # nothing changed: no menu/tooltip/icon churn in explorer
        self._shown_state = state
        self.stack_action.setText(tr("Stack activo · oculto") if hidden else tr("Stack activo") if active else tr("Nexus Stack · en pausa"))
        paused = self.backend.capture_paused()
        self.capture_action.setText(tr("Reanudar captura del portapapeles") if paused else tr("Pausar captura del portapapeles"))
        self.tray.setToolTip(tr("Nexus · captura en pausa") + self._suffix if paused else "Nexus" + self._suffix)
        self.tray.setIcon(tray_icon(paused))

    NOTICE_INTERVAL_S = 8.0

    def notify_paste_failure(self, reason):
        """Tell the user when a paste did not happen (it used to fail silently, H01/H09).

        Cancellations are the user's own doing (dismiss, new copy) and stay quiet;
        repeated failures are rate-limited so a burst cannot flood the tray."""
        if not reason or reason == tr("Operación cancelada para conservar el portapapeles actual"):
            return
        now = time.monotonic()
        if now - getattr(self, "_last_notice", -1e9) < self.NOTICE_INTERVAL_S:
            return
        self._last_notice = now
        self.tray.showMessage("Nexus", reason, _TRAY_WARNING, 6000)

    def show(self, notices=()):
        self.tray.show()
        if notices:
            self.tray.showMessage("Nexus", "\n".join(notices), _TRAY_WARNING, 8000)

    def close(self):
        self._timer.stop()
        self.tray.hide()

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
    def __init__(self, callback, on_theme_message=None):
        super().__init__(); self.callback = callback; self.on_theme_message = on_theme_message
    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            import ctypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY: self.callback(int(msg.wParam)); return True, 0
            if self.on_theme_message is not None and msg.message == windows_system_theme.WM_SETTINGCHANGE:
                if windows_system_theme.is_theme_message(int(msg.message), windows_system_theme.parameter_of(int(msg.lParam))):
                    self.on_theme_message()
        return False, 0

RESTART_ENV = "NEXUS_RESTARTING"


def restart_application():
    """Start a new Nexus with the exact same command line, then quit this one.

    The new process waits (bounded) for this one to release the single-instance
    mutex instead of exiting at once. Works for `python -m`/`pythonw -c` launches
    and for the frozen Nexus.exe alike (sys.orig_argv holds the real command)."""
    from PySide6.QtCore import QProcess
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
    os.environ[RESTART_ENV] = "1"
    try:
        QProcess.startDetached(argv[0], argv[1:])
    finally:
        os.environ.pop(RESTART_ENV, None)
    QApplication.instance().quit()


def main():
    mutex = acquire_single_instance_mutex()
    if mutex is None and os.environ.pop(RESTART_ENV, None) == "1":
        for _ in range(50):   # the previous instance is shutting down: wait up to ~5 s
            time.sleep(0.1)
            mutex = acquire_single_instance_mutex()
            if mutex is not None:
                break
    if mutex is None:
        return 0
    # Unwind successful registrations even if a later startup step fails.
    with ExitStack() as lifetime:
        lifetime.callback(release_handle, mutex)
        return _run_windows(lifetime)

def _run_windows(lifetime):
    windows_sandbox.assert_isolated()
    paths.ensure_dirs()
    # Timestamps make intermittent failures traceable; rotation caps the file (it grew unbounded).
    handler = logging.handlers.RotatingFileHandler(paths.LOG_PATH, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(ClipboardPrivacyFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for source in ("windows_app.py", "windows_keyboard.py", "windows_stack.py", "windows_paste.py"):
        digest = hashlib.sha256(Path(__file__).with_name(source).read_bytes()).hexdigest()
        logging.getLogger("nexus.startup").info("source=%s sha256=%s pid=%s", source, digest, os.getpid())
    lifetime.callback(handler.close)
    lifetime.callback(root.removeHandler, handler)
    set_app_user_model_id()  # before any window: taskbar identity + icon are Nexus', not pythonw's
    app = QApplication(sys.argv); app.setApplicationName("Nexus"); app.setWindowIcon(app_icon())
    logging.getLogger("nexus.startup").info(
        "run platform=%s sandbox=%s data=%s code=%s", app.platformName(), windows_sandbox.active(),
        paths.DATA_DIR.parent, Path(__file__).resolve().parent)
    app.setQuitOnLastWindowClosed(False); app.setFont(windows_design.font()); app.setStyleSheet(windows_design.qss())
    settings = windows_sandbox.apply_settings(load_settings())
    windows_optical.set_enabled(settings.get("glass_optical", True))
    shortcut_store = ShortcutStore(settings, windows_sandbox.default_overrides())
    notices = shortcut_store.sanitize()
    if notices:
        save_settings(settings)
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
                "window=%s requested=%s applied=%s native_enabled=%s visual_verified=%s reason=%s hwnd=%#x readback=%s hresults=%s dark=%s",
                type(window).__name__, state.requested, state.applied, state.native_enabled, state.verified_visual,
                state.reason, state.hwnd, state.readback, state.hresults, windows_design.native_variant()[0],
            )
        controller = BackdropController(
            window, backend=native_backend,
            request="desktop_acrylic",
            can_native_paint=native_surface_matches_probe, on_state=update_surface,
            variant=windows_design.native_variant,
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
            controller.schedule()  # re-read the glass variant: dark/light Acrylic + border of the same HWND
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
    # Liquid Glass follows the Windows light/dark preference, live. Windows sends
    # WM_SETTINGCHANGE("ImmersiveColorSet"); Qt's colorSchemeChanged is a second trigger.
    # Nothing is written to Windows: the preference is only read.
    theme_watcher = windows_system_theme.SystemThemeWatcher(app)
    lifetime.callback(theme_watcher.close)
    if windows_sandbox.active():
        # Sandbox only: flip the Liquid Glass variant live by writing light|dark into
        # <sandbox config>/system-theme.txt (tools/windows_acceptance.py theme ...).
        # The real Windows setting is never touched; deleting the file returns to it.
        theme_file, from_file = paths.CONFIG_DIR / "system-theme.txt", [False]
        def poll_theme_file():
            try:
                value = theme_file.read_text(encoding="utf-8").strip().lower()
            except OSError:
                value = ""
            if value in ("light", "dark") and windows_system_theme.override() != value:
                from_file[0] = True
                windows_system_theme.set_override(value); theme_watcher.notify()
            elif value not in ("light", "dark") and from_file[0]:
                from_file[0] = False
                windows_system_theme.set_override(None); theme_watcher.notify()
        theme_poll = QTimer(app); theme_poll.timeout.connect(poll_theme_file); theme_poll.start(500)
        lifetime.callback(theme_poll.stop)
    filter_ = HotkeyFilter(hotkey, on_theme_message=theme_watcher.notify); app.installNativeEventFilter(filter_)
    lifetime.callback(app.removeNativeEventFilter, filter_)
    # RegisterHotKey needs a real HWND so Windows delivers WM_HOTKEY to Qt's
    # native event dispatcher.  This host is intentionally never shown.
    hotkey_host = QWidget()
    hwnd = int(hotkey_host.winId())
    hotkeys = GlobalHotkeys(hwnd, shortcut_store, lambda: save_settings(settings))
    lifetime.callback(hotkeys.close)
    hotkeys.register_all()
    notices += hotkeys.notices
    settings_backend = SettingsBackend(settings, shortcut_store, hotkeys, timeline, notices)
    settings_backend.previews = (preview,)
    preview.network_enabled = settings_backend.link_previews()
    try:   # keep an enabled entry pointing at THIS interpreter (follows upgrades/rollbacks); never creates one
        logging.getLogger("nexus.autostart").info("sync %s", windows_autostart.sync(bool(settings.get("autostart", False))))
    except Exception as exc:  # noqa: BLE001 - autostart must never prevent startup
        logging.getLogger("nexus.autostart").warning("sync failed: %s", type(exc).__name__)
    timeline.configure_settings(settings_backend)
    def show_pinboards():
        stack_window.fade_hide()
        paste.remember_target()
        if not timeline.isVisible(): timeline.show_and_focus()
        timeline.show_pinboard_management()
    def show_settings():
        stack_window.fade_hide()
        paste.remember_target()
        timeline.show_settings_management()
    tray_controller = None
    def apply_theme(key):
        styles.set_active_theme(key); styles.clear_icon_cache(); app.setStyleSheet(windows_design.qss())
        apply_native_theme_material()
        if tray_controller is not None: tray_controller.set_theme_checked(key)
        timeline.set_theme_selection(key)
        timeline.apply_visual_theme(); stack_window.apply_visual_theme()
    timeline.theme_requested.connect(apply_theme)
    def system_theme_changed(is_light):
        logging.getLogger("nexus.theme").info("system theme changed light=%s adaptive=%s", is_light, windows_design.is_adaptive())
        if not windows_design.is_adaptive():
            return  # the solid themes ignore the Windows preference
        apply_theme(styles.active_theme().key)
        if timeline.isVisible():
            fade_in(timeline._pages, windows_design.MOTION_THEME_MS, start=.55)  # content eases to the new variant
    theme_watcher.changed.connect(system_theme_changed)
    tray_controller = TrayController(
        app, settings_backend, stack_window, show_panel=show_panel, show_stack=show_stack,
        show_pinboards=show_pinboards, show_settings=show_settings, apply_theme=apply_theme,
    )
    tray_controller.show(notices)
    paste._on_paste_failed = tray_controller.notify_paste_failure
    lifetime.callback(tray_controller.close)
    app.aboutToQuit.connect(lifetime.close)
    if os.environ.get("NEXUS_OPEN_ON_START") == "1":
        QTimer.singleShot(250, show_panel)
    return app.exec()

if __name__ == "__main__": raise SystemExit(main())
