from pathlib import Path

from longrun_agent.runtime.contracts import AgentRunRequest, AgentRunResult


def test_agent_run_result_contract_has_required_fields(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-0001"
    session_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = session_dir / "agent.stdout.log"
    stderr_path = session_dir / "agent.stderr.log"
    stdout_path.write_text("ok")
    stderr_path.write_text("")

    result = AgentRunResult(
        backend="codex_cli",
        return_code=0,
        timeout=False,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        metadata={"phase": "coding"},
    )

    assert result.backend == "codex_cli"
    assert result.return_code == 0
    assert result.timeout is False
    assert result.stdout_path == stdout_path
    assert result.stderr_path == stderr_path
    assert result.metadata["phase"] == "coding"


def test_agent_run_request_includes_expected_context(tmp_path: Path) -> None:
    request = AgentRunRequest(
        phase="initializer",
        project_dir=tmp_path,
        prompt_file=tmp_path / "prompt.md",
        session_dir=tmp_path / "session-0001",
        timeout_seconds=120,
    )

    assert request.phase == "initializer"
    assert request.project_dir == tmp_path
    assert request.timeout_seconds == 120

