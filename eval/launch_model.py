#!/usr/bin/env python3
"""
launch_model.py - Model evaluation runner and cluster orchestrator.

Usage:
    python3 eval/launch_model.py <model_name> [--fast] [--v]

Arguments:
    model_name: Name of the model to evaluate (from models.json).
    --fast:     Fast iteration mode. Uses the existing vLLM server without
                re-launching or tearing it down.
    --v / -v:   Verbose mode. Streams the full vLLM startup and server logs in real-time.

Workflow:
    1. Launch vLLM server via ./launch-cluster.sh --solo exec ... (skipped if --fast)
    2. Wait until server /health and /v1/models are UP and ready (streams logs if --v)
    3. Run warmup / sanity test
    4. Run evaluation harness via run_harness.py
    5. Stop vLLM server (skipped if --fast)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# Server & endpoint configuration
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


def build_vllm_command(model_config: dict[str, Any]) -> str:
    """Construct full vLLM launch command string from structured configuration.

    Concatenation order:
      1. model-arg-env (inline environment variables, e.g. VLLM_USE_DEEP_GEMM=0)
      2. model-arg (base vllm serve command)
      3. All other keys starting with 'model-arg-*' in sorted order
    """
    parts = []

    # 1. model-arg-env
    env_arg = str(model_config.get("model-arg-env", "")).strip()
    if env_arg:
        parts.append(env_arg)

    # 2. model-arg
    base_arg = str(model_config.get("model-arg", "")).strip()
    if base_arg:
        parts.append(base_arg)

    # 3. Everything else starting with model-arg-* (excluding model-arg-env and model-arg)
    for key in sorted(model_config.keys()):
        if key.startswith("model-arg-") and key not in ("model-arg-env", "model-arg"):
            val = str(model_config[key]).strip()
            if val:
                parts.append(val)

    return " ".join(parts)


def get_model_metadata(model_config: dict[str, Any]) -> dict[str, Any]:
    """Extract feature flags and metadata from model configuration."""
    spec_arg = str(model_config.get("model-arg-speculative", "")).strip()
    kv_arg = str(model_config.get("model-arg-kv-quant", "")).strip()
    platform_arg = str(model_config.get("model-arg-platform", "")).strip()
    env_arg = str(model_config.get("model-arg-env", "")).strip()

    spec_type = None
    if spec_arg:
        if "--diffusion-config" in spec_arg:
            spec_type = "diffusion"
        else:
            match = re.search(r'"method":\s*"([^"]+)"', spec_arg)
            spec_type = match.group(1) if match else "speculative"

    kv_type = None
    if kv_arg:
        match = re.search(r"--kv-cache-dtype\s+(\S+)", kv_arg)
        kv_type = match.group(1) if match else kv_arg

    return {
        "speculative_decoding": spec_type,
        "speculative_config": spec_arg if spec_arg else None,
        "kv_quantization": kv_type,
        "kv_quant_config": kv_arg if kv_arg else None,
        "platform_config": platform_arg if platform_arg else None,
        "env_config": env_arg if env_arg else None,
    }


def run_remote_command(cmd: str, host: str = REMOTE_HOST) -> subprocess.CompletedProcess:
    """Execute a command on the remote host via SSH."""
    full_cmd = ["ssh", host, cmd]
    return subprocess.run(full_cmd, capture_output=True, text=True)


def stop_model(host: str = REMOTE_HOST) -> bool:
    """Stop the running model container using ./launch-cluster.sh --solo stop."""
    print(f"--> Stopping model container on {host}...")
    stop_cmd = f"cd {REMOTE_VLLM_DIR} && ./launch-cluster.sh --solo stop"
    res = run_remote_command(stop_cmd, host=host)
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr and res.returncode != 0:
        print(f"Warning/Error: {res.stderr.strip()}", file=sys.stderr)
    return res.returncode == 0


def launch_model(model_name: str, vllm_cmd: str, host: str = REMOTE_HOST) -> bool:
    """Launch a model in daemon mode using ./launch-cluster.sh -d --solo exec vllm serve ..."""
    # Ensure any previous container is stopped
    stop_model(host=host)

    single_line_cmd = " ".join(line.rstrip(" \\") for line in vllm_cmd.strip().splitlines())
    launch_cmd = f"cd {REMOTE_VLLM_DIR} && ./launch-cluster.sh -d --solo exec {single_line_cmd}"

    print(f"\n--> Launching '{model_name}' on {host}...")
    print(f"    Command: {single_line_cmd}")
    res = run_remote_command(launch_cmd, host=host)
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr and res.returncode != 0:
        print(f"Error launching model: {res.stderr.strip()}", file=sys.stderr)
        return False

    return res.returncode == 0


def get_active_models(base_url: str = API_BASE_URL) -> list[str]:
    """Retrieve list of currently loaded model IDs from /v1/models if server is up."""
    try:
        req = urllib.request.Request(f"{base_url}/v1/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return [item.get("id", "") for item in data.get("data", [])]
    except Exception:
        pass
    return []


def wait_for_server_ready(base_url: str = API_BASE_URL, timeout_seconds: int = 600, verbose: bool = False, host: str = REMOTE_HOST) -> bool:
    """Poll health and models endpoint until the vLLM server is responsive."""
    print(f"--> Waiting for vLLM server to become ready at {base_url} (timeout: {timeout_seconds}s)...")
    health_url = f"{base_url}/health"
    models_url = f"{base_url}/v1/models"
    start_time = time.time()

    log_proc = None
    if verbose:
        print(f"--> [VERBOSE] Streaming live vLLM startup logs from {host}:vllm_node...")
        try:
            log_proc = subprocess.Popen(
                ["ssh", host, "docker logs -f vllm_node"],
                stdout=sys.stdout,
                stderr=sys.stderr,
                text=True,
            )
        except Exception as e:
            print(f"Warning: Could not start log streaming: {e}", file=sys.stderr)

    try:
        while time.time() - start_time < timeout_seconds:
            try:
                req = urllib.request.Request(health_url)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        req_models = urllib.request.Request(models_url)
                        with urllib.request.urlopen(req_models, timeout=3) as m_resp:
                            if m_resp.status == 200:
                                elapsed = round(time.time() - start_time, 1)
                                print(f"\n--> Server is UP and healthy after {elapsed}s!")
                                return True
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass

            if not verbose:
                time.sleep(10)
                sys.stdout.write(".")
                sys.stdout.flush()
            else:
                time.sleep(3)

        print(
            f"\nError: Timed out waiting for server at {base_url} after {timeout_seconds}s.",
            file=sys.stderr,
        )
        if not verbose:
            # Print last 30 log lines on failure for diagnostic visibility
            print("\n--> [DIAGNOSTIC] Last 30 lines of container log on failure:")
            fail_logs = run_remote_command("docker logs --tail 30 vllm_node", host=host)
            if fail_logs.stdout:
                print(fail_logs.stdout)
        return False
    finally:
        if log_proc:
            try:
                log_proc.terminate()
                log_proc.wait(timeout=2)
            except Exception:
                try:
                    log_proc.kill()
                except Exception:
                    pass


def run_sanity_test(model_name: str, base_url: str = API_BASE_URL) -> bool:
    """Send test query 'What is the capital of Japan? Answer with the city name only.' and validate."""
    chat_url = f"{base_url}/v1/chat/completions"
    prompt = "What is the capital of Japan? Answer with the city name only."
    print(f"\n--> Running Sanity Test on {model_name}...")
    print(f'    Prompt: "{prompt}"')

    payload = json.dumps(
        {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
        }
    ).encode("utf-8")

    req = urllib.request.Request(chat_url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            msg = data.get("choices", [{}])[0].get("message", {})
            content = (msg.get("content") or "").strip()
            reasoning = (msg.get("reasoning_content") or "").strip()
            answer = content if content else reasoning

            print(f'    Response: "{answer}"')
            if reasoning and content:
                print(f"    (Reasoning tokens: {len(reasoning.split())} words)")

            if "tokyo" in answer.lower():
                print("    ✓ Sanity test PASSED! (Answer contains 'Tokyo')")
                return True
            else:
                print(
                    f"    ✗ Sanity test FAILED: Expected 'Tokyo' in response, got '{answer}'",
                    file=sys.stderr,
                )
                return False
    except Exception as e:
        print(f"    ✗ Sanity test FAILED with error: {e}", file=sys.stderr)
        return False


def run_evaluation_harness(model_name: str, metadata: dict[str, Any]) -> bool:
    """Execute evaluation harness via run_harness.py."""
    print(f"\n--> Executing Evaluation Harness for {model_name}...")
    print(f"    Speculative Decoding: {metadata.get('speculative_decoding') or 'OFF'}")
    print(f"    KV Cache Quantization: {metadata.get('kv_quantization') or 'OFF'}")

    harness_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_harness.py")
    cmd = [sys.executable, harness_script, model_name]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"✗ Evaluation harness failed with exit code {res.returncode}", file=sys.stderr)
        return False
    print(f"✓ Evaluation harness completed successfully for {model_name}.")
    return True


def run_model_pipeline(
    model_name: str, model_config: dict[str, Any], host: str = REMOTE_HOST, base_url: str = API_BASE_URL, fast: bool = False, verbose: bool = False
) -> bool:
    """Run full lifecycle for a single model: launch -> wait -> warmup test -> run harness -> stop."""
    vllm_cmd = build_vllm_command(model_config)
    metadata = get_model_metadata(model_config)

    print(f"\n{'=' * 70}")
    print(f"STARTING PIPELINE FOR: {model_name} {'[FAST DEV MODE]' if fast else ''}".strip())
    print(f"  Speculative Decoding: {metadata['speculative_decoding'] or 'OFF'}")
    print(f"  KV Cache Quantization: {metadata['kv_quantization'] or 'OFF'}")
    print(f"{'=' * 70}")

    need_launch = True
    if fast:
        active_models = get_active_models(base_url=base_url)
        if model_name in active_models:
            print(f"--> Fast mode: server is already UP with active model '{model_name}'. Skipping launch.")
            need_launch = False
        elif active_models:
            print(
                f"--> Fast mode: server is UP with a different model {active_models}. Re-launching with '{model_name}'..."
            )
            need_launch = True
        else:
            print(f"--> Fast mode: server is down. Auto-launching '{model_name}'...")
            need_launch = True

    success = False
    try:
        if need_launch:
            if not launch_model(model_name, vllm_cmd, host=host):
                print(f"Failed to launch {model_name}", file=sys.stderr)
                return False

            if not wait_for_server_ready(base_url=base_url, verbose=verbose, host=host):
                print(f"Server failed to become ready for {model_name}", file=sys.stderr)
                return False

        # Direct API call as warmup / sanity test
        print("\n--> Running Direct API Warmup / Sanity Test...")
        sanity_ok = run_sanity_test(model_name, base_url=base_url)
        if not sanity_ok:
            print(f"Warmup test failed for {model_name}", file=sys.stderr)
            return False

        # Run evaluation harness
        harness_ok = run_evaluation_harness(model_name, metadata)
        if not harness_ok:
            return False

        success = True
    finally:
        if not fast:
            stop_model(host=host)
        else:
            print("--> Fast mode enabled: leaving vLLM server running.")

    print(f"{'=' * 70}")
    print(f"FINISHED PIPELINE FOR: {model_name} (Status: {'SUCCESS' if success else 'FAILED'})")
    print(f"{'=' * 70}\n")
    return success


def select_model(name: str | None, models: dict[str, Any]) -> str:
    """Resolve model by exact name."""
    model_keys = list(models.keys())
    if not model_keys:
        print("Error: No models configured in models.json", file=sys.stderr)
        sys.exit(1)

    if not name:
        print(
            "Error: Please specify a model name to run.\nAvailable models:\n" + "\n".join(f"  - {m}" for m in model_keys),
            file=sys.stderr,
        )
        sys.exit(1)

    if name in models:
        return name

    print(
        f"Error: Unknown model '{name}'. Available models:\n" + "\n".join(f"  - {m}" for m in model_keys),
        file=sys.stderr,
    )
    sys.exit(1)


def main():
    models = load_models()
    model_keys = list(models.keys())

    help_desc = "Run vLLM model evaluation pipeline.\n\nAvailable Models:\n" + "\n".join(f"  - {name}" for name in model_keys)

    parser = argparse.ArgumentParser(description=help_desc, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", help="Name of the model to evaluate (e.g. qwen/Qwen3.6-27B-FP8)")
    parser.add_argument("--fast", action="store_true", help="Fast mode: skip launch and teardown")
    parser.add_argument("--v", dest="verbose", action="store_true", help="Verbose mode")

    args = parser.parse_args()

    selected_model = select_model(args.model, models)
    model_config = models[selected_model]
    ok = run_model_pipeline(selected_model, model_config, fast=args.fast, verbose=args.verbose)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
