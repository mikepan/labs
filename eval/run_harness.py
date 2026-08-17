#!/usr/bin/env python3
"""
run_harness.py - Drive OpenCode CLI / Server inside an isolated Docker Sandbox to execute evaluation tasks.

Usage:
    python3 eval/run_harness.py <model_name> [--prompt "Why is the sky blue?"]
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

DEFAULT_PROMPT = "Why is the sky blue? Write out a .md file as the answer."
DEFAULT_LLM_BASE_URL = "http://spark:8000/v1"
SANDBOX_NAME = "eval-harness-worker"
TEMPLATE_TAG = "eval-base-harness:latest"
OPENCODE_PORT = 4096
OPENCODE_SERVER_URL = f"http://127.0.0.1:{OPENCODE_PORT}"
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_sandbox(name: str = SANDBOX_NAME, template: str = TEMPLATE_TAG, workspace: str = WORKSPACE_DIR) -> None:
    """Ensure a clean ephemeral sandbox container is created from the base template."""
    # Remove any existing container with same name
    subprocess.run(["sbx", "rm", "-f", name], capture_output=True, text=True)

    print(f"--> Provisioning isolated sandbox '{name}' from template '{template}'...")
    create_cmd = [
        "sbx", "create",
        "--name", name,
        "--template", template,
        "-p", f"{OPENCODE_PORT}:{OPENCODE_PORT}",
        "shell", workspace,
    ]
    res = subprocess.run(create_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error creating sandbox:\n{res.stderr}\n{res.stdout}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ Isolated sandbox '{name}' is ready.")


def remove_sandbox(name: str = SANDBOX_NAME) -> None:
    """Clean up and remove the sandbox container."""
    print(f"--> Cleaning up sandbox '{name}'...")
    subprocess.run(["sbx", "rm", "-f", name], capture_output=True, text=True)


def configure_opencode_in_sandbox(
    model_name: str,
    sandbox_name: str = SANDBOX_NAME,
    llm_base_url: str = DEFAULT_LLM_BASE_URL,
) -> None:
    """Write opencode.json configuration inside the sandbox container."""
    config_data = {
        "share": "disabled",
        "provider": {
            "sparky": {
                "name": "sparky",
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "baseURL": llm_base_url,
                },
                "models": {
                    model_name: {
                        "name": model_name,
                        "modalities": {
                            "input": ["text", "image"],
                            "output": ["text"],
                        },
                    },
                },
            },
        },
    }
    config_json = json.dumps(config_data, indent=2)
    setup_cmd = f"mkdir -p ~/.config/opencode && cat << 'EOF' > ~/.config/opencode/opencode.json\n{config_json}\nEOF"
    subprocess.run(["sbx", "exec", sandbox_name, "bash", "-c", setup_cmd], check=True, capture_output=True)


def start_opencode_in_sandbox(sandbox_name: str = SANDBOX_NAME, port: int = OPENCODE_PORT) -> None:
    """Start headless OpenCode REST server in background inside the sandbox."""
    print(f"--> Starting OpenCode server inside sandbox on port {port}...")
    run_server_cmd = (
        "export PATH=$HOME/.opencode/bin:$HOME/.local/bin:$PATH; "
        f"nohup opencode serve --port {port} --hostname 0.0.0.0 > /tmp/opencode_server.log 2>&1 &"
    )
    subprocess.run(["sbx", "exec", sandbox_name, "bash", "-c", run_server_cmd], check=True, capture_output=True)

    # Poll server endpoint until responsive
    for _ in range(20):
        try:
            with urllib.request.urlopen(f"{OPENCODE_SERVER_URL}/session", timeout=1):
                print(f"✓ OpenCode server responsive at {OPENCODE_SERVER_URL}")
                return
        except Exception:
            time.sleep(0.5)

    print("Warning: OpenCode server took longer than expected to respond.", file=sys.stderr)


def create_session(base_url: str = OPENCODE_SERVER_URL) -> str:
    """Create a new session in OpenCode server."""
    url = f"{base_url}/session"
    req = urllib.request.Request(
        url,
        data=json.dumps({"directory": WORKSPACE_DIR}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["id"]


def send_message(
    session_id: str,
    prompt: str,
    model_name: str,
    provider_id: str = "sparky",
    base_url: str = OPENCODE_SERVER_URL,
) -> dict[str, Any]:
    """Send task prompt to OpenCode and return full response."""
    url = f"{base_url}/session/{session_id}/message"
    payload = {
        "parts": [{"type": "text", "text": prompt}],
        "model": {
            "providerID": provider_id,
            "modelID": model_name,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_opencode_task(
    model_name: str,
    prompt: str = DEFAULT_PROMPT,
) -> dict[str, Any]:
    """Execute full task pipeline: sandbox provision -> opencode config & serve -> run task -> teardown."""
    ensure_sandbox()
    try:
        configure_opencode_in_sandbox(model_name)
        start_opencode_in_sandbox()

        print(f"--> Creating OpenCode session for model '{model_name}'...")
        session_id = create_session()
        print(f"✓ Session created: {session_id}")

        print(f"--> Sending prompt to agent: \"{prompt}\"...")
        start_time = time.time()
        result = send_message(session_id, prompt, model_name=model_name)
        elapsed = round(time.time() - start_time, 2)
        print(f"✓ Agent completed task in {elapsed}s!")

        return result
    finally:
        remove_sandbox()


def main():
    parser = argparse.ArgumentParser(description="Drive OpenCode harness in isolated Docker Sandbox for model evaluation")
    parser.add_argument("model", help="Model name (e.g. nvidia/diffusiongemma-26B-A4B-IT-NVFP4)")
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help=f"Task prompt to execute (default: '{DEFAULT_PROMPT}')",
    )
    args = parser.parse_args()

    print(f"======================================================================")
    print(f"RUNNING OPENCODE HARNESS IN DOCKER SANDBOX FOR MODEL: {args.model}")
    print(f"======================================================================")

    result = run_opencode_task(args.model, prompt=args.prompt)

    # Pretty-print text and reasoning output
    print("\n" + "=" * 70)
    print("HARNESS OUTPUT:")
    print("=" * 70)
    parts = result.get("parts", [])
    for part in parts:
        p_type = part.get("type")
        if p_type == "reasoning":
            print(f"\n[Reasoning]:\n{part.get('text', '')}")
        elif p_type == "text":
            print(f"\n[Response]:\n{part.get('text', '')}")
    print("=" * 70)


if __name__ == "__main__":
    main()
