"""Rutas: raiz de la app, datos por SO, resolucion del dir de modelos."""
import os
import sys

from instant2_app.identity import APP_SLUG


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


def resolve_data_dir():
    """Use the explicit override or Instant2's own per-user model directory.

    Never infer the location from cwd or a sibling checkout: either can belong
    to the original Instant installation.
    """
    env = os.environ.get("INSTANT2_DATA")
    if env and env.strip():
        return os.path.expanduser(os.path.expandvars(env.strip()))
    return user_data_dir()


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
