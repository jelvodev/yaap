# Check YAAP's pinned packages for known vulnerabilities: the frontend's npm packages, the Python lock files of YAAP
# and the sandbox helper, the pinned Claude Code and Codex, and the npm packages installed globally in the images.
# Debian's own packages are not checked. Needs npm and uv (uvx). Exits non-zero when a check finds something or cannot run. Run it in PowerShell
# from the YAAP folder:
#   powershell -ExecutionPolicy Bypass -File scripts\audit.ps1
$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')
$PipAudit = 'pip-audit==2.10.1'

function Fail([string]$Message) {
    Write-Host "Audit stopped: $Message" -ForegroundColor Red
    exit 2
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail 'npm is not installed: https://nodejs.org/' }
if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) { Fail 'uv is not installed: https://docs.astral.sh/uv/' }

# The global npm packages of the images, by the Dockerfile ARG that pins their version.
$GlobalPackages = @(
    @{ Dockerfile = 'docker/sandbox/Dockerfile'; Arg = 'PLAYWRIGHT_MCP_VERSION'; Package = '@playwright/mcp' },
    @{ Dockerfile = 'docker/sandbox/Dockerfile'; Arg = 'PYRIGHT_VERSION'; Package = 'pyright' },
    @{
        Dockerfile = 'docker/sandbox/Dockerfile'; Arg = 'TYPESCRIPT_LANGUAGE_SERVER_VERSION'
        Package = 'typescript-language-server'
    },
    @{ Dockerfile = 'docker/sandbox/Dockerfile'; Arg = 'TYPESCRIPT_VERSION'; Package = 'typescript' }
)

$Results = New-Object System.Collections.Generic.List[string]
$Failed = $false
function Record([string]$Name, [int]$ExitCode) {
    if ($ExitCode -eq 0) {
        $Results.Add("ok        $Name")
    } else {
        $Results.Add("FAILED    $Name (findings, or the check could not run)")
        $script:Failed = $true
    }
}

# The default value of an ARG in a Dockerfile.
function Get-ArgValue([string]$Dockerfile, [string]$Name) {
    foreach ($line in Get-Content $Dockerfile) {
        if ($line -match "^ARG $Name=(\S+)") { return $Matches[1] }
    }
    Fail "no ARG $Name in $Dockerfile"
}

Write-Host '== Frontend (frontend/package-lock.json)'
Push-Location frontend
npm audit
Record 'frontend npm packages' $LASTEXITCODE
Pop-Location

Write-Host ''
Write-Host '== Claude Code and Codex (tools/package-lock.json)'
Push-Location tools
npm audit
Record 'Claude Code and Codex npm packages' $LASTEXITCODE
Pop-Location

foreach ($lock in 'backend/requirements.lock', 'docker/sandbox-helper/requirements.lock') {
    Write-Host ''
    Write-Host "== Python ($lock)"
    uvx $PipAudit --require-hashes --disable-pip -r $lock
    Record "Python packages in $lock" $LASTEXITCODE
}

Write-Host ''
Write-Host '== Global npm packages in the images'
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("yaap-audit-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $deps = [ordered]@{}
    foreach ($entry in $GlobalPackages) {
        $version = Get-ArgValue $entry.Dockerfile $entry.Arg
        Write-Host "$($entry.Package)@$version ($($entry.Dockerfile))"
        $deps[$entry.Package] = $version
    }
    $manifest = [ordered]@{ name = 'yaap-image-globals'; private = $true; dependencies = $deps }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText((Join-Path $work 'package.json'), ($manifest | ConvertTo-Json), $utf8)
    # A global install has no lock file, so this resolves the dependencies as a build today would.
    Push-Location $work
    npm install --package-lock-only --ignore-scripts --no-fund --no-audit | Out-Null
    $status = $LASTEXITCODE
    if ($status -eq 0) {
        npm audit
        $status = $LASTEXITCODE
    }
    Pop-Location
    Record 'global npm packages in the images' $status
} finally {
    Remove-Item -Recurse -Force $work
}

Write-Host ''
Write-Host '== Summary'
foreach ($line in $Results) {
    $color = if ($line.StartsWith('ok')) { 'Green' } else { 'Red' }
    Write-Host $line -ForegroundColor $color
}
if ($Failed) { exit 1 }
exit 0
