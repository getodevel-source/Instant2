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
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import gui


class _SyncTasks:
    """TaskRunner sincrónico para los flujos de actualización."""

    def __init__(self):
        self.pending = 0

    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        self.pending += 1
        try:
            value = fn(lambda payload: on_progress and on_progress(payload))
        except Exception as exc:  # noqa: BLE001  (igual que el runner real)
            if on_error is not None:
                on_error(exc)
            value = None
        else:
            if on_result is not None:
                on_result(value)
        finally:
            self.pending -= 1
        return id(fn)

    def drain(self):
        return 0

    def close(self):
        self.pending = 0


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
            tasks=_SyncTasks(), schedule=lambda ms, fn: None)
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
        return (
            patch("instant2_app.gui._pending_update_path",
                  return_value=os.path.join(directory, "pending-update.json")),
            patch("instant2_app.gui._installer_log_path",
                  return_value=os.path.join(directory, "installer.log")),
        )

    def test_installer_runs_with_a_log_and_waits_for_the_result(self):
        logic, _emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            pending_patch, log_patch = self._isolated(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch,
                patch("instant2_app.gui.stop_daemon"),
                patch("instant2_app.gui.daemon_is_running", return_value=False),
                patch("instant2_app.gui.subprocess.run",
                      return_value=SimpleNamespace(returncode=0)) as run,
            ):
                logic.handle({"op": "apply_update"})

            self.assertEqual(run.call_count, 1)
            argv = run.call_args.args[0]
            self.assertIn("/SILENT", argv)
            self.assertTrue(any(str(part).startswith("/LOG=") for part in argv))
            self.assertTrue(logic._closed)

    def test_failed_installer_reports_the_log_instead_of_pretending(self):
        logic, emitted = _logic()
        logic._pending_update = _release()
        with tempfile.TemporaryDirectory() as temp:
            target, digest = _verified_file(temp, "Instant2-Setup.exe")
            logic._ready_update = (target, digest, "0.2.0")
            log_path = os.path.join(temp, "installer.log")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("Setup aborted\nERROR: no hay espacio\n")
            pending_patch, log_patch = self._isolated(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch,
                patch("instant2_app.gui.stop_daemon"),
                patch("instant2_app.gui.daemon_is_running", return_value=False),
                patch("instant2_app.gui.subprocess.run",
                      return_value=SimpleNamespace(returncode=5)) as run,
            ):
                logic.handle({"op": "apply_update"})

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
            pending_patch, log_patch = self._isolated(temp)
            with (
                patch("instant2_app.update.install_mode", return_value="installed"),
                pending_patch, log_patch,
                patch("instant2_app.gui.stop_daemon"),
                patch("instant2_app.gui.daemon_is_running", return_value=False),
                patch("instant2_app.gui.subprocess.run",
                      return_value=SimpleNamespace(returncode=0)),
            ):
                logic.handle({"op": "apply_update"})

            with open(os.path.join(temp, "pending-update.json"),
                      encoding="utf-8") as handle:
                import json
                note = json.load(handle)
            self.assertEqual(note["version"], "0.2.0")
            self.assertTrue(note["log"].endswith("installer.log"))


if __name__ == "__main__":
    unittest.main()
