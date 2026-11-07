"""The sandbox configuration must keep every protection, and the in-sandbox tests must refuse to run elsewhere."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import rc1_sandbox_prepare as prep  # noqa: E402


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rendered(tmp_path, python_dir=None):
    results = tmp_path / "results"
    results.mkdir(exist_ok=True)
    return prep.render(prep.TEMPLATE.read_text(encoding="utf-8"), ROOT, python_dir or tmp_path / "py", results)


def test_template_renders_and_passes_every_protection_check(tmp_path):
    verified = prep.validate(rendered(tmp_path), ROOT)
    assert any("ClipboardRedirection=Disable" in v for v in verified)
    assert any("Networking=Disable" in v for v in verified)


@pytest.mark.parametrize("before,after,message", [
    ("<ClipboardRedirection>Disable", "<ClipboardRedirection>Enable", "ClipboardRedirection"),
    ("<Networking>Disable", "<Networking>Default", "Networking"),
    ("<ProtectedClient>Enable", "<ProtectedClient>Disable", "ProtectedClient"),
    ("<AudioInput>Disable", "<AudioInput>Enable", "AudioInput"),
])
def test_weakening_any_setting_is_rejected(tmp_path, before, after, message):
    text = rendered(tmp_path)
    assert before in text
    with pytest.raises(prep.SandboxConfigError, match=message):
        prep.validate(text.replace(before, after), ROOT)


def test_repository_must_stay_read_only(tmp_path):
    text = rendered(tmp_path)
    repo_block = text.index(r"C:\Repo")
    weakened = text[:repo_block] + text[repo_block:].replace("<ReadOnly>true", "<ReadOnly>false", 1)
    with pytest.raises(prep.SandboxConfigError):
        prep.validate(weakened, ROOT)


def test_only_the_results_folder_may_be_writable(tmp_path):
    text = rendered(tmp_path).replace("<ReadOnly>true</ReadOnly>", "<ReadOnly>false</ReadOnly>")
    with pytest.raises(prep.SandboxConfigError, match="writable"):
        prep.validate(text, ROOT)


def test_results_folder_cannot_be_inside_the_repository():
    inside = ROOT / "results-inside"
    text = prep.render(prep.TEMPLATE.read_text(encoding="utf-8"), ROOT, Path("C:/py"), inside)
    with pytest.raises(prep.SandboxConfigError, match="outside the repository"):
        prep.validate(text, ROOT)


def test_logon_command_runs_the_isolation_checks_first(tmp_path):
    text = rendered(tmp_path).replace("bootstrap.ps1", "something_else.ps1")
    with pytest.raises(prep.SandboxConfigError, match="bootstrap"):
        prep.validate(text, ROOT)


def test_bootstrap_stops_before_any_clipboard_test_when_isolation_fails():
    script = (ROOT / "release" / "sandbox" / "guest" / "bootstrap.ps1").read_text(encoding="utf-8")
    gate = script.index("if (-not $allOk)")
    assert gate < script.index("install --package") < script.index("functional_tests.py' --results")
    assert "exit 1" in script[gate:gate + 300]
    # the clipboard is only written by the isolation step itself (sandbox sentinel), never before the gate
    assert script.index("Set-Clipboard") > script.index("Check 'sandbox clipboard starts empty'")


def test_functional_tests_refuse_outside_windows_sandbox(tmp_path, monkeypatch):
    module = load(ROOT / "release" / "sandbox" / "guest" / "functional_tests.py", "functional_tests")
    report = tmp_path / "isolation.json"
    report.write_text(json.dumps({"all_ok": True}), encoding="utf-8")
    monkeypatch.setenv("USERNAME", "Admin")
    with pytest.raises(SystemExit, match="REFUSED"):
        module.guard(report)                                    # right report, wrong machine
    monkeypatch.setenv("USERNAME", "WDAGUtilityAccount")
    report.write_text(json.dumps({"all_ok": False}), encoding="utf-8")
    with pytest.raises(SystemExit, match="REFUSED"):
        module.guard(report)                                    # right machine, failed isolation
    with pytest.raises(SystemExit, match="REFUSED"):
        module.guard(tmp_path / "missing.json")
    report.write_text(json.dumps({"all_ok": True}), encoding="utf-8")
    module.guard(report)                                        # both hold: allowed


def test_dry_run_touches_nothing_and_lists_every_test(tmp_path):
    done = subprocess.run([sys.executable, str(ROOT / "release" / "sandbox" / "guest" / "functional_tests.py"),
                           "--results", str(tmp_path), "--isolation-report", str(tmp_path / "none.json"), "--dry-run"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0
    assert all(f"T{n:02d}" in done.stdout for n in range(1, 13))
    assert "would refuse" in done.stdout          # on this (non-sandbox) machine
    assert not (tmp_path / "functional_results.json").exists()


def test_functional_tests_declare_every_test_they_implement():
    module = load(ROOT / "release" / "sandbox" / "guest" / "functional_tests.py", "functional_tests2")
    ids = {tid for tid, _ in module.TESTS}
    assert ids == {f"T{n:02d}" for n in range(1, 13)}
