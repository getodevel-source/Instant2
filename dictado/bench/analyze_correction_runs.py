"""Paired, reference-based analysis for synthetic correction benchmark runs."""
import argparse
import json
import math
import os
import random
import statistics

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 211


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _bootstrap_ci(differences, seed=BOOTSTRAP_SEED,
                  resamples=BOOTSTRAP_RESAMPLES):
    values = list(differences)
    if not values:
        return {"mean": None, "low": None, "high": None, "n": 0}
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(resamples):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return {
        "mean": statistics.mean(values),
        "low": means[max(0, math.floor(0.025 * resamples))],
        "high": means[min(resamples - 1, math.ceil(0.975 * resamples) - 1)],
        "n": n,
        "resamples": resamples,
        "seed": seed,
    }


def _model_neutral_server_command(command):
    normalized = []
    skip_next = False
    for argument in command:
        if skip_next:
            skip_next = False
            continue
        if argument in ("-m", "--alias"):
            normalized.append(argument)
            skip_next = True
        else:
            normalized.append(argument)
    return normalized

def _paired(qwen_report, bonsai_report):
    for key in ("corpus_sha256", "sampling_profile", "sampling_params",
                "candidate_request", "verifier_request", "server_sampling_params",
                "system_prompt_sha256", "verify_prompt_sha256"):
        if not qwen_report.get(key) or qwen_report.get(key) != bonsai_report.get(key):
            raise ValueError(f"paired reports differ or omit {key}")
    if qwen_report.get("case_ids") != bonsai_report.get("case_ids"):
        raise ValueError("paired reports have different case ID declarations")
    q_runtime, b_runtime = qwen_report.get("runtime"), bonsai_report.get("runtime")
    if not q_runtime or not b_runtime:
        raise ValueError("paired reports are missing runtime build metadata")
    for key in ("server_build", "server_binary_sha256", "port"):
        if not q_runtime.get(key) or q_runtime.get(key) != b_runtime.get(key):
            raise ValueError(f"paired runtimes differ or omit {key}")
    if _model_neutral_server_command(q_runtime.get("server_command", [])) != \
            _model_neutral_server_command(b_runtime.get("server_command", [])):
        raise ValueError("paired server flags differ")
    qwen_cases = {case["id"]: case for case in qwen_report["normal"]["cases"]}
    bonsai_cases = {case["id"]: case for case in bonsai_report["normal"]["cases"]}
    if qwen_cases.keys() != bonsai_cases.keys():
        raise ValueError("paired reports do not contain identical case IDs")
    rows = []
    for case_id in qwen_cases:
        q, b = qwen_cases[case_id], bonsai_cases[case_id]
        if q["baseline_wer"] != b["baseline_wer"]:
            raise ValueError(f"baseline mismatch for {case_id}")
        rows.append({"id": case_id, "qwen": q, "bonsai": b})
    return rows


def compare_reports(qwen_report, bonsai_report):
    rows = _paired(qwen_report, bonsai_report)
    content_rows = [row for row in rows if row["qwen"]["baseline_content_error"]]
    metrics = {}
    metric_specs = {
        "cer_gain_qwen": (rows, lambda q, b: b["cer"] - q["cer"]),
        "character_exact_gain_qwen": (
            rows, lambda q, b: float(q["exact_match"]) - float(b["exact_match"])),
        "normalized_token_wer_gain_qwen": (
            rows, lambda q, b: b["wer"] - q["wer"]),
        "normalized_token_exact_gain_qwen": (
            rows, lambda q, b: float(q["content_exact_match"])
            - float(b["content_exact_match"])),
        "baseline_content_wer_gain_qwen": (
            content_rows, lambda q, b: b["wer"] - q["wer"]),
        "baseline_content_token_exact_gain_qwen": (
            content_rows, lambda q, b: float(q["content_exact_match"])
            - float(b["content_exact_match"])),
    }
    for name, (selected, difference) in metric_specs.items():
        metrics[name] = _bootstrap_ci(
            [difference(row["qwen"], row["bonsai"]) for row in selected])
    q_invariant = qwen_report["normal"]["metrics"]["critical_invariant_violations"]
    b_invariant = bonsai_report["normal"]["metrics"]["critical_invariant_violations"]
    q_nochange = qwen_report["normal"]["metrics"]["no_change_lexical_edits"]
    b_nochange = bonsai_report["normal"]["metrics"]["no_change_lexical_edits"]
    q_harmful = qwen_report["normal"]["metrics"]["harmful_lexical_edits"]
    b_harmful = bonsai_report["normal"]["metrics"]["harmful_lexical_edits"]

    def direction(item):
        if item["low"] is not None and item["low"] > 0:
            return "qwen"
        if item["high"] is not None and item["high"] < 0:
            return "bonsai"
        return "tie"

    directions = {name: direction(value) for name, value in metrics.items()}
    return {
        "qwen_model": qwen_report["model"],
        "bonsai_model": bonsai_report["model"],
        "case_count": len(rows),
        "baseline_content_error_case_count": len(content_rows),
        "paired_bootstrap_ci": metrics,
        "metric_directions": directions,
        "critical_invariant_violations": {
            "qwen": q_invariant, "bonsai": b_invariant},
        "no_change_lexical_edits": {
            "qwen": q_nochange, "bonsai": b_nochange},
        "harmful_lexical_edits": {"qwen": q_harmful, "bonsai": b_harmful},
        "paired_cases": [{
            "id": row["id"],
            "baseline_content_error": row["qwen"]["baseline_content_error"],
            "baseline": {
                "cer": row["qwen"]["baseline_cer"],
                "wer": row["qwen"]["baseline_wer"],
            },
            "qwen": {key: row["qwen"][key] for key in (
                "accepted", "candidate", "cer", "wer", "exact_match",
                "content_exact_match", "missing", "meaning_check_passed",
                "no_change_lexical_edit", "harmful_lexical_edit",
                "latency_seconds")},
            "bonsai": {key: row["bonsai"][key] for key in (
                "accepted", "candidate", "cer", "wer", "exact_match",
                "content_exact_match", "missing", "meaning_check_passed",
                "no_change_lexical_edit", "harmful_lexical_edit",
                "latency_seconds")},
        } for row in rows],
    }


def _determinism_comparison(primary, repeat):
    for key in ("corpus_sha256", "sampling_profile", "sampling_params",
                "system_prompt_sha256", "verify_prompt_sha256"):
        if primary.get(key) != repeat.get(key):
            raise ValueError(f"determinism reports differ in {key}")
    primary_runtime, repeat_runtime = primary.get("runtime"), repeat.get("runtime")
    if not primary_runtime or not repeat_runtime:
        raise ValueError("determinism reports are missing runtime metadata")
    for key in ("server_build", "server_binary_sha256", "port"):
        if primary_runtime.get(key) != repeat_runtime.get(key):
            raise ValueError(f"determinism runtime differs in {key}")
    if _model_neutral_server_command(
            primary_runtime.get("server_command", [])) != \
            _model_neutral_server_command(repeat_runtime.get("server_command", [])):
        raise ValueError("determinism server flags differ")
    primary_cases = {case["id"]: case for case in primary["normal"]["cases"]}
    repeat_cases = {case["id"]: case for case in repeat["normal"]["cases"]}
    if not repeat_cases.keys() <= primary_cases.keys():
        raise ValueError("determinism report has case IDs absent from primary run")
    differences = [case_id for case_id, case in repeat_cases.items()
                   if (case["accepted"] != primary_cases[case_id]["accepted"]
                       or case["candidate"] != primary_cases[case_id]["candidate"])]
    return {
        "case_count": len(repeat_cases),
        "candidate_or_accepted_output_differences": differences,
        "deterministic_on_recheck": not differences,
    }


def analyze(qwen_primary_path, bonsai_primary_path, official_qwen_paths,
            official_bonsai_paths, qwen_recheck_path=None,
            bonsai_recheck_path=None):
    if len(official_qwen_paths) != 3 or len(official_bonsai_paths) != 3:
        raise ValueError("exactly three official-sampler reports per model are required")
    q_primary, b_primary = _load(qwen_primary_path), _load(bonsai_primary_path)
    primary = compare_reports(q_primary, b_primary)
    official = []
    for index, (q_path, b_path) in enumerate(zip(official_qwen_paths,
                                                 official_bonsai_paths), start=1):
        result = compare_reports(_load(q_path), _load(b_path))
        result["repeat"] = index
        official.append(result)
    primary_direction = _unanimous_direction(primary["metric_directions"])
    official_directions = [
        _unanimous_direction(report["metric_directions"]) for report in official]
    safety_clear = all(
        not values["qwen"] and not values["bonsai"]
        for report in [primary, *official]
        for values in (report["critical_invariant_violations"],
                       report["no_change_lexical_edits"]))
    if primary_direction == "qwen":
        harmful_not_worse = all(
            report["harmful_lexical_edits"]["qwen"] <=
            report["harmful_lexical_edits"]["bonsai"]
            for report in [primary, *official])
    elif primary_direction == "bonsai":
        harmful_not_worse = all(
            report["harmful_lexical_edits"]["bonsai"] <=
            report["harmful_lexical_edits"]["qwen"]
            for report in [primary, *official])
    else:
        harmful_not_worse = False
    same_direction = (primary_direction in ("qwen", "bonsai") and
                      all(value == primary_direction for value in official_directions))
    qwen_recheck = (
        _determinism_comparison(q_primary, _load(qwen_recheck_path))
        if qwen_recheck_path else {
            "case_count": 0, "candidate_or_accepted_output_differences": [],
            "deterministic_on_recheck": False, "error": "missing required recheck"})
    bonsai_recheck = (
        _determinism_comparison(b_primary, _load(bonsai_recheck_path))
        if bonsai_recheck_path else {
            "case_count": 0, "candidate_or_accepted_output_differences": [],
            "deterministic_on_recheck": False, "error": "missing required recheck"})
    determinism_clear = (
        qwen_recheck["deterministic_on_recheck"] and
        bonsai_recheck["deterministic_on_recheck"])
    conclusion = (
        primary_direction
        if safety_clear and harmful_not_worse and same_direction and determinism_clear
        else "inconclusive")
    return {
        "schema": "instant2-correction-paired-analysis/1",
        "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED,
                      "resampling_unit": "case; paired Qwen/Bonsai differences"},
        "primary": primary,
        "official_sampler_repeats": official,
        "official_repeat_directions": official_directions,
        "primary_direction": primary_direction,
        "safety_gate_clear": safety_clear,
        "harmful_edits_not_worse_for_proposed_winner": harmful_not_worse,
        "same_direction_across_samplers_and_repeats": same_direction,
        "determinism_rechecks": {
            "qwen": qwen_recheck, "bonsai": bonsai_recheck},
        "determinism_gate_clear": determinism_clear,
        "conclusion": conclusion,
    }


def _unanimous_direction(directions):
    unique = set(directions.values())
    return next(iter(unique)) if len(unique) == 1 and "tie" not in unique else "inconclusive"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen-primary", required=True)
    parser.add_argument("--bonsai-primary", required=True)
    parser.add_argument("--qwen-official", action="append", required=True)
    parser.add_argument("--bonsai-official", action="append", required=True)
    parser.add_argument("--qwen-recheck", required=True)
    parser.add_argument("--bonsai-recheck", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = analyze(
        args.qwen_primary, args.bonsai_primary,
        args.qwen_official, args.bonsai_official,
        args.qwen_recheck, args.bonsai_recheck)
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"Paired analysis saved: {args.report}")
    print(f"Conclusion: {report['conclusion']}")


if __name__ == "__main__":
    main()
