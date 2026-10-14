"""Runtime benchmark chooses CPU or one explicit accelerator deterministically."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))

from bench_llm_runtime import CANDIDATES, build_command


class LLMRuntimeBenchmarkTests(unittest.TestCase):
    def test_gpu_command_targets_selected_device_and_full_layer_offload(self):
        command = build_command("llama-server.exe", "qwen.gguf", "qwen-q4",
                                8080, "gpu", "Vulkan0", 6, 4096)
        self.assertIn("Vulkan0", command)
        self.assertIn("99", command)
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")

    def test_cpu_command_disables_gpu_layers(self):
        command = build_command("llama-server.exe", "qwen.gguf", "qwen-q4",
                                8080, "cpu", "Vulkan0", 4, 4096)
        self.assertEqual(command[command.index("--device") + 1], "none")
        self.assertEqual(command[command.index("--n-gpu-layers") + 1], "0")

    def test_candidates_cover_quantization_and_family_comparisons(self):
        self.assertEqual(set(CANDIDATES), {
            "qwen3.5-2b-q4", "qwen3.5-2b-q8", "qwen3.5-4b-q4",
            "qwen3.5-4b-q8", "llama3.2-3b-q4",
        })
        qwen_q4 = CANDIDATES["qwen3.5-2b-q4"][0]
        qwen_q8 = CANDIDATES["qwen3.5-2b-q8"][0]
        self.assertNotEqual(qwen_q4, qwen_q8)


if __name__ == "__main__":
    unittest.main()
