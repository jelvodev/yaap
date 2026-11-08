"""The PowerShell script that lets YAAP work on this Windows computer over SSH.

The user runs it as administrator. It installs and starts OpenSSH Server, creates the local user `yaap` that signs in
with YAAP's key only, gives it the chosen rights, and opens port 22 only for Docker's networks. YAAP itself never
changes the computer: it shows the script, the user reads and runs it.
"""

STANDARD = "standard"
ADMINISTRATOR = "administrator"
# Where Docker Desktop's containers come from when they reach host.docker.internal (its default networks).
DOCKER_NETWORKS = "172.16.0.0/12,192.168.65.0/24"


def ps_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def windows_setup_script(public_key: str, rights: str, folders: list[str], user: str = "yaap") -> str:
    """The script for the chosen rights; folders get modify rights for a standard user."""
    if rights not in (STANDARD, ADMINISTRATOR):
        raise ValueError("rights is standard or administrator")
    key = ps_string(public_key.strip())
    name = ps_string(user)
    lines = [
        "# Lets YAAP work on this computer over SSH. Run in PowerShell as administrator.",
        f"# Rights: {rights}. Read it before you run it.",
        "$ErrorActionPreference = 'Stop'",
        f"$user = {name}",
        f"$publicKey = {key}",
        "",
        "# 1. OpenSSH Server, started now and with Windows.",
        "$server = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'",
        "if ($server.State -ne 'Installed') { Add-WindowsCapability -Online -Name $server.Name | Out-Null }",
        "Set-Service -Name sshd -StartupType Automatic",
        "Start-Service sshd",
        "",
        "# 2. The user, with a long random password nobody knows: it signs in with YAAP's key only.",
        "if (-not (Get-LocalUser -Name $user -ErrorAction SilentlyContinue)) {",
        "  $bytes = New-Object byte[] 36",
        "  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)",
        "  $password = ConvertTo-SecureString ([Convert]::ToBase64String($bytes)) -AsPlainText -Force",
        "  New-LocalUser -Name $user -Password $password -PasswordNeverExpires -AccountNeverExpires -Description 'YAAP agent' | Out-Null",
        "}",
        "$sshdConfig = Join-Path $env:ProgramData 'ssh\\sshd_config'",
        "if (-not (Select-String -Path $sshdConfig -Pattern \"^Match User $user$\" -Quiet)) {",
        "  Add-Content -Path $sshdConfig -Value \"`r`nMatch User $user`r`n    PasswordAuthentication no`r`n    AuthorizedKeysFile __PROGRAMDATA__/ssh/${user}_authorized_keys`r`n\"",
        "}",
        "",
    ]
    if rights == ADMINISTRATOR:
        lines += [
            "# 3. Administrator: Windows OpenSSH reads the keys of administrators from one shared file.",
            "$administrators = Get-LocalGroup -SID 'S-1-5-32-544'",
            "if (-not (Get-LocalGroupMember -Group $administrators | Where-Object Name -like \"*\\$user\")) {",
            "  Add-LocalGroupMember -Group $administrators -Member $user",
            "}",
            "$keys = Join-Path $env:ProgramData 'ssh\\administrators_authorized_keys'",
            "if (-not (Test-Path $keys) -or -not (Select-String -Path $keys -SimpleMatch $publicKey -Quiet)) {",
            "  Add-Content -Path $keys -Value $publicKey -Encoding ascii",
            "}",
            "icacls.exe $keys /inheritance:r /grant '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null",
        ]
    else:
        lines += [
            "# 3. Standard user: its key in a file only administrators can change, and it may change only the listed folders.",
            "$keys = Join-Path $env:ProgramData \"ssh\\${user}_authorized_keys\"",
            "if (-not (Test-Path $keys) -or -not (Select-String -Path $keys -SimpleMatch $publicKey -Quiet)) {",
            "  Add-Content -Path $keys -Value $publicKey -Encoding ascii",
            "}",
            "icacls.exe $keys /inheritance:r /grant '*S-1-5-32-544:F' '*S-1-5-18:F' \"${user}:R\" | Out-Null",
        ]
        for folder in folders:
            lines.append(f"icacls.exe {ps_string(folder)} /grant \"${{user}}:(OI)(CI)M\" | Out-Null")
    lines += [
        "",
        "# 4. Port 22 only for Docker's networks, not for the local network.",
        "Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule",
        "Remove-NetFirewallRule -Name 'YAAP-SSH' -ErrorAction SilentlyContinue",
        f"New-NetFirewallRule -Name 'YAAP-SSH' -DisplayName 'YAAP SSH (Docker only)' -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress {DOCKER_NETWORKS} -Action Allow | Out-Null",
        "Restart-Service sshd",
        "Write-Host \"Done. In YAAP, press Test on this target and confirm the host key it shows.\"",
    ]
    return "\r\n".join(lines) + "\r\n"
