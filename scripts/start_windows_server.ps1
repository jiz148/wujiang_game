param(
    [int]$Port = 8000,
    [string]$BindHost = "0.0.0.0",
    [string]$PublicBaseUrl = "",
    [switch]$OpenFirewall,
    [switch]$NoPrompt,
    [switch]$LanOnly,
    [switch]$NoBrowser,
    [string]$PythonExe = "python",
    [string]$CloudflaredExe = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runPath = Join-Path $projectRoot "run.py"
$firewallScriptPath = Join-Path $PSScriptRoot "open_firewall_port.ps1"
$temporaryFirewallRuleName = "Wujiang Game Temporary TCP $Port"
$temporaryFirewallOpened = $false
$quickTunnelProcess = $null
$quickTunnelStdOutPath = ""
$quickTunnelStdErrPath = ""
$quickTunnelLogsDir = Join-Path $projectRoot ".runtime"

function Get-LanIpv4Candidates {
    $candidates = @()

    try {
        $configs = Get-NetIPConfiguration -ErrorAction Stop |
            Where-Object { $_.NetAdapter -and $_.NetAdapter.Status -eq "Up" -and $_.IPv4Address }

        $preferredConfigs = @(
            $configs | Where-Object { $_.IPv4DefaultGateway }
            $configs | Where-Object { -not $_.IPv4DefaultGateway }
        )

        $candidates = $preferredConfigs |
            ForEach-Object { $_.IPv4Address } |
            Where-Object {
                $_.IPAddress -and
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*"
            } |
            Select-Object -ExpandProperty IPAddress -Unique
    } catch {
        $candidates = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
            Where-Object {
                $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
                $_.IPAddressToString -notlike "127.*" -and
                $_.IPAddressToString -notlike "169.254.*"
            } |
            ForEach-Object { $_.IPAddressToString } |
            Select-Object -Unique
    }

    return @($candidates)
}

function Read-YesNo([string]$Prompt, [bool]$Default = $false) {
    $suffix = if ($Default) { "[Y/n]" } else { "[y/N]" }
    $raw = Read-Host "$Prompt $suffix"
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $Default
    }
    return $raw.Trim().ToLowerInvariant().StartsWith("y")
}

function Stop-RunningProcess([System.Diagnostics.Process]$Process, [string]$Label) {
    if (-not $Process) {
        return
    }
    try {
        $Process.Refresh()
    } catch {
        return
    }
    if ($Process.HasExited) {
        return
    }
    try {
        Stop-Process -Id $Process.Id -Force -ErrorAction Stop
        Write-Host "$Label closed."
    } catch {
        Write-Warning "Failed to stop ${Label}: $($_.Exception.Message)"
    }
}

function Get-CloudflaredDownloadUrl {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "Automatic cloudflared download currently supports 64-bit Windows only. Please install cloudflared manually and pass -CloudflaredExe."
    }
    return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
}

function Wait-ExecutableReady([string]$ExecutablePath, [int]$TimeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            Unblock-File -Path $ExecutablePath -ErrorAction SilentlyContinue
            $stream = [System.IO.File]::Open($ExecutablePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
            $stream.Close()
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)

    throw "The executable is still locked after waiting $TimeoutSeconds seconds: $ExecutablePath"
}

function Resolve-CloudflaredExecutable([string]$RequestedPath) {
    if ($RequestedPath) {
        $resolvedRequestedPath = Resolve-Path -LiteralPath $RequestedPath -ErrorAction Stop
        Wait-ExecutableReady -ExecutablePath $resolvedRequestedPath.Path
        return $resolvedRequestedPath.Path
    }

    try {
        $command = Get-Command cloudflared.exe -ErrorAction Stop
        if ($command.Source) {
            return $command.Source
        }
    } catch {
    }

    $localDir = Join-Path $projectRoot "tools\cloudflared"
    $localExe = Join-Path $localDir "cloudflared.exe"
    if (Test-Path $localExe) {
        Wait-ExecutableReady -ExecutablePath $localExe
        return (Resolve-Path -LiteralPath $localExe).Path
    }

    New-Item -ItemType Directory -Path $localDir -Force | Out-Null
    $downloadUrl = Get-CloudflaredDownloadUrl

    Write-Host "cloudflared was not found. Downloading a local copy for this project..."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $localExe
    Wait-ExecutableReady -ExecutablePath $localExe
    return (Resolve-Path -LiteralPath $localExe).Path
}

function Read-QuickTunnelUrl([string]$StdOutPath, [string]$StdErrPath) {
    $combined = ""
    foreach ($path in @($StdOutPath, $StdErrPath)) {
        if ($path -and (Test-Path $path)) {
            try {
                $combined += (Get-Content -LiteralPath $path -Raw -ErrorAction Stop)
                $combined += "`n"
            } catch {
            }
        }
    }
    $match = [System.Text.RegularExpressions.Regex]::Match($combined, 'https://[-a-z0-9]+\.trycloudflare\.com')
    if ($match.Success) {
        return $match.Value
    }
    return ""
}

function Get-QuickTunnelLogTail([string]$StdOutPath, [string]$StdErrPath) {
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($path in @($StdOutPath, $StdErrPath)) {
        if ($path -and (Test-Path $path)) {
            try {
                foreach ($line in (Get-Content -LiteralPath $path -Tail 30 -ErrorAction Stop)) {
                    if (-not [string]::IsNullOrWhiteSpace($line)) {
                        $lines.Add($line)
                    }
                }
            } catch {
            }
        }
    }
    return ($lines | Select-Object -Last 30) -join "`n"
}

function Start-QuickTunnel([string]$ExecutablePath, [int]$PortNumber) {
    New-Item -ItemType Directory -Path $quickTunnelLogsDir -Force | Out-Null

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdoutPath = Join-Path $quickTunnelLogsDir "cloudflared-$PortNumber-$timestamp.stdout.log"
    $stderrPath = Join-Path $quickTunnelLogsDir "cloudflared-$PortNumber-$timestamp.stderr.log"
    $arguments = @(
        "tunnel",
        "--url", "http://127.0.0.1:$PortNumber",
        "--no-autoupdate"
    )

    $process = Start-Process -FilePath $ExecutablePath `
        -ArgumentList $arguments `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath

    $deadline = (Get-Date).AddSeconds(35)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $process.Refresh()
        } catch {
        }

        if ($process.HasExited) {
            $logTail = Get-QuickTunnelLogTail -StdOutPath $stdoutPath -StdErrPath $stderrPath
            throw "cloudflared exited before the public URL was ready.`n$logTail"
        }

        $publicUrl = Read-QuickTunnelUrl -StdOutPath $stdoutPath -StdErrPath $stderrPath
        if ($publicUrl) {
            return @{
                Process = $process
                PublicUrl = $publicUrl
                StdOutPath = $stdoutPath
                StdErrPath = $stderrPath
            }
        }
    } while ((Get-Date) -lt $deadline)

    $logTail = Get-QuickTunnelLogTail -StdOutPath $stdoutPath -StdErrPath $stderrPath
    throw "Timed out waiting for cloudflared to publish a trycloudflare.com URL.`n$logTail"
}

if (-not (Test-Path $runPath)) {
    throw "Could not find run.py at $runPath"
}

$lanCandidates = @(Get-LanIpv4Candidates)
$suggestedBaseUrl = ""
if ($lanCandidates.Count -gt 0) {
    $suggestedIp = $lanCandidates | Select-Object -First 1
    $suggestedBaseUrl = "http://${suggestedIp}:$Port"
}

$useQuickTunnel = (-not $LanOnly) -and (-not $PublicBaseUrl)

if (-not $NoPrompt) {
    Write-Host ""
    Write-Host "Wujiang Windows server launcher"
    Write-Host "Project root: $projectRoot"
    Write-Host "Port: $Port"
    if ($lanCandidates.Count -gt 0) {
        Write-Host "Detected LAN IPv4 addresses:"
        foreach ($address in $lanCandidates) {
            Write-Host "  - $address"
        }
    } else {
        Write-Host "No LAN IPv4 address was detected automatically."
    }
    Write-Host ""

    if ($useQuickTunnel) {
        Write-Host "Default share mode: public temporary tunnel"
        Write-Host "Friends outside your home network can open the generated HTTPS link directly."
        Write-Host "No router port forwarding is needed for this mode."
        Write-Host "Use -LanOnly if you want the old LAN/manual-address mode instead."
        Write-Host ""
    }

    if (-not $useQuickTunnel -and -not $PublicBaseUrl) {
        if ($suggestedBaseUrl) {
            $enteredBaseUrl = Read-Host "Share homepage URL for friends (Enter = $suggestedBaseUrl)"
            $PublicBaseUrl = if ([string]::IsNullOrWhiteSpace($enteredBaseUrl)) { $suggestedBaseUrl } else { $enteredBaseUrl.Trim() }
        } else {
            $enteredBaseUrl = Read-Host "Share homepage URL for friends (leave blank to skip)"
            $PublicBaseUrl = $enteredBaseUrl.Trim()
        }
    }

    if ((-not $useQuickTunnel) -and (-not $OpenFirewall)) {
        $OpenFirewall = Read-YesNo "Temporarily open Windows Firewall for TCP $Port while the server is running?"
    }
}

if ($useQuickTunnel) {
    try {
        $resolvedCloudflared = Resolve-CloudflaredExecutable -RequestedPath $CloudflaredExe
        Write-Host ""
        Write-Host "Starting a public temporary tunnel..."
        $quickTunnelInfo = Start-QuickTunnel -ExecutablePath $resolvedCloudflared -PortNumber $Port
        $quickTunnelProcess = $quickTunnelInfo.Process
        $quickTunnelStdOutPath = $quickTunnelInfo.StdOutPath
        $quickTunnelStdErrPath = $quickTunnelInfo.StdErrPath
        $PublicBaseUrl = $quickTunnelInfo.PublicUrl
        $BindHost = "127.0.0.1"
    } catch {
        if ($NoPrompt) {
            throw
        }

        Write-Warning "Public temporary tunnel setup failed: $($_.Exception.Message)"
        Write-Warning "You can keep using LAN mode instead, or rerun later after checking your internet connection."

        if (Read-YesNo "Continue in LAN mode instead?" $true) {
            $useQuickTunnel = $false
            $LanOnly = $true
            Stop-RunningProcess -Process $quickTunnelProcess -Label "Public temporary tunnel"
            $quickTunnelProcess = $null
            if ($suggestedBaseUrl) {
                $enteredBaseUrl = Read-Host "Share homepage URL for friends (Enter = $suggestedBaseUrl)"
                $PublicBaseUrl = if ([string]::IsNullOrWhiteSpace($enteredBaseUrl)) { $suggestedBaseUrl } else { $enteredBaseUrl.Trim() }
            } else {
                $enteredBaseUrl = Read-Host "Share homepage URL for friends (leave blank to skip)"
                $PublicBaseUrl = $enteredBaseUrl.Trim()
            }
            if (-not $OpenFirewall) {
                $OpenFirewall = Read-YesNo "Temporarily open Windows Firewall for TCP $Port while the server is running?"
            }
            $BindHost = "0.0.0.0"
        } else {
            throw
        }
    }
}

if ($OpenFirewall) {
    try {
        & $firewallScriptPath -Port $Port -RuleName $temporaryFirewallRuleName
        $temporaryFirewallOpened = $true
    } catch {
        Write-Warning "Firewall setup was skipped: $($_.Exception.Message)"
        Write-Warning "You can still run the server now, but direct LAN/public-IP access may not work until the port is opened."
    }
}

$pythonArgs = @(
    $runPath,
    "--host", $BindHost,
    "--port", $Port.ToString()
)

if ($PublicBaseUrl) {
    $pythonArgs += @("--public-base-url", $PublicBaseUrl)
}

Write-Host ""
Write-Host "Starting Wujiang server..."
Write-Host "Local browser URL: http://127.0.0.1:$Port/"
if ($PublicBaseUrl) {
    if ($useQuickTunnel) {
        Write-Host "Public temporary URL: $PublicBaseUrl/"
        Write-Host "Room invite links will use this public URL."
        Write-Host "This quick tunnel is for online testing and may change every time you restart."
    } else {
        Write-Host "Share homepage URL: $PublicBaseUrl/"
        Write-Host "Room invite links will use this address."
    }
} elseif ($suggestedBaseUrl) {
    Write-Host "Suggested share homepage URL: $suggestedBaseUrl/"
}
Write-Host ""

if ((-not $NoBrowser) -and $PublicBaseUrl) {
    try {
        Start-Process $PublicBaseUrl | Out-Null
    } catch {
        Write-Warning "Could not open the browser automatically: $($_.Exception.Message)"
    }
}

try {
    & $PythonExe @pythonArgs
} finally {
    Stop-RunningProcess -Process $quickTunnelProcess -Label "Public temporary tunnel"

    if ($temporaryFirewallOpened) {
        try {
            & $firewallScriptPath -Port $Port -RuleName $temporaryFirewallRuleName -Remove
            Write-Host "Temporary Windows Firewall rule closed for TCP $Port."
        } catch {
            Write-Warning "Temporary firewall cleanup failed: $($_.Exception.Message)"
            Write-Warning "Run scripts\\open_firewall_port.ps1 -Port $Port -RuleName '$temporaryFirewallRuleName' -Remove as Administrator if needed."
        }
    }
}
