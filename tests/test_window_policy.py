from unittest.mock import patch
import json
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import QEvent,QPointF
from nexus_linux import paths,product_ui,desktop
from nexus_linux.stack import ClipStack
from nexus_linux.stack_window import StackWindow
from nexus_linux.timeline_window import TimelineWindow
from nexus_linux.settings import load_settings

@pytest.mark.parametrize('kind',['history','stack'])
def test_focus_loss_defaults_stay_open_and_opt_in_hides(qapp,storage,paste_service,tmp_path,kind):
    with patch.object(paths,'CONFIG_DIR',tmp_path):
        window=TimelineWindow(storage,paste_service) if kind=='history' else StackWindow(ClipStack(storage,paste_service))
        window.show()
        with patch.object(window,'isActiveWindow',return_value=False):
            window._dismiss_if_still_inactive()
            assert window.isVisible()
            (tmp_path/'settings.json').write_text(json.dumps({'hide_on_focus_loss':True}))
            window._dismiss_if_still_inactive()
            assert window.isVisible() if kind == "stack" else not window.isVisible()
        window.close()

def test_native_grip_uses_edges_not_global_coordinates(qapp):
    from PySide6.QtWidgets import QWidget
    window=QWidget();product_ui.attach(window);window.show();qapp.processEvents()
    corner=window._resize_edges.grips[4]
    class Native:
        def startSystemResize(self,edges): self.edges=edges;return True
    native=Native()
    event=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(6,6),QPointF(9999,9999),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    with patch.object(window,'windowHandle',return_value=native):corner.mousePressEvent(event)
    assert native.edges == Qt.Edge.LeftEdge|Qt.Edge.TopEdge
    assert event.isAccepted()
    assert len(window._resize_edges.grips)==8
    window.close()

def test_focus_policy_rejects_string_true(tmp_path):
    with patch.object(paths,'CONFIG_DIR',tmp_path):
        (tmp_path/'settings.json').write_text('{"hide_on_focus_loss":"true"}')
        assert load_settings().hide_on_focus_loss is False
