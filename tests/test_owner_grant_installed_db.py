"""Owner delegation CLI must target the same SQLite DB and signing key as the live adapter."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp-gateway"))

from http_server import _SQLiteOAuthPersistence, _verify_oauth_token


def test_owner_grant_provision_matches_installed_adapter_db(tmp_path: Path) -> None:
    # Use an entirely isolated root and synthetic auth. No production DB/key.
    db = tmp_path / "runtime" / "secrets" / "oauth.db"
    signing = tmp_path / "runtime" / "secrets" / "secrets" / "oauth_signing.key"
    bundle = tmp_path / "runtime" / "secrets" / "owner_delegation_bundle.json"
    db.parent.mkdir(parents=True)
    env = os.environ.copy()
    env["WINDOWS_MCP_GATEWAY_TOKEN"] = "isolated-synthetic-gateway-token-for-test-only"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "manage_owner_delegation.py"),
         "--oauth-db", str(db), "--tasks-db", str(db),
         "--signing-key-file", str(signing), "provision",
         "--client-id", "chatgpt-owner-isolated-test",
         "--token-out", str(bundle)],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=40,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert bundle.is_file() and signing.is_file() and db.is_file()
    info = json.loads(bundle.read_text(encoding="utf-8"))
    signing_key = signing.read_text(encoding="utf-8").strip()
    persistence = _SQLiteOAuthPersistence(db)
    ok, token_claims, error = _verify_oauth_token(
        info["access_token"], signing_key, set(), persistence=persistence,
        expected_owner_id="local_owner",
    )
    assert ok, error
    assert token_claims["client_id"] == "chatgpt-owner-isolated-test"
    assert "mcp:owner" in token_claims["scope"]
    rec = persistence.get_owner_capability_grant(info["capability_grant_id"])
    assert rec is not None
    assert rec["client_id"] == "chatgpt-owner-isolated-test"
    assert rec["oauth_token_jti"] == info["oauth_token_jti"]
    assert "mcp:owner" in info["scope"]
