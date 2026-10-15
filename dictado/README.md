# Instant2

Hold-to-talk offline en español. Mantené la tecla, hablá 60-120s, soltá y pegá.

Pipeline: mic 16kHz → VAD (frases) → Parakeet TDT v3 int8 offline
(`sherpa-onnx`, CPU) → portapapeles + Ctrl+V. Sin nube, sin GPU.
Idioma: Español (único). Sin selector: el setup muestra
"Español (único)" y guarda `lang: "es"` en la config para futuro;
el engine transcribe igual que siempre.

Errores conocidos del modelo (no son bugs de la app): nombres propios
raros pueden salir deformados (p.ej. "Instant2" → "instante"); siglas y
anglicismos se transcriben por fonética. Un perfil de vocabulario permite
corregir variantes reconocidas explícitamente; el pulido LLM por sí solo
ayuda con tildes y puntuación, pero no reemplaza el reconocimiento de audio.

En Windows, la vía recomendada es el instalador completo de la [última release](https://github.com/getodevel-source/Instant2/releases/latest) (`Instant2-Setup.exe`): por usuario, sin admin, con desinstalador y actualización in-place. Incluye Parakeet, Qwen 4B, Kokoro con Dora y llama.cpp. En una instalación nueva, deja activadas por defecto la apertura al terminar y el inicio con Windows; puedes desactivar ambas casillas. Para instalar sin conexión, descargá todos los archivos `Instant2-Setup*` y mantenelos juntos. El `Instant2.exe` portable y los binarios de Linux/macOS no incluyen los pesos.

> Los binarios están **sin firmar** por ahora: Windows SmartScreen muestra «Windows protegió su PC» la primera vez (elegí «Más información» → «Ejecutar de todas formas» solo si el instalador salió del enlace oficial); Gatekeeper en macOS puede pedirte que confirmes la apertura en Ajustes → Privacidad y seguridad. Esto no es un error de Instant2.

Para desarrollar el paquete desde `dictado/`:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -e .
```

Los ejemplos de CLI siguientes suponen ese entorno virtual activo. Los instaladores del repositorio usan el `.venv` de la raíz automáticamente.

### Windows

Sin dependencias del sistema. Si la tecla no responde en apps elevadas,
corré la terminal como administrador.

En Windows, las entradas WASAPI se abren en modo compartido con conversión
automática a 16 kHz cuando el dispositivo usa otra frecuencia (por ejemplo,
48 kHz), requerida por VAD y Parakeet. En micrófonos estéreo se capturan
hasta dos canales y se usa el de mayor energía.
El selector de configuración consolida alias repetidos entre host APIs y
prioriza WASAPI; si un backend ofrece varias entradas con el mismo nombre,
las conserva y las distingue por índice.
Al iniciar cada dictado se vuelve a resolver el micrófono guardado. Si el
inicio por callback falla, Instant2 intenta aliases del mismo nombre y deja
constancia del backend usado o del error en el log; el diagnóstico abre también
una captura por callback para comprobar ese mismo modo.

Al cerrar la ventana, la interfaz se cierra para liberar sus recursos; el
dictado sigue activo en la bandeja. Abrir Instant2 desde el icono enfoca una
ventana existente o abre una nueva, sin duplicar ventanas. Clic derecho ofrece
«Abrir ventana de Instant2», «Configurar micrófono y tecla», «Diagnóstico» y
«Salir de Instant2» (que detiene el dictado).
Windows puede ubicar el icono bajo la flecha de iconos ocultos; su visibilidad
fija se configura en la barra de tareas.

### Linux (X11 y Wayland)

X11 se instala con Debian/Ubuntu:

```bash
sudo apt install python3-venv libportaudio2 xclip xdotool libxcb-cursor0 libegl1 libgl1 libxkbcommon0
```

El panel web necesita las libs xcb/GL de Qt y el stack de QtWebEngine
(NSS, GBM, ALSA; el wheel de PySide6 no las trae);
`scripts/install.sh` instala estas dependencias en Debian/Ubuntu y Arch.

En Arch/Omarchy, mantené el sistema actualizado y ejecutá:

```bash
sudo pacman -S --needed python python-pip portaudio wl-clipboard wtype \
  xdg-desktop-portal xdg-desktop-portal-hyprland xcb-util-cursor \
  libxkbcommon mesa nss libxcomposite libxdamage libxrandr libxtst \
  cups atk alsa-lib qt6-wayland
```

Instant2 detecta Wayland automáticamente. El pegado usa `wl-clipboard` y
`wtype`; si el compositor rechaza el teclado virtual, la transcripción queda
en el portapapeles para pegarla manualmente. Las superficies nativas de
Wayland son el objetivo inicial; XWayland puede comportarse distinto.

#### Hyprland y Omarchy

El hotkey usa el portal XDG de atajos globales. Inici? Instant2 una vez y
consult? los atajos registrados:

```bash
hyprctl globalshortcuts
```

Busc? `Instant2: mantener para Dictado mejorado` y copi? el identificador
completo, que por defecto termina en `instant2-push-to-talk`. Asocialo a la tecla
configurada en Instant2 en tus bindings de Hyprland. En la configuraci?n Lua de
Omarchy, por ejemplo:

```lua
hl.bind("F9", hl.dsp.global("ID-DE-INSTANT2:instant2-push-to-talk"))
```

Reemplaz? la tecla por la configurada en Ajustes y el identificador por el que
devolvi? `hyprctl`. Recarg? Hyprland y reinici? Instant2. Si cambi?s la tecla,
volv? a consultar `hyprctl globalshortcuts` y actualiz? el binding con el nuevo
identificador.
El portal necesita el backend
`xdg-desktop-portal-hyprland` activo. Este soporte es experimental y requiere
verificación en la sesión concreta de Omarchy.

### macOS

```bash
brew install portaudio
```

El panel web abre sin dependencias extra. Autorizá micrófono y accesibilidad
(pegado por teclado) en Ajustes del Sistema. Teclas F: usá Fn+F9 si tu teclado
las mapea a multimedia.

## Uso

Con el entorno virtual activado:
```bash
instant2 setup   # panel de configuración (los tres sistemas)
instant2 run     # daemon: mantené la tecla, soltá para transcribir
instant2 stop    # frena el daemon (lo usa también el desinstalador)
instant2 check   # boot rapido: tecla + mic probe + warmup (<5s)
```

`instant2 setup` abre el centro gráfico PySide6 en los tres sistemas: navegación
lateral con cinco vistas (Inicio, Micrófono, Ajustes, Vocabulario, Leer texto). Inicio
muestra el estado del dictado, la tecla y los botones de iniciar/detener;
Micrófono trae selector y prueba de nivel (3 s); Ajustes trae la tecla y el
arranque con el sistema; **Vocabulario** trae tabla por perfil con término,
variantes (`a | b | c`), pill de sonido (`≈`) y botón de quitar (`✕`).
«Añadir término» agrega una fila en blanco lista para escribir. El panel indica
los cambios pendientes de guardar y, después de guardar, avisa si hace falta
reiniciar Instant2. El diagnóstico muestra el informe en una ventana
independiente. La ventana puede cerrarse sin detener el daemon; en Windows queda
el icono de bandeja, y en Linux/macOS el daemon sigue vivo hasta `scripts/instant2-stop.sh`.

**Leer texto** usa Kokoro local para sintetizar y reproducir texto en español
por CPU. La instalación completa de Windows trae el modelo y solo la voz Dora;
el portable y los paquetes de otros sistemas descargan unos 326 MB la primera
vez. La descarga verifica los archivos, se puede cancelar y reanudar. El texto
permanece en el campo de la pantalla y en memoria durante la lectura; no se
guarda ni se envía por red. Podés ajustar la velocidad y detener la
reproducción. El modelo Kokoro-82M usa Apache-2.0 y la implementación
kokoro-onnx usa MIT.

Sin ventana (servidores, SSH, scripts): `instant2 setup --tui` mantiene el
asistente de terminal; cualquier flag de CLI también lo usa.

`instant2` sin argumentos abre el panel y se asegura de un solo daemon activo.
Si ya hay una ventana abierta, una segunda apertura le pasa el pedido y sale
(Windows: mutex + `FindWindow`; Linux/macOS: canal `QLocalServer`).

El overlay dibuja en web (QtWebEngine) en los tres sistemas, con dos estilos
(`overlay_style` en la config): `orbital` (orbe con anillos que respiran con la
voz, por defecto) y `classic` (pastilla con barras y tecla visible). Si
QtWebEngine no puede abrir cae a Tk (X11 sin GL, instalación vieja sin
webengine): el daemon nunca se queda sin overlay. El renderer vive en
[`overlay_web.py`](src/instant2_app/overlay_web.py) y la pagina en
[`web/overlay.html`](src/instant2_app/web/overlay.html).

Para retocar la interfaz, los colores están en
[`branding.py`](src/instant2_app/branding.py) (`PALETTE`, única fuente de
verdad); las páginas [`web/overlay.html`](src/instant2_app/web/overlay.html)
y [`web/panel.html`](src/instant2_app/web/panel.html) reciben esos colores
desde sus compositores de página, sin mantener copias manuales.
`tests/test_overlay_palette.py` y `tests/test_panel_page.py` comprueban la
inyección de la paleta. La ventana del overlay se centra con
`WINDOW_SIZES`/`_OVERLAY_BOTTOM_GAP` en `overlay_web.py`; el panel vive en
`gui.py` (`PanelLogic` + host `qml/panel_web_host.qml`).

El asistente de terminal (`instant2 setup --tui`, o con cualquier flag de CLI)
hace, en orden:

1. **Modelos**: verifica Parakeet TDT v3 int8 (~670 MB) y VAD (~1 MB). El instalador completo de Windows los trae; en otros paquetes se descargan si faltan.
2. **Micrófono**: muestra nombre, backend y canales; permite medir nivel. Guarda nombre e índice como fallback si cambia el orden de dispositivos.
3. **Idioma**: español fijo.
4. **Tecla**: captura una tecla individual (Enter conserva la actual).
5. **Arranque con el sistema**: muestra el estado real y permite activarlo o desactivarlo.
6. **Prueba final**: abre el micrófono y calienta los modelos.

Modo no interactivo (conserva el micrófono y la tecla actuales):

```bash
instant2 setup --yes --key f9 --threads 4 --no-sound
```
`--mic` recibe el índice PortAudio mostrado junto al dispositivo; el setup guarda también el nombre para recuperarse si Windows cambia los índices.

Flags avanzados (solo flags, el interactivo no los pregunta):
`--threads` (default 4), `--sound`/`--no-sound` (default off),
`--llm-url` (default vacío = off), `--llm-token` (token Bearer, solo este
equipo), `--update-mode notify|auto|off` (default notify),
`--vad-model silero|ten` (default silero;
`ten` baja TEN-VAD int8 ~126 KB, VAD alternativo más preciso),
`--blank-penalty 0..1` (default 0; penalidad al blank del decode, solo con
WER medido — pasada de rosca inventa palabras),
`--context-profile`,
`--context-term "grafía=variante1|variante2"`,
`--context-remove-term "grafía"`, `--context-delete-profile`,
`--mic`, `--key`, `--no-meter`, `--no-probe`, `--yes`,
`--check-deps`, `--fix-deps`.
Arranque sin preguntar: `--autostart` lo prende, `--no-autostart` lo apaga;
con `--yes` pelado no se toca nada (se conserva lo que ya tenés).

## Dependencias

`instant2 setup` chequea las dependencias y te muestra `[OK]`/`[FALTA]`
en criollo. Con `--yes` solo avisa (nunca frena la config).
Con `--check-deps` muestra la tabla igual; con `--fix-deps` instala
lo que el SO deja y vuelve a chequear.

```bash
instant2 setup --check-deps
instant2 setup --fix-deps
```

| Qué | Cómo se detecta | Auto | Manual si falta |
|---|---|---|---|
| Python >= 3.12 | `sys.version` | no (instalalo vos) | python.org / tienda |
| pip | `import pip` | sí (`ensurepip`) | `python -m ensurepip` |
| numpy, sounddevice, sherpa-onnx, pyperclip | `importlib` | sí (`pip install`) | `pip install <paquete>` |
| PySide6 (panel, los tres sistemas) | `importlib` | sí (`pip install`) | `pip install PySide6` |
| Pillow (icono de ventana) | `importlib` | sí (`pip install`) | `pip install Pillow` |
| keyboard (Windows) / pynput (Linux X11 y macOS) | `importlib` | sí (`pip install`) | `pip install <paquete>` |
| dbus-fast (Linux Wayland) | `importlib` | sí (`pip install`) | `pip install dbus-fast` |
| xdg-desktop-portal / backend Hyprland | `xdg-desktop-portal` en PATH; backend de Hyprland instalado | sí (`apt`/`pacman`) sin pedir password al sistema | `sudo apt install xdg-desktop-portal xdg-desktop-portal-hyprland` / `sudo pacman -S xdg-desktop-portal xdg-desktop-portal-hyprland` |
| pystray (Windows) | icono de bandeja | sí (`pip install`) | `pip install pystray` |
| PortAudio Linux (`libportaudio2` / `portaudio`) | lib/ldconfig/dpkg | sí (`apt`/`pacman`) solo con sudo sin password o root | `sudo apt install libportaudio2` / `sudo pacman -S portaudio` |
| libs Qt linux (`libxcb-cursor0`, GL) | — (la pista la da el panel) | `install.sh` intenta `apt` | `sudo apt install libxcb-cursor0 libegl1 libgl1 libxkbcommon0` |
| xclip/xsel Linux X11 | PATH | sí (`apt`/`pacman`) solo con sudo sin password o root | `sudo apt install xclip` / `sudo pacman -S xclip` |
| xdotool Linux X11 | PATH | sí (`apt`/`pacman`) solo con sudo sin password o root | `sudo apt install xdotool` / `sudo pacman -S xdotool` |
| wl-clipboard Linux Wayland | `wl-copy` y `wl-paste` en PATH | sí (`apt`/`pacman`) solo con sudo sin password o root | `sudo apt install wl-clipboard` / `sudo pacman -S wl-clipboard` |
| wtype Linux Wayland | PATH | sí (`apt`/`pacman`) solo con sudo sin password o root | `sudo apt install wtype` / `sudo pacman -S wtype` |
| portaudio mac | `brew --prefix portaudio` | sí (`brew`) solo si tenés brew | `brew install portaudio` |
| Windows: nada del sistema | — | — | no hace falta nada |

En Wayland, el hotkey depende del portal global del compositor; ver [Hyprland y Omarchy](#hyprland-y-omarchy). Sin red no se instalan paquetes ni modelos; el setup informa qué falta. Para evitar preguntas en instalaciones automatizadas, definí `INSTANT2_UNATTENDED=1` antes de correr el instalador del repositorio.

## De dónde saca los modelos (INSTANT2_DATA)

Precedencia efectiva:

1. `INSTANT2_DATA` gana siempre (aunque no exista, se respeta tal cual).
2. Si no se define, usa el directorio de datos propio de Instant2
   (`%LOCALAPPDATA%/instant2/models` en Windows,
   `~/.local/share/instant2/models` en Linux,
   `~/Library/Application Support/instant2/models` en macOS).

Instant2 no busca modelos en el directorio actual ni junto a otra instalación;
así no reutiliza por accidente los datos de Instant. Los instaladores de
desarrollo del repositorio fijan `INSTANT2_DATA` a su propia carpeta `models`.

Sin modelos o sin mic, `setup` avisa y sigue (no crashea); `run` sin
modelos pide correr `setup` con red primero.

En **Ajustes** puedes elegir una tecla para Dictado mejorado y otra para leer la selección. Deben ser distintas; la captura acepta una tecla individual disponible en el sistema.
Config en `%APPDATA%/instant2` (win), `~/.config/instant2` (linux),
`~/Library/Application Support/instant2` (mac). Env `INSTANT2_*` pisa config
(`INSTANT2_MIC`, `INSTANT2_KEY`, `INSTANT2_READ_KEY`, `INSTANT2_THREADS`, `INSTANT2_SOUND`,
`INSTANT2_MAX_SEG`, `INSTANT2_VAD=silero|ten`, `INSTANT2_BLANK=0..1`,
`INSTANT2_LLM_URL`, `INSTANT2_LLM_TOKEN`,
`INSTANT2_UPDATE=notify|auto|off`
(o `INSTANT2_NO_UPDATE=1` para apagar el chequeo),
`INSTANT2_AUTOSTART=1/0`).

PID file: `run` escribe `instant2.pid` junto a la config al arrancar y lo
borra al salir limpio; los lanzadores `.bat` lo usan para stop/status
sin tocar procesos ajenos.

## Arranque con el sistema

En Windows, la casilla «Iniciar con Windows» está en el panel gráfico.
En Linux/macOS, el asistente terminal pregunta s/n después de la tecla,
muestra el estado real y qué va a crear; el default conserva el estado actual.
Si lo prendés, el daemon arranca solo y oculto en el próximo login; si lo apagás, no arranca más.

```bash
instant2 setup                       # interactivo: pregunta s/n
instant2 setup --yes --no-probe      # no toca nada, conserva lo que hay
instant2 setup --autostart           # lo prende sin preguntar
instant2 setup --no-autostart        # lo apaga sin preguntar
```

Qué crea en cada sistema (solo eso, nada más):

- Windows: acceso `Instant2 Dictado.lnk` en
  `%APPDATA%/Microsoft/Windows/Start Menu/Programs/Startup`, que apunta
  a `scripts/instant2-run.bat` del repo si lo encuentra (modo dev), y si no a
  `instant2.exe run` por PATH o `pythonw -m instant2_app run`. Se crea con
  el powershell que ya trae Windows, sin instalar nada nuevo.
- Linux: archivo `~/.config/autostart/instant2.desktop` (estándar
  freedesktop) que lanza `instant2 run` oculto con `nohup`.
- macOS: `~/Library/LaunchAgents/com.instant2.dictado.plist` que lanza
  `instant2 run` al iniciar sesión.

Para sacarlo alcanza con correr el setup y decir que no, o con
`instant2 setup --no-autostart`. Si lo borrás a mano de esas rutas vale
igual: el setup lo detecta apagado. Lo que vale de verdad es lo que hay
en el sistema, no la casilla guardada (`autostart` en la config es solo
un recordatorio; si no coinciden, el setup te avisa y deja lo del sistema).

## Contexto y vocabulario

En Windows, Preferencias permite crear perfiles y definir variantes exactas
que el reconocedor suele transcribir con otra grafía. Cada línea tiene la
grafía final, un tabulador y una o más variantes separadas por `|`:

```text
Instant2	instante | in stand
Parakeet	para kit
```

Solo se reemplazan esas variantes completas; no se fuerza una palabra del
glosario si el texto no contiene una variante.

### Emparejamiento por sonido (`~`)

Listar cada grafía que el motor inventa no escala: una marca como `Qwen` salió
«Quen», «Cuentres», «Quemet» y «cuen» en el mismo log. Un `~` delante de la
grafía activa el emparejamiento por sonido para ese término, y cubre esas
variantes sin enumerarlas:

```text
~Qwen	cuentres | quemet
~GitHub	hit hub
~Parakeet	para kit
```

Con `~`, `Quen`, `cuen` y `Cuen` se corrigen solos, igual que las palabras que
el motor parte o pega (`O en Pi` → `OpenAI`, `Git Hub` → `GitHub`).

La sustitución por sonido es conservadora a propósito, porque una corrección
falsa hace más daño que el error que arregla. Solo actúa cuando la palabra:

- tiene cuatro letras o más (las cortas del español son casi todas corrientes);
- no es una palabra corriente protegida (`quien`, `buen`, `ven`, `para`,
  `might`, `that`…), así «a quien quieras» y «buen resultados» quedan intactos;
- no es ya la propia grafía preferida;
- si son varias palabras, la primera suena como el principio del término y el
  resto como el final, con solo espacios en medio: la «y» de «Qwen y GitHub»
  nunca se pierde.

Dejá `~` apagado en términos cuyas variantes sean palabras reales: ahí conviene
listarlas a mano (`commit`, `staging`). Para ver qué cambiaría sin aplicar
nada, el perfil se puede probar desde Python con `context.correct_aliases`.

En el pipeline actual estas correcciones se aplican al texto **después** del
reconocimiento; no son pistas acústicas para Parakeet. Se probó el sesgo
contextual (hotwords) y **no funciona** con este modelo: sherpa-onnx lo
tokeniza con un `bpe.vocab` de sentencepiece que el paquete de Parakeet no
trae. Ver [`docs/precision.md`](../docs/precision.md).

Los perfiles y el vocabulario se guardan localmente en la configuración de
Instant2. En Linux/macOS también se pueden gestionar desde setup:

```bash
instant2 setup --yes --no-probe --context-profile Trabajo \
  --context-term "Instant2=instante|in stand"
```

Con `~` delante de la grafía se activa el sonido para ese término:

```bash
instant2 setup --yes --no-probe --context-profile Trabajo \
  --context-term "~Qwen=cuentres|quemet"
```

Para quitar un término o un perfil en Linux/macOS:

```bash
instant2 setup --yes --no-probe --context-profile Trabajo \
  --context-remove-term Instant2
instant2 setup --yes --no-probe --context-profile Trabajo \
  --context-delete-profile
```

El perfil activo se puede elegir mediante `INSTANT2_CONTEXT`. El vocabulario
explícito funciona sin LLM ni internet. Para pulido adicional, el LLM local
recibe el glosario activo y el glosario técnico general; el audio nunca se
envía a la nube.

Además del perfil personal, Instant2 trae un **diccionario técnico general**
(términos ingleses de desarrollo que Parakeet deforma: commit, build,
deploy, staging, Qwen, GitHub…): solo actúa cuando la confianza del
reconocimiento es baja, y nunca toca texto seguro ni palabras corrientes.

## Revisión LLM en Dictado mejorado

El instalador completo de Windows configura Qwen 4B y llama.cpp localmente. Si
usás el portable, Linux/macOS o querés otro servicio, podés conectar Ollama,
`llama-server` u otro endpoint compatible con Chat Completions de OpenAI desde
Ajustes. Indicá el nombre exacto del modelo que espera el servidor y dejá
activa la casilla «Pedir al LLM que revise cada dictado mejorado». Si el
servicio no está configurado o falla, el dictado conserva el resultado local.

En cada dictado mejorado con revisión activa, el LLM recibe la transcripción
y el vocabulario activo, nunca el audio. Instant2 usa su respuesta para corregir
errores de reconocimiento, incluidas palabras mal transcritas. Si cambia una
palabra o una tilde, una segunda comprobación conservadora tiene que aceptar
explícitamente la propuesta; ante un rechazo, una respuesta ambigua, un timeout
o un fallo del servicio, queda el texto corregido localmente. Esta protección
reduce cambios de sentido, pero no puede garantizar que nunca ocurran. Podés
desactivar la revisión para volver al recorrido sin llamada ni espera del LLM.

La interfaz indica si el endpoint es local o remoto. Un endpoint remoto recibe
el texto y el vocabulario activo, así que usá uno local si querés mantenerlos
en el equipo. Los tokens viajan en el encabezado `Authorization` y nunca se
escriben en los registros. Solo se permite `http://` en loopback
(`localhost`/`127.*`/`::1`); para otro destino usá `https://`.

El signo `¿` de apertura se restaura localmente y sin red siempre que una
oración cierre con `?` y empiece con palabra interrogativa
(qué/cómo/cuándo/dónde/cuál/cuánto/quién/por qué…): es determinista, nunca
reescribe palabras. El `¡` no se toca sin prosodia (falso positivo peor que
ausencia).

## Mantener un LLM local disponible

El instalador completo de Windows incluye y configura llama.cpp con Qwen 4B
Q4_K_M. Instant2 inicia el servidor local cuando corresponde, usa
automáticamente un dispositivo que el runtime detecte y prueba CPU si no hay
aceleración compatible. En **Ajustes → Voz, rendimiento y privacidad avanzada**
podés cambiar el modelo o el runtime. El portable y los paquetes de otros
sistemas requieren configurar los archivos locales o un servicio compatible.

Dejá **Automático** para que Instant2 elija un dispositivo que llama-server confirme disponible y pruebe CPU si falla el arranque acelerado. También podés elegir **Solo CPU**. El servidor administrado escucha en 127.0.0.1. Cerrar el panel no detiene el daemon ni su servidor; al detener el dictado, Instant2 cierra solo el proceso hijo que inició.

En **Prioridad de recursos**, elegí rapidez para mantener los modelos cargados, equilibrado para liberar los modelos de voz y dormir el LLM tras 5 minutos, o menor memoria para liberarlos tras 90 segundos. Una versión compatible de llama-server puede reactivar el modelo al recibir otra solicitud. El primer dictado después de liberar modelos puede tardar más. Los servidores locales ya iniciados y otros endpoints externos se reutilizan sin que Instant2 cambie su ciclo de vida.

Ollama y otros endpoints compatibles siguen disponibles. Configurá su URL y el nombre del modelo; Instant2 los usa sin iniciarlos ni detenerlos. Ajustes indica si el servicio es local o remoto. El audio siempre permanece en el motor local de reconocimiento. Si el LLM falla, Dictado mejorado conserva las correcciones locales.

## Binarios por SO

Las [releases de GitHub](https://github.com/getodevel-source/Instant2/releases/latest)
publican `Instant2-Setup.exe` junto a una o más partes `Instant2-Setup-N.bin` y
sus sidecars `.sha256`. El actualizador descarga y verifica todas las partes
del instalador juntas. También se publican `Instant2.exe` (portable de Windows),
`instant2-linux` y `instant2-macos`; estos paquetes no incluyen los pesos.
Actualmente salen **sin firma de sistema operativo** (ver la nota en
[Instalación](#instalación)); la ruta optativa de firma está en la
[guía de firma](../docs/firma-releases.md).

Verificá un asset a mano antes de instalarlo:

```powershell
Get-FileHash Instant2-Setup.exe -Algorithm SHA256
# compará con el contenido de Instant2-Setup.exe.sha256
```

```bash
sha256sum -c instant2-linux.sha256
```

## Actualizaciones

Al abrir el panel, Instant2 consulta como máximo una vez al día el último release en GitHub. Esa conexión expone tu IP a GitHub como cualquier visita web; no manda telemetría, ni audio, ni tu configuración.

El modo vive en `update_mode` (`notify` por defecto | `auto` | `off`); `INSTANT2_NO_UPDATE=1` equivale a `off`:

- `notify`: chequea y te avisa con la versión y las notas; descarga solo cuando lo pedís.
- `auto`: además la descarga y verifica en segundo plano, con progreso y reintento.
- `off`: ni chequea.

La instalación **siempre** pide tu confirmación («Actualizar y reiniciar»); nunca corta un dictado sin aviso. Toda descarga se verifica por SHA256 contra su sidecar antes de instalarse, y se vuelve a hashear justo antes de aplicar.

## Desinstalación

Desinstalar frena el dictado y borra la app, pero **conserva tu configuración y tus modelos** para no descargarlos de nuevo:

- Windows: el desinstalador frena el daemon antes de borrar; quedan `%APPDATA%\instant2` (config) y `%LOCALAPPDATA%\instant2\models` (modelos).
- Linux/macOS: `scripts/uninstall.sh` frena el daemon y el panel y quita el arranque automático (`instant2.desktop` / `com.instant2.dictado.plist`); con `--purge` borra además la configuración y los modelos descritos más arriba.
