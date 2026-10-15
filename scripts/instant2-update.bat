@echo off
REM Aplica una version descargada y verificada, desde el repo o el portable.
REM Uso: instant2-update.bat <nuevo-Instant2.exe> <sha256> [destino-Instant2.exe]
REM Re-verifica el hash, frena Instant2, respalda el exe, reemplaza, comprueba
REM que el daemon nuevo levante (si no, restaura) y abre el panel.
REM En exito borra %DST%.bak (no se acumulan respaldos); en rollback se
REM conserva para vuelta atras manual.
setlocal
set "ROOT=%~dp0.."
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

echo [1/6] Verificando SHA256...
for /f "skip=1 tokens=1" %%H in ('certutil -hashfile "%~1" SHA256 2^>nul') do (
  if /I not "%%H"=="%~2" goto :hashfail
  goto :hashok
)
goto :hashfail
:hashok
echo Hash OK.

echo [2/6] Frenando daemon...
if exist "%~dp0instant2-stop.bat" (
  call "%~dp0instant2-stop.bat" >nul 2>&1
) else (
  "%DST%" stop >nul 2>&1
)

echo [3/6] Esperando que se cierren las ventanas Instant2...
set "INSTANT2_TARGET=%DST%"
powershell -NoProfile -Command "$target=[IO.Path]::GetFullPath($env:INSTANT2_TARGET); $until=(Get-Date).AddSeconds(12); do { $running=Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $target }; if (-not $running) { exit 0 }; Start-Sleep -Milliseconds 200 } while ((Get-Date) -lt $until); exit 1" >nul 2>&1
if errorlevel 1 goto :locked

echo [4/6] Respaldando y reemplazando...
del "%DST%.bak" 2>nul
if exist "%DST%" move /Y "%DST%" "%DST%.bak" >nul
if exist "%DST%" goto :locked
copy /Y "%~1" "%DST%" >nul
if errorlevel 1 goto :copyfail

echo [5/6] Arrancando daemon nuevo...
REM El reinicio sobrevive al proceso onefile anterior; necesita extraer sus
REM propios recursos porque el directorio _MEI del proceso anterior se borra.
set "PYINSTALLER_RESET_ENVIRONMENT=1"
start "" "%DST%" run
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
echo ADVERTENCIA: el nuevo exe no levanto; restaurando el anterior...
del "%DST%" 2>nul
if exist "%DST%.bak" move /Y "%DST%.bak" "%DST%" >nul
start "" "%DST%" run
echo Se restauro la version anterior. Revisa el log en %APPDATA%\instant2.
goto :reopen
:healthy
echo Daemon nuevo en marcha.
REM Exito verificado: el respaldo ya cumplio (rollback disponible hasta aca).
del "%DST%.bak" 2>nul

:reopen
echo [6/6] Reabriendo ventana...
start "" "%DST%" setup
echo Listo: Instant2 actualizado.
timeout /t 2 /nobreak >nul
exit /b 0

:usage
echo Uso: instant2-update.bat ^<exe descargado^> ^<sha256 del sidecar^>
exit /b 1
:missing
echo ERROR: no existe "%~1".
exit /b 1
:selfcopy
echo ERROR: el origen es el propio destino; nada que instalar.
exit /b 1
:hashfail
echo ERROR: SHA256 no coincide; no se instala nada.
exit /b 1
:locked
echo ERROR: "%DST%" sigue en uso; cerra Instant2 y reintenta.
exit /b 1
:copyfail
echo ERROR: no se pudo copiar "%~1" a "%DST%".
if exist "%DST%.bak" move /Y "%DST%.bak" "%DST%" >nul
exit /b 1
