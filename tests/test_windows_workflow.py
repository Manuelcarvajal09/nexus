from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from nexus_linux.stack import ClipStack
from nexus_linux.stack_window import StackWindow
from nexus_linux.timeline_window import TimelineWindow


class CentralPaste:
    def __init__(self): self.calls=[]; self.written=[]
    def paste_item(self,item,overlay,completion=None,plain_text=False):
        self.calls.append((item.id,plain_text)); return True
    def write_to_clipboard(self,item): self.written.append(item); return True
    def synthetic_paste(self): return True


def test_timeline_double_click_dispatches_exactly_once(qapp,storage):
    paste=CentralPaste(); item=storage.capture(kind="text",text_content="double")
    window=TimelineWindow(storage,paste); window.refresh()
    index=window._model.index(0,0)
    window._list.doubleClicked.emit(index)
    assert paste.calls == [(item.id,False)]


def test_enter_and_double_click_share_central_paste(qapp,storage):
    paste=CentralPaste(); item=storage.capture(kind="text",text_content="enter")
    window=TimelineWindow(storage,paste); window.refresh(); index=window._model.index(0,0)
    window._on_activated(index)
    assert paste.calls == [(item.id,False)]


def test_timeline_exposes_pinboard_management_and_theme_controls(qapp,storage):
    window=TimelineWindow(storage,CentralPaste())
    requested=[]; themes=[]
    window.manage_pinboards_requested.connect(lambda: requested.append(True))
    window.theme_requested.connect(themes.append)
    manage_button=next(
        window._pinboard_row.itemAt(i).widget()
        for i in range(window._pinboard_row.count())
        if window._pinboard_row.itemAt(i).widget() and window._pinboard_row.itemAt(i).widget().text()=="+ Pinboard"
    )
    manage_button.click()
    first_action=next(iter(window._theme_actions))
    first_action.trigger()
    assert requested == [True]
    assert themes == [window._theme_actions[first_action]]


def test_close_button_hides_without_quitting(qapp,storage):
    window=TimelineWindow(storage,CentralPaste()); window.show(); assert window.isVisible()
    QTest.mouseClick(window._close_btn,Qt.MouseButton.LeftButton)
    assert not window.isVisible()


def test_stack_collecting_survives_window_hide(qapp,storage):
    paste=CentralPaste(); stack=ClipStack(storage,paste); window=StackWindow(stack)
    window.start_collecting(); window.show(); window.fade_hide()
    assert window.is_collecting() is True


def test_stack_defers_fifo_rearm_outside_clipboard_callback(qapp,storage,monkeypatch):
    paste=CentralPaste(); stack=ClipStack(storage,paste); window=StackWindow(stack)
    callbacks=[]
    monkeypatch.setattr("nexus_linux.stack_window.QTimer.singleShot",lambda delay,callback: callbacks.append(callback))
    first=storage.capture(kind="text",text_content="FIRST")
    second=storage.capture(kind="text",text_content="SECOND")
    window.add_capture(first)
    assert paste.written == []
    window.add_capture(second)
    assert paste.written == []
    assert len(callbacks) == 1
    callbacks.pop()()
    assert paste.written[-1].id == first.id


def test_stack_is_draggable_and_stays_on_top(qapp,storage):
    window=StackWindow(ClipStack(storage,CentralPaste()))
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window._header_widget in window._drag_sources
    assert window._order_btn not in window._drag_sources
    assert window._pause_btn not in window._drag_sources


def test_stack_consumes_selected_only_after_success(qapp,storage):
    paste=CentralPaste(); stack=ClipStack(storage,paste)
    first=storage.capture(kind="text",text_content="A"); second=storage.capture(kind="text",text_content="B")
    stack.add(first); stack.add(second); window=StackWindow(stack)
    window._finish_item_paste(first.id,False)
    assert [item.id for item in stack.items()] == [first.id,second.id]
    window._finish_item_paste(first.id,True)
    assert [item.id for item in stack.items()] == [second.id]


def test_fresh_stack_first_item_survives_failed_native_paste(qapp,storage):
    paste=CentralPaste(); stack=ClipStack(storage,paste)
    first=storage.capture(kind="text",text_content="NEXUS_STACK_1")
    second=storage.capture(kind="text",text_content="NEXUS_STACK_2")
    third=storage.capture(kind="text",text_content="NEXUS_STACK_3")
    for item in (first,second,third): stack.add(item)
    window=StackWindow(stack)
    window._finish_item_paste(first.id,False)
    assert [item.id for item in stack.items()] == [first.id,second.id,third.id]
    window._finish_item_paste(first.id,True)
    assert [item.id for item in stack.items()] == [second.id,third.id]
