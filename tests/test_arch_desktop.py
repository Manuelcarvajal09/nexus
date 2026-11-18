"""Compositor operations must affect only the matching Nexus surface."""
import os
import subprocess
from unittest.mock import patch
from PySide6.QtCore import QRect
from nexus_linux import desktop, caelestia_theme

class Window:
    def geometry(self): return QRect(100, 200, 640, 300)
    def isVisible(self): return True
    def windowTitle(self): return 'Nexus Shelf'

def test_desktop_only_targets_our_pid_and_title():
    clients=[{'pid':os.getpid()+1,'title':'Nexus Shelf','address':'0xbad'}, {'pid':os.getpid(),'title':'Nexus Shelf','address':'0xabc'}]
    with patch.dict(os.environ, HYPRLAND_INSTANCE_SIGNATURE='test'), patch.object(desktop, 'query', return_value=clients), patch.object(desktop.QTimer,'singleShot',side_effect=lambda ms, fn: fn()), patch.object(desktop.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'ok')) as run:
        desktop.present(Window())
    lua=run.call_args.args[0][2]
    assert 'address:0xabc' in lua and 'address:0xbad' not in lua
    assert 'action="set"' in lua
    assert 'relative=false' in lua and 'alter_zorder' in lua
    assert 'fullscreen' not in lua

def test_palette_is_parsed_as_data_not_lua(tmp_path):
    source=tmp_path/'colours.lua'
    source.write_text('return {\n surface = "aabbcc",\n injected = os.execute("false"),\n}\n')
    with patch.object(caelestia_theme, 'PALETTE', source):
        assert caelestia_theme.read_palette()=={'surface':'#aabbcc'}

def test_stack_actually_opens_and_hides(qapp, storage, paste_service, monkeypatch):
    from nexus_linux.stack import ClipStack
    from nexus_linux.stack_window import StackWindow
    from PySide6.QtCore import QRect
    stack = ClipStack(storage=storage, paste_service=paste_service)
    stack.add(storage.capture(kind='text', text_content='synthetic stack clip'))
    window = StackWindow(stack)
    monkeypatch.setattr(desktop, 'usable_geometry', lambda screen: QRect(0, 40, 1280, 680))
    monkeypatch.setattr(desktop, 'active_screen', lambda: object())
    monkeypatch.setattr(desktop, 'present', lambda window: None)
    window.show_and_focus()
    assert window.isVisible()
    rect = window._nexus_requested_geometry
    assert rect.left() >= 32 and rect.top() >= 72
    assert rect.right() < 1280-32 and rect.bottom() < 720-32
    assert len(stack._items) == 1
    window.fade_hide()
    assert not window.isVisible()
    window.show_and_focus()
    assert window.isVisible()
    window.close()

def test_reserved_edges_use_logical_monitor_geometry():
    from PySide6.QtCore import QRect
    class Screen:
        def name(self): return 'TEST-SCALED'
        def geometry(self): return QRect(1920, 0, 1280, 720)
        def availableGeometry(self): return self.geometry()
    with patch.dict(os.environ, HYPRLAND_INSTANCE_SIGNATURE='test'), patch.object(desktop, 'query', return_value=[{'name':'TEST-SCALED','reserved':[0,40,0,20]}]):
        assert desktop.usable_geometry(Screen()) == QRect(1920,40,1280,660)
