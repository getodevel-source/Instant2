@echo off
REM instant2-stop: frena SOLO la instancia instant2 por PID file, nunca procesos ajenos.
REM Limpia %APPDATA%\instant2\instant2.pid. Sale 0 con mensaje claro.
setlocal
set "PIDFILE=%APPDATA%\instant2\instant2.pid"
if not exist "%PIDFILE%" goto :fallback
set /p LEPID=<"%PIDFILE%"
if not defined LEPID goto :empty
tasklist /FI "PID eq %LEPID%" /FO TABLE /NH 2>nul | findstr /I "instant2.exe python.exe pythonw.exe" >nul
if errorlevel 1 goto :stale
powershell -NoProfile -Command "if ((Get-CimInstance Win32_Process -Filter \"ProcessId=%LEPID%\").CommandLine -like '*instant2*') { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 goto :recycled
taskkill /F /PID %LEPID% >nul 2>nul
if errorlevel 1 goto :fail
ping -n 6 127.0.0.1 >nul 2>nul
tasklist /FI "PID eq %LEPID%" /FO TABLE /NH 2>nul | findstr /I "instant2.exe python.exe pythonw.exe" >nul
if not errorlevel 1 goto :fail
del "%PIDFILE%" 2>nul
echo Daemon frenado PID %LEPID%.
exit /b 0
:recycled
del "%PIDFILE%" 2>nul
echo PID %LEPID% reciclado por el SO (ya no es Instant2); PID file limpio, nada que matar.
exit /b 0
:empty
del "%PIDFILE%" 2>nul
echo PID file vacio; se limpio. No habia instancia conocida.
exit /b 0
:stale
del "%PIDFILE%" 2>nul
echo No estaba corriendo PID %LEPID% stale, proceso muerto; PID file limpio.
exit /b 0
:fail
echo ERROR: no se pudo frenar el PID %LEPID%. Revisa permisos.
exit /b 1
:fallback
tasklist /FI "IMAGENAME eq instant2.exe" 2>nul | findstr /I "instant2.exe" >nul
if not errorlevel 1 goto :legacy_exe
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*instant2_app run*' }) { exit 1 } else { exit 0 }" >nul 2>nul
if errorlevel 1 goto :legacy_pyw
echo No estaba corriendo.
exit /b 0
:legacy_exe
echo Hay instant2.exe vivo pero sin PID file, lanzado con version vieja.
echo Corre: taskkill /IM instant2.exe solo si sabes que es tuyo.
exit /b 2
:legacy_pyw
echo Hay pythonw oculto con instant2 pero sin PID file, lanzado con version vieja.
echo Frenalo desde el Administrador de tareas, solo pythonw con linea instant2_app run.
exit /b 2
