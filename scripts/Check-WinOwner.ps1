#requires -Version 5.1
[CmdletBinding()]
param([ValidateRange(1024, 65535)][int]$Port = 8768)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$taskName = "WinOwnerMCP-$Port"
try {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    if ($task.State -ne "Running") { throw "Local MCP task is not running (state: $($task.State))." }
    $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($health.status -ne "ok" -or $health.require_autonomous_auth -ne $true -or
        $health.allow_loopback_tunnel_identity -ne $false -or
        $health.profile -notin @("owner_full_control", "read_only")) {
        throw "Local service health or authentication check failed."
    }
    $body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
    try {
        $null = Invoke-WebRequest "http://127.0.0.1:$Port/mcp" -Method Post -Body $body -UseBasicParsing -ContentType "application/json" -TimeoutSec 5
        throw "Unauthenticated tool discovery must be denied."
    } catch {
        if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 401) {
            throw "Unauthenticated tool discovery did not return HTTP 401."
        }
    }
    Write-Host "CHECK_PASS: local MCP is running, profile=$($health.profile), anonymous access blocked." -ForegroundColor Green
    $tunnelTaskName = "WinOwnerTunnel-$Port"
    $tunnelTask = Get-ScheduledTask -TaskName $tunnelTaskName -TaskPath '\' -ErrorAction SilentlyContinue
    if (-not $tunnelTask) {
        Write-Host "TUNNEL_NOT_CONFIGURED: no tunnel task for this port."
    } else {
        $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
        $runner = Join-Path $PSScriptRoot 'Run-WinOwnerTunnel.ps1'
        $actions = @($tunnelTask.Actions)
        if ($actions.Count -ne 1 -or $actions[0].WorkingDirectory -ne $root -or
            $actions[0].Arguments -ne ('-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner+'"')) {
            Write-Warning "TUNNEL_UNVERIFIED: task does not match this installation."
        } elseif ($tunnelTask.State -ne 'Running') {
            Write-Warning "TUNNEL_NOT_RUNNING: state=$($tunnelTask.State). Check Task Scheduler."
        } else {
            $healthFile = Join-Path $root 'runtime\tunnel\health-url.txt'
            $ready = $false
            if (Test-Path -LiteralPath $healthFile -PathType Leaf) {
                try {
                    $url = ([IO.File]::ReadAllText($healthFile)).Trim()
                    if ($url -match '^http://127\.0\.0\.1:[0-9]{1,5}/?$') {
                        $response = Invoke-WebRequest -Uri ($url.TrimEnd('/')+'/readyz') -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0
                        $ready = ($response.StatusCode -eq 200)
                    }
                } catch {}
            }
            if ($ready) { Write-Host 'TUNNEL_READY: local tunnel readiness probe passed.' -ForegroundColor Green }
            else { Write-Warning 'TUNNEL_NOT_READY: task running, readiness not verified.' }
        }
    }
    Write-Host "This check does NOT verify that ChatGPT account pairing is complete."
    exit 0
} catch {
    Write-Host "CHECK_FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Open docs\START_HERE.vi.md for beginner troubleshooting."
    exit 1
}