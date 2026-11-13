#requires -Version 5.1
[CmdletBinding()]
param([switch]$Doctor)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$exe=Join-Path $root 'runtime\tunnel\tunnel-client.exe'
$profile=Join-Path $root 'runtime\tunnel\winowner.yaml'
$plane=Join-Path $root 'runtime\secrets\tunnel_control_plane.key'
$local=Join-Path $root 'runtime\secrets\tunnel_mcp_auth_header.key'
foreach($file in @($exe,$profile,$plane,$local)){
    if(-not(Test-Path $file -PathType Leaf)){throw "Missing tunnel file: $(Split-Path $file -Leaf)"}
}
$env:CONTROL_PLANE_API_KEY=([IO.File]::ReadAllText($plane,[Text.Encoding]::UTF8)).Trim()
$env:WINOWNER_INTERNAL_AUTH=([IO.File]::ReadAllText($local,[Text.Encoding]::UTF8)).Trim()
try {
    if($Doctor){& $exe doctor --profile-file $profile --explain}
    else{& $exe run --profile-file $profile}
    exit $LASTEXITCODE
}finally{
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:WINOWNER_INTERNAL_AUTH -ErrorAction SilentlyContinue
}