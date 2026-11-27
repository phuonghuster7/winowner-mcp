"""Track UX-1 Restart Persistence & Auto-Profile Readiness Verification Suite.

Proves across full independent server/persistence restarts sharing the same SQLite DB:
1. Approved OAuth 2.1 pairing session (client, delegation, rolling grant, MCP session, chatgpt_connection,
   and HMAC-signed owner-approved runtime profile) survives full server restart so the approved client
   resumes approved scoped operations immediately without re-pairing or pasting tokens (both with existing
   access token and after refresh_token / client_credentials rotation).
2. Unverified / Authentication: None (`static_bearer`) and different OAuth clients remain strictly denied
   after restart (zero cross-client inheritance).
3. Revoked delegation stays revoked after restart (401/403 + profile fails closed back to read_only + audit log).
4. Expired delegation stays expired after restart (never auto-resurrects, profile fails closed to read_only).
5. Tampered `owner_runtime_state` SQLite row fails HMAC verification and falls back strictly to `read_only`.
6. Emergency Stop stays active across full restart until explicitly reset by the owner.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_GW_DIR = _PROJECT_ROOT / "mcp-gateway"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_GW_DIR) not in sys.path:
    sys.path.insert(0, str(_GW_DIR))

from cloud_autonomous_adapter import create_cloud_autonomous_adapter_app
from gateway_server import MCPGatewayServer
from http_server import _verify_oauth_token
from policy import GatewayPolicy
from runtime.coding_tools import CodingToolHandler
from runtime.contracts import RuntimeStatePaths


class _IsolatedRestartRig:
    """Spawns independent server/adapter instances backed by the same isolated temp directory and SQLite DB."""

    def __init__(self) -> None:
        self.td = tempfile.mkdtemp(prefix="test_ux_restart_")
        self.ws = Path(self.td) / "workspace"
        self.ws.mkdir(parents=True, exist_ok=True)
        (self.ws / "probe.txt").write_text("restart persistence probe\n", encoding="utf-8")
        self.outside_ws = Path(self.td) / "outside_dir"
        self.outside_ws.mkdir(parents=True, exist_ok=True)
        (self.outside_ws / "secret_note.txt").write_text("outside scope\n", encoding="utf-8")

        self.paths = RuntimeStatePaths.create_isolated(self.td)
        self.trusted_cfg = Path(self.td) / "trusted_workspaces.json"
        self.trusted_cfg.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "trusted_mode_enabled": True,
                    "trusted_workspaces": [
                        {
                            "workspace_root": str(self.ws),
                            "include_subdirectories": True,
                            "allowed_tools": [
                                "agent_workspace_list",
                                "agent_read_file",
                                "agent_write_file",
                                "agent_create_file",
                                "agent_apply_patch",
                                "agent_run_command",
                                "agent_git_status",
                            ],
                            "allowed_commands": ["python *", "pytest *", "git status*"],
                            "duration_seconds": 3600,
                            "max_tool_calls": 100,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.connector_secret = "ux1_shared_tunnel_bearer_secret_9876543210"
        self.signing_key = "ux1_independent_oauth_signing_key_1234567890"
        self.owner_admin_token = "mcp_oadm_ux1_local_owner_admin_secret_12345"

    def boot_instance(
        self,
        *,
        profile: str = "read_only",
        restore_approved_profile: bool = True,
    ) -> tuple[MCPGatewayServer, object, TestClient]:
        policy = GatewayPolicy(
            profile=profile,
            audit_log_path=self.paths.audit_log_path,
            emergency_stop_file=self.paths.emergency_stop_file,
        )
        existing_write = list(getattr(policy, "write_roots", []) or [])
        if self.ws.resolve() not in existing_write:
            policy.write_roots = [*existing_write, self.ws.resolve()]

        gw = MCPGatewayServer(policy=policy, runtime_paths=self.paths)
        agent_handler = gw.get_agent_tool_handler()
        agent_handler.trusted_config_path = self.trusted_cfg
        if hasattr(agent_handler.broker, "add_preapproved_root"):
            agent_handler.broker.add_preapproved_root(str(self.ws.resolve()))

        gw._coding_tool_handler = CodingToolHandler(
            agent_handler.store,
            agent_handler.broker,
            trusted_config_path=self.trusted_cfg,
            audit_path=Path(self.td) / "coding_audit.db",
        )
        app = create_cloud_autonomous_adapter_app(
            gateway=gw,
            caller_auth_token=self.connector_secret,
            oauth_signing_key=self.signing_key,
            owner_admin_token=self.owner_admin_token,
            db_path=self.paths.oauth_db_path,
            enable_direct_coding=True,
            restore_approved_profile=restore_approved_profile,
        )
        return gw, app, TestClient(app)

    def cleanup(self) -> None:
        shutil.rmtree(self.td, ignore_errors=True)


def _register_and_mint_oauth(client: TestClient, name: str) -> tuple[str, str, str]:
    reg = client.post(
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
    tok = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": cid, "client_secret": csec},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return cid, csec, tok.json()["access_token"]


def _mcp_init(client: TestClient, token: str) -> dict[str, str]:
    hdrs = {"Authorization": f"Bearer {token}", "Mcp-Protocol-Version": "2025-03-26"}
    r = client.post(
        "/mcp",
        headers=hdrs,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
    )
    assert r.status_code == 200, r.text
    return {**hdrs, "Mcp-Session-Id": r.headers["Mcp-Session-Id"]}


def _mcp_call(client: TestClient, headers: dict[str, str], name: str, arguments: dict) -> dict:
    r = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_ux1_approved_oauth_session_and_profile_survive_full_restart():
    """Approved OAuth 2.1 pairing + HMAC-signed profile survive full server restart without re-pairing or pasting tokens."""
    rig = _IsolatedRestartRig()
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": rig.owner_admin_token}

        # --- Boot #1: Fresh install starts strictly in read_only ---
        gw1, app1, client1 = rig.boot_instance(profile="read_only", restore_approved_profile=True)
        assert gw1.policy.profile == "read_only"

        cid_a, csec_a, tok_a = _register_and_mint_oauth(client1, "ChatGPT Approved Client A")
        cid_b, _csec_b, tok_b = _register_and_mint_oauth(client1, "ChatGPT Unapproved Client B")
        hdr_a = _mcp_init(client1, tok_a)

        # Verify real read tool call -> persisted in chatgpt_connection_state
        r_read = _mcp_call(client1, hdr_a, "agent_read_file", {"workspace_root": str(rig.ws), "path": "probe.txt"})
        assert r_read["isError"] is False

        # Request owner pairing and approve with single-step activate_profile=True
        ch = client1.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={"requested_permission_level": "SCOPED", "requested_workspaces": [str(rig.ws)], "requested_ttl_seconds": 3600},
        )
        assert ch.status_code == 201, ch.text
        ch_id = ch.json()["challenge_id"]

        app_res = client1.post(
            f"/oauth/pairing/challenges/{ch_id}/approve",
            headers=admin_hdr,
            json={
                "approved_permission_level": "SCOPED",
                "approved_workspaces": [str(rig.ws)],
                "approved_ttl_seconds": 3600,
                "activate_profile": True,
            },
        )
        assert app_res.status_code == 200, app_res.text
        assert app_res.json()["activated_profile"] == "owner_full_control"
        assert gw1.policy.profile == "owner_full_control"

        # Execute approved write before restart
        w1 = _mcp_call(
            client1,
            hdr_a,
            "agent_write_file",
            {
                "workspace_root": str(rig.ws),
                "path": "before_restart.txt",
                "content": "boot1 ok\n",
                "expected_sha256": "missing",
            },
        )
        assert w1["isError"] is False, w1

        # --- Boot #2: Full independent server/adapter restart sharing same SQLite DB ---
        # Even though startup default is profile="read_only", restore_approved_profile=True
        # restores HMAC-verified owner_full_control because an active valid delegation exists.
        gw2, app2, client2 = rig.boot_instance(profile="read_only", restore_approved_profile=True)
        assert gw2.policy.profile == "owner_full_control"

        # 1. GET /oauth/owner/status accurately reflects restored session, delegation, expiry, and chatgpt_connection
        st2 = client2.get("/oauth/owner/status", headers=admin_hdr)
        assert st2.status_code == 200, st2.text
        s2 = st2.json()
        assert s2["state"] == "OWNER_CONTROL_READY"
        assert s2["session_restored_from_db"] is True
        assert s2["chatgpt_connection"]["verified_read_tool_call"] is True
        assert s2["chatgpt_connection"]["last_verified_tool"] == "agent_read_file"
        assert s2["owner_identity"]["verified"] is True
        assert s2["owner_identity"]["client_id"] == cid_a
        assert s2["owner_identity"]["remaining_seconds"] > 0
        assert s2["persisted_profile_state"]["valid"] is True
        assert s2["persisted_profile_state"]["effective_profile"] == "owner_full_control"

        # 2. Existing MCP session & access token work immediately after restart WITHOUT re-pairing or initialize
        ps2 = _mcp_call(
            client2,
            hdr_a,
            "agent_powershell_execute",
            {"command": "Write-Output 'SURVIVED_RESTART_OK'", "cwd": str(rig.ws)},
        )
        assert ps2["isError"] is False, ps2
        assert "SURVIVED_RESTART_OK" in json.loads(ps2["content"][0]["text"])["stdout"]

        # 3. Fresh access token minted after restart (new JTI via client_credentials) also resumes seamlessly
        tok_a_fresh = client2.post(
            "/oauth/token",
            data={"grant_type": "client_credentials", "client_id": cid_a, "client_secret": csec_a},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert tok_a_fresh.status_code == 200, tok_a_fresh.text
        hdr_a_fresh = _mcp_init(client2, tok_a_fresh.json()["access_token"])
        w2 = _mcp_call(
            client2,
            hdr_a_fresh,
            "agent_write_file",
            {
                "workspace_root": str(rig.ws),
                "path": "after_restart.txt",
                "content": "boot2 ok\n",
                "expected_sha256": "missing",
            },
        )
        assert w2["isError"] is False, w2
        assert (rig.ws / "after_restart.txt").read_text(encoding="utf-8") == "boot2 ok\n"

        # 4. Scoped boundary still holds after restart (outside_ws strictly denied)
        out_ps = _mcp_call(
            client2,
            hdr_a_fresh,
            "agent_powershell_execute",
            {"command": "Write-Output 'OUTSIDE'", "cwd": str(rig.outside_ws)},
        )
        assert out_ps["isError"] is True

        # 5. Unverified / Authentication: None (static_bearer) AND different OAuth Client B remain strictly denied after restart
        hdr_static = _mcp_init(client2, rig.connector_secret)
        hdr_b = _mcp_init(client2, tok_b)
        for bad_hdr, label in ((hdr_static, "static_bearer"), (hdr_b, "other_oauth_client")):
            bad_ps = _mcp_call(
                client2,
                bad_hdr,
                "agent_powershell_execute",
                {"command": "Write-Output 'HIJACK'", "cwd": str(rig.ws)},
            )
            assert bad_ps["isError"] is True, f"{label} gained owner exec after restart!"
            bad_w = _mcp_call(
                client2,
                bad_hdr,
                "agent_write_file",
                {
                    "workspace_root": str(rig.ws),
                    "path": f"hijack_{label}.txt",
                    "content": "pwned",
                    "expected_sha256": "missing",
                },
            )
            assert bad_w["isError"] is True, f"{label} gained write access after restart!"
    finally:
        rig.cleanup()


def test_ux1_revoked_delegation_stays_revoked_and_profile_resets_across_restart():
    """Revoked delegation stays strictly revoked across restart, resets profile to read_only, and writes audit entry."""
    rig = _IsolatedRestartRig()
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": rig.owner_admin_token}
        gw1, app1, client1 = rig.boot_instance(profile="read_only", restore_approved_profile=True)

        cid_a, csec_a, tok_a = _register_and_mint_oauth(client1, "Client Revoke Restart")
        ch = client1.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={"requested_workspaces": [str(rig.ws)]},
        ).json()
        app_res = client1.post(
            f"/oauth/pairing/challenges/{ch['challenge_id']}/approve",
            headers=admin_hdr,
            json={"approved_workspaces": [str(rig.ws)], "activate_profile": True},
        )
        assert app_res.status_code == 200
        assert gw1.policy.profile == "owner_full_control"

        # Owner revokes access
        rev = client1.post(
            "/oauth/owner/revoke",
            headers=admin_hdr,
            json={"client_id": cid_a, "reason": "owner_revoked_before_restart"},
        )
        assert rev.status_code == 200, rev.text
        assert rev.json()["revoked"] is True
        assert gw1.policy.profile == "read_only"

        # Full restart -> must boot strictly in read_only and keep delegation/token revoked
        gw2, app2, client2 = rig.boot_instance(profile="read_only", restore_approved_profile=True)
        assert gw2.policy.profile == "read_only"

        st2 = client2.get("/oauth/owner/status", headers=admin_hdr).json()
        assert st2["profile"] == "read_only"
        assert st2["owner_identity"]["verified"] is False
        assert st2["owner_identity"]["revoked"] is True
        assert st2["effective_permissions"]["level"] == "READ"

        # Old token is rejected with 403 after restart
        old_tok_resp = client2.post(
            "/mcp",
            headers={"Authorization": f"Bearer {tok_a}", "Mcp-Protocol-Version": "2025-03-26"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        )
        assert old_tok_resp.status_code in (401, 403)

        # Even if client mints a fresh token via client_credentials, it has ZERO owner/write authority after restart
        fresh_tok = client2.post(
            "/oauth/token",
            data={"grant_type": "client_credentials", "client_id": cid_a, "client_secret": csec_a},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).json()["access_token"]
        hdr_fresh = _mcp_init(client2, fresh_tok)
        ps_after_rev = _mcp_call(
            client2,
            hdr_fresh,
            "agent_powershell_execute",
            {"command": "Write-Output 'REVOKED'", "cwd": str(rig.ws)},
        )
        assert ps_after_rev["isError"] is True

        # Verify revocation was durably written to audit log
        audit_text = rig.paths.audit_log_path.read_text(encoding="utf-8")
        assert "oauth_owner_revoke" in audit_text
    finally:
        rig.cleanup()


def test_ux1_expired_delegation_and_tampered_runtime_state_fail_closed_across_restart():
    """Expired delegation never resurrects across restart, and tampered owner_runtime_state falls back to read_only."""
    rig = _IsolatedRestartRig()
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": rig.owner_admin_token}
        gw1, app1, client1 = rig.boot_instance(profile="read_only", restore_approved_profile=True)

        cid_a, csec_a, tok_a = _register_and_mint_oauth(client1, "Client Expiry Restart")
        ch = client1.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={"requested_workspaces": [str(rig.ws)]},
        ).json()
        app_res = client1.post(
            f"/oauth/pairing/challenges/{ch['challenge_id']}/approve",
            headers=admin_hdr,
            json={"approved_workspaces": [str(rig.ws)], "activate_profile": True},
        )
        assert app_res.status_code == 200
        del_id = app_res.json()["delegation_id"]

        # Backdate delegation & grants in SQLite to expired timestamp (valid signature, expired time)
        pers1 = app1.state.oauth_persistence
        past_exp = time.time() - 60.0
        with pers1._db() as conn:
            row = conn.execute("SELECT * FROM owner_durable_delegations WHERE delegation_id = ?", (del_id,)).fetchone()
            d = dict(row)
            sig = pers1._sign_owner_durable_delegation(
                rig.signing_key,
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
                (past_exp, sig, del_id),
            )
            conn.execute("UPDATE owner_capability_grants SET expires_at = ? WHERE delegation_id = ?", (past_exp, del_id))

        # Restart -> expired delegation must NOT resurrect and profile MUST fail closed to read_only
        gw2, app2, client2 = rig.boot_instance(profile="read_only", restore_approved_profile=True)
        assert gw2.policy.profile == "read_only"

        st2 = client2.get("/oauth/owner/status", headers=admin_hdr).json()
        assert st2["owner_identity"]["verified"] is False
        assert st2["owner_identity"]["expired"] is True
        assert st2["effective_permissions"]["level"] == "READ"
        assert st2["persisted_profile_state"]["effective_profile"] == "read_only"

        hdr_a = _mcp_init(client2, tok_a)
        ps_exp = _mcp_call(
            client2,
            hdr_a,
            "agent_powershell_execute",
            {"command": "Write-Output 'EXPIRED'", "cwd": str(rig.ws)},
        )
        assert ps_exp["isError"] is True

        # Also prove POST /oauth/owner/profile refuses elevating profile when zero active delegations exist
        prof_refuse = client2.post("/oauth/owner/profile", headers=admin_hdr, json={"profile": "owner_full_control"})
        assert prof_refuse.status_code == 403

        # Tamper directly with owner_runtime_state in SQLite -> HMAC check fails closed to read_only on restart
        with app2.state.oauth_persistence._db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO owner_runtime_state (
                    id, approved_profile, approved_by, delegation_id, client_id, updated_at, signature
                ) VALUES (1, 'owner_full_control', 'attacker', 'fake_del', 'fake_client', ?, 'forged_sig')
                """,
                (time.time(),),
            )
        gw3, _app3, _client3 = rig.boot_instance(profile="read_only", restore_approved_profile=True)
        assert gw3.policy.profile == "read_only"
    finally:
        rig.cleanup()


def test_ux1_emergency_stop_persists_across_full_restart_until_owner_resets():
    """Emergency Stop remains active across full server restart and blocks all owner operations until owner explicitly resets."""
    rig = _IsolatedRestartRig()
    try:
        admin_hdr = {"X-MCP-Owner-Admin-Token": rig.owner_admin_token}
        gw1, app1, client1 = rig.boot_instance(profile="read_only", restore_approved_profile=True)

        cid_a, _csec_a, tok_a = _register_and_mint_oauth(client1, "Client Estop Restart")
        ch = client1.post(
            "/oauth/pairing/request",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={"requested_workspaces": [str(rig.ws)]},
        ).json()
        app_res = client1.post(
            f"/oauth/pairing/challenges/{ch['challenge_id']}/approve",
            headers=admin_hdr,
            json={"approved_workspaces": [str(rig.ws)], "activate_profile": True},
        )
        assert app_res.status_code == 200

        # Engage Emergency Stop via owner API
        es1 = client1.post("/oauth/owner/emergency-stop", headers=admin_hdr, json={"reason": "ux1_reboot_estop_test"})
        assert es1.status_code == 200
        assert es1.json()["owner_status"]["emergency_stop"] is True

        # Restart server while Emergency Stop file exists -> MUST stay halted & in read_only!
        gw2, app2, client2 = rig.boot_instance(profile="read_only", restore_approved_profile=True)
        assert gw2.is_emergency_stop_active() is True
        assert gw2.policy.profile == "read_only"

        st2 = client2.get("/oauth/owner/status", headers=admin_hdr).json()
        assert st2["emergency_stop"] is True
        assert st2["effective_permissions"]["level"] == "READ"

        hdr_a = _mcp_init(client2, tok_a)
        ps_stopped = _mcp_call(
            client2,
            hdr_a,
            "agent_powershell_execute",
            {"command": "Write-Output 'SHOULD_HALT'", "cwd": str(rig.ws)},
        )
        assert ps_stopped["isError"] is True

        # Owner explicitly resets Emergency Stop and re-activates approved profile -> approved client resumes
        rs2 = client2.post("/oauth/owner/emergency-stop/reset", headers=admin_hdr, json={})
        assert rs2.status_code == 200
        assert gw2.is_emergency_stop_active() is False

        prof_ok = client2.post("/oauth/owner/profile", headers=admin_hdr, json={"profile": "owner_full_control"})
        assert prof_ok.status_code == 200
        assert gw2.policy.profile == "owner_full_control"

        ps_resumed = _mcp_call(
            client2,
            hdr_a,
            "agent_powershell_execute",
            {"command": "Write-Output 'RESUMED_AFTER_RESET'", "cwd": str(rig.ws)},
        )
        assert ps_resumed["isError"] is False, ps_resumed
        assert "RESUMED_AFTER_RESET" in json.loads(ps_resumed["content"][0]["text"])["stdout"]
    finally:
        rig.cleanup()
