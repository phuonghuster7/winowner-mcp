#requires -Version 5.1
<#
.SYNOPSIS
    Thu hoi quyen truy cap (grant_id hoac delegation_id) da cap truoc do.
#>
[CmdletBinding()]
param(
    [string]$GrantId = '',
    [string]$DelegationId = '',
    [switch]$Yes,
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$scriptPy = Join-Path $root "scripts\manage_owner_delegation.py"
$oauthDb = Join-Path $root "runtime\secrets\oauth.db"
$pyExe = if (Test-Path -LiteralPath $venvPy -PathType Leaf) { $venvPy } else { "python" }

if (-not (Test-Path -LiteralPath $scriptPy -PathType Leaf)) {
    Write-Host "[THẤT BẠI] Thiếu scripts\manage_owner_delegation.py." -ForegroundColor Red
    exit 2
}

if (-not $GrantId -and -not $DelegationId) {
    Write-Host "Đang kiểm tra các quyền hiện có..."
    if (Test-Path -LiteralPath $oauthDb -PathType Leaf) {
        & $pyExe $scriptPy --oauth-db $oauthDb inspect
    } else {
        Write-Host "Chưa có cơ sở dữ liệu quyền (runtime\secrets\oauth.db)."
    }
    Write-Host ""
    $inputId = (Read-Host "Nhập mã grant_id (grant_...) hoặc delegation_id (odd_...) cần thu hồi").Trim()
    if (-not $inputId) {
        Write-Host "[THẤT BẠI] Chưa nhập mã cần thu hồi." -ForegroundColor Red
        exit 1
    }
    if ($inputId.StartsWith("odd_")) {
        $DelegationId = $inputId
    } else {
        $GrantId = $inputId
    }
}

if (-not $Yes) {
    $targetLabel = if ($DelegationId) { "delegation_id=$DelegationId" } else { "grant_id=$GrantId" }
    $ans = (Read-Host "Xác nhận thu hồi quyền [$targetLabel]? (gõ CO để đồng ý)").Trim()
    if ($ans -ine 'CO') {
        Write-Host "[THẤT BẠI] Đã hủy thao tác thu hồi." -ForegroundColor Red
        exit 1
    }
}

if ($DryRun) {
    Write-Host "[THÀNH CÔNG] DRY_RUN revoke target=$(if ($DelegationId) { $DelegationId } else { $GrantId })" -ForegroundColor Green
    exit 0
}

if ($DelegationId) {
    & $pyExe $scriptPy --oauth-db $oauthDb revoke-delegation --delegation-id $DelegationId
} else {
    & $pyExe $scriptPy --oauth-db $oauthDb revoke-grant --grant-id $GrantId
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[THẤT BẠI] Thu hồi quyền thất bại (mã lỗi $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "[THÀNH CÔNG] Đã thu hồi quyền thành công." -ForegroundColor Green
exit 0
