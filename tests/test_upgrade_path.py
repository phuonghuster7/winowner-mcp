from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
GO_PS1 = REPO_ROOT / "go.ps1"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_upgrade_backup_restore_between_two_fake_version_folders(tmp_path: Path) -> None:
    """Verify T5 upgrade helpers preserve secrets, oauth.db, and policy.json across version folders."""
    base = tmp_path / "WinOwnerMCP"
    v110 = base / "v1.1.0"
    v111 = base / "v1.1.1"
    (v110 / "scripts").mkdir(parents=True)
    (v110 / "config").mkdir(parents=True)
    (v110 / "scripts" / "Bootstrap-WindowsMcp.ps1").write_text("# stub\n", encoding="utf-8")
    (v111 / "scripts").mkdir(parents=True)
    (v111 / "config").mkdir(parents=True)
    (v111 / "scripts" / "Bootstrap-WindowsMcp.ps1").write_text("# stub v1.1.1\n", encoding="utf-8")

    # Seed v1.1.0 state
    secrets_110 = v110 / "runtime" / "secrets"
    secrets_110.mkdir(parents=True)
    (secrets_110 / "gateway_caller.token").write_text("dummy-caller-token-for-test", encoding="utf-8")
    (secrets_110 / "owner_admin.key").write_text("mcp_oadm_dummy_key_for_test\n", encoding="utf-8")
    (secrets_110 / "oauth.db").write_bytes(b"SQLite format 3\x00dummy-oauth-state")
    (v110 / "config" / "policy.json").write_text('{"default_profile": "read_only", "custom_flag": true}\n', encoding="utf-8")

    backup_dir = base / "backups" / "20261113T000000Z"

    # Load only the helper functions from go.ps1 using AST / scriptblock extraction and run them
    ps_script = f"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{GO_PS1}', [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) {{ throw "Parse errors in go.ps1" }}
foreach ($fnName in @('Find-PreviousInstallRoot', 'Backup-WinOwnerState', 'Restore-WinOwnerState')) {{
    $fnAst = $ast.FindAll({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $fnName }}, $true) | Select-Object -First 1
    if (-not $fnAst) {{ throw "Missing function $fnName in go.ps1" }}
    Invoke-Expression $fnAst.Extent.Text
}}
$prev = Find-PreviousInstallRoot '{v111}' 18799
if ($prev -ne '{v110}') {{ throw "Expected previous root {v110}, got $prev" }}
Backup-WinOwnerState $prev '{backup_dir}'
Restore-WinOwnerState '{backup_dir}' '{v111}'
"""
    res = subprocess.run(
        ["powershell", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"stdout={res.stdout}\nstderr={res.stderr}"

    # Verify backup and restored files in v1.1.1
    assert (backup_dir / "secrets" / "oauth.db").read_bytes() == b"SQLite format 3\x00dummy-oauth-state"
    assert (v111 / "runtime" / "secrets" / "gateway_caller.token").read_text(encoding="utf-8") == "dummy-caller-token-for-test"
    assert (v111 / "runtime" / "secrets" / "owner_admin.key").read_text(encoding="utf-8") == "mcp_oadm_dummy_key_for_test\n"
    assert (v111 / "runtime" / "secrets" / "oauth.db").read_bytes() == b"SQLite format 3\x00dummy-oauth-state"
    assert '"custom_flag": true' in (v111 / "config" / "policy.json").read_text(encoding="utf-8")

    # Verify Bootstrap-WindowsMcp.ps1 -Upgrade dry-run syntax/params work
    dry = subprocess.run(
        [
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "scripts" / "Bootstrap-WindowsMcp.ps1"),
            "-DryRun",
            "-Upgrade",
            "-Port",
            "18799",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0, f"stdout={dry.stdout}\nstderr={dry.stderr}"
    assert "DRY_RUN profile=read_only" in dry.stdout
