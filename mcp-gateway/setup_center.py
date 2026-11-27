"""Phase B/C/D/E — WinOwner Setup Center & Native Windows Fluent 4-Step Onboarding Wizard.

Provides:
1. Authoritative 4-Step State Machine reflecting real backend truth (ZERO fake success states):
   LOCAL_READY -> CHATGPT_CONNECTED -> OWNER_VERIFIED -> OWNER_CONTROL_READY
   plus deterministic 9-state UI lifecycle (`loading`, `connecting`, `connection_failed`,
   `waiting_authorization`, `revoked`, `expired`, `emergency_stopped`, `offline`, `reconnecting`).
2. Native Windows Fluent Light 4-Step Onboarding Wizard (`/setup`) designed for non-technical users:
   - Step 1: Setup (automatic readiness checks + Retry / Repair on failure)
   - Step 2: Connect ChatGPT (Tunnel ID + API Key, key preserved on error, inline alerts, real verification)
   - Step 3: Authorize (clear consent summary, verification code, single-click Confirm Owner Access)
   - Step 4: Ready (honest permission pill, Open ChatGPT, Test connection, 3 harmless examples, Revoke / E-Stop)
3. Slide-over Advanced Settings drawer (`Cài đặt nâng cao` / `Advanced Settings`):
   - 6-row live diagnostic status table (`Local Service`, `Tunnel`, `ChatGPT`, `Owner Identity`, `Permissions`, `Version`)
   - Redacted diagnostic/audit Logs (`GET /setup/api/logs`)
   - Repair, Check for Updates, Rollback (`POST /setup/api/actions/rollback`), Reconfigure Tunnel, Uninstall, Logout
4. Strict security invariants:
   - Bilingual VI & EN parity, zero secret exposure, DOM textContent-only rendering,
   - Loopback + Host/Origin/Referer + HttpOnly SameSite=Strict cookie + multi-tab CSRF handoff reuse & auto-resync.
"""
from __future__ import annotations

import asyncio
import getpass
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
for _p in (HERE, PROJECT_ROOT, PROJECT_ROOT / "backend", PROJECT_ROOT / "desktop-agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from policy import redact_secrets  # noqa: E402
from http_server import (  # noqa: E402
    _SQLiteOAuthPersistence,
    load_gateway_auth_token,
    load_oauth_signing_key,
    load_owner_admin_token,
    resolve_oauth_db_path,
)

TUNNEL_ID_REGEX = re.compile(r"^tunnel_(?:[a-z0-9]{32}|[a-z0-9]{4}_[a-z0-9]{32})$")
SEMVER_TAG_REGEX = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")

FOUR_STEP_SEQUENCE = (
    "LOCAL_READY",
    "CHATGPT_CONNECTED",
    "OWNER_VERIFIED",
    "OWNER_CONTROL_READY",
)

NINE_UI_STATES = (
    "loading",
    "connecting",
    "connection_failed",
    "waiting_authorization",
    "revoked",
    "expired",
    "emergency_stopped",
    "offline",
    "reconnecting",
)

_LOG_SECRET_PATTERNS = (
    re.compile(r"(Bearer\s+)[^\s\"',;]+", re.IGNORECASE),
    re.compile(r"\b(?:sk|rk|mcp_oadm|hnd|wsess)_[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(
        r"((?:runtime_api_key|api_key|access_token|refresh_token|client_secret|password|owner_admin_token|signing_key|cookie_token|csrf_token)\s*[:=]\s*['\"]?)[^'\"\s,;}{]+",
        re.IGNORECASE,
    ),
)


def get_current_windows_user_identity() -> str:
    """Return canonical lowercased Windows user identity for local handoff binding."""
    domain = (os.getenv("USERDOMAIN") or "").strip().lower()
    user = (os.getenv("USERNAME") or "").strip().lower()
    if not user:
        try:
            user = getpass.getuser().strip().lower()
        except Exception:
            user = "local_owner"
    return f"{domain}\\{user}" if domain else user


def _parse_semver_tuple(tag: str) -> tuple[int, int, int, int, str] | None:
    m = SEMVER_TAG_REGEX.match(str(tag or "").strip())
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
    return c_parsed > cur_parsed


def _default_local_service_probe(port: int = 8768) -> bool:
    """Live-probe GET http://127.0.0.1:<port>/health to confirm local MCP adapter liveness."""
    url = f"http://127.0.0.1:{int(port)}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.2) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return bool(
                isinstance(data, dict)
                and data.get("status") == "ok"
                and data.get("require_autonomous_auth") is True
                and data.get("allow_loopback_tunnel_identity") is False
            )
    except Exception:
        return False


def _default_tunnel_probe(root: Path, port: int = 8768) -> dict[str, Any]:
    """Probe local tunnel readiness via health-url.txt /readyz without exposing credentials."""
    health_url_file = root / "runtime" / "tunnel" / "health-url.txt"
    profile_file = root / "runtime" / "tunnel" / "winowner.yaml"
    configured = profile_file.is_file()
    readyz_ok = False
    detail = "TUNNEL_NOT_CONFIGURED"

    if health_url_file.is_file():
        try:
            raw_url = health_url_file.read_text(encoding="utf-8", errors="replace").strip()
            if re.match(r"^http://127\.0\.0\.1:\d{1,5}/?$", raw_url):
                probe_url = raw_url.rstrip("/") + "/readyz"
                req = urllib.request.Request(probe_url, method="GET")
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        readyz_ok = True
                        detail = "TUNNEL_READY"
                    else:
                        detail = "TUNNEL_NOT_READY"
            else:
                detail = "TUNNEL_UNVERIFIED"
        except Exception:
            detail = "TUNNEL_NOT_READY" if configured else "TUNNEL_NOT_CONFIGURED"
    elif configured:
        detail = "TUNNEL_NOT_RUNNING"

    return {
        "connected": bool(readyz_ok),
        "configured": bool(configured),
        "readyz_ok": bool(readyz_ok),
        "status_code": detail,
    }


def _has_rollback_snapshot(root: Path) -> bool:
    """Return True if at least one verified pre-update or tunnel reconfiguration backup exists."""
    try:
        pre_update = root / "backups"
        if pre_update.is_dir() and any(d.is_dir() and d.name.startswith("pre_update_") for d in pre_update.iterdir()):
            return True
        tunnel_bkp = root / "runtime" / "secrets" / "backups"
        if tunnel_bkp.is_dir() and any(
            d.is_dir() and d.name.startswith("tunnel_reconfig_") for d in tunnel_bkp.iterdir()
        ):
            return True
    except Exception:
        return False
    return False


def _scrub_log_text(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    """Scrub all credential-like patterns and known runtime secrets from log lines."""
    out = str(redact_secrets(text or ""))
    for sec in extra_secrets:
        if sec and len(sec) >= 6:
            out = out.replace(sec, "[REDACTED]")
    for pat in _LOG_SECRET_PATTERNS:
        if pat.groups:
            out = pat.sub(r"\1[REDACTED]", out)
        else:
            out = pat.sub("[REDACTED]", out)
    return out


def _inspect_last_delegation_lifecycle(persistence: Any) -> str:
    """Inspect SQLite owner_durable_delegations to distinguish 'active', 'revoked', 'expired', or 'none'."""
    if persistence is None or not hasattr(persistence, "_db"):
        return "none"
    now = time.time()
    try:
        with persistence._db() as conn:
            row = conn.execute(
                """
                SELECT revoked, expires_at
                FROM owner_durable_delegations
                WHERE owner_id = 'local_owner'
                ORDER BY issued_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return "none"
        revoked = bool(row[0])
        expires_at = float(row[1] or 0.0)
        if revoked:
            return "revoked"
        if expires_at > 0 and now >= expires_at:
            return "expired"
        return "active"
    except Exception:
        return "none"


def _resolve_persistence_and_keys(
    gw: Any,
    *,
    oauth_persistence: Any | None = None,
    oauth_signing_key: str | None = None,
    owner_admin_token: str | None = None,
    db_path: str | Path | None = None,
) -> tuple[_SQLiteOAuthPersistence, str, str]:
    pers = oauth_persistence or getattr(gw, "_oauth_persistence", None)
    resolved_db = resolve_oauth_db_path(db_path=db_path, gateway=gw)
    if pers is None:
        pers = _SQLiteOAuthPersistence(resolved_db)
        try:
            gw._oauth_persistence = pers
        except Exception:
            pass

    caller_tok = getattr(gw, "_caller_auth_token", None) or load_gateway_auth_token()
    sk = (
        oauth_signing_key
        or getattr(gw, "_oauth_signing_key", None)
        or load_oauth_signing_key(db_path=resolved_db, caller_auth_token=caller_tok)
    )
    adm = (
        owner_admin_token
        or getattr(gw, "_owner_admin_token", None)
        or load_owner_admin_token(db_path=resolved_db, caller_auth_token=caller_tok, oauth_signing_key=sk)
    )
    try:
        gw._oauth_signing_key = sk
        gw._owner_admin_token = adm
    except Exception:
        pass
    return pers, sk, adm


def compute_setup_center_state(
    gw: Any,
    *,
    persistence: _SQLiteOAuthPersistence,
    signing_key: str,
    port: int = 8768,
    local_service_ready: bool = True,
    tunnel_info: dict[str, Any] | None = None,
    version_state: dict[str, Any] | None = None,
    last_action_revoked: bool = False,
    rollback_available: bool | None = None,
) -> dict[str, Any]:
    """Compute the authoritative 4-step state machine, 9-state UI model, and 6-row status table from backend truth."""
    from runtime.release_identity import get_release_identity

    rel_id = get_release_identity()
    current_version = str(rel_id.get("release_version") or "v1.2.0")

    pol = getattr(gw, "policy", None)
    profile_name = str(getattr(pol, "profile", "read_only")) if pol is not None else "read_only"
    is_estop = bool(gw.is_emergency_stop_active()) if (gw is not None and hasattr(gw, "is_emergency_stop_active")) else False

    local_auth_enforced = True
    allow_loopback_tunnel_identity = False
    local_ok = bool(local_service_ready and local_auth_enforced and not allow_loopback_tunnel_identity)

    cg_raw = persistence.get_chatgpt_connection_state() if hasattr(persistence, "get_chatgpt_connection_state") else {}
    active_dels = (
        persistence.list_active_owner_delegations(secret=signing_key, owner_id="local_owner")
        if hasattr(persistence, "list_active_owner_delegations")
        else []
    )
    active_grants_cnt = (
        persistence.count_active_owner_grants(owner_id="local_owner")
        if hasattr(persistence, "count_active_owner_grants")
        else 0
    )
    pending_challenges = (
        persistence.list_owner_pairing_challenges(status="PENDING")
        if hasattr(persistence, "list_owner_pairing_challenges")
        else []
    )

    if tunnel_info is None:
        t_info = _default_tunnel_probe(PROJECT_ROOT, port=port)
    else:
        t_info = dict(tunnel_info)

    tunnel_connected = bool(t_info.get("connected", False))
    tunnel_configured = bool(t_info.get("configured", False))
    tunnel_status_code = str(t_info.get("status_code") or "TUNNEL_NOT_CONFIGURED")
    tunnel_explicitly_disconnected = bool(t_info.get("explicit_disconnect", False))

    # ChatGPT Verified requires a REAL verified read-only MCP tool call recorded by backend.
    # NEVER claim Verified from /health, /readyz, initialize, or tools/list alone!
    backend_read_verified = bool(cg_raw.get("verified_read_tool_call", False))
    if not local_ok or tunnel_explicitly_disconnected:
        chatgpt_verified = False
    else:
        chatgpt_verified = backend_read_verified

    has_verified_owner = bool(local_ok and (len(active_dels) > 0 or active_grants_cnt > 0))
    top_del = active_dels[0] if active_dels else None
    verified_cid = str(top_del["client_id"]) if top_del else ""
    verified_cid_masked = (f"{verified_cid[:12]}..." if len(verified_cid) > 15 else verified_cid) if verified_cid else ""
    expires_iso = (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(top_del["expires_at"])))
        if top_del and top_del.get("expires_at")
        else None
    )
    rem_sec = int(top_del.get("remaining_seconds", 0)) if top_del else 0

    enabled_tiers = sorted(t.value for t in getattr(pol, "enabled_tiers", frozenset())) if pol is not None else ["READ"]
    if not local_ok or is_estop or profile_name == "read_only" or not has_verified_owner:
        eff_level = "READ"
        ui_perm_label = "Read"
        eff_tiers = ["READ"]
        eff_workspaces = [str(r) for r in getattr(pol, "read_roots", [PROJECT_ROOT])] if pol is not None else [str(PROJECT_ROOT)]
        allow_dest = False
        owner_control_ready = False
    elif profile_name in ("owner_full_control", "full_agent"):
        unrestricted_ws = bool(getattr(pol, "unrestricted_workspace_access", False))
        del_global = bool(not top_del or top_del.get("owner_global_filesystem", False))
        if unrestricted_ws and del_global:
            eff_level = "OWNER"
            ui_perm_label = "Owner"
        else:
            eff_level = "SCOPED"
            ui_perm_label = "Scoped"
        eff_tiers = enabled_tiers
        eff_workspaces = (
            top_del["allowed_workspaces"]
            if (top_del and top_del.get("allowed_workspaces"))
            else [str(r) for r in getattr(pol, "write_roots", [PROJECT_ROOT])]
        )
        allow_dest = bool(top_del.get("allow_destructive_operations", False)) if top_del else False
        owner_control_ready = True
    else:
        eff_level = "SCOPED"
        ui_perm_label = "Scoped"
        eff_tiers = enabled_tiers
        eff_workspaces = (
            top_del["allowed_workspaces"]
            if (top_del and top_del.get("allowed_workspaces"))
            else [str(r) for r in getattr(pol, "write_roots", [PROJECT_ROOT])]
        )
        allow_dest = bool(top_del.get("allow_destructive_operations", False)) if top_del else False
        owner_control_ready = True

    v_state = version_state or {}
    latest_ver = str(v_state.get("latest_version") or current_version)
    update_available = bool(
        v_state.get("update_available", False) or _is_newer_semver(latest_ver, current_version)
    )
    version_label = "Update Available" if update_available else "Current"

    # 4-Step State Machine: LOCAL_READY -> CHATGPT_CONNECTED -> OWNER_VERIFIED -> OWNER_CONTROL_READY
    step1_done = local_ok
    step2_done = bool(step1_done and chatgpt_verified)
    step3_done = bool(step2_done and has_verified_owner)
    step4_done = bool(step3_done and owner_control_ready and not is_estop and ui_perm_label in ("Scoped", "Owner"))

    if not step1_done:
        current_state = "LOCAL_ERROR"
        completed_steps_count = 0
    elif not step2_done:
        current_state = "LOCAL_READY"
        completed_steps_count = 1
    elif not step3_done:
        current_state = "CHATGPT_CONNECTED"
        completed_steps_count = 2
    elif not step4_done:
        current_state = "OWNER_VERIFIED"
        completed_steps_count = 3
    else:
        current_state = "OWNER_CONTROL_READY"
        completed_steps_count = 4

    last_delegation_status = "active" if has_verified_owner else _inspect_last_delegation_lifecycle(persistence)
    if not has_verified_owner and last_action_revoked:
        last_delegation_status = "revoked"

    last_auth_mode = str(cg_raw.get("last_auth_mode") or "none")
    supports_verifiable_id = bool(cg_raw.get("supports_verifiable_owner_identity", False))

    # Centralized deterministic UI state (covers all 9 lifecycle visual states + ready states)
    if not step1_done:
        ui_state = "offline"
    elif is_estop:
        ui_state = "emergency_stopped"
    elif len(pending_challenges) > 0 and not has_verified_owner:
        ui_state = "waiting_authorization"
    elif not has_verified_owner and last_delegation_status == "revoked":
        ui_state = "revoked"
    elif not has_verified_owner and last_delegation_status == "expired":
        ui_state = "expired"
    elif not step2_done:
        if tunnel_status_code in ("TUNNEL_FAILED", "TUNNEL_ERROR"):
            ui_state = "connection_failed"
        elif tunnel_configured and not tunnel_connected and not tunnel_explicitly_disconnected:
            ui_state = "reconnecting"
        else:
            ui_state = "setup_connect"
    elif step4_done:
        ui_state = "ready_owner_control"
    elif step3_done:
        ui_state = "ready_read_only"
    elif not supports_verifiable_id or last_auth_mode == "shared_tunnel_bearer":
        ui_state = "ready_read_only"
    else:
        ui_state = "waiting_authorization"

    steps_detail = [
        {
            "step": 1,
            "id": "LOCAL_READY",
            "completed": step1_done,
            "active": current_state == "LOCAL_READY",
            "label_en": "1. Setup",
            "label_vi": "1. Cài đặt",
        },
        {
            "step": 2,
            "id": "CHATGPT_CONNECTED",
            "completed": step2_done,
            "active": current_state == "CHATGPT_CONNECTED",
            "label_en": "2. Connect",
            "label_vi": "2. Kết nối",
        },
        {
            "step": 3,
            "id": "OWNER_VERIFIED",
            "completed": step3_done,
            "active": current_state == "OWNER_VERIFIED",
            "label_en": "3. Authorize",
            "label_vi": "3. Xác nhận quyền",
        },
        {
            "step": 4,
            "id": "OWNER_CONTROL_READY",
            "completed": step4_done,
            "active": current_state == "OWNER_CONTROL_READY",
            "label_en": "4. Ready",
            "label_vi": "4. Sẵn sàng",
        },
    ]

    # Actionable guidance in English and Vietnamese (preserves honest Authentication: None boundary explanation)
    if not step1_done:
        guidance_en = "Local MCP service is not ready. Click 'Install / Repair' to verify and start the local service."
        guidance_vi = "Dịch vụ MCP cục bộ chưa sẵn sàng. Nhấn 'Cài đặt / Sửa chữa' để kiểm tra và khởi động dịch vụ."
    elif is_estop:
        guidance_en = "Emergency Stop is active. All write/execute/owner privileges are halted (`Permissions: Read`). Reset Emergency Stop when safe."
        guidance_vi = "Dừng khẩn cấp đang bật. Mọi quyền ghi/thực thi/owner đều tạm dừng (`Quyền hạn: Read`). Hãy mở khóa Dừng khẩn cấp khi an toàn."
    elif not step2_done:
        if not tunnel_connected:
            guidance_en = "Local service is ready (`Read`). Next: click 'Connect ChatGPT' to configure your Private Tunnel, then run a read-only prompt in ChatGPT and click 'Verify Connection'."
            guidance_vi = "Dịch vụ cục bộ đã sẵn sàng (`Read`). Tiếp theo: nhấn 'Kết nối ChatGPT' để cấu hình Tunnel riêng, sau đó chạy một lệnh chỉ đọc trong ChatGPT và nhấn 'Xác minh kết nối'."
        else:
            guidance_en = "Tunnel is connected, but ChatGPT has not executed a verified read-only tool call yet. Run the test prompt in ChatGPT, then click 'Verify Connection'."
            guidance_vi = "Tunnel đã kết nối nhưng ChatGPT chưa thực hiện lệnh gọi công cụ đọc thật nào. Hãy chạy câu lệnh thử trong ChatGPT rồi nhấn 'Xác minh kết nối'."
    elif not step3_done:
        if len(pending_challenges) > 0:
            guidance_en = f"{len(pending_challenges)} verifiable owner pairing request(s) pending review. Click 'Review Owner Access' to inspect the client identity, scope, and verification code, then Approve or Deny."
            guidance_vi = f"Có {len(pending_challenges)} yêu cầu ghép nối chủ máy đang chờ duyệt. Nhấn 'Duyệt quyền Chủ máy' để kiểm tra danh tính client, phạm vi và mã xác nhận, sau đó Phê duyệt hoặc Từ chối."
        elif last_auth_mode == "shared_tunnel_bearer" or not supports_verifiable_id:
            guidance_en = "ChatGPT is connected in Read-Only mode (`Authentication: None` / shared tunnel credential). Platform boundary: `Authentication: None` does not provide verifiable per-client identity and is strictly capped at `Read`. To use Scoped or Owner control, connect via OAuth 2.1 and approve the pairing challenge locally."
            guidance_vi = "ChatGPT đang kết nối ở chế độ Chỉ đọc (`Authentication: None` / khóa tunnel dùng chung). Giới hạn nền tảng: `Authentication: None` không cung cấp danh tính client có thể xác minh nên luôn giới hạn ở mức `Read`. Để dùng quyền `Scoped` hoặc `Owner`, hãy kết nối qua OAuth 2.1 và phê duyệt yêu cầu ghép nối ngay trên máy."
        else:
            guidance_en = "ChatGPT is verified (`Read`). When ChatGPT requests owner pairing, review and confirm access here."
            guidance_vi = "ChatGPT đã xác minh (`Read`). Khi ChatGPT gửi yêu cầu xác nhận quyền chủ máy, hãy kiểm tra và phê duyệt ngay tại đây."
    elif not step4_done:
        guidance_en = f"Owner identity is verified, but the local service profile is currently '{profile_name}' (`Permissions: Read`). Switch to a scoped or owner profile via 'Install / Repair' when you want write/execute control."
        guidance_vi = f"Danh tính chủ máy đã xác minh, nhưng cấu hình dịch vụ cục bộ đang ở '{profile_name}' (`Quyền hạn: Read`). Hãy chuyển sang cấu hình có phạm vi hoặc chủ máy qua 'Cài đặt / Sửa chữa' khi cần quyền ghi/thực thi."
    else:
        guidance_en = f"Owner Control Ready (`Permissions: {ui_perm_label}`). Only verified client '{verified_cid_masked}' is authorized within approved workspaces. Use 'Revoke Access' at any time to return to Read-Only."
        guidance_vi = f"Sẵn sàng điều khiển Chủ máy (`Quyền hạn: {ui_perm_label}`). Chỉ client đã xác minh '{verified_cid_masked}' được phép thao tác trong phạm vi đã duyệt. Nhấn 'Thu hồi quyền' bất cứ lúc nào để quay về Chỉ đọc."

    status_table = {
        "local_service": "Ready" if local_ok else "Error",
        "tunnel": "Connected" if tunnel_connected else "Disconnected",
        "chatgpt": "Verified" if chatgpt_verified else "Not Verified",
        "owner_identity": "Verified" if has_verified_owner else "Pending",
        "permissions": ui_perm_label,
        "version": version_label,
    }

    redacted_challenges = []
    for ch in pending_challenges:
        redacted_challenges.append({
            "challenge_id": ch.get("challenge_id"),
            "verification_code": ch.get("verification_code"),
            "status": ch.get("status"),
            "client_id": ch.get("client_id"),
            "client_name": ch.get("client_name"),
            "auth_method": ch.get("auth_method"),
            "verifiable_identity": bool(ch.get("verifiable_identity", False)),
            "requested_permission_level": ch.get("requested_permission_level"),
            "requested_tiers": ch.get("requested_tiers", []),
            "requested_workspaces": ch.get("requested_workspaces", []),
            "requested_ttl_seconds": ch.get("requested_ttl_seconds"),
            "allow_destructive": bool(ch.get("allow_destructive", False)),
            "created_at": ch.get("created_at"),
            "expires_at": ch.get("expires_at"),
            "remaining_seconds": ch.get("remaining_seconds"),
        })

    redacted_delegations = []
    for d in active_dels:
        redacted_delegations.append({
            "delegation_id": d.get("delegation_id"),
            "client_id": d.get("client_id"),
            "owner_id": d.get("owner_id"),
            "allowed_workspaces": d.get("allowed_workspaces", []),
            "allowed_tools_count": d.get("allowed_tools_count", 0),
            "allow_destructive_operations": bool(d.get("allow_destructive_operations", False)),
            "owner_global_filesystem": bool(d.get("owner_global_filesystem", False)),
            "expires_at": d.get("expires_at"),
            "remaining_seconds": d.get("remaining_seconds", 0),
        })

    rb_avail = _has_rollback_snapshot(PROJECT_ROOT) if rollback_available is None else bool(rollback_available)

    return {
        "ok": True,
        "schema_version": "1.2.0",
        "state": current_state,
        "ui_state": ui_state,
        "last_delegation_status": last_delegation_status,
        "completed_steps_count": completed_steps_count,
        "steps": steps_detail,
        "status_table": status_table,
        "checks": {
            "runtime_ready": bool(local_service_ready),
            "service_ready": bool(local_service_ready),
            "security_ready": bool(local_auth_enforced and not allow_loopback_tunnel_identity),
        },
        "profile": profile_name,
        "emergency_stop": is_estop,
        "local_auth_enforced": local_auth_enforced,
        "allow_loopback_tunnel_identity": allow_loopback_tunnel_identity,
        "tunnel_details": {
            "connected": tunnel_connected,
            "configured": tunnel_configured,
            "readyz_ok": bool(t_info.get("readyz_ok", False)),
            "status_code": tunnel_status_code,
        },
        "chatgpt_connection": {
            "verified_read_tool_call": chatgpt_verified,
            "last_verified_tool": cg_raw.get("last_verified_tool") if chatgpt_verified else None,
            "last_verified_at": cg_raw.get("last_verified_at") if chatgpt_verified else None,
            "last_auth_mode": last_auth_mode,
            "last_client_id_masked": str(cg_raw.get("last_client_id_masked") or ""),
            "supports_verifiable_owner_identity": supports_verifiable_id,
            "verification_prompt_en": "Check my computer specs and disk space",
            "verification_prompt_vi": "Kiểm tra cấu hình máy tính và dung lượng ổ đĩa của tôi",
        },
        "owner_identity": {
            "state": "Verified" if has_verified_owner else "Pending",
            "verified": has_verified_owner,
            "owner_id": "local_owner" if has_verified_owner else None,
            "client_id": verified_cid or None,
            "client_id_masked": verified_cid_masked,
            "auth_method": "oauth_2_1_pkce" if has_verified_owner else "none",
            "active_delegation_id": top_del["delegation_id"] if top_del else None,
            "active_delegations_count": len(active_dels),
            "active_grants_count": active_grants_cnt,
            "pending_challenges_count": len(redacted_challenges),
            "expires_at": expires_iso,
            "remaining_seconds": rem_sec,
        },
        "effective_permissions": {
            "level": eff_level,
            "label": ui_perm_label,
            "enabled_tiers": eff_tiers,
            "allowed_workspaces": eff_workspaces,
            "allow_destructive_operations": allow_dest,
            "owner_control_ready": step4_done,
            "unverifiable_session_escalation_blocked": True,
        },
        "version_info": {
            "status": version_label,
            "current_version": current_version,
            "latest_version": latest_ver,
            "update_available": update_available,
            "git_sha": rel_id.get("source_commit"),
            "verified_release_only": True,
            "rollback_available": rb_avail,
        },
        "guidance": {
            "en": guidance_en,
            "vi": guidance_vi,
        },
        "pending_challenges": redacted_challenges,
        "active_delegations": redacted_delegations,
    }


SETUP_CENTER_HTML_PAGE = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>WinOwner Setup Center — v1.2.0</title>
  <style>
    :root {
      --canvas: #f3f3f3;
      --surface: #ffffff;
      --surface-subtle: #f9fafb;
      --border: #e5e7eb;
      --border-input: #d1d5db;
      --border-input-hover: #9ca3af;
      --accent: #0067c0;
      --accent-hover: #005a9e;
      --accent-active: #004b85;
      --accent-soft: #eff6ff;
      --accent-soft-border: #bfdbfe;
      --accent-halo: #dbeafe;
      --text-primary: #111827;
      --text-secondary: #4b5563;
      --text-muted: #596270;
      --success-text: #166534;
      --success-icon: #15803d;
      --success-bg: #ecfdf5;
      --success-border: #a7f3d0;
      --warn-text: #854d0e;
      --warn-icon: #b45309;
      --warn-bg: #fffbeb;
      --warn-border: #fde68a;
      --danger-text: #991b1b;
      --danger-btn: #c42b1c;
      --danger-btn-hover: #a52216;
      --danger-bg: #fef2f2;
      --danger-border: #fecaca;
      --offline-text: #374151;
      --offline-icon: #6b7280;
      --offline-bg: #f3f4f6;
      --offline-border: #d1d5db;
      --font: 'Segoe UI Variable', 'Segoe UI', system-ui, -apple-system, sans-serif;
      --mono: 'Cascadia Mono', 'Consolas', monospace;
    }

    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }
    body {
      background: var(--canvas);
      color: var(--text-primary);
      font-family: var(--font);
      font-size: 14px;
      line-height: 1.5;
      min-height: 100vh;
    }

    :focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }

    .hidden { display: none !important; }
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }

    /* Top Bar (56px, Fluent Light) */
    header[role="banner"] {
      height: 56px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 24px;
      position: sticky;
      top: 0;
      z-index: 30;
    }
    .brand-group {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .brand-name {
      font-size: 15px;
      font-weight: 600;
      color: var(--text-primary);
      letter-spacing: -0.01em;
    }
    .brand-ver {
      font-size: 11px;
      font-weight: 600;
      color: var(--text-muted);
      background: var(--surface-subtle);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 1px 7px;
    }
    .topbar-controls {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .lang-toggle {
      display: inline-flex;
      align-items: center;
      background: var(--canvas);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 2px;
      height: 34px;
      cursor: pointer;
      font-family: var(--font);
      font-size: 12px;
      font-weight: 600;
      color: var(--text-secondary);
      gap: 2px;
    }
    .lang-seg {
      padding: 4px 8px;
      border-radius: 4px;
      line-height: 1.2;
      transition: background 0.12s, color 0.12s;
    }
    .lang-seg.active {
      background: var(--surface);
      color: var(--text-primary);
      box-shadow: 0 1px 2px rgba(17,24,39,0.08);
    }
    .btn-ghost-top {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 34px;
      padding: 0 10px;
      border: 1px solid transparent;
      border-radius: 6px;
      background: transparent;
      color: var(--text-secondary);
      font-family: var(--font);
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.12s, color 0.12s;
    }
    .btn-ghost-top:hover {
      background: var(--canvas);
      color: var(--text-primary);
    }

    /* Indeterminate Progress Bar */
    .top-progress-wrap {
      height: 3px;
      width: 100%;
      background: transparent;
      overflow: hidden;
      position: fixed;
      top: 56px;
      left: 0;
      z-index: 35;
    }
    .top-progress-bar {
      height: 100%;
      width: 38%;
      background: var(--accent);
      border-radius: 2px;
      animation: fluentSlide 1.3s ease-in-out infinite;
    }
    @keyframes fluentSlide {
      0% { transform: translateX(-110%); }
      100% { transform: translateX(360%); }
    }
    @keyframes fluentSpin {
      to { transform: rotate(360deg); }
    }
    @media (prefers-reduced-motion: reduce) {
      .top-progress-bar, .spinner { animation: none !important; }
    }

    /* Main Wizard Shell (fits 1280x720 above the fold) */
    .wizard-shell {
      max-width: 560px;
      width: 100%;
      margin: 0 auto;
      padding: 18px 16px 22px;
    }

    /* Horizontal 4-Step Stepper */
    nav.stepper {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      margin: 0 auto 14px;
      padding: 2px 4px;
      position: relative;
    }
    .step-item {
      display: flex;
      flex-direction: column;
      align-items: center;
      flex: 1;
      position: relative;
      background: transparent;
      border: 0;
      padding: 0 2px;
      cursor: default;
      font-family: var(--font);
      text-align: center;
    }
    .step-item.clickable { cursor: pointer; }
    .step-item:not(:last-child)::after {
      content: '';
      position: absolute;
      top: 12px;
      left: calc(50% + 14px);
      right: calc(-50% + 14px);
      height: 2px;
      background: var(--border);
      z-index: 0;
    }
    .step-item.done:not(:last-child)::after {
      background: var(--success-icon);
    }
    .step-node {
      width: 24px;
      height: 24px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 12px;
      font-weight: 600;
      position: relative;
      z-index: 1;
      background: var(--surface);
      border: 1.5px solid var(--border-input);
      color: var(--text-muted);
      transition: all 0.15s;
    }
    .step-item.done .step-node {
      background: var(--success-icon);
      border-color: var(--success-icon);
      color: #ffffff;
    }
    .step-item.active .step-node {
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
      box-shadow: 0 0 0 3px var(--accent-halo);
    }
    .step-item.error .step-node {
      background: var(--danger-btn);
      border-color: var(--danger-btn);
      color: #ffffff;
    }
    .step-label {
      margin-top: 6px;
      font-size: 12px;
      font-weight: 500;
      color: var(--text-muted);
      line-height: 1.2;
    }
    .step-item.active .step-label {
      color: var(--accent);
      font-weight: 600;
    }
    .step-item.done .step-label {
      color: var(--text-primary);
    }
    .step-item.error .step-label {
      color: var(--danger-text);
      font-weight: 600;
    }

    /* Centered Fluent Wizard Card */
    .wizard-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px 28px;
      box-shadow: 0 2px 8px rgba(17, 24, 39, 0.06), 0 1px 2px rgba(17, 24, 39, 0.04);
    }
    .card-header {
      margin-bottom: 16px;
    }
    .card-header-row {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    h1.step-title {
      margin: 0;
      font-size: 22px;
      font-weight: 600;
      line-height: 1.3;
      color: var(--text-primary);
      letter-spacing: -0.01em;
    }
    p.step-subtitle {
      margin: 6px 0 0;
      font-size: 14px;
      color: var(--text-secondary);
      line-height: 1.45;
    }

    /* State Alert Banners (All 9 states) */
    .callout {
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 14px;
      border: 1px solid var(--border);
      display: flex;
      align-items: flex-start;
      gap: 10px;
      font-size: 13.5px;
    }
    .callout-icon {
      flex-shrink: 0;
      margin-top: 1px;
      width: 18px;
      height: 18px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    .callout-body { flex: 1; min-width: 0; }
    .callout-title { font-weight: 600; margin: 0 0 2px; }
    .callout-desc { margin: 0; font-size: 13px; }
    .callout-actions { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; }

    .callout.info {
      background: var(--accent-soft);
      border-color: var(--accent-soft-border);
      color: var(--accent-hover);
    }
    .callout.success {
      background: var(--success-bg);
      border-color: var(--success-border);
      color: var(--success-text);
    }
    .callout.warn {
      background: var(--warn-bg);
      border-color: var(--warn-border);
      color: var(--warn-text);
    }
    .callout.danger {
      background: var(--danger-bg);
      border-color: var(--danger-border);
      color: var(--danger-text);
    }
    .callout.critical {
      background: var(--danger-bg);
      border: 2px solid var(--danger-btn);
      color: var(--danger-text);
    }
    .callout.offline {
      background: var(--offline-bg);
      border-color: var(--offline-border);
      color: var(--offline-text);
    }

    /* Spinner */
    .spinner {
      width: 20px;
      height: 20px;
      border: 2.5px solid var(--accent-halo);
      border-top-color: var(--accent);
      border-radius: 999px;
      display: inline-block;
      animation: fluentSpin 0.8s linear infinite;
      flex-shrink: 0;
    }
    .spinner.lg { width: 28px; height: 28px; border-width: 3px; }
    .loading-panel {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 12px;
      padding: 40px 16px;
      text-align: center;
      color: var(--text-secondary);
    }

    /* Step 1 Check Rows */
    .check-list {
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface-subtle);
      overflow: hidden;
      margin-bottom: 16px;
    }
    .check-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 11px 14px;
      border-bottom: 1px solid var(--border);
      font-size: 14px;
    }
    .check-row:last-child { border-bottom: 0; }
    .check-row-left {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 500;
      color: var(--text-primary);
    }
    .check-badge {
      font-size: 12px;
      font-weight: 600;
      padding: 2px 9px;
      border-radius: 999px;
    }
    .check-badge.ok { background: var(--success-bg); color: var(--success-text); border: 1px solid var(--success-border); }
    .check-badge.fail { background: var(--danger-bg); color: var(--danger-text); border: 1px solid var(--danger-border); }
    .check-badge.wait { background: var(--accent-soft); color: var(--accent-hover); border: 1px solid var(--accent-soft-border); }

    /* Form Controls */
    .field-group { margin-bottom: 12px; }
    label.field-label {
      display: block;
      font-size: 13px;
      font-weight: 600;
      color: var(--text-primary);
      margin-bottom: 4px;
    }
    .field-hint {
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 3px;
    }
    .input-wrap {
      position: relative;
      display: flex;
      align-items: center;
    }
    input[type="text"], input[type="password"], select {
      width: 100%;
      height: 38px;
      padding: 0 12px;
      border: 1px solid var(--border-input);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text-primary);
      font-family: var(--font);
      font-size: 14px;
      transition: border-color 0.12s, box-shadow 0.12s;
    }
    input.mono-input {
      font-family: var(--mono);
      font-size: 13px;
    }
    input:hover, select:hover { border-color: var(--border-input-hover); }
    input:focus, select:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 2px var(--accent-halo);
    }
    input:disabled, select:disabled {
      background: var(--surface-subtle);
      color: var(--text-muted);
      cursor: not-allowed;
    }
    .input-eye-btn {
      position: absolute;
      right: 6px;
      height: 28px;
      padding: 0 8px;
      border: 0;
      border-radius: 4px;
      background: transparent;
      color: var(--text-secondary);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
    }
    .input-eye-btn:hover { background: var(--canvas); color: var(--text-primary); }
    input.has-eye { padding-right: 68px; }

    /* Clean 2-Choice Mode Selector */
    .mode-choices {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 12px;
    }
    .mode-card {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 9px 10px;
      border: 1px solid var(--border-input);
      border-radius: 8px;
      background: var(--surface);
      cursor: pointer;
      transition: border-color 0.12s, background 0.12s;
    }
    .mode-card:hover { border-color: var(--border-input-hover); }
    .mode-card.selected {
      border-color: var(--accent);
      background: var(--accent-soft);
    }
    .mode-card input[type="radio"] {
      width: 15px;
      height: 15px;
      margin: 2px 0 0;
      accent-color: var(--accent);
      flex-shrink: 0;
    }
    .mode-card-title {
      font-size: 12.5px;
      font-weight: 600;
      color: var(--text-primary);
      line-height: 1.3;
    }
    .mode-card-desc {
      font-size: 11.5px;
      color: var(--text-secondary);
      line-height: 1.3;
      margin-top: 2px;
    }

    /* Collapsible Guide & Error Details */
    details.fluent-details {
      margin-bottom: 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface-subtle);
      padding: 8px 12px;
      font-size: 13px;
    }
    details.fluent-details summary {
      cursor: pointer;
      font-weight: 600;
      color: var(--accent);
      list-style: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    details.fluent-details summary::-webkit-details-marker { display: none; }
    details.fluent-details ol {
      margin: 8px 0 2px;
      padding-left: 18px;
      color: var(--text-secondary);
    }
    details.fluent-details li { margin-bottom: 4px; }
    .details-error-text {
      margin: 8px 0 0;
      font-family: var(--mono);
      font-size: 12px;
      color: var(--danger-text);
      white-space: pre-wrap;
      word-break: break-word;
    }

    /* Buttons */
    .btn-row {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 14px;
      flex-wrap: wrap;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 40px;
      padding: 0 16px;
      border-radius: 6px;
      font-family: var(--font);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      border: 1px solid var(--border-input);
      background: var(--surface);
      color: var(--text-primary);
      transition: background 0.12s, border-color 0.12s, color 0.12s;
    }
    .btn:hover:not(:disabled) { background: var(--surface-subtle); border-color: var(--border-input-hover); }
    .btn:disabled { opacity: 0.6; cursor: not-allowed; }
    .btn-primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }
    .btn-primary:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); color: #ffffff; }
    .btn-primary:active:not(:disabled) { background: var(--accent-active); border-color: var(--accent-active); }
    .btn-danger {
      background: var(--danger-btn);
      border-color: var(--danger-btn);
      color: #ffffff;
    }
    .btn-danger:hover:not(:disabled) { background: var(--danger-btn-hover); border-color: var(--danger-btn-hover); }
    .btn-danger-outline {
      background: var(--surface);
      border-color: var(--danger-border);
      color: var(--danger-text);
    }
    .btn-danger-outline:hover:not(:disabled) { background: var(--danger-bg); border-color: var(--danger-btn); }
    .btn-link {
      border: 0;
      background: transparent;
      color: var(--accent);
      padding: 4px 6px;
      min-height: 32px;
      font-size: 13px;
      font-weight: 600;
    }
    .btn-link:hover:not(:disabled) { background: var(--accent-soft); }
    .btn-sm { min-height: 32px; padding: 0 11px; font-size: 12.5px; }
    .btn-full { width: 100%; }

    /* Pills & Badges */
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid var(--border);
      background: var(--surface-subtle);
      color: var(--text-primary);
    }
    .pill.green { background: var(--success-bg); border-color: var(--success-border); color: var(--success-text); }
    .pill.blue { background: var(--accent-soft); border-color: var(--accent-soft-border); color: var(--accent-hover); }
    .pill.amber { background: var(--warn-bg); border-color: var(--warn-border); color: var(--warn-text); }
    .pill.red { background: var(--danger-bg); border-color: var(--danger-border); color: var(--danger-text); }
    .pill.neutral { background: var(--offline-bg); border-color: var(--offline-border); color: var(--offline-text); }
    .dot {
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: currentColor;
      display: inline-block;
    }

    /* Step 2B Prompt Box & Step 4 Examples */
    .subtle-box {
      background: var(--surface-subtle);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 12px;
    }
    .prompt-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface);
      margin-top: 6px;
    }
    .prompt-text {
      font-size: 13px;
      color: var(--text-primary);
      flex: 1;
      min-width: 0;
    }

    /* Step 3 Consent Summary */
    .consent-card {
      background: var(--surface-subtle);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
      margin-bottom: 12px;
    }
    .consent-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 10px;
    }
    .code-badge {
      font-family: var(--mono);
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0.05em;
      background: var(--accent-soft);
      color: var(--accent-hover);
      border: 1px solid var(--accent-soft-border);
      border-radius: 6px;
      padding: 4px 10px;
    }
    .consent-list {
      margin: 8px 0;
      padding-left: 18px;
      color: var(--text-secondary);
      font-size: 13px;
    }
    .consent-list li { margin-bottom: 4px; }
    .chip-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 6px;
    }
    .chip {
      font-size: 12px;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--text-secondary);
    }

    /* Step 4 Truth Strip & Safety Footer */
    .truth-strip {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      background: var(--surface-subtle);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 14px;
      margin-bottom: 14px;
    }
    .truth-item {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: var(--text-secondary);
    }
    .examples-heading {
      font-size: 12.5px;
      font-weight: 600;
      color: var(--text-secondary);
      margin: 14px 0 6px;
    }
    .safety-footer {
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }

    /* Phase D Slide-Over Advanced Settings Drawer */
    .drawer-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(17, 24, 39, 0.42);
      z-index: 60;
      display: flex;
      justify-content: flex-end;
    }
    .drawer-panel {
      width: 480px;
      max-width: 94vw;
      height: 100vh;
      background: var(--surface);
      border-left: 1px solid var(--border);
      box-shadow: -4px 0 24px rgba(17, 24, 39, 0.14);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .drawer-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }
    .drawer-title {
      margin: 0;
      font-size: 16px;
      font-weight: 600;
      color: var(--text-primary);
    }
    .drawer-body {
      padding: 16px 20px 24px;
      overflow-y: auto;
      flex: 1;
    }
    .adv-section {
      margin-bottom: 18px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    .adv-section:last-child { border-bottom: 0; margin-bottom: 0; }
    .adv-section-title {
      margin: 0 0 10px;
      font-size: 13px;
      font-weight: 600;
      color: var(--text-primary);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    table.adv-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12.5px;
    }
    table.adv-table th, table.adv-table td {
      text-align: left;
      padding: 7px 8px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }
    table.adv-table th {
      color: var(--text-muted);
      font-weight: 600;
      font-size: 11.5px;
    }
    .adv-logs {
      background: var(--surface-subtle);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px;
      font-family: var(--mono);
      font-size: 11.5px;
      line-height: 1.45;
      color: var(--text-secondary);
      max-height: 180px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .adv-action-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
    }
    .danger-zone {
      margin-top: 12px;
      padding: 12px;
      border: 1px solid var(--danger-border);
      border-radius: 8px;
      background: var(--danger-bg);
    }

    /* Fallback Login Overlay (Fluent Light) */
    #login-overlay {
      position: fixed;
      inset: 0;
      background: rgba(17, 24, 39, 0.45);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 100;
      padding: 16px;
    }
    .login-box {
      width: 420px;
      max-width: 100%;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px 26px;
      box-shadow: 0 12px 32px rgba(17, 24, 39, 0.16);
    }
    .login-box h2 {
      margin: 0 0 8px;
      font-size: 18px;
      font-weight: 600;
      color: var(--text-primary);
    }

    @media (max-width: 560px) {
      .wizard-card { padding: 20px 18px; }
      .mode-choices { grid-template-columns: 1fr; }
      header[role="banner"] { padding: 0 14px; }
    }
  </style>
</head>
<body>
  <!-- Fallback Local Owner Sign-In Overlay (Hidden by default; Open-SetupCenter.cmd handles automatic handoff) -->
  <div id="login-overlay" class="hidden" role="dialog" aria-modal="true" aria-labelledby="login-title">
    <div class="login-box">
      <h2 id="login-title">WinOwner Setup Center — Đăng nhập chủ máy</h2>
      <p id="login-desc" style="font-size:13px; color:var(--text-secondary); margin:0 0 14px;">
        Mở bằng <strong>Open-SetupCenter.cmd</strong> để tự động đăng nhập mà không cần nhập khóa thủ công.
      </p>
      <div class="field-group">
        <label class="field-label" for="owner-password" id="login-label">Khóa quản trị cục bộ</label>
        <input type="password" id="owner-password" placeholder="Nhập khóa quản trị" autocomplete="current-password" />
      </div>
      <div id="login-error" role="alert" style="color: var(--danger-text); font-size: 12.5px; margin-bottom: 10px;"></div>
      <button type="button" class="btn btn-primary btn-full" id="login-btn">Đăng nhập</button>
    </div>
  </div>

  <!-- Top Bar (56px, low visual weight) -->
  <header role="banner" aria-label="WinOwner Setup Center">
    <div class="brand-group">
      <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <rect x="0" y="0" width="24" height="24" rx="6" fill="#0067c0"></rect>
        <path d="M12 4.5L6.5 7V11.6C6.5 15.1 8.85 18.25 12 19.3C15.15 18.25 17.5 15.1 17.5 11.6V7L12 4.5Z" fill="none" stroke="#ffffff" stroke-width="1.6" stroke-linejoin="round"></path>
        <circle cx="12" cy="11.5" r="2.1" fill="#ffffff"></circle>
      </svg>
      <span class="brand-name" id="brand-wordmark">WinOwner MCP</span>
      <span class="brand-ver" id="brand-ver-tag">v1.2</span>
      <span class="sr-only" id="hdr-title">Trung tâm Thiết lập WinOwner</span>
    </div>
    <div class="topbar-controls">
      <button type="button" class="lang-toggle" id="lang-toggle-btn" aria-label="Switch language VI or EN">
        <span class="lang-seg active" id="lang-seg-vi">VI</span>
        <span class="lang-seg" id="lang-seg-en">EN</span>
      </button>
      <button type="button" class="btn-ghost-top" id="btn-advanced-settings" aria-haspopup="dialog" aria-controls="advanced-drawer">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="12" cy="12" r="3"></circle>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.6.85 1 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
        </svg>
        <span id="lbl-advanced-btn">Cài đặt nâng cao</span>
      </button>
    </div>
  </header>

  <div class="top-progress-wrap hidden" id="top-progress-wrap" aria-hidden="true">
    <div class="top-progress-bar"></div>
  </div>

  <!-- Centered 4-Step Wizard -->
  <div class="wizard-shell">
    <nav class="stepper" id="stepper-container" aria-label="Onboarding steps"></nav>

    <main class="wizard-card" id="wizard-main" data-ui-state="loading">
      <!-- Dynamic Lifecycle Banner (emergency_stopped / revoked / expired / offline / reconnecting) -->
      <div id="state-alert-banner" class="hidden"></div>

      <!-- Initial Loading View -->
      <section id="view-loading" class="loading-panel" aria-live="polite">
        <span class="spinner lg" aria-hidden="true"></span>
        <div id="loading-text" style="font-weight:500;">Đang kiểm tra trạng thái máy tính...</div>
      </section>

      <!-- STEP 1: CÀI ĐẶT / SETUP -->
      <section id="step-view-1" class="hidden">
        <div class="card-header">
          <h1 class="step-title" id="s1-title">Chuẩn bị máy tính của bạn</h1>
          <p class="step-subtitle" id="s1-subtitle">WinOwner đang kiểm tra nhanh các thành phần cần thiết trên máy.</p>
        </div>

        <div id="s1-alert" class="hidden" role="alert"></div>

        <div class="check-list" aria-live="polite">
          <div class="check-row">
            <span class="check-row-left" id="s1-chk1-lbl">Thành phần chạy cục bộ</span>
            <span class="check-badge wait" id="s1-chk1-val">Đang kiểm tra</span>
          </div>
          <div class="check-row">
            <span class="check-row-left" id="s1-chk2-lbl">Dịch vụ nền WinOwner</span>
            <span class="check-badge wait" id="s1-chk2-val">Đang kiểm tra</span>
          </div>
          <div class="check-row">
            <span class="check-row-left" id="s1-chk3-lbl">Cấu hình bảo mật cục bộ</span>
            <span class="check-badge wait" id="s1-chk3-val">Đang kiểm tra</span>
          </div>
        </div>

        <div id="s1-ready-banner" class="callout success hidden" aria-live="polite">
          <span class="callout-icon" aria-hidden="true">✓</span>
          <div class="callout-body">
            <div class="callout-title" id="s1-ready-text">Máy tính đã sẵn sàng</div>
          </div>
        </div>

        <details class="fluent-details hidden" id="s1-error-details">
          <summary id="s1-error-summary">Xem chi tiết lỗi</summary>
          <div class="details-error-text" id="s1-error-detail-text"></div>
        </details>

        <div class="btn-row">
          <button type="button" class="btn btn-primary" id="btn-s1-primary">Tiếp tục</button>
        </div>
      </section>

      <!-- STEP 2: KẾT NỐI CHATGPT / CONNECT -->
      <section id="step-view-2" class="hidden">
        <div class="card-header">
          <h1 class="step-title" id="s2-title">Kết nối máy tính với ChatGPT</h1>
          <p class="step-subtitle" id="s2-subtitle">Nhập thông tin đường truyền riêng để máy tính kết nối an toàn với ChatGPT.</p>
        </div>

        <!-- Sub-phase 2A: Enter Credentials -->
        <div id="s2-form-pane">
          <div id="s2-connecting-banner" class="callout info hidden" aria-live="polite">
            <span class="spinner" aria-hidden="true"></span>
            <div class="callout-body">
              <div class="callout-title" id="s2-connecting-text">Kết nối đang được thiết lập...</div>
            </div>
          </div>

          <div id="s2-error-alert" class="callout danger hidden" role="alert">
            <span class="callout-icon" aria-hidden="true">✕</span>
            <div class="callout-body">
              <div class="callout-title" id="s2-error-title">Chưa thể kết nối. Vui lòng thử lại.</div>
              <p class="callout-desc" id="s2-error-hint"></p>
            </div>
          </div>

          <details class="fluent-details hidden" id="s2-error-details">
            <summary id="s2-error-summary">Xem chi tiết lỗi</summary>
            <div class="details-error-text" id="s2-error-detail-text"></div>
          </details>

          <form id="connect-form" novalidate>
            <div class="field-group">
              <label class="field-label" for="input-tunnel-id" id="lbl-tunnel-id">Tunnel ID</label>
              <input type="text" id="input-tunnel-id" class="mono-input" placeholder="tunnel_..." autocomplete="off" spellcheck="false" />
              <div class="field-hint" id="hint-tunnel-id">Dán mã bắt đầu bằng tunnel_ từ trang OpenAI Tunnels.</div>
            </div>

            <div class="field-group">
              <label class="field-label" for="input-runtime-key" id="lbl-api-key">API Key</label>
              <div class="input-wrap">
                <input type="password" id="input-runtime-key" class="has-eye" placeholder="Nhập API Key của bạn" autocomplete="off" />
                <button type="button" class="input-eye-btn" id="btn-toggle-key-vis">Hiện</button>
              </div>
              <div class="field-hint" id="hint-api-key">Khóa chỉ lưu bảo mật trên máy của bạn và không bao giờ hiển thị lại.</div>
            </div>

            <div class="field-group">
              <span class="field-label" id="lbl-access-mode">Mức truy cập ban đầu</span>
              <div class="mode-choices" role="radiogroup" aria-labelledby="lbl-access-mode">
                <label class="mode-card selected" id="mode-card-readonly">
                  <input type="radio" name="connect_auth_mode" id="mode-radio-auth-none" value="auth_none" checked />
                  <div>
                    <div class="mode-card-title" id="mode-readonly-title">Chỉ xem (mặc định)</div>
                    <div class="mode-card-desc" id="mode-readonly-desc">Chỉ đọc thông tin an toàn, không thay đổi tệp.</div>
                  </div>
                </label>
                <label class="mode-card" id="mode-card-confirm">
                  <input type="radio" name="connect_auth_mode" id="mode-radio-oauth" value="oauth" />
                  <div>
                    <div class="mode-card-title" id="mode-confirm-title">Cho phép thao tác khi tôi xác nhận</div>
                    <div class="mode-card-desc" id="mode-confirm-desc">Có thể cấp quyền làm việc sau khi bạn xác nhận ở bước kế tiếp.</div>
                  </div>
                </label>
              </div>
            </div>

            <details class="fluent-details" id="s2-guide-details">
              <summary id="s2-guide-summary">Xem hướng dẫn lấy Tunnel ID và API Key</summary>
              <ol id="s2-guide-list">
                <li id="s2-guide-1">Mở trang OpenAI API Keys để tạo một khóa riêng cho máy tính này.</li>
                <li id="s2-guide-2">Mở trang OpenAI Tunnels, tạo Tunnel mới và sao chép mã Tunnel ID.</li>
                <li id="s2-guide-3">Dán cả hai vào biểu mẫu trên rồi bấm Kết nối và tiếp tục.</li>
              </ol>
              <div style="display:flex; gap:12px; margin-top:6px; font-size:12.5px;">
                <a href="https://platform.openai.com/settings/organization/api-keys" target="_blank" rel="noopener noreferrer" style="color:var(--accent);" id="lnk-openai-keys">Mở OpenAI API Keys ↗</a>
                <a href="https://platform.openai.com/settings/organization/tunnels" target="_blank" rel="noopener noreferrer" style="color:var(--accent);" id="lnk-openai-tunnels">Mở OpenAI Tunnels ↗</a>
              </div>
            </details>

            <div class="btn-row">
              <button type="submit" class="btn btn-primary" id="btn-submit-connect">Kết nối và tiếp tục</button>
              <button type="button" class="btn btn-link hidden" id="btn-cancel-change-tunnel">Quay lại kiểm tra</button>
            </div>
          </form>
        </div>

        <!-- Sub-phase 2B: Verify Real ChatGPT Tool Call -->
        <div id="s2-verify-pane" class="hidden">
          <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; gap:8px; flex-wrap:wrap;">
            <span class="pill green"><span class="dot"></span><span id="s2b-badge">Đường truyền đã kết nối</span></span>
            <button type="button" class="btn btn-link btn-sm" id="btn-change-tunnel">Đổi thông tin kết nối</button>
          </div>

          <div class="subtle-box">
            <div style="font-size:13.5px; font-weight:600; margin-bottom:6px;" id="s2b-step1-title">1. Thêm ứng dụng WinOwner trong ChatGPT</div>
            <div style="font-size:13px; color:var(--text-secondary); margin-bottom:10px;" id="s2b-step1-desc">
              Mở mục Plugins trong ChatGPT và thêm ứng dụng WinOwner bằng Tunnel ID bạn vừa kết nối.
            </div>
            <a href="https://chatgpt.com/plugins" target="_blank" rel="noopener noreferrer" class="btn btn-sm" id="btn-open-chatgpt-plugins">Mở ChatGPT ↗</a>

            <div style="font-size:13.5px; font-weight:600; margin:12px 0 4px;" id="s2b-step2-title">2. Gửi câu lệnh thử này trong ChatGPT để xác minh</div>
            <div class="prompt-row">
              <span class="prompt-text" id="s2b-test-prompt">Kiểm tra cấu hình máy tính và dung lượng ổ đĩa của tôi</span>
              <button type="button" class="btn btn-sm" id="btn-copy-verify-prompt">Sao chép</button>
            </div>
          </div>

          <div class="callout info" id="s2b-wait-box" aria-live="polite">
            <span class="spinner" aria-hidden="true"></span>
            <div class="callout-body">
              <div class="callout-title" id="s2b-wait-text">Đang chờ phản hồi đầu tiên từ ChatGPT...</div>
            </div>
          </div>

          <div class="btn-row">
            <button type="button" class="btn btn-primary" id="btn-verify-conn">Kiểm tra kết nối</button>
          </div>
        </div>
      </section>

      <!-- STEP 3: XÁC NHẬN QUYỀN / AUTHORIZE -->
      <section id="step-view-3" class="hidden">
        <div class="card-header">
          <h1 class="step-title" id="s3-title">Cho phép ChatGPT sử dụng máy tính</h1>
          <p class="step-subtitle" id="s3-subtitle">Kiểm tra kỹ phạm vi trước khi xác nhận. Bạn luôn có thể thu hồi bất cứ lúc nào.</p>
        </div>

        <div id="s3-error-alert" class="callout danger hidden" role="alert">
          <span class="callout-icon" aria-hidden="true">✕</span>
          <div class="callout-body">
            <div class="callout-title" id="s3-error-text"></div>
          </div>
        </div>

        <!-- Sub-state 3A: Pending Authorization Request -->
        <div id="s3-pending-pane" class="hidden">
          <div id="challenges-container"></div>
        </div>

        <!-- Sub-state 3B: Waiting or Read-Only Mode -->
        <div id="s3-empty-pane" class="hidden">
          <div class="callout info" id="s3-info-callout" aria-live="polite">
            <span class="callout-icon" id="s3-info-icon" aria-hidden="true">ℹ</span>
            <div class="callout-body">
              <div class="callout-title" id="s3-info-title">Đang chờ yêu cầu xác nhận từ ChatGPT...</div>
              <p class="callout-desc" id="s3-info-desc"></p>
            </div>
          </div>
          <div class="btn-row">
            <button type="button" class="btn btn-primary" id="btn-s3-primary">Kiểm tra yêu cầu</button>
            <button type="button" class="btn btn-link" id="btn-s3-secondary">Dùng chế độ Chỉ xem</button>
          </div>
        </div>
      </section>

      <!-- STEP 4: SẴN SÀNG SỬ DỤNG / READY -->
      <section id="step-view-4" class="hidden">
        <div class="card-header">
          <div class="card-header-row">
            <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" id="s4-header-icon">
              <circle cx="12" cy="12" r="10" fill="#15803d"></circle>
              <path d="M8 12.2l2.6 2.6L16.4 9" fill="none" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
            <h1 class="step-title" id="s4-title">Máy tính của bạn đã kết nối!</h1>
          </div>
          <p class="step-subtitle" id="s4-subtitle">Bạn có thể mở ChatGPT và bắt đầu sử dụng ngay bây giờ.</p>
        </div>

        <div class="truth-strip">
          <div class="truth-item">
            <span id="s4-lbl-conn">Trạng thái:</span>
            <span class="pill green" id="s4-pill-conn"><span class="dot"></span><span id="s4-val-conn">Đã kết nối</span></span>
          </div>
          <div class="truth-item">
            <span id="s4-lbl-perm">Quyền hiện tại:</span>
            <span class="pill blue" id="s4-pill-perm">Chỉ xem (An toàn)</span>
          </div>
        </div>

        <div id="s4-test-feedback" class="callout hidden" aria-live="polite"></div>

        <div class="btn-row" style="margin-top:0;">
          <a href="https://chatgpt.com" target="_blank" rel="noopener noreferrer" class="btn btn-primary" id="btn-open-chatgpt">Mở ChatGPT</a>
          <button type="button" class="btn" id="btn-test-conn">Thử kết nối</button>
        </div>

        <div class="examples-heading" id="s4-examples-title">Gợi ý câu lệnh an toàn để thử ngay:</div>
        <div id="s4-examples-list">
          <div class="prompt-row">
            <span class="prompt-text" id="ex-prompt-1">Kiểm tra cấu hình hệ thống (CPU, RAM)</span>
            <button type="button" class="btn btn-sm" data-copy-target="ex-prompt-1">Sao chép</button>
          </div>
          <div class="prompt-row">
            <span class="prompt-text" id="ex-prompt-2">Xem dung lượng trống của các ổ đĩa</span>
            <button type="button" class="btn btn-sm" data-copy-target="ex-prompt-2">Sao chép</button>
          </div>
          <div class="prompt-row">
            <span class="prompt-text" id="ex-prompt-3">Đọc một tệp trong thư mục đã cấp quyền</span>
            <button type="button" class="btn btn-sm" data-copy-target="ex-prompt-3">Sao chép</button>
          </div>
        </div>

        <div class="safety-footer">
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
            <button type="button" class="btn btn-sm" id="btn-revoke-inline">Quản lý / Thu hồi quyền</button>
            <span id="revoke-confirm-wrap" class="hidden" style="display:inline-flex; align-items:center; gap:6px;">
              <button type="button" class="btn btn-danger btn-sm" id="btn-revoke-confirm">Xác nhận thu hồi</button>
              <button type="button" class="btn btn-sm" id="btn-revoke-cancel">Hủy</button>
            </span>
          </div>
          <button type="button" class="btn btn-danger-outline btn-sm" id="btn-estop-footer">Emergency Stop</button>
        </div>
      </section>
    </main>
  </div>

  <!-- PHASE D: Slide-Over Advanced Settings Drawer (Hidden during normal onboarding) -->
  <div class="drawer-backdrop hidden" id="advanced-drawer" role="dialog" aria-modal="true" aria-labelledby="adv-drawer-title">
    <div class="drawer-panel">
      <div class="drawer-header">
        <h2 class="drawer-title" id="adv-drawer-title">Cài đặt nâng cao</h2>
        <button type="button" class="btn btn-sm" id="btn-close-advanced" aria-label="Close">✕</button>
      </div>
      <div class="drawer-body">
        <div id="adv-feedback" class="callout hidden" aria-live="polite"></div>

        <!-- Section 1: Diagnostics & 6-Row Live Status Table -->
        <div class="adv-section">
          <h3 class="adv-section-title">
            <span id="adv-sec-diag">Chẩn đoán &amp; Trạng thái dịch vụ</span>
            <button type="button" class="btn btn-sm" id="btn-adv-refresh">Làm mới</button>
          </h3>
          <table class="adv-table" aria-label="WinOwner Setup Center Status Table">
            <thead>
              <tr>
                <th id="th-component">Thành phần</th>
                <th id="th-status">Trạng thái</th>
                <th id="th-detail">Chi tiết</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td id="lbl-local-service">Local Service</td>
                <td><span id="val-local-service" class="pill">...</span></td>
                <td id="det-local-service" style="color:var(--text-muted);">...</td>
              </tr>
              <tr>
                <td id="lbl-tunnel">Tunnel</td>
                <td><span id="val-tunnel" class="pill">...</span></td>
                <td id="det-tunnel" style="color:var(--text-muted);">...</td>
              </tr>
              <tr>
                <td id="lbl-chatgpt">ChatGPT</td>
                <td><span id="val-chatgpt" class="pill">...</span></td>
                <td id="det-chatgpt" style="color:var(--text-muted);">...</td>
              </tr>
              <tr>
                <td id="lbl-owner">Owner Identity</td>
                <td><span id="val-owner" class="pill">...</span></td>
                <td id="det-owner" style="color:var(--text-muted);">...</td>
              </tr>
              <tr>
                <td id="lbl-permissions">Permissions</td>
                <td><span id="val-permissions" class="pill">...</span></td>
                <td id="det-permissions" style="color:var(--text-muted);">...</td>
              </tr>
              <tr>
                <td id="lbl-version">Version</td>
                <td><span id="val-version" class="pill">...</span></td>
                <td id="det-version" style="color:var(--text-muted);">v1.2.0</td>
              </tr>
            </tbody>
          </table>
          <div id="adv-guidance-box" style="margin-top:8px; font-size:12px; color:var(--text-secondary);"></div>
          <div style="margin-top:8px;">
            <a href="/console" style="color:var(--accent); font-size:12.5px; font-weight:600; text-decoration:none;" id="adv-console-link">Mở bảng điều khiển Mode B (/console) ↗</a>
          </div>
        </div>

        <!-- Section 2: Redacted Logs -->
        <div class="adv-section">
          <h3 class="adv-section-title">
            <span id="adv-sec-logs">Nhật ký hệ thống (đã ẩn bí mật)</span>
            <span style="display:flex; gap:6px;">
              <button type="button" class="btn btn-sm" id="btn-adv-load-logs">Tải nhật ký</button>
              <button type="button" class="btn btn-sm" id="btn-adv-copy-logs">Sao chép</button>
            </span>
          </h3>
          <div class="adv-logs" id="adv-logs-box">Chưa tải nhật ký.</div>
        </div>

        <!-- Section 3: Repair, Updates & Rollback -->
        <div class="adv-section">
          <h3 class="adv-section-title" id="adv-sec-maint">Bảo trì, Cập nhật &amp; Khôi phục</h3>
          <div id="adv-version-meta" style="font-size:12.5px; color:var(--text-secondary); margin-bottom:8px;"></div>
          <div class="adv-action-row">
            <button type="button" class="btn btn-sm" id="btn-adv-repair">Sửa chữa dịch vụ</button>
            <button type="button" class="btn btn-sm" id="btn-adv-updates">Kiểm tra cập nhật</button>
            <button type="button" class="btn btn-sm" id="btn-adv-rollback">Khôi phục bản trước (Rollback)</button>
          </div>
        </div>

        <!-- Section 4: Reconfigure Tunnel, Session & Uninstall -->
        <div class="adv-section">
          <h3 class="adv-section-title" id="adv-sec-reconfig">Cấu hình lại Tunnel</h3>
          <div id="reconfig-error-alert" class="callout danger hidden" role="alert">
            <span class="callout-icon" aria-hidden="true">✕</span>
            <div class="callout-body" id="reconfig-error-text"></div>
          </div>
          <div class="field-group">
            <label class="field-label" for="reconfig-tunnel-id" id="lbl-reconfig-tid">Tunnel ID mới</label>
            <input type="text" id="reconfig-tunnel-id" class="mono-input" placeholder="tunnel_..." autocomplete="off" />
          </div>
          <div class="field-group">
            <label class="field-label" for="reconfig-runtime-key" id="lbl-reconfig-key">API Key mới</label>
            <input type="password" id="reconfig-runtime-key" placeholder="Nhập API Key" autocomplete="off" />
          </div>
          <div class="field-group">
            <label class="field-label" for="reconfig-auth-mode" id="lbl-reconfig-mode">Chế độ kết nối</label>
            <select id="reconfig-auth-mode">
              <option value="auth_none" selected>Chỉ xem (Authentication: None — Mặc định Tunnel cục bộ)</option>
              <option value="oauth">Cho phép thao tác khi tôi xác nhận (OAuth 2.1)</option>
            </select>
          </div>
          <div class="adv-action-row">
            <button type="button" class="btn btn-primary btn-sm" id="btn-submit-reconfig">Cấu hình lại Tunnel</button>
          </div>

          <div class="adv-action-row" style="margin-top:14px;">
            <button type="button" class="btn btn-sm" id="btn-adv-revoke">Thu hồi toàn bộ quyền</button>
            <button type="button" class="btn btn-danger-outline btn-sm" id="btn-adv-estop">Emergency Stop</button>
            <button type="button" class="btn btn-sm" id="btn-adv-logout">Đăng xuất</button>
          </div>

          <div class="danger-zone">
            <div style="font-size:13px; font-weight:600; color:var(--danger-text); margin-bottom:4px;" id="adv-uninstall-title">Gỡ cài đặt dịch vụ cục bộ</div>
            <div style="font-size:12px; color:var(--text-secondary); margin-bottom:8px;" id="adv-uninstall-desc">Dừng và gỡ các dịch vụ nền đã xác minh. Dữ liệu cá nhân và khóa cục bộ vẫn được giữ nguyên.</div>
            <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
              <button type="button" class="btn btn-danger-outline btn-sm" id="btn-adv-uninstall">Gỡ cài đặt</button>
              <span id="uninstall-confirm-wrap" class="hidden" style="display:inline-flex; gap:6px;">
                <button type="button" class="btn btn-danger btn-sm" id="btn-adv-uninstall-confirm">Xác nhận gỡ cài đặt</button>
                <button type="button" class="btn btn-sm" id="btn-adv-uninstall-cancel">Hủy</button>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    let csrfToken = null;
    let currentLang = 'vi';
    let latestState = null;
    let uiOverrideState = null; // 'loading' | 'connecting' | 'connection_failed' | 'reconnecting' | 'offline' | null
    let manualStepView = null;  // 1 | 2 | 3 | 4 | null
    let forceStep2Form = false;
    let step1AutoAdvanced = false;
    let connectErrorDetail = '';
    let step1ErrorDetail = '';
    let pollTimer = null;
    let reconnectFailures = 0;

    const I18N = {
      vi: {
        hdrTitle: 'Trung tâm Thiết lập WinOwner',
        advBtn: 'Cài đặt nâng cao',
        steps: ['Cài đặt', 'Kết nối', 'Xác nhận quyền', 'Sẵn sàng'],
        loadingText: 'Đang kiểm tra trạng thái máy tính...',
        s1Title: 'Chuẩn bị máy tính của bạn',
        s1Subtitle: 'WinOwner đang kiểm tra nhanh các thành phần cần thiết trên máy.',
        s1Chk1: 'Thành phần chạy cục bộ',
        s1Chk2: 'Dịch vụ nền WinOwner',
        s1Chk3: 'Cấu hình bảo mật cục bộ',
        chkOk: 'Sẵn sàng',
        chkFail: 'Cần sửa chữa',
        chkWait: 'Đang kiểm tra',
        s1Ready: 'Máy tính đã sẵn sàng',
        s1FailTitle: 'Một số thành phần chưa sẵn sàng.',
        s1FailDesc: 'Nhấn Thử lại / Sửa chữa để WinOwner tự động khắc phục.',
        s1BtnContinue: 'Tiếp tục',
        s1BtnRepair: 'Thử lại / Sửa chữa',
        s1BtnRepairing: 'Đang sửa chữa...',
        viewErrDetails: 'Xem chi tiết lỗi',
        s2Title: 'Kết nối máy tính với ChatGPT',
        s2Subtitle: 'Nhập thông tin đường truyền riêng để máy tính kết nối an toàn với ChatGPT.',
        s2Connecting: 'Kết nối đang được thiết lập...',
        s2ErrorTitle: 'Chưa thể kết nối. Vui lòng thử lại.',
        s2ErrorHint: 'Kiểm tra lại Tunnel ID, API Key và kết nối mạng của bạn.',
        s2ErrorInvalidTid: 'Mã Tunnel ID chưa đúng định dạng (bắt đầu bằng tunnel_).',
        s2ErrorInvalidKey: 'Vui lòng nhập API Key hợp lệ.',
        lblTid: 'Tunnel ID',
        hintTid: 'Dán mã bắt đầu bằng tunnel_ từ trang OpenAI Tunnels.',
        lblKey: 'API Key',
        placeholderKey: 'Nhập API Key của bạn',
        hintKey: 'Khóa chỉ lưu bảo mật trên máy của bạn và không bao giờ hiển thị lại.',
        btnShow: 'Hiện',
        btnHide: 'Ẩn',
        lblAccessMode: 'Mức truy cập ban đầu',
        modeRoTitle: 'Chỉ xem (mặc định)',
        modeRoDesc: 'Chỉ đọc thông tin an toàn, không thay đổi tệp.',
        modeConfirmTitle: 'Cho phép thao tác khi tôi xác nhận',
        modeConfirmDesc: 'Cấp quyền làm việc sau khi bạn xác nhận rõ ràng ở bước tiếp theo.',
        s2GuideSummary: 'Xem hướng dẫn lấy Tunnel ID và API Key',
        s2Guide1: 'Mở trang OpenAI API Keys để tạo một khóa riêng cho máy tính này.',
        s2Guide2: 'Mở trang OpenAI Tunnels, tạo đường truyền mới và sao chép Tunnel ID.',
        s2Guide3: 'Dán cả hai vào biểu mẫu trên rồi bấm Kết nối và tiếp tục.',
        lnkKeys: 'Mở OpenAI API Keys ↗',
        lnkTunnels: 'Mở OpenAI Tunnels ↗',
        btnConnect: 'Kết nối và tiếp tục',
        btnConnecting: 'Đang kết nối...',
        btnRetry: 'Thử lại',
        btnCancelChange: 'Quay lại kiểm tra',
        s2bBadge: 'Đường truyền đã kết nối',
        btnChangeTunnel: 'Đổi thông tin kết nối',
        s2bStep1Title: '1. Thêm ứng dụng WinOwner trong ChatGPT',
        s2bStep1Desc: 'Mở ChatGPT và kết nối ứng dụng WinOwner bằng Tunnel ID bạn vừa thiết lập.',
        btnOpenPlugins: 'Mở ChatGPT ↗',
        s2bStep2Title: '2. Gửi câu lệnh thử này trong ChatGPT để xác minh',
        s2bTestPrompt: 'Kiểm tra cấu hình máy tính và dung lượng ổ đĩa của tôi',
        s2bWaitText: 'Đang chờ phản hồi đầu tiên từ ChatGPT...',
        btnVerifyConn: 'Kiểm tra kết nối',
        btnVerifying: 'Đang kiểm tra...',
        s3Title: 'Cho phép ChatGPT sử dụng máy tính',
        s3Subtitle: 'Kiểm tra kỹ phạm vi trước khi xác nhận. Bạn luôn có thể thu hồi bất cứ lúc nào.',
        confirmOwnerBtn: 'Xác nhận cấp quyền chủ máy',
        denyBtn: 'Từ chối',
        s3WaitTitle: 'Đang chờ yêu cầu xác nhận từ ChatGPT...',
        s3WaitDesc: 'Khi ChatGPT gửi yêu cầu thao tác trên máy tính, thông tin và mã xác nhận sẽ tự động hiện tại đây.',
        s3RoTitle: 'Đang kết nối ở chế độ Chỉ xem (An toàn)',
        s3RoDesc: 'ChatGPT hiện chỉ có thể đọc thông tin, không thể sửa tệp hoặc chạy lệnh trên máy tính của bạn.',
        s3BtnCheck: 'Kiểm tra yêu cầu',
        s3BtnGoReady: 'Tiếp tục với chế độ Chỉ xem',
        s3BtnUseRo: 'Dùng chế độ Chỉ xem',
        s3BtnSwitchControl: 'Đổi sang chế độ cho phép điều khiển',
        consentCodeLbl: 'Mã xác nhận:',
        consentAppFallback: 'Ứng dụng ChatGPT',
        consentScopeFolders: 'Thư mục được chọn',
        consentScopeFull: 'Toàn quyền chủ máy',
        consentOpRead: 'Đọc tệp và kiểm tra thông tin hệ thống trong phạm vi đã duyệt.',
        consentOpWrite: 'Tạo, chỉnh sửa tệp hoặc chạy lệnh trong thư mục được cấp phép.',
        consentOpSafe: 'Các thao tác phá hủy dữ liệu bị chặn mặc định.',
        consentOpDestructive: 'Bao gồm thao tác xóa hoặc thay đổi nhạy cảm theo yêu cầu.',
        consentExpiryTpl: 'Tự động hết hạn sau {dur}. Bạn có thể thu hồi hoặc dừng khẩn cấp bất cứ lúc nào.',
        s4Title: 'Máy tính của bạn đã kết nối!',
        s4Subtitle: 'Bạn có thể mở ChatGPT và bắt đầu sử dụng ngay bây giờ.',
        s4LblConn: 'Trạng thái:',
        s4ValConn: 'Đã kết nối',
        s4LblPerm: 'Quyền hiện tại:',
        permRead: 'Chỉ xem (An toàn)',
        permScoped: 'Điều khiển theo thư mục',
        permOwner: 'Toàn quyền chủ máy',
        btnOpenChatGPT: 'Mở ChatGPT',
        btnTestConn: 'Thử kết nối',
        s4TestOk: 'Kết nối hoạt động tốt và đã xác minh phản hồi từ ChatGPT.',
        s4TestWait: 'Đường truyền sẵn sàng, đang chờ một câu lệnh thử mới từ ChatGPT.',
        s4ExamplesTitle: 'Gợi ý câu lệnh an toàn để thử ngay:',
        ex1: 'Kiểm tra cấu hình hệ thống (CPU, RAM)',
        ex2: 'Xem dung lượng trống của các ổ đĩa',
        ex3: 'Đọc một tệp trong thư mục đã cấp quyền',
        btnCopy: 'Sao chép',
        btnCopied: 'Đã sao chép',
        btnRevokeInline: 'Quản lý / Thu hồi quyền',
        btnRevokeConfirm: 'Xác nhận thu hồi',
        btnCancel: 'Hủy',
        estopBtn: 'Dừng khẩn cấp (Emergency Stop)',
        estopResetBtn: 'Tắt Dừng khẩn cấp',
        stRevokedTitle: 'Quyền truy cập đã được thu hồi. Máy đang ở chế độ Chỉ xem.',
        stRevokedAction: 'Kết nối & cấp quyền lại',
        stWaitingAuthTitle: 'Đang chờ bạn xác nhận quyền truy cập',
        stWaitingAuthAction: 'Xem yêu cầu xác nhận',
        stExpiredTitle: 'Phiên cấp quyền đã hết hạn để đảm bảo an toàn.',
        stExpiredAction: 'Xác nhận lại quyền',
        stEstopTitle: 'Đã bật Dừng khẩn cấp — mọi thao tác điều khiển đã tạm ngưng.',
        stOfflineTitle: 'Máy tính đang ngoại tuyến hoặc dịch vụ cục bộ không phản hồi.',
        stOfflineRetry: 'Thử kết nối lại',
        stOfflineRepair: 'Sửa chữa tự động',
        stReconnectingTitle: 'Đang tự động kết nối lại...',
        advTitle: 'Cài đặt nâng cao',
        advSecDiag: 'Chẩn đoán & Trạng thái dịch vụ',
        advRefresh: 'Làm mới',
        thComp: 'Thành phần',
        thStatus: 'Trạng thái',
        thDetail: 'Chi tiết',
        advConsole: 'Mở bảng điều khiển Mode B (/console) ↗',
        advSecLogs: 'Nhật ký hệ thống (đã ẩn bí mật)',
        advLoadLogs: 'Tải nhật ký',
        advLogsEmpty: 'Chưa có mục nhật ký nào.',
        advSecMaint: 'Bảo trì, Cập nhật & Khôi phục',
        advRepair: 'Sửa chữa dịch vụ',
        advUpdates: 'Kiểm tra cập nhật',
        advRollback: 'Khôi phục bản trước (Rollback)',
        advRollbackNone: 'Chưa có bản sao lưu để khôi phục.',
        advSecReconfig: 'Cấu hình lại Tunnel',
        lblReconfigTid: 'Tunnel ID mới',
        lblReconfigKey: 'API Key mới',
        lblReconfigMode: 'Chế độ kết nối',
        btnReconfig: 'Cấu hình lại Tunnel',
        advRevoke: 'Thu hồi toàn bộ quyền',
        advLogout: 'Đăng xuất',
        advUninstallTitle: 'Gỡ cài đặt dịch vụ cục bộ',
        advUninstallDesc: 'Dừng và gỡ các dịch vụ nền đã xác minh. Dữ liệu cá nhân và khóa cục bộ vẫn được giữ nguyên.',
        advUninstallBtn: 'Gỡ cài đặt',
        advUninstallConfirm: 'Xác nhận gỡ cài đặt',
        loginTitle: 'Trung tâm Thiết lập WinOwner — Đăng nhập chủ máy',
        loginDesc: 'Mở bằng Open-SetupCenter.cmd để tự động đăng nhập mà không cần nhập khóa thủ công.',
        loginLabel: 'Khóa quản trị cục bộ',
        loginBtn: 'Đăng nhập',
        minutesUnit: 'phút',
        hoursUnit: 'giờ'
      },
      en: {
        hdrTitle: 'WinOwner Setup Center',
        advBtn: 'Advanced Settings',
        steps: ['Setup', 'Connect', 'Authorize', 'Ready'],
        loadingText: 'Checking computer status...',
        s1Title: 'Preparing your computer',
        s1Subtitle: 'WinOwner is checking the components required on your computer.',
        s1Chk1: 'Local runtime components',
        s1Chk2: 'WinOwner background service',
        s1Chk3: 'Local security configuration',
        chkOk: 'Ready',
        chkFail: 'Needs repair',
        chkWait: 'Checking',
        s1Ready: 'Your computer is ready',
        s1FailTitle: 'Some components are not ready yet.',
        s1FailDesc: 'Click Retry / Repair to fix local components automatically.',
        s1BtnContinue: 'Continue',
        s1BtnRepair: 'Retry / Repair',
        s1BtnRepairing: 'Repairing...',
        viewErrDetails: 'View error details',
        s2Title: 'Connect your computer to ChatGPT',
        s2Subtitle: 'Enter your private connection details so ChatGPT can reach this computer safely.',
        s2Connecting: 'Setting up connection...',
        s2ErrorTitle: 'Could not connect yet. Please try again.',
        s2ErrorHint: 'Check your Tunnel ID, API Key, and internet connection.',
        s2ErrorInvalidTid: 'Tunnel ID format looks invalid (it should start with tunnel_).',
        s2ErrorInvalidKey: 'Please enter a valid API Key.',
        lblTid: 'Tunnel ID',
        hintTid: 'Paste the ID starting with tunnel_ from OpenAI Tunnels.',
        lblKey: 'API Key',
        placeholderKey: 'Enter your API Key',
        hintKey: 'Stored securely on this computer only and never displayed again.',
        btnShow: 'Show',
        btnHide: 'Hide',
        lblAccessMode: 'Initial access mode',
        modeRoTitle: 'View-only (default)',
        modeRoDesc: 'Safely read information only; no file changes.',
        modeConfirmTitle: 'Allow actions when I confirm',
        modeConfirmDesc: 'Enables work actions only after you explicitly approve on the next step.',
        s2GuideSummary: 'How to get Tunnel ID & API Key',
        s2Guide1: 'Open OpenAI API Keys and create a key dedicated to this computer.',
        s2Guide2: 'Open OpenAI Tunnels, create a new tunnel, and copy its Tunnel ID.',
        s2Guide3: 'Paste both values above and click Connect and continue.',
        lnkKeys: 'Open OpenAI API Keys ↗',
        lnkTunnels: 'Open OpenAI Tunnels ↗',
        btnConnect: 'Connect and continue',
        btnConnecting: 'Connecting...',
        btnRetry: 'Try again',
        btnCancelChange: 'Back to verification',
        s2bBadge: 'Connection active',
        btnChangeTunnel: 'Change connection info',
        s2bStep1Title: '1. Add WinOwner in ChatGPT',
        s2bStep1Desc: 'Open ChatGPT and connect the WinOwner app using the Tunnel ID you just configured.',
        btnOpenPlugins: 'Open ChatGPT ↗',
        s2bStep2Title: '2. Send this quick test prompt in ChatGPT to verify',
        s2bTestPrompt: 'Check my computer specs and disk space',
        s2bWaitText: 'Waiting for first response from ChatGPT...',
        btnVerifyConn: 'Verify connection',
        btnVerifying: 'Verifying...',
        s3Title: 'Allow ChatGPT to use your computer',
        s3Subtitle: 'Review the requested scope before confirming. You can revoke access anytime.',
        confirmOwnerBtn: 'Confirm Owner Access',
        denyBtn: 'Deny',
        s3WaitTitle: 'Waiting for authorization request from ChatGPT...',
        s3WaitDesc: 'When ChatGPT requests access to act on your computer, the summary and verification code will appear here.',
        s3RoTitle: 'Connected in View-Only (Safe) mode',
        s3RoDesc: 'ChatGPT can safely read information, and cannot modify files or run commands on your computer.',
        s3BtnCheck: 'Check for requests',
        s3BtnGoReady: 'Continue in View-Only mode',
        s3BtnUseRo: 'Continue in View-Only mode',
        s3BtnSwitchControl: 'Switch to control-enabled mode',
        consentCodeLbl: 'Verification code:',
        consentAppFallback: 'ChatGPT App',
        consentScopeFolders: 'Approved folders',
        consentScopeFull: 'Full owner control',
        consentOpRead: 'Read files and inspect computer status within approved scope.',
        consentOpWrite: 'Create, edit files, or run tasks inside approved folders.',
        consentOpSafe: 'Destructive operations remain blocked by default.',
        consentOpDestructive: 'Includes destructive or sensitive changes as requested.',
        consentExpiryTpl: 'Expires automatically in {dur}. You can revoke or emergency-stop anytime.',
        s4Title: 'Your computer is connected!',
        s4Subtitle: 'Open ChatGPT to start working with your computer right away.',
        s4LblConn: 'Status:',
        s4ValConn: 'Connected',
        s4LblPerm: 'Active permission:',
        permRead: 'Read-Only (Safe)',
        permScoped: 'Scoped folder control',
        permOwner: 'Owner control',
        btnOpenChatGPT: 'Open ChatGPT',
        btnTestConn: 'Test connection',
        s4TestOk: 'Connection is healthy and ChatGPT response is verified.',
        s4TestWait: 'Connection is active; waiting for a fresh test prompt from ChatGPT.',
        s4ExamplesTitle: 'Safe example prompts to try right now:',
        ex1: 'Check my computer specs (CPU, RAM)',
        ex2: 'View free disk space',
        ex3: 'Read a file in an authorized workspace',
        btnCopy: 'Copy',
        btnCopied: 'Copied',
        btnRevokeInline: 'Manage / Revoke access',
        btnRevokeConfirm: 'Confirm revoke',
        btnCancel: 'Cancel',
        estopBtn: 'Emergency Stop',
        estopResetBtn: 'Reset Emergency Stop',
        stRevokedTitle: 'Access has been revoked. Your computer is in View-Only mode.',
        stRevokedAction: 'Reconnect & re-authorize',
        stWaitingAuthTitle: 'Waiting for your authorization',
        stWaitingAuthAction: 'Review request',
        stExpiredTitle: 'Authorization session expired for your safety.',
        stExpiredAction: 'Re-authorize access',
        stEstopTitle: 'Emergency Stop is active — all control actions are paused.',
        stOfflineTitle: 'Computer is offline or local service is not responding.',
        stOfflineRetry: 'Retry connection',
        stOfflineRepair: 'Auto-repair',
        stReconnectingTitle: 'Reconnecting automatically...',
        advTitle: 'Advanced Settings',
        advSecDiag: 'Diagnostics & Service Status',
        advRefresh: 'Refresh',
        thComp: 'Component',
        thStatus: 'Status',
        thDetail: 'Detail',
        advConsole: 'Open Mode B Console (/console) ↗',
        advSecLogs: 'System Logs (redacted)',
        advLoadLogs: 'Load Logs',
        advLogsEmpty: 'No log entries recorded yet.',
        advSecMaint: 'Repair, Updates & Rollback',
        advRepair: 'Repair Service',
        advUpdates: 'Check for Updates',
        advRollback: 'Rollback',
        advRollbackNone: 'No verified backup snapshot available for rollback yet.',
        advSecReconfig: 'Reconfigure Tunnel',
        lblReconfigTid: 'New Tunnel ID',
        lblReconfigKey: 'New API Key',
        lblReconfigMode: 'Connection mode',
        btnReconfig: 'Reconfigure Tunnel',
        advRevoke: 'Revoke All Access',
        advLogout: 'Sign Out',
        advUninstallTitle: 'Uninstall Local Services',
        advUninstallDesc: 'Stops and unregisters verified background services. Your local data and keys are preserved.',
        advUninstallBtn: 'Uninstall',
        advUninstallConfirm: 'Confirm Uninstall',
        loginTitle: 'WinOwner Setup Center — Local Owner Sign-In',
        loginDesc: 'Use Open-SetupCenter.cmd to sign in automatically with zero manual key entry.',
        loginLabel: 'Owner Console Secret / Admin Key',
        loginBtn: 'Sign In',
        minutesUnit: 'minutes',
        hoursUnit: 'hours'
      }
    };

    function T(key) {
      return (I18N[currentLang] && I18N[currentLang][key]) || I18N.en[key] || key;
    }

    function setText(id, text) {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    }

    function showEl(id, visible) {
      const el = document.getElementById(id);
      if (!el) return;
      if (visible) el.classList.remove('hidden');
      else el.classList.add('hidden');
    }

    function setPill(id, value, colorClass) {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = value;
      el.className = 'pill ' + colorClass;
    }

    function formatHumanDuration(seconds) {
      const secs = Math.max(60, Number(seconds || 3600));
      if (secs >= 3600 && secs % 3600 === 0) {
        return Math.round(secs / 3600) + ' ' + T('hoursUnit');
      }
      return Math.max(1, Math.round(secs / 60)) + ' ' + T('minutesUnit');
    }

    function createCheckSvg() {
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('width', '14');
      svg.setAttribute('height', '14');
      svg.setAttribute('viewBox', '0 0 24 24');
      svg.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', 'M5 12.5l4.2 4.2L19 7');
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', 'currentColor');
      path.setAttribute('stroke-width', '2.8');
      path.setAttribute('stroke-linecap', 'round');
      path.setAttribute('stroke-linejoin', 'round');
      svg.appendChild(path);
      return svg;
    }

    function setTopProgress(active) {
      showEl('top-progress-wrap', Boolean(active));
    }

    async function syncCsrfFromState() {
      try {
        const res = await fetch('/setup/api/state', {
          method: 'GET',
          headers: { 'Accept': 'application/json' },
          credentials: 'same-origin'
        });
        if (!res.ok) return null;
        const data = await res.json().catch(() => null);
        if (data && data.csrf_token) {
          csrfToken = data.csrf_token;
        }
        return data;
      } catch (_) {
        return null;
      }
    }

    // A1 Fix: Transparent CSRF resync & single retry on HTTP 403 or missing token
    async function apiCall(path, method = 'GET', body = null, _retried = false) {
      const isMutating = method !== 'GET' && method !== 'HEAD';
      if (isMutating && !csrfToken && path !== '/console/api/login' && path !== '/setup/api/local-handoff') {
        await syncCsrfFromState();
      }
      const headers = { 'Accept': 'application/json' };
      if (csrfToken && isMutating) {
        headers['X-CSRF-Token'] = csrfToken;
      }
      if (body !== null) {
        headers['Content-Type'] = 'application/json';
      }
      const res = await fetch(path, {
        method,
        headers,
        credentials: 'same-origin',
        body: body !== null ? JSON.stringify(body) : null
      });
      if (
        res.status === 403 &&
        isMutating &&
        !_retried &&
        path !== '/console/api/login' &&
        path !== '/setup/api/local-handoff'
      ) {
        const refreshed = await syncCsrfFromState();
        if (refreshed && refreshed.csrf_token) {
          return apiCall(path, method, body, true);
        }
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = new Error(data.error_description || data.error || ('HTTP ' + res.status));
        err.status = res.status;
        err.code = data.error || '';
        throw err;
      }
      return data;
    }

    function applyStaticTranslations() {
      document.documentElement.lang = currentLang;
      setText('hdr-title', T('hdrTitle'));
      setText('lbl-advanced-btn', T('advBtn'));
      document.getElementById('lang-seg-vi').classList.toggle('active', currentLang === 'vi');
      document.getElementById('lang-seg-en').classList.toggle('active', currentLang === 'en');

      setText('loading-text', T('loadingText'));
      setText('s1-title', T('s1Title'));
      setText('s1-subtitle', T('s1Subtitle'));
      setText('s1-chk1-lbl', T('s1Chk1'));
      setText('s1-chk2-lbl', T('s1Chk2'));
      setText('s1-chk3-lbl', T('s1Chk3'));
      setText('s1-ready-text', T('s1Ready'));
      setText('s1-error-summary', T('viewErrDetails'));

      setText('s2-title', T('s2Title'));
      setText('s2-subtitle', T('s2Subtitle'));
      setText('s2-connecting-text', T('s2Connecting'));
      setText('s2-error-title', T('s2ErrorTitle'));
      setText('s2-error-summary', T('viewErrDetails'));
      setText('lbl-tunnel-id', T('lblTid'));
      setText('hint-tunnel-id', T('hintTid'));
      setText('lbl-api-key', T('lblKey'));
      document.getElementById('input-runtime-key').placeholder = T('placeholderKey');
      setText('hint-api-key', T('hintKey'));
      const keyInput = document.getElementById('input-runtime-key');
      setText('btn-toggle-key-vis', keyInput.type === 'password' ? T('btnShow') : T('btnHide'));
      setText('lbl-access-mode', T('lblAccessMode'));
      setText('mode-readonly-title', T('modeRoTitle'));
      setText('mode-readonly-desc', T('modeRoDesc'));
      setText('mode-confirm-title', T('modeConfirmTitle'));
      setText('mode-confirm-desc', T('modeConfirmDesc'));
      setText('s2-guide-summary', T('s2GuideSummary'));
      setText('s2-guide-1', T('s2Guide1'));
      setText('s2-guide-2', T('s2Guide2'));
      setText('s2-guide-3', T('s2Guide3'));
      setText('lnk-openai-keys', T('lnkKeys'));
      setText('lnk-openai-tunnels', T('lnkTunnels'));
      setText('btn-cancel-change-tunnel', T('btnCancelChange'));

      setText('s2b-badge', T('s2bBadge'));
      setText('btn-change-tunnel', T('btnChangeTunnel'));
      setText('s2b-step1-title', T('s2bStep1Title'));
      setText('s2b-step1-desc', T('s2bStep1Desc'));
      setText('btn-open-chatgpt-plugins', T('btnOpenPlugins'));
      setText('s2b-step2-title', T('s2bStep2Title'));
      setText('s2b-test-prompt', T('s2bTestPrompt'));
      setText('btn-copy-verify-prompt', T('btnCopy'));
      setText('s2b-wait-text', T('s2bWaitText'));
      setText('btn-verify-conn', T('btnVerifyConn'));

      setText('s3-title', T('s3Title'));
      setText('s3-subtitle', T('s3Subtitle'));

      setText('s4-title', T('s4Title'));
      setText('s4-subtitle', T('s4Subtitle'));
      setText('s4-lbl-conn', T('s4LblConn'));
      setText('s4-val-conn', T('s4ValConn'));
      setText('s4-lbl-perm', T('s4LblPerm'));
      setText('btn-open-chatgpt', T('btnOpenChatGPT'));
      setText('btn-test-conn', T('btnTestConn'));
      setText('s4-examples-title', T('s4ExamplesTitle'));
      setText('ex-prompt-1', T('ex1'));
      setText('ex-prompt-2', T('ex2'));
      setText('ex-prompt-3', T('ex3'));
      document.querySelectorAll('[data-copy-target]').forEach((b) => { b.textContent = T('btnCopy'); });
      setText('btn-revoke-inline', T('btnRevokeInline'));
      setText('btn-revoke-confirm', T('btnRevokeConfirm'));
      setText('btn-revoke-cancel', T('btnCancel'));

      // Advanced Settings Drawer
      setText('adv-drawer-title', T('advTitle'));
      setText('adv-sec-diag', T('advSecDiag'));
      setText('btn-adv-refresh', T('advRefresh'));
      setText('th-component', T('thComp'));
      setText('th-status', T('thStatus'));
      setText('th-detail', T('thDetail'));
      setText('adv-console-link', T('advConsole'));
      setText('adv-sec-logs', T('advSecLogs'));
      setText('btn-adv-load-logs', T('advLoadLogs'));
      setText('btn-adv-copy-logs', T('btnCopy'));
      setText('adv-sec-maint', T('advSecMaint'));
      setText('btn-adv-repair', T('advRepair'));
      setText('btn-adv-updates', T('advUpdates'));
      setText('btn-adv-rollback', T('advRollback'));
      setText('adv-sec-reconfig', T('advSecReconfig'));
      setText('lbl-reconfig-tid', T('lblReconfigTid'));
      setText('lbl-reconfig-key', T('lblReconfigKey'));
      setText('lbl-reconfig-mode', T('lblReconfigMode'));
      setText('btn-submit-reconfig', T('btnReconfig'));
      setText('btn-adv-revoke', T('advRevoke'));
      setText('btn-adv-logout', T('advLogout'));
      setText('adv-uninstall-title', T('advUninstallTitle'));
      setText('adv-uninstall-desc', T('advUninstallDesc'));
      setText('btn-adv-uninstall', T('advUninstallBtn'));
      setText('btn-adv-uninstall-confirm', T('advUninstallConfirm'));
      setText('btn-adv-uninstall-cancel', T('btnCancel'));

      // Login overlay
      setText('login-title', T('loginTitle'));
      setText('login-label', T('loginLabel'));
      setText('login-btn', T('loginBtn'));

      if (latestState) renderState(latestState);
    }

    function toggleLanguage() {
      currentLang = currentLang === 'vi' ? 'en' : 'vi';
      applyStaticTranslations();
    }

    function determineEffectiveUiState(st) {
      if (uiOverrideState) return uiOverrideState;
      if (!st) return 'loading';
      return st.ui_state || 'setup_connect';
    }

    function determineTargetStep(st) {
      if (!st) return 1;
      const localOk = st.status_table && st.status_table.local_service === 'Ready';
      if (!localOk) return 1;
      const cgVerified = Boolean(st.chatgpt_connection && st.chatgpt_connection.verified_read_tool_call);
      if (!cgVerified) return 2;
      const pending = (st.pending_challenges || []).length;
      const ownerVerified = Boolean(st.owner_identity && st.owner_identity.verified);
      const effReady = Boolean(st.effective_permissions && st.effective_permissions.owner_control_ready);
      const supportsId = Boolean(st.chatgpt_connection && st.chatgpt_connection.supports_verifiable_owner_identity);

      if (st.emergency_stop) return 4;
      if (pending > 0 && !ownerVerified) return 3;
      if (effReady || ownerVerified) return 4;
      if (!supportsId) return 3;
      return 3;
    }

    function maxAllowedStep(st) {
      if (!st) return 1;
      const localOk = st.status_table && st.status_table.local_service === 'Ready';
      if (!localOk) return 1;
      const cgVerified = Boolean(st.chatgpt_connection && st.chatgpt_connection.verified_read_tool_call);
      if (!cgVerified) return 2;
      // Once ChatGPT is verified, user may view Step 3 or Step 4 (Step 4 honestly displays Read-Only if not escalated)
      return 4;
    }

    function renderStepper(st, activeStep) {
      const nav = document.getElementById('stepper-container');
      nav.textContent = '';
      const labels = T('steps');
      const localOk = Boolean(st && st.status_table && st.status_table.local_service === 'Ready');
      const cgOk = Boolean(st && st.chatgpt_connection && st.chatgpt_connection.verified_read_tool_call);
      const ownerOk = Boolean(st && st.owner_identity && st.owner_identity.verified);
      const ctrlOk = Boolean(st && st.effective_permissions && st.effective_permissions.owner_control_ready);
      const completed = [localOk, cgOk, ownerOk || (cgOk && activeStep === 4), ctrlOk || (cgOk && activeStep === 4)];
      const maxStep = maxAllowedStep(st);

      for (let i = 1; i <= 4; i++) {
        const btn = document.createElement('button');
        btn.type = 'button';
        const isError = (i === 1 && st && !localOk) || (i === 2 && uiOverrideState === 'connection_failed');
        const isDone = completed[i - 1] && i < activeStep && !isError;
        const isActive = i === activeStep;
        const canClick = Boolean(st && i <= maxStep);

        btn.className = 'step-item' + (isDone ? ' done' : '') + (isActive ? ' active' : '') + (isError ? ' error' : '') + (canClick ? ' clickable' : '');
        if (isActive) btn.setAttribute('aria-current', 'step');
        if (!canClick) btn.disabled = true;

        const node = document.createElement('span');
        node.className = 'step-node';
        if (isError) {
          node.textContent = '!';
        } else if (isDone || (i === 4 && ctrlOk)) {
          node.appendChild(createCheckSvg());
        } else {
          node.textContent = String(i);
        }

        const lbl = document.createElement('span');
        lbl.className = 'step-label';
        lbl.textContent = labels[i - 1];

        btn.appendChild(node);
        btn.appendChild(lbl);
        if (canClick) {
          btn.addEventListener('click', () => {
            manualStepView = i;
            if (i === 2) forceStep2Form = !(st.tunnel_details && st.tunnel_details.connected);
            renderState(latestState);
          });
        }
        nav.appendChild(btn);
      }
    }

    function renderStateBanner(st, uiState) {
      const box = document.getElementById('state-alert-banner');
      box.textContent = '';
      box.className = 'hidden';
      box.removeAttribute('role');

      function buildBanner(variant, iconText, titleText, actions, isAlert) {
        box.className = 'callout ' + variant;
        if (isAlert) box.setAttribute('role', 'alert');
        else box.setAttribute('aria-live', 'polite');

        const ic = document.createElement('span');
        ic.className = 'callout-icon';
        ic.setAttribute('aria-hidden', 'true');
        if (iconText === 'SPINNER') {
          const sp = document.createElement('span');
          sp.className = 'spinner';
          ic.appendChild(sp);
        } else {
          ic.textContent = iconText;
        }
        const body = document.createElement('div');
        body.className = 'callout-body';
        const title = document.createElement('div');
        title.className = 'callout-title';
        title.textContent = titleText;
        body.appendChild(title);

        if (actions && actions.length > 0) {
          const actRow = document.createElement('div');
          actRow.className = 'callout-actions';
          for (const a of actions) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = a.cls;
            b.textContent = a.label;
            b.addEventListener('click', a.onClick);
            actRow.appendChild(b);
          }
          body.appendChild(actRow);
        }
        box.appendChild(ic);
        box.appendChild(body);
      }

      if (uiState === 'emergency_stopped') {
        buildBanner('critical', '⛔', T('stEstopTitle'), [
          { cls: 'btn btn-danger btn-sm', label: T('estopResetBtn'), onClick: () => runEmergencyStopToggle() }
        ], true);
      } else if (uiState === 'revoked') {
        buildBanner('warn', '⚠', T('stRevokedTitle'), [
          { cls: 'btn btn-primary btn-sm', label: T('stRevokedAction'), onClick: () => { manualStepView = 3; renderState(latestState); } }
        ], false);
      } else if (uiState === 'expired') {
        buildBanner('warn', '⏱', T('stExpiredTitle'), [
          { cls: 'btn btn-primary btn-sm', label: T('stExpiredAction'), onClick: () => { manualStepView = 3; renderState(latestState); } }
        ], false);
      } else if (uiState === 'offline') {
        buildBanner('offline', '☁', T('stOfflineTitle'), [
          { cls: 'btn btn-primary btn-sm', label: T('stOfflineRetry'), onClick: () => refreshSetupState() },
          { cls: 'btn btn-sm', label: T('stOfflineRepair'), onClick: () => runInstallRepair() }
        ], true);
      } else if (uiState === 'waiting_authorization' && manualStepView !== null && manualStepView !== 3) {
        buildBanner('info', '🛡', T('stWaitingAuthTitle'), [
          { cls: 'btn btn-primary btn-sm', label: T('stWaitingAuthAction'), onClick: () => { manualStepView = 3; renderState(latestState); } }
        ], false);
      } else if (uiState === 'reconnecting') {
        buildBanner('info', 'SPINNER', T('stReconnectingTitle'), [], false);
      }
    }

    function renderStep1(st) {
      const checks = (st && st.checks) || {};
      const localOk = Boolean(st && st.status_table && st.status_table.local_service === 'Ready');
      const items = [
        ['s1-chk1-val', localOk && checks.runtime_ready !== false],
        ['s1-chk2-val', localOk && checks.service_ready !== false],
        ['s1-chk3-val', checks.security_ready !== false]
      ];
      for (const [id, ok] of items) {
        const el = document.getElementById(id);
        el.textContent = ok ? T('chkOk') : T('chkFail');
        el.className = 'check-badge ' + (ok ? 'ok' : 'fail');
      }

      const alertEl = document.getElementById('s1-alert');
      alertEl.textContent = '';
      if (!localOk) {
        alertEl.className = 'callout danger';
        const ic = document.createElement('span');
        ic.className = 'callout-icon';
        ic.textContent = '✕';
        const bd = document.createElement('div');
        bd.className = 'callout-body';
        const t = document.createElement('div');
        t.className = 'callout-title';
        t.textContent = T('s1FailTitle');
        const d = document.createElement('p');
        d.className = 'callout-desc';
        d.textContent = T('s1FailDesc');
        bd.appendChild(t);
        bd.appendChild(d);
        alertEl.appendChild(ic);
        alertEl.appendChild(bd);
        showEl('s1-ready-banner', false);
        showEl('s1-error-details', Boolean(step1ErrorDetail));
        setText('s1-error-detail-text', step1ErrorDetail);
        setText('btn-s1-primary', T('s1BtnRepair'));
      } else {
        alertEl.className = 'hidden';
        showEl('s1-ready-banner', true);
        showEl('s1-error-details', false);
        setText('btn-s1-primary', T('s1BtnContinue'));
      }
    }

    function renderStep2(st, uiState) {
      const tunnelConnected = Boolean(st && st.tunnel_details && st.tunnel_details.connected);
      const isConnecting = uiState === 'connecting';
      const showForm = !tunnelConnected || forceStep2Form || isConnecting || uiState === 'connection_failed';

      showEl('s2-form-pane', showForm);
      showEl('s2-verify-pane', !showForm);

      if (showForm) {
        showEl('s2-connecting-banner', isConnecting);
        showEl('s2-error-alert', uiState === 'connection_failed');
        showEl('s2-error-details', uiState === 'connection_failed' && Boolean(connectErrorDetail));
        setText('s2-error-detail-text', connectErrorDetail);
        setText('s2-error-hint', T('s2ErrorHint'));
        showEl('btn-cancel-change-tunnel', tunnelConnected && !isConnecting);

        const submitBtn = document.getElementById('btn-submit-connect');
        submitBtn.disabled = isConnecting;
        submitBtn.textContent = isConnecting
          ? T('btnConnecting')
          : (uiState === 'connection_failed' ? T('btnRetry') : T('btnConnect'));
        document.getElementById('input-tunnel-id').disabled = isConnecting;
        document.getElementById('input-runtime-key').disabled = isConnecting;
      } else {
        const cg = (st && st.chatgpt_connection) || {};
        const promptText = currentLang === 'vi'
          ? (cg.verification_prompt_vi || T('s2bTestPrompt'))
          : (cg.verification_prompt_en || T('s2bTestPrompt'));
        setText('s2b-test-prompt', promptText);
      }
    }

    function renderStep3(st) {
      const pending = (st && st.pending_challenges) || [];
      const container = document.getElementById('challenges-container');
      container.textContent = '';

      if (pending.length > 0) {
        showEl('s3-pending-pane', true);
        showEl('s3-empty-pane', false);

        for (const ch of pending) {
          const card = document.createElement('div');
          card.className = 'consent-card';

          const top = document.createElement('div');
          top.className = 'consent-top';
          const appWrap = document.createElement('div');
          const appName = document.createElement('div');
          appName.style.fontWeight = '600';
          appName.style.fontSize = '15px';
          appName.textContent = ch.client_name || T('consentAppFallback');
          const scopePill = document.createElement('span');
          const isFull = String(ch.requested_permission_level || '').toUpperCase() === 'OWNER';
          scopePill.className = 'pill ' + (isFull ? 'amber' : 'blue');
          scopePill.style.marginTop = '4px';
          scopePill.textContent = isFull ? T('consentScopeFull') : T('consentScopeFolders');
          appWrap.appendChild(appName);
          appWrap.appendChild(scopePill);

          const codeWrap = document.createElement('div');
          codeWrap.style.textAlign = 'right';
          const codeLbl = document.createElement('div');
          codeLbl.style.fontSize = '11.5px';
          codeLbl.style.color = 'var(--text-muted)';
          codeLbl.textContent = T('consentCodeLbl');
          const codeBadge = document.createElement('div');
          codeBadge.className = 'code-badge';
          codeBadge.textContent = ch.verification_code || '----';
          codeWrap.appendChild(codeLbl);
          codeWrap.appendChild(codeBadge);

          top.appendChild(appWrap);
          top.appendChild(codeWrap);
          card.appendChild(top);

          const wsList = ch.requested_workspaces || [];
          if (wsList.length > 0) {
            const chips = document.createElement('div');
            chips.className = 'chip-list';
            for (const wsPath of wsList) {
              const chip = document.createElement('span');
              chip.className = 'chip';
              chip.textContent = wsPath;
              chips.appendChild(chip);
            }
            card.appendChild(chips);
          }

          const ul = document.createElement('ul');
          ul.className = 'consent-list';
          const bullets = [
            T('consentOpRead'),
            T('consentOpWrite'),
            ch.allow_destructive ? T('consentOpDestructive') : T('consentOpSafe')
          ];
          for (const bTxt of bullets) {
            const li = document.createElement('li');
            li.textContent = bTxt;
            ul.appendChild(li);
          }
          card.appendChild(ul);

          const exp = document.createElement('div');
          exp.style.fontSize = '12px';
          exp.style.color = 'var(--text-muted)';
          exp.textContent = T('consentExpiryTpl').replace('{dur}', formatHumanDuration(ch.requested_ttl_seconds));
          card.appendChild(exp);

          const btnRow = document.createElement('div');
          btnRow.className = 'btn-row';
          const btnApprove = document.createElement('button');
          btnApprove.type = 'button';
          btnApprove.className = 'btn btn-primary';
          btnApprove.textContent = T('confirmOwnerBtn');
          btnApprove.addEventListener('click', () => confirmOwnerAccess(
            ch.challenge_id,
            ch.requested_permission_level,
            ch.requested_workspaces
          ));

          const btnDeny = document.createElement('button');
          btnDeny.type = 'button';
          btnDeny.className = 'btn';
          btnDeny.textContent = T('denyBtn');
          btnDeny.addEventListener('click', () => denyChallenge(ch.challenge_id));

          btnRow.appendChild(btnApprove);
          btnRow.appendChild(btnDeny);
          card.appendChild(btnRow);
          container.appendChild(card);
        }
      } else {
        showEl('s3-pending-pane', false);
        showEl('s3-empty-pane', true);
        const cg = (st && st.chatgpt_connection) || {};
        const isViewOnly = !cg.supports_verifiable_owner_identity || cg.last_auth_mode === 'shared_tunnel_bearer';
        if (isViewOnly) {
          setText('s3-info-icon', 'ℹ');
          setText('s3-info-title', T('s3RoTitle'));
          setText('s3-info-desc', T('s3RoDesc'));
          setText('btn-s3-primary', T('s3BtnGoReady'));
          setText('btn-s3-secondary', T('s3BtnSwitchControl'));
        } else {
          setText('s3-info-icon', '🛡');
          setText('s3-info-title', T('s3WaitTitle'));
          setText('s3-info-desc', T('s3WaitDesc'));
          setText('btn-s3-primary', T('s3BtnCheck'));
          setText('btn-s3-secondary', T('s3BtnUseRo'));
        }
      }
    }

    function renderStep4(st) {
      const ep = (st && st.effective_permissions) || {};
      const level = String(ep.level || 'READ').toUpperCase();
      const permPill = document.getElementById('s4-pill-perm');
      if (st && st.emergency_stop) {
        permPill.textContent = T('permRead');
        permPill.className = 'pill red';
      } else if (level === 'OWNER') {
        permPill.textContent = T('permOwner');
        permPill.className = 'pill green';
      } else if (level === 'SCOPED') {
        permPill.textContent = T('permScoped');
        permPill.className = 'pill blue';
      } else {
        permPill.textContent = T('permRead');
        permPill.className = 'pill neutral';
      }

      const estopBtn = document.getElementById('btn-estop-footer');
      const isEstop = Boolean(st && st.emergency_stop);
      estopBtn.textContent = isEstop ? T('estopResetBtn') : T('estopBtn');
      estopBtn.className = isEstop ? 'btn btn-danger btn-sm' : 'btn btn-danger-outline btn-sm';
    }

    function renderAdvancedDiagnostics(st) {
      if (!st) return;
      const tbl = st.status_table || {};
      setPill('val-local-service', tbl.local_service || 'Error', tbl.local_service === 'Ready' ? 'green' : 'red');
      setPill('val-tunnel', tbl.tunnel || 'Disconnected', tbl.tunnel === 'Connected' ? 'green' : 'amber');
      setPill('val-chatgpt', tbl.chatgpt || 'Not Verified', tbl.chatgpt === 'Verified' ? 'green' : 'amber');
      setPill('val-owner', tbl.owner_identity || 'Pending', tbl.owner_identity === 'Verified' ? 'green' : 'amber');
      const permColor = tbl.permissions === 'Owner' ? 'green' : (tbl.permissions === 'Scoped' ? 'blue' : 'amber');
      setPill('val-permissions', tbl.permissions || 'Read', permColor);
      setPill('val-version', tbl.version || 'Current', tbl.version === 'Current' ? 'green' : 'blue');

      setText('det-local-service', 'Profile: ' + (st.profile || 'read_only') + (st.emergency_stop ? ' [E-STOP]' : ''));
      setText('det-tunnel', (st.tunnel_details && st.tunnel_details.status_code) || 'TUNNEL_NOT_CONFIGURED');
      const cg = st.chatgpt_connection || {};
      setText('det-chatgpt', cg.verified_read_tool_call
        ? ((cg.last_verified_tool || 'verified') + ' (' + (cg.last_auth_mode || '') + ')')
        : 'Not verified');
      const oi = st.owner_identity || {};
      setText('det-owner', oi.verified
        ? ((oi.client_id_masked || 'verified') + ' (' + (oi.remaining_seconds || 0) + 's)')
        : ('Pending: ' + (oi.pending_challenges_count || 0)));
      const ep = st.effective_permissions || {};
      setText('det-permissions', (ep.enabled_tiers || ['READ']).join(', '));
      const vi = st.version_info || {};
      const verStr = (vi.current_version || 'v1.2.0') + (vi.update_available ? (' -> ' + vi.latest_version) : '');
      setText('det-version', verStr);
      setText('adv-version-meta', 'Version: ' + verStr + (vi.git_sha ? (' · ' + String(vi.git_sha).slice(0, 8)) : ''));
      setText('adv-guidance-box', (st.guidance && st.guidance[currentLang]) || '');

      const rbBtn = document.getElementById('btn-adv-rollback');
      const rbAvail = Boolean(vi.rollback_available);
      rbBtn.title = rbAvail ? '' : T('advRollbackNone');

      const advEstop = document.getElementById('btn-adv-estop');
      advEstop.textContent = st.emergency_stop ? T('estopResetBtn') : T('estopBtn');
    }

    function scheduleAutoPoll(activeStep, st) {
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
      if (!st) return;
      const tunnelConnected = Boolean(st.tunnel_details && st.tunnel_details.connected);
      const cgVerified = Boolean(st.chatgpt_connection && st.chatgpt_connection.verified_read_tool_call);
      const ownerVerified = Boolean(st.owner_identity && st.owner_identity.verified);
      const supportsId = Boolean(st.chatgpt_connection && st.chatgpt_connection.supports_verifiable_owner_identity);

      if (activeStep === 2 && tunnelConnected && !cgVerified && !forceStep2Form) {
        pollTimer = setTimeout(() => refreshSetupState({ silent: true }), 3000);
      } else if (activeStep === 3 && cgVerified && !ownerVerified && supportsId) {
        pollTimer = setTimeout(() => refreshSetupState({ silent: true }), 2500);
      } else if (uiOverrideState === 'reconnecting') {
        pollTimer = setTimeout(() => refreshSetupState({ silent: true }), 2500);
      }
    }

    function renderState(st) {
      latestState = st;
      const mainEl = document.getElementById('wizard-main');
      const uiState = determineEffectiveUiState(st);
      mainEl.setAttribute('data-ui-state', uiState);
      setTopProgress(uiState === 'connecting' || uiState === 'reconnecting');

      if (!st || uiState === 'loading') {
        showEl('view-loading', true);
        showEl('step-view-1', false);
        showEl('step-view-2', false);
        showEl('step-view-3', false);
        showEl('step-view-4', false);
        renderStepper(null, 1);
        return;
      }

      showEl('view-loading', false);
      const backendStep = determineTargetStep(st);
      const maxStep = maxAllowedStep(st);
      if (manualStepView !== null && manualStepView > maxStep) {
        manualStepView = null;
      }
      let activeStep = manualStepView !== null ? manualStepView : backendStep;

      // Auto-advance Step 1 -> Step 2 cleanly once local checks pass on initial visit
      if (activeStep === 1 && backendStep >= 2 && !step1AutoAdvanced && manualStepView === null) {
        step1AutoAdvanced = true;
        activeStep = backendStep;
      }

      renderStepper(st, activeStep);
      renderStateBanner(st, uiState);

      showEl('step-view-1', activeStep === 1);
      showEl('step-view-2', activeStep === 2);
      showEl('step-view-3', activeStep === 3);
      showEl('step-view-4', activeStep === 4);

      if (activeStep === 1) renderStep1(st);
      if (activeStep === 2) renderStep2(st, uiState);
      if (activeStep === 3) renderStep3(st);
      if (activeStep === 4) renderStep4(st);

      renderAdvancedDiagnostics(st);
      scheduleAutoPoll(activeStep, st);
    }

    async function refreshSetupState(opts = {}) {
      try {
        const st = await apiCall('/setup/api/state', 'GET');
        if (st && st.csrf_token) csrfToken = st.csrf_token;
        reconnectFailures = 0;
        if (uiOverrideState === 'offline' || uiOverrideState === 'reconnecting' || uiOverrideState === 'loading') {
          uiOverrideState = null;
        }
        renderState(st);
        return st;
      } catch (e) {
        if (e.status === 401) {
          document.getElementById('login-overlay').classList.remove('hidden');
          return null;
        }
        reconnectFailures += 1;
        uiOverrideState = reconnectFailures <= 2 ? 'reconnecting' : 'offline';
        if (latestState) {
          renderState(latestState);
        } else {
          document.getElementById('wizard-main').setAttribute('data-ui-state', uiOverrideState);
          renderStateBanner(null, uiOverrideState);
          showEl('view-loading', false);
        }
        if (!opts.silent) throw e;
        return null;
      }
    }

    function showAdvFeedback(variant, message) {
      const box = document.getElementById('adv-feedback');
      box.className = 'callout ' + variant;
      box.textContent = message;
    }

    async function runInstallRepair() {
      const btn = document.getElementById('btn-s1-primary');
      if (btn) {
        btn.disabled = true;
        btn.textContent = T('s1BtnRepairing');
      }
      uiOverrideState = 'connecting';
      if (latestState) renderState(latestState);
      try {
        const res = await apiCall('/setup/api/actions/install-repair', 'POST', {});
        step1ErrorDetail = '';
        uiOverrideState = null;
        manualStepView = null;
        if (res.state_snapshot) renderState(res.state_snapshot);
        showAdvFeedback('success', (res.summary && (currentLang === 'vi' ? res.summary.message_vi : res.summary.message_en)) || 'OK');
      } catch (e) {
        step1ErrorDetail = e.message;
        uiOverrideState = null;
        if (latestState) renderState(latestState);
        showAdvFeedback('danger', e.message);
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function submitConnectChatGPT(isReconfigure) {
      const tidEl = document.getElementById(isReconfigure ? 'reconfig-tunnel-id' : 'input-tunnel-id');
      const keyEl = document.getElementById(isReconfigure ? 'reconfig-runtime-key' : 'input-runtime-key');
      const tid = (tidEl.value || '').trim().toLowerCase();
      const apiKey = (keyEl.value || '').trim();
      let authMode = 'auth_none';
      if (isReconfigure) {
        const sel = document.getElementById('reconfig-auth-mode');
        authMode = sel ? sel.value : 'auth_none';
      } else {
        const checked = document.querySelector('input[name="connect_auth_mode"]:checked');
        authMode = checked ? checked.value : 'auth_none';
      }

      if (!/^tunnel_(?:[a-z0-9]{32}|[a-z0-9]{4}_[a-z0-9]{32})$/.test(tid)) {
        if (isReconfigure) {
          showEl('reconfig-error-alert', true);
          setText('reconfig-error-text', T('s2ErrorInvalidTid'));
        } else {
          connectErrorDetail = T('s2ErrorInvalidTid');
          uiOverrideState = 'connection_failed';
          renderState(latestState);
        }
        return;
      }
      if (apiKey.length < 20) {
        if (isReconfigure) {
          showEl('reconfig-error-alert', true);
          setText('reconfig-error-text', T('s2ErrorInvalidKey'));
        } else {
          connectErrorDetail = T('s2ErrorInvalidKey');
          uiOverrideState = 'connection_failed';
          renderState(latestState);
        }
        return;
      }

      const ep = isReconfigure ? '/setup/api/actions/reconfigure-tunnel' : '/setup/api/actions/connect-chatgpt';
      if (isReconfigure) {
        showEl('reconfig-error-alert', false);
        document.getElementById('btn-submit-reconfig').disabled = true;
      } else {
        connectErrorDetail = '';
        uiOverrideState = 'connecting';
        renderState(latestState);
      }

      try {
        const res = await apiCall(ep, 'POST', {
          tunnel_id: tid,
          runtime_api_key: apiKey,
          auth_mode: authMode
        });
        // A1 Fix: Clear API key ONLY after apiCall succeeds (never before!)
        keyEl.value = '';
        uiOverrideState = null;
        forceStep2Form = false;
        if (!isReconfigure) manualStepView = 2;
        if (res.state_snapshot) renderState(res.state_snapshot);
        if (isReconfigure) {
          showAdvFeedback('success', (res.summary && (currentLang === 'vi' ? res.summary.message_vi : res.summary.message_en)) || 'OK');
        }
      } catch (e) {
        // Key input intentionally preserved so user can retry without re-typing
        if (isReconfigure) {
          showEl('reconfig-error-alert', true);
          setText('reconfig-error-text', e.message);
        } else {
          connectErrorDetail = e.message;
          uiOverrideState = 'connection_failed';
          renderState(latestState);
        }
      } finally {
        if (isReconfigure) {
          document.getElementById('btn-submit-reconfig').disabled = false;
        }
      }
    }

    async function runVerifyConnection(fromStep4 = false) {
      const btn = document.getElementById(fromStep4 ? 'btn-test-conn' : 'btn-verify-conn');
      if (btn) btn.disabled = true;
      try {
        const res = await apiCall('/setup/api/actions/verify-connection', 'POST', {});
        uiOverrideState = null;
        if (res.state_snapshot) {
          if (!fromStep4 && res.state_snapshot.chatgpt_connection && res.state_snapshot.chatgpt_connection.verified_read_tool_call) {
            manualStepView = null;
          }
          renderState(res.state_snapshot);
        }
        if (fromStep4) {
          const fb = document.getElementById('s4-test-feedback');
          const ok = Boolean(res.summary && res.summary.chatgpt_verified);
          fb.className = 'callout ' + (ok ? 'success' : 'warn');
          fb.textContent = ok ? T('s4TestOk') : T('s4TestWait');
        }
      } catch (e) {
        if (fromStep4) {
          const fb = document.getElementById('s4-test-feedback');
          fb.className = 'callout danger';
          fb.textContent = e.message;
        }
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function confirmOwnerAccess(challengeId, level, workspaces) {
      showEl('s3-error-alert', false);
      try {
        const res = await apiCall('/setup/api/pairing/challenges/' + encodeURIComponent(challengeId) + '/approve', 'POST', {
          approved_permission_level: level,
          approved_workspaces: workspaces,
          activate_profile: true
        });
        uiOverrideState = null;
        manualStepView = 4;
        if (res.state_snapshot) renderState(res.state_snapshot);
      } catch (e) {
        showEl('s3-error-alert', true);
        setText('s3-error-text', e.message);
      }
    }

    async function denyChallenge(challengeId) {
      showEl('s3-error-alert', false);
      try {
        const res = await apiCall('/setup/api/pairing/challenges/' + encodeURIComponent(challengeId) + '/deny', 'POST', {
          reason: 'denied_by_owner_in_setup_center'
        });
        if (res.state_snapshot) renderState(res.state_snapshot);
      } catch (e) {
        showEl('s3-error-alert', true);
        setText('s3-error-text', e.message);
      }
    }

    async function runRevokeAccess() {
      try {
        const res = await apiCall('/setup/api/actions/revoke-access', 'POST', { revoke_all: true });
        showEl('revoke-confirm-wrap', false);
        showEl('btn-revoke-inline', true);
        if (res.state_snapshot) renderState(res.state_snapshot);
        showAdvFeedback('warn', T('stRevokedTitle'));
      } catch (e) {
        showAdvFeedback('danger', e.message);
      }
    }

    async function runEmergencyStopToggle() {
      const isActive = Boolean(latestState && latestState.emergency_stop);
      try {
        const res = await apiCall('/setup/api/actions/revoke-access', 'POST', {
          revoke_all: false,
          engage_emergency_stop: !isActive,
          reset_emergency_stop: isActive
        });
        if (res.state_snapshot) renderState(res.state_snapshot);
      } catch (e) {
        showAdvFeedback('danger', e.message);
      }
    }

    async function runCheckUpdates() {
      try {
        const res = await apiCall('/setup/api/actions/check-updates', 'POST', {});
        if (res.state_snapshot) renderState(res.state_snapshot);
        const s = res.summary || {};
        const msg = (currentLang === 'vi' ? s.message_vi : s.message_en) ||
          (s.update_available ? ('Update: ' + s.latest_version) : ('Current: ' + (s.current_version || 'v1.2.0')));
        showAdvFeedback(s.update_available ? 'info' : 'success', msg);
      } catch (e) {
        showAdvFeedback('danger', e.message);
      }
    }

    async function runRollback() {
      try {
        const res = await apiCall('/setup/api/actions/rollback', 'POST', {});
        if (res.state_snapshot) renderState(res.state_snapshot);
        const s = res.summary || {};
        showAdvFeedback('success', (currentLang === 'vi' ? s.message_vi : s.message_en) || 'Rollback complete');
      } catch (e) {
        showAdvFeedback('warn', e.message);
      }
    }

    async function runUninstall() {
      try {
        const res = await apiCall('/setup/api/actions/uninstall', 'POST', { confirm: true });
        showEl('uninstall-confirm-wrap', false);
        showEl('btn-adv-uninstall', true);
        manualStepView = null;
        if (res.state_snapshot) renderState(res.state_snapshot);
        const s = res.summary || {};
        showAdvFeedback('warn', (currentLang === 'vi' ? s.message_vi : s.message_en) || 'Uninstalled');
      } catch (e) {
        showAdvFeedback('danger', e.message);
      }
    }

    async function loadRedactedLogs() {
      try {
        const res = await apiCall('/setup/api/logs?limit=40', 'GET');
        const entries = (res && res.entries) || [];
        const box = document.getElementById('adv-logs-box');
        if (entries.length === 0) {
          box.textContent = T('advLogsEmpty');
          return;
        }
        box.textContent = entries.map((e) => {
          const ts = e.timestamp ? ('[' + e.timestamp + '] ') : '';
          const src = e.source ? ('(' + e.source + ') ') : '';
          return ts + src + (e.message || '');
        }).join('\\n');
      } catch (e) {
        setText('adv-logs-box', e.message);
      }
    }

    async function loginSetupCenter() {
      const pwdEl = document.getElementById('owner-password');
      const errEl = document.getElementById('login-error');
      errEl.textContent = '';
      try {
        const res = await apiCall('/console/api/login', 'POST', { password: pwdEl.value });
        csrfToken = res.csrf_token;
        pwdEl.value = '';
        document.getElementById('login-overlay').classList.add('hidden');
        await refreshSetupState();
      } catch (e) {
        errEl.textContent = e.message;
      }
    }

    async function logoutSetupCenter() {
      try { await apiCall('/console/api/logout', 'POST', {}); } catch (_) {}
      csrfToken = null;
      closeAdvancedDrawer();
      document.getElementById('login-overlay').classList.remove('hidden');
    }

    function openAdvancedDrawer() {
      showEl('advanced-drawer', true);
      loadRedactedLogs();
      document.getElementById('btn-close-advanced').focus();
    }

    function closeAdvancedDrawer() {
      showEl('advanced-drawer', false);
      // Clear sensitive reconfigure input when closing drawer
      document.getElementById('reconfig-runtime-key').value = '';
      showEl('reconfig-error-alert', false);
    }

    async function copyTextToClipboard(text, btn) {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
        }
        if (btn) {
          const orig = btn.textContent;
          btn.textContent = T('btnCopied');
          setTimeout(() => { btn.textContent = orig; }, 1600);
        }
      } catch (_) {}
    }

    function syncModeCardSelection() {
      const isRo = document.getElementById('mode-radio-auth-none').checked;
      document.getElementById('mode-card-readonly').classList.toggle('selected', isRo);
      document.getElementById('mode-card-confirm').classList.toggle('selected', !isRo);
    }

    window.addEventListener('DOMContentLoaded', async () => {
      // Wire all DOM events cleanly (zero inline event handlers)
      document.getElementById('lang-toggle-btn').addEventListener('click', toggleLanguage);
      document.getElementById('btn-advanced-settings').addEventListener('click', openAdvancedDrawer);
      document.getElementById('btn-close-advanced').addEventListener('click', closeAdvancedDrawer);
      document.getElementById('advanced-drawer').addEventListener('click', (ev) => {
        if (ev.target === ev.currentTarget) closeAdvancedDrawer();
      });
      document.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape' && !document.getElementById('advanced-drawer').classList.contains('hidden')) {
          closeAdvancedDrawer();
        }
      });

      document.getElementById('login-btn').addEventListener('click', loginSetupCenter);
      document.getElementById('owner-password').addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') loginSetupCenter();
      });

      document.getElementById('btn-s1-primary').addEventListener('click', () => {
        const localOk = Boolean(latestState && latestState.status_table && latestState.status_table.local_service === 'Ready');
        if (!localOk) {
          runInstallRepair();
        } else {
          manualStepView = 2;
          renderState(latestState);
        }
      });

      document.getElementById('connect-form').addEventListener('submit', (ev) => {
        ev.preventDefault();
        submitConnectChatGPT(false);
      });
      document.getElementById('btn-toggle-key-vis').addEventListener('click', () => {
        const inp = document.getElementById('input-runtime-key');
        inp.type = inp.type === 'password' ? 'text' : 'password';
        setText('btn-toggle-key-vis', inp.type === 'password' ? T('btnShow') : T('btnHide'));
      });
      document.getElementById('mode-radio-auth-none').addEventListener('change', syncModeCardSelection);
      document.getElementById('mode-radio-oauth').addEventListener('change', syncModeCardSelection);
      document.getElementById('btn-change-tunnel').addEventListener('click', () => {
        forceStep2Form = true;
        uiOverrideState = null;
        renderState(latestState);
      });
      document.getElementById('btn-cancel-change-tunnel').addEventListener('click', () => {
        document.getElementById('input-runtime-key').value = '';
        forceStep2Form = false;
        uiOverrideState = null;
        renderState(latestState);
      });
      document.getElementById('btn-copy-verify-prompt').addEventListener('click', (ev) => {
        copyTextToClipboard(document.getElementById('s2b-test-prompt').textContent, ev.currentTarget);
      });
      document.getElementById('btn-verify-conn').addEventListener('click', () => runVerifyConnection(false));

      document.getElementById('btn-s3-primary').addEventListener('click', () => {
        const cg = (latestState && latestState.chatgpt_connection) || {};
        const isViewOnly = !cg.supports_verifiable_owner_identity || cg.last_auth_mode === 'shared_tunnel_bearer';
        if (isViewOnly) {
          manualStepView = 4;
          renderState(latestState);
        } else {
          refreshSetupState();
        }
      });
      document.getElementById('btn-s3-secondary').addEventListener('click', () => {
        const cg = (latestState && latestState.chatgpt_connection) || {};
        const isViewOnly = !cg.supports_verifiable_owner_identity || cg.last_auth_mode === 'shared_tunnel_bearer';
        if (isViewOnly) {
          document.getElementById('mode-radio-oauth').checked = true;
          syncModeCardSelection();
          forceStep2Form = true;
          manualStepView = 2;
          renderState(latestState);
        } else {
          manualStepView = 4;
          renderState(latestState);
        }
      });

      document.getElementById('btn-test-conn').addEventListener('click', () => runVerifyConnection(true));
      document.querySelectorAll('[data-copy-target]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const targetId = btn.getAttribute('data-copy-target');
          const el = document.getElementById(targetId);
          if (el) copyTextToClipboard(el.textContent, btn);
        });
      });

      document.getElementById('btn-revoke-inline').addEventListener('click', () => {
        showEl('btn-revoke-inline', false);
        showEl('revoke-confirm-wrap', true);
      });
      document.getElementById('btn-revoke-cancel').addEventListener('click', () => {
        showEl('revoke-confirm-wrap', false);
        showEl('btn-revoke-inline', true);
      });
      document.getElementById('btn-revoke-confirm').addEventListener('click', runRevokeAccess);
      document.getElementById('btn-estop-footer').addEventListener('click', runEmergencyStopToggle);

      // Advanced drawer controls
      document.getElementById('btn-adv-refresh').addEventListener('click', () => refreshSetupState());
      document.getElementById('btn-adv-load-logs').addEventListener('click', loadRedactedLogs);
      document.getElementById('btn-adv-copy-logs').addEventListener('click', (ev) => {
        copyTextToClipboard(document.getElementById('adv-logs-box').textContent, ev.currentTarget);
      });
      document.getElementById('btn-adv-repair').addEventListener('click', runInstallRepair);
      document.getElementById('btn-adv-updates').addEventListener('click', runCheckUpdates);
      document.getElementById('btn-adv-rollback').addEventListener('click', runRollback);
      document.getElementById('btn-submit-reconfig').addEventListener('click', () => submitConnectChatGPT(true));
      document.getElementById('btn-adv-revoke').addEventListener('click', runRevokeAccess);
      document.getElementById('btn-adv-estop').addEventListener('click', runEmergencyStopToggle);
      document.getElementById('btn-adv-logout').addEventListener('click', logoutSetupCenter);
      document.getElementById('btn-adv-uninstall').addEventListener('click', () => {
        showEl('btn-adv-uninstall', false);
        showEl('uninstall-confirm-wrap', true);
      });
      document.getElementById('btn-adv-uninstall-cancel').addEventListener('click', () => {
        showEl('uninstall-confirm-wrap', false);
        showEl('btn-adv-uninstall', true);
      });
      document.getElementById('btn-adv-uninstall-confirm').addEventListener('click', runUninstall);

      // A1 Multi-tab CSRF resync on focus / visibilitychange
      window.addEventListener('focus', () => { syncCsrfFromState(); });
      document.addEventListener('visibilitychange', () => {
        if (!document.hidden) syncCsrfFromState();
      });

      applyStaticTranslations();
      syncModeCardSelection();

      // Local Windows-user launcher handoff (#handoff=...)
      const hashStr = window.location.hash || '';
      if (hashStr.startsWith('#handoff=')) {
        const handoffCode = decodeURIComponent(hashStr.slice('#handoff='.length)).trim();
        if (window.history && window.history.replaceState) {
          window.history.replaceState(null, '', window.location.pathname + window.location.search);
        } else {
          window.location.hash = '';
        }
        try {
          const res = await apiCall('/setup/api/local-handoff', 'POST', { handoff_code: handoffCode });
          if (res && res.authenticated) {
            csrfToken = res.csrf_token;
            document.getElementById('login-overlay').classList.add('hidden');
            if (res.state_snapshot) renderState(res.state_snapshot);
            else await refreshSetupState();
            return;
          }
        } catch (e) {
          setText('login-error', e.message);
        }
      }

      try {
        const st = await apiCall('/setup/api/state', 'GET').catch(() => null);
        if (st && st.ok) {
          if (st.csrf_token) csrfToken = st.csrf_token;
          document.getElementById('login-overlay').classList.add('hidden');
          renderState(st);
          return;
        }
      } catch (_) {}
      document.getElementById('login-overlay').classList.remove('hidden');
    });
  </script>
</body>
</html>
"""


def register_setup_center_routes(
    app: FastAPI,
    *,
    gw: Any,
    authenticate_request: Callable[..., tuple[Any, JSONResponse | None]],
    create_handoff_web_session: Callable[..., tuple[Any, bool]] | None = None,
    verify_host_and_origin: Callable[[Request], JSONResponse | None] | None = None,
    session_cookie_name: str = "mcp_console_session",
    session_cookie_ttl_seconds: int = 14400,
    oauth_persistence: Any | None = None,
    oauth_signing_key: str | None = None,
    owner_admin_token: str | None = None,
    db_path: str | Path | None = None,
    adapter_port: int = 8768,
    setup_action_hooks: dict[str, Callable[..., dict[str, Any]]] | None = None,
) -> None:
    """Mount WinOwner Setup Center UI (/setup) and API endpoints (/setup/api/*) on the Web Console app."""
    pers, sk, adm = _resolve_persistence_and_keys(
        gw,
        oauth_persistence=oauth_persistence,
        oauth_signing_key=oauth_signing_key,
        owner_admin_token=owner_admin_token,
        db_path=db_path,
    )
    hooks = dict(setup_action_hooks or {})
    state_lock = threading.Lock()
    handoff_lock = threading.Lock()
    handoff_tickets: dict[str, dict[str, Any]] = {}
    runtime_overrides: dict[str, Any] = {
        "local_service_ready": True,
        "tunnel_info": None,
        "version_state": None,
        "last_action_revoked": False,
        "using_fallback_local_override": False,
        "using_fallback_tunnel_override": False,
    }
    app.state.setup_center_overrides = runtime_overrides
    app.state.setup_center_persistence = pers
    app.state.setup_center_signing_key = sk
    app.state.setup_center_owner_admin_token = adm

    def _issue_local_handoff_ticket(
        *,
        windows_user: str | None = None,
        ttl_seconds: float = 60.0,
    ) -> dict[str, Any]:
        ttl = max(1.0, min(120.0, float(ttl_seconds)))
        now = time.time()
        bound_user = str(windows_user or get_current_windows_user_identity()).strip().lower()
        code = f"hnd_{secrets.token_hex(32)}"
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        expires_at = now + ttl
        with handoff_lock:
            stale = [k for k, v in handoff_tickets.items() if v.get("used") or now >= float(v.get("expires_at", 0.0))]
            for k in stale:
                handoff_tickets.pop(k, None)
            if len(handoff_tickets) >= 32:
                oldest = min(handoff_tickets.keys(), key=lambda k: handoff_tickets[k]["created_at"])
                handoff_tickets.pop(oldest, None)
            handoff_tickets[code_hash] = {
                "windows_user": bound_user,
                "created_at": now,
                "expires_at": expires_at,
                "used": False,
            }
        return {
            "ok": True,
            "handoff_code": code,
            "expires_at": expires_at,
            "ttl_seconds": ttl,
            "windows_user": bound_user,
        }

    app.state.issue_local_handoff_ticket = _issue_local_handoff_ticket

    def _sync_external_adapter_owner_endpoint(path: str, payload: dict[str, Any]) -> None:
        """Best-effort loopback sync to live adapter on adapter_port if running in a separate process."""
        if not adapter_port or not adm:
            return
        try:
            url = f"http://127.0.0.1:{int(adapter_port)}{path}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-MCP-Owner-Admin-Token": adm,
                },
            )
            with urllib.request.urlopen(req, timeout=1.5) as _:
                pass
        except Exception:
            pass

    def _current_snapshot(*, force_live: bool = False) -> dict[str, Any]:
        with state_lock:
            l_ready_ov = bool(runtime_overrides.get("local_service_ready", True))
            t_info_ov = runtime_overrides.get("tunnel_info")
            v_info = runtime_overrides.get("version_state")
            revoked_flag = bool(runtime_overrides.get("last_action_revoked", False))
            fallback_local = bool(runtime_overrides.get("using_fallback_local_override", False))
            fallback_tunnel = bool(runtime_overrides.get("using_fallback_tunnel_override", False))

        # A4/A5 Fix: Live probes are authoritative and never masked by sticky runtime_overrides.
        if "local_service_probe" in hooks and not fallback_local:
            l_ready = bool(hooks["local_service_probe"]())
        elif "local_service_probe" in hooks and force_live:
            l_ready = bool(hooks["local_service_probe"]())
        else:
            l_ready = l_ready_ov

        if "tunnel_probe" in hooks:
            if fallback_tunnel and t_info_ov is not None:
                t_info = t_info_ov
            else:
                t_info = hooks["tunnel_probe"]()
        elif "connect_chatgpt" in hooks and not fallback_tunnel:
            # Real hooks are active: probe live tunnel state instead of serving sticky cached override
            live_t = _default_tunnel_probe(PROJECT_ROOT, port=adapter_port)
            if t_info_ov is not None and t_info_ov.get("explicit_disconnect"):
                t_info = t_info_ov
            else:
                t_info = live_t
        else:
            t_info = t_info_ov

        return compute_setup_center_state(
            gw,
            persistence=pers,
            signing_key=sk,
            port=adapter_port,
            local_service_ready=l_ready,
            tunnel_info=t_info,
            version_state=v_info,
            last_action_revoked=revoked_flag,
        )

    @app.post("/setup/api/local-handoff/issue")
    async def setup_issue_local_handoff(request: Request) -> Response:
        client_host = (request.client.host if request.client else "").strip().lower()
        if client_host and client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return JSONResponse(
                status_code=403,
                content={"ok": False, "error": "NON_LOOPBACK_REJECTED", "error_description": "Local handoff is restricted to loopback (127.0.0.1)."},
            )
        if verify_host_and_origin is not None:
            guard_err = verify_host_and_origin(request)
            if guard_err is not None:
                return guard_err

        caller_secret = str(getattr(gw, "_caller_auth_token", "") or "")
        hdr_admin = (request.headers.get("x-mcp-owner-admin-token") or "").strip()
        if hdr_admin and caller_secret and hmac.compare_digest(hdr_admin.encode("utf-8"), caller_secret.encode("utf-8")):
            gw.policy.audit.log_event(
                tool_name="setup_center_handoff_issue",
                tier="SECURITY",
                decision="DENY",
                reason="Shared tunnel bearer cannot issue local Setup Center handoff tickets",
            )
            return JSONResponse(status_code=403, content={"ok": False, "error": "FORBIDDEN_TUNNEL_CREDENTIAL"})
        if not hdr_admin or not adm or not hmac.compare_digest(hdr_admin.encode("utf-8"), adm.encode("utf-8")):
            return JSONResponse(status_code=401, content={"ok": False, "error": "UNAUTHORIZED_HANDOFF_ISSUER"})

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        try:
            ttl_req = float(body.get("ttl_seconds", 60.0))
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={"ok": False, "error": "INVALID_TTL_SECONDS"})
        if not math.isfinite(ttl_req) or ttl_req <= 0:
            return JSONResponse(status_code=400, content={"ok": False, "error": "INVALID_TTL_SECONDS"})
        req_user = body.get("windows_user")
        ticket = _issue_local_handoff_ticket(windows_user=req_user, ttl_seconds=ttl_req)
        gw.policy.audit.log_event(
            tool_name="setup_center_handoff_issue",
            tier="SECURITY",
            decision="ALLOW",
            reason="Issued single-use current-Windows-user local Setup Center handoff ticket",
            result_summary={"windows_user": ticket["windows_user"], "ttl_seconds": ticket["ttl_seconds"]},
        )
        return JSONResponse(status_code=200, content=ticket)

    @app.post("/setup/api/local-handoff")
    async def setup_exchange_local_handoff(request: Request) -> Response:
        client_host = (request.client.host if request.client else "").strip().lower()
        if client_host and client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            gw.policy.audit.log_event(
                tool_name="setup_center_local_handoff",
                tier="SECURITY",
                decision="DENY",
                reason=f"Rejected non-loopback local handoff exchange from {client_host}",
            )
            return JSONResponse(
                status_code=403,
                content={"ok": False, "error": "NON_LOOPBACK_REJECTED", "error_description": "Local handoff requires loopback (127.0.0.1)."},
            )
        if verify_host_and_origin is not None:
            guard_err = verify_host_and_origin(request)
            if guard_err is not None:
                return guard_err
        if create_handoff_web_session is None:
            return JSONResponse(status_code=503, content={"ok": False, "error": "HANDOFF_UNAVAILABLE"})

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "INVALID_REQUEST"})

        raw_code = str(body.get("handoff_code") or "").strip()
        if not raw_code or not raw_code.startswith("hnd_"):
            return JSONResponse(status_code=401, content={"ok": False, "error": "INVALID_HANDOFF_CODE"})

        code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
        now = time.time()
        server_win_user = get_current_windows_user_identity()
        claimed_win_user = str(body.get("windows_user") or "").strip().lower()

        with handoff_lock:
            rec = handoff_tickets.get(code_hash)
            if rec is None:
                gw.policy.audit.log_event(
                    tool_name="setup_center_local_handoff",
                    tier="SECURITY",
                    decision="DENY",
                    reason="Unknown or missing local handoff code",
                )
                return JSONResponse(status_code=401, content={"ok": False, "error": "INVALID_HANDOFF_CODE"})
            if rec.get("used"):
                gw.policy.audit.log_event(
                    tool_name="setup_center_local_handoff",
                    tier="SECURITY",
                    decision="DENY",
                    reason="Rejected replay of already-used local handoff code",
                )
                return JSONResponse(status_code=409, content={"ok": False, "error": "HANDOFF_ALREADY_USED"})
            if now >= float(rec.get("expires_at", 0.0)):
                rec["used"] = True
                gw.policy.audit.log_event(
                    tool_name="setup_center_local_handoff",
                    tier="SECURITY",
                    decision="DENY",
                    reason="Rejected expired local handoff code",
                )
                return JSONResponse(status_code=401, content={"ok": False, "error": "HANDOFF_EXPIRED"})
            bound_user = str(rec.get("windows_user") or "").strip().lower()
            if bound_user != server_win_user or (claimed_win_user and claimed_win_user != bound_user):
                rec["used"] = True
                gw.policy.audit.log_event(
                    tool_name="setup_center_local_handoff",
                    tier="SECURITY",
                    decision="DENY",
                    reason=f"Rejected local handoff Windows user mismatch (bound={bound_user})",
                )
                return JSONResponse(
                    status_code=403,
                    content={"ok": False, "error": "WINDOWS_USER_MISMATCH", "error_description": "Handoff ticket does not belong to the active Windows owner account."},
                )
            rec["used"] = True

        # A1 Fix: Pass existing session cookie so opening a second tab reuses the valid session & CSRF token.
        existing_cookie = request.cookies.get(session_cookie_name)
        try:
            ws, trust_proxy = create_handoff_web_session(client_host or "127.0.0.1", existing_cookie)
        except TypeError:
            ws, trust_proxy = create_handoff_web_session(client_host or "127.0.0.1")

        is_https = (request.url.scheme == "https") or (
            trust_proxy and request.headers.get("x-forwarded-proto", "").lower() == "https"
        )
        gw.policy.audit.log_event(
            tool_name="setup_center_local_handoff",
            tier="SECURITY",
            decision="ALLOW",
            reason="Authenticated local Windows owner via single-use launcher handoff",
            result_summary={"web_session_id": ws.web_session_id, "windows_user": server_win_user},
        )
        resp = JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "authenticated": True,
                "auth_mode": "local_windows_user_handoff",
                "web_session_id": ws.web_session_id,
                "csrf_token": ws.csrf_token,
                "expires_at": ws.expires_at,
                "state_snapshot": _current_snapshot(),
            },
        )
        resp.set_cookie(
            key=session_cookie_name,
            value=ws.cookie_token,
            httponly=True,
            secure=is_https,
            samesite="strict",
            max_age=session_cookie_ttl_seconds,
            path="/",
        )
        return resp

    @app.get("/setup", response_class=HTMLResponse)
    @app.get("/console/setup", response_class=HTMLResponse)
    async def setup_center_ui() -> Response:
        return HTMLResponse(
            content=SETUP_CENTER_HTML_PAGE,
            status_code=200,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.get("/setup/api/state")
    async def setup_get_state(request: Request) -> Response:
        sess, err_resp = authenticate_request(request, require_csrf=False)
        if err_resp is not None:
            return err_resp
        snap = _current_snapshot(force_live=True)
        if sess is not None and getattr(sess, "csrf_token", None):
            snap["csrf_token"] = sess.csrf_token
        return JSONResponse(status_code=200, content=snap)

    @app.get("/setup/api/logs")
    async def setup_get_logs(request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=False)
        if err_resp is not None:
            return err_resp

        try:
            limit = int(request.query_params.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(200, limit))

        extra_secrets = tuple(
            s
            for s in (
                adm,
                sk,
                str(getattr(gw, "_caller_auth_token", "") or ""),
            )
            if s and len(s) >= 6
        )

        entries: list[dict[str, str]] = []
        audit_obj = getattr(getattr(gw, "policy", None), "audit", None)
        audit_path = getattr(audit_obj, "log_path", None) or getattr(getattr(gw, "policy", None), "audit_log_path", None)
        if audit_path is not None:
            ap = Path(audit_path)
            if ap.is_file():
                try:
                    raw_lines = ap.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in raw_lines[-limit:]:
                        line_s = line.strip()
                        if not line_s:
                            continue
                        try:
                            obj = json.loads(line_s)
                            if isinstance(obj, dict):
                                ts = str(obj.get("timestamp") or obj.get("ts") or "")
                                tool = str(obj.get("tool_name") or obj.get("event") or "audit")
                                dec = str(obj.get("decision") or "INFO")
                                rsn = str(obj.get("reason") or "")
                                msg = _scrub_log_text(f"[{dec}] {tool}: {rsn}".strip(), extra_secrets)
                                entries.append({"timestamp": ts, "source": "audit", "level": dec, "message": msg})
                                continue
                        except Exception:
                            pass
                        entries.append(
                            {
                                "timestamp": "",
                                "source": "audit",
                                "level": "INFO",
                                "message": _scrub_log_text(line_s, extra_secrets),
                            }
                        )
                except Exception:
                    pass

        return JSONResponse(
            status_code=200,
            content={"ok": True, "count": len(entries), "entries": entries[-limit:]},
        )

    @app.get("/setup/api/pairing/challenges")
    async def setup_list_challenges(request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=False)
        if err_resp is not None:
            return err_resp
        status_filter = request.query_params.get("status")
        challenges = pers.list_owner_pairing_challenges(status=status_filter)
        redacted = [
            {
                "challenge_id": c.get("challenge_id"),
                "verification_code": c.get("verification_code"),
                "status": c.get("status"),
                "client_id": c.get("client_id"),
                "client_name": c.get("client_name"),
                "auth_method": c.get("auth_method"),
                "verifiable_identity": bool(c.get("verifiable_identity", False)),
                "requested_permission_level": c.get("requested_permission_level"),
                "requested_tiers": c.get("requested_tiers", []),
                "requested_workspaces": c.get("requested_workspaces", []),
                "requested_ttl_seconds": c.get("requested_ttl_seconds"),
                "allow_destructive": bool(c.get("allow_destructive", False)),
                "created_at": c.get("created_at"),
                "expires_at": c.get("expires_at"),
                "remaining_seconds": c.get("remaining_seconds"),
            }
            for c in challenges
        ]
        pending_cnt = sum(1 for c in redacted if c.get("status") == "PENDING")
        return JSONResponse(
            status_code=200,
            content={"ok": True, "challenges": redacted, "pending_count": pending_cnt},
        )

    @app.post("/setup/api/pairing/challenges/{challenge_id}/approve")
    async def setup_approve_challenge(challenge_id: str, request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=True)
        if err_resp is not None:
            return err_resp
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid JSON body"})

        try:
            res = pers.approve_owner_pairing_challenge(
                challenge_id,
                secret=sk,
                approved_permission_level=body.get("approved_permission_level"),
                approved_tiers=body.get("approved_tiers"),
                approved_workspaces=body.get("approved_workspaces"),
                approved_capabilities=body.get("approved_capabilities"),
                approved_ttl_seconds=(
                    float(body["approved_ttl_seconds"])
                    if body.get("approved_ttl_seconds") is not None
                    else None
                ),
                allow_destructive=(
                    bool(body["allow_destructive"])
                    if body.get("allow_destructive") is not None
                    else None
                ),
                total_operation_budget=int(body.get("total_operation_budget", 5000)),
            )
        except LookupError as exc:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": "not_found", "error_description": str(exc)},
            )
        except PermissionError as exc:
            err_str = str(exc)
            code = (
                "CHALLENGE_EXPIRED"
                if "CHALLENGE_EXPIRED" in err_str
                else ("CHALLENGE_ALREADY_USED" if "CHALLENGE_ALREADY_USED" in err_str else "PAIRING_DENIED")
            )
            gw.policy.audit.log_event(
                tool_name="setup_center_pairing_approve",
                tier="SECURITY",
                decision="DENY",
                reason=err_str,
                arguments={"challenge_id": challenge_id, "error_code": code},
            )
            return JSONResponse(
                status_code=409 if code in ("CHALLENGE_EXPIRED", "CHALLENGE_ALREADY_USED") else 403,
                content={"ok": False, "error": "invalid_grant", "error_code": code, "error_description": err_str},
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "invalid_request", "error_description": str(exc)},
            )

        with state_lock:
            runtime_overrides["last_action_revoked"] = False

        activated_profile = None
        if bool(body.get("activate_profile", False)):
            req_target_prof = str(body.get("target_profile") or "").strip()
            eff_target_prof = req_target_prof or (
                "owner_full_control"
                if str(res.get("approved_permission_level") or "").upper() in ("OWNER", "SCOPED")
                else "read_only"
            )
            if hasattr(pers, "set_owner_approved_profile"):
                pers.set_owner_approved_profile(
                    secret=sk,
                    profile=eff_target_prof,
                    approved_by="local_owner",
                    delegation_id=str(res.get("delegation_id") or ""),
                    client_id=str(res.get("client_id") or ""),
                )
            if not (hasattr(gw, "is_emergency_stop_active") and gw.is_emergency_stop_active()):
                if hasattr(gw, "switch_policy_profile"):
                    gw.switch_policy_profile(eff_target_prof, reason="setup_center_single_button_confirm")
                approved_ws = body.get("approved_workspaces") or res.get("approved_workspaces") or []
                if isinstance(approved_ws, list) and getattr(gw, "policy", None) is not None:
                    for ws_item in approved_ws:
                        try:
                            ws_p = Path(str(ws_item)).resolve()
                            if ws_p.is_dir() and not gw.policy.is_sensitive_path_str(ws_p, check_content=False):
                                for attr in ("read_roots", "write_roots", "exec_roots"):
                                    roots_list = list(getattr(gw.policy, attr, []) or [])
                                    if ws_p not in roots_list:
                                        setattr(gw.policy, attr, [*roots_list, ws_p])
                        except Exception:
                            continue
                await asyncio.to_thread(
                    _sync_external_adapter_owner_endpoint,
                    "/oauth/owner/profile",
                    {"profile": eff_target_prof, "reason": "setup_center_single_button_confirm"},
                )
            activated_profile = eff_target_prof

        gw.policy.audit.log_event(
            tool_name="setup_center_pairing_approve",
            tier="SECURITY",
            decision="ALLOW",
            reason=f"Owner approved verifiable pairing challenge {challenge_id} in WinOwner Setup Center",
            result_summary={
                "challenge_id": challenge_id,
                "client_id": res.get("client_id"),
                "delegation_id": res.get("delegation_id"),
                "capability_grant_id": res.get("capability_grant_id"),
                "approved_permission_level": res.get("approved_permission_level"),
                "activated_profile": activated_profile,
            },
        )
        out_payload = {**res, "state_snapshot": _current_snapshot()}
        if activated_profile is not None:
            out_payload["activated_profile"] = activated_profile
        return JSONResponse(status_code=200, content=out_payload)

    @app.post("/setup/api/pairing/challenges/{challenge_id}/deny")
    async def setup_deny_challenge(challenge_id: str, request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=True)
        if err_resp is not None:
            return err_resp
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid JSON body"})
        reason = str(body.get("reason") or "denied_by_owner_in_setup_center")
        try:
            res = pers.deny_owner_pairing_challenge(challenge_id, secret=sk, reason=reason)
        except LookupError as exc:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": "not_found", "error_description": str(exc)},
            )
        except PermissionError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": "invalid_grant",
                    "error_code": "CHALLENGE_ALREADY_USED",
                    "error_description": str(exc),
                },
            )
        gw.policy.audit.log_event(
            tool_name="setup_center_pairing_deny",
            tier="SECURITY",
            decision="DENY",
            reason=f"Owner denied pairing challenge {challenge_id}: {reason}",
            result_summary={"challenge_id": challenge_id, "reason": reason},
        )
        return JSONResponse(status_code=200, content={**res, "state_snapshot": _current_snapshot()})

    # ------------------------------------------------------------------
    # Action 1: Install / Repair
    # ------------------------------------------------------------------
    @app.post("/setup/api/actions/install-repair")
    async def setup_action_install_repair(request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=True)
        if err_resp is not None:
            return err_resp
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        if "install_repair" in hooks:
            try:
                hook_res = await asyncio.to_thread(hooks["install_repair"], body)
            except Exception as exc:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": "install_repair_failed", "error_description": redact_secrets(str(exc))},
                )
            with state_lock:
                runtime_overrides["local_service_ready"] = bool(hook_res.get("local_service_ready", True))
                runtime_overrides["using_fallback_local_override"] = False
        else:
            hook_res = {
                "action": "install_repair",
                "status": "LOCAL_INSTALL_PASS",
                "profile": str(getattr(gw.policy, "profile", "read_only")),
                "message_en": "Local MCP service health and HTTP 401 authentication boundary verified.",
                "message_vi": "Đã xác minh dịch vụ MCP cục bộ và ranh giới chặn truy cập ẩn danh HTTP 401.",
            }
            with state_lock:
                runtime_overrides["local_service_ready"] = True
                runtime_overrides["using_fallback_local_override"] = True

        gw.policy.audit.log_event(
            tool_name="setup_center_install_repair",
            tier="SECURITY",
            decision="ALLOW",
            reason="Executed Install / Repair check from WinOwner Setup Center",
            result_summary=redact_secrets(hook_res),
        )
        return JSONResponse(
            status_code=200,
            content={"ok": True, "summary": redact_secrets(hook_res), "state_snapshot": _current_snapshot(force_live=True)},
        )

    # ------------------------------------------------------------------
    # Action 2 & 6: Connect ChatGPT / Reconfigure Tunnel
    # ------------------------------------------------------------------
    async def _handle_tunnel_connect_or_reconfig(request: Request, *, is_reconfigure: bool) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=True)
        if err_resp is not None:
            return err_resp
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid JSON body"})

        tunnel_id = str(body.get("tunnel_id") or "").strip().lower()
        runtime_api_key = str(body.get("runtime_api_key") or "").strip()
        auth_mode = str(body.get("auth_mode") or "auth_none").strip().lower()
        if auth_mode not in ("oauth", "auth_none"):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "invalid_auth_mode", "error_description": "auth_mode must be 'oauth' or 'auth_none'."},
            )
        body.pop("runtime_api_key", None)

        if not TUNNEL_ID_REGEX.match(tunnel_id):
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_tunnel_id",
                    "error_description": "Invalid OpenAI Tunnel ID format (expected tunnel_<32 hex> or tunnel_<4>_<32>).",
                },
            )
        if len(runtime_api_key) < 20:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_runtime_api_key",
                    "error_description": "Runtime API Key is missing or unexpectedly short.",
                },
            )

        with state_lock:
            prev_tunnel = runtime_overrides.get("tunnel_info") or {}
        effective_reconfigure = bool(is_reconfigure or prev_tunnel.get("configured", False))

        hook_key = "reconfigure_tunnel" if is_reconfigure else "connect_chatgpt"
        if hook_key in hooks:
            try:
                hook_res = await asyncio.to_thread(
                    hooks[hook_key],
                    {
                        "tunnel_id": tunnel_id,
                        "runtime_api_key": runtime_api_key,
                        "reconfigure": effective_reconfigure,
                        "auth_mode": auth_mode,
                    },
                )
            except Exception as exc:
                runtime_api_key = ""
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": "tunnel_setup_failed", "error_description": redact_secrets(str(exc))},
                )
            used_fallback = False
        else:
            hook_res = {
                "action": hook_key,
                "tunnel_id": tunnel_id,
                "auth_mode": auth_mode,
                "auto_reconfigure": effective_reconfigure,
                "status": "TUNNEL_SETUP_PASS",
                "readyz_ok": True,
                "chatgpt_tool_call_verified": False,
                "message_en": "Tunnel runtime ready; ChatGPT tool call NOT verified yet. Run a read-only prompt in ChatGPT next.",
                "message_vi": "Tunnel đã sẵn sàng; chưa xác minh lệnh gọi công cụ từ ChatGPT. Hãy chạy câu lệnh đọc thử trong ChatGPT.",
            }
            used_fallback = True
        runtime_api_key = ""

        if is_reconfigure and not hook_res.get("rolled_back", False):
            try:
                with pers._db() as conn:
                    conn.execute("DELETE FROM chatgpt_connection_state WHERE id = 1")
            except Exception:
                pass

        with state_lock:
            if used_fallback:
                runtime_overrides["tunnel_info"] = {
                    "connected": bool(hook_res.get("readyz_ok", True)),
                    "configured": True,
                    "readyz_ok": bool(hook_res.get("readyz_ok", True)),
                    "status_code": str(hook_res.get("status") or "TUNNEL_READY"),
                }
                runtime_overrides["using_fallback_tunnel_override"] = True
            else:
                # Real hook active: clear sticky override so live probe reflects true tunnel status
                runtime_overrides["tunnel_info"] = None
                runtime_overrides["using_fallback_tunnel_override"] = False

        safe_summary = redact_secrets({k: v for k, v in hook_res.items() if k != "runtime_api_key"})
        gw.policy.audit.log_event(
            tool_name=f"setup_center_{hook_key}",
            tier="SECURITY",
            decision="ALLOW",
            reason=f"Configured private tunnel ({tunnel_id}) via WinOwner Setup Center (reconfigure={is_reconfigure})",
            result_summary=safe_summary,
        )
        return JSONResponse(
            status_code=200,
            content={"ok": True, "summary": safe_summary, "state_snapshot": _current_snapshot(force_live=True)},
        )

    @app.post("/setup/api/actions/connect-chatgpt")
    async def setup_action_connect_chatgpt(request: Request) -> Response:
        return await _handle_tunnel_connect_or_reconfig(request, is_reconfigure=False)

    @app.post("/setup/api/actions/reconfigure-tunnel")
    async def setup_action_reconfigure_tunnel(request: Request) -> Response:
        return await _handle_tunnel_connect_or_reconfig(request, is_reconfigure=True)

    # ------------------------------------------------------------------
    # Action 3: Verify Connection (Never Fakes CHATGPT_CONNECTED!)
    # ------------------------------------------------------------------
    @app.post("/setup/api/actions/verify-connection")
    async def setup_action_verify_connection(request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=True)
        if err_resp is not None:
            return err_resp

        snap = _current_snapshot(force_live=True)
        cg = snap["chatgpt_connection"]
        verified = bool(cg["verified_read_tool_call"])
        summary = {
            "action": "verify_connection",
            "local_service_ready": snap["status_table"]["local_service"] == "Ready",
            "tunnel_connected": snap["status_table"]["tunnel"] == "Connected",
            "chatgpt_verified": verified,
            "state": snap["state"],
            "last_verified_tool": cg.get("last_verified_tool"),
            "last_verified_at": cg.get("last_verified_at"),
            "verification_prompt_en": cg.get("verification_prompt_en"),
            "verification_prompt_vi": cg.get("verification_prompt_vi"),
            "message_en": (
                f"ChatGPT connection verified via real read-only tool call ({cg.get('last_verified_tool')})."
                if verified
                else "Local service/tunnel checked, but NO real read-only MCP tool call from ChatGPT has been observed yet. Run the verification prompt in ChatGPT and click Verify Connection again."
            ),
            "message_vi": (
                f"Đã xác minh kết nối ChatGPT qua lệnh gọi công cụ đọc thật ({cg.get('last_verified_tool')})."
                if verified
                else "Dịch vụ cục bộ/tunnel đã kiểm tra, nhưng CHƯA ghi nhận lệnh gọi công cụ đọc thật nào từ ChatGPT. Hãy chạy câu lệnh thử trong ChatGPT rồi nhấn Xác minh kết nối lại."
            ),
        }
        return JSONResponse(status_code=200, content={"ok": True, "summary": summary, "state_snapshot": snap})

    # ------------------------------------------------------------------
    # Action 5: Revoke Access (+ Emergency Stop control)
    # ------------------------------------------------------------------
    @app.post("/setup/api/actions/revoke-access")
    async def setup_action_revoke_access(request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=True)
        if err_resp is not None:
            return err_resp
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        delegation_id = str(body.get("delegation_id") or "").strip() or None
        grant_id = str(body.get("grant_id") or "").strip() or None
        client_id = str(body.get("client_id") or "").strip() or None
        revoke_all = bool(body.get("revoke_all", not (delegation_id or grant_id or client_id)))
        reason = str(body.get("reason") or "revoked_by_owner_in_setup_center")

        rev_res = pers.revoke_owner_access(
            delegation_id=delegation_id,
            grant_id=grant_id,
            client_id=client_id,
            revoke_all=revoke_all,
            reason=reason,
        )
        active_revs = pers.load_active_revocations()
        try:
            gw._revoked_oauth_tokens = active_revs
        except Exception:
            pass

        if revoke_all and getattr(gw, "policy", None) is not None and hasattr(gw.policy, "sessions"):
            try:
                gw.policy.sessions.revoke_all(reason=reason)
            except Exception:
                pass

        remaining_dels = pers.list_active_owner_delegations(secret=sk, owner_id="local_owner") if hasattr(pers, "list_active_owner_delegations") else []
        remaining_grants = pers.count_active_owner_grants(owner_id="local_owner") if hasattr(pers, "count_active_owner_grants") else 0
        if (revoke_all or (len(remaining_dels) == 0 and remaining_grants <= 0)) and not bool(body.get("reset_emergency_stop", False)):
            if hasattr(pers, "clear_owner_approved_profile"):
                try:
                    pers.clear_owner_approved_profile(secret=sk, reason=reason)
                except Exception:
                    pass
            if hasattr(gw, "switch_policy_profile"):
                try:
                    gw.switch_policy_profile("read_only", reason=reason)
                except Exception:
                    pass
            await asyncio.to_thread(
                _sync_external_adapter_owner_endpoint,
                "/oauth/owner/revoke",
                {"revoke_all": revoke_all, "delegation_id": delegation_id, "grant_id": grant_id, "client_id": client_id, "reason": reason},
            )

        if bool(body.get("engage_emergency_stop", False)):
            gw.engage_emergency_stop(reason=reason)
            await asyncio.to_thread(_sync_external_adapter_owner_endpoint, "/oauth/owner/emergency-stop", {"reason": reason})
        elif bool(body.get("reset_emergency_stop", False)):
            gw.reset_emergency_stop()
            await asyncio.to_thread(_sync_external_adapter_owner_endpoint, "/oauth/owner/emergency-stop/reset", {"reason": reason})
        elif revoke_all or delegation_id or grant_id or client_id:
            with state_lock:
                runtime_overrides["last_action_revoked"] = True

        gw.policy.audit.log_event(
            tool_name="setup_center_revoke_access",
            tier="SECURITY",
            decision="ALLOW",
            reason=f"Owner revoked access via Setup Center (revoke_all={revoke_all})",
            result_summary=rev_res,
        )
        return JSONResponse(
            status_code=200,
            content={"ok": True, "summary": rev_res, "state_snapshot": _current_snapshot()},
        )

    # ------------------------------------------------------------------
    # Action 7: Check for Updates (Verified GitHub Releases Only)
    # ------------------------------------------------------------------
    @app.post("/setup/api/actions/check-updates")
    async def setup_action_check_updates(request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=True)
        if err_resp is not None:
            return err_resp
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        from runtime.release_identity import get_release_identity

        rel_id = get_release_identity()
        current_ver = str(rel_id.get("release_version") or "v1.2.0")

        candidate_ver = str(body.get("candidate_version") or "").strip()
        if candidate_ver and _parse_semver_tuple(candidate_ver) is None:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_release_version",
                    "error_description": "Updates strictly require a verified semantic version release tag (vX.Y.Z); arbitrary branch refs (main/HEAD) are prohibited.",
                },
            )

        if "check_updates" in hooks:
            try:
                hook_res = await asyncio.to_thread(hooks["check_updates"], body)
            except Exception as exc:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": "check_updates_failed", "error_description": redact_secrets(str(exc))},
                )
            latest_ver = str(hook_res.get("latest_version") or candidate_ver or current_ver)
        else:
            latest_ver = candidate_ver or current_ver
            hook_res = {
                "current_version": current_ver,
                "latest_version": latest_ver,
                "update_available": _is_newer_semver(latest_ver, current_ver),
                "verified_release_only": True,
                "sha256_verification_required": True,
            }

        if _parse_semver_tuple(latest_ver) is None:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "invalid_release_version",
                    "error_description": "Refusing non-semver release reference from update source.",
                },
            )

        is_newer = _is_newer_semver(latest_ver, current_ver)
        with state_lock:
            runtime_overrides["version_state"] = {
                "current_version": current_ver,
                "latest_version": latest_ver,
                "update_available": is_newer,
            }

        snap = _current_snapshot()
        return JSONResponse(
            status_code=200,
            content={"ok": True, "summary": redact_secrets(hook_res), "state_snapshot": snap},
        )

    # ------------------------------------------------------------------
    # Action 9 (Phase D / A5): Rollback (Verified Backup Snapshots Only)
    # ------------------------------------------------------------------
    @app.post("/setup/api/actions/rollback")
    async def setup_action_rollback(request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=True)
        if err_resp is not None:
            return err_resp
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid JSON body"})

        if "rollback" not in hooks:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "no_rollback_snapshot",
                    "error_description": "No verified rollback hook or backup snapshot is available.",
                },
            )

        try:
            hook_res = await asyncio.to_thread(hooks["rollback"], body)
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "rollback_failed", "error_description": redact_secrets(str(exc))},
            )

        with state_lock:
            runtime_overrides["tunnel_info"] = None
            runtime_overrides["using_fallback_tunnel_override"] = False

        safe_summary = redact_secrets(hook_res)
        gw.policy.audit.log_event(
            tool_name="setup_center_rollback",
            tier="SECURITY",
            decision="ALLOW",
            reason="Executed verified rollback from WinOwner Setup Center",
            result_summary=safe_summary,
        )
        return JSONResponse(
            status_code=200,
            content={"ok": True, "summary": safe_summary, "state_snapshot": _current_snapshot(force_live=True)},
        )

    # ------------------------------------------------------------------
    # Action 8: Uninstall (Verified Tasks Only, Preserves User Secrets)
    # ------------------------------------------------------------------
    @app.post("/setup/api/actions/uninstall")
    async def setup_action_uninstall(request: Request) -> Response:
        _, err_resp = authenticate_request(request, require_csrf=True)
        if err_resp is not None:
            return err_resp
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict) or body.get("confirm") is not True:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "confirmation_required",
                    "error_description": "Explicit confirmation ({'confirm': true}) is required before uninstalling local services.",
                },
            )

        if "uninstall" in hooks:
            try:
                hook_res = await asyncio.to_thread(hooks["uninstall"], body)
            except Exception as exc:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": "uninstall_failed", "error_description": redact_secrets(str(exc))},
                )
            used_fallback = False
        else:
            hook_res = {
                "action": "uninstall",
                "status": "SERVICE_AND_TUNNEL_STOPPED",
                "secrets_preserved": True,
                "message_en": "Verified local MCP service and tunnel tasks stopped and unregistered. Local user data and keys are preserved.",
                "message_vi": "Đã dừng và gỡ tác vụ MCP cục bộ cùng tunnel đã xác minh. Dữ liệu và khóa riêng trên máy vẫn được giữ an toàn.",
            }
            used_fallback = True

        with state_lock:
            runtime_overrides["local_service_ready"] = False
            runtime_overrides["using_fallback_local_override"] = used_fallback
            runtime_overrides["tunnel_info"] = {
                "connected": False,
                "configured": False,
                "readyz_ok": False,
                "explicit_disconnect": True,
                "status_code": "TUNNEL_TASK_REMOVED",
            }
            runtime_overrides["using_fallback_tunnel_override"] = used_fallback

        gw.policy.audit.log_event(
            tool_name="setup_center_uninstall",
            tier="SECURITY",
            decision="ALLOW",
            reason="Owner triggered verified uninstall from WinOwner Setup Center",
            result_summary=redact_secrets(hook_res),
        )
        return JSONResponse(
            status_code=200,
            content={"ok": True, "summary": redact_secrets(hook_res), "state_snapshot": _current_snapshot()},
        )
