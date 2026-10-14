"""Shared model-residency and daemon LLM ownership decisions."""
import os
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app.daemon import Daemon


class DaemonResourceLifecycleTests(unittest.TestCase):
    def daemon(self, config):
        daemon = Daemon.__new__(Daemon)
        daemon.cfg = config
        daemon._shutdown = threading.Event()
        daemon.lock = threading.Lock()
        daemon._model_idle_unload_seconds = 300
        daemon._model_idle_timer = None
        daemon._model_idle_generation = 0
        daemon.rec = {"grabando": False, "busy": False}
        daemon.engine = Mock()
        daemon.engine.has_loaded_models.return_value = False
        daemon._tts_busy = False
        daemon._tts_reader = None
        return daemon

    def test_llm_owner_uses_selected_idle_profile(self):
        for profile, seconds in (("speed", 0), ("balanced", 300), ("memory", 90)):
            daemon = self.daemon({
                "llm_normal_enabled": True,
                "llm_managed_enabled": True,
                "llm_url": "http://127.0.0.1:8080/v1",
                "llm_server_path": "llama-server",
                "llm_model_path": "model.gguf",
                "resource_mode": profile,
            })
            runtime, static = daemon._configured_llm_runtime()
            self.assertIsNone(static)
            self.assertEqual(runtime["llm_idle_sleep_seconds"], seconds)

    def test_disabled_remote_and_external_endpoints_are_never_managed(self):
        cases = [
            ({"llm_normal_enabled": False,
              "llm_url": "http://127.0.0.1:8080/v1"}, "disabled"),
            ({"llm_normal_enabled": True,
              "llm_url": "https://llm.example.test/v1"}, "configured"),
            ({"llm_normal_enabled": True,
              "llm_url": "http://127.0.0.1:11434/v1"}, "configured"),
        ]
        for config, expected_status in cases:
            daemon = self.daemon(config)
            runtime, static = daemon._configured_llm_runtime()
            self.assertIsNone(runtime)
            self.assertEqual(static["status"], expected_status)

    def test_idle_timer_releases_asr_and_tts_together(self):
        daemon = self.daemon({})
        daemon.engine.has_loaded_models.return_value = True
        daemon._tts_reader = Mock()
        daemon._tts_reader.is_loaded.return_value = True
        with patch("instant2_app.daemon.threading.Timer"):
            daemon._unload_models_if_idle()
        daemon.engine.unload.assert_called_once_with()
        daemon._tts_reader.unload.assert_called_once_with()

    def test_speed_profile_disables_idle_unload_timer(self):
        daemon = self.daemon({})
        daemon._model_idle_unload_seconds = 0
        with patch("instant2_app.daemon.threading.Timer") as timer:
            daemon._schedule_model_idle_unload()
        timer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
