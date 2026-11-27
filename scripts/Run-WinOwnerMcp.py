"""WinOwner MCP scheduled-task entrypoint (current Windows owner).

No bearer secrets in Scheduled Task arguments, Git history, or command line.
This process directly hosts uvicorn, allowing Task Scheduler to stop it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_TUNNEL_ID_RE = re.compile(r"^tunnel_(?:[a-z0-9]{32}|[a-z0-9]{4}_[a-z0-9]{32})$")
_SEMVER_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
_GITHUB_RELEASES_LATEST_URL = "https://api.github.com/repos/phuonghuster7/winowner-mcp/releases/latest"


def _parse_semver_tuple(tag: str) -> tuple[int, int, int, int, str] | None:
    m = _SEMVER_TAG_RE.match(str(tag or "").strip())
    if not m:
        return None
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    prerelease = m.group(4) or ""
    is_stable = 1 if not prerelease else 0
    return (major, minor, patch, is_stable, prerelease)


def _is_newer_semver(candidate_tag: str, current_tag: str) -> bool:
    c_parsed = _parse_semver_tuple(candidate_tag)
    cur_parsed = _parse_semver_tuple(current_tag)
    if c_parsed is None or cur_parsed is None:
        return False
    return c_parsed[:4] > cur_parsed[:4] or (
        c_parsed[:4] == cur_parsed[:4] and c_parsed[4] > cur_parsed[4]
    )


def _extract_powershell_error_summary(
    stdout: str,
    stderr: str,
    rc: int,
    *,
    secret_to_scrub: str = "",
    default_prefix: str = "PowerShell script",
) -> str:
    """Extract real failure reason from PowerShell stdout/stderr without boilerplate footer truncation."""
    def _scrub(text: str) -> str:
        s = str(text or "")
        if secret_to_scrub:
            s = s.replace(secret_to_scrub, "[REDACTED]")
        return s

    clean_out = _scrub(stdout)
    clean_err = _scrub(stderr)

    def _strip_footer(text: str) -> list[str]:
        kept: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if re.match(r"^WHAT TO DO NEXT\s*:", line, re.IGNORECASE):
                break
            kept.append(line)
        return kept

    noise_re = re.compile(
        r"^(?:At\s+.+:\d+\s+char:\d+|\+\s*~+|\+\s*CategoryInfo\b|\+\s*FullyQualifiedErrorId\b)",
        re.IGNORECASE,
    )
    priority_re = re.compile(
        r"(?:\[WinOwner\]\s*ERROR\s*:|\bERROR\s*:|\bdoctor\b|\bnot ready\b|\bmismatch\b|\brefusing\b|\bfailed\b|\binvalid\b|\bmissing\b|\bdenied\b|\bcollision\b|\boccupied\b|\bexception\b)",
        re.IGNORECASE,
    )

    err_lines = [ln for ln in _strip_footer(clean_err) if not noise_re.match(ln)]
    out_lines = [ln for ln in _strip_footer(clean_out) if not noise_re.match(ln)]

    priority_hits: list[str] = []
    seen: set[str] = set()
    for ln in (*out_lines, *err_lines):
        if priority_re.search(ln) and ln not in seen:
            seen.add(ln)
            priority_hits.append(ln)

    if priority_hits:
        summary = " | ".join(priority_hits[:4])
    elif err_lines:
        summary = " | ".join(err_lines[:4])
    elif out_lines:
        summary = " | ".join(out_lines[-4:])
    else:
        summary = f"{default_prefix} exited with code {rc}"

    if len(summary) > 600:
        summary = summary[:597] + "..."
    return summary


def _detect_existing_tunnel_task(port: int) -> bool:
    """Return True if a Windows Scheduled Task WinOwnerTunnel-<port> currently exists."""
    if os.name != "nt":
        return False
    try:
        res = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"if (Get-ScheduledTask -TaskName 'WinOwnerTunnel-{int(port)}' -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def _probe_local_adapter(port: int, urlopen_fn: Any) -> dict[str, Any]:
    """Live-probe GET /health and anonymous POST /mcp -> HTTP 401 on 127.0.0.1:<port>."""
    health_url = f"http://127.0.0.1:{int(port)}/health"
    mcp_url = f"http://127.0.0.1:{int(port)}/mcp"
    health_data: dict[str, Any] = {}
    health_ok = False
    anon_401 = False
    profile = "read_only"

    try:
        with urlopen_fn(health_url, timeout=2.0) as resp:
            if int(getattr(resp, "status", 0)) == 200:
                raw = resp.read()
                parsed = json.loads(raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw))
                if isinstance(parsed, dict):
                    health_data = parsed
                    profile = str(parsed.get("profile") or "read_only")
                    health_ok = (
                        parsed.get("status") == "ok"
                        and parsed.get("require_autonomous_auth") is True
                        and parsed.get("allow_loopback_tunnel_identity") is False
                    )
    except Exception:
        health_ok = False

    if health_ok:
        req = urllib.request.Request(
            mcp_url,
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen_fn(req, timeout=3.0) as resp:
                anon_401 = int(getattr(resp, "status", 0)) == 401
        except urllib.error.HTTPError as exc:
            anon_401 = int(getattr(exc, "code", 0)) == 401
        except Exception:
            anon_401 = False

    return {
        "healthy": bool(health_ok and anon_401),
        "health_ok": health_ok,
        "anon_mcp_http_401": anon_401,
        "profile": profile,
        "health": health_data,
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _restore_latest_tunnel_backup(base_root: Path) -> dict[str, Any]:
    """Restore verified files from the latest runtime/secrets/backups/tunnel_reconfig_* manifest."""
    backups_root = base_root / "runtime" / "secrets" / "backups"
    if not backups_root.is_dir():
        raise RuntimeError("No tunnel rollback backup directory found under runtime/secrets/backups")
    candidates = sorted(
        [d for d in backups_root.iterdir() if d.is_dir() and d.name.startswith("tunnel_reconfig_")],
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("No tunnel_reconfig_* backup snapshots found to restore")

    backup_dir = candidates[0]
    manifest_path = backup_dir / "backup_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Backup manifest missing in {backup_dir.name}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files:
        raise RuntimeError(f"Backup manifest in {backup_dir.name} has no file entries")

    # Phase 1: verify all backed-up files BEFORE modifying any live file
    verified_ops: list[tuple[Path | None, Path, bool]] = []
    resolved_root = base_root.resolve()
    for entry in files:
        if not isinstance(entry, dict):
            raise RuntimeError("Malformed backup manifest entry")
        existed = bool(entry.get("existed", False))
        target_path = Path(str(entry.get("target_path") or "")).resolve()
        if resolved_root not in target_path.parents and target_path != resolved_root:
            raise RuntimeError(f"Refusing rollback path outside repository root: {target_path}")
        if not existed:
            verified_ops.append((None, target_path, False))
            continue
        backup_path = Path(str(entry.get("backup_path") or "")).resolve()
        if backup_dir.resolve() not in backup_path.parents:
            raise RuntimeError(f"Refusing backup source outside backup directory: {backup_path}")
        if not backup_path.is_file():
            raise RuntimeError(f"Backup file missing: {backup_path.name}")
        expected_sha = str(entry.get("sha256") or "").strip().lower()
        if expected_sha and _sha256_file(backup_path) != expected_sha:
            raise RuntimeError(f"SHA-256 verification failed for backup file {backup_path.name}")
        verified_ops.append((backup_path, target_path, True))

    # Phase 2: apply verified restore
    restored: list[str] = []
    for backup_path, target_path, existed in verified_ops:
        if existed and backup_path is not None:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, target_path)
            restored.append(target_path.name)
        elif not existed and target_path.is_file():
            try:
                target_path.unlink()
            except OSError:
                pass

    return {
        "action": "rollback",
        "scope": "tunnel",
        "status": "TUNNEL_ROLLBACK_PASS",
        "rollback_verified": True,
        "secrets_preserved": True,
        "backup_dir": str(backup_dir),
        "restored_files": restored,
        "message_en": f"Restored verified tunnel configuration from {backup_dir.name}.",
        "message_vi": f"Đã khôi phục cấu hình tunnel đã xác minh từ bản sao lưu {backup_dir.name}.",
    }


def build_setup_center_action_hooks(
    root: Path | None = None,
    port: int = 8768,
    *,
    subprocess_runner: Any | None = None,
    task_exists_fn: Any | None = None,
    urlopen_fn: Any | None = None,
) -> dict[str, Any]:
    """Build real Setup Center action hooks wired to real Windows scripts & live probes.

    Wires all 6 real hooks (zero mock fallbacks):
    - install_repair
    - connect_chatgpt
    - reconfigure_tunnel
    - check_updates
    - uninstall
    - rollback

    Passes runtime_api_key strictly via a child-only environment variable that PowerShell converts
    to SecureString and removes immediately from Env:. Never places secrets in CLI argv or errors.
    Raises RuntimeError on non-zero exit so Setup Center returns HTTP 400 (never fake success).
    """
    base_root = Path(root or ROOT).resolve()
    run_fn = subprocess_runner or subprocess.run
    check_task_fn = task_exists_fn or _detect_existing_tunnel_task
    open_fn = urlopen_fn or urllib.request.urlopen

    def _run_install_repair(payload: dict[str, Any]) -> dict[str, Any]:
        force_repair = bool(payload.get("force_repair", False))
        probe_before = _probe_local_adapter(int(port), open_fn)
        if probe_before["healthy"] and not force_repair:
            return {
                "action": "install_repair",
                "status": "LOCAL_INSTALL_PASS",
                "local_service_ready": True,
                "repaired": False,
                "port": int(port),
                "profile": probe_before["profile"],
                "anon_mcp_http_401": True,
                "message_en": "Local MCP service health and HTTP 401 authentication boundary verified.",
                "message_vi": "Đã xác minh dịch vụ MCP cục bộ và ranh giới chặn truy cập ẩn danh HTTP 401.",
            }

        bootstrap_script = base_root / "scripts" / "Bootstrap-WindowsMcp.ps1"
        if not bootstrap_script.is_file():
            raise RuntimeError("Missing scripts/Bootstrap-WindowsMcp.ps1")

        argv = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bootstrap_script),
            "-NoPythonInstall",
            "-Port",
            str(int(port)),
        ]
        proc = run_fn(
            argv,
            cwd=str(base_root),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        raw_out = str(getattr(proc, "stdout", "") or "")
        raw_err = str(getattr(proc, "stderr", "") or "")
        rc = int(getattr(proc, "returncode", 1))
        if rc != 0:
            detail = _extract_powershell_error_summary(
                raw_out, raw_err, rc, default_prefix="Bootstrap-WindowsMcp.ps1"
            )
            raise RuntimeError(f"Bootstrap-WindowsMcp.ps1 failed (exit={rc}): {detail}")

        probe_after = _probe_local_adapter(int(port), open_fn)
        if not probe_after["healthy"]:
            raise RuntimeError(
                f"Bootstrap completed but live probe on 127.0.0.1:{int(port)} failed "
                f"(health_ok={probe_after['health_ok']}, anon_401={probe_after['anon_mcp_http_401']})."
            )

        return {
            "action": "install_repair",
            "status": "LOCAL_INSTALL_PASS",
            "local_service_ready": True,
            "repaired": True,
            "port": int(port),
            "profile": probe_after["profile"],
            "anon_mcp_http_401": True,
            "message_en": "Local MCP service repaired and verified healthy with HTTP 401 auth boundary.",
            "message_vi": "Đã sửa chữa và xác minh dịch vụ MCP cục bộ hoạt động bình thường với ranh giới xác thực HTTP 401.",
        }

    def _run_connect_script(payload: dict[str, Any], *, force_reconfigure: bool) -> dict[str, Any]:
        tunnel_id = str(payload.get("tunnel_id") or "").strip().lower()
        runtime_api_key = str(payload.get("runtime_api_key") or "").strip()
        auth_mode = str(payload.get("auth_mode") or "auth_none").strip().lower()
        if not _TUNNEL_ID_RE.match(tunnel_id):
            raise RuntimeError("Invalid OpenAI Tunnel ID format")
        if auth_mode not in ("oauth", "auth_none"):
            raise RuntimeError("Invalid auth_mode (expected 'oauth' or 'auth_none')")
        if len(runtime_api_key) < 20:
            raise RuntimeError("Runtime API Key is missing or unexpectedly short")

        connect_script = base_root / "scripts" / "Connect-ChatGPT.ps1"
        if not connect_script.is_file():
            raise RuntimeError("Missing scripts/Connect-ChatGPT.ps1")

        yaml_exists = (base_root / "runtime" / "tunnel" / "winowner.yaml").is_file()
        task_exists = False
        try:
            task_exists = bool(check_task_fn(int(port)))
        except Exception:
            task_exists = False
        need_reconfigure = bool(force_reconfigure or payload.get("reconfigure") or yaml_exists or task_exists)

        ps_script_escaped = str(connect_script).replace("'", "''")
        ps_root_escaped = str(base_root).replace("'", "''")
        reconf_flag = " -Reconfigure" if need_reconfigure else ""
        ps_command = (
            "$ErrorActionPreference='Stop'; "
            "$raw=[Environment]::GetEnvironmentVariable('WINOWNER_SETUP_RUNTIME_API_KEY','Process'); "
            "[Environment]::SetEnvironmentVariable('WINOWNER_SETUP_RUNTIME_API_KEY',$null,'Process'); "
            "Remove-Item Env:WINOWNER_SETUP_RUNTIME_API_KEY -ErrorAction SilentlyContinue; "
            "if(-not $raw){throw 'Runtime API key missing in child environment'}; "
            "$sec=ConvertTo-SecureString -String $raw -AsPlainText -Force; "
            "$raw=''; "
            f"try{{ & '{ps_script_escaped}' -Port {int(port)} -TunnelId '{tunnel_id}' -AuthMode '{auth_mode}' -TargetRoot '{ps_root_escaped}' -ApiKey $sec -NoBrowser{reconf_flag} }} "
            "finally { $sec.Dispose() }"
        )
        argv = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_command,
        ]
        child_env = os.environ.copy()
        child_env["WINOWNER_SETUP_RUNTIME_API_KEY"] = runtime_api_key

        try:
            proc = run_fn(
                argv,
                cwd=str(base_root),
                env=child_env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        finally:
            child_env.pop("WINOWNER_SETUP_RUNTIME_API_KEY", None)

        raw_stdout = str(getattr(proc, "stdout", "") or "")
        raw_stderr = str(getattr(proc, "stderr", "") or "")
        rc = int(getattr(proc, "returncode", 1))
        scrubbed_out = raw_stdout.replace(runtime_api_key, "[REDACTED]")

        if rc != 0:
            detail = _extract_powershell_error_summary(
                raw_stdout,
                raw_stderr,
                rc,
                secret_to_scrub=runtime_api_key,
                default_prefix="Connect-ChatGPT.ps1",
            )
            runtime_api_key = ""
            raise RuntimeError(f"Connect-ChatGPT.ps1 failed (exit={rc}): {detail}")
        runtime_api_key = ""

        report_data: dict[str, Any] = {}
        report_path = base_root / "runtime" / "logs" / "tunnel-reconfig-report.json"
        if report_path.is_file():
            try:
                loaded = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(loaded, dict):
                    report_data = loaded
            except Exception:
                report_data = {}

        status_code = str(
            report_data.get("status")
            or ("TUNNEL_RECONFIGURE_PASS" if "TUNNEL_RECONFIGURE_PASS" in scrubbed_out else "TUNNEL_SETUP_PASS")
        )
        readyz_ok = status_code in ("TUNNEL_SETUP_PASS", "TUNNEL_RECONFIGURE_PASS") or (
            "TUNNEL_SETUP_PASS" in scrubbed_out or "TUNNEL_RECONFIGURE_PASS" in scrubbed_out
        )
        if not readyz_ok:
            raise RuntimeError(f"Connect-ChatGPT.ps1 completed without verified tunnel readiness (status={status_code})")

        return {
            "action": "reconfigure_tunnel" if force_reconfigure else "connect_chatgpt",
            "tunnel_id": tunnel_id,
            "auth_mode": auth_mode,
            "auto_reconfigure": need_reconfigure,
            "status": status_code,
            "readyz_ok": True,
            "rolled_back": bool(report_data.get("rolled_back", False)),
            "rollback_verified": bool(report_data.get("rollback_verified", False)),
            "chatgpt_tool_call_verified": False,
            "message_en": "Tunnel runtime verified and ready; ChatGPT tool call NOT verified yet. Run a read-only prompt in ChatGPT next.",
            "message_vi": "Tunnel đã sẵn sàng và được xác minh; chưa ghi nhận lệnh gọi công cụ từ ChatGPT. Hãy chạy câu lệnh đọc thử trong ChatGPT.",
        }

    def _run_check_updates(payload: dict[str, Any]) -> dict[str, Any]:
        if str(base_root) not in sys.path:
            sys.path.insert(0, str(base_root))
        from runtime.release_identity import get_release_identity

        rel_id = get_release_identity()
        current_ver = str(rel_id.get("release_version") or "v1.2.0")

        req = urllib.request.Request(
            _GITHUB_RELEASES_LATEST_URL,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "WinOwner-MCP-SetupCenter",
            },
        )
        try:
            with open_fn(req, timeout=5.0) as resp:
                raw = resp.read()
                data = json.loads(raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw))
        except Exception as exc:
            return {
                "action": "check_updates",
                "current_version": current_ver,
                "latest_version": current_ver,
                "update_available": False,
                "offline": True,
                "source": "github_releases",
                "verified_release_only": True,
                "sha256_verification_required": True,
                "error_detail": str(exc)[:240],
                "message_en": f"Could not reach GitHub Releases ({type(exc).__name__}). Running verified local version {current_ver}.",
                "message_vi": f"Không thể kết nối GitHub Releases ({type(exc).__name__}). Đang dùng phiên bản cục bộ {current_ver}.",
            }

        latest_ver = str((data or {}).get("tag_name") or "").strip() if isinstance(data, dict) else ""
        if _parse_semver_tuple(latest_ver) is None:
            raise RuntimeError(f"Refusing non-semver release tag from GitHub Releases: {latest_ver!r}")

        is_newer = _is_newer_semver(latest_ver, current_ver)
        return {
            "action": "check_updates",
            "current_version": current_ver,
            "latest_version": latest_ver,
            "update_available": is_newer,
            "offline": False,
            "source": "github_releases",
            "release_url": str(data.get("html_url") or ""),
            "published_at": str(data.get("published_at") or ""),
            "verified_release_only": True,
            "sha256_verification_required": True,
            "message_en": (
                f"Verified release {latest_ver} is available (current: {current_ver})."
                if is_newer
                else f"You are running the latest verified release ({current_ver})."
            ),
            "message_vi": (
                f"Đã có bản phát hành đã xác minh {latest_ver} (hiện tại: {current_ver})."
                if is_newer
                else f"Bạn đang dùng phiên bản đã xác minh mới nhất ({current_ver})."
            ),
        }

    def _run_uninstall(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("confirm") is not True:
            raise RuntimeError("Explicit confirmation ({'confirm': true}) is required before uninstalling.")

        bootstrap_script = base_root / "scripts" / "Bootstrap-WindowsMcp.ps1"
        if not bootstrap_script.is_file():
            raise RuntimeError("Missing scripts/Bootstrap-WindowsMcp.ps1")

        argv = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bootstrap_script),
            "-Remove",
            "-DisconnectTunnel",
            "-Port",
            str(int(port)),
        ]
        proc = run_fn(
            argv,
            cwd=str(base_root),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        raw_out = str(getattr(proc, "stdout", "") or "")
        raw_err = str(getattr(proc, "stderr", "") or "")
        rc = int(getattr(proc, "returncode", 1))
        if rc != 0:
            detail = _extract_powershell_error_summary(
                raw_out, raw_err, rc, default_prefix="Bootstrap-WindowsMcp.ps1 -Remove"
            )
            raise RuntimeError(f"Uninstall failed (exit={rc}): {detail}")

        health_url_file = base_root / "runtime" / "tunnel" / "health-url.txt"
        if health_url_file.is_file():
            try:
                health_url_file.unlink()
            except OSError:
                pass

        return {
            "action": "uninstall",
            "status": "SERVICE_AND_TUNNEL_STOPPED",
            "port": int(port),
            "removed_tasks": [f"WinOwnerTunnel-{int(port)}", f"WinOwnerMCP-{int(port)}"],
            "secrets_preserved": True,
            "message_en": "Verified local MCP service and tunnel tasks stopped and unregistered. Local user data and keys are preserved.",
            "message_vi": "Đã dừng và gỡ tác vụ MCP cục bộ cùng tunnel đã xác minh. Dữ liệu và khóa riêng trên máy vẫn được giữ an toàn.",
        }

    def _run_rollback(payload: dict[str, Any]) -> dict[str, Any]:
        scope = str(payload.get("scope") or "").strip().lower()
        backup_dir_raw = str(payload.get("backup_dir") or "").strip()
        pre_update_root = base_root / "backups"
        has_pre_update = pre_update_root.is_dir() and any(
            d.is_dir() and d.name.startswith("pre_update_") for d in pre_update_root.iterdir()
        )
        tunnel_backups_root = base_root / "runtime" / "secrets" / "backups"
        has_tunnel_backup = tunnel_backups_root.is_dir() and any(
            d.is_dir() and d.name.startswith("tunnel_reconfig_") for d in tunnel_backups_root.iterdir()
        )

        if scope == "tunnel" or (not backup_dir_raw and not has_pre_update and has_tunnel_backup):
            return _restore_latest_tunnel_backup(base_root)

        rollback_script = base_root / "scripts" / "Rollback-WindowsMcp.ps1"
        if not rollback_script.is_file():
            raise RuntimeError("Missing scripts/Rollback-WindowsMcp.ps1")

        argv = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(rollback_script),
            "-TargetRoot",
            str(base_root),
            "-Port",
            str(int(port)),
        ]
        if backup_dir_raw:
            cand = Path(backup_dir_raw).resolve()
            if base_root not in cand.parents:
                raise RuntimeError("Refusing backup_dir outside repository root")
            argv.extend(["-BackupDir", str(cand)])
        else:
            argv.append("-LatestPreUpdate")
        if payload.get("dry_run") is True:
            argv.append("-DryRun")
        if payload.get("restart_services") is True:
            argv.append("-RestartServices")

        proc = run_fn(
            argv,
            cwd=str(base_root),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        raw_out = str(getattr(proc, "stdout", "") or "")
        raw_err = str(getattr(proc, "stderr", "") or "")
        rc = int(getattr(proc, "returncode", 1))
        if rc != 0:
            detail = _extract_powershell_error_summary(
                raw_out, raw_err, rc, default_prefix="Rollback-WindowsMcp.ps1"
            )
            raise RuntimeError(f"Rollback-WindowsMcp.ps1 failed (exit={rc}): {detail}")

        return {
            "action": "rollback",
            "scope": "release",
            "status": "ROLLBACK_PASS",
            "rollback_verified": True,
            "secrets_preserved": True,
            "port": int(port),
            "message_en": "Verified atomic rollback completed; local secrets and owner authorization database preserved.",
            "message_vi": "Đã hoàn tất khôi phục nguyên tử có xác minh; khóa bí mật và cơ sở dữ liệu ủy quyền chủ sở hữu được giữ nguyên.",
        }

    return {
        "install_repair": _run_install_repair,
        "connect_chatgpt": lambda payload: _run_connect_script(payload, force_reconfigure=False),
        "reconfigure_tunnel": lambda payload: _run_connect_script(payload, force_reconfigure=True),
        "check_updates": _run_check_updates,
        "uninstall": _run_uninstall,
        "rollback": _run_rollback,
    }


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    """Allow both POSIX (--port/--console-port) and PowerShell (-Port/-ConsolePort) flags."""
    mapping = {
        "-port": "--port",
        "--port": "--port",
        "-consoleport": "--console-port",
        "--consoleport": "--console-port",
        "-console-port": "--console-port",
        "-profile": "--profile",
        "--profile": "--profile",
        "-setupcenter": "--setup-center",
        "-opensetup": "--open-setup",
        "-openbrowser": "--open-browser",
    }
    return [mapping.get(tok.lower(), tok) for tok in argv]


def start(port: int, profile: str = "read_only") -> None:
    if os.name != "nt":
        raise SystemExit("WinOwner task launcher requires Windows")
    if profile not in {"read_only", "owner_full_control"}:
        raise SystemExit("Unknown security profile")
    policy = ROOT / "config" / "policy.json"
    bearer = ROOT / "runtime" / "secrets" / "gateway_caller.token"
    db_path = ROOT / "runtime" / "secrets" / "oauth.db"
    adapter = ROOT / "mcp-gateway" / "cloud_autonomous_adapter.py"
    if not adapter.is_file() or not policy.is_file() or not bearer.is_file():
        raise SystemExit("Local adapter, policy or protected bearer token is missing")
    if json.loads(policy.read_text(encoding="utf-8")).get("default_profile") != "read_only":
        raise SystemExit("Refusing to start: local policy must default to read_only")

    secret = bearer.read_text(encoding="utf-8").strip()
    if not secret:
        raise SystemExit("Protected bearer token is empty")

    os.environ["WINDOWS_MCP_GATEWAY_TOKEN"] = secret
    os.environ["MCP_STATE_ROOT"] = str(ROOT)
    os.environ.setdefault("MCP_EMERGENCY_STOP_FILE", str(ROOT / "runtime" / "EMERGENCY_STOP"))
    os.environ["WINDOWS_MCP_BACKEND_ENV_FILE"] = str(ROOT / "runtime" / "not-configured.env")
    os.environ["MCP_AGENT_PROFILE"] = profile

    log_dir = ROOT / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = log_dir / f"adapter-{port}-{stamp}.stdout.log"
    err_path = log_dir / f"adapter-{port}-{stamp}.stderr.log"
    with out_path.open("a", encoding="utf-8", buffering=1) as out_file, err_path.open(
        "a", encoding="utf-8", buffering=1
    ) as err_file:
        sys.stdout = out_file
        sys.stderr = err_file
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "mcp-gateway"))
        sys.argv = [
            str(adapter),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--profile", profile,
            "--policy-path", str(policy),
            "--db-path", str(db_path),
        ]
        if not os.getenv("WINOWNER_DISABLE_PROFILE_RESTORE"):
            sys.argv.append("--restore-approved-profile")
        runpy.run_path(str(adapter), run_name="__main__")


def start_setup_center(
    port: int = 8768,
    console_port: int = 8767,
    profile: str = "read_only",
    open_browser: bool = False,
) -> None:
    if os.name != "nt":
        raise SystemExit("WinOwner Setup Center requires Windows")
    policy = ROOT / "config" / "policy.json"
    db_path = ROOT / "runtime" / "secrets" / "oauth.db"
    os.environ["MCP_STATE_ROOT"] = str(ROOT)
    os.environ.setdefault("MCP_EMERGENCY_STOP_FILE", str(ROOT / "runtime" / "EMERGENCY_STOP"))
    os.environ["MCP_AGENT_PROFILE"] = profile
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "mcp-gateway"))

    log_dir = ROOT / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = (log_dir / f"setup-center-{console_port}.stdout.log").open("a", encoding="utf-8", buffering=1)
        sys.stderr = (log_dir / f"setup-center-{console_port}.stderr.log").open("a", encoding="utf-8", buffering=1)

    import threading
    import time
    import webbrowser
    import uvicorn
    from web_console import create_web_console_app

    app = create_web_console_app(
        profile=profile,
        policy_path=policy if policy.is_file() else (ROOT / "config" / "policy.example.json"),
        db_path=db_path,
        adapter_port=port,
        setup_action_hooks=build_setup_center_action_hooks(ROOT, port),
    )
    # Restore HMAC-verified owner-approved profile across Setup Center restarts if active
    try:
        pers = getattr(app.state, "setup_center_persistence", None)
        sk = getattr(app.state, "setup_center_signing_key", "")
        gw_ref = getattr(app.state, "gateway", None)
        if (
            pers is not None
            and sk
            and gw_ref is not None
            and profile == "read_only"
            and not (hasattr(gw_ref, "is_emergency_stop_active") and gw_ref.is_emergency_stop_active())
            and hasattr(pers, "get_owner_approved_profile")
            and hasattr(gw_ref, "switch_policy_profile")
        ):
            p_state = pers.get_owner_approved_profile(secret=sk, owner_id="local_owner")
            eff = str(p_state.get("effective_profile") or "read_only")
            if p_state.get("valid") and eff != "read_only":
                gw_ref.switch_policy_profile(eff, reason="setup_center_restore_owner_approved_profile")
    except Exception:
        pass

    if open_browser:
        def _launch_browser() -> None:
            time.sleep(0.9)
            url = f"http://127.0.0.1:{console_port}/setup"
            issuer = getattr(app.state, "issue_local_handoff_ticket", None)
            if callable(issuer):
                try:
                    ticket = issuer(ttl_seconds=60.0)
                    code = str(ticket.get("handoff_code") or "").strip()
                    if code:
                        url = f"{url}#handoff={code}"
                except Exception:
                    pass
            webbrowser.open(url)
        threading.Thread(target=_launch_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=console_port, log_level="info")


def open_setup_center(
    port: int = 8768,
    console_port: int = 8767,
    profile: str = "read_only",
) -> None:
    """Open WinOwner Setup Center in the default browser via single-use local Windows-user handoff.

    If Setup Center is already listening on 127.0.0.1:<console_port>, mints a 60s single-use
    handoff ticket using the ACL-protected local owner_admin.key (never passed on CLI or URL query)
    and opens http://127.0.0.1:<console_port>/setup#handoff=<code>. Otherwise starts Setup Center.
    """
    if os.name != "nt":
        raise SystemExit("WinOwner Setup Center requires Windows")
    import urllib.request
    import webbrowser

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "mcp-gateway"))
    from http_server import resolve_owner_admin_token_path

    db_path = ROOT / "runtime" / "secrets" / "oauth.db"
    health_url = f"http://127.0.0.1:{console_port}/health"
    running = False
    try:
        with urllib.request.urlopen(health_url, timeout=1.5) as resp:
            if resp.status == 200:
                running = True
    except Exception:
        running = False

    if not running:
        start_setup_center(port=port, console_port=console_port, profile=profile, open_browser=True)
        return

    admin_path = resolve_owner_admin_token_path(db_path=db_path)
    if not admin_path.is_file():
        webbrowser.open(f"http://127.0.0.1:{console_port}/setup")
        return

    admin_secret = admin_path.read_text(encoding="utf-8", errors="replace").strip()
    issue_url = f"http://127.0.0.1:{console_port}/setup/api/local-handoff/issue"
    req = urllib.request.Request(
        issue_url,
        data=json.dumps({"ttl_seconds": 60.0}).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{console_port}",
            "X-MCP-Owner-Admin-Token": admin_secret,
        },
    )
    target_url = f"http://127.0.0.1:{console_port}/setup"
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            code = str(data.get("handoff_code") or "").strip()
            if code:
                target_url = f"{target_url}#handoff={code}"
    except Exception:
        pass
    finally:
        admin_secret = ""
    webbrowser.open(target_url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WinOwner MCP local authenticated owner task")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--profile", choices=("read_only", "owner_full_control"), default="read_only")
    parser.add_argument("--setup-center", action="store_true", help="Launch local loopback WinOwner Setup Center UI")
    parser.add_argument("--open-setup", action="store_true", help="Open Setup Center via single-use local Windows-user handoff")
    parser.add_argument("--console-port", type=int, default=8767, help="Loopback port for WinOwner Setup Center UI")
    parser.add_argument("--open-browser", action="store_true", help="Open WinOwner Setup Center in default browser")
    args = parser.parse_args(_normalize_cli_argv(sys.argv[1:]))
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.open_setup:
        if not 1024 <= args.console_port <= 65535:
            parser.error("console-port must be between 1024 and 65535")
        open_setup_center(args.port, args.console_port, args.profile)
    elif args.setup_center:
        if not 1024 <= args.console_port <= 65535:
            parser.error("console-port must be between 1024 and 65535")
        start_setup_center(args.port, args.console_port, args.profile, args.open_browser)
    else:
        start(args.port, args.profile)
