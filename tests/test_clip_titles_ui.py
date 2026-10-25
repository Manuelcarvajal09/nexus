"""Title editing in the Windows panel: inline editor, menu, Details, cards, Stack row."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QBuffer, QEvent, QIODevice, QRect, QRectF, Qt
from PySide6.QtGui import QFocusEvent, QFont, QImage, QKeyEvent, QPainter, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QStyle, QStyleOptionViewItem

from nexus_linux import paths, styles, timeline_window, windows_timeline
from nexus_linux.row_widget import ClipRowWidget
from nexus_linux.storage import StorageService
from nexus_linux.timeline_model import ITEM_ROLE
from nexus_linux.windows_card_delegate import WindowsClipCardDelegate
from nexus_linux.windows_timeline import WindowsTimelineWindow

ORIGINAL = "Eres un auditor senior.\nRevisa TODO el sistema y entrega un informe completo."


@pytest.fixture
def panel(qapp, tmp_path, monkeypatch, paste_service):
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    storage = StorageService(tmp_path / "titles.sqlite")
    window = WindowsTimelineWindow(storage, paste_service)
    window.setFont(QFont("Segoe UI", 10))
    window.setStyleSheet(styles.qss())
    yield window
    window._search_debounce.stop()
    window._dismiss_timer.stop()
    if window._title_editor is not None:
        window._title_editor._finished = True
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    storage.close()


@pytest.fixture
def opened(panel, qapp):
    item = panel._storage.capture(kind="text", text_content=ORIGINAL, source_app="claude.exe")
    panel._storage.capture(kind="text", text_content="otro clip")
    panel.refresh()
    panel.show_and_focus()
    qapp.processEvents()
    panel._list.setCurrentIndex(panel._model.index(panel._model.row_for_id(item.id), 0))
    return panel, item


def test_f2_shortcut_opens_the_editor_for_the_current_card(opened):
    panel, item = opened
    f2 = [s for s in panel.findChildren(QShortcut) if s.key().toString() == "F2"]
    assert len(f2) == 1
    f2[0].activated.emit()
    editor = panel._title_editor
    assert editor is not None and editor.isVisible()
    assert panel._title_item_id == item.id
    assert panel._list.viewport().rect().intersects(editor.geometry())


def test_enter_saves_the_title_and_never_pastes_or_hides(opened, monkeypatch):
    panel, item = opened
    hidden = []
    monkeypatch.setattr(panel, "fade_hide", lambda: hidden.append(True))
    assert panel.begin_title_edit()
    panel._title_editor.setText("  Auditoría completa de ciberseguridad ")
    QTest.keyClick(panel._title_editor, Qt.Key.Key_Return)
    assert panel._title_editor is None
    assert panel._storage.get(item.id).title == "Auditoría completa de ciberseguridad"
    assert panel._paste.written == [] and panel._paste.paste_count == 0 and not hidden
    assert panel._model.item_at(panel._list.currentIndex().row()).id == item.id  # selection stays


def test_escape_cancels_without_saving_or_closing_the_panel(opened, monkeypatch):
    panel, item = opened
    hidden = []
    monkeypatch.setattr(panel, "fade_hide", lambda: hidden.append(True))
    panel.begin_title_edit()
    panel._title_editor.setText("No guardar")
    QTest.keyClick(panel._title_editor, Qt.Key.Key_Escape)
    assert panel._title_editor is None and not hidden
    assert panel._storage.get(item.id).title is None


def test_clearing_the_text_removes_an_existing_title(opened):
    panel, item = opened
    panel._storage.set_title(item.id, "Temporal")
    panel.refresh()
    panel.begin_title_edit(item.id)
    assert panel._title_editor.text() == "Temporal"
    panel._title_editor.setText("")
    QTest.keyClick(panel._title_editor, Qt.Key.Key_Return)
    assert panel._storage.get(item.id).title is None


def test_editor_limits_length_and_one_line(opened):
    panel, item = opened
    panel.begin_title_edit()
    panel._title_editor.setText("x" * 300)
    assert len(panel._title_editor.text()) == 80


def test_panel_shortcuts_cannot_steal_keys_from_the_editor(opened):
    panel, _ = opened
    panel.begin_title_edit()
    editor = panel._title_editor
    for key in (Qt.Key.Key_1, Qt.Key.Key_9, Qt.Key.Key_F):
        override = QKeyEvent(QEvent.Type.ShortcutOverride, key, Qt.KeyboardModifier.ControlModifier)
        editor.event(override)
        assert override.isAccepted(), key
    tab = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
    assert editor.event(tab) is True
    assert panel._title_editor is editor and panel._expanded is False


def test_losing_focus_or_hiding_the_panel_saves_what_was_typed(opened):
    panel, item = opened
    panel.begin_title_edit()
    panel._title_editor.setText("Al perder foco")
    panel._title_editor.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert panel._storage.get(item.id).title == "Al perder foco"

    panel.begin_title_edit()
    panel._title_editor.setText("Al ocultar")
    panel.fade_hide()
    assert panel._storage.get(item.id).title == "Al ocultar"


def test_editor_follows_its_card_when_the_list_refreshes(opened):
    panel, item = opened
    panel.begin_title_edit(item.id)
    panel._storage.capture(kind="text", text_content="llega un clip nuevo")  # new clip becomes row 0
    panel.refresh()
    row = panel._model.row_for_id(item.id)
    expected = WindowsClipCardDelegate.title_rect(panel._list.visualRect(panel._model.index(row, 0))).toRect()
    assert panel._title_editor is not None
    assert panel._title_editor.geometry().center() == expected.adjusted(-4, -2, 4, 2).center()
    panel._storage.delete(item.id)
    panel.refresh()
    assert panel._title_editor is None  # card disappeared -> editor closed, nothing saved


def test_context_menu_has_rename_and_it_opens_the_editor(opened, monkeypatch):
    panel, item = opened
    seen = []

    class RenameMenu(timeline_window.QMenu):
        def exec(self, _position):
            seen.extend(a.text() for a in self.actions())
            return next(a for a in self.actions() if a.text().startswith("Renombrar"))

    monkeypatch.setattr(timeline_window, "QMenu", RenameMenu)
    row = panel._model.row_for_id(item.id)
    panel._show_item_menu(panel._list.visualRect(panel._model.index(row, 0)).center())
    assert any(text.startswith("Renombrar") for text in seen)
    assert seen.index("Copiar nuevamente") < next(i for i, t in enumerate(seen) if t.startswith("Renombrar")) < seen.index("Detalles")
    assert panel._title_editor is not None and panel._title_item_id == item.id


def test_details_dialog_edits_the_title(opened, monkeypatch):
    panel, item = opened

    def accept_with_title(self):
        assert self.title_edit.maxLength() == 80
        self.title_edit.setText("Desde Detalles")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(windows_timeline._DetailsDialog, "exec", accept_with_title)
    panel._show_item_details(item)
    assert panel._storage.get(item.id).title == "Desde Detalles"
    monkeypatch.setattr(windows_timeline._DetailsDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    panel._show_item_details(panel._storage.get(item.id))
    assert panel._storage.get(item.id).title == "Desde Detalles"


def test_deleting_a_titled_clip_asks_first_and_plain_clips_do_not(opened, monkeypatch):
    panel, item = opened
    asked = []
    monkeypatch.setattr(windows_timeline.QMessageBox, "question",
                        lambda *args, **kwargs: asked.append(args[2]) or QMessageBox.StandardButton.Cancel)
    assert panel._confirm_delete(item) is True and not asked  # untitled: no question
    panel._storage.set_title(item.id, "Importante")
    titled = panel._storage.get(item.id)
    assert panel._confirm_delete(titled) is False and "Importante" in asked[0]
    monkeypatch.setattr(windows_timeline.QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    assert panel._confirm_delete(titled) is True


def test_pasting_a_titled_clip_writes_the_original_content(opened):
    panel, item = opened
    panel._storage.set_title(item.id, "Etiqueta que jamás se pega")
    titled = panel._storage.get(item.id)
    panel._select_and_paste(titled)
    written = panel._paste.written[-1]
    assert written.text_content == ORIGINAL and written.content_hash == item.content_hash
    panel._quick_paste(1)  # also by the numeric shortcut path
    assert all(w.text_content != "Etiqueta que jamás se pega" for w in panel._paste.written)


def test_history_search_finds_by_title_and_refresh_keeps_the_text(opened):
    panel, item = opened
    panel._storage.set_title(item.id, "Auditoría de CIBERSEGURIDAD")
    panel._search.setText("ciberseguridad")
    panel.refresh()
    assert [i.id for i in panel._model._items] == [item.id]
    assert panel._model.item_at(0).text_content == ORIGINAL


# -- cards and Stack rows -----------------------------------------------------

def _png():
    image = QImage(40, 30, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.red)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _model(kind="text", text="contenido", title=None, item_id=1):
    model = QStandardItemModel()
    entry = SimpleNamespace(
        id=item_id, kind=kind, text_content=text, content_hash=f"h{item_id}{kind}", title=title,
        image_data=_png() if kind == "image" else None, file_path="C:\\tmp\\archivo.txt" if kind == "file" else None,
        source_app="app.exe", updated_at=datetime.now(timezone.utc).timestamp(),
    )
    row = QStandardItem()
    row.setData(entry, ITEM_ROLE)
    model.appendRow(row)
    return model


def _render(delegate, model, height=148):
    image = QImage(216, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 216, height)
    option.state = QStyle.StateFlag.State_Enabled
    painter = QPainter(image)
    try:
        delegate.paint(painter, option, model.index(0, 0))
    finally:
        painter.end()
    return bytes(image.constBits())


@pytest.fixture
def delegate(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(styles, "_THEME_FILE", tmp_path / "theme.json")
    return WindowsClipCardDelegate(card_height=148)


@pytest.mark.parametrize("kind,text", [
    ("text", "Un prompt largo " * 20), ("url", "https://example.com/pagina"),
    ("file", ""), ("image", ""), ("text", "#FF2D55"), ("text", "git status --short"),
])
@pytest.mark.parametrize("height", [148, 160, 184])
def test_titled_cards_render_for_every_kind_without_changing_size(delegate, kind, text, height):
    delegate.set_card_height(height)
    plain = _render(delegate, _model(kind, text), height)
    titled = _render(delegate, _model(kind, text, title="Auditoría completa de ciberseguridad " * 3), height)
    assert plain != titled
    assert delegate.sizeHint(None, _model(kind, text).index(0, 0)).height() == max(148, height)


def test_title_is_not_painted_under_the_inline_editor(delegate):
    model = _model(title="Título visible")
    shown = _render(delegate, model)
    delegate.set_editing_item(1)
    assert _render(delegate, model) != shown
    delegate.set_editing_item(None)
    assert _render(delegate, model) == shown


def test_untitled_card_is_unchanged_by_the_title_feature(delegate):
    assert _render(delegate, _model(title=None)) == _render(delegate, _model(title=""))


def test_title_rect_sits_at_the_top_of_the_body_inside_the_card(delegate):
    card = QRect(0, 0, 216, 148)
    rect = WindowsClipCardDelegate.title_rect(card)
    header, body, footer = WindowsClipCardDelegate._regions(QRectF(card.adjusted(6, 6, -6, -6)))
    assert rect.top() == body.top() and rect.height() == WindowsClipCardDelegate.TITLE_HEIGHT
    assert rect.bottom() < footer.top() and rect.left() >= card.left() and rect.right() <= card.right()


def test_stack_row_shows_the_title_and_keeps_the_content_preview(qapp):
    plain = SimpleNamespace(id=1, kind="text", text_content="contenido del clip", content_hash="a",
                            image_data=None, title=None, is_pinned=False, preview=lambda n=64: "contenido del clip")
    titled = SimpleNamespace(**{**plain.__dict__, "title": "Mi etiqueta"})
    row = ClipRowWidget(plain, position=1)
    assert row.findChild(QLabel, "rowTitle").text() == "contenido del clip"
    row = ClipRowWidget(titled, position=1)
    assert row.findChild(QLabel, "rowTitle").text() == "Mi etiqueta"
    assert "contenido del clip" in row.findChild(QLabel, "rowMeta").text()
