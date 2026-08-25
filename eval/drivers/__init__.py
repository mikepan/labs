"""
eval.drivers - Abstract harness driver interface and registry.

Each agent harness (OpenCode, pi.dev, etc.) implements HarnessDriver,
converting its native response format into the common TurnData type
so the orchestrator and full_trace.json stay harness-agnostic.
"""

from abc import ABC, abstractmethod
from typing import Any

from eval.config import DEFAULT_IDLE_TIMEOUT_MINUTES, DEFAULT_MAX_STEP_TIMEOUT_MINUTES
from eval.sandbox import SandboxClient
from eval.trace import TurnData

__all__ = ["HarnessDriver", "get_driver"]


class HarnessDriver(ABC):
    """Interface that all agent harnesses must implement."""

    @abstractmethod
    def start(
        self,
        sandbox: SandboxClient,
        workspace: str,
        model_name: str,
        llm_base_url: str,
    ) -> str:
        """Configure and start the agent server in the sandbox.

        Returns the active model ID to use for subsequent requests.
        """

    @abstractmethod
    def create_session(self, sandbox: SandboxClient) -> str:
        """Create a new agent session. Returns session_id."""

    @abstractmethod
    def send_prompt(
        self,
        sandbox: SandboxClient,
        session_id: str,
        prompt: str,
        model_name: str,
        timeout: int = int(DEFAULT_MAX_STEP_TIMEOUT_MINUTES * 60),
        idle_timeout: int = int(DEFAULT_IDLE_TIMEOUT_MINUTES * 60),
    ) -> TurnData:
        """Send a prompt, wait for completion, return normalized TurnData."""

    @abstractmethod
    def get_session_info(self, sandbox: SandboxClient, session_id: str) -> dict[str, Any]:
        """Retrieve session metadata."""

    @abstractmethod
    def get_all_messages(self, sandbox: SandboxClient, session_id: str) -> list[dict[str, Any]]:
        """Retrieve full message history for the session."""

    @abstractmethod
    def get_server_log(self, sandbox: SandboxClient) -> str:
        """Retrieve agent server log content."""

    @property
    @abstractmethod
    def server_log_filename(self) -> str:
        """Filename for this harness's server log (e.g. 'opencode_server.log')."""


# ----- Driver registry -----

_DRIVERS: dict[str, type[HarnessDriver]] = {}


def register_driver(name: str):
    """Decorator to register a HarnessDriver implementation."""
    def decorator(cls: type[HarnessDriver]):
        _DRIVERS[name.lower()] = cls
        return cls
    return decorator


def get_driver(harness_name: str) -> HarnessDriver:
    """Look up and instantiate a driver by harness name.

    Triggers lazy import of driver modules to populate the registry.
    """
    # Lazy-import known driver modules so they self-register
    if not _DRIVERS:
        import eval.drivers.opencode  # noqa: F401
        import eval.drivers.pi  # noqa: F401

    key = harness_name.lower().strip()
    # Fuzzy match: 'opencode cli' -> 'opencode'
    for registered_name, driver_cls in _DRIVERS.items():
        if key.startswith(registered_name) or registered_name.startswith(key):
            return driver_cls()

    available = ", ".join(_DRIVERS.keys()) or "(none)"
    raise ValueError(f"Unknown harness '{harness_name}'. Available: {available}")
