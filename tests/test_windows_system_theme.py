"""System light/dark preference: native registry source, live updates, override for tests only."""
import pytest
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest

from nexus_linux import windows_system_theme as st


@pytest.fixture(autouse=True)
def clean():
    st.set_override(None)
    yield
    st.set_override(None)


class FakeKey:
    def __init__(self, value, kind=1):
        self.value, self.kind = value, kind


def reader(value, kind=1, error=None):
    class Registry:
        HKEY_CURRENT_USER, KEY_READ, REG_DWORD = "HKCU", 1, 4
        def OpenKey(self, *args):
            if error: raise error
            return self
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def QueryValueEx(self, key, name):
            assert name == "AppsUseLightTheme"
            return value, (self.REG_DWORD if kind else 3)
    return Registry()


@pytest.mark.parametrize("value,expected", [(1, True), (0, False)])
def test_reads_the_native_apps_theme_value(value, expected):
    assert st.system_prefers_light(registry=reader(value)) is expected


@pytest.mark.parametrize("registry", [
    reader(7), reader(1, kind=0), reader(0, error=FileNotFoundError()), reader(0, error=OSError()),
])
def test_unknown_or_missing_values_fall_back_to_dark(registry):
    assert st.system_prefers_light(registry=registry) is False


def test_the_real_registry_answers_with_a_boolean():
    assert isinstance(st.system_prefers_light(), bool)   # read-only; the value itself is the user's


def test_override_beats_the_registry_and_clears():
    st.set_override("light")
    assert st.system_prefers_light(registry=reader(0)) is True
    st.set_override("dark")
    assert st.system_prefers_light(registry=reader(1)) is False
    st.set_override(None)
    assert st.system_prefers_light(registry=reader(1)) is True
    with pytest.raises(ValueError):
        st.set_override("sepia")


def test_watcher_emits_only_real_changes_and_debounces(qapp):
    values = ["dark"]
    st.set_override("dark")
    seen = []
    watcher = st.SystemThemeWatcher(debounce_ms=20)
    watcher.changed.connect(seen.append)
    watcher.notify()                      # nothing changed
    QTest.qWait(80)
    assert seen == []
    st.set_override("light")
    for _ in range(5):                    # a burst of WM_SETTINGCHANGE messages
        watcher.notify()
    QTest.qWait(120)
    assert seen == [True]                 # one change, once
    watcher.notify(); QTest.qWait(80)
    assert seen == [True]
    st.set_override("dark"); watcher.notify(); QTest.qWait(80)
    assert seen == [True, False]
    watcher.close()


def test_native_message_filter_recognises_only_immersive_color_set():
    assert st.is_theme_message(0x001A, "ImmersiveColorSet") is True
    assert st.is_theme_message(0x001A, "WindowsThemeElement") is True
    assert st.is_theme_message(0x001A, "intl") is False
    assert st.is_theme_message(0x0010, "ImmersiveColorSet") is False
    assert st.is_theme_message(0x001A, None) is False


def test_the_registry_is_read_once_until_windows_announces_a_change(monkeypatch):
    """palette() runs on every card paint: the preference is cached and re-read on notification."""
    import winreg
    reads = []
    real_query = winreg.QueryValueEx
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a: (reads.append(1), real_query(*a))[1])
    st.invalidate_cache()
    first = st.system_prefers_light()
    for _ in range(50):
        assert st.system_prefers_light() is first
    assert len(reads) == 1
    st.invalidate_cache()
    st.system_prefers_light()
    assert len(reads) == 2
    st.invalidate_cache()   # leave no state behind


def test_an_explicit_registry_object_never_touches_the_cache():
    st.invalidate_cache()
    assert st.system_prefers_light(registry=reader(1)) is True
    assert st.system_prefers_light(registry=reader(0)) is False
