"""Live End-to-End Verification of all 15 Bridge v0.2.1 Read-Only Tools over MCP Streamable HTTP.

Connects to the MCP Bridge via MCP Streamable HTTP (`initialize`, `tools/list`, and `tools/call`)
and independently executes all 15 tools:
  1. get_roots
  2. list_directory
  3. list_tree
  4. stat_path
  5. read_file
  6. read_file_range
  7. get_file
  8. search_files
  9. search_code
  10. grep
  11. disk_usage
  12. path_exists
  13. git_status
  14. git_diff
  15. git_log

By default (when WINOWNER_LIVE_PROD_PORTS != '1'), launches a real isolated uvicorn HTTP server
on an ephemeral loopback port so production ports 8765-8768 are never touched.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import unittest
import urllib.request

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _d in (PROJECT_ROOT / "mcp-gateway", PROJECT_ROOT / "backend", PROJECT_ROOT / "desktop-agent"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from gateway_server import MCPGatewayServer  # noqa: E402
from http_server import create_gateway_http_app  # noqa: E402
from policy import SecurityPolicy  # noqa: E402

MCP_BRIDGE_URL = "http://127.0.0.1:8765/mcp"
REPORT_PATH = Path(tempfile.gettempdir()) / "winowner_live_15_tools_report.json"
POLICY_JSON_PATH = str(PROJECT_ROOT / "config" / "policy.example.json")
CONFIG_DIR_PATH = str(PROJECT_ROOT / "config")
GIT_REPO_CANDIDATES = (PROJECT_ROOT,)

EXPECTED_15_TOOLS: tuple[str, ...] = (
    "get_roots",
    "list_directory",
    "list_tree",
    "stat_path",
    "read_file",
    "read_file_range",
    "get_file",
    "search_files",
    "search_code",
    "grep",
    "disk_usage",
    "path_exists",
    "git_status",
    "git_diff",
    "git_log",
)

STALE_8_TOOLS_SNAPSHOT: frozenset[str] = frozenset((
    "list_directory",
    "read_file",
    "read_file_range",
    "git_status",
    "git_diff",
    "git_log",
    "search_code",
    "path_exists",
))

FORBIDDEN_WRITE_EXEC_TOOLS: tuple[str, ...] = (
    "write_file",
    "append_file",
    "patch_file",
    "replace_text",
    "delete_file",
    "delete_directory",
    "exec_command",
    "powershell_execute",
    "cmd_execute",
    "open_shell_session",
    "exec_in_session",
    "kill_process",
    "start_process",
    "start_task",
    "screenshot",
    "keyboard_hotkey",
    "focus_window",
)


def _allocate_ephemeral_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_http_ready(url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError(f"Isolated bridge HTTP server did not become ready at {url}")


def call_mcp_streamable_http(
    method: str,
    params: dict[str, Any] | None = None,
    req_id: int = 1,
    url: str | None = None,
    timeout: float = 15.0,
) -> tuple[dict[str, Any], float]:
    """Send an MCP JSON-RPC 2.0 request over Streamable HTTP and return (response_dict, latency_ms)."""
    target_url = url or MCP_BRIDGE_URL
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params if params is not None else {},
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        target_url,
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw_text = resp.read().decode("utf-8", errors="replace").strip()
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    for line in raw_text.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("data:"):
            json_str = trimmed[5:].strip()
            if json_str:
                return json.loads(json_str), latency_ms
    if raw_text.startswith("{"):
        return json.loads(raw_text), latency_ms
    raise ValueError(f"Unrecognized MCP Streamable HTTP response: {raw_text[:200]}")


def resolve_test_git_repo() -> str:
    """Select an existing Git repository allowed by the MCP Bridge."""
    for candidate in GIT_REPO_CANDIDATES:
        if (candidate / ".git").exists():
            return str(candidate)
    return str(GIT_REPO_CANDIDATES[0])


def run_live_15_tools_suite() -> dict[str, Any]:
    """Execute the full live 15-tool verification suite and save the JSON report."""
    git_repo_dir = resolve_test_git_repo()
    drive_root = str(Path(PROJECT_ROOT.anchor))

    # 1. MCP initialize handshake
    init_resp, init_latency_ms = call_mcp_streamable_http(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "live-15-tools-e2e-verifier", "version": "1.0.0"},
        },
        req_id=1,
    )
    init_result = init_resp.get("result", {})
    server_info = init_result.get("serverInfo", {})
    init_ok = bool(
        init_result.get("protocolVersion") == "2024-11-05"
        and isinstance(server_info, dict)
        and server_info.get("name")
        and server_info.get("version")
    )

    # 2. MCP tools/list discovery & schema validation
    list_resp, list_latency_ms = call_mcp_streamable_http("tools/list", {}, req_id=2)
    tools_list = list_resp.get("result", {}).get("tools", [])
    discovered_by_name: dict[str, dict[str, Any]] = {
        t["name"]: t for t in tools_list if isinstance(t, dict) and "name" in t
    }
    discovered_names = set(discovered_by_name.keys())
    expected_set = set(EXPECTED_15_TOOLS)

    exact_15_match = discovered_names == expected_set and len(tools_list) == 15
    not_stale_8 = discovered_names != STALE_8_TOOLS_SNAPSHOT
    exposed_forbidden = sorted(discovered_names.intersection(FORBIDDEN_WRITE_EXEC_TOOLS))

    schema_validation: dict[str, dict[str, Any]] = {}
    all_schemas_valid = True
    for name in EXPECTED_15_TOOLS:
        t_obj = discovered_by_name.get(name)
        if not t_obj:
            schema_validation[name] = {"valid": False, "reason": "missing_from_tools_list"}
            all_schemas_valid = False
            continue
        desc = t_obj.get("description")
        schema = t_obj.get("inputSchema")
        is_valid = bool(
            isinstance(desc, str)
            and len(desc.strip()) > 5
            and isinstance(schema, dict)
            and schema.get("type") == "object"
            and isinstance(schema.get("properties"), dict)
        )
        if not is_valid:
            all_schemas_valid = False
        schema_validation[name] = {
            "valid": is_valid,
            "description_length": len(desc.strip()) if isinstance(desc, str) else 0,
            "required": schema.get("required", []) if isinstance(schema, dict) else None,
            "properties": sorted(schema.get("properties", {}).keys()) if isinstance(schema, dict) else None,
        }

    # 3. Verify unapproved write/shell tools are rejected by tools/call
    blocked_Dynamic_checks: list[dict[str, Any]] = []
    for idx, (forbidden_name, forbidden_args) in enumerate(
        (
            ("write_file", {"path": POLICY_JSON_PATH + ".hack", "content": "x"}),
            ("exec_command", {"command": "whoami"}),
        ),
        start=100,
    ):
        f_resp, f_lat = call_mcp_streamable_http(
            "tools/call",
            {"name": forbidden_name, "arguments": forbidden_args},
            req_id=idx,
        )
        f_res = f_resp.get("result", {})
        is_err = bool(f_res.get("isError") is True or "error" in f_resp)
        blocked_Dynamic_checks.append(
            {
                "tool": forbidden_name,
                "blocked_as_expected": is_err,
                "latency_ms": f_lat,
            }
        )

    # 4. Execute each of the 15 read-only tools independently
    tool_test_specs: list[tuple[str, dict[str, Any]]] = [
        ("get_roots", {}),
        ("list_directory", {"path": str(PROJECT_ROOT), "include_hidden": False}),
        ("list_tree", {"path": CONFIG_DIR_PATH, "max_depth": 2, "max_entries": 25}),
        ("stat_path", {"path": POLICY_JSON_PATH}),
        ("read_file", {"path": POLICY_JSON_PATH}),
        ("read_file_range", {"path": POLICY_JSON_PATH, "start_line": 1, "end_line": 8}),
        ("get_file", {"path": POLICY_JSON_PATH}),
        ("search_files", {"root": CONFIG_DIR_PATH, "pattern": "*.json", "recursive": True, "max_results": 10}),
        ("search_code", {"cwd": CONFIG_DIR_PATH, "pattern": "read_only", "file_glob": "*.json", "max_results": 10}),
        ("grep", {"root": CONFIG_DIR_PATH, "pattern": "read_only", "file_glob": "*.json", "max_results": 10}),
        ("disk_usage", {"path": drive_root}),
        ("path_exists", {"path": POLICY_JSON_PATH}),
        ("git_status", {"cwd": git_repo_dir}),
        ("git_diff", {"cwd": git_repo_dir, "staged": False}),
        ("git_log", {"cwd": git_repo_dir, "n": 3}),
    ]

    tool_results: list[dict[str, Any]] = []
    for idx, (tool_name, arguments) in enumerate(tool_test_specs, start=10):
        resp, latency_ms = call_mcp_streamable_http(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            req_id=idx,
        )
        res_obj = resp.get("result", {})
        is_error = bool(res_obj.get("isError", False) or "error" in resp)
        content_items = res_obj.get("content", [])

        passed = False
        summary = ""
        if not is_error and isinstance(content_items, list) and len(content_items) > 0:
            first_item = content_items[0]
            if tool_name == "get_file":
                res_blob = first_item.get("resource", {}).get("blob") if isinstance(first_item, dict) else None
                if not res_blob and isinstance(first_item, dict) and isinstance(first_item.get("text"), str):
                    try:
                        parsed_file = json.loads(first_item["text"])
                        if isinstance(parsed_file, dict):
                            res_blob = (
                                (parsed_file.get("resource") or {}).get("blob")
                                or parsed_file.get("data_base64")
                            )
                    except json.JSONDecodeError:
                        res_blob = None
                if isinstance(res_blob, str) and len(res_blob) > 0:
                    decoded = base64.b64decode(res_blob).decode("utf-8", errors="replace")
                    passed = "read_only" in decoded
                    summary = f"EmbeddedResource blob ({len(res_blob)} base64 chars, decoded {len(decoded)} chars)"
            else:
                text_val = first_item.get("text", "") if isinstance(first_item, dict) else ""
                passed = isinstance(text_val, str) and len(text_val) > 0
                summary = text_val[:160].replace("\n", " ")
        else:
            summary = f"Error or empty response: {json.dumps(resp)[:160]}"

        tool_results.append(
            {
                "index": idx - 9,
                "tool": tool_name,
                "arguments": arguments,
                "status": "PASS" if passed else "FAIL",
                "passed": passed,
                "is_error": is_error,
                "latency_ms": latency_ms,
                "response_preview": summary,
            }
        )

    passed_tools_count = sum(1 for item in tool_results if item["passed"])
    overall_passed = bool(
        init_ok
        and exact_15_match
        and not_stale_8
        and len(exposed_forbidden) == 0
        and all_schemas_valid
        and all(c["blocked_as_expected"] for c in blocked_Dynamic_checks)
        and passed_tools_count == 15
    )

    report = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": MCP_BRIDGE_URL,
        "overall_status": "PASS" if overall_passed else "FAIL",
        "overall_passed": overall_passed,
        "initialize_handshake": {
            "passed": init_ok,
            "latency_ms": init_latency_ms,
            "protocol_version": init_result.get("protocolVersion"),
            "server_info": server_info,
        },
        "discovery_verification": {
            "latency_ms": list_latency_ms,
            "discovered_count": len(tools_list),
            "expected_count": 15,
            "exact_15_match": exact_15_match,
            "not_stale_8_tool_snapshot": not_stale_8,
            "zero_hidden_write_or_shell_tools": len(exposed_forbidden) == 0,
            "exposed_forbidden_tools": exposed_forbidden,
            "all_schemas_valid": all_schemas_valid,
            "discovered_tools": sorted(discovered_names),
            "schema_validation": schema_validation,
        },
        "negative_security_checks": blocked_Dynamic_checks,
        "tools_summary": {
            "passed": passed_tools_count,
            "total": len(tool_results),
            "total_latency_ms": round(sum(r["latency_ms"] for r in tool_results), 2),
        },
        "tool_results": tool_results,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


class TestLive15BridgeTools(unittest.TestCase):
    """Live E2E test suite verifying all 15 read-only tools over MCP Streamable HTTP."""

    _tmp_dir: tempfile.TemporaryDirectory[str] | None = None
    _server: uvicorn.Server | None = None
    _thread: threading.Thread | None = None

    @classmethod
    def setUpClass(cls) -> None:
        global MCP_BRIDGE_URL, REPORT_PATH, POLICY_JSON_PATH, CONFIG_DIR_PATH, GIT_REPO_CANDIDATES
        if os.environ.get("WINOWNER_LIVE_PROD_PORTS") != "1":
            cls._tmp_dir = tempfile.TemporaryDirectory(prefix="live_15_bridge_isolated_")
            base = Path(cls._tmp_dir.name).resolve()
            cfg_dir = base / "config"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            policy_data = json.loads((PROJECT_ROOT / "config" / "policy.example.json").read_text(encoding="utf-8"))
            drive_anchor = str(Path(PROJECT_ROOT.anchor))
            repo_dir = base / "git_repo"
            repo_dir.mkdir(parents=True, exist_ok=True)

            git_exe = shutil.which("git")
            if git_exe:
                subprocess.run([git_exe, "init", "-q"], cwd=str(repo_dir), check=False, capture_output=True)
                subprocess.run([git_exe, "config", "user.email", "test@example.invalid"], cwd=str(repo_dir), check=False, capture_output=True)
                subprocess.run([git_exe, "config", "user.name", "Test Owner"], cwd=str(repo_dir), check=False, capture_output=True)
                (repo_dir / "README.md").write_text("# Bridge Test Repo\n", encoding="utf-8")
                subprocess.run([git_exe, "add", "README.md"], cwd=str(repo_dir), check=False, capture_output=True)
                subprocess.run([git_exe, "commit", "-q", "-m", "Initial commit"], cwd=str(repo_dir), check=False, capture_output=True)

            policy_data["allowed_read_roots"] = [str(PROJECT_ROOT), str(base), drive_anchor]
            policy_data["allowed_git_repos"] = [str(repo_dir), str(PROJECT_ROOT)]
            policy_file = cfg_dir / "policy.json"
            policy_file.write_text(json.dumps(policy_data, indent=2), encoding="utf-8")

            POLICY_JSON_PATH = str(policy_file)
            CONFIG_DIR_PATH = str(cfg_dir)
            REPORT_PATH = base / "evidence" / "live_15_tools_report.json"
            GIT_REPO_CANDIDATES = (repo_dir, PROJECT_ROOT)

            port = _allocate_ephemeral_port()
            pol = SecurityPolicy(
                policy_path=policy_file,
                profile="read_only",
                audit_log_path=base / "bridge_audit.jsonl",
            )
            gw = MCPGatewayServer(policy=pol)
            gw.server_version = "0.2.1"
            app = create_gateway_http_app(
                gateway=gw,
                caller_auth_token="test_isolated_bridge15_token_1234567890",
                db_path=base / "bridge15_oauth.db",
            )
            cls._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
            cls._thread = threading.Thread(target=cls._server.run, daemon=True)
            cls._thread.start()
            MCP_BRIDGE_URL = f"http://127.0.0.1:{port}/mcp"
            _wait_http_ready(f"http://127.0.0.1:{port}/health")

        cls.report = run_live_15_tools_suite()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._server is not None:
            cls._server.should_exit = True
        if cls._thread is not None:
            cls._thread.join(timeout=5.0)
        cls._server = None
        cls._thread = None
        if cls._tmp_dir is not None:
            cls._tmp_dir.cleanup()
            cls._tmp_dir = None

    def test_01_mcp_initialize_handshake(self) -> None:
        init = self.report["initialize_handshake"]
        self.assertTrue(init["passed"], f"MCP initialize failed: {init}")
        self.assertEqual(init["protocol_version"], "2024-11-05")
        self.assertEqual(init["server_info"].get("version"), "0.2.1")

    def test_02_strict_15_tools_discovery_and_schemas(self) -> None:
        disc = self.report["discovery_verification"]
        self.assertEqual(disc["discovered_count"], 15)
        self.assertTrue(disc["exact_15_match"])
        self.assertTrue(disc["not_stale_8_tool_snapshot"])
        self.assertTrue(disc["zero_hidden_write_or_shell_tools"])
        self.assertTrue(disc["all_schemas_valid"])

    def test_03_write_and_shell_calls_rejected_on_bridge(self) -> None:
        for check in self.report["negative_security_checks"]:
            self.assertTrue(
                check["blocked_as_expected"],
                f"Forbidden tool {check['tool']} was not rejected on live bridge",
            )

    def test_04_all_15_tools_pass_individually(self) -> None:
        results_by_name = {r["tool"]: r for r in self.report["tool_results"]}
        self.assertEqual(len(results_by_name), 15)
        for tool_name in EXPECTED_15_TOOLS:
            self.assertIn(tool_name, results_by_name)
            res = results_by_name[tool_name]
            self.assertTrue(
                res["passed"],
                f"Live tool {tool_name} failed (is_error={res['is_error']}): {res['response_preview']}",
            )

    def test_05_report_file_saved(self) -> None:
        self.assertTrue(REPORT_PATH.exists(), f"Report file not created at {REPORT_PATH}")
        saved = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        self.assertTrue(saved["overall_passed"])
        self.assertEqual(saved["tools_summary"]["passed"], 15)


if __name__ == "__main__":
    unittest.main()
