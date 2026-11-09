param([switch]$DotSource)

function Test-OpenSSHEnabled {
    $svc = Get-Service -Name "sshd" -ErrorAction SilentlyContinue
    return ($null -ne $svc -and $svc.Status -eq "Running")
}

function Enable-OpenSSHServer {
    param(
        [Parameter(Mandatory)][string]$StatePath,
        [string]$LocalSourcePath,
        [int]$MaxAttempts = 3,
        [int]$RetryDelaySeconds = 5
    )

    $alreadyEnabled = Test-OpenSSHEnabled
    if (-not $alreadyEnabled) {
        # (WHY: OpenSSH Server is a Windows Capability, not an Optional
        # Feature - Add-WindowsCapability is the correct cmdlet on Windows
        # 10/11 and Server. Enable-WindowsOptionalFeature silently no-ops
        # on this capability name instead of erroring, which is why this
        # was wrong for a while without being caught - see ASSUMPTIONS.md
        # on WSUS potentially blocking the Windows Update fetch.)
        $capabilityName = "OpenSSH.Server~~~~0.0.1.0"
        $lastError = $null

        # (WHY: live testing (2026-11-03) found Add-WindowsCapability can
        # hang for a long time with zero console feedback when the capability
        # is ALREADY installed and this machine's path to Windows Update is
        # flaky - the add call still tries to contact WU to re-verify, even
        # though there's nothing to do. Checking first and skipping the add
        # entirely when it's already Installed avoids that call completely
        # in the common "already done" case, which is exactly what made the
        # script look hung with no way to tell if it was working.)
        Write-Output "Checking OpenSSH.Server capability..."
        $capability = Get-WindowsCapability -Online -Name $capabilityName
        $installed = ($capability.State -eq "Installed")

        if ($installed) {
            Write-Output "OpenSSH.Server capability already installed - skipping install."
        } else {
            # (WHY: live testing also hit a transient Add-WindowsCapability
            # failure - Error 87 "capability name not recognized" - that
            # cleared on an immediate retry of the exact same command. A
            # single attempt is not reliable; retry a few times before giving
            # up. -Source lets an engineer point at a local sxs folder from
            # install media when the client network blocks Windows
            # Update/WSUS for FOD content - still Microsoft-signed content,
            # just a different source, never a bundled third-party binary.)
            Write-Output "Installing OpenSSH.Server capability - this can take a minute or two on first install..."
            for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
                try {
                    if ($LocalSourcePath) {
                        Add-WindowsCapability -Online -Name $capabilityName -Source $LocalSourcePath -ErrorAction Stop
                    } else {
                        Add-WindowsCapability -Online -Name $capabilityName -ErrorAction Stop
                    }
                } catch {
                    $lastError = $_
                }

                $capability = Get-WindowsCapability -Online -Name $capabilityName
                if ($capability.State -eq "Installed") {
                    $installed = $true
                    break
                }

                Write-Output "Attempt $attempt of $MaxAttempts did not complete (state: $($capability.State))."
                if ($attempt -lt $MaxAttempts) {
                    Start-Sleep -Seconds $RetryDelaySeconds
                }
            }
        }

        if (-not $installed) {
            $sourceHint = if ($LocalSourcePath) { "local source '$LocalSourcePath'" } else { "Windows Update" }
            $errorDetail = if ($lastError) { " Last error: $($lastError.Exception.Message)" } else { "" }
            throw "OpenSSH.Server capability did not install after $MaxAttempts attempt(s) via $sourceHint " +
                "- final state '$($capability.State)'.$errorDetail " +
                "If this network blocks Windows Update/WSUS for Feature-on-Demand content, re-run with " +
                "-LocalSourcePath pointing at a matching sxs folder from Windows install media. If the " +
                "state stays NotPresent with DownloadSize/InstallSize both 0, this build may not have a " +
                "published OpenSSH package at all (seen on Insider/Canary builds) - try a different machine."
        }

        Write-Output "Starting sshd service..."
        Start-Service sshd -ErrorAction Stop
        Set-Service -Name sshd -StartupType Automatic -ErrorAction Stop

        # (WHY: a service can report started and then exit immediately -
        # e.g. host-key generation failing - so confirm it is still running
        # a moment later rather than trusting Start-Service alone.)
        Start-Sleep -Seconds 2
        $svc = Get-Service -Name sshd
        if ($svc.Status -ne "Running") {
            throw "sshd service started but is now '$($svc.Status)', not Running - check Windows Event " +
                "Viewer > Applications and Services Logs > OpenSSH for the cause."
        }
        Write-Output "sshd service is running."
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
        [int]$DaysUntilExpiry = 3,
        [string]$LocalSourcePath
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

    Enable-OpenSSHServer -StatePath $statePath -LocalSourcePath $LocalSourcePath

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
