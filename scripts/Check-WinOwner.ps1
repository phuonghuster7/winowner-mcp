#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 8768,
    [switch]$Json,
    [switch]$OpenSetupCenter
)
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
    $tunnelReady = $false
    $tunnelStateCode = "TUNNEL_NOT_CONFIGURED"
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    if (-not $tunnelTask) {
        Write-Host "TUNNEL_NOT_CONFIGURED: no tunnel task for this port."
    } else {
        $runner = Join-Path $PSScriptRoot 'Run-WinOwnerTunnel.ps1'
        $actions = @($tunnelTask.Actions)
        if ($actions.Count -ne 1 -or $actions[0].WorkingDirectory -ne $root -or
            $actions[0].Arguments -ne ('-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner+'"')) {
            $tunnelStateCode = "TUNNEL_UNVERIFIED"
            Write-Warning "TUNNEL_UNVERIFIED: task does not match this installation."
        } elseif ($tunnelTask.State -ne 'Running') {
            $tunnelStateCode = "TUNNEL_NOT_RUNNING"
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
            if ($ready) {
                $tunnelReady = $true
                $tunnelStateCode = "TUNNEL_READY"
                Write-Host 'TUNNEL_READY: local tunnel readiness probe passed.' -ForegroundColor Green
            } else {
                $tunnelStateCode = "TUNNEL_NOT_READY"
                Write-Warning 'TUNNEL_NOT_READY: task running, readiness not verified.'
            }
        }
    }

    # Compute 4-Step State Machine & 6-Row Live Status Summary from real backend truth
    $chatgptVerified = [bool]($health.PSObject.Properties['chatgpt_read_tool_verified'] -and $health.chatgpt_read_tool_verified)
    $ownerVerified = [bool]($health.PSObject.Properties['owner_identity_verified'] -and $health.owner_identity_verified)
    $rawPermLevel = if ($health.PSObject.Properties['effective_permission_level'] -and $health.effective_permission_level) { [string]$health.effective_permission_level } else { 'READ' }
    $permLabel = switch ($rawPermLevel.ToUpperInvariant()) {
        'OWNER'  { 'Owner' }
        'SCOPED' { 'Scoped' }
        default  { 'Read' }
    }
    $isEstop = [bool]($health.PSObject.Properties['emergency_stop'] -and $health.emergency_stop)
    $stepState = if (-not $chatgptVerified) {
        'LOCAL_READY'
    } elseif (-not $ownerVerified) {
        'CHATGPT_CONNECTED'
    } elseif ($permLabel -eq 'Read' -or $isEstop) {
        'OWNER_VERIFIED'
    } else {
        'OWNER_CONTROL_READY'
    }
    $relVer = if ($health.PSObject.Properties['release_version'] -and $health.release_version) { [string]$health.release_version } else { 'v1.2.0' }

    Write-Host ("STATUS_TABLE: LocalService=Ready | Tunnel={0} | ChatGPT={1} | OwnerIdentity={2} | Permissions={3} | Version=Current ({4}) | State={5}" -f `
        $(if ($tunnelReady) { 'Connected' } else { 'Disconnected' }), `
        $(if ($chatgptVerified) { 'Verified' } else { 'Not Verified' }), `
        $(if ($ownerVerified) { 'Verified' } else { 'Pending' }), `
        $permLabel, $relVer, $stepState)

    if ($Json) {
        $summaryObj = [ordered]@{
            ok = $true
            state = $stepState
            profile = $health.profile
            tunnel_code = $tunnelStateCode
            status_table = [ordered]@{
                local_service = 'Ready'
                tunnel = $(if ($tunnelReady) { 'Connected' } else { 'Disconnected' })
                chatgpt = $(if ($chatgptVerified) { 'Verified' } else { 'Not Verified' })
                owner_identity = $(if ($ownerVerified) { 'Verified' } else { 'Pending' })
                permissions = $permLabel
                version = 'Current'
            }
        }
        Write-Host ("SETUP_CENTER_STATUS_JSON: " + ($summaryObj | ConvertTo-Json -Compress))
    }

    if ($OpenSetupCenter) {
        $pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
        $mcpRunner = Join-Path $root "scripts\Run-WinOwnerMcp.py"
        if ((Test-Path -LiteralPath $pyw) -and (Test-Path -LiteralPath $mcpRunner)) {
            Start-Process -FilePath $pyw -ArgumentList @("-u", ('"{0}"' -f $mcpRunner), "--port", "$Port", "--profile", "$($health.profile)", "--setup-center", "--open-browser") -WorkingDirectory $root -WindowStyle Hidden
        }
    }

    Write-Host "This check does NOT verify that ChatGPT account pairing is complete."
    exit 0
} catch {
    Write-Host "CHECK_FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Open docs\START_HERE.vi.md for beginner troubleshooting."
    exit 1
}