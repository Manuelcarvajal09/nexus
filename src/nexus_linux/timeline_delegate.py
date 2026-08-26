"""Paints Timeline cards. A QStyledItemDelegate, not a QWidget-per-row: Qt
only calls paint() for rows actually inside the viewport, which is the
entire point (see timeline_model.py's docstring) -- this delegate must
never attach a QGraphicsEffect or otherwise create a persistent widget,
or it would reintroduce the exact per-row cost Phase 1 spent an audit
finding and fixing."""

import json

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from . import relative_time, styles, thumbnails, type_detection
from .timeline_model import ITEM_ROLE

CARD_WIDTH = 216
CARD_HEIGHT = 148
CARD_MARGIN = 6
CARD_PADDING = 10
THUMB_HEIGHT = 88

TYPE_ICON_NAMES = {
    "text": "text-x-generic",
    "url": "link",
    "youtube": "video-x-generic",
    "email": "mail-message",
    "phone": "call-start",
    "path": "folder",
    "command": "utilities-terminal",
    "json": "application-json",
    "code": "text-x-script",
}

TYPE_LABELS = {
    "text": "Texto", "url": "Enlace", "youtube": "YouTube", "image": "Imagen",
    "file": "Archivo", "color": "Color", "code": "Código", "json": "JSON",
    "command": "Comando", "path": "Ruta", "email": "Email", "phone": "Teléfono",
}


def _parse_color(text: str) -> QColor | None:
    text = text.strip()
    color = QColor(text)
    if color.isValid() and (text.startswith("#") or text.lower().startswith(("rgb", "hsl"))):
        return color
    return None


def _mono_font(base_size: int = 11) -> QFont:
    font = QFont("JetBrains Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    if font.family() != "JetBrains Mono":
        font = QFont("Noto Sans Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(base_size)
    return font


class ClipCardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_WIDTH, CARD_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        item = index.data(ITEM_ROLE)
        if item is None:
            return
        t = styles.active_theme()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        outer = option.rect.adjusted(CARD_MARGIN, CARD_MARGIN, -CARD_MARGIN, -CARD_MARGIN)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        bg = QColor(t.accent_dim) if selected else QColor(t.card_bg_hover if hovered else t.card_bg)
        border = QColor(t.accent) if selected else QColor(t.card_border_hover if hovered else t.card_border)
        path = QPainterPath()
        path.addRoundedRect(QRectF(outer), t.radius_card, t.radius_card)
        painter.fillPath(path, bg)
        painter.setPen(border)
        painter.drawPath(path)
        # Content must never bleed past the card's own rounded border --
        # QPainter.drawText() does not clip to the rect it's given by
        # default, only wraps, so a slightly-too-long line would otherwise
        # paint straight through into the next card's gap.
        painter.setClipPath(path)

        content = outer.adjusted(CARD_PADDING, CARD_PADDING, -CARD_PADDING, -CARD_PADDING)
        detected = type_detection.detect(item.kind, item.text_content)

        if detected in ("image", "youtube") and item.kind == "image" and item.image_data:
            self._paint_image_card(painter, content, item, detected, t)
        elif detected == "color":
            self._paint_color_card(painter, content, item, t)
        elif detected in ("code", "json"):
            self._paint_code_card(painter, content, item, detected, t)
        elif item.kind == "file":
            self._paint_file_card(painter, content, item, t)
        else:
            self._paint_text_card(painter, content, item, detected, t)

        # Quick Paste badge (Ctrl+1..9) -- purely positional, only the first
        # 9 visible-order rows ever get one. Row index comes from the model,
        # not from what's scrolled into view, matching Paste's own
        # "current list" semantics (see PASTE_PRODUCT_ANALYSIS.md §10).
        if 0 <= index.row() < 9:
            self._paint_quick_paste_badge(painter, outer, index.row() + 1, t)

        painter.restore()

    # -- per-type painters ------------------------------------------------

    def _paint_image_card(self, painter, rect, item, detected, t) -> None:
        thumb_rect = rect.adjusted(0, 0, 0, -(rect.height() - THUMB_HEIGHT))
        pixmap = thumbnails.rounded_rect_thumbnail(
            item.content_hash, item.image_data, thumb_rect.width(), THUMB_HEIGHT, t.radius_chip,
        )
        painter.drawPixmap(thumb_rect.topLeft(), pixmap)
        meta_rect = rect.adjusted(0, THUMB_HEIGHT + 6, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, TYPE_LABELS.get(detected, "Imagen"), t)

    def _paint_color_card(self, painter, rect, item, t) -> None:
        color = _parse_color(item.text_content or "") or QColor(t.accent)
        swatch_rect = rect.adjusted(0, 0, 0, -(rect.height() - THUMB_HEIGHT))
        swatch_path = QPainterPath()
        swatch_path.addRoundedRect(QRectF(swatch_rect), t.radius_chip, t.radius_chip)
        painter.fillPath(swatch_path, color)
        painter.setPen(QColor(t.card_border))
        painter.drawPath(swatch_path)

        value_font = QFont(t.font_family.split(",")[0].strip(' "'))
        value_font.setPointSize(11)
        value_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(value_font)
        painter.setPen(QColor(t.text_primary))
        value_rect = rect.adjusted(0, THUMB_HEIGHT + 8, 0, 0)
        painter.drawText(value_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, item.text_content.strip())

        meta_rect = value_rect.adjusted(0, 20, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, "Color", t)

    def _paint_code_card(self, painter, rect, item, detected, t) -> None:
        text = item.text_content or ""
        if detected == "json":
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except (ValueError, TypeError):
                pass

        painter.setFont(_mono_font())
        painter.setPen(QColor(t.text_primary))
        code_rect = rect.adjusted(0, 0, 0, -20)
        painter.drawText(
            code_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            text,
        )
        meta_rect = rect.adjusted(0, rect.height() - 16, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, TYPE_LABELS.get(detected, "Código"), t)

    def _paint_file_card(self, painter, rect, item, t) -> None:
        icon_pix = styles.kind_icon("file", color=t.accent, size=28).pixmap(28, 28)
        painter.drawPixmap(rect.left(), rect.top(), icon_pix)
        name_rect = rect.adjusted(0, 34, 0, -20)
        title_font = QFont(t.font_family.split(",")[0].strip(' "'))
        title_font.setPointSize(11)
        title_font.setWeight(QFont.Weight.Medium)
        painter.setFont(title_font)
        painter.setPen(QColor(t.text_primary))
        filename = (item.file_path or "").rsplit("/", 1)[-1] or "[archivo]"
        painter.drawText(
            name_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            filename,
        )
        meta_rect = rect.adjusted(0, rect.height() - 16, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, "Archivo", t)

    def _paint_text_card(self, painter, rect, item, detected, t) -> None:
        icon_name = TYPE_ICON_NAMES.get(detected, "text-x-generic")
        icon_pix = styles.icon(icon_name, color=t.accent, size=20).pixmap(20, 20)
        painter.drawPixmap(rect.left(), rect.top(), icon_pix)

        title_font = QFont(t.font_family.split(",")[0].strip(' "'))
        title_font.setPointSize(11)
        painter.setFont(title_font)
        painter.setPen(QColor(t.text_primary))
        text_rect = rect.adjusted(0, 28, 0, -20)
        preview = (item.text_content or item.preview(140)).strip()
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            preview[:220],
        )

        meta_rect = rect.adjusted(0, rect.height() - 16, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, TYPE_LABELS.get(detected, "Texto"), t)

    def _draw_meta_line(self, painter, rect, item, type_label: str, t) -> None:
        meta_font = QFont(t.font_family.split(",")[0].strip(' "'))
        meta_font.setPointSize(9)
        painter.setFont(meta_font)
        painter.setPen(QColor(t.text_tertiary))
        when = relative_time.format_relative(item.updated_at)
        source = item.source_app or type_label
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{source} · {when}")

    def _paint_quick_paste_badge(self, painter, outer_rect, number: int, t) -> None:
        size = 18
        badge_rect = QRectF(outer_rect.right() - size - 4, outer_rect.top() + 4, size, size)
        path = QPainterPath()
        path.addEllipse(badge_rect)
        painter.fillPath(path, QColor(t.bg_elevated))
        painter.setPen(QColor(t.card_border))
        painter.drawPath(path)

        font = QFont(t.font_family.split(",")[0].strip(' "'))
        font.setPointSize(9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(t.text_secondary))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(number))
