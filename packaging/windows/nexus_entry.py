"""Entry point of the frozen Nexus.exe (PyInstaller)."""
from nexus.platform.windows.app import main

if __name__ == "__main__":
    raise SystemExit(main())
