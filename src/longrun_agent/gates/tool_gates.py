"""Tool-level quality gate checks for post-execution validation.

This module adds tool-execution-level gates that complement the existing
session-level gates in checks.py and engine.py. These gates run after each
individual tool call to catch issues early.

The existing gates (in checks.py) validate session-level invariants like
feature list mutations and required artifacts. These new gates operate at
the tool call level: syntax checks after file edits, exit code monitoring
for bash commands, etc.
"""

import logging
import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GateSeverity(Enum):
    """How critical a gate failure is."""

    BLOCKING = "blocking"  # Stops the agent loop
    WARNING = "warning"  # Logged but agent continues
    INFO = "info"  # Informational only


@dataclass
class ToolGateResult:
    """Result of a single tool-level gate check.

    Distinct from the session-level GateResult in engine.py.

    Attributes:
        gate_id: Unique identifier for the gate.
        passed: Whether the check passed.
        severity: How critical a failure is.
        message: Human-readable description of the result.
        remediation: Suggested actions to fix the issue.
        evidence: Supporting data (e.g., error output, line numbers).
    """

    gate_id: str
    passed: bool
    severity: GateSeverity = GateSeverity.WARNING
    message: str = ""
    remediation: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking_failure(self) -> bool:
        """Whether this result represents a blocking failure."""
        return not self.passed and self.severity == GateSeverity.BLOCKING


class ToolGateCheck(ABC):
    """Abstract base class for tool-level gate checks.

    Subclass this to implement custom quality gates that run after each tool call.
    """

    @property
    @abstractmethod
    def gate_id(self) -> str:
        """Unique identifier for this gate."""
        ...

    @property
    def severity(self) -> GateSeverity:
        """Default severity for failures from this gate."""
        return GateSeverity.WARNING

    @abstractmethod
    def check(self, context: dict[str, Any]) -> ToolGateResult:
        """Run the gate check.

        Args:
            context: Dict with keys like 'tool_name', 'tool_input', 'tool_result',
                     'cwd', etc.

        Returns:
            ToolGateResult indicating pass or fail.
        """
        ...


class SyntaxCheckGate(ToolGateCheck):
    """Checks Python files for syntax errors after edits."""

    @property
    def gate_id(self) -> str:
        return "syntax_check"

    @property
    def severity(self) -> GateSeverity:
        return GateSeverity.BLOCKING

    def check(self, context: dict[str, Any]) -> ToolGateResult:
        tool_name = context.get("tool_name", "")
        tool_input = context.get("tool_input", {})

        # Only check after file edit operations
        if tool_name != "str_replace_editor":
            return ToolGateResult(gate_id=self.gate_id, passed=True, message="Not applicable")

        command = tool_input.get("command", "")
        if command not in ("create", "str_replace", "insert"):
            return ToolGateResult(gate_id=self.gate_id, passed=True, message="Not a write operation")

        file_path = tool_input.get("path", "")
        if not file_path.endswith(".py"):
            return ToolGateResult(gate_id=self.gate_id, passed=True, message="Not a Python file")

        if not os.path.exists(file_path):
            return ToolGateResult(
                gate_id=self.gate_id,
                passed=False,
                severity=self.severity,
                message=f"File does not exist: {file_path}",
            )

        try:
            result = subprocess.run(
                ["python3", "-c", f"import ast; ast.parse(open('{file_path}').read())"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return ToolGateResult(
                    gate_id=self.gate_id,
                    passed=True,
                    message=f"Syntax OK: {file_path}",
                )
            else:
                return ToolGateResult(
                    gate_id=self.gate_id,
                    passed=False,
                    severity=self.severity,
                    message=f"Syntax error in {file_path}",
                    remediation=[
                        f"Fix the syntax error in {file_path}",
                        "Check for missing colons, brackets, or indentation issues",
                    ],
                    evidence={"stderr": result.stderr[:500]},
                )
        except Exception as e:
            return ToolGateResult(
                gate_id=self.gate_id,
                passed=False,
                severity=GateSeverity.WARNING,
                message=f"Could not check syntax: {e}",
            )


class FileExistsGate(ToolGateCheck):
    """Checks that files referenced in tool operations actually exist."""

    @property
    def gate_id(self) -> str:
        return "file_exists"

    def check(self, context: dict[str, Any]) -> ToolGateResult:
        tool_name = context.get("tool_name", "")
        tool_input = context.get("tool_input", {})

        if tool_name != "str_replace_editor":
            return ToolGateResult(gate_id=self.gate_id, passed=True, message="Not applicable")

        command = tool_input.get("command", "")
        if command not in ("view", "str_replace", "insert"):
            return ToolGateResult(gate_id=self.gate_id, passed=True, message="Not applicable")

        file_path = tool_input.get("path", "")
        tool_result = context.get("tool_result", "")

        if "Error" in str(tool_result) and "not found" in str(tool_result).lower():
            return ToolGateResult(
                gate_id=self.gate_id,
                passed=False,
                severity=GateSeverity.WARNING,
                message=f"File operation failed: {file_path}",
                remediation=[
                    f"Check if {file_path} exists",
                    "Use 'view' command to list directory contents first",
                ],
                evidence={"tool_result": str(tool_result)[:300]},
            )

        return ToolGateResult(gate_id=self.gate_id, passed=True, message="File OK")


class BashExitCodeGate(ToolGateCheck):
    """Checks bash command exit codes for failures."""

    @property
    def gate_id(self) -> str:
        return "bash_exit_code"

    def check(self, context: dict[str, Any]) -> ToolGateResult:
        tool_name = context.get("tool_name", "")
        if tool_name != "bash":
            return ToolGateResult(gate_id=self.gate_id, passed=True, message="Not applicable")

        tool_result = context.get("tool_result", "")
        result_str = str(tool_result)

        # Check for non-zero exit code
        if "[Exit code:" in result_str:
            return ToolGateResult(
                gate_id=self.gate_id,
                passed=False,
                severity=GateSeverity.WARNING,
                message="Bash command exited with non-zero code",
                remediation=["Analyze the error output and try a different approach"],
                evidence={"output_tail": result_str[-300:]},
            )

        if result_str.startswith("Error:"):
            return ToolGateResult(
                gate_id=self.gate_id,
                passed=False,
                severity=GateSeverity.WARNING,
                message=f"Bash command failed: {result_str[:100]}",
                remediation=["Check command syntax and try again"],
                evidence={"error": result_str[:300]},
            )

        return ToolGateResult(gate_id=self.gate_id, passed=True, message="Command succeeded")


def get_default_tool_gates() -> list[ToolGateCheck]:
    """Get the default set of tool-level gate checks.

    Returns:
        List of default ToolGateCheck instances.
    """
    return [
        SyntaxCheckGate(),
        FileExistsGate(),
        BashExitCodeGate(),
    ]


class ToolGateEngine:
    """Orchestrates tool-level gate checks after each tool execution.

    This complements the session-level gate system in engine.py / checks.py.

    Usage:
        engine = ToolGateEngine()
        results = engine.run_gates(context)
        if engine.has_blocking_failures(results):
            # Handle failure — inject remediation into conversation
    """

    def __init__(
        self,
        gates: Optional[list[ToolGateCheck]] = None,
        enabled: bool = True,
    ):
        """Initialize the tool gate engine.

        Args:
            gates: Custom list of gate checks. Uses defaults if not provided.
            enabled: Whether gate checking is enabled.
        """
        self.gates = gates if gates is not None else get_default_tool_gates()
        self.enabled = enabled
        self._history: list[list[ToolGateResult]] = []

    def run_gates(self, context: dict[str, Any]) -> list[ToolGateResult]:
        """Run all registered gates against a tool execution context.

        Args:
            context: Dict with at least 'tool_name', 'tool_input', 'tool_result'.

        Returns:
            List of ToolGateResult objects from all gates.
        """
        if not self.enabled:
            return []

        results = []
        for gate in self.gates:
            try:
                result = gate.check(context)
                results.append(result)
            except Exception as e:
                logger.error(f"Gate {gate.gate_id} raised exception: {e}")
                results.append(
                    ToolGateResult(
                        gate_id=gate.gate_id,
                        passed=False,
                        severity=GateSeverity.WARNING,
                        message=f"Gate check failed with exception: {e}",
                    )
                )

        self._history.append(results)
        return results

    @staticmethod
    def has_blocking_failures(results: list[ToolGateResult]) -> bool:
        """Check if any results represent blocking failures."""
        return any(r.is_blocking_failure for r in results)

    @staticmethod
    def get_failures(results: list[ToolGateResult]) -> list[ToolGateResult]:
        """Get all failed gate results."""
        return [r for r in results if not r.passed]

    @staticmethod
    def format_failures_for_prompt(failures: list[ToolGateResult]) -> str:
        """Format gate failures into text for agent prompt injection.

        Args:
            failures: List of failed ToolGateResult objects.

        Returns:
            Formatted string describing failures and remediation steps.
        """
        if not failures:
            return ""

        lines = ["⚠️ Quality gate failures detected:"]
        for f in failures:
            lines.append(f"")
            lines.append(f"- [{f.gate_id}] {f.message}")
            for r in f.remediation:
                lines.append(f"  → {r}")

        lines.append("")
        lines.append("Please address these issues before proceeding.")
        return "\n".join(lines)

    @property
    def history(self) -> list[list[ToolGateResult]]:
        """Get the full history of gate results."""
        return list(self._history)

    def clear_history(self) -> None:
        """Clear the gate result history."""
        self._history = []
