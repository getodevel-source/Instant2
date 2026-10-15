"""CLI-to-GUI startup mode and daemon singleton behavior."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import __main__ as entry


class AppLifecycleTests(unittest.TestCase):
    def test_frozen_child_environment_resets_onefile_extraction(self):
        from instant2_app import launch
        with patch.object(launch.sys, "frozen", True, create=True), \
                patch.dict(os.environ, {"PYINSTALLER_RESET_ENVIRONMENT": "0"}):
            env = launch.app_environment()
            self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
            self.assertEqual(os.environ["PYINSTALLER_RESET_ENVIRONMENT"], "0")

    def test_daemon_mutex_released_after_failed_start(self):
        handle = object()
        instance = SimpleNamespace(run=Mock(side_effect=FileNotFoundError("model missing")))
        daemon = Mock(return_value=instance)
        with patch.object(entry, "_log_setup"), \
                patch.object(entry.logging, "getLogger"), \
                patch("instant2_app.config.load", return_value={}), \
                patch("instant2_app.daemon_lifecycle.acquire_daemon_mutex", return_value=handle), \
                patch("instant2_app.daemon_lifecycle.release_daemon_mutex") as release, \
                patch.dict(sys.modules, {"instant2_app.daemon": SimpleNamespace(Daemon=daemon)}):
            self.assertEqual(entry.main(["run"]), 2)
        daemon.assert_called_once_with({})
        instance.run.assert_called_once_with()
        release.assert_called_once_with(handle)

    def test_existing_gui_receives_start_setup_and_close_actions(self):
        from instant2_app import gui_lifecycle
        hwnd = 0x1234
        user32 = SimpleNamespace(
            FindWindowW=Mock(return_value=hwnd),
            ShowWindow=Mock(),
            SetForegroundWindow=Mock(),
            PostMessageW=Mock(return_value=1),
        )
        with patch("instant2_app.gui_lifecycle.os.name", "nt"), \
                patch("instant2_app.gui_lifecycle._user32", return_value=user32):
            self.assertTrue(gui_lifecycle.focus_existing_gui(request_daemon_start=True))
            user32.PostMessageW.assert_called_once_with(
                hwnd, gui_lifecycle._GUI_ACTION_MESSAGE,
                gui_lifecycle._ACTION_START_DAEMON, 0)
            user32.PostMessageW.reset_mock()
            self.assertTrue(gui_lifecycle.focus_existing_gui(page="setup"))
            user32.PostMessageW.assert_called_once_with(
                hwnd, gui_lifecycle._GUI_ACTION_MESSAGE,
                gui_lifecycle._ACTION_SETUP, 0)
            user32.PostMessageW.reset_mock()
            self.assertTrue(gui_lifecycle.close_existing_gui())
            user32.PostMessageW.assert_called_once_with(
                hwnd, gui_lifecycle._WM_CLOSE, 0, 0)

    def test_terminate_existing_gui_prefers_graceful_close(self):
        from instant2_app import gui_lifecycle

        hwnd = 0x1234
        user32 = SimpleNamespace(
            FindWindowW=Mock(side_effect=[hwnd, hwnd, 0]),
            ShowWindow=Mock(), SetForegroundWindow=Mock(),
            PostMessageW=Mock(return_value=1),
        )
        with patch("instant2_app.gui_lifecycle.os.name", "nt"), \
                patch("instant2_app.gui_lifecycle._user32", return_value=user32), \
                patch("instant2_app.gui_lifecycle.time.sleep"):
            self.assertTrue(gui_lifecycle.terminate_existing_gui())
        # Cierre limpio: WM_CLOSE y la ventana desaparece; no se mata nada.
        user32.PostMessageW.assert_called_once_with(
            hwnd, gui_lifecycle._WM_CLOSE, 0, 0)

    def test_terminate_existing_gui_forces_when_close_is_ignored(self):
        import ctypes as ctypes_module
        from instant2_app import gui_lifecycle

        hwnd = 0x1234
        handle = 0x55
        user32 = SimpleNamespace(
            FindWindowW=Mock(return_value=hwnd),
            ShowWindow=Mock(), SetForegroundWindow=Mock(),
            PostMessageW=Mock(return_value=1),
            GetWindowThreadProcessId=Mock(),
        )
        kernel32 = SimpleNamespace(
            OpenProcess=Mock(return_value=handle),
            TerminateProcess=Mock(return_value=1),
            CloseHandle=Mock(),
        )
        real_byref = ctypes_module.byref

        def byref_with_pid(obj):
            obj.value = 4321
            return real_byref(obj)

        with patch("instant2_app.gui_lifecycle.os.name", "nt"), \
                patch("instant2_app.gui_lifecycle._user32", return_value=user32), \
                patch("instant2_app.gui_lifecycle.ctypes.WinDLL",
                      return_value=kernel32, create=True), \
                patch("instant2_app.gui_lifecycle.ctypes.byref",
                      side_effect=byref_with_pid), \
                patch("instant2_app.gui_lifecycle.time.sleep"), \
                patch("instant2_app.gui_lifecycle.time.monotonic",
                      side_effect=[0, 100, 100]):
            self.assertTrue(gui_lifecycle.terminate_existing_gui(graceful_ms=1))
        user32.GetWindowThreadProcessId.assert_called_once()
        kernel32.OpenProcess.assert_called_once_with(0x0001, False, 4321)
        kernel32.TerminateProcess.assert_called_once_with(handle, 0)
        kernel32.CloseHandle.assert_called_once_with(handle)

    def test_terminate_existing_gui_refuses_alien_window_class(self):
        from instant2_app import gui_lifecycle
        hwnd = 0x9999
        user32 = SimpleNamespace(
            FindWindowW=Mock(return_value=hwnd),
            GetClassNameW=Mock(side_effect=lambda h, buf, n: setattr(buf, "value", "Chrome_WidgetWin_1") or 1),
            ShowWindow=Mock(), SetForegroundWindow=Mock(),
            PostMessageW=Mock(return_value=1),
            GetWindowThreadProcessId=Mock(),
        )
        with patch("instant2_app.gui_lifecycle.os.name", "nt"), \
                patch("instant2_app.gui_lifecycle._user32", return_value=user32):
            self.assertFalse(gui_lifecycle.terminate_existing_gui())
        user32.PostMessageW.assert_not_called()

    def test_posix_daemon_mutex_uses_pidfile_and_flock(self):
        import tempfile
        from instant2_app import daemon_lifecycle
        with tempfile.TemporaryDirectory() as d:
            with patch("instant2_app.daemon_lifecycle.os.name", "posix"), \
                    patch("instant2_app.paths.config_dir", return_value=d):
                h1 = daemon_lifecycle.acquire_daemon_mutex()
                self.assertIsNotNone(h1)
                self.assertTrue(os.path.isfile(os.path.join(d, "instant2.pid")))
                # Segunda instancia en el mismo proceso/thread con lock ya tomado
                daemon_lifecycle.release_daemon_mutex(h1)
                self.assertIsNone(daemon_lifecycle._lock_file)
    def test_write_pid_keeps_inode_when_lock_is_ours(self):
        import tempfile
        from instant2_app import daemon, daemon_lifecycle
        with tempfile.TemporaryDirectory() as d:
            with patch("instant2_app.daemon_lifecycle.os.name", "posix"), \
                    patch("instant2_app.paths.config_dir", return_value=d):
                if os.name == "nt" or not hasattr(os, "stat"):
                    self.skipTest("requiere inodos POSIX")
                try:
                    __import__("fcntl")
                except ImportError:
                    self.skipTest("requiere flock POSIX")
                handle = daemon_lifecycle.acquire_daemon_mutex()
                self.assertIsNotNone(handle)
                try:
                    before = os.stat(os.path.join(d, "instant2.pid")).st_ino
                    daemon.write_pid()
                    after = os.stat(os.path.join(d, "instant2.pid")).st_ino
                    self.assertEqual(before, after)
                    with open(os.path.join(d, "instant2.pid"), encoding="utf-8") as f:
                        self.assertEqual(f.read().strip(), str(os.getpid()))
                finally:
                    daemon_lifecycle.release_daemon_mutex(handle)

    def test_write_pid_after_replace_still_holds_lock(self):
        """Segunda instancia tras un replace ajeno no evade el flock (G1)."""
        import tempfile
        from instant2_app import daemon_lifecycle
        with tempfile.TemporaryDirectory() as d:
            with patch("instant2_app.daemon_lifecycle.os.name", "posix"), \
                    patch("instant2_app.paths.config_dir", return_value=d):
                try:
                    import fcntl
                except ImportError:
                    self.skipTest("requiere flock POSIX")
                if os.name == "nt":
                    self.skipTest("requiere flock POSIX")
                first = daemon_lifecycle.acquire_daemon_mutex()
                self.assertIsNotNone(first)
                old_file = daemon_lifecycle._lock_file
                try:
                    # Un escritor ajeno reemplaza el path (inodo nuevo).
                    with open(os.path.join(d, "instant2.pid"), "w", encoding="utf-8") as f:
                        f.write("999999")
                    # El dueño sigue lockeando el inodo viejo: reabrir el path
                    # nuevo NO debe poder tomar el lock mientras el dueño vive.
                    probe = open(os.path.join(d, "instant2.pid"), "a+b")
                    try:
                        with self.assertRaises(OSError):
                            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    finally:
                        probe.close()
                    self.assertIs(old_file, daemon_lifecycle._lock_file)
                finally:
                    daemon_lifecycle.release_daemon_mutex(first)
    def test_write_pid_uses_owned_lock_fd_without_replace(self):
        """Con lock propio, write_pid escribe en el fd (no replace) (G1)."""
        import tempfile
        from instant2_app import daemon, daemon_lifecycle
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "instant2.pid")
            with open(pidfile, "w", encoding="utf-8") as f:
                f.write("viejo")
            fd = open(pidfile, "r+b")
            try:
                with patch.object(daemon, "pid_path", return_value=pidfile), \
                        patch.object(daemon_lifecycle, "_lock_file", fd), \
                        patch("os.replace", side_effect=AssertionError("hizo replace")):
                    daemon.write_pid()
                fd.flush()
                with open(pidfile, encoding="utf-8") as f:
                    self.assertEqual(f.read().strip(), str(os.getpid()))
            finally:
                fd.close()


    def test_gui_mutex_failure_reports_unavailable(self):
        from instant2_app import gui
        with patch.object(gui.sys, "platform", "win32"), \
                patch("instant2_app.gui_lifecycle.acquire_gui_mutex",
                      side_effect=OSError("mutex unavailable")), \
                self.assertLogs("instant2", level="ERROR") as errors:
            self.assertEqual(gui.run_gui(), 2)
        self.assertIn("interfaz gráfica no disponible", errors.output[0])


    def test_direct_launch_requests_daemon_start(self):
        for platform in ("win32", "linux", "darwin"):
            with self.subTest(platform=platform), \
                    patch.object(entry, "_log_setup"), \
                    patch.object(entry, "_run_gui", return_value=0) as run_gui, \
                    patch.object(entry.sys, "platform", platform):
                self.assertEqual(entry.main([]), 0)
            run_gui.assert_called_once_with("home", start_daemon_on_open=True)

    def test_setup_and_os_autostart_flags_open_panel_on_every_os(self):
        for platform in ("win32", "linux", "darwin"):
            for argv, expected in (
                    (["setup"], None),
                    (["setup", "--autostart"], True),
                    (["setup", "--no-autostart"], False)):
                with self.subTest(platform=platform, argv=argv), \
                        patch.object(entry, "_log_setup"), \
                        patch.object(entry, "_run_gui", return_value=0) as run_gui, \
                        patch.object(entry.sys, "platform", platform):
                    self.assertEqual(entry.main(argv), 0)
                run_gui.assert_called_once_with("setup", autostart_override=expected)

    def test_diagnostics_opens_panel_on_every_os(self):
        for platform in ("win32", "linux", "darwin"):
            with self.subTest(platform=platform), \
                    patch.object(entry, "_log_setup"), \
                    patch.object(entry, "_run_gui", return_value=0) as run_gui, \
                    patch.object(entry.sys, "platform", platform):
                self.assertEqual(entry.main(["diagnostics"]), 0)
            run_gui.assert_called_once_with("diagnostics")

    def test_diagnostics_falls_back_to_console_without_panel(self):
        with patch.object(entry, "_log_setup"), \
                patch.object(entry, "_run_gui", return_value=2), \
                patch("instant2_app.daemon.cmd_check", return_value=0) as check, \
                patch("instant2_app.config.load", return_value={}), \
                patch.object(entry.sys, "platform", "linux"):
            self.assertEqual(entry.main(["diagnostics"]), 0)
        check.assert_called_once_with({})

    def test_voice_diagnostics_hide_internal_component_names(self):
        import io
        from contextlib import redirect_stdout

        from instant2_app import daemon
        engine = Mock()
        engine.recognizer.side_effect = FileNotFoundError(
            "modelo Parakeet incompleto")
        output = io.StringIO()
        with patch("instant2_app.daemon.hotkey.create",
                   return_value=SimpleNamespace(stop=Mock())), \
                patch("instant2_app.daemon.audio.resolve_mic", return_value=None), \
                patch("instant2_app.daemon.audio.probe", return_value=True), \
                patch("instant2_app.daemon.Engine", return_value=engine), \
                redirect_stdout(output):
            self.assertEqual(daemon.cmd_check({"key": "f9"}), 2)

        self.assertIn("COMPONENTES DE VOZ INCOMPLETOS", output.getvalue())
        self.assertNotIn("Parakeet", output.getvalue())

    def test_stop_command_reports_daemon_state(self):
        import io
        from contextlib import redirect_stdout

        for stopped, panel, expected in ((True, False, "Instant2 detenido."),
                                         (False, True, "Instant2 detenido."),
                                         (False, False, "No había nada corriendo.")):
            buffer = io.StringIO()
            with self.subTest(stopped=stopped, panel=panel), \
                    patch.object(entry, "_log_setup"), \
                    patch("instant2_app.gui.stop_daemon",
                          return_value=stopped) as stop, \
                    patch("instant2_app.gui_lifecycle.terminate_existing_gui",
                          return_value=panel) as close_panel, \
                    redirect_stdout(buffer):
                self.assertEqual(entry.main(["stop"]), 0)
            stop.assert_called_once_with()
            close_panel.assert_called_once_with()
            self.assertIn(expected, buffer.getvalue())

    def test_setup_flags_keep_terminal_wizard(self):
        with patch.object(entry, "_log_setup"), \
                patch.object(entry, "_run_gui", return_value=0) as run_gui, \
                patch("instant2_app.setup.cmd_setup", return_value=0) as cmd, \
                patch.object(entry.sys, "platform", "darwin"):
            self.assertEqual(entry.main(["setup", "--yes"]), 0)
        run_gui.assert_not_called()
        self.assertEqual(cmd.call_args.args[0], ["--yes"])

    def test_setup_tui_flag_forces_terminal_wizard(self):
        with patch.object(entry, "_log_setup"), \
                patch.object(entry, "_run_gui", return_value=0) as run_gui, \
                patch("instant2_app.setup.cmd_setup", return_value=0) as cmd, \
                patch.object(entry.sys, "platform", "linux"):
            self.assertEqual(entry.main(["setup", "--tui"]), 0)
        run_gui.assert_not_called()
        # --tui no se reenvía al asistente: es una elección de la entrada.
        self.assertEqual(cmd.call_args.args[0], [])

    def test_setup_voice_flags_are_declared_and_forwarded(self):
        with patch.object(entry, "_log_setup"), \
                patch.object(entry, "_run_gui", return_value=0) as run_gui, \
                patch("instant2_app.setup.cmd_setup", return_value=0) as cmd, \
                patch.object(entry.sys, "platform", "linux"):
            self.assertEqual(entry.main(
                ["setup", "--vad-model", "ten",
                 "--blank-penalty", "0.5"]), 0)
        run_gui.assert_not_called()
        forwarded = cmd.call_args.args[0]
        self.assertIn("--vad-model", forwarded)
        self.assertIn("ten", forwarded)
        self.assertIn("--blank-penalty", forwarded)
        self.assertIn("0.5", forwarded)

    def test_existing_daemon_mutex_skips_second_daemon_construction(self):
        daemon = Mock()
        with patch.object(entry, "_log_setup"), \
                patch("instant2_app.config.load", return_value={}), \
                patch("instant2_app.daemon_lifecycle.acquire_daemon_mutex", return_value=None), \
                patch.dict(sys.modules, {"instant2_app.daemon": SimpleNamespace(Daemon=daemon)}):
            self.assertEqual(entry.main(["run"]), 0)
        daemon.assert_not_called()



class WebEngineHelperPathTests(unittest.TestCase):
    """En congelados, QtWebEngine necesita que le digan dónde está su helper."""

    def test_sets_process_path_from_the_extracted_tree(self):
        import tempfile
        from instant2_app import launch

        with tempfile.TemporaryDirectory() as root:
            helper = os.path.join(root, "PySide6", "QtWebEngineProcess.app",
                                  "Contents", "MacOS", "QtWebEngineProcess")
            os.makedirs(os.path.dirname(helper))
            with open(helper, "w", encoding="utf-8") as handle:
                handle.write("")
            env = dict(os.environ)
            env.pop("QTWEBENGINEPROCESS_PATH", None)
            with patch.object(launch.sys, "frozen", True, create=True), \
                    patch.object(launch.sys, "_MEIPASS", root, create=True), \
                    patch.dict(os.environ, env, clear=True):
                found = launch.prepare_webengine_env()
                self.assertEqual(found, helper)
                self.assertEqual(os.environ["QTWEBENGINEPROCESS_PATH"], helper)

    def test_untouched_when_not_frozen_or_already_set(self):
        import tempfile
        from instant2_app import launch

        with tempfile.TemporaryDirectory() as root:
            os.environ.pop("QTWEBENGINEPROCESS_PATH", None)
            with patch.object(launch.sys, "_MEIPASS", root, create=True):
                self.assertIsNone(launch.prepare_webengine_env())
            with patch.object(launch.sys, "frozen", True, create=True), \
                    patch.object(launch.sys, "_MEIPASS", root, create=True), \
                    patch.dict(os.environ, {"QTWEBENGINEPROCESS_PATH": "ya-esta"}):
                self.assertIsNone(launch.prepare_webengine_env())
                self.assertEqual(os.environ["QTWEBENGINEPROCESS_PATH"], "ya-esta")


if __name__ == "__main__":
    unittest.main()
