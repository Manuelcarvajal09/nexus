"""Temporary Ctrl+V binding and exact-window dispatch; no input device access."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

DESCRIPTION = 'Nexus Stack sequential paste (temporary)'
HANDLE = 'nexus_stack_ctrlv'


def run(*args):
    result = subprocess.run(['hyprctl', *args], capture_output=True, text=True, timeout=3)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Hyprland rechazó la operación')
    return result.stdout


def evaluate(lua):
    if run('eval', lua).strip() != 'ok':
        raise RuntimeError('Hyprland no aceptó la integración Lua')


def remove_binding():
    # Remove only our stored handle, never hl.unbind("CTRL + V").
    evaluate(f'if _G.{HANDLE} then _G.{HANDLE}:remove(); _G.{HANDLE}=nil end')


def is_address(address):
    return isinstance(address, str) and re.fullmatch(r'0x[0-9a-fA-F]+', address) is not None


class HyprlandStack:
    def __init__(self):
        self.installed = False

    def install(self):
        if self.installed:
            # A config reload can invalidate handles: verify the current binds.
            if any(b.get('description') == DESCRIPTION for b in json.loads(run('-j','binds'))):
                return
            self.installed = False
        if not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
            raise RuntimeError('Pegado secuencial requiere la sesión Hyprland')
        binds = json.loads(run('-j','binds'))
        conflicts = [b for b in binds if b.get('modmask') == 4
                     and (str(b.get('key','')).upper() == 'V' or b.get('keycode') == 55)
                     and b.get('description') != DESCRIPTION]
        if conflicts:
            raise RuntimeError('Conflicto Ctrl+V: '+json.dumps(conflicts))
        # Snapshot before touching compositor state; includes no clip contents.
        from . import paths
        backup = paths.CONFIG_DIR/'stack-binding-backups'
        backup.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = backup/f'{time.time_ns()}.json'
        record.write_text(json.dumps({'binds':binds,'revert':'python -m nexus_linux.stack_hyprland reset'}, indent=2))
        record.chmod(0o600)
        token = uuid.uuid4().hex
        # No I/O inside the compositor callback. The external command talks to
        # the existing service and receives a target captured at key release.
        source = str(Path(__file__).resolve().parents[1])
        prefix = 'env NEXUS_PRODUCTION='+shlex.quote(os.environ.get('NEXUS_PRODUCTION','0'))
        if os.environ.get('NEXUS_PROFILE_ROOT'):
            prefix += ' NEXUS_PROFILE_ROOT='+shlex.quote(os.environ['NEXUS_PROFILE_ROOT'])
        prefix += ' PYTHONPATH='+shlex.quote(source)+' '+shlex.quote(sys.executable)+' -m nexus_linux.stack_hyprland paste '
        lua = f'''if _G.{HANDLE} then _G.{HANDLE}:remove() end
local sequence = 0
_G.{HANDLE} = hl.bind("CTRL + V", function()
    local w = hl.get_active_window()
    if not w then return end
    sequence = sequence + 1
    hl.exec_cmd({json.dumps(prefix)} .. "{token}:" .. tostring(sequence) .. " " .. w.address)
end, {{release = true, repeating = false, description = {json.dumps(DESCRIPTION)}}})'''
        evaluate(lua)
        self.installed = True

    def remove(self):
        if self.installed:
            remove_binding()
            self.installed = False

    def target_valid(self, address):
        if not is_address(address):
            return False
        window = json.loads(run('-j','activewindow'))
        return window.get('address') == address and window.get('pid') != os.getpid()

    def send(self, address):
        if not is_address(address):
            return False
        # Check focus and send in one compositor evaluation. Never steal focus
        # back to a window that changed while preparing the clipboard.
        lua = ('local w=hl.get_active_window(); assert(w and w.address=='+json.dumps(address)+
               ', "Nexus: foco cambió"); hl.dispatch(hl.dsp.send_shortcut({mods="CTRL", key="V", window='+
               json.dumps('address:'+address)+'}))')
        evaluate(lua)
        return True


def main():
    if len(sys.argv) == 2 and sys.argv[1] == 'check':
        binds = json.loads(run('-j','binds'))
        conflicts = [b for b in binds if b.get('modmask') == 4
                     and (str(b.get('key','')).upper() == 'V' or b.get('keycode') == 55)
                     and b.get('description') != DESCRIPTION]
        if conflicts:
            raise RuntimeError('Conflicto Ctrl+V: '+json.dumps(conflicts))
        evaluate('assert(type(hl.get_active_window)=="function" and type(hl.bind)=="function" and type(hl.dsp.send_shortcut)=="function")')
        print('Preflight Hyprland correcto: Ctrl+V sin conflictos; no se modifica ningún binding.')
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == 'reset':
        remove_binding()
        return 0
    if len(sys.argv) != 4 or sys.argv[1] != 'paste':
        raise RuntimeError('Uso: stack_hyprland reset | paste REQUEST ADDRESS')
    request, target = sys.argv[2:]
    if not is_address(target) or not re.fullmatch(r'[a-f0-9]{32}:[0-9]+',request):
        raise RuntimeError('Petición de pegado inválida')
    from .control import service_name
    from ast import literal_eval
    service = service_name()
    try:
        result = subprocess.run(['gdbus','call','--session','--dest',service,'--object-path','/Nexus',
            '--method','mx.com.storem.nexus.StackPaste',request,target], capture_output=True,text=True,timeout=20)
        if result.returncode == 0:
            reply = json.loads(literal_eval(result.stdout)[0])
            if reply.get('outcome') == 'inactive':
                # Empty queue / session ended while this command was queued.
                if HyprlandStack().target_valid(target):
                    HyprlandStack().send(target)
            return 0 if reply.get('outcome') in ('dispatched','duplicate','inactive') else 1
    except (ValueError, subprocess.TimeoutExpired):
        # Ambiguous reply: never forward a second paste or consume another item.
        pass
    owner = subprocess.run(['gdbus','call','--session','--dest','org.freedesktop.DBus',
        '--object-path','/org/freedesktop/DBus','--method','org.freedesktop.DBus.NameHasOwner',service],
        capture_output=True,text=True,timeout=3)
    if owner.returncode == 0 and owner.stdout.strip() == '(false,)':
        remove_binding()  # Recover native Ctrl+V after a crashed service.
        if HyprlandStack().target_valid(target):
            HyprlandStack().send(target)
    return 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print('Stack: '+str(exc), file=sys.stderr)
        raise SystemExit(1)
