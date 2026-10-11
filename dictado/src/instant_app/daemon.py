"""Daemon hold-to-talk: graba en RAM, al soltar reconoce y pega. Cross-platform."""
import collections
import logging
import os
import queue
import threading
import time

import numpy as np

from instant_app import audio, bias, hotkey, llm
from instant_app.engine import Engine, SILENCE_PEAK
from instant_app.overlay import Overlay
from instant_app.tray import TrayIcon

log = logging.getLogger("instant")


def pid_path():
    """Ruta del PID file: %APPDATA%/instant/instant.pid (config_dir).

    Es el único pidfile del daemon: el lock POSIX de `daemon_lifecycle`
    escribe en este mismo archivo (`_daemon_pidfile` lo reutiliza), y
    `release_daemon_mutex` lo borra vía `clear_pid` al liberar.
    """
    from instant_app.paths import config_dir
    import os
    return os.path.join(config_dir(), "instant.pid")


def _pidfile_lock_handle():
    """Handle del flock POSIX si este proceso es dueño del pidfile, o None.

    daemon_lifecycle._acquire_posix guarda el fd lockeado en _lock_file;
    escribir sobre ese mismo fd evita el replace que cambiaba el inodo
    y dejaba al lock apuntando al archivo viejo (G1).
    """
    try:
        from instant_app import daemon_lifecycle
        handle = daemon_lifecycle._lock_file
    except Exception:
        return None
    if handle in (None, True):
        return None
    fileno = getattr(handle, "fileno", None)
    if not callable(fileno):
        return None
    try:
        fileno()
    except (OSError, ValueError):
        return None
    return handle

def write_pid():
    """Registra el PID al arrancar para stop/status. Nunca tumba el daemon.

    Si este proceso ya es dueño del lock POSIX, escribe sobre el mismo
    fd lockeado (seek+truncate+write) para no cambiar el inodo con un
    replace: el flock seguiría atado al archivo viejo y una segunda
    instancia podría tomarlo.
    """
    import os
    import tempfile
    try:
        from instant_app.paths import ensure_private_dir, restrict_file
        p = pid_path()
        ensure_private_dir(os.path.dirname(p))
        restrict_file(p)
        owner = _pidfile_lock_handle()
        if owner is not None:
            try:
                owner.seek(0)
                owner.truncate()
                owner.write(str(os.getpid()).encode("ascii"))
                owner.flush()
                os.fsync(owner.fileno())
                return
            except (OSError, ValueError):
                pass
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix="instant.pid.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            os.replace(tmp, p)
            restrict_file(p)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception:
        log.warning("no pude escribir instant.pid", exc_info=True)


def _clear_pid_if(expected_pid):
    """Borra el pidfile solo si su contenido sigue siendo expected_pid.

    A diferencia de clear_pid() (que compara con getpid() del llamador),
    esto permite que stop_daemon limpie el rancio del daemon muerto: el
    contenido == PID muerto esperado, no el PID del proceso que limpia.
    True si el archivo quedó ausente (borrado o ya inexistente).
    """
    import os
    try:
        p = pid_path()
        with open(p, encoding="utf-8") as f:
            if f.read().strip() != str(expected_pid):
                return False
        os.remove(p)
        return True
    except FileNotFoundError:
        return True
    except Exception:
        log.warning("no pude borrar instant.pid", exc_info=True)
        return False


def clear_pid(expected_pid=None):
    """Borra el PID file al salir limpio, solo si es el propio.

    Sin expected_pid compara con getpid() (salida del daemon dueño).
    Con expected_pid explícito borra si el contenido == ese PID: lo usa
    stop_daemon para limpiar el rancio del daemon ya muerto (G1).
    No toca el archivo si contiene otro PID (proceso ajeno o nueva
    instancia): esa es la guarda que evita borrar el lock de otro dueño.
    """
    import os
    if expected_pid is None:
        expected_pid = os.getpid()
    _clear_pid_if(expected_pid)


def estimate_pitch_norm(mono, sample_rate=16000.0):
    """Tono fundamental -> 0..1 (80 Hz..400 Hz en escala log), o None.

    Autocorrelación normalizada por FFT sobre la ventana de ~60 ms que ya
    usa el espectro. Barato (~200k operaciones a 20 Hz) y suficiente para
    teñir la animación: graves abajo, agudos arriba.
    """
    import math

    try:
        import numpy as _np
    except ImportError:
        return None
    x = _np.asarray(mono, dtype=_np.float64).reshape(-1)
    n = x.size
    if n < 256:
        return None
    x = x - float(_np.mean(x))
    if float(_np.max(_np.abs(x))) < 1e-4:
        return None
    lo = max(2, int(sample_rate / 500.0))
    hi = min(n // 2, int(sample_rate / 70.0))
    if hi <= lo:
        return None
    spectrum = _np.abs(_np.fft.rfft(x, n * 2)) ** 2
    corr = _np.fft.irfft(spectrum)[:hi + 1]
    denom = float(corr[0]) if float(corr[0]) > 0 else 0.0
    if denom <= 0:
        return None
    corr = corr / denom
    peak = int(_np.argmax(corr[lo:hi + 1])) + lo
    if float(corr[peak]) < 0.35:
        return None
    freq = sample_rate / peak
    norm = (math.log(max(freq, 1.0)) - math.log(80.0)) / (
        math.log(400.0) - math.log(80.0))
    return max(0.0, min(1.0, norm))


class _StreamDied(RuntimeError):
    pass


class _LevelTracker:
    """Nivel real del mic -> overlay: espectro 9 bandas + tono (~20 Hz).

    Misma matematica que usaba _record inline; extraida para compartirla
    entre captura continua y one-shot sin duplicarla.
    """

    def __init__(self, sid, overlay):
        self.sid = sid
        self.overlay = overlay
        self.band_edges = np.logspace(np.log10(80.0), np.log10(7500.0), 10)
        self.band_max = np.full(9, 1e-3, dtype=np.float64)
        self.smooth = 0.0
        self.pitch = 0.5
        # Ventana corrediza de ~60 ms para el espectro: con bloques de 10 ms
        # la FFT no resuelve graves; acumulando se gana fidelidad real.
        self.spec_window = []
        self.last = 0.0

    def add(self, blk):
        try:
            self.spec_window.append(np.asarray(
                audio.to_mono(blk), dtype=np.float64).reshape(-1))
            if len(self.spec_window) > 6:
                del self.spec_window[0]
        except Exception:
            pass

    def tick(self):
        now = time.monotonic()
        if now - self.last < 0.05:
            return
        self.last = now
        sid = self.sid
        try:
            window = (np.concatenate(self.spec_window)
                      if self.spec_window else np.zeros(0))
            peak = float(np.max(np.abs(window))) if window.size else 0.0
            # Puerta de silencio (piso medido 0.0027): por debajo se mandan
            # ceros, nunca bandas normalizadas contra un techo colapsado,
            # que era lo que bailaba solo.
            if peak > 0.006 and window.size >= 256:
                windowed = window * np.hanning(window.size)
                spectrum = np.abs(np.fft.rfft(windowed))
                freqs = np.fft.rfftfreq(window.size, 1.0 / 16000.0)
                vals = np.empty(9)
                for i in range(9):
                    mask = (freqs >= self.band_edges[i]) & (freqs < self.band_edges[i + 1])
                    vals[i] = float(np.mean(spectrum[mask])) if np.any(mask) else 0.0
                self.band_max = np.maximum(self.band_max * 0.995, vals)
                bands = np.clip(vals / np.maximum(self.band_max, 1e-9), 0.0, 1.0)
            else:
                bands = np.zeros(9)
            raw = float(np.max(bands))
            # Ataque rápido, caída suave: orgánico, no nervioso.
            if raw > self.smooth:
                self.smooth = raw
            else:
                self.smooth = self.smooth * 0.82 + raw * 0.18
            overlay = self.overlay
            setter = getattr(overlay, "set_level", None)
            if setter is not None:
                setter(sid, self.smooth)
            bands_setter = getattr(overlay, "set_bands", None)
            if bands_setter is not None:
                bands_setter(sid, [float(v) for v in bands])
            # Tono: solo con voz (puerta de silencio); si no hay tono,
            # deriva despacio al neutro.
            if raw > 0.05:
                heard = estimate_pitch_norm(window)
                if heard is not None:
                    self.pitch += (heard - self.pitch) * 0.35
            else:
                self.pitch += (0.5 - self.pitch) * 0.05
            pitch_setter = getattr(overlay, "set_pitch", None)
            if pitch_setter is not None:
                pitch_setter(sid, self.pitch)
        except Exception:
            pass


class _Snapshot(list):
    """Lista de pre-roll con la generación de sesión que la abrió."""

    def __init__(self, blocks, generation):
        super().__init__(blocks)
        self.generation = generation


class StreamKeeper:
    """Captura continua con pre-roll para el daemon.

    Mantiene UN InputStream abierto toda la vida del daemon. El callback
    alimenta la cola de la sesion y un deque con los ultimos ~0.5s: al pulsar,
    la sesion arranca con ese prefijo y el onset ya no depende de cuanto tarde
    en abrir el microfono (la causa del inicio recortado).
    Si el stream muere, live() es False y la sesion cae al one-shot de siempre.
    """

    PRE_ROLL = 0.45
    # Tope de seguridad para la cola de sesion: a ~100 bloques/s cubre
    # ~120s de dictado maximo. Fuera de sesion la cola queda vacia
    # (ver _cb): el tope solo protege una sesion patologica.
    MAX_QUEUE_BLOCKS = 15000
    # Tope del pre-roll por conteo ademas de por tiempo: en un loop sintetico
    # (o reloj grueso) miles de callbacks pueden caer dentro de la misma
    # ventana temporal; 256 bloques cubren de sobra 0.55s a cualquier blocksize.
    MAX_PRE_BLOCKS = 256
    # Duracion maxima de una sesion: corta grabaciones infinitas por
    # tecla atascada o flanco de release perdido.
    MAX_SESSION_SECONDS = 120.0

    def __init__(self, cfg):
        self.cfg = cfg
        self.q = queue.Queue()
        self.pre = collections.deque()
        self.stream = None
        self.mic = None
        self._lock = threading.Lock()
        self._recording = threading.Event()
        # Generación de sesión: cada snapshot/begin la incrementa. El
        # _pump viejo y su end_session usan la que capturaron al arrancar;
        # si ya hay una sesión nueva, el viejo no drena ni consume.
        self._session = 0

    def _cb(self, indata, frames_, t, status):
        if status:
            log.warning("audio status: %s", status)
        try:
            blk = indata.copy()
        except Exception:
            return
        now = time.monotonic()
        with self._lock:
            self.pre.append((now, blk))
            while self.pre and now - self.pre[0][0] > self.PRE_ROLL + 0.1:
                self.pre.popleft()
            while len(self.pre) > self.MAX_PRE_BLOCKS:
                self.pre.popleft()
            # El borde entre pre-roll y cola viva debe ser atómico con
            # snapshot(): si encolamos fuera del lock, un bloque puede caer
            # en ambas colecciones y repetirse al principio del dictado.
            # Fuera de sesión solo se conserva el pre-roll acotado.
            if self._recording.is_set():
                try:
                    self.q.put_nowait(blk)
                except queue.Full:
                    pass
                if self.q.qsize() > self.MAX_QUEUE_BLOCKS:
                    try:
                        while self.q.qsize() > self.MAX_QUEUE_BLOCKS:
                            self.q.get_nowait()
                    except queue.Empty:
                        pass

    def _begin_session_locked(self):
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        self._recording.set()
        self._session += 1
        return self._session

    def begin_session(self):
        """Abre la ventana de grabacion: drena restos y habilita la cola."""
        with self._lock:
            return self._begin_session_locked()

    def current_session(self):
        """Generación vigente (el _pump viejo no pisa a la sesión nueva)."""
        with self._lock:
            return self._session

    def end_session(self, generation=None):
        """Cierra la ventana de grabacion y descarta audio tardio.

        Con `generation`: solo drena si sigue vigente; si un re-press ya
        abrió otra sesión, el _pump viejo no tira el onset de la nueva.
        Sin `generation` drena siempre (compat con llamadores viejos).
        """
        with self._lock:
            if generation is not None and generation != self._session:
                return False
            self._recording.clear()
            try:
                while True:
                    self.q.get_nowait()
            except queue.Empty:
                pass
            return True

    def start(self):
        t0 = time.monotonic()
        mic = audio.resolve_mic(
            self.cfg.get("mic_hint", ""), self.cfg.get("mic_index"),
            strict_hint=True)
        if self.cfg.get("mic_hint") and mic is None:
            raise RuntimeError(
                f"Configured microphone '{self.cfg['mic_hint']}' is no longer available")
        if self.cfg.get("mic_index") is not None and mic is None:
            raise RuntimeError(
                "Configured microphone index "
                f"'{self.cfg['mic_index']}' is no longer available")
        stream, mic = audio.open_input_stream(mic, callback=self._cb)
        self.stream = stream
        self.mic = mic
        log.info("captura continua lista en %.2fs (pre-roll %.2fs).",
                 time.monotonic() - t0, self.PRE_ROLL)
        return True

    def live(self):
        s = self.stream
        if s is None:
            return False
        try:
            return bool(s.active)
        except Exception:
            return False

    def snapshot(self, seconds):
        """Prefijo pre-roll + drena la cola (audio previo entre sesiones).

        Ventana <= 0 devuelve vacío siempre: con relojes gruesos (Windows
        ~15 ms) `now - t` puede dar exactamente 0.0 y un `<=` ingenuo
        resucitaría bloques que ya se drenaron.
        Abre ademas la ventana de grabacion: desde aqui el callback vuelve
        a encolar hasta end_session().
        Devuelve lista con `.generation`: el _record guarda la generación y
        su end_session solo drena si sigue vigente (re-press rápido). Sigue
        siendo lista (len/iter/==[]) para no romper llamadores viejos.
        """
        with self._lock:
            generation = self._begin_session_locked()
            if seconds <= 0:
                return _Snapshot([], generation)
            now = time.monotonic()
            items = list(self.pre)
        return _Snapshot(
            [blk for t, blk in items if now - t <= seconds], generation)

    def take(self, timeout=0.1):
        return self.q.get(timeout=timeout)

    def take_for(self, generation, timeout=0.1):
        """Take solo para la generación dada: si un re-press ya abrió otra
        sesión, el bloque (que es de la sesión nueva) se devuelve al FRENTE
        de la cola y se levanta queue.Empty. Sin esto el _pump viejo roba el
        onset de la sesión nueva aunque ya estuviera bloqueado en take.
        """
        blk = self.q.get(timeout=timeout)
        if generation == self.current_session():
            return blk
        with self.q.mutex:
            self.q.queue.appendleft(blk)
        try:
            self.q.task_done()
        except ValueError:
            pass
        raise queue.Empty()

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()


class Daemon:
    def __init__(self, cfg, data_dir=None):
        self.cfg = cfg
        self.engine = Engine(data_dir,
                             threads=cfg.get("threads", 4),
                             max_seg=cfg.get("max_seg", 20.0),
                             vad_sil=cfg.get("vad_sil", 0.5),
                             vad_pad=cfg.get("vad_pad", 0.3),
                             min_dur=cfg.get("min_dur", 0.4),
                             vad_model=cfg.get("vad_model", "silero"),
                             blank_penalty=cfg.get("blank_penalty", 0.0),
                             save_wavs_dir=os.environ.get("DICTADO_SAVE_WAVS", "") or None)
        self.overlay = Overlay(
            hotkey.key_label(cfg.get("key", "f9")),
            style=cfg.get("overlay_style", "orbital")
            if cfg.get("overlay_style") in ("classic", "orbital") else "orbital")
        self.mic = audio.resolve_mic(
            cfg.get("mic_hint", ""), cfg.get("mic_index"),
            strict_hint=bool(cfg.get("mic_hint")))
        self.hk = hotkey.create(cfg.get("key", "f9"))
        self.rec = {"sid": 0, "grabando": False, "frames": [], "t_start": 0.0,
                    "done": None, "busy": False}
        self.lock = threading.Lock()
        self._shutdown = threading.Event()
        self.tray = TrayIcon(self._shutdown)
        self.keeper = None

    def beep(self, freq=880, ms=120):
        if not self.cfg.get("sound"):
            return
        try:
            import sys
            if sys.platform == "win32":
                import winsound
                winsound.Beep(freq, ms)
            else:
                print("\a", end="", flush=True)
        except Exception as e:
            log.warning("beep fail: %s", e)

    # ---- sesion ----
    def _record(self, sid, done):
        keeper = getattr(self, "keeper", None)
        if keeper is not None and keeper.live():
            self._record_continuous(sid, done, keeper)
        else:
            self._record_oneshot(sid, done)

    def _pump(self, sid, next_block, frames, tracker):
        """Bucle comun de sesion: junta bloques, anima el overlay y corta al
        soltar, con flush de 0.3s para lo que quedo en buffers.

        next_block(timeout) entrega un bloque o levanta queue.Empty.
        Corta ademas por duracion maxima para que una tecla atascada no
        acumule bloques sin fin.
        Si al salir la sesión ya fue superada (rec[sid] != sid: re-press
        rápido abrió otra), salta el flush post-release: la cola del keeper
        es compartida y el flush viejo robaría el onset de la sesión nueva.
        """
        t_start = time.monotonic()
        try:
            while True:
                if time.monotonic() - t_start > StreamKeeper.MAX_SESSION_SECONDS:
                    log.warning("sesion %d supera %.0fs, corto por seguridad.",
                                sid, StreamKeeper.MAX_SESSION_SECONDS)
                    with self.lock:
                        if self.rec["sid"] == sid:
                            self.rec["grabando"] = False
                    break
                try:
                    blk = next_block(0.1)
                except queue.Empty:
                    with self.lock:
                        alive = self.rec["grabando"] and self.rec["sid"] == sid
                    if not alive:
                        break
                    continue
                frames.append(blk)
                tracker.add(blk)
                tracker.tick()
                with self.lock:
                    alive = self.rec["grabando"] and self.rec["sid"] == sid
                if not alive:
                    with self.lock:
                        superseded = self.rec["sid"] != sid
                    if superseded:
                        break
                    try:
                        t_end = time.monotonic() + 0.3
                        while True:
                            try:
                                extra = next_block(0.0)
                            except _StreamDied:
                                raise
                            except queue.Empty:
                                if time.monotonic() >= t_end:
                                    break
                                time.sleep(0.02)
                                continue
                            frames.append(extra)
                            tracker.add(extra)
                    except _StreamDied:
                        raise
                    except Exception:
                        pass
                    break
        except _StreamDied:
            raise
        except Exception:
            log.exception("sesion %d fail en loop", sid)
        return frames

    def _record_continuous(self, sid, done, keeper):
        frames = keeper.snapshot(StreamKeeper.PRE_ROLL)
        generation = getattr(frames, "generation", keeper.current_session())
        if not keeper.live():
            log.error("captura continua muerta al grabar sesion %d.", sid)
            with self.lock:
                if self.rec["sid"] == sid:
                    self.rec["grabando"] = False
            # El pre-roll es audio viejo de antes de que muriera el keeper:
            # transcribirlo pega texto rancio sin que el usuario hablara.
            self.overlay.show_error_for(
                sid,
                "Se perdió el micrófono. Revisá conexión y que otra app no lo esté usando.",
                "#f87171", 6000)
            try:
                keeper.end_session(generation)
            except Exception:
                pass
            done.set()
            return
        try:
            tracker = _LevelTracker(sid, self.overlay)
            for blk in frames:
                tracker.add(blk)
            tracker.tick()
            log.info("sesion %d con pre-roll %d bloques (captura continua).",
                     sid, len(frames))
            try:
                self.overlay.listening(sid)
            except Exception:
                log.exception("overlay listening fail sesion %d", sid)
                try:
                    keeper.end_session(generation)
                except Exception:
                    pass
                with self.lock:
                    if self.rec["sid"] == sid:
                        self.rec["grabando"] = False
                        self.rec["frames"] = []
                done.set()
                return
            def next_block(timeout):
                if not keeper.live():
                    raise _StreamDied()
                # El pump viejo no roba el onset de la sesión nueva: take_for
                # devuelve al frente lo que no sea de su generación (B1).
                return keeper.take_for(generation, timeout)
            try:
                self._pump(sid, next_block, frames, tracker)
            except _StreamDied:
                log.warning("sesion %d: stream muerto, transcribo lo juntado.", sid)
        finally:
            try:
                keeper.end_session(generation)
            except Exception:
                pass
        with self.lock:
            if self.rec["sid"] == sid:
                self.rec["frames"] = frames
        done.set()

    def _record_oneshot(self, sid, done):
        # Cola acotada como la del keeper: con la tecla atascada el callback
        # de PortAudio sigue empujando aunque ya no consumamos.
        q = queue.Queue(maxsize=StreamKeeper.MAX_QUEUE_BLOCKS)
        frames = []

        def cb(indata, frames_, t, status):
            if status:
                log.warning("audio status: %s", status)
            try:
                blk = indata.copy()
            except Exception:
                return
            try:
                q.put_nowait(blk)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(blk)
                except queue.Full:
                    pass

        try:
            t_mic = time.monotonic()
            mic = audio.resolve_mic(
                self.cfg.get("mic_hint", ""), self.cfg.get("mic_index"),
                strict_hint=True)
            if self.cfg.get("mic_hint") and mic is None:
                raise RuntimeError(
                    f"Configured microphone '{self.cfg['mic_hint']}' is no longer available")
            if self.cfg.get("mic_index") is not None and mic is None:
                raise RuntimeError(
                    "Configured microphone index "
                    f"'{self.cfg['mic_index']}' is no longer available")
            stream, mic = audio.open_input_stream(mic, callback=cb)
            log.info("mic listo en %.2fs (sesion %d).", time.monotonic() - t_mic, sid)
        except Exception:
            log.exception("ERROR abriendo microfono")
            with self.lock:
                if self.rec["sid"] == sid:
                    self.rec["grabando"] = False
            self.overlay.show_error_for(
                sid,
                "No se pudo iniciar el micrófono. Revisá conexión, permiso del sistema y "
                "que otra app no lo esté usando. El log tiene el error detallado.",
                "#f87171", 6000)
            done.set()
            return
        log.info("grabando sesion %d...", sid)
        tracker = _LevelTracker(sid, self.overlay)
        try:
            self.overlay.listening(sid)
        except Exception:
            log.exception("overlay listening fail sesion %d", sid)
            with self.lock:
                if self.rec["sid"] == sid:
                    self.rec["grabando"] = False
            try:
                stream.stop()
            except Exception:
                log.exception("deteniendo stream audio")
            try:
                stream.close()
            except Exception:
                log.exception("cerrando stream audio")
            done.set()
            return
        # Mismo bucle acotado que la via continua: corta a
        # MAX_SESSION_SECONDS aunque se pierda el flanco de release.
        # OJO: Queue.get(block, timeout): el timeout va por nombre.
        def next_block(timeout, _q=q):
            return _q.get(timeout=timeout)
        try:
            self._pump(sid, next_block, frames, tracker)
        except Exception:
            log.exception("sesion %d fail en loop", sid)
        finally:
            try:
                stream.stop()
            except Exception:
                log.exception("deteniendo stream audio")
            try:
                stream.close()
            except Exception:
                log.exception("cerrando stream audio")
        with self.lock:
            if self.rec["sid"] == sid:
                self.rec["frames"] = frames
        done.set()

    def _job(self, wav, dur, sid):
        try:
            if dur < self.engine.min_dur:
                log.info("muy corto (<%.1fs), descarto.", self.engine.min_dur)
                key = hotkey.key_label(self.cfg.get("key", "f9"))
                self.overlay.show_notice_for(
                    sid, f"Dictado muy corto — mantené {key} un poco más.")
                return
            peak = float(np.max(np.abs(wav))) if wav.size else 0.0
            if peak < SILENCE_PEAK:
                log.info("silencio (pico %.4f), descarto sin inferencia.", peak)
                self.overlay.show_notice_for(sid, "No detecté voz.")
                return
            t0 = time.time()
            text = self.engine.transcribe(wav)
            conf = float(getattr(self.engine, "last_conf", 1.0) or 1.0)
            text = bias.correct_biased(
                text, conf=conf,
                word_confs=getattr(self.engine, "last_word_confs", None))
            text = llm.maybe_polish(text, self.cfg, conf=conf)
            dt = time.time() - t0
            if not text:
                log.info("vacio tras %.1fs audio (%.2fs), nada que pegar.", dur, dt)
                self.overlay.show_notice_for(
                    sid,
                    "No pude reconocer el audio. Probá hablar más cerca del micrófono.",
                    milliseconds=2600)
                return
            log.info("[%.1fs audio -> %.2fs, RTF=%.2fx] transcripción: %d caracteres",
                     dur, dt, dt / max(dur, 0.1), len(text))
            import instant_app.paste as _paste_mod
            try:
                _paste_mod.paste(text + " ")
            except Exception as e:
                # C2: el mensaje accionable de paste (Wayland sin teclas,
                # falta xclip/xdotool...) llegaba tapado por el genérico.
                # Se surfea tal cual: ya dice qué instalar y qué hacer.
                detail = str(e).strip()
                log.exception("ERROR pegando sesion %d", sid)
                self.overlay.show_error_for(
                    sid, detail or "No se pudo pegar el texto. Abrí Diagnóstico.",
                    milliseconds=6000)
                return
            if float(getattr(self.engine, "last_clip", 0.0) or 0.0) > 0.02:
                self.overlay.show_notice_for(
                    sid, "El micrófono satura: bajá la ganancia o alejate un poco.")
            else:
                self.overlay.success_for(sid)
        except Exception:
            log.exception("ERROR transcripcion sesion %d", sid)
            self.overlay.show_error_for(
                sid, "No se pudo completar el dictado. Abrí Diagnóstico.",
                milliseconds=3000)
        finally:
            with self.lock:
                self.rec["busy"] = False
            self.beep(freq=440)

    def on_press(self):
        with self.lock:
            if self.rec["grabando"]:
                return
            if self.rec["busy"]:
                log.info("ocupado transcribiendo, ignoro.")
                return
            self.rec["sid"] += 1
            self.rec["grabando"] = True
            self.rec["frames"] = []
            self.rec["t_start"] = time.time()
            done = threading.Event()
            self.rec["done"] = done
            sid = self.rec["sid"]
        self.overlay.starting(sid)
        threading.Thread(target=self._record, args=(sid, done), daemon=True).start()

    def on_release(self):
        with self.lock:
            if not self.rec["grabando"]:
                return
            self.rec["grabando"] = False
            dur = time.time() - self.rec["t_start"]
            done = self.rec["done"]
            sid = self.rec["sid"]
        self.overlay.processing(sid)
        # El wait + concatenate + to_mono NO pueden correr en el hilo del
        # hotkey (polling GetAsyncKeyState): lo dejaban ciego hasta 10s y
        # se perdia el siguiente flanco. Se deriva a un worker.
        threading.Thread(target=self._finish_release, args=(sid, done, dur),
                         daemon=True).start()

    def _finish_release(self, sid, done, dur):
        if done is not None and not done.wait(timeout=10.0):
            log.warning("sesion %d no cerro mic en 10s, transcribo lo que hay.", sid)
        with self.lock:
            if self.rec["sid"] != sid or self.rec["busy"]:
                # Release viejo (o solape con un press nuevo): no robar frames
                # de la sesion vigente ni marcar busy ajeno.
                log.warning("sesion %d vieja al soltar (actual %d), descarto release.",
                            sid, self.rec["sid"])
                return
            frames = self.rec["frames"]
            self.rec["frames"] = []
            self.rec["busy"] = True
        try:
            if frames:
                try:
                    wav = audio.to_mono(np.concatenate(frames, axis=0))
                except (ValueError, MemoryError):
                    log.exception("sesion %d: audio corrupto o gigante, descarto.", sid)
                    self.overlay.show_error_for(
                        sid, "El audio salió corrupto o muy largo; lo descarté. "
                        "Probá un dictado más corto.",
                        milliseconds=3000)
                    with self.lock:
                        self.rec["busy"] = False
                    return
            else:
                wav = np.zeros(0, dtype=np.float32)
        except Exception:
            log.exception("sesion %d: no pude armar el wav.", sid)
            self.overlay.show_error_for(
                sid, "No se pudo completar el dictado. Abrí Diagnóstico.",
                milliseconds=3000)
            with self.lock:
                self.rec["busy"] = False
            return
        finally:
            # Libera la referencia pesada cuanto antes; _job recibe su copia.
            del frames
        log.info("soltado tras %.1fs, %d bloques -> VAD+VoxCore.", dur,
                 len(wav) // 160 if wav.size else 0)
        threading.Thread(target=self._job, args=(wav, dur, sid), daemon=True).start()

    def run(self):
        write_pid()
        try:
            if self.cfg.get("mic_hint") and self.mic is None:
                log.error("micrófono configurado no encontrado; no pruebo otro default.")
            elif self.cfg.get("mic_index") is not None and self.mic is None:
                log.error("mic_index %r vencido; no caigo al default en silencio.",
                          self.cfg.get("mic_index"))
            elif not audio.probe(self.mic):
                log.warning("mic probe FAIL; revisá conexión, permisos y uso por otra app.")
            self.engine.recognizer()
            self.engine.vad()
            self.keeper = StreamKeeper(self.cfg)
            try:
                self.keeper.start()
            except Exception:
                log.warning("captura continua no disponible; sigo con one-shot por sesion.",
                            exc_info=True)
                self.keeper = None
            try:
                self.tray.start()
            except Exception:
                log.exception("no pude iniciar el icono de bandeja")
            key = hotkey.key_label(self.cfg.get("key", "f9"))
            log.info("listo. Manten %s para dictar (60-120s), suelta para transcribir. Ctrl+C sale.", key)
            set_hotkey_error = getattr(self.hk, "set_error_callback", None)
            if set_hotkey_error is not None:
                set_hotkey_error(lambda message: self.overlay.show_error_for(
                    0, message, "#f87171", 10000))
            set_hotkey_ready = getattr(self.hk, "set_ready_callback", None)
            if set_hotkey_ready is not None:
                set_hotkey_ready(lambda message: self.overlay.show_notice_for(
                    0, message, "#fbbf24", 10000))
            self.hk.start(self.on_press, self.on_release)
            if not self.overlay.run_event_loop(
                    self._shutdown, lambda: log.info("vivo, esperando %s...", key)):
                while not self._shutdown.wait(60):
                    log.info("vivo, esperando %s...", key)
        except KeyboardInterrupt:
            log.info("chau.")
        finally:
            self._shutdown.set()
            keeper, self.keeper = getattr(self, "keeper", None), None
            if keeper is not None:
                try:
                    keeper.stop()
                except Exception:
                    pass
            self.overlay.hide()
            try:
                self.hk.stop()
            except Exception:
                pass
            self.tray.stop()
            clear_pid()


def cmd_check(cfg):
    """Boot rapido sin colgarse: valida tecla, mic y warmup de modelos."""
    t0 = time.time()
    key = cfg.get("key", "f9")
    try:
        hk = hotkey.create(key)
        hk.stop()
    except (ValueError, RuntimeError) as e:
        print(f"  tecla: FAIL ({e}). Opciones: {', '.join(hotkey.available_keys())}")
        return 2
    print(f"  tecla: {hotkey.key_label(key)} OK")
    hint = cfg.get("mic_hint", "")
    mic = audio.resolve_mic(hint, cfg.get("mic_index"), strict_hint=bool(hint))
    if hint and mic is None:
        ok = False
        print("  mic callback probe: FAIL (micrófono guardado no encontrado; "
              "reconectalo o elegí otro en Configuración)")
    else:
        ok = audio.probe(mic)
        print("  mic callback probe: "
              f"{'OK' if ok else 'FAIL (revisa conexión, permisos y uso por otra app)'}")
    try:
        eng = Engine(threads=cfg.get("threads", 4), max_seg=cfg.get("max_seg", 20.0))
        eng.recognizer()
        eng.vad()
    except FileNotFoundError as e:
        print(f"  SIN MODELOS: {e}")
        return 2
    dt = time.time() - t0
    print(f"  boot mic+warmup: {dt:.1f}s ({'OK <5s' if dt < 5 else 'LENTO >5s'})")
    return 0 if (ok and dt < 5) else 2
