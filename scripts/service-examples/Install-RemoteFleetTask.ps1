# Example only. Review exact account, paths and startup policy before installation.
# Does not set credentials, change SSH/firewall settings or grant endpoint access.
param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$WorkspaceDirectory,
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'Python interpreter is missing' }
if (-not (Test-Path -LiteralPath (Join-Path $WorkspaceDirectory 'workspace.json') -PathType Leaf)) { throw 'Workspace is missing' }
if ($PythonPath.Contains('"') -or $WorkspaceDirectory.Contains('"')) { throw 'Quotes are invalid in these paths' }
$arguments = '-m remote_fleet_agent.cli workspace-app serve --directory "' + $WorkspaceDirectory + '" --port 8877'
if (-not $Install) { Write-Output ('Review: ' + $PythonPath + ' ' + $arguments); return }
$action = New-ScheduledTaskAction -Execute $PythonPath -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'Remote Fleet Controller' -Action $action -Trigger $trigger -Principal $principal -Settings $settings
