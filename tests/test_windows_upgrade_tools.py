"""Acceptance sandbox launcher and database upgrade/rollback tools (synthetic data only)."""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus_linux import windows_sandbox
from nexus_linux.storage import StorageService

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


acceptance = load_tool("windows_acceptance")
dbtools = load_tool("windows_db_tools")


# -- acceptance launcher ------------------------------------------------------

def env_for(**overrides):
    temp = Path(tempfile.gettempdir()) / "nexus-sandbox-test"
    args = dict(sandbox_local=temp, real_root=Path(r"C:\Users\Someone\AppData\Local\Nexus"), code_src=ROOT / "src")
    args.update(overrides)
    base = overrides.pop("base", None) if "base" in overrides else None
    return acceptance.build_env(base or {"QT_QPA_PLATFORM": "offscreen", "PATH": "x", "PYTHONPATH": "old"}, **{k: v for k, v in args.items() if k != "base"})


def test_offscreen_never_reaches_the_test_process():
    env = env_for()
    assert "QT_QPA_PLATFORM" not in env
    assert env["PYTHONPATH"] == str(ROOT / "src") and env["PATH"] == "x"


def test_environment_is_fully_isolated_and_safe_by_default():
    env = env_for()
    assert env[windows_sandbox.ENV_FLAG] == "1" and env["NEXUS_OPEN_ON_START"] == "0"
    assert Path(env["LOCALAPPDATA"]).name == "nexus-sandbox-test"
    assert windows_sandbox.ENV_ALLOW_HOOK not in env          # no Ctrl+V hook unless asked
    assert env[windows_sandbox.ENV_REAL_ROOT].endswith("Nexus")


def test_options_open_panel_and_hook_are_explicit():
    env = env_for(open_panel=True, allow_hook=True)
    assert env["NEXUS_OPEN_ON_START"] == "1" and env[windows_sandbox.ENV_ALLOW_HOOK] == "1"
    leaked = acceptance.build_env({windows_sandbox.ENV_ALLOW_HOOK: "1"}, sandbox_local=Path(tempfile.gettempdir()) / "x",
                                  real_root=Path(r"C:\r\Nexus"), code_src=ROOT / "src")
    assert windows_sandbox.ENV_ALLOW_HOOK not in leaked       # a stale variable is scrubbed


def test_reduced_motion_is_per_process_and_a_stale_value_is_scrubbed():
    assert env_for(reduced_motion=True)["NEXUS_REDUCED_MOTION"] == "1"
    clean = acceptance.build_env({"NEXUS_REDUCED_MOTION": "1"}, sandbox_local=Path(tempfile.gettempdir()) / "x",
                                 real_root=Path(r"C:\r\Nexus"), code_src=ROOT / "src")
    assert "NEXUS_REDUCED_MOTION" not in clean


@pytest.mark.parametrize("sandbox,real", [
    (Path(r"C:\Users\Someone\AppData\Local\Nexus"), Path(r"C:\Users\Someone\AppData\Local\Nexus")),   # same folder
    (Path(r"C:\Users\Someone\AppData\Local"), Path(r"C:\Users\Someone\AppData\Local\Nexus")),         # contains the real one
    (Path(r"C:\Users\Someone\AppData\Local\Nexus\sandbox"), Path(r"C:\Users\Someone\AppData\Local\Nexus")),
    (Path(r"C:\not-temp\sandbox"), Path(r"C:\Users\Someone\AppData\Local\Nexus")),
])
def test_unsafe_layouts_are_refused(sandbox, real):
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.build_env({}, sandbox_local=sandbox, real_root=real, code_src=ROOT / "src")


def proc(pid, created, ppid=1):
    return {"pid": pid, "ppid": ppid, "created": created}


def test_only_recorded_processes_with_the_same_creation_time_are_targeted():
    recorded = [proc(100, "2026-10-25T10:00:00"), proc(101, "2026-10-25T10:00:01", 100)]
    running = [
        proc(100, "2026-10-25T10:00:00"),          # same process
        proc(101, "2026-10-25T18:30:00", 5),        # PID reused by something else later
        proc(13352, "2026-10-21T09:30:10"),         # the user's habitual Nexus
    ]
    targets = acceptance.validate_targets(recorded, running)
    assert [p["pid"] for p in targets] == [100]
    assert 13352 not in [p["pid"] for p in targets]
    assert acceptance.validate_targets([], running) == []


@pytest.mark.parametrize("stdout,expected", [
    ("", []),
    ('{"pid":1,"ppid":2,"created":"a"}', [{"pid": 1, "ppid": 2, "created": "a"}]),
    ('[{"pid":1,"ppid":2,"created":"a"},{"pid":3,"ppid":1,"created":"b"}]', 2),
])
def test_process_listing_accepts_none_one_or_many(stdout, expected):
    runner = lambda *a, **k: SimpleNamespace(stdout=stdout)
    result = acceptance.list_nexus_processes(run=runner)
    assert result == expected if isinstance(expected, list) else len(result) == expected


def test_startup_confirmation_accepts_only_a_real_platform_sandbox(tmp_path):
    sandbox = tmp_path / "nexus-sandbox"
    good = f"run platform=windows sandbox=True data={sandbox / 'Nexus'} code={ROOT / 'src'}"
    assert acceptance.confirm_startup("noise\n" + good, sandbox)["platform"] == "windows"
    bad = {
        "offscreen": good.replace("platform=windows", "platform=offscreen"),
        "not sandbox": good.replace("sandbox=True", "sandbox=False"),
        "wrong data": f"run platform=windows sandbox=True data={tmp_path / 'real' / 'Nexus'} code=x",
        "silent": "other line",
    }
    for name, line in bad.items():
        with pytest.raises(acceptance.AcceptanceError):
            acceptance.confirm_startup(line, sandbox)


def test_the_app_logs_the_line_the_launcher_checks():
    source = (ROOT / "src" / "nexus" / "platform" / "windows" / "app.py").read_text(encoding="utf-8")
    assert '"run platform=%s sandbox=%s data=%s code=%s"' in source


def test_no_code_path_can_signal_other_processes():
    """The application and the sandbox tooling never terminate other processes."""
    forbidden = ("taskkill", "TerminateProcess", "os.kill(", "Stop-Process", "psutil")
    for path in (ROOT / "src" / "nexus").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not [word for word in forbidden if word in text], path.name
    assert "taskkill" in (ROOT / "tools" / "windows_acceptance.py").read_text(encoding="utf-8")  # only here, only recorded PIDs


# -- database tools -----------------------------------------------------------

LEGACY_SCHEMA = """
CREATE TABLE clip_items (id INTEGER PRIMARY KEY AUTOINCREMENT, content_hash TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL, text_content TEXT, image_data BLOB, file_path TEXT, source_app TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, search_text TEXT);
CREATE TABLE pinboards (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, color_hex TEXT NOT NULL,
  emoji TEXT, sort_order INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE pinboard_items (pinboard_id INTEGER NOT NULL REFERENCES pinboards(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL REFERENCES clip_items(id) ON DELETE CASCADE, PRIMARY KEY (pinboard_id, item_id));
CREATE TABLE stack_state (position INTEGER PRIMARY KEY, item_id INTEGER NOT NULL);
"""


@pytest.fixture
def live(tmp_path):
    """A 'real' database in WAL mode with an open writer, like a running Nexus."""
    path = tmp_path / "real" / "nexus.sqlite"
    path.parent.mkdir()
    storage = StorageService(path)
    board = storage.create_pinboard("Prompts", "#FF2D55", "📌")
    for n in range(6):
        item = storage.capture(kind="text", text_content=f"original {n}")
        if n < 2:
            storage.add_to_pinboard(item.id, board.id)
    yield SimpleNamespace(path=path, storage=storage, board=board, dir=tmp_path)
    storage.close()


def test_backup_is_taken_while_the_writer_is_open_and_verifies(live):
    result = dbtools.backup(live.path, live.dir / "backups", label="pre-upgrade")
    assert result["integrity"] == "ok" and result["counts"]["clip_items"] == 6 and "pre-upgrade" in result["file"]
    report = dbtools.verify(result["path"])
    assert report["ok"] and report["sha256_matches"] and report["counts_match_manifest"]
    assert live.storage.recent()[0].text_content.startswith("original")   # source untouched and usable


def test_backup_never_overwrites_and_refuses_a_missing_source(live, monkeypatch):
    first = dbtools.backup(live.path, live.dir / "b", label="x")
    stamp = Path(first["path"]).name[len("nexus-"):len("nexus-") + 15]
    original = time.strftime
    # Same second, same label: the file that already exists must be protected.
    monkeypatch.setattr(dbtools.time, "strftime", lambda fmt, *a: stamp if "T" in fmt else original(fmt, *a))
    with pytest.raises(dbtools.DbToolError):
        dbtools.backup(live.path, live.dir / "b", label="x")
    monkeypatch.undo()
    with pytest.raises(dbtools.DbToolError):
        dbtools.backup(live.dir / "nope.sqlite", live.dir / "b")


def test_verify_detects_a_damaged_or_altered_backup(live):
    path = Path(dbtools.backup(live.path, live.dir / "b")["path"])
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    assert dbtools.verify(path)["ok"] is False


def test_restoring_a_backup_never_loses_clips_captured_afterwards(live):
    backup = Path(dbtools.backup(live.path, live.dir / "b")["path"])
    # After the backup: new clips, a re-copied clip, a title, a Pinboard and a membership.
    new_a = live.storage.capture(kind="text", text_content="after backup A")
    new_b = live.storage.capture(kind="text", text_content="after backup B")
    again = live.storage.capture(kind="text", text_content="original 3")          # re-copied: newer timestamp
    # Windows' clock ticks every ~15 ms: make "newer" strictly true instead of a coin toss.
    live.storage._conn.execute("UPDATE clip_items SET updated_at = ? WHERE id = ?", (new_b.updated_at + 1, again.id))
    live.storage._conn.commit()
    live.storage.set_title(new_a.id, "Título nuevo")
    live.storage.set_title(again.id, "Título de uno viejo")
    extra = live.storage.create_pinboard("Nuevo tablero", "#007AFF")
    live.storage.add_to_pinboard(new_b.id, extra.id)
    live.storage.add_to_pinboard(again.id, live.board.id)
    live.storage._conn.commit()

    merged = dbtools.merge_newer(backup, live.path, live.dir / "merged.sqlite")
    assert merged["integrity"] == "ok" and merged["clips_added"] == 2
    assert merged["merged_counts"]["clip_items"] == 6 + 2 and merged["pinboards_added"] == 1
    check = StorageService(live.dir / "merged.sqlite")
    texts = {i.text_content: i for i in check.recent(limit=50)}
    assert len(texts) == 8 and {"after backup A", "after backup B"} <= set(texts)
    assert texts["after backup A"].title == "Título nuevo" and texts["original 3"].title == "Título de uno viejo"
    assert live.board.id in texts["original 3"].pinboard_ids
    assert any(extra.id in i.pinboard_ids for i in texts.values())
    assert [i.text_content for i in check.recent(limit=1)] == ["original 3"]       # newest timestamp survived
    check.close()
    assert dbtools.verify(backup)["ok"]                                               # the backup itself is untouched


def test_merge_is_idempotent_and_refuses_to_overwrite(live):
    backup = Path(dbtools.backup(live.path, live.dir / "b")["path"])
    live.storage.capture(kind="text", text_content="late")
    first = dbtools.merge_newer(backup, live.path, live.dir / "m1.sqlite")
    second = dbtools.merge_newer(live.dir / "m1.sqlite", live.path, live.dir / "m2.sqlite")
    assert second["clips_added"] == 0 and second["merged_counts"] == first["merged_counts"]
    with pytest.raises(dbtools.DbToolError):
        dbtools.merge_newer(backup, live.path, live.dir / "m1.sqlite")


def test_merge_works_from_a_pre_titles_backup(tmp_path):
    old = tmp_path / "old.sqlite"
    con = sqlite3.connect(old)
    con.executescript(LEGACY_SCHEMA)
    con.execute("INSERT INTO clip_items (content_hash,kind,text_content,created_at,updated_at,search_text) VALUES ('h1','text','a',1,1,'a')")
    con.commit(); con.close()
    new = tmp_path / "new.sqlite"
    storage = StorageService(new)
    storage.capture(kind="text", text_content="a")      # hash differs from 'h1': counts as a different clip
    storage.capture(kind="text", text_content="b")
    storage.close()
    merged = dbtools.merge_newer(old, new, tmp_path / "out.sqlite")
    assert merged["integrity"] == "ok" and merged["merged_counts"]["clip_items"] == 3


def old_purge(path, days=30):
    """Retention exactly as the pre-titles Nexus did it (no knowledge of titles)."""
    con = sqlite3.connect(path)
    cutoff = time.time() - days * 86400
    removed = con.execute("DELETE FROM clip_items WHERE updated_at < ? AND id NOT IN (SELECT DISTINCT item_id FROM pinboard_items)",
                          (cutoff,)).rowcount
    con.commit(); con.close()
    return removed


def test_protect_titled_keeps_an_older_nexus_from_purging_titled_clips(live):
    keep = live.storage.capture(kind="text", text_content="importante con título")
    plain = live.storage.capture(kind="text", text_content="sin título")
    live.storage.set_title(keep.id, "No perder")
    for item in (keep, plain):
        live.storage._conn.execute("UPDATE clip_items SET updated_at = ? WHERE id = ?", (time.time() - 90 * 86400, item.id))
    live.storage._conn.commit()
    unprotected = live.dir / "unprotected.sqlite"
    sqlite3.connect(live.path).backup(sqlite3.connect(unprotected))
    protected = live.dir / "protected.sqlite"
    sqlite3.connect(live.path).backup(sqlite3.connect(protected))

    assert old_purge(unprotected) == 2                    # without protection the old Nexus would drop the titled clip
    result = dbtools.protect_titled(protected)
    assert result["protected"] == 1
    assert old_purge(protected) == 1                      # only the untitled one goes
    check = StorageService(protected)
    assert [i.title for i in check.recent(limit=50) if i.title] == ["No perder"]
    assert any(b.name == dbtools.PROTECTED_BOARD for b in check.list_pinboards())
    check.close()
    assert dbtools.protect_titled(protected)["protected"] == 0     # idempotent: nothing new to add


def test_protect_titled_on_a_database_without_titles_is_a_noop(tmp_path):
    old = tmp_path / "old.sqlite"
    con = sqlite3.connect(old); con.executescript(LEGACY_SCHEMA); con.commit(); con.close()
    assert dbtools.protect_titled(old)["protected"] == 0


def test_tools_print_counts_never_content(live, capsys):
    secret = live.storage.capture(kind="text", text_content="CONTENIDO-PRIVADO-NO-IMPRIMIR")
    result = dbtools.backup(live.path, live.dir / "b")
    dbtools.verify(result["path"])
    assert "CONTENIDO-PRIVADO" not in json.dumps(result)
    assert secret.id
