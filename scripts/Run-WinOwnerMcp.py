"""WinOwner MCP scheduled-task entrypoint (current Windows owner).

No bearer secrets in Scheduled Task arguments, Git history, or command line.
This process directly hosts uvicorn, allowing Task Scheduler to stop it.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


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
    )
    if open_browser:
        def _launch_browser() -> None:
            time.sleep(0.8)
            webbrowser.open(f"http://127.0.0.1:{console_port}/setup")
        threading.Thread(target=_launch_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=console_port, log_level="info")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WinOwner MCP local authenticated owner task")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--profile", choices=("read_only", "owner_full_control"), default="read_only")
    parser.add_argument("--setup-center", action="store_true", help="Launch local loopback WinOwner Setup Center UI")
    parser.add_argument("--console-port", type=int, default=8767, help="Loopback port for WinOwner Setup Center UI")
    parser.add_argument("--open-browser", action="store_true", help="Open WinOwner Setup Center in default browser")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.setup_center:
        if not 1024 <= args.console_port <= 65535:
            parser.error("console-port must be between 1024 and 65535")
        start_setup_center(args.port, args.console_port, args.profile, args.open_browser)
    else:
        start(args.port, args.profile)
