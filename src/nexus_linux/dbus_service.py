"""Exposes a tiny D-Bus service (mx.com.storem.nexus /Nexus) with two no-arg
methods, ShowMainPanel and ShowStack. This exists so a KDE Custom Shortcut
(System Settings -> Shortcuts -> Custom Shortcuts -> Command/URL) can bind a
real key combo to a plain `qdbus6` call.

Why not register through kglobalaccel directly, like GlobalShortcuts does:
that path is unreliable on this system (see MIGRATION_LINUX.md, hallazgo #3
-- the registered component's D-Bus object becomes unreachable for reasons
not fully isolated). A user-created KDE Custom Shortcut sidesteps that
entirely: KDE's own shortcut editor owns the key grab, and it just runs a
command we control -- no custom component registration involved."""

import logging

from PySide6.QtCore import QObject, Slot
from PySide6.QtDBus import QDBusConnection

SERVICE_NAME = "mx.com.storem.nexus"
OBJECT_PATH = "/Nexus"

log = logging.getLogger("nexus.dbus_service")


class NexusDBusService(QObject):
    def __init__(self, show_main_panel, show_stack, parent=None):
        super().__init__(parent)
        self._show_main_panel = show_main_panel
        self._show_stack = show_stack

    @Slot()
    def ShowMainPanel(self) -> None:
        self._show_main_panel()

    @Slot()
    def ShowStack(self) -> None:
        self._show_stack()


def register(show_main_panel, show_stack, parent=None) -> NexusDBusService | None:
    service = NexusDBusService(show_main_panel, show_stack, parent)
    bus = QDBusConnection.sessionBus()

    if not bus.registerObject(OBJECT_PATH, service, QDBusConnection.RegisterOption.ExportAllSlots):
        log.warning("No se pudo registrar el objeto D-Bus %s", OBJECT_PATH)
        return None

    if not bus.registerService(SERVICE_NAME):
        log.warning("No se pudo registrar el servicio D-Bus %s: %s", SERVICE_NAME, bus.lastError().message())
        bus.unregisterObject(OBJECT_PATH)
        return None

    log.info("Servicio D-Bus %s registrado en %s", SERVICE_NAME, OBJECT_PATH)
    return service
