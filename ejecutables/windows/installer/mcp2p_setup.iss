; ==============================================================================
; MCP2P — Script Oficial de Instalación Inno Setup para Windows
; ==============================================================================

#define MyAppName "MCP2P — Plataforma de Colaboración Agéntica"
#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "NMullerSM"
#define MyAppURL "https://github.com/NMullerSM/Mcp2p"
#define MyAppExeName "Mcp2p.exe"

[Setup]
AppId={{B7C1A4E2-9F3D-4A6B-8E21-3C5D9A1F0B77}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\MCP2P
DefaultGroupName=MCP2P
DisableProgramGroupPage=no
OutputBaseFilename=Mcp2p-Setup-v{#MyAppVersion}
OutputDir=..\
SetupIconFile=..\..\..\mcp2p.ico
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\mcp2p.ico
CreateUninstallRegKey=yes
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Actualización desde la propia app (src/app/auto_updater.py): lanza este instalador con
; /SILENT /CLOSEAPPLICATIONS /NORESTART /RELAUNCH=1. CloseApplications cierra Mcp2p.exe si sigue
; abierto (Restart Manager) para poder reemplazar sus archivos; la reapertura la hace el [Run]
; de abajo (no /RESTARTAPPLICATIONS, que no reabre una app que ya salió por sí sola y duplicaría la instancia).
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\Mcp2p\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\..\mcp2p.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MCP2P"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar MCP2P"; Filename: "{uninstallexe}"; IconFilename: "{app}\mcp2p.ico"
Name: "{autodesktop}\MCP2P"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,MCP2P}"; Flags: nowait postinstall skipifsilent
; Actualización automática: la app se cerró para instalar, así que se reabre al terminar (solo con /RELAUNCH=1;
; runasoriginaluser evita que arranque elevada si el instalador pidió UAC).
Filename: "{app}\{#MyAppExeName}"; Flags: nowait runasoriginaluser; Check: ShouldRelaunch

[Code]
function ShouldRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
