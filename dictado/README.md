# Instant

Hold-to-talk offline en español. Mantené la tecla, hablá 60-120s, soltá y pegá.

Pipeline: mic 16kHz → VAD (frases) → VoxCore int8 offline
(`sherpa-onnx`, CPU) → portapapeles + Ctrl+V. Sin nube, sin GPU.
Idioma: Español (único). Sin selector: el setup muestra
"Español (único)" y guarda `lang: "es"` en la config para futuro;
el engine transcribe igual que siempre.

Errores conocidos del modelo (no son bugs de la app): nombres propios
raros pueden salir deformados (p.ej. "Instant" → "instante"); siglas y
anglicismos se transcriben por fonética. Un perfil de vocabulario permite
corregir variantes reconocidas explícitamente; el pulido LLM por sí solo
ayuda con tildes y puntuación, pero no reemplaza el reconocimiento de audio.

En Windows, la vía recomendada es el instalador de la [última release](https://github.com/getodevel-source/Instant/releases/latest) (`Instant-Setup.exe`): por usuario, sin admin, con desinstalador y actualización in-place. El instalador conserva tu configuración y tus modelos. El `Instant.exe` portable y los binarios de Linux/macOS viven en la misma release.

> Los binarios están **sin firmar** por ahora: Windows SmartScreen muestra «Windows protegió su PC» la primera vez (elegí «Más información» → «Ejecutar de todas formas» solo si el instalador salió del enlace oficial); Gatekeeper en macOS puede pedirte que confirmes la apertura en Ajustes → Privacidad y seguridad. Esto no es un error de Instant.

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
48 kHz), requerida por VAD y VoxCore. En micrófonos estéreo se capturan
hasta dos canales y se usa el de mayor energía.
El selector de configuración consolida alias repetidos entre host APIs y
prioriza WASAPI; si un backend ofrece varias entradas con el mismo nombre,
las conserva y las distingue por índice.
Al iniciar cada dictado se vuelve a resolver el micrófono guardado. Si el
inicio por callback falla, Instant intenta aliases del mismo nombre y deja
constancia del backend usado o del error en el log; el diagnóstico abre también
una captura por callback para comprobar ese mismo modo.

Al cerrar la ventana, la interfaz se cierra para liberar sus recursos; el
dictado sigue activo en la bandeja. Abrir Instant desde el icono enfoca una
ventana existente o abre una nueva, sin duplicar ventanas. Clic derecho ofrece
«Abrir ventana de Instant», «Configurar micrófono y tecla», «Diagnóstico» y
«Salir de Instant» (que detiene el dictado).
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

Instant detecta Wayland automáticamente. El pegado usa `wl-clipboard` y
`wtype`; si el compositor rechaza el teclado virtual, la transcripción queda
en el portapapeles para pegarla manualmente. Las superficies nativas de
Wayland son el objetivo inicial; XWayland puede comportarse distinto.

#### Hyprland y Omarchy

El hotkey usa el portal XDG de atajos globales. Iniciá Instant una vez y
consultá los atajos registrados:

```bash
hyprctl globalshortcuts
```

Buscá la entrada cuya descripción dice `Instant: mantener para dictar` y
copiá su identificador completo (el formato es `aplicación:push-to-talk`).
Asocialo a la tecla configurada en Instant en tu archivo de bindings de
Hyprland. En la configuración Lua de Omarchy, por ejemplo:

```lua
hl.bind("F9", hl.dsp.global("ID-DE-INSTANT:push-to-talk"))
```

Reemplazá `F9` por la tecla configurada y `ID-DE-INSTANT:push-to-talk` por el
identificador completo que devolvió `hyprctl`. Recargá Hyprland y reiniciá
Instant. Mantené la tecla para grabar y
soltala para detener el dictado. El portal necesita el backend
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
instant setup   # panel de configuración (los tres sistemas)
instant run     # daemon: mantené la tecla, soltá para transcribir
instant stop    # frena el daemon (lo usa también el desinstalador)
instant check   # boot rapido: tecla + mic probe + warmup (<5s)
```

`instant setup` abre el centro gráfico PySide6 en los tres sistemas: navegación
lateral con cuatro vistas (Inicio, Micrófono, Ajustes, Vocabulario). Inicio
muestra el estado del dictado, la tecla y los botones de iniciar/detener;
Micrófono trae selector y prueba de nivel (3 s); Ajustes trae la tecla y el
arranque con el sistema; **Vocabulario** trae tabla por perfil con término,
variantes (`a | b | c`), pill de sonido (`≈`) y botón de quitar (`✕`).
«Añadir término» agrega una fila en blanco lista para escribir. El panel indica
los cambios pendientes de guardar y, después de guardar, avisa si hace falta
reiniciar Instant. El diagnóstico muestra el informe en una ventana
independiente. La ventana puede cerrarse sin detener el daemon; en Windows queda
el icono de bandeja, y en Linux/macOS el daemon sigue vivo hasta `scripts/instant-stop.sh`.

Sin ventana (servidores, SSH, scripts): `instant setup --tui` mantiene el
asistente de terminal; cualquier flag de CLI también lo usa.

`instant` sin argumentos abre el panel y se asegura de un solo daemon activo.
Si ya hay una ventana abierta, una segunda apertura le pasa el pedido y sale
(Windows: mutex + `FindWindow`; Linux/macOS: canal `QLocalServer`).

El overlay dibuja en web (QtWebEngine) en los tres sistemas, con dos estilos
(`overlay_style` en la config): `orbital` (orbe con anillos que respiran con la
voz, por defecto) y `classic` (pastilla con barras y tecla visible). Si
QtWebEngine no puede abrir cae a Tk (X11 sin GL, instalación vieja sin
webengine): el daemon nunca se queda sin overlay. El renderer vive en
[`overlay_web.py`](src/instant_app/overlay_web.py) y la pagina en
[`web/overlay.html`](src/instant_app/web/overlay.html).

Para retocar la interfaz, los colores están en
[`branding.py`](src/instant_app/branding.py) (`PALETTE`, única fuente de
verdad); las páginas [`web/overlay.html`](src/instant_app/web/overlay.html)
y [`web/panel.html`](src/instant_app/web/panel.html) repiten la paleta a
mano porque se sirven tal cual, sin motor de plantillas;
`tests/test_overlay_palette.py` y `tests/test_panel_page.py` fallan si se
despegan. La ventana del overlay se centra con
`WINDOW_SIZES`/`_OVERLAY_BOTTOM_GAP` en `overlay_web.py`; el panel vive en
`gui.py` (`PanelLogic` + host `qml/panel_web_host.qml`).

El asistente de terminal (`instant setup --tui`, o con cualquier flag de CLI)
hace, en orden:

1. **Modelos**: descarga VoxCore (~670 MB) y VAD (~1 MB) si faltan.
2. **Micrófono**: muestra nombre, backend y canales; permite medir nivel. Guarda nombre e índice como fallback si cambia el orden de dispositivos.
3. **Idioma**: español fijo.
4. **Tecla**: captura una tecla individual (Enter conserva la actual).
5. **Arranque con el sistema**: muestra el estado real y permite activarlo o desactivarlo.
6. **Prueba final**: abre el micrófono y calienta los modelos.

Modo no interactivo (conserva el micrófono y la tecla actuales):

```bash
instant setup --yes --key f9 --threads 4 --no-sound
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

`instant setup` chequea las dependencias y te muestra `[OK]`/`[FALTA]`
en criollo. Con `--yes` solo avisa (nunca frena la config).
Con `--check-deps` muestra la tabla igual; con `--fix-deps` instala
lo que el SO deja y vuelve a chequear.

```bash
instant setup --check-deps
instant setup --fix-deps
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

En Wayland, el hotkey depende del portal global del compositor; ver [Hyprland y Omarchy](#hyprland-y-omarchy). Sin red no se instalan paquetes ni modelos; el setup informa qué falta. Para evitar preguntas en instalaciones automatizadas, definí `INSTANT_UNATTENDED=1` antes de correr el instalador del repositorio.

## De dónde saca los modelos (DICTADO_DATA)

Precedencia efectiva:

1. `DICTADO_DATA` gana siempre (aunque no exista, se respeta tal cual).
2. `./models` con `parakeet-v3-int8/encoder.int8.onnx` gana al dir de usuario.
3. Con el ejecutable congelado, `../models` respecto de su carpeta se usa si
   contiene el marcador; así el panel y el daemon comparten los modelos del
   checkout.
4. `./models` sin ese marcador se ignora.
5. Si no hay nada, va al dir de usuario (`%LOCALAPPDATA%/instant/models`
   en win, `~/.local/share/instant/models` en linux,
   `~/Library/Application Support/instant/models` en mac).

Sin modelos o sin mic, `setup` avisa y sigue (no crashea); `run` sin
modelos pide correr `setup` con red primero.

Teclas: Windows `f9 f10 f20 scroll pause`, Linux/macOS `f9 f10 f11 f12`.
Config en `%APPDATA%/instant` (win), `~/.config/instant` (linux),
`~/Library/Application Support/instant` (mac). Env `DICTADO_*` pisa config
(`DICTADO_MIC`, `DICTADO_KEY`, `DICTADO_THREADS`, `DICTADO_SOUND`,
`DICTADO_MAX_SEG`, `DICTADO_VAD=silero|ten`, `DICTADO_BLANK=0..1`,
`DICTADO_LLM_URL`, `DICTADO_LLM_TOKEN`, `DICTADO_UPDATE=notify|auto|off`
(o `INSTANT_NO_UPDATE=1` para apagar el chequeo),
`DICTADO_AUTOSTART=1/0`).

PID file: `run` escribe `instant.pid` junto a la config al arrancar y lo
borra al salir limpio; los lanzadores `.bat` lo usan para stop/status
sin tocar procesos ajenos.

## Arranque con el sistema

En Windows, la casilla «Iniciar con Windows» está en el panel gráfico.
En Linux/macOS, el asistente terminal pregunta s/n después de la tecla,
muestra el estado real y qué va a crear; el default conserva el estado actual.
Si lo prendés, el daemon arranca solo y oculto en el próximo login; si lo apagás, no arranca más.

```bash
instant setup                       # interactivo: pregunta s/n
instant setup --yes --no-probe      # no toca nada, conserva lo que hay
instant setup --autostart           # lo prende sin preguntar
instant setup --no-autostart        # lo apaga sin preguntar
```

Qué crea en cada sistema (solo eso, nada más):

- Windows: acceso `Instant Dictado.lnk` en
  `%APPDATA%/Microsoft/Windows/Start Menu/Programs/Startup`, que apunta
  a `scripts/instant-run.bat` del repo si lo encuentra (modo dev), y si no a
  `instant.exe run` por PATH o `pythonw -m instant_app run`. Se crea con
  el powershell que ya trae Windows, sin instalar nada nuevo.
- Linux: archivo `~/.config/autostart/instant.desktop` (estándar
  freedesktop) que lanza `instant run` oculto con `nohup`.
- macOS: `~/Library/LaunchAgents/com.instant.dictado.plist` que lanza
  `instant run` al iniciar sesión.

Para sacarlo alcanza con correr el setup y decir que no, o con
`instant setup --no-autostart`. Si lo borrás a mano de esas rutas vale
igual: el setup lo detecta apagado. Lo que vale de verdad es lo que hay
en el sistema, no la casilla guardada (`autostart` en la config es solo
un recordatorio; si no coinciden, el setup te avisa y deja lo del sistema).

## Contexto y vocabulario

En Windows, Preferencias permite crear perfiles y definir variantes exactas
que el reconocedor suele transcribir con otra grafía. Cada línea tiene la
grafía final, un tabulador y una o más variantes separadas por `|`:

```text
Instant	instante | in stand
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
reconocimiento; no son pistas acústicas para VoxCore. Se probó el sesgo
contextual (hotwords) y **no funciona** con este modelo: sherpa-onnx lo
tokeniza con un `bpe.vocab` de sentencepiece que el paquete de VoxCore no
trae. Ver [`docs/precision.md`](../docs/precision.md).

Los perfiles y el vocabulario se guardan localmente en la configuración de
Instant. En Linux/macOS también se pueden gestionar desde setup:

```bash
instant setup --yes --no-probe --context-profile Trabajo \
  --context-term "Instant=instante|in stand"
```

Con `~` delante de la grafía se activa el sonido para ese término:

```bash
instant setup --yes --no-probe --context-profile Trabajo \
  --context-term "~Qwen=cuentres|quemet"
```

Para quitar un término o un perfil en Linux/macOS:

```bash
instant setup --yes --no-probe --context-profile Trabajo \
  --context-remove-term Instant
instant setup --yes --no-probe --context-profile Trabajo \
  --context-delete-profile
```

El perfil activo se puede elegir mediante `DICTADO_CONTEXT`. El vocabulario
explícito funciona sin LLM ni internet. Para pulido adicional, el LLM local
recibe el glosario activo y el glosario técnico general; el audio nunca se
envía a la nube.

Además del perfil personal, Instant trae un **diccionario técnico general**
(términos ingleses de desarrollo que VoxCore deforma: commit, build,
deploy, staging, Qwen, GitHub…): solo actúa cuando la confianza del
reconocimiento es baja, y nunca toca texto seguro ni palabras corrientes.

## Pulido LLM (opcional, off por defecto)

Si tenés `llama-server` local corriendo, en Windows configurá su URL en
Preferencias; en Linux/macOS usá `--llm-url` al setup (o
`DICTADO_LLM_URL=http://127.0.0.1:8080`). Si el servidor pide
autenticación, `--llm-token` (o `DICTADO_LLM_TOKEN`): viaja en el encabezado
`Authorization` y nunca queda en los registros. Solo se permite `http://`
en claro contra loopback (`localhost`/`127.*`/`::1`); cualquier otro
`http://` se rechaza (usá `https://`). El LLM recibe la transcripción y el
glosario activo —no el audio— y solo se lo consulta cuando la confianza del
decode es baja (media de log-probs por token < 0.85): el texto seguro se pega
directo, sin red ni espera. Corrige ortografía, tildes y puntuación. No puede
aprovechar la entonación del audio ni sustituir palabras: se rechaza toda
salida que cambie la secuencia de palabras (comparando el texto normalizado,
sin tildes ni mayúsculas). Si el servidor falla, se conserva la transcripción
con las sustituciones exactas del glosario; Instant nunca manda audio a la nube.

El signo `¿` de apertura se restaura localmente y sin red siempre que una
oración cierre con `?` y empiece con palabra interrogativa
(qué/cómo/cuándo/dónde/cuál/cuánto/quién/por qué…): es determinista, nunca
reescribe palabras. El `¡` no se toca sin prosodia (falso positivo peor que
ausencia).

## Binarios por SO

Los binarios salen de las [releases de GitHub](https://github.com/getodevel-source/Instant/releases/latest): `Instant-Setup.exe` (instalador de Windows), `Instant.exe` (portable de Windows), `instant-linux` y `instant-macos`. Cada asset trae un sidecar `.sha256` y el actualizador rechaza cualquier descarga sin sidecar válido o con hash distinto. Actualmente salen **sin firma de sistema operativo** (ver la nota en [Instalación](#instalación)); el workflow tiene una ruta optativa de firma y notarización que se activa solo cuando el repositorio configura certificados. Si se activa, también publica `Instant-macos.dmg`. El proceso está en la [guía de firma](../docs/firma-releases.md).

Verificá un asset a mano antes de instalarlo:

```powershell
Get-FileHash Instant-Setup.exe -Algorithm SHA256
# compará con el contenido de Instant-Setup.exe.sha256
```

```bash
sha256sum -c instant-linux.sha256
```

## Actualizaciones

Al abrir el panel, Instant consulta como máximo una vez al día el último release en GitHub. Esa conexión expone tu IP a GitHub como cualquier visita web; no manda telemetría, ni audio, ni tu configuración.

El modo vive en `update_mode` (`notify` por defecto | `auto` | `off`); `INSTANT_NO_UPDATE=1` equivale a `off`:

- `notify`: chequea y te avisa con la versión y las notas; descarga solo cuando lo pedís.
- `auto`: además la descarga y verifica en segundo plano, con progreso y reintento.
- `off`: ni chequea.

La instalación **siempre** pide tu confirmación («Actualizar y reiniciar»); nunca corta un dictado sin aviso. Toda descarga se verifica por SHA256 contra su sidecar antes de instalarse, y se vuelve a hashear justo antes de aplicar.

## Desinstalación

Desinstalar frena el dictado y borra la app, pero **conserva tu configuración y tus modelos** para no descargarlos de nuevo:

- Windows: el desinstalador frena el daemon antes de borrar; quedan `%APPDATA%\instant` (config) y `%LOCALAPPDATA%\instant\models` (modelos).
- Linux/macOS: `scripts/uninstall.sh` frena el daemon y el panel y quita el arranque automático (`instant.desktop` / `com.instant.dictado.plist`); con `--purge` borra además la configuración y los modelos descritos más arriba.
