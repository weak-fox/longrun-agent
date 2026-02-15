from pathlib import Path

from longrun_agent.cli import build_parser, run_loop, run_one_session
from longrun_agent.harness import HarnessConfig, SessionResult


def test_parser_accepts_backend_and_profile_overrides() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "run-loop",
            "--max-sessions",
            "3",
            "--continue-on-failure",
            "--backend",
            "claude_sdk",
            "--profile",
            "article",
            "--model-reasoning-effort",
            "xhigh",
        ]
    )

    assert args.command == "run-loop"
    assert args.max_sessions == 3
    assert args.continue_on_failure is True
    assert args.backend == "claude_sdk"
    assert args.profile == "article"
    assert args.model_reasoning_effort == "xhigh"


def test_run_one_session_applies_runtime_overrides(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _load_config(_path: Path) -> HarnessConfig:
        return HarnessConfig(
            project_dir=tmp_path,
            agent_command=["echo", "ok"],
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )

    class _FakeHarness:
        def __init__(self, config: HarnessConfig):
            captured["config"] = config

        def run_session(self):
            return SessionResult(
                session_id=1,
                phase="coding",
                success=True,
                message="ok",
                passing=1,
                total=2,
                progress_made=True,
                return_code=0,
            )

    monkeypatch.setattr("longrun_agent.cli.load_config", _load_config)
    monkeypatch.setattr("longrun_agent.cli.Harness", _FakeHarness)

    code = run_one_session(
        Path("longrun-agent.toml"),
        backend="claude_sdk",
        profile="article",
        backend_model="claude-opus-x",
        model_reasoning_effort="xhigh",
    )
    assert code == 0
    config = captured["config"]
    assert isinstance(config, HarnessConfig)
    assert config.backend_name == "claude_sdk"
    assert config.profile == "article"
    assert config.backend_model == "claude-opus-x"
    assert config.model_reasoning_effort == "xhigh"


def test_run_loop_applies_runtime_overrides(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _load_config(_path: Path) -> HarnessConfig:
        return HarnessConfig(
            project_dir=tmp_path,
            agent_command=["echo", "ok"],
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )

    class _FakeHarness:
        def __init__(self, config: HarnessConfig):
            captured["config"] = config
            self.last_loop_stop_reason = None

        def run_loop(self, max_sessions: int | None = None, continue_on_failure: bool = False):
            captured["max_sessions"] = max_sessions
            captured["continue_on_failure"] = continue_on_failure
            return [
                SessionResult(
                    session_id=1,
                    phase="coding",
                    success=True,
                    message="ok",
                    passing=1,
                    total=2,
                    progress_made=True,
                    return_code=0,
                )
            ]

    monkeypatch.setattr("longrun_agent.cli.load_config", _load_config)
    monkeypatch.setattr("longrun_agent.cli.Harness", _FakeHarness)

    code = run_loop(
        Path("longrun-agent.toml"),
        max_sessions=4,
        continue_on_failure=True,
        backend="claude_sdk",
        profile="article",
        backend_model="claude-opus-x",
        model_reasoning_effort="xhigh",
    )
    assert code == 0
    assert captured["max_sessions"] == 4
    assert captured["continue_on_failure"] is True
    config = captured["config"]
    assert isinstance(config, HarnessConfig)
    assert config.backend_name == "claude_sdk"
    assert config.profile == "article"
    assert config.backend_model == "claude-opus-x"
    assert config.model_reasoning_effort == "xhigh"


def test_run_loop_requires_max_sessions_when_continue_on_failure_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("longrun_agent.cli.load_config", lambda _path: HarnessConfig(
        project_dir=tmp_path,
        agent_command=["echo", "ok"],
        verification_commands=[],
        bearings_commands=[],
        auto_continue_delay_seconds=0,
    ))

    code = run_loop(
        Path("longrun-agent.toml"),
        max_sessions=None,
        continue_on_failure=True,
    )

    assert code == 2


def test_run_one_session_updates_codex_command_model_on_override(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _load_config(_path: Path) -> HarnessConfig:
        return HarnessConfig(
            project_dir=tmp_path,
            agent_command=[
                "codex",
                "exec",
                "-m",
                "old-model",
                "--phase",
                "{phase}",
                "--prompt-file",
                "{prompt_file}",
            ],
            backend_name="codex_cli",
            backend_model="old-model",
            verification_commands=[],
            bearings_commands=[],
            auto_continue_delay_seconds=0,
        )

    class _FakeHarness:
        def __init__(self, config: HarnessConfig):
            captured["config"] = config

        def run_session(self):
            return SessionResult(
                session_id=1,
                phase="coding",
                success=True,
                message="ok",
                passing=1,
                total=2,
                progress_made=True,
                return_code=0,
            )

    monkeypatch.setattr("longrun_agent.cli.load_config", _load_config)
    monkeypatch.setattr("longrun_agent.cli.Harness", _FakeHarness)

    code = run_one_session(
        Path("longrun-agent.toml"),
        backend="codex_cli",
        backend_model="gpt-5.2-codex",
    )
    assert code == 0
    config = captured["config"]
    assert isinstance(config, HarnessConfig)
    assert config.backend_model == "gpt-5.2-codex"
    assert config.agent_command[3] == "gpt-5.2-codex"
