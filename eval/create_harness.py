#!/usr/bin/env python3
"""
create_harness.py - Build and configure the evaluation agent sandbox on macOS using Docker Sandboxes (sbx).

Reads harnesses.json, provisions an isolated sandbox, installs all specified agent harnesses
(such as Pi and OpenCode CLI), validates installations, records detected versions into harnesses.json,
and saves a reusable template snapshot.

Usage:
    python3 eval/create_harness.py
"""

import json
import os
import subprocess
import sys
from typing import Any

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harnesses.json")
DEFAULT_SANDBOX_NAME = "eval-harness-builder"
DEFAULT_TEMPLATE_TAG = "eval-base-harness:latest"
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_sbx_policy() -> None:
    """Ensure global network policy is initialized in sbx."""
    res = subprocess.run(["sbx", "policy", "init", "allow-all"], capture_output=True, text=True)
    if res.returncode == 0 and res.stdout.strip():
        print(f"--> [sbx policy] {res.stdout.strip()}")


def load_harness_config(config_path: str = CONFIG_FILE) -> dict[str, Any]:
    """Load harnesses configuration from JSON."""
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_harness_config(config: dict[str, Any], config_path: str = CONFIG_FILE) -> None:
    """Write updated configuration back to JSON file."""
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
        f.write("\n")
    print(f"--> Updated {config_path} with detected harness versions.")


def sandbox_exists(name: str) -> bool:
    """Check if sandbox already exists."""
    res = subprocess.run(["sbx", "ls"], capture_output=True, text=True)
    return res.returncode == 0 and name in res.stdout


def remove_sandbox(name: str) -> None:
    """Remove sandbox if it exists."""
    if sandbox_exists(name):
        print(f"--> Removing existing sandbox '{name}'...")
        subprocess.run(["sbx", "rm", "-f", name], capture_output=True, text=True)


def stop_sandbox(name: str) -> None:
    """Stop sandbox if running."""
    subprocess.run(["sbx", "stop", name], capture_output=True, text=True)


def exec_in_sandbox(sandbox_name: str, cmd_str: str) -> tuple[int, str, str]:
    """Execute a bash command inside the sandbox."""
    full_cmd = ["sbx", "exec", sandbox_name, "bash", "-c", cmd_str]
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def install_harness(
    sandbox_name: str,
    harness_name: str,
    harness_info: dict[str, Any],
) -> str | None:
    """Install a specific harness in the sandbox and return its detected version."""
    norm_name = harness_name.lower().strip()
    print(f"\n======================================================================")
    print(f"INSTALLING HARNESS: {harness_name}")
    print(f"======================================================================")

    # Determine installation and version check commands
    install_cmd = harness_info.get("install_cmd")
    version_cmd = harness_info.get("version_cmd")

    if not install_cmd:
        if "pi" in norm_name:
            install_cmd = "curl -fsSL https://pi.dev/install.sh | sh"
            version_cmd = "pi --version"
        elif "opencode" in norm_name:
            install_cmd = (
                "curl -fsSL https://opencode.ai/install | bash && "
                "mkdir -p ~/.local/bin && ln -sf ~/.opencode/bin/opencode ~/.local/bin/opencode 2>/dev/null || true"
            )
            version_cmd = "export PATH=$HOME/.opencode/bin:$HOME/.local/bin:$PATH; opencode --version"
        else:
            print(f"Warning: Unknown harness '{harness_name}' with no install_cmd specified. Skipping.", file=sys.stderr)
            return None

    if not version_cmd:
        version_cmd = f"{norm_name} --version"

    print(f"--> Executing install command:")
    print(f"    {install_cmd}")
    code, stdout, stderr = exec_in_sandbox(sandbox_name, install_cmd)
    if code != 0:
        print(f"Error installing {harness_name} (code {code}):\n{stderr}\n{stdout}", file=sys.stderr)
        return None

    if stdout:
        print(stdout)

    # Check version
    print(f"\n--> Checking version via: {version_cmd}")
    code, v_stdout, v_stderr = exec_in_sandbox(sandbox_name, version_cmd)
    if code == 0 and v_stdout:
        # Extract version output (e.g. "0.84.2" or "1.18.18")
        version_str = v_stdout.splitlines()[-1].strip()
        print(f"✓ {harness_name} successfully installed! Detected version: {version_str}")
        return version_str
    else:
        print(f"Warning: Could not determine version for {harness_name}: {v_stderr} {v_stdout}", file=sys.stderr)
        return None


def save_as_template(sandbox_name: str, template_tag: str) -> bool:
    """Snapshot the sandbox image and save it as a reusable template."""
    print(f"\n--> Stopping sandbox '{sandbox_name}' before snapshotting...")
    stop_sandbox(sandbox_name)

    print(f"--> Saving sandbox '{sandbox_name}' snapshot to template tag '{template_tag}'...")
    res = subprocess.run(["sbx", "template", "save", sandbox_name, template_tag], capture_output=True, text=True)
    if res.returncode == 0:
        print(f"✓ Successfully saved template: {template_tag}")
        return True
    else:
        # Try with y input if prompted
        res = subprocess.run(
            ["sbx", "template", "save", sandbox_name, template_tag],
            input="y\n",
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            print(f"✓ Successfully saved template: {template_tag}")
            return True
        print(f"Warning: Failed to save template '{template_tag}': {res.stderr}\n{res.stdout}", file=sys.stderr)
        return False


def install_evaluation_dependencies(sandbox_name: str) -> None:
    """Install core evaluation dependencies (git, python3, pip, langdetect, html5lib, etc.) in the sandbox."""
    print(f"\n======================================================================")
    print(f"INSTALLING BASE EVALUATION DEPENDENCIES (git, python, langdetect, etc.)")
    print(f"======================================================================")

    # Shell script to install system tools & Python evaluation packages
    setup_script = """
    set -e
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq && apt-get install -y -qq git python3 python3-pip python3-venv curl jq
    elif command -v apk >/dev/null 2>&1; then
        apk update && apk add --no-cache git python3 py3-pip curl jq
    fi

    # Install Python evaluation packages
    python3 -m pip install --upgrade --quiet --break-system-packages pip 2>/dev/null || true
    python3 -m pip install --quiet --break-system-packages langdetect html5lib beautifulsoup4 2>/dev/null || \
    python3 -m pip install --quiet langdetect html5lib beautifulsoup4 || true
    """

    code, stdout, stderr = exec_in_sandbox(sandbox_name, setup_script)
    if code != 0:
        print(f"Warning: Issue installing base eval packages (code {code}):\n{stderr}\n{stdout}", file=sys.stderr)
    else:
        print("✓ System evaluation tools and Python packages installed.")

    # Validation check
    val_cmd = 'git --version && python3 --version && python3 -c "import langdetect, html5lib; print(\'✓ Evaluation Python modules validated (langdetect, html5lib).\')"'
    code, v_out, _ = exec_in_sandbox(sandbox_name, val_cmd)
    if code == 0 and v_out:
        for line in v_out.splitlines():
            print(f"  {line}")
    else:
        print(f"  Note: Validation output: {v_out}")


def main():
    ensure_sbx_policy()

    harnesses = load_harness_config(CONFIG_FILE)
    print(f"--> Loaded {len(harnesses)} harness(es) from {CONFIG_FILE}:")
    for h in harnesses:
        print(f"    - {h}")

    # Remove any existing builder sandbox
    remove_sandbox(DEFAULT_SANDBOX_NAME)

    # Create fresh sandbox
    print(f"\n--> Creating sandbox '{DEFAULT_SANDBOX_NAME}' with workspace: {WORKSPACE_DIR}...")
    create_res = subprocess.run(
        ["sbx", "create", "--name", DEFAULT_SANDBOX_NAME, "shell", WORKSPACE_DIR],
        capture_output=True,
        text=True,
    )
    if create_res.returncode != 0:
        print(f"Error creating sandbox:\n{create_res.stderr}\n{create_res.stdout}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ Sandbox '{DEFAULT_SANDBOX_NAME}' created.")

    # 1. Install base evaluation dependencies
    install_evaluation_dependencies(DEFAULT_SANDBOX_NAME)

    # 2. Install each agent harness
    updated = False
    for h_name, h_info in harnesses.items():
        v = install_harness(DEFAULT_SANDBOX_NAME, h_name, h_info)
        if v:
            h_info["version"] = v
            updated = True

    # Save template snapshot
    save_as_template(DEFAULT_SANDBOX_NAME, DEFAULT_TEMPLATE_TAG)

    # Save updated versions to JSON
    if updated:
        save_harness_config(harnesses, CONFIG_FILE)

    # Always cleanup builder container
    remove_sandbox(DEFAULT_SANDBOX_NAME)

    print("\n======================================================================")
    print("HARNESS CONFIGURATION COMPLETE")
    print(f"Base template ready: {DEFAULT_TEMPLATE_TAG}")
    print("======================================================================\n")


if __name__ == "__main__":
    main()
