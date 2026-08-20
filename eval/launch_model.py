#!/usr/bin/env python3
"""
launch_model.py - Minimal vLLM model evaluation runner and cluster orchestrator.

Usage:
    python3 eval/launch_model.py [model_name] [--test test_name] [--fast] [--v]
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

REMOTE_HOST = "mike@spark"
REMOTE_VLLM_DIR = "~/apps/spark-vllm-docker"
API_BASE_URL = "http://spark:8000"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")


def load_models(config_path: str = CONFIG_FILE) -> dict[str, Any]:
    """Load model definitions from JSON file."""
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}", file=sys.stderr)
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
    return subprocess.run(["ssh", host, cmd], capture_output=True, text=True)


def stop_model(host: str = REMOTE_HOST) -> bool:
    """Stop the running model container."""
    print(f"--> Stopping model container on {host}...")
    res = run_remote(f"cd {REMOTE_VLLM_DIR} && ./launch-cluster.sh --solo stop", host=host)
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr and res.returncode != 0:
        print(f"Warning: {res.stderr.strip()}", file=sys.stderr)
    return res.returncode == 0


def launch_model(model_name: str, vllm_cmd: str, host: str = REMOTE_HOST) -> bool:
    """Stop any running instance and start the model container in daemon mode."""
    stop_model(host=host)
    single_line_cmd = " ".join(vllm_cmd.split())
    print(f"\n--> Launching '{model_name}' on {host}...\n    Command: {single_line_cmd}")
    res = run_remote(f"cd {REMOTE_VLLM_DIR} && ./launch-cluster.sh -d --solo exec {single_line_cmd}", host=host)
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr and res.returncode != 0:
        print(f"Error launching model: {res.stderr.strip()}", file=sys.stderr)
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
                return expected_weight in loaded_ids
            return True
    except Exception:
        return False


def wait_for_server_ready(expected_weight: str | None = None, base_url: str = API_BASE_URL, timeout_seconds: int = 600, verbose: bool = False, host: str = REMOTE_HOST) -> bool:
    """Poll endpoint until the vLLM server is responsive with expected_model."""
    print(f"--> Waiting for vLLM server at {base_url} (timeout: {timeout_seconds}s)...")
    start_time = time.time()
    log_proc = None

    if verbose:
        print(f"--> [VERBOSE] Streaming live logs from {host}:vllm_node...")
        try:
            log_proc = subprocess.Popen(["ssh", host, "docker logs -f vllm_node"], stdout=sys.stdout, stderr=sys.stderr, text=True)
        except Exception as e:
            print(f"Warning: Could not stream logs: {e}", file=sys.stderr)

    try:
        while time.time() - start_time < timeout_seconds:
            if is_server_ready(expected_weight=expected_weight, base_url=base_url):
                elapsed = round(time.time() - start_time, 1)
                print(f"\n--> Server is UP and healthy after {elapsed}s!")
                return True
            time.sleep(3 if verbose else 10)
            if not verbose:
                sys.stdout.write(".")
                sys.stdout.flush()

        print(f"\nError: Timed out waiting for server at {base_url} after {timeout_seconds}s.", file=sys.stderr)
        return False
    finally:
        if log_proc:
            log_proc.terminate()


def run_sanity_test(model_id: str, base_url: str = API_BASE_URL) -> bool:
    """Send a test query to verify basic generation and reasoning output."""
    prompt = "What is the capital of Japan? Answer with the city name only."
    print(f"\n--> Running Sanity Test on {model_id}...\n    Prompt: \"{prompt}\"")

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

            print(f'    Response: "{answer}"')
            if reasoning and content:
                print(f"    (Reasoning tokens: {len(reasoning.split())} words)")

            if "tokyo" in answer.lower():
                print("    ✓ Sanity test PASSED!")
                return True
            print(f"    ✗ Sanity test FAILED: Expected 'Tokyo', got '{answer}'", file=sys.stderr)
            return False
    except Exception as e:
        print(f"    ✗ Sanity test FAILED with error: {e}", file=sys.stderr)
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
    print(f"\n{'=' * 70}\nSTARTING PIPELINE FOR: {model_name} {'[FAST DEV MODE]' if fast else ''}\n{'=' * 70}")

    weight_name, vllm_cmd = parse_model_config(model_config)
    need_launch = True
    if fast and is_server_ready(expected_weight=weight_name, base_url=base_url):
        print(f"--> Fast mode: server is already UP with '{weight_name}'. Skipping launch.")
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

        print(f"\n--> Executing Evaluation Harness for {model_name} (test: {test_name})...")
        harness_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_harness.py")
        res = subprocess.run([sys.executable, harness_script, model_name, "--test", test_name, "--base-url", f"{base_url}/v1"])
        if res.returncode != 0:
            print(f"✗ Evaluation harness failed with exit code {res.returncode}", file=sys.stderr)
            return False

        success = True
    finally:
        if not fast:
            stop_model(host=host)
        else:
            print("--> Fast mode enabled: leaving vLLM server running.")

    print(f"{'=' * 70}\nFINISHED PIPELINE FOR: {model_name} (Status: {'SUCCESS' if success else 'FAILED'})\n{'=' * 70}\n")
    return success


def main():
    models = load_models()
    parser = argparse.ArgumentParser(description="Run vLLM model evaluation pipeline.")
    parser.add_argument("model", nargs="?", default=None, choices=list(models.keys()), help="Model to evaluate (runs all if omitted)")
    parser.add_argument("--test", default="all", help="Test to run (default: all)")
    parser.add_argument("--fast", action="store_true", help="Fast mode: skip launch and teardown")
    parser.add_argument("--v", dest="verbose", action="store_true", help="Verbose log streaming")

    args = parser.parse_args()
    target_models = [args.model] if args.model else list(models.keys())

    for model_name in target_models:
        ok = run_model_pipeline(model_name, models[model_name], fast=args.fast, verbose=args.verbose, test_name=args.test)
        if not ok:
            print(f"Pipeline stopped on failure for model: {model_name}", file=sys.stderr)
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
