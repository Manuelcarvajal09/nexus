"""Opt-in production-path E2E: RegisterHotKey -> WH_KEYBOARD_LL -> real receiver.

Never calls Stack/paste internals. The normal Nexus must be stopped first.
Starts `python -m nexus_linux.windows_app` with LOCALAPPDATA pointing at an
isolated directory; all capture/consumption assertions read that temporary DB.
Input is synthetic with a NON-Nexus marker: NOT a physical-keyboard validation.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

# Resolve Windows application paths in the sandbox in BOTH processes, even
# when imported helper modules initialize paths/theme constants at import time.
SANDBOX_CREATED = None
if __name__ == "__main__" and "--output" in sys.argv:
    index = sys.argv.index("--output")
    if index + 1 < len(sys.argv):
        SANDBOX_CREATED = Path(sys.argv[index + 1]).resolve()
        SANDBOX_CREATED.mkdir(parents=True, exist_ok=False)
        os.environ["LOCALAPPDATA"] = str(SANDBOX_CREATED)

from PySide6.QtCore import QCoreApplication, QMimeData, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTextEdit, QWidget
from nexus_linux.windows_paste import INPUT, INPUTUNION, KEYBDINPUT, INPUT_KEYBOARD, KEYEVENTF_KEYUP
from nexus_linux.windows_platform import activate_foreground_window, foreground_window
from windows_stack_e2e import find_window, read_notepad_text, close_test_tab, powershell

u = ctypes.WinDLL("user32", use_last_error=True)
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
k.OpenMutexW.restype = wintypes.HANDLE
k.CloseHandle.argtypes = [wintypes.HANDLE]
u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
u.SendInput.restype = wintypes.UINT
u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.PostThreadMessageW.restype = wintypes.BOOL
u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
u.CreateWindowExW.restype = wintypes.HWND
u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u.SendMessageW.restype = ctypes.c_ssize_t
u.SetFocus.argtypes = [wintypes.HWND]
u.SetFocus.restype = wintypes.HWND
u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.IsWindowVisible.argtypes = [wintypes.HWND]
u.IsWindowVisible.restype = wintypes.BOOL
TEST_MARKER = 0x45453245
REPORT = {"input_origin": "SendInput NON-Nexus marker; NOT physical", "cases": []}
INJECTED_DOWN = set()


class MimeReceiver(QTextEdit):
    """Actual Qt paste receiver, records only synthetic fixture MIME metadata."""
    def __init__(self):
        super().__init__()
        self.received = []

    def insertFromMimeData(self, source):
        image = source.imageData() if source.hasImage() else None
        self.received.append({"image": source.hasImage(),
                              "image_size": (image.width(), image.height()) if hasattr(image, "width") else None,
                              "urls": [url.toLocalFile() for url in source.urls()],
                              "formats": list(source.formats())})
        super().insertFromMimeData(source)


def wait(check, label, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        result = check()
        if result:
            return result
        time.sleep(0.008)
    raise AssertionError(f"deadline: {label}")


def interval(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.003)


def send(vk, up=False):
    flags = (KEYEVENTF_KEYUP if up else 0) | (0x0001 if vk == 0xA3 else 0)
    data = INPUT(INPUT_KEYBOARD, INPUTUNION(ki=KEYBDINPUT(vk, 0, flags, 0, TEST_MARKER)))
    if u.SendInput(1, ctypes.byref(data), ctypes.sizeof(INPUT)) != 1:
        raise AssertionError("test input injection rejected")
    if up:
        INJECTED_DOWN.discard(vk)
    else:
        INJECTED_DOWN.add(vk)


def release_injected_keys():
    for vk in tuple(INJECTED_DOWN):
        try:
            send(vk, True)
        except Exception:
            REPORT.setdefault("cleanup_errors", []).append(f"key_release_{vk}")


def chord(letter, shift=False, hold=0.025):
    # Separate submissions ensure the real hook sees preceding modifier events.
    send(0x11)
    if shift:
        send(0x10)
    interval(0.012)
    send(ord(letter)); interval(hold); send(ord(letter), True)
    if shift:
        send(0x10, True)
    send(0x11, True)
    interval(0.012)


def passed(name, **details):
    REPORT["cases"].append({"name": name, "result": "PASS", **details})
    print("PASS", name, flush=True)


def normalized(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def select_fixture_document(hwnd, name):
    """Select/verify only our uniquely named tab before any input or discard."""
    name = name.replace("'", "''")
    script = f"""
    Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd})
    $cond=New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::TabItem)
    $tabs=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cond)
    $matches=@($tabs | Where-Object {{$_.Current.Name.Contains('{name}')}})
    if($matches.Count -ne 1){{throw 'unique fixture tab not found'}}
    $selection=$matches[0].GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
    $selection.Select()
    if(-not $selection.Current.IsSelected){{throw 'fixture tab not selected'}}
    $doccond=New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Document)
    $doc=$root.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$doccond)
    if(-not $doc){{throw 'fixture editor absent'}}
    $doc.SetFocus()
    'FIXTURE_SELECTED'
    """
    if powershell(script) != "FIXTURE_SELECTED":
        raise AssertionError("could not select/verify temporary Notepad document")


class Product:
    def __init__(self, root):
        self.root = root
        self.db = root / "Nexus/data/nexus.sqlite"
        self.proc = None
        self.stderr = None
        self.gui_pid = None

    def start(self):
        env = {**os.environ, "LOCALAPPDATA": str(self.root), "NEXUS_STACK_DIAGNOSTICS": "1",
               "NEXUS_OPEN_ON_START": "0", "QT_QPA_PLATFORM": "windows", "PYTHONUNBUFFERED": "1"}
        self.stderr = (self.root / "app-stderr.txt").open("ab")
        self.proc = subprocess.Popen([sys.executable, "-m", "nexus_linux.windows_app"],
            cwd=Path(__file__).resolve().parents[1], env=env,
            stdout=self.stderr, stderr=self.stderr, creationflags=0x08000000)
        wait(lambda: self.db.exists() and self.stack_hwnd(), "production composition startup", 20)
        if self.proc.poll() is not None:
            raise AssertionError("isolated product exited")
        pid = wintypes.DWORD(); u.GetWindowThreadProcessId(self.stack_hwnd(), ctypes.byref(pid))
        self.gui_pid = int(pid.value)
        if self.gui_pid != self.proc.pid:
            row = json.loads(powershell(
                f"Get-CimInstance Win32_Process -Filter 'ProcessId={self.gui_pid}' | "
                "Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"))
            if row["ParentProcessId"] != self.proc.pid:
                raise AssertionError("Stack HWND does not belong to isolated process/redirector")

    def query(self, sql, args=()):
        if not self.db.exists():
            return []
        try:
            with sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True, timeout=1) as c:
                return c.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []

    def stack_hwnd(self):
        rows = []
        cbtype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        @cbtype
        def enum(hwnd, _):
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, buf, len(buf))
            # Local test subprocess also has a pythonw/venv redirector; use its
            # unique temp DB ownership via no other Nexus instance running.
            if buf.value == "Nexus Stack":
                rows.append(int(hwnd))
            return True
        u.EnumWindows(enum, 0)
        return rows[0] if len(rows) == 1 else 0

    def count(self):
        return self.query("SELECT COUNT(*) FROM stack_state")[0][0]

    def copy(self, text):
        digest = hashlib.sha256(b"text" + text.encode("utf-8")).hexdigest()
        QApplication.clipboard().setText(text)
        wait(lambda: self.query("SELECT id FROM clip_items WHERE content_hash=?", (digest,)), "History external capture")
        return self.query("SELECT id FROM clip_items WHERE content_hash=?", (digest,))[0][0]

    def activate(self, hwnd):
        if not activate_foreground_window(hwnd):
            raise AssertionError("fixture could not take foreground")
        chord("C", shift=True)
        wait(lambda: u.IsWindowVisible(self.stack_hwnd()), "actual RegisterHotKey opens Stack")
        interval(0.12)

    def deactivate(self, target):
        h = self.stack_hwnd()
        if not u.IsWindowVisible(h):
            self.activate(target)
        # Same habitual toggle closes/deactivates a visible Stack panel.
        chord("C", shift=True)
        wait(lambda: not u.IsWindowVisible(self.stack_hwnd()), "actual hotkey deactivation")

    def stop(self):
        if self.proc is None:
            return
        h = self.stack_hwnd()
        if h:
            pid = wintypes.DWORD(); u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if self.gui_pid is not None and int(pid.value) != self.gui_pid:
                raise RuntimeError("Refusing shutdown of unexpected Nexus window")
            tid = u.GetWindowThreadProcessId(h, None)
            if not u.PostThreadMessageW(tid, 0x0012, 0, 0):
                raise RuntimeError("isolated product graceful quit failed")
        self.proc.wait(timeout=15)
        self.stderr.close()
        self.proc = None
        self.gui_pid = None


def native_edit(host):
    hwnd = u.CreateWindowExW(0, "EDIT", "", 0x50010004, 8, 8, 650, 280,
                            int(host.winId()), None, None, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(hwnd)


def edit_text(hwnd):
    n = u.SendMessageW(hwnd, 0x000E, 0, 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.SendMessageW(hwnd, 0x000D, n + 1, ctypes.cast(buf, ctypes.c_void_p).value)
    return buf.value


def run(args):
    mutex = k.OpenMutexW(0x00100000, False, "Local\\NexusWindowsErwin")
    if mutex:
        k.CloseHandle(mutex)
        raise SystemExit("REFUSED: normal Nexus must be stopped before using global clipboard")
    if ctypes.get_last_error() not in (0, 2):
        raise SystemExit("REFUSED: cannot establish absence of production mutex")
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    cb = QApplication.clipboard()
    old_hwnd = foreground_window()
    source = cb.mimeData()
    saved = [(fmt, bytes(source.data(fmt))) for fmt in source.formats()] if source else []
    image = cb.image().copy() if source and source.hasImage() else None
    old_text = source.text() if source and source.hasText() else None
    urls = list(source.urls()) if source and source.hasUrls() else None
    root = Path(args.output).resolve()
    if root != SANDBOX_CREATED:
        root.mkdir(parents=True, exist_ok=False)
    product = Product(root)
    note_name = "NexusHookE2E-" + uuid.uuid4().hex[:8] + ".txt"
    note = root / note_name; note.write_text("", encoding="utf-8")
    notepad = 0
    host = QWidget(); host.setWindowTitle("Nexus E2E disposable Win32 EDIT"); host.resize(680, 320)
    qt = QPlainTextEdit(); qt.setWindowTitle("Nexus E2E disposable Qt editor"); qt.resize(680, 320)
    mime_receiver = MimeReceiver(); mime_receiver.setWindowTitle("Nexus E2E disposable MIME receiver"); mime_receiver.resize(680, 320)
    try:
        host.show(); interval(0.05); edit = native_edit(host)
        initial = QImage(11, 7, QImage.Format.Format_ARGB32); initial.fill(0xFF572184)
        cb.setImage(initial); interval(0.04)
        product.start()
        assert product.count() == 0
        # All synthetic copies go only into the isolated production process.
        product.copy("INACTIVE_TEXT_" + uuid.uuid4().hex)
        pic = QImage(17, 13, QImage.Format.Format_ARGB32); pic.fill(0xFF2168A0); cb.setImage(pic)
        wait(lambda: product.query("SELECT id FROM clip_items WHERE kind='image'"), "History inactive image capture")
        assert product.count() == 0
        passed("startup_inactive_text_image_History_only")

        def sequence(label, target, reader, focus, lifo=False, fast=False):
            product.activate(target)
            vals = [f"{label}_{letter}_{i:03d}\n" for i, letter in enumerate("ABC", 1)]
            captured_ids = []
            for idx, val in enumerate(vals, 1):
                captured_ids.append(product.copy(val)); wait(lambda: product.count() == idx, "active capture count")
            assert [row[0] for row in product.query("SELECT item_id FROM stack_state ORDER BY position")] == captured_ids
            focus()
            ordered = list(reversed(vals)) if lifo else vals
            before = normalized(reader())
            if fast:
                for _ in vals:
                    chord("V", hold=0.004)
                wait(lambda: product.count() == 0, "rapid queue drains", 12)
                wait(lambda: normalized(reader()) == before + "".join(ordered), "rapid receiver exact sequence", 12)
            else:
                for idx, val in enumerate(ordered):
                    chord("V")
                    wait(lambda: product.count() == 2 - idx, "auto consumption")
                    expected_ids = captured_ids[:2 - idx] if lifo else captured_ids[idx + 1:]
                    assert [row[0] for row in product.query("SELECT item_id FROM stack_state ORDER BY position")] == expected_ids
                    expected = before + "".join(ordered[:idx + 1])
                    wait(lambda: normalized(reader()) == expected, "receiver exact item, no duplicate", 12)
            assert product.count() == 0
            assert product.query("SELECT COUNT(*) FROM clip_items WHERE id IN (?,?,?)", captured_ids) == [(3,)]
            # An active empty Stack must swallow rather than repeat last data.
            final = normalized(reader()); chord("V"); interval(0.25); assert normalized(reader()) == final
            passed(label, order="LIFO" if lifo else "FIFO", empty_suppressed=True,
                   exact_receiver=True, injected_via_global_hook=True)

        def focus_native():
            assert activate_foreground_window(int(host.winId())); u.SetFocus(edit); interval(0.03)
        sequence("WIN32_FIFO", int(host.winId()), lambda: edit_text(edit), focus_native)
        product.deactivate(int(host.winId()))
        n = product.count(); product.copy("AFTER_DEACTIVATE_" + uuid.uuid4().hex); assert product.count() == n
        focus_native(); before = edit_text(edit); chord("V")
        wait(lambda: edit_text(edit) != before, "deactivated native Ctrl+V")
        passed("deactivation_capture_stopped_native_paste_restored")

        subprocess.Popen(["notepad.exe", str(note)])
        notepad = wait(lambda: find_window(note_name), "temporary Notepad", 20)
        def focus_notepad():
            assert activate_foreground_window(notepad)
            select_fixture_document(notepad, note_name)
            assert foreground_window() == notepad
        sequence("NOTEPAD_FIFO", notepad, lambda: read_notepad_text(notepad),
                 focus_notepad)
        product.deactivate(notepad)
        # Restart: preserve one actual captured item and switch persisted order
        # only in this temporary fixture DB while the test product is stopped.
        recovery_text = "RECOVER_PENDING_" + uuid.uuid4().hex
        product.activate(notepad); saved_id = product.copy(recovery_text)
        wait(lambda: product.count() == 1, "pending persisted")
        product.stop(); product.start()
        assert product.count() == 1
        product.copy("AFTER_RESTART_INACTIVE_" + uuid.uuid4().hex)
        assert product.count() == 1
        passed("restart_pending_preserved_capture_inactive")
        assert product.query("SELECT item_id FROM stack_state ORDER BY position") == [(saved_id,)]
        product.activate(notepad); focus_notepad(); before_recovery = read_notepad_text(notepad); chord("V")
        wait(lambda: product.count() == 0, "recovered pending auto consumed")
        wait(lambda: read_notepad_text(notepad) == before_recovery + recovery_text, "recovered item inserted exactly once")
        product.deactivate(notepad); product.stop()
        with sqlite3.connect(product.db) as c:
            c.execute("INSERT OR REPLACE INTO stack_meta(key,value) VALUES('reversed_order','1')")
        product.start()
        sequence("NOTEPAD_LIFO", notepad, lambda: read_notepad_text(notepad),
                 focus_notepad, lifo=True)
        product.deactivate(notepad)
        qt.show(); qt.setFocus(); interval(0.05)
        sequence("QT_LIFO_RAPID", int(qt.winId()), qt.toPlainText,
                 lambda: (activate_foreground_window(int(qt.winId())), qt.setFocus(), interval(0.03)), lifo=True, fast=True)
        product.deactivate(int(qt.winId()))
        product.activate(int(qt.winId()))
        held_value = "HELD_V_ONCE_" + uuid.uuid4().hex + "\n"
        product.copy(held_value); wait(lambda: product.count() == 1, "held-key fixture capture")
        assert activate_foreground_window(int(qt.winId())); qt.setFocus(); interval(0.04)
        before_held = qt.toPlainText()
        send(0x11); interval(0.015); send(ord("V"))
        for _ in range(4):
            interval(0.015); send(ord("V"))  # emulate autorepeated V-down
        interval(0.05)
        assert qt.toPlainText() == before_held and product.count() == 1
        # Ctrl may be released before V; the keydown snapshot still identifies
        # one intent, and synthetic events must not create a recursive request.
        send(0x11, True); send(ord("V"), True)
        wait(lambda: product.count() == 0 and qt.toPlainText() == before_held + held_value,
             "one operation after autorepeat and reversed modifier release")
        passed("held_V_autorepeat_once_Ctrl_up_before_V")
        product.deactivate(int(qt.winId()))
        product.activate(int(qt.winId()))
        ctrl_values = ["RIGHT_CTRL_" + letter + "_" + uuid.uuid4().hex + "\n" for letter in "ABC"]
        for idx, value in enumerate(ctrl_values, 1):
            product.copy(value); wait(lambda: product.count() == idx, "right Ctrl queue fixture")
        assert activate_foreground_window(int(qt.winId())); qt.setFocus(); interval(0.04)
        before_ctrl = qt.toPlainText(); send(0xA3); interval(0.015)
        for _ in ctrl_values:
            send(ord("V")); interval(0.008); send(ord("V"), True); interval(0.012)
        # Keep Ctrl down beyond the old 1.5s cancellation boundary. The engine
        # must inject only V, never release a modifier owned by the user.
        interval(1.7)
        assert product.count() == 0 and u.GetAsyncKeyState(0xA3) & 0x8000
        send(0xA3, True)
        wait(lambda: product.count() == 0 and qt.toPlainText() == before_ctrl + "".join(reversed(ctrl_values)),
             "right Ctrl held across distinct V taps FIFO request queue", 12)
        passed("right_Ctrl_held_over_1_5s_three_distinct_V_releases_serial_exact_once")
        product.deactivate(int(qt.winId()))
        # The original V is swallowed before an asynchronous dispatch. Switching
        # to another destination before release must not consume/paste either.
        product.activate(int(qt.winId()))
        retry_text = "FOCUS_FAILURE_RETRY_" + uuid.uuid4().hex
        product.copy(retry_text)
        wait(lambda: product.count() == 1, "failure fixture captured")
        assert activate_foreground_window(int(qt.winId())); qt.setFocus(); interval(0.04)
        before_qt = qt.toPlainText(); before_edit = edit_text(edit)
        send(0x11); interval(0.012); send(ord("V")); interval(0.02)
        assert activate_foreground_window(int(host.winId())); u.SetFocus(edit)
        send(ord("V"), True); send(0x11, True); interval(0.5)
        assert product.count() == 1 and qt.toPlainText() == before_qt and edit_text(edit) == before_edit
        passed("focus_switch_detected_item_preserved_no_wrong_target_insertion")
        product.deactivate(int(host.winId()))
        # Pending failure fixture remains: consume via normal interception first.
        product.activate(int(host.winId())); focus_native(); before_retry = edit_text(edit); chord("V")
        wait(lambda: product.count() == 0, "failed item can retry normally")
        wait(lambda: edit_text(edit) == before_retry + retry_text, "failed item inserted on safe retry exactly once")
        product.deactivate(int(host.winId()))
        mime_receiver.show(); interval(0.05)
        product.activate(int(mime_receiver.winId()))
        image_history_before = len(product.query("SELECT id FROM clip_items WHERE kind='image'"))
        pic2 = QImage(31, 19, QImage.Format.Format_ARGB32); pic2.fill(0xFFA86132); cb.setImage(pic2)
        wait(lambda: product.count() == 1 and len(product.query("SELECT id FROM clip_items WHERE kind='image'")) > image_history_before,
             "active image collected")
        assert activate_foreground_window(int(mime_receiver.winId())); mime_receiver.setFocus(); interval(0.04); chord("V")
        wait(lambda: product.count() == 0 and mime_receiver.received, "real image Ctrl+V MIME reception")
        assert mime_receiver.received[-1]["image"] and mime_receiver.received[-1]["image_size"] == (31, 19)
        fixture_file = root / "synthetic-file.txt"; fixture_file.write_text("synthetic file fixture", encoding="utf-8")
        data = QMimeData(); data.setUrls([QUrl.fromLocalFile(str(fixture_file))]); cb.setMimeData(data)
        wait(lambda: product.count() == 1 and product.query("SELECT id FROM clip_items WHERE kind='file'"), "active file collected")
        assert activate_foreground_window(int(mime_receiver.winId())); mime_receiver.setFocus(); interval(0.04); chord("V")
        wait(lambda: product.count() == 0 and len(mime_receiver.received) == 2, "real file Ctrl+V MIME reception")
        # QUrl.toLocalFile uses forward slashes; pathlib's Windows string uses
        # backslashes. Compare filesystem identity, not serialization spelling.
        received_paths = [Path(path).resolve() for path in mime_receiver.received[-1]["urls"]]
        assert received_paths == [fixture_file.resolve()]
        passed("image_and_file_actual_Qt_MIME_receiver_native_formats")
        product.deactivate(int(mime_receiver.winId()))
        product.activate(int(mime_receiver.winId()))
        recovered_pic = QImage(23, 11, QImage.Format.Format_ARGB32); recovered_pic.fill(0xFF831650)
        cb.setImage(recovered_pic); wait(lambda: product.count() == 1, "pending image persisted")
        product.copy("PERSIST_TEXT_" + uuid.uuid4().hex); wait(lambda: product.count() == 2, "pending mixed queue")
        persisted = product.query("SELECT s.item_id,i.kind FROM stack_state s JOIN clip_items i ON i.id=s.item_id ORDER BY s.position")
        product.stop(); product.start()
        product.copy("INACTIVE_AFTER_MIXED_RESTART_" + uuid.uuid4().hex)
        assert product.query("SELECT s.item_id,i.kind FROM stack_state s JOIN clip_items i ON i.id=s.item_id ORDER BY s.position") == persisted
        passed("restart_mixed_image_text_order_and_IDs_preserved_inactive")
        assert product.query("PRAGMA integrity_check") == [("ok",)]
        assert product.query("SELECT COUNT(*) FROM pinboards")[0][0] == 0
        passed("temporary_database_integrity")
    finally:
        # Stop test History BEFORE restoring personal clipboard data.
        release_injected_keys()
        stopped = False
        try:
            product.stop()
            stopped = True
        except Exception as exc:
            REPORT.setdefault("cleanup_errors", []).append("test_stop_" + type(exc).__name__)
        try:
            if notepad and find_window(note_name):
                select_fixture_document(notepad, note_name)
                close_test_tab(notepad, note_name)
        except Exception as exc:
            REPORT["notepad_cleanup"] = type(exc).__name__
        try:
            qt.close(); host.close(); mime_receiver.close()
            if stopped:
                mime = QMimeData()
                for fmt, data in saved:
                    mime.setData(fmt, data)
                if old_text is not None: mime.setText(old_text)
                if urls is not None: mime.setUrls(urls)
                if image is not None: mime.setImageData(image)
                cb.setMimeData(mime); QCoreApplication.processEvents()
                restored = cb.mimeData()
                REPORT["clipboard_restore_exposed_formats"] = bool(restored is not None and all(bytes(restored.data(f)) == d for f,d in saved))
                # Make restored OLE data independent of process lifetime.
                ole = ctypes.OleDLL("ole32"); ole.OleFlushClipboard.restype = ctypes.c_long
                REPORT["ole_flush_hresult"] = int(ole.OleFlushClipboard())
            else:
                REPORT["clipboard_restore_exposed_formats"] = "DEFERRED: test monitor shutdown unverified"
        except Exception as exc:
            REPORT.setdefault("cleanup_errors", []).append("clipboard_restore_" + type(exc).__name__)
        try:
            REPORT["focus_restore"] = bool(activate_foreground_window(old_hwnd)) if old_hwnd else False
        except Exception as exc:
            REPORT.setdefault("cleanup_errors", []).append("focus_restore_" + type(exc).__name__)
        (root / "result.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
        print(json.dumps({k:v for k,v in REPORT.items() if k != "cases"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-global-clipboard", action="store_true", required=True)
    parser.add_argument("--output", required=True, help="New isolated test directory (must not exist)")
    run(parser.parse_args())
