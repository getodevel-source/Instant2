# Auditoría de las ocho mejoras de Instant2

Fecha: 14 de octubre de 2026. Ejecución real en Windows con Python 3.12,
PySide6/QtWebEngine, Parakeet TDT v3 int8, Kokoro y Qwen 3.5 4B Q4_K_M.
Se conservan controles, atajos y formato de configuración. No hay funciones de
producto ni opciones nuevas. El rediseño previo se conserva.

## Estado de cierre

| Área | Implementación y evidencia | Pendiente |
|---|---|---|
| 1. Portapapeles | Restauración condicionada a contenido/secuencia, regresión reproducida y corregida | Teclas reales en campos de aplicaciones externas |
| 2. Latencia F9 | Sin espera del servidor que no se usa; presupuesto compartido y tiempos por etapa | Medición desde F9 físico hasta el campo externo |
| 3. Precisión | 96 audios públicos con ASR real, 48 correcciones con Qwen real y filtros de fidelidad | Voz espontánea y micrófonos/ruido representativos del usuario |
| 4. Recursos | Workers por señal, caché de espectro, hilos acotados y reposo/recarga medidos | Sesiones prolongadas en equipos distintos |
| 5. Lectura | Primera ventana anticipada, estado oportuno y aborto de salida; reproducción larga real correcta | F10 físico sobre selección externa |
| 6. Mensajes | Acentos, nombre del motor, instalador y residencia corregidos | Sin pendiente identificado en los cambios revisados |
| 7. Mantenimiento | Responsabilidades separadas y paleta web centralizada | Sin pendiente identificado en los cambios revisados |
| 8. Distribución | Build congelado, apertura real y modelos offline verificados; ciclo Inno aislado aprobado | Instalación personal exacta, cierre de sus procesos y otras plataformas |

La auditoría no está cerrada globalmente. Una prueba de lógica o una captura
controlada no demuestra la entrega de teclas del sistema operativo.

## 1. Pegado y propiedad del portapapeles

La implementación anterior restauraba el texto previo aunque hubiera aparecido
una copia nueva durante la espera posterior a Ctrl+V. Se reprodujo el fallo antes
de modificar el código. F9 y F10 comparten ahora `clipboard.restore_text()`, que
comprueba contenido y, en Windows, secuencia antes y después de leer. X11,
Wayland y macOS comprueban contenido y el resultado de sus herramientas.
Una inyección fallida deja el dictado disponible para pegado manual.

Siete pruebas enfocadas cubren copias concurrentes, texto idéntico con secuencia
nueva, lectura ocupada, fallo de teclado y backends POSIX simulados. Se conserva
el contrato previo de texto plano. La comparación y la escritura no son atómicas:
queda una ventana mínima de carrera; POSIX no distingue una copia nueva idéntica.

`bench/desktop_delivery_smoke.py` verifica el primer plano nativo antes de
inyectar. Su ejecución quedó pendiente: Qt informaba foco, pero Windows no daba
a la ventana el primer plano. Se añadió ese guard para evitar enviar pruebas a
una aplicación ajena. El controlador de escritorio falló repetidamente con
`Computer Use native pipe is unavailable`. No se cuenta como prueba aprobada.

## 2. Espera al soltar F9

`dictation.prepare_text()` separa preparación y daemon. El dictado sin LLM,
desactivado o externo no espera el arranque del servidor administrado. Cuando se
usa este último, arranque y solicitudes comparten el presupuesto; se mantiene
el plazo mayor para despertar un modelo dormido. Al agotarse, usa la corrección
local existente. El verificador recibe el tiempo restante, máximo dos segundos,
y no se inicia si ya no queda tiempo. Los timeouts de socket no garantizan un
tiempo total absoluto frente a cualquier servidor o respuesta parcial.

Se registran `asr_ms`, `correction_ms` y `paste_ms`, sin transcripción. Seis
pruebas enfocadas cubren estos caminos. La prueba antigua del overlay se hizo
determinista ejecutando sus dos workers diferidos explícitamente.

## 3. Precisión con audio público

El usuario no tenía corpus privado y autorizó obtener audio de Internet.
Se usaron los splits test de [FLEURS, es_419](https://huggingface.co/datasets/google/fleurs)
y [VoxPopuli, es](https://huggingface.co/datasets/facebook/voxpopuli), con origen,
referencias y SHA-256 guardados localmente. Las fichas declaran CC BY 4.0 y
CC0 1.0 respectivamente. El corpus no se incluye en el producto.

Selección determinista distribuida: 48 clips por corpus, 24 por género
declarado; VoxPopuli solo con referencias gold, máximo dos clips por hablante,
37 hablantes distintos. FLEURS no proporciona identidad de hablante en estos
registros. Conversión explícita con FFmpeg a PCM mono de 16 kHz y 16 bits.

| ASR real, cuatro hilos | Audio | Palabras | WER ASR | WER local | Mediana por clip |
|---|---:|---:|---:|---:|---:|
| FLEURS, 48 clips | 598,44 s | 1.248 | 4,49 % | 4,49 % | 0,484 s |
| VoxPopuli, 48 clips | 740,53 s | 1.957 | 9,04 % | 9,04 % | 0,512 s |

Cero transcripciones vacías. Carga inicial: 1,74 s. WER suma sustituciones,
inserciones y borrados sobre palabras de referencia, normalizando acentos,
caja y puntuación. Los tiempos excluyen captura humana y pegado.

| Qwen real, submuestra fija | WER de entrada | LLM anterior | LLM con filtros |
|---|---:|---:|---:|
| FLEURS, 24 clips | 4,72 % | 5,19 % | 4,72 % |
| VoxPopuli, 24 clips | 9,71 % | 9,71 % | 9,51 % |

El filtro determinista protege cifras escritas con dígitos y el recuento de
negaciones/restricciones, y rechaza inserciones de contenido ajeno antes del
verificador probabilístico. Se reforzó la instrucción de no reescribir por
estilo. Dos pruebas tabuladas cubren esos contratos.

Persisten regresiones individuales: una en FLEURS y tres en VoxPopuli en la
submuestra final. Las sustituciones siguen dependiendo del verificador; no hay
garantía para todos los nombres ni cantidades expresadas en palabras. La media
de corrección fue 0,54/0,71 s. Es una muestra pequeña de lectura y discurso
parlamentario; no demuestra precisión conversacional general.

## 4. Recursos y recuperación

`TaskRunner` despierta Qt al recibir resultados, eliminando la consulta de cola
cada 30 ms. Al cerrar descarta referencias pendientes. Se conservan los latidos
existentes de daemon/LLM. Dos pruebas cubren entrega y cierre. `SpectrumMeter`
reutiliza Hanning e índices de nueve bandas; dos pruebas comparan el resultado
con el cálculo anterior, tamaños, silencio y conservación del audio.

Kokoro usa el presupuesto existente de hilos (1–8, limitado por CPU), un hilo
entre operaciones y espera sin spinning de ONNX Runtime. No se añade un ajuste.

| Medición Kokoro | Antes | Después |
|---|---:|---:|
| Carga | 2,31 s | 1,80 s |
| Síntesis de 6,025 s | 1,11 s | 1,03 s |
| Hilos tras síntesis | 23 | 21 |
| CPU en reposo, intervalo 1 s | 6,2 % | 0 % |
| RSS tras síntesis / descarga | 526 / 75 MB | 525 / 74 MB |

Parakeet: 812 MB cargado, 55 MB descargado; CPU 0 % en los dos segundos
posteriores al primer segundo de reposo, que todavía registró 9,4 %. Recarga
más transcripción: 2,12 s, con salida idéntica para el mismo WAV público.

Qwen administrado en Vulkan0: 2.932 MB residente, 78 MB en reposo, CPU 0 %;
despertar más una solicitud: 2,48 s. Se comprobó el presupuesto de 60 s y la
terminación del servidor propio. El intervalo de reposo se redujo solo en la
configuración de la prueba. RSS no incluye necesariamente toda la memoria GPU.
Son ejecuciones puntuales con cachés locales; no son garantías para otros PC.

## 5. Lectura y parada

La primera ventana conserva hasta 120 tokens, manteniendo contexto y
anticipación; las siguientes conservan 300. Continúan el solapamiento y fundido
existentes. «Leyendo» se publica antes del primer bloque. Cancelar aborta
PortAudio para descartar audio encolado; la inferencia iniciada termina antes
de liberar recursos. Una prueba cubre aborto, ausencia de bloques posteriores
y terminación del productor.

Ejecución con Kokoro y salida PortAudio reales:

- Frase breve: 2,4 s de audio, 24 escrituras, cero underflows.
- Texto largo completo: 43,84 s, 442 escrituras, cero underflows. Primer audio
  a 1,89 s con el modelo cargado; cruza varias ventanas de síntesis.
- Cancelación: cinco bloques y aborto 84,9 ms después de solicitarlo. El método
  retornó 3,77 s después de la solicitud por limpieza de inferencia en curso.
- Caso largo comparable antes/después: primer audio de 3,56 a 1,81 s.

Underflows mide continuidad de entrega al dispositivo; no sustituye una
evaluación perceptual por oyentes de prosodia y empalmes.

## 6. Mensajes

Se repararon acentos dañados en avisos de dictado y configuración. Los mensajes
de terminal y registros nombran Parakeet. El panel explica los modelos y runtime
del instalador completo y la descarga de Kokoro según el modo de recursos.
El ajuste de hilos describe reconocimiento y lectura.

## 7. Estructura, diseño y revisión

Se extrajeron `clipboard.py`, `dictation.py`, `tasks.py`, `reading.py` y
`metering.py`, manteniendo el puente y contratos existentes; `gui.TaskRunner`
sigue disponible. Los compositores inyectan las paletas web desde
`branding.PALETTE`. El QML conserva un color inicial de respaldo.

Se aplicaron `high-end-visual-design` y la referencia Motion Framer al diseño
y pulido, `systemic-issue-triage` a causas y responsabilidades,
`cognitive-doc-design` al informe y `open-code-review-delegate` a la revisión.
OCR resuelve archivos/reglas; la revisión la realiza el agente sin enviar
el repositorio a un servicio de revisión externo.

La suite final, desde `dictado/`, `python -m unittest discover -s tests`, terminó
con **376 pruebas, OK, seis omitidas, salida cero**. También se ejecutan regresiones
antiguas al importar módulos. Los errores provocados deliberadamente quedan
en el log sin ser fallos de suite. Pyflakes no reportó problemas en los módulos
nuevos y módulos de runtime modificados comprobados.

Se repitieron 17 capturas del panel y 40 del indicador: cinco vistas, F9/F10,
ambos estilos, éxito, carga, error, movimiento reducido, zoom 125/150 % y ventana
mínima. Se inspeccionaron capturas, DOM, navegación y desbordamiento horizontal.
Hubo mensajes del compositor GPU al cerrar un proceso de revisión; las
comprobaciones finalizaron correctamente.

## 8. Distribución y alcance

PyInstaller produjo un onedir final en 41,7 s. El ejecutable real abrió el panel
con configuración de auditoría, proceso vivo, paleta resuelta y fuente local.
Es una comprobación offscreen, sin selección o teclas de escritorio.

`prepare_offline_bundle.py` verificó ocho archivos, 3,73 GiB de modelos/voz, y
preparó llama.cpp b11573 CPU/Vulkan. Rechazó reutilizar un payload no vacío;
los recursos ya verificados se copiaron al build final con el mismo manifiesto.

Se compila `installer/instant2.iss` de producción sin cambios. El ciclo Inno
aprobado usa un fixture con AppId/nombre distintos, modelos junto al destino de
auditoría y sin `Instant2 stop`, que podría cerrar la sesión personal. Incluye
instalación limpia, reparación de un ejecutable corrompido, desinstalación y
reinstalación. Los ocho archivos de modelos sobrevivieron a cada fase; la
configuración de prueba también. El ejecutable instalado abrió el panel en la
prueba offscreen. Los binarios de producción y sus SHA-256 están en el
manifiesto local.

Esta variante no demuestra la actualización exacta de la instalación personal,
ni el cierre global de daemon/panel. Esa orden puede detener una sesión activa;
se dejó intacta la instalación y el proceso personal y no se simuló su cierre.

Linux/Wayland/macOS tienen cobertura de lógica y recetas de build, sin ejecución
de instalación o teclas reales en esas plataformas.

## Evidencia local y reproducción

`.audit-eight/` y `.ui-review/` están ignorados por Git. No contienen audio del
usuario. Referencias e hipótesis del corpus público permanecen locales.

| Evidencia | Archivo |
|---|---|
| Suite completa cruda | `.audit-eight/integration-final-raw.txt` |
| Origen y hashes del corpus | `.audit-eight/public-corpus/sources.json` |
| ASR / corrección local | `.audit-eight/public-baseline.json` |
| Qwen antes / después | `.audit-eight/llm-quality-before.json`, `llm-quality.json` |
| Recursos | `.audit-eight/tts-resource-*.json`, `asr-resources.json`, `llm-sleep.json` |
| Reproducción real | `.audit-eight/tts-playback.json` |
| UI | `.ui-review/native-review.json`, capturas `final-*`, `voice-*` |
| Build / apertura | `.audit-eight/build-final.log`, `frozen-smoke/report.json` |
| Instalación aislada | `.audit-eight/installer-lifecycle.json` |
| Revisión de código | `.audit-eight/review-rules.json`, `review-coverage.json` (45/45) |
| Hashes del instalador de producción | `.audit-eight/delivery-manifest.json` |

Los scripts de evaluación permanecen en `.audit-eight/`: adquisición pública,
calidad ASR/LLM, recursos, lectura y distribución. PyArrow y psutil se instalaron
en `.audit-eight/deps`; no se agregaron dependencias del producto.

## Unidades de reversión

No hay migraciones ni cambio del formato de configuración. No se hizo commit.
Implementación y pruebas se retiran juntas, conservando las demás modificaciones
del usuario y el rediseño autorizado:

| Unidad | Archivos relacionados |
|---|---|
| Portapapeles | `clipboard.py`, `paste.py`, `selection.py`, pruebas de propiedad |
| Presupuesto y fidelidad | `dictation.py`, integración de `_job`, `llm.py`, pruebas de latencia/fidelidad |
| Recursos y lectura | `tts.py`, `metering.py`, `resource_policy.py`, hilos desde panel/daemon y pruebas |
| Workers y panel | `tasks.py`, `reading.py`, integración en `gui.py`, pruebas |
| UI | HTML/QML, tokens/compositores, fuente/licencia, spec, pyproject y workflow |

Reinstalar o reparar no equivale a rollback automático del actualizador.
No se afirma recuperación automática de una actualización interrumpida.
