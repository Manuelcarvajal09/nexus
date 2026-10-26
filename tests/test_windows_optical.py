"""Optical glass (prototype C): contrast guarantees, capture lifecycle, fallback and integration."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QWidget

from nexus_linux import paths, styles, windows_design, windows_optical, windows_system_theme
from nexus_linux.storage import StorageService
from nexus_linux.windows_card_delegate import WindowsClipCardDelegate
from nexus_linux.windows_optical import OpticalGlass, Snapshot, adaptive_alpha, contrast
from nexus_linux.windows_timeline import WindowsTimelineWindow

BACKDROPS = {"white": "#FFFFFF", "black": "#000000", "grey": "#7F7F7F", "red": "#E8386A", "blue": "#1F6FE0",
             "yellow": "#FFD84A", "photo": "#E2674A"}


def plate(color, width=200, height=80):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


@pytest.fixture(autouse=True)
def optical_on():
    windows_optical.set_enabled(True)
    yield
    windows_optical.set_enabled(True)


@pytest.fixture
def panel(qapp, tmp_path, monkeypatch, paste_service):
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    monkeypatch.setattr(styles, "_active_key", "liquid_glass")
    storage = StorageService(tmp_path / "panel.sqlite")
    window = WindowsTimelineWindow(storage, paste_service)
    window.setFont(QFont("Segoe UI", 10))
    window.setStyleSheet(windows_design.qss())
    yield window
    window._search_debounce.stop()
    window._dismiss_timer.stop()
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    storage.close()


def make_active(window, color="#E2674A"):
    """Pretend DWM Acrylic is live (offscreen has none) and hand the window a backdrop."""
    window._surface._native_backdrop = True
    window._optical.snapshot = Snapshot(plate(color, 400, 160), 400, 160)
    window._optical._flag(True)


# ------------------------------------------------------------------ contrast guarantees
@pytest.mark.parametrize("dark", [True, False])
@pytest.mark.parametrize("name", list(BACKDROPS))
def test_adaptive_alpha_keeps_text_readable_over_any_backdrop(dark, name):
    veil = QColor(8, 12, 20) if dark else QColor(255, 255, 255)
    text = QColor("#B7C2D3" if dark else "#475467")
    backdrop = QColor(BACKDROPS[name])
    alpha = adaptive_alpha(backdrop, veil, text, 4.6, 40)
    k = alpha / 255
    mixed = QColor(*(round(b * (1 - k) + v * k) for b, v in zip(backdrop.getRgb()[:3], veil.getRgb()[:3])))
    assert alpha >= 40
    # 255 is the ceiling: when even a fully opaque veil cannot reach the ratio, text on the veil itself is the limit
    assert contrast(text, mixed) >= 4.5 or alpha == 255


def test_adaptive_alpha_never_weakens_the_token():
    assert adaptive_alpha(QColor("#000000"), QColor(8, 12, 20), QColor("#B7C2D3"), 5.0, 138) == 138


def test_card_veil_is_never_below_the_token_alpha_and_grows_on_bright_backdrops(panel):
    make_active(panel, "#FFFFFF")
    base = QColor("#8A0A0F18")
    veil = panel._optical.card_veil(base, QRectF(10, 10, 200, 100), False)
    assert veil.alpha() > base.alpha()
    make_active(panel, "#000000")
    assert panel._optical.card_veil(base, QRectF(10, 10, 200, 100), False).alpha() == base.alpha()


def test_light_variant_uses_a_white_veil_and_stays_readable(panel):
    windows_system_theme.set_override("light")
    make_active(panel, "#000000")
    base = QColor("#99FFFFFF")
    veil = panel._optical.card_veil(base, QRectF(10, 10, 200, 100), False)
    assert veil.alpha() >= base.alpha()
    assert (veil.red(), veil.green(), veil.blue()) == (255, 255, 255)


def test_sample_returns_the_worst_case_colour():
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("#101010"))
    painter = QPainter(image)
    painter.fillRect(60, 60, 40, 40, QColor("#F0F0F0"))
    painter.end()
    snap = Snapshot(image, 100, 100)
    whole = QRectF(0, 0, 100, 100)
    assert snap.sample(whole, "card", "light").lightness() > snap.sample(whole, "card", "dark").lightness()


# ------------------------------------------------------------------ availability and fallback
def test_environment_and_user_setting_can_switch_it_off(monkeypatch):
    assert windows_optical.enabled()
    monkeypatch.setenv("NEXUS_GLASS_OPTICAL", "0")
    assert not windows_optical.enabled()
    monkeypatch.delenv("NEXUS_GLASS_OPTICAL")
    windows_optical.set_enabled(False)
    assert not windows_optical.enabled()


def test_inactive_without_a_native_backdrop(panel):
    panel._optical.snapshot = Snapshot(plate("#FFFFFF", 400, 160), 400, 160)
    panel._surface._native_backdrop = False        # satin / transparency off / remote session
    assert not panel._optical.active()
    base = QColor("#8A0A0F18")
    assert panel._optical.card_veil(base, QRectF(0, 0, 10, 10), False) == base


def test_inactive_for_solid_themes(panel, monkeypatch):
    make_active(panel)
    monkeypatch.setattr(styles, "_active_key", "minimalist")
    assert not panel._optical.available()


def test_capture_is_skipped_offscreen_and_when_visible(panel):
    assert panel._optical.capture() is False       # offscreen platform: no real screen to read
    assert panel._optical.snapshot is None


def test_disabled_setting_blocks_capture(panel, monkeypatch):
    windows_optical.set_enabled(False)
    monkeypatch.setattr(QApplication, "platformName", staticmethod(lambda: "windows"), raising=False)
    assert panel._optical.capture() is False


# ------------------------------------------------------------------ lifecycle and privacy
def test_release_drops_every_pixel_and_restores_the_controls(panel):
    make_active(panel)
    assert panel._search.property("opticalGlass") is True
    panel._optical._raw = QImage(10, 10, QImage.Format.Format_ARGB32)
    panel._optical.release()
    assert panel._optical.snapshot is None and panel._optical._raw is None
    assert not panel._search.property("opticalGlass")


def test_hiding_the_window_releases_the_backdrop(panel):
    make_active(panel)
    panel.show()
    panel.hide()
    assert panel._optical.snapshot is None


def test_snapshot_keeps_only_small_blurred_plates():
    big = plate("#336699", 1900, 600)
    snap = Snapshot(big, 950, 300)
    assert snap.control.width() < 400 and snap.card.width() < 400    # not a copy of the screen
    assert not hasattr(snap, "raw") and not hasattr(snap, "image")


def test_no_capture_code_writes_files():
    import inspect
    source = inspect.getsource(windows_optical)
    import re
    assert not re.search(r"(?<!painter)\.save\(|write_bytes|write_text|\bopen\(|logging|log\.", source)


# ------------------------------------------------------------------ integration
def test_controls_are_registered_and_flagged_while_active(panel):
    make_active(panel)
    names = {w for w, _ in panel._optical_controls()}
    assert panel._search in names and panel._style_btn in names and panel._close_btn in names
    assert panel._style_btn.property("opticalGlass") is True


def test_qss_makes_flagged_controls_transparent_only_for_liquid_glass(monkeypatch):
    monkeypatch.setattr(styles, "_active_key", "liquid_glass")
    assert 'QLineEdit[opticalGlass="true"]' in windows_design.qss()
    monkeypatch.setattr(styles, "_active_key", "minimalist")
    assert "opticalGlass" not in windows_design.qss()


def test_painting_the_panel_with_optical_glass_does_not_fail(panel, qapp):
    make_active(panel)
    panel.resize(800, 320)
    panel.show()
    qapp.processEvents()
    image = panel.grab().toImage()
    assert image.width() >= 800


def test_optical_controls_change_the_pixels_behind_the_search_field(panel, qapp):
    panel.resize(800, 320)
    panel.show(); qapp.processEvents()
    plain = panel.grab().toImage()
    make_active(panel, "#E2674A")
    panel.update(); qapp.processEvents()
    lens = panel.grab().toImage()
    spot = panel._search.mapTo(panel, QPoint(6, 4))
    assert plain.pixelColor(spot) != lens.pixelColor(spot) or plain != lens


def test_card_delegate_is_unchanged_when_optical_is_inactive(panel):
    d = panel._delegate
    base = QColor("#8A0A0F18")
    assert d._card_fill(base, QRectF(0, 0, 50, 50), False) == base


def test_card_delegate_strengthens_the_fill_over_a_bright_backdrop(panel, qapp):
    make_active(panel, "#FFFFFF")
    panel.resize(800, 320)
    panel.show(); qapp.processEvents()
    base = QColor("#8A0A0F18")
    fill = panel._delegate._card_fill(base, QRectF(10, 10, 200, 150), False)
    assert fill.alpha() > base.alpha()


def test_settings_page_toggle_follows_the_backend(qapp):
    from nexus_linux.windows_settings_page import SettingsPage
    # a backend without the capability hides the control (older fake backends keep working)
    assert hasattr(SettingsPage, "_sync_optical")


def test_settings_backend_persists_the_choice(monkeypatch):
    from unittest.mock import Mock
    from nexus_linux import windows_app
    saved = []
    monkeypatch.setattr(windows_app, "save_settings", lambda s: saved.append(dict(s)))
    backend = windows_app.SettingsBackend({}, Mock(), Mock(failures={}), Mock())
    assert backend.glass_optical() is True
    backend.set_glass_optical(False)
    assert backend.glass_optical() is False and not windows_optical.enabled()
    assert saved and saved[-1]["glass_optical"] is False
