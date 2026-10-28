BeforeAll {
    . "$PSScriptRoot/uninstall.ps1" -DotSource
}

Describe "Remove-EngineerKey" {
    It "removes only the recorded key line, leaving other keys in the file intact" {
        $authKeys = Join-Path $TestDrive "authorized_keys_shared"
        $otherAdminKey = "ssh-ed25519 BBBB... other-admin@somewhere"
        $ourKey = "ssh-ed25519 AAAA... engineer@laptop"
        Set-Content $authKeys @($otherAdminKey, $ourKey)

        Remove-EngineerKey -AuthorizedKeysPath $authKeys -KeyContent $ourKey

        $remaining = Get-Content $authKeys
        $remaining | Should -Contain $otherAdminKey
        $remaining | Should -Not -Contain $ourKey
    }

    It "does nothing if no key was recorded (state predates this fix, or key was never added)" {
        $authKeys = Join-Path $TestDrive "authorized_keys_norecord"
        $otherAdminKey = "ssh-ed25519 BBBB... other-admin@somewhere"
        Set-Content $authKeys @($otherAdminKey)

        Remove-EngineerKey -AuthorizedKeysPath $authKeys -KeyContent ""

        (Get-Content $authKeys) | Should -Contain $otherAdminKey
    }
}

Describe "Invoke-Uninstall" {
    It "leaves OpenSSH running if it was already enabled before install" {
        $statePath = Join-Path $TestDrive "state.json"
        @{ ssh_was_already_enabled = $true; installed_at = "2026-10-24T00:00:00Z"; ssh_key_added = "ssh-ed25519 AAAA..." } |
            ConvertTo-Json | Set-Content $statePath

        Mock Remove-EngineerKey { }
        Mock Remove-ScopedFirewallRule { }
        Mock Unregister-ScheduledTask { }
        Mock Disable-WindowsOptionalFeature { } -Verifiable

        $receipt = Invoke-Uninstall -StatePath $statePath

        Should -Invoke Disable-WindowsOptionalFeature -Times 0
        $receipt.ssh_left_running | Should -Be $true
    }

    It "disables OpenSSH if this tool enabled it" {
        $statePath = Join-Path $TestDrive "state2.json"
        @{ ssh_was_already_enabled = $false; installed_at = "2026-10-24T00:00:00Z"; ssh_key_added = "ssh-ed25519 AAAA..." } |
            ConvertTo-Json | Set-Content $statePath

        Mock Remove-EngineerKey { }
        Mock Remove-ScopedFirewallRule { }
        Mock Unregister-ScheduledTask { }
        Mock Disable-WindowsOptionalFeature { } -Verifiable

        $receipt = Invoke-Uninstall -StatePath $statePath

        Should -InvokeVerifiable
        $receipt.ssh_left_running | Should -Be $false
    }

    It "produces a receipt with a timestamp and removal list" {
        $statePath = Join-Path $TestDrive "state3.json"
        @{ ssh_was_already_enabled = $true; installed_at = "2026-10-24T00:00:00Z"; ssh_key_added = "ssh-ed25519 AAAA..." } |
            ConvertTo-Json | Set-Content $statePath
        Mock Remove-EngineerKey { }
        Mock Remove-ScopedFirewallRule { }
        Mock Unregister-ScheduledTask { }

        $receipt = Invoke-Uninstall -StatePath $statePath

        $receipt.removed_at | Should -Not -BeNullOrEmpty
        $receipt.removed | Should -Contain "ssh_key"
        $receipt.removed | Should -Contain "firewall_rule"
        $receipt.removed | Should -Contain "self_destruct_task"
    }
}
