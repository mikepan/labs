# Evaluation Architecture & Workflow

## Overview
The evaluation framework benchmarks code-generation and multi-turn agent capabilities across local and remote LLMs running on vLLM clusters using isolated Docker sandboxes.

```
┌─────────────────┐       SSH / HTTP        ┌─────────────────────────────┐
│  Local Machine  │ ──────────────────────> │  Remote vLLM Host (spark)   │
│  (Runner & Sbx) │                         │  - Port 8000 (vLLM Serve)   │
└─────────────────┘                         └─────────────────────────────┘
```

---

## Directory Structure

- **`eval/`**: Orchestration and harness scripts.
  - `models.json`: Structured model configurations.
  - `launch_model.py`: End-to-end pipeline runner and cluster orchestrator.
  - `harnesses.json`: Harness CLI/server configurations and version definitions.
  - `run_harness.py`: Sandbox lifecycle manager, OpenCode server driver, test suite runner, assertion evaluator, and metric exporter.
- **`tests/`**: Benchmark test suites. Each suite defines multi-step prompts, point allocations, setup commands, and checks .
- **`results/`**: Output directory for evaluation runs. Subfolders are structured as `results/{model_slug}_{harness}_{timestamp}/`:
  - `results.json`: Summary benchmark metrics conforming to `benchmark-data.schema.json`.
  - `full_trace.json`: Comprehensive step-by-step traces, prompt messages, reasoning blocks, tool call records, and assertion results.
  - `opencode_server.log`: Raw OpenCode server execution logs inside the sandbox.
  - `artifacts/`: Extracted workspace files and generated deliverables.
- **`site/data/benchmark-data.json`**: Cumulative benchmark leaderboard records (17-column dataset conforming to schema with model-level memory_gb) updated automatically upon completion.
- **`site/`**: Web dashboard UI displaying leaderboard rankings, drilldown metrics, test breakdowns, and trace viewers.

---

## Execution Pipeline

1. **Model Launch & Weight Resolution**: `launch_model.py` parses `model-arg` to extract the target served model weight and generates the full launch command string. Deploys container in daemon mode on `mike@spark`.
2. **Readiness Probe**: Probes `/v1/models` to verify server health and confirm that the exact expected model weight is loaded before proceeding.
3. **Warmup & Sanity Test**: Sends a direct test query (`"What is the capital of Japan?"`) to `/v1/chat/completions` to warm up the LLM server and ensure basic content generation coherency.
4. **Sandbox Provisioning**: Creates an isolated ephemeral container from `eval-base-harness:latest` via `sbx` with port `4096` mapped.
5. **OpenCode Server Initialization**: Writes `~/.config/opencode/opencode.json` mapped to the LLM endpoint and starts headless OpenCode server inside the test workspace (`/tmp/eval_<test_id>`).
6. **Multi-Turn Step Execution**: Sends step prompts sequentially via OpenCode session API, capturing reasoning tokens, tool inputs/outputs, and assistant completions.
7. **Assertion Evaluation**: Runs test-defined assertions inside the sandbox container against the workspace state to score step completions.
8. **Persistence**: Copies generated artifacts and server logs to `results/{eval_name}/`, outputs `results.json` and `full_trace.json`, and updates `site/data/benchmark-data.json`.

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
# Run single test against an active vLLM endpoint
python3 eval/run_harness.py Qwen3.6-27B-FP8-NoThink --test test0

# Run all test suites against custom base URL
python3 eval/run_harness.py Qwen3.6-27B-FP8-NoThink --test all --base-url http://spark:8000/v1
```

### Serve Dashboard Web App
```bash
python3 -m http.server 8080 --directory site
# Open http://localhost:8080
```