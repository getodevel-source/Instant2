# Ciclo de vida de modelos y recursos en Instant2

Instant2 mantiene el motor de voz y el servicio de lenguaje bajo un único proceso de dictado persistente. El panel muestra su estado y permite pedir reintentos; cerrar su ventana no detiene el dictado ni apaga el servicio local.

## Uso rápido

En **Ajustes > Más opciones > Voz y rendimiento > Uso de memoria** elegí:

| Perfil | Comportamiento en reposo | Para quién |
|---|---|---|
| Más rapidez | Mantiene ASR, Kokoro y el servicio de lenguaje cargados mientras el proceso siga activo. | Equipos con memoria disponible y dictado frecuente. |
| Equilibrado (predeterminado) | Libera los modelos de voz y duerme el servicio de lenguaje tras 5 minutos sin actividad. | Uso general. |
| Ahorrar memoria | Libera los modelos tras 90 segundos. | Equipos con poca memoria o muchas aplicaciones abiertas. |

El próximo inicio del proceso de dictado aplica los cambios del perfil. El instalador de Windows incluye el runtime de llama.cpp; el modelo GGUF y los demás componentes de voz los descarga la app. El portable y los entornos de desarrollo pueden configurarlos por separado.

## Quién descarga cada modelo

El instalador ya no lleva pesos: cada componente llega a la máquina por su
propio camino, y solo el reconocimiento arranca solo.

| Componente | Peso | Quién lo baja | Cuándo |
|---|---|---|---|
| Parakeet int8 + detección de voz | ~670 MB | El panel, con `models.download_models` | La primera vez que se abre el panel sin los modelos. Una vez por instalación: si se cancela o falla, el botón «Reintentar descarga» es del usuario. |
| Voz de lectura (Kokoro + Dora) | ~326 MB | El panel, con `tts.ensure_model` | La primera lectura. |
| Modelo de lenguaje local (GGUF) | 3 GB | El panel, con `llm_model.ensure_model` | Solo cuando el usuario lo pide en Ajustes: son demasiados gigabytes para bajarlos sin permiso. |
| Modelos de la versión anterior | — | `paths.migrate_legacy_models` | Una vez, al resolver el directorio de datos: mueve lo que hay del directorio anterior (`%LOCALAPPDATA%/instant/models`) al de Instant2, sin copiar un byte y sin dejar duplicados. |

Las tres descargas reutilizan el mismo descargador: son reanudables (`.part`
con `Range`), verifican el SHA-256 antes de publicar el archivo, reintentan y
chequean el espacio en disco antes de empezar. Un `INSTANT2_DATA` explícito
desactiva la migración del directorio anterior: el override del usuario gana.

## Quién administra cada modelo

| Componente | Proceso que lo usa | Carga y descarga |
|---|---|---|
| Parakeet y detección de voz | Proceso de dictado | Se preparan al iniciar y se liberan según el perfil cuando el atajo de dictado queda inactivo. |
| Servicio local incluido | Proceso de dictado | Se inicia en segundo plano con los archivos configurados. Si admite `--sleep-idle-seconds`, libera el modelo en reposo; la siguiente solicitud lo despierta. Si no hay ningún GGUF elegido, se usa el que se descargó desde Ajustes. |
| Servicio externo local o remoto | Proveedor configurado | Instant2 envía solo texto. No intenta iniciar ni detener el servicio externo. Ajustes muestra si el destino está en este equipo o es remoto. |
| Voz de lectura desde el atajo | Proceso de dictado | Se carga al primer uso y se libera según el perfil. |
| Voz de lectura desde el panel | Proceso del panel | Se carga al iniciar una lectura y se libera según el mismo perfil. |

El panel no conserva un supervisor paralelo. Lee un estado pequeño, sin texto dictado ni indicaciones, que el proceso de dictado actualiza en la carpeta privada de configuración. El botón de reintento manda una señal local.

## Camino de un dictado con revisión de texto

1. El audio pasa al reconocedor local existente.
2. Instant2 aplica sus correcciones locales.
3. Si la revisión está habilitada, el proceso de dictado espera hasta 15 segundos a que termine el primer arranque del servicio administrado.
4. El texto se envía al servicio compatible que ya está configurado.
5. Si el servicio local está en reposo, recibe hasta 60 segundos para despertar y cargar el modelo.
6. Si el servicio falla o no termina de iniciar, se pega el texto preparado localmente. El dictado sigue disponible.

El servicio de revisión no recibe audio. El texto dictado y las respuestas del servicio no se escriben en los registros ni en el archivo de estado. La comprobación del texto conserva un límite breve para que una respuesta lenta no bloquee la sesión.

## Arranque, GPU y memoria

- El servicio local escucha solo en loopback; Instant2 no lo abre a la red local.
- Se consulta el servicio instalado para detectar dispositivos y memoria libre. Intenta usar un dispositivo acelerado y hace un único reintento con CPU si falla.
- Antes de intentar CPU, Instant2 estima el espacio necesario para el GGUF, el contexto y un margen para el escritorio. Es una advertencia preventiva, no una garantía de carga.
- Se usa una sola solicitud en paralelo y contexto de 4096 tokens para acotar memoria.
- Las versiones de llama.cpp con `--sleep-idle-seconds` pueden liberar los pesos sin cerrar el proceso. Versiones antiguas y proveedores externos conservan su propio comportamiento.

El primer dictado después de liberar los modelos puede tardar más: Parakeet se vuelve a cargar y, si corresponde, el servicio local sale del reposo. El perfil Más rapidez prioriza la latencia; Ahorrar memoria acepta ese arranque en frío.

## Qué medir en cada iteración

Para comparar cambios, registrar por separado:

- latencia fría del primer dictado después del arranque;
- latencia caliente p50/p95 de ASR y de la revisión de texto;
- RAM y VRAM al iniciar, durante inferencia y en reposo;
- tiempo de despertar del LLM dormido y tasa de fallback local;
- cortes de Kokoro durante lectura larga;
- comportamiento en CPU sin GPU y con 8 GB de RAM.

Los benchmarks sintéticos actuales ayudan a comparar versiones, pero no sustituyen las pruebas manuales en Windows de usuario ni en Wayland/Omarchy. No se debe reiniciar o cargar otro modelo en una sesión activa sin coordinarlo con quien está usando Instant2.
