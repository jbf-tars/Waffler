; Waffler unsigned installer for early testers
; Build with: iscc installer\windows\Waffler.iss

#define MyAppName "Waffler"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Waffler"
#define MyAppURL "https://waffler-app.com"
#define MyAppExeName "Waffler.exe"

[Setup]
AppId={{F35717B7-7C70-4AE9-A67F-51F1E4A8A912}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=
PrivilegesRequired=lowest
OutputDir=dist-installer
OutputBaseFilename=Waffler-Setup-{#MyAppVersion}
SetupIconFile=
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Resolve from this .iss location (installer\windows\) back to repo root
Source: "..\..\dist\Waffler\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
