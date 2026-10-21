# Auditoría del LLM local y del camino de corrección

**Fecha:** 16 de octubre de 2026 · **Revisión auditada:** rama `main`, commit `0c56c98` (versión 0.1.5)
**Alcance:** lectura del código de producción y perfil puntual de una máquina. La evaluación de Voice-to-Prompt se informa en una sección separada y en `docs/BENCHMARK-MODELOS.md`; no describe calidad del flujo de corrección.

Este documento separa a propósito dos cosas que suelen mezclarse:

1. **Hechos de implementación** (qué hace el código, con archivo y línea).
2. **Perfil observado en este equipo** (una corrida, no una garantía ni una recomendación).

Toda afirmación de rendimiento o de fidelidad del texto es una **observación de una máquina**, no una propiedad del producto.

---

## Resumen

- El runtime administrado arranca `llama-server` con **contexto de 4096 tokens**, **una sola ranura** y los hilos calculados desde la CPU (`llm_runtime.py:284-304`).
- El modelo local por defecto es **Qwen3.5 4B Q4_K_M en GGUF** (`config.py:315-320`, `THIRD-PARTY-NOTICES.txt:12-15`).
- La conversación que se envía tiene **siempre dos mensajes**: `system` + `user`. No hay turnos previos, ni historial, ni streaming (`llm.py:187-188`, `llm.py:246-251`).
- El modo directo se pide con `chat_template_kwargs: {"enable_thinking": false}` (`llm.py:190`, `llm.py:253`). Es el **único** mecanismo real: este modelo **no** tiene `/think` ni `/nothink`.
- El diccionario técnico `bias.correct_biased()` **no tiene llamador en producción**: solo lo usan benches y pruebas. Lo único que llega al LLM es su glosario de grafías (`bias.py:329`, `bias.py:366`, `llm.py:348-352`).
- Los fallbacks son seguros (ante cualquier problema se conserva el texto local) pero **silenciosos**: varios caminos degradan la función sin avisar al usuario.
- En producción hay dos contratos de system prompt distintos: `DEFAULT_SYSTEM` para corregir la transcripción y `VERIFY_SYSTEM` para aceptar o rechazar esa corrección (`dictado/src/instant2_app/llm.py:76-100`, `:104-129`). No existe un system prompt de Voice-to-Prompt en el producto.

---

## 1. Hechos de implementación (repositorio)

### 1.1 Modelo y cuantización

| Dato | Valor | Referencia |
|---|---|---|
| Modelo | Qwen3.5 4B | `THIRD-PARTY-NOTICES.txt:12-15` |
| Cuantización | **Q4_K_M** (GGUF; en el runtime se reporta `Q4_K - Medium`) | `THIRD-PARTY-NOTICES.txt:14-15` |
| Archivo esperado | `%LOCALAPPDATA%\Instant2\models\llm\Qwen_Qwen3.5-4B-Q4_K_M.gguf` | `config.py:317-318` |
| Nombre de modelo para la API | `qwen3.5:4b-q4_K_M` (alias que recibe el servidor) | `config.py:327`, `llm_runtime.py:292-294` |
| Licencia declarada | Apache-2.0 | `THIRD-PARTY-NOTICES.txt:14` |

La autoconfiguración de la app instalada rellena endpoint, modelo, ejecutable y ruta del GGUF solo si los dos archivos existen (`config.py:315-342`).

### 1.2 Runtime y parámetros del servidor

`LocalLLMRuntime._args()` (`llm_runtime.py:284-304`) construye esta línea de comandos:

```
llama-server --model <gguf> --host 127.0.0.1 --port <p> --no-webui --log-disable
             --ctx-size 4096 --parallel 1 --threads <max(1, min(8, cpu//2))>
             --alias <llm_model>
             [--device <dev> --n-gpu-layers 99 | --device none --n-gpu-layers 0]
             [--sleep-idle-seconds <s>]
```

- `--ctx-size 4096` y `--parallel 1`: `llm_runtime.py:287-288`.
- Hilos: la mitad de los lógicos, tope 8 (`llm_runtime.py:289`).
- Aceleración: GPU con 99 capas si se elige dispositivo, CPU si no (`llm_runtime.py:295-297`).
- Reposo: `--sleep-idle-seconds` solo si el binario lo soporta, detectado leyendo su `--help` (`llm_runtime.py:231-245`, `llm_runtime.py:303`).
- Estimaciones de memoria antes de cargar (`llm_runtime.py:38-52`, `llm_runtime.py:69-79`).
- Elección de dispositivo por VRAM libre (`llm_runtime.py:136-150`).

**No** se pasan `--jinja`, `--reasoning`, `--reasoning-format`, `--chat-template-kwargs`, `--json-schema`, `--flash-attn` ni tipos de caché KV.

### 1.3 Roles y plantilla de chat

- Se envían exactamente dos mensajes: `system` con la instrucción y `user` con el texto (`llm.py:187-188` en corrección; `llm.py:246-251` en verificación).
- La plantilla del modelo exige que un mensaje `system`, si existe, sea el primero; el armado actual lo cumple.
- La plantilla del GGUF cierra el prompt del asistente con `'<|im_start|>assistant\n'` y luego, **si `enable_thinking` es exactamente `false`**, inserta un bloque de pensamiento vacío; si no, abre el bloque de pensamiento.

### Contratos de los prompts de sistema en producción

`DEFAULT_SYSTEM` es el contrato de **corrección** (`dictado/src/instant2_app/llm.py:76-100`): parte de una hipótesis ASR y permite corregir ortografía, puntuación y errores de reconocimiento solo con alta confianza. Conserva intención, nombres, cifras, fechas, unidades, negaciones, restricciones y requisitos, así como la variedad regional y el voseo argentino. No añade tareas, información ni explicaciones; no reordena palabras. El glosario solo resuelve la grafía de términos ya presentes. La respuesta es exclusivamente el texto final.

`VERIFY_SYSTEM` es un contrato separado de **verificación** (`dictado/src/instant2_app/llm.py:104-129`): responde `ACCEPT` solo si la propuesta mantiene el mismo pedido y datos; protege restricciones, requisitos y persona/tiempo/modo verbal, rechaza cambios especulativos o meramente naturalizantes y devuelve exactamente `ACCEPT` o `REJECT`, sin explicación. No redacta ni corrige el texto.

Estos prompts pertenecen al flujo de corrección de dictado. **No hay un prompt de sistema de Voice-to-Prompt en producción**; la medición V2P está aislada y no modifica el producto.

### 1.4 Modo directo: `enable_thinking=false` es real; `/think` y `/nothink` no existen

Esto se verificó contra el archivo de modelo, no por suposición:

| Comprobación | Resultado |
|---|---|
| Cómo pide el producto el modo directo | `chat_template_kwargs: {"enable_thinking": false}` (`llm.py:190`, `llm.py:253`) |
| Condición en la plantilla del GGUF | `{%- if enable_thinking is defined and enable_thinking is false %}` |
| Ocurrencias de `/nothink` en el GGUF | **0** |
| Ocurrencias de `reasoning_effort` en el GGUF | **0** (la plantilla no implementa esfuerzo de razonamiento) |
| Campo `chat_template_kwargs` aceptado por el servidor | Sí: es un campo documentado de la API; `--chat-template-kwargs` existe como equivalente de arranque |
| `--jinja` | Habilitado por defecto en el build observado |
| `--reasoning` / `--reasoning-format` | Disponibles, por defecto `auto`; el producto no los usa |

Conclusión: **no** hay interruptores de texto tipo `/think` o `/nothink`. Si el campo no se envía, el modelo razona; si se envía `false`, responde directo. Cambiar de servidor (otro proveedor que ignore `chat_template_kwargs`) reintroduce el razonamiento.

### 1.5 Parámetros de generación

| Parámetro | Valor | Referencia |
|---|---|---|
| `temperature` | `0.0` (determinista) | `llm.py:189`, `llm.py:252` |
| `max_tokens` (corrección) | `min(512, max(64, palabras*4 + 32))` | `llm.py:191-193` |
| `max_tokens` (verificación) | `8` | `llm.py:254` |
| `chat_template_kwargs` | `{"enable_thinking": False}` en ambas llamadas | `llm.py:190`, `llm.py:253` |
| `stream` | **no se envía** (siempre petición bloqueante) | `llm.py:208`, `llm.py:263` |
| Truncado | `finish_reason == "length"` ⇒ se descarta la propuesta y se conserva el texto local | `llm.py:212-214` |

### 1.6 Sin streaming, sin historial, sin persistencia de texto

- **Sin streaming de chat**: ninguna llamada de `instant2_app` envía `stream`. Solo la lectura en voz alta (TTS) produce audio por bloques, que es otra cosa.
- **Sin historial**: cada dictado es una petición nueva de dos mensajes; nada se acumula entre dictados.
- **Sin texto persistido**: el estado que publica el proceso de dictado excluye explícitamente configuración, indicaciones y texto (`llm_runtime_status.py:21-32`); la configuración (`config.py`) no guarda transcripciones.
- **Sin contexto ambiente**: no se lee la ventana en primer plano ni el portapapeles para alimentar al LLM. La única fuente de texto extra es la lectura de selección a pedido del usuario (F10), que es un flujo aparte.

### 1.7 Presupuestos y tiempos de espera

- El presupuesto de corrección es **compartido**: espera de arranque del servidor administrado + llamada de corrección + verificación (`dictation.py:7-26`).
- Servicio administrado: 8 s si está despierto, **60 s si está dormido** (`llm_runtime.py:590-597`); servicio externo o sin administrar: 8 s (`dictation.py:18`).
- La espera máxima del evento de arranque es 15 s (`dictation.py:20-21`).
- La verificación recibe lo que queda del presupuesto, con tope de 2 s (`llm.py:361-365`).
- Nota técnica: los tiempos de espera son por operación de socket, así que el tiempo total puede superar el número nominal frente a un servidor lento o una respuesta parcial (ya documentado en `docs/auditoria-ocho-mejoras.md`).

### 1.8 Camino de reconocimiento (Parakeet)

| Etapa | Implementación | Referencia |
|---|---|---|
| Limpieza previa | quita continua + pasa-altos ~80 Hz en FFT, sin denoise | `engine.py:91-118` |
| Reconocedor | `sherpa_onnx.OfflineRecognizer.from_transducer`, `greedy_search`, `nemo_transducer`, **`provider="cpu"`** | `engine.py:290-295` |
| Detección de voz | TEN-VAD si se pidió **y** existe el archivo; si no, Silero | `engine.py:313-345` |
| Segmentación | tope de 20 s por tramo, fusión de tramos cortos | `engine.py:68-89`, `engine.py:360-388` |
| Unión de tramos | cose de solape por palabras | `engine.py:18-66` |
| Orquestación | `_job`: ASR → preparación de texto → pegado | `daemon.py:1065-1092` |

El reconocimiento corre en CPU a propósito: la GPU queda para el servicio de lenguaje (`engine.py:294-295`).

### 1.9 Contexto y glosario

- Las correcciones locales de alias/vocabulario se aplican **después** del reconocimiento: `context.correct_aliases()` (`context.py:206-389`), invocada desde `llm.correct_locally()` (`llm.py:375-379`).
- El glosario que recibe el LLM se arma con `context.correction_context()` (`context.py:392-399`) más `bias.terms_for_correction()` (`bias.py:366`), y se agrega al mensaje de sistema (`llm.py:183-184`, `llm.py:348-352`).
- El perfil activo de vocabulario vive en la configuración del usuario (`config.py`); sin perfil, el glosario general sigue aplicándose.

### 1.10 Diccionario técnico sin llamador en producción

`bias.correct_biased()` (`bias.py:329-365`) —el corrector que reescribe términos técnicos deformados por el ASR— **no se invoca en el camino de producción**. Sus únicos llamadores están en benches (`dictado/bench/bench_bias.py`, `bench_wer.py`, `bench_real_audio.py`) y en pruebas. En producción solo se usa `bias.terms_for_correction()` como **referencia de grafías** para el LLM.

Consecuencia práctica: sin LLM configurado, esa corrección de diccionario técnico no ocurre. Es una diferencia real entre lo que describen `CHANGELOG.md:243-252` y `docs/precision.md:43-52` (bias como etapa del camino, con efecto medido solo en su bench) y lo que ejecuta el código actual.

### 1.11 Fallbacks: seguros de intención, silenciosos en la práctica

| Disparador | Qué hace el código | Referencia | Riesgo |
|---|---|---|---|
| `finish_reason == "length"` | descarta la propuesta, conserva el texto local | `llm.py:212-214` | El truncado no se comunica al usuario; solo queda en el registro |
| El servidor ignora `chat_template_kwargs` (razona igual) | la verificación recibe contenido vacío ⇒ no es `ACCEPT` ⇒ texto local | `llm.py:253-254`, `llm.py:271-272` | La función se apaga sola, sin aviso |
| La verificación no termina dentro del presupuesto (≤2 s) | `REJECT` ⇒ texto local | `llm.py:361-365` | Con modelo dormido, la mejora rara vez se aplica |
| Excepción de red o del servidor | conserva el texto corregido localmente | `llm.py:367-372` | Correcto; no hay aviso visible |
| Sin endpoint configurado | solo corrección local | `dictation.py:16` | Correcto |
| TEN-VAD pedido y archivo ausente | usa Silero y lo registra como advertencia | `engine.py:313-317` | Cambia el comportamiento de segmentación respecto de lo configurado |

Además, el candidato del LLM se usa como texto final del dictado (`llm.py:167-175`): la salvaguarda es la comprobación de significado (`llm.py:223-272`) más el filtro determinista de hechos (`llm.py:460-479`), que protegen cifras, palabras numéricas, restricciones e inserciones, pero no todas las sustituciones léxicas.

---

## 2. Perfil observado en este equipo (una sola máquina)

> Observación puntual durante esta auditoría. No es una garantía ni una recomendación para otros equipos.

| Recurso | Observado |
|---|---|
| CPU | AMD Ryzen 5 7600, 6 núcleos / 12 lógicos |
| Memoria | 15,1 GiB totales |
| GPU usada por el runtime | AMD Radeon RX 6800 XT, 16 GB (reportada por llama.cpp como `Vulkan0`: 16.368 MiB, 15.569 MiB libres) |
| GPU secundaria | Radeon integrada (`Vulkan1`: 7.988 MiB, 7.589 MiB libres) |
| Runtime | `llama-server` build 11573 (`0.6.0-dev`, commit `0f701c882`, Clang 20.1.8, Windows x86_64) |
| Modelo en disco | `Qwen_Qwen3.5-4B-Q4_K_M.gguf`, 3.013.027.808 bytes (2,81 GiB) |
| Contexto efectivo del servidor en ejecución | 4096 tokens, 1 ranura |
| Estado del endpoint | `http://127.0.0.1:8080` respondía `health: ok`; el servidor estaba **en reposo** (`is_sleeping: true`) al momento de la observación |
| Primera respuesta tras el reposo | ~16,8 s |
| Respuestas en caliente | 0,17 s – 0,75 s (textos sintéticos cortos) |
| Configuración del usuario en ese momento | `llm_url` vacío: el dictado corría **solo con corrección local** |
| Advertencia registrada | TEN-VAD pedido en la configuración, archivo `ten_vad.onnx` ausente ⇒ Silero |

Matiz importante: un servidor local con el GGUF estaba en ejecución, pero **no** por la configuración observada (que no tenía endpoint). Es decir, había un runtime listo y la app no lo estaba usando. Eso es estado de esta máquina, no comportamiento del producto.

---

## 3. Qué evidencia **no** sirve como prueba de calidad

- `docs/benchmark-llm-local-2026-10-12.md` y `docs/informe-tecnologia-y-llm-local.md` miden **corrección de transcripción** sobre 20 frases sintéticas en un solo equipo. Sirven para ordenar candidatos y comparar versiones, no para afirmar precisión general ni para otros equipos.
- `docs/arquitectura-runtime-llm-local.md` describe el ciclo de vida y qué medir; no aporta números de calidad.
- `docs/plan-finetune.md` propone un fine-tune del ASR; su techo de WER es del modelo base, no del LLM.
- Las cifras de WER de Parakeet (FLEURS/VoxPopuli en `.audit-eight/`, ignorado por Git) son del ASR, no de la corrección ni del producto final.
- Ninguna de estas mediciones dice nada sobre generar prompts a partir de voz.

---

## 4. Diferencias entre documentación y código

| Afirmación de la documentación | Estado en el código |
|---|---|
| El diccionario técnico corrige términos después del reconocimiento (`CHANGELOG.md:243-252`, `docs/precision.md`) | `bias.correct_biased()` no tiene llamador en producción (`bias.py:329`); solo se usa su glosario (`llm.py:348-352`) |
| "Dictado usa como texto final la corrección de Qwen" (`CHANGELOG.md:93-97`) | Cierto, y el candidato se usa sin validar orden de palabras (`llm.py:167-175`); la protección es la comprobación de significado (`llm.py:223-272`) |
| El pulido LLM está "apagado por defecto" (`CHANGELOG.md:778`) | El interruptor existe (`config.py:33`) pero sin endpoint configurado la función queda inactiva aunque el interruptor esté activo (`dictation.py:16`) |
| La cuantización del modelo local por defecto es Q4_K_M | Cierto (`config.py:317-318`, `THIRD-PARTY-NOTICES.txt:14-15`) |

---

## 5. Riesgos priorizados

1. **Función apagada sin aviso**: con el interruptor de revisión activo pero sin endpoint —o con un servidor que ignore `chat_template_kwargs`— el usuario cree tener revisión y no la tiene. No hay superficie que lo muestre.
2. **Truncado silencioso**: `finish_reason == "length"` se resuelve bien, pero sin decir nada.
3. **Verificación con presupuesto corto**: 2 s frente a un modelo frío desactiva la mejora en la práctica.
4. **Deriva de código muerto**: el corrector de diccionario técnico no corre; la documentación lo da por activo.
5. **Contexto de 4096 tokens sin control previo**: un dictado largo, con glosario grande, puede llegar al límite; el fallback es seguro pero ciego.
6. **Ausencia de `--reasoning off` en el arranque**: la garantía de "sin razonamiento" depende de que cada cliente envíe el campo.

---

## 6. Fuentes

**Modelo y runtime**

- Qwen3.5 4B (modelo base): <https://huggingface.co/Qwen/Qwen3.5-4B>
- Cuantización GGUF Q4_K_M usada: <https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF>
- Versión del runtime: <https://github.com/ggml-org/llama.cpp/releases/tag/b11573>
- API del servidor (campos como `chat_template_kwargs`, `response_format`): <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>

**Reconocimiento de voz**

- Parakeet TDT 0.6B v3: <https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3>
- Conversión int8 para sherpa-onnx: <https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8>
- Modelos offline de sherpa-onnx: <https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html>

**Documentos internos**

- `docs/arquitectura-runtime-llm-local.md`, `docs/informe-tecnologia-y-llm-local.md`, `docs/benchmark-llm-local-2026-10-12.md`, `docs/auditoria-ocho-mejoras.md`, `docs/precision.md`, `docs/plan-finetune.md`, `docs/BENCHMARK-MODELOS.md`, `docs/VOICE-TO-PROMPT.md`

**Herramientas de medición del repositorio**

- `dictado/bench/bench_llm.py` (corrección sintética), `dictado/bench/bench_llm_runtime.py` (variantes y cuantizaciones), `dictado/bench/bench_wer.py` (WER del ASR), `dictado/bench/bench_bias.py` (diccionario técnico)

## 7. Evidencia V2P posterior (separada del producto)

Voice-to-Prompt sigue sin implementación de producción. El producto conserva Qwen3.5 4B Q4_K_M y los flujos actuales de Parakeet/Kokoro; ninguna medición de esta sección alteró el servidor de Qwen, el pipeline de dictado ni la configuración.

- C: 10.368 filas, 1.461 variantes ciegas; los 24 candidatos `direct`/`structured` fallan el gate.
- D post hoc: 3.456 filas con sampler recomendado, 684 variantes ciegas; ninguna configuración supera el gate.
- E post hoc: 1.728 filas de `two_stage`, 2.595 llamadas; ninguna de las ocho configuraciones supera el gate y 861 finales están vacíos por extracciones fail-closed.
- F post hoc: Qwen completó 864 filas. Después del recorte, solo se completaron 72 filas B4 y 72 B9 sobre 12 casos `off`, `direct`/`two_stage`; B2 fue detenido y quedan artefactos parciales. El primer intento B4 produjo 24 rechazos de conexión (infraestructura, no modelo); un único reintento separado completó, con 6 fallos de extracción. B9 completó con 3 fallos de extracción. No se declara ganador.
- Hallazgo metodológico: las referencias congeladas son estáticas entre contextos; 14/72 casos `context_available_retrieved` (5), `history_relevant` (5) y `selection_authorized` (4) contienen en su gold datos ausentes cuando el modo es `off`. Esto sesga potencialmente calidad y checks automáticos en `off`; no permite afirmar que el contexto mejore causalmente. El subconjunto F excluyó sus tres casos afectados para una sensibilidad de nueve categorías. Ningún gold se cambió retroactivamente.
- La evidencia está en `dictado/bench/results/v2p-2026-10-16/` (salida generada, fuera del control de versiones); los resultados completos, las nueve respuestas a preguntas de diagnóstico y los límites están en `docs/BENCHMARK-MODELOS.md`. El corpus usa datos sintéticos, no material privado.
- Sin inter-rater humano; C/D/E usaron evaluación GPT-6 independiente y F una sesión independiente nueva. No se hizo smoke CPU tras el recorte, no hay medidor de potencia, y 27B no se ejecutó porque el backend upstream requerido carecía de sus kernels.
