"""Descarga del modelo de lenguaje local (GGUF de 3 GB).

El instalador de Windows ya no lleva los pesos: el modelo de lenguaje local se
baja cuando el usuario lo pide desde Ajustes. Son 3 GB, así que bajarlo sin
que lo pidan no tiene sentido (a diferencia del reconocimiento, que son
670 MB y arranca solo).

La descarga reutiliza el descargador de `models`: es reanudable (`.part` con
Range), verifica el SHA-256 antes de publicar el archivo, reintenta y chequea
el espacio en disco antes de empezar.
"""
import logging
import os

from instant2_app import models

LLM_SUBDIR = "llm"
LLM_MODEL_NAME = "Qwen_Qwen3.5-4B-Q4_K_M.gguf"
LLM_MODEL_EXPECTED = (
    3_013_027_808,
    "13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983",
)
LLM_MODEL_URL = (
    "https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF/resolve/"
    "ba06320255db2dbec194dad738d066be90dabf29/"
    "Qwen_Qwen3.5-4B-Q4_K_M.gguf"
)

log = logging.getLogger("instant2")

# Marca al lado del archivo: deja constancia de que el GGUF pasó la
# verificación por hash y de que no cambió desde entonces (con solo el tamaño
# no se sabe si el archivo está corrupto).
VERIFIED_SUFFIX = ".verified"


def model_path(data_dir):
    """Ruta del GGUF dentro del directorio de datos."""
    return os.path.join(data_dir, LLM_SUBDIR, LLM_MODEL_NAME)


def verification_marker(data_dir):
    """Ruta de la marca de verificación del GGUF."""
    return model_path(data_dir) + VERIFIED_SUFFIX


def mark_verified(data_dir):
    """Deja constancia de que el archivo se verificó por hash."""
    path = model_path(data_dir)
    try:
        stat = os.stat(path)
        marker = verification_marker(data_dir)
        temp = f"{marker}.tmp-{os.getpid()}"
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(f"{stat.st_size} {stat.st_mtime_ns}\n")
        os.replace(temp, marker)
        return True
    except OSError:
        log.warning("no pude dejar la marca de verificación del modelo",
                    exc_info=True)
        return False


def is_verified(data_dir):
    """True si el archivo ya fue verificado por hash y no cambió desde entonces."""
    path = model_path(data_dir)
    try:
        with open(verification_marker(data_dir), encoding="utf-8") as handle:
            recorded = handle.read().split()
        stat = os.stat(path)
    except (OSError, ValueError):
        return False
    return (len(recorded) == 2
            and recorded[0] == str(stat.st_size)
            and recorded[1] == str(stat.st_mtime_ns))


def model_ready(data_dir, verify=False):
    """El modelo de lenguaje local está descargado.

    Por defecto alcanza con que el archivo exista y tenga el tamaño esperado
    (verificar el SHA-256 de 3 GB en cada refresco de la interfaz sería
    lento); con `verify=True` se comprueba el hash (y se deja la marca para no
    tener que volver a hacerlo).
    """
    path = model_path(data_dir)
    if not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) != LLM_MODEL_EXPECTED[0]:
            return False
    except OSError:
        return False
    if verify:
        ok, _reason = models.verify_file(path, LLM_MODEL_EXPECTED)
        if ok:
            mark_verified(data_dir)
        return ok
    return True


def needs_verification(data_dir):
    """El archivo tiene el tamaño esperado pero no está verificado por hash.

    Un GGUF de 3 GB con el tamaño justo puede estar corrupto: sin verificarlo
    no se puede decir que esté "listo".
    """
    path = model_path(data_dir)
    if not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) != LLM_MODEL_EXPECTED[0]:
            return False
    except OSError:
        return False
    return not is_verified(data_dir)


def ensure_model(data_dir, progress=None, cancel_event=None):
    """Descarga el GGUF si falta; `progress(done, total)` va en bytes.

    Reanudable: un `.part` cortado se conserva y la próxima vez se sigue desde
    ahí. El archivo solo se publica (os.replace) cuando el SHA-256 coincide.
    """
    from instant2_app.paths import ensure_private_dir

    directory = os.path.join(data_dir, LLM_SUBDIR)
    ensure_private_dir(directory)
    target = model_path(data_dir)
    ok, reason = models.verify_file(target, LLM_MODEL_EXPECTED)
    if ok:
        log.info("el modelo de lenguaje ya está descargado y verificado.")
        if progress is not None:
            progress(LLM_MODEL_EXPECTED[0], LLM_MODEL_EXPECTED[0])
        return target
    if os.path.isfile(target):
        log.warning("el modelo de lenguaje no verifica (%s); se vuelve a descargar.",
                    reason)
        try:
            os.remove(target)
        except OSError:
            log.exception("no pude borrar %s para re-descargarlo", target)
    part = target + ".part"
    pending = LLM_MODEL_EXPECTED[0]
    if os.path.isfile(part):
        try:
            pending = max(0, LLM_MODEL_EXPECTED[0] - os.path.getsize(part))
        except OSError:
            pending = LLM_MODEL_EXPECTED[0]
    models._space_check(pending, directory)

    def report(done, total):
        if progress is not None:
            progress(done, total)

    models._fetch(LLM_MODEL_URL, target, LLM_MODEL_EXPECTED, report,
                  cancel_event=cancel_event)
    # El archivo recién publicado tiene el hash verificado: se deja la marca
    # para no tener que volver a calcularlo.
    mark_verified(data_dir)
    return target
