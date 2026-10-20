; Instant2 Windows installer: per-user, no elevation, offline models included.
; The build pipeline passes AppVersion, SourceDir, ModelDir and IconFile.
; GitHub releases contain Instant2-Setup.exe and every numbered .bin volume.
; Keep the .exe, all .bin files and their .sha256 sidecars together.
; Uninstall deliberately preserves %APPDATA%\instant2 and
; %LOCALAPPDATA%\instant2, including installed models and preferences.
; The offline model bundle is 4 GB: the models the user already has are
; reused (see [Code]) instead of being copied again.

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
; Los modelos que el usuario ya tiene NO se vuelven a copiar: el Check
; saltea cada archivo que ya esta en el destino (y PrepareToInstall deja
; ahi los del slug legacy "instant", moviendolos, no copiandolos).
Source: "{#ModelDir}\*"; DestDir: "{localappdata}\instant2\models"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall; Check: ShouldInstallModel

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
// El bundle de modelos son 4 GB. Los que el usuario ya tiene no se vuelven
// a copiar:
//   - PrepareToInstall mueve los del directorio legacy (el slug viejo,
//     "%LOCALAPPDATA%\instant\models") al de Instant2. Es un rename: no se
//     copia ni un byte y la copia vieja no queda huerfana.
//   - El Check de la entrada de modelos saltea cada archivo que ya esta en
//     el destino, asi que una copia incompleta se completa sin recopiarla.
//
// OJO: en el [Code] el ";" no es un comentario (en Pascal es separador de
// sentencia) y rompe el parseo con "'BEGIN' expected". Los comentarios de
// esta seccion van con "//"; los ";" de las secciones de datos si valen.

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

// Check de la entrada de modelos: el archivo NO se copia si ya esta en el
// directorio de datos (los del bundle pesan hasta 3 GB). Setup llama a esta
// funcion una vez por archivo del bundle (no por entrada) y dice cual es con
// CurrentFileName.
//
// OJO con la semantica: Check devuelve True para INSTALAR el archivo. Asi
// que "ya esta instalado" se tiene que devolver invertido. La firma sin
// parametros es la que acepta Inno Setup 6: la de (TInstaller, TWizardForm,
// String) no compila, porque TInstaller no existe en ese dialecto.
function ShouldInstallModel(): Boolean;
begin
  Result := not FindModelFile(ExpandConstant(ModelsDir),
    ExtractFileName(CurrentFileName));
end;

{ Reutiliza los modelos del directorio legacy. Si el directorio nuevo no
  existe, se mueve el legacy entero (rename). Si los dos existen, se mueve
  lo que falte y se borra el duplicado (la copia vieja no puede quedar
  huerfana: el modelo del slug "instant" es el mismo que el de
  "instant2" y la app solo acepta los pesos fijados por hash). }
function ReuseLegacyModels(): Boolean;
var
  Legacy, Current, LegacyItem, CurrentItem, Name: String;
  Found: TFindRec;
begin
  Result := True;
  Legacy := ExpandConstant(LegacyModelsDir);
  Current := ExpandConstant(ModelsDir);
  if not DirExists(Legacy) then
    Exit;
  if not DirExists(Current) then
  begin
    ForceDirectories(ExpandConstant('{localappdata}\instant2'));
    if RenameFile(Legacy, Current) then
      Log('Modelos: se movio ' + Legacy + ' a ' + Current +
        ' (no se copio nada).')
    else
      Log('Modelos: no se pudo mover ' + Legacy + ' a ' + Current + '.');
    Exit;
  end;
  if FindFirst(Legacy + '\*', Found) then
  begin
    try
      repeat
        Name := Found.Name;
        if (Name <> '.') and (Name <> '..') then
        begin
          LegacyItem := Legacy + '\' + Name;
          CurrentItem := Current + '\' + Name;
          if DirExists(CurrentItem) or FileExists(CurrentItem) then
          begin
            DelTree(LegacyItem, True, True, True);
            Log('Modelos: duplicado borrado ' + LegacyItem +
              ' (ya estaba en ' + CurrentItem + ').');
          end
          else if RenameFile(LegacyItem, CurrentItem) then
            Log('Modelos: reutilizado ' + LegacyItem + ' movido a ' +
              CurrentItem + '.')
          else
            Log('Modelos: no se pudo mover ' + LegacyItem + ' a ' +
              CurrentItem + '.');
        end;
      until not FindNext(Found);
    finally
      FindClose(Found);
    end;
  end;
end;

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
  ReuseLegacyModels();
  Result := '';
end;

[UninstallDelete]
; La configuración y los modelos NO se tocan al desinstalar: viven en
; %APPDATA%\instant2 y %LOCALAPPDATA%\instant2 a propósito.
Type: dirifempty; Name: "{app}"
