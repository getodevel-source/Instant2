"""Icono de bandeja de Instant en Windows."""
import logging
import os
import subprocess
import sys

from instant_app.branding import create_icon_image
from instant_app.gui_lifecycle import close_existing_gui, focus_existing_gui
from instant_app.launch import app_command, app_environment

log = logging.getLogger("instant")


class TrayIcon:
    def __init__(self, shutdown):
        self.shutdown = shutdown
        self.icon = None

    def start(self):
        if sys.platform != "win32":
            return

        import pystray
        from instant_app import config, hotkey

        menu = self._build_menu(pystray)
        key = hotkey.key_label(config.load().get("key", "f9"))
        self.icon = pystray.Icon(
            "instant", create_icon_image(64),
            f"Instant — mantené {key} para dictar", menu)
        self.icon.run_detached()
        log.info("icono de bandeja registrado.")

    def _build_menu(self, pystray):
        return pystray.Menu(
            pystray.MenuItem("Instant · dictado local", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Abrir ventana de Instant", self._open, default=True),
            pystray.MenuItem("Configurar micrófono y tecla", self._configure),
            pystray.MenuItem("Diagnóstico", self._diagnostics),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir de Instant", self._on_exit),
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
            log.exception("no pude abrir la interfaz de Instant")
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
            log.exception("no pude cerrar la ventana de Instant desde la bandeja")

    def stop(self):
        if self.icon is None:
            return
        icon, self.icon = self.icon, None
        try:
            icon.stop()
        except Exception:
            log.exception("no pude retirar el icono de bandeja")
