"""Ciclo de vida del panel (`PanelLogic`), sin Qt ni WebEngine.

Cubre el comportamiento de consumidor: qué pasa al cerrar, cuándo arranca el
daemon, cómo se rutean las actualizaciones y qué copy ve el usuario en cada
estado. El dibujo de la página lo cubre `test_panel_page.py`; el motor real
(QtWebEngine) se prueba con el smoke manual.
"""
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import gui


def daemon_spawns(mock):
    """Llamadas a Popen que son el arranque del daemon (instant2 ... run).

    En Linux, importar sounddevice dispara `ldconfig` por ctypes y ese Popen
    también pasa por el parche: contar crudo daba falsos positivos.
    """
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

CFG = {"mic_hint": "", "mic_index": None, "key": "f9", "autostart": False}


class SyncTasks:
    """TaskRunner sincrónico: aplica el trabajo en el acto (determinista)."""

    def __init__(self):
        self.pending = 0
        self.on_idle = None
        self.on_error = None

    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        self.pending += 1
        try:
            value = fn(lambda payload: on_progress and on_progress(payload))
        except Exception as exc:  # noqa: BLE001  (igual que el runner real)
            if on_error is not None:
                on_error(exc)
            elif self.on_error is not None:
                self.on_error(exc)
            value = None
        else:
            if on_result is not None:
                on_result(value)
        finally:
            self.pending -= 1
        if self.on_idle is not None:
            self.on_idle()
        return id(fn)

    def drain(self):
        return 0

    def close(self):
        self.pending = 0


def make_logic(emitted=None, tasks=None, schedule=None, **kwargs):
    emitted = emitted if emitted is not None else []
    with patch("instant2_app.gui.config.load", return_value=dict(CFG)), \
            patch("instant2_app.gui.models.check", return_value={"parakeet": True, "vad": True}), \
            patch("instant2_app.gui.resolve_data_dir", return_value=os.path.join(os.getcwd(), "models")), \
            patch("instant2_app.gui.daemon_is_running", return_value=False), \
            patch("instant2_app.gui.audio.input_choices", return_value=[]), \
            patch("instant2_app.gui.autostart.is_enabled", return_value=False), \
            patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
                default=SimpleNamespace(device=(-1, -1)))}):
        logic = gui.PanelLogic(
            emit=lambda kind, payload: emitted.append((kind, payload)),
            tasks=tasks if tasks is not None else SyncTasks(),
            schedule=schedule if schedule is not None else (lambda ms, fn: None),
            **kwargs)
    return logic, emitted


class PanelLifecycleTests(unittest.TestCase):
    def test_removed_models_page_falls_back_to_home(self):
        logic, emitted = make_logic()
        logic.page = "settings"
        logic.navigate("models")
        self.assertEqual(logic.page, "home")
        self.assertIn(("navigate", {"page": "home"}), emitted)

    def test_home_status_explains_where_to_dictate_for_both_daemon_states(self):
        for running in (False, True):
            with self.subTest(running=running):
                logic, _emitted = make_logic()
                with patch("instant2_app.gui.daemon_is_running", return_value=running):
                    logic.refresh_daemon()
                state = logic.state_payload()
                self.assertEqual(state["status"]["state"],
                                 "ok" if running else "down")
                self.assertEqual(state["status"]["title"],
                                 "Dictado activo" if running
                                 else "Dictado detenido")
                self.assertEqual(state["status"]["detail"],
                                 "Enfocá el campo donde querés escribir. "
                                 "El servicio de dictado y el LLM tienen estados separados.")

    def test_running_daemon_can_be_restarted_when_prerequisites_are_ready(self):
        logic, _emitted = make_logic()
        logic._microphones_loaded = True
        logic.mic_selected = "USB Mic"
        logic.catalog.devices["USB Mic"] = (5, "USB Mic")
        with patch("instant2_app.gui.daemon_is_running", return_value=True):
            logic.refresh_daemon()
        status = logic.state_payload()["status"]
        self.assertFalse(status["can_start"])
        self.assertTrue(status["can_toggle"])
        self.assertTrue(status["can_stop"])

    def test_closing_drops_late_results_without_touching_daemon(self):
        started, release = threading.Event(), threading.Event()
        seen = []
        logic, _emitted = make_logic(tasks=gui.TaskRunner())

        def slow(_emit):
            started.set()
            release.wait(5)
            return "tarde"

        logic.tasks.submit(slow, lambda value: seen.append(value))
        self.assertTrue(started.wait(2))
        with patch("instant2_app.gui.daemon_is_running",
                   side_effect=AssertionError("close consultó el daemon")), \
                patch("instant2_app.gui.stop_daemon",
                      side_effect=AssertionError("close frenó el daemon")):
            logic.close()
        release.set()
        deadline = time.monotonic() + 3
        while logic.tasks.pending and time.monotonic() < deadline:
            logic.tasks.drain()
            time.sleep(0.02)
        logic.tasks.drain()
        self.assertTrue(logic._closed)
        self.assertEqual(seen, [])

    def test_logic_close_closes_qml_window_once_without_reentry(self):
        logic, _emitted = make_logic()
        panel = object.__new__(gui._WebPanel)
        panel.logic = logic
        panel._window_closing = False

        class FakeApplication:
            def __init__(self):
                self.quit_calls = 0

            def quit(self):
                self.quit_calls += 1

        class FakeRoot:
            def __init__(self):
                self.close_calls = 0

            def close(self):
                self.close_calls += 1
                panel._on_closed()

        panel.application = FakeApplication()
        panel.root = FakeRoot()
        logic.emit = panel._emit

        logic.close()
        logic.close()

        self.assertEqual(panel.root.close_calls, 1)
        self.assertEqual(panel.application.quit_calls, 1)
        self.assertTrue(panel._window_closing)

    def test_daemon_inherits_resolved_model_directory_and_start_is_single_flight(self):
        launch = Mock()
        logic, _emitted = make_logic()
        logic._daemon_state_ready = True
        logic._microphones_loaded = True
        logic.model_ready = True
        with patch("subprocess.Popen", launch):
            logic._start_daemon(save_settings=False)
            logic._start_daemon(save_settings=False)
        spawns = daemon_spawns(launch)
        self.assertEqual(len(spawns), 1)
        self.assertEqual(spawns[0].kwargs["env"]["INSTANT2_DATA"], logic.data_dir)
        self.assertEqual(spawns[0].kwargs["cwd"], gui._workdir())

    def test_noargs_start_waits_for_readiness_and_starts_once(self):
        import traceback

        calls = []

        def record_popen(*args, **kwargs):
            calls.append("".join(traceback.format_stack(limit=8)))
            return Mock()

        logic, _emitted = make_logic(start_daemon_on_open=True)
        logic.model_ready = True

        def why():
            return (f"spawns={len(daemon_spawns(launch))} "
                    f"pending={logic._daemon_start_pending} "
                    f"token={logic._daemon_start_token}\n" + "\n---\n".join(calls))

        with patch("subprocess.Popen", side_effect=record_popen) as launch:
            logic._maybe_start_daemon_on_open()
            self.assertEqual(daemon_spawns(launch), [])
            with patch("instant2_app.gui.daemon_is_running", return_value=False):
                logic.refresh_daemon()
            logic.refresh_microphones(initial=True)
            self.assertTrue(logic._daemon_state_ready)
            self.assertTrue(logic._microphones_loaded)
            logic._maybe_start_daemon_on_open()
            self.assertEqual(len(daemon_spawns(launch)), 1, why())
            logic._maybe_start_daemon_on_open()
            self.assertEqual(len(daemon_spawns(launch)), 1, why())
            # Daemon ya corriendo: no se arranca de nuevo.
            logic._start_daemon_on_open = True
            logic._last_daemon_running = True
            logic._maybe_start_daemon_on_open()
            self.assertEqual(len(daemon_spawns(launch)), 1, why())

    def test_start_refuses_without_models(self):
        launch = Mock()
        logic, emitted = make_logic()
        logic._daemon_state_ready = True
        logic._microphones_loaded = True
        logic.model_ready = False
        with patch("subprocess.Popen", launch):
            self.assertFalse(logic._start_daemon(save_settings=False))
        self.assertEqual(daemon_spawns(launch), [])
        titles = [payload["title"] for kind, payload in emitted if kind == "toast"]
        self.assertIn("Voz pendiente", titles)

    def test_daemon_start_timeout_sets_recovery_copy(self):
        scheduled = []
        logic, _emitted = make_logic(schedule=lambda ms, fn: scheduled.append((ms, fn)))
        logic._daemon_state_ready = True
        logic._microphones_loaded = True
        logic.model_ready = True
        with patch("subprocess.Popen", Mock()):
            self.assertTrue(logic._start_daemon(save_settings=False))
        timeouts = [fn for ms, fn in scheduled if ms == 10000]
        self.assertEqual(len(timeouts), 1)
        timeouts[0]()
        self.assertFalse(logic._daemon_start_pending)
        self.assertEqual(logic.status_detail, gui.DAEMON_MISSING_DETAIL)

    def test_update_routes_by_install_mode(self):
        # `_install_ready` re-hashea el archivo antes de ejecutarlo y
        # `apply_binary_update` exige `expected_sha256`: por eso se usan
        # archivos reales en un tempdir.
        import hashlib as _hashlib
        import tempfile as _tempfile

        def _verified(_dir, name):
            _path = os.path.join(_dir, name)
            with open(_path, "wb") as _handle:
                _handle.write(b"instalador-verificado-" * 100)
            with open(_path, "rb") as _handle:
                return _path, _hashlib.sha256(_handle.read()).hexdigest()

        # Instalado (Windows): frena el daemon y lanza el Setup en silencio.
        logic, _emitted = make_logic()
        with _tempfile.TemporaryDirectory() as _dir:
            _setup, _digest = _verified(_dir, "Instant2-Setup.exe")
            with patch("instant2_app.update.install_mode", return_value="installed"), \
                    patch("instant2_app.gui.stop_daemon") as stop, \
                    patch("instant2_app.gui.daemon_is_running", return_value=False), \
                    patch("instant2_app.gui._pending_update_path",
                          return_value=os.path.join(_dir, "pending-update.json")), \
                    patch("instant2_app.gui.subprocess.run",
                          return_value=SimpleNamespace(returncode=0)) as run:
                logic._install_ready(_setup, _digest)
        stop.assert_called_once()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1:4],
                         ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        # El log de Inno se pide para poder diagnosticar un aborto.
        self.assertTrue(any(str(part).startswith("/LOG=")
                            for part in run.call_args.args[0]))
        self.assertTrue(logic._closed)

        # Unix congelado: reemplazo en caliente + reinicio del dictado.
        logic, emitted = make_logic()
        with _tempfile.TemporaryDirectory() as _dir:
            _target, _digest = _verified(_dir, "instant2-linux")
            with patch.object(sys, "frozen", True, create=True), \
                    patch.object(sys, "platform", "linux"), \
                    patch("instant2_app.update.install_mode", return_value="portable"), \
                    patch("instant2_app.update.apply_binary_update",
                          return_value="/usr/local/bin/instant2") as apply_update, \
                    patch.object(logic, "stop_daemon_flow") as restart:
                logic._install_ready(_target, _digest)
        apply_update.assert_called_once_with(_target, expected_sha256=_digest)
        restart.assert_called_once()
        self.assertTrue(restart.call_args.kwargs["restart"])
        # El reinicio se cuenta: el llamador recibe si el dictado quedo
        # reiniciado (no se promete un reinicio que no ocurrio).
        self.assertTrue(callable(restart.call_args.kwargs["on_restart"]))

        # Portable sin script: aviso con instrucciones, sin lanzar nada.
        logic, emitted = make_logic()
        with _tempfile.TemporaryDirectory() as _dir:
            _target, _digest = _verified(_dir, "Instant2.exe")
            with patch("instant2_app.update.install_mode", return_value="portable"), \
                    patch("instant2_app.gui.os.path.isfile",
                          side_effect=lambda p: False
                          if str(p).endswith(".bat") else True), \
                    patch("instant2_app.gui.subprocess.Popen") as popen:
                logic._install_ready(_target, _digest)
        popen.assert_not_called()
        messages = [payload for kind, payload in emitted if kind == "toast"]
        self.assertTrue(any("instant2-update.bat" in item["message"] for item in messages))

    def test_apply_gui_action_scrolls_to_audio_and_requests_start(self):
        logic, emitted = make_logic()
        logic.apply_gui_action("setup", start_daemon=False)
        self.assertIn(("navigate", {"page": "audio"}), emitted)
        logic.apply_gui_action(None, start_daemon=True)
        self.assertTrue(logic._start_daemon_on_open)
        self.assertIn(("navigate", {"page": "home"}), emitted)

    def test_daemon_poll_failure_surfaces_in_status_detail(self):
        logic, _emitted = make_logic()
        with patch("instant2_app.gui.daemon_is_running",
                   side_effect=RuntimeError("boom")):
            logic.refresh_daemon()
        self.assertIn("boom", logic.status_detail)
        self.assertIn("No se pudo comprobar", logic.status_detail)

    def test_boot_detail_survives_daemon_poll(self):
        logic, _emitted = make_logic()
        logic.status_detail = gui.DAEMON_MISSING_DETAIL
        logic._daemon_start_pending = True
        with patch("instant2_app.gui.daemon_is_running", return_value=False):
            logic.refresh_daemon()
        self.assertEqual(logic.status_detail, gui.DAEMON_MISSING_DETAIL)

    def test_diagnostics_done_does_not_reopen_closed_modal(self):
        logic, emitted = make_logic()
        logic.show_diagnostics()
        self.assertTrue(logic._diagnostics_open)
        logic.handle({"op": "diagnostics_close"})
        emitted.clear()
        # El worker tardío no debe reabrir: sin emit de diagnostics.
        logic._diagnostic_running = False
        logic._diagnostics_text = "todo ok"
        logic.push_state()
        self.assertFalse(any(kind == "diagnostics" for kind, _payload in emitted))


if __name__ == "__main__":
    unittest.main()
