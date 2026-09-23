; Inno Setup script for the DaRe desktop app (Windows).
;
; Build (after `pyinstaller dare.spec --noconfirm`):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" dare.iss
; Output: dist/installer/DaRe-Setup-0.1.0.exe

#define AppName "DaRe - Data Restructurer"
#define AppExe "DaRe.exe"
#define AppVersion "0.1.0"
#define AppPublisher "Vikrant Mohan"
#define AppExeName "DaRe"
#define BundleDir "dist\dare"

[Setup]
AppId={{CD406DF1-3523-44A5-98CD-D030B7207220}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; {autopf} needs admin rights; the uninstaller entry shows up in Apps settings
PrivilegesRequired=admin
OutputDir=dist\installer
OutputBaseFilename=DaRe-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole PyInstaller onedir bundle (exe + _internal\), recursing subdirs
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppExeName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppExeName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppExeName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Runtime files the app may create next to the exe (a user .env is kept on purpose)
Type: filesandordirs; Name: "{app}\_internal\extractor_audit"
