; Waffler — Inno Setup Installer Script
; Produces: WafflerSetup.exe
; Run with: ISCC.exe installer.iss

[Setup]
AppName=Waffler
AppVersion=1.0.0
AppPublisher=Waffler
DefaultDirName={autopf}\Waffler
DefaultGroupName=Waffler
OutputDir=dist-installer
OutputBaseFilename=WafflerSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName=Waffler
SetupIconFile=icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Bundle the entire dist/Waffler folder
Source: "dist\Waffler\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Waffler"; Filename: "{app}\Waffler.exe"; IconFilename: "{app}\Waffler.exe"
Name: "{group}\Uninstall Waffler"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Waffler"; Filename: "{app}\Waffler.exe"; IconFilename: "{app}\Waffler.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Waffler.exe"; Description: "Launch Waffler"; Flags: nowait postinstall skipifsilent
