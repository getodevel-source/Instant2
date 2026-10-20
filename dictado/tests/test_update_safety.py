"""Bloque 1 del saneo del actualizador: actualizar no miente ni asusta.

Cada clase cubre una tarea del documento `odd/tasks/saneo-instalador-y-updater.md`:

- T1: el helper portable no abre una terminal y su salida queda en un log.
- T2: el helper no dice "Listo" cuando hubo rollback.
- T3: el instalador se espera, se lee su returncode y se verifica la version.
- T4: los probes del daemon son robustos (encoding, probe fallido, deadline).
- T5: todo toast de error queda registrado.
- T6: el hijo no hereda el `_MEIPASS` del padre.

unittest clásico (como el resto de la suite): sin red, sin Qt, sin SO real.
"""
import hashlib
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import gui


class _ThreadedTasks:
    """TaskRunner con hilo real para los flujos de actualización.

    El fake anterior corría `fn` Y `on_result` en el hilo que llamaba:
    eso tapaba justo el defecto que hay que detectar (una espera
    bloqueante en el hilo de UI). Acá el trabajo corre en un hilo aparte y
    los resultados se aplican en el hilo que llama a `drain()`, que es el
    hilo de UI en la app real.
    """

    def __init__(self):
        self.pending = 0
        self.ui_thread = threading.current_thread()
        self._events = queue.Queue()
        self._threads = []
        self._lock = threading.Lock()

    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        with self._lock:
            self.pending += 1

        def runner():
            try:
                value = fn(lambda payload: self._events.put(
                    ("progress", on_progress, payload)))
            except Exception as exc:  # noqa: BLE001  (igual que el runner real)
                self._events.put(("error", on_error, exc))
            else:
                self._events.put(("result", on_result, value))
            finally:
                with self._lock:
                    self.pending -= 1

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        with self._lock:
            self._threads.append(thread)
        return id(fn)

    def wait(self, timeout=30.0):
        """Espera a los workers y aplica los resultados en ESTE hilo."""
        for thread in list(self._threads):
            thread.join(timeout)
        return self.drain()

    def drain(self):
        """Aplica los eventos encolados; devuelve cuántos atendió."""
        applied = 0
        while True:
            try:
                kind, callback, value = self._events.get_nowait()
            except queue.Empty:
                break
            if callback is not None:
                callback(value)
            applied += 1
        return applied

    def close(self):
        self.pending = 0
        self._threads = []


def _logic(cfg=None):
    """PanelLogic mínimo con las dependencias externas simuladas."""
    emitted = []
    base_cfg = {"mic_hint": "", "mic_index": None, "key": "f9",
                "autostart": False, "update_last_check": 0}
    base_cfg.update(cfg or {})
    with (
        patch("instant2_app.gui.config.load", return_value=base_cfg),
        patch("instant2_app.gui.models.check",
              return_value={"parakeet": True, "vad": True}),
        patch("instant2_app.gui.resolve_data_dir",
              return_value=os.path.join(os.getcwd(), "models")),
        patch("instant2_app.gui.daemon_is_running", return_value=False),
        patch("instant2_app.gui.audio.input_choices", return_value=[]),
        patch("instant2_app.gui.autostart.is_enabled", return_value=False),
        patch.dict(sys.modules, {"sounddevice": SimpleNamespace(
            default=SimpleNamespace(device=(-1, -1)))}),
    ):
        logic = gui.PanelLogic(
            emit=lambda kind, payload: emitted.append((kind, payload)),
            tasks=_ThreadedTasks(), schedule=lambda ms, fn: None)
        logic._update_source_mode = False
    return logic, emitted


def _toasts(emitted):
    return [payload for kind, payload in emitted if kind == "toast"]


def _verified_file(directory, name="Instant2.exe", body=b"binario" * 100):
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(body)
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    return path, digest


def _release(version="0.2.0"):
    return {"update": True, "current": "0.1.0", "latest": version,
            "notes": "Novedades", "asset": "Instant2.exe",
            "asset_url": "https://x/Instant2.exe"}


def _isolated_update_paths(directory):
    """Corta la nota y los logs de actualización dentro de un temporal.

    Sin esto la suite escribiría en el %APPDATA% real de quien la corre.
    """
    return (
        patch("instant2_app.gui._pending_update_path",
              return_value=os.path.join(directory, "pending-update.json")),
        patch("instant2_app.gui._update_log_path",
              return_value=os.path.join(directory, "update.log")),
        patch("instant2_app.gui._installer_log_path",
              return_value=os.path.join(directory, "installer.log")),
    )


class T1HelperConsoleTests(unittest.TestCase):
    """T1: el helper portable se lanza sin ventana de consola."""

    def test_helper_launch_has_no_console_window_and_logs_its_output(self):
        logic, _emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp)
            logic._ready_update = (target, digest, "0.2.0")
            log_path = os.path.join(temp, "update.log")
            with (
                patch("instant2_app.update.install_mode", return_value="portable"),
                patch("instant2_app.gui.os.path.isfile", return_value=True),
                patch("instant2_app.gui._update_log_path", return_value=log_path),
                patch("instant2_app.gui._pending_update_path",
                      return_value=os.path.join(temp, "pending-update.json")),
                patch("instant2_app.gui.subprocess.Popen") as popen,
            ):
                logic.handle({"op": "apply_update"})
            self.assertTrue(os.path.isfile(log_path))

        self.assertEqual(popen.call_count, 1)
        kwargs = popen.call_args.kwargs
        # CREATE_NO_WINDOW: sin esta bandera cmd.exe abre una terminal.
        self.assertEqual(kwargs["creationflags"],
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if os.name == "nt":
            self.assertNotEqual(kwargs["creationflags"], 0)
        # La salida del helper va al log, no a una consola.
        self.assertIs(kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(os.path.basename(kwargs["stdout"].name), "update.log")

    def test_helper_log_lives_next_to_the_app_config(self):
        from instant2_app import paths
        self.assertEqual(
            os.path.basename(gui._update_log_path()), "update.log")
        self.assertEqual(
            os.path.dirname(gui._update_log_path()), paths.config_dir())


class T2HelperHonestyTests(unittest.TestCase):
    """T2: el helper deja todo en el log y no miente cuando revierte."""

    BAT = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "instant2-update.bat"))

    def _source(self):
        with open(self.BAT, encoding="utf-8") as handle:
            return handle.read()

    def _section(self, source, label):
        """El bloque de la etiqueta `:label` hasta la siguiente etiqueta."""
        start = source.index("\n:" + label + "\n") + 1
        rest = source[start:]
        end = rest.find("\n:")
        return rest if end < 0 else rest[:end]

    def test_every_message_goes_to_the_update_log(self):
        source = self._source()
        self.assertIn('set "LOG=%LOGDIR%\\update.log"', source)
        self.assertIn('>>"%LOG%" echo %MSG%', source)
        # El helper escribe siempre en el log; en consola solo si la app no
        # redirigio la salida (si no, el log tendria cada linea dos veces).
        self.assertIn("if not defined INSTANT2_UPDATE_LOG_REDIRECTED echo %MSG%",
                      source)

    def test_rollback_reports_the_failure_and_exits_with_error(self):
        source = self._source()
        rollback = self._section(source, "rollback")
        self.assertIn("ERROR:", rollback)
        self.assertIn("set \"RC=1\"", rollback)
        # El falso "Listo" no puede quedar en la rama que revierte.
        self.assertNotIn("Listo: Instant2 actualizado.", rollback)
        # Y el codigo de salida es el de RC, no un 0 fijo.
        self.assertIn("exit /b %RC%", source)

    def test_success_branch_is_the_only_one_that_says_listo(self):
        source = self._source()
        self.assertEqual(source.count("Listo: Instant2 actualizado."), 1)
        self.assertIn("if \"%RC%\"==\"0\" (", source)

    @unittest.skipUnless(os.name == "nt", "el helper es un .bat de Windows")
    def test_helper_writes_the_log_and_exits_with_error(self):
        with tempfile.TemporaryDirectory() as temp:
            appdata = os.path.join(temp, "appdata")
            os.makedirs(appdata)
            env = dict(os.environ)
            env["APPDATA"] = appdata
            env["INSTANT2_UPDATE_LOG_REDIRECTED"] = "1"
            result = subprocess.run(
                ["cmd", "/c", self.BAT], env=env, capture_output=True,
                text=True, timeout=60)
            log = os.path.join(appdata, "instant2", "update.log")
            self.assertEqual(result.returncode, 1)
            self.assertTrue(os.path.isfile(log))
            with open(log, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
            self.assertIn("Uso:", content)
            # Con la salida redirigida al log la consola queda muda.
            self.assertNotIn("Uso:", result.stdout)

    @unittest.skipUnless(os.name == "nt", "el helper es un .bat de Windows")
    def test_rollback_leaves_the_log_without_a_false_success(self):
        with tempfile.TemporaryDirectory() as temp:
            appdata = os.path.join(temp, "appdata")
            scripts = os.path.join(temp, "scripts")
            os.makedirs(appdata)
            os.makedirs(scripts)
            # El helper se copia lejos de instant2-stop.bat para no tocar
            # la app real de esta maquina.
            helper = os.path.join(scripts, "instant2-update.bat")
            shutil.copyfile(self.BAT, helper)
            # El destino es un .bat: arranca, pero no escribe pidfile, asi
            # que el helper cae en la rama de rollback.
            destination = os.path.join(scripts, "Instant2.exe")
            with open(destination, "w", encoding="utf-8") as handle:
                handle.write("@echo off\r\necho fake %*\r\n")
            source = os.path.join(temp, "nuevo.exe")
            with open(source, "wb") as handle:
                handle.write(b"binario nuevo")
            digest = hashlib.sha256(b"binario nuevo").hexdigest()

            env = dict(os.environ)
            env["APPDATA"] = appdata
            env["INSTANT2_UPDATE_LOG_REDIRECTED"] = "1"
            result = subprocess.run(
                ["cmd", "/c", helper, source, digest, destination], env=env,
                capture_output=True, text=True, timeout=120)

            self.assertEqual(result.returncode, 1)
            log = os.path.join(appdata, "instant2", "update.log")
            with open(log, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
            self.assertIn("ERROR:", content)
            self.assertNotIn("Listo: Instant2 actualizado.", content)
            # El destino anterior quedo restaurado.
            with open(destination, encoding="utf-8") as handle:
                self.assertIn("fake", handle.read())


class T3InstallerVerificationTests(unittest.TestCase):
    """T3: el instalador se espera, se lee su codigo y se verifica la version."""

    def _isolated(self, directory):
        """Corta la nota y el log de Inno dentro de un temporal."""
        return _isolated_update_paths(directory)

    def test_installer_runs_with_a_log_and_waits_for_the_result(self):
        logic, _emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            pending_patch, log_patch, _installer_patch = self._isolated(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch,
                patch("instant2_app.gui.stop_daemon"),
                patch("instant2_app.gui.daemon_is_running", return_value=False),
                patch("instant2_app.gui._pid_value", return_value=4242),
                patch("instant2_app.gui._probe_daemon_process",
                      return_value=(gui.DAEMON_STOPPED, None)),
                patch("instant2_app.gui.subprocess.run",
                      return_value=SimpleNamespace(returncode=0)) as run,
            ):
                logic.handle({"op": "apply_update"})
                logic.tasks.wait()

            self.assertEqual(run.call_count, 1)
            argv = run.call_args.args[0]
            self.assertIn("/SILENT", argv)
            self.assertTrue(any(str(part).startswith("/LOG=") for part in argv))
            self.assertTrue(logic._closed)

    def test_the_installer_is_never_waited_on_the_ui_thread(self):
        """La espera del instalador no puede caer en el hilo de UI.

        El instalador de Inno manda WM_CLOSE al panel para reemplazar los
        archivos: si el hilo de UI está bloqueado esperando al instalador,
        no procesa el cierre y Inno escala a matar el proceso.
        """
        logic, _emitted = _logic()
        logic._pending_update = _release()
        calls = []

        def fake_run(args, **kwargs):
            calls.append((list(args), threading.current_thread()))
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            pending_patch, log_patch, installer_patch = _isolated_update_paths(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, installer_patch,
                patch("instant2_app.gui.stop_daemon"),
                patch("instant2_app.gui._pid_value", return_value=4242),
                patch("instant2_app.gui._probe_daemon_process",
                      return_value=(gui.DAEMON_STOPPED, None)),
                patch("instant2_app.gui.subprocess.run", side_effect=fake_run),
            ):
                logic.handle({"op": "apply_update"})
                logic.tasks.wait()

        installers = [thread for argv, thread in calls if "/SILENT" in argv]
        self.assertEqual(len(installers), 1)
        self.assertIsNot(installers[0], threading.main_thread())

    def test_failed_installer_reports_the_log_instead_of_pretending(self):
        logic, emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            log_path = os.path.join(temp, "installer.log")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("Setup aborted\nERROR: no hay espacio\n")
            pending_patch, log_patch, _installer_patch = self._isolated(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch,
                patch("instant2_app.gui.stop_daemon"),
                patch("instant2_app.gui.daemon_is_running", return_value=False),
                patch("instant2_app.gui._pid_value", return_value=4242),
                patch("instant2_app.gui._probe_daemon_process",
                      return_value=(gui.DAEMON_STOPPED, None)),
                patch("instant2_app.gui.subprocess.run",
                      return_value=SimpleNamespace(returncode=5)) as run,
            ):
                logic.handle({"op": "apply_update"})
                logic.tasks.wait()

            self.assertEqual(run.call_count, 1)
            # La app sigue viva y el toast lleva el log del instalador.
            self.assertFalse(logic._closed)
            toasts = _toasts(emitted)
            self.assertTrue(any(toast["title"] == "Actualización sin aplicar"
                                for toast in toasts))
            detail = " ".join(toast["message"] for toast in toasts)
            self.assertIn("installer.log", detail)
            self.assertIn("no hay espacio", detail)

    def test_pending_update_is_reported_on_the_next_start(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "pending-update.json")
            log_path = os.path.join(temp, "installer.log")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("ERROR: el instalador se abortó\n")
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending), \
                    patch("instant2_app.gui._current_version",
                          return_value="0.1.5"):
                gui._write_pending_update("0.2.0", log_path)
                report = gui._pending_update_report()
            self.assertIsNotNone(report)
            title, message, level = report
            self.assertEqual(title, "Actualización sin aplicar")
            self.assertEqual(level, "error")
            self.assertIn("0.2.0", message)
            self.assertIn("0.1.5", message)
            self.assertIn("el instalador se abortó", message)
            # La nota se consume: no se vuelve a reportar.
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending):
                self.assertIsNone(gui._pending_update_report())

    def test_applied_update_is_confirmed_on_the_next_start(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "pending-update.json")
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending), \
                    patch("instant2_app.gui._current_version",
                          return_value="0.2.0"):
                gui._write_pending_update("0.2.0", os.path.join(temp, "x.log"))
                report = gui._pending_update_report()
            self.assertIsNotNone(report)
            title, message, level = report
            self.assertEqual(title, "Actualización aplicada")
            self.assertEqual(level, "info")
            self.assertIn("0.2.0", message)

    def test_installed_update_notes_the_requested_version(self):
        logic, _emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            pending_patch, log_patch, _installer_patch = self._isolated(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch,
                patch("instant2_app.gui.stop_daemon"),
                patch("instant2_app.gui.daemon_is_running", return_value=False),
                patch("instant2_app.gui._pid_value", return_value=4242),
                patch("instant2_app.gui._probe_daemon_process",
                      return_value=(gui.DAEMON_STOPPED, None)),
                patch("instant2_app.gui.subprocess.run",
                      return_value=SimpleNamespace(returncode=0)),
            ):
                logic.handle({"op": "apply_update"})
                logic.tasks.wait()

            with open(os.path.join(temp, "pending-update.json"),
                      encoding="utf-8") as handle:
                import json
                note = json.load(handle)
            self.assertEqual(note["version"], "0.2.0")
            self.assertTrue(note["log"].endswith("installer.log"))


class T4DaemonProbeTests(unittest.TestCase):
    """T4: los probes del daemon son robustos y el deadline es alcanzable."""

    def test_probe_decodes_with_an_explicit_encoding(self):
        with patch("instant2_app.gui.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout="ok")) as run:
            responded, code, output = gui._run_probe(["tasklist"], 5.0)
        self.assertTrue(responded)
        self.assertEqual(code, 0)
        self.assertEqual(output, "ok")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_probe_that_cannot_decode_does_not_raise(self):
        """Un byte raro no puede abortar la actualización."""
        with patch("instant2_app.gui.subprocess.run",
                   side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "malo")):
            responded, code, output = gui._run_probe(["tasklist"], 5.0)
        self.assertFalse(responded)
        self.assertIsNone(code)
        self.assertEqual(output, "")

    def test_probe_that_times_out_is_unknown_not_instant(self):
        with patch("instant2_app.gui.subprocess.run",
                   side_effect=TimeoutError("se colgo")):
            responded, _code, _output = gui._run_probe(["tasklist"], 5.0)
        self.assertFalse(responded)

    def test_failed_powershell_probe_does_not_mean_it_is_instant(self):
        """Un probe caido no puede afirmar que el PID sea de Instant2."""
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui.subprocess.run",
                      side_effect=OSError("no se pudo ejecutar")):
            self.assertIsNone(gui._pid_is_instant(4242))

    def test_failed_probe_leaves_the_daemon_state_unknown(self):
        gui._reset_known_daemon_state()
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui._pid_value", return_value=4242), \
                patch("instant2_app.gui.subprocess.run",
                      side_effect=OSError("tasklist no responde")):
            self.assertEqual(gui.daemon_probe_state(), gui.DAEMON_UNKNOWN)
            # Y "no se sabe" no es "está corriendo" (no hay estado previo).
            self.assertFalse(gui.daemon_is_running())
        gui._reset_known_daemon_state()

    def test_an_unknown_probe_keeps_the_last_known_state(self):
        """Un probe caido no puede hacer creer que el daemon esta apagado.

        Si el panel cree que el daemon no esta, arranca un segundo.
        """
        gui._reset_known_daemon_state()
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui._pid_value", return_value=4242), \
                patch("instant2_app.gui._probe_daemon_process",
                      return_value=(gui.DAEMON_RUNNING, None)):
            self.assertTrue(gui.daemon_is_running())
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui._pid_value", return_value=4242), \
                patch("instant2_app.gui._probe_daemon_process",
                      return_value=(gui.DAEMON_UNKNOWN, gui.PROBE_NO_IMAGE)):
            # El ultimo estado conocido sigue siendo "corriendo".
            self.assertTrue(gui.daemon_is_running())
        gui._reset_known_daemon_state()

    def test_dead_pid_is_stopped(self):
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui._pid_value", return_value=4242), \
                patch("instant2_app.gui.subprocess.run",
                      return_value=SimpleNamespace(
                          returncode=0,
                          stdout="INFO: No tasks are running which match "
                                 "the specified criteria.\n")):
            self.assertEqual(gui.daemon_probe_state(), gui.DAEMON_STOPPED)

    def test_stop_daemon_does_not_touch_the_pidfile_when_the_probe_fails(self):
        """Sin evidencia del proceso no se mata nada ni se borra el pidfile."""
        with tempfile.TemporaryDirectory() as temp:
            pidfile = os.path.join(temp, "instant2.pid")
            with open(pidfile, "w", encoding="utf-8") as handle:
                handle.write("424242")
            with patch("instant2_app.daemon.pid_path", return_value=pidfile), \
                    patch("instant2_app.gui._pid_value", return_value=424242), \
                    patch("instant2_app.gui._probe_daemon_process",
                          return_value=(gui.DAEMON_UNKNOWN, gui.PROBE_NO_IMAGE)):
                # None = "no se pudo verificar": ni vivo ni apagado.
                self.assertIsNone(gui.stop_daemon())
            # El pidfile sigue: si el daemon vive, el proximo stop lo frena.
            self.assertTrue(os.path.isfile(pidfile))

    @unittest.skipUnless(os.name == "nt", "mata el proceso con taskkill")
    def test_stop_daemon_kills_the_daemon_when_the_cmdline_probe_fails(self):
        """Un probe caido no puede dejar el daemon viejo vivo.

        Si el daemon sigue vivo, el ejecutable queda tomado y el
        actualizador falla al copiar encima (y el usuario ve un error sin
        explicar). El pidfile lo escribio el daemon: se frena igual.
        """
        with tempfile.TemporaryDirectory() as temp:
            pidfile = os.path.join(temp, "instant2.pid")
            with open(pidfile, "w", encoding="utf-8") as handle:
                handle.write("4242")
            with patch("instant2_app.daemon.pid_path", return_value=pidfile), \
                    patch("instant2_app.gui._pid_value", return_value=4242), \
                    patch("instant2_app.gui._probe_daemon_process",
                          return_value=(gui.DAEMON_UNKNOWN,
                                        gui.PROBE_NO_CMDLINE)), \
                    patch("instant2_app.gui.subprocess.Popen") as popen:
                self.assertTrue(gui.stop_daemon())
            self.assertEqual(popen.call_count, 1)
            self.assertIn("4242", popen.call_args.args[0])

    def test_the_panel_stops_the_daemon_even_if_the_probe_fails(self):
        """Si el probe no responde, el panel frena el daemon igual.

        Antes el panel decia "El daemon ya no estaba" mientras el daemon
        seguia vivo: la actualizacion en caliente dejaba el daemon viejo
        con el binario nuevo encima.
        """
        logic, _emitted = _logic()
        logic._last_daemon_running = True
        with patch("instant2_app.gui._pid_value", return_value=4242), \
                patch("instant2_app.gui._pid_is_instant", return_value=None), \
                patch("instant2_app.gui.subprocess.Popen") as popen:
            logic.stop_daemon_flow()
            logic.tasks.wait()
        if os.name == "nt":
            self.assertEqual(popen.call_count, 1)
            self.assertIn("4242", popen.call_args.args[0])
        self.assertNotIn("ya no estaba", logic.status_detail)

    def test_the_panel_does_not_kill_a_recycled_pid(self):
        """Un PID de otro proceso no se mata (eso no se negocia)."""
        logic, _emitted = _logic()
        logic._last_daemon_running = True
        with patch("instant2_app.gui._pid_value", return_value=4242), \
                patch("instant2_app.gui._pid_is_instant", return_value=False), \
                patch("instant2_app.gui.daemon_is_running", return_value=False), \
                patch("instant2_app.gui.subprocess.Popen") as popen:
            logic.stop_daemon_flow()
            logic.tasks.wait()
        popen.assert_not_called()

    def test_deadline_is_longer_than_the_worst_case_probe(self):
        """El margen para frenar el daemon tiene que alcanzar el probe."""
        self.assertGreater(gui.DAEMON_STOP_DEADLINE,
                           gui._PROBE_TASKLIST_TIMEOUT
                           + gui._PROBE_POWERSHELL_TIMEOUT)

    def test_probe_budget_is_split_between_the_two_probes(self):
        tasklist, powershell = gui._split_probe_budget(12.0)
        self.assertLessEqual(tasklist + powershell, 12.0)
        self.assertGreater(tasklist, 0)
        self.assertGreater(powershell, 0)

    def test_the_powershell_probe_gets_its_whole_timeout(self):
        """El probe de PowerShell no puede perder tiempo en un segundo reparto.

        `Get-CimInstance Win32_Process` con el WMI frio se pasa de 6 s con
        facilidad: si el probe se queda corto, el estado cae en
        'desconocido' y la actualizacion se frena sin motivo.
        """
        listing = '"Instant2.exe","4242","Consola","1","1","100 K"\n'
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui._pid_value", return_value=4242), \
                patch("instant2_app.gui._run_probe",
                      return_value=(True, 0, listing)) as probe:
            gui.daemon_probe_state()
        timeouts = [call.args[1] for call in probe.call_args_list]
        self.assertEqual(timeouts[0], gui._PROBE_TASKLIST_TIMEOUT)
        # El ultimo probe es el de PowerShell: recibe su timeout entero.
        self.assertEqual(timeouts[-1], gui._PROBE_POWERSHELL_TIMEOUT)

    def test_the_pid_probe_uses_the_timeout_it_receives(self):
        """El timeout que recibe el probe se usa tal cual: no se reparte."""
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui._run_probe",
                      return_value=(True, 0, "")) as probe:
            gui._pid_is_instant(4242, timeout=7.5)
        self.assertEqual(probe.call_args.args[1], 7.5)

    def test_installer_wait_distinguishes_a_live_daemon_from_a_dead_probe(self):
        logic, emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            pending_patch, log_patch, _installer_patch = _isolated_update_paths(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch,
                patch("instant2_app.gui.stop_daemon"),
                patch("instant2_app.gui._pid_value", return_value=4242),
                patch("instant2_app.gui._probe_daemon_process",
                      return_value=(gui.DAEMON_UNKNOWN, gui.PROBE_NO_IMAGE)),
                patch("instant2_app.gui.time.monotonic", return_value=0.0),
                patch("instant2_app.gui.subprocess.run",
                      return_value=SimpleNamespace(returncode=0)) as run,
            ):
                logic.handle({"op": "apply_update"})
                logic.tasks.wait()
            run.assert_not_called()
            detail = " ".join(toast["message"] for toast in _toasts(emitted))
            self.assertIn("No se pudo verificar", detail)

    def test_installer_wait_reports_a_daemon_that_does_not_stop(self):
        """Un daemon vivo que no se frena se reporta como "sigue activo"."""
        logic, emitted = _logic()
        logic._pending_update = _release()
        clock = {"now": 0.0}

        def probe(_pid, budget=None):
            # Cada probe se come 30 s: el deadline se cumple.
            clock["now"] += 30.0
            return gui.DAEMON_RUNNING, None

        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            pending_patch, log_patch, _installer_patch = _isolated_update_paths(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch,
                patch("instant2_app.gui.stop_daemon"),
                patch("instant2_app.gui._pid_value", return_value=4242),
                patch("instant2_app.gui._probe_daemon_process",
                      side_effect=probe),
                patch("instant2_app.gui.time.monotonic",
                      side_effect=lambda: clock["now"]),
                patch("instant2_app.gui.time.sleep"),
                patch("instant2_app.gui.subprocess.run",
                      return_value=SimpleNamespace(returncode=0)) as run,
            ):
                logic.handle({"op": "apply_update"})
                logic.tasks.wait()
            run.assert_not_called()
            detail = " ".join(toast["message"] for toast in _toasts(emitted))
            self.assertIn("sigue activo", detail)

    def test_the_probe_budget_never_drops_below_the_floor(self):
        """El probe conserva su presupuesto aunque el deadline se acerque.

        Con el presupuesto colapsado el probe se queda sin respuesta y un
        daemon vivo parecería "no verificable" en vez de "sigue activo".
        """
        clock = {"now": 0.0}
        budgets = []

        def probe(_pid, budget=None):
            budgets.append(budget)
            clock["now"] += 19.9  # el probe casi se come el deadline
            return gui.DAEMON_RUNNING, None

        with patch("instant2_app.gui.stop_daemon"), \
                patch("instant2_app.gui._pid_value", return_value=4242), \
                patch("instant2_app.gui._probe_daemon_process", \
                      side_effect=probe), \
                patch("instant2_app.gui.time.monotonic",
                      side_effect=lambda: clock["now"]), \
                patch("instant2_app.gui.time.sleep"):
            with self.assertRaises(RuntimeError):
                gui.PanelLogic._prepare_installer()
        self.assertGreaterEqual(len(budgets), 2)
        for budget in budgets:
            self.assertGreaterEqual(budget, gui._PROBE_BUDGET_FLOOR)

    def test_the_probe_is_not_polled_too_often(self):
        """Cada tasklist.exe cuesta ~0.25 s: no se sondea en bucle cerrado."""
        sleeps = []
        clock = {"now": 0.0}

        def probe(_pid, budget=None):
            clock["now"] += 1.0
            return gui.DAEMON_RUNNING, None

        with patch("instant2_app.gui.stop_daemon"), \
                patch("instant2_app.gui._pid_value", return_value=4242), \
                patch("instant2_app.gui._probe_daemon_process", \
                      side_effect=probe), \
                patch("instant2_app.gui.time.monotonic",
                      side_effect=lambda: clock["now"]), \
                patch("instant2_app.gui.time.sleep", side_effect=sleeps.append):
            with self.assertRaises(RuntimeError):
                gui.PanelLogic._prepare_installer()
        self.assertTrue(sleeps)
        for value in sleeps:
            self.assertGreaterEqual(value, 0.2)


class T5ErrorToastLoggingTests(unittest.TestCase):
    """T5: todo toast de error queda registrado en instant2.log."""

    def test_error_toast_is_logged_with_title_and_detail(self):
        logic, _emitted = _logic()
        with self.assertLogs("instant2", level="ERROR") as captured:
            logic.toast("Actualización sin aplicar", "el instalador se abortó",
                        level="error")
        self.assertTrue(captured.output)
        line = captured.output[0]
        self.assertIn("Actualización sin aplicar", line)
        self.assertIn("el instalador se abortó", line)

    def test_error_toast_that_does_not_go_through_toast_error_is_logged(self):
        """El error reportado por otra ruta tambien deja rastro."""
        logic, emitted = _logic()
        with self.assertLogs("instant2", level="ERROR") as captured:
            logic.toast("Fallo raro", "detalle del fallo", level="error")
        self.assertIn("Fallo raro", captured.output[0])
        self.assertTrue(any(toast["title"] == "Fallo raro"
                            for toast in _toasts(emitted)))

    def test_informative_toasts_are_not_logged_as_errors(self):
        logic, _emitted = _logic()
        with self.assertNoLogs("instant2", level="ERROR"):
            logic.toast("Actualización lista", "v0.2.0 se descargó")
            logic.toast("Aviso", "cuidado", level="warn")


class T6WebEngineEnvTests(unittest.TestCase):
    """T6: el hijo no hereda el `_MEIPASS` del padre."""

    def test_child_does_not_inherit_the_parents_webengine_helper(self):
        from instant2_app import launch
        meipass = os.path.join("C:", "Temp", "_MEI12345")
        helper = os.path.join(meipass, "PySide6", "QtWebEngineProcess")
        with patch.object(launch.sys, "frozen", True, create=True), \
                patch.object(launch.sys, "_MEIPASS", meipass, create=True), \
                patch.dict(os.environ,
                           {"QTWEBENGINEPROCESS_PATH": helper}):
            env = launch.app_environment()
            # El padre sigue usando su helper...
            self.assertIn("QTWEBENGINEPROCESS_PATH", os.environ)
        # ...pero el hijo no hereda una ruta que desaparece con el padre.
        self.assertNotIn("QTWEBENGINEPROCESS_PATH", env)

    def test_webengine_helper_outside_the_extraction_is_kept(self):
        from instant2_app import launch
        meipass = os.path.join("C:", "Temp", "_MEI12345")
        outside = os.path.join("C:", "Qt", "QtWebEngineProcess.exe")
        with patch.object(launch.sys, "frozen", True, create=True), \
                patch.object(launch.sys, "_MEIPASS", meipass, create=True), \
                patch.dict(os.environ,
                           {"QTWEBENGINEPROCESS_PATH": outside}):
            env = launch.app_environment()
        self.assertEqual(env["QTWEBENGINEPROCESS_PATH"], outside)

    def test_source_run_keeps_the_environment_as_it_is(self):
        """Sin congelar no hay `_MEIPASS` que limpiar."""
        from instant2_app import launch
        with patch.object(launch.sys, "frozen", False, create=True), \
                patch.dict(os.environ,
                           {"QTWEBENGINEPROCESS_PATH": "ya-esta"}):
            env = launch.app_environment()
        self.assertEqual(env["QTWEBENGINEPROCESS_PATH"], "ya-esta")


if __name__ == "__main__":
    unittest.main()
