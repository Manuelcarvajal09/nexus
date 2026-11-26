"""Hyprland placement for our own surfaces; Qt retains logical DPI sizing."""
import json
import logging
import os
import re
import subprocess
from PySide6.QtCore import QTimer, QRect
from PySide6.QtGui import QGuiApplication, QCursor

log = logging.getLogger('nexus.desktop')

def query(kind):
    result = subprocess.run(['hyprctl', '-j', kind], capture_output=True, timeout=1)
    if result.returncode:
        raise ValueError('Hyprland query failed')
    return json.loads(result.stdout)

def active_screen():
    if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        try:
            monitor = next(m for m in query('monitors') if m.get('focused'))
            screen = next((s for s in QGuiApplication.screens() if s.name() == monitor['name']), None)
            if screen:
                return screen
        except (OSError, ValueError, StopIteration, KeyError, subprocess.TimeoutExpired):
            pass
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

def usable_geometry(screen):
    rect = screen.availableGeometry()
    if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        try:
            monitor = next(m for m in query('monitors') if m.get('name') == screen.name())
            left, top, right, bottom = monitor.get('reserved', [0, 0, 0, 0])
            # Hyprland reserved edges are logical; QScreen handles scale/rotation.
            reserved = screen.geometry().adjusted(int(left), int(top), -int(right), -int(bottom))
            rect = rect.intersected(reserved)
        except (OSError, ValueError, StopIteration, TypeError, subprocess.TimeoutExpired):
            pass
    return rect

def set_geometry(window, rect):
    # Wayland may report (0,0) despite QWidget.setGeometry's requested position.
    window._nexus_requested_geometry = QRect(rect)
    window.setGeometry(rect)

def center(window, preferred=None):
    screen = active_screen()
    if screen is None:
        return False
    rect = usable_geometry(screen)
    wanted_w, wanted_h = preferred or (window.width(), window.height())
    width = min(wanted_w, max(1, rect.width()-64))
    height = min(wanted_h, max(1, rect.height()-64))
    set_geometry(window, QRect(rect.x()+(rect.width()-width)//2, rect.y()+(rect.height()-height)//2, width, height))
    return True

def present(window):
    if not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        return
    # Capture desired logical geometry before the compositor maps the surface.
    rect = getattr(window, "_nexus_requested_geometry", None) or window.geometry()
    desired = (rect.x(), rect.y(), rect.width(), rect.height())
    def apply(attempt=0):
        if not window.isVisible():
            return
        try:
            client = next((w for w in query('clients') if w.get('pid') == os.getpid() and w.get('title') == window.windowTitle()), None)
            if client is None:
                if attempt < 8:
                    QTimer.singleShot(35, lambda: apply(attempt+1))
                return
            address = client.get('address', '')
            if not re.fullmatch(r'0x[0-9a-fA-F]+', address):
                return
            selector = json.dumps('address:' + address)
            x, y, width, height = desired
            lua = (
                f'hl.dispatch(hl.dsp.window.float({{action="set",window={selector}}})); '
                f'hl.dispatch(hl.dsp.window.resize({{x={width},y={height},relative=false,window={selector}}})); '
                f'hl.dispatch(hl.dsp.window.move({{x={x},y={y},relative=false,window={selector}}})); '
                f'hl.dispatch(hl.dsp.window.alter_zorder({{mode="top",window={selector}}})); '
                f'hl.dispatch(hl.dsp.focus({{window={selector}}}))'
            )
            result = subprocess.run(['hyprctl', 'eval', lua], capture_output=True, timeout=1)
            if result.returncode or result.stdout.strip() != b'ok':
                log.warning('Hyprland rechazó posicionamiento; revisar prueba visual.')
        except (OSError, ValueError, subprocess.TimeoutExpired):
            log.warning('No se pudo posicionar la ventana Nexus.')
    QTimer.singleShot(0, apply)

# Geometry records contain only window metrics, never clip contents.
def _records():
    from . import paths
    try:
        data=json.loads((paths.CONFIG_DIR/'windows.json').read_text())
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError): return {}

def save(window, clients=None, monitors=None):
    from . import paths
    if not window.isVisible() or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'): return
    try:
        client=next(w for w in (clients if clients is not None else query('clients')) if w.get('pid')==os.getpid() and w.get('title')==window.windowTitle())
        monitor=next(m for m in (monitors if monitors is not None else query('monitors')) if m.get('id')==client.get('monitor'))
        record={'at':client['at'],'size':client['size'],'monitor':monitor['name']}
        previous=_records()
        if previous.get(window.windowTitle())==record: return
        previous[window.windowTitle()]=record
        paths.ensure_dirs()
        target=paths.CONFIG_DIR/'windows.json'; temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(previous)); temp.chmod(0o600); temp.replace(target)
    except (OSError,ValueError,KeyError,StopIteration,subprocess.TimeoutExpired): pass

def restore(window,preferred):
    record=_records().get(window.windowTitle())
    if not isinstance(record,dict): return center(window,preferred)
    screens=QGuiApplication.screens()
    screen=next((s for s in screens if s.name()==record.get('monitor')),None) or active_screen()
    if screen is None: return False
    try:
        rect=usable_geometry(screen); x,y=map(int,record['at']); w,h=map(int,record['size'])
        w=max(window.minimumWidth(),min(w,rect.width())); h=max(window.minimumHeight(),min(h,rect.height()))
        x=max(rect.x(),min(x,rect.right()-w+1)); y=max(rect.y(),min(y,rect.bottom()-h+1))
        set_geometry(window,QRect(x,y,w,h)); return True
    except (ValueError,TypeError,KeyError): return center(window,preferred)

_tracked=[]
_tracker=None

def track(window):
    import weakref
    from PySide6.QtWidgets import QApplication
    global _tracker
    _tracked.append(weakref.ref(window))
    if _tracker is not None:return
    def observe():
        from shiboken6 import isValid
        visible=[r() for r in _tracked if r() is not None and isValid(r()) and r().isVisible()]
        if not visible or not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):return
        try:
            clients=query('clients'); monitors=query('monitors')
            for surface in visible:save(surface,clients,monitors)
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    _tracker=QTimer(QApplication.instance());_tracker.timeout.connect(observe);_tracker.start(1000)

def restore_shelf(window):
    if window.windowTitle() in _records():return restore(window,(1200,390))
    screen=active_screen()
    if screen is None:return False
    rect=usable_geometry(screen)
    width=min(1600,max(760,round(rect.width()*.84)),max(1,rect.width()-64))
    height=min(390,max(380,round(rect.height()*.27)),max(1,rect.height()-64))
    set_geometry(window,QRect(rect.x()+(rect.width()-width)//2,rect.bottom()-height-31,width,height));return True
