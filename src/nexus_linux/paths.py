"""XDG-compliant paths. Nexus (macOS) hardcodes ~/Documents/Nexus/data; on Linux
we follow the XDG base directory spec instead, per the migration decision in
MIGRATION_LINUX.md."""

import os
from pathlib import Path


def _xdg(env_var: str, default: str) -> Path:
    value = os.environ.get(env_var)
    return Path(value) if value else Path.home() / default


if os.name == "nt":
    _appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Nexus"
    DATA_DIR = _appdata / "data"
    CONFIG_DIR = _appdata / "config"
    CACHE_DIR = _appdata / "cache"
    RUNTIME_DIR = _appdata / "runtime"
else:
    DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "nexus"
    CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "nexus"
    CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "nexus"
    RUNTIME_DIR = _xdg("XDG_RUNTIME_DIR", ".cache") / "nexus"

DB_PATH = DATA_DIR / "nexus.sqlite"
LOG_PATH = CACHE_DIR / "nexus.log"
LOCK_PATH = RUNTIME_DIR / "nexus-linux.lock"


def ensure_dirs() -> None:
    for d in (DATA_DIR, CONFIG_DIR, CACHE_DIR, RUNTIME_DIR):
        d.mkdir(parents=True, exist_ok=True)
