"""System-wide Ctrl+V detector for Nexus Stack, the Linux equivalent of
PasteStack.swift's NSEvent.addGlobalMonitorForEvents(matching: .keyDown)
global Accessibility key monitor. macOS gates that API behind the
Accessibility permission; Wayland has no per-app equivalent -- the only way
to see key events system-wide (regardless of which window has focus) is to
read the kernel input devices directly via evdev, which requires the user's
account to belong to the `input` group. Manuel explicitly authorized this
tradeoff (2026-08-24) after being told what it grants: any process running
as him could read all keyboard input, not just this one's Ctrl+V filter.

Runs the blocking evdev reads on a background thread (Qt's event loop must
stay free), multiplexing every keyboard-capable device with `selectors` so
one thread covers a laptop's built-in keyboard plus any USB ones. Emits
`paste_detected` (a Qt signal, thread-safe to connect to the main thread)
only for a plain Ctrl+V -- Ctrl+Shift+V (Nexus's own global shortcut) is
deliberately excluded so the two don't fight over the same physical keys."""

import logging
import selectors
import threading
import time

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("nexus.evdev_watcher")

try:
    import evdev
    from evdev import ecodes
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False

CTRL_CODES = {29, 97}  # KEY_LEFTCTRL, KEY_RIGHTCTRL
SHIFT_CODES = {42, 54}  # KEY_LEFTSHIFT, KEY_RIGHTSHIFT
KEY_V = 47


class EvdevStackWatcher(QObject):
    paste_detected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._suppress_until = 0.0

    def suppress_for(self, seconds: float = 0.6) -> None:
        """Call right before Nexus's own synthetic_paste() (ydotool) sends a
        Ctrl+V -- that keypress is real at the kernel level and would
        otherwise look identical to a genuine user paste."""
        self._suppress_until = time.monotonic() + seconds

    def start(self) -> bool:
        if not EVDEV_AVAILABLE:
            log.warning("Paquete 'evdev' no instalado; auto-avance del Stack deshabilitado.")
            return False

        devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except (OSError, PermissionError):
                continue
            caps = dev.capabilities().get(ecodes.EV_KEY, [])
            if KEY_V in caps and 29 in caps:  # looks like a real keyboard
                devices.append(dev)
            else:
                dev.close()

        if not devices:
            log.warning(
                "Sin acceso a dispositivos de teclado en /dev/input (grupo 'input' "
                "no aplicado todavía -- requiere cerrar sesión y volver a entrar "
                "tras usermod). Auto-avance del Stack deshabilitado; usa el botón "
                "\"Pegar siguiente (manual)\"."
            )
            return False

        log.info("Watcher de teclado activo en %d dispositivo(s): %s",
                  len(devices), ", ".join(d.name for d in devices))

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(devices,), daemon=True, name="nexus-evdev-watcher"
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self, devices: list) -> None:
        sel = selectors.DefaultSelector()
        for dev in devices:
            sel.register(dev, selectors.EVENT_READ)

        ctrl_held = set()
        shift_held = set()

        try:
            while not self._stop.is_set():
                for key, _ in sel.select(timeout=0.5):
                    device = key.fileobj
                    try:
                        for event in device.read():
                            if event.type != ecodes.EV_KEY:
                                continue
                            code, value = event.code, event.value  # value: 1=down 0=up 2=repeat
                            if code in CTRL_CODES:
                                (ctrl_held.add if value else ctrl_held.discard)(code)
                            elif code in SHIFT_CODES:
                                (shift_held.add if value else shift_held.discard)(code)
                            elif code == KEY_V and value == 1 and ctrl_held and not shift_held:
                                if time.monotonic() >= self._suppress_until:
                                    self.paste_detected.emit()
                    except (OSError, BlockingIOError):
                        continue
        finally:
            for dev in devices:
                try:
                    dev.close()
                except OSError:
                    pass
            sel.close()
