#requires -Version 5.1
<#
.SYNOPSIS
    Sao chep khoa chu may (owner_admin.key) vao bo nho tam (Clipboard) va tu dong xoa sau 60 giay.
#>
[CmdletBinding()]
param(
    [ValidateRange(0, 600)][int]$ClearAfterSeconds = 60,
    [string]$KeyPath = "",
    [switch]$NoWait
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ownerKeyPath = if ($KeyPath) { $KeyPath } else { Join-Path $root "runtime\secrets\owner_admin.key" }

if (-not (Test-Path -LiteralPath $ownerKeyPath -PathType Leaf)) {
    Write-Host "[THẤT BẠI] Chưa tìm thấy khóa chủ máy (runtime\secrets\owner_admin.key). Hãy chạy Install-WinOwner.cmd trước." -ForegroundColor Red
    exit 1
}

$keyValue = ([IO.File]::ReadAllText($ownerKeyPath, [Text.UTF8Encoding]::new($false))).Trim()
if (-not $keyValue) {
    Write-Host "[THẤT BẠI] File khóa chủ máy bị rỗng." -ForegroundColor Red
    exit 1
}

Set-Clipboard -Value $keyValue
$keyValue = $null

Write-Host "[THÀNH CÔNG] Đã sao chép khóa chủ máy vào bộ nhớ tạm (Clipboard) - KHÔNG hiển thị lên màn hình." -ForegroundColor Green
Write-Host "Hãy mở trang xác nhận trên ChatGPT, nhấp vào ô nhập mã khóa và bấm Ctrl + V để dán."
if (-not $NoWait -and $ClearAfterSeconds -gt 0) {
    Write-Host "Bộ nhớ tạm sẽ tự động xóa sau $ClearAfterSeconds giây để đảm bảo an toàn..."
    Start-Sleep -Seconds $ClearAfterSeconds
    try {
        Set-Clipboard -Value ""
    } catch {}
    Write-Host "Đã xóa khóa khỏi bộ nhớ tạm." -ForegroundColor Cyan
}
exit 0

