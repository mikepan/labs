# Evaluation Architecture & Workflow

## Overview
The evaluation framework benchmarks code-generation and multi-turn agent capabilities across local and remote LLMs running on vLLM clusters using isolated Docker sandboxes. It supports pluggable agent harnesses (OpenCode, pi.dev, etc.) via a driver abstraction with strict host isolation and anti-cheating protections.

```
┌─────────────────────────────────────────────────────────────┐
│                        Local Host                           │
│  ┌───────────────────────┐       ┌───────────────────────┐  │
│  │   Test Orchestrator   │       │   Docker Sandbox      │  │
│  │  - launch_eval.py     │ stdin │   - Ephemeral ws      │  │
│  │  - run_harness.py     │──────>│   - Agent Server (Pi/OC)│  │
│  │  - In-memory checks   │       │   - /tmp/eval_<test>  │  │
│  └───────────────────────┘       └───────────────────────┘  │
└──────────────────────────────────────────────│──────────────┘
                                               │ HTTP / API (Port 8000)
                                               ▼
                                  ┌─────────────────────────────┐
                                  │  Remote vLLM Host (spark)   │
                                  │  - vLLM Engine (CUDA)       │
                                  └─────────────────────────────┘
```

---

## Sandbox Security & Host Isolation Architecture

To prevent agent data contamination, host tampering, and model cheating:
1. **Zero Host Mounts**: Sandboxes are provisioned with ephemeral, empty host directories (`/tmp/sbx_empty_ws_...`). The host repository (`REPO_ROOT`) is **never** mounted into the container.
2. **In-Memory Assertion Execution**: Test validator code, ground truth algorithms, and evaluation scripts are bundled in-memory by the orchestrator and streamed via `stdin` to a transient Python subprocess. No validator files exist on disk in the sandbox.
3. **Dynamic Asset Streaming**: Test data assets (such as datasets or templates) are dynamically streamed directly into `/tmp/eval_<test_id>` over standard input during test setup.

---

## Directory Structure

- **`eval/`**: Orchestration, harness drivers, and infrastructure.
  - `config.py`: Centralized paths, host config, and environment defaults.
  - `common.py`: Shared logging and process execution helpers.
  - `sandbox.py`: `SandboxClient` class — encapsulates all Docker Sandbox (`sbx`) interactions, ephemeral workspace lifecycles, and asset streaming.
  - `trace.py`: Harness-agnostic data types (`TurnData`, `StepTrace`, `ToolCallEvent`) for consistent traces.
  - `results.py`: Model metadata, memory profiling, benchmark record construction, and result persistence.
  - `launch_eval.py`: End-to-end evaluation runner across models, tests, and harnesses with upfront validation.
  - `launch_model.py`: Dedicated remote vLLM model lifecycle manager (container start, probe, sanity test, teardown).
  - `launch_benchmark.py`: High-throughput LLM serving benchmark orchestrator and tool-eval-bench quality runner.
  - `run_harness.py`: Sandbox test runner — dynamic test loading, asset streaming, in-memory step evaluation, and trace generation.
  - `create_harness.py`: Builds and snapshots the sandbox template with agent CLI installations.
  - `test_analyzer.py`: Cross-model defect and pass-rate analysis tool.
  - `drivers/`: Pluggable harness driver implementations.
    - `__init__.py`: `HarnessDriver` ABC, `@register_driver` decorator, and `get_driver()` registry.
    - `opencode.py`: OpenCode CLI/Server driver (config, session management, message parsing).
    - `pi.py`: Pi coding agent RPC driver (config, session management, event parsing).
  - `models.json`: Structured model configurations (vLLM launch args, KV quant, speculative decoding).
  - `harnesses.json`: Harness CLI/server configurations and version definitions.
- **`tests/`**: Benchmark test suites. Each suite defines multi-step prompts, point allocations, setup commands, and checks.
  - `framework/`: Assertion primitives (`git_changes`, `lang_detect`, `files_identical`, `custom_check`) and in-memory evaluation runner.
- **`site/results/`**: Output directory for evaluation runs. Subfolders are structured as `site/results/{eval_id}/`:
  - `full_trace.json`: Comprehensive self-contained step-by-step traces, model metadata, reasoning streams, tool calls, and assertion results.
  - `<harness>_server.log`: Raw agent server execution logs inside the sandbox.
  - `artifacts/`: Extracted workspace files and generated deliverables.
- **`site/results/benchmark-data.json`**: Cumulative benchmark leaderboard records (key-value evaluations array) updated automatically upon completion.
- **`site/`**: Web dashboard UI displaying leaderboard rankings, drilldown metrics, test breakdowns, and chronological trace viewer.

---

## Execution Pipeline

1. **Model Launch & Weight Resolution**: `launch_eval.py` coordinates with `launch_model.py` to parse `model-arg`, extract the target served model weight, and deploy the container in daemon mode on `mike@spark`.
2. **Readiness Probe**: Probes `/v1/models` to verify server health and confirm that the exact expected model weight is loaded before proceeding.
3. **Warmup & Sanity Test**: Sends a direct test query (`"What is the capital of Japan?"`) to `/v1/chat/completions` to warm up the LLM server and ensure basic content generation coherency.
4. **Sandbox Provisioning**: Creates an isolated ephemeral container from `eval-base-harness:latest` via `sbx` with an empty host workspace.
5. **Workspace Setup & Asset Streaming**: Initializes `/tmp/eval_<test_id>`, creates baseline git tracking, and streams required test data files (e.g. `bookstore_orders.csv`) into the workspace.
6. **Agent Harness Initialization**: The selected `HarnessDriver` (e.g. `OpenCodeDriver`, `PiDriver`) configures and starts the agent server inside `/tmp/eval_<test_id>`.
7. **Multi-Turn Step Execution**: Sends step prompts sequentially via the driver, which normalizes responses into `TurnData` (reasoning tokens, tool calls, text completions, token counts).
8. **In-Memory Assertion Evaluation**: Streams self-contained evaluation scripts into sandbox memory to validate workspace changes and score step completions without exposing code to disk.
9. **Persistence**: Copies generated artifacts and server logs to `site/results/{eval_id}/`, saves `full_trace.json`, and updates `site/results/benchmark-data.json`.

---

## Scoring & Benchmark Consolidation Model

To capture both competence and reliability, metrics are consolidated as follows:

| Metric | Consolidation Method | Formula | Description |
| :--- | :--- | :--- | :--- |
| **Earned Score** | Mean (avg) | avg(score_t) = (1/N) * sum(score_(t, r)) | Captures average capability across runs without discarding partial successes. |
| **Intelligence** | Weighted Mean Score % | (sum(avg_score_t) / sum(max_score_t)) * 100 | Standardized overall intelligence benchmark score. |
| **Task Speed** | Mean Duration | (3600 * tasks) / sum(avg_duration_t) | Average throughput in tasks/hour. |
| **Consistency Rate** | Pass Agreement % | (Fully Passed Runs / Total Runs) * 100 | Measures determinism across repetitions (e.g., 3/3 = 100%, 2/3 = 66.7%). |
| **Context Peak** | Mean Peak Window | avg(peak_ctx_t) = (1/N) * sum(peak_ctx_(t, r)) | Average maximum context window required. |

---

## Environment Setup & Usage

### 0. Environment Setup (Recommended)
From the `labs/` directory, install the package in editable mode so `eval` and `tests` are available globally across all subprocesses:
```bash
pip install -e ".[eval]"
```

Alternatively, prefix your commands with `PYTHONPATH=.` so child processes (like `run_harness.py` spawned by `launch_eval.py`) inherit the package root.

---

### 1. End-to-End Evaluation Runner (`eval.launch_eval`)
```bash
# Run all configured models across all harnesses and tests (default: --model all --harness all --test all)
PYTHONPATH=. python3 -m eval.launch_eval

# Run a specific model across all harnesses and tests
PYTHONPATH=. python3 -m eval.launch_eval --model Qwen3.8

# Run a specific model on a specific test suite with a specific harness
PYTHONPATH=. python3 -m eval.launch_eval --model Qwen3.8 --harness "opencode cli" --test test0

# Keep-alive mode: keep model container running after evaluation completes (skips teardown)
PYTHONPATH=. python3 -m eval.launch_eval --model Qwen3.8-27B-FP8-low --harness pi --test test0 --keep-alive

# Verbose mode: stream real-time container startup and debug logs
PYTHONPATH=. python3 -m eval.launch_eval --model Qwen3.8-27B-FP8-low --v
```

### 2. Standalone Model Lifecycle Manager (`eval.launch_model`)
```bash
# Launch a model, wait for readiness probe, and execute sanity query (stops container when complete)
PYTHONPATH=. python3 -m eval.launch_model --model Qwen3.8-27B-FP8-low

# Launch a model and keep the container running for external benchmarking (e.g. tool-eval-bench)
PYTHONPATH=. python3 -m eval.launch_model --model Qwen3.8-27B-FP8-low --keep-alive

# Stop the running model container on the remote cluster
PYTHONPATH=. python3 -m eval.launch_model --stop

# Stream container startup logs
PYTHONPATH=. python3 -m eval.launch_model --model Qwen3.8-27B-FP8-low --keep-alive --v
```

### 3. Direct Sandbox Harness Runner (`eval.run_harness`)
```bash
# Run test0 against an already running vLLM endpoint with default harness (all configured harnesses)
PYTHONPATH=. python3 -m eval.run_harness --model Qwen3.8-27B-FP8-low --test test0

# Run specific harness (e.g. pi or opencode cli)
PYTHONPATH=. python3 -m eval.run_harness --model Qwen3.8-27B-FP8-low --harness "opencode cli" --test test0

# Run all test suites across all harnesses
PYTHONPATH=. python3 -m eval.run_harness --model Qwen3.8-27B-FP8-low --test all --harness all

# Verbose debug logging
PYTHONPATH=. python3 -m eval.run_harness --model Qwen3.8-27B-FP8-low --harness pi --test test0 --v
```

### 4. High-Throughput Serving Benchmark & Quality Runner (`eval.launch_benchmark`)
```bash
# Run serving throughput benchmark across all configured models
PYTHONPATH=. python3 -m eval.launch_benchmark

# Run benchmark for a specific model with 2 iterations
PYTHONPATH=. python3 -m eval.launch_benchmark --model Qwen3.8-27B-NVFP4-xhigh --runs 2

# Benchmark currently active server directly without stopping or starting containers
PYTHONPATH=. python3 -m eval.launch_benchmark --no-manage

```

### 5. Serve Dashboard Web App
```bash
python3 -m http.server 8080 --directory site
# Open http://localhost:8080
```

### 6. Cross-Model Defect & Pass-Rate Analyzer (`eval.test_analyzer`)
```bash
# Scan evaluation traces, print step pass-rate matrix, and flag 0% pass steps
PYTHONPATH=. python3 -m eval.test_analyzer
```