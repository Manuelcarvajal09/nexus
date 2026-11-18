"""Nexus shelf cards: clipped, local previews and bounded thumbnail cache."""
from pathlib import Path
from PySide6.QtCore import QRectF,QSize,Qt
from PySide6.QtGui import QColor,QFont,QFontMetrics,QPainterPath
from PySide6.QtWidgets import QStyledItemDelegate,QStyle
from . import styles,thumbnails,product_icons
from .timeline_model import ITEM_ROLE

class ShelfDelegate(QStyledItemDelegate):
    def set_preview_data(self,data):self._preview_data=data
    def set_card_height(self,height):pass
    card_width = 248

    def sizeHint(self, option, index):
        return QSize(self.card_width, 218)

    def paint(self,painter,option,index):
        item=index.data(ITEM_ROLE)
        if item is None:return
        t=styles.active_theme();painter.save();painter.setRenderHint(painter.RenderHint.Antialiasing)
        r=QRectF(option.rect).adjusted(5,5,-5,-5)
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        hover=bool(option.state & QStyle.StateFlag.State_MouseOver)
        border=styles.colour(t.accent if selected else t.text_secondary)
        if not selected:border.setAlpha(50 if hover else 25)
        painter.setBrush(styles.colour(t.card_bg_hover if selected or hover else t.card_bg));painter.setPen(border)
        painter.drawRoundedRect(r,14,14)
        path=QPainterPath();path.addRoundedRect(r,14,14);painter.setClipPath(path)
        product_icons.icon(item.kind,t.accent,18).paint(painter,int(r.x()+16),int(r.y()+16),18,18)
        font=QFont('Inter');font.setPixelSize(12);font.setWeight(QFont.Weight.DemiBold);painter.setFont(font);painter.setPen(styles.colour(t.text_secondary))
        label={'text':'TEXTO','url':'ENLACE','file':'ARCHIVO','image':'IMAGEN'}.get(item.kind,item.kind.upper())
        painter.drawText(QRectF(r.x()+44,r.y()+14,r.width()-60,24),Qt.AlignmentFlag.AlignVCenter,label)
        content=QRectF(r.x()+16,r.y()+54,r.width()-32,r.height()-98)
        painter.save();painter.setClipRect(content)
        if item.kind=='image' and item.image_data:
            thumb=thumbnails.rounded_rect_thumbnail(item.content_hash,item.image_data,int(content.width()),int(content.height()),9)
            painter.drawPixmap(int(content.x()),int(content.y()),thumb)
        else:
            font.setPixelSize(14);font.setWeight(QFont.Weight.Normal);painter.setFont(font);painter.setPen(styles.colour(t.text_primary))
            text=Path(item.file_path or '').name if item.kind=='file' else item.preview(180)
            painter.drawText(content,Qt.AlignmentFlag.AlignTop|Qt.TextFlag.TextWordWrap,text)
        painter.restore()
        font.setPixelSize(12);painter.setFont(font);painter.setPen(styles.colour(t.text_secondary))
        from .relative_time import format_relative
        meta = format_relative(item.updated_at) + (' · Guardado' if item.is_pinned else '')
        if index.row()<9:meta+=f'  ·  Ctrl+{index.row()+1}'
        painter.drawText(QRectF(r.x()+16,r.bottom()-30,r.width()-32,20),Qt.AlignmentFlag.AlignVCenter,meta)
        painter.restore()
