"""The Voice-to-Prompt benchmark reports honest metrics without network use.

Focused on the pieces that a scored run depends on:
- the throughput formula and its missing-metric edges,
- unavailable metrics for the production baseline (text-only API),
- runtime metadata that must not carry a private profile path,
- the frozen corpus/rubric wiring the harness refuses to run without.
"""
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import bench_v2p


def _runner_source():
    with open(bench_v2p.__file__, encoding="utf-8") as handle:
        return handle.read()


class ThroughputTests(unittest.TestCase):
    def test_uses_model_seconds_and_subtracts_ttft(self):
        row = {"completion_tokens": 50, "model_seconds": 2.0, "ttft_seconds": 0.5,
               "wall_seconds": 9.0}
        # generation phase is model_seconds - ttft_seconds, not the wall clock
        self.assertAlmostEqual(bench_v2p.throughput(row), 50 / 1.5)

    def test_row_key_is_the_one_the_runner_writes(self):
        # Regression guard for the bug that made throughput null in real runs:
        # rows carry model_seconds, so nothing may read total_seconds off a row.
        source = _runner_source()
        self.assertIn('"model_seconds": timing.get("total_seconds")', source)
        self.assertIsNone(re.search(r'row\.get\(\s*"total_seconds"', source))
        throughput_body = source.split("def throughput")[1].split("\ndef ")[0]
        self.assertIn('row.get("model_seconds")', throughput_body)
        self.assertIn('row.get("wall_seconds")', throughput_body)

    def test_falls_back_to_wall_seconds_when_the_call_time_is_missing(self):
        row = {"completion_tokens": 40, "model_seconds": None, "wall_seconds": 4.0}
        self.assertAlmostEqual(bench_v2p.throughput(row), 10.0)

    def test_without_ttft_the_whole_duration_is_the_generation_phase(self):
        row = {"completion_tokens": 30, "model_seconds": 3.0, "ttft_seconds": None}
        self.assertAlmostEqual(bench_v2p.throughput(row), 10.0)

    def test_unavailable_and_degenerate_metrics_return_none(self):
        for row in ({"completion_tokens": None, "model_seconds": 1.0},
                    {"completion_tokens": 0, "model_seconds": 1.0},
                    {"completion_tokens": 10},
                    {"completion_tokens": 10, "model_seconds": 0},
                    {"completion_tokens": 10, "model_seconds": -1.0},
                    {"completion_tokens": 10, "model_seconds": None,
                     "wall_seconds": 0}):
            with self.subTest(row=row):
                self.assertIsNone(bench_v2p.throughput(row))

    def test_ttft_not_smaller_than_total_is_ignored(self):
        row = {"completion_tokens": 20, "model_seconds": 2.0, "ttft_seconds": 3.0}
        self.assertAlmostEqual(bench_v2p.throughput(row), 10.0)


class BaselineMetricAvailabilityTests(unittest.TestCase):
    def test_baseline_declares_tokens_ttft_and_finish_reason_unavailable(self):
        self.assertIn("llm.polish_candidate", bench_v2p.BASELINE_METRICS_UNAVAILABLE)
        self.assertIn("TTFT", bench_v2p.BASELINE_METRICS_UNAVAILABLE)

    def test_baseline_does_not_estimate_tokens(self):
        """The production path returns text only: no counters to sum."""
        source = _runner_source()
        baseline = source[source.index("def run_baseline"):source.index("def run_direct")]
        self.assertIn('"prompt_tokens": None', baseline)
        self.assertIn('"completion_tokens": None', baseline)
        self.assertIn('"finish_reasons": []', baseline)
        self.assertNotIn("_sum_tokens", source)

    def test_run_summary_marks_baseline_metrics_unavailable(self):
        rows = [{"checks_apply": False, "error": None, "gold_leaks": [],
                 "finish_reasons": [], "prompt_tokens": None,
                 "completion_tokens": None, "wall_seconds": 1.0,
                 "ttft_seconds": None, "model_seconds": 1.0,
                 "tokens_per_second": None, "checks": {}}]
        summary = bench_v2p._summarise_run("m|baseline|off", "m", "baseline", "off",
                                           rows, {"references_sha256": "x"})
        self.assertFalse(summary["checks_apply"])
        self.assertEqual(summary["metrics_unavailable"],
                         bench_v2p.BASELINE_METRICS_UNAVAILABLE)
        self.assertIsNone(summary["tokens"]["prompt"])

    def test_prompt_strategies_do_not_carry_the_note(self):
        rows = [{"checks_apply": True, "error": None, "gold_leaks": [],
                 "finish_reasons": ["stop"], "prompt_tokens": 10,
                 "completion_tokens": 5, "wall_seconds": 1.0, "ttft_seconds": 0.1,
                 "model_seconds": 0.9, "tokens_per_second": 5.5, "checks": {}}]
        summary = bench_v2p._summarise_run("m|direct|off", "m", "direct", "off",
                                           rows, {"references_sha256": "x"})
        self.assertTrue(summary["checks_apply"])
        self.assertIsNone(summary["metrics_unavailable"])
        self.assertAlmostEqual(summary["tokens_per_second"]["p50"], 5.5)


class RuntimeSnapshotTests(unittest.TestCase):
    def test_basename_handles_both_separators(self):
        self.assertEqual(bench_v2p._basename(r"C:\models\qwen.gguf"), "qwen.gguf")
        self.assertEqual(bench_v2p._basename("/models/qwen.gguf"), "qwen.gguf")
        self.assertEqual(bench_v2p._basename("qwen.gguf"), "qwen.gguf")
        self.assertEqual(bench_v2p._basename(None), "")

    def test_no_absolute_path_survives_sanitisation(self):
        payload = {"model_path": r"C:\Users\someone\AppData\Local\app\models\q.gguf",
                   "model_alias": "qwen3.5:4b-q4_K_M",
                   "default_generation_settings": {"n_ctx": 4096}}
        clean = bench_v2p._sanitise_snapshot_value(payload)
        text = json.dumps(clean)
        self.assertNotIn("Users", text)
        self.assertNotIn("AppData", text)
        self.assertEqual(clean["model_file"], "q.gguf")
        self.assertEqual(clean["model_alias"], "qwen3.5:4b-q4_K_M")
        self.assertEqual(clean["default_generation_settings"]["n_ctx"], 4096)

    def test_sanitisation_keeps_only_settings_and_identifiers(self):
        probe = {"props": {"model_path": "/home/user/models/m.gguf",
                           "build_info": "b11573", "model_alias": "a",
                           "model_ftype": "Q4_K - Medium", "is_sleeping": True,
                           "chat_template": "x" * 50, "ui": {"settings": {}}},
                 "models": {"data": [{"id": "a", "path": "/home/user/models/m.gguf"}]},
                 "summary": {"model_file": "m.gguf", "n_ctx": 4096}}
        text = json.dumps(probe)
        self.assertIn("/home/user", text)  # the probe really contains a path
        clean = {"props": {key: probe["props"][key]
                           for key in bench_v2p._SNAPSHOT_PROPS_KEYS
                           if key in probe["props"]},
                 "models": bench_v2p._sanitise_snapshot_value(probe["models"])}
        clean["props"]["model_file"] = bench_v2p._basename(probe["props"]["model_path"])
        dumped = json.dumps(clean)
        self.assertNotIn("/home/user", dumped)
        self.assertNotIn("chat_template", dumped)
        self.assertEqual(clean["props"]["model_file"], "m.gguf")
        self.assertEqual(clean["models"]["data"][0]["model_file"], "m.gguf")

    def test_portable_path_hides_the_user_profile(self):
        with mock.patch.dict(os.environ, {"USERPROFILE": r"C:\Users\someone"}):
            portable = bench_v2p._portable_path(r"C:\Users\someone\AppData\Local\Temp\x")
        self.assertNotIn("someone", portable)
        repo_file = os.path.join(bench_v2p.HERE, "v2p_cases.json")
        self.assertEqual(bench_v2p._portable_path(repo_file),
                         "dictado/bench/v2p_cases.json")


class GoldLeakAuditTests(unittest.TestCase):
    """The leak audit must fail on injected gold and pass on real material."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {case["id"]: case for case in bench_v2p.load_corpus()["cases"]}

    def _cfg(self):
        return {"llm_url": "http://127.0.0.1:1/v1", "llm_model": "m"}

    def test_clean_assembly_reports_no_leak_for_every_case_and_mode(self):
        for case in self.cases.values():
            for mode in bench_v2p.CONTEXT_MODES:
                for strategy in ("direct", "structured", "legacy_instruction"):
                    with self.subTest(case=case["id"], mode=mode, strategy=strategy):
                        messages = bench_v2p.build_messages(strategy, case, mode, self._cfg())
                        self.assertEqual(
                            bench_v2p.assert_no_gold_leak(case, messages, mode, strategy,
                                                          self._cfg()), [])

    def test_injected_reference_prompt_is_detected(self):
        case = self.cases["ordinary_01"]
        messages = bench_v2p.build_messages("direct", case, "off", self._cfg())
        self.assertEqual(bench_v2p.assert_no_gold_leak(case, messages, "off"), [])
        messages[1]["content"] += " " + case["reference_prompt"]
        found = bench_v2p.assert_no_gold_leak(case, messages, "off")
        self.assertTrue(found)
        self.assertIn(case["reference_prompt"], found)

    def test_injected_forbidden_phrase_is_detected(self):
        case = self.cases["unknown_02"]  # forbidden includes madrugada/domingo
        messages = bench_v2p.build_messages("structured", case, "off", self._cfg())
        self.assertEqual(bench_v2p.assert_no_gold_leak(case, messages, "off",
                                                       "structured"), [])
        messages[1]["content"] += " La ventana de mantenimiento es la madrugada del domingo."
        found = bench_v2p.assert_no_gold_leak(case, messages, "off", "structured")
        self.assertIn("madrugada", found)
        self.assertIn("domingo", found)

    def test_a_phrase_that_comes_from_the_dictation_is_not_flagged(self):
        case = self.cases["ordinary_01"]
        messages = bench_v2p.build_messages("direct", case, "off", self._cfg())
        self.assertIn("informe de ventas", messages[1]["content"])
        self.assertNotIn("informe de ventas",
                         bench_v2p.assert_no_gold_leak(case, messages, "off"))

    def test_audit_ignores_the_fixed_system_prompt(self):
        """System text is harness text: planting gold there is not a leak."""
        case = self.cases["ordinary_01"]
        messages = bench_v2p.build_messages("direct", case, "off", self._cfg())
        messages[0]["content"] += " " + case["reference_prompt"]
        self.assertEqual(bench_v2p.assert_no_gold_leak(case, messages, "off"), [])




class TwoStageStrategyTests(unittest.TestCase):
    @staticmethod
    def _case():
        return next(item for item in bench_v2p.load_corpus()["cases"]
                    if item["id"] == "ordinary_01")

    @staticmethod
    def _args():
        return type("Args", (), {
            "temperature": 0.0, "max_tokens": 512, "seed": 0,
            "top_p": None, "top_k": None, "min_p": None,
            "presence_penalty": None, "repeat_penalty": None,
            "repeat_last_n": None, "frequency_penalty": None, "typical_p": None,
            "structured_mode": "jsonschema", "timeout": 10.0, "stream": True,
        })()

    @staticmethod
    def _call(text, finish_reason="stop"):
        value = {"text": text, "finish_reason": finish_reason,
                 "prompt_tokens": 10, "completion_tokens": 2,
                 "ttft_seconds": 0.1, "total_seconds": 0.5}
        return {"ok": True, "value": value, "seconds": 0.5,
                "error": None, "finish_reason": finish_reason}

    @staticmethod
    def _extraction(explicit_facts=None):
        return json.dumps({
            "intent": "resumir el informe",
            "explicit_facts": explicit_facts or [],
            "conditions": [],
            "restrictions": [],
            "authorized_context_facts": [],
            "unknowns": [],
        })

    def test_context_fact_must_match_its_authorized_provenance(self):
        payload = json.dumps({
            "intent": "preparar el informe",
            "explicit_facts": ["informe"],
            "conditions": [],
            "restrictions": [],
            "authorized_context_facts": [
                {"provenance": "selection", "fact": "El informe se entrega en PDF"}],
            "unknowns": [],
        })
        with self.assertRaisesRegex(ValueError, "procedencia autorizada literal"):
            bench_v2p.parse_two_stage_extraction(
                payload, {"provided_context": ["El informe se entrega en PDF"]},
                "preparar el informe")

    def test_source_matching_normalizes_case_accents_and_punctuation(self):
        payload = json.loads(self._extraction(["LAURA Méndez"]))
        payload["authorized_context_facts"] = [
            {"provenance": "provided_context", "fact": "Entrega, EN PDF!"}]
        parsed = bench_v2p.parse_two_stage_extraction(
            json.dumps(payload),
            {"provided_context": ["La entrega en PDF."]},
            "mandaselo a Laura Mendez, antes del viernes.")
        self.assertEqual(parsed["explicit_facts"], ["LAURA Méndez"])
        self.assertEqual(parsed["authorized_context_facts"][0]["fact"],
                         "Entrega, EN PDF!")

    def test_source_matching_rejects_changed_names_numbers_and_paths(self):
        cases = (
            ("mandaselo a Laura Mendez", "Laura Martinez"),
            ("prepara el informe 2026", "informe 2025"),
            ("abrí /srv/reportes/ventas-2026.csv",
             "/srv/reportes/ventas-2025.csv"),
        )
        for source, fact in cases:
            with self.subTest(source=source, fact=fact):
                with self.assertRaisesRegex(ValueError, "secuencia en el dictado"):
                    bench_v2p.parse_two_stage_extraction(
                        self._extraction([fact]), {}, source)

    def test_context_source_category_and_availability_are_required(self):
        payload = json.dumps({
            "intent": "enviar el informe",
            "explicit_facts": [],
            "conditions": [],
            "restrictions": [],
            "authorized_context_facts": [
                {"provenance": "provided_context", "fact": "Laura"}],
            "unknowns": [],
        })
        for sources in ({}, {"history": ["Laura"]}):
            with self.subTest(sources=sources):
                with self.assertRaisesRegex(ValueError,
                                            "procedencia autorizada literal"):
                    bench_v2p.parse_two_stage_extraction(
                        payload, sources, "mandale eso a laura")

    def test_empty_authorized_context_is_an_empty_array_in_both_stages(self):
        case = next(item for item in bench_v2p.load_corpus()["cases"]
                    if item["id"] == "ambiguous_01")
        extraction_user = bench_v2p.build_two_stage_extraction_messages(
            case, "off")[1]["content"]
        generation_user = bench_v2p.build_two_stage_generation_messages(
            case, "off", {"intent": "comunicar"})[1]["content"]
        for user in (extraction_user, generation_user):
            payload = json.loads(user)
            self.assertEqual(payload["authorized_context"], [])
            self.assertNotIn("(ninguno)", user)
        self.assertEqual(json.loads(extraction_user)["dictation"], case["input"])
        self.assertEqual(json.loads(generation_user)["dictation"], case["input"])

    def test_positive_conditions_and_negative_restrictions_are_separate(self):
        payload = json.loads(self._extraction(["informe"]))
        payload["conditions"] = ["antes del viernes"]
        payload["restrictions"] = ["sin incluir datos personales"]
        parsed = bench_v2p.parse_two_stage_extraction(
            json.dumps(payload), {},
            "prepara el informe antes del viernes sin incluir datos personales")
        self.assertEqual(parsed["conditions"], ["antes del viernes"])
        self.assertEqual(parsed["restrictions"], ["sin incluir datos personales"])

    def test_call_metadata_records_reasoning_flags_without_text(self):
        calls = [
            {"stage": "extract", "ok": True, "value": {"reasoning": "private"}},
            {"stage": "generate", "ok": True, "value": {"text": "final"}},
        ]
        metadata = [bench_v2p._call_metadata(call) for call in calls]
        self.assertEqual([call["reasoning_present"] for call in metadata],
                         [True, False])
        self.assertNotIn('"reasoning":', json.dumps(metadata))
        self.assertNotIn("private", json.dumps(metadata))
        row = {"checks_apply": True, "error": None, "gold_leaks": [], "checks": {},
               "reasoning_present": True, "calls": metadata}
        summary = bench_v2p._summarise_run(
            "m|two_stage|off", "m", "two_stage", "off", [row],
            {"references_sha256": "test"})
        self.assertEqual(summary["reasoning"], {
            "rows_present": 1, "calls_present": 1,
            "by_stage": {"extract": 1, "generate": 0}})

    def test_empty_reasoning_channels_do_not_set_aggregate_flag(self):
        calls = []

        def client(_request):
            if not calls:
                calls.append("extract")
                return self._call(self._extraction())
            calls.append("generate")
            return self._call("Prompt final.")

        outcome = bench_v2p.run_two_stage(
            self._case(), "off", {"llm_url": "http://127.0.0.1:1/v1",
                                  "llm_model": "fixture"},
            self._args(), client)
        self.assertEqual(calls, ["extract", "generate"])
        self.assertIsNone(outcome["reasoning"])

    def test_fabricated_explicit_fact_stops_before_generation(self):
        calls = []

        def client(_request):
            calls.append("extract")
            return self._call(self._extraction(["dos monitores"]))

        outcome = bench_v2p.run_two_stage(
            self._case(), "off", {"llm_url": "http://127.0.0.1:1/v1",
                                  "llm_model": "fixture"},
            self._args(), client)
        self.assertEqual(calls, ["extract"])
        self.assertEqual(outcome["response"], "")
        self.assertIn("secuencia en el dictado", outcome["error"])
        self.assertEqual(len(outcome["calls"]), 1)
        self.assertEqual(outcome["extraction_raw"], self._extraction(["dos monitores"]))

    def test_truncated_extraction_skips_generation(self):
        calls = []

        def client(_request):
            calls.append("extract")
            return self._call("{}", "length")

        outcome = bench_v2p.run_two_stage(
            self._case(), "off", {"llm_url": "http://127.0.0.1:1/v1",
                                  "llm_model": "fixture"},
            self._args(), client)
        self.assertEqual(calls, ["extract"])
        self.assertEqual(outcome["response"], "")
        self.assertFalse(outcome["extraction_ok"])
        self.assertIn("finish_reason=length", outcome["extraction_error"])
        self.assertEqual(outcome["error"], outcome["extraction_error"])
        self.assertEqual(outcome["timing"]["finish_reasons"], ["length"])

    def test_truncated_generation_discards_partial_response(self):
        calls = []

        def client(_request):
            if not calls:
                calls.append("extract")
                return self._call(self._extraction(["informe de ventas de octubre"]))
            calls.append("generate")
            return self._call("Prompt parcial e incompleto", "length")

        outcome = bench_v2p.run_two_stage(
            self._case(), "off", {"llm_url": "http://127.0.0.1:1/v1",
                                  "llm_model": "fixture"},
            self._args(), client)
        self.assertEqual(calls, ["extract", "generate"])
        self.assertEqual(outcome["response"], "")
        self.assertIn("finish_reason=length", outcome["generation_error"])
        self.assertEqual(outcome["error"], outcome["generation_error"])
        self.assertEqual([call["stage"] for call in outcome["calls"]],
                         ["extract", "generate"])
        self.assertEqual(outcome["timing"]["finish_reasons"], ["stop", "length"])
class ContextModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {case["id"]: case for case in bench_v2p.load_corpus()["cases"]}

    def test_oracle_relevant_is_declared_an_upper_bound(self):
        source = _runner_source()
        self.assertIn("UPPER BOUND", source)
        self.assertIn("not a product behavior", source)
        self.assertIn("oracle_relevant", bench_v2p.CONTEXT_MODES)

    def test_oracle_relevant_takes_relevant_context_and_drops_noise(self):
        retrieved = bench_v2p._context_block(self.cases["retrieved_01"], "oracle_relevant")
        self.assertIn("PDF", retrieved)
        self.assertIn("dolares", retrieved)
        relevant = bench_v2p._context_block(self.cases["history_relevant_01"],
                                            "oracle_relevant")
        self.assertIn("Acme Patagonia", relevant)
        irrelevant = bench_v2p._context_block(self.cases["history_irrelevant_01"],
                                              "oracle_relevant")
        self.assertEqual(irrelevant, "")
        authorized = bench_v2p._context_block(self.cases["selection_authorized_01"],
                                              "oracle_relevant")
        self.assertIn("mantenimiento", authorized)
        unauthorized = bench_v2p._context_block(self.cases["selection_absent_01"],
                                                "oracle_relevant")
        self.assertEqual(unauthorized, "")
        self.assertEqual(bench_v2p._context_block(self.cases["unknown_01"],
                                                  "oracle_relevant"), "")

    def test_all_is_the_stress_condition_that_injects_noise(self):
        stressed = bench_v2p._context_block(self.cases["history_irrelevant_01"], "all")
        self.assertIn("cumple", stressed)
        self.assertEqual(bench_v2p._context_block(self.cases["history_irrelevant_01"],
                                                  "off"), "")

    def test_unauthorized_selection_never_reaches_any_mode(self):
        case = self.cases["selection_absent_01"]
        secret = case["selection"]["text"]
        for mode in bench_v2p.CONTEXT_MODES:
            with self.subTest(mode=mode):
                self.assertNotIn(secret, bench_v2p._context_block(case, mode))


class LegacyInstructionControlTests(unittest.TestCase):
    def _cfg(self):
        return {"llm_url": "http://127.0.0.1:1/v1", "llm_model": "m"}

    def test_uses_the_app_instruction_verbatim(self):
        case = bench_v2p.load_corpus()["cases"][0]
        messages = bench_v2p.build_messages("legacy_instruction", case, "off", self._cfg())
        self.assertEqual(messages[0]["content"], bench_v2p.llm.DEFAULT_SYSTEM)

    def test_same_user_turn_as_direct(self):
        case = bench_v2p.load_corpus()["cases"][35]
        legacy = bench_v2p.build_messages("legacy_instruction", case, "provided",
                                          self._cfg())
        direct = bench_v2p.build_messages("direct", case, "provided", self._cfg())
        self.assertEqual(legacy[1]["content"], direct[1]["content"])
        self.assertNotEqual(legacy[0]["content"], direct[0]["content"])

    def test_not_described_as_a_historical_prompt(self):
        source = _runner_source()
        self.assertIn("NOT a historical Voice-to-Prompt prompt", source)
        self.assertIn("not a historical V2P prompt", source)

    def test_prompt_invariants_do_not_apply_and_the_note_says_why(self):
        self.assertFalse(bench_v2p._check_applies("legacy_instruction"))
        self.assertIn("correccion del dictado",
                      bench_v2p._checks_note("legacy_instruction"))
        self.assertIsNone(bench_v2p._checks_note("direct"))

    def test_it_is_a_registered_strategy(self):
        self.assertIn("legacy_instruction", bench_v2p.STRATEGIES)
        self.assertIn("legacy_instruction", bench_v2p.STRATEGY_RUNNERS)


class BaselineReplayTests(unittest.TestCase):
    """The baseline replay must mirror production, not the context factor."""

    def _cfg(self):
        return {"llm_url": "http://127.0.0.1:1/v1", "llm_model": "m"}

    def test_baseline_messages_are_identical_in_every_context_mode(self):
        case = bench_v2p.load_corpus()["cases"][0]
        reference = bench_v2p.build_messages("baseline", case, "off", self._cfg())
        for mode in bench_v2p.CONTEXT_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(
                    bench_v2p.build_messages("baseline", case, mode, self._cfg()),
                    reference)

    def test_baseline_system_message_is_the_production_one(self):
        case = bench_v2p.load_corpus()["cases"][0]
        cfg = self._cfg()
        expected = (bench_v2p.llm.DEFAULT_SYSTEM
                    + "\nGlosario de grafias preferidas (referencia, no contenido):\n"
                    + bench_v2p._glossary(cfg))
        for mode in ("off", "all", "oracle_relevant"):
            with self.subTest(mode=mode):
                messages = bench_v2p.build_messages("baseline", case, mode, cfg)
                self.assertEqual(messages[0]["content"], expected)
                self.assertEqual(messages[1]["content"],
                                 bench_v2p.llm.correct_locally(case["input"], cfg))

    def test_baseline_does_not_inject_user_context(self):
        """Context/project material never reaches the production replay."""
        case = {case["id"]: case for case in bench_v2p.load_corpus()["cases"]}[
            "selection_authorized_01"]
        messages = bench_v2p.build_messages("baseline", case, "all", self._cfg())
        self.assertNotIn(case["selection"]["text"], messages[1]["content"])
        self.assertNotIn(case["selection"]["text"], messages[0]["content"])

    def test_audited_baseline_messages_equal_the_executed_production_request(self):
        """The messages the harness audits must be the ones actually sent.

        Captures the real body produced by ``llm.polish_candidate`` (the
        production call) and compares it with ``build_messages`` for every
        context mode. This closes the gap that let a gated glossary slip in: the
        audited assembly and the executed request are now the same bytes.
        """
        case = bench_v2p.load_corpus()["cases"][0]
        cfg = self._cfg()
        captured = {}

        class _Response:
            status = 200

            def read(self):
                return json.dumps({"choices": [{"finish_reason": "stop",
                                                "message": {"content": "x"}}]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def _fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        with mock.patch("urllib.request.urlopen", _fake_urlopen):
            bench_v2p.llm.polish_candidate(
                bench_v2p.llm.correct_locally(case["input"], cfg),
                cfg["llm_url"], timeout=1.0, context_terms=bench_v2p._glossary(cfg),
                cfg=cfg)
        executed = captured["body"]["messages"]
        self.assertTrue(executed[0]["content"].endswith(bench_v2p._glossary(cfg)))
        for mode in bench_v2p.CONTEXT_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(
                    bench_v2p.build_messages("baseline", case, mode, cfg), executed)

    def test_baseline_glossary_is_not_gated_on_the_context_mode(self):
        source = _runner_source()
        self.assertIn("terms = _glossary(cfg)", source)
        self.assertNotIn('_glossary(cfg) if context_mode in ("glossary", "all")', source)


class SnapshotProjectionTests(unittest.TestCase):
    def test_generation_defaults_keep_only_sampler_values(self):
        props = {"model_path": r"C:\Users\someone\m.gguf",
                 "chat_template": "x" * 100,
                 "total_slots": 1,
                 "default_generation_settings": {
                     "n_ctx": 4096, "n_parallel": 1,
                     "params": {"temperature": 0.8, "top_p": 0.95, "top_k": 40,
                                "min_p": 0.05, "repeat_penalty": 1.0,
                                "repeat_last_n": 64, "presence_penalty": 0.0,
                                "frequency_penalty": 0.0, "typical_p": 1.0,
                                "n_predict": -1,
                                "seed": 4294967295, "dry_multiplier": 0.0}}}
        defaults = bench_v2p._generation_defaults(props)
        self.assertEqual(sorted(defaults), sorted(bench_v2p._GENERATION_DEFAULT_KEYS))
        self.assertEqual(defaults["top_p"], 0.95)
        self.assertNotIn("seed", defaults)
        self.assertNotIn("dry_multiplier", defaults)
        summary = bench_v2p._snapshot_summary(props)
        self.assertEqual(summary["n_ctx"], 4096)
        self.assertEqual(summary["n_parallel"], 1)
        self.assertEqual(summary["model_file"], "m.gguf")
        self.assertNotIn("model_path", summary)
        self.assertNotIn("x" * 100, json.dumps(summary))  # no raw payload blobs
        self.assertEqual(summary["generation_defaults"], defaults)

    def test_inherited_sampler_keys_are_declared(self):
        for key in ("top_p", "top_k", "min_p", "presence_penalty"):
            self.assertIn(key, bench_v2p._INHERITED_SAMPLER_KEYS)
        self.assertNotIn("temperature", bench_v2p._INHERITED_SAMPLER_KEYS)

    def test_summary_survives_a_server_without_defaults(self):
        summary = bench_v2p._snapshot_summary({})
        self.assertIsNone(summary["n_ctx"])
        self.assertEqual(summary["generation_defaults"], {})
        self.assertEqual(summary["model_file"], "")  # unknown, never a path


class SamplerOverrideTests(unittest.TestCase):
    def test_explicit_sampler_values_are_sent_in_request(self):
        args = bench_v2p.build_parser().parse_args([
            "--top-p", "0.80", "--top-k", "20", "--min-p", "0",
            "--presence-penalty", "1.5", "--repeat-penalty", "1",
            "--repeat-last-n", "64", "--frequency-penalty", "0",
            "--typical-p", "1"])
        body = bench_v2p._body({"llm_model": "model"}, [], args)
        request = bench_v2p._request("http://127.0.0.1/v1/chat/completions",
                                     body, 10)
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual({key: sent[key] for key in (
            "top_p", "top_k", "min_p", "presence_penalty", "repeat_penalty",
            "repeat_last_n", "frequency_penalty", "typical_p")}, {
                "top_p": 0.8, "top_k": 20, "min_p": 0.0,
                "presence_penalty": 1.5, "repeat_penalty": 1.0,
                "repeat_last_n": 64, "frequency_penalty": 0.0,
                "typical_p": 1.0})

    def test_unspecified_sampler_values_remain_inherited(self):
        args = bench_v2p.build_parser().parse_args([])
        body = bench_v2p._body({"llm_model": "model"}, [], args)
        request = bench_v2p._request("http://127.0.0.1/v1/chat/completions",
                                     body, 10)
        sent = json.loads(request.data.decode("utf-8"))
        for key in bench_v2p._INHERITED_SAMPLER_KEYS:
            self.assertNotIn(key, sent)
        self.assertEqual(bench_v2p._sampler_overrides(args), {})


class FrozenWiringTests(unittest.TestCase):
    def test_runner_hash_is_reported(self):
        digest = bench_v2p.runner_digest()
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in digest))

    def test_corpus_and_rubric_digests_are_available(self):
        digests = bench_v2p.corpus_digests()
        self.assertEqual(digests["case_count"], 72)
        self.assertEqual(len(digests["references_sha256"]), 64)
        self.assertEqual(len(bench_v2p.rubric_digest()), 64)
        self.assertEqual(len(bench_v2p.prescoring_digest()), 64)

    def test_frozen_manifest_matches_the_corpus_and_rubric(self):
        self.assertEqual(bench_v2p.verify_manifest(), [])

    def test_reference_prompts_satisfy_their_own_invariants(self):
        cases = bench_v2p.load_corpus()["cases"]
        self.assertEqual(bench_v2p.gold_self_check(cases), [])


if __name__ == "__main__":
    unittest.main()
