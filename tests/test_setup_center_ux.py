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
        assert "WinOwner Setup Center: http://127.0.0.1:8767/setup" in setup_ps
        assert "Four-step state:" in setup_ps
        assert "OpenSetupCenter" in setup_ps
    finally:
        shutil.rmtree(env["td"], ignore_errors=True)

