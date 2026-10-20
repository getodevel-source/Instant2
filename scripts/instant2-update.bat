@echo off
REM Aplica una version descargada y verificada, desde el repo o el portable.
REM Uso: instant2-update.bat [nuevo-Instant2.exe] [sha256] [destino-Instant2.exe]
REM Re-verifica el hash, frena Instant2, respalda el exe, reemplaza, comprueba
REM que el daemon nuevo levante (si no, restaura) y abre el panel.
REM En exito borra %DST%.bak (no se acumulan respaldos); en rollback se
REM conserva para vuelta atras manual.
REM Toda la salida va a %APPDATA%\instant2\update.log. La app lanza este
REM helper sin terminal (CREATE_NO_WINDOW) y con la salida ya apuntada al
REM log (INSTANT2_UPDATE_LOG_REDIRECTED=1): ahi queda el paso a paso y el
REM error real si la actualizacion se revierte.
setlocal
set "LOGDIR=%APPDATA%\instant2"
set "LOG=%LOGDIR%\update.log"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "ROOT=%~dp0.."
set "RC=0"

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if not exist "%~1" goto :missing
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
start "" "%DST%" run 2>nul
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
set "MSG=[6/6] Reabriendo ventana..."
call :say
start "" "%DST%" setup 2>nul
REM Sin terminal (CREATE_NO_WINDOW) `timeout` no puede leer la entrada:
REM el ping espera los mismos segundos y no depende de la consola.
ping -n 3 127.0.0.1 >nul 2>nul
if "%RC%"=="0" (
  set "MSG=Listo: Instant2 actualizado. Log: %LOG%"
) else (
  set "MSG=ERROR: la actualizacion fallo. Log: %LOG%"
)
call :say
exit /b %RC%

:usage
set "MSG=Uso: instant2-update.bat [nuevo-Instant2.exe] [sha256] [destino-Instant2.exe]"
call :say
exit /b 1
:missing
set "MSG=ERROR: no existe el archivo a instalar: %~1"
call :say
exit /b 1
:selfcopy
set "MSG=ERROR: el origen es el propio destino; nada que instalar."
call :say
exit /b 1
:hashfail
set "MSG=ERROR: el SHA256 no coincide; no se instala nada."
call :say
exit /b 1
:locked
set "MSG=ERROR: el destino sigue en uso; cerra Instant2 y reintenta. Destino: %DST%"
call :say
exit /b 1
:copyfail
set "MSG=ERROR: no se pudo copiar el archivo nuevo al destino: %DST%"
call :say
if exist "%DST%.bak" move /Y "%DST%.bak" "%DST%" >nul
exit /b 1

REM Escribe el mensaje en el log y, si la salida no esta ya redirigida al
REM log (la app la redirige), tambien en la consola.
:say
>>"%LOG%" echo %MSG%
if not defined INSTANT2_UPDATE_LOG_REDIRECTED echo %MSG%
goto :eof
