"""Read-only production capture verification; prints metadata, never clip text."""
import argparse
import json
import os
import sqlite3
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--watch-seconds', type=int, default=0)
    expected = parser.add_mutually_exclusive_group()
    expected.add_argument('--expect-image', action='store_true', help='Verify a newly copied PNG against its stored hash')
    expected.add_argument('--expect-text', help='Verify a non-sensitive test text physically copied in another app')
    args = parser.parse_args()
    if not 0 <= args.watch_seconds <= 300:
        raise RuntimeError('La espera debe ser entre 0 y 300 segundos')
    os.environ['NEXUS_PRODUCTION'] = '1'
    os.environ.pop('NEXUS_PROFILE_ROOT', None)
    from nexus.platform.linux.production_capture import production_destinations, inspect_destination
    from nexus.platform.linux.settings import load_settings
    from nexus.core.storage import StorageService
    config, database = production_destinations()
    summary = inspect_destination(config, database)
    summary['capture_enabled'] = load_settings().capture_enabled
    summary['retention_in_app'] = False
    print(json.dumps(summary), flush=True)
    if not database.exists():
        raise RuntimeError('La base de producción todavía no existe')
    started_at = time.time()
    deadline = time.monotonic()+args.watch_seconds
    expected_hash = StorageService.hash_of('text', args.expect_text, None, None) if args.expect_text else None
    previous = None
    while True:
        with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as db:
            counts = dict(db.execute('SELECT kind, COUNT(*) FROM clip_items GROUP BY kind'))
            stack = db.execute('SELECT COUNT(*) FROM stack_state').fetchone()[0]
            latest = db.execute('SELECT id,kind,updated_at FROM clip_items ORDER BY updated_at DESC LIMIT 1').fetchone()
            found = db.execute('SELECT id,content_hash FROM clip_items WHERE content_hash=? AND updated_at>=?', (expected_hash,started_at)).fetchone() if expected_hash else None
            if args.expect_image:
                found = db.execute("SELECT id,content_hash FROM clip_items WHERE kind='image' AND updated_at>=? ORDER BY updated_at DESC LIMIT 1", (started_at,)).fetchone()
        state = {'counts_by_kind':counts, 'stack_pending':stack, 'latest':latest}
        if state != previous:
            print(json.dumps(state), flush=True); previous = state
        if found:
            # Also prove collecting did not replace the latest normal copy.
            mime = 'image/png' if args.expect_image else 'text/plain'
            raw = subprocess.check_output(['wl-paste','--no-newline','-t',mime], timeout=2)
            actual_hash = StorageService.hash_of('image', None, raw, None) if args.expect_image else StorageService.hash_of('text', raw.decode('utf-8'), None, None)
            if actual_hash != found[1]:
                raise RuntimeError('El clip existe, pero el portapapeles actual no coincide')
            print('PASS: copia nueva encontrada en producción y portapapeles conservado', flush=True)
            return 0
        if time.monotonic() >= deadline:
            if expected_hash or args.expect_image:
                raise RuntimeError('No se comprobó una copia nueva del contenido esperado en producción')
            return 0
        time.sleep(.5)

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f'VERIFICACIÓN NO APROBADA: {exc}')
        raise SystemExit(1)
