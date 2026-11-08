# Set up and start YAAP on Windows: creates .env with a new master key (once), builds and starts the containers,
# waits until YAAP answers and asks for YAAP's password when none is set yet. Run it in PowerShell from the YAAP folder:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')
$Url = 'http://127.0.0.1:8080'

function Fail([string]$Message) {
    Write-Host "Setup stopped: $Message" -ForegroundColor Red
    exit 1
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail 'Docker is not installed. Install Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/'
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail 'Docker is installed but not running. Start Docker Desktop and run this script again.' }
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker Compose v2 is missing ('docker compose'). Update Docker Desktop." }

if (Test-Path .env) {
    Write-Host 'Using the existing .env (it is never changed by this script).'
} else {
    if (-not (Test-Path .env.example)) { Fail '.env.example is missing; is this the YAAP folder?' }
    # A Fernet key: 32 random bytes as URL-safe base64.
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $key = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')

    $lines = @(Get-Content .env.example)
    if ($lines -match '^YAAP_MASTER_KEY=') {
        $lines = $lines -replace '^YAAP_MASTER_KEY=.*', "YAAP_MASTER_KEY=$key"
    } else {
        $lines += '', "YAAP_MASTER_KEY=$key"
    }
    # UTF-8 without a byte order mark, which Docker Compose reads on every PowerShell version.
    [System.IO.File]::WriteAllLines((Join-Path (Get-Location) '.env'), [string[]]$lines, (New-Object System.Text.UTF8Encoding $false))
    Write-Host 'Created .env with a new master key.'
    Write-Host ''
    Write-Host '  IMPORTANT: keep a copy of YAAP_MASTER_KEY from .env somewhere safe, outside the data folder' -ForegroundColor Yellow
    Write-Host '  (a password manager is a good place). Without it, a backup''s keys and secrets cannot be read.' -ForegroundColor Yellow
    Write-Host ''
}

Write-Host 'Building and starting YAAP. The first build downloads a few GB and takes several minutes.'
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Fail 'docker compose up failed; see the messages above.' }

Write-Host "Waiting for YAAP to answer at $Url ..."
for ($i = 0; $i -lt 90; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$Url/api/v1/health" | Out-Null
    } catch {
        Start-Sleep -Seconds 2
        continue
    }
    Write-Host ''
    Write-Host "YAAP is running: $Url" -ForegroundColor Green
    $state = Invoke-RestMethod -TimeoutSec 5 "$Url/api/v1/auth/state"
    if (-not $state.password_set) {
        Write-Host ''
        Write-Host 'YAAP asks for a password in the browser. Choose it now (at least 10 characters),'
        Write-Host 'or press Enter to set it on the first visit in the browser instead.'
        $password = ''
        while ($true) {
            $password = [System.Net.NetworkCredential]::new('', (Read-Host 'Password' -AsSecureString)).Password
            if (-not $password) { break }
            $again = [System.Net.NetworkCredential]::new('', (Read-Host 'The same password again' -AsSecureString)).Password
            if ($password -ceq $again) { break }
            Write-Host 'The two passwords differ; try again.'
        }
        if ($password) {
            # On standard input, so the password never shows on a command line.
            $password | docker compose exec -T yaap python -m app.scripts.set_password --stdin
            if ($LASTEXITCODE -ne 0) { Write-Host 'The password was not set; set it in the browser instead.' }
        } else {
            Write-Host "Open $Url and set the password there first."
        }
        Remove-Variable password, again -ErrorAction SilentlyContinue
    }
    Write-Host ''
    Write-Host 'Next: connect a model. Either'
    Write-Host '  - sign in to Claude Code with your Claude subscription:   docker compose exec -it yaap claude auth login'
    Write-Host '  - sign in to Codex with your ChatGPT plan:                docker compose exec -it yaap codex login --device-auth'
    Write-Host '  - or add an API key (Anthropic, OpenAI) or an Ollama address in Settings > Connections.'
    Write-Host 'See docs\install.md for details.'
    exit 0
}
Fail 'YAAP did not answer within three minutes. See the log with: docker compose logs yaap'
