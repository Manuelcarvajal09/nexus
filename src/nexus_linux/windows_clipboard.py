"""Read-only, event-driven Windows clipboard history capture.

Never repair, restore or claim ownership of the system clipboard here. Delayed
image rendering/temporary locks are retried only while the same version exists.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QBuffer, QIODevice, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from .windows_platform import clipboard_requests_exclusion, clipboard_sequence, foreground_process_name

log = logging.getLogger("nexus.clipboard")


class WindowsClipboardMonitor(QObject):
    captured = Signal(object)
    captured_versioned = Signal(object, object)
    # ~1.6 s in total: Office/Teams/other clipboard tools can hold the clipboard
    # open longer than the previous ~370 ms, and the copy was then lost silently.
    RETRY_DELAYS = (20, 50, 100, 200, 400, 800)

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
        if sequence and sequence == self._last_sequence:
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
        try:
            item = self._storage.capture(source_app=source_app, **payload)
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
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "PNG"):
                return None
            # Store full resolution. Scaling belongs to thumbnail rendering,
            # not to the payload later pasted back into another application.
            return {"kind": "image", "image_data": bytes(buffer.data())}
        if mime.hasText() and mime.text().strip():
            text = mime.text()
            kind = "url" if text.strip().lower().startswith(("http://", "https://")) else "text"
            return {"kind": kind, "text_content": text}
        return None
