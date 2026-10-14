"""Bench de robustez con modelos reales. Solo mide, no cambia nada.

Uso (2-4 min, necesita los modelos descargados):
    python dictado/bench/bench_numbers.py

Mide: determinismo run-to-run, RTF, A/B del fallback en cortes abruptos y
bordes VAD vs ground truth en senal silabica sintetica.
OJO: los tonos sinteticos no son voz; sirven para estabilidad/segmentacion,
no para afirmar WER. La precision real se mide con wavs de fallo
(INSTANT2_SAVE_WAVS) cuando existan.
"""
import os
import statistics as st
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app.engine import Engine, SAMPLE_RATE  # noqa: E402
from instant2_app.paths import resolve_data_dir  # noqa: E402

SR = SAMPLE_RATE


def vowel(f0, dur, amp=0.2, seed=0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    x = (np.sin(2 * np.pi * f0 * t + float(rng.random()))
         + 0.5 * np.sin(2 * np.pi * f0 * 2 * t)
         + 0.25 * np.sin(2 * np.pi * f0 * 3.02 * t)
         + 0.02 * rng.standard_normal(n))
    r = int(0.015 * SR)
    env = np.ones(n)
    env[:r] = np.linspace(0, 1, r)
    env[-r:] = np.linspace(1, 0, r)
    return (x * env * amp / np.max(np.abs(x))).astype(np.float32)


def silence(dur):
    return np.zeros(int(SR * dur), dtype=np.float32)


class TEngine(Engine):
    def __init__(self, *a, thr=0.5, minsp=0.2, **k):
        super().__init__(*a, **k)
        self._thr = thr
        self._minsp = minsp

    def vad(self):
        import sherpa_onnx
        cfg = sherpa_onnx.VadModelConfig()
        if (self.vad_model or "silero") == "ten":
            cfg.ten_vad.model = self.paths["ten_vad"]
            cfg.ten_vad.threshold = self._thr
            cfg.ten_vad.min_silence_duration = self.vad_sil
            cfg.ten_vad.min_speech_duration = self._minsp
            cfg.ten_vad.window_size = 256
        else:
            cfg.silero_vad.model = self.paths["vad"]
            cfg.silero_vad.threshold = self._thr
            cfg.silero_vad.min_silence_duration = self.vad_sil
            cfg.silero_vad.min_speech_duration = self._minsp
            cfg.silero_vad.window_size = 512
        cfg.sample_rate = SR
        return sherpa_onnx.VadModel.create(cfg)


def main():
    data = resolve_data_dir()
    print("== carga engine ==", flush=True)
    t0 = time.time()
    eng = Engine(data_dir=data, threads=2)
    eng.recognizer()
    eng.vad()
    print(f"load {time.time() - t0:.1f}s", flush=True)

    wav6 = np.concatenate([
        vowel(130, 0.9, seed=1), silence(0.4), vowel(165, 1.1, seed=2),
        silence(0.5), vowel(110, 0.8, seed=3), silence(0.3),
        vowel(196, 1.2, seed=4),
    ])
    outs, dts = [], []
    for _ in range(6):
        a = time.time()
        outs.append(eng.transcribe(wav6))
        dts.append(time.time() - a)
    print(f"DET determinismo: {len(set(outs))}/6 distintos", flush=True)
    print(f"DET RTF medio: {sum(dts) / len(dts) / (len(wav6) / SR):.3f}x",
          flush=True)

    rng = np.random.default_rng(7)
    variants = []
    for i in range(12):
        f0 = float(rng.choice([110, 130, 165, 196, 220]))
        d = float(rng.uniform(0.4, 1.2))
        v = vowel(f0, d, amp=float(rng.uniform(0.05, 0.25)), seed=100 + i)
        if i % 2 == 0:
            v = v[int(0.03 * SR):]
        variants.append(np.concatenate([silence(0.2), v, silence(0.2)]))

    old_nonempty = new_nonempty = 0
    orig = eng._recover_empties
    for v in variants:
        eng._recover_empties = lambda w, p, b, c, t, cf=None, wc=None: t
        if eng.transcribe(v).strip():
            old_nonempty += 1
        eng._recover_empties = orig
        if eng.transcribe(v).strip():
            new_nonempty += 1
    print(f"AB fallback nonempty: vieja={old_nonempty}/12 nueva={new_nonempty}/12",
          flush=True)

    starts, gt, parts = 0.5, [], []
    for i, (f0, d, gap) in enumerate(
            [(130, 0.7, 0.5), (165, 0.5, 0.8), (110, 0.9, 0.4),
             (196, 0.4, 0.6), (147, 0.8, 0.5), (220, 0.6, 0.7)]):
        parts.append(silence(starts - sum(len(p) / SR for p in parts)))
        gt.append((starts, starts + d))
        parts.append(vowel(f0, d, seed=50 + i))
        starts += d + gap
    sig = np.concatenate(parts).astype(np.float32)

    import os as _os
    for vad_name in ("silero", "ten"):
        if vad_name == "ten" and not _os.path.isfile(
                os.path.join(data, "silero-vad", "ten_vad.onnx")):
            print("VAD ten: SKIP (falta ten_vad.onnx, `instant2 setup --vad-model ten`)",
                  flush=True)
            continue
        for thr in (0.3, 0.5, 0.7):
            e = TEngine(data_dir=data, threads=2, thr=thr, vad_model=vad_name)
            e._rec = eng._rec
            bounds = e.segment(sig)
            errs = []
            for gs, ge in gt:
                det = min(bounds, key=lambda b: abs(b[0] - gs))
                errs.append((det[0] - gs, det[1] - ge))
            on = [x * 1000 for x, _ in errs]
            off = [x * 1000 for _, x in errs]
            print(f"VAD {vad_name} thr={thr}: nsegs={len(bounds)} "
                  f"onset={st.mean(on):+.0f}ms±{st.pstdev(on):.0f} "
                  f"offset={st.mean(off):+.0f}ms±{st.pstdev(off):.0f}", flush=True)

    print("BENCH OK", flush=True)


if __name__ == "__main__":
    main()
