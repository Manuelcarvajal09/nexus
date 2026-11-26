"""Focused Win32 primitives used by Nexus; no polling and no shell tools."""
from __future__ import annotations
import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
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
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE   # 64-bit handle: the default int restype would truncate it
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

# Public Windows 11 DWM attributes.  The transient-window backdrop is the
# closest native Windows material to the blurred, content-aware control layer
# used by Liquid Glass; it is intentionally paired with Nexus's own optical
# rim/highlight composition in styles.py rather than presented as the design.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_SYSTEMBACKDROP_TYPE = 38
# The transparent Qt window paints the only rounded surface.  A native DWM
# corner radius produces a second, differently sized curve around it.
DWMWCP_DONOTROUND = 1
DWMSBT_AUTO, DWMSBT_TRANSIENTWINDOW = 0, 3
DWMWA_COLOR_DEFAULT, DWMWA_COLOR_NONE = 0xFFFFFFFF, 0xFFFFFFFE

class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", wintypes.DWORD),
        ("AnimationId", ctypes.c_int),
    ]

class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attrib", ctypes.c_int),
        ("pvData", ctypes.c_void_p),
        ("cbData", ctypes.c_size_t),
    ]

WCA_ACCENT_POLICY = 19
ACCENT_DISABLED, ACCENT_ENABLE_ACRYLICBLURBEHIND = 0, 4

def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)

def window_process_id(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)

def activate_foreground_window(hwnd: int) -> bool:
    """Complete an explicit Qt activation without changing global focus policy.

    RegisterHotKey activation can arrive on Nexus's dispatcher thread while
    foreground input belongs to another GUI thread. A brief AttachThreadInput
    allows SetForegroundWindow to fulfill the existing show-and-focus contract;
    focus is always detached in finally, and failure is reported, not forced.
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if foreground_window() == hwnd:
        return True
    current_thread = int(kernel32.GetCurrentThreadId())
    foreground = foreground_window()
    foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
    target_thread = int(user32.GetWindowThreadProcessId(hwnd, None))
    attached_target = bool(target_thread and target_thread != current_thread
                           and user32.AttachThreadInput(current_thread, target_thread, True))
    attached_foreground = bool(foreground_thread and foreground_thread not in
                               (current_thread, target_thread)
                               and user32.AttachThreadInput(current_thread, foreground_thread, True))
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)
    return foreground_window() == hwnd

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

SHELL_SURFACE_CLASSES = frozenset({"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                                   "TopLevelWindowForOverflowXamlIsland", "Progman", "WorkerW"})


def window_class_name(hwnd: int) -> str:
    if not hwnd: return ""
    buffer = ctypes.create_unicode_buffer(256)
    return buffer.value if user32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256) else ""


def is_shell_surface(hwnd: int) -> bool:
    """Taskbar, tray overflow and desktop: never a meaningful paste target.

    Opening Nexus from the tray icon makes the taskbar the foreground window;
    remembering it sent the later Ctrl+V into the taskbar (nothing pasted)."""
    return window_class_name(hwnd) in SHELL_SURFACE_CLASSES


def _dword_format(name: str) -> int | None:
    """Value of a 4-byte registered clipboard format, or None if absent/unreadable."""
    fmt = user32.RegisterClipboardFormatW(name)
    if not fmt or not user32.IsClipboardFormatAvailable(fmt):
        return None
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(fmt)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            if kernel32.GlobalSize(handle) < 4:
                return None
            return ctypes.c_uint32.from_address(pointer).value
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_requests_exclusion() -> bool:
    """Honour every opt-out Windows defines for clipboard monitors and history.

    Password managers and browsers mark secrets with one or more of these; the
    first one alone (the only one checked before) misses e.g. apps that only set
    CanIncludeInClipboardHistory=0, so their passwords were stored in history."""
    for name in ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore"):
        fmt = user32.RegisterClipboardFormatW(name)
        if fmt and user32.IsClipboardFormatAvailable(fmt): return True
    for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
        if _dword_format(name) == 0:
            return True
    return False

def clipboard_sequence() -> int:
    """Opaque Win32 version, including changes made by other processes."""
    return int(user32.GetClipboardSequenceNumber())

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

ERROR_HOTKEY_ALREADY_REGISTERED = 1409

def register_hotkey(hwnd: int, identifier: int, virtual_key: int, modifiers: int = MOD_CONTROL | MOD_SHIFT) -> bool:
    """`modifiers` use the RegisterHotKey flags (Alt=1, Ctrl=2, Shift=4, Win=8)."""
    return bool(user32.RegisterHotKey(hwnd, identifier, modifiers | MOD_NOREPEAT, virtual_key))

def last_hotkey_error() -> int:
    """Win32 error of the most recent failed register_hotkey on this thread."""
    return ctypes.get_last_error()

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

def apply_liquid_glass_backdrop(hwnd: int, enabled: bool) -> bool:
    """Enable native blur/material and rounded composition where supported.

    Unsupported Windows builds return an HRESULT; that is a safe no-op and
    the Qt-painted glass layers remain fully usable.
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    dark = ctypes.c_int(1)
    corner = ctypes.c_int(DWMWCP_DONOTROUND)
    border_color = ctypes.c_uint(DWMWA_COLOR_NONE if enabled else DWMWA_COLOR_DEFAULT)
    # Reset the system-selected material first: its dark-mode tint is much too
    # gray for Liquid Glass and destroys the colors that should pass through.
    backdrop = ctypes.c_int(DWMSBT_AUTO)
    dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark), ctypes.sizeof(dark))
    dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner))
    # Windows 11 otherwise adds its own small-radius outline around the HWND,
    # visibly crossing the larger Liquid Glass corner at the top left/right.
    dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_BORDER_COLOR, ctypes.byref(border_color), ctypes.sizeof(border_color)
    )
    dwm_result = dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
    )
    setter = getattr(user32, "SetWindowCompositionAttribute", None)
    if setter is None:
        return dwm_result == 0
    setter.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
    setter.restype = wintypes.BOOL
    # Do not apply acrylic to the complete HWND: it creates an extra
    # rectangular backing layer outside Nexus's rounded Qt panel.
    policy = ACCENTPOLICY(
        ACCENT_DISABLED,
        # No compositor border: Nexus paints its own rounded refractive rim.
        # AccentFlags=2 asks Windows for a second rectangular client-edge,
        # which is the duplicate outline visible around the rounded panel.
        0,
        0,
        0,
    )
    data = WINDOWCOMPOSITIONATTRIBDATA(
        WCA_ACCENT_POLICY, ctypes.cast(ctypes.byref(policy), ctypes.c_void_p), ctypes.sizeof(policy)
    )
    return bool(setter(hwnd, ctypes.byref(data)))
def acquire_single_instance_mutex(name=None):
    if name is None:
        from . import windows_sandbox
        name = windows_sandbox.mutex_name("Local\\NexusWindowsErwin")
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle or ctypes.get_last_error() == 183:
        if handle: kernel32.CloseHandle(handle)
        return None
    return handle

def release_handle(handle) -> None:
    if handle: kernel32.CloseHandle(handle)
