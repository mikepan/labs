"""
eval.drivers.pi - Pi Coding Agent harness driver.

Implements HarnessDriver for the Pi agent (https://pi.dev/docs/latest/rpc),
handling configuration, session management, prompt execution via RPC,
and normalized trace conversion.
"""

import json
import time
from datetime import datetime, timezone
from typing import Any

from eval.common import setup_logger, get_active_api_model, get_vllm_model_info
from eval.config import (
    DEFAULT_IDLE_TIMEOUT_MINUTES,
    DEFAULT_MAX_STEP_TIMEOUT_MINUTES,
    DEFAULT_PI_PORT,
)
from eval.drivers import HarnessDriver, register_driver
from eval.sandbox import SandboxClient
from eval.trace import ToolCallEvent, TurnData

__all__ = ["PiDriver"]

logger = setup_logger("driver.pi")


@register_driver("pi")
class PiDriver(HarnessDriver):
    """Driver for Pi coding agent harness (https://pi.dev/docs/latest/rpc)."""

    def __init__(self, port: int = DEFAULT_PI_PORT, provider_id: str = "sparky"):
        self.port = port
        self.provider_id = provider_id
        self._log_path = "/tmp/pi_server.log"
        self._server_script_path = "/tmp/pi_server.py"

    # ----- HarnessDriver interface -----

    def start(
        self,
        sandbox: SandboxClient,
        workspace: str,
        model_name: str,
        llm_base_url: str,
        reasoning_effort: str | None = None,
    ) -> str:
        """Configure Pi and start the Pi RPC bridge server inside the sandbox."""
        model_info = get_vllm_model_info(llm_base_url)
        active_model = (model_info.get("id") if model_info else None) or get_active_api_model(llm_base_url) or model_name
        max_context = int(model_info.get("max_model_len", 262144)) if model_info else 262144

        logger.info(
            "Configured Pi with API model ID: '%s' (config alias: '%s', context: %d, reasoning_effort: '%s')",
            active_model, model_name, max_context, reasoning_effort,
        )

        # Write ~/.pi/agent configs (models.json, settings.json, trust.json)
        self._write_config(sandbox, active_model, model_name, llm_base_url, max_context=max_context, reasoning_effort=reasoning_effort)

        # Start bridge server
        self._start_server(sandbox, workspace, active_model, reasoning_effort=reasoning_effort)

        return active_model


    def create_session(self, sandbox: SandboxClient) -> str:
        """Create a new Pi session via the bridge server."""
        script = f"""import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:{self.port}/session', data=b'{{}}', headers={{'Content-Type': 'application/json'}})
res = urllib.request.urlopen(req, timeout=15)
data = json.loads(res.read().decode('utf-8'))
print(data.get('id', ''))
"""
        res = sandbox.exec_python(script)
        if res.returncode != 0 or not res.stdout.strip():
            raise RuntimeError(f"Failed to create Pi session: {res.stderr}\n{res.stdout}")
        session_id = res.stdout.strip()
        logger.info("Pi session created: %s", session_id)
        return session_id

    def send_prompt(
        self,
        sandbox: SandboxClient,
        session_id: str,
        prompt: str,
        model_name: str,
        timeout: int = int(DEFAULT_MAX_STEP_TIMEOUT_MINUTES * 60),
        idle_timeout: int = int(DEFAULT_IDLE_TIMEOUT_MINUTES * 60),
        reasoning_effort: str | None = None,
    ) -> TurnData:
        """Send prompt to Pi, wait for completion, return normalized TurnData."""
        step_start_iso = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps({
            "prompt": prompt,
            "model": model_name,
            "timeout": timeout,
            "idle_timeout": idle_timeout,
            "reasoning_effort": reasoning_effort,
        })
        script = f"""import urllib.request, urllib.error, socket, json, sys
payload = sys.stdin.read().encode('utf-8')
req = urllib.request.Request(
    'http://127.0.0.1:{self.port}/session/{session_id}/message',
    data=payload,
    headers={{'Content-Type': 'application/json'}}
)
try:
    res = urllib.request.urlopen(req, timeout={timeout + 30})
    print("__JSON_START__" + res.read().decode('utf-8') + "__JSON_END__")
except (TimeoutError, socket.timeout):
    print(f"TIMEOUT:Step timed out after {timeout}s", file=sys.stderr)
    sys.exit(1)
except urllib.error.HTTPError as e:
    err_body = e.read().decode('utf-8', errors='replace')
    if e.code == 504:
        print(f"TIMEOUT:{{err_body}}", file=sys.stderr)
    else:
        print(f"HTTP_ERROR:{{e.code}}:{{err_body}}", file=sys.stderr)
    sys.exit(1)
except urllib.error.URLError as e:
    if isinstance(e.reason, (TimeoutError, socket.timeout)) or "timed out" in str(e).lower():
        print(f"TIMEOUT:Step timed out after {timeout}s", file=sys.stderr)
    else:
        print(f"ERROR:{{e}}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"ERROR:{{e}}", file=sys.stderr)
    sys.exit(1)
"""
        response_data = sandbox.exec_python_json(script, stdin=payload_json, label="Pi message")
        turn_dict = response_data.get("turn", response_data)
        return self._turn_dict_to_turndata(turn_dict, step_start_iso)

    def get_session_info(self, sandbox: SandboxClient, session_id: str) -> dict[str, Any]:
        """Retrieve session metadata from Pi."""
        script = f"""import urllib.request, json, sys
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
        """Retrieve full message history from the session."""
        script = f"""import urllib.request, json, sys
try:
    req = urllib.request.Request('http://127.0.0.1:{self.port}/session/{session_id}/messages')
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

    def get_server_log(self, sandbox: SandboxClient) -> str:
        """Retrieve Pi bridge server and agent logs."""
        return sandbox.read_file(self._log_path)

    @property
    def server_log_filename(self) -> str:
        return "pi_server.log"

    # ----- Internal helpers -----

    def _write_config(
        self,
        sandbox: SandboxClient,
        active_model: str,
        config_alias: str,
        llm_base_url: str,
        max_context: int = 262144,
        reasoning_effort: str | None = None,
    ) -> None:
        """Write ~/.pi/agent/models.json, settings.json, and trust.json inside sandbox."""
        is_reasoning = reasoning_effort not in ("off", "none")
        # For 'xhigh', vLLM's default is already xhigh, but Pi's OpenAI adapter converts xhigh -> 'high' (which vLLM rejects with 400).
        # Therefore, only enable supportsReasoningEffort for 'low' and 'medium'.
        supports_effort = bool(reasoning_effort and reasoning_effort.lower() in ("low", "medium"))

        model_entry: dict[str, Any] = {
            "id": active_model,
            "name": active_model,
            "reasoning": is_reasoning,
            "maxTokens": 65536,
            "contextWindow": max_context,
        }
        if supports_effort:
            model_entry["defaultReasoningEffort"] = reasoning_effort

        models_list = [model_entry]
        if config_alias and config_alias != active_model:
            alias_entry: dict[str, Any] = {
                "id": config_alias,
                "name": config_alias,
                "reasoning": is_reasoning,
                "maxTokens": 65536,
                "contextWindow": max_context,
            }
            if supports_effort:
                alias_entry["defaultReasoningEffort"] = reasoning_effort
            models_list.append(alias_entry)

        models_data = {
            "providers": {
                self.provider_id: {
                    "baseUrl": llm_base_url,
                    "api": "openai-completions",
                    "apiKey": "dummy",
                    "compat": {
                        "supportsDeveloperRole": False,
                        "supportsReasoningEffort": supports_effort,
                    },
                    "models": models_list,
                }
            }
        }

        settings_data = {
            "defaultProjectTrust": "always",
            "quiet": False,
        }

        trust_data = {
            "trusted": ["/"]
        }

        models_json = json.dumps(models_data, indent=2)
        settings_json = json.dumps(settings_data, indent=2)
        trust_json = json.dumps(trust_data, indent=2)

        setup_cmd = f"""
mkdir -p ~/.pi/agent
cat << 'EOF' > ~/.pi/agent/models.json
{models_json}
EOF
cat << 'EOF' > ~/.pi/agent/settings.json
{settings_json}
EOF
cat << 'EOF' > ~/.pi/agent/trust.json
{trust_json}
EOF
"""
        sandbox.exec(setup_cmd)

    def _get_server_script(self, active_model: str, reasoning_effort: str | None = None) -> str:
        """Generate the standalone Python bridge HTTP server script to run inside sandbox."""
        effort_str = (reasoning_effort or "").strip()
        return f'''#!/usr/bin/env python3
import http.server
import json
import os
import re
import select
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

PORT = {self.port}
PROVIDER = "{self.provider_id}"
DEFAULT_MODEL = "{active_model}"
REASONING_EFFORT = "{effort_str}"

sessions = {{}}
sessions_lock = threading.Lock()


class PiSession:
    def __init__(self, session_id, cwd):
        self.session_id = session_id
        self.cwd = cwd
        self.lock = threading.Lock()
        self.messages = []
        self.turns = []
        self.proc = None
        self._start_process()

    def _start_process(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

        env = dict(os.environ)
        path = env.get("PATH", "")
        home = os.path.expanduser("~")
        extra_paths = [
            "/usr/local/share/npm-global/bin",
            f"{{home}}/.npm-global/bin",
            f"{{home}}/.pi/bin",
            f"{{home}}/.local/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        env["PATH"] = ":".join(extra_paths + [path])

        cmd = [
            "pi",
            "--mode", "rpc",
            "--no-session",
            "--provider", PROVIDER,
            "--model", DEFAULT_MODEL,
        ]
        if REASONING_EFFORT:
            eff = REASONING_EFFORT.strip().lower()
            if eff in ("off", "none", "no"):
                cmd.extend(["--thinking", "off"])
            elif eff in ("minimal", "low", "medium", "high", "xhigh", "max"):
                cmd.extend(["--thinking", eff])

        sys.stderr.write(f"[pi_server] Spawning pi RPC process: {{cmd}} in {{self.cwd}}\\n")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.cwd,
            text=True,
            bufsize=1,
            env=env,
        )

    def _drain_output(self, timeout_sec=0.2):
        """Drain any leftover stdout data to prevent RPC message desynchronization."""
        if not self.proc or not self.proc.stdout:
            return
        while True:
            rlist, _, _ = select.select([self.proc.stdout], [], [], timeout_sec)
            if not rlist:
                break
            line = self.proc.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            if line_str:
                sys.stderr.write(f"[pi_rpc_drain] {{line_str[:200]}}\\n")

    def send_prompt(self, prompt, timeout=7200, idle_timeout=360, reasoning_effort=None):
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                sys.stderr.write(f"[pi_server] Respawning died pi process\\n")
                self._start_process()

            # Pre-prompt: drain any leftover trailing output
            self._drain_output(timeout_sec=0.1)

            req = {{"type": "prompt", "message": prompt}}
            sys.stderr.write(f"[pi_server] Sending prompt (timeout={{timeout}}s, idle_timeout={{idle_timeout}}s): {{prompt[:80]}}...\\n")
            try:
                self.proc.stdin.write(json.dumps(req) + "\\n")
                self.proc.stdin.flush()
            except Exception as e:
                sys.stderr.write(f"[pi_server] Error writing to stdin: {{e}}, respawning pi process...\\n")
                self._start_process()
                self.proc.stdin.write(json.dumps(req) + "\\n")
                self.proc.stdin.flush()

            events = []
            text_chunks = []
            reasoning_chunks = []
            tool_calls_map = {{}}
            tokens_in = 0
            tokens_out = 0
            peak_context_tokens = 0
            raw_messages = []
            start_time = time.time()
            last_activity_time = time.time()
            step_start_iso = datetime.now(timezone.utc).isoformat()

            agent_started = False
            try:
                while True:
                    elapsed = time.time() - start_time
                    idle_elapsed = time.time() - last_activity_time
                    if elapsed > timeout:
                        sys.stderr.write(f"[pi_server] Max timeout reached ({{elapsed:.1f}}s > {{timeout}}s), aborting and resetting\\n")
                        try:
                            self.proc.stdin.write(json.dumps({{"type": "abort"}}) + "\\n")
                            self.proc.stdin.flush()
                        except Exception:
                            pass
                        self._start_process()
                        raise TimeoutError(f"Pi execution exceeded maximum ceiling of {{timeout // 60}} minutes ({{elapsed:.1f}}s elapsed).")

                    if idle_elapsed > idle_timeout:
                        sys.stderr.write(f"[pi_server] Idle timeout reached ({{idle_elapsed:.1f}}s > {{idle_timeout}}s with no progress), aborting and resetting\\n")
                        try:
                            self.proc.stdin.write(json.dumps({{"type": "abort"}}) + "\\n")
                            self.proc.stdin.flush()
                        except Exception:
                            pass
                        self._start_process()
                        raise TimeoutError(f"Agent stalled: No activity received for {{idle_elapsed:.1f}}s (idle timeout of {{idle_timeout}}s based on max prefill).")

                    # Non-blocking or timed read
                    rlist, _, _ = select.select([self.proc.stdout], [], [], 1.0)
                    if not rlist:
                        if self.proc.poll() is not None:
                            raise RuntimeError(f"Pi process exited prematurely with code {{self.proc.returncode}}")
                        continue

                    line = self.proc.stdout.readline()
                    if not line:
                        if self.proc.poll() is not None:
                            raise RuntimeError(f"Pi process closed stdout with code {{self.proc.returncode}}")
                        continue

                    line_str = line.strip()
                    if not line_str:
                        continue

                    # Any line received indicates active progress from agent
                    last_activity_time = time.time()

                    # Log raw line to stderr for logging
                    sys.stderr.write(f"[pi_rpc] {{line_str[:200]}}\\n")

                    try:
                        event = json.loads(line_str)
                    except Exception:
                        continue

                    events.append(event)
                    e_type = event.get("type")

                    if e_type == "response":
                        cmd = event.get("command")
                        if cmd == "prompt" and not event.get("success"):
                            err_msg = event.get("error", "Prompt command failed")
                            sys.stderr.write(f"[pi_server] Pi prompt error: {{err_msg}}\\n")
                            if "already processing" in err_msg.lower():
                                sys.stderr.write(f"[pi_server] Pi stuck in active processing state, resetting process...\\n")
                                try:
                                    self.proc.stdin.write(json.dumps({{"type": "abort"}}) + "\\n")
                                    self.proc.stdin.flush()
                                except Exception:
                                    pass
                                self._start_process()
                            raise RuntimeError(f"Pi prompt command rejected: {{err_msg}}")

                    elif e_type == "agent_start":
                        agent_started = True

                    elif e_type == "message_update":
                        ame = event.get("assistantMessageEvent", {{}})
                        ame_type = ame.get("type")
                        if ame_type == "text_delta":
                            text_chunks.append(ame.get("delta", ""))
                        elif ame_type == "thinking_delta":
                            reasoning_chunks.append(ame.get("delta", ""))

                    elif e_type == "tool_execution_start":
                        call_id = event.get("toolCallId") or str(uuid.uuid4())
                        tool_calls_map[call_id] = {{
                            "tool": event.get("toolName", ""),
                            "call_id": call_id,
                            "status": "running",
                            "input": json.dumps(event.get("args", {{}})),
                            "output": "",
                            "exit_code": None,
                            "start_time": time.time(),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }}

                    elif e_type == "tool_execution_update":
                        call_id = event.get("toolCallId")
                        if call_id and call_id in tool_calls_map:
                            partial = event.get("partialResult", {{}})
                            text_out = "".join(
                                c.get("text", "")
                                for c in partial.get("content", [])
                                if isinstance(c, dict)
                            )
                            tool_calls_map[call_id]["output"] = text_out

                    elif e_type == "tool_execution_end":
                        call_id = event.get("toolCallId")
                        if call_id and call_id in tool_calls_map:
                            res_obj = event.get("result", {{}})
                            text_out = "".join(
                                c.get("text", "")
                                for c in res_obj.get("content", [])
                                if isinstance(c, dict)
                            )
                            t_start = tool_calls_map[call_id].get("start_time", time.time())
                            dur_ms = int((time.time() - t_start) * 1000)
                            is_err = bool(event.get("isError"))
                            tool_calls_map[call_id]["status"] = "error" if is_err else "completed"
                            tool_calls_map[call_id]["exit_code"] = 1 if is_err else 0
                            tool_calls_map[call_id]["output"] = text_out
                            tool_calls_map[call_id]["duration_ms"] = dur_ms

                    elif e_type == "turn_end":
                        msg = event.get("message", {{}})
                        if msg:
                            raw_messages.append(msg)
                            for c in msg.get("content", []):
                                if isinstance(c, dict):
                                    if c.get("type") == "text" and c.get("text"):
                                        if not text_chunks:
                                            text_chunks.append(c["text"])
                                    elif c.get("type") == "thinking" and c.get("thinking"):
                                        if not reasoning_chunks:
                                            reasoning_chunks.append(c["thinking"])
                            usage = msg.get("usage", {{}})
                            if usage:
                                u_in = usage.get("input", 0)
                                u_out = usage.get("output", 0)
                                tokens_in += u_in
                                tokens_out += u_out
                                peak_context_tokens = max(peak_context_tokens, u_in + u_out)

                    elif e_type == "agent_end":
                        gen_msgs = event.get("messages", [])
                        if gen_msgs:
                            raw_messages.extend(gen_msgs)
                        if agent_started and not event.get("willRetry"):
                            sys.stderr.write(f"[pi_server] Received agent_end (willRetry=False). Turn complete.\\n")
                            break

                    elif e_type == "agent_settled":
                        if agent_started:
                            sys.stderr.write(f"[pi_server] Received agent_settled. Turn complete.\\n")
                            break
                        else:
                            sys.stderr.write(f"[pi_server] Ignored trailing agent_settled before agent_start.\\n")

                # Drain trailing events so subsequent turns start clean
                self._drain_output(timeout_sec=0.1)

            except Exception:
                try:
                    self.proc.stdin.write(json.dumps({{"type": "abort"}}) + "\\n")
                    self.proc.stdin.flush()
                except Exception:
                    pass
                self._start_process()
                raise

            # Build chronological events and outputs from raw_messages
            norm_events = []
            text_list = []
            reasoning_list = []

            for msg in raw_messages:
                m_role = msg.get("role")
                m_content = msg.get("content", [])
                m_ts = datetime.now(timezone.utc).isoformat()

                if isinstance(m_content, str):
                    if m_role == "assistant" and m_content.strip():
                        norm_events.append({{"type": "response", "timestamp": m_ts, "content": m_content.strip()}})
                        text_list.append(m_content.strip())
                elif isinstance(m_content, list):
                    for part in m_content:
                        if not isinstance(part, dict):
                            continue
                        p_type = part.get("type")
                        if p_type == "thinking" and part.get("thinking"):
                            th = part["thinking"].strip()
                            if th:
                                norm_events.append({{"type": "reasoning", "timestamp": m_ts, "content": th}})
                                reasoning_list.append(th)
                        elif p_type == "text" and part.get("text"):
                            tx = part["text"].strip()
                            if tx and m_role == "assistant":
                                norm_events.append({{"type": "response", "timestamp": m_ts, "content": tx}})
                                text_list.append(tx)
                        elif p_type == "toolCall":
                            cid = part.get("id")
                            tc_info = tool_calls_map.get(cid, {{
                                "tool": part.get("name", "tool"),
                                "call_id": cid,
                                "status": "completed",
                                "input": json.dumps(part.get("arguments", {{}})),
                                "output": "",
                                "exit_code": 0,
                                "timestamp": m_ts,
                            }})
                            norm_events.append({{"type": "tool", "timestamp": tc_info.get("timestamp", m_ts), "data": tc_info}})

            tool_calls_list = list(tool_calls_map.values())
            if not norm_events:
                full_text = "".join(text_chunks).strip()
                full_reasoning = "".join(reasoning_chunks).strip()
                if full_reasoning:
                    norm_events.append({{"type": "reasoning", "timestamp": step_start_iso, "content": full_reasoning}})
                    reasoning_list.append(full_reasoning)
                for tc in tool_calls_list:
                    norm_events.append({{"type": "tool", "timestamp": tc.get("timestamp", step_start_iso), "data": tc}})
                if full_text:
                    norm_events.append({{"type": "response", "timestamp": step_start_iso, "content": full_text}})
                    text_list.append(full_text)

            turn_data = {{
                "reasoning": reasoning_list,
                "text": text_list,
                "tool_calls": tool_calls_list,
                "events": norm_events,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "peak_context_tokens": peak_context_tokens,
                "raw_messages": raw_messages if raw_messages else [{{
                    "role": "assistant",
                    "content": [{{"type": "text", "text": full_text}}] if full_text else [],
                }}],
            }}

            self.turns.append(turn_data)
            self.messages.append({{"role": "user", "content": prompt}})
            self.messages.extend(raw_messages)

            return turn_data


class RequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write("[pi_http] " + (format % args) + "\\n")

    def do_GET(self):
        if self.path == "/health":
            self._send_json({{"status": "ok", "port": PORT}})
        elif self.path.startswith("/session/"):
            parts = self.path.strip("/").split("/")
            session_id = parts[1]
            with sessions_lock:
                session = sessions.get(session_id)
            if not session:
                self.send_error(404, "Session not found")
                return

            if len(parts) == 2:
                # /session/<id>
                self._send_json({{
                    "id": session_id,
                    "cwd": session.cwd,
                    "turns_count": len(session.turns),
                    "messages_count": len(session.messages),
                }})
            elif len(parts) == 3 and parts[2] in ("message", "messages"):
                # /session/<id>/messages
                self._send_json(session.messages)
            else:
                self.send_error(404, "Unknown endpoint")
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{{}}"
        try:
            data = json.loads(body)
        except Exception:
            data = {{}}

        if self.path == "/session":
            session_id = str(uuid.uuid4())
            cwd = data.get("workspace") or os.getcwd()
            session = PiSession(session_id, cwd)
            with sessions_lock:
                sessions[session_id] = session
            self._send_json({{"id": session_id}})

        elif self.path.startswith("/session/") and self.path.endswith("/message"):
            parts = self.path.strip("/").split("/")
            session_id = parts[1]
            with sessions_lock:
                session = sessions.get(session_id)
            if not session:
                self.send_error(404, "Session not found")
                return

            prompt = data.get("prompt")
            if not prompt and "parts" in data:
                prompt = "\\n".join(p.get("text", "") for p in data.get("parts", []) if p.get("type") == "text")
            timeout = int(data.get("timeout", 7200))
            idle_timeout = int(data.get("idle_timeout", 360))

            reasoning_effort = data.get("reasoning_effort")

            try:
                turn = session.send_prompt(prompt, timeout=timeout, idle_timeout=idle_timeout, reasoning_effort=reasoning_effort)
                self._send_json({{"status": "ok", "turn": turn}})
            except TimeoutError as te:
                self.send_error(504, str(te))
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_error(404, "Not found")

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = http.server.HTTPServer(("0.0.0.0", PORT), RequestHandler)
    sys.stderr.write(f"[pi_server] Listening on port {{PORT}}...\\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with sessions_lock:
            for s in sessions.values():
                if s.proc:
                    try:
                        s.proc.terminate()
                    except Exception:
                        pass


if __name__ == "__main__":
    main()
'''

    def _start_server(
        self,
        sandbox: SandboxClient,
        workspace: str,
        active_model: str,
        reasoning_effort: str | None = None,
    ) -> None:
        """Deploy server script, start pi_server daemon, and wait for it to become responsive."""
        logger.info("Starting Pi bridge server in %s on port %d...", workspace, self.port)

        sandbox.exec("pkill -9 -f '[p]i_server.py' 2>/dev/null || true")

        server_code = self._get_server_script(active_model, reasoning_effort=reasoning_effort)
        sandbox.write_file(self._server_script_path, server_code)
        sandbox.exec(f"chmod +x {self._server_script_path}")

        daemon_cmd = (
            f"python3 -c \""
            f"import subprocess; "
            f"subprocess.Popen(['python3', '{self._server_script_path}'], "
            f"stdout=open('{self._log_path}', 'a'), stderr=subprocess.STDOUT, "
            f"start_new_session=True, cwd='{workspace}')\""
        )
        sandbox.exec(daemon_cmd)

        # Poll until responsive
        check_script = f"""import urllib.request, sys
try:
    res = urllib.request.urlopen('http://127.0.0.1:{self.port}/health', timeout=1)
    sys.exit(0)
except Exception:
    sys.exit(1)
"""
        for _ in range(30):
            res = sandbox.exec_python(check_script)
            if res.returncode == 0:
                logger.info("✓ Pi bridge server responsive at http://127.0.0.1:%d", self.port)
                return
            time.sleep(0.5)

        log_content = sandbox.read_file(self._log_path, max_lines=100)
        logger.error("Pi server failed to start. Logs:\n%s", log_content)
        raise RuntimeError("Pi bridge server failed to start inside sandbox.")

    def _turn_dict_to_turndata(self, turn_dict: dict[str, Any], step_start_iso: str) -> TurnData:
        """Convert serialized turn dictionary into TurnData dataclass."""
        tool_calls = []
        for tc_raw in turn_dict.get("tool_calls", []):
            tc = ToolCallEvent(
                tool=tc_raw.get("tool", ""),
                call_id=tc_raw.get("call_id"),
                status=tc_raw.get("status"),
                input=tc_raw.get("input"),
                output=tc_raw.get("output"),
                exit_code=tc_raw.get("exit_code"),
                duration_ms=tc_raw.get("duration_ms"),
                timestamp=tc_raw.get("timestamp", step_start_iso),
            )
            tool_calls.append(tc)

        return TurnData(
            tool_calls=tool_calls,
            reasoning=turn_dict.get("reasoning", []),
            text=turn_dict.get("text", []),
            events=turn_dict.get("events", []),
            tokens_in=turn_dict.get("tokens_in", 0),
            tokens_out=turn_dict.get("tokens_out", 0),
            peak_context_tokens=turn_dict.get("peak_context_tokens", 0),
            raw_messages=turn_dict.get("raw_messages", []),
        )

    def _parse_turn(self, step_messages: list[dict], step_start_iso: str) -> TurnData:
        """Fallback parse of raw messages into TurnData."""
        turn = TurnData(raw_messages=step_messages)
        for msg in step_messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, str) and role == "assistant":
                turn.text.append(content)
                turn.events.append({"type": "response", "timestamp": step_start_iso, "content": content})
            elif isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    i_type = item.get("type")
                    if i_type == "text" and item.get("text"):
                        turn.text.append(item["text"])
                        turn.events.append({"type": "response", "timestamp": step_start_iso, "content": item["text"]})
                    elif i_type == "thinking" and item.get("thinking"):
                        turn.reasoning.append(item["thinking"])
                        turn.events.append({"type": "reasoning", "timestamp": step_start_iso, "content": item["thinking"]})
                    elif i_type == "toolCall":
                        tc = ToolCallEvent(
                            tool=item.get("name", ""),
                            call_id=item.get("id"),
                            input=json.dumps(item.get("arguments", {})),
                            timestamp=step_start_iso,
                        )
                        turn.tool_calls.append(tc)
                        turn.events.append({"type": "tool", "timestamp": step_start_iso, "data": tc.to_dict()})

            usage = msg.get("usage", {})
            if usage:
                u_in = usage.get("input", 0)
                u_out = usage.get("output", 0)
                turn.tokens_in += u_in
                turn.tokens_out += u_out
                turn.peak_context_tokens = max(turn.peak_context_tokens, u_in + u_out)

        return turn
