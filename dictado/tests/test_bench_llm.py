"""The LLM benchmark exposes quality and safety metrics without network use."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import bench_llm
from instant2_app import llm


class LLMBenchmarkTests(unittest.TestCase):
    def test_meaning_guard_keeps_verb_forms_as_protected_details(self):
        self.assertIn("cada forma verbal: persona, tiempo y modo",
                      llm.VERIFY_SYSTEM)
        self.assertIn("'cuando llega el paquete' -> 'cuando llegue el paquete'",
                      llm.VERIFY_SYSTEM)
        self.assertNotIn("No rechaces solo porque cambie la conjugación",
                         llm.VERIFY_SYSTEM)

    def test_corpus_references_keep_words_except_explicit_brand_correction(self):
        for case in bench_llm.load_cases()["normal"]:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["input"].strip())
                self.assertTrue(case["expected"].strip())
                if case["id"] == "application_name":
                    self.assertEqual(llm._words(case["expected"])[0], "instant2")
                else:
                    self.assertEqual(llm._words(case["input"]),
                                     llm._words(case["expected"]))

    def test_normal_metrics_use_llm_corrections_and_report_word_changes(self):
        cases = [{"id": "recognition_fix", "input": "el tuvo estuvo listo",
                  "expected": "El tubo estuvo listo."},
                 {"id": "safe", "input": "hola mundo",
                  "expected": "Hola, mundo."}]
        cfg = {"llm_url": "http://127.0.0.1:11434", "llm_model": "test"}
        candidates = iter(["El tubo estuvo listo.", "Hola, mundo."])
        with patch.object(llm, "polish_candidate", side_effect=lambda *_a, **_k: next(candidates)), \
                patch.object(llm, "candidate_preserves_meaning", return_value=True):
            results, latencies = bench_llm.run_normal(cases, cfg, timeout=1.0)
        self.assertTrue(results[0]["word_sequence_changed"])
        self.assertEqual(results[0]["accepted"], "El tubo estuvo listo.")
        self.assertFalse(results[1]["word_sequence_changed"])
        self.assertEqual(len(latencies), 2)

    def test_sampling_profile_reaches_candidate_but_default_stays_unset(self):
        case = [{"id": "sample", "input": "hola mundo",
                 "expected": "Hola, mundo."}]
        cfg = {"llm_url": "http://127.0.0.1:11434", "llm_model": "test"}
        observed = []

        def candidate(*_args, **kwargs):
            observed.append(kwargs.get("sampling_params"))
            return "Hola, mundo."

        with patch.object(llm, "polish_candidate", side_effect=candidate), \
                patch.object(llm, "candidate_preserves_meaning", return_value=True):
            bench_llm.run_normal(case, cfg, timeout=1.0)
            profile = {"temperature": 0.7, "top_p": 0.8,
                       "top_k": 20, "presence_penalty": 1.5}
            bench_llm.run_normal(case, cfg, timeout=1.0, sampling=profile)

        self.assertEqual(observed, [None, profile])

    def test_normal_metrics_flag_verb_form_changes_for_review(self):
        cases = [{"id": "verb_form", "input": "cuando llega el paquete",
                  "expected": "Cuando llega el paquete.",
                  "must_keep": ["cuando llega el paquete"]}]
        cfg = {"llm_url": "http://127.0.0.1:11434", "llm_model": "test"}
        with patch.object(llm, "polish_candidate",
                          return_value="Cuando llegue el paquete."), \
                patch.object(llm, "candidate_preserves_meaning", return_value=False):
            results, _ = bench_llm.run_normal(cases, cfg, timeout=1.0)
        self.assertEqual(results[0]["candidate"], "Cuando llegue el paquete.")
        self.assertEqual(results[0]["accepted"], "cuando llega el paquete")
        self.assertEqual(results[0]["missing"], [])
        self.assertTrue(results[0]["candidate_word_sequence_changed"])
        self.assertFalse(results[0]["meaning_check_passed"])





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


if __name__ == "__main__":
    unittest.main()
