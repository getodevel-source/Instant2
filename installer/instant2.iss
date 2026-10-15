; Instant2 Windows installer: per-user, no elevation, offline models included.
; The build pipeline passes AppVersion, SourceDir, ModelDir and IconFile.
; GitHub releases contain Instant2-Setup.exe and every numbered .bin volume.
; Keep the .exe, all .bin files and their .sha256 sidecars together.
; Uninstall deliberately preserves %APPDATA%\instant2 and
; %LOCALAPPDATA%\instant2, including installed models and preferences.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Instant2"
#endif
#ifndef ModelDir
  #define ModelDir "..\dist\Instant2-Models"
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
; Preloaded ASR, LLM and TTS models go in Instant2's existing per-user model
; directory and remain there after updates and uninstall.
Source: "{#ModelDir}\*"; DestDir: "{localappdata}\instant2\models"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
; Mismo nombre y destino que el arranque administrado por la app (autostart.py):
; así la casilla del panel y esta tarea no se pisan entre sí.
Name: "{userstartup}\Instant2 Dictado.lnk"; Filename: "{app}\{#AppExe}"; Parameters: "run"; WorkingDir: "{userdocs}"; Tasks: autostart

[Run]
; En /SILENT no se abre nada: el updater silencioso no puede robar el foco ni
; abrir ventanas al usuario (skipifsilent). En instalación interactiva sí se
; abre Instant2 tras una instalación normal. El ejecutable sin argumentos
; muestra el panel e inicia el daemon si voz y micrófono están disponibles.
; Las instalaciones silenciosas de actualización no abren ventanas.
Filename: "{app}\{#AppExe}"; Description: "Abrir Instant2 y activar el dictado en segundo plano"; Flags: nowait postinstall skipifsilent

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
