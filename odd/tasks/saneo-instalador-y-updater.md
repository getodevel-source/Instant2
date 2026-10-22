# Saneo del instalador y del actualizador

## Objetivo

Dejar la version sana en dos frentes: que **actualizar Instant2 no produzca errores ni ventanas sorpresa**, y que el arbol de trabajo quede limpio, con cobertura y changelog al dia.

## Problema

El usuario reporto: al instalar una actualizacion aparecia una **terminal con pasos de progreso del daemon**, en medio de eso un **toast de error** que podia aceptar, y la aplicacion se volvia a abrir actualizada "como que no habia problema, pero si estaba el error".

Evidencia recolectada (maquina del maintainer, 2026-10-20):

- El toast **no deja rastro en el log**: `_toast_error` loguea con `log.warning("%s: %s", title, detail, exc_info=True)` (`dictado/src/instant2_app/gui.py:118`) y `instant2.log` no contiene ninguna linea con "Actualizacion". Es decir: el error que ve el usuario **no venia del flujo de actualizacion del codigo que estaba corriendo**, o venia de un toast que no pasa por `_toast_error`. Hoy no hay forma de saber cual: **falta instrumentacion**.
- La terminal es el helper portable `scripts/instant2-update.bat`, lanzado sin `CREATE_NO_WINDOW` (`gui.py:2272-2273`). Sus pasos son `[2/6] Frenando daemon...` y `[5/6] Arrancando daemon nuevo...`: exactamente lo que el usuario describe como "daemon 1/3".
- El log confirma un update real por el path portable el 2026-10-15 10:23:36 (`panel abierto (setup)`), sin errores registrados.
- El instalador Inno (path `installed`) se lanza con `Popen` **sin leer el returncode, sin `/LOG` y sin verificar la version instalada** (`gui.py:2308-2310`): un aborto de Inno es **invisible**.
- `instant2-update.bat:72-78` hace rollback y despues imprime `Listo: Instant2 actualizado.` con `exit /b 0` (linea 87-89): **falso exito**.
- `gui.py:2297-2301` da 8 s para frenar el daemon, pero una sola llamada a `daemon_is_running()` puede tardar 15 s (`tasklist` 5 s en `gui.py:202` + PowerShell 10 s en `gui.py:248`): el deadline es inalcanzable.
- `gui.py:249-250` devuelve `True` (es Instant2) cuando el probe de PowerShell falla, y `gui.py:202`/`gui.py:248` decodifican con `text=True` sin `encoding=`: un `UnicodeDecodeError` (un `ValueError`, no capturado) aborta la actualizacion.
- `launch.py:29-41` fija `QTWEBENGINEPROCESS_PATH` al `_MEIPASS` del padre y `app_environment()` (`launch.py:12-17`) no lo reapunta: el hijo hereda un path que desaparece al salir el padre.
- Instalado en la maquina: **0.1.0** (2026-10-14). GitHub: **0.1.5** (2026-10-15). Las cinco releases nunca se aplicaron.
- Los modelos del usuario estan en `%LOCALAPPDATA%\instant\models` (slug viejo) mientras el codigo actual usa `%LOCALAPPDATA%\instant2\models` (`identity.py:4`, `paths.py:49-52`), y `installer/instant2.iss:87` copia 4.010.135.264 bytes con `ignoreversion`, **sin migrar lo que ya hay**.

## Por que

Un actualizador que no verifica nada no puede fallar de forma honesta: el usuario ve un error y no sabe si la version se aplico. Eso es peor que un fallo visible. Ademas el instalador descarga 3,78 GB para instalar una app de 207 MB, y la GUI no arranca con Python 3.14 (`ctypes.wintypes.WINFUNCTYPE` no existe en 3.14; `gui_lifecycle.py:22`), mientras `pyproject.toml` declara `requires-python = ">=3.12"` sin techo.

## Alcance

**Autorizado (este documento):**

- T1 Silenciar la ventana del helper portable y volcar su salida a un log.
- T2 Que el helper no mienta en el rollback.
- T3 Que el actualizador verifique el resultado: exit code, log de Inno y version instalada.
- T4 Probes del daemon robustos: encoding explicito, probe fallido no significa "es Instant2", deadline alcanzable.
- T5 Loguear todo toast de error para que el proximo bug sea diagnosticable.
- T6 Que `QTWEBENGINEPROCESS_PATH` no filtre el `_MEIPASS` del padre.
- T7 Instalar sin re-copiar 4 GB: migrar los modelos del slug legacy al nuevo.
- T8 Python 3.14 no debe romper la GUI (tope en `requires-python`) y venv de desarrollo en 3.12.
- T9 `.gitignore`: artefactos generados (`.gguf` de 1,2 GB en la raiz, `dictado/bench/results/` con las claves del paquete ciego).
- T10 Recuperar la cobertura perdida: `test_bench_llm_runtime.py` y el passthrough de `llm.py`.
- T11 CHANGELOG de `[Sin publicar]` completo y los tres `.md` nuevos en `docs/`.
- T12 Commitear el trabajo de benchmark pendiente por unidades de trabajo revisables.

**Fuera de alcance (requiere propuesta aparte):**

- Sacar el modelo LLM de 3 GB del bundle offline: cambia el producto (el instalador deja de ser offline para el LLM) y por lo tanto el release.
- Taggear `instant2-v0.1.0` retroactivo.
- Cambiar el perfil de sampling del benchmark para que replique el body de produccion.

## Restricciones

- No se toca el comportamiento del nucleo de dictado, TTS o LLM.
- Todo cambio de codigo va acompanado de su test o, cuando el comportamiento no es testeable en Windows, de una razon explicita.
- La suite debe quedar verde en Python 3.12 (interpretador de CI) y en el 3.14 del sistema.
- Nada de artefactos generados en el repo: los `.gguf` y `dictado/bench/results/` se ignoran, no se commitean.
- `dictado/bench/results/` contiene las claves del paquete ciego (`blind-package-final/blinded_key_v2p.json`, `blinded_key.ALL.json`, `blinded_review.ALL.jsonl`): **nunca** se versionan.

## Checklist

### Bloque 1 - Actualizador observable y veraz (el bug reportado)

- [x] **T1** `gui.py:2271-2279`: `Popen(["cmd", "/c", ...])` con `CREATE_NO_WINDOW` para que no aparezca la terminal; el helper deja de ser una ventana visible.
- [x] **T2** `scripts/instant2-update.bat`: toda la salida a `%APPDATA%\instant2\update.log`; la rama `:rollback` imprime el fallo real y sale con `exit /b 1` (nada de "Listo: Instant2 actualizado.").
- [x] **T3** `gui.py:2303-2315`: pasar `/LOG=` a Inno, esperar el proceso, leer el returncode y verificar la version instalada antes de afirmar exito; si falla, toast con el log.
- [x] **T4** `gui.py:188-305`: `encoding="utf-8", errors="replace"` en `tasklist` y PowerShell; un probe fallido no se interpreta como "es Instant2"; el deadline del daemon es alcanzable y el fallo distingue "el daemon no se frena" de "el probe no pudo responder".
- [x] **T5** `gui.py:834` (`toast`): todo toast de nivel `error` queda registrado con titulo y detalle, aunque no pase por `_toast_error`.
- [x] **T6** `launch.py:12-41`: `app_environment()` reapunta o limpia `QTWEBENGINEPROCESS_PATH` para que el hijo no herede un `_MEIPASS` que va a desaparecer.

### Bloque 2 - Instalador sin recargar 4 GB

- [x] **T7** `installer/instant2.iss`: si existen modelos en el directorio legacy (`%LOCALAPPDATA%\instant\models`), se reutilizan en vez de volver a copiar los 4 GB; el bundle queda en `ignoreversion` solo para lo que no existe.

### Bloque 3 - Python

- [x] **T8** `dictado/pyproject.toml`: la GUI tiene que andar en 3.14 (no se topa `requires-python`: el tipo de callback de Windows se resuelve desde `ctypes`, que es donde vive); documentar el venv de desarrollo en 3.12.

### Bloque 4 - Higiene del repo

- [x] **T9** `.gitignore`: reglas para `*.gguf` y `dictado/bench/results/` (con las claves ciegas).
- [x] **T10** Tests: reescribir `dictado/tests/test_bench_llm_runtime.py` contra los 2 candidatos vigentes y recuperar el test del passthrough de `sampling_params` en `llm.py` (`min_p`, `repetition_penalty`).
- [x] **T11** `CHANGELOG.md`: `[Sin publicar]` describe los ~13 cambios pendientes; `AUDITORIA-LLM.md`, `BENCHMARK-MODELOS.md` y `VOICE-TO-PROMPT.md` a `docs/`.
- [x] **T12** Commits por unidad de trabajo para todo el trabajo de benchmark pendiente.

### Bloque 5 - El CI verde en las tres plataformas

- [x] **T13** `.gitattributes`: los finales de linea del arbol de trabajo quedan
  fijados (LF para el texto, CRLF para `.bat`/`.cmd`/`.ps1`/`.iss`) y los
  manifiestos congelados se recalculan contra los bytes canonicos.
- [x] **T14** `_portable_path`: la ruta del repo gana al perfil del usuario y
  el enmascarado se conserva para los archivos externos.
- [x] **T15** `test_windows_callbacks`: lo que es de Windows se salta en
  POSIX; la guarda que no se use `ctypes.wintypes` corre en los tres sistemas.

## Criterios de aceptacion

1. La suite completa (`cd dictado && python -m unittest discover -s tests`) pasa en 3.12 y en 3.14, sin tests nuevos saltados.
2. Ninguna actualizacion abre una ventana de consola ni una terminal visible.
3. Un fallo de instalacion produce un toast **con el log adjunto** y un exit code distinto de 0, nunca un "Listo" mentiroso.
4. Despues de una actualizacion, la app verifica que la version instalada es la nueva y lo dice.
5. Instalar sobre una maquina que ya tiene los modelos no vuelve a copiar 4 GB.
6. `git status` queda limpio salvo los artefactos deliberadamente ignorados.
7. Todo toast de error queda en `instant2.log`.

## Checks aplicables

- `cd dictado && python -m unittest discover -s tests` (3.12 del venv de CI y 3.14 del sistema).
- `python -m pyflakes dictado/src/instant2_app/*.py` (mismo criterio que el gate de CI: 38 archivos, 0 hallazgos).
- `python -m instant2_app --help` con `PYTHONPATH=dictado/src`.
- Revision manual del diff de `installer/instant2.iss` y de `scripts/instant2-update.bat` (no hay cobertura automatica de Inno Setup).

## Progreso

_(se actualiza al cerrar cada tarea, con la evidencia del check y el hash del commit)_

- **T1** `2640fb8` `fix(update): hide the helper console window`: el helper portable se lanza con `CREATE_NO_WINDOW` y su stdout/stderr van a `%APPDATA%\instant2\update.log`; evidencia: `python -m unittest tests.test_update_safety.T1HelperConsoleTests` -> Ran 2 tests, OK.
- **T2** `07f4e73` `fix(update): report a failed rollback instead of a false success`: el `.bat` escribe todos sus mensajes en `update.log`, la rama `:rollback` reporta el fallo real y sale con codigo 1 (nada de "Listo: Instant2 actualizado."); evidencia: `python -m unittest tests.test_update_safety.T2HelperHonestyTests` -> Ran 5 tests, OK (dos de ellos ejecutan el `.bat` de verdad).
- **T3** `d08fdf4` `fix(update): verify the installer result and installed version`: Inno recibe `/LOG=`, se espera el proceso y se lee su returncode; el fallo sale en un toast con la cola del log de Inno; evidencia: `python -m unittest tests.test_update_safety.T3InstallerVerificationTests` -> Ran 5 tests, OK.
- **T4** `a1552c7` `fix(update): make the daemon probe robust`: los probes decodifican con `encoding="utf-8", errors="replace"`, un probe caido da estado `unknown` (nunca "es Instant2"), el deadline del daemon (20 s) supera el peor caso de un probe (15 s) y el fallo distingue "sigue activo" de "no se pudo verificar"; evidencia: `python -m unittest tests.test_update_safety.T4DaemonProbeTests` -> Ran 11 tests, OK.
- **T5** `f12fe58` `fix(log): record every error toast`: `toast()` registra con titulo y detalle todo toast de nivel `error`, aunque no pase por `_toast_error`; evidencia: `python -m unittest tests.test_update_safety.T5ErrorToastLoggingTests` -> Ran 3 tests, OK.
- **T6** `7b73e11` `fix(launch): do not leak the parent _MEIPASS to children`: `app_environment()` saca del entorno del hijo el `QTWEBENGINEPROCESS_PATH` que apunta al `_MEIPASS` del padre; evidencia: `python -m unittest tests.test_update_safety.T6WebEngineEnvTests` -> Ran 3 tests, OK.

Checks del bloque: `cd dictado && python -m unittest discover -s tests` -> Ran 534 tests, OK (skipped=6) en 3.12.12 (venv de CI) y en 3.14.2 (sistema); `python -m pyflakes dictado/src/instant2_app/*.py` -> 38 archivos, 0 hallazgos; `python -m instant2_app --help` -> exit 0.

### Correccion del bloque 1 (revision adversarial)

- **La espera del instalador no puede caer en el hilo de UI** `b7f11cd` `fix(update): wait for the installer off the UI thread`: verificar el instalador, frenar el daemon y **esperar al instalador** pasan al worker; al hilo de UI solo llega el resultado (toast o cierre). El hilo de UI tiene que seguir respondiendo porque Inno manda `WM_CLOSE` al panel y, congelado, no lo procesa (Inno escala a matar el proceso). El fake de tests ahora corre el trabajo en un hilo real (antes corria `fn` y `on_result` en el hilo que llamaba, y por eso el defecto era invisible); evidencia: `python -m unittest tests.test_update_safety.T3InstallerVerificationTests` -> Ran 10 tests, OK.
- **El presupuesto del probe tiene piso y el deadline manda** `5841878` `fix(update): keep the daemon probe budget above its floor`: la espera del instalador pasaba el tiempo restante como presupuesto, el probe se quedaba sin respuesta y un daemon que no se frena se reportaba como "no se pudo verificar" (la rama de "sigue activo" era codigo muerto). Ahora el probe conserva su presupuesto completo (piso de 15 s) y el deadline tiene prioridad sobre el probe caido; entre probes se esperan 0.3 s (cada `tasklist.exe` cuesta ~0.25 s); evidencia: `python -m unittest tests.test_update_safety.T4DaemonProbeTests` -> Ran 19 tests, OK.
- **Un solo reparto del presupuesto** `c35118c` `fix(update): do not split the probe budget twice`: el timeout se repartia entre tasklist y PowerShell y despues se repartia otra vez: PowerShell recibia 6 s en vez de 10 y `Get-CimInstance Win32_Process` con el WMI frio se pasa de 6 s con facilidad. Ahora el timeout se pasa tal cual al probe de PowerShell; evidencia: `python -m unittest tests.test_update_safety.T4DaemonProbeTests` -> Ran 19 tests, OK.
- **El probe desconocido se trata como desconocido** `768dd09` `fix(update): treat an unknown daemon probe as unknown`: `_pid_is_instant` devuelve tres estados y dos rutas los trataban como booleano (`None` es falsy): el daemon no se frenaba, el log culpaba a un PID reciclado y el panel decia "El daemon ya no estaba" mientras el daemon seguia vivo (la actualizacion en caliente dejaba el daemon viejo con el binario nuevo encima y el helper portable terminaba en `:copyfail`). Ahora el probe distingue `PROBE_NO_IMAGE` (el SO no respondio: no se mata nada) de `PROBE_NO_CMDLINE` (la imagen es la nuestra pero no se pudo leer la linea de comandos: se frena igual, como antes de T4, y queda dicho en el log). `daemon_is_running()` conserva el ultimo estado conocido cuando el probe no responde (un fallo transitorio ya no hace creer que el daemon esta apagado ni que arranque un segundo). `Instant2.exe stop` tambien dice la verdad cuando no pudo verificar; evidencia: `python -m unittest tests.test_update_safety.T4DaemonProbeTests` -> Ran 19 tests, OK.
- **El resultado de la actualizacion se reporta honestamente** `f612b3d` `fix(update): report the update result honestly`: el codigo de salida del instalador no se podia leer en el caso instalado (Inno cierra el panel y el proceso muere antes de contarlo), asi que la rama que prometia un toast por codigo distinto de 0 era inalcanzable. Ahora el worker anota el codigo en `%APPDATA%\instant2\installer-result.json` y el arranque siguiente lo lee y reporta el fallo con la cola del log de Inno; una version esperada vacia ya no se reporta como "Actualizacion aplicada" sino como "sin verificar"; el fallo al arrancar el helper portable pasa por `_update_install_failed` (limpia la nota: no hay segundo toast rojo al arrancar de nuevo) y el `/LOG` de Inno va con comillas (Inno parsea la linea de comandos cruda); evidencia: `python -m unittest tests.test_update_safety.T3InstallerVerificationTests` -> Ran 10 tests, OK.
- **El actualizador tambien es observable cuando sale bien** `4e19719` `fix(log): record the update outcome and dedupe the toast log`: el reporte de la actualizacion (tambien el de exito) queda en el log como info, y las dos lineas del toast de error (`_toast_error` y `toast()`) salen con el mismo prefijo `toast de error: <titulo>` para que un solo grep las correlacione; evidencia: `python -m unittest tests.test_update_safety.T5ErrorToastLoggingTests` -> Ran 5 tests, OK.
- **El helper no deja ruido en el log** `3a86afc` `fix(update): keep the helper noise out of the update log`: los `start` del helper descartan el stderr (el aviso de Windows "not compatible with the version of Windows" caia en `update.log` y parecia un fallo de la actualizacion) y los tests del helper pasan a comprobar comportamiento (el mensaje va al log, la consola queda muda cuando la app redirige, el rollback cuenta el fallo real y sale con codigo 1) en vez de mirar el texto del `.bat`; evidencia: `python -m unittest tests.test_update_safety.T2HelperHonestyTests` -> Ran 5 tests, OK.

Checks de la correccion: `cd dictado && python -m unittest discover -s tests` -> Ran 549 tests, OK (skipped=6) en 3.14.2 (sistema) y en 3.12.12 (venv de CI); `python -m pyflakes dictado/src/instant2_app/*.py` -> 38 archivos, 0 hallazgos (exit 0 en los dos interpretes); `python -m instant2_app --help` -> exit 0.

Desviaciones y limites de esta tanda (no tocan T7-T12):

- La version instalada se verifica en el **arranque siguiente**, no en el proceso que instala: el instalador de Inno y el helper portable tienen que cerrar la app para poder reemplazar el binario (`installer/instant2.iss` corre `Instant2.exe stop` en `PrepareToInstall`), asi que el proceso que verifica ya no existe. Para eso la app anota la version pedida en `%APPDATA%\instant2\pending-update.json` y, al arrancar de nuevo, la compara con la version que esta corriendo (que ES la instalada): si no coinciden, sale un toast de error con el log adjunto.
- El helper se lanza sin terminal (`CREATE_NO_WINDOW`), asi que `timeout /t` (que necesita consola) se cambio por `ping -n` en `scripts/instant2-update.bat`.
- La ruta `installed` de Windows sigue sin reabrir la app al terminar: el `.iss` tiene `skipifsilent` en `[Run]` y ese archivo es T7, no de esta tanda.

### Hallazgo de la correccion (fuera del alcance de esta tanda)

- **El helper y el log se pisan**: la app abre `%APPDATA%\instant2\update.log` y se lo pasa al helper como stdout, asi que mientras el helper corre el archivo esta tomado y sus `>>"%LOG%"` fallan con "The process cannot access the file because it is being used by another process". En el launch real de la app los mensajes del helper **no** llegan al log (solo el ruido de cmd); el `.bat` lanzado standalone si los escribe (los tests de T2 no toman el archivo). Reproduccion: el log de `test_the_child_noise_stays_out_of_the_update_log` queda con once errores de bloqueo. Fix candidato: que la app no tome el log (el helper ya lo escribe solo con `>>`) o que lo abra con un modo de comparticion que acepte la escritura del helper. Queda para la proxima tanda: cambia el contrato de T1 y no estaba entre los hallazgos de la revision. **Resuelto en `cacc534`** (ver "Segunda correccion"): el log del usuario es del helper y el padre redirige la salida del hijo a otro archivo.

### Segunda correccion (el log del helper y el reinicio que no ocurrio)

- **El log del usuario es del helper** `cacc534` `fix(update): let the helper own the update log`: la app tomaba `%APPDATA%\instant2\update.log` y se lo pasaba al helper como stdout mientras el helper escribia sus mensajes en el mismo archivo con `>>`; en Windows los dos se pelean por el archivo (el hijo hereda el handle) y en el log del usuario quedaba **solo** el error de comparticion del sistema: ni el paso a paso ni el error real. Ahora el log del usuario lo escribe solo el helper y el padre redirige la salida del hijo a `%TEMP%\instant2_update_<pid>\helper-stdout.log` (sin ventana: `CREATE_NO_WINDOW` se mantiene). Ademas el `.bat` anota su codigo de salida en `%APPDATA%\instant2\helper-result.json` (el mismo mecanismo que `installer-result.json` de Inno) y el arranque siguiente lo cuenta y reporta el fallo con el log al lado: el portable falla igual de visible que el modo instalado. El test nuevo lanza el `.bat` real como lo lanza la app y comprueba que el log del usuario tiene el paso a paso y el error real; contra el codigo anterior ese log tenia solo trece errores de comparticion; evidencia: `python -m unittest tests.test_update_safety.T1HelperLogOwnershipTests` -> Ran 3 tests, OK; `python -m unittest tests.test_update_safety.T1HelperConsoleTests` -> Ran 2 tests, OK.
- **No prometer un reinicio que no ocurrio** `322cb27` `fix(update): stop claiming a restart that did not happen`: el toast de la actualizacion en caliente decia siempre "Se reinicia el dictado con ella", pero con el PID reciclado (el SO se lo dio a otro proceso) no se frena nada y no hay nada que reiniciar. Ahora `stop_daemon_flow` avisa el resultado del reinicio (True / False / "no se sabe") y el toast cuenta lo que paso: con el PID reciclado dice que el dictado no se reinicio y que hay que abrirlo de nuevo; con el probe caido (que frena igual) sigue diciendo que se reinicia; evidencia: `python -m unittest tests.test_update_safety.T4DaemonProbeTests` -> Ran 21 tests, OK.
- **Los tests no leen el %APPDATA% real** `e706f15` `test(update): isolate the helper result file in the update tests`: con el archivo de resultado del helper nuevo, los tests que leen el reporte de la actualizacion podian encontrar un `helper-result.json` rancio en el %APPDATA% real de quien corre la suite (si habia corrido una actualizacion de verdad) y reportar un fallo que no era de ese test. Ahora los dos archivos de resultado (el de Inno y el del helper) se cortan juntos en un temporal; evidencia: `python -m unittest tests.test_update_safety` -> Ran 49 tests, OK (con un `helper-result.json` rancio simulado en el %APPDATA% real, `tests.test_update_safety.T3InstallerVerificationTests` -> Ran 10 tests, OK).

Checks de la segunda correccion: `cd dictado && python -m unittest discover -s tests` -> Ran 554 tests, OK (skipped=6) en 3.14.2 (sistema) y en 3.12.12 (venv de CI); `python -m pyflakes dictado/src/instant2_app/*.py` -> 38 archivos, 0 hallazgos (exit 0 en los dos interpretes); `python -m instant2_app --help` -> exit 0.

Desviaciones y limites de esta tanda:

- El helper portable sigue **sin reabrir la app** en los caminos que fallan antes de reemplazar el binario (`:hashfail`, `:locked`, `:copyfail`): la app se cerraba igual (la cierra el padre al lanzar el helper) y el usuario tiene que abrirla a mano para ver el reporte. Es el mismo comportamiento que el modo instalado (Inno tampoco reabre). El camino de rollback si reabre la ventana.
- La salida cruda del hijo (el ruido de `cmd`) queda en `%TEMP%\instant2_update_<pid>\helper-stdout.log`; la nota `pending-update.json` guarda su ruta y el reporte de fallo la incluye, para que un fallo que no llego al log del usuario tambien sea explicable.

## Ruta y disparadores de delegacion

- T1-T6, T7, T8, T10 tocan 2 o mas archivos no triviales -> **writer delegado** (un writer por bloque).
- T9, T11 (edicion de `.gitignore` y CHANGELOG) son mecanicas -> **inline**.
- La verificacion de los writers corre en la suite completa mas el pyflakes, con lectura de vuelta del diff en el padre.

### Tercera tanda (los cinco pendientes que quedaban)

Cinco defectos verificados por el orquestador, en un writer delegado. Todos
tienen commit propio y evidencia.

- **El resultado de la actualizacion se reportaba para siempre** `987839a` `fix(update): expire a stale update result`: las notas (`pending-update.json`, `installer-result.json`, `helper-result.json`) se leian en cada arranque mientras existieran; si nunca se consumian, el toast rojo "Actualizacion sin aplicar" se repetia dias despues de una actualizacion que el usuario nunca pidio. Ahora cada nota lleva su fecha (campo `ts`, que el `.bat` tambien escribe) y **vence a las 24 horas**: una nota vieja se descarta en silencio y se borra; el caso legitimo (reabrir la app del dia siguiente) se sigue reportando con el log al lado. Una nota sin fecha usa la mtime del archivo. Evidencia: `python -m unittest tests.test_update_safety` -> Ran 60 tests, OK.
- **El helper no reabria el panel en sus caminos de fallo** `3214646` `fix(update): reopen the panel when the helper fails`: `:hashfail`, `:locked` y `:copyfail` salian con `exit /b 1` y la app se quedaba cerrada (la cierro el padre a proposito: hay que reemplazar el binario). Ahora **todos** los caminos de salida reabren el panel y siguen saliendo con codigo 1 (el de exito, con 0 y con el "Listo" solo en ese caso). De paso, `start` con un ejecutable que no existe se queda esperando en esta maquina: ahora solo se reabre si el archivo esta, y si no queda dicho en el log. Evidencia: `python -m unittest tests.test_update_safety` -> Ran 60 tests, OK (los tests lanzan el `.bat` de verdad y comprueban que el panel falso se abrio).
- **El instalador volvia a copiar los 4 GB del bundle** `74c5811` `fix(installer): reuse the models the user already has`: `PrepareToInstall` mueve (rename, sin copiar) los modelos del directorio legacy `%LOCALAPPDATA%\instant\models` al de Instant2 y borra el duplicado para que la copia vieja no quede huerfana; la entrada de modelos lleva `Check: ModelAlreadyInstalled`, que Setup llama una vez por archivo y saltea los que ya estan en el destino (una copia incompleta se completa sin recopiarla). No se tocan el AppId, el mecanismo de AppVersion, `CloseApplications`, `RestartApplications`, `PrivilegesRequired`, `OutputBaseFilename` ni la entrada de `[Files]` de la app. Inno Setup no tiene cobertura automatica en este repo: lo unico verificable sin compilar el instalador es el texto del script (guarda estatica en `dictado/tests/test_installer_script.py`). Evidencia: `python -m unittest tests.test_installer_script` -> Ran 4 tests, OK.
- **La instalacion silenciosa no volvia a abrir la app** `bbd628a` `fix(installer): relaunch Instant2 after a silent install`: la entrada de `[Run]` tenia `skipifsilent`, asi que la actualizacion desde la app (que instala en silencio) dejaba al usuario con la app cerrada y sin ver el resultado. Ahora la instalacion silenciosa tambien abre la app; la instalacion interactiva no cambia (la casilla de la pagina final sigue mandando). Evidencia: `python -m unittest tests.test_installer_script` -> Ran 6 tests, OK.
- **La GUI no arrancaba con Python 3.14** `2b750c6` `fix(gui): start the panel on Python 3.14`: el tipo de callback de Windows se tomaba de `ctypes.wintypes`, donde ese nombre no existe (ni en 3.12 ni en 3.14) - el log de la app instalada 0.1.0 lo prueba: `AttributeError: module 'ctypes.wintypes' has no attribute 'WINFUNCTYPE'`. El tipo vive en `ctypes`: ahora se resuelve en un solo lugar (`gui_lifecycle.winfunctype`), con un error claro si el interprete no lo trae, y lo usan el panel y el atajo de lectura. `requires-python` sigue sin techo: 3.14 anda. Evidencia: `python -m unittest tests.test_windows_callbacks` -> Ran 4 tests, OK (con el codigo anterior fallan: 1 fallo + 1 error) y smoke del panel con `QT_QPA_PLATFORM=offscreen`: "panel abierto (setup)" en 3.14.2 y en 3.12.12.
- **Venv de desarrollo documentado** `d80cafe` `docs(dev): document the 3.12 development environment`: el README de `dictado/` explica que el interprete de desarrollo es CPython 3.12 (el de CI), deja la ruta del 3.12.12 de la maquina y del venv equivalente al de CI, con los comandos de la suite y del pyflakes; el README de la raiz enlaza a esa seccion.

Checks de la tercera tanda: `cd dictado && python -m unittest discover -s tests` -> Ran 575 tests, OK (skipped=6) en 3.14.2 (sistema) y en 3.12.12 (venv de CI); `python -m pyflakes dictado/src/instant2_app/*.py` -> 38 archivos, 0 hallazgos (exit 0 en los dos interpretes); `python -m instant2_app --help` -> exit 0 en los dos interpretes.

Desviaciones y limites de esta tanda:

- **Inno Setup no se pudo compilar ni correr en esta tanda**: el `[Code]` nuevo (el rename del directorio legacy, el borrado del duplicado y el `Check` por archivo) quedo verificado por lectura del script y por la guarda estatica. Esto lo corrigio la quinta tanda: habia Inno Setup 6.7.3 instalado en la maquina (en `%LOCALAPPDATA%\Programs\Inno Setup 6`, no en `Program Files`) y el script no compilaba.
- **El `Check` de los modelos decide por nombre de archivo**: Setup llama a la funcion una vez por archivo del bundle y pasa el nombre con `CurrentFilename`; el archivo se busca por nombre en todo el directorio de modelos. Si un archivo con el mismo nombre estuviera en otro subdirectorio, se lo daria por instalado (la app verifica los pesos por hash y los vuelve a bajar si no verifican).
- **El duplicado del directorio legacy se borra**: cuando `%LOCALAPPDATA%\instant2\models` ya tiene el modelo, la copia del legacy es redundante (la app solo acepta los pesos fijados por hash) y se elimina para que no quede huerfana. Queda escrito en el log de Inno (`/LOG=`).
- **La nota vencida se descarta a las 24 horas**: una ventana mas corta (una hora) reportaria menos, pero reabrir la app a la manana siguiente tiene que seguir mostrando el fallo de la actualizacion de la noche.
- **El `.bat` tiene que quedar con finales de linea CRLF en el arbol de trabajo**: con LF, `cmd` deja de encontrar la etiqueta `:say` y los mensajes no se escriben (passo en esta tanda y quedo anotado para el que edite el archivo).

### Cuarta tanda (la higiene del repo: T9-T12)

El trabajo de benchmark que estaba pendiente en el arbol de trabajo se landeo
por unidades de trabajo revisables. Nada del trabajo de actualizacion ni del
instalador se toco en esta tanda.

- **Los artefactos generados dejan de ser versionables** `55b621c` `chore(repo): ignore the gguf weights and the generated benchmark results`: el `.gitignore` gana `*.gguf` y `dictado/bench/results/`. Motivo: el `.gguf` de la raiz son 1.212.103.520 bytes (1,13 GiB) y esta obsoleto (`bench_llm_runtime.py` apunta a `Ternary-Bonsai-2-9B-Q2_0.gguf`, no al 4B); y `dictado/bench/results/` son 680 archivos / 87.471.302 bytes de salida generada que ademas contiene las claves ciegas (`blind-package-final/blinded_key_v2p.json`, `blinded_key.ALL.json`, `blinded_review.ALL.jsonl`). El `.gguf` **no** se borro del disco: decidir que hacer con la descarga del usuario es del maintainer. Evidencia: `git check-ignore -v` marca las dos rutas y `git ls-files` no muestra ningun `.gguf` ni nada bajo `dictado/bench/results/`.
- **El passthrough de sampling del LLM acepta los seis parametros** `8c90a70` `fix(llm): forward min_p and repetition_penalty to the benchmark sampling`: `min_p` y `repetition_penalty` se ignoraban en silencio, asi que el body de la request no coincidia con los flags del servidor (`--min-p`, `--repeat-penalty`) y la comparacion pareada no tenia base. La produccion no cambia (llama sin `sampling_params` y sigue mandando solo el cuerpo determinista). Evidencia: `python -m unittest tests.test_llm_sampling_params` -> Ran 3 tests, OK.
- **Los corpus congelados se versionan con sus hashes** `ea6eeff` `feat(bench): freeze the correction and V2P corpora with their hashes`: `v2p_cases.json` (72 casos), `v2p_rubric.json`, `v2p_references.sha256`, `llm_cases_expanded_2026-10-16.json` (+ `.sha256`) y `correction-eval-prereg-2026-10-16.json`. Los hashes del manifiesto coinciden con los archivos: `llm_cases_expanded` `6533d230...`, `v2p_cases.json` `7d81ef20...`, `v2p_rubric.json` `6fda4286...`.
- **El harness V2P, el empaquetado ciego y el analisis pareado** `4d7bcfd` `feat(bench): add the V2P harness, the blind packaging and the paired analysis`: `bench_v2p.py`, `prepare_blind_v2p.py`, `aggregate_v2p.py`, `analyze_correction_runs.py` y sus tres pruebas. Evidencia: `python -m unittest tests.test_bench_v2p tests.test_aggregate_v2p tests.test_prepare_blind_v2p` -> OK.
- **El benchmark de correccion gana metricas y baja a dos candidatos** `9a5c249` `feat(bench): extend the correction benchmark with WER, invariants and two models`: WER normalizado, invariantes criticos, ediciones lexicas dañinas, corpus y subconjunto de casos explicitos, y `CANDIDATES` reducido a `qwen3.5-4b-q4` y `bonsai2-9b-q2`. La prueba vieja de runtime fijaba los cinco candidatos que este commit elimina, asi que se retira en este commit y su reemplazo llega en el siguiente.
- **La cobertura del runtime vuelve** `9791c4e` `test(bench): cover the two runtime candidates and the sampling profiles`: `dictado/tests/test_bench_llm_runtime.py` reescrito contra los dos candidatos y el comportamiento actual (flags de GPU/CPU, los dos perfiles de sampling, `_sha256_file`, `_process_memory`, `_wait_for_server` y `run_candidate`, con su reporte de runtime). Evidencia: `python -m unittest tests.test_bench_llm_runtime` -> Ran 14 tests, OK.
- **El CHANGELOG describe el trabajo pendiente** `896f736` `docs(changelog): describe the pending benchmark work`: la seccion `[Sin publicar]` pasa de una linea a las cuatro subsections (herramientas, validacion, correccion y documentacion).
- **Los tres documentos largos pasan a `docs/`** `5824891` `docs: move the LLM audit, the model benchmark and the V2P notes to docs/`: `AUDITORIA-LLM.md`, `BENCHMARK-MODELOS.md` y `VOICE-TO-PROMPT.md` salen de la raiz (donde ya vivian 14 documentos largos) y las referencias cruzadas pasan a la forma `docs/<archivo>.md`. `README.md` y `dictado/README.md` no los mencionaban, asi que no habia enlaces que corregir.

Checks de la cuarta tanda: `cd dictado && python -m unittest discover -s tests` -> Ran 597 tests, OK (skipped=6) en 3.14.2 (sistema) y en 3.12.12 (venv de CI); `python -m pyflakes dictado/src/instant2_app/*.py` -> 38 archivos, 0 hallazgos (exit 0 en los dos interpretes); `python -m instant2_app --help` -> exit 0. Los 597 tests son los 580 de la tanda anterior mas 14 de la prueba de runtime reescrita y 3 de la del passthrough de sampling; ningun test nuevo queda saltado.

Cada commit se verifico por separado (worktree temporal en `C:\PROYECTOS\_wt-verify`, checkout commit por commit): los 8 commits dan `Ran N tests, OK (skipped=7)`. El `skipped=7` de esa corrida es el test del icono (`falta el icono ...\dictado\build\instant2.ico`), que en el worktree no existe porque `dictado/build/` no esta versionado; en el checkout real son 6, los mismos de antes.

Desviaciones y limites de esta tanda:

- **La prueba del passthrough de sampling va en un archivo nuevo**: `dictado/tests/test_llm_sampling_params.py` y no dentro de `test_bench_llm.py`. Motivo: `test_bench_llm.py` (reescrito por el maintainer) depende del corpus congelado y de `analyze_correction_runs`, asi que el cambio de produccion en `llm.py` no se puede revisar en un commit separado sin partir ese archivo.
- **El harness de V2P no se toco**: no se cambio ni una linea de `bench_v2p.py`, `prepare_blind_v2p.py`, `aggregate_v2p.py` ni `analyze_correction_runs.py`. Solo se versionaron.
- **El destino del `.gguf` de 1,13 GiB queda para el maintainer**: el archivo sigue en la raiz del checkout (`TempInstant2-benchmarkrevD-modelsTernary-Bonsai-2-4B-Q2_0.gguf`, 1.212.103.520 bytes) y ahora esta ignorado. Borrarlo, moverlo a una carpeta de modelos o dejar de descargarlo son decisiones de producto.

### Hallazgos de la cuarta tanda (no se cambiaron)

- **`_portable_path` de `bench_v2p.py` da prioridad al perfil de usuario sobre la ruta del repo**: el chequeo de `%LOCALAPPDATA%`/`%TEMP%`/`%USERPROFILE%` va **antes** del `os.path.relpath` al repo (`dictado/bench/bench_v2p.py:1638-1650`). Si el repo esta dentro del perfil de usuario (por ejemplo un worktree en `C:\Users\...\AppData\Local\Temp\...`), una ruta interna del repo se reporta como `%LOCALAPPDATA%\...` en vez de `dictado/bench/v2p_cases.json`, y la prueba `test_portable_path_hides_the_user_profile` de `tests/test_bench_v2p.py` falla. En el checkout real (`C:\PROYECTOS\Instant2`) la prueba pasa. No se cambio el comportamiento del harness: la precedencia es una decision de diseno (un artefacto compartido no debe filtrar la ruta del usuario), pero la prueba asume que el repo no vive bajo el perfil. Se deja anotado para quien lo mantenga. **Resuelto en la sexta tanda (T14)**: la precedencia se invirtió porque una ruta dentro del repo no es una fuga del perfil, y el enmascarado se conserva para lo que es de verdad un archivo del usuario.
- **El bundle offline lleva 3 GB de pesos del LLM**: `installer/instant2.iss` copia 4.010.135.264 bytes, de los que ~3 GB son el LLM opcional. Sacarlo del bundle cambia el producto (el instalador deja de ser offline para el LLM) y por lo tanto el release: estaba declarado fuera de alcance en este documento y sigue pendiente de una propuesta aparte.
- **La lista `CANDIDATES` del runtime ya no cubre el 4B de Bonsai**: `bench_llm_runtime.py` apunta a `Ternary-Bonsai-2-9B-Q2_0.gguf`, pero el `.gguf` descargado en la maquina es el 4B (`Ternary-Bonsai-2-4B-Q2_0.gguf`). El 4B se midio en las corridas V2P (`bonsai2-4b-q2-0-revC`) con otro arnes, no con el runtime de correccion.


### Quinta tanda (el script de Inno Setup no compilaba)

El `.iss` que la tercera tanda dejo escrito **no compilaba**. Nadie lo noto
porque el repo no tiene cobertura de Inno Setup y la maquina del maintainer
tenia Inno Setup 6.7.3 instalado en `%LOCALAPPDATA%\Programs\Inno Setup 6`
(ruta por usuario, no `Program Files`, que es donde se lo busca).

- **El script de Inno Setup no compilaba** `fc2f600` `fix(installer): make the Inno Setup script compiles and install the models`: tres fallos en el `[Code]`.
  1. La firma del `Check` era `(Installer: TInstaller; WizardForm: TWizardForm; Param: String)`. `TInstaller` **no existe** en el dialecto de Inno Setup 6 y el compilador para con `Unknown type 'TInstaller'`. La firma valida es la de sin parametros, como en los ejemplos oficiales de Inno 6.
  2. Los comentarios de la seccion usaban `;`. En Pascal `;` es separador de sentencia, **no** un comentario, y el parser aborta con `'BEGIN' expected`. Ahora van con `//`.
  3. El `Check` devolvia "ya esta instalado" **sin invertir**. En Inno Setup, `Check` devuelve `True` para **instalar** el archivo: el instalador saltaba justo los modelos que faltaban y volvia a copiar los que ya estaban. Con 4 GB de bundle, una maquina nueva se quedaba sin modelos y una maquina con los modelos pagaba la copia entera otra vez.
  La prueba del instalador ahora **compila el script de verdad** cuando hay ISCC, y fija las tres guardas que habrian atrapado esto: la firma sin parametros, la semantica invertida y la prohibicion de comentarios `;` dentro del `[Code]`.

Verificacion (Inno Setup 6.7.3, en la maquina del maintainer):

- `ISCC.exe` sobre `installer/instant2.iss` con `AppVersion`, `SourceDir`, `ModelDir` e `IconFile` de prueba: **compila**, exit 0.
- Instalacion real de prueba con un `.iss` derivado (los paths de modelos apuntando a un temporal, con un guard que aborta si queda alguna ruta real) y los **cuatro escenarios**:

| escenario | resultado |
|---|---|
| sin modelos en ningun lado | se copia el bundle completo |
| solo los del slug legacy | los legacy se **mueven** (0 bytes copiados) y solo se copia lo que faltaba (kokoro y LLM) |
| solo los del slug nuevo | no se copia nada |
| los dos | se borra el duplicado legacy, no se copia nada, no queda huerfano |

- El orden es el correcto: `PrepareToInstall` mueve los modelos **antes** de que Setup evalue el `Check` por archivo, asi que el `Check` ya ve el destino y saltea lo que se acabo de mover.

Limites y accidents de esta tanda:

- **La primera ronda de instalaciones de prueba escribio en la maquina real**: el `DestDir` del `[Files]` de modelos es fijo (`{localappdata}\instant2\models`) y el probe solo aislaba las constantes de Pascal. Se crearon 7 archivos dummy en `%LOCALAPPDATA%\instant2\models` (que antes no existia) y **se borraron a mano**; el estado original quedo restaurado (`%LOCALAPPDATA%\instant\models` intacto, 640 MB, y `%LOCALAPPDATA%\instant2\models` otra vez inexistente). Los probes siguientes llevan un guard que aborta si el `.iss` derivado menciona una ruta real.
- **El bundle offline sigue llevando 3 GB de pesos del LLM**: con el fix, una actualizacion sobre una maquina con los modelos del slug legacy copia 0 bytes de ASR, pero sigue copiando los ~3,3 GB de `kokoro-onnx` y del `.gguf` del LLM. Sacarlos cambia el producto y el release: sigue declarado fuera de alcance.

### Sexta tanda (el CI verde en las tres plataformas)

El CI de GitHub (`python -m unittest discover -s tests` sobre windows, ubuntu
y macos) estaba **rojo en Linux y macOS**: 4 fallos y 31 errores. En Windows
todo estaba verde. La causa no era el codigo: era que **el arbol de trabajo
tenia finales de linea distintos en cada sistema**.

#### T13 `.gitattributes`: los finales de linea del repositorio quedan fijados

- **Causa**: `core.autocrlf=true` en Windows deja el arbol de trabajo con CRLF
  y en Linux y macOS con LF. Los hashes congelados se calcularon contra los
  bytes del archivo **como estaba en el arbol de trabajo de Windows** (CRLF),
  asi que en Linux y macOS el SHA256 del corpus daba otro: los 31
  `test_prepare_blind_v2p` reventaban con `RuntimeError: el manifiesto
  congelado no coincide: corpus_file_sha256 ...` y
  `test_bench_v2p.FrozenWiringTests.test_frozen_manifest_matches_the_corpus_and_rubric`
  fallaba con `Lists differ: ['corpus_file_sha256 cambio (7d81ef20...)']`.
- **Fix**: `.gitattributes` nuevo con `* text=auto eol=lf` y las excepciones
  que hacen falta (`*.bat`, `*.cmd`, `*.ps1` y `*.iss` con `eol=crlf`;
  `*.sh` con `eol=lf`; `*.png`, `*.ttf` y `*.gguf` como `binary`). Con
  `git add --renormalize .` el indice y el arbol de trabajo quedan de acuerdo
  con las reglas.
- **Por que `*.bat` y `*.iss` se quedan con CRLF**: con LF, `cmd` deja de
  encontrar la etiqueta `:say` de `scripts/instant2-update.bat` y los mensajes
  del helper dejan de escribirse **sin que nada lo avise**. Inno Setup
  tambien. Los `.sh` al reves: `bash` no tolera CRLF.
- **Los binarios no se tocan**: el SHA256 de `Manrope.ttf` y de las nueve
  capturas de `docs/screenshots/` es el mismo antes y despues de la
  renormalizacion (comparado byte a byte).

Hashes que cambiaron (los unicos): el manifiesto se recalculo con
`python bench/bench_v2p.py --freeze` **sobre el arbol de trabajo ya en LF**,
que es el canonico del repositorio:

| clave | antes (CRLF del arbol de trabajo de Windows) | despues (LF canonico) |
|---|---|---|
| `corpus_file_sha256` | `7d81ef200386766e55a8e43882d319c71dd56eb2c03ce0ff51ab1a64cddf4d99` | `b1e3a5fa6984e3fe887a5d9d8c2d695440e0a7653bdbeeeaec5442b7329c4781` |
| `rubric_sha256` | `6fda42869c6cddfd32330bb3e46a058aa207de5ac6d0f36559c08564f00cf750` | `c35147c6c39d16fedffbe3ee0c34259ff0d17bf7dfd440d1f6ec4695f3f172cc` |
| `references_sha256` | `352a1931c46db4bf134a42487fb977065b112ae2698e5c35fa675c6d0ec194db` | sin cambio (se calcula sobre el JSON parseado) |
| `prescoring_sha256` | `43f7dd03ac206b92db922e7825a3f900cf9574a0d58a52ce1a7711126ed12bcc` | sin cambio (idem) |
| `case_count` | `72` | sin cambio |

`dictado/bench/llm_cases_expanded_2026-10-16.sha256` y el `corpus_sha256` de
`dictado/bench/correction-eval-prereg-2026-10-16.json` **no cambiaron**: los
dos ya traian el hash canonico (`6533d23077eb9ae18f3548660fc95502bfc04c368cde14af67f3e46ea9ebfc69`),
porque ese corpus se congelo en una maquina con LF. Ahora una prueba nueva
comprueba que los dos coinciden con los bytes canonicos del corpus.

- **La prueba nueva compara los bytes canonicos, no los del disco**:
  `test_bench_v2p.FrozenWiringTests.test_the_frozen_manifest_matches_the_canonical_bytes`
  normaliza CRLF a LF antes de hashear, asi que el manifiesto se puede
  verificar aunque el archivo llegue al disco con otra representacion. El
  hash que usa el harness (`corpus_digests`/`rubric_digest`) sigue siendo el
  de los bytes tal cual: no se toco.

#### T14 `_portable_path`: el repo gana al perfil de usuario

- **Causa**: `_portable_path` (`dictado/bench/bench_v2p.py:1628`) chequeaba
  `%LOCALAPPDATA%`, `%TEMP%` y `%USERPROFILE%` **antes** del
  `os.path.relpath` al repo. El CI hace checkout en `/home/runner/work/...`,
  o sea dentro del home del runner: una ruta interna del repo se reportaba
  como `%HOME%/work/Instant2/Instant2/dictado/bench/v2p_cases.json` en vez de
  `dictado/bench/v2p_cases.json`.
- **Por que la precedencia estaba al reves**: la intencion del chequeo es que
  un artefacto compartido no filtre la ruta del usuario, pero una ruta
  **dentro** del repositorio no es una fuga del perfil: es el repositorio. Se
  invirtio la precedencia (repo primero, perfil despues) y se le exigio a la
  ruta ser **absoluta** para entrar en la rama del repo: una ruta relativa se
  resuelve contra el directorio actual y pareceria interna por accidente (en
  POSIX, `C:\Users\...` no es absoluta y `relpath` la metia dentro del repo).
- **El enmascarado del perfil se conserva** para lo que de verdad es del
  usuario: un archivo externo bajo el home sigue saliendo como
  `%HOME%/...`/`%USERPROFILE%/...` y uno fuera del home como su ultimo
  componente.
- Pruebas nuevas: `test_the_repository_wins_over_the_user_profile` (checkout
  **dentro** del home) y `test_a_file_outside_the_repository_keeps_its_home_masked`
  (checkout **fuera** del home).

#### T15 `test_windows_callbacks`: lo que es de Windows, se salta en POSIX

`test_the_callback_type_comes_from_ctypes` afirmaba que `ctypes.WINFUNCTYPE`
existe: en Linux y macOS no existe (vive en `ctypes` solo en Windows), asi que
la prueba fallaba ahi. Ahora lleva `@unittest.skipUnless(os.name == "nt", ...)`,
igual que `test_user32_resolves_the_callback_type_on_this_interpreter`. Lo que
**no** es de Windows se sigue ejecutando en los tres sistemas: que ningun
modulo del paquete tome el tipo de callback de `ctypes.wintypes`, y que el
helper diga con un mensaje claro cuando el interprete no trae el tipo.

Checks de la sexta tanda: `cd dictado && python -m unittest discover -s tests`
-> Ran 610 tests, OK (skipped=6) en 3.14.2 (sistema) y en 3.12.12 (venv de
CI); `python -m pyflakes dictado/src/instant2_app/*.py` -> 38 archivos, 0
hallazgos (exit 0 en los dos interpretes); `python -m instant2_app --help` ->
exit 0.

Limites de esta tanda:

- **Linux y macOS no se pudieron correr en esta maquina**: no hay WSL con
  distribucion instalada ni Docker. El comportamiento POSIX de
  `_portable_path` se simulo swapping `os.path` por `posixpath` y `os.sep` por
  `"/"` (con el layout `/home/runner/work/Instant2/Instant2` del CI): el
  archivo del repo sale `dictado/bench/v2p_cases.json`, el archivo externo
  bajo el home `%USERPROFILE%/models/qwen.gguf` y el de fuera del home
  `qwen.gguf`. Lo que va a comprobar el CI es lo mismo, sobre el checkout
  real de cada runner.
- **El hash crudo del harness sigue dependiendo de como esta el archivo**:
  `corpus_digests()` y `rubric_digest()` hashean los bytes tal cual. Con
  `.gitattributes` el archivo llega siempre con LF, asi que coinciden; si
  alguien materialize el archivo a mano con CRLF, la comparacion cruda
  (`verify_manifest`) falla y la canonica pasa. No se cambio el harness
  porque el contrato del manifiesto es "los bytes del archivo".
- **`--freeze` escribe el manifiesto con los finales de linea de la
  plataforma**: `write_manifest` abre el archivo con el modo texto de Python,
  asi que en Windows deja CRLF. Git lo normaliza a LF al commitear (por
  `.gitattributes`), asi que el blob commiteado es siempre el mismo; el
  archivo en disco queda con CRLF hasta el proximo checkout.
- **El `.bat` y el `.iss` no se tocaron**: solo cambia como se materializan
  (CRLF en el arbol de trabajo, que es como estaban antes).


### Cierre (la version 0.1.6 esta publicada)

- Los tres PRs del stack quedaron mergeados y con el CI verde en Windows,
  Linux y macOS:
  - #1 `fix(update): make the in-app update observable, honest and quiet`
  - #2 `fix(installer): make the Windows installer compile, reuse the models and reopen the app`
  - #3 `chore(repo): land the pending benchmark work and clean the working tree`
- La release `instant2-v0.1.6` salio del workflow de release con los nueve
  jobs en verde, incluido el build de Windows: **el script de Inno Setup
  compila en el runner de verdad**, que era la prueba que faltaba.
  Assets: `Instant2-Setup.exe` + tres volumenes `.bin` (el instalador de
  Windows), `Instant2.exe` (portable) y los binarios de Linux y macOS, cada
  uno con su sidecar SHA256.

Lo que el CI атrapó y no se habia visto en local (todo en Linux y macOS):

- **El manifiesto congelado no coincidia fuera de Windows.** Los hashes se
  calcularon contra el arbol de trabajo de Windows, con CRLF; en Linux y
  macOS el mismo archivo tiene LF. Se agrego `.gitattributes` para que el
  arbol de trabajo sea LF en las tres plataformas (con `*.bat`, `*.cmd`,
  `*.ps1` y `*.iss` en CRLF, que es lo que necesitan) y se regeneraron los
  hashes con el propio `--freeze` del harness.
- **Un daemon que ya no estaba se reportaba como error.** En POSIX, frenar
  un PID inexistente lanza `ProcessLookupError` y eso se mostraba como "No
  se pudo detener Instant2" con un detalle de "sin conexion al servidor": un
  toast de error sobre algo que no habia pasado, con la app funcionando
  igual. En Windows el mismo caso es `taskkill` fallando en silencio, asi que
  la app se comportaba distinto en cada sistema.
- **La ruta del repositorio ganaba o perdia contra el perfil del usuario**
  segun donde estuviera el checkout, y el CI hace checkout dentro de
  `/home/runner/work`. Ahora la ruta del repo gana cuando el archivo esta
  dentro del repo, y el enmascarado del perfil se mantiene para lo que es
  de verdad externo.
- **Un test de callbacks de Windows corria en POSIX** sin guarda.

Queda pendiente de una propuesta aparte (cambia el producto y el release):
el bundle offline lleva 3,3 GB de pesos del LLM opcional que la app ya
gestiona por su cuenta, y por eso instalar una actualizacion de Windows
sigue siendo descargar varios gigabytes.

### Los modelos ahora los baja la app (2026-10-20)

El instalador de Windows dejo de llevar los pesos (commit `10ad7c9`): el
payload cae de 3,74 GiB al tamano de la app mas el runtime de llama.cpp.
La cifra de «2 MB» que se midio con ISCC 6.7.3 venia de una compilacion
con el directorio de la app vacio (un stub de onedir): no describe el
instalador real, que ademas lleva la app de PyInstaller y el runtime. Eso deja sin dueno la migracion de los modelos del
directorio anterior, que hacia el propio instalador (`ReuseLegacyModels`), y
obliga a la app a bajar sus propios modelos, como ya hacia en Linux y macOS.
Lo que quedaba pendiente en el cierre de la 0.1.6 ("el bundle offline lleva
3,3 GB de pesos del LLM opcional que la app ya gestiona por su cuenta") queda
resuelto de ese lado: el instalador ya no lleva ni el LLM ni la voz.

Que se decidio:

- **La migracion de los modelos del directorio legacy pasa a la app.**
  `paths.migrate_legacy_models()` reproduce la semantica del instalador: si
  el directorio actual no existe se mueve el anterior entero (un rename, cero
  bytes copiados) y si ambos existen se mueve lo que falta y se borra la copia
  duplicada, para que el directorio viejo no quede como un duplicado huerfano.
  Se llama desde `resolve_data_dir()`, el unico punto por el que pasan
  `setup`, el panel, el daemon y el motor. Con `INSTANT2_DATA` puesto no se
  toca nada: el override explicito del usuario gana. Es idempotente y nunca
  propaga un error, porque toda la app depende de esa ruta.
- **El reconocimiento se baja solo la primera vez.** El panel arranca la
  descarga cuando detecta que faltan los modelos, reusando el camino de
  descarga y la UI de progreso y cancelacion que ya existian (no hay una
  segunda via de descarga). Una vez por instalacion: la marca queda en el
  directorio de datos, asi que si la persona cancela o la descarga falla, el
  boton "Reintentar descarga" es suyo y esta via no compite con el. No
  arranca con el panel cerrandose ni en el modo de diagnostico, y con el
  pedido de arranque pendiente el dictado arranca solo cuando terminan de
  bajar los modelos.
- **El modelo de lenguaje local (3 GB) es una descarga a pedido.** El runtime
  de llama.cpp sigue en el instalador, pero los pesos no: el GGUF se baja
  desde Ajustes, con su progreso y su cancelacion, porque 3 GB no se bajan
  sin permiso. El modulo nuevo `llm_model.py` reutiliza el descargador de
  `models` (reanudable con `Range`, SHA-256 antes de publicar el archivo,
  reintentos y control de espacio en disco). El runtime local usa ese GGUF
  cuando la persona no eligio ninguno a mano.
- **La marca de la config no se renombra.** `llm_bundle_initialized` conserva
  su nombre para que las configs de los usuarios sigan sirviendo; lo que
  cambia es su significado: "el LLM local quedo configurado solo".

Commits:

- `01396bb` `feat(paths): reutilizar los modelos del directorio anterior en lugar de volver a bajarlos`
- `6f459e8` `feat(gui): el panel baja solo el reconocimiento la primera vez`
- `c8c2ee4` `feat(llm): descargar el modelo de lenguaje local a pedido desde Ajustes`

Lo que queda para la persona: los 3 GB del LLM local no se bajan solos, a
proposito; el reconocimiento si, y son 670 MB.

### Septima tanda (instalador chico: la app trae sus propios modelos)

Decision de producto: el instalador de Windows deja de llevar los 4 GB de pesos.
Pasa de 3,78 GB a 204 MB (la app + el runtime de llama.cpp), y la app descarga
sus componentes de voz: el reconocimiento solo la primera vez (670 MB) y el
modelo de lenguaje local a pedido desde Ajustes (3 GB, no baja solo).

- **El bundle offline desaparece del pipeline**: `scripts/prepare_offline_bundle.py`
  pasa a `scripts/prepare_windows_assets.py` y solo arma lo que va en `{app}` (el
  runtime de llama.cpp, los avisos, el script de parada y las licencias).
  `installer/instant2.iss` pierde la entrada de modelos y el `[Code]` que
  movia los del directorio anterior.
- **La app reutiliza los modelos que ya tiene**: la migracion de
  `%LOCALAPPDATA%\instant\models` a `%LOCALAPPDATA%\instant2\models` pasa a ser
  un paso explicito del arranque (`paths.migrate_legacy_models_once`), con
  marcador, y con el override `INSTANT2_DATA` ganando siempre. Una entrada con
  el mismo nombre solo se borra del directorio anterior si el destino esta
  verificado; si no, se conserva con otro nombre (`.legacy`) y se avisa.
- **La descarga del LLM local deja de ser un final de camino**: la revision
  adversarial mostro que la descarga de 3 GB no configuraba nada
  (`llm_bundle_initialized` se quemaba sin configurar y el daemon rechazaba
  arrancar el runtime). Ahora la descarga deja el servicio local completo y el
  proceso de dictado puede usarlo.
- **La suite ya no puede romper la maquina**: `dictado/tests/_isolation.py`
  redirige `APPDATA`, `LOCALAPPDATA`, el HOME de POSIX y los overrides de la
  app a un temporal, y preserva el user-site de Python. Antes, correr la suite
  habia reseteado el `config.json` real del usuario y movido sus modelos.

La release `instant2-v0.1.7` salio publicada con los nueve jobs en verde, y
quedo **instalada y verificada en la maquina**: 0.1.7, daemon vivo, los 671 MB
de modelos intactos y sin una sola descarga, y el log de Inno confirmando que
la app se relanzo sola tras la instalacion silenciosa.

Pendiente de verificar en el proximo release real: el flujo completo del
actualizador in-app (descargar, instalar y verificar la version) con el
instalador chico, porque hasta ahora no hay una release mas nueva que la
instalada.

### Octava tanda (la 0.1.7 no se podia instalar: pedia "el disco")

Reporte del maintainer: al bajar la 0.1.7 e instalarla, el instalador pide un
disco. Diagnostico: `DiskSpanning=yes` en el `.iss`. Con esa directiva Inno
siempre parte el setup en `Instant2-Setup.exe` (2,2 MB, solo el programa) +
`Instant2-Setup-1.bin` (201,7 MB, los datos). Quien baja el `.exe`, que es el
enlace que ofrece la pagina del release, ejecuta un instalador sin payload: no
falla al compilar ni en CI, falla en la cara del usuario pidiendo "el disco"
con el `.bin`.

- **Un solo archivo, siempre**: `DiskSpanning=no`. La compilacion real del
  instalador completo da 957 MB en un unico `.exe` (verificado con ISCC
  6.7.3), sin `.bin` y sin ninguna pregunta.
- **La voz vuelve al instalador**: el dueno del producto corrio la decision
  anterior (instalador chico, todo online) al ver el costo real: sin los
  modelos, una instalacion nueva espera ~670 MB (6 a 9 minutos) y sin
  internet no dicta. La 0.1.8 vuelve a llevar Parakeet + Silero + Kokoro
  (951 MB) para que la app funcione desde el primer minuto, y deja afuera
  solo el LLM de 3 GB, que era el 76% del viejo bundle de 4 GB.
- **La actualizacion sigue siendo barata**: se compilan dos instaladores con
  el mismo `.iss` (`#ifdef ModelDir` y `{#OutputBase}`): `Instant2-Setup.exe`
  completo y `Instant2-Update.exe` solo app (204 MB). El actualizador in-app
  prefiere el segundo cuando los modelos ya estan en el directorio de datos.
- **El instalador no borra ni mueve nada del directorio legacy**: el `Check`
  saltea el archivo que ya esta en el directorio actual o en el viejo, y la
  migracion verificada la sigue haciendo la app al arrancar.
- Verificado en la maquina: el instalador completo compilado localmente
  instala en 40 s, sin pedir disco, relanza la app y no recopia ni un byte de
  los 951 MB que ya estaban (los timestamps de los modelos no cambiaron).

Pendiente: publicar la 0.1.8 y confirmar en el proximo release real que el
actualizador in-app baja `Instant2-Update.exe` (204 MB) y no el completo.

### Novena tanda (Qwen queda activo solo, sin pasos extra)

Reporte del maintainer: "siempre tiene que funcionar, Qwen es el chiste,
Parakeet es mierda al transcribir, Qwen la mejora. Quiero que apenas se
seleccione descargar Qwen en Instant2, apenas se instale se configure sola y
ya funcione a partir de siempre, a menos que se especifique lo contrario".

Lo que faltaba de verdad, con la 0.1.8 instalada:

- La descarga del GGUF si dejaba escrita la ruta del modelo, pero el resto
  (runtime, direccion, alias, marca de "configurado") lo completaba `config.load()`
  **en el arranque siguiente**. Con la app abierta, el daemon ya habia leido
  una configuracion sin servicio local y se quedaba en `needs_setup`: 3 GB
  descargados y el servicio sin arrancar hasta que alguien reiniciaba.
- La revision con LLM (`llm_normal_enabled`) era el interruptor correcto, pero
  ningun camino la encendia sola.

Que se implemento:

- `llm_defaults.auto_wire_changes()`: calcula el cambio de configuracion que
  deja el LLM local **completo y administrable** (runtime, `llm_url`,
  `llm_model`, `llm_model_path`, `llm_bundle_initialized`) y encendida la
  revision con LLM. Si la persona apago la revision a proposito, esa decision
  se respeta: el modelo se conecta pero el servicio no se enciende solo. Un
  GGUF elegido a mano sigue sin tocarse.
- `gui._use_downloaded_llm_model()` ahora usa esos cambios y devuelve si el
  servicio quedo completo; se llama al terminar la descarga y al verificar por
  hash un GGUF que ya estaba en disco. La descarga reconecta el servicio
  aunque la direccion estuviera vacia (bajar 3 GB es quererlo); la
  verificacion respeta la decision de vaciarla.
- **El daemon relee la configuracion mientras corre** (`daemon._reload_config_if_changed`
  y `daemon._sync_llm_runtime`). El daemon vive en segundo plano y la config
  puede cambiar en cualquier momento: el panel conecta el servicio local al
  terminar la descarga, o al arrancar si el modelo ya estaba en el disco. Sin
  esto, el daemon se quedaba en "No hay un servicio de lenguaje configurado"
  hasta que alguien reiniciaba el dictado a mano (que es lo que paso en la
  maquina: el modelo estaba en el disco, la config se completo al arrancar y
  el daemon, que ya corria, nunca la vio).
- `llm_normal_enabled` ya venia en `True` por defecto; la garantia nueva es
  que el servicio queda completo y arranca, y que la decision de apagarlo se
  respeta.

Evidencia: `cd dictated && python -m unittest discover -s tests` -> Ran 694
tests, OK (skipped=6) en 3.12.12 (venv de CI) y en 3.14.2 (sistema);
`python -m pyflakes dictado/src` y `python -m pyflakes scripts` -> 0 hallazgos.
Los tests nuevos (`test_llm_local_service.py`) fijan: la descarga deja la
revision encendida y el servicio administrable; una revision apagada a mano no
se enciende sola; la descarga reconecta una direccion vaciada a proposito; el
daemon relee la config cuando el archivo cambia; y el servicio local arranca
(o se frena) segun lo que diga la config.

Pendiente: publicar la 0.1.9 y verificar en la maquina que, con la app
instalada, la descarga de Qwen desde Ajustes deja `llama-server` corriendo y
que el dictado sale revisado por el LLM.
