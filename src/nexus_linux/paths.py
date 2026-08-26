"""XDG-compliant paths. Nexus (macOS) hardcodes ~/Documents/Nexus/data; on Linux
we follow the XDG base directory spec instead, per the migration decision in
MIGRATION_LINUX.md."""

import os
from pathlib import Path


def _xdg(env_var: str, default: str) -> Path:
    value = os.environ.get(env_var)
    return Path(value) if value else Path.home() / default


DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "nexus"
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "nexus"
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "nexus"

DB_PATH = DATA_DIR / "nexus.sqlite"
LOG_PATH = CACHE_DIR / "nexus.log"


def ensure_dirs() -> None:
    for d in (DATA_DIR, CONFIG_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
