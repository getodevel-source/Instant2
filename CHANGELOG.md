# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
El proyecto usa [versionado semántico](https://semver.org/lang/es/).

Instant2 se desarrolla en su propio repositorio, a partir de la base de Instant.
Las dos primeras entradas documentan versiones de Instant2. El historial que
sigue corresponde al Instant heredado y sus versiones no son releases de
Instant2. Para evitar colisiones con sus tags, Instant2 publica `instant2-vX.Y.Z`.

## [Sin publicar]

### Instalador de Windows
- El instalador de Windows es chico: instala la app y el runtime de llama.cpp
  y ya no lleva los pesos de los modelos (3,78 GB menos por instalación y por
  actualización). Los modelos llegan a la máquina por la app, que los descarga
  con su barra de progreso, se pueden cancelar, reanudar y verificar por
  hash. El tamaño exacto del instalador hay que medirlo sobre el artefacto
  real (app + runtime): la cifra de «2 MB» del commit `10ad7c9` salía de una
  compilación con el directorio de la app vacío y no describe lo que se baja.
- La primera vez que se abre el panel sin los modelos de reconocimiento, la
  app los baja sola (unos 670 MB). Es una sola vez por instalación: si se
  cancela o falla, el botón «Reintentar descarga» queda disponible y la app no
  vuelve a insistir sola. El asistente de primer arranque (`setup`) y el
  diagnóstico no descargan nada solo, y `INSTANT2_NO_AUTO_DOWNLOAD=1` apaga
  la descarga automática por completo.
- El modelo de lenguaje local (3 GB) se descarga a pedido, desde **Ajustes →
  Revisión del dictado y privacidad → Archivos del servicio local**, con su
  progreso y su botón de cancelar. No baja solo: son demasiados gigabytes
  para bajarlos sin permiso. Cuando la descarga termina, la app deja el
  servicio local configurado (runtime, dirección y alias): el proceso de
  dictado puede iniciarlo sin que haya que hacer nada más.
- Los modelos que dejó la versión anterior se reutilizan en vez de volver a
  bajarse: la app mueve los del directorio de Instant
  (`%LOCALAPPDATA%\instant\models`) al de Instant2
  (`%LOCALAPPDATA%\instant2\models`), sin copiar un byte y sin dejar
  duplicados. Si los dos directorios existen, se fusionan. Con
  `INSTANT2_DATA` configurado a mano, ese override gana y no se toca nada.
  Una entrada con el mismo nombre solo se borra del directorio anterior si el
  destino está verificado (los mismos archivos, con el mismo contenido): si el
  destino es una descarga a medias, la copia anterior se conserva con otro
  nombre (`<nombre>.legacy`) y se avisa en el log. Perder un GGUF que no se
  puede volver a bajar no tiene arreglo.

### Marca
- Nueva identidad visual: el símbolo son tres barras verticales («la señal»)
  y reemplaza al micrófono en el icono de la app, el instalador y el chip del
  panel. El micrófono sigue siendo solo un icono de la interfaz.
- Los logos están en curvas (Manrope 700, −0,035 em), así que el logotipo y
  los lockups se ven igual sin la fuente instalada. Hay variante para fondo
  oscuro y claro: en claro el símbolo va en tinta, porque el verde agua sobre
  blanco queda en 1,6:1.
- `design.md` es la guía de marca y `brand/` guarda los maestros SVG con sus
  derivados PNG, ICO y la imagen social. `python brand/tools/build_brand.py`
  los regenera desde los vectores; el `.ico` del build lo sigue haciendo
  `dictado/build_icon.py` con la misma geometría.

### Correcciones
- La descarga del modelo de lenguaje local ya no era un final de camino: el
  panel guardaba solo la ruta del archivo y el daemon se quedaba con «No hay
  un servicio de lenguaje configurado» después de 3 GB descargados. Ahora la
  descarga deja la config completa (runtime, `http://127.0.0.1:8080/v1`, alias
  y ruta del modelo) y el proceso de dictado puede usar el modelo.
- La marca «el LLM local ya quedó configurado» ya no se pone cuando no se
  configuró nada: con el modelo todavía sin descargar, el siguiente arranque
  vuelve a intentarlo. Y la reparación del runtime acepta el layout real
  (`llm-runtime\llama-server.exe`) además del viejo.
- Completar la descarga del modelo de lenguaje ya no guarda toda la config
  del panel: se guardan solo las claves que cambian, así que no se persisten
  ediciones sin confirmar ni se re-aplica el arranque con el sistema.
- Un GGUF que la persona eligió a mano no se reemplaza nunca, aunque el disco
  donde estaba no esté conectado. Y el archivo descargado se verifica por
  hash: uno de 3 GB con el tamaño justo pero corrupto no se reporta como
  «listo» y el botón de descarga vuelve a aparecer.
- La migración de los modelos del producto anterior ya no es un efecto
  secundario de «dónde están mis datos»: es un paso explícito del arranque
  (panel, daemon y asistente de setup), una sola vez por máquina, con marca y
  con lock para que dos procesos a la vez no se pisen.
- La suite de tests no toca más la máquina de quien la corre: cada módulo
  aísla `APPDATA`, `LOCALAPPDATA`, la configuración y los datos a un
  directorio temporal, y hay `scripts/run-tests.bat` y `scripts/run-tests.sh`
  para correrla con ese aislamiento. `INSTANT2_CONFIG_DIR` mueve el
  directorio de configuración (lo respeta la app, no solo los tests).
- Si seguís usando Instant (el producto anterior), avisá: sus modelos pasan
  al directorio de Instant2 y la app anterior deja de encontrarlos.
- El repositorio fija sus finales de línea (`.gitattributes`): el texto sale
  con LF en las tres plataformas y el helper de actualización y el script de
  Inno Setup siguen con CRLF, que es lo que necesitan. Antes, los hashes
  congelados del benchmark dependían de la máquina que hacía el checkout y el
  CI fallaba en Linux y macOS.
- Las rutas internas del repositorio ya no se reportan como rutas del perfil
  del usuario en los artefactos del benchmark: una ruta dentro del checkout es
  el checkout, no una fuga.
- El botón «Reintentar descarga» del reconocimiento ahora aparece después de
  una descarga fallida: el mensaje de error y el texto que mira el botón no
  coincidían.
- El servicio local de lenguaje usa el modelo descargado desde Ajustes cuando
  la persona no eligió ningún GGUF a mano, y el mensaje de error dice que se
  lo descargue desde Ajustes en vez de pedir un archivo que ya no existe.

## [0.1.6] - 2026-10-20

### Actualizaciones
- La actualización desde la app ya no abre una ventana de consola: el helper
  portable corre sin ventana y su paso a paso queda en
  `%APPDATA%\instant2\update.log`.
- El helper de actualización deja de mentir: si el binario nuevo no levanta,
  restaura el anterior, sale con código de error y dice qué pasó, en vez de
  imprimir "Listo: Instant2 actualizado".
- El actualizador verifica el resultado: lee el código de salida del
  instalador, adjunta el log de Inno Setup y confirma en el arranque
  siguiente que la versión que corre es la que se pidió. Si no coincide,
  avisa con el log.
- Un resultado de actualización viejo se descarta a las 24 horas: antes podía
  reportar un fallo de una actualización de días atrás en cada arranque.
- Todo toast de error queda registrado en `instant2.log` con su título y su
  detalle. Antes, el error que veía el usuario no dejaba rastro.
- El helper reabre el panel aunque la actualización falle, para que el
  resultado se vea sin tener que abrir la app a mano.
- La app ya no promete un reinicio del dictado que no ocurrió: el mensaje
  dice lo que pasó de verdad.

### Instalador de Windows
- El script de Inno Setup **no compilaba**, así que el instalador estaba roto
  de una forma que ninguna prueba del repositorio podía ver. Tres fallos: la
  firma del `Check` usaba un tipo que no existe en Inno Setup 6, los
  comentarios de la sección de código usaban `;` (que en Pascal no es un
  comentario) y el `Check` tenía la semántica invertida, con lo que el
  instalador saltaba los modelos que faltaban y volvía a copiar los que ya
  estaban.
- El instalador reutiliza los modelos que el usuario ya tiene en vez de
  volver a copiar gigabytes: mueve los del directorio anterior al nuevo y
  solo copia lo que falta.
- La instalación silenciosa vuelve a abrir la app, para que el resultado de la
  actualización se vea en el momento.
- El panel arranca con Python 3.14 (antes la interfaz gráfica no abría).

### Herramientas de benchmark
- El agregador V2P acepta cobertura parcial explícita para informes exploratorios
  y bloquea veredictos de ganador/viabilidad de corpus completo en esas muestras.
- Nuevo benchmark Voice-to-Prompt (`dictado/bench/bench_v2p.py`): compara cinco
  estrategias (la ruta de producción tal cual, un control negativo de
  instrucción, directa, estructurada y en dos pasos) sobre un corpus sintético
  congelado, con modelo, estrategia y contexto elegibles de forma
  independiente. Es una medición aislada: no cambia el runtime de la app.
- Nuevo empaquetado ciego (`dictado/bench/prepare_blind_v2p.py`): arma el paquete
  de revisión cualitativa (filas ciegas, manifiesto de procedencia e
  instrucciones de evaluación) y la clave privada que permite volver a unir cada
  variante con su configuración una vez terminada la puntuación.
- Nuevo análisis emparejado (`dictado/bench/analyze_correction_runs.py`): compara
  dos corridas del benchmark de corrección caso por caso, con intervalos de
  confianza por bootstrap pareado (10.000 remuestreos, semilla fija), y rechaza
  los informes que no son comparables (corpus, perfil de sampling, prompts o
  casos distintos).
- El runtime del benchmark de modelos queda con dos candidatos comparables
  (`qwen3.5-4b-q4` y `bonsai2-9b-q2`); los cinco candidatos anteriores no se
  pueden volver a comparar en la misma corrida.
- El runtime acepta perfiles de sampling por nombre, una etiqueta de corrida y un
  subconjunto de casos, y anota en cada informe el hash del servidor, el hash del
  modelo, la build del servidor y la memoria del proceso.
- La salida generada del benchmark (informes, logs y las claves ciegas de la
  revisión) queda fuera del control de versiones: publicarla destruiría la
  revisión ciega.

### Validación
- Corpus congelado de 72 casos de Voice-to-Prompt
  (`dictado/bench/v2p_cases.json`), su rúbrica congelada
  (`dictado/bench/v2p_rubric.json`) y el manifiesto de hashes
  (`dictado/bench/v2p_references.sha256`): las referencias no se editan después
  de correr el modelo.
- Corpus de corrección expandido a 48 casos sintéticos
  (`dictado/bench/llm_cases_expanded_2026-10-16.json`) con su hash, y
  pre-registro de la evaluación
  (`dictado/bench/correction-eval-prereg-2026-10-16.json`): corpus, reglas de
  aceptación, orden de inferencia y límites quedan escritos antes de las
  corridas.
- El scorer de corrección (`dictado/bench/bench_llm.py`) reporta, además del CER,
  el WER normalizado sobre tokens sin tildes ni puntuación, el conteo de
  invariantes críticos violados y el de ediciones léxicas dañinas, y separa los
  casos con error de contenido de los que solo cambian puntuación o mayúsculas.

### Corregido
- El pulido del LLM acepta `min_p` y `repetition_penalty` en el passthrough de
  sampling que usa el benchmark: antes se ignoraban en silencio y las corridas
  no eran comparables con los flags del servidor. La producción sigue mandando
  solo el cuerpo determinista (temperatura 0).

### Documentación
- `AUDITORIA-LLM.md`, `BENCHMARK-MODELOS.md` y `VOICE-TO-PROMPT.md` pasan a
  `docs/`, donde ya vive el resto de los documentos largos del repositorio.

## [0.1.5] - 2026-10-15

### Mejorado
- Se puede elegir una tecla independiente para leer el texto seleccionado; no se
  permite repetir la tecla de dictado.
- Ajustes separa voz, uso de memoria, privacidad y actualizaciones; las opciones
  técnicas quedan agrupadas aparte.
- El instalador de Windows abre Instant2 y activa el inicio con Windows por
  defecto en instalaciones nuevas.

## [0.1.4] - 2026-10-15

### Corregido
- El helper define la extracciÃ³n independiente de PyInstaller antes de cada
  subproceso del actualizador, incluido el comando `stop`.

### ValidaciÃ³n
- La prueba de regresiÃ³n verifica que la variable de entorno se defina antes de
  detener el daemon, arrancar el daemon nuevo y reabrir el panel.

## [0.1.3] - 2026-10-15

### Corregido
- El helper de actualizaciÃ³n portable reinicia el binario onefile con una
  extracciÃ³n PyInstaller independiente; evita que v0.1.2 intente cargar
  `python312.dll` desde la carpeta temporal eliminada al cerrar la versiÃ³n
  anterior.

### ValidaciÃ³n
- La actualizaciÃ³n desde Ajustes descargÃ³ y verificÃ³ v0.1.2, pero su primer
  arranque fallÃ³ al cargar `python312.dll`; el rollback restaurÃ³ la versiÃ³n
  anterior.
- Una prueba de regresiÃ³n verifica que el helper fija la extracciÃ³n
  independiente antes de arrancar el daemon y volver a abrir el panel.

## [0.1.2] - 2026-10-15

### Corregido
- La revisión opcional con LLM conserva cantidades dichas con palabras y
  rechaza tanto los cambios de valor como su conversión a cifras.
- Los tags nuevos de release se publican al completar CI para que el
  actualizador integrado los encuentre automáticamente.

### Validación
- Las pruebas de fidelidad comprueban cantidades escritas con palabras,
  cantidades pasadas a dígitos y correcciones ortográficas que deben conservarlas.

## [0.1.1] - 2026-10-14

### Mejorado
- La interfaz identifica las funciones por su uso y oculta los nombres de los
  componentes locales y sus archivos.
- El modo automático informa que descarga en segundo plano y solicita confirmar
  la instalación y el reinicio.
- El README de la aplicación incluye capturas propias de Instant2 y pasos de
  instalación para sus tres plataformas.

### Validación
- Audita el flujo de actualización, con cobertura de descarga, SHA256, reintento,
  confirmación y rutas de instalación, además de la suite multiplataforma del
  workflow de release. El tag `instant2-v0.1.1` evita reutilizar tags heredados
  de Instant y el actualizador interpreta su versión como `0.1.1`.

## [0.1.0] - 2026-10-12

### Primera versi?n independiente
- Identidad, paquete, configuraci?n y datos propios de Instant2.
- UI redise?ada para el dictado mejorado, los ajustes y el estado del servicio LLM.
- Correcci?n de transcripciones con LLM y fallback local ante fallos o propuestas
  que no superan la comprobaci?n conservadora.
- Administraci?n opcional del runtime LLM local y estado local/remoto visible.
  Los modelos quedan fuera del instalador.
- Animaciones ligadas a estados reales y preferencia de movimiento reducido.
- Audio y texto dictado fuera de los registros por defecto.

### Validaci?n
- A?ade comprobaciones del flujo de dictado, runtime local, errores y privacidad.

## Historial heredado de Instant

## [0.6.1] - 2026-10-12

### Mejorado
- Dictado usa como texto final la corrección de Qwen, que ahora puede
  resolver errores claros del reconocimiento de voz. Si el servicio falla,
  Instant2 conserva la corrección local.
- La instrucción de corrección pide conservar intención, nombres, cifras,
  negaciones y requisitos, y evitar información inventada.

### Validación
- Añade una prueba que confirma que Dictado acepta una corrección de
  palabra de Qwen y mantiene el fallback local cuando el servidor falla.

## [0.6.0] - 2026-10-12

### Agregado
- Indicador de servicio local o remoto y lectura de CPU, RAM y GPU para orientar
  la selecci?n del modelo. Los modelos quedan fuera del instalador y se conectan
  mediante un servidor compatible configurado por la persona.

### Mejorado
- El procesamiento de audio sigue siendo local; al LLM se le env?a texto.
  El audio y las transcripciones no se agregan a registros ni al historial.
- El pulido LLM opcional puede mejorar puntuaci?n y graf?a sin debilitar la
  protecci?n estricta disponible para las rutas que la requieran.

### Validaci?n pendiente
- Prueba manual de captura y pegado en Windows y Wayland/Omarchy, adem?s de
  mediciones de precisi?n y uso de recursos en m?s de un equipo.

## [0.5.2] - 2026-10-12

### Mejorado
- Reduce memoria temporal durante el procesamiento de audio y libera los
  modelos tras cinco minutos sin dictados. El primer dictado posterior puede
  tardar un poco más mientras se vuelven a cargar.
- Reduce recursos opcionales incluidos en los paquetes congelados.

### Corregido
- Hace más fiable el envío de Ctrl+V en Windows y el indicador ahora confirma
  que el texto está copiado, sin afirmar que la aplicación de destino lo pegó.

### Documentación
- Añade una evaluación de tecnologías y modelos LLM locales.

## [0.5.1] - 2026-10-11

### Agregado
- Soporte experimental de atajo global en Wayland mediante el portal de
  accesos directos, incluido Hyprland en distribuciones como Omarchy.
- Evaluador opcional de audio real que informa resultados sin guardar ni
  publicar grabaciones privadas.
- Guías de accesibilidad, precisión y validación del release.

### Mejorado
- Panel adaptable a escalas de pantalla habituales, con controles más grandes
  y mejor contraste.
- Descargas del modelo con reanudación, cancelación, reintentos y revisión del
  espacio disponible antes de empezar.
- Flujo de release con firma opcional para los binarios, cuando el repositorio
  tiene configuradas las credenciales correspondientes.

## [0.5.0] - 2026-10-10

### Agregado (producción)

- Consentimiento de actualizaciones: `update_mode` (`notify` por defecto
  | `auto` | `off`); `INSTANT_NO_UPDATE=1` equivale a `off`. En `notify`
  el panel avisa con versión y notas y descarga solo con tu clic; en `auto`
  descarga y verifica en segundo plano; la instalación siempre pide tu
  confirmación y nunca corta un dictado sin aviso.
- Re-hash SHA256 del archivo justo antes de aplicar la actualización
  (además de la verificación en descarga); sin hash esperado no se instala nada.
- Rollback POSIX: el binario previo se respalda (`.bak`) y se restaura si el
  reemplazo falla el chequeo mínimo (existe + ejecutable).
- Permisos best-effort: directorios de config/datos `0o700`,
  config/`.bak`/pid/log/wavs `0o600` (sin romper Windows).
- Release: provenance atestada + SBOM publicados (OIDC de GitHub, sin
  secretos); paso «Sign binary» renombrado a hash sidecar; actions con SHA pineado.
- Panel: checklist de primera ejecución en Inicio, selector de VAD
  (silero/ten) y `blank_penalty` en Ajustes avanzados; errores de red/SO
  accionables en criollo en vez de excepciones crudas.
- LLM: bloquea `http://` no-loopback (solo loopback en claro); Authorization
  por config/env sin loguearla; el log guarda la clase del error, nunca la URL.
- Puerta honesta en docs: primera vez (~670 MB + internet + ~1 GB libre),
  permisos de micrófono por SO, SmartScreen/Gatekeeper sin firma, Linux solo
  X11 y accesibilidad en macOS; panel documentado como navegación lateral de
  4 vistas; binarios verificables por SHA256; desinstalación documentada
  (qué queda y `--purge`).

### Corregido (auditoría interna, 4 rondas)
- Lanzadores `scripts/` reparados tras la mudanza: `ROOT` en la raíz del
  repo (venv, paquete, modelos y `dist/` resuelven bien); `update.bat` usa el
  `instant-stop.bat` vecino; `run`/`status` verifican la línea de comando
  antes de dar una instancia por viva.
- Ciclo de dictado: tope de sesión en one-shot, hotkey que sobrevive a
  errores de callback, releases viejos descartados por `sid`, avisos en el
  overlay en vías de error, pre-roll rancio nunca transcripto, `mic_index`
  vencido con error visible, re-pulsación rápida sin perder el onset.
- Motor: offsets temporales con `rs`, confianzas del retry rescatado,
  `drops` por pieza, `merge_short_bounds` con tope, instancia VAD perdedora
  liberada, `vad_sil`/`min_dur`/`vad_model` cableados.
- Configuración y ciclo de vida: instancia única POSIX con `flock` sobre un
  solo `instant.pid`, `update --apply` bloqueado en checkout, guardado
  atómico con `.bak`, autostart macOS con `launchctl`, foco/cierre de panel
  verificados, `stop` que limpia el pid rancio.
- Panel y overlay: toasts con `dismiss` notificado (sin fugas, con tope),
  selects saneados con aviso, `roundRect` con fallback, `key_capture`
  cableada, diagnóstico que no reabre el modal, `PageUp`/`PageDown` y
  `CapsLock` configurables.
- Corrección y pegado: `bias` sin tokens dictados en logs, `por` suelto fuera
  de interrogativas, vecinas sin cruzar oración, `quemet` solo en commit,
  clipboard guardado/restaurado, Wayland detectado antes de pisar, regla C
  siempre aplicada.
- Suite: 210 pruebas en verde (4 omitidas); 3 ficheros script convertidos a
  `unittest.TestCase`; `setuptools>=77`; bench de bias ejecutable de una
  pasada.

## [0.4.1] - 2026-10-09

### Mejorado

- Regla C fonética: "Will"→build con vecinas por sonido
  ("fall"~"falló" + "driver"); nombre real protegido ("testamento de Will").
- Alises de voz real: `bull`→build, `ploin`→plugin.
- Lanzadores `.bat`/`.sh` movidos a `scripts/` (raíz limpia).

## [0.4.0] - 2026-10-09

### Mejorado (precisión y rendimiento del dictado)

- Frontend de audio: DC-remove + high-pass ~80 Hz antes del reconocedor;
  aviso visible si el micrófono satura (>2 % muestras al tope).
- Overlap de 0,5 s de audio real por borde de segmento + costura de la zona
  común al unir textos: menos onsets recortados y vacíos en cortes.
- VAD alternativo TEN-VAD (int8, 126 KB) opt-in: `instant setup --vad-model ten`
  o `DICTADO_VAD=ten`. Default sigue Silero; si falta el modelo cae a Silero.
- Confianza por decode (media de log-probs): el LLM opcional solo interviene
  en takes dudosos (< 0.85); el texto seguro se pega sin red.
- Restauración local del `¿` en preguntas con palabra interrogativa,
  determinista y sin red. El `¡` no se toca.
- Bench `bench_numbers.py`: barrido A/B Silero vs TEN-VAD (thresholds
  0.3/0.5/0.7, onset/offset vs ground truth).
- Bench `bench_wer.py` nuevo: WER español con FLEURS es_419 (60 clips,
  normalización ES, Parakeet v3 int8 CPU). Medido 2026-10-09:
  baseline 0.050, sin-overlap 0.060, sin-frontend 0.048,
  blank 0.2/0.5 0.049/0.047, TEN-VAD 0.047, RTF ~0.04x, 0 vacíos.
  Test pareado: 54/60 clips iguales; sin overlap empeoran 4 y mejoran 2
  (efecto concentrado en multi-segmento). Diferencias ±0.003 = ruido
  con n=60: defaults conservadores (overlap on, blank 0, Silero).
- `blank_penalty` cableado (config, `DICTADO_BLANK`, `--blank-penalty`;
  default 0 = comportamiento actual).
- Diccionario técnico general (`bias.py`): commit/build/deploy/staging/
  Qwen/GitHub/… con spotter fonético y triple vía. Regla C (siempre):
  casos medidos en voz real con 2+ vecinas EXACTAS ("Will"+"fallo"+
  "driver"→build) corrigen sin gate de confianza; sin vecinas no toca
  ("Will Walchar no molesta" intacto). Niveles take/palabra con gates
  para el resto; fonético endurecido (dist ≤1); aliases palabra-ES solo
  con vecina y nunca como verbo. Batería adversaria: 30 sanas intactas.
  Aliases de voz real: bakken→backend, station→staging (cond.),
  wild/will/wey/güey→build (cond.), crawlback→rollback, paracid→Parakeet.
  Bench `bench_bias.py`: 0.254 → 0.206 (−19 %). FLEURS-60: 0.050 = 0.050.

### Quitado (limpieza)

- Código muerto sin importar: `theme.py`, `lmrescore.py` (experimento
  archivado). Bench UI `bakeoff/` ya decidido, bats de autostart sueltos y
  spec huérfano. Docs de precisión fusionados en `docs/precision.md`.

### Verificado

- `python -m unittest discover -s dictado/tests`: 114 pruebas, 2 omitidas.
- `python dictado/bench/bench_numbers.py`: determinismo, RTF y A/B VAD verdes.
- `python dictado/bench/bench_wer.py --n 60`: tabla de variantes arriba.
- `python dictado/bench/bench_bias.py`: 0.254 → 0.206 con bias (−19 %).
- Batería adversaria: 30 frases sanas intactas (test permanente).

## [0.3.5] - 2026-10-09

### Mejorado

- La ventana del panel abre en 1100 × 600 y permite reducirse hasta 900 × 520.
- Se quita la sección independiente Voz local. La descarga inicial del modelo
  aparece como un bloque compacto en Inicio y desaparece al quedar listo.
- Se elimina la mención técnica a Parakeet y CPU de la navegación del panel.
- Se actualiza la captura de la portada de GitHub con este diseño compacto.

### Verificado

- Pruebas de navegación para la sección retirada y el acceso a la descarga.
- Smoke visual de las cuatro secciones con el tamaño de ventana predeterminado.

## [0.3.4] - 2026-10-09

### Mejorado

- Rediseño minimalista del panel en vistas independientes de Inicio, Micrófono,
  Ajustes, Vocabulario y Voz local.
- El estado de guardado, el arranque y la disponibilidad de voz/micrófono ahora
  coinciden con las acciones habilitadas. Se aclaran las pruebas, errores y
  perfiles vacíos, y se corrige el foco de navegación y diálogos.
- Se simplifica la portada de GitHub y se actualiza la captura principal.

### Verificado

- `python -m unittest discover -s tests`: 112 pruebas, 2 omitidas por plataforma.
- `python bench/web_panel_smoke.py --page all --width 1000 --height 640`: las cinco
  vistas cargan al 100 % en la ventana mínima y se recorre ajustes/vocabulario.

## [0.3.3] - 2026-10-09

### Mejorado

- El panel presenta el espectro de voz y alinea navegación, controles, estados
  deshabilitados y selección de micrófono para que cada sección sea más clara.
- Los perfiles y el sonido se gestionan con diálogos accesibles y respuesta
  inmediata; las opciones de solo lectura dejan de mostrar rutas internas.
- La selección de sección sigue el desplazamiento y la navegación desde la
  bandeja, con ajustes responsivos para ventanas más estrechas.
- El README de GitHub queda reducido a instalación rápida, privacidad y enlaces,
  con capturas actuales del panel y del indicador de voz.

## [0.3.2] - 2026-10-08

### Corregido

- El indicador de voz reemplaza el orbe y los giros decorativos por una onda
  compacta de 21 barras interpoladas desde las nueve bandas reales; el nivel
  general deja de aplanar la forma del espectro. Escucha, transcripción,
  confirmación y avisos comparten la misma cápsula, con entrada y salida breves.
- La ventana del indicador de voz reduce su altura de 224 a 140 px y el ajuste
  describe el estilo como espectro reactivo.

### Verificado

- Renderer WebEngine: smoke manual de escucha, transcripción, confirmación y
  aviso; carga al 100 % y capturas de 480 × 140 px.
- `python -m unittest tests.test_overlay_web -v`: seis pruebas pasan.

## [0.3.1] - 2026-10-08

### Agregado

- Navegación lateral para Inicio, Micrófono, Ajustes, Vocabulario y Voz local;
  el panel también expone opciones avanzadas de rendimiento y apariencia.
- El overlay orbital reacciona con nueve barras suavizadas a partir del
  espectro del micrófono y distingue escucha, transcripción, pegado y avisos.
- El chequeo diario descarga en segundo plano el paquete de una versión nueva,
  verifica el SHA256 y muestra progreso, reintento y una acción explícita para
  actualizar y reiniciar.

### Corregido

- El puente QWebChannel del panel vuelve a despachar las acciones a Python;
  el medidor toma el nivel publicado por el backend.
- El estilo orbital queda como valor predeterminado, en línea con la guía de
  instalación.
- En Windows instalado, la espera a que cierre el daemon ocurre en segundo
  plano; si sigue activo, el panel muestra el fallo y permite actuar.
- El portable de Windows lleva su helper de actualización y pasa la ruta de
  su propio exe; el cambio conserva el respaldo y revierte si el daemon nuevo
  no arranca.
- En Linux/macOS, el binario verificado se copia junto al destino y se cambia
  de forma atómica, incluso si la carpeta temporal está en otro volumen.
- El guardado de configuración escribe y sincroniza un archivo temporal antes
  de reemplazar el JSON anterior; una serialización fallida conserva el último
  archivo válido, y si un archivo heredado está dañado recupera el `.bak`.
- Los alias de vocabulario de varias palabras ya no atraviesan puntuación ni
  consumen comas u otros separadores.
- Los portables Linux/macOS activan el arranque automático con su propio
  ejecutable; el diagnóstico de dependencias exige Python 3.12, igual que el
  paquete publicado, y evita instalar si el intérprete no cumple ese mínimo.
- La descarga de modelos calcula el espacio pendiente descontando las partes
  reanudables ya existentes. El panel y la política de privacidad aclaran que
  un endpoint LLM remoto recibe texto y vocabulario activo, nunca el audio;
  los logs de sesión dejaron de guardar el texto dictado. El guardado opt-in
  `DICTADO_SAVE_WAVS` queda documentado como almacenamiento de hasta 50 audios
  de sesiones sin reconocimiento.
- Se actualizaron las capturas del panel y del overlay para reflejar la UI
  nueva.

### Pruebas

- La suite completa pasa en Windows: 110 casos detectados, 108 ejecutados OK
  y 2 omitidos por plataforma (rama Unix de la UI y reemplazo POSIX). Los smokes del panel y
  overlay web abren al 100 %; el portable Windows también se construyó y su
  panel congelado abrió correctamente.

## [0.3.0] - 2026-10-08

Toda la UI pasa a web (QtWebEngine), elegida con el bake-off de
[`docs/bakeoff-ui.md`](docs/bakeoff-ui.md).

### Cambiado

- **El overlay del daemon dibuja en web.** La ventana es la misma de siempre
  (transparente, siempre encima, sin foco, abajo al centro del monitor activo)
  pero la página es `instant2_app/web/overlay.html`: los dos estilos de siempre
  (`classic` y `orbital`) y el contrato intacto (modo, mensaje, tecla, nivel,
  9 bandas y tono por QWebChannel). Si QtWebEngine no puede abrir, el daemon
  cae al respaldo Tk y lo deja en el log.
- **El panel de ajustes es web.** La ventana host (`qml/panel_web_host.qml` +
  `web/panel.html`) atiende el canal de instancia única y el mensaje de la
  bandeja igual que antes; el estado y las acciones viven en `PanelLogic`
  (`gui.py`), sin Qt, con los workers entregando por cola al hilo de UI. El
  chequeo diario silencioso, los toasts con acciones, el diagnóstico, la
  captura de tecla (`vk:N` en Windows), el vocabulario por perfiles y el
  guardado (con aviso de reinicio) se conservan tal cual.
- **Costo aceptado a cambio de unificar el stack:** tope de ~60 fps en el
  overlay, ~150 MB más de RAM del daemon y ~300 MB más de instalador (medido
  en el bake-off).
- El nivel de voz se coalesce del lado del consumidor (un frame JSON cada
  40 ms con lo último) y la página del overlay pausa su loop en idle: en
  reposo no hay rAF corriendo.

### Retirado

- El renderer Qt Quick del overlay (`_QtQuickOverlay` y los `.qml`
  `overlay.qml`/`overlay_orbital.qml`) y el panel QtWidgets completo; el QSS de
  `theme.py` (quedan sus tokens, que las páginas repiten y un test cuida).
- Pruebas atadas a los widgets (`test_qml_success` y las partes de widgets de
  `test_gui_settings`/`test_gui_lifecycle`), reemplazadas por equivalentes
  sobre `PanelLogic`.

### Agregado

- `dictado/bench/web_overlay_smoke.py` y `dictado/bench/web_panel_smoke.py`:
  smokes de las UIs web con capturas; no tocan el config ni el autostart
  reales.

### Pruebas

- `test_gui_lifecycle` y `test_gui_settings` ejercen `PanelLogic` sin Qt
  (mismo comportamiento de consumidor, sin ventanas).
- `test_panel_page` ancla el contrato de la página y de los hosts QML
  (`webChannel: channel` incluido: olvidarlo deja la página sin canal y en
  silencio); `test_overlay_palette` cubre la paleta del overlay web;
  `test_overlay_selection` la cadena web → Tk; `test_update` el flujo de
  actualización sobre la lógica.

## [0.2.1] - 2026-10-08

Instalador que compila en CI y desinstalación a prueba de carreras.

### Corregido

- **El instalador de Windows compila en CI.** Git-bash del runner reescribe
  los argumentos que empiezan con `/` (MSYS): ISCC recibía `DAppVersion=...`
  como nombre de script y abortaba. Ahora se invoca con `MSYS_NO_PATHCONV=1`
  y `MSYS2_ARG_CONV_EXCL=*`, así el `Instant-Setup.exe` sale con cada tag.
- **Desinstalación sin restos aunque el panel esté arrancando.** Si el `stop`
  propio corre antes de que el panel abra su ventana, el proceso aparece
  después y lockea los archivos. El desinstalador suma un cierre forzado de
  cualquier proceso que quede corriendo desde la carpeta instalada
  (verificado: 3 escenarios de carrera, carpeta borrada y sin restos).

## [0.2.0] - 2026-10-08

Misma UI en los tres sistemas, instalador de Windows y descarga de modelos de primera.

### Agregado

- **Instalador de Windows (`Instant-Setup.exe`), por usuario y sin admin.**
  Instala la variante en carpeta (onedir) bajo `%LOCALAPPDATA%\Programs\Instant`,
  con accesos directos, tarea opcional de arranque con Windows y desinstalador
  propio (aparece en «Aplicaciones instaladas»). El desinstalador frena el
  daemon antes de borrar y **no** toca configuración ni modelos. El
  `Instant.exe` portable sigue publicándose en la misma release.
- **Arranque medido: 20× más rápido con el instalador.** `--version` pasa de
  2,3-2,9 s (onefile, extrae 97 MB a temp en cada ejecución) a 0,13-0,25 s
  (onedir). El panel, el daemon y el CLI arrancan igual de rápido.
- **`instant stop`.** Frena el daemon y cierra el panel sin ventanas colgadas;
  lo usa el desinstalador (`[UninstallRun]`) y sirve para scripting.
- **Actualización según cómo esté instalada la app.** Instalada: el panel baja
  `Instant-Setup.exe`, frena el dictado y lo corre en modo silencioso (in-place,
  sin admin), y la app se reabre sola. Portable Windows: `instant-update.bat`
  de siempre (respalda `.bak` y verifica el hash). Linux/macOS: el binario se
  reemplaza en caliente (`instant update --download DIR --apply` o el panel) y
  el dictado se reinicia con la versión nueva.

### Cambiado

- **Descarga de modelos de primera.** Progreso con bytes y % reales, reanudable
  (`Range` sobre un `.part` que sobrevive a un corte), reintentos por archivo
  con descarte del contenido inválido, chequeo de espacio en disco antes de
  empezar (con colchón de 256 MB) y espejo configurable con
  `DICTADO_HF_ENDPOINT`/`HF_ENDPOINT`. Se retira la dependencia
  `huggingface_hub`: la descarga es propia, sobre stdlib.
- **Los tests ya no bajan 670 MB reales.** La cobertura de descarga corre
  contra un servidor HTTP local (`test_models_download.py`) y los tests de
  reanudación/espacio/espejo son deterministas; `test_regression` bajó de
  ~20-50 s a 0,4 s y la suite completa de ~30-50 s a ~5 s.
- **Un solo panel en los tres sistemas.** La ventana Qt de configuración,
  antes exclusiva de Windows, ahora es también la UI de Linux/macOS:
  `instant` y `instant setup` abren el panel en los tres sistemas; el
  asistente de terminal queda disponible con `instant setup --tui` o
  cualquier flag de CLI (scripts, SSH, servidores). Sin
  `DISPLAY`/`WAYLAND_DISPLAY` el panel no intenta abrirse y sugiere `--tui`.
- **El overlay del daemon es Qt Quick en los tres sistemas.** Antes solo
  Windows usaba el orbe QML y Linux/macOS dibujaban con Tk; ahora el mismo
  overlay (estilos `orbital`/`classic`, espectro por bandas y nivel reales)
  corre en los tres, y Tk queda como respaldo automático si Qt no puede
  abrir (X11 sin GL, instalaciones viejas). Los binarios de Linux/macOS
  empaquetan los `.qml` y los módulos Qt Quick.
- **Instancia única portable.** En Linux/macOS una segunda apertura no
  duplica la ventana: escribe el pedido (página, arranque del daemon) por un
  canal `QLocalServer` y sale; el panel contesta un acuse para que el
  lanzador no muera con el pedido en el buffer. En Windows se conserva el
  mutex + `FindWindow` de siempre.
- **`PySide6` y `Pillow` son dependencias de los tres sistemas** (la bandeja
  `pystray` sigue siendo solo de Windows). Los binarios de Linux/macOS del
  release incluyen el panel; `install.sh` intenta instalar las libs de Qt
  (`libxcb-cursor0`, GL, xkbcommon) y la tabla de dependencias suma esas
  filas, con pista de `apt` si el panel no abre.
- **La suite Qt corre en los tres sistemas en CI** (antes solo Windows):
  nuevo `tests/test_single_instance.py` cubre el canal con acuse y la rama
  unix de `run_gui`; `test_app_lifecycle` verifica los gates de arranque en
  win32/linux/darwin.

### Corregido

- **Los `.qml` del overlay viajan en el paquete.** Un `pip install` (incluido
  `install.bat`) armaba un wheel sin `instant2_app/qml/*.qml`: solo el ejecutable
  congelado los llevaba por `--add-data`. Ahora `package-data` los incluye, así
  que la instalación por pip también tiene el overlay Qt Quick.

## [0.1.2] - 2026-10-04

Avisos sin ventanas y daemon sin congelamientos.

### Cambiado

- **Avisos toast en vez de ventanas modales.** Pila abajo a la derecha con
  fundido y auto-cierre (y botones Descargar/Instalar en el propio aviso).
  Se migran 16 `QMessageBox` de guardar, modelos, daemon, pruebas y updates.
- **Sin congelamientos tras transcribir.** Cola acotada, VAD cacheado, decode
  seriado, `on_release` en worker, sesión máxima y `stop_daemon` sin
  powershell en el hilo UI.

## [0.1.1] - 2026-10-03

Primer release con actualización automática: se publica tageando `v0.1.1`
y el workflow construye los binarios de los tres sistemas con su `.sha256`,
pero solo después de que la suite pase en Windows, Linux y macOS y de que
`__version__` coincida con el tag.

### Cambiado

- **Interfaz más minimalista y prolija.** Los colores pasan a una sola fuente de
  verdad (`branding.PALETTE`) y las medidas, la tipografía y los tiempos de
  animación a `theme.py`, que arma la hoja de estilo: un cambio de marca ya no
  hay que perseguirlo por los archivos. La ventana gana marca en la barra
  lateral, encabezado con el nombre de la página, tarjetas y controles más
  aireados, y cada página tiene scroll, así que nada queda cortado al achicar.
- **La pastilla flotante sigue a la marca.** La transcripción deja el violeta
  por un cian de la misma familia que el verde agua; escuchar se anima con una
  onda fina y transcribir con un arco que barre sobre una guía tenue, en lugar
  de los ocho puntos. La pastilla es más chica, tiene sombra suave, entrada
  animada y los avisos se ajustan al largo del texto.
- El overlay Tk de Linux/macOS usa la misma paleta que el resto de la aplicación
  y el giro se apaga hacia el fondo en vez de cambiar de color.
- **Segunda pasada minimalista iOS.** La barra lateral se afina, los botones
  pierden el borde lateral y quedan en pastilla, los pesos tipográficos bajan
  de 700 a 500/600, la portada y las tarjetas respiran con más aire y el Inicio
  cambia la segunda tarjeta por una línea de estado tenue: era mucho cromo
  para dos datos.
- **La animación del dictado respira con tu voz y se ve nítida.** La onda del
  overlay pasa de 7 a 9 barras y el daemon le manda el nivel real del micrófono
  (~12 Hz): en silencio respira bajito, al hablar crece hasta ~1.6x, el anillo
  se expande y la pastilla vibra apenas. Además hay multisampleo (`layer
  samples 4`) en pastilla, micrófono, tilde y arcos, trazos un poco más
  gruesos, sombra de cinco pasos y textos en render nativo: se acabó lo
  pixelado en movimiento. El fallback Tk hace lo mismo a su escala.
- **Icono propio en la barra de tareas.** La ventana fijaba su icono pero
  Windows la agrupaba bajo Python (`pythonw.exe`); ahora declara su
  `AppUserModelID` y el icono de Instant aparece en la barra.
- **Ventana compacta de verdad.** Ancho total menor, barra de 188 px, portada
  fina (de ~210 a ~150 px) y las dos tarjetas de tecla/arranque fusionadas en
  una sola «General»: menos cajas para las mismas cuatro páginas.
- **El dictado aparece y se despide.** La pastilla nace creciendo con fade
  (escala + opacidad) y al terminar se achica con fade antes de esconderse, en
  espejo. Además vive **fija abajo al centro** de la pantalla, estilo asistente
  de voz: siempre en el mismo lugar, sin perseguir al cursor ni tapar el campo
  donde se escribe. El fallback Tk también centra abajo y funde entrada/salida
  con el alfa de la ventana.
- **Animación del dictado más expresiva.** La entrada nace con pop suave
  (escala desde 0.82 con leve sobreimpulso + fundido) y la salida se despide
  igual, en espejo. La onda en silencio apenas respira y al hablar se estira
  hasta ~1.7x con respuesta más rápida (nivel cada 60 ms, ganancia 5x); el
  anillo y la pastilla acompañan con más amplitud. El giro de transcripción
  gana trazo grueso, vuelta más rápida (900 ms) y un pulso central que marca
  que está pensando.
- **La pastilla respeta tu monitor de trabajo.** Sigue fija abajo al centro,
  pero del monitor donde está el cursor (el campo que dictás), no siempre del
  principal. Misma posición relativa, pantalla correcta.
- **Onda más sensible.** Nivel cada 50 ms con ganancia 6x: en silencio apenas
  respira (base 0.35x) y al hablar se estira hasta ~1.85x, con anillo,
  pastilla y brillo acompañando con más amplitud.
- **La onda ahora es un analizador de espectro real.** El daemon calcula por
  FFT la energía en 9 bandas logarítmicas (80 Hz..7.5 kHz) con techo
  adaptativo por banda: se autoajusta a tu mic y tu distancia, así hables
  bajito o fuerte la onda usa todo el rango. Cantá grave y se mueven las
  barras del centro; silbá agudo y saltan las de los bordes. Verificado con
  tonos sintéticos (150 Hz vs 4 kHz) y silencio en cero.
- **Fidelidad total.** Dos causas del movimiento raro, corregidas: en silencio
  se mandaban bandas normalizadas contra un techo colapsado (bailaba solo) —
  ahora por debajo del piso medido (0.006) se mandan ceros; y el reposo se
  aplana solo por barra (1-2 px) en vez de oscilar a 10 px. Además el espectro
  usa una ventana corrediza de ~60 ms (los bloques son de 10 ms y no resolvían
  graves) y la puerta de silencio se calibró contra el mic real.
- **Aparición con elevación.** La pastilla nace 14 px abajo y sube con el
  pop+fundido, y al terminar se va bajando 12 px mientras se achica y se
  funde. Mismo tratamiento en el fallback Tk (deslizamiento + alfa).
- **Transiciones que sí se ven.** Entrada más marcada (22 px en 500 ms con
  sobreimpulso estilo spring + fundido de 300 ms) y salida alargada a 300 ms
  con 18 px de recorrido: las salidas de 150-200 ms se sienten pero no se ven.
- **Gesto firma.** La entrada sube 34 px en 600 ms con un anillo que se
  expande una sola vez; la salida baja 26 px en 350 ms.
- **La despedida sí se ve ahora (dos bugs).** La ventana se fundía por su
  cuenta en 170 ms y tapaba la salida de 350 ms: parecía que desaparecía de
  golpe. Ahora solo la pastilla se funde. Además el ping nunca llegaba a
  mostrarse (un `PropertyAction` dentro de un paralelo se aplica al inicio):
  pasó a secuencia y se verificó con muestreo temporal — entrada y 34→0 con
  opacidad 0→1 y ping visible que se oculta; salida y 0→26 con fundido.
- **Cinco gestos premium.** Morph onda→tilde (la marca nace con sobreimpulso
  y el texto entra con fundido); ripple verde de confirmación al pegar;
  resplandor que respira con tu volumen; insignia de tecla que se aprieta
  mientras la mantenés; giro de transcripción con doble estela. Además el pop
  de la pastilla ahora crece desde el centro (origen de transformación).
  Criterio Apple HIG: entrar con ease-out, salir con ease-in, y que la
  despedida dure lo suficiente para registrarse.
- **Gesto firma: ping de aparición y despedida marcada.** La entrada sube
  34 px en 600 ms con un anillo que se expande una sola vez; la salida baja
  26 px en 350 ms. Recorridos y tiempos que no se pueden perder.
- **Criterio high-end: menos gestos, un solo lenguaje.** Se retiraron el ping,
  el ripple, el resplandor, las estelas, el squash de la tecla y los
  sobreimpulsos: juntos hacían ruido. Quedó un sistema coherente con curva
  bezier propia (0.32, 0.72, 0, 1) —entrada y tilde—, onda de voz como única
  protagonista, giro de un solo arco y salida en espejo. Origen de escala al
  centro.
- **Auditoría visual con capturas reales.** Se montó render offscreen de las
  4 páginas y se corrigió lo hallado: el texto de "Reiniciar con cambios" se
  recortaba (botón más alto con más padding y texto acortado a "Reiniciar"),
  se quitaron los subtítulos del encabezado (duplicaban la página), el
  subtítulo de la tarjeta de micrófono y se acortó "Probar 3 s" a "Probar".
  La portada creció a 160–196 px para que entren los dos botones sin
  apreturas. Se conserva todo texto funcional o exigido por tests.
- **Alternativa Orbital desde cero.** Segundo diseño del overlay con lenguaje
  propio (orbe concéntrico: tres anillos de graves/medios/agudos + núcleo que
  late, sin barras) sobre el mismo motor de voz real y la misma paleta
  (cubierta por tests). Se elige con `overlay_style: classic|orbital` en la
  config (o `DICTADO_OVERLAY`) y la actual queda intacta como default.
- **Orbital sin textos.** Fuera la insignia de tecla y el "Copiado": el gesto
  confirma, no hace falta escribirlo. Solo quedan los avisos de error, que
  son funcionales (sin ellos nunca sabrías por qué no se pegó nada).
- **Transiciones más ágiles.** Entrada 600→420 ms y salida 380→280 ms con el
  mismo idioma (bezier propia, recorridos intactos). El fallback Tk acompaña
  con pasos más grandes.
- **Calma premium.** Se retiró el pop por sílaba y se contuvieron todos los
  recorridos (entrada 24 px/380 ms, salida 24 px/260 ms, onda con tope 32 px,
  anillo y pastilla con gestos mínimos). La voz y el tono siguen reales, pero
  la pastilla susurra en vez de actuar.
- **La onda tiene tono.** El daemon estima tu tono por
  autocorrelación (verificado: 120 Hz→0.25, 220 Hz→0.63, 350 Hz→0.91) y tiñe
  la onda dentro de la familia de marca (verde agua→hielo). El micro-pop por
  sílaba se probó y se retiró en la pasada de calma.
- **Una sola pantalla.** Se eliminaron la barra lateral y las 4 páginas: todo
  (estado, micrófono, tecla, vocabulario, modelos, guardar) en una columna
  centrada con scroll. Los modelos quedaron en una línea sin sección y el
  Diagnóstico subió al encabezado. Navegar a una sección hace scroll.
- **Vocabulario en tabla.** Chau texto crudo con tabuladores: cada término es
  una fila con Término, Así se escucha (| separa variantes), Sonido ≈ y ✕,
  más botón de añadir. Misma semántica (incluido el `~` de sonido) y mismo
  guardado; el formato viejo sigue entendiéndose al cargar.
- **Robustez de tests Qt.** La tabla emite señales al destruirse: se
  desconecta en `closeEvent` y los tests destruyen ventanas con `del` +
  colecta explícita fuera de los pumps (el GC cíclico a mitad de pump
  abortaba Qt en offscreen).
- **Sin alturas fijas frágiles.** La portada ya no tiene altura máxima (a
  150 % de escala o ventana angosta el título ocupa dos líneas y nada se
  recorta), la insignia de tecla tampoco, y las filas de la tabla se
  autoajustan. Auditoría con capturas a 100 % y 150 %.
- **Apaisado de dos columnas.** La página ocupa todo el ancho: héroe arriba y
  dos columnas debajo (izquierda ajustes, derecha vocabulario ancho). Ventana
  inicial 1200×720, mínima 1000×640. Adiós columna chata.
- **Viewport que no se rompe al redimensionar.** El scroll es la ventana
  directo, sin envoltorios: antes el contenido quedaba en negro tras
  redimensionar. Verificado con stress de tamaños.
- **Vocabulario que no se rompe ni se pierde.** La tabla crece con las filas
  (hasta ~7 visibles), añadir enfoca y lleva a la fila nueva, y cada Guardar
  respalda la config anterior (`config.json.bak`).
- **Minimalismo pedido.** Diagnóstico fantasma abajo centrado; combos que no
  cambian con la rueda; interruptores pill; la voz sin nombres; sin campo LLM
  (vive en config/CLI); sin pastilla verde ni textos de estado; fila de
  modelos solo si falta descargar; ✕ circular con aire.
- **Sonido ≈ explícito.** La columna Sonido deja el pill sin perilla por un
  botón ≈: tenue apagado, teal encendido. Sin ambigüedad.
- **La ✕ con aire de verdad.** El margen interno del viewport recortaba el
  área pintable (él cortaba la cruz); se quitó y la columna tiene ancho fijo
  con aire. Verificado con zoom al píxel.
- **Columnas blindadas.** Sonido (78) y ✕ (62) con ancho fijo: ningún ajuste
  las puede apretar ni recortar, ni al mínimo de ventana.
- **Siete pedidos de minimalismo.** Diagnóstico fantasma sutil; la rueda ya no
  cambia los combos (scrollea la página); arranque y Sonido con interruptor
  pill; sin nombres de modelos en la UI ("la voz"); sin campo de LLM visible
  (vive en config/CLI); fuera la pastilla verde (solo un punto de estado); ✕
  circular para quitar términos.
- **Botones de vocabulario rehechos.** El pill `≈` y la `✕` se recortaban
  arriba/abajo (`resizeRowsToContents` dejaba filas más bajas que el botón y la
  fuente desbordaba) y la `✕` mostraba un cuadrado detrás (el envoltorio
  heredaba el fondo global de `QWidget`). Ahora: filas de 46 px mínimo
  garantizado, botones 30/28 px con fuentes contenidas, envoltorios
  transparentes y sin foco visible. Verificado con tests Qt y captura real.
- **Overlay Orbital afinado.** Velo y escenario traslúcidos (se notan también
  en fondos oscuros), voz contenida en dos pasadas, núcleo más chico, flash de
  despedida eliminado (mic/anillos/tilde ya no reaparecen en `idle`),
  composición global con `compositionScale` (0.56) y pastilla 16 px más abajo
  (`_OVERLAY_BOTTOM_GAP` 60). Arco de transcripción apenas más nítido; el
  movimiento (entrada/salida/tilde/giro) intacto.
- **`instant-run.bat` ya no confunde la ventana con el daemon.** Su chequeo de
  duplicados matcheaba cualquier `pythonw` con "instant" (incluida la ventana
  de ajustes) y se negaba a arrancar; ahora solo mira `instant2_app run`.
- **Actualización desde GitHub sin hacerlo a mano.** `instant update`
  consulta el último release y compara versiones; con `--download` baja el
  asset y lo verifica por SHA256 (sidecar `.sha256` publicado por el
  release). `instant-update.bat` frena todo, respalda el exe anterior y lo
  cambia. La ventana trae «Buscar actualizaciones» al lado de Diagnóstico.
  Sin sidecar no hay instalación. El workflow valida versión==tag y publica
  los hashes.
- **Actualizador con modales mínimos y red de seguridad.** La ventana chequea
  sola una vez por día y el botón se viste de primario si hay versión (sin
  modales); la descarga es atómica (`.part` + rename) y el instalador
  re-verifica el hash, restaura el `.bak` si el daemon nuevo no levanta y
  reabre la ventana. Sin hash o sin sidecar no instala nada.

### Corregido

- **Dictado más consistente ante cortes del VAD.** La inferencia sobre el
  recognizer compartido se serializa (el decode concurrente producía vacíos
  intermitentes), los segmentos de menos de 1.5 s se fusionan con su vecino
  y todo segmento vacío con audio fuerte se reintenta con ±1 s de contexto,
  con último intento sobre el audio completo. Solo actúa en la vía de fallo:
  sin vacíos no hay decodes extra.
- **Primeras palabras menos recortadas.** El VAD arranca con 0.20 s de voz
  (antes 0.25 s) y el padding por segmento sube a 0.3 s (ajustable con
  `vad_pad` en config). El log ahora muestra los cortes, el pico y la
  duración por segmento, y la latencia de apertura del micrófono.
- **Cola del dictado completa.** Al soltar la tecla se esperan hasta 0.3 s
  drenando la cola antes de cerrar el stream: lo que ya estaba en los buffers
  de audio se perdía aunque estuviera grabado.
- **Takes bajitos al mismo nivel.** Cada segmento con voz pero RMS menor a
  0.03 se normaliza hasta RMS 0.06 (tope 8x, solo boost, con `g=` en el log):
  hablar bajito o lejos ya no cambia la hipótesis.
- **El inicio ya no depende de abrir el micrófono.** Captura continua con
  pre-roll de ~0.45 s: un solo stream abierto toda la sesión, cada dictado
  arranca con lo ya grabado. Si el stream muere, cae al one-shot de siempre.
- **Fallos guardados para medir (opt-in).** Con `DICTADO_SAVE_WAVS=<carpeta>`,
  cada audio que el modelo deja vacío se guarda con su meta (duración, pico,
  cortes): corpus de voz real para dejar de suponer. Tope 50 archivos.
- **Vocabulario visible.** Cada sustitución del perfil queda en el log
  (`vocab: '...' -> '...'`), para armar el perfil con evidencia.

## [0.1.0] — 2026-10-03

Primera versión con la aplicación completa. Todo el reconocimiento corre local,
en CPU; el audio nunca sale del equipo.

### Agregado

- **Dictado hold-to-talk**: se mantiene F9 (o la tecla elegida), se habla y al
  soltar el texto se pega en el campo enfocado. Pipeline: micrófono a 16 kHz →
  Silero VAD que corta frases → Parakeet TDT v3 int8 por segmento en paralelo
  (`sherpa-onnx`, `provider=cpu`) → portapapeles + Ctrl+V.
- **Panel de control en Windows** (PySide6): estado del dictado y páginas de
  Audio, Preferencias, Modelos y Diagnóstico. Una sola ventana por sesión; al
  cerrarla, el dictado sigue en la bandeja.
- **Icono de bandeja**: abre la ventana, configura micrófono y tecla, abre el
  diagnóstico o sale de Instant cerrando también el dictado.
- **Overlay de progreso**: estados por sesión (escuchando, transcribiendo,
  copiado) con animación, en Qt Quick en Windows y Tk en Linux/macOS. Nunca
  muestra el texto dictado.
- **Perfiles de vocabulario local**: cada término tiene su grafía preferida y
  las variantes que el motor suele devolver distinto. Las sustituciones son
  locales y exactas; no hay corrección difusa.
- **Emparejamiento por sonido** (opt-in con `~`): cubre las variantes que el
  motor inventa sin enumerarlas, incluidas las palabras que parte o pega
  («O en Pi» → OpenAI). Con red de seguridad para no tocar palabras corrientes.
- **Pulido LLM opcional** (apagado por defecto): con un `llama-server` local
  corrige tildes, puntuación y los signos de apertura `¿`/`¡`. Recibe texto,
  nunca audio, y se rechaza toda salida que cambie la secuencia de palabras.
- **Verificación de integridad de los modelos**: los ~670 MB de Parakeet y el
  VAD se comprueban con SHA-256 al descargar; un archivo corrupto se vuelve a
  bajar y un archivo a medias no queda en su lugar.
- **Arranque con el sistema** opcional, y una sola instancia del dictado aunque
  se abra dos veces.
- **CLI** en Linux/macOS: `instant setup`, `run`, `check` y `diagnostics`, con
  gestión de perfiles de vocabulario por flags.
- **Suite de pruebas** (143 comprobaciones) que corre en Windows, Linux y macOS
  en cada push y antes de publicar.

### Limitaciones conocidas

- **Los ejecutables no están firmados.** Windows SmartScreen va a avisar al
  usuario la primera vez; es esperable hasta que haya un certificado de firma.
- **No existe un modelo de puntuación en español** para sherpa-onnx (solo
  inglés y chino). Los signos de apertura dependen del motor y, si faltan, del
  pulido LLM.
- **Los hotwords no funcionan con Parakeet v3**: sherpa-onnx los tokeniza con
  un `bpe.vocab` de sentencepiece que el paquete del modelo no trae. Por eso las
  marcas se corrigen después del reconocimiento, no antes. Ver
  [`docs/motores.md`](docs/motores.md).
- **El corpus de evaluación era de voz sintética** y se retiró junto con la
  comparación de motores; la decisión de motor quedó documentada en
  [`docs/motores.md`](docs/motores.md).
