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

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PaintMaskAnimator\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
