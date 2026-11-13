#requires -Version 5.1
<#
.SYNOPSIS
    Interactive Vietnamese wizard to pair WinOwner MCP with OpenAI tunnel-client & ChatGPT.
#>
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 8768,
    [string]$ProfileName = "winowner-mcp",
    [string]$TunnelId = "",
    [System.Security.SecureString]$ApiKey = $null,
    [string]$TunnelExe = "",
    [switch]$DryRun,
    [switch]$NoBrowser
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$secretsDir = Join-Path $root "runtime\secrets"
$binDir = Join-Path $root "runtime\bin"
$appRoaming = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $env:USERPROFILE "AppData\Roaming" }
$profileDir = Join-Path $appRoaming "tunnel-client"
$protectScript = Join-Path $PSScriptRoot "Protect-TunnelCredential.ps1"
$showOwnerKeyScript = Join-Path $PSScriptRoot "Show-OwnerKey.ps1"
$runnerScript = Join-Path $PSScriptRoot "Run-WinOwnerTunnel.ps1"
$taskName = "WinOwnerTunnel-$ProfileName"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Show-Prerequisites {
    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "  WinOwner MCP - HƯỚNG DẪN KẾT NỐI CHATGPT & TUNNEL" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "Trước khi tiếp tục, vui lòng kiểm tra các điều kiện sau:"
    Write-Host "  1. Gói ChatGPT: Cần ChatGPT Business / Enterprise / Edu để dùng"
    Write-Host "     đầy đủ công cụ MCP (gói Pro chỉ hỗ trợ tìm kiếm/đọc - read/fetch)."
    Write-Host "  2. OpenAI Platform: Tài khoản tổ chức (org) có quyền Tunnels Read + Use."
    Write-Host "  3. Thông tin cần chuẩn bị:"
    Write-Host "     - Mã đường hầm (tunnel_id, dạng tunnel_...)"
    Write-Host "     - Khóa API chạy tunnel (runtime API key, dạng sk-...)"
    Write-Host ""
}

function Find-LocalTunnelClient([string]$overridePath) {
    $candidates = @()
    if ($overridePath) { $candidates += $overridePath }
    $candidates += (Join-Path $binDir "tunnel-client.exe")
    if ($env:USERPROFILE) {
        $candidates += (Join-Path $env:USERPROFILE ".codex-chatgpt-web\bin\tunnel-client.exe")
    }
    $cmd = Get-Command tunnel-client.exe -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c -PathType Leaf)) {
            return [IO.Path]::GetFullPath($c)
        }
    }
    return $null
}

function Install-VerifiedTunnelClient {
    Write-Host "[WinOwner] Đang kiểm tra bản phát hành chính thức từ github.com/openai/tunnel-client..."
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/openai/tunnel-client/releases/latest" -Headers @{ "User-Agent" = "WinOwnerMCP" } -TimeoutSec 30
    $sumsAsset = $release.assets | Where-Object { $_.name -eq "SHA256SUMS.txt" } | Select-Object -First 1
    $winAsset = $release.assets | Where-Object { $_.name -match '(?i)windows.*(amd64|x86_64).*\.(zip|exe)$' } | Select-Object -First 1
    if (-not $sumsAsset -or -not $winAsset) {
        Write-Host "[THẤT BẠI] Bản phát hành không có đủ SHA256SUMS.txt hoặc gói Windows để xác minh tự động." -ForegroundColor Red
        Write-Host "Vui lòng tải thủ công từ https://github.com/openai/tunnel-client/releases và đặt file vào:"
        Write-Host "  $binDir\tunnel-client.exe"
        return $null
    }
    $tmp = Join-Path $env:TEMP ("winowner-tc-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    try {
        $sumsPath = Join-Path $tmp "SHA256SUMS.txt"
        $pkgPath = Join-Path $tmp $winAsset.name
        Invoke-WebRequest -Uri $sumsAsset.browser_download_url -OutFile $sumsPath -UseBasicParsing -TimeoutSec 60
        Invoke-WebRequest -Uri $winAsset.browser_download_url -OutFile $pkgPath -UseBasicParsing -TimeoutSec 120
        $expected = ""
        foreach ($line in (Get-Content -LiteralPath $sumsPath -Encoding UTF8)) {
            if ($line -match '^\s*([a-fA-F0-9]{64})\s+\*?(.+?)\s*$') {
                if ($Matches[2] -eq $winAsset.name) { $expected = $Matches[1].ToUpperInvariant(); break }
            }
        }
        if (-not $expected) {
            Write-Host "[THẤT BẠI] Không tìm thấy mã SHA-256 cho $($winAsset.name) trong SHA256SUMS.txt. Từ chối tự chạy." -ForegroundColor Red
            return $null
        }
        $actual = (Get-FileHash -LiteralPath $pkgPath -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($actual -ne $expected) {
            Write-Host "[THẤT BẠI] Sai mã SHA-256 cho $($winAsset.name)! Dự kiến=$expected Thực tế=$actual" -ForegroundColor Red
            return $null
        }
        Write-Host "[WinOwner] Đã xác minh SHA-256 của $($winAsset.name): $actual" -ForegroundColor Green
        New-Item -ItemType Directory -Path $binDir -Force | Out-Null
        $destExe = Join-Path $binDir "tunnel-client.exe"
        if ($winAsset.name -match '\.zip$') {
            $unp = Join-Path $tmp "unpacked"
            Expand-Archive -LiteralPath $pkgPath -DestinationPath $unp -Force
            $found = Get-ChildItem -LiteralPath $unp -Recurse -Filter "tunnel-client.exe" | Select-Object -First 1
            if (-not $found) { throw "Không tìm thấy tunnel-client.exe trong file zip." }
            Copy-Item -LiteralPath $found.FullName -Destination $destExe -Force
        } else {
            Copy-Item -LiteralPath $pkgPath -Destination $destExe -Force
        }
        return $destExe
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Show-Prerequisites

if (-not $DryRun) {
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($h.status -ne "ok") { throw "Unhealthy" }
        Write-Host "[WinOwner] Dịch vụ cục bộ đang chạy tốt trên cổng $Port (chế độ: $($h.profile))." -ForegroundColor Green
    } catch {
        Write-Host "[THẤT BẠI] Dịch vụ WinOwner MCP cục bộ chưa chạy trên cổng $Port." -ForegroundColor Red
        Write-Host "Vui lòng chạy Install-WinOwner.cmd hoặc Check-WinOwner.cmd trước."
        exit 1
    }
}

if (-not $TunnelId) {
    $TunnelId = (Read-Host "Nhập Tunnel ID (ví dụ: tunnel_...)").Trim()
}
if ([string]::IsNullOrWhiteSpace($TunnelId) -or $TunnelId -notmatch '^[A-Za-z0-9_-]{6,128}$') {
    Write-Host "[THẤT BẠI] Tunnel ID không hợp lệ." -ForegroundColor Red
    exit 1
}

if (-not $ApiKey) {
    $ApiKey = Read-Host "Nhập runtime API key (input được ẩn)" -AsSecureString
}
if (-not $ApiKey -or $ApiKey.Length -eq 0) {
    Write-Host "[THẤT BẠI] API key không được để trống." -ForegroundColor Red
    exit 1
}

if ($DryRun) {
    $foundDry = Find-LocalTunnelClient $TunnelExe
    Write-Host "[THÀNH CÔNG] DRY_RUN: tunnel_id=$TunnelId port=$Port profile=$ProfileName local_tunnel_client=$([bool]$foundDry)" -ForegroundColor Green
    exit 0
}

# Seal runtime API key with CurrentUser DPAPI (never stored as plaintext)
& $protectScript -SecretName "winowner-tunnel.api_key" -SecretValue $ApiKey -SecretsDir $secretsDir | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[THẤT BẠI] Không thể mã hóa API key bằng DPAPI." -ForegroundColor Red
    exit 1
}

$resolvedExe = Find-LocalTunnelClient $TunnelExe
if (-not $resolvedExe) {
    $resolvedExe = Install-VerifiedTunnelClient
}
if (-not $resolvedExe) {
    Write-Host "[THẤT BẠI] Không thể xác minh hoặc tìm thấy tunnel-client.exe." -ForegroundColor Red
    exit 1
}

$exeSha = (Get-FileHash -LiteralPath $resolvedExe -Algorithm SHA256).Hash
$sig = Get-AuthenticodeSignature -FilePath $resolvedExe
Write-Host "Thông tin tunnel-client.exe:"
Write-Host "  Đường dẫn   : $resolvedExe"
Write-Host "  SHA-256     : $exeSha"
Write-Host "  Authenticode: $($sig.Status) (OpenAI phát hành xác minh qua SHA256SUMS.txt)"

$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ApiKey)
try {
    $plainTmp = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    $env:WINOWNER_TUNNEL_API_KEY = $plainTmp
    $plainTmp = $null

    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
    & $resolvedExe init `
        --profile $ProfileName `
        --profile-dir $profileDir `
        --tunnel-id $TunnelId `
        --mcp-server-url "http://127.0.0.1:$Port/mcp" `
        --control-plane-api-key-ref "env:WINOWNER_TUNNEL_API_KEY" `
        --control-plane-base-url "https://api.openai.com" `
        --health-listen-addr "127.0.0.1:0" `
        --force
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[THẤT BẠI] tunnel-client init thất bại (mã $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }

    & $resolvedExe doctor --profile $ProfileName --profile-dir $profileDir --explain --json
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[THẤT BẠI] tunnel-client doctor báo lỗi (mã $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
} finally {
    if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    Remove-Item Env:WINOWNER_TUNNEL_API_KEY -ErrorAction SilentlyContinue
}

# Register hidden per-user logon task for tunnel-client
$psExe = Join-Path $PSHOME "powershell.exe"
$taskArgs = ('-WindowStyle Hidden -NoLogo -NoProfile -ExecutionPolicy Bypass -File "{0}" -ProfileName "{1}" -TunnelExe "{2}"' -f $runnerScript, $ProfileName, $resolvedExe)
$action = New-ScheduledTaskAction -Execute $psExe -Argument $taskArgs -WorkingDirectory $root
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Set-ScheduledTask -TaskName $taskName -Action $action | Out-Null
} else {
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "WinOwner MCP tunnel-client hidden per-user logon task" | Out-Null
}
Start-ScheduledTask -TaskName $taskName -ErrorAction Stop

# Copy owner key to clipboard without printing it, and open ChatGPT connector page
& $showOwnerKeyScript -NoWait
if (-not $NoBrowser) {
    Start-Process "https://chatgpt.com/#settings/Connectors"
}

Write-Host ""
Write-Host "[THÀNH CÔNG] Đã cấu hình tunnel-client ẩn ($taskName) và sao chép Owner Key vào clipboard." -ForegroundColor Green
Write-Host "LƯU Ý QUAN TRỌNG: Chưa xác nhận đã kết nối ChatGPT!" -ForegroundColor Yellow
Write-Host "Vui lòng mở trang ChatGPT Connectors vừa bật, dán Owner Key khi được hỏi, và chạy thử 1 lệnh gọi công cụ (tool call) thực tế để xác nhận kết nối."
exit 0
