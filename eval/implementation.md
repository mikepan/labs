# Evaluation Architecture & Workflow

## Overview
The evaluation framework benchmarks code-generation and multi-turn agent capabilities across local and remote LLMs running on vLLM clusters using isolated Docker sandboxes. It supports pluggable agent harnesses (OpenCode, pi.dev, etc.) via a driver abstraction.

```
┌─────────────────┐       SSH / HTTP        ┌─────────────────────────────┐
│  Local Machine  │ ──────────────────────> │  Remote vLLM Host (spark)   │
│  (Runner & Sbx) │                         │  - Port 8000 (vLLM Serve)   │
└─────────────────┘                         └─────────────────────────────┘
```

---

## Directory Structure

- **`eval/`**: Orchestration, harness drivers, and infrastructure.
  - `config.py`: Centralized paths, host config, and environment defaults.
  - `common.py`: Shared logging helpers.
  - `sandbox.py`: `SandboxClient` class — encapsulates all Docker Sandbox (`sbx`) interactions.
  - `trace.py`: Harness-agnostic data types (`TurnData`, `StepTrace`, `ToolCallEvent`) for consistent traces.
  - `results.py`: Model metadata, memory profiling, benchmark record construction, and result persistence.
  - `run_harness.py`: Slim orchestrator — test loading, step evaluation, and test suite execution.
  - `launch_model.py`: End-to-end pipeline runner and cluster orchestrator.
  - `create_harness.py`: Builds and snapshots the sandbox template with agent CLI installations.
  - `drivers/`: Pluggable harness driver implementations.
    - `__init__.py`: `HarnessDriver` ABC, `@register_driver` decorator, and `get_driver()` registry.
    - `opencode.py`: OpenCode CLI/Server driver (config, session management, message parsing).
  - `models.json`: Structured model configurations (vLLM launch args, KV quant, speculative decoding).
  - `harnesses.json`: Harness CLI/server configurations and version definitions.
- **`tests/`**: Benchmark test suites. Each suite defines multi-step prompts, point allocations, setup commands, and checks.
  - `framework/`: Assertion primitives (`git_changes`, `lang_detect`, `custom_check`) and evaluation runner.
- **`site/results/`**: Output directory for evaluation runs. Subfolders are structured as `site/results/{eval_id}/`:
  - `full_trace.json`: Comprehensive self-contained step-by-step traces, model metadata, reasoning streams, tool calls, and assertion results.
  - `<harness>_server.log`: Raw agent server execution logs inside the sandbox.
  - `artifacts/`: Extracted workspace files and generated deliverables.
- **`site/results/benchmark-data.json`**: Cumulative benchmark leaderboard records (key-value evaluations array) updated automatically upon completion.
- **`site/`**: Web dashboard UI displaying leaderboard rankings, drilldown metrics, test breakdowns, and chronological trace viewer.

---

## Execution Pipeline

1. **Model Launch & Weight Resolution**: `launch_model.py` parses `model-arg` to extract the target served model weight and generates the full launch command string. Deploys container in daemon mode on `mike@spark`.
2. **Readiness Probe**: Probes `/v1/models` to verify server health and confirm that the exact expected model weight is loaded before proceeding.
3. **Warmup & Sanity Test**: Sends a direct test query (`"What is the capital of Japan?"`) to `/v1/chat/completions` to warm up the LLM server and ensure basic content generation coherency.
4. **Sandbox Provisioning**: Creates an isolated ephemeral container from `eval-base-harness:latest` via `sbx` with port `4096` mapped.
5. **Agent Harness Initialization**: The selected `HarnessDriver` (e.g. `OpenCodeDriver`) configures and starts the agent server inside the test workspace (`/tmp/eval_<test_id>`).
6. **Multi-Turn Step Execution**: Sends step prompts sequentially via the driver, which normalizes responses into `TurnData` (reasoning tokens, tool calls, text completions, token counts).
7. **Assertion Evaluation**: Runs test-defined assertions inside the sandbox container against the workspace state to score step completions.
8. **Persistence**: Copies generated artifacts and server logs to `site/results/{eval_id}/`, saves `full_trace.json`, and updates `site/results/benchmark-data.json`.

---

## Usage

### Run via Cluster Orchestrator (`launch_model.py`)
```bash
# Run all configured models across all tests (default: model=all, --test all)
python3 eval/launch_model.py

# Run a specific model across all tests
python3 eval/launch_model.py Qwen3.6-27B-FP8-NoThink

# Run a specific model on a specific test suite
python3 eval/launch_model.py Qwen3.6-27B-FP8-Thinking --test test0

# Fast dev mode: attach to existing vLLM server without restarting/tearing down
python3 eval/launch_model.py Qwen3.6-27B-NVFP4 --test test0 --fast

# Verbose mode: stream real-time container startup and server logs
python3 eval/launch_model.py Qwen3.6-35B-A3B-NVFP4 --test test0 --v
```

### Run Harness Directly (`run_harness.py`)
```bash
# Run single test against an active vLLM endpoint (default harness: opencode)
python3 eval/run_harness.py Qwen3.6-27B-FP8-NoThink --test test0

# Run all test suites
python3 eval/run_harness.py Qwen3.6-27B-FP8-NoThink --test all

# Use a different agent harness (e.g. pi.dev)
python3 eval/run_harness.py Qwen3.6-27B-FP8-NoThink --test test0 --harness pi

# Verbose debug logging
python3 eval/run_harness.py Qwen3.6-27B-FP8-NoThink --test test0 --v
```

### Serve Dashboard Web App
```bash
python3 -m http.server 8080 --directory site
# Open http://localhost:8080
```