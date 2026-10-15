"""Lectura Kokoro: texto de sesión, voces, cancelación y errores privados."""
import json
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from instant2_app import tts
from test_gui_lifecycle import SyncTasks, make_logic


class FakeReader:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1
        return True

    def play(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if self.failure:
            raise self.failure
        return 1200


class PendingTasks(SyncTasks):
    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        self.job = (fn, on_result, on_error, on_progress)
        return 1


class KokoroTextTests(unittest.TestCase):
    def test_split_text_keeps_all_words_and_bounds_normal_chunks(self):
        text = ("Hola, Laura. " + "Esta es una frase larga para comprobar la lectura. " * 20)
        parts = tts.split_text(text, limit=90)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 90 for part in parts))
        self.assertEqual(" ".join(" ".join(parts).split()), " ".join(text.split()))

    def test_single_line_breaks_do_not_split_reading_into_short_chunks(self):
        text = "Hola, Laura.\n¿Seguimos con el informe?\nTe lo leo completo."
        self.assertEqual(tts.split_text(text), [
            "Hola, Laura. ¿Seguimos con el informe? Te lo leo completo."])

    def test_supported_spanish_voices_are_explicit(self):
        self.assertEqual(set(tts.VOICES), {"ef_dora"})
        self.assertEqual(tts.MODEL_TOTAL_BYTES,
                         tts.MODEL_FILE[0] + tts.DORA_FILE[0])
        self.assertLess(tts.DORA_FILE[0], 600_000)

    def test_dora_voice_archive_contains_only_the_verified_style(self):
        import hashlib
        import tempfile
        import numpy as np

        voice = np.arange(6, dtype=np.float32)
        raw = voice.tobytes()
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "ef_dora.bin")
            archive_path = os.path.join(directory, "voices-dora.npz")
            with open(source, "wb") as handle:
                handle.write(raw)
            with patch.object(tts, "DORA_FILE",
                              (len(raw), hashlib.sha256(raw).hexdigest())), \
                    patch.object(tts, "DORA_STYLE_SHAPE", (2, 1, 3)):
                self.assertEqual(tts._prepare_voice_archive(source, archive_path),
                                 archive_path)
                self.assertTrue(tts._voice_archive_valid(archive_path))
                with np.load(archive_path, allow_pickle=False) as result:
                    self.assertEqual(result.files, ["ef_dora"])
                    np.testing.assert_array_equal(result["ef_dora"],
                                                  voice.reshape(2, 1, 3))

    def test_bundled_dora_assets_are_ready_without_downloading(self):
        import hashlib
        import tempfile
        import numpy as np

        model_bytes = b"onnx model fixture"
        voice = np.arange(6, dtype=np.float32).reshape(2, 1, 3)
        voice_bytes = voice.tobytes()
        model = (len(model_bytes), hashlib.sha256(model_bytes).hexdigest())
        dora = (len(voice_bytes), hashlib.sha256(voice_bytes).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            paths = tts.asset_paths(directory)
            os.makedirs(paths["directory"])
            with open(paths["model"], "wb") as handle:
                handle.write(model_bytes)
            with open(paths["voices"], "wb") as handle:
                np.savez_compressed(handle, ef_dora=voice)
            legacy = os.path.join(paths["directory"], "voices-v1.0.bin")
            with open(legacy, "wb") as handle:
                handle.write(b"old all-voices package")
            with patch.object(tts, "MODEL_FILE", model), \
                    patch.object(tts, "DORA_FILE", dora), \
                    patch.object(tts, "DORA_STYLE_SHAPE", (2, 1, 3)), \
                    patch("instant2_app.tts.models._fetch") as fetch, \
                    patch("instant2_app.tts.models._space_check"):
                self.assertTrue(tts.model_ready(directory))
                resolved = tts.ensure_model(directory)
                fetch.assert_not_called()
                self.assertTrue(tts._voice_archive_valid(resolved["voices"]))
                self.assertFalse(os.path.exists(legacy))

    def test_load_uses_verified_dora_archive_after_raw_voice_is_removed(self):
        import tempfile
        import types

        with tempfile.TemporaryDirectory() as directory:
            paths = tts.asset_paths(directory)
            kokoro_module = types.ModuleType("kokoro_onnx")
            kokoro_module.Kokoro = SimpleNamespace(from_session=lambda session, voices: (session, voices))
            fake_session = object()
            options = SimpleNamespace(add_session_config_entry=lambda *args: None)
            ort_module = SimpleNamespace(SessionOptions=lambda: options,
                                         InferenceSession=lambda *args, **kwargs: fake_session)
            misaki_module = types.ModuleType("misaki")
            espeak_module = types.ModuleType("misaki.espeak")
            espeak_module.EspeakG2P = lambda **kwargs: kwargs
            reader = tts.KokoroReader(directory)

            with patch("instant2_app.tts.ensure_model", return_value=paths) as ensure, \
                    patch.dict(sys.modules, {
                        "kokoro_onnx": kokoro_module,
                        "misaki": misaki_module,
                        "misaki.espeak": espeak_module,
                        "onnxruntime": ort_module,
                    }):
                engine, g2p = reader._load(None, None)

            ensure.assert_called_once_with(directory, None, None)
            self.assertEqual(engine, (fake_session, paths["voices"]))
            self.assertEqual(options.intra_op_num_threads, reader.threads)
            self.assertEqual(options.inter_op_num_threads, 1)
            self.assertEqual(g2p, {"language": "es"})

    def test_kokoro_unload_releases_engine_and_phonemizer_references(self):
        reader = tts.KokoroReader("unused")
        reader._engine = object()
        reader._g2p = object()
        self.assertTrue(reader.is_loaded())
        self.assertTrue(reader.unload())
        self.assertFalse(reader.is_loaded())
        self.assertIsNone(reader._engine)
        self.assertIsNone(reader._g2p)
        self.assertFalse(reader.unload())

    def test_audio_uses_one_stream_and_prefetches_following_phrase(self):
        import numpy as np

        second_started = threading.Event()
        stream_instances = []

        class FakeEngine:
            def create(self, phonemes, _voice, **_kwargs):
                is_second = phonemes.startswith("segunda")
                if is_second:
                    second_started.set()
                return np.full(5000, -1.0 if is_second else 1.0,
                               dtype=np.float32), 24_000

        class FakeStream:
            def __init__(self, **_kwargs):
                self.blocks = []
                stream_instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def write(self, samples):
                if not self.blocks:
                    self.assert_prefetched = second_started.wait(2)
                self.blocks.append(np.asarray(samples).copy())

        reader = tts.KokoroReader("unused")
        reader._engine = FakeEngine()
        reader._g2p = lambda text: (text, None)
        audio = SimpleNamespace(OutputStream=FakeStream)
        with patch("instant2_app.tts.split_text",
                   return_value=["primera frase.", "segunda frase."]), \
                patch.dict(sys.modules, {"sounddevice": audio}):
            samples_written = reader.play("primera frase.\nsegunda frase.")

        self.assertEqual(samples_written, 10_000)
        self.assertEqual(len(stream_instances), 1)
        stream = stream_instances[0]
        self.assertTrue(stream.assert_prefetched)
        self.assertEqual([float(block[0]) for block in stream.blocks],
                         [1.0, 1.0, 1.0, -1.0, -1.0, -1.0])


class PanelReadTests(unittest.TestCase):
    def make_reader_logic(self, reader, tasks=None):
        with patch("instant2_app.tts.model_ready", return_value=True), \
                patch("instant2_app.tts.KokoroReader", return_value=reader):
            logic, emitted = make_logic(tasks=tasks or SyncTasks())
        logic._tts_reader = reader
        logic.cfg["resource_mode"] = "speed"
        return logic, emitted

    def test_selected_text_voice_and_speed_go_only_to_local_reader(self):
        reader = FakeReader()
        logic, emitted = self.make_reader_logic(reader)
        with patch("instant2_app.tts.model_ready", return_value=True):
            logic.handle({"op": "tts_play", "text": "Hola Laura.",
                          "voice": "ef_dora", "speed": 1.1})
        self.assertEqual(len(reader.calls), 1)
        text, options = reader.calls[0]
        self.assertEqual(text, "Hola Laura.")
        self.assertEqual(options["voice"], "ef_dora")
        self.assertEqual(options["speed"], 1.1)
        self.assertEqual(logic.state_payload()["tts"]["status"], "ready")
        serialized_states = json.dumps(
            [payload for kind, payload in emitted if kind == "state"],
            ensure_ascii=False)
        self.assertNotIn("Hola Laura.", serialized_states)

    def test_failure_does_not_return_or_log_input_text(self):
        private_text = "dato privado de prueba"
        reader = FakeReader(RuntimeError(private_text))
        logic, emitted = self.make_reader_logic(reader)
        with patch("instant2_app.tts.model_ready", return_value=True):
            logic.handle({"op": "tts_play", "text": private_text})
        state = logic.state_payload()["tts"]
        self.assertEqual(state["status"], "error")
        self.assertNotIn(private_text, state["message"])
        self.assertNotIn(private_text, json.dumps(emitted, ensure_ascii=False))

    def test_blank_or_oversized_text_is_rejected_before_starting(self):
        reader = FakeReader()
        logic, _emitted = self.make_reader_logic(reader)
        with patch("instant2_app.tts.model_ready", return_value=True):
            logic.handle({"op": "tts_play", "text": "  "})
            self.assertEqual(reader.calls, [])
            logic.handle({"op": "tts_play", "text": "x" * (tts.MAX_TEXT_CHARS + 1)})
        self.assertEqual(reader.calls, [])
        self.assertIn("10.000", logic.state_payload()["tts"]["message"])

    def test_stop_sets_cancellation_without_saving_text(self):
        reader = FakeReader()
        tasks = PendingTasks()
        logic, _emitted = self.make_reader_logic(reader, tasks=tasks)
        with patch("instant2_app.tts.model_ready", return_value=True):
            logic.handle({"op": "tts_play", "text": "Texto temporal."})
            event = logic._tts_cancel_event
            logic.handle({"op": "tts_stop"})
        self.assertTrue(event.is_set())
        self.assertTrue(logic.state_payload()["tts"]["canceling"])
        self.assertEqual(reader.calls, [])

    def test_text_reading_page_is_a_valid_navigation_target(self):
        logic, emitted = make_logic()
        logic.navigate("read")
        self.assertEqual(logic.page, "read")
        self.assertIn(("navigate", {"page": "read"}), emitted)

    def test_panel_reader_releases_kokoro_when_idle(self):
        reader = FakeReader()
        logic, _emitted = self.make_reader_logic(reader)
        logic._unload_tts_if_idle()
        self.assertEqual(reader.unloaded, 1)
