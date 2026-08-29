#define MyAppName "StatLine"
#define MyAppVersion "4.0.0rc1"
#define MyAppPublisher "StatLine"
#define MyAppExeName "runtime\python.exe"

[Setup]
AppId={{E091F35A-590A-4E72-9D63-5866AC71E590}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}

DefaultDirName={localappdata}\Programs\StatLine
DefaultGroupName=StatLine

PrivilegesRequired=lowest

OutputDir=dist
OutputBaseFilename=StatLineSetup

Compression=lzma2
SolidCompression=yes

WizardStyle=modern

ChangesAssociations=yes
ChangesEnvironment=yes

UninstallDisplayName=StatLine
UninstallDisplayIcon={app}\runtime\python.exe

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs


; ============================================================
; .statpack association
; ============================================================

[Registry]

Root: HKCU; \
    Subkey: "Software\Classes\.statpack"; \
    ValueType: string; \
    ValueName: ""; \
    ValueData: "StatLine.StatPack"; \
    Flags: uninsdeletevalue

Root: HKCU; \
    Subkey: "Software\Classes\.statpack"; \
    ValueType: string; \
    ValueName: "Content Type"; \
    ValueData: "application/vnd.statline.statpack"

Root: HKCU; \
    Subkey: "Software\Classes\StatLine.StatPack"; \
    ValueType: string; \
    ValueName: ""; \
    ValueData: "StatLine StatPack"; \
    Flags: uninsdeletekey

Root: HKCU; \
    Subkey: "Software\Classes\StatLine.StatPack"; \
    ValueType: string; \
    ValueName: "FriendlyTypeName"; \
    ValueData: "StatLine StatPack"

Root: HKCU; \
    Subkey: "Software\Classes\StatLine.StatPack\shell"; \
    ValueType: string; \
    ValueName: ""; \
    ValueData: "open"

Root: HKCU; \
    Subkey: "Software\Classes\StatLine.StatPack\shell\open"; \
    ValueType: string; \
    ValueName: ""; \
    ValueData: "Run with StatLine"

Root: HKCU; \
    Subkey: "Software\Classes\StatLine.StatPack\shell\open\command"; \
    ValueType: string; \
    ValueName: ""; \
    ValueData: """{app}\runtime\python.exe"" -m statline run ""%1"""


; ============================================================
; Start Menu
; ============================================================

[Icons]

Name: "{group}\StatLine"; \
    Filename: "{app}\runtime\python.exe"; \
    Parameters: "-m statline"

Name: "{group}\Uninstall StatLine"; \
    Filename: "{uninstallexe}"


; ============================================================
; PATH support
; ============================================================

[Code]

const
  EnvironmentKey = 'Environment';


function NormalizePathPart(S: String): String;
begin
  S := Trim(RemoveQuotes(S));

  while (Length(S) > 0) and (S[Length(S)] = '\') do
    Delete(S, Length(S), 1);

  Result := Lowercase(S);
end;


function PathContains(CurrentPath: String; Directory: String): Boolean;
var
  S: String;
  Item: String;
  P: Integer;
begin
  Result := False;
  S := CurrentPath;

  while S <> '' do
  begin
    P := Pos(';', S);

    if P > 0 then
    begin
      Item := Copy(S, 1, P - 1);
      Delete(S, 1, P);
    end
    else
    begin
      Item := S;
      S := '';
    end;

    if NormalizePathPart(Item) = NormalizePathPart(Directory) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;


function RemovePathEntry(CurrentPath: String; Directory: String): String;
var
  S: String;
  Item: String;
  NewPath: String;
  P: Integer;
begin
  S := CurrentPath;
  NewPath := '';

  while S <> '' do
  begin
    P := Pos(';', S);

    if P > 0 then
    begin
      Item := Copy(S, 1, P - 1);
      Delete(S, 1, P);
    end
    else
    begin
      Item := S;
      S := '';
    end;

    Item := Trim(Item);

    if (Item <> '') and
       (NormalizePathPart(Item) <> NormalizePathPart(Directory)) then
    begin
      if NewPath <> '' then
        NewPath := NewPath + ';';

      NewPath := NewPath + Item;
    end;
  end;

  Result := NewPath;
end;


procedure AddToPath;
var
  CurrentPath: String;
  BinPath: String;
begin
  BinPath := ExpandConstant('{app}\bin');

  if not RegQueryStringValue(
    HKCU,
    EnvironmentKey,
    'Path',
    CurrentPath
  ) then
    CurrentPath := '';

  if not PathContains(CurrentPath, BinPath) then
  begin
    if CurrentPath = '' then
      CurrentPath := BinPath
    else
      CurrentPath := CurrentPath + ';' + BinPath;

    RegWriteExpandStringValue(
      HKCU,
      EnvironmentKey,
      'Path',
      CurrentPath
    );

    Log('Added StatLine to PATH: ' + BinPath);
  end
  else
    Log('StatLine already present in PATH');
end;


procedure RemoveFromPath;
var
  CurrentPath: String;
  BinPath: String;
  NewPath: String;
begin
  BinPath := ExpandConstant('{app}\bin');

  if not RegQueryStringValue(
    HKCU,
    EnvironmentKey,
    'Path',
    CurrentPath
  ) then
    Exit;

  NewPath := RemovePathEntry(CurrentPath, BinPath);

  if NewPath <> CurrentPath then
  begin
    RegWriteExpandStringValue(
      HKCU,
      EnvironmentKey,
      'Path',
      NewPath
    );

    Log('Removed StatLine from PATH: ' + BinPath);
  end;
end;


procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    AddToPath;
end;


procedure CurUninstallStepChanged(
  CurUninstallStep: TUninstallStep
);
begin
  if CurUninstallStep = usUninstall then
    RemoveFromPath;
end;