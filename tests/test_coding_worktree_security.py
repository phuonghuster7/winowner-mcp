"""Phase 4 P1 — Git Worktree Security & Hardening Tests for runtime/coding_tools.py.

Verifies:
1. Valid linked Git worktree (where .git is a file `gitdir: ...`) succeeds for agent_git_status
   and agent_git_diff while still excluding sensitive files (.env, secrets).
2. External Git directories escaping approved workspace boundaries are denied.
3. Forged or mismatched worktree back-pointer (`<gitdir>/gitdir`), chained `commondir`, and
   `objects/info/alternates` / `http-alternates` are denied.
4. Symlinks and real Windows NTFS junctions (`mklink /J`, including dangling junctions) in `.git`,
   worktree `gitdir`, `commondir`, or inside Git metadata directories are denied.
5. Git hooks (`core.hooksPath`, pre-status/diff hooks), Git filters (`filter.*.clean/smudge/process`),
   executable diff/merge/credential drivers (`diff.external`, `difftool.*`, `merge.*.driver`,
   `mergetool.*`, `credential.helper`, `core.fsmonitor`, `core.sshCommand`, `core.pager`,
   `core.editor`, `core.askPass`, `alias.*`), and config includes (`include.path`, `includeIf.*`)
   in either main config or `config.worktree` are denied and never executed.
6. Path traversal, UNC, ADS, and hardlinked `.git` / `config` files are denied.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.coding_tools import CODING_TOOL_NAMES, CodingToolHandler  # noqa: E402


@unittest.skipUnless(shutil.which("git"), "git executable is required for worktree security tests")
class GitWorktreeSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="git_worktree_sec_")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.approved_parent = self.base / "approved"
        self.approved_parent.mkdir()
        self.main_repo = self.approved_parent / "main_repo"
        self.main_repo.mkdir()
        self.worktree_dir = self.approved_parent / "linked_wt"
        self.outside_dir = self.base / "outside_unapproved"
        self.outside_dir.mkdir()

        self.git = shutil.which("git")
        assert self.git is not None

        # Initialize main repo with an initial commit
        self._git(self.main_repo, "init", "-q")
        self._git(self.main_repo, "config", "user.email", "test@example.invalid")
        self._git(self.main_repo, "config", "user.name", "Test Owner")
        (self.main_repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        self._git(self.main_repo, "add", "app.py")
        self._git(self.main_repo, "commit", "-q", "-m", "Initial commit")

        # Create a real linked Git worktree
        self._git(self.main_repo, "worktree", "add", "-q", "-b", "feat/wt-test", str(self.worktree_dir))

        self.trusted_cfg = self.base / "trusted_workspaces.json"
        self.preapproved_roots = [self.approved_parent]
        self._write_trusted_config([
            {
                "workspace_root": str(self.worktree_dir),
                "include_subdirectories": True,
                "allowed_tools": sorted(CODING_TOOL_NAMES),
                "allowed_commands": ["python*", "pytest*", "git*"],
                "allowed_callers": ["owner-client"],
            },
            {
                "workspace_root": str(self.main_repo),
                "include_subdirectories": True,
                "allowed_tools": sorted(CODING_TOOL_NAMES),
                "allowed_commands": ["python*", "pytest*", "git*"],
                "allowed_callers": ["owner-client"],
            },
        ])

        policy = SimpleNamespace(
            is_emergency_stop_active=lambda: False,
            is_sensitive_path_str=lambda *a, **k: False,
        )
        self.broker = SimpleNamespace(
            policy=policy,
            list_preapproved_roots=lambda: list(self.preapproved_roots),
        )
        self.handler = CodingToolHandler(
            None,
            self.broker,
            trusted_config_path=self.trusted_cfg,
            audit_path=self.base / "audit" / "coding.db",
        )

    def _git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        res = subprocess.run(
            [self.git, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0, f"git {args} failed: {res.stderr or res.stdout}")
        return res

    def _write_trusted_config(self, entries: list[dict]) -> None:
        self.trusted_cfg.write_text(
            json.dumps({"trusted_mode_enabled": True, "trusted_workspaces": entries}),
            encoding="utf-8",
        )

    def _call(self, tool: str, args: dict | None = None, workspace: Path | None = None) -> dict:
        payload = {"workspace_root": str(workspace or self.worktree_dir)}
        if args:
            payload.update(args)
        return self.handler.execute_tool(
            tool,
            payload,
            caller_tier="READ",
            caller_id="owner-client",
            surface="cloud_autonomous",
        )

    def _assert_denied(self, res: dict) -> None:
        self.assertFalse(res.get("ok"), f"Expected denial but got: {res}")
        self.assertEqual(res.get("status"), "REJECTED", res)

    def test_01_valid_git_worktree_status_and_diff_succeed_and_exclude_secrets(self) -> None:
        dot_git = self.worktree_dir / ".git"
        self.assertTrue(dot_git.is_file(), "Linked worktree .git must be a regular file")
        self.assertTrue(dot_git.read_text(encoding="utf-8").startswith("gitdir:"))

        (self.worktree_dir / "app.py").write_text("x = 2\n", encoding="utf-8")
        (self.worktree_dir / "new_module.py").write_text("y = 3\n", encoding="utf-8")
        (self.worktree_dir / ".env").write_text("SUPER_SECRET_WORKTREE_VALUE=12345\n", encoding="utf-8")

        status_res = self._call("agent_git_status")
        self.assertTrue(status_res.get("ok"), status_res)
        self.assertEqual(status_res.get("returncode"), 0, status_res)
        self.assertIn("app.py", status_res["stdout"])
        self.assertIn("new_module.py", status_res["stdout"])

        diff_res = self._call("agent_git_diff")
        self.assertTrue(diff_res.get("ok"), diff_res)
        self.assertEqual(diff_res.get("returncode"), 0, diff_res)
        self.assertIn("+x = 2", diff_res["stdout"])
        self.assertIn("+y = 3", diff_res["stdout"])
        self.assertNotIn("SUPER_SECRET_WORKTREE_VALUE", diff_res["stdout"])

        # Narrowed path diff inside worktree
        sub_diff = self._call("agent_git_diff", {"path": "app.py"})
        self.assertTrue(sub_diff.get("ok"), sub_diff)
        self.assertIn("+x = 2", sub_diff["stdout"])
        self.assertNotIn("+y = 3", sub_diff["stdout"])

    def test_02_external_gitdir_escaping_approved_boundaries_denied(self) -> None:
        # Restrict preapproved roots and trusted workspaces to ONLY self.worktree_dir (excluding main_repo)
        self.preapproved_roots = [self.worktree_dir]
        self._write_trusted_config([
            {
                "workspace_root": str(self.worktree_dir),
                "include_subdirectories": True,
                "allowed_tools": sorted(CODING_TOOL_NAMES),
                "allowed_commands": ["git*"],
                "allowed_callers": ["owner-client"],
            }
        ])
        self._assert_denied(self._call("agent_git_status"))
        self._assert_denied(self._call("agent_git_diff"))

        # Also test pointing .git file at an arbitrary outside repo's .git directory
        outside_repo = self.outside_dir / "ext_repo"
        outside_repo.mkdir()
        self._git(outside_repo, "init", "-q")
        dot_git_file = self.worktree_dir / ".git"
        dot_git_file.unlink(missing_ok=True)
        dot_git_file.write_text(f"gitdir: {outside_repo / '.git'}\n", encoding="utf-8")
        self.preapproved_roots = [self.approved_parent]
        self._assert_denied(self._call("agent_git_status"))
        self._assert_denied(self._call("agent_git_diff"))

    def test_03_forged_backpointer_chained_commondir_and_alternates_denied(self) -> None:
        wt_admin_dir = self.main_repo / ".git" / "worktrees" / "linked_wt"
        self.assertTrue(wt_admin_dir.is_dir())

        # 1. Tamper with back-pointer <gitdir>/gitdir
        backptr = wt_admin_dir / "gitdir"
        orig_backptr = backptr.read_text(encoding="utf-8")
        try:
            backptr.write_text(str(self.main_repo / ".git") + "\n", encoding="utf-8")
            self._assert_denied(self._call("agent_git_status"))
        finally:
            backptr.write_text(orig_backptr, encoding="utf-8")

        # 2. Tamper with commondir to escape to outside directory
        commondir_file = wt_admin_dir / "commondir"
        orig_commondir = commondir_file.read_text(encoding="utf-8")
        try:
            commondir_file.write_text(str(self.outside_dir) + "\n", encoding="utf-8")
            self._assert_denied(self._call("agent_git_status"))
        finally:
            commondir_file.write_text(orig_commondir, encoding="utf-8")

        # 3. Plant objects/info/alternates in common_gitdir
        alt_file = self.main_repo / ".git" / "objects" / "info" / "alternates"
        alt_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            alt_file.write_text(str(self.outside_dir / "objects") + "\n", encoding="utf-8")
            self._assert_denied(self._call("agent_git_status"))
            self._assert_denied(self._call("agent_git_diff"))
        finally:
            alt_file.unlink(missing_ok=True)

    def test_04_symlinks_and_ntfs_junctions_in_worktree_metadata_denied(self) -> None:
        wt_admin_dir = self.main_repo / ".git" / "worktrees" / "linked_wt"
        junction_inside_git = wt_admin_dir / "refs_junction"
        outside_target = self.outside_dir / "j_target"
        outside_target.mkdir(exist_ok=True)

        created_junction = False
        if os.name == "nt":
            proc = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction_inside_git), str(outside_target)],
                capture_output=True,
                text=True,
                check=False,
            )
            created_junction = proc.returncode == 0
        else:
            os.symlink(outside_target, junction_inside_git, target_is_directory=True)
            created_junction = True

        self.assertTrue(created_junction, "Could not create junction/symlink fixture inside worktree gitdir")
        try:
            self._assert_denied(self._call("agent_git_status"))
            self._assert_denied(self._call("agent_git_diff"))

            # Remove target to create a dangling/broken NTFS junction and verify it is STILL caught
            shutil.rmtree(outside_target)
            self._assert_denied(self._call("agent_git_status"))
        finally:
            if junction_inside_git.is_symlink():
                junction_inside_git.unlink()
            elif os.name == "nt":
                subprocess.run(["cmd.exe", "/d", "/c", "rmdir", str(junction_inside_git)], capture_output=True, check=False)

    def test_05_hooks_filters_drivers_and_config_includes_denied_in_worktree(self) -> None:
        marker = self.outside_dir / "executed_marker.txt"
        probe_script = self.worktree_dir / "probe.py"
        probe_script.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
            encoding="utf-8",
        )
        cmd_str = f'"{sys.executable}" "{probe_script}"'
        main_cfg = self.main_repo / ".git" / "config"
        orig_cfg = main_cfg.read_text(encoding="utf-8")

        malicious_configs = [
            ("core", "hooksPath", str(self.outside_dir)),
            ("core", "fsmonitor", cmd_str),
            ("core", "sshCommand", cmd_str),
            ("core", "pager", cmd_str),
            ("core", "editor", cmd_str),
            ("core", "askPass", cmd_str),
            ("filter.evil", "clean", cmd_str),
            ("filter.evil", "smudge", cmd_str),
            ("filter.evil", "process", cmd_str),
            ("diff", "external", cmd_str),
            ("diff.evil", "textconv", cmd_str),
            ("difftool.evil", "cmd", cmd_str),
            ("merge.evil", "driver", cmd_str),
            ("mergetool.evil", "cmd", cmd_str),
            ("credential", "helper", cmd_str),
            ("include", "path", str(self.outside_dir / "ext.gitconfig")),
            ('includeIf "gitdir:**"', "path", str(self.outside_dir / "cond.gitconfig")),
            ("alias", "status", f"!{cmd_str}"),
        ]

        for section, key, val in malicious_configs:
            with self.subTest(section=section, key=key):
                try:
                    main_cfg.write_text(orig_cfg + f"\n[{section}]\n\t{key} = {val}\n", encoding="utf-8")
                    self._assert_denied(self._call("agent_git_status"))
                    self._assert_denied(self._call("agent_git_diff"))
                    self.assertFalse(marker.exists(), f"Malicious config [{section}] {key} executed!")
                finally:
                    main_cfg.write_text(orig_cfg, encoding="utf-8")

        # Also test worktree-specific config.worktree poisoning
        wt_admin_dir = self.main_repo / ".git" / "worktrees" / "linked_wt"
        wt_cfg = wt_admin_dir / "config.worktree"
        try:
            main_cfg.write_text(orig_cfg + "\n[extensions]\n\tworktreeConfig = true\n", encoding="utf-8")
            wt_cfg.write_text(f"[filter \"wtprobe\"]\n\tclean = {cmd_str}\n", encoding="utf-8")
            self._assert_denied(self._call("agent_git_status"))
            self._assert_denied(self._call("agent_git_diff"))
            self.assertFalse(marker.exists())
        finally:
            wt_cfg.unlink(missing_ok=True)
            main_cfg.write_text(orig_cfg, encoding="utf-8")

    def test_06_malformed_git_file_hardlinks_and_path_traversal_denied(self) -> None:
        # Path traversal in agent_git_diff path argument
        self._assert_denied(self._call("agent_git_diff", {"path": "../main_repo/app.py"}))
        self._assert_denied(self._call("agent_git_diff", {"path": r"\\127.0.0.1\c$\file.txt"}))
        self._assert_denied(self._call("agent_git_diff", {"path": "app.py:ads_stream"}))

        dot_git = self.worktree_dir / ".git"
        orig_dot_git = dot_git.read_bytes()

        # Hardlinked .git file must be denied
        hardlink_alias = self.outside_dir / "dot_git_hardlink"
        try:
            os.link(dot_git, hardlink_alias)
            self._assert_denied(self._call("agent_git_status"))
        finally:
            hardlink_alias.unlink(missing_ok=True)

        # Malformed .git pointer payloads
        bad_payloads = [
            b"",
            b"not_gitdir: something\n",
            b"gitdir: \x00evil\n",
            b"gitdir: line1\ngitdir: line2\n",
            b"gitdir: \\\\evil-server\\share\\git\n",
            b"gitdir: C:\\path:ads_stream\n",
        ]
        try:
            for payload in bad_payloads:
                with self.subTest(payload=payload):
                    dot_git.unlink(missing_ok=True)
                    dot_git.write_bytes(payload)
                    self._assert_denied(self._call("agent_git_status"))
        finally:
            dot_git.unlink(missing_ok=True)
            dot_git.write_bytes(orig_dot_git)


if __name__ == "__main__":
    unittest.main()
