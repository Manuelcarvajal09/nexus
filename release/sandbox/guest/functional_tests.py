"""Functional tests of an INSTALLED Nexus Windows RC, with SYNTHETIC data, inside Windows Sandbox only.

    functional_tests.py --results DIR --isolation-report DIR\\isolation.json [--dry-run]

THIS SCRIPT USES THE SYSTEM CLIPBOARD (it copies synthetic text/images and the app pastes them). It therefore
REFUSES to run unless ALL of these hold:
  * the Windows account is WDAGUtilityAccount (Windows Sandbox's own account)
  * the isolation report written by bootstrap.ps1 exists and says all_ok = true
--dry-run lists the tests and checks the guards without touching input, clipboard or processes.

Nothing here is a claim of success: every test records PASS / FAIL / BLOCKED with evidence in
functional_results.json. A test that cannot run is BLOCKED, never PASS.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sqlite3
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

TESTS = [
    ("T01", "Captura de texto copiado"),
    ("T02", "Captura de imagen sintética"),
    ("T03", "Manejo de duplicados"),
    ("T04", "Atajo global abre el panel y Esc lo cierra"),
    ("T05", "Recuperación del historial (búsqueda) y pegado en otra aplicación"),
    ("T06", "Comportamiento del foco tras pegar y tras cerrar"),
    ("T07", "Ctrl+V del Stack (orden FIFO, tres elementos)"),
    ("T08", "Persistencia tras reiniciar Nexus"),
    ("T09", "Recuperación: conflicto de atajo global al arrancar"),
    ("T10", "Recuperación: cierre forzado y reinicio (integridad de la base)"),
    ("T11", "Inicio con Windows: desactivado en instalación nueva; se activa y desactiva (registro HKCU real del sandbox)"),
    ("T12", "Cierre limpio desde el menú de la bandeja (salida ordenada, base cerrada, atajos liberados)"),
]


class Blocked(Exception):
    pass


def guard(isolation_report: Path) -> None:
    if os.environ.get("USERNAME") != "WDAGUtilityAccount":
        raise SystemExit("REFUSED: not running as WDAGUtilityAccount (Windows Sandbox). This script uses the clipboard.")
    try:
        report = json.loads(Path(isolation_report).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise SystemExit("REFUSED: isolation report missing or unreadable")
    if report.get("all_ok") is not True:
        raise SystemExit("REFUSED: the isolation checks did not all pass")


# --------------------------------------------------------------------------- native helpers
user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
KEYUP = 2
VK = {"CTRL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "ESC": 0x1B, "ENTER": 0x0D, "V": 0x56, "C": 0x43}


def key(vk, up=False):
    user32.keybd_event(vk, 0, KEYUP if up else 0, 0)


def chord(*names):
    for n in names:
        key(VK[n]); time.sleep(0.03)
    for n in reversed(names):
        key(VK[n], up=True); time.sleep(0.03)


def type_text(text):
    for ch in text.upper():
        vk = ord(ch)
        key(vk); time.sleep(0.02); key(vk, up=True); time.sleep(0.02)


def wait_for(predicate, timeout=8.0, step=0.1, pump=None):
    end = time.time() + timeout
    while time.time() < end:
        if pump:
            pump()
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


def foreground_pid():
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(owner))
    return owner.value


def force_foreground(hwnd):
    key(VK["ALT"]); key(VK["ALT"], up=True)
    user32.SetForegroundWindow(hwnd)


def visible_windows_of(pids):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.right - rect.left > 200 and rect.bottom - rect.top > 100:
                found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return found


def nexus_pids():
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*nexus_linux.windows_app*' } | "
              "ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60).stdout
    return {int(x) for x in out.split() if x.strip().isdigit()}


# --------------------------------------------------------------------------- the run
class Run:
    def __init__(self, results: Path):
        self.results = results
        self.data = Path(os.environ["LOCALAPPDATA"]) / "Nexus"
        self.db = self.data / "data" / "nexus.sqlite"
        self.log_path = self.data / "cache" / "nexus.log"
        self.outcomes = []
        self.target_file = results / "target.txt"
        self.target = None
        self.target_hwnd = 0
        self.target_pid = 0
        self.app = None
        self.clip = None

    # ---- environment
    def start_qt(self):
        from PySide6.QtGui import QGuiApplication
        self.app = QGuiApplication.instance() or QGuiApplication([])
        self.clip = self.app.clipboard()

    def pump(self):
        self.app.processEvents()

    def rows(self, where="1=1", args=()):
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        try:
            return con.execute(f"SELECT id, kind, text_content, image_data IS NOT NULL, updated_at FROM clip_items WHERE {where}", args).fetchall()
        finally:
            con.close()

    def start_nexus(self):
        state = json.loads(Path(r"C:\Nexus-RC1\current.json").read_text(encoding="utf-8-sig"))
        pythonw = Path(r"C:\Nexus-RC1\versions") / state["current"] / "venv" / "Scripts" / "pythonw.exe"
        env = {**os.environ, "NEXUS_OPEN_ON_START": "0"}
        before = nexus_pids()
        subprocess.Popen([str(pythonw), "-m", "nexus_linux.windows_app"], env=env, creationflags=0x00000008 | 0x00000200)
        if not wait_for(lambda: nexus_pids() - before, 40):
            raise Blocked("Nexus did not start")
        wait_for(lambda: self.log_path.exists() and "window=WindowsStackWindow" in self.log_path.read_text(encoding="utf-8", errors="replace"), 40)
        time.sleep(1.0)

    def kill_nexus(self, force=True):
        for pid in nexus_pids():
            subprocess.run(["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else []), capture_output=True)
        wait_for(lambda: not nexus_pids(), 15)

    def start_target(self):
        script = (
            "import tkinter as tk, pathlib\n"
            f"out = pathlib.Path(r'{self.target_file}')\n"
            "root = tk.Tk(); root.title('NexusFT-Target'); root.geometry('600x300+60+60')\n"
            "t = tk.Text(root); t.pack(fill='both', expand=True); t.focus_set()\n"
            "last = ['']\n"
            "def tick():\n"
            "    cur = t.get('1.0', 'end-1c')\n"
            "    if cur != last[0]:\n"
            "        out.write_text(cur, encoding='utf-8'); last[0] = cur\n"
            "    root.after(100, tick)\n"
            "tick(); root.mainloop()\n")
        self.target = subprocess.Popen([sys.executable, "-c", script])
        user32.FindWindowW.restype = wintypes.HWND
        self.target_hwnd = wait_for(lambda: user32.FindWindowW(None, "NexusFT-Target"), 15)
        if not self.target_hwnd:
            raise Blocked("target application did not start")
        owner = wintypes.DWORD(); user32.GetWindowThreadProcessId(self.target_hwnd, ctypes.byref(owner))
        self.target_pid = owner.value
        self.focus_target()

    def focus_target(self):
        force_foreground(self.target_hwnd)
        if not wait_for(lambda: foreground_pid() == self.target_pid, 5):
            raise Blocked("could not give the foreground to the target application")

    def target_text(self):
        return self.target_file.read_text(encoding="utf-8") if self.target_file.exists() else ""

    def copy_text(self, text):
        self.clip.setText(text)
        self.pump()

    def copy_synthetic_image(self):
        from PySide6.QtGui import QColor, QImage
        image = QImage(48, 48, QImage.Format.Format_ARGB32)
        image.fill(QColor(40, 120, 200))
        self.clip.setImage(image)
        self.pump()

    # ---- tests
    def record(self, tid, status, detail=""):
        title = dict(TESTS)[tid]
        self.outcomes.append({"id": tid, "title": title, "status": status, "detail": detail})
        print(f"{status:8} {tid} {title}  {detail}", flush=True)

    def run_all(self):
        self.start_qt()
        self.start_target()
        self.start_nexus()
        pids = nexus_pids()
        steps = [self.t01, self.t02, self.t03, self.t04, self.t05, self.t07, self.t08, self.t09, self.t10, self.t11, self.t12]
        for step in steps:
            tid = step.__name__.upper()
            try:
                step(pids)
            except Blocked as exc:
                self.record(tid, "BLOCKED", str(exc))
            except Exception as exc:  # a crash of the test itself is a result, not a pass
                self.record(tid, "FAIL", f"test error: {type(exc).__name__}: {exc}")
            pids = nexus_pids() or pids
        done = {o["id"] for o in self.outcomes}
        for tid, _title in TESTS:
            if tid not in done:
                self.record(tid, "BLOCKED", "not executed (an earlier step blocked)")
        return self.outcomes

    def t01(self, pids):
        self.copy_text("NXFT-TEXT-ALPHA")
        ok = wait_for(lambda: self.rows("text_content = ?", ("NXFT-TEXT-ALPHA",)), 8, pump=self.pump)
        self.record("T01", "PASS" if ok else "FAIL", f"rows={len(ok or [])}")

    def t02(self, pids):
        before = len(self.rows("kind = 'image'"))
        self.copy_synthetic_image()
        ok = wait_for(lambda: len(self.rows("kind = 'image'")) > before, 8, pump=self.pump)
        self.record("T02", "PASS" if ok else "FAIL", f"image rows {before} -> {len(self.rows('kind = ?', ('image',)))}")

    def t03(self, pids):
        first = self.rows("text_content = ?", ("NXFT-TEXT-ALPHA",))
        if not first:
            raise Blocked("T01 produced no row to duplicate")
        time.sleep(1.1)
        self.copy_text("NXFT-TEXT-OTHER"); wait_for(lambda: self.rows("text_content = ?", ("NXFT-TEXT-OTHER",)), 8, pump=self.pump)
        self.copy_text("NXFT-TEXT-ALPHA"); time.sleep(1.5); self.pump()
        again = self.rows("text_content = ?", ("NXFT-TEXT-ALPHA",))
        self.record("T03", "PASS" if len(again) == 1 and again[0][4] > first[0][4] else "FAIL",
                    f"rows={len(again)} updated_at moved={bool(again and again[0][4] > first[0][4])}")

    def open_panel(self, pids):
        self.focus_target()
        chord("CTRL", "SHIFT", "V")
        return wait_for(lambda: visible_windows_of(pids), 6) and wait_for(lambda: foreground_pid() in pids, 4)

    def t04(self, pids):
        opened = self.open_panel(pids)
        chord("ESC")
        closed = wait_for(lambda: not visible_windows_of(pids), 4)
        back = wait_for(lambda: foreground_pid() == self.target_pid, 4)
        self.record("T04", "PASS" if opened and closed and back else "FAIL", f"opened={bool(opened)} closed={bool(closed)} focus_back={bool(back)}")

    def t05(self, pids):
        before = self.target_text()
        if not self.open_panel(pids):
            raise Blocked("panel did not open")
        type_text("alpha")       # the search box has focus when the panel opens
        time.sleep(0.8)          # debounce of the search field is a UI contract, not a retry delay
        chord("ENTER")
        pasted = wait_for(lambda: "NXFT-TEXT-ALPHA" in self.target_text()[len(before):], 6)
        self.record("T05", "PASS" if pasted else "FAIL", f"target gained text={bool(pasted)}")
        back = wait_for(lambda: foreground_pid() == self.target_pid, 4)
        closed = wait_for(lambda: not visible_windows_of(pids), 4)
        self.record("T06", "PASS" if back and closed else "FAIL", f"focus_on_target={bool(back)} panel_closed={bool(closed)}")

    def t07(self, pids):
        self.focus_target()
        chord("CTRL", "SHIFT", "C")                       # Stack hotkey: start collecting
        if not wait_for(lambda: visible_windows_of(pids), 6):
            raise Blocked("Stack window did not open")
        self.focus_target()                               # the Stack stays visible; paste into the target
        for n in (1, 2, 3):
            self.copy_text(f"NXFT-STACK-{n}")
            if not wait_for(lambda n=n: self.rows("text_content = ?", (f"NXFT-STACK-{n}",)), 8, pump=self.pump):
                raise Blocked(f"copy {n} was not captured")
            time.sleep(0.4)
        before = self.target_text()
        for _ in range(3):
            length = len(self.target_text())
            self.focus_target()
            chord("CTRL", "V")
            if not wait_for(lambda: len(self.target_text()) > length, 6):
                break
        gained = self.target_text()[len(before):]
        order = [gained.find(f"NXFT-STACK-{n}") for n in (1, 2, 3)]
        self.record("T07", "PASS" if all(i >= 0 for i in order) and order == sorted(order) else "FAIL",
                    f"positions={order}")
        chord("ESC")

    def t08(self, pids):
        before = len(self.rows())
        self.kill_nexus(force=False)
        self.start_nexus()
        pids2 = nexus_pids()
        after = len(self.rows())
        opened = self.open_panel(pids2); chord("ESC")
        self.record("T08", "PASS" if after >= before and opened else "FAIL", f"rows {before} -> {after}, panel_opens={bool(opened)}")

    def t09(self, pids):
        self.kill_nexus()
        helper = subprocess.Popen([sys.executable, "-c",
            "import ctypes,time; u=ctypes.windll.user32; assert u.RegisterHotKey(None,1,0x4002|0x0004,0x56); time.sleep(120)"])
        try:
            time.sleep(1)
            self.start_nexus()
            alive = bool(nexus_pids())
            log = self.log_path.read_text(encoding="utf-8", errors="replace") if self.log_path.exists() else ""
            self.record("T09", "PASS" if alive else "FAIL", f"nexus_alive={alive} (a conflicting Ctrl+Shift+V was held by another process)")
        finally:
            helper.kill()

    def t10(self, pids):
        self.kill_nexus()                                   # taskkill /F: no clean shutdown
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        count = con.execute("SELECT COUNT(*) FROM clip_items").fetchone()[0]
        con.close()
        self.start_nexus()
        alive = bool(nexus_pids())
        self.record("T10", "PASS" if integrity == "ok" and alive else "FAIL", f"integrity={integrity} rows={count} restarted={alive}")


    # ---- autostart (real HKCU Run key of the SANDBOX profile)
    def run_key(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                return str(winreg.QueryValueEx(key, "Nexus")[0])
        except FileNotFoundError:
            return None

    def installed_python(self):
        state = json.loads(Path(r"C:\Nexus-RC1\current.json").read_text(encoding="utf-8-sig"))
        return Path(r"C:\Nexus-RC1\versions") / state["current"] / "venv" / "Scripts" / "python.exe"

    def t11(self, pids):
        absent_at_start = self.run_key() is None
        env = {k: v for k, v in os.environ.items() if k != "NEXUS_SANDBOX"}

        def call(code):
            return subprocess.run([str(self.installed_python()), "-c", code], capture_output=True, text=True, env=env, timeout=60)
        on = call("from nexus_linux import windows_autostart as a; print(a.set_enabled(True))")
        value = self.run_key()
        points_ok = bool(value) and "pythonw.exe" in value and "nexus_linux.windows_app" in value and r"C:\Nexus-RC1" in value
        off = call("from nexus_linux import windows_autostart as a; print(a.set_enabled(False))")
        absent_after = self.run_key() is None
        self.record("T11", "PASS" if absent_at_start and points_ok and absent_after else "FAIL",
                    f"absent_on_fresh_install={absent_at_start} enabled_points_at_installed_pythonw={points_ok} "
                    f"removed_on_disable={absent_after} (on={on.stdout.strip()} off={off.stdout.strip()})")

    # ---- clean quit from the tray menu (UI Automation; BLOCKED, never PASS, if the icon cannot be reached)
    def t12(self, pids):
        if not nexus_pids():
            self.start_nexus()
        pids = nexus_pids()
        script = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, 'Nexus')
$item = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
if (-not $item) { Write-Output 'NOTFOUND'; exit 3 }
$r = $item.Current.BoundingRectangle
Write-Output ("{0} {1}" -f [int]($r.X + $r.Width/2), [int]($r.Y + $r.Height/2))
"""
        found = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60)
        if "NOTFOUND" in found.stdout or not found.stdout.strip():
            raise Blocked("the Nexus tray icon is not reachable through UI Automation (it may be in the hidden-icons flyout)")
        x, y = (int(v) for v in found.stdout.split()[:2])
        user32.SetCursorPos(x, y)
        time.sleep(0.2)
        user32.mouse_event(0x0008, 0, 0, 0, 0)
        user32.mouse_event(0x0010, 0, 0, 0, 0)          # right click on the tray icon
        if not wait_for(lambda: len(visible_windows_of(pids)) >= 1, 4):
            raise Blocked("the tray menu did not open")
        key(0x26)
        key(0x26, up=True)                              # Up arrow: the last entry of the menu is "Salir de Nexus"
        time.sleep(0.2)
        chord("ENTER")
        gone = wait_for(lambda: not nexus_pids(), 15)
        leftovers = list(self.db.parent.glob("nexus.sqlite-*"))
        hotkey_free = bool(user32.RegisterHotKey(None, 99, 0x4002 | 0x0004, 0x56))
        if hotkey_free:
            user32.UnregisterHotKey(None, 99)
        self.record("T12", "PASS" if gone and not leftovers and hotkey_free else "FAIL",
                    f"process_exited={bool(gone)} no_wal_shm_left={not leftovers} global_hotkey_released={hotkey_free}")

    def finish(self):
        self.kill_nexus()
        if self.target:
            subprocess.run(["taskkill", "/PID", str(self.target.pid), "/T", "/F"], capture_output=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True)
    parser.add_argument("--isolation-report", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run:
        print("DRY RUN: no input, clipboard or process is touched. Tests that would run in the sandbox:")
        for tid, title in TESTS:
            print(f"  {tid} {title}")
        try:
            guard(Path(args.isolation_report))
            print("guards: would PASS here")
        except SystemExit as exc:
            print("guards: would refuse here ->", exc)
        return 0
    guard(Path(args.isolation_report))
    results = Path(args.results)
    run = Run(results)
    try:
        outcomes = run.run_all()
    finally:
        run.finish()
    (results / "functional_results.json").write_text(json.dumps(outcomes, indent=2, ensure_ascii=False), encoding="utf-8")
    failed = [o for o in outcomes if o["status"] != "PASS"]
    print(f"\n{len(outcomes) - len(failed)} PASS, {len(failed)} not passing (FAIL/BLOCKED)")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
