"""Shared image-thumbnail cache, used by both the vertical row widgets
(row_widget.py, for MainWindow's legacy list and StackWindow) and the
Timeline card delegate (timeline_delegate.py). Extracted from row_widget.py
during the Phase 2 Timeline work so both call sites share one cache instead
of decoding/scaling the same PNG twice for the same item.

Decoding + scaling + masking a PNG measured at ~10-15ms each live on Erwin
(see PERFORMANCE_BASELINE.md) -- redoing that per-open for every image row
was the dominant cost behind the ~900ms Ctrl+Shift+V latency found in the
Phase 1 audit. content_hash is stable for a given row's lifetime
(dedup-recapture only touches updated_at/source_app, never image_data), so
it's a safe cache key; capped so a long session with an image-heavy history
doesn't grow this unboundedly."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap

_CACHE_MAX = 2000
_circular_cache: dict[tuple[str, int], QPixmap] = {}
_rounded_cache: dict[tuple[str, int, int, int], QPixmap] = {}


def _evict_if_full(cache: dict) -> None:
    if len(cache) >= _CACHE_MAX:
        cache.pop(next(iter(cache)))


def circular_thumbnail(content_hash: str, image_data: bytes, size: int) -> QPixmap:
    """Square, circle-masked -- used for the small icon-badge-style
    thumbnail in the vertical row widgets."""
    key = (content_hash, size)
    cached = _circular_cache.get(key)
    if cached is not None:
        return cached

    source = QImage()
    source.loadFromData(image_data)
    scaled = QPixmap.fromImage(source).scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    x = (scaled.width() - size) // 2
    y = (scaled.height() - size) // 2
    painter.drawPixmap(-x, -y, scaled)
    painter.end()

    _evict_if_full(_circular_cache)
    _circular_cache[key] = result
    return result


def cached_rounded_rect(content_hash: str, width: int, height: int, radius: int):
    """The cached thumbnail, or None -- lets callers skip loading the image blob."""
    return _rounded_cache.get((content_hash, width, height, radius))


def rounded_rect_for(item, width: int, height: int, radius: int):
    """Thumbnail for a ClipItem, reading its image blob only on a cache miss
    (history rows are listed without the blob, see storage._LazyImageClipItem).
    None when the item has no usable image."""
    cached = cached_rounded_rect(item.content_hash, width, height, radius)
    if cached is not None:
        return cached
    data = item.image_data if item.kind == "image" else None
    return rounded_rect_thumbnail(item.content_hash, data, width, height, radius) if data else None


def circular_for(item, size: int):
    cached = _circular_cache.get((item.content_hash, size))
    if cached is not None:
        return cached
    data = item.image_data if item.kind == "image" else None
    return circular_thumbnail(item.content_hash, data, size) if data else None


def rounded_rect_thumbnail(content_hash: str, image_data: bytes, width: int, height: int, radius: int) -> QPixmap:
    """Rectangular, rounded-corner-masked, aspect-fill -- used for the
    dominant image element on a Timeline image/YouTube card."""
    key = (content_hash, width, height, radius)
    cached = _rounded_cache.get(key)
    if cached is not None:
        return cached

    source = QImage()
    source.loadFromData(image_data)
    scaled = QPixmap.fromImage(source).scaled(
        width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    result = QPixmap(width, height)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, width, height, radius, radius)
    painter.setClipPath(path)
    x = (scaled.width() - width) // 2
    y = (scaled.height() - height) // 2
    painter.drawPixmap(-x, -y, scaled)
    painter.end()

    _evict_if_full(_rounded_cache)
    _rounded_cache[key] = result
    return result
