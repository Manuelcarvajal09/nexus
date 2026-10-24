"""Deterministic DWM contract tests: no real native material or OS writes."""
import ctypes
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPlatformSurfaceEvent
from PySide6.QtWidgets import QWidget

from nexus_linux import windows_backdrop as backdrop


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.fail = {}
        self.readback = 3
        self.valid = True
        self.frameless_result = 0
        self.style = (backdrop.WS_POPUP | backdrop.WS_CAPTION | backdrop.WS_THICKFRAME
                      | backdrop.WS_SYSMENU | backdrop.WS_MINIMIZEBOX | backdrop.WS_MAXIMIZEBOX)

    def is_window(self, hwnd):
        return self.valid

    def window_style(self, hwnd):
        return {"style": self.style, "ex_style": 0, "layered": False}

    def prepare_frameless(self, hwnd):
        self.calls.append(("prepare_frameless", hwnd))
        self.style = ((self.style | backdrop.WS_CAPTION)
                      & ~(backdrop.WS_THICKFRAME | backdrop.WS_SYSMENU |
                          backdrop.WS_MINIMIZEBOX | backdrop.WS_MAXIMIZEBOX))
        return self.frameless_result

    def set_attribute(self, hwnd, attribute, value):
        self.calls.append(("set", hwnd, attribute, value))
        result = self.fail.get((attribute, value), 0)
        if isinstance(result, Exception):
            raise result
        return result

    def extend_frame(self, hwnd, margin):
        self.calls.append(("frame", hwnd, margin))
        return self.fail.get(("frame", margin), 0)

    def get_attribute(self, hwnd, attribute):
        self.calls.append(("get", hwnd, attribute))
        return self.fail.get("get", 0), self.readback

    def preferences(self, *, can_native_paint=False):
        return replace(eligible(), can_native_paint=can_native_paint)


def eligible():
    return backdrop.BackdropPreferences(22621, True, False, True, False, False, True)


def apply(api=None, prefs=None, request="desktop_acrylic", hwnd=123):
    api = api or FakeAPI()
    return backdrop.NativeBackend(api).apply(hwnd, request, prefs or eligible())


def assert_reset(api):
    assert api.calls[-2:] == [("set", 123, 38, 1), ("frame", 123, 0)]


def test_ack_enables_native_candidate_but_never_claims_runtime_visual_capture():
    api = FakeAPI()
    state = apply(api)
    assert state.applied == "desktop_acrylic"
    assert state.reason == "api_ack_visual_capture_required"
    assert state.native_enabled is True
    assert state.verified_visual is False
    assert state.readback == 3
    assert state.native_window_style["layered"] is False
    assert state.native_window_style["style"] & backdrop.WS_POPUP
    assert state.native_window_style["style"] & backdrop.WS_CAPTION
    assert not state.native_window_style["style"] & (backdrop.WS_SYSMENU | backdrop.WS_THICKFRAME)
    assert api.calls == [("prepare_frameless", 123), ("set", 123, 20, 1),
                         ("set", 123, 34, 0x635346), ("set", 123, 38, 3),
                         ("frame", 123, -1), ("get", 123, 38)]
    assert state.hresults == dict(frameless_style=0, dark=0, border=0, backdrop=0, frame=0, readback=0)


@pytest.mark.parametrize("name,value,reason", [
    ("os_build", 22000, "unsupported_build"), ("os_build", None, "unsupported_build"),
    ("composition", False, "composition_incompatible"),
    ("high_contrast", True, "high_contrast_incompatible"),
    ("transparency", False, "transparency_incompatible"),
    ("power_saver", True, "power_saver_incompatible"),
    ("remote_session", True, "remote_session_incompatible"),
    ("can_native_paint", False, "surface_not_probed"),
] + [(name, None, name + "_unknown") for name in
     ("composition", "high_contrast", "transparency", "power_saver", "remote_session")])
def test_preference_gates_fail_closed(name, value, reason):
    api = FakeAPI()
    state = apply(api, replace(eligible(), **{name: value}))
    assert state.applied == "satin" and state.reason == reason
    assert not state.verified_visual
    assert_reset(api)
    assert len(api.calls) == 5


def test_default_preferences_require_probe_and_satin_explicitly_clears_material():
    assert not backdrop.BackdropPreferences().can_native_paint
    api = FakeAPI()
    state = apply(api, request="satin")
    assert state.requested == "satin" and state.reason == "satin_requested"
    assert_reset(api)


@pytest.mark.parametrize("failure,reason", [
    ((20, 1), "dark_failed"),
    ((34, 0x635346), "border_failed"), ((38, 3), "backdrop_failed"),
    (("frame", -1), "frame_failed"), ("get", "readback_failed"),
])
@pytest.mark.parametrize("hr", [0x80070057, -2147024809, ctypes.c_long(-2147024809)])
def test_every_failed_hresult_is_signed_and_rolls_back(failure, reason, hr):
    api = FakeAPI()
    api.fail[failure] = hr
    state = apply(api)
    assert state.applied == "satin" and state.reason == reason
    assert -2147024809 in state.hresults.values()
    assert_reset(api)


def test_mismatch_and_failed_reset_remain_observable():
    api = FakeAPI()
    api.readback = 0
    api.fail[(38, 1)] = 0x80070057
    api.fail[("frame", 0)] = 0x80070057
    state = apply(api)
    assert state.reason == "readback_mismatch" and state.readback == 0
    assert state.hresults["reset_backdrop"] < 0
    assert state.hresults["reset_frame"] < 0
    assert not state.verified_visual
    assert_reset(api)


def test_exceptions_are_redacted_and_rollback_is_attempted():
    api = FakeAPI()
    api.fail[(38, 3)] = OSError("private file/user information")
    state = apply(api)
    assert state.reason == "backdrop_failed"
    assert "private" not in repr(state)
    assert_reset(api)


@pytest.mark.parametrize("hwnd,valid", [(0, True), (123, False)])
def test_invalid_window_never_calls_dwm(hwnd, valid):
    api = FakeAPI()
    api.valid = valid
    assert apply(api, hwnd=hwnd).reason == "invalid_window"
    assert not api.calls


def test_invalid_request_is_rejected_without_mutation():
    api = FakeAPI()
    with pytest.raises(ValueError, match="invalid_backdrop_request"):
        apply(api, request="auto")
    assert not api.calls


def test_success_hresult_need_not_be_exactly_zero():
    api = FakeAPI()
    api.fail[(38, 3)] = 1
    assert apply(api).applied == "desktop_acrylic"


def test_layered_native_window_fails_closed_and_restores_satin():
    api = FakeAPI()
    api.window_style = lambda hwnd: {"style": 0, "ex_style": 0x80000, "layered": True}
    state = apply(api)
    assert state.applied == "satin" and state.reason == "layered_surface_incompatible"
    assert state.verified_visual is False
    assert_reset(api)


def test_native_window_frame_setup_failure_falls_back_without_dwm_acrylic():
    api = FakeAPI()
    api.frameless_result = -2147024809
    state = apply(api)
    assert state.applied == "satin" and state.reason == "frameless_style_failed"
    assert state.hresults["frameless_style"] < 0
    assert not any(call[0] == "set" and call[2] == 38 and call[3] == 3 for call in api.calls)
    assert [call[1:3] for call in api.calls if call[0] == "set"] == [
        (123, 20), (123, 34), (123, 38),
    ]
    assert_reset(api)


def test_api_ack_without_matching_surface_probe_is_not_visually_verified():
    api = FakeAPI()
    state = apply(api, prefs=replace(eligible(), can_native_paint=False))
    assert state.applied == "satin" and state.reason == "surface_not_probed"
    assert state.verified_visual is False


def test_ctypes_layouts_and_real_read_only_preferences():
    if backdrop.sys.platform != "win32":
        pytest.skip("Windows read-only wrappers")
    api = backdrop.CtypesBackdropAPI()
    prefs = api.preferences()
    assert prefs.os_build >= 0
    assert prefs.can_native_paint is False
    assert ctypes.sizeof(backdrop.MARGINS) == 16
    assert ctypes.sizeof(backdrop.SYSTEM_POWER_STATUS) == 12
    assert api.dwm.DwmSetWindowAttribute.restype is ctypes.c_long
    assert api.dwm.DwmGetWindowAttribute.restype is ctypes.c_long


def test_controller_does_not_create_native_window_and_deduplicates(qapp):
    window = QWidget()
    api = FakeAPI()
    states = []
    controller = backdrop.BackdropController(window, backend=backdrop.NativeBackend(api), on_state=states.append)
    try:
        assert not window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        for _ in range(5):
            controller.schedule()
        qapp.processEvents()
        assert not states and not api.calls
        assert not window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        with pytest.raises(ValueError, match="already_installed"):
            backdrop.BackdropController(window)
    finally:
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_native_host_configuration_is_precreation_and_nonlayered(qapp, monkeypatch):
    monkeypatch.setattr("nexus_linux.windows_backdrop.QGuiApplication.platformName", lambda: "windows")
    window = QWidget()
    try:
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        backdrop.configure_native_host(window)
        assert not window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        with pytest.raises(RuntimeError, match="before_winId"):
            window.winId(); backdrop.configure_native_host(window)
    finally:
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_nccalcsize_is_handled_and_native_settings_are_forwarded(qapp):
    import ctypes
    from ctypes import wintypes
    window = QWidget()
    class Controller:
        def __init__(self): self.messages = []
        def native_settings_changed(self, message): self.messages.append(message)
    controller = Controller(); window._backdrop_controller = controller
    message = wintypes.MSG(); message.message = 0x0083
    try:
        assert backdrop.handle_native_host_event(window, b"windows_generic_MSG", ctypes.addressof(message)) == (True, 0)
        message.message = 0x031E
        assert backdrop.handle_native_host_event(window, b"windows_generic_MSG", ctypes.addressof(message)) is None
        assert controller.messages == [0x0083, 0x031E]
        assert backdrop.handle_native_host_event(window, b"windows_dispatcher_MSG", ctypes.addressof(message)) is None
    finally:
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_controller_queued_default_gate_close_surface_and_no_reentrant_winid(qapp):
    if backdrop.sys.platform != "win32":
        pytest.skip("Windows controller")
    window = QWidget()
    api = FakeAPI()
    states = []
    controller = backdrop.BackdropController(window, backend=backdrop.NativeBackend(api), on_state=states.append)
    try:
        window.winId()  # test creates it explicitly, controller never does
        hwnd = int(window.internalWinId())
        for _ in range(5):
            controller.native_settings_changed(0x031A)
        assert not states
        qapp.processEvents()
        assert len(states) == 1 and states[0].reason == "surface_not_probed"
        assert int(window.internalWinId()) == hwnd
        states.clear()
        controller.native_settings_changed(0x001A)
        controller.eventFilter(window, QEvent(QEvent.Type.Close))
        qapp.processEvents()
        assert not states
        controller.eventFilter(window, QEvent(QEvent.Type.Show))
        controller.eventFilter(window, SimpleNamespace(
            type=lambda: QEvent.Type.PlatformSurface,
            surfaceEventType=lambda: QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed))
        qapp.processEvents()
        assert not states
        controller.eventFilter(window, SimpleNamespace(
            type=lambda: QEvent.Type.PlatformSurface,
            surfaceEventType=lambda: QPlatformSurfaceEvent.SurfaceEventType.SurfaceCreated))
        qapp.processEvents()
        assert len(states) == 1
        states.clear()
        controller.can_native_paint = lambda w: True
        original = api.set_attribute

        def reentrant(*args):
            controller.eventFilter(window, QEvent(QEvent.Type.WinIdChange))
            return original(*args)

        api.set_attribute = reentrant
        controller.schedule()
        qapp.processEvents()
        qapp.processEvents()
        assert len(states) == 1 and states[0].applied == "desktop_acrylic"
        assert not controller._timer.isActive()
    finally:
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_controller_queued_work_is_destroy_safe(qapp):
    window = QWidget()
    api = FakeAPI()
    states = []
    controller = backdrop.BackdropController(window, backend=backdrop.NativeBackend(api), on_state=states.append)
    controller.schedule()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert not states and not api.calls


def test_diagnostic_error_resets_valid_window_without_exposing_error():
    api = FakeAPI()

    def fail(hwnd):
        raise OSError("private diagnostic")

    api.window_style = fail
    state = apply(api)
    assert state.reason == "window_diagnostics_unavailable"
    assert "private" not in repr(state)
    assert_reset(api)


@pytest.mark.parametrize("values,expected", [
    ([None, None], None), ([None, 1], True), ([1, None], True),
    ([1, 1], True), ([0, 1], False), ([1, 0], False), ([None, 0], False),
    ([2, 1], None), ([OSError("private"), 1], None),
])
def test_transparency_registry_reads_only_and_conflicts_fail_closed(monkeypatch, values, expected):
    import sys
    from contextlib import nullcontext

    paths = []

    def open_key(root, path, reserved, access):
        assert root == "HKCU" and reserved == 0 and access == 0x20019
        paths.append(path)
        value = values[len(paths) - 1]
        if value is None:
            raise FileNotFoundError()
        if isinstance(value, Exception):
            raise value
        return nullcontext(value)

    def query(value, name):
        assert name == "EnableTransparency"
        return value, 4

    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        HKEY_CURRENT_USER="HKCU", KEY_READ=0x20019, REG_DWORD=4,
        OpenKey=open_key, QueryValueEx=query))
    assert backdrop.CtypesBackdropAPI._transparency() is expected
    assert all(path.startswith("Software\\Microsoft\\Windows\\") for path in paths)


def test_controller_preferences_exception_resets_and_callback_cannot_self_queue(qapp):
    if backdrop.sys.platform != "win32":
        pytest.skip("Windows controller")
    window = QWidget()
    api = FakeAPI()

    def fail(**kwargs):
        raise OSError("private preference")

    api.preferences = fail
    states = []

    def changed(state):
        states.append(state)
        controller.eventFilter(window, QEvent(QEvent.Type.WinIdChange))

    controller = backdrop.BackdropController(window, backend=backdrop.NativeBackend(api), on_state=changed)
    try:
        window.winId()
        qapp.processEvents()
        qapp.processEvents()
        assert len(states) == 1
        assert states[0].applied == "satin" and not states[0].verified_visual
        assert "private" not in repr(states[0])
        hwnd = int(window.internalWinId())
        assert api.calls == [("prepare_frameless", hwnd), ("set", hwnd, 20, 0),
                             ("set", hwnd, 34, 0xFFFFFFFF),
                             ("set", hwnd, 38, 1), ("frame", hwnd, 0)]
        assert not controller._timer.isActive()
    finally:
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_controller_is_inert_outside_windows(qapp, monkeypatch):
    monkeypatch.setattr(backdrop.sys, "platform", "linux")
    window = QWidget()
    api = FakeAPI()
    controller = backdrop.BackdropController(window, backend=backdrop.NativeBackend(api))
    try:
        controller.schedule()
        controller.native_settings_changed(0x031E)
        qapp.processEvents()
        assert not controller._timer.isActive()
        assert not window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        assert not api.calls
    finally:
        window.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
