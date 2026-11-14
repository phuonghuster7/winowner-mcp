"""Local one-click release and safety dry-run checks (no cloud credentials)."""
from __future__ import annotations

import os
import socket
import subprocess
import zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

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
        assert "v1.2.0" in introduction
        assert "ChatGPT" in introduction
        assert not any(name in introduction for name in ("Claude", "Cursor", "Gemini"))
    assert "LOCAL_INSTALL_PASS" in ps
    assert "LegacyInstallRoot" in (ROOT / "go.ps1").read_text(encoding="utf-8")
    assert "UpgradeConfirmed" in (ROOT / "go.ps1").read_text(encoding="utf-8")
    assert (ROOT / "scripts" / "Test-WinOwnerOneClick.ps1").is_file()
    assert "Install-WinOwner.cmd" in builder.read_text(encoding="utf-8")
    docs = guide.read_text(encoding="utf-8")
    advanced = (ROOT / "docs" / "ADVANCED_CHATGPT.vi.md").read_text(encoding="utf-8")
    assert "tunnel_id" in advanced
    assert "CONTROL_PLANE_API_KEY" in advanced
    assert "OAuth" in docs
    assert "Check-WinOwner.cmd" in docs
    assert len(docs.encode("utf-8")) < 6000
    assert "https://github.com/phuonghuster7/winowner-mcp/raw/v1.2.0/go.ps1" in docs
    assert "go.ps1" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "*.cmd -text" in (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert (ROOT / "scripts" / "Test-WinOwnerOnline.ps1").is_file()
    assert "8765/mcp" not in (ROOT / "docs" / "CONNECT_CHATGPT.md").read_text(encoding="utf-8")
    assert "[REDACTED]" not in (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")


def test_nontech_chatgpt_journey_is_honest_and_actionable() -> None:
    vi = (ROOT / "README.vi.md").read_text(encoding="utf-8")
    en = (ROOT / "README.md").read_text(encoding="utf-8")
    quick = (ROOT / "docs" / "START_HERE.vi.md").read_text(encoding="utf-8")
    expected = 'powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/v1.2.0/go.ps1|iex"'
    for page in (vi, en, quick):
        assert expected in page
        assert "Authentication" in page or "Full Control" in page
        assert "Check-WinOwner.cmd" in page
        assert "Uninstall-WinOwner.cmd" in page
        assert "Authentication" in page or "OAuth" in page or "owner" in page
        assert "WinOwner MCP" in page
    assert "Connect-ChatGPT.cmd" in vi and "Connect-ChatGPT.cmd" in en
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


def test_chatgpt_authentication_none_is_not_anonymous_owner_access() -> None:
    guide = (ROOT / "docs" / "AUTH_NONE.vi.md").read_text(encoding="utf-8")
    intro = (ROOT / "docs" / "START_HERE.vi.md").read_text(encoding="utf-8")
    cli = (ROOT / "go.ps1").read_text(encoding="utf-8")
    setup = (ROOT / "scripts" / "Setup-WinOwner.ps1").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "Bootstrap-WindowsMcp.ps1").read_text(encoding="utf-8")
    for page in (guide, intro):
        assert "Authentication None" in page
        assert "401" in page
    assert "Authentication: None" in guide
    assert "owner" in guide
    assert "capability grant" in guide
    assert "Authentication None is possible with an authenticated tunnel" in cli
    assert "Authentication None requires a separately secured tunnel" in setup
    assert "$h.require_autonomous_auth -ne $true" in bootstrap
    assert "$h.allow_loopback_tunnel_identity -ne $false" in bootstrap
    assert "remote_oauth_paired=$false" in bootstrap


def test_owner_key_launcher_requires_separate_command_and_confirmation() -> None:
    launcher = (ROOT / "Show-OwnerKey.cmd").read_bytes()
    assert b'powershell.exe"\r\n"%PWSH%"' in launcher
    assert launcher.count(b"\r\n") == launcher.count(b"\n")
    script = (ROOT / "scripts" / "Show-OwnerKey.ps1").read_text(encoding="utf-8")
    assert "MessageBoxDefaultButton]::Button2" in script
    assert script.index("DialogResult]::Yes") < script.index("Clipboard]::SetText")
    assert "Owner credential bundle is invalid" in script
    assert "Write-Host $token" not in script


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD only")
def test_owner_key_cmd_executes_and_propagates_exit_code(tmp_path: Path) -> None:
    # Exercise the real launcher with a harmless stub, not a credential reader.
    launcher = tmp_path / "Show-OwnerKey.cmd"
    launcher.write_bytes((ROOT / launcher.name).read_bytes())
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "Show-OwnerKey.ps1").write_text(
        'param([switch]$Probe)\nif(-not $Probe){exit 9}\n'
        'Write-Output "OWNER_KEY_LAUNCHER_PROBE"\nexit 7\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(launcher), "-Probe"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 7, (result.stdout, result.stderr)
    assert "OWNER_KEY_LAUNCHER_PROBE" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell only")
@pytest.mark.parametrize("bundle", [None, "{malformed_fixture_marker"])
def test_owner_key_missing_or_malformed_bundle_fails_without_disclosure(
    tmp_path: Path, bundle: str | None,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "Show-OwnerKey.ps1"
    script.write_bytes((ROOT / "scripts" / script.name).read_bytes())
    if bundle is not None:
        secret_dir = tmp_path / "runtime" / "secrets"
        secret_dir.mkdir(parents=True)
        (secret_dir / "owner_delegation_bundle.json").write_text(bundle, encoding="utf-8")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Sta", "-File", str(script)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert ("No owner key exists" if bundle is None else "Owner credential bundle is invalid") in output
    assert "malformed_fixture_marker" not in output
    assert "OWNER_TOKEN_COPIED" not in output


def test_grant_launcher_reviews_exact_client_before_profile_changes() -> None:
    launcher = (ROOT / "Grant-OwnerAccess.cmd").read_bytes()
    assert b'powershell.exe"\r\n"%PWSH%"' in launcher
    assert launcher.count(b"\r\n") == launcher.count(b"\n")
    script = (ROOT / "scripts" / "Grant-OwnerAccess.ps1").read_text(encoding="utf-8")
    prompt = script.index("MessageBox]::Show")
    assert script.index("Requested client: $ClientId") < prompt
    assert script.index("Local security preflight failed") < prompt
    assert script.index("Required installed file missing") < prompt
    assert "MessageBoxDefaultButton]::Button2" in script
    assert script.index("DialogResult]::Yes") < script.index("-Remove -Port")
    assert script.index("DialogResult]::Yes") < script.index("provision --client-id")
    assert "A typed client ID alone does not prove identity" in script


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD only")
def test_grant_cmd_calls_stub_and_returns_exit_code(tmp_path: Path) -> None:
    # No live authorization, credentials, scheduled tasks or profile changes.
    launcher = tmp_path / "Grant-OwnerAccess.cmd"
    launcher.write_bytes((ROOT / launcher.name).read_bytes())
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "Grant-OwnerAccess.ps1").write_text(
        'param([switch]$Probe)\nif(-not $Probe){exit 9}\n'
        'Write-Output "GRANT_LAUNCHER_PROBE"\nexit 7\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(launcher), "-Probe"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 7, (result.stdout, result.stderr)
    assert "GRANT_LAUNCHER_PROBE" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell only")
def test_grant_missing_install_refuses_without_dialog(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "Grant-OwnerAccess.ps1"
    script.write_bytes((ROOT / "scripts" / script.name).read_bytes())
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Sta", "-File", str(script),
         "-ClientId", "synthetic-fixture"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert "virtual environment missing" in result.stdout + result.stderr
    assert "OWNER_GRANT_CREATED" not in result.stdout


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
                         "Uninstall-WinOwner.cmd", "Connect-ChatGPT.cmd", "Grant-OwnerAccess.cmd", "Show-OwnerKey.cmd", "scripts/Check-WinOwner.ps1",
                         "docs/ADVANCED_CHATGPT.vi.md", "docs/AUTH_NONE.vi.md"):
            assert required in names
        assert "scripts/Setup-WinOwner.ps1" in names
        assert "scripts/Connect-ChatGPT.ps1" in names
        assert "scripts/Run-WinOwnerTunnel.ps1" in names
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
    assert "profile=read_only" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell and local HTTP server only")
def test_exact_irm_pipe_iex_entrypoint_without_install() -> None:
    # Exercise the actual one-line web installation route using a local HTTP
    # server and an explicit dry-run environment flag. Never touch production.
    source = (ROOT / "go.ps1").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/go.ps1":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(source)))
            self.end_headers()
            self.wfile.write(source)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = (
            "$env:WINOWNER_BOOTSTRAP_DRYRUN='1';"
            f"irm http://127.0.0.1:{server.server_port}/go.ps1|iex"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "DRY_RUN_PASS" in result.stdout
        assert "refs/tags/v1.2.0.zip" in result.stdout
        assert "LOCAL_INSTALL_PASS" not in result.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_official_tunnel_client_security_contract() -> None:
    connector = (ROOT / "scripts" / "Connect-ChatGPT.ps1").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "Run-WinOwnerTunnel.ps1").read_text(encoding="utf-8")
    assert "openai/tunnel-client/releases/download" in connector
    assert "SHA256SUMS.txt" in connector
    assert "SHA256" in connector
    assert "cloudflared.exe" in connector and "cloudflared-manifest.json" in connector
    assert "icacls.exe" in connector
    assert "Run-WinOwnerTunnel.ps1" in connector
    assert "env:CONTROL_PLANE_API_KEY" in connector
    assert "env:WINOWNER_INTERNAL_AUTH" in connector
    assert "allow_loopback_tunnel_identity" in connector
    assert "require_autonomous_auth" in connector
    assert "/readyz" in connector
    assert "TUNNEL_PREPARED" in connector
    assert "ChatGPT tool call NOT verified" in connector
    assert "MaximumRedirection 0" in connector
    assert "$createdTask" in connector
    assert "--health.listen-addr" in runner
    assert "--health.url-file" in runner
    assert "mcp_oat_" not in connector  # No implicit owner impersonation
    assert "ReadAllText" in runner
    assert "Show-OwnerKey" in (ROOT / "README.vi.md").read_text(encoding="utf-8")
    assert "Grant-OwnerAccess" in (ROOT / "README.md").read_text(encoding="utf-8")
    grant = (ROOT / "scripts" / "Grant-OwnerAccess.ps1").read_text(encoding="utf-8")
    # The owner delegation manager must share the active server's OAuth DB,
    # NOT create separate runtime/oauth_state.db and use an unrelated key.
    assert "runtime\\secrets\\oauth.db" in grant
    assert "runtime\\secrets\\secrets\\oauth_signing.key" in grant
    assert "--oauth-db $activeDb" in grant
    assert "--signing-key-file $signingFile" in grant
    assert "read_only" in grant and "OwnerFullControl" in grant



@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell only")
def test_one_command_online_bootstrap_dry_run() -> None:
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(ROOT / "go.ps1"), "-DryRun", "-Port", "18790"]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "DRY_RUN_PASS" in result.stdout
    assert "github.com/phuonghuster7/winowner-mcp" in result.stdout
