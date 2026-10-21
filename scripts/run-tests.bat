@echo off
rem Suite de tests de Instant2 aislada: no toca el estado real de la maquina.
rem
rem La app escribe en dos lugares del disco del usuario: la configuracion
rem (%APPDATA%\instant2\config.json) y los modelos (%LOCALAPPDATA%\instant2\models
rem y el directorio del producto anterior). Este script los apunta a un
rem directorio temporal, asi que correr los tests nunca puede romper la
rem maquina de quien los corre.
rem
rem Uso: scripts\run-tests.bat [argumentos para unittest]
setlocal
set "ROOT=%~dp0.."
pushd "%ROOT%\dictado"

rem El sitio de usuario de Python esta bajo %%APPDATA%%: si lo movemos sin
rem guardarlo, el interprete deja de encontrar numpy, onnxruntime, etc.
set "USERSITE="
for /f "delims=" %%i in ('python -m site --user-site') do set "USERSITE=%%i"
if not "%USERSITE%"=="" set "PYTHONPATH=%USERSITE%;%PYTHONPATH%"

set "ISOLATION=%TEMP%\instant2-tests"
if exist "%ISOLATION%" rmdir /s /q "%ISOLATION%"
mkdir "%ISOLATION%\appdata" 2>nul
mkdir "%ISOLATION%\localdata" 2>nul
mkdir "%ISOLATION%\models" 2>nul
set "APPDATA=%ISOLATION%\appdata"
set "LOCALAPPDATA=%ISOLATION%\localdata"
set "INSTANT2_DATA=%ISOLATION%\models"
set "INSTANT2_CONFIG_DIR=%ISOLATION%\appdata\instant2"

python -m unittest discover -s tests %*
set "RC=%ERRORLEVEL%"
popd
endlocal & exit /b %RC%
