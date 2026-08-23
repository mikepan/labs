"""
eval.sandbox - Encapsulates all Docker Sandbox (sbx) interactions.

Provides SandboxClient with exec(), exec_python_json(), and lifecycle methods.
Replaces the duplicated exec_in_sandbox functions from run_harness.py and create_harness.py.
"""

import json
import os
import re
import subprocess
import sys
from typing import Any

from eval.common import setup_logger
from eval.config import DEFAULT_WORKER_SANDBOX_NAME, DEFAULT_TEMPLATE_TAG, DEFAULT_OPENCODE_PORT, REPO_ROOT

__all__ = ["SandboxClient"]

logger = setup_logger("sandbox")


class SandboxClient:
    """Encapsulates all interactions with a Docker Sandbox container."""

    def __init__(self, name: str = DEFAULT_WORKER_SANDBOX_NAME):
        self.name = name

    # ----- Core execution primitives -----

    def exec(self, cmd: str) -> tuple[int, str, str]:
        """Execute a bash command inside the sandbox."""
        res = subprocess.run(
            ["sbx", "exec", self.name, "bash", "-c", cmd],
            capture_output=True, text=True,
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    def exec_python(self, script: str, *, stdin: str | None = None) -> subprocess.CompletedProcess:
        """Run a Python script inside the sandbox, optionally piping stdin."""
        cmd = ["sbx", "exec", self.name, "python3", "-c", script]
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True)

    def exec_python_json(
        self,
        script: str,
        *,
        stdin: str | None = None,
        label: str = "sandbox",
    ) -> Any:
        """Run Python inside sandbox and extract JSON from __JSON_START__...__JSON_END__ markers.

        This replaces the repeated pattern used by send_message, get_session_messages,
        get_session_info, and evaluate_step functions.
        """
        res = self.exec_python(script, stdin=stdin)
        if res.returncode != 0:
            raise RuntimeError(f"{label} error (code {res.returncode}): {res.stderr}\n{res.stdout}")
        match = re.search(r"__JSON_START__(.*?)__JSON_END__", res.stdout, re.DOTALL)
        if not match:
            raise RuntimeError(f"Could not parse JSON from {label}: {res.stdout}\nStderr: {res.stderr}")
        return json.loads(match.group(1))

    # ----- Lifecycle management -----

    def ensure(self, template: str = DEFAULT_TEMPLATE_TAG, workspace: str | None = None) -> None:
        """Provision a clean ephemeral sandbox from the base template."""
        subprocess.run(["sbx", "rm", "-f", self.name], capture_output=True, text=True)
        logger.info("Provisioning sandbox '%s' from template '%s'...", self.name, template)
        create_cmd = [
            "sbx", "create",
            "--name", self.name,
            "--template", template,
            "-p", f"{DEFAULT_OPENCODE_PORT}:{DEFAULT_OPENCODE_PORT}",
            "shell", str(workspace or REPO_ROOT),
        ]
        res = subprocess.run(create_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Error creating sandbox:\n%s\n%s", res.stderr, res.stdout)
            sys.exit(1)
        logger.info("✓ Sandbox '%s' is ready.", self.name)

    def remove(self) -> None:
        """Clean up and remove the sandbox container."""
        logger.info("Cleaning up sandbox '%s'...", self.name)
        subprocess.run(["sbx", "rm", "-f", self.name], capture_output=True, text=True)

    def exists(self) -> bool:
        """Check if sandbox exists."""
        res = subprocess.run(["sbx", "ls"], capture_output=True, text=True)
        return res.returncode == 0 and self.name in res.stdout

    def stop(self) -> None:
        """Stop sandbox if running."""
        subprocess.run(["sbx", "stop", self.name], capture_output=True, text=True)

    # ----- File transfer -----

    def extract_artifacts(self, workspace_dir: str, dest_dir: str) -> None:
        """Extract workspace files (excluding .git) from sandbox to a local directory."""
        os.makedirs(dest_dir, exist_ok=True)
        tar_cmd = f"cd {workspace_dir} && tar --exclude='.git' -cf - ."
        res = subprocess.run(
            ["sbx", "exec", self.name, "bash", "-c", tar_cmd],
            capture_output=True,
        )
        if res.returncode == 0 and res.stdout:
            subprocess.run(
                ["tar", "-xf", "-", "-C", dest_dir],
                input=res.stdout, capture_output=True,
            )

    def extract_file(self, remote_path: str, local_path: str) -> bool:
        """Copy a single file from sandbox to local filesystem."""
        res = subprocess.run(
            ["sbx", "exec", self.name, "cat", remote_path],
            capture_output=True, text=True,
        )
        if res.returncode == 0 and res.stdout:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(res.stdout)
            return True
        return False

    def read_file(self, remote_path: str, max_lines: int = 500) -> str:
        """Read content from a file inside the sandbox."""
        cmd = ["sbx", "exec", self.name, "tail", "-n", str(max_lines), remote_path]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.stdout if res.returncode == 0 else ""

    # ----- Workspace setup -----

    def setup_test_workspace(self, workspace_dir: str, setup_cmds: list[str]) -> None:
        """Create and initialize a test workspace with git baseline."""
        self.exec(f"rm -rf {workspace_dir} && mkdir -p {workspace_dir}")
        for cmd in setup_cmds:
            self.exec(f"cd {workspace_dir} && {cmd}")

        git_setup = (
            f"cd {workspace_dir} && "
            "git init && "
            "git config user.email 'eval@example.com' && "
            "git config user.name 'Eval Runner' && "
            "git add -A && "
            "git commit --allow-empty -m 'initial commit'"
        )
        self.exec(git_setup)
        logger.debug("Initialized workspace: %s", workspace_dir)
