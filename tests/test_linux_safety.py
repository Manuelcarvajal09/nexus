"""No Qt/display/network required: retention and paste failure regressions."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from nexus_linux import paths
from nexus_linux import paste_service as paste_module
from nexus_linux.settings import load_settings
from nexus_linux.storage import StorageService


class RetentionSafety(unittest.TestCase):
    def test_old_history_survives_default_purge_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'copy.sqlite'
            service = StorageService(db, allow_retention=False)
            item = service.capture(kind='text', text_content='synthetic ancient clip')
            service._conn.execute('UPDATE clip_items SET updated_at = ?', (time.time() - 400 * 86400,))
            service._conn.commit()
            self.assertEqual(service.purge_older_than(30), 0)
            service.close()
            service = StorageService(db, allow_retention=False)
            self.assertEqual(service._conn.execute('SELECT COUNT(*) FROM clip_items').fetchone()[0], 1)
            self.assertEqual(service.get(item.id).text_content, 'synthetic ancient clip')
            self.assertEqual(service._conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            service.close()

    def test_legacy_days_does_not_enable_deletion(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(paths, 'CONFIG_DIR', Path(tmp)):
            (Path(tmp) / 'settings.json').write_text(json.dumps({'retention_days': 30, 'launch_at_login': True}))
            self.assertFalse(load_settings().allow_retention)
            self.assertFalse(load_settings().evdev_watcher)
            self.assertFalse(load_settings().network_previews)

    def test_invalid_boolean_cannot_enable_retention(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(paths, 'CONFIG_DIR', Path(tmp)):
            (Path(tmp) / 'settings.json').write_text(json.dumps({'allow_retention': 'false'}))
            self.assertFalse(load_settings().allow_retention)

    def test_explicit_opt_in_still_supports_retention(self):
        service = StorageService(Path(':memory:'), allow_retention=True)
        service.capture(kind='text', text_content='synthetic old')
        service._conn.execute('UPDATE clip_items SET updated_at = 0')
        service._conn.commit()
        self.assertEqual(service.purge_older_than(30), 1)
        service.close()


class PasteSafety(unittest.TestCase):
    def test_failed_clipboard_write_never_synthesizes(self):
        service = paste_module.PasteService()
        with patch.object(service, 'write_to_clipboard', return_value=False), patch.object(service, 'synthetic_paste') as send:
            self.assertFalse(service.paste(object()))
            send.assert_not_called()

    def test_wl_copy_failure_returns_false(self):
        service = StorageService(Path(':memory:'), allow_retention=False)
        item = service.capture(kind='text', text_content='synthetic')
        with patch.object(paste_module, 'WL_COPY_BIN', '/usr/bin/wl-copy'), patch.object(paste_module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            self.assertFalse(paste_module.PasteService().write_to_clipboard(item))
        service.close()

    def test_hyprland_never_pastes_without_exact_target(self):
        with patch.dict(os.environ, {'HYPRLAND_INSTANCE_SIGNATURE': 'synthetic'}), patch.object(paste_module.subprocess, 'run') as run:
            self.assertFalse(paste_module.PasteService().synthetic_paste())
            run.assert_not_called()

    def test_closed_target_never_receives_a_shortcut(self):
        service = paste_module.PasteService()
        service._target_address = '0xabc'
        with patch.object(paste_module, 'HYPRCTL_BIN', '/usr/bin/hyprctl'), patch.object(paste_module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'[]', b'')) as run:
            self.assertFalse(service._hyprland_paste())
            self.assertEqual(run.call_count, 1)

    def test_valid_target_uses_lua_dispatch_not_shell(self):
        service = paste_module.PasteService()
        service._target_address = '0xabc'
        replies = [subprocess.CompletedProcess([], 0, b'[{"address":"0xabc"}]', b''), subprocess.CompletedProcess([], 0, b'ok', b'')]
        with patch.object(paste_module, 'HYPRCTL_BIN', '/usr/bin/hyprctl'), patch.object(paste_module.subprocess, 'run', side_effect=replies) as run:
            self.assertTrue(service._hyprland_paste())
            self.assertEqual(run.call_args.args[0][:2], ['/usr/bin/hyprctl', 'eval'])
            self.assertIn('send_shortcut', run.call_args.args[0][2])
            self.assertNotIn('shell', run.call_args.kwargs)
