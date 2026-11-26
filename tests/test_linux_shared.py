"""Unification phase 2: the shared panel composed with the Linux services."""
import json

import pytest
from PySide6.QtCore import QEvent

from nexus_linux import linux_shared, paths


class FakeMonitor:
    def __init__(self):
        self.configured = []

    def configure(self, enabled):
        self.configured.append(enabled)


@pytest.fixture
def shared(qapp, storage, paste_service, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    (tmp_path / "config").mkdir()
    themes = []
    from nexus_linux.preview_service import PreviewService
    monitor, previews = FakeMonitor(), PreviewService(storage, allow_network=False)
    timeline, backend = linux_shared.build_shared_panel(storage, paste_service, previews, monitor, themes.append)
    yield timeline, backend, monitor, previews, tmp_path
    timeline.close()
    timeline.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_shared_panel_has_the_settings_page_without_windows_only_options(shared):
    timeline, backend, *_ = shared
    page = timeline._settings_page
    assert page is not None and not hasattr(backend, "glass_optical")
    page.refresh()
    assert page.optical_toggle.isHidden()          # DWM optical glass is Windows-only
    assert not page.autostart_toggle.isHidden()    # XDG autostart instead of the registry


def test_global_shortcuts_are_left_to_the_desktop(shared):
    _, backend, *_ = shared
    result = backend.change_shortcut("panel", "Ctrl+Alt+V")
    assert not result.ok and "escritorio" in result.message


def test_autostart_writes_and_removes_an_xdg_entry(shared):
    _, backend, *_, tmp_path = shared
    assert backend.set_autostart(True) == "" and backend.autostart_enabled()
    entry = (tmp_path / "xdg" / "autostart" / "nexus.desktop").read_text(encoding="utf-8")
    assert "Exec=" in entry and "nexus.platform.linux.app" in entry and "Type=Application" in entry
    assert backend.set_autostart(False) == "" and not backend.autostart_enabled()


def test_capture_and_previews_use_the_linux_preferences(shared):
    _, backend, monitor, previews, tmp_path = shared
    backend.set_capture_paused(False)
    stored = json.loads((tmp_path / "config" / "settings.json").read_text())
    assert stored["capture_enabled"] is True and monitor.configured == [True]
    backend.set_link_previews(True)
    assert previews.network_enabled and previews._allow_network
    assert backend.link_previews()


def test_ui_choice(monkeypatch):
    monkeypatch.setenv("NEXUS_UI", "shared")
    assert linux_shared.wants_shared_ui({})
    monkeypatch.setenv("NEXUS_UI", "classic")
    assert not linux_shared.wants_shared_ui({"ui": "shared"})
    monkeypatch.delenv("NEXUS_UI")
    assert linux_shared.wants_shared_ui({"ui": "shared"}) and not linux_shared.wants_shared_ui({})


def test_old_module_names_are_the_same_modules_and_runnable():
    """Launchers, autostart entries, Hyprland binds and systemd units written by
    earlier versions use nexus_linux.*: they must keep working after the move."""
    import importlib, importlib.util
    assert importlib.import_module("nexus_linux.windows_design") is importlib.import_module("nexus.ui.design")
    assert importlib.import_module("nexus_linux.storage").StorageService is importlib.import_module("nexus.core.storage").StorageService
    spec = importlib.util.find_spec("nexus_linux.app")
    # `python -m nexus_linux.app` copies the origin into sys.argv[0] (None crashed QApplication)
    assert spec.origin and spec.origin.replace("\\", "/").endswith("nexus/platform/linux/app.py")
    assert spec.loader.get_code("nexus_linux.app").co_filename == spec.origin
