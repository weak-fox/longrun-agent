"""Shared runtime contracts for backend-agnostic orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


SessionPhase = Literal["initializer", "coding", "repair"]


@dataclass(slots=True)
class AgentRunRequest:
    """Canonical input for a single backend session execution."""

    phase: SessionPhase
    project_dir: Path
    prompt_file: Path
    session_dir: Path
    timeout_seconds: int
    backend_model: str | None = None
    model_reasoning_effort: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRunResult:
    """Canonical backend execution output consumed by the orchestrator."""

    backend: str
    return_code: int | None
    timeout: bool
    stdout_path: Path
    stderr_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
