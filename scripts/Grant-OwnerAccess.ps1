#requires -Version 5.1
[CmdletBinding()]
param([string]$ClientId='', [string]$ChallengeId='', [ValidateRange(1024,65535)][int]$Port=8768)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python=Join-Path $root '.venv\Scripts\python.exe'
$cli=Join-Path $PSScriptRoot 'manage_owner_delegation.py'
$out=Join-Path $root 'runtime\secrets\owner_delegation_bundle.json'
if(-not(Test-Path -LiteralPath $python -PathType Leaf)){throw 'WinOwner virtual environment missing. Install and check WinOwner first.'}
if(-not(Test-Path -LiteralPath $cli -PathType Leaf)){throw 'Owner delegation manager missing'}
$bootstrap=Join-Path $PSScriptRoot 'Bootstrap-WindowsMcp.ps1'
$activeDb=Join-Path $root 'runtime\secrets\oauth.db'
$signingFile=Join-Path $root 'runtime\secrets\oauth_signing.key'
if(-not(Test-Path -LiteralPath $signingFile -PathType Leaf)){
    $legacySigningFile=Join-Path $root 'runtime\secrets\secrets\oauth_signing.key'
    if(Test-Path -LiteralPath $legacySigningFile -PathType Leaf){$signingFile=$legacySigningFile}
}
foreach($required in @($bootstrap,$activeDb,$signingFile)){
    if(-not(Test-Path -LiteralPath $required -PathType Leaf)){
        throw "Required installed file missing: $(Split-Path $required -Leaf). No permissions changed."
    }
}
if(Test-Path -LiteralPath $out -PathType Leaf){
    & $python $cli --oauth-db $activeDb --tasks-db $activeDb --signing-key-file $signingFile check-installed-owner --bundle-file $out | Out-Null
    if($LASTEXITCODE -eq 0){
        throw 'Owner token bundle already exists. Revoke old grants before provisioning a replacement.'
    }
    Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue
}
$VerificationCode=''
$RequestedLevel='OWNER'
$RequestedWorkspaces='ALL_FIXED_DRIVES'
if(-not $ClientId){
    $pendingRaw=& $python $cli --oauth-db $activeDb --tasks-db $activeDb --signing-key-file $signingFile latest-pending-challenge --json 2>$null
    if($LASTEXITCODE -eq 0 -and $pendingRaw){
        try{
            $pendingObj=$pendingRaw | ConvertFrom-Json -ErrorAction Stop
            if($pendingObj.ok -and $pendingObj.challenge){
                if(-not $ChallengeId){$ChallengeId=[string]$pendingObj.challenge.challenge_id}
                $ClientId=[string]$pendingObj.challenge.client_id
                $VerificationCode=[string]$pendingObj.challenge.verification_code
                $RequestedLevel=[string]$pendingObj.challenge.requested_permission_level
                if($pendingObj.challenge.requested_workspaces){
                    $RequestedWorkspaces=($pendingObj.challenge.requested_workspaces -join ', ')
                }
            }
        }catch{}
    }
}
if(-not $ClientId){
    throw 'No pending verifiable OAuth pairing challenge found. Initiate pairing from an OAuth 2.1 client session first (Authentication: None cannot escalate to owner).'
}
if($ClientId -notmatch '^[A-Za-z0-9_.:-]{5,100}$'){throw 'Invalid client ID. No authorization was granted.'}
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
    "Verification code: $(if($VerificationCode){$VerificationCode}else{'DIRECT_LOCAL_OWNER_REVIEW'})"
    "Requested scope: $RequestedLevel ($RequestedWorkspaces)"
    "Local port: $Port"
    ''
    'A typed client ID alone does not prove identity. Confirm only if you have independently verified this exact client and understand its privileges.'
) -join [Environment]::NewLine
$result=[System.Windows.Forms.MessageBox]::Show($msg,'WinOwner - Review Owner Full Control',[System.Windows.Forms.MessageBoxButtons]::YesNo,[System.Windows.Forms.MessageBoxIcon]::Warning,[System.Windows.Forms.MessageBoxDefaultButton]::Button2)
if($result -ne [System.Windows.Forms.DialogResult]::Yes){
    if($ChallengeId){
        & $python $cli --oauth-db $activeDb --tasks-db $activeDb --signing-key-file $signingFile deny-challenge --challenge-id $ChallengeId --reason 'denied_by_owner_dialog' | Out-Null
    }
    Write-Host 'Owner authorization cancelled'
    exit 2
}
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
    if($ChallengeId){
        & $python $cli --oauth-db $activeDb --tasks-db $activeDb --signing-key-file $signingFile approve-challenge --challenge-id $ChallengeId --token-out $out
    }else{
        & $python $cli --oauth-db $activeDb --tasks-db $activeDb --signing-key-file $signingFile provision --client-id $ClientId --token-out $out
    }
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
Write-Host 'OWNER_GRANT_CREATED. Owner delegation bound to verified client identity in local Setup Center / OAuth store.'