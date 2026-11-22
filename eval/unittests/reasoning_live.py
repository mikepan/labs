"""
Live reasoning-effort matrix test.

Integration test that proves each configured model and harness actually honors
the configured thinking effort. It runs two prompts per model/harness and samples
each prompt five times, then reports min/mean/max reasoning length.

This module is deliberately not named ``test_*`` so it is excluded from the
default discovery run. The ``full`` mode of
``eval.unittests.run`` adds it explicitly:

    uv run test full
    uv run test full --models all --samples 5
"""

from __future__ import annotations

import statistics
import unittest
from typing import Any

from eval.common import (
    load_harnesses_config,
    load_models,
    model_launch_key,
    resolve_target_models,
    setup_logger,
)
from eval.config import (
    API_BASE_URL,
    DEFAULT_IDLE_TIMEOUT_MINUTES,
    DEFAULT_MAX_STEP_TIMEOUT_MINUTES,
)
from eval.drivers import get_driver
from eval.launch_model import ensure_model_running, parse_model_config, stop_model
from eval.results import get_vllm_model_info
from eval.sandbox import SandboxClient, ensure_sandbox_policy_isolated

logger = setup_logger("reasoning-live")

__all__ = ["TestReasoningEffortLive", "configure"]

PROMPTS = (
    "A train travels 120 km at 80 km/h, then 90 km at 60 km/h. "
    "What is the average speed for the whole journey? Show your reasoning, then give the final answer.",
    "A shop starts with 48 apples. It sells 1/4 of them in the morning and 12 in the afternoon. "
    "How many apples are left? Show your reasoning, then give the final answer.",
)

LEVEL_ORDER = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

_CONFIG: dict[str, Any] = {
    "models": "served",
    "samples": 5,
    "keep_alive": False,
}


def configure(*, models: str = "served", samples: int = 5, keep_alive: bool = False) -> None:
    """Set live matrix options; called by the ``full`` test runner mode."""
    if samples < 1:
        raise ValueError("samples must be at least 1")
    _CONFIG.update(models=models.strip(), samples=samples, keep_alive=keep_alive)


def _model_matches(served_id: str, weight: str) -> bool:
    """Compare a served model id with a configured weight name."""
    return served_id == weight or served_id.endswith(weight) or weight.endswith(served_id)


def _reasoning_chars(turn: Any) -> int:
    return len("".join(turn.reasoning).strip())


def _turn_error(turn: Any) -> str | None:
    for message in turn.raw_messages:
        error = message.get("errorMessage")
        if not error and isinstance(message.get("info"), dict):
            error = message["info"].get("error")
        if error:
            return str(error)
    if not turn.reasoning and not turn.text:
        return "empty response"
    return None


def _selected_groups(models: dict[str, dict[str, Any]]) -> list[tuple[str, list[str]]]:
    """Resolve the requested model filter into launch groups and their weight names."""
    model_filter = _CONFIG["models"]
    groups: dict[tuple, list[str]] = {}
    for alias, config in models.items():
        if config.get("disabled"):
            continue
        groups.setdefault(model_launch_key(config), []).append(alias)

    selected: list[tuple[str, list[str]]] = []
    for aliases in groups.values():
        weight, _ = parse_model_config(models[aliases[0]])
        if model_filter == "served":
            info = get_vllm_model_info(base_url=API_BASE_URL)
            if info and _model_matches(str(info.get("id", "")), weight):
                selected.append((weight, aliases))
        elif model_filter == "all":
            selected.append((weight, aliases))
        else:
            matched = resolve_target_models({a: models[a] for a in aliases}, model_filter)
            if matched:
                selected.append((weight, matched))

    if not selected:
        raise unittest.SkipTest(f"No configured models matched {model_filter!r}.")

    return selected


class TestReasoningEffortLive(unittest.TestCase):
    """Empirically verify thinking effort for every configured model/harness pair."""

    def test_reasoning_effort_matrix(self) -> None:
        models = load_models()
        harness_names = list(load_harnesses_config())
        selected = _selected_groups(models)
        ensure_sandbox_policy_isolated()

        rows: list[dict[str, Any]] = []
        launch_errors: list[str] = []

        for group_index, (weight, aliases) in enumerate(selected):
            self._run_group(group_index, weight, aliases, models, harness_names, rows, launch_errors)

        self._print_table(rows)
        self._assert_matrix(rows)
        if launch_errors:
            self.fail("Model launch failures:\n" + "\n".join(launch_errors))

    def _run_group(
        self,
        group_index: int,
        weight: str,
        aliases: list[str],
        models: dict[str, dict[str, Any]],
        harness_names: list[str],
        rows: list[dict[str, Any]],
        launch_errors: list[str],
    ) -> None:
        """Ensure one model is served, then exercise every alias on every harness."""
        info = get_vllm_model_info(base_url=API_BASE_URL)
        served_id = str(info.get("id", "")) if info else ""
        launched = not (served_id and _model_matches(served_id, weight))

        if launched:
            logger.info("Launching model group %s (%s)...", weight, weight)
            ok, _ = ensure_model_running(
                model_name=weight,
                model_config=models[aliases[0]],
                base_url=API_BASE_URL,
            )
            if not ok:
                launch_errors.append(weight)
                return

        try:
            for harness_name in harness_names:
                self._run_harness(group_index, weight, aliases, models, harness_name, rows)
        finally:
            if launched and not _CONFIG["keep_alive"]:
                stop_model()

    def _run_harness(
        self,
        group_index: int,
        weight: str,
        aliases: list[str],
        models: dict[str, dict[str, Any]],
        harness_name: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """Start one sandbox and sample every effort alias through one harness."""
        driver = get_driver(harness_name)
        sandbox = SandboxClient(name=f"reasoning-{group_index}-{harness_name}")
        sandbox.ensure()
        workspace = "/home/agent/workspace"

        try:
            sandbox.exec(f"mkdir -p {workspace}")
            for alias in aliases:
                effort = str(models[alias]["reasoning_effort"])
                row: dict[str, Any] = {
                    "model": alias,
                    "weight": weight,
                    "harness": harness_name,
                    "effort": effort,
                    "lengths": [],
                    "errors": [],
                }
                rows.append(row)
                logger.info("Sampling %s on %s (effort=%s)...", alias, harness_name, effort)

                try:
                    active_model = driver.start(
                        sandbox, workspace, alias, API_BASE_URL, reasoning_effort=effort
                    )
                except Exception as exc:
                    row["errors"].append(f"start: {type(exc).__name__}: {exc}")
                    continue

                for sample in range(_CONFIG["samples"] * len(PROMPTS)):
                    prompt = PROMPTS[sample % len(PROMPTS)]
                    try:
                        session_id = driver.create_session(sandbox)
                        turn = driver.send_prompt(
                            sandbox,
                            session_id,
                            prompt,
                            active_model,
                            timeout=int(DEFAULT_MAX_STEP_TIMEOUT_MINUTES * 60),
                            idle_timeout=int(DEFAULT_IDLE_TIMEOUT_MINUTES * 60),
                            reasoning_effort=effort,
                        )
                    except Exception as exc:
                        row["errors"].append(f"sample {sample + 1}: {type(exc).__name__}: {exc}")
                        continue

                    error = _turn_error(turn)
                    if error:
                        logger.warning(
                            "%s/%s/%s sample %d failed: %s",
                            alias, harness_name, effort, sample + 1, error,
                        )
                        row["errors"].append(f"sample {sample + 1}: {error}")
                        continue

                    length = _reasoning_chars(turn)
                    row["lengths"].append(length)
                    logger.debug(
                        "%s/%s/%s sample %d: reasoning=%d chars, text=%d chars",
                        alias, harness_name, effort, sample + 1, length,
                        len("".join(turn.text).strip()),
                    )
        finally:
            sandbox.remove()

    def _print_table(self, rows: list[dict[str, Any]]) -> None:
        """Print the requested min/mean/max reasoning-length table."""
        print("\n| model name | harness name | reasoning effort | min reasoning length | mean reasoning length | max reasoning length |")
        print("|---|---|---|---:|---:|---:|")
        for row in rows:
            lengths = row["lengths"]
            if lengths:
                print(
                    f"| {row['model']} | {row['harness']} | {row['effort']} | "
                    f"{min(lengths)} | {statistics.mean(lengths):.1f} | {max(lengths)} |"
                )
            else:
                print(f"| {row['model']} | {row['harness']} | {row['effort']} | n/a | n/a | n/a |")

    def _assert_matrix(self, rows: list[dict[str, Any]]) -> None:
        """Assert that effort wiring matches the configured on/off/level semantics."""
        self.assertTrue(rows, "No reasoning samples were collected.")

        failed_rows = [row for row in rows if row["errors"]]
        self.assertFalse(
            failed_rows,
            "Reasoning requests failed:\n"
            + "\n".join(
                f"{row['model']} / {row['harness']} / {row['effort']}: {row['errors']}"
                for row in failed_rows
            ),
        )

        by_model: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        for row in rows:
            by_model.setdefault((row["weight"], row["harness"]), {})[row["effort"]] = row

        for (weight, harness), efforts in by_model.items():
            off = efforts.get("off")
            if off:
                self.assertEqual(
                    max(off["lengths"]), 0,
                    f"{weight} on {harness}: 'off' still produced reasoning tokens.",
                )
                on = efforts.get("on")
                if on:
                    self.assertGreater(
                        statistics.mean(on["lengths"]), statistics.mean(off["lengths"]),
                        f"{weight} on {harness}: 'on' did not produce more reasoning than 'off'.",
                    )

            levels = [effort for effort in LEVEL_ORDER if effort in efforts and effort not in ("off", "on")]
            for effort in levels:
                self.assertTrue(
                    efforts[effort]["lengths"],
                    f"{weight} on {harness}: effort '{effort}' collected no samples.",
                )
                self.assertGreater(
                    statistics.mean(efforts[effort]["lengths"]), 0,
                    f"{weight} on {harness}: effort '{effort}' produced no reasoning.",
                )


if __name__ == "__main__":
    unittest.main()
