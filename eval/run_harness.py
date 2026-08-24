#!/usr/bin/env python3
"""
run_harness.py - Drive agent harnesses inside an isolated Docker Sandbox to execute evaluation tasks.

Features:
  - Dynamically loads tests from tests/*/run.py
  - Provisions an isolated sandbox from eval-base-harness:latest
  - Delegates to pluggable HarnessDriver (OpenCode, pi.dev, etc.)
  - Generates self-contained execution trace in site/results/{eval_id}/
  - Appends / updates evaluations in site/results/benchmark-data.json

Usage:
    python3 eval/run_harness.py <model_name> [--test test0] [--harness opencode]
"""

import argparse
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from eval.common import setup_logger
from eval.config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_WORKER_SANDBOX_NAME,
    HARNESSES_CONFIG_FILE,
    RESULTS_DIR,
    TESTS_DIR,
)
from eval.drivers import HarnessDriver, get_driver
from eval.results import get_vllm_model_info, save_evaluation_results
from eval.sandbox import SandboxClient
from eval.trace import StepTrace, TurnData

logger = setup_logger("run_harness")


# =====================================================================
# Test Loading & Framework In-Memory Bundling
# =====================================================================

_FRAMEWORK_BUNDLE: tuple[str, str, str] | None = None


def _get_framework_bundle() -> tuple[str, str, str]:
    """Load framework spec, assertions, and runner source code for in-memory sandbox evaluation."""
    global _FRAMEWORK_BUNDLE
    if _FRAMEWORK_BUNDLE is None:
        fw_dir = TESTS_DIR / "framework"
        spec_code = (fw_dir / "spec.py").read_text(encoding="utf-8")
        assertions_code = (fw_dir / "assertions.py").read_text(encoding="utf-8")
        runner_code = (fw_dir / "runner.py").read_text(encoding="utf-8")
        _FRAMEWORK_BUNDLE = (spec_code, assertions_code, runner_code)
    return _FRAMEWORK_BUNDLE


def load_test_spec(test_name_or_path: str):
    """Load a TestSpec from a test directory or run.py file."""
    if os.path.isdir(test_name_or_path):
        target_file = os.path.join(test_name_or_path, "run.py")
    elif os.path.isfile(test_name_or_path):
        target_file = test_name_or_path
    else:
        candidate = str(TESTS_DIR / test_name_or_path / "run.py")
        if os.path.isfile(candidate):
            target_file = candidate
        else:
            raise FileNotFoundError(f"Cannot find test file for '{test_name_or_path}'")

    spec = importlib.util.spec_from_file_location("test_module", target_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec from {target_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "TEST"):
        return module.TEST
    raise AttributeError(f"Module {target_file} has no 'TEST' object defined.")


# =====================================================================
# Step Evaluation (runs test assertions inside sandbox in-memory)
# =====================================================================

def evaluate_step_in_sandbox(
    sandbox: SandboxClient,
    test_run_file: str,
    step_idx: int,
    workspace_dir: str,
) -> dict[str, Any]:
    """Execute step assertions directly inside the sandbox container in memory without disk persistence."""
    spec_code, assertions_code, runner_code = _get_framework_bundle()
    test_code = Path(test_run_file).read_text(encoding="utf-8")

    eval_script = f"""import json, sys, types
from pathlib import Path

t_mod = types.ModuleType('tests')
t_mod.__path__ = []
tf_mod = types.ModuleType('tests.framework')
tf_mod.__path__ = []

spec_mod = types.ModuleType('tests.framework.spec')
exec({spec_code!r}, spec_mod.__dict__)

assertions_mod = types.ModuleType('tests.framework.assertions')
exec({assertions_code!r}, assertions_mod.__dict__)

runner_mod = types.ModuleType('tests.framework.runner')
sys.modules['tests'] = t_mod
sys.modules['tests.framework'] = tf_mod
sys.modules['tests.framework.spec'] = spec_mod
sys.modules['tests.framework.assertions'] = assertions_mod
sys.modules['tests.framework.runner'] = runner_mod

exec({runner_code!r}, runner_mod.__dict__)

for mod in (spec_mod, assertions_mod, runner_mod):
    for k in getattr(mod, '__all__', []):
        setattr(tf_mod, k, getattr(mod, k))
t_mod.framework = tf_mod

test_mod = types.ModuleType('test_module')
test_mod.__file__ = '{workspace_dir}/run.py'
exec({test_code!r}, test_mod.__dict__)

test_obj = test_mod.TEST
step = test_obj.steps[{step_idx}]
res = runner_mod.evaluate_step(step, '{workspace_dir}')

output = {{
    "step_name": res.step_name,
    "passed": res.passed,
    "point": res.point,
    "score": res.score,
    "duration_seconds": res.duration_seconds,
    "check_results": [
        {{"passed": c.passed, "message": c.message, "details": c.details}}
        for c in res.check_results
    ]
}}
print("__JSON_START__" + json.dumps(output) + "__JSON_END__")
"""
    try:
        return sandbox.exec_python_json(eval_script, label=f"evaluate step {step_idx}")
    except RuntimeError as e:
        return {
            "step_name": f"Step {step_idx}",
            "passed": False,
            "point": 0,
            "score": 0,
            "duration_seconds": 0.0,
            "check_results": [{"passed": False, "message": str(e)}],
        }


# =====================================================================
# Test Suite Orchestrator
# =====================================================================

def _log_turn(turn: TurnData) -> None:
    """Log reasoning, tool calls, and response from a turn."""
    if turn.reasoning:
        logger.debug("[Reasoning]: %s...", turn.reasoning[-1].strip()[:140])
    for tc in turn.tool_calls:
        logger.info("  [Tool Call]: %s", tc.tool)
    if turn.text:
        logger.debug("[Response]: %s...", turn.text[-1].strip()[:140])


def run_test_suite_on_agent(
    model_name: str,
    test_specs: list[tuple[str, Any]],
    driver: HarnessDriver,
    sandbox: SandboxClient,
) -> dict[str, Any]:
    """Execute all test specs sequentially against an agent harness."""
    stage_dir = tempfile.mkdtemp(prefix="eval_artifacts_")
    eval_id = str(uuid.uuid4())
    suite_trace: dict[str, Any] = {
        "eval_id": eval_id,
        "model": model_name,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "tests": {},
    }
    test_results_summary: dict[str, Any] = {}

    for test_path, test_obj in test_specs:
        test_id = test_obj.name
        logger.info("RUNNING TEST: %s (%d steps)", test_id, len(test_obj.steps))

        # Setup workspace and start agent
        test_ws = f"/tmp/eval_{test_id}"
        sandbox.setup_test_workspace(test_ws, test_obj.setup)

        # Upload test data assets (excluding .py files and hidden directories)
        test_dir = Path(test_path).parent
        for asset_path in test_dir.iterdir():
            if asset_path.is_file() and not asset_path.name.endswith(".py") and not asset_path.name.startswith("."):
                remote_asset = f"{test_ws}/{asset_path.name}"
                logger.debug("Uploading test asset %s -> %s", asset_path.name, remote_asset)
                sandbox.upload_file(asset_path, remote_asset)

        active_model = driver.start(sandbox, test_ws, model_name, DEFAULT_LLM_BASE_URL)
        session_id = driver.create_session(sandbox)

        test_start = time.time()
        step_traces: list[dict] = []
        passed_steps = 0
        max_score = sum(s.point for s in test_obj.steps)
        earned_score = 0
        total_tokens_in = 0
        total_tokens_out = 0

        for idx, step in enumerate(test_obj.steps):
            step_name = step.name or f"Step {idx + 1}"
            step_point = step.point
            step_timeout = step.timeout
            logger.info("--- [Step %d/%d] %s (point=%d, timeout=%ds) ---", idx + 1, len(test_obj.steps), step_name, step_point, step_timeout)
            logger.debug("Prompt: %s...", step.prompt.strip()[:100])

            step_t0 = time.time()
            step_start_iso = datetime.fromtimestamp(step_t0, timezone.utc).isoformat()

            # Track message count before sending so we can recover partial traces on failure
            try:
                prev_msgs = driver.get_all_messages(sandbox, session_id)
                prev_msg_count = len(prev_msgs) if prev_msgs else 0
            except Exception:
                prev_msg_count = 0

            # Send prompt through the driver — returns normalized TurnData
            try:
                turn = driver.send_prompt(sandbox, session_id, step.prompt, active_model, timeout=step_timeout)
            except Exception as e:
                step_elapsed = round(time.time() - step_t0, 2)
                step_end_iso = datetime.now(timezone.utc).isoformat()
                error_msg = str(e)
                is_timeout = "timeout" in error_msg.lower() or "timed out" in error_msg.lower()
                fail_reason = "TIMEOUT" if is_timeout else "ERROR"
                logger.warning("  ✗ Step %d %s (0/%d pts) (%.2fs): %s",
                               idx + 1, fail_reason, step_point, step_elapsed, error_msg[:200])

                # Recover partial messages from the session to capture what the agent did
                partial_messages: list[dict] = []
                partial_tool_calls: list[dict] = []
                partial_events: list[dict] = []
                partial_reasoning: list[str] = []
                partial_text: list[str] = []
                try:
                    all_msgs = driver.get_all_messages(sandbox, session_id)
                    if all_msgs and len(all_msgs) > prev_msg_count:
                        partial_messages = all_msgs[prev_msg_count:]
                        # Parse partial messages using driver's _parse_turn if available
                        if hasattr(driver, '_parse_turn'):
                            partial_turn = driver._parse_turn(partial_messages, step_start_iso)
                            partial_tool_calls = [tc.to_dict() for tc in partial_turn.tool_calls]
                            partial_events = partial_turn.events
                            partial_reasoning = partial_turn.reasoning
                            partial_text = partial_turn.text
                            total_tokens_in += partial_turn.tokens_in
                            total_tokens_out += partial_turn.tokens_out
                        n_tool_calls = len(partial_tool_calls)
                        logger.warning("    Recovered %d messages (%d tool calls) from session before %s",
                                       len(partial_messages), n_tool_calls, fail_reason.lower())
                        # Log the tool calls so the user can see the loop
                        for tc in partial_tool_calls:
                            logger.warning("    - [Tool Call]: %s", tc.get("tool", "unknown"))
                except Exception as recover_err:
                    logger.debug("    Could not recover partial messages: %s", recover_err)

                # Build step trace with recovered partial data
                response_parts = partial_text or []
                response_parts.append(f"[{fail_reason}] {error_msg}")
                step_traces.append(StepTrace(
                    step_index=idx,
                    step_name=step_name,
                    prompt=step.prompt,
                    point=step_point,
                    earned_score=0,
                    max_score=step_point,
                    start_time=step_start_iso,
                    end_time=step_end_iso,
                    tokens_in=0,
                    tokens_out=0,
                    events=partial_events,
                    tool_calls=partial_tool_calls,
                    reasoning_blocks=partial_reasoning,
                    response_text="\n\n".join(response_parts),
                    messages=partial_messages,
                    evaluation={
                        "step_name": step_name,
                        "passed": False,
                        "point": step_point,
                        "score": 0,
                        "duration_seconds": step_elapsed,
                        "check_results": [{"passed": False, "message": f"Driver {fail_reason}: {error_msg}"}],
                    },
                    duration_seconds=step_elapsed,
                ).to_dict())
                continue

            step_elapsed = round(time.time() - step_t0, 2)
            step_end_iso = datetime.now(timezone.utc).isoformat()
            total_tokens_in += turn.tokens_in
            total_tokens_out += turn.tokens_out

            _log_turn(turn)

            # Evaluate step assertions in sandbox
            eval_res = evaluate_step_in_sandbox(sandbox, test_path, idx, test_ws)

            passed = eval_res.get("passed", False)
            step_score = eval_res.get("score", (step_point if passed else 0))
            earned_score += step_score

            if passed:
                passed_steps += 1
                logger.info("  ✓ Step %d PASSED (+%d/%d pts) (%.2fs)", idx + 1, step_score, step_point, step_elapsed)
            else:
                logger.warning("  ✗ Step %d FAILED (0/%d pts) (%.2fs)", idx + 1, step_point, step_elapsed)
                for cr in eval_res.get("check_results", []):
                    if not cr.get("passed"):
                        logger.warning("    - Failure: %s", cr.get("message"))

            step_traces.append(StepTrace(
                step_index=idx,
                step_name=step_name,
                prompt=step.prompt,
                point=step_point,
                earned_score=step_score,
                max_score=step_point,
                start_time=step_start_iso,
                end_time=step_end_iso,
                tokens_in=turn.tokens_in,
                tokens_out=turn.tokens_out,
                events=turn.events,
                tool_calls=[tc.to_dict() for tc in turn.tool_calls],
                reasoning_blocks=turn.reasoning,
                response_text="\n\n".join(turn.text),
                messages=turn.raw_messages,
                evaluation=eval_res,
                duration_seconds=step_elapsed,
            ).to_dict())

        test_duration = round(time.time() - test_start, 2)

        # Context usage
        vllm_info = get_vllm_model_info(base_url=DEFAULT_LLM_BASE_URL)
        max_ctx = int(vllm_info.get("max_model_len", 0)) if vllm_info else 0
        total_tokens = total_tokens_in + total_tokens_out
        context_used_pct = round((total_tokens / max_ctx) * 100.0, 2) if max_ctx > 0 else 0.0

        test_results_summary[test_id] = {
            "name": test_obj.description or test_id,
            "earned_score": earned_score,
            "max_score": max_score,
            "run_time_sec": test_duration,
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
            "context_used_pct": context_used_pct,
        }

        # Extract artifacts
        local_artifacts = os.path.join(stage_dir, test_id)
        sandbox.extract_artifacts(test_ws, local_artifacts)

        # Extract harness server log
        log_filename = driver.server_log_filename
        log_content = driver.get_server_log(sandbox)
        if log_content:
            with open(os.path.join(stage_dir, log_filename), "w", encoding="utf-8") as f:
                f.write(log_content)

        session_info = driver.get_session_info(sandbox, session_id)
        all_messages = driver.get_all_messages(sandbox, session_id)

        completion_rate = round((earned_score / max_score) * 100.0, 1) if max_score > 0 else 100.0
        suite_trace["tests"][test_id] = {
            "completion_rate": completion_rate,
            "earned_score": earned_score,
            "max_score": max_score,
            "passed_steps": passed_steps,
            "total_steps": len(test_obj.steps),
            "duration_seconds": test_duration,
            "session_id": session_id,
            "session_info": session_info,
            "steps": step_traces,
            "all_session_messages": all_messages,
        }

        logger.info("✓ Test '%s' complete: score %d/%d (%.1f%%) in %.2fs",
                     test_id, earned_score, max_score, completion_rate, test_duration)

    suite_trace["end_time"] = datetime.now(timezone.utc).isoformat()
    return {
        "eval_id": eval_id,
        "suite_trace": suite_trace,
        "test_results_summary": test_results_summary,
        "stage_dir": stage_dir,
    }


# =====================================================================
# Main Entry Point
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Drive evaluation harness inside isolated Docker sandbox")
    parser.add_argument("model", help="Model name (e.g. qwen/Qwen3.6-27B-FP8)")
    parser.add_argument("--test", default="test0", help="Specific test to run (e.g. 'test0' or 'all')")
    parser.add_argument("--harness", default="opencode", help="Harness name (default: opencode)")
    parser.add_argument("--v", dest="verbose", action="store_true", help="Verbose debug logging")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Determine harness version
    harness_version = "unknown"
    if os.path.exists(HARNESSES_CONFIG_FILE):
        try:
            with open(HARNESSES_CONFIG_FILE, "r", encoding="utf-8") as f:
                h_info = json.load(f)
                for k, v in h_info.items():
                    if args.harness.lower() in k.lower():
                        harness_version = v.get("version", harness_version)
        except Exception as e:
            logger.debug("Failed to read harness version from %s: %s", HARNESSES_CONFIG_FILE, e)

    # Discover tests
    test_specs = []
    if args.test == "all":
        for item in sorted(os.listdir(TESTS_DIR)):
            run_file = str(TESTS_DIR / item / "run.py")
            if os.path.isfile(run_file):
                test_specs.append((run_file, load_test_spec(run_file)))
    else:
        test_file = str(TESTS_DIR / args.test / "run.py")
        test_specs.append((test_file, load_test_spec(test_file)))

    # Select driver
    driver = get_driver(args.harness)
    logger.info("RUNNING %s HARNESS (%s) FOR MODEL: %s (Tests: %s)",
                args.harness.upper(), type(driver).__name__, args.model,
                [t[1].name for t in test_specs])

    # Provision sandbox
    sandbox = SandboxClient()
    sandbox.ensure()
    try:
        evaluation_output = run_test_suite_on_agent(
            model_name=args.model,
            test_specs=test_specs,
            driver=driver,
            sandbox=sandbox,
        )

        save_evaluation_results(
            model_name=args.model,
            harness_name=args.harness,
            harness_version=harness_version,
            evaluation_output=evaluation_output,
            base_url=DEFAULT_LLM_BASE_URL,
        )
    finally:
        sandbox.remove()


if __name__ == "__main__":
    main()
