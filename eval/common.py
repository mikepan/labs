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


def get_available_tests() -> list[str]:
    """Discover all valid test suites containing run.py under TESTS_DIR."""
    from eval.config import TESTS_DIR
    if not TESTS_DIR.exists():
        return []
    return [
        item for item in sorted(os.listdir(TESTS_DIR))
        if (TESTS_DIR / item / "run.py").is_file()
    ]


def load_harnesses_config() -> dict:
    """Load harnesses configuration dictionary from HARNESSES_CONFIG_FILE."""
    from eval.config import HARNESSES_CONFIG_FILE
    if os.path.exists(HARNESSES_CONFIG_FILE):
        try:
            with open(HARNESSES_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"pi": {}, "opencode cli": {}}


def get_active_api_model(base_url: str) -> str | None:
    """Query /v1/models on LLM server and return the first active model ID."""
    url = f"{base_url}/models" if not base_url.endswith("/models") else base_url
    data = http_json(url, timeout=3)
    if data and "data" in data and len(data["data"]) > 0:
        return data["data"][0].get("id")
    return None

