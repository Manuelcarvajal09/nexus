"""Linux preferences with explicit opt-in retention and external integrations."""
from dataclasses import dataclass
import json
import os

from nexus.core import paths


@dataclass(frozen=True)
class Settings:
    allow_retention: bool = False
    retention_days: int = 30
    network_previews: bool = False
    evdev_watcher: bool = False
    capture_enabled: bool = False
    hide_on_focus_loss: bool = False
    excluded_patterns: tuple[str, ...] = ()
    max_capture_bytes: int = 16 * 1024 * 1024


def capture_is_validated() -> bool:
    marker = paths.CONFIG_DIR / "capture-validated.json"
    if not marker.is_file() or marker.is_symlink():
        return False
    if not paths.PRODUCTION or os.environ.get("NEXUS_PROFILE_ROOT"):
        return True  # isolated test profiles never use the production database
    try:
        from nexus.platform.linux.production_capture import REQUIRED_TESTS
        value = json.loads(marker.read_text())
        return (isinstance(value, dict) and value.get("database") == str(paths.DB_PATH)
                and REQUIRED_TESTS <= set(value.get("tests", []))
                and not paths.DATA_DIR.is_symlink() and not paths.DB_PATH.is_symlink()
                and (not paths.DB_PATH.exists() or paths.DB_PATH.stat().st_nlink == 1))
    except (OSError, ValueError, TypeError):
        return False


def load_settings() -> Settings:
    try:
        value = json.loads((paths.CONFIG_DIR / "settings.json").read_text())
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    # Legacy Windows retention_days is NOT consent to automatic deletion.
    days = value.get("retention_days", 30)
    if type(days) is not int or days < 1:
        days = 30
    return Settings(
        excluded_patterns=tuple(p for p in value.get("excluded_patterns", []) if isinstance(p, str)) if isinstance(value.get("excluded_patterns", []), list) else (),
        hide_on_focus_loss=value.get("hide_on_focus_loss") is True,
        allow_retention=value.get("allow_retention") is True,
        retention_days=days,
        network_previews=value.get("network_previews") is True,
        evdev_watcher=value.get("evdev_watcher") is True,
        capture_enabled=value.get("capture_enabled", False) is True
        and os.environ.get("NEXUS_CAPTURE_DISABLED") != "1"
        and capture_is_validated(),
    )


def save_capture_preferences(enabled: bool, exclusions: list[str]) -> None:
    """Atomic preferences update; enabling alone cannot bypass the live gate."""
    target = paths.CONFIG_DIR / "settings.json"
    try:
        value = json.loads(target.read_text())
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    value.update(capture_enabled=enabled, excluded_patterns=exclusions, allow_retention=False)
    paths.ensure_dirs()
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2)); temp.chmod(0o600); temp.replace(target)
