"""Design review captures over synthetic data only (temporary DB, no hotkeys, no clipboard).

--real  uses Qt's native Windows platform and grabs the *screen* (real Acrylic/DWM
        composition on the real monitor). Without it, widgets are rendered offscreen
        (no backdrop): useful for layout, NOT proof of native behaviour.
Scale: QT_SCALE_FACTOR is applied on top of the monitor's own DPI; use --scale.
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

if "--real" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
else:
    os.environ["QT_QPA_PLATFORM"] = "windows"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tools"))

from PySide6.QtCore import QEvent, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QWidget
from nexus_linux import paths, styles, windows_app as wapp, windows_design
from nexus_linux.storage import StorageService
from nexus_linux.windows_settings import load_settings, save_settings
from nexus_linux.windows_shortcuts import ShortcutStore
from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow
from nexus_linux.windows_timeline import WindowsTimelineWindow
from windows_visual_preview import Paste, Screen, synthetic_image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--theme", default="liquid_glass", choices=styles.PRESETS)
    ap.add_argument("--scale", default="1")
    ap.add_argument("--screen", default="1920x1080", help="offscreen logical work area")
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--audit", action="store_true", help="print layout problems as JSON instead of saving images")
    ap.add_argument("--surfaces", default="history,pinboard,manager,empty,stack,settings,stress,notice")
    args = ap.parse_args()
    os.environ["QT_SCALE_FACTOR"] = args.scale
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    wanted = set(args.surfaces.split(","))
    with tempfile.TemporaryDirectory(prefix="nexus-review-") as tmp:
        sandbox = Path(tmp)
        paths.ensure_dirs = lambda: None
        paths.CONFIG_DIR = sandbox
        styles._THEME_FILE = sandbox / "theme.json"
        styles._active_key = args.theme
        app = QApplication([])
        windows_design.font_family()
        app.setStyleSheet(windows_design.qss()); app.setFont(windows_design.font())
        storage = StorageService(sandbox / "synthetic.sqlite")
        from nexus_linux import i18n
        english = i18n.language() == "en"   # sample data follows NEXUS_LANGUAGE, like the interface
        sample = {
            "boards": ("Shopping", "Ideas", "Work") if english else ("Compras", "Ideas", "Trabajo"),
            "text": ("Good ideas deserve a place.\n\nDesign with intent. Keep what matters. Come back when you need it."
                     if english else "Las buenas ideas merecen un lugar.\n\nDiseña con intención. Guarda lo importante. Vuelve cuando lo necesites."),
            "title": "Create function" if english else "Función de creación",
            "snipping": "Snipping Tool" if english else "Recortes",
        }
        board = storage.create_pinboard(sample["boards"][0], "#F0C56E", None)
        storage.create_pinboard(sample["boards"][1], "#BBA8FF", None); storage.create_pinboard(sample["boards"][2], "#80B5EE", None)
        storage.capture(kind="file", file_path="C:/Projects/Brand guidelines.pdf", source_app="Explorer")
        code = storage.capture(kind="text", text_content='const create = (idea) => {\n  return design(idea);\n};', source_app="VS Code")
        storage.capture(kind="text", text_content="#8BAEDB", source_app="Figma")
        link = storage.capture(kind="url", text_content="https://example.com/studio", source_app="Edge")
        image = storage.capture(kind="image", image_data=synthetic_image(), source_app=sample["snipping"])
        text = storage.capture(kind="text", text_content=sample["text"], source_app="Notepad")
        storage.add_to_pinboard(image.id, board.id); storage.add_to_pinboard(text.id, board.id)
        storage.set_title(code.id, sample["title"])
        for (row_id,) in storage._conn.execute("SELECT id FROM clip_items").fetchall():
            storage._conn.execute("UPDATE clip_items SET updated_at = ? WHERE id = ?", (time.time() - (text.id - row_id), row_id))
        storage._conn.commit()
        panel = WindowsTimelineWindow(storage, Paste())
        if not args.real:
            w, h = (int(v) for v in args.screen.lower().split("x"))
            panel.resolve_target_screen = lambda: Screen(w, h)
        panel._preview_data[link.id] = {"title": "A quieter place for your next big idea", "domain": "example.com"}
        panel._delegate.set_preview_data(panel._preview_data)
        wapp.register_hotkey = lambda *a, **k: True; wapp.unregister_hotkey = lambda *a, **k: True
        settings = load_settings(); store = ShortcutStore(settings)
        hotkeys = wapp.GlobalHotkeys(1, store, lambda: save_settings(settings)); hotkeys.register_all()
        panel.configure_settings(wapp.SettingsBackend(settings, store, hotkeys, panel, []))

        audit_report = {}

        def audit(widget, name):
            from PySide6.QtGui import QFontMetrics
            from PySide6.QtWidgets import QAbstractButton, QLabel, QScrollArea
            problems = []
            root = widget.rect()
            screen = widget.screen() or QGuiApplication.primaryScreen()
            if args.real and not screen.availableGeometry().contains(widget.frameGeometry()):
                problems.append(f"window outside screen: {widget.frameGeometry().getRect()} vs {screen.availableGeometry().getRect()}")
            for child in widget.findChildren(QWidget):
                if not child.isVisibleTo(widget) or child.width() <= 0:
                    continue
                ancestor, scrolled = child.parentWidget(), False
                while ancestor is not None and ancestor is not widget:
                    scrolled = scrolled or isinstance(ancestor, QScrollArea)
                    ancestor = ancestor.parentWidget()
                if not scrolled and not root.contains(child.mapTo(widget, child.rect().topLeft())):
                    problems.append(f"{type(child).__name__}#{child.objectName()} starts outside the window")
                right = child.mapTo(widget, child.rect().topRight()).x()
                if right > root.right() + 1 and not scrolled:
                    problems.append(f"{type(child).__name__}#{child.objectName()} overflows right by {right - root.right()}px")
                text = child.text() if isinstance(child, (QLabel, QAbstractButton)) else ""
                if text and not (isinstance(child, QLabel) and (child.wordWrap() or child.pixmap() is not None)):
                    need = QFontMetrics(child.font()).horizontalAdvance(text.split("	")[0])
                    if child.width() < need - 1 and child.maximumWidth() >= 16777215 - 1 or child.width() + 1 < need * 0.9:
                        problems.append(f"text may be clipped: '{text[:30]}' needs {need}px has {child.width()}px ({type(child).__name__}#{child.objectName()})")
            for area in widget.findChildren(QScrollArea):
                if area.isVisibleTo(widget) and area.horizontalScrollBar().maximum() > 0 and area.horizontalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
                    problems.append(f"horizontal scroll in {area.objectName()} (max {area.horizontalScrollBar().maximum()})")
            audit_report[name] = problems

        def snap(widget, name, wait=450):
            app.processEvents(); QTest.qWait(wait)
            if args.audit:
                audit(widget, name); return
            if args.real:
                g = widget.frameGeometry(); pad = 0
                r = QRect(g.x() - pad, g.y() - pad, g.width() + 2 * pad, g.height() + 2 * pad)
                screen = widget.screen() or QGuiApplication.primaryScreen()
                screen.grabWindow(0, r.x(), r.y(), r.width(), r.height()).save(str(out / f"{name}.png"))
            else:
                widget.grab().save(str(out / f"{name}.png"))

        panel.show_and_focus()
        if "history" in wanted: snap(panel, "history")
        if "pinboard" in wanted:
            panel._select_pinboard(board.id); snap(panel, "pinboard"); panel._select_pinboard(None)
        if "manager" in wanted:
            panel.show_pinboard_manager(); snap(panel, "manager"); panel.close_pinboard_manager()
        if "settings" in wanted:
            panel.show_settings(); snap(panel, "settings"); panel.close_pinboard_manager()
        if "empty" in wanted:
            panel._search.setText("synthetic nonexistent query"); panel.refresh(); snap(panel, "empty-search")
            panel._search.setText(""); panel.refresh()
        if "stress" in wanted:
            long_title = "Título extremadamente largo " * 6
            for n in range(60):
                it = storage.capture(kind="text", text_content=f"Elemento sintético {n} " + "palabra " * (n % 40), source_app="Aplicación con nombre muy largo")
                if n % 7 == 0: storage.set_title(it.id, long_title)
            panel.refresh(); panel._search.setText("sint"); panel.refresh(); snap(panel, "stress-search")
            panel._search.setText(""); panel.refresh()
        if "nasty" in wanted:
            for name in ("Una colección con un nombre absurdamente largo que no cabe en la barra lateral",
                         "Ñandú ÁÉÍÓÚ — café ☕ 日本語", "🔥🚀💡🎯📌⭐️🗂️🎨❤️📎", "W" * 30, "x"):
                b = storage.create_pinboard(name[:80])
                for k in range(3 if len(name) < 5 else 12):
                    storage.add_to_pinboard(storage.capture(kind="text", text_content=f"{name[:6]} {k}").id, b.id)
            panel.show_pinboard_manager(); snap(panel, "manager-nasty"); panel.close_pinboard_manager()
        if "notice" in wanted:
            panel.refresh()
            panel._delete_item(panel._storage.get(panel._model.item_at(0).id)); snap(panel, "notice-undo")
            panel.undo_delete()
        if "menus" in wanted:
            panel.refresh()
            def grab_popup(menu, name):
                # Menus are rendered from the widget itself (QSS look); native popups
                # are not reliably capturable from the screen in this setup.
                app.processEvents(); QTest.qWait(250)
                menu.grab().save(str(out / f"{name}.png"))
                for sub in menu.findChildren(QMenu):
                    if sub.isVisible(): sub.grab().save(str(out / f"{name}-sub.png"))
            counter = {"n": 0}
            def fake_exec(menu, pos=None, *a):
                counter["n"] += 1
                menu.popup(pos); grab_popup(menu, f"menu-item")
                for action in menu.actions():
                    if action.menu() is not None and action.text() == "Pinboards":
                        menu.setActiveAction(action); grab_popup(menu, "menu-item-pinboards"); break
                menu.close(); return None
            import nexus_linux.timeline_window as tw
            class CapMenu(QMenu):
                def exec(self, pos=None, *a): return fake_exec(self, pos)
            tw.QMenu = CapMenu
            index = panel._model.index(0, 0)
            c = panel._list.visualRect(index).center(); panel._show_item_menu(c)
            options = panel._management_btn.menu()
            options.popup(panel._management_btn.mapToGlobal(panel._management_btn.rect().bottomLeft())); grab_popup(options, "menu-options"); options.close()
        panel.close()
        if "stack" in wanted:
            stack = WindowsClipStack(storage, Paste()); sw = WindowsStackWindow(stack); sw.start_collecting()
            for item in (text, image, code): sw.add_capture(item)
            sw.show(); snap(sw, "stack")
            if os.environ.get("NEXUS_DEBUG_STACK"):
                L = sw._list; print("list", L.height(), L.count(), L.sizeHintForRow(0), L.spacing(), [L.visualItemRect(L.item(i)).getRect() for i in range(L.count())], [L.itemWidget(L.item(i)).geometry().getRect() for i in range(L.count())], "win", sw.height())
            sw.close(); sw.deleteLater()
        panel.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        storage.close()
        if args.audit:
            import json as _json
            print("AUDIT " + _json.dumps({"theme": args.theme, "scale": args.scale, "surfaces": audit_report}, ensure_ascii=False))
        else:
            print(out)


if __name__ == "__main__":
    main()
