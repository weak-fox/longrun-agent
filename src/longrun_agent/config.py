"""Configuration loading for the long-running harness."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import tomllib


DEFAULT_CONFIG_FILENAME = "longrun-agent.toml"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_CODEX_MODEL = "gpt-5.2-codex"
DEFAULT_CODEX_COMMAND_TEMPLATE = [
    "bash",
    "-lc",
    'LONGRUN_PHASE="{phase}" codex exec --skip-git-repo-check --full-auto --sandbox workspace-write -m "{backend_model}" -C "{project_dir}" < "{prompt_file}"',
]
LEGACY_CODEX_COMMAND_TEMPLATE_WITHOUT_SKIP = [
    "bash",
    "-lc",
    'LONGRUN_PHASE="{phase}" codex exec -m "{backend_model}" -C "{project_dir}" < "{prompt_file}"',
]
LEGACY_CODEX_COMMAND_TEMPLATE_WITH_SKIP_NO_FULL_AUTO = [
    "bash",
    "-lc",
    'LONGRUN_PHASE="{phase}" codex exec --skip-git-repo-check -m "{backend_model}" -C "{project_dir}" < "{prompt_file}"',
]
LEGACY_CODEX_COMMAND_TEMPLATE_WITH_FULL_AUTO_NO_SANDBOX = [
    "bash",
    "-lc",
    'LONGRUN_PHASE="{phase}" codex exec --skip-git-repo-check --full-auto -m "{backend_model}" -C "{project_dir}" < "{prompt_file}"',
]
LEGACY_AGENT_PLACEHOLDER_COMMAND = ["echo", "configure agent.command in longrun-agent.toml"]
LEGACY_CODEX_PLACEHOLDER_COMMAND = ["echo", "configure codex_cli command in longrun-agent.toml"]

DEFAULT_BEARINGS_COMMANDS = [
    "pwd",
    "ls -la",
    'cat "${LONGRUN_APP_SPEC_PATH:-.longrun/artifacts/app_spec.txt}"',
    'head -n 80 "${LONGRUN_FEATURE_LIST_PATH:-.longrun/artifacts/feature_list.json}" || true',
    'tail -n 80 "${LONGRUN_PROGRESS_PATH:-.longrun/artifacts/claude-progress.txt}" || true',
    "git log --oneline -20 || true",
]

DEFAULT_CONFIG_TEMPLATE = f"""[agent]
# Replace this with your real agent CLI command.
# Placeholders supported: {{project_dir}} {{session_dir}} {{prompt_file}} {{phase}} {{backend_model}}
command = {json.dumps(DEFAULT_CODEX_COMMAND_TEMPLATE)}
timeout_seconds = 3600

[runtime]
backend = "codex_cli"
profile = "default"
backend_model = "{DEFAULT_CODEX_MODEL}"
model_reasoning_effort = ""

[backends.codex_cli]
# Command template used by codex_cli backend.
# Placeholders supported: {{project_dir}} {{session_dir}} {{prompt_file}} {{phase}} {{backend_model}}
command = {json.dumps(DEFAULT_CODEX_COMMAND_TEMPLATE)}
model = "{DEFAULT_CODEX_MODEL}"
timeout_seconds = 3600

[backends.claude_sdk]
model = "{DEFAULT_CLAUDE_MODEL}"

[gates]
commit_required = false
progress_update_required = false
repair_on_verification_failure = false

[harness]
project_dir = "."
state_dir = ""
artifacts_dir = ""
auto_continue_delay_seconds = 3
feature_target = 200
max_no_progress_sessions = 5
max_features_per_session = 3
require_clean_git = false
bearings_commands = [
  "pwd",
  "ls -la",
  'cat "${{LONGRUN_APP_SPEC_PATH:-.longrun/artifacts/app_spec.txt}}"',
  'head -n 80 "${{LONGRUN_FEATURE_LIST_PATH:-.longrun/artifacts/feature_list.json}}" || true',
  'tail -n 80 "${{LONGRUN_PROGRESS_PATH:-.longrun/artifacts/claude-progress.txt}}" || true',
  "git log --oneline -20 || true",
]
pre_coding_commands = []
verification_commands = []
"""


def resolve_state_dir(project_dir: Path, state_dir: Path | None) -> Path:
    """Resolve effective state directory from project + optional config override."""
    if state_dir is None:
        return (project_dir / ".longrun").resolve()
    if state_dir.is_absolute():
        return state_dir.resolve()
    return (project_dir / state_dir).resolve()


def resolve_artifacts_dir(
    project_dir: Path,
    state_dir: Path | None,
    artifacts_dir: Path | None,
) -> Path:
    """Resolve effective artifacts directory.

    Default location follows the resolved state dir (`<state_dir>/artifacts`).
    """
    if artifacts_dir is None:
        return (resolve_state_dir(project_dir, state_dir) / "artifacts").resolve()
    if artifacts_dir.is_absolute():
        return artifacts_dir.resolve()
    return (project_dir / artifacts_dir).resolve()


def _is_legacy_codex_command_with_removed_flags(command: list[str]) -> bool:
    if len(command) >= 2 and command[0] == "codex" and command[1] == "exec":
        return "--phase" in command or "--prompt-file" in command

    if len(command) >= 3 and command[0] in {"bash", "sh", "zsh"} and command[1] == "-lc":
        shell_command = command[2]
        return "codex exec" in shell_command and (
            "--phase" in shell_command or "--prompt-file" in shell_command
        )

    return False


@dataclass(slots=True)
class HarnessConfig:
    """Runtime configuration for the harness."""

    project_dir: Path
    agent_command: list[str]
    state_dir: Path | None = None
    artifacts_dir: Path | None = None
    backend_name: str = "codex_cli"
    profile: str = "default"
    backend_model: str = DEFAULT_CODEX_MODEL
    model_reasoning_effort: str | None = None
    agent_timeout_seconds: int = 3600
    auto_continue_delay_seconds: int = 3
    feature_target: int = 200
    max_no_progress_sessions: int = 5
    max_features_per_session: int = 3
    require_clean_git: bool = False
    commit_required: bool = False
    progress_update_required: bool = False
    repair_on_verification_failure: bool = False
    bearings_commands: list[str] = field(default_factory=lambda: list(DEFAULT_BEARINGS_COMMANDS))
    pre_coding_commands: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)


def write_default_config(path: Path, project_dir: Path | None = None) -> Path:
    """Write a starter config if it does not exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        content = DEFAULT_CONFIG_TEMPLATE
        if project_dir is not None:
            normalized = project_dir.as_posix()
            content = content.replace('project_dir = "."', f'project_dir = "{normalized}"')
        path.write_text(content)
    return path


def load_config(path: Path) -> HarnessConfig:
    """Load TOML config with sensible defaults."""
    data: dict = {}
    if path.exists():
        data = tomllib.loads(path.read_text())

    agent_data = data.get("agent", {})
    runtime_data = data.get("runtime", {})
    harness_data = data.get("harness", {})
    backends_data = data.get("backends", {})
    gates_data = data.get("gates", {})

    if not isinstance(backends_data, dict):
        raise ValueError("backends section must be a table")
    if not isinstance(gates_data, dict):
        raise ValueError("gates section must be a table")

    codex_backend_data = backends_data.get("codex_cli", {})
    if not isinstance(codex_backend_data, dict):
        raise ValueError("backends.codex_cli must be a table")
    claude_backend_data = backends_data.get("claude_sdk", {})
    if not isinstance(claude_backend_data, dict):
        raise ValueError("backends.claude_sdk must be a table")

    backend_name = str(runtime_data.get("backend", "codex_cli"))
    default_command = list(DEFAULT_CODEX_COMMAND_TEMPLATE)
    command = codex_backend_data.get("command", agent_data.get("command", default_command))
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise ValueError("agent.command must be a list of strings")
    if backend_name == "codex_cli" and (
        command
        in (
            LEGACY_AGENT_PLACEHOLDER_COMMAND,
            LEGACY_CODEX_PLACEHOLDER_COMMAND,
            LEGACY_CODEX_COMMAND_TEMPLATE_WITHOUT_SKIP,
            LEGACY_CODEX_COMMAND_TEMPLATE_WITH_SKIP_NO_FULL_AUTO,
            LEGACY_CODEX_COMMAND_TEMPLATE_WITH_FULL_AUTO_NO_SANDBOX,
        )
        or _is_legacy_codex_command_with_removed_flags(command)
    ):
        command = list(DEFAULT_CODEX_COMMAND_TEMPLATE)

    project_dir_value = harness_data.get("project_dir", ".")
    if not isinstance(project_dir_value, str):
        raise ValueError("harness.project_dir must be a string")
    state_dir_value = harness_data.get("state_dir", "")
    if not isinstance(state_dir_value, str):
        raise ValueError("harness.state_dir must be a string")
    state_dir = Path(state_dir_value) if state_dir_value.strip() else None
    artifacts_dir_value = harness_data.get("artifacts_dir", "")
    if not isinstance(artifacts_dir_value, str):
        raise ValueError("harness.artifacts_dir must be a string")
    artifacts_dir = Path(artifacts_dir_value) if artifacts_dir_value.strip() else None

    bearings = harness_data.get("bearings_commands", DEFAULT_BEARINGS_COMMANDS)
    pre_coding = harness_data.get("pre_coding_commands", [])
    verification = harness_data.get("verification_commands", [])

    if not isinstance(bearings, list) or not all(isinstance(item, str) for item in bearings):
        raise ValueError("harness.bearings_commands must be a list of strings")

    if not isinstance(verification, list) or not all(
        isinstance(item, str) for item in verification
    ):
        raise ValueError("harness.verification_commands must be a list of strings")

    if not isinstance(pre_coding, list) or not all(isinstance(item, str) for item in pre_coding):
        raise ValueError("harness.pre_coding_commands must be a list of strings")

    runtime_backend_model = runtime_data.get("backend_model")
    runtime_reasoning_effort = runtime_data.get("model_reasoning_effort")
    if runtime_reasoning_effort is None:
        model_reasoning_effort: str | None = None
    else:
        if not isinstance(runtime_reasoning_effort, str):
            raise ValueError("runtime.model_reasoning_effort must be a string")
        model_reasoning_effort = runtime_reasoning_effort.strip() or None

    if runtime_backend_model is None:
        if backend_name == "claude_sdk":
            backend_model = str(
                claude_backend_data.get(
                    "model",
                    agent_data.get("model", DEFAULT_CLAUDE_MODEL),
                )
            )
        else:
            backend_model = str(codex_backend_data.get("model", DEFAULT_CODEX_MODEL))
    else:
        backend_model = str(runtime_backend_model)

    return HarnessConfig(
        project_dir=Path(project_dir_value),
        agent_command=command,
        state_dir=state_dir,
        artifacts_dir=artifacts_dir,
        backend_name=backend_name,
        profile=str(runtime_data.get("profile", "default")),
        backend_model=backend_model,
        model_reasoning_effort=model_reasoning_effort,
        agent_timeout_seconds=int(
            codex_backend_data.get(
                "timeout_seconds",
                agent_data.get("timeout_seconds", 3600),
            )
        ),
        auto_continue_delay_seconds=int(harness_data.get("auto_continue_delay_seconds", 3)),
        feature_target=int(harness_data.get("feature_target", 200)),
        max_no_progress_sessions=int(harness_data.get("max_no_progress_sessions", 5)),
        max_features_per_session=int(harness_data.get("max_features_per_session", 3)),
        require_clean_git=bool(harness_data.get("require_clean_git", False)),
        commit_required=bool(gates_data.get("commit_required", False)),
        progress_update_required=bool(gates_data.get("progress_update_required", False)),
        repair_on_verification_failure=bool(
            gates_data.get("repair_on_verification_failure", False)
        ),
        bearings_commands=bearings,
        pre_coding_commands=pre_coding,
        verification_commands=verification,
    )


_TOML_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(key: str) -> str:
    if _TOML_BARE_KEY_RE.match(key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        rendered = ", ".join(_toml_value(item) for item in value)
        return f"[{rendered}]"
    if isinstance(value, dict):
        rendered = ", ".join(f"{_toml_key(str(k))} = {_toml_value(v)}" for k, v in value.items())
        return f"{{ {rendered} }}"
    raise TypeError(f"Unsupported TOML value type: {type(value)!r}")


def _append_toml_table(lines: list[str], table: dict[str, object], path: tuple[str, ...]) -> None:
    if path:
        header = ".".join(_toml_key(part) for part in path)
        lines.append(f"[{header}]")

    scalar_items: list[tuple[str, object]] = []
    nested_items: list[tuple[str, dict[str, object]]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            nested_items.append((key, value))
        else:
            scalar_items.append((key, value))

    for key, value in scalar_items:
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")

    for key, child in nested_items:
        if lines and lines[-1] != "":
            lines.append("")
        _append_toml_table(lines, child, path + (key,))


def _serialize_toml(data: dict[str, object]) -> str:
    lines: list[str] = []
    _append_toml_table(lines, data, ())
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _managed_config_dict(config: HarnessConfig) -> dict[str, object]:
    codex_model = config.backend_model if config.backend_name == "codex_cli" else DEFAULT_CODEX_MODEL
    claude_model = config.backend_model if config.backend_name == "claude_sdk" else DEFAULT_CLAUDE_MODEL

    return {
        "agent": {
            "command": list(config.agent_command),
            "timeout_seconds": int(config.agent_timeout_seconds),
        },
        "runtime": {
            "backend": config.backend_name,
            "profile": config.profile,
            "backend_model": config.backend_model,
            "model_reasoning_effort": config.model_reasoning_effort or "",
        },
        "backends": {
            "codex_cli": {
                "command": list(config.agent_command),
                "model": codex_model,
                "timeout_seconds": int(config.agent_timeout_seconds),
            },
            "claude_sdk": {
                "model": claude_model,
            },
        },
        "gates": {
            "commit_required": bool(config.commit_required),
            "progress_update_required": bool(config.progress_update_required),
            "repair_on_verification_failure": bool(config.repair_on_verification_failure),
        },
        "harness": {
            "project_dir": config.project_dir.as_posix(),
            "state_dir": config.state_dir.as_posix() if config.state_dir else "",
            "artifacts_dir": config.artifacts_dir.as_posix() if config.artifacts_dir else "",
            "auto_continue_delay_seconds": int(config.auto_continue_delay_seconds),
            "feature_target": int(config.feature_target),
            "max_no_progress_sessions": int(config.max_no_progress_sessions),
            "max_features_per_session": int(config.max_features_per_session),
            "require_clean_git": bool(config.require_clean_git),
            "bearings_commands": list(config.bearings_commands),
            "pre_coding_commands": list(config.pre_coding_commands),
            "verification_commands": list(config.verification_commands),
        },
    }


def _merge_preserving_unmanaged_fields(
    existing: dict[str, object],
    managed: dict[str, object],
) -> dict[str, object]:
    merged: dict[str, object] = dict(existing)
    for key, value in managed.items():
        if isinstance(value, dict):
            current = merged.get(key)
            if not isinstance(current, dict):
                current = {}
                merged[key] = current
            merged[key] = _merge_preserving_unmanaged_fields(current, value)
        else:
            merged[key] = value
    return merged


def save_config(path: Path, config: HarnessConfig, *, preserve_unmanaged: bool = False) -> None:
    """Persist config in TOML layout.

    When preserve_unmanaged is true, unknown keys/tables already present in the
    existing config are retained.
    """
    managed = _managed_config_dict(config)
    data: dict[str, object] = managed
    if preserve_unmanaged and path.exists():
        existing = tomllib.loads(path.read_text())
        if isinstance(existing, dict):
            data = _merge_preserving_unmanaged_fields(existing, managed)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_toml(data))
