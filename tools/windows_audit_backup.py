"""Consistent local backup; prints only paths, counts and integrity, never clips."""
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main():
    root = Path(os.environ["LOCALAPPDATA"]) / "Nexus"
    target = root / "backups" / datetime.now(timezone.utc).strftime("windows-audit-%Y%m%dT%H%M%S%fZ")
    target.mkdir(parents=True, exist_ok=False)
    report = {"backup": str(target), "files": {}}
    source = root / "data" / "nexus.sqlite"
    if source.exists():
        dest = target / "nexus.sqlite"
        # SQLite online backup includes committed WAL data, unlike file copy.
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(dest) as out:
                src.backup(out)
                check = out.execute("PRAGMA integrity_check").fetchall()
                if check != [("ok",)]:
                    raise RuntimeError("Backup integrity check failed")
                report["counts"] = {
                    table: out.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    for table in ("clip_items", "pinboards", "pinboard_items", "stack_state")
                }
                report["mercado_libre_preserved"] = bool(out.execute(
                    "SELECT COUNT(*) FROM pinboards WHERE name = ?", ("Mercado Libre",)
                ).fetchone()[0])
                report["mercado_libre_normalized_matches"] = out.execute(
                    "SELECT COUNT(*) FROM pinboards WHERE lower(replace(trim(name), ?, ?)) = ?",
                    (" ", "", "mercadolibre"),
                ).fetchone()[0]
        report["integrity"] = "ok"
        report["files"][dest.name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    for name in ("settings.json", "theme.json"):
        source = root / "config" / name
        if source.exists():
            dest = target / name
            shutil.copy2(source, dest)
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            if digest != hashlib.sha256(source.read_bytes()).hexdigest():
                raise RuntimeError("Configuration backup verification failed")
            report["files"][name] = digest
    (target / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
