"""System prompt construction with bearings pre-injection and context awareness.

This module enhances the original simple prompt builder to support:
- Bearings pre-injection: run environment discovery commands and embed their
  output directly into the system prompt, saving agent tokens on repetitive
  environment exploration.
- Context-aware sections: conditionally include task context, tool guidance,
  workspace file tree, and custom instructions.
- Layered prompt composition: separate identity, environment, tools, and task
  sections for clearer structure.
"""

import logging
import os
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default bearings commands to discover the environment
DEFAULT_BEARINGS_COMMANDS: list[dict[str, Any]] = [
    {
        "label": "git_status",
        "command": "git status --short 2>/dev/null || echo '(not a git repo)'",
        "timeout": 10,
    },
    {
        "label": "directory_tree",
        "command": "find . -maxdepth 2 -type f | head -50 | sort",
        "timeout": 10,
    },
    {
        "label": "python_version",
        "command": "python3 --version 2>/dev/null || python --version 2>/dev/null || echo '(python not found)'",
        "timeout": 5,
    },
]


@dataclass
class BearingsResult:
    """Result of a single bearings command execution."""

    label: str
    command: str
    output: str
    success: bool
    duration_ms: float = 0.0


@dataclass
class PromptConfig:
    """Configuration for prompt construction.

    Attributes:
        include_bearings: Whether to run and inject bearings commands.
        bearings_commands: List of command specs to run for bearings.
            Each spec is a dict with 'label', 'command', and optional 'timeout'.
        include_file_tree: Whether to include workspace file tree.
        file_tree_max_depth: Max depth for file tree discovery.
        file_tree_max_files: Max number of files to list.
        custom_instructions: Additional instructions to append to the prompt.
        cwd: Working directory for bearings commands.
        task_context: Optional task description to include.
    """

    include_bearings: bool = True
    bearings_commands: list[dict[str, Any]] = field(
        default_factory=lambda: list(DEFAULT_BEARINGS_COMMANDS)
    )
    include_file_tree: bool = False  # Separate from bearings for fine control
    file_tree_max_depth: int = 2
    file_tree_max_files: int = 50
    custom_instructions: Optional[str] = None
    cwd: Optional[str] = None
    task_context: Optional[str] = None


def run_bearings_command(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 10,
) -> tuple[str, bool]:
    """Run a single bearings command and return its output.

    Args:
        command: Shell command to execute.
        cwd: Working directory for the command.
        timeout: Timeout in seconds.

    Returns:
        Tuple of (output_string, success_bool).
    """
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or os.getcwd(),
        )
        output = result.stdout.strip()
        if result.stderr.strip():
            output += f"\n{result.stderr.strip()}" if output else result.stderr.strip()
        return output or "(no output)", result.returncode == 0
    except subprocess.TimeoutExpired:
        return f"(timed out after {timeout}s)", False
    except Exception as e:
        return f"(error: {e})", False


def collect_bearings(
    config: Optional[PromptConfig] = None,
) -> list[BearingsResult]:
    """Run all bearings commands and collect results.

    Args:
        config: Prompt configuration with bearings commands.

    Returns:
        List of BearingsResult objects.
    """
    config = config or PromptConfig()
    results = []

    for cmd_spec in config.bearings_commands:
        label = cmd_spec.get("label", "unknown")
        command = cmd_spec.get("command", "")
        timeout = cmd_spec.get("timeout", 10)

        if not command:
            continue

        import time

        start = time.monotonic()
        output, success = run_bearings_command(
            command, cwd=config.cwd, timeout=timeout
        )
        duration_ms = (time.monotonic() - start) * 1000

        results.append(
            BearingsResult(
                label=label,
                command=command,
                output=output,
                success=success,
                duration_ms=duration_ms,
            )
        )

    return results


def format_bearings_section(results: list[BearingsResult]) -> str:
    """Format bearings results into a prompt section.

    Args:
        results: List of BearingsResult objects.

    Returns:
        Formatted string for inclusion in the system prompt.
    """
    if not results:
        return ""

    lines = ["## Environment Bearings", ""]
    lines.append(
        "The following environment information was pre-collected to save you time. "
        "Use this context instead of running these commands yourself."
    )
    lines.append("")

    for r in results:
        status = "✓" if r.success else "✗"
        lines.append(f"### {r.label} {status}")
        lines.append(f"```")
        lines.append(r.output)
        lines.append(f"```")
        lines.append("")

    return "\n".join(lines)


def build_system_prompt(
    config: Optional[PromptConfig] = None,
    bearings_results: Optional[list[BearingsResult]] = None,
) -> str:
    """Build the system prompt with optional bearings pre-injection.

    This is the enhanced version of the original build_system_prompt().
    It constructs a layered prompt with:
    1. Identity section
    2. Environment bearings (pre-collected)
    3. Tool usage guidelines
    4. Task context (if provided)
    5. Custom instructions (if provided)

    Args:
        config: Prompt configuration. Uses defaults if not provided.
        bearings_results: Pre-collected bearings results. If None and
            config.include_bearings is True, bearings will be collected.

    Returns:
        The complete system prompt string.
    """
    config = config or PromptConfig()
    cwd = config.cwd or os.getcwd()
    os_info = platform.platform()

    # Section 1: Identity
    sections = [
        "You are an AI assistant with access to tools for executing bash commands, "
        "editing files, and computer interaction.",
        "",
        f"Current working directory: {cwd}",
        f"Operating system: {os_info}",
    ]

    # Section 2: Bearings (pre-injected environment context)
    if config.include_bearings:
        if bearings_results is None:
            bearings_results = collect_bearings(config)
        bearings_section = format_bearings_section(bearings_results)
        if bearings_section:
            sections.append("")
            sections.append(bearings_section)

    # Section 3: Guidelines
    sections.append("")
    sections.append("## Guidelines")
    sections.append("")
    sections.append("- Use bash tool for running shell commands")
    sections.append("- Use str_replace_editor for file operations (view, create, replace)")
    sections.append("- Think step by step before acting")
    sections.append("- Verify your changes after making them")
    sections.append(
        "- If a command fails, analyze the error and try alternative approaches"
    )

    # Section 4: Task context
    if config.task_context:
        sections.append("")
        sections.append("## Task Context")
        sections.append("")
        sections.append(config.task_context)

    # Section 5: Custom instructions
    if config.custom_instructions:
        sections.append("")
        sections.append("## Additional Instructions")
        sections.append("")
        sections.append(config.custom_instructions)

    return "\n".join(sections)
