"""Regresion ciclo audio/hotkey: one-shot acotado, solape, restart, mic rancio, retry fraccionario."""
import os
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import daemon as daemon_module
from instant2_app import hotkey as hotkey_module
from instant2_app.engine import Engine, SAMPLE_RATE


def _check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


class _DummyOverlay:
    def __init__(self):
        self.errors = []
        self.listening_calls = []

    def listening(self, sid):
        self.listening_calls.append(sid)
        return True

    def show_error_for(self, sid, text, color="#f87171", milliseconds=3000):
        self.errors.append((sid, text))
        return True

    def show_notice_for(self, *a, **k):
        return True

    def success_for(self, *a, **k):
        return True

    def starting(self, *a, **k):
        return True

    def processing(self, *a, **k):
        return True

    def set_level(self, *a, **k):
        return True

    def set_bands(self, *a, **k):
        return True

    def set_pitch(self, *a, **k):
        return True


def _daemon(**kw):
    d = daemon_module.Daemon.__new__(daemon_module.Daemon)
    d.lock = threading.Lock()
    d.rec = {"sid": 0, "grabando": False, "frames": [], "t_start": 0.0,
             "done": None, "busy": False}
    d.overlay = _DummyOverlay()
    d.cfg = {"key": "f9"}
    d.beep = lambda **_: None
    for k, v in kw.items():
        setattr(d, k, v)
    return d


# 1. P0 one-shot largo: tecla atascada corta a MAX_SESSION_SECONDS.
# Replica el bug auditado: _record_oneshot con `while True` sin tope grababa
# infinito. Se prueba el metodo real con mic simulado y release perdido.
d = _daemon()
d.cfg = {}
d.rec.update(sid=1, grabando=True)
blk = np.zeros((160, 1), dtype=np.float32)


class _StuckStream:
    def stop(self):
        pass

    def close(self):
        pass


old_max = daemon_module.StreamKeeper.MAX_SESSION_SECONDS
daemon_module.StreamKeeper.MAX_SESSION_SECONDS = 0.3
try:
    with patch("instant2_app.audio.resolve_mic", return_value=None), \
            patch("instant2_app.audio.open_input_stream",
                  return_value=(_StuckStream(), None)):
        done = threading.Event()

        def _stuck():
            d._record_oneshot(1, done)

        t = threading.Thread(target=_stuck, daemon=True)
        t0 = time.monotonic()
        t.start()
        # Release perdido: grabando queda True; el tope debe cortar igual.
        closed = done.wait(timeout=10.0)
        dt = time.monotonic() - t0
finally:
    daemon_module.StreamKeeper.MAX_SESSION_SECONDS = old_max
_check("one-shot largo corta por tope",
       closed and dt < 5.0 and d.rec["grabando"] is False)

# 2. Solape: release viejo no roba frames de la sesion nueva.
d = _daemon()
d.rec.update(sid=2, grabando=False, frames=[np.zeros(4, dtype=np.float32)], busy=False)
done = threading.Event()
done.set()
d._finish_release(1, done, 0.5)
_check("release viejo no roba frames",
       len(d.rec["frames"]) == 1 and d.rec["busy"] is False)

# 3. Restart hotkey: start() tras stop() re-arma (clear + _down reseteado).
calls = []
hk = hotkey_module.PynputHotkey.__new__(hotkey_module.PynputHotkey)
hk._pk = type("PK", (), {"Listener": None, "Key": type("K", (), {"f9": object()})})
hk._target = object()
hk._norm = lambda key: True
hk._down = True
hk._suppress = False
hk._listener = None
started = []


class _L:
    def start(self):
        started.append(True)

    def stop(self):
        pass

    def is_alive(self):
        return False


hk._pk.Listener = lambda **kw: (_L(), kw.update({"_p": kw.get("on_press"), "_r": kw.get("on_release")}) or _L())[0]
hk.start(lambda: calls.append("p"), lambda: calls.append("r"))
_check("restart resetea _down", hk._down is False and started)

# 3b. WindowsPolling.start() limpia _stop (restart tras stop()).
wp = hotkey_module.WindowsPolling.__new__(hotkey_module.WindowsPolling)
wp.vk = 0x78
wp._user32 = None
wp._stop = threading.Event()
wp._stop.set()
wp._t = None
wp.is_down = lambda: False
wp.start(lambda: None, lambda: None, interval=0.005)
alive = wp._t is not None and wp._t.is_alive() and not wp._stop.is_set()
wp.stop()
_check("polling restart limpia _stop", alive)

# 3c. Callbacks con excepcion no matan el hilo de polling.
wp2 = hotkey_module.WindowsPolling.__new__(hotkey_module.WindowsPolling)
wp2.vk = 0x78
wp2._user32 = None
wp2._stop = threading.Event()
wp2._t = None
seq = iter([True, True, False])
wp2.is_down = lambda: next(seq, False)


def _boom():
    raise RuntimeError("press roto")


wp2.start(_boom, lambda: None, interval=0.005)
time.sleep(0.15)
survives = wp2._t.is_alive()
wp2.stop()
_check("excepcion en callback no mata polling", survives)

# 4. Keeper muerto + pre-roll rancio: error 'microfono perdido', sin transcribir.
d = _daemon()
keeper = daemon_module.StreamKeeper({})
keeper._cb(np.full((160, 1), 0.05, dtype=np.float32), 160, None, None)
keeper.stream = None  # muerto
done = threading.Event()
d._record_continuous(9, done, keeper)
_check("keeper muerto avisa microfono perdido",
       done.is_set() and any("micrófono" in t for _, t in d.overlay.errors)
       and d.rec["frames"] == [] and not d.overlay.listening_calls)

# 5. mic_index vencido: keeper.start() y _record_oneshot no caen al default.
with patch("instant2_app.audio.resolve_mic", return_value=None), \
        patch("instant2_app.audio.open_input_stream") as opener:
    k = daemon_module.StreamKeeper({"mic_index": 7})
    try:
        k.start()
        _check("keeper mic_index vencido levanta", False)
    except RuntimeError as e:
        _check("keeper mic_index vencido levanta", "index" in str(e))
    _check("keeper no abre default", not opener.called)

d = _daemon()
d.cfg = {"mic_index": 7}
d.rec.update(sid=3, grabando=True)
done = threading.Event()
with patch("instant2_app.audio.resolve_mic", return_value=None), \
        patch("instant2_app.audio.open_input_stream") as opener:
    d._record_oneshot(3, done)
_check("oneshot mic_index vencido no abre default",
       done.is_set() and not opener.called and d.rec["grabando"] is False
       and any("micrófono" in t for _, t in d.overlay.errors))

# 6. Duracion fraccionaria: el retry total usa int(capped*SR), no int(capped)*SR.
seen = {}


class _S:
    def __init__(self, decide):
        self._decide = decide
        self._wav = None

    def accept_waveform(self, _sr, wav):
        self._wav = np.asarray(wav, dtype=np.float32)


class _R:
    calls = 0

    def __init__(self, decide):
        self._decide = decide

    def create_stream(self):
        _R.calls += 1
        return _S(self._decide)

    def decode_stream(self, s):
        seen["n"] = len(s._wav)
        # Solo el retry total (2.5s capados) entiende el audio: los intentos
        # cortos (chunk 1s, retry +-1s de 2s) devuelven vacio.
        s.result = type("R", (), {"text": self._decide(s._wav)})


eng = Engine(data_dir=tempfile.mkdtemp(), threads=1)
eng.segment = lambda _wav: [(0.0, 1.0)]
eng._rec = _R(lambda w: "ok" if len(w) >= int(2.5 * SAMPLE_RATE) else "")
eng.FULL_RETRY_MAX_SECONDS = 2.5
wav = (np.sin(2 * np.pi * 220 * np.arange(int(2.7 * SAMPLE_RATE)) / SAMPLE_RATE)
       * 0.05).astype(np.float32)
_check("retry fraccionario llega al total", eng.transcribe(wav) == "ok")
_check("retry fraccionario conserva muestras", seen.get("n") == int(2.5 * SAMPLE_RATE))

# 7. Engine recibe vad_sil/min_dur del config (via Daemon.__init__ path).
with patch("instant2_app.daemon.Engine") as eng_cls, \
        patch("instant2_app.daemon.Overlay"), \
        patch("instant2_app.audio.resolve_mic", return_value=None), \
        patch("instant2_app.hotkey.create"):
    from instant2_app.daemon import Daemon as _D
    _D({"vad_sil": 0.9, "min_dur": 1.2, "vad_pad": 0.1})
_check("daemon pasa vad_sil/min_dur/vad_pad",
       eng_cls.call_args.kwargs.get("vad_sil") == 0.9
       and eng_cls.call_args.kwargs.get("min_dur") == 1.2
       and eng_cls.call_args.kwargs.get("vad_pad") == 0.1)

# 8. Flush no traga _StreamDied (muerte durante el flush post-release).
d = _daemon()
d.rec.update(sid=4, grabando=True)
state = {"n": 0}


def _flaky(timeout):
    state["n"] += 1
    if state["n"] <= 1:
        return blk
    if state["n"] == 2:
        with d.lock:
            d.rec["grabando"] = False
        return blk
    raise daemon_module._StreamDied()


try:
    d._pump(4, _flaky, [], daemon_module._LevelTracker(4, d.overlay))
    _check("flush propaga stream muerto", False)
except daemon_module._StreamDied:
    _check("flush propaga stream muerto", True)

# 9. B1: re-press rápido (<0.3s) no pierde el onset de la sesión nueva.
# El _pump viejo no hace flush sobre la cola compartida (superseded) y su
# end_session(generation) no drena lo que la sesión nueva ya encoló.
d = _daemon()
k = daemon_module.StreamKeeper({})
k.stream = type("S", (), {"active": True})()
d.keeper = k
d.rec.update(sid=1, grabando=True)
done1 = threading.Event()
t1 = threading.Thread(target=d._record_continuous, args=(1, done1, k))
t1.start()
time.sleep(0.15)
# Re-press rápido: release(1) + press(2) casi juntos.
with d.lock:
    d.rec["grabando"] = False
with d.lock:
    d.rec["sid"] = 2
    d.rec["grabando"] = True
    done2 = threading.Event()
    d.rec["done"] = done2
new_frames = k.snapshot(daemon_module.StreamKeeper.PRE_ROLL)
new_gen = new_frames.generation
# El onset de la sesión nueva llega justo tras el re-press.
onset = np.full((160, 1), 0.07, dtype=np.float32)
k._cb(onset, 160, None, None)
# El pump viejo termina (su next_block ve generación nueva y corta);
# su end_session vieja NO debe drenar el onset.
t1.join(timeout=5.0)
_check("pump viejo termina en re-press", not t1.is_alive() and done1.is_set())
_check("end_session vieja no drena onset",
       k.current_session() == new_gen and k.q.qsize() == 1)
# Y la sesión nueva sí lo puede tomar.
_check("sesion nueva conserva onset", k.take(timeout=1.0) is not None)
k.end_session(new_gen)
with d.lock:
    d.rec["grabando"] = False

# 9b. end_session(generation) vieja es no-op; la vigente sí drena.
k2 = daemon_module.StreamKeeper({})
k2.begin_session()
k2._cb(blk, 160, None, None)
_check("end vieja no drena", k2.end_session(9999) is False and k2.q.qsize() == 1)
_check("end vigente drena", k2.end_session(k2.current_session()) is True
       and k2.q.qsize() == 0)

# 10. C2: el error accionable de paste llega al overlay (no el genérico).
d = _daemon()
d.engine = Engine(data_dir=tempfile.mkdtemp(), threads=1)
d.engine.min_dur = 0.4
wav = (np.sin(2 * np.pi * 220 * np.arange(int(1.0 * SAMPLE_RATE)) / SAMPLE_RATE)
       * 0.05).astype(np.float32)
d.engine.transcribe = lambda _w: "hola"
with patch("instant2_app.paste.paste",
           side_effect=RuntimeError("en Wayland no hay inyeccion: instala xclip")):
    d._job(wav, 1.0, 77)
_check("paste Wayland surfea mensaje",
       any("xclip" in t for _, t in d.overlay.errors))

# 11. C1: Tk set_level a cero corta la cola (asigna, no max).
from instant2_app.overlay import _TkOverlay
tk = _TkOverlay.__new__(_TkOverlay)
tk._dispatch_lock = threading.Lock()
tk._level = 0.8
tk.set_level(3, 0.0)
_check("tk set_level cero asigna", tk._level == 0.0)
tk.set_level(3, 0.4)
tk.set_level(3, 0.2)
_check("tk set_level coalescea max subiendo", abs(tk._level - 0.4) < 1e-9)

print("OK: regresion audio/hotkey verde.")
