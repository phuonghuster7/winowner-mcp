#requires -Version 5.1
<#
.SYNOPSIS
    Cap quyen truy cap co thoi han cho thu muc cu the (goi manage_owner_delegation.py).
#>
[CmdletBinding()]
param(
    [string]$Folder = '',
    [ValidateSet('', 'read', 'write', 'commands', 'desktop')]
    [string]$Capability = '',
    [double]$Hours = 0,
    [switch]$Yes,
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$scriptPy = Join-Path $root "scripts\manage_owner_delegation.py"
$oauthDb = Join-Path $root "runtime\secrets\oauth.db"

function Test-UnsafeRootPath([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) { return $true }
    $trimmed = $PathValue.Trim()
    try {
        $full = [IO.Path]::GetFullPath($trimmed).TrimEnd('\')
    } catch {
        return $true
    }
    # Refuse whole drive (e.g. C:, C:\, D:\)
    if ($full -match '^[A-Za-z]:$') { return $true }
    $rootDrive = [IO.Path]::GetPathRoot($full).TrimEnd('\')
    if ($full -ieq $rootDrive) { return $true }

    # Refuse user profile root and system roots
    $forbidden = @()
    if ($env:USERPROFILE) { $forbidden += [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\') }
    if ($env:SystemRoot) { $forbidden += [IO.Path]::GetFullPath($env:SystemRoot).TrimEnd('\') }
    if ($env:ProgramFiles) { $forbidden += [IO.Path]::GetFullPath($env:ProgramFiles).TrimEnd('\') }
    $pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if ($pf86) { $forbidden += [IO.Path]::GetFullPath($pf86).TrimEnd('\') }
    $usersDir = Split-Path -Parent ([IO.Path]::GetFullPath($env:USERPROFILE))
    if ($usersDir) { $forbidden += $usersDir.TrimEnd('\') }

    foreach ($f in $forbidden) {
        if ($full -ieq $f) { return $true }
    }
    return $false
}

if (-not $Folder) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = "Chọn thư mục cụ thể được phép truy cập (KHÔNG chọn cả ổ đĩa hoặc thư mục gốc người dùng)"
    $dialog.ShowNewFolderButton = $false
    $res = $dialog.ShowDialog()
    if ($res -ne [System.Windows.Forms.DialogResult]::OK -or [string]::IsNullOrWhiteSpace($dialog.SelectedPath)) {
        Write-Host "[THẤT BẠI] Bạn đã hủy chọn thư mục." -ForegroundColor Red
        exit 1
    }
    $Folder = $dialog.SelectedPath
}

if (Test-UnsafeRootPath $Folder) {
    Write-Host "[THẤT BẠI] Từ chối đường dẫn quá rộng hoặc thư mục hệ thống/gốc người dùng: $Folder" -ForegroundColor Red
    Write-Host "Vui lòng chọn một thư mục con cụ thể (ví dụ: D:\CongViec\DuAn1)."
    exit 1
}

$resolvedFolder = [IO.Path]::GetFullPath($Folder)
if (-not (Test-Path -LiteralPath $resolvedFolder -PathType Container)) {
    Write-Host "[THẤT BẠI] Thư mục không tồn tại: $resolvedFolder" -ForegroundColor Red
    exit 1
}

if (-not $Capability) {
    Write-Host "Chọn mức quyền:"
    Write-Host "  1. Chỉ đọc file (read)"
    Write-Host "  2. Đọc và ghi/sửa file (write)"
    Write-Host "  3. Đọc file và chạy lệnh (commands)"
    Write-Host "  4. Đọc file và điều khiển màn hình/chuột/phím (desktop)"
    $choice = (Read-Host "Nhập số [1-4, mặc định 1]").Trim()
    switch ($choice) {
        ''  { $Capability = 'read' }
        '1' { $Capability = 'read' }
        '2' { $Capability = 'write' }
        '3' { $Capability = 'commands' }
        '4' { $Capability = 'desktop' }
        default {
            Write-Host "[THẤT BẠI] Lựa chọn không hợp lệ." -ForegroundColor Red
            exit 1
        }
    }
}

$capMap = @{
    'read'     = 'FILE_READ'
    'write'    = 'FILE_READ,FILE_WRITE'
    'commands' = 'FILE_READ,SHELL'
    'desktop'  = 'FILE_READ,DESKTOP'
}
$capDescMap = @{
    'read'     = 'Chỉ đọc file'
    'write'    = 'Đọc và ghi/sửa file'
    'commands' = 'Đọc file và chạy lệnh'
    'desktop'  = 'Đọc file và điều khiển màn hình/chuột/phím'
}
$capsArg = $capMap[$Capability]
$capDesc = $capDescMap[$Capability]

if ($Hours -le 0) {
    $rawHours = (Read-Host "Thời hạn cấp quyền (giờ, mặc định 1, tối đa 24)").Trim()
    if (-not $rawHours) {
        $Hours = 1
    } else {
        if (-not [double]::TryParse($rawHours, [ref]$Hours)) {
            Write-Host "[THẤT BẠI] Số giờ không hợp lệ." -ForegroundColor Red
            exit 1
        }
    }
}
if ($Hours -le 0 -or $Hours -gt 24) {
    Write-Host "[THẤT BẠI] Thời hạn phải lớn hơn 0 và tối đa 24 giờ." -ForegroundColor Red
    exit 1
}
$ttlSeconds = [int][Math]::Round($Hours * 3600)

Write-Host ""
Write-Host "=== TÓM TẮT CẤP QUYỀN ===" -ForegroundColor Cyan
Write-Host "  Thư mục   : $resolvedFolder"
Write-Host "  Mức quyền : $capDesc ($capsArg)"
Write-Host "  Thời hạn  : $Hours giờ ($ttlSeconds giây)"
Write-Host ""

if (-not $Yes) {
    $ans = (Read-Host "Xác nhận cấp quyền này? (gõ CO để đồng ý)").Trim()
    if ($ans -ine 'CO') {
        Write-Host "[THẤT BẠI] Đã hủy cấp quyền." -ForegroundColor Red
        exit 1
    }
}

if ($DryRun) {
    Write-Host "[THÀNH CÔNG] DRY_RUN: folder=$resolvedFolder caps=$capsArg ttl=$ttlSeconds" -ForegroundColor Green
    exit 0
}

$pyExe = if (Test-Path -LiteralPath $venvPy -PathType Leaf) { $venvPy } else { "python" }
if (-not (Test-Path -LiteralPath $scriptPy -PathType Leaf)) {
    Write-Host "[THẤT BẠI] Thiếu scripts\manage_owner_delegation.py." -ForegroundColor Red
    exit 2
}

$pyArgs = @(
    $scriptPy,
    "--oauth-db", $oauthDb,
    "provision",
    "--capabilities", $capsArg,
    "--allowed-paths", $resolvedFolder,
    "--token-ttl", "$ttlSeconds",
    "--grant-ttl", "$ttlSeconds",
    "--delegation-ttl", "$ttlSeconds"
)
& $pyExe @pyArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "[THẤT BẠI] Không thể cấp quyền (mã lỗi $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[THÀNH CÔNG] Đã cấp quyền '$capDesc' cho thư mục '$resolvedFolder' trong $Hours giờ." -ForegroundColor Green
exit 0
