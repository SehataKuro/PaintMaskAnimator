; Inno Setup script for PaintMaskAnimator.
; Compile: iscc /DAppVersion=<version from pyproject.toml> packaging\installer.iss
; The release workflow always supplies AppVersion.
; Expects the PyInstaller one-folder build at dist\PaintMaskAnimator\.

#ifndef AppVersion
  #error AppVersion must be supplied from pyproject.toml with /DAppVersion=x.y.z
#endif

#define AppName "PaintMaskAnimator"
#define AppPublisher "SehataKuro"
#define AppExeName "PaintMaskAnimator.exe"
#define ProgId "PaintMaskAnimator.Project"
; Must match CLSID_PMAN_THUMBNAIL in packaging\windows\pman_thumbnail\src\lib.rs.
#define ThumbnailClsid "{{E65E7337-4EA8-4AED-B618-247D06EAAC47}"
; IThumbnailProvider's shell extension key.
#define ThumbnailShellEx "{{e357fccd-a995-4576-b01f-234630154e96}"

[Setup]
AppId={{7C9F1E2A-2B41-4E7C-9E2A-PMA0ANIMATOR01}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist_installer
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install by default (no admin rights required).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; In-place update support: if the app is running (e.g. the in-app updater
; launched this installer), close it before replacing files, then relaunch.
CloseApplications=yes
CloseApplicationsFilter={#AppExeName}
RestartApplications=yes
; Tell Explorer that .pman now has an app and thumbnails.
ChangesAssociations=yes

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PaintMaskAnimator\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Explorer's thumbnail host may still hold the old DLL; replace it on restart then.
Source: "windows\pman_thumbnail\target\release\pman_thumbnail.dll"; DestDir: "{app}"; Flags: ignoreversion restartreplace uninsrestartdelete

[Registry]
; Double-click a .pman to open it. HKA = per-user or all users, matching the install mode.
Root: HKA; Subkey: "Software\Classes\.pman"; ValueType: string; ValueName: ""; ValueData: "{#ProgId}"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.pman\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "PaintMaskAnimator Project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""
; Show the thumbnail saved inside the project as the file's icon.
Root: HKA; Subkey: "Software\Classes\.pman\ShellEx\{#ThumbnailShellEx}"; ValueType: string; ValueName: ""; ValueData: "{#ThumbnailClsid}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\CLSID\{#ThumbnailClsid}"; ValueType: string; ValueName: ""; ValueData: "PaintMaskAnimator Thumbnail"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\CLSID\{#ThumbnailClsid}\InprocServer32"; ValueType: string; ValueName: ""; ValueData: "{app}\pman_thumbnail.dll"
Root: HKA; Subkey: "Software\Classes\CLSID\{#ThumbnailClsid}\InprocServer32"; ValueType: string; ValueName: "ThreadingModel"; ValueData: "Apartment"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
