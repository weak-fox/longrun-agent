from pathlib import Path

from longrun_agent.cli import build_parser, run_configure
from longrun_agent.config import load_config


def test_parser_accepts_configure_command_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "configure",
            "--backend",
            "claude_sdk",
            "--profile",
            "article",
            "--backend-model",
            "claude-opus-x",
            "--model-reasoning-effort",
            "xhigh",
            "--state-dir",
            "/tmp/longrun-state",
            "--artifacts-dir",
            ".longrun/artifacts",
            "--commit-required",
            "--non-interactive",
        ]
    )

    assert args.command == "configure"
    assert args.backend == "claude_sdk"
    assert args.profile == "article"
    assert args.backend_model == "claude-opus-x"
    assert args.model_reasoning_effort == "xhigh"
    assert args.state_dir == Path("/tmp/longrun-state")
    assert args.artifacts_dir == Path(".longrun/artifacts")
    assert args.commit_required is True
    assert args.non_interactive is True


def test_run_configure_updates_config_file_non_interactive(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "proj"

    code = run_configure(
        config_path=config_path,
        backend="claude_sdk",
        profile="article",
        backend_model="claude-opus-x",
        model_reasoning_effort="xhigh",
        project_dir=project_dir,
        state_dir=tmp_path / "state",
        artifacts_dir=Path(".longrun/artifacts"),
        codex_command="codex exec --phase {phase} --prompt-file {prompt_file}",
        codex_timeout_seconds=1234,
        commit_required=True,
        progress_update_required=True,
        repair_on_verification_failure=True,
        non_interactive=True,
    )

    assert code == 0
    loaded = load_config(config_path)
    assert loaded.backend_name == "claude_sdk"
    assert loaded.profile == "article"
    assert loaded.backend_model == "claude-opus-x"
    assert loaded.model_reasoning_effort == "xhigh"
    assert loaded.project_dir == project_dir
    assert loaded.state_dir == tmp_path / "state"
    assert loaded.artifacts_dir == Path(".longrun/artifacts")
    assert loaded.agent_timeout_seconds == 1234
    assert loaded.agent_command[:2] == ["codex", "exec"]
    assert loaded.commit_required is True
    assert loaded.progress_update_required is True
    assert loaded.repair_on_verification_failure is True
