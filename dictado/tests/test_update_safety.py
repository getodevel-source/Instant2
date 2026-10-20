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


if __name__ == "__main__":
    unittest.main()
