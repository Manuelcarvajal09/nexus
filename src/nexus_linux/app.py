"""Entry point: tray icon (mirrors AppDelegate.setUpStatusItem), clipboard
monitor wiring, global shortcuts, and retention cleanup timer (Nexus's
Constants.retentionDays = 30).

Runs Qt's event loop through qasync instead of plain app.exec(). This is
required by GlobalShortcuts: kglobalaccel needs the exact same D-Bus
connection that registered a shortcut to stay open for the app's whole
lifetime (see global_shortcuts.py), and dbus_next -- the library that can
actually send the correctly-typed D-Bus arguments kglobalaccel expects --
is asyncio-based. qasync merges that asyncio loop with Qt's own so both run
on the same thread without blocking each other."""

import asyncio
import fcntl
import logging
import logging.handlers
import os
import sys
import signal
import time
from pathlib import Path

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import dbus_service, paths, styles, type_detection, product_ui, desktop
from .clipboard_monitor import ClipboardMonitor
from .evdev_stack_watcher import EvdevStackWatcher
from .global_shortcuts import GlobalShortcuts
from .paste_service import PasteService, paste_failure_message
from .preview_service import PreviewService
from .stack import ClipStack
from .stack_window_linux import StackWindow
from .storage import StorageService
from .settings import load_settings
from .timeline_window_linux import TimelineWindow

RETENTION_DAYS = 30
RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"


def _setup_logging() -> None:
    paths.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        # Rotation caps the file (it grew without limit); one previous file is kept.
        handlers=[logging.handlers.RotatingFileHandler(paths.LOG_PATH, maxBytes=1_000_000, backupCount=1, encoding="utf-8"),
                  logging.StreamHandler(sys.stderr)],
    )


def _acquire_single_instance_lock():
    """Enforces exactly one running Nexus process. Previously nothing did --
    two copies would run two clipboard monitors, two evdev keyboard watchers,
    and race to register the same global shortcuts/tray, all silently.

    A held flock() on a well-known file is the standard Linux single-instance
    pattern: unlike a PID file, the kernel releases it automatically the
    instant this process's file descriptor closes for any reason,
    including a crash or `kill -9` -- so there is no stale-lock cleanup to
    get wrong. The open file object must be kept alive (returned, not
    dropped) for as long as this instance should hold the lock."""
    log = logging.getLogger("nexus")
    paths.ensure_dirs()
    # "a+", not "w": opening for write alone truncates the file immediately,
    # even for a losing instance that's about to fail the lock -- which would
    # wipe the real owner's PID out of a file it doesn't actually hold.
    lock_file = open(paths.LOCK_PATH, "a+")
    try:
        fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning("Ya hay una instancia de Nexus en ejecución; saliendo.")
        lock_file.close()
        return None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _install_exception_hook() -> None:
    """PySide6's default behavior for an exception raised inside a Qt slot is
    to print it to stderr and otherwise carry on silently -- for a background
    app with no visible console, that's indistinguishable from the action
    just quietly not working. Route it through logging (same file as
    everything else) instead, so a bug leaves a real trace to debug from."""
    log = logging.getLogger("nexus")

    def handle(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error("Excepción no manejada", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = handle


async def _run(app: QApplication, close_event: asyncio.Event) -> None:
    log = logging.getLogger("nexus")

    settings = load_settings()
    from . import caelestia_theme
    caelestia_theme.apply()
    app.setStyleSheet(styles.qss() + product_ui.qss())
    storage = StorageService(allow_retention=False)
    log.info("Storage abierto en %s", paths.DB_PATH)

    tray_icon_path = RESOURCES_DIR / "nexus-tray.png"
    tray = QSystemTrayIcon(QIcon(str(tray_icon_path)))
    tray.setToolTip("Nexus")

    last_notice = [0.0]

    def notify_paste_failed(reason: str) -> None:
        log.warning("Paste sintético falló: %s", reason)
        # What happened + what to do (H09); bursts are rate-limited so the tray
        # is not flooded when every paste fails for the same reason.
        if time.monotonic() - last_notice[0] < 8:
            return
        last_notice[0] = time.monotonic()
        tray.showMessage("Nexus no pudo pegar", paste_failure_message(reason),
                         QSystemTrayIcon.MessageIcon.Warning, 6000)

    log.info("Captura enabled=%s; configuración=%s; producción=%s; retención=False",
             settings.capture_enabled, paths.CONFIG_DIR, paths.PRODUCTION)
    monitor = ClipboardMonitor(storage, enabled=settings.capture_enabled)
    app.aboutToQuit.connect(monitor.stop)
    watcher = EvdevStackWatcher()
    paste_service = PasteService(
        on_write=monitor.suppress_next_change,
        on_synthetic_paste=watcher.suppress_for,
        on_paste_failed=notify_paste_failed,
    )
    # One main surface owns History and Pinboards; Stack remains independent.
    preview_service = PreviewService(storage, parent=app, allow_network=settings.network_previews)
    timeline = TimelineWindow(storage, paste_service, preview_service)

    clip_stack = ClipStack(storage=storage, paste_service=paste_service)
    restored_count = clip_stack.load_persisted(arm=False)
    if restored_count:
        log.info("Stack restaurado tras el último cierre: %d item(s) pendientes.", restored_count)
    stack_window = StackWindow(clip_stack)
    from .sequential_stack import SequentialStack
    from .stack_hyprland import HyprlandStack
    sequential = SequentialStack(clip_stack, HyprlandStack())
    stack_window.sequential = sequential
    def stack_changed():
        stack_window.refresh()
        if not clip_stack.active and not clip_stack.items():
            stack_window.fade_hide()
    sequential.changed = stack_changed
    app.aboutToQuit.connect(sequential.pause)
    app.aboutToQuit.connect(lambda: desktop.save(timeline))
    app.aboutToQuit.connect(lambda: desktop.save(stack_window))

    # Global keyboard observation cannot prove application receipt on Wayland.
    # The legacy evdev watcher is intentionally not started, even for old settings.
    log.info("Stack: política de envío Hyprland con recuperación; sin acuse de aplicación. Evdev deshabilitado.")
    app.aboutToQuit.connect(watcher.stop)

    def on_captured(item):
        log.info("Capturado clip #%s tipo=%s", item.id, item.kind)
        # Prefetch link/YouTube metadata right away -- "URL detectada ->
        # guardar item inmediatamente -> mostrar fallback -> worker de
        # metadata -> cache -> actualizar card". By the time the user next
        # opens Ctrl+Shift+V, this has usually already finished in the
        # background, so the enriched card is there from the very first
        # paint instead of popping in every time. request() is cache-first
        # and returns immediately either way -- this never blocks capture.
        detected = type_detection.detect(item.kind, item.text_content)
        if detected in ("url", "youtube"):
            preview_service.request(item.id, item.text_content, is_youtube=(detected == "youtube"))
        if timeline.isVisible():
            timeline.refresh()
        if stack_window.is_collecting():
            stack_window.add_capture(item)

    monitor.captured.connect(on_captured)

    def show_timeline() -> None:
        if timeline.isVisible():
            timeline.fade_hide()
        else:
            paste_service.remember_target()
            timeline.show_and_focus()

    def show_stack() -> None:
        if stack_window.isVisible():
            stack_window.fade_hide()
        else:
            paste_service.remember_target()
            clip_stack.activate()
            sequential.enable()
            stack_window.show_and_focus()

    def show_library() -> None:
        paste_service.remember_target()
        timeline.show_pinboards()
        timeline.show_and_focus()

    timeline.library_requested.connect(show_library)

    menu = QMenu()
    open_action = menu.addAction("Abrir Nexus")
    open_action.triggered.connect(show_timeline)
    stack_action = menu.addAction("Nexus Stack")
    stack_action.triggered.connect(show_stack)
    manage_action = menu.addAction("Administrar pinboards y biblioteca…")
    manage_action.triggered.connect(show_library)
    menu.addSeparator()

    from .appearance import AppearanceDialog
    appearance = AppearanceDialog(apply=lambda key: apply_theme(key), parent=timeline, capture_changed=monitor.configure)
    timeline.appearance_requested.connect(appearance.show)
    appearance_action = menu.addAction("Apariencia…")
    appearance_action.triggered.connect(appearance.show)
    theme_menu = menu.addMenu("Tema")
    theme_actions = {}

    def apply_theme(key: str) -> None:
        styles.set_active_theme(key)
        styles.clear_icon_cache()
        app.setStyleSheet(styles.qss() + product_ui.qss())
        for action, action_key in theme_actions.items():
            action.setChecked(action_key == key)
        stack_window.refresh()
        timeline._board_panel.refresh()
        timeline.refresh()

    for theme_key, theme_label in styles.theme_choices():
        theme_action = theme_menu.addAction(theme_label)
        theme_action.setCheckable(True)
        theme_action.setChecked(theme_key == styles.active_theme().key)
        theme_action.triggered.connect(lambda _checked, k=theme_key: apply_theme(k))
        theme_actions[theme_action] = theme_key

    if True:
        last_palette = caelestia_theme.read_palette()
        def update_desktop_theme():
            nonlocal last_palette
            current_palette = caelestia_theme.read_palette()
            if current_palette == last_palette:
                return
            last_palette = current_palette
            if styles.active_theme().key in ("auto", "caelestia") and caelestia_theme.apply():
                styles.clear_icon_cache()
                app.setStyleSheet(styles.qss() + product_ui.qss())
                timeline.refresh()
                stack_window.refresh()
        theme_timer = QTimer(app)
        theme_timer.timeout.connect(update_desktop_theme)
        theme_timer.start(3000)

    menu.addSeparator()
    quit_action = menu.addAction("Salir")
    quit_action.triggered.connect(app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: show_timeline()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.show()

    dbus_svc = dbus_service.register(
        show_main_panel=show_timeline, show_stack=show_stack, parent=app,
        stack_paste=sequential.dispatch,
        stack_status=sequential.status,
    )
    if dbus_svc is not None:
        log.info(
            "Servicio D-Bus manual disponible (respaldo): "
            "qdbus6 %s %s ShowMainPanel | ShowStack",
            dbus_service.SERVICE_NAME, dbus_service.OBJECT_PATH,
        )

    shortcuts = GlobalShortcuts()
    if os.environ.get("NEXUS_PROFILE_ROOT") or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        log.info("Hyprland: atajos externos por D-Bus; no se modifica ningún bind de Caelestia.")
    elif await shortcuts.connect_bus():
        ok_stack = await shortcuts.register("toggle-stack", "Abrir Nexus Stack", "Ctrl+Shift+C")
        ok_panel = await shortcuts.register("toggle-main-panel", "Abrir historial de Nexus", "Ctrl+Shift+V")
        def on_shortcut(action_id: str) -> None:
            t0 = time.perf_counter()
            log.info("Atajo global disparado: %s", action_id)
            if action_id == "toggle-stack":
                show_stack()
            elif action_id == "toggle-main-panel":
                show_timeline()
            log.info(
                "shortcut: %s -> ventana lista en %.1fms",
                action_id, (time.perf_counter() - t0) * 1000,
            )

        shortcuts.triggered.connect(on_shortcut)
        log.info(
            "Atajos globales: Ctrl+Shift+C(Stack)=%s Ctrl+Shift+V(Historial)=%s",
            ok_stack, ok_panel,
        )
    else:
        log.warning("No se pudo conectar al bus D-Bus para atajos globales.")

    log.info("Retención deshabilitada: ningún clip se elimina por edad.")

    if os.environ.get("NEXUS_OPEN_ON_START") == "1":
        QTimer.singleShot(250, show_timeline)

    log.info("Nexus Linux iniciado")
    await close_event.wait()
    monitor.stop()
    watcher.stop()
    storage.close()


def main() -> int:
    os.umask(0o077)
    _setup_logging()
    _install_exception_hook()

    lock_file = _acquire_single_instance_lock()
    if lock_file is None:
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName("Nexus")
    app.setDesktopFileName("mx.com.storem.nexus")
    app.setQuitOnLastWindowClosed(False)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Exit when this session compositor disappears, also on desktops without
    # a managed graphical-session.target. Poll only metadata, every five seconds.
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    session_guard = QTimer(app)
    if signature and os.environ.get("XDG_RUNTIME_DIR"):
        socket = Path(os.environ["XDG_RUNTIME_DIR"]) / "hypr" / signature / ".socket.sock"
        session_guard.timeout.connect(lambda: app.quit() if not socket.exists() else None)
        session_guard.start(5000)

    # A bounded smoke test is allowed only in an explicitly isolated profile.
    if os.environ.get("NEXUS_PROFILE_ROOT") and os.environ.get("NEXUS_SMOKE_EXIT_MS"):
        smoke_ms = int(os.environ["NEXUS_SMOKE_EXIT_MS"])
        if not 1000 <= smoke_ms <= 60000:
            raise ValueError("Smoke timeout must be between 1000 and 60000 ms")
        QTimer.singleShot(smoke_ms, app.quit)

    # Without a full desktop platform-theme plugin, Qt resolves
    # QIcon.fromTheme() against the empty "hicolor" fallback instead of the
    # desktop's actual icon set -- confirmed live on Erwin: QIcon.themeName()
    # read back "hicolor" and every lookup (e.g. "text-x-generic") returned a
    # null icon even though /usr/share/icons/breeze has it. Must be set after
    # QApplication exists; styles.icon()'s kind badges depend on this.
    QIcon.setThemeName("breeze-dark")

    if "Inter" in QFontDatabase.families():
        app.setFont(QFont("Inter", 10))

    # Applied once, at the QApplication level, rather than per-window: Qt
    # automatically re-polishes every existing widget when this changes, so
    # switching themes later (see the tray "Tema" menu) is just calling this
    # again -- no need to track every open window to restyle it by hand.
    app.setStyleSheet(styles.qss() + product_ui.qss())

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    close_event = asyncio.Event()
    app.aboutToQuit.connect(close_event.set)

    with loop:
        loop.run_until_complete(_run(app, close_event))
    return 0


if __name__ == "__main__":
    sys.exit(main())
