#requires -Version 5.1
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$file=Join-Path $root 'runtime\secrets\owner_delegation_bundle.json'
if(-not(Test-Path $file -PathType Leaf)){throw 'No owner key exists. Run Grant-OwnerAccess.cmd with the correct owner identity first.'}
$data=Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
$expires=[long]$data.expires_at
$now=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
if($expires -le $now){throw 'Owner OAuth token expired. Revoke/renew it through the owner access manager.'}
$token=[string]$data.access_token
if(-not $token.StartsWith('mcp_oat_')){throw 'Owner token bundle has invalid format'}
Write-Host ('Owner client ID: '+[string]$data.client_id)
Write-Host ('Owner token expires (UTC): '+[DateTimeOffset]::FromUnixTimeSeconds($expires).UtcDateTime.ToString('u'))
$msg="Copy a highly privileged owner OAuth token to the Windows clipboard? Only paste it into a trusted WinOwner owner-authorization field. Do not paste it into the Platform API key screen or share it in chat. Clipboard contents may be read by other programs."
$result=[System.Windows.Forms.MessageBox]::Show($msg,'WinOwner - Copy Owner Credential',[System.Windows.Forms.MessageBoxButtons]::YesNo,[System.Windows.Forms.MessageBoxIcon]::Warning,[System.Windows.Forms.MessageBoxDefaultButton]::Button2)
if($result -ne [System.Windows.Forms.DialogResult]::Yes){Write-Host 'Copy cancelled';exit 2}
[System.Windows.Forms.Clipboard]::SetText($token)
$token=''
Write-Host 'OWNER_TOKEN_COPIED. Clipboard contains a sensitive credential until replaced.'