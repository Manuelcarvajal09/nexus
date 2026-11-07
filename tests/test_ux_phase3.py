"""Phase 3 UX: undo delete, pinboard counts, empty states, Configuración without navigation."""
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from nexus_linux import paths, styles
from nexus_linux.storage import StorageService
from nexus_linux.windows_timeline import WindowsTimelineWindow


# ---- storage ---------------------------------------------------------------

def test_restore_item_brings_back_content_title_time_and_existing_boards(storage):
    board = storage.create_pinboard("A")
    gone = storage.create_pinboard("B")
    item = storage.capture(kind="text", text_content="synthetic restore me", source_app="Notepad")
    storage.set_title(item.id, "Mi título")
    storage.add_to_pinboard(item.id, board.id)
    storage.add_to_pinboard(item.id, gone.id)
    snapshot = storage.get(item.id)
    storage.delete(item.id)
    storage.delete_pinboard(gone.id)  # a board removed in the meantime must not break the undo
    restored = storage.restore_item(snapshot)
    assert restored.id == snapshot.id and restored.text_content == snapshot.text_content
    assert restored.title == "Mi título" and restored.created_at == snapshot.created_at
    assert restored.pinboard_ids == [board.id]
    assert [i.id for i in storage.recent(query="synthetic restore")] == [snapshot.id]  # searchable again


def test_restore_item_never_duplicates_content_copied_again_meanwhile(storage):
    item = storage.capture(kind="text", text_content="synthetic twin")
    snapshot = storage.get(item.id)
    storage.delete(item.id)
    again = storage.capture(kind="text", text_content="synthetic twin")
    restored = storage.restore_item(snapshot)
    assert restored.id == again.id
    assert storage.pinboard_counts()[None] == 1


def test_pinboard_counts_include_history_total(storage):
    a, b = storage.create_pinboard("A"), storage.create_pinboard("B")
    one = storage.capture(kind="text", text_content="one")
    two = storage.capture(kind="text", text_content="two")
    storage.add_to_pinboard(one.id, a.id); storage.add_to_pinboard(two.id, a.id); storage.add_to_pinboard(two.id, b.id)
    counts = storage.pinboard_counts()
    assert counts[a.id] == 2 and counts[b.id] == 1 and counts[None] == 2


# ---- window ----------------------------------------------------------------

@pytest.fixture
def window(qapp, tmp_path, monkeypatch, paste_service):
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    storage = StorageService(tmp_path / "phase3.sqlite")
    win = WindowsTimelineWindow(storage, paste_service)
    yield win, storage
    win._search_debounce.stop(); win._dismiss_timer.stop(); win._feedback_timer.stop()
    win.close(); win.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    storage.close()


def test_deleting_a_clip_offers_undo_and_ctrl_z_restores_it(window, qapp):
    win, storage = window
    item = storage.capture(kind="text", text_content="synthetic undo target")
    win.show(); win.refresh()
    win._delete_item(storage.get(item.id))
    assert storage.get(item.id) is None
    assert win._feedback_label.text() == "Eliminado" and not win._undo_btn.isHidden()
    win.undo_delete()
    assert storage.get(item.id) is not None and win._feedback_label.text() == "Restaurado"
    assert win._model.row_for_id(item.id) >= 0 and win._undo_btn.isHidden()


def test_undo_button_and_expiry(window, qapp):
    win, storage = window
    item = storage.capture(kind="text", text_content="synthetic expiry")
    win.show(); win.refresh()
    win._delete_item(storage.get(item.id))
    win._clear_notice()  # what the timer does after the grace period
    win.undo_delete()
    assert storage.get(item.id) is None  # nothing pending: Ctrl+Z is a no-op


def test_manager_sidebar_shows_how_many_clips_each_pinboard_holds(window):
    win, storage = window
    board = storage.create_pinboard("Compras")
    storage.add_to_pinboard(storage.capture(kind="text", text_content="x").id, board.id)
    win.show_pinboard_manager()
    texts = [win._sidebar._list.item(i).text() for i in range(win._sidebar._list.count())]
    assert any("Compras" in t and t.rstrip().endswith("· 1") for t in texts)


def test_manager_without_boards_explains_and_offers_creation(window):
    win, _ = window
    win.show_pinboard_manager()
    assert win._right_heading.text() == "Tus pinboards"
    labels = [w.text() for w in win._editor_scroll.widget().findChildren(QPushButton)]
    assert "+ Nuevo pinboard" in labels


def test_empty_states_have_an_icon_and_a_next_step(window):
    win, storage = window
    win.show(); win.refresh()
    assert "historial empieza" in win._empty_label.text() and win._empty_action.isHidden()
    win._search.setText("synthetic nothing"); win.refresh()
    assert not win._empty_action.isHidden() and win._empty_action.text() == "Borrar búsqueda"
    win._empty_action.click()
    assert win._search.text() == ""
    board = storage.create_pinboard("Vacío")
    win._select_pinboard(board.id); win.refresh()
    assert win._empty_action.text() == "Ir al historial"
    win._empty_action.click()
    assert win._current_pinboard_id is None
    assert not win._empty_icon.pixmap().isNull()



def test_supr_deletes_the_selected_card_and_keeps_a_neighbour_selected(window):
    win, storage = window
    items = [storage.capture(kind="text", text_content=f"synthetic card {n}") for n in range(3)]
    win.show(); win.refresh()
    win._list.setCurrentIndex(win._model.index(1, 0))
    victim = win._model.item_at(1)
    win.delete_current()
    assert storage.get(victim.id) is None and win._model.rowCount() == 2
    assert win._list.currentIndex().isValid()
    win.undo_delete()
    assert storage.get(victim.id) is not None


def test_supr_in_the_search_box_edits_text_and_never_deletes(window, monkeypatch):
    win, storage = window
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: win._search))  # offscreen has no real focus
    item = storage.capture(kind="text", text_content="synthetic keep me")
    win.show(); win.refresh(); win._search.setFocus()
    win._search.setText("abc")
    win.delete_current()
    assert storage.get(item.id) is not None


def test_item_menu_names_the_keys_of_frequent_actions(window):
    win, storage = window
    from PySide6.QtWidgets import QMenu
    seen = {}
    import nexus_linux.timeline_window as tw
    class Probe(QMenu):
        def exec(self, *a):
            seen["texts"] = [x.text() for x in self.actions()]
            return None
    tw.QMenu = Probe
    try:
        storage.capture(kind="text", text_content="synthetic menu")
        win.show(); win.refresh()
        win._show_item_menu(win._list.visualRect(win._model.index(0, 0)).center())
    finally:
        tw.QMenu = QMenu
    assert "Pegar\tEnter" in seen["texts"] and "Eliminar\tSupr" in seen["texts"]


def test_keyboard_pinboard_navigation_does_not_animate_but_clicks_may(window):
    win, storage = window
    board = storage.create_pinboard("Uno")
    win.show(); win.refresh()
    calls = []
    win._motion.transition = lambda widget: calls.append(widget)
    win._cycle_pinboard(1)
    assert win._current_pinboard_id == board.id and calls == []
    win._select_pinboard(None)
    assert len(calls) == 1


def test_dismissal_returns_focus_to_the_app_it_was_opened_over(window, monkeypatch):
    import os
    import nexus_linux.windows_timeline as wt
    win, _ = window
    calls = []
    monkeypatch.setattr(wt, "restore_focus_target", lambda target, pid: calls.append((target, pid)))
    own_hwnd = int(win.internalWinId())
    # Windows picked some foreign window: go back to where the user was.
    monkeypatch.setattr(wt, "foreground_window", lambda: 111)
    monkeypatch.setattr(wt, "window_process_id", lambda hwnd: os.getpid() + 1)
    win._return_focus("target")
    assert calls == [("target", os.getpid())]
    # Another Nexus window (the Stack that is opening) took focus legitimately: leave it alone.
    calls.clear()
    monkeypatch.setattr(wt, "window_process_id", lambda hwnd: os.getpid())
    win._return_focus("target")
    assert calls == []
    # Still ours (hide in progress): restore.
    monkeypatch.setattr(wt, "foreground_window", lambda: own_hwnd)
    win._return_focus("target")
    assert len(calls) == 1
