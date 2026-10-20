"""Panel de Instant2: ventana QtWebEngine con la UI en web (web/panel.html).

La migración separa el comportamiento de la ventana:

- `PanelLogic`: todo el estado y las acciones del panel, sin Qt. Los hilos de
  trabajo entregan por `TaskRunner` y el hilo de UI aplica con `drain()`. Los
  efectos visibles salen por `emit(kind, payload)`:
      state | toast | meter | progress | diagnostics | key_result | navigate | close
- `_WebPanel`: la ventana (host QML + WebEngineView + QWebChannel) que conecta
  el protocolo de `web/panel.html` con `PanelLogic`.

Contrato con la página (QWebChannel, objeto `bridge`):
    JS -> Python: bridge.call(json)   con {"op": ..., ...} (ver PanelLogic.handle)
    Python -> JS: señales state/toast/meter/progress/diagnostics/key_result
                  (cada payload es un JSON string)
La API pública hacia afuera es la de siempre: `run_gui`, `daemon_is_running`,
`stop_daemon`, `DeviceCatalog`, el canal de instancia única y los helpers.
"""
import ctypes
import json
import logging
import ntpath
import os
import re
import signal
import subprocess
import sys
import threading
import time

from instant2_app import audio, autostart, config, context, hotkey, models
from instant2_app.launch import app_command, app_environment
from instant2_app.identity import APP_NAME
from instant2_app.paths import config_dir, resolve_data_dir

log = logging.getLogger("instant2")

_APP_USER_MODEL_ID = f"getodevel-source.{APP_NAME}.0.1"
UPDATE_CHECK_INTERVAL = 86400
DAEMON_MISSING_DETAIL = ("Instant2 no pudo iniciar. Revisá Diagnóstico.")
STATUS_DETAIL = ("Enfocá el campo donde querés escribir. "
                 "El servicio de dictado y el LLM tienen estados separados.")
MIC_UNAVAILABLE_LABEL = "El micrófono seleccionado no está disponible"

_OVERLAY_STYLES = ("orbital", "classic")
_THREAD_OPTIONS = (1, 2, 4, 6, 8)
_OVERLAY_STYLE_DEFAULT = "orbital"
_THREADS_DEFAULT = 4


def _sanitize_overlay_style(value):
    """Fallback para configs viejas o editadas a mano: nunca en blanco."""
    return value if value in _OVERLAY_STYLES else _OVERLAY_STYLE_DEFAULT


def _sanitize_threads(value):
    """Clamp a 1..8 y snap a las opciones reales del select."""
    if isinstance(value, bool):
        return _THREADS_DEFAULT
    try:
        count = int(value)
    except (TypeError, ValueError):
        return _THREADS_DEFAULT
    count = max(1, min(8, count))
    return min(_THREAD_OPTIONS, key=lambda option: (abs(option - count), option))


def _threads_is_valid(value):
    """Lo que el select puede mostrar sin quedar en blanco."""
    if isinstance(value, bool):
        return False
    try:
        return int(value) in _THREAD_OPTIONS and float(value) == int(value)
    except (TypeError, ValueError):
        return False


_UPDATE_MODES = ("notify", "auto", "off")
# Tope de espera del instalador: copia los 4 GB de modelos, puede tardar.
_INSTALL_TIMEOUT = 3600
# Tope de cada probe del SO: tasklist y PowerShell no pueden colgarse.
_PROBE_TASKLIST_TIMEOUT = 5.0
_PROBE_POWERSHELL_TIMEOUT = 10.0
# Piso del presupuesto del probe: aunque quede poco tiempo para el
# deadline, el probe necesita su presupuesto completo para responder. Sin
# este piso, un daemon vivo parece "no verificable" cuando no queda tiempo.
_PROBE_BUDGET_FLOOR = _PROBE_TASKLIST_TIMEOUT + _PROBE_POWERSHELL_TIMEOUT
# Margen para frenar el daemon: tiene que ser MAYOR que el peor caso de un
# probe (5 s + 10 s), si no el deadline es inalcanzable y la actualización
# se cuelga sin poder distinguir por que.
DAEMON_STOP_DEADLINE = 20.0

DAEMON_RUNNING = "running"
DAEMON_STOPPED = "stopped"
DAEMON_UNKNOWN = "unknown"


def _friendly_error(exc):
    """(causa, acción) en criollo para un error de red/SO; el detalle crudo
    queda en el log. Nunca se muestra `str(exc)` sin traducir."""
    name = type(exc).__name__ if exc is not None else "Error"
    raw = str(exc) if exc is not None else ""
    lowered = f"{name} {raw}".casefold()
    net = ("urlerror", "timeout", "timed out", "connection", "network",
           "unreachable", "refused", "reset", "dns", "gaierror",
           "ssl", "certificate", "http", "404", "403", "500", "503",
           "rate-limit", "rate limit", "sin red", "offline")
    disk = ("no space", "no hay espacio", "disco lleno", "enospc",
            "disk full", "espacio")
    perm = ("permission", "permiso", "denied", "eacces", "eperm",
            "winerror 5", "unauthorized")
    busy = ("locked", "bloqueado", "in use", "en uso", "ebusy",
            "sharing violation", "winerror 32")
    if any(mark in lowered for mark in net):
        return ("Sin conexión al servidor.",
                "Revisá tu internet y reintentá. Si sigue, probá más tarde.")
    if any(mark in lowered for mark in disk):
        return ("Te quedaste sin espacio en disco.",
                "Liberá espacio (la descarga de voz pide ~1 GB libre) y reintentá.")
    if any(mark in lowered for mark in perm):
        return ("Al sistema no le gustó el permiso.",
                "Reabrí Instant2 normalmente (sin admin) y reintentá.")
    if any(mark in lowered for mark in busy):
        return ("El archivo está en uso por otro proceso.",
                "Cerrá Instant2 por completo (daemon incluido) y reintentá.")
    return ("Algo falló y no sabemos bien qué.",
            "Reintentá; si se repite, abrí Diagnóstico y copianos el texto.")


def _toast_error(logic, title, exc):
    """Toast accionable: causa+acción en criollo; detalle crudo solo al log."""
    cause, action = _friendly_error(exc)
    raw = str(exc) if exc is not None else ""
    detail = _public_error_detail(type(exc).__name__, raw)
    log.warning("%s: %s", title, detail, exc_info=True)
    logic.toast(title, f"{cause}\n{action}\nDetalle: {detail}", level="error")


def _toast_warn(logic, title, exc):
    """Variante warn de `_toast_error` (chequeos y descargas)."""
    cause, action = _friendly_error(exc)
    raw = str(exc) if exc is not None else ""
    detail = _public_error_detail(type(exc).__name__, raw)
    log.warning("%s: %s", title, detail, exc_info=True)
    logic.toast(title, f"{cause}\n{action}\nDetalle: {detail}", level="warn")


def _public_error_detail(error_type, message):
    """Keep useful failure details without exposing local component names."""
    text = f"{error_type}: {message}" if message else error_type
    for name in ("parakeet", "silero", "ten-vad", "kokoro", "dora",
                 "qwen", "ollama", "llama.cpp", "llama-server"):
        text = re.sub(re.escape(name), "componente local", text, flags=re.IGNORECASE)
    return text


def _update_mode(cfg):
    """Modo de actualización con consentimiento: notify|auto|off."""
    mode = (cfg or {}).get("update_mode", "notify")
    if mode not in _UPDATE_MODES:
        return "notify"
    return mode


def _updates_disabled(cfg):
    """True si el usuario apagó todo chequeo (config o INSTANT2_NO_UPDATE)."""
    if (os.environ.get("INSTANT2_NO_UPDATE") or "").strip().lower() in (
            "1", "true", "yes", "y", "on", "si", "s"):
        return True
    return _update_mode(cfg) == "off"


def _verify_install_file(path, expected):
    """SHA256 del archivo justo antes de ejecutarlo; error si difiere."""
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if not expected or actual.lower() != str(expected).lower():
        raise ValueError(
            "El archivo de actualización cambió después de verificarse "
            f"(esperado {str(expected)[:16]}…, actual {actual[:16]}…): "
            "no se instala nada. Reintentá la descarga.")
    return True



def _workdir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _update_log_path():
    """Log del helper de actualización: %APPDATA%\\instant2\\update.log."""
    return os.path.join(config_dir(), "update.log")


def _installer_log_path():
    """Log de Inno Setup: %APPDATA%\\instant2\\installer.log."""
    return os.path.join(config_dir(), "installer.log")


def _pending_update_path():
    """Nota de la actualización en curso: %APPDATA%\\instant2\\pending-update.json."""
    return os.path.join(config_dir(), "pending-update.json")


def _write_pending_update(version, log_path):
    """Deja anotada la actualización que se pidió aplicar.

    El instalador y el helper tienen que cerrar la app para poder
    reemplazar el binario, así que la comprobación de la versión instalada
    recién puede hacerse en el arranque siguiente.
    """
    payload = {"version": str(version or ""), "log": str(log_path or "")}
    try:
        path = _pending_update_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError:
        log.warning("no se pudo anotar la actualización pendiente",
                    exc_info=True)


def _read_pending_update():
    try:
        with open(_pending_update_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clear_pending_update():
    try:
        os.remove(_pending_update_path())
    except OSError:
        pass


def _log_tail(path, lines=8):
    """Últimas líneas de un log: el toast del fallo lleva el detalle."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read().splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _installer_failure(returncode, log_path):
    """Error con el log de Inno adjunto: el toast tiene que poder explicar."""
    message = (f"El instalador terminó con código {returncode}; no se "
               f"actualizó Instant2. Log: {log_path}")
    tail = _log_tail(log_path)
    if tail:
        message += "\n\nÚltimas líneas del log:\n" + tail
    return RuntimeError(message)


def _pending_update_report():
    """(título, mensaje, nivel) con el resultado de la actualización anterior.

    La app que está corriendo ES la versión instalada: acá se la compara con
    la que se pidió. Si no coinciden, la actualización no se aplicó y el
    log dice por qué.
    """
    data = _read_pending_update()
    if not data:
        return None
    expected = str(data.get("version") or "")
    log_path = str(data.get("log") or "")
    current = _current_version()
    _clear_pending_update()
    if expected and expected != current:
        message = (f"Se pidió actualizar a v{expected} y esta app sigue en "
                   f"v{current}: la actualización no se aplicó.")
        if log_path:
            message += f"\n\nLog: {log_path}"
            tail = _log_tail(log_path)
            if tail:
                message += "\n\nÚltimas líneas del log:\n" + tail
        return ("Actualización sin aplicar", message, "error")
    return ("Actualización aplicada", f"Instant2 quedó en v{current}.", "info")


def _pid_value():
    from instant2_app.daemon import pid_path
    try:
        with open(pid_path(), encoding="utf-8") as file:
            return int(file.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _run_probe(args, timeout):
    """Ejecuta un probe del SO. Devuelve (respondió, código, salida).

    `encoding`/`errors` explícitos: la salida de tasklist o de PowerShell
    puede traer bytes que no son UTF-8 y un UnicodeDecodeError (un
    ValueError, no un OSError) abortaría la actualización.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, creationflags=flags)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False, None, ""
    return True, result.returncode, result.stdout or ""


def _split_probe_budget(budget):
    """Reparte los segundos disponibles entre tasklist y PowerShell.

    Es el UNICO reparto: el timeout que sale de acá se pasa tal cual al
    probe de PowerShell. Repartirlo otra vez le dejaba 6 s en vez de 10 y
    `Get-CimInstance Win32_Process` con el WMI frio se pasa de 6 s con
    facilidad (el probe caia en 'desconocido' y frenaba la actualizacion).
    """
    if budget is None:
        return _PROBE_TASKLIST_TIMEOUT, _PROBE_POWERSHELL_TIMEOUT
    budget = max(float(budget), 0.1)
    tasklist = min(_PROBE_TASKLIST_TIMEOUT, budget * 0.4)
    powershell = min(_PROBE_POWERSHELL_TIMEOUT, budget - tasklist)
    return tasklist, max(powershell, 0.1)


def daemon_is_running():
    """True si el daemon de Instant2 está corriendo.

    Un probe que no responde NO es "está corriendo": el estado real lo
    devuelve `daemon_probe_state`, que distingue las tres cosas.
    """
    return daemon_probe_state() == DAEMON_RUNNING


def daemon_probe_state(budget=None):
    """Estado del daemon: 'running' | 'stopped' | 'unknown'.

    'unknown' es "el probe no pudo responder", no "no está": un probe
    caído no significa que el PID sea de Instant2 ni que el daemon esté
    apagado. `budget` acota los segundos que puede tardar el probe.
    """
    pid = _pid_value()
    if pid is None:
        return DAEMON_STOPPED
    if os.name == "nt":
        tasklist_budget, ps_budget = _split_probe_budget(budget)
        responded, code, listing = _run_probe(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            tasklist_budget)
        if not responded or code != 0:
            return DAEMON_UNKNOWN
        accepted_images = {"python.exe", "pythonw.exe", "instant2.exe"}
        if getattr(sys, "frozen", False):
            accepted_images.add(ntpath.basename(sys.executable).lower())
        for row in listing.splitlines():
            columns = [part.strip().strip('"') for part in row.split('","')]
            if len(columns) > 1 and columns[1] == str(pid):
                if columns[0].lower() not in accepted_images:
                    return DAEMON_STOPPED
                ours = _pid_is_instant(pid, timeout=ps_budget)
                if ours is None:
                    return DAEMON_UNKNOWN
                return DAEMON_RUNNING if ours else DAEMON_STOPPED
        return DAEMON_STOPPED
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return DAEMON_STOPPED
    except PermissionError:
        # EPERM: el proceso existe pero no tenemos permiso -> vivo (G4).
        return DAEMON_RUNNING
    except OSError:
        return DAEMON_UNKNOWN
    ours = _pid_is_instant(pid)
    if ours is None:
        return DAEMON_UNKNOWN
    return DAEMON_RUNNING if ours else DAEMON_STOPPED


def _display_input_name(name):
    label = name.strip()
    open_paren = label.find("(")
    if open_paren >= 0 and label.endswith(")"):
        kind, detail = label[:open_paren].strip(), label[open_paren + 1:-1].strip()
        label = detail if kind.casefold() == "microphone" else f"{kind}: {detail}"
    prefix, separator, rest = label.partition("-")
    if separator and prefix.strip().isdigit():
        label = rest.strip()
    return label


def _pid_is_instant(pid, timeout=None):
    """El PID file puede quedar rancio y el SO reciclar el número: antes de
    matar se confirma que la línea de comando sea de Instant2.

    True si es Instant2, False si es de otro proceso y None si el probe no
    pudo responder: un probe caído no es "es Instant2".

    `timeout` son los segundos del probe de PowerShell (no es un
    presupuesto total: no se reparte otra vez acá).
    """
    if os.name == "nt":
        if timeout is None:
            timeout = _PROBE_POWERSHELL_TIMEOUT
        responded, code, command = _run_probe(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
            timeout)
        if not responded or code != 0:
            return None
        command = (command or "").casefold()
        args = [part.strip('"') for part in command.split()]
        return "instant2" in command and "run" in args[1:]
    command = ""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as handle:
            command = handle.read().replace(b"\0", b" ").decode(
                "utf-8", "replace")
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        responded, code, output = _run_probe(
            ["ps", "-p", str(int(pid)), "-o", "args="],
            _PROBE_POWERSHELL_TIMEOUT)
        if not responded:
            return None
        if code != 0:
            return False
        command = output or ""
    command = command.casefold()
    args = [part.strip('"') for part in command.split()]
    return "instant2" in command and "run" in args[1:]


def stop_daemon():
    from instant2_app.daemon import clear_pid
    pid = _pid_value()
    if pid is None:
        return False
    state = daemon_probe_state()
    if state == DAEMON_UNKNOWN:
        # El probe no respondió: no se toca el pidfile ni se mata nada.
        # Un PID puede ser nuestro o no: no se afirma ninguna de las dos.
        log.warning("no se pudo verificar el PID %d; el daemon no se frena", pid)
        return False
    if state == DAEMON_STOPPED:
        # PID file rancio (proceso muerto o PID reciclado ajeno): se borra
        # solo si el contenido sigue siendo ese PID muerto (G1+G3). clear_pid
        # ahora acepta el PID esperado porque no es el getpid() del llamador.
        try:
            if _pid_value() == pid:
                clear_pid(pid)
        except OSError:
            pass
        return False
    if not _pid_is_instant(pid):
        log.warning("PID %d reciclado por el SO (no es Instant2); no se mata.", pid)
        return False
    if os.name == "nt":
        try:
            subprocess.Popen(
                ["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
        except OSError:
            return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


_GUI_SERVER_BASE = "instant2-gui"
_GUI_FRAME_MAX = 65536


def _frame_gui_request(page, start_daemon_on_open):
    """Serializa el pedido con prefijo de longitud (4 bytes big-endian).

    El framing evita que un payload fragmentado en varios readyRead se
    lea como JSON truncado (G7): el lector acumula hasta completar los N
    bytes anunciados y descarta lo que exceda _GUI_FRAME_MAX.
    """
    payload = json.dumps(
        {"page": page, "daemon": bool(start_daemon_on_open)}).encode("utf-8")
    import struct
    return struct.pack(">I", len(payload)) + payload


def _unframe_gui_requests(buffer):
    """Extrae los payloads completos de un buffer con framing; devuelve
    (payloads, resto). Los frames que exceden _GUI_FRAME_MAX se descartan
    (se salta su contenido) en vez de acumularlos sin cota."""
    import struct
    payloads, rest = [], bytes(buffer)
    while len(rest) >= 4:
        (size,) = struct.unpack(">I", rest[:4])
        if size > _GUI_FRAME_MAX:
            rest = rest[4 + size:] if len(rest) >= 4 + size else b""
            continue
        if len(rest) < 4 + size:
            break
        payloads.append(rest[4:4 + size])
        rest = rest[4 + size:]
    return payloads, rest


def _gui_server_name():
    """Canal de instancia única del panel; en Unix lleva el uid del usuario."""
    if os.name == "nt":
        return _GUI_SERVER_BASE
    return f"{_GUI_SERVER_BASE}-{os.getuid()}"


def _forward_to_existing_gui(page, start_daemon_on_open):
    """Pasa el pedido a la ventana ya abierta. True si había otra instancia.

    QLocalSocket/QLocalServer son el mecanismo portable de Qt: en Windows es
    la ruta de la bandeja (mutex + FindWindow), en Linux/macOS este canal.
    El pedido viaja con framing (prefijo de longitud de 4 bytes): el lector
    acumula fragmentos hasta completar el frame antes de parsear (G7).
    El panel contesta `ok`; sin esa confirmación este proceso, que sale
    enseguida, podría morir con el pedido todavía en el buffer (en Windows
    cerrar el socket con datos sin leer los descarta).
    """
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtNetwork import QLocalSocket

    socket = QLocalSocket()
    socket.connectToServer(_gui_server_name())
    if not socket.waitForConnected(500):
        return False
    socket.write(_frame_gui_request(page, start_daemon_on_open))
    socket.flush()
    loop = QEventLoop()
    socket.readyRead.connect(loop.quit)
    socket.disconnected.connect(loop.quit)
    if not socket.bytesAvailable():
        QTimer.singleShot(1000, loop.quit)
        loop.exec()
    ack = bytes(socket.readAll().data()) if socket.bytesAvailable() else b""
    socket.disconnectFromServer()
    return ack == b"ok"


def _install_gui_server(window):
    """La ventana atiende los pedidos de instancias nuevas (Linux/macOS)."""
    from PySide6.QtCore import QObject
    from PySide6.QtNetwork import QLocalServer

    name = _gui_server_name()
    # Un cierre sucio deja el socket huérfano: se limpia antes de escuchar.
    QLocalServer.removeServer(name)
    # QLocalServer necesita un QObject como parent: el panel web no lo es, su
    # ventana QML sí (en el doble de tests el propio window ya es QObject).
    parent = getattr(window, "root", window)
    if not isinstance(parent, QObject):
        parent = None
    server = QLocalServer(parent)
    if not server.listen(name):
        log.warning("sin canal de instancia única: %s", server.errorString())
        return None

    def on_connection():
        socket = server.nextPendingConnection()
        if socket is None:
            return
        pending = bytearray()

        def handle_frame(raw):
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                payload = {}
            if payload:
                window.apply_gui_action(
                    payload.get("page"), bool(payload.get("daemon")))

        def on_ready():
            # Compat: los lanzadores viejos escribían el JSON sin framing.
            # Se espera si el buffer es un frame parcial plausible; si no,
            # se trata como JSON legacy crudo (y la basura se acusa y
            # descarta como antes, sin romper el canal).
            import struct
            pending.extend(bytes(socket.readAll().data()))
            payloads, rest = _unframe_gui_requests(bytes(pending))
            if payloads:
                del pending[:len(pending) - len(rest)]
                for raw in payloads:
                    handle_frame(raw)
            else:
                if len(pending) >= 4:
                    (size,) = struct.unpack(">I", bytes(pending[:4]))
                    if size <= _GUI_FRAME_MAX and len(pending) < 4 + size:
                        return
                elif len(pending) < 4:
                    return
                try:
                    payload = json.loads(bytes(pending).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    payload = {}
                del pending[:]
                if payload:
                    window.apply_gui_action(
                        payload.get("page"), bool(payload.get("daemon")))
            # Acuse: el lanzador no sale hasta que el pedido llegó acá.
            socket.write(b"ok")
            socket.flush()
            socket.disconnectFromServer()

        socket.readyRead.connect(on_ready)
        if socket.bytesAvailable():
            on_ready()

    server.newConnection.connect(on_connection)
    return server


class DeviceCatalog:
    """Toolkit-independent microphone choice tracking and ambiguity handling."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.rows = ()
        self.devices = {}
        self.collisions = set()
        self.pending_device = None
        self.pending_identity = None

    @staticmethod
    def identity(device):
        if not device:
            return "", None
        name = " ".join(_display_input_name(str(device[1])).casefold().split())
        return name, device[0]

    def same_identity(self, left, right):
        if left[0] != right[0]:
            return False
        return left[0] not in self.collisions or left[1] == right[1]

    def remember(self, device):
        if device:
            self.pending_device = device
            self.pending_identity = self.identity(device)

    def refresh(self, preferred=None, preserve=False):
        previous = self.rows
        try:
            inputs = audio.input_choices()
        except Exception:
            log.exception("no pude listar micrófonos para el panel")
            inputs = []
        try:
            import sounddevice as sd
            default = tuple(sd.default.device)[0]
        except Exception:
            default = None
        default = audio.preferred_input_index(default, inputs) if inputs else None
        if preserve:
            preferred = preferred or self.pending_device
            current = None
            if preferred and inputs:
                old_index, old_name = preferred
                current = next((i for i, name, _ch, _rate in inputs
                                if i == old_index and name.casefold() == old_name.casefold()), None)
                if current is None:
                    old_display = _display_input_name(old_name).casefold()
                    was = [row for row in previous if _display_input_name(row[1]).casefold() == old_display]
                    now = [row for row in inputs if _display_input_name(row[1]).casefold() == old_display]
                    prior = next((row for row in was if row[0] == old_index and row[1].casefold() == old_name.casefold()), None)
                    if prior and len(was) == len(now) == 1 and (prior[2], prior[3]) == (now[0][2], now[0][3]):
                        current = now[0][0]
        else:
            current = audio.resolve_mic(self.cfg.get("mic_hint", ""), self.cfg.get("mic_index")) if inputs else None
            current = audio.preferred_input_index(current, inputs)
            if current is None:
                current = default
        self.rows = tuple(inputs)
        labels, devices, counts = [], {}, {}
        for index, name, _channels, _rate in inputs:
            display = _display_input_name(name)
            counts[display.casefold()] = counts.get(display.casefold(), 0) + 1
        self.collisions = {name for name, count in counts.items() if count > 1}
        selected = None
        for index, name, _channels, _rate in inputs:
            label = _display_input_name(name)
            if counts[label.casefold()] > 1:
                label += f" [{index}]"
            if index == default:
                label += " (predeterminado)"
            labels.append(label)
            devices[label] = (index, name)
            if index == current:
                selected = label
        self.devices = devices
        return labels, selected, bool(preserve and preferred and selected is None and labels)


from instant2_app.tasks import TaskRunner
from instant2_app.reading import PanelReadingActions


class PanelLogic(PanelReadingActions):
    """Estado y acciones del panel, sin Qt. Ver el docstring del módulo."""

    def __init__(self, page="home", autostart_override=None,
                 start_daemon_on_open=False, emit=None, tasks=None, schedule=None):
        self.emit = emit or (lambda kind, payload: None)
        self.tasks = tasks or TaskRunner()
        self.tasks.on_idle = lambda: self.emit("idle", {})
        # El shell inyecta su QTimer.singleShot; en tests alcanza sin reloj.
        self.schedule = schedule or (lambda milliseconds, fn: None)
        self.page = page
        self.cfg = config.load()
        from instant2_app.llm_resources import detect_resources, format_summary
        self._llm_resources = detect_resources()
        self._llm_resources_summary = format_summary(self._llm_resources)
        self._llm_runtime_state = {
            "status": "configured" if self.cfg.get("llm_url") else "needs_setup",
            "detail": ("Servicio configurado; el proceso de dictado publica el estado del servicio."
                       if self.cfg.get("llm_url") else
                       "Sin servicio configurado; el dictado local sigue disponible."),
            "device": "", "owned": False,
        }
        self._llm_runtime_next_check = 0.0
        self.data_dir = resolve_data_dir()
        self.catalog = DeviceCatalog(self.cfg)
        self.model_ready = all(models.check(self.data_dir).values())
        self._autostart_override = autostart_override
        self._start_daemon_on_open = start_daemon_on_open
        self._closed = False
        self._daemon_start_pending = False
        self._daemon_stop_pending = False
        self._daemon_start_token = 0
        self._daemon_check_pending = False
        self._daemon_state_ready = False
        self._last_daemon_running = False
        self._microphones_loaded = False
        self._microphones_refreshing = False
        self._microphone_test_pending = False
        self._settings_daemon_state = None
        self._restart_needed = False
        self._restart_settings = None
        self._saved_settings = None
        self._loaded_context_name = None
        self._vocab_rows_cache = []
        self._available_update = None
        self._pending_update = None
        self._ready_update = None
        self._update_checking = False
        self._update_download_pending = False
        self._update_install_pending = False
        from instant2_app.update import install_mode
        self._update_source_mode = install_mode() == "source"
        self.update_progress = {"visible": False, "percent": 0, "text": ""}
        self._update_silent_done = False
        self._diagnostic_running = False
        self._diagnostics_open = False
        self._diagnostics_text = "Ejecutando comprobaciones…"
        self._toast_callbacks = {}
        self._advanced_warned = set()
        self._key_capturing = False
        self._key_capture_target = "dictation"
        self.mic_labels = []
        self.mic_selected = None
        self.key_value = hotkey.key_label(self.cfg.get("key", "f9"))
        self.read_key_value = hotkey.key_label(self.cfg.get("read_key", "f10"))
        self.status_var = "Listo para dictar"
        self.status_detail = STATUS_DETAIL.format(key=self.key_value)
        self.meter = {"percent": 0, "text": "La prueba no guarda audio."}
        self.model_status = ""
        self.model_progress = {"visible": False, "percent": 0}
        self._model_download_pending = False
        self._model_canceling = False
        self._model_cancel_event = None
        from instant2_app import tts
        self._tts_reader = None
        self._tts_idle_timer = None
        self._tts_cancel_event = None
        self._tts_preferences = tts.load_preferences()
        tts_is_ready = tts.model_ready(self.data_dir)
        self._tts_state = {
            **self._tts_preferences,
            "status": "ready" if tts_is_ready else "missing",
            "model_ready": tts_is_ready,
            "busy": False,
            "canceling": False,
            "percent": 100 if tts_is_ready else 0,
            "message": ("La voz está lista en este equipo."
                        if tts_is_ready else
                        "La primera lectura descargará los componentes de voz (aprox. 326 MB)."),
        }
        self.update_button = "Buscar actualizaciones"
        self.settings_status = ""
        self._autostart_enabled = False
        self._init_settings(autostart_override)
        self._update_model_status()

    # ------------------------------------------------------------------ estado

    def state_payload(self):
        configured = context.profiles(self.cfg)
        try:
            active = context.active_name(self.cfg)
        except Exception:
            active = context.DEFAULT_PROFILE
        if self._daemon_stop_pending:
            daemon_status = "stopping"
            daemon_title = "Deteniendo dictado…"
        elif self._daemon_start_pending:
            daemon_status = "starting"
            daemon_title = "Iniciando dictado…"
        elif not self._daemon_state_ready or self._daemon_check_pending:
            daemon_status = "checking"
            daemon_title = "Comprobando Instant2…"
        elif self._last_daemon_running:
            daemon_status = "ok"
            daemon_title = "Dictado activo"
        else:
            daemon_status = "down"
            daemon_title = "Dictado detenido"
        return {
            "version": _current_version(),
            "page": self.page,
            "status": {
                "state": daemon_status,
                "title": daemon_title,
                "detail": self.status_detail,
                "disabled_reason": self._disabled_reason(),
                "can_start": bool(self._daemon_state_ready and self._microphones_loaded
                                  and self._selected_device() is not None
                                  and self.model_ready
                                  and not self._daemon_start_pending
                                  and not self._daemon_stop_pending
                                  and not self._last_daemon_running),
                "can_toggle": bool(self._daemon_state_ready and self._microphones_loaded
                                   and self._selected_device() is not None
                                   and self.model_ready
                                   and not self._daemon_start_pending
                                   and not self._daemon_stop_pending),
                "can_stop": bool(self._last_daemon_running
                                 and not self._daemon_stop_pending),
            },
            "key_label": self.key_value,
            "read_key_label": self.read_key_value,
            "autostart": bool(self._autostart_enabled),
            "advanced": {
                "llm_url": self.cfg.get("llm_url", ""),
                "llm_model": self.cfg.get("llm_model", ""),
                "llm_normal_enabled": bool(self.cfg.get("llm_normal_enabled", True)),
                "llm_server_path": self.cfg.get("llm_server_path", ""),
                "llm_model_path": self.cfg.get("llm_model_path", ""),
                "llm_acceleration": self.cfg.get("llm_acceleration", "auto"),
                "resource_mode": self.cfg.get("resource_mode", "balanced"),
                "llm_resources": self._llm_resources_summary,
                "llm_token_set": bool(self.cfg.get("llm_token", "")),
                "threads": _sanitize_threads(self.cfg.get("threads", _THREADS_DEFAULT)),
                "sound": bool(self.cfg.get("sound", False)),
                "overlay_style": _sanitize_overlay_style(
                    self.cfg.get("overlay_style", _OVERLAY_STYLE_DEFAULT)),
                "max_seg": self.cfg.get("max_seg", 20),
                "vad_model": self.cfg.get("vad_model", "silero")
                if self.cfg.get("vad_model") in ("silero", "ten") else "silero",
                "blank_penalty": self.cfg.get("blank_penalty", 0.0),
                "update_mode": _update_mode(self.cfg),
                "key_label": self.key_value,
                },
            "llm_runtime": dict(self._llm_runtime_state),
            "tts": dict(self._tts_state),
            "checklist": self._checklist(),
            "mic": {
                "labels": list(self.mic_labels),
                "selected": self.mic_selected,
                "available": self._selected_device() is not None,
                "has_devices": bool(self.catalog.devices),
                "unavailable_label": (MIC_UNAVAILABLE_LABEL
                                      if self.mic_selected == MIC_UNAVAILABLE_LABEL
                                      and self._selected_device() is None else ""),
                "refreshing": bool(self._microphones_refreshing
                                   or not self._microphones_loaded),
                "testing": bool(self._microphone_test_pending),
                "meter": dict(self.meter),
            },
            "model": {
                "ready": bool(self.model_ready),
                "status": self.model_status,
                "progress": dict(self.model_progress),
                "downloading": bool(self._model_download_pending),
                "canceling": bool(self._model_canceling),
                "button": ("Reintentar descarga"
                           if not self.model_ready
                           and (self.model_status.startswith("No se pudo")
                                or self.model_status.startswith("Descarga cancelada"))
                           else "Preparar reconocimiento"),
            },
            "vocab": {
                "profiles": list(configured),
                "active": active,
                "removable": active != context.DEFAULT_PROFILE,
                "rows": [
                    {"term": row.get("term", ""),
                     "heard": " | ".join(row.get("aliases", ())),
                     "sound": bool(row.get("sonido"))}
                    for row in self._vocab_rows_cache
                ],
            },
            "updates": {
                "button": self.update_button,
                "action": "apply_update" if self._ready_update else
                          "retry_update" if self._available_update and not self._update_download_pending
                          and not self._update_checking and not self._update_source_mode
                          else "check_updates",
                "busy": self._update_checking or self._update_download_pending
                        or self._update_install_pending,
                "progress": dict(self.update_progress),
            },
            "dirty": bool(self._dirty()),
            "settings_status": self.settings_status,
            "diagnostics": {
                "open": self._diagnostics_open,
                "running": self._diagnostic_running,
                "text": self._diagnostics_text,
            },
        }

    def push_state(self):
        self._warn_invalid_advanced()
        self.emit("state", self.state_payload())

    def _warn_invalid_advanced(self):
        """Si el config trae valores que el select no puede mostrar, el payload
        lleva el fallback y se avisa una vez en la UI (nunca silencio)."""
        style = self.cfg.get("overlay_style", _OVERLAY_STYLE_DEFAULT)
        if style not in _OVERLAY_STYLES:
            marker = ("overlay_style", str(style))
            if marker not in self._advanced_warned:
                self._advanced_warned.add(marker)
                self.toast("Indicador de voz no válido",
                           "Se muestra el espectro reactivo hasta que elijas "
                           "una opción válida.",
                           level="warn")
        threads = self.cfg.get("threads", _THREADS_DEFAULT)
        if not _threads_is_valid(threads):
            marker = ("threads", str(threads))
            if marker not in self._advanced_warned:
                self._advanced_warned.add(marker)
                self.toast("Hilos de CPU no válidos",
                           "Se usa un valor válido hasta que elijas entre "
                           "1, 2, 4, 6 u 8.",
                           level="warn")
        vad = self.cfg.get("vad_model", "silero")
        if vad not in ("silero", "ten"):
            marker = ("vad_model", str(vad))
            if marker not in self._advanced_warned:
                self._advanced_warned.add(marker)
                self.toast("Detector de voz no válido",
                           "Se usa la opción liviana hasta que elijas una opción válida.",
                           level="warn")

    def _checklist(self):
        """Checklist de Inicio con accesos al reconocimiento, micrófono y tecla."""
        key_ok = bool((self.key_value or "").strip())
        return [
            {"id": "models", "label": "Reconocimiento",
             "ok": bool(self.model_ready),
             "hint": ("listos" if self.model_ready
                      else "falta descargar ~670 MB"),
             "page": "home"},
            {"id": "mic", "label": "Micrófono",
             "ok": self._selected_device() is not None,
             "hint": (str(self.mic_selected)
                      if self._selected_device() is not None
                      else "elegí una entrada"),
             "page": "audio"},
            {"id": "key", "label": "Atajos de voz",
             "ok": key_ok,
             "hint": self.key_value,
             "page": "settings"},
        ]

    def _disabled_reason(self):
        """Motivo inline del botón deshabilitado (title + aria-describedby)."""
        if self._last_daemon_running:
            return ""
        if not self.model_ready:
            return "Completá la preparación del reconocimiento para habilitar el dictado."
        if self._selected_device() is None:
            if not self._microphones_loaded or self._microphones_refreshing:
                return "Buscando micrófonos…"
            if not self.catalog.devices:
                return "No se detectaron micrófonos: conectá una entrada."
            return "Elegí un micrófono disponible para iniciar Instant2."
        if (self._daemon_start_pending or self._daemon_stop_pending
                or self._daemon_check_pending
                or not self._daemon_state_ready):
            return "Comprobando el estado de Instant2…"
        return ""

    def toast(self, title, message="", level="info", actions=(), ms=4500):
        if self._closed:
            return
        if level == "error":
            # Todo error que ve el usuario tiene que quedar en el log: un
            # toast sin rastro no se puede diagnosticar despues. Los que
            # pasan por `_toast_error` dejan además el traceback.
            log.error("toast de error: %s: %s", title, message)
        toast_id = f"t{time.monotonic_ns()}"
        if actions:
            self._toast_callbacks[toast_id] = [
                callback for _label, callback in actions]
        self.emit("toast", {
            "id": toast_id,
            "title": title, "message": message, "level": level,
            "actions": [{"id": str(i), "label": label}
                        for i, (label, _callback) in enumerate(actions)],
            "ms": max(ms, 15000) if actions else ms,
        })

    # ------------------------------------------------------------- protocolo

    def handle(self, message):
        """Despacha un mensaje del bridge (`{"op": ...}`). Nunca propaga."""
        if not isinstance(message, dict):
            return
        op = message.get("op")
        handler = getattr(self, f"_op_{op}", None) if isinstance(op, str) else None
        try:
            if handler is not None:
                handler(message)
        except Exception:
            log.exception("op del panel falló: %s", op)

    def _op_ready(self, _message):
        self._toast_callbacks.clear()
        self.push_state()
        self._nudge_setup_if_needed()
        self._report_pending_update()

    def _op_navigate(self, message):
        self.navigate(message.get("page") or "home")
        self.push_state()

    def _op_toggle_daemon(self, _message):
        self.toggle_daemon()

    def _op_stop_daemon(self, _message):
        self.stop_daemon_flow()

    def _op_refresh_mics(self, _message):
        self.refresh_microphones()

    def _op_select_mic(self, message):
        self.select_mic(message.get("label"))

    def _op_test_mic(self, _message):
        self.test_microphone()

    def _op_key_capture_open(self, message):
        self.begin_key_capture(message.get("target"))

    def _op_key_captured(self, message):
        self.key_captured(message.get("code"), message.get("name"))

    def _op_key_cancel(self, _message):
        self._key_capturing = False

    def _op_set_autostart(self, message):
        self._autostart_enabled = bool(message.get("value"))
        self._update_settings_status()
        self.push_state()

    def _op_set_advanced(self, message):
        key = message.get("key")
        value = message.get("value")
        if key == "llm_url":
            value = str(value or "").strip()
            if value:
                try:
                    from instant2_app.llm import check_url
                    value = check_url(value)
                except ValueError as exc:
                    self.toast("Dirección no válida", str(exc), level="warn")
                    return
            self.cfg["llm_url"] = value
        elif key == "llm_model":
            value = str(value or "").strip()
            if len(value) > 200 or any(ord(char) < 32 for char in value):
                self.toast("Identificador no válido",
                           "Usá el identificador que muestra tu servicio.",
                           level="warn")
                return
            self.cfg["llm_model"] = value
        elif key == "llm_normal_enabled":
            self.cfg["llm_normal_enabled"] = (
                value is True or value in (1, "1", "true", "on", "yes", "si", "s"))
        elif key == "llm_managed_enabled":
            enabled = value is True or value in (1, "1", "true", "on", "yes", "si", "s")
            self.cfg["llm_managed_enabled"] = enabled
            if enabled and not str(self.cfg.get("llm_url", "")).strip():
                self.cfg["llm_url"] = "http://127.0.0.1:8080/v1"
        elif key in ("llm_server_path", "llm_model_path"):
            value = str(value or "").strip()
            if len(value) > 2048 or any(ord(char) < 32 for char in value):
                self.toast("Ruta no válida", "Revisá la ruta del servicio o del archivo de lenguaje.",
                           level="warn")
                return
            self.cfg[key] = value
            if value and not str(self.cfg.get("llm_url", "")).strip():
                self.cfg["llm_url"] = "http://127.0.0.1:8080/v1"
        elif key == "llm_acceleration":
            value = str(value or "").strip().lower()
            if value not in ("auto", "cpu"):
                self.toast("Aceleración no válida", "Elegí automática o CPU.",
                           level="warn")
                return
            self.cfg[key] = value
        elif key == "resource_mode":
            value = str(value or "").strip().lower()
            if value not in ("speed", "balanced", "memory"):
                self.toast("Perfil de recursos no válido",
                           "Elegí rapidez, equilibrado o menor uso de memoria.",
                           level="warn")
                return
            self.cfg[key] = value
        elif key == "threads":
            try:
                threads = int(value)
            except (TypeError, ValueError):
                self.toast("Cantidad de hilos no válida",
                           "Elegí entre 1, 2, 4, 6 u 8.", level="warn")
                return
            cpu_count = os.cpu_count() or 4
            clamped = max(1, min(8, cpu_count, threads))
            threads = min(_THREAD_OPTIONS,
                          key=lambda option: (abs(option - clamped), option))
            if threads != int(value) or clamped != int(value):
                self.toast("Hilos de CPU ajustados",
                           f"Se pidieron {value} hilos; se usan {threads} "
                           f"(límite: {min(8, cpu_count)} por CPU).",
                           level="warn")
            self.cfg["threads"] = threads
        elif key == "max_seg":
            try:
                max_seg = float(value)
            except (TypeError, ValueError):
                self.toast("Duración no válida", "Usá un valor entre 1 y 60 segundos.", level="warn")
                return
            if not 1 <= max_seg <= 60:
                self.toast("Duración no válida", "Usá un valor entre 1 y 60 segundos.", level="warn")
                return
            self.cfg["max_seg"] = max_seg
        elif key == "sound":
            self.cfg["sound"] = value is True or value in (1, "1", "true", "on")
        elif key == "overlay_style":
            if value not in _OVERLAY_STYLES:
                self.toast("Indicador de voz no válido",
                           "Elegí entre el espectro reactivo y las barras clásicas.",
                           level="warn")
                return
            self.cfg["overlay_style"] = value
        elif key == "vad_model":
            value = str(value or "").strip().lower()
            if value not in ("silero", "ten"):
                self.toast("Detector de voz no válido",
                           "Elegí la opción liviana o la de mayor precisión.",
                           level="warn")
                return
            self.cfg["vad_model"] = value
        elif key == "blank_penalty":
            try:
                penalty = float(value)
            except (TypeError, ValueError):
                self.toast("Penalidad no válida",
                           "Usá un valor entre 0 y 1 (0 = apagada).",
                           level="warn")
                return
            if not 0.0 <= penalty <= 1.0:
                self.toast("Penalidad no válida",
                           "Usá un valor entre 0 y 1 (0 = apagada).",
                           level="warn")
                return
            self.cfg["blank_penalty"] = penalty
        elif key == "update_mode":
            value = str(value or "").strip().lower()
            if value not in _UPDATE_MODES:
                self.toast("Modo de actualización no válido",
                           "Elegí avisarme antes, automático o apagado.",
                           level="warn")
                return
            self.cfg["update_mode"] = value
        elif key == "llm_token":
            # El token nunca vuelve al panel ni al log: solo se guarda.
            self.cfg["llm_token"] = str(value or "")
        else:
            return
        self._update_settings_status()
        self.push_state()

    def _op_context_select(self, message):
        self.context_select(message.get("name"))

    def _op_context_add(self, message):
        self.context_add(message.get("name"))

    def _op_context_remove(self, _message):
        self.context_remove()

    def _op_vocab_add(self, _message):
        self.vocab_add()

    def _op_vocab_set(self, message):
        self.vocab_set(message.get("row"), message.get("col"), message.get("value"))

    def _op_vocab_toggle_sound(self, message):
        self.vocab_toggle_sound(message.get("row"))

    def _op_vocab_delete(self, message):
        self.vocab_delete(message.get("row"))

    def _op_save_config(self, _message):
        self.save_config(show_message=True)

    def _op_llm_runtime_retry(self, _message):
        from instant2_app.llm_runtime_status import request_retry

        self.save_config(show_message=False)
        if self._last_daemon_running and self._restart_needed:
            self.toast("Reinicio necesario",
                       "Reiniciá el proceso de dictado para aplicar los ajustes del LLM.",
                       level="warn")
            return
        if not self._last_daemon_running:
            self.request_daemon_start()
            return
        try:
            request_retry()
        except OSError:
            self.toast("No se pudo reintentar",
                       "No pude enviar la solicitud al proceso de dictado.",
                       level="warn")
            return
        self._llm_runtime_state = {
            "status": "starting", "detail": "Reintentando el servicio local…",
            "device": "", "owned": False,
        }
        self._llm_runtime_next_check = time.monotonic() + 6.0
        self.push_state()

    def sync_llm_runtime(self, manual=False):
        """Read daemon-published state; the panel never starts or stops models."""
        if self._closed:
            return
        from instant2_app.llm_runtime import is_local_endpoint

        url = str(self.cfg.get("llm_url") or "").strip()
        if self.cfg.get("llm_normal_enabled", True) is False:
            state = {"status": "disabled",
                     "detail": "La revisión del dictado con LLM está apagada.",
                     "device": "", "owned": False}
        elif not url:
            state = {"status": "needs_setup",
                     "detail": "Sin servicio configurado; el dictado local sigue disponible.",
                     "device": "", "owned": False}
        elif not is_local_endpoint(url):
            state = {"status": "configured",
                     "detail": "Servicio remoto configurado; el texto dictado sale del equipo.",
                     "device": "", "owned": False}
        elif (self.cfg.get("llm_managed_enabled", True) is False
              or not self.cfg.get("llm_server_path")
              or not self.cfg.get("llm_model_path")):
            state = {"status": "configured",
                     "detail": "Servicio local configurado; Instant2 no administra su proceso.",
                     "device": "", "owned": False}
        elif not self._last_daemon_running:
            state = {
                "status": "starting" if self._daemon_start_pending else "stopped",
                "detail": ("Iniciando el proceso de dictado…" if self._daemon_start_pending
                           else "Iniciá el dictado para que su proceso persistente prepare el LLM."),
                "device": "", "owned": False,
            }
        else:
            try:
                from instant2_app.llm_runtime_status import read_status
                state = read_status(max_age_seconds=120.0) or {
                    "status": "starting",
                    "detail": "Esperando el estado del servicio administrado por el dictado.",
                    "device": "", "owned": False,
                }
            except Exception:
                state = {"status": "error",
                         "detail": "No se pudo leer el estado del servicio local.",
                         "device": "", "owned": False}
        changed = state != self._llm_runtime_state
        self._llm_runtime_state = dict(state)
        if changed or manual:
            self.push_state()

    def refresh_llm_runtime(self):
        """Refresh the text-free handoff without probing or owning the server."""
        if self._closed or time.monotonic() < self._llm_runtime_next_check:
            return
        self._llm_runtime_next_check = time.monotonic() + 2.5
        self.sync_llm_runtime()

    def _op_download_models(self, _message):
        self.download_models()

    def _op_cancel_model_download(self, _message):
        self.cancel_model_download()

    def _op_check_updates(self, _message):
        self.check_updates()

    def _op_retry_update(self, message):
        if _updates_disabled(self.cfg):
            self.toast("Actualizaciones",
                       "Las actualizaciones están apagadas en Ajustes.",
                       level="warn")
            return
        if (self._available_update and not self._update_download_pending
                and not self._update_source_mode):
            self._start_update_download(self._available_update)

    def _op_download_update(self, _message):
        # Consentimiento explícito del modo notify: el toast "Hay versión
        # nueva" pide clic ANTES de descargar.
        self._op_retry_update(_message)

    def _op_apply_update(self, _message):
        if self._ready_update and not self._update_install_pending:
            target, expected, _version = self._ready_update
            self._install_ready(target, expected)

    def _op_toast_action(self, message):
        callbacks = self._toast_callbacks.pop(message.get("id"), None)
        if not callbacks:
            return
        try:
            callbacks[int(message.get("action") or 0)]()
        except (IndexError, TypeError, ValueError):
            log.warning("acción de toast inválida: %s", message)

    def _op_toast_dismiss(self, message):
        """La página avisa al cerrar/expirar un toast: se sueltan sus
        acciones para no fugar callbacks ni dejar acciones muertas."""
        self._toast_callbacks.pop(message.get("id"), None)

    def _op_diagnostics(self, _message):
        self.show_diagnostics()

    def _op_diagnostics_close(self, _message):
        self._diagnostics_open = False
        self.push_state()

    # -------------------------------------------------------------- settings

    def _init_settings(self, autostart_override):
        configured = context.profiles(self.cfg)
        try:
            active = context.active_name(self.cfg)
        except Exception:
            active = context.DEFAULT_PROFILE
        if active not in configured:
            active = context.DEFAULT_PROFILE
        self._loaded_context_name = active
        self.cfg["active_context"] = active
        self._vocab_load_rows(configured.get(active, []))
        try:
            enabled = bool(autostart.is_enabled())
        except Exception:
            enabled = bool(self.cfg.get("autostart"))
        if autostart_override is not None:
            enabled = autostart_override
        self._autostart_enabled = enabled
        self._saved_settings = self._snapshot()
        self._restart_settings = self._restart_signature(self._saved_settings)
        self._settings_daemon_state = None
        self._update_settings_status()

    def _dirty(self):
        if self._saved_settings is None:
            return False
        return not self._same_settings(self._snapshot(), self._saved_settings)

    def _snapshot(self):
        device = self._selected_device()
        if device:
            self.catalog.remember(device)
        mic = self.catalog.pending_identity or DeviceCatalog.identity(
            (self.cfg.get("mic_index"), self.cfg.get("mic_hint", "")))
        self._store_vocab_table()
        context_state = tuple(
            (name, tuple((row["term"], tuple(row["aliases"]), bool(row.get("sonido")))
                         for row in items))
            for name, items in context.profiles(self.cfg).items())
        return (mic, self.key_value.strip().lower(), bool(self._autostart_enabled),
                self.cfg["active_context"], context_state,
                self.cfg.get("llm_url", ""),
                self.cfg.get("threads", 4), bool(self.cfg.get("sound", False)),
                self.cfg.get("overlay_style", "orbital"),
                self.cfg.get("max_seg", 20.0),
                self.cfg.get("vad_model", "silero"),
                self.cfg.get("blank_penalty", 0.0),
                _update_mode(self.cfg),
                self.cfg.get("llm_token", ""),
                self.cfg.get("llm_model", ""),
                bool(self.cfg.get("llm_normal_enabled", True)),
                True,
                self.cfg.get("llm_server_path", ""),
                self.cfg.get("llm_model_path", ""),
                self.cfg.get("llm_acceleration", "auto"),
                self.cfg.get("resource_mode", "balanced"),
                str(self.cfg.get("read_key", "f10")).strip().lower())

    @staticmethod
    def _restart_signature(snapshot):
        # El token y la preferencia de actualizacion no exigen reinicio.
        return (snapshot[0], snapshot[1], snapshot[3], snapshot[4], snapshot[5],
                snapshot[6], snapshot[7], snapshot[8], snapshot[9],
                snapshot[10], snapshot[11], snapshot[14], snapshot[15],
                snapshot[16], snapshot[17], snapshot[18], snapshot[19],
                snapshot[20], snapshot[21])

    def _same_settings(self, left, right):
        return (self.catalog.same_identity(left[0], right[0])
                and left[1:] == right[1:])

    def _update_settings_status(self):
        if self._saved_settings is None:
            self.settings_status = ""
        elif self._dirty():
            self.settings_status = "Hay cambios sin guardar."
        elif self._restart_needed:
            self.settings_status = ("Guardado; reiniciá Instant2 para aplicar "
                                    "micrófono, atajos, LLM y rendimiento.")
        else:
            self.settings_status = "Ajustes guardados."

    def _settings_daemon_changed(self, running):
        previous = self._settings_daemon_state
        self._settings_daemon_state = bool(running)
        if previous is None:
            if not running:
                self._restart_needed = False
        elif previous != bool(running):
            self._restart_needed = False
            if running:
                self._restart_settings = self._restart_signature(self._saved_settings)
        self._update_settings_status()

    # ------------------------------------------------------------ micrófonos

    def refresh_microphones(self, initial=False):
        if self._microphones_refreshing:
            return
        self._microphones_loaded = False
        self._microphones_refreshing = True
        self.push_state()
        preferred = None if initial else self.catalog.pending_device
        refresh = self.catalog.refresh

        def enumerate_devices(_emit):
            return refresh(preferred, preserve=not initial)

        def populated(result):
            if self._closed:
                return
            self._microphones_refreshing = False
            labels, selected, missing = result
            self.mic_labels = list(labels)
            if selected is not None:
                self.mic_selected = selected
            elif missing:
                self.mic_labels.append(MIC_UNAVAILABLE_LABEL)
                self.mic_selected = self.mic_labels[-1]
            elif labels:
                self.mic_selected = labels[0]
            else:
                self.mic_labels = ["No se detectaron micrófonos"]
                self.mic_selected = None
            device = self._selected_device()
            if device:
                self.catalog.remember(device)
            if initial and self._saved_settings is not None:
                snapshot = self._snapshot()
                self._saved_settings = (snapshot[0], self._saved_settings[1],
                                        *self._saved_settings[2:])
                self._restart_settings = (snapshot[0], *self._restart_settings[1:])
            if not labels:
                self.meter = {"percent": 0, "text": "No se detectaron micrófonos. "
                              "Conectá una entrada y actualizá la lista."}
            elif not device and missing:
                self.meter = {"percent": 0, "text": "El micrófono elegido no está "
                              "disponible; seleccioná otro."}
            elif not initial:
                self.meter = {"percent": 0, "text": "Lista de micrófonos actualizada."}
            self._microphones_loaded = True
            self._update_settings_status()
            self.push_state()
            self.emit("mic_loaded", {"labels": self.mic_labels,
                                     "selected": self.mic_selected})
            self._maybe_start_daemon_on_open()

        def failed(error):
            if self._closed:
                return
            self._microphones_refreshing = False
            cause, action = _friendly_error(error)
            self.meter = {"percent": 0,
                          "text": f"No se pudo actualizar la lista: {cause} {action}"}
            self._microphones_loaded = True
            self._update_settings_status()
            self.push_state()
            self.emit("mic_loaded", {"labels": self.mic_labels,
                                     "selected": self.mic_selected})
            self._maybe_start_daemon_on_open()

        self.tasks.submit(enumerate_devices, populated, failed)

    def _selected_device(self):
        label = self.mic_selected
        if not label:
            return None
        device = self.catalog.devices.get(label)
        return tuple(device) if device is not None else None

    def select_mic(self, label):
        if label is None:
            return
        self.mic_selected = label
        device = self._selected_device()
        if device:
            self.catalog.remember(device)
            self.meter = {"percent": 0, "text": "Entrada seleccionada."}
        self._update_settings_status()
        self.push_state()

    def test_microphone(self):
        if self._microphone_test_pending:
            return
        selected = self._selected_device()
        if not selected:
            self.toast("Sin micrófono", "Elegí un micrófono de entrada.",
                       level="warn")
            return
        self._microphone_test_pending = True
        self.meter = {"percent": 0, "text": "Hablá ahora…"}
        self.push_state()

        def capture(emit):
            def level(value):
                emit(("level", value))
            return audio.peak_meter(selected[0], seconds=3.0, on_level=level)

        def progress(event):
            if event and event[0] == "level":
                self.meter = {"percent": min(100, int(event[1] * 200)),
                              "text": f"Señal {event[1]:.3f}"}
                self.emit("meter", dict(self.meter))

        def done(peak):
            if self._closed:
                return
            self._microphone_test_pending = False
            if peak > .005:
                self.meter = {"percent": self.meter["percent"],
                              "text": f"Señal detectada ({peak:.3f})."}
            else:
                self.meter = {"percent": 0,
                              "text": "No detecté señal; revisá micrófono/volumen."}
            self.push_state()

        def failed(error):
            if self._closed:
                return
            self._microphone_test_pending = False
            self.meter = {"percent": 0, "text": "No se pudo probar el micrófono."}
            self.push_state()
            _toast_warn(self, "Prueba de micrófono", error)

        self.tasks.submit(capture, done, failed, progress)

    # ------------------------------------------------------------------ tecla

    def begin_key_capture(self, target="dictation"):
        self._key_capture_target = "read" if target == "read" else "dictation"
        self._key_capturing = True
        self.emit("key_capture", {"state": "open", "target": self._key_capture_target})

    def key_captured(self, code, name):
        name = (name or "").strip().lower().replace(" ", "_")
        if os.name == "nt":
            try:
                vk = int(code)
            except (TypeError, ValueError):
                vk = 0
            if 8 <= vk <= 0xFE:
                value = f"vk:{vk}"
            elif hotkey.is_valid_key(name):
                value = hotkey.normalize_key(name)
            else:
                self.emit("key_result", {
                    "ok": False,
                    "message": "No pude identificar esa tecla; prob\u00e1 otra.",
                })
                return
        elif hotkey.is_valid_key(name):
            value = hotkey.normalize_key(name)
        else:
            self.emit("key_result", {
                "ok": False,
                "message": "No pude identificar esa tecla; prob\u00e1 otra.",
            })
            return
        target = self._key_capture_target
        other_key = (self.cfg.get("key", "f9") if target == "read"
                     else self.cfg.get("read_key", "f10"))
        if hotkey.same_binding(value, other_key):
            self.emit("key_result", {
                "ok": False,
                "message": "Esa tecla ya está asignada al otro atajo. Elegí una distinta.",
            })
            return
        self._key_capturing = False
        if target == "read":
            self._set_read_key(value)
        else:
            self._set_key(value)
        self.emit("key_result", {"ok": True, "message": ""})

    def _set_key(self, key):
        self.cfg["key"] = key
        self.key_value = hotkey.key_label(key)
        self._update_settings_status()
        self.push_state()

    def _set_read_key(self, key):
        self.cfg["read_key"] = key
        self.read_key_value = hotkey.key_label(key)
        self._update_settings_status()
        self.push_state()

    # ------------------------------------------------------------ vocabulario

    def _vocab_load_rows(self, rows):
        """Vuelca filas normalizadas al cache sin marcar cambios."""
        self._vocab_rows_cache = [
            {"term": row.get("term", ""),
             "aliases": list(row.get("aliases", ())),
             "sonido": bool(row.get("sonido"))}
            for row in rows]

    def _vocab_rows(self):
        """Normaliza el cache con el mismo formato del editor."""
        draft = []
        for row in self._vocab_rows_cache:
            term = str(row.get("term", "")).strip()
            if not term:
                continue
            aliases = row.get("aliases", ())
            if isinstance(aliases, str):
                aliases = [part.strip() for part in aliases.split("|") if part.strip()]
            draft.append({"term": term, "aliases": list(aliases),
                          "sonido": bool(row.get("sonido"))})
        return context.parse_editor(context.editor_text(draft))

    def _store_vocab_table(self):
        name = self._loaded_context_name or context.DEFAULT_PROFILE
        configured = context.profiles(self.cfg)
        configured[name] = self._vocab_rows()
        self.cfg["context_profiles"] = configured
        self.cfg["active_context"] = self._loaded_context_name or name

    def context_select(self, name):
        if not name:
            return
        self._store_vocab_table()
        configured = context.profiles(self.cfg)
        if name not in configured:
            return
        self.cfg["active_context"] = name
        self._loaded_context_name = name
        self._vocab_load_rows(configured.get(name, []))
        self._update_settings_status()
        self.push_state()

    def context_add(self, name):
        name = (name or "").strip()
        if not name:
            return
        configured = context.profiles(self.cfg)
        if name in configured:
            self.toast("Instant2", "Ya existe ese perfil.", level="warn")
            return
        self._store_vocab_table()
        configured[name] = []
        self.cfg["context_profiles"] = configured
        self.cfg["active_context"] = name
        self._loaded_context_name = name
        self._vocab_load_rows([])
        self._update_settings_status()
        self.push_state()

    def context_remove(self):
        name = self._loaded_context_name or context.DEFAULT_PROFILE
        if name == context.DEFAULT_PROFILE:
            return
        self._store_vocab_table()
        configured = context.profiles(self.cfg)
        configured.pop(name, None)
        configured.setdefault(context.DEFAULT_PROFILE, [])
        self.cfg["context_profiles"] = configured
        self.cfg["active_context"] = context.DEFAULT_PROFILE
        self._loaded_context_name = context.DEFAULT_PROFILE
        self._vocab_load_rows(configured[context.DEFAULT_PROFILE])
        self._update_settings_status()
        self.push_state()

    def vocab_add(self):
        self._vocab_rows_cache.append({"term": "", "aliases": [], "sonido": False})
        self._update_settings_status()
        self.push_state()

    def vocab_set(self, row, col, value):
        try:
            index = int(row)
        except (TypeError, ValueError):
            return
        if not (0 <= index < len(self._vocab_rows_cache)):
            return
        entry = self._vocab_rows_cache[index]
        if col == "term":
            entry["term"] = str(value or "")
        elif col == "heard":
            entry["aliases"] = [part.strip() for part in str(value or "").split("|")
                                if part.strip()]
        else:
            return
        self._update_settings_status()
        self.push_state()

    def vocab_toggle_sound(self, row):
        try:
            index = int(row)
        except (TypeError, ValueError):
            return
        if not (0 <= index < len(self._vocab_rows_cache)):
            return
        entry = self._vocab_rows_cache[index]
        entry["sonido"] = not entry.get("sonido", False)
        self._update_settings_status()
        self.push_state()

    def vocab_delete(self, row):
        try:
            index = int(row)
        except (TypeError, ValueError):
            return
        if not (0 <= index < len(self._vocab_rows_cache)):
            return
        del self._vocab_rows_cache[index]
        self._update_settings_status()
        self.push_state()

    # --------------------------------------------------------------- guardar

    def save_config(self, show_message=True):
        selected = self._selected_device()
        if selected:
            self.catalog.remember(selected)
            self.cfg["mic_index"], self.cfg["mic_hint"] = selected
        snapshot = self._snapshot()
        self.cfg["key"], self.cfg["lang"], self.cfg["autostart"] = (
            snapshot[1], "es", snapshot[2])
        self.cfg["read_key"] = snapshot[21]
        self.cfg["llm_url"] = snapshot[5]
        self.cfg["threads"], self.cfg["sound"] = snapshot[6], snapshot[7]
        self.cfg["overlay_style"], self.cfg["max_seg"] = snapshot[8], snapshot[9]
        self.cfg["vad_model"], self.cfg["blank_penalty"] = snapshot[10], snapshot[11]
        self.cfg["update_mode"] = snapshot[12]
        self.cfg["llm_model"] = snapshot[14]
        self.cfg["llm_normal_enabled"] = snapshot[15]
        self.cfg["llm_managed_enabled"] = True
        self.cfg["llm_server_path"] = snapshot[17]
        self.cfg["llm_model_path"] = snapshot[18]
        self.cfg["llm_acceleration"] = snapshot[19]
        self.cfg["resource_mode"] = snapshot[20]
        config.save(self.cfg)
        self._schedule_tts_idle_unload()
        self.sync_llm_runtime()
        try:
            desired = snapshot[2]
            if autostart.is_enabled() != desired:
                (autostart.enable if desired else autostart.disable)()
        except Exception as exc:  # noqa: BLE001  (se informa, no se mata)
            _toast_warn(self, "Arranque con el sistema", exc)
        saved = (DeviceCatalog.identity(
            (self.cfg.get("mic_index"), self.cfg.get("mic_hint", ""))), *snapshot[1:])
        running = bool(self._last_daemon_running)
        signature = self._restart_signature(saved)
        if running:
            self._restart_needed = not (
                self.catalog.same_identity(signature[0], self._restart_settings[0])
                and signature[1:] == self._restart_settings[1:])
        else:
            self._restart_settings = signature
            self._restart_needed = False
        self._saved_settings = saved
        self._update_settings_status()
        if show_message:
            note = (" Micrófono, atajos, LLM y rendimiento guardados; reiniciá "
                    "Instant2 para aplicarlos." if self._restart_needed else
                    "Se guardaron tus preferencias en este equipo.")
            self.toast("Ajustes guardados", note)
        self.push_state()
        return True

    # ---------------------------------------------------------------- daemon

    def toggle_daemon(self):
        if self._daemon_start_pending or self._daemon_stop_pending:
            return
        if self._last_daemon_running:
            self.save_config(False)
            self.stop_daemon_flow(restart=True)
        else:
            self._start_daemon()

    def _maybe_start_daemon_on_open(self):
        if (not self._start_daemon_on_open or self._closed
                or not self._daemon_state_ready or self._daemon_check_pending
                or not self._microphones_loaded):
            return
        self._start_daemon_on_open = False
        if not self._last_daemon_running:
            self._start_daemon(save_settings=False)

    def _start_daemon(self, save_settings=True):
        if (self._daemon_start_pending or not self._daemon_state_ready
                or self._daemon_check_pending or not self._microphones_loaded
                or self._microphones_refreshing or self._daemon_stop_pending
                or self._last_daemon_running):
            return False
        if not self.model_ready:
            self.toast("Voz pendiente",
                       "Prepará los componentes de voz antes de iniciar Instant2.",
                       level="warn")
            return False
        if save_settings:
            self.save_config(False)
        try:
            env = app_environment()
            env["INSTANT2_DATA"] = self.data_dir
            self._daemon_start_pending = True
            self._daemon_start_token += 1
            token = self._daemon_start_token
            self.push_state()
            subprocess.Popen(app_command(("run",)), cwd=_workdir(),
                             env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.status_detail = "Iniciando Instant2…"
            self.push_state()
            self.schedule(10000, lambda: self._daemon_start_timeout(token))
            self.schedule(700, self.refresh_daemon)
            return True
        except Exception as exc:  # noqa: BLE001  (se informa al usuario)
            self._daemon_start_pending = False
            self.push_state()
            _toast_error(self, "No se pudo iniciar Instant2", exc)
            return False

    def _daemon_start_timeout(self, token):
        if self._closed:
            return
        if token != self._daemon_start_token or not self._daemon_start_pending:
            return
        self._daemon_start_pending = False
        if not self._last_daemon_running:
            self.status_detail = DAEMON_MISSING_DETAIL
            log.warning("el daemon no confirmó inicio dentro del plazo")
        self.push_state()

    def stop_daemon_flow(self, restart=False):
        if self._daemon_stop_pending:
            return
        pid = _pid_value()
        if pid is None or not self._last_daemon_running:
            if restart:
                self._start_daemon()
            else:
                self.refresh_daemon()
            return
        # _pid_is_instant hace powershell/Get-CimInstance (hasta 10s):
        # NUNCA en hilo UI. Se verifica en worker y recién ahí se mata.
        self._daemon_stop_pending = True
        self.status_detail = "Verificando Instant2…"
        self.push_state()

        def check(_emit):
            return _pid_is_instant(pid)

        def checked(is_ours):
            if self._closed:
                return
            if not is_ours:
                log.warning("PID %d reciclado por el SO (no es Instant2); no se mata.", pid)
                self._daemon_stop_pending = False
                self.status_detail = "El daemon ya no estaba; actualizando estado…"
                self.refresh_daemon()
                return
            try:
                if os.name == "nt":
                    subprocess.Popen(["taskkill", "/F", "/PID", str(pid)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    os.kill(pid, signal.SIGTERM)
            except OSError as exc:
                self._daemon_stop_pending = False
                self.status_detail = "No se pudo detener Instant2."
                self.push_state()
                _toast_error(self, "No se pudo detener Instant2", exc)
                return
            self.status_detail = "Cerrando Instant2…"
            self.push_state()
            if self._closed:
                return
            if restart:
                self.schedule(350, self._wait_before_restart)
            else:
                self.schedule(700, self._settle_stop_attempt)

        def failed(error):
            if self._closed:
                return
            self._daemon_stop_pending = False
            self.status_detail = "No se pudo comprobar el estado de Instant2."
            self.push_state()
            _toast_error(self, "No se pudo detener Instant2", error)

        self.tasks.submit(check, checked, failed)

    def _settle_stop_attempt(self):
        if self._closed:
            return
        self._daemon_stop_pending = False
        self.refresh_daemon()

    def _wait_before_restart(self):
        if self._closed:
            return
        if self._last_daemon_running:
            self.refresh_daemon()
            self.schedule(250, self._wait_before_restart)
        else:
            self._start_daemon()

    def refresh_daemon(self):
        if self._daemon_check_pending:
            return
        self._daemon_check_pending = True

        def result(running):
            if self._closed:
                return
            self._daemon_check_pending = False
            self._daemon_state_ready = True
            if not running:
                self._daemon_stop_pending = False
            if running and self._daemon_start_pending:
                self._daemon_start_pending = False
                self._daemon_start_token += 1
            if running != self._last_daemon_running:
                self._last_daemon_running = running
                self._settings_daemon_changed(running)
            if running:
                self.status_var = "Dictado activo"
                self.status_detail = STATUS_DETAIL.format(key=self.key_value)
            elif self._daemon_start_pending or self.status_detail == DAEMON_MISSING_DETAIL:
                self.status_var = "Listo para dictar"
            else:
                self.status_var = "Listo para dictar"
                self.status_detail = STATUS_DETAIL.format(key=self.key_value)
            self.push_state()
            self._maybe_start_daemon_on_open()

        def failed(error):
            if self._closed:
                return
            self._daemon_check_pending = False
            cause, action = _friendly_error(error)
            raw = str(error) if error is not None else ""
            detail = (f"{type(error).__name__}: {raw}" if raw
                      else type(error).__name__)
            log.warning("no se pudo comprobar el daemon desde el panel: %s",
                        detail, exc_info=True)
            if self._daemon_stop_pending:
                self._daemon_stop_pending = False
            self.status_var = "Listo para dictar"
            self.status_detail = (f"No se pudo comprobar si Instant2 está activo: "
                                  f"{cause} {action} (Detalle: {detail})")
            self.push_state()

        self.tasks.submit(lambda _emit: daemon_is_running(), result, failed)

    # ---------------------------------------------------------------- modelos

    def _update_model_status(self):
        self.model_ready = all(models.check(self.data_dir).values())
        if self.model_ready:
            self.model_status = "Voz lista en este equipo."
            self.model_progress = {"visible": False, "percent": 100}
        else:
            self.model_status = "Falta preparar el reconocimiento."
            self.model_progress = {"visible": False, "percent": 0}

    def download_models(self):
        if self.model_ready or self._model_download_pending:
            return
        cancel_event = threading.Event()
        self._model_cancel_event = cancel_event
        self._model_download_pending = True
        self._model_canceling = False
        self.model_status = "Revisando espacio en disco…"
        self.model_progress = {"visible": True, "percent": 0}
        self.push_state()

        def download(emit):
            return models.download_models(
                self.data_dir, progress=lambda *args: emit(args),
                cancel_event=cancel_event,
                preflight=lambda info: emit(("preflight", info)))

        def progress(event):
            if not event:
                return
            if event[0] == "preflight":
                info = event[1]
                if info["free_bytes"] is None:
                    self.model_status = (
                        "No pude consultar el espacio libre; voy a intentar "
                        "la descarga y verificaré antes de escribir.")
                else:
                    self.model_status = (
                        f"Espacio libre: {info['free_bytes'] / 1e9:.1f} GB. "
                        f"Se requieren {info['required_bytes'] / 1e9:.1f} GB "
                        "incluido el margen de seguridad.")
                self.emit("progress", {"percent": 0, "text": self.model_status})
                return
            step, done, total = event
            if not total:
                return
            label = "el reconocimiento" if step == "parakeet" else "la detección de voz"
            self.model_status = (f"Descargando {label}: {done / total:.0%} "
                                 f"({done / 1e6:.0f}/{total / 1e6:.0f} MB)")
            self.model_progress = {"visible": True,
                                   "percent": int(100 * done / total)}
            self.emit("progress", {"percent": self.model_progress["percent"],
                                   "text": self.model_status})

        def done(_value):
            self._model_download_pending = False
            self._model_canceling = False
            self._model_cancel_event = None
            if self._closed:
                return
            self._update_model_status()
            self.push_state()

        def failed(error):
            self._model_download_pending = False
            self._model_canceling = False
            self._model_cancel_event = None
            if self._closed:
                return
            self.model_progress = {"visible": False, "percent": 0}
            if isinstance(error, models.DownloadCancelled):
                self.model_status = (
                    "Descarga cancelada. El progreso parcial se conserva; "
                    "puedes reanudarla al reintentar.")
                self.push_state()
                return
            self.model_status = "No se pudieron preparar los componentes de voz."
            self.push_state()
            _toast_error(self, "Descarga fallida", error)

        self.tasks.submit(download, done, failed, progress)

    def cancel_model_download(self):
        if not self._model_download_pending or self._model_cancel_event is None:
            return
        self._model_canceling = True
        self.model_status = "Cancelando…"
        self._model_cancel_event.set()
        self.push_state()

    # ----------------------------------------------------------- diagnóstico

    def show_diagnostics(self):
        already_open = self._diagnostics_open
        self._diagnostics_open = True
        self._diagnostics_text = ("Ejecutando comprobaciones…"
                                  if not self._diagnostic_running else self._diagnostics_text)
        self.push_state()
        # El diagnóstico corre una sola vez: si el usuario cerró el modal, solo
        # se reabre cuando el pedido viene con la ventana abierta.
        if not already_open:
            self.emit("diagnostics", {"state": "running",
                                      "text": self._diagnostics_text, "open": True})
        if self._diagnostic_running:
            return
        self._diagnostic_running = True

        def diagnose(_emit):
            from contextlib import redirect_stdout
            from io import StringIO

            from instant2_app.daemon import cmd_check
            stream = StringIO()
            with redirect_stdout(stream):
                rc = cmd_check(config.load())
            return rc, stream.getvalue()

        def done(result):
            if self._closed:
                return
            self._diagnostic_running = False
            self._diagnostics_text = result[1] or "Sin salida de diagnóstico."
            self.push_state()
            # Si el usuario ya cerró el modal, el texto queda para la próxima
            # apertura: no se reabre solo.
            if self._diagnostics_open:
                self.emit("diagnostics", {"state": "done", "text": self._diagnostics_text,
                                          "open": True})

        def failed(error):
            if self._closed:
                return
            self._diagnostic_running = False
            cause, action = _friendly_error(error)
            raw = str(error) if error is not None else ""
            detail = _public_error_detail(type(error).__name__, raw)
            log.warning("diagnóstico fallido: %s", detail, exc_info=True)
            self._diagnostics_text = f"Diagnóstico fallido: {cause} {action} (Detalle: {detail})"
            self.push_state()
            if self._diagnostics_open:
                self.emit("diagnostics", {"state": "done", "text": self._diagnostics_text,
                                          "open": True})

        self.tasks.submit(diagnose, done, failed)

    # ---------------------------------------------------------- actualizar

    def _silent_update_check(self):
        """Chequeo diario según consentimiento: off=ni chequeo, notify=solo
        avisa (pide clic antes de descargar), auto=descarga como antes."""
        if self._update_silent_done or self._closed:
            return
        self._update_silent_done = True
        if _updates_disabled(self.cfg):
            return
        try:
            if time.time() - float(self.cfg.get("update_last_check", 0)) < UPDATE_CHECK_INTERVAL:
                return
        except (TypeError, ValueError):
            pass
        from instant2_app import update as update_module
        self.tasks.submit(lambda _emit: update_module.check(),
                          self._silent_update_result, lambda _error: None)

    def _silent_update_result(self, info):
        try:
            self.cfg["update_last_check"] = int(time.time())
            config.save(self.cfg)
        except Exception:
            log.warning("no pude sellar el chequeo de versión", exc_info=True)
        if self._closed or not info.get("update"):
            return
        self._apply_update_available(info)

    def _apply_update_available(self, info):
        self._available_update = info
        version = info.get("latest") or "nueva"
        if self._update_source_mode:
            self._ready_update = None
            self.update_button = f"Disponible v{version}"
            self.update_progress = {
                "visible": False, "percent": 0,
                "text": "Esta copia desde el repo se actualiza con git pull.",
            }
            self.push_state()
            return False
        if self._ready_update and self._pending_update:
            if self._pending_update.get("latest") == version:
                self.update_button = f"Reiniciar para aplicar v{version}"
                self.push_state()
                return
            self._ready_update = None
        if self._update_download_pending:
            return
        if _update_mode(self.cfg) == "notify" and not self._update_checking:
            # Consentimiento: se avisa y se pide clic ANTES de descargar.
            from instant2_app import update as update_module
            notes = update_module.clean_notes(info.get("notes"))
            details = ("Está disponible pero todavía no se descargó nada. "
                       "Tocá Descargar para traerla.")
            if notes:
                details += chr(10) + notes
            self.update_button = f"Descargar v{version}"
            self.push_state()
            self.toast(f"Hay versión nueva: {version}", details,
                       actions=(("Descargar",
                                 lambda: self._start_update_download(info)),
                                ("Después", lambda: None)))
            return False
        self._start_update_download(info)
        return True

    def _start_update_download(self, info):
        if self._closed or self._update_download_pending:
            return
        self._pending_update = info
        self._update_download_pending = True
        version = info.get("latest") or "nueva"
        self.update_button = f"Descargando v{version}…"
        self.update_progress = {
            "visible": True, "percent": 0,
            "text": "Conectando con el servidor de actualizaciones…",
        }
        self.push_state()
        self.tasks.submit(self._download_update, self._update_ready,
                          self._update_download_failed, self._update_download_progress)

    def check_updates(self):
        if self._update_checking or self._update_download_pending:
            return
        if _updates_disabled(self.cfg):
            self.toast("Actualizaciones",
                       "Las actualizaciones están apagadas en Ajustes. "
                       "Elegí “avisarme” o “automático” para volver a buscar.",
                       level="warn")
            return
        from instant2_app import update as update_module
        self._update_checking = True
        self.update_button = "Buscando actualizaciones…"
        self.push_state()
        self.tasks.submit(lambda _emit: update_module.check(),
                          self._updates_result, self._updates_failed)

    def _updates_failed(self, error):
        if self._closed:
            return
        self._update_checking = False
        if not self._available_update:
            self.update_button = "Buscar actualizaciones"
        self.push_state()
        _toast_warn(self, "Actualizaciones", error)

    @staticmethod
    def _update_progress_text(done, total):
        done_mb = done / (1024 * 1024)
        if total:
            total_mb = total / (1024 * 1024)
            return (f"Descargando: {done_mb:.0f} de {total_mb:.0f} MB "
                    f"({done / total:.0%})")
        return f"Descargando: {done_mb:.0f} MB"

    def _update_download_progress(self, event):
        if self._closed or not self._update_download_pending:
            return
        done = max(0, int(event.get("done") or 0))
        total = max(0, int(event.get("total") or 0))
        percent = min(100, int(100 * done / total)) if total else 0
        self.update_progress = {
            "visible": True,
            "percent": percent,
            "indeterminate": not bool(total),
            "text": self._update_progress_text(done, total),
        }
        self.push_state()

    def _updates_result(self, info):
        if self._closed:
            return
        self._update_checking = False
        if not info.get("update"):
            self._available_update = None
            self._pending_update = None
            self._ready_update = None
            self.update_button = "Buscar actualizaciones"
            self.update_progress = {"visible": False, "percent": 0, "text": ""}
            self.push_state()
            self.toast("Actualizaciones",
                       f"Estás al día (versión {info.get('current') or '?'}).")
            return
        from instant2_app import update as update_module
        notes = update_module.clean_notes(info.get("notes"))
        was_pending = self._update_download_pending
        was_ready = bool(self._ready_update)
        self._available_update = info
        if was_ready:
            self._update_ready(self._ready_update)
            return
        started = self._apply_update_available(info)
        if not was_pending and started is not False:
            if self._update_source_mode:
                details = "Esta copia del repo se actualiza con git pull."
            elif started:
                details = (f"Tenés {info.get('current') or '?'}; se está "
                           "descargando en segundo plano.")
            else:
                details = "La descarga de esta versión ya está en curso."
            if notes:
                details += chr(10) + notes
            self.toast(f"Hay versión nueva: {info['latest']}", details)

    def _download_pending(self):
        if self._available_update:
            self._start_update_download(self._available_update)

    def _download_update(self, emit):
        import tempfile
        from instant2_app import update as update_module
        info = self._pending_update
        os.makedirs(os.path.join(tempfile.gettempdir(), "instant2_update"),
                    exist_ok=True)
        download_dir = os.path.join(tempfile.gettempdir(), "instant2_update")
        last_report = [0.0]

        def report(done, total):
            now = time.monotonic()
            if total and done < total and now - last_report[0] < 0.2:
                return
            last_report[0] = now
            emit({"done": done, "total": total})

        target, expected = update_module.download_release_package(
            info, download_dir, progress=report)
        return target, expected

    def _update_download_failed(self, error):
        if self._closed:
            return
        self._update_download_pending = False
        self.update_progress = {
            "visible": False, "percent": 0,
            "text": "No se pudo descargar la actualización.",
        }
        version = (self._available_update or {}).get("latest", "nueva")
        self.update_button = f"Reintentar descarga v{version}"
        self.push_state()
        cause, action = _friendly_error(error)
        raw = str(error) if error is not None else ""
        detail = (f"{type(error).__name__}: {raw}" if raw
                  else type(error).__name__)
        log.warning("descarga de actualización fallida: %s", detail,
                    exc_info=True)
        self.toast("Descarga de actualización fallida",
                   f"{cause}\n{action}\nDetalle: {detail}", level="warn",
                   actions=(("Reintentar", self._download_pending),
                            ("Después", lambda: None)))

    def _update_ready(self, payload):
        if self._closed:
            return
        target, expected = payload[:2]
        self._update_download_pending = False
        version = (payload[2] if len(payload) > 2 else
                   (self._pending_update or {}).get("latest", "nueva"))
        self._ready_update = (target, expected, version)
        self.update_button = f"Reiniciar para aplicar v{version}"
        self.update_progress = {
            "visible": False, "percent": 100,
            "text": "Descarga verificada. Lista para instalar.",
        }
        self.push_state()
        self.toast("Actualización lista",
                   f"v{version} se descargó y verificó. Instant2 se va a cerrar "
                   "un momento para aplicar el cambio.",
                   actions=(("Actualizar y reiniciar",
                             lambda: self._install_ready(target, expected)),
                            ("Después", lambda: None)))

    def _pending_version(self):
        """La versión que se pidió instalar (la del release pendiente)."""
        ready = self._ready_update or ()
        if len(ready) > 2 and ready[2]:
            return str(ready[2])
        return str((self._pending_update or {}).get("latest", "") or "")

    def _report_pending_update(self):
        """Cuenta el resultado de la actualización anterior, si quedó a medias."""
        report = _pending_update_report()
        if report is None:
            return
        title, message, level = report
        self.toast(title, message, level=level)

    def _install_ready(self, target, expected):
        from instant2_app import update as update_module

        if self._update_install_pending:
            return
        try:
            _verify_install_file(target, expected)
        except Exception as exc:
            self._update_install_pending = False
            self.push_state()
            _toast_error(self, "Actualización sin aplicar", exc)
            return
        self._update_install_pending = True
        self.update_button = "Preparando actualización…"
        self.push_state()
        mode = update_module.install_mode()
        version = self._pending_version()
        if mode == "installed":
            # El instalador cierra la app: se anota qué se pidió para
            # poder verificar la versión instalada en el próximo arranque.
            _write_pending_update(version, _installer_log_path())
            self._install_via_setup(target, expected)
            return
        if sys.platform != "win32" and getattr(sys, "frozen", False):
            self._install_in_place(target, expected)
            return
        root = _workdir()
        destination = os.path.join(root, "dist", "Instant2.exe")
        script = os.path.join(root, "scripts", "instant2-update.bat")
        if not os.path.isfile(script):
            script = os.path.join(root, "instant2-update.bat")
        if getattr(sys, "frozen", False):
            destination = sys.executable
            bundled = os.path.join(getattr(sys, "_MEIPASS", ""),
                                   "instant2-update.bat")
            script = ""
            if os.path.isfile(bundled):
                # El onefile borra _MEIPASS al salir. Copiamos el helper chico
                # a Temp antes de cerrarnos y le pasamos el exe portable real.
                try:
                    import tempfile
                    import shutil
                    helper_dir = os.path.join(
                        tempfile.gettempdir(), f"instant2_update_{os.getpid()}")
                    os.makedirs(helper_dir, exist_ok=True)
                    script = os.path.join(helper_dir, "instant2-update.bat")
                    shutil.copyfile(bundled, script)
                except OSError as exc:
                    self._update_install_failed(exc)
                    return
        if not os.path.isfile(script):
            self._update_install_pending = False
            self.update_button = f"Reiniciar para aplicar v{(self._pending_update or {}).get('latest', '')}"
            self.push_state()
            self.toast(
                "Actualizaciones",
                "No encuentro instant2-update.bat junto a la app "
                "(instalación portable sin scripts)." + chr(10) + chr(10) +
                f"Instalá a mano: cerrá Instant2 por completo y copiá{chr(10)}{target}{chr(10)}"
                f"sobre tu binario de Instant2 (SHA256 {expected[:16]}…).",
                level="warn")
            return
        # El helper también cierra la app: se anota qué se pidió para poder
        # verificar la versión instalada en el próximo arranque.
        _write_pending_update(version, _update_log_path())
        try:
            log_path = _update_log_path()
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            output = open(log_path, "a", encoding="utf-8", errors="replace")
        except OSError as exc:
            self._update_install_failed(exc)
            return
        try:
            # Sin CREATE_NO_WINDOW el helper abre una terminal con sus pasos
            # en medio de la actualización. Su salida va al log: el usuario
            # no ve una ventana y el paso a paso queda para diagnosticar.
            env = dict(os.environ)
            # El helper sabe que su stdout ya apunta al log: no lo escribe
            # dos veces.
            env["INSTANT2_UPDATE_LOG_REDIRECTED"] = "1"
            subprocess.Popen(
                ["cmd", "/c", script, target, expected, destination], cwd=root,
                env=env, stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:  # noqa: BLE001  (se informa al usuario)
            self._update_install_pending = False
            self.update_button = f"Reiniciar para aplicar v{(self._pending_update or {}).get('latest', '')}"
            self.push_state()
            _toast_error(self, "Actualizaciones", exc)
            return
        finally:
            # El hijo ya tiene su propia copia del handle: el padre puede
            # cerrar el suyo sin perder la salida del helper.
            output.close()
        self.close()

    def _install_via_setup(self, installer_path, expected):
        """Modo instalado (Windows): el Setup nuevo actualiza y reabre solo.

        Todo lo bloqueante va en el worker: verificar el instalador, frenar
        el daemon y ESPERAR al instalador. El hilo de UI tiene que seguir
        respondiendo: el instalador de Inno manda WM_CLOSE al panel y un
        hilo congelado no lo procesa (Inno escala a matar el proceso).
        """
        self.tasks.submit(
            lambda _emit: self._run_installer(installer_path, expected),
            self._installer_finished,
            self._update_install_failed)

    @staticmethod
    def _run_installer(installer_path, expected=None):
        """Verifica, frena el daemon y espera al instalador. (hilo worker)

        Devuelve (codigo de salida, log de Inno, instalador). La espera
        puede tardar minutos (copia los 4 GB de modelos): por eso esto
        corre fuera del hilo de UI.
        """
        log_path = _installer_log_path()
        _verify_install_file(installer_path, expected)
        os.makedirs(config_dir(), exist_ok=True)
        PanelLogic._prepare_installer()
        # /LOG deja el detalle de Inno en un archivo: sin eso, un aborto
        # del instalador es invisible. Se espera el proceso y se lee su
        # código de salida: sirve cuando el instalador falla antes de
        # cerrar el panel (Inno lo cierra después para copiar los archivos).
        result = subprocess.run(
            [installer_path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
             f"/LOG={log_path}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=_INSTALL_TIMEOUT)
        return result.returncode, log_path, installer_path

    def _installer_finished(self, outcome):
        """El instalador terminó: se informa en el hilo de UI.

        Ojo: Inno cierra el panel (Restart Manager) para poder reemplazar
        los archivos, así que muchas veces este callback no llega: el
        mecanismo que verifica el resultado de verdad es la nota
        `pending-update.json`, que el arranque siguiente compara con la
        versión instalada.
        """
        if self._closed:
            return
        returncode, log_path, installer_path = outcome
        if returncode != 0:
            self._update_install_failed(
                _installer_failure(returncode, log_path))
            return
        log.info("instalador %s terminado con codigo 0 (log: %s)",
                 installer_path, log_path)
        # La app que sigue corriendo es la versión vieja: se cierra para que
        # el usuario la abra de nuevo y vea la versión nueva.
        self.close()

    @staticmethod
    def _prepare_installer():
        # El daemon no tiene ventana: el Restart Manager del instalador no
        # puede cerrarlo, así que se frena acá y se espera a que suelte
        # los archivos antes de copiar encima.
        stop_daemon()
        deadline = time.monotonic() + DAEMON_STOP_DEADLINE
        alive = False
        while True:
            remaining = deadline - time.monotonic()
            # El probe tiene un piso de presupuesto: con el tiempo que
            # queda para el deadline se quedaría sin respuesta y un daemon
            # vivo parecería "no verificable" en vez de "sigue activo".
            state = daemon_probe_state(
                budget=max(remaining, _PROBE_BUDGET_FLOOR))
            if state == DAEMON_STOPPED:
                return
            alive = alive or state == DAEMON_RUNNING
            # El deadline tiene prioridad sobre el probe caído: si el daemon
            # se vio vivo y no se frenó, el problema es el daemon, no el
            # probe.
            if time.monotonic() >= deadline:
                if alive:
                    raise RuntimeError(
                        "El servicio de dictado sigue activo. "
                        "Cerralo y reintentá.")
                raise RuntimeError(
                    "No se pudo verificar si el servicio de dictado está "
                    "activo: cerrá Instant2 por completo y reintentá.")
            if state == DAEMON_UNKNOWN:
                raise RuntimeError(
                    "No se pudo verificar si el servicio de dictado está "
                    "activo: cerrá Instant2 por completo y reintentá.")
            # 0.3 s entre probes: el sondeo no tiene que ser agresivo (cada
            # tasklist.exe cuesta ~0.25 s en esta máquina).
            time.sleep(0.3)

    def _update_install_failed(self, error):
        if self._closed:
            return
        # El fallo se está contando ahora: no queda nada pendiente para
        # reportar en el próximo arranque.
        _clear_pending_update()
        self._update_install_pending = False
        self.update_button = (f"Reintentar instalación v"
                              f"{(self._pending_update or {}).get('latest', '')}")
        self.push_state()
        _toast_error(self, "Actualización sin aplicar", error)

    def _install_in_place(self, downloaded, expected=None):
        """Linux/macOS: reemplazo en caliente del binario y reinicio del daemon."""
        from instant2_app import update as update_module

        try:
            _verify_install_file(downloaded, expected)
            applied = update_module.apply_binary_update(
                downloaded, expected_sha256=expected)
        except TypeError:
            # update.py viejo sin expected requerido: se intenta sin él, pero
            # el re-hash previo ya corrió (nunca se instala a ciegas).
            try:
                applied = update_module.apply_binary_update(downloaded)
            except Exception as exc:  # noqa: BLE001  (se informa al usuario)
                self._update_install_pending = False
                self.update_button = f"Reiniciar para aplicar v{(self._pending_update or {}).get('latest', '')}"
                self.push_state()
                _toast_error(self, "Actualizaciones", exc)
                return
        except Exception as exc:  # noqa: BLE001  (se informa al usuario)
            self._update_install_pending = False
            self.update_button = f"Reiniciar para aplicar v{(self._pending_update or {}).get('latest', '')}"
            self.push_state()
            _toast_error(self, "Actualizaciones", exc)
            return
        log.info("binario actualizado en %s", applied)
        self.toast("Actualizaciones",
                   "Versión nueva aplicada al binario. Se reinicia el "
                   "dictado con ella; reabrí el panel para tenerla también acá.",
                   level="info")
        self.stop_daemon_flow(restart=True)

    # ------------------------------------------------------------- navegación

    def navigate(self, name):
        pages = {"home", "audio", "settings", "vocab", "read"}
        self.page = name if isinstance(name, str) and name in pages else "home"
        self.emit("navigate", {"page": self.page})

    def request_daemon_start(self):
        self._start_daemon_on_open = True
        self._maybe_start_daemon_on_open()

    def apply_gui_action(self, page=None, start_daemon=False):
        """Pedido de otra instancia: enfoca la ventana y aplica la acción."""
        if page == "diagnostics":
            self.show_diagnostics()
        elif page == "setup":
            self.navigate("audio")
        if start_daemon:
            self.navigate("home")
            self.request_daemon_start()
        if page == "home" or (page is None and not start_daemon):
            self._nudge_setup_if_needed()

    def _nudge_setup_if_needed(self):
        """Avisa que falta el reconocedor sin bloquear la lectura local."""
        if self.page != "home" or self.model_ready:
            return
        self.toast("Dictado pendiente",
                   "Para dictar, prepará el reconocimiento desde Ajustes. Mientras tanto, "
                   "podés usar Leer texto en voz alta.",
                   level="warn")

    def close(self):
        if self._closed:
            return
        self._closed = True
        tts_timer, self._tts_idle_timer = self._tts_idle_timer, None
        if tts_timer is not None:
            tts_timer.cancel()
        if self._tts_cancel_event is not None:
            self._tts_cancel_event.set()
        # The resident daemon owns its local LLM; closing this panel must not
        # stop dictation or the model that serves it.
        if self._model_cancel_event is not None:
            self._model_cancel_event.set()
        self.tasks.close()
        self.emit("close", {})


def _current_version():
    try:
        from instant2_app import __version__
        return __version__
    except Exception:  # pragma: no cover - versión siempre presente
        return "?"


def _panel_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def compose_panel_page(html, qwebchannel_js):
    """Resuelve recursos locales e inyecta el canal en la página autónoma."""
    from pathlib import Path
    from instant2_app.branding import panel_css_tokens

    base_url = Path(_panel_dir()).as_uri() + "/"
    html = html.replace("<!--PANEL_ASSETS-->", f'<base href="{base_url}">', 1)
    html = html.replace("/*PANEL_PALETTE*/", panel_css_tokens(), 1)
    return html.replace("<!--QWEBCHANNEL-->",
                        "<script>\n" + qwebchannel_js + "\n</script>", 1)


def write_panel_page():
    """Compone web/panel.html + qwebchannel.js en el dir de datos."""
    from importlib import import_module
    from PySide6.QtCore import QFile, QIODevice

    import_module("PySide6.QtWebChannel")  # registra :/qtwebchannel/qwebchannel.js

    from instant2_app.paths import config_dir

    handle = QFile(":/qtwebchannel/qwebchannel.js")
    if not handle.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("no pude leer :/qtwebchannel/qwebchannel.js")
    try:
        channel_js = bytes(handle.readAll()).decode("utf-8")
    finally:
        handle.close()
    with open(os.path.join(_panel_dir(), "panel.html"), encoding="utf-8") as source:
        html = source.read()
    directory = os.path.join(config_dir(), "webui")
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, "panel.html")
    tmp = target + f".tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as destination:
        destination.write(compose_panel_page(html, channel_js))
    os.replace(tmp, target)
    return target


class _WebPanel:
    """Ventana QtWebEngine del panel; conecta la página con `PanelLogic`."""

    def __init__(self, page="home", autostart_override=None,
                 start_daemon_on_open=False):
        import importlib

        qtcore = importlib.import_module("PySide6.QtCore")
        qtgui = importlib.import_module("PySide6.QtGui")
        qtqml = importlib.import_module("PySide6.QtQml")
        webengine = importlib.import_module("PySide6.QtWebEngineQuick")
        QObject, QPoint, QThread, QTimer, Qt, QUrl = (
            getattr(qtcore, name) for name in
            ("QObject", "QPoint", "QThread", "QTimer", "Qt", "QUrl"))
        Signal, Slot = qtcore.Signal, qtcore.Slot
        QGuiApplication = qtgui.QGuiApplication
        QQmlApplicationEngine = qtqml.QQmlApplicationEngine

        # Inicialización del motor ANTES de crear la QGuiApplication (requisito
        # de QtWebEngine; idempotente si ya se llamó). En binarios congelados
        # primero se le dice dónde está su proceso helper.
        from instant2_app.launch import prepare_webengine_env
        prepare_webengine_env()
        webengine.QtWebEngineQuick.initialize()
        self.application = QGuiApplication.instance() or QGuiApplication(["Instant2"])
        if QThread.currentThread() != self.application.thread():
            raise RuntimeError("web panel must be created on the QApplication thread")
        self.application.setQuitOnLastWindowClosed(False)

        self._engine = QQmlApplicationEngine()
        host_path = os.path.join(os.path.dirname(__file__), "qml", "panel_web_host.qml")
        self._engine.load(QUrl.fromLocalFile(host_path))
        roots = self._engine.rootObjects()
        if not roots:
            raise RuntimeError(f"Could not load panel host: {host_path}")
        self.root = roots[0]
        self.view = self.root.findChild(QObject, "view")
        if self.view is None:
            raise RuntimeError("WebEngineView not found in panel host")

        class Bridge(QObject):
            tasksReady = Signal()
            state = Signal(str)
            toast = Signal(str)
            meter = Signal(str)
            progress = Signal(str)
            diagnostics = Signal(str)
            key_result = Signal(str)
            key_capture = Signal(str)
            navigate = Signal(str)

            @Slot(str)
            def call(self, payload):
                self.received.emit(payload)

            @Slot(str, str)
            def fileSelected(self, kind, path):
                panel._handle_runtime_file_selected(kind, path)

            received = Signal(str)

        panel = self
        self.bridge = Bridge(self.application)
        self.root.registerBridge(self.bridge)
        self._window_closing = False

        self.logic = PanelLogic(
            page=page, autostart_override=autostart_override,
            start_daemon_on_open=start_daemon_on_open,
            emit=self._emit, schedule=self._schedule)
        self.bridge.received.connect(
            self._handle_call, Qt.ConnectionType.QueuedConnection)

        self.root.setProperty("pageUrl", QUrl.fromLocalFile(write_panel_page()))
        self.root.setProperty("visible", True)

        # Workers wake the UI only when a result/progress event is available.
        self.bridge.tasksReady.connect(
            self.logic.tasks.drain, Qt.ConnectionType.QueuedConnection)
        self.logic.tasks.notify = self.bridge.tasksReady.emit
        self._daemon_timer = QTimer(self.root)
        self._daemon_timer.setInterval(3000)
        self._daemon_timer.timeout.connect(self.logic.refresh_daemon)
        self._daemon_timer.start()
        self._llm_timer = QTimer(self.root)
        self._llm_timer.setInterval(3000)
        self._llm_timer.timeout.connect(self.logic.refresh_llm_runtime)
        self._llm_timer.start()

        QTimer.singleShot(0, lambda: self.logic.refresh_microphones(initial=True))
        QTimer.singleShot(900, self.logic.sync_llm_runtime)
        QTimer.singleShot(800, self.logic.refresh_daemon)
        QTimer.singleShot(8000, self.logic._silent_update_check)
        try:
            if page == "diagnostics":
                QTimer.singleShot(150, self.logic.show_diagnostics)
            self.apply_gui_action(page, start_daemon_on_open)
        except Exception:
            log.warning("no pude aplicar la acción de arranque", exc_info=True)

        try:
            self.root.closed.connect(self._on_closed)
        except Exception:
            log.debug("no pude conectar closed del host", exc_info=True)

    # ------------------------------------------------------------ conexiones

    def _emit(self, kind, payload):
        try:
            if kind == "state":
                self.bridge.state.emit(json.dumps(payload, ensure_ascii=False))
            elif kind == "toast":
                self.bridge.toast.emit(json.dumps(payload, ensure_ascii=False))
            elif kind == "meter":
                self.bridge.meter.emit(json.dumps(payload, ensure_ascii=False))
            elif kind == "progress":
                self.bridge.progress.emit(json.dumps(payload, ensure_ascii=False))
            elif kind == "diagnostics":
                self.bridge.diagnostics.emit(json.dumps(payload, ensure_ascii=False))
            elif kind == "key_result":
                self.bridge.key_result.emit(json.dumps(payload, ensure_ascii=False))
            elif kind == "key_capture":
                self.bridge.key_capture.emit(json.dumps(payload, ensure_ascii=False))
            elif kind == "navigate":
                self.bridge.navigate.emit(json.dumps(payload, ensure_ascii=False))
            elif kind == "close":
                self._close_window()
            # "idle" y "mic_loaded" no tienen contraparte en la página.
        except Exception:
            log.debug("no pude empujar %s al panel", kind, exc_info=True)

    def _handle_call(self, payload):
        try:
            message = json.loads(payload)
        except ValueError:
            return
        if message.get("op") == "choose_runtime_file":
            kind = message.get("kind")
            if kind in ("llm_server_path", "llm_model_path"):
                try:
                    self.root.openFileDialog(kind)
                except Exception:
                    self.logic.toast("No se pudo abrir el selector de archivos",
                                     "Podés pegar la ruta del archivo en Ajustes.",
                                     level="warn")
            return
        self.logic.handle(message)

    def _handle_runtime_file_selected(self, kind, path):
        if kind not in ("llm_server_path", "llm_model_path"):
            return
        self.logic.handle({"op": "set_advanced", "key": kind,
                           "value": path})

    def _schedule(self, milliseconds, fn):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(milliseconds, fn)

    def _on_closed(self):
        """El usuario cerró la ventana: se cierra la lógica y sale el proceso."""
        self._window_closing = True
        try:
            self.logic.close()
        finally:
            self.application.quit()

    def _close_window(self):
        """Cierra el host QML sin reentrar por la señal `closed` de la ventana."""
        if self._window_closing:
            return
        self._window_closing = True
        try:
            self.root.close()
        except Exception:
            log.exception("no pude cerrar la ventana del panel")
            self.application.quit()

    # ------------------------------------------------------------- pública

    def apply_gui_action(self, page=None, start_daemon=False):
        """Pedido de otra instancia: enfoca la ventana y aplica la acción."""
        try:
            self.root.setProperty("visible", True)
            self.root.showNormal()
            self.root.raise_()
            self.root.requestActivate()
        except Exception:
            log.debug("no pude enfocar el panel", exc_info=True)
        self.logic.apply_gui_action(page, start_daemon)

    @property
    def _closed(self):
        return self.logic._closed

    def close(self):
        self.logic.close()

    def show(self):
        self.root.setProperty("visible", True)

    def showNormal(self):
        self.show()

    def raise_(self):
        try:
            self.root.raise_()
        except Exception:
            pass

    def activateWindow(self):
        try:
            self.root.requestActivate()
        except Exception:
            pass


def run_gui(page="home", autostart_override=None, start_daemon_on_open=False):
    mutex = None
    try:
        if sys.platform == "win32":
            # Icono propio en la barra de tareas: sin AppUserModelID explícito,
            # Windows agrupa la ventana bajo el icono de Python (pythonw.exe).
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    _APP_USER_MODEL_ID)
            except Exception:
                log.warning("no pude fijar el AppUserModelID", exc_info=True)
            from instant2_app.gui_lifecycle import acquire_gui_mutex
            mutex = acquire_gui_mutex(
                request_daemon_start=start_daemon_on_open, page=page)
            if mutex is None:
                return 0
        elif sys.platform.startswith("linux") and not (
                os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            # Sin servidor gráfico Qt aborta el proceso: se avisa y se sale.
            logging.getLogger("instant2").error(
                "sin entorno gráfico (DISPLAY/WAYLAND_DISPLAY); "
                "para configurar sin ventana: instant2 setup --tui")
            return 2
        from PySide6.QtWebEngineQuick import QtWebEngineQuick

        from instant2_app.launch import prepare_webengine_env
        prepare_webengine_env()
        QtWebEngineQuick.initialize()
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
        if sys.platform != "win32" and _forward_to_existing_gui(
                page, start_daemon_on_open):
            log.info("el panel ya estaba abierto; se le pasó el pedido.")
            return 0
        app.setApplicationName("Instant2")
        from io import BytesIO

        from PySide6.QtGui import QIcon, QPixmap

        from instant2_app.branding import create_icon_image
        icon_bytes = BytesIO()
        create_icon_image(64).save(icon_bytes, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(icon_bytes.getvalue(), "PNG")
        app.setWindowIcon(QIcon(pixmap))
        window = _WebPanel(page=page, autostart_override=autostart_override,
                           start_daemon_on_open=start_daemon_on_open)
        if sys.platform != "win32":
            # La referencia vive en la ventana: el canal muere con ella.
            window._gui_server = _install_gui_server(window)
        log.info("panel abierto (%s).", page)
        return app.exec()
    except Exception:
        logging.getLogger("instant2").exception("interfaz gráfica no disponible")
        return 2
    finally:
        if mutex is not None:
            from instant2_app.gui_lifecycle import release_gui_mutex
            release_gui_mutex(mutex)
