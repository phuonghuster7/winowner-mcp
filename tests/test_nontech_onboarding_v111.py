"""Unit and dry-run tests for WinOwner MCP v1.1.1 nontech onboarding (T1-T6)."""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run_ps(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        cwd=ROOT,
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_t1_default_profile_is_read_only_and_switch_requires_phrase() -> None:
    """T1: Setup/Bootstrap default to read_only; Enable-FullControl refuses wrong confirmation."""
    setup = _run_ps(["-File", str(ROOT / "scripts" / "Setup-WinOwner.ps1"), "-DryRun", "-Port", "18791"])
    assert setup.returncode == 0, setup.stderr
    assert "profile=read_only" in setup.stdout
    assert "DRY_RUN_PASS" in setup.stdout

    # Wrong confirmation phrase must abort without switching profile
    switch_fail = _run_ps([
        "-File", str(ROOT / "scripts" / "Switch-WinOwnerProfile.ps1"),
        "-Profile", "owner_full_control",
        "-Port", "18791",
        "-ConfirmPhrase", "NOPE",
    ])
    assert switch_fail.returncode != 0
    assert "WinOwnerMCP-18791" in switch_fail.stdout or "THẤT BẠI" in switch_fail.stdout


def test_t2_consent_page_has_vietnamese_labels() -> None:
    """T2: _render_consent_html includes Vietnamese + English labels without weakening auth."""
    sys.path.insert(0, str(ROOT / "mcp-gateway"))
    import http_server  # noqa: PLC0415

    html = http_server._render_consent_html(
        client_id="cid_test",
        scope="mcp:tools",
        resource="http://127.0.0.1:8768/mcp",
        redirect_uri="https://chatgpt.com/aip/callback",
        code_challenge="cc",
        code_challenge_method="S256",
        state="st",
    )
    assert "Mã khóa chủ máy" in html
    assert "Xác nhận quyền truy cập cho Trợ lý" in html
    assert "Từ chối (Deny)" in html


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_t2_show_owner_key_does_not_print_secret(tmp_path: Path) -> None:
    """T2: Show-OwnerKey.ps1 copies key to clipboard without echoing the secret to stdout."""
    secret_val = "mcp_oadm_test_secret_never_print_123456"
    key_file = tmp_path / "owner_admin.key"
    key_file.write_text(secret_val + "\n", encoding="utf-8")

    res = _run_ps([
        "-File",
        str(ROOT / "scripts" / "Show-OwnerKey.ps1"),
        "-KeyPath",
        str(key_file),
        "-NoWait",
    ])
    assert res.returncode == 0, res.stderr
    assert secret_val not in res.stdout
    assert secret_val not in res.stderr
    assert "THÀNH CÔNG" in res.stdout or "THANH CONG" in res.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_t3_grant_access_refuses_unsafe_roots_and_runs_dry_run(tmp_path: Path) -> None:
    """T3: Grant-Access.ps1 blocks drive roots and user profile root, allows specific folder dry-run."""
    safe_dir = tmp_path / "ProjectA"
    safe_dir.mkdir()

    ok = _run_ps([
        "-File", str(ROOT / "scripts" / "Grant-Access.ps1"),
        "-Folder", str(safe_dir), "-Capability", "read", "-Hours", "1", "-Yes", "-DryRun",
    ])
    assert ok.returncode == 0, ok.stderr
    assert "DRY_RUN:" in ok.stdout

    for unsafe in ("C:\\", os.environ.get("USERPROFILE", "C:\\Users")):
        bad = _run_ps([
            "-File", str(ROOT / "scripts" / "Grant-Access.ps1"),
            "-Folder", unsafe, "-Capability", "read", "-Hours", "1", "-Yes", "-DryRun",
        ])
        assert bad.returncode != 0


def test_t4_bootstrap_uses_pythonw_for_hidden_scheduled_task() -> None:
    """T4: Bootstrap-WindowsMcp.ps1 uses pythonw.exe for scheduled task execution."""
    text = (ROOT / "scripts" / "Bootstrap-WindowsMcp.ps1").read_text(encoding="utf-8")
    assert "pythonw.exe" in text
    assert "$taskPython" in text


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 required")
def test_t6_go_ps1_pinned_tag_iex_and_sha256_mismatch_abort(tmp_path: Path) -> None:
    """T6: go.ps1 supports env-var driven iex, blocks unpinned main without flag, and aborts on bad SHA-256."""
    # 1. Default pinned tag via iex + WINOWNER_DRYRUN=1
    iex_ok = _run_ps(
        ["-Command", f"$env:WINOWNER_DRYRUN='1'; iex (Get-Content -LiteralPath '{ROOT / 'go.ps1'}' -Raw)"],
    )
    assert iex_ok.returncode == 0, iex_ok.stderr
    assert "releases/download/v1.1.1/winowner-mcp-v1.1.1.zip" in iex_ok.stdout
    assert "pinned=True" in iex_ok.stdout

    # 2. Unpinned main without -AllowUnpinned must fail
    unpinned_fail = _run_ps(
        ["-File", str(ROOT / "go.ps1"), "-Ref", "main", "-DryRun"],
    )
    assert unpinned_fail.returncode != 0

    # 3. SHA-256 mismatch on -ArchivePath must abort
    fake_zip = tmp_path / "fake.zip"
    with zipfile.ZipFile(fake_zip, "w") as zf:
        zf.writestr("dummy.txt", b"x" * 35000)
    bad_hash = _run_ps([
        "-File", str(ROOT / "go.ps1"),
        "-ArchivePath", str(fake_zip),
        "-ExpectedSha256", "0" * 64,
        "-InstallRoot", str(tmp_path / "install"),
        "-Port", "18792",
    ])
    assert bad_hash.returncode != 0
    assert "SHA-256" in bad_hash.stdout or "SHA-256" in bad_hash.stderr

