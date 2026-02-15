from pathlib import Path

from longrun_agent.config import (
    DEFAULT_CODEX_COMMAND_TEMPLATE,
    DEFAULT_CODEX_MODEL,
    HarnessConfig,
    load_config,
    save_config,
    write_default_config,
)


def test_write_default_config_can_embed_project_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    project_dir = tmp_path / "project"

    write_default_config(config_path, project_dir=project_dir)
    config = load_config(config_path)

    assert config.project_dir == project_dir
    assert config.agent_command == DEFAULT_CODEX_COMMAND_TEMPLATE
    assert config.backend_model == DEFAULT_CODEX_MODEL


def test_write_default_config_creates_parent_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "nested" / "configs" / "longrun-agent.toml"

    write_default_config(config_path)

    assert config_path.exists()
    config = load_config(config_path)
    assert config.backend_name == "codex_cli"


def test_load_config_reads_article_guardrail_options(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[agent]
command = ["echo", "ok"]

[harness]
project_dir = "."
max_features_per_session = 2
pre_coding_commands = ["echo baseline"]
"""
    )

    config = load_config(config_path)

    assert config.max_features_per_session == 2
    assert config.pre_coding_commands == ["echo baseline"]


def test_load_config_reads_runtime_backend_options(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[agent]
command = ["echo", "ok"]

[runtime]
backend = "claude_sdk"
profile = "article"
backend_model = "claude-opus-x"
model_reasoning_effort = "xhigh"

[harness]
project_dir = "."
"""
    )

    config = load_config(config_path)

    assert config.backend_name == "claude_sdk"
    assert config.profile == "article"
    assert config.backend_model == "claude-opus-x"
    assert config.model_reasoning_effort == "xhigh"


def test_load_config_reads_harness_state_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[agent]
command = ["echo", "ok"]

[harness]
project_dir = "."
state_dir = "/tmp/longrun-state"
"""
    )

    config = load_config(config_path)

    assert config.project_dir == Path(".")
    assert config.state_dir == Path("/tmp/longrun-state")


def test_load_config_uses_claude_model_when_runtime_backend_model_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[runtime]
backend = "claude_sdk"
profile = "article"

[backends.claude_sdk]
model = "claude-opus-custom"

[harness]
project_dir = "."
"""
    )

    config = load_config(config_path)
    assert config.backend_name == "claude_sdk"
    assert config.backend_model == "claude-opus-custom"


def test_load_config_prefers_backends_codex_cli_command_over_agent_command(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[agent]
command = ["echo", "agent"]

[backends.codex_cli]
command = ["echo", "codex"]

[harness]
project_dir = "."
"""
    )

    config = load_config(config_path)

    assert config.agent_command == ["echo", "codex"]


def test_load_config_migrates_legacy_placeholder_codex_command(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[agent]
command = ["echo", "configure agent.command in longrun-agent.toml"]

[runtime]
backend = "codex_cli"

[harness]
project_dir = "."
"""
    )

    config = load_config(config_path)

    assert config.backend_name == "codex_cli"
    assert config.agent_command == DEFAULT_CODEX_COMMAND_TEMPLATE


def test_load_config_migrates_old_default_codex_command_without_skip_flag(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[runtime]
backend = "codex_cli"

[backends.codex_cli]
command = ["bash", "-lc", "LONGRUN_PHASE=\\"{phase}\\" codex exec -m \\"{backend_model}\\" -C \\"{project_dir}\\" < \\"{prompt_file}\\""]

[harness]
project_dir = "."
"""
    )

    config = load_config(config_path)

    assert config.backend_name == "codex_cli"
    assert config.agent_command == DEFAULT_CODEX_COMMAND_TEMPLATE


def test_load_config_migrates_previous_default_codex_command_without_full_auto(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[runtime]
backend = "codex_cli"

[backends.codex_cli]
command = ["bash", "-lc", "LONGRUN_PHASE=\\"{phase}\\" codex exec --skip-git-repo-check -m \\"{backend_model}\\" -C \\"{project_dir}\\" < \\"{prompt_file}\\""]

[harness]
project_dir = "."
"""
    )

    config = load_config(config_path)

    assert config.backend_name == "codex_cli"
    assert config.agent_command == DEFAULT_CODEX_COMMAND_TEMPLATE


def test_load_config_migrates_legacy_codex_command_with_phase_and_prompt_flags(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[runtime]
backend = "codex_cli"

[backends.codex_cli]
command = ["codex", "exec", "--phase", "{phase}", "--prompt-file", "{prompt_file}", "-m", "{backend_model}"]

[harness]
project_dir = "."
"""
    )

    config = load_config(config_path)

    assert config.backend_name == "codex_cli"
    assert config.agent_command == DEFAULT_CODEX_COMMAND_TEMPLATE


def test_load_config_migrates_shell_legacy_codex_command_with_phase_flag(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config_path.write_text(
        """[runtime]
backend = "codex_cli"

[backends.codex_cli]
command = ["bash", "-lc", "codex exec --phase {phase} --prompt-file {prompt_file} -m {backend_model}"]

[harness]
project_dir = "."
"""
    )

    config = load_config(config_path)

    assert config.backend_name == "codex_cli"
    assert config.agent_command == DEFAULT_CODEX_COMMAND_TEMPLATE


def test_save_config_roundtrip_preserves_core_runtime_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "longrun-agent.toml"
    config = HarnessConfig(
        project_dir=tmp_path / "project",
        agent_command=["codex", "exec", "--phase", "{phase}"],
        backend_name="claude_sdk",
        profile="article",
        backend_model="claude-opus-x",
        model_reasoning_effort="xhigh",
        agent_timeout_seconds=123,
        state_dir=tmp_path / "state",
        commit_required=True,
        progress_update_required=True,
        repair_on_verification_failure=True,
        bearings_commands=["pwd"],
        pre_coding_commands=["echo pre"],
        verification_commands=["echo verify"],
    )
    save_config(config_path, config)

    loaded = load_config(config_path)
    assert loaded.backend_name == "claude_sdk"
    assert loaded.profile == "article"
    assert loaded.backend_model == "claude-opus-x"
    assert loaded.model_reasoning_effort == "xhigh"
    assert loaded.agent_timeout_seconds == 123
    assert loaded.state_dir == tmp_path / "state"
    assert loaded.agent_command == ["codex", "exec", "--phase", "{phase}"]
    assert loaded.commit_required is True
    assert loaded.progress_update_required is True
    assert loaded.repair_on_verification_failure is True
