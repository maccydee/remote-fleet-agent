# Retired prototype. Keep the entry point only to fail safely for old commands.
param([switch]$DotSource)

function Invoke-Install {
    param(
        [string]$AllowedIP,
        [string]$PublicKeyPath,
        [int]$DaysUntilExpiry = 3,
        [string]$LocalSourcePath
    )
    throw 'The legacy installer is retired. Generate a new kit with remote-fleet-agent init-client and run Install-Assessment.ps1. Existing cleanup tasks are not removed by this message.'
}

if ($DotSource) { return }
Invoke-Install @args
