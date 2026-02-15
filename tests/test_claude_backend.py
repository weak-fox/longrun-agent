from pathlib import Path

import pytest

from longrun_agent.backends.claude_sdk import ClaudeSdkBackend
from longrun_agent.runtime.contracts import AgentRunRequest


def test_claude_backend_errors_without_sdk(tmp_path: Path) -> None:
    session_dir = tmp_path / ".longrun" / "sessions" / "session-0001"
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = session_dir / "prompt.md"
    prompt_file.write_text("hello")

    backend = ClaudeSdkBackend(project_dir=tmp_path, model="claude-sonnet-4-5-20250929")

    with pytest.raises(RuntimeError, match="claude-code-sdk is required"):
        backend.run(
            AgentRunRequest(
                phase="coding",
                project_dir=tmp_path,
                prompt_file=prompt_file,
                session_dir=session_dir,
                timeout_seconds=10,
            )
        )

