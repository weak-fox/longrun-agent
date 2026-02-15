from pathlib import Path

from longrun_agent.cli import (
    GuidedGoalDraft,
    _parse_goal_draft,
    build_parser,
    run_bootstrap,
)
from longrun_agent.config import DEFAULT_CODEX_MODEL, load_config


def test_parser_accepts_bootstrap_guided_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["bootstrap", "--guided"])
    assert args.command == "bootstrap"
    assert args.guided is True


def test_run_bootstrap_guided_updates_app_spec_and_feature_target(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"
    answers = iter(["Build a lightweight task board", "", "42"])

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(
        "longrun_agent.cli._generate_goal_draft_with_agent",
        lambda _config, goal: GuidedGoalDraft(
            goal=goal,
            primary_users="Small teams",
            core_flows=["Create task", "Move task", "Search task"],
            constraints=["No paid services", "Local-first"],
            done_criteria="All listed flows work end-to-end",
            feature_target=30,
            assumptions=["Single-tenant usage"],
        ),
    )

    code = run_bootstrap(config_path, project_dir, guided=True)
    assert code == 0

    spec_text = (project_dir / "app_spec.txt").read_text()
    assert "Build a lightweight task board" in spec_text
    assert "- Create task" in spec_text
    assert "- No paid services" in spec_text

    config = load_config(config_path)
    assert config.feature_target == 42
    assert config.backend_model == DEFAULT_CODEX_MODEL


def test_run_bootstrap_guided_requires_tty(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = run_bootstrap(config_path, project_dir, guided=True)
    assert code == 2


def test_run_bootstrap_guided_prompts_include_examples(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"
    prompts: list[str] = []
    answers = iter(["", "", ""])

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "longrun_agent.cli._generate_goal_draft_with_agent",
        lambda _config, goal: GuidedGoalDraft(
            goal=goal,
            primary_users="Individuals and small teams",
            core_flows=["Create item", "Edit item", "Complete item", "Search item"],
            constraints=["Reuse existing stack"],
            done_criteria="Core flow works",
            feature_target=40,
            assumptions=[],
        ),
    )

    def _input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", _input)

    code = run_bootstrap(config_path, project_dir, guided=True)
    assert code == 0
    assert any("e.g." in prompt for prompt in prompts)
    assert any("20-80 recommended" in prompt for prompt in prompts)


def test_run_bootstrap_guided_falls_back_to_manual_when_agent_draft_fails(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"
    answers = iter(
        [
            "Build local todo app",
            "Small teams",
            "create task, edit task, search task",
            "no paid apis",
            "user can finish task flow",
            "",
            "",
            "35",
        ]
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(
        "longrun_agent.cli._generate_goal_draft_with_agent",
        lambda _config, _goal: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    code = run_bootstrap(config_path, project_dir, guided=True)
    assert code == 0
    spec_text = (project_dir / "app_spec.txt").read_text()
    assert "Build local todo app" in spec_text
    assert "Small teams" in spec_text
    assert "- create task" in spec_text


def test_parse_goal_draft_accepts_json_code_block() -> None:
    draft = _parse_goal_draft(
        raw_output="""```json
{
  "primary_users": "Design teams",
  "core_flows": ["Create board", "Move card", "Search card", "Export"],
  "constraints": ["No paid APIs"],
  "done_criteria": "User can run full flow",
  "feature_target": 25,
  "assumptions": ["Single workspace"]
}
```""",
        goal="Build kanban board",
        default_feature_target=40,
    )

    assert draft.primary_users == "Design teams"
    assert draft.feature_target == 25
    assert draft.core_flows[0] == "Create board"


def test_run_bootstrap_guided_keeps_previous_draft_when_regeneration_fails(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"
    answers = iter(
        [
            "Build kanban app",
            "n",
            "Use offline storage",
            "31",
        ]
    )
    calls = {"count": 0}

    def _draft(_config, goal):
        calls["count"] += 1
        if calls["count"] == 1:
            return GuidedGoalDraft(
                goal=goal,
                primary_users="Teams",
                core_flows=["Create board", "Create card", "Move card", "Search card"],
                constraints=["No paid APIs"],
                done_criteria="Main workflow works",
                feature_target=24,
                assumptions=[],
            )
        raise RuntimeError("regen failed")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr("longrun_agent.cli._generate_goal_draft_with_agent", _draft)

    code = run_bootstrap(config_path, project_dir, guided=True)
    assert code == 0
    spec_text = (project_dir / "app_spec.txt").read_text()
    assert "Build kanban app" in spec_text
    assert "Main workflow works" in spec_text
