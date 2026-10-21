"""Session and glossary diagnostics do not persist dictated content."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import bias, context, llm
from instant2_app.daemon import Daemon
from instant2_app.engine import Engine, SAMPLE_RATE


class TranscriptLogPrivacyTests(unittest.TestCase):
    def test_meaning_gate_failure_does_not_log_transcript_or_exception_text(self):
        secret = "Frase privada llegada a las ocho"
        cfg = {"llm_url": "http://127.0.0.1:8080", "llm_model": "local"}
        with patch("instant2_app.llm.correct_locally", return_value=secret), \
                patch("instant2_app.context.correction_context", return_value=""), \
                patch("instant2_app.bias.terms_for_correction", return_value=""), \
                patch("instant2_app.llm.polish_candidate", return_value="Frase privada llegue"), \
                patch("instant2_app.llm.candidate_preserves_meaning",
                      side_effect=RuntimeError(secret)), \
                self.assertLogs("instant2", level="WARNING") as captured:
            result = llm.maybe_polish(secret, cfg)

        log_text = "\n".join(captured.output)
        self.assertEqual(result, secret)
        self.assertNotIn(secret, log_text)
        self.assertNotIn("frase privada", log_text.lower())

    def test_glossary_correction_does_not_log_before_or_after_text(self):
        cfg = {"context_profiles": {"General": [
            {"term": "Instant2", "aliases": ["instante"]}]}}
        with patch.object(context.log, "info") as info, \
                patch.object(context.log, "debug") as debug:
            result = context.correct_aliases("instante privado", cfg)
        self.assertEqual(result, "Instant2 privado")
        info.assert_not_called()
        debug.assert_called_once_with("vocab: se aplicaron correcciones locales")

    def test_bias_correction_does_not_log_dictated_word(self):
        secret = "quemet confidencial z9x8"
        text = f"subi el {secret} al repo push"
        with self.assertLogs("instant2", level="DEBUG") as captured:
            bias.correct_biased(text, conf=0.5)
        log_text = "\n".join(captured.output)
        self.assertNotIn(secret, log_text)
        self.assertNotIn("quemet confidencial", log_text)
    def test_success_log_reports_metrics_without_transcript(self):
        import numpy as np

        secret = "Frase dictada confidencial 9b3d"
        overlay = SimpleNamespace(success_for=Mock(), show_notice_for=Mock(),
                                  show_error_for=Mock())
        fake = SimpleNamespace(
            engine=SimpleNamespace(min_dur=0.01,
                                   transcribe=Mock(return_value=secret)),
            cfg={"key": "f9"}, overlay=overlay, lock=threading.Lock(),
            rec={"busy": True}, beep=Mock(),
            _schedule_model_idle_unload_locked=Mock())
        with patch("instant2_app.llm.maybe_polish", return_value=secret), \
                patch("instant2_app.paste.paste") as paste, \
                self.assertLogs("instant2", level="INFO") as captured:
            Daemon._job(fake, np.ones(1600, dtype=np.float32), 0.1, 1)

        log_text = "\n".join(captured.output)
        self.assertNotIn(secret, log_text)
        self.assertIn(f"{len(secret)} caracteres", log_text)
        paste.assert_called_once_with(secret + " ")
        overlay.success_for.assert_called_once_with(1)
        self.assertFalse(fake.rec["busy"])

    def test_engine_segment_log_reports_length_without_transcript(self):
        import numpy as np

        secret = "Frase reconocida confidencial"
        engine = Engine.__new__(Engine)
        engine.save_wavs_dir = None
        with patch.object(engine, "segment", return_value=[(0.0, 1.0)]), \
                patch.object(engine, "_one", return_value=(0, secret)), \
                self.assertLogs("instant2", level="INFO") as captured:
            result = engine.transcribe(np.ones(SAMPLE_RATE, dtype=np.float32))

        self.assertEqual(result, secret)
        log_text = "\n".join(captured.output)
        self.assertNotIn(secret, log_text)
        self.assertIn(f"{len(secret)} caracteres", log_text)

    def test_engine_retry_log_reports_length_without_transcript(self):
        import numpy as np

        secret = "Texto recuperado confidencial"
        engine = Engine.__new__(Engine)
        engine._one = Mock(return_value=(-1, secret))
        wav = np.ones(SAMPLE_RATE, dtype=np.float32)
        chunks = [(0, wav, 1.0)]
        with self.assertLogs("instant2", level="INFO") as captured:
            result = engine._recover_empties(
                wav, 1.0, [(0.0, 1.0)], chunks, [""])

        log_text = "\n".join(captured.output)
        self.assertNotIn(secret, log_text)
        self.assertIn(f"{len(secret)} caracteres", log_text)
        self.assertEqual(result, [secret])

    def test_malformed_llm_response_error_does_not_include_response_content(self):
        import json

        secret = "Eco confidencial del usuario"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"echo": secret}).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(RuntimeError) as caught:
                llm.polish("texto de prueba", "http://127.0.0.1:8080")
        self.assertNotIn(secret, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
