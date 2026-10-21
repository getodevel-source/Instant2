"""Pruebas de rendimiento/regresion para los fixes de congelamiento."""
import inspect
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import daemon as daemon_module
from instant2_app import __main__ as main_mod
from instant2_app.engine import Engine


def _blk():
    return np.full((160, 1), 0.05, dtype=np.float32)


class _FakeVad:
    def window_size(self):
        return 512

    def is_speech(self, _w):
        return True


class _NullOverlay:
    def processing(self, _s):
        pass

    def starting(self, _s):
        pass

    def listening(self, _s):
        pass

    def show_notice_for(self, *a, **k):
        pass

    def show_error_for(self, *a, **k):
        pass

    def success_for(self, _s):
        pass


def _fake_sherpa(calls):
    return types.SimpleNamespace(
        VadModelConfig=lambda: types.SimpleNamespace(
            silero_vad=types.SimpleNamespace(model="", threshold=0.5,
                                             min_silence_duration=0.5,
                                             min_speech_duration=0.2,
                                             window_size=512),
            sample_rate=16000),
        VadModel=types.SimpleNamespace(
            create=lambda _c: (calls.__setitem__("n", calls["n"] + 1),
                               _FakeVad())[1]),
    )


class _ManualTimer:
    instances = []

    def __init__(self, interval, function, args=(), kwargs=None):
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = False
        self.cancelled = False
        self.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class PerfFreezeTests(unittest.TestCase):
    def test_queue_bounded_outside_session(self):
        keeper = daemon_module.StreamKeeper({})
        blk = _blk()
        t0 = time.time()
        for _ in range(10000):
            keeper._cb(blk, 160, None, None)
        dt = time.time() - t0
        self.assertEqual(keeper.q.qsize(), 0, f"q={keeper.q.qsize()}")
        self.assertLess(dt, 2.0, f"{dt:.2f}s")
        self.assertLessEqual(len(keeper.pre), 256, f"pre={len(keeper.pre)}")

    def test_session_enqueue_and_drain(self):
        keeper = daemon_module.StreamKeeper({})
        blk = _blk()
        keeper.begin_session()
        for _ in range(100):
            keeper._cb(blk, 160, None, None)
        self.assertEqual(keeper.q.qsize(), 100, f"q={keeper.q.qsize()}")
        keeper.end_session()
        self.assertEqual(keeper.q.qsize(), 0)

    def test_snapshot_fast(self):
        keeper = daemon_module.StreamKeeper({})
        blk = _blk()
        keeper.begin_session()
        for _ in range(1000):
            keeper._cb(blk, 160, None, None)
        t0 = time.time()
        keeper.snapshot(0.45)
        dt = time.time() - t0
        self.assertLess(dt, 0.5, f"{dt:.3f}s")
        keeper.end_session()

    def test_engine_threads_clamp_and_caps(self):
        eng = Engine(data_dir=tempfile.mkdtemp(), threads=64)
        cpu = os.cpu_count() or 4
        self.assertGreaterEqual(eng.threads, 1)
        self.assertLessEqual(eng.threads, min(8, cpu))
        eng2 = Engine(data_dir=tempfile.mkdtemp(), threads="no-int")
        eng_default = Engine(data_dir=tempfile.mkdtemp())
        self.assertEqual(eng2.threads, eng_default.threads)
        self.assertEqual(eng.FULL_RETRY_MAX_SECONDS, 30.0)
        self.assertEqual(daemon_module.StreamKeeper.MAX_SESSION_SECONDS, 120.0)

    def test_vad_reused_and_released_on_unload(self):
        calls = {"n": 0}
        dd = tempfile.mkdtemp()
        os.makedirs(os.path.join(dd, "parakeet-v3-int8"), exist_ok=True)
        os.makedirs(os.path.join(dd, "silero-vad"), exist_ok=True)
        open(os.path.join(dd, "silero-vad", "silero_vad.onnx"), "wb").close()
        with patch.dict(sys.modules, {"sherpa_onnx": _fake_sherpa(calls)}):
            eng3 = Engine(data_dir=dd)
            v1 = eng3.vad()
            v2 = eng3.vad()
            self.assertEqual(calls["n"], 1, f"creates={calls['n']}")
            self.assertIs(v1, v2)
            eng3.unload()
            v3 = eng3.vad()
            self.assertEqual(calls["n"], 2)
            self.assertIsNot(v3, v1)

    def test_idle_model_unload_waits_for_recording_and_transcription(self):
        _ManualTimer.instances.clear()
        d = daemon_module.Daemon.__new__(daemon_module.Daemon)
        d.lock = threading.Lock()
        d._shutdown = threading.Event()
        d._model_idle_unload_seconds = 300.0
        d._model_idle_timer = None
        d._model_idle_generation = 0
        d.rec = {"grabando": True, "busy": False}
        d.engine = Mock()
        d.engine.has_loaded_models.return_value = True
        with patch.object(daemon_module.threading, "Timer", _ManualTimer):
            d._unload_models_if_idle()
            self.assertEqual(d.engine.unload.call_count, 0)
            self.assertEqual(_ManualTimer.instances[-1].interval, 30.0)

            d.rec["grabando"] = False
            d.rec["busy"] = True
            d._unload_models_if_idle()
            self.assertEqual(d.engine.unload.call_count, 0)

            d.rec["busy"] = False
            d._unload_models_if_idle()
            self.assertEqual(d.engine.unload.call_count, 1)

    def test_stale_model_idle_timer_cannot_unload_after_reschedule(self):
        _ManualTimer.instances.clear()
        d = daemon_module.Daemon.__new__(daemon_module.Daemon)
        d.lock = threading.Lock()
        d._shutdown = threading.Event()
        d._model_idle_unload_seconds = 300.0
        d._model_idle_timer = None
        d._model_idle_generation = 0
        d.rec = {"grabando": False, "busy": False}
        d.engine = Mock()
        d.engine.has_loaded_models.return_value = True
        with patch.object(daemon_module.threading, "Timer", _ManualTimer):
            d._schedule_model_idle_unload()
            stale = _ManualTimer.instances[-1]
            d._schedule_model_idle_unload()
            current = _ManualTimer.instances[-1]
            self.assertTrue(stale.cancelled)
            self.assertNotEqual(stale.args, current.args)

            stale.function(*stale.args, **stale.kwargs)
            self.assertEqual(d.engine.unload.call_count, 0)
            current.function(*current.args, **current.kwargs)
            self.assertEqual(d.engine.unload.call_count, 1)

    def test_on_release_returns_fast(self):
        d = daemon_module.Daemon.__new__(daemon_module.Daemon)
        d.lock = threading.Lock()
        d.rec = {"sid": 1, "grabando": True, "frames": [], "t_start": time.time(),
                 "done": None, "busy": False}
        d.cfg = {}
        d.overlay = _NullOverlay()
        d.engine = Engine(data_dir=tempfile.mkdtemp())
        d.rec["done"] = threading.Event()
        t0 = time.time()
        d.on_release()
        dt = time.time() - t0
        self.assertLess(dt, 0.2, f"{dt:.3f}s")

    def test_log_uses_rotating_handler(self):
        src = inspect.getsource(main_mod._log_setup)
        self.assertIn("RotatingFileHandler", src)
        self.assertIn("maxBytes", src)
        self.assertIn("backupCount", src)

    def test_transcribe_serialized_without_threadpool(self):
        eng_path = os.path.join(os.path.dirname(__file__), "..", "src",
                                "instant2_app", "engine.py")
        with open(eng_path, encoding="utf-8") as handle:
            eng_src = handle.read()
        self.assertNotIn("ThreadPoolExecutor", eng_src)


if __name__ == "__main__":
    unittest.main()
