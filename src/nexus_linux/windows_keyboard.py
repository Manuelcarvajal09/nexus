"""Intercept explicitly enabled external Ctrl+V; never touch Nexus input."""
from __future__ import annotations
import ctypes, logging, os, threading, time, uuid
from dataclasses import dataclass
from ctypes import wintypes
from PySide6.QtCore import QObject, Signal
from . import windows_sandbox
from .windows_platform import NEXUS_INPUT_MARKER, FocusTarget, capture_focus_target

WH_KEYBOARD_LL, WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP, WM_QUIT = 13, 0x0100, 0x0104, 0x0101, 0x0105, 0x0012
VK_CONTROL, VK_V, LLKHF_INJECTED = 0x11, 0x56, 0x10
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_longlong
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

MODIFIER_FRESH_S = 0.25   # how long a recorded modifier event outranks the async key state
HOOK_REFRESH_MS = 15000    # re-install the low-level hook this often (Windows drops slow hooks silently)
WM_TIMER = 0x0113


def modifier_down(recorded, physical):
    """Whether a modifier counts as held for the V event being decided.

    A recorded hook event is trusted only while fresh (the async state may lag
    behind it for a few ms). Afterwards the real key state wins: Windows does
    not deliver key-ups to the hook across Win+L, UAC or Ctrl+Alt+Del, and a
    stale "Win is down" used to make every later Ctrl+V look like Win+Ctrl+V,
    so the Stack silently stopped intercepting until Nexus restarted."""
    if recorded is None:
        return physical
    is_down, at = recorded
    if time.monotonic() - at < MODIFIER_FRESH_S:
        return is_down
    return physical


@dataclass(frozen=True)
class PasteRequest:
    event_id: str
    target: FocusTarget
    pressed_at: float


class WindowsPasteWatcher(QObject):
    paste_detected = Signal(object)
    hook_failed = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent); self._thread=None; self._thread_id=0; self._hook=None; self._callback=None; self._paste_chord_active=False
        self._stopping = threading.Event()
        self._paste_sequence = 0
        self._enabled = False
        self._request = None
        self._v_down = False
        self._modifiers = {}
    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
    def decide(self, message, key, marker=0, *, ctrl=False, other_modifier=False, target=None):
        """Bounded hook decision, also usable by native-free integration tests.

        True means suppress this V event. A held intercepted V stays suppressed
        even across pause; marked input cannot reset physical chord tracking.
        """
        if marker == NEXUS_INPUT_MARKER or key != VK_V:
            return False
        if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
            if self._v_down:
                return self._paste_chord_active
            self._v_down = True
            self._paste_chord_active = bool(self._enabled and ctrl and not other_modifier
                                            and target and target.process_id != os.getpid())
            if self._paste_chord_active:
                self._request = PasteRequest(uuid.uuid4().hex, target, time.perf_counter())
            return self._paste_chord_active
        if message in (WM_KEYUP, WM_SYSKEYUP):
            swallowed = self._paste_chord_active
            request = self._request
            self._v_down = self._paste_chord_active = False
            self._request = None
            if swallowed and self._enabled and request:
                self.paste_detected.emit(request)
            return swallowed
        return False
    def start(self, enabled=True):
        if not windows_sandbox.hook_allowed(): return
        if self._thread and self._thread.is_alive(): return
        self._stopping.clear()
        self.set_enabled(enabled)
        self._thread=threading.Thread(target=self._run,name="NexusPasteWatcher",daemon=True); self._thread.start()
    def _run(self):
        self._thread_id=int(kernel32.GetCurrentThreadId())
        @HOOKPROC
        def callback(code,wparam,lparam):
            if code>=0:
                event=ctypes.cast(lparam,ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                own = int(event.dwExtraInfo) == NEXUS_INPUT_MARKER
                key = int(event.vkCode)
                if not own and key in (0x11,0xA2,0xA3,0x10,0xA0,0xA1,0x12,0xA4,0xA5,0x5B,0x5C):
                    # Async state for the changing key is not yet updated in
                    # LowLevelKeyboardProc. Record prior physical key events.
                    self._modifiers[key] = (int(wparam) in (WM_KEYDOWN, WM_SYSKEYDOWN), time.monotonic())
                def down(keys):
                    return any(modifier_down(self._modifiers.get(k), bool(user32.GetAsyncKeyState(k)&0x8000)) for k in keys)
                if not own and event.vkCode == VK_V:
                    if self.decide(int(wparam), int(event.vkCode), int(event.dwExtraInfo),
                                   ctrl=down((0x11,0xA2,0xA3)),
                                   other_modifier=down((0x10,0xA0,0xA1,0x12,0xA4,0xA5,0x5B,0x5C)),
                                   target=capture_focus_target() if not self._v_down else None):
                        return 1
            return user32.CallNextHookEx(self._hook,code,wparam,lparam)
        self._callback=callback
        # Force creation of this thread's message queue before a concurrent
        # stop() can post WM_QUIT (PostThreadMessage otherwise fails).
        message=wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message),None,0,0,0)
        try:
            if self._stopping.is_set(): return
            self._hook=user32.SetWindowsHookExW(WH_KEYBOARD_LL,callback,None,0)
            if not self._hook:
                self.set_enabled(False)
                logging.getLogger("nexus.keyboard").warning("paste_watcher.hook_failed winerror=%s", ctypes.get_last_error())
                self.hook_failed.emit("Stack detenido: no se pudo registrar Ctrl+V")
            # Windows removes a low-level hook that once exceeded LowLevelHooksTimeout
            # (e.g. while the UI thread held the GIL) WITHOUT notifying anyone: the
            # thread keeps waiting and Ctrl+V is simply no longer seen. Re-install
            # periodically; the new hook goes in before the old one is removed.
            timer = user32.SetTimer(None, 0, HOOK_REFRESH_MS, None) if self._hook else 0
            while self._hook and not self._stopping.is_set() and user32.GetMessageW(ctypes.byref(message),None,0,0)>0:
                if message.message == WM_TIMER and message.hwnd is None:
                    self._refresh_hook(callback)
                    continue
                user32.TranslateMessage(ctypes.byref(message)); user32.DispatchMessageW(ctypes.byref(message))
            if timer:
                user32.KillTimer(None, timer)
        finally:
            unexpected_exit = bool(self._hook) and not self._stopping.is_set()
            if self._hook: user32.UnhookWindowsHookEx(self._hook)
            self._hook=None; self._thread_id=0; self._callback=None; self._paste_chord_active=False; self._paste_sequence=0
            self._v_down=False; self._request=None
            self._modifiers.clear()
            if unexpected_exit:
                self.set_enabled(False)
                self.hook_failed.emit("Stack detenido: el observador Ctrl+V se desconectó")
    def _refresh_hook(self, callback):
        if self._v_down or self._stopping.is_set():
            return   # never swap hooks in the middle of a chord
        fresh = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, None, 0)
        if not fresh:
            return   # keep the current hook; try again on the next tick
        old, self._hook = self._hook, fresh
        user32.UnhookWindowsHookEx(old)
    def stop(self):
        self.set_enabled(False)
        self._stopping.set()
        if self._thread_id: user32.PostThreadMessageW(self._thread_id,WM_QUIT,0,0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
