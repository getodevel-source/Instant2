"""Lectura local en español con Kokoro ONNX.

El texto recibido solo vive en memoria durante la solicitud. No se escribe en
configuración, historial, archivos temporales ni registros. Los pesos se
descargan bajo el directorio de modelos de Instant2 y se verifican por SHA-256.
"""
import os
import json
import hashlib
import queue
import re
import tempfile
import threading
import zipfile

from instant2_app import models
from instant2_app.paths import config_dir, ensure_private_dir, restrict_file

KOKORO_SUBDIR = "kokoro-onnx"
MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.1/kokoro-v1.0.onnx"
)
VOICES_URL = (
    "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/"
    "1939ad2a8e416c0acfeecc08a694d14ef25f2231/voices/ef_dora.bin"
)
# The ONNX asset is from kokoro-onnx model-files-v1.1. The Spanish Dora style
# is pinned to one onnx-community revision and converted to a one-voice NPZ.
MODEL_FILE = (325_505_369,
              "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a")
DORA_FILE = (522_240,
             "f66ec66bd295acb18372e37008533a9a3228483ccd294e7538d5d9294ac9a532")
DORA_STYLE_SHAPE = (510, 1, 256)
MODEL_TOTAL_BYTES = MODEL_FILE[0] + DORA_FILE[0]
MAX_TEXT_CHARS = 10_000
VOICES = {"ef_dora": "Dora · femenina"}
DEFAULT_VOICE = "ef_dora"
DEFAULT_SPEED = 1.0
# Las pausas predeterminadas de Kokoro (250 ms entre frases) se notan mucho
# cuando el texto se reproduce en varios lotes. Estas pausas conservan la
# puntuación sin que cada lote suene como una lectura separada.
SENTENCE_PAUSE_SECONDS = 0.12
CLAUSE_PAUSE_SECONDS = 0.06
_DOWNLOAD_LOCK = threading.Lock()
_PREFERENCES_FILE = "tts-preferences.json"


class SpeechCancelled(RuntimeError):
    """La persona detuvo una lectura o una descarga."""


def asset_paths(data_dir):
    directory = os.path.join(data_dir, KOKORO_SUBDIR)
    return {
        "directory": directory,
        "model": os.path.join(directory, "kokoro-v1.0.onnx"),
        "voice_source": os.path.join(directory, "ef_dora.bin"),
        "voices": os.path.join(directory, "voices-dora-v1.0.npz"),
    }


def model_ready(data_dir):
    """Comprobación rápida para UI; la integridad SHA se comprueba al cargar."""
    paths = asset_paths(data_dir)
    try:
        return (os.path.isfile(paths["model"])
                and os.path.getsize(paths["model"]) == MODEL_FILE[0]
                and os.path.isfile(paths["voices"]))
    except OSError:
        return False


def _voice_archive_valid(path):
    """Check that the cached Kokoro voice archive contains only Dora."""
    try:
        import numpy as np

        with np.load(path, allow_pickle=False) as archive:
            if archive.files != [DEFAULT_VOICE]:
                return False
            voice = archive[DEFAULT_VOICE]
            if voice.shape != DORA_STYLE_SHAPE or voice.dtype != np.float32:
                return False
            return hashlib.sha256(voice.tobytes()).hexdigest() == DORA_FILE[1]
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return False


def _prepare_voice_archive(source_path, archive_path):
    """Verify Dora's standalone float32 style and wrap it as Kokoro's NPZ."""
    import numpy as np

    ok, _reason = models.verify_file(source_path, DORA_FILE)
    if not ok:
        raise models.ModelIntegrityError("Dora voice failed integrity verification")
    if _voice_archive_valid(archive_path):
        return archive_path

    voice = np.fromfile(source_path, dtype=np.float32)
    expected_values = 1
    for dimension in DORA_STYLE_SHAPE:
        expected_values *= dimension
    if voice.size != expected_values:
        raise models.ModelIntegrityError("Dora voice file has an invalid format")
    voice = voice.reshape(DORA_STYLE_SHAPE)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=".voices-dora-", suffix=".npz.tmp",
            dir=os.path.dirname(archive_path) or ".")
        with os.fdopen(fd, "wb") as handle:
            np.savez_compressed(handle, **{DEFAULT_VOICE: voice})
            handle.flush()
            os.fsync(handle.fileno())
        if not _voice_archive_valid(temporary):
            raise models.ModelIntegrityError("no pude preparar la voz de Dora")
        os.replace(temporary, archive_path)
        temporary = None
        restrict_file(archive_path)
        try:
            os.remove(source_path)
        except OSError:
            pass
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return archive_path


def load_preferences():
    """Load only voice choices; dictated or selected text is never persisted."""
    result = {"voice": DEFAULT_VOICE, "speed": DEFAULT_SPEED}
    try:
        with open(os.path.join(config_dir(), _PREFERENCES_FILE),
                  encoding="utf-8") as handle:
            saved = json.load(handle)
        if isinstance(saved, dict):
            voice = saved.get("voice")
            if isinstance(voice, str) and voice in VOICES:
                result["voice"] = voice
            try:
                result["speed"] = max(0.8, min(1.2, float(saved.get("speed", 1.0))))
            except (TypeError, ValueError):
                pass
    except (OSError, ValueError):
        pass
    return result


def save_preferences(voice, speed):
    """Atomically save voice/speed in a private file, without reading text."""
    voice = voice if isinstance(voice, str) and voice in VOICES else DEFAULT_VOICE
    try:
        speed = max(0.8, min(1.2, float(speed)))
    except (TypeError, ValueError):
        speed = DEFAULT_SPEED
    directory = config_dir()
    ensure_private_dir(directory)
    path = os.path.join(directory, _PREFERENCES_FILE)
    fd, temporary = tempfile.mkstemp(prefix=".tts-prefs-", suffix=".tmp",
                                     dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"voice": voice, "speed": speed}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        restrict_file(path)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return {"voice": voice, "speed": speed}


def _cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise SpeechCancelled("lectura cancelada")


def ensure_model(data_dir, progress=None, cancel_event=None):
    """Descarga el ONNX y solo la voz Dora; conserva descargas parciales."""
    paths = asset_paths(data_dir)
    ensure_private_dir(paths["directory"])
    entries = ((MODEL_URL, paths["model"], MODEL_FILE),
               (VOICES_URL, paths["voice_source"], DORA_FILE))
    with _DOWNLOAD_LOCK:
        _cancelled(cancel_event)
        pending = []
        already_done = 0
        model_ok, _reason = models.verify_file(paths["model"], MODEL_FILE)
        if model_ok:
            already_done += MODEL_FILE[0]
        else:
            pending.append(entries[0])
        archive_ok = _voice_archive_valid(paths["voices"])
        source_ok, _reason = models.verify_file(paths["voice_source"], DORA_FILE)
        if archive_ok or source_ok:
            already_done += DORA_FILE[0]
        else:
            pending.append(entries[1])
        pending_bytes = sum(
            max(0, expected[0] - (os.path.getsize(target + ".part")
                                  if os.path.isfile(target + ".part") else 0))
            for _url, target, expected in pending)
        models._space_check(pending_bytes, paths["directory"])
        done_before = already_done
        for index, (url, target, expected) in enumerate(pending, start=1):
            _cancelled(cancel_event)
            this_file_base = done_before

            def report(done, _total, base=this_file_base):
                if progress is not None:
                    progress(base + done, MODEL_TOTAL_BYTES,
                             f"Descargando componentes de voz ({index} de {len(pending)})…")

            models._fetch(url, target, expected, report, cancel_event=cancel_event)
            done_before += expected[0]
        ok, _reason = models.verify_file(paths["model"], MODEL_FILE)
        if not ok:
            raise models.ModelIntegrityError("Kokoro model failed integrity verification")
        if not _voice_archive_valid(paths["voices"]):
            _prepare_voice_archive(paths["voice_source"], paths["voices"])
        try:
            os.remove(paths["voice_source"])
        except OSError:
            pass
        try:
            os.remove(os.path.join(paths["directory"], "voices-v1.0.bin"))
        except OSError:
            pass
        if progress is not None:
            progress(MODEL_TOTAL_BYTES, MODEL_TOTAL_BYTES, "Componentes de voz verificados.")
    return paths


def split_text(text, limit=450):
    """Parte texto largo en frases, no en renglones de formato."""
    pieces = []
    # Los saltos de línea suelen ser ajuste visual o formato pegado. No hagas
    # que cada renglón se convierta en una llamada de voz independiente.
    normalized = re.sub(r"\s*\n+\s*", " ", text.strip())
    for paragraph in (normalized,):
        words = paragraph.split()
        if not words:
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                pieces.append(current)
            current = word
            if len(current) > limit:
                pieces.append(current)
                current = ""
        if current:
            pieces.append(current)

    chunks = []
    sentence_split = re.compile(r"(?<=[.!?…])\s+")
    for piece in pieces:
        sentences = sentence_split.split(piece)
        # Repack whole sentences while respecting the engine's practical limit.
        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > limit:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


def _continuous_audio_chunks(engine, phonemes, voice, speed, cancel_event=None):
    """Sintetiza ventanas solapadas y las entrega mientras avanza el modelo.

    Kokoro ya usa estas ventanas para mantener la prosodia en textos largos,
    pero su API `create()` devuelve el audio completo al final. Esta versión
    reproduce el mismo solapamiento y fundido cruzado de la versión fijada de
    kokoro-onnx, entregando cada tramo al reproductor antes de sintetizar todo
    el texto.
    """
    import numpy as np
    from kokoro_onnx import sliding
    from kokoro_onnx.config import SAMPLE_RATE

    tokens = engine.tokenizer.tokenize(phonemes, limit=None)
    if not tokens:
        raise ValueError("empty phonemes")
    known_phonemes = engine.tokenizer.known(phonemes)
    if len(known_phonemes) != len(tokens):
        raise RuntimeError("Kokoro no pudo alinear los fonemas de la lectura")
    voice_style = engine.get_voice_style(voice)
    style = engine._style_for(voice_style, len(tokens))
    insert_pauses = engine.create_timed.__globals__["insert_pauses"]
    start = 0
    held_tail = None
    fade_samples = max(1, int(sliding.CROSSFADE * SAMPLE_RATE))

    while start < len(tokens):
        _cancelled(cancel_event)
        # A shorter first keep region starts speaking earlier. Full context and
        # lookahead remain intact; following windows keep the upstream size.
        keep = min(120, sliding.KEEP) if start == 0 else sliding.KEEP
        end = min(start + keep, len(tokens))
        first = max(0, start - sliding.CONTEXT)
        last = min(len(tokens), end + sliding.LOOKAHEAD)
        window_audio, duration = engine._infer(tokens[first:last], style, speed)
        edges = sliding.token_edges(duration, len(window_audio))

        if end < len(tokens):
            end = sliding._quietest(
                window_audio,
                edges,
                first,
                sliding._candidates(
                    tokens, engine._stops, engine._spaces, end, start, last),
                SAMPLE_RATE,
            )
        if end <= start:
            raise RuntimeError(f"Kokoro no pudo avanzar la ventana {start}:{end}")

        begin_sample = edges[start - first + 1]
        end_sample = edges[end - first + 1]
        piece = window_audio[begin_sample:end_sample]
        piece_edges = edges[start - first + 1:end - first + 2] - begin_sample
        spoken = sliding.timings(
            known_phonemes[start:end], piece_edges, SAMPLE_RATE)
        piece, _spoken = insert_pauses(
            piece, spoken, SAMPLE_RATE,
            SENTENCE_PAUSE_SECONDS, CLAUSE_PAUSE_SECONDS)

        final_piece = end == len(tokens)
        if held_tail is None:
            if final_piece:
                if len(piece):
                    yield piece
            elif len(piece) > fade_samples:
                yield piece[:-fade_samples]
                held_tail = piece[-fade_samples:]
            else:
                held_tail = piece
        else:
            overlap = min(fade_samples, len(held_tail), len(piece))
            if len(held_tail) > overlap:
                yield held_tail[:-overlap]
            if overlap:
                ramp = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
                yield held_tail[-overlap:] * (1.0 - ramp) + piece[:overlap] * ramp
            remainder = piece[overlap:]
            if final_piece:
                if len(remainder):
                    yield remainder
                held_tail = None
            elif len(remainder) > fade_samples:
                yield remainder[:-fade_samples]
                held_tail = remainder[-fade_samples:]
            else:
                held_tail = remainder
        start = end

    if held_tail is not None and len(held_tail):
        yield held_tail


class KokoroReader:
    """Mantiene Kokoro en memoria durante la sesión para acelerar relecturas."""

    def __init__(self, data_dir, threads=4):
        from instant2_app.resource_policy import inference_threads
        self.data_dir = data_dir
        self.threads = inference_threads(threads)
        self._engine = None
        self._g2p = None
        self._lock = threading.Lock()

    def unload(self):
        """Release the resident Kokoro model and phonemizer when idle."""
        with self._lock:
            was_loaded = self._engine is not None or self._g2p is not None
            self._engine = None
            self._g2p = None
        return was_loaded

    def is_loaded(self):
        with self._lock:
            return self._engine is not None or self._g2p is not None

    def _load(self, cancel_event, progress):
        _cancelled(cancel_event)
        if self._engine is None or self._g2p is None:
            paths = ensure_model(self.data_dir, progress, cancel_event)
            _cancelled(cancel_event)
            from kokoro_onnx import Kokoro
            from misaki.espeak import EspeakG2P
            import onnxruntime as ort
            # ensure_model() verifies the compact Dora archive and removes the
            # raw .bin. Re-preparing it here would fail on every later load
            # because that source file is intentionally no longer present.
            options = ort.SessionOptions()
            options.intra_op_num_threads = self.threads
            options.inter_op_num_threads = 1
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            options.add_session_config_entry("session.inter_op.allow_spinning", "0")
            session = ort.InferenceSession(paths["model"], sess_options=options,
                                           providers=["CPUExecutionProvider"])
            self._engine = Kokoro.from_session(session, paths["voices"])
            self._g2p = EspeakG2P(language="es")
        return self._engine, self._g2p

    def play(self, text, voice=DEFAULT_VOICE, speed=DEFAULT_SPEED,
             cancel_event=None, progress=None, level_callback=None,
             bands_callback=None):
        """Reproduce texto continuo con audio anticipado y un solo flujo de salida."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty text")
        text = text.strip()
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError("text too long")
        if voice not in VOICES:
            voice = DEFAULT_VOICE
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            speed = DEFAULT_SPEED
        speed = max(0.8, min(1.2, speed))
        text = re.sub(r"\s*\n+\s*", " ", text.strip())
        fallback_chunks = split_text(text)
        if not fallback_chunks:
            raise ValueError("empty text")

        import numpy as np
        import sounddevice as sd

        with self._lock:
            engine, g2p = self._load(cancel_event, progress)
            _cancelled(cancel_event)
            if progress is not None:
                progress(None, None, "Preparando lectura continua…")
            sample_rate = 24_000
            block_samples = sample_rate // 10
            completed = 0
            from instant2_app.metering import SpectrumMeter
            meter = SpectrumMeter(sample_rate, .98) if bands_callback is not None else None

            def synthesize(chunk):
                phonemes, _ = g2p(chunk)
                samples, generated_rate = engine.create(
                    phonemes, voice, speed=speed, lang="es", is_phonemes=True,
                    sentence_pause=SENTENCE_PAUSE_SECONDS,
                    clause_pause=CLAUSE_PAUSE_SECONDS,
                    continuous=True)
                if generated_rate != sample_rate:
                    raise RuntimeError("unexpected Kokoro sample rate")
                return np.asarray(samples, dtype=np.float32).reshape(-1)

            continuous_supported = (
                bool(getattr(engine, "has_timings", False))
                and hasattr(engine, "tokenizer")
                and hasattr(engine, "_infer")
                and hasattr(engine, "_style_for")
            )
            if continuous_supported:
                phonemes, _ = g2p(text)
                audio_source = _continuous_audio_chunks(
                    engine, phonemes, voice, speed, cancel_event)
                total_chunks = None
            else:
                audio_source = (
                    synthesize(chunk) for chunk in fallback_chunks)
                total_chunks = len(fallback_chunks)

            _cancelled(cancel_event)
            ready = queue.Queue(maxsize=2)
            producer_stop = threading.Event()
            finished = object()

            def offer(item):
                while not producer_stop.is_set() and not (
                        cancel_event is not None and cancel_event.is_set()):
                    try:
                        ready.put(item, timeout=0.1)
                        return True
                    except queue.Full:
                        continue
                return False

            def prepare_following_chunks():
                try:
                    for index, samples in enumerate(audio_source):
                        _cancelled(cancel_event)
                        if not offer(("audio", index, samples)):
                            return
                except SpeechCancelled:
                    pass
                except Exception as exc:  # deliver the failure on the audio thread
                    offer(("error", exc))
                finally:
                    offer(finished)

            producer = threading.Thread(
                target=prepare_following_chunks,
                name="instant2-kokoro-prefetch", daemon=True)
            producer.start()

            def next_audio():
                while True:
                    _cancelled(cancel_event)
                    try:
                        item = ready.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if item is finished:
                        return None
                    if item[0] == "error":
                        raise item[1]
                    return item

            try:
                first = next_audio()
                if first is None:
                    return completed

                def output(stream, samples):
                    nonlocal completed
                    for offset in range(0, len(samples), block_samples):
                        _cancelled(cancel_event)
                        block = samples[offset:offset + block_samples]
                        stream.write(block)
                        if len(block) and (level_callback is not None
                                           or bands_callback is not None):
                            try:
                                rms = float(np.sqrt(np.mean(np.square(block))))
                                if level_callback is not None:
                                    level_callback(min(1.0, rms * 7.0))
                                if bands_callback is not None:
                                    if rms > 0.006:
                                        bands = meter.bands(block)
                                    else:
                                        bands = np.zeros(9, dtype=np.float64)
                                    bands_callback([float(v) for v in bands])
                            except Exception:
                                # Metering is visual feedback only; a failed
                                # animation update must never interrupt speech.
                                pass
                    completed += len(samples)

                with sd.OutputStream(samplerate=sample_rate, channels=1,
                                     dtype="float32", latency="high") as stream:
                    try:
                        _cancelled(cancel_event)
                        if progress is not None:
                            progress(None, None, "Leyendo voz continua…")
                        item = first
                        while item is not None:
                            _kind, chunk_index, samples = item
                            output(stream, samples)
                            if progress is not None and total_chunks is not None:
                                progress(chunk_index + 1, total_chunks,
                                         f"Leyendo frase {chunk_index + 1} de {total_chunks}…")
                            item = next_audio()
                    except SpeechCancelled:
                        # stop() drains buffered audio; abort() drops it so
                        # The reading shortcut does not keep speaking after cancellation.
                        stream.abort()
                        raise
            finally:
                producer_stop.set()
                producer.join()
            return completed
