"""Local one-click release and safety dry-run checks (no cloud credentials)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "v1.2.0"

# Ensure child Windows PowerShell 5.1 processes do not inherit PowerShell 7 PSModulePath from pwsh CI runners.
os.environ.pop("PSModulePath", None)

# SHA-256 of the eight PNG entries in the supplied WinOwner_v1.2_APPROVED_8_UI_images.zip.
GUIDE_PNG_SHA256 = {
    "guide-01-en.png": "0c48ef3147bbb482c230ccb41dc47af3700198f275db1665bf5c6a959066aeab",
    "guide-01-vi.png": "fa806fcd571567236529b50703308c2a053f53d52edba4a6fd0a2c44fb5b83e5",
    "guide-02-en.png": "eab29c807891118e23e619d2f7920849dfdc29aa2d463bf76321faf04f5a7f6c",
    "guide-02-vi.png": "5745360e21c38142a513236d24a78cf3e91a274efd6bb5382da5ae85ccaddaa1",
    "guide-03-en.png": "e6e619ce5f26b617f3cd624e5189fa5f3b17d00ff0df64607112365489858176",
    "guide-03-vi.png": "1a71a0d555ab5695ff9366a0e8f317eb498b853f60583b6f3b5ede3d365a62ed",
    "guide-04-en.png": "ce335ca0f10d549d213ab6067fde54733fb525c5d3703e7e432431108e5e4ee6",
    "guide-04-vi.png": "913ecac7bf51109440ea1872f294acc3d8d82328b19bb55b915510cfe5395330",
}


def _source_release_version() -> str:
    script = (ROOT / "go.ps1").read_text(encoding="utf-8")
    match = re.search(r"\[string\]\$Ref\s*=\s*'(?P<version>v\d+\.\d+\.\d+)'", script)
    assert match, "go.ps1 must target an explicit version"
    assert match.group("version") == RELEASE_VERSION
    return match.group("version")


def test_release_version_alignment() -> None:
    version = _source_release_version()
    script = (ROOT / "go.ps1").read_text(encoding="utf-8")
    assert f"WinOwner MCP {version}" in script
    assert f"WinOwnerMCP\\{version}" in script
    for relative in ("README.md", "README.vi.md", "docs/START_HERE.vi.md"):
        page = (ROOT / relative).read_text(encoding="utf-8")
        refs = re.findall(r"github\.com/phuonghuster7/winowner-mcp/raw/([^/]+)/go\.ps1", page)
        assert refs and set(refs) == {version}, (relative, refs)
        install_paths = re.findall(r"WinOwnerMCP\\(v\d+\.\d+\.\d+(?:-rc\d+)?)", page)
        assert install_paths and set(install_paths) == {version}, (relative, install_paths)
        if relative.startswith("README"):
            assert re.search(rf"^# WinOwner MCP {re.escape(version)}$", page, re.M), relative
    assert f"targets **{version}**" in (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert version in (ROOT / "docs" / "AUTH_NONE.vi.md").read_text(encoding="utf-8")
    assert version[1:] in (ROOT / "docs" / "ADVANCED_CHATGPT.vi.md").read_text(encoding="utf-8")
    policy_example = json.loads((ROOT / "config" / "policy.example.json").read_text(encoding="utf-8"))
    assert policy_example.get("version") == version[1:]
    bug_template = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(encoding="utf-8")
    assert f"placeholder: {version}" in bug_template
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first_release_heading = re.search(r"^## .*?\b(v\d+\.\d+\.\d+(?:-rc\d+)?)\b", changelog, re.M)
    assert first_release_heading and first_release_heading.group(1) == version


def test_release_version_alignment_runtime_and_installers() -> None:
    version = _source_release_version()
    for relative in ("Install-WinOwner.cmd", "scripts/Setup-WinOwner.ps1", "docs/INSTALLATION.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert f"WinOwner MCP {version}" in text, relative
    builder = (ROOT / "scripts/Build-ReleasePackage.ps1").read_text(encoding="utf-8")
    assert f'[string]$Version = "{version}"' in builder
    runtime_version = (ROOT / "runtime/__init__.py").read_text(encoding="utf-8")
    assert re.search(rf'^__version__\s*=\s*[\'\"]{re.escape(version[1:])}[\'\"]\s*$', runtime_version, re.M)
    for relative in ("scripts/Test-WinOwnerOneClick.ps1", "scripts/Test-WinOwnerOnline.ps1",
                     "scripts/Test-WinOwnerUpgrade.ps1"):
        script = (ROOT / relative).read_text(encoding="utf-8")
        zip_versions = re.findall(r"winowner-mcp-(v\d+\.\d+\.\d+(?:-rc\d+)?)\.zip", script)
        assert zip_versions and set(zip_versions) == {version}, (relative, zip_versions)
        build_versions = re.findall(r"Build-ReleasePackage\.ps1[^\n]*-Version\s+(v\d+\.\d+\.\d+(?:-rc\d+)?)", script)
        assert build_versions and set(build_versions) == {version}, (relative, build_versions)
    # Exercise the manifest-free fallback independently of local release evidence.
    import runpy
    from tempfile import TemporaryDirectory
    namespace = runpy.run_path(str(ROOT / "runtime/release_identity.py"))
    get_identity = namespace["get_release_identity"]
    with TemporaryDirectory() as directory:
        get_identity.__globals__["PROJECT_ROOT"] = Path(directory)
        assert get_identity()["release_version"] == version


@pytest.mark.parametrize("name, expected", GUIDE_PNG_SHA256.items())
def test_supplied_guide_png_hash(name: str, expected: str) -> None:
    path = ROOT / "docs/assets" / name
    assert path.is_file(), f"Missing supplied PNG: {name}"
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), name
    assert hashlib.sha256(data).hexdigest() == expected, name


@pytest.mark.parametrize("relative, language", [("README.md", "en"), ("README.vi.md", "vi")])
def test_documentation_assets_are_in_package_allowlist(relative: str, language: str) -> None:
    builder = (ROOT / "scripts" / "Build-ReleasePackage.ps1").read_text(encoding="utf-8")
    assert 'Dir = "docs\\assets"' in builder
    assert ".svg" not in builder and "demo-readonly" not in builder
    assets_dir = ROOT / "docs" / "assets"
    actual_files = {item.name for item in assets_dir.iterdir() if item.is_file()}
    assert actual_files == set(GUIDE_PNG_SHA256.keys()), actual_files
    page = (ROOT / relative).read_text(encoding="utf-8")
    assets = re.findall(r"!\[[^\]]*\]\((docs/assets/[^)]+)\)", page)
    expected_sequence = [f"docs/assets/guide-0{i}-{language}.png" for i in range(1, 5)]
    assert assets == expected_sequence, (relative, assets, expected_sequence)
    for asset in assets:
        item = ROOT / asset
        assert item.is_file(), asset
        assert item.suffix == ".png", "README images must use reviewed PNG exports"
        assert item.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), asset
        assert f'"{item.name}"' in builder, item.name
    for md in sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").glob("*.md")):
        text = md.read_text(encoding="utf-8")
        assert "demo-readonly" not in text, md.relative_to(ROOT)
        assert not re.search(r"docs/assets/[^)\s]+\.svg", text), md.relative_to(ROOT)


def _markdown_without_fences(page: str) -> str:
    lines = []
    fence = None
    for line in page.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker.group(1)
            elif marker.group(1).startswith(fence):
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


def _markdown_anchors(page: Path) -> set[str]:
    text = _markdown_without_fences(page.read_text(encoding="utf-8"))
    anchors: set[str] = set()
    for heading in re.findall(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        slug = re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
        anchor = slug
        suffix = 1
        while anchor in anchors:
            anchor = f"{slug}-{suffix}"
            suffix += 1
        anchors.add(anchor)
    return anchors


def test_markdown_local_links_and_images_resolve() -> None:
    pages = sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").glob("*.md"))
    errors = []
    for page in pages:
        prose = _markdown_without_fences(page.read_text(encoding="utf-8"))
        for is_image, dest in re.findall(r"(!?)\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^\s)]+)(?:[ \t]+[^\n)]*)?\)", prose):
            url = urlsplit(dest.strip("<>"))
            if url.scheme or url.netloc:
                continue
            link_path = unquote(url.path)
            candidate = Path(os.path.abspath(page.parent / link_path)) if link_path else page
            target = candidate.resolve()
            if not target.is_relative_to(ROOT) or not target.exists() or (is_image and not target.is_file()):
                errors.append(f"{page.relative_to(ROOT)}: {dest} has no local target")
                continue
            if candidate.as_posix() != target.as_posix():
                errors.append(f"{page.relative_to(ROOT)}: {dest} differs from the canonical path or casing")
            if url.fragment and target.suffix.lower() == ".md" and unquote(url.fragment) not in _markdown_anchors(target):
                errors.append(f"{page.relative_to(ROOT)}: {dest} has no matching heading")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize(
    "relative, headings, optional_step",
    [
        (
            "README.md",
            [
                "Overview",
                "Key features",
                "Architecture",
                "System requirements",
                "Quick Start",
                "Usage examples",
                "Capabilities — 34 tools",
                "Security & Permissions",
                "Troubleshooting",
                "License",
            ],
            "#### Step 4 · Plugin Permissions (optional)",
        ),
        (
            "README.vi.md",
            [
                "Tổng quan",
                "Tính năng chính",
                "Kiến trúc",
                "Yêu cầu hệ thống",
                "Bắt đầu nhanh",
                "Ví dụ sử dụng",
                "Danh mục chức năng — 34 công cụ",
                "Bảo mật & Phân quyền",
                "Xử lý sự cố",
                "Giấy phép",
            ],
            "#### Bước 4 · Quyền Plugin (tùy chọn)",
        ),
    ],
)
def test_readme_product_standard_layout(relative: str, headings: list[str], optional_step: str) -> None:
    text = _markdown_without_fences((ROOT / relative).read_text(encoding="utf-8"))
    h2_headings = re.findall(r"^## +(.+?)\s*$", text, re.M)
    assert h2_headings == headings, (relative, h2_headings)
    assert optional_step in text, (relative, optional_step)



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
        assert _source_release_version() in introduction
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
    assert f"https://github.com/phuonghuster7/winowner-mcp/raw/{_source_release_version()}/go.ps1" in docs
    assert "go.ps1" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "*.cmd -text" in (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert (ROOT / "scripts" / "Test-WinOwnerOnline.ps1").is_file()
    assert "8765/mcp" not in (ROOT / "docs" / "CONNECT_CHATGPT.md").read_text(encoding="utf-8")
    assert "[REDACTED]" not in (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")


def test_nontech_chatgpt_journey_is_honest_and_actionable() -> None:
    vi = (ROOT / "README.vi.md").read_text(encoding="utf-8")
    en = (ROOT / "README.md").read_text(encoding="utf-8")
    quick = (ROOT / "docs" / "START_HERE.vi.md").read_text(encoding="utf-8")
    expected = f'powershell -NoP -C "irm https://github.com/phuonghuster7/winowner-mcp/raw/{_source_release_version()}/go.ps1|iex"'
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
    assert script.index("check-installed-owner") < script.index("Clipboard]::SetText")


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


def test_tunnel_status_and_explicit_uninstall_contract() -> None:
    uninstall = (ROOT / "Uninstall-WinOwner.cmd").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "Bootstrap-WindowsMcp.ps1").read_text(encoding="utf-8")
    check = (ROOT / "scripts" / "Check-WinOwner.ps1").read_text(encoding="utf-8")
    assert "-Remove -DisconnectTunnel" in uninstall
    assert "DisconnectTunnel requires Remove" in bootstrap
    assert "if ($DisconnectTunnel)" in bootstrap
    assert "Task name collision for $tunnelTaskName" in bootstrap
    assert "Tunnel task did not stop" in bootstrap
    assert "Tunnel task removal could not be verified" in bootstrap
    assert "TUNNEL_TASK_REMOVED" in bootstrap
    for state in ("TUNNEL_NOT_CONFIGURED", "TUNNEL_UNVERIFIED", "TUNNEL_NOT_RUNNING",
                  "TUNNEL_NOT_READY", "TUNNEL_READY"):
        assert state in check
    assert "MaximumRedirection 0" in check
    assert "does NOT verify that ChatGPT account pairing is complete" in check


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell only")
@pytest.mark.parametrize("collision", [False, True])
@pytest.mark.parametrize("tunnel_only", [False, True])
def test_tunnel_uninstall_checks_exact_task_before_removing(tmp_path: Path, collision: bool, tunnel_only: bool) -> None:
    # Mock every scheduled-task mutation: never touch a real tunnel or task.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "Bootstrap-WindowsMcp.ps1"
    script.write_bytes((ROOT / "scripts" / script.name).read_bytes())
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    harness = tmp_path / "harness.ps1"
    harness.write_text(r"""
param([string]$Root, [int]$Port, [int]$Collision, [int]$TunnelOnly)
$global:WinOwnerFixtureTunnelOnly=$TunnelOnly
$global:WinOwnerFixtureRemoved=$false
$global:WinOwnerFixturePort=$Port
$global:WinOwnerFixtureRoot=$Root
$global:WinOwnerFixtureCollision=$Collision
$global:WinOwnerFixtureStopped=$false
function global:Get-ScheduledTask {
    [CmdletBinding()] param([string]$TaskName,[string]$TaskPath)
    if ($global:WinOwnerFixtureTunnelOnly -and $TaskName -eq "WinOwnerMCP-$global:WinOwnerFixturePort") {throw "Local task must not be inspected"}
    if ($TaskName -ne "WinOwnerTunnel-$global:WinOwnerFixturePort" -or $global:WinOwnerFixtureRemoved) {return}
    $runner=Join-Path $global:WinOwnerFixtureRoot 'scripts\Run-WinOwnerTunnel.ps1'
    $arguments='-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner+'"'
    $wd=if($global:WinOwnerFixtureCollision){Join-Path $global:WinOwnerFixtureRoot 'other-install'}else{$global:WinOwnerFixtureRoot}
    [PSCustomObject]@{
        Actions=@([PSCustomObject]@{Execute=(Join-Path $PSHOME 'powershell.exe');Arguments=$arguments;WorkingDirectory=$wd})
        Principal=[PSCustomObject]@{UserId=[Security.Principal.WindowsIdentity]::GetCurrent().Name}
        State=if($global:WinOwnerFixtureStopped){'Ready'}else{'Running'}
    }
}
function global:Stop-ScheduledTask {
    [CmdletBinding()] param([string]$TaskName,[string]$TaskPath)
    if($TaskName -ne "WinOwnerTunnel-$global:WinOwnerFixturePort"){throw 'unexpected task'}
    Write-Host 'MOCK_STOP'; $global:WinOwnerFixtureStopped=$true
}
function global:Unregister-ScheduledTask {
    [CmdletBinding(SupportsShouldProcess=$true)] param([string]$TaskName,[string]$TaskPath)
    if($TaskName -ne "WinOwnerTunnel-$global:WinOwnerFixturePort"){throw 'unexpected task'}
    Write-Host 'MOCK_UNREGISTER'; $global:WinOwnerFixtureRemoved=$true
}
$ErrorActionPreference='Stop'
& (Join-Path $Root 'scripts\Bootstrap-WindowsMcp.ps1') -Remove -DisconnectTunnel -TunnelOnly:([bool]$TunnelOnly) -Port $Port
exit $LASTEXITCODE
""", encoding="utf-8")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-File", str(harness), "-Root", str(tmp_path),
         "-Port", str(port), "-Collision", str(int(collision)), "-TunnelOnly", str(int(tunnel_only))],
        cwd=tmp_path, capture_output=True, text=True, timeout=18,
    )
    output = result.stdout + result.stderr
    if collision:
        assert result.returncode != 0
        assert "Task name collision" in output
        assert "MOCK_STOP" not in output and "MOCK_UNREGISTER" not in output
    else:
        assert result.returncode == 0, output
        assert "MOCK_STOP" in output and "MOCK_UNREGISTER" in output
        assert "TUNNEL_TASK_REMOVED" in output
        if tunnel_only:
            assert "TUNNEL_DISCONNECT_COMPLETE" in output
            assert "TASK_REMOVED: WinOwnerMCP-" not in output


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
    ]
    result = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, (result.stdout, result.stderr)
    package = tmp_path / f"winowner-mcp-{RELEASE_VERSION}.zip"
    manifest = json.loads((tmp_path / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == RELEASE_VERSION
    assert manifest["package_name"] == package.name
    assert manifest["zip_size_bytes"] == package.stat().st_size
    assert manifest["zip_sha256"].lower() == hashlib.sha256(package.read_bytes()).hexdigest()
    manifest_files = {entry["relative_path"]: entry for entry in manifest["files"]}
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)), "Duplicate ZIP entries"
        for required in ("go.ps1", "Install-WinOwner.cmd", "Check-WinOwner.cmd",
                         "Uninstall-WinOwner.cmd", "Connect-ChatGPT.cmd", "Disconnect-ChatGPT.cmd", "Grant-OwnerAccess.cmd", "Show-OwnerKey.cmd", "Revoke-OwnerAccess.cmd", "scripts/Check-WinOwner.ps1",
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
        assert archive.testzip() is None
        errors = []
        for name, expected in GUIDE_PNG_SHA256.items():
            relative = f"docs/assets/{name}"
            if relative not in names or relative not in manifest_files:
                errors.append(f"{relative}: missing from ZIP or manifest")
                continue
            data = archive.read(relative)
            if hashlib.sha256(data).hexdigest() != expected:
                errors.append(f"{relative}: ZIP SHA-256 differs from supplied PNG")
            entry = manifest_files[relative]
            if entry["sha256"].lower() != expected or entry["size_bytes"] != len(data):
                errors.append(f"{relative}: release manifest does not match supplied PNG")
        assert not errors, "\n".join(errors)


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
def test_exact_irm_pipe_iex_entrypoint_without_install(tmp_path: Path) -> None:
    # Exercise the actual one-line web installation route using a local HTTP
    # server and an explicit dry-run environment flag. Never touch production.
    source = (ROOT / "go.ps1").read_bytes()
    requests: list[str] = []
    fake_appdata = tmp_path / "LocalAppData"
    fake_appdata.mkdir()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
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
        # Match the documented CMD syntax, replacing only the remote URL.
        # Explicit dry-run and disposable LocalAppData keep the rehearsal local.
        command = f'powershell -NoP -C "irm http://127.0.0.1:{server.server_port}/go.ps1|iex"'
        env = os.environ.copy()
        env["WINOWNER_BOOTSTRAP_DRYRUN"] = "1"
        env["LOCALAPPDATA"] = str(fake_appdata)
        result = subprocess.run(
            f'cmd.exe /d /s /c "{command}"',
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "DRY_RUN_PASS" in result.stdout
        assert "refs/tags/v1.2.0.zip" in result.stdout
        assert "LOCAL_INSTALL_PASS" not in result.stdout
        assert requests == ["/go.ps1"]
        assert not (fake_appdata / "WinOwnerMCP").exists()
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
    assert "Show-OwnerKey.ps1" not in connector
    assert connector.index("$existing=Get-ScheduledTask") < connector.index("Read-Host 'OpenAI tunnel_id'")
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


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell only")
def test_build_release_package_from_external_cwd_without_project_root(tmp_path: Path) -> None:
    """Invoking Build-ReleasePackage.ps1 from an external CWD without -ProjectRoot must resolve the repo root via $PSScriptRoot."""
    ext_cwd = tmp_path / "New folder"
    ext_cwd.mkdir(parents=True, exist_ok=True)
    out_dir = tmp_path / "ext_dist"

    # 1. Dry-run from external CWD without -ProjectRoot
    dry_cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "Build-ReleasePackage.ps1"),
        "-Version",
        RELEASE_VERSION,
        "-OutputDir",
        str(out_dir),
        "-DryRun",
    ]
    dry_res = subprocess.run(dry_cmd, cwd=ext_cwd, capture_output=True, text=True, timeout=45)
    assert dry_res.returncode == 0, (dry_res.stdout, dry_res.stderr)
    assert "SecretScan: PASS (0 violations)" in dry_res.stdout
    m = re.search(r"Files:\s*(\d+)\s*\|", dry_res.stdout)
    assert m and int(m.group(1)) >= 150, dry_res.stdout

    # 2. Full package build from external CWD without -ProjectRoot
    build_cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "Build-ReleasePackage.ps1"),
        "-Version",
        RELEASE_VERSION,
        "-OutputDir",
        str(out_dir),
    ]
    build_res = subprocess.run(build_cmd, cwd=ext_cwd, capture_output=True, text=True, timeout=90)
    assert build_res.returncode == 0, (build_res.stdout, build_res.stderr)
    zip_file = out_dir / f"winowner-mcp-{RELEASE_VERSION}.zip"
    assert zip_file.is_file()
    with zipfile.ZipFile(zip_file) as zf:
        names = set(zf.namelist())
        assert "Install-WinOwner.cmd" in names
        assert "tests/fixtures/shipment_fixture.py" in names

