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
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "firewall"; Description: "Allow phones on the school Wi-Fi to connect (adds a Windows Firewall rule; asks for administrator permission)"; GroupDescription: "Phones:"
Name: "autostart"; Description: "Start the attendance server automatically when Windows starts (recommended: phones and shortcuts always work)"; GroupDescription: "Background server:"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{group}\Quit {#AppName} server"; Filename: "{app}\{#AppName}.exe"; Parameters: "--quit"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Registry]
; faceattend://start - the "Start FaceAttend" button on the offline page
Root: HKCU; Subkey: "Software\Classes\faceattend"; ValueType: string; ValueName: ""; ValueData: "URL:FaceAttend"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\faceattend"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\faceattend\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppName}.exe"" --background ""%1"""
; start with Windows
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppName}.exe"" --background"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
; Windows Firewall: let phones on the Wi-Fi reach FaceAttend (the most common reason "phone cannot connect")
Filename: "{cmd}"; Parameters: "/c netsh advfirewall firewall delete rule name=""{#AppName}"" >nul & netsh advfirewall firewall add rule name=""{#AppName}"" dir=in action=allow program=""{app}\{#AppName}.exe"" enable=yes profile=any"; Flags: runhidden shellexec waituntilterminated skipifsilent; Verb: runas; Tasks: firewall; StatusMsg: "Allowing phones through Windows Firewall..."
Filename: "{app}\{#AppName}.exe"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c netsh advfirewall firewall delete rule name=""{#AppName}"""; Flags: runhidden shellexec; Verb: runas; RunOnceId: "Firewall"; Tasks: firewall
Filename: "{app}\{#AppName}.exe"; Parameters: "--quit"; Flags: runhidden; RunOnceId: "QuitServer"

[Code]
// Stop a running background server before files are replaced (updates).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  if FileExists(ExpandConstant('{app}\{#AppName}.exe')) then
    Exec(ExpandConstant('{app}\{#AppName}.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
