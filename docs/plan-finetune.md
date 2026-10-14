# Plan fine-tune Parakeet v3 → español técnico (Instant2)

Objetivo: un encoder/joiner que reconozca commit/build/deploy/… de verdad,
en vez de rescatarlos por texto. La app no cambia: los ONNX fine-tuneados
reemplazan los archivos en `models/parakeet-v3-int8/` (opt-in).

## Por qué

El techo actual (WER 5 % general, 8-19 % técnico) es del modelo base, no del
pipeline: commit/build no están en su vocabulario efectivo y los mapea a lo
parecido en español. El bias textual rescata (−19 %) pero no puede con
errores seguros ("Will" con conf alta) ni alucinaciones ("compara Git").
Solo un modelo que haya VISTO esos términos los emite bien.

## Corpus (mezcla 80/20 contra olvido catastrófico)

| Fuente | Horas | Rol |
|---|---|---|
| FLEURS es_419 train | ~3 | español general (no olvidar) |
| Frases técnicas TTS (4 voces ES, edge-tts) | ~1 | commit/build/deploy/… en contexto |
| Tomas reales del usuario + referencia | min | el mic y la voz que importan |
| bench_bias refs x20 (ya existe) | — | sobremuestreo textual del dominio |

Todo con transcripción exacta. `dictado/bench/.bias_cache/` y
`.fleurs_es_cache/train_es.txt` ya existen como base.

## Receta (NeMo, LoRA en decoder/joiner)

1. Base `nvidia/parakeet-tdt-0.6b-v3` en NeMo 2.5+.
2. LoRA (r=8-16) solo en decoder+joiner; encoder congelado (el oído ya sirve).
3. 5-10 epochs, lr 1e-4, early stop en WER dev técnico.
4. Export ONNX int8 con `sherpa-onnx/scripts/nemo/` (mismo formato actual).
5. Validar: FLEURS-60 + bench_bias + tomas reales antes/después.

## Dónde entrenar (nuestro hardware no alcanza: CPU)

| Opción | Costo aprox | Notas |
|---|---|---|
| Kaggle GPU (30 h/sem gratis) | 0 | alcanza para LoRA 0.6B en días |
| Colab Pro | ~US$10/mes | más simple, menos horas |
| Vast/RunPod RTX 4090 x24 h | ~US$10-15 | lo más rápido y barato puntual |
| Comprar GPU local | US$1500+ | solo si el ciclo se repite |

LoRA 0.6B cabe en 16 GB VRAM. Sin GPU local: RunPod puntual es lo óptimo.

## Criterios de aceptación (harness ya existe)

- FLEURS-60: no empeora más de +0.005 WER (olvido bajo control).
- bench_bias (24 frases): mejora ≥20 % vs baseline actual.
- Tomas reales: "Will"→build y técnicos seguros se emiten bien.
- Si no cumple las tres, no se distribuye. Sin excepciones.

## Riesgos

- Corpus chico → overfit a 4 voces TTS. Mitiga: RIRs/ruido + tus tomas.
- Distribución: +670 MB opt-in (`parakeet-v3-instant2/`), setup lo baja aparte.
- Licencia: CC-BY-4.0 permite derivados con atribución.
