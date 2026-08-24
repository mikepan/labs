"""
eval.drivers.opencode - OpenCode CLI/Server harness driver.

Implements HarnessDriver for the OpenCode agent, handling configuration,
session management, prompt execution, and response parsing.
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

from eval.common import setup_logger
from eval.config import DEFAULT_OPENCODE_PORT
from eval.drivers import HarnessDriver, register_driver
from eval.sandbox import SandboxClient
from eval.trace import ToolCallEvent, TurnData

__all__ = ["OpenCodeDriver"]

logger = setup_logger("driver.opencode")


def _get_active_api_model(base_url: str) -> str | None:
    """Query /v1/models on LLM server and return the first active model ID."""
    try:
        import requests
        url = f"{base_url}/models" if not base_url.endswith("/models") else base_url
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            models_list = res.json().get("data", [])
            if models_list and "id" in models_list[0]:
                return models_list[0]["id"]
    except Exception as e:
        logger.debug("Failed to query active API model: %s", e)
    return None


@register_driver("opencode")
class OpenCodeDriver(HarnessDriver):
    """Driver for OpenCode CLI/Server agent harness."""

    def __init__(self, port: int = DEFAULT_OPENCODE_PORT, provider_id: str = "sparky"):
        self.port = port
        self.provider_id = provider_id
        self._log_path = "/tmp/opencode_server.log"

    # ----- HarnessDriver interface -----

    def start(
        self,
        sandbox: SandboxClient,
        workspace: str,
        model_name: str,
        llm_base_url: str,
    ) -> str:
        """Configure OpenCode and start its server in the sandbox workspace."""
        active_model = _get_active_api_model(llm_base_url) or model_name
        logger.info(
            "Configured OpenCode with API model ID: '%s' (config alias: '%s')",
            active_model, model_name,
        )

        # Build opencode.json config
        self._write_config(sandbox, active_model, model_name, llm_base_url)

        # Start server
        self._start_server(sandbox, workspace)

        return active_model

    def create_session(self, sandbox: SandboxClient) -> str:
        """Create a new OpenCode session via API."""
        script = f"""import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:{self.port}/session', data=b'{{}}', headers={{'Content-Type': 'application/json'}})
res = urllib.request.urlopen(req, timeout=10)
data = json.loads(res.read().decode('utf-8'))
print(data.get('id', ''))
"""
        res = sandbox.exec_python(script)
        if res.returncode != 0 or not res.stdout.strip():
            raise RuntimeError(f"Failed to create OpenCode session: {res.stderr}\n{res.stdout}")
        session_id = res.stdout.strip()
        logger.info("OpenCode session created: %s", session_id)
        return session_id

    def send_prompt(
        self,
        sandbox: SandboxClient,
        session_id: str,
        prompt: str,
        model_name: str,
        timeout: int,
    ) -> TurnData:
        """Send prompt to OpenCode, wait for response, return normalized TurnData."""
        # Get message count before sending
        msgs_before = self._get_messages(sandbox, session_id)
        prev_count = len(msgs_before)

        step_start_iso = datetime.now(timezone.utc).isoformat()

        # Send the message
        payload_json = json.dumps({
            "parts": [{"type": "text", "text": prompt}],
            "model": {
                "providerID": self.provider_id,
                "modelID": model_name,
            },
        })
        script = f"""import urllib.request, json, sys
payload = sys.stdin.read().encode('utf-8')
req = urllib.request.Request(
    'http://127.0.0.1:{self.port}/session/{session_id}/message',
    data=payload,
    headers={{'Content-Type': 'application/json'}}
)
try:
    res = urllib.request.urlopen(req, timeout={timeout})
    print("__JSON_START__" + res.read().decode('utf-8') + "__JSON_END__")
except urllib.error.HTTPError as e:
    err_body = e.read().decode('utf-8', errors='replace')
    print(f"HTTP_ERROR:{{e.code}}:{{err_body}}", file=sys.stderr)
    sys.exit(1)
"""
        agent_response = sandbox.exec_python_json(script, stdin=payload_json, label="OpenCode message")

        # Get messages after, compute delta
        msgs_after = self._get_messages(sandbox, session_id)
        step_messages = msgs_after[prev_count:] if len(msgs_after) > prev_count else [agent_response]

        # Parse into normalized TurnData
        return self._parse_turn(step_messages, step_start_iso)

    def get_session_info(self, sandbox: SandboxClient, session_id: str) -> dict[str, Any]:
        """Retrieve session metadata from OpenCode."""
        script = f"""import urllib.request, json
try:
    req = urllib.request.Request('http://127.0.0.1:{self.port}/session/{session_id}')
    res = urllib.request.urlopen(req, timeout=30)
    data = json.loads(res.read().decode('utf-8'))
    print("__JSON_START__" + json.dumps(data) + "__JSON_END__")
except Exception as e:
    print(f"ERROR:{{e}}", file=sys.stderr)
"""
        try:
            return sandbox.exec_python_json(script, label="session info")
        except RuntimeError:
            return {}

    def get_all_messages(self, sandbox: SandboxClient, session_id: str) -> list[dict[str, Any]]:
        """Retrieve full message history."""
        return self._get_messages(sandbox, session_id)

    def get_server_log(self, sandbox: SandboxClient) -> str:
        """Retrieve OpenCode server log."""
        return sandbox.read_file(self._log_path)

    @property
    def server_log_filename(self) -> str:
        return "opencode_server.log"

    # ----- Internal helpers -----

    def _get_messages(self, sandbox: SandboxClient, session_id: str) -> list[dict[str, Any]]:
        """Retrieve all messages from an OpenCode session."""
        script = f"""import urllib.request, json
try:
    req = urllib.request.Request('http://127.0.0.1:{self.port}/session/{session_id}/message')
    res = urllib.request.urlopen(req, timeout=30)
    data = json.loads(res.read().decode('utf-8'))
    print("__JSON_START__" + json.dumps(data) + "__JSON_END__")
except Exception as e:
    print(f"ERROR:{{e}}", file=sys.stderr)
"""
        try:
            return sandbox.exec_python_json(script, label="session messages")
        except RuntimeError:
            return []

    def _write_config(
        self,
        sandbox: SandboxClient,
        active_model: str,
        config_alias: str,
        llm_base_url: str,
    ) -> None:
        """Write opencode.json configuration inside the sandbox."""
        model_entry = {
            "name": active_model,
            "modalities": {"input": ["text", "image"], "output": ["text"]},
        }
        models_config = {active_model: model_entry}
        if active_model != config_alias:
            models_config[config_alias] = model_entry

        config_data = {
            "$schema": "https://opencode.ai/config.json",
            "share": "disabled",
            "permission": {"*": "allow"},
            "provider": {
                self.provider_id: {
                    "name": self.provider_id,
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {"baseURL": llm_base_url, "apiKey": "dummy"},
                    "models": models_config,
                },
            },
        }
        config_json = json.dumps(config_data, indent=2)
        setup_cmd = f"mkdir -p ~/.config/opencode && cat << 'EOF' > ~/.config/opencode/opencode.json\n{config_json}\nEOF"
        sandbox.exec(setup_cmd)

    def _start_server(self, sandbox: SandboxClient, workspace: str) -> None:
        """Start OpenCode server and wait for it to become responsive."""
        logger.info("Starting OpenCode server in %s on port %d...", workspace, self.port)
        run_cmd = (
            f"killall opencode 2>/dev/null || true; "
            f"export PATH=$HOME/.opencode/bin:$HOME/.local/bin:$PATH; "
            f"cd {workspace} && (nohup opencode serve --port {self.port} --hostname 0.0.0.0 "
            f"--print-logs --log-level DEBUG </dev/null >{self._log_path} 2>&1 & disown)"
        )
        sandbox.exec(run_cmd)

        # Poll until responsive
        check_script = f"""import urllib.request, sys
try:
    res = urllib.request.urlopen('http://127.0.0.1:{self.port}/session', timeout=1)
    sys.exit(0)
except Exception:
    sys.exit(1)
"""
        for _ in range(30):
            res = sandbox.exec_python(check_script)
            if res.returncode == 0:
                logger.info("✓ OpenCode server responsive at http://127.0.0.1:%d", self.port)
                return
            time.sleep(0.5)

        log_content = sandbox.read_file(self._log_path, max_lines=100)
        logger.error("OpenCode server failed to start. Logs:\n%s", log_content)
        raise RuntimeError("OpenCode server failed to start inside sandbox.")

    def _parse_turn(self, step_messages: list[dict], step_start_iso: str) -> TurnData:
        """Parse OpenCode messages into normalized TurnData."""
        turn = TurnData(raw_messages=step_messages)

        for msg in step_messages:
            msg_time = msg.get("info", {}).get("time", {})
            msg_created = msg_time.get("created")
            msg_iso = (
                datetime.fromtimestamp(msg_created / 1000.0, timezone.utc).isoformat()
                if msg_created else step_start_iso
            )

            for p in msg.get("parts", []):
                p_type = p.get("type")

                if p_type == "reasoning" and p.get("text"):
                    text = p["text"]
                    turn.reasoning.append(text)
                    turn.events.append({"type": "reasoning", "timestamp": msg_iso, "content": text})

                elif p_type == "text" and p.get("text") and msg.get("info", {}).get("role") == "assistant":
                    text = p["text"]
                    turn.text.append(text)
                    turn.events.append({"type": "response", "timestamp": msg_iso, "content": text})

                elif p_type == "tool":
                    state = p.get("state", {})
                    t_time = state.get("time", {})
                    dur_ms = (
                        (t_time.get("end", 0) - t_time.get("start", 0))
                        if t_time and t_time.get("end") else None
                    )
                    tool_iso = (
                        datetime.fromtimestamp(t_time["start"] / 1000.0, timezone.utc).isoformat()
                        if t_time and t_time.get("start") else msg_iso
                    )

                    tc = ToolCallEvent(
                        tool=p.get("tool", ""),
                        call_id=p.get("callID"),
                        status=state.get("status"),
                        input=state.get("input"),
                        output=state.get("output"),
                        exit_code=state.get("metadata", {}).get("exit"),
                        duration_ms=dur_ms,
                        timestamp=tool_iso,
                    )
                    turn.tool_calls.append(tc)
                    turn.events.append({"type": "tool", "timestamp": tool_iso, "data": tc.to_dict()})

                elif p_type == "step-finish":
                    toks = p.get("tokens", {})
                    turn.tokens_in += toks.get("input", 0)
                    turn.tokens_out += toks.get("output", 0)

        # Promote final reasoning to response if no explicit text was emitted
        if not turn.text and turn.reasoning:
            for i in range(len(turn.events) - 1, -1, -1):
                if turn.events[i]["type"] == "reasoning":
                    turn.events[i]["type"] = "response"
                    turn.text.append(turn.events[i]["content"])
                    break

        return turn
