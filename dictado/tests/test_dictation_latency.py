"""Dictation cannot wait for an optional model it is not configured to use."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from instant2_app.daemon import Daemon
from instant2_app.dictation import prepare_text
from instant2_app import llm


class DictationLatencyTests(unittest.TestCase):
    def daemon(self, cfg, managed=None):
        return SimpleNamespace(
            engine=SimpleNamespace(min_dur=.01, transcribe=Mock(return_value="hola")),
            cfg=cfg, overlay=Mock(), lock=threading.Lock(), rec={"busy": True},
            beep=Mock(), _schedule_model_idle_unload_locked=Mock(),
            _llm_initial_ready=Mock(), _llm_runtime=Mock(),
            _llm_runtime_config=managed)

    def test_local_only_dictation_does_not_wait_for_llm_startup(self):
        d = self.daemon({"llm_url": "", "llm_normal_enabled": True})
        with patch("instant2_app.llm.maybe_polish", return_value="hola"), patch("instant2_app.paste.paste"):
            Daemon._job(d, np.ones(1600, dtype=np.float32), .1, 1)
        d._llm_initial_ready.wait.assert_not_called()
        d._llm_runtime.request_timeout.assert_not_called()

    def test_remote_llm_does_not_wait_for_managed_local_startup(self):
        d = self.daemon({"llm_url": "https://example.test/v1"})
        with patch("instant2_app.llm.maybe_polish", return_value="hola"), patch("instant2_app.paste.paste"):
            Daemon._job(d, np.ones(1600, dtype=np.float32), .1, 1)
        d._llm_initial_ready.wait.assert_not_called()

    def test_disabled_llm_does_not_wait_even_with_old_managed_configuration(self):
        d = self.daemon({"llm_url": "http://127.0.0.1:8080", "llm_normal_enabled": False}, {"llm_url": "http://127.0.0.1:8080"})
        with patch("instant2_app.llm.maybe_polish", return_value="hola"), patch("instant2_app.paste.paste"):
            Daemon._job(d, np.ones(1600, dtype=np.float32), .1, 1)
        d._llm_initial_ready.wait.assert_not_called()
        d._llm_runtime.request_timeout.assert_not_called()


class CorrectionBudgetTests(unittest.TestCase):
    def test_startup_exhausting_budget_keeps_local_correction_without_http(self):
        event = Mock()
        event.is_set.return_value = False
        runtime = Mock()
        runtime.request_timeout.return_value = 8.0
        cfg = {"llm_url": "http://127.0.0.1:8080"}
        with patch("instant2_app.dictation.time.monotonic", side_effect=[0, 0, 8.1]), \
                patch("instant2_app.llm.maybe_polish") as polish, \
                patch("instant2_app.llm.correct_locally", return_value="local"):
            self.assertEqual(prepare_text("recognized", cfg, initial_ready=event, runtime=runtime, runtime_config={}), "local")
        event.wait.assert_called_once_with(timeout=8.0)
        polish.assert_not_called()

    def test_verification_shares_remaining_budget_after_first_response(self):
        cfg = {"llm_url": "http://127.0.0.1:8080"}
        with patch("instant2_app.llm.time.monotonic", side_effect=[0, 7.5]), \
                patch("instant2_app.llm.polish_candidate", return_value="candidate"), \
                patch("instant2_app.llm.candidate_preserves_meaning", return_value=True) as verify:
            self.assertEqual(llm.maybe_polish("recognized", cfg, timeout=8), "candidate")
        self.assertEqual(verify.call_args.kwargs["timeout"], .5)

    def test_finished_budget_does_not_start_second_request(self):
        cfg = {"llm_url": "http://127.0.0.1:8080"}
        with patch("instant2_app.llm.time.monotonic", side_effect=[0, 8.1]), \
                patch("instant2_app.llm.polish_candidate", return_value="candidate"), \
                patch("instant2_app.llm.candidate_preserves_meaning") as verify:
            self.assertEqual(llm.maybe_polish("recognized", cfg, timeout=8), "recognized")
        verify.assert_not_called()



if __name__ == "__main__":
    unittest.main()
