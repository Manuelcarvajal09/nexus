"""Generate the Windows Sandbox configuration for Nexus RC1 and verify its protections (host side).

    python tools/rc1_sandbox_prepare.py [--results DIR] [--out FILE]

This only writes files. It does NOT enable Windows features, start the sandbox, read the clipboard or
touch any Nexus process. Starting the sandbox is a manual step (double-click the generated .wsb).
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "release" / "sandbox" / "nexus-rc1.wsb.template"
REQUIRED_SETTINGS = {
    "ClipboardRedirection": "Disable",
    "Networking": "Disable",
    "AudioInput": "Disable",
    "VideoInput": "Disable",
    "PrinterRedirection": "Disable",
    "ProtectedClient": "Enable",
}
WRITABLE_SANDBOX_FOLDERS = {"C:\\Results"}


class SandboxConfigError(Exception):
    pass


def render(template_text: str, repo: Path, python_dir: Path, results: Path) -> str:
    for placeholder in ("@REPO@", "@PYTHON@", "@RESULTS@"):
        if placeholder not in template_text:
            raise SandboxConfigError(f"template lacks {placeholder}")
    return (template_text.replace("@REPO@", str(repo)).replace("@PYTHON@", str(python_dir))
            .replace("@RESULTS@", str(results)))


def validate(xml_text: str, repo: Path | None = None) -> list[str]:
    """Raise SandboxConfigError unless every required protection is present. Returns what was verified."""
    try:
        config = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SandboxConfigError(f"not valid XML: {exc}") from exc
    verified = []
    for tag, wanted in REQUIRED_SETTINGS.items():
        node = config.find(tag)
        if node is None or (node.text or "").strip() != wanted:
            raise SandboxConfigError(f"{tag} must be {wanted}")
        verified.append(f"{tag}={wanted}")
    folders = config.findall("./MappedFolders/MappedFolder")
    if not folders:
        raise SandboxConfigError("no mapped folders")
    writable = []
    for folder in folders:
        sandbox_folder = folder.findtext("SandboxFolder", "").strip()
        read_only = folder.findtext("ReadOnly", "").strip().lower()
        if read_only not in ("true", "false"):
            raise SandboxConfigError(f"{sandbox_folder}: ReadOnly must be explicit")
        if read_only == "false":
            writable.append(sandbox_folder)
    if set(writable) != WRITABLE_SANDBOX_FOLDERS:
        raise SandboxConfigError(f"only {sorted(WRITABLE_SANDBOX_FOLDERS)} may be writable, found {writable}")
    repo_folder = next((f for f in folders if f.findtext("SandboxFolder", "").strip() == "C:\\Repo"), None)
    if repo_folder is None or repo_folder.findtext("ReadOnly", "").strip().lower() != "true":
        raise SandboxConfigError("the repository must be mapped read-only at C:\\Repo")
    for folder in folders:  # the writable results folder must not be (inside) the repository or a user's data folder
        if folder.findtext("SandboxFolder", "").strip() == "C:\\Results" and repo is not None:
            results = Path(folder.findtext("HostFolder", "")).resolve()
            if results == repo.resolve() or repo.resolve() in results.parents or results in repo.resolve().parents:
                raise SandboxConfigError("the writable results folder must be outside the repository")
    verified.append("repository read-only, host Python read-only, a single writable results folder")
    command = config.findtext("./LogonCommand/Command", "")
    if "bootstrap.ps1" not in command:
        raise SandboxConfigError("LogonCommand must run guest\\bootstrap.ps1 (isolation checks come first)")
    verified.append("logon command runs the isolation checks first")
    return verified


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", default=str(ROOT.parent / "sandbox-results"),
                        help="EMPTY host folder that receives the logs (the only writable mapping)")
    parser.add_argument("--out", default=str(ROOT.parent / "sandbox-run" / "nexus-rc1.wsb"))
    args = parser.parse_args(argv)
    results = Path(args.results).resolve()
    python_dir = Path(sys.base_prefix).resolve()
    results.mkdir(parents=True, exist_ok=True)
    if any(results.iterdir()):
        print(f"NOTE: {results} is not empty; use a fresh folder for a clean run")
    text = render(TEMPLATE.read_text(encoding="utf-8"), ROOT, python_dir, results)
    try:
        for line in validate(text, ROOT):
            print("OK  ", line)
    except SandboxConfigError as exc:
        print("FAIL", exc)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"written {out}\nrepo (read-only): {ROOT}\npython (read-only): {python_dir}\nresults (writable): {results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
