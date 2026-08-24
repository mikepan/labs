"""
eval.common - Shared helpers, logging formatters, and utility functions.
"""

import json
import logging
import os
import subprocess
import sys
import urllib.request

class ColorFormatter(logging.Formatter):
    """Zero-dependency ANSI terminal color formatter for structured logging."""
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

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, "")
        time_str = f"{self.DIM}{self.formatTime(record, '%H:%M:%S')}{self.RESET}"
        level_str = f"{color}{self.BOLD}[{record.levelname}]{self.RESET}"
        
        msg = record.getMessage()
        # Highlight checkmarks and crossmarks
        if "✓" in msg:
            msg = msg.replace("✓", "\033[32m✓\033[0m")
        if "✗" in msg:
            msg = msg.replace("✗", "\033[31m✗\033[0m")
            
        return f"{time_str} {level_str} {msg}"


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


def load_json_config(path: str | os.PathLike, label: str = "config") -> dict:
    """Load a JSON config file, exiting with an error if not found."""
    if not os.path.exists(path):
        print(f"[ERROR] {label} file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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
