from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication, QImage

from .windows_platform import VK_V, activate_window, foreground_window

user32 = ctypes.WinDLL("user32", use_last_error=True)
INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class INPUTUNION(ctypes.Union): _fields_ = [("ki", KEYBDINPUT)]
class INPUT(ctypes.Structure): _anonymous_ = ("u",); _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]

class WindowsPasteService:
    def __init__(self, on_write=None, on_paste_failed=None):
        self._on_write, self._on_paste_failed, self._target = on_write, on_paste_failed, 0

    def remember_target(self):
        self._target = foreground_window()

    def write_to_clipboard(self, item):
        if self._on_write: self._on_write()
        clipboard = QGuiApplication.clipboard()
        if item.kind in ("text", "url"):
            clipboard.setText(item.text_content or "")
        elif item.kind == "image" and item.image_data:
            image = QImage(); image.loadFromData(item.image_data, "PNG"); clipboard.setImage(image)
        elif item.kind == "file" and item.file_path:
            from PySide6.QtCore import QMimeData, QUrl
            mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(item.file_path)]); clipboard.setMimeData(mime)
        else: return False
        return True

    def synthetic_paste(self):
        if not self._target or not activate_window(self._target) or foreground_window() != self._target:
            if self._on_paste_failed: self._on_paste_failed("No se pudo restaurar el foco de la aplicación destino")
            return False
        events = (INPUT * 4)(
            INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(0x11,0,0,0,0))),
            INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(VK_V,0,0,0,0))),
            INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(VK_V,0,KEYEVENTF_KEYUP,0,0))),
            INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(0x11,0,KEYEVENTF_KEYUP,0,0))),
        )
        return user32.SendInput(4, events, ctypes.sizeof(INPUT)) == 4
