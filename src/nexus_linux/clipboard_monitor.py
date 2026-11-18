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
subprocess instead of QClipboard. Writes use wl-copy through PasteService, with exact-payload suppression.
"""

import logging
import hashlib
import time
import sys
from .settings import load_settings
import shutil
import subprocess

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, QRunnable, QThreadPool

from .storage import StorageService
from .clipboard_offer import read_offer

log = logging.getLogger("nexus.monitor")

WL_PASTE = shutil.which("wl-paste")

# A background capture service that just stops working after the watched
# process dies -- compositor restart, wl-paste crash, anything -- with no
# indication to the user is exactly the kind of silent failure a polished
# app doesn't have. Restart with backoff instead of giving up after one
# death; MAX_RESTARTS guards against a genuine crash loop hammering the log.
RESTART_DELAY_MS = 2000
MAX_RESTARTS = 10

class _ReaderSignals(QObject):
    ready = Signal(object)


class _Reader(QRunnable):
    def __init__(self, signals, generation):
        super().__init__()
        self.signals = signals
        self.generation = generation

    def run(self):
        self.signals.ready.emit((self.generation, ClipboardMonitor._snapshot()))


class ClipboardMonitor(QObject):
    captured = Signal(object)  # emits ClipItem
    selection_observed = Signal(str)  # Metadata only, including rejected states.

    def __init__(self, storage: StorageService, parent=None, *, enabled: bool = True):
        super().__init__(parent)
        self._storage = storage
        self._internal_writes = {}
        self._internal_current = None
        self._internal_seen = False
        self._settings = load_settings()
        self._last_hash = None
        self._reading = False
        self._read_pending = False
        self._selection_generation = 0
        self._last_selection_state = 'unknown'
        self._reader_pool = QThreadPool(self)
        self._reader_pool.setMaxThreadCount(1)
        self._reader_signals = _ReaderSignals(self)
        self._reader_signals.ready.connect(self._read_finished)
        self._process: QProcess | None = None
        self._restart_count = 0
        self._watch_buffer = b""
        self._stopped = not enabled

        if not enabled:
            return

        if WL_PASTE is None:
            log.warning("wl-paste no encontrado; el monitor de portapapeles no arrancará.")
            return

        self._start_watch_process()

    def _start_watch_process(self) -> None:
        if self._stopped:
            return
        self._watch_buffer = b""
        self._process = QProcess(self)
        self._process.setProgram(WL_PASTE)
        self._process.setArguments(["--watch", sys.executable, "-c",
            "import os; print(os.environ.get('CLIPBOARD_STATE', 'unknown'), flush=True)"])
        self._process.readyReadStandardOutput.connect(self._on_watch_tick)
        self._process.finished.connect(self._on_watch_process_finished)
        self._process.start()
        if not self._process.waitForStarted(2000):
            log.warning("No se pudo iniciar 'wl-paste --watch'.")
        else:
            log.info("Monitor Wayland iniciado: wl-paste --watch (captura en segundo plano)")
            # A restart that then stays healthy for a while shouldn't count
            # against MAX_RESTARTS forever -- only a tight crash loop should.
            QTimer.singleShot(60_000, self._reset_restart_count_if_alive)

    def _reset_restart_count_if_alive(self) -> None:
        if self._process is not None and self._process.state() == QProcess.ProcessState.Running:
            self._restart_count = 0

    def _on_watch_process_finished(self, exit_code: int, _exit_status) -> None:
        if self._stopped:
            return
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

    def suppress_next_change(self, data=None, mime=None) -> None:
        """Suppress the exact internal payload, never blindly skip another copy."""
        if data is not None:
            digest = hashlib.sha256(data).hexdigest()
            self._internal_writes[digest] = time.monotonic() + 3
            self._internal_current = digest
            self._internal_seen = False
            # A queued snapshot predating our clipboard write cannot describe
            # the clipboard prepared for this dispatch.
            self._selection_generation += 1

    def _excluded(self, raw, types):
        if "x-kde-passwordManagerHint" in types or len(raw) > self._settings.max_capture_bytes:
            return True
        now = time.monotonic()
        self._internal_writes = {k: v for k, v in self._internal_writes.items() if v > now}
        digest = hashlib.sha256(raw).hexdigest()
        if digest == self._internal_current:
            self._internal_seen = True
            return True
        if self._internal_seen:
            self._internal_current = None
            self._internal_seen = False
        if digest in self._internal_writes:
            return True
        text = raw.decode("utf-8", "replace")
        for pattern in self._settings.excluded_patterns:
            # Literal exclusions avoid running arbitrary, potentially costly regexes.
            if pattern and pattern in text:
                return True
        return False

    def configure(self, enabled):
        self._settings = load_settings()
        if not enabled:
            self.stop()
        elif self._stopped and WL_PASTE:
            self._stopped = False
            self._restart_count = 0
            self._start_watch_process()

    def stop(self) -> None:
        self._stopped = True
        self._selection_generation += 1
        self._read_pending = False
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(1000):
                self._process.kill()
                self._process.waitForFinished(1000)
        # A reader must finish before its signal owner and storage are destroyed.
        self._reader_pool.waitForDone(4500)

    def _on_watch_tick(self) -> None:
        self._watch_buffer += bytes(self._process.readAllStandardOutput())
        lines = self._watch_buffer.split(b"\n")
        self._watch_buffer = lines.pop()
        states = [line.decode("utf-8", "replace") for line in lines]
        for state in states:
            self._selection_generation += 1
            self._last_selection_state = state
            self.selection_observed.emit(state)
        if not states:
            return
        if states[-1] != 'data':
            self._read_pending = False
            return
        self._queue_read()

    def _queue_read(self):
        if self._stopped:
            return
        if self._reading:
            self._read_pending = True
            return
        self._reading = True
        self._reader_pool.start(_Reader(self._reader_signals, self._selection_generation))

    def _read_finished(self, snapshot):
        self._reading = False
        generation, offer = snapshot
        if (not self._stopped and generation == self._selection_generation
                and self._last_selection_state == 'data' and offer is not None):
            self._store_snapshot(*offer)
        if self._read_pending:
            self._read_pending = False
            self._queue_read()

    def _capture_current_clipboard(self) -> None:
        # Synchronous helper for diagnostics/tests; watch callbacks use a worker.
        offer = self._snapshot()
        if offer is not None:
            self._store_snapshot(*offer)

    @staticmethod
    def _snapshot():
        types = ClipboardMonitor._list_types()
        if not types or "x-kde-passwordManagerHint" in types:
            return
        mime = next((m for m in ('text/uri-list', 'image/png', 'text/plain') if m in types), None)
        if mime is None:
            mime = next((m for m in types if m.startswith('text/plain;')), None)
        if mime is None:
            return None
        offer = read_offer(WL_PASTE, mime)
        if offer is None or offer[0] != 'data':
            return None
        # CLIPBOARD_STATE is bound to these exact bytes, unlike list-types above.
        state, exact_mime, raw = offer
        canonical = 'text/plain' if exact_mime.startswith('text/plain;') else exact_mime
        return [exact_mime], canonical, raw

    def _store_snapshot(self, types, mime, raw):
        if raw is None or self._excluded(raw, types):
            return
        item = None
        if mime == "text/uri-list":
            from urllib.parse import urlsplit, unquote
            uris = [u for u in raw.decode("utf-8", "replace").splitlines() if u.strip() and not u.startswith("#")]
            local_files = [unquote(urlsplit(u).path) for u in uris
                           if u.startswith("file://") and urlsplit(u).netloc in ("", "localhost")]
            if local_files:
                item = self._storage.capture(kind="file", file_path=local_files[0])
            elif uris:
                item = self._storage.capture(kind="url", text_content=uris[0])
        elif mime == "image/png" and raw:
            item = self._storage.capture(kind="image", image_data=raw)
        elif mime == "text/plain":
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
        offer = read_offer(WL_PASTE, mime)
        return offer[2] if offer is not None and offer[0] == 'data' else None
