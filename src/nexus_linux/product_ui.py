"""Shared Nexus product tokens and native Wayland drag/resize affordances."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QLabel, QApplication, QWidget, QStyleOption, QStyle, QPushButton
import logging
from .settings import load_settings
from PySide6.QtGui import QPainter
from . import styles

SPACE = (4,8,12,16,24,32)
PANEL_RADIUS = 22
CARD_RADIUS = 12
ROW_HEIGHT = 88

def qss():
    t=styles.active_theme()
    return f'''
QWidget#panelRoot {{ background: {t.bg}; border: 1px solid {t.card_border}; border-radius: {PANEL_RADIUS}px; }}
QLineEdit {{ min-height: 32px; padding: 6px 12px; border-radius: 12px; border: 1px solid {t.card_border}; }}
QLineEdit:focus {{ border: 1px solid {t.accent}; }}
QPushButton {{ min-height: 24px; padding: 4px 10px; border-radius: 10px; font-size: 13px; }}
QPushButton:focus {{ border: 1px solid {t.accent}; }}
QPushButton:checked {{ background: {t.card_bg_hover}; color: {t.accent}; border: 1px solid {t.accent}; }}
QPushButton:disabled {{ color: {t.text_tertiary}; }}
QListView {{ background: transparent; border: none; outline: none; }}
QScrollBar:vertical {{ width: 6px; background: transparent; margin: 4px 0; }}
QScrollBar::handle:vertical {{ background: {t.card_border}; border-radius: 3px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{ height: 6px; background: transparent; margin: 0px 4px; }}
QScrollBar::handle:horizontal {{ background: {t.card_border}; border-radius: 3px; min-width: 28px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QComboBox {{ background: {t.bg_elevated}; color: {t.text_primary}; padding: 4px 10px; min-height: 24px; border: 1px solid {t.card_border}; border-radius: 10px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {t.bg_elevated}; color: {t.text_primary}; selection-background-color: {t.card_bg_hover}; }}
QListWidget#collections::item {{ padding: 9px 10px; border-radius: 10px; color: {t.text_secondary}; }}
QListWidget#collections::item:hover {{ background: {t.card_bg_hover}; }}
QListWidget#collections::item:selected {{ background: {t.accent_dim}; color: {t.text_primary}; border: 1px solid {t.accent}; }}
QPushButton#manageCollections {{ background: {t.bg_elevated}; border: 1px solid {t.card_border}; text-align: left; }}
QLabel#sectionTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#productTitle {{ font-size: 18px; font-weight: 600; }}
QLabel#productMeta {{ color: {t.text_secondary}; font-size: 12px; }}
'''

class DragTitle(QLabel):
    def mousePressEvent(self, event):
        if event.button()==Qt.MouseButton.LeftButton and self.window().windowHandle():
            accepted=self.window().windowHandle().startSystemMove()
            logging.getLogger("nexus.window").info("system-move accepted=%s",accepted)
            event.accept()
        else: super().mousePressEvent(event)

def hide_on_focus_loss():
    return load_settings().hide_on_focus_loss

class ResizeGrip(QWidget):
    """A real local pointer target; Qt forwards its current Wayland serial."""
    def __init__(self,window,edges,cursor):
        super().__init__(window)
        self.edges=edges
        self.setCursor(cursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("Redimensionar ventana")
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.window().windowHandle():
            accepted=self.window().windowHandle().startSystemResize(self.edges)
            logging.getLogger('nexus.window').info('system-resize edges=%s accepted=%s',self.edges,accepted)
            if accepted:
                event.accept();return
        super().mousePressEvent(event)

class ResizeEdges(QObject):
    def __init__(self,window):
        super().__init__(window)
        E=Qt.Edge; C=Qt.CursorShape
        specs=((E.LeftEdge,C.SizeHorCursor),(E.RightEdge,C.SizeHorCursor),
               (E.TopEdge,C.SizeVerCursor),(E.BottomEdge,C.SizeVerCursor),
               (E.TopEdge|E.LeftEdge,C.SizeFDiagCursor),(E.TopEdge|E.RightEdge,C.SizeBDiagCursor),
               (E.BottomEdge|E.LeftEdge,C.SizeBDiagCursor),(E.BottomEdge|E.RightEdge,C.SizeFDiagCursor))
        self.grips=[ResizeGrip(window,edges,cursor) for edges,cursor in specs]
        window.installEventFilter(self)
    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show):
            w,h=self.parent().width(),self.parent().height(); e=8;c=18
            boxes=((0,c,e,h-2*c),(w-e,c,e,h-2*c),(c,0,w-2*c,e),(c,h-e,w-2*c,e),
                   (0,0,c,c),(w-c,0,c,c),(0,h-c,c,c),(w-c,h-c,c,c))
            for grip,box in zip(self.grips,boxes):
                grip.setGeometry(*box);grip.show();grip.raise_()
        return False

def attach(window):
    window.setObjectName("panelRoot")
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    window.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    window.setMinimumSize(392,420)
    handler=ResizeEdges(window)
    # Grips receive local mouse events; no global-coordinate event filter.
    window._resize_edges=handler

class CloseButton(QPushButton):
    def __init__(self,window):
        super().__init__("×",window)
        self.setFixedSize(28,28)
        self.setStyleSheet("min-height: 0; padding: 0; font-size: 18px;")
        self.setToolTip("Ocultar Nexus · Escape")
        self.setAccessibleName("Ocultar Nexus")
        self.clicked.connect(window.fade_hide)

class Surface(QWidget):
    def closeEvent(self,event):
        if hasattr(self,'fade_hide'):
            self.fade_hide();event.ignore()
        else:super().closeEvent(event)
    def paintEvent(self,event):
        option=QStyleOption();option.initFrom(self)
        painter=QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget,option,painter,self)
        painter.end()
