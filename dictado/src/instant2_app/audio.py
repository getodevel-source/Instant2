"""Audio: lista de entradas, seleccion, probe y medidor de nivel (stdlib+TUI)."""
import logging
import queue
import re
import sys
import time

log = logging.getLogger("instant2")

SAMPLE_RATE = 16000
CHANNELS = 1

SKIP_INPUT_NAMES = ("sound mapper", "primary sound", "stereo mix", "what u hear",
                    "wave out", "loopback", "speakers wave", "microphone wave")

_WINDOWS_INPUT_PRIORITY = {
    "Windows WASAPI": 0,
    "Windows DirectSound": 1,
    "MME": 2,
    "Windows WDM-KS": 3,
}


def input_stream_options(device):
    """Habilita conversión compartida WASAPI para la tasa de 16 kHz."""
    if sys.platform != "win32":
        return {}
    import sounddevice as sd

    info = sd.query_devices(device, "input")
    hostapi = sd.query_hostapis(info["hostapi"])["name"]
    if hostapi == "Windows WASAPI":
        return {"extra_settings": sd.WasapiSettings(auto_convert=True)}
    return {}


def input_channels(device):
    """Captura hasta dos canales; el reconocedor recibe audio mono."""
    import sounddevice as sd

    info = sd.query_devices(device, "input")
    return min(2, max(1, int(info.get("max_input_channels", 1))))


def to_mono(samples):
    """Elige el canal de mayor energía para no descartar un mic estéreo."""
    import numpy as np

    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        return data
    if data.shape[1] == CHANNELS:
        return data[:, 0]
    levels = np.mean(np.square(data), axis=0)
    return np.ascontiguousarray(data[:, int(np.argmax(levels))])


def list_inputs():
    """Enumeración cruda de PortAudio; puede repetir alias entre host APIs."""
    import sounddevice as sd

    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            out.append((i, d["name"], d["max_input_channels"],
                        d.get("default_samplerate", 0)))
    return out


def _windows_input_identity(name):
    start = name.find("(")
    end = name.rfind(")")
    if start >= 0:
        name = name[start + 1:end if end > start else None]
    name = re.sub(r"^\s*\d+\s*-\s*", "", name.casefold())
    return re.sub(r"\s+", " ", name).strip(" ()\t")


def _same_windows_input(left, right):
    left = _windows_input_identity(left)
    right = _windows_input_identity(right)
    if left == right:
        return bool(left)
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 12 and longer.startswith(shorter)


def input_choices():
    """Entradas presentables; consolida alias de host API sin perder duplicados reales."""
    entries = [
        entry for entry in list_inputs()
        if not any(skip in entry[1].casefold() for skip in SKIP_INPUT_NAMES)
    ]
    if sys.platform != "win32":
        return entries

    ranked = []
    for order, entry in enumerate(entries):
        try:
            backend = input_hostapi(entry[0])
        except Exception:
            backend = ""
        ranked.append((_WINDOWS_INPUT_PRIORITY.get(backend, 4), order, backend, entry))
    ranked.sort(key=lambda candidate: (candidate[0], candidate[1]))

    groups = []
    for candidate in ranked:
        matching = [
            index for index, group in enumerate(groups)
            if any(_same_windows_input(candidate[3][1], row[3][1]) for row in group)
        ]
        if not matching:
            groups.append([candidate])
            continue
        first = matching[0]
        groups[first].append(candidate)
        for index in reversed(matching[1:]):
            groups[first].extend(groups.pop(index))

    selected = []
    for group in groups:
        counts = {}
        for candidate in group:
            counts[candidate[2]] = counts.get(candidate[2], 0) + 1
        keep = max(counts.values())
        selected.extend(sorted(group, key=lambda candidate: (candidate[0], candidate[1]))[:keep])
    return [candidate[3] for candidate in sorted(selected, key=lambda candidate: candidate[1])]


def preferred_input_index(index, choices=None):
    """Mapea un índice alias a su entrada visible/preferida."""
    if index is None:
        return None
    choices = input_choices() if choices is None else choices
    if any(entry[0] == index for entry in choices):
        return index
    if sys.platform != "win32":
        return None
    raw = next((entry for entry in list_inputs() if entry[0] == index), None)
    if raw is None:
        return None
    return next(
        (entry[0] for entry in choices if _same_windows_input(raw[1], entry[1])),
        None,
    )


def input_hostapi(device):
    """Nombre del backend de PortAudio para mostrarlo en el setup."""
    import sounddevice as sd

    info = sd.query_devices(device, "input")
    return sd.query_hostapis(info["hostapi"])["name"]


def _backend_label(device):
    try:
        return input_hostapi(device)
    except Exception:
        return "backend desconocido"


def _input_aliases(device):
    """Aliases cross-host API de la misma ocurrencia, nunca otro mic homónimo."""
    if sys.platform != "win32":
        return [device]
    import sounddevice as sd

    try:
        source = sd.query_devices(device, "input")
        source_name = source["name"]
        source_hostapi = source["hostapi"]
        matching = [
            entry for entry in list_inputs()
            if _same_windows_input(source_name, entry[1])
        ]
        by_hostapi = {}
        for entry in matching:
            hostapi = sd.query_devices(entry[0], "input")["hostapi"]
            by_hostapi.setdefault(hostapi, []).append(entry)
        source_entries = by_hostapi[source_hostapi]
        if len(source_entries) != 1 or source_entries[0][0] != device:
            log.info("aliases ambiguos para mic [%d]: hay %d entradas homónimas "
                     "en el host API seleccionado.", device, len(source_entries))
            return [device]
    except Exception:
        log.warning("no pude asociar aliases del mic [%s] sin ambigüedad",
                    device, exc_info=True)
        return [device]

    aliases = []
    for hostapi, entries in by_hostapi.items():
        if hostapi == source_hostapi:
            continue
        if len(entries) != 1:
            log.info("omito alias del host API %s: hay %d entradas homónimas.",
                     hostapi, len(entries))
            continue
        aliases.append(entries[0])

    def rank(entry):
        try:
            backend = input_hostapi(entry[0])
        except Exception:
            backend = ""
        return _WINDOWS_INPUT_PRIORITY.get(backend, 4), entry[0]

    return [device] + [
        entry[0] for entry in sorted(aliases, key=rank)
    ]


def open_input_stream(device, callback=None):
    """Inicia la captura y, en Windows, prueba solo aliases del mismo mic al fallar."""
    import sounddevice as sd

    candidates = _input_aliases(device)
    last_error = None
    for index, candidate in enumerate(candidates):
        stream = None
        try:
            options = {
                "samplerate": SAMPLE_RATE,
                "channels": input_channels(candidate),
                "dtype": "float32",
                "device": candidate,
                **input_stream_options(candidate),
            }
            if callback is not None:
                options["callback"] = callback
            stream = sd.InputStream(**options)
            stream.start()
        except Exception as error:
            last_error = error
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    log.exception("error cerrando intento fallido del mic [%d]", candidate)
            if index + 1 < len(candidates):
                log.warning("falló mic [%d] (%s); intento alias de la misma entrada "
                            "[%d] (%s): %s",
                            candidate, _backend_label(candidate),
                            candidates[index + 1], _backend_label(candidates[index + 1]),
                            error)
            continue
        if candidate != device:
            log.warning("mic [%d] (%s) falló; captura activa por alias de la misma "
                        "entrada [%d] (%s).",
                        device, _backend_label(device), candidate,
                        _backend_label(candidate))
        return stream, candidate
    raise last_error


def resolve_mic(mic_hint="", mic_index=None, *, strict_hint=False):
    """Resuelve por nombre, conservando índices duplicados y aliases seleccionables."""
    import sounddevice as sd

    choices = input_choices() if mic_hint or mic_index is not None else []
    if mic_hint:
        hint = mic_hint.casefold()
        matches = [
            entry for entry in choices
            if hint in entry[1].casefold() or (
                sys.platform == "win32" and _same_windows_input(mic_hint, entry[1]))
        ]
        if matches:
            selected = next((entry for entry in matches if entry[0] == mic_index), None)
            if selected is None:
                if strict_hint and len(matches) != 1:
                    log.warning("mic '%s' ambiguo al cambiar el índice; no elijo "
                                "un micrófono homónimo.", mic_hint)
                    return None
                selected = matches[0]
            log.info("mic: [%d] %s (resuelto por nombre)", selected[0], selected[1])
            return selected[0]
        log.warning("mic '%s' no encontrado.", mic_hint)
        for i, name, _ch, _rate in choices:
            log.warning("  [%d] %s", i, name)
        if strict_hint:
            log.warning("no uso el índice guardado ni otro default.")
            return None

    if mic_index is not None:
        try:
            d = sd.query_devices(mic_index)
            if d["max_input_channels"] > 0:
                if any(entry[0] == mic_index for entry in choices):
                    log.info("mic: [%d] %s (fijado por config)", mic_index, d["name"])
                    return mic_index
                if sys.platform == "win32":
                    preferred = preferred_input_index(mic_index, choices)
                    if preferred is not None:
                        log.info("mic: [%d] alias Windows preferido para [%d] %s",
                                 preferred, mic_index, d["name"])
                        return preferred
                log.warning("mic_index %d no seleccionable; uso el default del sistema.",
                            mic_index)
            else:
                log.warning("mic_index %d sin entrada; uso el default del sistema.", mic_index)
        except Exception:
            log.warning("mic_index %d invalido; uso el default del sistema.", mic_index)
    try:
        log.info("mic: default del sistema (%s)", sd.default.device)
    except Exception:
        pass
    return None


def probe(device, seconds=0.3):
    """Prueba apertura con el mismo modo callback usado por dictado."""
    stream = None
    try:
        stream, selected = open_input_stream(
            device, callback=lambda _indata, _frames, _time, _status: None)
        time.sleep(seconds)
        log.info("mic probe OK [%s].", selected)
        return True
    except Exception:
        log.exception("mic probe FAIL (revisa permisos, conexión o uso por otra app)")
        return False
    finally:
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()


def peak_meter(device, seconds=3.0, width=40, on_level=None):
    """Mide niveles por callback durante el intervalo solicitado."""
    import numpy as np

    if on_level is None:
        print(f"  Midiendo {seconds:.0f}s... habla ahora!")
    peak_max = 0.0
    samples = queue.Queue()

    def cb(indata, frames, time_info, status):
        if status:
            log.warning("audio status durante medidor: %s", status)
        samples.put(indata.copy())

    stream = None
    try:
        stream, _selected = open_input_stream(device, callback=cb)
        t0 = time.time()
        while time.time() - t0 < seconds:
            remaining = seconds - (time.time() - t0)
            try:
                data = samples.get(timeout=min(0.1, max(0.001, remaining)))
            except queue.Empty:
                continue
            peak = float(np.max(np.abs(data))) if data.size else 0.0
            peak_max = max(peak_max, peak)
            if on_level is not None:
                on_level(peak)
            else:
                bars = int(min(1.0, peak * 8) * width)
                print(f"\r  [{'#' * bars}{' ' * (width - bars)}] {peak:.3f}",
                      end="", flush=True)
        if on_level is None:
            print(f"\n  pico maximo: {peak_max:.3f} "
                  f"({'OK hay senal' if peak_max > 0.005 else 'SILENCIO: revisa mic/volume'})")
    except Exception:
        log.exception("medidor fail")
    finally:
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                log.exception("error deteniendo el medidor")
            try:
                stream.close()
            except Exception:
                log.exception("error cerrando el medidor")
    return peak_max
