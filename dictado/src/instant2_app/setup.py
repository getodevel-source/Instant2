"""TUI setup simple (stdlib): modelos juntos, mic con medidor, tecla,
arranque con el sistema, test final.

Sobrevive sin modelos (avisa y sigue) y sin mic (no crashea).
Modo no interactivo: `instant2 setup --yes` (no pregunta nada; usa config
actual salvo flags --mic/--key/--threads/--sound/--no-sound/--llm-url/
--context-profile/--context-term/--context-remove-term/--context-delete-profile/
--autostart/--no-autostart).
"""
import logging

from instant2_app import audio, config, context, hotkey
from instant2_app.paths import resolve_data_dir, user_data_dir

log = logging.getLogger("instant2")


def _ask(question, default=None):
    suffix = f" [{default}]" if default is not None else ""
    try:
        v = input(f"  {question}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise KeyboardInterrupt
    return v if v else (default if default is not None else "")


def _ask_int(question, default, lo, hi):
    while True:
        v = _ask(question, str(default))
        try:
            n = int(v)
            if lo <= n <= hi:
                return n
        except ValueError:
            pass
        print(f"  numero entre {lo} y {hi}.")


def _parse_args(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="instant2 setup")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="no interactivo: usa defaults/config actual sin preguntar")
    ap.add_argument("--mic", type=int, default=None, help="indice sounddevice del mic")
    ap.add_argument("--key", default=None, help="tecla hold-to-talk")
    ap.add_argument("--threads", type=int, default=None, help="hilos CPU (tope 8)")
    ap.add_argument("--sound", action="store_true", default=None, help="activa pitidos")
    ap.add_argument("--no-sound", action="store_true", help="apaga pitidos")
    ap.add_argument("--autostart", action="store_true", default=None,
                    help="activa arranque con el sistema")
    ap.add_argument("--no-autostart", action="store_true",
                    help="desactiva arranque con el sistema")
    ap.add_argument("--llm-url", default=None, help="llama-server local (vacio=off)")
    ap.add_argument("--llm-token", default=None,
                    help="token Bearer del servidor (se guarda solo en este equipo)")
    ap.add_argument("--update-mode", default=None,
                    choices=("notify", "auto", "off"),
                    help="actualizaciones: avisar (default), auto u off")
    ap.add_argument("--context-profile", default=None,
                    help="activa o crea un perfil de vocabulario local")
    ap.add_argument("--context-term", action="append", default=[],
                    help="añade grafía preferida=variante1|variante2 al perfil")
    ap.add_argument("--context-remove-term", action="append", default=[],
                    help="elimina del perfil el término con esa grafía preferida")
    ap.add_argument("--context-delete-profile", action="store_true",
                    help="elimina el perfil indicado por --context-profile")
    ap.add_argument("--no-meter", action="store_true", help="salta medidor de nivel")
    ap.add_argument("--vad-model", default=None, choices=("silero", "ten"),
                    help="VAD: silero (default) o ten (mas preciso, ~126 KB extra)")
    ap.add_argument("--blank-penalty", type=float, default=None,
                    help="penalidad al blank 0..1 (default 0; solo con WER medido)")
    ap.add_argument("--no-probe", action="store_true", help="salta probe final y warmup")
    ap.add_argument("--check-deps", action="store_true",
                    help="muestra tabla de dependencias y sigue")
    ap.add_argument("--fix-deps", action="store_true",
                    help="autoinstala lo permitido por el SO y re-chequea")
    known, unknown = ap.parse_known_args(argv)
    for flag in unknown:
        print(f"  ERROR: flag desconocido {flag}; revisá `instant2 setup --help`.")
        raise SystemExit(2)
    return known


def _setup_cause(exc):
    """Causa resumida en consola: qué falló y qué hacer (detalle en el log)."""
    lowered = f"{type(exc).__name__} {exc}".casefold()
    if any(mark in lowered for mark in (
            "urlerror", "timeout", "connection", "network", "unreachable",
            "refused", "reset", "dns", "ssl", "http", "404", "403", "500",
            "sin red", "offline")):
        return (f"sin red ({exc}); conectate y re-corre `instant2 setup`.")
    if any(mark in lowered for mark in (
            "no space", "no hay espacio", "disco lleno", "enospc",
            "disk full", "espacio")):
        return (f"sin espacio ({exc}); liberá ~1 GB y re-corre `instant2 setup`.")
    if "sha256" in lowered or "hash" in lowered or "verific" in lowered:
        return (f"el archivo bajado no verifica ({exc}); "
                "re-corre `instant2 setup` (reintenta solo).")
    return f"{exc}; re-corre `instant2 setup`."


def _real_inputs():
    """Lista entradas útiles sin aliases de backend repetidos."""
    try:
        return audio.input_choices()
    except Exception:
        log.exception("sin backend de audio (instala PortAudio?)")
        return []


def cmd_setup(argv=None):
    o = _parse_args(argv)
    print("== instant2 setup ==")
    cfg = config.load()
    rc = 0
    from instant2_app import deps
    if o.fix_deps:
        try:
            results = deps.ensure(auto=True)
        except Exception:
            log.exception("autoinstalacion fallida; sigo con el setup")
            results = deps.check()
    else:
        results = deps.check()
    print(deps.report(results))
    if not results.get("sherpa_onnx", {}).get("ok", True) \
            or not results.get("sounddevice", {}).get("ok", True):
        print("  AVISO: falta dependencia critica (sherpa_onnx/sounddevice); "
              "la config se guarda igual.")
        rc = max(rc, 2)

    # 1. Modelos juntos: Parakeet (~670MB) + VAD (~1MB) en un solo paso.
    data_dir = resolve_data_dir()
    if data_dir == user_data_dir():
        print(f"  modelos en: {data_dir}")
    else:
        print(f"  modelos en: {data_dir} (dev/INSTANT2_DATA)")
    from instant2_app import models as dl
    status = dl.check(data_dir, verify=True)
    models_ok = all(status.values())
    if models_ok:
        print("  modelos OK (Parakeet + VAD).")
    else:
        missing = sorted(k for k, ok in status.items() if not ok)
        print(f"  faltan: {', '.join(missing)}.")
        print("  descargando modelos juntos: Parakeet (~670MB) + VAD (~1MB)...")
        try:
            dl.download_models(data_dir)
            models_ok = all(dl.check(data_dir, verify=True).values())
            print("  modelos OK (Parakeet + VAD)." if models_ok
                  else "  descarga incompleta, reintenta luego.")
        except Exception as exc:
            print(f"  ERROR modelos: {_setup_cause(exc)}")
            log.exception("SIN MODELOS: sin red o sin espacio; el resto se configura igual. "
                          "Re-corre `instant2 setup` con red para descargar.")
            models_ok = False
            rc = 2
    if not models_ok:
        print("  Configuración incompleta: modelos sin descargar -> "
              "corre `instant2 setup` con red y ~1 GB libre.")

    # 2. Microfono: lista con backend, medidor y seleccion persistente por nombre.
    inputs = _real_inputs()
    if not inputs:
        print("  sin dispositivos de entrada. Conecta un mic y re-corre `instant2 setup`.")
        rc = max(rc, 2)
    elif o.mic is not None:
        idx = audio.preferred_input_index(o.mic, inputs)
        name = next((n for i, n, _c, _r in inputs if i == idx), None)
        if name is None:
            print(f"  --mic {o.mic} no es entrada valida; queda la config actual.")
            rc = max(rc, 2)
        else:
            cfg["mic_index"] = idx
            cfg["mic_hint"] = name
            print(f"  elegido (--mic): {name} — {audio.input_hostapi(idx)}")
    elif o.yes:
        current = audio.resolve_mic(cfg.get("mic_hint", ""), cfg.get("mic_index"))
        current = audio.preferred_input_index(current, inputs)
        name = next((n for i, n, _c, _r in inputs if i == current), None)
        if current is None:
            print("  mic (config actual): predeterminado del sistema.")
        else:
            print(f"  mic (config actual): {name or current}")
    else:
        print("  microfonos disponibles:")
        try:
            import sounddevice as sd
            raw_default = tuple(sd.default.device)[0]
        except Exception:
            raw_default = None
        default = audio.preferred_input_index(raw_default, inputs)
        current = audio.resolve_mic(cfg.get("mic_hint", ""), cfg.get("mic_index"))
        if current is None:
            current = raw_default
        current = audio.preferred_input_index(current, inputs)
        for number, (index, name, channels, _rate) in enumerate(inputs, start=1):
            mark = " (predeterminado)" if index == default else ""
            try:
                backend = audio.input_hostapi(index)
            except Exception:
                backend = "backend desconocido"
            print(f"    {number}. {name} — {backend} ({channels}ch, dispositivo {index}){mark}")
        selected_number = next(
            (number for number, (index, _name, _ch, _rate) in enumerate(inputs, start=1)
             if index == current),
            1,
        )
        selected = _ask_int("microfono (numero de la lista)", selected_number, 1, len(inputs)) - 1
        idx, name, _ch, _rate = inputs[selected]
        cfg["mic_index"] = idx
        cfg["mic_hint"] = name
        print(f"  elegido: {name} ({audio.input_hostapi(idx)})")
        if not o.no_meter:
            if _ask("probar nivel (habla 3s)? s/n", "s").lower().startswith("s"):
                audio.peak_meter(idx)

    # 3. Idioma fijo: espanol unico (se guarda para futuro; el engine no cambia).
    cfg["lang"] = "es"
    print("  idioma: Español (único)")

    # 4. Tecla con captura: presiona la tecla para asignar.
    keys = hotkey.available_keys()
    if o.key is not None:
        if hotkey.is_valid_key(o.key):
            cfg["key"] = hotkey.normalize_key(o.key)
        else:
            print(f"  --key invalida. Opciones: {', '.join(keys)}; queda {cfg.get('key')}.")
            rc = max(rc, 2)
    elif o.yes:
        print(f"  tecla (config actual): {hotkey.key_label(cfg.get('key', 'f9'))}")
    else:
        print(f"  teclas disponibles: {', '.join(keys)} o una tecla individual")
        cfg["key"] = hotkey.capture_key(
            "Presiona la tecla para dictar... (Enter = mantener la actual)",
            cfg.get("key", "f9"))
        print(f"  tecla: {hotkey.key_label(cfg['key'])}")

    # 4b. Arranque con el sistema (casilla): muestra estado real del SO,
    # pregunta s/n (default = estado actual) y aplica. Flags no interactivos
    # --autostart/--no-autostart; --yes solo conserva salvo flag. La casilla
    # se recuerda en cfg["autostart"]; el estado real lo manda el SO.
    from instant2_app import autostart as _as
    try:
        real = bool(_as.is_enabled())
    except Exception:
        log.exception("autostart is_enabled fallo")
        real = False
    try:
        line = _as.describe()
    except Exception:
        line = "Arranque no disponible en este sistema"
    pref_before = cfg.get("autostart")
    want = None
    if o.autostart and o.no_autostart:
        print("  --autostart y --no-autostart juntos; gana --no-autostart.")
        want = False
    elif o.autostart:
        want = True
    elif o.no_autostart:
        want = False
    elif o.yes:
        print(f"  arranque: {'activado' if real else 'desactivado'} ({line}; se conserva)")
    else:
        print(f"  arranque: {'activado' if real else 'desactivado'} ({line})")
        ans = _ask("activar arranque con el sistema? s/n",
                   "s" if real else "n").lower()
        want = ans.startswith(("s", "y"))
    if want is not None:
        try:
            msg = _as.enable() if want else _as.disable()
            print(f"  {msg}")
        except Exception as exc:
            print(f"  ERROR arranque: {_setup_cause(exc)}")
            rc = max(rc, 2)
        # La casilla pedida queda recordada igual para reintentar luego.
        cfg["autostart"] = bool(want)
    else:
        # Sin cambio pedido: si la casilla guardada difiere del SO, aviso sin pelear.
        if pref_before is not None and bool(pref_before) != real:
            print("  AVISO: tu casilla decia %s pero el sistema tiene %s; "
                  "queda lo del sistema." % (
                      "activado" if pref_before else "desactivado",
                      "activado" if real else "desactivado"))
        cfg["autostart"] = real
    import multiprocessing
    try:
        cpu = multiprocessing.cpu_count()
    except Exception:
        cpu = 4
    max_t = max(1, min(cpu or 4, 8))
    if o.threads is not None:
        cfg["threads"] = min(max(1, o.threads), max_t)
    if o.sound:
        cfg["sound"] = True
    elif o.no_sound:
        cfg["sound"] = False
    if o.llm_url is not None:
        cfg["llm_url"] = o.llm_url
    if o.llm_token is not None:
        cfg["llm_token"] = o.llm_token
    if o.update_mode is not None:
        cfg["update_mode"] = o.update_mode
    if o.vad_model is not None:
        cfg["vad_model"] = o.vad_model
        if o.vad_model == "ten":
            from instant2_app import models as _dl2
            from instant2_app.paths import resolve_data_dir as _ddir
            try:
                _dl2.download_models(_ddir(), include_ten_vad=True)
                print("  ten-vad OK.")
            except Exception as exc:
                print(f"  ERROR ten-vad: {_setup_cause(exc)} (sigo con silero).")
                log.exception("no pude bajar ten-vad; sigo con silero.")
                cfg["vad_model"] = "silero"
                rc = max(rc, 2)
    if o.blank_penalty is not None:
        cfg["blank_penalty"] = max(0.0, min(1.0, o.blank_penalty))

    context_requested = (
        o.context_profile is not None or bool(o.context_term)
        or bool(o.context_remove_term) or o.context_delete_profile)
    if context_requested:
        profiles = context.profiles(cfg)
        name = (o.context_profile or cfg.get(
            "active_context", context.DEFAULT_PROFILE)).strip()
        if not name:
            print("  --context-profile no puede estar vacio.")
            rc = max(rc, 2)
        elif o.context_delete_profile:
            if o.context_profile is None:
                print("  --context-delete-profile requiere --context-profile.")
                rc = max(rc, 2)
            elif name == context.DEFAULT_PROFILE:
                print("  no se puede eliminar el perfil General.")
                rc = max(rc, 2)
            elif o.context_term or o.context_remove_term:
                print("  no combines eliminar perfil con cambios de términos.")
                rc = max(rc, 2)
            else:
                profiles.pop(name, None)
                profiles.setdefault(context.DEFAULT_PROFILE, [])
                cfg["context_profiles"] = profiles
                cfg["active_context"] = context.DEFAULT_PROFILE
        else:
            entries = profiles.setdefault(name, [])
            for value in o.context_term:
                term, separator, variants = value.partition("=")
                term = term.strip()
                # `~grafia` activa el emparejamiento por sonido del termino.
                sonido = term.startswith(context.SOUND_PREFIX)
                if sonido:
                    term = term[len(context.SOUND_PREFIX):].strip()
                aliases = [item.strip() for item in variants.split("|") if item.strip()]
                if not term or (separator and not aliases):
                    print(f"  --context-term invalido: {value!r}; usa grafia=variante1|variante2.")
                    rc = max(rc, 2)
                    continue
                existing = next((item for item in entries
                                 if item["term"].casefold() == term.casefold()), None)
                if existing is None:
                    entries.append({"term": term, "aliases": aliases,
                                    "sonido": sonido})
                else:
                    existing["term"] = term
                    existing["aliases"] = list(dict.fromkeys(existing["aliases"] + aliases))
                    existing["sonido"] = existing.get("sonido", False) or sonido
            remove = {term.strip().lstrip(context.SOUND_PREFIX).strip().casefold()
                      for term in o.context_remove_term}
            if remove:
                entries[:] = [item for item in entries
                              if item["term"].casefold() not in remove]
            cfg["context_profiles"] = profiles
            cfg["active_context"] = name

    path = config.save(cfg)

    # 6. Test final: probe mic + warmup modelos.
    print(f"  config en: {path}")
    if o.no_probe:
        if rc == 0 and models_ok and inputs:
            print(f"Listo. Mantén {cfg.get('key', 'f9').upper()} y dicta.")
            return rc
        missing = ("modelos sin descargar" if not models_ok else
                   "mic sin detectar" if not inputs else "revisá avisos")
        print(f"  Configuración incompleta: {missing} -> "
              "corre `instant2 setup` para completarla.")
        return 2
    mic_ok = True
    if inputs:
        from instant2_app.audio import probe, resolve_mic
        try:
            mic = resolve_mic(cfg.get("mic_hint", ""), cfg.get("mic_index"))
            mic_ok = probe(mic)
        except Exception as exc:
            print(f"  ERROR mic: {_setup_cause(exc)}")
            log.exception("mic probe FAIL")
            mic_ok = False
        print(f"  mic probe: {'OK' if mic_ok else 'FAIL (revisa uso exclusivo)'}")
    else:
        print("  mic probe: SKIP (sin mics)")
        mic_ok = False
    if models_ok:
        try:
            from instant2_app.engine import Engine
            eng = Engine(data_dir, threads=cfg.get("threads", 4),
                         max_seg=cfg.get("max_seg", 20.0),
                         vad_model=cfg.get("vad_model", "silero"))
            eng.recognizer()
            eng.vad()
            print("  warmup modelos: OK")
        except Exception as exc:
            print(f"  ERROR warmup: {_setup_cause(exc)}")
            log.exception("warmup modelos FAIL")
            models_ok = False
    else:
        print("  warmup modelos: SKIP (sin modelos)")
    if not (mic_ok and models_ok):
        missing = ", ".join(
            part for part, ok in (("modelos sin descargar", models_ok),
                                  ("mic sin detectar", mic_ok and bool(inputs)))
            if not ok) or "revisá avisos"
        print(f"  Configuración incompleta: {missing} -> "
              "corre `instant2 setup` para completarla.")
        return 2
    print(f"Listo. Mantén {cfg.get('key', 'f9').upper()} y dicta.")
    return rc
