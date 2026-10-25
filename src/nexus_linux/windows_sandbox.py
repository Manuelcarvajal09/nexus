"""Opt-in isolation for development runs (NEXUS_SANDBOX=1).

A sandboxed Nexus must never touch the user's real data or compete with the
instance they use every day, so when enabled it:

* refuses to start unless every data path lives under the system temp dir and
  outside the real Nexus root recorded by the launcher;
* uses its own single-instance mutex;
* swaps the global hotkeys for ones that are rarely used by other apps;
* starts with clipboard capture paused;
* never installs the low-level Ctrl+V hook unless explicitly allowed.

Without NEXUS_SANDBOX=1 every function here is a no-op, so normal runs are
unchanged.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

ENV_FLAG = "NEXUS_SANDBOX"
ENV_REAL_ROOT = "NEXUS_SANDBOX_REAL_ROOT"
ENV_ALLOW_HOOK = "NEXUS_SANDBOX_ALLOW_HOOK"
MUTEX_NAME = "Local\\NexusWindowsSandbox"
SANDBOX_HOTKEYS = {"panel_hotkey": "Ctrl+Shift+9", "stack_hotkey": "Ctrl+Shift+7"}
COUNTED_TABLES = ("clip_items", "pinboards", "pinboard_items", "stack_state")


class SandboxError(RuntimeError):
    pass


def active() -> bool:
    return os.environ.get(ENV_FLAG) == "1"


def mutex_name(default: str) -> str:
    return MUTEX_NAME if active() else default


def apply_settings(settings: dict) -> dict:
    if not active():
        return settings
    return {**settings, **SANDBOX_HOTKEYS, "paused": True}


def hook_allowed() -> bool:
    return not active() or os.environ.get(ENV_ALLOW_HOOK) == "1"


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def assert_isolated() -> None:
    if not active():
        return
    from . import paths

    real = os.environ.get(ENV_REAL_ROOT)
    if not real:
        raise SandboxError("NEXUS_SANDBOX requiere NEXUS_SANDBOX_REAL_ROOT; usa tools/windows_sandbox_run.py")
    real_root = Path(real).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    for path in (paths.DB_PATH, paths.CONFIG_DIR, paths.CACHE_DIR, paths.RUNTIME_DIR, paths.LOG_PATH):
        resolved = Path(path).resolve()
        if _within(resolved, real_root) or not _within(resolved, temp_root):
            raise SandboxError(f"Ruta fuera del sandbox: {resolved.name}")


def snapshot_database(source: Path, destination: Path) -> dict:
    """Copy a live SQLite database with the backup API (read-only on the source)
    and verify the copy. Returns integrity and row counts only, never content."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise SandboxError("El destino del respaldo ya existe; no se sobrescribe")
    src = sqlite3.connect(f"file:{Path(source).as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(destination))
        try:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {}
            for table in COUNTED_TABLES:
                try:
                    counts[table] = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.OperationalError:
                    counts[table] = None
            source_counts = {}
            for table in COUNTED_TABLES:
                try:
                    source_counts[table] = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.OperationalError:
                    source_counts[table] = None
        finally:
            dst.close()
    finally:
        src.close()
    return {"integrity": integrity, "counts": counts, "source_counts": source_counts}
