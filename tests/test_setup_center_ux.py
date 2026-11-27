"""Phase 3 — WinOwner Setup Center & Four-Step Onboarding UX Automated Acceptance Suite.

Verifies:
1. 4-Step State Machine truth progression (ZERO fake green states):
   LOCAL_READY -> CHATGPT_CONNECTED -> OWNER_VERIFIED -> OWNER_CONTROL_READY
   - /health and /readyz and MCP initialize/tools/list NEVER advance to CHATGPT_CONNECTED.
   - Real authenticated read-only MCP tool call advances to CHATGPT_CONNECTED.
   - Authentication: None (shared tunnel bearer) is strictly capped at Read (cannot pair or escalate to OWNER_VERIFIED).
   - Verifiable OAuth 2.1 pairing challenge approved in Setup Center advances to OWNER_VERIFIED / OWNER_CONTROL_READY.
   - Under read_only server profile, approving owner identity advances to OWNER_VERIFIED while Permissions honestly stays 'Read' (never fake 'Owner').
2. 6-Row Live Status Table exact contract:
   Local Service (Ready/Error), Tunnel (Connected/Disconnected), ChatGPT (Verified/Not Verified),
   Owner Identity (Verified/Pending), Permissions (Read/Scoped/Owner), Version (Current/Update Available).
3. All 8 Setup Center Actions:
   Install / Repair, Connect ChatGPT, Verify Connection, Review Owner Access (Approve / Deny + single-use anti-replay + expiry),
   Revoke Access (+ Emergency Stop sync), Reconfigure Tunnel, Check for Updates (semver-only, branch HEAD rejected), Uninstall.
4. Security & Bilingual UX invariants:
   - Bilingual VI & EN UI shell (/setup), zero raw secrets/tokens in HTML/JS/localStorage/responses/logs,
   - Zero innerHTML XSS sinks in web_console.py and setup_center.py,
   - Loopback Host/Origin/Referer, HttpOnly SameSite=Strict cookie, X-CSRF-Token enforcement.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _p in (
    PROJECT_ROOT,
    PROJECT_ROOT / "mcp-gateway",
    PROJECT_ROOT / "backend",
    PROJECT_ROOT / "desktop-agent",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from cloud_autonomous_adapter import create_cloud_autonomous_adapter_app  # noqa: E402
from gateway_server import MCPGatewayServer  # noqa: E402
from policy import GatewayPolicy  # noqa: E402
from runtime.coding_tools import CodingToolHandler  # noqa: E402
from runtime.contracts import RuntimeStatePaths  # noqa: E402
from web_console import create_web_console_app  # noqa: E402

TEST_CONSOLE_SECRET = "phase3-setup-center-owner-secret-99887766"
TEST_TUNNEL_BEARER = "shared_tunnel_bearer_phase3_test_1122334455"
TEST_OAUTH_SIGNING_KEY = "independent_oauth_signing_key_phase3_test_66778899"
TEST_OWNER_ADMIN_TOKEN = "mcp_oadm_independent_owner_admin_phase3_123456"


def _build_dual_surface_env(profile: str = "owner_full_control"):
    """Create an isolated MCP Cloud Adapter + WinOwner Setup Center sharing the same gateway and SQLite DB."""
    td = tempfile.mkdtemp(prefix="test_setup_center_ux_")
    ws = Path(td) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "hello.txt").write_text("hello from phase 3 workspace\n", encoding="utf-8")

    paths = RuntimeStatePaths.create_isolated(td)
    trusted_cfg = Path(td) / "trusted_workspaces.json"
    trusted_cfg.write_text(
        json.dumps(
            {
                "version": "1.0",
                "trusted_mode_enabled": True,
                "trusted_workspaces": [
                    {
                        "workspace_root": str(ws),
                        "include_subdirectories": True,
                        "allowed_tools": [
                            "agent_workspace_list",
                            "agent_read_file",
                            "agent_write_file",
                            "agent_git_status",
                        ],
                        "allowed_commands": ["python *", "pytest *"],
                        "duration_seconds": 3600,
                        "max_tool_calls": 100,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    policy = GatewayPolicy(
        profile=profile,
        audit_log_path=paths.audit_log_path,
        emergency_stop_file=paths.emergency_stop_file,
    )
    existing_write_roots = list(getattr(policy, "write_roots", []) or [])
    if ws.resolve() not in existing_write_roots:
        policy.write_roots = [*existing_write_roots, ws.resolve()]

    gw = MCPGatewayServer(policy=policy, runtime_paths=paths)
    agent_handler = gw.get_agent_tool_handler()
    agent_handler.trusted_config_path = trusted_cfg
    gw._coding_tool_handler = CodingToolHandler(
        agent_handler.store,
        agent_handler.broker,
        trusted_config_path=trusted_cfg,
        audit_path=Path(td) / "coding_audit.db",
    )

    adapter_app = create_cloud_autonomous_adapter_app(
        gateway=gw,
        caller_auth_token=TEST_TUNNEL_BEARER,
        oauth_signing_key=TEST_OAUTH_SIGNING_KEY,
        owner_admin_token=TEST_OWNER_ADMIN_TOKEN,
        db_path=paths.oauth_db_path,
        enable_direct_coding=True,
    )

    tunnel_probe_state = {
        "connected": False,
        "configured": False,
        "readyz_ok": False,
        "status_code": "TUNNEL_NOT_CONFIGURED",
    }

    console_app = create_web_console_app(
        gateway=gw,
        owner_password=TEST_CONSOLE_SECRET,
        oauth_persistence=adapter_app.state.oauth_persistence,
        oauth_signing_key=TEST_OAUTH_SIGNING_KEY,
        owner_admin_token=TEST_OWNER_ADMIN_TOKEN,
        db_path=paths.oauth_db_path,
        setup_action_hooks={
            "tunnel_probe": lambda: dict(tunnel_probe_state),
        },
    )

    return {
        "td": td,
        "ws": ws,
        "paths": paths,
        "gw": gw,
        "adapter_client": TestClient(adapter_app),
        "console_client": TestClient(console_app),
        "tunnel_probe_state": tunnel_probe_state,
    }


def _login_setup_center(console_client: TestClient) -> str:
    resp = console_client.post("/console/api/login", json={"password": TEST_CONSOLE_SECRET})
    assert resp.status_code == 200, resp.text
    return resp.json()["csrf_token"]


def _register_oauth_client_and_token(adapter_client: TestClient, name: str = "ChatGPT OAuth Client") -> tuple[str, str]:
    reg = adapter_client.post(
        "/oauth/register",
        json={
            "client_name": name,
            "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
            "grant_types": ["authorization_code", "refresh_token", "client_credentials"],
            "scope": "mcp:autonomous",
        },
    )
    assert reg.status_code == 201, reg.text
    cid = reg.json()["client_id"]
    csec = reg.json()["client_secret"]
    tok = adapter_client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": cid, "client_secret": csec},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return cid, tok.json()["access_token"]


def _mcp_init_and_headers(adapter_client: TestClient, bearer: str) -> dict[str, str]:
    hdrs = {"Authorization": f"Bearer {bearer}", "Mcp-Protocol-Version": "2025-03-26"}
    r = adapter_client.post(
        "/mcp",
        headers=hdrs,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
    )
    assert r.status_code == 200, r.text
    return {**hdrs, "Mcp-Session-Id": r.headers["Mcp-Session-Id"]}


def test_setup_center_ui_shell_bilingual_and_zero_secrets_and_csrf():
    """Verify Setup Center HTML shell has bilingual VI/EN support, zero secrets/innerHTML, and strict auth/CSRF."""
    env = _build_dual_surface_env(profile="owner_full_control")
    try:
        c_client = env["console_client"]

        # 1. Public UI route /setup loads with CSP, X-Frame-Options: DENY, bilingual dictionary, and zero secrets
        ui = c_client.get("/setup")
        assert ui.status_code == 200
        assert "WinOwner Setup Center" in ui.text
        assert "Trung tâm Thiết lập WinOwner" in ui.text
        assert TEST_CONSOLE_SECRET not in ui.text
        assert TEST_TUNNEL_BEARER not in ui.text
        assert TEST_OAUTH_SIGNING_KEY not in ui.text
        assert TEST_OWNER_ADMIN_TOKEN not in ui.text
        assert "localStorage.setItem" not in ui.text
        assert "innerHTML" not in ui.text
        assert ui.headers.get("x-frame-options") == "DENY"
        assert "default-src 'self'" in ui.headers.get("content-security-policy", "")

        # Also verify P1-3 fix: Mode B /console has zero innerHTML sinks
        mode_b_ui = c_client.get("/console")
        assert mode_b_ui.status_code == 200
        assert "innerHTML" not in mode_b_ui.text

        # 2. Unauthenticated requests to /setup/api/* are rejected with 401
        assert c_client.get("/setup/api/state").status_code == 401
        assert c_client.get("/setup/api/pairing/challenges").status_code == 401
        assert c_client.post("/setup/api/actions/verify-connection", json={}).status_code == 401

        # 3. After owner login, POST without X-CSRF-Token or with cross-origin header is rejected with 403
        csrf = _login_setup_center(c_client)
        assert c_client.post("/setup/api/actions/verify-connection", json={}).status_code == 403
        assert (
            c_client.post(
                "/setup/api/actions/verify-connection",
                headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example.com"},
                json={},
            ).status_code
            == 403
        )
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


def test_four_step_state_machine_and_zero_fake_green_states():
    """Verify LOCAL_READY -> CHATGPT_CONNECTED -> OWNER_VERIFIED -> OWNER_CONTROL_READY transitions and zero fake states."""
    env = _build_dual_surface_env(profile="owner_full_control")
    try:
        a_client = env["adapter_client"]
        c_client = env["console_client"]
        ws = env["ws"]
        csrf = _login_setup_center(c_client)
        csrf_hdr = {"X-CSRF-Token": csrf}

        # Step 1: Initial state is LOCAL_READY (Tunnel Disconnected, ChatGPT Not Verified, Owner Pending, Permissions Read)
        st1 = c_client.get("/setup/api/state").json()
        assert st1["state"] == "LOCAL_READY"
        assert st1["completed_steps_count"] == 1
        assert st1["status_table"] == {
            "local_service": "Ready",
            "tunnel": "Disconnected",
            "chatgpt": "Not Verified",
            "owner_identity": "Pending",
            "permissions": "Read",
            "version": "Current",
        }

        # Action 2: Connect ChatGPT Tunnel (valid tunnel_id + hidden runtime_api_key; secret never echoed)
        secret_runtime_key = "rk_test_super_secret_runtime_api_key_value_999888777"
        conn_res = c_client.post(
            "/setup/api/actions/connect-chatgpt",
            headers=csrf_hdr,
            json={
                "tunnel_id": "tunnel_1234567890abcdef1234567890abcdef",
                "runtime_api_key": secret_runtime_key,
            },
        )
        assert conn_res.status_code == 200, conn_res.text
        assert secret_runtime_key not in conn_res.text
        st_after_tunnel = conn_res.json()["state_snapshot"]
        assert st_after_tunnel["status_table"]["tunnel"] == "Connected"
        # CRITICAL: Even with Tunnel Connected (/readyz == 200) and /health == 200, ChatGPT MUST remain Not Verified!
        assert st_after_tunnel["status_table"]["chatgpt"] == "Not Verified"
        assert st_after_tunnel["state"] == "LOCAL_READY"

        # Call /health, initialize, and tools/list over MCP — NONE of these may flip ChatGPT to Verified!
        assert a_client.get("/health").status_code == 200
        cid, access_token = _register_oauth_client_and_token(a_client, "ChatGPT Workspace Client")
        mcp_hdr = _mcp_init_and_headers(a_client, access_token)
        t_list = a_client.post(
            "/mcp",
            headers=mcp_hdr,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert t_list.status_code == 200

        # Action 3: Verify Connection before any real tool call -> still LOCAL_READY / chatgpt_verified=False
        ver_before = c_client.post("/setup/api/actions/verify-connection", headers=csrf_hdr, json={}).json()
        assert ver_before["summary"]["chatgpt_verified"] is False
        assert ver_before["state_snapshot"]["state"] == "LOCAL_READY"
        assert ver_before["state_snapshot"]["status_table"]["chatgpt"] == "Not Verified"

        # Step 2: Execute a REAL authenticated read-only MCP tool call (agent_read_file)
        read_call = a_client.post(
            "/mcp",
            headers=mcp_hdr,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "agent_read_file", "arguments": {"workspace_root": str(ws), "path": "hello.txt"}},
            },
        )
        assert read_call.status_code == 200
        assert read_call.json()["result"]["isError"] is False

        ver_after = c_client.post("/setup/api/actions/verify-connection", headers=csrf_hdr, json={}).json()
        assert ver_after["summary"]["chatgpt_verified"] is True
        st2 = ver_after["state_snapshot"]
        assert st2["state"] == "CHATGPT_CONNECTED"
        assert st2["completed_steps_count"] == 2
        assert st2["status_table"]["chatgpt"] == "Verified"
        assert st2["status_table"]["owner_identity"] == "Pending"
        assert st2["status_table"]["permissions"] == "Read"

        # Step 3 & 4: Client requests verifiable owner pairing; challenge appears in Setup Center Review Owner Access
        pair_req = a_client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "requested_permission_level": "OWNER",
                "requested_workspaces": [str(ws)],
                "requested_ttl_seconds": 3600,
                "allow_destructive": False,
            },
        )
        assert pair_req.status_code == 201
        challenge_id = pair_req.json()["challenge_id"]

        st_pending = c_client.get("/setup/api/state").json()
        assert st_pending["state"] == "CHATGPT_CONNECTED"
        assert st_pending["owner_identity"]["pending_challenges_count"] == 1
        assert st_pending["pending_challenges"][0]["challenge_id"] == challenge_id

        # Action 4: Owner approves the pending challenge in Setup Center (scoped to ws)
        app_res = c_client.post(
            f"/setup/api/pairing/challenges/{challenge_id}/approve",
            headers=csrf_hdr,
            json={
                "approved_permission_level": "SCOPED",
                "approved_workspaces": [str(ws)],
                "approved_ttl_seconds": 1800,
                "allow_destructive": False,
            },
        )
        assert app_res.status_code == 200, app_res.text
        assert "access_token" not in app_res.text
        st4 = app_res.json()["state_snapshot"]
        assert st4["state"] == "OWNER_CONTROL_READY"
        assert st4["completed_steps_count"] == 4
        assert st4["status_table"]["owner_identity"] == "Verified"
        assert st4["status_table"]["permissions"] == "Scoped"
        assert st4["effective_permissions"]["owner_control_ready"] is True

        # Replaying the approved challenge in Setup Center fails with 409
        replay_res = c_client.post(
            f"/setup/api/pairing/challenges/{challenge_id}/approve",
            headers=csrf_hdr,
            json={},
        )
        assert replay_res.status_code == 409
        assert replay_res.json()["error_code"] == "CHALLENGE_ALREADY_USED"

        # Action 5: Revoke Access immediately downgrades state to CHATGPT_CONNECTED and Permissions to Read
        rev_res = c_client.post(
            "/setup/api/actions/revoke-access",
            headers=csrf_hdr,
            json={"revoke_all": True},
        )
        assert rev_res.status_code == 200
        st_rev = rev_res.json()["state_snapshot"]
        assert st_rev["state"] == "CHATGPT_CONNECTED"
        assert st_rev["status_table"]["owner_identity"] == "Pending"
        assert st_rev["status_table"]["permissions"] == "Read"
        assert st_rev["effective_permissions"]["owner_control_ready"] is False
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


def test_read_only_profile_and_auth_none_boundary_in_setup_center():
    """Verify read_only profile never claims Owner permissions and Authentication: None cannot pair."""
    env = _build_dual_surface_env(profile="read_only")
    try:
        a_client = env["adapter_client"]
        c_client = env["console_client"]
        ws = env["ws"]
        csrf = _login_setup_center(c_client)
        csrf_hdr = {"X-CSRF-Token": csrf}

        # 1. Connect via Authentication: None (shared tunnel bearer) and run a read tool
        tunnel_hdr = _mcp_init_and_headers(a_client, TEST_TUNNEL_BEARER)
        r_call = a_client.post(
            "/mcp",
            headers=tunnel_hdr,
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "agent_read_file", "arguments": {"workspace_root": str(ws), "path": "hello.txt"}},
            },
        )
        assert r_call.status_code == 200
        assert r_call.json()["result"]["isError"] is False

        st_auth_none = c_client.get("/setup/api/state").json()
        assert st_auth_none["state"] == "CHATGPT_CONNECTED"
        assert st_auth_none["chatgpt_connection"]["last_auth_mode"] == "shared_tunnel_bearer"
        assert st_auth_none["chatgpt_connection"]["supports_verifiable_owner_identity"] is False
        assert st_auth_none["status_table"]["permissions"] == "Read"
        assert "Authentication: None" in st_auth_none["guidance"]["en"]
        assert "Authentication: None" in st_auth_none["guidance"]["vi"]

        # 2. Now connect with verifiable OAuth 2.1 client and approve pairing while server profile is read_only
        cid, tok = _register_oauth_client_and_token(a_client, "ReadOnly Server OAuth Client")
        ch = a_client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok}"},
            json={"requested_permission_level": "OWNER", "requested_workspaces": [str(ws)]},
        ).json()

        app_ro = c_client.post(
            f"/setup/api/pairing/challenges/{ch['challenge_id']}/approve",
            headers=csrf_hdr,
            json={"approved_permission_level": "OWNER", "approved_workspaces": [str(ws)]},
        )
        assert app_ro.status_code == 200
        st_ro = app_ro.json()["state_snapshot"]
        # Under read_only profile, state stops at OWNER_VERIFIED (step 3) and Permissions MUST show 'Read'!
        assert st_ro["state"] == "OWNER_VERIFIED"
        assert st_ro["completed_steps_count"] == 3
        assert st_ro["status_table"]["owner_identity"] == "Verified"
        assert st_ro["status_table"]["permissions"] == "Read"
        assert st_ro["effective_permissions"]["owner_control_ready"] is False
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


def test_setup_center_deny_reconfigure_updates_and_uninstall_actions():
    """Verify Deny challenge, Reconfigure Tunnel, Check for Updates (semver check), and Uninstall actions."""
    env = _build_dual_surface_env(profile="owner_full_control")
    try:
        a_client = env["adapter_client"]
        c_client = env["console_client"]
        ws = env["ws"]
        csrf = _login_setup_center(c_client)
        csrf_hdr = {"X-CSRF-Token": csrf}

        # 1. Create and Deny a challenge via Setup Center
        cid, tok = _register_oauth_client_and_token(a_client, "Deny Test Client")
        ch = a_client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok}"},
            json={"requested_workspaces": [str(ws)]},
        ).json()
        deny_res = c_client.post(
            f"/setup/api/pairing/challenges/{ch['challenge_id']}/deny",
            headers=csrf_hdr,
            json={"reason": "owner_rejected_in_ui"},
        )
        assert deny_res.status_code == 200
        assert deny_res.json()["status"] == "DENIED"
        assert deny_res.json()["state_snapshot"]["status_table"]["owner_identity"] == "Pending"

        # 2. Reconfigure Tunnel clears previous ChatGPT read-tool verification
        mcp_hdr = _mcp_init_and_headers(a_client, tok)
        a_client.post(
            "/mcp",
            headers=mcp_hdr,
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "agent_read_file", "arguments": {"workspace_root": str(ws), "path": "hello.txt"}},
            },
        )
        assert c_client.get("/setup/api/state").json()["status_table"]["chatgpt"] == "Verified"

        reconf_res = c_client.post(
            "/setup/api/actions/reconfigure-tunnel",
            headers=csrf_hdr,
            json={
                "tunnel_id": "tunnel_abcdef1234567890abcdef1234567890",
                "runtime_api_key": "rk_reconfig_secret_key_never_echoed_123456789",
            },
        )
        assert reconf_res.status_code == 200
        assert "rk_reconfig_secret_key_never_echoed_123456789" not in reconf_res.text
        assert reconf_res.json()["state_snapshot"]["status_table"]["tunnel"] == "Connected"
        assert reconf_res.json()["state_snapshot"]["status_table"]["chatgpt"] == "Not Verified"
        assert reconf_res.json()["state_snapshot"]["state"] == "LOCAL_READY"

        # 3. Check for Updates: rejects arbitrary branch ref ('main'), accepts verified newer semver ('v1.3.0')
        bad_upd = c_client.post(
            "/setup/api/actions/check-updates",
            headers=csrf_hdr,
            json={"candidate_version": "main"},
        )
        assert bad_upd.status_code == 400
        assert bad_upd.json()["error"] == "invalid_release_version"

        ok_upd = c_client.post(
            "/setup/api/actions/check-updates",
            headers=csrf_hdr,
            json={"candidate_version": "v1.3.0"},
        )
        assert ok_upd.status_code == 200
        assert ok_upd.json()["state_snapshot"]["status_table"]["version"] == "Update Available"

        cur_upd = c_client.post(
            "/setup/api/actions/check-updates",
            headers=csrf_hdr,
            json={"candidate_version": "v1.2.0"},
        )
        assert cur_upd.status_code == 200
        assert cur_upd.json()["state_snapshot"]["status_table"]["version"] == "Current"

        # 4. Uninstall requires confirm=True, then sets Local Service to Error/Stopped and Tunnel to Disconnected
        uninst_no_confirm = c_client.post("/setup/api/actions/uninstall", headers=csrf_hdr, json={})
        assert uninst_no_confirm.status_code == 400

        uninst_ok = c_client.post("/setup/api/actions/uninstall", headers=csrf_hdr, json={"confirm": True})
        assert uninst_ok.status_code == 200
        st_uninst = uninst_ok.json()["state_snapshot"]
        assert st_uninst["state"] == "LOCAL_ERROR"
        assert st_uninst["status_table"]["local_service"] == "Error"
        assert st_uninst["status_table"]["tunnel"] == "Disconnected"

        # 5. Install / Repair restores Local Service to Ready (LOCAL_READY)
        repair_ok = c_client.post("/setup/api/actions/install-repair", headers=csrf_hdr, json={})
        assert repair_ok.status_code == 200
        assert repair_ok.json()["state_snapshot"]["status_table"]["local_service"] == "Ready"
        assert repair_ok.json()["state_snapshot"]["state"] == "LOCAL_READY"
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


def test_setup_center_emergency_stop_and_check_winowner_script_contract():
    """Verify Emergency Stop toggle in Setup Center and Check-WinOwner.ps1 4-step/6-row contract."""
    env = _build_dual_surface_env(profile="owner_full_control")
    try:
        a_client = env["adapter_client"]
        c_client = env["console_client"]
        ws = env["ws"]
        csrf = _login_setup_center(c_client)
        csrf_hdr = {"X-CSRF-Token": csrf}

        # Pair and verify owner control ready
        cid, tok = _register_oauth_client_and_token(a_client, "EStop Sync Client")
        mcp_hdr = _mcp_init_and_headers(a_client, tok)
        a_client.post(
            "/mcp",
            headers=mcp_hdr,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "agent_read_file", "arguments": {"workspace_root": str(ws), "path": "hello.txt"}},
            },
        )
        ch = a_client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok}"},
            json={"requested_permission_level": "OWNER", "requested_workspaces": [str(ws)]},
        ).json()
        c_client.post(
            f"/setup/api/pairing/challenges/{ch['challenge_id']}/approve",
            headers=csrf_hdr,
            json={"approved_permission_level": "OWNER", "approved_workspaces": [str(ws)]},
        )
        st_ready = c_client.get("/setup/api/state").json()
        assert st_ready["state"] == "OWNER_CONTROL_READY"
        assert st_ready["status_table"]["permissions"] in ("Scoped", "Owner")

        # Engage Emergency Stop via Revoke Access
        estop_res = c_client.post(
            "/setup/api/actions/revoke-access",
            headers=csrf_hdr,
            json={"revoke_all": False, "engage_emergency_stop": True},
        )
        assert estop_res.status_code == 200
        st_estop = estop_res.json()["state_snapshot"]
        assert st_estop["emergency_stop"] is True
        assert st_estop["status_table"]["permissions"] == "Read"
        assert st_estop["state"] != "OWNER_CONTROL_READY"
        assert a_client.get("/health").json()["emergency_stop"] is True

        # Reset Emergency Stop
        reset_res = c_client.post(
            "/setup/api/actions/revoke-access",
            headers=csrf_hdr,
            json={"revoke_all": False, "reset_emergency_stop": True},
        )
        assert reset_res.status_code == 200
        assert reset_res.json()["state_snapshot"]["emergency_stop"] is False

        # Verify Check-WinOwner.ps1 and Setup-WinOwner.ps1 4-step & Setup Center contract
        check_ps = (PROJECT_ROOT / "scripts" / "Check-WinOwner.ps1").read_text(encoding="utf-8")
        for marker in (
            "LOCAL_READY",
            "CHATGPT_CONNECTED",
            "OWNER_VERIFIED",
            "OWNER_CONTROL_READY",
            "STATUS_TABLE:",
            "SETUP_CENTER_STATUS_JSON:",
            "OpenSetupCenter",
        ):
            assert marker in check_ps, marker

        setup_ps = (PROJECT_ROOT / "scripts" / "Setup-WinOwner.ps1").read_text(encoding="utf-8")
        assert "WinOwner Setup Center: http://127.0.0.1:$effectiveConsolePort/setup" in setup_ps
        assert "8767" in setup_ps
        assert "Four-step state:" in setup_ps
        assert "OpenSetupCenter" in setup_ps
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


def test_local_windows_user_handoff_zero_manual_admin_key_and_security_guards():
    """Track UX-2: Single-use current-Windows-user loopback handoff eliminates manual Admin Key while enforcing strict guards."""
    env = _build_dual_surface_env(profile="read_only")
    try:
        c_client = env["console_client"]
        console_app = c_client.app

        # 1. /setup HTML starts with login-overlay hidden (fallback only) and wires single-button confirmation & handoff scrub
        ui = c_client.get("/setup")
        assert ui.status_code == 200
        assert 'id="login-overlay" class="hidden"' in ui.text
        assert "Open-SetupCenter.cmd" in ui.text
        assert "/setup/api/local-handoff" in ui.text
        assert "history.replaceState" in ui.text
        assert "Xác nhận cấp quyền chủ máy" in ui.text
        assert "Confirm Owner Access" in ui.text
        assert "Emergency Stop" in ui.text
        assert TEST_CONSOLE_SECRET not in ui.text
        assert TEST_OWNER_ADMIN_TOKEN not in ui.text
        assert TEST_TUNNEL_BEARER not in ui.text

        # 2. /setup/api/local-handoff/issue rejects unauthenticated, shared tunnel bearer, cross-origin, and non-loopback
        assert c_client.post("/setup/api/local-handoff/issue", json={}).status_code == 401
        tunnel_try = c_client.post(
            "/setup/api/local-handoff/issue",
            headers={"X-MCP-Owner-Admin-Token": TEST_TUNNEL_BEARER},
            json={},
        )
        assert tunnel_try.status_code == 403
        assert tunnel_try.json()["error"] == "FORBIDDEN_TUNNEL_CREDENTIAL"

        evil_origin_issue = c_client.post(
            "/setup/api/local-handoff/issue",
            headers={"X-MCP-Owner-Admin-Token": TEST_OWNER_ADMIN_TOKEN, "Origin": "https://evil.example.com"},
            json={},
        )
        assert evil_origin_issue.status_code == 403

        non_loopback_client = TestClient(console_app, client=("192.0.2.99", 54321))
        non_lb_issue = non_loopback_client.post(
            "/setup/api/local-handoff/issue",
            headers={"X-MCP-Owner-Admin-Token": TEST_OWNER_ADMIN_TOKEN},
            json={},
        )
        assert non_lb_issue.status_code == 403
        assert non_lb_issue.json()["error"] == "NON_LOOPBACK_REJECTED"

        # 3. Issue valid single-use handoff ticket via local owner_admin_token
        issue_ok = c_client.post(
            "/setup/api/local-handoff/issue",
            headers={"X-MCP-Owner-Admin-Token": TEST_OWNER_ADMIN_TOKEN},
            json={"ttl_seconds": 60},
        )
        assert issue_ok.status_code == 200, issue_ok.text
        ticket = issue_ok.json()
        code = ticket["handoff_code"]
        assert code.startswith("hnd_")
        assert TEST_OWNER_ADMIN_TOKEN not in issue_ok.text
        assert TEST_CONSOLE_SECRET not in issue_ok.text

        # 4. Non-loopback, cross-origin, and untrusted Host exchange attempts are rejected (without consuming valid ticket)
        nl_ex = non_loopback_client.post("/setup/api/local-handoff", json={"handoff_code": code})
        assert nl_ex.status_code == 403
        assert nl_ex.json()["error"] == "NON_LOOPBACK_REJECTED"

        evil_org_ex = c_client.post(
            "/setup/api/local-handoff",
            headers={"Origin": "https://evil.example.com"},
            json={"handoff_code": code},
        )
        assert evil_org_ex.status_code == 403

        evil_host_ex = c_client.post(
            "/setup/api/local-handoff",
            headers={"Host": "evil.example.com"},
            json={"handoff_code": code},
        )
        assert evil_host_ex.status_code == 403

        # 5. Valid loopback exchange authenticates seamlessly (zero manual password), sets HttpOnly SameSite=Strict cookie + CSRF
        ex_ok = c_client.post("/setup/api/local-handoff", json={"handoff_code": code})
        assert ex_ok.status_code == 200, ex_ok.text
        ex_body = ex_ok.json()
        assert ex_body["authenticated"] is True
        assert ex_body["auth_mode"] == "local_windows_user_handoff"
        csrf = ex_body["csrf_token"]
        assert csrf
        set_cookie_hdr = ex_ok.headers.get("set-cookie", "").lower()
        assert "httponly" in set_cookie_hdr
        assert "samesite=strict" in set_cookie_hdr
        assert TEST_OWNER_ADMIN_TOKEN not in ex_ok.text
        assert TEST_CONSOLE_SECRET not in ex_ok.text

        # Authenticated GET /setup/api/state works immediately and supplies csrf_token for reload continuity
        st_resp = c_client.get("/setup/api/state")
        assert st_resp.status_code == 200
        assert st_resp.json()["csrf_token"] == csrf

        # Authenticated POST with X-CSRF-Token works without ever signing in with password
        ver_resp = c_client.post("/setup/api/actions/verify-connection", headers={"X-CSRF-Token": csrf}, json={})
        assert ver_resp.status_code == 200

        # 6. Single-use anti-replay: reusing the exact same code fails with 409 HANDOFF_ALREADY_USED
        replay = c_client.post("/setup/api/local-handoff", json={"handoff_code": code})
        assert replay.status_code == 409
        assert replay.json()["error"] == "HANDOFF_ALREADY_USED"

        # 7. Windows user mismatch is rejected with 403 WINDOWS_USER_MISMATCH
        other_user_ticket = c_client.post(
            "/setup/api/local-handoff/issue",
            headers={"X-MCP-Owner-Admin-Token": TEST_OWNER_ADMIN_TOKEN},
            json={"windows_user": "otherdomain\\otheruser", "ttl_seconds": 30},
        ).json()
        mismatch = c_client.post("/setup/api/local-handoff", json={"handoff_code": other_user_ticket["handoff_code"]})
        assert mismatch.status_code == 403
        assert mismatch.json()["error"] == "WINDOWS_USER_MISMATCH"

        # 8. Short-TTL expired ticket is rejected with 401 HANDOFF_EXPIRED
        exp_ticket = c_client.post(
            "/setup/api/local-handoff/issue",
            headers={"X-MCP-Owner-Admin-Token": TEST_OWNER_ADMIN_TOKEN},
            json={"ttl_seconds": 1.0},
        ).json()
        time.sleep(1.15)
        exp_resp = c_client.post("/setup/api/local-handoff", json={"handoff_code": exp_ticket["handoff_code"]})
        assert exp_resp.status_code == 401
        assert exp_resp.json()["error"] == "HANDOFF_EXPIRED"
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


def test_single_button_owner_confirmation_activates_profile_and_syncs_revoke_and_estop():
    """Track UX-2: Single-button Confirm Owner Access activates profile from read_only, aligns workspace, and syncs Revoke/E-Stop."""
    env = _build_dual_surface_env(profile="read_only")
    try:
        a_client = env["adapter_client"]
        c_client = env["console_client"]
        gw = env["gw"]
        ws = env["ws"]
        ws2 = Path(env["td"]) / "auto_aligned_ws"
        ws2.mkdir(parents=True, exist_ok=True)
        pers = c_client.app.state.setup_center_persistence
        sk = c_client.app.state.setup_center_signing_key

        assert gw.policy.profile == "read_only"

        # Authenticate via zero-manual-key local handoff
        ticket = c_client.post(
            "/setup/api/local-handoff/issue",
            headers={"X-MCP-Owner-Admin-Token": TEST_OWNER_ADMIN_TOKEN},
            json={},
        ).json()
        csrf = c_client.post("/setup/api/local-handoff", json={"handoff_code": ticket["handoff_code"]}).json()["csrf_token"]
        csrf_hdr = {"X-CSRF-Token": csrf}

        # Connect OAuth 2.1 client and complete verified read tool call
        cid, tok = _register_oauth_client_and_token(a_client, "ChatGPT Single-Button Client")
        mcp_hdr = _mcp_init_and_headers(a_client, tok)
        r_read = a_client.post(
            "/mcp",
            headers=mcp_hdr,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "agent_read_file", "arguments": {"workspace_root": str(ws), "path": "hello.txt"}},
            },
        )
        assert r_read.status_code == 200 and r_read.json()["result"]["isError"] is False

        # Request pairing including new non-sensitive workspace ws2
        ch = a_client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok}"},
            json={
                "requested_permission_level": "SCOPED",
                "requested_workspaces": [str(ws), str(ws2)],
                "requested_ttl_seconds": 3600,
                "allow_destructive": False,
            },
        ).json()

        # Single-button confirmation: activate_profile=True elevates read_only -> owner_full_control & persists HMAC state
        confirm_res = c_client.post(
            f"/setup/api/pairing/challenges/{ch['challenge_id']}/approve",
            headers=csrf_hdr,
            json={
                "approved_permission_level": "SCOPED",
                "approved_workspaces": [str(ws), str(ws2)],
                "activate_profile": True,
            },
        )
        assert confirm_res.status_code == 200, confirm_res.text
        c_data = confirm_res.json()
        assert c_data["activated_profile"] == "owner_full_control"
        assert gw.policy.profile == "owner_full_control"
        assert ws2.resolve() in gw.policy.write_roots
        assert c_data["state_snapshot"]["state"] == "OWNER_CONTROL_READY"
        assert c_data["state_snapshot"]["status_table"]["owner_identity"] == "Verified"
        assert c_data["state_snapshot"]["status_table"]["permissions"] == "Scoped"

        # HMAC-verified owner_runtime_state persisted cleanly in SQLite
        persisted = pers.get_owner_approved_profile(secret=sk, owner_id="local_owner")
        assert persisted["valid"] is True
        assert persisted["effective_profile"] == "owner_full_control"

        # Clicking Revoke Access immediately clears persisted profile state and reverts gateway to read_only
        rev_res = c_client.post(
            "/setup/api/actions/revoke-access",
            headers=csrf_hdr,
            json={"revoke_all": True},
        )
        assert rev_res.status_code == 200, rev_res.text
        assert gw.policy.profile == "read_only"
        assert rev_res.json()["state_snapshot"]["status_table"]["permissions"] == "Read"
        assert rev_res.json()["state_snapshot"]["status_table"]["owner_identity"] == "Pending"
        persisted_after_rev = pers.get_owner_approved_profile(secret=sk, owner_id="local_owner")
        assert persisted_after_rev["effective_profile"] == "read_only"
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


def test_auto_config_tunnel_auth_mode_and_open_setup_center_launcher_contract():
    """Track UX-2: Verify tunnel auth_mode validation, auto_reconfigure detection, and Open-SetupCenter.cmd CRLF contract."""
    env = _build_dual_surface_env(profile="read_only")
    try:
        c_client = env["console_client"]
        csrf = _login_setup_center(c_client)
        csrf_hdr = {"X-CSRF-Token": csrf}

        # Invalid auth_mode rejected with 400
        bad_mode = c_client.post(
            "/setup/api/actions/connect-chatgpt",
            headers=csrf_hdr,
            json={
                "tunnel_id": "tunnel_1234567890abcdef1234567890abcdef",
                "runtime_api_key": "rk_valid_length_runtime_key_test_1234567890",
                "auth_mode": "anonymous_owner",
            },
        )
        assert bad_mode.status_code == 400
        assert bad_mode.json()["error"] == "invalid_auth_mode"

        # Omitting auth_mode defaults cleanly to auth_none (local Private Tunnel safe default)
        default_conn = c_client.post(
            "/setup/api/actions/connect-chatgpt",
            headers=csrf_hdr,
            json={
                "tunnel_id": "tunnel_1234567890abcdef1234567890abcdef",
                "runtime_api_key": "rk_valid_length_runtime_key_test_1234567890",
            },
        )
        assert default_conn.status_code == 200
        assert default_conn.json()["summary"]["auth_mode"] == "auth_none"
        assert default_conn.json()["summary"]["auto_reconfigure"] is False

        # Explicit oauth mode is still honored (and detects existing tunnel -> auto_reconfigure is True)
        first_conn = c_client.post(
            "/setup/api/actions/connect-chatgpt",
            headers=csrf_hdr,
            json={
                "tunnel_id": "tunnel_1234567890abcdef1234567890abcdef",
                "runtime_api_key": "rk_valid_length_runtime_key_test_1234567890",
                "auth_mode": "oauth",
            },
        )
        assert first_conn.status_code == 200
        assert first_conn.json()["summary"]["auth_mode"] == "oauth"
        assert first_conn.json()["summary"]["auto_reconfigure"] is True

        # Reconfigure omitting auth_mode also defaults to auth_none
        second_conn = c_client.post(
            "/setup/api/actions/reconfigure-tunnel",
            headers=csrf_hdr,
            json={
                "tunnel_id": "tunnel_abcdef1234567890abcdef1234567890",
                "runtime_api_key": "rk_valid_length_runtime_key_test_9876543210",
            },
        )
        assert second_conn.status_code == 200
        assert second_conn.json()["summary"]["auth_mode"] == "auth_none"
        assert second_conn.json()["summary"]["auto_reconfigure"] is True

        # Verify Open-SetupCenter.cmd exists, has strict CRLF line endings, and is packaged in Build-ReleasePackage.ps1
        cmd_path = PROJECT_ROOT / "Open-SetupCenter.cmd"
        assert cmd_path.is_file()
        raw = cmd_path.read_bytes()
        assert b"\r\n" in raw
        assert raw.count(b"\r\n") == raw.count(b"\n")
        assert "--open-setup" in raw.decode("utf-8")
        builder = (PROJECT_ROOT / "scripts" / "Build-ReleasePackage.ps1").read_text(encoding="utf-8")
        assert '"Open-SetupCenter.cmd"' in builder
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


def test_run_winowner_live_setup_center_tunnel_hooks_and_ttl_validation():
    """Verify live Run-WinOwnerMcp.py tunnel hooks execute Connect-ChatGPT.ps1 without leaking secrets and fail closed."""
    import importlib.util
    import types

    runner_path = PROJECT_ROOT / "scripts" / "Run-WinOwnerMcp.py"
    spec = importlib.util.spec_from_file_location("run_winowner_mcp_mod", runner_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    td = Path(tempfile.mkdtemp(prefix="test_live_tunnel_hooks_"))
    try:
        (td / "scripts").mkdir(parents=True, exist_ok=True)
        (td / "scripts" / "Connect-ChatGPT.ps1").write_text("# stub\n", encoding="utf-8")
        secret_key = "rk_live_hook_super_secret_runtime_key_1234567890"
        tid = "tunnel_1234567890abcdef1234567890abcdef"
        captured: list[dict[str, Any]] = []

        def _fake_ok_runner(argv, **kwargs):
            captured.append({"argv": list(argv), "env": dict(kwargs.get("env") or {})})
            return types.SimpleNamespace(
                returncode=0,
                stdout="TUNNEL_SETUP_PASS: runtime ready; ChatGPT tool call NOT verified.\n",
                stderr="",
            )

        # 1. Initial connect (no yaml, no existing task) -> passes -Port, -TunnelId, -AuthMode, -NoBrowser, NO -Reconfigure
        hooks = mod.build_setup_center_action_hooks(
            td,
            19768,
            subprocess_runner=_fake_ok_runner,
            task_exists_fn=lambda _p: False,
        )
        res1 = hooks["connect_chatgpt"]({"tunnel_id": tid, "runtime_api_key": secret_key, "auth_mode": "oauth", "reconfigure": False})
        assert res1["status"] == "TUNNEL_SETUP_PASS"
        assert res1["readyz_ok"] is True
        assert res1["auto_reconfigure"] is False
        assert len(captured) == 1
        argv1 = " ".join(captured[0]["argv"])
        assert "-Port 19768" in argv1
        assert f"-TunnelId '{tid}'" in argv1
        assert "-AuthMode 'oauth'" in argv1
        assert "-NoBrowser" in argv1
        assert "-Reconfigure" not in argv1
        assert secret_key not in argv1
        assert captured[0]["env"].get("WINOWNER_SETUP_RUNTIME_API_KEY") == secret_key
        assert "Remove-Item Env:WINOWNER_SETUP_RUNTIME_API_KEY" in argv1

        # 2. Auto-detects existing winowner.yaml -> automatically appends -Reconfigure
        (td / "runtime" / "tunnel").mkdir(parents=True, exist_ok=True)
        (td / "runtime" / "tunnel" / "winowner.yaml").write_text("config_version: 1\n", encoding="utf-8")
        res2 = hooks["connect_chatgpt"]({"tunnel_id": tid, "runtime_api_key": secret_key, "auth_mode": "auth_none", "reconfigure": False})
        assert res2["auto_reconfigure"] is True
        argv2 = " ".join(captured[1]["argv"])
        assert "-AuthMode 'auth_none'" in argv2
        assert "-Reconfigure" in argv2
        assert secret_key not in argv2

        # 3. Non-zero exit from Connect-ChatGPT.ps1 raises RuntimeError with secret scrubbed -> HTTP 400 in Setup Center (never fake PASS)
        def _fake_fail_runner(argv, **kwargs):
            return types.SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=f"Tunnel doctor failed for key {secret_key} on port 19768",
            )

        fail_hooks = mod.build_setup_center_action_hooks(
            td,
            19768,
            subprocess_runner=_fake_fail_runner,
            task_exists_fn=lambda _p: True,
        )
        with pytest.raises(RuntimeError) as exc_info:
            fail_hooks["reconfigure_tunnel"]({"tunnel_id": tid, "runtime_api_key": secret_key, "auth_mode": "oauth", "reconfigure": True})
        assert secret_key not in str(exc_info.value)
        assert "Connect-ChatGPT.ps1 failed" in str(exc_info.value)

        # Verify Setup Center returns HTTP 400 (never fake TUNNEL_SETUP_PASS) when live hook fails, and rejects non-numeric ttl_seconds
        env = _build_dual_surface_env(profile="read_only")
        try:
            c_app = create_web_console_app(
                gateway=env["gw"],
                owner_password=TEST_CONSOLE_SECRET,
                oauth_persistence=env["adapter_client"].app.state.oauth_persistence,
                oauth_signing_key=TEST_OAUTH_SIGNING_KEY,
                owner_admin_token=TEST_OWNER_ADMIN_TOKEN,
                db_path=env["paths"].oauth_db_path,
                setup_action_hooks=fail_hooks,
            )
            c_client = TestClient(c_app)
            csrf = _login_setup_center(c_client)
            fail_resp = c_client.post(
                "/setup/api/actions/connect-chatgpt",
                headers={"X-CSRF-Token": csrf},
                json={"tunnel_id": tid, "runtime_api_key": secret_key, "auth_mode": "oauth"},
            )
            assert fail_resp.status_code == 400
            assert fail_resp.json()["error"] == "tunnel_setup_failed"
            assert secret_key not in fail_resp.text
            assert "TUNNEL_SETUP_PASS" not in fail_resp.text

            bad_ttl = c_client.post(
                "/setup/api/local-handoff/issue",
                headers={"X-MCP-Owner-Admin-Token": TEST_OWNER_ADMIN_TOKEN},
                json={"ttl_seconds": "not-a-number"},
            )
            assert bad_ttl.status_code == 400
            assert bad_ttl.json()["error"] == "INVALID_TTL_SECONDS"
        finally:
            shutil.rmtree(env["td"], ignore_errors=True)

        # 4. PowerShell-style flags (-Port / -ConsolePort) normalize cleanly for Open-SetupCenter.cmd
        assert mod._normalize_cli_argv(["--open-setup", "-Port", "19768", "-ConsolePort", "19767"]) == [
            "--open-setup", "--port", "19768", "--console-port", "19767"
        ]
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_fluent_four_step_wizard_zero_jargon_and_zero_innerhtml_anywhere():
    """Phase B/C/D: Verify Fluent Light 4-step wizard, zero banned jargon on Steps 1-4, and zero innerHTML anywhere in setup_center.py."""
    import re

    setup_src = (PROJECT_ROOT / "mcp-gateway" / "setup_center.py").read_text(encoding="utf-8")
    # Hard security invariant: zero innerHTML (even in comments) and zero localStorage.setItem anywhere in setup_center.py
    assert "innerHTML" not in setup_src
    assert "localStorage.setItem" not in setup_src

    env = _build_dual_surface_env(profile="read_only")
    try:
        html = env["console_client"].get("/setup").text

        # Fluent Light Design System tokens & layout geometry
        for token in (
            "--canvas: #f3f3f3",
            "--surface: #ffffff",
            "--accent: #0067c0",
            "max-width: 560px",
            'id="lang-toggle-btn"',
            'id="btn-advanced-settings"',
            'id="stepper-container"',
            'id="wizard-main"',
            'id="step-view-1"',
            'id="step-view-2"',
            'id="step-view-3"',
            'id="step-view-4"',
            'id="advanced-drawer"',
            'class="drawer-backdrop hidden"',
            'data-ui-state="loading"',
            ":focus-visible",
            "prefers-reduced-motion",
            "Chuẩn bị máy tính của bạn",
            "Preparing your computer",
            "Kết nối máy tính với ChatGPT",
            "Connect your computer to ChatGPT",
            "Cho phép ChatGPT sử dụng máy tính",
            "Allow ChatGPT to use your computer",
            "Máy tính của bạn đã kết nối!",
            "Your computer is connected!",
            "Chỉ xem (mặc định)",
            "Cho phép thao tác khi tôi xác nhận",
            "Kết nối đang được thiết lập...",
            "Chưa thể kết nối. Vui lòng thử lại.",
            "https://chatgpt.com",
            "Cài đặt nâng cao",
            "Advanced Settings",
        ):
            assert token in html, f"Missing expected Fluent wizard marker: {token}"

        # Verify Steps 1-4 main wizard markup (<main id="wizard-main">...</main>) has ZERO banned jargon or <pre> dumps
        m = re.search(r'<main[^>]*id="wizard-main"[^>]*>(.*?)</main>', html, re.S)
        assert m is not None, "Could not locate <main id='wizard-main'>"
        wizard_markup = m.group(1)
        banned_in_main_steps = (
            "127.0.0.1",
            "8767",
            "8768",
            "19768",
            "HTTP 401",
            "OAuth DB",
            "OAuth 2.1",
            "PKCE",
            "Client ID",
            "Bearer token",
            "Runtime state",
            "WinOwnerMCP-",
            "WinOwnerTunnel-",
            "agent_system_status",
            "delegation_id",
            "TTL: 3600s",
            "LOCAL_READY",
            "CHATGPT_CONNECTED",
            "OWNER_VERIFIED",
            "OWNER_CONTROL_READY",
            "read_only",
            "owner_full_control",
            "<pre",
        )
        for bad in banned_in_main_steps:
            assert bad not in wizard_markup, f"Banned technical jargon leaked into main Steps 1-4 markup: {bad}"

        # Verify A1 JS fixes: API key cleared ONLY after apiCall succeeds (never before), plus 403 CSRF auto-resync & focus hook
        submit_fn_match = re.search(r"async function submitConnectChatGPT\(isReconfigure\)\s*\{(.*?)\n    \}", html, re.S)
        assert submit_fn_match is not None
        submit_fn_body = submit_fn_match.group(1)
        api_call_pos = submit_fn_body.find("await apiCall(ep, 'POST'")
        key_wipe_pos = submit_fn_body.find("keyEl.value = ''")
        assert api_call_pos != -1 and key_wipe_pos != -1
        assert key_wipe_pos > api_call_pos, "keyEl.value must ONLY be wiped AFTER await apiCall() succeeds!"
        # Default Private Tunnel auth_mode must be auth_none on Step 2 UI, Advanced Reconfigure, and JS submitConnectChatGPT
        assert 'id="mode-radio-auth-none" value="auth_none" checked' in html
        assert 'id="mode-radio-oauth" value="oauth" />' in html
        assert '<option value="auth_none" selected>' in html
        assert "let authMode = 'auth_none';" in submit_fn_body
        assert 'id="s2-error-alert" class="callout danger hidden" role="alert"' in html
        assert "res.status === 403" in html
        assert "window.addEventListener('focus'" in html
        assert "visibilitychange" in html

        # Verify all 9 distinct UI states exist in JS / HTML
        for state_name in (
            "loading",
            "connecting",
            "connection_failed",
            "waiting_authorization",
            "revoked",
            "expired",
            "emergency_stopped",
            "offline",
            "reconnecting",
        ):
            assert state_name in html, f"Missing UI state handling for: {state_name}"

        # Verify inline <script> has zero JS SyntaxError (e.g. unescaped newline in Python triple-quoted string)
        script_m = re.search(r"<script>(.*?)</script>", html, re.S)
        assert script_m is not None, "Missing inline <script> block in /setup"
        js_code = script_m.group(1)
        assert "join('\n')" not in js_code
        node_bin = shutil.which("node")
        if node_bin:
            import subprocess

            js_tmp = Path(env["td"]) / "setup_center_check.js"
            js_tmp.write_text(js_code, encoding="utf-8")
            chk = subprocess.run([node_bin, "--check", str(js_tmp)], capture_output=True, text=True, timeout=15)
            assert chk.returncode == 0, f"Inline JS syntax error in /setup: {chk.stderr}"

        # Verify Advanced Settings drawer contains all required diagnostic & maintenance controls
        drawer_m = re.search(r'id="advanced-drawer".*?<script>', html, re.S)
        assert drawer_m is not None
        drawer_html = drawer_m.group(0)
        for adv_id in (
            'id="val-local-service"',
            'id="val-tunnel"',
            'id="val-chatgpt"',
            'id="val-owner"',
            'id="val-permissions"',
            'id="val-version"',
            'id="adv-logs-box"',
            'id="btn-adv-repair"',
            'id="btn-adv-updates"',
            'id="btn-adv-rollback"',
            'id="btn-submit-reconfig"',
            'id="btn-adv-revoke"',
            'id="btn-adv-estop"',
            'id="btn-adv-logout"',
            'id="btn-adv-uninstall"',
        ):
            assert adv_id in drawer_html, f"Missing Advanced Settings control: {adv_id}"
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


def test_nine_lifecycle_ui_states_logs_redaction_rollback_and_live_probes():
    """Phase D/E + A4/A5: Verify backend ui_state transitions (incl. expired/revoked/estop/offline/reconnecting), GET /setup/api/logs redaction, POST /setup/api/actions/rollback, and live probe freshness."""
    env = _build_dual_surface_env(profile="owner_full_control")
    try:
        a_client = env["adapter_client"]
        gw = env["gw"]
        ws = env["ws"]
        pers = a_client.app.state.oauth_persistence

        live_state = {
            "local_ok": True,
            "tunnel": {
                "connected": False,
                "configured": False,
                "readyz_ok": False,
                "status_code": "TUNNEL_NOT_CONFIGURED",
            },
        }
        rollback_calls: list[dict[str, Any]] = []

        def _fake_connect(payload: dict[str, Any]) -> dict[str, Any]:
            live_state["tunnel"] = {
                "connected": True,
                "configured": True,
                "readyz_ok": True,
                "status_code": "TUNNEL_READY",
            }
            return {
                "action": "connect_chatgpt",
                "tunnel_id": payload["tunnel_id"],
                "auth_mode": payload["auth_mode"],
                "status": "TUNNEL_SETUP_PASS",
                "readyz_ok": True,
            }

        def _fake_rollback(payload: dict[str, Any]) -> dict[str, Any]:
            rollback_calls.append(dict(payload))
            return {
                "action": "rollback",
                "status": "TUNNEL_ROLLBACK_PASS",
                "rollback_verified": True,
                "secrets_preserved": True,
                "message_en": "Restored verified tunnel backup.",
                "message_vi": "Đã khôi phục bản sao lưu tunnel đã xác minh.",
            }

        c_app = create_web_console_app(
            gateway=gw,
            owner_password=TEST_CONSOLE_SECRET,
            oauth_persistence=pers,
            oauth_signing_key=TEST_OAUTH_SIGNING_KEY,
            owner_admin_token=TEST_OWNER_ADMIN_TOKEN,
            db_path=env["paths"].oauth_db_path,
            setup_action_hooks={
                "local_service_probe": lambda: live_state["local_ok"],
                "tunnel_probe": lambda: dict(live_state["tunnel"]),
                "connect_chatgpt": _fake_connect,
                "rollback": _fake_rollback,
            },
        )
        c_client = TestClient(c_app)
        csrf = _login_setup_center(c_client)
        csrf_hdr = {"X-CSRF-Token": csrf}

        # 1. Offline state when live local_service_probe flips False
        live_state["local_ok"] = False
        st_off = c_client.get("/setup/api/state").json()
        assert st_off["state"] == "LOCAL_ERROR"
        assert st_off["ui_state"] == "offline"

        # Restore local_ok -> live probe immediately reflects Ready (never masked by sticky override)
        live_state["local_ok"] = True
        st_ready = c_client.get("/setup/api/state").json()
        assert st_ready["state"] == "LOCAL_READY"
        assert st_ready["ui_state"] == "setup_connect"

        # 2. Uppercase Tunnel ID normalized + live tunnel probe reflects subsequent disconnection (no sticky override!)
        r_conn = c_client.post(
            "/setup/api/actions/connect-chatgpt",
            headers=csrf_hdr,
            json={
                "tunnel_id": "TUNNEL_1234567890ABCDEF1234567890ABCDEF",
                "runtime_api_key": "rk_uppercase_test_secret_never_logged_12345678",
                "auth_mode": "oauth",
            },
        )
        assert r_conn.status_code == 200, r_conn.text
        assert r_conn.json()["summary"]["tunnel_id"] == "tunnel_1234567890abcdef1234567890abcdef"
        assert r_conn.json()["state_snapshot"]["status_table"]["tunnel"] == "Connected"

        # Simulate tunnel dropping into reconnecting, then connection_failed -> verify-connection reflects live probe!
        live_state["tunnel"] = {"connected": False, "configured": True, "readyz_ok": False, "status_code": "TUNNEL_NOT_READY"}
        st_reconn = c_client.post("/setup/api/actions/verify-connection", headers=csrf_hdr, json={}).json()["state_snapshot"]
        assert st_reconn["status_table"]["tunnel"] == "Disconnected"
        assert st_reconn["ui_state"] == "reconnecting"

        live_state["tunnel"] = {"connected": False, "configured": True, "readyz_ok": False, "status_code": "TUNNEL_FAILED"}
        st_fail = c_client.get("/setup/api/state").json()
        assert st_fail["ui_state"] == "connection_failed"

        live_state["tunnel"] = {"connected": True, "configured": True, "readyz_ok": True, "status_code": "TUNNEL_READY"}

        # 3. Waiting authorization -> Owner ready -> Emergency Stop -> Revoked -> Expired
        cid, tok = _register_oauth_client_and_token(a_client, "State Machine Client")
        mcp_hdr = _mcp_init_and_headers(a_client, tok)
        a_client.post(
            "/mcp",
            headers=mcp_hdr,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "agent_read_file", "arguments": {"workspace_root": str(ws), "path": "hello.txt"}},
            },
        )
        ch = a_client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok}"},
            json={"requested_permission_level": "SCOPED", "requested_workspaces": [str(ws)], "requested_ttl_seconds": 60},
        ).json()
        st_wait = c_client.get("/setup/api/state").json()
        assert st_wait["ui_state"] == "waiting_authorization"

        c_client.post(
            f"/setup/api/pairing/challenges/{ch['challenge_id']}/approve",
            headers=csrf_hdr,
            json={"approved_permission_level": "SCOPED", "approved_workspaces": [str(ws)], "approved_ttl_seconds": 60, "activate_profile": True},
        )
        st_ctrl = c_client.get("/setup/api/state").json()
        assert st_ctrl["state"] == "OWNER_CONTROL_READY"
        assert st_ctrl["ui_state"] == "ready_owner_control"

        # Emergency Stop -> emergency_stopped
        c_client.post("/setup/api/actions/revoke-access", headers=csrf_hdr, json={"revoke_all": False, "engage_emergency_stop": True})
        assert c_client.get("/setup/api/state").json()["ui_state"] == "emergency_stopped"
        c_client.post("/setup/api/actions/revoke-access", headers=csrf_hdr, json={"revoke_all": False, "reset_emergency_stop": True})

        # Simulate expired delegation & grant in SQLite -> ui_state == "expired"
        past_ts = time.time() - 10.0
        with pers._db() as conn:
            conn.execute("UPDATE owner_durable_delegations SET expires_at = ? WHERE owner_id = 'local_owner'", (past_ts,))
            conn.execute("UPDATE owner_capability_grants SET expires_at = ? WHERE owner_id = 'local_owner'", (past_ts,))
        st_exp = c_client.get("/setup/api/state").json()
        assert st_exp["last_delegation_status"] == "expired"
        assert st_exp["ui_state"] == "expired"

        # Revoke access -> ui_state == "revoked"
        c_client.post("/setup/api/actions/revoke-access", headers=csrf_hdr, json={"revoke_all": True})
        st_rev = c_client.get("/setup/api/state").json()
        assert st_rev["ui_state"] == "revoked"

        # 4. POST /setup/api/actions/rollback wired to real hook
        rb_res = c_client.post("/setup/api/actions/rollback", headers=csrf_hdr, json={"scope": "tunnel"})
        assert rb_res.status_code == 200, rb_res.text
        assert rb_res.json()["summary"]["status"] == "TUNNEL_ROLLBACK_PASS"
        assert len(rollback_calls) == 1

        # Without rollback hook -> fail closed HTTP 400 (never fake rollback PASS)
        no_rb_client = env["console_client"]
        no_rb_csrf = _login_setup_center(no_rb_client)
        rb_missing = no_rb_client.post("/setup/api/actions/rollback", headers={"X-CSRF-Token": no_rb_csrf}, json={})
        assert rb_missing.status_code == 400
        assert rb_missing.json()["error"] == "no_rollback_snapshot"

        # 5. GET /setup/api/logs requires auth and scrubs all secret patterns & runtime keys
        unauth_logs = TestClient(c_app).get("/setup/api/logs")
        assert unauth_logs.status_code == 401

        secret_probe = "rk_super_secret_leaked_in_audit_999888777"
        gw.policy.audit.log_event(
            tool_name="log_redaction_probe",
            tier="SECURITY",
            decision="ALLOW",
            reason=f"Bearer {TEST_TUNNEL_BEARER} key={secret_probe} adm={TEST_OWNER_ADMIN_TOKEN}",
        )
        logs_res = c_client.get("/setup/api/logs?limit=20")
        assert logs_res.status_code == 200, logs_res.text
        logs_text = logs_res.text
        assert logs_res.json()["count"] >= 1
        assert secret_probe not in logs_text
        assert TEST_TUNNEL_BEARER not in logs_text
        assert TEST_OWNER_ADMIN_TOKEN not in logs_text
        assert TEST_OAUTH_SIGNING_KEY not in logs_text
        assert "[REDACTED]" in logs_text
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)


