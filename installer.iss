; VoiceFlow — Inno Setup Installer Script
; Produces: VoiceFlowSetup.exe
; Run with: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss

[Setup]
AppName=VoiceFlow
AppVersion=1.0.0
AppPublisher=VoiceFlow
DefaultDirName={autopf}\VoiceFlow
DefaultGroupName=VoiceFlow
OutputDir=dist-installer
OutputBaseFilename=VoiceFlowSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName=VoiceFlow
; Uncomment and set path if you have an icon:
; SetupIconFile=icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checked

[Files]
; Bundle the entire dist/VoiceFlow folder
Source: "dist\VoiceFlow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\VoiceFlow"; Filename: "{app}\VoiceFlow.exe"
Name: "{group}\Uninstall VoiceFlow"; Filename: "{uninstallexe}"
Name: "{autodesktop}\VoiceFlow"; Filename: "{app}\VoiceFlow.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VoiceFlow.exe"; Description: "Launch VoiceFlow"; Flags: nowait postinstall skipifsilent
