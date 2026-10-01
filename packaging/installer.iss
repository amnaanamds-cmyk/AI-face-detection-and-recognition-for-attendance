; Inno Setup script: turns dist\FaceAttend into dist\FaceAttend-Setup.exe
; Built automatically by packaging/build_exe.py when Inno Setup 6 is installed.
#define AppName "FaceAttend"
#define AppVersion GetEnv("APP_VERSION") == "" ? "1.0.0" : GetEnv("APP_VERSION")

[Setup]
AppId={{6C1C4F0E-8A63-4B7F-9E52-0F6A1D7B2C11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; per-user install by default: no administrator rights needed (schools often lock these)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup
SetupIconFile=..\build\desktop\icon.ico
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent
