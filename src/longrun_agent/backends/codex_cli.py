"""Codex CLI backend adapter."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from longrun_agent.backends.base import AgentBackend
from longrun_agent.runtime.contracts import AgentRunRequest, AgentRunResult


class CodexCliBackend(AgentBackend):
    """Backend adapter for subprocess-driven Codex execution."""

    name = "codex_cli"

    def __init__(self, project_dir: Path, command_template: list[str]):
        self.project_dir = project_dir
        self.command_template = command_template

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        command = self._render_command(request)
        request.session_dir.mkdir(parents=True, exist_ok=True)
        command_file = request.session_dir / "agent.command.txt"
        stdout_file = request.session_dir / "agent.stdout.log"
        stderr_file = request.session_dir / "agent.stderr.log"
        command_file.write_text(" ".join(command))

        try:
            completed = subprocess.run(
                command,
                cwd=request.project_dir,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_text = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr_text = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            stdout_file.write_text(stdout_text)
            stderr_file.write_text(stderr_text)
            return AgentRunResult(
                backend=self.name,
                return_code=None,
                timeout=True,
                stdout_path=stdout_file,
                stderr_path=stderr_file,
                metadata={"command": command},
            )

        stdout_file.write_text(completed.stdout)
        stderr_file.write_text(completed.stderr)
        return AgentRunResult(
            backend=self.name,
            return_code=completed.returncode,
            timeout=False,
            stdout_path=stdout_file,
            stderr_path=stderr_file,
            metadata={"command": command},
        )

    def _render_command(self, request: AgentRunRequest) -> list[str]:
        reasoning_toml = self._reasoning_effort_toml(request.model_reasoning_effort)
        mapping = {
            "project_dir": str(request.project_dir),
            "session_dir": str(request.session_dir),
            "prompt_file": str(request.prompt_file),
            "phase": request.phase,
            "backend_model": request.backend_model or "",
            "model_reasoning_effort": request.model_reasoning_effort or "",
            "model_reasoning_effort_toml": reasoning_toml or "",
        }
        rendered: list[str] = []
        for token in self.command_template:
            try:
                rendered.append(token.format_map(mapping))
            except KeyError as exc:
                msg = f"Unknown placeholder in codex command template: {exc}"
                raise ValueError(msg) from exc
        if not rendered:
            raise ValueError("codex command template is empty")
        rendered = self._inject_reasoning_effort_override(rendered, reasoning_toml)
        if bool(request.metadata.get("force_workspace_write")):
            rendered = self._inject_workspace_write_override(rendered)
        return rendered

    @staticmethod
    def _reasoning_effort_toml(effort: str | None) -> str | None:
        if effort is None:
            return None
        trimmed = effort.strip()
        if not trimmed:
            return None
        return f"model_reasoning_effort={json.dumps(trimmed)}"

    def _inject_reasoning_effort_override(
        self,
        rendered: list[str],
        reasoning_toml: str | None,
    ) -> list[str]:
        if reasoning_toml is None:
            return rendered

        if any("model_reasoning_effort" in token for token in rendered):
            return rendered

        if len(rendered) >= 2 and rendered[0] == "codex" and rendered[1] == "exec":
            return [*rendered[:2], "-c", reasoning_toml, *rendered[2:]]

        if len(rendered) >= 3 and rendered[0] in {"bash", "sh", "zsh"} and rendered[1] == "-lc":
            command = rendered[2]
            marker = "codex exec"
            if marker in command:
                updated = list(rendered)
                updated[2] = command.replace(
                    marker,
                    f"{marker} -c {reasoning_toml}",
                    1,
                )
                return updated

        return rendered

    def _inject_workspace_write_override(self, rendered: list[str]) -> list[str]:
        if len(rendered) >= 2 and rendered[0] == "codex" and rendered[1] == "exec":
            return self._set_or_insert_sandbox_tokens(rendered)

        if len(rendered) >= 3 and rendered[0] in {"bash", "sh", "zsh"} and rendered[1] == "-lc":
            command = rendered[2]
            marker = "codex exec"
            if marker in command:
                updated = list(rendered)
                if "--sandbox" in command or " -s " in command:
                    with_named = re.sub(
                        r"(--sandbox\s+)(read-only|workspace-write|danger-full-access)",
                        r"\1workspace-write",
                        command,
                        count=1,
                    )
                    with_short = re.sub(
                        r"(-s\s+)(read-only|workspace-write|danger-full-access)",
                        r"\1workspace-write",
                        with_named,
                        count=1,
                    )
                    updated[2] = with_short
                else:
                    updated[2] = command.replace(
                        marker,
                        f"{marker} --sandbox workspace-write",
                        1,
                    )
                return updated

        return rendered

    @staticmethod
    def _set_or_insert_sandbox_tokens(rendered: list[str]) -> list[str]:
        updated = list(rendered)
        for i, token in enumerate(updated):
            if token in {"--sandbox", "-s"} and i + 1 < len(updated):
                updated[i + 1] = "workspace-write"
                return updated
        return [*updated[:2], "--sandbox", "workspace-write", *updated[2:]]
