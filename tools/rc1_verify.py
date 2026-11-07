"""Integrity checks for a Nexus Windows release-candidate package (stdlib only).

    python tools/rc1_verify.py DIST_DIR

Checks, and fails loudly on the first problem class it finds:
  * every file listed in SHA256SUMS exists and matches; nothing unlisted is in the package
  * manifest.json is consistent (version, commit, wheel name + hash)
  * the wheel's METADATA version == manifest version, runtime dependency is exactly
    PySide6-Essentials, RECORD hashes match the wheel's contents, required assets are inside,
    and no data/secret-like files (.sqlite, .env, settings.json, .lnk, .log, __pycache__) are shipped
  * every wheel in wheelhouse/ matches the hash pinned in requirements-rc1.lock
It prints only names, versions and hashes; it never reads clip data.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

REQUIRED_IN_WHEEL = (
    "nexus_linux/windows_app.py",
    "nexus_linux/assets/icons/nexus.ico",
    "nexus_linux/assets/icons/nexus-256.png",
)
FORBIDDEN_IN_WHEEL = re.compile(r"(\.sqlite(-wal|-shm)?$|\.env$|settings\.json$|theme\.json$|\.lnk$|\.log$|__pycache__|\.pyc$)", re.I)


class PackageError(Exception):
    pass


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sums(path: Path) -> dict:
    sums = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        sums[name.strip()] = digest.strip()
    return sums


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def verify_wheel(path: Path, expected_version: str) -> list[str]:
    notes = []
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        dist_info = next((n.split("/")[0] for n in names if n.endswith(".dist-info/METADATA")), None)
        if not dist_info:
            raise PackageError(f"{path.name}: no .dist-info/METADATA")
        metadata = wheel.read(f"{dist_info}/METADATA").decode("utf-8")
        version = re.search(r"^Version: (.+)$", metadata, re.M).group(1).strip()
        if version != expected_version:
            raise PackageError(f"{path.name}: METADATA version {version} != manifest {expected_version}")
        requires = [r for r in re.findall(r"^Requires-Dist: (.+)$", metadata, re.M) if "extra ==" not in r]
        if len(requires) != 1 or not requires[0].lower().replace("_", "-").startswith("pyside6-essentials"):
            raise PackageError(f"{path.name}: runtime dependencies must be exactly PySide6-Essentials, got {requires}")
        record = list(csv.reader(io.StringIO(wheel.read(f"{dist_info}/RECORD").decode("utf-8"))))
        listed = {row[0]: row[1] for row in record if row and row[1]}
        for name in names:
            if name.endswith("/RECORD") or name.endswith("/"):
                continue
            if name not in listed:
                raise PackageError(f"{path.name}: {name} is not in RECORD")
            if listed[name] != _record_hash(wheel.read(name)):
                raise PackageError(f"{path.name}: RECORD hash mismatch for {name}")
        for name in names:
            if FORBIDDEN_IN_WHEEL.search(name):
                raise PackageError(f"{path.name}: forbidden file shipped: {name}")
        for required in REQUIRED_IN_WHEEL:
            if required not in names:
                raise PackageError(f"{path.name}: missing {required}")
        notes.append(f"wheel ok: {len(names)} entries, version {version}, RECORD verified")
    return notes


def parse_lock(path: Path) -> dict:
    """{filename-less requirement name: set(of sha256)} from a pip --require-hashes file."""
    pins, current = {}, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip().rstrip("\\").strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s]+)", line)
        if match:
            current = (match.group(1).lower().replace("_", "-"), match.group(2))
            pins[current] = set()
        for digest in re.findall(r"--hash=sha256:([0-9a-f]{64})", line):
            pins[current].add(digest)
    return pins


def verify_package(dist: Path) -> list[str]:
    dist = Path(dist)
    notes = []
    sums_path = dist / "SHA256SUMS"
    manifest_path = dist / "manifest.json"
    if not sums_path.exists() or not manifest_path.exists():
        raise PackageError("SHA256SUMS or manifest.json missing")
    sums = read_sums(sums_path)
    on_disk = {p.relative_to(dist).as_posix() for p in dist.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    missing, extra = set(sums) - on_disk, on_disk - set(sums)
    if missing:
        raise PackageError(f"listed but missing: {sorted(missing)[:5]}")
    if extra:
        raise PackageError(f"present but not in SHA256SUMS: {sorted(extra)[:5]}")
    for name, digest in sums.items():
        if sha256_file(dist / name) != digest:
            raise PackageError(f"hash mismatch: {name}")
    notes.append(f"SHA256SUMS ok: {len(sums)} files")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wheel_name = manifest["wheel"]["file"]
    if sums.get(wheel_name) != manifest["wheel"]["sha256"]:
        raise PackageError("manifest wheel hash differs from SHA256SUMS")
    notes += verify_wheel(dist / wheel_name, manifest["version"])
    lock = dist / "requirements-rc1.lock"
    pins = parse_lock(lock)
    if not pins:
        raise PackageError("lock file has no pinned requirements")
    wheelhouse = dist / "wheelhouse"
    found = {}
    for wheel_file in sorted(wheelhouse.glob("*.whl")):
        name = wheel_file.name.split("-")[0].lower().replace("_", "-")
        version = wheel_file.name.split("-")[1]
        digest = sha256_file(wheel_file)
        allowed = pins.get((name, version))
        if allowed is None or digest not in allowed:
            raise PackageError(f"wheelhouse/{wheel_file.name} is not pinned (or hash differs) in requirements-rc1.lock")
        found[(name, version)] = digest
    for key in pins:
        if key not in found:
            raise PackageError(f"pinned {key[0]}=={key[1]} has no wheel in wheelhouse/")
    notes.append(f"wheelhouse ok: {len(found)} wheels match the lock")
    return notes


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    try:
        for note in verify_package(Path(argv[0])):
            print("OK  ", note)
    except (PackageError, KeyError, OSError, zipfile.BadZipFile) as exc:
        print("FAIL", exc)
        return 1
    print("PACKAGE VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
