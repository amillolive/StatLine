[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$branch = (& git branch --show-current).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to determine the current Git branch."
}

if ($branch -notin @("main", "next")) {
    Write-Host "Not syncing component mirrors from branch: $branch"
    exit 0
}

function Sync-Component {
    param(
        [Parameter(Mandatory = $true)][string]$Prefix,
        [Parameter(Mandatory = $true)][string]$Remote
    )

    Write-Host ""
    Write-Host "Checking $Prefix -> $Remote/$branch"

    $sha = (& git subtree split --prefix=$Prefix HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $sha) {
        throw "Failed to split $Prefix."
    }

    $remoteLine = & git ls-remote --heads $Remote "refs/heads/$branch"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read $Remote/$branch."
    }

    $remoteSha = ""
    if ($remoteLine) {
        $remoteSha = ($remoteLine -split "\s+")[0]
    }

    if ($sha -eq $remoteSha) {
        Write-Host "$Remote/$branch already synchronized."
        return
    }

    Write-Host "Updating $Remote/$branch"
    Write-Host "  old: $(if ($remoteSha) { $remoteSha } else { '<new branch>' })"
    Write-Host "  new: $sha"

    & git push $Remote "${sha}:refs/heads/$branch"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to push $Prefix to $Remote/$branch."
    }
}

Sync-Component -Prefix "statline/core" -Remote "core"
Sync-Component -Prefix "statline/app" -Remote "app"
Sync-Component -Prefix "statline/gateway" -Remote "gateway"

Write-Host ""
Write-Host "StatLine component synchronization complete."
