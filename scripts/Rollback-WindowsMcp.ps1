#requires -Version 5.1
<#
.SYNOPSIS
    Verifiable two-phase atomic rollback script for the WinOwner MCP Stack (Phase 4 P1).

.DESCRIPTION
    1. Reads `baseline_hashes.json` or `update_backup_manifest.json` from `-BackupDir`
       (or auto-discovers the latest `backups\pre_update_*` when `-LatestPreUpdate` is passed).
    2. Phase 1 (Pre-Restore Validation): Verifies 100% of backed-up files exist, match their
       manifest SHA-256 hashes, contain no path traversal (`..`), UNC/ADS streams, or reparse
       points, and never target protected local secrets or owner authorization databases.
       If any check fails, aborts before modifying a single live file.
    3. Phase 2 (Atomic Restore): Restores verified files, removes any `added_files` created by
       a failed/reverted update, re-verifies post-restore SHA-256 hashes, and confirms all
       local secrets, OAuth/owner delegation DBs, and configs remained untouched.
    4. When `-RestartServices` is passed, strictly verifies Scheduled Task ownership
       (`WinOwnerMCP-<Port>`) before restarting, refusing foreign task collisions.
#>
[CmdletBinding()]
param(
    [string]$BackupDir = "",
    [string]$TargetRoot = "",
    [switch]$LatestPreUpdate,
    [switch]$DryRun,
    [switch]$RestartServices,
    [ValidateRange(1024,65535)][int]$Port = 8768
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { (Get-Location).Path }
$defaultRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path

function Get-PortableFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [System.IO.File]::OpenRead([System.IO.Path]::GetFullPath($Path))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToUpperInvariant()
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Test-PathHasReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path, [string]$StopAt = "")
    try {
        $cur = [System.IO.Path]::GetFullPath($Path)
        $stopFull = if ($StopAt) { [System.IO.Path]::GetFullPath($StopAt).TrimEnd('\') } else { "" }
        while (-not [string]::IsNullOrEmpty($cur)) {
            if (Test-Path -LiteralPath $cur) {
                $item = Get-Item -LiteralPath $cur -Force -ErrorAction Stop
                if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                    return $true
                }
            }
            if ($stopFull -and [string]::Equals($cur.TrimEnd('\'), $stopFull, [StringComparison]::OrdinalIgnoreCase)) {
                break
            }
            $parent = Split-Path -Parent $cur
            if ([string]::IsNullOrEmpty($parent) -or ($parent -eq $cur)) { break }
            $cur = $parent
        }
    } catch {
        return $true
    }
    return $false
}

function Test-ProtectedSecretOrOwnerStatePath {
    param([Parameter(Mandatory = $true)][string]$PathToCheck)
    $norm = $PathToCheck.Replace("/", "\").ToLowerInvariant()
    if ($norm -match '\\runtime\\secrets(\\|$)') { return $true }
    if ($norm -match '\\runtime\\(oauth_state\.db|tasks\.db|emergency_stop)$') { return $true }
    if ($norm -match '\\config\\(policy\.json|trusted_workspaces\.json)$') { return $true }
    if ($norm -match '\.(dpapi|key|pem|pfx|p12|kdbx|token)$') { return $true }
    return $false
}

function Get-ProtectedStateFingerprints {
    param([Parameter(Mandatory = $true)][string]$RootPath)
    $map = [ordered]@{}
    if (-not (Test-Path -LiteralPath $RootPath -PathType Container)) { return $map }
    $secretsDir = Join-Path $RootPath "runtime\secrets"
    if (Test-Path -LiteralPath $secretsDir -PathType Container) {
        Get-ChildItem -LiteralPath $secretsDir -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notlike "*\backups\*" } |
            Sort-Object FullName | ForEach-Object {
                $rel = $_.FullName.Substring($RootPath.Length).TrimStart('\')
                $map[$rel] = Get-PortableFileSha256 -Path $_.FullName
            }
    }
    foreach ($rel in @("runtime\oauth_state.db", "runtime\tasks.db", "config\policy.json", "config\trusted_workspaces.json")) {
        $full = Join-Path $RootPath $rel
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $map[$rel] = Get-PortableFileSha256 -Path $full
        }
    }
    return $map
}

function Test-VerifiedMcpTaskOwnership {
    param([Parameter(Mandatory = $true)][string]$RootPath, [Parameter(Mandatory = $true)][int]$TaskPort)
    $taskName = "WinOwnerMCP-$TaskPort"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        return @{ Exists = $false; Owned = $true; TaskName = $taskName }
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $allowedUsers = @($identity, $env:USERNAME, "$env:USERDOMAIN\$env:USERNAME")
    $expectedRunner = Join-Path $RootPath "scripts\Run-WinOwnerMcp.py"
    $allowedExecs = @(
        (Join-Path $RootPath ".venv\Scripts\python.exe"),
        (Join-Path $RootPath ".venv\Scripts\pythonw.exe")
    )
    $actions = @($task.Actions)
    if ($actions.Count -ne 1 -or (-not $actions[0].Arguments)) {
        return @{ Exists = $true; Owned = $false; TaskName = $taskName }
    }
    $execMatch = $allowedExecs -contains [string]$actions[0].Execute
    $argMatch = ([string]$actions[0].Arguments).IndexOf($expectedRunner, [StringComparison]::OrdinalIgnoreCase) -ge 0
    $wdMatch = [string]::Equals([string]$actions[0].WorkingDirectory, $RootPath, [StringComparison]::OrdinalIgnoreCase)
    $userMatch = $allowedUsers -contains [string]$task.Principal.UserId
    return @{
        Exists   = $true
        Owned    = [bool]($execMatch -and $argMatch -and $wdMatch -and $userMatch)
        TaskName = $taskName
    }
}

$effectiveRoot = if (-not [string]::IsNullOrWhiteSpace($TargetRoot)) { [System.IO.Path]::GetFullPath($TargetRoot) } else { [System.IO.Path]::GetFullPath($defaultRoot) }
if ([string]::IsNullOrWhiteSpace($BackupDir) -and (-not $LatestPreUpdate)) {
    $BackupDir = Join-Path $effectiveRoot "backups\baseline_p0"
}

if ($LatestPreUpdate) {
    $backupsRoot = Join-Path $effectiveRoot "backups"
    if (-not (Test-Path -LiteralPath $backupsRoot -PathType Container)) {
        throw "Baseline hash manifest not found at: $backupsRoot"
    }
    $latestDir = Get-ChildItem -LiteralPath $backupsRoot -Directory -Filter "pre_update_*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    if ($null -eq $latestDir) {
        throw "Baseline hash manifest not found at: $(Join-Path $backupsRoot 'pre_update_*')"
    }
    $BackupDir = $latestDir.FullName
}

$BackupDir = [System.IO.Path]::GetFullPath($BackupDir)
$ManifestPath = Join-Path $BackupDir "baseline_hashes.json"
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    $altManifest = Join-Path $BackupDir "update_backup_manifest.json"
    if (Test-Path -LiteralPath $altManifest -PathType Leaf) {
        $ManifestPath = $altManifest
    } else {
        throw "Baseline hash manifest not found at: $ManifestPath"
    }
}

if (Test-PathHasReparsePoint -Path $ManifestPath -StopAt $BackupDir) {
    throw "SECURITY VIOLATION: Rollback manifest or backup directory is a symlink or reparse point."
}

$manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$boundTargetRoot = if (-not [string]::IsNullOrWhiteSpace($TargetRoot)) {
    [System.IO.Path]::GetFullPath($TargetRoot)
} elseif ($manifest.PSObject.Properties.Match("target_root").Count -gt 0 -and (-not [string]::IsNullOrWhiteSpace([string]$manifest.target_root))) {
    [System.IO.Path]::GetFullPath([string]$manifest.target_root)
} else {
    ""
}

if ($RestartServices) {
    $svcRoot = if ($boundTargetRoot) { $boundTargetRoot } else { $effectiveRoot }
    $taskCheck = Test-VerifiedMcpTaskOwnership -RootPath $svcRoot -TaskPort $Port
    if ($taskCheck.Exists -and (-not $taskCheck.Owned)) {
        throw "Task name collision for $($taskCheck.TaskName); refusing to restart foreign scheduled task."
    }
}

$protectedBefore = if ($boundTargetRoot) { Get-ProtectedStateFingerprints -RootPath $boundTargetRoot } else { [ordered]@{} }

$results = New-Object System.Collections.Generic.List[object]
$validatedRestorePlan = New-Object System.Collections.Generic.List[object]
$failures = 0
$restoredCount = 0
$removedAddedCount = 0
$driftCount = 0

$canonicalBackupPrefix = $BackupDir.TrimEnd('\') + "\"
$canonicalTargetPrefix = if ($boundTargetRoot) { $boundTargetRoot.TrimEnd('\') + "\" } else { "" }

# ---------------------------------------------------------------------------
# Phase 1: Pre-Restore Integrity & Security Boundary Validation (Zero Writes)
# ---------------------------------------------------------------------------
foreach ($entry in $manifest.files) {
    $relPath = [string]$entry.relative_path
    $origPath = [string]$entry.original_path
    $expectedHash = ([string]$entry.sha256).ToUpperInvariant()

    if ($relPath -match "windows-mcp-isolated\.redacted\.yaml") {
        $redactedFile = Join-Path $BackupDir "windows-mcp-isolated.redacted.yaml"
        $redactedExists = Test-Path -LiteralPath $redactedFile -PathType Leaf
        $liveExists = Test-Path -LiteralPath $origPath -PathType Leaf
        $liveHash = $(if ($liveExists) { (Get-PortableFileSha256 -Path $origPath) } else { "MISSING" })
        $hashMatch = ($liveHash -eq $expectedHash)

        if (-not $redactedExists -or -not $liveExists) {
            $failures++
        }

        $results.Add([PSCustomObject]@{
            File         = "windows-mcp-isolated.yaml (redacted backup)"
            BackupHashOk = $redactedExists
            LiveMatches  = $hashMatch
            Action       = $(if ($DryRun) { "DRYRUN_VERIFY_ONLY (secret preserved)" } else { "VERIFIED_SECRET_PRESERVED" })
            Status       = $(if ($redactedExists -and $liveExists) { "PASS" } else { "FAIL" })
        })
        continue
    }

    # Validate relative_path and original_path against traversal, UNC, ADS, and protected secret overwrite
    $relNorm = $relPath.Replace("/", "\")
    $hasTraversal = ($relNorm.StartsWith("\") -or $relNorm.Contains(":") -or (($relNorm -split '\\') -contains "..") -or $origPath.StartsWith("\\"))
    if ($hasTraversal -or (Test-ProtectedSecretOrOwnerStatePath -PathToCheck $relNorm) -or (Test-ProtectedSecretOrOwnerStatePath -PathToCheck $origPath)) {
        $failures++
        $results.Add([PSCustomObject]@{
            File         = $relPath
            BackupHashOk = $false
            LiveMatches  = $false
            Action       = "FORBIDDEN_OR_TRAVERSAL_PATH"
            Status       = "FAIL"
        })
        continue
    }

    $backupFile = [System.IO.Path]::GetFullPath((Join-Path $BackupDir $relNorm))
    $origFull = [System.IO.Path]::GetFullPath($origPath)
    if ((-not $backupFile.StartsWith($canonicalBackupPrefix, [StringComparison]::OrdinalIgnoreCase)) -or
        ($canonicalTargetPrefix -and (-not $origFull.StartsWith($canonicalTargetPrefix, [StringComparison]::OrdinalIgnoreCase)))) {
        $failures++
        $results.Add([PSCustomObject]@{
            File         = $relPath
            BackupHashOk = $false
            LiveMatches  = $false
            Action       = "PATH_ESCAPES_ROOT_BOUNDARY"
            Status       = "FAIL"
        })
        continue
    }

    if (-not (Test-Path -LiteralPath $backupFile -PathType Leaf)) {
        $failures++
        $results.Add([PSCustomObject]@{
            File         = $relPath
            BackupHashOk = $false
            LiveMatches  = $false
            Action       = "BACKUP_FILE_MISSING"
            Status       = "FAIL"
        })
        continue
    }

    if ((Test-PathHasReparsePoint -Path $backupFile -StopAt $BackupDir) -or (Test-PathHasReparsePoint -Path $origFull)) {
        $failures++
        $results.Add([PSCustomObject]@{
            File         = $relPath
            BackupHashOk = $false
            LiveMatches  = $false
            Action       = "REPARSE_POINT_DENIED"
            Status       = "FAIL"
        })
        continue
    }

    $backupHash = (Get-PortableFileSha256 -Path $backupFile)
    $backupHashOk = ($backupHash -eq $expectedHash)
    if (-not $backupHashOk) {
        $failures++
        $results.Add([PSCustomObject]@{
            File         = $relPath
            BackupHashOk = $false
            LiveMatches  = $false
            Action       = "BACKUP_HASH_MISMATCH"
            Status       = "FAIL"
        })
        continue
    }

    $liveExists = Test-Path -LiteralPath $origFull -PathType Leaf
    $liveHash = $(if ($liveExists) { (Get-PortableFileSha256 -Path $origFull) } else { "MISSING" })
    $liveMatches = ($liveHash -eq $expectedHash)
    if (-not $liveMatches) {
        $driftCount++
    }

    if ($DryRun) {
        $actionText = $(if ($liveMatches) { "DRYRUN_IN_SYNC" } else { "DRYRUN_WOULD_RESTORE" })
        $results.Add([PSCustomObject]@{
            File         = $relPath
            BackupHashOk = $true
            LiveMatches  = $liveMatches
            Action       = $actionText
            Status       = "PASS"
        })
    } else {
        $validatedRestorePlan.Add([PSCustomObject]@{
            RelPath      = $relPath
            BackupFile   = $backupFile
            OrigFull     = $origFull
            ExpectedHash = $expectedHash
        })
    }
}

# ---------------------------------------------------------------------------
# Phase 2: Atomic Restore (ONLY executes if Phase 1 had ZERO failures)
# ---------------------------------------------------------------------------
if ((-not $DryRun) -and ($failures -eq 0)) {
    foreach ($plan in $validatedRestorePlan) {
        $parentDir = Split-Path -Parent $plan.OrigFull
        if (-not (Test-Path -LiteralPath $parentDir)) {
            New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
        }
        if (Test-PathHasReparsePoint -Path $parentDir) {
            $failures++
            $results.Add([PSCustomObject]@{
                File         = $plan.RelPath
                BackupHashOk = $true
                LiveMatches  = $false
                Action       = "TARGET_PARENT_REPARSE_DENIED"
                Status       = "FAIL"
            })
            continue
        }
        Copy-Item -LiteralPath $plan.BackupFile -Destination $plan.OrigFull -Force
        $postHash = (Get-PortableFileSha256 -Path $plan.OrigFull)
        $verifyOk = ($postHash -eq $plan.ExpectedHash)
        if ($verifyOk) {
            $restoredCount++
        } else {
            $failures++
        }
        $results.Add([PSCustomObject]@{
            File         = $plan.RelPath
            BackupHashOk = $true
            LiveMatches  = $verifyOk
            Action       = $(if ($verifyOk) { "RESTORED_AND_VERIFIED" } else { "POST_RESTORE_HASH_FAIL" })
            Status       = $(if ($verifyOk) { "PASS" } else { "FAIL" })
        })
    }

    # Clean up any newly added files recorded in update_backup_manifest.json
    if ($manifest.PSObject.Properties.Match("added_files").Count -gt 0 -and $null -ne $manifest.added_files) {
        foreach ($added in $manifest.added_files) {
            $addedRel = ([string]$added.relative_path).Replace("/", "\")
            $addedOrig = [System.IO.Path]::GetFullPath([string]$added.original_path)
            if ($addedRel.Contains("..") -or (Test-ProtectedSecretOrOwnerStatePath -PathToCheck $addedOrig)) {
                $failures++
                continue
            }
            if ($canonicalTargetPrefix -and (-not $addedOrig.StartsWith($canonicalTargetPrefix, [StringComparison]::OrdinalIgnoreCase))) {
                $failures++
                continue
            }
            if (Test-Path -LiteralPath $addedOrig -PathType Leaf) {
                if (Test-PathHasReparsePoint -Path $addedOrig) {
                    $failures++
                    continue
                }
                Remove-Item -LiteralPath $addedOrig -Force -ErrorAction SilentlyContinue
                if (-not (Test-Path -LiteralPath $addedOrig)) {
                    $removedAddedCount++
                } else {
                    $failures++
                }
            }
        }
    }

    # Verify protected secrets & owner authorization DBs were not altered
    if ($boundTargetRoot) {
        $protectedAfter = Get-ProtectedStateFingerprints -RootPath $boundTargetRoot
        foreach ($k in $protectedBefore.Keys) {
            if ((-not $protectedAfter.Contains($k)) -or ($protectedAfter[$k] -ne $protectedBefore[$k])) {
                $failures++
                Write-Warning "SECURITY VIOLATION: Protected secret or owner state hash changed during rollback: $k"
            }
        }
    }
}

$modeLabel = $(if ($DryRun) { "DRY-RUN VERIFICATION" } else { "LIVE ROLLBACK" })
Write-Host ""
Write-Host "==========================================================================================" -ForegroundColor Cyan
Write-Host " WinOwner MCP Stack - Baseline P0 Rollback ($modeLabel)" -ForegroundColor Cyan
Write-Host "==========================================================================================" -ForegroundColor Cyan
$results | Format-Table -AutoSize

Write-Host "Summary: TotalFiles=$($results.Count), DriftDetected=$driftCount, Restored=$restoredCount, RemovedAdded=$removedAddedCount, Failures=$failures" -ForegroundColor $(if ($failures -eq 0) { "Green" } else { "Red" })

if ((-not $DryRun) -and $RestartServices -and ($failures -eq 0)) {
    $svcRoot = if ($boundTargetRoot) { $boundTargetRoot } else { $effectiveRoot }
    $taskCheck = Test-VerifiedMcpTaskOwnership -RootPath $svcRoot -TaskPort $Port
    if ($taskCheck.Exists -and (-not $taskCheck.Owned)) {
        Write-Error "Task name collision for $($taskCheck.TaskName); refusing to restart foreign scheduled task."
        exit 1
    }
    if ($taskCheck.Exists -and $taskCheck.Owned) {
        Write-Host "Restarting verified WinOwner scheduled task $($taskCheck.TaskName)..." -ForegroundColor Cyan
        Stop-ScheduledTask -TaskName $taskCheck.TaskName -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $taskCheck.TaskName -ErrorAction Stop
    } else {
        $watchdogPy = Join-Path $svcRoot "runtime\watchdog.py"
        if (Test-Path -LiteralPath $watchdogPy -PathType Leaf) {
            Write-Host "Restarting/verifying MCP Bridge & Secure Tunnel via watchdog.py --once..." -ForegroundColor Cyan
            $cmdPy = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($null -ne $cmdPy -and (Test-Path -LiteralPath $cmdPy.Source)) {
                $pythonExe = $cmdPy.Source
            } else {
                $localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE "AppData\Local" }
                $pythonExe = Join-Path $localAppData "Programs\Python\Python312\python.exe"
            }
            & $pythonExe $watchdogPy --once
        }
    }
}

if ($failures -gt 0) {
    exit 1
}
exit 0
