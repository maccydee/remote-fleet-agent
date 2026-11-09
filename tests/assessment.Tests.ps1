BeforeAll {
    . (Join-Path $PSScriptRoot '../src/remote_fleet_agent/assessment/Install-Assessment.ps1') -DotSource
}
Describe 'Bundled assessment installer guards' {
    It 'accepts SSH within single ports and ranges' {
        Test-PortIncludesSSH @('22') | Should -BeTrue
        Test-PortIncludesSSH @('20-30') | Should -BeTrue
        Test-PortIncludesSSH @('443') | Should -BeFalse
    }
    It 'covers IPv4 except the engineer with 32 sibling networks' {
        $ranges=@(Get-OtherSourceRanges '192.0.2.10')
        $ranges.Count | Should -Be 32
        $coverage=@(Get-FirewallCoverage $ranges)
        $coverage.Count | Should -Be 2
        $n=ConvertTo-AddressNumber ([Net.IPAddress]::Parse('192.0.2.10'))
        $coverage | Should -Contain ('4|0|'+($n-1).ToString())
        $coverage | Should -Contain ('4|'+($n+1).ToString()+'|4294967295')
    }
    It 'normalizes equivalent masks and ranges' {
        $a=@(Get-FirewallCoverage @('192.0.2.0/24'))
        $b=@(Get-FirewallCoverage @('192.0.2.0/255.255.255.0'))
        $c=@(Get-FirewallCoverage @('192.0.2.0-192.0.2.255'))
        ($a -join ',') | Should -Be ($b -join ',')
        ($a -join ',') | Should -Be ($c -join ',')
    }
    It 'rejects invalid engineer addresses' {
        {Get-OtherSourceRanges '0.0.0.0/0'} | Should -Throw
        {Get-OtherSourceRanges '999.0.0.1'} | Should -Throw
        {Get-OtherSourceRanges '::1'} | Should -Throw
    }
    It 'normalizes both IPv6 halves to full coverage' {
        (@(Get-FirewallCoverage @('::/1','8000::/1')) -join ',') | Should -Be '16|0|340282366920938463463374607431768211455'
    }
    It 'retains the original journal if replacing state fails' {
        $path=Join-Path $TestDrive 'state.json'
        Write-FleetState -State @{original=$true} -Path $path
        # A directory at the backup pathname causes atomic Replace to fail.
        $null=New-Item -ItemType Directory ($path+'.before-install')
        {Write-FleetState -State @{original=$false} -Path $path} | Should -Throw
        (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json).original | Should -BeTrue
    }
    It 'saves successive states and a pre-change backup' {
        $path=Join-Path $TestDrive 'updated.json'
        Write-FleetState -State @{revision=1} -Path $path
        Write-FleetState -State @{revision=2} -Path $path
        (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json).revision | Should -Be 2
        (Get-Content -LiteralPath ($path+'.before-install') -Raw | ConvertFrom-Json).revision | Should -Be 1
    }
}
Describe 'Scheduled cleanup arguments' {
    It 'encodes paths so task launch cannot reinterpret their quoting' {
        $cleanupArgs=Get-FleetCleanupArguments -CleanupPath "C:\ProgramData\Fleet's kit\Remove-Assessment.ps1" -StateDirectory 'C:\ProgramData\State folder'
        $cleanupArgs | Should -Match '^-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand [A-Za-z0-9+/=]+$'
        $command=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String(($cleanupArgs -split ' ')[-1]))
        $command | Should -Match "Fleet''s kit"
        $command | Should -Match "-StateDirectory 'C:\\ProgramData\\State folder'"
        $command | Should -Match 'scheduled-cleanup-error.txt'
        $tokens=$null;$parseErrors=$null
        $null=[Management.Automation.Language.Parser]::ParseInput($command,[ref]$tokens,[ref]$parseErrors)
        @($parseErrors).Count | Should -Be 0
    }
}
