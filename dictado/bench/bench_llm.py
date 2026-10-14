"""Compare configured OpenAI-compatible LLMs on safe synthetic Spanish text.

No audio, user history, or dictation logs are used. By default only aggregate
metrics are printed. Pass --report to save per-case outputs for local review.

Example:
    python bench/bench_llm.py --url http://127.0.0.1:11434 --model qwen3.5:2b-q4_K_M
"""
import argparse
import json
import math
import os
import statistics
import sys
import time
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import llm  # noqa: E402

CASES = os.path.join(os.path.dirname(__file__), "llm_cases.json")


def _canonical(text):
    return " ".join(unicodedata.normalize("NFC", (text or "")).split()).casefold()


def _char_error_rate(reference, hypothesis):
    ref, hyp = _canonical(reference), _canonical(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, char in enumerate(ref, start=1):
        current = [i]
        for j, other in enumerate(hyp, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (0 if char == other else 1)))
        previous = current
    return previous[-1] / len(ref)


def _contains_anchor(text, anchor):
    # Keep accents: "prioriza" and "priorizá" encode different persons.
    # Punctuation and capitalization still do not affect literal anchors.
    phrase = " ".join(llm._surface_words(anchor))
    words = " ".join(llm._surface_words(text or ""))
    return phrase in words


def load_cases():
    with open(CASES, encoding="utf-8") as handle:
        return json.load(handle)


def run_normal(cases, cfg, timeout, sampling=None):
    results = []
    latencies = []
    for case in cases:
        started = time.perf_counter()
        local = llm.correct_locally(case["input"], cfg)
        candidate = llm.polish_candidate(
            local, cfg["llm_url"], timeout=timeout, cfg=cfg,
            sampling_params=sampling)
        meaning_check = llm.candidate_preserves_meaning(
            local, candidate, cfg["llm_url"], timeout=2.0, cfg=cfg)
        elapsed = time.perf_counter() - started
        # Simula la ruta de producción sin cargar perfiles/glosarios personales:
        # el LLM puede corregir palabras, pero cambios léxicos o de tildes
        # requieren una confirmación conservadora del mismo modelo.
        accepted = candidate if meaning_check else local
        must_keep = case.get("must_keep", [])
        preserved = [anchor for anchor in must_keep
                     if _contains_anchor(accepted, anchor)]
        latencies.append(elapsed)
        results.append({
            "id": case["id"],
            "candidate": candidate,
            "accepted": accepted,
            "meaning_check_passed": meaning_check,
            "word_sequence_changed": (
                llm._words(accepted) != llm._words(local)),
            "candidate_word_sequence_changed": (
                llm._words(candidate) != llm._words(local)),
            "must_keep": must_keep,
            "preserved": preserved,
            "missing": [anchor for anchor in must_keep
                        if anchor not in preserved],
            "exact_match": _canonical(accepted) == _canonical(case["expected"]),
            "baseline_exact_match": _canonical(case["input"]) == _canonical(case["expected"]),
            "baseline_cer": _char_error_rate(case["expected"], case["input"]),
            "cer": _char_error_rate(case["expected"], accepted),
            "latency_seconds": elapsed,
        })
    return results, latencies


def _latency_summary(values):
    if not values:
        return {"count": 0, "first_request_seconds": None,
                "warm_p50_seconds": None, "warm_p95_seconds": None}
    ordered = sorted(values)
    warm = sorted(values[1:]) or ordered
    p95_index = max(0, min(len(warm) - 1, math.ceil(0.95 * len(warm)) - 1))
    return {
        "count": len(values),
        "first_request_seconds": values[0],
        "warm_p50_seconds": statistics.median(warm),
        "warm_p95_seconds": warm[p95_index],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="OpenAI-compatible base URL")
    parser.add_argument("--model", required=True, help="Exact model name on the server")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--sampling-profile", choices=("production", "qwen-instruct"),
        default="production",
        help=("production usa temperatura 0; qwen-instruct prueba las "
              "recomendaciones generales de Qwen3.5 sin cambiar la app"))
    parser.add_argument("--report", help="Optional local JSON report with synthetic case outputs")
    args = parser.parse_args()
    cfg = {"llm_url": args.url, "llm_model": args.model}
    cases = load_cases()
    sampling = ({"temperature": 0.7, "top_p": 0.8, "top_k": 20,
                 "presence_penalty": 1.5}
                if args.sampling_profile == "qwen-instruct" else None)
    report = {
        "model": args.model,
        "endpoint_kind": llm.service_info(cfg)["kind"],
        "sampling_profile": args.sampling_profile,
    }

    normal, latencies = run_normal(
        cases["normal"], cfg, args.timeout, sampling=sampling)
    normal_anchor_total = sum(len(item["must_keep"]) for item in normal)
    normal_anchor_kept = sum(len(item["preserved"]) for item in normal)
    report["normal"] = {
        "metrics": {
            "exact_match_rate": sum(item["exact_match"] for item in normal) / len(normal),
            "baseline_exact_match_rate": sum(
                item["baseline_exact_match"] for item in normal) / len(normal),
            "word_sequence_changed_rate": sum(
                item["word_sequence_changed"] for item in normal) / len(normal),
            "candidate_rejection_rate": sum(
                not item["meaning_check_passed"] for item in normal) / len(normal),
            "required_anchor_preservation_rate": (
                normal_anchor_kept / normal_anchor_total
                if normal_anchor_total else 1.0),
            "baseline_character_error_rate": statistics.mean(
                item["baseline_cer"] for item in normal),
            "mean_character_error_rate": statistics.mean(item["cer"] for item in normal),
            "character_error_rate_reduction": statistics.mean(
                item["baseline_cer"] - item["cer"] for item in normal),
            "latency": _latency_summary(latencies),
        },
        "cases": normal,
    }
    m = report["normal"]["metrics"]
    print("Dictado mejorado: "
          f"exactitud={m['exact_match_rate']:.0%} "
          f"(baseline {m['baseline_exact_match_rate']:.0%}), "
          f"propuestas que cambian palabras="
          f"{m['word_sequence_changed_rate']:.0%}, "
          f"propuestas rechazadas="
          f"{m['candidate_rejection_rate']:.0%}, "
          f"anclas conservadas={m['required_anchor_preservation_rate']:.0%}, "
          f"CER={m['baseline_character_error_rate']:.3f} -> "
          f"{m['mean_character_error_rate']:.3f} "
          f"(cambio {m['character_error_rate_reduction']:+.3f}), "
          f"primera={m['latency']['first_request_seconds']:.3f}s, "
          f"caliente p50={m['latency']['warm_p50_seconds']:.3f}s, "
          f"p95={m['latency']['warm_p95_seconds']:.3f}s")

    print(f"Modelo: {args.model} · servicio: {report['endpoint_kind']} · "
          f"muestreo: {args.sampling_profile} · "
          "la primera solicitud se mide aparte; la carga del modelo se mide al iniciar el servidor.")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"Detalle local guardado en: {args.report}")


if __name__ == "__main__":
    main()
