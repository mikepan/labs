import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from eval.common import setup_logger, run_cmd
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
        res = run_cmd("sbx", "exec", self.name, "bash", "-c", cmd)
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    def exec_python(self, script: str, *, stdin: str | None = None) -> Any:
        """Run a Python script inside the sandbox, optionally piping stdin."""
        return run_cmd("sbx", "exec", self.name, "python3", "-c", script, input=stdin)

    def exec_python_json(
        self,
        script: str,
        *,
        stdin: str | None = None,
        label: str = "sandbox",
    ) -> Any:
        """Run Python inside sandbox and extract JSON from __JSON_START__...__JSON_END__ markers."""
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
        run_cmd("sbx", "rm", "-f", self.name)
        logger.info("Provisioning sandbox '%s' from template '%s'...", self.name, template)
        res = run_cmd(
            "sbx", "create",
            "--name", self.name,
            "--template", template,
            "-p", f"{DEFAULT_OPENCODE_PORT}:{DEFAULT_OPENCODE_PORT}",
            "shell", str(workspace or REPO_ROOT),
        )
        if res.returncode != 0:
            logger.error("Error creating sandbox:\n%s\n%s", res.stderr, res.stdout)
            sys.exit(1)
        logger.info("✓ Sandbox '%s' is ready.", self.name)

    def remove(self) -> None:
        """Clean up and remove the sandbox container."""
        logger.info("Cleaning up sandbox '%s'...", self.name)
        run_cmd("sbx", "rm", "-f", self.name)

    def exists(self) -> bool:
        """Check if sandbox exists."""
        res = run_cmd("sbx", "ls")
        return res.returncode == 0 and self.name in res.stdout

    def stop(self) -> None:
        """Stop sandbox if running."""
        run_cmd("sbx", "stop", self.name)

    # ----- File transfer -----

    def extract_artifacts(self, workspace_dir: str, dest_dir: str | Path) -> None:
        """Extract workspace files (excluding .git) from sandbox to a local directory."""
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        tar_cmd = f"cd {workspace_dir} && tar --exclude='.git' -cf - ."
        res = run_cmd("sbx", "exec", self.name, "bash", "-c", tar_cmd)
        if res.returncode == 0 and res.stdout:
            run_cmd("tar", "-xf", "-", "-C", str(dest_dir), input=res.stdout)

    def extract_file(self, remote_path: str, local_path: str | Path) -> bool:
        """Copy a single file from sandbox to local filesystem."""
        res = run_cmd("sbx", "exec", self.name, "cat", remote_path)
        if res.returncode == 0 and res.stdout:
            target = Path(local_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(res.stdout, encoding="utf-8")
            return True
        return False

    def read_file(self, remote_path: str, max_lines: int = 500) -> str:
        """Read content from a file inside the sandbox."""
        res = run_cmd("sbx", "exec", self.name, "tail", "-n", str(max_lines), remote_path)
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
