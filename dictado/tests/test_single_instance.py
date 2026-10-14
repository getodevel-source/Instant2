"""Canal de instancia única del panel (QLocalServer/QLocalSocket).

En Linux/macOS una segunda apertura no crea otra ventana: escribe el pedido
por el canal y sale. En Windows ese rol lo cumple el mutex + FindWindow
(test_app_lifecycle.py); acá se cubre el mecanismo portable, que corre en
los tres sistemas.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from importlib import import_module
    import_module("PySide6")
except ImportError:
    print("SKIP canal de instancia única: PySide6 no está instalado")
else:
    from PySide6.QtCore import QEventLoop, QObject, QTimer
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    from instant2_app import gui

    class _WindowStub(QObject):
        """Ventana mínima: solo recibe la acción que llegue por el canal."""

        def __init__(self):
            super().__init__()
            self.actions = []

        def apply_gui_action(self, page=None, start_daemon=False):
            self.actions.append((page, start_daemon))

    class SingleInstanceChannelTests(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            cls.app = QApplication.instance() or QApplication([])

        def setUp(self):
            QLocalServer.removeServer(gui._gui_server_name())
            self.server = None

        def tearDown(self):
            if self.server is not None:
                self.server.close()
                self.server = None
            QLocalServer.removeServer(gui._gui_server_name())

        def _send_raw(self, raw):
            """Escribe bytes crudos y espera el acuse del panel."""
            socket = QLocalSocket()
            socket.connectToServer(gui._gui_server_name())
            self.assertTrue(socket.waitForConnected(500))
            socket.write(raw)
            socket.flush()
            loop = QEventLoop()
            socket.disconnected.connect(loop.quit)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()

        def test_forwarding_reaches_the_listening_window(self):
            window = _WindowStub()
            self.server = gui._install_gui_server(window)
            self.assertIsNotNone(self.server)
            self.assertTrue(gui._forward_to_existing_gui("setup", True))
            # El forward espera el acuse: al volver, la acción ya se aplicó.
            self.assertEqual(window.actions, [("setup", True)])

        def test_web_panel_stub_without_qobject_parent_is_supported(self):
            """El panel web no es QObject: el canal usa su ventana QML como parent."""

            class _NotAQObject:
                def __init__(self):
                    self.root = QObject()
                    self.actions = []

                def apply_gui_action(self, page=None, start_daemon=False):
                    self.actions.append((page, start_daemon))

            window = _NotAQObject()
            self.server = gui._install_gui_server(window)
            self.assertIsNotNone(self.server)
            self.assertTrue(gui._forward_to_existing_gui("home", True))
            self.assertEqual(window.actions, [("home", True)])

        def test_forwarding_without_server_is_false(self):
            self.assertFalse(gui._forward_to_existing_gui("home", False))

        def test_garbage_payload_does_not_break_the_channel(self):
            window = _WindowStub()
            self.server = gui._install_gui_server(window)
            self._send_raw(b"\xff\x00 not json")
            self.assertTrue(gui._forward_to_existing_gui("diagnostics", False))
            self.assertEqual(window.actions, [("diagnostics", False)])

        @unittest.skipIf(sys.platform == "win32",
                         "la rama unix de run_gui solo corre en Linux/macOS")
        def test_run_gui_installs_the_channel(self):
            """La ventana real atiende el canal: integración de punta a punta.

            En un proceso aparte a propósito: el QApplication compartido de la
            clase puede quedar con un `quit()` pendiente de otro test y el
            `exec()` de `run_gui` volvería al instante (Qt sale enseguida si
            `quitNow` está puesto). Un proceso nuevo es, además, lo que pasa en
            producción: la segunda apertura es otro proceso.
            """
            import shutil
            import subprocess
            import tempfile
            import time

            home = tempfile.mkdtemp(prefix="instant2-gui-home-")
            self.addCleanup(shutil.rmtree, home, ignore_errors=True)
            source = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "src"))
            env = dict(
                os.environ,
                QT_QPA_PLATFORM="offscreen",
                # Chromium en un runner sin GPU: sin esto el renderer
                # puede no arrancar y el canal nunca contesta.
                QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu",
                DISPLAY=os.environ.get("DISPLAY", ":0"),
                HOME=home,
                INSTANT2_DATA=os.path.join(home, "models"),
                PYTHONPATH=os.pathsep.join(
                    [source] + ([os.environ["PYTHONPATH"]]
                                if os.environ.get("PYTHONPATH") else [])),
            )
            process = subprocess.Popen(
                [sys.executable, "-c",
                 "import sys; from instant2_app.gui import run_gui; "
                 "sys.exit(run_gui('setup'))"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            def stop_child():
                try:
                    process.kill()
                    process.wait(timeout=10)
                except Exception:
                    pass
                for stream in (process.stdout, process.stderr):
                    try:
                        stream.close()
                    except Exception:
                        pass

            self.addCleanup(stop_child)
            deadline = time.monotonic() + 45
            answered = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    if process.returncode == 2:
                        # run_gui devuelve 2 cuando el entorno no puede abrir la
                        # UI (sin display, WebEngine sin GL): acá no hay canal
                        # que probar, no es un fallo del producto.
                        self.skipTest("el panel no pudo abrir en este entorno")
                    self.fail("el panel murió antes de atender el canal "
                              f"(rc={process.returncode})")
                if gui._forward_to_existing_gui(None, False):
                    answered = True
                    break
                time.sleep(0.25)
            self.assertTrue(answered, "el canal del panel no respondió en 45 s")


if __name__ == "__main__":
    unittest.main()
