"""El runtime del benchmark compara los dos candidatos vigentes.

Cubre los candidatos, los flags de GPU/CPU, los perfiles de sampling, el hash
de los archivos, la memoria del proceso del servidor y el reporte que deja
`run_candidate`.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))

import bench_llm_runtime


class _FakeProcess:
    """Servidor simulado: nunca levanta, pero cuenta como proceso vivo."""

    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class LLMRuntimeCandidateTests(unittest.TestCase):
    def test_candidates_are_the_two_current_models(self):
        self.assertEqual(set(bench_llm_runtime.CANDIDATES),
                         {"qwen3.5-4b-q4", "bonsai2-9b-q2"})
        self.assertEqual(
            bench_llm_runtime.CANDIDATES["qwen3.5-4b-q4"],
            ("Qwen_Qwen3.5-4B-Q4_K_M.gguf", "qwen3.5:4b-q4_K_M"))
        self.assertEqual(
            bench_llm_runtime.CANDIDATES["bonsai2-9b-q2"],
            ("Ternary-Bonsai-2-9B-Q2_0.gguf", "bonsai2-9b-q2-0"))

    def test_sampling_profiles_declare_the_same_knobs(self):
        self.assertEqual(set(bench_llm_runtime.SAMPLING),
                         {"matched-deterministic", "bonsai-nonthinking"})
        expected = {"temperature", "top_p", "top_k", "min_p",
                    "presence_penalty", "repetition_penalty"}
        for name, profile in bench_llm_runtime.SAMPLING.items():
            with self.subTest(profile=name):
                self.assertEqual(set(profile), expected)


class LLMRuntimeCommandTests(unittest.TestCase):
    def test_gpu_command_targets_selected_device_and_full_layer_offload(self):
        command = bench_llm_runtime.build_command(
            "llama-server.exe", "qwen.gguf", "qwen-q4", 8081, "gpu",
            "Vulkan0", 6, 4096)
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(command[command.index("--port") + 1], "8081")
        self.assertEqual(command[command.index("--device") + 1], "Vulkan0")
        self.assertEqual(command[command.index("--n-gpu-layers") + 1], "99")
        self.assertEqual(command[command.index("--ctx-size") + 1], "4096")
        self.assertEqual(command[command.index("--threads") + 1], "6")

    def test_cpu_command_disables_gpu_layers(self):
        command = bench_llm_runtime.build_command(
            "llama-server.exe", "qwen.gguf", "qwen-q4", 8081, "cpu",
            "Vulkan0", 4, 4096)
        self.assertEqual(command[command.index("--device") + 1], "none")
        self.assertEqual(command[command.index("--n-gpu-layers") + 1], "0")

    def test_matched_deterministic_profile_is_the_default(self):
        command = bench_llm_runtime.build_command(
            "llama-server.exe", "qwen.gguf", "qwen-q4", 8081, "gpu",
            "Vulkan0", 6, 4096)
        self.assertEqual(command[command.index("--temp") + 1], "0.0")
        self.assertEqual(command[command.index("--top-p") + 1], "1.0")
        self.assertEqual(command[command.index("--top-k") + 1], "0")
        self.assertEqual(command[command.index("--min-p") + 1], "0.0")
        self.assertEqual(command[command.index("--presence-penalty") + 1], "0.0")
        self.assertEqual(command[command.index("--repeat-penalty") + 1], "1.0")
        self.assertEqual(command[command.index("--repeat-last-n") + 1], "64")

    def test_bonsai_nonthinking_profile_sets_the_official_sampler(self):
        command = bench_llm_runtime.build_command(
            "llama-server.exe", "bonsai.gguf", "bonsai", 8081, "gpu",
            "Vulkan0", 6, 4096, "bonsai-nonthinking")
        self.assertEqual(command[command.index("--temp") + 1], "0.7")
        self.assertEqual(command[command.index("--top-p") + 1], "0.8")
        self.assertEqual(command[command.index("--top-k") + 1], "20")
        self.assertEqual(command[command.index("--min-p") + 1], "0.0")
        self.assertEqual(command[command.index("--presence-penalty") + 1], "1.5")
        self.assertEqual(command[command.index("--repeat-penalty") + 1], "1.0")

    def test_unknown_sampling_profile_is_rejected(self):
        with self.assertRaises(KeyError):
            bench_llm_runtime.build_command(
                "llama-server.exe", "qwen.gguf", "qwen-q4", 8081, "gpu",
                "Vulkan0", 6, 4096, "no-existe")


class LLMRuntimeHelperTests(unittest.TestCase):
    def test_sha256_file_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "modelo.gguf")
            with open(path, "wb") as handle:
                handle.write(b"pesos" * 5000)
            with open(path, "rb") as handle:
                expected = hashlib.sha256(handle.read()).hexdigest()
            self.assertEqual(bench_llm_runtime._sha256_file(path), expected)

    def test_process_memory_reports_counters_on_windows(self):
        memory = bench_llm_runtime._process_memory(os.getpid())
        if os.name != "nt":
            self.assertIsNone(memory)
            return
        self.assertIsInstance(memory, dict)
        self.assertIn("working_set_bytes", memory)
        self.assertGreater(memory["working_set_bytes"], 0)
        self.assertGreater(memory["peak_working_set_bytes"], 0)

    def test_wait_for_server_fails_when_the_server_dies(self):
        process = _FakeProcess()
        process.returncode = 3
        with self.assertRaises(RuntimeError) as caught:
            bench_llm_runtime._wait_for_server(process, 1, timeout=5)
        self.assertIn("3", str(caught.exception))

    def test_wait_for_server_times_out_without_a_healthy_server(self):
        with self.assertRaises(TimeoutError):
            bench_llm_runtime._wait_for_server(_FakeProcess(), 1, timeout=0.01)


class LLMRuntimeRunCandidateTests(unittest.TestCase):
    def _workspace(self, tmp):
        model_dir = os.path.join(tmp, "models")
        os.makedirs(model_dir)
        filename, _alias = bench_llm_runtime.CANDIDATES["qwen3.5-4b-q4"]
        model_path = os.path.join(model_dir, filename)
        with open(model_path, "wb") as handle:
            handle.write(b"qwen")
        server = os.path.join(tmp, "llama-server.exe")
        with open(server, "wb") as handle:
            handle.write(b"server")
        return model_dir, model_path, server

    def test_missing_model_file_is_reported_without_starting_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = bench_llm_runtime.run_candidate(
                "qwen3.5-4b-q4", os.path.join(tmp, "vacio"),
                "llama-server.exe", os.path.join(tmp, "reportes"))
        self.assertEqual(result["candidate"], "qwen3.5-4b-q4")
        self.assertEqual(result["error"], "No se encontró el archivo de modelo.")

    def test_run_candidate_records_the_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir, model_path, server = self._workspace(tmp)
            report_dir = os.path.join(tmp, "reportes")
            seen = {}

            def fake_run(command, **_kwargs):
                if len(command) > 1 and command[1] == "--version":
                    return subprocess.CompletedProcess(
                        command, 0, "build 9000", "")
                seen["bench_command"] = list(command)
                report = command[command.index("--report") + 1]
                with open(report, "w", encoding="utf-8") as handle:
                    json.dump({"model": "qwen3.5:4b-q4_K_M",
                               "normal": {"cases": []}}, handle)
                return subprocess.CompletedProcess(command, 0, "benchmark ok", "")

            with patch.object(bench_llm_runtime.subprocess, "run", fake_run), \
                    patch.object(bench_llm_runtime.subprocess, "Popen",
                                 return_value=_FakeProcess()), \
                    patch.object(bench_llm_runtime, "_wait_for_server",
                                 return_value=1.25), \
                    patch.object(bench_llm_runtime, "_process_memory",
                                 return_value={"working_set_bytes": 1234}):
                result = bench_llm_runtime.run_candidate(
                    "qwen3.5-4b-q4", model_dir, server, report_dir,
                    port=8099, backend="cpu", device="Vulkan0", threads=2,
                    context=2048, sampling_profile="bonsai-nonthinking",
                    case_ids=["caso-1", "caso-2"], run_label="rep-oficial")

            self.assertNotIn("error", result)
            self.assertEqual(result["candidate"], "qwen3.5-4b-q4")
            self.assertEqual(result["backend"], "cpu")
            self.assertEqual(result["device"], "CPU")
            self.assertEqual(result["port"], 8099)
            self.assertEqual(result["sampling_profile"], "bonsai-nonthinking")
            self.assertEqual(result["sampling_params"],
                             bench_llm_runtime.SAMPLING["bonsai-nonthinking"])
            self.assertEqual(result["run_label"], "rep-oficial")
            self.assertEqual(result["server_start_seconds"], 1.25)
            self.assertEqual(result["server_memory"], {"working_set_bytes": 1234})
            self.assertEqual(result["model_sha256"],
                             bench_llm_runtime._sha256_file(model_path))
            self.assertEqual(result["server_binary_sha256"],
                             bench_llm_runtime._sha256_file(server))
            self.assertEqual(result["server_build"], "build 9000")
            self.assertEqual(result["benchmark_exit_code"], 0)
            self.assertEqual(result["benchmark_output"], "benchmark ok")
            self.assertIn("metrics", result)

            report_path = result["report_path"]
            self.assertTrue(report_path.endswith(
                "qwen3.5-4b-q4-bonsai-nonthinking-rep-oficial-cpu.json"))
            self.assertTrue(os.path.isfile(result["server_log_path"]))

            with open(report_path, encoding="utf-8") as handle:
                written = json.load(handle)
            self.assertIn("runtime", written)
            self.assertEqual(written["runtime"]["run_label"], "rep-oficial")
            self.assertEqual(written["runtime"]["port"], 8099)
            self.assertEqual(written["runtime"]["server_memory"],
                             {"working_set_bytes": 1234})
            self.assertEqual(written["runtime"]["server_start_seconds"], 1.25)

            bench_command = seen["bench_command"]
            self.assertEqual(bench_command[bench_command.index("--url") + 1],
                             "http://127.0.0.1:8099")
            self.assertEqual(bench_command[bench_command.index("--sampling-profile") + 1],
                             "bonsai-nonthinking")
            self.assertEqual(bench_command[bench_command.index("--case-ids") + 1],
                             "caso-1,caso-2")

    def test_run_candidate_reports_a_server_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir, _model_path, server = self._workspace(tmp)
            report_dir = os.path.join(tmp, "reportes")

            def fake_run(command, **_kwargs):
                return subprocess.CompletedProcess(command, 0, "build 9000", "")

            with patch.object(bench_llm_runtime.subprocess, "run", fake_run), \
                    patch.object(bench_llm_runtime.subprocess, "Popen",
                                 return_value=_FakeProcess()), \
                    patch.object(bench_llm_runtime, "_wait_for_server",
                                 side_effect=RuntimeError("no arranco")):
                result = bench_llm_runtime.run_candidate(
                    "qwen3.5-4b-q4", model_dir, server, report_dir)

            self.assertEqual(result["error"], "RuntimeError")
            self.assertEqual(result["message"], "no arranco")
            self.assertEqual(result["sampling_profile"], "matched-deterministic")
            self.assertIn("server_command", result)
            self.assertIn("benchmark_command", result)


if __name__ == "__main__":
    unittest.main()
