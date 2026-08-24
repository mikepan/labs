#!/usr/bin/env python3
"""
launch_eval.py - Minimal vLLM model evaluation runner and cluster orchestrator.

Orchestrates full evaluation pipeline across models, tests, and harnesses
with upfront validation of all parameters before starting any containers.

Usage:
    python3 eval/launch_eval.py [--model model_name] [--harness harness_name] [--test test_name] [--fast] [--v]
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from typing import Any

from eval.config import (
    API_BASE_URL,
    HARNESSES_CONFIG_FILE,
    REMOTE_HOST,
    REPO_ROOT,
    TESTS_DIR,
)
from eval.common import setup_logger
from eval.drivers import get_driver
from eval.launch_model import (
    ensure_model_running,
    load_models,
    stop_model,
)

logger = setup_logger("launch_eval")


def get_available_tests() -> list[str]:
    """Discover all valid test suites containing run.py under TESTS_DIR."""
    if not TESTS_DIR.exists():
        return []
    return [
        item for item in sorted(os.listdir(TESTS_DIR))
        if (TESTS_DIR / item / "run.py").is_file()
    ]


def get_available_harnesses() -> list[str]:
    """Retrieve all configured harness names from harnesses.json."""
    if HARNESSES_CONFIG_FILE.exists():
        try:
            with open(HARNESSES_CONFIG_FILE, "r", encoding="utf-8") as f:
                return list(json.load(f).keys())
        except Exception as e:
            logger.debug("Failed to read harnesses: %s", e)
    return ["pi", "opencode cli"]


def validate_arguments(
    model_arg: str,
    test_arg: str,
    harness_arg: str,
    models: dict[str, Any],
) -> tuple[list[str], str, str]:
    """Validate model, test, and harness arguments upfront before execution."""
    # 1. Validate Model
    if model_arg in (None, "all"):
        target_models = list(models.keys())
    elif model_arg in models:
        target_models = [model_arg]
    else:
        available_models = ", ".join(models.keys()) or "(none)"
        logger.error("Unknown model '%s'. Available models: %s (or 'all')", model_arg, available_models)
        sys.exit(1)

    # 2. Validate Test
    available_tests = get_available_tests()
    if test_arg != "all" and test_arg not in available_tests:
        avail_str = ", ".join(available_tests) or "(none)"
        logger.error("Unknown test '%s'. Available tests: %s (or 'all')", test_arg, avail_str)
        sys.exit(1)

    # 3. Validate Harness
    available_harnesses = get_available_harnesses()
    if harness_arg != "all":
        harness_match = None
        for h in available_harnesses:
            if harness_arg.lower() in h.lower() or h.lower() in harness_arg.lower():
                harness_match = h
                break
        if not harness_match:
            try:
                get_driver(harness_arg)
            except ValueError:
                avail_str = ", ".join(available_harnesses) or "(none)"
                logger.error("Unknown harness '%s'. Available harnesses: %s (or 'all')", harness_arg, avail_str)
                sys.exit(1)

    return target_models, test_arg, harness_arg


def run_model_pipeline(
    model_name: str,
    model_config: dict[str, Any],
    host: str = REMOTE_HOST,
    base_url: str = API_BASE_URL,
    fast: bool = False,
    verbose: bool = False,
    test_name: str = "all",
    harness: str = "all",
) -> bool:
    """Run full lifecycle: ensure model running -> execute harness -> teardown."""
    logger.info("STARTING PIPELINE FOR: %s (harness: %s, test: %s)", model_name, harness, test_name)

    success = False
    try:
        ok, weight_name = ensure_model_running(
            model_name=model_name,
            model_config=model_config,
            host=host,
            base_url=base_url,
            fast=fast,
            verbose=verbose,
        )
        if not ok:
            return False

        logger.info("Executing Evaluation Harness for %s (test: %s, harness: %s)...", model_name, test_name, harness)
        harness_script = REPO_ROOT / "eval" / "run_harness.py"
        cmd = [sys.executable, str(harness_script), "--model", model_name, "--test", test_name, "--harness", harness]
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
    parser = argparse.ArgumentParser(description="Run vLLM model evaluation pipeline across models, tests, and harnesses.")
    parser.add_argument("--model", default="all", help="Model to evaluate (e.g. 'all' or specific model, default: all)")
    parser.add_argument("--harness", default="all", help="Harness to run (e.g. 'pi', 'opencode', or 'all', default: all)")
    parser.add_argument("--test", default="all", help="Test to run (e.g. 'test0' or 'all', default: all)")
    parser.add_argument("--fast", action="store_true", help="Fast mode: skip launch and teardown")
    parser.add_argument("--v", dest="verbose", action="store_true", help="Verbose log streaming")

    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Perform upfront validation on model, test, and harness
    target_models, valid_test, valid_harness = validate_arguments(
        model_arg=args.model,
        test_arg=args.test,
        harness_arg=args.harness,
        models=models,
    )

    logger.info("Validated configuration: %d model(s) %s, test '%s', harness '%s'",
                len(target_models), target_models, valid_test, valid_harness)

    for model_name in target_models:
        ok = run_model_pipeline(
            model_name,
            models[model_name],
            fast=args.fast,
            verbose=args.verbose,
            test_name=valid_test,
            harness=valid_harness,
        )
        if not ok:
            logger.error("Pipeline stopped on failure for model: %s", model_name)
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
