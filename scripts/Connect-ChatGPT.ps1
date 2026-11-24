#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateRange(1024,65535)][int]$Port=8768,
    [string]$TunnelId='',
    [switch]$DryRun,
    [switch]$NoBrowser,
    [switch]$NoStart,
    [string]$ArchivePath='',
    [string]$ChecksumPath='',
    [switch]$Reconfigure,
    [ValidateSet('auth_none','oauth')][string]$AuthMode='auth_none',
    [Security.SecureString]$ApiKey=$null,
    [string]$TargetRoot='',
    [ValidateRange(2,180)][int]$ReadyTimeoutSeconds=45,
    [switch]$SimulateCutoverFailure
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root=if($TargetRoot){[IO.Path]::GetFullPath($TargetRoot)}else{[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))}
$version='v0.0.20'
$asset="tunnel-client-$version-windows-amd64.zip"
$base="https://github.com/openai/tunnel-client/releases/download/$version"
$manifestHash='897c4823a24336e2cacd53b69abad8842130bef4eb029c703c7bd0d3c5f4a05d'
$private=Join-Path $root 'runtime\secrets'
$clientDir=Join-Path $root 'runtime\tunnel'
$logDir=Join-Path $root 'runtime\logs'
$binary=Join-Path $clientDir 'tunnel-client.exe'
$profile=Join-Path $clientDir 'winowner.yaml'
$gatewayKey=Join-Path $private 'gateway_caller.token'
$planeKey=Join-Path $private 'tunnel_control_plane.key'
$localKey=Join-Path $private 'tunnel_mcp_auth_header.key'
$runner=Join-Path $root 'scripts\Run-WinOwnerTunnel.ps1'
if(-not(Test-Path -LiteralPath $runner -PathType Leaf)){
    $runner=Join-Path $PSScriptRoot 'Run-WinOwnerTunnel.ps1'
}
$taskName="WinOwnerTunnel-$Port"
$healthUrlFile=Join-Path $clientDir 'health-url.txt'
$createdTask=$false
$cutoverStarted=$false
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name

function Get-PortableSha256([string]$path){
    $stream=[IO.File]::OpenRead([IO.Path]::GetFullPath($path))
    $sha=[Security.Cryptography.SHA256]::Create()
    try{
        return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant()
    }finally{
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Protect-File([string]$path){
    & icacls.exe $path /inheritance:r /grant:r "$($user):(F)" 'SYSTEM:(F)' | Out-Null
    if($LASTEXITCODE -ne 0){throw "ACL protection failed: $(Split-Path $path -Leaf)"}
}

function Store-Secret([string]$path,[string]$content){
    if(-not $content){throw 'Secret was empty'}
    [IO.File]::WriteAllText($path,$content,[Text.UTF8Encoding]::new($false))
    Protect-File $path
}

function Test-VerifiedTunnelTaskOwnership($taskObj){
    if($null -eq $taskObj){return $false}
    $actions=@($taskObj.Actions)
    $expectedExec=Join-Path $PSHOME 'powershell.exe'
    $expectedArgs='-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner+'"'
    $allowedUsers=@($user,$env:USERNAME,("$env:USERDOMAIN\$env:USERNAME"))
    if($actions.Count -ne 1){return $false}
    if([string]$actions[0].Execute -ne $expectedExec){return $false}
    if([string]$actions[0].Arguments -ne $expectedArgs){return $false}
    if([string]$actions[0].WorkingDirectory -ne $root){return $false}
    if($allowedUsers -notcontains [string]$taskObj.Principal.UserId){return $false}
    return $true
}

function Wait-TunnelHealthReady([string]$urlFile,[int]$timeoutSec){
    $deadline=[DateTime]::UtcNow.AddSeconds($timeoutSec)
    while([DateTime]::UtcNow -lt $deadline){
        if(Test-Path -LiteralPath $urlFile -PathType Leaf){
            try {
                $url=([IO.File]::ReadAllText($urlFile)).Trim()
                if($url -match '^http://127\.0\.0\.1:[0-9]{1,5}/?$'){
                    $response=Invoke-WebRequest -Uri ($url.TrimEnd('/')+'/readyz') -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0
                    if($response.StatusCode -eq 200){return $true}
                }
            }catch{}
        }
        Start-Sleep -Milliseconds 350
    }
    return $false
}

function Verify-OfficialZip([string]$temp){
    $zip=Join-Path $temp $asset
    $sums=Join-Path $temp 'SHA256SUMS.txt'
    if($ArchivePath -or $ChecksumPath){
        if(-not($ArchivePath -and $ChecksumPath)){throw 'Both offline ZIP and SHA256SUMS are required'}
        Copy-Item -LiteralPath $ArchivePath -Destination $zip
        Copy-Item -LiteralPath $ChecksumPath -Destination $sums
    } else {
        Invoke-WebRequest -Uri "$base/SHA256SUMS.txt" -OutFile $sums -TimeoutSec 90 -UseBasicParsing
        Invoke-WebRequest -Uri "$base/$asset" -OutFile $zip -TimeoutSec 900 -UseBasicParsing
    }
    if((Get-PortableSha256 $sums) -ne $manifestHash){
        throw 'Official SHA256SUMS manifest hash mismatch'
    }
    $text=[IO.File]::ReadAllText($sums,[Text.Encoding]::UTF8)
    $matches=@($text -split '\r?\n' | Where-Object {$_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($asset) + '\s*$')})
    if($matches.Count -ne 1){throw 'Checksum entry is missing or ambiguous'}
    $expected=($matches[0].Trim() -split '\s+')[0].ToLowerInvariant()
    if((Get-PortableSha256 $zip) -ne $expected){throw 'SHA256 ZIP mismatch'}
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive=[IO.Compression.ZipFile]::OpenRead($zip)
    try{
        foreach($name in @('tunnel-client.exe','cloudflared.exe','cloudflared-manifest.json')){
            $entries=@($archive.Entries | Where-Object {$_.FullName -eq $name})
            if($entries.Count -ne 1 -or $entries[0].Length -lt 100){
                throw "Missing official Windows tunnel runtime component: $name"
            }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0],(Join-Path $temp $name),$true)
        }
        return (Join-Path $temp 'tunnel-client.exe')
    }finally{$archive.Dispose()}
}

function Write-TunnelReport([hashtable]$data){
    try{
        if(-not(Test-Path -LiteralPath $logDir)){
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        }
        $reportFile=Join-Path $logDir 'tunnel-reconfig-report.json'
        [IO.File]::WriteAllText($reportFile,($data | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
    }catch{}
}

if($DryRun){Write-Host "DRY_RUN_PASS: $base/$asset and protected per-user $taskName (auth_mode=$AuthMode, reconfigure=$([bool]$Reconfigure))";exit 0}
if(-not [Environment]::Is64BitOperatingSystem){throw 'Windows x64 required'}
if(-not(Test-Path -LiteralPath $runner -PathType Leaf)){throw 'Tunnel runner missing'}
$health=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
if($health.status -ne 'ok' -or $health.require_autonomous_auth -ne $true -or
   $health.allow_loopback_tunnel_identity -ne $false){
    throw 'WinOwner local auth check failed. Run Check-WinOwner.cmd first.'
}
if(-not(Test-Path -LiteralPath $gatewayKey -PathType Leaf)){throw 'Local authenticated token missing'}

$existing=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if($existing){
    if(-not(Test-VerifiedTunnelTaskOwnership $existing)){
        throw "Task name collision for $taskName; refusing to modify foreign scheduled task or secrets."
    }
    if(-not $Reconfigure){
        throw 'Tunnel task exists. Disconnect-ChatGPT.cmd can remove the verified local tunnel task only; local service is kept. Pass -Reconfigure to safely reconfigure with backup and rollback.'
    }
}

if(-not $TunnelId){$TunnelId=(Read-Host 'OpenAI tunnel_id').Trim()}
if($TunnelId -notmatch '^tunnel_(?:[a-z0-9]{32}|[a-z0-9]{4}_[a-z0-9]{32})$'){throw 'Invalid official tunnel ID format'}

$secure=if($null -ne $ApiKey){$ApiKey}else{Read-Host 'Runtime API key (hidden)' -AsSecureString}
$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try{$apiKeyPlain=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)}
finally{
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    if($null -eq $ApiKey){$secure.Dispose()}
}
if($apiKeyPlain.Length -lt 20){$apiKeyPlain='';throw 'Runtime API key unexpectedly short'}

$temp=Join-Path $env:TEMP ('winowner-tunnel-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $temp -Force | Out-Null
$backupDir=''
$backupEntries=@()
$hadExistingTask=($null -ne $existing)
$wasTaskRunning=($hadExistingTask -and [string]$existing.State -eq 'Running')
$previousTunnelId=''
if(Test-Path -LiteralPath $profile -PathType Leaf){
    $prevYaml=[IO.File]::ReadAllText($profile,[Text.Encoding]::UTF8)
    if($prevYaml -match '(?m)^\s*tunnel_id:\s*(\S+)\s*$'){$previousTunnelId=$Matches[1].Trim()}
}

try{
    $verified=Verify-OfficialZip $temp
    New-Item -ItemType Directory $private,$clientDir -Force | Out-Null
    & icacls.exe $private /inheritance:r /grant:r "$($user):(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Private directory ACL failed'}

    # 1. Stage candidate files in ACL-protected staging directory BEFORE touching live config
    $stageDir=Join-Path $temp 'stage'
    New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
    & icacls.exe $stageDir /inheritance:r /grant:r "$($user):(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Stage directory ACL failed'}

    $local=([IO.File]::ReadAllText($gatewayKey,[Text.Encoding]::UTF8)).Trim()
    $stagePlane=Join-Path $stageDir 'tunnel_control_plane.key'
    $stageLocal=Join-Path $stageDir 'tunnel_mcp_auth_header.key'
    $stageProfile=Join-Path $stageDir 'winowner.yaml'
    $stageHealth=Join-Path $stageDir 'health-url.txt'

    Store-Secret $stagePlane $apiKeyPlain
    Store-Secret $stageLocal ('Bearer ' + $local)
    $apiKeyPlain=''

    if($AuthMode -eq 'oauth'){
        $yaml=@"
config_version: 1
control_plane:
  tunnel_id: $TunnelId
  api_key: env:CONTROL_PLANE_API_KEY
mcp:
  server_urls:
    - channel: main
      url: http://127.0.0.1:$Port/mcp
  oauth_discovery: enabled
"@
    } else {
        $yaml=@"
config_version: 1
control_plane:
  tunnel_id: $TunnelId
  api_key: env:CONTROL_PLANE_API_KEY
mcp:
  server_urls:
    - channel: main
      url: http://127.0.0.1:$Port/mcp
  extra_headers:
    Authorization: env:WINOWNER_INTERNAL_AUTH
  discovery_extra_headers:
    Authorization: env:WINOWNER_INTERNAL_AUTH
  oauth_discovery: disabled
"@
    }
    [IO.File]::WriteAllText($stageProfile,$yaml,[Text.UTF8Encoding]::new($false))
    Protect-File $stageProfile

    # 2. Pre-cutover validation: run doctor against STAGED config while live tunnel remains untouched
    $stageExe=Join-Path $temp 'tunnel-client.exe'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner -Doctor -ProfilePath $stageProfile -PlaneKeyPath $stagePlane -LocalKeyPath $stageLocal -BinaryPath $stageExe
    if($LASTEXITCODE -ne 0){throw 'Tunnel doctor failed; no autostart task registered and existing configuration was not modified'}

    # If reconfiguring to a new TunnelId (or when old task is not running) and starting is requested,
    # validate live candidate readiness (/readyz) BEFORE stopping or cutting over the existing tunnel!
    if((-not $NoStart) -and ($hadExistingTask -or (Test-Path -LiteralPath $profile -PathType Leaf))){
        $canProbeInParallel=(-not $wasTaskRunning) -or ($previousTunnelId -and ($previousTunnelId -ne $TunnelId))
        if($canProbeInParallel){
            Remove-Item -LiteralPath $stageHealth -Force -ErrorAction SilentlyContinue
            $probeArgs=@('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$runner,'-ProfilePath',$stageProfile,'-PlaneKeyPath',$stagePlane,'-LocalKeyPath',$stageLocal,'-BinaryPath',$stageExe,'-HealthUrlFile',$stageHealth)
            $probeProc=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $probeArgs -WorkingDirectory $root -WindowStyle Hidden -PassThru
            try{
                $probeTimeout=[Math]::Min(20,$ReadyTimeoutSeconds)
                $probeOk=Wait-TunnelHealthReady $stageHealth $probeTimeout
                if(-not $probeOk){
                    throw 'Candidate tunnel failed pre-cutover readiness probe (/readyz); existing tunnel connection was NOT interrupted.'
                }
            }finally{
                if($probeProc -and (-not $probeProc.HasExited)){
                    try{Stop-Process -Id $probeProc.Id -Force -ErrorAction SilentlyContinue}catch{}
                }
                Remove-Item -LiteralPath $stageHealth -Force -ErrorAction SilentlyContinue
            }
        }
    }

    # 3. Backup existing live config, secrets, and binaries into ACL-protected secrets/backups dir BEFORE cutover
    $ts=(Get-Date).ToString('yyyyMMdd_HHmmss_fff')
    $backupDir=Join-Path $private "backups\tunnel_reconfig_$ts"
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    Protect-File $backupDir

    $liveTargets=@(
        @{Name='winowner.yaml'; Path=$profile; Secret=$true},
        @{Name='tunnel_control_plane.key'; Path=$planeKey; Secret=$true},
        @{Name='tunnel_mcp_auth_header.key'; Path=$localKey; Secret=$true},
        @{Name='tunnel-client.exe'; Path=$binary; Secret=$false},
        @{Name='cloudflared.exe'; Path=(Join-Path $clientDir 'cloudflared.exe'); Secret=$false},
        @{Name='cloudflared-manifest.json'; Path=(Join-Path $clientDir 'cloudflared-manifest.json'); Secret=$false}
    )
    foreach($t in $liveTargets){
        $existed=Test-Path -LiteralPath $t.Path -PathType Leaf
        $sha=$null
        $bkpFile=Join-Path $backupDir $t.Name
        if($existed){
            $sha=Get-PortableSha256 $t.Path
            Copy-Item -LiteralPath $t.Path -Destination $bkpFile -Force
            if($t.Secret){Protect-File $bkpFile}
        }
        $backupEntries += [PSCustomObject]@{
            name=$t.Name
            target_path=$t.Path
            backup_path=$bkpFile
            existed=$existed
            sha256=$sha
            secret=[bool]$t.Secret
        }
    }
    $bkpManifest=[ordered]@{
        schema_version='1.2.0'
        created_at=(Get-Date).ToString('o')
        port=$Port
        task_name=$taskName
        had_existing_task=$hadExistingTask
        was_task_running=$wasTaskRunning
        previous_tunnel_id=$previousTunnelId
        new_tunnel_id=$TunnelId
        auth_mode=$AuthMode
        files=$backupEntries
    }
    $bkpManifestPath=Join-Path $backupDir 'backup_manifest.json'
    [IO.File]::WriteAllText($bkpManifestPath,($bkpManifest | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
    Protect-File $bkpManifestPath

    # 4. Atomic Cutover
    $cutoverStarted=$true
    if($hadExistingTask){
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $stopDeadline=[DateTime]::UtcNow.AddSeconds(10)
        while([DateTime]::UtcNow -lt $stopDeadline){
            $curTask=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if(-not $curTask -or $curTask.State -notin @('Running','Queued')){break}
            Start-Sleep -Milliseconds 250
        }
    }

    Copy-Item -LiteralPath $stagePlane -Destination $planeKey -Force
    Protect-File $planeKey
    Copy-Item -LiteralPath $stageLocal -Destination $localKey -Force
    Protect-File $localKey
    Copy-Item -LiteralPath $stageProfile -Destination $profile -Force
    Protect-File $profile
    foreach($name in @('tunnel-client.exe','cloudflared.exe','cloudflared-manifest.json')){
        Copy-Item -LiteralPath (Join-Path $temp $name) -Destination (Join-Path $clientDir $name) -Force
    }

    if($SimulateCutoverFailure){
        throw 'Simulated post-cutover failure triggered via -SimulateCutoverFailure.'
    }

    if(-not $NoStart){
        if(-not $hadExistingTask){
            $powershell=Join-Path $PSHOME 'powershell.exe'
            $args='-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner+'"'
            $action=New-ScheduledTaskAction -Execute $powershell -Argument $args -WorkingDirectory $root
            $trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
            $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
            $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
            Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
            $createdTask=$true
        }
        Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $taskName
        $ready=$false
        $deadline=[DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
        while([DateTime]::UtcNow -lt $deadline){
            if(Test-Path -LiteralPath $healthUrlFile -PathType Leaf){
                try {
                    $url=([IO.File]::ReadAllText($healthUrlFile)).Trim()
                    if($url -match '^http://127\.0\.0\.1:[0-9]{1,5}/?$'){
                        $response=Invoke-WebRequest -Uri ($url.TrimEnd('/')+'/readyz') -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0
                        if($response.StatusCode -eq 200){$ready=$true;break}
                    }
                }catch{}
            }
            Start-Sleep -Milliseconds 500
        }
        if(-not $ready){throw 'Tunnel is not ready. Check the runtime key, network and workspace permissions; retry setup.'}
    }else{
        Write-TunnelReport @{
            schema_version='1.2.0'
            status='TUNNEL_PREPARED'
            port=$Port
            task_name=$taskName
            tunnel_id=$TunnelId
            auth_mode=$AuthMode
            reconfigured=[bool]$hadExistingTask
            backup_dir=$backupDir
            rolled_back=$false
            rollback_verified=$false
        }
        Write-Host 'TUNNEL_PREPARED: configuration checked; tunnel not started.'
        exit 0
    }

    Write-TunnelReport @{
        schema_version='1.2.0'
        status=if($hadExistingTask){'TUNNEL_RECONFIGURE_PASS'}else{'TUNNEL_SETUP_PASS'}
        port=$Port
        task_name=$taskName
        tunnel_id=$TunnelId
        auth_mode=$AuthMode
        reconfigured=[bool]$hadExistingTask
        backup_dir=$backupDir
        rolled_back=$false
        rollback_verified=$false
    }
    if($hadExistingTask){
        Write-Host 'TUNNEL_RECONFIGURE_PASS: runtime reconfigured and ready; ChatGPT tool call NOT verified.'
    }
    Write-Host 'TUNNEL_SETUP_PASS: runtime ready; ChatGPT tool call NOT verified.'
    Write-Host 'In ChatGPT choose Plugins > Add custom MCP server > Tunnel > Authentication None.'
    Write-Host 'Owner permissions need separate verified delegation. Confirm by making a real ChatGPT tool call.'
    if(-not $NoBrowser){Start-Process 'https://chatgpt.com/#settings/Connectors'}
}catch{
    $errMsg=$_.Exception.Message
    $rolledBack=$false
    $rollbackVerified=$false
    if($cutoverStarted -and $backupEntries.Count -gt 0){
        $rolledBack=$true
        $allFilesRestored=$true
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        foreach($entry in $backupEntries){
            if($entry.existed){
                if(Test-Path -LiteralPath $entry.backup_path -PathType Leaf){
                    Copy-Item -LiteralPath $entry.backup_path -Destination $entry.target_path -Force
                    if($entry.secret){Protect-File $entry.target_path}
                    $restoredSha=Get-PortableSha256 $entry.target_path
                    if($restoredSha -ne $entry.sha256){$allFilesRestored=$false}
                } else {
                    $allFilesRestored=$false
                }
            } else {
                Remove-Item -LiteralPath $entry.target_path -Force -ErrorAction SilentlyContinue
                if(Test-Path -LiteralPath $entry.target_path){$allFilesRestored=$false}
            }
        }
        if($hadExistingTask){
            try{
                if(-not(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)){
                    $powershell=Join-Path $PSHOME 'powershell.exe'
                    $args='-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner+'"'
                    $action=New-ScheduledTaskAction -Execute $powershell -Argument $args -WorkingDirectory $root
                    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
                    $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
                    $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
                    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
                }
                if($wasTaskRunning -and (-not $NoStart)){
                    Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue
                    Start-ScheduledTask -TaskName $taskName
                    if(-not(Wait-TunnelHealthReady $healthUrlFile ([Math]::Min(20,$ReadyTimeoutSeconds)))){
                        $allFilesRestored=$false
                    }
                }
            }catch{
                $allFilesRestored=$false
            }
        } elseif($createdTask){
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        }
        $rollbackVerified=$allFilesRestored
        if($rollbackVerified){
            Write-Host 'TUNNEL_ROLLBACK_VERIFIED: previous tunnel configuration and state restored after failure.'
        } else {
            Write-Warning 'TUNNEL_ROLLBACK_INCOMPLETE: verify tunnel configuration manually.'
        }
    } elseif($createdTask){
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    Write-TunnelReport @{
        schema_version='1.2.0'
        status='TUNNEL_SETUP_FAILED'
        port=$Port
        task_name=$taskName
        tunnel_id=$TunnelId
        auth_mode=$AuthMode
        reconfigured=[bool]$hadExistingTask
        backup_dir=$backupDir
        rolled_back=$rolledBack
        rollback_verified=$rollbackVerified
        error_message=$errMsg
    }
    throw
}finally{
    $apiKeyPlain=''
    Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
}
