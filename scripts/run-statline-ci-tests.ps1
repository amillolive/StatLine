[CmdletBinding()]
param(
    # Positional pytest selector. Examples:
    #   tests\test_gateway_v4.py
    #   tests\test_gateway_v4.py::test_name
    # Omit it to run the whole tests/ tree.
    [Parameter(Position = 0)]
    [string]$TestTarget = "tests",

    # GitHub's current matrix is 3.10 through 3.14. Default is the CI tooling version.
    [ValidateSet("3.10", "3.11", "3.12", "3.13", "3.14")]
    [string]$PythonVersion = "3.14",

    # Run the test matrix for every Python version on this operating system.
    [switch]$AllPython,

    # Also mirror Ruff, Mypy, Pyright, build/Twine, and wheel smoke from ci.yml.
    [switch]$FullCI,

    # Extra arguments appended to pytest, e.g. -PytestArgs @('-k','gateway','-vv')
    [string[]]$PytestArgs = @(),

    # Keep temporary virtual environments instead of deleting them.
    [switch]$KeepEnvs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
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
            if ((Test-Path (Join-Path $resolved "pyproject.toml")) -and
                (Test-Path (Join-Path $resolved ".github\workflows\ci.yml"))) {
                return $resolved
            }
        }
        catch {
            # Try the next candidate.
        }
    }

    throw "Could not find the StatLine repository root. Put this script in the repo root or scripts/."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter()][string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [switch]$AllowFailure
    )

    $old = Get-Location
    $code = 1
    try {
        Set-Location $WorkingDirectory
        Write-Host ("> " + $Command + " " + ($Arguments -join " ")) -ForegroundColor DarkGray
        & $Command @Arguments | Out-Host
        $code = $LASTEXITCODE
    }
    finally {
        Set-Location $old
    }

    if ($code -ne 0 -and -not $AllowFailure) {
        throw "Command failed with exit code $code`: $Command $($Arguments -join ' ')"
    }
    return $code
}

function New-CIEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$Root,
        [string[]]$Packages = @(),
        [switch]$EditableDevpack
    )

    $safeVersion = $Version.Replace('.', '')
    $venv = Join-Path $Root "$Name-py$safeVersion"
    Write-Step "Preparing $Name environment (Python $Version)"
    $null = Invoke-Checked "uv" @("venv", $venv, "--python", $Version) $repoRoot

    $python = Join-Path $venv "Scripts\python.exe"
    if (-not (Test-Path $python)) {
        throw "uv did not create $python"
    }

    if ($EditableDevpack) {
        # Exact install form from the GitHub mypy/tests jobs.
        $null = Invoke-Checked "uv" @("pip", "install", "--python", $python, "-e", ".[devpack]") $repoRoot
    }
    elseif ($Packages.Count -gt 0) {
        $null = Invoke-Checked "uv" (@("pip", "install", "--python", $python) + $Packages) $repoRoot
    }

    return $python
}

function Run-PytestLikeGitHub {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$Target
    )

    $safeVersion = $Version.Replace('.', '')
    $resultDir = Join-Path $repoRoot "test-results\local-py$safeVersion"
    New-Item -ItemType Directory -Force -Path $resultDir | Out-Null

    $junit = Join-Path $resultDir "junit.xml"
    $coverage = Join-Path $resultDir "coverage.xml"

    # Keep pytest's tmp_path/tmpdir state inside this isolated CI run. Using
    # pytest's default %TEMP%\pytest-of-$env:USERNAME location makes the local
    # matrix vulnerable to stale or ACL-broken directories left by unrelated
    # pytest runs on Windows.
    $baseTemp = Join-Path $tempRoot "pytest-py$safeVersion"
    if (Test-Path -LiteralPath $baseTemp) {
        Remove-Item -LiteralPath $baseTemp -Recurse -Force
    }

    if ($Target -eq "tests") {
        $detected = Get-ChildItem -Path (Join-Path $repoRoot "tests") -Filter "test_*.py" -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $detected) {
            Write-Warning "No tests detected. Writing empty result files, matching GitHub CI."
            '<?xml version="1.0" ?><testsuite></testsuite>' | Set-Content -LiteralPath $junit -Encoding UTF8
            '<?xml version="1.0" ?><coverage></coverage>' | Set-Content -LiteralPath $coverage -Encoding UTF8
            return 0
        }
    }

    $args = @(
        "-m", "pytest",
        "-ra",
        "--maxfail=1",
        "--durations=10",
        "--cov=statline",
        "--cov-report=xml:$coverage",
        "--junitxml=$junit",
        "--basetemp=$baseTemp",
        $Target
    ) + $PytestArgs

    Write-Step "GitHub-equivalent pytest: Python $Version / $Target"
    return Invoke-Checked $Python $args $repoRoot -AllowFailure
}

if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    throw "uv is required and was not found on PATH."
}

$repoRoot = Find-RepoRoot
$targetFile = $TestTarget.Split('::')[0]
if ($targetFile -match '\.py$') {
    $candidate = if ([System.IO.Path]::IsPathRooted($targetFile)) { $targetFile } else { Join-Path $repoRoot $targetFile }
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw "Test target does not exist: $targetFile"
    }
}

# Exact repository-level environment from .github/workflows/ci.yml.
$ciEnv = @{
    PIP_DISABLE_PIP_VERSION_CHECK = "1"
    PIP_NO_PYTHON_VERSION_WARNING = "1"
    PYTHONUNBUFFERED = "1"
    PYTHONIOENCODING = "utf-8"
    PYTHONUTF8 = "1"
    TZ = "UTC"
    CI = "1"
    ORION_TOKEN = "TEST_TOKEN"
    STATLINE_ENV = "test"
    STATLINE_MODE = "local"
    STATLINE_DEBUG = "0"
    STATLINE_LOADER_STRICT = "1"
    SKIP_LIVE_SHEETS = "1"
    SLAPI_URL = "http://127.0.0.1:8000"
}

$oldEnv = @{}
foreach ($key in $ciEnv.Keys) {
    $oldEnv[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
    [Environment]::SetEnvironmentVariable($key, $ciEnv[$key], "Process")
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("statline-ci-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
$failures = New-Object System.Collections.Generic.List[string]
$advisories = New-Object System.Collections.Generic.List[string]
$finalExitCode = 1

try {
    Write-Host "StatLine local CI mirror" -ForegroundColor Green
    Write-Host "Repo       : $repoRoot"
    Write-Host "Target     : $TestTarget"
    Write-Host "Mode       : $(if ($FullCI) { 'full CI' } else { 'pytest job' })"
    Write-Host "OS         : $([System.Environment]::OSVersion.VersionString)"

    $versions = if ($AllPython) {
        @("3.10", "3.11", "3.12", "3.13", "3.14")
    }
    else {
        @($PythonVersion)
    }

    # These jobs are independent in GitHub, so a Ruff/Mypy failure does not stop tests from running.
    if ($FullCI) {
        $ruffPython = New-CIEnvironment -Name "ruff" -Version "3.14" -Root $tempRoot -Packages @("ruff>=0.5,<1.0")

        Write-Step "Ruff format check"
        $code = Invoke-Checked $ruffPython @("-m", "ruff", "format", "--check", ".") $repoRoot -AllowFailure
        if ($code -ne 0) { $failures.Add("Ruff format") }

        Write-Step "Ruff lint"
        $code = Invoke-Checked $ruffPython @("-m", "ruff", "check", ".", "--output-format=github") $repoRoot -AllowFailure
        if ($code -ne 0) { $failures.Add("Ruff lint") }

        $mypyPython = New-CIEnvironment -Name "mypy" -Version "3.14" -Root $tempRoot -EditableDevpack

        Write-Step "Mypy core (blocking)"
        $code = Invoke-Checked $mypyPython @("-m", "mypy", "--config-file", "mypy-core.ini", ".") $repoRoot -AllowFailure
        if ($code -ne 0) { $failures.Add("Mypy core") }

        Write-Step "Mypy nice (advisory / non-blocking)"
        $code = Invoke-Checked $mypyPython @("-m", "mypy", "--config-file", "mypy-nice.ini", ".") $repoRoot -AllowFailure
        if ($code -ne 0) {
            $advisories.Add("Mypy nice")
            Write-Warning "Mypy nice failed. GitHub treats this as non-blocking."
        }

        Write-Step "Pyright strict (blocking / Pylance parity)"
        $code = Invoke-Checked $mypyPython @("-m", "pyright") $repoRoot -AllowFailure
        if ($code -ne 0) { $failures.Add("Pyright") }
    }

    # GitHub uses fail-fast:false, so every requested Python version is attempted.
    foreach ($version in $versions) {
        $testPython = New-CIEnvironment -Name "tests" -Version $version -Root $tempRoot -EditableDevpack
        $code = Run-PytestLikeGitHub $testPython $version $TestTarget
        if ($code -ne 0) {
            $failures.Add("Tests py$version")
        }
    }

    if ($FullCI) {
        # GitHub's build job depends on Ruff, Mypy, Pyright, and tests, so it only runs if all blocking jobs pass.
        if ($failures.Count -eq 0) {
            $buildPython = New-CIEnvironment -Name "build" -Version "3.14" -Root $tempRoot -Packages @("build>=1.2,<2.0", "twine>=7.0,<8.0")
            $localDist = Join-Path $tempRoot "dist"
            New-Item -ItemType Directory -Force -Path $localDist | Out-Null

            Write-Step "Build sdist & wheel"
            $code = Invoke-Checked $buildPython @("-m", "build", "--outdir", $localDist) $repoRoot -AllowFailure
            if ($code -ne 0) {
                $failures.Add("Build")
            }
            else {
                $distFiles = @(Get-ChildItem -LiteralPath $localDist -File)
                if ($distFiles.Count -eq 0) {
                    $failures.Add("Build produced no artifacts")
                }
                else {
                    Write-Step "Twine check"
                    $code = Invoke-Checked $buildPython (@("-m", "twine", "check") + @($distFiles.FullName)) $repoRoot -AllowFailure
                    if ($code -ne 0) {
                        $failures.Add("Twine check")
                    }
                    else {
                        $wheel = @($distFiles | Where-Object { $_.Extension -eq ".whl" } | Select-Object -First 1)
                        if ($wheel.Count -eq 0) {
                            $failures.Add("Wheel smoke: no wheel artifact")
                        }
                        else {
                            $smokePython = New-CIEnvironment -Name "smoke" -Version "3.14" -Root $tempRoot

                            Write-Step "Wheel smoke: install"
                            $code = Invoke-Checked "uv" @("pip", "install", "--python", $smokePython, $wheel[0].FullName) $tempRoot -AllowFailure
                            if ($code -ne 0) {
                                $failures.Add("Wheel smoke install")
                            }
                            else {
                                Write-Step "Wheel smoke: import package"
                                $code = Invoke-Checked $smokePython @("-c", "import statline; print('Import OK', statline.__version__)") $tempRoot -AllowFailure
                                if ($code -ne 0) { $failures.Add("Wheel smoke import") }

                                Write-Step "Wheel smoke: module CLI help"
                                $code = Invoke-Checked $smokePython @("-m", "statline.cli", "--mode", "local", "--help") $tempRoot -AllowFailure
                                if ($code -ne 0) { $failures.Add("Wheel smoke module CLI") }

                                $statlineExe = Join-Path (Split-Path $smokePython) "statline.exe"
                                Write-Step "Wheel smoke: console script help"
                                $code = Invoke-Checked $statlineExe @("--mode", "local", "--help") $tempRoot -AllowFailure
                                if ($code -ne 0) { $failures.Add("Wheel smoke console script") }

                                $publicSmoke = "from statline import list_adapters,list_datasets,load_adapter,load_dataset,score_row; adapters=list_adapters(); assert adapters and 'eba.players' in adapters, adapters; datasets=list_datasets(); assert 'EBA_Elevate302/eba_s1_players.csv' in datasets, datasets; adapter=load_adapter('eba.players'); row=load_dataset('EBA_Elevate302/eba_s1_players',limit=1)[0]; result=score_row(adapter,row); assert isinstance(result['pri'],int), result; assert 'scores' in result, result; print('Smoke OK:',adapter.key,result['pri'])"
                                Write-Step "Wheel smoke: public API"
                                $code = Invoke-Checked $smokePython @("-c", $publicSmoke) $tempRoot -AllowFailure
                                if ($code -ne 0) { $failures.Add("Wheel smoke public API") }

                                $registrySmoke = "from statline.core.adapters import list_adapters,load_adapter; names=list_adapters(); assert names, 'No adapters discovered'; adapters=[load_adapter(name) for name in names]; assert all(a.key and a.score_profiles for a in adapters); print('Adapters OK:', ', '.join(names))"
                                Write-Step "Wheel smoke: adapter registry"
                                $code = Invoke-Checked $smokePython @("-c", $registrySmoke) $tempRoot -AllowFailure
                                if ($code -ne 0) { $failures.Add("Wheel smoke adapter registry") }
                            }
                        }
                    }
                }
            }
        }
        else {
            Write-Host "`n==> Build skipped because a blocking prerequisite failed (same gating as GitHub)." -ForegroundColor Yellow
        }
    }

    Write-Host "`n================ LOCAL CI RESULT ================" -ForegroundColor Cyan
    if ($advisories.Count -gt 0) {
        Write-Host ("Advisory : " + ($advisories -join ", ")) -ForegroundColor Yellow
    }

    if ($failures.Count -eq 0) {
        Write-Host "PASS" -ForegroundColor Green
        Write-Host "GitHub-equivalent blocking checks passed for the requested local matrix."
        Write-Host "Results: $(Join-Path $repoRoot 'test-results\local-py*')"
        $finalExitCode = 0
    }
    else {
        Write-Host "FAIL" -ForegroundColor Red
        foreach ($failure in $failures) {
            Write-Host "  - $failure" -ForegroundColor Red
        }
        Write-Host "Results: $(Join-Path $repoRoot 'test-results\local-py*')"
        $finalExitCode = 1
    }
}
finally {
    foreach ($key in $ciEnv.Keys) {
        [Environment]::SetEnvironmentVariable($key, $oldEnv[$key], "Process")
    }

    if ($KeepEnvs) {
        Write-Host "Temporary CI environments kept at: $tempRoot" -ForegroundColor Yellow
    }
    elseif (Test-Path $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

exit $finalExitCode
