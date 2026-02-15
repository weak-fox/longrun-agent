"""Factory for backend adapter construction."""

from __future__ import annotations

from pathlib import Path

from .base import AgentBackend
from .claude_sdk import ClaudeSdkBackend
from .codex_cli import CodexCliBackend
from ..config import DEFAULT_CODEX_COMMAND_TEMPLATE


def create_backend(
    backend_name: str,
    project_dir: Path,
    command_template: list[str] | None = None,
    model: str | None = None,
) -> AgentBackend:
    """Create a backend adapter by name."""
    if backend_name == "codex_cli":
        return CodexCliBackend(
            project_dir=project_dir,
            command_template=command_template or list(DEFAULT_CODEX_COMMAND_TEMPLATE),
        )

    if backend_name == "claude_sdk":
        return ClaudeSdkBackend(
            project_dir=project_dir,
            model=model or "claude-sonnet-4-5-20250929",
        )

    raise ValueError(f"Unknown backend: {backend_name}")
