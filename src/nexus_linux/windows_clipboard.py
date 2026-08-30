from __future__ import annotations

import hashlib
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication


class WindowsClipboardMonitor(QObject):
    captured = Signal(object)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self._storage = storage
        self._suppressed = False
        self._last_hash = None
        self._clipboard = QGuiApplication.clipboard()
        self._clipboard.dataChanged.connect(self._capture)

    def suppress_next_change(self):
        self._suppressed = True

    def _capture(self):
        if self._suppressed:
            self._suppressed = False
            return
        mime = self._clipboard.mimeData()
        item = None
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                item = self._storage.capture(kind="file", file_path=paths[0])
        elif mime.hasImage():
            image = self._clipboard.image()
            if not image.isNull():
                from PySide6.QtCore import QBuffer, QIODevice
                buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, "PNG")
                item = self._storage.capture(kind="image", image_data=bytes(buffer.data()))
        elif mime.hasText() and mime.text().strip():
            item = self._storage.capture(kind="text", text_content=mime.text())
        if item and item.content_hash != self._last_hash:
            self._last_hash = item.content_hash
            self.captured.emit(item)
