"""Paints Timeline cards. A QStyledItemDelegate, not a QWidget-per-row: Qt
only calls paint() for rows actually inside the viewport, which is the
entire point (see timeline_model.py's docstring) -- this delegate must
never attach a QGraphicsEffect or otherwise create a persistent widget,
or it would reintroduce the exact per-row cost Phase 1 spent an audit
finding and fixing."""

import json
from functools import lru_cache
from urllib.parse import urlparse

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from .i18n import plural, tr
from . import relative_time, styles, thumbnails, type_detection
from .timeline_model import ITEM_ROLE

CARD_WIDTH = 216
CARD_HEIGHT = 148
CARD_MARGIN = 6
CARD_PADDING = 10
THUMB_HEIGHT = 88

@lru_cache(maxsize=1024)
def _detect(kind, text):
    """type_detection.detect() parses the whole clip (json.loads, splitlines);
    paint() runs on every hover frame. Strings cache their hash: hits are cheap."""
    return type_detection.detect(kind, text)


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
    "text": tr("Texto"), "url": tr("Enlace"), "youtube": "YouTube", "image": tr("Imagen"),
    "file": tr("Archivo"), "color": tr("Color"), "code": tr("Código"), "json": "JSON",
    "command": tr("Comando"), "path": tr("Ruta"), "email": tr("Email"), "phone": tr("Teléfono"),
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
    """card_height defaults to the standard CARD_HEIGHT but is overridable
    per responsive mode (see panel_geometry.py) -- COMPACT/portrait uses a
    shorter card. thumb_height (the dominant image/color area) scales down
    proportionally so a shorter card doesn't just clip its thumbnail."""

    def __init__(self, parent=None, card_height: int = CARD_HEIGHT):
        super().__init__(parent)
        self.set_card_height(card_height)
        # Reference (not a copy) to TimelineWindow's {item_id: preview dict}
        # -- the window mutates this in place as PreviewService.preview_ready
        # fires and triggers a repaint; the delegate just reads whatever is
        # there at paint() time, synchronously, no network/blocking involved.
        self._preview_data: dict = {}

    def set_preview_data(self, preview_data: dict) -> None:
        self._preview_data = preview_data

    def set_card_height(self, card_height: int) -> None:
        self._card_height = card_height
        # THUMB_HEIGHT (88) leaves 60px below it for the meta line + padding
        # at the standard card height -- keep that same fixed footer budget
        # at any card height so the meta line never gets cramped or clipped.
        self._thumb_height = max(40, card_height - (CARD_HEIGHT - THUMB_HEIGHT))

    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_WIDTH, self._card_height)

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

        bg = styles.qcolor(t.accent_dim) if selected else styles.qcolor(t.card_bg_hover if hovered else t.card_bg)
        border = styles.qcolor(t.accent) if selected else styles.qcolor(t.card_border_hover if hovered else t.card_border)
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
        detected = _detect(item.kind, item.text_content)
        image_thumb = None
        if detected == "image" and item.kind == "image":
            thumb_rect = content.adjusted(0, 0, 0, -(content.height() - self._thumb_height))
            image_thumb = thumbnails.rounded_rect_for(item, thumb_rect.width(), self._thumb_height, t.radius_chip)

        if detected in ("url", "youtube"):
            self._paint_url_card(painter, content, item, detected, t)
        elif image_thumb is not None:
            self._paint_image_card(painter, content, item, detected, t, image_thumb)
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

    def _paint_url_card(self, painter, rect, item, detected, t) -> None:
        """Renders enriched (favicon/OG image/title) when PreviewService has
        already fetched it; otherwise the domain-only fallback -- this is
        the 'mostrar fallback' step of the architecture, not an error
        state. Never triggers a fetch itself: paint() must stay a pure,
        synchronous, local read (see class docstring)."""
        preview = self._preview_data.get(item.id)
        og_image = preview.get("og_image") if preview else None
        favicon = preview.get("favicon") if preview else None
        title = (preview.get("title") if preview else None) or (item.text_content or "").strip()
        domain = (preview.get("domain") if preview else None) or (urlparse(item.text_content or "").hostname or "")
        channel = preview.get("channel") if preview else None

        if og_image:
            thumb_rect = rect.adjusted(0, 0, 0, -(rect.height() - self._thumb_height))
            pixmap = thumbnails.rounded_rect_thumbnail(
                f"og:{item.content_hash}", og_image, thumb_rect.width(), self._thumb_height, t.radius_chip,
            )
            painter.drawPixmap(thumb_rect.topLeft(), pixmap)
            text_top = self._thumb_height + 6
        else:
            icon_size = 20
            if favicon:
                icon_pix = thumbnails.rounded_rect_thumbnail(
                    f"favicon:{item.content_hash}", favicon, icon_size, icon_size, 4,
                )
            else:
                icon_name = TYPE_ICON_NAMES.get(detected, "link")
                icon_pix = styles.icon(icon_name, color=t.accent, size=icon_size).pixmap(icon_size, icon_size)
            painter.drawPixmap(rect.left(), rect.top(), icon_pix)
            text_top = 28

        title_font = QFont(t.font_family.split(",")[0].strip(' "'))
        title_font.setPointSize(11)
        painter.setFont(title_font)
        painter.setPen(styles.qcolor(t.text_primary))
        text_rect = rect.adjusted(0, text_top, 0, -20)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            (title or domain)[:220],
        )

        label = "YouTube" if detected == "youtube" else domain
        if detected == "youtube" and channel:
            label = f"{channel} · YouTube"
        meta_rect = rect.adjusted(0, rect.height() - 16, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, label, t)

    def _paint_image_card(self, painter, rect, item, detected, t, pixmap=None) -> None:
        thumb_rect = rect.adjusted(0, 0, 0, -(rect.height() - self._thumb_height))
        if pixmap is None:
            pixmap = thumbnails.rounded_rect_for(item, thumb_rect.width(), self._thumb_height, t.radius_chip)
        if pixmap is None:
            return
        painter.drawPixmap(thumb_rect.topLeft(), pixmap)
        meta_rect = rect.adjusted(0, self._thumb_height + 6, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, TYPE_LABELS.get(detected, tr("Imagen")), t)

    def _paint_color_card(self, painter, rect, item, t) -> None:
        color = _parse_color(item.text_content or "") or styles.qcolor(t.accent)
        swatch_rect = rect.adjusted(0, 0, 0, -(rect.height() - self._thumb_height))
        swatch_path = QPainterPath()
        swatch_path.addRoundedRect(QRectF(swatch_rect), t.radius_chip, t.radius_chip)
        painter.fillPath(swatch_path, color)
        painter.setPen(styles.qcolor(t.card_border))
        painter.drawPath(swatch_path)

        value_font = QFont(t.font_family.split(",")[0].strip(' "'))
        value_font.setPointSize(11)
        value_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(value_font)
        painter.setPen(styles.qcolor(t.text_primary))
        value_rect = rect.adjusted(0, self._thumb_height + 8, 0, 0)
        painter.drawText(value_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, item.text_content.strip())

        meta_rect = value_rect.adjusted(0, 20, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, tr("Color"), t)

    def _paint_code_card(self, painter, rect, item, detected, t) -> None:
        text = item.text_content or ""
        if detected == "json":
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except (ValueError, TypeError):
                pass

        painter.setFont(_mono_font())
        painter.setPen(styles.qcolor(t.text_primary))
        code_rect = rect.adjusted(0, 0, 0, -20)
        painter.drawText(
            code_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            text,
        )
        meta_rect = rect.adjusted(0, rect.height() - 16, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, TYPE_LABELS.get(detected, tr("Código")), t)

    def _paint_file_card(self, painter, rect, item, t) -> None:
        icon_pix = styles.kind_icon("file", color=t.accent, size=28).pixmap(28, 28)
        painter.drawPixmap(rect.left(), rect.top(), icon_pix)
        name_rect = rect.adjusted(0, 34, 0, -20)
        title_font = QFont(t.font_family.split(",")[0].strip(' "'))
        title_font.setPointSize(11)
        title_font.setWeight(QFont.Weight.Medium)
        painter.setFont(title_font)
        painter.setPen(styles.qcolor(t.text_primary))
        filename = (item.file_path or "").rsplit("/", 1)[-1] or "[archivo]"
        painter.drawText(
            name_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            filename,
        )
        meta_rect = rect.adjusted(0, rect.height() - 16, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, tr("Archivo"), t)

    def _paint_text_card(self, painter, rect, item, detected, t) -> None:
        icon_name = TYPE_ICON_NAMES.get(detected, "text-x-generic")
        icon_pix = styles.icon(icon_name, color=t.accent, size=20).pixmap(20, 20)
        painter.drawPixmap(rect.left(), rect.top(), icon_pix)

        title_font = QFont(t.font_family.split(",")[0].strip(' "'))
        title_font.setPointSize(11)
        painter.setFont(title_font)
        painter.setPen(styles.qcolor(t.text_primary))
        text_rect = rect.adjusted(0, 28, 0, -20)
        preview = (item.text_content or item.preview(140)).strip()
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            preview[:220],
        )

        meta_rect = rect.adjusted(0, rect.height() - 16, 0, 0)
        self._draw_meta_line(painter, meta_rect, item, TYPE_LABELS.get(detected, tr("Texto")), t)

    def _draw_meta_line(self, painter, rect, item, type_label: str, t) -> None:
        meta_font = QFont(t.font_family.split(",")[0].strip(' "'))
        meta_font.setPointSize(9)
        painter.setFont(meta_font)
        painter.setPen(styles.qcolor(t.text_tertiary))
        when = relative_time.format_relative(item.updated_at)
        source = item.source_app or type_label
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{source} · {when}")

    def _paint_quick_paste_badge(self, painter, outer_rect, number: int, t) -> None:
        size = 18
        badge_rect = QRectF(outer_rect.right() - size - 4, outer_rect.top() + 4, size, size)
        path = QPainterPath()
        path.addEllipse(badge_rect)
        painter.fillPath(path, styles.qcolor(t.bg_elevated))
        painter.setPen(styles.qcolor(t.card_border))
        painter.drawPath(path)

        font = QFont(t.font_family.split(",")[0].strip(' "'))
        font.setPointSize(9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(styles.qcolor(t.text_secondary))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(number))
