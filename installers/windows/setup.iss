[Setup]
AppName=StyleAI
AppVersion={#AppVersion}
DefaultDirName={commonpf}\StyleAI
DefaultGroupName=StyleAI
OutputBaseFilename=StyleAI-windows-x64-{#AppVersion}
OutputDir=Output
Compression=lzma
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
SetupIconFile=plugin\StyleAI.lrdevplugin\icon.ico
SourceDir=..\..

[Files]
; Backend files
Source: "build\styleai-server\*"; DestDir: "{app}\backend"; Flags: ignoreversion recursesubdirs createallsubdirs

; Plugin files (Global location for Lightroom)
Source: "build\StyleAI.lrplugin\*"; DestDir: "{userappdata}\Adobe\Lightroom\Modules\StyleAI.lrplugin"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\StyleAI Backend"; Filename: "{app}\backend\styleai-server.cmd"; IconFilename: "{app}\backend\app\src\icon.ico"

[Registry]
; Run backend at system startup for current user
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "StyleAIBackend"; ValueData: """{app}\backend\styleai-server.cmd"""; Flags: uninsdeletevalue

[Run]
; Start the backend immediately after installation
Filename: "{app}\backend\styleai-server.cmd"; Description: "Start StyleAI Backend"; Flags: nowait postinstall skipifsilent runhidden

[UninstallRun]
; Stop existing backend process before uninstalling
Filename: "taskkill"; Parameters: "/F /IM python.exe /T /FI ""WINDOWTITLE eq styleai-server*"""; Flags: runhidden; RunOnceId: "StopBackend"
Filename: "taskkill"; Parameters: "/F /IM pythonw.exe /T /FI ""WINDOWTITLE eq styleai-server*"""; Flags: runhidden; RunOnceId: "StopBackendW"
Filename: "taskkill"; Parameters: "/F /IM cmd.exe /T /FI ""WINDOWTITLE eq styleai-server*"""; Flags: runhidden; RunOnceId: "StopBackendCmd"

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  // Try to stop the service/process before starting setup (for updates)
  Exec('taskkill', '/F /IM python.exe /T /FI "WINDOWTITLE eq styleai-server*"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill', '/F /IM pythonw.exe /T /FI "WINDOWTITLE eq styleai-server*"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill', '/F /IM cmd.exe /T /FI "WINDOWTITLE eq styleai-server*"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
