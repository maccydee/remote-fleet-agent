BeforeAll {
    . "$PSScriptRoot/install.ps1" -DotSource
}

Describe 'Retired legacy installer' {
    It 'refuses installation and exposes no old access or scheduled-task helpers' {
        { Invoke-Install -AllowedIP '192.0.2.1' -PublicKeyPath 'unused.pub' } | Should -Throw '*retired*'
        Get-Command Enable-OpenSSHServer -ErrorAction SilentlyContinue | Should -BeNullOrEmpty
        Get-Command Register-SelfDestructTask -ErrorAction SilentlyContinue | Should -BeNullOrEmpty
    }
}
