"""Registers global shortcuts via KDE's kglobalaccel over D-Bus, using a
connection that stays open for the app's entire lifetime.

Root cause of the earlier broken implementation (see MIGRATION_LINUX.md,
hallazgo #3): doRegister/setShortcut were each sent through a fresh `busctl`
subprocess that disconnects the instant its single call returns. Watching
live D-Bus traffic with `busctl --user monitor org.kde.kglobalaccel` while
Nexus started proved it: doRegister came from unique name :1.416, setShortcut
from :1.417 -- two different connections, both gone within milliseconds.
kglobalaccel ties a component's live object (and its ability to actually
fire) to the connection that registered it; once that connection drops, the
binding stops working even though the key sequence stays written to
~/.config/kglobalshortcutsrc.

Fix: use dbus_next (pure-Python, explicit D-Bus type signatures -- PySide6's
own QtDBus module can't reliably send 'ai'/'u' typed arguments from Python,
see the same file in git history) over a single asyncio connection that
lives as long as the Qt app does, bridged into the Qt event loop via qasync
(see app.py). registration and signal reception happen on that one
connection, exactly like KGlobalAccel::self() does internally in a C++ KDE
app.

Confirmed live on Erwin (2026-08-24) with `busctl --user monitor` plus a
side-by-side test: a *brand new* component id registered this same way was
immediately reachable via `busctl call .../component/<id> ... allShortcutInfos`,
while the old id "nexus_linux" kept returning "No such object path" even
through this fixed code path -- it had accumulated corrupted state from many
earlier broken registration attempts (crashes, disconnects, re-registrations)
across debugging sessions. Renamed the component to "nexus_linux_app" to
start clean rather than fight kglobalaccel's internal state for the old id."""

import logging

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QKeySequence

COMPONENT = "nexus_linux_app"
COMPONENT_FRIENDLY = "Nexus Linux"

log = logging.getLogger("nexus.shortcuts")


def _key_to_int(key_sequence: str) -> int | None:
    seq = QKeySequence(key_sequence)
    if seq.isEmpty():
        return None
    return seq[0].toCombined()


class GlobalShortcuts(QObject):
    triggered = Signal(str)  # action_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bus: MessageBus | None = None
        self._registered_actions: dict[str, str] = {}  # action_id -> display_name

    async def connect_bus(self) -> bool:
        try:
            self._bus = await MessageBus().connect()
        except Exception as exc:  # dbus_next raises plain Exception subclasses on connect failure
            log.warning("No se pudo conectar al bus de sesión: %s", exc)
            return False

        await self._bus.call(Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="AddMatch", signature="s",
            body=[f"type='signal',interface='org.kde.kglobalaccel.Component',"
                  f"member='globalShortcutPressed',path='/component/{COMPONENT}'"],
        ))
        self._bus.add_message_handler(self._on_message)
        return True

    async def register(self, action_id: str, display_name: str, key_sequence: str) -> bool:
        if self._bus is None:
            return False

        key_int = _key_to_int(key_sequence)
        if key_int is None:
            log.warning("Secuencia de teclas inválida: %s", key_sequence)
            return False

        action_tuple = [COMPONENT, action_id, COMPONENT_FRIENDLY, display_name]

        reply = await self._bus.call(Message(
            destination="org.kde.kglobalaccel", path="/kglobalaccel",
            interface="org.kde.KGlobalAccel", member="doRegister",
            signature="as", body=[action_tuple],
        ))
        if reply.message_type == MessageType.ERROR:
            log.warning("doRegister falló para %s: %s", action_id, reply.body)
            return False

        reply = await self._bus.call(Message(
            destination="org.kde.kglobalaccel", path="/kglobalaccel",
            interface="org.kde.KGlobalAccel", member="setShortcut",
            # flags=2 (SetPresent) -- verified live on Erwin (2026-08-24) with a
            # flag sweep (0, 1, 2, 4) + real ydotool keypress + signal listener:
            # only flag=2 actually activates the binding for real dispatch.
            # flags=0 (what every earlier attempt used) writes the key sequence
            # to kglobalshortcutsrc and the component reports it via
            # allShortcutInfos, but KWin never routes a real key event to it --
            # a fully silent, inert "default". This -- not the component-name
            # corruption from the previous debugging round -- was the actual
            # root cause of shortcuts never firing.
            signature="asaiu", body=[action_tuple, [key_int], 2],
        ))
        if reply.message_type == MessageType.ERROR:
            log.warning("setShortcut falló para %s: %s", action_id, reply.body)
            return False

        self._registered_actions[action_id] = display_name
        log.info("Atajo global registrado: %s -> %s (%s)", key_sequence, action_id, display_name)
        return True

    def _on_message(self, msg: Message):
        if (msg.message_type == MessageType.SIGNAL
                and msg.member == "globalShortcutPressed"
                and msg.body and msg.body[1] in self._registered_actions):
            self.triggered.emit(msg.body[1])
        return False  # let other handlers see it too
