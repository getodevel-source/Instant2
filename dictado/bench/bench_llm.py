"""Compare configured OpenAI-compatible LLMs on safe synthetic Spanish text.

No audio, user history, or dictation logs are used. By default only aggregate
metrics are printed. Pass --report to save per-case outputs for local review.

Example:
    python bench/bench_llm.py --url http://127.0.0.1:11434 --model qwen3.5:2b-q4_K_M
"""
import argparse
import hashlib
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

def _word_error_rate(reference, hypothesis):
    """Levenshtein WER over accent-insensitive, punctuation-free word tokens."""
    ref, hyp = llm._words(reference), llm._words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, token in enumerate(ref, start=1):
        current = [i]
        for j, other in enumerate(hyp, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (0 if token == other else 1)))
        previous = current
    return previous[-1] / len(ref)


def _contains_anchor(text, anchor):
    # Keep accents: "prioriza" and "priorizá" encode different persons.
    # Punctuation and capitalization still do not affect literal anchors.
    phrase = " ".join(llm._surface_words(anchor))
    words = " ".join(llm._surface_words(text or ""))
    return phrase in words


def load_cases(path=CASES):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _mean(values):
    values = list(values)
    return statistics.mean(values) if values else 0.0


def _aggregate_metrics(cases):
    baseline_cer = _mean(item["baseline_cer"] for item in cases)
    mean_cer = _mean(item["cer"] for item in cases)
    baseline_wer = _mean(item["baseline_wer"] for item in cases)
    mean_wer = _mean(item["wer"] for item in cases)
    no_change_cases = [item for item in cases if item.get("no_change")]
    content_cases = [item for item in cases if item["baseline_content_error"]]
    baseline_content_wer = _mean(
        item["baseline_wer"] for item in content_cases)
    accepted_content_wer = _mean(item["wer"] for item in content_cases)
    return {
        "case_count": len(cases),
        "exact_match_rate": _mean(item["exact_match"] for item in cases),
        "baseline_exact_match_rate": _mean(
            item["baseline_exact_match"] for item in cases),
        "content_exact_match_rate": _mean(
            item["content_exact_match"] for item in cases),
        "baseline_content_exact_match_rate": _mean(
            item["baseline_content_exact_match"] for item in cases),
        "word_sequence_changed_rate": _mean(
            item["word_sequence_changed"] for item in cases),
        "verifier_rejections": sum(
            not item["meaning_check_passed"] for item in cases),
        "verifier_rejection_rate": _mean(
            not item["meaning_check_passed"] for item in cases),
        "critical_invariant_violations": sum(
            len(item["missing"]) for item in cases),
        "cases_with_critical_invariant_violations": sum(
            bool(item["missing"]) for item in cases),
        "harmful_lexical_edits": sum(item["harmful_lexical_edit"] for item in cases),
        "no_change_lexical_edits": sum(
            item["no_change_lexical_edit"] for item in no_change_cases),
        "no_change_case_count": len(no_change_cases),
        "baseline_character_error_rate": baseline_cer,
        "mean_character_error_rate": mean_cer,
        "character_error_rate_reduction": baseline_cer - mean_cer,
        "baseline_word_error_rate": baseline_wer,
        "mean_word_error_rate": mean_wer,
        "word_error_rate_reduction": baseline_wer - mean_wer,
        "baseline_content_error_case_count": len(content_cases),
        "baseline_content_mean_wer": baseline_content_wer,
        "accepted_content_mean_wer": accepted_content_wer,
        "baseline_content_wer_reduction": (
            baseline_content_wer - accepted_content_wer),
        "baseline_content_token_exact_match_rate": _mean(
            item["baseline_content_exact_match"] for item in content_cases),
        "accepted_content_token_exact_match_rate": _mean(
            item["content_exact_match"] for item in content_cases),
    }


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
        accepted = candidate if meaning_check else local
        must_keep = case.get("must_keep", [])
        preserved = [anchor for anchor in must_keep
                     if _contains_anchor(accepted, anchor)]
        baseline_cer = _char_error_rate(case["expected"], case["input"])
        cer = _char_error_rate(case["expected"], accepted)
        baseline_wer = _word_error_rate(case["expected"], case["input"])
        wer = _word_error_rate(case["expected"], accepted)
        expected_words = llm._words(case["expected"])
        input_words = llm._words(case["input"])
        accepted_words = llm._words(accepted)
        accepted_word_sequence_changed = accepted_words != input_words
        latencies.append(elapsed)
        results.append({
            "id": case["id"],
            "candidate": candidate,
            "accepted": accepted,
            "meaning_check_passed": meaning_check,
            "word_sequence_changed": accepted_words != llm._words(local),
            "candidate_word_sequence_changed": (
                llm._words(candidate) != llm._words(local)),
            "accepted_word_sequence_changed_from_input": accepted_word_sequence_changed,
            "must_keep": must_keep,
            "preserved": preserved,
            "missing": [anchor for anchor in must_keep
                        if anchor not in preserved],
            "no_change": bool(case.get("no_change")),
            "no_change_lexical_edit": (
                bool(case.get("no_change")) and accepted_word_sequence_changed),
            "harmful_lexical_edit": (
                accepted_word_sequence_changed and cer > baseline_cer),
            "exact_match": _canonical(accepted) == _canonical(case["expected"]),
            "baseline_exact_match": _canonical(case["input"]) == _canonical(case["expected"]),
            "content_exact_match": accepted_words == expected_words,
            "baseline_content_exact_match": input_words == expected_words,
            "baseline_content_error": baseline_wer > 0,
            "baseline_cer": baseline_cer,
            "cer": cer,
            "baseline_wer": baseline_wer,
            "wer": wer,
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
    parser.add_argument("--corpus", default=CASES,
                        help="Explicit JSON corpus path (default: built-in 20-case corpus)")
    parser.add_argument(
        "--sampling-profile",
        choices=("production", "matched-deterministic", "qwen-instruct",
                 "bonsai-nonthinking"),
        default="production",
        help=("matched-deterministic: temp=0, top_p=1, top_k=0, min_p=0, "
              "presence_penalty=0, repetition_penalty=1; bonsai-nonthinking: "
              "official non-thinking values, with thinking disabled"))
    parser.add_argument("--case-ids", help="Comma-separated case IDs to run (optional subset)")
    parser.add_argument("--report", help="Optional local JSON report with synthetic case outputs")
    args = parser.parse_args()
    cfg = {"llm_url": args.url, "llm_model": args.model}
    cases = load_cases(args.corpus)
    all_case_ids = {case["id"] for case in cases["normal"]}
    if args.case_ids:
        requested_ids = {value.strip() for value in args.case_ids.split(",") if value.strip()}
        unknown_ids = requested_ids - all_case_ids
        if unknown_ids:
            parser.error("unknown case IDs: " + ", ".join(sorted(unknown_ids)))
        cases["normal"] = [
            case for case in cases["normal"] if case["id"] in requested_ids]
    sampling_profiles = {
        "production": {"temperature": 0.0, "min_p": 0.0,
                       "repetition_penalty": 1.0},
        "matched-deterministic": {
            "temperature": 0.0, "top_p": 1.0, "top_k": 0, "min_p": 0.0,
            "presence_penalty": 0.0, "repetition_penalty": 1.0},
        "qwen-instruct": {"temperature": 0.7, "top_p": 0.8, "top_k": 20,
                          "presence_penalty": 1.5, "min_p": 0.0,
                          "repetition_penalty": 1.0},
        "bonsai-nonthinking": {"temperature": 0.7, "top_p": 0.80,
                               "top_k": 20, "min_p": 0.0,
                               "presence_penalty": 1.5,
                               "repetition_penalty": 1.0},
    }
    sampling = sampling_profiles[args.sampling_profile]
    with open(args.corpus, "rb") as handle:
        corpus_sha256 = hashlib.sha256(handle.read()).hexdigest()
    report = {
        "model": args.model,
        "endpoint_kind": llm.service_info(cfg)["kind"],
        "sampling_profile": args.sampling_profile,
        "sampling_params": sampling,
        "candidate_request": {
            "sampling_params": sampling,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        "verifier_request": {
            "sampling_overrides": {"temperature": 0.0},
            "chat_template_kwargs": {"enable_thinking": False},
            "max_tokens": 8,
            "other_sampler_values": "inherited from identical server flags below; verifier does not send top_p/top_k/min_p/penalties",
        },
        "server_sampling_params": sampling,
        "system_prompt_sha256": hashlib.sha256(
            llm.DEFAULT_SYSTEM.encode("utf-8")).hexdigest(),
        "verify_prompt_sha256": hashlib.sha256(
            llm.VERIFY_SYSTEM.encode("utf-8")).hexdigest(),
        "corpus_path": os.path.abspath(args.corpus),
        "corpus_sha256": corpus_sha256,
        "case_ids": [case["id"] for case in cases["normal"]],
    }

    normal, latencies = run_normal(
        cases["normal"], cfg, args.timeout, sampling=sampling)
    metrics = _aggregate_metrics(normal)
    anchor_total = sum(len(item["must_keep"]) for item in normal)
    anchor_kept = sum(len(item["preserved"]) for item in normal)
    metrics["required_anchor_preservation_rate"] = (
        anchor_kept / anchor_total if anchor_total else 1.0)
    metrics["latency"] = _latency_summary(latencies)
    report["normal"] = {"metrics": metrics, "cases": normal}
    m = metrics
    print("Dictado mejorado: "
          f"exactitud={m['exact_match_rate']:.0%} "
          f"(baseline {m['baseline_exact_match_rate']:.0%}), "
          f"propuestas que cambian palabras="
          f"{m['word_sequence_changed_rate']:.0%}, "
          f"rechazos={m['verifier_rejections']}, "
          f"invariantes críticos violados={m['critical_invariant_violations']}, "
          f"CER={m['baseline_character_error_rate']:.3f} -> "
          f"{m['mean_character_error_rate']:.3f} "
          f"(cambio {m['character_error_rate_reduction']:+.3f}), "
          f"WER={m['baseline_word_error_rate']:.3f} -> "
          f"{m['mean_word_error_rate']:.3f} "
          f"(contenido baseline={m['baseline_content_error_case_count']}, "
          f"reducción WER={m['baseline_content_wer_reduction']:+.3f}), "
          f"no-change lexical edits={m['no_change_lexical_edits']}, "
          f"harmful lexical edits={m['harmful_lexical_edits']}, "
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
