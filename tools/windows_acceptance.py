"""Isolated, stoppable acceptance run of the updated Nexus (real Windows platform).

    python tools/windows_acceptance.py start [--open] [--refresh] [--allow-hook] [--code DIR]
    python tools/windows_acceptance.py status
    python tools/windows_acceptance.py stop

What it guarantees (and checks, instead of assuming):
  * its own LOCALAPPDATA under the system temp dir: own SQLite copy, settings,
    theme, log -- your personal configuration is never read or written;
  * its own single-instance mutex and its own global hotkeys (Ctrl+Shift+9 / 7);
  * clipboard capture starts PAUSED (turn it on from Configuración if a test needs it);
  * no Ctrl+V hook unless you pass --allow-hook;
  * QT_QPA_PLATFORM=offscreen is removed from the child environment, and the child
    must report platform=windows in its own log or the run is aborted;
  * `stop` only ends the processes this tool started (identified by PID *and*
    creation time); it never touches any other Nexus process.

--code DIR runs the app from DIR/src (e.g. a `git archive` of a release) instead of
this working tree; useful to rehearse exactly what will be installed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from nexus_linux import windows_sandbox  # noqa: E402
import windows_sandbox_run as sandbox_run  # noqa: E402

STATE_NAME = "acceptance.json"
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


class AcceptanceError(RuntimeError):
    pass


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def build_env(base: dict, *, sandbox_local, real_root, code_src, open_panel=False, allow_hook=False,
              reduced_motion=False) -> dict:
    """The environment of the test process; raises if it could touch real data."""
    env = dict(base)
    env.pop("QT_QPA_PLATFORM", None)  # never offscreen: the real Windows platform
    sandbox_local, real_root = Path(sandbox_local).resolve(), Path(real_root).resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    if not _within(sandbox_local, temp):
        raise AcceptanceError("El sandbox debe vivir bajo la carpeta temporal del sistema.")
    if sandbox_local == real_root or _within(sandbox_local, real_root) or _within(real_root, sandbox_local):
        raise AcceptanceError("El sandbox se solapa con la carpeta real de Nexus.")
    env.update({
        "LOCALAPPDATA": str(sandbox_local),
        windows_sandbox.ENV_FLAG: "1",
        windows_sandbox.ENV_REAL_ROOT: str(real_root),
        "PYTHONPATH": str(code_src),
        "NEXUS_OPEN_ON_START": "1" if open_panel else "0",
    })
    if allow_hook:
        env[windows_sandbox.ENV_ALLOW_HOOK] = "1"
    else:
        env.pop(windows_sandbox.ENV_ALLOW_HOOK, None)
    if reduced_motion:
        env["NEXUS_REDUCED_MOTION"] = "1"   # only for this process; no Windows setting is changed
    else:
        env.pop("NEXUS_REDUCED_MOTION", None)
    if "QT_QPA_PLATFORM" in env:
        raise AcceptanceError("QT_QPA_PLATFORM no debe estar presente en el proceso de prueba.")
    return env


def list_nexus_processes(run=subprocess.run) -> list[dict]:
    """Every running Nexus process: {pid, ppid, created}. Reads, never signals."""
    # Only Python / launcher executables, and never the query's own PowerShell.
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and "
        "$_.Name -match '^(pythonw?|nexus-windows)\\.exe$' -and $_.CommandLine -and "
        "($_.CommandLine -like '*nexus_linux.windows_app*' -or $_.CommandLine -like '*nexus-windows*') } | "
        "Select-Object @{n='pid';e={[int]$_.ProcessId}}, @{n='ppid';e={[int]$_.ParentProcessId}}, "
        "@{n='created';e={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress"
    )
    result = run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60)
    text = (result.stdout or "").strip()
    if not text:
        return []
    data = json.loads(text)
    return [data] if isinstance(data, dict) else list(data)


def validate_targets(recorded: list[dict], running: list[dict]) -> list[dict]:
    """Recorded processes that are still the same process (PID and creation time)."""
    alive = {(p["pid"], p["created"]) for p in running}
    return [p for p in recorded if (p["pid"], p["created"]) in alive]


def state_path() -> Path:
    return sandbox_run.sandbox_layout()[1] / STATE_NAME


def load_state() -> dict | None:
    path = state_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def read_new_log(log_path: Path, offset: int) -> str:
    if not log_path.exists():
        return ""
    with open(log_path, "rb") as handle:
        handle.seek(offset)
        return handle.read().decode("utf-8", "replace")


def confirm_startup(text: str, sandbox_local: Path) -> dict:
    """Parse the child's own startup line; raises unless it is a real-platform sandbox."""
    line = next((l for l in text.splitlines() if "run platform=" in l), "")
    if not line:
        raise AcceptanceError("El proceso de prueba no informó su plataforma.")
    fields = dict(part.split("=", 1) for part in line.split("run ", 1)[1].split(" ") if "=" in part)
    if fields.get("platform") != "windows":
        raise AcceptanceError(f"Plataforma inesperada: {fields.get('platform')} (se esperaba windows).")
    if fields.get("sandbox") != "True":
        raise AcceptanceError("El proceso no está en modo sandbox.")
    if Path(line.split("data=", 1)[1].split(" code=")[0]).resolve() != (sandbox_local / "Nexus").resolve():
        raise AcceptanceError("El proceso no usa la carpeta de datos del sandbox.")
    return fields


def cmd_start(args) -> int:
    existing = load_state()
    if existing and validate_targets(existing["processes"], list_nexus_processes()):
        print("Ya hay una prueba en marcha. Ciérrala con:  python tools/windows_acceptance.py stop")
        return 1
    real_before = list_nexus_processes()
    real_root, sandbox_local, _db = sandbox_run.ensure_snapshot(args.refresh)
    code_src = (Path(args.code).resolve() / "src") if args.code else ROOT / "src"
    if not (code_src / "nexus_linux" / "windows_app.py").exists():
        raise AcceptanceError(f"No encuentro el código en {code_src}")
    env = build_env(os.environ, sandbox_local=sandbox_local, real_root=real_root, code_src=code_src,
                    open_panel=args.open, allow_hook=args.allow_hook, reduced_motion=args.reduced_motion)
    log_path = sandbox_local / "Nexus" / "cache" / "nexus.log"
    offset = log_path.stat().st_size if log_path.exists() else 0
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    interpreter = str(pythonw if pythonw.exists() else Path(sys.executable).with_name("pythonw.exe"))
    subprocess.Popen([interpreter, "-m", "nexus_linux.windows_app"], env=env, cwd=str(code_src.parent),
                     creationflags=DETACHED, close_fds=True)
    deadline, text = time.time() + 30, ""
    while time.time() < deadline:
        time.sleep(0.5)
        text = read_new_log(log_path, offset)
        if "window=WindowsStackWindow" in text:
            break
    new = [p for p in list_nexus_processes() if p["pid"] not in {r["pid"] for r in real_before}]
    try:
        fields = confirm_startup(text, sandbox_local)
    except AcceptanceError:
        kill(new)
        raise
    state = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "processes": new, "code": str(code_src),
             "open": args.open, "allow_hook": args.allow_hook}
    state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("PRUEBA AISLADA EN MARCHA")
    print(f"  procesos de la prueba : {', '.join(str(p['pid']) for p in new)}")
    print(f"  tu Nexus habitual     : {', '.join(str(p['pid']) for p in real_before) or '(no detectado)'}  (no se toca)")
    print(f"  plataforma Qt         : {fields['platform']}  (offscreen NO presente)")
    print(f"  datos                 : {sandbox_local / 'Nexus'}")
    print(f"  código                : {code_src}")
    print("  atajos de la prueba   : Ctrl+Shift+9 (panel)  ·  Ctrl+Shift+7 (Stack)")
    print("  captura del portapapeles: PAUSADA" + ("  ·  hook de Ctrl+V: ACTIVADO (--allow-hook)" if args.allow_hook else ""))
    if args.reduced_motion:
        print("  movimiento            : REDUCIDO (solo este proceso; no se cambia ningún ajuste de Windows)")
    print("  para cerrar           : menú de la bandeja > «Salir de Nexus» (icono 'Nexus (sandbox)'),")
    print("                          o:  python tools/windows_acceptance.py stop")
    return 0


def kill(processes: list[dict]) -> None:
    for process in processes:
        subprocess.run(["taskkill", "/PID", str(process["pid"]), "/T", "/F"], capture_output=True, timeout=30)


def cmd_stop(_args) -> int:
    state = load_state()
    if not state:
        print("No hay ninguna prueba registrada; no se cierra nada.")
        return 0
    running = list_nexus_processes()
    targets = validate_targets(state["processes"], running)
    if not targets:
        print("La prueba ya estaba cerrada (por la bandeja o por tu parte).")
    kill(targets)
    time.sleep(1.5)
    leftover = validate_targets(state["processes"], list_nexus_processes())
    if leftover:
        print(f"No pude cerrar: {[p['pid'] for p in leftover]}")
        return 1
    state_path().unlink(missing_ok=True)
    others = [p["pid"] for p in list_nexus_processes()]
    print(f"Prueba cerrada. Procesos Nexus que siguen en marcha (los tuyos, intactos): {others or 'ninguno'}")
    return 0


def cmd_theme(args) -> int:
    """Flip Liquid Glass between Light and Dark INSIDE the isolated run (never the Windows setting)."""
    target = sandbox_run.sandbox_layout()[1] / "Nexus" / "config" / "system-theme.txt"
    if args.value == "real":
        target.unlink(missing_ok=True)
        print("La prueba vuelve a seguir el tema real de Windows.")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(args.value, encoding="utf-8")
    print(f"Liquid Glass de la prueba -> {args.value} (se aplica en ~1 s; tu configuración de Windows no cambia).")
    return 0


def cmd_status(_args) -> int:
    state = load_state()
    running = list_nexus_processes()
    mine = validate_targets(state["processes"], running) if state else []
    theirs = [p for p in running if (p["pid"], p["created"]) not in {(m["pid"], m["created"]) for m in mine}]
    print(f"Prueba aislada: {'EN MARCHA ' + str([p['pid'] for p in mine]) if mine else 'cerrada'}")
    print(f"Tu Nexus habitual: {[p['pid'] for p in theirs] or 'no detectado'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--open", action="store_true", help="abrir el panel al arrancar")
    start.add_argument("--refresh", action="store_true", help="rehacer la copia de la base desde la real (solo lectura)")
    start.add_argument("--allow-hook", action="store_true", help="permitir el hook de Ctrl+V (solo para probar Stack)")
    start.add_argument("--reduced-motion", action="store_true", help="arrancar con movimiento reducido (solo este proceso)")
    start.add_argument("--code", help="carpeta con una copia del código (git archive) en vez de este árbol de trabajo")
    sub.add_parser("stop")
    sub.add_parser("status")
    theme = sub.add_parser("theme", help="alternar Liquid Glass Light/Dark solo dentro de la prueba")
    theme.add_argument("value", choices=("light", "dark", "real"))
    args = parser.parse_args()
    try:
        return {"start": cmd_start, "stop": cmd_stop, "status": cmd_status, "theme": cmd_theme}[args.command](args)
    except AcceptanceError as error:
        print(f"ABORTADO: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
