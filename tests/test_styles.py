import json

from PySide6.QtGui import QColor

from nexus_linux import styles


def test_theme_selection_persists_and_is_reloaded(tmp_path, monkeypatch):
    theme_file = tmp_path / "theme.json"
    monkeypatch.setattr(styles, "_THEME_FILE", theme_file)
    monkeypatch.setattr(styles, "_active_key", styles.DEFAULT_THEME)

    styles.set_active_theme("minimalist")

    assert styles.active_theme().key == "minimalist"
    assert json.loads(theme_file.read_text())["theme"] == "minimalist"
    assert styles._load_theme_key() == "minimalist"


def test_all_exposed_theme_choices_have_complete_presets():
    choices = styles.theme_choices()
    assert len(choices) >= 4
    for key, label in choices:
        theme = styles.PRESETS[key]
        assert label == theme.display_name
        assert theme.bg and theme.card_bg and theme.accent and theme.text_primary


def test_liquid_glass_is_exposed_and_has_optical_composition():
    keys = [key for key, _label in styles.theme_choices()]
    assert keys[0] == "liquid_glass"
    assert styles.PRESETS["liquid_glass"].liquid_glass is True

    previous = styles._active_key
    try:
        styles._active_key = "liquid_glass"
        sheet = styles.qss()
        assert "qlineargradient" in sheet
        assert "rgba(255, 255, 255, 0.12)" in sheet
        assert "border-radius: 32px" in sheet
    finally:
        styles._active_key = previous


def test_qcolor_parses_qss_rgba_without_turning_black():
    color = styles.qcolor("rgba(255, 255, 255, 0.075)")
    assert color.isValid()
    assert color.red() == color.green() == color.blue() == 255
    assert color.alpha() == 19
