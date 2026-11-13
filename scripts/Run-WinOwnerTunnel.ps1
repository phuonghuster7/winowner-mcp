#requires -Version 5.1
<#
.SYNOPSIS
    Hidden per-user runner for openai/tunnel-client using CurrentUser DPAPI secret.
#>
[CmdletBinding()]
param(
    [string]$ProfileName = "winowner-mcp",
    [string]$ProfileDir = "",
    [string]$TunnelExe = "",
    [string]$SecretsDir = ""
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Security

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $SecretsDir) { $SecretsDir = Join-Path $root "runtime\secrets" }
if (-not $ProfileDir) {
    $appRoaming = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $env:USERPROFILE "AppData\Roaming" }
    $ProfileDir = Join-Path $appRoaming "tunnel-client"
}
if (-not $TunnelExe) {
    $localBin = Join-Path $root "runtime\bin\tunnel-client.exe"
    if (Test-Path -LiteralPath $localBin -PathType Leaf) { $TunnelExe = $localBin }
}
if (-not $TunnelExe -or -not (Test-Path -LiteralPath $TunnelExe -PathType Leaf)) {
    throw "Khong tim thay tunnel-client.exe"
}

$dpapiPath = Join-Path $SecretsDir "winowner-tunnel.api_key.dpapi"
if (-not (Test-Path -LiteralPath $dpapiPath -PathType Leaf)) {
    throw "Khong tim thay file khoa ma hoa DPAPI: $dpapiPath"
}

$entropy = [System.Text.Encoding]::UTF8.GetBytes("WindowsMcpAgent::P0.5::CredentialStore::v1")
$cipherB64 = ([IO.File]::ReadAllText($dpapiPath, [Text.Encoding]::UTF8)).Trim()
$cipherBytes = [Convert]::FromBase64String($cipherB64)
$rawBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
    $cipherBytes,
    $entropy,
    [System.Security.Cryptography.DataProtectionScope]::CurrentUser
)
$plainKey = [System.Text.Encoding]::UTF8.GetString($rawBytes)
[Array]::Clear($rawBytes, 0, $rawBytes.Length)

try {
    $env:WINOWNER_TUNNEL_API_KEY = $plainKey
    $plainKey = $null
    $logDir = Join-Path $root "runtime\logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $healthUrlFile = Join-Path $logDir "$ProfileName-tunnel-health.url"
    & $TunnelExe run --profile $ProfileName --profile-dir $ProfileDir --health.listen-addr "127.0.0.1:0" --health.url-file $healthUrlFile
    exit $LASTEXITCODE
} finally {
    Remove-Item Env:WINOWNER_TUNNEL_API_KEY -ErrorAction SilentlyContinue
}
