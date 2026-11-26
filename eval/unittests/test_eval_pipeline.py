"""
eval.unittests.test_eval_pipeline - End-to-end evaluation pipeline test suite.

Exercises the full evaluation chain:
1. Dummy OpenAI/vLLM HTTP server startup & readiness
2. Sandbox provisioning & isolated workspace setup
3. Batch asset streaming (upload_tree with exclusions)
4. In-sandbox zero-disk evaluation engine via stdin
5. Harness driver prompt dispatch & TurnData normalization
6. Artifact staging, server log extraction, and results saving
7. Ephemeral sandbox teardown
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from eval.common import http_json
from eval.config import (
    DEFAULT_TEMPLATE_TAG,
    TESTS_DIR,
)
from eval.drivers import HarnessDriver
from eval.drivers.pi import PiDriver
from eval.launch_benchmark import write_results_file
from eval.results import resolve_model_info, save_evaluation_results
from eval.run_harness import (
    evaluate_step_in_sandbox,
    load_test_spec,
    run_test_suite_on_agent,
)
from eval.sandbox import SandboxClient
from eval.tests.framework import git_changes
from eval.trace import ToolCallEvent, TurnData
from eval.unittests.dummy_server import start_dummy_server, stop_dummy_server


class MockDriver(HarnessDriver):
    """Minimal mock harness driver for exercising test execution pipeline without external agent CLIs."""

    def start(
        self,
        sandbox: SandboxClient,
        workspace: str,
        model_name: str,
        llm_base_url: str,
        reasoning_effort: str | None = None,
        provider: str = "vllm",
    ) -> str:
        active_model, _ = resolve_model_info(llm_base_url, fallback_name=model_name)
        return active_model

    def create_session(self, sandbox: SandboxClient) -> str:
        return "dummy-session-123"

    def send_prompt(
        self,
        sandbox: SandboxClient,
        session_id: str,
        prompt: str,
        model_name: str,
        timeout: int = 60,
        idle_timeout: int = 10,
        reasoning_effort: str | None = None,
    ) -> TurnData:
        # Simulate creating a file in the workspace to test assertion evaluation
        sandbox.exec("echo 'Hello World from Dummy' > /home/agent/workspace/hello.txt")
        return TurnData(
            tool_calls=[
                ToolCallEvent(tool="write", timestamp="2026-08-30T12:00:00Z", input="hello.txt", output="OK")
            ],
            reasoning=["Planning response", "Created hello.txt"],
            text=["Done! I created hello.txt."],
            tokens_in=45,
            tokens_out=22,
            peak_context_tokens=67,
            raw_messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "Done! I created hello.txt."},
            ],
        )

    def get_session_info(self, sandbox: SandboxClient, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "status": "active"}

    def get_all_messages(self, sandbox: SandboxClient, session_id: str) -> list[dict[str, Any]]:
        return [
            {"role": "assistant", "content": "Done! I created hello.txt."}
        ]

    def get_server_log(self, sandbox: SandboxClient) -> str:
        return "Dummy server started\nReady for requests\nTurn processed successfully"

    @property
    def server_log_filename(self) -> str:
        return "mock_server.log"


class TestEvaluationPipeline(unittest.TestCase):
    """Complete test suite exercising the end-to-end evaluation chain."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server, cls.base_url = start_dummy_server()

    @classmethod
    def tearDownClass(cls) -> None:
        stop_dummy_server(cls.server)

    def test_01_dummy_server_endpoints(self) -> None:
        """Verify dummy OpenAI/vLLM server responds to /models and /chat/completions."""
        # 1. Test /models endpoint
        models_data = http_json(f"{self.base_url}/v1/models")
        self.assertIsNotNone(models_data)
        self.assertIn("data", models_data)
        self.assertEqual(models_data["data"][0]["id"], "dummy-model")

        # 2. Test resolve_model_info helper (base URL without /v1 suffix)
        active_model, max_ctx = resolve_model_info(self.base_url, fallback_name="fallback")
        self.assertEqual(active_model, "dummy-model")
        self.assertEqual(max_ctx, 8192)

    def test_02_sandbox_lifecycle_and_transfers(self) -> None:
        """Verify sandbox creation, atomic tar asset uploads, and artifact extraction."""
        sandbox = SandboxClient("test-worker-pipeline")
        sandbox.ensure(template=DEFAULT_TEMPLATE_TAG, publish_ports=False)
        try:
            self.assertTrue(sandbox.exists())

            # 1. Test write_file (binary and text)
            self.assertTrue(sandbox.write_file("/home/agent/test.txt", "hello text"))
            self.assertTrue(sandbox.write_file("/home/agent/test.bin", b"\x00\x01\x02\x03"))

            content = sandbox.read_file("/home/agent/test.txt")
            self.assertEqual(content.strip(), "hello text")

            # 2. Test upload_tree with exclusion filtering
            with tempfile.TemporaryDirectory() as tmp_src:
                (Path(tmp_src) / "data.json").write_text('{"key": "value"}')
                (Path(tmp_src) / "ignore.py").write_text("print('test')")
                (Path(tmp_src) / ".hidden").mkdir()
                (Path(tmp_src) / ".hidden" / "secret.txt").write_text("secret")

                ok = sandbox.upload_tree(tmp_src, "/home/agent/uploaded", exclude={"*.py", ".*"})
                self.assertTrue(ok)

                # Confirm data.json exists but ignore.py and .hidden were excluded
                code, stdout, _ = sandbox.exec("ls /home/agent/uploaded")
                self.assertEqual(code, 0)
                self.assertIn("data.json", stdout)
                self.assertNotIn("ignore.py", stdout)
                self.assertNotIn(".hidden", stdout)

            # 3. Test extract_artifacts
            with tempfile.TemporaryDirectory() as tmp_dst:
                sandbox.extract_artifacts("/home/agent/uploaded", tmp_dst)
                extracted_file = Path(tmp_dst) / "data.json"
                self.assertTrue(extracted_file.exists())
                self.assertEqual(extracted_file.read_text().strip(), '{"key": "value"}')

        finally:
            sandbox.remove()
            self.assertFalse(sandbox.exists())

    def test_03_zero_disk_in_memory_evaluation(self) -> None:
        """Verify step assertions execute in RAM via stdin with zero files written to disk."""
        sandbox = SandboxClient("test-worker-inmem")
        sandbox.ensure(template=DEFAULT_TEMPLATE_TAG, publish_ports=False)
        try:
            workspace = "/home/agent/workspace"
            sandbox.exec(f"mkdir -p {workspace} && cd {workspace} && git init && git config user.name 'Test' && git config user.email 'test@test.com'")

            # Create an asset in workspace
            sandbox.exec(f"echo 'print(\"hello\")' > {workspace}/script.py && cd {workspace} && git add . && git commit -m 'initial'")

            # Modify file
            sandbox.exec(f"echo 'print(\"hello world\")' > {workspace}/script.py")

            # Load actual test spec from test0
            test_run_file = str(TESTS_DIR / "test0" / "run.py")
            res = evaluate_step_in_sandbox(sandbox, test_run_file, step_idx=0, workspace_dir=workspace)

            # Verify evaluation response structure
            self.assertIn("step_name", res)
            self.assertIn("passed", res)
            self.assertIn("point", res)
            self.assertIn("score", res)
            self.assertIn("check_results", res)

            # Verify zero framework files leaked to the workspace directory
            code, stdout, _ = sandbox.exec(f"ls {workspace}")
            self.assertNotIn("spec.py", stdout)
            self.assertNotIn("assertions.py", stdout)
            self.assertNotIn("runner.py", stdout)

        finally:
            sandbox.remove()

    def test_04_full_evaluation_lifecycle(self) -> None:
        """Run a full multi-step evaluation lifecycle against MockDriver and dummy server."""
        sandbox = SandboxClient("test-worker-e2e")
        sandbox.ensure(template=DEFAULT_TEMPLATE_TAG, publish_ports=False)
        driver = MockDriver()

        try:
            test_specs = [(str(TESTS_DIR / "test0" / "run.py"), load_test_spec(str(TESTS_DIR / "test0" / "run.py")))]

            # Run test suite orchestrator
            output = run_test_suite_on_agent(
                model_name="dummy-model",
                test_specs=test_specs,
                driver=driver,
                sandbox=sandbox,
                reasoning_effort="on",
            )

            self.assertIn("eval_id", output)
            self.assertIn("suite_trace", output)
            self.assertIn("test_results_summary", output)
            self.assertIn("stage_dir", output)

            suite_trace = output["suite_trace"]
            self.assertEqual(suite_trace["model"], "dummy-model")
            self.assertIn("tests", suite_trace)

            with tempfile.TemporaryDirectory() as tmp_results_dir:
                tmp_bench_file = os.path.join(tmp_results_dir, "benchmark-data.json")
                saved_eval_dir = save_evaluation_results(
                    model_name="dummy-model",
                    harness_name="mock_harness",
                    harness_version="1.0.0",
                    evaluation_output=output,
                    base_url=f"{self.base_url}/v1",
                    memory_gb=4.5,
                    results_dir=tmp_results_dir,
                    benchmark_data_file=tmp_bench_file,
                )
                self.assertTrue(os.path.isdir(saved_eval_dir))
                self.assertTrue(os.path.exists(os.path.join(saved_eval_dir, "full_trace.json")))
                self.assertTrue(os.path.exists(os.path.join(saved_eval_dir, "mock_server.log")))
                self.assertTrue(os.path.exists(tmp_bench_file))

        finally:
            sandbox.remove()

    def test_05_benchmark_results_append(self) -> None:
        """Verify write_results_file appends to existing result files and never overwrites."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            res_file = Path(tmp_dir) / "result.txt"
            res_file.write_text("Baseline\n", encoding="utf-8")

            summary_1 = [{
                "model": "model-a",
                "memory_gb": 12.0,
                "fresh_prefill_tps": 100.0,
                "cached_prefill_tps": 200.0,
                "decode_tps": 30.0,
                "eff_gen_tps": 25.0,
                "ttft": 0.5,
                "gen_tokens": 500.0,
            }]
            write_results_file(summary_1, filepath=res_file)

            summary_2 = [{
                "model": "model-b",
                "memory_gb": 16.0,
                "fresh_prefill_tps": 150.0,
                "cached_prefill_tps": 250.0,
                "decode_tps": 40.0,
                "eff_gen_tps": 35.0,
                "ttft": 0.3,
                "gen_tokens": 600.0,
            }]
            write_results_file(summary_2, filepath=res_file)

            content = res_file.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("Baseline\n"))
            self.assertIn("model-a", content)
            self.assertIn("model-b", content)

    def test_06_tool_eval_output_parsing_and_normalization(self) -> None:
        """Verify tool-eval-bench JSON parsing, score normalization to 40pts, and wall-time capture."""
        from eval.tool_eval import parse_tool_eval_output

        # Mock 88 scenarios where 78 pass (156 points) and 10 fail/timeout (0 points)
        scenario_results = []
        for i in range(1, 79):
            scenario_results.append({
                "scenario_id": f"TC-{i:02d}",
                "status": "pass",
                "points": 2,
                "summary": f"Scenario {i} passed",
                "duration_seconds": 1.5,
                "prompt_tokens": 100,
                "completion_tokens": 50,
            })
        for i in range(79, 89):
            scenario_results.append({
                "scenario_id": f"TC-{i:02d}",
                "status": "fail",
                "points": 0,
                "summary": f"Scenario {i} timed out",
                "failure_kind": "timeout",
                "duration_seconds": 60.0,
                "prompt_tokens": 100,
                "completion_tokens": 0,
            })

        mock_bench_data = {
            "run_id": "test-run-123",
            "scores": {
                "final_score": 100,  # score that ignored timed out tests
                "total_points": 156,
                "max_points": 156,
                "scenario_results": scenario_results,
            }
        }

        wall_time = 312.45
        parsed = parse_tool_eval_output(mock_bench_data, wall_time_sec=wall_time, max_normalized_points=40.0)

        # 156 earned points out of 176 total points -> normalized to 40 points: 156/176 * 40 = 35 (rounded to int)
        self.assertEqual(parsed["earned_points"], 156)
        self.assertEqual(parsed["max_benchmark_points"], 176)
        self.assertEqual(parsed["normalized_earned_score"], 35)
        self.assertEqual(parsed["wall_time_sec"], 312.45)

        summary = parsed["summary"]
        self.assertEqual(summary["name"], "tool-eval-bench")
        self.assertEqual(summary["earned_score"], 35)
        self.assertEqual(summary["max_score"], 40)
        self.assertEqual(summary["run_time_sec"], 312.45)
        self.assertEqual(summary["tokens_in"], 88 * 100)
        self.assertEqual(summary["tokens_out"], 78 * 50)

        trace = parsed["trace"]
        self.assertEqual(trace["name"], "tool-eval-bench")
        self.assertEqual(trace["earned_score"], 35)
        self.assertEqual(trace["max_score"], 40)
        self.assertEqual(trace["passed_steps"], 78)
        self.assertEqual(trace["total_steps"], 88)
        self.assertEqual(trace["duration_seconds"], 312.45)
        self.assertEqual(len(trace["steps"]), 88)
        self.assertTrue(trace["steps"][0]["evaluation"]["passed"])
        self.assertFalse(trace["steps"][78]["evaluation"]["passed"])

    def test_07_tool_eval_results_saving_integration(self) -> None:
        """Verify full evaluation saving with tool-eval-bench included matches benchmark-data schema."""
        from eval.tool_eval import parse_tool_eval_output, TOOL_EVAL_TEST_KEY

        mock_bench_data = {
            "scores": {
                "scenario_results": [
                    {"scenario_id": "TC-01", "status": "pass", "points": 2, "summary": "Pass", "duration_seconds": 2.0, "prompt_tokens": 50, "completion_tokens": 10},
                    {"scenario_id": "TC-02", "status": "pass", "points": 2, "summary": "Pass", "duration_seconds": 2.0, "prompt_tokens": 50, "completion_tokens": 10},
                ]
            }
        }
        parsed = parse_tool_eval_output(mock_bench_data, wall_time_sec=4.0, max_normalized_points=40.0)

        eval_id = "test-eval-tooleval"
        output = {
            "eval_id": eval_id,
            "suite_trace": {
                "eval_id": eval_id,
                "model": "dummy-model",
                "start_time": "2026-09-12T12:00:00Z",
                "end_time": "2026-09-12T12:01:00Z",
                "tests": {
                    TOOL_EVAL_TEST_KEY: parsed["trace"],
                }
            },
            "test_results_summary": {
                TOOL_EVAL_TEST_KEY: parsed["summary"],
            },
            "stage_dir": None,
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            bench_file = Path(tmp_dir) / "benchmark-data.json"
            saved_dir = save_evaluation_results(
                model_name="dummy-model",
                harness_name="opencode",
                harness_version="1.0.0",
                evaluation_output=output,
                base_url=f"{self.base_url}/v1",
                memory_gb=8.0,
                results_dir=tmp_dir,
                benchmark_data_file=str(bench_file),
            )
            self.assertTrue(os.path.isdir(saved_dir))
            full_trace_file = Path(saved_dir) / "full_trace.json"
            self.assertTrue(full_trace_file.exists())
            
            trace_data = json.loads(full_trace_file.read_text(encoding="utf-8"))
            self.assertIn(TOOL_EVAL_TEST_KEY, trace_data["tests"])

            bench_data = json.loads(bench_file.read_text(encoding="utf-8"))
            eval_record = bench_data["evaluations"][0]
            self.assertIn(TOOL_EVAL_TEST_KEY, eval_record["test_results"])
            self.assertEqual(eval_record["test_results"][TOOL_EVAL_TEST_KEY]["earned_score"], 40)
            self.assertEqual(eval_record["test_results"][TOOL_EVAL_TEST_KEY]["max_score"], 40)

    def test_08_tool_eval_reasoning_effort_kwargs(self) -> None:
        """Verify run_tool_eval_benchmark passes reasoning_effort correctly via extra_params to the Python API."""
        import sys
        from unittest.mock import MagicMock, patch

        captured_extra_params: list[dict] = []

        dummy_result = {
            "scores": {
                "total_points": 100,
                "max_points": 176,
                "scenario_results": [],
            }
        }

        async def mock_run_benchmark(**kwargs):
            captured_extra_params.append(kwargs.get("extra_params") or {})
            return dummy_result

        created_mock_mods = []
        mock_top = MagicMock()
        mock_api = MagicMock()
        mock_api.run_benchmark = mock_run_benchmark
        mock_top.api = mock_api
        mock_scenarios = MagicMock()
        mock_scenarios.ALL_SCENARIOS = ["s1"]
        mock_scenarios.ALL_SCENARIOS_WITH_HARDMODE = ["s1", "s2"]

        for mod_name, mod_obj in [
            ("tool_eval_bench", mock_top),
            ("tool_eval_bench.api", mock_api),
            ("tool_eval_bench.adapters", MagicMock()),
            ("tool_eval_bench.adapters.openai_compat", MagicMock()),
            ("tool_eval_bench.evals", MagicMock()),
            ("tool_eval_bench.evals.scenarios", mock_scenarios),
            ("tool_eval_bench.utils", MagicMock()),
            ("tool_eval_bench.utils.metadata", MagicMock()),
        ]:
            if mod_name not in sys.modules:
                sys.modules[mod_name] = mod_obj
                created_mock_mods.append(mod_name)

        from eval.tool_eval import run_tool_eval_benchmark

        try:
            # Patch _git_sha for Python 3.14 compat and the API run call
            with patch("tool_eval_bench.utils.metadata._git_sha", return_value="abc123"), \
                 patch("tool_eval_bench.api.run_benchmark", new=mock_run_benchmark):


            # 1. Granular efforts are forwarded, including Muse's reasoning_strength.
                for effort in ("low", "medium", "high", "xhigh"):
                    captured_extra_params.clear()
                    res = run_tool_eval_benchmark(
                        base_url="http://spark:8000",
                        timeout=600,
                        parallel=2,
                        hardmode=True,
                        reasoning_effort=effort,
                    )
                    self.assertIsNotNone(res)
                    self.assertEqual(len(captured_extra_params), 1)
                    self.assertEqual(captured_extra_params[0].get("reasoning_effort"), effort)
                    self.assertEqual(
                        captured_extra_params[0].get("chat_template_kwargs"),
                        {"reasoning_strength": effort},
                    )

                # 2. "off" disables thinking via vLLM's valid "none" effort.
                captured_extra_params.clear()
                res_off = run_tool_eval_benchmark(
                    base_url="http://spark:8000",
                    timeout=600,
                    parallel=2,
                    hardmode=True,
                    reasoning_effort="off",
                )
                self.assertIsNotNone(res_off)
                self.assertEqual(captured_extra_params[0], {"reasoning_effort": "none"})

                # 3. "on" uses the model default and sends no effort fields.
                captured_extra_params.clear()
                res_on = run_tool_eval_benchmark(
                    base_url="http://spark:8000",
                    timeout=600,
                    parallel=2,
                    hardmode=True,
                    reasoning_effort="on",
                )
                self.assertIsNotNone(res_on)
                self.assertEqual(captured_extra_params[0], {})
        finally:
            for mod_name in created_mock_mods:
                sys.modules.pop(mod_name, None)

    def test_09_concurrent_save_evaluation_results_file_locking(self) -> None:
        """Verify concurrent executions of save_evaluation_results safely lock and serialize benchmark-data.json updates."""
        from concurrent.futures import ThreadPoolExecutor
        from eval.results import save_evaluation_results

        with tempfile.TemporaryDirectory() as tmp_dir:
            bench_file = os.path.join(tmp_dir, "benchmark-data.json")
            res_dir = os.path.join(tmp_dir, "results")

            def save_worker(idx: int) -> str:
                eval_id = f"eval-concurrent-{idx}"
                eval_output = {
                    "eval_id": eval_id,
                    "suite_trace": {
                        "eval_id": eval_id,
                        "model": "Qwen3.8-27B-NVFP4",
                        "start_time": "2026-09-12T12:00:00Z",
                        "tests": {},
                        "end_time": "2026-09-12T12:10:00Z",
                    },
                    "test_results_summary": {
                        "test0": {
                            "name": "test0",
                            "earned_score": 10 + idx,
                            "max_score": 10 + idx,
                            "run_time_sec": 100.0,
                            "tokens_in": 1000,
                            "tokens_out": 200,
                            "context_used_pct": 10.0,
                        }
                    },
                    "stage_dir": None,
                }
                return save_evaluation_results(
                    model_name="Qwen3.8-27B-NVFP4",
                    harness_name=f"harness_{idx}",
                    harness_version="1.0",
                    evaluation_output=eval_output,
                    base_url="http://mock:8000",
                    memory_gb=50.0,
                    results_dir=res_dir,
                    benchmark_data_file=bench_file,
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(save_worker, range(10)))

            self.assertEqual(len(results), 10)
            self.assertTrue(os.path.exists(bench_file))

            with open(bench_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            evals = data.get("evaluations", [])
            self.assertEqual(len(evals), 10)
            saved_ids = {e["eval_id"] for e in evals}
            self.assertEqual(saved_ids, {f"eval-concurrent-{i}" for i in range(10)})

    def test_10_sandbox_lifecycle_and_signal_cleanup(self) -> None:
        """Verify SandboxClient registers in active set and cleanup_all_sandboxes cleans everything."""
        from unittest.mock import patch
        from eval.sandbox import (
            SandboxClient,
            cleanup_all_sandboxes,
            _ACTIVE_SANDBOXES,
            _ACTIVE_EPHEMERAL_DIRS,
        )

        with patch("eval.sandbox.run_cmd") as mock_cmd:
            from subprocess import CompletedProcess
            mock_cmd.return_value = CompletedProcess(args=["sbx"], returncode=0, stdout="", stderr="")

            sbx1 = SandboxClient(name="test-parallel-1")
            sbx2 = SandboxClient(name="test-parallel-2")

            sbx1.ensure()
            sbx2.ensure()

            self.assertIn("test-parallel-1", _ACTIVE_SANDBOXES)
            self.assertIn("test-parallel-2", _ACTIVE_SANDBOXES)
            self.assertEqual(len(_ACTIVE_EPHEMERAL_DIRS), 2)

            cleanup_all_sandboxes()

            self.assertEqual(len(_ACTIVE_SANDBOXES), 0)
            self.assertEqual(len(_ACTIVE_EPHEMERAL_DIRS), 0)

    def test_11_network_failure_preflight_aborts_test(self) -> None:
        """Verify that preflight network failure to the LLM backend aborts test suite immediately."""
        from unittest.mock import MagicMock
        from eval.common import NetworkConnectivityError

        mock_sandbox = MagicMock()
        mock_sandbox.name = "test-net-guard-sandbox"
        mock_sandbox.verify_network_isolation.return_value = (True, "")
        mock_sandbox.check_backend_connectivity.return_value = (
            False,
            "HTTP 500: dial tcp4 192.168.1.108:8000: connect: no route to host",
        )

        class NonMockDriver(HarnessDriver):
            def start(self, *args, **kwargs): return "model"
            def create_session(self, *args, **kwargs): return "s1"
            def send_prompt(self, *args, **kwargs): raise AssertionError("Should not be called")
            def get_session_info(self, *args, **kwargs): return {}
            def get_all_messages(self, *args, **kwargs): return []
            def get_server_log(self, *args, **kwargs): return ""
            @property
            def server_log_filename(self): return "server.log"

        driver = NonMockDriver()
        test_specs = [(str(TESTS_DIR / "test0" / "run.py"), load_test_spec(str(TESTS_DIR / "test0" / "run.py")))]

        with self.assertRaises(NetworkConnectivityError) as ctx:
            run_test_suite_on_agent(
                model_name="test-model",
                test_specs=test_specs,
                driver=driver,
                sandbox=mock_sandbox,
                llm_base_url="http://spark:8000/v1",
            )
        self.assertIn("failed preflight connectivity", str(ctx.exception))
        self.assertIn("no route to host", str(ctx.exception))

    def test_12_network_failure_mid_test_stops_test(self) -> None:
        """Verify that a network connectivity failure during prompt execution immediately stops the test."""
        from unittest.mock import MagicMock
        from eval.common import NetworkConnectivityError

        mock_sandbox = MagicMock()
        mock_sandbox.name = "test-mid-net-guard-sandbox"
        mock_sandbox.verify_network_isolation.return_value = (True, "")
        mock_sandbox.check_backend_connectivity.return_value = (True, "")

        class FailingNetDriver(HarnessDriver):
            def start(self, *args, **kwargs): return "model"
            def create_session(self, *args, **kwargs): return "s1"
            def send_prompt(self, *args, **kwargs):
                raise NetworkConnectivityError("OpenCode backend connection failed: dial tcp4 192.168.1.108:8000: connect: no route to host")
            def get_session_info(self, *args, **kwargs): return {}
            def get_all_messages(self, *args, **kwargs): return []
            def get_server_log(self, *args, **kwargs): return ""
            @property
            def server_log_filename(self): return "server.log"

        driver = FailingNetDriver()
        test_specs = [(str(TESTS_DIR / "test0" / "run.py"), load_test_spec(str(TESTS_DIR / "test0" / "run.py")))]

        with self.assertRaises(NetworkConnectivityError) as ctx:
            run_test_suite_on_agent(
                model_name="test-model",
                test_specs=test_specs,
                driver=driver,
                sandbox=mock_sandbox,
                llm_base_url="http://spark:8000/v1",
            )
        self.assertIn("due to network connectivity failure", str(ctx.exception))

    def test_13_sandbox_network_isolation(self) -> None:
        """Verify SandboxClient network isolation, policy enforcement, and leak detection."""
        from unittest.mock import MagicMock, patch
        from subprocess import CompletedProcess
        from eval.sandbox import SandboxClient, ensure_sandbox_policy_isolated

        # 1. Test ensure_sandbox_policy_isolated removes default-allow-all
        with patch("eval.sandbox.run_cmd") as mock_cmd:
            inspect_output = """Policy: local-policy
Rules in this policy:
allow ** network - active
Rule IDs:
- default-allow-all yes sbx policy rm network --id default-allow-all
"""
            mock_cmd.return_value = CompletedProcess(args=["sbx"], returncode=0, stdout=inspect_output, stderr="")
            ok = ensure_sandbox_policy_isolated(allowed_hosts=["spark", "192.168.1.108"])
            self.assertTrue(ok)
            # Verify rm was called for default-allow-all
            rm_calls = [call for call in mock_cmd.call_args_list if "default-allow-all" in str(call)]
            self.assertTrue(len(rm_calls) > 0)

        # 1b. Failure to remove default-allow-all must abort, not warn
        with patch("eval.sandbox.run_cmd") as mock_cmd:
            inspect_output = """Policy: local-policy
Rules in this policy:
allow ** network - active
Rule IDs:
- default-allow-all yes sbx policy rm network --id default-allow-all
"""
            mock_cmd.side_effect = [
                CompletedProcess(args=["sbx"], returncode=0, stdout=inspect_output, stderr=""),
                CompletedProcess(args=["sbx"], returncode=1, stdout="", stderr="permission denied"),
            ]
            with self.assertRaises(RuntimeError) as ctx:
                ensure_sandbox_policy_isolated(allowed_hosts=["spark"])
            self.assertIn("Failed to remove", str(ctx.exception))

        # 2. Test isolate_network applies iptables rules
        with patch("eval.sandbox.run_cmd") as mock_cmd:
            mock_cmd.return_value = CompletedProcess(args=["sbx"], returncode=0, stdout="", stderr="")
            sbx = SandboxClient(name="mock-iso-box")
            success = sbx.isolate_network()
            self.assertTrue(success)
            self.assertTrue(mock_cmd.called)
            applied_script = str(mock_cmd.call_args)
            self.assertIn("iptables -P OUTPUT DROP", applied_script)
            self.assertIn("iptables -A OUTPUT -o lo -j ACCEPT", applied_script)
            self.assertIn("172.16.0.0/12", applied_script)

        # 3. Test verify_network_isolation detecting isolation vs leaks
        sbx = SandboxClient(name="mock-iso-box")
        with patch.object(sbx, "exec_python_json") as mock_exec:
            # Case A: Isolated (direct TCP/HTTPS blocked, proxy denies CONNECT)
            mock_exec.return_value = {
                "ok": True,
                "details": {
                    "checks": {
                        "direct_tcp": "blocked",
                        "direct_https": "000",
                        "connect:ambientcg.com": "403:000",
                    },
                    "leaks": [],
                },
            }
            is_iso, err = sbx.verify_network_isolation()
            self.assertTrue(is_iso)
            self.assertEqual(err, "")

            # Case B: Proxy forwarded the request instead of denying it
            mock_exec.return_value = {
                "ok": False,
                "details": {
                    "checks": {
                        "direct_tcp": "blocked",
                        "direct_https": "000",
                        "proxy:ambientcg.com": "forwarded",
                    },
                    "leaks": [
                        "Proxy did not deny ambientcg.com (response: 'HTTP/1.1 403 Forbidden')"
                    ],
                },
            }
            is_iso, err = sbx.verify_network_isolation()
            self.assertFalse(is_iso)
            self.assertIn("Proxy did not deny ambientcg.com", err)

            # Case C: Verifier failure must fail closed
            mock_exec.side_effect = RuntimeError("exec failed")
            is_iso, err = sbx.verify_network_isolation()
            self.assertFalse(is_iso)
            self.assertIn("failed to run", err)

        # 4. Test preflight fails if sandbox network has unfixable internet leak
        mock_leaky_sandbox = MagicMock()
        mock_leaky_sandbox.name = "leaky-sandbox"
        mock_leaky_sandbox.verify_network_isolation.return_value = (False, "Public HTTP domain accessible")

        class NonMockDriver(HarnessDriver):
            def start(self, *args, **kwargs): return "model"
            def create_session(self, *args, **kwargs): return "s1"
            def send_prompt(self, *args, **kwargs): return ""
            def get_session_info(self, *args, **kwargs): return {}
            def get_all_messages(self, *args, **kwargs): return []
            def get_server_log(self, *args, **kwargs): return ""
            @property
            def server_log_filename(self): return "server.log"

        test_specs = [(str(TESTS_DIR / "test0" / "run.py"), load_test_spec(str(TESTS_DIR / "test0" / "run.py")))]
        with self.assertRaises(RuntimeError) as ctx:
            run_test_suite_on_agent(
                model_name="test-model",
                test_specs=test_specs,
                driver=NonMockDriver(),
                sandbox=mock_leaky_sandbox,
                llm_base_url="http://spark:8000/v1",
            )
        self.assertIn("network isolation failed", str(ctx.exception))

    def test_pi_driver_parse_turn(self):
        """Test that PiDriver._parse_turn correctly recovers events, tool calls, and tokens from partial messages."""
        driver = PiDriver()
        step_start_iso = "2026-09-23T12:00:00+00:00"

        messages = [
            {"role": "user", "content": "Sort these files."},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "First, let's explore the directory."},
                    {"type": "toolCall", "id": "call-1", "name": "bash", "arguments": {"command": "ls -la"}},
                ],
                "usage": {"input": 100, "output": 25},
            },
            {
                "role": "toolResult",
                "toolCallId": "call-1",
                "content": [{"type": "text", "text": "file1.png\nfile2.png"}],
                "isError": False,
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Now running analysis algorithm..."},
                    {"type": "toolCall", "id": "call-2", "name": "bash", "arguments": {"command": "python3 analyze.py"}},
                ],
                "usage": {"input": 150, "output": 40},
            },
        ]

        turn = driver._parse_turn(messages, step_start_iso)

        self.assertEqual(turn.tokens_in, 250)
        self.assertEqual(turn.tokens_out, 65)
        self.assertEqual(turn.peak_context_tokens, 190)
        self.assertEqual(len(turn.reasoning), 2)
        self.assertEqual(len(turn.tool_calls), 2)

        # Completed tool call
        self.assertEqual(turn.tool_calls[0].call_id, "call-1")
        self.assertEqual(turn.tool_calls[0].tool, "bash")
        self.assertEqual(turn.tool_calls[0].status, "completed")
        self.assertEqual(turn.tool_calls[0].exit_code, 0)
        self.assertIn("file1.png", turn.tool_calls[0].output)

        # In-flight/running tool call before timeout
        self.assertEqual(turn.tool_calls[1].call_id, "call-2")
        self.assertEqual(turn.tool_calls[1].tool, "bash")
        self.assertEqual(turn.tool_calls[1].status, "running")
        self.assertIsNone(turn.tool_calls[1].exit_code)
        self.assertEqual(turn.tool_calls[1].output, "")

        # Since no explicit text was emitted, the last reasoning should be promoted to response
        self.assertEqual(len(turn.text), 1)
        self.assertEqual(turn.text[0], "Now running analysis algorithm...")

        # Verify events list
        event_types = [e["type"] for e in turn.events]
        self.assertEqual(event_types, ["reasoning", "tool", "response", "tool"])

    def test_pi_driver_parse_turn_with_explicit_text(self):
        """Test PiDriver._parse_turn with explicit assistant text response and tool error."""
        driver = PiDriver()
        step_start_iso = "2026-09-23T12:00:00+00:00"

        messages = [
            {"role": "user", "content": "Run command"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "I will run bad_command."},
                    {"type": "toolCall", "id": "call-err", "name": "bash", "arguments": {"command": "bad_command"}},
                ],
                "usage": {"input": 50, "output": 10},
            },
            {
                "role": "toolResult",
                "toolCallId": "call-err",
                "content": [{"type": "text", "text": "command not found"}],
                "isError": True,
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "The command failed as expected."},
                ],
                "usage": {"input": 70, "output": 15},
            },
        ]

        turn = driver._parse_turn(messages, step_start_iso)
        self.assertEqual(turn.tokens_in, 120)
        self.assertEqual(turn.tokens_out, 25)
        self.assertEqual(len(turn.tool_calls), 1)
        self.assertEqual(turn.tool_calls[0].status, "error")
        self.assertEqual(turn.tool_calls[0].exit_code, 1)
        self.assertEqual(turn.tool_calls[0].output, "command not found")
        self.assertEqual(turn.text, ["The command failed as expected."])

    def test_pi_driver_thinking_config_placeholders(self):
        """Verify Pi thinking config uses Pi's $var placeholders rather than literal $thinking.* strings."""
        from unittest.mock import MagicMock

        driver = PiDriver()
        sandbox = MagicMock()

        driver._write_config(
            sandbox, "active-model", "alias", "http://localhost:8000/v1", reasoning_effort="medium"
        )
        setup_cmd = sandbox.exec.call_args[0][0]
        self.assertIn('"$var": "thinking.effort"', setup_cmd)
        self.assertIn('"reasoning_strength"', setup_cmd)
        self.assertIn('"thinkingLevelMap"', setup_cmd)
        self.assertNotIn('"$thinking.effort"', setup_cmd)

        sandbox.reset_mock()
        driver._write_config(
            sandbox, "active-model", "alias", "http://localhost:8000/v1", reasoning_effort="off"
        )
        setup_cmd = sandbox.exec.call_args[0][0]
        self.assertIn('"$var": "thinking.enabled"', setup_cmd)
        self.assertNotIn('"$thinking.enabled"', setup_cmd)


class TestGitChangeAssert(unittest.TestCase):
    """git_changes must verify real git state rather than just file existence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        self._git("init")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args: str):
        subprocess.run(["git", *args], cwd=self.ws, check=True, capture_output=True)

    def test_add_modify_delete(self):
        Path(self.ws, "a.txt").write_text("one\n")
        self.assertTrue(git_changes("a.txt", "A").evaluate(self.ws).passed)
        self.assertFalse(git_changes("a.txt", "M").evaluate(self.ws).passed)

        self._git("add", "-A")
        self._git("commit", "-m", "init")
        self.assertFalse(git_changes("a.txt", "M").evaluate(self.ws).passed)

        Path(self.ws, "a.txt").write_text("two\n")
        self.assertTrue(git_changes("a.txt", "M").evaluate(self.ws).passed)
        self.assertFalse(git_changes("a.txt", "A").evaluate(self.ws).passed)

        os.remove(Path(self.ws, "a.txt"))
        self.assertTrue(git_changes("a.txt", "D").evaluate(self.ws).passed)


if __name__ == "__main__":
    unittest.main()






