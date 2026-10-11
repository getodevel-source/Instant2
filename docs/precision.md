# Precisión del dictado (Parakeet TDT v3 + Silero VAD, CPU)

Un solo documento: decisión del motor, diagnóstico de variabilidad, rondas
de mejora con números y lo que queda. El plan de modelo nuevo vive en
[`plan-finetune.md`](plan-finetune.md).

## Motor: Parakeet (Qwen retirado)

Medido en 34 frases ES con técnicos/preguntas/nombres, dos pasadas de voces:
Parakeet y Qwen+hotwords empatan en WER (0.056-0.092 vs 0.069-0.075, ranking
invertido entre pasadas). Decisión por lo concluyente: Parakeet 3× más rápido
(0.25 s vs 0.8 s) y pone el `¿` en 4/8 preguntas (Qwen 0/8). Qwen retirado
(modelo, A/B y arnés eliminados).

Vías descartadas (no repetir): hotwords en Parakeet (falta `bpe.vocab`;
greedy los rechaza con `ValueError`), `modified_beam_search` (ruido, y con
TDT alucina ~20 % según sherpa-onnx#3267), modelo de puntuación ES
(inexistente en sherpa-onnx), HomophoneReplacer (solo zh), fusión ROVER
(4× tiempo por 5 errores/720 palabras), clave fonética sola (insuficiente),
boosting nativo NeMo 2.5 (exige GPU; en CPU sherpa-onnx lo rechaza en greedy
y el beam TDT está roto: callejón hasta nuevo release).

## Diagnóstico: el input nunca es el mismo (2026-10-03, 231 audios reales)

Audio sano (pico mín 0.0076 > gate 0.005) y aun así 14 sesiones (~6 %) con
vacíos: segmentación VAD + decode aislado por segmento + colapso greedy a
blank. 33 % de dictados en >1 segmento. Causas: apertura del stream tras la
tecla (mitigado con pre-roll continuo 0.45 s), VAD recortando ataques,
transducer sin contexto izquierdo en cortes, sin AGC (mitigado con
`_fit_level` boost-only), post sin red de seguridad.

## Rondas y números

- Fixes base: `_decode_lock` (race del recognizer), `_recover_empties`
  (retry ±1 s + total 30 s), `merge_short_bounds` 1.5 s, VAD 0.20 s/pad 0.3 s,
  flush 0.3 s, pre-roll continuo, `DICTADO_SAVE_WAVS`.
- Frontend (`frontend`): DC-remove + high-pass 80 Hz + aviso de clip >2 %.
- Overlap 0.5 s de audio real por borde + costura (`_dedup_overlap`).
- TEN-VAD int8 opt-in (`--vad-model ten`); default Silero.
- Confianza por decode y por palabra (`ys_log_probs`); LLM solo en dudosos.
- `restore_openers`: `¿` determinista en preguntas-Q.
- `blank_penalty` cableado, default 0.
- Bias general (`bias.py`): diccionario técnico + spotter fonético + vecinas
  + regla C (Will→build con 2 vecinas, sin gate) + guarda inglés. Batería
  adversaria permanente: 30 sanas intactas.

| Bench | Resultado |
|---|---|
| FLEURS es_419 60 clips | baseline 0.050; bias 0.050 (intacto); resto ±0.003 = ruido |
| bench_bias 24 frases TTS degradadas | 0.254 → 0.206 (−19 %) |
| Voz + mic reales, técnico | 12/12 técnicos bien; 0 FP en 129 palabras sanas (WER 5.4 %) |
| Voz + mic reales, general 129 palabras | 5.4 % (todo del motor, nada del bias) |

## Lo que queda

Para evaluar voces y micrófonos nuevos sin enviar datos fuera del equipo,
está disponible [`bench_real_audio.py`](../dictado/bench/bench_real_audio.py).
Requiere consentimiento afirmativo por clip y genera únicamente métricas
agregadas; el esquema del manifiesto, las reglas de privacidad y los límites
del benchmark están en la sección “Evaluación privada de voz real” de este
documento. Todavía no hay un baseline nuevo: el corpus debe ser aportado y
consentido antes de medirlo.

Modelo: los técnicos seguros-erróneos ("Will" conf alta) y alucinaciones
("compara Git") no los arregla texto. Vía: fine-tune LoRA del decoder
([`plan-finetune.md`](plan-finetune.md)). Rescoring KenLM medido y
descartado (el sobremuestreo premia "commit" hasta sobre "comer": peligro FP).

## Evaluación privada de voz real

Desde `dictado/`, crear localmente `bench/private-real-audio.csv` con estas
columnas: `path,reference,condition,microphone,speaker_id,consent`. Cada fila
debe identificar un clip WAV PCM de 16 bits y 16 kHz (mono o estéreo), una
referencia revisada, categorías generales de condición/micrófono y un
identificador pseudónimo de hablante. `consent` debe ser `yes`, `si`, `sí`,
`true` o `1`. La ruta puede ser absoluta o relativa al CSV.

Ejecutar con los modelos de Instant ya descargados:

```bash
python bench/bench_real_audio.py --manifest bench/private-real-audio.csv
```

El resultado muestra WER agrupado, sustituciones, borrados, inserciones,
fragmentos vacíos y cambios/regresiones del corrector. Opcionalmente,
`--json bench/private-real-audio-summary.json` guarda solo esas métricas. No
se imprimen referencias, audio, rutas ni IDs de hablante. El manifiesto y los
WAV quedan excluidos de Git por defecto. No agregar audio real al repositorio,
al CI o a servicios externos; pedir autorización a cada hablante y permitir
que retire su clip. La herramienta no crea consentimiento: solo verifica que
la persona responsable haya marcado afirmativamente cada fila.
