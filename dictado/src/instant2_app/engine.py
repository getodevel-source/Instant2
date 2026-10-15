"""STT: VAD (frases) + Parakeet int8 offline por segmento. CPU-only."""
import logging
import os
import re
import threading
import time

import numpy as np

from instant2_app.paths import model_paths

log = logging.getLogger("instant2")

SAMPLE_RATE = 16000
SILENCE_PEAK = 0.005


def _overlap_count(left, right, max_words=6):
    """Nº de palabras del arranque de `right` ya dichas al final de `left`.

    Núcleo compartido con `_dedup_overlap`: mismo criterio (mayor solape
    de palabras hasta `max_words`, casefold sin puntuación). Separado para
    que el decode pueda desplazar `last_word_confs` en vez de truncar la
    cola (ver `transcribe`).
    """
    if not left or not right:
        return 0
    norm = lambda w: re.sub(r"[^\w]", "", w.casefold())
    lw = left.split()
    rw = right.split()
    ln = [norm(w) for w in lw]
    rn = [norm(w) for w in rw]
    best = 0
    for k in range(1, min(max_words, len(ln), len(rn)) + 1):
        if ln[-k:] == rn[:k] and all(rn[:k]):
            best = k
    return best


def _dedup_overlap(left, right, max_words=6):
    """Quita de `right` el prefijo ya dicho al final de `left`.

    Los chunks se decodifican con overlap de audio real: la zona comun
    sale en ambos textos. Busca el mayor solape de palabras (hasta
    `max_words`) entre el final de `left` y el arranque de `right` y lo
    recorta. Comparacion casefold sin puntuacion; si no hay solape
    devuelve `right` intacto. Nunca inventa ni reordena.
    """
    best = _overlap_count(left, right, max_words)
    return " ".join(right.split()[best:]) if best else right


def join_texts(texts):
    """Une textos de segmentos: filtra vacios, cose overlap, pega
    puntuacion, colapsa espacios."""
    parts = [t.strip() for t in texts if t and t.strip()]
    if not parts:
        return ""
    merged = [parts[0]]
    for nxt in parts[1:]:
        merged.append(_dedup_overlap(merged[-1], nxt))
    s = " ".join(p for p in merged if p)
    s = re.sub(r"\s+([,.;:!?%)\]])", r"\1", s)
    s = re.sub(r"([(¿¡])\s+", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()

def merge_short_bounds(bounds, min_len=1.5, max_gap=1.0, max_seg=20.0):
    """Une un segmento corto (<min_len) con su vecino si el hueco <= max_gap.

    Evita decodificar palabras sueltas sin contexto (pierden precision).
    min_len 1.5s (antes 1.0s): los arranques de 1-1.5s sin contexto izquierdo
    son los que mas colapsan a blank en el transducer.
    Tope max_seg: no fusiona si el tramo unido lo supera (el VAD ya parte
    por max_seg; re-unirlo reintroduciria el chunk gigante).
    `bounds`: [(t0, t1)] en segundos. Devuelve lista nueva.
    """
    bs = [(float(s), float(e)) for s, e in bounds]
    if len(bs) < 2:
        return bs
    out = [bs[0]]
    for s, e in bs[1:]:
        ps, pe = out[-1]
        if (s - pe) <= max_gap and ((pe - ps) < min_len or (e - s) < min_len) and (e - ps) <= max_seg:
            out[-1] = (ps, e)
        else:
            out.append((s, e))
    return out


def frontend(wav):
    """Acondiciona audio para Parakeet: DC-remove + high-pass ~80 Hz.

    Quita el offset de continua del ADC y el retumbe grave antes de
    `_fit_level`: el modelo entrena con voz centrada en cero y ese piso
    constante cambia la hipotesis aunque al oido suene igual. Sin estado:
    opera sobre el take completo, nunca por bloques. No toca denoise
    (un reductor siempre-on degrada WER en audio limpio); solo deja el
    espectro donde vive la voz. Devuelve (wav, dc, clip_ratio).
    """
    w = np.ascontiguousarray(np.asarray(wav).flatten(), dtype=np.float32)
    if w.size == 0:
        return w, 0.0, 0.0
    dc = float(np.mean(w, dtype=np.float64))
    w = (w - dc).astype(np.float32)
    # High-pass ~80 Hz via FFT: anula bins bajo 80 Hz y vuelve al tiempo.
    # O(n log n), exacto, sin loops ni dependencias nuevas. En takes de
    # 60-120 s tarda ms.
    x = w.astype(np.float64)
    spec = np.fft.rfft(x)
    # `freqs` ocupa otros ~8 MB en una toma de 120 s. El mismo corte
    # estricto (<80 Hz) equivale a anular ceil(80*N/SR) bins; aritmética
    # entera conserva exactamente el conjunto de bins y evita esa matriz.
    cutoff_bins = (80 * x.size + SAMPLE_RATE - 1) // SAMPLE_RATE
    spec[:cutoff_bins] = 0.0
    y = np.ascontiguousarray(np.fft.irfft(spec, n=x.size), dtype=np.float32)
    clip = float(np.mean(np.abs(y) >= 0.999)) if y.size else 0.0
    return y, dc, clip


def _mean_conf(ys_log_probs):
    """Confianza 0..1 de un decode: exp(media de log-probs por token).

    sherpa-onnx expone `ys_log_probs` en el resultado del transducer.
    Sin scores (mock/modelo viejo) devuelve 1.0: sin evidencia de duda no
    se castiga al texto. Un segmento dudoso (media baja) es el que el
    gate LLM debe pulir; el seguro se pega directo.
    """
    try:
        probs = list(ys_log_probs) if ys_log_probs is not None else []
    except TypeError:
        return 1.0
    if not probs:
        return 1.0
    try:
        mean = float(sum(float(p) for p in probs) / len(probs))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, float(np.exp(mean))))


def _word_confs(tokens, ys_log_probs, timestamps):
    """[(palabra, conf, t0, t1)] alineando tokens BPE a palabras.

    Los tokens que empiezan con ' ' abren palabra (convencion sentencepiece
    de Parakeet). La confianza de la palabra es exp(min(log-probs)): el
    eslabon debil manda ("qu"=-0.68 delata "quemita" aunque "em"/"ita"
    salgan seguras). Sin scores devuelve []: sin evidencia no hay gate fino.
    """
    try:
        toks = [str(t) for t in (tokens or [])]
        probs = [float(p) for p in (ys_log_probs or [])]
        times = [float(t) for t in (timestamps or [])]
    except (TypeError, ValueError):
        return []
    if not toks or len(probs) < len(toks):
        return []
    if len(times) < len(toks):
        times = times + [times[-1] if times else 0.0] * (len(toks) - len(times))
    out, cur, cur_lp, cur_t0 = [], "", [], 0.0
    for i, tok in enumerate(toks):
        piece = tok[1:] if tok.startswith(" ") else tok
        if tok.startswith(" ") and cur:
            conf = max(0.0, min(1.0, float(np.exp(min(cur_lp)))) if cur_lp else 1.0)
            out.append((cur, conf, cur_t0, times[i - 1] if i else 0.0))
            cur, cur_lp = "", []
        if not cur:
            cur_t0 = times[i] if i < len(times) else 0.0
        cur += piece
        if i < len(probs):
            cur_lp.append(probs[i])
    if cur:
        conf = max(0.0, min(1.0, float(np.exp(min(cur_lp)))) if cur_lp else 1.0)
        out.append((cur, conf, cur_t0, times[-1]))
    return [(w, c, a, b) for w, c, a, b in out if w.strip()]


def _unpack_one(res):
    """(idx, text, conf, wconfs) desde `_one`, tolerando mocks viejos.

    Mocks de 2-tupla (idx, text) -> conf 1.0, sin word-confs. De 3-tupla
    (idx, text, conf) -> sin word-confs. Nunca tumba el decode por un mock.
    """
    if len(res) >= 4:
        return res[0], res[1], res[2], res[3]
    if len(res) == 3:
        return res[0], res[1], res[2], []
    return res[0], res[1], 1.0, []


def _fit_level(wav):
    """Sube chunks debiles a un RMS nominal (solo boost, nunca recorta).

    Hablar bajito o lejos cambiaba la hipotesis aunque al oido sonara igual.
    Con voz presente (pico >= SILENCE_PEAK) y RMS < 0.03, aplica ganancia
    hasta RMS 0.06 (tope 8x). Devuelve (wav, ganancia).
    """
    if wav.size == 0:
        return wav, 1.0
    peak = float(np.max(np.abs(wav)))
    if peak < SILENCE_PEAK:
        return wav, 1.0
    rms = float(np.sqrt(np.mean(np.square(wav, dtype=np.float64))))
    if rms >= 0.03 or rms < 1e-6:
        return wav, 1.0
    gain = min(8.0, 0.06 / rms)
    if gain <= 1.0:
        return wav, 1.0
    return np.clip(wav * gain, -1.0, 1.0).astype(np.float32), gain


class Engine:
    # Tope del reintento total: decodificar hasta 120s de audio duplicaba
    # el pico de CPU/RAM justo en el peor caso (audio largo/ruidoso).
    FULL_RETRY_MAX_SECONDS = 30.0
    # Overlap de audio real por borde de chunk (0.5 s por lado): el
    # transducer colapsa en palabras cortadas sin contexto; con overlap la
    # zona comun se decodifica dos veces y se cose en `join_texts`.
    EDGE_OVERLAP = 0.5
    # Limites defensivos de hilos ONNX intra-op.
    MIN_THREADS = 1
    MAX_THREADS = 8

    def __init__(self, data_dir=None, threads=4, max_seg=20.0,
                 vad_sil=0.5, vad_pad=0.3, min_dur=0.4, save_wavs_dir=None,
                 vad_model="silero", blank_penalty=0.0):
        from instant2_app.resource_policy import inference_threads
        threads = inference_threads(threads)
        self.paths = model_paths(data_dir)
        self.threads = threads
        self.max_seg = max_seg
        self.vad_sil = vad_sil
        self.vad_pad = vad_pad
        self.min_dur = min_dur
        # Penalidad al blank en greedy (resta al logit): reduce deletions
        # (vacios) pero pasada de rosca inventa inserciones. Default 0.0 =
        # comportamiento actual; solo se mueve con WER medido. Rango 0..1.
        try:
            bp = float(blank_penalty)
        except (TypeError, ValueError):
            bp = 0.0
        self.blank_penalty = max(0.0, min(1.0, bp))
        # VAD en uso: "silero" (default) o "ten" (opt-in, mas preciso).
        # Si se pide ten y el modelo falta, vad() cae a silero con aviso.
        self.vad_model = (vad_model or "silero").strip().lower()
        self._vad_name = self.vad_model
        # Opt-in (INSTANT2_SAVE_WAVS): guarda los wavs que el modelo deja
        # vacios, para medir con voz real en vez de suponer.
        self.save_wavs_dir = save_wavs_dir
        self._rec = None
        self._vad = None
        self._lock = threading.Lock()
        # La inferencia sobre el recognizer compartido se serializa (_one):
        # con decodificaciones concurrentes el transducer colapsaba a blank de
        # forma intermitente (~6% de sesiones). RTF ~0.05x deja margen.
        self._decode_lock = threading.Lock()
        # Fraccion de muestras saturadas del ultimo take (frontend): el
        # daemon la usa para avisar "baja la ganancia" en vez de transcribir
        # distorsion. 0.0 hasta el primer transcribe().
        self.last_clip = 0.0
        # Confianza 0..1 del ultimo take (media de log-probs por token).
        # 1.0 hasta el primer transcribe(): sin evidencia de duda no se pule.
        self.last_conf = 1.0
        # [(palabra, conf, t0, t1)] del ultimo take, segundos del take.
        # [] hasta el primer transcribe con scores.
        self.last_word_confs = []

    def unload(self):
        with self._lock:
            self._rec = None
            self._vad = None

    def has_loaded_models(self):
        """Consulta sin bloquear si hay algún modelo residente."""
        return self._rec is not None or self._vad is not None

    def recognizer(self):
        with self._lock:
            if self._rec is None:
                import sherpa_onnx

                p = self.paths
                for k in ("encoder", "decoder", "joiner", "tokens"):
                    if not os.path.isfile(p[k]):
                        raise FileNotFoundError(f"modelo Parakeet incompleto, falta: {p[k]} "
                                                f"(corre `instant2 setup`)")
                log.info("cargando Parakeet int8 threads=%d blank_penalty=%.2f ...",
                         self.threads, self.blank_penalty)
                t0 = time.time()
                self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
                    encoder=p["encoder"], decoder=p["decoder"], joiner=p["joiner"],
                    tokens=p["tokens"], num_threads=self.threads,
                    decoding_method="greedy_search", blank_penalty=self.blank_penalty,
                    model_type="nemo_transducer", provider="cpu", debug=False)
                log.info("modelo listo en %.1fs. GPU intacta (provider=cpu).", time.time() - t0)
                try:
                    s = self._rec.create_stream()
                    s.accept_waveform(SAMPLE_RATE, np.zeros(SAMPLE_RATE, dtype=np.float32))
                    self._rec.decode_stream(s)
                    log.info("warmup ok.")
                except Exception as e:
                    log.warning("warmup fail (no bloqueante): %s", e)
            return self._rec

    def vad(self):
        # Reutiliza una instancia: antes se creaba un VadModel nativo en
        # CADA transcribe() sin liberarlo (leak nativo acumulativo).
        with self._lock:
            if self._vad is not None:
                return self._vad
        import sherpa_onnx

        use_ten = (self.vad_model or "silero").strip().lower() in ("ten", "ten-vad")
        if use_ten and not os.path.isfile(self.paths["ten_vad"]):
            log.warning("ten-vad pedido pero falta %s; uso silero.",
                        self.paths["ten_vad"])
            use_ten = False
        cfg = sherpa_onnx.VadModelConfig()
        if use_ten:
            # TEN-VAD (int8, 126 KB): alternativa medida mas precisa que
            # Silero con overhead minimo. Ventana 256 (recomendada sherpa).
            cfg.ten_vad.model = self.paths["ten_vad"]
            cfg.ten_vad.threshold = 0.5
            cfg.ten_vad.min_silence_duration = self.vad_sil
            cfg.ten_vad.min_speech_duration = 0.2
            cfg.ten_vad.window_size = 256
            self._vad_name = "ten"
        else:
            if not os.path.isfile(self.paths["vad"]):
                raise FileNotFoundError(
                    f"falta VAD: {self.paths['vad']} (corre `instant2 setup`)")
            cfg.silero_vad.model = self.paths["vad"]
            cfg.silero_vad.threshold = 0.5
            cfg.silero_vad.min_silence_duration = self.vad_sil
            # 0.20s (antes 0.25s): los ataques suaves de la primera palabra
            # quedaban marcados como no-voz y el onset se recortaba.
            cfg.silero_vad.min_speech_duration = 0.2
            cfg.silero_vad.window_size = 512
            self._vad_name = "silero"
        cfg.sample_rate = SAMPLE_RATE
        inst = sherpa_onnx.VadModel.create(cfg)
        with self._lock:
            if self._vad is None:
                self._vad = inst
                return inst
            loser = inst
        # Hubo carrera: otra hebra gano; no dejar la instancia perdedora
        # colgada (handle nativo sin liberar).
        try:
            closer = getattr(loser, "close", None)
            if closer is not None:
                closer()
            else:
                del loser
        except Exception:
            pass
        with self._lock:
            return self._vad

    def segment(self, audio):
        """Corta audio en frases por VAD. Devuelve [(t0, t1)] en segundos."""
        wav = np.ascontiguousarray(np.asarray(audio).flatten(), dtype=np.float32)
        vad = self.vad()
        ws = vad.window_size()
        min_sil_w = max(1, int(self.vad_sil * SAMPLE_RATE / ws))
        speeches = []
        for i in range(0, len(wav) - ws + 1, ws):
            try:
                speeches.append(bool(vad.is_speech(wav[i:i + ws])))
            except Exception:
                log.exception("VAD window fail en %d", i)
                speeches.append(False)
        segs, start, sil = [], None, 0
        for i, sp in enumerate(speeches):
            if sp:
                if start is None:
                    start = i * ws
                sil = 0
            elif start is not None:
                sil += 1
                if sil >= min_sil_w:
                    segs.append((start, min(len(wav), i * ws)))
                    start, sil = None, 0
        if start is not None:
            segs.append((start, len(wav)))
        out = []
        for s, e in segs:
            while (e - s) / SAMPLE_RATE > self.max_seg:
                mid = (s + e) // 2
                w = SAMPLE_RATE
                lo, hi = max(s, mid - w), min(e, mid + w)
                if hi - lo < 1600:
                    cut = mid
                else:
                    frame = 1600
                    ergy = np.array([np.abs(wav[j:j + frame]).mean()
                                     for j in range(lo, hi - frame, frame)])
                    cut = lo + int(np.argmin(ergy)) * frame if len(ergy) else mid
                cut = max(s + SAMPLE_RATE, min(e - SAMPLE_RATE, cut))
                out.append((s, cut))
                s = cut
            out.append((s, e))
        res = []
        for s, e in out:
            s = max(0, int(s - self.vad_pad * SAMPLE_RATE))
            e = min(len(wav), int(e + self.vad_pad * SAMPLE_RATE))
            if (e - s) / SAMPLE_RATE >= 0.3:
                res.append((s / SAMPLE_RATE, e / SAMPLE_RATE))
        if not res and len(wav) / SAMPLE_RATE >= self.min_dur:
            peak = float(np.max(np.abs(wav))) if wav.size else 0.0
            if peak >= SILENCE_PEAK:
                log.warning("VAD no corto nada (habla continua?), todo junto %.1fs.",
                            len(wav) / SAMPLE_RATE)
                res = [(0.0, min(len(wav) / SAMPLE_RATE, 120.0))]
        return res

    def _one(self, args):
        idx, wav = args
        rec = self.recognizer()
        # Serializa la inferencia: el recognizer es compartido y el decode
        # concurrente producia vacios intermitentes.
        with self._decode_lock:
            s = rec.create_stream()
            s.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(wav, dtype=np.float32))
            rec.decode_stream(s)
            text = (s.result.text or "").strip()
            conf = _mean_conf(getattr(s.result, "ys_log_probs", None))
            wconfs = _word_confs(getattr(s.result, "tokens", None),
                                 getattr(s.result, "ys_log_probs", None),
                                 getattr(s.result, "timestamps", None))
            return idx, text, conf, wconfs

    def _recover_empties(self, wav, peak, bounds, chunks, texts, confs=None, wconfs=None):
        """Reintento secuencial de segmentos vacios con contexto ampliado.

        El transducer colapsa a blank en chunks cortos o cortados a mitad de
        palabra aunque el pico sea bueno. Solo actua en la via de fallo: sin
        vacios no decodifica nada extra.

        Si se pasan `confs`/`wconfs` se actualizan en el sitio con los del
        reintento (el `rs` del retry queda en `chunks[i][3]` para el offset
        temporal): sin esto `last_conf`/`last_word_confs` quedaban rancios
        del decode fallido mientras el texto era el rescatado. Sin ellos
        devuelve solo la lista (compat con llamantes viejos).
        """
        fixed = list(texts)
        fixed_c = list(confs) if confs is not None else None
        fixed_w = [list(w) for w in wconfs] if wconfs is not None else None
        for idx, text in enumerate(fixed):
            if text:
                continue
            s, e = bounds[idx]
            cpeak = float(np.max(np.abs(chunks[idx][1]))) if chunks[idx][1].size else 0.0
            cdur = len(chunks[idx][1]) / SAMPLE_RATE
            if cpeak < SILENCE_PEAK or cdur < 0.3:
                continue
            # Contexto +-1s: le devuelve al transducer el arranque que el
            # corte le saco (la causa del inicio recortado).
            rs = max(0.0, s - 1.0)
            re_ = min(len(wav) / SAMPLE_RATE, e + 1.0)
            rwav, rgain = _fit_level(wav[int(rs * SAMPLE_RATE):int(re_ * SAMPLE_RATE)])
            try:
                _, retry, rc, rw = _unpack_one(self._one((-1, rwav)))
                retry = (retry or "").strip()
            except Exception:
                log.exception("reintento seg %d fail", idx + 1)
                continue
            log.info("seg %d/%d reintento (%.1f-%.1fs pico=%.4f g=%.1f): %d caracteres",
                     idx + 1, len(chunks), rs, re_, cpeak, rgain, len(retry))
            if retry:
                fixed[idx] = retry
                if fixed_c is not None:
                    fixed_c[idx] = rc
                if fixed_w is not None:
                    fixed_w[idx] = list(rw) if rw else []
                c0 = chunks[idx]
                if len(c0) >= 4:
                    chunks[idx] = (c0[0], c0[1], c0[2], rs)
        if any(fixed):
            if confs is not None:
                confs[:] = fixed_c
            if wconfs is not None:
                wconfs[:] = fixed_w
            return fixed
        # Perdida total con audio fuerte: ultimo intento acotado (antes 120s:
        # duplicaba el pico de CPU/RAM justo en el peor caso).
        if peak >= SILENCE_PEAK and len(wav) / SAMPLE_RATE >= 0.3:
            capped = min(len(wav) / SAMPLE_RATE, self.FULL_RETRY_MAX_SECONDS)
            full = wav[:int(capped * SAMPLE_RATE)]
            try:
                _, retry, rc, rw = _unpack_one(self._one((-1, full)))
                retry = (retry or "").strip()
            except Exception:
                log.exception("reintento total fail")
                return fixed
            log.info("reintento total (%.1fs): %d caracteres",
                     len(full) / SAMPLE_RATE, len(retry))
            if retry:
                if confs is not None or wconfs is not None:
                    if confs is not None:
                        confs[:] = [rc]
                    if wconfs is not None:
                        wconfs[:] = [list(rw) if rw else []]
                    bounds[:] = [(0.0, capped)]
                    chunks[:] = [(0, full, 1.0, 0.0)]
                return [retry]
        return fixed

    def transcribe(self, audio):
        """VAD + Parakeet por segmento en serie. Devuelve texto unido."""
        wav = np.ascontiguousarray(np.asarray(audio).flatten(), dtype=np.float32)
        wav, dc, clip = frontend(wav)
        dur = len(wav) / SAMPLE_RATE
        peak = float(np.max(np.abs(wav))) if wav.size else 0.0
        log.info("audio %.1fs pico=%.4f dc=%.5f clip=%.3f, segmentando...",
                 dur, peak, dc, clip)
        t0 = time.time()
        bounds = merge_short_bounds(self.segment(wav), max_seg=getattr(self, "max_seg", 20.0))
        log.info("%d segmentos en %.2fs: %s.", len(bounds), time.time() - t0,
                 ", ".join(f"{s:.2f}-{e:.2f}" for s, e in bounds))
        if not bounds:
            self.last_clip = clip
            self.last_conf = 1.0
            self.last_word_confs = []
            return ""
        chunks = []
        # Overlap de audio REAL en bordes (EDGE_OVERLAP 0.5 s por lado):
        # cada chunk ve el arranque/cola del vecino para que el transducer
        # no decodifique palabras cortadas sin contexto. La zona comun sale
        # en ambos textos y `join_texts` la cose con `_dedup_overlap`.
        # Solo en la via con >1 segmento: con 1 chunk no hay borde que coser
        # y el decode extra seria puro costo.
        ov = self.EDGE_OVERLAP if len(bounds) > 1 else 0.0
        for i, (s, e) in enumerate(bounds):
            rs = max(0.0, s - ov)
            re_ = min(len(wav) / SAMPLE_RATE, e + ov)
            raw = wav[int(rs * SAMPLE_RATE):int(re_ * SAMPLE_RATE)]
            normed, gain = _fit_level(raw)
            chunks.append((i, normed, gain, rs))
            del raw
        t0 = time.time()
        # Decode SERIADO: el recognizer ya se serializaba con _decode_lock
        # (el decode concurrente colapsaba a blank) pero el ThreadPool
        # mantenia N hilos bloqueados reteniendo chunks mientras cada decode
        # usaba `threads` hilos ONNX -> pico NxM CPU-bound que congelaba el
        # SO. En serie el RTF es el mismo y el pico es 1xM.
        texts = [""] * len(chunks)
        confs = [1.0] * len(chunks)
        wconfs = [[] for _ in chunks]
        for i, w, _g, _rs in chunks:
            idx, text, conf, wc = _unpack_one(self._one((i, w)))
            texts[idx] = (text or "").strip()
            confs[idx] = conf
            wconfs[idx] = wc
            cpeak = float(np.max(np.abs(chunks[idx][1]))) if chunks[idx][1].size else 0.0
            log.info("seg %d/%d (%.1fs pico=%.4f g=%.1f conf=%.2f): %d caracteres",
                     idx + 1, len(chunks),
                     len(chunks[idx][1]) / SAMPLE_RATE, cpeak, chunks[idx][2],
                     conf, len(texts[idx]))
        log.info("decode %d segs en %.2fs.", len(chunks), time.time() - t0)
        texts = self._recover_empties(wav, peak, bounds, chunks, texts, confs, wconfs)
        if self.save_wavs_dir and not any(t.strip() for t in texts):
            self._dump_failure(wav, peak, dur, bounds)
        self.last_clip = clip
        # Confianza del take: media ponderada por longitud (segmentos vacios
        # no aportan). El gate LLM pule solo takes dudosos.
        weights = [len(t) for t in texts]
        self.last_conf = (sum(c * w for c, w in zip(confs, weights)) / sum(weights)
                          if sum(weights) else 1.0)
        # Confianzas por palabra (con offset temporal del chunk): el bias
        # abre el gate por palabra aunque el take sea seguro. B2: el offset
        # es rs (arranque REAL del chunk con overlap), no s: con s todo el
        # take multi-segmento iba +ov desplazado. B3: el dedup recorta el
        # prefijo duplicado de cada borde, asi que de cada segmento se saltan
        # `drop` palabras (no se trunca la cola): el gate mira la vecina
        # correcta. join_texts usa el mismo _overlap_count: mismo recorte.
        merged = []
        drops = [0] * len(texts)
        # Igual que join_texts: cada borde se cose contra la pieza ya
        # unida (merged[-1]), no contra el acumulado plano. Con el
        # acumulado, una repeticion legitima en un borde tardio podia
        # confundirse con un duplicado del primer segmento (3+ segs).
        pieces = []
        for j, t in enumerate(texts):
            s = (t or "").strip()
            if not s:
                continue
            if pieces:
                drops[j] = _overlap_count(pieces[-1], s)
                pieces.append(" ".join(s.split()[drops[j]:]))
            else:
                pieces.append(s)
        for j, (_s, _e) in enumerate(bounds):
            rs = chunks[j][3]
            for w, c, a, b in wconfs[j][drops[j]:]:
                merged.append((w, c, rs + a, rs + b))
        self.last_word_confs = merged
        return join_texts(texts)

    def _dump_failure(self, wav, peak, dur, bounds):
        """Guarda el wav que el modelo dejo vacio + meta, sin tumbar nunca."""
        try:
            import datetime
            import wave

            from instant2_app.paths import ensure_private_dir, restrict_file
            ensure_private_dir(self.save_wavs_dir)
            wavs = sorted(
                (os.path.join(self.save_wavs_dir, f) for f in os.listdir(self.save_wavs_dir)
                 if f.endswith(".wav")),
                key=lambda p: os.path.getmtime(p))
            while len(wavs) >= 50:
                try:
                    os.remove(wavs.pop(0))
                except OSError:
                    break
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            base = os.path.join(self.save_wavs_dir, f"fallo-{stamp}")
            pcm = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(base + ".wav", "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm.tobytes())
            restrict_file(base + ".wav")
            with open(base + ".txt", "w", encoding="utf-8") as f:
                f.write(f"dur={dur:.1f}s pico={peak:.4f} "
                        f"bounds={[(round(s, 2), round(e, 2)) for s, e in bounds]}\n")
            restrict_file(base + ".txt")
            log.info("wav de fallo guardado en %s.", base + ".wav")
        except Exception:
            log.exception("no pude guardar wav de fallo")
