# Release YAAP (for the maintainer): sets the version everywhere, rolls the changelog, commits, tags the release and
# asks before pushing. Run it in PowerShell from the YAAP folder, on an up-to-date, clean main:
#   powershell -ExecutionPolicy Bypass -File scripts\release.ps1 0.6.0
param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')
$Tag = "v$Version"

function Fail([string]$Message) {
    Write-Host "Release stopped: $Message" -ForegroundColor Red
    exit 1
}

# Runs git and stops on a failure; returns its output.
function Invoke-Git {
    $output = & git @args
    if ($LASTEXITCODE -ne 0) { Fail "git $($args -join ' ') failed." }
    return $output
}

if ($Version -notmatch '^\d+\.\d+\.\d+$') { Fail "'$Version' is not a version like 1.2.3." }
if ((Invoke-Git branch --show-current) -ne 'main') { Fail 'check out main first.' }
if (Invoke-Git status --porcelain) { Fail 'the working copy has uncommitted changes.' }
Invoke-Git fetch origin main --tags --quiet
if ((Invoke-Git rev-parse HEAD) -ne (Invoke-Git rev-parse origin/main)) {
    Fail 'main is not equal to origin/main; pull or push first.'
}
if (Invoke-Git tag --list $Tag) { Fail "the tag $Tag already exists." }

$Python = Join-Path $PWD 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }

Push-Location backend
try {
    & $Python -m app.scripts.set_version $Version
    if ($LASTEXITCODE -ne 0) { Fail 'the version was not set.' }
    $Notes = (& $Python -m app.scripts.set_version --section $Version) -join "`n"
    if ($LASTEXITCODE -ne 0) { Fail 'the changelog has no section.' }
} finally {
    Pop-Location
}

Invoke-Git add -A
Invoke-Git commit --quiet -m "Set the version to $Version"
$Message = "YAAP $Version`n`n$Notes"
Invoke-Git tag -a $Tag -m $Message
Write-Host ''
Write-Host "Committed and tagged ${Tag}:"
Invoke-Git show --no-patch --format='%h %s' HEAD
Write-Host ''
Write-Host $Notes
Write-Host ''
$Answer = Read-Host "Push main and $Tag to origin now? [y/N]"
if ($Answer -eq 'y' -or $Answer -eq 'Y') {
    Invoke-Git push origin main $Tag
    Write-Host "Pushed $Tag."
} else {
    Write-Host "Not pushed. Push later with: git push origin main $Tag"
}
