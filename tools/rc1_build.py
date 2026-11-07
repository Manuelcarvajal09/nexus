"""Build the Nexus Windows release-candidate package LOCALLY (never publishes anything).

    python tools/rc1_build.py --out dist-rc1 [--python C:\\path\\to\\python.exe] [--skip-wheelhouse]

What it does
  1. refuses to build from a dirty tree (tracked files; file-mode-only changes are ignored)
  2. exports the HEAD commit with `git archive` -> the wheel is built from the commit, not the working tree
  3. creates a throw-away build venv with pinned, hash-checked setuptools/wheel
  4. builds the wheel TWICE in different directories with SOURCE_DATE_EPOCH = commit time and
     fails unless both builds are byte-identical (reproducible build)
  5. downloads the pinned runtime dependencies into wheelhouse/ with --require-hashes
  6. assembles dist: wheel, wheelhouse, lock, tools, release docs, sandbox files, manifest.json, SHA256SUMS
  7. runs tools/rc1_verify.py on the result

Needs network ONLY for step 3 and 5 on the build machine (pip); the installed package needs none.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import rc1_verify  # noqa: E402

BUILD_REQUIREMENTS = ROOT / "release" / "requirements-build.lock"
RUNTIME_LOCK = ROOT / "release" / "requirements-rc1.lock"
SHIPPED_TOOLS = ("rc1_manage.py", "rc1_verify.py", "windows_db_tools.py")
SHIPPED_DOCS = ("README_RC1.md", "CHANGELOG_RC1.md")


def _pins(lock: Path) -> list[str]:
    return [line.split(" ")[0] for line in lock.read_text(encoding="utf-8").splitlines()
            if "==" in line and not line.startswith((" ", "#"))]


def run(cmd, **kwargs):
    kwargs.setdefault("check", True)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run([str(c) for c in cmd], **kwargs)


def git(*args) -> str:
    return run(["git", "-C", ROOT, *args]).stdout.strip()


def ensure_clean_tree():
    dirty = run(["git", "-C", ROOT, "-c", "core.fileMode=false", "status", "--porcelain", "--untracked-files=no"]).stdout.strip()
    if dirty:
        raise SystemExit("Refusing to build from a dirty tree (commit first):\n" + dirty)


def export_commit(dest: Path) -> None:
    archive = dest.with_suffix(".tar")
    run(["git", "-C", ROOT, "archive", "--format=tar", "-o", archive, "HEAD"])
    dest.mkdir(parents=True)
    run(["tar", "-xf", archive, "-C", dest])
    archive.unlink()


def make_build_venv(base_python: str, venv: Path):
    run([base_python, "-m", "venv", venv])
    python = venv / "Scripts" / "python.exe"
    run([python, "-m", "pip", "install", "--quiet", "--require-hashes", "-r", BUILD_REQUIREMENTS])
    return python


def build_wheel(python: Path, source: Path, out: Path, epoch: str) -> Path:
    out.mkdir(parents=True)
    env = {**os.environ, "SOURCE_DATE_EPOCH": epoch, "PYTHONHASHSEED": "0"}
    run([python, "-m", "pip", "wheel", "--quiet", "--no-deps", "--no-build-isolation", "--no-cache-dir",
         "-w", out, source], env=env)
    wheels = list(out.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one wheel, found {wheels}")
    return wheels[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="dist-rc1")
    parser.add_argument("--python", default=sys.executable, help="base CPython 3.11 used for the temporary venvs")
    parser.add_argument("--skip-wheelhouse", action="store_true", help="skip the dependency download (verification will then fail)")
    args = parser.parse_args()

    ensure_clean_tree()
    commit = git("rev-parse", "HEAD")
    epoch = git("log", "-1", "--format=%ct")
    out = (ROOT / args.out).resolve()
    if out.exists():
        raise SystemExit(f"{out} already exists; remove it first (this tool never overwrites a package)")
    print(f"commit {commit[:12]}  epoch {epoch}")

    with tempfile.TemporaryDirectory(prefix="nexus-rc1-build-") as tmp:
        tmp = Path(tmp)
        source = tmp / "src-export"
        export_commit(source)
        python = make_build_venv(args.python, tmp / "buildvenv")
        first = build_wheel(python, source, tmp / "wheel-a", epoch)
        second = build_wheel(python, source, tmp / "wheel-b", epoch)
        hash_a, hash_b = rc1_verify.sha256_file(first), rc1_verify.sha256_file(second)
        print(f"build A {hash_a}\nbuild B {hash_b}")
        if hash_a != hash_b or first.name != second.name:
            raise SystemExit("NOT REPRODUCIBLE: the two builds differ")
        print("reproducible: both builds are byte-identical")

        out.mkdir()
        shutil.copy2(first, out / first.name)
        shutil.copy2(RUNTIME_LOCK, out / "requirements-rc1.lock")
        if not args.skip_wheelhouse:
            (out / "wheelhouse").mkdir()
            run([python, "-m", "pip", "download", "--quiet", "--only-binary=:all:", "--no-deps", "--require-hashes",
                 "--python-version", "3.11", "--platform", "win_amd64", "-r", RUNTIME_LOCK, "-d", out / "wheelhouse"])
        (out / "tools").mkdir()
        for name in SHIPPED_TOOLS:
            shutil.copy2(ROOT / "tools" / name, out / "tools" / name)
        for name in SHIPPED_DOCS:
            shutil.copy2(ROOT / "release" / name, out / name)
        # Python/OS facts of the machine that built it, for the record (no user data).
        version = first.name.split("-")[1]
        manifest = {
            "name": "nexus-windows",
            "version": version,
            "commit": commit,
            "commit_epoch": int(epoch),
            "built_with": {"python": run([python, "--version"]).stdout.strip(),
                           "build_requirements": _pins(BUILD_REQUIREMENTS)},
            "runtime_requires": _pins(RUNTIME_LOCK),
            "target": {"os": "Windows 10/11 x64", "python": "CPython 3.11"},
            "wheel": {"file": first.name, "sha256": hash_a},
            "reproducible": True,
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        # Sandbox files are generated afterwards by tools/rc1_sandbox_prepare.py (they need absolute host paths).
        names = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        (out / "SHA256SUMS").write_text(
            "".join(f"{rc1_verify.sha256_file(out / n)}  {n}\n" for n in names), encoding="utf-8", newline="\n")

    for note in rc1_verify.verify_package(out):
        print("OK  ", note)
    print(f"\nPACKAGE READY: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
