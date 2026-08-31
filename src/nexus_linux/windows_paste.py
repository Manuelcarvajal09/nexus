from __future__ import annotations
import ctypes, logging, os, time
from ctypes import wintypes
from enum import Enum
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
log = logging.getLogger("nexus.paste")

class PasteResult(str, Enum):
    SUCCESS = "SUCCESS"
    TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"
    FOCUS_RESTORE_FAILED = "FOCUS_RESTORE_FAILED"
    CLIPBOARD_WRITE_FAILED = "CLIPBOARD_WRITE_FAILED"
    INPUT_FAILED = "INPUT_FAILED"
    CANCELLED = "CANCELLED"

class WindowsPasteService(QObject):
    paste_finished = Signal(bool, str)
    def __init__(self, on_write=None, on_paste_failed=None, parent=None):
        super().__init__(parent); self._on_write=on_write; self._on_paste_failed=on_paste_failed
        self._target: FocusTarget|None=None; self._busy=False; self._attempts=0; self._overlay=None; self._completion=None
        self._item_id=None; self._started_at=0.0
    def remember_target(self):
        candidate=capture_focus_target()
        if candidate and candidate.process_id != os.getpid():
            self._target=candidate
            log.info("target.remember hwnd=%#x focus=%#x pid=%s tid=%s", candidate.window, candidate.focused_control, candidate.process_id, candidate.thread_id)
        else:
            log.warning("target.reject hwnd=%s pid=%s", getattr(candidate,"window",None), getattr(candidate,"process_id",None))
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
        if self._busy:
            log.warning("paste.reject result=%s item=%s", PasteResult.CANCELLED.value, getattr(item,"id",None)); return False
        if self._target is None:
            self._report_immediate_failure(PasteResult.TARGET_UNAVAILABLE, completion); return False
        if not self.write_to_clipboard(item,plain_text):
            self._report_immediate_failure(PasteResult.CLIPBOARD_WRITE_FAILED, completion); return False
        self._busy=True; self._attempts=0; self._overlay=overlay; self._completion=completion
        self._item_id=getattr(item,"id",None); self._started_at=time.perf_counter()
        log.info("paste.begin item=%s target=%#x", self._item_id, self._target.window)
        overlay.hide(); log.info("overlay.hide item=%s visible=%s", self._item_id, overlay.isVisible() if hasattr(overlay,"isVisible") else "unknown")
        QTimer.singleShot(0,self._restore_and_send); return True
    def _restore_and_send(self):
        self._attempts+=1
        if restore_focus_target(self._target,os.getpid()):
            sent=self._send_ctrl_v()
            self._finish(PasteResult.SUCCESS if sent else PasteResult.INPUT_FAILED); return
        if self._attempts<10: QTimer.singleShot(15,self._restore_and_send); return
        self._finish(PasteResult.FOCUS_RESTORE_FAILED)
    def _send_ctrl_v(self):
        events=(INPUT*4)(INPUT(INPUT_KEYBOARD,INPUTUNION(ki=KEYBDINPUT(0x11,0,0,0,0))),INPUT(INPUT_KEYBOARD,INPUTUNION(ki=KEYBDINPUT(VK_V,0,0,0,0))),INPUT(INPUT_KEYBOARD,INPUTUNION(ki=KEYBDINPUT(VK_V,0,KEYEVENTF_KEYUP,0,0))),INPUT(INPUT_KEYBOARD,INPUTUNION(ki=KEYBDINPUT(0x11,0,KEYEVENTF_KEYUP,0,0))))
        return user32.SendInput(4,events,ctypes.sizeof(INPUT))==4
    def synthetic_paste(self):
        return bool(restore_focus_target(self._target,os.getpid()) and self._send_ctrl_v())
    def _report_immediate_failure(self,result,completion):
        reason=self._reason(result)
        log.warning("paste.finish result=%s reason=%s", result.value, reason)
        if self._on_paste_failed: self._on_paste_failed(reason)
        if completion: completion(False)
        self.paste_finished.emit(False,reason)
    def _reason(self,result):
        return {
            PasteResult.TARGET_UNAVAILABLE:"La aplicación destino ya no está disponible",
            PasteResult.FOCUS_RESTORE_FAILED:"No se pudo recuperar de forma segura el foco destino",
            PasteResult.CLIPBOARD_WRITE_FAILED:"No se pudo preparar el contenido en el portapapeles",
            PasteResult.INPUT_FAILED:"Windows no aceptó la pulsación de pegado",
            PasteResult.CANCELLED:"Ya hay una operación de pegado en curso",
        }.get(result,"")
    def _finish(self,result):
        ok=result is PasteResult.SUCCESS; reason=self._reason(result)
        completion,overlay=self._completion,self._overlay; self._busy=False; self._completion=None; self._overlay=None
        elapsed=(time.perf_counter()-self._started_at)*1000 if self._started_at else 0
        log.info("paste.finish item=%s result=%s attempts=%s latency_ms=%.1f", self._item_id, result.value, self._attempts, elapsed)
        self._item_id=None; self._started_at=0.0
        if not ok:
            if overlay is not None: overlay.show_and_focus()
            if self._on_paste_failed: self._on_paste_failed(reason)
        if completion: completion(ok)
        self.paste_finished.emit(ok,reason)
