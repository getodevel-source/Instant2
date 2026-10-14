"""Set tecnico ES con TTS + degradado: valida el bias general.

FLEURS no contiene terminos tecnicos ni takes dudosos, asi que no puede
medir el bias (disenado para rescatar tecnicos en takes de baja confianza).
Este bench genera 24 frases con los terminos del diccionario general via
TTS (voz ES), las degrada como mic real y mide baseline vs bias con WER.

Uso: python dictado/bench/bench_bias.py [--regen]
Cachea en dictado/bench/.bias_cache/ (wavs + refs).
"""
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from bench_wer import _degrade, _norm_es, _wer  # noqa: E402
from instant2_app.engine import SAMPLE_RATE  # noqa: E402
from instant2_app.paths import resolve_data_dir  # noqa: E402

CACHE = os.path.join(os.path.dirname(__file__), ".bias_cache")
SR = SAMPLE_RATE

SENTENCES = [
    "Hice un commit del workflow en staging y el build falló.",
    "El deploy del backend tarda porque el driver es viejo.",
    "El frontend carga el plugin pero el rollback no anda.",
    "Probé el benchmark en Python y el plugin falló.",
    "Subí el commit a GitHub y corrí el workflow de staging.",
    "El driver del backend rompe el deploy del frontend.",
    "Hacemos rollback del build y repetimos el benchmark.",
    "El plugin de Python necesita un commit urgente.",
    "Dicto con Parakeet y corrijo con el perfil de Instant2.",
    "Qwen y OpenAI fallan igual que GitHub en nombres.",
    "Le pregunté a Qwen por el deploy y me dijo OpenAI.",
    "Parakeet transcribe mejor que Qwen los nombres de GitHub.",
    "El staging del backend pide otro commit antes del deploy.",
    "Corrí el benchmark del workflow tras el rollback.",
    "El frontend sin el plugin no pasa el build.",
    "Python y GitHub se llevan bien con Parakeet e Instant2.",
    "Hice commit, push y deploy del backend a staging.",
    "El driver viejo frena el workflow del frontend.",
    "Staging y rollback antes de cada deploy del build.",
    "El benchmark marca que el plugin gasta driver.",
    "Qwen dice que OpenAI usa Parakeet para GitHub.",
    "Instant2 dicta y Qwen corrige mejor que OpenAI.",
    "El workflow pide commit en staging tras el build.",
    "Hago el deploy con rollback si el driver falla.",
]


async def _tts(text, dst, voice="es-AR-ElenaNeural"):
    import edge_tts

    tts = edge_tts.Communicate(text, voice)
    await tts.save(dst)


def build_cache():
    import asyncio
    import wave

    os.makedirs(CACHE, exist_ok=True)
    refs_path = os.path.join(CACHE, "refs.txt")
    have = 0
    if os.path.isfile(refs_path):
        with open(refs_path, encoding="utf-8") as f:
            have = len([line for line in f if line.strip()])
    if have >= len(SENTENCES):
        print(f"cache bias: {have} frases", flush=True)
        return
    voices = ["es-AR-ElenaNeural", "es-ES-ElviraNeural", "es-MX-DaliaNeural",
              "es-CO-SalomeNeural"]
    for i, s in enumerate(SENTENCES):
        if i < have:
            continue
        raw = os.path.join(CACHE, f"tech_{i:02d}.mp3")
        asyncio.run(_tts(s, raw, voices[i % len(voices)]))
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", raw,
             "-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"],
            capture_output=True)
        wav = np.frombuffer(p.stdout, dtype=np.float32)
        wav = _degrade(wav, seed=1000 + i)
        pcm = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(os.path.join(CACHE, f"tech_{i:02d}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        with open(refs_path, "a", encoding="utf-8") as f:
            f.write(s.replace("\n", " ") + "\n")


def main():
    import wave

    from instant2_app import bias as _bias
    from instant2_app.engine import Engine

    build_cache()
    refs_path = os.path.join(CACHE, "refs.txt")
    if not os.path.isfile(refs_path):
        print("BIASBENCH sin cache (refs.txt ausente): corre con --regen "
              "o conexion para TTS; nada que medir.", flush=True)
        return
    with open(refs_path, encoding="utf-8") as f:
        refs = [line.strip() for line in f if line.strip()]
    if not refs:
        print("BIASBENCH cache vacia (0 refs): nada que medir.", flush=True)
        return
    wavs = []
    for i in range(len(refs)):
        with wave.open(os.path.join(CACHE, f"tech_{i:02d}.wav"), "rb") as w:
            n = w.getnframes()
            wav = np.frombuffer(w.readframes(n),
                                dtype=np.int16).astype(np.float32) / 32768.0
        wavs.append(np.ascontiguousarray(wav, dtype=np.float32))
    eng = Engine(data_dir=resolve_data_dir(), threads=4)
    eng.recognizer()
    eng.vad()
    # Una sola pasada de transcribe por wav: baseline guarda el texto y la
    # confianza; bias reutiliza ese mismo texto (sin doble pasada).
    base, confs, wconfs = [], [], []
    for w in wavs:
        base.append(eng.transcribe(w))
        confs.append(eng.last_conf)
        wconfs.append(list(eng.last_word_confs))
    for name in ("baseline", "bias"):
        if name == "baseline":
            hyps = list(base)
        else:
            hyps = [_bias.correct_biased(h, conf=c, word_confs=wc)
                    for h, c, wc in zip(base, confs, wconfs)]
        wers = [_wer(_norm_es(r), _norm_es(h))
                for r, h in zip(refs, hyps)]
        print(f"BIASBENCH {name}: n={len(wavs)} "
              f"wer={float(np.mean(wers)):.3f} "
              f"conf_media={float(np.mean(confs)):.2f}", flush=True)
        bad = [(r, h) for r, h in zip(refs, hyps)
               if _wer(_norm_es(r), _norm_es(h)) > 0.3][:5]
        for r, h in bad:
            print(f"  ref={r[:60]!r}\n  hyp={h[:60]!r}", flush=True)


if __name__ == "__main__":
    main()
