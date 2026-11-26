#!/usr/bin/env python3
"""Bounded isolated process/CPU/single-instance/clean-shutdown check."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home()/'.local/share/nexus-runtime/venv/bin/python'
with tempfile.TemporaryDirectory(prefix='nexus-background-check-') as tmp:
    env = dict(os.environ, PYTHONPATH=str(ROOT/'src'), QT_QPA_PLATFORM='offscreen',
               NEXUS_PROFILE_ROOT=tmp, NEXUS_CAPTURE_DISABLED='1', HYPRLAND_INSTANCE_SIGNATURE='')
    env.pop('NEXUS_SMOKE_EXIT_MS', None); env.pop('NEXUS_OPEN_ON_START', None)
    cmd = [str(RUNTIME), '-m', 'nexus.platform.linux.app']
    log_path = Path(tmp)/'process.log'
    with log_path.open('w') as log:
        process = subprocess.Popen(cmd, env=env, stdout=log, stderr=log)
        try:
            time.sleep(1)
            if process.poll() is not None:
                raise RuntimeError('El proceso principal terminó antes de la prueba')
            second = subprocess.run(cmd, env=env, capture_output=True, timeout=4)
            if second.returncode or b'Ya hay una instancia' not in second.stderr:
                raise RuntimeError('La segunda instancia no fue rechazada correctamente')
            def cpu_ticks():
                fields = Path(f'/proc/{process.pid}/stat').read_text().rsplit(')',1)[1].split()
                return int(fields[11])+int(fields[12])
            before = cpu_ticks(); start = time.monotonic()
            time.sleep(5)
            elapsed = time.monotonic()-start
            seconds = (cpu_ticks()-before)/os.sysconf('SC_CLK_TCK')
            rss = int(Path(f'/proc/{process.pid}/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
            process.send_signal(signal.SIGTERM)
            code = process.wait(timeout=5)
            if code != 0:
                raise RuntimeError(f'Cierre SIGTERM no limpio: {code}')
            database = Path(tmp)/'data/nexus.sqlite'
            with sqlite3.connect(database) as db:
                assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                assert db.execute('SELECT COUNT(*) FROM clip_items').fetchone()[0] == 0
            print(json.dumps({'environment':'offscreen, capture disabled, isolated temporary profile',
                'single_instance':'PASS', 'sigterm':'PASS', 'sqlite_integrity':'PASS',
                'elapsed_seconds':round(elapsed,3), 'cpu_seconds':seconds,
                'cpu_percent_one_core':round(100*seconds/elapsed,3),
                'rss_mib':round(rss/1048576,1)}, indent=2))
        finally:
            if process.poll() is None:
                process.kill(); process.wait()
