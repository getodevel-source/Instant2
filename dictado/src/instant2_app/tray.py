"""Icono de bandeja de Instant2 en Windows."""
import logging
import os
import subprocess
import sys
import threading
import time

from instant2_app.branding import create_icon_image
from instant2_app.gui_lifecycle import close_existing_gui, focus_existing_gui
from instant2_app.launch import app_command, app_environment

log = logging.getLogger("instant2")


def _checked_shell_notify_icon(shell_notify_icon, add_code, on_added):
    """Check the result pystray's Win32 backend currently discards."""
    def checked(message, data):
        result = shell_notify_icon(message, data)
        if message == add_code:
            if not result:
                raise OSError("Windows rechazó el registro del icono de bandeja.")
            on_added()
        return result

    return checked


class TrayIcon:
    def __init__(self, shutdown):
        self.shutdown = shutdown
        self.icon = None
        self._tray_ready = threading.Event()
        self._shell_notify_restore = None

    def start(self):
        if sys.platform != "win32":
            return

        import pystray
        from pystray._util import win32 as tray_win32
        from instant2_app import config, hotkey

        menu = self._build_menu(pystray)
        cfg = config.load()
        normal_key = hotkey.key_label(cfg.get("key", "f9"))
        self.icon = pystray.Icon(
            "instant2", create_icon_image(64),
            f"Instant2 · Dictado {normal_key} · Leer selección F10", menu)
        original_notify = tray_win32.Shell_NotifyIcon
        tray_win32.Shell_NotifyIcon = _checked_shell_notify_icon(
            original_notify, tray_win32.NIM_ADD, self._on_icon_added)
        self._shell_notify_restore = lambda: setattr(
            tray_win32, "Shell_NotifyIcon", original_notify)
        try:
            self.icon.run_detached(setup=self._show_icon)
        except Exception:
            self._restore_shell_notify_icon()
            self.icon = None
            raise
        if not self._tray_ready.wait(timeout=5):
            log.error("la bandeja no confirmó el inicio del icono dentro de 5s.")

    def _on_icon_added(self):
        log.info("Windows aceptó el registro del icono de bandeja.")

    def _show_icon(self, icon):
        try:
            for attempt in range(1, 4):
                try:
                    icon.visible = True
                    return
                except Exception:
                    if attempt == 3:
                        log.exception("no se pudo preparar o mostrar el icono de bandeja tras 3 intentos.")
                        return
                    log.warning("falló la preparación o el registro del icono de bandeja (intento %d/3); reintento.",
                                attempt, exc_info=True)
                    time.sleep(0.25 * attempt)
        finally:
            self._tray_ready.set()

    def _restore_shell_notify_icon(self):
        restore, self._shell_notify_restore = self._shell_notify_restore, None
        if restore is not None:
            try:
                restore()
            except Exception:
                log.exception("no pude restaurar la función de la bandeja de Windows.")

    def _build_menu(self, pystray):
        return pystray.Menu(
            pystray.MenuItem("Instant2 · dictado local", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Abrir ventana de Instant2", self._open, default=True),
            pystray.MenuItem("Configurar micrófono y atajos", self._configure),
            pystray.MenuItem("Diagnóstico", self._diagnostics),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir de Instant2", self._on_exit),
        )

    def _launch_gui(self, args=()):
        try:
            page = args[0] if args else None
            if focus_existing_gui(request_daemon_start=not args, page=page):
                return "focused"
            subprocess.Popen(
                app_command(args),
                cwd=os.getcwd(),
                env=app_environment(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "launched"
        except Exception:
            log.exception("no pude abrir la interfaz de Instant2")
            return "failed"

    def _open(self, icon, item):
        return self._launch_gui()

    def _configure(self, icon, item):
        return self._launch_gui(("setup",))

    def _diagnostics(self, icon, item):
        return self._launch_gui(("diagnostics",))

    def _on_exit(self, icon, item):
        self.shutdown.set()
        try:
            close_existing_gui()
        except Exception:
            log.exception("no pude cerrar la ventana de Instant2 desde la bandeja")

    def stop(self):
        if self.icon is None:
            self._restore_shell_notify_icon()
            return
        icon, self.icon = self.icon, None
        try:
            icon.stop()
        except Exception:
            log.exception("no pude retirar el icono de bandeja")
        finally:
            self._restore_shell_notify_icon()
