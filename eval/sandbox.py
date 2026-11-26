from __future__ import annotations

import atexit
import fnmatch
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
from typing import Any

from eval.common import setup_logger, run_cmd, NetworkConnectivityError, is_network_error
from eval.config import (
    API_BASE_URL,
    DEFAULT_API_TIMEOUT_SECONDS,
    DEFAULT_OPENCODE_PORT,
    DEFAULT_PI_PORT,
    DEFAULT_TEMPLATE_TAG,
    DEFAULT_WORKER_SANDBOX_NAME,
)

__all__ = [
    "SandboxClient",
    "cleanup_all_sandboxes",
    "ensure_sandbox_policy_isolated",
]

logger = setup_logger("sandbox")

_ACTIVE_SANDBOXES: set[str] = set()
_ACTIVE_EPHEMERAL_DIRS: set[str] = set()
_REGISTRY_LOCK = threading.Lock()

# Build/cache artifacts are never part of a solution. Excluding them via
# .git/info/exclude keeps agent-generated caches out of the automatic step
# commits (and out of git status) without planting a .gitignore in the project.
GIT_EXCLUDE_PATTERNS = (
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    "htmlcov/",
    "*.log",
    ".DS_Store",
    "node_modules/",
    ".venv/",
    "*.egg-info/",
)


def _register_active_sandbox(name: str) -> None:
    with _REGISTRY_LOCK:
        _ACTIVE_SANDBOXES.add(name)


def _unregister_active_sandbox(name: str) -> None:
    with _REGISTRY_LOCK:
        _ACTIVE_SANDBOXES.discard(name)


def _register_active_dir(path: str) -> None:
    with _REGISTRY_LOCK:
        _ACTIVE_EPHEMERAL_DIRS.add(path)


def _unregister_active_dir(path: str) -> None:
    with _REGISTRY_LOCK:
        _ACTIVE_EPHEMERAL_DIRS.discard(path)


def cleanup_all_sandboxes() -> None:
    """Tear down all currently active sandboxes and ephemeral workspaces."""
    with _REGISTRY_LOCK:
        sandboxes = list(_ACTIVE_SANDBOXES)
        dirs = list(_ACTIVE_EPHEMERAL_DIRS)
        _ACTIVE_SANDBOXES.clear()
        _ACTIVE_EPHEMERAL_DIRS.clear()

    if not sandboxes and not dirs:
        return

    for sbx_name in sandboxes:
        try:
            logger.info("Cleaning up active sandbox '%s'...", sbx_name)
            run_cmd("sbx", "rm", "-f", sbx_name, timeout=10)
        except Exception:
            pass

    for d in dirs:
        try:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


def _signal_handler(signum: int, _frame: Any) -> None:
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    logger.warning("Received signal %s (%d): cleaning up all active sandboxes...", sig_name, signum)
    cleanup_all_sandboxes()
    sys.exit(128 + signum)


def _init_lifecycle_hooks() -> None:
    atexit.register(cleanup_all_sandboxes)
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, RuntimeError, AttributeError):
        pass


_init_lifecycle_hooks()


def ensure_sandbox_policy_isolated(allowed_hosts: list[str] | None = None) -> bool:
    """Ensure host Docker Sandboxes policy denies general internet and allows only specified hosts.

    Removes 'default-allow-all' from network rules if present, and ensures
    allowed_hosts (defaulting to the LLM backend host/IP) are explicitly allowed.
    Raises if the policy cannot be made default-deny; an eval must not run with
    an unrestricted network.
    """
    try:
        res = run_cmd("sbx", "policy", "inspect", "local-policy")
        if res.returncode != 0:
            raise RuntimeError(
                f"Could not inspect sbx policy: {res.stderr.strip() or res.stdout.strip()}"
            )

        # If default-allow-all rule exists, remove it to enable default-deny
        if "default-allow-all" in res.stdout:
            rm_res = run_cmd("sbx", "policy", "rm", "network", "--id", "default-allow-all")
            if rm_res.returncode != 0:
                raise RuntimeError(
                    f"Failed to remove 'default-allow-all' policy: "
                    f"{rm_res.stderr.strip() or rm_res.stdout.strip()}"
                )
            logger.info("✓ Removed 'default-allow-all' network policy rule.")

        # Determine target hosts to allow
        targets: set[str] = set()
        if allowed_hosts:
            for h in allowed_hosts:
                targets.add(h)
                targets.add(f"{h}:*")
        else:
            from urllib.parse import urlparse
            import socket
            parsed = urlparse(API_BASE_URL)
            host = parsed.hostname or "spark"
            targets.add(host)
            targets.add(f"{host}:*")
            try:
                ip = socket.gethostbyname(host)
                targets.add(ip)
                targets.add(f"{ip}:*")
            except Exception:
                pass

        # Check existing rules and add missing allow rules
        existing = res.stdout
        for target in sorted(targets):
            # Check if target is already allowed in policy
            if f"allow      {target}" in existing or f"allow {target}" in existing:
                continue
            add_res = run_cmd("sbx", "policy", "allow", "network", target)
            if add_res.returncode != 0:
                raise RuntimeError(
                    f"Failed to add sbx policy allow for {target}: "
                    f"{add_res.stderr.strip() or add_res.stdout.strip()}"
                )
            logger.info("✓ Added sbx policy network allow: %s", target)

        return True
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Exception while ensuring sandbox network policy: {e}") from e


class SandboxClient:
    """Encapsulates all interactions with a Docker Sandbox container."""

    def __init__(self, name: str = DEFAULT_WORKER_SANDBOX_NAME):
        self.name = re.sub(r"[^a-zA-Z0-9.-]+", "-", name).strip("-")
        self._ephemeral_dir: str | None = None

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
            err_text = res.stderr.strip() or res.stdout.strip()
            if "NETWORK_ERROR:" in err_text:
                clean_net = err_text.split("NETWORK_ERROR:", 1)[1].strip()
                raise NetworkConnectivityError(clean_net)
            if is_network_error(err_text):
                raise NetworkConnectivityError(f"{label} network failure: {err_text}")
            if "TIMEOUT:" in err_text:
                clean_timeout = err_text.split("TIMEOUT:", 1)[1].strip()
                raise TimeoutError(clean_timeout)
            if "HTTP_ERROR:504" in err_text or "timed out" in err_text.lower():
                raise TimeoutError(f"{label} timed out")
            if "Traceback (most recent call last):" in err_text:
                last_line = err_text.strip().splitlines()[-1]
                raise RuntimeError(f"{label} error: {last_line}")
            raise RuntimeError(f"{label} error (code {res.returncode}): {err_text}")
        match = re.search(r"__JSON_START__(.*?)__JSON_END__", res.stdout, re.DOTALL)
        if not match:
            raise RuntimeError(f"Could not parse JSON from {label}: {res.stdout}\nStderr: {res.stderr}")
        return json.loads(match.group(1))

    def api_request(
        self,
        port: int,
        path: str,
        method: str = "GET",
        data: dict | list | None = None,
        timeout: int = DEFAULT_API_TIMEOUT_SECONDS,
    ) -> Any:
        """Perform an HTTP request against a service inside the sandbox and return parsed JSON."""
        endpoint = f"http://127.0.0.1:{port}{path if path.startswith('/') else '/' + path}"
        payload_str = json.dumps(data) if data is not None else None
        script = f"""import urllib.request, json, sys
data = sys.stdin.read().encode('utf-8') if {payload_str is not None} else None
headers = {{'Content-Type': 'application/json'}} if data else {{}}
req = urllib.request.Request({endpoint!r}, data=data, headers=headers, method={method!r})
try:
    with urllib.request.urlopen(req, timeout={timeout}) as resp:
        body = resp.read().decode('utf-8')
        print("__JSON_START__" + (body if body.strip() else '{{}}') + "__JSON_END__")
except Exception as e:
    print(f"ERROR:{{e}}", file=sys.stderr)
    sys.exit(1)
"""
        return self.exec_python_json(script, stdin=payload_str, label=f"{method} {path}")

    def check_backend_connectivity(self, target_url: str, timeout: int = 5) -> tuple[bool, str]:
        """Test if target_url (LLM backend) is reachable from inside this sandbox container.

        Returns:
            (True, "") if reachable.
            (False, error_details) if connection failed or proxy reported network failure.
        """
        base = target_url.rstrip("/")
        candidates = [f"{base}/v1/models", f"{base}/health", base]

        script = f"""import urllib.request, urllib.error, sys, json
candidates = {candidates!r}
last_err = None
for url in candidates:
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout={timeout}) as resp:
            print("__JSON_START__" + json.dumps({{"ok": True, "status": resp.status, "url": url}}) + "__JSON_END__")
            sys.exit(0)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        if any(p in body.lower() for p in ['dial tcp', 'no route to host', 'connection refused']):
            last_err = f"HTTP {{e.code}}: {{body.strip()}}"
            continue
        print("__JSON_START__" + json.dumps({{"ok": True, "status": e.code, "url": url}}) + "__JSON_END__")
        sys.exit(0)
    except Exception as e:
        last_err = str(e)

print("__JSON_START__" + json.dumps({{"ok": False, "error": last_err or "Backend unreachable"}}) + "__JSON_END__")
"""
        try:
            data = self.exec_python_json(script, label="backend connectivity check")
            if data.get("ok"):
                return True, ""
            return False, data.get("error", "Unknown network failure")
        except Exception as e:
            return False, str(e)

    def isolate_network(self) -> bool:
        """Configure container firewall (iptables) to prevent raw internet access.

        Permits:
        - Loopback (lo, 127.0.0.0/8)
        - Established & related connections
        - Container/Docker bridge subnets (172.16.0.0/12)
        - Local private networks (192.168.0.0/16, 10.0.0.0/8)

        Rejects all other outbound traffic to block direct/raw public internet access.
        """
        cidrs = ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]

        allow_rules = "\n".join(f"iptables -A OUTPUT -d {cidr} -j ACCEPT" for cidr in sorted(set(cidrs)))
        script = f"""
iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
{allow_rules}
iptables -A OUTPUT -j REJECT --reject-with icmp-port-unreachable
iptables -P OUTPUT DROP
"""
        cmd = ["sbx", "exec", self.name, "sudo", "sh", "-c", script]
        res = run_cmd(*cmd)
        if res.returncode != 0:
            logger.warning("Could not apply iptables isolation to '%s': %s", self.name, res.stderr.strip() or res.stdout.strip())
            return False
        logger.info("✓ Sandbox '%s' network isolated (direct internet access disabled).", self.name)
        return True

    def verify_network_isolation(self) -> tuple[bool, str]:
        """Verify that the sandbox has no general internet egress.

        The sbx proxy answers every CONNECT with 200, then terminates TLS and
        returns 403 with an "Approval required" body for non-allowlisted hosts.
        Therefore CONNECT status is not a usable signal: a real origin response
        and a proxy denial can both look like 200 then 403.

        Checks:
        1. Direct raw TCP to a public IP is blocked.
        2. Direct HTTPS bypassing the proxy is blocked.
        3. Non-allowlisted hosts are denied by the proxy (its approval body).
        """
        script = r'''import json, shutil, socket, subprocess

def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return -1, "", str(e)

checks = {}
leaks = []

if not shutil.which("curl"):
    leaks.append("curl unavailable; cannot verify proxy egress")

# 1. Direct raw TCP to a public IP must fail.
try:
    s = socket.create_connection(("1.1.1.1", 80), timeout=2)
    s.close()
    leaks.append("Direct raw TCP socket to 1.1.1.1:80 succeeded")
except Exception:
    checks["direct_tcp"] = "blocked"

# 2. Direct HTTPS, bypassing the proxy, must fail.
rc, out, err = run([
    "curl", "--noproxy", "*", "-k", "-sS", "-o", "/dev/null",
    "-w", "%{http_code}", "--max-time", "3", "https://1.1.1.1",
])
out = out.strip()
checks["direct_https"] = out or (f"curl_error: {err.strip()[:120]}" if err.strip() else "no status")
if out not in ("", "000"):
    leaks.append(f"Direct HTTPS egress succeeded (status {out})")

# 3. A non-allowlisted host must get the proxy's approval-required body.
#    Any other response means the request reached an origin (or a permissive
#    proxy) and must be treated as a leak, even if the final status is 403.
for host in ("example.com", "ambientcg.com", "api.ambientcg.com"):
    rc, out, err = run([
        "curl", "-sS", "-i", "--max-time", "4", f"https://{host}",
    ])
    body = out.strip()
    denied = (
        f"Approval required for {host}" in body
        or "sbx policy approval ls" in body
    )
    if denied:
        checks[f"proxy:{host}"] = "approval required"
    elif rc == 0 and body:
        leaks.append(
            f"Proxy did not deny {host} (response: {body[:160]!r})"
        )
    else:
        checks[f"proxy:{host}"] = "unreachable"
        if err.strip():
            checks[f"proxy:{host}:error"] = err.strip()[:120]

ok = not leaks
print("__JSON_START__" + json.dumps({"ok": ok, "details": {"checks": checks, "leaks": leaks}}) + "__JSON_END__")
'''
        try:
            data = self.exec_python_json(script, label="network isolation check")
        except Exception as e:
            return False, f"Network isolation check failed to run: {e}"
        if data.get("ok"):
            return True, ""
        leaks = data.get("details", {}).get("leaks", [])
        err_msg = "; ".join(leaks) if leaks else "Internet access not blocked"
        return False, err_msg

    # ----- Lifecycle management -----

    def ensure(
        self,
        template: str = DEFAULT_TEMPLATE_TAG,
        workspace: str | None = None,
        publish_ports: bool = False,
    ) -> None:
        """Provision a clean ephemeral sandbox from the base template with an isolated workspace."""
        self.remove()

        if workspace:
            ws_path = workspace
        else:
            self._ephemeral_dir = tempfile.mkdtemp(prefix="sbx_empty_ws_")
            ws_path = self._ephemeral_dir
            _register_active_dir(self._ephemeral_dir)

        _register_active_sandbox(self.name)
        logger.info("Provisioning sandbox '%s' from template '%s' (workspace: %s)...", self.name, template, ws_path)
        cmd_args = ["sbx", "create", "--name", self.name, "--template", template]
        if publish_ports:
            cmd_args.extend([
                "-p", f"{DEFAULT_OPENCODE_PORT}:{DEFAULT_OPENCODE_PORT}",
                "-p", f"{DEFAULT_PI_PORT}:{DEFAULT_PI_PORT}",
            ])
        cmd_args.extend(["shell", ws_path])

        res = run_cmd(*cmd_args)
        if res.returncode != 0:
            self.remove()
            raise RuntimeError(f"Error creating sandbox '{self.name}':\n{res.stderr}\n{res.stdout}")

        # Enforce sandbox network isolation (block public internet)
        self.isolate_network()

        logger.info("✓ Sandbox '%s' is ready.", self.name)

    def remove(self) -> None:
        """Clean up and remove the sandbox container and ephemeral workspace."""
        _unregister_active_sandbox(self.name)
        logger.info("Cleaning up sandbox '%s'...", self.name)
        run_cmd("sbx", "rm", "-f", self.name)
        if self._ephemeral_dir:
            _unregister_active_dir(self._ephemeral_dir)
            if os.path.exists(self._ephemeral_dir):
                shutil.rmtree(self._ephemeral_dir, ignore_errors=True)
            self._ephemeral_dir = None

    def exists(self) -> bool:
        """Check if sandbox exists."""
        res = run_cmd("sbx", "ls")
        return res.returncode == 0 and self.name in res.stdout

    def stop(self) -> None:
        """Stop sandbox if running."""
        run_cmd("sbx", "stop", self.name)

    # ----- File transfer -----

    def write_file(self, remote_path: str, content: str | bytes) -> bool:
        """Write string or byte content directly to a remote path inside the sandbox."""
        if isinstance(content, bytes):
            import base64
            b64 = base64.b64encode(content).decode("ascii")
            cmd = f"mkdir -p \"$(dirname '{remote_path}')\" && base64 -d > '{remote_path}'"
            return run_cmd("sbx", "exec", self.name, "bash", "-c", cmd, input=b64).returncode == 0
        cmd = f"mkdir -p \"$(dirname '{remote_path}')\" && cat > '{remote_path}'"
        return run_cmd("sbx", "exec", self.name, "bash", "-c", cmd, input=content).returncode == 0

    def upload_tree(self, local_path: str | Path, remote_path: str, exclude: set[str] | list[str] | None = None) -> bool:
        """Upload a local directory tree into a remote sandbox path in a single atomic tar stream."""
        src = Path(local_path)
        if not src.is_dir():
            return False

        exclude_set = set(exclude) if exclude else set()

        def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
            parts = Path(tarinfo.name).parts
            for p in parts:
                if p.startswith(".") and p not in (".", ".."):
                    return None
                if p == "__pycache__" or "private" in p.lower():
                    return None
                for pat in exclude_set:
                    if fnmatch.fnmatch(p, pat):
                        return None
            return tarinfo

        buf = io.BytesIO()
        try:
            with tarfile.open(fileobj=buf, mode="w") as tar:
                tar.add(str(src), arcname="", filter=_filter)
            tar_bytes = buf.getvalue()
            import base64
            b64 = base64.b64encode(tar_bytes).decode("ascii")
            tar_cmd = f"mkdir -p '{remote_path}' && base64 -d | tar -xf - -C '{remote_path}'"
            res = run_cmd("sbx", "exec", self.name, "bash", "-c", tar_cmd, input=b64)
            return res.returncode == 0
        except Exception as e:
            logger.error("Failed to upload tree %s -> %s: %s", local_path, remote_path, e)
            return False

    def extract_artifacts(self, workspace_dir: str, dest_dir: str | Path) -> None:
        """Extract workspace files (excluding git and build artifacts) to a local directory."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        excludes = " ".join(
            f"--exclude={shlex.quote(p)}"
            for p in (".git", "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store", "node_modules")
        )
        tar_cmd = f"cd {workspace_dir} && tar {excludes} -cf - ."
        try:
            p1 = subprocess.Popen(
                ["sbx", "exec", self.name, "bash", "-c", tar_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            p2 = subprocess.Popen(
                ["tar", "-xf", "-", "-C", str(dest)],
                stdin=p1.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if p1.stdout:
                p1.stdout.close()
            _, p2_err = p2.communicate()
            _, p1_err = p1.communicate()
            if p2.returncode != 0:
                logger.warning("Artifact extraction warning (tar -xf): %s", p2_err.decode(errors="replace").strip())
            if p1.returncode != 0:
                logger.warning("Artifact extraction warning (sbx exec): %s", p1_err.decode(errors="replace").strip())
        except Exception as e:
            logger.error("Failed to extract artifacts from %s to %s: %s", workspace_dir, dest_dir, e)

    def read_file(self, remote_path: str, max_lines: int | None = None) -> str:
        """Read content from a file inside the sandbox."""
        cmd = ["sbx", "exec", self.name, "tail", "-n", str(max_lines), remote_path] if max_lines is not None else ["sbx", "exec", self.name, "cat", remote_path]
        res = run_cmd(*cmd)
        return res.stdout if res.returncode == 0 else ""

    # ----- Workspace setup -----

    def setup_test_workspace(self, workspace_dir: str, setup_cmds: list[str]) -> None:
        """Create and initialize a test workspace with git baseline in a single execution."""
        cmds = [
            f"rm -rf {workspace_dir}",
            f"mkdir -p {workspace_dir}",
            f"cd {workspace_dir}",
            *setup_cmds,
            "git init",
            "git config user.email 'alex.chen@innovatech.io'",
            "git config user.name 'Alex Chen'",
            "printf '%s\\n' " + " ".join(shlex.quote(p) for p in GIT_EXCLUDE_PATTERNS) + " > .git/info/exclude",
            "git add -A",
            "git commit --allow-empty -m 'chore: initialize project structure'",
        ]
        combined_script = " && ".join(cmds)
        self.exec(combined_script)
        logger.debug("Initialized workspace: %s", workspace_dir)

