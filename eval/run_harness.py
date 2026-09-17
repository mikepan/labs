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
import concurrent.futures
import copy
import importlib.util
import json
import logging
import os
from pathlib import Path
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from eval.common import get_harness_logger, setup_logger, load_harnesses_config, load_json_config, get_available_tests
from eval.config import (
    API_BASE_URL,
    DEFAULT_HINT_SCORE_FACTOR,
    DEFAULT_IDLE_TIMEOUT_MINUTES,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_MAX_STEP_TIMEOUT_MINUTES,
    MODELS_CONFIG_FILE,
    RESULTS_DIR,
    TESTS_DIR,
)
from eval.drivers import HarnessDriver, get_driver
from eval.results import get_vllm_model_info, save_evaluation_results
from eval.sandbox import SandboxClient
from eval.tool_eval import run_tool_eval_benchmark, TOOL_EVAL_TEST_KEY
from eval.trace import StepTrace, TurnData


logger = setup_logger("run_harness")


# =====================================================================
# Test Loading & Framework In-Memory Bundling
# =====================================================================

def _get_runner_bundle() -> str:
    """Build unified in-memory test framework module bundle."""
    fw_dir = TESTS_DIR / "framework"
    spec_code = (fw_dir / "spec.py").read_text(encoding="utf-8")
    assertions_code = (fw_dir / "assertions.py").read_text(encoding="utf-8")
    runner_code = (fw_dir / "runner.py").read_text(encoding="utf-8")
    return f"""import json, os, re, subprocess, sys, time, types
eval_mod = types.ModuleType('eval')
eval_cfg = types.ModuleType('eval.config')
eval_cfg.DEFAULT_STEP_POINT = 1
eval_mod.config = eval_cfg
sys.modules['eval'] = eval_mod
sys.modules['eval.config'] = eval_cfg

tf = types.ModuleType('tests.framework')
sys.modules['tests'] = types.ModuleType('tests')
sys.modules['tests'].framework = tf
sys.modules['tests.framework'] = tf
sys.modules['tests.framework.spec'] = tf
sys.modules['tests.framework.assertions'] = tf
sys.modules['tests.framework.runner'] = tf
exec({spec_code!r}, tf.__dict__)
exec({assertions_code!r}, tf.__dict__)
exec({runner_code!r}, tf.__dict__)
"""


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
    """Execute step assertions in sandbox RAM via stdin with zero disk footprint."""
    runner_bundle = _get_runner_bundle()
    test_code = Path(test_run_file).read_text(encoding="utf-8")

    eval_script = f"""{runner_bundle}
test_mod = types.ModuleType('test_module')
exec({test_code!r}, test_mod.__dict__)
step = test_mod.TEST.steps[{step_idx}]
res = sys.modules['tests.framework'].evaluate_step(step, {workspace_dir!r})
out = {{
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
print("__JSON_START__" + json.dumps(out) + "__JSON_END__")
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

def _log_turn(turn: TurnData, log_target: Any = None) -> None:
    """Log reasoning, tool calls, and response from a turn."""
    l = log_target or logger
    if turn.reasoning:
        l.debug("[Reasoning]: %s...", turn.reasoning[-1].strip()[:140])
    for tc in turn.tool_calls:
        l.info("  [Tool Call]: %s", tc.tool)
    if turn.text:
        l.debug("[Response]: %s...", turn.text[-1].strip()[:140])


def run_test_suite_on_agent(
    model_name: str,
    test_specs: list[tuple[str, Any]],
    driver: HarnessDriver,
    sandbox: SandboxClient,
    reasoning_effort: str | None = None,
    harness_name: str | None = None,
    llm_base_url: str = DEFAULT_LLM_BASE_URL,
) -> dict[str, Any]:
    """Execute all test specs sequentially against an agent harness."""
    h_logger = get_harness_logger(harness_name) if harness_name else logger
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
        h_logger.info("RUNNING TEST: %s (%d steps)", test_id, len(test_obj.steps))

        # Setup workspace and start agent
        test_ws = "/home/agent/workspace"
        sandbox.setup_test_workspace(test_ws, test_obj.setup)

        # Upload test data assets in a single atomic tar batch (excluding test definition run.py)
        test_dir = Path(test_path).parent
        sandbox.upload_tree(
            test_dir,
            test_ws,
            exclude={"run.py"},
        )

        # Commit initial test data assets so git change tracking starts with a clean baseline
        sandbox.exec(f"cd {test_ws} && git add -A && git commit --allow-empty -m 'feat: import project assets and resources'")

        active_model = driver.start(sandbox, test_ws, model_name, llm_base_url, reasoning_effort=reasoning_effort)
        session_id = driver.create_session(sandbox)

        test_start = time.time()
        step_traces: list[dict] = []
        passed_steps = 0
        max_score = sum(s.point for s in test_obj.steps)
        earned_score = 0
        total_tokens_in = 0
        total_tokens_out = 0

        vllm_info = get_vllm_model_info(base_url=llm_base_url)
        meta_dict = vllm_info.get("meta") or {} if vllm_info else {}
        max_ctx = int(vllm_info.get("max_model_len") or meta_dict.get("n_ctx") or 0) if vllm_info else 0

        for idx, step in enumerate(test_obj.steps):
            step_name = step.name or f"Step {idx + 1}"
            step_point = step.point
            step_timeout_minutes = step.timeout_minutes if step.timeout_minutes is not None else DEFAULT_MAX_STEP_TIMEOUT_MINUTES
            step_timeout_seconds = int(step_timeout_minutes * 60)
            step_idle_seconds = int(DEFAULT_IDLE_TIMEOUT_MINUTES * 60)
            h_logger.info("--- [Step %d/%d] %s (point=%d, max_timeout=%s min, idle_timeout=%s min) ---",
                          idx + 1, len(test_obj.steps), step_name, step_point, step_timeout_minutes, DEFAULT_IDLE_TIMEOUT_MINUTES)
            h_logger.debug("Prompt: %s...", step.prompt.strip()[:100])

            step_t0 = time.time()
            step_start_iso = datetime.fromtimestamp(step_t0, timezone.utc).isoformat()

            # Track message count before sending so we can recover partial traces on failure
            prev_msgs = driver.get_all_messages(sandbox, session_id)
            prev_msg_count = len(prev_msgs) if prev_msgs else 0

            # Send prompt through the driver — returns normalized TurnData
            try:
                turn = driver.send_prompt(
                    sandbox,
                    session_id,
                    step.prompt,
                    active_model,
                    timeout=step_timeout_seconds,
                    idle_timeout=step_idle_seconds,
                    reasoning_effort=reasoning_effort,
                )
            except Exception as e:
                step_elapsed = round(time.time() - step_t0, 2)
                step_end_iso = datetime.now(timezone.utc).isoformat()
                is_timeout = isinstance(e, TimeoutError) or "timeout" in str(e).lower() or "timed out" in str(e).lower()
                fail_reason = "TIMEOUT" if is_timeout else "ERROR"
                if is_timeout:
                    err_str = str(e)
                    if "stalled" in err_str.lower() or "idle" in err_str.lower() or "activity" in err_str.lower():
                        friendly_msg = err_str
                    else:
                        friendly_msg = f"Step execution exceeded max ceiling of {step_timeout_minutes} min ({step_elapsed}s elapsed)"
                else:
                    raw_err = str(e)
                    if "Traceback (most recent call last):" in raw_err:
                        raw_err = raw_err.strip().splitlines()[-1]
                    friendly_msg = f"Driver error: {raw_err}"

                h_logger.warning("  ✗ Step %d %s (0/%d pts) (%.2fs): %s",
                                 idx + 1, fail_reason, step_point, step_elapsed, friendly_msg)

                # Recover partial messages from the session to capture what the agent did
                partial_messages: list[dict] = []
                partial_tool_calls: list[dict] = []
                partial_events: list[dict] = []
                partial_reasoning: list[str] = []
                partial_text: list[str] = []
                partial_tokens_in: int = 0
                partial_tokens_out: int = 0
                partial_peak_ctx: int = 0
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
                            partial_tokens_in = partial_turn.tokens_in
                            partial_tokens_out = partial_turn.tokens_out
                            partial_peak_ctx = partial_turn.peak_context_tokens
                            total_tokens_in += partial_turn.tokens_in
                            total_tokens_out += partial_turn.tokens_out
                        n_tool_calls = len(partial_tool_calls)
                        h_logger.warning("    Recovered %d messages (%d tool calls) from session before %s",
                                         len(partial_messages), n_tool_calls, fail_reason.lower())
                        # Log the tool calls so the user can see the loop
                        for tc in partial_tool_calls:
                            h_logger.warning("    - [Tool Call]: %s", tc.get("tool", "unknown"))
                except Exception as recover_err:
                    h_logger.debug("    Could not recover partial messages: %s", recover_err)

                # Step context usage
                step_context_used_pct = round((partial_peak_ctx / max_ctx) * 100.0, 2) if max_ctx > 0 else 0.0

                step_traces.append(StepTrace(
                    step_index=idx,
                    step_name=step_name,
                    prompt=step.prompt,
                    point=step_point,
                    earned_score=0,
                    max_score=step_point,
                    start_time=step_start_iso,
                    end_time=step_end_iso,
                    tokens_in=partial_tokens_in,
                    tokens_out=partial_tokens_out,
                    peak_context_tokens=partial_peak_ctx,
                    context_used_pct=step_context_used_pct,
                    used_hint=False,
                    events=partial_events,
                    evaluation={
                        "passed": False,
                        "score": 0,
                        "point": step_point,
                        "check_results": [{
                            "passed": False,
                            "message": f"Step aborted due to {fail_reason}: {friendly_msg}",
                            "details": None,
                        }],
                    },
                    duration_seconds=step_elapsed,
                ).to_dict())
                continue

            _log_turn(turn, log_target=h_logger)

            total_tokens_in += turn.tokens_in
            total_tokens_out += turn.tokens_out

            # Evaluate step assertions in sandbox
            eval_res = evaluate_step_in_sandbox(sandbox, test_path, idx, test_ws)

            passed = eval_res.get("passed", False)
            used_hint = False

            # If check fails and step defines a hint, supply hint and grant retry in the same session
            if not passed and step.hint:
                h_logger.info("  ℹ Step %d failed initial check. Supplying hint and granting retry...", idx + 1)
                h_logger.debug("Hint: %s...", step.hint.strip()[:100])
                try:
                    hint_turn = driver.send_prompt(
                        sandbox,
                        session_id,
                        step.hint,
                        active_model,
                        timeout=step_timeout_seconds,
                        idle_timeout=step_idle_seconds,
                        reasoning_effort=reasoning_effort,
                    )
                    used_hint = True
                    total_tokens_in += hint_turn.tokens_in
                    total_tokens_out += hint_turn.tokens_out
                    turn.tokens_in += hint_turn.tokens_in
                    turn.tokens_out += hint_turn.tokens_out
                    turn.peak_context_tokens = max(turn.peak_context_tokens, hint_turn.peak_context_tokens)

                    # Inject hint as a regular user prompt into the chronological events stream
                    hint_ts = datetime.now(timezone.utc).isoformat()
                    turn.events.append({
                        "type": "user",
                        "timestamp": hint_ts,
                        "content": step.hint,
                    })
                    turn.events.extend(hint_turn.events)
                    turn.reasoning.extend(hint_turn.reasoning)
                    turn.text.extend(hint_turn.text)
                    turn.tool_calls.extend(hint_turn.tool_calls)
                    turn.raw_messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": step.hint}],
                    })
                    turn.raw_messages.extend(hint_turn.raw_messages)

                    _log_turn(hint_turn, log_target=h_logger)

                    # Re-evaluate step assertions after hint
                    eval_res = evaluate_step_in_sandbox(sandbox, test_path, idx, test_ws)
                    passed = eval_res.get("passed", False)
                except Exception as e_hint:
                    h_logger.warning("  ✗ Hint attempt encountered driver error: %s", e_hint)

            step_elapsed = round(time.time() - step_t0, 2)
            step_end_iso = datetime.now(timezone.utc).isoformat()

            if passed:
                passed_steps += 1
                if used_hint:
                    step_score = round(step_point * DEFAULT_HINT_SCORE_FACTOR, 2)
                    h_logger.info("  ✓ Step %d PASSED WITH HINT (+%s/%d pts) (%.2fs)", idx + 1, step_score, step_point, step_elapsed)
                else:
                    step_score = step_point
                    h_logger.info("  ✓ Step %d PASSED (+%d/%d pts) (%.2fs)", idx + 1, step_score, step_point, step_elapsed)
            else:
                step_score = 0
                h_logger.warning("  ✗ Step %d FAILED (0/%d pts) (%.2fs)", idx + 1, step_point, step_elapsed)
                for cr in eval_res.get("check_results", []):
                    if not cr.get("passed"):
                        h_logger.warning("    - Failure: %s", cr.get("message"))

            earned_score += step_score

            # Step context usage
            step_peak_ctx = turn.peak_context_tokens
            step_context_used_pct = round((step_peak_ctx / max_ctx) * 100.0, 2) if max_ctx > 0 else 0.0

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
                peak_context_tokens=step_peak_ctx,
                context_used_pct=step_context_used_pct,
                used_hint=used_hint,
                events=turn.events,
                evaluation=eval_res,
                duration_seconds=step_elapsed,
            ).to_dict())

        test_duration = round(time.time() - test_start, 2)

        # Context usage (peak single turn across all steps)
        test_peak_ctx = max([st.get("peak_context_tokens", 0) for st in step_traces], default=0)
        test_context_used_pct = round((test_peak_ctx / max_ctx) * 100.0, 2) if max_ctx > 0 else 0.0

        test_results_summary[test_id] = {
            "name": test_obj.name or test_id,
            "earned_score": earned_score,
            "max_score": max_score,
            "run_time_sec": test_duration,
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
            "peak_context_tokens": test_peak_ctx,
            "context_used_pct": test_context_used_pct,
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

        completion_rate = round((earned_score / max_score) * 100.0, 1) if max_score > 0 else 100.0
        suite_trace["tests"][test_id] = {
            "name": test_obj.name or test_id,
            "completion_rate": completion_rate,
            "earned_score": earned_score,
            "max_score": max_score,
            "passed_steps": passed_steps,
            "total_steps": len(test_obj.steps),
            "duration_seconds": test_duration,
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
            "peak_context_tokens": test_peak_ctx,
            "context_used_pct": test_context_used_pct,
            "session_id": session_id,
            "session_info": session_info,
            "steps": step_traces,
        }

        h_logger.info("✓ Test '%s' complete: score %d/%d (%.1f%%) in %.2fs",
                      test_id, earned_score, max_score, completion_rate, test_duration)

    suite_trace["end_time"] = datetime.now(timezone.utc).isoformat()
    return {
        "eval_id": eval_id,
        "suite_trace": suite_trace,
        "test_results_summary": test_results_summary,
        "stage_dir": stage_dir,
    }


def main():
    parser = argparse.ArgumentParser(description="Drive evaluation harness inside isolated Docker sandbox")
    parser.add_argument("--model", required=True, help="Model name (e.g. qwen/Qwen3.6-27B-FP8)")
    parser.add_argument("--test", default="all", help="Specific test to run (e.g. 'test0', 'tool-eval-bench', or 'all', default: all)")
    parser.add_argument("--harness", default="all", help="Harness name (e.g. 'pi', 'opencode', or 'all', default: all)")
    parser.add_argument("--reasoning", default=None, help="Reasoning effort override (e.g. 'low', 'medium', 'xhigh', 'off')")
    parser.add_argument("--base-url", default=None, help=f"LLM base URL override (default: {DEFAULT_LLM_BASE_URL})")
    parser.add_argument("--memory-gb", type=float, default=None, help="Measured runtime GPU memory in GB")
    parser.add_argument("--v", dest="verbose", action="store_true", help="Verbose debug logging")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    harnesses_cfg = load_harnesses_config()
    target_harnesses = list(harnesses_cfg.keys()) if args.harness == "all" else [args.harness]

    models_cfg = load_json_config(MODELS_CONFIG_FILE) if os.path.exists(MODELS_CONFIG_FILE) else {}
    reasoning_effort = args.reasoning if args.reasoning is not None else models_cfg.get(args.model, {}).get("reasoning_effort")
    llm_base_url = args.base_url if args.base_url else DEFAULT_LLM_BASE_URL

    should_run_tool_eval = args.test in ("all", TOOL_EVAL_TEST_KEY)
    tool_eval_output = None

    # 1. Run tool-eval-bench first before any sandbox harness execution
    if should_run_tool_eval:
        tool_eval_output = run_tool_eval_benchmark(
            base_url=llm_base_url,
            reasoning_effort=reasoning_effort,
            verbose=args.verbose,
        )

    # If only running tool-eval-bench, save results standalone without sandbox harness
    if args.test == TOOL_EVAL_TEST_KEY:
        for harness_name in target_harnesses:
            harness_version = harnesses_cfg.get(harness_name, {}).get("version", "unknown")
            eval_id = str(uuid.uuid4())
            suite_trace = {
                "eval_id": eval_id,
                "model": args.model,
                "start_time": datetime.now(timezone.utc).isoformat(),
                "tests": {
                    TOOL_EVAL_TEST_KEY: tool_eval_output["trace"]
                } if tool_eval_output else {},
                "end_time": datetime.now(timezone.utc).isoformat(),
            }
            test_results_summary = {
                TOOL_EVAL_TEST_KEY: tool_eval_output["summary"]
            } if tool_eval_output else {}
            evaluation_output = {
                "eval_id": eval_id,
                "suite_trace": suite_trace,
                "test_results_summary": test_results_summary,
                "stage_dir": None,
            }
            save_evaluation_results(
                model_name=args.model,
                harness_name=harness_name,
                harness_version=harness_version,
                evaluation_output=evaluation_output,
                base_url=llm_base_url,
                memory_gb=args.memory_gb,
                reasoning_effort=reasoning_effort,
            )
        return

    # 2. Run agent harness test suites inside isolated sandbox
    target_tests = get_available_tests() if args.test == "all" else [args.test]
    test_specs = []
    for t_name in target_tests:
        if t_name == TOOL_EVAL_TEST_KEY:
            continue
        run_file = str(TESTS_DIR / t_name / "run.py") if not os.path.isfile(t_name) else t_name
        test_specs.append((run_file, load_test_spec(run_file)))

    def run_single_harness(harness_name: str) -> None:
        h_logger = get_harness_logger(harness_name)
        harness_version = harnesses_cfg.get(harness_name, {}).get("version", "unknown")
        driver = get_driver(harness_name)
        h_logger.info("==================================================")
        h_logger.info("STARTING %s HARNESS (%s) FOR MODEL: %s (Reasoning Effort: %s, Tests: %s)",
                      harness_name.upper(), type(driver).__name__, args.model, reasoning_effort,
                      [t[1].name for t in test_specs])
        h_logger.info("==================================================")

        sandbox = SandboxClient(name=f"workspace-runner-{harness_name}")
        sandbox.ensure()
        try:
            evaluation_output = run_test_suite_on_agent(
                model_name=args.model,
                test_specs=test_specs,
                driver=driver,
                sandbox=sandbox,
                reasoning_effort=reasoning_effort,
                harness_name=harness_name,
                llm_base_url=llm_base_url,
            )

            # Attach pre-computed tool-eval-bench results (deep copy for thread safety)
            if tool_eval_output:
                evaluation_output["test_results_summary"][TOOL_EVAL_TEST_KEY] = copy.deepcopy(tool_eval_output["summary"])
                evaluation_output["suite_trace"]["tests"][TOOL_EVAL_TEST_KEY] = copy.deepcopy(tool_eval_output["trace"])

            save_evaluation_results(
                model_name=args.model,
                harness_name=harness_name,
                harness_version=harness_version,
                evaluation_output=evaluation_output,
                base_url=llm_base_url,
                memory_gb=args.memory_gb,
                reasoning_effort=reasoning_effort,
            )
        finally:
            sandbox.remove()

    if len(target_harnesses) == 1:
        run_single_harness(target_harnesses[0])
    else:
        logger.info("Executing %d harnesses concurrently in parallel: %s", len(target_harnesses), target_harnesses)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_harnesses)) as executor:
            futures = [executor.submit(run_single_harness, h) for h in target_harnesses]
            for f in concurrent.futures.as_completed(futures):
                f.result()


if __name__ == "__main__":
    main()

