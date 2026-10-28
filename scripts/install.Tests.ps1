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
        'Enable-WindowsOptionalFeature'  = { param([switch]$Online, $FeatureName, [switch]$NoRestart) }
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
        Mock Get-Service { $null } -ParameterFilter { $Name -eq "sshd" }
        Mock Enable-WindowsOptionalFeature { } -Verifiable
        Mock Start-Service { }
        Mock Set-Service { }
        $statePath = Join-Path $TestDrive "state.json"

        Enable-OpenSSHServer -StatePath $statePath

        Should -InvokeVerifiable
        $state = Get-Content $statePath | ConvertFrom-Json
        $state.ssh_was_already_enabled | Should -Be $false
    }

    It "does not re-enable when already running, records true" {
        Mock Get-Service { [PSCustomObject]@{ Status = "Running" } } -ParameterFilter { $Name -eq "sshd" }
        Mock Enable-WindowsOptionalFeature { }
        $statePath = Join-Path $TestDrive "state2.json"

        Enable-OpenSSHServer -StatePath $statePath

        Should -Invoke Enable-WindowsOptionalFeature -Times 0
        $state = Get-Content $statePath | ConvertFrom-Json
        $state.ssh_was_already_enabled | Should -Be $true
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

Describe "Invoke-Install (main)" {
    It "runs all four steps in order for valid input" {
        # DEVIATION: mock New-Item and Join-Path too - the real install path
        # is the literal Windows path "C:\ProgramData\remote-fleet-agent",
        # and macOS pwsh has no C: PSDrive, so even Join-Path (not just
        # New-Item) throws trying to resolve it. Neither mock's return value
        # matters here since the function receiving it (Enable-OpenSSHServer)
        # is itself mocked below - this is only about not erroring on an
        # OS-specific path quirk unrelated to what this test checks.
        Mock New-Item { }
        Mock Join-Path { param($Path, $ChildPath) "$Path\$ChildPath" }
        Mock Enable-OpenSSHServer { } -Verifiable
        Mock Add-EngineerKey { } -Verifiable
        Mock Add-ScopedFirewallRule { } -Verifiable
        Mock Register-SelfDestructTask { } -Verifiable
        $keyFile = Join-Path $TestDrive "key.pub"
        Set-Content $keyFile "ssh-ed25519 AAAA..."

        Invoke-Install -AllowedIP "192.168.1.50" -PublicKeyPath $keyFile

        Should -InvokeVerifiable
    }

    It "rejects a malformed IP before making any changes" {
        Mock New-Item { }
        Mock Enable-OpenSSHServer { }
        $keyFile = Join-Path $TestDrive "key2.pub"
        Set-Content $keyFile "ssh-ed25519 AAAA..."

        { Invoke-Install -AllowedIP "not-an-ip" -PublicKeyPath $keyFile } | Should -Throw
        Should -Invoke Enable-OpenSSHServer -Times 0
    }

    It "rejects a missing public key file" {
        Mock New-Item { }
        { Invoke-Install -AllowedIP "192.168.1.50" -PublicKeyPath (Join-Path $TestDrive "missing.pub") } |
            Should -Throw
    }
}
