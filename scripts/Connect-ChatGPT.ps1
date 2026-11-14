#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateRange(1024,65535)][int]$Port=8768,
    [string]$TunnelId='',
    [switch]$DryRun,
    [switch]$NoBrowser,
    [switch]$NoStart,
    [string]$ArchivePath='',
    [string]$ChecksumPath=''
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$version='v0.0.20'
$asset="tunnel-client-$version-windows-amd64.zip"
$base="https://github.com/openai/tunnel-client/releases/download/$version"
$manifestHash='897c4823a24336e2cacd53b69abad8842130bef4eb029c703c7bd0d3c5f4a05d'
$private=Join-Path $root 'runtime\secrets'
$clientDir=Join-Path $root 'runtime\tunnel'
$binary=Join-Path $clientDir 'tunnel-client.exe'
$profile=Join-Path $clientDir 'winowner.yaml'
$gatewayKey=Join-Path $private 'gateway_caller.token'
$planeKey=Join-Path $private 'tunnel_control_plane.key'
$localKey=Join-Path $private 'tunnel_mcp_auth_header.key'
$runner=Join-Path $PSScriptRoot 'Run-WinOwnerTunnel.ps1'
$taskName="WinOwnerTunnel-$Port"
$healthUrlFile=Join-Path $clientDir 'health-url.txt'
$createdTask=$false
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
function Protect-File([string]$path){
    & icacls.exe $path /inheritance:r /grant:r "$($user):(F)" 'SYSTEM:(F)' | Out-Null
    if($LASTEXITCODE -ne 0){throw "ACL protection failed: $(Split-Path $path -Leaf)"}
}
function Store-Secret([string]$path,[string]$content){
    if(-not $content){throw 'Secret was empty'}
    [IO.File]::WriteAllText($path,$content,[Text.UTF8Encoding]::new($false))
    Protect-File $path
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
    if((Get-FileHash $sums -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifestHash){
        throw 'Official SHA256SUMS manifest hash mismatch'
    }
    $text=[IO.File]::ReadAllText($sums,[Text.Encoding]::UTF8)
    $matches=@($text -split '\r?\n' | Where-Object {$_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($asset) + '\s*$')})
    if($matches.Count -ne 1){throw 'Checksum entry is missing or ambiguous'}
    $expected=($matches[0].Trim() -split '\s+')[0].ToLowerInvariant()
    if((Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected){throw 'SHA256 ZIP mismatch'}
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
if($DryRun){Write-Host "DRY_RUN_PASS: $base/$asset and protected per-user $taskName";exit 0}
if(-not [Environment]::Is64BitOperatingSystem){throw 'Windows x64 required'}
if(-not(Test-Path $runner -PathType Leaf)){throw 'Tunnel runner missing'}
$health=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
if($health.status -ne 'ok' -or $health.require_autonomous_auth -ne $true -or
   $health.allow_loopback_tunnel_identity -ne $false){
    throw 'WinOwner local auth check failed. Run Check-WinOwner.cmd first.'
}
if(-not(Test-Path $gatewayKey -PathType Leaf)){throw 'Local authenticated token missing'}
if(-not $TunnelId){$TunnelId=(Read-Host 'OpenAI tunnel_id').Trim()}
if($TunnelId -notmatch '^tunnel_(?:[a-z0-9]{32}|[a-z0-9]{4}_[a-z0-9]{32})$'){throw 'Invalid official tunnel ID format'}
$existing=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if($existing){throw 'Tunnel task exists. Stop and remove it explicitly before configuring again.'}
$secure=Read-Host 'Runtime API key (hidden)' -AsSecureString
$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try{$apiKey=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)}
finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr);$secure.Dispose()}
if($apiKey.Length -lt 20){throw 'Runtime API key unexpectedly short'}
$temp=Join-Path $env:TEMP ('winowner-tunnel-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $temp -Force | Out-Null
try{
    $verified=Verify-OfficialZip $temp
    New-Item -ItemType Directory $private,$clientDir -Force | Out-Null
    & icacls.exe $private /inheritance:r /grant:r "$($user):(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Private directory ACL failed'}
    $local=([IO.File]::ReadAllText($gatewayKey,[Text.Encoding]::UTF8)).Trim()
    Store-Secret $planeKey $apiKey
    Store-Secret $localKey ('Bearer ' + $local)
    $apiKey=''
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
    [IO.File]::WriteAllText($profile,$yaml,[Text.UTF8Encoding]::new($false))
    Protect-File $profile
    foreach($name in @('tunnel-client.exe','cloudflared.exe','cloudflared-manifest.json')){
        Copy-Item -LiteralPath (Join-Path $temp $name) -Destination (Join-Path $clientDir $name) -Force
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner -Doctor
    if($LASTEXITCODE -ne 0){throw 'Tunnel doctor failed; no autostart task registered'}
    if(-not $NoStart){
        $powershell=Join-Path $PSHOME 'powershell.exe'
        $args='-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner+'"'
        $action=New-ScheduledTaskAction -Execute $powershell -Argument $args -WorkingDirectory $root
        $trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
        $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
        $createdTask=$true
        Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $taskName
        $ready=$false
        $deadline=[DateTime]::UtcNow.AddSeconds(45)
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
            Start-Sleep -Milliseconds 750
        }
        if(-not $ready){throw 'Tunnel is not ready. Check the runtime key, network and workspace permissions; retry setup.'}
    }else{
        Write-Host 'TUNNEL_PREPARED: configuration checked; tunnel not started.'
        exit 0
    }
    Write-Host 'TUNNEL_SETUP_PASS: runtime ready; ChatGPT tool call NOT verified.'
    Write-Host 'In ChatGPT choose Plugins > Add custom MCP server > Tunnel > Authentication None.'
    Write-Host 'Owner permissions need separate verified delegation. Confirm by making a real ChatGPT tool call.'
    $ownerBundle=Join-Path $private 'owner_delegation_bundle.json'
    if(Test-Path $ownerBundle -PathType Leaf){
        $copy=(Read-Host 'Owner token exists. Copy it via an explicit confirmation dialog? (y/N)').Trim()
        if($copy -eq 'y' -or $copy -eq 'Y'){
            & powershell.exe -NoProfile -Sta -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Show-OwnerKey.ps1')
            if($LASTEXITCODE -ne 0){Write-Warning 'Owner token was not copied'}
        }
    }
    if(-not $NoBrowser){Start-Process 'https://chatgpt.com/#settings/Connectors'}
}catch{
    if($createdTask){
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    throw
}finally{
    $apiKey=''
    Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
}