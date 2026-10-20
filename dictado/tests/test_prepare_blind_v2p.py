"""The blind package must be deduplicated, complete and identity-free.

Builds a synthetic results tree over real frozen-corpus cases and checks the
dedup key, the mapping completeness, the task context actually sent (empty for
the production baseline, the runner's own block otherwise), the privacy rules of
each artifact, and the integrity guard.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))

import bench_v2p
import prepare_blind_v2p as pb

CASES = {case["id"]: case for case in bench_v2p.load_corpus()["cases"]}
PLAIN = "ordinary_01"            # no provided context
RETRIEVED = "retrieved_01"       # has provided_context
NOISY = "history_irrelevant_01"  # has an irrelevant turn
UNAUTHORIZED = "selection_absent_01"  # selection present but not authorized
CASE_IDS = (PLAIN, RETRIEVED, NOISY, UNAUTHORIZED)


def row(case_id, strategy, context_mode, model, response):
    return {
        "run_id": f"{model}|{strategy}|{context_mode}",
        "case_id": case_id,
        "category": CASES[case_id]["category"],
        "model": model,
        "strategy": strategy,
        "context_mode": context_mode,
        "response": response,
        "checks_apply": strategy != "baseline",
        "checks": {},
        "error": None,
        "gold_leaks": [],
    }


def build_tree(root, responses=None):
    """Two models x two reps x (baseline, direct) x (off, all)."""
    responses = responses or {}
    for model, model_id in (("model-one", "m-one"), ("model-two", "m-two")):
        for rep in ("rep1", "rep2"):
            rep_dir = os.path.join(root, model, rep)
            os.makedirs(rep_dir, exist_ok=True)
            rows = []
            for case_id in CASE_IDS:
                for strategy in ("baseline", "direct"):
                    for context_mode in ("off", "all"):
                        text = responses.get((case_id, strategy, context_mode, rep),
                                             f"respuesta de {case_id}")
                        rows.append(row(case_id, strategy, context_mode, model, text))
            with open(os.path.join(rep_dir, "rows.jsonl"), "w", encoding="utf-8") as handle:
                for item in rows:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            metadata = {
                "scored": True,
                "runner": {"sha256": "a" * 64},
                "matrix": {"models": [model_id], "strategies": ["baseline", "direct"],
                           "context_modes": ["off", "all"], "cases": len(CASE_IDS)},
                "runtime": {"summary": {"model_alias": model_id}},
            }
            with open(os.path.join(rep_dir, "metadata.json"), "w", encoding="utf-8") as handle:
                json.dump(metadata, handle, ensure_ascii=False, indent=2)
    return root


class PackageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.results = os.path.join(self._tmp.name, "results")
        os.makedirs(self.results)
        self.out = os.path.join(self._tmp.name, "blind")
        build_tree(self.results)
        self.package = pb.build_package(self.results, self.out, seed=7)
        self.review = pb.read_jsonl(self.package["review"])
        self.key = pb.read_json(os.path.join(self.out, pb.KEY_NAME))
        self.manifest = pb.read_json(os.path.join(self.out, pb.MANIFEST_NAME))
        self.by_id = {entry["row_id"]: entry for entry in self.key["rows"]}

    def test_internal_extraction_debug_text_is_not_in_blind_review(self):
        self.assertNotIn("extraction_raw", pb.REVIEW_FIELDS)
        self.assertTrue(all("extraction_raw" not in row for row in self.review))

    def tearDown(self):
        self._tmp.cleanup()


class DedupTests(PackageTestCase):
    def test_identical_variants_collapse_and_map_to_every_source(self):
        # baseline ignores context, so off and all produce the SAME variant:
        # 2 models x 2 reps x 2 context modes = 8 mappings, one blind row.
        baseline_rows = [entry for entry in self.key["rows"]
                         if any(m["strategy"] == "baseline" for m in entry["mapped"])]
        self.assertTrue(baseline_rows)
        for entry in baseline_rows:
            baseline_maps = [m for m in entry["mapped"] if m["strategy"] == "baseline"]
            self.assertEqual(len(baseline_maps), 8)
            self.assertEqual({m["context_mode"] for m in baseline_maps}, {"off", "all"})

    def test_every_source_row_is_mapped_exactly_once(self):
        self.assertEqual(sum(len(entry["mapped"]) for entry in self.key["rows"]),
                         len(CASE_IDS) * 2 * 2 * 2 * 2)  # cases x strategies x modes x reps x models

    def test_distinct_variants_are_preserved(self):
        results = os.path.join(self._tmp.name, "results-var")
        os.makedirs(results)
        build_tree(results, responses={(RETRIEVED, "direct", "off", "rep2"):
                                       "variante distinta"})
        package = pb.build_package(results, os.path.join(self._tmp.name, "blind-var"),
                                   seed=7)
        review = pb.read_jsonl(package["review"])
        variants = [row for row in review if row["case_id"] == RETRIEVED]
        self.assertGreaterEqual(len(variants), 2)
        self.assertIn("variante distinta", {row["response"] for row in variants})

    def test_empty_response_variants_are_kept_and_counted(self):
        results = os.path.join(self._tmp.name, "results-empty")
        os.makedirs(results)
        build_tree(results, responses={(NOISY, "direct", "off", "rep1"): "",
                                       (NOISY, "direct", "off", "rep2"): ""})
        package = pb.build_package(results, os.path.join(self._tmp.name, "blind-empty"),
                                   seed=7)
        review = pb.read_jsonl(package["review"])
        empty = [row for row in review if row["response"] == ""]
        self.assertEqual(len(empty), 1)
        self.assertEqual(package["report"]["counts"]["empty_response_variants"], 1)

    def test_row_ids_are_unique_random_and_shuffled(self):
        ids = [row["row_id"] for row in self.review]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(all(item.startswith("blind-") for item in ids))
        self.assertEqual([entry["row_id"] for entry in self.key["rows"]], ids)

    def test_review_and_key_are_separate_files(self):
        self.assertNotEqual(self.package["review"], self.package["key"])
        with open(self.package["key"], encoding="utf-8") as handle:
            key_text = handle.read()
        self.assertIn("mapped", key_text)


class TaskContextTests(PackageTestCase):
    def test_baseline_rows_have_empty_authorized_context(self):
        for row in self.review:
            mapped = self.by_id[row["row_id"]]["mapped"]
            if all(item["strategy"] == "baseline" for item in mapped):
                with self.subTest(case=row["case_id"]):
                    self.assertEqual(row["authorized_context"], "")

    def test_baseline_gets_no_context_even_when_the_corpus_has_it(self):
        """retrieved_01 carries provided_context; the baseline still sees none."""
        variants = [row for row in self.review
                    if any(item["strategy"] == "baseline"
                           for item in self.by_id[row["row_id"]]["mapped"])]
        self.assertTrue(variants)
        for row in variants:
            with self.subTest(case=row["case_id"]):
                self.assertEqual(row["authorized_context"], "")
        with_context = [row for row in self.review
                        if row["case_id"] == RETRIEVED
                        and any(item["strategy"] != "baseline"
                                and item["context_mode"] == "all"
                                for item in self.by_id[row["row_id"]]["mapped"])]
        self.assertTrue(with_context)
        for row in with_context:
            self.assertTrue(row["authorized_context"])

    def test_other_strategies_carry_the_runner_context_block(self):
        for row in self.review:
            for item in self.by_id[row["row_id"]]["mapped"]:
                if item["strategy"] == "baseline":
                    continue
                expected = bench_v2p._context_block(CASES[row["case_id"]],
                                                    item["context_mode"])
                with self.subTest(case=row["case_id"], mode=item["context_mode"]):
                    self.assertEqual(row["authorized_context"], expected)

    def test_unauthorized_selection_never_reaches_the_context(self):
        case = CASES[UNAUTHORIZED]
        secret = case["selection"]["text"]
        for row in self.review:
            if row["case_id"] == UNAUTHORIZED:
                with self.subTest(row=row["row_id"]):
                    self.assertNotIn(secret, row["authorized_context"])


class LexicalReferenceTests(PackageTestCase):
    """The fixed spelling glossary is separate evidence from task context."""

    def _cfg(self, model_id="m-one"):
        return {"llm_url": "http://127.0.0.1:8080/v1", "llm_model": model_id}

    def _variants(self, case_id):
        return [row for row in self.review if row["case_id"] == case_id]

    def test_baseline_carries_the_glossary_in_every_context_mode(self):
        rows = [row for row in self.review
                if any(item["strategy"] == "baseline"
                       for item in self.by_id[row["row_id"]]["mapped"])]
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(case=row["case_id"]):
                self.assertTrue(row["lexical_reference"])
                self.assertTrue(row["lexical_reference"].startswith("Glosario tecnico"))

    def test_glossary_matches_the_runner_helper_and_has_no_profile_terms(self):
        row = [item for item in self.review
               if any(m["strategy"] == "baseline" for m in self.by_id[item["row_id"]]["mapped"])][0]
        expected = bench_v2p._glossary(self._cfg())
        self.assertEqual(row["lexical_reference"], expected)
        # no profile / custom vocabulary can leak in: cfg carries endpoint + id only
        self.assertEqual(bench_v2p._glossary(self._cfg("otro-modelo")), expected)

    def test_direct_and_legacy_get_it_only_in_the_modes_that_include_it(self):
        for strategy in ("direct", "legacy_instruction"):
            with_glossary = pb.lexical_reference(self._cfg(), strategy, "all")
            without = pb.lexical_reference(self._cfg(), strategy, "off")
            self.assertTrue(with_glossary)
            self.assertEqual(without, "")

    def test_structured_never_carries_it(self):
        for mode in ("off", "all", "glossary", "oracle_relevant"):
            with self.subTest(mode=mode):
                self.assertEqual(pb.lexical_reference(self._cfg(), "structured", mode), "")

    def test_lexical_reference_is_part_of_the_dedupe_identity(self):
        """Same case, same response, same task context, different glossary -> 2 rows."""
        case = CASES[PLAIN]
        without = pb.variant_key(case, "direct", "off", "misma respuesta", "")
        with_glossary = pb.variant_key(case, "direct", "off", "misma respuesta",
                                       pb.lexical_reference(self._cfg(), "direct", "all"))
        self.assertNotEqual(without, with_glossary)
        self.assertEqual(len({without, with_glossary}), 2)
        # and an identical glossary does not split the variant
        again = pb.variant_key(case, "direct", "off", "misma respuesta",
                               pb.lexical_reference(self._cfg(), "direct", "all"))
        self.assertEqual(with_glossary, again)

    def test_instructions_label_it_as_spelling_only(self):
        with open(self.package["instructions"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("lexical_reference", text)
        self.assertIn("SOLO referencia de ortografía", text)
        self.assertIn("no es un dato del pedido", text)

    def test_manifest_counts_rows_with_a_glossary(self):
        self.assertIn("rows_with_lexical_reference", self.manifest["counts"])
        self.assertGreater(self.manifest["counts"]["rows_with_lexical_reference"], 0)
        self.assertLessEqual(self.manifest["counts"]["rows_with_lexical_reference"],
                             self.manifest["counts"]["blind_rows"])


class PrivacyTests(PackageTestCase):
    def test_review_rows_carry_only_the_allowed_fields(self):
        for row in self.review:
            self.assertEqual(tuple(row.keys()), pb.REVIEW_FIELDS)
            self.assertIn("lexical_reference", pb.REVIEW_FIELDS)
            self.assertIsNone(row["ratings"]["fidelity"])
            self.assertEqual(row["critical_errors"], [])

    def test_no_configuration_identity_in_authored_fields(self):
        tokens = pb.identity_tokens(self.package["stats"], CASES)
        pb.assert_no_identity(self.package["review"], tokens, fields_only=True)

    def test_instructions_and_manifest_are_identity_free(self):
        tokens = pb.identity_tokens(self.package["stats"], CASES)
        pb.assert_no_identity(self.package["instructions"], tokens)
        pb.assert_no_identity(self.package["manifest"], tokens)
        blob = json.dumps(self.manifest, ensure_ascii=False).lower()
        for token in ("baseline", "direct", "oracle_relevant", "model-one", "m-one"):
            self.assertNotIn(token, blob)

    def test_identity_check_catches_an_injected_configuration(self):
        injected = os.path.join(self._tmp.name, "injected.jsonl")
        with open(injected, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"row_id": "x", "case_id": PLAIN,
                                     "category": "ordinary", "input": "direct",
                                     "authorized_context": "", "reference_prompt": "p",
                                     "response": "r", "ratings": {},
                                     "critical_errors": []}) + "\n")
        with self.assertRaises(AssertionError):
            pb.assert_no_identity(injected, ("direct",), fields_only=True)

    def test_identity_check_catches_an_extra_field(self):
        injected = os.path.join(self._tmp.name, "extra.jsonl")
        with open(injected, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"row_id": "x", "strategy": "direct"}) + "\n")
        with self.assertRaises(AssertionError):
            pb.assert_no_identity(injected, (), fields_only=True)


class ManifestTests(PackageTestCase):
    def test_manifest_proves_hashes_and_counts_without_conditions(self):
        self.assertIn("blinded_review_v2p.jsonl", self.manifest["files_sha256"])
        self.assertIn("blinded_key_v2p.json", self.manifest["files_sha256"])
        self.assertIn("blind_evaluator_instructions.txt", self.manifest["files_sha256"])
        for name, digest in self.manifest["files_sha256"].items():
            self.assertEqual(digest,
                             pb.sha256_file(os.path.join(self.out, name)))
        self.assertEqual(self.manifest["corpus"]["case_count"], len(CASES))
        self.assertEqual(self.manifest["corpus"]["references_sha256"],
                         bench_v2p.corpus_digests()["references_sha256"])
        counts = self.manifest["counts"]
        self.assertEqual(counts["blind_rows"], len(self.review))
        self.assertEqual(counts["cases_mapped"], len(CASE_IDS))
        self.assertEqual(counts["mapped_entries"], 64)
        self.assertEqual(self.manifest["runner_sha256"], ["a" * 64])
        self.assertNotIn("conditions", self.manifest)

    def test_instructions_name_the_rubric_but_no_configuration(self):
        with open(self.package["instructions"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(bench_v2p.rubric_digest(), text)
        self.assertIn("critical_errors", text)
        self.assertIn("rechaza la respuesta", text)


class MainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.results = os.path.join(self._tmp.name, "results")
        os.makedirs(self.results)

    def tearDown(self):
        self._tmp.cleanup()

    def test_main_writes_the_package_and_returns_zero(self):
        build_tree(self.results)
        out = os.path.join(self._tmp.name, "blind")
        code = pb.main(["--results-dir", self.results, "--out-dir", out])
        self.assertEqual(code, pb.EXIT_OK)
        for name in (pb.REVIEW_NAME, pb.KEY_NAME, pb.MANIFEST_NAME, pb.INSTRUCTIONS_NAME):
            self.assertTrue(os.path.isfile(os.path.join(out, name)), name)

    def test_models_filter_limits_the_sources(self):
        build_tree(self.results)
        package = pb.build_package(self.results, os.path.join(self._tmp.name, "one"),
                                   seed=1, model_filter={"model-one"})
        self.assertEqual(package["report"]["counts"]["sources_read"], 2)

    def test_empty_results_tree_is_an_integrity_error(self):
        code = pb.main(["--results-dir", self.results,
                        "--out-dir", os.path.join(self._tmp.name, "blind")])
        self.assertEqual(code, pb.EXIT_INTEGRITY)


if __name__ == "__main__":
    unittest.main()
