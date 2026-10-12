# Informe de mejoras tecnológicas y evaluación de un LLM local

**Fecha:** 11 de octubre de 2026  
**Alcance:** experiencia de uso, rendimiento, precisión y posibilidad de usar otro modelo de lenguaje local en Instant.

## Resumen ejecutivo

Instant ya tiene una base sólida: reconocimiento de voz local, vocabulario personal, correcciones deterministas, control de errores del endpoint opcional y una interfaz con controles de accesibilidad y escala. La próxima mejora debería enfocarse en medir y hacer visibles los estados del producto, además de probar con cuidado cualquier nuevo modelo.

La conclusión sobre el LLM es concreta: **sí vale la pena probar un modelo local pequeño para corregir tildes, mayúsculas y puntuación; no hay evidencia de que vaya a reconocer mejor la voz que el motor actual**. Instant usa Parakeet TDT v3 int8 como motor de voz. El LLM opcional es una etapa distinta: recibe texto ya reconocido y no recibe el audio. En este checkout no hay un modelo de lenguaje seleccionado ni incluido por defecto; la aplicación permite configurar un endpoint local compatible.

Recomiendo iniciar una comparación con **Qwen3.5 2B Q4_K_M** como variante liviana y **Qwen3.5 4B Q4_K_M** como candidata de mayor calidad. En el registro de Ollama sus archivos figuran aproximadamente en 1,9 GB y 3,3 GB, respectivamente. Esos números describen la descarga del modelo, no la RAM total que consumirá durante la ejecución. [Etiquetas y tamaños publicados por Ollama](https://ollama.com/library/qwen3.5/tags). La comparación debe incluir también Gemma 4 E2B/E4B si el primer resultado no satisface el español de dictado, aunque las variantes que publica hoy Ollama ocupan más espacio de descarga. [Modelos Gemma 4 en Ollama](https://ollama.com/library/gemma4/tags).

No recomiendo reemplazar Parakeet ni activar el LLM por defecto todavía. Primero hay que medir ambos candidatos con el corpus de Instant y conservar la validación actual que impide que el LLM cambie la secuencia de palabras.

## 1. Qué hay hoy en Instant

### Reconocimiento y corrección

El flujo actual separa cuatro trabajos:

1. Instant captura audio local a 16 kHz.
2. Silero VAD detecta los segmentos hablados; TEN-VAD es una alternativa optativa.
3. Parakeet TDT v3 int8 reconoce las palabras. La conversión usada por sherpa-onnx pesa cerca de 671 MB y el modelo está documentado para 25 idiomas europeos, entre ellos español. [Documentación de sherpa-onnx sobre Parakeet v3](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html), [archivos del modelo ONNX](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/tree/main).
4. Instant aplica el vocabulario y reglas locales; solo si el resultado queda por debajo del umbral de confianza configurado intenta el pulido opcional mediante un endpoint LLM.

El LLM no se instala con Instant. La URL está vacía por defecto. Cuando se configura, Instant manda el texto reconocido y el vocabulario activo, nunca el audio. Un endpoint de loopback puede mantener esa petición en el equipo; un endpoint remoto recibe esos datos y debe tratarse como tal.

El prompt pide corregir ortografía, tildes y puntuación sin agregar, quitar, cambiar ni reordenar palabras. Después, el programa compara la secuencia de palabras normalizada y conserva el texto anterior si detecta diferencias. La normalización ignora mayúsculas y tildes, así que el LLM puede afectar esos aspectos y la puntuación, pero no debería reemplazar términos. Si el servicio falla, Instant continúa con la corrección local. También hay una regla determinista para añadir `¿` en preguntas obvias.

La interfaz expone URL y token del servidor; hoy el usuario debe saber qué servicio local ejecutar y qué dirección poner. No hay un selector de modelos ni un botón que pruebe la conexión.

### Evidencia de precisión disponible

La documentación de precisión del proyecto ya deja una base útil, pero acotada:

- En FLEURS es_419, el benchmark de 60 clips registró WER 0,050 para el baseline y también para la variante de bias.
- Un conjunto técnico sintético mostró una reducción de WER de 0,254 a 0,206 con bias; sirve para comparar esa batería y no para prometer el mismo resultado en dictado espontáneo.
- Una prueba técnica local reportó 12/12 términos técnicos reconocidos y 0 falsos positivos en 129 palabras sanas; es una muestra pequeña.
- En una prueba anterior de 34 frases, Parakeet y una alternativa de reconocimiento basada en Qwen quedaron cerca en WER y cambiaron de posición entre dos pasadas. Parakeet fue aproximadamente tres veces más rápido en esa medición y produjo más signos `¿` correctos. Por ese resultado se retiró aquella integración de reconocimiento Qwen.

Esos datos no evalúan Qwen3.5 como corrector textual: son otro modelo, otra tarea y otra integración. No deben usarse como prueba a favor o en contra de los candidatos de este informe. Tampoco hay todavía un baseline nuevo de LLM local sobre el corpus privado consentido.

### Rendimiento que ya se está trabajando

En el checkout compartido hay un cambio local para liberar el modelo de voz después de cinco minutos sin uso y volver a cargarlo cuando haga falta. Una medición aislada del proceso Engine pasó de unos 733 MiB cargado a 55 MiB tras descargarlo; al volver a cargarlo tardó aproximadamente 1,56 segundos. Eso mide el Engine, no la aplicación completa, y todavía es una mejora de trabajo, no una característica que deba darse por disponible en un release.

También se midió el ajuste de hilos de CPU en una señal sintética de 4,4 segundos: cuatro hilos dieron el mejor resultado local, alrededor de 0,65 s; más hilos no mejoraron esa prueba. Es evidencia para mantener cuatro como valor inicial en esa máquina, no una regla para todos los procesadores. El benchmark de voz real debe guiar cualquier ajuste por equipo.

## 2. Qué puede y qué no puede mejorar un LLM local

### Puede ayudar con

- Tildes, signos de apertura, comas, puntos y mayúsculas.
- Presentación coherente de cifras, siglas o nombres cuando el texto ya contiene esos elementos y las reglas permiten normalizarlos sin cambiar palabras.
- Variantes ortográficas para un término presente en la transcripción, usando el vocabulario activo como referencia.
- Una interfaz de corrección con una propuesta alternativa para que la persona la acepte o la descarte, siempre que eso se mantenga opcional.

### No resuelve por sí solo

- Palabras que el reconocedor omitió por completo: el LLM no oyó el audio y no tiene una fuente confiable para recuperarlas.
- Distinguir homófonos o nombres propios ambiguos a partir del texto sin información acústica adicional. Puede elegir la frase más probable y sonar convincente, aunque no sea lo que se dijo.
- La segmentación de frases, micrófonos deficientes, ruido, ataques recortados o vacíos de transcripción. Son problemas del audio, VAD o ASR.
- Aumentar la precisión léxica manteniendo a la vez una regla estricta que no permite cambios de palabras. Con la protección actual, el beneficio esperado está en puntuación, mayúsculas y acentuación.

Esta diferencia es importante: **un modelo de lenguaje general no sustituye a un modelo de reconocimiento de voz**. Si queremos mejorar qué palabras reconoce Instant, hay que comparar motores ASR con audio idéntico y referencias revisadas. Si queremos mejorar cómo se presenta el texto reconocido, entonces sí corresponde probar un LLM como editor posterior.

## 3. Comparación de candidatos locales

Los tamaños de descarga varían por formato, cuantización y etiqueta; el tamaño del archivo no equivale a la memoria total de ejecución. También hay diferencias por CPU, GPU, backend y longitud de contexto. La tabla es una selección para hacer un piloto, no una afirmación de que uno sea el mejor corrector de español.

| Candidato | Tamaño publicado de descarga | Uso que probaría | Ventajas potenciales | Costos y dudas |
|---|---:|---|---|---|
| Qwen3.5 2B Q4_K_M | ~1,9 GB | Perfil **Liviano** | Menor descarga entre los candidatos principales; sirve para medir si un modelo pequeño alcanza para tildes y puntuación. | La calidad en frases incompletas, nombres rioplatenses y dictado espontáneo debe medirse. No hay garantía de que sea más rápido en todos los equipos. |
| Qwen3.5 4B Q4_K_M | ~3,3 GB | Perfil **Equilibrado** | Más capacidad que el 2B con tamaño todavía moderado; sería mi primer candidato de calidad si el 2B comete errores de formato. La tarjeta del 4B declara licencia Apache 2.0 para ese repositorio. | Puede ocupar más RAM y tardar más en CPU. Los benchmarks generales del modelo no predicen la calidad de corrección del español de Instant. |
| Gemma 4 E2B | ~4,6–7,5 GB en la etiqueta actual de Ollama | Alternativa de borde | Google documenta soporte multilingüe en más de 140 idiomas; Ollama describe E2B como variante para dispositivos edge. | El tamaño de descarga publicado es mayor que Qwen3.5 2B/4B. Falta comprobar el uso real de RAM, la velocidad y la calidad de edición en la aplicación. |
| Gemma 4 E4B | ~6,6–9,5 GB en la etiqueta actual de Ollama | Alternativa de calidad | Más capacidad que E2B; la tarjeta oficial documenta entrada de texto e imagen y soporte de audio en E2B/E4B/12B, aunque Instant solo necesita texto. | Descarga más pesada y las funciones multimodales no aportan al pulido de dictado. El costo de CPU/RAM podría no compensar. |

Referencias: [Qwen3.5 2B y 4B Q4_K_M en Ollama](https://ollama.com/library/qwen3.5/tags), [tarjeta de Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B), [tamaños de Gemma 4 en Ollama](https://ollama.com/library/gemma4/tags), [tarjeta oficial de Gemma 4](https://ai.google.dev/gemma/docs/core/model_card_4?hl=es).

### Recomendación de selección

Empezaría por Qwen3.5 2B Q4_K_M como prueba de eficiencia y Qwen3.5 4B Q4_K_M como prueba de calidad. Si el 2B ofrece prácticamente el mismo resultado, elegiría el más pequeño. Si el 4B mejora puntos de puntuación o acentuación sin cambiar palabras y con latencia aceptable, ofrecería ambos como perfiles. Gemma 4 puede entrar en una segunda ronda, sobre todo si el 4B de Qwen no resuelve los errores que interesan.

No seleccionaría un modelo por su puntuación en razonamiento, código o preguntas y respuestas: no son la tarea de Instant. Tampoco usaría ventanas de contexto enormes para corregir una frase corta. El servicio debe trabajar con el contexto mínimo que necesite el texto y el glosario.

## 4. Cómo conectarlo sin hacer pesada la aplicación

La solución con menor impacto en el tamaño de Instant es mantener el LLM como componente optativo y externo:

1. El usuario instala un runtime, por ejemplo Ollama o llama.cpp, y descarga el modelo que su equipo puede manejar.
2. Instant se conecta al endpoint local compatible que ese runtime expone.
3. La aplicación conserva el modo actual apagado por defecto y el fallback local si el servidor no responde.

Ollama publica compatibilidad con Chat Completions de OpenAI. llama.cpp ofrece servidor HTTP con endpoints compatibles y cuantización para CPU/GPU. Esto se alinea con la integración de endpoint que ya tiene Instant y evita descargar varios gigabytes dentro del instalador principal. [Compatibilidad OpenAI de Ollama](https://ollama.com/blog/openai-compatibility), [servidor de llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

La integración existente ya manda temperatura cero y tiene un timeout de ocho segundos. Antes de ampliar su uso, añadiría una prueba de conexión que confirme servidor, modelo cargado, formato de respuesta y tiempo de respuesta sin enviar texto privado. También convendría identificar claramente en pantalla si el destino es local o remoto. Un endpoint remoto puede ser útil, pero la aplicación debe avisar que transmite texto y glosario fuera del equipo.

Para evitar una espera larga si el LLM está apagado, mostraría su estado y mantendría un fallback rápido. Después de varios fallos consecutivos, Instant podría pausar temporalmente las solicitudes y permitir reintentarlo desde Preferencias. No debe bloquear la entrega del resultado de voz esperando ocho segundos ante cada error.

El modelo tampoco debería iniciarse automáticamente en segundo plano sin que el usuario haya elegido esa opción. Mantenerlo fuera del paquete principal contiene el tamaño de descarga, el consumo en reposo y la carga de soporte. El unload del modelo ASR y el ciclo de vida del LLM se deben medir por separado: apagar un modelo puede ahorrar memoria, pero cargarlo de nuevo añade latencia.

## 5. Mejoras recomendadas para Instant

| Prioridad | Mejora | Beneficio | Costo o riesgo | Recomendación |
|---|---|---|---|---|
| P1 | Asistente de primera configuración | Hace más claro elegir micrófono, atajo, permisos, descarga y prueba de pegado. | Trabajo de UI y revisión por sistema operativo. | Guiar el primer dictado de principio a fin y conservar la configuración simple. |
| P1 | Estado verificable del endpoint LLM | Evita que el usuario configure una URL sin saber si hay servidor y modelo disponibles. | Compatibilidad imperfecta entre implementaciones OpenAI-like. | Botón “Probar conexión” y diagnóstico que no incluya texto dictado. |
| P1 | Medición de confianza por escenario | Reduce consultas LLM innecesarias y permite ajustar el umbral usando voz real. | La confianza del decoder no es automáticamente una probabilidad calibrada de acierto. | Calibrar con clips consentidos y reportar falsos seguros/falsos dudosos por tipo de frase. |
| P1 | Banco de evaluación de corrección | Compara LLM y reglas locales sin tocar el flujo de producción. | Requiere referencias revisadas y una muestra suficientemente variada. | Guardar métricas agregadas y mantener audio y manifiesto privados. |
| P2 | Vista previa opcional para correcciones dudosas | Permite revisar una propuesta cuando el dictado tiene baja confianza. | Puede ralentizar el flujo de “mantener y soltar”. | Dejar el pegado directo como default; ofrecer vista previa como preferencia. |
| P2 | Acceso directo a vocabulario desde una corrección | Facilita enseñar un nombre técnico o propio que falla repetidamente. | Si se aprende automáticamente podría perpetuar errores. | Añadir términos solo mediante acción explícita; no capturar todo el dictado como historial. |
| P2 | Perfiles de uso “Batería / Equilibrado / Rápido” | Hace más entendible CPU e impacto en autonomía que elegir hilos sin guía. | Una opción puede rendir distinto en cada procesador. | Los perfiles deben ser puntos de partida configurables; dejar 4 hilos como valor equilibrado actual hasta medir más equipos. |
| P2 | Acceso visual a escala y movimiento reducido | Aumenta comodidad sin cambiar el motor de voz. | Revisión manual por plataforma. | La escala 100/125/150 % y varias mejoras accesibles ya existen; completar las verificaciones antes de rediseñar la interfaz. |
| P3 | Reemplazar WebEngine del panel | Podría bajar instalación y memoria si se confirma que el panel tiene un peso medible relevante. | Alto costo de mantenimiento y posible pérdida de consistencia entre UI y lógica. | Medir paquete, RAM idle y arranque antes de plantear migrar el panel a widgets nativos. |

### Experiencia visual concreta

El panel ya incluye escala de interfaz, navegación por secciones, ajustes avanzados y un overlay de estados del dictado. El próximo paso visual debería ser que la persona entienda qué está ocurriendo sin abrir preferencias:

- En el panel principal, mostrar micrófono activo, tecla elegida, modelo listo y estado del servicio opcional.
- En el overlay, diferenciar con claridad “escuchando”, “transcribiendo”, “pegado” y “falló el pegado”; conservar una salida accesible y de bajo movimiento.
- Durante la primera configuración, ofrecer una prueba corta de micro y una prueba de pegar en un campo de texto. Evitar explicaciones técnicas largas; dejar detalles avanzados en preferencias.
- Ofrecer una vista previa de baja confianza solo a quien la active. La rapidez del dictado por atajo sigue siendo el recorrido principal.
- No guardar transcripciones por defecto. Si se introduce historial para facilitar correcciones, que sea voluntario, local, temporal y con un control claro para borrarlo.

## 6. Plan de prueba del LLM local

### Comparación

Usar exactamente los mismos audios y referencias para cada alternativa:

1. Instant sin LLM, con vocabulario y reglas locales.
2. Qwen3.5 2B Q4_K_M.
3. Qwen3.5 4B Q4_K_M.
4. Gemma 4 E2B/E4B, solo si aporta una comparación útil después de las primeras dos variantes.

Registrar dos resultados para no mezclar tareas: primero el texto crudo del ASR; luego el texto después del postprocesamiento. Así se puede ver si cambió el reconocimiento o solo la presentación.

### Qué medir

- **WER y CER** respecto de la referencia humana, antes y después del postprocesamiento.
- **Palabras cambiadas:** inserciones, borrados, reemplazos y reordenamientos que intente el LLM, aunque el guard las rechace.
- **Puntuación y tildes:** evaluación por separado, ya que un WER normalizado suele ignorar esos cambios.
- **Nombres, términos técnicos y cifras:** tasa de conservación y corrección según el glosario.
- **Falsas correcciones:** cambios que parecen gramaticales pero alteran la intención.
- **Latencia:** mediana, p95, primera carga del modelo y tiempo de respuesta caliente.
- **Recursos:** RAM pico del proceso del runtime, CPU mientras corrige y consumo en reposo.
- **Fallback:** comprobar que desconectar el servidor conserva la salida local sin bloquear el dictado.

### Puerta de calidad

- En el modo protegido de Instant, mantener exactamente la secuencia normalizada de palabras. La regla actual de palabras debe seguir activa.
- No aceptar un modelo si empeora WER en el corpus general. Como referencia del proyecto para el benchmark FLEURS, el plan de precisión ya fija un límite de no más de `+0.005` WER frente al baseline. Aplicaría el mismo límite y reportaría resultados por separado en voz espontánea, nombres propios y fraseo técnico.
- No decidir con un promedio solo: revisar los errores y falsos positivos. Una mejora pequeña en puntuación no compensa introducir una palabra incorrecta.
- Medir una muestra con micrófonos, acentos y niveles de ruido variados. Los clips de voz real requieren consentimiento y no deben subirse a CI ni enviarse a servicios remotos.
- Separar calidad del modelo del tiempo de carga inicial y del costo de mantener otro runtime local.

Un LLM protegido que no puede cambiar palabras no debería presentarse como “más preciso para transcribir”. Si su valor es mejorar puntuación o acentos, la comparación debe demostrar ese beneficio específico y también el costo en tiempo y memoria.

## 7. Hoja de ruta propuesta

### Etapa A: cerrar la base sin cambiar el ASR

1. Completar el baseline del corpus privado consentido con las métricas que ya produce el harness.
2. Medir RSS total del panel y del daemon por separado, carga en frío, uso en reposo y consumo durante dictado.
3. Seguir usando cuatro hilos como punto de partida en la máquina medida; ampliar mediciones a equipos modestos y equipos con CPU reciente.
4. Verificar en entornos reales el ajuste de descarga del ASR en reposo, porque ahorra memoria a cambio de una carga fría aproximada de 1,56 s en la medición aislada.

### Etapa B: piloto LLM que no afecte al usuario final

1. Ejecutar una comparación offline con las transcripciones del corpus y con frases difíciles revisadas.
2. Probar primero los modelos 2B y 4B de Qwen3.5 en modo instructivo y con respuesta corta.
3. Mantener el guard de palabras, la temperatura cero y el fallback a reglas locales.
4. Reportar calidad de puntuación/tildes, intentos de cambio de palabras, latencia y RAM. No cambiar defaults a partir de una demo manual.

### Etapa C: mejorar la interfaz de integración

Si el piloto demuestra valor, añadir en Preferencias:

- Opciones simples: “Apagado”, “Modelo local ligero” y “Modelo local de más calidad”.
- Estado de conexión y nombre del modelo detectado.
- Acción de probar endpoint que envíe una cadena fija de prueba, no datos del usuario.
- Aviso visible cuando el endpoint configurado no es local.
- Timeout breve y fallback, con aviso no invasivo ante fallo repetido.

No empaquetar el modelo ni descargarlo sin consentimiento explícito. Mantener el modelo opcional y administrado por el runtime elegido por el usuario.

### Etapa D: considerar un nuevo ASR solo con evidencia

Si los errores restantes son sustituciones o vacíos de voz, se debe probar otro modelo ASR que soporte español con audio idéntico y mismo hardware. La precisión actual ya tiene un arnés y puertas de regresión. No mezclar esa comparación con un LLM que solo recibe texto ni atribuir al corrector recuperaciones que nunca escuchó.

## 8. Decisión recomendada

1. **Conservar Parakeet TDT v3 int8 como ASR base por ahora.** La evidencia local favorece su velocidad; la precisión real debe seguir creciendo con un corpus más amplio.
2. **Probar Qwen3.5 2B Q4_K_M y 4B Q4_K_M fuera del flujo normal.** El primero explora el mínimo costo; el segundo explora una posible mejora de formato con mayor tamaño.
3. **No cambiar el guard de palabras ni enviar audio al LLM.** La protección actual coincide con el objetivo de no sacrificar fidelidad.
4. **No incluir el LLM en el paquete ni activarlo por defecto.** Mantenerlo optativo mantiene pequeño el instalador y deja elegir según el equipo.
5. **Priorizar el onboarding, el estado del endpoint y la prueba con métricas.** Esas mejoras reducen confusión sin añadir carga de modelo a quienes no necesitan el LLM.

## Fuentes externas consultadas

- [Sherpa-onnx: Parakeet TDT v3, idiomas y uso](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html)
- [Modelo Parakeet TDT v3 int8 y tamaño publicado](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/tree/main)
- [Qwen3.5: etiquetas y tamaños cuantizados](https://ollama.com/library/qwen3.5/tags)
- [Tarjeta oficial de Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Gemma 4: etiquetas y tamaños en Ollama](https://ollama.com/library/gemma4/tags)
- [Google: tarjeta oficial de Gemma 4](https://ai.google.dev/gemma/docs/core/model_card_4?hl=es)
- [Ollama: compatibilidad con Chat Completions](https://ollama.com/blog/openai-compatibility)
- [llama.cpp: servidor HTTP y API compatible](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## Alcance y límites de este informe

Este documento analiza el código y los informes de precisión presentes en el checkout al 11 de octubre de 2026, y añade documentación oficial de los modelos y runtimes consultados para esta fecha. Los tamaños de modelo pueden cambiar entre etiquetas y cuantizaciones. No se ejecutó una comparación de Qwen3.5 o Gemma 4 sobre audio o transcripciones de Instant; por eso se presentan como candidatos a medir, no como una mejora ya demostrada. El informe no implementa cambios de código.
