"""Local one-click release and safety dry-run checks (no cloud credentials)."""
from __future__ import annotations

import os
import socket
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_beginner_entrypoint_and_release_inventory() -> None:
    launcher = ROOT / "Install-WinOwner.cmd"
    setup = ROOT / "scripts" / "Setup-WinOwner.ps1"
    guide = ROOT / "docs" / "START_HERE.vi.md"
    builder = ROOT / "scripts" / "Build-ReleasePackage.ps1"

    for path in (launcher, setup, guide, builder):
        assert path.is_file(), f"Missing source-release file: {path}"
    batch = launcher.read_text(encoding="utf-8")
    assert "Setup-WinOwner.ps1" in batch
    assert "-ExecutionPolicy Bypass" in batch  # process-scoped, no registry modification
    assert "pause" in batch.lower()  # reports errors in Explorer double-click mode
    ps = setup.read_text(encoding="utf-8")
    assert "Bootstrap-WindowsMcp.ps1" in ps
    assert "allow_loopback_tunnel_identity" in ps
    assert "remote_oauth_paired" in ps
    assert "owner_global_filesystem_enabled" in ps
    assert "LOCAL_INSTALL_PASS" in ps
    assert "Install-WinOwner.cmd" in builder.read_text(encoding="utf-8")
    docs = guide.read_text(encoding="utf-8")
    assert "tunnel_id" in docs
    assert "CONTROL_PLANE_API_KEY" in docs
    assert "OAuth" in docs
    assert "LOCAL_INSTALL_PASS" in docs
    assert "127.0.0.1:8768" in docs


@pytest.mark.skipif(os.name != "nt", reason="Windows ZIP builder only")
def test_release_zip_has_portable_entries_and_oneclick(tmp_path: Path) -> None:
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "Build-ReleasePackage.ps1"),
        "-ProjectRoot",
        str(ROOT),
        "-OutputDir",
        str(tmp_path),
        "-Version",
        "v0-onboarding-ci",
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, (result.stdout, result.stderr)
    with zipfile.ZipFile(tmp_path / "winowner-mcp-v0-onboarding-ci.zip") as archive:
        names = archive.namelist()
        assert "Install-WinOwner.cmd" in names
        assert "scripts/Setup-WinOwner.ps1" in names
        assert "docs/START_HERE.vi.md" in names
        assert "requirements.txt" in names
        assert all("\\" not in name for name in names)
        assert "config/policy.json" not in names
        assert not any(name.startswith("runtime/secrets/") and not name.endswith(".gitkeep") for name in names)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell / Scheduled Tasks only")
def test_beginner_frontend_dry_run_does_not_install() -> None:
    # Reserve an ephemeral local port, release it, then exercise the dry run.
    # The local backend production port 8768 is intentionally never touched.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "Setup-WinOwner.ps1"),
        "-DryRun",
        "-NoPythonInstall",
        "-NoOpenGuide",
        "-Port",
        str(port),
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "DRY_RUN_PASS" in result.stdout
    assert "LOCAL_INSTALL_PASS" not in result.stdout
