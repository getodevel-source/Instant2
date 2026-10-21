; Instant2 Windows installer: per-user, no elevation.
; The build pipeline passes AppVersion, SourceDir and IconFile. The full
; build also passes ModelDir: the voice models (ASR + TTS, ~951 MB) travel
; inside the setup so a fresh install dictates and reads offline from the
; first minute. The optional 3 GB local LLM is never bundled: the app
; downloads it on demand from Settings.
; Every build is a single file (DiskSpanning=no). A split installer makes
; Inno ask for "the disk" with Instant2-Setup-1.bin, and whoever downloaded
; only the .exe cannot install.
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
; Sin OutputBase, el instalador completo. El release tambien publica
; Instant2-Update.exe (la app sola, sin ModelDir) para que el actualizador
; in-app no vuelva a bajar los modelos que ya estan en el disco.
#ifndef OutputBase
  #define OutputBase "Instant2-Setup"
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
OutputBaseFilename={#OutputBase}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/fast
SolidCompression=no
; UN SOLO ARCHIVO, siempre. Con DiskSpanning=yes Inno parte el instalador en
; Setup.exe (el programa) + Setup-1.bin (los datos): quien baja solo el .exe
; se queda con un instalador que pide "el disco" con el .bin y no instala
; nada. El payload completo (app + voz) pesa menos de 2 GB, asi que entra en
; un solo archivo.
DiskSpanning=no
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
#ifdef ModelDir
; Los pesos de voz (reconocimiento + lectura en voz alta) van al directorio de
; datos del usuario, que sobrevive a las actualizaciones y a la desinstalacion.
; El LLM de 3 GB no se incluye nunca. `ShouldInstallModel` saltea lo que ya
; esta en el destino (o en el directorio legacy, que la app mueve al arrancar):
; asi una actualizacion no recopia 951 MB de modelos.
Source: "{#ModelDir}\*"; DestDir: "{localappdata}\instant2\models"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall; Check: ShouldInstallModel
#endif

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
// El instalador copia los pesos de voz al directorio de datos del usuario.
// Lo unico que hay que hacer antes de copiar es liberar los binarios que
// tienen bloqueados los archivos de {app}:
//   - El daemon de Instant2 se frena con el propio ejecutable ("stop").
//   - El runtime de llama.cpp incluido se frena con el script que lo acompana.
//
// OJO: en el [Code] el ";" no es un comentario (en Pascal es separador de
// sentencia) y rompe el parseo con "'BEGIN' expected". Los comentarios de
// esta seccion van con "//"; los ";" de las secciones de datos si valen.

#ifdef ModelDir
const
  ModelsDir = '{localappdata}\instant2\models';
  LegacyModelsDir = '{localappdata}\instant\models';

{ ¿Hay un archivo con ese nombre en el directorio de modelos o en alguno de
  sus subdirectorios? }
function FindModelFile(Directory, Name: String): Boolean;
var
  Found: TFindRec;
  SubDir: String;
begin
  Result := False;
  if Name = '' then
    Exit;
  if FileExists(Directory + '\' + Name) then
  begin
    Result := True;
    Exit;
  end;
  if not DirExists(Directory) then
    Exit;
  if FindFirst(Directory + '\*', Found) then
  begin
    try
      repeat
        if (Found.Name <> '.') and (Found.Name <> '..') then
        begin
          SubDir := Directory + '\' + Found.Name;
          if DirExists(SubDir) then
          begin
            if FindModelFile(SubDir, Name) then
            begin
              Result := True;
              Exit;
            end;
          end;
        end;
      until not FindNext(Found);
    finally
      FindClose(Found);
    end;
  end;
end;

// Check de la entrada de modelos: devuelve True para INSTALAR el archivo, asi
// que "ya esta" se devuelve invertido.
//
// Se saltea el archivo que ya esta en el directorio de datos actual O en el
// legacy ("%LOCALAPPDATA%\instant\models"): la app mueve los del slug viejo al
// arrancar (rename, sin recopiar) y copiarlos aca dejaria 951 MB duplicados.
// El instalador no borra ni mueve nada del directorio legacy: de eso se ocupa
// la app, que verifica el destino antes de borrar.
//
// La firma sin parametros es la que acepta Inno Setup 6: la de (TInstaller,
// TWizardForm, String) no compila, porque TInstaller no existe en este dialecto.
function ShouldInstallModel(): Boolean;
begin
  Result := not (FindModelFile(ExpandConstant(ModelsDir),
    ExtractFileName(CurrentFileName)) or
    FindModelFile(ExpandConstant(LegacyModelsDir),
    ExtractFileName(CurrentFileName)));
end;
#endif

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
