"""Resource recommendations stay conservative and never require a GPU."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import llm_resources


class LLMResourceTests(unittest.TestCase):
    def test_large_gpu_recommends_testing_small_quantized_models_first(self):
        result = llm_resources._recommendation(
            total_bytes=16 * 1024 ** 3,
            available_bytes=8 * 1024 ** 3,
            gpus=[{"name": "GPU", "vram_bytes": 16 * 1024 ** 3}],
        )
        self.assertEqual(result["profile"], "gpu")
        self.assertIn("2B y 4B", result["model_hint"])
        self.assertIn("confirma", result["runtime_hint"])

    def test_cpu_only_recommends_a_small_q4_model(self):
        result = llm_resources._recommendation(
            total_bytes=8 * 1024 ** 3,
            available_bytes=4 * 1024 ** 3,
            gpus=[],
        )
        self.assertEqual(result["profile"], "cpu")
        self.assertIn("Q4", result["model_hint"])
        self.assertIn("CPU", result["runtime_hint"])

    def test_low_free_memory_recommends_not_loading_a_model(self):
        result = llm_resources._recommendation(
            total_bytes=8 * 1024 ** 3,
            available_bytes=1 * 1024 ** 3,
            gpus=[],
        )
        self.assertEqual(result["profile"], "constrained")
        self.assertIn("apagado", result["model_hint"])

    def test_summary_reports_cpu_ram_gpu_and_no_user_paths(self):
        value = {
            "cpu": "Ryzen 5",
            "ram_total_bytes": 16 * 1024 ** 3,
            "ram_available_bytes": 6 * 1024 ** 3,
            "gpus": [{"name": "Radeon RX", "vram_bytes": 16 * 1024 ** 3}],
            "model_hint": "Probar 2B y 4B.",
        }
        summary = llm_resources.format_summary(value)
        self.assertIn("Ryzen 5", summary)
        self.assertIn("RAM 16.0 GB", summary)
        self.assertIn("Radeon RX (16.0 GB VRAM)", summary)
        self.assertNotIn(os.environ.get("USERPROFILE", "C:\\Users"), summary)

    def test_detect_resources_survives_platform_probe_errors(self):
        with patch.object(llm_resources.sys, "platform", "win32"), \
                patch.object(llm_resources, "_windows_memory", return_value=(None, None)), \
                patch.object(llm_resources, "_windows_gpus", return_value=[]), \
                patch.object(llm_resources, "_cpu_name", return_value="CPU"), \
                patch.object(llm_resources.os, "cpu_count", return_value=2):
            result = llm_resources.detect_resources()
        self.assertEqual(result["profile"], "constrained")
        self.assertEqual(result["logical_cpus"], 2)


if __name__ == "__main__":
    unittest.main()
