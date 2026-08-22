#!/usr/bin/env python3
"""
launch_model.py - Minimal vLLM model evaluation runner and cluster orchestrator.

Usage:
    python3 eval/launch_model.py [model_name] [--test test_name] [--fast] [--v]
"""

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from config import *
from common import setup_logger
from typing import Any

logger = setup_logger("launch_model")


def load_models(config_path: str = MODELS_CONFIG_FILE) -> dict[str, Any]:
    """Load model definitions from JSON file."""
    if not os.path.exists(config_path):
        logger.error("Config file not found at %s", config_path)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


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
    return subprocess.run(["ssh", host, cmd], capture_output=True, text=True)


def stop_model(host: str = REMOTE_HOST) -> bool:
    """Stop the running model container."""
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
    res = run_remote(f"cd {REMOTE_VLLM_DIR} && ./launch-cluster.sh -d --solo exec {single_line_cmd}", host=host)
    if res.stdout and res.stdout.strip():
        logger.debug("[launch_model] %s", res.stdout.strip())
    if res.stderr and res.returncode != 0:
        logger.error("Error launching model %s: %s", model_name, res.stderr.strip())
        return False
    return res.returncode == 0


def is_server_ready(expected_weight: str | None = None, base_url: str = API_BASE_URL) -> bool:
    """Check if /v1/models responds with HTTP 200 and has expected_model loaded."""
    try:
        req = urllib.request.Request(f"{base_url}/v1/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status != 200:
                return False
            if expected_weight:
                data = json.loads(resp.read().decode("utf-8"))
                loaded_ids = [item.get("id") for item in data.get("data", [])]
                logger.debug("Loaded model IDs on server: %s (looking for '%s')", loaded_ids, expected_weight)
                return expected_weight in loaded_ids
            return True
    except Exception as e:
        return False


def wait_for_server_ready(expected_weight: str | None = None, base_url: str = API_BASE_URL, timeout_seconds: int = 600, verbose: bool = False, host: str = REMOTE_HOST) -> bool:
    """Poll endpoint until the vLLM server is responsive with expected_model."""
    logger.info("Waiting for vLLM server at %s (timeout: %ds)...", base_url, timeout_seconds)
    start_time = time.time()
    log_proc = None

    if verbose:
        logger.info("Streaming live logs from %s:vllm_node...", host)
        try:
            log_proc = subprocess.Popen(["ssh", host, "docker logs -f vllm_node"], stdout=sys.stdout, stderr=sys.stderr, text=True)
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


def run_sanity_test(model_id: str, base_url: str = API_BASE_URL) -> bool:
    """Send a test query to verify basic generation and reasoning output."""
    prompt = "What is the capital of Japan? Answer with the city name only."
    logger.debug("Running Sanity Test on %s with prompt: \"%s\"", model_id, prompt)

    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
    }).encode("utf-8")
    req = urllib.request.Request(f"{base_url}/v1/chat/completions", data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            msg = data.get("choices", [{}])[0].get("message", {})
            content = (msg.get("content") or "").strip()
            reasoning = (msg.get("reasoning_content") or "").strip()
            answer = content or reasoning

            logger.debug("Sanity test response: \"%s\"", answer)
            if reasoning and content:
                logger.debug("Reasoning tokens: %d words", len(reasoning.split()))

            if "tokyo" in answer.lower():
                logger.info("✓ Sanity test PASSED!")
                return True
            logger.error("✗ Sanity test FAILED: Expected 'Tokyo', got '%s'", answer)
            return False
    except Exception as e:
        logger.error("✗ Sanity test FAILED with error: %s", e)
        return False


def run_model_pipeline(
    model_name: str,
    model_config: dict[str, Any],
    host: str = REMOTE_HOST,
    base_url: str = API_BASE_URL,
    fast: bool = False,
    verbose: bool = False,
    test_name: str = "all",
) -> bool:
    """Run full lifecycle: launch -> wait -> sanity test -> harness -> stop."""
    logger.info("STARTING PIPELINE FOR: %s", model_name)

    weight_name, vllm_cmd = parse_model_config(model_config)
    need_launch = True
    if fast and is_server_ready(expected_weight=weight_name, base_url=base_url):
        logger.info("Fast mode: '%s' is already UP. Skipping launch.", weight_name)
        need_launch = False

    success = False
    try:
        if need_launch:
            if not launch_model(model_name, vllm_cmd, host=host):
                return False
            if not wait_for_server_ready(expected_weight=weight_name, base_url=base_url, verbose=verbose, host=host):
                return False

        if not run_sanity_test(weight_name, base_url=base_url):
            return False

        logger.info("Executing Evaluation Harness for %s (test: %s)...", model_name, test_name)
        harness_script = REPO_ROOT / "eval" / "run_harness.py"
        cmd = [sys.executable, str(harness_script), model_name, "--test", test_name]
        if verbose:
            cmd.append("--v")
        res = subprocess.run(cmd)
        if res.returncode != 0:
            logger.error("Evaluation harness failed with exit code %d", res.returncode)
            return False

        success = True
    finally:
        if not fast:
            stop_model(host=host)
        else:
            logger.info("Fast mode enabled: leaving vLLM server running.")

    status_str = "SUCCESS" if success else "FAILED"
    logger.info("FINISHED PIPELINE FOR: %s (Status: %s)", model_name, status_str)
    return success


def main():
    models = load_models()
    parser = argparse.ArgumentParser(description="Run vLLM model evaluation pipeline.")
    parser.add_argument("model", nargs="?", default=None, choices=list(models.keys()), help="Model to evaluate (runs all if omitted)")
    parser.add_argument("--test", default="all", help="Test to run (default: all)")
    parser.add_argument("--fast", action="store_true", help="Fast mode: skip launch and teardown")
    parser.add_argument("--v", dest="verbose", action="store_true", help="Verbose log streaming")

    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    target_models = [args.model] if args.model else list(models.keys())
    logger.info("Running '%s' test(s) on %d model(s)...", args.test, len(target_models))

    for model_name in target_models:
        ok = run_model_pipeline(model_name, models[model_name], fast=args.fast, verbose=args.verbose, test_name=args.test)
        if not ok:
            logger.error("Pipeline stopped on failure for model: %s", model_name)
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
