@echo off
REM Abre el asistente de configuración de Instant2.
setlocal
set "ROOT=%~dp0.."
if not defined INSTANT2_DATA set "INSTANT2_DATA=%ROOT%\models"
pushd "%ROOT%"

if not "%~1"=="" goto :console
if exist "%ROOT%\dist\Instant2.exe" goto :launch_gui_exe
if exist "%ROOT%\.venv\Scripts\pythonw.exe" goto :launch_venv_gui
goto :path_pythonw

:launch_gui_exe
start "" "%ROOT%\dist\Instant2.exe" setup
goto :done

:launch_venv_gui
start "" "%ROOT%\.venv\Scripts\pythonw.exe" -m instant2_app setup
goto :done

:path_pythonw
set "INSTANT2_PYTHONW="
for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined INSTANT2_PYTHONW set "INSTANT2_PYTHONW=%%P"
if defined INSTANT2_PYTHONW goto :system_pythonw
echo [Instant2] No encuentro Instant2.exe ni pythonw.exe para abrir la interfaz.
set "SETUP_RC=1"
goto :finish

:system_pythonw
start "" "%INSTANT2_PYTHONW%" -m instant2_app setup
goto :done

:console
if exist "%ROOT%\.venv\Scripts\python.exe" (
  call "%ROOT%\.venv\Scripts\python.exe" -m instant2_app setup %*
  goto :done
)

where instant2.exe >nul 2>nul
if not errorlevel 1 (
  call instant2.exe setup %*
  goto :done
)
call python -m instant2_app setup %*

:done
set "SETUP_RC=%ERRORLEVEL%"
:finish
popd
exit /b %SETUP_RC%
