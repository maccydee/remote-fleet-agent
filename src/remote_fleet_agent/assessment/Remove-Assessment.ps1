[CmdletBinding()]
param([Parameter(Mandatory)][string]$StateDirectory, [string]$ReportDirectory=(Join-Path $PSScriptRoot 'results'))
$ErrorActionPreference='Stop'
$statePath=Join-Path $StateDirectory 'state.json'
if (-not (Test-Path -LiteralPath $statePath)) { throw 'Assessment state is missing; stop and review rather than guessing the original configuration.' }
$state=Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($state.completed) { Write-Host 'Test cleanup already completed. See cleanup-result.json.'; return }
$errors=New-Object 'System.Collections.Generic.List[string]'
$auth=Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
try {
    if ((Test-Path -LiteralPath $auth) -and -not $state.keyWasPresent) {
        $lines=@(Get-Content -LiteralPath $auth | Where-Object { $_ -ne $state.key })
        $text=if($lines.Count){($lines -join "`r`n")+"`r`n"}else{''}
        [IO.File]::WriteAllText($auth,$text,(New-Object Text.UTF8Encoding($false)))
        if (@(Get-Content -LiteralPath $auth) -contains $state.key) { throw 'Test key is still present.' }
    }
    if ((Test-Path -LiteralPath $auth) -and $state.originalAuthAcl) {
        $acl=Get-Acl -LiteralPath $auth
        $acl.SetSecurityDescriptorSddlForm($state.originalAuthAcl)
        Set-Acl -LiteralPath $auth -AclObject $acl
    }
} catch { $errors.Add("Key cleanup: $($_.Exception.Message)") }
try {
    if (Get-NetFirewallRule -Name $state.ruleName -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -Name $state.ruleName }
    if (Get-NetFirewallRule -Name $state.ruleName -ErrorAction SilentlyContinue) { throw 'Test firewall rule remains.' }
} catch { $errors.Add("Test firewall cleanup: $($_.Exception.Message)") }
try {
    $svc=Get-Service sshd -ErrorAction SilentlyContinue
    if ($svc -and -not $state.serviceWasRunning) { Stop-Service sshd -Force }
    # v2 starts SSH automatically for the assessment. Restore Manual/Disabled;
    # leave pre-existing Auto details (including delayed auto-start) untouched.
    if ($state.originalServiceStartMode -in @('Disabled','Manual')) {
        $mode=switch($state.originalServiceStartMode){'Auto'{'Automatic'};'Manual'{'Manual'};'Disabled'{'Disabled'};default{throw 'Unknown original service startup mode'}}
        Set-Service sshd -StartupType $mode
    }
    if ($state.serviceWasRunning) { Start-Service sshd }
    if (-not $state.capabilityWasInstalled) {
        $cap=Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
        if ($cap.State -ne 'NotPresent') { $null=Remove-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' }
        if ((Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0').State -ne 'NotPresent') { throw 'OpenSSH feature removal is incomplete; service was stopped but removal needs review.' }
    }
    $svc=Get-Service sshd -ErrorAction SilentlyContinue
    if ($state.serviceWasRunning -and (!$svc -or $svc.Status -ne 'Running')) { throw 'Original running service was not restored.' }
    if (-not $state.serviceWasRunning -and $svc -and $svc.Status -ne 'Stopped') { throw 'Test-started service is still running.' }
} catch { $errors.Add("SSH service/feature cleanup: $($_.Exception.Message)") }
try {
    $default=Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
    if ($state.defaultRuleExisted) {
        if (-not $default) { throw 'Original default firewall rule is missing; inspect before recreating it.' }
        if($state.installerVersion -notin @('2.0','2.1','2.2','2.3','3.0')){Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -RemoteAddress $state.defaultRemoteAddresses -Enabled $state.defaultRuleEnabled}
    } elseif ($default) {
        Remove-NetFirewallRule -Name 'OpenSSH-Server-In-TCP'
    }
} catch { $errors.Add("Original firewall restoration: $($_.Exception.Message)") }
try {
    if ($errors.Count -eq 0 -and $state.legacyTaskExisted -and $state.legacyTaskWasEnabled) {
        $task=Get-ScheduledTask -TaskName 'RemoteFleetAgent-SelfDestruct' -ErrorAction SilentlyContinue
        if($task){$null=Enable-ScheduledTask -TaskName 'RemoteFleetAgent-SelfDestruct'}
    }
} catch { $errors.Add("Previous cleanup task restoration: $($_.Exception.Message)") }
try {
    foreach($profile in @($(if($errors.Count -eq 0){$state.firewallProfiles}))) {
        if($profile -and [string]$profile.Enabled -ne 'True') { Set-NetFirewallProfile -Profile $profile.Name -Enabled $profile.Enabled }
    }
} catch { $errors.Add("Firewall profile restoration: $($_.Exception.Message)") }
# Keep other-source blocks until key/service restoration attempts finish.
try {
    if($errors.Count -eq 0) {
        foreach($name in @($state.blockRuleName,$state.ipv6BlockRuleName)) {
            if(-not $name){continue}
            if(Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue){Remove-NetFirewallRule -Name $name}
            if(Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue){throw "Assessment block rule remains: $name"}
        }
    }
} catch { $errors.Add("Assessment block-rule removal: $($_.Exception.Message)") }
if ($errors.Count -eq 0) {
    try { Unregister-ScheduledTask -TaskName $state.taskName -Confirm:$false -ErrorAction SilentlyContinue }
    catch { $errors.Add("Cleanup task removal: $($_.Exception.Message)") }
    if (Get-ScheduledTask -TaskName $state.taskName -ErrorAction SilentlyContinue) { $errors.Add('Cleanup task still exists.') }
}
$receipt=@{cleanedAtUtc=(Get-Date).ToUniversalTime().ToString('o'); computerName=$env:COMPUTERNAME; success=($errors.Count -eq 0); errors=@($errors.ToArray()); keyWasPreExisting=$state.keyWasPresent; originalServiceWasRunning=$state.serviceWasRunning; stateJournalSaved=$false}
if($errors.Count -eq 0) {
    $state.completed=$true
    $temporaryState=$statePath+'.cleanup-tmp'
    try {
        # Do not truncate the original rollback journal if the disk is full.
        $state | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -LiteralPath $temporaryState
        [IO.File]::Replace($temporaryState,$statePath,($statePath+'.before-cleanup-'+(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')))
        $receipt.stateJournalSaved=$true
    } catch { Write-Warning ('Access cleanup finished, but the state journal could not be marked complete. The original journal is retained. '+$_.Exception.Message) }
    finally {if(Test-Path -LiteralPath $temporaryState){Remove-Item -LiteralPath $temporaryState -Force -ErrorAction SilentlyContinue}}
}
$json=$receipt | ConvertTo-Json -Depth 6
# Receipt storage is best-effort. It must never stop access revocation.
try {$json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $StateDirectory 'cleanup-result.json')}
catch {Write-Warning 'Could not save the local cleanup receipt; see the console/shared receipt.'}
if($ReportDirectory) {
    try {
        $null=New-Item -ItemType Directory -Force -Path $ReportDirectory
        $safeName=$env:COMPUTERNAME -replace '[^A-Za-z0-9._-]','_'
        if(-not $safeName){$safeName='unknown-host'}
        $json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $ReportDirectory ($safeName+'-cleanup-result.json'))
    } catch {Write-Warning 'Could not save a shared cleanup receipt; the full result is printed below.'}
}
Write-Host $json
if ($errors.Count) {throw 'Cleanup is incomplete. The console result lists outstanding changes; retry after disk space is available.'}
Write-Host 'ASSESSMENT CLEANUP COMPLETE. Original service and firewall settings restored; test key/rules removed where newly added.'
