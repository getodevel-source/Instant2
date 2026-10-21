@echo off
REM Aplica una version descargada y verificada, desde el repo o el portable.
REM Uso: instant2-update.bat [nuevo-Instant2.exe] [sha256] [destino-Instant2.exe]
REM Re-verifica el hash, frena Instant2, respalda el exe, reemplaza, comprueba
REM que el daemon nuevo levante (si no, restaura) y abre el panel.
REM El helper reabre el panel en TODOS sus caminos de salida, tambien en los
REM que fallan: la app se cierra a proposito (hay que reemplazar el binario)
REM y el que la devuelve es este script. Si no, el usuario se queda con la
REM app cerrada y sin forma de saber que paso.
REM En exito borra %DST%.bak (no se acumulan respaldos); en rollback se
REM conserva para vuelta atras manual.
REM El log %APPDATA%\instant2\update.log es de este helper: lo escribe con
REM ">>" y nadie mas lo abre. La app lanza este helper sin terminal
REM (CREATE_NO_WINDOW) y con la salida del hijo apuntada a otro archivo
REM (INSTANT2_UPDATE_LOG_REDIRECTED=1): si la app tomara el mismo log para
REM la salida, los dos se pelean por el archivo y los mensajes del helper
REM no llegan nunca al log.
REM Ademas el codigo de salida queda en %APPDATA%\instant2\helper-result.json
REM (como installer-result.json del instalador de Inno): la app lo lee en el
REM arranque siguiente y reporta el fallo con el log al lado.
setlocal
set "LOGDIR=%APPDATA%\instant2"
set "LOG=%LOGDIR%\update.log"
set "RESULT=%LOGDIR%\helper-result.json"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "ROOT=%~dp0.."
set "RC=0"

REM El destino se resuelve antes de validar los argumentos: el helper es el
REM que reabre el panel en TODOS sus caminos de salida (la app se cierra a
REM proposito para reemplazar el binario) y necesita saber cual es el
REM ejecutable, tambien cuando la llamada vino mal formada.
if not "%~3"=="" goto :explicit_dest
if exist "%ROOT%\dist\Instant2.exe" set "DST=%ROOT%\dist\Instant2.exe"
if defined DST goto :dest_ready
set "DST=%ROOT%\Instant2.exe"
goto :dest_ready
:explicit_dest
set "DST=%~3"
:dest_ready
set "PIDFILE=%APPDATA%\instant2\instant2.pid"
for %%D in ("%DST%") do set "DSTFULL=%%~fD"

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if not exist "%~1" goto :missing
if /I "%~f1"=="%DSTFULL%" goto :selfcopy

set "MSG==== Actualizacion %DATE% %TIME% ==="
call :say
set "MSG=[1/6] Verificando SHA256..."
call :say
for /f "skip=1 tokens=1" %%H in ('certutil -hashfile "%~1" SHA256 2^>nul') do (
  if /I not "%%H"=="%~2" goto :hashfail
  goto :hashok
)
goto :hashfail
:hashok
set "MSG=Hash OK."
call :say

REM Cada proceso portable onefile debe extraer sus propios recursos, incluso
REM los subprocesos stop/run/setup que viven fuera del proceso anterior.
set "PYINSTALLER_RESET_ENVIRONMENT=1"

set "MSG=[2/6] Frenando daemon..."
call :say
if exist "%~dp0instant2-stop.bat" (
  call "%~dp0instant2-stop.bat" >nul 2>&1
) else (
  "%DST%" stop >nul 2>&1
)

set "MSG=[3/6] Esperando que se cierren las ventanas Instant2..."
call :say
set "INSTANT2_TARGET=%DST%"
powershell -NoProfile -Command "$target=[IO.Path]::GetFullPath($env:INSTANT2_TARGET); $until=(Get-Date).AddSeconds(12); do { $running=Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $target }; if (-not $running) { exit 0 }; Start-Sleep -Milliseconds 200 } while ((Get-Date) -lt $until); exit 1" >nul 2>&1
if errorlevel 1 goto :locked

set "MSG=[4/6] Respaldando y reemplazando..."
call :say
del "%DST%.bak" 2>nul
if exist "%DST%" move /Y "%DST%" "%DST%.bak" >nul
if exist "%DST%" goto :locked
copy /Y "%~1" "%DST%" >nul
if errorlevel 1 goto :copyfail

set "MSG=[5/6] Arrancando daemon nuevo..."
call :say
REM El stderr del proceso que arranca no va al log: Windows suelta avisos
REM de "not compatible with the version of Windows" que no dicen nada del
REM resultado de la actualizacion y ensucian el log.
start "" "%DST%" run 2>nul
ping -n 9 127.0.0.1 >nul 2>nul
set "NEWPID="
if exist "%PIDFILE%" set /p NEWPID=<"%PIDFILE%"
if not defined NEWPID goto :rollback
REM Health-check: el PID del pidfile tiene que existir, ser un Instant2 (o
REM python corriendo este DST) y su cmdline tiene que apuntar al DST nuevo.
REM Solo el nombre de imagen no alcanza: otro python/instant2 viejo daria OK.
tasklist /FI "PID eq %NEWPID%" /FO TABLE /NH 2>nul | findstr /I "instant2.exe python.exe pythonw.exe" >nul
if errorlevel 1 goto :rollback
set "DSTCHECK=%DST%"
powershell -NoProfile -Command "$p=(Get-CimInstance Win32_Process -Filter ('ProcessId='+$env:NEWPID) | Select-Object -First 1); if (-not $p) { exit 1 }; $exe=[IO.Path]::GetFullPath($p.ExecutablePath); $want=[IO.Path]::GetFullPath($env:DSTCHECK); if ($exe -ieq $want) { exit 0 }; if ($p.CommandLine -and $p.CommandLine -like ('*'+$want+'*')) { exit 0 }; exit 1" >nul 2>&1
if errorlevel 1 goto :rollback
goto :healthy

:rollback
set "MSG=ERROR: el daemon nuevo no arranco; se restaura la version anterior."
call :say
del "%DST%" 2>nul
if exist "%DST%.bak" move /Y "%DST%.bak" "%DST%" >nul
REM `start` con un ejecutable que no existe se queda esperando: solo se
REM arranca el daemon viejo si el archivo quedo en su lugar.
if exist "%DST%" start "" "%DST%" run 2>nul
set "MSG=ERROR: la actualizacion NO se aplico; se revirtio a la version anterior."
call :say
set "MSG=Log completo: %LOG%"
call :say
set "RC=1"
goto :reopen

:healthy
set "MSG=Daemon nuevo en marcha."
call :say
REM Exito verificado: el respaldo ya cumplio (rollback disponible hasta aca).
del "%DST%.bak" 2>nul

:reopen
REM El codigo de salida se anota ANTES de reabrir la ventana: la app que
REM vuelve a abrir lee el resultado y reporta el fallo con el log al lado.
call :result %RC%
set "MSG=[6/6] Reabriendo ventana..."
call :say
REM `start` con un ejecutable que no existe no vuelve: Windows se queda
REM esperando. Solo se reabre el panel si el archivo esta.
if exist "%DST%" (
  start "" "%DST%" setup 2>nul
  REM Sin terminal (CREATE_NO_WINDOW) `timeout` no puede leer la entrada:
  REM el ping espera los mismos segundos y no depende de la consola.
  ping -n 3 127.0.0.1 >nul 2>nul
) else (
  set "MSG=ERROR: no se pudo reabrir el panel: no existe %DST%"
  call :say
)
if "%RC%"=="0" (
  set "MSG=Listo: Instant2 actualizado. Log: %LOG%"
) else (
  set "MSG=ERROR: la actualizacion fallo. Log: %LOG%"
)
call :say
exit /b %RC%

REM Los caminos de fallo tambien reabren el panel (y salen con codigo 1): la
REM app se cierra a proposito y el helper es el que la devuelve.
:usage
set "MSG=Uso: instant2-update.bat [nuevo-Instant2.exe] [sha256] [destino-Instant2.exe]"
call :say
set "RC=1"
goto :reopen
:missing
set "MSG=ERROR: no existe el archivo a instalar: %~1"
call :say
set "RC=1"
goto :reopen
:selfcopy
set "MSG=ERROR: el origen es el propio destino; nada que instalar."
call :say
set "RC=1"
goto :reopen
:hashfail
set "MSG=ERROR: el SHA256 no coincide; no se instala nada."
call :say
set "RC=1"
goto :reopen
:locked
set "MSG=ERROR: el destino sigue en uso; cerra Instant2 y reintenta. Destino: %DST%"
call :say
set "RC=1"
goto :reopen
:copyfail
set "MSG=ERROR: no se pudo copiar el archivo nuevo al destino: %DST%"
call :say
if exist "%DST%.bak" move /Y "%DST%.bak" "%DST%" >nul
set "RC=1"
goto :reopen

REM Escribe el mensaje en el log y, si la salida no esta ya redirigida al
REM log (la app la redirige), tambien en la consola.
:say
>>"%LOG%" echo %MSG%
if not defined INSTANT2_UPDATE_LOG_REDIRECTED echo %MSG%
goto :eof

REM Anota el codigo de salida para que la app lo lea en el arranque
REM siguiente (mismo mecanismo que installer-result.json de Inno). El log
REM va con barras "/" porque JSON no acepta "\".
REM El "ts" (epoch, en segundos) es la fecha de la nota: sin ella la nota
REM no vence nunca y el error se repite en cada arranque, dias despues.
REM Si PowerShell no responde, la nota se escribe sin "ts" y la app usa la
REM fecha de modificacion del archivo.
:result
set "RC=%~1"
set "TS="
for /f "delims=" %%T in ('powershell -NoProfile -Command "[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()"') do set "TS=%%T"
set "TSOK="
echo %TS%|findstr /R /C:"^[0-9][0-9]*$" >nul 2>&1 && set "TSOK=1"
if defined TSOK (
  >"%RESULT%" echo {"returncode":%RC%,"log":"%LOG:\=/%","ts":%TS%}
) else (
  >"%RESULT%" echo {"returncode":%RC%,"log":"%LOG:\=/%"}
)
goto :eof
