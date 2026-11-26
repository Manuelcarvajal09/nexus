"""Read-only, event-driven Windows clipboard history capture.

Never repair, restore or claim ownership of the system clipboard here. Delayed
image rendering/temporary locks are retried only while the same version exists.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QBuffer, QIODevice, QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from .windows_platform import clipboard_requests_exclusion, clipboard_sequence, foreground_process_name

log = logging.getLogger("nexus.clipboard")


def _encode_payload(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        return None
    return {"kind": "image", "image_data": bytes(buffer.data())}


@dataclass(eq=False)
class _Pending:
    generation: int
    sequence: int
    source_app: str
    payload: dict = field(default_factory=dict)
    encoding: bool = False


class _EncodeSignals(QObject):
    done = Signal(object, object)   # (entry, payload or None), delivered on the UI thread


class _EncodeTask(QRunnable):
    def __init__(self, image, entry, done):
        super().__init__()
        self._image, self._entry, self._done = image.copy(), entry, done

    def run(self):
        try:
            payload = _encode_payload(self._image)
        except Exception:   # noqa: BLE001 - never let a worker exception vanish the capture queue
            payload = None
        self._done.emit(self._entry, payload)


class WindowsClipboardMonitor(QObject):
    captured = Signal(object)
    captured_versioned = Signal(object, object)
    # ~1.6 s in total: Office/Teams/other clipboard tools can hold the clipboard
    # open longer than the previous ~370 ms, and the copy was then lost silently.
    RETRY_DELAYS = (20, 50, 100, 200, 400, 800)
    ASYNC_IMAGE_ENCODE = None   # None: on the real Windows platform only (tests force True/False)

    def __init__(self, storage, settings=None, parent=None):
        super().__init__(parent)
        self._storage = storage
        self._suppressed = False
        self._suppression_generation = 0
        self._generation = 0
        self._last_sequence = None
        self._running = True
        self._clipboard = QGuiApplication.clipboard()
        self._settings = settings or {}
        self._queue = deque()
        self._encoded = _EncodeSignals(self)
        self._encoded.done.connect(self._on_encoded)
        self._clipboard.dataChanged.connect(self._capture)

    def suppress_next_change(self):
        # Compatibility with the paste service. Suppression is advisory only:
        # an external owner must NEVER be skipped because an identical own
        # write emitted no notification, or because delivery was delayed.
        self._suppressed = True
        self._suppression_generation += 1
        generation = self._suppression_generation
        QTimer.singleShot(0, lambda: self._expire_suppression(generation))

    def _expire_suppression(self, generation):
        if generation == self._suppression_generation:
            self._suppressed = False

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._generation += 1
        self._queue.clear()
        self._clipboard.dataChanged.disconnect(self._capture)

    def _capture(self):
        if not self._running:
            return
        self._suppressed = False
        if self._clipboard.ownsClipboard():
            # Nexus' own write: must not cancel a retry still pending for an external copy.
            return
        self._generation += 1
        generation = self._generation
        sequence = clipboard_sequence()
        if sequence and (sequence == self._last_sequence or any(p.sequence == sequence for p in self._queue)):
            return
        source_app = foreground_process_name()
        self._read(generation, sequence, source_app, 0)

    def _read(self, generation, sequence, source_app, attempt):
        if generation != self._generation or clipboard_sequence() != sequence:
            return
        excluded = {name.lower() for name in self._settings.get("excluded_apps", [])}
        if (self._clipboard.ownsClipboard() or self._settings.get("paused")
                or source_app.lower() in excluded or clipboard_requests_exclusion()):
            return
        try:
            payload = self._snapshot()
        except (RuntimeError, OSError):
            # Do not log exceptions: providers can include private content.
            payload = None
        current_sequence = clipboard_sequence()
        if generation != self._generation:
            return
        if current_sequence != sequence:
            # Rendering a delayed CF_DIB can itself advance the Win32 version.
            # Re-read a fresh snapshot, never persist a mixed-version payload.
            if attempt < len(self.RETRY_DELAYS):
                QTimer.singleShot(self.RETRY_DELAYS[attempt],
                    lambda: self._read(generation, current_sequence, foreground_process_name(), attempt + 1))
            return
        if payload is None:
            if attempt < len(self.RETRY_DELAYS):
                QTimer.singleShot(self.RETRY_DELAYS[attempt],
                    lambda: self._read(generation, sequence, source_app, attempt + 1))
            else:
                log.warning("capture.dropped reason=unreadable attempts=%s", attempt + 1)
            return
        self._queue.append(_Pending(generation, sequence, source_app, payload))
        self._pump()

    def _async_encode(self):
        if self.ASYNC_IMAGE_ENCODE is not None:
            return self.ASYNC_IMAGE_ENCODE
        return QGuiApplication.platformName() == "windows"

    def _pump(self):
        """Store snapshots strictly in copy order. A large image is PNG-encoded on a
        worker thread (a 4K screenshot took ~0.7 s, freezing the UI and starving the
        Ctrl+V hook); copies made meanwhile wait behind it, so History and Stack keep
        the order in which things were copied."""
        while self._queue and self._running:
            head = self._queue[0]
            image = head.payload.get("qimage")
            if image is not None:
                if head.encoding:
                    return
                if self._async_encode():
                    head.encoding = True
                    QThreadPool.globalInstance().start(_EncodeTask(image, head, self._encoded.done))
                    return
                head.payload = _encode_payload(image)
            self._queue.popleft()
            if head.payload is None:
                log.warning("capture.dropped reason=encode_failed")
                continue
            self._store(head)

    @Slot(object, object)
    def _on_encoded(self, entry, payload):
        if not self._queue or self._queue[0] is not entry:
            return   # stopped meanwhile
        entry.payload, entry.encoding = payload, False
        self._pump()

    def _store(self, entry):
        generation, sequence = entry.generation, entry.sequence
        try:
            item = self._storage.capture(source_app=entry.source_app, **entry.payload)
        except Exception as exc:
            # SQLite must not terminate the Qt notification callback. No data
            # or exception messages are written to diagnostics.
            log.error("capture.storage_failed exception_type=%s", type(exc).__name__)
            return
        self._last_sequence = sequence if sequence else None
        # Dedup belongs to storage, not this signal: a genuine re-copy must
        # refresh ordering and may need to be collected in a new Stack session.
        self.captured.emit(item)
        # History can retain this snapshot even if another process copied
        # during SQLite/UI work. Stack must never attribute it to that NEW
        # native version. Carry provenance rather than querying it downstream.
        if generation == self._generation and clipboard_sequence() == sequence:
            self.captured_versioned.emit(item, sequence)

    def _snapshot(self):
        mime = self._clipboard.mimeData()
        if mime is None:
            return None
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                return {"kind": "file", "file_path": paths[0]}
        if mime.hasImage():
            image = self._clipboard.image()
            if image.isNull():
                return None
            # Store full resolution. Scaling belongs to thumbnail rendering,
            # not to the payload later pasted back into another application.
            # Encoding to PNG happens in _pump (off the UI thread on Windows).
            return {"kind": "image", "qimage": image}
        if mime.hasText() and mime.text().strip():
            text = mime.text()
            kind = "url" if text.strip().lower().startswith(("http://", "https://")) else "text"
            return {"kind": kind, "text_content": text}
        return None
