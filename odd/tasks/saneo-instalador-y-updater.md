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

- [ ] **T7** `installer/instant2.iss`: si existen modelos en el directorio legacy (`%LOCALAPPDATA%\instant\models`), se reutilizan en vez de volver a copiar los 4 GB; el bundle queda en `ignoreversion` solo para lo que no existe.

### Bloque 3 - Python

- [ ] **T8** `dictado/pyproject.toml`: techo en `requires-python` mientras la GUI dependa de `ctypes.wintypes.WINFUNCTYPE`; documentar el venv de desarrollo en 3.12.

### Bloque 4 - Higiene del repo

- [ ] **T9** `.gitignore`: reglas para `*.gguf` y `dictado/bench/results/` (con las claves ciegas).
- [ ] **T10** Tests: reescribir `dictado/tests/test_bench_llm_runtime.py` contra los 2 candidatos vigentes y recuperar el test del passthrough de `sampling_params` en `llm.py` (`min_p`, `repetition_penalty`).
- [ ] **T11** `CHANGELOG.md`: `[Sin publicar]` describe los ~13 cambios pendientes; `AUDITORIA-LLM.md`, `BENCHMARK-MODELOS.md` y `VOICE-TO-PROMPT.md` a `docs/`.
- [ ] **T12** Commits por unidad de trabajo para todo el trabajo de benchmark pendiente.

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

Desviaciones y limites de esta tanda (no tocan T7-T12):

- La version instalada se verifica en el **arranque siguiente**, no en el proceso que instala: el instalador de Inno y el helper portable tienen que cerrar la app para poder reemplazar el binario (`installer/instant2.iss` corre `Instant2.exe stop` en `PrepareToInstall`), asi que el proceso que verifica ya no existe. Para eso la app anota la version pedida en `%APPDATA%\instant2\pending-update.json` y, al arrancar de nuevo, la compara con la version que esta corriendo (que ES la instalada): si no coinciden, sale un toast de error con el log adjunto.
- El helper se lanza sin terminal (`CREATE_NO_WINDOW`), asi que `timeout /t` (que necesita consola) se cambio por `ping -n` en `scripts/instant2-update.bat`.
- La ruta `installed` de Windows sigue sin reabrir la app al terminar: el `.iss` tiene `skipifsilent` en `[Run]` y ese archivo es T7, no de esta tanda.

## Ruta y disparadores de delegacion

- T1-T6, T7, T8, T10 tocan 2 o mas archivos no triviales -> **writer delegado** (un writer por bloque).
- T9, T11 (edicion de `.gitignore` y CHANGELOG) son mecanicas -> **inline**.
- La verificacion de los writers corre en la suite completa mas el pyflakes, con lectura de vuelta del diff en el padre.
