import stat
import sys
from pathlib import Path

from longrun_agent.backends.codex_cli import CodexCliBackend
from longrun_agent.runtime.contracts import AgentRunRequest


def _write_agent_script(path: Path) -> Path:
    script = path / "fake_codex_backend_agent.py"
    script.write_text(
        """import sys
from pathlib import Path

project_dir = Path(sys.argv[1])
phase = sys.argv[2]
prompt_file = Path(sys.argv[3])
print(f"phase={phase}")
print(f"prompt={prompt_file.read_text().strip()}")
(project_dir / "backend_marker.txt").write_text("ok")
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_codex_backend_runs_command_and_persists_logs(tmp_path: Path) -> None:
    session_dir = tmp_path / ".longrun" / "sessions" / "session-0001"
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = session_dir / "prompt.md"
    prompt_file.write_text("do work")
    script = _write_agent_script(tmp_path)

    backend = CodexCliBackend(
        project_dir=tmp_path,
        command_template=[sys.executable, str(script), "{project_dir}", "{phase}", "{prompt_file}"],
    )
    result = backend.run(
        AgentRunRequest(
            phase="coding",
            project_dir=tmp_path,
            prompt_file=prompt_file,
            session_dir=session_dir,
            timeout_seconds=30,
        )
    )

    assert result.backend == "codex_cli"
    assert result.return_code == 0
    assert result.timeout is False
    assert (session_dir / "agent.command.txt").exists()
    assert "phase=coding" in (session_dir / "agent.stdout.log").read_text()
    assert (tmp_path / "backend_marker.txt").read_text() == "ok"


def test_codex_backend_reports_timeout(tmp_path: Path) -> None:
    session_dir = tmp_path / ".longrun" / "sessions" / "session-0002"
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = session_dir / "prompt.md"
    prompt_file.write_text("timeout")

    backend = CodexCliBackend(
        project_dir=tmp_path,
        command_template=[sys.executable, "-c", "import time; time.sleep(2)"],
    )
    result = backend.run(
        AgentRunRequest(
            phase="coding",
            project_dir=tmp_path,
            prompt_file=prompt_file,
            session_dir=session_dir,
            timeout_seconds=1,
        )
    )

    assert result.timeout is True
    assert result.return_code is None
    assert (session_dir / "agent.stdout.log").exists()
    assert (session_dir / "agent.stderr.log").exists()


def test_codex_backend_renders_backend_model_placeholder(tmp_path: Path) -> None:
    session_dir = tmp_path / ".longrun" / "sessions" / "session-0003"
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = session_dir / "prompt.md"
    prompt_file.write_text("model test")

    backend = CodexCliBackend(
        project_dir=tmp_path,
        command_template=["echo", "{backend_model}"],
    )
    result = backend.run(
        AgentRunRequest(
            phase="coding",
            project_dir=tmp_path,
            prompt_file=prompt_file,
            session_dir=session_dir,
            timeout_seconds=30,
            backend_model="gpt-5.2-codex",
        )
    )

    assert result.return_code == 0
    assert "gpt-5.2-codex" in (session_dir / "agent.command.txt").read_text()


def test_codex_backend_renders_reasoning_effort_placeholder(tmp_path: Path) -> None:
    session_dir = tmp_path / ".longrun" / "sessions" / "session-0004"
    session_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = session_dir / "prompt.md"
    prompt_file.write_text("reasoning test")

    backend = CodexCliBackend(
        project_dir=tmp_path,
        command_template=["echo", "{model_reasoning_effort}"],
    )
    result = backend.run(
        AgentRunRequest(
            phase="coding",
            project_dir=tmp_path,
            prompt_file=prompt_file,
            session_dir=session_dir,
            timeout_seconds=30,
            model_reasoning_effort="xhigh",
        )
    )

    assert result.return_code == 0
    assert "xhigh" in (session_dir / "agent.command.txt").read_text()


def test_codex_backend_injects_reasoning_effort_for_codex_exec(tmp_path: Path) -> None:
    session_dir = tmp_path / ".longrun" / "sessions" / "session-0005"
    prompt_file = session_dir / "prompt.md"
    backend = CodexCliBackend(
        project_dir=tmp_path,
        command_template=["codex", "exec", "-m", "{backend_model}"],
    )
    request = AgentRunRequest(
        phase="coding",
        project_dir=tmp_path,
        prompt_file=prompt_file,
        session_dir=session_dir,
        timeout_seconds=30,
        backend_model="gpt-5.3-codex",
        model_reasoning_effort="xhigh",
    )

    rendered = backend._render_command(request)

    assert rendered[:4] == [
        "codex",
        "exec",
        "-c",
        'model_reasoning_effort="xhigh"',
    ]


def test_codex_backend_can_force_workspace_write_sandbox(tmp_path: Path) -> None:
    session_dir = tmp_path / ".longrun" / "sessions" / "session-0006"
    prompt_file = session_dir / "prompt.md"
    backend = CodexCliBackend(
        project_dir=tmp_path,
        command_template=["codex", "exec", "-m", "{backend_model}"],
    )
    request = AgentRunRequest(
        phase="coding",
        project_dir=tmp_path,
        prompt_file=prompt_file,
        session_dir=session_dir,
        timeout_seconds=30,
        backend_model="gpt-5.3-codex",
        metadata={"force_workspace_write": True},
    )

    rendered = backend._render_command(request)

    assert "--sandbox" in rendered
    idx = rendered.index("--sandbox")
    assert rendered[idx + 1] == "workspace-write"
