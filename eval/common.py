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
import urllib.error
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


def send_chat_completion(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    reasoning_effort: str | None = None,
    max_tokens: int = 1024,
    temperature: float | None = None,
    top_p: float | None = None,
    timeout: int = 30,
) -> tuple[str, str, dict[str, int]]:
    """Send a streaming chat completion request to an OpenAI-compatible endpoint.

    Returns:
        (content, reasoning_content, token_usage_dict)
    """
    target_url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if reasoning_effort and reasoning_effort.lower() not in ("off", "none", "on", ""):
        payload["reasoning_effort"] = reasoning_effort.lower()
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p

    req = urllib.request.Request(
        target_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        reasoning_parts.append(reasoning)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {target_url}: {detail[:1000]}") from e
    except Exception as e:
        raise RuntimeError(f"Request to {target_url} failed: {e}") from e

    content = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    if not content and reasoning:
        content = reasoning

    comp_details = usage.get("completion_tokens_details") or {}
    reasoning_toks = comp_details.get("reasoning_tokens") if isinstance(comp_details, dict) else 0
    token_usage = {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "reasoning_tokens": reasoning_toks or 0,
    }
    return content, reasoning, token_usage


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


def load_models() -> dict[str, dict]:
    """Load models.json, expanding each model's reasoning-effort list into aliases.

    A model with one effort keeps its base name. With several, each effort gets a
    ``-<effort>`` suffix except ``xhigh``, which keeps the unsuffixed default name.
    """
    from eval.config import MODELS_CONFIG_FILE
    models: dict[str, dict] = {}
    for name, cfg in load_json_config(MODELS_CONFIG_FILE, label="models config").items():
        efforts = cfg["reasoning_effort"]
        for effort in efforts:
            suffix = "" if len(efforts) == 1 or effort == "xhigh" else f"-{effort}"
            models[f"{name}{suffix}"] = {**cfg, "reasoning_effort": effort}
    return models


def resolve_target_models(models: dict[str, Any], model_arg: str | None = "all") -> list[str]:
    """Resolve target model names by case-insensitive substring match, comma-list, or 'all'."""
    if not model_arg or model_arg == "all":
        return [m for m, cfg in models.items() if not cfg.get("disabled", False) and cfg.get("enabled", True) is not False]

    result: list[str] = []
    for item in (x.strip() for x in model_arg.split(",") if x.strip()):
        for m in models:
            if item.lower() in m.lower() and m not in result:
                result.append(m)
    return result


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


