"""Reproducible post-run aggregation for the Voice-to-Prompt benchmark.

Covers the contract checks the aggregator must enforce (frozen hashes, harness
revision consistency, 72 unique cases, expected repeats, duplicate condition
rows), the repeat-level statistics, response stability, the N/A handling for the
production baseline, and the guard that prevents unblinding before scoring.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

import aggregate_v2p as agg

MANIFEST = agg.read_manifest()
CASES = 72
CONDITIONS = (("baseline", "off"), ("direct", "off"), ("structured", "oracle_relevant"))


def row(case_index, strategy, context_mode, model, *, anchors, response="r",
        checks_apply=True, ttft=0.1, tps=100.0, error=None, unavailable=None,
        wall=1.0, finish=("stop",)):
    return {
        "run_id": f"{model}|{strategy}|{context_mode}",
        "case_id": f"case_{case_index:03d}",
        "category": "ordinary",
        "model": model,
        "strategy": strategy,
        "context_mode": context_mode,
        "checks_apply": checks_apply,
        "checks": {"anchors_present": anchors, "forbidden_absent": True,
                   "restrictions_present": True, "length_ok": True,
                   "empty_output": False, "structured_fields_filled": True},
        "response": response,
        "wall_seconds": wall,
        "ttft_seconds": ttft,
        "tokens_per_second": tps,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "finish_reasons": list(finish),
        "error": error,
        "gold_leaks": [],
        "metrics_unavailable": unavailable,
    }


def build_tree(root, reps=3, *, cases=CASES, anchors_by_rep=None, ttft=0.1,
               tps=100.0, runner="runnerhash", digests=None, responses=None,
               duplicate_case=False, unavailable=None, checks_apply=True,
               wall=1.0, extra_rep_runner=None):
    """Write <root>/<model>/rep<N>/{rows.jsonl,metadata.json}."""
    model = "fixture-model"
    model_dir = os.path.join(root, model)
    os.makedirs(model_dir, exist_ok=True)
    digests = digests or {
        "corpus": {"references_sha256": MANIFEST["references_sha256"],
                   "corpus_file_sha256": MANIFEST["corpus_file_sha256"],
                   "case_count": CASES},
        "rubric": {"sha256": MANIFEST["rubric_sha256"],
                   "prescoring_sha256": MANIFEST["prescoring_sha256"]},
    }
    for index in range(1, reps + 1):
        rep = f"rep{index}"
        rep_dir = os.path.join(model_dir, rep)
        os.makedirs(rep_dir, exist_ok=True)
        anchors = (anchors_by_rep or {}).get(rep, 1.0)
        anchors_true = round(anchors * cases)
        rows = []
        for case_index in range(cases):
            for strategy, context_mode in CONDITIONS:
                is_baseline = strategy == "baseline"
                rows.append(row(
                    case_index, strategy, context_mode, model,
                    anchors=None if is_baseline else case_index < anchors_true,
                    checks_apply=False if is_baseline else checks_apply,
                    ttft=None if is_baseline else ttft,
                    tps=None if is_baseline else tps,
                    unavailable=unavailable if is_baseline else None,
                    response=(responses or {}).get(rep, {}).get(case_index, "r"),
                    wall=wall))
        if duplicate_case:
            rows.append(rows[0])
        with open(os.path.join(rep_dir, "rows.jsonl"), "w", encoding="utf-8") as handle:
            for item in rows:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        rep_runner = runner if extra_rep_runner is None or rep != "rep2" else extra_rep_runner
        metadata = {
            "scored": True, "transport": "endpoint",
            "runner": ({"path": "dictado/bench/bench_v2p.py", "sha256": rep_runner}
                       if rep_runner else None),
            "matrix": {"models": [model], "strategies": ["baseline", "direct",
                                                         "structured"],
                       "context_modes": [mode for _s, mode in CONDITIONS],
                       "cases": cases, "runs": 3, "rows": len(rows)},
            "cases_run": {"count": cases, "ids": [f"case_{i:03d}" for i in range(cases)]},
            "runtime": {"summary": {"model_alias": model, "model_ftype": "Q4_K - Medium",
                                    "n_ctx": 4096,
                                    "generation_defaults": {"top_p": 0.95, "top_k": 40}}},
            "options": {"inherited_from_server": ["top_p", "top_k"], "stream": True},
            **digests,
        }
        with open(os.path.join(rep_dir, "metadata.json"), "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2)
    return model_dir


class AggregateFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def report(self, **kwargs):
        build_tree(self.root, **kwargs)
        models = agg.discover_models(self.root)
        return agg.build_report(self.root, models, MANIFEST, kwargs.pop("expect", 3))


class ValidationTests(AggregateFixture):
    def test_valid_tree_reports_no_problems(self):
        report = self.report()
        self.assertEqual(report["validation_problems"], 0)
        model = report["models"][0]
        self.assertEqual(model["validation"]["repeats"], 3)
        self.assertEqual(model["validation"]["missing_repeats"], [])
        self.assertEqual(model["validation"]["cases_per_condition"], CASES)
        self.assertTrue(model["validation"]["frozen_hashes_ok"])
        self.assertEqual(model["runner_consistency"], "uniform")
        self.assertEqual(len(model["validation"]["conditions"]), len(CONDITIONS))
    def test_explicit_12_case_subset_is_valid_but_marked_exploratory(self):
        build_tree(self.root, cases=12)
        models = agg.discover_models(self.root)
        report = agg.build_report(self.root, models, MANIFEST, 3,
                                  expected_cases=12)
        self.assertEqual(report["validation_problems"], 0)
        self.assertEqual(report["expected_cases"], 12)
        self.assertEqual(report["scope"], "exploratory_subset")
        self.assertEqual(report["models"][0]["validation"]["cases_per_condition"], 12)


    def test_missing_repeat_is_reported(self):
        build_tree(self.root, reps=2)
        models = agg.discover_models(self.root)
        report = agg.build_report(self.root, models, MANIFEST, 3)
        problems = report["models"][0]["validation"]["problems"]
        self.assertTrue(any("faltan repeticiones" in problem for problem in problems))
        self.assertEqual(report["models"][0]["validation"]["missing_repeats"], ["rep3"])

    def test_wrong_case_count_is_reported(self):
        build_tree(self.root, cases=CASES - 1)
        models = agg.discover_models(self.root)
        report = agg.build_report(self.root, models, MANIFEST, 3)
        problems = report["models"][0]["validation"]["problems"]
        self.assertTrue(any("casos únicos" in problem for problem in problems))

    def test_duplicate_condition_rows_are_reported(self):
        build_tree(self.root, duplicate_case=True)
        models = agg.discover_models(self.root)
        report = agg.build_report(self.root, models, MANIFEST, 3)
        problems = report["models"][0]["validation"]["problems"]
        self.assertTrue(any("duplicad" in problem for problem in problems))

    def test_mixed_harness_revisions_are_reported(self):
        build_tree(self.root, extra_rep_runner="otherhash")
        models = agg.discover_models(self.root)
        report = agg.build_report(self.root, models, MANIFEST, 3)
        model = report["models"][0]
        self.assertEqual(model["runner_consistency"], "mixed")
        self.assertTrue(any("revisiones mezcladas" in problem
                            for problem in model["validation"]["problems"]))

    def test_missing_runner_hash_is_labelled_unknown_not_fatal(self):
        build_tree(self.root, runner=None)
        models = agg.discover_models(self.root)
        report = agg.build_report(self.root, models, MANIFEST, 3)
        model = report["models"][0]
        self.assertEqual(model["runner_consistency"], "unknown")
        self.assertEqual(model["revisions"]["rep1"], "unknown")
        self.assertEqual(model["validation"]["problems"], [])

    def test_frozen_hash_mismatch_is_reported(self):
        digests = {"corpus": {"references_sha256": "0" * 64,
                              "corpus_file_sha256": MANIFEST["corpus_file_sha256"],
                              "case_count": CASES},
                   "rubric": {"sha256": MANIFEST["rubric_sha256"],
                              "prescoring_sha256": MANIFEST["prescoring_sha256"]}}
        build_tree(self.root, digests=digests)
        models = agg.discover_models(self.root)
        report = agg.build_report(self.root, models, MANIFEST, 3)
        model = report["models"][0]
        self.assertFalse(model["validation"]["frozen_hashes_ok"])
        self.assertTrue(any("references_sha256" in problem
                            for problem in model["validation"]["problems"]))


    def test_signatures_distinguish_revisions_that_share_a_recorded_hash(self):
        """Same recorded hash, different harness fields: not clean repeats."""
        build_tree(self.root)
        rep2 = os.path.join(self.root, "fixture-model", "rep2")
        path = os.path.join(rep2, "metadata.json")
        with open(path, encoding="utf-8") as handle:
            metadata = json.load(handle)
        metadata["options"].pop("inherited_from_server", None)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2)
        models = agg.discover_models(self.root)
        report = agg.build_report(self.root, models, MANIFEST, 3)
        model = report["models"][0]
        self.assertEqual(model["runner_consistency"], "uniform")
        signatures = model["revision_signatures"]
        self.assertNotEqual(signatures["rep1"], signatures["rep2"])
        problems = model["validation"]["problems"]
        self.assertTrue(any("firma de revisión" in item for item in problems))
        self.assertTrue(any("no identifica la revisión" in item for item in problems))

    def test_identical_signatures_produce_no_revision_problem(self):
        report = self.report()
        problems = report["models"][0]["validation"]["problems"]
        self.assertFalse(any("firma de revisión" in item for item in problems))
        signatures = report["models"][0]["revision_signatures"]
        self.assertEqual(len({tuple(sorted(item.items())) for item in signatures.values()}), 1)


class StatisticsTests(AggregateFixture):
    def test_repeat_level_median_mean_and_range(self):
        report = self.report(anchors_by_rep={"rep1": 1.0, "rep2": 0.5, "rep3": 1.0})
        condition = [item for item in report["models"][0]["conditions"]
                     if item["condition"] == "direct|off"][0]
        anchors = condition["auto_checks"]["anchors_present"]
        self.assertEqual(anchors["per_rep"], [1.0, 0.5, 1.0])
        self.assertEqual(anchors["median"], 1.0)
        self.assertAlmostEqual(anchors["mean"], 2.5 / 3)
        self.assertEqual(anchors["min"], 0.5)
        self.assertEqual(anchors["max"], 1.0)

    def test_latency_and_throughput_aggregate_across_repeats(self):
        report = self.report(ttft=0.2, tps=50.0, wall=2.0)
        condition = [item for item in report["models"][0]["conditions"]
                     if item["condition"] == "direct|off"][0]
        self.assertAlmostEqual(condition["wall_seconds"]["p50"]["median"], 2.0)
        self.assertAlmostEqual(condition["ttft_seconds"]["p50"]["median"], 0.2)
        self.assertAlmostEqual(condition["tokens_per_second"]["p50"]["median"], 50.0)
        self.assertEqual(condition["finish_reasons"], {"stop": 216})

        self.assertAlmostEqual(condition["all_attempt_wall_seconds"]["p50"]["median"], 2.0)
        self.assertAlmostEqual(condition["successful_wall_seconds"]["p50"]["median"], 2.0)
        self.assertEqual(condition["successful_rows"]["median"], CASES)
        self.assertIsNone(condition["extraction_failure_rate"]["median"])

    def test_two_stage_success_latency_excludes_fail_closed_extractions(self):
        success = row(0, "two_stage", "off", "m", anchors=True,
                      response="prompt final", wall=5.0)
        success.update({
            "extraction_ok": True,
            "calls": [{"stage": "extract", "finish_reason": "stop", "error": None,
                       "total_seconds": 1.0},
                      {"stage": "generate", "finish_reason": "stop", "error": None,
                       "total_seconds": 4.0}],
        })
        failed = row(1, "two_stage", "off", "m", anchors=False,
                     response="", wall=1.0)
        failed.update({"extraction_ok": False, "calls": [
            {"stage": "extract", "finish_reason": "length", "error": None,
             "total_seconds": 1.0}]})
        infra = row(2, "two_stage", "off", "m", anchors=False,
                    response="", wall=2.0, error="URLError")
        infra.update({"extraction_ok": False, "calls": [
            {"stage": "extract", "finish_reason": None, "error": "URLError",
             "total_seconds": None}]})
        metrics = agg.per_rep_metrics([success, failed, infra])["two_stage|off"]
        self.assertEqual(metrics["wall_p50"], 1.0)
        self.assertEqual(metrics["all_attempt_wall_p50"], 2.0)
        self.assertEqual(metrics["success_wall_p50"], 5.0)
        self.assertEqual(metrics["success_wall_p95"], 5.0)
        self.assertEqual(metrics["successful_rows"], 1)
        self.assertEqual(metrics["extraction_failures"], 1)
        self.assertAlmostEqual(metrics["extraction_failure_rate"], 1 / 3)

    def test_baseline_is_na_not_zero(self):
        report = self.report(unavailable="la ruta de produccion devuelve solo texto")
        baseline = [item for item in report["models"][0]["conditions"]
                    if item["condition"] == "baseline|off"][0]
        self.assertFalse(baseline["checks_apply"])
        for key, block in baseline["auto_checks"].items():
            with self.subTest(key=key):
                self.assertIsNone(block["median"])
                self.assertFalse(block["available"])
        self.assertIsNone(baseline["ttft_seconds"])
        self.assertIsNone(baseline["tokens_per_second"])
        self.assertIn("solo texto", baseline["metrics_unavailable"])

    def test_errors_are_counted_per_repeat(self):
        build_tree(self.root)
        rep_dir = os.path.join(self.root, "fixture-model", "rep1")
        path = os.path.join(rep_dir, "rows.jsonl")
        with open(path, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        target = next(index for index, item in enumerate(rows)
                      if item["strategy"] == "direct")
        rows[target].update({"error": "HTTPError 500", "finish_reasons": [],
                             "tokens_per_second": None, "completion_tokens": None})
        with open(path, "w", encoding="utf-8") as handle:
            for item in rows:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        models = agg.discover_models(self.root)
        report = agg.build_report(self.root, models, MANIFEST, 3)
        condition = [item for item in report["models"][0]["conditions"]
                     if item["condition"] == "direct|off"][0]
        self.assertEqual(condition["errors"]["per_rep"][0], 1)
        self.assertEqual(condition["errors"]["per_rep"][1], 0)
        self.assertEqual(condition["errors"]["max"], 1)
        self.assertEqual(condition["errors"]["mean"], 1 / 3)

    def test_response_stability_across_repeats(self):
        responses = {"rep1": {1: "igual"}, "rep2": {1: "distinto"},
                     "rep3": {1: "igual"}}
        report = self.report(responses=responses)
        condition = [item for item in report["models"][0]["conditions"]
                     if item["condition"] == "direct|off"][0]
        stability = condition["stability"]
        self.assertEqual(stability["unstable"], ["case_001"])
        self.assertEqual(stability["stable"], CASES - 1)
        self.assertIn("case_001", stability["examples"])


class HelperTests(unittest.TestCase):
    def test_percentile_matches_the_runner_convention(self):
        values = list(range(1, 11))
        self.assertEqual(agg.percentile(values, 0.95), 10)
        self.assertEqual(agg.percentile(values, 0.50), 5)
        self.assertIsNone(agg.percentile([], 0.95))

    def test_spread_keeps_per_rep_positions_and_flags_availability(self):
        block = agg.spread([1.0, None, 3.0])
        self.assertEqual(block["per_rep"], [1.0, None, 3.0])
        self.assertTrue(block["available"])
        self.assertEqual(block["median"], 2.0)
        self.assertEqual(block["mean"], 2.0)
        self.assertEqual((block["min"], block["max"]), (1.0, 3.0))
        empty = agg.spread([None, None])
        self.assertFalse(empty["available"])
        self.assertIsNone(empty["median"])
        self.assertFalse(agg.spread([])["available"])

    def test_numbers_ignores_booleans_and_none(self):
        self.assertEqual(agg.numbers([True, False, None, 2, 3.5]), [2, 3.5])


class GuardTests(AggregateFixture):
    def test_scoring_requires_the_explicit_guard(self):
        build_tree(self.root)
        code = agg.main(["--results-dir", self.root, "--scored-jsonl", "x.jsonl",
                         "--blinded-key", "key.json"])
        self.assertEqual(code, agg.EXIT_USAGE)

    def test_scored_and_key_must_be_passed_together(self):
        build_tree(self.root)
        code = agg.main(["--results-dir", self.root, "--scored-jsonl", "x.jsonl",
                         "--allow-unblind-after-scoring"])
        self.assertEqual(code, agg.EXIT_USAGE)

    def test_report_without_scores_carries_scored_null(self):
        report = self.report()
        self.assertIsNone(report["scored"])

    def test_tree_without_blinded_files_still_aggregates(self):
        """The aggregator must not depend on the blinded material."""
        build_tree(self.root)
        rep_dir = os.path.join(self.root, "fixture-model", "rep1")
        self.assertFalse(os.path.exists(os.path.join(rep_dir, "blinded_key.json")))
        self.assertEqual(agg.main(["--results-dir", self.root]), agg.EXIT_OK)

    def test_usage_error_when_results_dir_is_missing(self):
        self.assertEqual(agg.main(["--results-dir", self.root + "_missing"]),
                         agg.EXIT_NO_DATA)


class ScoringTests(unittest.TestCase):
    def _score(self, rows):
        (candidates, unresolved, total, dimension_ids, problems,
         coverage) = agg.load_scored(rows["scored_path"], rows["key_path"],
                                     agg.read_json(agg.RUBRIC_PATH))
        return (agg.score_candidates(candidates, dimension_ids, coverage),
                unresolved, problems)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        rubric = agg.read_json(agg.RUBRIC_PATH)
        self.dimensions = [item["id"] for item in rubric["ratings"]["dimensions"]]
        self.critical = rubric["critical_errors"][0]["id"]

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, scores, key):
        scored_path = os.path.join(self.root, "scored.jsonl")
        key_path = os.path.join(self.root, "key.json")
        with open(scored_path, "w", encoding="utf-8") as handle:
            for item in scores:
                handle.write(json.dumps(item) + "\n")
        with open(key_path, "w", encoding="utf-8") as handle:
            json.dump(key, handle)
        return scored_path, key_path

    def test_accepts_a_candidate_with_no_critical_error_and_all_dimensions_high(self):
        scores = [{"row_id": "row-a", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": []}]
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct", "context_mode": "off"}]
        paths = self._write(scores, key)
        rows = {"scored_path": paths[0], "key_path": paths[1]}
        verdict, unresolved, problems = self._score(rows)
        self.assertEqual(verdict["verdict"], "ganador")
        self.assertEqual(verdict["winners"], ["m|direct|off"])
        self.assertEqual(problems, [])
        self.assertEqual(unresolved, 0)

    def test_two_stage_is_eligible_while_correction_controls_are_not(self):
        strategies = ("direct", "structured", "two_stage",
                      "baseline", "legacy_instruction")
        scores = []
        key = []
        for index, strategy in enumerate(strategies):
            row_id = f"row-{index}"
            scores.append({"row_id": row_id,
                           "ratings": {dim: 5 for dim in self.dimensions},
                           "critical_errors": []})
            key.append({"row_id": row_id, "model": "m", "strategy": strategy,
                        "context_mode": "off"})
        paths = self._write(scores, key)
        verdict, unresolved, problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertEqual(problems, [])
        self.assertEqual(unresolved, 0)
        self.assertTrue(verdict["review_complete"])
        self.assertEqual(verdict["candidate_strategies"],
                         ["direct", "structured", "two_stage"])
        self.assertEqual(verdict["verdict"], "ganador")
        self.assertEqual(verdict["winners"],
                         ["m|direct|off", "m|structured|off", "m|two_stage|off"])
        by_name = {item["candidate"]: item for item in verdict["candidates"]}
        self.assertTrue(by_name["m|two_stage|off"]["eligible"])
        self.assertFalse(by_name["m|baseline|off"]["eligible"])
        self.assertFalse(by_name["m|legacy_instruction|off"]["eligible"])
    def test_partial_subset_never_declares_full_corpus_viability(self):
        scored = agg.score_candidates(
            {"m|two_stage|off": {
                "rows": [{"ratings": {dim: 5 for dim in self.dimensions},
                          "errors": [], "missing": []}],
                "missing_dims": 0,
            }},
            self.dimensions,
            {"complete": True},
        )
        limited = agg.constrain_subset_verdict(scored, 12)
        self.assertEqual(limited["verdict"], "sin ganador")
        self.assertEqual(limited["winners"], [])
        self.assertEqual(limited["viability_scope"], "exploratory_subset_only")
        self.assertTrue(limited["candidates"][0]["viable_in_sample"])
        self.assertFalse(limited["candidates"][0]["viable"])

    def test_any_critical_error_rejects_even_with_perfect_style(self):
        scores = [{"row_id": "row-a", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": [self.critical]}]
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct", "context_mode": "off"}]
        paths = self._write(scores, key)
        verdict, _unresolved, _problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertEqual(verdict["verdict"], "sin ganador")
        candidate = verdict["candidates"][0]
        self.assertFalse(candidate["viable"])
        self.assertEqual(candidate["critical_error_rate"], 1.0)
        self.assertIn(candidate, verdict["closest"])

    def test_a_single_dimension_below_four_blocks_viability(self):
        ratings = {dim: 5 for dim in self.dimensions}
        ratings["clarity"] = 3
        scores = [{"row_id": "row-a", "ratings": ratings, "critical_errors": []}]
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct", "context_mode": "off"}]
        paths = self._write(scores, key)
        verdict, _unresolved, _problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        candidate = verdict["candidates"][0]
        self.assertEqual(candidate["failing_dimensions"], ["clarity"])
        self.assertFalse(candidate["viable"])
        self.assertEqual(verdict["verdict"], "sin ganador")

    def test_missing_dimensions_and_unresolved_rows_are_reported(self):
        scores = [{"row_id": "row-a", "ratings": {"fidelity": 5}, "critical_errors": []},
                  {"row_id": "row-unknown", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": []}]
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct", "context_mode": "off"}]
        paths = self._write(scores, key)
        verdict, unresolved, _problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertEqual(unresolved, 1)
        self.assertEqual(verdict["candidates"][0]["rows_with_missing_dimensions"], 1)

    def test_deduplicated_blind_key_expands_to_every_configuration(self):
        """One scored blind row counts for every configuration it maps to."""
        key = {"rows": [{"row_id": "row-a", "case_id": "c",
                         "mapped": [{"model": "m1", "strategy": "direct",
                                     "context_mode": "off", "rep": "rep1"},
                                    {"model": "m2", "strategy": "direct",
                                     "context_mode": "off", "rep": "rep2"}]}]}
        scores = [{"row_id": "row-a", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": []}]
        scored_path = os.path.join(self.root, "scored.jsonl")
        key_path = os.path.join(self.root, "nested-key.json")
        with open(scored_path, "w", encoding="utf-8") as handle:
            for item in scores:
                handle.write(json.dumps(item) + "\n")
        with open(key_path, "w", encoding="utf-8") as handle:
            json.dump(key, handle)
        (candidates, unresolved, total, dimension_ids, problems,
         coverage) = agg.load_scored(scored_path, key_path,
                                     agg.read_json(agg.RUBRIC_PATH))
        self.assertEqual(unresolved, 0)
        self.assertEqual(total, 1)
        self.assertEqual(problems, [])
        self.assertTrue(coverage["complete"])
        result = agg.score_candidates(candidates, dimension_ids, coverage)
        self.assertEqual(sorted(item["candidate"] for item in result["candidates"]),
                         ["m1|direct|off", "m2|direct|off"])
        self.assertTrue(all(item["viable"] for item in result["candidates"]))

    def test_omitted_scored_row_makes_the_review_incomplete(self):
        """A missing score must not let the candidate pass the gate."""
        scores = [{"row_id": "row-a", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": []}]
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct", "context_mode": "off"},
               {"row_id": "row-b", "model": "m", "strategy": "direct", "context_mode": "off"}]
        paths = self._write(scores, key)
        verdict, _unresolved, problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertTrue(any("sin puntaje" in item for item in problems))
        self.assertFalse(verdict["review_complete"])
        self.assertEqual(verdict["verdict"], "sin ganador")
        self.assertEqual(verdict["winners"], [])
        self.assertIn("incompleta", verdict["no_winner_reason"])
        self.assertEqual(verdict["coverage"]["missing"], 1)

    def test_duplicated_scored_row_makes_the_review_incomplete(self):
        ratings = {"row_id": "row-a", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": []}
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct", "context_mode": "off"}]
        paths = self._write([ratings, dict(ratings)], key)
        verdict, _unresolved, problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertTrue(any("duplicado" in item for item in problems))
        self.assertFalse(verdict["review_complete"])
        self.assertEqual(verdict["verdict"], "sin ganador")
        self.assertEqual(verdict["coverage"]["duplicated"], 1)

    def test_unknown_scored_row_makes_the_review_incomplete(self):
        scores = [{"row_id": "row-z", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": []}]
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct", "context_mode": "off"}]
        paths = self._write(scores, key)
        verdict, unresolved, problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertEqual(unresolved, 1)
        self.assertTrue(any("desconocido" in item for item in problems))
        self.assertFalse(verdict["review_complete"])
        self.assertEqual(verdict["verdict"], "sin ganador")

    def test_perfect_scores_with_a_complete_review_can_still_win(self):
        scores = [{"row_id": "row-a", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": []}]
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct", "context_mode": "off"}]
        paths = self._write(scores, key)
        verdict, _unresolved, problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertEqual(problems, [])
        self.assertTrue(verdict["review_complete"])
        self.assertEqual(verdict["verdict"], "ganador")

    def test_correction_references_are_visible_but_not_eligible(self):
        """baseline and legacy_instruction keep metrics but cannot win."""
        ratings = {dim: 5 for dim in self.dimensions}
        scores = [{"row_id": "row-base", "ratings": ratings, "critical_errors": []},
                  {"row_id": "row-legacy", "ratings": ratings, "critical_errors": []},
                  {"row_id": "row-direct", "ratings": ratings, "critical_errors": []}]
        key = [{"row_id": "row-base", "model": "m", "strategy": "baseline",
                "context_mode": "off"},
               {"row_id": "row-legacy", "model": "m", "strategy": "legacy_instruction",
                "context_mode": "off"},
               {"row_id": "row-direct", "model": "m", "strategy": "direct",
                "context_mode": "off"}]
        paths = self._write(scores, key)
        verdict, _unresolved, problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertEqual(problems, [])
        by_candidate = {item["candidate"]: item for item in verdict["candidates"]}
        self.assertFalse(by_candidate["m|baseline|off"]["eligible"])
        self.assertFalse(by_candidate["m|legacy_instruction|off"]["eligible"])
        self.assertIn("referencia de corrección",
                      by_candidate["m|baseline|off"]["ineligible_reason"])
        self.assertIn("control negativo",
                      by_candidate["m|legacy_instruction|off"]["ineligible_reason"])
        # metrics stay visible even though they cannot win
        self.assertEqual(by_candidate["m|baseline|off"]["full_gate_rate"], 1.0)
        self.assertEqual(by_candidate["m|legacy_instruction|off"]["dimension_medians"],
                         by_candidate["m|direct|off"]["dimension_medians"])
        self.assertEqual(verdict["winners"], ["m|direct|off"])
        self.assertEqual(sorted(verdict["ineligible"]),
                         ["m|baseline|off", "m|legacy_instruction|off"])

    def test_candidate_strategy_allowlist_includes_all_v2p_arms(self):
        ratings = {dim: 5 for dim in self.dimensions}
        scores = [{"row_id": "row-x", "ratings": ratings, "critical_errors": []}]
        key = [{"row_id": "row-x", "model": "m", "strategy": "otra_cosa",
                "context_mode": "off"}]
        paths = self._write(scores, key)
        verdict, _unresolved, _problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertEqual(verdict["candidate_strategies"],
                         ["direct", "structured", "two_stage"])
        self.assertFalse(verdict["candidates"][0]["eligible"])
        self.assertEqual(verdict["verdict"], "sin ganador")
        self.assertEqual(verdict["closest"], [])

    def test_incomplete_review_exits_with_a_validation_error(self):
        tree = os.path.join(self.root, "tree")
        os.makedirs(tree)
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct",
                "context_mode": "off"},
               {"row_id": "row-b", "model": "m", "strategy": "direct",
                "context_mode": "off"}]
        scores = [{"row_id": "row-a", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": []}]
        scored_path = os.path.join(self.root, "scores.jsonl")
        key_path = os.path.join(self.root, "key.json")
        with open(scored_path, "w", encoding="utf-8") as handle:
            for item in scores:
                handle.write(json.dumps(item) + "\n")
        with open(key_path, "w", encoding="utf-8") as handle:
            json.dump(key, handle)
        code = agg.main(["--results-dir", self.root, "--scored-jsonl", scored_path,
                         "--blinded-key", key_path, "--allow-unblind-after-scoring"])
        self.assertNotEqual(code, agg.EXIT_OK)

    def test_unknown_critical_error_id_is_flagged(self):
        scores = [{"row_id": "row-a", "ratings": {dim: 5 for dim in self.dimensions},
                   "critical_errors": ["invented_class"]}]
        key = [{"row_id": "row-a", "model": "m", "strategy": "direct", "context_mode": "off"}]
        paths = self._write(scores, key)
        _verdict, _unresolved, problems = self._score(
            {"scored_path": paths[0], "key_path": paths[1]})
        self.assertTrue(any("desconocido" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
