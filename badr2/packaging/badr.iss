; Inno Setup script for the self-contained BADR 2 installer.
; Built by .github/workflows/badr2-windows.yml:  iscc /DStage=<staging dir> /DOut=<output dir> badr.iss
;
; Staging layout:   BADR.exe          native launcher
;                   app\badr\         the application
;                   app\pydeps\       bundled Python packages (AI SDK, Earth Engine, serial, MQTT)
;                   qgis\             QGIS LTR runtime (OSGeo4W tree)

#ifndef Stage
  #define Stage "..\..\stage"
#endif
#ifndef Out
  #define Out "..\..\dist"
#endif
#ifndef AppVersion
  #define AppVersion "2.1.0"
#endif

[Setup]
AppId={{6F3B1C52-8E0A-4B1E-9C55-BADR20000001}
AppName=BADR
AppVersion={#AppVersion}
AppPublisher=BADR
DefaultDirName={autopf}\BADR
DefaultGroupName=BADR
DisableProgramGroupPage=yes
; per-user install: no administrator rights needed
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Out}
OutputBaseFilename=BADR-Setup
SetupIconFile=badr.ico
UninstallDisplayIcon={app}\BADR.exe
Compression=lzma2/fast
SolidCompression=no
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
WizardStyle=modern
LicenseFile={#Stage}\LICENSE.txt

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\BADR"; Filename: "{app}\BADR.exe"
Name: "{group}\BADR (training demo)"; Filename: "{app}\BADR.exe"; Parameters: "--demo"
Name: "{autodesktop}\BADR"; Filename: "{app}\BADR.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\BADR.exe"; Description: "Start BADR"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
