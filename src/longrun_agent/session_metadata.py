"""Session metadata tracking for observability and diagnostics.

Tracks rich metadata for each agent run including:
- Duration timing (start, end, elapsed)
- Token usage (input, output, cumulative)
- Tool call inventory (counts by tool, success/failure)
- Iteration count and stop reason
- Gate results summary
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolCallRecord:
    """Record of a single tool call."""

    tool_name: str
    duration_ms: float
    success: bool
    error: Optional[str] = None


@dataclass
class SessionMetadata:
    """Rich metadata collected during an agent session.

    Attributes:
        session_id: Unique identifier for this session.
        start_time: Monotonic start time.
        end_time: Monotonic end time.
        wall_start: Wall clock start (ISO format string).
        wall_end: Wall clock end (ISO format string).
        iterations: Number of loop iterations completed.
        stop_reason: Why the loop ended ('end_turn', 'max_iterations', 'error', 'interrupt').
        tool_calls: List of individual tool call records.
        total_input_tokens: Cumulative input tokens.
        total_output_tokens: Cumulative output tokens.
        api_calls: Number of API calls made.
        gate_failures: Count of gate failures encountered.
        errors: List of error messages encountered.
    """

    session_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    wall_start: str = ""
    wall_end: str = ""
    iterations: int = 0
    stop_reason: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    api_calls: int = 0
    gate_failures: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        """Total session duration in seconds."""
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0.0

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.total_input_tokens + self.total_output_tokens

    @property
    def tool_call_count(self) -> int:
        """Total number of tool calls."""
        return len(self.tool_calls)

    @property
    def tool_success_rate(self) -> float:
        """Fraction of tool calls that succeeded (0.0 to 1.0)."""
        if not self.tool_calls:
            return 1.0
        successes = sum(1 for t in self.tool_calls if t.success)
        return successes / len(self.tool_calls)

    def tool_counts_by_name(self) -> dict[str, int]:
        """Get tool call counts grouped by tool name."""
        counts: dict[str, int] = {}
        for tc in self.tool_calls:
            counts[tc.tool_name] = counts.get(tc.tool_name, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata to a dict for JSON output."""
        return {
            "session_id": self.session_id,
            "duration_seconds": round(self.duration_seconds, 2),
            "wall_start": self.wall_start,
            "wall_end": self.wall_end,
            "iterations": self.iterations,
            "stop_reason": self.stop_reason,
            "api_calls": self.api_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "tool_call_count": self.tool_call_count,
            "tool_success_rate": round(self.tool_success_rate, 3),
            "tool_counts_by_name": self.tool_counts_by_name(),
            "gate_failures": self.gate_failures,
            "errors": self.errors,
        }


class SessionTracker:
    """Tracks metadata during an agent session.

    Usage:
        tracker = SessionTracker(session_id="run-001")
        tracker.start()
        # ... during the run ...
        tracker.record_api_call(usage)
        tracker.record_tool_call("bash", 150.0, True)
        tracker.record_gate_failure()
        # ... when done ...
        tracker.finish(stop_reason="end_turn")
        metadata = tracker.metadata
    """

    def __init__(self, session_id: str = ""):
        self.metadata = SessionMetadata(session_id=session_id)
        self._started = False

    def start(self) -> None:
        """Mark the session as started. Records start times."""
        self.metadata.start_time = time.monotonic()
        self.metadata.wall_start = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._started = True

    def finish(self, stop_reason: str = "") -> None:
        """Mark the session as finished. Records end times and stop reason.

        Args:
            stop_reason: Why the session ended (e.g., 'end_turn', 'max_iterations').
        """
        self.metadata.end_time = time.monotonic()
        self.metadata.wall_end = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.metadata.stop_reason = stop_reason

    def record_iteration(self) -> None:
        """Record one loop iteration."""
        self.metadata.iterations += 1

    def record_api_call(self, usage: Any = None) -> None:
        """Record an API call and its token usage.

        Args:
            usage: The usage object from the API response.
        """
        self.metadata.api_calls += 1
        if usage:
            self.metadata.total_input_tokens += getattr(usage, "input_tokens", 0)
            self.metadata.total_output_tokens += getattr(usage, "output_tokens", 0)

    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Record a tool call execution.

        Args:
            tool_name: Name of the tool called.
            duration_ms: Duration in milliseconds.
            success: Whether the tool call succeeded.
            error: Error message if the call failed.
        """
        self.metadata.tool_calls.append(
            ToolCallRecord(
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=success,
                error=error,
            )
        )

    def record_gate_failure(self) -> None:
        """Record a gate failure."""
        self.metadata.gate_failures += 1

    def record_error(self, error: str) -> None:
        """Record an error message.

        Args:
            error: Description of the error.
        """
        self.metadata.errors.append(error)

    @property
    def is_running(self) -> bool:
        """Whether the session is currently running."""
        return self._started and self.metadata.end_time == 0.0
