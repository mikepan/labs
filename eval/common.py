"""
eval.common - Shared helpers, logging formatters, and utility functions.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

class ColorFormatter(logging.Formatter):
    """Zero-dependency ANSI terminal color formatter for structured logging with harness tagging."""
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    
    LEVEL_COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Green
        logging.WARNING: "\033[33m",   # Yellow
        logging.ERROR: "\033[31m",     # Red
        logging.CRITICAL: "\033[35m",  # Magenta
    }

    HARNESS_COLORS = {
        "OPENCODE": "\033[1;36m",      # Bold Cyan
        "PI": "\033[1;35m",            # Bold Magenta
        "TOOL_EVAL": "\033[1;33m",      # Bold Yellow
        "TOOL-EVAL": "\033[1;33m",      # Bold Yellow
        "HARNESS": "\033[1;34m",        # Bold Blue
    }

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, "")
        time_str = f"{self.DIM}{self.formatTime(record, '%H:%M:%S')}{self.RESET}"
        level_str = f"{color}{self.BOLD}[{record.levelname}]{self.RESET}"

        # Extract harness name from record attribute or logger name (e.g. 'harness.opencode')
        harness_val = getattr(record, "harness", None)
        if not harness_val and record.name.startswith("harness."):
            harness_val = record.name.split(".", 1)[1]

        harness_tag = ""
        if harness_val:
            h_str = str(harness_val).upper().replace("_", "-")
            h_color = self.HARNESS_COLORS.get(h_str, "\033[1;34m")
            harness_tag = f" {h_color}[{h_str}]{self.RESET}"
        
        msg = record.getMessage()
        # Highlight checkmarks and crossmarks
        if "✓" in msg:
            msg = msg.replace("✓", "\033[32m✓\033[0m")
        if "✗" in msg:
            msg = msg.replace("✗", "\033[31m✗\033[0m")
            
        return f"{time_str} {level_str}{harness_tag} {msg}"


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Create a configured logger with ANSI color output."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(ColorFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_harness_logger(harness_name: str, level: int = logging.INFO) -> logging.Logger:
    """Create or retrieve a logger pre-configured with a colored harness tag."""
    name = f"harness.{harness_name.lower().strip()}"
    return setup_logger(name, level=level)


def load_json_config(path: str | os.PathLike, label: str = "config") -> dict:
    """Load a JSON config file, exiting with an error if not found."""
    p = Path(path)
    if not p.is_file():
        print(f"[ERROR] {label} file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(p.read_text(encoding="utf-8"))


def run_cmd(*cmd: str, input: str | None = None, check: bool = False, timeout: int | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Execute a command via subprocess, capturing output as text."""
    return subprocess.run(cmd, input=input, capture_output=True, text=True, check=check, timeout=timeout, cwd=cwd)


def http_json(url: str, method: str = "GET", data: dict | None = None, timeout: int = 5) -> dict | None:
    """Perform an HTTP request and parse JSON response, returning None on failure."""
    try:
        payload = json.dumps(data).encode("utf-8") if data else None
        headers = {"Content-Type": "application/json"} if payload else {}
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None


def get_available_tests() -> list[str]:
    """Discover all valid test suites containing run.py under TESTS_DIR."""
    from eval.config import TESTS_DIR
    if not TESTS_DIR.exists():
        return []
    return [
        d.name for d in sorted(TESTS_DIR.iterdir())
        if (d / "run.py").is_file()
    ]


def load_harnesses_config() -> dict:
    """Load harnesses configuration dictionary from HARNESSES_CONFIG_FILE."""
    from eval.config import HARNESSES_CONFIG_FILE
    return load_json_config(HARNESSES_CONFIG_FILE, label="harnesses config")




def prevent_system_sleep() -> subprocess.Popen | None:
    """Prevent macOS from going to sleep or dimming display during evaluation."""
    import shutil
    if sys.platform == "darwin":
        caff = shutil.which("caffeinate")
        if caff:
            return subprocess.Popen(
                [caff, "-dimsu", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    return None


class NetworkConnectivityError(RuntimeError):
    """Raised when in-sandbox network connectivity to the LLM backend fails."""
    pass


NETWORK_ERROR_PATTERNS = (
    "no route to host",
    "connection refused",
    "network is unreachable",
    "dial tcp",
    "ehostunreach",
    "econnrefused",
    "enetunreach",
    "name or service not known",
    "nodename nor servname provided",
    "temporary failure in name resolution",
    "couldn't connect to server",
    "failed to connect to",
    "network_error:",
)


def is_network_error(err: object) -> bool:
    """Check if an exception or error string indicates a network/backend connectivity failure."""
    if isinstance(err, NetworkConnectivityError):
        return True
    msg = str(err).lower()
    return any(pat in msg for pat in NETWORK_ERROR_PATTERNS)


