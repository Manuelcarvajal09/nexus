"""Reusable clip row: icon chip + title + muted metadata line, used by both
MainWindow's history list and StackWindow's collection list so they share
one visual language instead of each hand-rolling QListWidgetItem text."""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import styles

KIND_LABELS = {"text": "Texto", "url": "Enlace", "image": "Imagen", "file": "Archivo"}
BADGE_SIZE = 34

# Decoding + scaling + circular-masking a PNG is the expensive part of
# building an image row (~10-15ms each measured live on Erwin) -- and
# MainWindow.refresh() rebuilds every visible row from scratch on every
# single window open. A history page with 60+ image items therefore redid
# that work 60+ times per Ctrl+Shift+V, which measured as ~800ms of the
# ~900ms it took the panel to appear. content_hash is stable for a given
# row's lifetime (dedup-recapture only touches updated_at/source_app, never
# image_data), so it's a safe cache key; capped so a long-running session
# with a large image-heavy history doesn't grow this unboundedly.
_THUMBNAIL_CACHE_MAX = 2000
_thumbnail_cache: dict[tuple[str, int], QPixmap] = {}


def _cached_circular_thumbnail(content_hash: str, image_data: bytes, size: int) -> QPixmap:
    key = (content_hash, size)
    cached = _thumbnail_cache.get(key)
    if cached is not None:
        return cached
    if len(_thumbnail_cache) >= _THUMBNAIL_CACHE_MAX:
        _thumbnail_cache.pop(next(iter(_thumbnail_cache)))
    pixmap = _circular_thumbnail(image_data, size)
    _thumbnail_cache[key] = pixmap
    return pixmap


def _circular_thumbnail(image_data: bytes, size: int) -> QPixmap:
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
    return result


class ClipRowWidget(QWidget):
    def __init__(self, item, position: int | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("clipRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("selected", "false")
        self.setProperty("hovered", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 9, 12, 9)
        root.setSpacing(11)

        badge = QWidget()
        badge.setObjectName("iconBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        badge.setFixedSize(BADGE_SIZE, BADGE_SIZE)
        badge_layout = QVBoxLayout(badge)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if item.kind == "image" and item.image_data:
            icon_label.setPixmap(_cached_circular_thumbnail(item.content_hash, item.image_data, BADGE_SIZE))
            icon_label.setFixedSize(BADGE_SIZE, BADGE_SIZE)
        else:
            icon_label.setPixmap(styles.kind_icon(item.kind).pixmap(18, 18))
        badge_layout.addWidget(icon_label)
        root.addWidget(badge)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title = QLabel(item.preview(64))
        title.setObjectName("rowTitle")
        # Clipboard contents can be arbitrarily long (shell commands, URLs,
        # JSON). They should clip inside the card, never dictate a wider list.
        title.setMinimumWidth(0)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_col.addWidget(title)

        meta_parts = [KIND_LABELS.get(item.kind, item.kind)]
        if position is not None:
            meta_parts.insert(0, f"#{position}")
        if getattr(item, "is_pinned", False):
            meta_parts.append("anclado")
        meta = QLabel("  ·  ".join(meta_parts))
        meta.setObjectName("rowMeta")
        meta.setMinimumWidth(0)
        meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_col.addWidget(meta)

        root.addLayout(text_col, stretch=1)

        if getattr(item, "is_pinned", False):
            pin = QLabel("📌")
            pin.setStyleSheet("background: transparent; font-size: 12px;")
            root.addWidget(pin)

        # Soft elevation on hover -- a still shadow would just look like a
        # design mistake sitting there permanently, so it starts invisible
        # (blurRadius 0) and only animates in on enterEvent. This is the one
        # place in the row that touches motion at all; everything else
        # (background/border) is instant QSS, deliberately -- see
        # animations.py for why window-level motion stays opacity-only, the
        # same "don't animate more than helps" reasoning applies here.
        #
        # Built lazily (see _ensure_shadow), not here: QGraphicsEffect forces
        # Qt to render its widget through an offscreen compositing buffer
        # instead of the normal fast paint path, and that setup cost is paid
        # the moment setGraphicsEffect() is called -- eagerly attaching one to
        # every row measured at ~800ms to construct a 200-row history refresh
        # (confirmed live on Erwin), which is the entire reason Ctrl+Shift+V
        # felt slow to open. A typical viewport only shows 6-8 rows, so almost
        # all of that cost was for effects nobody was ever going to see.
        self._shadow: QGraphicsDropShadowEffect | None = None

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)

    def _ensure_shadow(self) -> QGraphicsDropShadowEffect:
        if self._shadow is None:
            self._shadow = QGraphicsDropShadowEffect(self)
            self._shadow.setColor(QColor(0, 0, 0, 0))
            self._shadow.setBlurRadius(0)
            self._shadow.setOffset(0, 3)
            self.setGraphicsEffect(self._shadow)
        return self._shadow

    def _animate_shadow(self, hovered: bool) -> None:
        shadow = self._ensure_shadow()
        blur_anim = QPropertyAnimation(shadow, b"blurRadius", self)
        blur_anim.setDuration(140)
        blur_anim.setStartValue(shadow.blurRadius())
        blur_anim.setEndValue(16 if hovered else 0)
        blur_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        blur_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        shadow.setColor(QColor(0, 0, 0, 90 if hovered else 0))
        self._shadow_anim = blur_anim

    def enterEvent(self, event) -> None:
        self.setProperty("hovered", "true")
        self._repolish()
        self._animate_shadow(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.setProperty("hovered", "false")
        self._repolish()
        self._animate_shadow(False)
        super().leaveEvent(event)
