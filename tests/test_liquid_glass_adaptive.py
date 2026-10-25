"""Liquid Glass Dark/Light: variant selection, QSS, effective contrast, backdrop wiring, icons."""
import ctypes
import re
from ctypes import wintypes
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractButton, QWidget

from nexus_linux import styles, windows_app, windows_design as design, windows_system_theme as system_theme
from nexus_linux.windows_backdrop import BackdropController, BackdropPreferences, NativeBackend


@pytest.fixture
def glass_theme(monkeypatch):
    monkeypatch.setattr(styles, "_active_key", "liquid_glass")


# -- variant selection --------------------------------------------------------

def test_liquid_glass_follows_the_system_theme(glass_theme):
    system_theme.set_override("dark")
    assert design.palette() is design.LIQUID_GLASS_DARK and design.glass() is design.GLASS_DARK and not design.is_light()
    assert design.native_variant() == (True, design.GLASS_DARK.dwm_border)
    system_theme.set_override("light")
    assert design.palette() is design.LIQUID_GLASS_LIGHT and design.glass() is design.GLASS_LIGHT and design.is_light()
    assert design.native_variant() == (False, design.GLASS_LIGHT.dwm_border)


@pytest.mark.parametrize("theme", ["midnight_teal", "minimalist", "storem", "tech_neon"])
def test_the_other_themes_ignore_the_system_theme(monkeypatch, theme):
    monkeypatch.setattr(styles, "_active_key", theme)
    system_theme.set_override("light")
    assert design.palette() is design.PALETTES[theme] and design.glass() is None
    assert not design.is_light() and not design.is_adaptive()
    assert design.native_variant() == (True, 0x635346)


def test_both_variants_share_one_identity(glass_theme):
    dark, light = design.LIQUID_GLASS_DARK, design.LIQUID_GLASS_LIGHT
    assert type(dark) is type(light) and dark.accent_ink and light.accent_ink
    # same translucent-glass language: fills carry alpha in both
    for palette in (dark, light):
        for token in (palette.card, palette.card_hover, palette.border, palette.panel_raised, palette.accent_fill):
            assert len(token) == 9 and token.startswith("#")
    assert design.GLASS_DARK.dark and not design.GLASS_LIGHT.dark


# -- QSS ------------------------------------------------------------------------

@pytest.mark.parametrize("variant", ["dark", "light"])
def test_qss_never_leaks_argb_hex_and_menus_stay_legible(glass_theme, variant):
    system_theme.set_override(variant)
    sheet = design.qss()
    assert not re.search(r"#[0-9A-Fa-f]{8}\b", sheet)            # QSS needs rgba(), not #AARRGGBB
    menu = re.search(r"QMenu, QToolTip, QMessageBox \{ background: rgba\((\d+), (\d+), (\d+), (\d+)\)", sheet)
    assert menu and int(menu.group(4)) >= 235                      # pop-ups are separate windows: near-opaque


def test_the_two_variants_produce_different_sheets(glass_theme):
    system_theme.set_override("dark"); dark = design.qss()
    system_theme.set_override("light"); light = design.qss()
    assert dark != light and design.LIQUID_GLASS_DARK.text.lower() in dark.lower() and design.LIQUID_GLASS_LIGHT.text.lower() in light.lower()


def test_native_menu_arrows_are_removed_in_every_theme():
    sheet = design.qss()
    assert re.search(r"QPushButton::menu-indicator, QToolButton::menu-indicator \{ image: none; width: 0; height: 0; \}", sheet)


# -- effective contrast over the real Acrylic ---------------------------------------
# DWM Acrylic is modelled from real captures taken on this machine: it keeps ~35% of the
# blurred backdrop and replaces the rest with its own tint (see tools/windows_glass_probe.py).
ACRYLIC = {"dark": ((28, 30, 36), .35), "light": ((235, 238, 244), .35)}
BACKDROPS = [(255, 255, 255), (200, 200, 200), (128, 128, 128), (60, 60, 60), (0, 0, 0),
             (232, 56, 106), (31, 111, 224), (255, 216, 74), (27, 32, 39)]


def over(top: QColor, bottom):
    a = top.alpha() / 255
    return tuple(top.getRgb()[i] * a + bottom[i] * (1 - a) for i in range(3))


def luminance_of(rgb):
    channels = [c / 255 for c in rgb]
    r, g, b = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in channels]
    return .2126 * r + .7152 * g + .0722 * b


def contrast(ink: QColor, background):
    a, b = luminance_of(over(ink, background)) if ink.alpha() < 255 else luminance_of(ink.getRgb()[:3]), luminance_of(background)
    return (max(a, b) + .05) / (min(a, b) + .05)


@pytest.mark.parametrize("variant", ["dark", "light"])
@pytest.mark.parametrize("state", ["card", "selected"])
@pytest.mark.parametrize("backdrop", BACKDROPS)
def test_text_stays_readable_on_glass_cards_over_any_backdrop(glass_theme, variant, state, backdrop):
    system_theme.set_override(variant)
    palette, g = design.palette(), design.glass()
    tint, keep = ACRYLIC[variant]
    acrylic = tuple(keep * backdrop[i] + (1 - keep) * tint[i] for i in range(3))
    panel = over(QColor(g.tint), acrylic)
    card = over(QColor(palette.card), panel)
    if state == "selected":
        card = over(QColor(palette.accent_fill), card)
    for name, minimum in (("text", 7.0), ("muted", 4.5), ("faint", 4.5)):
        ratio = contrast(QColor(getattr(palette, name)), card)
        assert ratio >= minimum, (variant, state, backdrop, name, round(ratio, 2))


@pytest.mark.parametrize("variant", ["dark", "light"])
@pytest.mark.parametrize("backdrop", BACKDROPS)
def test_text_and_controls_on_the_bare_glass_panel_are_readable(glass_theme, variant, backdrop):
    system_theme.set_override(variant)
    palette, g = design.palette(), design.glass()
    tint, keep = ACRYLIC[variant]
    acrylic = tuple(keep * backdrop[i] + (1 - keep) * tint[i] for i in range(3))
    panel = over(QColor(g.tint), acrylic)
    control = over(QColor(g.control), panel)
    for background in (panel, control):
        assert contrast(QColor(palette.text), background) >= 4.5, (variant, backdrop)
        assert contrast(QColor(palette.muted), background) >= 3.0, (variant, backdrop)   # secondary labels/eyebrow


# -- backdrop wiring ------------------------------------------------------------------

class FakeAPI:
    def __init__(self):
        self.attributes = {}
    def is_window(self, hwnd): return True
    def window_style(self, hwnd): return {"layered": False}
    def set_attribute(self, hwnd, attribute, value): self.attributes[attribute] = value; return 0
    def get_attribute(self, hwnd, attribute): return 0, 3
    def extend_frame(self, hwnd, margin): return 0


def ready_prefs(**overrides):
    values = dict(os_build=26300, composition=True, high_contrast=False, transparency=True, power_saver=False,
                  remote_session=False, can_native_paint=True)
    values.update(overrides)
    return BackdropPreferences(**values)


def test_light_variant_turns_the_acrylic_to_light_and_changes_the_border():
    for dark, border in ((True, 0x635346), (False, 0xD1C3B9)):
        api = FakeAPI()
        state = NativeBackend(api).apply(1234, "desktop_acrylic", ready_prefs(dark_mode=dark, border_color=border))
        assert state.native_enabled and api.attributes[20] == (1 if dark else 0)
        assert api.attributes[34] == border and api.attributes[38] == 3


def test_controller_rereads_the_variant_on_every_apply(qapp):
    seen = []
    class Backend:
        class api:
            @staticmethod
            def preferences(can_native_paint=False): return ready_prefs()
        def apply(self, hwnd, request, prefs):
            seen.append((prefs.dark_mode, prefs.border_color))
            return Mock(native_enabled=True)
    variant = [(True, 1)]
    widget = QWidget(); widget.show(); widget.winId()
    controller = BackdropController(widget, backend=Backend(), can_native_paint=lambda w: True,
                                    on_state=lambda s: None, variant=lambda: variant[0])
    controller._apply()
    variant[0] = (False, 2)
    controller._apply()
    assert seen == [(True, 1), (False, 2)]
    controller.close(); widget.close()


# -- the native message ------------------------------------------------------------------

def message(number, text=None):
    buffer = ctypes.create_unicode_buffer(text) if text is not None else None
    msg = wintypes.MSG()
    msg.message = number
    msg.lParam = ctypes.addressof(buffer) if buffer is not None else 0
    return msg, buffer


def test_wm_settingchange_for_the_color_set_reaches_the_theme_watcher(qapp):
    hits, hotkeys = [], []
    filter_ = windows_app.HotkeyFilter(hotkeys.append, on_theme_message=lambda: hits.append(True))
    for number, text, expected in ((0x001A, "ImmersiveColorSet", 1), (0x001A, "intl", 1), (0x0010, "ImmersiveColorSet", 1)):
        msg, keep = message(number, text)
        assert filter_.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)
        assert len(hits) == expected, (number, text)
    msg, keep = message(0x001A, "WindowsThemeElement")
    filter_.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
    assert len(hits) == 2 and hotkeys == []
    hot, keep = message(0x0312)
    hot.wParam = 1
    assert filter_.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(hot)) == (True, 0) and hotkeys == [1]


def test_motion_of_the_variant_switch_is_brief():
    assert 0 < design.MOTION_THEME_MS <= 150


# -- icons ---------------------------------------------------------------------------------------

def opaque_columns(pixmap):
    image = pixmap.toImage()
    return {x for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() > 40}


@pytest.mark.parametrize("name", ["more", "chevron_down", "chevron_right", "check", "close", "search", "history", "back", "brand"])
def test_every_icon_is_drawn_at_1x_2x_3x_and_not_empty(qapp, name):
    icon = design.icon(name, "#223344", 18)
    for ratio in (1, 2, 3):
        pixmap = icon.pixmap(QSize(18, 18), ratio)
        assert pixmap.width() == 18 * ratio and not pixmap.isNull()
        assert opaque_columns(pixmap), (name, ratio)


def test_options_icon_is_three_separate_dots(qapp):
    pixmap = design.icon("more", "#223344", 18).pixmap(QSize(18, 18), 3)
    columns = sorted(opaque_columns(pixmap))
    groups = 1 + sum(1 for a, b in zip(columns, columns[1:]) if b - a > 1)
    assert groups == 3


def test_icons_differ_by_name_and_follow_their_colour(qapp):
    a = design.icon("more", "#223344", 18).pixmap(18, 18).toImage()
    assert a != design.icon("chevron_down", "#223344", 18).pixmap(18, 18).toImage()
    assert a != design.icon("more", "#AA5533", 18).pixmap(18, 18).toImage()


def test_no_button_uses_a_unicode_glyph_as_an_icon(qapp, tmp_path, monkeypatch, paste_service):
    from nexus_linux import paths
    from nexus_linux.storage import StorageService
    from nexus_linux.windows_timeline import WindowsTimelineWindow
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    storage = StorageService(tmp_path / "icons.sqlite")
    window = WindowsTimelineWindow(storage, paste_service)
    window.apply_visual_theme()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)   # the removed Expandir button is gone for real
    glyphs = set("⋯⌄▾▸▴▲▼✕×‹›⟨⟩…")
    for button in window.findChildren(QAbstractButton):
        assert not (set(button.text()) & glyphs), repr(button.text())
    assert window._management_btn.text() == "" and not window._management_btn.icon().isNull()
    assert window._style_btn.text() == "Estilo" and not window._style_btn.icon().isNull()
    assert window._style_btn.layoutDirection().name == "RightToLeft"    # chevron sits after the label
    window._search_debounce.stop(); window._dismiss_timer.stop(); window.close(); window.deleteLater()
    storage.close()
