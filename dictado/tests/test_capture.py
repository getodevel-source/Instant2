"""Captura continua: pre-roll, tracker de nivel, pump y guardado de fallos."""
import os
import queue
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import daemon as daemon_module
from instant2_app.engine import Engine, SAMPLE_RATE


def _check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


# --- StreamKeeper sin mic real: se alimenta el callback a mano. ---
# Fuera de sesion solo se conserva pre-roll (cola vacia): evita que q crezca
# sin limite entre dictados (freeze). En sesion si encola hasta end_session.
keeper = daemon_module.StreamKeeper({})
blk = np.full((160, 1), 0.05, dtype=np.float32)
for _ in range(3):
    keeper._cb(blk, 160, None, None)
_check("fuera de sesion no encola", keeper.q.qsize() == 0)
_check("pre-roll conserva 3", len(keeper.snapshot(10.0)) == 3)
keeper.end_session()
keeper.begin_session()
for _ in range(3):
    keeper._cb(blk, 160, None, None)
_check("en sesion encola bloques", keeper.q.qsize() == 3)
got = keeper.snapshot(10.0)
_check("pre-roll fusiona historial y cola sin repetir", len(got) == 6
       and keeper.q.qsize() == 0)
_check("ventana 0 no trae nada", keeper.snapshot(0.0) == [])
keeper.end_session()
_check("fin de sesion drena cola", keeper.q.qsize() == 0)
_check("sin stream no esta vivo", keeper.live() is False)


class _FakeStream:
    def __init__(self, active=True):
        self.active = active


keeper.stream = _FakeStream(True)
_check("stream activo vive", keeper.live() is True)
keeper.stream = _FakeStream(False)
_check("stream caido no vive", keeper.live() is False)
keeper.stream = None
keeper.begin_session()
_check("take entrega lo encolado",
       (keeper._cb(blk, 160, None, None), keeper.take(timeout=1.0))[1] is not None)
keeper.end_session()


# --- _LevelTracker: matematica viva sin Qt. ---
class _DummyOverlay:
    def __init__(self):
        self.levels = []
        self.bands = []

    def set_level(self, sid, value):
        self.levels.append((sid, value))

    def set_bands(self, sid, values):
        self.bands.append(list(values))

    def set_pitch(self, sid, value):
        pass


overlay = _DummyOverlay()
tracker = daemon_module._LevelTracker(7, overlay)
tone = (np.sin(2 * np.pi * 220 * np.arange(960) / 16000) * 0.1).astype(np.float32)
for _ in range(7):
    tracker.add(tone.reshape(-1, 1))
tracker.tick()
time.sleep(0.06)
tracker.tick()
_check("tracker reporta nivel con voz", len(overlay.levels) >= 1
        and all(sid == 7 for sid, _v in overlay.levels))
_check("tracker nivel en rango", all(0.0 <= v <= 1.0 for _s, v in overlay.levels))
_check("tracker 9 bandas", overlay.bands and len(overlay.bands[-1]) == 9)


# --- _pump: junta bloques y corta al soltar, sin mic. ---
daemon = daemon_module.Daemon.__new__(daemon_module.Daemon)
daemon.lock = daemon_module.threading.Lock()
daemon.rec = {"sid": 5, "grabando": True, "frames": [], "t_start": 0.0,
              "done": None, "busy": False}
blocks = [np.zeros((160, 1), dtype=np.float32) for _ in range(3)]
pending = list(blocks)


def _next(timeout):
    if pending:
        return pending.pop(0)
    with daemon.lock:
        daemon.rec["grabando"] = False
    raise queue.Empty()


frames = daemon._pump(5, _next, [], daemon_module._LevelTracker(5, overlay))
_check("pump junta y corta al soltar", len(frames) == 3)


def _dying(_timeout):
    raise daemon_module._StreamDied()


try:
    daemon._pump(5, _dying, [], daemon_module._LevelTracker(5, overlay))
    _check("pump propaga stream muerto", False)
except daemon_module._StreamDied:
    _check("pump propaga stream muerto", True)


# --- _dump_failure: el vacio con save dir deja wav + meta. ---
class _NullStream:
    def __init__(self):
        self.result = type("R", (), {"text": ""})

    def accept_waveform(self, _sr, _wav):
        pass


class _NullRec:
    def create_stream(self):
        return _NullStream()

    def decode_stream(self, _s):
        pass


saved = tempfile.mkdtemp()
eng = Engine(data_dir=tempfile.mkdtemp(), save_wavs_dir=saved)
eng.segment = lambda _wav: [(0.0, 1.0)]
eng._rec = _NullRec()
silencio = np.zeros(2 * SAMPLE_RATE, dtype=np.float32)
_check("vacio devuelve vacio", eng.transcribe(silencio) == "")
wavs = [f for f in os.listdir(saved) if f.endswith(".wav")]
metas = [f for f in os.listdir(saved) if f.endswith(".txt")]
_check("fallo guarda wav+meta", len(wavs) == 1 and len(metas) == 1)

print("OK: captura continua verde.")
