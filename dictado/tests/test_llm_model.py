"""El modelo de lenguaje local (3 GB) se descarga a pedido y se verifica.

La suite no baja los 3 GB reales: contra un servidor HTTP local con un
archivo de unos KB se fijan la semántica de la descarga (reanudable,
verificada, cancelable) y el estado que ve el panel.
"""
import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import config, gui, llm_model, models

BODY = b"gguf-de-prueba-" * 100  # ~1.6 KB
# Los valores reales del módulo, antes de que los tests los reemplacen.
REAL_EXPECTED = llm_model.LLM_MODEL_EXPECTED
REAL_URL = llm_model.LLM_MODEL_URL


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Sirve el payload de prueba y respeta Range con 206."""

    disable_nagle_algorithm = True

    def do_GET(self):
        data = self.server.payload
        rng = self.headers.get("Range")
        self.server.ranges.append(rng)
        if rng and rng.startswith("bytes="):
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            body = data[start:]
            self.send_response(206)
            self.send_header(
                "Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _Server:
    def __init__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        self.httpd.payload = BODY
        self.httpd.ranges = []
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/modelo.gguf"

    @property
    def ranges(self):
        return self.httpd.ranges

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class LLMModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Server()
        cls.addClassCleanup(cls.server.close)

    def setUp(self):
        self.server.httpd.payload = BODY
        self.server.ranges.clear()
        self.expected = (len(BODY), hashlib.sha256(BODY).hexdigest())
        for item in (patch.object(llm_model, "LLM_MODEL_EXPECTED", self.expected),
                     patch.object(llm_model, "LLM_MODEL_URL", self.server.url)):
            item.start()
            self.addCleanup(item.stop)
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)

    def test_the_pinned_values_are_the_ones_the_bundler_used(self):
        self.assertEqual(llm_model.LLM_SUBDIR, "llm")
        self.assertEqual(llm_model.LLM_MODEL_NAME, "Qwen_Qwen3.5-4B-Q4_K_M.gguf")
        self.assertEqual(REAL_EXPECTED[0], 3_013_027_808)
        self.assertEqual(
            REAL_EXPECTED[1],
            "13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983")
        self.assertEqual(
            REAL_URL,
            "https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF/resolve/"
            "ba06320255db2dbec194dad738d066be90dabf29/"
            "Qwen_Qwen3.5-4B-Q4_K_M.gguf")

    def test_the_model_lives_in_the_data_directory(self):
        self.assertEqual(
            llm_model.model_path(self.data_dir),
            os.path.join(self.data_dir, "llm", "Qwen_Qwen3.5-4B-Q4_K_M.gguf"))

    def test_not_ready_without_the_file(self):
        self.assertFalse(llm_model.model_ready(self.data_dir))

    def test_ready_only_with_the_expected_size(self):
        target = llm_model.model_path(self.data_dir)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(b"corto")
        self.assertFalse(llm_model.model_ready(self.data_dir))
        with open(target, "wb") as handle:
            handle.write(BODY)
        self.assertTrue(llm_model.model_ready(self.data_dir))
        # Con verificación completa también da bien (el hash coincide).
        self.assertTrue(llm_model.model_ready(self.data_dir, verify=True))

    def test_the_download_publishes_the_file_after_verifying_it(self):
        events = []
        llm_model.ensure_model(self.data_dir, progress=lambda *a: events.append(a))
        target = llm_model.model_path(self.data_dir)
        self.assertTrue(os.path.isfile(target))
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), BODY)
        self.assertTrue(events)
        self.assertEqual(events[-1], (len(BODY), len(BODY)))
        self.assertFalse(os.path.isfile(target + ".part"))

    def test_a_file_that_does_not_verify_is_not_published(self):
        self.server.httpd.payload = b"contenido equivocado"
        with patch.object(models, "DOWNLOAD_ATTEMPTS", 1):
            with self.assertRaises(models.ModelIntegrityError):
                llm_model.ensure_model(self.data_dir)
        target = llm_model.model_path(self.data_dir)
        self.assertFalse(os.path.isfile(target))
        self.assertFalse(os.path.isfile(target + ".part"))

    def test_a_corrupt_file_is_downloaded_again(self):
        target = llm_model.model_path(self.data_dir)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(b"truncado")
        llm_model.ensure_model(self.data_dir)
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), BODY)

    def test_the_download_resumes_with_range(self):
        target = llm_model.model_path(self.data_dir)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target + ".part", "wb") as handle:
            handle.write(BODY[: len(BODY) // 2])
        llm_model.ensure_model(self.data_dir)
        self.assertIn(f"bytes={len(BODY) // 2}-", self.server.ranges)
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), BODY)

    def test_cancel_keeps_the_partial_file_and_publishes_nothing(self):
        cancel = threading.Event()

        def stop_after_first_chunk(done, _total):
            if done:
                cancel.set()

        with patch.object(models, "CHUNK", 64):
            with self.assertRaises(models.DownloadCancelled):
                llm_model.ensure_model(self.data_dir, progress=stop_after_first_chunk,
                                       cancel_event=cancel)
        target = llm_model.model_path(self.data_dir)
        self.assertFalse(os.path.isfile(target))
        self.assertTrue(os.path.isfile(target + ".part"))

    def test_it_refuses_to_start_without_disk_space(self):
        class _Usage:
            free = 1024
        with patch("shutil.disk_usage", return_value=_Usage()):
            with self.assertRaises(models.ModelIntegrityError) as caught:
                llm_model.ensure_model(self.data_dir)
        self.assertIn("espacio", str(caught.exception))
        self.assertEqual(self.server.ranges, [])


class PendingTasks:
    """Guarda las tareas sin ejecutarlas: el estado queda "en curso"."""

    def __init__(self):
        self.submitted = []
        self.on_idle = None

    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        self.submitted.append((fn, on_result, on_error, on_progress))
        return id(fn)

    def drain(self):
        return 0

    def close(self):
        self.submitted = []


def make_logic(case, data_dir):
    """Panel de prueba: sin red, sin Qt y con las tareas en suspenso.

    Los parches viven hasta el final del test (no solo durante la
    construcción del panel) y la config se escribe en un archivo temporal:
    la suite nunca toca la config real de la maquina.
    """
    emitted = []
    cfg = dict(config.DEFAULTS)
    config_path = os.path.join(data_dir, "config.json")
    case._hold("instant2_app.gui.config.load", return_value=cfg)
    case._hold("instant2_app.config.config_path", return_value=config_path)
    case._hold("instant2_app.gui.models.check",
               return_value={"parakeet": True, "vad": True})
    case._hold("instant2_app.gui.resolve_data_dir", return_value=data_dir)
    case._hold("instant2_app.gui.daemon_is_running", return_value=False)
    case._hold("instant2_app.gui.audio.input_choices", return_value=[])
    case._hold("instant2_app.gui.autostart.is_enabled", return_value=False)
    modules = patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
        default=SimpleNamespace(device=(-1, -1)))})
    modules.start()
    case.addCleanup(modules.stop)
    logic = gui.PanelLogic(
        emit=lambda kind, payload: emitted.append((kind, payload)),
        tasks=PendingTasks(), schedule=lambda ms, fn: None)
    return logic, emitted, config_path


def _disk_config(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class PanelLLMModelStateTests(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)

    def _hold(self, target, **kwargs):
        """Parche que vive hasta el final del test."""
        patcher = patch(target, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher

    def test_the_panel_starts_with_the_model_missing(self):
        logic, _emitted, _path = make_logic(self, self.data_dir)
        state = logic.state_payload()["llm_model"]
        self.assertEqual(state["status"], "missing")
        self.assertFalse(state["ready"])
        self.assertFalse(state["downloading"])
        self.assertIn("No descargado", state["message"])

    def test_the_download_does_not_start_by_itself(self):
        logic, _emitted, _path = make_logic(self, self.data_dir)
        state = logic.state_payload()["llm_model"]
        self.assertEqual(state["status"], "missing")
        self.assertFalse(state["downloading"])
        self.assertFalse(state["ready"])

    def test_the_explicit_action_starts_the_download(self):
        logic, _emitted, _path = make_logic(self, self.data_dir)
        logic.handle({"op": "download_llm_model"})
        state = logic.state_payload()["llm_model"]
        self.assertEqual(state["status"], "downloading")
        self.assertTrue(state["downloading"])
        self.assertEqual(len(logic.tasks.submitted), 1)

    def test_the_progress_reaches_the_state(self):
        logic, _emitted, _path = make_logic(self, self.data_dir)
        logic.handle({"op": "download_llm_model"})
        _fn, _on_result, _on_error, on_progress = logic.tasks.submitted[0]
        on_progress((512 * 1024 * 1024, 3_013_027_808))
        state = logic.state_payload()["llm_model"]
        self.assertEqual(state["percent"], 17)
        self.assertIn("Descargando", state["message"])

    def test_the_state_is_ready_when_the_download_finishes(self):
        logic, _emitted, _path = make_logic(self, self.data_dir)
        logic.handle({"op": "download_llm_model"})
        _fn, on_result, _on_error, _on_progress = logic.tasks.submitted[0]
        with patch("instant2_app.llm_model.model_ready", return_value=True):
            on_result(None)
        state = logic.state_payload()["llm_model"]
        self.assertEqual(state["status"], "ready")
        self.assertTrue(state["ready"])
        self.assertEqual(state["percent"], 100)

    def test_a_cancelled_download_keeps_the_partial_file(self):
        logic, _emitted, _path = make_logic(self, self.data_dir)
        logic.handle({"op": "download_llm_model"})
        _fn, _on_result, on_error, _on_progress = logic.tasks.submitted[0]
        on_error(models.DownloadCancelled("cancelada"))
        state = logic.state_payload()["llm_model"]
        self.assertEqual(state["status"], "missing")
        self.assertFalse(state["downloading"])
        self.assertIn("Descarga cancelada", state["message"])

    def test_a_failed_download_is_reported(self):
        logic, _emitted, _path = make_logic(self, self.data_dir)
        logic.handle({"op": "download_llm_model"})
        _fn, _on_result, on_error, _on_progress = logic.tasks.submitted[0]
        on_error(models.ModelIntegrityError("sin red"))
        state = logic.state_payload()["llm_model"]
        self.assertEqual(state["status"], "error")
        self.assertFalse(state["downloading"])

    def test_the_cancel_button_stops_the_download(self):
        logic, _emitted, _path = make_logic(self, self.data_dir)
        logic.handle({"op": "download_llm_model"})
        logic.handle({"op": "cancel_llm_model"})
        state = logic.state_payload()["llm_model"]
        self.assertTrue(state["canceling"])
        self.assertTrue(logic._llm_model_cancel_event.is_set())

    def test_the_downloaded_model_is_wired_when_the_user_chosen_none(self):
        logic, _emitted, config_path = make_logic(self, self.data_dir)
        model = os.path.join(self.data_dir, "llm",
                             "Qwen_Qwen3.5-4B-Q4_K_M.gguf")
        os.makedirs(os.path.dirname(model), exist_ok=True)
        with open(model, "wb") as handle:
            handle.write(b"gguf")
        logic._use_downloaded_llm_model()
        disk = _disk_config(config_path)
        self.assertEqual(disk["llm_model_path"], model)
        self.assertEqual(logic.cfg["llm_model_path"], model)

    def test_a_model_chosen_by_hand_is_not_replaced(self):
        logic, _emitted, config_path = make_logic(self, self.data_dir)
        chosen = os.path.join(self.data_dir, "otro.gguf")
        with open(chosen, "wb") as handle:
            handle.write(b"x")
        logic.cfg["llm_model_path"] = chosen
        logic._use_downloaded_llm_model()
        self.assertEqual(logic.cfg["llm_model_path"], chosen)
        # El archivo de la config tampoco se toco.
        self.assertFalse(os.path.exists(config_path))

if __name__ == "__main__":
    unittest.main()
