#!/usr/bin/env python3
"""Reproducible per-user integration. Refuse conflicts; track every owned file."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = Path.home() / '.local/share/nexus-runtime/venv/bin/python'
START = '-- BEGIN NEXUS MANAGED INTEGRATION --'
END = '-- END NEXUS MANAGED INTEGRATION --'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError(f'{args[0]}: {result.stderr.strip() or result.stdout.strip()}')
    return result.stdout


def check_binds(binds, alternate=False):
    wanted = 69 if alternate else 5  # Hyprland SHIFT=1 CTRL=4 SUPER=64
    conflicts = []
    for bind in binds:
        key = str(bind.get('key', '')).upper()
        if bind.get('keycode') in (54, 55):
            key = 'C' if bind['keycode'] == 54 else 'V'
        if bind.get('modmask') == wanted and key in ('C', 'V'):
            conflicts.append(f"{key}: {bind.get('dispatcher')} {bind.get('arg')}")
    return conflicts


def known_directory_repair(path, root, expected):
    """Recognize only the exact manual removal of erroneous directory quotes."""
    if not expected:
        return False
    raw = path.read_text()
    corrected = f"WorkingDirectory={root}\n"
    if raw.count(corrected) != 1:
        return False
    previous = raw.replace(corrected, f"WorkingDirectory={json.dumps(str(root))}\n")
    return digest(previous.encode()) == expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('check', 'install', 'uninstall', 'update-service'))
    parser.add_argument('--runtime', type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument('--alternate-shortcuts', action='store_true', help='Super+Ctrl+Shift+C/V; retains Foot copy/paste')
    parser.add_argument('--prefix', type=Path, help='stage all generated integration in this directory, without touching the session')
    args = parser.parse_args()
    stage = args.prefix is not None
    data = args.prefix / 'share' if stage else Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))
    config = args.prefix / 'config' if stage else Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))
    bin_dir = args.prefix / 'bin' if stage else Path.home()/'.local/bin'
    integration = config/'nexus/integration'
    manifest_path = integration/'manifest.json'
    user_lua = config/'caelestia/hypr-user.lua'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'files': {}}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup_dir = integration/'backups'/stamp

    def backup(path):
        if path.exists():
            backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            dest = backup_dir/(digest(str(path).encode())[:12]+'-'+path.name)
            shutil.copy2(path, dest)

    writes = []

    def write(path, content, executable=False):
        if args.action == 'update-service' and path != config/'systemd/user/nexus.service':
            return
        writes.append((path, content, executable))

    def commit_write(path, content, executable=False):
        raw = content.encode()
        if path.exists():
            if path.read_bytes() == raw:
                manifest['files'][str(path)] = digest(raw)
                return
            expected = manifest['files'].get(str(path))
            if not expected or digest(path.read_bytes()) != expected:
                raise RuntimeError(f'Archivo ajeno o modificado: {path}; no se sobrescribe')
            backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name+'.nexus-tmp')
        temp.write_bytes(raw); temp.chmod(0o700 if executable else 0o600); temp.replace(path)
        manifest['files'][str(path)] = digest(raw)

    allowed = {str(p) for p in (bin_dir/'nexus', config/'systemd/user/nexus.service', data/'applications/mx.com.storem.nexus.desktop', integration/'hyprland.lua')}
    if set(manifest.get('files', {})) - allowed:
        raise RuntimeError('Manifest contiene rutas ajenas; se conserva todo')

    if args.action == 'uninstall':
        if not manifest_path.exists():
            print('No existe integración gestionada; datos intactos.'); return 0
        if not stage:
            run(['systemctl', '--user', 'stop', 'nexus.service'])
        if user_lua.exists():
            text = user_lua.read_text()
            if START in text:
                a = text.index(START); b = text.index(END, a)+len(END)
                backup(user_lua)
                user_lua.write_text(text[:a]+text[b:])
        for name, expected in manifest['files'].items():
            path = Path(name)
            if path.exists() and digest(path.read_bytes()) == expected:
                path.unlink()
            elif path.exists():
                print(f'Se conserva archivo modificado: {path}')
        manifest_path.unlink()
        if not stage:
            run(['systemctl', '--user', 'daemon-reload'])
        print('Integración retirada. Historial, configuración, runtime y backups conservados. Reinicia sesión para descargar los bindings.')
        return 0

    runtime = args.runtime.absolute()
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise RuntimeError(f'Runtime privado inválido: {runtime}')
    run([str(runtime), '-c', 'import PySide6, qasync, dbus_next'])
    for command in ('wl-copy', 'wl-paste', 'gdbus', 'systemctl', 'hyprctl'):
        if not shutil.which(command):
            raise RuntimeError(f'Falta dependencia: {command}')
    if not stage and args.action != 'update-service':
        # Must query actual bindings successfully before any writes.
        binds = json.loads(run(['hyprctl', '-j', 'binds']))
        conflicts = check_binds(binds, args.alternate_shortcuts)
        # Ignore only an exact previously managed Nexus command on update.
        conflicts = [c for c in conflicts if str(bin_dir/'nexus') not in c or not manifest_path.exists()]
        if conflicts:
            raise RuntimeError('Conflictos de compositor: '+ '; '.join(conflicts))
        foot = config/'foot/foot.ini'
        if not args.alternate_shortcuts and (foot.exists() or shutil.which('foot')):
            raise RuntimeError('Foot utiliza Ctrl+Shift+C/V para copiar/pegar. Alternativa: --alternate-shortcuts, verificada contra bindings activos.')
        run(['systemctl', '--user', 'show-environment'])
        if not (config/'hypr/hyprland.lua').is_file():
            raise RuntimeError('Se esperaba Hyprland Lua/Caelestia; no se modifica otra arquitectura de configuración')
    if args.action == 'check':
        print('Preflight correcto' + (' (staging: sin validar sesión ni conflictos activos)' if stage else ''))
        return 0

    if args.action == 'update-service':
        if not manifest_path.exists():
            raise RuntimeError('Actualización requiere manifest de la instalación existente')
        if not stage:
            run(['systemctl', '--user', 'show-environment'])

    launcher = bin_dir/'nexus'
    shell_root = shlex.quote(str(ROOT/'src'))
    shell_python = shlex.quote(str(runtime))
    write(launcher, '#!/bin/sh\nunset NEXUS_PROFILE_ROOT NEXUS_SMOKE_EXIT_MS NEXUS_OPEN_ON_START\nexport NEXUS_PRODUCTION=1\nexport PYTHONPATH='+shell_root+'\nexec '+shell_python+' -m nexus_linux.session "$@"\n', True)
    # ExecStart/Environment parse quoted words. WorkingDirectory is a scalar
    # path: do not wrap it in shell/JSON quotes.
    if any('%' in str(p) or '\n' in str(p) for p in (ROOT, runtime, launcher)):
        raise RuntimeError('Ruta no compatible con systemd/Desktop Entry')
    unit = f'''[Unit]
Description=Nexus clipboard manager
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecCondition={json.dumps(str(runtime))} -m nexus_linux.session ready
ExecStart={json.dumps(str(runtime))} -m nexus_linux.app
WorkingDirectory={ROOT}
Environment="NEXUS_PRODUCTION=1"
Environment="NEXUS_PROFILE_ROOT="
Environment="NEXUS_OPEN_ON_START=0"
Environment="NEXUS_CAPTURE_DISABLED=0"
Environment={json.dumps("XDG_CONFIG_HOME="+str(config))}
Environment={json.dumps("XDG_DATA_HOME="+str(data))}
Environment={json.dumps('PYTHONPATH='+str(ROOT/'src'))}
Environment="QT_QPA_PLATFORM=wayland"
UMask=0077
Restart=on-failure
RestartSec=3
TimeoutStopSec=8
'''
    write(config/'systemd/user/nexus.service', unit)
    write(data/'applications/mx.com.storem.nexus.desktop', f'''[Desktop Entry]
Type=Application
Name=Nexus
Comment=Historial, Pinboards y Stack del portapapeles
Exec={json.dumps(str(launcher))} panel
Icon={ROOT}/resources/nexus-icon.png
Terminal=false
Categories=Utility;
StartupNotify=false
''')
    mods = 'SUPER + CTRL + SHIFT' if args.alternate_shortcuts else 'CTRL + SHIFT'
    fragment = integration/'hyprland.lua'
    lua_cmd = lambda action: json.dumps(shlex.quote(str(launcher))+' '+action)
    write(fragment, f'''-- Nexus managed fragment. Loading is deferred to the next session.
hl.bind("{mods} + V", hl.dsp.exec_cmd({lua_cmd('panel')}))
hl.bind("{mods} + C", hl.dsp.exec_cmd({lua_cmd('stack')}))
hl.on("hyprland.start", function()
    hl.exec_cmd({lua_cmd('session-start')})
end)
''')
    original = user_lua.read_text() if user_lua.exists() else ''
    block = START+'\n'+'dofile('+json.dumps(str(fragment))+')\n'+END
    if START in original:
        a = original.index(START); b = original.index(END, a)+len(END)
        updated = original[:a]+block+original[b:]
    else:
        updated = original+'\n'+block+'\n'
    # Complete preflight before touching any integration file.
    for path, content, _ in writes:
        if path.is_symlink():
            raise RuntimeError(f'Se conserva enlace simbólico: {path}')
        if path.exists():
            expected = manifest['files'].get(str(path))
            if digest(path.read_bytes()) != expected:
                if path == config/'systemd/user/nexus.service' and known_directory_repair(path, ROOT, expected):
                    manifest['files'][str(path)] = digest(path.read_bytes())
                else:
                    raise RuntimeError(f'Archivo ajeno o modificado: {path}')
    if user_lua.is_symlink():
        raise RuntimeError(f'Se conserva enlace simbólico: {user_lua}')
    for path, content, executable in writes:
        commit_write(path, content, executable)
        # Checkpoint ownership so interruption remains reversible.
        integration.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2))
    if args.action != 'update-service' and updated != original:
        backup(user_lua); user_lua.parent.mkdir(parents=True, exist_ok=True); user_lua.write_text(updated)
    integration.mkdir(parents=True, exist_ok=True)
    manifest['version'] = '1.0.0rc1'
    if args.action != 'update-service':
        manifest['shortcuts'] = mods+' + C/V'
    manifest_path.write_text(json.dumps(manifest, indent=2)); manifest_path.chmod(0o600)
    if not stage:
        run(['systemctl', '--user', 'daemon-reload'])
    if args.action == 'update-service':
        print('Servicio actualizado con backup; atajos intactos. Captura requiere validación separada.')
        return 0
    print('Integración '+('generada en staging' if stage else 'instalada; reinicia sesión para autoinicio y bindings')+'. Captura real desactivada; no se toca ninguna base existente.')
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f'BLOQUEADO: {exc}', file=sys.stderr)
        raise SystemExit(1)
