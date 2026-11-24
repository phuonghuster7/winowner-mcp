#requires -Version 5.1
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$file=Join-Path $root 'runtime\secrets\owner_delegation_bundle.json'
if(-not(Test-Path $file -PathType Leaf)){throw 'No owner key exists. Run Grant-OwnerAccess.cmd with the correct owner identity first.'}
# A malformed secret bundle must never be included in exception output.
try{
    $data=Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    $expires=[long]$data.expires_at
    $token=[string]$data.access_token
}catch{
    throw 'Owner credential bundle is invalid. Revoke/renew it using the owner access manager.'
}
$now=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
if($expires -le $now){throw 'Owner OAuth token expired. Revoke/renew it through the owner access manager.'}
if(-not $token.StartsWith('mcp_oat_')){throw 'Owner token bundle has invalid format'}
$pairingSummary="WinOwner Verified Owner Delegation | client_id=$($data.client_id) | delegation_id=$($data.delegation_id) | grant_id=$($data.capability_grant_id) | expires_utc=$([DateTimeOffset]::FromUnixTimeSeconds($expires).UtcDateTime.ToString('u'))"
$token=''
# Refuse to copy if the installed authorization cannot be checked.
$python=Join-Path $root '.venv\Scripts\python.exe'
$cli=Join-Path $PSScriptRoot 'manage_owner_delegation.py'
$db=Join-Path $root 'runtime\secrets\oauth.db'
if(-not(Test-Path -LiteralPath $python -PathType Leaf) -or
   -not(Test-Path -LiteralPath $cli -PathType Leaf) -or
   -not(Test-Path -LiteralPath $db -PathType Leaf)){
    throw 'Owner revocation status unavailable. Credential copy blocked.'
}
& $python $cli --oauth-db $db --tasks-db $db check-installed-owner --bundle-file $file
if($LASTEXITCODE -ne 0){throw 'Owner bundle revoked, expired or unverified. Credential copy blocked.'}
Write-Host ('Owner delegation expires (UTC): '+[DateTimeOffset]::FromUnixTimeSeconds($expires).UtcDateTime.ToString('u'))
$msg="Copy non-secret Owner Pairing Verification Summary to the Windows clipboard? Raw owner tokens are never pasted into ChatGPT."
$result=[System.Windows.Forms.MessageBox]::Show($msg,'WinOwner - Copy Owner Pairing Summary',[System.Windows.Forms.MessageBoxButtons]::YesNo,[System.Windows.Forms.MessageBoxIcon]::Warning,[System.Windows.Forms.MessageBoxDefaultButton]::Button2)
if($result -ne [System.Windows.Forms.DialogResult]::Yes){Write-Host 'Copy cancelled';exit 2}
try{
    [System.Windows.Forms.Clipboard]::SetText($pairingSummary)
}catch{
    throw 'Clipboard copy failed. No credential is printed; retry only if necessary.'
}finally{
    $pairingSummary=''
    $data=$null
}
Write-Host 'OWNER_PAIRING_SUMMARY_COPIED. Raw owner tokens are never copied or pasted into ChatGPT.'