"""Load Win32 DLLs only on Windows, so Windows modules stay importable everywhere.

The shared interface imports modules such as windows_platform at load time;
on Linux/macOS they get stand-ins that accept the usual argtypes/restype
declarations and raise OSError only if a Win32 function is actually called.
"""
from __future__ import annotations

import ctypes
import sys

IS_WINDOWS = sys.platform == "win32"
# Callback type for Win32 hooks (stdcall); the C calling convention elsewhere.
FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class _Function:
    def __init__(self, name: str):
        self.__name__ = name
        self.argtypes = None
        self.restype = None

    def __call__(self, *args, **kwargs):
        raise OSError(f"{self.__name__} is only available on Windows")


class _Library:
    def __init__(self, name: str):
        self._name = name
        self._functions: dict[str, _Function] = {}

    def __getattr__(self, attr: str) -> _Function:
        if attr.startswith("_"):
            raise AttributeError(attr)
        return self._functions.setdefault(attr, _Function(f"{self._name}.{attr}"))


def load(name: str):
    """ctypes.WinDLL(name, use_last_error=True) on Windows, a stand-in elsewhere."""
    return ctypes.WinDLL(name, use_last_error=True) if IS_WINDOWS else _Library(name)


def last_error() -> int:
    return ctypes.get_last_error() if IS_WINDOWS else 0
