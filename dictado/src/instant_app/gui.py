"""Panel de Instant: ventana QtWebEngine con la UI en web (web/panel.html).

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
import os
import queue
import signal
import subprocess
import sys
import threading
import time

from instant_app import audio, autostart, config, context, hotkey, models
from instant_app.launch import app_command, app_environment
from instant_app.paths import resolve_data_dir

log = logging.getLogger("instant")

_APP_USER_MODEL_ID = "getodevel-source.Instant.0.1"
UPDATE_CHECK_INTERVAL = 86400
DAEMON_MISSING_DETAIL = ("Instant no pudo iniciar. Revisá Diagnóstico.")
STATUS_DETAIL = "Enfocá el campo donde querés escribir."
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
                "Reabrí Instant normalmente (sin admin) y reintentá.")
    if any(mark in lowered for mark in busy):
        return ("El archivo está en uso por otro proceso.",
                "Cerrá Instant por completo (daemon incluido) y reintentá.")
    return ("Algo falló y no sabemos bien qué.",
            "Reintentá; si se repite, abrí Diagnóstico y copianos el texto.")


def _toast_error(logic, title, exc):
    """Toast accionable: causa+acción en criollo; detalle crudo solo al log."""
    cause, action = _friendly_error(exc)
    raw = str(exc) if exc is not None else ""
    detail = f"{type(exc).__name__}: {raw}" if raw else type(exc).__name__
    log.warning("%s: %s", title, detail, exc_info=True)
    logic.toast(title, f"{cause}\n{action}\nDetalle: {detail}", level="error")


def _toast_warn(logic, title, exc):
    """Variante warn de `_toast_error` (chequeos y descargas)."""
    cause, action = _friendly_error(exc)
    raw = str(exc) if exc is not None else ""
    detail = f"{type(exc).__name__}: {raw}" if raw else type(exc).__name__
    log.warning("%s: %s", title, detail, exc_info=True)
    logic.toast(title, f"{cause}\n{action}\nDetalle: {detail}", level="warn")


def _update_mode(cfg):
    """Modo de actualización con consentimiento: notify|auto|off."""
    mode = (cfg or {}).get("update_mode", "notify")
    if mode not in _UPDATE_MODES:
        return "notify"
    return mode


def _updates_disabled(cfg):
    """True si el usuario apagó todo chequeo (config o INSTANT_NO_UPDATE)."""
    if (os.environ.get("INSTANT_NO_UPDATE") or "").strip().lower() in (
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


def _pid_value():
    from instant_app.daemon import pid_path
    try:
        with open(pid_path(), encoding="utf-8") as file:
            return int(file.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def daemon_is_running():
    """True si el PID del pidfile sigue vivo y es Instant (POSIX verifica cmdline).

    POSIX: kill(pid, 0) con EPERM significa proceso vivo sin permiso para
    señalizar -> True (G4). Tras el kill-0 se verifica la cmdline con
    `_pid_is_instant` como hacen los .sh (`matches_instant`): un PID
    reciclado por otro programa no cuenta como vivo (G5).
    """
    pid = _pid_value()
    if pid is None:
        return False
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired):
            return False
        for row in result.stdout.splitlines():
            columns = [part.strip().strip('"') for part in row.split('","')]
            if len(columns) > 1 and columns[1] == str(pid):
                return columns[0].lower() in {"python.exe", "pythonw.exe", "instant.exe"}
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM: el proceso existe pero no tenemos permiso -> vivo (G4).
        return True
    except OSError:
        return False
    return _pid_is_instant(pid)


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


def _pid_is_instant(pid):
    """El PID file puede quedar rancio y el SO reciclar el número: antes de
    matar se confirma que la línea de comando sea de Instant."""
    if os.name == "nt":
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, timeout=10, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired):
            return True
        command = (result.stdout or "").casefold()
        return "instant" in command
    command = ""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as handle:
            command = handle.read().replace(b"\0", b" ").decode(
                "utf-8", "replace")
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        try:
            result = subprocess.run(
                ["ps", "-p", str(int(pid)), "-o", "args="],
                capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError, ValueError):
            return True
        if result.returncode != 0:
            return False
        command = result.stdout or ""
    return "instant" in command.casefold()


def stop_daemon():
    from instant_app.daemon import clear_pid
    pid = _pid_value()
    if pid is None:
        return False
    if not daemon_is_running():
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
        log.warning("PID %d reciclado por el SO (no es Instant); no se mata.", pid)
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


_GUI_SERVER_BASE = "instant-gui"
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


class TaskRunner:
    """Trabajo bloqueante en hilos, resultados encolados para el hilo de UI.

    Los hilos NUNCA tocan la UI: encolan y quien llama a `drain()` (el timer
    del panel, o un test) los aplica en su hilo. `close()` descarta lo que
    llegue después: una ventana cerrada no recibe resultados tardíos.
    """
    def __init__(self):
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._handlers = {}
        self._pending = 0
        self._closed = False
        self.on_idle = None
        self.on_error = None

    @property
    def pending(self):
        with self._lock:
            return self._pending

    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        with self._lock:
            if self._closed:
                return None
            token = id(fn) ^ time.monotonic_ns()
            self._handlers[token] = (on_result, on_error, on_progress)
            self._pending += 1

        def runner():
            try:
                value = fn(lambda payload: self._queue.put(("progress", token, payload)))
                self._queue.put(("result", token, value))
            except Exception as exc:  # noqa: BLE001  (cruza el hilo a la UI)
                self._queue.put(("error", token, exc))
            finally:
                self._queue.put(("done", token, None))

        threading.Thread(target=runner, daemon=True).start()
        return token

    def drain(self):
        """Aplica los eventos encolados; devuelve cuántos atendió."""
        applied = 0
        while True:
            try:
                kind, token, value = self._queue.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                handlers = self._handlers.get(token)
                closed = self._closed
            if kind == "done":
                with self._lock:
                    self._handlers.pop(token, None)
                    if self._pending:
                        self._pending -= 1
                    idle = self._pending == 0
                if idle and self.on_idle is not None and not closed:
                    self.on_idle()
                continue
            if closed or handlers is None:
                continue
            on_result, on_error, on_progress = handlers
            if kind == "progress" and on_progress is not None:
                on_progress(value)
            elif kind == "result" and on_result is not None:
                on_result(value)
            elif kind == "error":
                if on_error is not None:
                    on_error(value)
                elif self.on_error is not None:
                    self.on_error(value)
            applied += 1
        return applied

    def close(self):
        with self._lock:
            self._closed = True
            self._handlers.clear()
            self._pending = 0


class PanelLogic:
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
        from instant_app.update import install_mode
        self._update_source_mode = install_mode() == "source"
        self.update_progress = {"visible": False, "percent": 0, "text": ""}
        self._update_silent_done = False
        self._diagnostic_running = False
        self._diagnostics_open = False
        self._diagnostics_text = "Ejecutando comprobaciones…"
        self._toast_callbacks = {}
        self._advanced_warned = set()
        self._key_capturing = False
        self.mic_labels = []
        self.mic_selected = None
        self.key_value = hotkey.key_label(self.cfg.get("key", "f9"))
        self.status_var = "Listo para dictar"
        self.status_detail = STATUS_DETAIL.format(key=self.key_value)
        self.meter = {"percent": 0, "text": "La prueba no guarda audio."}
        self.model_status = ""
        self.model_progress = {"visible": False, "percent": 0}
        self._model_download_pending = False
        self._model_canceling = False
        self._model_cancel_event = None
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
            daemon_title = "Deteniendo Instant…"
        elif self._daemon_start_pending:
            daemon_status = "starting"
            daemon_title = "Iniciando Instant…"
        elif not self._daemon_state_ready or self._daemon_check_pending:
            daemon_status = "checking"
            daemon_title = "Comprobando Instant…"
        elif self._last_daemon_running:
            daemon_status = "ok"
            daemon_title = "Instant está activo"
        else:
            daemon_status = "down"
            daemon_title = "Instant está detenido"
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
            "autostart": bool(self._autostart_enabled),
            "advanced": {
                "llm_url": self.cfg.get("llm_url", ""),
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
            },
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
                           else "Descargar modelo"),
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
                           "Se usa silero hasta que elijas silero o ten.",
                           level="warn")

    def _checklist(self):
        """Checklist de Inicio: modelos/mic/tecla con link a su sección."""
        key_ok = bool((self.key_value or "").strip())
        return [
            {"id": "models", "label": "Modelos de voz",
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
            {"id": "key", "label": "Tecla para dictar",
             "ok": key_ok,
             "hint": str(self.key_value or "sin tecla"),
             "page": "settings"},
        ]

    def _disabled_reason(self):
        """Motivo inline del botón deshabilitado (title + aria-describedby)."""
        if self._last_daemon_running:
            return ""
        if not self.model_ready:
            return "Completá la descarga del modelo para habilitar el dictado."
        if self._selected_device() is None:
            if not self._microphones_loaded or self._microphones_refreshing:
                return "Buscando micrófonos…"
            if not self.catalog.devices:
                return "No se detectaron micrófonos: conectá una entrada."
            return "Elegí un micrófono disponible para iniciar Instant."
        if (self._daemon_start_pending or self._daemon_stop_pending
                or self._daemon_check_pending
                or not self._daemon_state_ready):
            return "Comprobando el estado de Instant…"
        return ""

    def toast(self, title, message="", level="info", actions=(), ms=4500):
        if self._closed:
            return
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

    def _op_key_capture_open(self, _message):
        self.begin_key_capture()

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
                    from instant_app.llm import check_url
                    value = check_url(value)
                except ValueError as exc:
                    self.toast("Dirección no válida", str(exc), level="warn")
                    return
            self.cfg["llm_url"] = value
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
                           "Elegí silero (liviano) o ten (más preciso).",
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
                self.cfg.get("llm_token", ""))

    @staticmethod
    def _restart_signature(snapshot):
        # El token (índice 13) no exige reinicio: se excluye a propósito.
        return (snapshot[0], snapshot[1], snapshot[3], snapshot[4], snapshot[5],
                snapshot[6], snapshot[7], snapshot[8], snapshot[9],
                snapshot[10], snapshot[11])

    def _same_settings(self, left, right):
        return (self.catalog.same_identity(left[0], right[0])
                and left[1:] == right[1:])

    def _update_settings_status(self):
        if self._saved_settings is None:
            self.settings_status = ""
        elif self._dirty():
            self.settings_status = "Hay cambios sin guardar."
        elif self._restart_needed:
            self.settings_status = ("Guardado; reiniciá Instant para aplicar "
                                    "micrófono, tecla, voz y rendimiento.")
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

    def begin_key_capture(self):
        self._key_capturing = True
        self.emit("key_capture", {"state": "open"})

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
                self.emit("key_result", {"ok": False,
                                         "message": "No pude identificar esa tecla; probá otra."})
                return
        elif hotkey.is_valid_key(name):
            value = hotkey.normalize_key(name)
        else:
            self.emit("key_result", {"ok": False,
                                     "message": "No pude identificar esa tecla; probá otra."})
            return
        self._key_capturing = False
        self._set_key(value)
        self.emit("key_result", {"ok": True, "message": ""})

    def _set_key(self, key):
        previous_detail = STATUS_DETAIL.format(key=self.key_value)
        self.key_value = hotkey.key_label(key)
        if self.status_detail == previous_detail:
            self.status_detail = STATUS_DETAIL.format(key=self.key_value)
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
            self.toast("Instant", "Ya existe ese perfil.", level="warn")
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
        self.cfg["llm_url"] = snapshot[5]
        self.cfg["threads"], self.cfg["sound"] = snapshot[6], snapshot[7]
        self.cfg["overlay_style"], self.cfg["max_seg"] = snapshot[8], snapshot[9]
        self.cfg["vad_model"], self.cfg["blank_penalty"] = snapshot[10], snapshot[11]
        self.cfg["update_mode"] = snapshot[12]
        config.save(self.cfg)
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
            note = (" Micrófono, tecla, voz y rendimiento guardados; reiniciá "
                    "Instant para aplicarlos." if self._restart_needed else
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
            self.toast("Modelos pendientes",
                       "Descargá los modelos de voz antes de iniciar Instant.",
                       level="warn")
            return False
        if save_settings:
            self.save_config(False)
        try:
            env = app_environment()
            env["DICTADO_DATA"] = self.data_dir
            self._daemon_start_pending = True
            self._daemon_start_token += 1
            token = self._daemon_start_token
            self.push_state()
            subprocess.Popen(app_command(("run",)), cwd=_workdir(),
                             env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.status_detail = "Iniciando Instant…"
            self.push_state()
            self.schedule(10000, lambda: self._daemon_start_timeout(token))
            self.schedule(700, self.refresh_daemon)
            return True
        except Exception as exc:  # noqa: BLE001  (se informa al usuario)
            self._daemon_start_pending = False
            self.push_state()
            _toast_error(self, "No se pudo iniciar Instant", exc)
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
        self.status_detail = "Verificando Instant…"
        self.push_state()

        def check(_emit):
            return _pid_is_instant(pid)

        def checked(is_ours):
            if self._closed:
                return
            if not is_ours:
                log.warning("PID %d reciclado por el SO (no es Instant); no se mata.", pid)
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
                self.status_detail = "No se pudo detener Instant."
                self.push_state()
                _toast_error(self, "No se pudo detener Instant", exc)
                return
            self.status_detail = "Cerrando Instant…"
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
            self.status_detail = "No se pudo comprobar el estado de Instant."
            self.push_state()
            _toast_error(self, "No se pudo detener Instant", error)

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
                self.status_var = "Instant está activo"
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
            self.status_detail = (f"No se pudo comprobar si Instant está activo: "
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
            self.model_status = "Falta descargar el modelo."
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
            label = "el modelo" if step == "parakeet" else "el detector de voz"
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
            self.model_status = "No se pudo descargar el modelo."
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

            from instant_app.daemon import cmd_check
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
            detail = (f"{type(error).__name__}: {raw}" if raw
                      else type(error).__name__)
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
        from instant_app import update as update_module
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
            from instant_app import update as update_module
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
        from instant_app import update as update_module
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
        from instant_app import update as update_module
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
        from instant_app import update as update_module
        info = self._pending_update
        os.makedirs(os.path.join(tempfile.gettempdir(), "instant_update"),
                    exist_ok=True)
        target = os.path.join(tempfile.gettempdir(), "instant_update", info["asset"])
        expected = update_module.fetch_expected_sha256(info["asset_url"])
        last_report = [0.0]

        def report(done, total):
            now = time.monotonic()
            if total and done < total and now - last_report[0] < 0.2:
                return
            last_report[0] = now
            emit({"done": done, "total": total})

        update_module.download(
            info["asset_url"], target, expected_sha256=expected, progress=report)
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
                   f"v{version} se descargó y verificó. Instant se va a cerrar "
                   "un momento para aplicar el cambio.",
                   actions=(("Actualizar y reiniciar",
                             lambda: self._install_ready(target, expected)),
                            ("Después", lambda: None)))

    def _install_ready(self, target, expected):
        from instant_app import update as update_module

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
        if mode == "installed":
            self._install_via_setup(target, expected)
            return
        if sys.platform != "win32" and getattr(sys, "frozen", False):
            self._install_in_place(target, expected)
            return
        root = _workdir()
        destination = os.path.join(root, "dist", "Instant.exe")
        script = os.path.join(root, "scripts", "instant-update.bat")
        if not os.path.isfile(script):
            script = os.path.join(root, "instant-update.bat")
        if getattr(sys, "frozen", False):
            destination = sys.executable
            bundled = os.path.join(getattr(sys, "_MEIPASS", ""),
                                   "instant-update.bat")
            script = ""
            if os.path.isfile(bundled):
                # El onefile borra _MEIPASS al salir. Copiamos el helper chico
                # a Temp antes de cerrarnos y le pasamos el exe portable real.
                try:
                    import tempfile
                    import shutil
                    helper_dir = os.path.join(
                        tempfile.gettempdir(), f"instant_update_{os.getpid()}")
                    os.makedirs(helper_dir, exist_ok=True)
                    script = os.path.join(helper_dir, "instant-update.bat")
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
                "No encuentro instant-update.bat junto a la app "
                "(instalación portable sin scripts)." + chr(10) + chr(10) +
                f"Instalá a mano: cerrá Instant por completo y copiá{chr(10)}{target}{chr(10)}"
                f"sobre tu binario de Instant (SHA256 {expected[:16]}…).",
                level="warn")
            return
        try:
            subprocess.Popen(
                ["cmd", "/c", script, target, expected, destination], cwd=root)
        except Exception as exc:  # noqa: BLE001  (se informa al usuario)
            self._update_install_pending = False
            self.update_button = f"Reiniciar para aplicar v{(self._pending_update or {}).get('latest', '')}"
            self.push_state()
            _toast_error(self, "Actualizaciones", exc)
            return
        self.close()

    def _install_via_setup(self, installer_path, expected):
        """Modo instalado (Windows): el Setup nuevo actualiza y reabre solo."""
        # Frenar y esperar al daemon puede tardar varios segundos. Hacerlo en
        # el worker evita congelar el panel mientras Windows suelta sus archivos.
        self.tasks.submit(
            lambda _emit: self._prepare_installer(),
            lambda _value: self._launch_setup_installer(installer_path, expected),
            self._update_install_failed)

    @staticmethod
    def _prepare_installer():
        # El daemon no tiene ventana: el Restart Manager del instalador no
        # puede cerrarlo, así que se frena acá y se espera a que suelte
        # los archivos antes de copiar encima.
        stop_daemon()
        deadline = time.monotonic() + 8.0
        while daemon_is_running() and time.monotonic() < deadline:
            time.sleep(0.1)
        if daemon_is_running():
            raise RuntimeError("El servicio de dictado sigue activo. Cerralo y reintentá.")

    def _launch_setup_installer(self, installer_path, expected=None):
        if self._closed:
            return
        try:
            _verify_install_file(installer_path, expected)
            subprocess.Popen(
                [installer_path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                close_fds=True)
        except Exception as exc:  # noqa: BLE001  (se informa al usuario)
            self._update_install_failed(exc)
            return
        log.info("instalador %s lanzado en silencio", installer_path)
        self.close()

    def _update_install_failed(self, error):
        if self._closed:
            return
        self._update_install_pending = False
        self.update_button = (f"Reintentar instalación v"
                              f"{(self._pending_update or {}).get('latest', '')}")
        self.push_state()
        _toast_error(self, "Actualización sin aplicar", error)

    def _install_in_place(self, downloaded, expected=None):
        """Linux/macOS: reemplazo en caliente del binario y reinicio del daemon."""
        from instant_app import update as update_module

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
        pages = {"home", "audio", "settings", "vocab"}
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
        """Primera ejecución: home con modelos pendientes lleva a setup visible.

        Solo redirige la vista (nunca descarga sola): el usuario ve la página
        de ajustes con el aviso de modelos pendientes."""
        if self.page != "home" or self.model_ready:
            return
        self.navigate("settings")
        self.push_state()
        self.toast("Te falta un paso",
                   "Descargá los modelos de voz (~670 MB, una sola vez) para "
                   "empezar a dictar. Estás en Ajustes: tocá Descargar modelo.",
                   level="warn")

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._model_cancel_event is not None:
            self._model_cancel_event.set()
        self.tasks.close()
        self.emit("close", {})


def _current_version():
    try:
        from instant_app import __version__
        return __version__
    except Exception:  # pragma: no cover - versión siempre presente
        return "?"


def _panel_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def compose_panel_page(html, qwebchannel_js):
    """Inyecta qwebchannel.js en la plantilla (marcador comentado)."""
    return html.replace("<!--QWEBCHANNEL-->",
                        "<script>\n" + qwebchannel_js + "\n</script>", 1)


def write_panel_page():
    """Compone web/panel.html + qwebchannel.js en el dir de datos."""
    from importlib import import_module
    from PySide6.QtCore import QFile, QIODevice

    import_module("PySide6.QtWebChannel")  # registra :/qtwebchannel/qwebchannel.js

    from instant_app.paths import config_dir

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
        from instant_app.launch import prepare_webengine_env
        prepare_webengine_env()
        webengine.QtWebEngineQuick.initialize()
        self.application = QGuiApplication.instance() or QGuiApplication(["Instant"])
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

            received = Signal(str)

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

        # Drenaje de resultados de workers + latido de estado del daemon.
        self._drain_timer = QTimer(self.root)
        self._drain_timer.setInterval(30)
        self._drain_timer.timeout.connect(self.logic.tasks.drain)
        self._drain_timer.start()
        self._daemon_timer = QTimer(self.root)
        self._daemon_timer.setInterval(3000)
        self._daemon_timer.timeout.connect(self.logic.refresh_daemon)
        self._daemon_timer.start()

        QTimer.singleShot(0, lambda: self.logic.refresh_microphones(initial=True))
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
        self.logic.handle(message)

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
            from instant_app.gui_lifecycle import acquire_gui_mutex
            mutex = acquire_gui_mutex(
                request_daemon_start=start_daemon_on_open, page=page)
            if mutex is None:
                return 0
        elif sys.platform.startswith("linux") and not (
                os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            # Sin servidor gráfico Qt aborta el proceso: se avisa y se sale.
            logging.getLogger("instant").error(
                "sin entorno gráfico (DISPLAY/WAYLAND_DISPLAY); "
                "para configurar sin ventana: instant setup --tui")
            return 2
        from PySide6.QtWebEngineQuick import QtWebEngineQuick

        from instant_app.launch import prepare_webengine_env
        prepare_webengine_env()
        QtWebEngineQuick.initialize()
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
        if sys.platform != "win32" and _forward_to_existing_gui(
                page, start_daemon_on_open):
            log.info("el panel ya estaba abierto; se le pasó el pedido.")
            return 0
        app.setApplicationName("Instant")
        from io import BytesIO

        from PySide6.QtGui import QIcon, QPixmap

        from instant_app.branding import create_icon_image
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
        logging.getLogger("instant").exception("interfaz gráfica no disponible")
        return 2
    finally:
        if mutex is not None:
            from instant_app.gui_lifecycle import release_gui_mutex
            release_gui_mutex(mutex)
