from pathlib import Path

import pytest

from longrun_agent.backends.factory import create_backend
from longrun_agent.config import DEFAULT_CODEX_COMMAND_TEMPLATE


def test_create_backend_returns_codex_backend(tmp_path: Path) -> None:
    backend = create_backend(
        backend_name="codex_cli",
        project_dir=tmp_path,
        command_template=["echo", "ok"],
    )

    assert backend.name == "codex_cli"


def test_create_backend_uses_default_codex_command_when_template_missing(tmp_path: Path) -> None:
    backend = create_backend(
        backend_name="codex_cli",
        project_dir=tmp_path,
    )

    assert backend.name == "codex_cli"
    assert backend.command_template == DEFAULT_CODEX_COMMAND_TEMPLATE


def test_create_backend_returns_claude_backend(tmp_path: Path) -> None:
    backend = create_backend(
        backend_name="claude_sdk",
        project_dir=tmp_path,
        model="claude-sonnet-4-5-20250929",
    )

    assert backend.name == "claude_sdk"


def test_create_backend_rejects_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        create_backend(
            backend_name="unknown",
            project_dir=tmp_path,
        )
