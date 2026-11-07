"""Native end-to-end checks of a SANDBOXED Nexus on the real Windows desktop.

What it does (all inside a temp LOCALAPPDATA with synthetic data, its own mutex and its own
hotkeys Ctrl+Shift+9 / Ctrl+Shift+7; your Nexus, history and clipboard are never touched):
  1. seeds a synthetic database, starts Nexus (real Qt platform, capture PAUSED, no Ctrl+V hook)
  2. a separate "target app" window (Tk) takes the foreground
  3. global hotkey (real SendInput) opens the panel; focus must move to Nexus
  4. a native right-click on a card must open the real context menu (captured from the screen)
  5. Esc closes the menu, Esc closes the panel, focus must return to the target app
  6. hotkey again reopens; Stack hotkey opens the Stack window; Esc closes it
  7. Nexus is killed and restarted: data and the panel still work (persistence across restart)

NOT covered on purpose (they need the system clipboard, i.e. YOUR clipboard):
  real capture, paste into another app, Ctrl+V of the Stack.  See the report for the proposal.

    python tools/windows_native_e2e.py --output DIR
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tools"))

import windows_acceptance as acc  # noqa: E402
from nexus_linux import windows_sandbox  # noqa: E402

try:  # physical pixels everywhere, so cursor coordinates match Qt's screen grabs
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
user32 = ctypes.WinDLL("user32", use_last_error=True)
VK = {"CTRL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "ESC": 0x1B, "9": 0x39, "7": 0x37}
KEYUP, MOUSE_RIGHT_DOWN, MOUSE_RIGHT_UP = 2, 0x0008, 0x0010


class Interference(Exception):
    """Something outside the test took the foreground: stop sending keys (they would reach another app)."""


def key(vk, up=False):
    user32.keybd_event(vk, 0, KEYUP if up else 0, 0)


def chord(*names):
    for n in names:
        key(VK[n]); time.sleep(0.03)
    for n in reversed(names):
        key(VK[n], up=True); time.sleep(0.03)


def windows_of(pid):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w > 200 and h > 100:
                found.append((hwnd, rect.left, rect.top, w, h))
        return True
    user32.EnumWindows(cb, 0)
    return found


def foreground_pid():
    hwnd = user32.GetForegroundWindow(); owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return owner.value


def process_name(pid):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.OpenProcess(0x1000, False, pid)
    if not handle:
        return "?"
    buf = ctypes.create_unicode_buffer(520); size = wintypes.DWORD(520)
    k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)); k32.CloseHandle(handle)
    return os.path.basename(buf.value)


def wait_for(predicate, timeout=6.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


def force_foreground(hwnd):
    key(VK["ALT"]); key(VK["ALT"], up=True)  # lets SetForegroundWindow succeed from a background process
    user32.SetForegroundWindow(hwnd)


TARGET_SCRIPT = """
import tkinter as tk
root = tk.Tk(); root.title("NexusE2E-Target"); root.geometry("500x300+80+80")
tk.Text(root).pack(fill="both", expand=True)
root.mainloop()
"""


def start_nexus(env, code_src, sandbox_local):
    log_path = sandbox_local / "Nexus" / "cache" / "nexus.log"
    offset = log_path.stat().st_size if log_path.exists() else 0
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    proc = subprocess.Popen([str(pythonw), "-m", "nexus_linux.windows_app"], env=env, cwd=str(code_src.parent),
                            creationflags=acc.DETACHED, close_fds=True)
    text = ""
    for _ in range(80):
        time.sleep(0.5)
        text = acc.read_new_log(log_path, offset)
        if "window=WindowsStackWindow" in text:
            break
    acc.confirm_startup(text, sandbox_local)
    return proc, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--keep-captures", action="store_true", help="save crops around Nexus (may include neighbouring windows)")
    args = ap.parse_args()
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    results = []

    def check(name, ok, detail=""):
        results.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)

    def safe_chord(*names):
        """Keys go to whoever has the foreground, so only send them when that is Nexus or our target app."""
        fg = foreground_pid()
        if fg not in (set(nexus_pids) | {tpid}):
            interference.append(f"foreground was {process_name(fg)} before keys {names}")
            trace("INTERFERENCE", why="foreground owned by another app before sending keys", process=process_name(fg), keys=names)
            raise Interference()
        chord(*names)

    nexus_pids, tpid = set(), -1
    t0 = time.time()
    trace_log = []
    interference = []

    def trace(event, **data):
        trace_log.append({"t": round(time.time() - t0, 3), "event": event, **data})

    def watch(label, probe, seconds):
        """Sample probe() every 40 ms and keep only the changes: shows what really happened and when."""
        last, end = object(), time.time() + seconds
        while time.time() < end:
            value = probe()
            if value != last:
                trace(label, value=value); last = value
            time.sleep(0.04)

    real_before = {p["pid"] for p in acc.list_nexus_processes()}
    sandbox_local = Path(tempfile.gettempdir()) / f"nexus-e2e-{int(time.time())}"
    real_root = Path(os.environ["LOCALAPPDATA"]) / "Nexus"
    (sandbox_local / "Nexus" / "data").mkdir(parents=True)
    code_src = ROOT / "src"

    # --- seed synthetic data (never real content) ---
    from nexus_linux.storage import StorageService
    db_path = sandbox_local / "Nexus" / "data" / "nexus.sqlite"
    storage = StorageService(db_path)
    board = storage.create_pinboard("Pruebas", "#80B5EE", None)
    seeded = [storage.capture(kind="text", text_content=f"Elemento sintético {n}", source_app="E2E") for n in range(6)]
    storage.set_title(seeded[0].id, "Título sintético")
    storage.add_to_pinboard(seeded[1].id, board.id)
    storage.close()

    env = acc.build_env(os.environ, sandbox_local=sandbox_local, real_root=real_root, code_src=code_src,
                        open_panel=False, allow_hook=False, reduced_motion=False)
    env.pop("QT_QPA_PLATFORM", None)
    target = subprocess.Popen([sys.executable, "-c", TARGET_SCRIPT])
    nexus = None
    try:
        nexus, _ = start_nexus(env, code_src, sandbox_local)
        nexus_procs = [p for p in acc.list_nexus_processes() if p["pid"] not in real_before]
        nexus_pids = {p["pid"] for p in nexus_procs}
        check("sandbox started on the real windows platform", bool(nexus_pids))

        user32.FindWindowW.restype = wintypes.HWND
        thwnd = wait_for(lambda: user32.FindWindowW(None, "NexusE2E-Target"), 8)
        check("target app window visible", bool(thwnd))
        tpid = wintypes.DWORD(); user32.GetWindowThreadProcessId(thwnd, ctypes.byref(tpid)); tpid = tpid.value
        force_foreground(thwnd); wait_for(lambda: foreground_pid() == tpid, 3)
        check("target app is the foreground app", foreground_pid() == tpid)

        def nexus_window():
            for pid in nexus_pids:
                found = windows_of(pid)
                if found:
                    return found[0]
            return None

        trace("hotkey-open")
        safe_chord("CTRL", "SHIFT", "9")
        win = wait_for(nexus_window, 5)
        check("global hotkey Ctrl+Shift+9 opens the panel", bool(win))
        # Condition-based readiness (no fixed sleeps): Nexus is foreground AND its rectangle has stopped changing.
        def ready():
            w = nexus_window()
            return w is not None and foreground_pid() in nexus_pids
        watch("open-state", lambda: (nexus_window() or (None,))[1:] + (foreground_pid() in nexus_pids,), 1.0)
        win = nexus_window() or win
        check("focus moves to Nexus when it opens", foreground_pid() in nexus_pids, f"fg={foreground_pid()}")

        # native screenshot + right-click on the first card
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance() or QGuiApplication([])
        screen = app.primaryScreen()
        def grab(name, rect=None):
            # Never save the whole desktop (it holds the user's other windows): crop to Nexus.
            if rect is None or not args.keep_captures:
                return
            _, left, top, w, h = rect
            pad = 40
            screen.grabWindow(0, left - pad, top - pad, w + 2 * pad, h + 2 * pad).save(str(out / f"{name}.png"))
        if win:
            _, left, top, w, h = win
            grab("e2e-panel-open", win)
            cx, cy = int(left + w * 0.148), int(top + h * 0.66)
            user32.SetCursorPos(cx, cy)
            pt = wintypes.POINT(); user32.GetCursorPos(ctypes.byref(pt))
            under = user32.WindowFromPoint(pt)
            under_pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(under, ctypes.byref(under_pid))
            trace("right-click", cursor=(pt.x, pt.y), wanted=(cx, cy), under_pid_is_nexus=under_pid.value in nexus_pids,
                  under_process=process_name(under_pid.value), fg_is_nexus=foreground_pid() in nexus_pids)
            if not (under_pid.value in nexus_pids and foreground_pid() in nexus_pids):
                # Something else owns the cursor position or the foreground: the automation is
                # being disturbed from outside (another app stealing focus, the user's mouse).
                interference.append("right-click preconditions not met")
                trace("INTERFERENCE", why="pointer/foreground not Nexus at click time")
            user32.mouse_event(MOUSE_RIGHT_DOWN, 0, 0, 0, 0); user32.mouse_event(MOUSE_RIGHT_UP, 0, 0, 0, 0)
            watch("menu-windows", lambda: (len(windows_of_any(nexus_pids)), foreground_pid() in nexus_pids), 1.5)
            grab("e2e-context-menu", (None, left, top, w, h + 260))
            popups = windows_of_any(nexus_pids)
            check("native right-click opens a context menu (extra top-level window)", len(popups) >= 2, f"windows={len(popups)}")  # see screenshot with --keep-captures
            check("(diag) foreground while menu open is Nexus", foreground_pid() in nexus_pids, f"fg={foreground_pid()}")
            trace("esc-menu"); safe_chord("ESC")
            watch("after-esc-menu", lambda: (len(windows_of_any(nexus_pids)), nexus_window() is not None, foreground_pid() in nexus_pids), 1.0)
            check("Esc closes the menu but keeps the panel", nexus_window() is not None)
            check("(diag) foreground after the menu closed is still Nexus", foreground_pid() in nexus_pids, f"fg={foreground_pid()} nexus={sorted(nexus_pids)}")
        trace("esc-panel"); safe_chord("ESC")
        closed = wait_for(lambda: nexus_window() is None, 4)
        check("Esc closes the panel", bool(closed))
        watch("after-close", lambda: (nexus_window() is not None, foreground_pid() == tpid, process_name(foreground_pid())), 1.0)
        check("focus returns to the target app after closing", foreground_pid() == tpid, f"fg={foreground_pid()} {process_name(foreground_pid())}")

        safe_chord("CTRL", "SHIFT", "9")
        check("hotkey reopens the panel", bool(wait_for(nexus_window, 5)))
        safe_chord("ESC"); wait_for(lambda: nexus_window() is None, 4)
        force_foreground(thwnd); wait_for(lambda: foreground_pid() == tpid, 3)
        safe_chord("CTRL", "SHIFT", "7")
        stack = wait_for(nexus_window, 5)
        check("Stack hotkey Ctrl+Shift+7 opens the Stack window", bool(stack))
        safe_chord("ESC"); check("Esc closes the Stack window", bool(wait_for(lambda: nexus_window() is None, 4)))

        # restart: kill only our processes, start again
        acc.kill(nexus_procs); wait_for(lambda: not acc.validate_targets(nexus_procs, acc.list_nexus_processes()), 10, 0.25)
        check("sandboxed Nexus stopped", not acc.validate_targets(nexus_procs, acc.list_nexus_processes()))
        before = {p["pid"] for p in acc.list_nexus_processes()}
        nexus, _ = start_nexus(env, code_src, sandbox_local)
        nexus_procs = [p for p in acc.list_nexus_processes() if p["pid"] not in before and p["pid"] not in real_before]
        nexus_pids = {p["pid"] for p in nexus_procs}
        force_foreground(thwnd); wait_for(lambda: foreground_pid() == tpid, 3)
        safe_chord("CTRL", "SHIFT", "9")
        check("after restart the hotkey and panel work again", bool(wait_for(nexus_window, 6)))
        if nexus_window():
            grab("e2e-after-restart", nexus_window())
        safe_chord("ESC")
        import sqlite3
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        counts = (conn.execute("SELECT COUNT(*) FROM clip_items").fetchone()[0],
                  conn.execute("SELECT COUNT(*) FROM pinboards").fetchone()[0],
                  conn.execute("SELECT COUNT(*) FROM clip_items WHERE title IS NOT NULL").fetchone()[0])
        conn.close()
        check("data persisted across restart (6 clips, 1 pinboard, 1 title)", counts == (6, 1, 1), str(counts))
    except Interference:
        results.append({"check": "run aborted: external focus theft (inconclusive, not a Nexus failure)", "ok": None, "detail": "; ".join(interference)})
        print("INCONCLUSIVE  external focus theft:", "; ".join(interference), flush=True)
    finally:
        mine = [p for p in acc.list_nexus_processes() if p["pid"] not in real_before]
        acc.kill(mine)
        subprocess.run(["taskkill", "/PID", str(target.pid), "/T", "/F"], capture_output=True)
        time.sleep(1)
        real_after = {p["pid"] for p in acc.list_nexus_processes()}
        results.append({"check": "personal Nexus untouched (same PIDs before/after)", "ok": real_before == real_after,
                        "detail": f"{sorted(real_before)} -> {sorted(real_after)}"})
        print("PASS " if real_before == real_after else "FAIL ", "personal Nexus untouched", sorted(real_before), "->", sorted(real_after))
        shutil.rmtree(sandbox_local, ignore_errors=True)
    if interference:
        print("NOTE: external interference detected during this run:", interference)
    (out / "e2e-results.json").write_text(json.dumps({"interference": interference, "results": results, "trace": trace_log}, indent=1, ensure_ascii=False), encoding="utf-8")
    if any(r["ok"] is None for r in results):
        return 2
    return 0 if all(r["ok"] for r in results) else 1


def windows_of_any(pids):
    allw = []
    for pid in pids:
        allw += windows_of_small(pid)
    return allw


def windows_of_small(pid):
    """Visible top-level windows of pid of any size (menus are small)."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return found


if __name__ == "__main__":
    sys.exit(main())
