"""The LLM benchmark exposes quality and safety metrics without network use."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

import bench_llm
import analyze_correction_runs


class LLMBenchmarkTests(unittest.TestCase):
    def test_frozen_corpus_anchors_and_no_change_controls(self):
        corpus_path = os.path.join(
            os.path.dirname(__file__), "..", "bench",
            "llm_cases_expanded_2026-10-16.json")
        cases = bench_llm.load_cases(corpus_path)["normal"]
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["input"].strip())
                self.assertTrue(case["expected"].strip())
                self.assertTrue(case["must_keep"])
                self.assertTrue(all(
                    bench_llm._contains_anchor(case["expected"], anchor)
                    for anchor in case["must_keep"]))
        no_change = [case for case in cases if case.get("no_change")]
        self.assertGreaterEqual(len(no_change), 4)
        self.assertTrue(all(
            bench_llm._canonical(case["input"]) ==
            bench_llm._canonical(case["expected"])
            for case in no_change))





    def test_anchor_matching_ignores_case_and_punctuation_but_keeps_accents(self):
        self.assertTrue(bench_llm._contains_anchor(
            "M\u00e1ximo, hola.", "M\u00e1XIMO hola"))
        self.assertFalse(bench_llm._contains_anchor(
            "M\u00e1ximo, hola.", "maximo hola"))
        self.assertFalse(bench_llm._contains_anchor(
            "Prioriz\u00e1 precisi\u00f3n.", "Prioriza precisi\u00f3n"))
        self.assertFalse(bench_llm._contains_anchor(
            "Cuando llegue el paquete.", "cuando llega el paquete"))

    def test_latency_separates_initial_and_warm_calls(self):
        summary = bench_llm._latency_summary([3.0, 1.0, 2.0, 1.5])
        self.assertEqual(summary["first_request_seconds"], 3.0)
        self.assertEqual(summary["warm_p50_seconds"], 1.5)
        self.assertEqual(summary["warm_p95_seconds"], 2.0)

    def test_explicit_corpus_path_and_frozen_schema(self):
        corpus_path = os.path.join(
            os.path.dirname(__file__), "..", "bench",
            "llm_cases_expanded_2026-10-16.json")
        corpus = bench_llm.load_cases(corpus_path)
        cases = corpus["normal"]
        self.assertEqual(len(cases), 48)
        self.assertEqual(len({case["id"] for case in cases}), 48)
        self.assertGreaterEqual(
            sum(bool(case.get("no_change")) for case in cases), 4)
        self.assertGreaterEqual(sum(
            bench_llm._word_error_rate(case["expected"], case["input"]) > 0
            for case in cases), 16)
        self.assertTrue(all(case["expected"] and case["must_keep"]
                            for case in cases))
        self.assertTrue(all(not any(key in case for key in ("speaker", "audio"))
                            for case in cases))

    def test_aggregate_metrics_count_invariant_and_harm_categories(self):
        metrics = bench_llm._aggregate_metrics([
            {"exact_match": True, "baseline_exact_match": False,
             "content_exact_match": True, "baseline_content_exact_match": False,
             "baseline_content_error": True, "baseline_wer": 0.2, "wer": 0.0,
             "word_sequence_changed": True, "meaning_check_passed": True,
             "missing": [], "harmful_lexical_edit": False,
             "no_change": True, "no_change_lexical_edit": False,
             "baseline_cer": 0.2, "cer": 0.0},
            {"exact_match": False, "baseline_exact_match": True,
             "content_exact_match": False, "baseline_content_exact_match": True,
             "baseline_content_error": False, "baseline_wer": 0.0, "wer": 0.1,
             "word_sequence_changed": False, "meaning_check_passed": False,
             "missing": ["no borres"], "harmful_lexical_edit": True,
             "no_change": True, "no_change_lexical_edit": True,
             "baseline_cer": 0.0, "cer": 0.1},
        ])
        self.assertEqual(metrics["verifier_rejections"], 1)
        self.assertEqual(metrics["critical_invariant_violations"], 1)
        self.assertEqual(metrics["cases_with_critical_invariant_violations"], 1)
        self.assertEqual(metrics["harmful_lexical_edits"], 1)
        self.assertEqual(metrics["no_change_lexical_edits"], 1)
        self.assertAlmostEqual(metrics["character_error_rate_reduction"], 0.05)
        self.assertAlmostEqual(metrics["word_error_rate_reduction"], 0.05)
        self.assertEqual(metrics["baseline_content_error_case_count"], 1)
        self.assertEqual(metrics["accepted_content_token_exact_match_rate"], 1.0)

    def test_normalized_wer_separates_punctuation_from_content(self):
        self.assertEqual(
            bench_llm._word_error_rate("hola mundo", "Hola, mundo!"), 0.0)
        self.assertGreater(
            bench_llm._word_error_rate("el tubo está listo", "el tuvo está listo"),
            0.0)
    def test_paired_analysis_rejects_incompatible_reports(self):
        report = {
            "corpus_sha256": "corpus",
            "sampling_profile": "matched-deterministic",
            "sampling_params": {"temperature": 0.0},
            "candidate_request": {"sampling_params": {"temperature": 0.0}},
            "verifier_request": {"temperature": 0.0},
            "server_sampling_params": {"temperature": 0.0},
            "system_prompt_sha256": "system",
            "verify_prompt_sha256": "verify",
            "case_ids": ["case-1"],
            "runtime": {
                "server_build": "build",
                "server_binary_sha256": "binary",
                "port": 8081,
                "server_command": [
                    "llama-server.exe", "-m", "qwen.gguf", "--alias", "qwen",
                    "--host", "127.0.0.1", "--port", "8081", "--temp", "0"],
            },
            "normal": {"cases": [{"id": "case-1", "baseline_wer": 0.25}]},
        }
        self.assertEqual(
            len(analyze_correction_runs._paired(report, dict(report))), 1)
        for key, bad_value in (
                ("corpus_sha256", "other"),
                ("sampling_profile", "bonsai-nonthinking"),
                ("case_ids", ["case-2"])):
            incompatible = dict(report)
            incompatible[key] = bad_value
            with self.subTest(field=key), self.assertRaises(ValueError):
                analyze_correction_runs._paired(report, incompatible)



if __name__ == "__main__":
    unittest.main()
