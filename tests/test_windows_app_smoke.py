"""Exercise the Windows composition root in a fresh, fully sandboxed process."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize("qt_platform", ["offscreen", "windows"])
def test_windows_startup_shutdown_composes_inline_panel_and_inactive_stack(tmp_path, qt_platform):
    script = r'''
import json, sys
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication
from nexus_linux import windows_app as app, styles
from nexus_linux.storage import StorageService
from nexus_linux.windows_settings import DEFAULTS

root = Path(sys.argv[1])
events = {"registered": [], "unregistered": [], "watcher_starts": 0, "monitor_stops": 0, "released": []}

class Application(QApplication):
    def __init__(self, args):
        super().__init__(args)
        QTimer.singleShot(100, self.quit)

class Monitor(QObject):
    captured = Signal(object)
    captured_versioned = Signal(object, object)
    def __init__(self, *args): super().__init__()
    def stop(self): events["monitor_stops"] += 1
    def suppress_next_change(self): pass

class Watcher(QObject):
    paste_detected = Signal(object)
    def __init__(self, parent=None): super().__init__(parent)
    def start(self): events["watcher_starts"] += 1
    def stop(self): pass

class Tray:
    def __init__(self, *args): pass
    def setToolTip(self, *args): pass
    def setContextMenu(self, *args): pass
    def show(self): pass
    def hide(self): pass

class Backdrop:
    def __init__(self, window, **kwargs): self.window, self.request = window, kwargs["request"]
    class Timer:
        def stop(self): pass
    _timer = Timer()
    def schedule(self): pass
    def set_request(self, request): self.request = request
    def native_settings_changed(self, message): pass
    def close(self): pass

def register(hwnd, identifier, key, modifiers=None):
    events["registered"].append(identifier)
    return True

storage = StorageService(root / "synthetic.sqlite")
storage.create_pinboard("Mercado Libre", "#007AFF")
storage.capture(kind="text", text_content="synthetic smoke test")
with patch.object(app, "QApplication", Application), \
     patch.object(app, "WindowsClipboardMonitor", Monitor), \
     patch.object(app, "WindowsPasteWatcher", Watcher), \
     patch.object(app, "QSystemTrayIcon", Tray), \
     patch.object(app, "BackdropController", Backdrop), \
     patch.object(app, "StorageService", lambda: storage), \
     patch.object(app, "load_settings", lambda: dict(DEFAULTS)), \
     patch.object(app, "save_settings", lambda settings: None), \
     patch.object(app.paths, "LOG_PATH", root / "synthetic.log"), \
     patch.object(styles, "_THEME_FILE", root / "synthetic-theme.json"), \
     patch.object(app, "acquire_single_instance_mutex", lambda: 123), \
     patch.object(app, "release_handle", events["released"].append), \
     patch.object(app, "register_hotkey", register), \
     patch.object(app, "unregister_hotkey", lambda hwnd, identifier: events["unregistered"].append(identifier)):
    assert app.main() == 0
assert events["registered"] == [1, 2]
assert events["unregistered"] == [2, 1]
assert events["watcher_starts"] == 0
assert events["monitor_stops"] == 1
assert events["released"] == [123]
try:
    storage.recent()
except Exception as error:
    assert type(error).__name__ == "ProgrammingError"
else:
    raise AssertionError("Storage was not closed")
print(json.dumps(events))
'''
    env = {**os.environ, "QT_QPA_PLATFORM": qt_platform, "NEXUS_OPEN_ON_START": "0"}
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(tmp_path)],
        cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '"watcher_starts": 0' in completed.stdout
