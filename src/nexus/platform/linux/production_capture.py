"""Production capture safety checks, without opening/migrating legacy data."""
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess

REQUIRED_TESTS = {'text', 'png', 'uri-list-single-file', 'internal-suppression', 'storage-dedup', 'sqlite-integrity', 'stack-collection-preserves-clipboard', 'background-watch', 'sensitive-exclusion', 'sensitive-offer-bound'}


def production_destinations():
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'nexus/production'
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'nexus/production'
    return config, data/'nexus.sqlite'


def inspect_destination(config, database):
    """Read only. A populated, unvalidated destination is never adopted."""
    for path in (config, config/'settings.json', config/'capture-validated.json', database.parent, database):
        if path.is_symlink():
            raise RuntimeError(f'Destino enlazado rechazado: {path}')
    marker = config/'capture-validated.json'
    validated = False
    if marker.exists():
        try:
            value = json.loads(marker.read_text())
            validated = isinstance(value, dict) and value.get('database') == str(database) and REQUIRED_TESTS <= set(value.get('tests', []))
        except (ValueError, TypeError):
            raise RuntimeError('Marcador de validación inválido; se conserva sin habilitar captura')
    count = 0
    if database.exists():
        if database.stat().st_nlink != 1:
            raise RuntimeError('La base de producción comparte un hardlink con otro destino')
        with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Integridad de producción falló')
            count = db.execute('SELECT COUNT(*) FROM clip_items').fetchone()[0]
        if count and not validated:
            raise RuntimeError(f'Destino con {count} clips sin validación reconocida; se conserva sin habilitar captura')
    return {'database':str(database), 'count':count, 'previously_validated':validated}


def check_stopped_service(config, database):
    """Synthetic validation must never run alongside a real capture service."""
    result = subprocess.run(['systemctl','--user','show','nexus.service','-p','ActiveState','-p','Environment','-p','EnvironmentFiles'],
                            capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError('No se pudo comprobar nexus.service: '+result.stderr.strip())
    values = dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if values.get('ActiveState') not in ('inactive','failed'):
        raise RuntimeError('Detén nexus.service antes de validar para evitar capturar pruebas en producción')
    if values.get('EnvironmentFiles'):
        raise RuntimeError('Servicio con EnvironmentFiles externos: revisar destino antes de activar')
    env = dict(word.split('=',1) for word in shlex.split(values.get('Environment','')) if '=' in word)
    if env.get('NEXUS_PRODUCTION') != '1' or env.get('NEXUS_PROFILE_ROOT'):
        raise RuntimeError('El servicio no utiliza el perfil de producción separado')
    # Pin the roots in the generated unit rather than rely on manager/shell drift.
    if env.get('XDG_CONFIG_HOME') != str(config.parent.parent) or env.get('XDG_DATA_HOME') != str(database.parent.parent.parent):
        raise RuntimeError('Destinos del servicio no coinciden; aplica integrate.py update-service primero')
    if env.get('NEXUS_CAPTURE_DISABLED') != '0':
        raise RuntimeError('La unidad mantiene NEXUS_CAPTURE_DISABLED; aplica update-service primero')
