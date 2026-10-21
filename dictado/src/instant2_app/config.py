"""Config JSON por usuario + override por env INSTANT2_*."""
import json
import logging
import os
import tempfile

from instant2_app.paths import config_dir

log = logging.getLogger("instant2")

DEFAULTS = {
    "mic_hint": "",
    "mic_index": None,
    "key": "f9",
    "read_key": "f10",
    "lang": "es",
    "threads": 4,
    "sound": False,
    "max_seg": 20.0,
    "vad_model": "silero",
    "blank_penalty": 0.0,
    "llm_url": "",
    "llm_token": "",
    "llm_model": "",
    # Local model lifecycle is managed by Instant2 by default once configured.
    "llm_managed_enabled": True,
    "llm_server_path": "",
    "llm_model_path": "",
    # El nombre de la clave es histórico: venía del bundle offline del
    # instalador. Ahora significa "el LLM local quedó configurado solo". No
    # se renombra para que las configs de los usuarios sigan sirviendo.
    "llm_bundle_initialized": False,
    "llm_acceleration": "auto",
    "llm_normal_enabled": True,
    "resource_mode": "balanced",
    "active_context": "General",
    "context_profiles": {"General": []},
    "autostart": False,
    "overlay_style": "orbital",
    "update_mode": "notify",
    "update_last_check": 0,
}

INT_KEYS = ("threads",)
FLOAT_KEYS = ("max_seg", "blank_penalty")


def parse_vad_mode(value):
    """Accept public descriptions while preserving older internal flag values."""
    normalized = " ".join(str(value).strip().casefold().replace("_", " ").split())
    aliases = {
        "liviano": "silero", "liviana": "silero", "silero": "silero",
        "más preciso": "ten", "mas preciso": "ten",
        "más precisa": "ten", "mas precisa": "ten",
        "ten": "ten",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError("elegí «liviana» o «más precisa».") from exc


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError("la raíz de la config no es un objeto JSON")
    return value


def _write_temp_json(path, value):
    directory = os.path.dirname(path) or "."
    fd, temp_path = tempfile.mkstemp(prefix=".config-", suffix=".tmp",
                                     dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        return temp_path
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _replace_json_atomically(path, value):
    temp_path = _write_temp_json(path, value)
    try:
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def config_path():
    return os.path.join(config_dir(), "config.json")


def load():
    cfg = dict(DEFAULTS)
    path = config_path()
    try:
        disk = _read_json(path)
    except FileNotFoundError:
        disk = {}
    except Exception:
        log.exception("config corrupta; intento recuperar el respaldo")
        try:
            disk = _read_json(path + ".bak")
        except FileNotFoundError:
            disk = {}
        except Exception:
            log.exception("respaldo de config ausente o corrupto; uso defaults")
            disk = {}
        else:
            try:
                _replace_json_atomically(path, disk)
                log.warning("config recuperada desde el respaldo")
            except OSError:
                log.exception("leí el respaldo, pero no pude reparar la config")
    for k in DEFAULTS:
        if k in disk:
            v = disk[k]
            if k in INT_KEYS and isinstance(v, bool):
                continue
            if k in INT_KEYS and not isinstance(v, int):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    log.warning("ignoro %s=%r de la config (no es int)", k, disk[k])
                    continue
            if k in FLOAT_KEYS and isinstance(v, bool):
                continue
            if k in FLOAT_KEYS and not isinstance(v, (int, float)):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    log.warning("ignoro %s=%r de la config (no es float)", k, disk[k])
                    continue
                v = float(v)
            cfg[k] = v
    # Env pisa archivo.
    env_map = {"INSTANT2_MIC": "mic_hint", "INSTANT2_KEY": "key",
               "INSTANT2_READ_KEY": "read_key",
               "INSTANT2_THREADS": "threads", "INSTANT2_SOUND": "sound",
               "INSTANT2_MAX_SEG": "max_seg", "INSTANT2_VAD": "vad_model",
               "INSTANT2_BLANK": "blank_penalty",
               "INSTANT2_LLM_URL": "llm_url",
               "INSTANT2_LLM_TOKEN": "llm_token",
               "INSTANT2_LLM_MODEL": "llm_model",
               "INSTANT2_LLM_SERVER": "llm_server_path",
               "INSTANT2_LLM_MODEL_PATH": "llm_model_path",
               "INSTANT2_LLM_NORMAL": "llm_normal_enabled",
               "INSTANT2_UPDATE": "update_mode",
               "INSTANT2_CONTEXT": "active_context",
               "INSTANT2_AUTOSTART": "autostart",
               "INSTANT2_OVERLAY": "overlay_style"}
    for env, k in env_map.items():
        v = os.environ.get(env)
        if v is None or v == "":
            continue
        if k in INT_KEYS:
            try:
                value = int(v)
            except ValueError:
                log.warning("ignoro %s=%r (no es int)", env, v)
                continue
            if k == "threads":
                try:
                    cpu = os.cpu_count() or 4
                except Exception:
                    cpu = 4
                value = max(1, min(8, min(value, cpu)))
            cfg[k] = value
        elif k in FLOAT_KEYS:
            try:
                cfg[k] = float(v)
            except ValueError:
                log.warning("ignoro %s=%r (no es float)", env, v)
        elif k == "sound":
            cfg[k] = v.strip().lower() in ("1", "true", "yes", "y", "on", "si", "s")
        elif k == "llm_normal_enabled":
            cfg[k] = v.strip().lower() in ("1", "true", "yes", "y", "on", "si", "s")
        elif k == "autostart":
            cfg[k] = v.strip().lower() in ("1", "true", "yes", "y", "s", "si", "on")
        elif k == "mic_hint" and v.lstrip("-").isdigit():
            cfg["mic_index"] = int(v)
            cfg["mic_hint"] = ""
        elif k == "vad_model":
            v = v.strip().lower()
            cfg[k] = v if v in ("silero", "ten") else "silero"
        else:
            cfg[k] = v.lower() if k in ("key", "read_key") else v
    if cfg.get("update_mode") not in ("notify", "auto", "off"):
        cfg["update_mode"] = "notify"
    tok = cfg.get("llm_token", "")
    cfg["llm_token"] = tok if isinstance(tok, str) else ""
    model = cfg.get("llm_model", "")
    cfg["llm_model"] = model.strip()[:200] if isinstance(model, str) else ""
    for key in ("llm_server_path", "llm_model_path"):
        value = cfg.get(key, "")
        cfg[key] = value.strip()[:2048] if isinstance(value, str) else ""
    cfg["llm_managed_enabled"] = cfg.get("llm_managed_enabled") is True
    cfg["llm_bundle_initialized"] = cfg.get("llm_bundle_initialized") is True
    if cfg.get("llm_acceleration") not in ("auto", "cpu"):
        cfg["llm_acceleration"] = "auto"
    cfg["llm_normal_enabled"] = cfg.get("llm_normal_enabled") is not False
    if cfg.get("resource_mode") not in ("speed", "balanced", "memory"):
        cfg["resource_mode"] = "balanced"
    _normalize_hotkeys(cfg)
    if (os.environ.get("INSTANT2_NO_UPDATE") or "").strip().lower() in (
            "1", "true", "yes", "y", "on", "si", "s"):
        cfg["update_mode"] = "off"
    try:
        cfg["blank_penalty"] = max(0.0, min(1.0, float(cfg.get("blank_penalty", 0.0))))
    except (TypeError, ValueError):
        cfg["blank_penalty"] = 0.0
    if _initialize_local_llm(cfg):
        save(cfg)
    cfg["llm_bundle_initialized"] = cfg.get("llm_bundle_initialized") is True
    return cfg


def save(cfg):
    from instant2_app.paths import ensure_private_dir, restrict_file

    _normalize_hotkeys(cfg)
    path = config_path()
    directory = os.path.dirname(path) or "."
    ensure_private_dir(directory)
    temp_path = None
    slim = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    try:
        temp_path = _write_temp_json(path, slim)

        # Nota: config.json guarda `llm_url` en claro (URL sin token: el token
        # va en `INSTANT2_LLM_TOKEN`/env o config `llm_token`, nunca en la URL).
        # Preserve the last valid config only after the replacement content
        # has been serialized successfully. El .bak se escribe atomico con
        if os.path.isfile(path):
            try:
                bak_tmp = _write_temp_json(path + ".bak", _read_json(path))
                os.replace(bak_tmp, path + ".bak")
                try:
                    fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
                except OSError:
                    pass
                else:
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            except Exception:
                log.warning("no pude respaldar la config anterior", exc_info=True)
        os.replace(temp_path, path)
        temp_path = None
        # Best-effort (no rompe Windows): solo el usuario lee la config.
        restrict_file(path + ".bak")
        restrict_file(path)
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
    log.info("config guardada en %s", path)
    return path


def update(values):
    """Guarda solo las claves indicadas y deja el resto de la config intacta.

    Es lo que usan los cambios puntuales (por ejemplo, la ruta del modelo que
    se acaba de descargar): no se persiste la config entera de la interfaz,
    con ediciones sin confirmar ni cambios de arranque con el sistema.
    """
    path = config_path()
    try:
        current = _read_json(path)
    except FileNotFoundError:
        current = {}
    except Exception:
        log.exception("config ilegible; guardo solo los cambios indicados")
        current = {}
    if not isinstance(current, dict):
        current = {}
    changed = {}
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        if current.get(key) != value:
            changed[key] = value
    if changed:
        current.update(changed)
        save(current)
    return changed


def _normalize_hotkeys(cfg):
    """Keep the dictation and reading shortcuts valid and physically distinct."""
    from instant2_app import hotkey

    dictation = str(cfg.get("key", "f9") or "f9").strip().lower()
    if not hotkey.is_valid_key(dictation):
        dictation = "f9"
    reading = str(cfg.get("read_key", "f10") or "f10").strip().lower()
    if not hotkey.is_valid_key(reading):
        reading = "f10"
    if hotkey.same_binding(dictation, reading):
        candidates = ("f10", "f11", "f12", "f9", "f8", "f7", "f6",
                      "f5", "f4", "f3", "f2", "f1")
        reading = next((candidate for candidate in candidates
                        if hotkey.is_valid_key(candidate)
                        and not hotkey.same_binding(dictation, candidate)),
                       "f10")
    cfg["key"] = dictation
    cfg["read_key"] = reading


def _initialize_local_llm(cfg):
    """Configure the default local LLM once, when runtime and model are here.

    User-selected endpoints and file paths always win. The marker lets a user
    deliberately clear or replace the local defaults without having them
    silently reappear at the next launch.

    The model is not shipped with the installer anymore: it is downloaded on
    demand from Settings, so this only takes effect once the download is
    done. Until then nothing is configured and nothing is marked: the next
    launch tries again.
    """
    if cfg.get("llm_bundle_initialized") is True:
        return False

    from instant2_app import llm_defaults

    outcome = llm_defaults.apply_local_defaults(cfg)
    changed = outcome["changed"]
    if outcome["managed"]:
        # La marca se pone cuando el servicio local quedó completo (runtime,
        # modelo, dirección y alias): así el usuario puede vaciar la
        # dirección y que no le vuelva sola en el próximo arranque.
        if not cfg.get("llm_bundle_initialized"):
            cfg["llm_bundle_initialized"] = True
            changed = True
    return changed
