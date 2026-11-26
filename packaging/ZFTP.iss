#define AppName "Z FTP"
#define AppVersion "2.1.12"
#define AppUserModelID "Zyvelia.ZFTP"
[Setup]
AppId={{77CD47FA-45F5-467F-A93B-676F2DC0A635}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Z FTP
DefaultDirName={localappdata}\Programs\ZFTP
DefaultGroupName=Z FTP
Uninstallable=not IsPortableInstall
PrivilegesRequired=lowest
OutputDir=..\installer_output
OutputBaseFilename=ZFTP_Setup_v2.1.12
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
Filename: "{app}\zftp-portable.ini"; Section: "ZFTP"; Key: "Portable"; String: "1"; Check: IsPortableInstall
[Registry]
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; ValueType: string; ValueName: "DisplayName"; ValueData: "{#AppName}"; Flags: uninsdeletevalue uninsdeletekeyifempty; Check: not IsPortableInstall
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; ValueType: string; ValueName: "IconUri"; ValueData: "{app}\_internal\assets\zftp_icon.png"; Flags: uninsdeletevalue uninsdeletekeyifempty; Check: not IsPortableInstall
[UninstallDelete]
Type: files; Name: "{app}\zftp-version.ini"
[Icons]
Name: "{autoprograms}\Z FTP - Check for updates"; Filename: "{cmd}"; Parameters: "/D /C """"{app}\Update-ZFTP.bat"""""; WorkingDir: "{app}"; IconFilename: "{app}\ZFTP.exe"; Check: not IsPortableInstall
Name: "{autoprograms}\Z FTP"; Filename: "{app}\ZFTP.exe"; IconFilename: "{app}\ZFTP.exe"; AppUserModelID: "{#AppUserModelID}"; Check: not IsPortableInstall
Name: "{autodesktop}\Z FTP"; Filename: "{app}\ZFTP.exe"; Tasks: desktopicon; AppUserModelID: "{#AppUserModelID}"; Check: not IsPortableInstall
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
[Run]
Filename: "{app}\ZFTP.exe"; Description: "Launch Z FTP"; Flags: nowait postinstall skipifsilent


[Code]
var
  InstallModePage: TInputOptionWizardPage;
  PreviousPortable: Boolean;

function PortableParameter: Boolean;
begin
  Result := ExpandConstant('{param:PORTABLE|0}') = '1';
end;

function IsPortableInstall: Boolean;
begin
  Result := PortableParameter;
  if InstallModePage <> nil then
    Result := Result or (InstallModePage.SelectedValueIndex = 1);
end;

procedure InitializeWizard;
begin
  InstallModePage := CreateInputOptionPage(wpWelcome, 'Installation type',
    'Choose a standard or portable installation',
    'Portable stores settings in a data folder beside ZFTP.exe.', True, False);
  InstallModePage.Add('Standard installation');
  InstallModePage.Add('Portable folder');
  if PortableParameter then
    InstallModePage.SelectedValueIndex := 1
  else
    InstallModePage.SelectedValueIndex := 0;
  PreviousPortable := False;
  if PortableParameter and (ExpandConstant('{param:DIR|}') = '') then
    WizardForm.DirEdit.Text := ExpandConstant('{userdocs}\Z FTP Portable');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = InstallModePage.ID) and PortableParameter;
  if (PageID = wpSelectTasks) and IsPortableInstall then
    Result := True;
end;

function NextButtonClick(PageID: Integer): Boolean;
begin
  Result := True;
  if (PageID = InstallModePage.ID) and (PreviousPortable <> IsPortableInstall)
    and (ExpandConstant('{param:DIR|}') = '') then
  begin
    if IsPortableInstall then
      WizardForm.DirEdit.Text := ExpandConstant('{userdocs}\Z FTP Portable')
    else
      WizardForm.DirEdit.Text := ExpandConstant('{localappdata}\Programs\ZFTP');
    PreviousPortable := IsPortableInstall;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Target: String;
begin
  Result := '';
  Target := AddBackslash(WizardDirValue);
  if IsPortableInstall and FileExists(Target + 'unins000.exe') then
    Result := 'Choose a separate folder for the portable installation.'
  else if (not IsPortableInstall) and FileExists(Target + 'zftp-portable.ini') then
    Result := 'This folder contains a portable installation. Select Portable folder to update it.';
end;
