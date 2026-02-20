; Natter — Inno Setup Installer Script
; Produces: NatterSetup.exe
; Run with: ISCC.exe installer.iss

[Setup]
AppName=Natter
AppVersion=1.0.0
AppPublisher=Natter
DefaultDirName={autopf}\Natter
DefaultGroupName=Natter
OutputDir=dist-installer
OutputBaseFilename=NatterSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName=Natter
SetupIconFile=icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Bundle the entire dist/Natter folder
Source: "dist\Natter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Natter"; Filename: "{app}\Natter.exe"; IconFilename: "{app}\Natter.exe"
Name: "{group}\Uninstall Natter"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Natter"; Filename: "{app}\Natter.exe"; IconFilename: "{app}\Natter.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Natter.exe"; Description: "Launch Natter"; Flags: nowait postinstall skipifsilent
