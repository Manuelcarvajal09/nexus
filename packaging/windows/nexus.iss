; Inno Setup 6 script for the Nexus installer.
; build.ps1 passes /DAppVersion=<version> /DSourceDir=<PyInstaller dist\Nexus> /DOutputDir=<folder>.
;
; Install scope: the first page asks "Install for me only" (no administrator rights,
; %LOCALAPPDATA%\Programs\Nexus) or "Install for all users" (UAC, Program Files).
; User data is never inside the install folder: it lives in each user's %LOCALAPPDATA%\Nexus.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\pyinstaller\dist\Nexus"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist-installer"
#endif

#ifndef AppMutexName
  #define AppMutexName "NexusWindowsErwin"
#endif

#define AppName "Nexus"
#define AppExe "Nexus.exe"
#define AppPublisher "Manuel Carvajal"
#define AppURL "https://github.com/Manuelcarvajal09/nexus"

[Setup]
AppId={{7D0C8E8B-3B7A-4E2B-9C6E-5A1B9E0D4F21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} installer
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user by default; the scope dialog lets the user choose all users (UAC).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UsedUserAreasWarning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
SetupIconFile=..\..\src\nexus_linux\assets\icons\nexus.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
LicenseFile=..\..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=Nexus-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
; Close a running Nexus before replacing its files (upgrade/uninstall).
AppMutex={#AppMutexName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[CustomMessages]
english.LaunchNexus=Start Nexus
spanish.LaunchNexus=Abrir Nexus
english.DeleteData=Also delete your Nexus history, pinboards and settings?%n%nChoose No to keep them for a future installation.
spanish.DeleteData=¿Borrar también tu historial, pinboards y configuración de Nexus?%n%nElige No para conservarlos para una instalación futura.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "StoreM.Nexus"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchNexus}"; Flags: nowait postinstall skipifsilent

[Code]
function AutostartIsOurs(Value: String): Boolean;
begin
  Result := Pos(Lowercase(ExpandConstant('{app}\{#AppExe}')), Lowercase(Value)) > 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { Remove the "start with Windows" entry Nexus created for this user, if it points here. }
    if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Nexus', Value) and AutostartIsOurs(Value) then
      RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Nexus');
  end;
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
  begin
    if MsgBox(CustomMessage('DeleteData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{localappdata}\Nexus'), True, True, True);
  end;
end;
