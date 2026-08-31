from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice, QObject, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication
from .windows_platform import clipboard_requests_exclusion, foreground_process_name


class WindowsClipboardMonitor(QObject):
    captured = Signal(object)

    def __init__(self, storage, settings=None, parent=None):
        super().__init__(parent)
        self._storage = storage
        self._suppressed = False
        self._suppression_generation = 0
        self._last_hash = None
        self._clipboard = QGuiApplication.clipboard()
        self._settings = settings or {}
        self._clipboard.dataChanged.connect(self._capture)

    def suppress_next_change(self):
        self._suppressed = True
        self._suppression_generation += 1
        generation = self._suppression_generation
        # Identical clipboard writes can be coalesced without dataChanged.
        # Expire on the next event-loop turn so a later USER copy is never
        # swallowed by a stale suppression flag.
        QTimer.singleShot(0, lambda: self._expire_suppression(generation))

    def _expire_suppression(self, generation: int) -> None:
        if generation == self._suppression_generation:
            self._suppressed = False

    def _capture(self):
        if self._suppressed:
            self._suppressed = False
            return
        source_app = foreground_process_name()
        excluded = {name.lower() for name in self._settings.get("excluded_apps", [])}
        if self._settings.get("paused") or source_app.lower() in excluded or clipboard_requests_exclusion():
            return
        mime = self._clipboard.mimeData()
        item = None
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                item = self._storage.capture(kind="file", file_path=paths[0], source_app=source_app)
        elif mime.hasImage():
            image = self._clipboard.image()
            if not image.isNull():
                if image.width() > 1600 or image.height() > 1600:
                    image = image.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, "PNG")
                item = self._storage.capture(kind="image", image_data=bytes(buffer.data()), source_app=source_app)
        elif mime.hasText() and mime.text().strip():
            text = mime.text()
            kind = "url" if text.strip().lower().startswith(("http://", "https://")) else "text"
            item = self._storage.capture(kind=kind, text_content=text, source_app=source_app)
        if item and item.content_hash != self._last_hash:
            self._last_hash = item.content_hash
            self.captured.emit(item)
