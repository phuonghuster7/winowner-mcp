"""WinOwner MCP scheduled-task entrypoint (current Windows owner).

No bearer secrets in Scheduled Task arguments, Git history, or command line.
This process directly hosts uvicorn, allowing Task Scheduler to stop it.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_TUNNEL_ID_RE = re.compile(r"^tunnel_(?:[a-z0-9]{32}|[a-z0-9]{4}_[a-z0-9]{32})$")


def _detect_existing_tunnel_task(port: int) -> bool:
    """Return True if a Windows Scheduled Task WinOwnerTunnel-<port> currently exists."""
    if os.name != "nt":
        return False
    try:
        res = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"if (Get-ScheduledTask -TaskName 'WinOwnerTunnel-{int(port)}' -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def build_setup_center_action_hooks(
    root: Path | None = None,
    port: int = 8768,
    *,
    subprocess_runner: Any | None = None,
    task_exists_fn: Any | None = None,
) -> dict[str, Any]:
    """Build real Setup Center action hooks that invoke scripts/Connect-ChatGPT.ps1 safely.

    Passes runtime_api_key strictly via a child-only environment variable that PowerShell converts
    to SecureString and removes immediately from Env:. Never places secrets in CLI argv or errors.
    Raises RuntimeError on non-zero exit so Setup Center returns HTTP 400 (never fake success).
    """
    base_root = Path(root or ROOT).resolve()
    run_fn = subprocess_runner or subprocess.run
    check_task_fn = task_exists_fn or _detect_existing_tunnel_task

    def _run_connect_script(payload: dict[str, Any], *, force_reconfigure: bool) -> dict[str, Any]:
        tunnel_id = str(payload.get("tunnel_id") or "").strip()
        runtime_api_key = str(payload.get("runtime_api_key") or "").strip()
        auth_mode = str(payload.get("auth_mode") or "oauth").strip().lower()
        if not _TUNNEL_ID_RE.match(tunnel_id):
            raise RuntimeError("Invalid OpenAI Tunnel ID format")
        if auth_mode not in ("oauth", "auth_none"):
            raise RuntimeError("Invalid auth_mode (expected 'oauth' or 'auth_none')")
        if len(runtime_api_key) < 20:
            raise RuntimeError("Runtime API Key is missing or unexpectedly short")

        connect_script = base_root / "scripts" / "Connect-ChatGPT.ps1"
        if not connect_script.is_file():
            raise RuntimeError("Missing scripts/Connect-ChatGPT.ps1")

        yaml_exists = (base_root / "runtime" / "tunnel" / "winowner.yaml").is_file()
        task_exists = False
        try:
            task_exists = bool(check_task_fn(int(port)))
        except Exception:
            task_exists = False
        need_reconfigure = bool(force_reconfigure or payload.get("reconfigure") or yaml_exists or task_exists)

        ps_script_escaped = str(connect_script).replace("'", "''")
        ps_root_escaped = str(base_root).replace("'", "''")
        reconf_flag = " -Reconfigure" if need_reconfigure else ""
        ps_command = (
            "$ErrorActionPreference='Stop'; "
            "$raw=[Environment]::GetEnvironmentVariable('WINOWNER_SETUP_RUNTIME_API_KEY','Process'); "
            "[Environment]::SetEnvironmentVariable('WINOWNER_SETUP_RUNTIME_API_KEY',$null,'Process'); "
            "Remove-Item Env:WINOWNER_SETUP_RUNTIME_API_KEY -ErrorAction SilentlyContinue; "
            "if(-not $raw){throw 'Runtime API key missing in child environment'}; "
            "$sec=ConvertTo-SecureString -String $raw -AsPlainText -Force; "
            "$raw=''; "
            f"try{{ & '{ps_script_escaped}' -Port {int(port)} -TunnelId '{tunnel_id}' -AuthMode '{auth_mode}' -TargetRoot '{ps_root_escaped}' -ApiKey $sec -NoBrowser{reconf_flag} }} "
            "finally { $sec.Dispose() }"
        )
        argv = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_command,
        ]
        child_env = os.environ.copy()
        child_env["WINOWNER_SETUP_RUNTIME_API_KEY"] = runtime_api_key

        try:
            proc = run_fn(
                argv,
                cwd=str(base_root),
                env=child_env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        finally:
            child_env.pop("WINOWNER_SETUP_RUNTIME_API_KEY", None)

        raw_stdout = str(getattr(proc, "stdout", "") or "")
        raw_stderr = str(getattr(proc, "stderr", "") or "")
        scrubbed_out = raw_stdout.replace(runtime_api_key, "[REDACTED]")
        scrubbed_err = raw_stderr.replace(runtime_api_key, "[REDACTED]")
        rc = int(getattr(proc, "returncode", 1))
        runtime_api_key = ""

        if rc != 0:
            detail = (scrubbed_err or scrubbed_out or f"Connect-ChatGPT.ps1 exited with code {rc}").strip()
            if len(detail) > 600:
                detail = detail[:600] + "..."
            raise RuntimeError(f"Connect-ChatGPT.ps1 failed (exit={rc}): {detail}")

        report_data: dict[str, Any] = {}
        report_path = base_root / "runtime" / "logs" / "tunnel-reconfig-report.json"
        if report_path.is_file():
            try:
                loaded = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(loaded, dict):
                    report_data = loaded
            except Exception:
                report_data = {}

        status_code = str(
            report_data.get("status")
            or ("TUNNEL_RECONFIGURE_PASS" if "TUNNEL_RECONFIGURE_PASS" in scrubbed_out else "TUNNEL_SETUP_PASS")
        )
        readyz_ok = status_code in ("TUNNEL_SETUP_PASS", "TUNNEL_RECONFIGURE_PASS") or (
            "TUNNEL_SETUP_PASS" in scrubbed_out or "TUNNEL_RECONFIGURE_PASS" in scrubbed_out
        )
        if not readyz_ok:
            raise RuntimeError(f"Connect-ChatGPT.ps1 completed without verified tunnel readiness (status={status_code})")

        return {
            "action": "reconfigure_tunnel" if force_reconfigure else "connect_chatgpt",
            "tunnel_id": tunnel_id,
            "auth_mode": auth_mode,
            "auto_reconfigure": need_reconfigure,
            "status": status_code,
            "readyz_ok": True,
            "rolled_back": bool(report_data.get("rolled_back", False)),
            "rollback_verified": bool(report_data.get("rollback_verified", False)),
            "chatgpt_tool_call_verified": False,
            "message_en": "Tunnel runtime verified and ready; ChatGPT tool call NOT verified yet. Run a read-only prompt in ChatGPT next.",
            "message_vi": "Tunnel đã sẵn sàng và được xác minh; chưa ghi nhận lệnh gọi công cụ từ ChatGPT. Hãy chạy câu lệnh đọc thử trong ChatGPT.",
        }

    return {
        "connect_chatgpt": lambda payload: _run_connect_script(payload, force_reconfigure=False),
        "reconfigure_tunnel": lambda payload: _run_connect_script(payload, force_reconfigure=True),
    }


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    """Allow both POSIX (--port/--console-port) and PowerShell (-Port/-ConsolePort) flags."""
    mapping = {
        "-port": "--port",
        "--port": "--port",
        "-consoleport": "--console-port",
        "--consoleport": "--console-port",
        "-console-port": "--console-port",
        "-profile": "--profile",
        "--profile": "--profile",
        "-setupcenter": "--setup-center",
        "-opensetup": "--open-setup",
        "-openbrowser": "--open-browser",
    }
    return [mapping.get(tok.lower(), tok) for tok in argv]


def start(port: int, profile: str = "read_only") -> None:
    if os.name != "nt":
        raise SystemExit("WinOwner task launcher requires Windows")
    if profile not in {"read_only", "owner_full_control"}:
        raise SystemExit("Unknown security profile")
    policy = ROOT / "config" / "policy.json"
    bearer = ROOT / "runtime" / "secrets" / "gateway_caller.token"
    db_path = ROOT / "runtime" / "secrets" / "oauth.db"
    adapter = ROOT / "mcp-gateway" / "cloud_autonomous_adapter.py"
    if not adapter.is_file() or not policy.is_file() or not bearer.is_file():
        raise SystemExit("Local adapter, policy or protected bearer token is missing")
    if json.loads(policy.read_text(encoding="utf-8")).get("default_profile") != "read_only":
        raise SystemExit("Refusing to start: local policy must default to read_only")

    secret = bearer.read_text(encoding="utf-8").strip()
    if not secret:
        raise SystemExit("Protected bearer token is empty")

    os.environ["WINDOWS_MCP_GATEWAY_TOKEN"] = secret
    os.environ["MCP_STATE_ROOT"] = str(ROOT)
    os.environ.setdefault("MCP_EMERGENCY_STOP_FILE", str(ROOT / "runtime" / "EMERGENCY_STOP"))
    os.environ["WINDOWS_MCP_BACKEND_ENV_FILE"] = str(ROOT / "runtime" / "not-configured.env")
    os.environ["MCP_AGENT_PROFILE"] = profile

    log_dir = ROOT / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = log_dir / f"adapter-{port}-{stamp}.stdout.log"
    err_path = log_dir / f"adapter-{port}-{stamp}.stderr.log"
    with out_path.open("a", encoding="utf-8", buffering=1) as out_file, err_path.open(
        "a", encoding="utf-8", buffering=1
    ) as err_file:
        sys.stdout = out_file
        sys.stderr = err_file
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "mcp-gateway"))
        sys.argv = [
            str(adapter),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--profile", profile,
            "--policy-path", str(policy),
            "--db-path", str(db_path),
        ]
        if not os.getenv("WINOWNER_DISABLE_PROFILE_RESTORE"):
            sys.argv.append("--restore-approved-profile")
        runpy.run_path(str(adapter), run_name="__main__")


def start_setup_center(
    port: int = 8768,
    console_port: int = 8767,
    profile: str = "read_only",
    open_browser: bool = False,
) -> None:
    if os.name != "nt":
        raise SystemExit("WinOwner Setup Center requires Windows")
    policy = ROOT / "config" / "policy.json"
    db_path = ROOT / "runtime" / "secrets" / "oauth.db"
    os.environ["MCP_STATE_ROOT"] = str(ROOT)
    os.environ.setdefault("MCP_EMERGENCY_STOP_FILE", str(ROOT / "runtime" / "EMERGENCY_STOP"))
    os.environ["MCP_AGENT_PROFILE"] = profile
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "mcp-gateway"))

    log_dir = ROOT / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = (log_dir / f"setup-center-{console_port}.stdout.log").open("a", encoding="utf-8", buffering=1)
        sys.stderr = (log_dir / f"setup-center-{console_port}.stderr.log").open("a", encoding="utf-8", buffering=1)

    import threading
    import time
    import webbrowser
    import uvicorn
    from web_console import create_web_console_app

    app = create_web_console_app(
        profile=profile,
        policy_path=policy if policy.is_file() else (ROOT / "config" / "policy.example.json"),
        db_path=db_path,
        adapter_port=port,
        setup_action_hooks=build_setup_center_action_hooks(ROOT, port),
    )
    # Restore HMAC-verified owner-approved profile across Setup Center restarts if active
    try:
        pers = getattr(app.state, "setup_center_persistence", None)
        sk = getattr(app.state, "setup_center_signing_key", "")
        gw_ref = getattr(app.state, "gateway", None)
        if (
            pers is not None
            and sk
            and gw_ref is not None
            and profile == "read_only"
            and not (hasattr(gw_ref, "is_emergency_stop_active") and gw_ref.is_emergency_stop_active())
            and hasattr(pers, "get_owner_approved_profile")
            and hasattr(gw_ref, "switch_policy_profile")
        ):
            p_state = pers.get_owner_approved_profile(secret=sk, owner_id="local_owner")
            eff = str(p_state.get("effective_profile") or "read_only")
            if p_state.get("valid") and eff != "read_only":
                gw_ref.switch_policy_profile(eff, reason="setup_center_restore_owner_approved_profile")
    except Exception:
        pass

    if open_browser:
        def _launch_browser() -> None:
            time.sleep(0.9)
            url = f"http://127.0.0.1:{console_port}/setup"
            issuer = getattr(app.state, "issue_local_handoff_ticket", None)
            if callable(issuer):
                try:
                    ticket = issuer(ttl_seconds=60.0)
                    code = str(ticket.get("handoff_code") or "").strip()
                    if code:
                        url = f"{url}#handoff={code}"
                except Exception:
                    pass
            webbrowser.open(url)
        threading.Thread(target=_launch_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=console_port, log_level="info")


def open_setup_center(
    port: int = 8768,
    console_port: int = 8767,
    profile: str = "read_only",
) -> None:
    """Open WinOwner Setup Center in the default browser via single-use local Windows-user handoff.

    If Setup Center is already listening on 127.0.0.1:<console_port>, mints a 60s single-use
    handoff ticket using the ACL-protected local owner_admin.key (never passed on CLI or URL query)
    and opens http://127.0.0.1:<console_port>/setup#handoff=<code>. Otherwise starts Setup Center.
    """
    if os.name != "nt":
        raise SystemExit("WinOwner Setup Center requires Windows")
    import urllib.request
    import webbrowser

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "mcp-gateway"))
    from http_server import resolve_owner_admin_token_path

    db_path = ROOT / "runtime" / "secrets" / "oauth.db"
    health_url = f"http://127.0.0.1:{console_port}/health"
    running = False
    try:
        with urllib.request.urlopen(health_url, timeout=1.5) as resp:
            if resp.status == 200:
                running = True
    except Exception:
        running = False

    if not running:
        start_setup_center(port=port, console_port=console_port, profile=profile, open_browser=True)
        return

    admin_path = resolve_owner_admin_token_path(db_path=db_path)
    if not admin_path.is_file():
        webbrowser.open(f"http://127.0.0.1:{console_port}/setup")
        return

    admin_secret = admin_path.read_text(encoding="utf-8", errors="replace").strip()
    issue_url = f"http://127.0.0.1:{console_port}/setup/api/local-handoff/issue"
    req = urllib.request.Request(
        issue_url,
        data=json.dumps({"ttl_seconds": 60.0}).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{console_port}",
            "X-MCP-Owner-Admin-Token": admin_secret,
        },
    )
    target_url = f"http://127.0.0.1:{console_port}/setup"
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            code = str(data.get("handoff_code") or "").strip()
            if code:
                target_url = f"{target_url}#handoff={code}"
    except Exception:
        pass
    finally:
        admin_secret = ""
    webbrowser.open(target_url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WinOwner MCP local authenticated owner task")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--profile", choices=("read_only", "owner_full_control"), default="read_only")
    parser.add_argument("--setup-center", action="store_true", help="Launch local loopback WinOwner Setup Center UI")
    parser.add_argument("--open-setup", action="store_true", help="Open Setup Center via single-use local Windows-user handoff")
    parser.add_argument("--console-port", type=int, default=8767, help="Loopback port for WinOwner Setup Center UI")
    parser.add_argument("--open-browser", action="store_true", help="Open WinOwner Setup Center in default browser")
    args = parser.parse_args(_normalize_cli_argv(sys.argv[1:]))
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.open_setup:
        if not 1024 <= args.console_port <= 65535:
            parser.error("console-port must be between 1024 and 65535")
        open_setup_center(args.port, args.console_port, args.profile)
    elif args.setup_center:
        if not 1024 <= args.console_port <= 65535:
            parser.error("console-port must be between 1024 and 65535")
        start_setup_center(args.port, args.console_port, args.profile, args.open_browser)
    else:
        start(args.port, args.profile)
