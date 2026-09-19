#!/usr/bin/env python3
"""
launch_eval.py - Minimal vLLM model evaluation runner and cluster orchestrator.

Orchestrates full evaluation pipeline across models, tests, and harnesses
with upfront validation of all parameters before starting any containers.

Usage:
    python3 eval/launch_eval.py [--model model_name] [--harness harness_name] [--test test_name] [--keep-alive] [--v]

"""

import argparse
import logging
import subprocess
import sys
from typing import Any

from eval.config import (
    API_BASE_URL,
    MODELS_CONFIG_FILE,
    REMOTE_HOST,
    REPO_ROOT,
)
from eval.common import (
    setup_logger,
    get_available_tests,
    load_harnesses_config,
    load_json_config,
    prevent_system_sleep,
)
from eval.launch_model import (
    ensure_model_running,
    stop_model,
)

from eval.results import calculate_model_memory_gb

logger = setup_logger("launch_eval")


def validate_arguments(
    model_arg: str,
    test_arg: str,
    harness_arg: str,
    models: dict[str, Any],
) -> tuple[list[str], str, str]:
    """Validate model, test, and harness arguments upfront before execution."""
    # 1. Validate Model (supports comma-separated list, exact match, 'all', or partial substring matching)
    if model_arg in (None, "all"):
        target_models = [m for m, cfg in models.items() if not cfg.get("disabled", False) and cfg.get("enabled", True) is not False]
        disabled_models = [m for m, cfg in models.items() if cfg.get("disabled", False) or cfg.get("enabled", True) is False]
        if disabled_models:
            logger.info("Skipping %d disabled model(s) for 'all': %s", len(disabled_models), disabled_models)
    elif "," in model_arg:
        items = [x.strip() for x in model_arg.split(",") if x.strip()]
        target_models = []
        for item in items:
            if item in models:
                if item not in target_models:
                    target_models.append(item)
            else:
                matched = [m for m in models.keys() if item.lower() in m.lower()]
                for m in matched:
                    if m not in target_models:
                        target_models.append(m)
        if not target_models:
            available_models = ", ".join(models.keys()) or "(none)"
            logger.error("No models matched in list '%s'. Available models: %s", model_arg, available_models)
            sys.exit(1)
    elif model_arg in models:
        target_models = [model_arg]
    else:
        # Match all models containing the search substring (case-insensitive)
        matched = [m for m in models.keys() if model_arg.lower() in m.lower()]
        if matched:
            target_models = matched
            logger.info("Matched %d model(s) for filter '%s': %s", len(target_models), model_arg, target_models)
        else:
            available_models = ", ".join(models.keys()) or "(none)"
            logger.error("Unknown model or filter '%s'. Available models: %s (or 'all')", model_arg, available_models)
            sys.exit(1)

    # 2. Validate Test
    available_tests = get_available_tests() + ["tool-eval-bench"]
    if test_arg != "all" and test_arg not in available_tests:
        matched_tests = [t for t in available_tests if test_arg.lower() in t.lower()]
        if len(matched_tests) >= 1:
            original_test_arg = test_arg
            test_arg = matched_tests[0]
            logger.info("Matched test filter '%s' -> '%s'", original_test_arg, test_arg)
        else:
            avail_str = ", ".join(available_tests) or "(none)"
            logger.error("Unknown test '%s'. Available tests: %s (or 'all')", test_arg, avail_str)
            sys.exit(1)

    # 3. Validate Harness
    available_harnesses = list(load_harnesses_config().keys())
    if harness_arg != "all":
        matched = [h for h in available_harnesses if harness_arg.lower() in h.lower() or h.lower() in harness_arg.lower()]
        if matched:
            harness_arg = matched[0]
        else:
            avail_str = ", ".join(available_harnesses) or "(none)"
            logger.error("Unknown harness '%s'. Available harnesses: %s (or 'all')", harness_arg, avail_str)
            sys.exit(1)

    return target_models, test_arg, harness_arg


def run_model_pipeline(
    model_name: str,
    model_config: dict[str, Any],
    host: str = REMOTE_HOST,
    base_url: str = API_BASE_URL,
    keep_alive: bool = False,
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
            verbose=verbose,
        )
        if not ok:
            return False

        memory_gb = calculate_model_memory_gb(host=host)

        logger.info("Executing Evaluation Harness for %s (test: %s, harness: %s, memory: %.1f GB)...",
                    model_name, test_name, harness, memory_gb)
        harness_script = REPO_ROOT / "eval" / "run_harness.py"
        cmd = [
            sys.executable,
            str(harness_script),
            "--model", model_name,
            "--test", test_name,
            "--harness", harness,
        ]
        if memory_gb > 0:
            cmd.extend(["--memory-gb", str(memory_gb)])
        if verbose:
            cmd.append("--v")
        res = subprocess.run(cmd)
        if res.returncode != 0:
            logger.error("Evaluation harness failed with exit code %d", res.returncode)
            return False

        success = True
    finally:
        if not keep_alive:
            stop_model(host=host)
        else:
            logger.info("Keep-alive enabled: leaving vLLM server running.")

    status_str = "SUCCESS" if success else "FAILED"
    logger.info("FINISHED PIPELINE FOR: %s (Status: %s)", model_name, status_str)
    return success


def main():
    prevent_system_sleep()
    models = load_json_config(MODELS_CONFIG_FILE)
    parser = argparse.ArgumentParser(description="Run vLLM model evaluation pipeline across models, tests, and harnesses.")
    parser.add_argument("--model", default="all", help="Model to evaluate (e.g. 'all' or specific model, default: all)")
    parser.add_argument("--harness", default="all", help="Harness to run (e.g. 'pi', 'opencode', or 'all', default: all)")
    parser.add_argument("--test", default="all", help="Test to run (e.g. 'test0' or 'all', default: all)")
    parser.add_argument(
        "--keep-alive",
        action="store_true",
        help="Keep model server running after evaluation completes (skips container teardown)",
    )
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
            keep_alive=args.keep_alive,
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
