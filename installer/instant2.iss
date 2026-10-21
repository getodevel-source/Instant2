; Instant2 Windows installer: per-user, no elevation, no model weights.
; The build pipeline passes AppVersion, SourceDir and IconFile.
; The installer ships the app and the llama.cpp runtime; the app downloads
; the voice components (ASR, TTS and the optional local LLM) itself.
; GitHub releases contain Instant2-Setup.exe and, if the payload ever needs
; it, every numbered .bin volume. Keep the .exe, all .bin files and their
; .sha256 sidecars together.
; Uninstall deliberately preserves %APPDATA%\instant2 and
; %LOCALAPPDATA%\instant2, including downloaded models and preferences.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Instant2"
#endif
#ifndef IconFile
  #define IconFile "..\dictado\build\instant2.ico"
#endif

#define AppName "Instant2"
#define AppExe "Instant2.exe"
#define AppPublisher "getodevel-source"
#define AppURL "https://github.com/getodevel-source/Instant2"

; LicenseFile solo si hay LICENSE en la raíz (el CI compila desde la raíz).
#if FileExists("..\\LICENSE") || FileExists("LICENSE")
#if FileExists("..\\LICENSE")
#define LicensePath "..\\LICENSE"
#else
#define LicensePath "LICENSE"
#endif
#endif

[Setup]
; AppId estable: las versiones nuevas actualizan esta instalación en el lugar.
AppId={{5F0CA570-852A-4BF3-8F53-E51260B4E347}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
AppCopyright=Copyright (c) 2026 {#AppPublisher} (MIT, ver LICENSE en el repo)
#ifdef LicensePath
LicenseFile={#LicensePath}
#endif
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
; Sin admin: todo vive bajo %LOCALAPPDATA% y el registro del usuario.
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Instant2-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/fast
SolidCompression=no
; GitHub limits each release asset to less than 2 GiB. Inno creates the setup
; executable and sequential .bin volumes; keep every volume below that limit.
DiskSpanning=yes
DiskSliceSize=1900000000
WizardStyle=modern
CloseApplications=force
RestartApplications=no
; La app es de 64 bits (PySide6/sherpa-onnx): en ARM64 corre emulada.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
VersionInfoVersion={#AppVersion}.0

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "autostart"; Description: "Iniciar Instant2 con Windows (dictado en segundo plano)"; GroupDescription: "Inicio automático:"; Flags: checkedonce
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; El instalador NO lleva pesos de modelo: la app descarga sus componentes
; de voz (ASR, TTS y el LLM local opcional) al directorio de datos del
; usuario, que sobrevive a las actualizaciones y a la desinstalacion.

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
; Mismo nombre y destino que el arranque administrado por la app (autostart.py):
; así la casilla del panel y esta tarea no se pisan entre sí.
Name: "{userstartup}\Instant2 Dictado.lnk"; Filename: "{app}\{#AppExe}"; Parameters: "run"; WorkingDir: "{userdocs}"; Tasks: autostart

[Run]
; La app se abre al terminar la instalacion, tambien en silencio: la
; actualizacion desde la app instala en silencio y, si no se abriera, el
; usuario se queda con la app cerrada y sin ver el resultado (la nota de
; verificacion se reporta en el arranque siguiente).
; En instalacion interactiva la casilla de la pagina final sigue mandando
; (el usuario puede desmarcarla). El ejecutable sin argumentos muestra el
; panel e inicia el daemon si voz y microfono estan disponibles.
Filename: "{app}\{#AppExe}"; Description: "Abrir Instant2 y activar el dictado en segundo plano"; Flags: nowait postinstall

[UninstallRun]
; El daemon no tiene ventana: el Restart Manager no puede cerrarlo, así que
; se frena con el propio binario antes de borrar los archivos (evita restos).
Filename: "{app}\{#AppExe}"; Parameters: "stop"; RunOnceId: "StopInstantDaemon"
; Carrera: si el panel/daemon estaba *arrancando* cuando corrió el stop, el
; proceso aparece después y lockea los archivos. Se fuerza el cierre de
; cualquier cosa que quede corriendo desde {app}.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -Command ""Get-Process -Name Instant2 -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}\*' }} | Stop-Process -Force"""; RunOnceId: "KillInstantProcesses"; Flags: runhidden
; The runtime is stopped before removing its DLLs.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\stop-bundled-runtime.ps1"" ""{app}\llm-runtime"""; RunOnceId: "StopBundledLlamaServer"; Flags: runhidden

[Code]
// El instalador no lleva pesos de modelo: la app descarga sus componentes de
// voz al directorio de datos del usuario. Lo unico que hay que hacer antes de
// copiar los archivos es liberar los binarios que tienen bloqueados los
// archivos de {app}:
//   - El daemon de Instant2 se frena con el propio ejecutable ("stop").
//   - El runtime de llama.cpp incluido se frena con el script que lo acompana.
//
// OJO: en el [Code] el ";" no es un comentario (en Pascal es separador de
// sentencia) y rompe el parseo con "'BEGIN' expected". Los comentarios de
// esta seccion van con "//"; los ";" de las secciones de datos si valen.

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  StopScript: String;
  PowerShell: String;
begin
  if FileExists(ExpandConstant('{app}\Instant2.exe')) then
    Exec(ExpandConstant('{app}\Instant2.exe'), 'stop',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
  StopScript := ExpandConstant('{app}\stop-bundled-runtime.ps1');
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  if FileExists(StopScript) then
    Exec(PowerShell,
      '-NoProfile -ExecutionPolicy Bypass -File "' + StopScript + '" "' +
      ExpandConstant('{app}\llm-runtime') + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

[UninstallDelete]
; La configuración y los modelos NO se tocan al desinstalar: viven en
; %APPDATA%\instant2 y %LOCALAPPDATA%\instant2 a propósito.
Type: dirifempty; Name: "{app}"
