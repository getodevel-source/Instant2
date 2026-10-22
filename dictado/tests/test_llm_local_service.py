"""El modelo de lenguaje local descargado deja el servicio local funcionando.

Antes, el panel guardaba solo la ruta del archivo: el daemon no encontraba
runtime ni dirección y el usuario se quedaba con un final de camino (3 GB
descargados y "No hay un servicio de lenguaje configurado").

Estos tests fijan el estado final:

- después de la descarga, la config tiene runtime, dirección, alias y modelo;
- el daemon acepta esa configuración (su puerta de paso);
- al día siguiente, la config sola completa lo que falta.
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

from instant2_app import config, daemon, gui, llm_model, models

BODY = b"gguf-de-prueba-" * 100  # ~1.6 KB


class _Handler(http.server.BaseHTTPRequestHandler):
    """Sirve el payload de prueba."""

    def do_GET(self):
        data = self.server.payload
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


class _Server:
    def __init__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.payload = BODY
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/modelo.gguf"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


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


def _frozen_app(root):
    """App instalada simulada: la app y el runtime de llama.cpp."""
    app_dir = os.path.join(root, "Instant2")
    runtime = os.path.join(app_dir, "llm-runtime")
    os.makedirs(runtime)
    server = os.path.join(runtime, "llama-server.exe")
    with open(server, "wb") as handle:
        handle.write(b"stub")
    return app_dir, server


def _make_logic(case, data_dir, app_dir, config_path):
    emitted = []
    cfg = dict(config.DEFAULTS)
    case._hold("instant2_app.gui.config.load", return_value=cfg)
    case._hold("instant2_app.config.config_path", return_value=config_path)
    case._hold("instant2_app.llm_defaults.app_dir", return_value=app_dir)
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
    return logic, emitted


def _disk_config(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _daemon_gate(cfg):
    """La puerta del daemon: (config del runtime, estado estático)."""
    return daemon.Daemon._configured_llm_runtime(SimpleNamespace(cfg=cfg))


class LocalLLMWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Server()
        cls.addClassCleanup(cls.server.close)

    def setUp(self):
        self.expected = (len(BODY), hashlib.sha256(BODY).hexdigest())
        for item in (patch.object(llm_model, "LLM_MODEL_EXPECTED", self.expected),
                     patch.object(llm_model, "LLM_MODEL_URL", self.server.url)):
            item.start()
            self.addCleanup(item.stop)
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.app_dir, self.server_path = _frozen_app(self.root)
        self.data_dir = os.path.join(self.root, "models")
        os.makedirs(self.data_dir)
        self.config_path = os.path.join(self.root, "config.json")

    def _hold(self, target, **kwargs):
        """Parche que vive hasta el final del test."""
        patcher = patch(target, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher

    def test_after_the_download_the_local_service_is_configured(self):
        logic, _emitted = _make_logic(self, self.data_dir, self.app_dir,
                                      self.config_path)
        logic.handle({"op": "download_llm_model"})
        _fn, on_result, _on_error, _on_progress = logic.tasks.submitted[0]
        logic.tasks.submitted[0][0](lambda payload: None)
        on_result(None)

        disk = _disk_config(self.config_path)
        model = os.path.join(self.data_dir, "llm",
                             "Qwen_Qwen3.5-4B-Q4_K_M.gguf")
        self.assertTrue(os.path.isfile(model))
        self.assertEqual(disk["llm_server_path"], self.server_path)
        self.assertEqual(disk["llm_url"], "http://127.0.0.1:8080/v1")
        self.assertEqual(disk["llm_model"], "qwen3.5:4b-q4_K_M")
        self.assertEqual(disk["llm_model_path"], model)

        # El daemon tiene que aceptar esa configuración: si no, el usuario
        # descargó 3 GB para nada.
        with patch("instant2_app.config.config_path", return_value=self.config_path):
            cfg = config.load()
        runtime_cfg, static_state = _daemon_gate(cfg)
        self.assertIsNone(static_state)
        self.assertIsNotNone(runtime_cfg)
        self.assertEqual(runtime_cfg["llm_server_path"], self.server_path)
        self.assertEqual(runtime_cfg["llm_model_path"], model)

    def test_the_next_launch_completes_a_half_configured_model(self):
        """El caso que dejó el panel viejo: solo la ruta del modelo."""
        model = os.path.join(self.data_dir, "llm",
                             "Qwen_Qwen3.5-4B-Q4_K_M.gguf")
        os.makedirs(os.path.dirname(model), exist_ok=True)
        with open(model, "wb") as handle:
            handle.write(BODY)
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({"llm_model_path": model}, handle)

        with patch("instant2_app.config.config_path", return_value=self.config_path), \
                patch("instant2_app.paths.resolve_data_dir", return_value=self.data_dir), \
                patch("instant2_app.llm_defaults.app_dir", return_value=self.app_dir):
            cfg = config.load()

        self.assertEqual(cfg["llm_server_path"], self.server_path)
        self.assertEqual(cfg["llm_url"], "http://127.0.0.1:8080/v1")
        self.assertEqual(cfg["llm_model"], "qwen3.5:4b-q4_K_M")
        self.assertTrue(cfg["llm_bundle_initialized"])

        runtime_cfg, static_state = _daemon_gate(cfg)
        self.assertIsNone(static_state)
        self.assertIsNotNone(runtime_cfg)

    def test_a_download_that_never_happened_leaves_the_service_unconfigured(self):
        """Sin modelo no hay servicio: el daemon lo dice, sin inventar nada."""
        with patch("instant2_app.config.config_path", return_value=self.config_path), \
                patch("instant2_app.paths.resolve_data_dir", return_value=self.data_dir), \
                patch("instant2_app.llm_defaults.app_dir", return_value=self.app_dir):
            cfg = config.load()
        self.assertEqual(cfg["llm_url"], "")
        self.assertEqual(cfg["llm_server_path"], "")
        self.assertFalse(cfg["llm_bundle_initialized"])
        runtime_cfg, static_state = _daemon_gate(cfg)
        self.assertIsNone(runtime_cfg)
        self.assertIsNotNone(static_state)
        self.assertEqual(static_state["status"], "needs_setup")

    def test_the_download_only_writes_the_keys_it_changed(self):
        """La descarga no persiste la config entera de la interfaz."""
        logic, _emitted = _make_logic(self, self.data_dir, self.app_dir,
                                      self.config_path)
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({**config.DEFAULTS, "key": "f10", "autostart": True},
                      handle)
        logic.cfg["key"] = "f11"
        logic.handle({"op": "download_llm_model"})
        logic.tasks.submitted[0][0](lambda payload: None)
        logic.tasks.submitted[0][1](None)

        disk = _disk_config(self.config_path)
        # Lo que el usuario tenía y no confirmó sigue intacto.
        self.assertEqual(disk["key"], "f10")
        self.assertIs(disk["autostart"], True)
        # Y lo de la descarga está.
        self.assertEqual(disk["llm_model_path"], os.path.join(
            self.data_dir, "llm", "Qwen_Qwen3.5-4B-Q4_K_M.gguf"))
        self.assertEqual(disk["llm_url"], "http://127.0.0.1:8080/v1")

    def test_the_download_leaves_the_review_turned_on_by_itself(self):
        """Qwen se activa solo: descargar el modelo es activarlo."""
        logic, _emitted = _make_logic(self, self.data_dir, self.app_dir,
                                      self.config_path)
        logic.handle({"op": "download_llm_model"})
        logic.tasks.submitted[0][0](lambda payload: None)
        logic.tasks.submitted[0][1](None)

        disk = _disk_config(self.config_path)
        self.assertIs(disk["llm_normal_enabled"], True)
        self.assertIs(disk["llm_bundle_initialized"], True)
        self.assertIs(logic.cfg["llm_normal_enabled"], True)

        # Y el daemon lo usa de verdad: servicio local administrable.
        runtime_cfg, static_state = _daemon_gate(logic.cfg)
        self.assertIsNone(static_state)
        self.assertIsNotNone(runtime_cfg)

    def test_a_review_that_was_turned_off_is_not_turned_on_by_the_download(self):
        """La decisión explícita de la persona manda: no se enciende sola."""
        logic, _emitted = _make_logic(self, self.data_dir, self.app_dir,
                                      self.config_path)
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({**config.DEFAULTS, "llm_normal_enabled": False}, handle)
        logic.cfg["llm_normal_enabled"] = False
        logic.handle({"op": "download_llm_model"})
        logic.tasks.submitted[0][0](lambda payload: None)
        logic.tasks.submitted[0][1](None)

        disk = _disk_config(self.config_path)
        self.assertIs(disk["llm_normal_enabled"], False)
        self.assertIs(logic.cfg["llm_normal_enabled"], False)
        # Igual queda conectado: reactivarlo es un solo interruptor.
        self.assertEqual(disk["llm_url"], "http://127.0.0.1:8080/v1")
        self.assertEqual(disk["llm_model_path"], os.path.join(
            self.data_dir, "llm", "Qwen_Qwen3.5-4B-Q4_K_M.gguf"))

    def test_the_running_daemon_is_restarted_to_pick_the_service_up(self):
        """El daemon lee la config al arrancar: hay que volverlo a levantar."""
        logic, _emitted = _make_logic(self, self.data_dir, self.app_dir,
                                      self.config_path)
        restarts = []
        logic.stop_daemon_flow = lambda restart=False, on_restart=None: (
            restarts.append(restart) or True)
        logic._last_daemon_running = True
        logic.handle({"op": "download_llm_model"})
        logic.tasks.submitted[0][0](lambda payload: None)
        logic.tasks.submitted[0][1](None)
        self.assertEqual(restarts, [True])

    def test_a_daemon_that_is_not_running_is_not_restarted(self):
        """Si el dictado no está corriendo, arrancará con la config nueva."""
        logic, _emitted = _make_logic(self, self.data_dir, self.app_dir,
                                      self.config_path)
        restarts = []
        logic.stop_daemon_flow = lambda restart=False, on_restart=None: (
            restarts.append(restart) or True)
        logic._last_daemon_running = False
        logic.handle({"op": "download_llm_model"})
        logic.tasks.submitted[0][0](lambda payload: None)
        logic.tasks.submitted[0][1](None)
        self.assertEqual(restarts, [])

    def test_a_review_that_was_turned_off_does_not_restart_the_daemon(self):
        logic, _emitted = _make_logic(self, self.data_dir, self.app_dir,
                                      self.config_path)
        restarts = []
        logic.stop_daemon_flow = lambda restart=False, on_restart=None: (
            restarts.append(restart) or True)
        logic._last_daemon_running = True
        logic.cfg["llm_normal_enabled"] = False
        logic.handle({"op": "download_llm_model"})
        logic.tasks.submitted[0][0](lambda payload: None)
        logic.tasks.submitted[0][1](None)
        self.assertEqual(restarts, [])


class LocalLLMVerificationTests(unittest.TestCase):
    """Un GGUF de 3 GB con el tamaño justo puede estar corrupto."""

    def setUp(self):
        self.expected = (len(BODY), hashlib.sha256(BODY).hexdigest())
        for item in (patch.object(llm_model, "LLM_MODEL_EXPECTED", self.expected),):
            item.start()
            self.addCleanup(item.stop)
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        self.model = llm_model.model_path(self.data_dir)
        os.makedirs(os.path.dirname(self.model), exist_ok=True)

    def test_a_file_with_the_right_size_but_broken_content_is_not_verified(self):
        with open(self.model, "wb") as handle:
            handle.write(b"x" * len(BODY))
        self.assertTrue(llm_model.model_ready(self.data_dir))
        self.assertTrue(llm_model.needs_verification(self.data_dir))
        self.assertFalse(llm_model.model_ready(self.data_dir, verify=True))
        self.assertTrue(llm_model.needs_verification(self.data_dir))

    def test_a_verified_file_does_not_need_the_hash_again(self):
        with open(self.model, "wb") as handle:
            handle.write(BODY)
        self.assertTrue(llm_model.model_ready(self.data_dir, verify=True))
        self.assertFalse(llm_model.needs_verification(self.data_dir))


if __name__ == "__main__":
    unittest.main()
