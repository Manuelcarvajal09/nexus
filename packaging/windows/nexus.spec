# PyInstaller spec for Nexus.exe (one-folder build: fast start, no temp extraction).
# Build with packaging/windows/build.ps1, which also writes version_info.txt.
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src"

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "nexus_entry.py")],
    pathex=[str(SRC)],
    datas=[(str(SRC / "nexus_linux" / "assets" / "icons"), "nexus_linux/assets/icons")],
    hiddenimports=["nexus_linux.i18n_en"],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtWebEngineCore", "PySide6.QtPdf", "PySide6.QtMultimedia", "PySide6.QtOpenGL",
              "PySide6.QtSvg", "PySide6.QtDBus", "PySide6.QtTest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Nexus",
    icon=str(SRC / "nexus_linux" / "assets" / "icons" / "nexus.ico"),
    version=str(ROOT / "packaging" / "windows" / "version_info.txt"),
    console=False,
    disable_windowed_traceback=False,
    uac_admin=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Nexus", upx=False)
