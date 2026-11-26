"""Invoke Nexus's user D-Bus actions without loading Qt or clip contents."""
import argparse
import hashlib
import os
import subprocess

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('panel', 'stack'))
    args = parser.parse_args()
    return call(args.action)

def service_name():
    service = 'mx.com.storem.nexus'
    if os.environ.get('NEXUS_PRODUCTION') == '1':
        service += '.production'
    profile = os.environ.get('NEXUS_PROFILE_ROOT')
    if profile:
        service += '.profile_' + hashlib.sha256(profile.encode()).hexdigest()[:12]
    return service

def call(action):
    service = service_name()
    method = 'ShowMainPanel' if action == 'panel' else 'ShowStack'
    return subprocess.run(['gdbus', 'call', '--session', '--dest', service, '--object-path', '/Nexus', '--method', 'mx.com.storem.nexus.' + method], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2).returncode

if __name__ == '__main__':
    raise SystemExit(main())
