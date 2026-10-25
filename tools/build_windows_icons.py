"""Regenerate the Windows icon assets from the vector mark in windows_icons.py.

    python tools/build_windows_icons.py          # writes src/nexus_linux/assets/icons/
    python tools/build_windows_icons.py --check  # exit 1 if the committed files are stale

Output: nexus.ico (16-256 px, PNG entries) plus one PNG per size. Nothing is
installed or registered anywhere.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

from nexus_linux import windows_icons  # noqa: E402

TARGET = ROOT / "src" / "nexus_linux" / "assets" / "icons"


def expected_files() -> dict:
    files = {"nexus.ico": windows_icons.build_ico_bytes()}
    for size in windows_icons.SIZES:
        files[f"nexus-{size}.png"] = windows_icons.png_bytes(windows_icons.render_brand(size))
    files["nexus-tray-paused-32.png"] = windows_icons.png_bytes(windows_icons.render_brand(32, paused=True))
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    files = expected_files()
    if args.check:
        stale = [n for n, data in files.items() if not (TARGET / n).exists() or (TARGET / n).read_bytes() != data]
        print("stale:", stale or "none")
        return 1 if stale else 0
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (TARGET / name).write_bytes(data)
    print(f"wrote {len(files)} files to {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
