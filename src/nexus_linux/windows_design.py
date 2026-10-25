"""Opt-in Windows visual system. Shared Linux themes are never mutated.

All dimensions are logical Qt pixels. No fonts are installed system-wide;
Segoe UI Variable ships with Windows 11, Segoe UI is the native fallback.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from . import styles


@dataclass(frozen=True)
class Palette:
    panel: str
    panel_raised: str
    card: str
    card_hover: str
    border: str
    border_hover: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_fill: str
    accent_ink: str = "#101A28"


PALETTES = {
    "liquid_glass": Palette("#171C25", "#202733", "#252D3A", "#303C4D", "#3D4859", "#6F809A", "#F3F6FC", "#B1BDCF", "#93A2B8", "#B3CFF6", "#334965"),
    "minimalist": Palette("#1B1E23", "#232830", "#292F38", "#333C48", "#414955", "#727F90", "#F5F7FA", "#B4BDCB", "#959FAE", "#D6DFEC", "#3D4858"),
    "midnight_teal": Palette("#06151B", "#0B242A", "#10383D", "#164A4E", "rgba(78, 221, 213, 0.22)", "rgba(78, 221, 213, 0.55)", "#E6FFFC", "#C4E4E2", "#A8CFCC", "#4EDDD5", "rgba(78, 221, 213, 0.16)", "#00201E"),
    "storem": Palette("#211E19", "#29261F", "#322D25", "#40392F", "#554A3B", "#88755C", "#FAF3E8", "#CCC0AA", "#B2A48E", "#E0BF85", "#58492E", "#201B11"),
    "tech_neon": Palette("#080B12", "#0D131C", "#101722", "#17202D", "rgba(0, 229, 255, 0.42)", "rgba(255, 43, 214, 0.55)", "#E6FFFF", "#C2D6E4", "#AEC5D5", "#00E5FF", "rgba(0, 229, 255, 0.15)", "#001416"),
}

SPACING = (4, 8, 12, 16, 20, 24, 32)
# Surface paint begins 8px inside the widget; preserve 28px of visible padding.
SURFACE_MARGIN = 36
BLOCK_GAP, INTERNAL_GAP = 20, 12
WORKAREA_SIDE, WORKAREA_BOTTOM = 24, 28
RADIUS_PANEL, RADIUS_CARD, RADIUS_CONTROL = 22, 16, 10
MOTION_FAST_MS, MOTION_OPEN_MS, MOTION_PAGE_MS = 90, 130, 90
MOTION_HOVER_MS, MOTION_FOCUS_MS = 150, 110


def palette() -> Palette:
    return PALETTES.get(styles.active_theme().key, PALETTES["liquid_glass"])


@lru_cache(maxsize=1)
def font_family() -> str:
    if QGuiApplication.instance() is None:
        return "Segoe UI"
    # The Windows offscreen plugin exposes no system fonts. Register the OS
    # fonts in THIS PROCESS for faithful screenshot/tests, never install fonts.
    if QGuiApplication.platformName() == "offscreen" and not QFontDatabase.families():
        directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("SegUIVar.ttf", "segoeui.ttf", "segoeuib.ttf", "consola.ttf", "seguisym.ttf", "seguiemj.ttf"):
            if (directory / name).is_file():
                QFontDatabase.addApplicationFont(str(directory / name))
    available = set(QFontDatabase.families())
    return next((name for name in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Arial") if name in available), "Segoe UI")


def font(pixel_size=13, weight=QFont.Weight.Normal, mono=False) -> QFont:
    result = QFont("Consolas" if mono else font_family())
    result.setPixelSize(pixel_size)
    result.setWeight(weight)
    if mono:
        result.setStyleHint(QFont.StyleHint.Monospace)
    return result


def motion_enabled() -> bool:
    if os.environ.get("NEXUS_REDUCED_MOTION", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    enabled = ctypes.c_int(1)
    # Read the user's accessibility preference, do not change it.
    if os.name == "nt":
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
    return bool(enabled.value)


def theme_choices():
    return [(key, {"liquid_glass": "Liquid Glass", "midnight_teal": "Midnight Teal"}.get(key, label))
            for key, label in styles.theme_choices()]


@lru_cache(maxsize=128)
def icon(name, color, size=18) -> QIcon:
    """Small crisp line icons, no platform theme or Unicode glyph dependency."""
    image = QPixmap(size * 2, size * 2)
    image.setDevicePixelRatio(2)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 20, size / 20)
    painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if name == "search":
        painter.drawEllipse(QRectF(3, 3, 10, 10)); painter.drawLine(QPointF(12, 12), QPointF(17, 17))
    elif name == "close":
        painter.drawLine(5, 5, 15, 15); painter.drawLine(15, 5, 5, 15)
    elif name in ("down", "up"):
        y = 8 if name == "down" else 12
        middle = 12 if name == "down" else 8
        painter.drawLine(5, y, 10, middle); painter.drawLine(10, middle, 15, y)
    elif name == "plus":
        painter.drawLine(4, 10, 16, 10); painter.drawLine(10, 4, 10, 16)
    elif name == "history":
        painter.drawEllipse(QRectF(3, 3, 14, 14)); painter.drawLine(10, 6, 10, 10); painter.drawLine(10, 10, 13, 12)
    elif name == "board":
        painter.drawRoundedRect(QRectF(3, 3, 14, 14), 3, 3); painter.drawLine(8, 4, 8, 16)
    elif name == "back":
        painter.drawLine(15, 10, 5, 10); painter.drawLine(5, 10, 9, 6); painter.drawLine(5, 10, 9, 14)
    elif name == "brand":
        painter.drawRoundedRect(QRectF(2, 2, 16, 16), 5, 5)
        painter.drawLine(7, 13, 7, 7); painter.drawLine(7, 7, 13, 13); painter.drawLine(13, 13, 13, 7)
    elif name == "text":
        painter.drawLine(4, 5, 16, 5); painter.drawLine(4, 10, 16, 10); painter.drawLine(4, 15, 12, 15)
    elif name == "file":
        painter.drawRoundedRect(QRectF(4, 2, 12, 16), 2, 2); painter.drawLine(11, 2, 11, 7); painter.drawLine(11, 7, 16, 7)
    elif name == "link":
        painter.drawRoundedRect(QRectF(2, 6, 10, 8), 4, 4); painter.drawRoundedRect(QRectF(8, 6, 10, 8), 4, 4)
    painter.end()
    return QIcon(image)


def qss() -> str:
    p = palette()
    family = font_family()
    glass = styles.active_theme().liquid_glass
    # Liquid Glass controls are translucent enough to reveal the native
    # material, never flat opaque grey rectangles.
    control_bg = "rgba(48, 78, 104, 96)" if glass else p.panel_raised
    control_hover = "rgba(64, 100, 130, 126)" if glass else p.card_hover
    card_bg = "rgba(42, 70, 94, 90)" if glass else p.card
    card_hover = "rgba(60, 92, 120, 118)" if glass else p.card_hover
    control_border = "rgba(204, 232, 255, 92)" if glass else p.border
    control_border_hover = "rgba(235, 248, 255, 166)" if glass else p.border_hover
    menu_bg = "rgba(10, 18, 28, 244)" if glass else p.panel_raised
    # Keep shared editor/row selectors as a compatibility base, then replace
    # every surface/control family for Windows. Never replace styles.PRESETS.
    return styles.qss() + f"""
    * {{ font-family: "{family}"; }}
    QWidget {{ background: transparent; color: {p.text}; font-size: 13px; }}
    QWidget#overlayWindow, QWidget#panelSurface {{ background: transparent; border: none; }}
    QStackedWidget, QScrollArea, QAbstractScrollArea {{ background: transparent; border: none; }}
    QLabel {{ background: transparent; border: none; }}
    QLabel#titleCompact {{ font-size: 19px; font-weight: 700; letter-spacing: -0.6px; color: {p.text}; }}
    QLabel#title {{ font-size: 19px; font-weight: 600; letter-spacing: -0.4px; }}
    QLabel#subtitle, QLabel#rowMeta {{ color: {p.muted}; font-size: 11px; }}
    QLabel#rowTitle {{ font-size: 13px; font-weight: 500; color: {p.text}; }}
    QLabel#contextEyebrow {{ color: {p.faint}; font-size: 10px; font-weight: 600; letter-spacing: 1.2px; }}
    QLabel#successFeedback {{ color: {p.accent}; font-size: 11px; font-weight: 600; }}
    QLabel#badgeCompact, QLabel#badge {{ background: {p.accent_fill}; color: {p.accent}; border-radius: 7px; padding: 3px 7px; font-size: 10px; font-weight: 600; }}
    QLineEdit {{ background: {control_bg}; border: 1px solid {control_border}; border-radius: 10px;
        padding: 9px 12px; color: {p.text}; font-size: 13px; selection-background-color: {p.accent_fill}; }}
    QLineEdit:hover {{ background: {control_hover}; border-color: {control_border_hover}; }}
    QLineEdit:focus {{ background: {card_hover}; border: 1px solid {p.accent}; }}
    QLineEdit:disabled {{ color: {p.faint}; border-color: {control_border}; }}
    QLineEdit#quickSearch {{ padding: 9px 12px; border-radius: 12px; font-size: 13px;
        background: {control_bg}; border-color: {control_border}; }}
    QPushButton {{ background: {control_bg}; border: 1px solid {control_border}; border-radius: 10px;
        padding: 7px 12px; color: {p.muted}; font-size: 12px; font-weight: 500; }}
    QPushButton:hover {{ background: {control_hover}; border-color: {control_border_hover}; color: {p.text}; }}
    QPushButton:pressed {{ background: {p.accent_fill}; border-color: {p.accent}; }}
    QPushButton:checked {{ background: {p.accent_fill}; color: {p.accent}; border-color: {p.border_hover}; }}
    QPushButton:focus {{ border: 1px solid {p.accent}; color: {p.text}; }}
    QPushButton:disabled {{ color: {p.faint}; background: {control_bg}; border-color: {control_border}; }}
    QPushButton#primary {{ background: {p.accent}; color: {p.accent_ink}; border: 1px solid {p.accent}; font-weight: 600; padding: 9px 16px; }}
    QPushButton#primary:hover {{ background: {p.text}; color: {p.accent_ink}; border-color: {p.text}; }}
    QPushButton#primary:disabled {{ background: {p.card}; color: {p.faint}; border-color: {p.border}; }}
    QPushButton#flat, QPushButton#expandToggle {{ background: transparent; border: 1px solid transparent; border-radius: 9px; color: {p.muted}; padding: 6px 10px; font-size: 11px; }}
    QPushButton#flat:hover, QPushButton#expandToggle:hover {{ background: {card_bg}; border-color: {control_border}; color: {p.text}; }}
    QPushButton#flat:focus, QPushButton#expandToggle:focus {{ border-color: {p.accent}; }}
    QToolButton#navigationOptions {{ background: transparent; border: 1px solid transparent; border-radius: 9px;
        color: {p.muted}; padding: 0; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; font-size: 16px; }}
    QToolButton#navigationOptions:hover {{ background: {card_bg}; border-color: {control_border}; color: {p.text}; }}
    QToolButton#navigationOptions:checked {{ background: {p.accent_fill}; border-color: {p.border_hover}; color: {p.accent}; }}
    QPushButton#pinboardChipCompact {{ background: transparent; border: 1px solid transparent; border-radius: 10px;
        color: {p.muted}; font-size: 12px; font-weight: 500; padding: 7px 12px; }}
    QPushButton#pinboardChipCompact:hover {{ background: {card_bg}; color: {p.text}; border-color: {control_border}; }}
    QPushButton#pinboardChipCompact:checked, QPushButton#flat:checked, QPushButton#toggleActive {{
        background: {p.accent_fill}; color: {p.accent}; border: 1px solid {p.border_hover}; font-weight: 600; }}
    QPushButton#pinboardChipCompact:focus {{ border-color: {p.accent}; }}
    QPushButton#closeButton {{ background: transparent; border: 1px solid transparent; border-radius: 9px;
        min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0; color: {p.muted}; font-size: 18px; }}
    QPushButton#closeButton:hover {{ background: {card_bg}; border-color: {control_border}; color: {p.text}; }}
    QPushButton#closeButton:focus {{ border-color: {p.accent}; }}
    QPushButton[colorSwatch="true"] {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 0; }}
    QPushButton[colorSwatch="true"]:hover {{ background: {p.card_hover}; }}
    QPushButton[colorSwatch="true"]:focus {{ background: {p.accent_fill}; border-color: {p.accent}; }}
    QWidget#clipRow {{ background: {card_bg}; border: 1px solid {control_border}; border-radius: 13px; }}
    QWidget#clipRow[hovered="true"] {{ background: {card_hover}; border-color: {control_border_hover}; }}
    QWidget#clipRow[selected="true"] {{ background: {p.accent_fill}; border-color: {p.accent}; }}
    QWidget#iconBadge {{ background: {control_bg}; border: 1px solid {control_border}; border-radius: 10px; }}
    QListWidget#sidebarList::item {{ color: {p.muted}; padding: 9px 10px; margin: 2px 0; border-radius: 9px; font-size: 12px; }}
    QListWidget#sidebarList::item:selected {{ background: {p.accent_fill}; color: {p.accent}; font-weight: 600; }}
    QListWidget#sidebarList::item:hover {{ background: {card_bg}; color: {p.text}; }}
    QLabel#emptyState {{ color: {p.muted}; font-size: 14px; padding: 20px; background: transparent; border: none; }}
    QLineEdit#titleEditor {{ padding: 0 6px; border-radius: 7px; font-size: 13px; font-weight: 600;
        background: {card_hover}; border: 1px solid {p.accent}; }}
    QLabel#settingsHeading {{ color: {p.text}; font-size: 14px; font-weight: 600; }}
    QLabel#settingsSub, QLabel#shortcutHint {{ color: {p.muted}; font-size: 11px; }}
    QLabel#shortcutLabel {{ color: {p.text}; font-size: 12px; font-weight: 600; }}
    QLabel#scopePill {{ background: {control_bg}; color: {p.muted}; border-radius: 7px; padding: 2px 8px; font-size: 10px; font-weight: 600; }}
    QLabel#scopePill[scope="global"] {{ background: {p.accent_fill}; color: {p.accent}; }}
    QLabel#keyPill {{ background: transparent; color: {p.muted}; border: 1px dashed {control_border}; border-radius: 9px; padding: 6px 10px; font-size: 12px; }}
    QLabel#statusOk {{ color: {p.accent}; font-size: 11px; }}
    QLabel#statusError {{ color: #e07a6f; font-size: 11px; }}
    QKeySequenceEdit#keyEdit QLineEdit {{ padding: 6px 10px; border-radius: 9px; font-size: 12px; font-weight: 600; }}
    QPushButton#captureToggle {{ padding: 8px 16px; font-size: 12px; }}
    QDialog#detailsDialog {{ background: {menu_bg}; border: 1px solid {control_border_hover}; }}
    QLabel#detailKey {{ color: {p.faint}; font-size: 11px; }}
    QLabel#detailValue {{ color: {p.text}; font-size: 12px; }}
    QMenu, QToolTip, QMessageBox {{ background: {menu_bg}; color: {p.text}; border: 1px solid {control_border_hover}; border-radius: 11px; }}
    QMenu {{ padding: 6px; }}
    QMenu::item {{ padding: 8px 28px 8px 12px; border-radius: 7px; color: {p.muted}; font-size: 12px; }}
    QMenu::item:selected {{ background: {p.accent_fill}; color: {p.text}; }}
    QMenu::item:disabled {{ color: {p.faint}; background: transparent; }}
    QMenu::item:checked {{ color: {p.accent}; }}
    QMenu::separator {{ background: {p.border}; height: 1px; margin: 5px 7px; }}
    QToolTip {{ padding: 6px 9px; font-size: 11px; }}
    QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 0; }}
    QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
    QScrollBar::handle:horizontal, QScrollBar::handle:vertical {{ background: {p.border_hover}; border-radius: 3px; min-width: 24px; min-height: 24px; }}
    QScrollBar::handle:hover {{ background: {p.accent}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; background: transparent; border: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QPushButton::menu-indicator {{ subcontrol-origin: padding; subcontrol-position: center right; right: 3px; width: 8px; }}
    """
