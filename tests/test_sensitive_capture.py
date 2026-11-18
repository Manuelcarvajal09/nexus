"""Sensitive offers and selection races must fail closed before persistence."""
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from nexus_linux.clipboard_monitor import ClipboardMonitor, _Reader, _ReaderSignals
from nexus_linux.clipboard_offer import write_offer, read_offer, MAX_BYTES


class ForbiddenRead:
    def read(self, *args):
        raise AssertionError('A sensitive/unknown offer must not be read')


@pytest.mark.parametrize('state', ['sensitive', 'nil', 'clear', 'unknown', 'unexpected'])
def test_rejected_state_never_reads_or_exports_content(state):
    output = BytesIO()
    write_offer(state, 'text/plain', ForbiddenRead(), output)
    header, raw = output.getvalue().split(b'\n', 1)
    assert json.loads(header) == {'state':state, 'mime':'text/plain', 'size':0}
    assert raw == b''


def test_payload_is_bounded_before_export():
    output = BytesIO()
    write_offer('data', 'image/png', BytesIO(b'x'*(MAX_BYTES+1)), output)
    header, raw = output.getvalue().split(b'\n', 1)
    assert json.loads(header)['state'] == 'oversize'
    assert raw == b''


def test_public_types_then_sensitive_offer_never_reaches_sqlite(storage, qapp):
    # Reproduces the old split-connection race: MIME list was public, but
    # clipboard ownership changed before reading. The reading connection now
    # authorizes its OWN offer, even if it contains malicious raw bytes.
    monitor = ClipboardMonitor(storage, enabled=False)
    monitor._stopped = False
    monitor._last_selection_state = 'data'
    signals = _ReaderSignals()
    signals.ready.connect(monitor._read_finished)
    with patch.object(ClipboardMonitor, '_list_types', return_value=['text/plain']), patch(
        'nexus_linux.clipboard_monitor.read_offer', return_value=('sensitive','text/plain',b'synthetic-secret')) as offer:
        _Reader(signals, monitor._selection_generation).run()
    offer.assert_called_once()
    assert storage._conn.execute('SELECT COUNT(*) FROM clip_items').fetchone()[0] == 0
    monitor.stop()


def test_password_hint_rejected_before_transfer(storage):
    monitor = ClipboardMonitor(storage, enabled=False)
    with patch.object(ClipboardMonitor, '_list_types', return_value=['text/plain','x-kde-passwordManagerHint']), patch(
        'nexus_linux.clipboard_monitor.read_offer') as offer:
        monitor._capture_current_clipboard()
    offer.assert_not_called()
    assert storage.recent() == []


def test_sensitive_notification_invalidates_pending_reader(storage, qapp):
    monitor = ClipboardMonitor(storage, enabled=False)
    monitor._stopped = False
    monitor._reading = True
    monitor._read_pending = True
    old_generation = monitor._selection_generation
    monitor._process = Mock()
    monitor._process.readAllStandardOutput.return_value = b'sensi'
    monitor._on_watch_tick()
    monitor._process.readAllStandardOutput.return_value = b'tive\n'
    monitor._on_watch_tick()
    assert monitor._last_selection_state == 'sensitive'
    assert not monitor._read_pending
    with patch.object(monitor, '_queue_read') as queue:
        monitor._read_finished((old_generation, (['text/plain'],'text/plain',b'synthetic-secret')))
    queue.assert_not_called()
    assert storage.recent() == []
    monitor._process = None
    monitor.stop()


def test_new_public_selection_does_not_accept_old_reader(storage, qapp):
    monitor = ClipboardMonitor(storage, enabled=False)
    monitor._stopped = False
    monitor._last_selection_state = 'data'
    monitor._selection_generation = 2
    monitor._read_finished((1, (['text/plain'],'text/plain',b'old')))
    assert storage.recent() == []
    monitor._read_finished((2, (['text/plain'],'text/plain',b'new')))
    assert storage.recent()[0].text_content == 'new'
    monitor.stop()


@pytest.mark.parametrize('mime, canonical, raw', [
    ('text/plain;charset=utf-8', 'text/plain', '日本語'.encode()),
    ('image/png', 'image/png', b'\x89PNG\r\n\x00'),
    ('text/uri-list', 'text/uri-list', b'file:///tmp/test\r\n'),
])
def test_exact_mime_and_bytes_are_preserved(mime, canonical, raw):
    with patch.object(ClipboardMonitor, '_list_types', return_value=[mime]), patch(
        'nexus_linux.clipboard_monitor.read_offer', return_value=('data', mime, raw)) as offer:
        assert ClipboardMonitor._snapshot() == ([mime], canonical, raw)
    assert offer.call_args.args[1] == mime


@pytest.mark.parametrize('state', ['sensitive','data'])
def test_real_helper_process_frame_without_wayland(tmp_path, state):
    # Real process + pipes + helper; fake transport only, not a Wayland PASS.
    fake = tmp_path/'wl-paste'
    fake.write_text(f'''#!{sys.executable}
from io import BytesIO
import sys
from nexus_linux.clipboard_offer import write_offer
write_offer({state!r}, sys.argv[sys.argv.index('--type')+1], BytesIO(b'public-test'), sys.stdout.buffer)
''')
    fake.chmod(0o700)
    with patch.dict(os.environ, {'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}):
        assert read_offer(str(fake), 'text/plain') == (state, 'text/plain', b'public-test' if state == 'data' else None)


def test_invalid_validation_marker_cannot_authorize_production(tmp_path):
    from nexus_linux.production_capture import inspect_destination
    config = tmp_path/'config'; config.mkdir()
    (config/'capture-validated.json').write_text('{"tests":[{}]}')
    # Malformed marker is rejected, even with an empty destination.
    from nexus_linux.production_capture import REQUIRED_TESTS
    assert 'sensitive-offer-bound' in REQUIRED_TESTS
    # The unrelated marker grants no authorization.
    assert not inspect_destination(config, tmp_path/'data.sqlite')['previously_validated']


@pytest.mark.parametrize('state', ['sensitive', 'data', None])
def test_helper_executable_obeys_actual_environment(state):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    env.pop('CLIPBOARD_STATE', None)
    if state is not None:
        env['CLIPBOARD_STATE'] = state
    result = subprocess.run([sys.executable,'-m','nexus_linux.clipboard_offer','text/plain'],
                            env=env, input=b'synthetic-secret', capture_output=True, check=True, timeout=2)
    header, raw = result.stdout.split(b'\n',1)
    assert json.loads(header)['state'] == (state or 'unknown')
    assert raw == (b'synthetic-secret' if state == 'data' else b'')


def test_validator_refuses_preimported_production_paths(tmp_path):
    from nexus_linux.capture_validation import isolated_database
    cached = SimpleNamespace(DB_PATH=tmp_path/'production/nexus.sqlite', CONFIG_DIR=tmp_path/'production/config')
    with pytest.raises(RuntimeError, match='no se abre ninguna base'):
        isolated_database(cached, tmp_path/'test')
    isolated = SimpleNamespace(DB_PATH=tmp_path/'test/data/nexus.sqlite', CONFIG_DIR=tmp_path/'test/config')
    assert isolated_database(isolated, tmp_path/'test') == isolated.DB_PATH
