"""Multi-theme visual system, adapted from Nexus's ThemeSpec/ThemeManager
(Models/Theme.swift, Services/ThemeManager.swift, Views/Themes/*.swift).

The original ships 5 themes: Liquid Glass (default), Minimalista, Material 3
Expressive ("midnight teal"), Storem, Tech Neon. Liquid Glass is skipped here
-- true backdrop blur ("ultraThinMaterial") has no equivalent in Qt Widgets
without fighting KWin's blur effect per-window -- the other four are real,
selectable palettes (Theme dataclass + PRESETS below), matching the
original's actual hex values, fonts, and radii where they translate to Qt
(QSS has no per-card variable corner radii or ambient glow the way SwiftUI
does, so those go through apply_glow()/apply_card_shadow() instead).

Current theme is persisted in paths.CONFIG_DIR / "theme.json" and switched
from the tray menu (see app.py); qss() is applied at the QApplication level
so every window updates the instant it changes, no per-widget bookkeeping."""

import json
import re
from dataclasses import dataclass, replace

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from . import paths


@dataclass(frozen=True)
class Theme:
    key: str
    display_name: str
    bg: str
    bg_elevated: str
    card_bg: str
    card_bg_hover: str
    card_border: str
    card_border_hover: str
    accent: str
    accent_dim: str
    accent_ink: str  # text color painted ON TOP of a solid accent fill
    secondary: str
    text_primary: str
    text_secondary: str
    text_tertiary: str
    danger: str
    font_family: str
    radius_panel: int
    radius_card: int
    radius_chip: int
    glow: bool = False


# text_tertiary carries real text (card time/source, row meta): every preset
# keeps it at >= 4.5:1 on both bg and card_bg (it was 2.5-3.7:1 before 2026-11).
PRESETS: dict[str, Theme] = {
    "midnight_teal": Theme(
        key="midnight_teal",
        display_name="Material 3 (midnight teal)",
        bg="#0A0F1A", bg_elevated="#0D141F",
        card_bg="#111B28", card_bg_hover="#16222F",
        card_border="rgba(126, 247, 230, 0.16)", card_border_hover="rgba(126, 247, 230, 0.38)",
        accent="#00E6F0", accent_dim="rgba(0, 230, 240, 0.14)", accent_ink="#001416",
        secondary="#7EF7E6",
        text_primary="#EEF6F7", text_secondary="#8CA3AA", text_tertiary="#76878F",
        danger="#FF7A7A",
        font_family='"Inter", "Inter Display", "Cantarell", -apple-system, sans-serif',
        radius_panel=18, radius_card=12, radius_chip=9,
        glow=True,
    ),
    "minimalist": Theme(
        key="minimalist",
        display_name="Minimalista",
        bg="#1A1A1A", bg_elevated="#202020",
        card_bg="#242424", card_bg_hover="#2C2C2C",
        card_border="rgba(255, 255, 255, 0.08)", card_border_hover="rgba(255, 255, 255, 0.18)",
        accent="#DDDDDD", accent_dim="rgba(221, 221, 221, 0.12)", accent_ink="#1A1A1A",
        secondary="#FFFFFF",
        text_primary="#EAEAEA", text_secondary="#9A9A9A", text_tertiary="#8C8C8C",
        danger="#E5796B",
        font_family='"Inter", "Cantarell", -apple-system, sans-serif',
        radius_panel=10, radius_card=8, radius_chip=6,
        glow=False,
    ),
    "storem": Theme(
        key="storem",
        display_name="Storem",
        bg="#080807", bg_elevated="#0D0D0B",
        card_bg="#121210", card_bg_hover="#191916",
        card_border="rgba(184, 148, 60, 0.45)", card_border_hover="rgba(216, 178, 115, 0.7)",
        accent="#D8B273", accent_dim="rgba(216, 178, 115, 0.14)", accent_ink="#1A1408",
        secondary="#B8943C",
        text_primary="#EDE6D6", text_secondary="#9A917C", text_tertiary="#837E71",
        danger="#C97B6A",
        font_family='"Noto Serif", "Georgia", serif',
        radius_panel=12, radius_card=10, radius_chip=8,
        glow=False,
    ),
    "tech_neon": Theme(
        key="tech_neon",
        display_name="Tech Neon",
        bg="#0A0E27", bg_elevated="#0D1230",
        card_bg="#101540", card_bg_hover="#161C4E",
        card_border="rgba(0, 255, 255, 0.6)", card_border_hover="rgba(255, 0, 255, 0.55)",
        accent="#00FFFF", accent_dim="rgba(0, 255, 255, 0.16)", accent_ink="#001414",
        secondary="#FF00FF",
        text_primary="#D9FDFF", text_secondary="rgba(217, 253, 255, 0.75)", text_tertiary="rgba(217, 253, 255, 0.5)",
        danger="#FF5C8A",
        font_family='"Noto Sans Mono", "JetBrains Mono", monospace',
        radius_panel=6, radius_card=6, radius_chip=6,
        glow=True,
    ),
}

# Keep all original palettes. Glass is an opaque, readable Linux adaptation:
# compositor blur is optional, never required for contrast.
PRESETS["liquid_glass"] = replace(PRESETS["midnight_teal"], key="liquid_glass",
    display_name="Liquid Glass", bg="#101824", bg_elevated="#1B2838",
    card_bg="#243244", card_bg_hover="#304258", accent="#82C7FF",
    secondary="#B8DCFF", text_primary="#F7FAFF", text_secondary="#C0CBD8",
    text_tertiary="#8F9CA3", glow=False)  # tertiary: >= 4.5:1 on its lighter cards
PRESETS["light"] = replace(PRESETS["minimalist"], key="light", display_name="Claro",
    bg="#F5F7FA", bg_elevated="#FFFFFF", card_bg="#FFFFFF", card_bg_hover="#E8EEF8",
    card_border="#D3DAE5", card_border_hover="#8CA2C2", accent="#255BC7",
    accent_dim="#E0E9FA", accent_ink="#FFFFFF", secondary="#365A91",
    text_primary="#172338", text_secondary="#4E6078", text_tertiary="#627086", danger="#B52B40")
PRESETS["dark"] = replace(PRESETS["minimalist"], key="dark", display_name="Oscuro")
PRESETS["auto"] = replace(PRESETS["midnight_teal"], key="auto", display_name="Automático · Caelestia")

DEFAULT_THEME = "auto"
_THEME_FILE = None  # resolved lazily so tests/imports don't touch disk


def _theme_file():
    global _THEME_FILE
    if _THEME_FILE is None:
        paths.ensure_dirs()
        _THEME_FILE = paths.CONFIG_DIR / "theme.json"
    return _THEME_FILE


def _load_theme_key() -> str:
    f = _theme_file()
    if f.exists():
        try:
            value = json.loads(f.read_text())
            key = value.get("theme") if isinstance(value, dict) else None
            if key in PRESETS:
                return key
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_THEME


_active_key = _load_theme_key()


def active_theme() -> Theme:
    return PRESETS[_active_key]


def set_active_theme(key: str) -> None:
    global _active_key
    if key not in PRESETS:
        return
    _active_key = key
    try:
        target = _theme_file()
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps({"theme": key}))
        temp.chmod(0o600)
        temp.replace(target)
    except OSError:
        pass


def theme_choices() -> list[tuple[str, str]]:
    """(key, display_name) pairs in a stable, deliberate order."""
    order = ["auto", "light", "dark", "liquid_glass", "midnight_teal", "minimalist", "storem", "tech_neon"]
    return [(k, PRESETS[k].display_name) for k in order + (["caelestia"] if "caelestia" in PRESETS else [])]


# -- Back-compat module-level constants (kept for callers not yet migrated to
# reading the active Theme object directly); refreshed by qss() every call so
# they always reflect whichever theme is currently active. --
BG = ""
CARD_BG = ""
ACCENT = ""
ACCENT_DIM = ""
TEXT_SECONDARY = ""
TEXT_TERTIARY = ""

RADIUS_BADGE = 17  # half of row_widget.BADGE_SIZE (34) -- a true circle chip


def colour(value: str) -> QColor:
    """QSS rgba values are not CSS strings accepted by QColor itself."""
    match = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", value)
    if not match:
        return QColor(value)
    red, green, blue, alpha = match.groups()
    alpha = float(alpha)
    return QColor(int(red), int(green), int(blue), round(alpha * 255 if alpha <= 1 else alpha))


def qss() -> str:
    t = active_theme()
    global BG, CARD_BG, ACCENT, ACCENT_DIM, TEXT_SECONDARY, TEXT_TERTIARY
    BG, CARD_BG, ACCENT, ACCENT_DIM = t.bg, t.card_bg, t.accent, t.accent_dim
    TEXT_SECONDARY, TEXT_TERTIARY = t.text_secondary, t.text_tertiary

    return f"""
* {{
    font-family: {t.font_family};
}}

QWidget {{
    background-color: {t.bg};
    color: {t.text_primary};
    font-size: 13px;
}}

QWidget#panelRoot {{
    background-color: {t.bg};
    border-radius: {t.radius_panel}px;
}}

QLabel#title {{
    font-size: 19px;
    font-weight: 600;
    letter-spacing: -0.2px;
    color: {t.text_primary};
}}

QLabel#subtitle {{
    color: {t.text_secondary};
    font-size: 12px;
}}

QLabel#rowTitle {{
    color: {t.text_primary};
    font-size: 13px;
    font-weight: 500;
    background: transparent;
}}

QLabel#rowMeta {{
    color: {t.text_tertiary};
    font-size: 12px;
    background: transparent;
}}

QLineEdit {{
    background-color: {t.card_bg};
    border: 1px solid {t.card_border};
    border-radius: {t.radius_chip}px;
    padding: 9px 13px;
    color: {t.text_primary};
    selection-background-color: {t.accent_dim};
    font-size: 13px;
}}

QLineEdit:focus {{
    border: 1px solid {t.accent};
    background-color: {t.bg_elevated};
}}

QListWidget {{
    background-color: transparent;
    border: none;
    outline: none;
}}

QListWidget::item {{
    background-color: transparent;
    border: none;
    border-radius: {t.radius_card}px;
    padding: 0px;
    margin: 3px 1px;
}}

QListWidget::item:selected {{
    background-color: transparent;
}}

QWidget#clipRow {{
    background-color: {t.card_bg};
    border: 1px solid {t.card_border};
    border-radius: {t.radius_card}px;
}}

QWidget#clipRow[hovered="true"] {{
    background-color: {t.card_bg_hover};
    border: 1px solid {t.card_border_hover};
}}

QWidget#clipRow[selected="true"] {{
    background-color: {t.accent_dim};
    border: 1px solid {t.accent};
}}

QWidget#iconBadge {{
    background-color: {t.accent_dim};
    border: 1px solid {t.card_border};
    border-radius: {RADIUS_BADGE}px;
}}

QListWidget#sidebarList::item {{
    background-color: transparent;
    border: none;
    border-radius: {t.radius_chip}px;
    padding: 7px 9px;
    margin: 1px 0;
    color: {t.text_secondary};
    font-size: 12.5px;
}}

QListWidget#sidebarList::item:hover {{
    background-color: {t.card_bg_hover};
    border: none;
}}

QListWidget#sidebarList::item:selected {{
    background-color: {t.accent_dim};
    border: none;
    color: {t.accent};
    font-weight: 600;
}}

QSplitter::handle {{
    background-color: {t.card_border};
}}

QPushButton {{
    background-color: {t.card_bg};
    border: 1px solid {t.card_border};
    border-radius: {t.radius_chip}px;
    padding: 8px 16px;
    color: {t.text_primary};
    font-size: 12.5px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {t.card_bg_hover};
    border: 1px solid {t.card_border_hover};
}}

QPushButton:pressed {{
    background-color: {t.accent_dim};
}}

QPushButton#primary {{
    background-color: {t.accent};
    color: {t.accent_ink};
    border: none;
    font-weight: 600;
    padding: 10px 18px;
}}

QPushButton#primary:hover {{
    background-color: {t.secondary};
}}

QPushButton#primary:disabled {{
    background-color: {t.card_bg};
    color: {t.text_tertiary};
}}

QPushButton#danger {{
    color: {t.danger};
    border-color: {t.card_border};
}}

QPushButton#danger:hover {{
    background-color: {t.accent_dim};
    border-color: {t.card_border_hover};
}}

QPushButton#flat {{
    background-color: transparent;
    border: none;
    color: {t.text_secondary};
    font-weight: 500;
}}

QPushButton#flat:hover {{
    color: {t.text_primary};
    background-color: {t.card_bg_hover};
}}

QPushButton#flat:checked {{
    background-color: {t.accent_dim};
    color: {t.accent};
    font-weight: 600;
}}

QPushButton#toggleActive {{
    background-color: {t.accent_dim};
    border: 1px solid {t.accent};
    color: {t.accent};
    font-weight: 600;
}}

QMenu {{
    background-color: {t.card_bg};
    border: 1px solid {t.card_border};
    border-radius: {t.radius_chip}px;
    padding: 6px;
    color: {t.text_primary};
}}

QMenu::item {{
    padding: 7px 22px;
    border-radius: 7px;
    font-size: 12.5px;
}}

QMenu::separator {{
    height: 1px;
    background: {t.card_border};
    margin: 6px 4px;
}}

QMenu::item:selected {{
    background-color: {t.accent_dim};
    color: {t.accent};
}}

QMenu::item:checked {{
    color: {t.accent};
    font-weight: 600;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {t.card_border_hover};
    border-radius: 4px;
    min-height: 28px;
}}

QScrollBar::handle:vertical:hover {{
    background: {t.accent};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QLabel#badge {{
    background-color: {t.accent_dim};
    color: {t.accent};
    border-radius: 7px;
    padding: 2px 8px;
    font-size: 12px;
    font-weight: 600;
}}

QLabel#titleCompact {{
    font-size: 12px;
    font-weight: 700;
    letter-spacing: -0.1px;
    color: {t.text_primary};
}}

QLabel#badgeCompact {{
    background-color: {t.accent_dim};
    color: {t.accent};
    border-radius: 6px;
    padding: 0px 6px;
    font-size: 11px;
    font-weight: 600;
}}

QLineEdit#quickSearch {{
    padding: 2px 9px;
    font-size: 12px;
}}

QPushButton#pinboardChipCompact {{
    background-color: transparent;
    border: none;
    color: {t.text_secondary};
    font-weight: 500;
    font-size: 12px;
    padding: 2px 8px;
}}

QPushButton#pinboardChipCompact:hover {{
    color: {t.text_primary};
    background-color: {t.card_bg_hover};
}}

QPushButton#pinboardChipCompact:checked {{
    background-color: {t.accent_dim};
    color: {t.accent};
    font-weight: 600;
}}

QPushButton#expandToggle {{
    background-color: transparent;
    border: 1px solid {t.card_border};
    border-radius: {t.radius_chip}px;
    color: {t.text_secondary};
    font-size: 11px;
    font-weight: 600;
    padding: 2px 7px;
}}

QPushButton#expandToggle:hover {{
    color: {t.text_primary};
    border-color: {t.card_border_hover};
    background-color: {t.card_bg_hover};
}}

QLabel#emptyState {{
    color: {t.text_secondary};
    font-size: 13px;
}}
"""


_icon_cache: dict[tuple[str, str, int], QIcon] = {}


def icon(name: str, color: str | None = None, size: int = 16) -> QIcon:
    """A KDE Breeze icon recolored to fit the theme -- native-feeling glyphs
    (matching what the user already sees everywhere else in Plasma) instead
    of emoji, tinted so they read correctly on the always-dark background
    regardless of which icon theme/light-dark variant the desktop is set to.
    color defaults to the active theme's secondary text color."""
    if color is None:
        color = active_theme().text_secondary
    key = (name, color, size)
    if key in _icon_cache:
        return _icon_cache[key]

    source = QIcon.fromTheme(name)
    pixmap = source.pixmap(QSize(size, size))
    if pixmap.isNull():
        _icon_cache[key] = QIcon()
        return _icon_cache[key]

    tinted = QPixmap(pixmap.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(color))
    painter.end()

    result = QIcon(tinted)
    _icon_cache[key] = result
    return result


def clear_icon_cache() -> None:
    """Call after switching themes -- cached icons were tinted with the
    previous theme's colors and QIcon has no way to re-tint in place."""
    _icon_cache.clear()


KIND_ICON_NAMES = {
    "text": "text-x-generic",
    "url": "link",
    "image": "image-x-generic",
    "file": "package-x-generic",
}


def kind_icon(kind: str, color: str | None = None, size: int = 17) -> QIcon:
    if color is None:
        color = active_theme().accent
    return icon(KIND_ICON_NAMES.get(kind, "text-x-generic"), color, size)


def apply_glow(widget: QWidget, color: str | None = None, radius: int = 18, opacity: int = 120) -> None:
    """Mirrors ThemeSpec.glow on ClipItemCard -- a soft ambient glow, applied
    via QGraphicsDropShadowEffect since QSS has no box-shadow equivalent."""
    if color is None:
        color = active_theme().accent
    effect = QGraphicsDropShadowEffect(widget)
    qcolor = QColor(color)
    qcolor.setAlpha(opacity)
    effect.setColor(qcolor)
    effect.setBlurRadius(radius)
    effect.setOffset(0, 0)
    widget.setGraphicsEffect(effect)


def apply_card_shadow(widget: QWidget, opacity: int = 90, radius: int = 22, y_offset: int = 6) -> None:
    """A soft, low, dark elevation shadow -- the macOS "card floating above
    the background" look -- rather than the neon glow above."""
    effect = QGraphicsDropShadowEffect(widget)
    color = QColor("#000000")
    color.setAlpha(opacity)
    effect.setColor(color)
    effect.setBlurRadius(radius)
    effect.setOffset(0, y_offset)
    widget.setGraphicsEffect(effect)
