"""Observe genuine Ctrl+V without stealing it from the target application."""
from __future__ import annotations
import ctypes, os, threading
from ctypes import wintypes
from PySide6.QtCore import QObject, Signal
from .windows_platform import NEXUS_INPUT_MARKER, foreground_window, window_process_id

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

class WindowsPasteWatcher(QObject):
    paste_detected = Signal()
    def __init__(self, parent=None):
        super().__init__(parent); self._thread=None; self._thread_id=0; self._hook=None; self._callback=None; self._paste_chord_active=False
    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._thread=threading.Thread(target=self._run,name="NexusPasteWatcher",daemon=True); self._thread.start()
    def _run(self):
        self._thread_id=int(kernel32.GetCurrentThreadId())
        @HOOKPROC
        def callback(code,wparam,lparam):
            if code>=0:
                event=ctypes.cast(lparam,ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                external=window_process_id(foreground_window()) != os.getpid()
                generated_by_nexus=int(event.dwExtraInfo)==NEXUS_INPUT_MARKER
                if int(wparam) in (WM_KEYDOWN,WM_SYSKEYDOWN) and event.vkCode==VK_V:
                    self._paste_chord_active=bool(user32.GetAsyncKeyState(VK_CONTROL)&0x8000) and not generated_by_nexus and external
                # Confirm only on V release, but remember Ctrl from keydown:
                # users may release Ctrl a fraction before V, in which case
                # querying GetAsyncKeyState here loses a perfectly valid paste.
                elif int(wparam) in (WM_KEYUP,WM_SYSKEYUP) and event.vkCode==VK_V and self._paste_chord_active:
                    self._paste_chord_active=False
                    self.paste_detected.emit()
            return user32.CallNextHookEx(self._hook,code,wparam,lparam)
        self._callback=callback
        self._hook=user32.SetWindowsHookExW(WH_KEYBOARD_LL,callback,None,0)
        message=wintypes.MSG()
        while self._hook and user32.GetMessageW(ctypes.byref(message),None,0,0)>0:
            user32.TranslateMessage(ctypes.byref(message)); user32.DispatchMessageW(ctypes.byref(message))
        if self._hook: user32.UnhookWindowsHookEx(self._hook)
        self._hook=None
    def stop(self):
        if self._thread_id: user32.PostThreadMessageW(self._thread_id,WM_QUIT,0,0)
