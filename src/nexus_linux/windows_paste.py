from __future__ import annotations
import ctypes, logging, os, time, uuid
from ctypes import wintypes
from enum import Enum
from PySide6.QtCore import QMimeData, QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QGuiApplication, QImage
from .windows_platform import (
    NEXUS_INPUT_MARKER, VK_V, FocusTarget, capture_focus_target,
    restore_focus_target, clipboard_sequence, foreground_window, is_shell_surface,
    input_blocked_by_uipi,
)

user32 = ctypes.WinDLL("user32", use_last_error=True)
INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]
class INPUTUNION(ctypes.Union):
    # The union must include its largest Win32 member.  With only KEYBDINPUT
    # ctypes made INPUT 32 bytes on x64; Windows requires sizeof(INPUT)==40
    # and rejected every synthetic Ctrl+V with SendInput returning zero.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]
class INPUT(ctypes.Structure): _anonymous_ = ("u",); _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
log = logging.getLogger("nexus.paste")
WRITE_MARKER = "application/x-nexus-write-id"

class PasteResult(str, Enum):
    INPUT_ACCEPTED = "INPUT_ACCEPTED"
    TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"
    FOCUS_RESTORE_FAILED = "FOCUS_RESTORE_FAILED"
    CLIPBOARD_WRITE_FAILED = "CLIPBOARD_WRITE_FAILED"
    INPUT_FAILED = "INPUT_FAILED"
    ELEVATED_TARGET = "ELEVATED_TARGET"
    CANCELLED = "CANCELLED"

class WindowsPasteService(QObject):
    paste_finished = Signal(bool, str)
    def __init__(self, on_write=None, on_paste_failed=None, parent=None):
        super().__init__(parent); self._on_write=on_write; self._on_paste_failed=on_paste_failed
        self._target: FocusTarget|None=None; self._busy=False; self._attempts=0; self._overlay=None; self._completion=None
        self._item_id=None; self._started_at=0.0; self._write_sequence=None
        self._last_write_sequence=None; self._paste_generation=0
    def remember_target(self):
        candidate=capture_focus_target()
        if candidate and is_shell_surface(candidate.window):
            # Taskbar/tray/desktop (e.g. Nexus opened from the tray icon): keep the
            # last real app instead of sending the paste into the taskbar.
            log.info("target.skip_shell hwnd=%#x keep=%s", candidate.window, self._target is not None)
            candidate = None
        if candidate and candidate.process_id != os.getpid():
            self._target=candidate
            log.info("target.remember hwnd=%#x focus=%#x pid=%s tid=%s", candidate.window, candidate.focused_control, candidate.process_id, candidate.thread_id)
        else:
            log.warning("target.reject hwnd=%s pid=%s", getattr(candidate,"window",None), getattr(candidate,"process_id",None))
        return self._target
    def write_to_clipboard(self, item, plain_text=False):
        self._last_write_sequence = None
        # Validate before announcing an own write. Invalid images must not
        # suppress the user's next genuine notification.
        mime=QMimeData()
        if item.kind in ("text","url") or plain_text: mime.setText(item.text_content or item.file_path or "")
        elif item.kind=="image" and item.image_data:
            image=QImage(); image.loadFromData(item.image_data,"PNG")
            if image.isNull(): return False
            mime.setImageData(image)
        elif item.kind=="file" and item.file_path:
            mime.setUrls([QUrl.fromLocalFile(item.file_path)])
        else: return False
        token = uuid.uuid4().hex.encode("ascii")
        mime.setData(WRITE_MARKER, token)
        if self._on_write: self._on_write()
        try:
            cb = QGuiApplication.clipboard()
            sequence_before = clipboard_sequence() if QGuiApplication.platformName() == "windows" else None
            cb.setMimeData(mime)
            # Qt's setters return void even when Windows cannot open the
            # clipboard. Verify ownership AND this particular write instead of
            # synthesizing Ctrl+V against an older value after a failed write.
            if QGuiApplication.platformName() == "windows" and not cb.ownsClipboard():
                return False
            sequence_written = clipboard_sequence() if QGuiApplication.platformName() == "windows" else None
            current = cb.mimeData()
            marker_matches = current is not None and bytes(current.data(WRITE_MARKER)) == token
            sequence_verified = (
                sequence_before is None or
                (sequence_written not in (None, 0) and sequence_written != sequence_before
                 and clipboard_sequence() == sequence_written)
            )
            if not marker_matches or not sequence_verified:
                log.warning("clipboard.write_unverified marker=%s sequence=%s",
                            marker_matches, sequence_verified)
                return False
            self._last_write_sequence = sequence_written
            return True
        except (RuntimeError, OSError) as exc:
            log.error("clipboard.write_failed exception_type=%s", type(exc).__name__)
            return False
    def paste_item(self,item,overlay,completion=None,plain_text=False):
        if self._busy:
            log.warning("paste.reject result=%s item=%s", PasteResult.CANCELLED.value, getattr(item,"id",None)); return False
        if self._target is None:
            self._report_immediate_failure(PasteResult.TARGET_UNAVAILABLE, completion); return False
        if not self.write_to_clipboard(item,plain_text):
            self._report_immediate_failure(PasteResult.CLIPBOARD_WRITE_FAILED, completion); return False
        written_sequence = self._last_write_sequence
        if QGuiApplication.platformName() == "windows":
            if not written_sequence or clipboard_sequence() != written_sequence:
                self._report_immediate_failure(PasteResult.CANCELLED, completion); return False
            self._write_sequence = written_sequence
        else:
            self._write_sequence = clipboard_sequence()
        self._paste_generation += 1
        self._busy=True; self._attempts=0; self._overlay=overlay; self._completion=completion
        self._item_id=getattr(item,"id",None); self._started_at=time.perf_counter()
        log.info("paste.begin item=%s target=%#x", self._item_id, self._target.window)
        overlay.hide(); log.info("overlay.hide item=%s visible=%s", self._item_id, overlay.isVisible() if hasattr(overlay,"isVisible") else "unknown")
        self._queue_restore(0); return True
    def paste_request(self, item, target, completion, valid=lambda: True):
        """External Stack transaction: never restore/steal focus, never sleep.

        The caller serializes requests and owns the post-dispatch settle check.
        Acknowledgement means all expected marked events were accepted only:
        four with neutral Ctrl, or V-down/up while preserving a user's Ctrl.
        """
        if self._busy:
            return False
        if not valid() or not self.target_matches(target):
            completion(False)
            return False
        if not self.write_to_clipboard(item):
            completion(False)
            return False
        sequence = self._last_write_sequence
        if not sequence or clipboard_sequence() != sequence:
            completion(False)
            return False
        self._write_sequence = sequence
        self._paste_generation += 1
        generation = self._paste_generation
        self._busy = True
        self._overlay = None
        self._completion = completion
        self._item_id = item.id
        self._started_at = time.perf_counter()
        deadline = time.monotonic() + 1.5

        def dispatch():
            if not self._busy or generation != self._paste_generation:
                return
            if not valid() or clipboard_sequence() != sequence or not self.target_matches(target):
                self._finish(PasteResult.CANCELLED, restore_overlay=False)
                return
            if self.modifiers_down():
                if time.monotonic() >= deadline:
                    self._finish(PasteResult.CANCELLED, restore_overlay=False)
                else:
                    QTimer.singleShot(10, dispatch)
                return
            try:
                sent = self._send_ctrl_v()
            except Exception:
                sent = False
            ok = sent and clipboard_sequence() == sequence and self.target_matches(target) and valid()
            self._finish(PasteResult.INPUT_ACCEPTED if ok else PasteResult.INPUT_FAILED, restore_overlay=False)

        QTimer.singleShot(0, dispatch)
        return True

    @staticmethod
    def target_matches(target):
        return bool(target and target.process_id != os.getpid() and capture_focus_target() == target)

    @staticmethod
    def modifiers_down():
        # A user's held Ctrl is the intended paste modifier, not a reason to
        # wait/cancel. _send_ctrl_v preserves it and injects only V in that case.
        # Shift/Alt/Win or a still-held V would change/repeat the action.
        return any(user32.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x12, 0x5B, 0x5C, VK_V))
    def _queue_restore(self, delay):
        generation = self._paste_generation
        QTimer.singleShot(delay, lambda: self._restore_and_send(generation))
    def _restore_and_send(self, generation=None):
        if not self._busy or (generation is not None and generation != self._paste_generation):
            return
        if clipboard_sequence() != self._write_sequence:
            self._finish(PasteResult.CANCELLED); return
        self._attempts+=1
        try:
            restored = restore_focus_target(self._target, os.getpid())
        except Exception as exc:
            log.error("paste.focus_restore_exception exception_type=%s", type(exc).__name__)
            self._finish(PasteResult.FOCUS_RESTORE_FAILED)
            return
        if restored:
            if clipboard_sequence() != self._write_sequence:
                self._finish(PasteResult.CANCELLED); return
            if self.modifiers_down():
                if time.perf_counter() - self._started_at >= 1.5:
                    self._finish(PasteResult.CANCELLED)
                else:
                    self._queue_restore(10)
                return
            if input_blocked_by_uipi(getattr(self._target, "process_id", 0)):
                # Windows silently drops synthetic keys sent to an elevated app; it used to
                # be logged as INPUT_ACCEPTED. The app is focused and the content copied.
                self._finish(PasteResult.ELEVATED_TARGET, restore_overlay=False)
                return
            try:
                sent = self._send_ctrl_v()
            except Exception as exc:
                log.error("paste.sendinput_exception exception_type=%s", type(exc).__name__)
                sent = False
            if sent and clipboard_sequence() != self._write_sequence:
                self._finish(PasteResult.CANCELLED)
            elif sent and foreground_window() != self._target.window:
                self._finish(PasteResult.FOCUS_RESTORE_FAILED)
            else:
                self._finish(PasteResult.INPUT_ACCEPTED if sent else PasteResult.INPUT_FAILED)
            return
        if self._attempts<10: self._queue_restore(15); return
        self._finish(PasteResult.FOCUS_RESTORE_FAILED)
    def cancel_pending(self, overlay=None):
        if self._busy and (overlay is None or overlay is self._overlay):
            self._finish(PasteResult.CANCELLED, restore_overlay=False)
    def _send_ctrl_v(self):
        marker = NEXUS_INPUT_MARKER
        held_control = any(user32.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0xA2, 0xA3))
        keys = [(VK_V, 0), (VK_V, KEYEVENTF_KEYUP)]
        if not held_control:
            keys = [(0x11, 0)] + keys + [(0x11, KEYEVENTF_KEYUP)]
        def batch(pairs):
            return (INPUT * len(pairs))(*[
                INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(key, 0, flags, 0, marker)))
                for key, flags in pairs
            ])
        events = batch(keys)
        sent = int(user32.SendInput(len(keys), events, ctypes.sizeof(INPUT)))
        if sent != len(keys):
            log.error("sendinput.failed sent=%s expected=%s cb_size=%s winerror=%s",
                      sent, len(keys), ctypes.sizeof(INPUT), ctypes.get_last_error())
            if 0 < sent < len(keys):
                # Release only keys Nexus pressed. Never synthesize Ctrl-up if
                # Ctrl belongs to the user. Prioritize Ctrl-up when it is ours,
                # then retry the still-unaccepted suffix (bounded, no sleeps).
                remaining = ([(0x11, KEYEVENTF_KEYUP)] if not held_control else []) + [(VK_V, KEYEVENTF_KEYUP)]
                for _ in range(3):
                    cleaned = int(user32.SendInput(len(remaining), batch(remaining), ctypes.sizeof(INPUT)))
                    remaining = remaining[min(max(cleaned, 0), len(remaining)):]
                    if not remaining:
                        break
                if remaining:
                    log.error("sendinput.release_unconfirmed key_count=%s manual_key_release_may_be_needed", len(remaining))
        return sent == len(keys)
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
            # What happened + what to do (H09). The content stays on the clipboard
            # whenever the write itself succeeded, so Ctrl+V is always the way out.
            PasteResult.TARGET_UNAVAILABLE:"No se pegó: la aplicación donde estabas ya no está abierta. Abre Nexus desde esa aplicación e inténtalo otra vez.",
            PasteResult.FOCUS_RESTORE_FAILED:"No se pegó: Windows no dejó volver a la aplicación anterior. El contenido ya está copiado: pulsa Ctrl+V allí.",
            PasteResult.CLIPBOARD_WRITE_FAILED:"No se pegó: otra aplicación está usando el portapapeles. Inténtalo de nuevo en un momento.",
            PasteResult.INPUT_ACCEPTED:"Windows aceptó Ctrl+V; Nexus no puede verificar que la aplicación destino insertara el contenido",
            PasteResult.INPUT_FAILED:"No se pegó: Windows bloqueó la pulsación (pasa con aplicaciones abiertas como administrador). El contenido ya está copiado: pulsa Ctrl+V.",
            PasteResult.ELEVATED_TARGET:"No se pegó solo: esa aplicación está abierta como administrador y Windows no deja que Nexus escriba en ella. El contenido ya está copiado: pulsa Ctrl+V.",
            PasteResult.CANCELLED:"Operación cancelada para conservar el portapapeles actual",
        }.get(result,"")
    def _finish(self,result, restore_overlay=True):
        # INPUT_ACCEPTED means SendInput accepted all key events, not that the
        # destination application acknowledged or inserted the clipboard data.
        ok = result is PasteResult.INPUT_ACCEPTED
        reason = self._reason(result)
        completion,overlay=self._completion,self._overlay; self._busy=False; self._completion=None; self._overlay=None
        elapsed=(time.perf_counter()-self._started_at)*1000 if self._started_at else 0
        log.info("paste.finish item=%s result=%s attempts=%s latency_ms=%.1f", self._item_id, result.value, self._attempts, elapsed)
        self._item_id=None; self._started_at=0.0
        if not ok:
            if restore_overlay and overlay is not None: overlay.show_and_focus()
            if self._on_paste_failed: self._on_paste_failed(reason)
        if completion: completion(ok)
        self.paste_finished.emit(ok,reason)
