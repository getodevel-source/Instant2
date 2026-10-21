"""Rutas: raiz de la app, datos por SO, resolucion del dir de modelos."""
import logging
import os
import shutil
import sys

from instant2_app.identity import APP_SLUG

log = logging.getLogger("instant2")


def _restrict(path, mode):
    """chmod best-effort: en Windows no hay bits POSIX (se ignora), en POSIX
    un fallo de permiso no puede romper el arranque ni el guardado."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def ensure_private_dir(path):
    """Crea `path` con 0o700 best-effort (config/datos solo del usuario).
    Idempotente: si ya existe, solo intenta restringir bits sin romper nada."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    _restrict(path, 0o700)
    return path


def restrict_file(path):
    """chmod 0o600 best-effort para secretos/logs (config.json/.bak, pid,
    instant2.log, wavs). Nunca lanza."""
    _restrict(path, 0o600)
    return path

PARAKEET_SUBDIR = "parakeet-v3-int8"
VAD_SUBDIR = "silero-vad"
# Slug del producto anterior: sus modelos quedaban en un directorio aparte.
LEGACY_APP_SLUG = "instant"


def config_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, APP_SLUG)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_SLUG)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and xdg.strip():
        return os.path.join(xdg.strip(), APP_SLUG)
    return os.path.join(os.path.expanduser("~"), ".config", APP_SLUG)


def user_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, APP_SLUG, "models")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                            APP_SLUG, "models")
    xdg = os.environ.get("XDG_DATA_HOME")
    if not (xdg and xdg.strip()):
        xdg = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(xdg.strip(), APP_SLUG, "models")


def legacy_user_data_dir():
    """Directorio de modelos del producto anterior (slug "instant").

    El instalador de Windows movía el contenido de acá al directorio actual
    antes de instalar los modelos. Como el instalador ya no lleva los pesos,
    esa migración pasó a ser responsabilidad de la app.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, LEGACY_APP_SLUG, "models")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                            LEGACY_APP_SLUG, "models")
    xdg = os.environ.get("XDG_DATA_HOME")
    if not (xdg and xdg.strip()):
        xdg = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(xdg.strip(), LEGACY_APP_SLUG, "models")


def _remove_tree(path):
    """Borra un archivo o un directorio completo; no propaga el error."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        os.remove(path)
    except OSError:
        log.warning("no pude borrar %s", path, exc_info=True)


def migrate_legacy_models(data_dir=None, legacy_dir=None):
    """Reutiliza los modelos que dejó el producto anterior ("instant").

    Reproduce lo que hacía el instalador (`ReuseLegacyModels` en el Inno Setup
    que ya no existe):

    - si el directorio actual no existe, se mueve el anterior entero (un
      rename: no se copia ni un byte) y no queda una copia huérfana;
    - si los dos existen, se mueve lo que falta y se borra la copia
      duplicada, para que el directorio anterior no quede como un duplicado
      viejo.

    Es idempotente y nunca propaga un error: este camino lo usa toda la app
    y una migración fallida no puede impedir que arranque.
    """
    current = os.path.abspath(data_dir or user_data_dir())
    legacy = os.path.abspath(legacy_dir or legacy_user_data_dir())
    result = {"moved": False, "merged": False, "moved_entries": 0,
              "removed_duplicates": 0}
    try:
        if os.path.normcase(legacy) == os.path.normcase(current):
            return result
        if not os.path.isdir(legacy):
            return result
        if not os.path.exists(current):
            ensure_private_dir(os.path.dirname(current))
            os.rename(legacy, current)
            result["moved"] = True
            log.info("modelos: se movió %s a %s (no se copió nada).",
                     legacy, current)
            return result
        for name in sorted(os.listdir(legacy)):
            legacy_item = os.path.join(legacy, name)
            current_item = os.path.join(current, name)
            if os.path.exists(current_item):
                _remove_tree(legacy_item)
                result["removed_duplicates"] += 1
                log.info("modelos: duplicado borrado %s (ya estaba en %s).",
                         legacy_item, current_item)
                continue
            try:
                os.rename(legacy_item, current_item)
            except OSError:
                log.warning("no pude mover %s a %s", legacy_item, current_item,
                            exc_info=True)
                continue
            result["merged"] = True
            result["moved_entries"] += 1
            log.info("modelos: se reutilizó %s movido a %s.",
                     legacy_item, current_item)
        # Si el directorio anterior quedó vacío se borra: sin modelos
        # duplicados dando vueltas.
        try:
            os.rmdir(legacy)
        except OSError:
            pass
    except Exception:  # noqa: BLE001  (una migración fallida no rompe el arranque)
        log.exception("no pude migrar los modelos de %s", legacy)
    return result


def resolve_data_dir():
    """Use the explicit override or Instant2's own per-user model directory.

    Never infer the location from cwd or a sibling checkout: either can belong
    to the original Instant installation.

    Sin override, se reaprovechan los modelos del producto anterior: el
    instalador ya no los copia, así que la app los mueve una sola vez.
    """
    env = os.environ.get("INSTANT2_DATA")
    if env and env.strip():
        return os.path.expanduser(os.path.expandvars(env.strip()))
    data_dir = user_data_dir()
    migrate_legacy_models(data_dir)
    return data_dir


def model_paths(data_dir=None):
    d = data_dir or resolve_data_dir()
    mdir = os.path.join(d, PARAKEET_SUBDIR)
    return {
        "encoder": os.path.join(mdir, "encoder.int8.onnx"),
        "decoder": os.path.join(mdir, "decoder.int8.onnx"),
        "joiner": os.path.join(mdir, "joiner.int8.onnx"),
        "tokens": os.path.join(mdir, "tokens.txt"),
        "vad": os.path.join(d, VAD_SUBDIR, "silero_vad.onnx"),
        "ten_vad": os.path.join(d, VAD_SUBDIR, "ten_vad.onnx"),
    }
