BeforeAll {
    . "$PSScriptRoot/uninstall.ps1" -DotSource
}

Describe "Invoke-Uninstall" {
    It "leaves OpenSSH running if it was already enabled before install" {
        $statePath = Join-Path $TestDrive "state.json"
        @{ ssh_was_already_enabled = $true; installed_at = "2026-10-24T00:00:00Z" } |
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
        @{ ssh_was_already_enabled = $false; installed_at = "2026-10-24T00:00:00Z" } |
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
        @{ ssh_was_already_enabled = $true; installed_at = "2026-10-24T00:00:00Z" } |
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
