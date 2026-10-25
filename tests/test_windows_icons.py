"""The official Nexus icon: design, assets, taskbar identity and a console-free launcher."""
import inspect
import shutil
import struct
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QColor, QImage

from nexus_linux import windows_app, windows_design, windows_icons, windows_sandbox

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "nexus_linux" / "assets" / "icons"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def close_to(color: QColor, expected: str, tolerance=40) -> bool:
    other = QColor(expected)
    return all(abs(a - b) <= tolerance for a, b in zip(color.getRgb()[:3], other.getRgb()[:3]))


def count_pixels(image: QImage, expected: str, tolerance=40) -> int:
    return sum(1 for y in range(image.height()) for x in range(image.width())
               if image.pixelColor(x, y).alpha() > 200 and close_to(image.pixelColor(x, y), expected, tolerance))


# -- the design -----------------------------------------------------------

def test_brand_colors_are_the_liquid_glass_accent_and_panel():
    palette = windows_design.PALETTES["liquid_glass"]
    assert windows_icons.BRAND_ACCENT.lower() == palette.accent.lower()   # the light-blue outline
    assert windows_icons.BRAND_TILE.lower() == palette.panel.lower()


@pytest.mark.parametrize("size", windows_icons.SIZES)
def test_every_size_draws_the_light_blue_n_on_the_dark_tile(qapp, size):
    image = windows_icons.render_brand(size)
    assert (image.width(), image.height()) == (size, size)
    assert image.pixelColor(0, 0).alpha() == 0                      # rounded corner is transparent
    centre = image.pixelColor(size // 2, size // 2)
    assert centre.alpha() == 255
    assert count_pixels(image, windows_icons.BRAND_ACCENT) >= max(8, size * size // 40)   # the outline is there
    assert count_pixels(image, windows_icons.BRAND_TILE, tolerance=6) > size * size // 6  # and so is the tile


def test_the_n_is_inside_the_outline_not_just_a_ring(qapp):
    image = windows_icons.render_brand(256)
    # The N's diagonal crosses the centre of the glyph.
    assert close_to(image.pixelColor(128, 128), windows_icons.BRAND_ACCENT, tolerance=30)
    # The area between the ring and the N is empty tile.
    assert close_to(image.pixelColor(100, 70), windows_icons.BRAND_TILE, tolerance=12)


def test_paused_variant_adds_an_amber_dot_without_changing_the_size(qapp):
    normal, paused = windows_icons.render_brand(32), windows_icons.render_brand(32, paused=True)
    assert normal != paused and paused.size() == normal.size()
    assert count_pixels(normal, windows_icons.PAUSED_DOT) == 0
    assert count_pixels(paused, windows_icons.PAUSED_DOT) > 8


def test_icon_objects_cover_every_size(qapp):
    for icon in (windows_icons.app_icon(), windows_icons.tray_icon(False), windows_icons.tray_icon(True)):
        assert not icon.isNull()
        assert {s.width() for s in icon.availableSizes()} >= set(windows_icons.SIZES)
    assert windows_icons.tray_icon(True).pixmap(32).toImage() != windows_icons.tray_icon(False).pixmap(32).toImage()


# -- the shipped files ------------------------------------------------------

def parse_ico(data: bytes):
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    entries = []
    for index in range(count):
        width, height, _c, _r, planes, bpp, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        entries.append((width or 256, height or 256, planes, bpp, data[offset:offset + length]))
    return entries


def test_ico_has_every_size_as_valid_png_entries(qapp):
    data = (ASSETS / "nexus.ico").read_bytes()
    entries = parse_ico(data)
    assert [e[0] for e in entries] == list(windows_icons.SIZES)
    for width, height, planes, bpp, payload in entries:
        assert (width, height) == (width, width) and planes == 1 and bpp == 32
        assert payload.startswith(PNG_SIGNATURE)
        image = QImage.fromData(payload, "PNG")
        assert (image.width(), image.height()) == (width, height)


def test_generated_ico_and_pngs_match_the_vector_mark(qapp):
    """Stale-asset guard: re-run tools/build_windows_icons.py after changing the mark."""
    for width, _h, _p, _b, payload in parse_ico((ASSETS / "nexus.ico").read_bytes()):
        assert QImage.fromData(payload, "PNG").convertToFormat(QImage.Format.Format_ARGB32) == \
            windows_icons.render_brand(width).convertToFormat(QImage.Format.Format_ARGB32)
    for size in windows_icons.SIZES:
        assert QImage(str(ASSETS / f"nexus-{size}.png")).convertToFormat(QImage.Format.Format_ARGB32) == \
            windows_icons.render_brand(size).convertToFormat(QImage.Format.Format_ARGB32)
    assert QImage(str(ASSETS / "nexus-tray-paused-32.png")).convertToFormat(QImage.Format.Format_ARGB32) == \
        windows_icons.render_brand(32, paused=True).convertToFormat(QImage.Format.Format_ARGB32)


def test_ico_builder_is_deterministic(qapp):
    assert windows_icons.build_ico_bytes() == windows_icons.build_ico_bytes()


# -- taskbar identity ---------------------------------------------------------

def test_app_user_model_id_is_nexus_not_pythonw(monkeypatch):
    shell32 = Mock()
    shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0
    monkeypatch.setattr(windows_icons.ctypes, "windll", Mock(shell32=shell32), raising=False)
    monkeypatch.delenv(windows_sandbox.ENV_FLAG, raising=False)
    assert windows_icons.set_app_user_model_id() is True
    assert shell32.SetCurrentProcessExplicitAppUserModelID.call_args.args[0].value == "StoreM.Nexus"


def test_sandbox_gets_its_own_taskbar_identity(monkeypatch):
    shell32 = Mock()
    shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0
    monkeypatch.setattr(windows_icons.ctypes, "windll", Mock(shell32=shell32), raising=False)
    monkeypatch.setenv(windows_sandbox.ENV_FLAG, "1")
    windows_icons.set_app_user_model_id()
    assert shell32.SetCurrentProcessExplicitAppUserModelID.call_args.args[0].value == "StoreM.Nexus.Sandbox"


def test_app_user_model_id_failures_never_break_startup(monkeypatch):
    shell32 = Mock()
    shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0x80070057
    monkeypatch.setattr(windows_icons.ctypes, "windll", Mock(shell32=shell32), raising=False)
    assert windows_icons.set_app_user_model_id("x") is False
    monkeypatch.setattr(windows_icons.sys, "platform", "linux")
    assert windows_icons.set_app_user_model_id("x") is False


def test_identity_and_icon_are_set_before_any_window_exists():
    source = inspect.getsource(windows_app._run_windows)
    assert source.index("set_app_user_model_id()") < source.index("QApplication(sys.argv)")
    assert "app.setWindowIcon(app_icon())" in source
    assert source.index("app.setWindowIcon") < source.index("WindowsTimelineWindow(")


# -- launching without a console --------------------------------------------

def test_project_declares_a_gui_launcher_and_ships_the_icons():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["gui-scripts"] == {"nexus-windows": "nexus_linux.windows_app:main"}
    assert "nexus-windows" not in config["project"].get("scripts", {})   # would open a console
    assert "assets/icons/*" in config["tool"]["setuptools"]["package-data"]["nexus_linux"]


@pytest.mark.skipif(not (ROOT / ".venv" / "Scripts" / "pythonw.exe").exists() or not shutil.which("powershell"),
                    reason="needs the project venv and Windows PowerShell")
def test_shortcut_tool_targets_pythonw_with_the_nexus_icon_and_refuses_to_overwrite(tmp_path):
    lnk = tmp_path / "Nexus.lnk"
    script = ROOT / "tools" / "windows_make_shortcut.ps1"
    run = lambda *extra: subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Path", str(lnk), *extra],
        capture_output=True, text=True, timeout=60)
    assert run().returncode == 0 and lnk.exists()
    assert run().returncode != 0                      # second time: refuses without -Force
    assert run("-Force").returncode == 0
    read = subprocess.run(["powershell", "-NoProfile", "-Command",
                           f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
                           "$s.TargetPath; $s.Arguments; $s.IconLocation"], capture_output=True, text=True, timeout=60)
    target, arguments, icon = read.stdout.strip().splitlines()[:3]
    assert target.lower().endswith(r"\.venv\scripts\pythonw.exe") and "cmd.exe" not in target.lower()
    assert arguments == "-m nexus_linux.windows_app"
    assert icon.lower().endswith(r"nexus_linux\assets\icons\nexus.ico,0")
