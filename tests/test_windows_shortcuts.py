"""Shortcut model: defaults, validation, conflicts, persistence. Pure logic, no native calls."""
import json

import pytest

from nexus_linux import paths, windows_settings
from nexus_linux.windows_settings import DEFAULTS, load_settings, save_settings
from nexus_linux.windows_shortcuts import (
    GLOBAL, LOCAL, MOD_ALT, MOD_CONTROL, MOD_SHIFT, SPECS, ShortcutStore, expand_quick_paste, parse_global,
)


@pytest.fixture
def store(qapp):
    return ShortcutStore(dict(DEFAULTS))


def spec(identifier):
    return next(s for s in SPECS if s.id == identifier)


# -- the catalogue --------------------------------------------------------

def test_catalogue_lists_every_existing_shortcut_with_scope_and_purpose():
    ids = {s.id for s in SPECS}
    assert {"panel", "stack", "search", "rename", "quick_paste", "board_prev", "board_next",
            "paste", "paste_plain", "close", "focus_toggle", "stack_remove", "stack_paste"} <= ids
    assert {s.id for s in SPECS if s.scope == GLOBAL and s.editable} == {"panel", "stack"}
    for s in SPECS:
        assert s.label and s.description and s.default and s.scope in (GLOBAL, LOCAL)
        assert s.editable or s.reason  # fixed keys explain why they are fixed


def test_defaults_match_the_current_behavior(store):
    assert store.get("panel") == "Ctrl+Shift+V" and store.get("stack") == "Ctrl+Shift+C"
    assert store.get("search") == "Ctrl+F" and store.get("rename") == "F2"
    assert store.get("quick_paste") == "Ctrl+1"
    assert (store.get("board_prev"), store.get("board_next")) == ("Ctrl+Left", "Ctrl+Right")
    assert all(store.is_default(s.id) for s in SPECS)


def test_quick_paste_expands_to_nine_keys():
    assert expand_quick_paste("Ctrl+1") == [f"Ctrl+{n}" for n in range(1, 10)]
    assert expand_quick_paste("Alt+Shift+1")[8] == "Alt+Shift+9"


# -- global parsing -------------------------------------------------------

@pytest.mark.parametrize("text,modifiers,vk", [
    ("Ctrl+Shift+V", MOD_CONTROL | MOD_SHIFT, ord("V")),
    ("ctrl+alt+9", MOD_CONTROL | MOD_ALT, ord("9")),
    ("Ctrl+Shift+F9", MOD_CONTROL | MOD_SHIFT, 0x78),
    ("Alt+Space", MOD_ALT, 0x20),
    ("Ctrl+Alt+Left", MOD_CONTROL | MOD_ALT, 0x25),
    ("Ctrl+Shift+;", MOD_CONTROL | MOD_SHIFT, 0xBA),
    ("Ctrl+Shift+Ñ".replace("Ñ", "/"), MOD_CONTROL | MOD_SHIFT, 0xBF),
])
def test_parse_global_maps_modifiers_and_virtual_keys(qapp, text, modifiers, vk):
    assert parse_global(text) == (modifiers, vk)


@pytest.mark.parametrize("text", ["", "V", "Shift+V", "Ctrl+Meta+V", "Ctrl", "Ctrl+K, Ctrl+C", "Ctrl+Shift+Ñ"])
def test_parse_global_rejects_unusable_combinations(qapp, text):
    with pytest.raises(ValueError):
        parse_global(text)


# -- validation -----------------------------------------------------------

@pytest.mark.parametrize("identifier,text", [
    ("panel", "Ctrl+Alt+H"), ("stack", "ctrl+shift+k"), ("search", "Ctrl+K"), ("rename", "F3"),
    ("quick_paste", "Alt+1"), ("board_prev", "Ctrl+Up"),
])
def test_valid_changes_are_normalized_and_persisted_in_settings(qapp, identifier, text):
    settings = dict(DEFAULTS)
    store = ShortcutStore(settings)
    result = store.set(identifier, text)
    assert result.ok, result.message
    assert store.get(identifier) == result.value and not store.is_default(identifier)
    assert ShortcutStore(settings).get(identifier) == result.value  # a new store sees it (restart)


def test_normalization_is_canonical(store):
    assert store.set("panel", "ctrl+shift+h").value == "Ctrl+Shift+H"


@pytest.mark.parametrize("identifier,text,fragment", [
    ("panel", "Shift+H", "Ctrl o Alt"),
    ("panel", "Ctrl+Meta+H", "Windows"),
    ("panel", "Alt+F4", "reservad"),
    ("panel", "Ctrl+Shift+Escape", "reservad"),
    ("panel", "Ctrl+Shift+C", "Activar Stack"),     # the other global
    ("panel", "Ctrl+F", "Buscar"),                  # a local one: the global would shadow it
    ("search", "Ctrl+Shift+V", "Mostrar u ocultar"),  # and the other way around
    ("search", "Ctrl+3", "Pegado rápido"),          # inside the quick-paste range
    ("search", "Return", "reservad"),
    ("search", "Escape", "reservad"),
    ("search", "Ctrl+V", "reservad"),
    ("search", "Ctrl+A", "reservad"),
    ("search", "A", "modificador"),                 # a bare letter would break type-to-search
    ("search", "Ctrl+Meta+F", "Windows"),
    ("search", "Ctrl+K, Ctrl+C", "una sola"),
    ("rename", "Ctrl+F", "Buscar"),
    ("quick_paste", "Ctrl+2", "1"),
    ("quick_paste", "1", "modificador"),
    ("board_next", "Ctrl+Left", "Pinboard anterior"),
])
def test_invalid_changes_are_refused_with_a_clear_reason_and_change_nothing(qapp, identifier, text, fragment):
    settings = dict(DEFAULTS)
    store = ShortcutStore(settings)
    before = (store.get(identifier), json.dumps(settings, sort_keys=True))
    result = store.set(identifier, text)
    assert not result.ok and fragment.lower() in result.message.lower(), result.message
    assert (store.get(identifier), json.dumps(settings, sort_keys=True)) == before


def test_fixed_shortcuts_cannot_be_changed(store):
    for identifier in ("paste", "close", "focus_toggle", "stack_remove", "stack_paste"):
        result = store.set(identifier, "Ctrl+Alt+Q")
        assert not result.ok and "no se puede" in result.message.lower()


def test_assigning_the_same_value_again_is_not_a_conflict_with_itself(store):
    assert store.set("search", "Ctrl+F").ok
    assert store.set("panel", "Ctrl+Shift+V").ok


def test_swapping_through_a_free_key_is_possible(store):
    assert store.set("search", "Ctrl+K").ok
    assert store.set("rename", "Ctrl+F").ok  # Ctrl+F was released by search
    assert (store.get("search"), store.get("rename")) == ("Ctrl+K", "Ctrl+F")


# -- reset and persistence ------------------------------------------------

def test_reset_one_and_reset_all(qapp):
    settings = dict(DEFAULTS)
    store = ShortcutStore(settings)
    store.set("panel", "Ctrl+Alt+H"); store.set("search", "Ctrl+K"); store.set("quick_paste", "Alt+1")
    store.reset("search")
    assert store.get("search") == "Ctrl+F" and store.get("panel") == "Ctrl+Alt+H"
    store.reset_all()
    assert all(store.is_default(s.id) for s in SPECS)
    assert settings["panel_hotkey"] == "Ctrl+Shift+V" and not settings.get("shortcuts")


def test_only_overrides_are_stored_for_local_shortcuts(qapp):
    settings = dict(DEFAULTS)
    store = ShortcutStore(settings)
    store.set("search", "Ctrl+K")
    assert settings["shortcuts"] == {"search": "Ctrl+K"}
    store.set("search", "Ctrl+F")  # back to the default
    assert settings["shortcuts"] == {}


def test_sanitize_repairs_hand_edited_settings(qapp):
    settings = {**DEFAULTS, "panel_hotkey": "garbage", "stack_hotkey": "Ctrl+Shift+V",
                "shortcuts": {"search": "Ctrl+Shift+V", "rename": "nonsense", "unknown": "Ctrl+Q", "board_prev": "Ctrl+K"}}
    store = ShortcutStore(settings)
    notices = store.sanitize()
    assert store.get("panel") == "Ctrl+Shift+V"
    assert store.get("stack") == "Ctrl+Shift+C"          # duplicate of panel: back to default
    assert store.get("search") == "Ctrl+F"               # collided with a global: back to default
    assert store.get("rename") == "F2"
    assert store.get("board_prev") == "Ctrl+K"           # valid override survives
    assert "unknown" not in settings["shortcuts"]
    assert len(notices) >= 3


def test_sandbox_defaults_never_fall_back_to_the_real_hotkeys(qapp):
    settings = {**DEFAULTS, "panel_hotkey": "Ctrl+Shift+9", "stack_hotkey": "Ctrl+Shift+7"}
    store = ShortcutStore(settings, default_overrides={"panel": "Ctrl+Shift+9", "stack": "Ctrl+Shift+7"})
    store.set("panel", "Ctrl+Alt+H")
    store.reset("panel")
    assert store.get("panel") == "Ctrl+Shift+9"
    store.reset_all()
    assert (store.get("panel"), store.get("stack")) == ("Ctrl+Shift+9", "Ctrl+Shift+7")


# -- settings file --------------------------------------------------------

@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ensure_dirs", lambda: (tmp_path.mkdir(exist_ok=True)))
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    return tmp_path


def test_save_then_load_round_trips_including_shortcuts(config, qapp):
    settings = load_settings()
    store = ShortcutStore(settings)
    store.set("panel", "Ctrl+Alt+H"); store.set("quick_paste", "Alt+1")
    save_settings(settings)
    reloaded = load_settings()
    assert reloaded["panel_hotkey"] == "Ctrl+Alt+H" and reloaded["shortcuts"] == {"quick_paste": "Alt+1"}
    assert ShortcutStore(reloaded).get("quick_paste") == "Alt+1"


def test_save_is_atomic_and_keeps_a_backup_of_the_previous_file(config):
    settings = load_settings()
    save_settings(settings)
    settings["paused"] = True
    save_settings(settings)
    assert json.loads((config / "settings.json").read_text(encoding="utf-8"))["paused"] is True
    assert json.loads((config / "settings.json.bak").read_text(encoding="utf-8"))["paused"] is False
    assert not list(config.glob("*.tmp"))


def test_a_corrupt_settings_file_is_preserved_not_overwritten(config):
    (config / "settings.json").write_text("{ this is not json", encoding="utf-8")
    settings = load_settings()
    assert settings["panel_hotkey"] == "Ctrl+Shift+V"
    kept = list(config.glob("settings.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "{ this is not json"
    assert json.loads((config / "settings.json").read_text(encoding="utf-8"))["panel_hotkey"] == "Ctrl+Shift+V"


def test_non_object_json_is_treated_as_corrupt(config):
    (config / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert load_settings()["retention_days"] == 30
    assert list(config.glob("settings.json.corrupt-*"))


def test_load_still_adds_missing_keys_and_keeps_user_values(config):
    (config / "settings.json").write_text(json.dumps({"panel_hotkey": "Ctrl+Shift+Q", "paused": True}), encoding="utf-8")
    loaded = load_settings()
    assert loaded["panel_hotkey"] == "Ctrl+Shift+Q" and loaded["paused"] is True and loaded["retention_days"] == 30
