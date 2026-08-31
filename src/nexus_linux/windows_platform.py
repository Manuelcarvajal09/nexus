"""Focused Win32 primitives used by Nexus; no polling and no shell tools."""
from __future__ import annotations
import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WM_HOTKEY = 0x0312
WM_NCLBUTTONDOWN, HTCAPTION = 0x00A1, 2
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0002, 0x0004, 0x4000
VK_C, VK_V, SW_RESTORE = 0x43, 0x56, 9
# dwExtraInfo signature attached only to keyboard input synthesized by Nexus.
# The low-level Stack watcher ignores this exact value rather than rejecting
# every LLKHF_INJECTED event (which would also reject accessibility tools,
# remappers, on-screen keyboards and native test automation).
NEXUS_INPUT_MARKER = 0x4E45585553

class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND), ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]

@dataclass(frozen=True)
class FocusTarget:
    window: int
    focused_control: int
    thread_id: int
    process_id: int

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype = wintypes.BOOL
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]

def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)

def window_process_id(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)

def foreground_process_name() -> str:
    pid = window_process_id(foreground_window())
    if not pid: return ""
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle: return ""
    try:
        size = wintypes.DWORD(32768); buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)

def clipboard_requests_exclusion() -> bool:
    for name in ("ExcludeClipboardContentFromMonitorProcessing",):
        fmt = user32.RegisterClipboardFormatW(name)
        if fmt and user32.IsClipboardFormatAvailable(fmt): return True
    return False

def capture_focus_target() -> FocusTarget | None:
    hwnd = foreground_window()
    if not hwnd: return None
    pid = wintypes.DWORD()
    thread_id = int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    focused = int(info.hwndFocus or 0) if thread_id and user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)) else 0
    return FocusTarget(hwnd, focused, thread_id, int(pid.value))

def restore_focus_target(target: FocusTarget | None, nexus_pid: int) -> bool:
    if target is None or target.process_id == nexus_pid or not user32.IsWindow(target.window) or not user32.IsWindowVisible(target.window): return False
    current_thread = int(kernel32.GetCurrentThreadId())
    foreground = foreground_window()
    foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
    attached_target = bool(target.thread_id != current_thread and user32.AttachThreadInput(current_thread, target.thread_id, True))
    attached_foreground = bool(foreground_thread and foreground_thread not in (current_thread,target.thread_id) and user32.AttachThreadInput(current_thread, foreground_thread, True))
    try:
        if user32.IsIconic(target.window): user32.ShowWindow(target.window, SW_RESTORE)
        user32.SetForegroundWindow(target.window)
        if target.focused_control and user32.IsWindow(target.focused_control): user32.SetFocus(target.focused_control)
    finally:
        if attached_foreground: user32.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_target: user32.AttachThreadInput(current_thread, target.thread_id, False)
    return foreground_window() == target.window

def register_hotkey(hwnd: int, identifier: int, virtual_key: int) -> bool:
    return bool(user32.RegisterHotKey(hwnd, identifier, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, virtual_key))

def unregister_hotkey(hwnd: int, identifier: int) -> None:
    user32.UnregisterHotKey(hwnd, identifier)

def begin_native_window_drag(hwnd: int) -> bool:
    """Hand a frameless window move to Windows' native caption loop.

    Qt mouse-move delivery is not reliable once the pointer crosses from a
    label to another child widget.  HTCAPTION makes the OS move the window
    exactly like a normal title bar while preserving custom chrome and the
    always-on-top flag.
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    user32.ReleaseCapture()
    user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
    return True

def acquire_single_instance_mutex(name="Local\\NexusWindowsErwin"):
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle or ctypes.get_last_error() == 183:
        if handle: kernel32.CloseHandle(handle)
        return None
    return handle

def release_handle(handle) -> None:
    if handle: kernel32.CloseHandle(handle)
