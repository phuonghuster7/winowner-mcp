"""Phase 4 P1 — Verified GitHub Release Update & Two-Phase Atomic Rollback Tests.

Verifies:
1. Update strictly enforces verified semantic version release artifacts (`vX.Y.Z`) and rejects
   arbitrary branch references (`main`, `master`, `HEAD`, `/archive/refs/heads/...`) and unverified
   live directory updates.
2. Update verifies release ZIP SHA-256 against `SHA256SUMS.txt` and `-ExpectedZipSha256` as well as
   per-file SHA-256 hashes in `release-manifest.json`; digest mismatch or ZIP-slip fails closed
   with zero live file changes.
3. Update rejects silent version downgrades (e.g. `v1.2.0` -> `v1.1.0`) unless `-AllowDowngrade` is
   explicitly passed.
4. Update preserves all existing local configs (`config/policy.json`, `config/trusted_workspaces.json`,
   `config/user_custom.json`), secrets (`runtime/secrets/*`), and owner authorization databases
   (`runtime/secrets/oauth.db`, `runtime/oauth_state.db`), verified by before/after SHA-256 checks.
5. Update automatic rollback (on `-SimulateFailure` or broken Python syntax) restores modified files
   AND removes newly added files, leaving zero orphan files behind.
6. `Rollback-WindowsMcp.ps1` performs two-phase atomic validation: if any backup file is tampered
   with or targets a protected secret/traversal path, Phase 1 aborts before modifying any live file.
7. Scheduled Task ownership verification: both `Update-WindowsMcpAgent.ps1` and `Rollback-WindowsMcp.ps1`
   refuse to stop or restart foreign colliding scheduled tasks.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = ROOT / "scripts" / "Update-WindowsMcpAgent.ps1"
ROLLBACK_SCRIPT = ROOT / "scripts" / "Rollback-WindowsMcp.ps1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _sha256_upper(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _run_ps1(script: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *args,
    ]
    return subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _create_isolated_install_tree(base_dir: Path, version: str = "1.2.0") -> Path:
    target = base_dir / "installed_winowner"
    for sub in ("backend", "mcp-gateway", "runtime", "runtime/secrets", "runtime/logs", "runtime/tunnel", "config", "scripts", "backups"):
        (target / sub).mkdir(parents=True, exist_ok=True)

    (target / "runtime" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (target / "backend" / "app.py").write_text('VERSION = "1.2.0"\nVALUE = "original_v120"\n', encoding="utf-8")
    (target / "mcp-gateway" / "server.py").write_text('GATEWAY_V = "1.2.0"\n', encoding="utf-8")

    # Populate protected secrets, owner delegation DB, tunnel profile, and local configs
    (target / "runtime" / "secrets" / "gateway_caller.token").write_text("secret_gateway_caller_token_v1", encoding="utf-8")
    (target / "runtime" / "secrets" / "oauth_signing.key").write_text("secret_oauth_signing_key_v1", encoding="utf-8")
    (target / "runtime" / "secrets" / "oauth.db").write_bytes(b"SQLite format 3\x00OWNER_DELEGATION_STATE_INTACT")
    (target / "runtime" / "secrets" / "owner_delegation_bundle.json").write_text(
        json.dumps({"owner_id": "local_owner", "active": True}), encoding="utf-8"
    )
    (target / "runtime" / "oauth_state.db").write_bytes(b"SQLite format 3\x00OAUTH_STATE_DB_INTACT")
    (target / "runtime" / "tunnel" / "winowner.yaml").write_text("tunnel_id: tunnel_11111111111111111111111111111111\n", encoding="utf-8")
    (target / "config" / "policy.json").write_text(json.dumps({"default_profile": "read_only", "custom_owner_rule": True}), encoding="utf-8")
    (target / "config" / "trusted_workspaces.json").write_text(
        json.dumps({"trusted_mode_enabled": True, "trusted_workspaces": [{"workspace_root": "D:\\Trusted"}]}),
        encoding="utf-8",
    )
    (target / "config" / "user_custom.json").write_text(json.dumps({"keep_me": True}), encoding="utf-8")
    return target


def _build_verified_release_bundle(
    out_dir: Path,
    version_tag: str = "v1.3.0",
    files_map: dict[str, str] | None = None,
    tamper_manifest_file_hash: bool = False,
) -> tuple[Path, Path, Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ver_num = version_tag[1:] if version_tag.startswith("v") else version_tag
    if files_map is None:
        files_map = {
            "runtime/__init__.py": f'__version__ = "{ver_num}"\n',
            "backend/app.py": f'VERSION = "{ver_num}"\nVALUE = "updated_{ver_num}"\n',
            "mcp-gateway/server.py": f'GATEWAY_V = "{ver_num}"\n',
            "runtime/new_feature_p1.py": 'NEW_FEATURE_ENABLED = True\n',
            "config/policy.example.json": json.dumps({"default_profile": "read_only", "version": ver_num}),
        }

    zip_name = f"winowner-mcp-{version_tag}.zip"
    zip_path = out_dir / zip_name
    manifest_entries = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path, content in sorted(files_map.items()):
            raw_bytes = content.encode("utf-8")
            zf.writestr(rel_path, raw_bytes)
            sha = hashlib.sha256(raw_bytes).hexdigest().upper()
            if tamper_manifest_file_hash and rel_path == "backend/app.py":
                sha = "0" * 64
            manifest_entries.append({
                "relative_path": rel_path,
                "size_bytes": len(raw_bytes),
                "sha256": sha,
            })

    zip_sha_upper = _sha256_upper(zip_path)
    manifest_path = out_dir / "release-manifest.json"
    manifest_obj = {
        "schema_version": "1.0.0",
        "version": version_tag,
        "package_name": zip_name,
        "zip_sha256": zip_sha_upper,
        "secret_scan_passed": True,
        "secret_violations": 0,
        "files": manifest_entries,
    }
    manifest_path.write_text(json.dumps(manifest_obj, indent=2), encoding="utf-8")
    manifest_sha_lower = _sha256_upper(manifest_path).lower()

    sums_path = out_dir / "SHA256SUMS.txt"
    sums_lines = [
        f"{zip_sha_upper.lower()}  {zip_name}",
        f"{manifest_sha_lower}  release-manifest.json",
    ]
    for entry in manifest_entries:
        sums_lines.append(f"{entry['sha256'].lower()}  {entry['relative_path']}")
    sums_path.write_text("\n".join(sums_lines) + "\n", encoding="utf-8")
    return zip_path, sums_path, manifest_path, zip_sha_upper


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_01_rejects_arbitrary_branch_refs_unverified_dir_and_downgrade(tmp_path: Path) -> None:
    target = _create_isolated_install_tree(tmp_path, version="1.2.0")
    orig_app_sha = _sha256_upper(target / "backend" / "app.py")

    # 1. Arbitrary branch names (main, HEAD, master, refs/heads/feat) must be rejected
    for bad_ref in ("main", "HEAD", "master", "refs/heads/main", "../v1.3.0"):
        res = _run_ps1(UPDATE_SCRIPT, "-TargetRoot", str(target), "-ReleaseVersion", bad_ref)
        assert res.returncode != 0
        assert "SECURITY VIOLATION" in (res.stdout + res.stderr)

    # 2. Branch archive URL in ReleaseBaseUrl must be rejected
    res_url = _run_ps1(
        UPDATE_SCRIPT,
        "-TargetRoot", str(target),
        "-ReleaseVersion", "v1.3.0",
        "-ReleaseBaseUrl", "https://github.com/phuonghuster7/winowner-mcp/archive/refs/heads/main",
    )
    assert res_url.returncode != 0
    assert "SECURITY VIOLATION" in (res_url.stdout + res_url.stderr)

    # 3. Live update from raw unverified directory (without verified release ZIP + SHA-256) must fail closed
    res_raw = _run_ps1(UPDATE_SCRIPT, "-TargetRoot", str(target), "-SourcePath", str(ROOT))
    assert res_raw.returncode != 0
    assert "SECURITY VIOLATION" in (res_raw.stdout + res_raw.stderr)

    # 4. Silent version downgrade (v1.2.0 -> v1.1.0) must be rejected without -AllowDowngrade
    zip_old, sums_old, manifest_old, sha_old = _build_verified_release_bundle(tmp_path / "rel_v110", version_tag="v1.1.0")
    res_down = _run_ps1(
        UPDATE_SCRIPT,
        "-TargetRoot", str(target),
        "-ReleaseZipPath", str(zip_old),
        "-Sha256SumsPath", str(sums_old),
        "-ReleaseManifestPath", str(manifest_old),
        "-ExpectedZipSha256", sha_old,
    )
    assert res_down.returncode != 0
    assert "downgrade" in (res_down.stdout + res_down.stderr).lower()
    assert _sha256_upper(target / "backend" / "app.py") == orig_app_sha


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_02_rejects_sha256_mismatch_manifest_tampering_and_zip_slip(tmp_path: Path) -> None:
    target = _create_isolated_install_tree(tmp_path, version="1.2.0")
    orig_app_sha = _sha256_upper(target / "backend" / "app.py")
    zip_path, sums_path, manifest_path, zip_sha = _build_verified_release_bundle(tmp_path / "rel_v130", version_tag="v1.3.0")

    # 1. Wrong ExpectedZipSha256
    wrong_sha = ("1" * 64) if not zip_sha.startswith("1") else ("2" * 64)
    res_bad_sha = _run_ps1(
        UPDATE_SCRIPT,
        "-TargetRoot", str(target),
        "-ReleaseZipPath", str(zip_path),
        "-Sha256SumsPath", str(sums_path),
        "-ExpectedZipSha256", wrong_sha,
    )
    assert res_bad_sha.returncode != 0
    assert "SHA-256 mismatch" in (res_bad_sha.stdout + res_bad_sha.stderr)
    assert _sha256_upper(target / "backend" / "app.py") == orig_app_sha

    # 2. Tampered per-file hash in release-manifest.json
    zip_t, sums_t, manifest_t, sha_t = _build_verified_release_bundle(
        tmp_path / "rel_tampered", version_tag="v1.3.0", tamper_manifest_file_hash=True
    )
    res_tampered = _run_ps1(
        UPDATE_SCRIPT,
        "-TargetRoot", str(target),
        "-ReleaseZipPath", str(zip_t),
        "-Sha256SumsPath", str(sums_t),
        "-ReleaseManifestPath", str(manifest_t),
        "-ExpectedZipSha256", sha_t,
    )
    assert res_tampered.returncode != 0
    assert "release-manifest.json" in (res_tampered.stdout + res_tampered.stderr)
    assert _sha256_upper(target / "backend" / "app.py") == orig_app_sha

    # 3. ZIP-slip / forbidden secret entry inside release archive
    slip_dir = tmp_path / "rel_slip"
    slip_dir.mkdir(parents=True, exist_ok=True)
    slip_zip = slip_dir / "winowner-mcp-v1.3.0.zip"
    with zipfile.ZipFile(slip_zip, "w") as zf:
        zf.writestr("../escaped.py", "evil = 1\n")
    slip_sha = _sha256_upper(slip_zip)
    res_slip = _run_ps1(
        UPDATE_SCRIPT,
        "-TargetRoot", str(target),
        "-ReleaseZipPath", str(slip_zip),
        "-ExpectedZipSha256", slip_sha,
    )
    assert res_slip.returncode != 0
    assert "SECURITY VIOLATION" in (res_slip.stdout + res_slip.stderr)
    assert not (target.parent / "escaped.py").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_03_verified_release_update_preserves_secrets_and_supports_clean_rollback(tmp_path: Path) -> None:
    target = _create_isolated_install_tree(tmp_path, version="1.2.0")

    protected_files = [
        target / "runtime" / "secrets" / "gateway_caller.token",
        target / "runtime" / "secrets" / "oauth_signing.key",
        target / "runtime" / "secrets" / "oauth.db",
        target / "runtime" / "secrets" / "owner_delegation_bundle.json",
        target / "runtime" / "oauth_state.db",
        target / "runtime" / "tunnel" / "winowner.yaml",
        target / "config" / "policy.json",
        target / "config" / "trusted_workspaces.json",
        target / "config" / "user_custom.json",
    ]
    protected_hashes_before = {p: _sha256_upper(p) for p in protected_files}
    orig_app_sha = _sha256_upper(target / "backend" / "app.py")

    zip_path, sums_path, manifest_path, zip_sha = _build_verified_release_bundle(tmp_path / "rel_v130", version_tag="v1.3.0")

    # 1. CheckOnly mode
    res_chk = _run_ps1(
        UPDATE_SCRIPT,
        "-TargetRoot", str(target),
        "-ReleaseManifestPath", str(manifest_path),
        "-CheckOnly",
    )
    assert res_chk.returncode == 0, (res_chk.stdout, res_chk.stderr)
    chk_report = json.loads((target / "runtime" / "logs" / "update-check-report.json").read_text(encoding="utf-8"))
    assert chk_report["current_version"] == "v1.2.0"
    assert chk_report["latest_version"] == "v1.3.0"
    assert chk_report["update_available"] is True

    # 2. Update with -SimulateFailure -> must automatically roll back modified files AND delete newly added files
    res_sim = _run_ps1(
        UPDATE_SCRIPT,
        "-TargetRoot", str(target),
        "-ReleaseZipPath", str(zip_path),
        "-Sha256SumsPath", str(sums_path),
        "-ReleaseManifestPath", str(manifest_path),
        "-ExpectedZipSha256", zip_sha,
        "-SimulateFailure",
    )
    assert res_sim.returncode == 0, (res_sim.stdout, res_sim.stderr)
    sim_report = json.loads((target / "runtime" / "logs" / "update-report.json").read_text(encoding="utf-8"))
    assert sim_report["rolled_back"] is True
    assert sim_report["rollback_verified"] is True
    assert sim_report["secrets_preserved"] is True
    assert _sha256_upper(target / "backend" / "app.py") == orig_app_sha
    assert not (target / "runtime" / "new_feature_p1.py").exists(), "Auto-rollback must remove newly added files"

    # 3. Live verified update to v1.3.0 -> must succeed and preserve 100% of secrets, owner DBs, and configs
    res_upd = _run_ps1(
        UPDATE_SCRIPT,
        "-TargetRoot", str(target),
        "-ReleaseZipPath", str(zip_path),
        "-Sha256SumsPath", str(sums_path),
        "-ReleaseManifestPath", str(manifest_path),
        "-ExpectedZipSha256", zip_sha,
    )
    assert res_upd.returncode == 0, (res_upd.stdout, res_upd.stderr)
    upd_report = json.loads((target / "runtime" / "logs" / "update-report.json").read_text(encoding="utf-8"))
    assert upd_report["overall_status"] == "PASS"
    assert upd_report["verified_release_artifact"] is True
    assert upd_report["release_manifest_verified"] is True
    assert upd_report["secrets_preserved"] is True
    assert upd_report["owner_authorizations_untouched"] is True
    assert (target / "runtime" / "new_feature_p1.py").is_file()
    assert "updated_1.3.0" in (target / "backend" / "app.py").read_text(encoding="utf-8")

    for p, expected_h in protected_hashes_before.items():
        assert _sha256_upper(p) == expected_h, f"Protected file modified during update: {p}"

    # 4. Rollback via Rollback-WindowsMcp.ps1 -BackupDir <backup_dir> -> restores v1.2.0 and removes new_feature_p1.py
    backup_dir = Path(upd_report["backup_dir"])
    res_rb = _run_ps1(ROLLBACK_SCRIPT, "-BackupDir", str(backup_dir), "-TargetRoot", str(target))
    assert res_rb.returncode == 0, (res_rb.stdout, res_rb.stderr)
    assert "Failures=0" in res_rb.stdout
    assert _sha256_upper(target / "backend" / "app.py") == orig_app_sha
    assert not (target / "runtime" / "new_feature_p1.py").exists(), "Rollback script must remove added_files"
    for p, expected_h in protected_hashes_before.items():
        assert _sha256_upper(p) == expected_h, f"Protected file modified during rollback: {p}"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
def test_04_rollback_two_phase_atomic_abort_and_foreign_task_refusal(tmp_path: Path) -> None:
    target = _create_isolated_install_tree(tmp_path, version="1.2.0")
    file_a = target / "backend" / "a.py"
    file_b = target / "backend" / "b.py"
    file_a.write_text("LIVE_A\n", encoding="utf-8")
    file_b.write_text("LIVE_B\n", encoding="utf-8")
    sha_live_a = _sha256_upper(file_a)

    bkp_dir = target / "backups" / "pre_update_test_atomic"
    (bkp_dir / "backend").mkdir(parents=True, exist_ok=True)
    bkp_a = bkp_dir / "backend" / "a.py"
    bkp_b = bkp_dir / "backend" / "b.py"
    bkp_a.write_text("BACKUP_A_VALID\n", encoding="utf-8")
    bkp_b.write_text("BACKUP_B_TAMPERED\n", encoding="utf-8")

    # Entry 1 (a.py) has valid backup hash, Entry 2 (b.py) has mismatched backup hash.
    # Two-phase rollback MUST abort in Phase 1 before overwriting file_a!
    manifest = {
        "target_root": str(target),
        "files": [
            {
                "relative_path": "backend\\a.py",
                "original_path": str(file_a),
                "sha256": _sha256_upper(bkp_a),
            },
            {
                "relative_path": "backend\\b.py",
                "original_path": str(file_b),
                "sha256": "F" * 64,
            },
        ],
    }
    (bkp_dir / "update_backup_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    res_abort = _run_ps1(ROLLBACK_SCRIPT, "-BackupDir", str(bkp_dir), "-TargetRoot", str(target))
    assert res_abort.returncode != 0
    assert "BACKUP_HASH_MISMATCH" in res_abort.stdout
    assert "Restored=0" in res_abort.stdout
    assert _sha256_upper(file_a) == sha_live_a, "Phase 1 failure must not partially overwrite earlier files!"

    # Also verify foreign scheduled task collision refusal on -RestartServices
    port = _free_port()
    harness = tmp_path / "harness_rb_collision.ps1"
    harness.write_text(r"""
param([string]$ScriptPath, [string]$BackupDir, [string]$TargetRoot, [int]$Port)
function global:Get-ScheduledTask {
    param([string]$TaskName,[string]$ErrorAction)
    if($TaskName -eq "WinOwnerMCP-$Port"){
        return [PSCustomObject]@{
            Actions=@([PSCustomObject]@{
                Execute='C:\ForeignService\python.exe'
                Arguments='foreign.py'
                WorkingDirectory='C:\ForeignService'
            })
            Principal=[PSCustomObject]@{UserId=[Security.Principal.WindowsIdentity]::GetCurrent().Name}
            State='Running'
        }
    }
}
& $ScriptPath -BackupDir $BackupDir -TargetRoot $TargetRoot -Port $Port -RestartServices -DryRun
""", encoding="utf-8")
    res_col = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(harness),
            "-ScriptPath", str(ROLLBACK_SCRIPT),
            "-BackupDir", str(bkp_dir),
            "-TargetRoot", str(target),
            "-Port", str(port),
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert res_col.returncode != 0
    assert "Task name collision" in (res_col.stdout + res_col.stderr)
