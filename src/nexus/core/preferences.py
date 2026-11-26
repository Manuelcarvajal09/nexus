from __future__ import annotations
import json, os, shutil, time
from nexus.core.i18n import plural, tr
from nexus.core import paths

DEFAULTS = {
    "panel_hotkey": "Ctrl+Shift+V", "stack_hotkey": "Ctrl+Shift+C",
    "autostart": False, "retention_days": 30, "paused": False,
    "hide_on_focus_loss": False, "bottom_gap": 24,
    "excluded_apps": ["1Password.exe", "Bitwarden.exe", "KeePass.exe", "KeePassXC.exe", "LastPass.exe"],
}

def save_settings(settings: dict) -> None:
    """Atomic write (temp file + replace); the previous file is kept as .bak."""
    paths.ensure_dirs(); path = paths.CONFIG_DIR / "settings.json"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
    os.replace(temporary, path)

def load_settings():
    paths.ensure_dirs(); path = paths.CONFIG_DIR / "settings.json"
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(stored, dict): raise ValueError("settings.json is not an object")
        data = {**DEFAULTS, **stored}
        if data != stored: save_settings(data)
    except FileNotFoundError:
        data = DEFAULTS.copy(); save_settings(data)
    except (json.JSONDecodeError, ValueError):
        # Never overwrite what the user had: keep the unreadable file aside.
        path.replace(path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%dT%H%M%S')}"))
        data = DEFAULTS.copy(); save_settings(data)
    return data

def vk_for(shortcut: str) -> int:
    normalized = shortcut.upper().replace(" ", "")
    if not normalized.startswith("CTRL+SHIFT+") or len(normalized) != len("CTRL+SHIFT+") + 1:
        raise ValueError(tr("Solo se admiten atajos Ctrl+Shift+<tecla>"))
    return ord(normalized[-1])
