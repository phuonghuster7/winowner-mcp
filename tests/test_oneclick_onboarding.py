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
    assert "pause >nul" in batch.lower()  # the interactive double-click keeps results visible
    assert 'if "%~1"==""' in batch  # automated/online installation never waits
    ps = setup.read_text(encoding="utf-8")
    assert "Bootstrap-WindowsMcp.ps1" in ps
    assert "allow_loopback_tunnel_identity" in ps
    assert "remote_oauth_paired" in ps
    assert "owner_global_filesystem_enabled" in ps
    assert "-OwnerFullControl" in ps
    assert "WinOwner-SETUP-RESULT.txt" in ps
    assert "Start-Process -FilePath \"notepad.exe\"" not in ps
    assert "Read-Host" not in ps
    assert "Get-Credential" not in ps
    for name in ("README.md", "README.vi.md"):
        introduction = (ROOT / name).read_text(encoding="utf-8")
        assert "v1.1.0" in introduction
        assert "ChatGPT" in introduction
        assert not any(name in introduction for name in ("Claude", "Cursor", "Gemini"))
    assert "LOCAL_INSTALL_PASS" in ps
    assert (ROOT / "scripts" / "Test-WinOwnerOneClick.ps1").is_file()
    assert "Install-WinOwner.cmd" in builder.read_text(encoding="utf-8")
    docs = guide.read_text(encoding="utf-8")
    advanced = (ROOT / "docs" / "ADVANCED_CHATGPT.vi.md").read_text(encoding="utf-8")
    assert "tunnel_id" in advanced
    assert "CONTROL_PLANE_API_KEY" in advanced
    assert "OAuth" in docs
    assert "Check-WinOwner.cmd" in docs
    assert len(docs.encode("utf-8")) < 6000
    assert "https://github.com/phuonghuster7/winowner-mcp/raw/main/go.ps1" in docs
    assert "go.ps1" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "*.cmd -text" in (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert (ROOT / "scripts" / "Test-WinOwnerOnline.ps1").is_file()
    assert "8765/mcp" not in (ROOT / "docs" / "CONNECT_CHATGPT.md").read_text(encoding="utf-8")
    assert "[REDACTED]" not in (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")


def test_nontech_chatgpt_journey_is_honest_and_actionable() -> None:
    vi = (ROOT / "README.vi.md").read_text(encoding="utf-8")
    en = (ROOT / "README.md").read_text(encoding="utf-8")
    quick = (ROOT / "docs" / "START_HERE.vi.md").read_text(encoding="utf-8")
    expected = 'powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/main/go.ps1|iex"'
    for page in (vi, en, quick):
        assert expected in page
        assert "full MCP" in page
        assert "Check-WinOwner.cmd" in page
        assert "Uninstall-WinOwner.cmd" in page
        assert "OAuth" in page
        assert "WinOwner MCP" in page
    assert "Business" in vi and "Enterprise/Edu" in en
    assert "\u0054\u0048\u00c0\u004e\u0048 \u0043\u00d4\u004e\u0047" in quick
    assert "\u0054\u0048\u1ea4\u0054 \u0042\u1ea0\u0049" in quick
    assert "Win + R" in quick
    assert "Uninstall-WinOwner.cmd" in quick
    assert "%LOCALAPPDATA%" in quick
    assert len(quick.encode("utf-8")) < 5000
    assert not any(word in vi for word in ("Claude", "Gemini", "Cursor"))
    assert "127.0.0.1:8765/mcp" not in (
        ROOT / "docs" / "CONNECT_CHATGPT.md"
    ).read_text(encoding="utf-8")


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
    result = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, (result.stdout, result.stderr)
    with zipfile.ZipFile(tmp_path / "winowner-mcp-v0-onboarding-ci.zip") as archive:
        names = archive.namelist()
        for required in ("go.ps1", "Install-WinOwner.cmd", "Check-WinOwner.cmd",
                         "Uninstall-WinOwner.cmd", "scripts/Check-WinOwner.ps1",
                         "docs/ADVANCED_CHATGPT.vi.md"):
            assert required in names
        assert "scripts/Setup-WinOwner.ps1" in names
        for cmd_file in ("Install-WinOwner.cmd", "Check-WinOwner.cmd", "Uninstall-WinOwner.cmd"):
            raw = archive.read(cmd_file)
            assert b"\r\n" in raw
            assert raw.count(b"\r\n") == raw.count(b"\n"), f"{cmd_file} has Unix-only line endings"

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
    assert "profile=owner_full_control" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell only")
def test_one_command_online_bootstrap_dry_run() -> None:
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(ROOT / "go.ps1"), "-DryRun", "-Port", "18790"]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "DRY_RUN_PASS" in result.stdout
    assert "github.com/phuonghuster7/winowner-mcp" in result.stdout
