; Waffler unsigned installer for early testers
; Build with: iscc installer\windows\Waffler.iss

#define MyAppName "Waffler"
#define MyAppVersion "2.1.19"
#define MyAppPublisher "Waffler"
#define MyAppURL "https://wafflerai.com"
#define MyAppExeName "Waffler.exe"

[Setup]
AppId={{2C9F4E1A-8B3D-4F7C-A2E5-6D0B1C3F8E9A}
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
; v3.14.73 — when updating over a running Waffler, force-close every running
; instance (main + overlay subprocess) via Restart Manager before copying
; files, so locked _internal\ DLLs don't make the install silently skip them.
; The in-app updater also force-kills Waffler.exe first; this covers manual
; re-runs of the installer too. RestartApplications=no because the updater
; (or the user) owns the relaunch.
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
AppMutex=Waffler-Single-Instance-Mutex-v1

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
