"""CLI y entrada gráfica de Instant2."""
import argparse
import logging
import os
import sys

from instant2_app.identity import APP_COMMAND


def _parse_vad_mode(value):
    from instant2_app.config import parse_vad_mode
    return parse_vad_mode(value)


def _log_setup():
    from logging.handlers import RotatingFileHandler

    from instant2_app.paths import config_dir, ensure_private_dir, restrict_file

    directory = ensure_private_dir(config_dir())
    log_path = os.path.join(directory, "instant2.log")
    # Best-effort (no rompe Windows): el log puede rozar datos sensibles.
    restrict_file(log_path)
    for rotated in (log_path + ".1", log_path + ".2", log_path + ".3"):
        restrict_file(rotated)
    handlers = [RotatingFileHandler(
        log_path, maxBytes=2 << 20,
        backupCount=3, encoding="utf-8")]
    has_console = sys.stdout is not None and sys.stderr is not None
    if has_console:
        handlers.append(logging.StreamHandler())
    else:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def _run_gui(*args, **kwargs):
    from importlib import import_module

    return import_module("instant2_app.gui").run_gui(*args, **kwargs)


def main(argv=None):
    from instant2_app import __version__

    ap = argparse.ArgumentParser(prog=APP_COMMAND, description="Dictado local en español.")
    ap.add_argument("--version", action="version", version=f"{APP_COMMAND} {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    p_setup = sub.add_parser("setup", help="configuración de Instant2")
    p_setup.add_argument("--tui", action="store_true",
                         help="asistente de terminal, sin ventana")
    p_setup.add_argument("-y", "--yes", action="store_true",
                         help="no interactivo: usa defaults/config actual")
    p_setup.add_argument("--mic", type=int, default=None)
    p_setup.add_argument("--key", default=None)
    p_setup.add_argument("--threads", type=int, default=None)
    p_setup.add_argument("--sound", action="store_true", default=None)
    p_setup.add_argument("--no-sound", action="store_true")
    p_setup.add_argument("--llm-url", default=None)
    p_setup.add_argument("--llm-token", default=None,
                         help="token Bearer del servidor LLM (solo este equipo)")
    p_setup.add_argument("--update-mode", default=None,
                         choices=("notify", "auto", "off"),
                         help="actualizaciones: avisar, auto u off")
    p_setup.add_argument("--context-profile", default=None)
    p_setup.add_argument("--context-term", action="append", default=[])
    p_setup.add_argument("--context-remove-term", action="append", default=[])
    p_setup.add_argument("--context-delete-profile", action="store_true")
    p_setup.add_argument("--autostart", action="store_true", default=None,
                         help="activa arranque con el sistema")
    p_setup.add_argument("--no-autostart", action="store_true",
                         help="desactiva el arranque con el sistema")
    p_setup.add_argument("--no-meter", action="store_true")
    p_setup.add_argument("--vad-model", default=None, type=_parse_vad_mode,
                         metavar="MODO",
                         help="detección liviana o más precisa (componente adicional)")
    p_setup.add_argument("--blank-penalty", type=float, default=None,
                         help="penalidad al silencio 0..1 (default 0)")
    p_setup.add_argument("--no-probe", action="store_true")
    p_setup.add_argument("--check-deps", action="store_true",
                         help="muestra tabla de dependencias y sigue")
    p_setup.add_argument("--fix-deps", action="store_true",
                         help="autoinstala lo permitido por el SO y re-chequea")
    sub.add_parser("run", help="daemon hold-to-talk")
    sub.add_parser("stop", help="frena el daemon en ejecución (deja todo limpio)")
    sub.add_parser("check", help="diagnóstico de teclado, micrófono y voz")
    sub.add_parser("diagnostics", help="abre el diagnóstico visual")
    p_update = sub.add_parser("update", help="busca una versión nueva en GitHub")
    p_update.add_argument("--download", metavar="DIR", default=None,
                          help="además descarga y verifica el asset en DIR")
    p_update.add_argument("--apply", action="store_true",
                          help="tras descargar, aplica el binario (Linux/macOS)")
    p_update.add_argument("--allow-source-apply", action="store_true",
                          help="permite --apply aunque no sea binario congelado")
    args, rest = ap.parse_known_args(argv)
    _log_setup()
    if args.cmd is None:
        if rest:
            ap.error("argumentos no reconocidos: " + " ".join(rest))
        return _run_gui("home", start_daemon_on_open=True)

    if args.cmd == "diagnostics":
        rc = _run_gui("diagnostics")
        if rc != 2:
            return rc
        # Sin ventana (SSH, servidor) el diagnóstico sale en consola.
        from instant2_app import config as config_module
        from instant2_app.daemon import cmd_check
        return cmd_check(config_module.load())

    if args.cmd == "stop":
        # Lo usa también el desinstalador de Windows ([UninstallRun]): deja la
        # carpeta libre (daemon + panel) antes de borrar archivos.
        from instant2_app.gui import stop_daemon
        from instant2_app.gui_lifecycle import terminate_existing_gui
        stopped = stop_daemon()
        panel = terminate_existing_gui()
        if stopped or panel:
            print("Instant2 detenido.")
        elif stopped is None:
            # El probe del SO no respondió: no se afirma que no haya nada
            # corriendo.
            print("No se pudo comprobar si el servicio de dictado sigue "
                  "vivo; cerrá Instant2 a mano si sigue abierto.")
        else:
            print("No había nada corriendo.")
        return 0

    if args.cmd == "setup":
        # Sin flags abre la ventana (los tres sistemas); cualquier flag o
        # `--tui` mantiene el asistente de terminal, para scripts y consolas.
        cli_options = (
            args.tui, args.yes, args.mic is not None, args.key is not None,
            args.threads is not None, args.sound is not None, args.no_sound,
            args.llm_url is not None, args.llm_token is not None,
            args.update_mode is not None, args.context_profile is not None,
            bool(args.context_term), bool(args.context_remove_term),
            args.context_delete_profile, args.no_meter, args.no_probe,
            args.vad_model is not None, args.blank_penalty is not None,
            args.check_deps, args.fix_deps, bool(rest))
        if not any(cli_options):
            override = False if args.no_autostart else True if args.autostart else None
            rc = _run_gui("setup", autostart_override=override)
            if rc != 2:
                return rc
            print("Sin entorno gráfico; abro el asistente de terminal (TUI)...")
        from instant2_app.setup import cmd_setup
        forward = ["--yes"] if args.yes else []
        for key in ("mic", "key", "threads", "llm_url", "llm_token",
                    "update_mode", "context_profile",
                    "vad_model", "blank_penalty"):
            value = getattr(args, key)
            if value is not None:
                forward.extend((f"--{key.replace('_', '-')}", str(value)))
        for value in args.context_term:
            forward.extend(("--context-term", value))
        for value in args.context_remove_term:
            forward.extend(("--context-remove-term", value))
        if args.context_delete_profile:
            forward.append("--context-delete-profile")
        for flag in ("sound", "no_sound", "no_meter", "no_probe", "check_deps",
                     "fix_deps", "autostart", "no_autostart"):
            if getattr(args, flag):
                forward.append(f"--{flag.replace('_', '-')}")
        forward.extend(rest)
        try:
            return cmd_setup(forward)
        except KeyboardInterrupt:
            print("\nsetup cancelado.")
            return 130

    from instant2_app import config
    cfg = config.load()
    if args.cmd == "check":
        from instant2_app.daemon import cmd_check
        return cmd_check(cfg)
    if args.cmd == "update":
        from instant2_app import update as update_module
        return update_module.cmd_update(
            getattr(args, "download", None),
            apply=bool(getattr(args, "apply", False)),
            allow_source_apply=bool(getattr(args, "allow_source_apply", False)))
    from instant2_app.daemon_lifecycle import (
        acquire_daemon_mutex, release_daemon_mutex)
    # El daemon es parte del arranque: le toca la migración de los modelos del
    # producto anterior (una sola vez por máquina).
    from instant2_app.paths import migrate_legacy_models_once
    migrate_legacy_models_once()
    mutex = acquire_daemon_mutex()
    if mutex is None:
        logging.getLogger("instant2").info(
            "daemon ya activo; se omite el segundo inicio.")
        return 0
    try:
        from instant2_app.daemon import Daemon
        Daemon(cfg).run()
    except FileNotFoundError as exc:
        logging.getLogger("instant2").error(
            "%s -> corre `instant2 setup` primero.", exc)
        return 2
    finally:
        release_daemon_mutex(mutex)
    return 0


if __name__ == "__main__":
    sys.exit(main())
