"""TimelineModel is a plain QAbstractListModel wrapping a Python list --
no QApplication/display needed, confirmed to instantiate and behave
correctly headless. This is the structural fix behind the Timeline's
Ctrl+Shift+V latency (see PERFORMANCE_BASELINE.md §2.1): these tests pin
down the model contract the virtualized QListView relies on."""

from PySide6.QtCore import QModelIndex

from nexus_linux.timeline_model import ITEM_ROLE, TimelineModel


def _fake_item(item_id: int):
    class _Item:
        def __init__(self, id_):
            self.id = id_

        def preview(self, max_len=60):
            return f"item-{self.id}"

    return _Item(item_id)


class TestEmptyModel:
    def test_starts_empty(self):
        model = TimelineModel()
        assert model.rowCount() == 0

    def test_item_at_out_of_range_returns_none(self):
        model = TimelineModel()
        assert model.item_at(0) is None
        assert model.item_at(-1) is None

    def test_row_for_id_on_empty_model_returns_negative_one(self):
        model = TimelineModel()
        assert model.row_for_id(999) == -1


class TestSetItems:
    def test_row_count_matches_items(self):
        model = TimelineModel()
        items = [_fake_item(i) for i in range(5)]
        model.set_items(items)
        assert model.rowCount() == 5

    def test_item_at_returns_correct_object_by_position(self):
        model = TimelineModel()
        items = [_fake_item(i) for i in range(3)]
        model.set_items(items)
        assert model.item_at(0).id == 0
        assert model.item_at(1).id == 1
        assert model.item_at(2).id == 2

    def test_row_for_id_finds_correct_row(self):
        model = TimelineModel()
        items = [_fake_item(10), _fake_item(20), _fake_item(30)]
        model.set_items(items)
        assert model.row_for_id(20) == 1
        assert model.row_for_id(999) == -1

    def test_replacing_items_replaces_wholesale_not_appends(self):
        model = TimelineModel()
        model.set_items([_fake_item(1), _fake_item(2)])
        model.set_items([_fake_item(3)])
        assert model.rowCount() == 1
        assert model.item_at(0).id == 3

    def test_setting_empty_list_clears_model(self):
        model = TimelineModel()
        model.set_items([_fake_item(1), _fake_item(2)])
        model.set_items([])
        assert model.rowCount() == 0


class TestDataRole:
    def test_item_role_returns_the_object_itself(self):
        model = TimelineModel()
        item = _fake_item(42)
        model.set_items([item])
        index = model.index(0, 0)
        assert model.data(index, ITEM_ROLE) is item

    def test_display_role_uses_preview(self):
        model = TimelineModel()
        model.set_items([_fake_item(7)])
        index = model.index(0, 0)
        assert model.data(index) == "item-7"

    def test_invalid_index_returns_none(self):
        model = TimelineModel()
        model.set_items([_fake_item(1)])
        assert model.data(QModelIndex(), ITEM_ROLE) is None

    def test_out_of_range_index_returns_none(self):
        model = TimelineModel()
        model.set_items([_fake_item(1)])
        out_of_range = model.index(5, 0)
        assert model.data(out_of_range, ITEM_ROLE) is None

    def test_unknown_role_returns_none(self):
        model = TimelineModel()
        model.set_items([_fake_item(1)])
        index = model.index(0, 0)
        assert model.data(index, ITEM_ROLE + 999) is None
