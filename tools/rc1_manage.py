"""Install / upgrade / roll back / uninstall a Nexus Windows RC package (stdlib only).

    python rc1_manage.py install   --package DIST --root ROOT [--data-dir DATA]
    python rc1_manage.py status    --root ROOT
    python rc1_manage.py rollback  --root ROOT [--to VERSION]
    python rc1_manage.py uninstall --root ROOT [--remove-data DATA --yes]

Layout under ROOT (everything the app code needs; user data is NEVER inside it):
    versions/<version>/venv        isolated interpreter + the installed wheel
    versions/<version>/INSTALLED.json
    current.json                   {"current": "...", "previous": [...]}
    Nexus.cmd                      GUI launcher of the current version (no console window)
    .nexus-rc-install              marker: uninstall refuses to delete a folder without it

Safety rules
  * --root is mandatory. Unless it lives under the system temp folder, --allow-real-install is required.
  * The package is verified (SHA256SUMS, wheel RECORD, pinned wheelhouse) BEFORE anything is created.
  * Dependencies come only from the package's wheelhouse: pip runs with --no-index --require-hashes.
  * Install never starts the app, never creates a start-with-Windows entry, never touches Start Menu/taskbar and
    never writes user data. If the user turned start-with-Windows ON, upgrade/rollback keep it ON (re-pointed).
  * Upgrade refuses while a Nexus of this root is running, and (with --data-dir) first takes a verified
    SQLite backup using the online backup API.
  * Uninstall removes only ROOT; user data is kept unless --remove-data DATA --yes is also given.
"""
from __future__ import annotations

import sys as _sys
_sys.dont_write_bytecode = True  # never drop __pycache__ into a (possibly read-only) release package

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = ".nexus-rc-install"


class ManageError(Exception):
    pass


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Nexus"


class RunKey:
    """Per-user Run key (the same entry the app's Configuración › Inicio switch manages)."""

    def read(self, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                return str(winreg.QueryValueEx(key, name)[0])
        except (FileNotFoundError, OSError):
            return None

    def write(self, name, value):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            return True
        except FileNotFoundError:
            return False


def _points_into(command, root: Path) -> bool:
    return bool(command) and str(root.resolve()).lower() in command.lower()


def follow_autostart(root: Path, version: str, registry=None, log=print) -> str:
    """If the user turned "start with Windows" ON and the entry belongs to THIS install root, point it at the
    version that is now current (upgrades and rollbacks keep the choice). Never creates an entry, never touches
    an entry of another program or install, and does nothing when the choice is OFF (the default)."""
    registry = registry or RunKey()
    current = registry.read(RUN_VALUE)
    if not _points_into(current, root):
        return "untouched"
    pythonw = root / "versions" / version / "venv" / "Scripts" / "pythonw.exe"
    registry.write(RUN_VALUE, f'"{pythonw}" -m nexus_linux.windows_app')
    log(f"start-with-Windows was ON: now points at {version}")
    return "re-pointed"


def drop_autostart(root: Path, registry=None, log=print) -> str:
    """On uninstall: remove the Run entry only if it points into the root being removed (no dangling entry)."""
    registry = registry or RunKey()
    if _points_into(registry.read(RUN_VALUE), root):
        registry.delete(RUN_VALUE)
        log("removed the start-with-Windows entry that pointed into the removed install")
        return "removed"
    return "untouched"


def _import_verify(package: Path):
    for candidate in (package / "tools", HERE):
        if (candidate / "rc1_verify.py").exists():
            sys.path.insert(0, str(candidate))
            import rc1_verify  # type: ignore
            return rc1_verify
    raise ManageError("rc1_verify.py not found")


def real_run(cmd, **kwargs):
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run([str(c) for c in cmd], **kwargs)


def is_under_temp(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
        return True
    except ValueError:
        return False


def check_root(root: Path, allow_real: bool) -> Path:
    root = root.resolve()
    if not allow_real and not is_under_temp(root):
        raise ManageError(f"{root} is outside the system temp folder: pass --allow-real-install to confirm "
                          "that you really want to install there")
    return root


def read_state(root: Path) -> dict:
    path = root / "current.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"current": None, "previous": []}


def write_state(root: Path, state: dict) -> None:
    (root / "current.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def write_launcher(root: Path, version: str) -> Path:
    pythonw = root / "versions" / version / "venv" / "Scripts" / "pythonw.exe"
    launcher = root / "Nexus.cmd"
    launcher.write_text(f'@echo off\r\nstart "Nexus" /b "{pythonw}" -m nexus_linux.windows_app\r\n', encoding="utf-8", newline="")
    return launcher


def nexus_running_from(root: Path, run=real_run) -> list[str]:
    """Command lines of running Nexus processes that use an interpreter under ROOT (read-only query)."""
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*nexus_linux.windows_app*' } | "
              "ForEach-Object { $_.ExecutablePath }")
    result = run(["powershell", "-NoProfile", "-Command", script], timeout=60)
    prefix = str(root.resolve()).lower()
    return [line for line in (result.stdout or "").splitlines() if line.strip().lower().startswith(prefix)]


def install(package: Path, root: Path, data_dir: Path | None = None, python: str | None = None,
            run=real_run, running=nexus_running_from, log=print, registry=None) -> dict:
    package = Path(package).resolve()
    verify = _import_verify(package)
    for note in verify.verify_package(package):
        log("verified:", note)
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("created by rc1_manage.py\n", encoding="utf-8")
    state = read_state(root)
    target = root / "versions" / version
    if target.exists():
        if (target / "INSTALLED.json").exists():
            raise ManageError(f"version {version} is already installed in {root}; uninstall it or choose another root")
        shutil.rmtree(target)   # leftover of an interrupted install (INSTALLED.json is written last): safe to redo
    if state["current"] and running(root):
        raise ManageError("Nexus is running from this install root: close it from its tray menu first")

    backup_info = None
    if state["current"] and data_dir is not None:
        db = Path(data_dir) / "data" / "nexus.sqlite"
        if db.exists():
            tools = package / "tools"
            stamp = time.strftime("%Y%m%dT%H%M%S")
            dest = root / "backups" / f"pre-{version}-{stamp}"
            dest.mkdir(parents=True)
            out = run([python or sys.executable, tools / "windows_db_tools.py", "backup", db, dest, "--label", "pre-upgrade"])
            if out.returncode != 0:
                raise ManageError("database backup failed; upgrade aborted:\n" + (out.stdout or "") + (out.stderr or ""))
            backup_file = sorted(dest.glob("*.sqlite"))[-1]
            check = run([python or sys.executable, tools / "windows_db_tools.py", "verify", backup_file])
            if check.returncode != 0:
                raise ManageError("database backup did not verify; upgrade aborted:\n" + (check.stdout or ""))
            backup_info = str(backup_file)
            log("database backup verified:", backup_info)

    try:
        venv = target / "venv"
        target.mkdir(parents=True)
        base = python or sys.executable
        step = run([base, "-m", "venv", venv])
        if step.returncode != 0:
            raise ManageError("venv creation failed:\n" + (step.stderr or ""))
        py = venv / "Scripts" / "python.exe"
        pip = [py, "-m", "pip", "install", "--no-index", "--find-links", package / "wheelhouse", "--disable-pip-version-check"]
        step = run([*pip, "--require-hashes", "-r", package / "requirements-rc1.lock"])
        if step.returncode != 0:
            raise ManageError("dependency install failed (offline, hash-pinned):\n" + (step.stdout or "") + (step.stderr or ""))
        wheel = package / manifest["wheel"]["file"]
        step = run([*pip, "--no-deps", wheel])
        if step.returncode != 0:
            raise ManageError("wheel install failed:\n" + (step.stdout or "") + (step.stderr or ""))
        smoke = run([py, "-c", "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; import nexus_linux, PySide6.QtWidgets; "
                               "print(nexus_linux.__version__)"])
        if smoke.returncode != 0 or smoke.stdout.strip() != version:
            raise ManageError(f"smoke import failed or wrong version ({smoke.stdout.strip()!r}):\n{smoke.stderr}")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)  # a half-installed version must never become 'current'
        raise

    (target / "INSTALLED.json").write_text(json.dumps({
        "version": version, "commit": manifest["commit"], "wheel_sha256": manifest["wheel"]["sha256"],
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "database_backup": backup_info}, indent=2) + "\n", encoding="utf-8")
    previous = state["previous"] + ([state["current"]] if state["current"] else [])
    state = {"current": version, "previous": previous}
    write_state(root, state)
    launcher = write_launcher(root, version)
    follow_autostart(root, version, registry, log)
    log(f"installed {version} -> {target}\nlauncher: {launcher}\nuser data untouched (lives outside ROOT)")
    return state


def rollback(root: Path, to: str | None = None, running=nexus_running_from, log=print, registry=None) -> dict:
    """Switch the launcher to an older version (default: the previously current one).

    Versions stay on disk, so `--to <newer>` switches forward again. The data folder is never modified."""
    state = read_state(root)
    target = to or (state["previous"][-1] if state["previous"] else None)
    if target is None:
        raise ManageError("there is no previous version to roll back to")
    if target == state["current"]:
        raise ManageError(f"{target} is already the current version")
    if not (root / "versions" / target / "venv").exists():
        raise ManageError(f"version {target} is not on disk")
    if running(root):
        raise ManageError("Nexus is running from this install root: close it first")
    previous = [v for v in state["previous"] if v != target]
    if state["current"]:
        previous.append(state["current"])
    state = {"current": target, "previous": previous}
    write_state(root, state)
    write_launcher(root, target)
    follow_autostart(root, target, registry, log)
    log(f"switched: current is now {target}; the data folder was not modified")
    return state


def uninstall(root: Path, remove_data: Path | None = None, yes: bool = False, running=nexus_running_from, log=print, registry=None) -> None:
    if not (root / MARKER).exists():
        raise ManageError(f"{root} has no {MARKER}: refusing to delete a folder this tool did not create")
    if running(root):
        raise ManageError("Nexus is running from this install root: close it first")
    if remove_data is not None:
        if not yes:
            raise ManageError("--remove-data needs --yes")
        if not (Path(remove_data) / "data" / "nexus.sqlite").exists() and not (Path(remove_data) / "config").exists():
            raise ManageError(f"{remove_data} does not look like a Nexus data folder")
    drop_autostart(root, registry, log)
    shutil.rmtree(root)
    log(f"removed {root}")
    if remove_data is not None:
        shutil.rmtree(remove_data)
        log(f"removed user data {remove_data} (explicitly requested)")
    else:
        log("user data kept (%LOCALAPPDATA%\\Nexus or the folder you configured)")


def status(root: Path, log=print) -> dict:
    state = read_state(root)
    versions = sorted(p.name for p in (root / "versions").glob("*")) if (root / "versions").exists() else []
    log(json.dumps({"root": str(root), **state, "on_disk": versions,
                    "launcher": str(root / "Nexus.cmd") if (root / "Nexus.cmd").exists() else None}, indent=2))
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "status", "rollback", "uninstall"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True)
        p.add_argument("--allow-real-install", action="store_true")
        if name == "install":
            p.add_argument("--package", required=True)
            p.add_argument("--data-dir", help="Nexus data folder (the one that contains data/nexus.sqlite) to back up before upgrading")
            p.add_argument("--python", help="base CPython 3.11 to create the venv with")
        if name == "rollback":
            p.add_argument("--to", help="switch to this on-disk version instead of the previous one")
        if name == "uninstall":
            p.add_argument("--remove-data")
            p.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = check_root(Path(args.root), args.allow_real_install)
        if args.command == "install":
            install(Path(args.package), root, Path(args.data_dir) if args.data_dir else None, args.python)
        elif args.command == "rollback":
            rollback(root, args.to)
        elif args.command == "uninstall":
            uninstall(root, Path(args.remove_data) if args.remove_data else None, args.yes)
        else:
            status(root)
    except ManageError as exc:
        print("REFUSED/FAILED:", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
