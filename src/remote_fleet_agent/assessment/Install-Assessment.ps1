[CmdletBinding()]
param([string]$AllowedIP, [string]$Engagement, [string]$LocalSourcePath, [ValidateRange(0.1,100)][double]$MinimumFreeGB=2, [ValidateRange(1,14)][int]$ExpiryDays=3, [switch]$DotSource)

function Test-PortIncludesSSH {
    param([string[]]$Ports)
    foreach ($port in $Ports) {
        if ($port -eq 'Any' -or $port -eq '22') { return $true }
        if ($port -match '^(\d+)-(\d+)$' -and [int]$Matches[1] -le 22 -and [int]$Matches[2] -ge 22) { return $true }
    }
    return $false
}

function Get-OtherSourceRanges {
    param([string]$EngineerIP)
    $parsed=$null
    if(-not [Net.IPAddress]::TryParse($EngineerIP,[ref]$parsed) -or $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork){throw 'A single IPv4 address is required.'}
    $bytes=$parsed.GetAddressBytes()
    $number=[uint64]$bytes[0]*16777216+[uint64]$bytes[1]*65536+[uint64]$bytes[2]*256+[uint64]$bytes[3]
    function To-Dotted([uint64]$n) { return ('{0}.{1}.{2}.{3}' -f (($n -shr 24) -band 255),(($n -shr 16) -band 255),(($n -shr 8) -band 255),($n -band 255)) }
    # The 32 sibling subnets along this address's binary path cover every
    # IPv4 address except this one, without /0 or full-space range endpoints.
    for($prefix=1;$prefix -le 32;$prefix++) {
        $size=[uint64][Math]::Pow(2,32-$prefix)
        $parent=[uint64]([Math]::Floor($number/($size*2))*($size*2))
        $start=if($number -ge ($parent+$size)){$parent}else{$parent+$size}
        (To-Dotted $start)+'/'+$prefix
    }
}

function ConvertTo-AddressNumber {
    param([Net.IPAddress]$Address)
    # Leading zero keeps the hexadecimal BigInteger positive, including IPv6.
    $hex='00'+([BitConverter]::ToString($Address.GetAddressBytes()).Replace('-',''))
    [System.Numerics.BigInteger]::Parse($hex,[Globalization.NumberStyles]::AllowHexSpecifier)
}

function Get-FirewallCoverage {
    param([string[]]$Addresses)
    Add-Type -AssemblyName System.Numerics -ErrorAction Stop
    $intervals=@(foreach($value in $Addresses) {
        foreach($raw in $value.Split(',')) {
            $address=$raw.Trim()
            if(-not $address){continue}
            $prefixParts=$address.Split('/')
            $rangeParts=$address.Split('-')
            try {
                if($rangeParts.Count -eq 2) {
                    $first=[Net.IPAddress]::Parse($rangeParts[0].Trim())
                    $last=[Net.IPAddress]::Parse($rangeParts[1].Trim())
                    if($first.AddressFamily -ne $last.AddressFamily){throw 'Range endpoints have different address families.'}
                    $start=ConvertTo-AddressNumber $first
                    $end=ConvertTo-AddressNumber $last
                } elseif($prefixParts.Count -eq 2) {
                    $first=[Net.IPAddress]::Parse($prefixParts[0].Trim())
                    $bits=$first.GetAddressBytes().Count*8
                    if($prefixParts[1] -match '^\d+$') {
                        $prefix=[int]$prefixParts[1]
                    } else {
                        $mask=[Net.IPAddress]::Parse($prefixParts[1].Trim())
                        if($mask.AddressFamily -ne $first.AddressFamily){throw 'Mask has a different address family.'}
                        $maskBits=($mask.GetAddressBytes() | ForEach-Object {[Convert]::ToString($_,2).PadLeft(8,'0')}) -join ''
                        if($maskBits -notmatch '^1*0*$'){throw 'Mask is not contiguous.'}
                        $prefix=($maskBits -replace '0','').Length
                    }
                    if($prefix -lt 0 -or $prefix -gt $bits){throw 'Prefix length is out of range.'}
                    $size=[System.Numerics.BigInteger]::Pow(2,($bits-$prefix))
                    $number=ConvertTo-AddressNumber $first
                    $start=[System.Numerics.BigInteger]::Multiply([System.Numerics.BigInteger]::Divide($number,$size),$size)
                    $end=$start+$size-1
                } elseif($prefixParts.Count -eq 1 -and $rangeParts.Count -eq 1) {
                    $first=[Net.IPAddress]::Parse($address)
                    $start=ConvertTo-AddressNumber $first
                    $end=$start
                } else {throw 'Unsupported address syntax.'}
                if($start -gt $end){throw 'Range endpoints are reversed.'}
                [PSCustomObject]@{Family=$first.GetAddressBytes().Count;Start=$start;End=$end}
            } catch {
                throw "Cannot verify Windows firewall address '$address': $($_.Exception.Message)"
            }
        }
    })
    # Compare the union of covered addresses, not Windows' choice of text format.
    foreach($family in @(4,16)) {
        $current=$null
        foreach($interval in @($intervals | Where-Object {$_.Family -eq $family} | Sort-Object Start,End)) {
            if($null -eq $current){$current=$interval;continue}
            if($interval.Start -le ($current.End+1)) {
                if($interval.End -gt $current.End){$current.End=$interval.End}
            } else {
                [string]$family+'|'+$current.Start.ToString()+'|'+$current.End.ToString()
                $current=$interval
            }
        }
        if($null -ne $current){[string]$family+'|'+$current.Start.ToString()+'|'+$current.End.ToString()}
    }
}

function Set-FleetFirewall {
    param([string]$EngineerIP,[string]$AllowRule,[string]$BlockRule)
    $ErrorActionPreference='Stop'
    [string[]]$blocked=@(Get-OtherSourceRanges $EngineerIP)
    [string[]]$ipv6Blocked=@('::/1','8000::/1')
    Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True
    # Update block protection in place so a repair never opens a gap in an
    # already-running SSH service. Existing unrelated allow rules are untouched.
    foreach($spec in @(@{name=$BlockRule;action='Block';addresses=$blocked},@{name=($BlockRule+'-IPv6');action='Block';addresses=$ipv6Blocked},@{name=$AllowRule;action='Allow';addresses=@($EngineerIP)})) {
        $options=@{PolicyStore='PersistentStore';Name=$spec.name;Direction='Inbound';Protocol='TCP';LocalPort=22;RemoteAddress=[string[]]$spec.addresses;Action=$spec.action;Profile='Any';Enabled='True'}
        Write-Host ('Applying firewall rule '+$spec.name+' ('+$spec.addresses.Count+' addresses)')
        if(Get-NetFirewallRule -PolicyStore PersistentStore -Name $spec.name -ErrorAction SilentlyContinue) {
            Set-NetFirewallRule @options
        } else {
            $null=New-NetFirewallRule @options -DisplayName ('Fleet assessment SSH '+$spec.action)
        }
    }
    foreach($spec in @(@{name=$BlockRule;action='Block';addresses=$blocked},@{name=($BlockRule+'-IPv6');action='Block';addresses=$ipv6Blocked},@{name=$AllowRule;action='Allow';addresses=@($EngineerIP)})) {
        $effective=@(Get-NetFirewallRule -PolicyStore ActiveStore -Name $spec.name -ErrorAction Stop)
        if($effective.Count -ne 1 -or [string]$effective[0].Enabled -ne 'True' -or [string]$effective[0].Action -ne $spec.action){throw 'Temporary firewall rule is not active.'}
        $actual=@(($effective[0] | Get-NetFirewallAddressFilter).RemoteAddress)
        $expected=@(Get-FirewallCoverage -Addresses $spec.addresses)
        $actual=@(Get-FirewallCoverage -Addresses $actual)
        if(@(Compare-Object ($expected | Sort-Object) ($actual | Sort-Object)).Count){throw ('Effective remote addresses differ for '+$spec.name+'. Expected: '+($spec.addresses -join ', ')+'; Windows returned: '+(@(($effective[0] | Get-NetFirewallAddressFilter).RemoteAddress) -join ', '))}
    }
    $effectiveProfiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore)
    if(@($effectiveProfiles | Where-Object {[string]$_.Enabled -ne 'True' -or [string]$_.AllowLocalFirewallRules -eq 'False'}).Count){throw 'Windows policy prevented effective local firewall protection.'}
}

function Write-FleetResult {
    param($Result,[string]$KitDirectory,[string]$StateDirectory)
    $safeName=$env:COMPUTERNAME -replace '[^A-Za-z0-9._-]','_'
    if(-not $safeName){$safeName='unknown-host'}
    $json=$Result | ConvertTo-Json -Depth 8
    foreach($directory in @($StateDirectory,(Join-Path $KitDirectory 'results'))) {
        if(-not $directory){continue}
        try {
            $null=New-Item -ItemType Directory -Force -Path $directory
            $json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $directory ($safeName+'-install-result.json'))
        } catch {Write-Warning "Could not save a result in $directory. $($_.Exception.Message)"}
    }
}

function Assert-FleetDiskSpace {
    param([string]$SystemDrive,[double]$MinimumGB)
    $driveName=$SystemDrive.TrimEnd([char[]]@(':','\'))
    $drive=Get-PSDrive -Name $driveName -PSProvider FileSystem -ErrorAction Stop
    $freeGB=[Math]::Round($drive.Free/1GB,2)
    Write-Host ("Free space on $SystemDrive = $freeGB GB; installer safety headroom: $MinimumGB GB")
    if($drive.Free -lt ($MinimumGB*1GB)) {
        throw "Insufficient free space on $SystemDrive ($freeGB GB). This installer requires $MinimumGB GB safety headroom before changing anything. Free space manually, then run again. No files were deleted."
    }
}

function Get-FleetCleanupArguments {
    param([string]$CleanupPath,[string]$StateDirectory)
    $cleanupLiteral=$CleanupPath.Replace("'","''")
    $stateLiteral=$StateDirectory.Replace("'","''")
    $errorLiteral=($StateDirectory.TrimEnd([char]92)+'\scheduled-cleanup-error.txt').Replace("'","''")
    $command="`$ErrorActionPreference='Stop'; try { & '$cleanupLiteral' -StateDirectory '$stateLiteral' } catch { try { `$_ | Out-String | Set-Content -LiteralPath '$errorLiteral' } catch {}; exit 1 }"
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    return '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand '+$encoded
}

function Write-FleetState {
    param($State,[string]$Path)
    $temporary=$Path+'.install-tmp'
    try {
        $State | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -LiteralPath $temporary
        if(Test-Path -LiteralPath $Path) {
            [IO.File]::Replace($temporary,$Path,($Path+'.before-install'))
        } else { [IO.File]::Move($temporary,$Path) }
    } finally {if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue}}
}

function Invoke-AssessmentInstall {
    param([string]$EngineerIP, [string]$KitDirectory)
    $ErrorActionPreference='Stop'
    Write-Host 'Remote Fleet assessment installer v3.0'
    $global:FleetStage='preflight'
    $global:FleetInstallErrorMessage=$null
    if ($env:OS -ne 'Windows_NT') { throw 'Run this on the Windows assessment PC.' }
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open Windows PowerShell as administrator and run Install-Assessment.ps1 again.'
    }
    if ($Engagement -notmatch '^[a-z0-9][a-z0-9-]{0,47}$') { throw 'Engagement must use lowercase letters, numbers and hyphens, up to 48 characters.' }
    $global:FleetStage='disk_space'
    Assert-FleetDiskSpace -SystemDrive $env:SystemDrive -MinimumGB $MinimumFreeGB
    $ip=$null
    if (-not [Net.IPAddress]::TryParse($EngineerIP,[ref]$ip) -or $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw 'AllowedIP must be a single IPv4 address.'
    }
    $key=(Get-Content -LiteralPath (Join-Path $KitDirectory 'id_ed25519.pub') -Raw).Trim()
    if ($key -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?$') { throw 'Expected one ed25519 public key line.' }
    $stateDir=Join-Path $env:ProgramData ('RemoteFleetAgent-'+$Engagement)
    $statePath=Join-Path $stateDir 'state.json'
    $global:FleetNeedsRestart=$false
    $global:FleetStage='preflight'
    if ((Test-Path -LiteralPath $stateDir) -and ((Get-Item -LiteralPath $stateDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'State directory must not be a reparse point.' }
    $previous=$null
    if (Test-Path -LiteralPath $statePath) {
        $previous=Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($previous.key -ne $key) { throw 'The saved installation belongs to a different public key. Use that engagement cleanup first.' }
        if ($previous.completed) {
            $archive=$stateDir+'-completed-'+(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
            Move-Item -LiteralPath $stateDir -Destination $archive
            $previous=$null
        }
    }
    $ruleName=('RemoteFleetAgent-'+$Engagement+'-SSH')
    $blockRuleName=('RemoteFleetAgent-'+$Engagement+'-OtherSources')
    $profiles=@(Get-NetFirewallProfile -PolicyStore PersistentStore | Select-Object Name,@{Name='Enabled';Expression={[string]$_.Enabled}})
    $effectiveProfiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore)
    if (@($effectiveProfiles | Where-Object { [string]$_.AllowLocalFirewallRules -eq 'False' }).Count) {
        throw 'Managed firewall policy prevents local rules. Cannot safely override this policy; use central deployment.'
    }
    # Existing allow rules are left intact. A temporary TCP/22 block covers every
    # other source address and takes precedence over broad allow rules.
    $defaultRule=Get-NetFirewallRule -PolicyStore PersistentStore -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
    $legacyTask=Get-ScheduledTask -TaskName 'RemoteFleetAgent-SelfDestruct' -ErrorAction SilentlyContinue
    if ($legacyTask -and $legacyTask.State -eq 'Running') {
        for($wait=0;$wait -lt 15 -and $legacyTask.State -eq 'Running';$wait++) {
            Start-Sleep -Seconds 2
            $legacyTask=Get-ScheduledTask -TaskName 'RemoteFleetAgent-SelfDestruct' -ErrorAction SilentlyContinue
        }
        if ($legacyTask -and $legacyTask.State -eq 'Running') { throw 'An older cleanup is actively running. Its result must settle before installation can continue.' }
    }
    $capName='OpenSSH.Server~~~~0.0.1.0'
    Write-Host 'Checking whether Windows OpenSSH Server is already installed...'
    $cap=Get-WindowsCapability -Online -Name $capName
    Write-Host ('Detected OpenSSH Server feature state: '+$cap.State)
    if ($cap.State -notin @('Installed','NotPresent')) { throw "OpenSSH capability state is $($cap.State); review pending changes first." }
    $svc=Get-CimInstance Win32_Service -Filter "Name='sshd'"
    if ($svc -and $svc.State -notin @('Running','Stopped')) { throw 'SSH service is transitioning; review it first.' }
    $authPath=Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
    $sshDir=Split-Path $authPath
    foreach ($path in @($sshDir,$authPath)) {
        if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing a reparse point at $path"
        }
    }
    $keyWasPresent=$false
    $originalAcl=$null
    if (Test-Path -LiteralPath $authPath) {
        $keyWasPresent=@(Get-Content -LiteralPath $authPath) -contains $key
        $originalAcl=(Get-Acl -LiteralPath $authPath).Sddl
    }
    $baseline=[ordered]@{
        engagement=$Engagement; schema=1; createdAtUtc=(Get-Date).ToUniversalTime().ToString('o')
        expiresAtUtc=(Get-Date).AddDays($ExpiryDays).ToUniversalTime().ToString('o')
        engineerIP=$EngineerIP; key=$key; keyWasPresent=$keyWasPresent
        originalAuthAcl=$originalAcl; capabilityWasInstalled=($cap.State -eq 'Installed')
        serviceWasRunning=($svc -and $svc.State -eq 'Running')
        originalServiceStartMode=if($svc){$svc.StartMode}else{$null}
        defaultRuleExisted=[bool]$defaultRule
        defaultRuleEnabled=if($defaultRule){[string]$defaultRule.Enabled}else{$null}
        defaultRemoteAddresses=if($defaultRule){@(($defaultRule | Get-NetFirewallAddressFilter).RemoteAddress)}else{@()}
        ruleName=$ruleName; blockRuleName=$blockRuleName; ipv6BlockRuleName=($blockRuleName+'-IPv6'); taskName=('RemoteFleetAgent-'+$Engagement+'-Cleanup'); completed=$false
        firewallProfiles=$profiles; legacyTaskWasEnabled=($legacyTask -and $legacyTask.State -ne 'Disabled'); legacyTaskExisted=[bool]$legacyTask
        installerVersion='3.0'; stage='baseline_saved'
    }
    if ($previous) {
        # Original pre-install facts survive every repair/re-run.
        $baseline=$previous
        foreach($pair in @{blockRuleName=$blockRuleName;ipv6BlockRuleName=($blockRuleName+'-IPv6');firewallProfiles=$profiles;legacyTaskExisted=[bool]$legacyTask;legacyTaskWasEnabled=($legacyTask -and $legacyTask.State -ne 'Disabled')}.GetEnumerator()) {
            if (-not $baseline.PSObject.Properties[$pair.Key]) { $baseline | Add-Member -NotePropertyName $pair.Key -NotePropertyValue $pair.Value }
        }
        $baseline.engineerIP=$EngineerIP
        $baseline.expiresAtUtc=(Get-Date).AddDays($ExpiryDays).ToUniversalTime().ToString('o')
    }
    $null=New-Item -ItemType Directory -Force -Path $stateDir
    & icacls.exe $stateDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not protect the local cleanup directory. Nothing installed.' }
    Write-FleetState -State $baseline -Path $statePath
    $cleanup=Join-Path $stateDir 'Remove-Assessment.ps1'
    Copy-Item -LiteralPath (Join-Path $KitDirectory 'Remove-Assessment.ps1') -Destination $cleanup
    try {
        $global:FleetStage='cleanup_registration'
        # Register cleanup before enabling the service; it is a protected local file.
        $trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddDays($ExpiryDays) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 7)
        $action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument (Get-FleetCleanupArguments -CleanupPath $cleanup -StateDirectory $stateDir)
        $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $null=Register-ScheduledTask -TaskName $baseline.taskName -Trigger $trigger -Action $action -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force
        if ($legacyTask) { $null=Disable-ScheduledTask -TaskName 'RemoteFleetAgent-SelfDestruct' }
        $global:FleetStage='firewall_scope'
        Set-FleetFirewall -EngineerIP $EngineerIP -AllowRule $ruleName -BlockRule $blockRuleName
        $global:FleetStage='windows_feature'
        if ((Get-WindowsCapability -Online -Name $capName).State -ne 'Installed') {
            $lastFeatureError=$null
            for($attempt=1;$attempt -le 3;$attempt++) {
                Write-Host "Installing Microsoft OpenSSH Server, attempt $attempt/3..."
                try {
                    $options=@{Online=$true;Name=$capName;ErrorAction='Stop'}
                    if($LocalSourcePath){$options.Source=$LocalSourcePath;$options.LimitAccess=$true}
                    $feature=Add-WindowsCapability @options
                    if($feature.RestartNeeded){$global:FleetNeedsRestart=$true;throw 'Windows reports that a restart is required to finish the feature installation.'}
                } catch {
                    $lastFeatureError=$_.Exception.Message
                    $code=$_.Exception.HResult
                    if($lastFeatureError -match '(?i)(not enough space|insufficient.*space|disk.*full|0x80070070)' -or $code -eq -2147024784){throw 'Windows servicing ran out of disk space. Retry was stopped; free space before another install.'}
                }
                if ((Get-WindowsCapability -Online -Name $capName).State -eq 'Installed') {break}
                if($attempt -lt 3){Start-Sleep -Seconds 3}
            }
            if($global:FleetNeedsRestart){throw 'Windows requires a restart before this assessment install can continue.'}
            if ((Get-WindowsCapability -Online -Name $capName).State -ne 'Installed') { throw "OpenSSH feature did not install after three attempts. Windows response: $lastFeatureError" }
        }
        # Reconcile again in case adding the feature created another allow rule.
        Set-FleetFirewall -EngineerIP $EngineerIP -AllowRule $ruleName -BlockRule $blockRuleName
        $global:FleetStage='key_permissions'
        $null=New-Item -ItemType Directory -Force -Path $sshDir
        if (-not (Test-Path -LiteralPath $authPath) -or @((Get-Content -LiteralPath $authPath -ErrorAction SilentlyContinue)) -notcontains $key) {
            $existing=if(Test-Path -LiteralPath $authPath){[IO.File]::ReadAllText($authPath)}else{''}
            if ($existing -and -not $existing.EndsWith("`n")) { $existing+="`r`n" }
            [IO.File]::WriteAllText($authPath,($existing+$key+"`r`n"),(New-Object Text.UTF8Encoding($false)))
        }
        # Build the exact admin/SYSTEM DACL expected by OpenSSH, using locale-independent SIDs.
        $acl=Get-Acl -LiteralPath $authPath
        $acl.SetAccessRuleProtection($true,$false)
        foreach ($entry in @($acl.Access)) { $null=$acl.RemoveAccessRuleSpecific($entry) }
        foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
            $id=New-Object Security.Principal.SecurityIdentifier($sid)
            $entry=New-Object Security.AccessControl.FileSystemAccessRule($id,'FullControl','Allow')
            $acl.AddAccessRule($entry)
        }
        $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
        Set-Acl -LiteralPath $authPath -AclObject $acl
        $global:FleetStage='ssh_service'
        if (-not $svc -or $svc.StartMode -ne 'Auto') { Set-Service sshd -StartupType Automatic }
        for($attempt=1;$attempt -le 3;$attempt++) {
            try { Start-Service sshd -ErrorAction Stop } catch { Write-Warning "SSH start attempt ${attempt}: $($_.Exception.Message)" }
            Start-Sleep -Seconds 2
            if ((Get-Service sshd).Status -eq 'Running') {break}
        }
        if ((Get-Service sshd).Status -ne 'Running') { throw 'SSH service failed to stay running after three attempts.' }
        $global:FleetStage='verify'
        & (Join-Path $env:SystemRoot 'System32\OpenSSH\sshd.exe') -t
        if($LASTEXITCODE -ne 0){throw 'Existing SSH configuration failed sshd validation.'}
        $hostKeyPath=Join-Path $env:ProgramData 'ssh\ssh_host_ed25519_key.pub'
        $hostKey=(Get-Content -LiteralPath $hostKeyPath -Raw).Trim()
        $sshKeygen=Join-Path $env:SystemRoot 'System32\OpenSSH\ssh-keygen.exe'
        $fingerprint=& $sshKeygen -lf $hostKeyPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not read the SSH host fingerprint.' }
        $receipt=[ordered]@{
            status='ready_for_connection_test'; installerVersion='3.0'; installedAtUtc=(Get-Date).ToUniversalTime().ToString('o'); computerName=$env:COMPUTERNAME
            windowsIdentity=$identity.Name; windowsUser=$env:USERNAME; engineerIP=$EngineerIP
            ipv4=@(Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias,IPAddress)
            hostPublicKey=$hostKey; fingerprint=$fingerprint; expiresAtUtc=$baseline.expiresAtUtc
            cleanupCommand="powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$cleanup`" -StateDirectory `"$stateDir`""
            note='Administrator SSH key access for testing. No password values or private keys collected.'
        }
        Write-FleetResult -Result $receipt -KitDirectory $KitDirectory -StateDirectory $stateDir
        Write-Host "ASSESSMENT INSTALL COMPLETE. Windows user: $($identity.Name)"
        Write-Host "Mac allowed: $EngineerIP; cleanup due: $($baseline.expiresAtUtc)"
        Write-Host "Host key fingerprint: $fingerprint"
        Write-Host ('Per-PC result saved under results\'+$env:COMPUTERNAME+'-install-result.json. No per-PC prompts are required.')
    } catch {
        $installError=$_
        $global:FleetInstallErrorMessage=$installError.Exception.Message
        Write-Warning 'Installation failed. Attempting to restore the original state...'
        $rollbackStatus='completed'
        try { & $cleanup }
        catch { $rollbackStatus='needs_attention'; Write-Warning "Cleanup also failed: $($_.Exception.Message). State and scheduled cleanup are retained in $stateDir." }
        $global:FleetRollbackStatus=$rollbackStatus
        Write-FleetResult -Result @{status='failed';rollback=$rollbackStatus;stage=$global:FleetStage;error=$installError.Exception.Message;computerName=$env:COMPUTERNAME;atUtc=(Get-Date).ToUniversalTime().ToString('o')} -KitDirectory $KitDirectory -StateDirectory $stateDir
        throw $installError
    }
}

if ($DotSource) { return }
try {
    Invoke-AssessmentInstall -EngineerIP $AllowedIP -KitDirectory $PSScriptRoot
} catch {
    $failure=@{status='failed';rollback=$global:FleetRollbackStatus;stage=$global:FleetStage;error=$(if($global:FleetInstallErrorMessage){$global:FleetInstallErrorMessage}else{$_.Exception.Message});computerName=$env:COMPUTERNAME;atUtc=(Get-Date).ToUniversalTime().ToString('o')}
    Write-FleetResult -Result $failure -KitDirectory $PSScriptRoot -StateDirectory ''
    Write-Error $failure.error
    exit 1
}
