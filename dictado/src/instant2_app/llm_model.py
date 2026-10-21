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


def model_path(data_dir):
    """Ruta del GGUF dentro del directorio de datos."""
    return os.path.join(data_dir, LLM_SUBDIR, LLM_MODEL_NAME)


def model_ready(data_dir, verify=False):
    """El modelo de lenguaje local está descargado.

    Por defecto alcanza con que el archivo exista y tenga el tamaño esperado
    (verificar el SHA-256 de 3 GB en cada refresco de la interfaz sería
    lento); con `verify=True` se comprueba el hash.
    """
    path = model_path(data_dir)
    if not os.path.isfile(path):
        return False
    if verify:
        ok, _reason = models.verify_file(path, LLM_MODEL_EXPECTED)
        return ok
    try:
        return os.path.getsize(path) == LLM_MODEL_EXPECTED[0]
    except OSError:
        return False


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
    return target
