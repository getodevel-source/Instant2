"""Voice-to-Prompt strategy benchmark (isolated; never changes app runtime).

Compares five strategies on the frozen synthetic corpus ``v2p_cases.json``:

1. ``baseline``   - the CURRENT product path replayed exactly: local correction
                    (``llm.correct_locally``) -> correction prompt
                    (``llm.DEFAULT_SYSTEM`` + production glossary) ->
                    ``llm.polish_candidate`` -> conservative meaning gate
                    (``llm.candidate_preserves_meaning``).
2. ``legacy_instruction`` - one call with the app's correction instruction:
                    a negative control for the prompt factor, NOT historical
                    Voice-to-Prompt (no such prompt exists).
3. ``direct``     - one call with a redesigned Voice-to-Prompt system prompt.
4. ``structured`` - one schema-constrained extraction call plus a DETERMINISTIC
                    renderer (no second model call) that turns fields into
                    the final prompt.
5. ``two_stage``  - one schema-validated extraction call, then a separate
                    generation call using that structure, the original
                    dictation and only the authorized context. If extraction
                    fails, generation is skipped and the response is empty.

``two_stage`` is a new isolated post-hoc arm. It is not part of the frozen C or
D result directories and does not change product code or defaults.

Factorial dimensions are independently selectable: ``--model``/``--models``,
``--strategies`` and ``--context``. Every combination runs the whole corpus (or
the ``--limit``/``--smoke`` subset) and writes one JSONL row per case.

Context modes: ``off``, ``glossary``, ``provided``, ``history``, ``selection``,
``all`` and ``oracle_relevant``. ``all`` is a stress condition: it injects every
turn and the glossary, including irrelevant history. ``oracle_relevant`` is an
UPPER BOUND, not a product behavior: it injects only the context the corpus marks
as relevant (provided_context; history only for ``history_relevant``; selection
only when authorized) and no glossary, so it measures what perfect context
selection could reach. A production retriever must be compared against it, never
confused with it.

Gold isolation: the corpus holds author-written references (``reference_prompt``,
``expected``, ``anchors``, ``forbidden``, ``length``, ``notes``). Those fields are
NEVER sent to the model. Only ``input`` and the user-provided context that the
selected ``--context`` mode enables are used to build requests, and
``assert_no_gold_leak`` audits the USER turn before every request (and in
``--dry-run``): a gold phrase present there but absent from the dictation plus
the authorized context block is a leak. The fixed system prompt is ignored by
that audit on purpose, since it is harness text and not a channel for gold.

Invariants are deliberately narrow and defensible:
``anchors_present``, ``forbidden_absent`` (no invented data / no leak of an
unauthorized selection or an irrelevant turn), ``restrictions_present``,
``unknown_safe``, ``length_ok`` and ``empty_output``. Anchors and forbidden
phrases are compared case- and accent-insensitively by word sequence; the strict
accent-sensitive anchor count is also reported. Nothing here judges quality:
blinded review rows are exported so a human scores the prompts
(``blinded_review.jsonl`` + ``blinded_key.json``). No model acts as judge.

Usage:
    python bench/bench_v2p.py --freeze
    python bench/bench_v2p.py --dry-run
    python bench/bench_v2p.py --fixture                      # plumbing smoke, no model
    python bench/bench_v2p.py --url http://127.0.0.1:8080/v1 \
        --models qwen3.5:4b-q4_K_M --strategies baseline,direct,structured,two_stage \
        --context off --smoke 6 --out-dir C:/Temp/instant2-v2p

Exit codes: 0 ok, 2 usage/validation error, 3 frozen references changed,
4 endpoint or request failure, 5 plumbing smoke failed.

While a scored run is in flight, ``--fixture`` never touches a live endpoint:
it serves OpenAI-compatible stubs (streaming SSE, JSON schema, HTTP 500 and
``finish_reason=length``) from a loopback ``http.server`` on an ephemeral port
and uses four built-in smoke cases that are disjoint from the frozen corpus.
Fixture rows are written with ``"scored": false`` in the metadata.
"""
import argparse
import hashlib
import http.server
import json
import math
import os
import random
import re
import statistics
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instant2_app import bias, context, llm  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS_PATH = os.path.join(HERE, "v2p_cases.json")
MANIFEST_PATH = os.path.join(HERE, "v2p_references.sha256")
RUBRIC_PATH = os.path.join(HERE, "v2p_rubric.json")
CORPUS_SCHEMA = "instant2-v2p-corpus/1"

STRATEGIES = ("baseline", "legacy_instruction", "direct", "structured", "two_stage")
CONTEXT_MODES = ("off", "glossary", "provided", "history", "selection", "all",
                 "oracle_relevant")
GOLD_FIELDS = ("reference_prompt", "expected", "anchors", "forbidden", "length",
               "notes", "missing_context_markers")

EXIT_USAGE = 2
EXIT_FROZEN = 3
EXIT_ENDPOINT = 4
EXIT_SMOKE = 5

# --------------------------------------------------------------------------
# Bench-only prompts (never imported by the app)
# --------------------------------------------------------------------------

DIRECT_SYSTEM = (
    "Convertís un pedido dictado en español en un prompt claro y autocontenido "
    "para un asistente de IA. El texto viene de reconocimiento de voz: no trae "
    "puntuación y puede tener muletillas, repeticiones y falsos arranques; "
    "quitalos y quedate con la versión final que eligió el hablante. "
    "Conservá la intención y TODOS los datos dichos: nombres propios, cifras, "
    "fechas, horas, unidades, rutas, comandos, condiciones y restricciones. "
    "No agregues datos que no estén en el dictado ni en el contexto aportado y "
    "no completes con valores plausibles lo que no se dijo: si un dato necesario "
    "falta, dejà el hueco explícito (por ejemplo «sin especificar») en vez de "
    "inventarlo. Conservá cada condición y cada restricción por separado, sin "
    "fusionarlas ni omitir ninguna. Conservá el tratamiento y la variedad "
    "regional del hablante (vos, tú, usted). Si el pedido trae una referencia "
    "ambigua («eso», «el archivo ese»), dejá esa referencia tal cual: no la "
    "reemplaces por algo concreto. No respondas el pedido, no agregues "
    "explicaciones ni títulos: escribí solo el prompt final, sin comillas."
)

STRUCTURED_SYSTEM = (
    "Extraés un pedido dictado en español al JSON del esquema. Reglas: "
    "«objetivo» es la acción pedida, en infinitivo, sin agregar datos. "
    "«destinatario» es la persona nombrada; «destino» es el canal o ámbito pedido "
    "para la entrega (por ejemplo «correo», «repo», «chat»). Son campos distintos y "
    "los dos son opcionales: si el hablante no nombró el canal, dejá «destino» en "
    "null y NO lo agregues a «datos_faltantes» salvo que el pedido no se pueda "
    "ejecutar sin elegir un canal (por ejemplo, «avisale al equipo» no obliga a "
    "elegir canal; «mandale el informe por mail» sí lo nombra). NUNCA asumas un "
    "canal porque el pedido mencione enviar algo a alguien, y no conviertas una "
    "ausencia normal en una advertencia. Lo mismo vale para «destinatario»: no lo "
    "deduzcas. "
    "«datos_dichos» son los datos textuales que el hablante dijo (nombres, "
    "cifras, fechas, horas, rutas, comandos, columnas): copialos literales, sin "
    "traducirlos ni reformatearlos. «datos_faltantes» es lo que el pedido NO PUEDE "
    "ejecutar sin definir: nunca lo rellenes con un valor plausible y no listes "
    "ausencias que no cambian la ejecucion. "
    "«restricciones» son las prohibiciones o límites explícitos; «condiciones» "
    "son los requisitos o formatos pedidos. «contexto_usado» son los datos que "
    "tomaste del contexto aportado, con el mismo texto. Si un dato viene de una "
    "referencia ambigua, no lo inventes: va en «datos_faltantes». "
    "Si el dictado tiene muletillas, repeticiones o correcciones, usá la versión "
    "final del hablante. Devolvé solo el JSON."
)

INSTRUCTION_SCHEMA = {
    "type": "object",
    "properties": {
        "objetivo": {"type": "string"},
        "destinatario": {"type": ["string", "null"]},
        "destino": {"type": ["string", "null"]},
        "formato": {"type": ["string", "null"]},
        "tono": {"type": ["string", "null"]},
        "plazo": {"type": ["string", "null"]},
        "condiciones": {"type": "array", "items": {"type": "string"}},
        "restricciones": {"type": "array", "items": {"type": "string"}},
        "datos_dichos": {"type": "array", "items": {"type": "string"}},
        "datos_faltantes": {"type": "array", "items": {"type": "string"}},
        "contexto_usado": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["objetivo", "destinatario", "destino", "formato", "tono", "plazo",
                 "condiciones", "restricciones", "datos_dichos",
                 "datos_faltantes", "contexto_usado"],
    "additionalProperties": False,
}

TWO_STAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "minLength": 3},
        "explicit_facts": {
            "type": "array", "items": {"type": "string", "minLength": 1}},
        "conditions": {
            "type": "array", "items": {"type": "string", "minLength": 1}},
        "restrictions": {
            "type": "array", "items": {"type": "string", "minLength": 1}},
        "authorized_context_facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "provenance": {"type": "string",
                                   "enum": ["provided_context", "history", "selection"]},
                    "fact": {"type": "string", "minLength": 1},
                },
                "required": ["provenance", "fact"],
                "additionalProperties": False,
            },
        },
        "unknowns": {
            "type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "required": ["intent", "explicit_facts", "conditions", "restrictions",
                 "authorized_context_facts", "unknowns"],
    "additionalProperties": False,
}

TWO_STAGE_KEYS = tuple(TWO_STAGE_SCHEMA["properties"])
TWO_STAGE_EXTRACT_SYSTEM = (
    "ETAPA 1: recibís un JSON de usuario con dictation y authorized_context. "
    "Extraé una estructura JSON compacta; no generes el prompt final ni expliques "
    "el razonamiento. Si una lista no tiene elementos, devolvé [] exactamente; "
    "nunca uses cadenas vacías, texto marcador ni prosa como sustituto. "
    "intent es una frase breve en español natural que expresa qué quiere hacer "
    "la persona; no es una etiqueta, categoría ni enum. "
    "explicit_facts solo incluye entidades nombradas, nombres, cifras, rutas o "
    "términos concretos dichos literalmente en dictation; excluye acciones, "
    "palabras genéricas y el pedido completo. conditions contiene únicamente "
    "requisitos afirmativos (por ejemplo, 'antes del viernes'); restrictions "
    "contiene únicamente negaciones, prohibiciones o límites negativos (por "
    "ejemplo, 'sin tecnicismos'). Copiá esos fragmentos verbatim del dictado; "
    "no conviertas una restricción en condición positiva. "
    "authorized_context_facts solo puede copiar hechos concretos de una entrada "
    "individual de authorized_context, conservando su provenance exacta entre "
    "provided_context, history o selection. No unas fuentes, no cambies la "
    "procedencia y no inventes hechos. Ejemplo: si dictation es "
    "'Pedí un informe para Ana antes del viernes, sin tablas' y no hay contexto, "
    "intent: 'Preparar un informe para Ana'; explicit_facts: ['Ana']; "
    "conditions: ['antes del viernes']; restrictions: ['sin tablas']; "
    "authorized_context_facts: []; unknowns: []. "
    "Si no hay datos desconocidos relevantes, unknowns es []; si falta un dato "
    "necesario, anotá solo su nombre breve (p. ej. 'fecha del informe'), no una "
    "frase vacía ni un valor supuesto. Devolvé solo JSON que cumpla el esquema."
)
TWO_STAGE_GENERATE_SYSTEM = (
    "ETAPA 2: redactá el prompt final, claro y conciso, en español para cumplir "
    "el pedido. Usá la estructura extraída como guía, pero dictation es la fuente "
    "principal. Conservá la intención, hechos concretos, condiciones afirmativas "
    "y restricciones/negaciones por separado, sin invertirlas ni omitirlas. "
    "Solo incorporá hechos de authorized_context_facts cuando su provenance "
    "coincida con una entrada de contexto autorizada; no uses otros recuerdos ni "
    "completes valores plausibles. Si unknowns no está vacío, no inventes esos "
    "datos. No respondas el pedido ni expliques el proceso. Devolvé solo el "
    "prompt final."
)

STRUCTURED_KEYS = tuple(INSTRUCTION_SCHEMA["properties"])
SCALAR_KEYS = ("objetivo", "destinatario", "destino", "formato", "tono", "plazo")


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

def _fold(text):
    """Casefold and strip diacritics: data presence must not depend on tildes."""
    decomposed = unicodedata.normalize("NFD", (text or "").casefold())
    return "".join(char for char in decomposed
                   if unicodedata.category(char) != "Mn")


def _fold_words(text):
    return re.findall(r"\w+", _fold(text))


_PROVENANCE_TOKEN_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[\\/][\w.-]+(?:[\\/][\w.-]+)*|"
    r"[\w.-]+[\\/][\w./-]+)|"
    r"(?P<file>[\w-]+(?:\.[\w-]+)+)|"
    r"(?P<number>\d+(?:[-_/.,]\d+)*(?:%|°)?)|"
    r"(?P<word>\w+)",
    re.UNICODE,
)


def _provenance_tokens(text):
    """Fold ordinary words, but keep paths/files/numeric tokens indivisible."""
    tokens = []
    for match in _PROVENANCE_TOKEN_RE.finditer(text or ""):
        kind = match.lastgroup
        value = match.group(0)
        if kind in ("path", "file", "number"):
            # Paths are case-sensitive and numeric punctuation can change a
            # value, so these tokens intentionally receive no normalization.
            tokens.append("\0" + value)
        else:
            tokens.append(_fold(value))
    return tokens


def _provenance_subsequence(phrase, source):
    needle = _provenance_tokens(phrase)
    haystack = _provenance_tokens(source)
    if not needle:
        return True
    limit = len(haystack) - len(needle)
    return any(haystack[start:start + len(needle)] == needle
               for start in range(limit + 1))


def _strict_words(text):
    return re.findall(r"\w+", unicodedata.normalize("NFC", (text or "")).casefold())


def _phrase_in(words, phrase):
    needle = _fold_words(phrase)
    if not needle:
        return True
    limit = len(words) - len(needle)
    for start in range(limit + 1):
        if words[start:start + len(needle)] == needle:
            return True
    return False


def _surface(text):
    return " ".join(unicodedata.normalize("NFC", text or "").split())


def _percentiles(values):
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return {"count": 0, "p50": None, "p95": None, "min": None, "max": None}
    def pick(quantile):
        index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
        return ordered[index]
    return {"count": len(ordered), "p50": statistics.median(ordered),
            "p95": pick(0.95), "min": ordered[0], "max": ordered[-1]}


# --------------------------------------------------------------------------
# Corpus and frozen references
# --------------------------------------------------------------------------

def load_corpus(path=CORPUS_PATH):
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    if document.get("schema") != CORPUS_SCHEMA:
        raise ValueError(f"esquema de corpus inesperado: {document.get('schema')!r}")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("el corpus no tiene casos")
    ids = set()
    for case in cases:
        for field in ("id", "category", "input", "reference_prompt", "expected",
                      "anchors", "forbidden", "length"):
            if field not in case:
                raise ValueError(f"caso {case.get('id')!r} sin campo {field!r}")
        if case["id"] in ids:
            raise ValueError(f"id duplicado: {case['id']!r}")
        ids.add(case["id"])
    return document


def _references_projection(cases):
    """Canonical projection of the author-written gold, for the freeze digest."""
    projection = []
    for case in cases:
        projection.append({
            "id": case["id"],
            "category": case["category"],
            "input": case["input"],
            "reference_prompt": case["reference_prompt"],
            "expected": case["expected"],
            "anchors": case.get("anchors", []),
            "forbidden": case.get("forbidden", []),
            "missing_context_markers": case.get("missing_context_markers", []),
            "evaluator": case.get("evaluator", {}),
            "length": case["length"],
            "notes": case.get("notes", ""),
        })
    return projection


def runner_digest():
    """SHA-256 of this harness, so a scored run can be tied to its revision."""
    try:
        with open(os.path.abspath(__file__), "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def load_rubric(path=RUBRIC_PATH):
    """Pre-registered scoring rubric. Never drives an automatic check."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def rubric_digest(path=RUBRIC_PATH):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def prescoring_digest(path=RUBRIC_PATH):
    """Digest of the scoring contract only (classes, dimensions, gates)."""
    rubric = load_rubric(path)
    contract = {
        "schema": rubric.get("schema"),
        "version": rubric.get("version"),
        "critical_errors": [item.get("id") for item in rubric.get("critical_errors") or []],
        "dimensions": [item.get("id") for item in
                       (rubric.get("ratings") or {}).get("dimensions") or []],
        "acceptance_gates": rubric.get("acceptance_gates"),
    }
    return hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def corpus_digests(path=CORPUS_PATH, cases=None):
    if cases is None:
        cases = load_corpus(path)["cases"]
    with open(path, "rb") as handle:
        file_bytes = handle.read()
    projection = json.dumps(_references_projection(cases), ensure_ascii=False,
                            sort_keys=True, separators=(",", ":"))
    return {
        "corpus_file_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "references_sha256": hashlib.sha256(projection.encode("utf-8")).hexdigest(),
        "case_count": len(cases),
    }


def write_manifest(path=MANIFEST_PATH, corpus_path=CORPUS_PATH):
    digests = corpus_digests(corpus_path)
    lines = [
        "# Referencias congeladas de Voice-to-Prompt: NO editar despues de correr",
        "# el modelo. Regenerar solo si el corpus cambia a proposito (--freeze).",
        f"references_sha256 {digests['references_sha256']}",
        f"corpus_file_sha256 {digests['corpus_file_sha256']}",
        f"rubric_sha256 {rubric_digest()}",
        f"prescoring_sha256 {prescoring_digest()}",
        f"case_count {digests['case_count']}",
    ]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return digests


def read_manifest(path=MANIFEST_PATH):
    if not os.path.isfile(path):
        return None
    found = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition(" ")
            found[key] = value.strip()
    return found


def verify_manifest(path=MANIFEST_PATH, corpus_path=CORPUS_PATH):
    """Return the list of frozen-reference problems (empty means untouched)."""
    frozen = read_manifest(path)
    if frozen is None:
        return ["no existe el manifiesto de referencias; correr --freeze antes"]
    digests = corpus_digests(corpus_path)
    problems = []
    for key in ("references_sha256", "corpus_file_sha256"):
        if frozen.get(key) != digests[key]:
            problems.append(f"{key} cambio ({frozen.get(key)} -> {digests[key]})")
    if frozen.get("rubric_sha256") != rubric_digest():
        problems.append("rubric_sha256 cambio: el rubric se edito despues del freeze")
    if frozen.get("prescoring_sha256") != prescoring_digest():
        problems.append("prescoring_sha256 cambio: el rubric o sus escalas se editaron "
                        "despues del freeze")
    if frozen.get("case_count") != str(digests["case_count"]):
        problems.append("case_count cambio "
                        f"({frozen.get('case_count')} -> {digests['case_count']})")
    return problems


# --------------------------------------------------------------------------
# Prompt assembly (never reads gold fields)
# --------------------------------------------------------------------------

def _glossary(cfg):
    """Same glossary the production path injects (llm.py:346-352)."""
    terms = context.correction_context(cfg or {})
    general = bias.terms_for_correction()
    return (terms + "\n" + general) if terms else general


def _context_block(case, context_mode):
    """User-provided material only: provided_context, history, selection.

    ``oracle_relevant`` is an UPPER BOUND, not a product behavior: it injects only
    the context the corpus marks as relevant (provided_context; history only for
    the ``history_relevant`` category; selection only when authorized) and no
    glossary. It measures what the task could reach with perfect selection, so a
    production retriever must be compared against it, never confused with it.
    """
    oracle = context_mode == "oracle_relevant"
    lines = []
    if context_mode in ("provided", "all", "oracle_relevant"):
        for item in case.get("provided_context") or []:
            lines.append(f"- {item.get('label') or item.get('kind')}: {item['value']}")
    if context_mode in ("history", "all"):
        for turn in case.get("history") or []:
            role = "usuario" if turn.get("role") == "user" else "asistente"
            lines.append(f"- turno anterior ({role}): {turn.get('content', '')}")
    if oracle and case.get("category") == "history_relevant":
        for turn in case.get("history") or []:
            role = "usuario" if turn.get("role") == "user" else "asistente"
            lines.append(f"- turno anterior ({role}): {turn.get('content', '')}")
    if context_mode in ("selection", "all", "oracle_relevant"):
        selection = case.get("selection") or {}
        if selection.get("authorized") and selection.get("text"):
            lines.append("- texto seleccionado por el usuario: " + selection["text"])
    return "\n".join(lines)


def build_messages(strategy, case, context_mode, cfg):
    """Assemble the request messages from non-gold fields only."""
    user = case["input"]
    block = _context_block(case, context_mode)
    if strategy == "baseline":
        # Production replays the LOCAL correction of the transcript and ALWAYS
        # sends its glossary (llm.py:348-352). context_mode is intentionally
        # unused here: the baseline arms are diagnostic duplicates.
        return [{"role": "system",
                 "content": llm.DEFAULT_SYSTEM
                 + "\nGlosario de grafias preferidas (referencia, no contenido):\n"
                 + _glossary(cfg)},
                {"role": "user", "content": llm.correct_locally(user, cfg)}]
    if strategy == "direct":
        system = DIRECT_SYSTEM
        if context_mode in ("glossary", "all"):
            system += ("\nTerminos preferidos de escritura (referencia, no contenido):\n"
                       + _glossary(cfg))
        if block:
            user = ("CONTEXTO APORTADO POR EL USUARIO (usalo solo si el pedido lo "
                    "necesita):\n" + block + "\n\nDICTADO:\n" + user)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if strategy == "legacy_instruction":
        # Negative control for the prompt factor: the app's own correction
        # instruction, verbatim, with the SAME context wrapper as ``direct``.
        # This is NOT a historical Voice-to-Prompt prompt: none ever existed.
        system = llm.DEFAULT_SYSTEM
        if context_mode in ("glossary", "all"):
            system += ("\nGlosario de grafias preferidas (referencia, no contenido):\n"
                       + _glossary(cfg))
        if block:
            user = ("CONTEXTO APORTADO POR EL USUARIO (usalo solo si el pedido lo "
                    "necesita):\n" + block + "\n\nDICTADO:\n" + user)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if strategy == "two_stage":
        return build_two_stage_extraction_messages(case, context_mode)
    system = STRUCTURED_SYSTEM
    if block:
        user = ("CONTEXTO APORTADO POR EL USUARIO:\n" + block + "\n\nDICTADO:\n" + user)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_two_stage_extraction_messages(case, context_mode):
    """First-stage input: dictation and structured, authorized context only."""
    user = json.dumps({
        "authorized_context": _two_stage_context_payload(case, context_mode),
        "dictation": case["input"],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return [{"role": "system", "content": TWO_STAGE_EXTRACT_SYSTEM},
            {"role": "user", "content": user}]


def build_two_stage_generation_messages(case, context_mode, extraction):
    """Second-stage input carries only original sources and validated JSON."""
    user = json.dumps({
        "authorized_context": _two_stage_context_payload(case, context_mode),
        "dictation": case["input"],
        "extraction": extraction,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return [{"role": "system", "content": TWO_STAGE_GENERATE_SYSTEM},
            {"role": "user", "content": user}]


def _two_stage_context_sources(case, context_mode):
    """Return exact strings authorized from each source in this context mode."""
    sources = {}
    if context_mode in ("provided", "all", "oracle_relevant"):
        values = [item.get("value", "") for item in case.get("provided_context") or []
                  if item.get("value")]
        if values:
            sources["provided_context"] = values
    if context_mode in ("history", "all") or (
            context_mode == "oracle_relevant" and case.get("category") == "history_relevant"):
        values = [turn.get("content", "") for turn in case.get("history") or []
                  if turn.get("content")]
        if values:
            sources["history"] = values
    if context_mode in ("selection", "all", "oracle_relevant"):
        selection = case.get("selection") or {}
        if selection.get("authorized") and selection.get("text"):
            sources["selection"] = [selection["text"]]
    return sources


def _two_stage_context_payload(case, context_mode):
    return [
        {"provenance": provenance, "facts": facts}
        for provenance, facts in _two_stage_context_sources(case, context_mode).items()
    ]


def gold_strings(case):
    expected = case.get("expected") or {}
    values = [case.get("reference_prompt", ""), case.get("notes", ""),
              expected.get("intent", ""), expected.get("mode", ""),
              expected.get("destination", "")]
    for key in ("constraints", "unknowns"):
        values.extend(expected.get(key) or [])
    for restriction in expected.get("restrictions") or []:
        values.extend(_restriction_variants(restriction))
    for item in expected.get("context") or []:
        values.append(item.get("value", ""))
    values.extend(case.get("anchors") or [])
    values.extend(case.get("forbidden") or [])
    values.extend(case.get("missing_context_markers") or [])
    return [_surface(value) for value in values if _surface(value)]


def _user_text(messages):
    return " ".join(message.get("content", "") for message in messages
                    if message.get("role") == "user")


def _allowed_source(case, context_mode, strategy="direct", cfg=None):
    """Everything the model may legitimately see for this case, and nothing else.

    The dictation itself, plus the authorized context block for the selected
    mode. For the production baseline the locally corrected transcript is also
    user-derived (a pure function of the input and the app's own local rules),
    so it counts as source material.
    """
    parts = [case["input"], _context_block(case, context_mode)]
    if strategy == "baseline":
        parts.append(llm.correct_locally(case["input"], cfg or {}))
    return " ".join(part for part in parts if part)


def assert_no_gold_leak(case, messages, context_mode, strategy="direct", cfg=None):
    """Gold reaching the USER turn must come from the user's own material.

    Only the user message is audited: the system prompt is fixed harness text and
    is not a channel for gold. A gold phrase that appears in the user message but
    is absent from the allowed source (dictation + authorized context) is a leak.
    """
    allowed = _fold(_allowed_source(case, context_mode, strategy, cfg))
    user = _fold(_user_text(messages))
    leaks = []
    for phrase in gold_strings(case):
        if len(_fold_words(phrase)) < 2 and len(phrase) < 5:
            continue
        folded = _fold(phrase)
        if folded in allowed:
            continue
        if folded in user:
            leaks.append(phrase)
    return leaks


# --------------------------------------------------------------------------
# Deterministic renderer (structured strategy)
# --------------------------------------------------------------------------

def _clean(value):
    text = " ".join(str(value or "").split())
    return text.rstrip(". ")


def render_prompt(fields):
    """Deterministic Spanish prompt from the extracted fields. No model call."""
    lines = []
    objetivo = _clean(fields.get("objetivo"))
    lines.append(f"Objetivo: {objetivo}." if objetivo else
                 "Objetivo: (no se pudo extraer del dictado).")
    for label, key in (("Destinatario", "destinatario"), ("Destino", "destino"),
                       ("Formato", "formato"), ("Tono", "tono"), ("Plazo", "plazo")):
        value = _clean(fields.get(key))
        if value:
            lines.append(f"{label}: {value}.")
    condiciones = [_clean(item) for item in fields.get("condiciones") or [] if _clean(item)]
    if condiciones:
        lines.append("Condiciones:")
        lines.extend(f"- {item}." for item in condiciones)
    restricciones = [_clean(item) for item in fields.get("restricciones") or [] if _clean(item)]
    if restricciones:
        lines.append("Restricciones:")
        lines.extend(f"- {item}." for item in restricciones)
    datos = [_clean(item) for item in fields.get("datos_dichos") or [] if _clean(item)]
    if datos:
        lines.append("Datos dichos: " + "; ".join(datos) + ".")
    usado = [_clean(item) for item in fields.get("contexto_usado") or [] if _clean(item)]
    if usado:
        lines.append("Datos tomados del contexto aportado: " + "; ".join(usado) + ".")
    faltantes = [_clean(item) for item in fields.get("datos_faltantes") or [] if _clean(item)]
    if faltantes:
        lines.append("Aclaracion: no hay dato sobre " + ", ".join(faltantes)
                     + "; no lo inventes.")
    return "\n".join(lines)


def parse_structured(content):
    """Lenient JSON object extraction for the prompt-json fallback mode."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return json.loads(text[start:end + 1])


def normalise_fields(payload):
    """Keep only the schema keys with the right shapes (defensive)."""
    fields = {}
    for key in STRUCTURED_KEYS:
        value = payload.get(key) if isinstance(payload, dict) else None
        if key in SCALAR_KEYS:
            fields[key] = value.strip() if isinstance(value, str) and value.strip() else None
        else:
            fields[key] = [str(item).strip() for item in value if str(item).strip()] \
                if isinstance(value, list) else []
    return fields


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"clave JSON duplicada: {key}")
        result[key] = value
    return result


def parse_two_stage_extraction(content, authorized_sources, original_dictation):
    """Validate JSON and conservatively match each fact to its source."""
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_json_keys)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON de extracción inválido: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != set(TWO_STAGE_KEYS):
        raise ValueError("la extracción no coincide con el esquema")
    if not isinstance(payload.get("intent"), str) or not payload["intent"].strip():
        raise ValueError("intent debe ser texto no vacío")

    result = {"intent": payload["intent"].strip()}
    for key in ("explicit_facts", "conditions", "restrictions", "unknowns"):
        values = payload.get(key)
        if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"{key} debe ser una lista de textos no vacíos")
        values = [value.strip() for value in values]
        if key in ("explicit_facts", "conditions", "restrictions") and any(
                not _provenance_subsequence(value, original_dictation)
                for value in values):
            raise ValueError(f"{key} no aparece como secuencia en el dictado")
        result[key] = values

    context_facts = payload.get("authorized_context_facts")
    if not isinstance(context_facts, list):
        raise ValueError("authorized_context_facts debe ser una lista")
    result["authorized_context_facts"] = []
    for item in context_facts:
        if (not isinstance(item, dict)
                or set(item) != {"provenance", "fact"}
                or not isinstance(item.get("provenance"), str)
                or not isinstance(item.get("fact"), str)
                or not item["fact"].strip()):
            raise ValueError("hecho de contexto inválido")
        provenance = item["provenance"]
        fact = item["fact"].strip()
        authorized_values = authorized_sources.get(provenance) or []
        if not any(_provenance_subsequence(fact, source)
                   for source in authorized_values):
            raise ValueError("hecho de contexto sin procedencia autorizada literal")
        result["authorized_context_facts"].append(
            {"provenance": provenance, "fact": fact})
    return result


# --------------------------------------------------------------------------
# HTTP client (OpenAI-compatible, streaming and not)
# --------------------------------------------------------------------------

def _endpoint(base):
    return llm._chat_completion_endpoint(llm.check_url(base))


def _request(url, body, timeout, token=None, stream=False):
    payload = dict(body)
    payload["stream"] = bool(stream)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers=headers, method="POST")


def chat(base_url, body, timeout, token=None, stream=False):
    """One chat completion. Returns timing, tokens, finish reason and text."""
    url = _endpoint(base_url)
    if not stream:
        started = time.perf_counter()
        with urllib.request.urlopen(_request(url, body, timeout, token, False),
                                    timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        elapsed = time.perf_counter() - started
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        return {
            "text": message.get("content") or "",
            "reasoning": message.get("reasoning_content") or "",
            "finish_reason": choice.get("finish_reason"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "ttft_seconds": None,
            "total_seconds": elapsed,
            "streamed": False,
            "timings": data.get("timings"),
        }

    started = time.perf_counter()
    first_event = None
    ttft = None
    chunks = []
    reasoning = []
    finish_reason = None
    usage = {}
    timings = None
    with urllib.request.urlopen(_request(url, body, timeout, token, True),
                                timeout=timeout) as response:
        while True:
            raw = response.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if first_event is None:
                first_event = time.perf_counter() - started
            choice = (event.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                chunks.append(piece)
                if ttft is None:
                    ttft = time.perf_counter() - started
            reasoning_piece = delta.get("reasoning_content")
            if reasoning_piece:
                reasoning.append(reasoning_piece)
                if ttft is None:
                    ttft = time.perf_counter() - started
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            if event.get("usage"):
                usage = event["usage"]
            if event.get("timings"):
                timings = event["timings"]
    elapsed = time.perf_counter() - started
    text = "".join(chunks)
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if completion_tokens is None and isinstance(timings, dict):
        prompt_tokens = prompt_tokens or timings.get("prompt_n")
        completion_tokens = timings.get("predicted_n")
    return {
        "text": text,
        "reasoning": "".join(reasoning),
        "finish_reason": finish_reason,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "ttft_seconds": ttft,
        "total_seconds": elapsed,
        "streamed": True,
        "timings": timings,
        "first_event_seconds": first_event,
        "output_tokens_estimated": len(chunks) or None,
    }


def throughput(row):
    """Aggregate completion tokens over all measured generation phases."""
    completion = row.get("completion_tokens")
    if not isinstance(completion, (int, float)) or completion <= 0:
        return None
    generation = row.get("generation_seconds")
    if isinstance(generation, (int, float)) and generation > 0:
        return completion / generation
    total = row.get("model_seconds")
    if not isinstance(total, (int, float)) or total <= 0:
        total = row.get("wall_seconds")
    if not isinstance(total, (int, float)) or total <= 0:
        return None
    ttft = row.get("ttft_seconds")
    generation = total - ttft if isinstance(ttft, (int, float)) and 0 < ttft < total else total
    return completion / generation if generation > 0 else None


# --------------------------------------------------------------------------
# Fixture transport (plumbing smoke with no model and no live endpoint)
# --------------------------------------------------------------------------

SMOKE_CASES = [
    {
        "id": "smoke_simple",
        "category": "smoke_fixture",
        "input": "che haceme un prompt para pedir la cotizacion de diez monitores",
        "reference_prompt": "Hace un prompt para pedir la cotizacion de diez monitores.",
        "expected": {"intent": "pedir la cotizacion de diez monitores",
                     "constraints": ["diez monitores"], "context": [],
                     "unknowns": [], "mode": "libre", "destination": "ninguno",
                     "restrictions": []},
        "anchors": ["monitores"],
        "forbidden": ["veinte monitores"],
        "length": {"min_words": 3, "max_words": 40},
    },
    {
        "id": "smoke_long_stream",
        "category": "smoke_fixture",
        "input": "mira necesito un prompt bastante largo para pedir que revisen el documento "
                 "de arquitectura del servicio de reportes y que marquen los riesgos de "
                 "escalabilidad y las dependencias externas antes de la reunion del miercoles",
        "reference_prompt": "Hace un prompt para que revisen el documento de arquitectura del "
                            "servicio de reportes y marquen los riesgos de escalabilidad y las "
                            "dependencias externas antes de la reunion del miercoles.",
        "expected": {"intent": "pedir la revision del documento de arquitectura",
                     "constraints": ["riesgos de escalabilidad", "dependencias externas",
                                     "reunion del miercoles"],
                     "context": [], "unknowns": [], "mode": "libre",
                     "destination": "ninguno", "restrictions": []},
        "anchors": ["arquitectura", "escalabilidad", "dependencias externas"],
        "forbidden": ["martes"],
        "length": {"min_words": 8, "max_words": 60},
    },
    {
        "id": "smoke_http_error",
        "category": "smoke_fixture",
        "input": "FIXTURE_HTTP_ERROR pedido de prueba para verificar el error del transporte",
        "reference_prompt": "Pedido de prueba del transporte.",
        "expected": {"intent": "verificar el manejo de error HTTP", "constraints": [],
                     "context": [], "unknowns": [], "mode": "libre",
                     "destination": "ninguno", "restrictions": []},
        "anchors": [],
        "forbidden": [],
        "length": {"min_words": 0, "max_words": 200},
    },
    {
        "id": "smoke_truncated",
        "category": "smoke_fixture",
        "input": "FIXTURE_TRUNCATE pedido de prueba para verificar el finish_reason length",
        "reference_prompt": "Pedido de prueba del finish_reason length.",
        "expected": {"intent": "verificar finish_reason length", "constraints": [],
                     "context": [], "unknowns": [], "mode": "libre",
                     "destination": "ninguno", "restrictions": []},
        "anchors": [],
        "forbidden": [],
        "length": {"min_words": 0, "max_words": 200},
    },
]


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible stub: streaming, JSON schema and error paths."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the smoke output readable
        return

    def _head(self, status, content_type, length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/props"):
            payload = {"build_info": "fixture", "model_alias": "fixture-model",
                       "model_path": "fixture-gguf", "model_ftype": "fixture",
                       "total_slots": 1, "is_sleeping": False,
                       "chat_template_caps": {"supports_system_role": True},
                       "default_generation_settings": {"n_ctx": 4096, "params": {}}}
        elif self.path.startswith("/v1/models"):
            payload = {"data": [{"id": "fixture-model"}]}
        else:
            payload = {"status": "ok"}
        body = json.dumps(payload).encode("utf-8")
        self._head(200, "application/json", len(body))
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        schema_spec = (body.get("response_format") or {}).get("json_schema") or {}
        schema = schema_spec.get("schema")
        system = next((message.get("content", "") for message in body.get("messages", [])
                       if message.get("role") == "system"), "")
        user = next((message.get("content", "") for message in body.get("messages", [])
                     if message.get("role") == "user"), "")
        if "FIXTURE_HTTP_ERROR" in user:
            payload = json.dumps({"error": {"message": "fixture HTTP 500"}}).encode("utf-8")
            self._head(500, "application/json", len(payload))
            self.wfile.write(payload)
            return
        if schema_spec.get("name") == "two_stage_extraction" or system.startswith("ETAPA 1:"):
            content = json.dumps(_fixture_two_stage(user), ensure_ascii=False)
        elif schema:
            content = json.dumps(_fixture_fields(user), ensure_ascii=False)
        else:
            content = "Prompt de prueba: " + " ".join(_surface(user).split()[:40]) + "."
        prompt_tokens = max(1, len(user.split()) + 20)
        if body.get("stream"):
            self._stream(content, prompt_tokens, "FIXTURE_TRUNCATE" in user)
        else:
            completion = len(content.split())
            payload = json.dumps({
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": prompt_tokens,
                          "completion_tokens": completion,
                          "total_tokens": prompt_tokens + completion},
            }).encode("utf-8")
            self._head(200, "application/json", len(payload))
            self.wfile.write(payload)

    def _stream(self, content, prompt_tokens, force_length=False):
        finish = "length" if force_length or "FIXTURE_TRUNCATE" in content else "stop"
        self._head(200, "text/event-stream")
        pieces = [content[index:index + 12] for index in range(0, len(content), 12)] or [""]
        for piece in pieces:
            chunk = {"choices": [{"index": 0, "finish_reason": None,
                                  "delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.01)
        last = {"choices": [{"index": 0, "finish_reason": finish, "delta": {}}],
                "usage": {"prompt_tokens": prompt_tokens,
                          "completion_tokens": len(content.split()),
                          "total_tokens": prompt_tokens + len(content.split())},
                "timings": {"prompt_n": prompt_tokens,
                            "predicted_n": len(content.split()),
                            "predicted_ms": 10.0 * len(pieces)}}
        self.wfile.write(f"data: {json.dumps(last)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def _fixture_fields(user):
    """Schema-conformant extraction stub derived deterministically from input."""
    words = _surface(user).split()
    return {
        "objetivo": "fixture " + " ".join(words[:6]),
        "destinatario": None, "formato": None, "tono": None, "plazo": None,
        "condiciones": [], "restricciones": [], "datos_dichos": [],
        "datos_faltantes": [], "contexto_usado": [],
    }


def _fixture_two_stage(user):
    """Return a valid compact extraction for the local plumbing fixture."""
    return {
        "intent": "verificar el flujo de dos etapas",
        "explicit_facts": [],
        "conditions": [],
        "restrictions": [],
        "authorized_context_facts": [],
        "unknowns": [],
    }


class FixtureServer:
    """Loopback stub server for the plumbing smoke (no model, no live endpoint)."""

    def __init__(self):
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                       _FixtureHandler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="v2p-fixture", daemon=True)

    @property
    def url(self):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False


# --------------------------------------------------------------------------
# Strategies
# --------------------------------------------------------------------------

# The production correction path returns text only (llm.polish_candidate and
# llm.maybe_polish build no response object), so tokens, TTFT and finish_reason
# cannot be observed for the baseline strategy. They are reported as unavailable
# instead of being estimated.
BASELINE_METRICS_UNAVAILABLE = (
    "la ruta de produccion (llm.polish_candidate / llm.maybe_polish) devuelve solo "
    "texto: no hay tokens, ni TTFT, ni finish_reason observables")


def run_baseline(case, context_mode, cfg, args, client):
    """Replay the current production correction path (llm.py:332-372).

    The production path ALWAYS sends its glossary (``llm.py:348-352`` builds
    ``context.correction_context() + bias.terms_for_correction()`` unconditionally)
    and never consumes project, history or selection context. So the replay uses
    the same glossary in every context mode and ignores ``context_mode`` entirely:
    baseline output must be identical for off/all/oracle_relevant, and those arms
    are kept on purpose as diagnostic duplicates rather than as distinct
    conditions. Gating the glossary on the context mode would have been a
    harness bug, not a product behavior.
    """
    text = case["input"]
    local = llm.correct_locally(text, cfg)
    terms = _glossary(cfg)
    deadline = time.monotonic() + args.timeout
    candidate = client(lambda: llm.polish_candidate(
        local, cfg["llm_url"], timeout=args.timeout, context_terms=terms, cfg=cfg))
    remaining = max(0.0, deadline - time.monotonic())
    candidate_text = _value_text(candidate)
    accepted = local
    gate = None
    if candidate_text and remaining > 0:
        gate = client(lambda: llm.candidate_preserves_meaning(
            local, candidate_text, cfg["llm_url"],
            timeout=min(2.0, remaining), cfg=cfg))
        if _value(gate) is True:
            accepted = candidate_text
    checks = _check_case(case, "baseline", accepted)
    production_match = None
    if args.verify_baseline:
        try:
            production_match = (llm.maybe_polish(text, cfg, timeout=args.timeout)
                                == accepted)
        except Exception as exc:  # noqa: BLE001 - recorded, never raised
            production_match = f"error:{type(exc).__name__}"
    return {
        "response": accepted,
        "candidate": candidate_text or None,
        "meaning_check_passed": _value(gate) if gate else None,
        "production_replay_matches_maybe_polish": production_match,
        "checks": checks,
        "calls": [call for call in (candidate, gate) if call],
        "timing": {
            "total_seconds": sum(call["seconds"] for call in (candidate, gate) if call),
            "ttft_seconds": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "finish_reasons": [],
            "metrics_unavailable": BASELINE_METRICS_UNAVAILABLE,
        },
    }


def run_direct(case, context_mode, cfg, args, client):
    messages = build_messages("direct", case, context_mode, cfg)
    body = _body(cfg, messages, args)
    call = client(lambda: chat(cfg["llm_url"], body, args.timeout,
                              token=_token(cfg), stream=args.stream))
    text = _value_text(call).strip()
    return {
        "response": text,
        "reasoning": _reasoning(call),
        "checks": _check_case(case, "direct", text),
        "calls": [call],
        "timing": _timing(call),
    }


def run_structured(case, context_mode, cfg, args, client):
    messages = build_messages("structured", case, context_mode, cfg)
    body = _body(cfg, messages, args)
    if args.structured_mode == "jsonschema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "instruccion", "strict": True,
                            "schema": INSTRUCTION_SCHEMA},
        }
    call = client(lambda: chat(cfg["llm_url"], body, args.timeout,
                              token=_token(cfg), stream=args.stream))
    raw = _value_text(call)
    fields = None
    rendered = ""
    render_error = None
    if raw:
        try:
            fields = normalise_fields(parse_structured(raw))
            rendered = render_prompt(fields)
        except Exception as exc:  # noqa: BLE001 - reported per case
            render_error = f"{type(exc).__name__}: {exc}"
    return {
        "response": rendered,
        "reasoning": _reasoning(call),
        "extraction_raw": raw or None,
        "fields": fields,
        "render_error": render_error,
        "checks": _check_case(case, "structured", rendered, fields),
        "calls": [call],
        "timing": _timing(call),
    }




def run_two_stage(case, context_mode, cfg, args, client):
    """Extract strict JSON, then generate only when each stage is complete."""
    calls = []
    fields = None
    extraction_error = None
    generation_error = None

    extraction_body = _body(
        cfg, build_two_stage_extraction_messages(case, context_mode), args)
    if args.structured_mode == "jsonschema":
        extraction_body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "two_stage_extraction", "strict": True,
                            "schema": TWO_STAGE_SCHEMA},
        }
    extraction_call = client(lambda: chat(
        cfg["llm_url"], extraction_body, args.timeout,
        token=_token(cfg), stream=args.stream))
    extraction_call["stage"] = "extract"
    calls.append(extraction_call)

    extraction_value = _value(extraction_call)
    raw = _value_text(extraction_call)
    if not isinstance(extraction_value, dict):
        extraction_error = extraction_call.get("error") or "falló la extracción"
    elif extraction_value.get("finish_reason") not in (None, "stop"):
        extraction_error = ("extracción incompleta: finish_reason="
                            + str(extraction_value.get("finish_reason")))
    else:
        try:
            fields = parse_two_stage_extraction(
                raw, _two_stage_context_sources(case, context_mode), case["input"])
        except (TypeError, ValueError) as exc:
            extraction_error = f"{type(exc).__name__}: {exc}"

    response = ""
    if fields is not None:
        generation_body = _body(
            cfg, build_two_stage_generation_messages(case, context_mode, fields), args)
        generation_call = client(lambda: chat(
            cfg["llm_url"], generation_body, args.timeout,
            token=_token(cfg), stream=args.stream))
        generation_call["stage"] = "generate"
        calls.append(generation_call)
        generation_value = _value(generation_call)
        if not isinstance(generation_value, dict):
            generation_error = generation_call.get("error") or "falló la generación"
        elif generation_value.get("finish_reason") not in (None, "stop"):
            generation_error = ("generación incompleta: finish_reason="
                                + str(generation_value.get("finish_reason")))
        else:
            response = _value_text(generation_call).strip()

    reasoning_parts = [
        value.get("reasoning") or "" for call in calls
        if isinstance((value := _value(call)), dict)
    ]
    reasoning = "\n".join(part for part in reasoning_parts if part)
    outcome_error = extraction_error or generation_error
    return {
        "response": response,
        "reasoning": reasoning or None,
        "extraction_raw": raw,
        "fields": fields,
        "extraction_ok": fields is not None,
        "extraction_error": extraction_error,
        "generation_error": generation_error,
        "error": outcome_error,
        "checks": _check_case(case, "two_stage", response, fields),
        "calls": calls,
        "timing": _aggregate_call_timing(calls),
    }
def run_legacy_instruction(case, context_mode, cfg, args, client):
    """Prompt-factor negative control: the app's correction instruction verbatim.

    Same transport, limits and context wrapper as ``direct``; only the system
    prompt differs. If a redesigned prompt beats this, the gain comes from the
    instruction, not from the runtime. This is not a historical V2P prompt.
    """
    messages = build_messages("legacy_instruction", case, context_mode, cfg)
    body = _body(cfg, messages, args)
    call = client(lambda: chat(cfg["llm_url"], body, args.timeout,
                              token=_token(cfg), stream=args.stream))
    text = _value_text(call).strip()
    return {
        "response": text,
        "reasoning": _reasoning(call),
        "checks": _check_case(case, "legacy_instruction", text),
        "calls": [call],
        "timing": _timing(call),
    }


STRATEGY_RUNNERS = {"baseline": run_baseline, "direct": run_direct,
                    "structured": run_structured, "two_stage": run_two_stage,
                    "legacy_instruction": run_legacy_instruction}


def _sampler_overrides(args):
    return {key: value for key, value in (
        ("top_p", args.top_p), ("top_k", args.top_k), ("min_p", args.min_p),
        ("presence_penalty", args.presence_penalty),
        ("repeat_penalty", args.repeat_penalty),
        ("repeat_last_n", args.repeat_last_n),
        ("frequency_penalty", args.frequency_penalty),
        ("typical_p", args.typical_p)) if value is not None}


def _body(cfg, messages, args):
    body = {"model": llm.resolve_model(cfg), "messages": messages,
            "temperature": args.temperature, "max_tokens": args.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False}}
    if args.seed is not None:
        body["seed"] = args.seed
    body.update(_sampler_overrides(args))
    return body


def _token(cfg):
    return llm.resolve_token(cfg) or None


def _value(call):
    """The payload of a wrapped call, or None when the call failed."""
    return call.get("value") if call and call.get("ok") else None


def _value_text(call):
    """Text of a wrapped call: chat() returns a dict, polish_candidate a str."""
    value = _value(call)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("text") or ""
    return ""


def _reasoning(call):
    value = _value(call)
    return (value.get("reasoning") or None) if isinstance(value, dict) else None


def _call_metadata(call):
    """Persist call telemetry and a reasoning flag, never reasoning text."""
    value = _value(call)
    value = value if isinstance(value, dict) else {}
    return {
        "stage": call.get("stage"),
        "seconds": call.get("seconds"),
        "error": call.get("error"),
        "finish_reason": call.get("finish_reason"),
        "total_seconds": value.get("total_seconds"),
        "ttft_seconds": value.get("ttft_seconds"),
        "prompt_tokens": value.get("prompt_tokens"),
        "completion_tokens": value.get("completion_tokens"),
        "streamed": value.get("streamed"),
        "reasoning_present": bool(value.get("reasoning")),
    }


def _timing(call):
    value = _value(call)
    if not isinstance(value, dict):
        return {"total_seconds": (call or {}).get("seconds"), "ttft_seconds": None,
                "prompt_tokens": None, "completion_tokens": None,
                "finish_reasons": []}
    return {
        "total_seconds": value.get("total_seconds"),
        "ttft_seconds": value.get("ttft_seconds"),
        "prompt_tokens": value.get("prompt_tokens"),
        "completion_tokens": value.get("completion_tokens"),
        "finish_reasons": [value.get("finish_reason")],
    }


def _aggregate_call_timing(calls):
    """Sum both model calls and generation phases; expose extraction TTFT first."""
    values = [_value(call) for call in calls]
    values = [value for value in values if isinstance(value, dict)]
    model_times = [value.get("total_seconds") for value in values
                   if isinstance(value.get("total_seconds"), (int, float))]
    generation_times = []
    for value in values:
        total = value.get("total_seconds")
        if not isinstance(total, (int, float)) or total <= 0:
            continue
        ttft = value.get("ttft_seconds")
        generation_times.append(
            total - ttft if isinstance(ttft, (int, float)) and 0 < ttft < total
            else total)
    return {
        "total_seconds": sum(model_times) if model_times else None,
        "generation_seconds": sum(generation_times) if generation_times else None,
        "ttft_seconds": values[0].get("ttft_seconds") if values else None,
        "prompt_tokens": _sum(value.get("prompt_tokens") for value in values),
        "completion_tokens": _sum(value.get("completion_tokens") for value in values),
        "finish_reasons": [value.get("finish_reason") for value in values
                           if value.get("finish_reason") is not None],
    }


# --------------------------------------------------------------------------
# Invariants (defensible only)
# --------------------------------------------------------------------------

# Function words ignored when matching a spoken restriction, so that
# "sin tecnicismos" also matches "sin los tecnicismos" and "un maximo de diez
# lineas" matches "maximo diez lineas". Negations, quantifiers and limits are
# NOT ignored: "sin X" can never match "con X".
_RESTRICTION_FUNCTION_WORDS = frozenset({
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "y", "que", "en", "a", "al", "para", "se", "su", "sus", "lo", "le",
    "les", "es", "sea", "lo", "el", "por",
})


def _filter_words(words):
    return [word for word in words if word not in _RESTRICTION_FUNCTION_WORDS]


def _restriction_variants(restriction):
    """A restriction is a phrase, or {"any": [...]} when inflection varies."""
    if isinstance(restriction, dict):
        return [str(item) for item in restriction.get("any") or []]
    return [str(restriction)]


def _restriction_ok(restriction, filtered_words):
    for variant in _restriction_variants(restriction):
        if _phrase_in(filtered_words, " ".join(_filter_words(_fold_words(variant)))):
            return True
    return False


def _check_case(case, strategy, response, fields=None):
    text = response or ""
    words = _fold_words(text)
    filtered = _filter_words(words)
    strict = _strict_words(text)
    anchors = case.get("anchors") or []
    forbidden = case.get("forbidden") or []
    restrictions = (case.get("expected") or {}).get("restrictions") or []
    unknowns = (case.get("expected") or {}).get("unknowns") or []
    minimum = (case.get("length") or {}).get("min_words") or 0
    maximum = (case.get("length") or {}).get("max_words")
    hits_forbidden = [item for item in forbidden if _phrase_in(words, item)]
    missing_anchors = [item for item in anchors if not _phrase_in(words, item)]
    missing_strict = [item for item in anchors if not _phrase_in(strict, item)]
    missing_restrictions = [item for item in restrictions
                            if not _restriction_ok(item, filtered)]
    checks = {
        "empty_output": not text.strip(),
        "anchors_present": not missing_anchors,
        "anchors_missing": missing_anchors,
        "anchors_present_strict": not missing_strict,
        "anchors_missing_strict": missing_strict,
        "forbidden_absent": not hits_forbidden,
        "forbidden_hits": hits_forbidden,
        "restrictions_present": not missing_restrictions,
        "restrictions_missing": missing_restrictions,
        "unknowns_declared": unknowns,
        "unknown_safe": not hits_forbidden,
        "length_ok": (minimum <= len(words) <= maximum) if maximum else len(words) >= minimum,
        "word_count": len(words),
        "structured_fields_filled": None,
    }
    if strategy == "structured":
        checks["structured_fields_filled"] = bool(fields and fields.get("objetivo"))
    elif strategy == "two_stage":
        checks["structured_fields_filled"] = bool(fields and fields.get("intent"))
    return checks


def _check_applies(strategy):
    return strategy in ("direct", "structured", "two_stage")


def _checks_note(strategy):
    """Why a run's prompt invariants do not apply, when they do not."""
    if _check_applies(strategy):
        return None
    if strategy == "baseline":
        return ("baseline produce una correccion del dictado, no un prompt: los "
                "invariantes de prompt no aplican a esta corrida")
    return ("legacy_instruction produce una correccion del dictado (llm.DEFAULT_SYSTEM), "
            "no un prompt: los invariantes de prompt no aplican a esta corrida")


# --------------------------------------------------------------------------
# Runner plumbing
# --------------------------------------------------------------------------

def _client_factory(strategy):
    """Wrap every outbound call with timing and error capture."""
    def make(callable_):
        started = time.perf_counter()
        record = {"call": strategy, "seconds": None, "ok": False, "value": None,
                  "error": None, "finish_reason": None}
        try:
            value = callable_()
            record["value"] = value
            record["ok"] = True
            if isinstance(value, dict):
                record["finish_reason"] = value.get("finish_reason")
        except urllib.error.HTTPError as exc:  # server answered with an error
            body = exc.read().decode("utf-8", "replace")[:300]
            record["error"] = f"HTTPError {exc.code}: {body}"
        except Exception as exc:  # noqa: BLE001 - network/timeout/parse, recorded
            record["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            record["seconds"] = time.perf_counter() - started
        return record
    return make


def _select_cases(cases, args):
    selected = list(cases)
    if args.categories:
        wanted = {item.strip() for item in args.categories.split(",") if item.strip()}
        selected = [case for case in selected if case["category"] in wanted]
    if args.cases:
        wanted = {item.strip() for item in args.cases.split(",") if item.strip()}
        selected = [case for case in selected if case["id"] in wanted]
    return selected


def _spread(cases, limit):
    """Take up to `limit` cases spread across categories, in corpus order."""
    if limit is None or limit >= len(cases):
        return list(cases)
    buckets = {}
    for case in cases:
        buckets.setdefault(case["category"], []).append(case)
    spread = []
    index = 0
    while len(spread) < limit:
        added = False
        for category in list(buckets):
            bucket = buckets[category]
            if index < len(bucket) and len(spread) < limit:
                spread.append(bucket[index])
                added = True
        if not added:
            break
        index += 1
    return spread


def _rubric_metadata():
    rubric = load_rubric()
    return {
        "path": _portable_path(RUBRIC_PATH),
        "schema": rubric.get("schema"),
        "version": rubric.get("version"),
        "status": rubric.get("status"),
        "sha256": rubric_digest(),
        "prescoring_sha256": prescoring_digest(),
        "critical_errors": [item.get("id") for item in
                            rubric.get("critical_errors") or []],
        "rating_dimensions": [item.get("id") for item in
                              (rubric.get("ratings") or {}).get("dimensions") or []],
        "acceptance_gates": rubric.get("acceptance_gates"),
    }


_SNAPSHOT_PROPS_KEYS = ("build_info", "model_alias", "model_ftype", "total_slots",
                        "is_sleeping", "chat_template_caps", "modalities",
                        "bos_token", "eos_token")

# Server-side sampler defaults: without these, a run's effective sampling is not
# auditable, because the runner only overrides temperature, max_tokens, seed and
# stream. Values only, never the surrounding payload (no chat template, no paths).
_GENERATION_DEFAULT_KEYS = ("temperature", "top_p", "top_k", "min_p",
                            "repeat_penalty", "repeat_last_n", "presence_penalty",
                            "frequency_penalty", "typical_p", "n_predict")
_INHERITED_SAMPLER_KEYS = ("top_p", "top_k", "min_p", "repeat_penalty",
                           "repeat_last_n", "presence_penalty", "frequency_penalty",
                           "typical_p", "n_predict")

_PATH_KEYS = ("model_path", "path", "model_file", "model_dir", "lora_path",
              "mmproj_path")


def _basename(value):
    """Last path component for Windows and POSIX values, without os.path."""
    text = str(value or "")
    for separator in ("\\", "/"):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]
    return text


def _portable_path(value):
    """A path suitable for a shared artifact: no user profile, no absolute root.

    Repository-internal paths become repo-relative; paths under the user profile
    become environment placeholders; anything else keeps only its last
    component. Never applied to URLs.

    The repository wins over the user profile: a path inside the checkout is the
    repository itself, not a leak of the user's home, even when the checkout
    lives under it (CI checks out in `/home/runner/work/...`). A genuinely
    external file still gets its home directory masked. Only absolute paths can
    be inside the repository: a relative one is resolved against the current
    directory and would look internal by accident.
    """
    text = str(value or "")
    if not text:
        return text
    root = os.path.dirname(os.path.dirname(HERE))
    if os.path.isabs(text):
        try:
            relative = os.path.relpath(text, root)
            if (relative != os.pardir
                    and not relative.startswith(os.pardir + os.sep)):
                return relative.replace(os.sep, "/")
        except ValueError:
            pass
    for variable, placeholder in (("LOCALAPPDATA", "%LOCALAPPDATA%"),
                                  ("APPDATA", "%APPDATA%"),
                                  ("TEMP", "%TEMP%"), ("TMP", "%TMP%"),
                                  ("USERPROFILE", "%USERPROFILE%"),
                                  ("HOME", "%HOME%")):
        base = os.environ.get(variable)
        if base and text.lower().startswith(base.lower()):
            return placeholder + text[len(base):]
    return _basename(text)


def _sanitise_snapshot_value(value):
    """Drop or reduce every path-shaped field, recursively."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if key in _PATH_KEYS:
                reduced = _basename(item)
                if reduced:
                    clean["model_file" if key in ("model_path", "path", "model_file")
                          else key] = reduced
                continue
            clean[key] = _sanitise_snapshot_value(item)
        return clean
    if isinstance(value, list):
        return [_sanitise_snapshot_value(item) for item in value]
    return value


def _generation_defaults(props):
    """Sampler defaults the server would apply when the request omits them."""
    params = ((props or {}).get("default_generation_settings") or {}).get("params") or {}
    return {key: params[key] for key in _GENERATION_DEFAULT_KEYS if key in params}


def _snapshot_summary(props):
    """Portable runtime summary: settings and identifiers only, never payloads."""
    default = (props or {}).get("default_generation_settings") or {}
    return {
        "build_info": props.get("build_info"),
        "model_file": _basename(props.get("model_path")),
        "model_alias": props.get("model_alias"),
        "model_ftype": props.get("model_ftype"),
        "n_ctx": default.get("n_ctx"),
        "n_parallel": default.get("n_parallel") or props.get("total_slots"),
        "total_slots": props.get("total_slots"),
        "is_sleeping": props.get("is_sleeping"),
        "chat_template_caps": props.get("chat_template_caps"),
        "generation_defaults": _generation_defaults(props),
    }


def runtime_snapshot(base_url, token=None, timeout=10.0):
    """Best-effort /props and /v1/models snapshot, with no private paths.

    Only settings and identifiers are kept: the model's absolute path is reduced
    to its file name, and the full /props payload (chat template, UI state) is
    not stored.
    """
    snapshot = {}
    raw = {}
    for suffix, key in (("/props", "props"), ("/v1/models", "models")):
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        try:
            with urllib.request.urlopen(url + suffix, timeout=timeout) as response:
                raw[key] = json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001 - optional metadata
            raw[key] = {"error": f"{type(exc).__name__}: {exc}"}
    props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
    default = props.get("default_generation_settings") or {}
    snapshot["props"] = {key: props[key] for key in _SNAPSHOT_PROPS_KEYS if key in props}
    if props.get("model_path"):
        snapshot["props"]["model_file"] = _basename(props["model_path"])
    snapshot["models"] = _sanitise_snapshot_value(raw.get("models"))
    snapshot["summary"] = _snapshot_summary(props)
    return snapshot


def _git_head():
    try:
        import subprocess
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE,
                                capture_output=True, text=True, timeout=10,
                                check=False)
        return result.stdout.strip() or None
    except Exception:  # noqa: BLE001 - metadata only
        return None


def run_matrix(cases, args, models, strategies, context_modes, output):
    digests = corpus_digests()
    rows = []
    runs = []
    for model in models:
        for strategy in strategies:
            for context_mode in context_modes:
                run_id = f"{model}|{strategy}|{context_mode}"
                cfg = {"llm_url": args.url, "llm_model": model}
                client = _client_factory(strategy)
                runner = STRATEGY_RUNNERS[strategy]
                run_rows = []
                for case in cases:
                    messages = build_messages(strategy, case, context_mode, cfg)
                    leaks = assert_no_gold_leak(case, messages, context_mode,
                                                strategy, cfg)
                    started = time.perf_counter()
                    try:
                        outcome = runner(case, context_mode, cfg, args, client)
                        error = None
                    except Exception as exc:  # noqa: BLE001 - one bad case, not the run
                        outcome = {"response": "", "checks": {}, "calls": [],
                                   "timing": {}}
                        error = f"{type(exc).__name__}: {exc}"
                    wall = time.perf_counter() - started
                    timing = outcome.get("timing") or {}
                    row = {
                        "run_id": run_id,
                        "case_id": case["id"],
                        "category": case["category"],
                        "model": model,
                        "strategy": strategy,
                        "context_mode": context_mode,
                        "input": case["input"],
                        "response": outcome.get("response", ""),
                        "reasoning_present": bool(outcome.get("reasoning")),
                        "checks_apply": _check_applies(strategy),
                        "checks": outcome.get("checks", {}),
                        "gold_leaks": leaks,
                        "error": error or outcome.get("error")
                                 or _first_error(outcome.get("calls") or []),
                        "wall_seconds": wall,
                        "ttft_seconds": timing.get("ttft_seconds"),
                        "model_seconds": timing.get("total_seconds"),
                        "generation_seconds": timing.get("generation_seconds"),
                        "prompt_tokens": timing.get("prompt_tokens"),
                        "completion_tokens": timing.get("completion_tokens"),
                        "finish_reasons": timing.get("finish_reasons") or [],
                        "metrics_unavailable": timing.get("metrics_unavailable"),
                        "calls": [_call_metadata(call)
                                  for call in outcome.get("calls") or []],
                    }
                    if strategy == "two_stage":
                        row["extraction_ok"] = outcome.get("extraction_ok", False)
                        row["extraction_error"] = outcome.get("extraction_error")
                        row["generation_error"] = outcome.get("generation_error")
                        row["extraction_raw"] = outcome.get("extraction_raw")
                        row["fields"] = outcome.get("fields")
                    if strategy == "structured":
                        row["extraction_ok"] = outcome.get("extraction_raw") is not None
                        row["render_error"] = outcome.get("render_error")
                        row["fields"] = outcome.get("fields")
                    if strategy == "baseline":
                        row["candidate"] = outcome.get("candidate")
                        row["meaning_check_passed"] = outcome.get("meaning_check_passed")
                        row["production_replay_matches_maybe_polish"] = outcome.get(
                            "production_replay_matches_maybe_polish")
                    row["tokens_per_second"] = throughput(row)
                    rows.append(row)
                    run_rows.append(row)
                    if not args.quiet:
                        status = "err" if row["error"] else "ok"
                        print(f"  [{run_id}] {case['id']:<24} {status} "
                              f"{row['wall_seconds']:.2f}s "
                              f"tok={row['prompt_tokens']}/{row['completion_tokens']} "
                              f"fr={row['finish_reasons']}", flush=True)
                runs.append(_summarise_run(run_id, model, strategy, context_mode,
                                           run_rows, digests))
    metadata = {
        "scored": not args.fixture,
        "transport": "fixture" if args.fixture else "endpoint",
        "cases_sha256": (hashlib.sha256(json.dumps(
            _references_projection(cases), ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()),
        "runner": {"path": _portable_path(os.path.abspath(__file__)),
                   "sha256": runner_digest()},
        "corpus": {"path": _portable_path(CORPUS_PATH), **digests,
                   "frozen_manifest": read_manifest(),
                   "frozen_problems": verify_manifest()},
        "cases_run": {"count": len(cases), "ids": [case["id"] for case in cases]},
        "rubric": _rubric_metadata(),
        "matrix": {"models": models, "strategies": strategies,
                   "context_modes": context_modes, "cases": len(cases),
                   "runs": len(runs), "rows": len(rows)},
        "options": {
            "url": args.url, "timeout_seconds": args.timeout,
            "max_tokens": args.max_tokens, "temperature": args.temperature,
            "seed": args.seed, "stream": args.stream,
            "structured_mode": args.structured_mode,
            "verify_baseline": args.verify_baseline,
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_channel_note": (
                "reasoning_present records whether a non-empty reasoning channel "
                "was returned; enable_thinking=false is a template hint, not proof "
                "that the runtime emitted no reasoning channel."),
            "request_overrides": {"temperature": args.temperature,
                                  "max_tokens": args.max_tokens,
                                  "seed": args.seed, "stream": args.stream,
                                  **_sampler_overrides(args)},
            "inherited_from_server": [key for key in _INHERITED_SAMPLER_KEYS
                                      if key not in _sampler_overrides(args)],
            "prompts": {"direct_system_sha256": _sha(DIRECT_SYSTEM),
                        "structured_system_sha256": _sha(STRUCTURED_SYSTEM),
                        "schema_sha256": _sha(json.dumps(INSTRUCTION_SCHEMA,
                                                         sort_keys=True)),
                        "two_stage_extract_system_sha256": _sha(TWO_STAGE_EXTRACT_SYSTEM),
                        "two_stage_generate_system_sha256": _sha(TWO_STAGE_GENERATE_SYSTEM),
                        "two_stage_schema_sha256": _sha(json.dumps(TWO_STAGE_SCHEMA,
                                                                  sort_keys=True))},
        },
        "runtime": runtime_snapshot(args.url, timeout=min(10.0, args.timeout))
        if args.url and not args.dry_run else None,
        "repo_head": _git_head(),
        "started_at": args._started_at,
        "finished_at": time.time(),
    }
    _write_outputs(output, rows, runs, metadata, cases, args)
    return rows, runs, metadata


def _first_error(calls):
    for call in calls:
        if call.get("error"):
            return call["error"]
    return None


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _summarise_run(run_id, model, strategy, context_mode, rows, digests):
    applicable = [row for row in rows if row["checks_apply"]] or rows
    applies = _check_applies(strategy)
    def rate(key):
        if not applies:
            return None
        values = [bool((row.get("checks") or {}).get(key)) for row in applicable
                  if (row.get("checks") or {}).get(key) is not None]
        return (sum(values) / len(values)) if values else None
    anchor_failures = sum(len((row.get("checks") or {}).get("anchors_missing") or [])
                          for row in applicable) if applies else None
    forbidden_hits = sum(len((row.get("checks") or {}).get("forbidden_hits") or [])
                         for row in applicable) if applies else None
    return {
        "run_id": run_id,
        "model": model,
        "strategy": strategy,
        "context_mode": context_mode,
        "cases": len(rows),
        "checks_apply": applies,
        "checks_note": _checks_note(strategy),
        "metrics_unavailable": (BASELINE_METRICS_UNAVAILABLE
                                if strategy == "baseline" else None),
        "errors": sum(1 for row in rows if row["error"]),
        "gold_leaks": sum(len(row["gold_leaks"] or []) for row in rows),
        "check_rates": {
            "anchors_present": rate("anchors_present"),
            "forbidden_absent": rate("forbidden_absent"),
            "restrictions_present": rate("restrictions_present"),
            "length_ok": rate("length_ok"),
            "empty_output": rate("empty_output"),
            "structured_fields_filled": rate("structured_fields_filled"),
        },
        "anchor_failures": anchor_failures,
        "forbidden_hits": forbidden_hits,
        "reasoning": {
            "rows_present": sum(1 for row in rows if row.get("reasoning_present")),
            "calls_present": sum(
                1 for row in rows for call in (row.get("calls") or [])
                if call.get("reasoning_present")),
            "by_stage": {
                stage: sum(
                    1 for row in rows for call in (row.get("calls") or [])
                    if call.get("stage") == stage and call.get("reasoning_present"))
                for stage in sorted({
                    call.get("stage") for row in rows for call in (row.get("calls") or [])
                    if call.get("stage")
                })
            },
        },
        "finish_reasons": _counter(
            reason for row in rows for reason in (row.get("finish_reasons") or [])),
        "tokens": {
            "prompt": _sum(row.get("prompt_tokens") for row in rows),
            "completion": _sum(row.get("completion_tokens") for row in rows),
        },
        "wall_seconds": _percentiles([row.get("wall_seconds") for row in rows]),
        "ttft_seconds": _percentiles([row.get("ttft_seconds") for row in rows]),
        "model_seconds": _percentiles([row.get("model_seconds") for row in rows]),
        "tokens_per_second": _percentiles([row.get("tokens_per_second") for row in rows]),
        "corpus_references_sha256": digests["references_sha256"],
    }


def _sum(values):
    numbers = [value for value in values if isinstance(value, (int, float))]
    return sum(numbers) if numbers else None


def _counter(values):
    counts = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _write_outputs(output, rows, runs, metadata, cases, args):
    if args.dry_run:
        return
    os.makedirs(output, exist_ok=True)
    with open(os.path.join(output, "rows.jsonl"), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(os.path.join(output, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump({"runs": runs, "metadata": metadata}, handle,
                  ensure_ascii=False, indent=2)
    with open(os.path.join(output, "metadata.json"), "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
    _write_blinded(output, rows, cases, args, load_rubric())


def _write_blinded(output, rows, cases, args, rubric):
    """Blind review rows: input, authorized context, frozen reference, response.

    No strategy, no model, no key material. IDs are random (seeded) and the
    order is shuffled with the same seed; the mapping lives in blinded_key.json.
    """
    by_id = {case["id"]: case for case in cases}
    entries = [(row, by_id.get(row["case_id"]) or {})
               for row in rows if (row.get("response") or "").strip()]
    rng = random.Random(args.seed if args.seed is not None else 0)
    rng.shuffle(entries)
    dimensions = [item.get("id") for item in
                  (rubric.get("ratings") or {}).get("dimensions") or []
                  if isinstance(item, dict)]
    ids, used = [], set()
    for _ in entries:
        row_id = "row-" + "".join(rng.choice("0123456789abcdef") for _ in range(10))
        while row_id in used:
            row_id = "row-" + "".join(rng.choice("0123456789abcdef") for _ in range(10))
        used.add(row_id)
        ids.append(row_id)

    with open(os.path.join(output, "blinded_review.jsonl"), "w", encoding="utf-8") as handle:
        for row_id, (row, case) in zip(ids, entries):
            handle.write(json.dumps({
                "row_id": row_id,
                "case_id": row["case_id"],
                "category": row["category"],
                "input": case.get("input", row["input"]),
                "authorized_context": _context_block(case, row["context_mode"]),
                "reference_prompt": case.get("reference_prompt", ""),
                "response": row["response"],
                "ratings": {dimension: None for dimension in dimensions},
                "critical_errors": [],
            }, ensure_ascii=False) + "\n")
    key = [{"row_id": row_id, "case_id": row["case_id"], "model": row["model"],
            "strategy": row["strategy"], "context_mode": row["context_mode"],
            "run_id": row["run_id"]}
           for row_id, (row, _case) in zip(ids, entries)]
    with open(os.path.join(output, "blinded_key.json"), "w", encoding="utf-8") as handle:
        json.dump(key, handle, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------

def gold_self_check(cases):
    """Every reference prompt must satisfy its own anchors/forbidden/length."""
    problems = []
    for case in cases:
        checks = _check_case(case, "direct", case["reference_prompt"])
        if not checks.get("anchors_present"):
            problems.append(f"{case['id']}: faltan anclas {checks['anchors_missing']}")
        if not checks.get("forbidden_absent"):
            problems.append(f"{case['id']}: prohibidos presentes {checks['forbidden_hits']}")
        if not checks.get("restrictions_present"):
            problems.append(
                f"{case['id']}: faltan restricciones {checks['restrictions_missing']}")
        if not checks.get("length_ok"):
            problems.append(
                f"{case['id']}: longitud {checks['word_count']} fuera de {case['length']}")
    return problems


def dry_run(cases, args, models, strategies, context_modes):
    digests = corpus_digests(cases=cases)
    problems = verify_manifest()
    print(f"Corpus            : {CORPUS_PATH}")
    print(f"Casos             : {len(cases)}")
    print(f"corpus_file_sha256: {digests['corpus_file_sha256']}")
    print(f"references_sha256 : {digests['references_sha256']}")
    rubric = load_rubric()
    print(f"Rubric            : {rubric.get('version')} ({rubric.get('status')}) "
          f"sha256 {rubric_digest()[:16]}… prescoring {prescoring_digest()[:16]}…")
    print(f"  errores criticos: {[item.get('id') for item in rubric.get('critical_errors') or []]}")
    print(f"  dimensiones 1-5 : {[item.get('id') for item in (rubric.get('ratings') or {}).get('dimensions') or []]}")
    if problems:
        print("Referencias congeladas: ROTAS")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("Referencias congeladas: verificadas contra v2p_references.sha256")
    counts = _counter(case["category"] for case in cases)
    for category, count in sorted(counts.items()):
        print(f"  {category:<28} {count}")
    print(f"Matriz            : {len(models)} modelo(s) x {len(strategies)} estrategia(s) "
          f"x {len(context_modes)} contexto(s) x {len(cases)} caso(s) = "
          f"{len(models) * len(strategies) * len(context_modes) * len(cases)} filas")
    leaks = 0
    for strategy in strategies:
        for context_mode in context_modes:
            for case in cases:
                messages = build_messages(strategy, case, context_mode,
                                          {"llm_url": args.url, "llm_model": models[0]})
                found = assert_no_gold_leak(case, messages, context_mode, strategy,
                                            {"llm_url": args.url,
                                             "llm_model": models[0]})
                if found:
                    leaks += 1
                    if leaks <= 10:
                        print(f"  FUGA {case['id']} {strategy}/{context_mode}: {found}")
    print(f"Chequeo de fuga de gold: {'OK' if not leaks else f'{leaks} problema(s)'}")
    unauthorized = 0
    for case in cases:
        selection = case.get("selection") or {}
        if not selection.get("text") or selection.get("authorized"):
            continue
        text = _fold(selection["text"])
        for mode in CONTEXT_MODES:
            if text in _fold(_context_block(case, mode)):
                unauthorized += 1
                if unauthorized <= 5:
                    print(f"  SELECCION NO AUTORIZADA en el contexto inyectado: "
                          f"{case['id']} / {mode}")
        if text in _fold(case.get("reference_prompt", "")):
            unauthorized += 1
            print(f"  SELECCION NO AUTORIZADA en el reference_prompt: {case['id']}")
    print(f"Seleccion no autorizada fuera de todo material: "
          f"{'OK' if not unauthorized else f'{unauthorized} problema(s)'}")
    gold_failures = gold_self_check(cases)
    print(f"Autoconsistencia del gold (el reference_prompt cumple sus invariantes): "
          f"{'OK' if not gold_failures else f'{len(gold_failures)} problema(s)'}")
    for failure in gold_failures[:20]:
        print(f"  - {failure}")
    sample = _spread(cases, 1)[0]
    for strategy in strategies:
        messages = build_messages(strategy, sample, context_modes[0],
                                  {"llm_url": args.url, "llm_model": models[0]})
        print(f"\n--- {strategy} / {context_modes[0]} / {sample['id']} ---")
        for message in messages:
            print(f"[{message['role']}] {message['content'][:400]}")
    if strategies and "structured" in strategies:
        print("\n--- renderer determinista (ejemplo sintetico) ---")
        print(render_prompt({"objetivo": "resumir el informe de ventas de octubre",
                             "destinatario": "Laura Mendez", "formato": None,
                             "tono": None, "plazo": "viernes",
                             "condiciones": ["dos parrafos"],
                             "restricciones": ["sin nombres propios"],
                             "datos_dichos": ["informe de ventas de octubre"],
                             "datos_faltantes": [], "contexto_usado": []}))
    return 0 if not leaks else EXIT_USAGE


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="OpenAI-compatible base URL (e.g. http://127.0.0.1:8080/v1)")
    parser.add_argument("--models", default="local",
                        help="Comma-separated model ids; the server must know them")
    parser.add_argument("--corpus", default=CORPUS_PATH)
    parser.add_argument("--out-dir", default=os.path.join(
        os.environ.get("TEMP") or "/tmp", "instant2-v2p"))
    parser.add_argument("--strategies", default="baseline,direct,structured",
                        help="Comma-separated subset of " + ",".join(STRATEGIES))
    parser.add_argument("--context", default="off",
                        help="Comma-separated subset of " + ",".join(CONTEXT_MODES))
    parser.add_argument("--categories", help="Filter by category")
    parser.add_argument("--cases", help="Filter by case id")
    parser.add_argument("--limit", type=int, help="First N cases (spread by category)")
    parser.add_argument("--smoke", type=int, help="Shortcut for --limit N and quiet output")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--top-p", type=float)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--min-p", type=float)
    parser.add_argument("--presence-penalty", type=float)
    parser.add_argument("--repeat-penalty", type=float)
    parser.add_argument("--repeat-last-n", type=int)
    parser.add_argument("--frequency-penalty", type=float)
    parser.add_argument("--typical-p", type=float)
    parser.add_argument("--structured-mode", choices=("jsonschema", "prompt-json"),
                        default="jsonschema")
    parser.add_argument("--no-stream", dest="stream", action="store_false",
                        help="Disable streaming (no TTFT measurement)")
    parser.add_argument("--verify-baseline", action="store_true",
                        help="Also run llm.maybe_polish and compare with the replay")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate corpus, freeze and prompt assembly; no network")
    parser.add_argument("--fixture", action="store_true",
                        help="Plumbing smoke against a local stub server: no model, no "
                             "live endpoint, and NOT scored (uses built-in smoke cases)")
    parser.add_argument("--freeze", action="store_true",
                        help="Write v2p_references.sha256 from the current corpus")
    parser.add_argument("--allow-reference-change", action="store_true",
                        help="Run even if the frozen references changed")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args._started_at = time.time()
    if args.smoke:
        args.limit = args.smoke
    try:
        document = load_corpus(args.corpus)
    except (OSError, ValueError) as exc:
        print(f"corpus invalido: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.freeze:
        digests = write_manifest()
        print(f"Referencias congeladas: {digests['case_count']} casos")
        print(f"  references_sha256  {digests['references_sha256']}")
        print(f"  corpus_file_sha256 {digests['corpus_file_sha256']}")
        print(f"  escrito en {MANIFEST_PATH}")
        return 0

    cases = _select_cases(document["cases"], args)
    if args.limit:
        cases = _spread(cases, args.limit)
    if not cases:
        print("no hay casos para correr (revisar filtros)", file=sys.stderr)
        return EXIT_USAGE
    if args.list_cases:
        for case in document["cases"]:
            print(f"{case['id']:<28} {case['category']}")
        print(f"total: {len(document['cases'])}")
        return 0

    strategies = [item.strip() for item in args.strategies.split(",") if item.strip()]
    context_modes = [item.strip() for item in args.context.split(",") if item.strip()]
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    for name in strategies:
        if name not in STRATEGIES:
            print(f"estrategia desconocida: {name}", file=sys.stderr)
            return EXIT_USAGE
    for name in context_modes:
        if name not in CONTEXT_MODES:
            print(f"modo de contexto desconocido: {name}", file=sys.stderr)
            return EXIT_USAGE
    if not models:
        print("falta --models", file=sys.stderr)
        return EXIT_USAGE

    if args.dry_run:
        return dry_run(cases, args, models, strategies, context_modes)

    if args.fixture:
        smoke = list(SMOKE_CASES)
        if args.limit:
            smoke = _spread(smoke, args.limit)
        if args.models.strip() == "local":
            models = ["fixture-model"]
        print(f"Modo fixture: transporte simulado, NO puntuado; {len(smoke)} casos de humo "
              "disjuntos del corpus congelado.")
        with FixtureServer() as fixture:
            args.url = fixture.url
            return _run_and_report(smoke, args, models, strategies, context_modes)

    if not args.url:
        print("falta --url (o usar --dry-run o --fixture)", file=sys.stderr)
        return EXIT_USAGE
    return _run_and_report(cases, args, models, strategies, context_modes)


def _verify_smoke(rows, output):
    """Plumbing invariants the fixture smoke must prove (fails the run if not)."""
    problems = []
    by_key = {(row["case_id"], row["strategy"]): row for row in rows}
    for strategy in ("direct", "structured", "legacy_instruction", "two_stage"):
        if (("smoke_simple", strategy) not in by_key
                and ("smoke_long_stream", strategy) not in by_key):
            continue
        for case_id in ("smoke_simple", "smoke_long_stream"):
            row = by_key.get((case_id, strategy))
            if row is None:
                problems.append(f"falta la fila {case_id}/{strategy}")
                continue
            if row["error"]:
                problems.append(f"{case_id}/{strategy}: error inesperado {row['error']}")
            if not row["ttft_seconds"] or not row["model_seconds"]:
                problems.append(f"{case_id}/{strategy}: sin ttft/total")
            if not row["prompt_tokens"] or not row["completion_tokens"]:
                problems.append(f"{case_id}/{strategy}: sin conteo de tokens")
            if (row["checks"] or {}).get("empty_output"):
                problems.append(f"{case_id}/{strategy}: respuesta vacia")
            if row["finish_reasons"] != ["stop"] * (2 if strategy == "two_stage" else 1):
                problems.append(f"{case_id}/{strategy}: finish_reason {row['finish_reasons']}")
            if strategy == "two_stage":
                stages = [call.get("stage") for call in row["calls"]]
                if stages != ["extract", "generate"] or not row.get("extraction_ok"):
                    problems.append("two_stage: el flujo correcto no completó ambas etapas")
        truncated = by_key.get(("smoke_truncated", strategy))
        if truncated and truncated["finish_reasons"] != ["length"]:
            problems.append(f"smoke_truncated/{strategy}: no se capturo finish_reason length")
        if strategy == "two_stage" and truncated:
            if (len(truncated["calls"]) != 1 or truncated.get("response")
                    or truncated.get("extraction_ok")):
                problems.append("two_stage: extracción truncada no cerró el flujo")
        errored = by_key.get(("smoke_http_error", strategy))
        if errored and not errored["error"]:
            problems.append(f"smoke_http_error/{strategy}: no se capturo el error HTTP")
        if strategy == "two_stage" and errored and len(errored["calls"]) != 1:
            problems.append("two_stage: error de extracción disparó la segunda etapa")
        # deterministic renderer must fill the objective from the stub extraction
        if strategy == "structured":
            row = by_key.get(("smoke_simple", "structured"))
            if row and not (row.get("fields") or {}).get("objetivo"):
                problems.append("structured: el renderer no recibio 'objetivo'")
    for name in ("rows.jsonl", "summary.json", "metadata.json",
                 "blinded_review.jsonl", "blinded_key.json"):
        if not os.path.isfile(os.path.join(output, name)):
            problems.append(f"falta el archivo de salida {name}")
    review = _count_lines(os.path.join(output, "blinded_review.jsonl"))
    key = _load_json_len(os.path.join(output, "blinded_key.json"))
    if review != key:
        problems.append(f"filas ciegas ({review}) != clave ({key})")
    if review == 0:
        problems.append("no se exportaron filas ciegas")
    dimensions = [item.get("id") for item in
                  (load_rubric().get("ratings") or {}).get("dimensions") or []]
    first = _first_json_line(os.path.join(output, "blinded_review.jsonl"))
    if first is not None:
        for field in ("row_id", "case_id", "input", "authorized_context",
                      "reference_prompt", "response", "ratings", "critical_errors"):
            if field not in first:
                problems.append(f"fila ciega sin campo {field}")
        if not first.get("reference_prompt"):
            problems.append("fila ciega sin reference_prompt congelado")
        if not str(first.get("row_id", "")).startswith("row-"):
            problems.append("row_id no es aleatorio")
        if sorted((first.get("ratings") or {}).keys()) != sorted(dimensions):
            problems.append("la plantilla de puntajes no coincide con el rubric")
    return problems


def _first_json_line(path):
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    return json.loads(line)
    except (OSError, ValueError):
        return None
    return None


def _count_lines(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _load_json_len(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return len(json.load(handle))
    except (OSError, ValueError):
        return 0


def _run_and_report(cases, args, models, strategies, context_modes):
    problems = verify_manifest()
    if problems and not args.allow_reference_change:
        print("referencias congeladas cambiadas; no se corre (usar --allow-reference-change "
              "solo si el cambio es deliberado):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_FROZEN

    digests = corpus_digests()
    print(f"V2P: {len(models)} modelo(s) x {len(strategies)} estrategia(s) x "
          f"{len(context_modes)} contexto(s) x {len(cases)} caso(s)")
    print(f"Referencias congeladas: {digests['references_sha256'][:16]}… "
          f"({digests['case_count']} casos en el corpus completo, "
          f"verificados ANTES de esta salida)")
    if not args.fixture:
        gold_problems = gold_self_check(cases)
        if gold_problems:
            print("referencias inconsistentes con sus propios invariantes; no se puntua:",
                  file=sys.stderr)
            for problem in gold_problems:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_FROZEN
    rows, runs, metadata = run_matrix(cases, args, models, strategies, context_modes,
                                      args.out_dir)
    print(f"\nFilas: {len(rows)} · errores: {sum(1 for row in rows if row['error'])} · "
          f"fugas de gold: {sum(len(row['gold_leaks'] or []) for row in rows)}")
    for run in runs:
        rates = run["check_rates"]
        print(f"  {run['run_id']:<40} casos={run['cases']} errores={run['errors']} "
              f"anclas={_fmt(rates['anchors_present'])} "
              f"prohibidos={_fmt(rates['forbidden_absent'])} "
              f"restricciones={_fmt(rates['restrictions_present'])} "
              f"longitud={_fmt(rates['length_ok'])} "
              f"p50={_fmt(run['wall_seconds']['p50'], 2)}s "
              f"p95={_fmt(run['wall_seconds']['p95'], 2)}s "
              f"ttft_p50={_fmt(run['ttft_seconds']['p50'], 2)}s")
    print(f"Salida local: {_portable_path(args.out_dir)}")
    if rows and all(row["error"] for row in rows):
        print("todas las solicitudes fallaron: revisar endpoint, modelo o red",
              file=sys.stderr)
        return EXIT_ENDPOINT
    if args.fixture:
        smoke_problems = _verify_smoke(rows, args.out_dir)
        if smoke_problems:
            print("\nSmoke de plumbing: FALLO", file=sys.stderr)
            for problem in smoke_problems:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_SMOKE
        print("Smoke de plumbing: OK (streaming, tokens, finish_reason, error, "
              "renderer, filas ciegas)")
    return 0


def _fmt(value, digits=0):
    if value is None:
        return "n/d"
    if digits:
        return f"{value:.{digits}f}"
    return f"{value:.0%}"


if __name__ == "__main__":
    sys.exit(main())
