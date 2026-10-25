"""Render synthetic UI fixtures only; never access the desktop or clipboard."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication
from nexus_linux import paths, styles
from nexus_linux.storage import StorageService
from nexus_linux.windows_timeline import WindowsTimelineWindow
from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow


class Paste:
    def paste_item(self, *args, **kwargs): return False
    def write_to_clipboard(self, *args, **kwargs): return False


class Screen:
    def __init__(self, width=1920, height=1080): self.width, self.height = width, height
    def availableGeometry(self): return QRect(0, 0, self.width, self.height)
    def geometry(self): return self.availableGeometry()


def synthetic_image():
    image = QImage(700, 420, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, 700, 420)
    gradient.setColorAt(0, QColor("#547AA8")); gradient.setColorAt(1, QColor("#C79A8E"))
    painter.fillRect(image.rect(), gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#E9D9CB")); painter.drawEllipse(460, 46, 88, 88)
    painter.setBrush(QColor("#35465C")); painter.drawEllipse(-120, 220, 720, 480)
    painter.setBrush(QColor("#25354A")); painter.drawEllipse(260, 255, 700, 450)
    painter.end()
    buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, "PNG")
    return bytes(buffer.data())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--theme", default="liquid_glass", choices=styles.PRESETS)
    parser.add_argument("--legacy-style", "--baseline", dest="legacy_style", action="store_true", help="Diagnostic legacy stylesheet only, not a source rollback")
    parser.add_argument("--screen", default="1920x1080", help="Logical work area WIDTHxHEIGHT")
    parser.add_argument("--scale", default="1", help="Qt DPI scale (1, 1.5, 2)")
    args = parser.parse_args()
    os.environ["QT_SCALE_FACTOR"] = args.scale
    screen_width, screen_height = (int(value) for value in args.screen.lower().split("x"))
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nexus-visual-") as temporary:
        sandbox = Path(temporary)
        paths.ensure_dirs = lambda: None
        styles._THEME_FILE = sandbox / "theme.json"
        styles._active_key = args.theme
        app = QApplication([])
        from nexus_linux import windows_design
        windows_design.font_family()  # process-local system fonts for offscreen
        if args.legacy_style:
            app.setStyleSheet(styles.qss()); app.setFont(QFont("Segoe UI", 10))
        else:
            app.setStyleSheet(windows_design.qss()); app.setFont(windows_design.font())
        storage = StorageService(sandbox / "synthetic.sqlite")
        board = storage.create_pinboard("Mercado Libre", "#F0C56E", None)
        storage.create_pinboard("Ideas", "#BBA8FF", None)
        storage.create_pinboard("Trabajo", "#80B5EE", None)
        storage.capture(kind="file", file_path="C:/Projects/Brand guidelines.pdf", source_app="Explorer")
        code = storage.capture(kind="text", text_content='const create = (idea) => {\n  return design(idea);\n};', source_app="VS Code")
        storage.capture(kind="text", text_content="#8BAEDB", source_app="Figma")
        link = storage.capture(kind="url", text_content="https://example.com/studio", source_app="Edge")
        image = storage.capture(kind="image", image_data=synthetic_image(), source_app="Recortes")
        text = storage.capture(kind="text", text_content="Las buenas ideas merecen un lugar.\n\nDiseña con intención. Guarda lo importante. Vuelve cuando lo necesites.", source_app="Notepad")
        storage.add_to_pinboard(image.id, board.id); storage.add_to_pinboard(text.id, board.id)
        # Deterministic synthetic ordering independent of Windows clock tick
        # granularity. This connection points ONLY to the temporary fixture DB.
        for row_id, in storage._conn.execute("SELECT id FROM clip_items").fetchall():
            storage._conn.execute("UPDATE clip_items SET updated_at = ? WHERE id = ?", (time.time() - (text.id - row_id), row_id))
        storage._conn.commit()
        panel = WindowsTimelineWindow(storage, Paste())
        panel.resolve_target_screen = lambda: Screen(screen_width, screen_height)
        panel._preview_data[link.id] = {"title": "A quieter place for your next big idea", "domain": "example.com"}
        panel._delegate.set_preview_data(panel._preview_data)
        panel.show_and_focus(); app.processEvents()
        # Finish finite animations deterministically, without a desktop capture.
        from PySide6.QtTest import QTest
        QTest.qWait(180)
        panel.grab().save(str(output / "history.png"))
        panel._select_pinboard(board.id); QTest.qWait(180)
        panel.grab().save(str(output / "pinboard.png"))
        panel.show_pinboard_manager(); QTest.qWait(180)
        panel.grab().save(str(output / "pinboard-manager.png"))
        panel.close_pinboard_manager(); panel._select_pinboard(None)
        panel._preview_data[link.id] = {"title": "A quieter place for your next big idea", "domain": "example.com"}
        panel._search.setText("synthetic nonexistent query"); panel.refresh(); QTest.qWait(180)
        panel.grab().save(str(output / "empty-search.png"))
        stack = WindowsClipStack(storage, Paste()); stack_window = WindowsStackWindow(stack)
        stack_window.start_collecting()
        for item in (text, image, code): stack_window.add_capture(item)
        stack_window.show(); app.processEvents(); QTest.qWait(180)
        stack_window.grab().save(str(output / "stack.png"))
        for window in (panel, stack_window):
            window.close(); window.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        storage.close()
        print(str(output))


if __name__ == "__main__": main()
