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


# Liquid Glass is the only adaptive theme: Dark and Light share one identity
# (same geometry, same light-blue N, same rim/specular language) and differ in
# tint, ink and fills. Colours written "#AARRGGBB" are translucent: the panel is
# real DWM Acrylic, and cards/controls are glass layers composited over it, so
# their *effective* contrast depends on what is behind Nexus (see test_glass_contrast).
LIQUID_GLASS_DARK = Palette(
    panel="#171C25", panel_raised="#1AFFFFFF", card="#8A0A0F18", card_hover="#9E111826",
    border="#29FFFFFF", border_hover="#59FFFFFF", text="#F6F8FC", muted="#C9D3E1", faint="#B7C2D3",
    accent="#B3CFF6", accent_fill="#1FB3CFF6", accent_ink="#101A28")
LIQUID_GLASS_LIGHT = Palette(
    panel="#F4F6FA", panel_raised="#B3FFFFFF", card="#99FFFFFF", card_hover="#B8FFFFFF",
    border="#26111827", border_hover="#4D111827", text="#111827", muted="#344054", faint="#475467",
    accent="#2563C9", accent_fill="#1A2563C9", accent_ink="#FFFFFF")


@dataclass(frozen=True)
class Glass:
    """Painted layers of the glass material (all "#AARRGGBB"; see Palette)."""
    dark: bool
    tint: str           # flat tint laid over the Acrylic
    specular: str       # soft reflection along the top edge
    rim_top: str        # inner highlight line (top edge only: DWM owns the single outline)
    control: str
    control_hover: str
    control_border: str
    control_border_hover: str
    menu: str           # pop-up menus are separate windows: nearly opaque
    dwm_border: int     # COLORREF (0x00BBGGRR) for the single native contour


GLASS_DARK = Glass(True, tint="#260B1019", specular="#14FFFFFF", rim_top="#52FFFFFF",
                   control="#14FFFFFF", control_hover="#24FFFFFF", control_border="#2EFFFFFF",
                   control_border_hover="#66FFFFFF", menu="#F2141A26", dwm_border=0x635346)
GLASS_LIGHT = Glass(False, tint="#47FFFFFF", specular="#59FFFFFF", rim_top="#E6FFFFFF",
                    control="#80FFFFFF", control_hover="#B3FFFFFF", control_border="#2E111827",
                    control_border_hover="#59111827", menu="#F5F8FAFD", dwm_border=0xD1C3B9)


PALETTES = {
    "liquid_glass": LIQUID_GLASS_DARK,
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
STATUS_ERROR = "#F28B82"  # error text on the dark themes: >= 4.5:1 on panel and card
STATUS_ERROR_LIGHT = "#B42318"  # Liquid Glass Light: the pink above was only 2.2:1 on its pale panel
MOTION_THEME_MS = 140  # Liquid Glass Light <-> Dark: content eases, the HWND never fades
MOTION_OPEN_CONTENT_MS = 110  # content easing in when the panel opens (the HWND never fades)
MOTION_HOVER_MS, MOTION_FOCUS_MS = 150, 110


def is_adaptive() -> bool:
    """Only Liquid Glass follows the Windows light/dark preference."""
    return styles.active_theme().key == "liquid_glass"


def is_light() -> bool:
    if not is_adaptive():
        return False
    from . import windows_system_theme
    return windows_system_theme.system_prefers_light()


def palette() -> Palette:
    if is_light():
        return LIQUID_GLASS_LIGHT
    return PALETTES.get(styles.active_theme().key, PALETTES["liquid_glass"])


def status_error() -> str:
    """Error ink that stays readable (>= 4.5:1) on the active variant."""
    return STATUS_ERROR_LIGHT if is_light() else STATUS_ERROR


def glass() -> Glass | None:
    """Glass layer tokens for the active variant, or None for the solid themes."""
    if not styles.active_theme().liquid_glass:
        return None
    return GLASS_LIGHT if is_light() else GLASS_DARK


def native_variant() -> tuple[bool, int]:
    """(dark_mode, DWM border COLORREF) for the window the backdrop is applied to."""
    g = glass()
    return (g.dark, g.dwm_border) if g is not None else (True, 0x635346)


def rgba(color: str) -> str:
    """QSS spelling of a colour: "#AARRGGBB" becomes rgba(r, g, b, a) (integer
    alpha, which is what QSS honours reliably on Windows)."""
    if isinstance(color, str) and color.startswith("#") and len(color) == 9:
        a, r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5, 7))
        return f"rgba({r}, {g}, {b}, {a})"
    return color


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


@lru_cache(maxsize=128)
def _font(family, pixel_size, weight, mono) -> QFont:
    result = QFont(family)
    result.setPixelSize(pixel_size)
    result.setWeight(weight)
    if mono:
        result.setStyleHint(QFont.StyleHint.Monospace)
    return result


def font(pixel_size=13, weight=QFont.Weight.Normal, mono=False) -> QFont:
    # Card painting asks for ~5 fonts per card per frame: build each once and hand
    # out copies (QFont is implicitly shared, so a copy is cheap and safe to modify).
    return QFont(_font("Consolas" if mono else font_family(), pixel_size, weight, bool(mono)))


def motion_enabled() -> bool:
    if os.environ.get("NEXUS_REDUCED_MOTION", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    enabled = ctypes.c_int(1)
    # Read the user's accessibility preference, do not change it.
    if os.name == "nt":
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
    return bool(enabled.value)


def theme_choices():
    from .i18n import tr
    return [(key, {"liquid_glass": "Liquid Glass", "midnight_teal": "Midnight Teal"}.get(key, tr(label)))
            for key, label in styles.theme_choices()]


def _draw_icon(name, color, size, ratio) -> QPixmap:
    image = QPixmap(round(size * ratio), round(size * ratio))
    image.setDevicePixelRatio(ratio)
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
    elif name == "more":
        # Three dots on the optical centre: drawn, not the "⋯" glyph, whose
        # baseline and weight changed with the font and collided with the
        # native menu arrow.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        for x in (4.5, 10, 15.5):
            painter.drawEllipse(QPointF(x, 10), 1.55, 1.55)
    elif name == "chevron_down":
        painter.drawLine(QPointF(5.5, 8), QPointF(10, 12.5)); painter.drawLine(QPointF(10, 12.5), QPointF(14.5, 8))
    elif name == "chevron_right":
        painter.drawLine(QPointF(8, 5.5), QPointF(12.5, 10)); painter.drawLine(QPointF(12.5, 10), QPointF(8, 14.5))
    elif name == "chevron_left":
        painter.drawLine(QPointF(12, 5.5), QPointF(7.5, 10)); painter.drawLine(QPointF(7.5, 10), QPointF(12, 14.5))
    elif name == "check":
        painter.drawLine(QPointF(4.5, 10.5), QPointF(8.5, 14.5)); painter.drawLine(QPointF(8.5, 14.5), QPointF(15.5, 6))
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
    return image


@lru_cache(maxsize=256)
def icon(name, color, size=18) -> QIcon:
    """Small crisp line icons, no platform theme or Unicode glyph dependency.

    Rendered at 1x, 2x and 3x so Qt picks the sharp one at 100%-300% scaling."""
    result = QIcon()
    for ratio in (1, 2, 3):
        result.addPixmap(_draw_icon(name, color, size, ratio))
    return result


def qss() -> str:
    from dataclasses import astuple
    p = Palette(*[rgba(value) for value in astuple(palette())])   # QSS spelling of every colour
    family = font_family()
    g = glass()
    # Liquid Glass controls are translucent layers over the real Acrylic, never
    # flat grey rectangles; every other theme keeps its solid fills.
    control_bg = rgba(g.control) if g else p.panel_raised
    control_hover = rgba(g.control_hover) if g else p.card_hover
    soft_hover = control_hover if g else p.card        # hover of flat buttons, chips, close
    focus_bg = control_hover if g else p.card_hover
    card_bg, card_hover = p.card, p.card_hover
    control_border = rgba(g.control_border) if g else p.border
    control_border_hover = rgba(g.control_border_hover) if g else p.border_hover
    menu_bg = rgba(g.menu) if g else p.panel_raised
    # Keep shared editor/row selectors as a compatibility base, then replace
    # every surface/control family for Windows. Never replace styles.PRESETS.
    return styles.qss() + f"""
    * {{ font-family: "{family}"; }}
    QWidget {{ background: transparent; color: {p.text}; font-size: 13px; }}
    QWidget#overlayWindow, QWidget#panelSurface {{ background: transparent; border: none; }}
    QStackedWidget, QScrollArea, QAbstractScrollArea {{ background: transparent; border: none; }}
    QLabel {{ background: transparent; border: none; }}
    QLabel#titleCompact {{ font-size: 19px; font-weight: 700; letter-spacing: -0.6px; color: {p.text}; }}
    QLabel#title {{ font-size: 19px; font-weight: 600; letter-spacing: -0.4px; color: {p.text}; }}
    QLabel#subtitle, QLabel#rowMeta {{ color: {p.muted}; font-size: 11px; }}
    QLabel#rowTitle {{ font-size: 13px; font-weight: 500; color: {p.text}; }}
    QLabel#contextEyebrow {{ color: {p.faint}; font-size: 10px; font-weight: 600; letter-spacing: 1.2px; }}
    QLabel#successFeedback {{ color: {p.accent}; font-size: 11px; font-weight: 600; }}
    QLabel#badgeCompact, QLabel#badge {{ background: {p.accent_fill}; color: {p.accent}; border-radius: 7px; padding: 3px 7px; font-size: 10px; font-weight: 600; }}
    QLineEdit {{ background: {control_bg}; border: 1px solid {control_border}; border-radius: 10px;
        padding: 9px 12px; color: {p.text}; font-size: 13px; selection-background-color: {p.accent_fill}; }}
    QLineEdit:hover {{ background: {control_hover}; border-color: {control_border_hover}; }}
    QLineEdit:focus {{ background: {focus_bg}; border: 1px solid {p.accent}; }}
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
    QPushButton#flat:hover, QPushButton#expandToggle:hover {{ background: {soft_hover}; border-color: {control_border}; color: {p.text}; }}
    QPushButton#flat:focus, QPushButton#expandToggle:focus {{ border-color: {p.accent}; }}
    QToolButton#navigationOptions {{ background: transparent; border: 1px solid transparent; border-radius: 9px;
        color: {p.muted}; padding: 0; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; font-size: 16px; }}
    QToolButton#navigationOptions:hover {{ background: {soft_hover}; border-color: {control_border}; color: {p.text}; }}
    QToolButton#navigationOptions:checked {{ background: {p.accent_fill}; border-color: {p.border_hover}; color: {p.accent}; }}
    QPushButton#pinboardChipCompact {{ background: transparent; border: 1px solid transparent; border-radius: 10px;
        color: {p.muted}; font-size: 12px; font-weight: 500; padding: 7px 12px; }}
    QPushButton#pinboardChipCompact:hover {{ background: {soft_hover}; color: {p.text}; border-color: {control_border}; }}
    QPushButton#pinboardChipCompact:checked, QPushButton#flat:checked, QPushButton#toggleActive {{
        background: {p.accent_fill}; color: {p.accent}; border: 1px solid {p.border_hover}; font-weight: 600; }}
    QPushButton#pinboardChipCompact:focus {{ border-color: {p.accent}; }}
    QPushButton#closeButton {{ background: transparent; border: 1px solid transparent; border-radius: 9px;
        min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0; color: {p.muted}; font-size: 18px; }}
    QPushButton#closeButton:hover {{ background: {soft_hover}; border-color: {control_border}; color: {p.text}; }}
    QPushButton#closeButton:focus {{ border-color: {p.accent}; }}
    QToolButton#edgeHint {{ background: {menu_bg}; border: 1px solid {control_border_hover}; border-radius: 15px; padding: 0; }}
    QToolButton#edgeHint:hover {{ border-color: {p.accent}; }}
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
    QLabel#settingsPageTitle {{ color: {p.muted}; font-size: 12px; font-weight: 600; letter-spacing: .6px; }}
    QLabel#settingsSub, QLabel#shortcutHint {{ color: {p.muted}; font-size: 11px; }}
    QLabel#shortcutLabel {{ color: {p.text}; font-size: 12px; font-weight: 600; }}
    QLabel#scopePill {{ background: {control_bg}; color: {p.muted}; border-radius: 7px; padding: 2px 8px; font-size: 10px; font-weight: 600; }}
    QLabel#scopePill[scope="global"] {{ background: {p.accent_fill}; color: {p.accent}; }}
    QLabel#keyPill {{ background: transparent; color: {p.muted}; border: 1px dashed {control_border}; border-radius: 9px; padding: 6px 10px; font-size: 12px; }}
    QLabel#statusOk {{ color: {p.accent}; font-size: 11px; }}
    QLabel#statusError {{ color: {status_error()}; font-size: 11px; }}
    QKeySequenceEdit#keyEdit QLineEdit {{ padding: 6px 10px; border-radius: 9px; font-size: 12px; font-weight: 600; }}
    QPushButton#captureToggle, QPushButton#autostartToggle, QPushButton#previewsToggle {{ padding: 8px 16px; font-size: 12px; }}
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
    QScrollBar::handle:horizontal, QScrollBar::handle:vertical {{ background: {control_border_hover}; border-radius: 3px; min-width: 24px; min-height: 24px; }}
    QScrollBar::handle:hover {{ background: {p.accent}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; background: transparent; border: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    /* The native drop-down arrow is replaced by a drawn chevron icon: it used to collide with the glyph of the options button. */
    QPushButton::menu-indicator, QToolButton::menu-indicator {{ image: none; width: 0; height: 0; }}
    """ + (f"""
    /* Optical glass (windows_optical) paints the control's lens, veil and rim behind it: the widget itself
       only keeps a light hover wash and the accent focus border. */
    QLineEdit[opticalGlass="true"], QPushButton[opticalGlass="true"] {{ background: transparent; border-color: transparent; }}
    QLineEdit[opticalGlass="true"]:hover, QPushButton[opticalGlass="true"]:hover {{
        background: {"rgba(255, 255, 255, 0.10)" if g.dark else "rgba(255, 255, 255, 0.32)"}; border-color: transparent; }}
    QLineEdit[opticalGlass="true"]:focus, QPushButton[opticalGlass="true"]:focus {{ background: transparent; border: 1px solid {p.accent}; }}
    QPushButton[opticalGlass="true"]:checked {{ background: {p.accent_fill}; border-color: {p.border_hover}; }}
    """ if g else "")
