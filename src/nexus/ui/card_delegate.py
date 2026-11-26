"""Windows-only, virtualized premium cards. No fetching and no per-row widgets."""

import os
from functools import lru_cache
from urllib.parse import urlparse

from PySide6.QtCore import QEasingCurve, QEvent, QPersistentModelIndex, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QStyle, QToolTip

from nexus.core.i18n import plural, tr
from nexus.core import relative_time
from nexus.core import thumbnails
from nexus.core import type_detection
from nexus.ui import design as windows_design
from nexus.ui.timeline_delegate import CARD_MARGIN, TYPE_LABELS, ClipCardDelegate, _parse_color
from nexus.core.timeline_model import ITEM_ROLE
from nexus.ui.design import MOTION_HOVER_MS


@lru_cache(maxsize=1024)
def _detect(kind, text):
    """type_detection.detect() runs json.loads/splitlines over the whole clip; it
    used to run on every paint of every card (each hover frame). Strings cache
    their own hash, so a hit costs a dict lookup."""
    return type_detection.detect(kind, text)


def _mix(first, second, amount):
    a, b = QColor(first), QColor(second)
    return QColor(*(round(x + (y - x) * amount) for x, y in zip(a.getRgb(), b.getRgb())))


class WindowsClipCardDelegate(ClipCardDelegate):
    """One finite animation shared by all changing *visible* cards.

    Parent to the item view (or call attach_view). Input observation never consumes
    an event or changes current/selection. Qt owns all keyboard/paste semantics.
    """

    def __init__(self, parent=None, card_height=172):
        super().__init__(parent, card_height)
        self._view = None
        self._selection = None
        self._hover = QPersistentModelIndex()
        self._levels = {}
        self._starts = {}
        self._targets = {}
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(MOTION_HOVER_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._animation.valueChanged.connect(self._frame)
        self._animation.finished.connect(self._finish_motion)
        if isinstance(parent, QAbstractItemView):
            self.attach_view(parent)

    def set_card_height(self, card_height):
        super().set_card_height(max(148, int(card_height)))

    def set_optical(self, optical, window):
        """Optical glass of the owning window: gives cards a contrast layer sized to the backdrop."""
        self._optical, self._optical_window = optical, window

    def _card_fill(self, base, outer, selected):
        optical = getattr(self, "_optical", None)
        if optical is None or not optical.active() or self._view is None:
            return base
        window = self._optical_window
        top_left = self._view.viewport().mapTo(window, outer.topLeft().toPoint())
        return optical.card_veil(base, QRectF(QPointF(top_left), outer.size()), selected)

    def set_card_width(self, width):
        self._card_width = max(180, int(width))

    def sizeHint(self, option, index):
        return QSize(getattr(self, "_card_width", 216), self._card_height)

    def attach_view(self, view):
        if self._view is view:
            self._connect_selection()
            return
        if self._view is not None:
            self._view.viewport().removeEventFilter(self)
            self._disconnect_selection()
        self._animation.stop()
        self._levels.clear()
        self._targets.clear()
        self._hover = QPersistentModelIndex()
        self._view = view
        view.viewport().setMouseTracking(True)
        view.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        view.viewport().installEventFilter(self)
        self._connect_selection()

    def _disconnect_selection(self):
        if self._selection is not None:
            self._selection.selectionChanged.disconnect(self._retarget)
            self._selection.currentChanged.disconnect(self._retarget)
        self._selection = None

    def _connect_selection(self):
        selection = self._view.selectionModel()
        if selection is not self._selection:
            self._disconnect_selection()
            self._selection = selection
            if selection is not None:
                selection.selectionChanged.connect(self._retarget)
                selection.currentChanged.connect(self._retarget)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.MouseMove:
            hovered = QPersistentModelIndex(self._view.indexAt(event.position().toPoint()))
            if hovered != self._hover:
                self._hover = hovered
                self._retarget()
        elif kind in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._hover = QPersistentModelIndex()
            self._retarget()
            if kind == QEvent.Type.Hide:
                self._animation.stop()
                self._finish_motion()
        elif kind in (QEvent.Type.Show, QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._connect_selection()
            self._retarget()
        return False

    def _visible(self, index):
        return index.isValid() and self._view.visualRect(index).intersects(self._view.viewport().rect())

    def _retarget(self, *_):
        if self._view is None:
            return
        self._animation.stop()
        candidates = set(self._levels) | {self._hover}
        if self._selection is not None:
            candidates.update(QPersistentModelIndex(i) for i in self._selection.selectedIndexes())
        self._starts, self._targets = {}, {}
        for index in candidates:
            if not self._visible(index):
                continue
            target = min(1.0, (0.65 if self._selection and self._selection.isSelected(index) else 0.0)
                         + (0.35 if index == self._hover else 0.0))
            self._starts[index] = self._levels.get(index, 0.0)
            self._targets[index] = target
        self._levels = dict(self._starts)
        reduced = os.environ.get("NEXUS_REDUCED_MOTION", "").lower() in ("1", "true", "yes", "on")
        if reduced or not windows_design.motion_enabled() or not self._view.isVisible():
            self._frame(1.0)
            self._finish_motion()
        elif any(self._starts[i] != v for i, v in self._targets.items()):
            self._animation.start()

    def _frame(self, progress):
        for index, target in self._targets.items():
            self._levels[index] = self._starts[index] + (target - self._starts[index]) * progress
            if self._visible(index):
                self._view.viewport().update(self._view.visualRect(index))

    def _finish_motion(self):
        self._levels = {i: v for i, v in self._targets.items() if v and self._visible(i)}
        self._starts.clear()
        self._targets.clear()

    TITLE_HEIGHT = 20

    def set_editing_item(self, item_id):
        """While the inline title editor covers a card, its title is not painted
        underneath it (the editor background can be translucent)."""
        self._editing_item_id = item_id

    @classmethod
    def title_rect(cls, card_rect):
        """Where a custom title is drawn (and edited) inside a card's rect."""
        outer = QRectF(card_rect.adjusted(CARD_MARGIN, CARD_MARGIN, -CARD_MARGIN, -CARD_MARGIN))
        _, body, _ = cls._regions(outer)
        return QRectF(body.left(), body.top(), body.width(), cls.TITLE_HEIGHT)

    @staticmethod
    def _regions(outer):
        """Disjoint clipping budgets, including at the 148px minimum."""
        inner = outer.adjusted(14, 0, -14, 0)
        return (QRectF(inner.left(), outer.top() + 10, inner.width(), 22),
                QRectF(inner.left(), outer.top() + 42, inner.width(), outer.height() - 76),
                QRectF(inner.left(), outer.bottom() - 26, inner.width(), 16))

    @staticmethod
    def _text(painter, rect, text, size=13, color=None, weight=QFont.Weight.Normal, mono=False, spacing=0.0):
        """Line-based wrapping with an explicitly elided final line."""
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        font = windows_design.font(size, weight=weight, mono=mono)
        if spacing:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        painter.setFont(font)
        painter.setPen(QColor(color))
        metrics = QFontMetrics(font)
        lines = max(1, int(rect.height()) // metrics.lineSpacing())
        remaining = str(text).replace("\r", "").strip()[:4096]
        for line in range(lines):
            if not remaining:
                break
            if line == lines - 1:
                part = metrics.elidedText(" ".join(remaining.split()), Qt.TextElideMode.ElideRight, int(rect.width()))
                remaining = ""
            else:
                paragraph, _, tail = remaining.partition("\n")
                length = len(paragraph)
                if metrics.horizontalAdvance(paragraph) > rect.width():
                    low, high = 1, len(paragraph)
                    while low < high:
                        mid = (low + high + 1) // 2
                        if metrics.horizontalAdvance(paragraph[:mid]) <= rect.width():
                            low = mid
                        else:
                            high = mid - 1
                    length = low
                    space = paragraph.rfind(" ", 0, length + 1)
                    if space > 0:
                        length = space
                    part, remaining = paragraph[:length], remaining[length:].lstrip()
                else:
                    part, remaining = paragraph, tail
            painter.drawText(QRectF(rect.left(), rect.top() + line * metrics.lineSpacing(), rect.width(), metrics.lineSpacing()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, part)
        painter.restore()

    @classmethod
    def badge_rect(cls, card_rect):
        """The quick-paste number (1-9) in a card's header."""
        outer = QRectF(card_rect.adjusted(CARD_MARGIN, CARD_MARGIN, -CARD_MARGIN, -CARD_MARGIN))
        header, _, _ = cls._regions(outer)
        return QRectF(header.right() - 20, header.top() + 1, 20, 19)

    def helpEvent(self, event, view, option, index):
        # The bare "1".."9" badge says nothing on its own (H06/H10): explain it on hover.
        if (event.type() == QEvent.Type.ToolTip and index.isValid() and index.row() < 9
                and self.badge_rect(option.rect).adjusted(-4, -4, 4, 4).contains(QPointF(event.pos()))):
            number = index.row() + 1
            shortcut = (getattr(view.window(), "_local_shortcuts", None) or {}).get("quick_paste", {}).get(number)
            keys = shortcut.key().toString(QKeySequence.SequenceFormat.NativeText) if shortcut is not None else ""
            QToolTip.showText(event.globalPos(), tr("Pegado rápido · {keys}", keys=keys or f"Ctrl+{number}"), view)
            return True
        return super().helpEvent(event, view, option, index)

    def paint(self, painter, option, index):
        item = index.data(ITEM_ROLE)
        if item is None:
            return
        tokens = windows_design.palette()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focus = bool(option.state & QStyle.StateFlag.State_HasFocus)
        level = self._levels.get(QPersistentModelIndex(index), min(1.0, selected * .65 + hovered * .35))
        outer = QRectF(option.rect.adjusted(CARD_MARGIN, CARD_MARGIN, -CARD_MARGIN, -CARD_MARGIN))
        header, body, footer = self._regions(outer)
        detected = _detect(item.kind, item.text_content)
        semantic = {"url": "#6098ce", "youtube": "#6098ce", "code": "#a28bd0", "json": "#a28bd0",
                    "command": "#a28bd0", "image": "#69a98b", "file": "#c39c5d", "path": "#c39c5d"}.get(detected, "#d28b81")
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(outer, 16, 16)
        glass = windows_design.glass()
        painter.fillPath(shape, self._card_fill(_mix(tokens.card, tokens.card_hover, level), outer, selected))
        if selected and glass is not None:
            # Selection tints the glass with the accent, eased by the same animation level.
            tint = QColor(tokens.accent_fill)
            tint.setAlpha(round(tint.alpha() * min(1.0, level / .65)))
            painter.fillPath(shape, tint)
        painter.setPen(QPen(QColor(tokens.accent if selected else tokens.border_hover if hovered else tokens.border), 1.5 if selected else 1))
        painter.drawPath(shape)
        optical = getattr(self, "_optical", None)
        if optical is not None and glass is not None and optical.active():
            from nexus.ui import optical as windows_optical
            windows_optical.specular(painter, outer, 16, glass.dark, .22 if glass.dark else .4)
            windows_optical.rim(painter, outer, 16, glass.dark, strength=.75 if not selected else .5)
        painter.setClipPath(shape)
        band = QRectF(outer.left(), outer.top(), outer.width(), 38)
        painter.fillRect(band, _mix(tokens.card, semantic, .10 + level * .06))
        if glass is not None:
            # Reflection on the top edge of the glass card: one inner line, fading to the corners.
            rim = QColor(glass.rim_top)
            rim.setAlpha(round(rim.alpha() * (.55 if glass.dark else .9)))
            painter.setPen(QPen(rim, 1))
            painter.drawLine(QPointF(outer.left() + 16, outer.top() + 1.5), QPointF(outer.right() - 16, outer.top() + 1.5))
        painter.save()
        painter.setClipRect(header, Qt.ClipOperation.IntersectClip)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(semantic))
        painter.drawRoundedRect(QRectF(header.left(), header.top() + 6, 3, 10), 1.5, 1.5)
        # Type label: small, uppercase and quiet, so title and content lead the hierarchy.
        self._text(painter, header.adjusted(10, 0, -34, 0), TYPE_LABELS.get(detected, tr("Texto")).upper(), 10,
                   tokens.muted, QFont.Weight.DemiBold, spacing=.7)
        if index.row() < 9:
            badge = self.badge_rect(option.rect)
            painter.setBrush(QColor(tokens.panel_raised))
            painter.drawRoundedRect(badge, 6, 6)
            painter.setFont(windows_design.font(10, weight=QFont.Weight.DemiBold))
            painter.setPen(QColor(tokens.muted))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(index.row() + 1))
        painter.restore()
        custom_title = getattr(item, "title", None)
        content_color = tokens.text
        if custom_title:
            # The label heads the body; the real content shrinks and dims below.
            if item.id != getattr(self, "_editing_item_id", None):
                painter.save()
                painter.setClipRect(self.title_rect(option.rect), Qt.ClipOperation.IntersectClip)
                self._text(painter, self.title_rect(option.rect), custom_title, 13, tokens.text, QFont.Weight.DemiBold)
                painter.restore()
            body = QRectF(body.left(), body.top() + self.TITLE_HEIGHT + 2,
                          body.width(), max(14.0, body.height() - self.TITLE_HEIGHT - 2))
            content_color = tokens.muted
        painter.save()
        painter.setClipRect(body, Qt.ClipOperation.IntersectClip)
        preview = self._preview_data.get(item.id) or {}
        text = item.text_content or ""
        # Image rows are listed without their blob: only read it if the thumbnail
        # for this exact size is not cached yet (see storage._LazyImageClipItem).
        image = (item.kind == "image") if detected == "image" else preview.get("og_image") if detected in ("url", "youtube") else None
        # A title and domain are more useful than a tiny OG strip. Keep both
        # readable in compact mode rather than squeezing three regions together.
        if detected in ("url", "youtube") and body.height() < 96:
            image = None
        text_rect = QRectF(body)
        if image:
            height = int(body.height() if detected == "image" else max(16, body.height() * .42))
            dpr = painter.device().devicePixelRatioF()
            key = f"win:{item.content_hash}:{detected}"
            size = (max(1, round(body.width() * dpr)), max(1, round(height * dpr)), round(7 * dpr))
            thumb = thumbnails.cached_rounded_rect(key, *size)
            if thumb is None:
                data = item.image_data if detected == "image" else image
                thumb = thumbnails.rounded_rect_thumbnail(key, data, *size) if data else None
            if thumb is not None:
                painter.drawPixmap(QRectF(body.left(), body.top(), body.width(), height), thumb, QRectF(thumb.rect()))
                text_rect.setTop(body.top() + height + 6)
            else:
                image = None
        if detected in ("url", "youtube"):
            domain = preview.get("domain") or urlparse(text if "://" in text else "https://" + text).hostname or text
            title = preview.get("title") or domain
            title_rect = text_rect.adjusted(0, 0, 0, -18)
            self._text(painter, title_rect, title, 14 if not custom_title else 12, content_color, QFont.Weight.DemiBold)
            self._text(painter, QRectF(body.left(), body.bottom() - 15, body.width(), 15), domain, 11, tokens.muted)
        elif detected == "color":
            swatch = QRectF(body.left(), body.top(), 30, body.height())
            painter.fillRect(swatch, _parse_color(text) or QColor(tokens.accent))
            self._text(painter, body.adjusted(40, 0, 0, 0), text, 14, tokens.text, QFont.Weight.DemiBold)
        elif detected != "image" or not image:
            if detected == "file":
                text = (item.file_path or "").replace("\\", "/").rsplit("/", 1)[-1] or tr("Archivo")
            self._text(painter, text_rect, text or tr("Vista previa no disponible"), 13, content_color,
                       mono=detected in ("code", "json", "command"))
        painter.restore()
        painter.save()
        painter.setClipRect(footer, Qt.ClipOperation.IntersectClip)
        when = relative_time.format_relative(item.updated_at)
        time_width = min(footer.width() * .45, QFontMetrics(windows_design.font(11)).horizontalAdvance(when) + 2)
        self._text(painter, footer.adjusted(0, 0, -time_width - 8, 0), item.source_app or "Nexus", 11, tokens.muted)
        self._text(painter, QRectF(footer.right() - time_width, footer.top(), time_width, footer.height()), when, 11, tokens.faint)
        painter.restore()
        if focus:
            # Keyboard focus: a soft solid inner ring, distinct from the
            # selection border and from hover, readable in every theme.
            ring = QColor(tokens.accent)
            ring.setAlpha(150)
            painter.setPen(QPen(ring, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(outer.adjusted(3, 3, -3, -3), 13, 13)
        painter.restore()
