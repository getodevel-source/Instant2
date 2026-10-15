"""Descarga de modelos: Parakeet int8 (~670MB) + Silero VAD (~1MB).

Cada archivo se verifica con su SHA-256 antes de darlo por bueno. Sin eso, una
descarga cortada o corrupta deja la aplicación fallando después, al cargar el
modelo, con un error que no explica nada. Los hashes de Parakeet son los que
publica Hugging Face en la metadata LFS del repositorio; el de Silero VAD se
calculó sobre el archivo oficial de la release de sherpa-onnx.

La descarga es propia (HTTP con Range) para poder ofrecer lo que la API de
Hugging Face no da: bytes reales en la barra de progreso, reanudación de un
`.part` cortado, chequeo de espacio en disco y espejo configurable
(`INSTANT2_HF_ENDPOINT` o `HF_ENDPOINT`).
"""
import hashlib
import logging
import os
import shutil
import time

PARAKEET_REPO = "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
PARAKEET_FILES = ["encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"]
VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
HF_ENDPOINT_DEFAULT = "https://huggingface.co"

# Nombre de archivo -> (bytes, sha256). Los tres ONNX son LFS en Hugging Face y
# su sha256 esta publicado; tokens.txt es pequeño y no esta en LFS, pero al ser
# el vocabulario del modelo conviene verificar su tamaño.
PARAKEET_SHA256 = {
    "encoder.int8.onnx": (652184281,
                          "acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247"),
    "decoder.int8.onnx": (11845275,
                          "179e50c43d1a9de79c8a24149a2f9bac6eb5981823f2a2ed88d655b24248db4e"),
    "joiner.int8.onnx": (6355277,
                         "3164c13fc2821009440d20fcb5fdc78bff28b4db2f8d0f0b329101719c0948b3"),
    "tokens.txt": (93939,
                   "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d"),
}
VAD_SHA256 = (643854,
              "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6")
# TEN-VAD (alternativa a Silero, opt-in por config `vad_model=ten`):
# build de sherpa-onnx sobre TEN-framework/ten-vad PR #36 (pitch=0).
# Solo 16 kHz, igual que Silero. Se usa el int8 (126 KB).
TEN_VAD_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
               "asr-models/ten-vad.int8.onnx")
TEN_VAD_SHA256 = (129534,
                  "880c072f188efa169ea028b2159d1b3a438e153d080b87eac31b74ecad511e61")

DOWNLOAD_ATTEMPTS = 3
# Colchón que se exige libre además de lo que falta bajar: el disco escribe
# mientras descarga y un disco lleno a mitad de camino es peor que no empezar.
DISK_MARGIN = 256 * 1024 * 1024
CHUNK = 1 << 20
READ_TIMEOUT = 60

log = logging.getLogger("instant2")


class ModelIntegrityError(RuntimeError):
    """El archivo descargado no coincide con el hash esperado."""


class DownloadCancelled(RuntimeError):
    """El usuario canceló una descarga sin descartar los datos parciales."""


def _names():
    # En disco sherpa espera encoder.int8.onnx etc; engine mapea esos nombres.
    return {"encoder.int8.onnx": "encoder.int8.onnx",
            "decoder.int8.onnx": "decoder.int8.onnx",
            "joiner.int8.onnx": "joiner.int8.onnx",
            "tokens.txt": "tokens.txt"}


def sha256_of(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_file(path, expected):
    """(ok, motivo) comparando tamaño y sha256 contra `expected`."""
    size, digest = expected
    if not os.path.isfile(path):
        return False, "no existe"
    actual_size = os.path.getsize(path)
    if actual_size != size:
        return False, f"tamaño {actual_size} != {size}"
    actual = sha256_of(path)
    if actual != digest:
        return False, f"sha256 {actual[:16]}… != {digest[:16]}…"
    return True, ""


def _endpoint():
    """Espejo de Hugging Face en uso (INSTANT2_HF_ENDPOINT gana a HF_ENDPOINT)."""
    value = (os.environ.get("INSTANT2_HF_ENDPOINT")
             or os.environ.get("HF_ENDPOINT")
             or HF_ENDPOINT_DEFAULT)
    return value.strip().rstrip("/") or HF_ENDPOINT_DEFAULT


def _steps():
    """Pasos de descarga: [(paso, subdir, [(url, nombre, esperado)])]."""
    from instant2_app.paths import PARAKEET_SUBDIR, VAD_SUBDIR

    endpoint = _endpoint()
    parakeet = [
        (f"{endpoint}/{PARAKEET_REPO}/resolve/main/{name}",
         name, PARAKEET_SHA256[name])
        for name in PARAKEET_FILES]
    return (("parakeet", PARAKEET_SUBDIR, parakeet),
            ("vad", VAD_SUBDIR, [(VAD_URL, "silero_vad.onnx", VAD_SHA256)]))


def _ten_vad_step():
    """Paso extra opt-in: TEN-VAD int8 (~126 KB, VAD alternativo)."""
    from instant2_app.paths import VAD_SUBDIR

    return ("ten-vad", VAD_SUBDIR,
            [(TEN_VAD_URL, "ten_vad.onnx", TEN_VAD_SHA256)])


def _space_info(pending_bytes, destination_dir):
    """Espacio libre y requerido para descargar y verificar los archivos."""
    try:
        free = shutil.disk_usage(destination_dir).free
    except OSError:
        free = None
    required = pending_bytes + DISK_MARGIN if pending_bytes else 0
    return {
        "pending_bytes": max(0, int(pending_bytes)),
        "required_bytes": required,
        "free_bytes": free,
        "enough": free is None or free >= required,
    }


def _space_check(pending_bytes, destination_dir):
    """Frena antes de empezar si no hay disco y devuelve el preflight."""
    info = _space_info(pending_bytes, destination_dir)
    if not info["enough"]:
        required = info["required_bytes"]
        free = info["free_bytes"]
        raise ModelIntegrityError(
            "no hay espacio para los modelos: hacen falta "
            f"{required / 1e6:.0f} MB libres en {destination_dir}, "
            f"pero hay {free / 1e6:.0f} MB. Liberá espacio y reintentá.")
    return info


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled("descarga cancelada por el usuario")


def _fetch_once(url, part, size, report, cancel_event=None):
    """Un intento HTTP: reanuda desde `.part` con Range y reporta bytes."""
    import urllib.request

    _check_cancelled(cancel_event)
    offset = os.path.getsize(part) if os.path.isfile(part) else 0
    if offset >= size:
        return  # ya está completo en disco; lo decide la verificación
    headers = {
        "User-Agent": "Instant2 (+https://github.com/getodevel-source/Instant2)"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=READ_TIMEOUT) as response:
        if offset and getattr(response, "status", 200) != 206:
            # El servidor (o el espejo) ignoró el rango: se empieza de cero.
            offset = 0
        with open(part, "ab" if offset else "wb") as handle:
            done = offset
            report(done, size)
            while True:
                _check_cancelled(cancel_event)
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                report(min(done, size), size)


def _fetch(url, destination, expected, report, attempts=None,
           cancel_event=None):
    """Baja `url` a `destination`: reanudable, con reintentos y SHA-256.

    Escribe en `destination + '.part'` y publica con os.replace recién cuando
    el hash coincide, así un corte nunca deja un archivo a medias en su lugar.
    Un `.part` cortado se conserva para reanudar en el próximo intento; uno
    completo pero inválido se descarta.
    """
    if attempts is None:
        attempts = DOWNLOAD_ATTEMPTS
    size, _digest = expected
    part = destination + ".part"
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            _check_cancelled(cancel_event)
            _fetch_once(url, part, size, report, cancel_event=cancel_event)
            _check_cancelled(cancel_event)
            ok, reason = verify_file(part, expected)
            if not ok:
                raise ModelIntegrityError(
                    f"{os.path.basename(destination)} {reason}")
            os.replace(part, destination)
            return
        except DownloadCancelled:
            raise
        except ModelIntegrityError as error:
            last_error = error
            log.warning("descarga de %s no verifica (intento %d/%d): %s",
                        os.path.basename(destination), attempt, attempts, error)
            # Contenido inválido: reanudarlo no arregla nada, se empieza de cero.
            if os.path.isfile(part):
                try:
                    os.remove(part)
                except OSError:
                    pass
            if attempt < attempts:
                if cancel_event is not None:
                    if cancel_event.wait(2 * attempt):
                        raise DownloadCancelled(
                            "descarga cancelada por el usuario") from error
                else:
                    time.sleep(2 * attempt)
        except Exception as error:  # red o disco: el .part queda para reanudar
            last_error = error
            log.warning("descarga de %s falló (intento %d/%d): %s",
                        os.path.basename(destination), attempt, attempts, error)
            if attempt < attempts:
                if cancel_event is not None:
                    if cancel_event.wait(2 * attempt):
                        raise DownloadCancelled(
                            "descarga cancelada por el usuario") from error
                else:
                    time.sleep(2 * attempt)
    raise ModelIntegrityError(
        f"no se pudo descargar {os.path.basename(destination)} tras "
        f"{attempts} intentos: {last_error}")


def download_models(data_dir, progress=None, include_ten_vad=False,
                    cancel_event=None, preflight=None):
    """Baja Parakeet int8 (~670MB) + Silero VAD (~1MB).

    Con `include_ten_vad=True` suma TEN-VAD int8 (~126 KB, VAD alternativo
    opt-in por config `vad_model=ten`). progress(step, done, total):
    step = "parakeet"|"vad"|"ten-vad", done/total en bytes
    reales de ese paso (callable solo cuando hay algo que bajar). Sin callback
    usa log + print simple. Reanudable (`.part` + Range) y con espejo
    configurable por `INSTANT2_HF_ENDPOINT`/`HF_ENDPOINT`. Un archivo presente
    pero corrupto se vuelve a descargar en lugar de darse por bueno.
    """
    printed = {"progress": False}

    def _report(step, done, total):
        if progress is not None:
            progress(step, done, total)
        elif total > (1 << 20):
            printed["progress"] = True
            print(f"\r  {step}: {done / 1e6:.0f}/{total / 1e6:.0f} MB",
                  end="", flush=True)

    steps = []
    pending_bytes = 0
    wanted = list(_steps())
    if include_ten_vad:
        wanted.append(_ten_vad_step())
    for step, subdir, entries in wanted:
        directory = os.path.join(data_dir, subdir)
        from instant2_app.paths import ensure_private_dir
        ensure_private_dir(directory)
        total = sum(expected[0] for _url, _name, expected in entries)
        done = 0
        jobs = []
        for url, name, expected in entries:
            target = os.path.join(directory, name)
            ok, reason = verify_file(target, expected)
            if ok:
                done += expected[0]
                log.info("%s ya presente y verificado.", name)
                continue
            if os.path.isfile(target):
                log.warning("%s %s; se vuelve a descargar", name, reason)
                try:
                    os.remove(target)
                except OSError:
                    log.exception("no pude borrar %s para re-descargarlo", target)
            jobs.append((url, target, expected))
        if jobs:
            steps.append((step, total, done, jobs))
            pending_bytes += sum(
                max(0, expected[0] - (os.path.getsize(target + ".part")
                                      if os.path.isfile(target + ".part") else 0))
                for _url, target, expected in jobs)
        elif progress is None:
            label = {
                "parakeet": "reconocimiento",
                "vad": "detección de voz",
                "ten-vad": "detección de voz avanzada",
            }.get(step, "componente de voz")
            print(f"  {label}: ya presente y verificado.")

    if not steps:
        return data_dir

    space = _space_info(pending_bytes, data_dir)
    if preflight is not None:
        preflight(space)
    _space_check(pending_bytes, data_dir)
    _check_cancelled(cancel_event)
    # Solo el host del espejo, nunca la URL completa: la URL lleva repo/ruta
    # y el log no necesita más para diagnosticar.
    try:
        from urllib.parse import urlsplit as _urlsplit
        _host = _urlsplit(_endpoint()).hostname or "huggingface.co"
    except Exception:
        _host = "huggingface.co"
    log.info("modelos: %.0f MB pendientes desde %s",
             pending_bytes / 1e6, _host)

    for step, total, done, jobs in steps:
        if progress is None:
            print(f"  {step}: bajando ~{(total - done) / 1e6:.0f} MB…")
        for url, target, expected in jobs:
            base = done

            def report(file_done, _file_total, _base=base, _step=step,
                       _total=total):
                _report(_step, min(_base + file_done, _total), _total)

            _fetch(url, target, expected, report, cancel_event=cancel_event)
            done += expected[0]
        if progress is None:
            if printed["progress"]:
                print()
                printed["progress"] = False
            print(f"  {step}: OK (verificado).")
    return data_dir


def check(data_dir, include_ten_vad=False, verify=False):
    from instant2_app.paths import model_paths

    p = model_paths(data_dir)
    if not include_ten_vad:
        p = {k: v for k, v in p.items() if k != "ten_vad"}
    if not verify:
        return {k: os.path.isfile(v) for k, v in p.items()}
    expected = dict(PARAKEET_SHA256)
    expected["silero_vad.onnx"] = VAD_SHA256
    if include_ten_vad:
        expected["ten_vad.onnx"] = TEN_VAD_SHA256
    out = {}
    for key, path in p.items():
        name = os.path.basename(path)
        exp = expected.get(name)
        if exp is None:
            out[key] = os.path.isfile(path)
            continue
        ok, _reason = verify_file(path, exp)
        out[key] = ok
    return out

def check_integrity(data_dir, parakeet=None, vad=None, ten_vad=None):
    """Estado por archivo: True si verifica, o el motivo si no.

    `check` responde si el archivo existe; esto responde si además sirve. Los
    hashes se pueden pasar para probar sin los 670 MB reales.
    """
    from instant2_app.paths import PARAKEET_SUBDIR, VAD_SUBDIR

    parakeet = PARAKEET_SHA256 if parakeet is None else parakeet
    vad = VAD_SHA256 if vad is None else vad
    ten_vad = TEN_VAD_SHA256 if ten_vad is None else ten_vad
    result = {}
    for name, expected in parakeet.items():
        ok, reason = verify_file(
            os.path.join(data_dir, PARAKEET_SUBDIR, name), expected)
        result[name] = True if ok else reason
    ok, reason = verify_file(os.path.join(data_dir, VAD_SUBDIR, "silero_vad.onnx"),
                             vad)
    result["silero_vad.onnx"] = True if ok else reason
    ten_path = os.path.join(data_dir, VAD_SUBDIR, "ten_vad.onnx")
    if ten_vad is not None and os.path.isfile(ten_path):
        ok, reason = verify_file(ten_path, ten_vad)
        result["ten_vad.onnx"] = True if ok else reason
    return result
