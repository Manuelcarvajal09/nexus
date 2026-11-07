"""Install / start / upgrade / roll back / uninstall check of a Nexus RC package on the REAL Windows host,
entirely inside a temporary folder with synthetic data.

    python tools/rc1_lifecycle_check.py --package dist-rc1 [--upgrade-package dist-rc1b] [--python BASE]

Guarantees: every path lives under the system temp folder; the app runs in sandbox mode (own mutex, own hotkeys,
capture paused, no Ctrl+V hook) with LOCALAPPDATA redirected; your Nexus processes are never signalled (the
check compares their PIDs before and after) and your data folder is never opened. The system clipboard is not used.
It does NOT touch Startup, Start Menu or the taskbar, and it does not test clipboard capture/paste (Windows Sandbox).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import windows_acceptance as acc  # noqa: E402
import rc1_verify  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    return bool(ok)


def manage(*args, **kwargs):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "rc1_manage.py"), *map(str, args)],
                          capture_output=True, text=True, timeout=900, **kwargs)


def state(root: Path) -> dict:
    return json.loads((root / "current.json").read_text(encoding="utf-8"))


def venv_python(root: Path, version: str, windowless=True) -> Path:
    return root / "versions" / version / "venv" / "Scripts" / ("pythonw.exe" if windowless else "python.exe")


def counts(db: Path) -> tuple:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return (con.execute("SELECT COUNT(*) FROM clip_items").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM pinboards").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM clip_items WHERE title IS NOT NULL").fetchone()[0],
                con.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        con.close()


def seed(root: Path, version: str, local: Path) -> None:
    code = ("from nexus_linux.storage import StorageService\n"
            "s = StorageService()\n"
            "b = s.create_pinboard('Pruebas', '#80B5EE', None)\n"
            "items = [s.capture(kind='text', text_content=f'Elemento sintético {n}', source_app='RC1') for n in range(6)]\n"
            "s.set_title(items[0].id, 'Título sintético'); s.add_to_pinboard(items[1].id, b.id); s.close()\n")
    env = {**os.environ, "LOCALAPPDATA": str(local), "QT_QPA_PLATFORM": "offscreen"}
    env.pop("PYTHONPATH", None)
    done = subprocess.run([str(venv_python(root, version, False)), "-c", code], env=env, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise SystemExit("seeding failed:\n" + done.stderr)


def start_app(root: Path, version: str, local: Path, real_root: Path):
    log = local / "Nexus" / "cache" / "nexus.log"
    offset = log.stat().st_size if log.exists() else 0
    env = {**os.environ, "LOCALAPPDATA": str(local), "NEXUS_SANDBOX": "1", "NEXUS_SANDBOX_REAL_ROOT": str(real_root),
           "NEXUS_OPEN_ON_START": "0"}
    for name in ("PYTHONPATH", "QT_QPA_PLATFORM"):
        env.pop(name, None)
    before = {p["pid"] for p in acc.list_nexus_processes()}
    subprocess.Popen([str(venv_python(root, version)), "-m", "nexus_linux.windows_app"], env=env, cwd=str(root),
                     creationflags=acc.DETACHED, close_fds=True)
    text = ""
    end = time.time() + 40
    while time.time() < end:
        text = acc.read_new_log(log, offset)
        if "window=WindowsStackWindow" in text:
            break
        time.sleep(0.25)
    mine = [p for p in acc.list_nexus_processes() if p["pid"] not in before]
    try:
        fields = acc.confirm_startup(text, local)
        line = next(l for l in text.splitlines() if "run platform=" in l)
        code = line.split("code=", 1)[1].strip() if "code=" in line else ""
    except Exception as exc:  # noqa: BLE001 - reported as a failed check
        fields, code = {"error": str(exc)}, ""
    return mine, fields, code


def stop_app(mine):
    acc.kill(mine)
    end = time.time() + 15
    while time.time() < end and acc.validate_targets(mine, acc.list_nexus_processes()):
        time.sleep(0.25)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--package", required=True)
    parser.add_argument("--upgrade-package")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--keep", action="store_true", help="keep the temp folder for inspection")
    args = parser.parse_args()
    package = Path(args.package).resolve()
    real_before = {p["pid"] for p in acc.list_nexus_processes()}
    real_root = Path(os.environ["LOCALAPPDATA"]) / "Nexus"
    work = Path(tempfile.mkdtemp(prefix="nexus-rc-lifecycle-"))
    root, local = work / "install", work / "local"
    data_dir = local / "Nexus"
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Nexus.lnk"

    def startup_state():
        return (startup.exists(), startup.stat().st_mtime if startup.exists() else None)

    startup_before = startup_state()

    def run_key_state():
        """Names and values of the user's HKCU Run entries (read only): they must be identical afterwards."""
        import winreg
        values = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                        values[name] = value
                        index += 1
                    except OSError:
                        break
        except FileNotFoundError:
            pass
        return values

    run_before = run_key_state()
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    v1 = manifest["version"]
    print(f"work folder: {work}\npackage: {package.name}  version {v1}  commit {manifest['commit'][:12]}")
    try:
        # A. integrity, and a tampered copy must be refused before anything is created
        try:
            rc1_verify.verify_package(package); check("package integrity (SHA256SUMS, wheel RECORD, pinned wheelhouse)", True)
        except rc1_verify.PackageError as exc:
            check("package integrity", False, str(exc)); return 1
        tampered = work / "tampered"; shutil.copytree(package, tampered)
        wheel = next(tampered.glob("*.whl")); wheel.write_bytes(wheel.read_bytes() + b"x")
        refused = manage("install", "--package", tampered, "--root", work / "never", "--python", args.python)
        check("a tampered package is refused (hash mismatch) and nothing is installed",
              refused.returncode != 0 and "hash mismatch" in refused.stdout + refused.stderr and not (work / "never" / "versions").exists(),
              (refused.stdout + refused.stderr).strip().splitlines()[-1][:120])

        # B. refuses a root outside temp unless explicitly allowed
        outside = manage("install", "--package", package, "--root", Path.home() / "nexus-should-not-exist", "--python", args.python)
        check("an install root outside the temp folder needs --allow-real-install", outside.returncode != 0
              and "allow-real-install" in outside.stdout + outside.stderr and not (Path.home() / "nexus-should-not-exist").exists())

        # C. clean install
        done = manage("install", "--package", package, "--root", root, "--python", args.python)
        check("clean install (offline, hash-pinned)", done.returncode == 0 and state(root)["current"] == v1, done.stdout.strip().splitlines()[-1] if done.stdout.strip() else done.stderr[-200:])
        if state(root)["current"] != v1:
            return 1
        check("launcher created, no console window (pythonw)", "pythonw.exe" in (root / "Nexus.cmd").read_text())
        check("install did not touch the user's Startup shortcut", startup_state() == startup_before,
              f"{startup_before} -> {startup_state()}")
        # D. start the installed app on synthetic data
        seed(root, v1, local)
        before_counts = counts(data_dir / "data" / "nexus.sqlite")
        check("installed package seeds a synthetic database", before_counts[:3] == (6, 1, 1) and before_counts[3] == "ok", str(before_counts))
        mine, fields, code = start_app(root, v1, local, real_root)
        check("installed app starts on the real windows platform (sandbox mode)", fields.get("platform") == "windows" and fields.get("sandbox") == "True", str(fields))
        check("it runs the INSTALLED code, not the repository", str(root).lower() in code.lower() or "site-packages" in code.lower(), code)
        stop_app(mine)
        check("installed app stops", not acc.validate_targets(mine, acc.list_nexus_processes()))
        check("data intact after running and stopping", counts(data_dir / "data" / "nexus.sqlite") == before_counts)

        # E. failed install must not damage the current version
        if args.upgrade_package:
            up = Path(args.upgrade_package).resolve()
            v2 = json.loads((up / "manifest.json").read_text(encoding="utf-8"))["version"]
            broken = work / "broken"; shutil.copytree(up, broken)
            next((broken / "wheelhouse").glob("shiboken6*")).unlink()      # simulate a damaged package (also fails verification)
            bad = manage("install", "--package", broken, "--root", root, "--data-dir", data_dir, "--python", args.python)
            check("a damaged upgrade package fails safely (refused, current version untouched)",
                  bad.returncode != 0 and ("not pinned" in bad.stdout + bad.stderr or "hash mismatch" in bad.stdout + bad.stderr or "missing" in bad.stdout + bad.stderr
                                           or "FAILED" in bad.stdout + bad.stderr or "Error" in bad.stderr)
                  and state(root)["current"] == v1 and not (root / "versions" / v2).exists())
            # F. real upgrade with a verified backup
            good = manage("install", "--package", up, "--root", root, "--data-dir", data_dir, "--python", args.python)
            st = state(root)
            check("upgrade installs the new version next to the old one", good.returncode == 0 and st["current"] == v2 and st["previous"] == [v1],
                  good.stdout.strip().splitlines()[-1] if good.stdout.strip() else good.stderr[-200:])
            backups = list((root / "backups").rglob("*.sqlite"))
            check("a verified database backup was taken before upgrading", len(backups) == 1 and counts(backups[0])[:3] == (6, 1, 1), str([b.name for b in backups]))
            check("user data untouched by the upgrade", counts(data_dir / "data" / "nexus.sqlite") == before_counts)
            mine, fields, code = start_app(root, v2, local, real_root)
            check("the upgraded app starts on the existing data", fields.get("platform") == "windows", str(fields))
            stop_app(mine)
            check("data intact after the upgraded app ran", counts(data_dir / "data" / "nexus.sqlite")[:3] == (6, 1, 1)
                  and counts(data_dir / "data" / "nexus.sqlite")[3] == "ok")
            # G. rollback and forward
            back = manage("rollback", "--root", root)
            check("rollback switches the launcher to the previous version", back.returncode == 0 and state(root)["current"] == v1 and (root / "versions" / v2).exists())
            mine, fields, code = start_app(root, v1, local, real_root)
            check("the rolled-back version starts on the same data", fields.get("platform") == "windows", str(fields))
            stop_app(mine)
            fwd = manage("rollback", "--root", root, "--to", v2)
            check("switching forward again works", fwd.returncode == 0 and state(root)["current"] == v2)

        # H. uninstall keeps data; a fresh install afterwards works
        gone = manage("uninstall", "--root", root)
        check("uninstall removes the install root", gone.returncode == 0 and not root.exists(), gone.stdout.strip().splitlines()[-1] if gone.stdout.strip() else "")
        check("uninstall keeps the user data", counts(data_dir / "data" / "nexus.sqlite")[:3] == (6, 1, 1))
        again = manage("install", "--package", package, "--root", root, "--python", args.python)
        check("re-install after uninstall works and sees the old data", again.returncode == 0 and counts(data_dir / "data" / "nexus.sqlite")[:3] == (6, 1, 1))
        mine, fields, code = start_app(root, v1, local, real_root)
        check("re-installed app starts", fields.get("platform") == "windows", str(fields)); stop_app(mine)
        manage("uninstall", "--root", root)
    finally:
        mine_now = [p for p in acc.list_nexus_processes() if p["pid"] not in real_before]
        acc.kill(mine_now)
        time.sleep(1)
        real_after = {p["pid"] for p in acc.list_nexus_processes()}
        check("your Windows start-up (HKCU Run) entries are identical after the whole lifecycle", run_key_state() == run_before,
              f"{len(run_before)} entries")
        check("your own Nexus processes were never touched (same PIDs before/after)", real_before == real_after, f"{sorted(real_before)} -> {sorted(real_after)}")
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    out = ROOT.parent / "sandbox-run" / "lifecycle-results.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"package": package.name, "version": v1, "results": RESULTS}, indent=2, ensure_ascii=False), encoding="utf-8")
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
