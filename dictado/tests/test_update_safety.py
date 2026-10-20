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
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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
    Los dos archivos de resultado (el del instalador de Inno y el del
    helper portable) se cortan juntos: uno solo dejaria al otro leer el
    %APPDATA% real de quien corre la suite.
    """
    results = patch.multiple(
        "instant2_app.gui",
        _installer_result_path=(
            lambda: os.path.join(directory, "installer-result.json")),
        _helper_result_path=(
            lambda: os.path.join(directory, "helper-result.json")),
    )
    return (
        patch("instant2_app.gui._pending_update_path",
              return_value=os.path.join(directory, "pending-update.json")),
        patch("instant2_app.gui._update_log_path",
              return_value=os.path.join(directory, "update.log")),
        patch("instant2_app.gui._installer_log_path",
              return_value=os.path.join(directory, "installer.log")),
        results,
    )


class T1HelperConsoleTests(unittest.TestCase):
    """T1: el helper portable se lanza sin ventana de consola."""

    def test_helper_launch_has_no_console_window_and_redirects_its_output(self):
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

        self.assertEqual(popen.call_count, 1)
        kwargs = popen.call_args.kwargs
        # CREATE_NO_WINDOW: sin esta bandera cmd.exe abre una terminal.
        self.assertEqual(kwargs["creationflags"],
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if os.name == "nt":
            self.assertNotEqual(kwargs["creationflags"], 0)
        # La salida del helper va a un archivo, no a una consola.
        self.assertIs(kwargs["stderr"], subprocess.STDOUT)
        self.assertTrue(os.path.isfile(kwargs["stdout"].name))
        # El log del usuario es del helper: si el padre lo tomara para la
        # salida del hijo, los dos se pelean por el archivo y los mensajes
        # del helper nunca llegan al log.
        self.assertNotEqual(os.path.abspath(kwargs["stdout"].name),
                            os.path.abspath(log_path))
        self.assertNotEqual(os.path.basename(kwargs["stdout"].name),
                            "update.log")

    def test_helper_log_lives_next_to_the_app_config(self):
        from instant2_app import paths
        self.assertEqual(
            os.path.basename(gui._update_log_path()), "update.log")
        self.assertEqual(
            os.path.dirname(gui._update_log_path()), paths.config_dir())


class T1HelperLogOwnershipTests(unittest.TestCase):
    """El log del usuario es del helper: el padre no lo toma.

    La app abria `%APPDATA%\\instant2\\update.log` y se lo pasaba al helper
    como stdout, mientras el helper escribia sus mensajes en el mismo
    archivo con `>>`. En Windows los dos se pelean por el archivo: el
    helper no puede escribir y en el log queda solo el error de
    comparticion del sistema (ni el paso a paso ni el error real).
    """

    BAT = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "instant2-update.bat"))

    @unittest.skipUnless(os.name == "nt", "el helper es un .bat de Windows")
    def test_the_user_log_gets_the_helper_step_by_step_and_its_error(self):
        """El log del usuario tiene el paso a paso y el error real.

        Se lanza el `.bat` de verdad, como lo lanza la app (el helper
        reemplaza el binario mientras la app esta cerrada).
        """
        logic, _emitted = _logic()
        logic._pending_update = _release()
        real_popen = subprocess.Popen
        launched = []

        def spy(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.append(process)
            return process

        with tempfile.TemporaryDirectory() as temp:
            appdata = os.path.join(temp, "appdata")
            root = os.path.join(temp, "root")
            scripts = os.path.join(root, "scripts")
            dist = os.path.join(root, "dist")
            for folder in (appdata, scripts, dist):
                os.makedirs(folder)
            # El helper real, pero solo: sin instant2-stop.bat al lado no
            # toca el daemon de esta maquina.
            helper = os.path.join(scripts, "instant2-update.bat")
            shutil.copyfile(self.BAT, helper)
            # El destino es un .bat: arranca, pero no escribe pidfile, asi
            # que el helper cae en la rama de rollback.
            destination = os.path.join(dist, "Instant2.exe")
            with open(destination, "w", encoding="utf-8") as handle:
                handle.write("@echo off\r\necho fake %*\r\n")
            source = os.path.join(temp, "nuevo.exe")
            with open(source, "wb") as handle:
                handle.write(b"MZ" + b"\x00" * 200)
            with open(source, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()

            logic._ready_update = (source, digest, "0.2.0")
            with patch.dict(os.environ, {"APPDATA": appdata}), \
                    patch("instant2_app.update.install_mode",
                          return_value="portable"), \
                    patch("instant2_app.gui._workdir", return_value=root), \
                    patch("instant2_app.gui.subprocess.Popen",
                          side_effect=spy):
                logic.handle({"op": "apply_update"})
                self.assertEqual(len(launched), 1)
                launched[0].wait(timeout=180)
                log = os.path.join(appdata, "instant2", "update.log")
                with open(log, encoding="utf-8", errors="replace") as handle:
                    content = handle.read()
                result = os.path.join(appdata, "instant2", "helper-result.json")
                payload = None
                if os.path.isfile(result):
                    with open(result, encoding="utf-8") as handle:
                        payload = json.load(handle)

        # El paso a paso del helper esta en el log del usuario...
        self.assertIn("[1/6] Verificando SHA256...", content)
        self.assertIn("[6/6] Reabriendo ventana...", content)
        # ...y tambien el error real (no el error de comparticion).
        self.assertIn("ERROR:", content)
        self.assertIn("NO se aplico", content)
        # El codigo de salida queda anotado para el arranque siguiente.
        self.assertIsNotNone(payload)
        self.assertEqual(payload["returncode"], 1)
        self.assertTrue(payload["log"].endswith("update.log"))

    def test_the_helper_failure_is_reported_on_the_next_start(self):
        """El codigo del helper queda anotado aunque el panel muera.

        El helper reemplaza el binario con la app cerrada: el proceso que
        lo lanzo no puede contar como termino. El `.bat` lo anota y el
        arranque siguiente lo cuenta con el log al lado.
        """
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "pending-update.json")
            log_path = os.path.join(temp, "update.log")
            result = os.path.join(temp, "helper-result.json")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("ERROR: el daemon nuevo no arranco\n")
            with open(result, "w", encoding="utf-8") as handle:
                json.dump({"returncode": 1, "log": log_path}, handle)
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending), \
                    patch("instant2_app.gui._installer_result_path",
                          return_value=os.path.join(temp, "installer.json")), \
                    patch("instant2_app.gui._helper_result_path",
                          return_value=result), \
                    patch("instant2_app.gui._current_version",
                          return_value="0.1.5"):
                gui._write_pending_update("0.2.0", log_path)
                report = gui._pending_update_report()
            self.assertIsNotNone(report)
            title, message, level = report
            self.assertEqual(title, "Actualización sin aplicar")
            self.assertEqual(level, "error")
            self.assertIn("1", message)
            self.assertIn("el daemon nuevo no arranco", message)
            # El resultado se consume: no se repite el error.
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending), \
                    patch("instant2_app.gui._installer_result_path",
                          return_value=os.path.join(temp, "installer.json")), \
                    patch("instant2_app.gui._helper_result_path",
                          return_value=result):
                self.assertIsNone(gui._pending_update_report())

    def test_a_helper_without_result_does_not_report_a_failure(self):
        """Sin resultado anotado no hay fallo que reportar."""
        with tempfile.TemporaryDirectory() as temp:
            with patch("instant2_app.gui._pending_update_path",
                       return_value=os.path.join(temp, "pending.json")), \
                    patch("instant2_app.gui._installer_result_path",
                          return_value=os.path.join(temp, "installer.json")), \
                    patch("instant2_app.gui._helper_result_path",
                          return_value=os.path.join(temp, "helper.json")):
                self.assertIsNone(gui._pending_update_report())


class T2HelperHonestyTests(unittest.TestCase):
    """T2: el helper deja todo en el log y no miente cuando revierte."""

    BAT = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "instant2-update.bat"))

    def _helper_env(self, appdata, redirected=True):
        env = dict(os.environ)
        env["APPDATA"] = appdata
        if redirected:
            env["INSTANT2_UPDATE_LOG_REDIRECTED"] = "1"
        else:
            env.pop("INSTANT2_UPDATE_LOG_REDIRECTED", None)
        return env

    @unittest.skipUnless(os.name == "nt", "el helper es un .bat de Windows")
    def test_every_message_goes_to_the_update_log(self):
        """Los mensajes del helper van al log y no a la consola.

        Con la salida ya redirigida al log, la consola queda muda: el
        usuario no ve una ventana y el paso a paso queda para diagnosing.
        """
        with tempfile.TemporaryDirectory() as temp:
            appdata = os.path.join(temp, "appdata")
            os.makedirs(appdata)
            env = self._helper_env(appdata)
            result = subprocess.run(
                ["cmd", "/c", self.BAT], env=env, capture_output=True,
                text=True, timeout=60)
            log = os.path.join(appdata, "instant2", "update.log")
            with open(log, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Uso:", content)
        self.assertNotIn("Uso:", result.stdout)
        self.assertEqual(result.stdout.strip(), "")

    @unittest.skipUnless(os.name == "nt", "el helper es un .bat de Windows")
    def test_without_the_redirect_the_helper_also_writes_to_the_console(self):
        """Si la app no redirige la salida, el helper escribe en consola."""
        with tempfile.TemporaryDirectory() as temp:
            appdata = os.path.join(temp, "appdata")
            os.makedirs(appdata)
            env = self._helper_env(appdata, redirected=False)
            result = subprocess.run(
                ["cmd", "/c", self.BAT], env=env, capture_output=True,
                text=True, timeout=60)
            log = os.path.join(appdata, "instant2", "update.log")
            with open(log, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        self.assertEqual(result.returncode, 1)
        # El mensaje esta en el log y tambien en la consola.
        self.assertIn("Uso:", content)
        self.assertIn("Uso:", result.stdout)

    @unittest.skipUnless(os.name == "nt", "el helper es un .bat de Windows")
    def test_helper_writes_the_log_and_exits_with_error(self):
        with tempfile.TemporaryDirectory() as temp:
            appdata = os.path.join(temp, "appdata")
            os.makedirs(appdata)
            env = self._helper_env(appdata)
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
    def test_rollback_reports_the_failure_and_exits_with_error(self):
        """El rollback cuenta el fallo real y sale con codigo de error."""
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

            env = self._helper_env(appdata)
            result = subprocess.run(
                ["cmd", "/c", helper, source, digest, destination], env=env,
                capture_output=True, text=True, timeout=120)

            self.assertEqual(result.returncode, 1)
            log = os.path.join(appdata, "instant2", "update.log")
            with open(log, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
            # El fallo real queda escrito...
            self.assertIn("ERROR:", content)
            self.assertIn("NO se aplico", content)
            # ...y el falso "Listo" no aparece en el rollback.
            self.assertNotIn("Listo:", content)
            # El destino anterior quedo restaurado.
            with open(destination, encoding="utf-8") as handle:
                self.assertIn("fake", handle.read())

    @unittest.skipUnless(os.name == "nt", "el helper es un .bat de Windows")
    def test_the_child_noise_stays_out_of_the_update_log(self):
        """El ruido del proceso que arranca no va al log de la actualización.

        Windows suelta avisos de "not compatible with the version of
        Windows" al arrancar el ejecutable: en el log de la actualización
        parecian fallos de la actualizacion.
        """
        with tempfile.TemporaryDirectory() as temp:
            appdata = os.path.join(temp, "appdata")
            scripts = os.path.join(temp, "scripts")
            os.makedirs(appdata)
            os.makedirs(scripts)
            helper = os.path.join(scripts, "instant2-update.bat")
            shutil.copyfile(self.BAT, helper)
            # La version vieja es un .bat (arranca sin ruido): la que se
            # copia encima es un PE invalido y Windows no la puede
            # arrancar ("not compatible with the version of Windows").
            destination = os.path.join(scripts, "Instant2.exe")
            with open(destination, "w", encoding="utf-8") as handle:
                handle.write("@echo off\r\necho fake %*\r\n")
            source = os.path.join(temp, "nuevo.exe")
            with open(source, "wb") as handle:
                handle.write(b"MZ" + b"\x00" * 200)
            with open(source, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()

            env = self._helper_env(appdata)
            log = os.path.join(appdata, "instant2", "update.log")
            os.makedirs(os.path.dirname(log), exist_ok=True)
            # El helper se lanza como lo hace la app: con la salida ya
            # apuntando al log (por eso el ruido del hijo llegaba al log).
            # El padre cierra su handle enseguida: si lo mantiene, el
            # helper no puede abrir el log para escribir sus mensajes.
            output = open(log, "a", encoding="utf-8", errors="replace")
            try:
                process = subprocess.Popen(
                    ["cmd", "/c", helper, source, digest, destination],
                    env=env, stdout=output, stderr=subprocess.STDOUT)
            finally:
                output.close()
            result = process.wait(timeout=120)

            with open(log, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
            self.assertEqual(result, 1)
            # El ruido de Windows al arrancar el ejecutable no esta en el
            # log: no dice nada del resultado de la actualizacion.
            self.assertNotIn("not compatible with the version of Windows",
                             content)


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
            (pending_patch, log_patch, _installer_patch,
             _result_patch) = self._isolated(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch, _result_patch,
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

    def test_the_installer_log_path_is_quoted(self):
        """Inno parsea la linea de comandos cruda: un espacio en el
        %APPDATA% mandaba el log a otro lado y el toast no tenia log."""
        logic, _emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            (pending_patch, log_patch, installer_patch,
             result_patch) = _isolated_update_paths(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, installer_patch, result_patch,
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
            argv = run.call_args.args[0]
            log_arg = [part for part in argv if str(part).startswith("/LOG=")]
            self.assertEqual(len(log_arg), 1)
            self.assertTrue(log_arg[0].startswith('/LOG="'))
            self.assertTrue(log_arg[0].endswith('"'))
            self.assertIn("installer.log", log_arg[0])

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
            (pending_patch, log_patch, installer_patch,
             result_patch) = _isolated_update_paths(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, installer_patch, result_patch,
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
            (pending_patch, log_patch, _installer_patch,
             _result_patch) = self._isolated(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch, _result_patch,
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
                    patch("instant2_app.gui._installer_result_path",
                          return_value=os.path.join(temp, "result.json")), \
                    patch("instant2_app.gui._helper_result_path",
                          return_value=os.path.join(temp, "helper.json")), \
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
                       return_value=pending), \
                    patch("instant2_app.gui._installer_result_path",
                          return_value=os.path.join(temp, "result.json")), \
                    patch("instant2_app.gui._helper_result_path",
                          return_value=os.path.join(temp, "helper.json")):
                self.assertIsNone(gui._pending_update_report())

    def test_applied_update_is_confirmed_on_the_next_start(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "pending-update.json")
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending), \
                    patch("instant2_app.gui._installer_result_path",
                          return_value=os.path.join(temp, "result.json")), \
                    patch("instant2_app.gui._helper_result_path",
                          return_value=os.path.join(temp, "helper.json")), \
                    patch("instant2_app.gui._current_version",
                          return_value="0.2.0"):
                gui._write_pending_update("0.2.0", os.path.join(temp, "x.log"))
                report = gui._pending_update_report()
            self.assertIsNotNone(report)
            title, message, level = report
            self.assertEqual(title, "Actualización aplicada")
            self.assertEqual(level, "info")
            self.assertIn("0.2.0", message)

    def test_the_installer_result_is_readable_after_the_panel_dies(self):
        """El codigo de salida queda anotado aunque el panel muera.

        Inno cierra el panel para reemplazar los archivos: el proceso que
        espera al instalador puede no llegar a leer su codigo de salida.
        """
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "pending-update.json")
            result = os.path.join(temp, "installer-result.json")
            log_path = os.path.join(temp, "installer.log")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("ERROR: el instalador se abortó\n")
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending), \
                    patch("instant2_app.gui._installer_result_path",
                          return_value=result), \
                    patch("instant2_app.gui._helper_result_path",
                          return_value=os.path.join(temp, "helper.json")), \
                    patch("instant2_app.gui._current_version",
                          return_value="0.1.5"):
                gui._write_pending_update("0.2.0", log_path)
                gui._write_installer_result(5, log_path, "0.2.0")
                report = gui._pending_update_report()
            self.assertIsNotNone(report)
            title, message, level = report
            self.assertEqual(title, "Actualización sin aplicar")
            self.assertEqual(level, "error")
            self.assertIn("5", message)
            self.assertIn("el instalador se abortó", message)
            # La nota y el resultado se consumen: no se repita el error.
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending), \
                    patch("instant2_app.gui._installer_result_path",
                          return_value=result), \
                    patch("instant2_app.gui._helper_result_path",
                          return_value=os.path.join(temp, "helper.json")):
                self.assertIsNone(gui._pending_update_report())

    def test_an_update_without_version_is_never_reported_as_applied(self):
        """Sin version anotada no se puede decir que se aplico."""
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "pending-update.json")
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending), \
                    patch("instant2_app.gui._installer_result_path",
                          return_value=os.path.join(temp, "result.json")), \
                    patch("instant2_app.gui._helper_result_path",
                          return_value=os.path.join(temp, "helper.json")), \
                    patch("instant2_app.gui._current_version",
                          return_value="0.1.5"):
                gui._write_pending_update("", "")
                report = gui._pending_update_report()
            self.assertIsNotNone(report)
            title, message, level = report
            self.assertNotEqual(title, "Actualización aplicada")
            self.assertEqual(level, "warn")
            self.assertIn("no se puede confirmar", message)

    def test_a_helper_that_does_not_start_leaves_no_second_toast(self):
        """Si el helper no arranca, la nota se limpia: no hay otro toast.

        Sin limpiar la nota, el arranque siguiente reportaba el mismo
        fallo con un segundo toast rojo.
        """
        logic, emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp)
            logic._ready_update = (target, digest, "0.2.0")
            (pending_patch, log_patch, _installer_patch,
             _result_patch) = _isolated_update_paths(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="portable"),
                patch("instant2_app.gui.os.path.isfile", return_value=True),
                pending_patch, log_patch, _installer_patch, _result_patch,
                patch("instant2_app.gui.subprocess.Popen",
                      side_effect=OSError("el helper no arranco")),
            ):
                logic.handle({"op": "apply_update"})
            # La nota no queda: el proximo arranque no repite el error.
            self.assertFalse(os.path.exists(
                os.path.join(temp, "pending-update.json")))
            toasts = _toasts(emitted)
            self.assertTrue(any(toast["title"] == "Actualización sin aplicar"
                                for toast in toasts))

    def test_installed_update_notes_the_requested_version(self):
        logic, _emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            (pending_patch, log_patch, _installer_patch,
             _result_patch) = self._isolated(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch, _result_patch,
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

    def test_the_update_does_not_promise_a_restart_that_did_not_happen(self):
        """Con el PID reciclado no hay daemon: el mensaje tiene que decirlo.

        Antes el toast de la actualizacion decia siempre "Se reinicia el
        dictado con ella", pero con el PID reciclado no se frena nada y
        tampoco hay nada que reiniciar: el mensaje mentia.
        """
        logic, emitted = _logic()
        logic._last_daemon_running = True
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp)
            with patch("instant2_app.update.apply_binary_update",
                       return_value=target), \
                    patch("instant2_app.gui._pid_value", return_value=4242), \
                    patch("instant2_app.gui._pid_is_instant", return_value=False), \
                    patch("instant2_app.gui.daemon_is_running",
                          return_value=False), \
                    patch("instant2_app.gui.subprocess.Popen"):
                logic._install_in_place(target, digest)
                logic.tasks.wait()
        message = " ".join(toast["message"] for toast in _toasts(emitted))
        self.assertNotIn("Se reinicia el dictado", message)
        self.assertIn("no se reinició", message)

    def test_the_update_says_when_the_dictation_was_restarted(self):
        """Si el daemon se frena y vuelve, el mensaje lo dice."""
        logic, emitted = _logic()
        logic._last_daemon_running = True
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp)
            with patch("instant2_app.update.apply_binary_update",
                       return_value=target), \
                    patch("instant2_app.gui._pid_value", return_value=4242), \
                    patch("instant2_app.gui._pid_is_instant", return_value=None), \
                    patch("instant2_app.gui.daemon_is_running",
                          return_value=False), \
                    patch("instant2_app.gui.subprocess.Popen"):
                logic._install_in_place(target, digest)
                logic.tasks.wait()
        message = " ".join(toast["message"] for toast in _toasts(emitted))
        self.assertIn("Se reinicia el dictado", message)

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
            (pending_patch, log_patch, _installer_patch,
             _result_patch) = _isolated_update_paths(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch, _result_patch,
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
            (pending_patch, log_patch, _installer_patch,
             _result_patch) = _isolated_update_paths(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch, _installer_patch, _result_patch,
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

    def test_the_two_lines_of_an_error_toast_have_the_same_prefix(self):
        """Un solo grep tiene que encontrar las dos lineas del error.

        `_toast_error` deja el traceback y `toast()` deja el mensaje que ve
        el usuario: con prefijos distintos no se podian correlacionar.
        """
        logic, _emitted = _logic()
        with self.assertLogs("instant2", level="ERROR") as captured:
            gui._toast_error(logic, "Actualización sin aplicar",
                             RuntimeError("se abortó el instalador"))
        self.assertGreaterEqual(len(captured.output), 2)
        for line in captured.output:
            self.assertIn("toast de error: Actualización sin aplicar:", line)

    def test_a_successful_update_is_also_logged(self):
        """El actualizador tiene que ser observable cuando sale bien."""
        logic, _emitted = _logic()
        with patch("instant2_app.gui._pending_update_report",
                   return_value=("Actualización aplicada",
                                 "Instant2 quedó en v0.2.0.", "info")):
            with self.assertLogs("instant2", level="INFO") as captured:
                logic._report_pending_update()
        self.assertTrue(any("Actualización aplicada" in line
                            for line in captured.output))


class StaleUpdateResultTests(unittest.TestCase):
    """Las notas de actualización vencen: no se reportan para siempre.

    Una nota que nunca se consumió (el usuario no volvió a abrir la app, o la
    actualización se abortó) quedaba en el %APPDATA% y se reportaba en cada
    arranque: un toast rojo "Actualización sin aplicar" días después de una
    actualización que el usuario nunca pidió.
    """

    BAT = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "instant2-update.bat"))

    def _isolated(self, directory):
        return _isolated_update_paths(directory)

    def test_a_stale_helper_result_is_discarded_without_reporting_it(self):
        """Un resultado rancio no se reporta: se borra y no hay toast."""
        with tempfile.TemporaryDirectory() as temp:
            result = os.path.join(temp, "helper-result.json")
            log_path = os.path.join(temp, "update.log")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("ERROR: el daemon nuevo no arranco\n")
            with open(result, "w", encoding="utf-8") as handle:
                json.dump({"returncode": 1, "log": log_path,
                           "ts": time.time() - gui._UPDATE_RESULT_TTL_SECONDS
                           - 60}, handle)
            (pending_patch, _log_patch, _installer_patch,
             _result_patch) = self._isolated(temp)
            with pending_patch, _log_patch, _installer_patch, _result_patch:
                self.assertIsNone(gui._pending_update_report())
            # La nota vencida no queda para el siguiente arranque.
            self.assertFalse(os.path.exists(result))

    def test_a_fresh_helper_result_is_still_reported(self):
        """El caso legítimo se sigue reportando: reabrir la app right after."""
        with tempfile.TemporaryDirectory() as temp:
            result = os.path.join(temp, "helper-result.json")
            log_path = os.path.join(temp, "update.log")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("ERROR: el daemon nuevo no arranco\n")
            with open(result, "w", encoding="utf-8") as handle:
                json.dump({"returncode": 1, "log": log_path,
                           "ts": time.time()}, handle)
            (pending_patch, _log_patch, _installer_patch,
             _result_patch) = self._isolated(temp)
            with pending_patch, _log_patch, _installer_patch, _result_patch:
                report = gui._pending_update_report()
            self.assertIsNotNone(report)
            title, message, level = report
            self.assertEqual(title, "Actualización sin aplicar")
            self.assertEqual(level, "error")
            self.assertIn("el daemon nuevo no arranco", message)

    def test_a_stale_pending_note_is_discarded(self):
        """La nota de la actualización en curso también vence."""
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "pending-update.json")
            with open(pending, "w", encoding="utf-8") as handle:
                json.dump({"version": "0.2.0", "log": "",
                           "ts": time.time() - gui._UPDATE_RESULT_TTL_SECONDS
                           - 60}, handle)
            (pending_patch, _log_patch, _installer_patch,
             _result_patch) = self._isolated(temp)
            with pending_patch, _log_patch, _installer_patch, _result_patch:
                self.assertIsNone(gui._pending_update_report())
            self.assertFalse(os.path.exists(pending))

    def test_a_note_without_a_timestamp_uses_the_date_of_the_file(self):
        """Una nota sin `ts` (de una versión anterior) se fecha con su mtime."""
        with tempfile.TemporaryDirectory() as temp:
            result = os.path.join(temp, "helper-result.json")
            with open(result, "w", encoding="utf-8") as handle:
                json.dump({"returncode": 1, "log": ""}, handle)
            old = time.time() - gui._UPDATE_RESULT_TTL_SECONDS - 60
            os.utime(result, (old, old))
            (pending_patch, _log_patch, _installer_patch,
             _result_patch) = self._isolated(temp)
            with pending_patch, _log_patch, _installer_patch, _result_patch:
                self.assertIsNone(gui._pending_update_report())

    def test_the_pending_note_carries_its_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = os.path.join(temp, "pending-update.json")
            with patch("instant2_app.gui._pending_update_path",
                       return_value=pending):
                gui._write_pending_update("0.2.0", os.path.join(temp, "x.log"))
            with open(pending, encoding="utf-8") as handle:
                note = json.load(handle)
            self.assertIn("ts", note)
            self.assertAlmostEqual(note["ts"], time.time(), delta=60)

    def test_the_installer_result_carries_its_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            result = os.path.join(temp, "installer-result.json")
            with patch("instant2_app.gui._installer_result_path",
                       return_value=result):
                gui._write_installer_result(5, os.path.join(temp, "x.log"))
            with open(result, encoding="utf-8") as handle:
                note = json.load(handle)
            self.assertIn("ts", note)
            self.assertAlmostEqual(note["ts"], time.time(), delta=60)

    @unittest.skipUnless(os.name == "nt", "el helper es un .bat de Windows")
    def test_the_helper_dates_its_own_result(self):
        """El `.bat` anota la fecha de su resultado: sin ella no vencería."""
        with tempfile.TemporaryDirectory() as temp:
            appdata = os.path.join(temp, "appdata")
            os.makedirs(appdata)
            env = dict(os.environ)
            env["APPDATA"] = appdata
            env["INSTANT2_UPDATE_LOG_REDIRECTED"] = "1"
            result = subprocess.run(
                ["cmd", "/c", self.BAT], env=env, capture_output=True,
                text=True, timeout=120)
            self.assertEqual(result.returncode, 1)
            path = os.path.join(appdata, "instant2", "helper-result.json")
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertIn("ts", payload)
            self.assertAlmostEqual(payload["ts"], time.time(), delta=300)


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
