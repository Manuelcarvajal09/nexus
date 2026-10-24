"""Synthetic offscreen renders: no storage, clipboard, network, or user files."""

import importlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from PySide6.QtCore import QAbstractAnimation, QBuffer, QByteArray, QEvent, QIODevice, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QMouseEvent, QPainter, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QListView, QStyle, QStyleOptionViewItem

from nexus_linux import thumbnails, type_detection
from nexus_linux.timeline_model import ITEM_ROLE


@pytest.fixture
def design(qapp, monkeypatch):
    """Contract double also permits running while Architect builds design tokens."""
    import nexus_linux
    # Windows' offscreen plugin may enumerate zero fonts. Load real system
    # faces into this QApplication only, not a global font installation.
    if not QFontDatabase.families() and sys.platform == "win32":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
    try:
        module = importlib.import_module("nexus_linux.windows_design")
    except ModuleNotFoundError as error:
        if error.name != "nexus_linux.windows_design":
            raise
        module = ModuleType("nexus_linux.windows_design")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setattr(nexus_linux, "windows_design", module, raising=False)
    mode = {"light": False}

    def palette():
        light = mode["light"]
        return SimpleNamespace(panel="#f5f5f7" if light else "#17191f",
                               panel_raised="#e8e9ee" if light else "#292c36",
                               card="#ffffff" if light else "#22252e",
                               card_hover="#eef1f7" if light else "#333946",
                               border="#d5d8df" if light else "#3b404d",
                               border_hover="#a5abba" if light else "#69748a",
                               text="#202530" if light else "#f0f2f6",
                               muted="#545d70" if light else "#b4bdcf",
                               faint="#697387" if light else "#939fb6",
                               accent="#698ada", accent_fill="#364973", accent_ink="#ffffff")

    def font(pixel_size=13, weight=QFont.Weight.Normal, mono=False):
        result = QFont("Consolas" if mono else "Segoe UI Variable")
        if not mono:
            result.setFamilies(["Segoe UI Variable", "Segoe UI"])
        result.setPixelSize(pixel_size)
        result.setWeight(weight)
        return result

    monkeypatch.setattr(module, "palette", palette, raising=False)
    monkeypatch.setattr(module, "font", font, raising=False)
    monkeypatch.setattr(module, "motion_enabled", lambda: True, raising=False)
    delegate_module = importlib.import_module("nexus_linux.windows_card_delegate")
    monkeypatch.setattr(delegate_module, "windows_design", module)
    return delegate_module, mode


def png_bytes():
    image = QImage(120, 40, QImage.Format.Format_ARGB32)
    image.fill(QColor("#3b9873"))
    painter = QPainter(image)
    painter.fillRect(0, 0, 30, 40, QColor("#d27168"))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


SAMPLES = {
    "text": ("text", "Synthetic note with a long title " * 20),
    "url": ("url", "https://example.test/research"),
    "youtube": ("url", "https://youtu.be/synthetic"),
    "image": ("image", ""), "file": ("file", ""),
    "color": ("text", "#498ab9"),
    "code": ("text", "def synthetic():\n    return 'example'"),
    "json": ("text", '{"synthetic": true, "count": 42}'),
    "command": ("text", "git status --short"),
    "path": ("text", "/synthetic/example.txt"),
    "email": ("text", "synthetic@example.test"),
    "phone": ("text", "+1 555 010 0123"),
}


def model_for(detected="text", count=1):
    model = QStandardItemModel()
    kind, text = SAMPLES[detected]
    for row in range(count):
        entry = SimpleNamespace(id=row, kind=kind, text_content=text, content_hash=f"synthetic-{row}-{detected}",
                                image_data=png_bytes() if kind == "image" else None,
                                file_path="C:\\Synthetic\\very-long-filename-for-elision.txt",
                                source_app="Synthetic Editor With A Very Long Name",
                                updated_at=datetime.now(timezone.utc).timestamp())
        item = QStandardItem()
        item.setData(entry, ITEM_ROLE)
        model.appendRow(item)
    return model


def render(delegate, model, flags=QStyle.StateFlag.State_Enabled, dpr=1):
    image = QImage(round(216 * dpr), round(delegate._card_height * dpr), QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 216, delegate._card_height)
    option.state = flags
    painter = QPainter(image)
    try:
        delegate.paint(painter, option, model.index(0, 0))
    finally:
        painter.end()
    return image


@pytest.mark.parametrize("detected", SAMPLES)
@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("dpr", [1, 1.5, 2])
def test_all_types_minimum_render(qapp, design, detected, light, dpr):
    module, mode = design
    mode["light"] = light
    delegate = module.WindowsClipCardDelegate(card_height=100)
    model = model_for(detected)
    assert type_detection.detect(model.index(0, 0).data(ITEM_ROLE).kind, SAMPLES[detected][1]) == detected
    assert delegate.sizeHint(None, model.index(0, 0)).height() == 148
    previews = {0: {"title": "Enriched synthetic title " * 15, "domain": "example.test"}}
    delegate.set_preview_data(previews)
    assert delegate._preview_data is previews
    image = render(delegate, model, dpr=dpr)
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(round(108 * dpr), round(70 * dpr)).alpha() == 255
    header, body, footer = delegate._regions(QRectF(6, 6, 204, 136))
    assert header.bottom() < body.top() < body.bottom() < footer.top()
    assert body.height() >= 60


def test_states_and_theme_visibly_differ(qapp, design):
    module, mode = design
    delegate = module.WindowsClipCardDelegate()
    model = model_for()
    base = QStyle.StateFlag.State_Enabled
    images = [render(delegate, model, flags=base | state) for state in
              (QStyle.StateFlag.State_None, QStyle.StateFlag.State_MouseOver,
               QStyle.StateFlag.State_Selected, QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)]
    assert len({bytes(image.constBits()) for image in images}) == 4
    mode["light"] = True
    assert render(delegate, model) != images[0]


def test_long_title_is_elided_and_regions_are_clipped(qapp, design, monkeypatch):
    module, _ = design
    delegate = module.WindowsClipCardDelegate(card_height=148)
    model = model_for("url")
    delegate.set_preview_data({0: {"title": "X" * 4000, "domain": "example.test", "og_image": png_bytes()}})
    calls = []

    class RecordingPainter(QPainter):
        def drawText(self, rect, flags, text):
            calls.append((QRectF(rect), text, self.clipBoundingRect()))
            return super().drawText(rect, flags, text)

    image = QImage(216, 148, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = RecordingPainter(image)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 216, 148)
    delegate.paint(painter, option, model.index(0, 0))
    painter.end()
    title_calls = [(rect, text, clip) for rect, text, clip in calls if text.startswith("X")]
    assert title_calls and title_calls[-1][1].endswith("…")
    assert title_calls[0][2].height() >= 30  # compact OG must not crush the title
    assert all(clip.bottom() < 116 for _, _, clip in title_calls)
    assert any(text == "example.test" for _, text, _ in calls)


def test_thumbnail_cover_cache_and_enriched_image(qapp, design):
    module, _ = design
    delegate = module.WindowsClipCardDelegate(card_height=220)
    model = model_for("image")
    first = render(delegate, model, dpr=2)
    cache = dict(thumbnails._rounded_cache)
    assert any(key[0].startswith("win:") for key in cache)
    assert render(delegate, model, dpr=2) == first
    assert all(thumbnails._rounded_cache[key].cacheKey() == pix.cacheKey() for key, pix in cache.items())
    delegate.set_preview_data({0: {"title": "Synthetic article", "domain": "example.test", "og_image": png_bytes()}})
    assert not render(delegate, model_for("url")).isNull()


@pytest.fixture
def animated_view(qapp, design):
    module, _ = design
    view = QListView()
    model = model_for(count=30)
    view.setModel(model)
    delegate = module.WindowsClipCardDelegate(view)
    view.setItemDelegate(delegate)
    view.resize(250, 200)
    view.show()
    qapp.processEvents()
    delegate._animation.stop()
    delegate._levels.clear()
    yield view, model, delegate
    view.close()
    view.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_motion_is_finite_visible_only_and_preserves_selection(animated_view, qapp):
    view, model, delegate = animated_view
    original = list(view.selectionModel().selectedIndexes())
    original_current = view.currentIndex()
    point = QPointF(view.visualRect(model.index(0, 0)).center())
    event = QMouseEvent(QEvent.Type.MouseMove, point, point, Qt.MouseButton.NoButton,
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    delegate.eventFilter(view.viewport(), event)
    assert delegate._animation.state() == QAbstractAnimation.State.Running
    assert all(delegate._visible(i) for i in delegate._targets)
    delegate._animation.setCurrentTime(75)
    assert any(0 < level < .35 for level in delegate._levels.values())
    delegate._animation.setCurrentTime(150)
    assert delegate._animation.state() == QAbstractAnimation.State.Stopped
    assert view.selectionModel().selectedIndexes() == original
    assert view.currentIndex() == original_current
    delegate.eventFilter(view.viewport(), QEvent(QEvent.Type.Leave))
    delegate._animation.setCurrentTime(150)
    assert delegate._levels == {}
    assert len(delegate.findChildren(type(delegate._animation))) == 1


def test_selection_animates_and_does_not_repaint_offscreen_cards(animated_view, monkeypatch):
    view, model, delegate = animated_view
    updates = []
    monkeypatch.setattr(view.viewport(), "update", lambda rect: updates.append(QRect(rect)))
    view.setCurrentIndex(model.index(0, 0))
    delegate._retarget()
    delegate._animation.setCurrentTime(75)
    assert updates and all(rect.intersects(view.viewport().rect()) for rect in updates)
    assert all(delegate._visible(index) for index in delegate._targets)
    assert view.selectionModel().selectedIndexes() == [model.index(0, 0)]
    delegate._animation.setCurrentTime(150)
    assert delegate._animation.state() == QAbstractAnimation.State.Stopped


@pytest.mark.parametrize("system_enabled", [False, True])
def test_reduced_motion_has_no_running_animation(animated_view, design, monkeypatch, system_enabled):
    view, model, delegate = animated_view
    module, _ = design
    monkeypatch.setattr(module.windows_design, "motion_enabled", lambda: system_enabled)
    if system_enabled:
        monkeypatch.setenv("NEXUS_REDUCED_MOTION", "1")
    view.setCurrentIndex(model.index(1, 0))
    delegate._retarget()
    assert delegate._animation.state() == QAbstractAnimation.State.Stopped
    assert view.currentIndex() == model.index(1, 0)
