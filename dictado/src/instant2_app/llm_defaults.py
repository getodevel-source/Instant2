"""Valores por defecto del servicio de lenguaje local (LLM administrado).

El runtime (`llama-server`) viene con la instalación; el modelo de 3 GB ya no
viene con nada y se descarga a pedido desde Ajustes. Este módulo es el único
lugar que decide cómo se arma la configuración del servicio local, para que el
panel (justo después de la descarga) y el arranque (al día siguiente) lleguen
al mismo estado: runtime, dirección, alias y ruta del modelo.

Reglas:

- el servicio remoto que el usuario configuró manda: si `llm_url` no es local
  no se toca nada;
- un GGUF que el usuario eligió a mano no se reemplaza nunca;
- el runtime y la dirección solo se completan cuando hay runtime Y modelo de
  verdad: si falta algo, no se promete un servicio que no puede arrancar.
"""
import os
import sys

DEFAULT_LLM_URL = "http://127.0.0.1:8080/v1"
DEFAULT_LLM_MODEL = "qwen3.5:4b-q4_K_M"

# El runtime se instaló en dos layouts: el actual (`llm-runtime\llama-server.exe`)
# y el de las versiones previas (`llm-runtime\llama\llama-server.exe`).
SERVER_LAYOUTS = (("llm-runtime", "llama-server.exe"),
                  ("llm-runtime", "llama", "llama-server.exe"))


def app_dir():
    """Directorio de la app (donde vive el runtime en el binario congelado)."""
    return os.path.dirname(os.path.abspath(sys.executable))


def runtime_server_path(base_dir=None):
    """Ruta del `llama-server` que trae la instalación ("" si no está)."""
    base = base_dir or app_dir()
    for parts in SERVER_LAYOUTS:
        candidate = os.path.join(base, *parts)
        if os.path.isfile(candidate):
            return candidate
    return ""


def is_runtime_layout(path):
    """True si la ruta parece el runtime de la instalación."""
    normalized = os.path.normcase(str(path or "").replace("/", "\\"))
    return any(normalized.endswith(os.path.normcase("\\" + "\\".join(parts)))
               for parts in SERVER_LAYOUTS)


def downloaded_model_path(data_dir=None):
    """Ruta del GGUF que se descarga desde Ajustes."""
    from instant2_app import llm_model
    from instant2_app.paths import resolve_data_dir

    return llm_model.model_path(data_dir or resolve_data_dir())


def same_path(left, right):
    """Compara dos rutas sin distinguir mayúsculas ni barras."""
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
        os.path.abspath(str(right)))


def same_model_name(left, right):
    """True si las dos rutas apuntan al mismo archivo GGUF (por su nombre)."""
    if not left or not right:
        return False
    return os.path.basename(str(left)).casefold() == os.path.basename(
        str(right)).casefold()


def _is_local_endpoint(url):
    """True si la dirección es un servicio local (loopback)."""
    from instant2_app.llm_runtime import is_local_endpoint

    return is_local_endpoint(url)


def local_changes(cfg, data_dir=None, server_path=None, model_path=None):
    """Claves de la configuración que hay que cambiar para el LLM local.

    Devuelve un dict vacío cuando no hay nada que tocar: el usuario ya lo
    tiene configurado, eligió un servicio remoto, o todavía no hay modelo.
    """
    url = str(cfg.get("llm_url") or "").strip()
    if url and not _is_local_endpoint(url):
        # Servicio remoto: el usuario lo eligió y manda.
        return {}

    server = server_path if server_path is not None else runtime_server_path()
    model = model_path if model_path is not None else downloaded_model_path(data_dir)
    model_ok = bool(model) and os.path.isfile(model)
    server_ok = bool(server) and os.path.isfile(server)

    changes = {}
    current_model = str(cfg.get("llm_model_path") or "").strip()
    if model_ok and (not current_model or same_model_name(current_model, model)):
        if not same_path(current_model, model):
            changes["llm_model_path"] = model

    if not (model_ok and server_ok):
        # Sin runtime y modelo no hay servicio que arranque: no se promete nada.
        return changes

    current_server = str(cfg.get("llm_server_path") or "").strip()
    if (not current_server
            or (is_runtime_layout(current_server)
                and not os.path.isfile(current_server))):
        changes["llm_server_path"] = server
    if not url:
        changes["llm_url"] = DEFAULT_LLM_URL
    if not str(cfg.get("llm_model") or "").strip():
        changes["llm_model"] = DEFAULT_LLM_MODEL
    return changes


def is_managed(cfg):
    """True si el servicio local queda administrable por Instant2.

    Es la misma condición que aplica el daemon: dirección local, alias,
    runtime presente y modelo presente.
    """
    url = str(cfg.get("llm_url") or "").strip()
    if not url or not _is_local_endpoint(url):
        return False
    if not str(cfg.get("llm_model") or "").strip():
        return False
    server = str(cfg.get("llm_server_path") or "").strip()
    model = str(cfg.get("llm_model_path") or "").strip()
    if not server or not os.path.isfile(server):
        return False
    if not model or not os.path.isfile(model):
        return False
    return True


def apply_local_defaults(cfg, data_dir=None, server_path=None, model_path=None):
    """Aplica los valores por defecto del LLM local sobre `cfg`.

    Devuelve {"changed": bool, "managed": bool}: `managed` dice si el
    servicio local quedó completo (runtime, modelo, dirección y alias), que
    es lo que permite administrarlo.
    """
    changes = local_changes(cfg, data_dir=data_dir, server_path=server_path,
                            model_path=model_path)
    for key, value in changes.items():
        cfg[key] = value
    return {"changed": bool(changes), "managed": is_managed(cfg),
            "changes": changes}
