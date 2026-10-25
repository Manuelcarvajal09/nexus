"""Prepare (and optionally launch) an isolated Nexus for development.

Never touches the real Nexus data: the sandbox lives under the system temp dir
with its own LOCALAPPDATA, a verified read-only snapshot of the real database,
its own mutex, rarely used global hotkeys, paused capture and no Ctrl+V hook.

    python tools/windows_sandbox_run.py --prepare          # snapshot only
    python tools/windows_sandbox_run.py --launch           # snapshot (if missing) + start
    python tools/windows_sandbox_run.py --prepare --refresh  # new snapshot (old one is kept as .old)

For a guided, stoppable acceptance run use tools/windows_acceptance.py instead.
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


def sandbox_layout(real_local: str | None = None):
    """(real Nexus root, sandbox LOCALAPPDATA, sandbox DB, backups dir)."""
    real_local = real_local or os.environ.get("LOCALAPPDATA")
    if not real_local:
        raise SystemExit("LOCALAPPDATA no está definido")
    sandbox_local = Path(tempfile.gettempdir()) / "nexus-sandbox"
    return (Path(real_local) / "Nexus", sandbox_local,
            sandbox_local / "Nexus" / "data" / "nexus.sqlite", sandbox_local / "backups")


def ensure_snapshot(refresh: bool = False, real_local: str | None = None, log=print):
    """Create the sandbox database from a verified read-only snapshot of the real one."""
    real_root, sandbox_local, sandbox_db, backups = sandbox_layout(real_local)
    real_db = real_root / "data" / "nexus.sqlite"
    if refresh and sandbox_db.exists():
        sandbox_db.rename(sandbox_db.with_name(f"nexus.sqlite.old-{int(time.time())}"))
        for suffix in ("-wal", "-shm"):
            stale = sandbox_db.with_name(sandbox_db.name + suffix)
            if stale.exists():
                stale.unlink()
    if not sandbox_db.exists():
        if not real_db.exists():
            log("No existe la base real; el sandbox arrancará vacío")
        else:
            report = windows_sandbox.snapshot_database(real_db, sandbox_db)
            pristine = windows_sandbox.snapshot_database(
                sandbox_db, backups / f"nexus-pre-E1-{time.strftime('%Y%m%dT%H%M%S')}.sqlite")
            log(f"snapshot integrity: {report['integrity']} | copia pristina: {pristine['integrity']}")
            log(f"filas origen : {report['source_counts']}")
            log(f"filas copia  : {report['counts']}")
            if report["integrity"] != "ok" or report["counts"] != report["source_counts"]:
                raise SystemExit("La copia NO coincide con el origen; se aborta")
    else:
        log(f"Se reutiliza el sandbox existente: {sandbox_db}")
    return real_root, sandbox_local, sandbox_db


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if not (args.prepare or args.launch):
        parser.error("indica --prepare o --launch")

    real_root, sandbox_local, _db = ensure_snapshot(args.refresh)
    if not args.launch:
        return 0

    env = {
        **os.environ,
        "LOCALAPPDATA": str(sandbox_local),
        windows_sandbox.ENV_FLAG: "1",
        windows_sandbox.ENV_REAL_ROOT: str(real_root),
        "PYTHONPATH": str(ROOT / "src"),
    }
    if env.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        del env["QT_QPA_PLATFORM"]  # a launch must render with the real Windows platform
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = str(pythonw if pythonw.exists() else exe)
    subprocess.Popen([interpreter, "-m", "nexus_linux.windows_app"], env=env, cwd=str(ROOT))
    print("Sandbox lanzado (captura pausada, atajos Ctrl+Shift+9 / Ctrl+Shift+7, sin hook de Ctrl+V)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
