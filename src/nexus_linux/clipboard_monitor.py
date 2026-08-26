"""Mirrors Nexus's ClipboardMonitor: observes clipboard changes, captures
files, images, URLs, or text in that priority order.

Qt6's built-in QClipboard on the wayland QPA plugin uses the standard
wl_data_device protocol, which -- by deliberate Wayland design -- only
delivers selection-changed notifications to the currently focused client.
A background clipboard manager is not focused most of the time, so
QClipboard.dataChanged() silently never fires for copies made in other
apps (verified live against Erwin on 2026-08-23: wl-copy/wl-paste worked
at the OS level, but neither dataChanged() nor a polling QClipboard read
ever saw the change).

wl-clipboard's wl-paste --watch uses wlr-data-control-v1 / ext-data-control-v1
instead: the protocol Wayland compositors expose specifically for privileged
background clipboard tools, unaffected by focus. That is the correct,
security-respecting fix (per the migration brief: no disabling Wayland
security, use the sanctioned alternative) -- so capture is driven by that
subprocess instead of QClipboard. QClipboard is still used for *writing*
(PasteService), since a foreground user-initiated write is exactly the case
the standard protocol handles fine.
"""

import logging
import shutil
import subprocess

from PySide6.QtCore import QBuffer, QIODevice, QObject, QProcess, Qt, QTimer, Signal
from PySide6.QtGui import QImage

from .storage import StorageService

log = logging.getLogger("nexus.monitor")

WL_PASTE = shutil.which("wl-paste")

# A background capture service that just stops working after the watched
# process dies -- compositor restart, wl-paste crash, anything -- with no
# indication to the user is exactly the kind of silent failure a polished
# app doesn't have. Restart with backoff instead of giving up after one
# death; MAX_RESTARTS guards against a genuine crash loop hammering the log.
RESTART_DELAY_MS = 2000
MAX_RESTARTS = 10

# Guards against a single huge capture (a multi-monitor 5K screenshot can be
# tens of MB as raw PNG) silently bloating the SQLite file forever -- resize
# down to a sane max dimension before it ever reaches storage.capture().
MAX_IMAGE_DIMENSION = 1600


def _downscale_png(raw: bytes) -> bytes:
    """Caps stored image dimensions so a 5K screenshot doesn't silently turn
    into a multi-MB SQLite row every time it's copied. Returns the original
    bytes unchanged if decoding fails or it's already small enough."""
    image = QImage()
    if not image.loadFromData(raw, "PNG"):
        return raw
    if image.width() <= MAX_IMAGE_DIMENSION and image.height() <= MAX_IMAGE_DIMENSION:
        return raw

    scaled = image.scaled(
        MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION,
        Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
    )
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    scaled.save(buffer, "PNG")
    return bytes(buffer.data())


class ClipboardMonitor(QObject):
    captured = Signal(object)  # emits ClipItem

    def __init__(self, storage: StorageService, parent=None):
        super().__init__(parent)
        self._storage = storage
        self._suppress_next = False
        self._last_hash = None
        self._process: QProcess | None = None
        self._restart_count = 0

        if WL_PASTE is None:
            log.warning("wl-paste no encontrado; el monitor de portapapeles no arrancará.")
            return

        self._start_watch_process()

    def _start_watch_process(self) -> None:
        self._process = QProcess(self)
        self._process.setProgram(WL_PASTE)
        self._process.setArguments(["--watch", "echo", "changed"])
        self._process.readyReadStandardOutput.connect(self._on_watch_tick)
        self._process.finished.connect(self._on_watch_process_finished)
        self._process.start()
        if not self._process.waitForStarted(2000):
            log.warning("No se pudo iniciar 'wl-paste --watch'.")
        else:
            # A restart that then stays healthy for a while shouldn't count
            # against MAX_RESTARTS forever -- only a tight crash loop should.
            QTimer.singleShot(60_000, self._reset_restart_count_if_alive)

    def _reset_restart_count_if_alive(self) -> None:
        if self._process is not None and self._process.state() == QProcess.ProcessState.Running:
            self._restart_count = 0

    def _on_watch_process_finished(self, exit_code: int, _exit_status) -> None:
        if self._restart_count >= MAX_RESTARTS:
            log.error(
                "'wl-paste --watch' murió %d veces seguidas (código %s); dejando de "
                "reintentar para no saturar el log. La captura de portapapeles quedó "
                "inactiva -- reinicia Nexus para recuperarla.",
                self._restart_count, exit_code,
            )
            return
        self._restart_count += 1
        log.warning(
            "'wl-paste --watch' terminó inesperadamente (código %s); reintentando en %.1fs (%d/%d).",
            exit_code, RESTART_DELAY_MS / 1000, self._restart_count, MAX_RESTARTS,
        )
        QTimer.singleShot(RESTART_DELAY_MS, self._start_watch_process)

    def suppress_next_change(self) -> None:
        """Call before Nexus itself writes to the clipboard (e.g. re-arming a
        Stack item), so that write isn't captured as a new history entry."""
        self._suppress_next = True

    def _on_watch_tick(self) -> None:
        # Drain output; one "changed\n" line is emitted per clipboard change,
        # but bursts can coalesce into one readyRead, so just react once.
        self._process.readAllStandardOutput()
        if self._suppress_next:
            self._suppress_next = False
            return
        self._capture_current_clipboard()

    def _capture_current_clipboard(self) -> None:
        types = self._list_types()
        if not types:
            return

        item = None
        if "text/uri-list" in types:
            raw = self._read(mime="text/uri-list")
            if raw is not None:
                uris = [u for u in raw.decode("utf-8", "replace").splitlines() if u.strip()]
                local_files = [u[len("file://"):] for u in uris if u.startswith("file://")]
                if local_files:
                    item = self._storage.capture(kind="file", file_path=local_files[0])
                elif uris:
                    item = self._storage.capture(kind="url", text_content=uris[0])
        elif "image/png" in types:
            raw = self._read(mime="image/png")
            if raw:
                item = self._storage.capture(kind="image", image_data=_downscale_png(raw))
        elif any(t.startswith("text/plain") for t in types):
            raw = self._read(mime="text/plain")
            if raw is not None:
                text = raw.decode("utf-8", "replace")
                if text.strip():
                    item = self._storage.capture(kind="text", text_content=text)

        if item is not None and item.content_hash != self._last_hash:
            self._last_hash = item.content_hash
            self.captured.emit(item)

    @staticmethod
    def _list_types() -> list[str]:
        try:
            result = subprocess.run(
                [WL_PASTE, "--list-types"], capture_output=True, timeout=2, check=False
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.decode("utf-8", "replace").splitlines() if line]

    @staticmethod
    def _read(mime: str) -> bytes | None:
        try:
            result = subprocess.run(
                [WL_PASTE, "--no-newline", "-t", mime], capture_output=True, timeout=2, check=False
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout
