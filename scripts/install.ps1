param([switch]$DotSource)

function Test-OpenSSHEnabled {
    $svc = Get-Service -Name "sshd" -ErrorAction SilentlyContinue
    return ($null -ne $svc -and $svc.Status -eq "Running")
}

function Enable-OpenSSHServer {
    param([Parameter(Mandatory)][string]$StatePath)

    $alreadyEnabled = Test-OpenSSHEnabled
    if (-not $alreadyEnabled) {
        # (WHY: Add-WindowsCapability/Enable-WindowsOptionalFeature is the
        # documented in-box way to install OpenSSH.Server on Windows
        # 10/11 and Server, keeping this offline-friendly unless WSUS
        # blocks the feature - see ASSUMPTIONS.md.)
        Enable-WindowsOptionalFeature -Online -FeatureName "OpenSSH.Server" -NoRestart
        Start-Service sshd
        Set-Service -Name sshd -StartupType Automatic
    }

    $state = @{ ssh_was_already_enabled = $alreadyEnabled; installed_at = (Get-Date).ToUniversalTime().ToString("o") }
    $state | ConvertTo-Json | Set-Content -Path $StatePath
}

function Add-EngineerKey {
    param(
        [Parameter(Mandatory)][string]$PublicKeyPath,
        [Parameter(Mandatory)][string]$AuthorizedKeysPath,
        [string]$PublicKeyContent
    )
    if (-not $PublicKeyContent) {
        $PublicKeyContent = (Get-Content $PublicKeyPath -Raw).Trim()
    }
    $existing = @()
    if (Test-Path $AuthorizedKeysPath) {
        $existing = Get-Content $AuthorizedKeysPath
    }
    if ($existing -notcontains $PublicKeyContent) {
        Add-Content -Path $AuthorizedKeysPath -Value $PublicKeyContent
    }
}

function Add-ScopedFirewallRule {
    param([Parameter(Mandatory)][string]$AllowedIP)

    $ruleName = "RemoteFleetAgent-SSH"
    if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -Name $ruleName
    }
    New-NetFirewallRule -Name $ruleName -DisplayName "Remote Fleet Agent (SSH, scoped)" `
        -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress $AllowedIP -Action Allow
}

function Register-SelfDestructTask {
    param(
        [Parameter(Mandatory)][string]$UninstallScriptPath,
        [int]$DaysUntilExpiry = 3
    )

    $taskName = "RemoteFleetAgent-SelfDestruct"
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        # (WHY: re-running install.ps1 on a return visit must reset the
        # 3-day clock, not add a second competing task.)
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddDays($DaysUntilExpiry)
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$UninstallScriptPath`""
    Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action `
        -User "SYSTEM" -RunLevel Highest
}

function Invoke-Install {
    param(
        [Parameter(Mandatory)][string]$AllowedIP,
        [Parameter(Mandatory)][string]$PublicKeyPath,
        [int]$DaysUntilExpiry = 3
    )

    if ($AllowedIP -notmatch '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$') {
        throw "AllowedIP '$AllowedIP' is not a valid IPv4 address"
    }
    if (-not (Test-Path $PublicKeyPath)) {
        throw "PublicKeyPath '$PublicKeyPath' does not exist"
    }

    $stateDir = "C:\ProgramData\remote-fleet-agent"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $statePath = Join-Path $stateDir "state.json"

    Enable-OpenSSHServer -StatePath $statePath

    $publicKeyContent = (Get-Content $PublicKeyPath -Raw).Trim()
    Add-EngineerKey -PublicKeyPath $PublicKeyPath `
        -AuthorizedKeysPath "$env:ProgramData\ssh\administrators_authorized_keys" `
        -PublicKeyContent $publicKeyContent

    # (WHY: record exactly which key line was added so uninstall can remove
    # only that line later, never the whole shared authorized_keys file -
    # see Remove-EngineerKey in uninstall.ps1.)
    $state = Get-Content $statePath | ConvertFrom-Json
    $state | Add-Member -NotePropertyName ssh_key_added -NotePropertyValue $publicKeyContent -Force
    $state | ConvertTo-Json | Set-Content -Path $statePath

    Add-ScopedFirewallRule -AllowedIP $AllowedIP

    # (WHY: the self-destruct task runs days later, almost certainly after
    # the USB stick install.ps1 was launched from has been unplugged - it
    # must reference a LOCAL copy of uninstall.ps1, never $PSScriptRoot,
    # or the scheduled task silently finds nothing to run and the whole
    # dead-man's-switch does nothing.)
    $localUninstallPath = Join-Path $stateDir "uninstall.ps1"
    Copy-Item -Path (Join-Path $PSScriptRoot "uninstall.ps1") -Destination $localUninstallPath -Force
    Register-SelfDestructTask -UninstallScriptPath $localUninstallPath `
        -DaysUntilExpiry $DaysUntilExpiry

    Write-Output "Install complete. Self-destructs in $DaysUntilExpiry day(s) unless re-run."
}

if ($DotSource) { return }
Invoke-Install @args
