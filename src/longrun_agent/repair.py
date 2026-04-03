"""Repair mechanism for automatic recovery from tool execution failures.

When a tool call fails (non-zero exit code, syntax errors, etc.), the repair
mechanism can:
1. Analyze the failure context
2. Build a remediation prompt with failure details
3. Inject the prompt back into the conversation for self-correction
4. Track repair attempts to avoid infinite loops

Supports graduated repair strategies:
- Level 1 (HINT): Inject a hint about the failure into the conversation
- Level 2 (GUIDED): Provide specific remediation steps
- Level 3 (ROLLBACK): Undo the last action and suggest alternative approach
"""

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RepairLevel(Enum):
    """Graduated levels of repair intervention."""

    HINT = auto()  # Just tell the agent what went wrong
    GUIDED = auto()  # Provide specific remediation steps
    ROLLBACK = auto()  # Undo + suggest alternative


@dataclass
class RepairAttempt:
    """Record of a single repair attempt."""

    level: RepairLevel
    trigger: str  # What caused the repair (gate_id, error type, etc.)
    message: str  # The repair message injected
    iteration: int  # Which iteration this happened on
    resolved: bool = False  # Whether subsequent action resolved the issue


@dataclass
class RepairPolicy:
    """Configuration for the repair mechanism.

    Attributes:
        enabled: Whether repair is enabled.
        max_repairs_per_session: Total repair attempts allowed per session.
        max_consecutive_repairs: Max repairs in a row before stopping.
        escalation_threshold: After this many same-type repairs, escalate level.
    """

    enabled: bool = True
    max_repairs_per_session: int = 10
    max_consecutive_repairs: int = 3
    escalation_threshold: int = 2


class RepairEngine:
    """Manages automatic repair attempts during agent execution.

    Usage:
        engine = RepairEngine()
        
        # When a tool fails:
        repair_msg = engine.build_repair_message(
            trigger="syntax_check",
            error_detail="SyntaxError: unexpected indent at line 15",
            context={"file": "main.py", "tool": "str_replace_editor"},
        )
        
        if repair_msg:
            # Inject into conversation as a user message
            conversation.add_user_message(repair_msg)
    """

    def __init__(self, policy: Optional[RepairPolicy] = None):
        """Initialize the repair engine.

        Args:
            policy: Repair policy configuration. Uses defaults if not provided.
        """
        self.policy = policy or RepairPolicy()
        self.attempts: list[RepairAttempt] = []
        self._consecutive_repairs = 0

    @property
    def total_repairs(self) -> int:
        """Total number of repair attempts made."""
        return len(self.attempts)

    @property
    def can_repair(self) -> bool:
        """Whether more repair attempts are allowed."""
        if not self.policy.enabled:
            return False
        if self.total_repairs >= self.policy.max_repairs_per_session:
            logger.warning("Max repairs per session reached")
            return False
        if self._consecutive_repairs >= self.policy.max_consecutive_repairs:
            logger.warning("Max consecutive repairs reached")
            return False
        return True

    def _determine_level(self, trigger: str) -> RepairLevel:
        """Determine repair level based on history.

        Escalates from HINT → GUIDED → ROLLBACK based on how many times
        the same trigger has caused repairs.

        Args:
            trigger: The trigger identifier (e.g., gate_id).

        Returns:
            The appropriate RepairLevel.
        """
        same_trigger_count = sum(
            1 for a in self.attempts if a.trigger == trigger
        )
        if same_trigger_count >= self.policy.escalation_threshold * 2:
            return RepairLevel.ROLLBACK
        elif same_trigger_count >= self.policy.escalation_threshold:
            return RepairLevel.GUIDED
        else:
            return RepairLevel.HINT

    def build_repair_message(
        self,
        trigger: str,
        error_detail: str,
        context: Optional[dict[str, Any]] = None,
        iteration: int = 0,
    ) -> Optional[str]:
        """Build a repair message to inject into the conversation.

        Args:
            trigger: What caused the repair (gate_id, error type, etc.).
            error_detail: Detailed description of the failure.
            context: Additional context (file paths, tool names, etc.).
            iteration: Current iteration number.

        Returns:
            The repair message string, or None if repairs are exhausted.
        """
        if not self.can_repair:
            return None

        level = self._determine_level(trigger)
        context = context or {}

        if level == RepairLevel.HINT:
            message = self._build_hint(trigger, error_detail, context)
        elif level == RepairLevel.GUIDED:
            message = self._build_guided(trigger, error_detail, context)
        else:
            message = self._build_rollback(trigger, error_detail, context)

        attempt = RepairAttempt(
            level=level,
            trigger=trigger,
            message=message,
            iteration=iteration,
        )
        self.attempts.append(attempt)
        self._consecutive_repairs += 1

        logger.info(
            f"Repair attempt #{self.total_repairs} "
            f"(level={level.name}, trigger={trigger})"
        )

        return message

    def mark_resolved(self) -> None:
        """Mark the last repair attempt as resolved. Resets consecutive counter."""
        if self.attempts:
            self.attempts[-1].resolved = True
        self._consecutive_repairs = 0

    def _build_hint(
        self, trigger: str, error_detail: str, context: dict[str, Any]
    ) -> str:
        """Build a Level 1 (HINT) repair message."""
        lines = [
            "⚠️ The previous action encountered an issue:",
            f"",
            f"**Issue**: {error_detail}",
        ]
        if context.get("file"):
            lines.append(f"**File**: {context['file']}")
        lines.append("")
        lines.append("Please review and correct the issue before proceeding.")
        return "\n".join(lines)

    def _build_guided(
        self, trigger: str, error_detail: str, context: dict[str, Any]
    ) -> str:
        """Build a Level 2 (GUIDED) repair message with specific steps."""
        lines = [
            "⚠️ Repeated issue detected — here is guided remediation:",
            f"",
            f"**Issue**: {error_detail}",
            f"**Trigger**: {trigger}",
        ]
        if context.get("file"):
            lines.append(f"**File**: {context['file']}")
        lines.append("")
        lines.append("**Suggested steps:**")
        lines.append(f"1. Review the error output carefully")
        lines.append(f"2. Check the affected file for the specific issue")
        lines.append(f"3. Apply a targeted fix (do not rewrite entire file)")
        lines.append(f"4. Verify the fix by running relevant checks")
        if context.get("remediation"):
            for i, step in enumerate(context["remediation"], start=5):
                lines.append(f"{i}. {step}")
        return "\n".join(lines)

    def _build_rollback(
        self, trigger: str, error_detail: str, context: dict[str, Any]
    ) -> str:
        """Build a Level 3 (ROLLBACK) repair message suggesting alternative approach."""
        lines = [
            "🔴 Multiple repair attempts for the same issue have failed.",
            f"",
            f"**Issue**: {error_detail}",
            f"**Trigger**: {trigger}",
            f"**Previous attempts**: {self.total_repairs}",
        ]
        if context.get("file"):
            lines.append(f"**File**: {context['file']}")
        lines.append("")
        lines.append("**Required action:**")
        lines.append("1. STOP the current approach")
        lines.append("2. If you modified a file, undo the last edit")
        lines.append("3. Think of a completely different approach")
        lines.append("4. Explain your new strategy before implementing it")
        return "\n".join(lines)

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of all repair attempts.

        Returns:
            Dict with repair statistics.
        """
        return {
            "total_repairs": self.total_repairs,
            "consecutive_repairs": self._consecutive_repairs,
            "resolved_count": sum(1 for a in self.attempts if a.resolved),
            "repairs_by_level": {
                level.name: sum(1 for a in self.attempts if a.level == level)
                for level in RepairLevel
            },
            "repairs_by_trigger": self._count_by_trigger(),
        }

    def _count_by_trigger(self) -> dict[str, int]:
        """Count repairs grouped by trigger."""
        counts: dict[str, int] = {}
        for a in self.attempts:
            counts[a.trigger] = counts.get(a.trigger, 0) + 1
        return counts
