"""Safe data operations for upgrading Nexus and, if needed, rolling back (stdlib only).

    python tools/windows_db_tools.py backup  SOURCE.sqlite DEST_DIR [--label TEXT]
    python tools/windows_db_tools.py verify  BACKUP.sqlite
    python tools/windows_db_tools.py merge-newer --backup BACKUP.sqlite --current CURRENT.sqlite --out NEW.sqlite
    python tools/windows_db_tools.py protect-titled DB.sqlite

* backup      SQLite online-backup API, read-only on the source (safe while Nexus runs),
              never overwrites, writes a manifest (counts + SHA-256) next to the copy.
* verify      integrity_check, counts and checksum against the manifest.
* merge-newer builds NEW from BACKUP and then adds everything CURRENT has that BACKUP
              lacks (new clips, newer timestamps, titles, Pinboards and memberships), so
              restoring a backup never loses clips captured after it was taken.
* protect-titled  adds every titled clip to a "protected" Pinboard so that an OLDER
              Nexus (which does not know titles) never purges it by retention.

Only counts and integrity results are printed, never clip content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

COUNT_TABLES = ("clip_items", "pinboards", "pinboard_items", "stack_state")
PROTECTED_BOARD = "Con título (protegidos)"


class DbToolError(RuntimeError):
    pass


def connect_ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)


def table_counts(con: sqlite3.Connection, schema: str = "main") -> dict:
    result = {}
    for table in COUNT_TABLES:
        try:
            result[table] = con.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0]
        except sqlite3.OperationalError:
            result[table] = None
    return result


def columns(con: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA {schema}.table_info({table})")]


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_path(backup: Path) -> Path:
    return backup.with_name(backup.name + ".manifest.json")


def backup(source, dest_dir, label: str = "") -> dict:
    source, dest_dir = Path(source), Path(dest_dir)
    if not source.exists():
        raise DbToolError(f"No existe la base de origen: {source}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    target = dest_dir / f"nexus-{stamp}{('-' + label) if label else ''}.sqlite"
    if target.exists() or manifest_path(target).exists():
        raise DbToolError(f"El respaldo ya existe; no se sobrescribe: {target.name}")
    src = connect_ro(source)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
            backup_counts = table_counts(dst)
            clip_columns = columns(dst, "clip_items")
        finally:
            dst.close()
        source_counts_after = table_counts(src)
    finally:
        src.close()
    manifest = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source.name,
        "file": target.name,
        "integrity": integrity,
        "counts": backup_counts,
        "source_counts_after": source_counts_after,
        "has_title_column": "title" in clip_columns,
        "sha256": sha256_file(target),
        "size": target.stat().st_size,
    }
    manifest_path(target).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if integrity != "ok":
        raise DbToolError(f"El respaldo NO pasó integrity_check ({integrity}); no lo uses.")
    manifest["path"] = str(target)
    return manifest


def verify(backup_file) -> dict:
    backup_file = Path(backup_file)
    con = connect_ro(backup_file)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        counts_now = table_counts(con)
    except sqlite3.DatabaseError as error:
        integrity, counts_now = f"error: {error}", {}
    finally:
        con.close()
    report = {"file": backup_file.name, "integrity": integrity, "counts": counts_now, "manifest": None, "sha256_matches": None}
    manifest_file = manifest_path(backup_file)
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        report["manifest"] = True
        report["sha256_matches"] = manifest["sha256"] == sha256_file(backup_file)
        report["counts_match_manifest"] = manifest["counts"] == counts_now
    report["ok"] = integrity == "ok" and report["sha256_matches"] is not False and report.get("counts_match_manifest") is not False
    return report


def merge_newer(backup_file, current_file, out_file) -> dict:
    backup_file, current_file, out_file = Path(backup_file), Path(current_file), Path(out_file)
    if out_file.exists():
        raise DbToolError(f"El destino ya existe; no se sobrescribe: {out_file}")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    source = connect_ro(backup_file)
    try:
        target = sqlite3.connect(str(out_file))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    con = sqlite3.connect(str(out_file), uri=True)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("ATTACH DATABASE ? AS cur", (f"file:{current_file.resolve().as_posix()}?mode=ro",))
        before = table_counts(con)
        main_cols, cur_cols = columns(con, "clip_items"), columns(con, "clip_items", "cur")
        shared = [c for c in main_cols if c in cur_cols and c != "id"]
        col_list = ", ".join(shared)
        added = con.execute(
            f"INSERT INTO main.clip_items ({col_list}) SELECT {col_list} FROM cur.clip_items "
            "WHERE content_hash NOT IN (SELECT content_hash FROM main.clip_items)").rowcount
        refreshed = con.execute(
            "UPDATE main.clip_items SET updated_at = (SELECT c.updated_at FROM cur.clip_items c "
            "WHERE c.content_hash = main.clip_items.content_hash) WHERE EXISTS (SELECT 1 FROM cur.clip_items c "
            "WHERE c.content_hash = main.clip_items.content_hash AND c.updated_at > main.clip_items.updated_at)").rowcount
        titled = 0
        if "title" in main_cols and "title" in cur_cols:
            titled = con.execute(
                "UPDATE main.clip_items SET title = (SELECT c.title FROM cur.clip_items c WHERE c.content_hash = main.clip_items.content_hash), "
                "title_search = (SELECT c.title_search FROM cur.clip_items c WHERE c.content_hash = main.clip_items.content_hash) "
                "WHERE title IS NULL AND EXISTS (SELECT 1 FROM cur.clip_items c WHERE c.content_hash = main.clip_items.content_hash AND c.title IS NOT NULL)").rowcount
        boards = con.execute(
            "INSERT OR IGNORE INTO main.pinboards (id, name, color_hex, emoji, sort_order, created_at) "
            "SELECT id, name, color_hex, emoji, sort_order, created_at FROM cur.pinboards "
            "WHERE id NOT IN (SELECT id FROM main.pinboards)").rowcount
        links = con.execute(
            "INSERT OR IGNORE INTO main.pinboard_items (pinboard_id, item_id) "
            "SELECT cp.pinboard_id, m.id FROM cur.pinboard_items cp "
            "JOIN cur.clip_items cc ON cc.id = cp.item_id "
            "JOIN main.clip_items m ON m.content_hash = cc.content_hash "
            "WHERE cp.pinboard_id IN (SELECT id FROM main.pinboards)").rowcount
        con.commit()
        con.execute("DETACH DATABASE cur")
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        after = table_counts(con)
    finally:
        con.close()
    if integrity != "ok":
        raise DbToolError(f"La base fusionada NO pasó integrity_check ({integrity}).")
    return {"integrity": integrity, "backup_counts": before, "merged_counts": after, "clips_added": added,
            "timestamps_refreshed": refreshed, "titles_recovered": titled, "pinboards_added": boards,
            "memberships_added": links, "out": str(out_file)}


def protect_titled(db_file, board_name: str = PROTECTED_BOARD) -> dict:
    con = sqlite3.connect(str(db_file))
    try:
        con.execute("PRAGMA foreign_keys = ON")
        if "title" not in columns(con, "clip_items"):
            return {"protected": 0, "board_id": None, "note": "la base no tiene títulos"}
        row = con.execute("SELECT id FROM pinboards WHERE name = ?", (board_name,)).fetchone()
        if row is None:
            order = con.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM pinboards").fetchone()[0]
            cursor = con.execute(
                "INSERT INTO pinboards (name, color_hex, emoji, sort_order, created_at) VALUES (?, '#8E8E93', '🔒', ?, ?)",
                (board_name, order, time.time()))
            board_id = cursor.lastrowid
        else:
            board_id = row[0]
        protected = con.execute(
            "INSERT OR IGNORE INTO pinboard_items (pinboard_id, item_id) SELECT ?, id FROM clip_items WHERE title IS NOT NULL",
            (board_id,)).rowcount
        con.commit()
        return {"protected": protected, "board_id": board_id}
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup"); b.add_argument("source"); b.add_argument("dest_dir"); b.add_argument("--label", default="")
    v = sub.add_parser("verify"); v.add_argument("backup")
    m = sub.add_parser("merge-newer")
    m.add_argument("--backup", required=True); m.add_argument("--current", required=True); m.add_argument("--out", required=True)
    p = sub.add_parser("protect-titled"); p.add_argument("db")
    args = parser.parse_args()
    try:
        if args.command == "backup":
            result = backup(args.source, args.dest_dir, args.label)
        elif args.command == "verify":
            result = verify(args.backup)
        elif args.command == "merge-newer":
            result = merge_newer(args.backup, args.current, args.out)
        else:
            result = protect_titled(args.db)
    except DbToolError as error:
        print(f"ABORTADO: {error}")
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
