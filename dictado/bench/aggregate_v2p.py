"""Aggregate Voice-to-Prompt runs without scoring quality.

Reads a results tree of ``<model>/rep*/rows.jsonl`` plus ``metadata.json`` and
reports, per condition (``strategy|context_mode``):

- validation of the frozen contract (corpus, rubric, harness revision),
  the expected unique cases per condition (72 by default), repeat count, duplicate
  condition rows and a consistent condition set across repeats;
  smaller ``--expect-cases`` values are exploratory subsets and cannot establish
  full-corpus viability or a winner;
- repeat-level median/mean/range of the automatic checks (anchors, forbidden,
  restrictions, length, empty output, structured fields);
- wall-clock p50/p95, TTFT p50/p95 and tokens-per-second p50/p95, per repeat and
  aggregated across repeats. The production baseline cannot observe tokens, TTFT
  or finish reasons, so those are reported as N/A with the reason, never as zero;
- finish-reason histogram, errors and gold-leak count;
- per-case exact response stability across the repeats, with the unstable cases.

It never opens the blinded material from the tree and never scores quality: the
scored path only runs when the reviewer passes ``--scored-jsonl`` and
``--blinded-key`` **and** ``--allow-unblind-after-scoring``, which is the explicit
acknowledgement that scoring is finished and the key may be opened. Without the
paths the report carries ``"scored": null``.

No model calls, no network, no external dependencies, read-only on the results
tree. The only file ever written is the optional ``--json`` output.

Usage:
    python bench/aggregate_v2p.py
    python bench/aggregate_v2p.py --results-dir bench/results/v2p-2026-10-16 \
        --models qwen-q4-k-m-revC,bonsai2-2b-q2-0-revC --json C:/Temp/agg.json
    python bench/aggregate_v2p.py --expect-cases 12 --expect-repeats 3 \
        --results-dir bench/results/exploratory-12 --json subset.json
    python bench/aggregate_v2p.py --scored-jsonl scored.jsonl \
        --blinded-key blinded_key.json --allow-unblind-after-scoring

Exit codes: 0 ok, 2 usage/guard problem, 3 validation problems, 4 no data found.

Output schema (also written by --json):
    {
      "generated_at": float, "results_dir": str, "manifest": {...},
      "expect_repeats": int,
      "models": [{
        "model_dir": str, "model_ids": [...], "aliases": [...],
        "model_ftypes": [...], "revisions": {"rep1": "sha|unknown", ...},
        "runner_consistency": "uniform"|"mixed"|"unknown",
        "metadata_gaps": ["rep1: falta metadata runner.sha256", ...],
        "validation": {"problems": [...], "frozen_hashes_ok": bool,
                       "repeats": int, "missing_repeats": [...],
                       "conditions": [...], "cases_per_condition": int},
        "conditions": [{
          "condition": "strategy|context_mode",
          "repeats": int, "rows": int, "checks_apply": bool,
          "auto_checks": {"anchors_present": {"per_rep": [...], "median": ...,
                          "mean": ..., "min": ..., "max": ...}, ...},
          "all_attempt_wall_seconds": {"p50": {...}, "p95": {...}},
          "successful_wall_seconds": {"p50": {...}, "p95": {...}},
          "successful_rows": {...}, "extraction_failures": {...},
          "extraction_failure_rate": {...},
          "wall_seconds": {"p50": {...}, "p95": {...}},
          "ttft_seconds": {"p50": {...}, "p95": {...}} | null,
          "tokens_per_second": {"p50": {...}, "p95": {...}} | null,
          "metrics_unavailable": str|null,
          "finish_reasons": {...}, "errors": {...}, "gold_leaks": {...},
          "stability": {"cases": int, "stable": int, "unstable": [...],
                        "insufficient_repeats": bool, "examples": {...}}
        }]
      }],
      "scored": null | {"candidate_level_rule": str, "candidate_strategies": [...],
                        "review_complete": bool, "coverage": {"key_rows": int,
                        "scored_rows": int, "missing": int, "duplicated": int,
                        "unknown": int, "complete": bool},
                        "scored_rows": int, "unresolved_rows": int,
                        "candidates": [{"candidate": str, "eligible": bool,
                        "ineligible_reason": str|null, "viable": bool, ...}],
                        "verdict": "ganador"|"sin ganador", "winners": [...],
                        "ineligible": [...], "closest": [...],
                        "no_winner_reason": str|null},
      "notes": [...]
    }
"""
import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(HERE, "v2p_references.sha256")
RUBRIC_PATH = os.path.join(HERE, "v2p_rubric.json")
DEFAULT_RESULTS_DIR = os.path.join(HERE, "results")
EXPECTED_CASES = 72

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_VALIDATION = 3
EXIT_NO_DATA = 4

CHECK_KEYS = ("anchors_present", "forbidden_absent", "restrictions_present",
              "length_ok", "empty_output", "structured_fields_filled")
FROZEN_FIELDS = (("corpus", "references_sha256", "references_sha256"),
                 ("corpus", "corpus_file_sha256", "corpus_file_sha256"),
                 ("rubric", "sha256", "rubric_sha256"),
                 ("rubric", "prescoring_sha256", "prescoring_sha256"))
GATE_MIN_RATING = 4  # from the frozen rubric: every dimension >= 4/5
CLOSEST_LIMIT = 5
# Direct, structured, and the isolated two-stage strategy are V2P candidates.
# Baseline and legacy_instruction remain visible controls, not candidates.
CANDIDATE_STRATEGIES = ("direct", "structured", "two_stage")
INELIGIBLE_REASONS = {
    "baseline": ("baseline es la referencia de corrección de dictado, no un "
                 "candidato de Voice-to-Prompt"),
    "legacy_instruction": ("legacy_instruction es el control negativo del factor "
                           "instrucción (corrección de dictado), no un candidato de "
                           "Voice-to-Prompt"),
}

# Fields the aggregator wants in every run's metadata. Absent ones are reported
# instead of guessed, so old runs are labelled rather than silently averaged.
METADATA_EXPECTED = (("runner", "sha256"),
                     ("runtime.summary", "generation_defaults"),
                     ("runtime.summary", "n_ctx"),
                     ("options", "inherited_from_server"),
                     ("cases_run", "count"))


def revision_signature(metadata, rows):
    """Field-level fingerprint of the harness revision that produced a run.

    ``runner.sha256`` is computed when the metadata is written, so a run that
    finished while the file was being edited can record a hash that does not
    correspond to the code it executed. These fields do distinguish the
    revisions, so they are compared across repeats.
    """
    direct = next((row for row in rows if row.get("strategy") == "direct"), None)
    baseline = next((row for row in rows if row.get("strategy") == "baseline"), None)
    summary = (metadata.get("runtime") or {}).get("summary") or {}
    return {
        "runner_hash_recorded": bool((metadata.get("runner") or {}).get("sha256")),
        "generation_defaults": "generation_defaults" in summary,
        "inherited_from_server": "inherited_from_server" in (metadata.get("options") or {}),
        "row_metrics_unavailable": "metrics_unavailable" in (direct or {}),
        "direct_tps_present": isinstance((direct or {}).get("tokens_per_second"),
                                         (int, float)),
        "baseline_finish_reasons_empty": (baseline or {}).get("finish_reasons") == [],
    }


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def read_manifest(path=MANIFEST_PATH):
    manifest = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition(" ")
                manifest[key] = value.strip()
    except OSError:
        return {}
    return manifest


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def sha256_file(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def numbers(values):
    return [value for value in values if isinstance(value, (int, float))
            and not isinstance(value, bool)]


def percentile(values, quantile):
    """Nearest-rank percentile, same convention as the runner's summary."""
    ordered = sorted(numbers(values))
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def median(values):
    ordered = numbers(values)
    return statistics.median(ordered) if ordered else None


def mean(values):
    ordered = numbers(values)
    return statistics.fmean(ordered) if ordered else None


def spread(values):
    """Median, mean and range of one metric across repeats."""
    ordered = numbers(values)
    return {
        "per_rep": list(values),
        "available": bool(ordered),
        "median": statistics.median(ordered) if ordered else None,
        "mean": statistics.fmean(ordered) if ordered else None,
        "min": min(ordered) if ordered else None,
        "max": max(ordered) if ordered else None,
    }


def counter(values):
    counts = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _dig(node, path):
    current = node
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


# --------------------------------------------------------------------------
# Discovery and per-rep metrics
# --------------------------------------------------------------------------

def discover_models(results_dir):
    """Map each model directory to its repeat directories (rep*/rows.jsonl)."""
    models = {}
    for dirpath, _dirnames, filenames in os.walk(results_dir):
        if not os.path.basename(dirpath).startswith("rep"):
            continue
        if "rows.jsonl" not in filenames or "metadata.json" not in filenames:
            continue
        model_dir = os.path.dirname(dirpath)
        models.setdefault(model_dir, {})[os.path.basename(dirpath)] = dirpath
    return models


def condition_of(row):
    return f"{row.get('strategy')}|{row.get('context_mode')}"


def per_rep_metrics(rows):
    """Per-repeat condition stats, retaining all-attempt and success latencies.

    All-attempt wall percentiles use every row with a numeric duration. Success
    latency additionally requires an error-free, nonempty, stop-finished output;
    two_stage must have completed its generation call. Extraction failures count
    only completed, non-error extract calls that fail extraction validation.
    """
    by_condition = {}
    for row in rows:
        by_condition.setdefault(condition_of(row), []).append(row)
    metrics = {}
    for condition, items in by_condition.items():
        applicable = [row for row in items if row.get("checks_apply")]
        clean = [row for row in items if not row.get("error")]
        checks = {}
        for key in CHECK_KEYS:
            values = [bool((row.get("checks") or {})[key]) for row in applicable
                      if (row.get("checks") or {}).get(key) is not None]
            checks[key] = (sum(values) / len(values)) if values else None
        unavailable = next((row.get("metrics_unavailable") for row in items
                            if row.get("metrics_unavailable")), None)
        is_two_stage = bool(items and items[0].get("strategy") == "two_stage")
        successful = []
        extraction_failures = 0
        for row in items:
            response = row.get("response") or ""
            if is_two_stage:
                calls = row.get("calls") or []
                extraction = next((call for call in calls
                                   if call.get("stage") == "extract"), None)
                generation = next((call for call in calls
                                   if call.get("stage") == "generate"), None)
                if (not row.get("extraction_ok") and extraction
                        and not extraction.get("error")
                        and extraction.get("total_seconds") is not None):
                    extraction_failures += 1
                if (not row.get("error") and response and generation
                        and not generation.get("error")
                        and generation.get("finish_reason") == "stop"):
                    successful.append(row)
            elif (not row.get("error") and response
                  and "stop" in (row.get("finish_reasons") or [])):
                successful.append(row)
        metrics[condition] = {
            "rows": len(items),
            "cases": len({row.get("case_id") for row in items}),
            "checks_apply": bool(applicable),
            "checks": checks,
            "all_attempt_wall_p50": percentile(
                [row.get("wall_seconds") for row in items], 0.50),
            "all_attempt_wall_p95": percentile(
                [row.get("wall_seconds") for row in items], 0.95),
            "wall_p50": percentile([row.get("wall_seconds") for row in clean], 0.50),
            "wall_p95": percentile([row.get("wall_seconds") for row in clean], 0.95),
            "success_wall_p50": percentile(
                [row.get("wall_seconds") for row in successful], 0.50),
            "success_wall_p95": percentile(
                [row.get("wall_seconds") for row in successful], 0.95),
            "successful_rows": len(successful),
            "extraction_failures": extraction_failures if is_two_stage else None,
            "extraction_failure_rate": (
                extraction_failures / len(items) if is_two_stage and items else None),
            "ttft_p50": percentile([row.get("ttft_seconds") for row in clean], 0.50),
            "ttft_p95": percentile([row.get("ttft_seconds") for row in clean], 0.95),
            "tps_p50": percentile([row.get("tokens_per_second") for row in clean], 0.50),
            "tps_p95": percentile([row.get("tokens_per_second") for row in clean], 0.95),
            "finish_reasons": counter(reason for row in items
                                      for reason in (row.get("finish_reasons") or [])),
            "errors": sum(1 for row in items if row.get("error")),
            "gold_leaks": sum(len(row.get("gold_leaks") or []) for row in items),
            "unavailable": unavailable,
        }
    return metrics


def stability_by_condition(responses):
    """Per-case exact response stability across repeats.

    ``responses`` maps condition -> case_id -> {rep: response}. A case is stable
    when every repeat produced exactly the same string.
    """
    out = {}
    for condition, by_case in responses.items():
        stable, unstable, examples = 0, [], {}
        for case_id in sorted(by_case):
            values = by_case[case_id]
            if len(values) < 2:
                continue
            if len(set(values.values())) == 1:
                stable += 1
            else:
                unstable.append(case_id)
                if len(examples) < 3:
                    examples[case_id] = {rep: (value or "")[:200]
                                         for rep, value in sorted(values.items())}
        out[condition] = {
            "cases": len(by_case),
            "stable": stable,
            "unstable": unstable,
            "unstable_count": len(unstable),
            "insufficient_repeats": len(by_case) == 0 or any(
                len(values) < 2 for values in by_case.values()),
            "examples": examples,
        }
    return out


def collect_responses(per_rep_rows):
    """condition -> case_id -> {rep: response}."""
    responses = {}
    for rep, rows in per_rep_rows.items():
        for row in rows:
            responses.setdefault(condition_of(row), {}).setdefault(
                row.get("case_id"), {})[rep] = row.get("response") or ""
    return responses


def aggregate_condition(per_rep, condition, reps):
    """Repeat-level aggregation for one condition."""
    values = {rep: (per_rep.get(rep) or {}).get(condition) for rep in reps}
    present = {rep: value for rep, value in values.items() if value}
    if not present:
        return None
    sample = next(iter(present.values()))
    aggregate = {
        "condition": condition,
        "repeats": len(present),
        "rows": sample["rows"],
        "cases_per_repeat": sample["cases"],
        "checks_apply": sample["checks_apply"],
        "case_counts": {rep: value["cases"] for rep, value in values.items()
                        if value},
        "metrics_unavailable": next((value["unavailable"] for value in present.values()
                                     if value["unavailable"]), None),
        "auto_checks": {},
        "all_attempt_wall_seconds": {
            "p50": spread([_dig(value, "all_attempt_wall_p50")
                           for value in values.values()]),
            "p95": spread([_dig(value, "all_attempt_wall_p95")
                           for value in values.values()]),
        },
        "wall_seconds": {
            "p50": spread([_dig(value, "wall_p50") for value in values.values()]),
            "p95": spread([_dig(value, "wall_p95") for value in values.values()]),
        },
        "successful_wall_seconds": {
            "p50": spread([_dig(value, "success_wall_p50")
                           for value in values.values()]),
            "p95": spread([_dig(value, "success_wall_p95")
                           for value in values.values()]),
        },
        "successful_rows": spread([value["successful_rows"]
                                   for value in values.values()]),
        "extraction_failures": spread([value["extraction_failures"]
                                       for value in values.values()]),
        "extraction_failure_rate": spread([value["extraction_failure_rate"]
                                           for value in values.values()]),
        "finish_reasons": counter(reason for value in present.values()
                                  for reason, count in value["finish_reasons"].items()
                                  for _ in range(count)),
        "errors": spread([value["errors"] for value in values.values()]),
        "gold_leaks": spread([value["gold_leaks"] for value in values.values()]),
    }
    for key in CHECK_KEYS:
        aggregate["auto_checks"][key] = spread(
            [_dig(value, f"checks.{key}") for value in values.values()])
    for label, keys in (("ttft_seconds", ("ttft_p50", "ttft_p95")),
                        ("tokens_per_second", ("tps_p50", "tps_p95"))):
        block = {key.split("_")[1]: spread([_dig(value, key)
                                            for value in values.values()])
                 for key in keys}
        aggregate[label] = block if any(block[key]["available"] for key in block) else None
    return aggregate


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_model(model_dir, reps, loaded, manifest, expect_repeats,
                   expected_cases=EXPECTED_CASES):
    """Contract checks for one model directory and the requested case coverage."""
    problems = []
    rep_names = sorted(reps)
    expected_reps = [f"rep{index}" for index in range(1, expect_repeats + 1)]
    missing_repeats = [name for name in expected_reps if name not in reps]
    if missing_repeats:
        problems.append(f"faltan repeticiones: {', '.join(missing_repeats)}")

    frozen_ok = True
    revisions = {}
    signatures = {}
    for rep in rep_names:
        metadata = loaded[rep]["metadata"]
        runner = (metadata.get("runner") or {}).get("sha256")
        revisions[rep] = runner or "unknown"
        signatures[rep] = revision_signature(metadata, loaded[rep]["rows"])
        for section, field, manifest_key in FROZEN_FIELDS:
            value = (metadata.get(section) or {}).get(field)
            if not value or value != manifest.get(manifest_key):
                frozen_ok = False
                problems.append(f"{rep}: {section}.{field} no coincide con el manifiesto")
        if (metadata.get("corpus") or {}).get("case_count") not in (None, EXPECTED_CASES):
            frozen_ok = False
            problems.append(f"{rep}: case_count inesperado en la metadata")

    hashes = {value for value in revisions.values() if value != "unknown"}
    if len(hashes) > 1:
        consistency = "mixed"
        problems.append("revisiones mezcladas: los reps usaron arneses distintos")
    elif hashes:
        consistency = "uniform"
    else:
        consistency = "unknown"

    if len(rep_names) > 1:
        distinct = {tuple(sorted(signature.items())) for signature in signatures.values()}
        if len(distinct) > 1:
            problems.append(
                "las repeticiones no comparten la firma de revisión (campos del arnés "
                "distintos), así que no son repeticiones limpias de la misma revisión")
            if consistency == "uniform":
                problems.append(
                    "el hash de arnés registrado es el mismo pero la firma de revisión "
                    "difiere: el digest se calcula al escribir la metadata, no al arrancar, "
                    "así que no identifica la revisión que realmente corrió")

    condition_sets = {}
    cases_per_condition = set()
    for rep in rep_names:
        rows = loaded[rep]["rows"]
        by_condition = {}
        for row in rows:
            by_condition.setdefault(condition_of(row), []).append(row)
        condition_sets[rep] = set(by_condition)
        for condition, items in by_condition.items():
            ids = [row.get("case_id") for row in items]
            unique = len(set(ids))
            cases_per_condition.add(unique)
            if unique != expected_cases:
                problems.append(f"{rep}: {condition} tiene {unique} casos "
                                f"únicos (esperado {expected_cases})")
            if len(ids) != unique:
                problems.append(f"{rep}: {condition} tiene filas duplicadas")
        keys = [(row.get("run_id"), row.get("case_id")) for row in rows]
        if len(keys) != len(set(keys)):
            problems.append(f"{rep}: hay filas de condición duplicadas")

    reference_set = condition_sets[rep_names[0]] if rep_names else set()
    for rep in rep_names[1:]:
        if condition_sets[rep] != reference_set:
            problems.append(f"{rep}: el conjunto de condiciones difiere de "
                            f"{rep_names[0]}")

    model_ids, aliases, ftypes = set(), set(), set()
    gaps = []
    for rep in rep_names:
        metadata = loaded[rep]["metadata"]
        for path, key in METADATA_EXPECTED:
            node = _dig(metadata, path)
            if not isinstance(node, dict) or key not in node:
                gaps.append(f"{rep}: falta metadata {path}.{key}")
        matrix = loaded[rep]["metadata"].get("matrix") or {}
        model_ids.update(matrix.get("models") or [])
        summary = (loaded[rep]["metadata"].get("runtime") or {}).get("summary") or {}
        if summary.get("model_alias"):
            aliases.add(summary["model_alias"])
        if summary.get("model_ftype"):
            ftypes.add(summary["model_ftype"])
    if len(model_ids) > 1:
        problems.append(f"ids de modelo distintos entre reps: {sorted(model_ids)}")

    facts = {
        "model_dir": model_dir,
        "model_ids": sorted(model_ids),
        "aliases": sorted(aliases),
        "model_ftypes": sorted(ftypes),
        "revisions": revisions,
        "runner_consistency": consistency,
        "revision_signatures": signatures,
        "metadata_gaps": gaps,
        "validation": {
            "problems": problems,
            "frozen_hashes_ok": frozen_ok,
            "repeats": len(rep_names),
            "missing_repeats": missing_repeats,
            "conditions": sorted(reference_set),
            "cases_per_condition": sorted(cases_per_condition)[0]
            if len(cases_per_condition) == 1 else sorted(cases_per_condition),
        },
    }
    return facts, problems


# --------------------------------------------------------------------------
# Optional scored path (explicitly guarded)
# --------------------------------------------------------------------------

def _key_targets(key):
    """Configurations a scored row belongs to.

    Accepts the flat form (``model``/``strategy``/``context_mode`` at the top
    level) and the deduplicated blind-package form, where one row maps to every
    configuration that produced the same variant.
    """
    mapped = key.get("mapped")
    if isinstance(mapped, list) and mapped:
        return [item for item in mapped if isinstance(item, dict)]
    if key.get("model") is not None:
        return [key]
    return []


def load_scored(scored_path, key_path, rubric):
    """Join reviewer scores with the key. Only called behind the guard.

    The blind review must be COMPLETE: exactly one scored row for every row of
    the key. Missing, duplicated or unknown row ids are reported as scoring
    problems, the coverage is marked incomplete, and the report cannot declare a
    winner from it.
    """
    dimension_ids = [item.get("id") for item in
                     (rubric.get("ratings") or {}).get("dimensions") or []
                     if isinstance(item, dict)]
    critical_ids = [item.get("id") for item in rubric.get("critical_errors") or []
                    if isinstance(item, dict)]
    scores = read_jsonl(scored_path)
    key_rows = read_json(key_path)
    if isinstance(key_rows, dict):
        key_rows = key_rows.get("rows") or []
    key_rows = [row for row in key_rows if isinstance(row, dict)]
    key_by_row = {row.get("row_id"): row for row in key_rows}

    problems = []
    seen = {}
    duplicates = []
    for score in scores:
        row_id = score.get("row_id")
        if row_id in seen:
            duplicates.append(row_id)
        seen.setdefault(row_id, score)
    unknown = [row_id for row_id in seen if row_id not in key_by_row]
    missing = [row_id for row_id in key_by_row if row_id not in seen]
    if missing:
        problems.append(f"{len(missing)} fila(s) de la clave sin puntaje: "
                        f"{', '.join(str(item) for item in missing[:5])}")
    if duplicates:
        problems.append(f"row_id duplicado en el puntaje: "
                        f"{', '.join(str(item) for item in sorted(set(duplicates))[:5])}")
    if unknown:
        problems.append(f"row_id desconocido en el puntaje: "
                        f"{', '.join(str(item) for item in sorted(unknown)[:5])}")
    coverage = {
        "key_rows": len(key_rows),
        "scored_rows": len(scores),
        "distinct_scored_rows": len(seen),
        "missing": len(missing),
        "duplicated": len(duplicates),
        "unknown": len(unknown),
        "complete": not (missing or duplicates or unknown),
    }

    candidates = {}
    for row_id, score in seen.items():
        key = key_by_row.get(row_id)
        targets = _key_targets(key or {})
        if not targets:
            continue
        ratings = score.get("ratings") or {}
        absent = [dim for dim in dimension_ids if ratings.get(dim) is None]
        errors = [item for item in (score.get("critical_errors") or []) if item]
        unknown_errors = [item for item in errors if item not in critical_ids]
        if unknown_errors:
            problems.append(f"{row_id}: error crítico desconocido {unknown_errors}")
        for target in targets:
            candidate = (f"{target.get('model')}|{target.get('strategy')}|"
                         f"{target.get('context_mode')}")
            entry = candidates.setdefault(candidate, {"rows": [], "missing_dims": 0})
            entry["rows"].append({"ratings": ratings, "errors": errors,
                                  "missing": absent})
            if absent:
                entry["missing_dims"] += 1
    return candidates, len(unknown), len(scores), dimension_ids, problems, coverage


def score_candidates(candidates, dimension_ids, coverage=None):
    """Candidate verdicts using the frozen gates, never a model as judge.

    ``coverage`` comes from :func:`load_scored`: an incomplete blind review can
    never produce a winner. Direct, structured, and two_stage are candidates;
    baseline and legacy_instruction remain visible with ``eligible: false`` and
    reasons, excluded from winners and closest-candidate selection.
    """
    complete = True if coverage is None else bool(coverage.get("complete"))
    scored = []
    for name, data in sorted(candidates.items()):
        parts = name.split("|")
        strategy = parts[1] if len(parts) == 3 else ""
        eligible = strategy in CANDIDATE_STRATEGIES
        rows = data["rows"]
        count = len(rows)
        with_errors = sum(1 for row in rows if row["errors"])
        full_gate = sum(1 for row in rows
                        if not row["errors"] and not row["missing"]
                        and all(row["ratings"].get(dim, 0) >= GATE_MIN_RATING
                                for dim in dimension_ids))
        medians = {dim: median([row["ratings"].get(dim) for row in rows])
                   for dim in dimension_ids}
        failing = [dim for dim, value in medians.items()
                   if value is None or value < GATE_MIN_RATING]
        viable = (eligible and count > 0 and with_errors == 0 and not failing
                  and full_gate == count)
        scored.append({
            "candidate": name,
            "strategy": strategy,
            "eligible": eligible,
            "ineligible_reason": (None if eligible else
                                  INELIGIBLE_REASONS.get(
                                      strategy, f"estrategia no candidata: {strategy!r}")),
            "scored_rows": count,
            "critical_error_rate": with_errors / count if count else None,
            "rows_with_missing_dimensions": data["missing_dims"],
            "dimension_medians": medians,
            "failing_dimensions": failing,
            "full_gate_rate": full_gate / count if count else None,
            "viable": viable,
        })
    # An incomplete blind review can never produce a winner, even if the scored
    # subset looks perfect.
    winners = [item for item in scored if item["viable"]] if complete else []
    eligible_scored = [item for item in scored if item["eligible"]]
    closest = sorted(eligible_scored,
                     key=lambda item: (len(item["failing_dimensions"]),
                                       item["critical_error_rate"] or 1.0))[
        :CLOSEST_LIMIT]
    if not complete:
        reason = "la revisión ciega está incompleta: no se puede declarar ganador"
    elif not eligible_scored:
        reason = "no hay candidatos elegibles puntuados"
    elif not winners:
        reason = "ningún candidato elegible cumple el gate completo"
    else:
        reason = None
    return {
        "candidate_level_rule": (
            "viable = elegible Y 0 errores criticos Y todas las medianas de las "
            "siete dimensiones >= 4 Y 100% de las respuestas cumplen el gate completo"),
        "candidate_strategies": list(CANDIDATE_STRATEGIES),
        "review_complete": complete,
        "coverage": coverage,
        "candidates": scored,
        "verdict": "ganador" if winners else "sin ganador",
        "winners": [item["candidate"] for item in winners],
        "ineligible": [item["candidate"] for item in scored if not item["eligible"]],
        "closest": [] if winners else closest,
        "no_winner_reason": reason,
    }
def constrain_subset_verdict(scored, expected_cases):
    """Keep partial-sample gate observations, but never infer full viability."""
    if expected_cases >= EXPECTED_CASES:
        return scored
    for candidate in scored["candidates"]:
        candidate["viable_in_sample"] = candidate["viable"]
        candidate["viable"] = False
    scored["viability_scope"] = "exploratory_subset_only"
    scored["verdict"] = "sin ganador"
    scored["winners"] = []
    scored["no_winner_reason"] = (
        f"muestra exploratoria parcial de {expected_cases}/{EXPECTED_CASES} casos: "
        "no permite declarar viabilidad ni ganador para el corpus completo")
    return scored


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def build_report(results_dir, models, manifest, expect_repeats, model_filter=None,
                 expected_cases=EXPECTED_CASES):
    report = {
        "generated_at": time.time(),
        "results_dir": results_dir,
        "manifest": manifest,
        "expect_repeats": expect_repeats,
        "expected_cases": expected_cases,
        "scope": "full_corpus" if expected_cases == EXPECTED_CASES
                 else "exploratory_subset",
        "models": [],
        "scored": None,
        "notes": [],
    }
    all_problems = 0
    for model_dir, reps in sorted(models.items()):
        name = os.path.basename(model_dir.rstrip("\\/"))
        if model_filter and name not in model_filter:
            continue
        loaded = {}
        for rep, rep_dir in reps.items():
            loaded[rep] = {"rows": read_jsonl(os.path.join(rep_dir, "rows.jsonl")),
                           "metadata": read_json(os.path.join(rep_dir, "metadata.json"))}
        rep_names = sorted(loaded)
        facts, problems = validate_model(model_dir, reps, loaded, manifest,
                                         expect_repeats, expected_cases)
        all_problems += len(problems)
        per_rep = {rep: per_rep_metrics(loaded[rep]["rows"]) for rep in rep_names}
        responses = collect_responses({rep: loaded[rep]["rows"] for rep in rep_names})
        stability = stability_by_condition(responses)
        conditions = []
        for condition in sorted({condition for metrics in per_rep.values()
                                 for condition in metrics}):
            aggregate = aggregate_condition(per_rep, condition, rep_names)
            if aggregate is None:
                continue
            aggregate["stability"] = stability.get(condition, {})
            aggregate["per_rep"] = {
                rep: per_rep.get(rep, {}).get(condition) for rep in rep_names}
            conditions.append(aggregate)
        facts["conditions"] = conditions
        report["models"].append(facts)
    if not report["models"]:
        report["notes"].append("no se encontraron corridas con rep*/rows.jsonl")
    report["validation_problems"] = all_problems
    return report


def _format(value, digits=3):
    if value is None:
        return "n/d"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _percent(value):
    return "n/d" if value is None else f"{value:.0%}"


def print_report(report):
    print(f"Resultados en: {report['results_dir']}")
    print(f"Manifiesto: references={report['manifest'].get('references_sha256', 'n/d')[:16]}… "
          f"rubric={report['manifest'].get('rubric_sha256', 'n/d')[:16]}… "
          f"casos={report['manifest'].get('case_count', 'n/d')}")
    for model in report["models"]:
        validation = model["validation"]
        print(f"\n== {os.path.basename(model['model_dir'])} "
              f"({' + '.join(model['model_ids']) or 'sin id'}) ==")
        print(f"   reps={validation['repeats']} "
              f"faltan={validation['missing_repeats'] or 'ninguna'} "
              f"arnés={model['runner_consistency']} "
              f"hashes_congelados_ok={validation['frozen_hashes_ok']} "
              f"casos/condición={validation['cases_per_condition']} "
              f"condiciones={len(validation['conditions'])}")
        if model["aliases"] or model["model_ftypes"]:
            print(f"   alias={model['aliases']} ftype={model['model_ftypes']} "
                  f"revisiones={ {rep: value[:12] for rep, value in model['revisions'].items()} }")
        for gap in model["metadata_gaps"]:
            print(f"   metadata faltante: {gap}")
        for problem in validation["problems"]:
            print(f"   PROBLEMA: {problem}")
        print(f"   {'condición':<26} {'anclas':>7} {'prohib':>7} {'restr':>7} "
              f"{'long':>7} {'wall p50':>9} {'ttft p50':>9} {'tps p50':>8} "
              f"{'err':>4} {'estab':>7}")
        for condition in model["conditions"]:
            checks = condition["auto_checks"]
            ttft = condition.get("ttft_seconds") or {}
            tps = condition.get("tokens_per_second") or {}
            stability = condition.get("stability") or {}
            stable = (f"{stability.get('stable', 0)}/{stability.get('cases', 0)}"
                      if stability else "n/d")
            print(f"   {condition['condition']:<26} "
                  f"{_percent(checks['anchors_present']['median']):>7} "
                  f"{_percent(checks['forbidden_absent']['median']):>7} "
                  f"{_percent(checks['restrictions_present']['median']):>7} "
                  f"{_percent(checks['length_ok']['median']):>7} "
                  f"{_format(condition['wall_seconds']['p50']['median']):>9} "
                  f"{_format((ttft.get('p50') or {}).get('median')):>9} "
                  f"{_format((tps.get('p50') or {}).get('median'), 1):>8} "
                  f"{_format(condition['errors']['median'], 0):>4} "
                  f"{stable:>7}")
            if condition.get("metrics_unavailable"):
                print(f"      (métricas no disponibles: {condition['metrics_unavailable'][:90]}…)")
            if stability.get("unstable_count"):
                print(f"      respuestas inestables entre reps: {stability['unstable_count']} "
                      f"p.ej. {', '.join(stability['unstable'][:5])}")
    if report.get("scored"):
        scored = report["scored"]
        print(f"\n== Puntaje (clave abierta con --allow-unblind-after-scoring) ==")
        coverage = scored.get("coverage") or {}
        print(f"   filas puntuadas={scored['scored_rows']} "
              f"sin resolver={scored['unresolved_rows']} "
              f"revisión completa={scored['review_complete']} "
              f"(clave={coverage.get('key_rows')} faltan={coverage.get('missing')} "
              f"duplicadas={coverage.get('duplicated')} "
              f"desconocidas={coverage.get('unknown')})")
        print(f"   veredicto={scored['verdict']} ganadores={scored.get('winners')} "
              f"elegibles={list(scored.get('candidate_strategies') or [])}")
        for candidate in scored["candidates"]:
            tag = "elegible" if candidate["eligible"] else "NO elegible"
            print(f"   {candidate['candidate']:<40} {tag:<11} "
                  f"viable={candidate['viable']} "
                  f"crit={_percent(candidate['critical_error_rate'])} "
                  f"gate={_percent(candidate['full_gate_rate'])} "
                  f"fallan={candidate['failing_dimensions']}")
            if candidate["ineligible_reason"]:
                print(f"      ({candidate['ineligible_reason']})")
        if scored["verdict"] != "ganador":
            if scored.get("no_winner_reason"):
                print(f"   motivo: {scored['no_winner_reason']}")
            print("   candidatos más cercanos: "
                  + ", ".join(item["candidate"] for item in scored["closest"]))
    for note in report["notes"]:
        print(f"nota: {note}")


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", default=DEFAULT_RESULTS_DIR,
                        help="Directory containing <model>/rep*/rows.jsonl")
    parser.add_argument("--models", help="Comma-separated model directory names")
    parser.add_argument("--expect-repeats", type=int, default=3)
    parser.add_argument("--json", help="Optional local JSON report path")
    parser.add_argument("--expect-cases", type=int, default=EXPECTED_CASES,
                        help="Unique cases per condition (default: frozen full corpus, 72); "
                             "smaller values are exploratory subsets and cannot establish a winner")
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument("--scored-jsonl",
                        help="Blinded review JSONL already scored by the reviewer")
    parser.add_argument("--blinded-key",
                        help="Row-id key, needed to map scores to candidates")
    parser.add_argument("--allow-unblind-after-scoring", action="store_true",
                        help="Acknowledge that scoring is finished and the key may be opened")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if bool(args.scored_jsonl) != bool(args.blinded_key):
        print("--scored-jsonl y --blinded-key se pasan juntos", file=sys.stderr)
        return EXIT_USAGE
    wants_scoring = bool(args.scored_jsonl)
    if wants_scoring and not args.allow_unblind_after_scoring:
        print("El puntaje cualitativo no se calcula sin --allow-unblind-after-scoring: "
              "esa bandera confirma que el puntaje ciego terminó y que la clave se puede abrir.",
              file=sys.stderr)
        return EXIT_USAGE
    if not os.path.isdir(args.results_dir):
        print(f"no existe el directorio de resultados: {args.results_dir}", file=sys.stderr)
        return EXIT_NO_DATA

    manifest = read_manifest(args.manifest)
    if not manifest:
        print(f"no se pudo leer el manifiesto congelado: {args.manifest}", file=sys.stderr)
        return EXIT_VALIDATION
    model_filter = None
    if args.models:
        model_filter = {item.strip() for item in args.models.split(",") if item.strip()}
    models = discover_models(args.results_dir)
    if not 1 <= args.expect_cases <= EXPECTED_CASES:
        print(f"--expect-cases debe estar entre 1 y {EXPECTED_CASES}", file=sys.stderr)
        return EXIT_USAGE
    report = build_report(args.results_dir, models, manifest, args.expect_repeats,
                          model_filter, args.expect_cases)
    if wants_scoring:
        rubric = read_json(RUBRIC_PATH)
        (candidates, unresolved, scored_rows, dimension_ids, problems,
         coverage) = load_scored(args.scored_jsonl, args.blinded_key, rubric)
        report["scored"] = constrain_subset_verdict(
            score_candidates(candidates, dimension_ids, coverage), args.expect_cases)
        report["scored"].update({"scored_rows": scored_rows,
                                 "unresolved_rows": unresolved,
                                 "rubric_sha256": manifest.get("rubric_sha256"),
                                 "scoring_problems": problems})
        if args.expect_cases < EXPECTED_CASES:
            report["notes"].append(
                "La viabilidad de muestra se informa como viable_in_sample; "
                "no se infiere elegibilidad completa ni ganador.")
        report["validation_problems"] += len(problems)

    print_report(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"\nJSON local: {args.json}")
    if not report["models"]:
        return EXIT_NO_DATA
    if report["validation_problems"]:
        print(f"\n{report['validation_problems']} problema(s) de validación",
              file=sys.stderr)
        return EXIT_VALIDATION
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
