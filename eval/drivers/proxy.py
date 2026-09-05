"""
eval.drivers.proxy - In-sandbox HTTP proxy for OpenAI-compatible reasoning parameter injection.

NOTE: This proxy was previously needed because OpenCode configured custom OpenAI-compatible
providers with `@ai-sdk/openai-compatible`, which dropped `reasoningEffort` parameters.
It is no longer called/needed because OpenCode is now configured natively with
`"npm": "@ai-sdk/openai"` along with `reasoningEffort` and `forceReasoning: true`.
The file is kept for standalone reference/troubleshooting if needed.
"""

import logging
import os
import time

from eval.sandbox import SandboxClient

logger = logging.getLogger("eval")

DEFAULT_PROXY_PORT = 4098
DEFAULT_PROXY_SCRIPT_PATH = "/tmp/.opencode_proxy.py"
DEFAULT_PROXY_LOG_PATH = "/tmp/opencode_proxy.log"


def get_proxy_script(llm_base_url: str, reasoning_effort: str, proxy_port: int = DEFAULT_PROXY_PORT) -> str:
    """Generate standalone HTTP proxy script to inject reasoning_effort into vLLM completion requests."""
    target_base = llm_base_url.rstrip("/")
    if target_base.endswith("/v1"):
        target_base = target_base[:-3]

    return f'''#!/usr/bin/env python3
import http.server
import json
import socketserver
import sys
import urllib.error
import urllib.request

PORT = {proxy_port}
TARGET_BASE = "{target_base}"
REASONING_EFFORT = "{reasoning_effort}"


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        if self.path.endswith("/chat/completions") and REASONING_EFFORT:
            try:
                data = json.loads(body.decode("utf-8"))
                if REASONING_EFFORT in ("low", "medium", "xhigh"):
                    data["reasoning_effort"] = REASONING_EFFORT
                body = json.dumps(data).encode("utf-8")
            except Exception:
                pass
        target_url = TARGET_BASE + self.path
        req = urllib.request.Request(
            target_url,
            data=body,
            headers={{k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}},
        )
        req.add_header("Content-Length", str(len(body)))
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items():
                if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(500)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'{{"status":"ok"}}')
            return
        target_url = TARGET_BASE + self.path
        req = urllib.request.Request(target_url)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items():
                if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(500)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))


def main():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), ProxyHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
'''


def start_reasoning_proxy(
    sandbox: SandboxClient,
    llm_base_url: str,
    reasoning_effort: str,
    proxy_port: int = DEFAULT_PROXY_PORT,
    script_path: str = DEFAULT_PROXY_SCRIPT_PATH,
    log_path: str = DEFAULT_PROXY_LOG_PATH,
) -> str:
    """Start an in-sandbox HTTP proxy and return its base URL (e.g. http://127.0.0.1:4098/v1)."""
    logger.info(
        "Starting OpenCode reasoning proxy for effort '%s' targeting %s on port %d...",
        reasoning_effort, llm_base_url, proxy_port,
    )

    stop_reasoning_proxy(sandbox, proxy_port=proxy_port, script_path=script_path)

    proxy_code = get_proxy_script(llm_base_url, reasoning_effort, proxy_port=proxy_port)
    sandbox.write_file(script_path, proxy_code)
    sandbox.exec(f"chmod +x {script_path}")

    daemon_cmd = (
        f"python3 -c \""
        f"import subprocess; "
        f"subprocess.Popen(['python3', '{script_path}'], "
        f"stdout=open('{log_path}', 'a'), stderr=subprocess.STDOUT, "
        f"start_new_session=True)\""
    )
    sandbox.exec(daemon_cmd)

    # Poll until responsive
    check_script = f"""import urllib.request, sys
try:
    res = urllib.request.urlopen('http://127.0.0.1:{proxy_port}/health', timeout=1)
    sys.exit(0)
except Exception:
    sys.exit(1)
"""
    for _ in range(30):
        res = sandbox.exec_python(check_script)
        if res.returncode == 0:
            logger.info("✓ OpenCode reasoning proxy responsive at http://127.0.0.1:%d", proxy_port)
            return f"http://127.0.0.1:{proxy_port}/v1"
        time.sleep(0.5)

    log_content = sandbox.read_file(log_path, max_lines=50)
    logger.error("OpenCode reasoning proxy failed to start. Logs:\n%s", log_content)
    raise RuntimeError("OpenCode reasoning proxy failed to start inside sandbox.")


def stop_reasoning_proxy(
    sandbox: SandboxClient,
    proxy_port: int = DEFAULT_PROXY_PORT,
    script_path: str = DEFAULT_PROXY_SCRIPT_PATH,
) -> None:
    """Stop the in-sandbox reasoning proxy if running."""
    script_base = os.path.basename(script_path)
    sandbox.exec(f"pkill -9 -f '{script_base}' 2>/dev/null || true; fuser -k {proxy_port}/tcp 2>/dev/null || true")
