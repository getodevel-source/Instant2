"""Rutas: raiz de la app, datos por SO, resolucion del dir de modelos."""
import hashlib
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
# Override del directorio de configuracion (tests y suite aislada).
CONFIG_DIR_ENV = "INSTANT2_CONFIG_DIR"
# Sufijo de la copia anterior que se conserva cuando el destino no está
# verificado (una descarga parcial, por ejemplo).
LEGACY_SUFFIX = ".legacy"


def config_dir():
    """Directorio de la configuracion del usuario.

    `INSTANT2_CONFIG_DIR` lo sobreescribe: lo usan los tests (y los scripts
    que corren la suite) para no tocar el `%APPDATA%` real de la maquina.
    """
    override = os.environ.get(CONFIG_DIR_ENV)
    if override and override.strip():
        return os.path.expanduser(os.path.expandvars(override.strip()))
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


def _sha256(path):
    """SHA-256 del archivo; best-effort ("" si no se puede leer)."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _known_digest(name):
    """(bytes, sha256) publicados de los archivos que descarga la app.

    Con el hash conocido no hace falta calcular el del archivo anterior: si
    el del destino coincide con el publicado, el destino es la copia buena.
    """
    from instant2_app import models

    expected = models.PARAKEET_SHA256.get(name)
    if expected:
        return expected
    if name == "silero_vad.onnx":
        return models.VAD_SHA256
    if name == "ten_vad.onnx":
        return models.TEN_VAD_SHA256
    return None


def _same_content(legacy_file, current_file):
    """True si los dos archivos tienen el mismo contenido."""
    try:
        legacy_size = os.path.getsize(legacy_file)
        current_size = os.path.getsize(current_file)
    except OSError:
        return False
    if legacy_size != current_size:
        return False
    expected = _known_digest(os.path.basename(legacy_file))
    if expected and expected[0] == current_size:
        # El destino se puede verificar contra el hash publicado: si coincide,
        # la copia del destino es la buena y la anterior sobra.
        return _sha256(current_file) == expected[1]
    legacy_digest = _sha256(legacy_file)
    if not legacy_digest:
        return False
    return legacy_digest == _sha256(current_file)


def _tree_files(root):
    """(ruta relativa, ruta absoluta) de los archivos del árbol, ordenados."""
    found = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(base, name)
            found.append((os.path.relpath(full, root), full))
    return sorted(found)


def _equivalent_entry(legacy_item, current_item):
    """True si la entrada del destino es equivalente a la anterior.

    Solo se borra la copia anterior cuando el destino está verificado: los
    mismos archivos, con el mismo contenido. Un destino parcial (una descarga
    cortada) NO es equivalente: la copia anterior se conserva aparte.
    """
    legacy_is_dir = os.path.isdir(legacy_item) and not os.path.islink(legacy_item)
    current_is_dir = os.path.isdir(current_item) and not os.path.islink(current_item)
    if legacy_is_dir != current_is_dir:
        return False
    if not legacy_is_dir:
        return _same_content(legacy_item, current_item)
    for relative, legacy_file in _tree_files(legacy_item):
        current_file = os.path.join(current_item, relative)
        if not os.path.isfile(current_file):
            return False
        if not _same_content(legacy_file, current_file):
            return False
    return True


def _rename_aside(path):
    """Renombra la entrada anterior para conservarla (nunca se borra a ciegas)."""
    candidate = path + LEGACY_SUFFIX
    index = 1
    while os.path.exists(candidate):
        candidate = f"{path}{LEGACY_SUFFIX}-{index}"
        index += 1
    os.rename(path, candidate)
    return candidate


def migrate_legacy_models(data_dir=None, legacy_dir=None):
    """Reutiliza los modelos que dejó el producto anterior ("instant").

    Reproduce lo que hacía el instalador (`ReuseLegacyModels` en el Inno Setup
    que ya no existe), con una diferencia importante: el instalador corría una
    sola vez, antes de que la app escribiera nada en el destino; ahora la app
    llena el destino con descargas parciales, así que una entrada con el mismo
    nombre NO es un duplicado verificado.

    - si el directorio actual no existe, se mueve el anterior entero (un
      rename: no se copia ni un byte) y no queda una copia huérfana;
    - si los dos existen, se mueve lo que falta;
    - si el destino ya tiene una entrada con el mismo nombre, solo se borra la
      anterior si el destino está verificado (mismos archivos, mismo
      contenido). Si no, la anterior se conserva con otro nombre (`<nombre>.legacy`)
      y se avisa: el destino puede ser una descarga parcial.

    Es idempotente y nunca propaga un error: este camino lo usa toda la app
    y una migración fallida no puede impedir que arranque.
    """
    current = os.path.abspath(data_dir or user_data_dir())
    legacy = os.path.abspath(legacy_dir or legacy_user_data_dir())
    result = {"moved": False, "merged": False, "moved_entries": 0,
              "removed_duplicates": 0, "kept_aside": 0, "failed": False}
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
                if not _equivalent_entry(legacy_item, current_item):
                    try:
                        kept = _rename_aside(legacy_item)
                    except OSError:
                        log.warning(
                            "no pude conservar %s: se deja donde está.", legacy_item,
                            exc_info=True)
                        continue
                    result["kept_aside"] += 1
                    log.warning(
                        "modelos: %s ya existe en %s y no es una copia verificada "
                        "(puede ser una descarga parcial); la copia anterior se "
                        "conserva como %s.", legacy_item, current_item, kept)
                    continue
                _remove_tree(legacy_item)
                result["removed_duplicates"] += 1
                log.info("modelos: duplicado verificado, se borró %s.",
                         legacy_item)
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
        result["failed"] = True
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
