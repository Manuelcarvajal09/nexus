"""Automatic dispatch policy, recovery and binding isolation (not live PASS)."""
import json
from unittest.mock import Mock, patch
import pytest
from nexus_linux.stack import ClipStack
from nexus_linux.sequential_stack import SequentialStack
from nexus_linux.stack_hyprland import HyprlandStack, DESCRIPTION


def request(n):
    return 'a'*32+':'+str(n)


class Transport:
    def __init__(self):
        self.valid = True
        self.accepted = True
        self.installed = False
        self.sends = []
    def install(self): self.installed = True
    def remove(self): self.installed = False
    def target_valid(self, target): return self.valid
    def send(self, target):
        self.sends.append(target)
        return self.accepted


def make(storage,paste_service,reversed_order=False):
    stack = ClipStack(storage,paste_service,reversed_order=reversed_order)
    stack.activate()
    for n in range(10):
        stack.add(storage.capture(kind='text',text_content=str(n)))
    transport = Transport()
    controller = SequentialStack(stack,transport)
    assert controller.enable()
    return stack,transport,controller


@pytest.mark.parametrize('reversed_order',[False,True])
def test_ten_requests_deliver_correct_payload_and_consume_once(storage,paste_service,reversed_order):
    stack,transport,controller = make(storage,paste_service,reversed_order)
    order = list(map(str,range(10)))
    if reversed_order: order.reverse()
    for n,text in enumerate(order,1):
        result = controller.dispatch(request(n),'0x123')
        assert result['outcome'] == 'dispatched'
        assert not result['application_receipt']
        assert result['remaining'] == 10-n
        assert paste_service.written[-1].text_content == text
        assert len(paste_service.written) == n  # Never eager-write the next clip.
        assert controller.dispatch(request(n),'0x123')['outcome'] == 'duplicate'
        assert len(stack.items()) == 10-n
    assert not stack.active and not controller.enabled and not transport.installed
    assert len(transport.sends) == 10
    assert len(storage.recent()) == 10  # Pending consumption never deletes history.
    assert storage.load_stack_state()[0] == []


def test_failed_prepare_or_focus_change_does_not_consume(storage,paste_service):
    stack,transport,controller = make(storage,paste_service)
    paste_service.fail_write = True
    assert controller.dispatch(request(1),'0x123')['outcome'] == 'failed'
    assert len(stack.items()) == 10 and transport.sends == []
    paste_service.fail_write = False
    with patch.object(transport,'target_valid',side_effect=[True,False]):
        assert controller.dispatch(request(2),'0x123')['outcome'] == 'failed'
    assert len(stack.items()) == 10 and transport.sends == []
    assert controller.dispatch(request(3),'0x123')['remaining'] == 9


def test_rejected_send_keeps_pending_item(storage,paste_service):
    stack,transport,controller = make(storage,paste_service)
    transport.accepted = False
    assert controller.dispatch(request(1),'0x123')['outcome'] == 'failed'
    assert len(stack.items()) == 10
    transport.accepted = True
    assert controller.dispatch(request(2),'0x123')['remaining'] == 9


def test_ambiguous_send_blocks_automatic_retry_and_survives_restart(storage,paste_service):
    stack,transport,controller = make(storage,paste_service)
    with patch.object(transport,'send',side_effect=TimeoutError):
        assert controller.dispatch(request(1),'0x123')['outcome'] == 'uncertain'
    assert controller.dispatch(request(2),'0x123')['outcome'] == 'blocked'
    restored = ClipStack(storage,paste_service)
    assert restored.load_persisted(arm=False) == 10
    restored.activate()
    restarted = SequentialStack(restored,transport)
    restarted.enable()
    assert restarted.blocked
    assert restarted.dispatch(request(3),'0x123')['outcome'] == 'blocked'
    restarted.retry_pending()
    assert restarted.dispatch(request(4),'0x123')['remaining'] == 9


def test_journal_and_queue_commit_atomically_on_storage_failure(storage,paste_service):
    stack,transport,controller = make(storage,paste_service)
    with patch.object(storage,'save_stack_state',side_effect=__import__('sqlite3').OperationalError('disk error')):
        assert controller.dispatch(request(1),'0x123')['outcome'] == 'uncertain'
    assert len(stack.items()) == len(storage.load_stack_state()[0]) == 10
    assert controller.blocked
    restarted = SequentialStack(stack,transport)
    assert restarted.blocked


@pytest.mark.parametrize('reversed_order',[False,True])
def test_recover_last_dispatched_preserves_history_and_order(file_storage,paste_service,reversed_order):
    from nexus_linux.storage import StorageService
    stack,transport,controller = make(file_storage,paste_service,reversed_order)
    expected = stack.items()[-1 if reversed_order else 0].id
    controller.dispatch(request(1),'0x123')
    second = StorageService(file_storage.db_path,allow_retention=False)
    restored = ClipStack(second,paste_service)
    restored.load_persisted(arm=False)
    recovery = SequentialStack(restored,transport)
    assert recovery.restore_last()
    assert restored.items()[-1 if reversed_order else 0].id == expected
    assert len(second.recent()) == len(restored.items()) == 10
    second.close()


def test_pause_only_removes_binding_not_collection(storage,paste_service):
    stack,transport,controller = make(storage,paste_service)
    controller.pause()
    assert stack.active and len(stack.items()) == 10
    assert not transport.installed
    assert controller.dispatch(request(1),'0x123')['outcome'] == 'inactive'


def test_binding_conflict_is_refused_before_backup_or_mutation(tmp_path):
    from nexus_linux import paths
    backend = HyprlandStack()
    with patch.dict('os.environ',{'HYPRLAND_INSTANCE_SIGNATURE':'test'}), patch.object(paths,'CONFIG_DIR',tmp_path), patch(
        'nexus_linux.stack_hyprland.run',return_value=json.dumps([{'modmask':4,'key':'V','dispatcher':'existing'}])), patch(
        'nexus_linux.stack_hyprland.evaluate') as evaluate:
        with pytest.raises(RuntimeError,match='Conflicto'): backend.install()
    evaluate.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_binding_releases_once_and_removes_own_handle_only(tmp_path):
    from nexus_linux import paths
    backend = HyprlandStack()
    with patch.dict('os.environ',{'HYPRLAND_INSTANCE_SIGNATURE':'test'}), patch.object(paths,'CONFIG_DIR',tmp_path), patch(
        'nexus_linux.stack_hyprland.run',return_value='[]'), patch('nexus_linux.stack_hyprland.evaluate') as evaluate:
        backend.install()
        lua = evaluate.call_args.args[0]
        assert 'release = true' in lua and 'repeating = false' in lua
        assert 'hl.get_active_window()' in lua
        assert 'w.address' in lua and DESCRIPTION in lua
        assert 'io.popen' not in lua
        backend.remove()
        assert ':remove()' in evaluate.call_args.args[0]
        assert 'hl.unbind' not in evaluate.call_args.args[0]
    assert len(list((tmp_path/'stack-binding-backups').glob('*.json'))) == 1


def test_window_hide_cancel_clear_and_pause_are_distinct(storage,paste_service,qapp):
    from nexus_linux.stack_window import StackWindow
    stack,transport,controller = make(storage,paste_service)
    window = StackWindow(stack); window.sequential = controller
    window.show_and_focus(); window.fade_hide()
    assert stack.active and controller.enabled and len(stack.items()) == 10
    window.show_and_focus()
    assert len(stack.items()) == 10
    window._toggle_capture()
    assert not window.is_collecting() and stack.active and controller.enabled
    window._cancel_session()
    assert not stack.active and not controller.enabled and len(stack.items()) == 10
    window._on_clear()
    assert stack.items() == [] and len(storage.recent()) == 10
    window.close(); window.deleteLater()


def test_last_dispatch_updates_window_and_hides_only_when_empty(storage,paste_service,qapp):
    from nexus_linux.stack_window import StackWindow
    stack,transport,controller = make(storage,paste_service)
    window = StackWindow(stack); window.sequential = controller
    def changed():
        window.refresh()
        if not stack.active and not stack.items(): window.fade_hide()
    controller.changed = changed
    window.show_and_focus()
    for n in range(1,11):
        controller.dispatch(request(n),'0x123')
        assert window.isVisible() == (n < 10)
    window.close(); window.deleteLater()


def test_internal_clip_not_recaptured_after_slow_dispatch(storage):
    from nexus_linux.clipboard_monitor import ClipboardMonitor
    monitor = ClipboardMonitor(storage,enabled=False)
    with patch('nexus_linux.clipboard_monitor.time.monotonic',return_value=1):
        monitor.suppress_next_change(b'internal','text/plain')
    with patch('nexus_linux.clipboard_monitor.time.monotonic',return_value=20):
        monitor._store_snapshot(['text/plain'],'text/plain',b'internal')
        assert storage.recent() == []
        monitor._store_snapshot(['text/plain'],'text/plain',b'external')
        assert storage.recent()[0].text_content == 'external'
        monitor._store_snapshot(['text/plain'],'text/plain',b'internal')
        assert len(storage.recent()) == 2  # A later distinct external copy unsets ownership suppression.


@pytest.mark.parametrize('outcome',['dispatched','duplicate','inactive'])
def test_cli_forwards_only_when_session_inactive(outcome):
    import subprocess
    from nexus_linux.stack_hyprland import main
    response = subprocess.CompletedProcess([],0,repr((json.dumps({'outcome':outcome}),)), '')
    with patch('sys.argv',['stack_hyprland','paste',request(1),'0x123']), patch(
        'nexus_linux.stack_hyprland.subprocess.run',return_value=response), patch.object(
        HyprlandStack,'target_valid',return_value=True), patch.object(HyprlandStack,'send') as send:
        assert main() == 0
    assert send.call_count == (1 if outcome == 'inactive' else 0)


def test_cli_ambiguous_response_never_sends_second_paste():
    import subprocess
    from nexus_linux.stack_hyprland import main
    owner = subprocess.CompletedProcess([],0,'(true,)','')
    with patch('sys.argv',['stack_hyprland','paste',request(1),'0x123']), patch(
        'nexus_linux.stack_hyprland.subprocess.run',side_effect=[subprocess.TimeoutExpired('gdbus',20),owner]), patch.object(
        HyprlandStack,'send') as send:
        assert main() == 1
    send.assert_not_called()


def test_dbus_exports_status_and_dispatch_without_loading_another_instance(storage,paste_service,qapp):
    from nexus_linux.dbus_service import NexusDBusService
    stack,transport,controller = make(storage,paste_service)
    service = NexusDBusService(lambda:None,lambda:None,stack_paste=controller.dispatch,stack_status=controller.status)
    assert service.metaObject().indexOfMethod('StackPaste(QString,QString)') >= 0
    assert service.metaObject().indexOfMethod('StackStatus()') >= 0
    assert json.loads(service.StackStatus())['pending_ids'] == [clip.id for clip in stack.items()]
    assert json.loads(service.StackPaste(request(1),'0x123'))['remaining'] == 9
