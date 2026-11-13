#requires -Version 5.1
<#
.SYNOPSIS
    Chuyen doi che do hoat dong WinOwner MCP (read_only <-> owner_full_control).
#>
[CmdletBinding()]
param(
    [ValidateSet('read_only', 'owner_full_control')]
    [string]$Profile = 'owner_full_control',
    [ValidateRange(1024, 65535)][int]$Port = 8768,
    [string]$ConfirmPhrase = '',
    [switch]$Force
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$bootstrap = Join-Path $PSScriptRoot "Bootstrap-WindowsMcp.ps1"
$powershellExe = Join-Path $PSHOME "powershell.exe"
$taskName = "WinOwnerMCP-$Port"

if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) {
    Write-Host "[THẤT BẠI] Thiếu file scripts\Bootstrap-WindowsMcp.ps1." -ForegroundColor Red
    exit 2
}

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $existingTask) {
    Write-Host "[THẤT BẠI] Chưa tìm thấy dịch vụ $taskName. Vui lòng chạy Install-WinOwner.cmd trước." -ForegroundColor Red
    exit 1
}

if ($Profile -eq 'owner_full_control') {
    Write-Host "=================================================================" -ForegroundColor Yellow
    Write-Host " CẢNH BÁO BẢO MẬT: BẬT CHẾ ĐỘ TOÀN QUYỀN (owner_full_control)" -ForegroundColor Yellow
    Write-Host "=================================================================" -ForegroundColor Yellow
    Write-Host "Chế độ này cho phép ChatGPT (sau khi bạn xác thực danh tính):"
    Write-Host "  1. Sửa, xóa, tạo mới file trên máy tính."
    Write-Host "  2. Chạy lệnh hệ thống Windows (PowerShell / cmd / script)."
    Write-Host "  3. Điều khiển màn hình, chuột và bàn phím."
    Write-Host ""
    Write-Host "Mặc định an toàn là CHỈ ĐỌC (read_only). Chỉ bật khi bạn thật sự cần."
    Write-Host ""
    $expected = "TOI DONG Y TOAN QUYEN"
    if (-not $Force) {
        if (-not $ConfirmPhrase) {
            $ConfirmPhrase = Read-Host "Gõ chính xác cụm từ sau để xác nhận [$expected]"
        }
        if ($ConfirmPhrase.Trim() -cne $expected) {
            Write-Host "[THẤT BẠI] Cụm từ xác nhận không khớp. Giữ nguyên chế độ an toàn hiện tại." -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host " TẮT CHẾ ĐỘ TOÀN QUYỀN -> QUAY VỀ CHỈ ĐỌC AN TOÀN (read_only)" -ForegroundColor Cyan
    Write-Host "=================================================================" -ForegroundColor Cyan
}

$argsList = @(
    "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", ('"{0}"' -f $bootstrap),
    "-Port", "$Port",
    "-SwitchProfile"
)
if ($Profile -eq 'owner_full_control') {
    $argsList += "-OwnerFullControl"
}

$child = Start-Process -FilePath $powershellExe -ArgumentList $argsList -WorkingDirectory $root -PassThru -Wait -NoNewWindow -ErrorAction Stop
if ($child.ExitCode -ne 0) {
    Write-Host "[THẤT BẠI] Chuyển chế độ thất bại (mã lỗi $($child.ExitCode))." -ForegroundColor Red
    exit $child.ExitCode
}

$health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5 -ErrorAction Stop
if ($health.status -ne "ok" -or $health.profile -ne $Profile -or $health.require_autonomous_auth -ne $true) {
    Write-Host "[THẤT BẠI] Kiểm tra sức khỏe dịch vụ sau khi chuyển chế độ không đạt." -ForegroundColor Red
    exit 5
}

$resultPath = Join-Path $root "WinOwner-SETUP-RESULT.txt"
$lines = @(
    "WinOwner MCP v1.1.1",
    "Windows install status: LOCAL_INSTALL_PASS",
    "Local profile: $Profile",
    "ChatGPT connected: NOT YET (owner OAuth required)",
    "Local MCP: http://127.0.0.1:$Port/mcp",
    "Next steps: docs\START_HERE.vi.md",
    "This file contains no keys or authentication tokens."
)
[IO.File]::WriteAllText($resultPath, ($lines -join [Environment]::NewLine), [Text.UTF8Encoding]::new($false))

Write-Host "[THÀNH CÔNG] Đã chuyển WinOwner MCP sang chế độ: $Profile (Port $Port)." -ForegroundColor Green
exit 0
