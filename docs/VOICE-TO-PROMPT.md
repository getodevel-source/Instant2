# Voice-to-Prompt: diagnóstico y prototipo aislado

**Fecha:** 16 de octubre de 2026 · **Revisión auditada:** rama `main`, commit `0c56c98`
**Estado:** Voice-to-Prompt sigue **sin implementación de producto**. C, D y E están completos, sin candidato que supere el gate; F se redujo a un diagnóstico emparejado de 12 casos por decisión expresa.

> Ningún brazo obtuvo un ganador. El corpus tiene un solo gold fijo por caso; en 14 casos de `off` el gold exige datos solo disponibles vía contexto. Los cambios de calidad por contexto son potencialmente sesgados y no demuestran causalidad.

---

## Resumen

- Voice-to-Prompt **no tiene implementación de producción** y la búsqueda del código/historial original de V2P no encontró resultados; el prototipo de medición está aislado en el benchmark.
- La comparación C, D y E está completa y no identifica ganador. F conserva la matriz completa Qwen pero solo 12 casos emparejados para Bonsai 4B/9B; se evaluó como exploratorio, sin ganador.
- Las mediciones de corrección de dictado que hay en `docs/` **son de otra tarea** y no sirven como evidencia de V2P.
- El runtime actual **sí** puede responder en modo directo sin traza de razonamiento: se verificó que la condición `enable_thinking=false` de la plantilla del modelo funciona con el `llama-server` del instalador.
- El prototipo de medición separa **transformación directa** de **extracción estructurada + renderizado determinista**, para que la parte verificable no dependa del criterio del modelo.
- El contexto se inyecta con **procedencia explícita** (dicho / recuperado / desconocido) y con compuertas por modo; el texto seleccionado sin autorización queda excluido por construcción.
- C/D/E se evaluaron ciegamente con GPT-6 independiente; F tuvo una sesión nueva de revisor independiente sobre 72 variantes. No hubo inter-rater humano. El problema de referencias estáticas invalida conclusiones causales de calidad `off` vs `oracle_relevant`.

---

## 1. ¿Existe hoy en el repositorio? No

Evidencia de búsqueda (todo sobre el árbol de trabajo y sobre el historial completo de Git):

| Comprobación | Resultado |
|---|---|
| Búsqueda de `voice-to-prompt`, `voz a prompt`, `voice2prompt`, `prompt de voz` en todo el árbol | **0 archivos** |
| `git log --all -S"Voice-to-Prompt"` | **0 commits** |
| `git log --all --grep=prompt -i` | Solo 2 commits, ambos sobre el glosario del corrector (`111309f`, `cfa3df0`) |
| Archivos agregados alguna vez con "prompt" en el nombre | Ninguno |
| Ramas locales y remotas (`origin`, `upstream`) | `main`, `feat/in-app-updates-and-ui-copy`, `fix/instant2-v0.1.2`, `release/instant2-v0.1.5`, `fix/overlay-voice-waveform`, `release/v0.5.1`, `release/v0.5.2` — ninguna de V2P |
| Usos de la palabra "prompt" en el producto | Solo la lista de mensajes del cliente de chat y las dos instrucciones de corrección (`llm.py:76-101`, `llm.py:104-131`) |

El patrón más cercano para insertar una función nueva sin tocar el dictado es el flujo de **lectura de selección (F10)**: un pipeline independiente que reutiliza el indicador y los hilos del proceso de dictado (`daemon.py:763-812`, `reading.py`, `tts.py`).

## 2. Por qué la evidencia de corrección no sirve como evidencia de V2P

- Las instrucciones de corrección **prohíben generar**: piden conservar intención, nombres, cifras y restricciones, y no agregar nada (`llm.py:76-101`).
- La comprobación posterior exige que se preserve el mismo pedido y castiga cualquier palabra nueva (`llm.py:104-131`, `llm.py:460-479`).
- Los números publicados miden **error de transcripción** sobre corpus sintéticos, en un equipo (`docs/benchmark-llm-local-2026-10-12.md`, `docs/informe-tecnologia-y-llm-local.md`).
- Un sistema optimizado para *no cambiar nada* no dice nada sobre *transformar correctamente*. Son tareas distintas y el arnés también lo es.

## 3. Qué permite afirmar la evidencia verificada del runtime

Observado contra el `llama-server` del instalador (build 11573) y el GGUF Qwen3.5 4B Q4_K_M:

| Hecho verificado | Evidencia |
|---|---|
| Modo directo disponible con `chat_template_kwargs: {"enable_thinking": false}` | Condición en la plantilla del GGUF; el campo es aceptado por el servidor |
| Sin el campo, el modelo **sí** razona y la respuesta útil queda vacía con tope de tokens bajo | Medición directa: contenido vacío, `finish_reason=length`, traza en el campo de razonamiento |
| No existen `/think` ni `/nothink` | Conteo de bytes en el GGUF: `/nothink` = 0; `enable_thinking` = 2 (variable de plantilla) |
| La plantilla no implementa esfuerzo de razonamiento | `reasoning_effort` = 0 en el GGUF; el servidor reporta `supports_reasoning_effort: false` |
| El modo directo produce salida limpia y rápida | Respuestas en caliente de 0,2 s a 0,9 s con textos sintéticos |
| Salida JSON restringida por esquema | Verificado con `response_format` de tipo `json_schema` en el mismo runtime |
| El producto no usa streaming ni historial | `llm.py:208`, `llm.py:263`; sin acumulación entre dictados |
| No hay contexto ambiente que se pueda "aprovechar" | No se lee la ventana en primer plano ni el portapapeles; la única fuente extra es la selección por F10 |

Esto habilita el prototipo, **no** valida su calidad.

## 4. Arnés aislado y límite con producto

El arnés de medición de `dictado/bench/bench_v2p.py` está separado del camino de dictado y de la aplicación; no existe implementación de producto. Estrategias comparables:

| Estrategia | Qué hace | Para qué sirve |
|---|---|---|
| `baseline` | Reproduce el camino de producción: corrección local → corrección con el LLM → comprobación de significado (`llm.py:332-372`) | Referencia honesta: devuelve una corrección del dictado, no un prompt |
| `direct` | Una llamada con una instrucción nueva de "convertí el dictado en un prompt" | Mide la transformación libre |
| `structured` | Una llamada restringida por esquema + **renderizador determinista** (sin segunda llamada al modelo) | Deja el formato en manos de código auditable |
| `two_stage` | Dos llamadas: extracción JSON validada (hechos explícitos literales del dictado y contexto literal con procedencia) y generación; ante extracción o generación inválida no entrega respuesta final | E: matriz completa post hoc; F: brazo reducido de 12 casos con control `direct`; no hay candidato ganador |

Interfaz mínima del prototipo:

- `PROMPT_SYSTEM`: instrucción nueva (no se reutiliza `DEFAULT_SYSTEM`, que prohíbe generar).
- `build_prompt(texto, cfg, ...)`: transformación directa, `temperature=0`, modo directo, tope de tokens acorde.
- `extract_instruction(texto, cfg, schema)`: extracción estructurada (`json_schema` estricto).
- `render_prompt(campos)`: renderizado determinista en español, sin modelo.
- `run_two_stage`: extrae JSON con intención, hechos explícitos literales, restricciones/negaciones, hechos de contexto autorizados con procedencia y desconocidos; valida esquema, hechos explícitos contra el dictado y procedencia contextual antes de generar. Una extracción inválida/truncada cierra sin segunda llamada; generación incompleta/no-stop descarta el texto parcial y no produce respuesta.
- `gate_preserved_terms(origen, salida, campos)`: comprobación de datos conservados propuesta para producto; no forma parte del arnés actual.

Regla de diseño del prototipo: cuando el esquema alcanza, **no** se deja el formato a texto libre; la transformación directa queda propuesta como respaldo para habla fuera de esquema.

## 5. Procedencia del contexto

Cada caso del corpus marca el origen de cada dato:

| Procedencia | Significado | Cómo se trata |
|---|---|---|
| `explicit` | El dato se dijo en el propio pedido | Debe copiarse literal (nombres, cifras, rutas, comandos) |
| `retrieved` | El dato viene de contexto aportado por el usuario | Puede usarse solo si el modo de contexto lo habilita |
| `unknown` | El dato no está en ninguna parte | Debe quedar explícito como desconocido; rellenarlo es error crítico |
| `history` | El dato viene de un turno anterior | Solo si el modo lo habilita; el turno irrelevante no debe filtrarse |
| `selection` | El dato viene de texto seleccionado | Solo si el usuario lo autorizó explícitamente |

Compuertas de contexto, seleccionables por separado: `off`, `glossary`, `provided`, `history`, `selection`, `all`.

Reglas de aislamiento:

- Sin canal dictado, `destino` queda nulo y **no se inventa**; tampoco se declara como dato faltante si el pedido se puede ejecutar sin elegirlo.
- La selección **no autorizada** no se inyecta nunca y su contenido no puede aparecer en la salida (verificado en el arnés para todos los modos de contexto).
- Sin contexto ambiente: no hay ventana en primer plano, ni portapapeles, ni historial implícito. Lo que no se aporta explícitamente, no existe para el modelo.

## 6. Validación determinista y respaldo propuesto

- **Invariantes automáticos** (solo lo defendible): anclas presentes, prohibidos ausentes (dato inventado, fuga de selección no autorizada, turno irrelevante), restricciones presentes, longitud razonable, salida no vacía.
- **Evaluación ciega:** C/D/E se puntuaron con evaluadores GPT-6 independientes, sin evaluador humano; F fue puntuada por un único revisor independiente nuevo. Las puntuaciones no son evaluación humana ni inter-rater. C/D/E/F y su cobertura se detallan en `docs/BENCHMARK-MODELOS.md`.
- **Material ciego**: filas con identificador aleatorio que contienen entrada, **solo el contexto autorizado**, el prompt de referencia congelado y la respuesta. El mapeo a modelo/estrategia/contexto vive en un archivo aparte.
- **Rubric pre-registrado** antes de ver cualquier salida.
- **Estado actual del arnés**: `baseline`, `direct` y `structured` se ejecutan de forma independiente; `two_stage` es una cadena explícita de extracción validada y generación, sin estrategia de respaldo. No hay caídas automáticas entre estrategias; los invariantes y el gate solo se informan por fila, no redirigen la salida.
- **Propuesta para integrar luego (no es comportamiento actual)**: en la futura función de producto, si el renderizado determinista falla o la extracción no devuelve campos, caer a transformación directa y, en última instancia, al texto original, para que nunca quede una salida inventada ni vacía en silencio. Ese encadenado requiere su propia prueba antes de considerarse implementado.
- **Sin traza de razonamiento**: todas las llamadas del prototipo fijan `enable_thinking: false`, como el camino actual (`llm.py:190`).

## 7. Gate de confiabilidad conservador (congelado antes de medir)

- Un **error crítico** rechaza la respuesta sin importar el estilo. Clases: cambio de intención, negación o restricción alterada, condición perdida o alterada, dato inventado, contexto no autorizado o irrelevante, nombre/cifra/ruta equivocados.
- Un candidato solo es **viable** si no tiene errores críticos **y** las siete dimensiones quedan en 4/5 o más: fidelidad, intención, disciplina de contexto, restricciones, claridad, concisión y robustez.
- Si **ningún** candidato cumple todo el gate, se informa *sin ganador* con los más cercanos y el gate que los descarta. No hay ganador por promedio ni por mayoría.
- Los umbrales y las clases de error están congelados por hash; cualquier cambio exige versión nueva del rubric y freeze nuevo, y las corridas anteriores quedan marcadas como rubric viejo. No se relajan después de ver salidas.

## 8. Corpus congelado y artefactos

| Artefacto | Qué es |
|---|---|
| `dictado/bench/v2p_cases.json` | 72 casos sintéticos en español rioplatense, con prompt de referencia escrito a mano y expectativas por caso |
| `dictado/bench/v2p_rubric.json` | Rubric pre-registrado: errores críticos, siete dimensiones 1-5 con anclas, gates de aceptación |
| `dictado/bench/v2p_references.sha256` | Congelado: hash de referencias, del archivo del corpus, del rubric y del contrato de puntaje, más la cantidad de casos |
| `dictado/bench/bench_v2p.py` | Arnés: baseline/direct/structured y brazo exploratorio `two_stage` post hoc; dimensiones modelo/instrucción/contexto, JSONL por corrida, tokens por llamada, TTFT/tiempos, motivos de fin, errores y percentiles |

Cobertura del corpus: pedidos ordinarios, ambiguos, con muletillas, largos, con múltiples condiciones, con contexto disponible (dicho y recuperado), con contexto ausente, con historial relevante y con historial irrelevante, con selección autorizada y con selección no autorizada.

El arnés puede correr contra cualquier endpoint compatible, tiene modo `--dry-run` (sin red: valida congelado, fuga de gold, consistencia del propio corpus) y modo `--fixture` (humo de plomería contra un servidor simulado, sin modelo y **no puntuado**).

## 9. Resultados y estado final

- **C completa:** 10.368 filas (4 modelos × 4 estrategias × 3 contextos × 72 × 3), 1.461 variantes ciegas puntuadas; los 24 candidatos de `direct`/`structured` fallan el gate.
- **D completa y post hoc:** 3.456 filas con sampler recomendado; no hay candidato que pase el gate. Está separada de C.
- **E completa y post hoc:** 1.728 filas de `two_stage`, 2.595 llamadas reales; las 8 configuraciones fallan el gate. Fallos de extracción hacen que muchas salidas finales queden vacías.
- **F reducida y post hoc:** Qwen completó su matriz `direct`/`two_stage` con `off`/`oracle_relevant` (864 filas). Bonsai 2B fue detenido y 4B/9B se midieron solo en 12 casos estratificados emparejados, `off`, tres repeticiones y ambos métodos (144 filas Bonsai, 207 llamadas de modelo observadas de un máximo de 216). La primera corrida B4 falló por conexión rechazada (infraestructura, no modelo); un único reintento separado completó, con fallos de extracción candidatos separados. No es benchmark de ganador.
- **Limitación crítica de calidad por contexto:** los 14 casos `context_available_retrieved` (5), `history_relevant` (5) y `selection_authorized` (4) tienen un gold que incluye datos contextuales ausentes en `off`. No se corrigió el corpus congelado ni se rehízo ninguna matriz. F incluye tres: `retrieved_01`, `history_relevant_01`, `selection_authorized_01`; la sensibilidad de nueve categorías los excluye.
- **Decisión:** no hay evidencia suficiente para cambiar el producto ni integrar V2P. Qwen permanece en su flujo actual; Parakeet y Kokoro no se modificaron.
- **Cobertura del hardware:** no se hizo un smoke CPU tras el recorte; no hay medición de potencia ni extrapolación de CPU. Bonsai 27B no se midió porque el backend upstream observado no implementa los kernels requeridos.
- **Trazabilidad y respuesta a las nueve preguntas:** `docs/BENCHMARK-MODELOS.md`, secciones 9.1-9.4; reportes estructurados en `dictado/bench/results/v2p-2026-10-16/` (salida generada, fuera del control de versiones).
- Fuera del alcance: entrenamiento o fine-tune, cambios en el camino de dictado y uso de texto del usuario como contexto sin autorización explícita.

## 10. Fuentes

- API del servidor (modo directo por plantilla, salida restringida por esquema): <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>
- Versión del runtime del instalador: <https://github.com/ggml-org/llama.cpp/releases/tag/b11573>
- Modelo base: <https://huggingface.co/Qwen/Qwen3.5-4B> · Cuantización Q4_K_M: <https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF>
- Reconocimiento local (referencia del pipeline actual): <https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3>
- Ciclo de vida y presupuestos del runtime local: `docs/arquitectura-runtime-llm-local.md`
- Estado del LLM local y fallbacks: `docs/AUDITORIA-LLM.md`
