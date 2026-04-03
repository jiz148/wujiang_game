param(
    [int]$Port = 8000,
    [string]$RuleName = "",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $RuleName) {
    $RuleName = "Wujiang Game TCP $Port"
}

if (-not (Test-IsAdmin)) {
    throw "Please run this script as Administrator to change a Windows Firewall rule."
}

if ($Remove) {
    $existingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    if ($existingRule) {
        Remove-NetFirewallRule -DisplayName $RuleName | Out-Null
        Write-Host "Firewall rule removed: $RuleName"
    } else {
        Write-Host "Firewall rule not found: $RuleName"
    }
    exit 0
}

$existingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Set-NetFirewallRule -DisplayName $RuleName -Enabled True -Action Allow -Profile Any | Out-Null
    Write-Host "Firewall rule already exists and is enabled: $RuleName"
    exit 0
}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Inbound `
    -Action Allow `
    -Profile Any `
    -Protocol TCP `
    -LocalPort $Port | Out-Null

Write-Host "Firewall rule created: $RuleName"
