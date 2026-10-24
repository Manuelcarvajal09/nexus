"""Opt-in native Windows/Notepad Stack test; never run in normal pytest.

It temporarily uses the global clipboard and opens a uniquely named Notepad
document. Pass --allow-global-clipboard only after consenting to that test.
All clipboard formats exposed by Qt are snapshotted in memory and restored.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import os
import subprocess
import sys
import tempfile
import time
import uuid
from ctypes import wintypes
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("This opt-in integration test runs only on Windows.")

from PySide6.QtCore import QCoreApplication, QMimeData
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from nexus_linux import storage as storage_module
from nexus_linux.storage import StorageService
from nexus_linux.windows_paste import WindowsPasteService
from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow
from nexus_linux.windows_platform import activate_foreground_window, foreground_window

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, len(buf))
    return buf.value


def find_window(document_name: str) -> int:
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and document_name.lower() in title(hwnd).lower():
            found.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else 0


def pump_until(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        value = predicate()
        if value:
            return value
    raise TimeoutError("bounded Windows UI wait expired")


def wait_for_notepad_text(hwnd: int, expected: str, timeout=8.0) -> None:
    end = time.monotonic() + timeout
    actual = ""
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        try:
            actual = read_notepad_text(hwnd)
        except RuntimeError:
            time.sleep(0.05)
            continue
        if actual == expected:
            return
        time.sleep(0.05)
    raise TimeoutError(
        f"Notepad did not expose expected text (expected_chars={len(expected)}, observed_chars={len(actual)})"
    )


def powershell(script: str) -> str:
    wrapped = "$ErrorActionPreference='Stop'; try {\n" + script + "\n} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
    encoded = base64.b64encode(wrapped.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=8, creationflags=0x08000000,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(
            f"Notepad UI Automation probe failed exit={result.returncode}: "
            f"{detail[-1][:240] if detail else 'no diagnostic'}"
        )
    return result.stdout.strip()


def read_notepad_text(hwnd: int) -> str:
    script = f"""
    Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd})
    $cond=New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
      [System.Windows.Automation.ControlType]::Document)
    $doc=$root.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$cond)
    if(-not $doc){{throw 'Notepad document control not found'}}
    $pattern=$doc.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
    $text=$pattern.DocumentRange.GetText(-1)
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text))
    """
    encoded = powershell(script)
    return base64.b64decode(encoded).decode("utf-8")


def close_test_tab(hwnd: int, document_name: str) -> None:
    # Close only our uniquely named tab, discard only its synthetic contents,
    # and leave the user's existing Notepad window/tabs untouched.
    name = document_name.replace("'", "''")
    script = f"""
    Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd})
    function Find-Name($base,$scope,$names) {{
      foreach($n in $names){{$c=New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty,$n)
        $e=$base.FindFirst($scope,$c); if($e){{return $e}} }}
      return $null
    }}
    $file=Find-Name $root ([System.Windows.Automation.TreeScope]::Descendants) @('Archivo','File')
    if(-not $file){{throw 'File menu not found'}}
    $file.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    Start-Sleep -Milliseconds 100
    $close=Find-Name $root ([System.Windows.Automation.TreeScope]::Descendants) @('Cerrar pestaña','Close tab')
    if(-not $close){{throw 'Close-tab command not found'}}
    $close.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    Start-Sleep -Milliseconds 100
    $discard=Find-Name $root ([System.Windows.Automation.TreeScope]::Descendants) @('No guardar',"Don't save")
    if($discard){{$discard.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()}}
    Start-Sleep -Milliseconds 100
    $cond=New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
      [System.Windows.Automation.ControlType]::TabItem)
    $tabs=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cond)
    foreach($tab in $tabs){{if($tab.Current.Name.Contains('{name}')){{throw 'Test tab still exists'}}}}
    'TEST_TAB_CLOSED'
    """
    if powershell(script) != "TEST_TAB_CLOSED":
        raise RuntimeError("Could not verify test-only Notepad tab cleanup")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-global-clipboard", action="store_true")
    args = parser.parse_args()
    if not args.allow_global_clipboard:
        parser.error("explicit --allow-global-clipboard required")
    # A live Nexus monitor would capture these synthetic clipboard writes into
    # the user's real History. Fail closed instead of treating a temp test DB
    # as isolation from the system-wide clipboard.
    mutex = kernel32.OpenMutexW(0x00100000, False, "Local\\NexusWindowsErwin")
    if mutex:
        kernel32.CloseHandle(mutex)
        parser.error("Nexus is running; stop it or use an isolated Windows session before the real clipboard test")
    if ctypes.get_last_error() not in (0, 2):
        parser.error("Could not prove that the Nexus clipboard monitor is stopped")

    app = QApplication.instance() or QApplication([])
    clipboard = QGuiApplication.clipboard()
    original_foreground = int(user32.GetForegroundWindow() or 0)
    source = clipboard.mimeData()
    formats = list(source.formats()) if source else []
    raw = [(fmt, bytes(source.data(fmt))) for fmt in formats] if source else []
    text = source.text() if source and source.hasText() else None
    urls = list(source.urls()) if source and source.hasUrls() else None
    image = source.imageData() if source and source.hasImage() else None
    image = image.copy() if hasattr(image, "copy") else image

    def restore_clipboard() -> None:
        mime = QMimeData()
        for fmt, data in raw:
            mime.setData(fmt, data)
        if text is not None:
            mime.setText(text)
        if urls is not None:
            mime.setUrls(urls)
        if image is not None:
            mime.setImageData(image)
        clipboard.setMimeData(mime)
        QCoreApplication.processEvents()
        current = clipboard.mimeData()
        if raw and (current is None or any(bytes(current.data(fmt)) != data for fmt, data in raw)):
            raise RuntimeError("Could not verify restoration of all Qt-exposed clipboard formats")

    notepad_hwnd = 0
    window = storage = None
    try:
        with tempfile.TemporaryDirectory(prefix="nexus-stack-e2e-") as temp:
            document = Path(temp) / f"NexusStackE2E-{uuid.uuid4().hex[:8]}.txt"
            document.write_text("", encoding="utf-8")
            subprocess.Popen(["notepad.exe", str(document)])
            notepad_hwnd = pump_until(lambda: find_window(document.name), timeout=15.0)
            try:
                user32.ShowWindow(notepad_hwnd, 9)
                if not activate_foreground_window(notepad_hwnd):
                    raise RuntimeError("Could not activate disposable Notepad for the integration test")
                pump_until(lambda: foreground_window() == notepad_hwnd)
            except Exception:
                try:
                    close_test_tab(notepad_hwnd, document.name)
                except Exception:
                    print("notepad_test_tab_cleanup=UNVERIFIED")
                notepad_hwnd = 0
                raise
            try:
                # Use only a temporary SQLite DB; do not touch Nexus's live data.
                storage_module.paths.ensure_dirs = lambda: None
                storage = StorageService(db_path=Path(temp) / "stack.sqlite")
                paste = WindowsPasteService()
                paste.paste_finished.connect(
                    lambda accepted, reason: print(
                        f"paste_input_accepted={accepted} "
                        f"target_foreground_at_dispatch={foreground_window() == notepad_hwnd}"
                    )
                )
                target = paste.remember_target()
                if target is None or target.process_id == os.getpid():
                    raise RuntimeError("Could not capture disposable Notepad as target")
                focused_class = class_name(target.focused_control) if target.focused_control else ""
                if not any(part in focused_class for part in ("Edit", "RichEdit", "NotepadTextBox")):
                    raise RuntimeError("Notepad did not expose an editable focus control")
                print(f"notepad_focused_control_class={focused_class}")
                stack = WindowsClipStack(storage, paste)
                stack.start_collecting()
                values = ("Nexus A áéíóú — 😀|", "Nexus B ñ漢字 🚀|", "Nexus C café|")
                items = [storage.capture(kind="text", text_content=value) for value in values]
                for item in items:
                    stack.add(item, already_on_clipboard=True)
                window = WindowsStackWindow(stack)
                window.show()
                QCoreApplication.processEvents()

                expected = ""
                for position, (item, value) in enumerate(zip(items, values), start=1):
                    window.advance()
                    expected += value
                    wait_for_notepad_text(notepad_hwnd, expected)
                    pump_until(lambda: not any(row.id == item.id for row in stack.items()), timeout=5.0)
                    if window.isVisible():
                        raise AssertionError("Stack panel did not hide after the Notepad paste")
                    if position < len(items):
                        if not paste.remember_target():
                            raise AssertionError("Could not refresh the Notepad target after paste")
                        window.show_and_focus()
                        QCoreApplication.processEvents()
                        if not window.isVisible():
                            raise AssertionError("Hidden active Stack did not reopen")
                if stack.items() or read_notepad_text(notepad_hwnd) != "".join(values):
                    raise AssertionError("Notepad FIFO or final empty Stack verification failed")
                print("notepad_fifo=PASS unicode=PASS three_auto_consumed_pastes=PASS stack_empty=PASS")
            finally:
                if window is not None:
                    window.close()
                    window.deleteLater()
                    QCoreApplication.processEvents()
                    window = None
                if storage is not None:
                    storage.close()
                    storage = None
                if notepad_hwnd and find_window(document.name):
                    close_test_tab(notepad_hwnd, document.name)
                notepad_hwnd = 0
    finally:
        try:
            restore_clipboard()
            print(f"clipboard_restore=PASS exposed_formats={len(formats)}")
        except Exception as exc:
            print(f"clipboard_restore=FAIL exception_type={type(exc).__name__}")
            raise
        finally:
            if original_foreground and user32.IsWindow(original_foreground):
                restored = activate_foreground_window(original_foreground)
                print(f"foreground_restore={restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
