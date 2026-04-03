from pathlib import Path
import tomllib

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


def test_run_configure_preserves_unrelated_config_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[agent]
command = ["echo", "ok"]
timeout_seconds = 3600
custom_agent_flag = "keep"

[runtime]
backend = "codex_cli"
profile = "default"
backend_model = "gpt-5.2-codex"
model_reasoning_effort = ""
custom_runtime_flag = "keep"

[backends.codex_cli]
command = ["echo", "ok"]
model = "gpt-5.2-codex"
timeout_seconds = 3600
custom_codex_flag = "keep"

[backends.claude_sdk]
model = "claude-sonnet-4-5-20250929"
custom_claude_flag = "keep"

[gates]
commit_required = false
progress_update_required = false
repair_on_verification_failure = false
custom_gate_flag = "keep"

[harness]
project_dir = "."
state_dir = ""
artifacts_dir = ""
auto_continue_delay_seconds = 3
feature_target = 200
max_no_progress_sessions = 5
max_features_per_session = 3
require_clean_git = false
bearings_commands = ["pwd"]
pre_coding_commands = []
verification_commands = []
custom_harness_flag = "keep"

[custom]
value = "keep"
"""
    )

    code = run_configure(
        config_path=config_path,
        backend="claude_sdk",
        profile="article",
        backend_model="claude-opus-x",
        commit_required=True,
        non_interactive=True,
    )

    assert code == 0
    parsed = tomllib.loads(config_path.read_text())
    assert parsed["custom"]["value"] == "keep"
    assert parsed["agent"]["custom_agent_flag"] == "keep"
    assert parsed["runtime"]["custom_runtime_flag"] == "keep"
    assert parsed["backends"]["codex_cli"]["custom_codex_flag"] == "keep"
    assert parsed["backends"]["claude_sdk"]["custom_claude_flag"] == "keep"
    assert parsed["gates"]["custom_gate_flag"] == "keep"
    assert parsed["harness"]["custom_harness_flag"] == "keep"
