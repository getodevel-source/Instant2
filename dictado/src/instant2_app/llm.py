"""Pulido opcional por endpoint; el vocabulario local no requiere conexión.

En Dictado normal, Qwen recibe la hipótesis de ASR y su corrección se usa como
salida final. Si el servidor falla, se conserva el texto corregido localmente.

Seguridad: `http://` en claro solo se permite contra loopback (localhost,
127.* o ::1); cualquier otro `http://` se rechaza con un error accionable
(pasá a `https://` o usá un túnel local). El token (`llm_token` en config o
`INSTANT2_LLM_TOKEN`) viaja en `Authorization: Bearer` y nunca se loguea.
"""
import ipaddress
from collections import Counter
from difflib import SequenceMatcher
import logging
import os
import re
import time
import unicodedata
from urllib.parse import urlsplit

log = logging.getLogger("instant2")

# Hosts donde el claro es aceptable: solo esta máquina (nunca sale a la red).
_LOOPBACK_NAMES = frozenset({"localhost"})


def _is_loopback_host(host):
    """True si `host` es loopback (nombre o IP). Sin DNS: lo que no se puede
    clasificar con certeza, no es loopback."""
    if not host:
        return False
    name = host.strip().strip("[]").lower().rstrip(".")
    if name in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def check_url(url):
    """Valida el endpoint LLM. Devuelve la URL normalizada o lanza ValueError
    accionable. `http://` no-loopback se bloquea (usá `https://`)."""
    target = (url or "").strip()
    if not target:
        return ""
    try:
        parsed = urlsplit(target)
    except ValueError as exc:
        raise ValueError(f"URL del LLM no válida ({exc}); "
                         "usá http://localhost:8080 o https://…") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL del LLM no válida: usá http(s)://host… "
                         "o dejá el campo vacío para no usar red.")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise ValueError(
            f"el LLM en http://{parsed.hostname} viaja en claro por la red: "
            "usá https:// o un endpoint local (http://localhost:… o http://127.0.0.1:…).")
    return target


def resolve_token(cfg=None):
    """Token Bearer del LLM (config `llm_token` o env `INSTANT2_LLM_TOKEN`).
    Vacío = sin auth. Nunca se loguea ni se incluye en mensajes de error."""
    cfg = cfg or {}
    return (cfg.get("llm_token", "")
            or os.environ.get("INSTANT2_LLM_TOKEN", ""))


def resolve_model(cfg=None):
    """Model name accepted by the configured OpenAI-compatible server."""
    cfg = cfg or {}
    value = cfg.get("llm_model", "") or os.environ.get("INSTANT2_LLM_MODEL", "")
    return value.strip() if isinstance(value, str) and value.strip() else "local"

DEFAULT_SYSTEM = (
    "El texto es una hipótesis de reconocimiento de voz en español. Corrige "
    "ortografía, tildes, mayúsculas, puntuación y errores de reconocimiento "
    "que el contexto permita resolver con alta confianza. Conserva la intención, "
    "los nombres propios, las cifras, las fechas, las unidades, las negaciones, "
    "las restricciones y los requisitos. No agregues información, tareas ni "
    "explicaciones. Conserva la variedad regional y el tratamiento usado, "
    "incluidos vos, tú y usted; no lo formalices ni neutralices. Si el texto "
    "contiene marcas claras de voseo como 'podés' o 'guardá', conserva ese "
    "tratamiento en las órdenes directas y sus tildes, como 'compará' y 'señalá'. "
    "No cambies el tiempo, modo ni persona verbal ni conviertas una afirmación "
    "en una orden o viceversa, salvo para corregir un error de reconocimiento "
    "inequívoco. En una negación coordinada, corrige una forma verbal que rompa "
    "la misma orden: 'No borres el aviso ni cambié los nombres' puede ser "
    "'No borres el aviso ni cambies los nombres' cuando esa es la lectura gramatical "
    "clara. No reordenes ni agregues palabras. No omitas ninguna salvo que sea "
    "claramente un fragmento ASR que rompe la gramática y quitarla no elimine "
    "un hecho, negación, límite o requisito. Si una pregunta directa está formulada "
    "como pregunta aunque falten sus signos, agrega '¿' y '?'; no conviertas "
    "afirmaciones ni órdenes claras en preguntas. Si el verbo ya forma una frase "
    "gramatical, no lo sustituyas por una conjugación que parezca más natural; "
    "solo cámbialo si el contexto confirma un error de reconocimiento. Si una palabra es ambigua, "
    "consérvala como está. Usa el glosario solo para elegir la grafía de un "
    "término ya presente; nunca insertes términos del glosario. Devuelve solo "
    "el texto final, sin comillas ni comentarios."
)


VERIFY_SYSTEM = (
    "Compara la transcripción ASR con la propuesta. Responde ACCEPT solo si "
    "preservan el mismo pedido y todos sus datos. Tolera puntuación, tildes, "
    "mayúsculas, un arranque repetido y una corrección de reconocimiento "
    "inequívoca. Protege nombres, cifras, fechas, unidades, negaciones, "
    "restricciones, condiciones, incertidumbre, responsables y requisitos. "
    "También conserva el voseo/tuteo y cada forma verbal: persona, tiempo y "
    "modo. No cambies un verbo solo porque otra conjugación suene más natural. "
    "Acepta una conjugación distinta únicamente si corrige un error ASR claro "
    "y mantiene la misma orden y persona. ACCEPT: 'Necesito que preparen, "
    "necesito que prepares un resumen para Laura Méndez' -> 'Necesito que "
    "prepares un resumen para Laura Méndez' (se quitó un arranque repetido). "
    "ACCEPT: 'Compará ... y señalás solo las diferencias' -> 'Compará ... "
    "y señalá solo las diferencias' (misma orden; conserva 'solo'). ACCEPT: "
    "'No borres la advertencia ni cambié los nombres' -> 'No borres la "
    "advertencia ni cambies los nombres' (corrección clara de la misma orden). "
    "REJECT: 'cuando llega el paquete' -> 'cuando llegue el paquete' (cambia "
    "el modo/tiempo verbal); '38.000 pesos' -> '42.500 pesos'; 'No envíes "
    "el mensaje' -> 'Envía el mensaje'; 'solo las diferencias' -> 'las "
    "diferencias'. Ante cualquier duda sobre un dato o la intención, responde "
    "REJECT. Rechaza también reformulaciones que solo suenen más naturales, "
    "palabras nuevas inferidas del contexto y cambios de artículos o "
    "preposiciones cuando la frase original ya sea válida. Una verificación "
    "de estilo no demuestra que el audio dijera otra cosa. Responde exactamente "
    "ACCEPT o REJECT, sin explicar."
)



def _chat_completion_endpoint(base):
    """Build the compatible chat endpoint from a server/API root URL.

    Settings may contain either ``http://127.0.0.1:8080`` or
    ``http://127.0.0.1:8080/v1``. Do not append a second ``/v1`` in the
    latter case.
    """
    base = base.rstrip("/")
    if base.endswith(("/chat/completions", "/completions", "/completion")):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def service_info(cfg=None, url=None):
    """Describe si el endpoint configurado es local o remoto, sin secretos."""
    target = resolve_url(cfg, url)
    if not target:
        return {"kind": "none", "label": "No hay servidor de lenguaje configurado"}
    try:
        parsed = urlsplit(check_url(target))
        host = parsed.hostname or "servidor"
        local = _is_loopback_host(host)
        display_host = f"[{host}]" if ":" in host else host
        if parsed.port:
            display_host += f":{parsed.port}"
        kind = "local" if local else "remote"
        label = f"Servicio {'local' if local else 'remoto'} · {display_host}"
        return {"kind": kind, "label": label}
    except (ValueError, TypeError):
        return {"kind": "invalid", "label": "La dirección del servidor no es válida"}


def polish_candidate(text, url, timeout=8.0, system=None, context_terms="",
                     token=None, cfg=None, sampling_params=None):
    """Return the raw text candidate from the configured LLM endpoint.

    This deliberately does not validate word order. Dictado normal uses this
    candidate as its final text so Qwen can correct ASR word errors. Callers
    that require an identical word sequence can use :func:`polish` instead.
    """
    import json
    import urllib.request

    if not text or not text.strip():
        return text
    # Falla rápido y accionable: un http:// no-loopback no sale a la red.
    endpoint = _chat_completion_endpoint(check_url(url))
    system = system or DEFAULT_SYSTEM
    if context_terms:
        system += "\nGlosario de grafias preferidas (referencia, no contenido):\n" + context_terms
    body = {
        "model": resolve_model(cfg),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": text}],
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
        # A 100-word Spanish dictation can need well over 128 output tokens.
        # Scale with input length so a useful correction is not cut off midway.
        "max_tokens": min(512, max(64, len(text.split()) * 4 + 32)),
    }
    # Optional sampling overrides are used by the local quality benchmark.
    # Production calls omit them and keep the deterministic temperature=0 path.
    if sampling_params:
        for name in ("temperature", "top_p", "top_k", "presence_penalty"):
            if name in sampling_params:
                body[name] = sampling_params[name]
    headers = {"Content-Type": "application/json"}
    bearer = token if token is not None else resolve_token(cfg)
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(
        endpoint, data=json.dumps(body).encode("utf-8"),
        headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            log.info("LLM response truncated; keep local transcript.")
            return text
        out = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            "respuesta LLM inesperada: falta choices[0].message.content") from None
    out = (out or "").strip().strip("\"'“”‘’")
    return out or text


def candidate_preserves_meaning(original, candidate, url, timeout=2.0,
                                token=None, cfg=None, system=VERIFY_SYSTEM):
    """Ask the configured LLM to gate lexical changes conservatively.

    Punctuation- and capitalization-only edits do not need a second request.
    Accent changes are checked because they can change meaning in Spanish. If
    surface words change, only an explicit ACCEPT lets the candidate pass; a
    rejection, malformed response, or caller-side error falls back to the local
    transcript. The request contains text only, never audio.
    """
    if not original or not candidate:
        return bool(original == candidate)
    if _surface_words(original) == _surface_words(candidate):
        return True
    if not preserves_facts(original, candidate):
        return False

    import json
    import urllib.request

    endpoint = _chat_completion_endpoint(check_url(url))
    body = {
        "model": resolve_model(cfg),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                "TRANSCRIPCIÓN ORIGINAL:\n" + original
                + "\n\nPROPUESTA:\n" + candidate)},
        ],
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": 8,
    }
    headers = {"Content-Type": "application/json"}
    bearer = token if token is not None else resolve_token(cfg)
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(
        endpoint, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return False
    if not isinstance(answer, str):
        return False
    decision = re.sub(r"[\s.!:]+$", "", answer).upper()
    return decision == "ACCEPT"


def polish(text, url, timeout=8.0, system=None, context_terms="", token=None,
           cfg=None):
    """Polish text while preserving the normalized sequence of every word."""
    out = polish_candidate(text, url, timeout=timeout, system=system,
                           context_terms=context_terms, token=token, cfg=cfg)
    if out and _words(out) != _words(text):
        log.warning("LLM cambió palabras; conservo la transcripción local.")
        return text
    return out or text


def resolve_url(cfg=None, url=None):
    if url is not None:
        return url
    cfg = cfg or {}
    return cfg.get("llm_url", "") or os.environ.get("INSTANT2_LLM_URL", "")


# Palabras-Q que abren pregunta en español. Conservador a proposito: sin
# prosodia no se distingue "¡qué bueno!" de "¿qué hora es?", asi que solo
# se restaura `¿` (nunca `¡`) y solo si la oracion ya cierra con `?`.
_QUESTION_OPENERS = frozenset(
    "qué que cómo como cuándo cuando dónde donde cuál cual cuáles cuales "
    "cuánto cuanto cuánta cuanta cuánto cuanto quién quien quiénes quienes "
    "adónde adonde porqué porque".split())
_POR_QUE_RE = re.compile(r"por\s+que\s+", flags=re.UNICODE)


def restore_openers(text):
    """Agrega `¿` faltante en preguntas obvias. Determinista, sin red.

    Si una oracion termina en `?` sin abrir con `¿` y empieza con palabra-Q
    (o "por que/porque/por-que" inicial como compuesto), antepone `¿`.
    "Por" suelto NO abre ("por favor, pasame eso?" queda intacto).
    No toca `¡`: sin prosodia una exclamacion es indistinguible y el
    falso positivo hace mas daño. Nunca reescribe palabras.
    """
    if not text or "?" not in text:
        return text
    parts = re.split(r"(?<=[.?!])(\s+)", text)
    out = []
    for part in parts:
        stripped = part.lstrip()
        if stripped.endswith("?") and not stripped.startswith("¿"):
            first = re.match(r"([^\W\d_]+)", stripped, flags=re.UNICODE)
            word = (first.group(1).casefold() if first else "")
            low = stripped.casefold()
            is_q = (word in _QUESTION_OPENERS
                    or low.startswith(("porque ", "por-que "))
                    or _POR_QUE_RE.match(low) is not None)
            if is_q:
                indent = part[:len(part) - len(stripped)]
                part = indent + "¿" + stripped
        out.append(part)
    return "".join(out)


def maybe_polish(text, cfg=None, url=None, conf=1.0, min_conf=0.85,
                 timeout=None):
    """Apply local fixes, then use the configured LLM result for normal dictation.

    The LLM remains opt-out and is never required for dictation. Accepting its
    result lets it repair clear ASR word errors; failures fall back to the
    locally corrected transcript. ``conf``/``min_conf`` stay in the signature
    for callers and compatibility.
    """
    from instant2_app import context

    corrected = correct_locally(text, cfg)
    target = resolve_url(cfg, url)
    if not target or cfg.get("llm_normal_enabled", True) is False:
        return corrected
    try:
        from instant2_app import bias as _bias

        _terms = context.correction_context(cfg)
        _gen = _bias.terms_for_correction()
        terms = (_terms + "\n" + _gen) if _terms else _gen
        request_timeout = 8.0 if timeout is None else max(0.1, float(timeout))
        deadline = time.monotonic() + request_timeout
        candidate = polish_candidate(
            corrected, target, context_terms=terms, cfg=cfg,
            timeout=request_timeout)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return corrected
        if not candidate_preserves_meaning(
                corrected, candidate, target,
                timeout=min(2.0, remaining), cfg=cfg):
            log.info("LLM candidate rejected by meaning check; keep local text.")
            return corrected
        return candidate
    except Exception as exc:
        # Clase del error, nunca la URL ni el cuerpo: la URL puede llevar
        # token en query y el texto dictado no va al log.
        log.warning("LLM off/fail (%s): sigo con texto corregido localmente.",
                    type(exc).__name__)
        return corrected


def correct_locally(text, cfg=None):
    """Aplica únicamente las correcciones locales compartidas por Instant2."""
    from instant2_app import context

    return restore_openers(context.correct_aliases(text, cfg))


def _words(text):
    normalized = unicodedata.normalize("NFD", text.casefold())
    return re.findall(
        r"\w+", "".join(char for char in normalized
                        if unicodedata.category(char) != "Mn"))


def _surface_words(text):
    """Word tokens that retain accents so meaning-sensitive edits are gated."""
    return re.findall(r"\w+", unicodedata.normalize("NFC", text).casefold())


_RESTRICTIONS = frozenset({"no", "ni", "nunca", "jamas", "sin", "solo",
                            "solamente", "unicamente", "excepto", "salvo"})
_GRAMMAR_INSERTIONS = frozenset({"a", "al", "de", "del", "el", "la", "los",
                                  "las", "un", "una", "unos", "unas", "en"})
_NUMBER_WORDS = frozenset({
    "un", "uno", "una", "dos", "tres", "cuatro", "cinco", "seis",
    "siete", "ocho", "nueve", "cero", "diez", "once", "doce", "trece",
    "catorce", "quince", "dieciseis", "diecisiete", "dieciocho",
    "diecinueve", "veinte", "veintiuno", "veintiun", "veintiuna",
    "veintidos", "veintitres", "veinticuatro", "veinticinco", "veintiseis",
    "veintisiete", "veintiocho", "veintinueve", "treinta", "cuarenta",
    "cincuenta", "sesenta", "setenta", "ochenta", "noventa", "cien",
    "ciento", "doscientos", "doscientas", "trescientos", "trescientas",
    "cuatrocientos", "cuatrocientas", "quinientos", "quinientas",
    "seiscientos", "seiscientas", "setecientos", "setecientas", "ochocientos",
    "ochocientas", "novecientos", "novecientas", "mil", "millon", "millones",
    "billon", "billones",
})
_NUMBER_WORD_CONNECTORS = frozenset({"y", "coma", "punto"})
_NUMBER_WORD_PREFIXES = frozenset({"menos", "mas"})
_NUMBER_UNITS = frozenset({
    "centavo", "centavos", "dolar", "dolares", "euro", "euros",
    "peso", "pesos", "real", "reales", "segundo", "segundos", "minuto",
    "minutos", "hora", "horas", "dia", "dias", "semana", "semanas",
    "mes", "meses", "ano", "anos", "milisegundo", "milisegundos",
    "metro", "metros", "centimetro", "centimetros", "milimetro",
    "milimetros", "kilometro", "kilometros", "milla", "millas", "gramo",
    "gramos", "kilogramo", "kilogramos", "kilo", "kilos", "tonelada",
    "toneladas", "litro", "litros", "mililitro", "mililitros", "byte",
    "bytes", "kilobyte", "kilobytes", "megabyte", "megabytes", "gigabyte",
    "gigabytes", "terabyte", "terabytes", "giga", "gigas",
})


def _number_word_phrases(text):
    """Number expressions in words, retained conservatively and in order.

    Inferring the value of a free-form Spanish numeral is ambiguous (for
    example, ``un`` can also be an article). Comparing its normalized words
    protects the spoken quantity and common measurement unit without asking a
    model to interpret it.
    """
    words = _words(text)
    phrases = []
    current = []
    for index, word in enumerate(words):
        previous_number = bool(current) and current[-1] in _NUMBER_WORDS
        next_number = (index + 1 < len(words)
                       and words[index + 1] in _NUMBER_WORDS)
        if word in _NUMBER_WORDS:
            current.append(word)
        elif (word in _NUMBER_WORD_PREFIXES and not current and next_number):
            current.append(word)
        elif word in _NUMBER_WORD_CONNECTORS and previous_number and next_number:
            current.append(word)
        else:
            if current:
                unit = (words[index] if index < len(words)
                        and words[index] in _NUMBER_UNITS else "")
                phrases.append((tuple(current), unit))
                current = []
    if current:
        phrases.append((tuple(current), ""))
    return phrases


def preserves_facts(original, candidate):
    """Reject provable data changes before asking the probabilistic verifier.

    The verifier can still judge lexical repairs, repetitions and grammar, but
    cannot authorize changing a literal number/restriction or inventing content.
    """
    original_words, candidate_words = _words(original), _words(candidate)
    if Counter(word for word in original_words if word in _RESTRICTIONS) != Counter(
            word for word in candidate_words if word in _RESTRICTIONS):
        return False
    if re.findall(r"\d+(?:[.,]\d+)*", original) != re.findall(r"\d+(?:[.,]\d+)*", candidate):
        return False
    if _number_word_phrases(original) != _number_word_phrases(candidate):
        return False
    changes = SequenceMatcher(None, original_words, candidate_words, autojunk=False)
    for operation, _start, _end, begin, end in changes.get_opcodes():
        if operation == "insert" and any(word not in _GRAMMAR_INSERTIONS
                                          for word in candidate_words[begin:end]):
            return False
    return True
