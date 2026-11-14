#requires -Version 5.1
[CmdletBinding()]
param([string]$ClientId='', [ValidateRange(1024,65535)][int]$Port=8768)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python=Join-Path $root '.venv\Scripts\python.exe'
$cli=Join-Path $PSScriptRoot 'manage_owner_delegation.py'
$out=Join-Path $root 'runtime\secrets\owner_delegation_bundle.json'
if(-not(Test-Path -LiteralPath $python -PathType Leaf)){throw 'WinOwner virtual environment missing. Install and check WinOwner first.'}
if(-not(Test-Path -LiteralPath $cli -PathType Leaf)){throw 'Owner delegation manager missing'}
if(-not $ClientId){$ClientId=(Read-Host 'Previously verified ChatGPT OAuth client_id (not a tunnel_id)').Trim()}
if($ClientId -notmatch '^[A-Za-z0-9_.:-]{5,100}$'){throw 'Invalid client ID. No authorization was granted.'}
if(Test-Path -LiteralPath $out -PathType Leaf){throw 'Owner token bundle already exists. Revoke old grants before provisioning a replacement.'}
$bootstrap=Join-Path $PSScriptRoot 'Bootstrap-WindowsMcp.ps1'
$activeDb=Join-Path $root 'runtime\secrets\oauth.db'
$signingFile=Join-Path $root 'runtime\secrets\secrets\oauth_signing.key'
foreach($required in @($bootstrap,$activeDb,$signingFile)){
    if(-not(Test-Path -LiteralPath $required -PathType Leaf)){
        throw "Required installed file missing: $(Split-Path $required -Leaf). No permissions changed."
    }
}
$health=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
$oldProfile=$health.profile
if($health.status -ne 'ok' -or $health.require_autonomous_auth -ne $true -or
   $health.allow_loopback_tunnel_identity -ne $false -or
   $oldProfile -notin @('read_only','owner_full_control')){
    throw 'Local security preflight failed. No permissions changed.'
}
$msg=@(
    'Owner Full Control grants broad file access, commands and potentially destructive changes on this Windows account.'
    'This is separate from ChatGPT Authentication None.'
    ''
    "Requested client: $ClientId"
    "Local port: $Port"
    ''
    'A typed client ID alone does not prove identity. Confirm only if you have independently verified this exact client and understand its privileges.'
) -join [Environment]::NewLine
$result=[System.Windows.Forms.MessageBox]::Show($msg,'WinOwner - Review Owner Full Control',[System.Windows.Forms.MessageBoxButtons]::YesNo,[System.Windows.Forms.MessageBoxIcon]::Warning,[System.Windows.Forms.MessageBoxDefaultButton]::Button2)
if($result -ne [System.Windows.Forms.DialogResult]::Yes){Write-Host 'Owner authorization cancelled';exit 2}
$changed=$false
if($oldProfile -eq 'read_only'){
    Write-Host 'Switching authenticated local task to owner_full_control after owner consent.'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -Remove -Port $Port
    if($LASTEXITCODE -ne 0){throw 'Cannot stop verified read_only task'}
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -OwnerFullControl -NoPythonInstall -Port $Port
    if($LASTEXITCODE -ne 0){
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -NoPythonInstall -Port $Port
        throw 'Cannot switch profile. Read_only rollback attempted.'
    }
    $changed=$true
}
try{
    # Use the existing installed adapter database and signing key only.
    & $python $cli --oauth-db $activeDb --tasks-db $activeDb --signing-key-file $signingFile provision --client-id $ClientId --token-out $out
    if($LASTEXITCODE -ne 0){throw 'Owner grant provisioning failed'}
}catch{
    if($changed){
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -Remove -Port $Port
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -NoPythonInstall -Port $Port
    }
    throw
}
if(-not(Test-Path -LiteralPath $out -PathType Leaf)){throw 'Owner grant bundle missing'}
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $out /inheritance:r /grant:r "$($user):(F)" 'SYSTEM:(F)' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Owner bundle ACL failed. Protect the bundle and revoke the newly issued grant.'}
Write-Host 'OWNER_GRANT_CREATED. Use Show-OwnerKey.cmd only for an explicit owner authorization step.'