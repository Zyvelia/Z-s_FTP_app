#define AppName "Z FTP"
#define AppVersion "2.1.6"
[Setup]
AppId={{77CD47FA-45F5-467F-A93B-676F2DC0A635}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Z FTP
DefaultDirName={localappdata}\Programs\ZFTP
DefaultGroupName=Z FTP
PrivilegesRequired=lowest
OutputDir=..\installer_output
OutputBaseFilename=ZFTP_Setup_v2.1.6
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\zftp_icon.ico
WizardImageFile=..\assets\installer_sidebar.bmp
WizardSmallImageFile=..\assets\installer_small.bmp
UninstallDisplayIcon={app}\ZFTP.exe
DisableProgramGroupPage=yes
[Files]
Source: "..\dist\ZFTP\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\Update-ZFTP.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Update-ZFTP.bat"; DestDir: "{app}"; Flags: ignoreversion
[INI]
Filename: "{app}\zftp-version.ini"; Section: "ZFTP"; Key: "Version"; String: "{#AppVersion}"
[UninstallDelete]
Type: files; Name: "{app}\zftp-version.ini"
[Icons]
Name: "{autoprograms}\Z FTP - Check for updates"; Filename: "{cmd}"; Parameters: "/D /C """"{app}\Update-ZFTP.bat"""""; WorkingDir: "{app}"; IconFilename: "{app}\ZFTP.exe"
Name: "{autoprograms}\Z FTP"; Filename: "{app}\ZFTP.exe"; IconFilename: "{app}\ZFTP.exe"
Name: "{autodesktop}\Z FTP"; Filename: "{app}\ZFTP.exe"; Tasks: desktopicon
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
[Run]
Filename: "{app}\ZFTP.exe"; Description: "Launch Z FTP"; Flags: nowait postinstall skipifsilent
