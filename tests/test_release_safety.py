"""Nexus 1.0 contracts: delivery, isolation, original data and integration."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from nexus_linux import paths, styles
from nexus_linux.stack import ClipStack
from nexus_linux.stack_window import StackWindow
from nexus_linux.settings import load_settings
from nexus_linux.clipboard_monitor import ClipboardMonitor


def test_hide_reopen_capture_activation_is_idempotent(qapp, storage, paste_service):
    stack = ClipStack(storage, paste_service)
    window = StackWindow(stack)
    stack.activate(); stack.activate()
    window.add_capture(storage.capture(kind='text', text_content='A'))
    window.fade_hide()
    assert window.is_collecting()
    window.add_capture(storage.capture(kind='text', text_content='B'))
    assert len(stack.items()) == 2
    window._on_clear()
    assert not stack.active and stack.items() == []
    assert len(storage.recent()) == 2
    # Flush Qt deferred row destruction while the owner and storage are alive.
    from PySide6.QtCore import QCoreApplication, QEvent
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_confirmation_never_removes_changed_head(storage, paste_service):
    stack = ClipStack(storage, paste_service)
    stack.add(storage.capture(kind='text', text_content='A'))
    stack.add(storage.capture(kind='text', text_content='B'))
    stack.prepare_manual_paste()
    stack.toggle_order()
    assert stack.confirm_pending() is None
    assert len(stack.items()) == 2


def test_empty_lifo_preference_survives_restart(storage, paste_service):
    stack = ClipStack(storage, paste_service); stack.toggle_order()
    stack.clear()
    restored = ClipStack(storage, paste_service); restored.load_persisted(arm=False)
    assert restored.reversed_order
    assert not restored.active


def test_actual_file_restart_preserves_fifo_lifo_without_clipboard_write(tmp_path, paste_service):
    from nexus_linux.storage import StorageService
    db = tmp_path/'production.sqlite'
    first = StorageService(db)
    stack = ClipStack(first, paste_service)
    for text in ('A', 'B', 'C'):
        stack.add(first.capture(kind='text', text_content=text))
    stack.toggle_order(); first.close()
    paste_service.written.clear()
    second = StorageService(db)
    stack = ClipStack(second, paste_service)
    assert stack.load_persisted(arm=False) == 3
    assert stack.reversed_order and not stack.active
    assert paste_service.written == []
    assert stack.prepare_manual_paste().text_content == 'C'
    assert stack.confirm_pending().text_content == 'C'
    assert stack.prepare_manual_paste().text_content == 'B'
    second.close()


def test_capture_requires_validation_marker(tmp_path):
    with patch.object(paths, 'CONFIG_DIR', tmp_path):
        (tmp_path/'settings.json').write_text(json.dumps({'capture_enabled': True}))
        assert not load_settings().capture_enabled
        (tmp_path/'capture-validated.json').write_text('{}')
        assert load_settings().capture_enabled
        (tmp_path/'settings.json').write_text(json.dumps({'capture_enabled': False}))
        assert not load_settings().capture_enabled


def test_exact_internal_suppression_never_discards_unrelated_copy(storage):
    monitor = ClipboardMonitor(storage, enabled=False)
    monitor.suppress_next_change(b'internal', 'text/plain')
    assert monitor._excluded(b'internal', [])
    assert not monitor._excluded(b'external', [])
    assert monitor._excluded(b'password', ['x-kde-passwordManagerHint'])
    assert monitor._excluded(b'a' * (monitor._settings.max_capture_bytes+1), [])


def test_exclusion_before_storage(storage):
    monitor = ClipboardMonitor(storage, enabled=False)
    with patch.object(monitor, '_list_types', return_value=['x-kde-passwordManagerHint', 'text/plain']), patch.object(monitor, '_read') as read:
        monitor._capture_current_clipboard(); read.assert_not_called()
    assert storage.recent() == []


def test_palettes_and_live_selection_persist(qapp, tmp_path):
    from nexus_linux.appearance import AppearanceDialog
    from PySide6.QtWidgets import QApplication
    original = styles.active_theme().key
    with patch.object(styles, '_THEME_FILE', tmp_path/'theme.json'):
        def apply(key):
            styles.set_active_theme(key); QApplication.instance().setStyleSheet(styles.qss())
        dialog = AppearanceDialog(apply)
        assert {'auto','light','dark','liquid_glass','storem','tech_neon','minimalist','midnight_teal'} <= {p.key for p in dialog.previews}
        for preview in dialog.previews:
            preview.click()
            assert json.loads((tmp_path/'theme.json').read_text())['theme'] == preview.key
        styles.set_active_theme(original)
        dialog.close()


def test_installer_staging_idempotent_and_uninstall_preserves_data(tmp_path):
    root = Path(__file__).resolve().parents[1]
    prefix = tmp_path/'integration'
    cmd = [sys.executable, str(root/'scripts/integrate.py')]
    runtime = str(Path.home()/'.local/share/nexus-runtime/venv/bin/python')
    subprocess.run(cmd+['install','--runtime',runtime,'--prefix',str(prefix)], check=True)
    manifest = (prefix/'config/nexus/integration/manifest.json').read_bytes()
    subprocess.run(cmd+['install','--runtime',runtime,'--prefix',str(prefix)], check=True)
    assert (prefix/'config/nexus/integration/manifest.json').read_bytes() == manifest
    data = prefix/'share/nexus/production/nexus.sqlite'; data.parent.mkdir(parents=True); data.write_bytes(b'preserve')
    subprocess.run(cmd+['uninstall','--prefix',str(prefix)], check=True)
    assert data.read_bytes() == b'preserve'
    assert not (prefix/'bin/nexus').exists()
    assert 'NEXUS MANAGED' not in (prefix/'config/caelestia/hypr-user.lua').read_text()


def test_binding_conflicts():
    source = Path(__file__).resolve().parents[1]/'scripts/integrate.py'
    spec = importlib.util.spec_from_file_location('integration', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert module.check_binds([{'modmask':5, 'key':'v', 'dispatcher':'exec', 'arg':'other'}])
    assert not module.check_binds([{'modmask':5, 'key':'v'}], alternate=True)


def test_stack_repeated_refreshes_deliveries_and_cancel_on_persistent_surface(qapp, storage, paste_service):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QCoreApplication, QEvent
    stack = ClipStack(storage, paste_service)
    window = StackWindow(stack)
    window.show()
    for cycle in range(25):
        stack.activate()
        for n in range(3):
            window.add_capture(storage.capture(kind='text', text_content=f'{cycle}-{n}'))
            QTest.qWait(1)
        window._prepare_manual(); window._confirm_delivery()
        assert len(stack.items()) == 2
        window.fade_hide()
        assert window.is_collecting()
        window._on_clear()
        QTest.qWait(1)
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_supported_payloads_preserve_bytes_and_decode_file_uri(storage):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage
    monitor = ClipboardMonitor(storage, enabled=False)
    monitor._store_snapshot(['text/plain'], 'text/plain', 'Español 日本語\nB'.encode())
    image = QImage(2, 2, QImage.Format.Format_ARGB32); image.fill(0xff336699)
    buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, 'PNG')
    raw = bytes(buffer.data())
    monitor._store_snapshot(['image/png'], 'image/png', raw)
    monitor._store_snapshot(['text/uri-list'], 'text/uri-list', b'file:///tmp/mi%20archivo.txt\r\n')
    rows = storage.recent()
    assert rows[0].file_path == '/tmp/mi archivo.txt'
    assert rows[1].image_data == raw
    assert rows[2].text_content == 'Español 日本語\nB'


def test_production_paths_are_separate_from_legacy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    import os
    env = dict(os.environ, PYTHONPATH=str(root/'src'), NEXUS_PRODUCTION='1',
               XDG_DATA_HOME=str(tmp_path/'share'), XDG_CONFIG_HOME=str(tmp_path/'config'))
    env.pop('NEXUS_PROFILE_ROOT', None)
    output = subprocess.check_output([sys.executable, '-c',
        'from nexus_linux import paths; print(paths.DB_PATH); print(paths.CONFIG_DIR)'], env=env, text=True)
    assert output.splitlines() == [str(tmp_path/'share/nexus/production/nexus.sqlite'), str(tmp_path/'config/nexus/production')]
    assert not (tmp_path/'share').exists()


def test_installer_refuses_foreign_file_before_any_config_change(tmp_path):
    root = Path(__file__).resolve().parents[1]
    prefix = tmp_path/'integration'
    launcher = prefix/'bin/nexus'; launcher.parent.mkdir(parents=True); launcher.write_text('foreign launcher')
    result = subprocess.run([sys.executable, str(root/'scripts/integrate.py'), 'install', '--prefix',str(prefix)], capture_output=True)
    assert result.returncode == 1
    assert launcher.read_text() == 'foreign launcher'
    assert not (prefix/'config').exists()


def test_session_start_imports_environment_before_restart_and_keeps_ui_hidden(tmp_path):
    from nexus_linux import session
    import os
    with patch.object(sys, 'argv', ['nexus', 'session-start']), patch.dict(os.environ,
            XDG_CONFIG_HOME=str(tmp_path), WAYLAND_DISPLAY='wayland-test', HYPRLAND_INSTANCE_SIGNATURE='test'), \
            patch.object(session.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
        assert session.main() == 0
    commands = [call.args[0] for call in run.call_args_list]
    assert commands[0][:3] == ['systemctl','--user','import-environment']
    assert commands[1] == ['systemctl','--user','restart','nexus.service']
    assert len(commands) == 2  # no request to show a panel


def test_existing_dbus_instance_does_not_restart_on_a_shortcut():
    from nexus_linux import session, control
    with patch.object(sys, 'argv', ['nexus', 'stack']), patch.object(control, 'call', return_value=0), \
            patch.object(session.subprocess, 'run') as run:
        assert session.main() == 0
    run.assert_not_called()
