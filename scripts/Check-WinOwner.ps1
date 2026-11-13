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
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    foreach ($secName in @("gateway_caller.token", "owner_admin.key")) {
        $secPath = Join-Path $root ("runtime\secrets\" + $secName)
        if (-not (Test-Path -LiteralPath $secPath -PathType Leaf)) {
            throw "Required secret file missing: runtime\secrets\$secName"
        }
        $aclOut = (& icacls.exe $secPath 2>&1) -join "`n"
        if ($LASTEXITCODE -ne 0 -or $aclOut -match '(?i)(Everyone|BUILTIN\\Users|NT AUTHORITY\\Authenticated Users)') {
            throw "Secret file ACL check failed for runtime\secrets\$secName"
        }
    }
    Write-Host "CHECK_PASS: local MCP is running, profile=$($health.profile), secret ACLs verified, anonymous access blocked." -ForegroundColor Green
    Write-Host "This check does NOT verify that ChatGPT account pairing is complete."
    exit 0
} catch {
    Write-Host "CHECK_FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Open docs\START_HERE.vi.md for beginner troubleshooting."
    exit 1
}