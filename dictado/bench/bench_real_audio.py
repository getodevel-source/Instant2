"""Evaluación privada de un corpus local de voz real, con consentimiento.

Uso desde ``dictado/`` después de descargar modelos::

    python bench/bench_real_audio.py --manifest bench/private-real-audio.csv

El manifiesto y sus WAV nunca se imprimen ni se suben. La salida solo incluye
métricas agregadas por condición y categoría de micrófono. No guardar el
manifiesto, WAV ni resultados con datos personales en Git.
"""
import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SAMPLE_RATE = 16000
_FIELDS = {"path", "reference", "condition", "microphone", "speaker_id",
           "consent"}
_YES = {"1", "true", "yes", "y", "si", "sí"}


def _norm_es(text):
    text = unicodedata.normalize("NFD", (text or "").casefold())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _edit_counts(reference, hypothesis):
    """Devuelve sustituciones, borrados e inserciones con alineación estable."""
    ref, hyp = _norm_es(reference).split(), _norm_es(hypothesis).split()
    rows, cols = len(ref) + 1, len(hyp) + 1
    costs = [[0] * cols for _ in range(rows)]
    ops = [[None] * cols for _ in range(rows)]
    for i in range(1, rows):
        costs[i][0], ops[i][0] = i, "D"
    for j in range(1, cols):
        costs[0][j], ops[0][j] = j, "I"
    for i in range(1, rows):
        for j in range(1, cols):
            same = ref[i - 1] == hyp[j - 1]
            candidates = [
                (costs[i - 1][j - 1] + (not same), 0 if same else 1,
                 "M" if same else "S"),
                (costs[i - 1][j] + 1, 2, "D"),
                (costs[i][j - 1] + 1, 3, "I"),
            ]
            costs[i][j], _priority, ops[i][j] = min(candidates)
    counts = {"substitutions": 0, "deletions": 0, "insertions": 0}
    i, j = len(ref), len(hyp)
    mapping = {"S": "substitutions", "D": "deletions", "I": "insertions"}
    while i or j:
        op = ops[i][j]
        if op in mapping:
            counts[mapping[op]] += 1
        if op in ("M", "S"):
            i, j = i - 1, j - 1
        elif op == "D":
            i -= 1
        elif op == "I":
            j -= 1
    counts["reference_words"] = len(ref)
    counts["errors"] = costs[-1][-1]
    return counts


def read_manifest(path):
    """Lee y valida el CSV sin mostrar referencias, IDs ni rutas."""
    manifest = os.path.abspath(os.path.expanduser(path))
    base = os.path.dirname(manifest)
    records = []
    with open(manifest, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        missing = sorted(_FIELDS - fields)
        if missing:
            raise ValueError("faltan columnas requeridas: " + ", ".join(missing))
        for line, row in enumerate(reader, start=2):
            if (row.get("consent") or "").strip().casefold() not in _YES:
                raise ValueError(f"fila {line}: requiere consentimiento afirmativo")
            reference = (row.get("reference") or "").strip()
            speaker = (row.get("speaker_id") or "").strip()
            condition = (row.get("condition") or "").strip()
            microphone = (row.get("microphone") or "").strip()
            audio_path = (row.get("path") or "").strip()
            if not all((reference, speaker, condition, microphone, audio_path)):
                raise ValueError(f"fila {line}: hay campos vacíos")
            if not _norm_es(reference):
                raise ValueError(f"fila {line}: la referencia no contiene palabras")
            if not os.path.isabs(audio_path):
                audio_path = os.path.join(base, audio_path)
            if not os.path.isfile(audio_path):
                raise ValueError(f"fila {line}: no se encuentra el WAV indicado")
            records.append({
                "path": audio_path,
                "reference": reference,
                "speaker_id": speaker,
                "condition": condition,
                "microphone": microphone,
                "line": line,
            })
    if not records:
        raise ValueError("el manifiesto no contiene clips")
    return records


def read_wav(path):
    """Carga PCM 16-bit/16 kHz mono o estéreo, sin remuestreo oculto."""
    with wave.open(path, "rb") as wav:
        if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2:
            raise ValueError("se requiere WAV PCM de 16 bits")
        if wav.getframerate() != SAMPLE_RATE:
            raise ValueError("se requiere audio a 16000 Hz")
        channels = wav.getnchannels()
        if channels not in (1, 2):
            raise ValueError("se admite audio mono o estéreo")
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    if channels == 2:
        data = data.reshape(-1, 2).astype(np.float32).mean(axis=1)
    else:
        data = data.astype(np.float32)
    return np.ascontiguousarray(data / 32768.0, dtype=np.float32)


def _new_bucket():
    return {
        "clips": 0, "speakers": set(), "empty_raw": 0,
        "empty_corrected": 0, "bias_changed": 0,
        "bias_regressions": 0,
        "raw": {"errors": 0, "reference_words": 0, "substitutions": 0,
                "deletions": 0, "insertions": 0},
        "corrected": {"errors": 0, "reference_words": 0,
                      "substitutions": 0, "deletions": 0, "insertions": 0},
    }


def _add_count(target, source):
    for key in target:
        target[key] += source[key]


def _public_bucket(bucket):
    def score(name):
        counts = bucket[name]
        words = counts["reference_words"]
        return {
            **counts,
            "wer": round(counts["errors"] / words, 6) if words else None,
        }
    return {
        "clips": bucket["clips"],
        "speakers": len(bucket["speakers"]),
        "empty_raw": bucket["empty_raw"],
        "empty_corrected": bucket["empty_corrected"],
        "bias_changed": bucket["bias_changed"],
        "bias_regressions": bucket["bias_regressions"],
        "raw": score("raw"),
        "corrected": score("corrected"),
    }


def evaluate(records, engine, bias_module=None):
    """Transcribe en memoria; solo devuelve contadores agregados."""
    groups = {"overall": _new_bucket()}
    for record in records:
        keys = ("overall", f"condition:{record['condition']}",
                f"microphone:{record['microphone']}")
        for key in keys:
            groups.setdefault(key, _new_bucket())
        try:
            audio = read_wav(record["path"])
        except (OSError, EOFError, wave.Error, ValueError) as error:
            raise ValueError(
                f"fila {record['line']}: WAV inválido ({error})") from None
        hypothesis = engine.transcribe(audio)
        corrected = (bias_module.correct_biased(
            hypothesis, conf=engine.last_conf) if bias_module else hypothesis)
        raw_counts = _edit_counts(record["reference"], hypothesis)
        corrected_counts = _edit_counts(record["reference"], corrected)
        for key in keys:
            bucket = groups[key]
            bucket["clips"] += 1
            bucket["speakers"].add(record["speaker_id"])
            bucket["empty_raw"] += not bool((hypothesis or "").strip())
            bucket["empty_corrected"] += not bool((corrected or "").strip())
            bucket["bias_changed"] += _norm_es(hypothesis) != _norm_es(corrected)
            bucket["bias_regressions"] += (
                corrected_counts["errors"] > raw_counts["errors"])
            _add_count(bucket["raw"], raw_counts)
            _add_count(bucket["corrected"], corrected_counts)
        del audio, hypothesis, corrected
    return {key: _public_bucket(value) for key, value in sorted(groups.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True,
                        help="CSV privado con clips y consentimiento explícito")
    parser.add_argument("--data-dir", default=None,
                        help="directorio local de modelos Instant")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--json", dest="json_path", default=None,
                        help="guardar únicamente métricas agregadas en este archivo")
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads debe ser mayor que cero")

    records = read_manifest(args.manifest)
    from instant_app import bias
    from instant_app.engine import Engine
    from instant_app.paths import resolve_data_dir

    data_dir = args.data_dir or resolve_data_dir()
    engine = Engine(data_dir=data_dir, threads=args.threads)
    engine.recognizer()
    engine.vad()
    summary = evaluate(records, engine, bias_module=bias)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.json_path:
        destination = os.path.abspath(os.path.expanduser(args.json_path))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
            handle.write("\n")


if __name__ == "__main__":
    main()
