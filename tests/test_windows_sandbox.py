"""The sandbox hooks must isolate a dev run and change nothing otherwise."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from nexus_linux import paths, windows_platform, windows_sandbox
from nexus_linux.windows_keyboard import WindowsPasteWatcher
from nexus_linux.windows_settings import DEFAULTS, vk_for
from nexus_linux.storage import StorageService


def test_without_flag_everything_is_unchanged(monkeypatch):
    monkeypatch.delenv(windows_sandbox.ENV_FLAG, raising=False)
    assert not windows_sandbox.active()
    assert windows_sandbox.mutex_name("Local\\NexusWindowsErwin") == "Local\\NexusWindowsErwin"
    assert windows_sandbox.apply_settings(DEFAULTS) is DEFAULTS
    assert windows_sandbox.hook_allowed()
    windows_sandbox.assert_isolated()  # no-op even with real paths


def test_flag_changes_mutex_hotkeys_and_pauses_capture(monkeypatch):
    monkeypatch.setenv(windows_sandbox.ENV_FLAG, "1")
    assert windows_sandbox.mutex_name("Local\\NexusWindowsErwin") != "Local\\NexusWindowsErwin"
    settings = windows_sandbox.apply_settings(DEFAULTS)
    assert settings["paused"] is True
    assert settings["panel_hotkey"] != DEFAULTS["panel_hotkey"]
    assert settings["stack_hotkey"] != DEFAULTS["stack_hotkey"]
    assert vk_for(settings["panel_hotkey"]) != vk_for(settings["stack_hotkey"])
    assert DEFAULTS["paused"] is False  # defaults themselves are never mutated


def test_sandbox_never_installs_the_ctrl_v_hook(qapp, monkeypatch):
    monkeypatch.setenv(windows_sandbox.ENV_FLAG, "1")
    watcher = WindowsPasteWatcher()
    watcher.start(enabled=True)
    assert watcher._thread is None
    monkeypatch.setenv(windows_sandbox.ENV_ALLOW_HOOK, "1")
    assert windows_sandbox.hook_allowed()


def test_default_mutex_name_follows_the_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(windows_platform.kernel32, "CreateMutexW", lambda a, b, name: seen.append(name) or 0)
    monkeypatch.delenv(windows_sandbox.ENV_FLAG, raising=False)
    windows_platform.acquire_single_instance_mutex()
    monkeypatch.setenv(windows_sandbox.ENV_FLAG, "1")
    windows_platform.acquire_single_instance_mutex()
    assert seen == ["Local\\NexusWindowsErwin", windows_sandbox.MUTEX_NAME]


def _point_paths_at(monkeypatch, root: Path):
    monkeypatch.setattr(paths, "DATA_DIR", root / "data")
    monkeypatch.setattr(paths, "CONFIG_DIR", root / "config")
    monkeypatch.setattr(paths, "CACHE_DIR", root / "cache")
    monkeypatch.setattr(paths, "RUNTIME_DIR", root / "runtime")
    monkeypatch.setattr(paths, "DB_PATH", root / "data" / "nexus.sqlite")
    monkeypatch.setattr(paths, "LOG_PATH", root / "cache" / "nexus.log")


def test_guard_accepts_temp_paths_and_rejects_real_root(monkeypatch, tmp_path):
    real_root = tmp_path / "real" / "Nexus"
    sandbox_root = Path(tempfile.gettempdir()) / "nexus-sandbox-test" / "Nexus"
    monkeypatch.setenv(windows_sandbox.ENV_FLAG, "1")
    monkeypatch.setenv(windows_sandbox.ENV_REAL_ROOT, str(real_root))
    _point_paths_at(monkeypatch, sandbox_root)
    windows_sandbox.assert_isolated()
    _point_paths_at(monkeypatch, real_root)
    with pytest.raises(windows_sandbox.SandboxError):
        windows_sandbox.assert_isolated()


def test_guard_rejects_paths_outside_temp_and_missing_real_root(monkeypatch, tmp_path):
    monkeypatch.setenv(windows_sandbox.ENV_FLAG, "1")
    monkeypatch.delenv(windows_sandbox.ENV_REAL_ROOT, raising=False)
    with pytest.raises(windows_sandbox.SandboxError):
        windows_sandbox.assert_isolated()
    monkeypatch.setenv(windows_sandbox.ENV_REAL_ROOT, str(tmp_path / "real"))
    _point_paths_at(monkeypatch, Path.home() / "definitely-not-temp" / "Nexus")
    with pytest.raises(windows_sandbox.SandboxError):
        windows_sandbox.assert_isolated()


def test_snapshot_copies_verifies_and_never_overwrites(tmp_path):
    source = tmp_path / "source.sqlite"
    storage = StorageService(source)
    storage.capture(kind="text", text_content="private synthetic")
    board = storage.create_pinboard("Prompts", "#FF2D55")
    storage.close()
    report = windows_sandbox.snapshot_database(source, tmp_path / "copy" / "nexus.sqlite")
    assert report["integrity"] == "ok"
    assert report["counts"] == report["source_counts"]
    assert report["counts"]["clip_items"] == 1 and report["counts"]["pinboards"] == 1
    assert "private synthetic" not in str(report)
    with pytest.raises(windows_sandbox.SandboxError):
        windows_sandbox.snapshot_database(source, tmp_path / "copy" / "nexus.sqlite")
    # the source stays byte-for-byte usable and unchanged in content
    with sqlite3.connect(str(source)) as check:
        assert check.execute("SELECT COUNT(*) FROM clip_items").fetchone()[0] == 1
