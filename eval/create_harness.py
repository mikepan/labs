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
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from eval.common import setup_logger, run_cmd, load_harnesses_config
from eval.config import (
    DEFAULT_BUILDER_SANDBOX_NAME,
    DEFAULT_TEMPLATE_TAG,
    HARNESSES_CONFIG_FILE,
)
from eval.sandbox import SandboxClient

logger = setup_logger("create_harness")


def ensure_sbx_policy() -> None:
    """Ensure global network policy is initialized in sbx."""
    res = run_cmd("sbx", "policy", "init", "allow-all")
    if res.returncode == 0 and res.stdout.strip():
        logger.debug("[sbx policy] %s", res.stdout.strip())


def save_harness_config(config: dict[str, Any], config_path: str = HARNESSES_CONFIG_FILE) -> None:
    """Write updated configuration back to JSON file."""
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
        f.write("\n")
    logger.info("Updated %s with detected harness versions.", config_path)



def install_harness(
    sandbox: SandboxClient,
    harness_name: str,
    harness_info: dict[str, Any],
) -> str | None:
    """Install a specific harness in the sandbox and return its detected version."""
    norm_name = harness_name.lower().strip()
    logger.info("Installing harness: %s", harness_name)

    install_cmd = harness_info.get("install_cmd")
    version_cmd = harness_info.get("version_cmd")

    if not install_cmd:
        if "pi" in norm_name:
            install_cmd = (
                "(curl -fsSL https://pi.dev/install.sh | bash || "
                "curl -fsSL https://pi.dev/install.sh | sh || "
                "npm install -g @earendil-works/pi-coding-agent) && "
                "mkdir -p ~/.local/bin && (ln -sf ~/.pi/bin/pi ~/.local/bin/pi 2>/dev/null || true)"
            )
            version_cmd = "export PATH=$HOME/.pi/bin:$HOME/.local/bin:/usr/local/bin:$PATH; pi --version"
        elif "opencode" in norm_name:
            install_cmd = (
                "curl -fsSL https://opencode.ai/install | bash && "
                "mkdir -p ~/.local/bin && ln -sf ~/.opencode/bin/opencode ~/.local/bin/opencode 2>/dev/null || true"
            )
            version_cmd = "export PATH=$HOME/.opencode/bin:$HOME/.local/bin:$PATH; opencode --version"
        else:
            logger.warning("Unknown harness '%s' with no install_cmd specified. Skipping.", harness_name)
            return None

    if not version_cmd:
        version_cmd = f"export PATH=$HOME/.pi/bin:$HOME/.opencode/bin:$HOME/.local/bin:/usr/local/bin:$PATH; {norm_name} --version"

    logger.debug("Executing install command: %s", install_cmd)
    code, stdout, stderr = sandbox.exec(install_cmd)
    if code != 0:
        logger.error("Error installing %s (code %d):\n%s\n%s", harness_name, code, stderr, stdout)
        return None

    if stdout:
        logger.debug("[install output] %s", stdout)

    logger.debug("Checking version via: %s", version_cmd)
    code, v_stdout, v_stderr = sandbox.exec(version_cmd)
    if code == 0 and v_stdout:
        version_str = v_stdout.splitlines()[-1].strip()
        logger.info("✓ %s successfully installed! Detected version: %s", harness_name, version_str)
        return version_str
    else:
        logger.warning("Could not determine version for %s: %s %s", harness_name, v_stderr, v_stdout)
        return None


def save_as_template(sandbox: SandboxClient, template_tag: str) -> bool:
    """Snapshot the sandbox image and save it as a reusable template."""
    logger.info("Stopping sandbox '%s' before snapshotting...", sandbox.name)
    sandbox.stop()

    logger.info("Saving sandbox '%s' snapshot to template tag '%s'...", sandbox.name, template_tag)
    res = subprocess.run(["sbx", "template", "save", sandbox.name, template_tag], capture_output=True, text=True)
    if res.returncode == 0:
        logger.info("✓ Successfully saved template: %s", template_tag)
        return True

    # Try with y input if prompted
    res = subprocess.run(
        ["sbx", "template", "save", sandbox.name, template_tag],
        input="y\n",
        capture_output=True,
        text=True,
    )
    if res.returncode == 0:
        logger.info("✓ Successfully saved template: %s", template_tag)
        return True
    logger.warning("Failed to save template '%s': %s\n%s", template_tag, res.stderr, res.stdout)
    return False


def install_evaluation_dependencies(sandbox: SandboxClient) -> None:
    """Install core evaluation dependencies (git, python3, pip, nodejs, npm, openjdk, ktlint, langdetect, html5lib, etc.)."""
    logger.info("Installing base evaluation dependencies (git, python, nodejs, openjdk, ktlint, langdetect, etc.)...")

    setup_script = """
    set -e
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        sudo apt-get update -qq && sudo apt-get install -y -qq git python3 python3-pip python3-venv curl jq nodejs npm openjdk-21-jre-headless || sudo apt-get install -y -qq default-jre-headless || true
    elif command -v apk >/dev/null 2>&1; then
        apk update && apk add --no-cache git python3 py3-pip curl jq nodejs npm openjdk17-jre
    fi

    # Install ktlint
    if ! command -v ktlint >/dev/null 2>&1; then
        sudo curl -sSL https://github.com/pinterest/ktlint/releases/download/1.5.0/ktlint -o /usr/local/bin/ktlint
        sudo chmod a+x /usr/local/bin/ktlint
    fi

    # Install Python evaluation packages
    python3 -m pip install --upgrade --quiet --break-system-packages pip 2>/dev/null || true
    python3 -m pip install --quiet --break-system-packages langdetect html5lib beautifulsoup4 pillow numpy 2>/dev/null || \
    python3 -m pip install --quiet langdetect html5lib beautifulsoup4 pillow numpy || true
    """

    code, stdout, stderr = sandbox.exec(setup_script)
    if code != 0:
        logger.warning("Issue installing base eval packages (code %d):\n%s\n%s", code, stderr, stdout)
    else:
        logger.info("✓ System evaluation tools, OpenJDK, ktlint, and Python packages installed.")

    # Validation
    val_cmd = 'git --version && python3 --version && java -version && ktlint --version && python3 -c "import langdetect, html5lib, PIL, numpy; print(\'✓ Evaluation Python modules validated (langdetect, html5lib, pillow, numpy).\')"'
    code, v_out, _ = sandbox.exec(val_cmd)
    if code == 0 and v_out:
        for line in v_out.splitlines():
            logger.debug("Validation: %s", line)
    else:
        logger.debug("Validation output: %s", v_out)


def main():
    ensure_sbx_policy()

    harnesses = load_harnesses_config()
    logger.info("Loaded %d harness(es) from %s:", len(harnesses), HARNESSES_CONFIG_FILE)
    for h in harnesses:
        logger.info("  - %s", h)

    # Create fresh builder sandbox with ephemeral empty workspace
    tmp_ws = tempfile.mkdtemp(prefix="sbx_builder_ws_")
    sandbox = SandboxClient(name=DEFAULT_BUILDER_SANDBOX_NAME)
    sandbox.remove()

    logger.info("Creating sandbox '%s' with isolated workspace: %s...", sandbox.name, tmp_ws)
    create_res = subprocess.run(
        ["sbx", "create", "--name", sandbox.name, "shell", tmp_ws],
        capture_output=True,
        text=True,
    )
    if create_res.returncode != 0:
        logger.error("Error creating sandbox:\n%s\n%s", create_res.stderr, create_res.stdout)
        shutil.rmtree(tmp_ws, ignore_errors=True)
        sys.exit(1)
    logger.info("✓ Sandbox '%s' created.", sandbox.name)

    try:
        # 1. Install base evaluation dependencies
        install_evaluation_dependencies(sandbox)

        # 2. Install each agent harness
        updated = False
        for h_name, h_info in harnesses.items():
            v = install_harness(sandbox, h_name, h_info)
            if v:
                h_info["version"] = v
                updated = True

        # Save template snapshot
        save_as_template(sandbox, DEFAULT_TEMPLATE_TAG)

        # Save updated versions to JSON
        if updated:
            save_harness_config(harnesses)
    finally:
        # Always cleanup builder container and temp workspace
        sandbox.remove()
        shutil.rmtree(tmp_ws, ignore_errors=True)

    logger.info("HARNESS CONFIGURATION COMPLETE. Base template ready: %s", DEFAULT_TEMPLATE_TAG)


if __name__ == "__main__":
    main()
