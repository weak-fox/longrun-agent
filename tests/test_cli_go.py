from pathlib import Path

from longrun_agent.cli import _resolve_config_path, build_parser, run_go
from longrun_agent.config import load_config


def test_parser_accepts_go_command_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "go",
            "--goal",
            "Build a lightweight kanban board",
            "--max-sessions",
            "7",
            "--backend",
            "codex_cli",
            "--backend-model",
            "gpt-5.3-codex",
            "--feature-target",
            "36",
            "--brainstorm-rounds",
            "3",
            "--non-interactive",
            "--yes",
        ]
    )

    assert args.command == "go"
    assert args.goal == "Build a lightweight kanban board"
    assert args.max_sessions == 7
    assert args.backend == "codex_cli"
    assert args.backend_model == "gpt-5.3-codex"
    assert args.feature_target == 36
    assert args.brainstorm_rounds == 3
    assert args.non_interactive is True
    assert args.yes is True


def test_run_go_non_interactive_runs_goal_setup_and_loop(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "longrun_agent.cli._validate_project_python_environment",
        lambda _project_dir, _allow_any_python: None,
    )

    def _fake_goal_setup(**kwargs):
        captured["goal_setup"] = kwargs

    def _fake_run_loop(
        _config_path: Path,
        max_sessions: int | None,
        backend: str | None = None,
        profile: str | None = None,
        backend_model: str | None = None,
        model_reasoning_effort: str | None = None,
    ) -> int:
        captured["run_loop"] = {
            "max_sessions": max_sessions,
            "backend": backend,
            "profile": profile,
            "backend_model": backend_model,
            "model_reasoning_effort": model_reasoning_effort,
        }
        return 0

    monkeypatch.setattr("longrun_agent.cli._run_goal_setup_for_go", _fake_goal_setup)
    monkeypatch.setattr("longrun_agent.cli.run_loop", _fake_run_loop)

    code = run_go(
        config_path=config_path,
        goal="Build a practical task manager",
        max_sessions=7,
        project_dir=project_dir,
        feature_target=36,
        brainstorm_rounds=3,
        non_interactive=True,
        yes=True,
    )

    assert code == 0
    assert "goal_setup" in captured
    goal_setup = captured["goal_setup"]
    assert isinstance(goal_setup, dict)
    assert goal_setup["goal"] == "Build a practical task manager"
    assert goal_setup["brainstorm_rounds"] == 3
    assert goal_setup["feature_target_override"] == 36
    assert goal_setup["interactive"] is False

    run_loop_args = captured["run_loop"]
    assert isinstance(run_loop_args, dict)
    assert run_loop_args["max_sessions"] == 7

    config = load_config(config_path)
    assert config.project_dir == project_dir.resolve()


def test_run_go_aborts_when_python_environment_check_fails(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"
    called = {"loop": False}

    monkeypatch.setattr(
        "longrun_agent.cli._validate_project_python_environment",
        lambda _project_dir, _allow_any_python: "wrong python env",
    )
    monkeypatch.setattr(
        "longrun_agent.cli._run_goal_setup_for_go",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("goal setup should not run")),
    )

    def _fake_run_loop(*_args, **_kwargs) -> int:
        called["loop"] = True
        return 0

    monkeypatch.setattr("longrun_agent.cli.run_loop", _fake_run_loop)

    code = run_go(
        config_path=config_path,
        goal="Build a practical task manager",
        project_dir=project_dir,
        non_interactive=True,
        yes=True,
    )

    assert code == 2
    assert called["loop"] is False


def test_run_go_requires_goal_when_non_interactive_and_spec_is_placeholder(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"
    called = {"loop": False}

    monkeypatch.setattr(
        "longrun_agent.cli._validate_project_python_environment",
        lambda _project_dir, _allow_any_python: None,
    )
    monkeypatch.setattr(
        "longrun_agent.cli._run_goal_setup_for_go",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("goal setup should not run")),
    )

    def _fake_run_loop(*_args, **_kwargs) -> int:
        called["loop"] = True
        return 0

    monkeypatch.setattr("longrun_agent.cli.run_loop", _fake_run_loop)

    code = run_go(
        config_path=config_path,
        goal=None,
        project_dir=project_dir,
        non_interactive=True,
        yes=True,
    )

    assert code == 2
    assert called["loop"] is False


def test_run_go_runs_first_time_setup_when_config_missing_and_interactive(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"
    captured: dict[str, object] = {}

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "longrun_agent.cli._validate_project_python_environment",
        lambda _project_dir, _allow_any_python: None,
    )

    def _fake_first_time_setup(config_path_arg: Path, project_dir_arg: Path | None = None) -> int:
        captured["first_time_setup"] = {
            "config_path": config_path_arg,
            "project_dir": project_dir_arg,
        }
        return 0

    monkeypatch.setattr("longrun_agent.cli._run_first_time_setup_for_go", _fake_first_time_setup)
    monkeypatch.setattr("longrun_agent.cli._run_goal_setup_for_go", lambda **_kwargs: None)
    monkeypatch.setattr("longrun_agent.cli.run_loop", lambda *_args, **_kwargs: 0)

    code = run_go(
        config_path=config_path,
        goal="Build a practical task manager",
        project_dir=project_dir,
    )

    assert code == 0
    first_time = captured["first_time_setup"]
    assert isinstance(first_time, dict)
    assert first_time["config_path"] == config_path
    assert first_time["project_dir"] == project_dir


def test_resolve_config_path_uses_external_default_when_local_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    local_default = Path("longrun-agent.toml")
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)

    monkeypatch.setattr("longrun_agent.cli.Path.home", lambda: fake_home)

    resolved = _resolve_config_path(local_default, project_dir_hint=project_dir)

    assert resolved.parent == fake_home / ".longrun-agent" / "configs"
    assert resolved.name.startswith("project-")
    assert resolved.suffix == ".toml"


def test_resolve_config_path_prefers_local_default_when_file_exists(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    local_default = Path("longrun-agent.toml")
    local_default.write_text("[harness]\nproject_dir='.'\n")
    try:
        resolved = _resolve_config_path(local_default, project_dir_hint=tmp_path)
        assert resolved == local_default
    finally:
        local_default.unlink(missing_ok=True)
