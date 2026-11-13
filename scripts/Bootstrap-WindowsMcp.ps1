#requires -Version 5.1
<#
WinOwner MCP safe local bootstrap.
Installs authenticated local adapter; registers restartable current-user Task Scheduler task.
Owner full-control mode only applies to an authenticated owner; anonymous access remains denied. Remote ChatGPT OAuth pairing is a separate required step.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$NoStart,
    [switch]$NoPythonInstall,
    [switch]$OwnerFullControl,
    [switch]$SwitchProfile,
    [switch]$Upgrade,
    [switch]$Remove,
    [ValidateRange(1024,65535)][int]$Port = 8768
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$venv = Join-Path $root ".venv\Scripts\python.exe"
$venvW = Join-Path $root ".venv\Scripts\pythonw.exe"
$runner = Join-Path $root "scripts\Run-WinOwnerMcp.py"
$adapter = Join-Path $root "mcp-gateway\cloud_autonomous_adapter.py"
$policyExample = Join-Path $root "config\policy.example.json"
$policyPath = Join-Path $root "config\policy.json"
$secretDir = Join-Path $root "runtime\secrets"
$tokenPath = Join-Path $secretDir "gateway_caller.token"
$ownerKeyPath = Join-Path $secretDir "owner_admin.key"
$logDir = Join-Path $root "runtime\logs"
$taskName = "WinOwnerMCP-$Port"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$profile = if ($OwnerFullControl) { 'owner_full_control' } else { 'read_only' }
function Step([string]$s) { Write-Host "[WinOwner] $s" }
function Check-Exit([string]$s) { if ($LASTEXITCODE -ne 0) { throw "$s failed: $LASTEXITCODE" } }
function Port-IsFree {
    $l = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try { $l.Start(); return $true } catch { return $false } finally { try { $l.Stop() } catch {} }
}
function Find-Python {
    $candidates = @()
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { $candidates += ,@($py.Source, "-3.12") }
    if ($env:LOCALAPPDATA) { $candidates += ,@((Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")) }
    $exe = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($exe) { $candidates += ,@($exe.Source) }
    foreach ($c in $candidates) {
        if (-not (Test-Path -LiteralPath $c[0])) { continue }
        $p = @()
        if ($c.Count -gt 1) { $p = @($c[1]) }
        try {
            $null = & $c[0] @p -c "import sys; assert sys.version_info[:2] == (3,12)" 2>$null
            if ($LASTEXITCODE -eq 0) { return @{Path=$c[0]; Args=$p} }
        } catch {}
    }
    return $null
}
function Managed-Task {
    $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($t) {
        $a = @($t.Actions)
        $hasValidArgs = ($a.Count -eq 1 -and $a[0].Arguments)
        if ($hasValidArgs) {
            if ($Upgrade) {
                $execOk = $a[0].Execute -match '(?i)\\\.venv\\Scripts\\pythonw?\.exe$'
                $runnerOk = ($a[0].Arguments -match '(?i)scripts\\Run-WinOwnerMcp\.py') -and ($a[0].Arguments -match ("(?<!\S)--port\s+" + $Port + "(?=\s|$)"))
            } else {
                $execOk = ($a[0].Execute.IndexOf($venv, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
                          ($a[0].Execute.IndexOf($venvW, [StringComparison]::OrdinalIgnoreCase) -ge 0)
                $runnerOk = $a[0].Arguments.IndexOf($runner, [StringComparison]::OrdinalIgnoreCase) -ge 0
            }
        } else {
            $execOk = $false
            $runnerOk = $false
        }
        if (-not $hasValidArgs -or (-not $runnerOk) -or (-not $execOk) -or
            @($identity, $env:USERNAME, ("$env:USERDOMAIN\$env:USERNAME")) -notcontains $t.Principal.UserId) {
            throw "Task name collision for $taskName; refusing to modify another task."
        }
        # Refuse silently changing the profile of an existing task unless -SwitchProfile or -Upgrade is explicitly set.
        # Allow -Remove to cleanly remove either verified mode.
        if (-not $Remove -and -not $SwitchProfile -and -not $Upgrade) {
            $taskIsFull = $a[0].Arguments -match '(?i)(?:^|\s)--profile\s+owner_full_control(?:\s|$)'
            if ($taskIsFull -ne [bool]$OwnerFullControl) {
                throw "Existing WinOwner task uses a different profile; use -SwitchProfile or explicitly remove the task."
            }
        }
    }
    return $t
}
function Healthy-And-Authenticated {
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($h.status -ne "ok" -or $h.profile -ne $profile -or $h.require_autonomous_auth -ne $true -or
            $h.allow_loopback_tunnel_identity -ne $false) { return $false }
        try {
            $body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
            $null = Invoke-WebRequest "http://127.0.0.1:$Port/mcp" -Method Post -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 3
            return $false
        } catch {
            return [bool]($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401)
        }
    } catch { return $false }
}
if ([Environment]::OSVersion.Platform.ToString() -ne "Win32NT") { throw "Windows required" }
function Stop-VerifiedLocalListener {
    $listeners = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $listener.OwningProcess)
        $isRunner = ($proc.CommandLine.IndexOf($runner, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
                    ($proc.CommandLine.IndexOf($adapter, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
                    ($Upgrade -and ($proc.CommandLine -match '(?i)scripts\\Run-WinOwnerMcp\.py'))
        if (-not $proc -or -not $proc.CommandLine -or (-not $isRunner) -or
            $proc.CommandLine -notmatch ("(?<!\S)--port\s+" + $Port + "(?=\s|$)")) {
            throw "Refusing to stop unverified listener on port $Port."
        }
        Stop-Process -Id $listener.OwningProcess -Force -ErrorAction Stop
    }
}
if ($Remove) {
    $task = Managed-Task
    if ($task) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    }
    # Task Scheduler may take several seconds to terminate the service after
    # Stop/Unregister on a busy CI runner. Wait for clean exit before attempting
    # any verified process-level stop; never kill an unrecognized port owner.
    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    while ((-not (Port-IsFree)) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    if (-not (Port-IsFree)) {
        Stop-VerifiedLocalListener
        Start-Sleep -Milliseconds 400
    }
    if (-not (Port-IsFree)) { throw "Removal incomplete: port $Port remains occupied." }
    Step "TASK_REMOVED: $taskName, verified local listener stopped. Credentials/config kept for owner review."
    exit 0
}
foreach ($p in @($runner, $adapter, $policyExample, (Join-Path $root "requirements.txt"))) {
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "Missing release file: $p" }
}
$task = Managed-Task
$free = Port-IsFree
if ($DryRun) {
    $py = Find-Python
    Step "DRY_RUN profile=$profile Python312=$([bool]$py) port_free=$free task_exists=$([bool]$task)"
    if (-not $py -and $NoPythonInstall) { throw "Python 3.12 unavailable" }
    exit 0
}
if ($task -and -not $SwitchProfile -and -not $Upgrade -and (Healthy-And-Authenticated)) {
    Step "LOCAL_INSTALL_PASS: existing managed task is healthy; no changes made."
    exit 0
}
if ($task -and ($SwitchProfile -or $Upgrade)) {
    if ($Upgrade -and -not $SwitchProfile -and -not $OwnerFullControl) {
        if ($task.Actions[0].Arguments -match '(?i)(?:^|\s)--profile\s+owner_full_control(?:\s|$)') {
            $profile = 'owner_full_control'
        }
    }
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    while ((-not (Port-IsFree)) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    if (-not (Port-IsFree)) {
        Stop-VerifiedLocalListener
        Start-Sleep -Milliseconds 400
    }
    $free = Port-IsFree
}
if (-not $free) { throw "Port $Port occupied by an unverified process; no changes made." }
$py = Find-Python
if (-not $py) {
    if ($NoPythonInstall) { throw "Python 3.12 missing" }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) { throw "Python 3.12 missing and winget unavailable" }
    & $winget.Source install --id Python.Python.3.12 --exact --scope user --silent --accept-source-agreements --accept-package-agreements
    Check-Exit "Python install"
    $py = Find-Python
    if (-not $py) { throw "Open a new PowerShell after installing Python 3.12 and rerun" }
}
if (-not (Test-Path -LiteralPath $venv)) {
    & $py.Path @($py.Args) -m venv (Join-Path $root ".venv")
    Check-Exit "venv"
}
Step "Installing/verifying pinned dependencies."
& $venv -m pip install --disable-pip-version-check --quiet -r (Join-Path $root "requirements.txt")
Check-Exit "Python dependencies"
New-Item -ItemType Directory -Path $secretDir, $logDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $policyPath)) {
    Copy-Item -LiteralPath $policyExample -Destination $policyPath
}
$policy = Get-Content $policyPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($policy.default_profile -ne "read_only") { throw "Refusing non-read_only policy" }
& icacls.exe $secretDir /inheritance:r /grant:r "$($identity):(OI)(CI)F" "SYSTEM:(OI)(CI)F" | Out-Null
Check-Exit "private directory ACL"
if (-not (Test-Path -LiteralPath $tokenPath)) {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    [IO.File]::WriteAllText($tokenPath, [Convert]::ToBase64String($bytes), [Text.UTF8Encoding]::new($false))
    [Array]::Clear($bytes,0,$bytes.Length)
}
& icacls.exe $tokenPath /inheritance:r /grant:r "$($identity):(F)" "SYSTEM:(F)" | Out-Null
Check-Exit "private token ACL"
if (-not (Test-Path -LiteralPath $ownerKeyPath)) {
    $obytes = New-Object byte[] 36
    $orng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $orng.GetBytes($obytes) } finally { $orng.Dispose() }
    $b64url = [Convert]::ToBase64String($obytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
    [Array]::Clear($obytes, 0, $obytes.Length)
    [IO.File]::WriteAllText($ownerKeyPath, ("mcp_oadm_" + $b64url + "`n"), [Text.UTF8Encoding]::new($false))
}
& icacls.exe $ownerKeyPath /inheritance:r /grant:r "$($identity):(F)" "SYSTEM:(F)" | Out-Null
Check-Exit "owner admin key ACL"
function Assert-RestrictedAcl([string]$targetPath) {
    $aclOut = (& icacls.exe $targetPath 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "icacls check failed for $targetPath" }
    if ($aclOut -match '(?i)(Everyone|BUILTIN\\Users|NT AUTHORITY\\Authenticated Users)') {
        throw "Unsafe ACL detected on $targetPath"
    }
}
Assert-RestrictedAcl $tokenPath
Assert-RestrictedAcl $ownerKeyPath
if ($NoStart) { Step "LOCAL_PREPARED: no task started or registered"; exit 0 }
$created = $false
$previousAction = $null
$taskPython = if (Test-Path -LiteralPath $venvW) { $venvW } else { $venv }
$arguments = ('-u "{0}" --port {1} --profile {2}' -f $runner, $Port, $profile)
$action = New-ScheduledTaskAction -Execute $taskPython -Argument $arguments -WorkingDirectory $root
if (-not $task) {
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "WinOwner MCP $profile local adapter, current-user logon" | Out-Null
    $created = $true
} elseif ($SwitchProfile -or $Upgrade) {
    $previousAction = $task.Actions[0]
    Set-ScheduledTask -TaskName $taskName -Action $action | Out-Null
}
try {
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $ready = $false
    for ($i=0; $i -lt 60; $i++) {
        if (Healthy-And-Authenticated) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "LOCAL_INSTALL_FAILED: health or anonymous-denial test failed" }
    $task = Managed-Task
    if ($task.State -ne "Running") { throw "LOCAL_INSTALL_FAILED: managed task not running" }
    $report = [ordered]@{
        status="LOCAL_INSTALL_PASS"; url="http://127.0.0.1:$Port/mcp";
        task_name=$taskName; auto_start_at_owner_logon=$true;
        profile=$profile; anonymous_mcp_denied=$true;
        remote_oauth_paired=$false; owner_global_filesystem_enabled=[bool]$OwnerFullControl;
        full_control_requires_verified_owner=$true
    }
    [IO.File]::WriteAllText((Join-Path $logDir "bootstrap-report.json"),($report | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    Step "LOCAL_INSTALL_PASS: $taskName running ($profile); auto-start at owner logon."
    if ($OwnerFullControl) { Step "Full-control tools require verified owner OAuth and an active owner delegation." }
    Step "Remote OAuth/tunnel pairing is NOT complete; consult docs/CONNECT_CHATGPT.md."
} catch {
    if ($created) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    } elseif ($previousAction) {
        Step "Upgrade health check failed; rolling back scheduled task to previous version."
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        try {
            Set-ScheduledTask -TaskName $taskName -Action $previousAction | Out-Null
            Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        } catch {}
    }
    throw
}
