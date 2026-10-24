"""Isolated synthetic Qt benchmark; no native clipboard/hotkeys/user database.

Run each variant in a fresh process with the same interpreter and idle duration.
This measures the panel, not total production Nexus or compositor performance.
"""
import argparse
import ctypes
import json
import os
import statistics
import sys
import time
from pathlib import Path
from ctypes import wintypes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from nexus_linux.storage import StorageService


class Counters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
            "PagefileUsage", "PeakPagefileUsage")
    ]


def memory_mib():
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.WorkingSetSize / 1024**2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--composition", action="store_true", help="Include the other eagerly-created Windows UI objects")
    parser.add_argument("--styled", action="store_true", help="Apply the corresponding application-wide visual system")
    parser.add_argument("--idle-seconds", type=float, default=10)
    args = parser.parse_args()
    if args.legacy:
        from nexus_linux.timeline_window import TimelineWindow as Panel
    else:
        from nexus_linux.windows_timeline import WindowsTimelineWindow as Panel
    app = QApplication([])
    if args.styled:
        if args.legacy:
            from nexus_linux import styles
            app.setStyleSheet(styles.qss())
        else:
            from nexus_linux import windows_design
            app.setFont(windows_design.font())
            app.setStyleSheet(windows_design.qss())
    storage = StorageService(Path(":memory:"))
    for i in range(1000):
        storage.capture(kind="text", text_content=f"Synthetic benchmark item {i}")
    class Paste:
        def paste_item(self, *args, **kwargs): return False
    panel = Panel(storage, Paste())
    auxiliaries = []
    if args.composition:
        if args.legacy:
            from nexus_linux.main_window import MainWindow
            from nexus_linux.stack import ClipStack
            from nexus_linux.stack_window import StackWindow
            auxiliaries = [MainWindow(storage, Paste()), StackWindow(ClipStack(storage, Paste()))]
        else:
            from nexus_linux.windows_stack import WindowsClipStack, WindowsStackWindow
            auxiliaries = [WindowsStackWindow(WindowsClipStack(storage, Paste()))]
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        panel.show_and_focus(); app.processEvents()
        timings.append((time.perf_counter() - start) * 1000)
        panel.hide(); app.processEvents()
    loop = QEventLoop()
    QTimer.singleShot(int(args.idle_seconds * 1000), loop.quit)
    wall = time.perf_counter(); cpu = time.process_time()
    loop.exec()
    cpu_seconds = time.process_time() - cpu
    wall_seconds = time.perf_counter() - wall
    result = {
        "variant": "legacy" if args.legacy else "corrected",
        "mode": "synthetic-offscreen-ui-composition" if args.composition else "synthetic-offscreen-panel-only", "items": 1000,
        "styled": args.styled,
        "working_set_mib": round(memory_mib(), 2),
        "idle_wall_seconds": round(wall_seconds, 3),
        "idle_cpu_seconds": round(cpu_seconds, 4),
        "idle_cpu_percent_one_core": round(cpu_seconds / wall_seconds * 100, 3),
        "open_median_ms": round(statistics.median(timings), 2),
        "open_max_ms": round(max(timings), 2),
    }
    for window in [panel, *auxiliaries]: window.close()
    storage.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
