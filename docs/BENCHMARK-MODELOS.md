# Benchmark de modelos para Voice-to-Prompt

**Fecha:** 16 de octubre de 2026 · **Estado:** C y D completos; E completa; F reducida por repriorización y puntuada como sensibilidad exploratoria. Ninguna ronda supera el gate; no hay ganador ni recomendación de cambio de producto.
**Alcance:** benchmark aislado de dictado→prompt en esta máquina, sus controles y sus limitaciones. Resultados y límites en [Resultados medidos](#resultados-medidos).

> C es la matriz congelada; D y E son comparaciones post hoc completas; F se redujo a una muestra emparejada. Ningún resultado autoriza una conclusión causal de calidad por contexto: el corpus usa un único gold por caso aunque `off` omita contexto que el gold presupone.

---

## 1. Alcance y estado

| Punto | Estado |
|---|---|
| Corpus de referencia | Congelado, 72 casos, hash en `dictado/bench/v2p_references.sha256` |
| Rubric de puntaje | Pre-registrado, hash en el mismo manifiesto |
| Arnés | Congelado en revisión C (`dictado/bench/bench_v2p.py`) |
| Corridas y puntaje | **Completos** para las 12 matrices revisión C; puntuación ciega 1.461/1.461 |
| Ganador C | **Sin ganador**: todos los candidatos fallan el gate estricto |
| Datos de usuario | Ninguno: el corpus es sintético y no hay audio, transcripciones ni rutas de perfil en los artefactos |

El producto no tiene hoy ninguna función de Voice-to-Prompt (ver `docs/VOICE-TO-PROMPT.md`). Este benchmark mide **prototipos aislados**, no comportamiento distribuido.

## 2. Metodología

**Unidad de análisis:** una respuesta del modelo para (caso del corpus × estrategia × modo de contexto × modelo), más su tiempo y sus tokens.

**Procedimiento por corrida**

1. Se verifica el manifiesto congelado (referencias, corpus, rubric y contrato de puntaje). Si algo cambió, la corrida **no arranca** (código 3).
2. Se audita que no haya fuga de gold: en el turno de usuario no puede aparecer ninguna frase de referencia que no provenga del dictado o del contexto autorizado.
3. Se ejecutan los casos, uno por uno, y se escribe una fila por caso en `rows.jsonl`.
4. Se agregan percentiles y conteos en `summary.json`, y se guarda la metadata de la corrida (runtime, opciones, hashes).
5. Se exportan filas ciegas para el puntaje cualitativo.

**Invariantes automáticos** (solo lo defendible, y solo como señal de triaje): anclas presentes, prohibidos ausentes, restricciones presentes, longitud razonable, salida no vacía. También se informa la coincidencia estricta de anclas (sensible a tildes) además de la plegada.

**Puntaje cualitativo:** revisión **ciega e independiente** sobre material que incluye entrada, **solo el contexto autorizado**, el prompt de referencia congelado y la respuesta, con identificadores aleatorios; el mapa a modelo/estrategia/contexto vive en un archivo aparte (`blinded_key.json`) y se abre recién después de puntuar. Aplican las siete dimensiones del rubric y las clases de error crítico.

**Gates (congelados, no se relajan después de ver salidas)**

- Cualquier **error crítico** rechaza la respuesta, sin importar el estilo.
- Un candidato es **viable** solo si no tiene errores críticos y las **siete** dimensiones quedan en ≥ 4/5.
- Si **ningún** candidato cumple todo, se informa *sin ganador* con los más cercanos y el gate que los descarta; no hay ganador por promedio ni por mayoría.

## 3. Diseño de la matriz

Tres dimensiones independientes, seleccionables por separado en la línea de comandos:

| Dimensión | Valores | Qué mide |
|---|---|---|
| Modelo | Id de modelo que conoce el servidor (se pinnea por corrida) | Efecto del modelo |
| Instrucción | `baseline`, `legacy_instruction`, `direct`, `structured` | Efecto del diseño de la instrucción/pipeline |
| Contexto | Medidos: `off`, `all`, `oracle_relevant`. Seleccionables fuera de la matriz medida: `glossary`, `provided`, `history`, `selection` | Efecto de la selección de contexto |

**Las estrategias**

| Estrategia | Qué es | Qué se espera de ella |
|---|---|---|
| `baseline` | Réplica exacta del camino de producción: corrección local → corrección con LLM (incluye **siempre** su glosario, como producción) → comprobación de significado | Devuelve una corrección del dictado, **no** un prompt: es la referencia honesta, no un competidor de V2P |
| `legacy_instruction` | Control negativo del factor instrucción: la instrucción de corrección de la app, textual, con el mismo transporte y el mismo envoltorio de contexto que `direct` | Si un prompt rediseñado no le gana, la mejora no viene de la instrucción. **No** es un prompt histórico de V2P (no existe ninguno) |
| `direct` | Una llamada con una instrucción rediseñada de "convertí el dictado en un prompt" | Transformación libre |
| `structured` | Una llamada restringida por esquema JSON + **renderizador determinista** en código (sin segunda llamada al modelo) | Deja el formato en manos auditables |
| `two_stage` | Dos llamadas reales: extracción JSON validada (intención, hechos explícitos literales del dictado, restricciones/negaciones, hechos de contexto literal con procedencia y desconocidos), seguida de generación con esa estructura, el dictado original y exactamente el contexto autorizado | Brazo exploratorio post hoc; extracción inválida/truncada o generación incompleta/no-stop cierra sin prompt final |

`two_stage` es una estrategia nueva y aislada del arnés, **post hoc**: no pertenece a C ni D. E la midió en matriz completa; F la comparó con `direct` en el subconjunto emparejado descrito en §9.4. En esta estrategia la salida ciega es únicamente el texto de la segunda llamada; el registro conserva ambas llamadas, telemetría y errores.

**Los modos de contexto** (el arnés ofrece siete; la matriz medida usa tres)

- `off` **(medido)** — el modelo recibe solo el dictado; sirve como control sin inyección de contexto.
- `oracle_relevant` **(medido)** — inyecta solo el contexto que el corpus marca relevante, ningún glosario. Es cota superior, no comportamiento de producto.
- `all` **(medido)** — **estrés de contaminación en todos los modelos medidos**: inyecta todo, incluido historial no relacionado y el glosario. Se conserva además la evidencia específica de Qwen en `off`/`all` que ya existe como replicados de estrés.
- `glossary`, `provided`, `history` y `selection` — **seleccionables, fuera de la matriz medida**: agregan glosario, contexto aportado, turnos previos o texto seleccionado autorizado. Sirven para diagnósticos puntuales y no se informan como resultados.

**Limitación de referencia transversal.** Aunque la entrada enviada en `off` y `oracle_relevant` difiere solo por el contexto, el corpus tiene un único gold estático por caso. En cada condición `off`, 14/72 filas por repetición exigen información no disponible: `context_available_retrieved` (5), `history_relevant` (5) y `selection_authorized` (4). Por eso diferencias de puntajes cualitativos y checks automáticos por contexto no permiten atribución causal y pueden penalizar artificialmente `off`. Los deltas son descriptivos y potencialmente sesgados; no se editó el corpus ni se repitió matriz alguna.

**Repeticiones y tamaño:** 3 repeticiones por celda. La matriz medida usa **3 de los 7 modos** (`off`, `all`, `oracle_relevant`) con las **4 estrategias** y los **72 casos**: 4 × 3 × 72 = **864 filas por modelo por repetición**. Corren esa matriz completa Qwen y cada modelo de comparación 2B, 4B y 9B; cada uno se identifica por su id y el hash de su archivo en la corrida.

**Modos de ejecución sin modelo:** `--dry-run` valida congelado, fuga de gold y autoconsistencia del propio corpus (sin red); `--fixture` hace el humo de plomería contra un servidor simulado y marca `scored: false`.

## 4. Hardware, backend, modelo y cuantización

### 4.1 Hardware observado (una máquina)

| Recurso | Observado |
|---|---|
| CPU | AMD Ryzen 5 7600, 6 núcleos / 12 lógicos |
| Memoria | 15,1 GiB |
| GPU del runtime | AMD Radeon RX 6800 XT, 16 GB (`Vulkan0`: 16.368 MiB, 15.569 MiB libres al medir) |
| GPU secundaria | Radeon integrada (`Vulkan1`: 7.988 MiB, 7.589 MiB libres) |

Un solo equipo. Los resultados **no** se extrapolan a otros modelos de GPU, a CPU sin aceleración ni a equipos con menos memoria.

### 4.2 Backend

| Dato | Valor |
|---|---|
| Runtime | `llama-server` build 11573 (`0.6.0-dev`, commit `0f701c882`, Clang 20.1.8, Windows x86_64) |
| Arranque del servidor de referencia | `--ctx-size 4096 --parallel 1 --threads max(1, min(8, cpu//2))`, `--no-webui --log-disable`, GPU `--device <dev> --n-gpu-layers 99` o CPU `--device none --n-gpu-layers 0`, `--alias <modelo>`, y `--sleep-idle-seconds` si el binario lo soporta. Es el mismo conjunto que arma el producto (`llm_runtime.py:284-304`) |
| Modo directo | `chat_template_kwargs: {"enable_thinking": false}` por pedido |
| `--jinja` | Habilitado por defecto en este build |
| `--reasoning`, `--reasoning-format` | Disponibles, por defecto `auto`; el producto y el arnés **no** los usan |
| Capacidades que reporta el servidor | `supports_reasoning_effort: false`, `supports_preserve_reasoning: false`, `supports_system_role: true`, `supports_typed_content: true` |

Compatibilidad con cualquier endpoint compatible con OpenAI: el arnés solo usa `POST /v1/chat/completions` (más `GET /props` y `GET /v1/models` para la metadata, que son opcionales). **Salvedad verificada:** si un servidor ignora `chat_template_kwargs`, el modelo razona; el arnés lo registra (`reasoning_present`) y las salidas quedan vacías o truncadas, que es exactamente lo que el rubric debe castigar. La salida restringida por esquema necesita `response_format` de tipo `json_schema`; hay un modo alternativo `prompt-json` para servidores que no lo soportan.

### 4.3 Modelo y cuantización

| Dato | Valor | Fuente |
|---|---|---|
| Modelo de referencia | Qwen3.5 4B | `THIRD-PARTY-NOTICES.txt:12-15` |
| Cuantización | Q4_K_M (GGUF; el runtime reporta `Q4_K - Medium`) | idem |
| Archivo | `Qwen_Qwen3.5-4B-Q4_K_M.gguf`, 3.013.027.808 bytes | observado en disco |
| Arquitectura declarada en el GGUF | `qwen35`, nombre `Qwen3.5 4B` | observado |
| Id hacia la API | `qwen3.5:4b-q4_K_M` | observado |
| Contexto efectivo | 4096 tokens, 1 ranura | observado en `/props` |
| Licencia declarada | Apache-2.0 | `THIRD-PARTY-NOTICES.txt:14` |

**Candidatos de cuantización/tamaño que el repositorio ya contempla** (`dictado/bench/bench_llm_runtime.py`): Qwen3.5 2B Q4_K_M, 2B Q8_0, 4B Q4_K_M, 4B Q8_0 y Llama 3.2 3B Instruct Q4_K_M. Son opciones de comparación por archivo GGUF y **no** se corrieron en este benchmark. El plan de corrida incluye, además del modelo de referencia, los modelos de comparación de 2B, 4B y 9B; cada uno se identifica por su id, el archivo y su hash en la corrida —este documento no afirma de antemano familia ni cuantización de esos archivos.

**Relación pedido-servidor (auditable):** el arnés solo fija `temperature`, `max_tokens`, `seed` y `stream`, más `chat_template_kwargs` (que no es un muestreador sino el modo directo); el resto de los muestreadores (`top_p`, `top_k`, `min_p`, `repeat_penalty`, `presence_penalty`, `frequency_penalty`, `typical_p`, `n_predict`) provienen de los valores por defecto del servidor, que la metadata guarda como `runtime.summary.generation_defaults`, junto con `n_ctx` y `n_parallel`/`total_slots`. No se guarda el payload crudo de `/props`: ni plantilla de chat, ni rutas absolutas.

## 5. Corpus y rubric congelados

| Artefacto | Rol | SHA-256 |
|---|---|---|
| `dictado/bench/v2p_cases.json` | 72 casos sintéticos con prompt de referencia escrito a mano | archivo `7d81ef200386766e55a8e43882d319c71dd56eb2c03ce0ff51ab1a64cddf4d99` |
| — proyección de referencias | Solo campos de gold, canónico | `references_sha256 352a1931c46db4bf134a42487fb977065b112ae2698e5c35fa675c6d0ec194db` |
| `dictado/bench/v2p_rubric.json` | Rubric pre-registrado (versión 2026-10-16.2) | `rubric_sha256 6fda42869c6cddfd32330bb3e46a058aa207de5ac6d0f36559c08564f00cf750` |
| — contrato de puntaje | Errores críticos, dimensiones y gates | `prescoring_sha256 43f7dd03ac206b92db922e7825a3f900cf9574a0d58a52ce1a7711126ed12bcc` |

**Cobertura (72 casos):** pedidos ordinarios (8), ambiguos (6), con muletillas (7), largos (6), con múltiples condiciones (9), con contexto disponible explícito (6) y recuperado (5), con contexto ausente (7), con historial relevante (5) e irrelevante (5), con selección autorizada (4) y con selección no autorizada (4).

**Clases de error crítico:** cambio de intención; negación o restricción alterada; condición perdida o alterada; dato inventado; contexto no autorizado o irrelevante; nombre, cifra o ruta equivocados.

**Dimensiones 1-5:** fidelidad, intención, disciplina de contexto, restricciones, claridad, concisión y robustez, cada una con anclas escritas para 1, 3 y 5.

**Reglas de aislamiento del corpus:** el gold nunca se envía al modelo; la selección no autorizada no se inyecta en ningún modo y su contenido no puede aparecer en la salida; el prompt de referencia se verifica contra sus propios invariantes antes de puntuar; el rubric y el corpus están congelados por hash y no se relajan después de ver salidas.

## 6. Qué se reporta y qué no se puede observar

| Métrica | Disponibilidad |
|---|---|
| Texto de la respuesta por caso | Sí, en `rows.jsonl` |
| Tokens de entrada y de salida | Sí para `direct`, `structured` y `legacy_instruction` (respuesta completa de la API) |
| TTFT y tiempo total | Sí para las mismas tres (hay modo streaming) |
| Tokens por segundo | Sí: se define como tokens generados sobre la **fase de generación** (`tiempo del modelo - TTFT`, o el tiempo del modelo si no hay TTFT) |
| Percentiles p50/p95 | Sí, por corrida |
| Motivo de fin (`stop`/`length`) y errores | Sí, por fila |
| Tokens y TTFT del **baseline** | **No disponibles**: el camino de producción devuelve solo texto, así que el arnés los marca como no disponibles en vez de estimarlos. La fila también deja explícito ese motivo |
| Puntaje cualitativo | C, D y E completos; F evaluada de forma ciega en un solo lote de 72 variantes, con sensibilidad de 9 categorías; cobertura exacta y reportes en §9 |

## 7. Reproducibilidad y trazabilidad

- **Arnés congelado (revisión C):** `dictado/bench/bench_v2p.py`, SHA-256 `1ed9e87a4d4e622ca18773bfa913bd892d0417744dfe04fdcf9b354225fba875`, registrado en cada corrida como `runner.sha256`. Cualquier corrida futura debe citar el hash con el que se ejecutó.
- *Historial de revisiones (para no mezclar evidencia):* revisión A = primera versión del arnés (columnas de tokens por segundo nulas y auditoría de fuga de gold tautológica, es decir, sin valor probatorio); revisión B = correcciones de tokens/segundo, privacidad de rutas, hash del arnés y modos `oracle_relevant`/`legacy_instruction`; revisión C = glosario del baseline siempre presente (como producción), proyección de los valores por defecto del servidor y pruebas del arnés. Para `direct`, `structured` y `legacy_instruction` los pedidos que ve el modelo son idénticos entre revisiones en `off`/`all`. Para `baseline` **no**: la revisión C ahora envía siempre el glosario de producción, así que sus celdas de `off`, `oracle_relevant`, `provided`, `history` y `selection` cambian respecto de A y B (en `all` y `glossary` ya lo enviaba). Por eso **toda** fila de `baseline` anterior a la revisión C queda superada.
- *Replicados de estrés de Qwen `off`/`all` (revisión A e intermedia):* son 2 corridas de revisión A y 1 de una revisión intermedia sin hash recuperable. Se conservan por pedido explícito, pero **no** pueden presentarse como tres repeticiones limpias. Hashes de su `metadata.json` ya sanitizado: `dbef9efe…` (rep1), `3dd3e14d…` (rep2), `850afb01…` (rep3). Sus filas de tokens por segundo deben recalcularse aparte.
- **Aislamiento del entorno:** las corridas puntuadas usan solo texto sintético del corpus, escriben en un directorio local y no envían audio ni texto de usuario.

## 8. Limitaciones y advertencias

1. **Una máquina, un backend, una cuantización de referencia.** El equipo (Ryzen 5 7600, 15,1 GiB, RX 6800 XT por Vulkan) no representa CPU sin aceleración, GPU de gama media ni equipos con poca memoria.
2. **Corpus sintético, sin audio.** Mide el paso dictado→prompt, no la precisión del reconocimiento ni la voz espontánea.
3. **`oracle_relevant` es una cota superior.** No existe un recuperador de contexto de producción en este trabajo; comparar contra esta cota no autoriza ninguna afirmación sobre un recuperador real.
4. **`baseline` es un control de corrección de dictado, no un candidato de Voice-to-Prompt ni elegible a ganador.** Se conserva como referencia del pipeline. No tiene un rubric relajado: el baseline no puede alterar las dimensiones o gates congelados ni rebajar el requisito de calidad de prompt para los candidatos V2P.
5. **Los checks automáticos no son un juez.** Las invariantes sirven solo para triage. La revisión ciega C ya se puntuó con un evaluador LLM separado (sin juez humano); sus resultados y límites están en §9. No se infiere calidad a partir de las métricas de ejecución.
6. **Los motivos de fin y los tiempos dependen del servidor.** Un servidor lento, en reposo o que razona cambia los tiempos y puede vaciar la respuesta; eso se reporta, no se compensa.
7. **Nada de esto es una recomendación de producto.** La decisión final exige cumplir el gate completo; si nadie lo cumple, la recomendación es seguir midiendo.
8. **No se completó compatibilidad CPU.** Por el recorte explícito no se ejecutó el smoke CPU; no hay medición de potencia eléctrica ni extrapolación CPU/GPU. Bonsai 27B se excluyó porque el upstream probado carecía de los kernels de transform requeridos.

## 9. Resultados medidos

**Estado:** C (temperatura 0), D (sampler recomendado post hoc) y E (`two_stage`, post hoc) están completos y puntuados. F terminó solo en alcance reducido: Qwen conservó la matriz completa; de Bonsai 4B/9B se midieron 12 casos emparejados, contexto `off`, `direct` y `two_stage`; Bonsai 2B se canceló y solo hay artefactos parciales. Ningún conjunto da un ganador; el problema de referencias estático limita las conclusiones de calidad por contexto en todas las rondas.

### Diseño ejecutado y trazabilidad

Se midió la matriz completa: **4 modelos × 4 estrategias × 3 contextos × 72 casos × 3 repeticiones = 10.368 filas**, 864 por repetición-modelo y 2.592 por modelo. Los modelos son Qwen3.5 4B Q4_K_M y Bonsai 2 2B, 4B y 9B Q2_0; estrategias `baseline`, `legacy_instruction`, `direct` y `structured`; contextos `off`, `all` y `oracle_relevant`. Las 12 matrices finales (cuatro modelos, `rep1`–`rep3`) tienen **cero errores del runner**, 12/12 condiciones y 72 casos únicos por condición; los hashes congelados de corpus, referencias, rubric y contrato de puntaje coinciden en las cuatro matrices.

| Artefacto congelado | SHA-256 |
|---|---|
| Corpus `dictado/bench/v2p_cases.json` | `7d81ef200386766e55a8e43882d319c71dd56eb2c03ce0ff51ab1a64cddf4d99` |
| Referencias `dictado/bench/v2p_references.sha256` | `352a1931c46db4bf134a42487fb977065b112ae2698e5c35fa675c6d0ec194db` |
| Rubric `dictado/bench/v2p_rubric.json` | `6fda42869c6cddfd32330bb3e46a058aa207de5ac6d0f36559c08564f00cf750` |
| Contrato de puntaje | `43f7dd03ac206b92db922e7825a3f900cf9574a0d58a52ce1a7711126ed12bcc` |
| Arnés `dictado/bench/bench_v2p.py` | `1ed9e87a4d4e622ca18773bfa913bd892d0417744dfe04fdcf9b354225fba875` |

### Archivos y entornos medidos

Los tamaños y SHA-256 son los registrados en `dictado/bench/results/v2p-2026-10-16/model-artifacts.json`; los ids de API, ficheros y hashes quedan también en metadata por réplica.

| Modelo | Cuantización / archivo | Bytes | SHA-256 del archivo |
|---|---|---:|---|
| Qwen3.5 4B | Q4_K_M · `Qwen_Qwen3.5-4B-Q4_K_M.gguf` | 3.013.027.808 | `13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983` |
| Bonsai 2 2B | Q2_0 · `Ternary-Bonsai-2-2B-Q2_0.gguf` | 546.689.504 | `c2893fa6f951daee8b7263d5f49b455e57989ef56417a22125b03566dc560650` |
| Bonsai 2 4B | Q2_0 · `Ternary-Bonsai-2-4B-Q2_0.gguf` | 1.212.103.520 | `64a5183b44921bdb5ac4a6f5f92007821d6ebe5c66997d3c147fd214ab9433d4` |
| Bonsai 2 9B | Q2_0 · `Ternary-Bonsai-2-9B-Q2_0.gguf` | 2.556.640.128 | `1f884aa56aacd2bbbb2e4b7abaead2b1102b74a9c75a23fc33dab916813a384d` |

Qwen se ejecutó con el servidor de producto **llama.cpp b11573-0f701c882**, puerto 8080, sin modificar el servidor ni su puerto. Los tres Bonsai se ejecutaron con upstream **llama.cpp b11580-4413a1924**, en el puerto 8081. Los cuatro usaron `--ctx-size 4096`, una ranura/paralelismo, streaming y el dispositivo Vulkan del equipo (`Vulkan0`, 99 capas GPU). Contrástese como una comparación de despliegues, no un efecto puro de pesos.

**Muestreo.** El arnés fijó en cada pedido `temperature=0`, `max_tokens=512`, `seed=0`, `stream=true`; `direct` y `structured` también enviaron `chat_template_kwargs={"enable_thinking": false}`, y `structured` usó `jsonschema`. Los otros valores provinieron de los defaults observados del servidor; son distintos entre builds y se mantuvieron sin override:

| Servidor | top_p | top_k | min_p | repeat_penalty | presence_penalty | frequency_penalty | typical_p | n_predict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Qwen b11573 | 0,95 | 40 | 0,05 | 1,0 | 0,0 | 0,0 | 1,0 | -1 |
| Bonsai b11580 | 0,95 | 20 | 0,0 | 1,0 | 0,0 | 0,0 | 1,0 | -1 |

No se hizo override de esos ocho parámetros. En baseline no están disponibles tokens/TTFT/TPS porque la ruta medida devuelve texto sin telemetría de tokens; se reportan como N/A, no como cero.

**Memoria y carga observadas.** Evidencia en `bonsai2-{2b,4b,9b}-q2-0-revC/{resources.jsonl,load_time.txt}`: máximos muestreados de `llama-server` (no estimaciones de requerimiento) fueron aproximadamente: 2B, 1.698 MiB de working set y 755 MB de VRAM dedicada del proceso; 4B, 3.249 MiB de working set y 1.568 MB de VRAM dedicada; 9B, 4.766 MiB de working set y 2.739 MB de VRAM dedicada. El máximo de RAM privada observado fue 2.301 MiB (2B), 3.959 MiB (4B) y 5.440 MiB (9B); el mínimo de RAM libre del host registrado fue 2.174, 2.981 y 2.319 MiB respectivamente. Los registros de carga indican `scored_ready_seconds=1` para 2B y `0` para 4B/9B (resolución gruesa del capturador; no equivale a medir latencia de lectura/carga). El snapshot pasivo de Qwen (`qwen-product-passive-snapshot.jsonl`) capturó el servidor durmiendo: no sirve para afirmar memoria del modelo cargado ni del KV; **ambos valores de Qwen quedan no disponibles**. Los artefactos no proporcionan una medida separada y fiable de bytes de KV-cache para Bonsai; solo queda documentado el contexto configurado de 4096 tokens y una ranura. No imputamos KV a partir de VRAM total.

### Resultados de ejecución (estadística descriptiva y triage automático)

La tabla siguiente debe leerse como estadística descriptiva y señales de triaje, no como puntuación cualitativa ni ranking. Cada condición agrega sus tres repeticiones de 72 respuestas; tiempos en segundos, TPS en tokens/s. `wall` es tiempo total, TTFT y TPS son N/A para baseline. Las invariantes automáticas son anclas, restricciones, prohibidos, longitud razonable y salida segura/desconocidos, cuando aplican; los controles (`legacy_instruction`) no tienen esas comprobaciones de calidad. Salidas vacías o truncadas permanecen en las filas y se cuentan como fallos de invariantes cuando corresponde.

El **informe canónico** `dictado/bench/results/v2p-2026-10-16/aggregate-v2p-report.json` valida cuatro modelos × 12 condiciones: cada uno tiene 3 repeticiones, 72 casos únicos por condición, hashes congelados correctos, conjunto completo de condiciones, arnés uniforme, ningún error de validación ni hueco de metadata, cero errores de runner y cero fugas de gold. Las 48 condiciones tienen 72/72 casos estables en texto exacto entre sus repeticiones. El puntaje ciego de revisión C se informa más abajo; el informe de ejecución no es un veredicto de calidad.

Cada fila de las tablas corresponde a 72 casos por repetición y agrega tres repeticiones. `wall`, `TTFT` y `TPS` muestran el valor mediano entre las tres repeticiones de sus respectivos p50/p95 (segundos y tokens/s). `A/P/R/L` son checks automáticos de anclas presentes, prohibidos ausentes, restricciones presentes y longitud válida; `JSON` es campos estructurados completos, `vacías` es recuento agregado de respuestas vacías en las 216 filas de esa condición. Porcentajes = medianas del check entre repeticiones; `—` = no aplica. Los errores de runner son cero en las 48 condiciones; todos los checks son **triage**, no juicio de calidad.

#### Qwen3.5 4B Q4_K_M

| Condición | A/P/R/L % | JSON % | Vacías /216 | wall p50/p95 s | TTFT p50/p95 s | TPS p50/p95 |
|---|---:|---:|---:|---:|---:|---:|
| baseline · all | — | — | — | 0.375 / 0.899 | — | — |
| baseline · off | — | — | — | 0.377 / 0.911 | — | — |
| baseline · oracle_relevant | — | — | — | 0.375 / 0.904 | — | — |
| direct · all | 89/100/93/89 | — | 0 | 0.304 / 2.622 | 0.111 / 0.151 | 117.3 / 125.3 |
| direct · off | 72/100/93/83 | — | 0 | 0.291 / 2.913 | 0.107 / 0.140 | 117.2 / 126.1 |
| direct · oracle_relevant | 90/100/93/90 | — | 0 | 0.317 / 1.563 | 0.110 / 0.149 | 116.2 / 125.0 |
| legacy_instruction · all | — | — | — | 0.249 / 0.677 | 0.114 / 0.246 | 116.3 / 123.9 |
| legacy_instruction · off | — | — | — | 0.223 / 0.613 | 0.106 / 0.137 | 116.7 / 122.1 |
| legacy_instruction · oracle_relevant | — | — | — | 0.245 / 0.602 | 0.111 / 0.155 | 118.0 / 123.2 |
| structured · all | 96/89/97/50 | 100 | 0 | 2.024 / 3.185 | 0.135 / 0.177 | 95.4 / 102.4 |
| structured · off | 76/96/97/65 | 100 | 0 | 1.992 / 3.189 | 0.132 / 0.181 | 94.3 / 101.0 |
| structured · oracle_relevant | 96/96/97/57 | 100 | 0 | 2.028 / 3.328 | 0.136 / 0.175 | 94.1 / 99.4 |

#### Bonsai 2 2B Q2_0

| Condición | A/P/R/L % | JSON % | Vacías /216 | wall p50/p95 s | TTFT p50/p95 s | TPS p50/p95 |
|---|---:|---:|---:|---:|---:|---:|
| baseline · all | — | — | — | 0.307 / 0.569 | — | — |
| baseline · off | — | — | — | 0.310 / 0.572 | — | — |
| baseline · oracle_relevant | — | — | — | 0.309 / 0.569 | — | — |
| direct · all | 71/90/85/54 | — | 0 | 0.294 / 1.343 | 0.065 / 0.085 | 243.6 / 252.0 |
| direct · off | 54/97/88/49 | — | 0 | 0.391 / 2.186 | 0.065 / 0.088 | 244.2 / 254.2 |
| direct · oracle_relevant | 72/97/88/53 | — | 0 | 0.344 / 2.178 | 0.066 / 0.084 | 245.0 / 253.8 |
| legacy_instruction · all | — | — | — | 0.216 / 0.998 | 0.069 / 0.086 | 243.6 / 257.3 |
| legacy_instruction · off | — | — | — | 0.342 / 1.801 | 0.064 / 0.080 | 243.7 / 259.0 |
| legacy_instruction · oracle_relevant | — | — | — | 0.221 / 1.227 | 0.066 / 0.084 | 243.7 / 257.1 |
| structured · all | 68/88/83/79 | 100 | 0 | 1.041 / 1.560 | 0.069 / 0.095 | 159.3 / 184.0 |
| structured · off | 50/92/83/67 | 100 | 0 | 1.059 / 1.617 | 0.068 / 0.099 | 151.3 / 185.7 |
| structured · oracle_relevant | 68/94/83/74 | 100 | 0 | 1.049 / 1.540 | 0.068 / 0.101 | 156.0 / 182.7 |

#### Bonsai 2 4B Q2_0

| Condición | A/P/R/L % | JSON % | Vacías /216 | wall p50/p95 s | TTFT p50/p95 s | TPS p50/p95 |
|---|---:|---:|---:|---:|---:|---:|
| baseline · all | — | — | — | 0.332 / 0.850 | — | — |
| baseline · off | — | — | — | 0.330 / 0.888 | — | — |
| baseline · oracle_relevant | — | — | — | 0.331 / 0.847 | — | — |
| direct · all | 71/99/96/90 | — | 0 | 0.206 / 0.522 | 0.109 / 0.145 | 155.8 / 162.5 |
| direct · off | 72/99/97/93 | — | 0 | 0.202 / 0.524 | 0.105 / 0.134 | 155.0 / 162.4 |
| direct · oracle_relevant | 75/99/97/96 | — | 0 | 0.206 / 0.510 | 0.107 / 0.144 | 155.0 / 162.8 |
| legacy_instruction · all | — | — | — | 0.216 / 1.152 | 0.109 / 0.148 | 151.5 / 161.5 |
| legacy_instruction · off | — | — | — | 0.191 / 0.568 | 0.104 / 0.137 | 156.2 / 164.9 |
| legacy_instruction · oracle_relevant | — | — | — | 0.207 / 0.563 | 0.108 / 0.145 | 155.0 / 164.5 |
| structured · all | 94/86/97/8 | 98.6 | 3 | 2.060 / 2.816 | 0.110 / 0.143 | 125.6 / 132.5 |
| structured · off | 75/93/97/3 | 97.2 | 6 | 2.072 / 3.442 | 0.108 / 0.146 | 124.6 / 132.0 |
| structured · oracle_relevant | 94/93/97/8 | 98.6 | 3 | 2.114 / 2.988 | 0.110 / 0.149 | 120.8 / 129.7 |

#### Bonsai 2 9B Q2_0

| Condición | A/P/R/L % | JSON % | Vacías /216 | wall p50/p95 s | TTFT p50/p95 s | TPS p50/p95 |
|---|---:|---:|---:|---:|---:|---:|
| baseline · all | — | — | — | 0.487 / 1.247 | — | — |
| baseline · off | — | — | — | 0.484 / 1.339 | — | — |
| baseline · oracle_relevant | — | — | — | 0.483 / 1.261 | — | — |
| direct · all | 72/97/90/89 | — | 0 | 0.372 / 3.315 | 0.153 / 0.232 | 81.7 / 87.0 |
| direct · off | 69/94/92/81 | — | 0 | 0.325 / 3.271 | 0.148 / 0.217 | 81.7 / 87.1 |
| direct · oracle_relevant | 78/97/92/89 | — | 0 | 0.365 / 3.261 | 0.151 / 0.231 | 81.4 / 86.3 |
| legacy_instruction · all | — | — | — | 0.361 / 0.996 | 0.153 / 0.231 | 80.4 / 84.9 |
| legacy_instruction · off | — | — | — | 0.320 / 0.983 | 0.148 / 0.221 | 81.8 / 85.1 |
| legacy_instruction · oracle_relevant | — | — | — | 0.356 / 0.983 | 0.152 / 0.229 | 81.4 / 84.9 |
| structured · all | 93/94/96/56 | 97.2 | 6 | 2.552 / 4.461 | 0.171 / 0.229 | 69.0 / 73.1 |
| structured · off | 74/97/96/56 | 95.8 | 9 | 2.530 / 5.333 | 0.167 / 0.209 | 68.1 / 73.3 |
| structured · oracle_relevant | 93/97/96/56 | 97.2 | 6 | 2.519 / 4.479 | 0.167 / 0.227 | 68.7 / 73.1 |

Los registros de `rows.jsonl` confirman, agregados sobre `rep1`–`rep3`, estas respuestas `structured` vacías por condición: 4B `off` 6/216, `oracle_relevant` 3/216, `all` 3/216 (**12 total**); 9B `off` 9/216, `oracle_relevant` 6/216, `all` 6/216 (**21 total**). Cada una de esas filas tiene `finish_reasons=["length"]` y `render_error` de tipo `ValueError`; no son errores del runner. Qwen y 2B no tienen salidas vacías. Las respuestas siguen en el paquete y no se rellenan ni corrigen. También hubo `finish_reasons=["length"]` en condiciones `direct`: 2B `off` y `oracle_relevant`, 15 cada una; 9B, 9 en cada una. Con `max_tokens=512`, esos finales se reportan como señal de límite/truncamiento, no como una clasificación humana; `length_ok` y `structured_fields_filled` solo describen longitud/forma.

En `v2p-summary.json` cada condición tiene `n=216` y errores cero, que coincide con las 72 filas × 3 repeticiones validadas por el agregador; las métricas automáticas compartidas coinciden tras redondeo. El resumen previo combina las 216 filas de cada condición en un único percentil, mientras el informe canónico calcula percentiles por repetición y presenta la mediana de esos tres percentiles. Por ello algunos tiempos cambian, no los datos ni la cobertura: p. ej., Bonsai 2B `direct|off` wall p50 es 0,391 s canónico frente a 0,406 s en el resumen previo. El informe canónico añade estabilidad exacta, finales, fugas de gold, vacíos y completitud JSON. El campo `unknown_safe` del resumen previo coincide con `forbidden_absent` en sus checks; el informe final expresa la auditoría de gold con cero fugas para todas las condiciones. `aggregate-v2p-report.json` es la fuente final de las tablas de resultados.

Las tasas automáticas (incluidas salidas vacías, longitud y campos estructurados) son **solo triage** y no deciden calidad. C, D y E tienen puntaje ciego completo; F tiene un puntaje exploratorio y la sensibilidad 9-categorías excluye los tres casos con dependencia de contexto ausente. Los resultados detallados siguen abajo.

**Bonsai 2 27B está excluido y es opcional**, no una matriz incompleta: esta corrida usa upstream llama.cpp, mientras la guía de soporte del proyecto Bonsai señala que 27B requiere transforms Hadamard/FWHT, sign-flip y graph-transform que upstream no implementa; el GGUF puede aparentar cargar sin ejecutar esas transformaciones correctamente. No era válido medirlo con ese backend.

### Puntaje cualitativo ciego — revisión C

El informe canónico `dictado/bench/results/v2p-2026-10-16/aggregate-v2p-scored-report.json` clasifica 24 configuraciones elegibles (`direct` y `structured` × cuatro modelos × tres contextos), cada una con 216 observaciones puntuadas. `baseline` y `legacy_instruction` son controles no elegibles. No hay ganador: **las 24 configuraciones fallan el gate estricto**. `full gate` en la tabla es la proporción de respuestas individuales sin error crítico y con las siete dimensiones ≥4; una configuración solo es viable si además no tiene ningún error crítico y todas las filas cumplen el gate.

Revisión hecha con `openai-codex/gpt-6-luna:high`, en 15 lotes ciegos disjuntos; no hubo evaluador humano. Cobertura exacta: **1.461/1.461** objetos, sin faltantes ni duplicados. Antes de revelar la clave C se corrigieron dos typos de `row_id`, usando la alineación probada del orden de origen; no se cambiaron calificaciones, códigos críticos ni notas. La mejor tasa de filas que pasa el gate es Qwen `direct|all` (**61,11%**), pero su tasa de errores críticos es **15,28%**, así que falla el gate estricto. El reporte canónico publica medianas por dimensión, no medias; no se derivan medias no disponibles.

Las medianas siguen el orden **Fid. / Int. / Disc. contexto / Restr. / Clar. / Conc. / Robustez**. Las latencias wall p50/p95 (segundos) son las medianas de los percentiles por repetición del informe de ejecución canónico.

| Modelo | Estrategia | Contexto | Error crítico % | Full gate % | Medianas (7 dimensiones) | Wall p50/p95 s |
|---|---|---|---:|---:|---|---:|
| Qwen 4B Q4_K_M | direct | all | 15,28 | 61,11 | 5 / 5 / 5 / 5 / 5 / 5 / 4 | 0,304 / 2,622 |
| Qwen 4B Q4_K_M | direct | off | 25,00 | 47,22 | 4 / 4 / 5 / 5 / 4 / 5 / 3 | 0,291 / 2,913 |
| Qwen 4B Q4_K_M | direct | oracle_relevant | 20,83 | 56,94 | 5 / 5 / 5 / 5 / 5 / 5 / 4 | 0,317 / 1,563 |
| Qwen 4B Q4_K_M | structured | all | 34,72 | 12,50 | 3 / 4 / 4 / 4 / 3 / 2,5 / 3 | 2,024 / 3,185 |
| Qwen 4B Q4_K_M | structured | off | 29,17 | 11,11 | 3 / 4 / 4 / 4 / 3 / 3 / 3 | 1,992 / 3,189 |
| Qwen 4B Q4_K_M | structured | oracle_relevant | 27,78 | 13,89 | 3 / 4 / 4 / 4 / 3 / 3 / 3 | 2,028 / 3,328 |
| Bonsai 2 2B Q2_0 | direct | all | 59,72 | 8,33 | 2 / 2 / 4 / 3 / 3 / 2,5 / 2 | 0,294 / 1,343 |
| Bonsai 2 2B Q2_0 | direct | off | 62,50 | 11,11 | 2 / 3 / 3 / 3 / 3 / 2 / 2 | 0,391 / 2,186 |
| Bonsai 2 2B Q2_0 | direct | oracle_relevant | 54,17 | 12,50 | 2 / 3 / 4 / 3 / 3 / 2 / 2 | 0,344 / 2,178 |
| Bonsai 2 2B Q2_0 | structured | all | 61,11 | 11,11 | 2 / 3 / 3 / 2 / 3 / 2 / 2 | 1,041 / 1,560 |
| Bonsai 2 2B Q2_0 | structured | off | 77,78 | 4,17 | 2 / 2,5 / 2 / 2 / 2 / 2 / 2 | 1,059 / 1,617 |
| Bonsai 2 2B Q2_0 | structured | oracle_relevant | 61,11 | 11,11 | 2 / 3 / 3 / 2 / 3 / 2 / 2 | 1,049 / 1,540 |
| Bonsai 2 4B Q2_0 | direct | all | 11,11 | 37,50 | 4 / 4 / 5 / 5 / 4 / 5 / 3 | 0,206 / 0,522 |
| Bonsai 2 4B Q2_0 | direct | off | 23,61 | 40,28 | 4 / 4 / 5 / 5 / 4 / 5 / 3 | 0,202 / 0,524 |
| Bonsai 2 4B Q2_0 | direct | oracle_relevant | 16,67 | 40,28 | 4 / 4 / 5 / 5 / 4 / 5 / 3 | 0,206 / 0,510 |
| Bonsai 2 4B Q2_0 | structured | all | 61,11 | 5,56 | 2 / 3 / 3 / 3 / 3 / 1 / 2 | 2,060 / 2,816 |
| Bonsai 2 4B Q2_0 | structured | off | 63,89 | 5,56 | 2 / 3 / 3 / 3 / 3 / 2 / 2 | 2,072 / 3,442 |
| Bonsai 2 4B Q2_0 | structured | oracle_relevant | 59,72 | 5,56 | 2 / 3 / 3 / 3 / 3 / 2 / 2 | 2,114 / 2,988 |
| Bonsai 2 9B Q2_0 | direct | all | 13,89 | 44,44 | 4 / 4 / 5 / 5 / 4 / 5 / 3,5 | 0,372 / 3,315 |
| Bonsai 2 9B Q2_0 | direct | off | 15,28 | 38,89 | 4 / 4 / 4,5 / 4 / 4 / 5 / 3 | 0,325 / 3,271 |
| Bonsai 2 9B Q2_0 | direct | oracle_relevant | 15,28 | 41,67 | 4 / 4 / 5 / 4 / 4 / 5 / 3 | 0,365 / 3,261 |
| Bonsai 2 9B Q2_0 | structured | all | 16,67 | 16,67 | 4 / 4 / 4 / 4 / 3 / 3 / 3 | 2,552 / 4,461 |
| Bonsai 2 9B Q2_0 | structured | off | 16,67 | 18,06 | 3 / 4 / 4 / 4 / 4 / 3 / 3 | 2,530 / 5,333 |
| Bonsai 2 9B Q2_0 | structured | oracle_relevant | 13,89 | 18,06 | 4 / 4 / 5 / 4 / 3 / 3 / 3 | 2,519 / 4,479 |

**Separación de protocolos.** C usa temperatura 0; D cambia a sampler recomendado post hoc y no se combina con C; E compara el nuevo esquema `two_stage`; F es post hoc, con `direct` de control emparejado y dos etapas revisadas, pero su brazo Bonsai se redujo a 12 casos. Los resultados D/E/F se reportan separados y no se atribuye causalidad por contexto debido a referencias estáticas.

**Archivos de puntaje:** `dictado/bench/results/v2p-2026-10-16/blind-package-final/scored_review_v2p.jsonl` (SHA-256 `206192912e2eba6a011c22fa09b8593c3583564b363b24ec02c2a432626012bb`); `dictado/bench/results/v2p-2026-10-16/aggregate-v2p-scored-report.json` (SHA-256 `f4f7b511f98160b22fc490eb4f77c9afdd2cf8bf15ed3b324a6d27b7e4a31e3e`). La clave y los archivos de lotes se preservan.

### Puntaje, alcance y fuentes

- **Resultados completos:** C y D no tienen ganador. E midió `two_stage` con todos los modelos/contextos; ningún candidato supera el gate. F fue reducida por decisión del usuario y no es un benchmark completo ni puede producir un ganador.
- Los checks automáticos (anclas, restricciones, términos prohibidos, longitud, campos estructurados, vacío, estabilidad exacta y fin de generación) son **solo triage**. No sustituyen el rubric ciego ni deciden calidad por sí solos.
- La puntuación C, el informe canónico y sus hashes están en `dictado/bench/results/v2p-2026-10-16/blind-package-final/scored_review_v2p.jsonl` y `dictado/bench/results/v2p-2026-10-16/aggregate-v2p-scored-report.json`; no se publican medias porque el informe canónico no las expone.
- No se midió 27B; no se deben extrapolar resultados entre los dos builds, cuantizaciones o dispositivos.

**Fuentes externas del modelo/runtime:** [anuncio oficial de Prism ML sobre Bonsai 2 2B/4B/9B](https://prismml.com/news/prismml-expands-bonsai-2-family-with-2b-4b-and-9b-models-for-on-device-ai); [tarjeta Bonsai 2 2B](https://huggingface.co/prism-ml/Ternary-Bonsai-2-2B-gguf), [4B](https://huggingface.co/prism-ml/Ternary-Bonsai-2-4B-gguf) y [9B](https://huggingface.co/prism-ml/Ternary-Bonsai-2-9B-gguf); [tarjeta Qwen3.5 4B](https://huggingface.co/Qwen/Qwen3.5-4B) y [GGUF Q4_K_M usado por el producto](https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF); [compatibilidad de backend Bonsai](https://github.com/PrismML-Eng/Bonsai-demo/blob/main/BACKEND-SUPPORT.md); [documentación oficial de llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) y [build de producto b11573](https://github.com/ggml-org/llama.cpp/releases/tag/b11573).

**Fuentes de datos y reproducción.** Las entradas finales, y no resultados previos/de diagnóstico, están exactamente en:

- `dictado/bench/results/v2p-2026-10-16/qwen-q4-k-m-revC/{rep1,rep2,rep3}/{rows.jsonl,metadata.json}`
- `dictado/bench/results/v2p-2026-10-16/bonsai2-2b-q2-0-revC/{rep1,rep2,rep3}/{rows.jsonl,metadata.json}`
- `dictado/bench/results/v2p-2026-10-16/bonsai2-4b-q2-0-revC/{rep1,rep2,rep3}/{rows.jsonl,metadata.json}`
- `dictado/bench/results/v2p-2026-10-16/bonsai2-9b-q2-0-revC/{rep1,rep2,rep3}/{rows.jsonl,metadata.json}`
- Metadatos de artefactos/modelos: `dictado/bench/results/v2p-2026-10-16/model-artifacts.json`; resumen anterior: `dictado/bench/results/v2p-2026-10-16/v2p-summary.json`; informe canónico final: `dictado/bench/results/v2p-2026-10-16/aggregate-v2p-report.json`; recursos Bonsai: `dictado/bench/results/v2p-2026-10-16/bonsai2-{2b,4b,9b}-q2-0-revC/{resources.jsonl,load_time.txt}`; observación pasiva de Qwen: `dictado/bench/results/v2p-2026-10-16/qwen-product-passive-snapshot.jsonl`.
- Puntaje ciego C: `dictado/bench/results/v2p-2026-10-16/blind-package-final/scored_review_v2p.jsonl`; reporte puntuado: `dictado/bench/results/v2p-2026-10-16/aggregate-v2p-scored-report.json`; temporización canónica: `dictado/bench/results/v2p-2026-10-16/aggregate-v2p-report.json`; resumen C y recursos: `dictado/bench/results/v2p-2026-10-16/v2p-summary.json` y `bonsai2-{2b,4b,9b}-q2-0-revC/{resources.jsonl,load_time.txt}`.

```powershell
$results = "dictado/bench/results/v2p-2026-10-16"
$runRoot = "$results/reproduction"
foreach ($rep in 1..3) {
  python dictado/bench/bench_v2p.py --url http://127.0.0.1:8080/v1 --models "qwen3.5:4b-q4_K_M" --strategies baseline,legacy_instruction,direct,structured --context off,all,oracle_relevant --out-dir "$runRoot/qwen-q4-k-m-revC/rep$rep" --max-tokens 512 --temperature 0 --seed 0 --structured-mode jsonschema
  python dictado/bench/bench_v2p.py --url http://127.0.0.1:8081/v1 --models "bonsai2-2b-q2-0" --strategies baseline,legacy_instruction,direct,structured --context off,all,oracle_relevant --out-dir "$runRoot/bonsai2-2b-q2-0-revC/rep$rep" --max-tokens 512 --temperature 0 --seed 0 --structured-mode jsonschema
  python dictado/bench/bench_v2p.py --url http://127.0.0.1:8081/v1 --models "bonsai2-4b-q2-0" --strategies baseline,legacy_instruction,direct,structured --context off,all,oracle_relevant --out-dir "$runRoot/bonsai2-4b-q2-0-revC/rep$rep" --max-tokens 512 --temperature 0 --seed 0 --structured-mode jsonschema
  python dictado/bench/bench_v2p.py --url http://127.0.0.1:8081/v1 --models "bonsai2-9b-q2-0" --strategies baseline,legacy_instruction,direct,structured --context off,all,oracle_relevant --out-dir "$runRoot/bonsai2-9b-q2-0-revC/rep$rep" --max-tokens 512 --temperature 0 --seed 0 --structured-mode jsonschema
}
python dictado/bench/prepare_blind_v2p.py --results-dir $results --models qwen-q4-k-m-revC,bonsai2-2b-q2-0-revC,bonsai2-4b-q2-0-revC,bonsai2-9b-q2-0-revC --out-dir "$results/blind-package-final"
python dictado/bench/aggregate_v2p.py --results-dir $results --models qwen-q4-k-m-revC,bonsai2-2b-q2-0-revC,bonsai2-4b-q2-0-revC,bonsai2-9b-q2-0-revC --expect-repeats 3 --scored-jsonl "$results/blind-package-final/scored_review_v2p.jsonl" --blinded-key "$results/blind-package-final/blinded_key_v2p.json" --allow-unblind-after-scoring --json "$results/aggregate-v2p-scored-report.json"
```

Antes del runner, inicia los servidores con los builds/modelos y los defaults indicados arriba (Qwen b11573 en 8080, Bonsai upstream b11580 en 8081). Cada iteración escribe una matriz en su directorio de réplica; el comando no es necesario para reproducir el informe a partir de artefactos existentes. El agregador es solo lectura de datos excepto por el JSON indicado y no abre clave ni puntajes sin opciones explícitas.

### 9.1 Revisión D — sampler recomendado (post hoc)

D corrió **4 modelos × 2 estrategias (`direct`,`structured`) × 2 contextos (`off`,`oracle_relevant`) × 72 casos × 3 repeticiones = 3.456 filas**. Se puntuaron 684 variantes ciegas con GPT-6 independiente, sin evaluación humana; cobertura exacta, cero problemas de validación. Ninguna de las 16 configuraciones supera el gate. `Críticos/full gate` son porcentajes de respuestas puntuadas; no son una estimación causal por contexto dado el gold estático descrito arriba.

| Modelo | Estrategia | Contexto | Crítico % | Full gate % |
|---|---|---:|---:|---:|
| Qwen 4B | direct | off | 38,89 | 47,22 |
| Qwen 4B | direct | oracle_relevant | 30,56 | 58,33 |
| Qwen 4B | structured | off | 18,06 | 34,72 |
| Qwen 4B | structured | oracle_relevant | 16,67 | 31,94 |
| Bonsai 2 2B | direct | off | 63,89 | 11,11 |
| Bonsai 2 2B | direct | oracle_relevant | 59,72 | 13,89 |
| Bonsai 2 2B | structured | off | 79,17 | 6,94 |
| Bonsai 2 2B | structured | oracle_relevant | 72,22 | 13,89 |
| Bonsai 2 4B | direct | off | 41,67 | 48,61 |
| Bonsai 2 4B | direct | oracle_relevant | 34,72 | 52,78 |
| Bonsai 2 4B | structured | off | 58,33 | 1,39 |
| Bonsai 2 4B | structured | oracle_relevant | 54,17 | 2,78 |
| Bonsai 2 9B | direct | off | 43,06 | 43,06 |
| Bonsai 2 9B | direct | oracle_relevant | 36,11 | 54,17 |
| Bonsai 2 9B | structured | off | 29,17 | 20,83 |
| Bonsai 2 9B | structured | oracle_relevant | 26,39 | 23,61 |

Fuente: `dictado/bench/results/v2p-2026-10-16/sampler-recommended-revD/aggregate-v2p-scored-report.json`. D cambia el sampler respecto de C y no se mezcla con esa ronda.

### 9.2 Revisión E — `two_stage` de esquema revisado (post hoc)

E ejecutó **4 modelos × 1 estrategia (`two_stage`) × 2 contextos × 72 × 3 = 1.728 filas**, 2.595 llamadas reales frente a un máximo de 3.456; el agregador reportó cero problemas, errores de runner o fugas de gold, y 72/72 casos estables por condición. Se puntuaron 244 variantes ciegas, cobertura completa. Se incluyen las ocho configuraciones candidatas (`two_stage` es elegible); todas fallan el gate.

| Modelo | Contexto | Fallo de extracción /72 por repetición | Error crítico % | Full gate % | Wall todos intentos p50/p95 s | Wall éxito p50/p95 s |
|---|---|---:|---:|---:|---:|---:|
| Qwen 4B | off | 12 (16,7%) | 9,72 | 38,89 | 1,388 / 2,134 | 1,429 / 2,134 |
| Qwen 4B | oracle_relevant | 22 (30,6%) | 8,33 | 40,28 | 1,366 / 2,141 | 1,423 / 2,173 |
| Bonsai 2 2B | off | 47 (65,3%) | 9,72 | 4,17 | 0,927 / 1,313 | 0,986 / 1,184 |
| Bonsai 2 2B | oracle_relevant | 56 (77,8%) | 5,56 | 4,17 | 0,932 / 1,300 | 0,986 / 1,173 |
| Bonsai 2 4B | off | 52 (72,2%) | 9,72 | 6,94 | 1,338 / 2,151 | 1,579 / 2,190 |
| Bonsai 2 4B | oracle_relevant | 53 (73,6%) | 11,11 | 6,94 | 1,366 / 2,116 | 1,592 / 2,314 |
| Bonsai 2 9B | off | 17 (23,6%) | 6,94 | 40,28 | 1,779 / 2,624 | 1,874 / 2,538 |
| Bonsai 2 9B | oracle_relevant | 28 (38,9%) | 5,56 | 40,28 | 1,917 / 2,616 | 1,864 / 2,568 |

El reporte canónico es `dictado/bench/results/v2p-2026-10-16/two-stage-exploratory-revE/aggregate-v2p-scored-report.json`; puntajes en `dictado/bench/results/v2p-2026-10-16/blind-package-two-stage-revE/scored_review_v2p.jsonl`. Fallo de extracción significa fallo de modelo comprobado en etapa 1; se separa de transporte/infraestructura. Los p50/p95 de todos los intentos incluyen respuestas vacías fail-closed; los de éxito incluyen solo stage 2 completo y respuesta no vacía. El gold estático limita las comparaciones de calidad entre contextos.

### 9.3 Revisión F — control `direct` y `two_stage`, alcance reducido

El brazo inicial Qwen terminó sus cuatro celdas (`direct`,`two_stage` × `off`,`oracle_relevant` × 72 × 3 = 864 filas; 17 fallos de extracción en el total `two_stage`). El primer lanzador falló por el parámetro PowerShell reservado `$Pid`, antes de solicitudes/mediciones; la corrección se registró y Qwen se ejecutó sin reiniciar, reconfigurar ni reemplazar el servidor de producto. Tras la repriorización, el full F de Bonsai 2B se detuvo; se preservaron sus artefactos parciales y no se cuentan como resultados. No se hizo full F de 4B/9B.

Se congeló antes de producir salidas Bonsai el manifiesto `subset-manifest-F.json` (12 casos, un caso por categoría, SHA-256 `06559b039363666420a6046baa7beb946e357b14bdf694d20a924ef7b5d1a41e`; runner `e8326574e5a793d811a82f8b8927fcf384e7598a7360fed6e4dea9b8550fb94c`). Se midieron solo Bonsai 4B y 9B, `off`, `direct` y esquema revisado `two_stage`, tres repeticiones: **144 filas**, 207 llamadas de modelo observadas frente al máximo 216. B4 rep1 inicial produjo 24/24 errores de conexión rechazada (WinError 10061), no fallos del modelo ni llamadas procesadas por el modelo; se conservó sin sobrescribir. El proceso inicial PID 14900 había pasado readiness y aparecía escuchando antes de la réplica, luego ya no estaba; el free RAM medido antes de rep1 era 761 MiB. No hay evidencia que confirme OOM. Un único reintento B4 en carpeta separada pasó health (PID 19016; 5.030 MiB libres) y completó 72 filas con 6 fallos de extracción de `two_stage`; B9 pasó health (PID 6424; 3.887 MiB libres), completó 72 filas con 3 fallos de extracción. Ambos tuvieron cero fallos de infraestructura y cero fugas de gold. Las latencias por intento, latencias de éxito y telemetría de recursos se conservan junto con las filas.

El control ciego emparejado usa las 72 filas Qwen correspondientes a los mismos 12 casos/contexto `off` de la matriz Qwen completa y las 144 filas Bonsai. Un revisor independiente nuevo puntuó **72 variantes ciegas distintas, 216 mapeos origen** (12 casos × 3 modelos × 2 estrategias × 3 repeticiones); cobertura exacta, cero ID desconocidos/duplicados/faltantes. Antes de puntuar, el paquete se validó sin etiquetas de modelo, estrategia o contexto. El reporte de 12 casos es raw y contiene los tres casos con gold dependiente de contexto; no sirve para concluir calidad sobre esos casos. Por eso se generó, sin inferencia adicional, una sensibilidad de nueve categorías que excluye `retrieved_01`, `history_relevant_01` y `selection_authorized_01`: 162 filas fuente y 54 variantes ciegas, una por celda caso×modelo×estrategia después de deduplicar. Solo esta tabla se usa para el diagnóstico de calidad F; es exploratoria, no full-gate de producto.

| Modelo | Estrategia | Crítico % | Full gate en muestra % | Medianas (fidelidad/intención/contexto/restricciones/claridad/concisión/robustez) | Fallos stage-1 /9 por rep | Wall all p50/p95 s | Wall éxito p50/p95 s |
|---|---|---:|---:|---|---:|---:|---:|
| Qwen 4B | direct | 66,67 | 33,33 | 2 / 4 / 5 / 4 / 4 / 4 / 3 | — | 0,712 / 3,233 | 0,712 / 3,233 |
| Qwen 4B | two_stage | 11,11 | 66,67 | 5 / 5 / 5 / 5 / 5 / 5 / 4 | 0 | 1,490 / 2,003 | 1,490 / 2,003 |
| Bonsai 2 4B | direct | 22,22 | 44,44 | 4 / 4 / 5 / 5 / 4 / 5 / 3 | — | 0,353 / 0,654 | 0,353 / 0,654 |
| Bonsai 2 4B | two_stage | 22,22 | 11,11 | 4 / 4 / 5 / 4 / 4 / 3 / 4 | 2 | 1,195 / 2,180 | 1,259 / 2,180 |
| Bonsai 2 9B | direct | 44,44 | 55,56 | 4 / 4 / 5 / 5 / 4 / 5 / 4 | — | 0,700 / 3,246 | 0,700 / 3,246 |
| Bonsai 2 9B | two_stage | 11,11 | 55,56 | 4 / 4 / 5 / 5 / 5 / 5 / 4 | 1 | 1,626 / 3,420 | 1,626 / 3,420 |

Los números de F tienen **27 respuestas puntuadas por configuración** (9 casos × 3 repeticiones); no son 27 muestras independientes. El full gate en la muestra, las medianas y los críticos son una sensibilidad de un solo revisor, no evidencia para proclamar ganador. La métrica de latencia resume los tres p50/p95 por repetición; `all` incluye fail-closed y `éxito` exige stage 2 final no vacío. E vs F también es una comparación post hoc con variación entre sesiones evaluadoras.

Artefactos: `dictado/bench/results/v2p-2026-10-16/two-stage-direct-exploratory-revF/` contiene Qwen completo, intentos parciales preservados, manifiesto, recursos, filas y reportes. Paquetes ciegos F: `blind-package-matched12-off-revF/` (puntuación completa) y `blind-package-matched9-sensitivity-revF/` (subconjunto analítico). Los reportes son `aggregate-matched12-off-scored-report.json` y `aggregate-matched9-off-sensitivity-scored-report.json`. E permanece separado.

### 9.4 Respuestas explícitas a las nueve preguntas

| # | Pregunta | Conclusión basada en la evidencia disponible |
|---:|---|---|
| 1 | ¿Qwen 3.5 4B era insuficiente para V2P? | Bajo C/D/E y el arnés medido, Qwen no cumple el gate completo; eso no prueba incapacidad intrínseca del modelo. La sensibilidad F de nueve categorías sugiere que Qwen `two_stage` funciona mejor que `direct` en esa muestra, pero no es un benchmark completo y un solo evaluador limita la conclusión. |
| 2 | ¿Una mejora del system prompt resuelve el problema? | No demostrado de forma general. E/F prueban esquema revisado y segunda etapa, pero E tuvo 861/1.728 salidas vacías por fail-closed de etapa 1 y ninguna configuración pasó el gate. La pequeña sensibilidad F favorece `two_stage` frente a `direct` para Qwen, no acredita resolución. |
| 3 | ¿Agregar contexto real mejora significativamente? | **No establecido.** La entrada varía como se diseñó, pero el gold único estático introduce en cada condición `off` 14/72 casos que exigen contexto ausente: 5 retrieved, 5 history-relevant y 4 selection-authorized. Los deltas off/oracle son descriptivos y potencialmente sesgados, no causales. |
| 4 | ¿Había errores de implementación/configuración del harness? | Sí: revisiones A–C corrigieron problemas de medición (entre ellos TPS y una auditoría de fuga de gold tautológica); esos errores no describen el producto ni las corridas finales C/D. El lanzador inicial F `$Pid` falló antes de solicitudes; se registró como fallo de launcher, no del modelo. E/F separan extracción y errores de infraestructura. Queda la limitación del gold estático, sin edición retroactiva. |
| 5 | ¿Bonsai 2 4B supera a Qwen 4B? | No demostrado. C/D/E no dan una victoria general; en F-9, B4 `direct` fue más rápido y mostró 44,44% gate vs Qwen `direct` 33,33%, pero `two_stage` B4 obtuvo 11,11% vs Qwen 66,67%, con solo 27 respuestas/configuración y fallo de extracción B4. No hay base para sustituir Qwen. |
| 6 | ¿Bonsai 2 9B mejora lo suficiente? | No demostrado. E tuvo full gate ≈40% en dos contextos pero fallos críticos y stage-1; F-9 fue prometedor en `two_stage` (55,56% full gate, 11,11% críticos), pero es solo una muestra y no demuestra gate de corpus completo. |
| 7 | ¿Qué modelo ofrece mejor experiencia interactiva? | No se elige ninguno para V2P. En F-9 B4 `direct` fue el más rápido (wall p50/p95 0,353/0,654 s), pero calidad no es concluyente; `two_stage` agrega latencia y errores de extracción. La latencia sola no define experiencia aceptable. |
| 8 | ¿Conviene un mismo LLM para todas las funciones? | No hay evidencia para unificar: corrección de dictado y V2P tienen contratos distintos. Se conserva Qwen en su función actual; V2P debe seguir aislado hasta validar su propia calidad y fallback. |
| 9 | ¿Qué arquitectura permitiría incorporar V2P de forma confiable? | Aún no integrar. Si se retoma, usar extracción con procedencia y esquema estricto, validación determinista de hechos/restricciones/contexto, generación solo desde datos validados, rechazo visible/fallback seguro y evaluación con gold separado por contexto. Cualquier release requiere superar el gate completo en un corpus corregido y congelado nuevo. |


## 10. Fuentes

**Runtime y modelo**

- API del servidor de llama.cpp (campos por pedido como `chat_template_kwargs` y `response_format`): <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>
- Versión del runtime usada por el instalador: <https://github.com/ggml-org/llama.cpp/releases/tag/b11573>
- Modelo base: <https://huggingface.co/Qwen/Qwen3.5-4B> · Cuantización Q4_K_M: <https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF>
- Reconocimiento local del producto (contexto del pipeline, no de este benchmark): <https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3>, <https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8>

**Artefactos internos**

- Arnés y modos: `dictado/bench/bench_v2p.py` · congelado: `dictado/bench/v2p_references.sha256`
- Corpus: `dictado/bench/v2p_cases.json` · Rubric: `dictado/bench/v2p_rubric.json` · Pruebas del arnés: `dictado/tests/test_bench_v2p.py`
- Diagnóstico del producto: `docs/AUDITORIA-LLM.md` · Diseño del prototipo: `docs/VOICE-TO-PROMPT.md`
- Comparación previa de cuantizaciones (solo corrección de dictado, una máquina, 20 frases): `docs/benchmark-llm-local-2026-10-12.md`, `docs/informe-tecnologia-y-llm-local.md`

**Advertencia de interpretación:** las mediciones de `docs/benchmark-llm-local-2026-10-12.md` y `docs/informe-tecnologia-y-llm-local.md` son de **corrección de transcripción**, no de Voice-to-Prompt, y no se pueden usar como evidencia de calidad de esta tarea.
