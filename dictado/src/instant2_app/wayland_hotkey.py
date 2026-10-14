"""Hold-to-talk through the XDG GlobalShortcuts portal on Wayland.

Hyprland exposes this portal through xdg-desktop-portal-hyprland. The
compositor owns the actual key binding; this client receives Activated and
Deactivated events and keeps the audio session aligned with press/release.
"""
import asyncio
import logging
import os
import secrets
import threading

log = logging.getLogger("instant2")

_PORTAL = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
_REQUEST = "org.freedesktop.portal.Request"
_SESSION = "org.freedesktop.portal.Session"
_SHORTCUT_ID = "instant2-push-to-talk"
_REQUEST_TIMEOUT = 45

_KEYSYM = {
    "backspace": "BackSpace", "tab": "Tab", "enter": "Return",
    "return": "Return", "esc": "Escape", "escape": "Escape",
    "space": "space", "delete": "Delete", "insert": "Insert",
    "home": "Home", "end": "End", "page_up": "Page_Up",
    "page_down": "Page_Down", "left": "Left", "right": "Right",
    "up": "Up", "down": "Down", "caps_lock": "Caps_Lock",
    "num_lock": "Num_Lock", "scroll_lock": "Scroll_Lock",
    "pause": "Pause", "print_screen": "Print",
}


def _trigger_for(key_name):
    key = str(key_name or "f9").strip().lower()
    if key in _KEYSYM:
        return _KEYSYM[key]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return key
    if key.startswith("f") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return f"F{number}"
    raise ValueError(f"la tecla {key!r} no se puede registrar en el portal Wayland")


def _shortcut_id(key_name):
    key = str(key_name or "").strip().lower()
    if key == "f9":
        return _SHORTCUT_ID
    safe_key = "".join(char if char.isalnum() else "-" for char in key).strip("-") or "custom"
    return f"instant2-push-to-talk-{safe_key}"

class PortalHotkey:
    """A global press/release hotkey managed by the desktop compositor."""

    def __init__(self, key_name):
        self.key_name = str(key_name or "f9").strip().lower()
        if not self.key_name:
            raise ValueError("se necesita una tecla para dictar")
        self.shortcut_id = _shortcut_id(self.key_name)
        self.trigger = _trigger_for(self.key_name)
        self._thread = None
        self._loop = None
        self._shutdown = None
        self._stop_requested = threading.Event()
        self._pressed = False
        self._session_path = None
        self._on_press = None
        self._on_release = None
        self._on_error = None
        self._on_ready = None

    def set_error_callback(self, callback):
        self._on_error = callback

    def set_ready_callback(self, callback):
        self._on_ready = callback

    def start(self, on_press, on_release, interval=0.01):
        del interval
        if self._thread is not None and self._thread.is_alive():
            log.warning("hotkey portal Wayland ya activo; ignoro segundo start.")
            return
        self._on_press = on_press
        self._on_release = on_release
        self._stop_requested.clear()
        self._pressed = False
        self._thread = threading.Thread(
            target=self._thread_main, name="instant2-wayland-hotkey", daemon=True)
        self._thread.start()

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            message = self._helpful_error(exc)
            log.error("hotkey Wayland no disponible: %s", message)
            self._release_if_pressed()
            callback = self._on_error
            if callback is not None:
                try:
                    callback(message)
                except Exception:
                    log.exception("falló el aviso del hotkey Wayland")

    @staticmethod
    def _helpful_error(exc):
        detail = str(exc).strip()
        message = (
            "No se pudo registrar la tecla de dictado en Wayland. "
            "Revisá que xdg-desktop-portal y el portal del compositor estén activos."
        )
        if PortalHotkey._is_hyprland():
            message += (
                " En Hyprland, consultá `hyprctl globalshortcuts` y asociá "
                "el atajo push-to-talk."
            )
        return f"{message} ({detail})" if detail else message

    @staticmethod
    def _is_hyprland():
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
                    or "hyprland" in desktop)

    async def _run(self):
        from dbus_fast import BusType, Variant
        from dbus_fast.aio import MessageBus
        from dbus_fast.message import Message, MessageType

        self._Message = Message
        self._MessageType = MessageType
        self._Variant = Variant
        self._loop = asyncio.get_running_loop()
        self._shutdown = asyncio.Event()
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        try:
            session = await self._create_session(bus)
            self._session_path = session
            shortcuts = await self._list_shortcuts(bus, session)
            first_registration = self.shortcut_id not in shortcuts
            if first_registration:
                shortcuts |= await self._bind_shortcut(bus, session)
            if self.shortcut_id not in shortcuts:
                raise RuntimeError("el portal no confirm? la tecla de dictado")

            await self._add_match(
                bus,
                "type='signal',path='{}',interface='{}'".format(
                    session, _SHORTCUTS))
            bus.add_message_handler(self._on_portal_signal)
            log.info("tecla de dictado Wayland registrada (%s); compositor=%s.",
                     self.key_name.upper(),
                     "Hyprland" if self._is_hyprland() else "portal")
            if first_registration and self._on_ready is not None:
                try:
                    if self._is_hyprland():
                        message = (
                            "Hyprland: asociá push-to-talk con "
                            "hyprctl globalshortcuts.")
                    else:
                        message = (
                            "Atajo Wayland registrado. Revisá los atajos "
                            "globales de tu escritorio.")
                    self._on_ready(message)
                except Exception:
                    log.exception("falló el aviso de configuración del hotkey")
            if not self._stop_requested.is_set():
                await self._shutdown.wait()
        finally:
            if self._session_path:
                try:
                    reply = await bus.call(Message(
                        destination=_PORTAL, path=self._session_path,
                        interface=_SESSION, member="Close"))
                    if reply.message_type == MessageType.ERROR:
                        log.debug("no se pudo cerrar la sesión portal: %s",
                                  reply.body[0] if reply.body else "error")
                except Exception:
                    log.debug("falló el cierre de la sesión portal", exc_info=True)
            bus.disconnect()
            self._session_path = None

    async def _create_session(self, bus):
        token = self._token()
        result = await self._portal_request(
            bus, "CreateSession", "a{sv}", [{
                "handle_token": self._Variant("s", token),
                "session_handle_token": self._Variant("s", self._token()),
            }], token)
        session = self._value(result.get("session_handle"))
        if not session:
            raise RuntimeError("el portal no devolvió una sesión de atajos")
        return str(session)

    async def _list_shortcuts(self, bus, session):
        token = self._token()
        result = await self._portal_request(
            bus, "ListShortcuts", "oa{sv}", [session, {
                "handle_token": self._Variant("s", token),
            }], token)
        return self._bound_ids(result)

    async def _bind_shortcut(self, bus, session):
        token = self._token()
        shortcuts = [(self.shortcut_id, {
            "description": self._Variant("s", "Instant2: mantener para dictar"),
            "preferred_trigger": self._Variant("s", self.trigger),
        })]
        result = await self._portal_request(
            bus, "BindShortcuts", "oa(sa{sv})sa{sv}",
            [session, shortcuts, "", {
                "handle_token": self._Variant("s", token),
            }], token)
        return self._bound_ids(result)

    @staticmethod
    def _bound_ids(result):
        shortcuts = PortalHotkey._value(result.get("shortcuts", [])) or []
        return {str(item[0]) for item in shortcuts if item}

    async def _portal_request(self, bus, member, signature, body, token):
        Message = self._Message
        MessageType = self._MessageType
        loop = asyncio.get_running_loop()
        response = loop.create_future()
        rule = (
            "type='signal',interface='{}',member='Response'".format(_REQUEST))
        await self._add_match(bus, rule)

        def receive(message):
            if (message.message_type != MessageType.SIGNAL
                    or message.interface != _REQUEST
                    or message.member != "Response"
                    or not (message.path or "").endswith("/" + token)
                    or response.done()):
                return
            code = int(message.body[0]) if message.body else 2
            results = message.body[1] if len(message.body) > 1 else {}
            if code == 0:
                response.set_result(results)
            else:
                response.set_exception(RuntimeError(
                    f"el portal canceló la solicitud ({code})"))

        bus.add_message_handler(receive)
        try:
            reply = await bus.call(Message(
                destination=_PORTAL, path=_PORTAL_PATH,
                interface=_SHORTCUTS, member=member,
                signature=signature, body=body))
            if reply.message_type == MessageType.ERROR:
                reason = reply.body[0] if reply.body else "error D-Bus"
                raise RuntimeError(str(reason))
            return await asyncio.wait_for(response, timeout=_REQUEST_TIMEOUT)
        finally:
            bus.remove_message_handler(receive)
            await self._remove_match(bus, rule)

    async def _add_match(self, bus, rule):
        Message = self._Message
        reply = await bus.call(Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="AddMatch",
            signature="s", body=[rule]))
        if reply.message_type == self._MessageType.ERROR:
            reason = reply.body[0] if reply.body else "no se pudo suscribir a D-Bus"
            raise RuntimeError(str(reason))

    async def _remove_match(self, bus, rule):
        try:
            reply = await bus.call(self._Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="RemoveMatch",
                signature="s", body=[rule]))
            if reply.message_type == self._MessageType.ERROR:
                log.debug("no se pudo retirar match D-Bus: %s",
                          reply.body[0] if reply.body else "error")
        except Exception:
            log.debug("falló retiro de match D-Bus", exc_info=True)

    @staticmethod
    def _value(value):
        return getattr(value, "value", value)

    @staticmethod
    def _token():
        return "instant2_" + secrets.token_hex(8)

    def _on_portal_signal(self, message):
        if (message.message_type != self._MessageType.SIGNAL
                or message.interface != _SHORTCUTS
                or message.path != self._session_path
                or len(message.body) < 2
                or str(message.body[1]) != self.shortcut_id):
            return
        if message.member == "Activated" and not self._pressed:
            self._pressed = True
            self._on_press()
        elif message.member == "Deactivated" and self._pressed:
            self._pressed = False
            self._on_release()

    def _release_if_pressed(self):
        was_pressed, self._pressed = self._pressed, False
        if was_pressed and self._on_release is not None:
            try:
                self._on_release()
            except Exception:
                log.exception("fall? el release al cerrar hotkey Wayland")

    def stop(self):
        self._stop_requested.set()
        if self._loop is not None and self._shutdown is not None:
            try:
                self._loop.call_soon_threadsafe(self._shutdown.set)
            except RuntimeError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._release_if_pressed()
