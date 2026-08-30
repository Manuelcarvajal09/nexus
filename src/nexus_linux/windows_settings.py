from __future__ import annotations
import json
from . import paths

DEFAULTS = {"panel_hotkey": "Ctrl+Shift+V", "stack_hotkey": "Ctrl+Shift+C", "launch_at_login": True}

def load_settings():
    paths.ensure_dirs(); path = paths.CONFIG_DIR / "settings.json"
    try: data = {**DEFAULTS, **json.loads(path.read_text(encoding="utf-8"))}
    except (FileNotFoundError, json.JSONDecodeError):
        data = DEFAULTS.copy(); path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data

def vk_for(shortcut: str) -> int:
    normalized = shortcut.upper().replace(" ", "")
    if not normalized.startswith("CTRL+SHIFT+") or len(normalized) != len("CTRL+SHIFT+") + 1:
        raise ValueError("Solo se admiten atajos Ctrl+Shift+<tecla>")
    return ord(normalized[-1])
