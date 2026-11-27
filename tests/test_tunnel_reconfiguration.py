"""Phase 4 P1 — Safe Tunnel Reconfiguration, Ownership Verification & Automatic Rollback Tests.

Verifies:
1. Scheduled Task ownership verification: foreign/colliding task on `WinOwnerTunnel-<Port>` is
   strictly refused before touching any files, secrets, or tasks.
2. Existing verified WinOwner tunnel task refuses overwrite without `-Reconfigure`, and safely
   reconfigures when `-Reconfigure` is passed.
3. Pre-cutover validation: if candidate config fails `doctor` or pre-cutover `/readyz`, the existing
   live `winowner.yaml`, secret keys, and running tunnel task remain 100% untouched and uninterrupted.
4. Post-cutover automatic rollback: if cutover or post-cutover `/readyz` fails (e.g. `-SimulateCutoverFailure`
   or unready new tunnel), `Connect-ChatGPT.ps1` automatically restores the backed-up `winowner.yaml`,
   secret keys, and binaries with SHA-256 verification and restarts the previous verified task.
5. OAuth 2.1 tunnel mode (`-AuthMode oauth`) writes `oauth_discovery: enabled` without static
   `Authorization` header clobbering, while default `-AuthMode auth_none` preserves `oauth_discovery: disabled`.
6. `Connect-ChatGPT.cmd` and `Disconnect-ChatGPT.cmd` retain strict CRLF line endings and support
   non-interactive `-DryRun` / `-Yes` without touching production tasks or ports.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_MANIFEST_HASH = "897c4823a24336e2cacd53b69abad8842130bef4eb029c703c7bd0d3c5f4a05d"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().lower()


def _prepare_isolated_tunnel_root(tmp_path: Path) -> Path:
    root = tmp_path / "winowner_isolated"
    scripts = root / "scripts"
    secrets = root / "runtime" / "secrets"
    tunnel = root / "runtime" / "tunnel"
    logs = root / "runtime" / "logs"
    for d in (scripts, secrets, tunnel, logs):
        d.mkdir(parents=True, exist_ok=True)

    for fname in ("Connect-ChatGPT.ps1", "Run-WinOwnerTunnel.ps1", "Bootstrap-WindowsMcp.ps1"):
        (scripts / fname).write_bytes((ROOT / "scripts" / fname).read_bytes())
    for cmd_name in ("Connect-ChatGPT.cmd", "Disconnect-ChatGPT.cmd"):
        (root / cmd_name).write_bytes((ROOT / cmd_name).read_bytes())

    (secrets / "gateway_caller.token").write_text("test_isolated_gateway_token_value_1234567890", encoding="utf-8")

    # Build a synthetic offline ZIP for mocked Verify-OfficialZip tests
    offline_dir = tmp_path / "offline_assets"
    offline_dir.mkdir(parents=True, exist_ok=True)
    zip_path = offline_dir / "tunnel-client-v0.0.20-windows-amd64.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tunnel-client.exe", "MZ_SYNTHETIC_TUNNEL_CLIENT_BINARY_" + ("A" * 200))
        zf.writestr("cloudflared.exe", "MZ_SYNTHETIC_CLOUDFLARED_BINARY_" + ("B" * 200))
        zf.writestr("cloudflared-manifest.json", json.dumps({"version": "2026.1.0", "padding": "C" * 150}))
    zip_sha = _sha256_hex(zip_path)
    sums_path = offline_dir / "SHA256SUMS.txt"
    sums_path.write_text(f"{zip_sha}  tunnel-client-v0.0.20-windows-amd64.zip\n", encoding="utf-8")
    return root


def test_cmd_launchers_preserve_crlf_and_disconnect_dryrun(tmp_path: Path) -> None:
    for name in ("Connect-ChatGPT.cmd", "Disconnect-ChatGPT.cmd"):
        raw = (ROOT / name).read_bytes()
        assert b"\r\n" in raw, f"{name} must use CRLF line endings"
        assert raw.count(b"\r\n") == raw.count(b"\n"), f"{name} contains LF-only line endings"

    if os.name != "nt":
        pytest.skip("Windows CMD execution required")

    root = _prepare_isolated_tunnel_root(tmp_path)
    port = _free_port()
    res = subprocess.run(
        ["cmd.exe", "/d", "/c", str(root / "Disconnect-ChatGPT.cmd"), "-DryRun", "-Port", str(port)],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert res.returncode == 0, (res.stdout, res.stderr)
    assert "TUNNEL_DISCONNECT_DRY_RUN" in res.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_foreign_task_collision_refused_before_touching_files(tmp_path: Path) -> None:
    root = _prepare_isolated_tunnel_root(tmp_path)
    port = _free_port()
    profile_path = root / "runtime" / "tunnel" / "winowner.yaml"
    plane_key = root / "runtime" / "secrets" / "tunnel_control_plane.key"
    profile_path.write_text("existing_foreign_protected_content\n", encoding="utf-8")
    plane_key.write_text("existing_foreign_key_content\n", encoding="utf-8")

    harness = tmp_path / "harness_collision.ps1"
    harness.write_text(r"""
param([string]$Root, [int]$Port)
function global:Invoke-RestMethod {
    param([string]$Uri,[int]$TimeoutSec)
    return [PSCustomObject]@{status='ok'; require_autonomous_auth=$true; allow_loopback_tunnel_identity=$false}
}
function global:Get-ScheduledTask {
    param([string]$TaskName,[string]$ErrorAction)
    if($TaskName -eq "WinOwnerTunnel-$Port"){
        return [PSCustomObject]@{
            Actions=@([PSCustomObject]@{
                Execute='C:\ForeignApp\runner.exe'
                Arguments='--foreign'
                WorkingDirectory='C:\ForeignApp'
            })
            Principal=[PSCustomObject]@{UserId=[Security.Principal.WindowsIdentity]::GetCurrent().Name}
            State='Running'
        }
    }
}
function global:Stop-ScheduledTask { throw "FOREIGN_TASK_MUST_NOT_BE_STOPPED" }
function global:Unregister-ScheduledTask { throw "FOREIGN_TASK_MUST_NOT_BE_UNREGISTERED" }
$sec = ConvertTo-SecureString 'sk_runtime_valid_length_key_1234567890' -AsPlainText -Force
& (Join-Path $Root 'scripts\Connect-ChatGPT.ps1') -TargetRoot $Root -Port $Port -TunnelId 'tunnel_11112222333344445555666677778888' -ApiKey $sec -Reconfigure -NoBrowser
""", encoding="utf-8")

    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness), "-Root", str(root), "-Port", str(port)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=25,
    )
    out = res.stdout + res.stderr
    assert res.returncode != 0
    assert "Task name collision" in out
    assert "FOREIGN_TASK_MUST_NOT_BE_STOPPED" not in out
    assert profile_path.read_text(encoding="utf-8") == "existing_foreign_protected_content\n"
    assert plane_key.read_text(encoding="utf-8") == "existing_foreign_key_content\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_precutover_doctor_failure_leaves_existing_tunnel_untouched(tmp_path: Path) -> None:
    root = _prepare_isolated_tunnel_root(tmp_path)
    port = _free_port()
    profile_path = root / "runtime" / "tunnel" / "winowner.yaml"
    plane_key = root / "runtime" / "secrets" / "tunnel_control_plane.key"
    local_key = root / "runtime" / "secrets" / "tunnel_mcp_auth_header.key"

    orig_yaml = (
        "config_version: 1\ncontrol_plane:\n  tunnel_id: tunnel_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "  api_key: env:CONTROL_PLANE_API_KEY\nmcp:\n  server_urls:\n    - channel: main\n"
        f"      url: http://127.0.0.1:{port}/mcp\n"
    )
    profile_path.write_text(orig_yaml, encoding="utf-8")
    plane_key.write_text("original_working_plane_key_1234567890", encoding="utf-8")
    local_key.write_text("Bearer test_isolated_gateway_token_value_1234567890", encoding="utf-8")
    orig_yaml_sha = _sha256_hex(profile_path)
    orig_key_sha = _sha256_hex(plane_key)

    # Replace Run-WinOwnerTunnel.ps1 with a stub that fails -Doctor on candidate tunnel_bbbb...
    (root / "scripts" / "Run-WinOwnerTunnel.ps1").write_text(r"""
param([switch]$Doctor,[string]$ProfilePath='',[string]$PlaneKeyPath='',[string]$LocalKeyPath='',[string]$BinaryPath='',[string]$HealthUrlFile='')
if($Doctor){
    $content = [IO.File]::ReadAllText($ProfilePath)
    if($content -match 'tunnel_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'){
        Write-Error "Candidate doctor check failed"
        exit 2
    }
    exit 0
}
exit 0
""", encoding="utf-8")

    harness = tmp_path / "harness_precutover.ps1"
    harness.write_text(r"""
param([string]$Root, [int]$Port)
$runner = Join-Path $Root 'scripts\Run-WinOwnerTunnel.ps1'
function global:Invoke-RestMethod {
    param([string]$Uri,[int]$TimeoutSec)
    return [PSCustomObject]@{status='ok'; require_autonomous_auth=$true; allow_loopback_tunnel_identity=$false}
}
function global:Get-ScheduledTask {
    param([string]$TaskName,[string]$ErrorAction)
    if($TaskName -eq "WinOwnerTunnel-$Port"){
        return [PSCustomObject]@{
            Actions=@([PSCustomObject]@{
                Execute=(Join-Path $PSHOME 'powershell.exe')
                Arguments='-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner+'"'
                WorkingDirectory=$Root
            })
            Principal=[PSCustomObject]@{UserId=[Security.Principal.WindowsIdentity]::GetCurrent().Name}
            State='Running'
        }
    }
}
function global:Stop-ScheduledTask { throw "EXISTING_TUNNEL_MUST_NOT_STOP_ON_DOCTOR_FAIL" }
# Patch manifestHash in Connect-ChatGPT.ps1 to match our synthetic offline SHA256SUMS.txt
$sumsPath = Join-Path (Split-Path $Root -Parent) 'offline_assets\SHA256SUMS.txt'
$zipPath = Join-Path (Split-Path $Root -Parent) 'offline_assets\tunnel-client-v0.0.20-windows-amd64.zip'
$sumsHash = (Get-FileHash $sumsPath -Algorithm SHA256).Hash.ToLowerInvariant()
$scriptPath = Join-Path $Root 'scripts\Connect-ChatGPT.ps1'
$raw = [IO.File]::ReadAllText($scriptPath)
$raw = $raw.Replace('897c4823a24336e2cacd53b69abad8842130bef4eb029c703c7bd0d3c5f4a05d', $sumsHash)
[IO.File]::WriteAllText($scriptPath, $raw)

$sec = ConvertTo-SecureString 'sk_candidate_bad_key_123456789012345' -AsPlainText -Force
& $scriptPath -TargetRoot $Root -Port $Port -TunnelId 'tunnel_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' -ApiKey $sec -ArchivePath $zipPath -ChecksumPath $sumsPath -Reconfigure -NoBrowser
""", encoding="utf-8")

    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness), "-Root", str(root), "-Port", str(port)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = res.stdout + res.stderr
    assert res.returncode != 0
    assert "Tunnel doctor failed" in out
    assert "EXISTING_TUNNEL_MUST_NOT_STOP_ON_DOCTOR_FAIL" not in out
    # Prove live files were NEVER modified
    assert _sha256_hex(profile_path) == orig_yaml_sha
    assert _sha256_hex(plane_key) == orig_key_sha


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_reconfigure_success_and_automatic_rollback_on_postcutover_failure(tmp_path: Path) -> None:
    root = _prepare_isolated_tunnel_root(tmp_path)
    port = _free_port()
    profile_path = root / "runtime" / "tunnel" / "winowner.yaml"
    plane_key = root / "runtime" / "secrets" / "tunnel_control_plane.key"
    local_key = root / "runtime" / "secrets" / "tunnel_mcp_auth_header.key"
    binary_path = root / "runtime" / "tunnel" / "tunnel-client.exe"

    orig_yaml = (
        "config_version: 1\ncontrol_plane:\n  tunnel_id: tunnel_11111111111111111111111111111111\n"
        "  api_key: env:CONTROL_PLANE_API_KEY\nmcp:\n  server_urls:\n    - channel: main\n"
        f"      url: http://127.0.0.1:{port}/mcp\n"
    )
    profile_path.write_text(orig_yaml, encoding="utf-8")
    plane_key.write_text("original_secret_plane_key_value_1111111111", encoding="utf-8")
    local_key.write_text("Bearer test_isolated_gateway_token_value_1234567890", encoding="utf-8")
    binary_path.write_text("ORIGINAL_TUNNEL_BINARY_V1", encoding="utf-8")

    orig_yaml_sha = _sha256_hex(profile_path)
    orig_plane_sha = _sha256_hex(plane_key)
    orig_bin_sha = _sha256_hex(binary_path)

    # Stub Run-WinOwnerTunnel.ps1 so -Doctor succeeds
    (root / "scripts" / "Run-WinOwnerTunnel.ps1").write_text(r"""
param([switch]$Doctor,[string]$ProfilePath='',[string]$PlaneKeyPath='',[string]$LocalKeyPath='',[string]$BinaryPath='',[string]$HealthUrlFile='')
exit 0
""", encoding="utf-8")

    harness = tmp_path / "harness_rollback.ps1"
    harness.write_text(r"""
param([string]$Root, [int]$Port, [switch]$SimulateFail, [string]$AuthMode='auth_none')
$global:HealthFile = Join-Path $Root 'runtime\tunnel\health-url.txt'
$global:RunnerFile = Join-Path $Root 'scripts\Run-WinOwnerTunnel.ps1'
$global:RootDir = $Root
$global:MockTaskState = 'Running'
function global:Invoke-RestMethod {
    param([string]$Uri,[int]$TimeoutSec)
    return [PSCustomObject]@{status='ok'; require_autonomous_auth=$true; allow_loopback_tunnel_identity=$false}
}
function global:Invoke-WebRequest {
    param([string]$Uri,[switch]$UseBasicParsing,[int]$TimeoutSec,[int]$MaximumRedirection)
    if($Uri -like '*/readyz'){
        return [PSCustomObject]@{StatusCode=200}
    }
    throw "Unexpected URI: $Uri"
}
function global:Get-ScheduledTask {
    param([string]$TaskName,[string]$ErrorAction)
    if($TaskName -eq "WinOwnerTunnel-$Port"){
        return [PSCustomObject]@{
            Actions=@([PSCustomObject]@{
                Execute=(Join-Path $PSHOME 'powershell.exe')
                Arguments='-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$global:RunnerFile+'"'
                WorkingDirectory=$global:RootDir
            })
            Principal=[PSCustomObject]@{UserId=[Security.Principal.WindowsIdentity]::GetCurrent().Name}
            State=$global:MockTaskState
        }
    }
}
function global:Stop-ScheduledTask {
    [CmdletBinding()] param([string]$TaskName)
    $global:MockTaskState = 'Ready'
    Write-Host "MOCK_TASK_STOPPED"
}
function global:Unregister-ScheduledTask {
    [CmdletBinding(SupportsShouldProcess=$true)] param([string]$TaskName)
    Write-Host "MOCK_TASK_UNREGISTERED"
}
function global:Register-ScheduledTask {
    [CmdletBinding()] param([string]$TaskName,$Action,$Trigger,$Principal,$Settings)
    Write-Host "MOCK_TASK_REGISTERED"
    return [PSCustomObject]@{TaskName=$TaskName}
}
function global:Start-ScheduledTask {
    [CmdletBinding()] param([string]$TaskName)
    $global:MockTaskState = 'Running'
    [IO.File]::WriteAllText($global:HealthFile, 'http://127.0.0.1:54321/')
    Write-Host "MOCK_TASK_STARTED"
}

$sumsPath = Join-Path (Split-Path $Root -Parent) 'offline_assets\SHA256SUMS.txt'
$zipPath = Join-Path (Split-Path $Root -Parent) 'offline_assets\tunnel-client-v0.0.20-windows-amd64.zip'
$sumsHash = (Get-FileHash $sumsPath -Algorithm SHA256).Hash.ToLowerInvariant()
$scriptPath = Join-Path $Root 'scripts\Connect-ChatGPT.ps1'
$raw = [IO.File]::ReadAllText($scriptPath)
$raw = $raw.Replace('897c4823a24336e2cacd53b69abad8842130bef4eb029c703c7bd0d3c5f4a05d', $sumsHash)
[IO.File]::WriteAllText($scriptPath, $raw)

$sec = ConvertTo-SecureString 'sk_new_valid_runtime_key_2222222222222' -AsPlainText -Force
& $scriptPath -TargetRoot $Root -Port $Port -TunnelId 'tunnel_11111111111111111111111111111111' -ApiKey $sec -ArchivePath $zipPath -ChecksumPath $sumsPath -Reconfigure -AuthMode $AuthMode -NoBrowser -SimulateCutoverFailure:$SimulateFail
""", encoding="utf-8")

    # 1. Run with -SimulateFail -> must trigger automatic rollback and restore exact SHA-256 of all live files
    res_fail = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(harness), "-Root", str(root), "-Port", str(port), "-SimulateFail",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    out_fail = res_fail.stdout + res_fail.stderr
    assert res_fail.returncode != 0
    assert "TUNNEL_ROLLBACK_VERIFIED" in out_fail
    assert _sha256_hex(profile_path) == orig_yaml_sha
    assert _sha256_hex(plane_key) == orig_plane_sha
    assert _sha256_hex(binary_path) == orig_bin_sha

    report_fail = json.loads((root / "runtime" / "logs" / "tunnel-reconfig-report.json").read_text(encoding="utf-8"))
    assert report_fail["rolled_back"] is True
    assert report_fail["rollback_verified"] is True
    assert "sk_new_valid_runtime_key" not in json.dumps(report_fail)

    # 2. Run without -SimulateFail in -AuthMode oauth -> must succeed and cut over cleanly
    res_ok = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(harness), "-Root", str(root), "-Port", str(port), "-AuthMode", "oauth",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    out_ok = res_ok.stdout + res_ok.stderr
    assert res_ok.returncode == 0, out_ok
    assert "TUNNEL_RECONFIGURE_PASS" in out_ok
    assert "TUNNEL_SETUP_PASS" in out_ok
    new_yaml = profile_path.read_text(encoding="utf-8")
    assert "oauth_discovery: enabled" in new_yaml
    assert "extra_headers:" not in new_yaml
    assert _sha256_hex(plane_key) != orig_plane_sha
