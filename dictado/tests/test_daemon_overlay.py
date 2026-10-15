"""Daemon y overlay: progreso por sesion y feedback consumidor-visible."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app.engine import join_texts, merge_short_bounds


def _check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


# Overlay progression is ordered by session/phase, with errors preempting completion.
from instant2_app import daemon as daemon_module
from instant2_app.overlay import OverlayTransitions


_states = OverlayTransitions()
_check("overlay start to listening to processing",
       _states.accept(1, "starting") < _states.accept(1, "listening")
       < _states.accept(1, "processing"))
_check("late listening cannot replace processing",
       _states.accept(1, "listening") is None)
_check("success appears after processing", _states.accept(1, "success") is not None)
_check("error replaces success and stays terminal",
       _states.accept(1, "error") is not None
       and _states.accept(1, "success") is None)
_check("new press starts a new overlay session",
       _states.accept(2, "starting") is not None)


class _OverlaySequence:
    def __init__(self):
        self.events = []
        self.transitions = OverlayTransitions()

    def _append(self, session, state, *detail):
        if self.transitions.accept(session, state) is None:
            return
        self.events.append((state, session) + detail)

    def starting(self, session):
        self._append(session, "starting")

    def listening(self, session):
        self._append(session, "listening")

    def processing(self, session):
        self._append(session, "processing")

    def success_for(self, session, milliseconds=760):
        self._append(session, "success", milliseconds)

    def show_notice_for(self, session, text, color="#f2c36a", milliseconds=2000):
        self._append(session, "notice", text, milliseconds)

    def show_error_for(self, session, text, color="#ff908b", milliseconds=3000):
        self._append(session, "error", text, milliseconds)

    def hide(self):
        self.events.append(("hide",))


class _DeferredJob:
    _jobs = []

    def __init__(self, target, args, daemon):
        self.target, self.args = target, args
        self.daemon = daemon
        self._jobs.append(self)

    def start(self):
        pass


_deferred_jobs = _DeferredJob._jobs


class _FakeEngine:
    min_dur = 0.0

    def transcribe(self, _wav):
        return "recognized"


_daemon = daemon_module.Daemon.__new__(daemon_module.Daemon)
_daemon.lock = daemon_module.threading.Lock()
_daemon._overlay_sid = 0
_daemon._tts_busy = False
_daemon.rec = {
    "sid": 0, "grabando": False, "frames": [], "t_start": 0.0,
    "done": None, "busy": False,
}
_daemon.overlay = _OverlaySequence()
_daemon.engine = _FakeEngine()
_daemon.cfg = {"llm_url": ""}
_daemon.beep = lambda **_kwargs: None
with patch("instant2_app.daemon.threading.Thread", _DeferredJob):
    _daemon.on_press()
_check("key-down sends immediate starting state",
       _daemon.overlay.events == [("starting", 1)])
_daemon.rec["frames"] = [daemon_module.np.array([0.2, 0.2], dtype=daemon_module.np.float32)]
_daemon.rec["done"].set()
with patch("instant2_app.daemon.threading.Thread", _DeferredJob):
    _daemon.on_release()
_check("release goes directly to processing",
       _daemon.overlay.events == [("starting", 1), ("processing", 1)])
_daemon.overlay.listening(1)
_check("late mic-open update cannot reverse release transition",
       _daemon.overlay.events[-1] == ("processing", 1))
with patch("instant2_app.paste.paste", return_value=None):
    # Finishing release queues a separate recognition worker. Keep both jobs
    # deterministic instead of racing a live thread against the assertion.
    with patch("instant2_app.daemon.threading.Thread", _DeferredJob):
        release_job = _deferred_jobs[-1]
        release_job.target(*release_job.args)
    recognition_job = _deferred_jobs[-1]
    recognition_job.target(*recognition_job.args)
_check("successful paste produces brief confirmation",
       _daemon.overlay.events[-1] == ("success", 1, 760))
_daemon.overlay.starting(2)
_daemon.overlay.listening(2)
_daemon.overlay.processing(2)
_daemon.engine.transcribe = lambda _wav: (_ for _ in ()).throw(RuntimeError("recognizer failed"))
_daemon._job(daemon_module.np.array([0.2], dtype=daemon_module.np.float32), 1.0, 2)
_check("recognition error replaces processing with actionable feedback",
       _daemon.overlay.events[-1][0:2] == ("error", 2)
       and "Diagn" in _daemon.overlay.events[-1][2])

# The daemon job uses the active local glossary before paste.
_daemon.overlay.starting(3)
_daemon.overlay.processing(3)
_daemon.engine.transcribe = lambda _wav: "in stand"
_daemon.cfg = {
    "llm_url": "",
    "active_context": "Trabajo",
    "context_profiles": {
        "General": [],
        "Trabajo": [{"term": "Instant2", "aliases": ["in stand"]}],
    },
}
with patch("instant2_app.paste.paste") as _pasted:
    _daemon._job(daemon_module.np.array([0.2], dtype=daemon_module.np.float32), 1.0, 3)
_check("daemon pastes context-corrected transcription",
       _pasted.call_args.args[0] == "Instant2 ")


# frontend: DC-remove + high-pass, sin tocar la voz.
from instant2_app.engine import frontend
import numpy as _np
_sr = 16000
_t = _np.arange(2 * _sr) / _sr
_tono = (_np.sin(2 * _np.pi * 220 * _t) * 0.2).astype(_np.float32)
_out, _dc, _clip = frontend(_tono + 0.1)
_check("frontend quita DC", abs(_dc - 0.1) < 1e-6)
_check("frontend conserva tono 220Hz",
        abs(float(_np.max(_np.abs(_out))) - 0.2) < 0.02)
_check("frontend sin clip en voz sana", _clip == 0.0)
_out, _dc, _clip = frontend((_tono * 10).astype(_np.float32))
_check("frontend detecta saturacion", _clip > 0.02)
_out, _dc, _clip = frontend(_np.zeros(100, dtype=_np.float32))
_check("frontend silencio intacto", _dc == 0.0 and _clip == 0.0)

# El recorte FFT optimizado debe entregar exactamente la señal anterior al
# reconocedor, incluyendo cortes al borde de un bin y tomas de 120 segundos.
def _legacy_frontend(wav):
    w = _np.ascontiguousarray(_np.asarray(wav).flatten(), dtype=_np.float32)
    if w.size == 0:
        return w, 0.0, 0.0
    dc = float(_np.mean(w, dtype=_np.float64))
    w = (w - dc).astype(_np.float32)
    x = w.astype(_np.float64)
    spec = _np.fft.rfft(x)
    freqs = _np.fft.rfftfreq(x.size, 1.0 / float(_sr))
    spec[freqs < 80.0] = 0.0
    y = _np.ascontiguousarray(_np.fft.irfft(spec, n=x.size), dtype=_np.float32)
    clip = float(_np.mean(_np.abs(y) >= 0.999)) if y.size else 0.0
    return y, dc, clip


_rng = _np.random.default_rng(9173)
_frontend_equal = True
for _size in (15999, 16000, 16001, 44100, 1920000):
    _sample = _rng.normal(0.0, 0.1, _size).astype(_np.float32)
    _actual, _actual_dc, _actual_clip = frontend(_sample)
    _expected, _expected_dc, _expected_clip = _legacy_frontend(_sample)
    _frontend_equal &= (
        _np.array_equal(_actual, _expected)
        and _actual_dc == _expected_dc
        and _actual_clip == _expected_clip
    )
_check("frontend FFT optimizado conserva audio idéntico",
       _frontend_equal)

# join_texts: une, cose overlap, pega puntuacion, colapsa espacios.
_check("join vacios", join_texts(["", "  ", ""]) == "")
_check("join puntuacion", join_texts(["hola mundo", ", ¿ como estas ?"]) == "hola mundo, ¿como estas?")
_check("join espacios", join_texts(["  hola   mundo  "]) == "hola mundo")
_check("join cose overlap",
        join_texts(["hola mundo cruel", "mundo cruel adios"]) == "hola mundo cruel adios")
_check("join sin overlap intacto",
        join_texts(["hola mundo", "adios planeta"]) == "hola mundo adios planeta")
_check("join overlap parcial una palabra",
        join_texts(["que tal", "tal vez"]) == "que tal vez")

_check("merge cortos", merge_short_bounds([(0.0, 0.3), (0.5, 2.0)]) == [(0.0, 2.0)])
_check("merge largos", merge_short_bounds([(0.0, 3.0), (5.0, 8.0)]) == [(0.0, 3.0), (5.0, 8.0)])
_check("merge gap grande", merge_short_bounds([(0.0, 0.3), (5.0, 5.5)]) == [(0.0, 0.3), (5.0, 5.5)])

print("OK: daemon y overlay verdes.")
