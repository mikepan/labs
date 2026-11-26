#!/usr/bin/env python3
"""
launch_eval.py - Minimal vLLM model evaluation runner and cluster orchestrator.

Orchestrates full evaluation pipeline across models, tests, and harnesses
with upfront validation of all parameters before starting any containers.

Usage:
    uv run eval [--model model_name] [--harness harness_name] [--test test_name] [--keep-alive] [--v]

"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.config import (
    API_BASE_URL,
    REMOTE_HOST,
)
from eval.common import (
    setup_logger,
    get_available_tests,
    load_harnesses_config,
    load_models,
    model_launch_key,
    prevent_system_sleep,
    resolve_target_models,
)
from eval.launch_model import (
    ensure_model_running,
    stop_model,
)
from eval.run_harness import DIRECT_BENCHMARKS

from eval.results import calculate_model_memory_gb

logger = setup_logger("launch_eval")


def validate_arguments(
    model_arg: str,
    test_arg: str,
    harness_arg: str,
    models: dict[str, Any],
) -> tuple[list[str], str, str]:
    """Validate model, test, and harness arguments upfront before execution."""
    target_models = resolve_target_models(models, model_arg)
    if not target_models:
        available_models = ", ".join(models.keys()) or "(none)"
        logger.error("No models matched filter '%s'. Available models: %s", model_arg, available_models)
        sys.exit(1)


    # 1. Validate Test
    available_tests = get_available_tests() + list(DIRECT_BENCHMARKS)
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

    # 2. Validate Harness
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
    model_names: list[str],
    model_configs: dict[str, dict[str, Any]],
    host: str = REMOTE_HOST,
    base_url: str = API_BASE_URL,
    keep_alive: bool = False,
    verbose: bool = False,
    test_name: str = "all",
    harness: str = "all",
) -> list[str]:
    """Run one model launch across all of its effort aliases, returning failed aliases."""
    primary = model_names[0]
    efforts = [model_configs[name]["reasoning_effort"] for name in model_names]
    logger.info("STARTING PIPELINE FOR: %s (efforts: %s, harness: %s, test: %s)",
                primary, efforts, harness, test_name)

    is_remote = model_configs[primary].get("provider") == "openrouter"
    failed: list[str] = []
    try:
        if is_remote:
            logger.info("Remote provider: skipping local model launch for %s", primary)
            memory_gb = 0.0
        else:
            ok, _ = ensure_model_running(
                model_name=primary,
                model_config=model_configs[primary],
                host=host,
                base_url=base_url,
                verbose=verbose,
            )
            if not ok:
                return list(model_names)

            memory_gb = calculate_model_memory_gb(host=host)
        venv_python = REPO_ROOT / ".venv" / "bin" / "python3"

        for model_name in model_names:
            logger.info("Executing Evaluation Harness for %s (test: %s, harness: %s, memory: %.1f GB)...",
                        model_name, test_name, harness, memory_gb)
            cmd = [
                str(venv_python),
                "-m", "eval.run_harness",
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
                logger.error("Evaluation harness failed with exit code %d for %s", res.returncode, model_name)
                failed.append(model_name)
    finally:
        if not is_remote:
            if not keep_alive:
                stop_model(host=host)
            else:
                logger.info("Keep-alive enabled: leaving vLLM server running.")

    status_str = "SUCCESS" if not failed else "FAILED"
    logger.info("FINISHED PIPELINE FOR: %s (Status: %s)", primary, status_str)
    return failed


def main():
    prevent_system_sleep()
    models = load_models()
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

    failed_models: list[str] = []
    groups: dict[tuple, list[str]] = {}
    for model_name in target_models:
        groups.setdefault(model_launch_key(models[model_name]), []).append(model_name)

    for model_names in groups.values():
        failed_models.extend(
            run_model_pipeline(
                model_names,
                models,
                keep_alive=args.keep_alive,
                verbose=args.verbose,
                test_name=valid_test,
                harness=valid_harness,
            )
        )

    if failed_models:
        logger.error("Evaluation finished with %d failed model(s): %s", len(failed_models), failed_models)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
