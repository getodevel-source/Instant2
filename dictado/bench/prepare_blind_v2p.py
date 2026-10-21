"""Build ONE blind qualitative package for the Voice-to-Prompt benchmark.

Takes completed ``<model>/rep*/rows.jsonl`` plus the frozen corpus and produces:

- ``blinded_review_v2p.jsonl`` - one row per DISTINCT output variant, with the
  dictation, the task context actually sent, the frozen reference prompt and the
  response. No model, strategy or context identity, and no output mapping.
- ``blinded_key_v2p.json`` - the private map. Each blind row points to EVERY
  (model, strategy, context, replicate) that produced that exact variant, so the
  key can be joined back per configuration without exposing anything in review.
- ``blind_manifest.json`` - privacy-safe provenance: file hashes, corpus and
  rubric digests, case count, row count, harness revisions. No conditions.
- ``blind_evaluator_instructions.txt`` - how to rate, taken from the frozen
  rubric. It names critical error classes and dimensions, never a configuration.

Deduplication key is (case, exact response, frozen reference, authorized task
context). Exact duplicates across repeats and configurations collapse into one
blind row mapped to all of them; every distinct variant survives, including
unstable ones and empty responses, so nothing that differs gets skipped.

``authorized_context`` is the context the model ACTUALLY received:

- ``baseline``: empty. Production correction gets the transcript plus a fixed
  lexical glossary only, never corpus project, history or selection material.
- every other strategy: the runner's own ``_context_block`` for that context
  mode (``oracle_relevant`` therefore injects only authored relevant context).

The tool never calls a model, never opens scores, and only reads the results
tree; it writes the four package files into ``--out-dir``.

Usage:
    python bench/prepare_blind_v2p.py --results-dir bench/results/v2p-2026-10-16 \
        --models qwen-q4-k-m-revC,bonsai2-2b-q2-0-revC --out-dir C:/Temp/blind

Exit codes: 0 ok, 2 usage problem, 3 integrity problem (frozen hash mismatch or
no variants found).
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import aggregate_v2p  # noqa: E402 - discovery is shared with the aggregator
import bench_v2p  # noqa: E402 - frozen corpus, rubric and _context_block

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INTEGRITY = 3

REVIEW_NAME = "blinded_review_v2p.jsonl"
KEY_NAME = "blinded_key_v2p.json"
MANIFEST_NAME = "blind_manifest.json"
INSTRUCTIONS_NAME = "blind_evaluator_instructions.txt"

REVIEW_FIELDS = ("row_id", "case_id", "category", "input", "authorized_context",
                 "lexical_reference", "reference_prompt", "response", "ratings",
                 "critical_errors")
# Strategies and modes that receive the fixed spelling glossary. The baseline
# always does (production correction sends it); the redesigned prompt and the
# legacy control only in the modes that include it.
LEXICAL_STRATEGIES = ("direct", "legacy_instruction")
LEXICAL_MODES = ("glossary", "all")
IDENTITY_TOKENS = ("baseline", "legacy_instruction", "direct", "structured",
                   "two_stage", "off", "all", "glossary", "provided", "history",
                   "selection", "oracle_relevant")


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def authorized_context(case, strategy, context_mode):
    """The task context actually sent for this row (empty for the baseline)."""
    if strategy == "baseline":
        return ""
    return bench_v2p._context_block(case, context_mode)


def benchmark_cfg(metadata):
    """Same cfg the runner used for the glossary: endpoint + model id only.

    No profile and no custom vocabulary: ``context.correction_context`` returns
    nothing for a cfg without context profiles, so the glossary is the fixed
    general dictionary.
    """
    options = metadata.get("options") or {}
    model_ids = (metadata.get("matrix") or {}).get("models") or []
    return {"llm_url": options.get("url") or "",
            "llm_model": model_ids[0] if model_ids else ""}


def lexical_reference(cfg, strategy, context_mode):
    """Fixed spelling glossary actually sent to the model, or "" when none.

    It is reference for spelling only, never task facts: a term that appears here
    and nowhere in the dictation is not a datum of the request.
    """
    if strategy == "baseline":
        return bench_v2p._glossary(cfg)
    if strategy in LEXICAL_STRATEGIES and context_mode in LEXICAL_MODES:
        return bench_v2p._glossary(cfg)
    return ""


def variant_key(case, strategy, context_mode, response, lexical=""):
    return (case["id"], response, case["reference_prompt"],
            authorized_context(case, strategy, context_mode), lexical)


def collect_variants(results_dir, cases, model_filter=None):
    """Distinct output variants, each mapped to every source that produced it."""
    variants = {}
    stats = {"sources": 0, "rows_read": 0, "rows_unknown_case": 0,
             "empty_responses": 0, "runner_digests": set(), "models": {}}
    models = aggregate_v2p.discover_models(results_dir)
    for model_dir, reps in sorted(models.items()):
        name = os.path.basename(model_dir.rstrip("\\/"))
        if model_filter and name not in model_filter:
            continue
        for rep, rep_dir in sorted(reps.items()):
            metadata = read_json(os.path.join(rep_dir, "metadata.json"))
            cfg = benchmark_cfg(metadata)
            runner = (metadata.get("runner") or {}).get("sha256")
            if runner:
                stats["runner_digests"].add(runner)
            entry = stats["models"].setdefault(name, {"model_ids": set(), "reps": []})
            entry["model_ids"].update((metadata.get("matrix") or {}).get("models") or [])
            entry["reps"].append(rep)
            stats["sources"] += 1
            for row in read_jsonl(os.path.join(rep_dir, "rows.jsonl")):
                stats["rows_read"] += 1
                case = cases.get(row.get("case_id"))
                if case is None:
                    stats["rows_unknown_case"] += 1
                    continue
                response = row.get("response") or ""
                strategy = row.get("strategy") or ""
                context_mode = row.get("context_mode") or ""
                if not response.strip():
                    stats["empty_responses"] += 1
                lexical = lexical_reference(cfg, strategy, context_mode)
                key = variant_key(case, strategy, context_mode, response, lexical)
                item = variants.setdefault(key, {
                    "case_id": case["id"],
                    "category": case.get("category", ""),
                    "input": case["input"],
                    "authorized_context": key[3],
                    "lexical_reference": key[4],
                    "reference_prompt": case["reference_prompt"],
                    "response": response,
                    "mapped": [],
                })
                item["mapped"].append({"model": name, "strategy": strategy,
                                       "context_mode": context_mode, "rep": rep})
    return variants, stats


def write_review_and_key(variants, out_dir, seed, rubric):
    """Shuffled review rows with safe ids, plus the private map."""
    dimensions = [item["id"] for item in
                  (rubric.get("ratings") or {}).get("dimensions") or []
                  if isinstance(item, dict)]
    entries = list(variants.values())
    rng = random.Random(seed)
    rng.shuffle(entries)
    ids, used = [], set()
    for _ in entries:
        row_id = "blind-" + "".join(rng.choice("0123456789abcdef") for _ in range(10))
        while row_id in used:
            row_id = "blind-" + "".join(rng.choice("0123456789abcdef") for _ in range(10))
        used.add(row_id)
        ids.append(row_id)

    review_path = os.path.join(out_dir, REVIEW_NAME)
    with open(review_path, "w", encoding="utf-8") as handle:
        for row_id, entry in zip(ids, entries):
            handle.write(json.dumps({
                "row_id": row_id,
                "case_id": entry["case_id"],
                "category": entry["category"],
                "input": entry["input"],
                "authorized_context": entry["authorized_context"],
                "lexical_reference": entry["lexical_reference"],
                "reference_prompt": entry["reference_prompt"],
                "response": entry["response"],
                "ratings": {dimension: None for dimension in dimensions},
                "critical_errors": [],
            }, ensure_ascii=False) + "\n")

    key = {
        "schema": "instant2-v2p-blind-key/1",
        "seed": seed,
        "rows": [{"row_id": row_id, "case_id": entry["case_id"],
                  "variant_sha256": sha256_text(entry["response"]),
                  "mapped": entry["mapped"]}
                 for row_id, entry in zip(ids, entries)],
    }
    key_path = os.path.join(out_dir, KEY_NAME)
    with open(key_path, "w", encoding="utf-8") as handle:
        json.dump(key, handle, ensure_ascii=False, indent=2)
    return review_path, key_path, ids, entries, dimensions


def write_instructions(out_dir, rubric, review_path, dimensions):
    """Evaluator guidance: rubric only, never a configuration."""
    critical = [f"- {item.get('id')}: {item.get('label')}"
                for item in rubric.get("critical_errors") or []
                if isinstance(item, dict)]
    lines = [
        "Instrucciones para puntuar el paquete ciego de Voice-to-Prompt",
        "",
        "Qué es: cada línea de %s es una respuesta distinta a un pedido dictado." % REVIEW_NAME,
        "No hay ninguna etiqueta de modelo, estrategia ni condición: a propósito.",
        "No abras %s hasta terminar todos los puntajes." % KEY_NAME,
        "",
        "Campos de cada fila:",
        "- row_id: identificador aleatorio de la fila.",
        "- case_id y category: el caso del corpus y su categoría (para agrupar).",
        "- input: el dictado tal como entró al modelo.",
        "- authorized_context: el contexto de tarea que el modelo realmente recibió",
        "  en esa corrida (material de proyecto, de historial o texto seleccionado",
        "  autorizado, según el caso). Vacío significa que no recibió contexto de tarea.",
        "- lexical_reference: el glosario fijo de grafías que esa corrida recibió.",
        "  Es SOLO referencia de ortografía, no datos de la tarea: un término que",
        "  aparezca acá y no en el dictado no es un dato del pedido, y usarlo como",
        "  dato es un error. Vacío significa que esa corrida no recibió glosario.",
        "- reference_prompt: el prompt de referencia escrito a mano (gold congelado).",
        "- response: lo que produjo el modelo.",
        "- ratings: completá cada dimensión con 1 a 5.",
        "- critical_errors: lista de códigos de error crítico detectados.",
        "",
        "Escala 1-5 por dimensión (%s):" % ", ".join(dimensions),
        rubric.get("ratings", {}).get("scale", "1 = inaceptable, 5 = fiel y usable"),
        "Anclas por dimensión: ver v2p_rubric.json (version %s, sha256 %s)."
        % (rubric.get("version"), bench_v2p.rubric_digest()),
        "",
        "Errores críticos (cualquiera rechaza la respuesta, sin importar el estilo):",
    ]
    lines.extend(critical)
    lines.extend([
        "",
        "Gates congelados:",
        "- Cualquier error crítico rechaza la respuesta.",
        "- Una respuesta es viable solo si no tiene errores críticos y las siete",
        "  dimensiones quedan en 4 o más.",
        "- Si ninguna respuesta del paquete es viable, el resultado es 'sin ganador'.",
        "",
        "Si la respuesta está vacía, puntuá 1 en todas las dimensiones y anotá el",
        "fallo en critical_errors solo si corresponde a una clase de la lista.",
        "Devolvé el mismo JSONL con ratings y critical_errors completos.",
        "",
    ])
    path = os.path.join(out_dir, INSTRUCTIONS_NAME)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


def write_manifest(out_dir, paths, stats, seed, cases, rubric, extra):
    manifest = {
        "schema": "instant2-v2p-blind-manifest/1",
        "generated_at": extra["generated_at"],
        "seed": seed,
        "files_sha256": {os.path.basename(path): sha256_file(path)
                         for path in paths},
        "corpus": {
            "references_sha256": extra["references_sha256"],
            "corpus_file_sha256": extra["corpus_file_sha256"],
            "case_count": len(cases),
        },
        "rubric": {"version": rubric.get("version"),
                   "sha256": bench_v2p.rubric_digest(),
                   "prescoring_sha256": bench_v2p.prescoring_digest()},
        "counts": {
            "blind_rows": extra["blind_rows"],
            "distinct_variants": extra["blind_rows"],
            "cases_mapped": extra["cases_mapped"],
            "sources_read": stats["sources"],
            "rows_read": stats["rows_read"],
            "rows_unknown_case": stats["rows_unknown_case"],
            "empty_response_variants": extra["empty_response_variants"],
            "rows_with_lexical_reference": extra["rows_with_lexical_reference"],
            "mapped_entries": extra["mapped_entries"],
        },
        "runner_sha256": sorted(stats["runner_digests"]),
        "privacy": ("sin modelos, sin estrategias y sin condiciones: solo hashes, "
                    "recuentos y revisión del arnés"),
    }
    path = os.path.join(out_dir, MANIFEST_NAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return path, manifest


def identity_tokens(stats, cases, model_filter=None):
    """Strings that must not appear in the review, instructions or manifest."""
    tokens = set(IDENTITY_TOKENS)
    for name, entry in stats["models"].items():
        if model_filter and name not in model_filter:
            continue
        tokens.add(name)
        tokens.update(entry["model_ids"])
    return sorted(token for token in tokens if token)


def assert_no_identity(path, tokens, fields_only=False):
    """Structural + word-boundary check that no configuration leaked.

    On the review file the check covers the fields we author (row_id, case_id,
    category, input, authorized_context, reference_prompt) and the field NAMES,
    but never the response text: a model output that happens to contain the word
    "direct" is not a harness leak.
    """
    if fields_only:
        scanned = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                extra = set(row) - set(REVIEW_FIELDS)
                if extra:
                    raise AssertionError(
                        f"campos no permitidos en la fila ciega: {sorted(extra)}")
                scanned.append(" ".join(str(row.get(key, "")) for key in REVIEW_FIELDS
                                        if key not in ("response", "ratings",
                                                       "critical_errors")))
        text = "\n".join(scanned)
    else:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    found = [token for token in tokens
             if re.search(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])",
                          text, flags=re.IGNORECASE)]
    if found:
        raise AssertionError(
            f"identidad de configuración en {os.path.basename(path)}: {found}")
    return True


def build_package(results_dir, out_dir, seed, model_filter=None):
    manifest = bench_v2p.read_manifest()
    cases_list = bench_v2p.load_corpus()["cases"]
    cases = {case["id"]: case for case in cases_list}
    rubric = read_json(bench_v2p.RUBRIC_PATH)
    digests = bench_v2p.corpus_digests()
    expected = {
        "references_sha256": digests["references_sha256"],
        "corpus_file_sha256": digests["corpus_file_sha256"],
        "rubric_sha256": bench_v2p.rubric_digest(),
        "prescoring_sha256": bench_v2p.prescoring_digest(),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise RuntimeError(f"el manifiesto congelado no coincide: {key}")

    variants, stats = collect_variants(results_dir, cases, model_filter)
    if not variants:
        raise RuntimeError("no se encontraron respuestas para armar el paquete")
    os.makedirs(out_dir, exist_ok=True)
    review_path, key_path, ids, entries, dimensions = write_review_and_key(
        variants, out_dir, seed, rubric)
    instructions_path = write_instructions(out_dir, rubric, review_path, dimensions)
    manifest_path, written = write_manifest(
        out_dir, (review_path, key_path, instructions_path), stats, seed, cases, rubric,
        {"generated_at": time.time(),
         "references_sha256": digests["references_sha256"],
         "corpus_file_sha256": digests["corpus_file_sha256"],
         "blind_rows": len(entries),
         "cases_mapped": len({entry["case_id"] for entry in entries}),
         "empty_response_variants": sum(1 for entry in entries
                                        if not entry["response"].strip()),
         "rows_with_lexical_reference": sum(1 for entry in entries
                                            if entry["lexical_reference"]),
         "mapped_entries": sum(len(entry["mapped"]) for entry in entries)})

    tokens = identity_tokens(stats, cases, model_filter)
    assert_no_identity(review_path, tokens, fields_only=True)
    assert_no_identity(instructions_path, tokens)
    assert_no_identity(manifest_path, tokens)
    return {"review": review_path, "key": key_path, "manifest": manifest_path,
            "instructions": instructions_path, "report": written, "stats": stats}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", default=aggregate_v2p.DEFAULT_RESULTS_DIR)
    parser.add_argument("--models", help="Comma-separated model directory names")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed", type=int, default=20261016)
    args = parser.parse_args(argv)
    model_filter = None
    if args.models:
        model_filter = {item.strip() for item in args.models.split(",") if item.strip()}
    try:
        package = build_package(args.results_dir, args.out_dir, args.seed, model_filter)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INTEGRITY
    report = package["report"]
    print(f"Paquete ciego en: {args.out_dir}")
    print(f"  filas ciegas únicas: {report['counts']['blind_rows']} "
          f"(casos {report['counts']['cases_mapped']}, "
          f"vacías {report['counts']['empty_response_variants']})")
    print(f"  fuentes leídas: {report['counts']['sources_read']} "
          f"({report['counts']['rows_read']} filas, "
          f"mapeos {report['counts']['mapped_entries']})")
    print(f"  sin modelos, estrategias ni condiciones en revisión, instrucciones y manifiesto")
    print(f"  revisión: {os.path.basename(package['review'])}  "
          f"clave: {os.path.basename(package['key'])}")
    print(f"  manifiesto: {os.path.basename(package['manifest'])}  "
          f"instrucciones: {os.path.basename(package['instructions'])}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
