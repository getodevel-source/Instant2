"""Lifecycle and privacy contracts for the managed local LLM process."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app.llm_runtime import (  # noqa: E402
    LocalLLMRuntime, RuntimeConfigError, cpu_memory_error,
    cpu_memory_required_bytes, gpu_memory_required_mib, health_url,
    is_local_endpoint, is_remote_endpoint, local_runtime_target, parse_devices,
)


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Process:
    def __init__(self, code=None):
        self.code = code
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0

    def kill(self):
        self.killed = True
        self.code = -9

    def wait(self, timeout=None):
        self.waited = True
        return self.code


class LocalLLMRuntimeTests(unittest.TestCase):
    def test_idle_sleep_flag_is_enabled_only_when_supported(self):
        base = {"llm_model": "qwen-local", "llm_idle_sleep_seconds": 300}
        args = LocalLLMRuntime._args(
            "llama-server", "model.gguf", "127.0.0.1", 8080, base,
            sleep_supported=True)
        self.assertIn("--parallel", args)
        self.assertEqual(args[args.index("--parallel") + 1], "1")
        self.assertEqual(args[args.index("--sleep-idle-seconds") + 1], "300")

        resident = LocalLLMRuntime._args(
            "llama-server", "model.gguf", "127.0.0.1", 8080,
            {**base, "llm_idle_sleep_seconds": 0}, sleep_supported=True)
        self.assertNotIn("--sleep-idle-seconds", resident)
        unsupported = LocalLLMRuntime._args(
            "llama-server", "model.gguf", "127.0.0.1", 8080, base,
            sleep_supported=False)
        self.assertNotIn("--sleep-idle-seconds", unsupported)

    def test_sleeping_model_uses_a_longer_wake_timeout(self):
        runtime = LocalLLMRuntime()
        runtime._target_url = "http://127.0.0.1:8080/v1"
        runtime._last_state = {"status": "sleeping"}
        with patch.object(runtime, "_is_sleeping", return_value=True):
            self.assertEqual(runtime.request_timeout({}), 60.0)
        runtime._last_state = {"status": "ready"}
        with patch.object(runtime, "_is_sleeping", return_value=False):
            self.assertEqual(runtime.request_timeout({}), 8.0)

    def test_endpoint_scope_distinguishes_local_remote_and_invalid(self):
        self.assertTrue(is_local_endpoint("http://127.0.0.1:8080/v1"))
        self.assertTrue(is_local_endpoint("http://localhost:8080/v1"))
        self.assertFalse(is_local_endpoint("https://llm.example.test/v1"))
        self.assertFalse(is_local_endpoint("not a URL"))
        self.assertTrue(is_remote_endpoint("https://llm.example.test/v1"))
        self.assertFalse(is_remote_endpoint("http://127.0.0.1:8080/v1"))
        self.assertFalse(is_remote_endpoint("not a URL"))

    def test_local_endpoint_validation_and_health_route(self):
        self.assertEqual(local_runtime_target("http://127.0.0.1:8080/v1"),
                         ("127.0.0.1", 8080))
        self.assertEqual(local_runtime_target("http://[::1]:9000"), ("::1", 9000))
        self.assertEqual(health_url("http://127.0.0.1:8080/v1"),
                         "http://127.0.0.1:8080/health")
        with self.assertRaises(RuntimeConfigError):
            local_runtime_target("https://example.com/v1")

    def test_device_picker_prefers_free_vram_and_skips_tight_devices(self):
        output = ("Available devices:\n"
                  "  Vulkan0: integrated (8192 MiB, 1200 MiB free)\n"
                  "  Vulkan1: discrete (16368 MiB, 15000 MiB free)\n")
        self.assertEqual(parse_devices(output), ["Vulkan1"])
        self.assertEqual(parse_devices("CUDA0: GPU\n"), ["CUDA0"])

    def test_device_picker_uses_model_size_for_vram_headroom(self):
        model_size = 4 * 1024 ** 3
        required_mib = gpu_memory_required_mib(model_size)
        self.assertEqual(required_mib, 5428)
        output = ("Available devices:\n"
                  f"  CUDA0: GPU (8192 MiB, {required_mib - 1} MiB free)\n"
                  f"  CUDA1: GPU (8192 MiB, {required_mib} MiB free)\n")
        self.assertEqual(parse_devices(output, min_free_mib=required_mib), ["CUDA1"])
        self.assertGreaterEqual(gpu_memory_required_mib(1), 1536)

    def test_runtime_passes_model_aware_vram_estimate_to_device_picker(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = os.path.join(directory, "llama-server.exe")
            model = os.path.join(directory, "model.gguf")
            for path in (executable, model):
                with open(path, "wb") as handle:
                    handle.write(b"fixture model")
            health_calls = 0

            def opener(*_args, **_kwargs):
                nonlocal health_calls
                health_calls += 1
                if health_calls == 1:
                    raise URLError("not running yet")
                return Response()

            selected_thresholds = []
            runtime = LocalLLMRuntime(
                popen=lambda *_args, **_kwargs: Process(), opener=opener,
                sleep=lambda _seconds: None,
                available_memory=lambda: 8 * 1024 ** 3,
            )
            runtime._devices = lambda _executable, min_free_mib: (
                selected_thresholds.append(min_free_mib) or ["Vulkan0"])
            state = runtime.start({
                "llm_url": "http://127.0.0.1:8080/v1",
                "llm_server_path": executable,
                "llm_model_path": model,
                "llm_acceleration": "auto",
            }, timeout=0.1)
            self.assertEqual(state["status"], "ready")
            self.assertEqual(selected_thresholds,
                             [gpu_memory_required_mib(os.path.getsize(model))])
            runtime.stop()

    def test_cpu_memory_estimate_keeps_context_and_desktop_reserve(self):
        model_size = 2 * 1024 ** 3
        required = cpu_memory_required_bytes(model_size)
        self.assertGreater(required, model_size + 1024 ** 3)
        self.assertEqual(cpu_memory_error(model_size, required), "")
        self.assertIn("RAM libre", cpu_memory_error(model_size, required - 1))
        self.assertEqual(cpu_memory_error(model_size, None), "")
        self.assertIn("RAM libre", cpu_memory_error(model_size, 0))

    def test_existing_local_endpoint_is_reused_and_never_stopped(self):
        runtime = LocalLLMRuntime(opener=lambda *_args, **_kwargs: Response())
        state = runtime.start({"llm_url": "http://127.0.0.1:8080/v1"})
        self.assertEqual(state["status"], "ready")
        self.assertFalse(state["owned"])
        self.assertIsNone(runtime.process)
        runtime.stop()
        self.assertEqual(runtime.state()["status"], "disabled")

    def test_acceleration_failure_falls_back_to_cpu_without_private_args(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = os.path.join(directory, "llama-server.exe")
            model = os.path.join(directory, "model.gguf")
            for path in (executable, model):
                with open(path, "wb") as handle:
                    handle.write(b"fixture")
            processes = [Process(code=1), Process()]
            popen_calls = []

            def popen(args, **kwargs):
                popen_calls.append((args, kwargs))
                return processes[len(popen_calls) - 1]

            health_calls = 0

            def opener(*_args, **_kwargs):
                nonlocal health_calls
                health_calls += 1
                if health_calls == 1:
                    raise URLError("offline")
                return Response()

            devices = type("Devices", (), {
                "returncode": 0,
                "stdout": "Available devices:\n Vulkan0: GPU (16000 MiB, 14000 MiB free)\n",
            })()
            with patch("instant2_app.llm_runtime.subprocess.run", return_value=devices):
                runtime = LocalLLMRuntime(popen=popen, opener=opener,
                                          sleep=lambda _seconds: None,
                                          available_memory=lambda: 16 * 1024 ** 3)
                state = runtime.start({
                    "llm_url": "http://127.0.0.1:8080/v1",
                    "llm_server_path": executable,
                    "llm_model_path": model,
                    "llm_model": "qwen-local",
                }, timeout=0.1)

            self.assertEqual(state["status"], "ready")
            self.assertEqual(state["device"], "CPU")
            self.assertTrue(processes[0].terminated is False)
            self.assertIn("--device", popen_calls[0][0])
            self.assertIn("Vulkan0", popen_calls[0][0])
            self.assertIn("--device", popen_calls[1][0])
            self.assertIn("none", popen_calls[1][0])
            for args, kwargs in popen_calls:
                self.assertIn("127.0.0.1", args)
                self.assertNotIn("private transcript", args)
                self.assertIs(kwargs["stdin"], __import__("subprocess").DEVNULL)
                self.assertIs(kwargs["stdout"], __import__("subprocess").DEVNULL)
                self.assertIs(kwargs["stderr"], __import__("subprocess").DEVNULL)
                self.assertIn("--log-disable", args)
            runtime.stop()
            self.assertTrue(processes[1].terminated)

    def test_missing_files_are_reported_without_spawning_or_downloading(self):
        runtime = LocalLLMRuntime(opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")))
        state = runtime.start({
            "llm_managed_enabled": True,
            "llm_url": "http://127.0.0.1:8080/v1",
            "llm_server_path": "missing-server",
            "llm_model_path": "missing-model.gguf",
        })
        self.assertEqual(state["status"], "needs_setup")
        self.assertIn("Elegí", state["detail"])
        self.assertIsNone(runtime.process)

    def test_saved_model_change_restarts_only_the_owned_server(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = os.path.join(directory, "llama-server")
            model_a = os.path.join(directory, "a.gguf")
            model_b = os.path.join(directory, "b.gguf")
            for path in (executable, model_a, model_b):
                with open(path, "wb") as handle:
                    handle.write(b"fixture")
            processes = [Process(), Process()]
            calls = []

            def popen(args, **kwargs):
                calls.append(args)
                return processes[len(calls) - 1]

            health_calls = 0

            def opener(*_args, **_kwargs):
                nonlocal health_calls
                health_calls += 1
                if health_calls == 1:
                    raise URLError("not running yet")
                return Response()

            runtime = LocalLLMRuntime(popen=popen, opener=opener,
                                      sleep=lambda _seconds: None,
                                      available_memory=lambda: 16 * 1024 ** 3)
            base = {"llm_url": "http://127.0.0.1:8080/v1",
                    "llm_server_path": executable,
                    "llm_acceleration": "cpu"}
            self.assertTrue(runtime.start({**base, "llm_model_path": model_a})["owned"])
            self.assertTrue(runtime.start({**base, "llm_model_path": model_b})["owned"])
            self.assertTrue(processes[0].terminated)
            self.assertIs(runtime.process, processes[1])
            runtime.stop()
            self.assertTrue(processes[1].terminated)

    def test_low_ram_blocks_cpu_start_without_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = os.path.join(directory, "llama-server.exe")
            model = os.path.join(directory, "large.gguf")
            for path in (executable, model):
                with open(path, "wb") as handle:
                    handle.write(b"fixture")
            popen_calls = []
            runtime = LocalLLMRuntime(
                popen=lambda *args, **kwargs: popen_calls.append((args, kwargs)),
                opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
                available_memory=lambda: 512 * 1024 ** 2,
            )
            state = runtime.start({
                "llm_url": "http://127.0.0.1:8080/v1",
                "llm_server_path": executable,
                "llm_model_path": model,
                "llm_acceleration": "cpu",
            })
            self.assertEqual(state["status"], "error")
            self.assertIn("RAM libre", state["detail"])
            self.assertEqual(popen_calls, [])
            self.assertIsNone(runtime.process)

    def test_low_ram_skips_cpu_after_failed_gpu_start(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = os.path.join(directory, "llama-server.exe")
            model = os.path.join(directory, "model.gguf")
            for path in (executable, model):
                with open(path, "wb") as handle:
                    handle.write(b"fixture")
            gpu_process = Process(code=1)
            popen_calls = []
            devices = type("Devices", (), {
                "returncode": 0,
                "stdout": "Available devices:\n Vulkan0: GPU (16000 MiB, 14000 MiB free)\n",
            })()
            runtime = LocalLLMRuntime(
                popen=lambda args, **kwargs: popen_calls.append((args, kwargs)) or gpu_process,
                opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
                available_memory=lambda: 512 * 1024 ** 2,
            )
            with patch("instant2_app.llm_runtime.subprocess.run", return_value=devices):
                state = runtime.start({
                    "llm_url": "http://127.0.0.1:8080/v1",
                    "llm_server_path": executable,
                    "llm_model_path": model,
                    "llm_model": "qwen-local",
                    "llm_acceleration": "auto",
                }, timeout=0.1)
            self.assertEqual(state["status"], "error")
            self.assertIn("No se pudo iniciar con GPU", state["detail"])
            self.assertIn("RAM libre", state["detail"])
            self.assertEqual(len(popen_calls), 1)
            self.assertIn("Vulkan0", popen_calls[0][0])

if __name__ == "__main__":
    unittest.main()
