@echo off
REM Diagnostico rapido: NO levanta el daemon, solo lo inspecciona.
REM Modelos: %ROOT%\models [relativo a la raiz del repo]. Solo default, respeta INSTANT2_DATA previa.
setlocal
set "ROOT=%~dp0.."
if not defined INSTANT2_DATA set "INSTANT2_DATA=%ROOT%\models"
echo == proceso ==
set "PIDFILE=%APPDATA%\instant2\instant2.pid"
if not exist "%PIDFILE%" goto :nopid
set /p LEPID=<"%PIDFILE%"
if not defined LEPID goto :emptypid
tasklist /FI "PID eq %LEPID%" /FO TABLE /NH 2>nul | findstr /I "instant2.exe python.exe pythonw.exe" >nul
if errorlevel 1 goto :stalepid
powershell -NoProfile -Command "if ((Get-CimInstance Win32_Process -Filter \"ProcessId=%LEPID%\").CommandLine -like '*instant2*') { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 goto :stalepid
echo vivo: SI PID %LEPID%
goto :models
:stalepid
echo vivo: NO PID %LEPID% stale; el proceso murio.
del "%PIDFILE%" 2>nul
echo PID file stale limpio.
goto :models
:emptypid
echo PID file vacio; se limpia.
del "%PIDFILE%" 2>nul
goto :scanlegacy
:nopid
goto :scanlegacy
:scanlegacy
tasklist /FI "IMAGENAME eq instant2.exe" 2>nul | findstr /I "instant2.exe" >nul
if not errorlevel 1 goto :legacy_exe
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*instant2*' }) { exit 1 } else { exit 0 }" >nul 2>nul
if errorlevel 1 goto :legacy_pyw
echo vivo: NO
goto :models
:legacy_exe
echo vivo: SI instant2.exe sin PID file, lanzado con version vieja.
goto :models
:legacy_pyw
echo vivo: SI pythonw oculto sin PID file, lanzado con version vieja.
goto :models
:models
echo == modelos ==
echo INSTANT2_DATA=%INSTANT2_DATA%
if defined INSTANT2_DATA goto :hasdata
echo var vacia: el exe usaria el default %LOCALAPPDATA%\instant2\models
if exist "%LOCALAPPDATA%\instant2\models\parakeet-v3-int8\encoder.int8.onnx" goto :defok
echo default %LOCALAPPDATA%\instant2\models: VACIO - fija INSTANT2_DATA o usa instant2-run.bat
goto :log
:hasdata
if exist "%INSTANT2_DATA%\parakeet-v3-int8\encoder.int8.onnx" goto :dataok
echo dir INSTANT2_DATA: VACIO o sin modelos, revisa la ruta.
goto :log
:dataok
echo dir INSTANT2_DATA: OK encoder.int8.onnx presente.
goto :log
:defok
echo default %LOCALAPPDATA%\instant2\models: OK
:log
echo.
echo == log: ultimas 15 lineas de %%APPDATA%%\instant2\instant2.log ==
powershell -NoProfile -Command "Get-Content \"$env:APPDATA\instant2\instant2.log\" -Tail 15"
echo.
echo == config: %%APPDATA%%\instant2\config.json ==
type "%APPDATA%\instant2\config.json"
