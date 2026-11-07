"""Package integrity (rc1_verify) and install/upgrade/rollback/uninstall logic (rc1_manage), without building real wheels."""
import base64
import csv
import hashlib
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import rc1_manage as manage  # noqa: E402
import rc1_verify as verify  # noqa: E402


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_package(tmp_path, version="0.2.0rc1", requires="PySide6-Essentials>=6.7", extra_wheel_files=None):
    dist = tmp_path / f"dist-{version}"
    (dist / "wheelhouse").mkdir(parents=True)
    (dist / "tools").mkdir()
    info = f"nexus_windows-{version}.dist-info"
    files = {
        "nexus_linux/__init__.py": f'__version__ = "{version}"\n'.encode(),
        "nexus_linux/windows_app.py": b"# app\n",
        "nexus_linux/assets/icons/nexus.ico": b"ico",
        "nexus_linux/assets/icons/nexus-256.png": b"png",
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: nexus-windows\nVersion: {version}\nRequires-Dist: {requires}\n"
                            'Requires-Dist: pytest>=8; extra == "dev"\n'.encode().decode().encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\n",
    }
    files.update(extra_wheel_files or {})
    rows = [[n, _record_hash(d), str(len(d))] for n, d in files.items()] + [[f"{info}/RECORD", "", ""]]
    buffer = io.StringIO(); csv.writer(buffer).writerows(rows)
    wheel_name = f"nexus_windows-{version}-py3-none-any.whl"
    with zipfile.ZipFile(dist / wheel_name, "w") as wheel:
        for n, d in files.items():
            wheel.writestr(n, d)
        wheel.writestr(f"{info}/RECORD", buffer.getvalue())
    pins = []
    for name, ver, tag in (("PySide6-Essentials", "6.12.0", "pyside6_essentials"), ("shiboken6", "6.12.0", "shiboken6")):
        data = f"fake wheel {name}".encode()
        file = dist / "wheelhouse" / f"{tag}-{ver}-cp310-abi3-win_amd64.whl"
        file.write_bytes(data)
        pins.append(f"{name}=={ver} \\\n    --hash=sha256:{hashlib.sha256(data).hexdigest()}\n")
    (dist / "requirements-rc1.lock").write_text("# pins\n" + "".join(pins), encoding="utf-8")
    for tool in ("rc1_manage.py", "rc1_verify.py", "windows_db_tools.py"):
        shutil.copy2(ROOT / "tools" / tool, dist / "tools" / tool)
    (dist / "README_RC1.md").write_text("readme", encoding="utf-8")
    manifest = {"name": "nexus-windows", "version": version, "commit": "abc123",
                "wheel": {"file": wheel_name, "sha256": verify.sha256_file(dist / wheel_name)}}
    (dist / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    seal(dist)
    return dist


def seal(dist):
    names = sorted(p.relative_to(dist).as_posix() for p in dist.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (dist / "SHA256SUMS").write_text("".join(f"{verify.sha256_file(dist / n)}  {n}\n" for n in names), encoding="utf-8")


# ----------------------------------------------------------------- integrity
def test_valid_package_verifies(tmp_path):
    notes = verify.verify_package(make_package(tmp_path))
    assert any("SHA256SUMS ok" in n for n in notes) and any("wheelhouse ok" in n for n in notes)


def test_tampered_wheel_is_rejected(tmp_path):
    dist = make_package(tmp_path)
    wheel = next(dist.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"x")
    with pytest.raises(verify.PackageError, match="hash mismatch"):
        verify.verify_package(dist)


def test_file_added_after_sealing_is_rejected(tmp_path):
    dist = make_package(tmp_path)
    (dist / "extra.txt").write_text("surprise")
    with pytest.raises(verify.PackageError, match="not in SHA256SUMS"):
        verify.verify_package(dist)


def test_resealed_but_modified_wheelhouse_is_caught_by_the_lock(tmp_path):
    dist = make_package(tmp_path)
    next((dist / "wheelhouse").glob("shiboken6*")).write_bytes(b"swapped")
    seal(dist)                                  # an attacker can re-seal the sums, but not the pinned hashes
    with pytest.raises(verify.PackageError, match="not pinned"):
        verify.verify_package(dist)


def test_wrong_runtime_dependency_is_rejected(tmp_path):
    with pytest.raises(verify.PackageError, match="exactly PySide6-Essentials"):
        verify.verify_package(make_package(tmp_path, requires="PySide6>=6.7"))


def test_data_files_inside_the_wheel_are_rejected(tmp_path):
    for name in ("nexus_linux/clips.sqlite", "nexus_linux/settings.json", "nexus_linux/x.lnk", "nexus_linux/__pycache__/a.pyc"):
        with pytest.raises(verify.PackageError, match="forbidden"):
            verify.verify_package(make_package(tmp_path / name.replace("/", "_"), extra_wheel_files={name: b"data"}))


def test_manifest_version_must_match_wheel_metadata(tmp_path):
    dist = make_package(tmp_path)
    manifest = json.loads((dist / "manifest.json").read_text())
    manifest["version"] = "9.9.9"
    (dist / "manifest.json").write_text(json.dumps(manifest))
    seal(dist)
    with pytest.raises(verify.PackageError, match="METADATA version"):
        verify.verify_package(dist)


# ----------------------------------------------------------------- manage
class FakeRunner:
    """Pretends to be subprocess.run for venv/pip/python; records commands."""
    def __init__(self, version, smoke_ok=True, backup_ok=True):
        self.version, self.smoke_ok, self.backup_ok, self.commands = version, smoke_ok, backup_ok, []

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.commands.append(cmd)
        done = SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[1:3] == ["-m", "venv"]:
            scripts = Path(cmd[3]) / "Scripts"
            scripts.mkdir(parents=True)
            (scripts / "python.exe").write_text(""); (scripts / "pythonw.exe").write_text("")
        elif "-c" in cmd:
            done.stdout = (self.version if self.smoke_ok else "wrong") + "\n"
        elif any(c.endswith("windows_db_tools.py") for c in cmd):
            if cmd[2] == "backup":
                Path(cmd[4]).mkdir(parents=True, exist_ok=True)
                if self.backup_ok:
                    (Path(cmd[4]) / "nexus-backup.sqlite").write_text("backup")
                else:
                    done.returncode = 1
        return done


def idle(_root):
    return []


def test_install_uses_only_the_offline_hash_pinned_wheelhouse(tmp_path):
    dist, root = make_package(tmp_path), tmp_path / "root"
    runner = FakeRunner("0.2.0rc1")
    state = manage.install(dist, root, run=runner, running=idle, log=lambda *a: None)
    assert state == {"current": "0.2.0rc1", "previous": []}
    pip_calls = [c for c in runner.commands if "pip" in c and "install" in c]
    assert len(pip_calls) == 2 and all("--no-index" in c for c in pip_calls)
    assert "--require-hashes" in pip_calls[0] and "--no-deps" in pip_calls[1]
    launcher = (root / "Nexus.cmd").read_text()
    assert "pythonw.exe" in launcher and "0.2.0rc1" in launcher and "nexus_linux.windows_app" in launcher
    assert (root / manage.MARKER).exists()


def test_install_refuses_a_tampered_package_before_creating_anything(tmp_path):
    dist = make_package(tmp_path)
    wheel = next(dist.glob("*.whl")); wheel.write_bytes(wheel.read_bytes() + b"x")
    root = tmp_path / "root"
    with pytest.raises(verify.PackageError):
        manage.install(dist, root, run=FakeRunner("0.2.0rc1"), running=idle, log=lambda *a: None)
    assert not (root / "versions").exists()


def test_failed_smoke_test_never_leaves_a_half_installed_version(tmp_path):
    dist, root = make_package(tmp_path), tmp_path / "root"
    with pytest.raises(manage.ManageError, match="smoke"):
        manage.install(dist, root, run=FakeRunner("0.2.0rc1", smoke_ok=False), running=idle, log=lambda *a: None)
    assert not (root / "versions" / "0.2.0rc1").exists()
    assert manage.read_state(root)["current"] is None


def test_upgrade_backs_up_the_database_first_and_keeps_the_previous_version(tmp_path):
    root, data = tmp_path / "root", tmp_path / "data"
    (data / "data").mkdir(parents=True); (data / "data" / "nexus.sqlite").write_text("db")
    manage.install(make_package(tmp_path / "a", "0.2.0rc1"), root, run=FakeRunner("0.2.0rc1"), running=idle, log=lambda *a: None)
    runner = FakeRunner("0.2.0rc2")
    state = manage.install(make_package(tmp_path / "b", "0.2.0rc2"), root, data, run=runner, running=idle, log=lambda *a: None)
    assert state == {"current": "0.2.0rc2", "previous": ["0.2.0rc1"]}
    order = [c[2] for c in runner.commands if c[1].endswith("windows_db_tools.py")]
    assert order == ["backup", "verify"]
    assert (root / "versions" / "0.2.0rc1" / "venv").exists()          # old version intact for rollback
    assert (data / "data" / "nexus.sqlite").read_text() == "db"          # data untouched


def test_upgrade_aborts_when_the_backup_fails(tmp_path):
    root, data = tmp_path / "root", tmp_path / "data"
    (data / "data").mkdir(parents=True); (data / "data" / "nexus.sqlite").write_text("db")
    manage.install(make_package(tmp_path / "a", "0.2.0rc1"), root, run=FakeRunner("0.2.0rc1"), running=idle, log=lambda *a: None)
    with pytest.raises(manage.ManageError, match="backup"):
        manage.install(make_package(tmp_path / "b", "0.2.0rc2"), root, data, run=FakeRunner("0.2.0rc2", backup_ok=False),
                       running=idle, log=lambda *a: None)
    assert manage.read_state(root)["current"] == "0.2.0rc1"
    assert not (root / "versions" / "0.2.0rc2").exists()


def test_upgrade_refuses_while_nexus_is_running(tmp_path):
    root = tmp_path / "root"
    manage.install(make_package(tmp_path / "a", "0.2.0rc1"), root, run=FakeRunner("0.2.0rc1"), running=idle, log=lambda *a: None)
    with pytest.raises(manage.ManageError, match="running"):
        manage.install(make_package(tmp_path / "b", "0.2.0rc2"), root, run=FakeRunner("0.2.0rc2"),
                       running=lambda r: ["x"], log=lambda *a: None)


def test_rollback_switches_back_and_forward_without_touching_data(tmp_path):
    root = tmp_path / "root"
    for ver in ("0.2.0rc1", "0.2.0rc2"):
        manage.install(make_package(tmp_path / ver, ver), root, run=FakeRunner(ver), running=idle, log=lambda *a: None)
    state = manage.rollback(root, running=idle, log=lambda *a: None)
    assert state["current"] == "0.2.0rc1" and "0.2.0rc1" in (root / "Nexus.cmd").read_text()
    state = manage.rollback(root, to="0.2.0rc2", running=idle, log=lambda *a: None)
    assert state["current"] == "0.2.0rc2"
    with pytest.raises(manage.ManageError, match="not on disk"):
        manage.rollback(root, to="9.9.9", running=idle, log=lambda *a: None)


def test_rollback_with_nothing_older_is_refused(tmp_path):
    root = tmp_path / "root"
    manage.install(make_package(tmp_path / "a"), root, run=FakeRunner("0.2.0rc1"), running=idle, log=lambda *a: None)
    with pytest.raises(manage.ManageError, match="no previous"):
        manage.rollback(root, running=idle, log=lambda *a: None)


def test_uninstall_removes_only_the_install_root_and_keeps_user_data(tmp_path):
    root, data = tmp_path / "root", tmp_path / "data"
    (data / "data").mkdir(parents=True); (data / "data" / "nexus.sqlite").write_text("db")
    manage.install(make_package(tmp_path / "a"), root, run=FakeRunner("0.2.0rc1"), running=idle, log=lambda *a: None)
    manage.uninstall(root, running=idle, log=lambda *a: None)
    assert not root.exists() and (data / "data" / "nexus.sqlite").exists()


def test_uninstall_refuses_a_folder_it_did_not_create(tmp_path):
    stranger = tmp_path / "precious"
    stranger.mkdir(); (stranger / "file.txt").write_text("keep")
    with pytest.raises(manage.ManageError, match="refusing"):
        manage.uninstall(stranger, running=idle, log=lambda *a: None)
    assert (stranger / "file.txt").exists()


def test_removing_data_needs_an_explicit_flag_and_confirmation(tmp_path):
    root, data = tmp_path / "root", tmp_path / "data"
    (data / "data").mkdir(parents=True); (data / "data" / "nexus.sqlite").write_text("db")
    manage.install(make_package(tmp_path / "a"), root, run=FakeRunner("0.2.0rc1"), running=idle, log=lambda *a: None)
    with pytest.raises(manage.ManageError, match="--yes"):
        manage.uninstall(root, remove_data=data, yes=False, running=idle, log=lambda *a: None)
    assert (data / "data" / "nexus.sqlite").exists() and root.exists()
    with pytest.raises(manage.ManageError, match="does not look like"):
        manage.uninstall(root, remove_data=tmp_path, yes=True, running=idle, log=lambda *a: None)


def test_real_install_locations_need_an_explicit_override(tmp_path):
    outside = Path.home() / "definitely-not-temp-nexus-test"
    with pytest.raises(manage.ManageError, match="allow-real-install"):
        manage.check_root(outside, allow_real=False)
    assert manage.check_root(outside, allow_real=True) == outside.resolve()
    assert manage.check_root(tmp_path / "ok", allow_real=False)


def test_cli_requires_an_explicit_root():
    with pytest.raises(SystemExit):
        manage.main(["install", "--package", "x"])
