"""Phase A Root Cause Backend & Pipeline Regression Tests (A1, A2, A3, A5).

Covers:
1. A1: Local handoff reuses existing valid loopback WebConsoleAuthSession and preserves csrf_token
   across multiple open browser tabs so Tab 1 never hits HTTP 403 Invalid CSRF token.
2. A2 & A3: Dynamic adapter port reporting in /health, /.well-known/oauth-protected-resource,
   real /readyz (200 ready / 503 degraded) on both cloud_autonomous_adapter and web_console,
   and Setup-WinOwner.ps1 using $effectiveConsolePort instead of hardcoded 8767.
3. A1 & A5: All 6 real Setup Center action hooks wired in scripts/Run-WinOwnerMcp.py
   (install_repair, connect_chatgpt, reconfigure_tunnel, check_updates, uninstall, rollback),
   uppercase tunnel_id normalization, secret scrubbing, and real PowerShell error extraction
   before WHAT TO DO NEXT: boilerplate footer.
4. A1-3c: Connect-ChatGPT.ps1 verified-runtime-hashes.json SHA-256 cache reuse & cutover.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import types
import urllib.error
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
from policy import GatewayPolicy
from runtime.contracts import RuntimeStatePaths
from web_console import create_web_console_app


def _load_run_winowner_module():
    spec = importlib.util.spec_from_file_location(
        "run_winowner_mcp_phase_a",
        _PROJECT_ROOT / "scripts" / "Run-WinOwnerMcp.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeHttpResponse:
    def __init__(self, status: int, body: bytes | str) -> None:
        self.status = status
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *args) -> None:
        return None


def test_handoff_reuses_valid_cookie_session_and_preserves_csrf(tmp_path: Path):
    """Opening a 2nd Setup Center tab via local handoff must reuse the active loopback session & CSRF token."""
    paths = RuntimeStatePaths.create_isolated(tmp_path)
    hook_calls: list[dict] = []

    def _fake_connect(payload: dict) -> dict:
        hook_calls.append({k: v for k, v in payload.items() if k != "runtime_api_key"})
        return {
            "action": "connect_chatgpt",
            "tunnel_id": payload["tunnel_id"],
            "auth_mode": payload["auth_mode"],
            "status": "TUNNEL_SETUP_PASS",
            "readyz_ok": True,
            "chatgpt_tool_call_verified": False,
        }

    app = create_web_console_app(
        profile="read_only",
        policy_path=_PROJECT_ROOT / "config" / "policy.example.json",
        db_path=paths.oauth_db_path,
        adapter_port=19768,
        setup_action_hooks={"connect_chatgpt": _fake_connect},
    )

    # Verify /readyz on web console returns 200 ready
    c_ready = TestClient(app)
    r_ready = c_ready.get("/readyz")
    assert r_ready.status_code == 200, r_ready.text
    ready_json = r_ready.json()
    assert ready_json["ready"] is True
    assert ready_json["service"] == "mode-b-owner-web-console"
    assert ready_json["adapter_port"] == 19768
    assert ready_json["db_ready"] is True

    # Tab 1 redeems handoff ticket #1
    client_tab1 = TestClient(app)
    t1 = app.state.issue_local_handoff_ticket(ttl_seconds=60.0)
    r1 = client_tab1.post(
        "/setup/api/local-handoff",
        json={"handoff_code": t1["handoff_code"]},
        headers={"Origin": "http://127.0.0.1:19767"},
    )
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    csrf_1 = d1["csrf_token"]
    ws_id_1 = d1["web_session_id"]

    # Tab 2 opens Setup Center via a fresh handoff ticket #2 (simulating Open-WinOwnerSetup.cmd)
    client_tab2 = TestClient(app)
    t2 = app.state.issue_local_handoff_ticket(ttl_seconds=60.0)
    r2 = client_tab2.post(
        "/setup/api/local-handoff",
        json={"handoff_code": t2["handoff_code"]},
        headers={"Origin": "http://127.0.0.1:19767"},
    )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["web_session_id"] == ws_id_1
    assert d2["csrf_token"] == csrf_1

    # Tab 1's subsequent POST with its original cookie + X-CSRF-Token must succeed (200, NEVER 403!)
    r_act = client_tab1.post(
        "/setup/api/actions/connect-chatgpt",
        json={
            "tunnel_id": "tunnel_0123456789abcdef0123456789abcdef",
            "runtime_api_key": "dummy_runtime_api_key_phase_a_1234567890",
            "auth_mode": "oauth",
        },
        headers={
            "Origin": "http://127.0.0.1:19767",
            "X-CSRF-Token": csrf_1,
        },
    )
    assert r_act.status_code == 200, r_act.text
    assert r_act.json()["ok"] is True
    assert len(hook_calls) == 1


def test_adapter_health_and_readyz_report_actual_custom_port(tmp_path: Path):
    """Adapter /health, /.well-known/oauth-protected-resource, and /readyz reflect the real bound port and degrade to 503."""
    paths = RuntimeStatePaths.create_isolated(tmp_path)
    policy = GatewayPolicy(
        profile="read_only",
        audit_log_path=paths.audit_log_path,
        emergency_stop_file=paths.emergency_stop_file,
    )
    gw = MCPGatewayServer(policy=policy, runtime_paths=paths)
    custom_port = 19768
    app = create_cloud_autonomous_adapter_app(
        gateway=gw,
        caller_auth_token="phase_a_adapter_fixture_" + "0000000001",
        oauth_signing_key="phase_a_signing_fixture_" + "0000000002",
        owner_admin_token="mcp_oadm_fixture_" + "0000000003",
        db_path=paths.oauth_db_path,
        port=custom_port,
    )
    client = TestClient(app)

    r_health = client.get("/health")
    assert r_health.status_code == 200, r_health.text
    h = r_health.json()
    assert h["status"] == "ok"
    assert h["port"] == custom_port

    r_wk = client.get("/.well-known/oauth-protected-resource")
    assert r_wk.status_code == 200, r_wk.text
    wk = r_wk.json()
    assert wk["resource_uri"] == f"http://127.0.0.1:{custom_port}/mcp"

    r_ready = client.get("/readyz")
    assert r_ready.status_code == 200, r_ready.text
    rd = r_ready.json()
    assert rd["ready"] is True
    assert rd["port"] == custom_port
    assert rd["local_service_ready"] is True
    assert rd["db_ready"] is True

    # Simulate broken SQLite persistence -> /readyz must return HTTP 503 with ready=False
    orig_db_fn = app.state.oauth_persistence._db

    def _broken_db():
        raise RuntimeError("simulated sqlite unavailable")

    app.state.oauth_persistence._db = _broken_db
    try:
        r_deg = client.get("/readyz")
        assert r_deg.status_code == 503, r_deg.text
        deg = r_deg.json()
        assert deg["ready"] is False
        assert deg["db_ready"] is False
    finally:
        app.state.oauth_persistence._db = orig_db_fn

    # Verify Setup-WinOwner.ps1 no longer hardcodes 8767 in Write-SetupResult / launcher
    setup_ps1 = (_PROJECT_ROOT / "scripts" / "Setup-WinOwner.ps1").read_text(encoding="utf-8")
    assert "$effectiveConsolePort" in setup_ps1
    assert "http://127.0.0.1:8767/setup" not in setup_ps1


def test_real_setup_center_action_hooks_no_mock_fallbacks(tmp_path: Path):
    """Run-WinOwnerMcp.py wires all 6 real hooks (install_repair, connect_chatgpt, reconfigure_tunnel, check_updates, uninstall, rollback)."""
    mod = _load_run_winowner_module()

    # Prepare isolated fake repo layout under tmp_path
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in ("Bootstrap-WindowsMcp.ps1", "Connect-ChatGPT.ps1", "Rollback-WindowsMcp.ps1"):
        (scripts_dir / name).write_text("# stub script for unit test\n", encoding="utf-8")
    (tmp_path / "runtime" / "secrets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "tunnel").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    secret_marker = tmp_path / "runtime" / "secrets" / "owner_admin.key"
    secret_marker.write_text("KEEP_OWNER_KEY", encoding="utf-8")
    policy_marker = tmp_path / "config" / "policy.json"
    policy_marker.write_text('{"default_profile": "read_only"}', encoding="utf-8")
    health_url_file = tmp_path / "runtime" / "tunnel" / "health-url.txt"
    health_url_file.write_text("http://127.0.0.1:19999\n", encoding="utf-8")

    # 1. Error extraction test: real [WinOwner] ERROR / doctor line extracted BEFORE WHAT TO DO NEXT footer, secret scrubbed
    secret_val = "dummy_proj_secret_value_phase_a_9876543210"
    noisy_stdout = (
        "[WinOwner] Step 1: checking local runtime...\n"
        f"[WinOwner] ERROR: doctor failed for key {secret_val}: control plane returned 401 unauthorized\n"
        "WHAT TO DO NEXT:\n"
        "1. Open ChatGPT\n"
        "2. Copy your Tunnel ID\n"
    )
    extracted = mod._extract_powershell_error_summary(
        noisy_stdout,
        "",
        1,
        secret_to_scrub=secret_val,
        default_prefix="Connect-ChatGPT.ps1",
    )
    assert "doctor failed" in extracted
    assert "WHAT TO DO NEXT" not in extracted
    assert secret_val not in extracted
    assert "[REDACTED]" in extracted

    # 2. Wire hooks with deterministic fake subprocess_runner & urlopen_fn
    invocations: list[list[str]] = []
    probe_state = {"healthy": True}
    github_mode = {"mode": "newer"}

    def _fake_subprocess(argv, **kwargs):
        invocations.append(list(argv))
        joined = " ".join(argv)
        if "Bootstrap-WindowsMcp.ps1" in joined and "-Remove" not in joined:
            probe_state["healthy"] = True
            return types.SimpleNamespace(returncode=0, stdout="LOCAL_INSTALL_PASS\n", stderr="")
        if "Bootstrap-WindowsMcp.ps1" in joined and "-Remove" in joined:
            return types.SimpleNamespace(returncode=0, stdout="TASK_REMOVED\n", stderr="")
        if "Connect-ChatGPT.ps1" in joined:
            assert secret_val not in joined  # secret must NEVER appear on CLI argv
            return types.SimpleNamespace(returncode=0, stdout="TUNNEL_SETUP_PASS\n", stderr="")
        if "Rollback-WindowsMcp.ps1" in joined:
            return types.SimpleNamespace(returncode=0, stdout="ROLLBACK_PASS\n", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="unexpected invocation")

    def _fake_urlopen(req_or_url, timeout=5.0):
        url = req_or_url.full_url if hasattr(req_or_url, "full_url") else str(req_or_url)
        if url.endswith("/health"):
            if not probe_state["healthy"]:
                raise urllib.error.URLError("connection refused")
            return _FakeHttpResponse(
                200,
                json.dumps(
                    {
                        "status": "ok",
                        "profile": "read_only",
                        "require_autonomous_auth": True,
                        "allow_loopback_tunnel_identity": False,
                    }
                ),
            )
        if url.endswith("/mcp"):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b""))
        if "api.github.com" in url:
            if github_mode["mode"] == "newer":
                return _FakeHttpResponse(
                    200,
                    json.dumps(
                        {
                            "tag_name": "v9.9.9",
                            "html_url": "https://github.com/phuonghuster7/winowner-mcp/releases/tag/v9.9.9",
                            "published_at": "2026-11-27T00:00:00Z",
                        }
                    ),
                )
            if github_mode["mode"] == "bad_tag":
                return _FakeHttpResponse(200, json.dumps({"tag_name": "main"}))
            raise urllib.error.URLError("offline")
        raise AssertionError(f"Unexpected URL: {url}")

    hooks = mod.build_setup_center_action_hooks(
        tmp_path,
        port=19768,
        subprocess_runner=_fake_subprocess,
        task_exists_fn=lambda _p: False,
        urlopen_fn=_fake_urlopen,
    )
    assert set(hooks.keys()) == {
        "install_repair",
        "connect_chatgpt",
        "reconfigure_tunnel",
        "check_updates",
        "uninstall",
        "rollback",
    }

    # 2a. install_repair when already healthy -> no bootstrap run needed
    res_ok = hooks["install_repair"]({})
    assert res_ok["status"] == "LOCAL_INSTALL_PASS"
    assert res_ok["repaired"] is False
    assert len(invocations) == 0

    # 2b. install_repair when down -> invokes Bootstrap-WindowsMcp.ps1 -NoPythonInstall -Port 19768 & re-probes
    probe_state["healthy"] = False
    res_rep = hooks["install_repair"]({})
    assert res_rep["status"] == "LOCAL_INSTALL_PASS"
    assert res_rep["repaired"] is True
    assert any("Bootstrap-WindowsMcp.ps1" in " ".join(c) and "-NoPythonInstall" in c for c in invocations)

    # 2c. connect_chatgpt normalizes uppercase tunnel_id
    res_conn = hooks["connect_chatgpt"](
        {
            "tunnel_id": "TUNNEL_ABCDEF0123456789ABCDEF0123456789",
            "runtime_api_key": secret_val,
            "auth_mode": "oauth",
        }
    )
    assert res_conn["tunnel_id"] == "tunnel_abcdef0123456789abcdef0123456789"
    assert res_conn["readyz_ok"] is True

    # 2d. check_updates (newer release, non-semver rejection, graceful offline fallback)
    upd = hooks["check_updates"]({})
    assert upd["offline"] is False
    assert upd["update_available"] is True
    assert upd["latest_version"] == "v9.9.9"

    github_mode["mode"] = "bad_tag"
    with pytest.raises(RuntimeError, match="non-semver"):
        hooks["check_updates"]({})

    github_mode["mode"] = "offline"
    upd_off = hooks["check_updates"]({})
    assert upd_off["offline"] is True
    assert upd_off["update_available"] is False

    # 2e. uninstall requires confirm=True, invokes -Remove -DisconnectTunnel, removes health-url.txt, preserves secrets
    with pytest.raises(RuntimeError, match="confirmation"):
        hooks["uninstall"]({})
    res_un = hooks["uninstall"]({"confirm": True})
    assert res_un["status"] == "SERVICE_AND_TUNNEL_STOPPED"
    assert res_un["secrets_preserved"] is True
    assert not health_url_file.exists()
    assert secret_marker.read_text(encoding="utf-8") == "KEEP_OWNER_KEY"
    assert policy_marker.is_file()

    # 2f. rollback: verified tunnel backup restore when only tunnel_reconfig_* exists
    bkp_dir = tmp_path / "runtime" / "secrets" / "backups" / "tunnel_reconfig_20261127_000001_000"
    bkp_dir.mkdir(parents=True, exist_ok=True)
    bkp_yaml = bkp_dir / "winowner.yaml"
    bkp_yaml.write_text("tunnel_id: restored_tunnel\n", encoding="utf-8")
    target_yaml = tmp_path / "runtime" / "tunnel" / "winowner.yaml"
    target_yaml.write_text("tunnel_id: broken_tunnel\n", encoding="utf-8")
    bkp_sha = hashlib.sha256(bkp_yaml.read_bytes()).hexdigest()
    (bkp_dir / "backup_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.2.0",
                "files": [
                    {
                        "name": "winowner.yaml",
                        "target_path": str(target_yaml),
                        "backup_path": str(bkp_yaml),
                        "existed": True,
                        "sha256": bkp_sha,
                        "secret": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    res_tb = hooks["rollback"]({})
    assert res_tb["status"] == "TUNNEL_ROLLBACK_PASS"
    assert target_yaml.read_text(encoding="utf-8") == "tunnel_id: restored_tunnel\n"

    # 2g. rollback: invokes Rollback-WindowsMcp.ps1 -LatestPreUpdate when pre_update_* exists
    (tmp_path / "backups" / "pre_update_20261127").mkdir(parents=True, exist_ok=True)
    res_rb = hooks["rollback"]({})
    assert res_rb["status"] == "ROLLBACK_PASS"
    assert any("Rollback-WindowsMcp.ps1" in " ".join(c) and "-LatestPreUpdate" in c for c in invocations)


def test_connect_chatgpt_reuses_cached_verified_runtime_hashes():
    """Verify Connect-ChatGPT.ps1 checks verified-runtime-hashes.json SHA-256 cache before downloading ZIP."""
    content = (_PROJECT_ROOT / "scripts" / "Connect-ChatGPT.ps1").read_text(encoding="utf-8")
    assert "verified-runtime-hashes.json" in content
    assert "$cached.version -eq $version" in content
    assert "$cached.manifest_hash -eq $manifestHash" in content
    assert "Get-PortableSha256" in content
    assert "$stageCacheFile" in content
