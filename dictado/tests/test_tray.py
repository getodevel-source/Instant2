"""Ciclo de vida del icono de bandeja."""
import os
import runpy
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app.tray import TrayIcon, _checked_shell_notify_icon


class TrayLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.shutdown = threading.Event()
        self.tray = TrayIcon(self.shutdown)

    def test_menu_offers_open_as_default_action(self):
        try:
            import pystray
        except ImportError:
            self.skipTest("pystray no disponible")
        menu = self.tray._build_menu(pystray)
        default_item = next(
            (item for item in menu if getattr(item, "default", False)), None)
        self.assertIsNotNone(default_item)
        self.assertEqual(default_item.text, "Abrir ventana de Instant2")
        self.assertTrue(default_item.visible)
        self.assertTrue(default_item.enabled)

    def test_shell_notify_icon_reports_success_only_when_windows_accepts_it(self):
        added = []
        checked = _checked_shell_notify_icon(
            lambda message, data: True, 0, lambda: added.append(True))
        self.assertTrue(checked(0, object()))
        self.assertEqual(added, [True])

        rejected = _checked_shell_notify_icon(
            lambda message, data: False, 0, lambda: added.append(False))
        with self.assertRaisesRegex(OSError, "Windows rechazó"):
            rejected(0, object())
        self.assertEqual(added, [True])

    def test_pyinstaller_pillow_hook_includes_ico_writer_for_pystray(self):
        hook = os.path.join(os.path.dirname(__file__), "..", "hooks",
                            "hook-PIL.Image.py")
        config = runpy.run_path(hook)
        self.assertIn("PIL.IcoImagePlugin", config["hiddenimports"])

    def test_show_icon_retries_transient_registration_failure(self):
        class FlakyIcon:
            def __init__(self):
                self.attempts = 0
                self._visible = False

            @property
            def visible(self):
                return self._visible

            @visible.setter
            def visible(self, value):
                self.attempts += 1
                if self.attempts == 1:
                    raise OSError("transient")
                self._visible = value

        icon = FlakyIcon()
        with patch("instant2_app.tray.time.sleep"):
            self.tray._show_icon(icon)
        self.assertTrue(icon.visible)
        self.assertEqual(icon.attempts, 2)
        self.assertTrue(self.tray._tray_ready.is_set())

    def test_show_icon_logs_persistent_registration_failure(self):
        class BrokenIcon:
            @property
            def visible(self):
                return False

            @visible.setter
            def visible(self, _value):
                raise OSError("rejected")

        with patch("instant2_app.tray.time.sleep"):
            with self.assertLogs("instant2", level="ERROR") as errors:
                self.tray._show_icon(BrokenIcon())
        self.assertTrue(any("no se pudo preparar o mostrar" in line
                            for line in errors.output))
        self.assertTrue(self.tray._tray_ready.is_set())

    def test_exit_closes_gui_and_requests_shutdown(self):
        self.shutdown.clear()
        with patch("instant2_app.tray.close_existing_gui") as close_gui:
            self.tray._on_exit(None, None)
        self.assertTrue(self.shutdown.is_set())
        self.assertTrue(close_gui.called)

    def test_launch_reuses_existing_gui_without_duplicate(self):
        for args, requested, page in (((), True, None), (("setup",), False, "setup")):
            with (
                patch("instant2_app.tray.focus_existing_gui", return_value=True) as focus,
                patch("instant2_app.tray.subprocess.Popen") as launch,
            ):
                outcome = self.tray._launch_gui(args)
            self.assertEqual(outcome, "focused")
            focus.assert_called_once_with(request_daemon_start=requested, page=page)
            launch.assert_not_called()

    def test_open_launches_isolated_gui_without_importing_gui(self):
        with (
            patch("instant2_app.tray.focus_existing_gui", return_value=False),
            patch("instant2_app.tray.app_command", return_value=["Instant2.exe"]) as command,
            patch("instant2_app.tray.app_environment",
                  return_value={"PYINSTALLER_RESET_ENVIRONMENT": "1"}),
            patch("instant2_app.tray.subprocess.Popen") as launch,
            patch.dict(sys.modules, {"instant2_app.gui": None}),
        ):
            outcome = self.tray._open(None, None)
        self.assertEqual(outcome, "launched")
        launch.assert_called_once()
        command.assert_called_once_with(())
        self.assertEqual(launch.call_args.kwargs["env"].get("PYINSTALLER_RESET_ENVIRONMENT"), "1")

    def test_open_reports_popen_failures(self):
        with (
            patch("instant2_app.tray.focus_existing_gui", return_value=False),
            patch("instant2_app.tray.app_command", return_value=["Instant2.exe"]),
            patch("instant2_app.tray.subprocess.Popen", side_effect=OSError("launch failed")),
        ):
            with self.assertLogs("instant2", level="ERROR") as errors:
                outcome = self.tray._open(None, None)
        self.assertEqual(outcome, "failed")
        self.assertTrue(any("no pude abrir la interfaz" in line for line in errors.output))


if __name__ == "__main__":
    unittest.main()
