"""Optional "start Nexus with Windows".

* OFF by default. A fresh install never creates the entry; only the user's explicit choice does
  (Configuración › Inicio, setting ``autostart``). The legacy ``launch_at_login`` key was never read by
  any code and is deliberately ignored: old settings files carry it as ``true``.
* Per-user only (HKCU\\...\\Run). No administrator rights, no Startup-folder shortcut, no scheduled task.
* The entry launches exactly the interpreter that is running now (``pythonw.exe -m nexus_linux.windows_app``).
  On every start ``sync`` re-points an enabled entry at the running interpreter, so it follows upgrades and
  rollbacks; a disabled setting removes a stale entry we created.
* Never active in development sandbox mode (NEXUS_SANDBOX=1): a test instance must not leave anything behind.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import windows_sandbox

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Nexus"
MODULE = "nexus_linux.windows_app"


class AutostartError(RuntimeError):
    pass


class WinRunKey:
    """The real per-user Run key. Replaceable in tests."""

    def read(self, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                value, _kind = winreg.QueryValueEx(key, name)
                return str(value)
        except FileNotFoundError:
            return None

    def write(self, name, value):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            return True
        except FileNotFoundError:
            return False


def unavailable_reason():
    """Why the registry must not be touched here (None = fine)."""
    if windows_sandbox.active():
        return "autostart is disabled in development sandbox mode"
    if os.name != "nt":
        return "autostart is only available on Windows"
    return None


def default_registry():
    reason = unavailable_reason()
    if reason:
        raise AutostartError(reason)
    return WinRunKey()


def launch_command(executable=None) -> str:
    exe = Path(executable or sys.executable)
    if exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")      # no console window at login
    return f'"{exe}" -m {MODULE}'


def current_command(registry=None):
    return (registry or default_registry()).read(VALUE_NAME)


def is_enabled(registry=None) -> bool:
    return current_command(registry) is not None


def enable(registry=None, command=None) -> str:
    registry = registry or default_registry()
    command = command or launch_command()
    registry.write(VALUE_NAME, command)
    return command


def disable(registry=None) -> bool:
    return (registry or default_registry()).delete(VALUE_NAME)


def is_ours(command) -> bool:
    return bool(command) and MODULE in command


def sync(enabled: bool, registry=None, command=None) -> str:
    """Startup check. Only ever keeps an ENABLED entry pointing at this interpreter (follows upgrades and
    rollbacks); it never deletes anything: removing is an explicit user action (set_enabled(False))."""
    if not enabled:
        return "unchanged"
    try:
        registry = registry or default_registry()
    except AutostartError:
        return "skipped"
    existing = registry.read(VALUE_NAME)
    wanted = command or launch_command()
    if existing == wanted:
        return "unchanged"
    registry.write(VALUE_NAME, wanted)
    return "created" if existing is None else "re-pointed"


def set_enabled(enabled: bool, registry=None, command=None) -> str:
    """The user's explicit choice from Configuración."""
    registry = registry or default_registry()
    if enabled:
        enable(registry, command)
        return "created"
    existing = registry.read(VALUE_NAME)
    if existing is not None and is_ours(existing):
        registry.delete(VALUE_NAME)
        return "removed"
    return "unchanged"      # nothing of ours is there (another program's entry is never touched)
