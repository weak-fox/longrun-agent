"""Backend-specific instruction file and repository context guidance snippets."""

from __future__ import annotations

from textwrap import dedent

_BACKEND_INSTRUCTION_FILE = {
    "codex_cli": "AGENTS.md",
    "claude_sdk": "claude.md",
}


def instruction_file_for_backend(backend_name: str) -> str:
    """Return the instruction file that should be used for the active backend."""
    return _BACKEND_INSTRUCTION_FILE.get(backend_name, "AGENTS.md")


def build_instruction_and_layered_reading_guidance(backend_name: str) -> str:
    """Build a shared guidance block that enforces backend-specific docs and layered reads."""
    instruction_file = instruction_file_for_backend(backend_name)
    alternative_file = "claude.md" if instruction_file == "AGENTS.md" else "AGENTS.md"

    return dedent(
        f"""
        ### Backend-specific instruction file (MANDATORY)
        - Read `{instruction_file}` first if it exists at repository root.
        - In this session, treat `{alternative_file}` as inactive guidance.
        - If `{instruction_file}` is missing, note it in progress output and continue with conservative defaults.

        ### Layered repository reading (MANDATORY)
        - Layer 1 (stack + constraints): inspect manifests and tooling (`pyproject.toml`, `package.json`, `go.mod`, lockfiles, CI configs, lint/test configs).
        - Layer 2 (architecture map): inspect entrypoints, routing/composition files, and module boundaries.
        - Layer 3 (task scope): inspect only modules/tests touched by the current task plus direct dependencies.
        - Escalate to broader reads only when evidence shows cross-module coupling or unclear ownership.
        - Do not read the entire repository by default.
        """
    ).strip()
