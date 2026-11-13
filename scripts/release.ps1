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

# The workflow .github/workflows/release.yml builds the images and then publishes the GitHub Release when the tag
# arrives; the public API shows when it is there, so this needs no token. Unauthenticated, GitHub allows 60 requests an
# hour: one every 90 seconds, for up to 90 minutes. Without a Release by then, it says how to make one by hand.
function Wait-ForRelease {
    $Remote = Invoke-Git remote get-url origin
    if ($Remote -notmatch '^(?:https://(?:[^@/]*@)?github\.com/|git@github\.com:)(?<repo>[^/]+/[^/]+?)(?:\.git)?$') {
        Write-Host 'origin is not on github.com, so there is no GitHub Release to wait for.'
        return
    }
    $Repo = $Matches['repo']
    $Url = "https://api.github.com/repos/$Repo/releases/tags/$Tag"
    Write-Host "Waiting for the images and the GitHub Release of $Tag (Actions: https://github.com/$Repo/actions)..."
    for ($Try = 0; $Try -lt 60; $Try++) {
        try {
            Invoke-RestMethod -Uri $Url -ErrorAction Stop | Out-Null
            Write-Host "Released: https://github.com/$Repo/releases/tag/$Tag" -ForegroundColor Green
            return
        } catch {
            # A 404 until the workflow has run, or a network hiccup: both mean "not yet". The exception types differ
            # between Windows PowerShell 5.1 and PowerShell 7, so this catches all; the loop ends after 90 minutes.
            Start-Sleep -Seconds 90
        }
    }
    Write-Host "No GitHub Release of $Tag after 90 minutes; see https://github.com/$Repo/actions." -ForegroundColor Yellow
    Write-Host "To make it by hand, open https://github.com/$Repo/releases/new?tag=$Tag with the title"
    Write-Host "`"YAAP $Version`" and this text:"
    Write-Host ''
    Write-Host $Notes
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
# Through a file: Windows PowerShell 5.1 splits an argument with double quotes in it, which the notes can have.
$MessageFile = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($MessageFile, $Message, (New-Object System.Text.UTF8Encoding $false))
    Invoke-Git tag -a $Tag --cleanup=whitespace -F $MessageFile
} finally {
    Remove-Item -LiteralPath $MessageFile -ErrorAction SilentlyContinue
}
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
    Wait-ForRelease
} else {
    Write-Host "Not pushed. Push later with: git push origin main $Tag"
}
