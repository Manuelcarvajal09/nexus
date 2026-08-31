import json

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
