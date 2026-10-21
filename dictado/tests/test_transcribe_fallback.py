"""Transcribe: reintento de segmentos vacios con contexto ampliado."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: F401  (aisla el estado real del usuario)

from instant2_app.engine import Engine, SAMPLE_RATE, _fit_level


def _check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


class _FakeStream:
    def __init__(self, decide, counter):
        self._decide = decide
        self._counter = counter
        self._wav = None

    def accept_waveform(self, _sr, wav):
        self._wav = np.asarray(wav, dtype=np.float32)


class _FakeRec:
    def __init__(self, decide):
        self._decide = decide
        self.calls = 0

    def create_stream(self):
        self.calls += 1
        return _FakeStream(self._decide, self)

    def decode_stream(self, s):
        s.result = type("R", (), {"text": self._decide(s._wav)})


def _engine(segment, decide):
    eng = Engine(data_dir=tempfile.mkdtemp(), threads=2)
    eng.segment = lambda _wav: segment
    eng._rec = _FakeRec(decide)
    return eng


def _dur(wav):
    return len(wav) / SAMPLE_RATE


def _tone(seconds, amp=0.05, freq=220.0):
    """Senoide con contenido AC: sobrevive al frontend (DC-remove + HP),
    como la voz real. Las constantes puras son offset ADC, no voz."""
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    return (np.sin(2 * np.pi * freq * t) * amp).astype(np.float32)


# 1. Perdida total con audio fuerte: el reintento con contexto recupera.
wav = _tone(3.0)
eng = _engine([(0.0, 1.0)], lambda w: "" if _dur(w) < 1.5 else "hola mundo")
_check("fallback recupera vacio total", eng.transcribe(wav) == "hola mundo")
_check("fallback total usa 2 decodes", eng._rec.calls == 2)

# 2. Sin vacios no hay decodes extra.
eng = _engine([(0.0, 1.0)], lambda _w: "ok")
_check("sin vacios no reintenta", eng.transcribe(wav) == "ok" and eng._rec.calls == 1)

# 3. Chunk en silencio no se reintenta (el modelo devuelve vacio real).
silencio = np.zeros(2 * SAMPLE_RATE, dtype=np.float32)
eng = _engine([(0.0, 1.0)],
              lambda w: "" if float(np.max(np.abs(w))) < 0.005 else "nunca")
_check("silencio no reintenta", eng.transcribe(silencio) == "" and eng._rec.calls == 1)


# 4. Perdida parcial: solo el segmento vacio se reintenta y se empalma.
# Gap 1.2s (> max_gap) para que el merge no los una: el contexto del
# reintento (+-1s) igual alcanza la zona fuerte.
wav4 = np.concatenate([_tone(3.2, amp=0.05), _tone(0.5, amp=0.09),
                       _tone(1.3, amp=0.05)])


def _decide(w):
    if _dur(w) < 1.5:
        return ""
    return "segunda" if float(np.max(np.abs(w))) > 0.07 else "primera"


eng = _engine([(0.0, 2.0), (3.2, 3.7)], _decide)
_check("fallback parcial empalma", eng.transcribe(wav4) == "primera segunda")
# Con overlap de bordes el seg 2 ya trae contexto y decodifica directo:
# 2 decodes, sin reintentos (antes 3: el overlap mejoro el caso).
_check("overlap evita el reintento", eng._rec.calls == 2)

# 4b. Si aun con overlap el seg sale vacio, el retry +-1s lo rescata.
eng = _engine([(0.0, 2.0), (3.2, 3.7)],
              lambda w: "rescatada" if _dur(w) >= 2.6 else "")
_check("retry rescata vacio con overlap", eng.transcribe(wav4) == "rescatada")
_check("retry parcial usa 4 decodes", eng._rec.calls == 4)

# 5. _fit_level: solo boost, nunca recorta ni toca el silencio.
_out, g = _fit_level(np.zeros(1600, dtype=np.float32))
_check("nivel silencio intacto", g == 1.0)
_out, g = _fit_level(np.full(16000, 0.5, dtype=np.float32))
_check("nivel fuerte intacto", g == 1.0)
out, g = _fit_level(np.full(16000, 0.01, dtype=np.float32))
_check("nivel debil con boost", abs(g - 6.0) < 1e-6)
_check("nivel debil llega a nominal",
        abs(float(np.sqrt(np.mean(np.square(out, dtype=np.float64)))) - 0.06) < 1e-6)

# 6. Un take bajito que el modelo solo entiende nominal se rescata en el
# primer decode (sin reintentos).
bajo = _tone(2.0, amp=0.01)
eng = _engine([(0.0, 1.0)],
              lambda w: "se escucha" if float(
                  np.sqrt(np.mean(np.square(w, dtype=np.float64)))) >= 0.05 else "")
_check("take bajito se rescata", eng.transcribe(bajo) == "se escucha"
        and eng._rec.calls == 1)

# 7. Confianza: exp(media log-probs); sin scores no se castiga.
from instant2_app.engine import _mean_conf
_check("conf sin scores es 1", _mean_conf(None) == 1.0 and _mean_conf([]) == 1.0)
_check("conf tokens seguros alta",
        abs(_mean_conf([-0.1, -0.2, -0.05]) - 0.89) < 0.02)
_check("conf tokens dudosos baja", _mean_conf([-2.0, -3.0]) < 0.1)
_check("conf acotada", 0.0 <= _mean_conf([-1000.0]) <= 1.0)

# 8. blank_penalty y vad_model: defaults seguros, clamp 0..1.
_e = Engine(data_dir=tempfile.mkdtemp())
_check("blank default 0", _e.blank_penalty == 0.0 and _e.vad_model == "silero")
_e = Engine(data_dir=tempfile.mkdtemp(), blank_penalty=2.0, vad_model="TEN")
_check("blank clamp + vad norm", _e.blank_penalty == 1.0 and _e.vad_model == "ten")
_e = Engine(data_dir=tempfile.mkdtemp(), blank_penalty="mal")
_check("blank invalido a 0", _e.blank_penalty == 0.0)
_check("word_confs init vacio", _e.last_word_confs == [])

# 9. Gate por palabra: alinea tokens a palabras con el eslabon debil.
from instant2_app.engine import _word_confs
_wc = _word_confs(
    [" S", "ub", "í", " qu", "em", "ita"],
    [-0.01, -0.0, -0.0, -0.68, -0.0, -0.01],
    [0.0, 0.16, 0.32, 0.56, 0.72, 0.88])
_check("word_confs alinea",
        [w for w, _c, _a, _b in _wc] == ["Subí", "quemita"]
        and abs(_wc[1][1] - 0.51) < 0.02)
_check("word_confs sin scores vacio", _word_confs(None, None, None) == [])

# 10. B2: last_word_confs usa rs (arranque real con overlap), no s.
# Take multi-segmento: seg2 arranca en s=3.2 pero el chunk ve desde rs=2.7.
eng = _engine([(0.0, 2.0), (3.2, 3.7)], lambda _w: "x")
_t2 = ["hola", "adios"]
eng._one = lambda args: (args[0], _t2[args[0]], 1.0,
                         [(_t2[args[0]], 0.9, 0.05, 0.25)])
eng.transcribe(wav4)
_t = [t for _w, _c, t, _b in eng.last_word_confs]
_check("word_confs con offset rs",
        len(_t) == 2 and abs(_t[0] - 0.05) < 1e-9 and abs(_t[1] - 2.75) < 1e-9)

# 11. B3: el dedup recorta el prefijo duplicado; el gate desplaza, no trunca.
# "mundo cruel" sale en ambos decodes; join lo cose una vez y las confianzas
# conservan 4 palabras (no 5): se salta el duplicado del seg2, no la cola.
from instant2_app.engine import _overlap_count
_check("overlap_count dos palabras", _overlap_count("hola mundo cruel", "mundo cruel adios") == 2)
eng = _engine([(0.0, 2.0), (3.2, 3.7)], lambda _w: "x")
_texts = ["hola mundo cruel", "mundo cruel adios"]
eng._one = lambda args: (args[0], _texts[args[0]], 1.0,
                         [(w, 0.9, 0.0, 0.1) for w in _texts[args[0]].split()])
_out = eng.transcribe(wav4)
_check("join cose duplicado", _out == "hola mundo cruel adios")
_check("word_confs desplaza duplicado",
        [w for w, _c, _a, _b in eng.last_word_confs]
        == ["hola", "mundo", "cruel", "adios"])

# 12. H1 (medio): el retry total devuelve tambien confs/wconfs del rescate.
# Antes el texto era el rescatado pero last_conf/last_word_confs quedaban
# rancios del decode fallido (conf 1.0 / vacio): el gate LLM/bias pulia
# (o no) con evidencia equivocada. Las confs del retry llevan el offset
# rs del retry (aqui rs=0.0: arranque real del reintento total).
eng = _engine([(0.0, 1.0)], lambda _w: "x")
calls = {"n": 0}
def _one_retry(args):
    calls["n"] += 1
    if calls["n"] <= 1:
        return (args[0], "", 1.0, [])
    return (args[0], "hola mundo", 0.42,
            [("hola", 0.4, 0.1, 0.2), ("mundo", 0.5, 0.3, 0.4)])
eng._one = _one_retry
_out = eng.transcribe(wav)
_check("retry total rescata texto", _out == "hola mundo")
_check("retry total actualiza last_conf",
        abs(eng.last_conf - 0.42) < 1e-9)
_check("retry total actualiza word_confs",
        [w for w, _c, _a, _b in eng.last_word_confs] == ["hola", "mundo"]
        and abs(eng.last_word_confs[0][2] - 0.1) < 1e-9
        and abs(eng.last_word_confs[1][1] - 0.5) < 1e-9)

# 12b. H1 parcial: el retry de un segmento rescata texto + confs + offset.
eng = _engine([(0.0, 2.0), (3.2, 3.7)], lambda _w: "x")
_seq = {"n": 0}
def _one_partial(args):
    _seq["n"] += 1
    if _seq["n"] == 1:
        return (args[0], "primera", 0.8, [("primera", 0.8, 0.0, 0.1)])
    if _seq["n"] == 2:
        return (args[0], "", 1.0, [])
    return (args[0], "segunda", 0.33, [("segunda", 0.33, 0.2, 0.3)])
eng._one = _one_partial
_out = eng.transcribe(wav4)
_check("retry parcial rescata texto", _out == "primera segunda")
_exp_conf = (0.8 * len("primera") + 0.33 * len("segunda")) / (len("primera") + len("segunda"))
_check("retry parcial actualiza last_conf", abs(eng.last_conf - _exp_conf) < 1e-9)
_check("retry parcial actualiza word_confs con rs del retry",
        [w for w, _c, _a, _b in eng.last_word_confs] == ["primera", "segunda"]
        and abs(eng.last_word_confs[1][2] - (2.2 + 0.2)) < 1e-9)

 # 13. H2 (bajo): el drop de cada borde se calcula contra la pieza ya unida
 # (merged[-1]), igual que join_texts, no contra el acumulado plano.
 # "mundo noche dia" repite palabras del seg1 en el arranque del seg3 pero el
 # borde real seg2->seg3 ("noche dia" vs "mundo ...") no solapa: con el
 # acumulado el seg3 recortaba 3 palabras que join_texts conserva -> las
 # word_confs quedaban desalineadas del texto (el gate por posicion miraba
 # la vecina equivocada).
eng = _engine([(0.0, 2.0), (3.2, 3.7), (5.0, 5.5)], lambda _w: "x")
_t3 = ["hola mundo", "noche dia", "mundo noche dia claro"]
eng._one = lambda args: (args[0], _t3[args[0]], 1.0,
                         [(w, 0.9, 0.0, 0.1) for w in _t3[args[0]].split()])
_out = eng.transcribe(_tone(8.0))
_check("tres segs sin solape conservan todo",
        _out == "hola mundo noche dia mundo noche dia claro")
_check("drops por pieza no recortan repeticion lejana",
        [w for w, _c, _a, _b in eng.last_word_confs]
        == ["hola", "mundo", "noche", "dia", "mundo", "noche", "dia", "claro"])

# 14. merge_short_bounds no reune por encima del tope max_seg.
from instant2_app.engine import merge_short_bounds
_check("merge respeta max_seg",
        merge_short_bounds([(0.0, 19.0), (19.5, 21.0)], max_seg=20.0)
        == [(0.0, 19.0), (19.5, 21.0)])
_check("merge corto sigue uniendo",
        merge_short_bounds([(0.0, 0.3), (0.5, 2.0)], max_seg=20.0) == [(0.0, 2.0)])
