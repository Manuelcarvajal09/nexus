from __future__ import annotations
import ctypes, os
from ctypes import wintypes
from PySide6.QtCore import QMimeData, QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QGuiApplication, QImage
from .windows_platform import VK_V, FocusTarget, capture_focus_target, restore_focus_target

user32 = ctypes.WinDLL("user32", use_last_error=True)
INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class INPUTUNION(ctypes.Union): _fields_ = [("ki", KEYBDINPUT)]
class INPUT(ctypes.Structure): _anonymous_ = ("u",); _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]

class WindowsPasteService(QObject):
    paste_finished = Signal(bool, str)
    def __init__(self, on_write=None, on_paste_failed=None, parent=None):
        super().__init__(parent); self._on_write=on_write; self._on_paste_failed=on_paste_failed
        self._target: FocusTarget|None=None; self._busy=False; self._attempts=0; self._overlay=None; self._completion=None
    def remember_target(self):
        candidate=capture_focus_target()
        if candidate and candidate.process_id != os.getpid(): self._target=candidate
        return self._target
    def write_to_clipboard(self, item, plain_text=False):
        if self._on_write: self._on_write()
        cb=QGuiApplication.clipboard()
        if item.kind in ("text","url") or plain_text: cb.setText(item.text_content or item.file_path or "")
        elif item.kind=="image" and item.image_data:
            image=QImage(); image.loadFromData(item.image_data,"PNG")
            if image.isNull(): return False
            cb.setImage(image)
        elif item.kind=="file" and item.file_path:
            mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(item.file_path)]); cb.setMimeData(mime)
        else: return False
        return True
    def paste_item(self,item,overlay,completion=None,plain_text=False):
        if self._busy or self._target is None or not self.write_to_clipboard(item,plain_text): return False
        self._busy=True; self._attempts=0; self._overlay=overlay; self._completion=completion; overlay.hide(); QTimer.singleShot(0,self._restore_and_send); return True
    def _restore_and_send(self):
        self._attempts+=1
        if restore_focus_target(self._target,os.getpid()): self._finish(self._send_ctrl_v(),""); return
        if self._attempts<10: QTimer.singleShot(15,self._restore_and_send); return
        self._finish(False,"No se pudo recuperar de forma segura el foco destino")
    def _send_ctrl_v(self):
        events=(INPUT*4)(INPUT(INPUT_KEYBOARD,INPUTUNION(ki=KEYBDINPUT(0x11,0,0,0,0))),INPUT(INPUT_KEYBOARD,INPUTUNION(ki=KEYBDINPUT(VK_V,0,0,0,0))),INPUT(INPUT_KEYBOARD,INPUTUNION(ki=KEYBDINPUT(VK_V,0,KEYEVENTF_KEYUP,0,0))),INPUT(INPUT_KEYBOARD,INPUTUNION(ki=KEYBDINPUT(0x11,0,KEYEVENTF_KEYUP,0,0))))
        return user32.SendInput(4,events,ctypes.sizeof(INPUT))==4
    def synthetic_paste(self):
        return bool(restore_focus_target(self._target,os.getpid()) and self._send_ctrl_v())
    def _finish(self,ok,reason):
        completion,overlay=self._completion,self._overlay; self._busy=False; self._completion=None; self._overlay=None
        if not ok:
            if overlay is not None: overlay.show_and_focus()
            if self._on_paste_failed: self._on_paste_failed(reason)
        if completion: completion(ok)
        self.paste_finished.emit(ok,reason)
