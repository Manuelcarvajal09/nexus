"""Start-with-Windows: off by default, explicit opt-in, respected by upgrades; never touches the real registry here."""
import json
import sys
from pathlib import Path

from nexus_linux import windows_autostart as autostart
from nexus_linux import windows_sandbox
from nexus_linux.windows_settings import DEFAULTS

_REAL_DEFAULT_REGISTRY = autostart.default_registry   # captured before conftest swaps in a fake for every test

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import rc1_manage as manage  # noqa: E402
from tests.test_rc1_package import FakeRunner, idle, make_package  # noqa: E402


class Reg:
    def __init__(self, **values):
        self.values = dict(values)

    def read(self, name):
        return self.values.get(name)

    def write(self, name, value):
        self.values[name] = value

    def delete(self, name):
        return self.values.pop(name, None) is not None


def quiet(*_a):
    return None


# ---- defaults -------------------------------------------------------------
def test_new_installs_default_to_autostart_off_and_drop_the_legacy_key():
    assert DEFAULTS["autostart"] is False
    assert "launch_at_login" not in DEFAULTS     # never read by any code; old settings files carry it as true


def test_old_settings_with_the_legacy_true_flag_do_not_turn_autostart_on(tmp_path, monkeypatch):
    from nexus_linux import paths
    from nexus_linux.windows_settings import load_settings
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"launch_at_login": True, "retention_days": 30}), encoding="utf-8")
    settings = load_settings()
    assert settings["autostart"] is False
    assert settings["launch_at_login"] is True        # the user's file is not rewritten destructively


# ---- module ---------------------------------------------------------------
def test_command_uses_pythonw_so_no_console_appears(tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    assert autostart.launch_command(tmp_path / "python.exe") == f'"{tmp_path / "pythonw.exe"}" -m nexus.platform.windows.app'


def test_startup_sync_never_creates_an_entry_when_the_choice_is_off():
    registry = Reg()
    assert autostart.sync(False, registry) == "unchanged"
    assert registry.values == {}


def test_startup_sync_never_deletes_anything():
    registry = Reg(Nexus='"C:\\old\\pythonw.exe" -m nexus_linux.windows_app')
    assert autostart.sync(False, registry) == "unchanged"
    assert registry.values                              # only an explicit switch-off removes it


def test_enabled_entry_follows_the_running_interpreter():
    registry = Reg(Nexus='"C:\\old\\pythonw.exe" -m nexus_linux.windows_app')
    new = '"C:\\new\\pythonw.exe" -m nexus_linux.windows_app'
    assert autostart.sync(True, registry, command=new) == "re-pointed"
    assert registry.values["Nexus"] == new
    assert autostart.sync(True, registry, command=new) == "unchanged"


def test_explicit_switch_on_and_off():
    registry = Reg()
    assert autostart.set_enabled(True, registry, command='"x\\pythonw.exe" -m nexus_linux.windows_app') == "created"
    assert autostart.is_enabled(registry)
    assert autostart.set_enabled(False, registry) == "removed"
    assert not autostart.is_enabled(registry)


def test_switching_off_never_removes_another_programs_entry():
    registry = Reg(Nexus='"C:\\Other\\app.exe" --background')
    assert autostart.set_enabled(False, registry) == "unchanged"
    assert registry.values


def test_sandbox_mode_refuses_to_touch_the_registry(monkeypatch):
    import pytest
    monkeypatch.setattr(autostart, "default_registry", _REAL_DEFAULT_REGISTRY)    # the real one, not the test fake
    monkeypatch.setenv(windows_sandbox.ENV_FLAG, "1")
    assert "sandbox" in autostart.unavailable_reason()
    with pytest.raises(autostart.AutostartError):
        autostart.default_registry()
    assert autostart.sync(True) == "skipped"      # startup never fails and never writes in a development sandbox


# ---- backend / settings page ----------------------------------------------
def make_backend():
    from nexus_linux import windows_app
    backend = windows_app.SettingsBackend.__new__(windows_app.SettingsBackend)
    backend.settings = {"autostart": False}
    backend._save = lambda: None
    return backend


def test_backend_saves_the_choice_only_after_the_registry_accepted_it():
    backend, registry = make_backend(), Reg()
    assert backend.set_autostart(True, registry) == ""
    assert backend.autostart_enabled() and registry.values
    assert backend.set_autostart(False, registry) == ""
    assert not backend.autostart_enabled() and not registry.values


def test_a_registry_failure_leaves_the_setting_unchanged_and_explains():
    class Broken(Reg):
        def write(self, name, value):
            raise PermissionError("denied")
    backend = make_backend()
    assert backend.set_autostart(True, Broken())
    assert not backend.autostart_enabled()


def test_settings_page_has_an_off_by_default_switch(qapp, tmp_path, monkeypatch, paste_service, fake_autostart_registry):
    from nexus_linux import paths, styles, windows_app
    from nexus_linux.storage import StorageService
    from nexus_linux.windows_settings import load_settings, save_settings
    from nexus_linux.windows_shortcuts import ShortcutStore
    from nexus_linux.windows_timeline import WindowsTimelineWindow
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    monkeypatch.setattr(windows_app, "register_hotkey", lambda *a, **k: True)
    monkeypatch.setattr(windows_app, "unregister_hotkey", lambda *a, **k: True)
    storage = StorageService(tmp_path / "s.sqlite")
    window = WindowsTimelineWindow(storage, paste_service)
    settings = load_settings()
    store = ShortcutStore(settings)
    hotkeys = windows_app.GlobalHotkeys(1, store, lambda: save_settings(settings))
    hotkeys.register_all()
    window.configure_settings(windows_app.SettingsBackend(settings, store, hotkeys, window, []))
    page = window._settings_page
    page.refresh()
    assert not page.autostart_toggle.isChecked() and "desactivado" in page.autostart_toggle.text()
    assert fake_autostart_registry.values == {}              # merely showing the page wrote nothing
    page.autostart_toggle.click()
    assert page.autostart_toggle.isChecked() and "activado" in page.autostart_toggle.text()
    assert "nexus.platform.windows.app" in fake_autostart_registry.values["Nexus"] and settings["autostart"] is True
    page.autostart_toggle.click()
    assert fake_autostart_registry.values == {} and settings["autostart"] is False
    window.close()
    window.deleteLater()
    storage.close()


# ---- install / upgrade tool -----------------------------------------------
def pythonw(root, version):
    return root / "versions" / version / "venv" / "Scripts" / "pythonw.exe"


def test_upgrade_keeps_an_enabled_choice_and_points_it_at_the_new_version(tmp_path):
    root, registry = tmp_path / "root", Reg()
    manage.install(make_package(tmp_path / "a", "0.2.0rc1"), root, run=FakeRunner("0.2.0rc1"), running=idle, log=quiet, registry=registry)
    assert registry.values == {}                                  # a new install never creates the entry
    registry.values["Nexus"] = f'"{pythonw(root, "0.2.0rc1")}" -m nexus_linux.windows_app'   # the user turned it on
    manage.install(make_package(tmp_path / "b", "0.2.0rc2"), root, run=FakeRunner("0.2.0rc2"), running=idle, log=quiet, registry=registry)
    assert "0.2.0rc2" in registry.values["Nexus"]
    manage.rollback(root, running=idle, log=quiet, registry=registry)
    assert "0.2.0rc1" in registry.values["Nexus"]


def test_upgrade_leaves_foreign_entries_alone_and_keeps_an_off_choice_off(tmp_path):
    root = tmp_path / "root"
    stranger = '"C:\\Elsewhere\\pythonw.exe" -m nexus_linux.windows_app'     # e.g. the user's personal install
    registry = Reg(Nexus=stranger)
    manage.install(make_package(tmp_path / "a", "0.2.0rc1"), root, run=FakeRunner("0.2.0rc1"), running=idle, log=quiet, registry=registry)
    manage.install(make_package(tmp_path / "b", "0.2.0rc2"), root, run=FakeRunner("0.2.0rc2"), running=idle, log=quiet, registry=registry)
    assert registry.values == {"Nexus": stranger}
    off = Reg()
    manage.install(make_package(tmp_path / "c", "0.2.0rc3"), root, run=FakeRunner("0.2.0rc3"), running=idle, log=quiet, registry=off)
    assert off.values == {}                                       # OFF stays OFF through upgrades


def test_uninstall_removes_only_an_entry_that_points_into_the_removed_root(tmp_path):
    root = tmp_path / "root"
    manage.install(make_package(tmp_path / "a"), root, run=FakeRunner("0.2.0rc1"), running=idle, log=quiet, registry=Reg())
    mine = Reg(Nexus=f'"{pythonw(root, "0.2.0rc1")}" -m nexus_linux.windows_app')
    manage.uninstall(root, running=idle, log=quiet, registry=mine)
    assert mine.values == {}
    other = tmp_path / "root2"
    manage.install(make_package(tmp_path / "b"), other, run=FakeRunner("0.2.0rc1"), running=idle, log=quiet, registry=Reg())
    stranger = Reg(Nexus='"C:\\Elsewhere\\pythonw.exe" -m nexus_linux.windows_app')
    manage.uninstall(other, running=idle, log=quiet, registry=stranger)
    assert stranger.values                                        # someone else's entry survives


def test_entries_written_before_the_reorganisation_are_still_recognised():
    assert autostart.is_ours(r'"C:\old\pythonw.exe" -m nexus_linux.windows_app')
    assert autostart.is_ours(r'"C:\new\pythonw.exe" -m nexus.platform.windows.app')
