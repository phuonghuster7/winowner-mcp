"""Phase 2 P0 Verifiable Owner Pairing & End-to-End Authorization Consistency Test Suite."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_GW_DIR = _PROJECT_ROOT / "mcp-gateway"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_GW_DIR) not in sys.path:
    sys.path.insert(0, str(_GW_DIR))

from cloud_autonomous_adapter import create_cloud_autonomous_adapter_app
from gateway_server import MCPGatewayServer
from http_server import (
    _issue_oauth_token,
    _verify_oauth_token,
    resolve_oauth_signing_key_path,
    resolve_owner_admin_token_path,
)
from policy import GatewayPolicy
from runtime.contracts import RuntimeStatePaths


def _create_test_env(profile: str = "owner_full_control"):
    td = tempfile.mkdtemp(prefix="test_owner_pairing_p0_")
    ws = Path(td) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "readme.txt").write_text("hello owner pairing\n", encoding="utf-8")
    outside_ws = Path(td) / "outside_secret_area"
    outside_ws.mkdir(parents=True, exist_ok=True)
    (outside_ws / "outside.txt").write_text("outside data\n", encoding="utf-8")

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
                            "agent_create_file",
                            "agent_apply_patch",
                            "agent_run_command",
                            "agent_run_tests",
                            "agent_git_status",
                            "agent_git_diff",
                        ],
                        "allowed_commands": ["python *", "pytest *", "git status*", "git diff*"],
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
    if hasattr(agent_handler.broker, "add_preapproved_root"):
        agent_handler.broker.add_preapproved_root(str(ws.resolve()))
    elif isinstance(getattr(agent_handler.broker, "preapproved_roots", None), set):
        agent_handler.broker.preapproved_roots.add(str(ws.resolve()))
    elif isinstance(getattr(agent_handler.broker, "preapproved_roots", None), list):
        agent_handler.broker.preapproved_roots.append(str(ws.resolve()))

    from runtime.coding_tools import CodingToolHandler

    gw._coding_tool_handler = CodingToolHandler(
        agent_handler.store,
        agent_handler.broker,
        trusted_config_path=trusted_cfg,
        audit_path=Path(td) / "coding_audit.db",
    )

    connector_secret = "shared_tunnel_bearer_secret_p0_test_987654321"
    signing_key = "independent_oauth_signing_key_p0_test_1234567890"
    owner_admin_token = "mcp_oadm_independent_local_owner_admin_secret_12345"

    app = create_cloud_autonomous_adapter_app(
        gateway=gw,
        caller_auth_token=connector_secret,
        oauth_signing_key=signing_key,
        owner_admin_token=owner_admin_token,
        db_path=paths.oauth_db_path,
        enable_direct_coding=True,
    )
    client = TestClient(app)
    return td, ws, outside_ws, paths, gw, app, client, connector_secret, signing_key, owner_admin_token


def _register_and_mint_oauth_client(client: TestClient, client_name: str = "ChatGPT Web Connector") -> tuple[str, str, str]:
    reg = client.post(
        "/oauth/register",
        json={
            "client_name": client_name,
            "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
            "grant_types": ["authorization_code", "refresh_token", "client_credentials"],
            "scope": "mcp:autonomous",
        },
    )
    assert reg.status_code == 201, reg.text
    reg_data = reg.json()
    cid = reg_data["client_id"]
    csec = reg_data["client_secret"]

    tok = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": csec,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    access_token = tok.json()["access_token"]
    return cid, csec, access_token


def _mcp_init(client: TestClient, bearer_token: str) -> dict[str, str]:
    hdrs = {"Authorization": f"Bearer {bearer_token}", "Mcp-Protocol-Version": "2025-03-26"}
    r = client.post(
        "/mcp",
        headers=hdrs,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
    )
    assert r.status_code == 200, r.text
    sid = r.headers["Mcp-Session-Id"]
    return {**hdrs, "Mcp-Session-Id": sid}


def _mcp_call(client: TestClient, headers: dict[str, str], name: str, arguments: dict) -> dict:
    r = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_p0_owner_pairing_approve_e2e_and_status_consistency():
    """Verify full verifiable owner pairing approve lifecycle and unified effective-permission snapshot."""
    td, ws, outside_ws, paths, gw, app, client, connector_secret, signing_key, owner_admin_token = _create_test_env(
        profile="owner_full_control"
    )
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": owner_admin_token}

        # 1. Initial status is LOCAL_READY (no verified read tool call, no paired owner)
        st0 = client.get("/oauth/owner/status", headers=admin_hdr)
        assert st0.status_code == 200, st0.text
        s0 = st0.json()
        assert s0["state"] == "LOCAL_READY"
        assert s0["chatgpt_connection"]["verified_read_tool_call"] is False
        assert s0["owner_identity"]["verified"] is False
        assert s0["effective_permissions"]["level"] == "READ"
        assert s0["effective_permissions"]["owner_control_ready"] is False

        # 2. Register OAuth 2.1 client and obtain standard mcp:autonomous token
        cid, _csec, access_token = _register_and_mint_oauth_client(client, "ChatGPT Verifiable Client A")
        mcp_hdr = _mcp_init(client, access_token)

        # 3. Perform a real read-only tool call -> status advances to CHATGPT_CONNECTED
        read_res = _mcp_call(client, mcp_hdr, "agent_read_file", {"workspace_root": str(ws), "path": "readme.txt"})
        assert read_res["isError"] is False

        st1 = client.get("/oauth/owner/status", headers=admin_hdr).json()
        assert st1["state"] == "CHATGPT_CONNECTED"
        assert st1["chatgpt_connection"]["verified_read_tool_call"] is True
        assert st1["chatgpt_connection"]["last_verified_tool"] == "agent_read_file"
        assert st1["chatgpt_connection"]["last_auth_mode"] == "oauth_2_1"
        assert st1["chatgpt_connection"]["supports_verifiable_owner_identity"] is True
        assert st1["owner_identity"]["verified"] is False

        # 4. Before owner pairing approval, owner tool call is denied
        pre_ps = _mcp_call(
            client,
            mcp_hdr,
            "agent_powershell_execute",
            {"command": "Write-Output 'PRE_PAIR_DENIED'", "cwd": str(ws)},
        )
        assert pre_ps["isError"] is True

        # 5. Client initiates verifiable owner pairing request (no manual client_id entry or secret pasting)
        pair_req = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "requested_permission_level": "OWNER",
                "requested_workspaces": [str(ws)],
                "requested_ttl_seconds": 3600,
                "allow_destructive": False,
            },
        )
        assert pair_req.status_code == 201, pair_req.text
        ch = pair_req.json()
        challenge_id = ch["challenge_id"]
        assert ch["status"] == "PENDING"
        assert ch["client_id"] == cid
        assert ch["verifiable_identity"] is True
        assert len(ch["verification_code"]) == 9 and "-" in ch["verification_code"]

        # 6. Local Windows owner lists pending challenges
        list_ch = client.get("/oauth/pairing/challenges?status=PENDING", headers=admin_hdr)
        assert list_ch.status_code == 200
        pending_list = list_ch.json()["challenges"]
        assert any(c["challenge_id"] == challenge_id and c["client_id"] == cid for c in pending_list)

        # 7. Local Windows owner explicitly approves the challenge (bound strictly to cid)
        app_resp = client.post(
            f"/oauth/pairing/challenges/{challenge_id}/approve",
            headers=admin_hdr,
            json={
                "approved_permission_level": "SCOPED",
                "approved_workspaces": [str(ws)],
                "approved_ttl_seconds": 1800,
                "allow_destructive": False,
            },
        )
        assert app_resp.status_code == 200, app_resp.text
        app_data = app_resp.json()
        assert app_data["ok"] is True
        assert app_data["client_id"] == cid
        assert app_data["delegation_id"].startswith("odd_")
        assert app_data["capability_grant_id"].startswith("ocg_")
        assert "access_token" not in app_data  # Never exposes raw owner token for pasting

        # 8. Unified status & /health now agree on OWNER_CONTROL_READY
        st2 = client.get("/oauth/owner/status", headers=admin_hdr).json()
        assert st2["state"] == "OWNER_CONTROL_READY"
        assert st2["owner_identity"]["verified"] is True
        assert st2["owner_identity"]["client_id"] == cid
        assert st2["effective_permissions"]["owner_control_ready"] is True
        assert st2["effective_permissions"]["level"] in ("SCOPED", "OWNER")

        h2 = client.get("/health").json()
        assert h2["owner_identity_verified"] is True
        assert h2["active_owner_delegations_count"] == 1
        assert h2["chatgpt_read_tool_verified"] is True

        # 9. Paired client immediately executes owner PowerShell and file write within approved workspace
        post_ps = _mcp_call(
            client,
            mcp_hdr,
            "agent_powershell_execute",
            {"command": "Write-Output 'PAIRED_OWNER_EXEC_OK'", "cwd": str(ws)},
        )
        assert post_ps["isError"] is False, post_ps
        ps_payload = json.loads(post_ps["content"][0]["text"])
        assert "PAIRED_OWNER_EXEC_OK" in ps_payload["stdout"]

        write_res = _mcp_call(
            client,
            mcp_hdr,
            "agent_write_file",
            {
                "workspace_root": str(ws),
                "path": "paired_out.txt",
                "content": "written by paired owner\n",
                "expected_sha256": "missing",
            },
        )
        assert write_res["isError"] is False, write_res
        assert (ws / "paired_out.txt").read_text(encoding="utf-8") == "written by paired owner\n"

        # 10. Out-of-scope workspace (outside_ws) is strictly denied for this scoped owner delegation
        out_ps = _mcp_call(
            client,
            mcp_hdr,
            "agent_powershell_execute",
            {"command": "Write-Output 'OUTSIDE_SCOPE'", "cwd": str(outside_ws)},
        )
        assert out_ps["isError"] is True

        out_read = _mcp_call(
            client,
            mcp_hdr,
            "agent_read_file",
            {"workspace_root": str(outside_ws), "path": "outside.txt"},
        )
        assert out_read["isError"] is True
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_p0_owner_pairing_deny_expiry_and_anti_replay():
    """Verify owner deny, challenge TTL expiration, and single-use anti-replay enforcement."""
    td, ws, outside_ws, paths, gw, app, client, connector_secret, signing_key, owner_admin_token = _create_test_env(
        profile="owner_full_control"
    )
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": owner_admin_token}
        cid, _csec, access_token = _register_and_mint_oauth_client(client, "Client Deny Replay Test")
        mcp_hdr = _mcp_init(client, access_token)

        # 1. Owner Deny flow via MCP tool agent_request_owner_pairing
        mcp_pair = _mcp_call(
            client,
            mcp_hdr,
            "agent_request_owner_pairing",
            {"requested_permission_level": "OWNER", "requested_workspaces": [str(ws)]},
        )
        assert mcp_pair["isError"] is False
        ch1 = json.loads(mcp_pair["content"][0]["text"])
        ch1_id = ch1["challenge_id"]

        deny_resp = client.post(
            f"/oauth/pairing/challenges/{ch1_id}/deny",
            headers=admin_hdr,
            json={"reason": "operator_rejected_request"},
        )
        assert deny_resp.status_code == 200
        assert deny_resp.json()["status"] == "DENIED"

        # Replaying / approving a denied challenge must fail closed with 409
        replay_after_deny = client.post(
            f"/oauth/pairing/challenges/{ch1_id}/approve",
            headers=admin_hdr,
            json={},
        )
        assert replay_after_deny.status_code == 409
        assert replay_after_deny.json()["error_code"] == "CHALLENGE_ALREADY_USED"

        # Client still has no owner privileges after deny
        ps_denied = _mcp_call(
            client,
            mcp_hdr,
            "agent_powershell_execute",
            {"command": "Write-Output 'AFTER_DENY'", "cwd": str(ws)},
        )
        assert ps_denied["isError"] is True

        # 2. Challenge Expiry flow
        exp_req = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"requested_workspaces": [str(ws)], "challenge_ttl_seconds": 1.0},
        )
        assert exp_req.status_code == 201
        ch_exp_id = exp_req.json()["challenge_id"]
        time.sleep(1.15)

        approve_expired = client.post(
            f"/oauth/pairing/challenges/{ch_exp_id}/approve",
            headers=admin_hdr,
            json={},
        )
        assert approve_expired.status_code == 409
        assert approve_expired.json()["error_code"] == "CHALLENGE_EXPIRED"

        # 3. Single-use Anti-Replay on Approved challenge
        ok_req = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"requested_workspaces": [str(ws)]},
        )
        ch_ok_id = ok_req.json()["challenge_id"]
        first_approve = client.post(f"/oauth/pairing/challenges/{ch_ok_id}/approve", headers=admin_hdr, json={})
        assert first_approve.status_code == 200

        second_approve = client.post(f"/oauth/pairing/challenges/{ch_ok_id}/approve", headers=admin_hdr, json={})
        assert second_approve.status_code == 409
        assert second_approve.json()["error_code"] == "CHALLENGE_ALREADY_USED"

        deny_after_approve = client.post(f"/oauth/pairing/challenges/{ch_ok_id}/deny", headers=admin_hdr, json={})
        assert deny_after_approve.status_code == 409
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_p0_unverifiable_tunnel_client_and_credential_forgery_rejected():
    """Verify Authentication: None / shared tunnel bearer cannot request pairing, approve challenges, or inherit owner privileges."""
    td, ws, outside_ws, paths, gw, app, client, connector_secret, signing_key, owner_admin_token = _create_test_env(
        profile="owner_full_control"
    )
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": owner_admin_token}
        tunnel_hdr = _mcp_init(client, connector_secret)

        # 1. Shared tunnel bearer cannot request owner pairing via HTTP endpoint
        http_pair = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {connector_secret}"},
            json={"requested_permission_level": "OWNER"},
        )
        assert http_pair.status_code == 403
        assert http_pair.json()["error_code"] == "UNVERIFIABLE_CLIENT_IDENTITY"

        # 2. Shared tunnel bearer cannot request owner pairing via MCP tool
        mcp_pair = _mcp_call(
            client,
            tunnel_hdr,
            "agent_request_owner_pairing",
            {"requested_permission_level": "OWNER"},
        )
        assert mcp_pair["isError"] is True
        assert "UNVERIFIABLE_CLIENT_IDENTITY" in mcp_pair["content"][0]["text"]

        # 3. Shared tunnel bearer cannot call local owner administration endpoints
        for method, path in (
            ("GET", "/oauth/pairing/challenges"),
            ("GET", "/oauth/owner/status"),
            ("POST", "/oauth/owner/revoke"),
            ("POST", "/oauth/pairing/challenges/pair_fake/approve"),
        ):
            r = client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {connector_secret}", "X-MCP-Owner-Admin-Token": connector_secret},
                json={"owner_admin_token": connector_secret} if method == "POST" else None,
            )
            assert r.status_code == 403, f"Tunnel bearer was not rejected with 403 on {method} {path}: {r.status_code}"

        # 4. Pair OAuth Client A as owner; verify OAuth Client B and tunnel bearer Client C do NOT inherit owner access
        cid_a, _, tok_a = _register_and_mint_oauth_client(client, "Client A Verified Owner")
        cid_b, _, tok_b = _register_and_mint_oauth_client(client, "Client B Separate OAuth")

        # Client B cannot request pairing pretending to be Client A
        spoof_req = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok_b}"},
            json={"client_id": cid_a},
        )
        assert spoof_req.status_code == 403

        ch_a = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={"requested_workspaces": [str(ws)]},
        ).json()
        app_a = client.post(
            f"/oauth/pairing/challenges/{ch_a['challenge_id']}/approve",
            headers=admin_hdr,
            json={"approved_workspaces": [str(ws)]},
        ).json()
        grant_a_id = app_a["capability_grant_id"]

        hdr_a = _mcp_init(client, tok_a)
        hdr_b = _mcp_init(client, tok_b)

        # Client A succeeds
        res_a = _mcp_call(client, hdr_a, "agent_powershell_execute", {"command": "Write-Output 'A_OK'", "cwd": str(ws)})
        assert res_a["isError"] is False

        # Client B (other OAuth client on same tunnel) is denied even if passing Client A's grant_id
        res_b = _mcp_call(
            client,
            hdr_b,
            "agent_powershell_execute",
            {"command": "Write-Output 'B_HIJACK'", "cwd": str(ws), "capability_grant_id": grant_a_id},
        )
        assert res_b["isError"] is True

        # Client C (shared tunnel bearer on same tunnel) is denied and cannot write files
        res_c = _mcp_call(
            client,
            tunnel_hdr,
            "agent_powershell_execute",
            {"command": "Write-Output 'C_HIJACK'", "cwd": str(ws), "capability_grant_id": grant_a_id},
        )
        assert res_c["isError"] is True

        res_c_write = _mcp_call(
            client,
            tunnel_hdr,
            "agent_write_file",
            {
                "workspace_root": str(ws),
                "path": "readme.txt",
                "content": "overwritten by tunnel bearer",
                "expected_sha256": hashlib.sha256((ws / "readme.txt").read_bytes()).hexdigest(),
            },
        )
        assert res_c_write["isError"] is True
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_p0_1_dcr_client_hijack_and_p0_2_p0_3_revocation_and_expiry():
    """Verify P0-1 DCR anti-hijack, P0-2 no expired delegation resurrection, and P0-3 coding tool revocation cutoff."""
    td, ws, outside_ws, paths, gw, app, client, connector_secret, signing_key, owner_admin_token = _create_test_env(
        profile="owner_full_control"
    )
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": owner_admin_token}
        cid_a, csec_a, tok_a = _register_and_mint_oauth_client(client, "Legitimate Client A")

        # P0-1: Unauthenticated attacker cannot overwrite existing client_id via POST /oauth/register
        hijack_resp = client.post(
            "/oauth/register",
            json={
                "client_id": cid_a,
                "client_name": "Attacker Overwrite",
                "redirect_uris": ["https://evil.example.com/cb"],
            },
        )
        assert hijack_resp.status_code == 403

        # P0-1: Reserved client_id and invalid scopes are rejected
        reserved_resp = client.post("/oauth/register", json={"client_id": "chatgpt_web_owner_client"})
        assert reserved_resp.status_code == 403
        bad_scope_resp = client.post("/oauth/register", json={"scope": "mcp:autonomous mcp:root_exploit"})
        assert bad_scope_resp.status_code == 400

        # Pair Client A and verify active operation
        ch = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={"requested_workspaces": [str(ws)]},
        ).json()
        app_res = client.post(
            f"/oauth/pairing/challenges/{ch['challenge_id']}/approve",
            headers=admin_hdr,
            json={"approved_workspaces": [str(ws)]},
        ).json()
        del_id = app_res["delegation_id"]
        hdr_a = _mcp_init(client, tok_a)

        w_ok = _mcp_call(
            client,
            hdr_a,
            "agent_write_file",
            {
                "workspace_root": str(ws),
                "path": "before_revoke.txt",
                "content": "ok\n",
                "expected_sha256": "missing",
            },
        )
        assert w_ok["isError"] is False, w_ok

        # P0-2: Expire the durable delegation and child grants in SQLite and verify NO auto-resurrection occurs
        now = time.time()
        persistence = app.state.oauth_persistence
        with persistence._db() as conn:
            row = conn.execute("SELECT * FROM owner_durable_delegations WHERE delegation_id = ?", (del_id,)).fetchone()
            d = dict(row)
            past_exp = now - 10.0
            new_sig = persistence._sign_owner_durable_delegation(
                signing_key,
                d["delegation_id"],
                d["owner_id"],
                d["client_id"],
                d["allowed_tools"],
                d["allowed_paths"],
                float(d["issued_at"]),
                past_exp,
                int(d["total_operation_budget"]),
                int(d["allow_destructive_operations"]),
                d["audit_identity"],
            )
            conn.execute(
                "UPDATE owner_durable_delegations SET expires_at = ?, signature = ? WHERE delegation_id = ?",
                (past_exp, new_sig, del_id),
            )
            conn.execute("UPDATE owner_capability_grants SET expires_at = ? WHERE delegation_id = ?", (past_exp, del_id))

        _, pay_a, _ = _verify_oauth_token(tok_a, signing_key, set())
        renewed = persistence.resolve_or_renew_owner_grant_for_token(
            secret=signing_key,
            owner_id="local_owner",
            client_id=cid_a,
            oauth_token_jti=pay_a["jti"],
        )
        assert renewed is None, "P0-2 FAILED: Expired durable delegation auto-resurrected!"

        # P0-3: Expired owner delegation cannot write files or run commands via coding_tools
        w_exp = _mcp_call(
            client,
            hdr_a,
            "agent_write_file",
            {
                "workspace_root": str(ws),
                "path": "after_expiry.txt",
                "content": "should fail\n",
                "expected_sha256": "missing",
            },
        )
        assert w_exp["isError"] is True
        assert not (ws / "after_expiry.txt").exists()

        # Re-pair Client A, then test full revocation via POST /oauth/owner/revoke
        ch2 = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={"requested_workspaces": [str(ws)]},
        ).json()
        client.post(f"/oauth/pairing/challenges/{ch2['challenge_id']}/approve", headers=admin_hdr, json={})

        rev_resp = client.post("/oauth/owner/revoke", headers=admin_hdr, json={"client_id": cid_a})
        assert rev_resp.status_code == 200
        assert rev_resp.json()["revoked"] is True
        assert rev_resp.json()["owner_status"]["owner_identity"]["verified"] is False
        assert rev_resp.json()["owner_status"]["effective_permissions"]["level"] == "READ"

        # Revoked client token is immediately rejected
        after_rev = client.post(
            "/mcp",
            headers=hdr_a,
            json={
                "jsonrpc": "2.0",
                "id": 55,
                "method": "tools/call",
                "params": {
                    "name": "agent_write_file",
                    "arguments": {"workspace_root": str(ws), "path": "x.txt", "content": "x", "expected_sha256": "missing"},
                },
            },
        )
        assert after_rev.status_code == 403 or after_rev.json().get("result", {}).get("isError") is True
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_p0_4_and_p0_5_read_only_profile_and_trusted_workspace_auto_grant_boundary():
    """Verify P0-4 read_only profile hard boundary and P0-5 no Trusted Workspace auto-grant for READ callers."""
    td, ws, outside_ws, paths, gw, app, client, connector_secret, signing_key, owner_admin_token = _create_test_env(
        profile="read_only"
    )
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": owner_admin_token}
        cid, _, tok = _register_and_mint_oauth_client(client, "ReadOnly Profile Client")

        # Pair client while server is in read_only profile
        ch = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok}"},
            json={"requested_workspaces": [str(ws)]},
        ).json()
        client.post(f"/oauth/pairing/challenges/{ch['challenge_id']}/approve", headers=admin_hdr, json={})

        # Even though owner identity is verified, effective permissions under read_only profile MUST be READ
        st = client.get("/oauth/owner/status", headers=admin_hdr).json()
        assert st["profile"] == "read_only"
        assert st["owner_identity"]["verified"] is True
        assert st["effective_permissions"]["level"] == "READ"
        assert st["effective_permissions"]["owner_control_ready"] is False

        hdr = _mcp_init(client, tok)

        # tools/list under read_only profile must NOT expose 34 owner tools
        t_list = client.post("/mcp", headers=hdr, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).json()
        tool_names = [t["name"] for t in t_list["result"]["tools"]]
        assert "agent_powershell_execute" not in tool_names

        # Direct tools/call on owner exec, desktop, and write tools must fail closed under read_only
        ps_ro = _mcp_call(client, hdr, "agent_powershell_execute", {"command": "Write-Output 'RO'", "cwd": str(ws)})
        assert ps_ro["isError"] is True

        win_ro = _mcp_call(client, hdr, "agent_list_windows", {})
        assert win_ro["isError"] is True

        write_ro = _mcp_call(
            client,
            hdr,
            "agent_write_file",
            {"workspace_root": str(ws), "path": "ro.txt", "content": "ro", "expected_sha256": "missing"},
        )
        assert write_ro["isError"] is True

        # P0-5: agent_submit_task targeting a configured Trusted Workspace must NOT auto-grant to READ caller / read_only profile
        sub_ro = _mcp_call(
            client,
            hdr,
            "agent_submit_task",
            {
                "objective": "Run git status in trusted workspace",
                "workspace_root": str(ws),
                "allowed_tools": ["read_file", "git_status"],
            },
        )
        assert sub_ro["isError"] is False, sub_ro
        sub_data = json.loads(sub_ro["content"][0]["text"])
        assert sub_data["status"] == "AUTHORIZATION_PENDING"
        assert sub_data["requires_owner_approval"] is True
        assert sub_data.get("grant_id") is None
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_p0_6_and_p0_7_multipath_secret_exfiltration_and_owner_control_tools():
    """Verify P0-6 multi-path & secret file validation and P0-7 owner control tool authorization."""
    td, ws, outside_ws, paths, gw, app, client, connector_secret, signing_key, owner_admin_token = _create_test_env(
        profile="owner_full_control"
    )
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": owner_admin_token}
        cid, _, tok = _register_and_mint_oauth_client(client, "P0-6 P0-7 Client")
        ch = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok}"},
            json={"requested_workspaces": [str(ws)]},
        ).json()
        client.post(
            f"/oauth/pairing/challenges/{ch['challenge_id']}/approve",
            headers=admin_hdr,
            json={"approved_workspaces": [str(ws)]},
        )
        hdr_owner = _mcp_init(client, tok)

        # P0-6a: agent_visual_verify with save_path inside ws AND verify_path pointing to outside_ws -> DENY
        vv_outside = _mcp_call(
            client,
            hdr_owner,
            "agent_visual_verify",
            {
                "save_path": str(ws / "shot.png"),
                "verify_path": str(outside_ws / "outside.txt"),
            },
        )
        assert vv_outside["isError"] is True

        # P0-6b: agent_visual_verify with verify_path pointing to a sensitive file (.env / oauth_signing.key) inside ws -> DENY
        secret_file = ws / "oauth_signing.key"
        secret_file.write_text("TOP_SECRET_KEY_VALUE", encoding="utf-8")
        vv_secret = _mcp_call(
            client,
            hdr_owner,
            "agent_visual_verify",
            {
                "save_path": str(ws / "shot.png"),
                "verify_path": str(secret_file),
            },
        )
        assert vv_secret["isError"] is True
        assert "TOP_SECRET_KEY_VALUE" not in vv_secret["content"][0]["text"]

        # P0-6c: agent_powershell_execute omitting cwd must run inside grant's allowed_paths ([ws]), NEVER escaping to PROJECT_ROOT
        ps_no_cwd = _mcp_call(
            client,
            hdr_owner,
            "agent_powershell_execute",
            {"command": "(Get-Location).Path"},
        )
        assert ps_no_cwd["isError"] is False, ps_no_cwd
        no_cwd_out = json.loads(ps_no_cwd["content"][0]["text"])
        assert str(ws.resolve()).lower() in no_cwd_out["stdout"].lower()
        assert str(_PROJECT_ROOT.resolve()).lower() not in no_cwd_out["stdout"].lower()

        # P0-6d: Explicit cwd outside grant allowed_paths (PROJECT_ROOT or outside_ws) is strictly DENIED
        ps_escape_root = _mcp_call(
            client,
            hdr_owner,
            "agent_powershell_execute",
            {"command": "Get-Location", "cwd": str(_PROJECT_ROOT)},
        )
        assert ps_escape_root["isError"] is True

        # P0-7: Non-owner static bearer on owner_full_control server cannot invoke OWNER_CONTROL_AUTONOMOUS_TOOLS
        hdr_static = _mcp_init(client, connector_secret)
        for ctrl_tool, ctrl_args in (
            ("agent_approve_task", {"task_id": "t_1"}),
            ("agent_emergency_stop", {"reason": "unauthenticated_stop"}),
            ("agent_task_cancel", {"task_id": "t_1"}),
        ):
            r_ctrl = _mcp_call(client, hdr_static, ctrl_tool, ctrl_args)
            assert r_ctrl["isError"] is True, f"P0-7 FAILED: Static bearer invoked {ctrl_tool}!"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_p1_1_p1_2_refresh_rotation_key_path_and_cli_commands():
    """Verify P1-1 key path resolution without double-nesting, P1-2 single-use refresh token rotation, and CLI pairing commands."""
    td, ws, outside_ws, paths, gw, app, client, connector_secret, signing_key, owner_admin_token = _create_test_env(
        profile="owner_full_control"
    )
    try:
        # P1-1: When db_path is inside a 'secrets' folder, resolve_oauth_signing_key_path must not double-nest secrets/secrets/
        secrets_db = Path(td) / "runtime" / "secrets" / "oauth.db"
        secrets_db.parent.mkdir(parents=True, exist_ok=True)
        resolved_sk = resolve_oauth_signing_key_path(db_path=secrets_db)
        resolved_adm = resolve_owner_admin_token_path(db_path=secrets_db)
        assert resolved_sk == secrets_db.parent / "oauth_signing.key"
        assert resolved_adm == secrets_db.parent / "owner_admin.key"

        # P1-2: Refresh token rotation is single-use
        ref_tok = _issue_oauth_token(
            client_id="refresh_rotation_client",
            secret=signing_key,
            scope="mcp:autonomous",
            ttl_seconds=3600,
            owner_id="local_owner",
            typ="refresh",
        )["access_token"]

        r1 = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": ref_tok, "client_id": "refresh_rotation_client"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r1.status_code == 200, r1.text
        new_ref_tok = r1.json()["refresh_token"]
        assert new_ref_tok != ref_tok

        # Replaying consumed refresh_token must fail with 400 invalid_grant
        r_replay = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": ref_tok, "client_id": "refresh_rotation_client"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r_replay.status_code == 400

        # CLI status & challenge commands against isolated DB
        cli_script = _PROJECT_ROOT / "scripts" / "manage_owner_delegation.py"
        sk_file = Path(td) / "oauth_signing.key"
        sk_file.write_text(signing_key + "\n", encoding="utf-8")

        cid, _, tok = _register_and_mint_oauth_client(client, "CLI Challenge Client")
        ch = client.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok}"},
            json={"requested_workspaces": [str(ws)]},
        ).json()

        lpc_proc = subprocess.run(
            [
                sys.executable,
                str(cli_script),
                "--oauth-db",
                str(paths.oauth_db_path),
                "--tasks-db",
                str(paths.oauth_db_path),
                "--signing-key-file",
                str(sk_file),
                "latest-pending-challenge",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert lpc_proc.returncode == 0, lpc_proc.stderr
        lpc_data = json.loads(lpc_proc.stdout)
        assert lpc_data["ok"] is True
        assert lpc_data["challenge"]["challenge_id"] == ch["challenge_id"]

        bundle_out = Path(td) / "owner_bundle.json"
        app_proc = subprocess.run(
            [
                sys.executable,
                str(cli_script),
                "--oauth-db",
                str(paths.oauth_db_path),
                "--tasks-db",
                str(paths.oauth_db_path),
                "--signing-key-file",
                str(sk_file),
                "approve-challenge",
                "--challenge-id",
                ch["challenge_id"],
                "--token-out",
                str(bundle_out),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert app_proc.returncode == 0, app_proc.stderr
        assert bundle_out.is_file()

        st_proc = subprocess.run(
            [
                sys.executable,
                str(cli_script),
                "--oauth-db",
                str(paths.oauth_db_path),
                "--tasks-db",
                str(paths.oauth_db_path),
                "--signing-key-file",
                str(sk_file),
                "status",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert st_proc.returncode == 0, st_proc.stderr
        st_data = json.loads(st_proc.stdout)
        assert st_data["owner_identity"]["verified"] is True
        assert st_data["owner_identity"]["client_id"] == cid
    finally:
        shutil.rmtree(td, ignore_errors=True)
