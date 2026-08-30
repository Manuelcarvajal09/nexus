"""Small, dependency-free Win32 bridge for Nexus's native Windows runtime."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_HOTKEY = 0x0312
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0002, 0x0004, 0x4000
VK_C, VK_V = 0x43, 0x56

user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def activate_window(hwnd: int) -> bool:
    return bool(hwnd and user32.SetForegroundWindow(wintypes.HWND(hwnd)))


def register_hotkey(hwnd: int, identifier: int, virtual_key: int) -> bool:
    return bool(user32.RegisterHotKey(wintypes.HWND(hwnd), identifier, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, virtual_key))


def unregister_hotkey(hwnd: int, identifier: int) -> None:
    user32.UnregisterHotKey(wintypes.HWND(hwnd), identifier)
