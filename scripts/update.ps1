# Update YAAP on Windows to the newest release (or to the newest commit of main), with a backup first and a way back
# when the new version does not start. Run it in PowerShell from the YAAP folder:
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 [-Channel stable|main] [-Yes]   update
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -Rollback [-Yes]                 go back to the version before the last update
# Needs a clean checkout: it stops and names the files when you changed a tracked file. Your data, .env and
# docker-compose.override.yml are never touched. The decisions (busy, backup, rollback, changelog) are Python, in
# the image: python -m app.scripts.update; this script only runs git and Docker.
param(
    [ValidateSet('stable', 'main')][string]$Channel = 'stable',
    [switch]$Yes,
    [switch]$Rollback
)
$ErrorActionPreference = 'Stop'
# Docker and git write UTF-8; Windows PowerShell 5.1 would otherwise read it in the console code page.
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location (Join-Path $PSScriptRoot '..')
$Url = 'http://127.0.0.1:8080'
$Images = 'yaap', 'yaap-sandbox', 'yaap-sandbox-helper'

function Fail([string]$Message) {
    Write-Host "Update stopped: $Message" -ForegroundColor Red
    exit 1
}

# Where the data folder is on this computer (YAAP_DATA_HOST in .env or the environment, else .\data).
$Data = $env:YAAP_DATA_HOST
if (-not $Data -and (Test-Path .env)) {
    $line = Get-Content .env | Where-Object { $_ -match '^YAAP_DATA_HOST=' } | Select-Object -Last 1
    if ($line) { $Data = ($line -replace '^YAAP_DATA_HOST=', '').Trim().Trim('"', "'") }
}
if (-not $Data) { $Data = './data' }
$State = Join-Path $Data 'update-state.json'
$Log = Join-Path $Data 'update.log'

function Ask([string]$Question, [bool]$Default = $true) {
    if ($Yes) { return $true }
    $hint = if ($Default) { '[Y/n]' } else { '[y/N]' }
    $reply = Read-Host "$Question $hint"
    if (-not $reply) { return $Default }
    return $reply -match '^(y|yes)$'
}

# Runs a native command with its output discarded and says whether it succeeded. Windows PowerShell 5.1 turns what a
# native command writes to stderr into a terminating error under 'Stop' when it is redirected, so it runs under 'Continue'.
function Test-Native([scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command *> $null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-Running {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$Url/api/v1/health" | Out-Null; return $true } catch { return $false }
}

function Get-RunningVersion {
    try { return (Invoke-RestMethod -TimeoutSec 5 "$Url/api/v1/version").version } catch { return '' }
}

function Get-FileVersion([string]$Text) {
    if ($Text -match '(?m)^VERSION = "([^"]+)"') { return $Matches[1] }
    return ''
}

function Write-Log([string]$Message) {
    try {
        New-Item -ItemType Directory -Force $Data | Out-Null
        Add-Content -Path $Log -Value ('{0} {1}' -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'), $Message)
    } catch { Write-Host "Could not write $Log." }
}

# The Python steps run in the image: in the container when YAAP runs, else in a throwaway one on the current image.
# Standard input (the changelog) is passed on through $Input.
function Invoke-Step {
    $lines = @($input)
    $prefix = if (Test-Running) { @('exec', '-T', 'yaap') } else { @('run', '--rm', '--no-deps', '-T', 'yaap') }
    $command = @('compose') + $prefix + @('python', '-m', 'app.scripts.update') + $args
    if ($lines.Count -gt 0) { $lines | & docker @command } else { & docker @command }
}

function Wait-ForHealth {
    Write-Host "Waiting for YAAP to answer at $Url ..."
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-Running) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Get-JsonValue([string]$Key) {
    if (-not (Test-Path $State)) { return '' }
    $value = (Get-Content $State -Raw | ConvertFrom-Json).$Key
    if ($null -eq $value) { return '' }
    return [string]$value
}

# ---- Checks ----------------------------------------------------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail 'Docker is not installed. Install Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/'
}
if (-not (Test-Native { docker info })) { Fail 'Docker is installed but not running. Start Docker Desktop and run this script again.' }
if (-not (Test-Native { docker compose version })) { Fail "Docker Compose v2 is missing ('docker compose'). Update Docker Desktop." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'git is not installed.' }
if (-not ((Test-Path docker-compose.yml) -and (Test-Path backend/app/version.py) -and (Test-Path .git))) {
    Fail 'This is not the YAAP folder (a git checkout with docker-compose.yml).'
}
$changed = @(git status --porcelain --untracked-files=no)
if ($changed.Count -gt 0) {
    Write-Host 'These tracked files have changes:'
    $changed | ForEach-Object { Write-Host "  $_" }
    Fail 'Commit or undo them first. If you change YAAP itself, update with git instead of this script.'
}
$OldCommit = (git rev-parse HEAD).Trim()
$OldBranch = (git symbolic-ref --short -q HEAD)
if (-not $OldBranch) { $OldBranch = '' }

# ---- Going back ------------------------------------------------------------------------------------------------
# Old code, the previous images, the data when the database moved on, then start without building.
function Restore-Previous([string]$Commit, [string]$Branch, [string]$Backup) {
    # Check the backup before anything changes: without it a database that moved on cannot be restored.
    if ($Backup -and -not (Test-Path (Join-Path $Data "backups/$Backup"))) {
        Fail "The backup $Data/backups/$Backup does not exist, so nothing was changed. Restore the data from another backup as described in docs/usage.md (Backup and restore)."
    }
    Write-Host "Going back to $($Commit.Substring(0, [Math]::Min(12, $Commit.Length))) ..."
    if ($Branch) {
        git checkout $Branch
        if ($LASTEXITCODE -ne 0) { Fail "git could not check out $Branch." }
        git reset --keep $Commit
    } else {
        git checkout --detach $Commit
    }
    if ($LASTEXITCODE -ne 0) { Fail 'git could not go back to the old commit.' }
    foreach ($image in $Images) {
        if (Test-Native { docker image inspect "${image}:previous" }) { docker tag "${image}:previous" "${image}:local" }
    }
    if ($Backup) {
        docker compose run --rm --no-deps -T yaap python -m app.scripts.update rollback $Backup
        if ($LASTEXITCODE -ne 0) {
            Fail "The old version could not judge the database. Your backup is $Data/backups/$Backup; restore it as described in docs/usage.md (Backup and restore)."
        }
    }
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { Fail 'docker compose up failed; see the messages above.' }
    if (Wait-ForHealth) {
        Write-Host "YAAP is running again on the previous version: $Url" -ForegroundColor Green
        Write-Log "rolled back to $($Commit.Substring(0, [Math]::Min(12, $Commit.Length)))"
        Remove-Item $State -ErrorAction SilentlyContinue
    } else {
        Fail 'YAAP did not answer within three minutes after going back. See the log with: docker compose logs yaap'
    }
}

if ($Rollback) {
    if (Test-Path $State) {
        $backCommit = Get-JsonValue 'old_commit'; $backBranch = Get-JsonValue 'old_branch'; $backBackup = Get-JsonValue 'backup'
    } else {
        $last = if (Test-Path $Log) { Get-Content $Log | Where-Object { $_ -match ' updated ' } | Select-Object -Last 1 } else { $null }
        if (-not $last) { Fail "There is no update to go back from ($State and $Log have none)." }
        $backCommit = if ($last -match ' from commit ([0-9a-f]+)') { $Matches[1] } else { '' }
        $backBranch = if ($last -match ' branch (\S+)' -and $Matches[1] -ne '-') { $Matches[1] } else { '' }
        $backBackup = if ($last -match ' backup (yaap-backup-[0-9-]+\.zip)') { $Matches[1] } else { '' }
    }
    if (-not $backCommit) { Fail 'The previous version is not recorded.' }
    Write-Host "This goes back to commit $($backCommit.Substring(0, [Math]::Min(12, $backCommit.Length))). Work done since that update is lost when the database"
    Write-Host "moved on: it is restored from $(if ($backBackup) { $backBackup } else { 'no backup' }), and what it replaces stays in $Data/.restore-previous."
    if (-not (Ask 'Go back?' $false)) { Fail 'Nothing was changed.' }
    Restore-Previous $backCommit $backBranch $backBackup
    exit 0
}

# ---- Which version ---------------------------------------------------------------------------------------------
if (Test-Path $State) {
    Write-Host "An earlier update did not finish (it is recorded in $State)."
    if (Ask 'Go back to the version before it?' $true) {
        Restore-Previous (Get-JsonValue 'old_commit') (Get-JsonValue 'old_branch') (Get-JsonValue 'backup')
        exit 0
    }
    Fail "Run this script with -Rollback when you want to go back, or remove $State if the update is fine."
}

Write-Host 'Looking for a new version ...'
git fetch --tags --force --quiet origin
if ($LASTEXITCODE -ne 0) { Fail "git could not reach the remote ('git fetch'). Check your connection." }
$Current = Get-RunningVersion
if (-not $Current) { $Current = Get-FileVersion (Get-Content backend/app/version.py -Raw) }

if ($Channel -eq 'stable') {
    $tag = git tag --list 'v*' --sort=-v:refname | Where-Object { $_ -match '^v\d+\.\d+\.\d+$' } | Select-Object -First 1
    if (-not $tag) { Fail 'No release (a tag like v0.6.0) was found.' }
    $New = $tag.Substring(1); $Target = $tag; $ChangelogTo = $New
    if ([version]$New -le [version]$Current) { Write-Host "YAAP $Current is the newest release."; exit 0 }
} else {
    git fetch --quiet origin main
    if ($LASTEXITCODE -ne 0) { Fail 'git could not fetch main.' }
    $Target = 'origin/main'
    if ((git rev-parse HEAD) -eq (git rev-parse origin/main)) { Write-Host 'YAAP is on the newest commit of main.'; exit 0 }
    $New = Get-FileVersion ((git show origin/main:backend/app/version.py) -join "`n")
    $ChangelogTo = 'main'
}

# ---- Show what comes -------------------------------------------------------------------------------------------
Write-Host ''
Write-Host "Current version: $Current"
Write-Host "New version:     $New ($Channel, $(git rev-parse --short $Target))"
Write-Host ''
$changelog = git show "${Target}:CHANGELOG.md"
$formatted = $changelog | Invoke-Step changelog $Current $ChangelogTo
if ($LASTEXITCODE -ne 0) {
    Write-Host 'The changelog could not be formatted; this is the start of CHANGELOG.md of the new version:'
    $changelog | Select-Object -First 60
} else {
    $formatted | ForEach-Object { if ($_ -match '^Action needed') { Write-Host $_ -ForegroundColor Yellow } else { Write-Host $_ } }
}
Write-Host ''
if (-not (Ask "Update YAAP from $Current to $New?" $true)) { Fail 'Nothing was changed.' }

# ---- Busy? -----------------------------------------------------------------------------------------------------
if (Test-Running) {
    $working = @(docker compose exec -T yaap python -m app.scripts.update busy)
    if ($LASTEXITCODE -ne 0) { Fail 'YAAP could not say what is working.' }
    if ($working.Count -gt 0) {
        Write-Host 'YAAP is working on:'
        $working | ForEach-Object { Write-Host "  $_" }
        Write-Host 'Updating restarts YAAP and cuts this work off.'
        if ($Yes) { Fail 'Run it again when this is done, or without -Yes to go on anyway.' }
        if (-not (Ask 'Update anyway?' $false)) { Fail 'Nothing was changed.' }
    }
}

# ---- Backup ----------------------------------------------------------------------------------------------------
Write-Host 'Making a backup ...'
$Backup = (Invoke-Step backup | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { Fail 'The backup failed; nothing was changed.' }
if ($Backup -notmatch '^yaap-backup-\d{8}-\d{6}\.zip$') { Fail 'The backup step gave no backup name; nothing was changed.' }
Write-Host "Backup: $Backup"

# ---- Keep the old version --------------------------------------------------------------------------------------
foreach ($image in $Images) {
    if (Test-Native { docker image inspect "${image}:local" }) { docker tag "${image}:local" "${image}:previous" }
}
New-Item -ItemType Directory -Force $Data | Out-Null
$record = [ordered]@{
    old_commit = $OldCommit; old_branch = $OldBranch; old_version = $Current; new_version = $New
    backup = $Backup; channel = $Channel
}
# UTF-8 without a byte order mark.
[System.IO.File]::WriteAllText((Join-Path (Get-Location) $State), ($record | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))

# ---- New code, build and start ---------------------------------------------------------------------------------
if ($Channel -eq 'stable') {
    git checkout --detach $Target
} else {
    git checkout main
    if ($LASTEXITCODE -eq 0) { git pull --ff-only origin main }
}
if ($LASTEXITCODE -ne 0) {
    Write-Host 'git could not check out the new version. Nothing was built.' -ForegroundColor Red
    if (Ask "Go back to $Current?" $true) {
        Restore-Previous $OldCommit $OldBranch $Backup
        exit 1
    }
    Fail 'YAAP stays as it is. Go back later with: scripts\update.ps1 -Rollback'
}
$env:YAAP_COMMIT = (git rev-parse --short HEAD).Trim()
$env:YAAP_CHANNEL = $Channel
$env:CLI_REFRESH = (Get-Date).ToString('yyyy-MM')
Write-Host "Building and starting YAAP $New. This takes a few minutes."

$ok = $false
docker compose up -d --build
if ($LASTEXITCODE -eq 0 -and (Wait-ForHealth)) {
    $reported = Get-RunningVersion
    if ($reported -eq $New) { $ok = $true } else { Write-Host "YAAP answers, but reports version '$reported' instead of $New." -ForegroundColor Red }
}

if ($ok) {
    $branchField = if ($OldBranch) { $OldBranch } else { '-' }
    Write-Log "updated $Current -> $New from commit $OldCommit branch $branchField to $($env:YAAP_COMMIT) channel $Channel backup $Backup"
    Remove-Item $State -ErrorAction SilentlyContinue
    Test-Native { docker image prune -f --filter dangling=true --filter label=dev.yaap=true } | Out-Null
    Write-Host ''
    Write-Host "YAAP is updated: $Current -> $New ($Url)" -ForegroundColor Green
    Write-Host 'The version before it stays as the :previous images; scripts\update.ps1 -Rollback goes back.'
    $summary = $changelog | Invoke-Step changelog $Current $ChangelogTo
    $cut = [Array]::IndexOf(@($summary), 'What changed:')
    if ($LASTEXITCODE -eq 0 -and $cut -gt 0) { $summary | Select-Object -First $cut | ForEach-Object { Write-Host $_ } }
    exit 0
}

Write-Host ''
Write-Host 'The update did not work. The last 40 lines of the log:' -ForegroundColor Red
docker compose logs --no-color --tail 40 yaap
Write-Host ''
if (Ask "Go back to $Current?" $true) {
    Restore-Previous $OldCommit $OldBranch $Backup
    exit 1
}
Fail "YAAP stays on $New. Go back later with: scripts\update.ps1 -Rollback"
