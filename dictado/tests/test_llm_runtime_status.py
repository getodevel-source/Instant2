"""Daemon-to-panel runtime status contains no transcript material."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import llm_runtime_status as status


class LLMRuntimeStatusTests(unittest.TestCase):
    def test_status_is_text_free_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(status, "config_dir", return_value=directory):
                saved = status.write_status({
                    "status": "sleeping", "detail": "LLM local en reposo.",
                    "device": "CPU", "owned": True,
                    "transcript": "dato privado", "prompt": "instrucción privada",
                }, pid=123)
                self.assertEqual(saved["status"], "sleeping")
                self.assertEqual(status.read_status()["device"], "CPU")
                with open(os.path.join(directory, status.STATUS_NAME),
                          encoding="utf-8") as handle:
                    raw = handle.read()
                self.assertNotIn("dato privado", raw)
                self.assertNotIn("instrucción privada", raw)

    def test_stale_status_is_never_reported_as_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {"status": "ready", "detail": "ready", "device": "CPU",
                       "owned": True, "pid": 123, "updated_at": 1}
            with patch.object(status, "_path",
                              return_value=os.path.join(directory, "status.json")):
                with open(status._path(status.STATUS_NAME), "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                self.assertEqual(status.read_status(max_age_seconds=1)["status"],
                                 "stopped")

    def test_retry_is_a_text_free_one_shot_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(status, "config_dir", return_value=directory):
                self.assertTrue(status.request_retry())
                self.assertTrue(status.consume_retry_request())
                self.assertFalse(status.consume_retry_request())


if __name__ == "__main__":
    unittest.main()
