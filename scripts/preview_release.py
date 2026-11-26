#!/usr/bin/env python3
"""Render synthetic Qt previews without clipboard access or session changes."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['HYPRLAND_INSTANCE_SIGNATURE'] = ''
os.environ['NEXUS_CAPTURE_DISABLED'] = '1'


def main():
    with tempfile.TemporaryDirectory(prefix='nexus-preview-') as tmp:
        os.environ['NEXUS_PROFILE_ROOT'] = tmp
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QImage, QColor
        from PySide6.QtCore import QBuffer, QIODevice
        from nexus_linux import styles, product_ui, caelestia_theme
        from nexus_linux.storage import StorageService
        from nexus_linux.timeline_window import TimelineWindow
        from nexus_linux.stack_window import StackWindow
        from nexus_linux.stack import ClipStack
        from nexus_linux.appearance import AppearanceDialog
        class NoClipboard:
            def write_to_clipboard(self, item): return True
            def synthetic_paste(self): return False
        app = QApplication([])
        caelestia_theme.apply()
        app.setStyleSheet(styles.qss()+product_ui.qss())
        storage = StorageService(Path(':memory:'), allow_retention=False)
        board = storage.create_pinboard('Proyecto Nexus', '#397FD5', '◆')
        storage.create_pinboard('Ideas', '#B28CFF', '✦')
        storage.create_pinboard('Fragmentos', '#39B982', '⌘')
        texts = ['Construir herramientas que se sientan como parte de tu escritorio.',
                 'Una idea, un lugar.\n\nHistorial y colecciones en una misma ventana.',
                 'def preparar_siguiente():\n    return stack.siguiente()',
                 'https://example.com/nexus']
        items = []
        for text in texts:
            item = storage.capture(kind='text', text_content=text)
            storage.add_to_pinboard(item.id, board.id); items.append(item)
        image = QImage(480, 240, QImage.Format.Format_ARGB32); image.fill(QColor('#397FD5'))
        buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, 'PNG')
        storage.capture(kind='image', image_data=bytes(buffer.data()))
        out = ROOT/'docs/verification'; out.mkdir(parents=True, exist_ok=True)
        window = TimelineWindow(storage, NoClipboard()); window.resize(1200, 390); window.show()
        app.processEvents(); window.grab().save(str(out/'history.png'))
        window.show_pinboards(); app.processEvents(); window.grab().save(str(out/'pinboards.png'))
        clip_stack = ClipStack(storage, NoClipboard())
        for item in items[:3]: clip_stack.add(item)
        stack = StackWindow(clip_stack); stack.resize(420, 640); stack.show()
        app.processEvents(); stack.grab().save(str(out/'stack.png'))
        dialog = AppearanceDialog(styles.set_active_theme); dialog.show()
        app.processEvents(); dialog.grab().save(str(out/'appearance.png'))
        dialog.close(); stack.close(); window.close(); storage.close()
        print('Previews sintéticos en', out)

if __name__ == '__main__':
    main()
