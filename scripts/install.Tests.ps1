BeforeAll {
    # DEVIATION: these Windows-only cmdlets (OpenSSH / WindowsOptionalFeature /
    # NetFirewall / ScheduledTask families) do not exist on this macOS host,
    # and Pester 6's `Mock` requires the target command to already resolve
    # before it can wrap it (it throws "Could not find Command X" otherwise).
    # Define a harmless stub only when the real cmdlet isn't present, so Mock
    # has something to replace. On a real Windows host these guards are
    # no-ops and the genuine cmdlets are used untouched.
    $stubCmdlets = @{
        'Get-Service'                   = { param($Name, $ErrorAction) }
        'Start-Service'                 = { param($Name) }
        'Set-Service'                   = { param($Name, $StartupType) }
        'Add-WindowsCapability'          = { param([switch]$Online, $Name, $Source, $ErrorAction) }
        'Get-WindowsCapability'          = { param([switch]$Online, $Name) }
        'Get-NetFirewallRule'            = { param($Name, $ErrorAction) }
        'Remove-NetFirewallRule'         = { param($Name) }
        'New-NetFirewallRule'            = { param($Name, $DisplayName, $Direction, $Protocol, $LocalPort, $RemoteAddress, $Action) }
        'Get-ScheduledTask'              = { param($TaskName, $ErrorAction) }
        'Unregister-ScheduledTask'       = { param($TaskName, [switch]$Confirm) }
        'New-ScheduledTaskTrigger'       = { param([switch]$Once, $At) }
        'New-ScheduledTaskAction'        = { param($Execute, $Argument) }
        'Register-ScheduledTask'         = { param($TaskName, $Trigger, $Action, $User, $RunLevel) }
    }
    foreach ($name in $stubCmdlets.Keys) {
        if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
            Set-Item -Path "function:script:$name" -Value $stubCmdlets[$name]
        }
    }

    . "$PSScriptRoot/install.ps1" -DotSource
}

Describe "Test-OpenSSHEnabled / Enable-OpenSSHServer" {
    It "enables OpenSSH and records false when not previously enabled" {
        $script:getServiceCalls = 0
        Mock Get-Service {
            $script:getServiceCalls++
            if ($script:getServiceCalls -eq 1) { return $null }
            return [PSCustomObject]@{ Status = "Running" }
        } -ParameterFilter { $Name -eq "sshd" }
        Mock Add-WindowsCapability { } -Verifiable
        $script:capCheckCalls = 0
        Mock Get-WindowsCapability {
            $script:capCheckCalls++
            if ($script:capCheckCalls -eq 1) { [PSCustomObject]@{ State = "NotPresent" } }
            else { [PSCustomObject]@{ State = "Installed" } }
        }
        Mock Start-Service { }
        Mock Set-Service { }
        Mock Start-Sleep { }
        $statePath = Join-Path $TestDrive "state.json"

        Enable-OpenSSHServer -StatePath $statePath

        Should -InvokeVerifiable
        $state = Get-Content $statePath | ConvertFrom-Json
        $state.ssh_was_already_enabled | Should -Be $false
    }

    It "skips Add-WindowsCapability entirely when the capability is already installed" {
        # (WHY: live testing (2026-11-03) found this was the actual cause of
        # the script appearing to hang with no feedback - it kept calling
        # Add-WindowsCapability to re-verify a capability that was already
        # installed, and that online re-check call could sit for a long time
        # with nothing printed. Checking first and skipping entirely when
        # already Installed removes that unnecessary call in the common case.)
        $script:skipSvcCalls = 0
        Mock Get-Service {
            $script:skipSvcCalls++
            if ($script:skipSvcCalls -eq 1) { return $null }
            return [PSCustomObject]@{ Status = "Running" }
        } -ParameterFilter { $Name -eq "sshd" }
        Mock Get-WindowsCapability { [PSCustomObject]@{ State = "Installed" } }
        Mock Add-WindowsCapability { }
        Mock Start-Service { }
        Mock Set-Service { }
        Mock Start-Sleep { }
        $statePath = Join-Path $TestDrive "state10.json"

        Enable-OpenSSHServer -StatePath $statePath

        Should -Invoke Add-WindowsCapability -Times 0
        Should -Invoke Start-Service -Times 1
    }

    It "does not re-enable when already running, records true" {
        Mock Get-Service { [PSCustomObject]@{ Status = "Running" } } -ParameterFilter { $Name -eq "sshd" }
        Mock Add-WindowsCapability { }
        $statePath = Join-Path $TestDrive "state2.json"

        Enable-OpenSSHServer -StatePath $statePath

        Should -Invoke Add-WindowsCapability -Times 0
        $state = Get-Content $statePath | ConvertFrom-Json
        $state.ssh_was_already_enabled | Should -Be $true
    }

    It "retries and succeeds if the first attempt fails transiently" {
        # (WHY: live testing against a real Windows machine (2026-11-03) hit
        # exactly this - Add-WindowsCapability errored on attempt 1 with
        # "Error 87: capability name not recognized", then the identical
        # command succeeded on an immediate retry with no other change. A
        # single attempt is not reliable; install.ps1 must recover from this
        # automatically instead of requiring the engineer to notice and
        # retry by hand.)
        $script:retrySvcCalls = 0
        Mock Get-Service {
            $script:retrySvcCalls++
            if ($script:retrySvcCalls -eq 1) { return $null }
            return [PSCustomObject]@{ Status = "Running" }
        } -ParameterFilter { $Name -eq "sshd" }
        $script:capabilityAttempt = 0
        Mock Add-WindowsCapability {
            $script:capabilityAttempt++
            if ($script:capabilityAttempt -eq 1) { throw "Error 87: capability name not recognized" }
        }
        Mock Get-WindowsCapability {
            if ($script:capabilityAttempt -lt 2) { [PSCustomObject]@{ State = "NotPresent" } }
            else { [PSCustomObject]@{ State = "Installed" } }
        }
        Mock Start-Service { }
        Mock Set-Service { }
        Mock Start-Sleep { }
        $statePath = Join-Path $TestDrive "state7.json"

        Enable-OpenSSHServer -StatePath $statePath -MaxAttempts 3 -RetryDelaySeconds 0

        Should -Invoke Add-WindowsCapability -Times 2
        $state = Get-Content $statePath | ConvertFrom-Json
        $state.ssh_was_already_enabled | Should -Be $false
    }

    It "passes -LocalSourcePath through to Add-WindowsCapability when given" {
        $script:localSrcSvcCalls = 0
        Mock Get-Service {
            $script:localSrcSvcCalls++
            if ($script:localSrcSvcCalls -eq 1) { return $null }
            return [PSCustomObject]@{ Status = "Running" }
        } -ParameterFilter { $Name -eq "sshd" }
        Mock Add-WindowsCapability { } -Verifiable -ParameterFilter { $Source -eq "D:\sxs" }
        $script:localSrcCapCalls = 0
        Mock Get-WindowsCapability {
            $script:localSrcCapCalls++
            if ($script:localSrcCapCalls -eq 1) { [PSCustomObject]@{ State = "NotPresent" } }
            else { [PSCustomObject]@{ State = "Installed" } }
        }
        Mock Start-Service { }
        Mock Set-Service { }
        Mock Start-Sleep { }
        $statePath = Join-Path $TestDrive "state8.json"

        Enable-OpenSSHServer -StatePath $statePath -LocalSourcePath "D:\sxs"

        Should -InvokeVerifiable
    }

    It "throws if sshd starts but is not Running shortly afterwards" {
        # (WHY: a service can report started and then exit immediately, e.g.
        # host-key generation failing - Start-Service succeeding is not
        # proof the service is actually up.)
        $script:crashSvcCalls = 0
        Mock Get-Service {
            $script:crashSvcCalls++
            if ($script:crashSvcCalls -eq 1) { return $null }
            return [PSCustomObject]@{ Status = "Stopped" }
        } -ParameterFilter { $Name -eq "sshd" }
        Mock Add-WindowsCapability { }
        Mock Get-WindowsCapability { [PSCustomObject]@{ State = "Installed" } }
        Mock Start-Service { }
        Mock Set-Service { }
        Mock Start-Sleep { }
        $statePath = Join-Path $TestDrive "state9.json"

        { Enable-OpenSSHServer -StatePath $statePath } | Should -Throw "*not Running*"
    }

    It "throws if the capability never reports Installed after all retry attempts" {
        # (WHY: Add-WindowsCapability can return without throwing even when
        # it didn't actually install anything, e.g. no reachable Windows
        # Update/WSUS source for the payload - this is the real-world bug
        # that made install.ps1 silently report success on a machine with
        # a blocked FOD source. Verify-then-throw catches it instead.)
        Mock Get-Service { $null } -ParameterFilter { $Name -eq "sshd" }
        Mock Add-WindowsCapability { }
        Mock Get-WindowsCapability { [PSCustomObject]@{ State = "NotPresent" } }
        Mock Start-Service { }
        Mock Set-Service { }
        Mock Start-Sleep { }
        $statePath = Join-Path $TestDrive "state6.json"

        { Enable-OpenSSHServer -StatePath $statePath -MaxAttempts 2 -RetryDelaySeconds 0 } | Should -Throw "*did not install*"
        Should -Invoke Add-WindowsCapability -Times 2
        Should -Invoke Start-Service -Times 0
    }
}

Describe "Add-EngineerKey" {
    It "appends a new key" {
        $authKeys = Join-Path $TestDrive "authorized_keys"
        Set-Content $authKeys ""
        Add-EngineerKey -PublicKeyPath (Join-Path $TestDrive "key.pub") -AuthorizedKeysPath $authKeys `
            -PublicKeyContent "ssh-ed25519 AAAA... engineer@laptop"
        (Get-Content $authKeys) | Should -Contain "ssh-ed25519 AAAA... engineer@laptop"
    }

    It "does not duplicate an already-present key" {
        $authKeys = Join-Path $TestDrive "authorized_keys2"
        Set-Content $authKeys "ssh-ed25519 AAAA... engineer@laptop"
        Add-EngineerKey -PublicKeyPath (Join-Path $TestDrive "key.pub") -AuthorizedKeysPath $authKeys `
            -PublicKeyContent "ssh-ed25519 AAAA... engineer@laptop"
        (Get-Content $authKeys | Measure-Object -Line).Lines | Should -Be 1
    }
}

Describe "Add-ScopedFirewallRule" {
    It "creates a new rule when none exists" {
        Mock Get-NetFirewallRule { $null }
        Mock New-NetFirewallRule { } -Verifiable
        Mock Remove-NetFirewallRule { }

        Add-ScopedFirewallRule -AllowedIP "192.168.1.50"

        Should -InvokeVerifiable
        Should -Invoke Remove-NetFirewallRule -Times 0
    }

    It "replaces an existing rule rather than stacking" {
        Mock Get-NetFirewallRule { [PSCustomObject]@{ Name = "RemoteFleetAgent-SSH" } }
        Mock Remove-NetFirewallRule { } -Verifiable
        Mock New-NetFirewallRule { } -Verifiable

        Add-ScopedFirewallRule -AllowedIP "192.168.1.51"

        Should -InvokeVerifiable
    }
}

Describe "Register-SelfDestructTask" {
    It "creates a task 3 days out by default" {
        Mock Get-ScheduledTask { $null }
        Mock Unregister-ScheduledTask { }
        Mock New-ScheduledTaskTrigger { [PSCustomObject]@{} }
        Mock New-ScheduledTaskAction { [PSCustomObject]@{} }
        Mock Register-ScheduledTask { } -Verifiable

        Register-SelfDestructTask -UninstallScriptPath "C:\agent\uninstall.ps1"

        Should -InvokeVerifiable
        Should -Invoke Unregister-ScheduledTask -Times 0
    }

    It "resets the clock on re-run rather than stacking tasks" {
        Mock Get-ScheduledTask { [PSCustomObject]@{ TaskName = "RemoteFleetAgent-SelfDestruct" } }
        Mock Unregister-ScheduledTask { } -Verifiable
        Mock New-ScheduledTaskTrigger { [PSCustomObject]@{} }
        Mock New-ScheduledTaskAction { [PSCustomObject]@{} }
        Mock Register-ScheduledTask { } -Verifiable

        Register-SelfDestructTask -UninstallScriptPath "C:\agent\uninstall.ps1"

        Should -InvokeVerifiable
    }

    It "honours a custom expiry window" {
        Mock Get-ScheduledTask { $null }
        Mock Unregister-ScheduledTask { }
        Mock Register-ScheduledTask { }
        Mock New-ScheduledTaskAction { [PSCustomObject]@{} }
        Mock New-ScheduledTaskTrigger { [PSCustomObject]@{} } -Verifiable -ParameterFilter {
            $Once -and $At -lt (Get-Date).AddDays(1).AddMinutes(1)
        }

        Register-SelfDestructTask -UninstallScriptPath "C:\agent\uninstall.ps1" -DaysUntilExpiry 1

        Should -InvokeVerifiable
    }
}

Describe "Retired legacy installer" {
    It "refuses installation before changing a service or writing state" {
        Mock Enable-OpenSSHServer { throw 'Must not be reached' }
        { Invoke-Install -AllowedIP '192.0.2.1' -PublicKeyPath 'unused.pub' } | Should -Throw '*retired*'
        Should -Invoke Enable-OpenSSHServer -Times 0
    }
}
