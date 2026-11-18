"""Virtualized vertical clip cards, without per-row widgets or full text layouts."""
from pathlib import Path
from PySide6.QtCore import QRectF,QSize,Qt
from PySide6.QtGui import QColor,QFont,QFontMetrics
from PySide6.QtWidgets import QStyledItemDelegate,QStyle
from . import styles,relative_time,thumbnails,product_icons
from .timeline_model import ITEM_ROLE
from .product_ui import ROW_HEIGHT,CARD_RADIUS

class HistoryDelegate(QStyledItemDelegate):
    def set_preview_data(self,data): self._preview_data=data
    def set_card_height(self,height): pass
    def sizeHint(self,option,index): return QSize(340,ROW_HEIGHT)
    def paint(self,painter,option,index):
        item=index.data(ITEM_ROLE)
        if item is None: return
        t=styles.active_theme(); painter.save(); painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect=QRectF(option.rect).adjusted(0,4,-2,-4)
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        hover=bool(option.state & QStyle.StateFlag.State_MouseOver)
        border=QColor(t.accent if selected else t.text_secondary)
        if not selected: border.setAlpha(36)
        painter.setPen(border)
        painter.setBrush(QColor(t.card_bg_hover if selected or hover else t.card_bg))
        painter.drawRoundedRect(rect,CARD_RADIUS,CARD_RADIUS)
        icon=product_icons.icon(item.kind,t.accent,20)
        icon.paint(painter,int(rect.x()+12),int(rect.y()+14),20,20)
        left=rect.x()+44; right=rect.right()-12
        if item.kind=='image' and item.image_data:
            thumb=thumbnails.rounded_rect_thumbnail(item.content_hash,item.image_data,56,56,8)
            painter.drawPixmap(int(rect.right()-68),int(rect.y()+12),thumb)
            right-=64
        painter.setPen(QColor(t.text_primary)); font=QFont('Inter');font.setPixelSize(14);painter.setFont(font)
        preview=item.preview(220).replace('\n',' ')
        if item.kind=='file': preview=Path(item.file_path or '').name or 'Archivo'
        painter.drawText(QRectF(left,rect.y()+12,right-left,24),Qt.AlignmentFlag.AlignVCenter,QFontMetrics(font).elidedText(preview,Qt.TextElideMode.ElideRight,int(right-left)))
        painter.setPen(QColor(t.text_secondary));font.setPixelSize(12);painter.setFont(font)
        label={'text':'Texto','url':'Enlace','image':'Imagen','file':'Archivo'}.get(item.kind,item.kind)
        if item.is_pinned: label+=' · Pinboard'
        painter.drawText(QRectF(left,rect.y()+42,right-left,20),Qt.AlignmentFlag.AlignVCenter,label)
        painter.restore()
