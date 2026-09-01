[CmdletBinding(DefaultParameterSetName = "Source")]

param(

    # Default: build the exact working tree this script lives under, including uncommitted changes.

    # Use -Ref main / -Ref v4.0.0rc5 / -Ref <commit> to build an isolated Git snapshot instead.

    [Parameter(ParameterSetName = "Source")]

    [string]$Ref,

    # Build an installer around an already-built wheel instead of building the source tree.

    [Parameter(Mandatory = $true, ParameterSetName = "Wheel")]

    [string]$Wheel,

    # The installer historically shipped the full StatLine experience, so extras is the default.

    [ValidateSet("base", "os", "remote", "extras")]

    [string]$Variant = "extras",

    # A major/minor selector is fine; uv resolves it to an exact CPython release.

    [string]$PythonVersion = "3.14",

    # Relative paths are resolved from the repository root.

    [string]$OutputDir = "installer\dist",

    # Preserve the temporary payload/ISS/worktree for inspection.

    [switch]$KeepWork

)

Set-StrictMode -Version Latest

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {

    Write-Host "`n==> $Message" -ForegroundColor Cyan

}

function Invoke-Native {

    param(

        [Parameter(Mandatory = $true)][string]$Command,

        [Parameter()][string[]]$Arguments = @(),

        [Parameter()][string]$WorkingDirectory

    )

    $old = Get-Location

    try {

        if ($WorkingDirectory) {

            Set-Location $WorkingDirectory

        }

        Write-Host ("> " + $Command + " " + ($Arguments -join " ")) -ForegroundColor DarkGray

        & $Command @Arguments

        $code = $LASTEXITCODE

        if ($code -ne 0) {

            throw "Command failed with exit code $code`: $Command $($Arguments -join ' ')"

        }

    }

    finally {

        Set-Location $old

    }

}

function Find-RepoRoot {

    $candidates = @()

    if ($PSScriptRoot) {

        $candidates += $PSScriptRoot

        $candidates += (Join-Path $PSScriptRoot "..")

    }

    $candidates += (Get-Location).Path

    foreach ($candidate in $candidates) {

        try {

            $resolved = (Resolve-Path $candidate -ErrorAction Stop).Path

            if (Test-Path (Join-Path $resolved "pyproject.toml")) {

                return $resolved

            }

        }

        catch {

            # Try the next candidate.

        }

    }

    throw "Could not find the StatLine repository root (pyproject.toml). Put this script in the repo root or scripts/."

}

function Get-ProjectVersion([string]$PyprojectPath) {

    $text = Get-Content -Raw -LiteralPath $PyprojectPath

    $project = [regex]::Match(

        $text,

        '(?ms)^\[project\]\s*(?<body>.*?)(?=^\[|\z)'

    )

    if (-not $project.Success) {

        throw "Could not locate [project] in $PyprojectPath"

    }

    $match = [regex]::Match($project.Groups['body'].Value, '(?m)^version\s*=\s*"(?<v>[^"]+)"\s*$')

    if (-not $match.Success) {

        throw "Could not locate project.version in $PyprojectPath"

    }

    return $match.Groups['v'].Value

}

function Get-WheelVersion([string]$WheelPath) {

    $name = [System.IO.Path]::GetFileName($WheelPath)

    $match = [regex]::Match($name, '^statline-(?<v>[^-]+)-.+\.whl$', 'IgnoreCase')

    if (-not $match.Success) {

        throw "Wheel filename does not look like a StatLine wheel: $name"

    }

    return $match.Groups['v'].Value

}

function Find-Iscc {

    $fromPath = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue

    if ($fromPath) {

        return $fromPath.Source

    }

    # Force collection semantics even when exactly one ISCC path exists. Under

    # Set-StrictMode a one-item pipeline otherwise becomes a scalar and has no .Count.

    $candidates = @(

        @(

            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),

            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),

            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")

        ) | Where-Object { $_ -and (Test-Path $_) }

    )

    if ($candidates.Count -gt 0) {

        return $candidates[0]

    }

    throw @"

Inno Setup 6 compiler (ISCC.exe) was not found.

Install it once, then rerun this script:

  winget install --id JRSoftware.InnoSetup -e

"@

}

if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {

    throw "uv is required and was not found on PATH. Install uv, then rerun this script."

}

if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {

    throw "git is required and was not found on PATH."

}

$repoRoot = Find-RepoRoot

if ([System.IO.Path]::IsPathRooted($OutputDir)) {

    $finalOutputDir = [System.IO.Path]::GetFullPath($OutputDir)

}

else {

    $finalOutputDir = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDir))

}

New-Item -ItemType Directory -Force -Path $finalOutputDir | Out-Null

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("statline-installer-" + [guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

$worktreePath = $null

$sourceRoot = $repoRoot

$buildVenv = Join-Path $tempRoot "build-venv"

$buildDist = Join-Path $tempRoot "dist"

$payloadRoot = Join-Path $tempRoot "installer\payload"

$runtimeRoot = Join-Path $payloadRoot "runtime"

$binRoot = Join-Path $payloadRoot "bin"

$assetsRoot = Join-Path $payloadRoot "assets"

$issPath = Join-Path $tempRoot "installer\StatLine.iss"

try {

    if ($PSCmdlet.ParameterSetName -eq "Source" -and $Ref) {

        Write-Step "Creating isolated source tree for Git ref '$Ref'"

        $worktreePath = Join-Path $tempRoot "source"

        Invoke-Native "git" @("-C", $repoRoot, "worktree", "add", "--detach", $worktreePath, $Ref)

        $sourceRoot = $worktreePath

    }

    elseif ($PSCmdlet.ParameterSetName -eq "Source") {

        Write-Step "Using the current working tree (dirty changes are included)"

        Write-Host "Source: $sourceRoot"

    }

    Write-Step "Creating clean Python $PythonVersion build environment"

    Invoke-Native "uv" @("venv", $buildVenv, "--python", $PythonVersion)

    $buildPython = Join-Path $buildVenv "Scripts\python.exe"

    if (-not (Test-Path $buildPython)) {

        throw "Build Python was not created at $buildPython"

    }

    Invoke-Native "uv" @(

        "pip", "install", "--python", $buildPython,

        "build>=1.2,<2.0", "twine>=7.0,<8.0"

    )

    if ($PSCmdlet.ParameterSetName -eq "Wheel") {

        $wheelPath = (Resolve-Path -LiteralPath $Wheel).Path

        $version = Get-WheelVersion $wheelPath

        Write-Step "Using supplied wheel: $([System.IO.Path]::GetFileName($wheelPath))"

        Invoke-Native $buildPython @("-m", "twine", "check", $wheelPath)

    }

    else {

        $version = Get-ProjectVersion (Join-Path $sourceRoot "pyproject.toml")

        Write-Step "Building StatLine $version from source"

        New-Item -ItemType Directory -Force -Path $buildDist | Out-Null

        Invoke-Native $buildPython @("-m", "build", "--outdir", $buildDist, $sourceRoot)

        $artifacts = @(Get-ChildItem -LiteralPath $buildDist -File | Sort-Object Name)

        if ($artifacts.Count -eq 0) {

            throw "Build produced no distributions."

        }

        Invoke-Native $buildPython (@("-m", "twine", "check") + @($artifacts.FullName))

        $wheels = @($artifacts | Where-Object { $_.Name -like "statline-$version-*.whl" })

        if ($wheels.Count -ne 1) {

            throw "Expected exactly one StatLine $version wheel; found $($wheels.Count)."

        }

        $wheelPath = $wheels[0].FullName

    }

    $resolvedPython = (& $buildPython -c "import platform; print(platform.python_version())").Trim()

    if ($LASTEXITCODE -ne 0 -or -not $resolvedPython) {

        throw "Could not resolve the exact runtime Python version."

    }

    $pythonDigits = (& $buildPython -c "import sys; print(f'{sys.version_info.major}{sys.version_info.minor}')").Trim()

    $pythonMajorMinor = (& $buildPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()

    Write-Step "Creating standalone CPython $resolvedPython runtime"

    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

    New-Item -ItemType Directory -Force -Path $binRoot | Out-Null

    New-Item -ItemType Directory -Force -Path $assetsRoot | Out-Null

    $statpackIconSource = Join-Path $sourceRoot "assets\statpack-icon-main.png"

    if (-not (Test-Path -LiteralPath $statpackIconSource -PathType Leaf)) {

        throw "StatPack icon was not found: $statpackIconSource"

    }

    Copy-Item -LiteralPath $statpackIconSource -Destination (Join-Path $assetsRoot "statpack-icon-main.png") -Force

    $embedZip = Join-Path $tempRoot "python-embed.zip"

    $embedUrl = "https://www.python.org/ftp/python/$resolvedPython/python-$resolvedPython-embed-amd64.zip"

    Write-Host "Downloading $embedUrl"

    Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip -Headers @{ "User-Agent" = "StatLine-Installer-Builder" }

    Expand-Archive -LiteralPath $embedZip -DestinationPath $runtimeRoot -Force

    $sitePackages = Join-Path $runtimeRoot "Lib\site-packages"

    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

    $pthPath = Join-Path $runtimeRoot ("python" + $pythonDigits + "._pth")

    if (-not (Test-Path $pthPath)) {

        $pthCandidate = Get-ChildItem -LiteralPath $runtimeRoot -Filter "python*._pth" | Select-Object -First 1

        if (-not $pthCandidate) {

            throw "Embedded Python did not contain a python*._pth file."

        }

        $pthPath = $pthCandidate.FullName

    }

    $pthLines = @(Get-Content -LiteralPath $pthPath)

    $newPth = New-Object System.Collections.Generic.List[string]

    $hasSitePackages = $false

    $hasImportSite = $false

    foreach ($line in $pthLines) {

        if ($line -match '^\s*Lib[\\/]site-packages\s*$') {

            $hasSitePackages = $true

            $newPth.Add('Lib\site-packages')

        }

        elseif ($line -match '^\s*#\s*import site\s*$' -or $line -match '^\s*import site\s*$') {

            if (-not $hasSitePackages) {

                $newPth.Add('Lib\site-packages')

                $hasSitePackages = $true

            }

            $newPth.Add('import site')

            $hasImportSite = $true

        }

        else {

            $newPth.Add($line)

        }

    }

    if (-not $hasSitePackages) {

        $newPth.Add('Lib\site-packages')

    }

    if (-not $hasImportSite) {

        $newPth.Add('import site')

    }

    Set-Content -LiteralPath $pthPath -Value $newPth -Encoding Ascii

    Write-Step "Installing StatLine $version ($Variant) into the standalone runtime"

    if ($Variant -eq "base") {

        $wheelSpec = $wheelPath

    }

    else {

        $wheelSpec = "${wheelPath}[$Variant]"

    }

    Invoke-Native "uv" @(

        "pip", "install",

        "--target", $sitePackages,

        "--python-version", $pythonMajorMinor,

        "--compile-bytecode",

        $wheelSpec

    )

    # Reassert the payload directories immediately before writing launchers.
    # Set-Content can create a file, but not a missing parent directory.
    [System.IO.Directory]::CreateDirectory($payloadRoot) | Out-Null
    [System.IO.Directory]::CreateDirectory($runtimeRoot) | Out-Null
    [System.IO.Directory]::CreateDirectory($binRoot) | Out-Null

    if (-not (Test-Path -LiteralPath $binRoot -PathType Container)) {
        throw "Payload bin directory could not be created: $binRoot"
    }

    $statlineLauncher = Join-Path $binRoot "statline.cmd"
    $slapiLauncher = Join-Path $binRoot "slapi.cmd"

    @'

@echo off

"%~dp0..\runtime\python.exe" -m statline %*

'@ | Set-Content -LiteralPath $statlineLauncher -Encoding Ascii

    @'

@echo off

"%~dp0..\runtime\python.exe" -c "from statline.app.runner.main import main; main()" %*

'@ | Set-Content -LiteralPath $slapiLauncher -Encoding Ascii

    if (-not (Test-Path -LiteralPath $statlineLauncher -PathType Leaf)) {
        throw "Failed to create StatLine launcher: $statlineLauncher"
    }

    if (-not (Test-Path -LiteralPath $slapiLauncher -PathType Leaf)) {
        throw "Failed to create SLAPI launcher: $slapiLauncher"
    }

    Write-Step "Smoke-testing the payload before packaging"

    $runtimePython = Join-Path $runtimeRoot "python.exe"

    $installedVersion = (& $runtimePython -c "import statline; print(statline.__version__)").Trim()

    if ($LASTEXITCODE -ne 0) {

        throw "Embedded StatLine import failed."

    }

    if ($installedVersion.TrimStart('v') -ne $version.TrimStart('v')) {

        throw "Payload version mismatch: expected $version, got $installedVersion"

    }

    Invoke-Native $runtimePython @("-m", "statline", "--help")

    if ($Variant -eq "extras") {

        Invoke-Native $runtimePython @("-c", "import cryptography, fastapi, textual, uvicorn; print('extras: OK')")

    }

    Write-Step "Generating Inno Setup definition"

    $issDir = Split-Path -Parent $issPath

    New-Item -ItemType Directory -Force -Path $issDir | Out-Null

    $escapedOutput = $finalOutputDir.Replace('"', '""')

    $iss = @"

#define MyAppName "StatLine"

#define MyAppVersion "$version"

#define MyAppPublisher "StatLine LLC"

[Setup]

AppId={{E091F35A-590A-4E72-9D63-5866AC71E590}

AppName={#MyAppName}

AppVersion={#MyAppVersion}

AppPublisher={#MyAppPublisher}

DefaultDirName={localappdata}\Programs\StatLine

DefaultGroupName=StatLine

PrivilegesRequired=lowest

OutputDir=$escapedOutput

OutputBaseFilename=StatLineSetup-v$version

Compression=lzma2

SolidCompression=yes

WizardStyle=modern

ChangesAssociations=yes

ChangesEnvironment=yes

UninstallDisplayName=StatLine {#MyAppVersion}

UninstallDisplayIcon={app}\runtime\python.exe

ArchitecturesAllowed=x64compatible

ArchitecturesInstallIn64BitMode=x64compatible

[Files]

Source: "payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]

Root: HKCU; Subkey: "Software\Classes\.statpack"; ValueType: string; ValueName: ""; ValueData: "StatLine.StatPack"; Flags: uninsdeletevalue

Root: HKCU; Subkey: "Software\Classes\.statpack"; ValueType: string; ValueName: "Content Type"; ValueData: "application/vnd.statline.statpack"

Root: HKCU; Subkey: "Software\Classes\StatLine.StatPack"; ValueType: string; ValueName: ""; ValueData: "StatLine StatPack"; Flags: uninsdeletekey

Root: HKCU; Subkey: "Software\Classes\StatLine.StatPack"; ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "StatLine StatPack"

Root: HKCU; Subkey: "Software\Classes\StatLine.StatPack\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\assets\statpack-icon-main.png"

Root: HKCU; Subkey: "Software\Classes\StatLine.StatPack\shell"; ValueType: string; ValueName: ""; ValueData: "open"

Root: HKCU; Subkey: "Software\Classes\StatLine.StatPack\shell\open"; ValueType: string; ValueName: ""; ValueData: "Run with StatLine"

Root: HKCU; Subkey: "Software\Classes\StatLine.StatPack\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\runtime\python.exe"" -m statline statpack run --pause ""%1"""

[Icons]

Name: "{group}\StatLine"; Filename: "{app}\bin\statline.cmd"; WorkingDir: "{%USERPROFILE}"

Name: "{group}\Uninstall StatLine"; Filename: "{uninstallexe}"

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

  S, Item: String;

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

  S, Item, NewPath: String;

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

    if (Item <> '') and (NormalizePathPart(Item) <> NormalizePathPart(Directory)) then

    begin

      if NewPath <> '' then NewPath := NewPath + ';';

      NewPath := NewPath + Item;

    end;

  end;

  Result := NewPath;

end;

procedure AddToPath;

var

  CurrentPath, BinPath: String;

begin

  BinPath := ExpandConstant('{app}\bin');

  if not RegQueryStringValue(HKCU, EnvironmentKey, 'Path', CurrentPath) then

    CurrentPath := '';

  if not PathContains(CurrentPath, BinPath) then

  begin

    if CurrentPath = '' then CurrentPath := BinPath

    else CurrentPath := CurrentPath + ';' + BinPath;

    RegWriteExpandStringValue(HKCU, EnvironmentKey, 'Path', CurrentPath);

    Log('Added StatLine to PATH: ' + BinPath);

  end;

end;

procedure RemoveFromPath;

var

  CurrentPath, BinPath, NewPath: String;

begin

  BinPath := ExpandConstant('{app}\bin');

  if not RegQueryStringValue(HKCU, EnvironmentKey, 'Path', CurrentPath) then Exit;

  NewPath := RemovePathEntry(CurrentPath, BinPath);

  if NewPath <> CurrentPath then

  begin

    RegWriteExpandStringValue(HKCU, EnvironmentKey, 'Path', NewPath);

    Log('Removed StatLine from PATH: ' + BinPath);

  end;

end;

procedure CurStepChanged(CurStep: TSetupStep);

begin

  if CurStep = ssPostInstall then AddToPath;

end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);

begin

  if CurUninstallStep = usUninstall then RemoveFromPath;

end;

"@

    Set-Content -LiteralPath $issPath -Value $iss -Encoding UTF8

    $iscc = Find-Iscc

    Write-Step "Compiling StatLineSetup-v$version.exe"

    Invoke-Native $iscc @($issPath) $issDir

    $installerPath = Join-Path $finalOutputDir "StatLineSetup-v$version.exe"

    if (-not (Test-Path $installerPath)) {

        throw "Inno Setup completed but the expected installer was not found: $installerPath"

    }

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installerPath).Hash

    Write-Host "`nSUCCESS" -ForegroundColor Green

    Write-Host "Installer : $installerPath"

    Write-Host "Version   : $version"

    Write-Host "Source    : $(if ($Ref) { $Ref } elseif ($PSCmdlet.ParameterSetName -eq 'Wheel') { $wheelPath } else { 'current working tree' })"

    Write-Host "Variant   : $Variant"

    Write-Host "Python    : $resolvedPython"

    Write-Host "SHA256    : $hash"

    if ($KeepWork) {

        Write-Host "Work dir  : $tempRoot"

    }

}

finally {

    if ($worktreePath -and (Test-Path $worktreePath)) {

        try {

            Invoke-Native "git" @("-C", $repoRoot, "worktree", "remove", "--force", $worktreePath)

        }

        catch {

            Write-Warning "Could not remove temporary Git worktree: $worktreePath"

        }

    }

    if (-not $KeepWork -and (Test-Path $tempRoot)) {

        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue

    }

}
