"""Overlay del daemon con la UI en web (QtWebEngine + QWebChannel).

La ventana es la misma de siempre (transparente, encima, sin foco) pero el
dibujo vive en `web/overlay.html` corriendo en QtWebEngine y habla con el
daemon por QWebChannel: Python empuja frames (nivel/bandas/pitch/modo) y la
pagina avisa `ready` al arrancar. Reemplaza al renderer Qt Quick como primera
opcion; Qt Quick y Tk quedan como respaldos (overlay.py los elige).

Decisiones de diseño:

- El coalescing del nivel vive del lado consumidor: los hilos de audio escriben
  el ultimo valor y un timer del hilo Qt emite como maximo cada 40 ms (mismo
  espiritu que las compuertas del renderer QML, sin colas que crezcan).
- La pagina pausa su loop de dibujo en idle: sin dictado no hay rAF corriendo,
  asi el daemon en reposo no gasta CPU de mas.
- `qwebchannel.js` se inyecta desde el recurso propio de Qt al componer la
  pagina (no se copia el archivo ajeno al repo).
"""
import importlib
import json
import logging
import os
import threading
import time

from instant2_app.paths import config_dir

log = logging.getLogger("instant2")

# Tamano de ventana por estilo: la composicion se dibuja abajo al centro
# del monitor y el ancho extra es para los avisos largos (no hace falta
# reposicionar ni animar el tamano de la ventana).
WINDOW_SIZES = {"classic": (480, 140), "orbital": (480, 140)}
_OVERLAY_BOTTOM_GAP = 16
_FLUSH_SECONDS = 0.04
_PAGE_PLACEHOLDER = "<!--QWEBCHANNEL-->"
_PAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def frame_payload(mode, message, key_label, style, level=None, pitch=None,
                  bands=None, read_key_label="F10"):
    """JSON parcial de estado consumido por el overlay."""
    payload = {"mode": mode, "message": message or "", "keyLabel": key_label,
               "style": style}
    if read_key_label != "F10":
        payload["readKeyLabel"] = read_key_label
    if level is not None:
        payload["level"] = round(float(level), 4)
    if pitch is not None:
        payload["pitch"] = round(float(pitch), 4)
    if bands is not None:
        payload["bands"] = [round(float(v), 4) for v in bands]
    return json.dumps(payload, separators=(",", ":"))

def compose_page(html, qwebchannel_js):
    """Inyecta qwebchannel.js en la plantilla (marcador comentado, una vez)."""
    from instant2_app.branding import overlay_tokens
    html = html.replace("/*OVERLAY_PALETTE*/", json.dumps(overlay_tokens()), 1)
    return html.replace(
        _PAGE_PLACEHOLDER, "<script>\n" + qwebchannel_js + "\n</script>", 1)


def _read_qwebchannel():
    from importlib import import_module
    from PySide6.QtCore import QFile, QIODevice

    import_module("PySide6.QtWebChannel")  # registra :/qtwebchannel/qwebchannel.js
    handle = QFile(":/qtwebchannel/qwebchannel.js")
    if not handle.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("no pude leer :/qtwebchannel/qwebchannel.js")
    try:
        return bytes(handle.readAll()).decode("utf-8")
    finally:
        handle.close()


def write_page(directory=None):
    """Compone web/overlay.html + qwebchannel.js en el dir de datos y devuelve
    la ruta final. Se reescribe en cada arranque: es salida derivada, no dato."""
    directory = directory or os.path.join(config_dir(), "webui")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(_PAGE_DIR, "overlay.html"), encoding="utf-8") as handle:
        html = handle.read()
    target = os.path.join(directory, "overlay.html")
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(compose_page(html, _read_qwebchannel()))
    os.replace(tmp, target)
    return target


class _WebOverlay:
    """Renderer del daemon sobre QtWebEngine; interfaz identica a la de Qt Quick."""

    def __init__(self, key_label, style="classic", read_key_label="F10"):
        qtcore = importlib.import_module("PySide6.QtCore")
        qtgui = importlib.import_module("PySide6.QtGui")
        qtqml = importlib.import_module("PySide6.QtQml")
        webengine = importlib.import_module("PySide6.QtWebEngineQuick")
        QObject, QPoint, QThread, QTimer, Qt, QUrl = (
            getattr(qtcore, name) for name in ("QObject", "QPoint", "QThread", "QTimer", "Qt", "QUrl"))
        Signal, Slot = qtcore.Signal, qtcore.Slot
        QCursor, QGuiApplication = qtgui.QCursor, qtgui.QGuiApplication
        QQmlApplicationEngine = qtqml.QQmlApplicationEngine

        # Inicializacion del motor ANTES de crear la QGuiApplication (requisito
        # de QtWebEngine; idempotente si ya se llamo). En binarios congelados
        # primero se le dice donde esta su proceso helper.
        from instant2_app.launch import prepare_webengine_env
        prepare_webengine_env()
        webengine.QtWebEngineQuick.initialize()
        self.application = QGuiApplication.instance() or QGuiApplication(["Instant2"])
        if QThread.currentThread() != self.application.thread():
            raise RuntimeError("web overlay must be created on the QApplication thread")
        self.application.setQuitOnLastWindowClosed(False)

        self.style = style if style in WINDOW_SIZES else "classic"
        self.key_label = key_label
        self.read_key_label = read_key_label
        self._dispatch_lock = threading.Lock()
        self._latest_token = -1
        self._pending_lock = threading.Lock()
        self._pending = {}
        self._mode = "idle"
        self._message = ""
        self._ready = False

        class EventBridge(QObject):
            frame = Signal(str)           # Python -> JS (QWebChannel)
            dispatchIn = Signal(object)   # dispatch (cualquier hilo) -> presentador
            received = Signal(str)        # JS -> Python (ready, ...)

            @Slot(str)
            def event(self, payload):
                # Slot que JS invoca por QWebChannel: bridge.event(json).
                self.received.emit(payload)

        class Presenter(QObject):
            def __init__(presenter, owner):
                super().__init__()
                presenter.owner = owner
                presenter.current_token = -1
                presenter.last_heartbeat = time.monotonic()

            def _position(presenter):
                cursor = QCursor.pos()
                screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
                bounds = screen.availableGeometry()
                width, height = WINDOW_SIZES[presenter.owner.style]
                x = bounds.left() + (bounds.width() - width) // 2
                y = bounds.bottom() - height - _OVERLAY_BOTTOM_GAP
                x = max(bounds.left(), x)
                y = max(bounds.top(), y)
                presenter.owner.root.setProperty("width", width)
                presenter.owner.root.setProperty("height", height)
                presenter.owner.root.setPosition(QPoint(x, y))
                presenter.owner.root.raise_()

            @Slot(object)
            def render(presenter, event):
                token, _session, state, text, color, milliseconds = event
                if token < presenter.current_token:
                    return
                presenter.current_token = token
                presenter.owner._mode = state
                presenter.owner._message = text or ""
                presenter._position()
                presenter.owner._push_frame()
                if state == "idle":
                    return
                if milliseconds:
                    QTimer.singleShot(
                        milliseconds, lambda t=token: presenter._expire(t))

            def _expire(presenter, token):
                if (token == presenter.current_token
                        and presenter.owner._is_latest_token(token)):
                    presenter.owner._mode = "idle"
                    presenter.owner._message = ""
                    presenter.owner._push_frame()

            @Slot(str)
            def onEvent(presenter, payload):
                try:
                    event = json.loads(payload)
                except ValueError:
                    return
                if event.get("kind") == "ready":
                    presenter.owner._ready = True
                    log.info("overlay web listo (%s)", presenter.owner.style)
                    # La pagina pudo arrancar con un estado ya despachado:
                    # se re-empuja el actual para no perder el primer frame.
                    presenter.owner._push_frame()


        self.engine = QQmlApplicationEngine()
        host_path = os.path.join(os.path.dirname(__file__), "qml", "overlay_web_host.qml")
        self.engine.load(QUrl.fromLocalFile(host_path))
        roots = self.engine.rootObjects()
        if not roots:
            raise RuntimeError(f"Could not load web overlay host: {host_path}")
        self.root = roots[0]
        self.view = self.root.findChild(QObject, "view")
        if self.view is None:
            raise RuntimeError("WebEngineView not found in host")

        self.bridge = EventBridge(self.application)
        self.presenter = Presenter(self)
        self.root.registerBridge(self.bridge)
        self.bridge.received.connect(
            self.presenter.onEvent, Qt.ConnectionType.QueuedConnection)
        self.bridge.dispatchIn.connect(
            self.presenter.render, Qt.ConnectionType.QueuedConnection)
        self.root.setProperty("pageUrl", QUrl.fromLocalFile(write_page()))
        self.presenter._position()

        # Flush del nivel: un solo frame cada 40 ms con lo ultimo pendiente.
        # Los frames viajan por la senal `frame` del canal (el camino validado
        # en el bake-off: runJavaScript del view QML exige callbacks QJSValue
        # que PySide no puede construir).
        self._flush_timer = QTimer(self.presenter)
        self._flush_timer.setInterval(int(_FLUSH_SECONDS * 1000))
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start()

    def _push_frame(self):
        try:
            self.bridge.frame.emit(frame_payload(
                self._mode, self._message, self.key_label, self.style,
                read_key_label=self.read_key_label))
        except Exception:
            log.debug("frame de estado descartado", exc_info=True)

    def _flush(self):
        with self._pending_lock:
            pending = self._pending
            self._pending = {}
        if not pending:
            return
        try:
            self.bridge.frame.emit(frame_payload(
                self._mode, self._message, self.key_label, self.style,
                read_key_label=self.read_key_label, **pending))
        except Exception:
            log.debug("frame de nivel descartado", exc_info=True)

    # Sin guarda de sesion a proposito: el wrapper Overlay.set_level/bands/pitch
    # ya rechaza sesiones viejas bajo lock; el renderer solo coalescea el ultimo
    # valor pendiente (ver _flush cada 40 ms).
    def set_level(self, session, value):
        del session
        with self._pending_lock:
            self._pending["level"] = value

    def set_bands(self, session, values):
        del session
        with self._pending_lock:
            self._pending["bands"] = list(values)

    def set_pitch(self, session, value):
        del session
        with self._pending_lock:
            self._pending["pitch"] = value

    def clear_bands(self):
        with self._pending_lock:
            self._pending["bands"] = [0.0] * 9

    def _is_latest_token(self, token):
        with self._dispatch_lock:
            return token == self._latest_token

    def dispatch(self, event):
        token = event[0]
        with self._dispatch_lock:
            self._latest_token = max(self._latest_token, token)
        self.bridge.dispatchIn.emit(event)

    def run_event_loop(self, shutdown, on_heartbeat):
        from PySide6.QtCore import QTimer

        timer = QTimer(self.presenter)
        timer.setInterval(100)

        def poll():
            if shutdown.is_set():
                self.application.quit()
                return
            now = time.monotonic()
            if now - self.presenter.last_heartbeat >= 60:
                self.presenter.last_heartbeat = now
                on_heartbeat()

        timer.timeout.connect(poll)
        timer.start()
        self.application.exec()
        timer.stop()
        return True
