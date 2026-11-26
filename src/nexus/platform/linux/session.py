"""Launch through systemd with the current graphical session environment."""
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    action = sys.argv[1] if len(sys.argv)>1 else 'panel'
    if action not in ('panel', 'stack', 'session-start', 'disable-autostart', 'enable-autostart', 'ready'):
        raise SystemExit('Acciones: panel, stack, session-start, disable-autostart')
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'nexus/integration'
    disabled = config/'autostart-disabled'
    if action == 'enable-autostart':
        disabled.unlink(missing_ok=True)
        return 0
    if action == 'disable-autostart':
        disabled.parent.mkdir(parents=True, exist_ok=True); disabled.touch(mode=0o600)
        return 0
    if action == 'session-start' and disabled.exists():
        return 0
    if action in ('panel', 'stack'):
        from nexus.platform.linux.control import call
        if call(action) == 0:
            return 0
    # Do not start a graphical process before the compositor is available.
    if not os.environ.get('WAYLAND_DISPLAY') or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        raise SystemExit('Nexus requiere una sesión Wayland/Hyprland activa')
    if action == 'ready':
        display = os.environ['WAYLAND_DISPLAY']
        socket = Path(display) if display.startswith('/') else Path(os.environ.get('XDG_RUNTIME_DIR', ''))/display
        if not socket.is_socket():
            return 1
        return subprocess.run(['hyprctl', 'version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2).returncode
    variables = [k for k in ('WAYLAND_DISPLAY', 'DISPLAY', 'XDG_RUNTIME_DIR',
        'DBUS_SESSION_BUS_ADDRESS', 'HYPRLAND_INSTANCE_SIGNATURE', 'XDG_CURRENT_DESKTOP') if k in os.environ]
    subprocess.run(['systemctl', '--user', 'import-environment', *variables], check=True, timeout=5)
    service_action = 'restart' if action == 'session-start' else 'start'
    subprocess.run(['systemctl', '--user', service_action, 'nexus.service'], check=True, timeout=12)
    if action != 'session-start':
        from nexus.platform.linux.control import call
        for _ in range(30):
            result = call(action)
            if result == 0:
                return 0
            time.sleep(.1)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
