import json
from pathlib import Path

from longrun_agent.cli import _resolve_config_path, _run_goal_setup_for_go, build_parser, run_go
from longrun_agent.config import HarnessConfig, load_config
from longrun_agent.runtime.contracts import AgentRunResult


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
    assert args.continue_on_failure is True


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
        continue_on_failure: bool = False,
        backend: str | None = None,
        profile: str | None = None,
        backend_model: str | None = None,
        model_reasoning_effort: str | None = None,
    ) -> int:
        captured["run_loop"] = {
            "max_sessions": max_sessions,
            "continue_on_failure": continue_on_failure,
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
    assert run_loop_args["continue_on_failure"] is True

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


def test_resolve_config_path_uses_local_default_when_local_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    local_default = Path("longrun-agent.toml")
    resolved = _resolve_config_path(local_default, project_dir_hint=tmp_path / "project")
    assert resolved == local_default


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


def test_goal_setup_writes_app_spec_into_artifacts_dir_when_configured(
    tmp_path: Path, monkeypatch
) -> None:
    from longrun_agent.cli import GuidedGoalDraft

    config = HarnessConfig(
        project_dir=tmp_path,
        artifacts_dir=Path(".longrun/artifacts"),
        agent_command=["echo", "ok"],
        feature_target=20,
    )

    monkeypatch.setattr(
        "longrun_agent.cli._generate_goal_draft_with_agent",
        lambda _config, goal: GuidedGoalDraft(
            goal=goal,
            primary_users="Team",
            core_flows=["A", "B", "C", "D"],
            constraints=["Reuse existing stack"],
            done_criteria="Done",
            feature_target=22,
            assumptions=[],
        ),
    )

    _run_goal_setup_for_go(
        config=config,
        goal="Improve an existing project",
        interactive=False,
        brainstorm_rounds=0,
        skip_brainstorm=True,
        assume_yes=True,
        feature_target_override=12,
    )

    assert (tmp_path / ".longrun" / "artifacts" / "app_spec.txt").exists()
    assert not (tmp_path / "app_spec.txt").exists()


def test_run_go_generates_stack_aware_incremental_spec_for_existing_codebase(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "existing-web"
    (project_dir / "src").mkdir(parents=True)
    (project_dir / "src" / "App.tsx").write_text("export const App = () => <main>Existing app</main>;\n")
    (project_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "existing-web",
                "private": True,
                "dependencies": {"react": "^18.3.0"},
                "devDependencies": {"typescript": "^5.6.0"},
            },
            indent=2,
        )
    )

    monkeypatch.setattr(
        "longrun_agent.cli._validate_project_python_environment",
        lambda _project_dir, _allow_any_python: None,
    )
    monkeypatch.setattr("longrun_agent.cli.run_loop", lambda *_args, **_kwargs: 0)

    class _StackAwareBackend:
        name = "fake-stack-aware"

        def run(self, request):
            request.session_dir.mkdir(parents=True, exist_ok=True)
            stdout = request.session_dir / "agent.stdout.log"
            stderr = request.session_dir / "agent.stderr.log"

            prompt_text = request.prompt_file.read_text()
            assert (
                "fits the existing repository instead of inventing a greenfield rewrite."
                in prompt_text
            )
            assert (
                "Prefer incremental improvements over technology migration unless the goal explicitly asks for migration."
                in prompt_text
            )

            package = json.loads((request.project_dir / "package.json").read_text())
            deps = package.get("dependencies", {})
            dev_deps = package.get("devDependencies", {})
            uses_react = isinstance(deps, dict) and "react" in deps
            uses_typescript = (
                isinstance(dev_deps, dict)
                and "typescript" in dev_deps
                or isinstance(deps, dict)
                and "typescript" in deps
            )
            stack = "React + TypeScript" if uses_react and uses_typescript else "existing stack"

            payload = {
                "primary_users": "Existing product users",
                "core_flows": [
                    "Filter existing records",
                    "Batch update selected records",
                    "Persist filters in URL",
                    "Keep existing navigation intact",
                ],
                "constraints": [
                    f"Keep existing {stack} stack",
                    "Prefer incremental changes over rewrites",
                ],
                "done_criteria": "New flow works on top of current app and existing checks stay green",
                "feature_target": 24,
                "assumptions": ["Extend current modules instead of building from scratch"],
            }
            stdout.write_text(json.dumps(payload, ensure_ascii=False))
            stderr.write_text("")
            return AgentRunResult(
                backend=self.name,
                return_code=0,
                timeout=False,
                stdout_path=stdout,
                stderr_path=stderr,
                metadata={},
            )

    monkeypatch.setattr(
        "longrun_agent.cli.create_backend",
        lambda **_kwargs: _StackAwareBackend(),
    )

    code = run_go(
        config_path=config_path,
        goal="在现有项目基础上新增筛选和批量操作",
        max_sessions=1,
        project_dir=project_dir,
        feature_target=24,
        brainstorm_rounds=0,
        skip_brainstorm=True,
        non_interactive=True,
        yes=True,
        allow_any_python=True,
    )

    assert code == 0
    spec_path = project_dir / ".longrun" / "artifacts" / "app_spec.txt"
    assert spec_path.exists()
    spec_text = spec_path.read_text()
    assert "Keep existing React + TypeScript stack" in spec_text
    assert "Prefer incremental changes over rewrites" in spec_text
    assert not (project_dir / "app_spec.txt").exists()

    draft_prompt = project_dir / ".longrun" / "guided-goal" / "goal-draft.prompt.md"
    assert draft_prompt.exists()
    prompt_text = draft_prompt.read_text()
    assert "Layered repository reading (MANDATORY)" in prompt_text
    assert "Read `AGENTS.md` first" in prompt_text
