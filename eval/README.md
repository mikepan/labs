# FiniBench: Agentic AI Velocity & Efficiency Benchmark

> **"Why does my self-hosted model feel so much dumber than official benchmarks suggest?"**

Official model cards boast about FP16 scores on single-turn quizzes. In practice, self-hosters run quantized models (FP8, NVFP4, INT4, Q2_XSS) inside multi-turn coding loops (**OpenCode**, **Pi**).

Single-turn metrics fail to predict agentic success. Under the weight of sequential tool calls, compiler errors, and expanding context, lower-precision weights often derail completely. A 4-bit quant behaves nothing like its 16-bit original.

**FiniBench** benchmarks the real-world triad:

**`Model × Quantization × Agent Harness`**

Instead of vanity tokens-per-second, we measure **verified work delivered**:
- **Task Speed (`tasks/hr`)**: Verified multi-turn problems solved per hour.
- **Intelligence Density (`tasks/GB`)**: Work delivered per gigabyte of VRAM.
- **Pass Rate**: Percentage of multi-step tasks completed end-to-end.

---

## 🏗️ How It Works

```
┌──────────────────────────────────────────────────────────────┐
│                       Local Host (Mac)                       │
│  ┌──────────────────┐           ┌─────────────────────────┐  │
│  │   Orchestrator   │   stdin   │   Docker Sandbox (sbx)  │  │
│  │ (eval / harness) │──────────>│   - Ephemeral workspace │  │
│  │                  │  RAM-only │   - Agent CLI (Pi/OC)   │  │
│  └──────────────────┘ assertions└─────────────────────────┘  │
└──────────────────────────────────────────────│───────────────┘
                                               │ HTTP / Port 8000
                                               ▼
                                ┌──────────────────────────────┐
                                │   Remote vLLM Host (spark)   │
                                └──────────────────────────────┘
```

### 1. Zero-Contamination Sandboxing
- **No Host Mounts**: Sandboxes boot with an empty, ephemeral directory. The repo is never mounted.
- **RAM-Only Assertions**: Validators and ground truths are piped into Python RAM via `stdin`—never written to container disk, preventing cheating.
- **Asset Streaming**: Fixtures and starter files stream directly over `stdin` into `/tmp/eval_<test_id>`.
- **Network Isolation**: Blocks general internet access; only permits traffic to the LLM backend.

### 2. Evaluation Battery
1. **Multi-Step Workflows (`test0` – `test3`)**: Greenfield coding, debugging, language porting (Kotlin), and texture classification.
2. **Trivia QA (`trivia`)**: 700+ questions measuring breadth of knowledge, instruction-following, and consistency.
3. **Structured Tool Calling (`tool-eval-bench`)**: Tests tool-calling decisions and schema accuracy across diverse scenarios.

---

## 🚀 Setup

Requires [uv](https://docs.astral.sh/uv/) (Python 3.14 managed automatically):

```bash
uv sync
```

---

## 💻 Running Benchmarks

### 1. End-to-End (`eval`)
Handles remote model startup, readiness checks, sandbox creation, test execution, and teardown:

```bash
# Run all configured models
uv run eval

# Target a specific model, harness, or test
uv run eval --model Qwen3.6-35B-A3B-NVFP4 --harness "opencode cli" --test test0
```

### 2. Standalone Model Server (`model`)
Manage remote vLLM containers independently:

```bash
# Launch model and wait for readiness probe
uv run model --model Qwen3.6-35B-A3B-NVFP4

# Stream startup logs / stop container
uv run model --model Qwen3.6-35B-A3B-NVFP4 --v
uv run model --stop
```

### 3. Direct Sandbox Runner (`harness`)
Run tests against an existing, active LLM endpoint:

```bash
uv run harness --model Qwen3.6-35B-A3B-NVFP4 --test test0
uv run harness --model Qwen3.6-35B-A3B-NVFP4 --test all --harness all
```

### 4. Standalone Tool & Trivia Benchmarks
```bash
uv run trivia
uv run benchmark --model Qwen3.8-27B-NVFP4-xhigh --runs 2
```

### 5. Results & Web Dashboard
```bash
# Spot failure patterns across runs
uv run test-analyzer

# View interactive leaderboard at http://localhost:8080
python3 -m http.server 8080 --directory site
```

---

## ⚡ Techniques & Key Features

- **In-Memory RAM Assertions**: Zero-disk footprint prevents agent inspection or solution leakage.
- **Dynamic Asset Streaming**: Injects files on-the-fly without host volume mounts.
- **Strict Network Guards**: Preflight checks enforce firewall isolation from public internet.
- **Pluggable Drivers**: `HarnessDriver` unifies CLI and RPC agents (OpenCode, Pi) into standard `TurnData`.
- **Lifecycle Automation**: Automated vLLM deployment, `/v1/models` probing, and `--keep-alive` support.
- **Sleep Prevention**: Engages `caffeinate` to protect long overnight runs from macOS sleep.
- **Defect Analysis**: `test-analyzer` flags 0% pass bottlenecks and model-specific failure modes.
- **Parallel Execution**: Ephemeral sandboxes allow concurrent runs with zero disk or lock collisions.
- **Instant Dashboard Sync**: Atomic updates to `benchmark-data.json` feed real-time charts and rankings.
