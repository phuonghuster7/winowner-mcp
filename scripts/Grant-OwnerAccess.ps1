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
$msg="Owner Full Control grants broad file access, commands and potentially destructive changes on this Windows account. This is separate from ChatGPT Authentication None. Confirm only for a client you own and trust."
$result=[System.Windows.Forms.MessageBox]::Show($msg,'WinOwner - Authorize Owner Full Control',[System.Windows.Forms.MessageBoxButtons]::YesNo,[System.Windows.Forms.MessageBoxIcon]::Warning,[System.Windows.Forms.MessageBoxDefaultButton]::Button2)
if($result -ne [System.Windows.Forms.DialogResult]::Yes){Write-Host 'Owner authorization cancelled';exit 2}
if(-not $ClientId){$ClientId=(Read-Host 'Verified ChatGPT OAuth client_id (not a tunnel_id)').Trim()}
if($ClientId -notmatch '^[A-Za-z0-9_.:-]{5,100}$'){throw 'Invalid client ID. No authorization was granted.'}
if(Test-Path -LiteralPath $out -PathType Leaf){throw 'Owner token bundle already exists. Revoke old grants before provisioning a replacement.'}
$bootstrap=Join-Path $PSScriptRoot 'Bootstrap-WindowsMcp.ps1'
$oldProfile=(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5).profile
if($oldProfile -notin @('read_only','owner_full_control')){throw 'Unexpected local profile'}
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
    # The installed server binds its OAuth state to runtime/secrets/oauth.db.
# http_server.resolve_oauth_signing_key_path(db_path=...) resolves the
# signing key under that DB's parent/secrets subdirectory.
$activeDb=Join-Path $root 'runtime\secrets\oauth.db'
$signingFile=Join-Path $root 'runtime\secrets\secrets\oauth_signing.key'
if(-not(Test-Path -LiteralPath $activeDb -PathType Leaf)){
    throw 'Active OAuth database is missing; refusing to grant owner access in another database.'
}
if(-not(Test-Path -LiteralPath $signingFile -PathType Leaf)){
    throw 'Active OAuth signing key is missing; refusing to mint incompatible owner tokens.'
}
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