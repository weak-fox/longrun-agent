"""Claude Agent SDK backend adapter."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from longrun_agent.article.security import bash_security_hook
from longrun_agent.backends.base import AgentBackend
from longrun_agent.runtime.contracts import AgentRunRequest, AgentRunResult

PUPPETEER_TOOLS = [
    "mcp__puppeteer__puppeteer_navigate",
    "mcp__puppeteer__puppeteer_screenshot",
    "mcp__puppeteer__puppeteer_click",
    "mcp__puppeteer__puppeteer_fill",
    "mcp__puppeteer__puppeteer_select",
    "mcp__puppeteer__puppeteer_hover",
    "mcp__puppeteer__puppeteer_evaluate",
]

BUILTIN_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
]


class ClaudeSdkBackend(AgentBackend):
    """Backend adapter for claude-code-sdk execution."""

    name = "claude_sdk"

    def __init__(self, project_dir: Path, model: str):
        self.project_dir = project_dir
        self.model = model

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        request.session_dir.mkdir(parents=True, exist_ok=True)
        command_file = request.session_dir / "agent.command.txt"
        stdout_file = request.session_dir / "agent.stdout.log"
        stderr_file = request.session_dir / "agent.stderr.log"
        command_file.write_text(f"claude_sdk model={self.model}")

        self._import_sdk()  # fail fast with a clear dependency error
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")

        try:
            stdout_text, stderr_text = asyncio.run(
                asyncio.wait_for(self._run_once(request), timeout=request.timeout_seconds)
            )
        except TimeoutError:
            stdout_file.write_text("")
            stderr_file.write_text(f"Session timed out after {request.timeout_seconds}s")
            return AgentRunResult(
                backend=self.name,
                return_code=None,
                timeout=True,
                stdout_path=stdout_file,
                stderr_path=stderr_file,
                metadata={"model": self.model},
            )
        except Exception as exc:
            stdout_file.write_text("")
            stderr_file.write_text(str(exc))
            return AgentRunResult(
                backend=self.name,
                return_code=1,
                timeout=False,
                stdout_path=stdout_file,
                stderr_path=stderr_file,
                metadata={"model": self.model},
            )

        stdout_file.write_text(stdout_text)
        stderr_file.write_text(stderr_text)
        return AgentRunResult(
            backend=self.name,
            return_code=0,
            timeout=False,
            stdout_path=stdout_file,
            stderr_path=stderr_file,
            metadata={"model": self.model},
        )

    @staticmethod
    def _import_sdk() -> tuple[Any, Any, Any]:
        try:
            from claude_code_sdk import ClaudeCodeOptions, ClaudeSDKClient
            from claude_code_sdk.types import HookMatcher
        except ImportError as exc:
            raise RuntimeError(
                "claude-code-sdk is required for claude_sdk backend. "
                "Install with: pip install claude-code-sdk"
            ) from exc
        return ClaudeCodeOptions, ClaudeSDKClient, HookMatcher

    def _create_client(self, project_dir: Path):
        ClaudeCodeOptions, ClaudeSDKClient, HookMatcher = self._import_sdk()

        security_settings = {
            "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True},
            "permissions": {
                "defaultMode": "acceptEdits",
                "allow": [
                    "Read(./**)",
                    "Write(./**)",
                    "Edit(./**)",
                    "Glob(./**)",
                    "Grep(./**)",
                    "Bash(*)",
                    *PUPPETEER_TOOLS,
                ],
            },
        }

        settings_file = project_dir / ".claude_settings.json"
        settings_file.write_text(json.dumps(security_settings, indent=2) + "\n")

        return ClaudeSDKClient(
            options=ClaudeCodeOptions(
                model=self.model,
                system_prompt=(
                    "You are an expert full-stack developer building "
                    "a production-quality web application."
                ),
                allowed_tools=[*BUILTIN_TOOLS, *PUPPETEER_TOOLS],
                mcp_servers={
                    "puppeteer": {
                        "command": "npx",
                        "args": ["puppeteer-mcp-server"],
                    }
                },
                hooks={
                    "PreToolUse": [
                        HookMatcher(matcher="Bash", hooks=[bash_security_hook]),
                    ],
                },
                max_turns=1000,
                cwd=str(project_dir.resolve()),
                settings=str(settings_file.resolve()),
            )
        )

    async def _run_once(self, request: AgentRunRequest) -> tuple[str, str]:
        prompt = request.prompt_file.read_text()
        client = self._create_client(project_dir=request.project_dir)
        chunks: list[str] = []
        errors: list[str] = []

        async with client:
            await client.query(prompt)
            async for msg in client.receive_response():
                msg_type = type(msg).__name__
                if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                    for block in msg.content:
                        if type(block).__name__ == "TextBlock" and hasattr(block, "text"):
                            chunks.append(block.text)
                elif msg_type == "UserMessage" and hasattr(msg, "content"):
                    for block in msg.content:
                        if type(block).__name__ == "ToolResultBlock":
                            content = getattr(block, "content", "")
                            if getattr(block, "is_error", False):
                                errors.append(str(content))

        return "".join(chunks), "\n".join(errors)
