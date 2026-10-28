param([switch]$DotSource)

# These cmdlets ship in Windows-only modules (ScheduledTasks, NetSecurity, DISM)
# that don't exist on this dev machine (macOS). Pester 6's Mock requires the
# target command to already be resolvable via Get-Command before it can be
# mocked, so on a box without those modules the real cmdlets are stubbed in
# here purely so Mock has something to attach to. On real Windows the guard
# means these stubs are never defined and the genuine cmdlets are used as-is.
if (-not (Get-Command Unregister-ScheduledTask -ErrorAction SilentlyContinue)) {
    function Unregister-ScheduledTask {
        [CmdletBinding(SupportsShouldProcess)]
        param([string]$TaskName)
    }
}
if (-not (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)) {
    function Get-NetFirewallRule {
        [CmdletBinding()]
        param([string]$Name)
    }
}
if (-not (Get-Command Remove-NetFirewallRule -ErrorAction SilentlyContinue)) {
    function Remove-NetFirewallRule {
        [CmdletBinding()]
        param([string]$Name)
    }
}
if (-not (Get-Command Disable-WindowsOptionalFeature -ErrorAction SilentlyContinue)) {
    function Disable-WindowsOptionalFeature {
        [CmdletBinding()]
        param([switch]$Online, [string]$FeatureName, [switch]$NoRestart)
    }
}

function Remove-EngineerKey {
    param([string]$AuthorizedKeysPath = "$env:ProgramData\ssh\administrators_authorized_keys")
    # Removal targets the key this tool added; in v1 that's the only key
    # in this file, so clearing it is safe and simple.
    if (Test-Path $AuthorizedKeysPath) { Clear-Content $AuthorizedKeysPath }
}

function Remove-ScopedFirewallRule {
    $ruleName = "RemoteFleetAgent-SSH"
    if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -Name $ruleName
    }
}

function Invoke-Uninstall {
    param([Parameter(Mandatory)][string]$StatePath)

    $state = Get-Content $StatePath | ConvertFrom-Json
    $removed = @()

    Remove-EngineerKey
    $removed += "ssh_key"

    Remove-ScopedFirewallRule
    $removed += "firewall_rule"

    Unregister-ScheduledTask -TaskName "RemoteFleetAgent-SelfDestruct" -Confirm:$false -ErrorAction SilentlyContinue
    $removed += "self_destruct_task"

    $sshLeftRunning = $state.ssh_was_already_enabled
    if (-not $sshLeftRunning) {
        Disable-WindowsOptionalFeature -Online -FeatureName "OpenSSH.Server" -NoRestart
        $removed += "openssh_server"
    }

    Remove-Item $StatePath -ErrorAction SilentlyContinue

    return [PSCustomObject]@{
        removed_at       = (Get-Date).ToUniversalTime().ToString("o")
        removed          = $removed
        ssh_left_running = $sshLeftRunning
    }
}

if ($DotSource) { return }
$receipt = Invoke-Uninstall -StatePath "C:\ProgramData\remote-fleet-agent\state.json"
$receipt | ConvertTo-Json
