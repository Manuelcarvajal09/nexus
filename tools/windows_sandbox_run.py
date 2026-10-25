"""Prepare (and optionally launch) an isolated Nexus for development.

Never touches the real Nexus data: the sandbox lives under the system temp dir
with its own LOCALAPPDATA, a verified read-only snapshot of the real database,
its own mutex, rarely used global hotkeys, paused capture and no Ctrl+V hook.

    python tools/windows_sandbox_run.py --prepare          # snapshot only
    python tools/windows_sandbox_run.py --launch           # snapshot (if missing) + start
    python tools/windows_sandbox_run.py --prepare --refresh  # new snapshot (old one is kept as .old)

Prints only integrity results and row counts, never clip content.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nexus_linux import windows_sandbox  # noqa: E402  (stdlib-only module, safe before env switch)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if not (args.prepare or args.launch):
        parser.error("indica --prepare o --launch")

    real_local = os.environ.get("LOCALAPPDATA")
    if not real_local:
        print("LOCALAPPDATA no está definido")
        return 2
    real_root = Path(real_local) / "Nexus"
    real_db = real_root / "data" / "nexus.sqlite"
    sandbox_local = Path(tempfile.gettempdir()) / "nexus-sandbox"
    sandbox_db = sandbox_local / "Nexus" / "data" / "nexus.sqlite"
    backups = sandbox_local / "backups"

    if args.refresh and sandbox_db.exists():
        sandbox_db.rename(sandbox_db.with_name(f"nexus.sqlite.old-{int(time.time())}"))
        for suffix in ("-wal", "-shm"):
            stale = sandbox_db.with_name(sandbox_db.name + suffix)
            if stale.exists():
                stale.unlink()
    if not sandbox_db.exists():
        if not real_db.exists():
            print("No existe la base real; el sandbox arrancará vacío")
        else:
            report = windows_sandbox.snapshot_database(real_db, sandbox_db)
            pristine = windows_sandbox.snapshot_database(sandbox_db, backups / f"nexus-pre-E1-{time.strftime('%Y%m%dT%H%M%S')}.sqlite")
            print("snapshot integrity:", report["integrity"], "| copia pristina:", pristine["integrity"])
            print("filas origen :", report["source_counts"])
            print("filas copia  :", report["counts"])
            if report["integrity"] != "ok" or report["counts"] != report["source_counts"]:
                print("La copia NO coincide con el origen; se aborta")
                return 1
    else:
        print("Se reutiliza el sandbox existente:", sandbox_db)

    if not args.launch:
        return 0

    env = {
        **os.environ,
        "LOCALAPPDATA": str(sandbox_local),
        windows_sandbox.ENV_FLAG: "1",
        windows_sandbox.ENV_REAL_ROOT: str(real_root),
        "PYTHONPATH": str(ROOT / "src"),
    }
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = str(pythonw if pythonw.exists() else exe)
    subprocess.Popen([interpreter, "-m", "nexus_linux.windows_app"], env=env, cwd=str(ROOT))
    print("Sandbox lanzado (captura pausada, atajos Ctrl+Shift+9 / Ctrl+Shift+7, sin hook de Ctrl+V)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
