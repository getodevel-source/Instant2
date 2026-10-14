@echo off
REM instant2-run: SIN args -> arranca el daemon OCULTO [sin ventana visible].
REM Con args -> passthrough en consola [ej. instant2-run.bat --help, run, check].
REM Sin duplicados: si ya hay instancia viva [PID file o tasklist], avisa y sale.
REM Modelos: %ROOT%\models [relativo a la raiz del repo]. Solo default, respeta INSTANT2_DATA previa.
setlocal
set "ROOT=%~dp0.."
if not defined INSTANT2_DATA set "INSTANT2_DATA=%ROOT%\models"
if not "%~1"=="" goto :foreground
set "PIDFILE=%APPDATA%\instant2\instant2.pid"
if exist "%PIDFILE%" goto :checkpid
goto :scan
:checkpid
set /p LEPID=<"%PIDFILE%"
if not defined LEPID goto :stale
tasklist /FI "PID eq %LEPID%" /FO TABLE /NH 2>nul | findstr /I "instant2.exe python.exe pythonw.exe" >nul
if errorlevel 1 goto :stale
powershell -NoProfile -Command "if ((Get-CimInstance Win32_Process -Filter \"ProcessId=%LEPID%\").CommandLine -like '*instant2*') { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 goto :stale
echo Ya hay instancia viva PID %LEPID%. No se lanza otra. Usa instant2-status.bat.
exit /b 0
:stale
del "%PIDFILE%" 2>nul
:scan
REM Solo el DAEMON cuenta (...\instant2... run...), no la GUI (setup/home):
REM antes cualquier Instant2.exe (incluida la ventana de ajustes) bloqueaba
REM el arranque del daemon (falso positivo).
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'Instant2.exe' -or $_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.CommandLine -like '*instant2*run*' }) { exit 1 } else { exit 0 }" >nul 2>nul
if errorlevel 1 goto :dup_exe
goto :launch
:dup_exe
echo Ya hay daemon vivo sin PID file. No se lanza otro.
exit /b 0
:launch
set "INSTANT2_PYTHON=%ROOT%\.venv\Scripts\pythonw.exe"
if exist "%INSTANT2_PYTHON%" goto :start_hidden
set "INSTANT2_PYTHON=%ROOT%\.venv\Scripts\python.exe"
if exist "%INSTANT2_PYTHON%" goto :start_hidden
set "INSTANT2_PYTHON="
for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined INSTANT2_PYTHON set "INSTANT2_PYTHON=%%P"
if not defined INSTANT2_PYTHON (
  for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined INSTANT2_PYTHON set "INSTANT2_PYTHON=%%P"
)
::start_hidden
set "INSTANT2_WORKDIR=%ROOT%"
powershell -NoProfile -Command "try { Start-Process -FilePath $env:INSTANT2_PYTHON -ArgumentList '-m instant2_app run' -WindowStyle Hidden -WorkingDirectory $env:INSTANT2_WORKDIR; exit 0 } catch { Write-Error $_; exit 1 }"
echo Daemon lanzado oculto sin ventana. Verifica con instant2-status.bat; frena con instant2-stop.bat.
exit /b 0
:fail
echo ERROR: no se pudo lanzar el daemon oculto.
exit /b 1
:foreground
if exist "%ROOT%\.venv\Scripts\python.exe" goto :fg_venv
where instant2.exe >nul 2>nul
if %ERRORLEVEL%==0 goto :fg_exe
goto :fg_python
:fg_venv
"%ROOT%\.venv\Scripts\python.exe" -m instant2_app %*
exit /b %ERRORLEVEL%
:fg_exe
instant2.exe %*
exit /b %ERRORLEVEL%
:fg_python
python -m instant2_app %*
exit /b %ERRORLEVEL%
