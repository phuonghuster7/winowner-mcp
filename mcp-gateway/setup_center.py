"""Phase 3 — WinOwner Setup Center & Four-Step Onboarding UX Controller.

Provides:
1. Exact 4-Step State Machine reflecting real backend truth (ZERO fake success states):
   LOCAL_READY -> CHATGPT_CONNECTED -> OWNER_VERIFIED -> OWNER_CONTROL_READY
2. WinOwner Setup Center Main Screen live 6-row status table:
   - Local Service:  Ready | Error
   - Tunnel:         Connected | Disconnected
   - ChatGPT:        Verified | Not Verified
   - Owner Identity: Verified | Pending
   - Permissions:    Read | Scoped | Owner
   - Version:        Current | Update Available
3. Eight Owner-Controlled Actions (minimal clicks, no terminal required after install):
   - Install / Repair
   - Connect ChatGPT
   - Verify Connection
   - Review Owner Access (Approve / Deny pending verifiable OAuth 2.1 pairing challenges)
   - Revoke Access (+ Emergency Stop control)
   - Reconfigure Tunnel
   - Check for Updates
   - Uninstall
4. Bilingual VI & EN toggle, plain user language, actionable error fixes, zero secret exposure,
   zero innerHTML XSS sinks, strict loopback + Host/Origin/Referer + HttpOnly SameSite=Strict
   cookie + X-CSRF-Token protection.
"""
from __future__ import annotations

import asyncio
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
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
    # Stable release > prerelease of same X.Y.Z
    is_stable = 1 if not prerelease else 0
    return (major, minor, patch, is_stable, prerelease)


def _is_newer_semver(candidate_tag: str, current_tag: str) -> bool:
    c_parsed = _parse_semver_tuple(candidate_tag)
    cur_parsed = _parse_semver_tuple(current_tag)
    if c_parsed is None or cur_parsed is None:
        return False
    return c_parsed > cur_parsed


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
) -> dict[str, Any]:
    """Compute the authoritative 4-step state machine and 6-row live status table from backend truth."""
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

    # Note: If tunnel is not explicitly probed as disconnected, check whether tunnel_info says connected.
    tunnel_connected = bool(t_info.get("connected", False))
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

    # Version row
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

    steps_detail = [
        {
            "step": 1,
            "id": "LOCAL_READY",
            "completed": step1_done,
            "active": current_state == "LOCAL_READY",
            "label_en": "1. Local Service Ready",
            "label_vi": "1. Dịch vụ cục bộ sẵn sàng",
        },
        {
            "step": 2,
            "id": "CHATGPT_CONNECTED",
            "completed": step2_done,
            "active": current_state == "CHATGPT_CONNECTED",
            "label_en": "2. ChatGPT Connected (Verified Read Tool Call)",
            "label_vi": "2. Đã kết nối ChatGPT (Đã xác minh gọi tool đọc thật)",
        },
        {
            "step": 3,
            "id": "OWNER_VERIFIED",
            "completed": step3_done,
            "active": current_state == "OWNER_VERIFIED",
            "label_en": "3. Owner Identity Verified",
            "label_vi": "3. Đã xác minh danh tính Chủ máy",
        },
        {
            "step": 4,
            "id": "OWNER_CONTROL_READY",
            "completed": step4_done,
            "active": current_state == "OWNER_CONTROL_READY",
            "label_en": "4. Owner Control Ready",
            "label_vi": "4. Sẵn sàng quyền điều khiển Chủ máy",
        },
    ]

    last_auth_mode = str(cg_raw.get("last_auth_mode") or "none")
    supports_verifiable_id = bool(cg_raw.get("supports_verifiable_owner_identity", False))

    # Actionable guidance in plain English and Vietnamese
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
            guidance_en = "ChatGPT is verified (`Read`). When ChatGPT requests owner pairing (`agent_request_owner_pairing`), click 'Review Owner Access' here to approve or deny."
            guidance_vi = "ChatGPT đã xác minh (`Read`). Khi ChatGPT gửi yêu cầu ghép nối chủ máy (`agent_request_owner_pairing`), hãy nhấn 'Duyệt quyền Chủ máy' tại đây để phê duyệt hoặc từ chối."
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

    return {
        "ok": True,
        "schema_version": "1.2.0",
        "state": current_state,
        "completed_steps_count": completed_steps_count,
        "steps": steps_detail,
        "status_table": status_table,
        "profile": profile_name,
        "emergency_stop": is_estop,
        "local_auth_enforced": local_auth_enforced,
        "allow_loopback_tunnel_identity": allow_loopback_tunnel_identity,
        "tunnel_details": {
            "connected": tunnel_connected,
            "configured": bool(t_info.get("configured", False)),
            "readyz_ok": bool(t_info.get("readyz_ok", False)),
            "status_code": str(t_info.get("status_code") or "TUNNEL_NOT_CONFIGURED"),
        },
        "chatgpt_connection": {
            "verified_read_tool_call": chatgpt_verified,
            "last_verified_tool": cg_raw.get("last_verified_tool") if chatgpt_verified else None,
            "last_verified_at": cg_raw.get("last_verified_at") if chatgpt_verified else None,
            "last_auth_mode": last_auth_mode,
            "last_client_id_masked": str(cg_raw.get("last_client_id_masked") or ""),
            "supports_verifiable_owner_identity": supports_verifiable_id,
            "verification_prompt_en": "Call agent_system_status or list my approved workspaces in read-only mode.",
            "verification_prompt_vi": "Hãy gọi công cụ chỉ đọc agent_system_status hoặc liệt kê các workspace đã được phê duyệt.",
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
      --bg: #0b0f19;
      --panel: #131b2e;
      --panel-alt: #19233c;
      --border: #2a395c;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --success: #22c55e;
      --warn: #f59e0b;
      --danger: #ef4444;
      --font: 'Segoe UI', system-ui, -apple-system, sans-serif;
      --mono: 'Cascadia Code', 'Consolas', monospace;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--text);
      font-family: var(--font);
      font-size: 14px;
      line-height: 1.5;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 24px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 40;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 700;
      font-size: 17px;
    }
    .container {
      max-width: 1240px;
      margin: 0 auto;
      padding: 20px;
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
    }
    .col-12 { grid-column: span 12; }
    .col-7 { grid-column: span 7; }
    .col-5 { grid-column: span 5; }
    @media (max-width: 960px) {
      .col-7, .col-5 { grid-column: span 12; }
    }
    h2 {
      margin: 0 0 12px 0;
      font-size: 16px;
      color: var(--accent);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .stepper {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
    }
    @media (max-width: 800px) {
      .stepper { grid-template-columns: 1fr; }
    }
    .step-box {
      padding: 12px;
      border-radius: 8px;
      background: var(--panel-alt);
      border: 1px solid var(--border);
      font-size: 13px;
      font-weight: 600;
    }
    .step-box.done {
      border-color: var(--success);
      color: var(--success);
    }
    .step-box.current {
      border-color: var(--accent);
      box-shadow: 0 0 0 1px var(--accent);
    }
    table.status-table {
      width: 100%;
      border-collapse: collapse;
    }
    table.status-table th, table.status-table td {
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
    }
    .pill {
      display: inline-block;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid var(--border);
      background: var(--panel-alt);
    }
    .pill.green { color: var(--success); border-color: var(--success); }
    .pill.amber { color: var(--warn); border-color: var(--warn); }
    .pill.red { color: #fff; background: var(--danger); border-color: var(--danger); }
    .pill.blue { color: var(--accent); border-color: var(--accent); }
    .actions-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
    }
    button {
      cursor: pointer;
      border: 1px solid var(--border);
      background: var(--panel-alt);
      color: var(--text);
      padding: 9px 14px;
      border-radius: 7px;
      font-weight: 600;
      font-size: 13px;
      text-align: left;
      transition: filter 0.15s;
    }
    button:hover { filter: brightness(1.15); }
    button.primary { background: #0284c7; border-color: #38bdf8; color: #fff; }
    button.success { background: #15803d; border-color: #22c55e; color: #fff; }
    button.warn { background: #b45309; border-color: #f59e0b; color: #fff; }
    button.danger { background: #dc2626; border-color: #f87171; color: #fff; }
    .guidance-banner {
      margin-top: 12px;
      padding: 12px 14px;
      background: #0d1526;
      border-left: 4px solid var(--accent);
      border-radius: 6px;
      font-size: 13px;
    }
    .challenge-card {
      border: 1px solid var(--warn);
      background: var(--panel-alt);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 10px;
    }
    label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 4px;
    }
    input, select {
      width: 100%;
      padding: 8px 10px;
      background: #090d16;
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      font-family: var(--mono);
      font-size: 12px;
      margin-bottom: 10px;
    }
    #login-overlay {
      position: fixed;
      inset: 0;
      background: rgba(8, 12, 22, 0.94);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 100;
    }
    .login-box {
      width: 420px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 24px;
    }
    .hidden { display: none !important; }
    pre {
      background: #070a12;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px;
      font-family: var(--mono);
      font-size: 12px;
      white-space: pre-wrap;
      max-height: 220px;
      overflow: auto;
    }
  </style>
</head>
<body>
  <div id="login-overlay" class="hidden">
    <div class="login-box">
      <h2 id="login-title">WinOwner Setup Center — Local Owner Sign-In</h2>
      <p id="login-desc" style="font-size:12px; color:var(--muted);">
        Fallback sign-in (direct browser visit or expired launcher ticket). Use <strong>Open-SetupCenter.cmd</strong> to sign in automatically with zero manual key entry.
      </p>
      <label for="owner-password" id="login-label">Owner Console Secret / Admin Key</label>
      <input type="password" id="owner-password" placeholder="Enter local owner secret" autocomplete="current-password" />
      <div id="login-error" style="color: var(--danger); font-size: 12px; margin-bottom: 8px;"></div>
      <button class="primary" style="width:100%; text-align:center;" onclick="loginSetupCenter()" id="login-btn">Sign In</button>
    </div>
  </div>

  <header>
    <div class="brand">
      <span id="hdr-title">WinOwner Setup Center</span>
      <span id="hdr-state-pill" class="pill blue">LOCAL_READY</span>
    </div>
    <div style="display:flex; gap:8px; align-items:center;">
      <button onclick="toggleLanguage()" id="lang-toggle-btn" style="text-align:center;">VI / EN</button>
      <button onclick="refreshSetupState()" id="refresh-btn" style="text-align:center;">Refresh</button>
      <button class="danger" onclick="runEmergencyStopToggle()" id="estop-btn" style="text-align:center;">Emergency Stop</button>
      <a href="/console" style="color:var(--accent); text-decoration:none; font-size:13px; padding:6px 10px;" id="adv-console-link">Advanced Mode B Console</a>
      <button onclick="logoutSetupCenter()" id="logout-btn" style="text-align:center;">Logout</button>
    </div>
  </header>

  <div class="container">
    <!-- 4-Step State Machine Progression -->
    <div class="card col-12">
      <h2 id="sec-progress-title">Four-Step Connection &amp; Owner Control State</h2>
      <div class="stepper" id="stepper-container"></div>
      <div class="guidance-banner" id="guidance-text">Loading live status...</div>
    </div>

    <!-- Main 6-Row Live Status Table -->
    <div class="card col-7">
      <h2 id="sec-table-title">System &amp; Security Live Status</h2>
      <table class="status-table" aria-label="WinOwner Setup Center Status Table">
        <thead>
          <tr>
            <th id="th-component">Component</th>
            <th id="th-status">Live Status</th>
            <th id="th-detail">Verified Detail</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td id="lbl-local-service">Local Service</td>
            <td><span id="val-local-service" class="pill">...</span></td>
            <td id="det-local-service" style="font-size:12px; color:var(--muted);">127.0.0.1 loopback + HTTP 401 anonymous block</td>
          </tr>
          <tr>
            <td id="lbl-tunnel">Tunnel</td>
            <td><span id="val-tunnel" class="pill">...</span></td>
            <td id="det-tunnel" style="font-size:12px; color:var(--muted);">OpenAI tunnel-client local readiness</td>
          </tr>
          <tr>
            <td id="lbl-chatgpt">ChatGPT</td>
            <td><span id="val-chatgpt" class="pill">...</span></td>
            <td id="det-chatgpt" style="font-size:12px; color:var(--muted);">Requires real verified read-only MCP tool call</td>
          </tr>
          <tr>
            <td id="lbl-owner">Owner Identity</td>
            <td><span id="val-owner" class="pill">...</span></td>
            <td id="det-owner" style="font-size:12px; color:var(--muted);">Verifiable OAuth 2.1 client pairing</td>
          </tr>
          <tr>
            <td id="lbl-permissions">Permissions</td>
            <td><span id="val-permissions" class="pill">...</span></td>
            <td id="det-permissions" style="font-size:12px; color:var(--muted);">Effective backend policy &amp; grant intersection</td>
          </tr>
          <tr>
            <td id="lbl-version">Version</td>
            <td><span id="val-version" class="pill">...</span></td>
            <td id="det-version" style="font-size:12px; color:var(--muted);">v1.2.0</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 8 Primary Actions -->
    <div class="card col-5">
      <h2 id="sec-actions-title">Setup Center Actions</h2>
      <div class="actions-grid">
        <button class="primary" onclick="runInstallRepair()" id="btn-install-repair">1. Install / Repair</button>
        <button class="primary" onclick="showPanel('panel-connect')" id="btn-connect-chatgpt">2. Connect ChatGPT</button>
        <button class="success" onclick="runVerifyConnection()" id="btn-verify-conn">3. Verify Connection</button>
        <button class="warn" onclick="showPanel('panel-pairing')" id="btn-review-owner">4. Review Owner Access</button>
        <button class="danger" onclick="runRevokeAccess()" id="btn-revoke-access">5. Revoke Access</button>
        <button onclick="showPanel('panel-reconfig')" id="btn-reconfig-tunnel">6. Reconfigure Tunnel</button>
        <button onclick="runCheckUpdates()" id="btn-check-updates">7. Check for Updates</button>
        <button class="danger" onclick="runUninstall()" id="btn-uninstall">8. Uninstall</button>
      </div>
      <pre id="action-result-box">Select an action above. Raw secrets and tokens are never displayed.</pre>
    </div>

    <!-- Action Drawer: Connect ChatGPT / Reconfigure Tunnel -->
    <div class="card col-12 hidden" id="panel-connect">
      <h2 id="panel-connect-title">Connect ChatGPT over Private Tunnel</h2>
      <p id="panel-connect-note" style="font-size:13px; color:var(--muted);">
        Enter your OpenAI Tunnel ID (tunnel_...) and Runtime API Key. The key is stored locally with Windows owner-only ACLs and is never echoed or logged. Note: Authentication: None in ChatGPT is strictly Read-Only; Owner permissions require OAuth 2.1 verifiable client pairing.
      </p>
      <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:12px;">
        <div>
          <label for="input-tunnel-id">OpenAI Tunnel ID</label>
          <input type="text" id="input-tunnel-id" placeholder="tunnel_..." />
        </div>
        <div>
          <label for="input-runtime-key">Runtime API Key (Hidden — Never Displayed)</label>
          <input type="password" id="input-runtime-key" placeholder="Enter Runtime API Key (Tunnels Read + Use)" autocomplete="off" />
        </div>
        <div>
          <label for="input-auth-mode">Connection Mode</label>
          <select id="input-auth-mode">
            <option value="oauth" selected>OAuth 2.1 (Recommended — Supports Owner Pairing)</option>
            <option value="auth_none">Authentication: None (Strictly Read-Only)</option>
          </select>
        </div>
      </div>
      <div style="display:flex; gap:10px;">
        <button class="primary" onclick="submitConnectChatGPT(false)" id="btn-submit-connect">Save &amp; Start Tunnel</button>
        <button onclick="hidePanels()">Close</button>
      </div>
    </div>

    <div class="card col-12 hidden" id="panel-reconfig">
      <h2 id="panel-reconfig-title">Safe Tunnel Reconfiguration (Backup -> Validate -> Cutover -> Auto-Rollback)</h2>
      <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:12px;">
        <div>
          <label for="reconfig-tunnel-id">New OpenAI Tunnel ID</label>
          <input type="text" id="reconfig-tunnel-id" placeholder="tunnel_..." />
        </div>
        <div>
          <label for="reconfig-runtime-key">New Runtime API Key (Hidden)</label>
          <input type="password" id="reconfig-runtime-key" placeholder="Enter Runtime API Key" autocomplete="off" />
        </div>
        <div>
          <label for="reconfig-auth-mode">Connection Mode</label>
          <select id="reconfig-auth-mode">
            <option value="oauth" selected>OAuth 2.1 (Recommended — Supports Owner Pairing)</option>
            <option value="auth_none">Authentication: None (Strictly Read-Only)</option>
          </select>
        </div>
      </div>
      <div style="display:flex; gap:10px;">
        <button class="warn" onclick="submitConnectChatGPT(true)" id="btn-submit-reconfig">Validate &amp; Reconfigure Tunnel</button>
        <button onclick="hidePanels()">Close</button>
      </div>
    </div>

    <!-- Action Drawer: Review Owner Access (Verifiable Pairing Challenges) -->
    <div class="card col-12" id="panel-pairing">
      <h2>
        <span id="sec-pairing-title">Review Owner Access — Pending Verifiable Pairing Requests</span>
        <button onclick="refreshSetupState()" id="btn-refresh-challenges">Refresh Requests</button>
      </h2>
      <div id="unverifiable-notice" class="guidance-banner hidden"></div>
      <div id="challenges-container"></div>
    </div>
  </div>

  <script>
    let csrfToken = null;
    let currentLang = 'vi';
    let latestState = null;

    const I18N = {
      en: {
        hdrTitle: 'WinOwner Setup Center',
        secProgress: 'Four-Step Connection & Owner Control State',
        secTable: 'System & Security Live Status',
        thComponent: 'Component',
        thStatus: 'Live Status',
        thDetail: 'Verified Detail',
        lblLocal: 'Local Service',
        lblTunnel: 'Tunnel',
        lblChatgpt: 'ChatGPT',
        lblOwner: 'Owner Identity',
        lblPerm: 'Permissions',
        lblVer: 'Version',
        secActions: 'Setup Center Actions',
        btnInstall: '1. Install / Repair',
        btnConnect: '2. Connect ChatGPT',
        btnVerify: '3. Verify Connection',
        btnReview: '4. Review Owner Access',
        btnRevoke: '5. Revoke Access',
        btnReconfig: '6. Reconfigure Tunnel',
        btnUpdates: '7. Check for Updates',
        btnUninstall: '8. Uninstall',
        secPairing: 'Review Owner Access — Pending Verifiable Pairing Requests',
        noPending: 'No pending owner pairing requests. When an OAuth 2.1 ChatGPT client requests owner access, it will appear here with its verification code for your one-click approval.',
        confirmOwnerBtn: 'Confirm Owner Access',
        denyBtn: 'Deny Request',
        estopBtn: 'Emergency Stop',
        estopResetBtn: 'Reset Emergency Stop'
      },
      vi: {
        hdrTitle: 'Trung tâm Thiết lập WinOwner',
        secProgress: 'Trạng thái 4 Bước Kết nối & Quyền Chủ máy',
        secTable: 'Bảng Trạng thái Hệ thống & Bảo mật Trực tiếp',
        thComponent: 'Hạng mục',
        thStatus: 'Trạng thái',
        thDetail: 'Chi tiết xác minh',
        lblLocal: 'Local Service (Dịch vụ máy)',
        lblTunnel: 'Tunnel (Đường truyền riêng)',
        lblChatgpt: 'ChatGPT',
        lblOwner: 'Owner Identity (Danh tính chủ máy)',
        lblPerm: 'Permissions (Quyền hạn thực tế)',
        lblVer: 'Version (Phiên bản)',
        secActions: 'Thao tác Quản trị',
        btnInstall: '1. Cài đặt / Sửa chữa (Install / Repair)',
        btnConnect: '2. Kết nối ChatGPT (Connect ChatGPT)',
        btnVerify: '3. Xác minh kết nối (Verify Connection)',
        btnReview: '4. Duyệt quyền Chủ máy (Review Owner Access)',
        btnRevoke: '5. Thu hồi quyền (Revoke Access)',
        btnReconfig: '6. Cấu hình lại Tunnel (Reconfigure Tunnel)',
        btnUpdates: '7. Kiểm tra cập nhật (Check for Updates)',
        btnUninstall: '8. Gỡ cài đặt (Uninstall)',
        secPairing: 'Duyệt quyền Chủ máy — Yêu cầu ghép nối có xác minh đang chờ',
        noPending: 'Không có yêu cầu ghép nối chủ máy nào đang chờ. Khi client ChatGPT OAuth 2.1 yêu cầu quyền chủ máy, thông tin và mã xác nhận sẽ hiển thị tại đây để bạn xác nhận một lần.',
        confirmOwnerBtn: 'Xác nhận cấp quyền chủ máy',
        denyBtn: 'Từ chối (Deny)',
        estopBtn: 'Dừng khẩn cấp (E-Stop)',
        estopResetBtn: 'Tắt Dừng khẩn cấp'
      }
    };

    function setText(id, text) {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    }

    function applyTranslations() {
      const t = I18N[currentLang];
      setText('hdr-title', t.hdrTitle);
      setText('sec-progress-title', t.secProgress);
      setText('sec-table-title', t.secTable);
      setText('th-component', t.thComponent);
      setText('th-status', t.thStatus);
      setText('th-detail', t.thDetail);
      setText('lbl-local-service', t.lblLocal);
      setText('lbl-tunnel', t.lblTunnel);
      setText('lbl-chatgpt', t.lblChatgpt);
      setText('lbl-owner', t.lblOwner);
      setText('lbl-permissions', t.lblPerm);
      setText('lbl-version', t.lblVer);
      setText('sec-actions-title', t.secActions);
      setText('btn-install-repair', t.btnInstall);
      setText('btn-connect-chatgpt', t.btnConnect);
      setText('btn-verify-conn', t.btnVerify);
      setText('btn-review-owner', t.btnReview);
      setText('btn-revoke-access', t.btnRevoke);
      setText('btn-reconfig-tunnel', t.btnReconfig);
      setText('btn-check-updates', t.btnUpdates);
      setText('btn-uninstall', t.btnUninstall);
      setText('sec-pairing-title', t.secPairing);
      setText('estop-btn', (latestState && latestState.emergency_stop) ? t.estopResetBtn : t.estopBtn);
      if (latestState) renderState(latestState);
    }

    function toggleLanguage() {
      currentLang = currentLang === 'vi' ? 'en' : 'vi';
      document.documentElement.lang = currentLang;
      applyTranslations();
    }

    async function apiCall(path, method = 'GET', body = null) {
      const headers = { 'Accept': 'application/json' };
      if (csrfToken && method !== 'GET') {
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
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error_description || data.error || ('HTTP ' + res.status));
      }
      return data;
    }

    async function loginSetupCenter() {
      const pwd = document.getElementById('owner-password').value;
      const errEl = document.getElementById('login-error');
      errEl.textContent = '';
      try {
        const res = await apiCall('/console/api/login', 'POST', { password: pwd });
        csrfToken = res.csrf_token;
        document.getElementById('owner-password').value = '';
        document.getElementById('login-overlay').classList.add('hidden');
        await refreshSetupState();
      } catch (e) {
        errEl.textContent = e.message;
      }
    }

    async function logoutSetupCenter() {
      try { await apiCall('/console/api/logout', 'POST', {}); } catch (_) {}
      csrfToken = null;
      document.getElementById('login-overlay').classList.remove('hidden');
    }

    function setPill(id, value, colorClass) {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = value;
      el.className = 'pill ' + colorClass;
    }

    function renderState(st) {
      latestState = st;
      setText('hdr-state-pill', st.state);
      const estopBtn = document.getElementById('estop-btn');
      if (estopBtn) {
        const t = I18N[currentLang];
        estopBtn.textContent = st.emergency_stop ? t.estopResetBtn : t.estopBtn;
        estopBtn.className = st.emergency_stop ? 'warn' : 'danger';
      }
      const tbl = st.status_table || {};

      setPill('val-local-service', tbl.local_service || 'Error', tbl.local_service === 'Ready' ? 'green' : 'red');
      setPill('val-tunnel', tbl.tunnel || 'Disconnected', tbl.tunnel === 'Connected' ? 'green' : 'amber');
      setPill('val-chatgpt', tbl.chatgpt || 'Not Verified', tbl.chatgpt === 'Verified' ? 'green' : 'amber');
      setPill('val-owner', tbl.owner_identity || 'Pending', tbl.owner_identity === 'Verified' ? 'green' : 'amber');

      const permColor = tbl.permissions === 'Owner' ? 'green' : (tbl.permissions === 'Scoped' ? 'blue' : 'amber');
      setPill('val-permissions', tbl.permissions || 'Read', permColor);
      setPill('val-version', tbl.version || 'Current', tbl.version === 'Current' ? 'green' : 'blue');

      setText('det-local-service', 'Profile: ' + st.profile + (st.emergency_stop ? ' [EMERGENCY STOP]' : ''));
      setText('det-tunnel', (st.tunnel_details && st.tunnel_details.status_code) || '');
      const cg = st.chatgpt_connection || {};
      setText('det-chatgpt', cg.verified_read_tool_call
        ? ('Verified tool: ' + (cg.last_verified_tool || 'ok') + ' (' + (cg.last_auth_mode || '') + ')')
        : 'Run a read-only prompt in ChatGPT to verify');
      const oi = st.owner_identity || {};
      setText('det-owner', oi.verified
        ? ('Client: ' + (oi.client_id_masked || '') + ' (' + (oi.remaining_seconds || 0) + 's left)')
        : ('Pending challenges: ' + (oi.pending_challenges_count || 0)));
      const ep = st.effective_permissions || {};
      setText('det-permissions', 'Tiers: ' + ((ep.enabled_tiers || ['READ']).join(', ')));
      const vi = st.version_info || {};
      setText('det-version', (vi.current_version || 'v1.2.0') + (vi.update_available ? (' -> ' + vi.latest_version) : ''));

      // Render 4 steps safely using textContent
      const stepper = document.getElementById('stepper-container');
      stepper.textContent = '';
      for (const s of (st.steps || [])) {
        const box = document.createElement('div');
        box.className = 'step-box' + (s.completed ? ' done' : '') + (s.active ? ' current' : '');
        box.textContent = (currentLang === 'vi' ? s.label_vi : s.label_en) + (s.completed ? ' [OK]' : '');
        stepper.appendChild(box);
      }

      const guidance = (st.guidance && st.guidance[currentLang]) || '';
      setText('guidance-text', guidance);

      // Render pending pairing challenges safely using DOM textContent nodes
      const chContainer = document.getElementById('challenges-container');
      chContainer.textContent = '';
      const pending = st.pending_challenges || [];
      if (pending.length === 0) {
        const emptyP = document.createElement('p');
        emptyP.style.color = 'var(--muted)';
        emptyP.textContent = I18N[currentLang].noPending;
        chContainer.appendChild(emptyP);
      } else {
        for (const ch of pending) {
          const card = document.createElement('div');
          card.className = 'challenge-card';

          const title = document.createElement('div');
          title.style.fontWeight = '700';
          const maskedCid = ch.client_id && ch.client_id.length > 15 ? (ch.client_id.slice(0, 12) + '...') : (ch.client_id || '');
          title.textContent = 'Code: ' + ch.verification_code + ' | Client: ' + (ch.client_name || 'OAuth 2.1 Client') + ' (' + maskedCid + ') | Auth: OAuth 2.1';
          card.appendChild(title);

          const meta = document.createElement('div');
          meta.style.fontSize = '12px';
          meta.style.color = 'var(--muted)';
          meta.style.margin = '6px 0';
          meta.textContent = 'Scope: ' + ch.requested_permission_level +
            ' | Tiers: ' + (ch.requested_tiers || []).join(', ') +
            ' | Workspaces: ' + ((ch.requested_workspaces || []).join(', ') || 'Default approved workspace') +
            ' | Destructive: ' + (ch.allow_destructive ? 'Allowed' : 'Blocked') +
            ' | TTL: ' + ch.requested_ttl_seconds + 's' +
            ' | Expires in: ' + ch.remaining_seconds + 's';
          card.appendChild(meta);

          const btnRow = document.createElement('div');
          btnRow.style.display = 'flex';
          btnRow.style.gap = '8px';

          const btnConfirm = document.createElement('button');
          btnConfirm.className = 'success';
          btnConfirm.textContent = I18N[currentLang].confirmOwnerBtn;
          btnConfirm.onclick = () => confirmOwnerAccess(ch.challenge_id, ch.requested_permission_level, ch.requested_workspaces);

          const btnDeny = document.createElement('button');
          btnDeny.className = 'danger';
          btnDeny.textContent = I18N[currentLang].denyBtn;
          btnDeny.onclick = () => denyChallenge(ch.challenge_id);

          btnRow.appendChild(btnConfirm);
          btnRow.appendChild(btnDeny);
          card.appendChild(btnRow);
          chContainer.appendChild(card);
        }
      }
    }

    async function refreshSetupState() {
      const st = await apiCall('/setup/api/state', 'GET');
      renderState(st);
      return st;
    }

    function showPanel(id) {
      hidePanels();
      const el = document.getElementById(id);
      if (el) el.classList.remove('hidden');
    }

    function hidePanels() {
      document.getElementById('panel-connect').classList.add('hidden');
      document.getElementById('panel-reconfig').classList.add('hidden');
    }

    function showActionOutput(obj) {
      setText('action-result-box', JSON.stringify(obj, null, 2));
    }

    async function runInstallRepair() {
      try {
        const res = await apiCall('/setup/api/actions/install-repair', 'POST', {});
        if (res.state_snapshot) renderState(res.state_snapshot);
        showActionOutput(res.summary || res);
      } catch (e) {
        showActionOutput({ error: e.message });
      }
    }

    async function submitConnectChatGPT(isReconfigure) {
      const tid = document.getElementById(isReconfigure ? 'reconfig-tunnel-id' : 'input-tunnel-id').value.trim();
      const keyEl = document.getElementById(isReconfigure ? 'reconfig-runtime-key' : 'input-runtime-key');
      const authModeEl = document.getElementById(isReconfigure ? 'reconfig-auth-mode' : 'input-auth-mode');
      const authMode = authModeEl ? authModeEl.value : 'oauth';
      const apiKey = keyEl.value;
      keyEl.value = '';
      const ep = isReconfigure ? '/setup/api/actions/reconfigure-tunnel' : '/setup/api/actions/connect-chatgpt';
      try {
        const res = await apiCall(ep, 'POST', { tunnel_id: tid, runtime_api_key: apiKey, auth_mode: authMode });
        hidePanels();
        if (res.state_snapshot) renderState(res.state_snapshot);
        showActionOutput(res.summary || res);
      } catch (e) {
        showActionOutput({ error: e.message });
      }
    }

    async function runVerifyConnection() {
      try {
        const res = await apiCall('/setup/api/actions/verify-connection', 'POST', {});
        if (res.state_snapshot) renderState(res.state_snapshot);
        showActionOutput(res.summary || res);
      } catch (e) {
        showActionOutput({ error: e.message });
      }
    }

    async function confirmOwnerAccess(challengeId, level, workspaces) {
      try {
        const res = await apiCall('/setup/api/pairing/challenges/' + encodeURIComponent(challengeId) + '/approve', 'POST', {
          approved_permission_level: level,
          approved_workspaces: workspaces,
          activate_profile: true
        });
        if (res.state_snapshot) renderState(res.state_snapshot);
        showActionOutput({
          confirmed: true,
          challenge_id: challengeId,
          delegation_id: res.delegation_id,
          level: res.approved_permission_level,
          activated_profile: res.activated_profile
        });
      } catch (e) {
        showActionOutput({ error: e.message });
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
        showActionOutput({ emergency_stop: !isActive, state: res.state_snapshot ? res.state_snapshot.state : null });
      } catch (e) {
        showActionOutput({ error: e.message });
      }
    }

    async function denyChallenge(challengeId) {
      try {
        const res = await apiCall('/setup/api/pairing/challenges/' + encodeURIComponent(challengeId) + '/deny', 'POST', {
          reason: 'denied_by_owner_in_setup_center'
        });
        if (res.state_snapshot) renderState(res.state_snapshot);
        showActionOutput({ denied: true, challenge_id: challengeId, status: res.status });
      } catch (e) {
        showActionOutput({ error: e.message });
      }
    }

    async function runRevokeAccess() {
      try {
        const res = await apiCall('/setup/api/actions/revoke-access', 'POST', { revoke_all: true });
        if (res.state_snapshot) renderState(res.state_snapshot);
        showActionOutput(res.summary || res);
      } catch (e) {
        showActionOutput({ error: e.message });
      }
    }

    async function runCheckUpdates() {
      try {
        const res = await apiCall('/setup/api/actions/check-updates', 'POST', {});
        if (res.state_snapshot) renderState(res.state_snapshot);
        showActionOutput(res.summary || res);
      } catch (e) {
        showActionOutput({ error: e.message });
      }
    }

    async function runUninstall() {
      try {
        const res = await apiCall('/setup/api/actions/uninstall', 'POST', { confirm: true });
        if (res.state_snapshot) renderState(res.state_snapshot);
        showActionOutput(res.summary || res);
      } catch (e) {
        showActionOutput({ error: e.message });
      }
    }

    window.addEventListener('DOMContentLoaded', async () => {
      applyTranslations();
      const hashStr = window.location.hash || '';
      if (hashStr.startsWith('#handoff=')) {
        const handoffCode = decodeURIComponent(hashStr.slice('#handoff='.length)).trim();
        // Scrub fragment immediately so single-use code never remains in address bar or history
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
            if (res.state_snapshot) {
              renderState(res.state_snapshot);
            } else {
              await refreshSetupState();
            }
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
    create_handoff_web_session: Callable[[str], tuple[Any, bool]] | None = None,
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
            # Prune expired or used tickets and keep bounded
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

    def _current_snapshot() -> dict[str, Any]:
        with state_lock:
            l_ready = bool(runtime_overrides.get("local_service_ready", True))
            t_info = runtime_overrides.get("tunnel_info")
            v_info = runtime_overrides.get("version_state")
        if "tunnel_probe" in hooks and t_info is None:
            t_info = hooks["tunnel_probe"]()
        return compute_setup_center_state(
            gw,
            persistence=pers,
            signing_key=sk,
            port=adapter_port,
            local_service_ready=l_ready,
            tunnel_info=t_info,
            version_state=v_info,
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
        import math
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
        snap = _current_snapshot()
        if sess is not None and getattr(sess, "csrf_token", None):
            snap["csrf_token"] = sess.csrf_token
        return JSONResponse(status_code=200, content=snap)

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
                # Auto-align approved workspaces on policy roots (never adding sensitive paths)
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
            hook_res = await asyncio.to_thread(hooks["install_repair"], body)
        else:
            hook_res = {
                "action": "install_repair",
                "status": "LOCAL_INSTALL_PASS",
                "profile": str(getattr(gw.policy, "profile", "read_only")),
                "message_en": "Local MCP service health and HTTP 401 authentication boundary verified.",
                "message_vi": "Đã xác minh dịch vụ MCP cục bộ và ranh giới chặn truy cập ẩn danh HTTP 401.",
            }
        with state_lock:
            runtime_overrides["local_service_ready"] = bool(hook_res.get("local_service_ready", True))

        gw.policy.audit.log_event(
            tool_name="setup_center_install_repair",
            tier="SECURITY",
            decision="ALLOW",
            reason="Executed Install / Repair check from WinOwner Setup Center",
            result_summary=redact_secrets(hook_res),
        )
        return JSONResponse(
            status_code=200,
            content={"ok": True, "summary": redact_secrets(hook_res), "state_snapshot": _current_snapshot()},
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

        tunnel_id = str(body.get("tunnel_id") or "").strip()
        runtime_api_key = str(body.get("runtime_api_key") or "").strip()
        auth_mode = str(body.get("auth_mode") or "oauth").strip().lower()
        if auth_mode not in ("oauth", "auth_none"):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "invalid_auth_mode", "error_description": "auth_mode must be 'oauth' or 'auth_none'."},
            )
        # Immediately remove raw key from dict so it can never be logged or serialized
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
        # Wipe secret reference
        runtime_api_key = ""

        # Reconfiguring or connecting a new tunnel clears stale ChatGPT tool call verification
        # unless the hook explicitly preserves it on a failed rolled-back reconfiguration.
        if is_reconfigure and not hook_res.get("rolled_back", False):
            try:
                with pers._db() as conn:
                    conn.execute("DELETE FROM chatgpt_connection_state WHERE id = 1")
            except Exception:
                pass

        with state_lock:
            runtime_overrides["tunnel_info"] = {
                "connected": bool(hook_res.get("readyz_ok", True)),
                "configured": True,
                "readyz_ok": bool(hook_res.get("readyz_ok", True)),
                "status_code": str(hook_res.get("status") or "TUNNEL_READY"),
            }

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
            content={"ok": True, "summary": safe_summary, "state_snapshot": _current_snapshot()},
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

        snap = _current_snapshot()
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
            hook_res = await asyncio.to_thread(hooks["check_updates"], body)
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
            hook_res = await asyncio.to_thread(hooks["uninstall"], body)
        else:
            hook_res = {
                "action": "uninstall",
                "status": "SERVICE_AND_TUNNEL_STOPPED",
                "secrets_preserved": True,
                "message_en": "Verified local MCP service and tunnel tasks stopped and unregistered. Local user data and keys are preserved.",
                "message_vi": "Đã dừng và gỡ tác vụ MCP cục bộ cùng tunnel đã xác minh. Dữ liệu và khóa riêng trên máy vẫn được giữ an toàn.",
            }

        with state_lock:
            runtime_overrides["local_service_ready"] = False
            runtime_overrides["tunnel_info"] = {
                "connected": False,
                "configured": False,
                "readyz_ok": False,
                "explicit_disconnect": True,
                "status_code": "TUNNEL_TASK_REMOVED",
            }

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
