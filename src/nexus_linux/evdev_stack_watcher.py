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
deliberately excluded so the two don't fight over the same physical keys.

Root cause fixed 2026-08-26: a Bluetooth keyboard reconnect (or any device
unplug) leaves a previously-opened /dev/input/eventN pointing at a deleted
inode. The kernel then reports that fd as permanently read-ready (an
EOF/error condition), so selectors.select() returns it on every iteration;
read() raised OSError, which the old code caught and silently retried with
zero backoff -- an unbounded busy loop pegging one CPU core (confirmed live
on Erwin: one process had accumulated 17h+ of CPU time over an 8h wall-clock
session). Fixed by unregistering/closing a device the instant its read()
fails, and periodically rescanning for newly-appeared keyboards (this also
closes the hot-plug gap noted in MIGRATION_LINUX.md)."""

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

RESCAN_INTERVAL_S = 15.0  # how often to look for keyboards that appeared after startup


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

    @staticmethod
    def _open_keyboards(exclude_paths: set) -> dict:
        """Opens every not-yet-known device that looks like a real keyboard
        (has KEY_V and KEY_LEFTCTRL). Returns {path: InputDevice}."""
        found = {}
        for path in evdev.list_devices():
            if path in exclude_paths:
                continue
            try:
                dev = evdev.InputDevice(path)
            except (OSError, PermissionError):
                continue
            caps = dev.capabilities().get(ecodes.EV_KEY, [])
            if KEY_V in caps and 29 in caps:
                found[path] = dev
            else:
                dev.close()
        return found

    def start(self) -> bool:
        if not EVDEV_AVAILABLE:
            log.warning("Paquete 'evdev' no instalado; auto-avance del Stack deshabilitado.")
            return False

        devices = self._open_keyboards(exclude_paths=set())
        if not devices:
            log.warning(
                "Sin acceso a dispositivos de teclado en /dev/input (grupo 'input' "
                "no aplicado todavía -- requiere cerrar sesión y volver a entrar "
                "tras usermod). Auto-avance del Stack deshabilitado; usa el botón "
                "\"Pegar siguiente (manual)\"."
            )
            return False

        log.info("Watcher de teclado activo en %d dispositivo(s): %s",
                  len(devices), ", ".join(d.name for d in devices.values()))

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

    def _run(self, devices: dict) -> None:
        sel = selectors.DefaultSelector()
        for dev in devices.values():
            sel.register(dev, selectors.EVENT_READ)

        ctrl_held = set()
        shift_held = set()
        next_rescan = time.monotonic() + RESCAN_INTERVAL_S

        def _drop(path: str) -> None:
            dev = devices.pop(path, None)
            if dev is None:
                return
            try:
                sel.unregister(dev)
            except KeyError:
                pass
            try:
                dev.close()
            except OSError:
                pass
            log.warning("Dispositivo de teclado desconectado, dejando de leerlo: %s (%s)", dev.name, path)

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
                    except BlockingIOError:
                        continue
                    except OSError:
                        # Device vanished (unplugged / Bluetooth reconnect left a
                        # stale fd on a deleted inode). Without this, a selector
                        # keeps reporting a dead fd as permanently read-ready and
                        # this loop busy-spins at 100% CPU forever -- confirmed
                        # live on Erwin, see module docstring. Drop it instead of
                        # retrying; a rescan will pick up its replacement.
                        _drop(device.path)

                now = time.monotonic()
                if now >= next_rescan:
                    next_rescan = now + RESCAN_INTERVAL_S
                    for path, dev in self._open_keyboards(exclude_paths=set(devices)).items():
                        devices[path] = dev
                        sel.register(dev, selectors.EVENT_READ)
                        log.info("Nuevo dispositivo de teclado detectado: %s (%s)", dev.name, path)
        finally:
            for dev in devices.values():
                try:
                    dev.close()
                except OSError:
                    pass
            sel.close()
