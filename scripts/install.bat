@echo off
REM Instala Instant2 en un entorno virtual del repositorio y abre el asistente.
setlocal
set "ROOT=%~dp0.."
if not defined INSTANT2_DATA set "INSTANT2_DATA=%ROOT%\models"

where python >nul 2>nul
if errorlevel 1 (
  echo [Instant2] Falta Python 3.12 o posterior en PATH.
  echo Instala Python desde https://www.python.org/downloads/ y reintenta.
  exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
  echo [Instant2] Se requiere Python 3.12 o posterior.
  exit /b 1
)

set "VENV_PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
  echo [Instant2] Creando entorno virtual...
  python -m venv "%ROOT%\.venv"
  if errorlevel 1 (
    echo [Instant2] No se pudo crear .venv.
    exit /b 1
  )
)

echo [Instant2] Instalando Instant2 y sus dependencias...
call "%VENV_PYTHON%" -m pip install "%ROOT%\dictado"
if errorlevel 1 (
  echo [Instant2] Fallo la instalacion del paquete. Revisa tu conexion y reintenta.
  exit /b 1
)

set "SETUP_ARGS="
if defined INSTANT2_UNATTENDED set "SETUP_ARGS=--yes"
if /i "%INSTANT2_AUTOSTART%"=="1" set "SETUP_ARGS=%SETUP_ARGS% --autostart"

echo [Instant2] Configurando modelos, microfono y tecla...
pushd "%ROOT%"
if defined INSTANT2_UNATTENDED (
  call "%VENV_PYTHON%" -m instant2_app setup %SETUP_ARGS%
) else (
  if exist "%ROOT%\.venv\Scripts\pythonw.exe" (
    call "%ROOT%\.venv\Scripts\pythonw.exe" -m instant2_app setup %SETUP_ARGS%
  ) else (
    call "%VENV_PYTHON%" -m instant2_app setup %SETUP_ARGS%
  )
)
set "SETUP_RC=%ERRORLEVEL%"
popd
if not "%SETUP_RC%"=="0" (
  echo [Instant2] La configuracion no termino correctamente. Reintenta con instant2-setup.bat.
  exit /b %SETUP_RC%
)

echo.
echo [Instant2] Instalacion completa. Arranca con instant2-run.bat.
