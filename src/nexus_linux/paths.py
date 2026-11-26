"""XDG-compliant paths. Nexus (macOS) hardcodes ~/Documents/Nexus/data; on Linux
we follow the XDG base directory spec instead, per the migration decision in
MIGRATION_LINUX.md."""

import os
from pathlib import Path


def _xdg(env_var: str, default: str) -> Path:
    value = os.environ.get(env_var)
    return Path(value) if value else Path.home() / default


_profile = os.environ.get("NEXUS_PROFILE_ROOT")
if _profile:
    _root = Path(_profile)
    if not _root.is_absolute():
        raise ValueError("NEXUS_PROFILE_ROOT must be an absolute path")
    DATA_DIR = _root / "data"
    CONFIG_DIR = _root / "config"
    CACHE_DIR = _root / "cache"
    RUNTIME_DIR = _root / "runtime"
elif os.name == "nt":
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

PRODUCTION = os.environ.get("NEXUS_PRODUCTION") == "1"
# A fresh database; legacy/synthetic/history databases are never opened by installation.
if PRODUCTION and not _profile:
    DATA_DIR = DATA_DIR / "production"
    CONFIG_DIR = CONFIG_DIR / "production"
DB_PATH = DATA_DIR / "nexus.sqlite"
LOG_PATH = CACHE_DIR / "nexus.log"
LOCK_PATH = RUNTIME_DIR / "nexus-linux.lock"


def ensure_dirs() -> None:
    if PRODUCTION and not _profile and (DATA_DIR.is_symlink() or DB_PATH.is_symlink()):
        raise RuntimeError("El destino de producción no puede ser un enlace a otra base")
    for d in (DATA_DIR, CONFIG_DIR, CACHE_DIR, RUNTIME_DIR):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
