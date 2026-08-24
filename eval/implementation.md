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
  - `run_harness.py`: Sandbox test runner — dynamic test loading, asset streaming, in-memory step evaluation, and trace generation.
  - `create_harness.py`: Builds and snapshots the sandbox template with agent CLI installations.
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
| **Earned Score** | Mean ($\mu$) | $\mu_t = \frac{1}{N} \sum_{r=1}^N \text{score}_{t, r}$ | Captures average capability across runs without discarding partial successes. |
| **Intelligence** | Weighted Mean Score % | $\frac{\sum_t \mu_t}{\sum_t \text{max_score}_t} \times 100$ | Standardized overall intelligence benchmark score. |
| **Task Speed** | Mean Duration | $\frac{3600 \times \text{tasks}}{\sum_t \mu(\text{duration}_t)}$ | Average throughput in tasks/hour. |
| **Consistency Rate** | Pass Agreement % | $\frac{\text{Fully Passed Runs}}{\text{Total Runs}} \times 100$ | Measures determinism across repetitions (e.g., 3/3 = 100%, 2/3 = 66.7%). |
| **Context Peak** | Mean Peak Window | $\frac{1}{N} \sum_{r=1}^N \text{peak_ctx}_{t, r}$ | Average maximum context window required. |

---

## Usage

### 1. End-to-End Evaluation Runner (`launch_eval.py`)
```bash
# Run all configured models across all harnesses and tests (default: --model all --harness all --test all)
python3 eval/launch_eval.py

# Run a specific model across all harnesses and tests
python3 eval/launch_eval.py --model Qwen3.6-27B-FP8-NoThink

# Run a specific model on a specific test suite with a specific harness
python3 eval/launch_eval.py --model diffusiongemma-26B-A4B-IT-NVFP4-Reasoning --harness pi --test test0

# Fast dev mode: attach to existing vLLM server without restarting/tearing down
python3 eval/launch_eval.py --model Qwen3.6-27B-NVFP4 --harness pi --test test0 --fast

# Verbose mode: stream real-time container startup and debug logs
python3 eval/launch_eval.py --model Qwen3.6-35B-A3B-NVFP4 --v
```

### 2. Standalone Model Lifecycle Manager (`launch_model.py`)
```bash
# Launch a model, wait for readiness probe, and execute sanity query
python3 eval/launch_model.py --model Qwen3.6-27B-FP8-Thinking

# Launch model in fast mode (skips if already running and healthy)
python3 eval/launch_model.py --model Qwen3.6-27B-FP8-Thinking --fast

# Stop the running model container on the remote cluster
python3 eval/launch_model.py --stop

# Stream container startup logs
python3 eval/launch_model.py --model Qwen3.6-27B-FP8-Thinking --v
```

### 3. Direct Sandbox Harness Runner (`run_harness.py`)
```bash
# Run test0 against an already running vLLM endpoint with default harness (all configured harnesses)
python3 eval/run_harness.py --model Qwen3.6-27B-FP8-NoThink --test test0

# Run specific harness (e.g. pi or opencode)
python3 eval/run_harness.py --model Qwen3.6-27B-FP8-NoThink --harness pi --test test0

# Run all test suites across all harnesses
python3 eval/run_harness.py --model Qwen3.6-27B-FP8-NoThink --test all --harness all

# Verbose debug logging
python3 eval/run_harness.py --model Qwen3.6-27B-FP8-NoThink --harness pi --test test0 --v
```

### 4. Serve Dashboard Web App
```bash
python3 -m http.server 8080 --directory site
# Open http://localhost:8080
```

### 5. Cross-Model Defect & Pass-Rate Analyzer (`test_analyzer.py`)
```bash
# Scan evaluation traces, print step pass-rate matrix, and flag 0% pass steps
python3 eval/test_analyzer.py
```