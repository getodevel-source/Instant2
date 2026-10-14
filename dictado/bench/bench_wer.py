"""WER en español con corpus publico (FLEURS es_419): mide, no cambia nada.

Uso (necesita modelos descargados + `pip install datasets jiwer`, solo bench):
    python dictado/bench/bench_wer.py [--n 100] [--variant baseline,blank02,...]

Cachea la muestra en `dictado/bench/.fleurs_es_cache/` (wavs 16k + refs) para
no re-descargar: la primera corrida baja ~100 clips (~20 min audio).
Variantes:
    baseline  Engine tal cual (silero, blank 0, overlap+frontend on)
    blank02/blank05   mismo + blank_penalty 0.2 / 0.5
    nooverlap         EDGE_OVERLAP = 0
    nofrontend        frontend = identidad (mide su aporte)
    ten               VAD TEN-VAD (requiere ten_vad.onnx)
Normalizacion ES para WER: minusculas, sin puntuacion, espacios simples
(jiwer con transform propia; no usa normalizador ingles de Whisper).
OJO: FLEURS es lectura limpia, optimista vs dictado con mic. Sirve para
ordenar variantes entre si, no para afirmar WER absoluto del producto.
"""
import argparse
import os
import re
import subprocess
import sys
import time
import unicodedata

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app.engine import Engine, SAMPLE_RATE  # noqa: E402
from instant2_app.paths import resolve_data_dir  # noqa: E402

CACHE = os.path.join(os.path.dirname(__file__), ".fleurs_es_cache")
SR = SAMPLE_RATE


def _norm_es(text):
    text = unicodedata.normalize("NFD", (text or "").casefold())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _wer(ref, hyp):
    """WER = (S+D+I)/N con Levenshtein en palabras. Sin dependencias."""
    r, h = (ref or "").split(), (hyp or "").split()
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, a in enumerate(r, start=1):
        cur = [i]
        for j, b in enumerate(h, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (0 if a == b else 1)))
        prev = cur
    return prev[-1] / len(r)


def _decode_blob(blob, sr=SR):
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", str(sr), "pipe:1"],
        input=blob, capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def load_sample(n):
    import wave

    def _read_wav(path):
        with wave.open(path, "rb") as w:
            n = w.getnframes()
            sw = w.getsampwidth()
            raw = w.readframes(n)
            if sw == 4:
                return np.frombuffer(raw, dtype=np.float32)
            return (np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                    / 32768.0)

    os.makedirs(CACHE, exist_ok=True)
    refs_path = os.path.join(CACHE, "refs.txt")
    have = 0
    if os.path.isfile(refs_path):
        with open(refs_path, encoding="utf-8") as f:
            have = len([line for line in f if line.strip()])
    if have >= n:
        print(f"cache: {have} clips en {CACHE}", flush=True)
    else:
        from datasets import Audio, load_dataset

        ds = load_dataset("google/fleurs", "es_419", split="test",
                          streaming=True)
        ds = ds.cast_column("audio", Audio(decode=False))
        idx = have
        t0 = time.time()
        for r in ds:
            if idx >= n:
                break
            wav = _decode_blob(r["audio"]["bytes"])
            pcm = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(os.path.join(CACHE, f"clip_{idx:04d}.wav"),
                           "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(pcm.tobytes())
            with open(refs_path, "a", encoding="utf-8") as f:
                f.write(r["raw_transcription"].replace("\n", " ") + "\n")
            idx += 1
        print(f"bajada: {idx - have} clips en {time.time() - t0:.0f}s",
              flush=True)
    with open(refs_path, encoding="utf-8") as f:
        refs = [line.strip() for line in f if line.strip()][:n]
    wavs = []
    for i in range(len(refs)):
        wav = _read_wav(os.path.join(CACHE, f"clip_{i:04d}.wav"))
        wavs.append(np.ascontiguousarray(wav, dtype=np.float32))
    return wavs, refs


def make_engine(data, variant):
    kw = {"data_dir": data, "threads": 4}
    if variant in ("blank02", "blank05"):
        kw["blank_penalty"] = 0.2 if variant == "blank02" else 0.5
    if variant == "ten":
        kw["vad_model"] = "ten"
    eng = Engine(**kw)
    if variant == "nofrontend":
        import instant2_app.engine as _mod

        _mod.frontend = lambda w: (
            np.ascontiguousarray(np.asarray(w).flatten(), dtype=np.float32),
            0.0, 0.0)
    if variant == "nooverlap":
        eng.EDGE_OVERLAP = 0.0
    eng.recognizer()
    eng.vad()
    return eng


def transcribe_variant(eng, wav, variant):
    text = eng.transcribe(wav)
    if variant == "bias":
        from instant2_app import bias as _bias

        text = _bias.correct_biased(text, conf=eng.last_conf)
    return text


def _degrade(wav, seed=0):
    """Simula mic real generico: -12 dB de nivel + ruido rosa a 15 dB SNR
    + high-pass de laptop barata. Determinista por seed. No modela ningun
    usuario: son las 3 degradaciones mas comunes en dictado con mic."""
    rng = np.random.default_rng(seed)
    x = np.asarray(wav, dtype=np.float64) * 0.25
    white = rng.standard_normal(x.size)
    pink = np.cumsum(white)
    pink = pink / max(1e-9, float(np.max(np.abs(pink))))
    sig_rms = float(np.sqrt(np.mean(x ** 2))) + 1e-9
    noise = pink * (sig_rms / (10 ** (15 / 20))
                    / (float(np.sqrt(np.mean(pink ** 2))) + 1e-9))
    y = x + noise
    spec = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(y.size, 1.0 / float(SR))
    spec[freqs < 200.0] = 0.0
    return np.ascontiguousarray(np.fft.irfft(spec, n=y.size), dtype=np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--variant", default="baseline",
                    help="coma-separadas: baseline,blank02,blank05,"
                         "nooverlap,nofrontend,ten,bias")
    ap.add_argument("--degrade", action="store_true",
                    help="simula mic real (ruido+nivel+HP laptop) para "
                         "validar el bias en takes dudosos")
    args = ap.parse_args()
    data = resolve_data_dir()
    wavs, refs = load_sample(args.n)
    if args.degrade:
        wavs = [_degrade(w, seed=i) for i, w in enumerate(wavs)]
    variants = [v.strip() for v in args.variant.split(",") if v.strip()]
    for variant in variants:
        eng = make_engine(data, variant)
        hyps, dts = [], []
        t0 = time.time()
        for wav in wavs:
            a = time.time()
            hyps.append(transcribe_variant(eng, wav, variant))
            dts.append(time.time() - a)
        total_dur = sum(len(w) / SR for w in wavs)
        wers = [_wer(_norm_es(r), _norm_es(h))
                for r, h in zip(refs, hyps)]
        empties = sum(1 for h in hyps if not h.strip())
        print(f"WER {variant}: n={len(wavs)} "
              f"wer={float(np.mean(wers)):.3f} "
              f"p50={float(np.median(wers)):.3f} "
              f"vacios={empties} "
              f"rtf={sum(dts) / total_dur:.3f}x "
              f"({time.time() - t0:.0f}s pared)", flush=True)


if __name__ == "__main__":
    main()
