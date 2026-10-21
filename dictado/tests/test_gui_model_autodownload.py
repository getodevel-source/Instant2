"""El panel arranca solo la descarga del reconocimiento (una vez por instalación).

El instalador de Windows ya no lleva los pesos, así que la primera vez el
panel tiene que bajarlos solo. Estos tests fijan que:

- la descarga arranca sola cuando los modelos faltan;
- no vuelve a arrancar sola (ni después de un fallo o una cancelación);
- el botón "Reintentar descarga" sigue funcionando;
- no arranca con el panel cerrándose ni en el modo de diagnóstico.
"""
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import gui, models


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


def make_logic(data_dir, page="home", models_ready=False, **kwargs):
    """Panel de prueba: sin red, sin Qt y con las tareas en suspenso."""
    emitted = []
    scheduled = []
    checks = {"parakeet": models_ready, "vad": models_ready}

    def schedule(_milliseconds, fn):
        scheduled.append(fn)

    with patch("instant2_app.gui.config.load", return_value={}), \
            patch("instant2_app.gui.models.check", return_value=dict(checks)), \
            patch("instant2_app.gui.resolve_data_dir", return_value=data_dir), \
            patch("instant2_app.gui.daemon_is_running", return_value=False), \
            patch("instant2_app.gui.audio.input_choices", return_value=[]), \
            patch("instant2_app.gui.autostart.is_enabled", return_value=False), \
            patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
                default=SimpleNamespace(device=(-1, -1)))}):
        logic = gui.PanelLogic(
            page=page, emit=lambda kind, payload: emitted.append((kind, payload)),
            tasks=PendingTasks(), schedule=schedule, **kwargs)
    return logic, emitted, scheduled


def daemon_spawns(mock):
    """Llamadas a Popen que son el arranque del daemon."""
    found = []
    for entry in mock.call_args_list:
        if not entry.args:
            continue
        argv = entry.args[0]
        if not isinstance(argv, (list, tuple)):
            continue
        if any("instant2" in str(part).lower() for part in argv):
            found.append(entry)
    return found


class AutoDownloadTests(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)

    def test_the_panel_starts_the_recognition_download_on_its_own(self):
        logic, _emitted, scheduled = make_logic(self.data_dir)
        self.assertEqual(len(scheduled), 1, "el panel no programó la descarga")
        self.assertTrue(scheduled[0]())
        payload = logic.state_payload()
        self.assertTrue(payload["model"]["downloading"])
        self.assertTrue(payload["model"]["progress"]["visible"])

    def test_the_download_reaches_the_downloader(self):
        logic, _emitted, scheduled = make_logic(self.data_dir)
        scheduled[0]()
        self.assertEqual(len(logic.tasks.submitted), 1)
        downloaded = []
        with patch.object(models, "download_models",
                          side_effect=lambda *a, **k: downloaded.append(a)):
            logic.tasks.submitted[0][0](lambda payload: None)
        self.assertEqual(len(downloaded), 1)
        self.assertEqual(downloaded[0][0], self.data_dir)

    def test_the_automatic_download_does_not_start_twice(self):
        logic, _emitted, scheduled = make_logic(self.data_dir)
        scheduled[0]()
        self.assertEqual(len(logic.tasks.submitted), 1)
        # El mismo panel, otro disparo: no hay una segunda descarga.
        self.assertFalse(logic._auto_download_models())
        self.assertEqual(len(logic.tasks.submitted), 1)

    def test_a_new_panel_does_not_restart_it_after_a_failure(self):
        logic, _emitted, scheduled = make_logic(self.data_dir)
        scheduled[0]()
        task, _on_result, on_error, _on_progress = logic.tasks.submitted[0]
        on_error(models.ModelIntegrityError("sin red"))
        self.assertTrue(logic.state_payload()["model"]["status"].startswith(
            "No se pudo"))
        # Otro panel, mismo directorio de datos: la vía automática no insiste.
        other, _emitted2, scheduled2 = make_logic(self.data_dir)
        self.assertFalse(other._auto_download_models())
        self.assertEqual(other.tasks.submitted, [])
        self.assertTrue(scheduled2)

    def test_the_retry_button_keeps_working_after_a_failure(self):
        logic, _emitted, scheduled = make_logic(self.data_dir)
        scheduled[0]()
        _task, _on_result, on_error, _on_progress = logic.tasks.submitted[0]
        on_error(models.DownloadCancelled("cancelada"))
        payload = logic.state_payload()
        self.assertEqual(payload["model"]["button"], "Reintentar descarga")
        self.assertTrue(payload["model"]["status"].startswith("Descarga cancelada"))
        # El botón manual arranca una descarga nueva.
        logic.handle({"op": "download_models"})
        self.assertEqual(len(logic.tasks.submitted), 2)

    def test_it_does_not_start_while_the_panel_is_closing(self):
        logic, _emitted, scheduled = make_logic(self.data_dir)
        logic.close()
        self.assertFalse(scheduled[0]())
        self.assertEqual(logic.tasks.submitted, [])

    def test_it_does_not_start_in_the_diagnostics_mode(self):
        logic, _emitted, scheduled = make_logic(self.data_dir, page="diagnostics")
        self.assertFalse(scheduled[0]())
        self.assertEqual(logic.tasks.submitted, [])

    def test_it_does_not_start_when_the_models_are_already_there(self):
        logic, _emitted, scheduled = make_logic(self.data_dir, models_ready=True)
        self.assertFalse(scheduled[0]())
        self.assertEqual(logic.tasks.submitted, [])

    def test_the_marker_lives_in_the_data_directory(self):
        logic, _emitted, scheduled = make_logic(self.data_dir)
        scheduled[0]()
        self.assertTrue(os.path.isfile(
            os.path.join(self.data_dir, gui.AUTO_DOWNLOAD_MARKER)))

    def test_the_dictation_waits_for_the_download_and_starts_by_itself(self):
        logic, _emitted, scheduled = make_logic(
            self.data_dir, start_daemon_on_open=True)
        logic._daemon_state_ready = True
        logic._microphones_loaded = True
        with patch("subprocess.Popen") as launch:
            logic._maybe_start_daemon_on_open()
            self.assertEqual(daemon_spawns(launch), [])
            scheduled[0]()
            _task, on_result, _on_error, _on_progress = logic.tasks.submitted[0]
            with patch("instant2_app.gui.models.check",
                       return_value={"parakeet": True, "vad": True}):
                on_result(None)
            self.assertEqual(len(daemon_spawns(launch)), 1)

    def test_the_dictation_is_not_blocked_by_a_confusing_message(self):
        logic, emitted, scheduled = make_logic(self.data_dir)
        logic._daemon_state_ready = True
        logic._microphones_loaded = True
        scheduled[0]()
        with patch("subprocess.Popen"):
            self.assertFalse(logic._start_daemon(save_settings=False))
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Voz en preparación", titles)


if __name__ == "__main__":
    unittest.main()
