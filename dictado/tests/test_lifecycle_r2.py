"""Ronda 2: ciclo de vida (pidfile, GUI, daemon, autostart, warmup).

unittest clásico (como el resto de la suite): sin red, sin Qt, sin SO real.
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app import gui


class UpdateBatSelfcopyTests(unittest.TestCase):
    def test_selfcopy_compares_normalized_paths(self):
        """G2: :selfcopy compara %~f1 contra el DST normalizado, no el
        literal con '..'/relativo (ese nunca igualaba y no detectaba)."""
        repo = os.path.join(os.path.dirname(__file__), "..", "..")
        bat = os.path.normpath(os.path.join(repo, "scripts", "instant2-update.bat"))
        with open(bat, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("DSTFULL", source)
        self.assertIn('"%~f1"=="%DSTFULL%"', source)
        self.assertNotIn('"%~f1"=="%DST%"', source)

class PosixPidIsInstantTests(unittest.TestCase):
    def test_proc_cmdline_with_instant_is_ours(self):
        with patch("instant2_app.gui.os.name", "posix"), \
                patch("builtins.open", create=True) as fake_open:
            fake_open.return_value.__enter__.return_value.read.return_value = (
                b"python\x00-m\x00instant2_app\x00run\x00")
            self.assertTrue(gui._pid_is_instant(1234))

    def test_proc_cmdline_without_instant_is_alien(self):
        with patch("instant2_app.gui.os.name", "posix"), \
                patch("builtins.open", create=True) as fake_open:
            fake_open.return_value.__enter__.return_value.read.return_value = (
                b"notepad.exe\x00")
            self.assertFalse(gui._pid_is_instant(5678))

    def test_proc_missing_falls_back_to_ps(self):
        with patch("instant2_app.gui.os.name", "posix"), \
                patch("builtins.open", side_effect=FileNotFoundError), \
                patch("instant2_app.gui.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="python -m instant2_app run\n")
            self.assertTrue(gui._pid_is_instant(910))
            run.assert_called_once()
            self.assertIn("ps", run.call_args.args[0])

    def test_ps_without_instant_is_alien(self):
        with patch("instant2_app.gui.os.name", "posix"), \
                patch("builtins.open", side_effect=FileNotFoundError), \
                patch("instant2_app.gui.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="vim somefile\n")
            self.assertFalse(gui._pid_is_instant(911))

    def test_ps_dead_pid_is_not_instant(self):
        with patch("instant2_app.gui.os.name", "posix"), \
                patch("builtins.open", side_effect=FileNotFoundError), \
                patch("instant2_app.gui.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=1, stdout="")
            self.assertFalse(gui._pid_is_instant(912))


class WindowsDaemonRecognitionTests(unittest.TestCase):
    def test_frozen_mvp_daemon_is_detected_by_its_actual_exe_name(self):
        tasklist = SimpleNamespace(
            returncode=0,
            stdout='"Instant2-MVP.exe","4952","Console","1","40,000 K"\n')
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui._pid_value", return_value=4952), \
                patch.object(gui.sys, "frozen", True, create=True), \
                patch.object(gui.sys, "executable",
                             r"C:\\Instant2-MVP\\Instant2-MVP.exe"), \
                patch("instant2_app.gui.subprocess.run", return_value=tasklist), \
                patch("instant2_app.gui._pid_is_instant", return_value=True) as is_run:
            self.assertTrue(gui.daemon_is_running())
        self.assertEqual(is_run.call_count, 1)
        self.assertEqual(is_run.call_args.args[0], 4952)

    def test_windows_pid_must_be_a_run_subprocess(self):
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui.subprocess.run", return_value=SimpleNamespace(
                    returncode=0,
                    stdout='"C:\\Instant2-MVP.exe" setup')):
            self.assertFalse(gui._pid_is_instant(21820))
        with patch("instant2_app.gui.os.name", "nt"), \
                patch("instant2_app.gui.subprocess.run", return_value=SimpleNamespace(
                    returncode=0,
                    stdout='C:\\Instant2-MVP.exe run')):
            self.assertTrue(gui._pid_is_instant(4952))


class FindOurGuiTests(unittest.TestCase):
    def _fake_user32(self, windows):
        """windows: {hwnd: (title, classname)}; EnumWindows itera el dict."""
        def find_window(_cls, title):
            for hwnd, (wtitle, _wclass) in windows.items():
                if wtitle == title:
                    return hwnd
            return 0

        def enum_windows(callback, _param):
            for hwnd in list(windows):
                keep_going = callback(hwnd, 0)
                if not keep_going:
                    break
            return True

        def get_text(hwnd, buf, _n):
            buf.value = windows[hwnd][0]
            return len(buf.value)

        def get_class(hwnd, buf, _n):
            buf.value = windows[hwnd][1]
            return len(buf.value)

        return SimpleNamespace(
            FindWindowW=Mock(side_effect=find_window),
            EnumWindows=Mock(side_effect=enum_windows),
            GetWindowTextW=Mock(side_effect=get_text),
            GetClassNameW=Mock(side_effect=get_class),
            ShowWindow=Mock(), SetForegroundWindow=Mock(return_value=True),
            PostMessageW=Mock(return_value=1),
        )

    def test_exact_title_and_qt_class_wins(self):
        from instant2_app import gui_lifecycle
        windows = {0x100: ("Instant2", "Qt515QWindowIcon")}
        user32 = self._fake_user32(windows)
        self.assertEqual(gui_lifecycle._find_our_gui(user32), 0x100)

    def test_two_windows_first_qt_with_instant_title_wins(self):
        from instant2_app import gui_lifecycle
        windows = {
            0x200: ("Bloc de notas", "Notepad"),
            0x201: ("Instant2 — Ajustes", "Chrome_WidgetWin_1"),
            0x202: ("Instant2 — Ajustes", "Qt515QWindowIcon"),
        }
        user32 = self._fake_user32(windows)
        self.assertEqual(gui_lifecycle._find_our_gui(user32), 0x202)

    def test_alien_title_only_is_rejected(self):
        from instant2_app import gui_lifecycle
        windows = {0x300: ("Instant2", "Chrome_WidgetWin_1")}
        user32 = self._fake_user32(windows)
        self.assertIsNone(gui_lifecycle._find_our_gui(user32))


class PidfileOwnershipTests(unittest.TestCase):
    def test_daemon_pidfile_and_pid_path_agree(self):
        import tempfile
        from instant2_app import daemon, daemon_lifecycle
        with tempfile.TemporaryDirectory() as d:
            with patch("instant2_app.paths.config_dir", return_value=d):
                self.assertEqual(daemon_lifecycle._daemon_pidfile(), daemon.pid_path())

    def test_release_posix_unlinks_own_pid(self):
        import tempfile
        from instant2_app import daemon_lifecycle
        with tempfile.TemporaryDirectory() as d:
            with patch("instant2_app.daemon_lifecycle.os.name", "posix"), \
                    patch("instant2_app.paths.config_dir", return_value=d):
                handle = daemon_lifecycle.acquire_daemon_mutex()
                self.assertIsNotNone(handle)
                pidfile = os.path.join(d, "instant2.pid")
                self.assertTrue(os.path.isfile(pidfile))
                daemon_lifecycle.release_daemon_mutex(handle)
                self.assertIsNone(daemon_lifecycle._lock_file)
                self.assertFalse(os.path.exists(pidfile))

    def test_clear_pid_keeps_foreign_pid(self):
        import tempfile
        from instant2_app import daemon
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "instant2.pid")
            with open(pidfile, "w", encoding="utf-8") as handle:
                handle.write("999999")
            with patch.object(daemon, "pid_path", return_value=pidfile):
                daemon.clear_pid()
            self.assertTrue(os.path.isfile(pidfile))

    def test_clear_pid_with_expected_pid_cleans_stale_daemon(self):
        """stop tras kill deja el pidfile limpio: clear_pid(pid muerto) (G3)."""
        import tempfile
        from instant2_app import daemon
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "instant2.pid")
            with open(pidfile, "w", encoding="utf-8") as handle:
                handle.write("424242")
            with patch.object(daemon, "pid_path", return_value=pidfile):
                daemon.clear_pid(424242)
            self.assertFalse(os.path.exists(pidfile))

    def test_clear_pid_with_expected_pid_keeps_other_owner(self):
        import tempfile
        from instant2_app import daemon
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "instant2.pid")
            with open(pidfile, "w", encoding="utf-8") as handle:
                handle.write("777")
            with patch.object(daemon, "pid_path", return_value=pidfile):
                daemon.clear_pid(424242)
            self.assertTrue(os.path.isfile(pidfile))


class DaemonIsRunningTests(unittest.TestCase):
    def test_posix_eperm_means_alive(self):
        """kill-0 con EPERM = vivo sin permiso (G4)."""
        with patch("instant2_app.gui.os.name", "posix"), \
                patch("instant2_app.gui._pid_value", return_value=99999), \
                patch("instant2_app.gui.os.kill", side_effect=PermissionError(1, "denied")):
            self.assertTrue(gui.daemon_is_running())

    def test_posix_dead_pid_is_not_running(self):
        with patch("instant2_app.gui.os.name", "posix"), \
                patch("instant2_app.gui._pid_value", return_value=99999), \
                patch("instant2_app.gui.os.kill", side_effect=ProcessLookupError()):
            self.assertFalse(gui.daemon_is_running())

    def test_posix_recycled_pid_is_not_running(self):
        """kill-0 OK pero cmdline ajena = PID reciclado, no vivo (G5)."""
        with patch("instant2_app.gui.os.name", "posix"), \
                patch("instant2_app.gui._pid_value", return_value=99999), \
                patch("instant2_app.gui.os.kill", return_value=None), \
                patch("instant2_app.gui._pid_is_instant", return_value=False):
            self.assertFalse(gui.daemon_is_running())

    def test_posix_live_instant_pid_is_running(self):
        with patch("instant2_app.gui.os.name", "posix"), \
                patch("instant2_app.gui._pid_value", return_value=99999), \
                patch("instant2_app.gui.os.kill", return_value=None), \
                patch("instant2_app.gui._pid_is_instant", return_value=True):
            self.assertTrue(gui.daemon_is_running())

    def test_stop_daemon_cleans_stale_pidfile(self):
        """stop tras kill (proceso muerto) deja el pidfile limpio (G1+G3)."""
        import tempfile
        from instant2_app import gui as gui_module
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "instant2.pid")
            with open(pidfile, "w", encoding="utf-8") as handle:
                handle.write("424242")
            with patch("instant2_app.daemon.pid_path", return_value=pidfile), \
                    patch.object(gui_module, "_pid_value", return_value=424242), \
                    patch.object(gui_module, "_probe_daemon_process",
                                 return_value=(gui_module.DAEMON_STOPPED, None)):
                self.assertFalse(gui_module.stop_daemon())
            self.assertFalse(os.path.exists(pidfile))

    def test_stop_daemon_keeps_fresh_owner_pidfile(self):
        """stop no borra si el pidfile cambió de dueño entre lecturas."""
        import tempfile
        from instant2_app import gui as gui_module
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "instant2.pid")
            with open(pidfile, "w", encoding="utf-8") as handle:
                handle.write("424242")
            reads = iter([424242, 777777])
            with patch("instant2_app.daemon.pid_path", return_value=pidfile), \
                    patch.object(gui_module, "_pid_value", side_effect=lambda: next(reads)), \
                    patch.object(gui_module, "_probe_daemon_process",
                                 return_value=(gui_module.DAEMON_STOPPED, None)):
                self.assertFalse(gui_module.stop_daemon())
            with open(pidfile, encoding="utf-8") as handle:
                self.assertEqual(handle.read().strip(), "424242")


class SourceApplyGuardTests(unittest.TestCase):
    def test_allow_source_still_requires_explicit_target(self):
        from instant2_app import update as update_module
        with patch.object(update_module.sys, "platform", "linux"), \
                patch("instant2_app.update.install_mode", return_value="source"):
            with self.assertRaisesRegex(RuntimeError, "instalaci"):
                update_module.apply_binary_update("descarga", allow_source=True)

    def test_source_target_inside_prefix_is_rejected(self):
        from instant2_app import update as update_module
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(update_module.sys, "platform", "linux"), \
                patch("instant2_app.update.install_mode", return_value="source"), \
                patch.object(update_module.sys, "prefix", directory), \
                patch.object(update_module.sys, "executable",
                             os.path.join(directory, "bin", "python")):
            inside = os.path.join(directory, "bin", "instant2")
            with self.assertRaisesRegex(RuntimeError, "fuera del int"):
                update_module.apply_binary_update("descarga", inside,
                                                  allow_source=True)


class ForwardAckTests(unittest.TestCase):
    def test_forward_without_ack_is_false(self):
        from instant2_app import gui as gui_module

        class _Socket:
            def connectToServer(self, _name):
                pass

            def waitForConnected(self, _ms):
                return True

            def write(self, _payload):
                pass

            def flush(self):
                pass

            def bytesAvailable(self):
                return False

            def readAll(self):
                return SimpleNamespace(data=lambda: b"")

            def disconnectFromServer(self):
                pass

        class _Loop:
            def __init__(self):
                self.quit = lambda: None

            def exec(self):
                pass

        with patch.dict("sys.modules", {
                "PySide6": SimpleNamespace(QtCore=SimpleNamespace()),
                "PySide6.QtCore": SimpleNamespace(QEventLoop=_Loop,
                                                  QTimer=SimpleNamespace(singleShot=lambda *a: None)),
                "PySide6.QtNetwork": SimpleNamespace(QLocalSocket=_Socket)}):
            import importlib
            importlib.reload(gui_module)
        try:
            self.assertFalse(gui_module._forward_to_existing_gui("home", False))
        finally:
            import importlib
            importlib.reload(gui_module)

    def test_framing_roundtrip_single_payload(self):
        from instant2_app import gui as gui_module
        frame = gui_module._frame_gui_request("setup", True)
        payloads, rest = gui_module._unframe_gui_requests(frame)
        self.assertEqual(rest, b"")
        self.assertEqual(len(payloads), 1)
        import json
        self.assertEqual(json.loads(payloads[0].decode("utf-8")),
                         {"page": "setup", "daemon": True})

    def test_fragmented_frame_needs_both_halves(self):
        """Caso fragmentado: media frame no entrega nada; completa sí (G7)."""
        from instant2_app import gui as gui_module
        frame = gui_module._frame_gui_request("diagnostics", False)
        half = len(frame) // 2
        payloads, rest = gui_module._unframe_gui_requests(frame[:half])
        self.assertEqual(payloads, [])
        self.assertEqual(rest, frame[:half])
        payloads, rest = gui_module._unframe_gui_requests(rest + frame[half:])
        self.assertEqual(rest, b"")
        self.assertEqual(len(payloads), 1)
        import json
        self.assertEqual(json.loads(payloads[0].decode("utf-8"))["page"], "diagnostics")

    def test_oversize_frame_is_discarded(self):
        from instant2_app import gui as gui_module
        import struct
        bad = struct.pack(">I", gui_module._GUI_FRAME_MAX + 1)
        payloads, rest = gui_module._unframe_gui_requests(bad + b"x" * 10)
        self.assertEqual(payloads, [])

    def test_forward_sends_framed_payload(self):
        from instant2_app import gui as gui_module
        sent = {}

        class _Socket:
            readyRead = SimpleNamespace(connect=lambda *a: None)
            disconnected = SimpleNamespace(connect=lambda *a: None)

            def connectToServer(self, _name):
                pass

            def waitForConnected(self, _ms):
                return True

            def write(self, payload):
                sent["frame"] = bytes(payload)

            def flush(self):
                pass

            def bytesAvailable(self):
                return True

            def readAll(self):
                return SimpleNamespace(data=lambda: b"ok")

            def disconnectFromServer(self):
                pass

        class _Loop:
            def __init__(self):
                self.quit = lambda: None

            def exec(self):
                pass

        with patch.dict("sys.modules", {
                "PySide6": SimpleNamespace(QtCore=SimpleNamespace()),
                "PySide6.QtCore": SimpleNamespace(QEventLoop=_Loop,
                                                  QTimer=SimpleNamespace(singleShot=lambda *a: None)),
                "PySide6.QtNetwork": SimpleNamespace(QLocalSocket=_Socket)}):
            import importlib
            importlib.reload(gui_module)
            try:
                self.assertTrue(gui_module._forward_to_existing_gui("home", False))
                payloads, rest = gui_module._unframe_gui_requests(sent["frame"])
                self.assertEqual(rest, b"")
                self.assertEqual(len(payloads), 1)
            finally:
                importlib.reload(gui_module)


class MacBootstrapTests(unittest.TestCase):
    def test_bootstrap_failure_raises_instead_of_activado(self):
        import tempfile
        from instant2_app import autostart as autostart_module
        with tempfile.TemporaryDirectory() as home:
            saved = {key: os.environ.get(key) for key in ("HOME", "INSTANT2_AUTOSTART_PLATFORM")}
            os.environ["HOME"] = home
            os.environ["INSTANT2_AUTOSTART_PLATFORM"] = "darwin"
            try:
                real_getuid = getattr(os, "getuid", None)
                os.getuid = lambda: 501
                try:
                    with patch.object(autostart_module, "_mac_launchctl",
                                      return_value=(1, "boom")):
                        with self.assertRaisesRegex(RuntimeError, "NO qued"):
                            autostart_module.enable()
                finally:
                    if real_getuid is None:
                        del os.getuid
                    else:
                        os.getuid = real_getuid
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


class SetupWarmupTests(unittest.TestCase):
    def test_warmup_passes_vad_model_from_config(self):
        from instant2_app import setup as setup_module
        seen = {}

        class _Engine:
            def __init__(self, data_dir, threads=4, max_seg=20.0, vad_model="silero"):
                seen["vad_model"] = vad_model

            def recognizer(self):
                pass

            def vad(self):
                pass

        cfg = {"mic_hint": "", "mic_index": None, "key": "f9",
               "threads": 4, "max_seg": 20.0, "vad_model": "ten"}
        with patch("instant2_app.setup.config.load", return_value=dict(cfg)), \
                patch("instant2_app.setup.config.save", return_value="config.json"), \
                patch("instant2_app.deps.check", return_value={
                    "sherpa_onnx": {"ok": True}, "sounddevice": {"ok": True}}), \
                patch("instant2_app.deps.report", return_value=""), \
                patch("instant2_app.models.check", return_value={"voxcore": True, "vad": True}), \
                patch("instant2_app.setup._real_inputs", return_value=[]), \
                patch("instant2_app.autostart.is_enabled", return_value=False), \
                patch("instant2_app.autostart.describe", return_value="off"), \
                patch("instant2_app.engine.Engine", _Engine):
            setup_module.cmd_setup(["--yes"])
        self.assertEqual(seen.get("vad_model"), "ten")


if __name__ == "__main__":
    unittest.main()
