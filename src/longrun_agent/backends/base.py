"""Backend abstraction for executing one agent session."""

from __future__ import annotations

from abc import ABC, abstractmethod

from longrun_agent.runtime.contracts import AgentRunRequest, AgentRunResult


class AgentBackend(ABC):
    """Adapter interface implemented by each backend runtime."""

    name: str

    @abstractmethod
    def run(self, request: AgentRunRequest) -> AgentRunResult:
        """Run one session and return normalized execution metadata."""

