"""Capture activation must prove destination, isolation and clipboard preservation."""
import shutil
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest
from nexus_linux.stack import ClipStack
from nexus_linux.production_capture import inspect_destination, check_stopped_service, REQUIRED_TESTS
from nexus_linux.storage import StorageService

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux desktop integration (wl-clipboard, XDG, systemd)")

ROOT = Path(__file__).resolve().parents[1]


def test_collection_activation_and_order_never_write_clipboard(storage, paste_service):
    stack = ClipStack(storage, paste_service)
    stack.activate()
    for text in ('A','B','C'):
        stack.add(storage.capture(kind='text', text_content=text))
    stack.activate(); stack.toggle_order()
    stack.remove_persistent(stack.items()[1].id)
    assert paste_service.written == []
    assert len(stack.items()) == 2
    assert stack.prepare_manual_paste().text_content == 'C'
    assert len(paste_service.written) == 1


def test_destination_refuses_populated_unvalidated_data(tmp_path):
    database = tmp_path/'production/nexus.sqlite'; database.parent.mkdir()
    storage = StorageService(database, allow_retention=False)
    storage.capture(kind='text', text_content='synthetic content'); storage.close()
    with pytest.raises(RuntimeError, match='sin validación'):
        inspect_destination(tmp_path/'config', database)
    with __import__('sqlite3').connect(database) as db:
        assert db.execute('SELECT COUNT(*) FROM clip_items').fetchone()[0] == 1


def test_destination_accepts_only_its_own_validation(tmp_path):
    config = tmp_path/'config'; config.mkdir()
    database = tmp_path/'production/nexus.sqlite'; database.parent.mkdir()
    storage = StorageService(database); storage.capture(kind='text', text_content='real marker'); storage.close()
    marker = config/'capture-validated.json'
    marker.write_text(json.dumps({'database':'/unrelated/data.sqlite','tests':sorted(REQUIRED_TESTS)}))
    with pytest.raises(RuntimeError): inspect_destination(config, database)
    marker.write_text(json.dumps({'database':str(database),'tests':sorted(REQUIRED_TESTS)}))
    assert inspect_destination(config, database)['count'] == 1


def test_service_must_be_stopped_and_use_matching_roots(tmp_path):
    config = tmp_path/'config/nexus/production'; database = tmp_path/'share/nexus/production/nexus.sqlite'
    properties = ('ActiveState=inactive\nEnvironment=NEXUS_PRODUCTION=1 NEXUS_PROFILE_ROOT= '
        f'XDG_CONFIG_HOME={tmp_path}/config XDG_DATA_HOME={tmp_path}/share NEXUS_CAPTURE_DISABLED=0\nEnvironmentFiles=\n')
    with patch('nexus_linux.production_capture.subprocess.run',return_value=subprocess.CompletedProcess([],0,properties)):
        check_stopped_service(config,database)
    with patch('nexus_linux.production_capture.subprocess.run',return_value=subprocess.CompletedProcess([],0,properties.replace('inactive','active'))):
        with pytest.raises(RuntimeError, match='Detén'): check_stopped_service(config,database)
    with patch('nexus_linux.production_capture.subprocess.run',return_value=subprocess.CompletedProcess([],0,properties.replace('NEXUS_PRODUCTION=1','NEXUS_PRODUCTION=0'))):
        with pytest.raises(RuntimeError, match='perfil'): check_stopped_service(config,database)


@pytest.mark.skipif(not (Path.home() / '.local/share/nexus-runtime/venv/bin/python').exists(), reason='needs the installed Nexus Linux runtime')
def test_service_only_update_recognizes_exact_manual_fix_preserves_bindings(tmp_path):
    prefix = tmp_path/'integration'
    cmd = [sys.executable,str(ROOT/'scripts/integrate.py')]
    subprocess.run(cmd+['install','--prefix',str(prefix),'--alternate-shortcuts'],check=True)
    unit = prefix/'config/systemd/user/nexus.service'
    manifest_path = prefix/'config/nexus/integration/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    # Simulate the previous generator, then the user's exact manual repair.
    old = unit.read_text().replace(f'WorkingDirectory={ROOT}',f'WorkingDirectory="{ROOT}"')
    import hashlib
    manifest['files'][str(unit)] = hashlib.sha256(old.encode()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    bindings = prefix/'config/nexus/integration/hyprland.lua'
    customization = prefix/'config/caelestia/hypr-user.lua'
    before = bindings.read_bytes(), customization.read_bytes()
    subprocess.run(cmd+['update-service','--prefix',str(prefix)],check=True)
    assert before == (bindings.read_bytes(),customization.read_bytes())
    assert f'WorkingDirectory={ROOT}\n' in unit.read_text()
    assert f'WorkingDirectory="{ROOT}"' not in unit.read_text()
    assert json.loads(manifest_path.read_text())['shortcuts'] == 'SUPER + CTRL + SHIFT + C/V'
    # A different unrecognized edit remains protected.
    unit.write_text(unit.read_text()+'# user customization\n')
    result = subprocess.run(cmd+['update-service','--prefix',str(prefix)],capture_output=True)
    assert result.returncode == 1
    assert '# user customization' in unit.read_text()
