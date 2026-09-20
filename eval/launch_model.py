#!/usr/bin/env python3
"""
launch_model.py - Dedicated vLLM model lifecycle manager and cluster orchestrator.

Handles remote container startup, readiness probing, sanity queries, and teardown.

Usage:
    python3 eval/launch_model.py [--model model_name] [--stop] [--v]
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import time
from typing import Any

from eval.config import (
    API_BASE_URL,
    MODELS_CONFIG_FILE,
    REMOTE_HOST,
    REMOTE_VLLM_DIR,
)
from eval.common import setup_logger, load_json_config, run_cmd, http_json, resolve_target_models

logger = setup_logger("launch_model")

__all__ = [
    "parse_model_config",
    "run_remote",
    "stop_model",
    "launch_model",
    "is_server_ready",
    "wait_for_server_ready",
    "check_kv_cache_concurrency",
    "run_sanity_test",
    "ensure_model_running",
]


def parse_model_config(cfg: dict[str, Any]) -> tuple[str, str]:
    """Extract the model weight name and construct full vLLM launch command string."""
    env = str(cfg.get("model-arg-env", "")).strip()
    base = str(cfg.get("model-arg", "")).strip()
    extras = [
        str(v).strip()
        for k, v in sorted(cfg.items())
        if k.startswith("model-arg-") and k not in ("model-arg-env", "model-arg") and v
    ]
    vllm_cmd = " ".join(filter(None, [env, base, *extras]))

    tokens = base.split()
    if "serve" in tokens:
        idx = tokens.index("serve")
        if idx + 1 < len(tokens) and not tokens[idx + 1].startswith("-"):
            return tokens[idx + 1], vllm_cmd
    raise ValueError("Could not determine served model weight from 'model-arg' (expected 'vllm serve <model_id>')")


def run_remote(cmd: str, host: str = REMOTE_HOST) -> subprocess.CompletedProcess:
    """Execute a command on the remote host via SSH."""
    logger.debug("Executing remote command on %s: %s", host, cmd)
    return run_cmd("ssh", host, cmd)


def stop_model(host: str = REMOTE_HOST) -> bool:
    """Stop the running model container on the remote cluster."""
    logger.info("Stopping running model container on %s...", host)
    res = run_remote(f"cd {REMOTE_VLLM_DIR} && ./launch-cluster.sh --solo stop", host=host)
    if res.stdout and res.stdout.strip():
        logger.debug("[stop_model] %s", res.stdout.strip())
    if res.stderr and res.returncode != 0:
        logger.info("[stop_model warning] %s", res.stderr.strip())
    return res.returncode == 0


def launch_model(model_name: str, vllm_cmd: str, host: str = REMOTE_HOST) -> bool:
    """Stop any running instance and start the model container in daemon mode."""
    stop_model(host=host)
    single_line_cmd = " ".join(vllm_cmd.split())
    logger.info("Launching '%s'", model_name)
    logger.debug("Command: %s", single_line_cmd)

    mod_flag = ""
    if "Nemotron-3-Super" in model_name or "Nemotron-3-Super" in single_line_cmd:
        mod_flag = "--apply-mod mods/nemotron-super"

    launch_script_cmd = f"./launch-cluster.sh -d --solo {mod_flag} exec {single_line_cmd}".replace("  ", " ")
    res = run_remote(f"cd {REMOTE_VLLM_DIR} && {launch_script_cmd}", host=host)
    if res.stdout and res.stdout.strip():
        logger.debug("[launch_model] %s", res.stdout.strip())
    if res.stderr and res.returncode != 0:
        logger.error("Error launching model %s: %s", model_name, res.stderr.strip())
        return False
    return res.returncode == 0


def is_server_ready(expected_weight: str | None = None, base_url: str = API_BASE_URL) -> bool:
    """Check if /v1/models responds with HTTP 200 and has expected_model loaded."""
    data = http_json(f"{base_url}/v1/models", timeout=3)
    if not data:
        return False
    if expected_weight:
        loaded_ids = [item.get("id") for item in data.get("data", [])]
        logger.debug("Loaded model IDs on server: %s (looking for '%s')", loaded_ids, expected_weight)
        return expected_weight in loaded_ids
    return True


def wait_for_server_ready(
    expected_weight: str | None = None,
    base_url: str = API_BASE_URL,
    timeout_seconds: int = 600,
    verbose: bool = False,
    host: str = REMOTE_HOST,
) -> bool:
    """Poll endpoint until the vLLM server is responsive with expected_model."""
    logger.info("Waiting for vLLM server at %s (timeout: %ds)...", base_url, timeout_seconds)
    start_time = time.time()
    log_proc = None

    if verbose:
        logger.info("Streaming live logs from %s:vllm_node...", host)
        try:
            log_proc = subprocess.Popen(
                ["ssh", host, "docker logs -f vllm_node"],
                stdout=sys.stdout,
                stderr=sys.stderr,
                text=True,
            )
        except Exception as e:
            logger.warning("Could not stream logs: %s", e)

    try:
        while time.time() - start_time < timeout_seconds:
            if is_server_ready(expected_weight=expected_weight, base_url=base_url):
                elapsed = round(time.time() - start_time, 1)
                logger.info("Server is UP and healthy after %.1fs!", elapsed)
                return True
            time.sleep(3 if verbose else 10)
            if not verbose:
                logger.debug("Waiting for server...")

        logger.error("Timed out waiting for server at %s after %ds.", base_url, timeout_seconds)
        return False
    finally:
        if log_proc:
            log_proc.terminate()


def run_sanity_test(model_id: str, base_url: str = API_BASE_URL, reasoning_effort: str | None = None) -> bool:
    """Send a test query to verify basic generation and reasoning output."""
    prompt = "What is the capital of Japan? Answer with the city name only."
    logger.debug("Running Sanity Test on %s with prompt: \"%s\" (reasoning_effort: %s)", model_id, prompt, reasoning_effort)

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
    }
    if reasoning_effort and reasoning_effort not in ("off", "none", "on"):
        payload["reasoning_effort"] = reasoning_effort

    data = http_json(
        f"{base_url}/v1/chat/completions",
        method="POST",
        data=payload,
        timeout=60,
    )
    if not data:
        logger.error("✗ Sanity test FAILED: No response from server")
        return False

    msg = data.get("choices", [{}])[0].get("message", {})
    answer = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    logger.debug("Sanity test response: \"%s\"", answer)
    if "tokyo" in answer.lower():
        logger.info("✓ Sanity test PASSED!")
        return True
    logger.error("✗ Sanity test FAILED: Expected 'Tokyo', got '%s'", answer)
    return False


def check_kv_cache_concurrency(
    host: str = REMOTE_HOST,
    container_name: str = "vllm_node",
    min_concurrency: float = 2.0,
    max_concurrency: float = 2.1,
) -> float | None:
    """Extract and validate the Maximum concurrency reported by vLLM in container logs."""
    res = run_remote(f"docker logs {container_name}", host=host)
    full_output = (res.stdout or "") + (res.stderr or "")

    # Look for memory difference guidance log
    mem_match = re.search(r"If OOM'ed, check the difference of initial free memory[^\n]+", full_output)
    if mem_match:
        logger.info("[kv-cache-check] %s", mem_match.group(0).strip())

    # Look for: GPU KV cache size: 527,372 tokens, Maximum concurrency for 131,072 tokens per request: 4.02x
    matches = re.findall(r"Maximum concurrency for [0-9,]+ tokens per request:\s*([0-9.]+)x", full_output)
    if not matches:
        logger.error("[kv-cache-check] ✗ Could not find 'Maximum concurrency' in container logs. Aborting.")
        return None

    concurrency_val = float(matches[-1])
    logger.info("[kv-cache-check] Detected Maximum concurrency: %.2fx", concurrency_val)

    if min_concurrency <= concurrency_val <= max_concurrency:
        logger.info("✓ Maximum concurrency is within expected range [%.2f, %.2f] (got %.2fx)", min_concurrency, max_concurrency, concurrency_val)
    else:
        logger.warning("⚠ Maximum concurrency %.2fx is OUTSIDE expected range [%.2f, %.2f]", concurrency_val, min_concurrency, max_concurrency)

    return concurrency_val


def ensure_model_running(
    model_name: str,
    model_config: dict[str, Any],
    host: str = REMOTE_HOST,
    base_url: str = API_BASE_URL,
    verbose: bool = False,
) -> tuple[bool, str]:
    """Ensure target model is running on the cluster, ready, and sanity-tested.

    Always restarts the container first to guarantee the correct runtime configuration.
    Leaves the model running and returns (success, weight_name).
    """
    weight_name, vllm_cmd = parse_model_config(model_config)
    reasoning_effort = model_config.get("reasoning_effort")

    if not launch_model(model_name, vllm_cmd, host=host):
        return False, weight_name
    if not wait_for_server_ready(expected_weight=weight_name, base_url=base_url, verbose=verbose, host=host):
        return False, weight_name

    concurrency = check_kv_cache_concurrency(host=host)
    if concurrency is None:
        logger.error("Aborting model startup: failed to verify KV cache concurrency.")
        return False, weight_name

    if not run_sanity_test(weight_name, base_url=base_url, reasoning_effort=reasoning_effort):
        return False, weight_name

    logger.info("Keep-alive enabled: leaving '%s' running.", weight_name)
    return True, weight_name


def main():
    models = load_json_config(MODELS_CONFIG_FILE)
    parser = argparse.ArgumentParser(description="Launch, manage, or test vLLM models on remote cluster.")
    parser.add_argument("--model", default="all", help="Model to launch or 'all'")
    parser.add_argument("--stop", action="store_true", help="Stop running model container")
    parser.add_argument("--v", dest="verbose", action="store_true", help="Verbose log streaming")

    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if args.stop:
        ok = stop_model()
        sys.exit(0 if ok else 1)

    target_models = resolve_target_models(models, args.model)

    for m_name in target_models:
        ok, _ = ensure_model_running(m_name, models[m_name], verbose=args.verbose)
        if not ok:
            logger.error("Failed to start model: %s", m_name)
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
