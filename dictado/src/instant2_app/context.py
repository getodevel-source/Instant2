"""User-managed local vocabulary profiles for transcription correction."""
import logging
import re
import unicodedata

log = logging.getLogger("instant2")

DEFAULT_PROFILE = "General"
DEFAULT_PROFILES = {DEFAULT_PROFILE: []}

# Marcador de linea en el editor: `~` delante de la grafia activa el
# emparejamiento por sonido para ese termino.
SOUND_PREFIX = "~"

# Palabras corrientes del español cuyas claves de sonido nunca se sustituyen.
# Sin esto, activar el sonido en un termino podria reescribir texto legitimo:
# «quien» suena igual que «quen» (variante real de «Qwen»).
_STOPLIST_WORDS = (
    "quien", "quienes", "que", "cual", "cuales", "cuando", "cuanto", "cuanta",
    "cuantos", "cuantas", "como", "donde", "porque", "para", "pero", "este",
    "esta", "esto", "estos", "estas", "ese", "esa", "eso", "esos", "esas",
    "aquel", "aquella", "uno", "una", "unos", "unas", "con", "sin", "por",
    "del", "las", "los", "mas", "muy", "tan", "tanto", "todo", "toda",
    "todos", "todas", "nada", "algo", "alguien", "nadie", "siempre", "nunca",
    "tambien", "solo", "sola", "solo", "aun", "ya", "aca", "ahi", "alli",
    "voy", "vas", "va", "van", "vamos", "ser", "son", "esta", "estan",
    "tiene", "tienen", "hacer", "hace", "hacen", "puede", "pueden", "quiero",
    "quiere", "quieres", "decir", "dice", "dicen", "ver", "veo", "ves",
    "cosa", "cosas", "vez", "veces", "parte", "partes", "punto", "puntos",
    "caso", "casos", "modo", "forma", "formas", "tipo", "tipos", "lugar",
    "hora", "horas", "dia", "dias", "ano", "anos", "mes", "meses", "manera",
    "tema", "temas", "nivel", "niveles", "linea", "lineas", "cambio",
    "cambios", "cuenta", "cuentas", "cuento", "cuentos", "cuenta",
    # Palabras cortas que suenan parecido a marcas y se sustituirian por error
    # («buen» por «Qwen», «ven» por «Qwen», «cuan» por «Qwen»).
    "buen", "bien", "ven", "van", "vino", "cuan", "quien", "quienes",
    "pan", "pen", "pie", "pia", "gen", "gen", "fin", "fino", "son", "sin",
    "con", "tan", "ten", "ton", "don", "dan", "den", "din", "man", "mes",
    "mal", "mar", "par", "por", "ser", "ver", "dar", "ir", "ya", "mas",
    # Palabras inglesas frecuentes en frases tecnicas: no deben disparar una
    # sustitucion por parecido.
    "might", "must", "should", "would", "could", "will", "have", "with",
    "from", "this", "that", "they", "there", "then", "than", "them", "were",
    "what", "when", "which", "while", "about", "into", "over", "under",
)


def _sound_key(word):
    """Clave de sonido aproximada al español (como suena, no como se escribe).

    No es una transliteracion fonetica formal: agrupa los sonidos que el
    reconocedor confunde al dictar marcas y terminos en inglés dentro de una
    frase en español («Qwen» -> «cuen», «GitHub» -> «hit hub»).
    """
    text = unicodedata.normalize("NFD", (word or "").casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"[^a-z]", "", text)
    if not text:
        return ""
    text = re.sub(r"ch", "c", text)
    text = re.sub(r"ll", "y", text)
    text = text.replace("qu", "k").replace("q", "k")
    text = text.replace("gu", "g").replace("g", "j")
    text = text.replace("c", "k").replace("z", "s").replace("x", "ks")
    text = text.replace("v", "b").replace("w", "u").replace("h", "")
    text = text.replace("y", "i")
    text = re.sub(r"(.)\1+", r"\1", text)
    return text


def _sound_tokens(text):
    """(claves de sonido, palabras originales) de un texto."""
    words = re.findall(r"[^\W\d_]+", text or "", flags=re.UNICODE)
    return [_sound_key(word) for word in words], words


_STOPLIST_KEYS = frozenset(_sound_key(word) for word in _STOPLIST_WORDS)


def _edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, start=1):
        current = [i]
        for j, b in enumerate(right, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (0 if a == b else 1)))
        previous = current
    return previous[-1]


def _sound_close(keys, target):
    """¿Las claves `keys` suenan como `target`, tolerando un error?

    El reconocedor no solo sustituye sonidos: a veces pierde una letra
    («Qwen» -> «ken», «GitHub» -> «itub») o parte una palabra en dos
    («OpenAI» -> «O en Pi»). La comparacion directa tolera una edicion; cuando
    las palabras se fusionan para formar el termino se toleran dos, porque la
    fusion ya es de por si una coincidencia fuerte.
    """
    if not keys or not target:
        return False
    if len(keys) == len(target):
        return sum(_edit_distance(k, t) for k, t in zip(keys, target)) <= 1
    if len(keys) == len(target) + 1:
        # Una palabra de mas: se prueba salteando cada posicion.
        for skip in range(len(keys)):
            rest = keys[:skip] + keys[skip + 1:]
            if sum(_edit_distance(k, t) for k, t in zip(rest, target)) <= 1:
                return True
    return False


def _merged_candidates(words, max_parts=3):
    """Claves de sonido de fusionar palabras contiguas (hasta `max_parts`).

    Cubre el caso en que el motor parte una palabra: «O en Pi» -> «openai»,
    «Local Might» -> «lokalmind». Solo se fusionan palabras que no estan en la
    lista de proteccion; la comparacion exige despues dos ediciones o menos.
    """
    keys = [_sound_key(word) for word in words]
    if any(not key or key in _STOPLIST_KEYS for key in keys):
        return []
    candidates = []
    for size in range(2, min(max_parts, len(keys)) + 1):
        for start in range(0, len(keys) - size + 1):
            chunk = keys[start:start + size]
            candidates.append((_sound_key("".join(chunk)), words[start:start + size]))
    return candidates


def profiles(config):
    """Return a sanitized copy of configured context profiles."""
    raw = (config or {}).get("context_profiles", DEFAULT_PROFILES)
    result = {}
    if isinstance(raw, dict):
        for name, items in raw.items():
            name = str(name).strip()
            if not name or not isinstance(items, list):
                continue
            cleaned = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                term = str(item.get("term", "")).strip()
                aliases = item.get("aliases", [])
                if not term or not isinstance(aliases, list):
                    continue
                aliases = list(dict.fromkeys(
                    alias for alias in (str(value).strip() for value in aliases)
                    if alias and alias.casefold() != term.casefold()))
                cleaned.append({"term": term, "aliases": aliases,
                                "sonido": bool(item.get("sonido"))})
            result[name] = cleaned
    if not result:
        result = {DEFAULT_PROFILE: []}
    return result


def active_name(config):
    available = profiles(config)
    name = str((config or {}).get("active_context", DEFAULT_PROFILE)).strip()
    return name if name in available else DEFAULT_PROFILE if DEFAULT_PROFILE in available else next(iter(available))


def active_terms(config):
    available = profiles(config)
    return available[active_name(config)]


def parse_editor(text):
    """Parse `preferred spelling<TAB>heard variant|another variant` rows.

    Un `~` delante de la grafia activa el emparejamiento por sonido para ese
    termino, para no tener que listar cada variante que el motor inventa.
    """
    terms = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        term, separator, variants = line.partition("\t")
        term = term.strip()
        sound = term.startswith(SOUND_PREFIX)
        if sound:
            term = term[len(SOUND_PREFIX):].strip()
        aliases = [value.strip() for value in variants.split("|") if value.strip()] if separator else []
        if term:
            terms.append({"term": term, "aliases": list(dict.fromkeys(aliases)),
                          "sonido": sound})
    return terms


def editor_text(items):
    """Format profile rows for the settings editor."""
    return "\n".join(
        (SOUND_PREFIX if item.get("sonido") else "") + item["term"]
        + ("\t" + " | ".join(item["aliases"]) if item["aliases"] else "")
        for item in items)


def _tokens(text):
    """Alterna palabras y separadores, para poder reemplazar sin perder formato."""
    return re.findall(r"[^\W\d_]+|[^\w]+|_+|\d+", text or "", flags=re.UNICODE)


def correct_aliases(text, config):
    """Replace listed recognition variants, and by sound when asked for.

    Las variantes listadas se sustituyen siempre, palabra por palabra. El
    emparejamiento por sonido solo actua en los terminos marcados con `sonido`,
    tolera un error de sonido o una palabra de diferencia, y nunca toca las
    palabras corrientes del español de la lista de proteccion.

    Se recorre el texto palabra por palabra probando la ventana mas larga
    primero: el reconocedor parte palabras («O en Pi» por «OpenAI») y tambien
    las pega, y un patron que consume palabras se saltea esas ventanas.
    """
    if not text:
        return text

    exact = {}
    sound = []
    protected = set()
    for item in active_terms(config):
        term = item["term"]
        for alias in item["aliases"]:
            exact.setdefault(alias.casefold(), term)
        if item.get("sonido"):
            keys = tuple(_sound_key(word) for word in term.split())
            if keys and all(keys):
                sound.append((keys, term))
                # Nunca sustituir la propia grafia preferida.
                protected.add(keys)

    if not exact and not sound:
        return text

    def sound_term(parts):
        """Grafia preferida para una secuencia de palabras, o None.

        Solo se acepta 1 palabra -> 1 termino. Las variantes de varias palabras
        (nombres con espacio, expresiones partidas) se declaran como alias
        exactos, que es mas predecible y no arrastra palabras vecinas.

        Se exige que la palabra tenga cuatro letras o mas: las palabras cortas
        del español son casi todas corrientes («buen», «ven», «pan») y
        sustituirlas hace mas daño que el error que corrigen.
        """
        if len(parts) != 1:
            return None
        if len(parts[0]) < 4:
            return None
        key = _sound_key(parts[0])
        if not key or (key,) in protected or key in _STOPLIST_KEYS:
            return None
        for target_keys, term in sound:
            if _sound_close((key,), target_keys):
                return term
        return None

    def sound_term_for_split(words):
        """Grafia preferida cuando el motor partio una palabra en varias.

        «O en Pi» por «OpenAI»: se fusionan las palabras y se compara contra
        los terminos de una sola palabra, con dos ediciones de tolerancia.

        Es la regla mas riesgosa del modulo, asi que se protege por tres lados:
        ninguna palabra puede ser corriente del español («para que» no debe
        volverse «Parakeet»), el termino tiene que tener cuerpo suficiente
        (cinco letras o mas) y la fusion tiene que conservar la mitad del
        sonido del termino.
        """
        if len(words) < 2:
            return None
        # Una letra suelta en el MEDIO de la fusion es una conjuncion o un
        # articulo («Qwen y GitHub»), y fusionarla se comeria texto legitimo.
        # En los bordes si puede ser parte de la palabra partida: la «O» de
        # «O en Pi» es el principio de «OpenAI».
        if any(len(word) < 2 for word in words[1:-1]):
            return None
        keys = [_sound_key(word) for word in words]
        if any(not key or key in _STOPLIST_KEYS for key in keys):
            return None
        merged = _sound_key("".join(words))
        if not merged or (merged,) in protected:
            return None
        for target_keys, term in sound:
            if len(target_keys) != 1:
                continue
            target = target_keys[0]
            if len(target) < 5:
                continue
            # Estructura: la primera palabra suena como el principio del
            # termino y el resto como el final. Asi «O en Pi» cubre «openai»
            # (o + enpi, que dista dos ediciones de «enai») y «Qwen y» se
            # rechaza, porque una letra suelta no llega al minimo de la cola.
            head, tail = keys[0], _sound_key("".join(words[1:]))
            if len(words) > 1:
                if len(tail) < 3:
                    continue
                if not target.startswith(head):
                    continue
                if _edit_distance(tail, target[-len(tail):]) > 2:
                    continue
            elif not target.startswith(head):
                continue
            distance = _edit_distance(merged, target)
            if distance <= 2 and distance * 2 <= len(target):
                return term
        return None

    tokens = _tokens(text)
    # Ventana maxima: una palabra del motor puede venir partida en varias
    # («O en Pi» por «OpenAI»), asi que se prueba hasta tres palabras juntas.
    max_window = 3 if sound else 1
    longest_alias = max((len(alias.split()) for alias in exact), default=1)
    max_window = max(max_window, longest_alias)

    def match_at(start):
        """(termino, tokens consumidos) para la mejor coincidencia, o None.

        Prioridad: alias exacto mas largo primero (es lo que el usuario
        declaro), y despues el emparejamiento por sonido.
        """
        words, positions, cursor = [], [], start
        while cursor < len(tokens) and len(words) < max_window:
            piece = tokens[cursor]
            if piece and piece[0].isalpha():
                words.append(piece)
                positions.append(cursor)
            cursor += 1
        if not words:
            return None
        # 1) Alias exactos, de mas palabras a menos (incluida una sola).
        for size in range(len(words), 0, -1):
            positions_used = positions[:size]
            gaps_ok = all(
                all(piece.isspace() for piece in
                    tokens[positions_used[k] + 1:positions_used[k + 1]])
                for k in range(len(positions_used) - 1))
            if not gaps_ok:
                continue
            phrase = " ".join(words[:size])
            term = exact.get(phrase.casefold())
            if term:
                return term, positions[size - 1] - start + 1
        if not sound:
            return None
        # 2) Palabras partidas: solo palabras contiguas, con nada mas que
        #    espacios entre medio. Si no, la fusion se comeria palabras ajenas
        #    («Qwen y GitHub» no debe perder la «y»). Se incluye el tamaño 1
        #    porque el motor tambien puede pegar palabras («GitHub»).
        for size in range(len(words), 0, -1):
            positions_used = positions[:size]
            if any(len(word) < 2 for word in words[1:size - 1]):
                continue
            gaps_ok = all(
                all(piece.isspace() for piece in
                    tokens[positions_used[k] + 1:positions_used[k + 1]])
                for k in range(len(positions_used) - 1))
            if not gaps_ok:
                continue
            term = sound_term_for_split(words[:size])
            if term:
                return term, positions_used[-1] - start + 1
        # 3) Una palabra por sonido.
        term = sound_term([words[0]])
        return (term, positions[0] - start + 1) if term else None

    out = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token or not token[0].isalpha():
            out.append(token)
            index += 1
            continue
        found = match_at(index)
        if found:
            term, consumed = found
            out.append(term)
            index += consumed
        else:
            out.append(token)
            index += 1
    result = "".join(out)
    if result != text:
        log.debug("vocab: se aplicaron correcciones locales")
    return result


def correction_context(config):
    """Return a compact spelling reference for a local correction model."""
    rows = active_terms(config)
    if not rows:
        return ""
    return "\n".join(
        f"- {item['term']}" + (f" (si se reconoce como: {'; '.join(item['aliases'])})" if item["aliases"] else "")
        for item in rows)
