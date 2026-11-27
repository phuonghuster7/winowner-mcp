#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Doctor,
    [string]$ProfilePath='',
    [string]$PlaneKeyPath='',
    [string]$LocalKeyPath='',
    [string]$BinaryPath='',
    [string]$HealthUrlFile=''
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$exe=if($BinaryPath){[IO.Path]::GetFullPath($BinaryPath)}else{Join-Path $root 'runtime\tunnel\tunnel-client.exe'}
$profile=if($ProfilePath){[IO.Path]::GetFullPath($ProfilePath)}else{Join-Path $root 'runtime\tunnel\winowner.yaml'}
$plane=if($PlaneKeyPath){[IO.Path]::GetFullPath($PlaneKeyPath)}else{Join-Path $root 'runtime\secrets\tunnel_control_plane.key'}
$local=if($LocalKeyPath){[IO.Path]::GetFullPath($LocalKeyPath)}else{Join-Path $root 'runtime\secrets\tunnel_mcp_auth_header.key'}
foreach($file in @($exe,$profile,$plane,$local)){
    if(-not(Test-Path -LiteralPath $file -PathType Leaf)){throw "Missing tunnel file: $(Split-Path $file -Leaf)"}
    $item=Get-Item -LiteralPath $file -Force
    if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Refusing reparse-point tunnel file: $(Split-Path $file -Leaf)"}
}
$env:CONTROL_PLANE_API_KEY=([IO.File]::ReadAllText($plane,[Text.Encoding]::UTF8)).Trim()
$env:WINOWNER_INTERNAL_AUTH=([IO.File]::ReadAllText($local,[Text.Encoding]::UTF8)).Trim()
$healthUrlFile=if($HealthUrlFile){[IO.Path]::GetFullPath($HealthUrlFile)}else{Join-Path $root 'runtime\tunnel\health-url.txt'}
try {
    if($Doctor){& $exe doctor --profile-file $profile --explain}
    else{
        Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue
        & $exe run --profile-file $profile --health.listen-addr '127.0.0.1:0' --health.url-file $healthUrlFile
    }
    exit $LASTEXITCODE
}finally{
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:WINOWNER_INTERNAL_AUTH -ErrorAction SilentlyContinue
    if(-not $Doctor){
        Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue
    }
}
