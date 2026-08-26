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
import os
import sys
import time
from pathlib import Path

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import dbus_service, paths, styles
from .clipboard_monitor import ClipboardMonitor
from .evdev_stack_watcher import EvdevStackWatcher
from .global_shortcuts import GlobalShortcuts
from .main_window import MainWindow
from .paste_service import PasteService
from .stack import ClipStack
from .stack_window import StackWindow
from .storage import StorageService

RETENTION_DAYS = 30
RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"


def _setup_logging() -> None:
    paths.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(paths.LOG_PATH), logging.StreamHandler(sys.stderr)],
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

    storage = StorageService()
    log.info("Storage abierto en %s", paths.DB_PATH)

    tray_icon_path = RESOURCES_DIR / "nexus-tray.png"
    tray = QSystemTrayIcon(QIcon(str(tray_icon_path)))
    tray.setToolTip("Nexus")

    def notify_paste_failed(reason: str) -> None:
        log.warning("Paste sintético falló: %s", reason)
        tray.showMessage(
            "Nexus no pudo pegar",
            "El elemento se copió al portapapeles, pero no se pudo escribir automáticamente. Pégalo con Ctrl+V.",
            QSystemTrayIcon.MessageIcon.Warning, 4000,
        )

    monitor = ClipboardMonitor(storage)
    watcher = EvdevStackWatcher()
    paste_service = PasteService(
        on_write=monitor.suppress_next_change,
        on_synthetic_paste=watcher.suppress_for,
        on_paste_failed=notify_paste_failed,
    )
    window = MainWindow(storage, paste_service)

    clip_stack = ClipStack(storage=storage, paste_service=paste_service)
    stack_window = StackWindow(clip_stack)

    def on_real_paste_detected():
        if stack_window.is_collecting() and stack_window.has_pending_items():
            item = clip_stack.consume_after_real_paste()
            log.info("Ctrl+V real detectado -> consumido del Stack: #%s", item.id if item else None)
            stack_window.refresh()
            if not stack_window.has_pending_items():
                stack_window.fade_hide()

    watcher.paste_detected.connect(on_real_paste_detected)
    if watcher.start():
        log.info("Auto-avance del Stack activo (watcher de teclado real, grupo 'input' confirmado).")
    else:
        log.info("Auto-avance del Stack no disponible; usa \"Pegar siguiente (manual)\".")
    app.aboutToQuit.connect(watcher.stop)

    def on_captured(item):
        log.info("Capturado clip #%s tipo=%s", item.id, item.kind)
        if window.isVisible():
            window.refresh()
        if stack_window.is_collecting():
            stack_window.add_capture(item)

    monitor.captured.connect(on_captured)

    menu = QMenu()
    open_action = menu.addAction("Abrir Nexus")
    open_action.triggered.connect(window.show_and_focus)
    stack_action = menu.addAction("Nexus Stack")
    stack_action.triggered.connect(stack_window.show_and_focus)
    menu.addSeparator()

    theme_menu = menu.addMenu("Tema")
    theme_actions = {}

    def apply_theme(key: str) -> None:
        styles.set_active_theme(key)
        styles.clear_icon_cache()
        app.setStyleSheet(styles.qss())
        for action, action_key in theme_actions.items():
            action.setChecked(action_key == key)
        window.refresh()
        stack_window.refresh()
        window.refresh_sidebar_icons()

    for theme_key, theme_label in styles.theme_choices():
        theme_action = theme_menu.addAction(theme_label)
        theme_action.setCheckable(True)
        theme_action.setChecked(theme_key == styles.active_theme().key)
        theme_action.triggered.connect(lambda _checked, k=theme_key: apply_theme(k))
        theme_actions[theme_action] = theme_key

    menu.addSeparator()
    quit_action = menu.addAction("Salir")
    quit_action.triggered.connect(app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.show_and_focus()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.show()

    dbus_svc = dbus_service.register(
        show_main_panel=window.show_and_focus, show_stack=stack_window.show_and_focus, parent=app,
    )
    if dbus_svc is not None:
        log.info(
            "Servicio D-Bus manual disponible (respaldo): "
            "qdbus6 %s %s ShowMainPanel | ShowStack",
            dbus_service.SERVICE_NAME, dbus_service.OBJECT_PATH,
        )

    shortcuts = GlobalShortcuts()
    if await shortcuts.connect_bus():
        ok_stack = await shortcuts.register("toggle-stack", "Abrir Nexus Stack", "Ctrl+Shift+C")
        ok_panel = await shortcuts.register("toggle-main-panel", "Abrir historial de Nexus", "Ctrl+Shift+V")
        def on_shortcut(action_id: str) -> None:
            t0 = time.perf_counter()
            log.info("Atajo global disparado: %s", action_id)
            if action_id == "toggle-stack":
                if stack_window.isVisible():
                    stack_window.fade_hide()
                else:
                    stack_window.show_and_focus()
            elif action_id == "toggle-main-panel":
                if window.isVisible():
                    window.fade_hide()
                else:
                    window.show_and_focus()
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

    retention_timer = QTimer()
    retention_timer.timeout.connect(lambda: log.info(
        "Purga de retención: %s elementos eliminados", storage.purge_older_than(RETENTION_DAYS)
    ))
    retention_timer.start(6 * 60 * 60 * 1000)  # every 6h, same order of magnitude as a daily sweep

    log.info("Nexus Linux iniciado")
    await close_event.wait()


def main() -> int:
    _setup_logging()
    _install_exception_hook()

    lock_file = _acquire_single_instance_lock()
    if lock_file is None:
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName("Nexus")
    app.setQuitOnLastWindowClosed(False)

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
    app.setStyleSheet(styles.qss())

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    close_event = asyncio.Event()
    app.aboutToQuit.connect(close_event.set)

    with loop:
        loop.run_until_complete(_run(app, close_event))
    return 0


if __name__ == "__main__":
    sys.exit(main())
