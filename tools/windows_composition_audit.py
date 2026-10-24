"""Isolated real-Windows composition audit; no application startup or DB.

Only owned panel screen rectangles over an owned synthetic background are saved.
Native API acknowledgements are diagnostics, never visual acceptance.
"""
from __future__ import annotations

import ctypes
import argparse
from ctypes import wintypes
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import subprocess


def companion_main():
    """Own a separate application focus sink; never capture its pixels."""
    os.environ["QT_QPA_PLATFORM"] = "windows"
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    class FocusSink(QWidget):
        def closeEvent(self, event):
            super().closeEvent(event)
            app.quit()  # A Qt Tool window is not a normal last-primary-window.
    window = FocusSink()
    window.setWindowTitle("Owned composition focus sink")
    window.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                          Qt.WindowType.WindowStaysOnTopHint)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    area = app.primaryScreen().availableGeometry()
    window.setGeometry(area.x()+20, area.y()+20, 240, 80)
    window.setStyleSheet("background:#26364a;")
    window.show(); app.processEvents()
    print(json.dumps({"hwnd": int(window.winId()), "pid": os.getpid(), "parent_pid": os.getppid()}), flush=True)
    QTimer.singleShot(120000, app.quit)  # Fail-safe lifetime, no habitual process.
    app.exec()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", default="G0,G,H", help="Comma-separated isolated profiles")
    args = parser.parse_args()
    profiles = args.profiles.split(",")
    if not profiles or any(p not in ("G0", "G", "H") for p in profiles):
        parser.error("Only G0,G,H are authorized for this cross-process experiment")
    root = Path("C:/Users/Admin/AppData/Local/Temp/opencode")
    output = root / ("nexus-composition-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    output.mkdir(parents=True, exist_ok=False)
    # Redirect any import-time path resolution; never inspect existing settings.
    os.environ["LOCALAPPDATA"] = str(output / "sandbox")
    os.environ["QT_QPA_PLATFORM"] = "windows"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from nexus_linux import styles
    from nexus_linux.windows_design import palette
    from nexus_linux.windows_material import WindowsGlassSurface
    from nexus_linux.windows_backdrop import NativeBackend, handle_native_host_event
    from windows_nexus_material_probe import (
        SyntheticBackdrop, foreground_owned_window, put_owned_window_topmost,
        restore_foreground, user32, get_style, ENUMPROC,
    )

    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    def bounds(hwnd):
        r = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            raise ctypes.WinError()
        return [r.left, r.top, r.right, r.bottom]

    class Pane(QWidget):
        def __init__(self, profile):
            super().__init__()
            self.profile = profile
            self.nc_activation_messages = []
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
            self.setAutoFillBackground(False)
            self.surface = WindowsGlassSurface(self)
            self.surface.set_native_backdrop(True)

        def resizeEvent(self, event):
            self.surface.setGeometry(self.rect())
            super().resizeEvent(event)

        def nativeEvent(self, kind, message):
            if self.profile == "H" and kind == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0086:  # WM_NCACTIVATE, never veto real focus transfer.
                    self.nc_activation_messages.append(bool(msg.wParam))
                    return True, 1
            result = handle_native_host_event(self, kind, message)
            return result if result is not None else super().nativeEvent(kind, message)

        def paintEvent(self, event):
            p = QPainter(self)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self.rect(), QColor(0, 0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            if self.profile == "E":
                self.surface.paint_native_backdrop(p)
            elif self.profile != "A":
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                inset = 0 if self.profile in ("G", "G0", "H") else .5 if self.profile in ("F", "F0") else 8.5
                r = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
                path = QPainterPath()
                if self.profile in ("G", "G0", "H"):
                    path.addRect(r)  # DWM alone owns silhouette; no Qt rounded edge.
                else:
                    path.addRoundedRect(r, 22, 22)
                if self.profile in ("C", "D"):
                    for width, alpha in ((14, 7), (10, 12), (5, 24)):
                        p.setPen(QPen(QColor(0, 0, 0, alpha), width))
                        p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
                base = QColor(palette().panel)
                top = base.lighter(132); top.setAlpha(60)
                middle = QColor(base); middle.setAlpha(42)
                bottom = base.darker(112); bottom.setAlpha(50)
                gradient = QLinearGradient(r.topLeft(), r.bottomRight())
                for position, color in ((0, top), (.46, middle), (1, bottom)):
                    gradient.setColorAt(position, color)
                p.fillPath(path, gradient)
                if self.profile in ("D", "F", "F0"):
                    p.setPen(QPen(QColor(239, 246, 255, 55), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)
            p.end()

    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    styles._active_key = "liquid_glass"
    screen = app.primaryScreen()
    if screen is None or app.platformName() != "windows":
        raise RuntimeError("Real Windows desktop required")
    original = int(user32.GetForegroundWindow() or 0)
    report = {"output": str(output), "build": sys.getwindowsversion().build,
              "capture": "guarded real screen ROI, not QWidget.render", "profiles": [],
              "foreground_before": original, "database": False, "clipboard": False,
              "hooks": False, "shortcuts": False, "attribute33_touched": False}
    backend = NativeBackend()
    backdrop = SyntheticBackdrop(screen)
    # This owned background doubles as the inactive-state focus sink.
    backdrop.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)
    pane = None
    companion = None
    companion_pid = None
    sink_hwnd = None
    companion_stderr = None

    def foreground_pid():
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        return int(pid.value)

    def activation_evidence(active):
        foreground = int(user32.GetForegroundWindow() or 0)
        pid = foreground_pid()
        state = app.applicationState()
        if active:
            valid = foreground == int(pane.winId()) and pid == os.getpid() and pane.isActiveWindow() and state == Qt.ApplicationState.ApplicationActive
        else:
            valid = foreground == sink_hwnd and pid == companion_pid and pid != os.getpid() and not pane.isActiveWindow() and state == Qt.ApplicationState.ApplicationInactive
        if not valid:
            raise RuntimeError(f"Cross-process activation mismatch: pid={pid}, Qt={state.name}, pane_active={pane.isActiveWindow()}")
        return {"foreground_hwnd": foreground, "foreground_pid": pid, "pane_pid": os.getpid(),
                "qt_application_state": state.name, "qt_active": pane.isActiveWindow(),
                "panel_hwnd": int(pane.winId()), "active": active}

    def guard(target, roi):
        bg = int(backdrop.winId()); seen = []; blockers = []
        if sink_hwnd:
            r = bounds(sink_hwnd)
            if r[0] < roi[2] and r[2] > roi[0] and r[1] < roi[3] and r[3] > roi[1]:
                raise RuntimeError("Companion focus sink overlaps capture ROI")
        @ENUMPROC
        def visit(hwnd, unused):
            hwnd = int(hwnd)
            if hwnd == target:
                seen.append("panel")
            if hwnd == bg:
                seen.append("backdrop"); return False
            if user32.IsWindowVisible(hwnd) and hwnd != target:
                r = bounds(hwnd)
                if r[0] < roi[2] and r[2] > roi[0] and r[1] < roi[3] and r[3] > roi[1]:
                    blockers.append(hwnd)  # Reject even unexpected owned overlays.
            return True
        user32.EnumWindows(visit, 0)
        b = bounds(bg)
        covered = b[0] <= roi[0] and b[1] <= roi[1] and b[2] >= roi[2] and b[3] >= roi[3]
        expected = ["panel", "backdrop"] if target else ["backdrop"]
        if blockers or seen != expected or not covered:
            raise RuntimeError(f"Privacy guard refused capture: {seen}, blockers={blockers}, covered={covered}")
        return {"order": seen, "blockers": blockers, "backdrop_covers_roi": covered}

    def capture(name, target, roi, logical):
        proof = guard(target, roi)
        shot = screen.grabWindow(0, *logical)
        guard(target, roi)  # Discard if Z order changed during capture.
        if shot.isNull() or [shot.width(), shot.height()] != [roi[2]-roi[0], roi[3]-roi[1]]:
            raise RuntimeError("Physical/logical screenshot size mismatch")
        image = shot.toImage()
        if not image.save(str(output / (name + ".png"))):
            raise RuntimeError("Capture save failed")
        w, h = image.width(), image.height()
        corners = {"TL": (0, 0), "TR": (w-80, 0), "BL": (0, h-80), "BR": (w-80, h-80)}
        for corner, (x, y) in corners.items():
            image.copy(x, y, 80, 80).scaled(320, 320).save(str(output / f"{name}-{corner}.png"))
        edges = {"top": (0, 0, w, 16), "bottom": (0, h-16, w, 16),
                 "left": (0, 0, 16, h), "right": (w-16, 0, 16, h)}
        for edge, rect in edges.items():
            image.copy(*rect).scaled(rect[2]*4, rect[3]*4).save(str(output / f"{name}-edge-{edge}.png"))
        points = [(0, 0), (2, 2), (10, 10), (22, 0), (40, 4), (4, 40),
                  (w//2, h//2), (w//2+12, h//2), (w//2+24, h//2), (4, h//2), (12, h//2)]
        return image, {"file": name + ".png", "guard": proof, "native_roi": roi,
                       "logical_roi": logical, "dpr": shot.devicePixelRatio(),
                       "samples": {f"{x},{y}": image.pixelColor(x, y).name() for x, y in points}}

    def corner_scan(image, reference):
        """Exact observed footprint, including AA; not a fitted ideal radius."""
        w, h = image.width(), image.height()
        results = {}
        for key, flip_x, flip_y in (("TL", False, False), ("TR", True, False),
                                    ("BL", False, True), ("BR", True, True)):
            rows = []
            for y in range(32):
                row = ""
                for x in range(32):
                    px, py = (w-1-x if flip_x else x), (h-1-y if flip_y else y)
                    row += "." if image.pixelColor(px, py) == reference.pixelColor(px, py) else "#"
                rows.append(row)
            results[key] = {"mask": rows, "leading_background_pixels":
                            [len(row)-len(row.lstrip(".")) for row in rows]}
        return results

    try:
        backdrop.show(); put_owned_window_topmost(backdrop); QTest.qWait(150)
        companion_stderr = (output / "companion-stderr.txt").open("w", encoding="utf-8")
        companion = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--focus-companion"],
                                     stdout=subprocess.PIPE, stderr=companion_stderr, text=True)
        ready = json.loads(companion.stdout.readline())
        sink_hwnd = int(ready["hwnd"])
        sink_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(sink_hwnd, ctypes.byref(sink_pid))
        report["companion_handshake"] = {**ready, "launcher_pid": companion.pid, "hwnd_pid": sink_pid.value}
        # Windows venv python.exe may be a redirector that spawns the interpreter.
        # Corroborate parentage through a native process snapshot, not names/titles.
        class ProcessEntry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                        ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                        ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                        ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR*260)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for name in ("Process32FirstW", "Process32NextW"):
            getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError()
        parents = {}
        try:
            entry = ProcessEntry(); entry.dwSize = ctypes.sizeof(entry)
            exists = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while exists:
                parents[int(entry.pid)] = int(entry.parent)
                exists = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        lineage = [int(ready["pid"])]
        for _ in range(8):
            if lineage[-1] == companion.pid or not parents.get(lineage[-1]):
                break
            lineage.append(parents[lineage[-1]])
        report["companion_handshake"]["verified_lineage"] = lineage
        if companion.pid not in lineage or sink_pid.value != ready["pid"]:
            raise RuntimeError("Companion ownership mismatch")
        companion_pid = int(ready["pid"])
        report["companion"] = {**ready, "physical_bounds": bounds(sink_hwnd), "pane_pid": os.getpid()}
        area = screen.availableGeometry()
        geometry = [area.x()+max(60, (area.width()-640)//2), area.y()+max(100, (area.height()-360)//2), 640, 360]
        # F0 isolates full-client painting from the separate native-region change.
        for profile in profiles:
            pane = Pane(profile); pane.setGeometry(*geometry)
            hwnd = int(pane.winId())
            before = backend.api.window_style(hwnd)
            state = backend.apply(hwnd, "desktop_acrylic", backend.api.preferences(can_native_paint=True))
            if not state.native_enabled:
                raise RuntimeError("Native Acrylic not available: " + state.reason)
            pane.show(); app.processEvents(); QTest.qWait(150)
            border = None
            if profile in ("G", "G0", "H"):
                color = 0x635346 if profile == "G" else 0xFFFFFFFE
                hr = backend.api.set_attribute(hwnd, 34, color)
                border = {"requested_colorref": hex(color), "set_hresult": hr,
                          "readback": backend.api.get_attribute(hwnd, 34)}
                if hr < 0:
                    raise RuntimeError("Native border call failed")
            row = {"profile": profile, "native_state": asdict(state), "before": before,
                   "after": backend.api.window_style(hwnd), "qt_flags": int(pane.windowFlags()),
                   "qt_translucent": pane.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
                   "qt_no_system_background": pane.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground),
                   "region": None, "native_border": border, "captures": []}
            row["backdrop_readback_after_region"] = backend.api.get_attribute(hwnd, 38)
            report["profiles"].append(row)
            for phase in (0, 1):
                backdrop.phase = phase; backdrop.update()
                pane.hide()
                # Hiding an active pane may reactivate an unrelated prior window.
                # Focus our sink before calibration, never a habitual process.
                if not foreground_owned_window(backdrop):
                    raise RuntimeError("Could not focus owned calibration backdrop")
                put_owned_window_topmost(backdrop); app.processEvents(); QTest.qWait(180)
                roi = bounds(hwnd)
                reference, baseline = capture(f"{profile}-pattern{phase}-background", 0, roi, geometry)
                row.setdefault("backgrounds", []).append(baseline)
                padded_geometry = [geometry[0]-8, geometry[1]-8, geometry[2]+16, geometry[3]+16]
                dpr = baseline["dpr"]
                pad = round(8*dpr)
                padded_roi = [roi[0]-pad, roi[1]-pad, roi[2]+pad, roi[3]+pad]
                outside_reference, _ = capture(f"{profile}-pattern{phase}-background-perimeter", 0,
                                                padded_roi, padded_geometry)
                pane.show()
                for active in (True, False):
                    if not (foreground_owned_window(pane) if active else restore_foreground(sink_hwnd)):
                        raise RuntimeError("Could not establish owned focus state")
                    put_owned_window_topmost(backdrop); put_owned_window_topmost(pane)
                    app.processEvents(); QTest.qWait(260)
                    if (int(user32.GetForegroundWindow() or 0) == hwnd) != active:
                        raise RuntimeError("Foreground state mismatch")
                    name = f"{profile}-pattern{phase}-{'active' if active else 'inactive'}"
                    image, evidence = capture(name, hwnd, bounds(hwnd), geometry)
                    evidence["active"] = active; evidence["phase"] = phase
                    evidence["qt_active"] = pane.isActiveWindow()
                    evidence["foreground_hwnd"] = int(user32.GetForegroundWindow() or 0)
                    evidence["panel_hwnd"] = hwnd
                    evidence["nc_activation_messages"] = list(pane.nc_activation_messages)
                    evidence["native_styles"] = backend.api.window_style(hwnd)
                    evidence.update(activation_evidence(active))
                    if evidence["native_styles"]["layered"] or evidence["qt_active"] != active:
                        raise RuntimeError("Layered or activation mismatch at capture")
                    if (evidence["native_styles"]["style"] ^ before["style"]) & 0x80000000:
                        raise RuntimeError("WS_POPUP changed")
                    evidence["corner_scan_32_physical"] = corner_scan(image, reference)
                    exterior, perimeter = capture(name+"-perimeter", hwnd, padded_roi, padded_geometry)
                    # Exclude the HWND entirely. Some DWM shadows may extend outside it;
                    # count actual differences rather than assuming exterior is empty.
                    outside_count = different = 0
                    for y in range(exterior.height()):
                        for x in range(exterior.width()):
                            if x < pad or y < pad or x >= exterior.width()-pad or y >= exterior.height()-pad:
                                outside_count += 1
                                different += exterior.pixelColor(x, y) != outside_reference.pixelColor(x, y)
                    evidence["outside_hwnd"] = {"pixels": outside_count, "different_from_background": different,
                                                "perimeter_file": perimeter["file"]}
                    evidence["corner_equals_backdrop"] = {
                        key: image.pixelColor(x, y) == reference.pixelColor(x, y)
                        for key, (x, y) in {"TL": (2, 2), "TR": (image.width()-3, 2),
                                           "BL": (2, image.height()-3), "BR": (image.width()-3, image.height()-3)}.items()}
                    row["captures"].append(evidence)
            if profile in ("G0", "G", "H"):
                # Distinguish live inactive Acrylic from a frozen previous frame.
                # Do not hide/reactivate the pane while changing the background.
                row["inactive_hold"] = []
                for phase in (0, 1):
                    backdrop.phase = phase; backdrop.update()
                    app.processEvents(); QTest.qWait(350)
                    activation_evidence(False)
                    image, evidence = capture(f"{profile}-pattern{phase}-inactive-held", hwnd, bounds(hwnd), geometry)
                    evidence["foreground_hwnd"] = int(user32.GetForegroundWindow() or 0)
                    evidence["panel_hwnd"] = hwnd
                    evidence["qt_active"] = pane.isActiveWindow()
                    evidence["native_styles"] = backend.api.window_style(hwnd)
                    evidence["nc_activation_messages"] = list(pane.nc_activation_messages)
                    evidence.update(activation_evidence(False))
                    evidence["phase"] = phase
                    if evidence["native_styles"]["layered"]:
                        raise RuntimeError("Inactive held pane unexpectedly layered")
                    row["inactive_hold"].append(evidence)
            pane.hide(); pane.close(); pane.destroy(); pane = None
            app.processEvents()
    finally:
        if pane is not None:
            pane.hide(); pane.close(); pane.destroy()
        backdrop.hide(); backdrop.close(); backdrop.destroy(); app.processEvents()
        if companion is not None:
            if sink_hwnd:
                user32.PostMessageW(sink_hwnd, 0x0010, 0, 0)  # Close only the verified owned HWND.
            try:
                companion.wait(timeout=3)
                report["companion_forced_termination"] = False
            except subprocess.TimeoutExpired:
                companion.terminate(); companion.wait(timeout=3)
                report["companion_forced_termination"] = True
            report["companion_exit_code"] = companion.returncode
        if companion_stderr is not None:
            companion_stderr.close()
        report["foreground_restored"] = restore_foreground(original)
        report["foreground_after"] = int(user32.GetForegroundWindow() or 0)
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(str(output), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["--focus-companion"]:
        companion_main()
    else:
        main()
