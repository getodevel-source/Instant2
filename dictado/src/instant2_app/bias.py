"""Diccionario tecnico general + spotter fonetico con gate de confianza.

El perfil de vocabulario (`context.py`) es por usuario: corrige SUS marcas.
Este modulo es general: corrige los terminos tecnicos ingleses que Parakeet
deforma sistematicamente para TODO hispanohablante dev (commit->comic,
build->wey/bill, deploy->depliegue...). Misma vara para todos, sin entrenar
niGPU: es la version portable de la idea de TurboBias/CTC-WS (phrase-boosting
con evidencia acustica), aplicada en texto con gate de confianza.

Solo actua cuando el take es dudoso (`conf < threshold`, default 0.85): en
texto seguro no toca nada (un falso positivo hace mas dano que el error).
Las sustituciones exigen match fonetico (`_sound_close`), nunca ocurrencia
de substring: "para" no se vuelve un termino.
"""
import logging
import re

from instant2_app import context

log = logging.getLogger("instant2")

# termino -> variantes que Parakeet escribe (curado de docs/precision.md +
# medicion con voz real + bench FLEURS; se amplia con evidencia, no a ojo).
# OJO: solo deformaciones que NO son palabra espanola valida ("quemet",
# "kemite"). Las que si lo son ("comic", "comer") van SOLO al nivel 2 con
# vecina: sin contexto "lei un comic" y "voy a comer" quedan intactos.
GENERAL_TERMS = {
    "commit": ["quemet", "kemite", "camer", "quemita"],
    "build": ["bill", "bifallo", "bil", "bull"],
    "deploy": ["depliegue", "diploi"],
    "staging": ["esteyin", "esteyshin"],
    "rollback": ["rolbak", "rolback", "crawlback"],
    "workflow": ["work flow", "guorflou"],
    "frontend": ["front end", "fron ten"],
    "backend": ["back end", "bak end", "bakken", "baken"],
    "plugin": ["pluguin", "plagin", "pline", "ploin"],
    "benchmark": ["benchmar"],
    "Python": ["paiton", "piton"],
    "GitHub": ["hit hub", "jit hub", "github"],
    "Parakeet": ["parkit", "para kit", "parakit", "paracid", "paragit"],
    # "quemet" es SOLO de "commit": estuvo tambien en "Qwen" y ganaba
    # "commit" por orden de insercion; Qwen conserva quen/cuen/cuentres.
    "Qwen": ["quen", "cuen", "cuentres"],
    "OpenAI": ["o en pi", "open ai", "oupen ei ai"],
}

# Aliases que SON palabra espanola valida: solo corrigen con vecina
# ("comic"->commit junto a "workflow"; "wey"->build junto a "fallo";
# "instante"->Instant2 junto a "dicta/voz"; sin vecina quedan intactos).
COND_ALIASES = {
    "commit": ["comic", "comer"],
    "build": ["wey", "guey", "güey", "way", "wei", "wild", "wheel"],
    "Instant2": ["instante"],
    "staging": ["station"],
}

DEFAULT_THRESHOLD = 0.85
WORD_THRESHOLD = 0.80
# Ventana de contexto: ±8 palabras (la vecina tecnica suele estar en la
# misma oracion: "El Will fallo en la manana por el driver" tiene driver
# a 7 de distancia). El nivel 2 exige 2 puntos; la regla C 2 exactas.
WINDOW = 8
# El alias condicional ("comic"/"comer"/"wey") vale salvo como verbo:
# tras preposicion o "que" ("voy a comer", "hay que comer") no se toca.
_VERB_BEFORE = frozenset("a de para por al del que".split())
# escribio "comic" junto a "workflow", la vecina confirma "commit"; si lo
# escribio junto a "superheroes", era historieta y no se toca. Curado de
# co-ocurrencia dev real, conservador: pocas vecinas fuertes > muchas flojas.
NEIGHBOURS = {
    "commit": ["push", "pull", "repo", "branch", "staging", "deploy",
               "workflow", "merge", "checkout", "hash", "repositorio"],
    "build": ["fallo", "falla", "fallar", "error", "paso", "pasa",
              "frontend", "plugin", "compilar", "romper", "driver",
              "staging", "backend", "deploy"],
    "deploy": ["staging", "backend", "frontend", "produccion", "servidor",
               "rollback", "release", "desplegar"],
    "staging": ["deploy", "commit", "workflow", "produccion", "backend"],
    "rollback": ["deploy", "build", "staging", "version", "revertir"],
    "workflow": ["commit", "staging", "github", "actions", "pipeline",
                 "correr", "corri"],
    "frontend": ["plugin", "build", "deploy", "carga", "cargar", "backend"],
    "backend": ["plugin", "build", "deploy", "servidor", "api", "responde",
                "frontend"],
    "plugin": ["frontend", "backend", "instalar", "cargar", "carga", "fallo"],
    "driver": ["viejo", "instalar", "falla", "dispositivo", "version"],
    "benchmark": ["workflow", "plugin", "medir", "marca", "correr", "marcar"],
    "Python": ["codigo", "script", "ejecutar", "libreria", "programa"],
    "GitHub": ["commit", "push", "repo", "pull", "workflow", "clonar",
               "subir", "correr"],
    "Parakeet": ["dicta", "dictado", "dictar", "voz", "transcribir",
                 "modelo", "instant2"],
    "Instant2": ["dicta", "dictado", "dictar", "voz", "parakeet", "modelo"],
    "Qwen": ["modelo", "nombres", "api", "openai", "motor", "llm"],
    "OpenAI": ["modelo", "nombres", "api", "qwen", "motor", "llm"],
}

# Casos medidos en voz real (≥2 tomas) donde el modelo se equivoca SEGURO:
# el gate de confianza no se abre y hay que corregir por contexto puro.
# En dictado en español, "will/wild/way" como palabra inglesa no existe:
# siempre es "build" deformado. Exige ≥2 puntos de vecinas (exacta=2,
# fonetica ≤1 = 1): "El Will fall por el driver" (fall~fallo + driver)
# -> build; "Will Walchar no molesta" (0) queda intacto.
STRONG_SPOT = {
    "will": ("build", ["fallo", "falla", "fallar", "error", "driver",
                        "frontend", "plugin", "staging", "backend"]),
    "wild": ("build", ["fallo", "falla", "fallar", "error", "driver",
                        "frontend", "plugin", "staging", "backend"]),
}


def _as_profile():
    # Terminos con aliases condicionales (palabra ES valida) van SIN sonido
    # en el nivel 1: "comic"/"comer"/"wey" solo se rescatan en el nivel 2
    # con vecina. El resto mantiene sonido (deformaciones no-palabra).
    return [{"term": term, "aliases": aliases,
             "sonido": term not in COND_ALIASES}
            for term, aliases in GENERAL_TERMS.items()]


def _words_with_keys(text):
    words = re.findall(r"[^\W\d_]+", text or "", flags=re.UNICODE)
    return words, [context._sound_key(w) for w in words]


def _sent_ids(toks, widx):
    """Id de oracion por palabra: sube en cada .?! entre palabras.

    Las vecinas no cruzan frontera de oracion: "lei un comic. Hace push"
    no rescata (push esta en otra oracion), "comic del workflow" si.
    """
    ids = []
    sent = 0
    prev = 0
    for ti in widx:
        for t in toks[prev:ti]:
            if t and re.search(r"[.!?]", t):
                sent += 1
                break
        ids.append(sent)
        prev = ti + 1
    return ids


def _neighbour_points(keys, idx, neighbours, sent=None):
    """Puntos de evidencia contextual en ±WINDOW sin cruzar oracion.

    Match exacto de vecina = 2 puntos (fuerte); fonetico ≤1 = 1 punto
    (debil, la vecina tambien sale deformada). Se exigen ≥2 puntos:
    una exacta o dos debiles. Las protegidas no cuentan. `sent` (de
    `_sent_ids`): la vecina con otra oracion no suma.
    """
    want = {context._sound_key(n) for n in neighbours}
    lo, hi = max(0, idx - WINDOW), min(len(keys), idx + WINDOW + 1)
    points = 0
    for j in range(lo, hi):
        if j == idx or not keys[j] or keys[j] in context._STOPLIST_KEYS:
            continue
        if sent is not None and sent[j] != sent[idx]:
            continue
        if keys[j] in want:
            return 2
        for w in want:
            if context._edit_distance(keys[j], w) <= 1:
                points += 1
                if points >= 2:
                    return 2
                break
    return points


def _has_neighbour(keys, idx, neighbours, sent=None):
    """Compat: ¿evidencia suficiente (≥2 puntos, misma oracion)?"""
    return _neighbour_points(keys, idx, neighbours, sent) >= 2


def _strong_spot(text):
    """Regla C: casos medidos en voz real con 2+ vecinas EXACTAS.

    Sin gate de confianza: la evidencia es el contexto puro, no la duda.
    "El Will fallo por el driver" (fallo+driver exactas)->build.
    "Will Walchar no molesta" (0 vecinas) queda intacto.
    """
    toks = context._tokens(text)
    widx = [i for i, t in enumerate(toks)
            if re.fullmatch(r"[^\W\d_]+", t, flags=re.UNICODE)]
    words = [toks[i] for i in widx]
    if not words:
        return text
    sent = _sent_ids(toks, widx)
    for pos, word in enumerate(words):
        strong = STRONG_SPOT.get(word.casefold())
        if strong is None:
            continue
        term, want = strong
        want_keys = {context._sound_key(n) for n in want}
        lo, hi = max(0, pos - WINDOW), min(len(words), pos + WINDOW + 1)
        points = 0
        for j in range(lo, hi):
            if j == pos or sent[j] != sent[pos]:
                continue
            wj = words[j].casefold()
            if wj in want:
                points += 2
            else:
                kj = context._sound_key(words[j])
                if kj and kj not in context._STOPLIST_KEYS and any(
                        context._edit_distance(kj, w) <= 1 for w in want_keys):
                    points += 1
            if points >= 2:
                break
        if points >= 2:
            toks[widx[pos]] = term
            log.debug("bias strong rescato %d puntos.", points)
    return "".join(toks)


def _weak_spot(text, allow=None):
    """Rescates debiles confirmados por contexto: match fonetico ≤2 ediciones
    con una palabra del texto hacia un termino, + vecina en la ventana.

    Cubre lo que el nivel fuerte no ve: deformaciones de 2 letras
    ("quemita"->"commit") o palabras corrientes que suenan al termino.
    Sin vecina no toca: "lei un comic de superheroes" queda intacto.
    `allow`: set de indices de palabra habilitados (gate por palabra);
    None = todas (take dudoso). Devuelve el texto con rescates aplicados.
    """
    toks = context._tokens(text)
    # Indices de palabras en `toks` + sus claves (alineados con el texto).
    widx = [i for i, t in enumerate(toks)
            if re.fullmatch(r"[^\W\d_]+", t, flags=re.UNICODE)]
    words = [toks[i] for i in widx]
    keys = [context._sound_key(w) for w in words]
    if not words:
        return text
    sent = _sent_ids(toks, widx)
    for pos, (word, key) in enumerate(zip(words, keys)):
        if allow is not None and pos not in allow:
            continue
        # `_tokens` keeps a digit suffix as a separate token, so the brand
        # Instant2 arrives here as `Instant`, `2`. Do not phonetic-rescue its
        # alphabetic half to the canonical term and duplicate the suffix.
        token_index = widx[pos]
        if (word.casefold() == "instant" and token_index + 1 < len(toks)
                and toks[token_index + 1] == "2"):
            continue
        if not key or key in context._STOPLIST_KEYS:
            continue
        for term, neighbours in NEIGHBOURS.items():
            cond = [a.casefold() for a in COND_ALIASES.get(term, ())]
            if word.casefold() in cond:
                # sustantivo ("un comic", "el wey"); tras preposicion es
                # verbo ("voy a comer") y se salta. Sin filtro de
                # distancia: el alias es exacto, la vecina decide.
                prev = words[pos - 1].casefold() if pos > 0 else ""
                if prev in _VERB_BEFORE:
                    continue
            else:
                tkey = context._sound_key(term)
                if not tkey:
                    continue
                dist = context._edit_distance(key, tkey)
                # Nivel 2 endurecido: solo dist ≤1. Dist 2 se elimino
                # (falsos positivos en palabras corrientes); los casos
                # reales de dist 2 estan como alias listados (nivel 1).
                if dist > 1:
                    continue
                # La grafia preferida ya presente no se reescribe.
                if key == tkey and word.casefold() == term.casefold():
                    continue
            if _has_neighbour(keys, pos, neighbours, sent):
                toks[widx[pos]] = term
                log.debug("bias contexto rescato len=%d.", len(word))
                keys[pos] = context._sound_key(term)
                break
    return "".join(toks)


def _align_allow(text, word_confs, word_threshold=WORD_THRESHOLD):
    """Indices de palabra del texto con confianza bajo el umbral.

    Alinea por posicion las palabras del decode (`word_confs`, en orden)
    con las del texto final: coinciden salvo el dedup de overlap, que
    recorta como mucho la zona comun (el orden se preserva). Si los
    conteos difieren en mas de 2, no hay alineacion fiable: devuelve None
    (el gate por palabra no aplica, solo el del take).
    """
    words = re.findall(r"[^\W\d_]+", text or "", flags=re.UNICODE)
    if not word_confs or abs(len(words) - len(word_confs)) > 2:
        return None
    allow = set()
    for i, wc in enumerate(word_confs[:len(words)]):
        try:
            c = float(wc[1])
        except (TypeError, ValueError, IndexError):
            continue
        if c < word_threshold:
            allow.add(i)
    return allow


def _EN_COMMON():
    return frozenset(
        "the of to and in is it you that he was for on are as with his they "
        "i at be this have from or one had by word but not what all were we "
        "when your can said there use an each which she do how their if will "
        "up other about out many then them these so some her would make like "
        "him into time has look two more write go see number no way could "
        "people my than first water been call who oil its now find long down "
        "day did get come made may part".split())


def _is_english_take(text, ratio=0.40):
    """¿El take es dictado en ingles, no español con tecnicos?

    Si ≥40 % de las palabras son inglesas comunes (the/of/to/...), no se
    toca nada: el usuario dicta en ingles a proposito y el bias ES
    romperia ("will" real -> "build"). Los tecnicos sueltos en español
    nunca llegan al 40 %.
    """
    words = re.findall(r"[^\W\d_]+", text or "", flags=re.UNICODE)
    words = [w.casefold() for w in words if len(w) > 1]
    if len(words) < 4:
        return False
    common = _EN_COMMON()
    hits = sum(1 for w in words if w in common)
    return (hits / len(words)) >= ratio


def correct_biased(text, conf=1.0, threshold=DEFAULT_THRESHOLD,
                   word_confs=None, word_threshold=WORD_THRESHOLD):
    """Corrige terminos tecnicos generales con triple via.

    Regla C (siempre): casos medidos en voz real con 2+ vecinas
    ("Will"+"fallo"+"driver"->build). Sin gate de confianza.
    Gate del take (`conf < threshold`): niveles 1+2 en todo el texto.
    Gate por palabra (`word_confs`): palabras dudadas + vecina, take seguro.
    Dictado en ingles (≥40 % palabras comunes): intacto siempre.
    Sin ninguna evidencia devuelve el texto intacto.
    """
    if not text or not text.strip():
        return text
    if _is_english_take(text):
        return text
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        # Como llm.maybe_polish: conf invalida = take seguro (1.0).
        # La Regla C corre siempre igual (ver docstring).
        conf = 1.0
    out = _strong_spot(text)
    if conf < threshold:
        cfg = {"active_context": "General",
               "context_profiles": {"General": _as_profile()}}
        out = context.correct_aliases(out, cfg)
        out = _weak_spot(out)
    else:
        allow = _align_allow(out, word_confs, word_threshold)
        if allow:
            out = _weak_spot(out, allow=allow)
    if out != text:
        log.info("bias general rescato %d caracteres (conf=%.2f).",
                 len(out), conf)
    return out


def terms_for_correction():
    """Referencia compacta para el pulido LLM (grafias preferidas)."""
    return "Glosario tecnico (referencia, no contenido): " + ", ".join(
        sorted(GENERAL_TERMS))
