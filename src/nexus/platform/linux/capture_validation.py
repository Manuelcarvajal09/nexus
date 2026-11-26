"""Explicit live capture gate. All synthetic data stays in a temporary profile.
Run only from a real session; --test-clipboard intentionally replaces its clipboard.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def isolated_database(paths, root):
    """Reject cached production paths before constructing StorageService."""
    expected = Path(root)/'data/nexus.sqlite'
    if paths.DB_PATH != expected or paths.CONFIG_DIR != Path(root)/'config':
        raise RuntimeError('Perfil importado antes del aislamiento; no se abre ninguna base')
    return expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-clipboard', action='store_true', required=True,
                        help='Run synthetic Wayland tests; replaces current clipboard contents')
    parser.add_argument('--enable-production', action='store_true')
    args = parser.parse_args()
    if not os.environ.get('WAYLAND_DISPLAY'):
        raise SystemExit('Falta una sesión Wayland')
    from nexus.platform.linux.production_capture import production_destinations, inspect_destination, check_stopped_service
    config, database = production_destinations()
    check_stopped_service(config, database)
    destination = inspect_destination(config, database)
    print('Destino de producción comprobado:', json.dumps(destination))
    for tool in ('wl-copy', 'wl-paste'):
        subprocess.run([tool, '--version'], check=True, capture_output=True, timeout=2)
    access = subprocess.run(['wl-paste', '--list-types'], capture_output=True, timeout=2)
    if access.returncode and b'Nothing is copied' not in access.stderr:
        raise RuntimeError('Acceso Wayland no disponible: '+access.stderr.decode('utf-8', 'replace').strip())
    # Check session connectivity before creating even an isolated database.
    subprocess.run(['hyprctl', 'version'], check=True, capture_output=True, timeout=2)
    previous_profile = os.environ.get("NEXUS_PROFILE_ROOT")
    with tempfile.TemporaryDirectory(prefix='nexus-capture-validation-') as tmp:
        os.environ['NEXUS_PROFILE_ROOT'] = tmp
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage, QColor
        from PySide6.QtTest import QTest
        from nexus.core.storage import StorageService
        from nexus.platform.linux.clipboard_monitor import ClipboardMonitor
        from nexus.platform.linux.paste_service import PasteService
        from nexus.core import paths
        expected_database = isolated_database(paths, tmp)
        print('Base aislada de validación:', expected_database, flush=True)
        app = QApplication([])
        storage = StorageService(db_path=expected_database, allow_retention=False)
        # Replace the old selection before any watcher starts: no existing
        # real clip is ever written even to this temporary validation database.
        subprocess.run(["wl-copy", "-t", "text/plain"], input=b"NEXUS-VALIDATION-START", check=True, timeout=2)
        monitor = ClipboardMonitor(storage, enabled=True)
        items = []
        states = []
        monitor.captured.connect(items.append)
        monitor.selection_observed.connect(states.append)
        deadline = time.monotonic()+3
        while not items and time.monotonic() < deadline:
            QTest.qWait(30)
        if not items or items[-1].text_content != 'NEXUS-VALIDATION-START':
            monitor.stop(); storage.close()
            subprocess.run(['wl-copy', '--clear'], check=True, timeout=2)
            raise RuntimeError('El watcher no confirmó la selección inicial de prueba')
        # Initial selection may be captured: only test values determine success.
        cases = [('text/plain', b'NEXUS-VALIDATION-A'), ('text/plain', b'NEXUS-VALIDATION-B')]
        image = QImage(24, 24, QImage.Format.Format_ARGB32); image.fill(QColor('#397FD5'))
        buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, 'PNG')
        cases += [('image/png', bytes(buffer.data())), ('text/uri-list', b'file:///tmp/nexus-validation.txt\r\n')]
        observed = []
        try:
            for mime, payload in cases:
                before = len(items)
                subprocess.run(['wl-copy', '-t', mime], input=payload, check=True, timeout=2)
                deadline = time.monotonic()+3
                while len(items) == before and time.monotonic() < deadline:
                    QTest.qWait(30)
                if len(items) != before+1:
                    raise RuntimeError(f'Captura no confirmada para {mime}')
                item = items[-1]
                expected = {'text/plain':'text', 'image/png':'image', 'text/uri-list':'file'}[mime]
                if item.kind != expected:
                    raise RuntimeError(f'Tipo incorrecto: {item.kind} / {expected}')
                if expected == 'text' and item.text_content.encode() != payload:
                    raise RuntimeError('Texto alterado')
                if expected == 'image' and item.image_data != payload:
                    raise RuntimeError('Imagen alterada')
                observed.append(item)
            from nexus.core.stack import ClipStack
            class NoClipboardWrites:
                def write_to_clipboard(self, item):
                    raise RuntimeError('Stack modificó el portapapeles durante la acumulación')
            stack = ClipStack(storage, NoClipboardWrites())
            stack.activate()
            for item in observed:
                stack.add(item)
            stack.activate(); stack.toggle_order()
            if len(stack.items()) != len(observed):
                raise RuntimeError('Stack no acumuló los elementos de validación')
            clipboard_before = subprocess.check_output(['wl-paste', '--no-newline', '-t', 'text/uri-list'], timeout=2)
            if clipboard_before != cases[-1][1]:
                raise RuntimeError('Stack alteró el último contenido copiado')
            count = len(items)
            paste = PasteService(on_write=monitor.suppress_next_change)
            if not paste.write_to_clipboard(observed[0]):
                raise RuntimeError('No se pudo preparar el portapapeles')
            QTest.qWait(400)
            if len(items) != count:
                raise RuntimeError('La escritura interna se recapturó')
            count = len(items)
            rows_before_sensitive = len(storage.recent())
            states_before_sensitive = len(states)
            subprocess.run(['wl-copy', '--sensitive', '-t', 'text/plain'],
                input=b'NEXUS-VALIDATION-SENSITIVE', check=True, timeout=2)
            offered_types = ClipboardMonitor._list_types()
            print('MIME de la selección sensible:', json.dumps(offered_types), flush=True)
            if 'x-kde-passwordManagerHint' not in offered_types:
                raise RuntimeError('La selección perdió x-kde-passwordManagerHint; revisar otros gestores de portapapeles. No se habilita captura')
            deadline = time.monotonic()+3
            while 'sensitive' not in states[states_before_sensitive:] and time.monotonic() < deadline:
                QTest.qWait(30)
            if 'sensitive' not in states[states_before_sensitive:]:
                raise RuntimeError('El watcher no confirmó CLIPBOARD_STATE=sensitive; no se aprueba la exclusión')
            # Drain readers that were pending when the selection changed.
            monitor._reader_pool.waitForDone(4500)
            QTest.qWait(150)
            secret_hash = StorageService.hash_of('text', 'NEXUS-VALIDATION-SENSITIVE', None, None)
            secret_rows = storage._conn.execute('SELECT COUNT(*) FROM clip_items WHERE content_hash=?', (secret_hash,)).fetchone()[0]
            if secret_rows:
                raise RuntimeError('Se almacenó la selección sensible en el perfil aislado de validación')
            if len(items) != count or len(storage.recent()) != rows_before_sensitive:
                raise RuntimeError('Persistencia inesperada durante la exclusión sensible, en el perfil aislado')
            # Prove the same content remains capturable when explicitly public:
            # this rules out passing because of hash suppression or a dead watch.
            before_public = len(items)
            subprocess.run(['wl-copy', '-t', 'text/plain'], input=b'NEXUS-VALIDATION-SENSITIVE', check=True, timeout=2)
            deadline = time.monotonic()+3
            while len(items) == before_public and time.monotonic() < deadline:
                QTest.qWait(30)
            if len(items) != before_public+1 or items[-1].content_hash != secret_hash:
                raise RuntimeError('El control público del mismo texto no se capturó')
            # Consecutive identical external payload does not create another row.
            before_rows = len(storage.recent())
            before_events = len(items)
            subprocess.run(['wl-copy', '-t', 'text/uri-list'], input=cases[-1][1], check=True, timeout=2)
            deadline = time.monotonic()+3
            while len(items) == before_events and time.monotonic() < deadline:
                QTest.qWait(30)
            if len(items) != before_events+1 or items[-1].content_hash != observed[-1].content_hash:
                raise RuntimeError('El watcher no confirmó la recopia externa')
            if len(storage.recent()) != before_rows:
                raise RuntimeError('Duplicado de almacenamiento')
            if storage._conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Integridad SQLite falló')
        finally:
            monitor.stop()
            # Allow all worker callbacks to finish before disposing Qt/SQLite.
            from PySide6.QtCore import QThreadPool
            QThreadPool.globalInstance().waitForDone(5000)
            app.processEvents(); storage.close()
            # Leave no synthetic selection for the production watcher's initial
            # read when the user starts nexus.service after this validation.
            subprocess.run(['wl-copy', '--clear'], check=True, timeout=2)
        result = {'version':'1.0.0rc1', 'validated_at':time.time(),
                  'tests':['text', 'png', 'uri-list-single-file', 'internal-suppression', 'storage-dedup', 'sqlite-integrity', 'stack-collection-preserves-clipboard', 'background-watch', 'sensitive-exclusion', 'sensitive-offer-bound'],
                  'stack_application_receipt':False, 'synthetic_profile_removed':True}
    if args.enable_production:
        # Separate phase, after all live tests succeed. Never import/migrate history.
        # Recheck destination after live tests and immediately before enabling.
        check_stopped_service(config, database)
        inspect_destination(config, database)
        marker = config/'capture-validated.json'
        config.mkdir(parents=True, exist_ok=True, mode=0o700)
        preferences = config/'settings.json'
        value = json.loads(preferences.read_text()) if preferences.exists() else {}
        if not isinstance(value, dict):
            raise RuntimeError('settings.json debe contener un objeto JSON')
        # Preserve a reversible copy of existing preferences, never clip data.
        if preferences.exists():
            import shutil
            backup = config/'backups'/str(time.time_ns())
            backup.mkdir(parents=True, mode=0o700)
            shutil.copy2(preferences, backup/'settings.json')
            if marker.exists():
                shutil.copy2(marker, backup/'capture-validated.json')
        value.update(capture_enabled=True, allow_retention=False, evdev_watcher=False)
        temp = preferences.with_suffix('.tmp'); temp.write_text(json.dumps(value, indent=2)); temp.chmod(0o600); temp.replace(preferences)
        result['database'] = str(database)
        temp = marker.with_suffix('.tmp'); temp.write_text(json.dumps(result, indent=2)); temp.chmod(0o600); temp.replace(marker)
        print('Captura habilitada para el próximo reinicio de Nexus; destino nuevo y separado:', database)
    if previous_profile is None:
        os.environ.pop("NEXUS_PROFILE_ROOT", None)
    else:
        os.environ["NEXUS_PROFILE_ROOT"] = previous_profile
    print(json.dumps(result, indent=2))
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f'VALIDACIÓN FALLIDA: {exc}', file=sys.stderr)
        raise SystemExit(1)
